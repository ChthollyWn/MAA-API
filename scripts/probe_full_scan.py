#!/usr/bin/env python3
"""M39 W1 探针：全量扫描「详情页左滑遍历」的可行性取证。

要证四件事：

1. 详情页左滑能不能换到下一个干员——哪种几何 / 时长划得动（``adb shell input swipe``
   在这台设备上一格都不动，见下）；
2. 滑动之后画面多久稳定（决定读数时机与 ``postDelay``）；
3. 「划不动」在像素上是什么样——末位判定的信号；
4. 不回列表连续左滑，顺序是不是沿列表顺序走下去（身份对齐的前提）。

跑法（先确认模拟器已连上 ``adb``，游戏停在干员详情页；``--step detail`` 会自己从列表点开
第一格）::

    .venv/bin/python scripts/probe_full_scan.py --step variants
    .venv/bin/python scripts/probe_full_scan.py --step walk --count 4

产物：``/tmp/m39_probe_*.png`` 与 stdout 的人读结论。
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops

REPO_ROOT = Path(__file__).resolve().parents[1]
for extra in (REPO_ROOT, REPO_ROOT / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import probe_training_scan as p36  # noqa: E402
from maa_api.services.account import scan_tasks as st  # noqa: E402
from maa_api.services.account.scan import (  # noqa: E402
    CARDS_PER_SCREEN,
    _name_en_hit,
    cell_of,
)

#: 一次滑动之后等多久再抓图（判断「多久稳定」用，抓完这一帧还会再抓一帧）。
STABLE_WAIT = 0.9
#: 滑动前先抓一帧的等待（避免抓到上一动作的动画残留）。
BEFORE_WAIT = 0.6

#: 「画面没动」判据：实测划不动 mean=0.02 / frac>25=0.000，划得动 mean=48.57 / frac>25=0.548，
#: 取 0.05 做门槛有两个数量级的余量。
NO_CHANGE_MOVED = 0.05

#: 标定用的滑动变体：短名 → (起点矩形, 终点矩形, specialParams, postDelay)。
#: 坐标全是内核 1280x720 基准（``asst.get_image()`` 拿到的是 2560x1440，内核自己换算）。
SWIPE_VARIANTS: dict[str, tuple[tuple[int, int, int, int], tuple[int, int, int, int], list[int], int]] = {
    # 生产翻屏节点同形状（列表页已验证可用），搬到详情页试。
    "mid": ((1080, 350, 100, 100), (100, 350, 100, 100), [400, 1, 37, 1], 800),
    # 起点更靠右／更长距离。
    "edge": ((1220, 360, 100, 100), (60, 360, 100, 100), [400, 1, 37, 1], 800),
    # 慢一点，给引擎更多中间 MOVE 事件。
    "slow": ((1080, 350, 100, 100), (100, 350, 100, 100), [1200, 1, 37, 1], 800),
    # 纵向位置更靠下（避开立绘，落在属性面板那一条）。
    "low": ((1080, 620, 100, 100), (120, 620, 100, 100), [400, 1, 37, 1], 800),
    # 反方向：往右划（用来在第一个干员上取「划不动」的像素信号）。
    "prev": ((100, 350, 100, 100), (1080, 350, 100, 100), [400, 1, 37, 1], 800),
    # 上面那些的起点都落在右侧面板（技能/模组区）上；这三条从**立绘中间**起手。
    "center": ((650, 400, 100, 100), (250, 400, 100, 100), [400, 1, 37, 1], 800),
    "centerhigh": ((700, 250, 100, 100), (200, 250, 100, 100), [400, 1, 37, 1], 800),
    "centerlong": ((760, 430, 100, 100), (120, 430, 100, 100), [400, 1, 37, 1], 800),
    # 立绘区间里的反向（第一个干员上试）。
    "centerprev": ((250, 400, 100, 100), (650, 400, 100, 100), [400, 1, 37, 1], 800),
    # 反向划的正确起手（起点在立绘里，不在左侧属性面板上）。
    "centerback": ((400, 400, 100, 100), (800, 400, 100, 100), [400, 1, 37, 1], 800),
}


def swipe_variant_nodes() -> dict[str, dict[str, Any]]:
    """一次性标定节点：每个变体一个 ``JustReturn`` + ``Swipe`` 节点。"""
    nodes: dict[str, dict[str, Any]] = {}
    for key, (frm, to, params, post) in SWIPE_VARIANTS.items():
        name = f"{st.SCAN_TASK_PREFIX}@ProbeSwipe{key.title()}"
        nodes[name] = {
            "algorithm": "JustReturn",
            "action": "Swipe",
            "specificRect": list(frm),
            "rectMove": list(to),
            "specialParams": list(params),
            "postDelay": post,
            "maxTimes": 1,
            "next": [st.DONE],
            "exceededNext": [st.DONE],
            "doc": f"M39 标定：详情页左滑变体 {key}",
        }
    return nodes


def variant_node(key: str) -> str:
    return f"{st.SCAN_TASK_PREFIX}@ProbeSwipe{key.title()}"


# ---------------------------------------------------------------------------
# 像素比对
# ---------------------------------------------------------------------------


def frame_diff(before: Image.Image, after: Image.Image) -> tuple[float, float]:
    """两张整屏的差异：``(平均灰度差, 明显变化的像素占比)``。"""
    a = before.convert("L").resize((320, 180))
    b = after.convert("L").resize((320, 180))
    hist = ImageChops.difference(a, b).histogram()
    total = 320 * 180
    mean = sum(index * count for index, count in enumerate(hist)) / total
    moved = sum(count for index, count in enumerate(hist) if index > 25) / total
    return mean, moved


def grab(probe: p36.Probe, name: str) -> Image.Image | None:
    """抓一帧**实时**整屏。

    实测：``asst.get_image()`` 在这轮里返回的是缓存帧（一整个 variants 轮次里 5 张图
    md5 完全相同，且与当场的 ``adb screencap`` 不同）——只有跑过识别节点的轮次里它才刷新。
    标定像素差必须用实时帧，所以这里直接走 ``adb exec-out screencap``。
    """
    assert probe.asst is not None
    raw = _screencap(probe.adb, probe.address)
    if not raw:
        print("[probe] 取图失败")
        return None
    path = Path(f"/tmp/m39_probe_{name}.png")
    path.write_bytes(raw)
    image = Image.open(io.BytesIO(raw))
    image.load()
    return image


def _screencap(adb: str, address: str) -> bytes:
    import subprocess

    result = subprocess.run(
        [adb, "-s", address, "exec-out", "screencap", "-p"],
        capture_output=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else b""


def click_cell(probe: p36.Probe, row: str, column: int) -> None:
    x, y = st.card_center(row, column)
    print(f"[probe] 点开 {row} 第 {column + 1} 列（内核坐标 {x},{y}）")
    probe.click(x, y)


# ---------------------------------------------------------------------------
# 身份核对：详情页左下角的拉丁名
# ---------------------------------------------------------------------------

#: 只读 ROI：详情页左下角的**拉丁名**（内核 1280x720 基准）。设备（2560x1440）上文字带实测在
#: x 20–1036、y 912–968：白字带描边，横在中文美术字上方，与上面那行 ★ 不重叠。
#: 2026-10-11 第二轮：左边从 8 放到 **0**（原来只留 2 个内核像素的余量，`Ines`→`nes`、
#: `SilverAsh`→`ilverAsh` 这种掉首字母多半就是左边缘切掉的），上下各加 4 像素余量。
OPER_NAME_EN_ROI = (0, 452, 524, 32)
#: 探针自己声明的节点名（生产侧 2026-10-11 还没有这个节点，先在这里试）。
OPER_NAME_EN_NODE = "MaaApi@OperatorScan@ProbeOperNameEn"
#: name 步的抽样格子：两排各挑几个，覆盖长短名字与不同立绘底色。
NAME_SAMPLE_CELLS: tuple[tuple[str, int], ...] = (
    ("top", 0),
    ("top", 2),
    ("top", 4),
    ("top", 7),
    ("bottom", 0),
    ("bottom", 3),
    ("bottom", 6),
    ("bottom", 7),
)


def name_en_nodes() -> dict[str, dict[str, Any]]:
    """「读详情页拉丁名」的只读节点（只进探针层，不改生产定义）。"""
    return {
        OPER_NAME_EN_NODE: {
            "algorithm": "OcrDetect",
            "isAscii": True,
            "text": [],
            "roi": list(OPER_NAME_EN_ROI),
            "action": "DoNothing",
            "doc": "只读：详情页左下角拉丁名（M39 探针，用来核对身份）",
        }
    }


def _norm_name(text: str | None) -> str:
    """比对用归一化：去变音符、去一切非字母数字（``Wiš'adel`` → ``wisadel``）。"""
    lowered = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in lowered.lower() if ch.isalnum())


