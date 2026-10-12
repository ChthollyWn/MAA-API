#!/usr/bin/env python3
"""M42 W0 探针：森空岛扫码登录与账号练度取数的形状取证。

要证十件事（规格 §2.5），全部落在 ``/tmp/m42_skland/report.json`` 里：

1. ``potentialRank`` 与 OperBox 的 ``potential`` 是否差 1（本脚本只取接口原值，对照另跑）；
2. ``data.charInfoMap`` 的字段集合；
3. ``skills[].id`` 与本机 ``character_table`` 是否同一命名空间；
4. ``equip[].id`` 的形状（模组类型映射的输入）；
5. ``bindingList[]`` 的真实字段；
6. ``scan_status`` 确认后的 ``status`` 与 ``data.scanCode`` 形状；
7. ``cred`` 与签名密钥的长度、``auth/refresh`` 的返回形状；
8. ``favorPercent`` 的量级与 ``chars`` 是否含未拥有干员；
9. ``chars`` 的数量级（决定 ``SKLAND_MIN_CHARS`` 阈值）；
10. 请求头 ``platform`` / ``vName`` 用社区值能否通过（签名是否被上游接受）。

跑法（两步，中间用户拿手机扫码）::

    .venv/bin/python scripts/probe_skland_login.py new
    # 打开 /tmp/m42_skland/qr.png，用森空岛 App 扫它，然后在 App 里确认
    .venv/bin/python scripts/probe_skland_login.py poll <scan_id>

产物：``/tmp/m42_skland/{qr.svg,qr.png,report.json,probe.log}``。

**脱敏**：``cred`` / 签名密钥 / 鹰角 token 只落长度不落值；昵称只落长度；二维码文件在
``poll`` 成功后不会被删除（用户可能还要重扫），但 ``report.json`` 里不含任何可直接登录的
凭证。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from maa_api.domain.errors import AppError  # noqa: E402
from maa_api.services.skland.client import SklandClient, SklandCredential  # noqa: E402
from maa_api.util.qr import qr_matrix, qr_svg  # noqa: E402

#: 产物目录。探针不往仓库里写东西。
OUT_DIR = Path(os.environ.get("M42_PROBE_DIR", "/tmp/m42_skland"))

#: 轮询节奏与上限（上游 ``scan_status`` 的 100 = 未扫码、102 = 已失效）。
POLL_INTERVAL_SECONDS = 2.0
POLL_TIMEOUT_SECONDS = 300.0

#: 请求头里这两项只有社区实现参考，W0 就是来确认它们的（规格 §2.5-10）。
PLATFORM_HEADER = os.environ.get("M42_PLATFORM", "1")
VNAME_HEADER = os.environ.get("M42_VNAME", "1.0.0")

logger = logging.getLogger("probe_skland")


def _setup_logging() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(OUT_DIR / "probe.log", encoding="utf8"),
        ],
    )


def _write_qr(scan_url: str) -> None:
    """把登录串渲染成 SVG 与 PNG（PNG 走 Pillow 画矩阵，纯粹为了好扫）。"""
    (OUT_DIR / "qr.svg").write_text(qr_svg(scan_url, scale=8), encoding="utf8")
    matrix = qr_matrix(scan_url)
    size = len(matrix)
    border, scale = 4, 8
    from PIL import Image

    image = Image.new("RGB", ((size + 2 * border) * scale, (size + 2 * border) * scale), "white")
    pixels = image.load()
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if not dark:
                continue
            for dy in range(scale):
                for dx in range(scale):
                    pixels[(x + border) * scale + dx, (y + border) * scale + dy] = (0, 0, 0)
    image.save(OUT_DIR / "qr.png")
    logger.info("二维码已写：%s（%d×%d 模块，PNG %dpx）", OUT_DIR / "qr.png", size, size, image.width)


async def _cmd_new(args: argparse.Namespace) -> int:
    client = SklandClient(extra_headers={"platform": PLATFORM_HEADER, "vName": VNAME_HEADER})
    try:
        login = await client.generate_scan()
    finally:
        await client.aclose()
    _write_qr(login.scan_url)
    state = {"scan_id": login.scan_id, "scan_url": login.scan_url, "created_at": time.time()}
    (OUT_DIR / "session.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf8")
    print(f"scan_id   = {login.scan_id}")
    print(f"scan_url  = {login.scan_url}")
    print(f"二维码    = {OUT_DIR / 'qr.png'}（用森空岛 App 扫它并在 App 里确认）")
    print(f"下一步    = .venv/bin/python scripts/probe_skland_login.py poll {login.scan_id}")
    return 0


async def _poll_scan_code(
    client: SklandClient, scan_id: str, timeout: float = POLL_TIMEOUT_SECONDS
) -> tuple[int, str | None, str, list[dict[str, Any]]]:
    """轮询到确认或失效；返回``(最终 status, scan_code, msg, 轨迹)``。"""
    trace: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout
    last: tuple[int, str, str] | None = None
    while time.monotonic() < deadline:
        status, scan_code, msg = await client.scan_status(scan_id)
        if last != (status, msg, str(scan_code is not None)):
            trace.append(
                {
                    "t": round(time.time(), 3),
                    "status": status,
                    "msg": msg,
                    "has_scan_code": scan_code is not None,
                }
            )
            logger.info("scan_status：status=%s msg=%r scanCode=%s", status, msg, "有" if scan_code else "无")
            last = (status, msg, str(scan_code is not None))
        if status == 0 and scan_code:
            return status, scan_code, msg, trace
        # 101 ＝ 已扫码待确认（真机实测 2026-10-12）：继续等同一张码，**不能**当成
        # 失效去换新码，否则用户刚扫的那张会被自己的探针作废。
        if status not in (0, 100, 101):
            return status, scan_code, msg, trace
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"轮询 {timeout:.0f} 秒仍未确认扫码")


def _shape(value: Any, *, depth: int = 0) -> dict[str, Any]:
    """给一个 JSON 值做「形状画像」：类型、长度、键集合；不落值。"""
    if isinstance(value, dict):
        return {
            "type": "dict",
            "count": len(value),
            "keys": sorted(str(k) for k in value),
            **(
                {"sample": _shape(next(iter(value.values())), depth=depth + 1)}
                if value and depth < 2
                else {}
            ),
        }
    if isinstance(value, list):
        return {
            "type": "list",
            "count": len(value),
            **({"sample": _shape(value[0], depth=depth + 1)} if value and depth < 2 else {}),
        }
    if isinstance(value, bool):
        return {"type": "bool"}
    if isinstance(value, int | float):
        return {"type": "number"}
    if value is None:
        return {"type": "null"}
    return {"type": "str", "length": len(str(value))}


def _char_sample(char: dict[str, Any]) -> dict[str, Any]:
    """一名干员的读数原样（不含隐私）。"""
    return {
        "keys": sorted(char),
        "charId": char.get("charId"),
        "level": char.get("level"),
        "evolvePhase": char.get("evolvePhase"),
        "potentialRank": char.get("potentialRank"),
        "mainSkillLvl": char.get("mainSkillLvl"),
        "favorPercent": char.get("favorPercent"),
        "gainTime": char.get("gainTime"),
        "skinId": char.get("skinId"),
        "defaultSkillId": char.get("defaultSkillId"),
        "defaultEquipId": char.get("defaultEquipId"),
        "skills": char.get("skills"),
        "equip": char.get("equip"),
    }


async def _run_chain(client: SklandClient, scan_code: str, report: dict[str, Any]) -> dict[str, Any]:
    """``scanCode`` → token → code → cred → 角色 → 干员数据；结果填进 ``report``。"""
    token = await client.token_by_scan_code(scan_code)
    report["token_by_scan_code"] = {"token_len": len(token)}

    code = await client.grant(token)
    report["grant"] = {"code_len": len(code)}

    credential = await client.generate_cred(code)
    report["generate_cred"] = {"cred_len": len(credential.cred), "key_len": len(credential.token)}

    refreshed = await client.refresh(credential)
    report["refresh"] = {"cred_same": refreshed.cred == credential.cred, "key_len": len(refreshed.token)}
    credential = refreshed

    roles = await client.binding(credential)
    report["binding"] = {
        "role_count": len(roles),
        "roles": [
            {
                "uid_len": len(role.uid),
                "app_code": role.app_code,
                "channel_master_id": role.channel_master_id,
                "nickname_len": len(role.nickname) if role.nickname else 0,
                "level": role.level,
                "is_default": role.is_default,
            }
            for role in roles
        ],
    }
    if not roles:
        report["error"] = "binding 里没有 arknights 角色"
        return report

    role = next((r for r in roles if r.is_default), roles[0])
    data = await client.player_info(credential, role.uid)

    chars = data.get("chars") or []
    report["player_info"] = {
        "top_keys": sorted(data),
        "status": _shape(data.get("status")),
        "status_level": (data.get("status") or {}).get("level"),
        "status_name_len": len((data.get("status") or {}).get("name") or ""),
        "building_keys": sorted((data.get("building") or {})),
        "chars_count": len(chars),
        "char_keys_union": sorted({k for c in chars for k in c}),
        "char_sample_first": _char_sample(chars[0]) if chars else None,
        "char_sample_last": _char_sample(chars[-1]) if chars else None,
        "evolve_phase_values": sorted({c.get("evolvePhase") for c in chars}),
        "potential_rank_values": sorted({c.get("potentialRank") for c in chars}),
        "favor_percent_min_max": _min_max([c.get("favorPercent") for c in chars]),
        "skill_ids_sample": [s.get("id") for c in chars[:3] for s in (c.get("skills") or [])],
        "equip_ids_sample": [e.get("id") for c in chars[:3] for e in (c.get("equip") or [])],
        "chars_with_equip": sum(1 for c in chars if c.get("equip")),
        "char_info_map": _map_shape(data.get("charInfoMap")),
        "equipment_info_map": _map_shape(data.get("equipmentInfoMap")),
        "other_map_like_keys": sorted(
            k for k, v in data.items() if isinstance(v, dict) and k not in ("status", "building")
        ),
    }
    report["credential_left_len"] = {"cred": len(credential.cred), "key": len(credential.token)}
    return report


async def _cmd_poll(args: argparse.Namespace) -> int:
    report: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "scan_id": args.scan_id,
        "headers": {"platform": PLATFORM_HEADER, "vName": VNAME_HEADER},
    }
    client = SklandClient(extra_headers={"platform": PLATFORM_HEADER, "vName": VNAME_HEADER})
    try:
        status, scan_code, msg, trace = await _poll_scan_code(client, args.scan_id, args.timeout)
        report["scan_status"] = {"final_status": status, "msg": msg, "trace": trace}
        if not scan_code:
            report["error"] = "没有拿到 scanCode"
            _dump(report)
            return 1
        report = await _run_chain(client, scan_code, report)
    finally:
        await client.aclose()

    _dump(report)
    if report.get("error"):
        print(f"\n报告：{OUT_DIR / 'report.json'}（失败：{report['error']}）")
        return 1
    print(f"\n报告：{OUT_DIR / 'report.json'}")
    print(f"干员数 = {report['player_info']['chars_count']}")
    return 0


async def _cmd_watch(args: argparse.Namespace) -> int:
    """等人扫码：会话过期就自动换一张新码，直到扫到或超时。

    上游的扫码会话实测约 3 分钟失效（§2.1），而人未必正好在屏幕前，所以这里把
    「生成 → 等 → 过期再生成」串起来；每换一张都会重写并打开 ``qr.png``。
    """
    deadline = time.monotonic() + args.minutes * 60
    client = SklandClient(extra_headers={"platform": PLATFORM_HEADER, "vName": VNAME_HEADER})
    rounds = 0
    try:
        while time.monotonic() < deadline:
            rounds += 1
            login = await client.generate_scan()
            _write_qr(login.scan_url)
            logger.info(
                "第 %d 张二维码已写：%s（scan_id=%s）", rounds, OUT_DIR / "qr.png", login.scan_id
            )
            if args.open:
                subprocess.run(["open", str(OUT_DIR / "qr.png")], check=False)
            try:
                status, scan_code, msg, trace = await _poll_scan_code(
                    client, login.scan_id, min(args.session_timeout, max(deadline - time.monotonic(), 1))
                )
            except TimeoutError:
                logger.info("这一张还没人扫（已过期），换一张")
                continue
            if not scan_code:
                logger.info("这一张还没有结论（status=%s），换一张", status)
                continue
            report: dict[str, Any] = {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "scan_id": login.scan_id,
                "headers": {"platform": PLATFORM_HEADER, "vName": VNAME_HEADER},
                "scan_status": {"final_status": status, "msg": msg, "trace": trace},
            }
            report = await _run_chain(client, scan_code, report)
            _dump(report)
            if report.get("error"):
                print(f"\n报告：{OUT_DIR / 'report.json'}（失败：{report['error']}）")
                return 1
            print(f"\n报告：{OUT_DIR / 'report.json'}")
            print(f"干员数 = {report['player_info']['chars_count']}")
            return 0
    finally:
        await client.aclose()
    logger.error("等待 %.0f 分钟仍没有扫码（共生成 %d 张码）", args.minutes, rounds)
    return 1


def _map_shape(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not value:
        return {"count": 0}
    key, entry = next(iter(value.items()))
    return {
        "count": len(value),
        "sample_key": key,
        "sample_entry_keys": sorted(entry) if isinstance(entry, dict) else [type(entry).__name__],
        "sample_entry": entry if isinstance(entry, dict) else None,
    }


def _min_max(values: list[Any]) -> dict[str, Any]:
    numbers = [v for v in values if isinstance(v, int | float)]
    return {"min": min(numbers), "max": max(numbers)} if numbers else {"min": None, "max": None}


def _dump(report: dict[str, Any]) -> None:
    (OUT_DIR / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("new", help="生成扫码会话与二维码")
    poll = sub.add_parser("poll", help="轮询扫码结果并跑完取数链路")
    poll.add_argument("scan_id")
    poll.add_argument(
        "--timeout",
        type=float,
        default=POLL_TIMEOUT_SECONDS,
        help=f"轮询上限秒数（默认 {POLL_TIMEOUT_SECONDS:.0f}；上游会话实测约 3 分钟后失效）",
    )
    watch = sub.add_parser("watch", help="等人扫码：过期自动换新码，直到扫到或超时")
    watch.add_argument("--minutes", type=float, default=20.0, help="总等待分钟数（默认 20）")
    watch.add_argument(
        "--session-timeout",
        type=float,
        default=150.0,
        help="单张二维码的等待秒数（默认 150，略小于实测的约 180 秒失效窗口）",
    )
    watch.add_argument("--no-open", dest="open", action="store_false", help="不自动打开图片")
    args = parser.parse_args(argv)

    _setup_logging()
    try:
        if args.cmd == "new":
            return asyncio.run(_cmd_new(args))
        if args.cmd == "watch":
            return asyncio.run(_cmd_watch(args))
        return asyncio.run(_cmd_poll(args))
    except AppError as exc:
        logger.error("上游报错：%s（%s）", exc.code, exc.message)
        return 1
    except TimeoutError as exc:
        logger.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