def expected_names_en() -> dict[str, str]:
    """本机 ``battle_data.json`` 的「中文名 → 拉丁名」（与生产干员目录同源）。"""
    for path in (
        REPO_ROOT / "resource" / "maa-layers" / "repo" / "resource" / "battle_data.json",
        REPO_ROOT / "resource" / "maa-layers" / "custom" / "resource" / "battle_data.json",
    ):
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            return {
                str(entry["name"]): str(entry.get("name_en") or "")
                for entry in payload.get("chars", {}).values()
                if isinstance(entry, dict) and entry.get("name")
            }
    raise SystemExit("找不到 battle_data.json（先确认内核资源在）")


# ---------------------------------------------------------------------------
# 步骤
# ---------------------------------------------------------------------------


def parse_cells(spec: str) -> tuple[tuple[str, int], ...]:
    """``"top0,bottom3"``（也接受 ``top:0``）→ ``(("top", 0), ("bottom", 3))``。"""
    cells: list[tuple[str, int]] = []
    for chunk in (part.strip().replace(":", "") for part in (spec or "").split(",")):
        if not chunk:
            continue
        row = next((key for key in st.ROW_KEYS if chunk.startswith(key)), "")
        column = chunk[len(row):]
        if row and column.isdigit():
            cells.append((row, int(column)))
    return tuple(cells)


def step_name(probe: p36.Probe, *, cells: int, only: tuple[tuple[str, int], ...] = ()) -> dict[str, Any]:
    """读一屏卡名 → 逐格点开详情 → 只读拉丁名 → 与 ``battle_data`` 的 ``name_en`` 对账。

    对账走**生产口径**的 :func:`maa_api.services.account.scan._name_en_hit`：候选集是这
    一屏 16 格按扫描顺序（列优先）排出来的拉丁名，跟全量扫描逐张核对时一模一样。

    要证的是：详情页左下角那行拉丁名能不能用内核 ASCII OCR 稳定读出来。读得出来，
    全量扫描就能在**不回列表**的前提下逐张核对身份——今晚那处甩动漂移正是「滑过去之后
    没人知道画面里是谁」造成的。
    """
    expected = expected_names_en()
    _, names = p36.read_box(probe, label="name-box")
    print(f"[probe] 本屏卡名读到 {len(names)}/16 格")
    # 生产口径的期望表：扫描顺序第 i 张（i 从 0 数）的拉丁名 = 那一格卡名的 name_en。
    order = {cell_of(index): index for index in range(CARDS_PER_SCREEN)}
    expected_en = {
        order[cell]: expected[zh]
        for cell, zh in names.items()
        if cell in order and expected.get(zh)
    }
    report: dict[str, Any] = {
        "box": {f"{row}{column}": name for (row, column), name in names.items()},
        "expected_en": {str(index): name for index, name in expected_en.items()},
        "cells": [],
    }
    sample = only or NAME_SAMPLE_CELLS[: max(1, cells)]
    hits = 0
    checked = 0
    for index, (row, column) in enumerate(sample):
        zh = names.get((row, column))
        want = expected.get(zh or "", "")
        click_cell(probe, row, column)
        time.sleep(1.4)
        if index == 0:
            grab(probe, "name-first")  # 留一帧当证据
        messages = probe.run_nodes((OPER_NAME_EN_NODE,), label=f"name-{row}{column}")
        readings = p36._readings(messages)
        item = readings.get(st.node_key(OPER_NAME_EN_NODE)) or {}
        text = item.get("text")
        if isinstance(text, list):
            text = " ".join(str(part) for part in text)
        got, exp = _norm_name(str(text or "")), _norm_name(want)
        # 名字被截断也算对得上（长名字可能超出 ROI）：前缀关系且够长即可。
        ok = bool(got) and bool(exp) and (got == exp or exp.startswith(got) or got.startswith(exp))
        # 生产口径：这一格在期望表里时，模糊比对认出来的必须是它自己。
        cell_index = order.get((row, column))
        hit = _name_en_hit(str(text or ""), expected_en) if expected_en else None
        if cell_index in expected_en:
            checked += 1
            verdict = "对得上" if hit == cell_index else f"对不上（认成了第 {hit + 1} 张）" if hit is not None else "认不出"
        else:
            verdict = "这一格没有期望值（卡名没读出/目录里没拉丁名），生产里不核对"
        # 对不上时反查「读到的拉丁名是谁」——判断到底是漏读还是真错位。
        by_en = {_norm_name(en): zh for zh, en in expected.items() if en}
        seen_as = "" if ok else (by_en.get(got) or next(
            (zh for key, zh in by_en.items() if key and (key.startswith(got) or got.startswith(key))), ""
        ))
        hits += int(ok)
        print(
            f"[probe] {row}{column}：卡名 {zh or '—（没读出来）'} → 拉丁名 "
            f"«{text or ''}»（score={item.get('score')}）期望 «{want}» → "
            f"{'对得上' if ok else f'对不上（读到的像是 {seen_as or '？'}）'}；{verdict}"
        )
        report["cells"].append(
            {
                "cell": f"{row}{column}",
                "zh": zh,
                "want": want,
                "got": text,
                "score": item.get("score"),
                "ok": ok,
                "looks_like": seen_as,
                "scan_index": cell_index,
                "prod_hit": hit,
                "prod_ok": hit == cell_index if cell_index in expected_en else None,
            }
        )
        probe.tap_back()
        time.sleep(1.2)
    total = len(report["cells"])
    prod_hits = sum(1 for item in report["cells"] if item["prod_ok"])
    print(
        f"\n[probe] 拉丁名对账：前缀比对 {hits}/{total}；生产口径（模糊 + 唯一性）"
        f"{prod_hits}/{checked} 对得上"
    )
    report["hits"] = hits
    report["prod_hits"] = prod_hits
    report["checked"] = checked
    return report


def step_variants(probe: p36.Probe) -> dict[str, Any]:
    """在详情页上逐个试滑动变体，报每个变体前后两帧的像素差。"""
    report: dict[str, Any] = {}
    for key in SWIPE_VARIANTS:
        time.sleep(BEFORE_WAIT)
        before = grab(probe, f"{key}_before")
        if before is None:
            break
        probe.run_nodes((variant_node(key),), label=f"swipe-{key}")
        time.sleep(STABLE_WAIT)
        after = grab(probe, f"{key}_after")
        time.sleep(0.8)
        later = grab(probe, f"{key}_later")
        if after is None or later is None:
            break
        mean, moved = frame_diff(before, after)
        mean2, moved2 = frame_diff(after, later)
        print(
            f"[probe] 变体 {key:5s}：划后立即 mean={mean:6.2f} frac>25={moved:.3f}；"
            f"再等 0.8 秒 mean={mean2:5.2f} frac>25={moved2:.3f}"
        )
        report[key] = {
            "immediate": [round(mean, 3), round(moved, 4)],
            "settled": [round(mean2, 3), round(moved2, 4)],
        }
    return report


def step_walk(probe: p36.Probe, *, count: int, variant: str) -> dict[str, Any]:
    """在详情页上连续左滑 ``count`` 次，每次抓图 + 跑生产详情链读数。"""
    report: dict[str, Any] = {"variant": variant, "rounds": []}
    node = variant_node(variant)
    for index in range(count + 1):
        image = grab(probe, f"walk{index:02d}")
        messages, gaps = probe.run_chain(st.detail_screen_nodes(), label=f"walk{index:02d}")
        readings = p36._readings(messages)
        print(f"\n=== 第 {index} 张详情页（{len(messages)} 条回调，缺口 {gaps}）===")
        p36._print_readings("详情页读数", readings)
        if image is not None:
            p36.print_detail_summary(readings, image)
        report["rounds"].append(
            {
                "index": index,
                "gaps": gaps,
                "readings": {
                    key: {
                        "text": value.get("text"),
                        "template": value.get("template"),
                        "score": value.get("score"),
                    }
                    for key, value in readings.items()
                },
            }
        )
        if index == count:
            break
        probe.run_nodes((node,), label=f"walk{index:02d}-swipe")
        time.sleep(0.8)
    return report


def step_list_swipe(probe: p36.Probe) -> dict[str, Any]:
    """回到列表页，用生产翻屏节点划一次——验证内核注入本身动不动得动屏幕。"""
    probe.run_nodes((st.BACK,), label="back")
    time.sleep(1.2)
    before = grab(probe, "list_before")
    probe.run_nodes((st.SWIPE_NEXT_PAGE,), label="list-swipe")
    time.sleep(1.2)
    after = grab(probe, "list_after")
    if before is None or after is None:
        return {}
    mean, moved = frame_diff(before, after)
    print(f"[probe] 列表翻屏：mean={mean:6.2f} frac>25={moved:.3f}")
    return {"list_swipe": [round(mean, 3), round(moved, 4)]}


def step_enter(probe: p36.Probe) -> dict[str, Any]:
    """回到干净基线：跑内核 ``EnterBox``（顺带按等级排序），再读一屏卡名。"""
    probe.run_nodes((st.ENTER_BOX,), label="enter-box")
    time.sleep(1.5)
    grab(probe, "enter_box")
    _, names = p36.read_box(probe, label="box-enter")
    p36.print_box(names)
    return {f"{row}{column}": name for (row, column), name in sorted(names.items())}


def step_backsync(probe: p36.Probe, *, swipes: int, variant: str, assume: str) -> dict[str, Any]:
    """「读一屏卡名 → 点第一格 → 滑 k 次 → 返回」之后，列表还在不在原来那一屏。

    身份对齐就靠这条性质：每屏先用列表卡名认人，再在详情页上滑完这一屏。
    """
    if assume == "detail":
        probe.run_nodes((st.BACK,), label="back-to-list")
        time.sleep(1.2)
    time.sleep(BEFORE_WAIT)
    _, before = p36.read_box(probe, label="box-before")
    p36.print_box(before)
    click_cell(probe, "top", 0)
    time.sleep(1.5)
    for index in range(swipes):
        grab(probe, f"backsync{index}")
        probe.run_nodes((variant_node(variant),), label=f"backsync-swipe{index}")
        time.sleep(1.0)
    grab(probe, f"backsync{swipes}")
    probe.run_nodes((st.BACK,), label="back")
    time.sleep(1.2)
    _, after = p36.read_box(probe, label="box-after")
    p36.print_box(after)
    same = before == after
    print(
        f"[probe] 滑 {swipes} 次再返回：列表卡名{'与滑动前一致（位置保持）' if same else '变了！'}"
    )
    return {
        "swipes": swipes,
        "same": same,
        "before": {f"{row}{column}": name for (row, column), name in sorted(before.items())},
        "after": {f"{row}{column}": name for (row, column), name in sorted(after.items())},
    }


def step_one(probe: p36.Probe, *, variant: str, assume: str) -> dict[str, Any]:
    """只试一个变体：抓 before → 划 → 抓 after，报像素差（末位判定的信号强度）。"""
    if assume == "list":
        click_cell(probe, "top", 0)
        time.sleep(1.5)
    time.sleep(BEFORE_WAIT)
    before = grab(probe, f"one_{variant}_before")
    probe.run_nodes((variant_node(variant),), label=f"one-{variant}")
    time.sleep(STABLE_WAIT)
    after = grab(probe, f"one_{variant}_after")
    if before is None or after is None:
        return {}
    mean, moved = frame_diff(before, after)
    print(f"[probe] 单试 {variant}：mean={mean:6.2f} frac>25={moved:.3f}")
    return {"variant": variant, "mean": round(mean, 3), "moved": round(moved, 4)}


def step_listend(probe: p36.Probe, *, max_turns: int = 30) -> dict[str, Any]:
    """把列表一屏屏翻到底：验证「翻不动」的像素信号，并数出总屏数与末屏人数。

    全量扫描的收尾判据有三条，这是其中一条（翻页无变化 ⇒ 已到最后一屏）。
    """
    turns = 0
    diffs: list[tuple[int, float, float]] = []
    _, guard = p36.read_box(probe, label="box-guard")
    if len([name for name in guard.values() if name]) < 4:
        raise RuntimeError("当前画面不是干员列表（卡名读到不足 4 格），先回列表再跑这个步骤")
    while turns < max_turns:
        before = grab(probe, f"listend{turns:02d}_before")
        probe.run_nodes((st.SWIPE_NEXT_PAGE,), label=f"turn-{turns}")
        time.sleep(STABLE_WAIT)
        after = grab(probe, f"listend{turns:02d}_after")
        if before is None or after is None:
            break
        mean, moved = frame_diff(before, after)
        diffs.append((turns, round(mean, 2), round(moved, 4)))
        print(f"[probe] 第 {turns + 1} 次翻页：mean={mean:6.2f} frac>25={moved:.3f}")
        if moved < NO_CHANGE_MOVED:
            print(f"[probe] 翻页无变化 ⇒ 已在最后一屏（共翻 {turns} 次）")
            break
        turns += 1
    _, names = p36.read_box(probe, label="box-last")
    p36.print_box(names)
    tail = sum(1 for name in names.values() if name)
    print(f"[probe] 末屏读到 {tail}/16 格非空")
    probe.run_nodes((st.BACK,), label="leave-box")
    time.sleep(1.2)
    probe.run_nodes((st.ENTER_BOX,), label="re-enter-box")
    time.sleep(1.5)
    return {"turns": turns, "last_page_names": tail, "diffs": diffs}


def step_sweep(probe: p36.Probe, *, max_turns: int = 30, settle: float = 2.0) -> dict[str, Any]:
    """按生产机制一屏屏翻到底：每翻一次**读一遍卡名**，数出屏数 / 每屏可读格数 / 末屏信号。

    与 ``step_listend`` 的差别：那个只看像素，这个把「翻页之后卡名读得干不干净」也量出来——
    全量扫描的收尾判据 2（新屏卡名与上屏有交集 ⇒ 翻不动）必须在真实读数上验证，顺带量出
    固定 ROI 与滚动位置的长期对齐情况。
    """
    _, guard = p36.read_box(probe, label="sweep-guard")
    guard_names = [name for name in guard.values() if name]
    if len(guard_names) < 4:
        raise RuntimeError("当前画面不是干员列表（卡名读到不足 4 格），先回列表再跑这个步骤")
    pages: list[dict[str, Any]] = []
    turns: list[dict[str, Any]] = []
    prev_frame = grab(probe, "sweep00")
    prev_names = guard
    print(f"[probe] 第 1 屏读到 {len(guard_names)}/16 格")
    for turn in range(1, max_turns + 1):
        probe.run_nodes((st.SWIPE_NEXT_PAGE,), label=f"sweep-turn-{turn}")
        time.sleep(settle)
        frame = grab(probe, f"sweep{turn:02d}")
        mean, moved = frame_diff(prev_frame, frame) if prev_frame is not None and frame is not None else (0.0, 0.0)
        _, names = p36.read_box(probe, label=f"sweep-read-{turn}")
        readable = [name for name in names.values() if name]
        shared = sorted({name for name in readable} & {name for name in prev_names.values() if name})
        print(
            f"[probe] 第 {turn + 1} 屏：mean={mean:6.2f} frac>25={moved:.3f} "
            f"可读 {len(readable)}/16 与上屏重名 {len(shared)} 个 {shared[:4]}"
        )
        pages.append({f"{row}:{column}": names.get((row, column)) for row in st.ROW_KEYS for column in range(st.GRID_COLUMNS)})
        turns.append({"turn": turn, "mean": round(mean, 2), "moved": round(moved, 4), "readable": len(readable), "shared": shared})
        if mean < 2.0:
            print(f"[probe] 画面没动（mean={mean:.2f}）⇒ 到底了，共 {turn} 次翻页")
            break
        if shared and len(shared) >= len(readable):
            # 相邻两屏重名是正常的（一次翻页只前进 5 列）；**全都重名**才说明列表没再往前。
            print(f"[probe] 新屏的人上屏都见过（{len(shared)}/{len(readable)}）⇒ 翻不动，共 {turn} 次翻页")
            break
        prev_frame, prev_names = frame, names
        if not readable:
            print("[probe] 整屏读不出卡名 ⇒ 收尾")
            break
    p36.print_box(prev_names)
    probe.run_nodes((st.BACK,), label="sweep-leave-box")
    time.sleep(1.2)
    probe.run_nodes((st.ENTER_BOX,), label="sweep-re-enter")
    time.sleep(1.5)
    return {"turns": turns, "pages": pages}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default=p36.DEFAULT_ADB)
    parser.add_argument("--address", default=p36.DEFAULT_ADDRESS)
    parser.add_argument("--kernel", default=str(p36.DEFAULT_KERNEL))
    parser.add_argument(
        "--step",
        choices=("variants", "walk", "detail", "list-swipe", "backsync", "enter", "one", "listend", "sweep", "name"),
        default="variants",
    )
    parser.add_argument("--assume", choices=("list", "detail"), default="list")
    parser.add_argument("--row", choices=st.ROW_KEYS, default="top")
    parser.add_argument("--column", type=int, default=0)
    parser.add_argument("--count", type=int, default=3, help="walk 步：滑动次数；sweep 步：最多翻几屏")
    parser.add_argument("--cells", default="", help="name 步：指定格子，如 top0,bottom3（空则用默认抽样）")
    parser.add_argument("--variant", default="mid", choices=sorted(SWIPE_VARIANTS))
    parser.add_argument("--report", default="/tmp/m39_probe_report.json")
    args = parser.parse_args()

    probe = p36.Probe(adb=args.adb, address=args.address, kernel=Path(args.kernel))
    probe.extra_tasks = {**swipe_variant_nodes(), **name_en_nodes()}
    report: dict[str, Any] = {}
    try:
        probe.start()
        if args.step == "detail":
            click_cell(probe, args.row, args.column)
            time.sleep(1.5)
            image = grab(probe, "detail")
            if image is not None:
                print("[probe] 已抓详情页整屏")
        elif args.step == "variants":
            report["variants"] = step_variants(probe)
        elif args.step == "list-swipe":
            report["list_swipe"] = step_list_swipe(probe)
        elif args.step == "backsync":
            report["backsync"] = step_backsync(
                probe, swipes=args.count, variant=args.variant, assume=args.assume
            )
        elif args.step == "enter":
            report["enter"] = step_enter(probe)
        elif args.step == "listend":
            report["listend"] = step_listend(probe)
        elif args.step == "sweep":
            report["sweep"] = step_sweep(probe, max_turns=args.count)
        elif args.step == "one":
            report["one"] = step_one(probe, variant=args.variant, assume=args.assume)
        elif args.step == "name":
            report["name"] = step_name(probe, cells=args.count, only=parse_cells(args.cells))
        else:
            report["walk"] = step_walk(probe, count=args.count, variant=args.variant)
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n[probe] 报告写入 {args.report}")
    finally:
        probe.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
