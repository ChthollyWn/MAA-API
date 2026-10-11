#!/usr/bin/env python3
"""M36 W1 探针：在真机上验证「只读节点」能不能读出练度（规格 §2.2 / §4.1）。

要证的四件事：

1. 干员列表的卡名能按列读全（16 列），且列几何能定位到卡面中心；
2. 打开某张卡的详情页后，``RANK n``／专精徽章／精英化徽记／模组编号与 ``STAGE n``
   都能读到（内核自带 ``OperProgress@*`` 的 roi 与模板在国服客户端上仍然对得上）；
3. 命中模板的名字会出现在回调里（专精 0-3、精英化 0-2 靠它判定）；
4. 返回箭头能把界面带回列表、横向划一下能换下一屏（扫描循环的两个导航动作）。

本脚本只做实测取证，不是生产代码：内核资源从内核目录加载，自定义只读节点写进
``/tmp`` 下的一次性资源层，不碰仓库里的 ``resource/``；除了「点开卡片」与「返回/
翻屏」这两类导航动作，不点任何升级/专精/模组按钮。

跑法（先确认模拟器已连上 ``adb``，游戏停在干员列表）::

    .venv/bin/python scripts/probe_training_scan.py --step box
    .venv/bin/python scripts/probe_training_scan.py --step detail --name 风笛
    .venv/bin/python scripts/probe_training_scan.py --step detail --row bottom --column 3
    .venv/bin/python scripts/probe_training_scan.py --step all --name 风笛
    .venv/bin/python scripts/probe_training_scan.py --step swipe

产物：``/tmp/m36_probe_report.json``（原始回调）与 stdout 的人读结论。
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # 允许直接跑脚本
    sys.path.insert(0, str(REPO_ROOT))

from maa_api.core.asst import Asst  # noqa: E402
from maa_api.core.enums import Message  # noqa: E402
from maa_api.services.account import scan_tasks as st  # noqa: E402
from maa_api.services.account.scan_image import mastery_levels_from_image  # noqa: E402

DEFAULT_ADB = "/opt/homebrew/bin/adb"
DEFAULT_ADDRESS = "127.0.0.1:5555"
DEFAULT_KERNEL = REPO_ROOT / "resource" / "lib" / "maa" / "Darwin"

#: 一次自定义任务跑多久算超时。
PIPELINE_TIMEOUT = 90.0


class Probe:
    """跑一轮探针：连设备、按步执行、把所有回调原样收好。"""

    def __init__(self, *, adb: str, address: str, kernel: Path) -> None:
        self.adb = adb
        self.address = address
        self.kernel = kernel
        self.messages: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self.layer_dir: Path | None = None
        self.user_dir: Path | None = None
        self.asst: Asst | None = None
        #: 额外塞进只读层的一次性节点（标定用，不进生产定义）。
        self.extra_tasks: dict[str, dict[str, Any]] = {}

    # -- 回调 ---------------------------------------------------------------

    def _on_message(self, msg: int, details: bytes | None, _arg: Any) -> None:
        record: dict[str, Any] = {"msg": int(msg), "name": _message_name(int(msg))}
        if details:
            try:
                record["details"] = json.loads(details.decode("utf-8"))
            except Exception:  # pragma: no cover - 回调里的脏数据
                record["raw"] = details.decode("utf-8", "replace")[:400]
        with self._lock:
            self.messages.append(record)

    # -- 生命周期 -----------------------------------------------------------

    def start(self) -> None:
        self.user_dir = Path(tempfile.mkdtemp(prefix="m36_probe_user_"))
        if not Asst.load(self.kernel, user_dir=self.user_dir):
            raise SystemExit(f"内核资源加载失败：{self.kernel}")

        # 增量层的根是「含 ``resource/`` 的那一层」（与 ``main.py:527`` 的
        # ``incremental_paths = [<resource>/maa-layers/cache, <resource>/maa-layers/custom]`` 同构）。
        layer_root = Path(tempfile.mkdtemp(prefix="m36_probe_layer_"))
        tasks_dir = layer_root / "resource" / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        definitions = {**st.operator_scan_tasks(), **self.extra_tasks}
        (tasks_dir / st.TASK_FILE_NAME).write_text(
            json.dumps(definitions, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # 只读节点层里只放自定义任务 JSON（专精已经改成自己看截图数亮点，
        # 不再需要往增量层塞模板，见 ``scan_image``）。
        if not Asst.load(layer_root, incremental=True):
            raise SystemExit("自定义只读资源层加载失败")
        self.layer_dir = layer_root

        self.asst = Asst(self._on_message)
        print(f"[probe] 内核 {self.asst.get_version()}；只读节点层 {layer_root}")
        self.asst.connect_async(self.adb, self.address)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.asst.connected():
                break
            time.sleep(0.5)
        else:
            raise SystemExit("连接设备超时（检查模拟器与 adb）")
        print(f"[probe] 已连接 {self.address}")

        # 自检：内核这一版 ``get_tasks_list`` 恒返回空（真机实测），判断「节点进没进任务表」
        # 只能靠 ``append_task`` 成不成——而它会把任务挂进实例，所以用一次性的实例试。
        checker = Asst(lambda *_: None)
        rejected: list[str] = []
        for label, chain in (
            ("卡名链", st.box_screen_nodes()),
            ("详情链", st.detail_screen_nodes()),
            ("返回", (st.BACK,)),
            ("翻屏", (st.SWIPE_NEXT_PAGE,)),
        ):
            if not checker.append_task("Custom", {"task_names": list(chain)}):
                rejected.append(label)
        print(f"[probe] 节点自检：{'全部可提交' if not rejected else '内核不接受 ' + '、'.join(rejected)}")

    def stop(self) -> None:
        for path in (self.layer_dir, self.user_dir):
            if path is not None:
                shutil.rmtree(path, ignore_errors=True)

    # -- 跑节点 -------------------------------------------------------------

    def run_nodes(self, nodes: tuple[str, ...], *, label: str) -> list[dict[str, Any]]:
        """跑一组只读节点，返回这一段之内收到的回调。"""
        assert self.asst is not None
        with self._lock:
            start = len(self.messages)
        call_id = 0
        for attempt in range(5):
            call_id = self.asst.append_task("Custom", {"task_names": list(nodes)})
            if call_id:
                break
            print(f"[probe] {label} 第 {attempt + 1} 次提交返回 0，1 秒后重试")
            time.sleep(1.0)
        if call_id == 0:
            raise SystemExit(f"{label} 提交失败：内核不接受这条链（入口 {nodes[0]}），先看节点自检")
        if not self.asst.start():
            raise SystemExit("引擎启动失败")
        deadline = time.monotonic() + PIPELINE_TIMEOUT
        while time.monotonic() < deadline:
            if not self.asst.running():
                break
            time.sleep(0.2)
        else:
            self.asst.stop()
            print(f"[probe] {label} 超时，已停止")
        time.sleep(0.5)
        with self._lock:
            return list(self.messages[start:])

    def click(self, x: int, y: int) -> None:
        assert self.asst is not None
        self.asst.click(int(x), int(y))
        time.sleep(2.0)

    def tap_back(self) -> None:
        """跑返回箭头节点（导航用，不消耗资源）。"""
        assert self.asst is not None
        self.run_nodes((st.BACK,), label="back")

    def run_chain(
        self, nodes: tuple[str, ...], *, label: str, max_rounds: int = 24
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """按段跑一条链，返回 ``（全部回调, 读不出来的节点短名）``。

        真机实测：链上**某个节点识别不出东西时整条链就结束**——后面的节点不会被尝试，
        ``exceededNext`` 也没把它接上。所以想读满一屏，只能自己从停下的那个节点重新
        提交。这也是生产侧扫描服务要用的读法（规格 §4.3）。
        """
        collected: list[dict[str, Any]] = []
        gaps: list[str] = []
        index = 0
        rounds = 0
        while index < len(nodes) and rounds < max_rounds:
            rounds += 1
            chunk = list(nodes[index:])
            messages = self.run_nodes(tuple(chunk), label=f"{label}[{index}]")
            collected.extend(messages)
            reached = -1
            for record in _recognitions(messages):
                reported = st.node_key(record["task"])
                for offset, name in enumerate(chunk):
                    if st.node_key(name) == reported:
                        reached = max(reached, index + offset)
            if reached < index:
                gaps.append(st.node_key(nodes[index]))
                print(f"[probe] {label}：{st.node_key(nodes[index])} 读不出来，跳过它继续")
                index += 1
            else:
                index = reached + 1
        return collected, gaps


# ---------------------------------------------------------------------------
# 回调解析
# ---------------------------------------------------------------------------


def _message_name(msg: int) -> str:
    try:
        return Message(msg).name
    except ValueError:
        return f"msg{msg}"


def _inner(record: dict[str, Any]) -> dict[str, Any]:
    """``SubTask*`` 回调里真正带识别结果的是内层 ``details``。

    外层是 ``{class, details:{action, algorithm, result, task}, first, pre_task, subtask,…}``。
    """
    outer = record.get("details")
    if not isinstance(outer, dict):
        return {}
    inner = outer.get("details")
    return inner if isinstance(inner, dict) else {}


def _recognitions(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从回调里挑出「带识别结果」的那些（OCR / 模板匹配），按节点短名归位。"""
    found: list[dict[str, Any]] = []
    for record in messages:
        inner = _inner(record)
        result = inner.get("result")
        if not result:
            continue
        found.append(
            {
                "msg": record["name"],
                "task": inner.get("task"),
                "key": st.node_key(inner.get("task")),
                "result": [result] if isinstance(result, dict) else list(result),
            }
        )
    return found


def _readings(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """把一段回调压成 ``{节点短名: 第一条识别结果}``。"""
    readings: dict[str, dict[str, Any]] = {}
    for record in _recognitions(messages):
        for item in record["result"]:
            if isinstance(item, dict):
                readings.setdefault(record["key"], item)
    return readings


def _print_messages(messages: list[dict[str, Any]]) -> None:
    print(f"[probe] 这一段收到 {len(messages)} 条回调")
    for record in messages:
        inner = _inner(record)
        if not inner:
            continue
        print(
            f"  - [{record['name']}] task={inner.get('task')} "
            f"result={json.dumps(inner.get('result'), ensure_ascii=False)}"
        )


def _print_readings(label: str, readings: dict[str, dict[str, Any]]) -> None:
    print(f"\n=== {label} ===")
    if not readings:
        print("（一个识别结果都没有）")
    for key, item in readings.items():
        print(
            f"- {key}: text={item.get('text')!r} template={item.get('template')} "
            f"score={item.get('score')} rect={item.get('rect')}"
        )


# ---------------------------------------------------------------------------
# 步骤
# ---------------------------------------------------------------------------


def read_box(probe: Probe, *, label: str = "box") -> tuple[list[dict[str, Any]], dict[tuple[str, int], str]]:
    """读一屏卡名（按段续跑），返回 ``（原始回调, {(行, 列): 名字})``。"""
    messages, gaps = probe.run_chain(st.box_screen_nodes(), label=label)
    names: dict[tuple[str, int], str] = {}
    for record in _recognitions(messages):
        place = st.parse_card_name_node(record["task"])
        if place is None:
            continue
        for item in record["result"]:
            text = (item or {}).get("text") if isinstance(item, dict) else None
            if text:
                names.setdefault(place, str(text))
    if gaps:
        print(f"[probe] 读不出来的格子：{gaps}")
    return messages, names


def print_box(names: dict[tuple[str, int], str]) -> None:
    print(f"\n=== 卡名（读到 {len(names)}/{st.GRID_COLUMNS * st.GRID_ROWS} 格）===")
    for row in st.ROW_KEYS:
        cells = [f"{column}:{names.get((row, column)) or '—'}" for column in range(st.GRID_COLUMNS)]
        print(f"- {row:>6}: " + "  ".join(cells))


def locate(names: dict[tuple[str, int], str], target: str) -> tuple[str, int] | None:
    for (row, column), text in names.items():
        if text == target:
            return row, column
    for (row, column), text in names.items():
        if target in text:
            return row, column
    return None


def step_box(probe: Probe) -> tuple[list[dict[str, Any]], dict[tuple[str, int], str]]:
    messages, names = read_box(probe)
    _print_messages(messages)
    _print_readings("干员列表卡名 OCR", _readings(messages))
    print_box(names)
    return messages, names


def step_detail(probe: Probe, *, row: str, column: int) -> list[dict[str, Any]]:
    x, y = st.card_center(row, column)
    print(f"\n[probe] 点开 {row} 第 {column + 1} 列：内核坐标 ({x}, {y})")
    probe.click(x, y)
    messages: list[dict[str, Any]] = []
    try:
        messages, gaps = probe.run_chain(st.detail_screen_nodes(), label="detail")
        _print_messages(messages)
        _print_readings("详情页读数", _readings(messages))
        if gaps:
            print(f"[probe] 详情页读不出来的节点：{gaps}")
    finally:
        # 不管这一步成不成，都要把游戏带回列表页——否则后面的读数全是在详情页上测的。
        probe.tap_back()
        print("[probe] 已按返回箭头")
    return messages


def step_read(
    probe: Probe, *, row: str, column: int, image_path: Path | None = None
) -> list[dict[str, Any]]:
    """跑一遍**生产详情链**，再自己数专精——真机验收就靠它复现读数。

    专精不在内核里读（模板路没有区分度，见 ``scan_tasks`` 里那段实测），而是拿
    ``AsstGetImage`` 的那一帧截图自己数亮点；所以这一步合起来看「内核读出来的
    （技能等级 / 精英化 / 模组）+ 截图数出来的（专精）」。
    """
    x, y = st.card_center(row, column)
    print(f"\n[probe] 点开 {row} 第 {column + 1} 列：内核坐标 ({x}, {y})")
    probe.click(x, y)
    messages: list[dict[str, Any]] = []
    try:
        messages, gaps = probe.run_chain(st.detail_screen_nodes(), label="detail")
        _print_messages(messages)
        readings = _readings(messages)
        _print_readings("详情页读数", readings)
        if gaps:
            print(f"[probe] 详情页读不出来的节点：{gaps}")
        print_detail_summary(readings, capture_screen(probe, image_path))
    finally:
        # 不管这一步成不成，都要把游戏带回列表页——否则后面的读数全是在详情页上测的。
        probe.tap_back()
        print("[probe] 已按返回箭头")
    return messages


def capture_screen(probe: Probe, image_path: Path | None = None) -> Image.Image | None:
    """抓当前屏（就是内核 ``AsstGetImage`` 那一帧），顺带存一份便于回看。"""
    assert probe.asst is not None
    raw = probe.asst.get_image()
    if not raw:
        print("[probe] 取图失败（分辨率还没上报？）")
        return None
    path = Path(image_path or DEFAULT_IMAGE)
    path.write_bytes(raw)
    print(f"[probe] 截图存到 {path}")
    image = Image.open(io.BytesIO(raw))
    image.load()
    return image


def print_detail_summary(
    readings: dict[str, dict[str, Any]], image: Image.Image | None
) -> None:
    """把「内核读数 + 截图数出来的专精」打成一段人看的读数。"""
    print("\n=== 详情页读数（生产路径）===")
    print(f"- 技能等级：{readings.get('SkillLevel', {}).get('text')!r}")
    for name, level in st.ELITE_NODE_LEVELS.items():
        hit = readings.get(st.node_key(name))
        if hit:
            print(f"- 精英化：E{level}（{hit.get('template')} score={hit.get('score')}）")
    print(
        f"- 模组：{readings.get('ModuleCode', {}).get('text')!r}"
        f" / STAGE {readings.get('ModuleStage', {}).get('text')!r}"
    )
    if image is None:
        print("- 专精：没取到截图，跳过")
        return
    masteries = mastery_levels_from_image(image)
    print(
        "- 专精（截图数亮点）："
        + "  ".join(f"{slot} 技能={level}" for slot, level in sorted(masteries.items()))
    )


def step_back(probe: Probe) -> list[dict[str, Any]]:
    """把游戏从详情页带回列表（探针被上一次失败留在详情页时的收尾）。"""
    messages = probe.run_nodes((st.BACK,), label="back")
    _print_messages(messages)
    print("[probe] 已按返回箭头")
    return messages


def step_swipe(probe: Probe) -> list[dict[str, Any]]:
    messages = probe.run_nodes((st.SWIPE_NEXT_PAGE,), label="swipe")
    _print_messages(messages)
    print("[probe] 已翻一屏")
    return messages


def raw_name_nodes() -> dict[str, dict[str, Any]]:
    """一次性标定：用「不带错字修正表」的裸 OCR 读卡名。

    生产定义走 ``baseTask: OperBoxNameOCR``（内核那张 ``CharsNameOcrReplace`` 表），
    但真机上总有几格读回来是空字符串——要分清是「OCR 没认出来」还是「那张表把不认识的
    名字改没了」。这里每个 roi 与生产完全一样，只是换成裸 ``OcrDetect``，链式接法与
    生产同构（命中/没命中都往下走）。
    """
    nodes: dict[str, dict[str, Any]] = {}
    names: list[str] = []
    for row in st.ROW_KEYS:
        for column in range(st.GRID_COLUMNS):
            name = f"{st.SCAN_TASK_PREFIX}@RawName{row.title()}Col{column}"
            names.append(name)
            nodes[name] = {
                "algorithm": "OcrDetect",
                "text": [],
                "roi": [st.COLUMN_PITCH * column, st.NAME_BAND_Y[row], st.COLUMN_PITCH, st.NAME_BAND_HEIGHT],
                "action": "DoNothing",
                "maxTimes": 1,
                "doc": f"一次性标定：裸 OCR 读 {row} 第 {column + 1} 列卡名",
            }
    for index, name in enumerate(names):
        if index:
            nodes[name]["cache"] = True
        if index + 1 < len(names):
            nodes[name]["next"] = [names[index + 1]]
            nodes[name]["exceededNext"] = [names[index + 1]]
    return nodes


def step_names(probe: Probe) -> list[dict[str, Any]]:
    ordered = tuple(
        f"{st.SCAN_TASK_PREFIX}@RawName{row.title()}Col{column}"
        for row in st.ROW_KEYS
        for column in range(st.GRID_COLUMNS)
    )
    messages = probe.run_nodes(ordered, label="rawnames")
    _print_messages(messages)
    _print_readings("裸 OCR 卡名", _readings(messages))
    return messages


#: 下排卡名条的 roi 候选：(y, 高度)。生产用的是 (666, 30)。
BAND_VARIANTS: tuple[tuple[int, int], ...] = ((658, 24), (662, 26), (666, 30), (670, 26), (674, 22))


def band_nodes(columns: tuple[int, ...]) -> dict[str, dict[str, Any]]:
    """一次性标定：下排查名条在几个 (y, 高度) 候选下的读数对比。"""
    nodes: dict[str, dict[str, Any]] = {}
    names: list[str] = []
    for index, (y, height) in enumerate(BAND_VARIANTS):
        for column in columns:
            name = f"{st.SCAN_TASK_PREFIX}@BandV{index}C{column}"
            names.append(name)
            nodes[name] = {
                "text": [],
                "roi": [st.COLUMN_PITCH * column, y, st.COLUMN_PITCH, height],
                "action": "DoNothing",
                "baseTask": st.CARD_NAME_BASE_TASK,
                "maxTimes": 1,
                "doc": f"标定：下排第 {column + 1} 列 y={y} h={height}",
            }
    for index, name in enumerate(names):
        if index:
            nodes[name]["cache"] = True
        if index + 1 < len(names):
            nodes[name]["next"] = [names[index + 1]]
            nodes[name]["exceededNext"] = [names[index + 1]]
    return nodes


def step_bands(probe: Probe) -> list[dict[str, Any]]:
    ordered = tuple(
        f"{st.SCAN_TASK_PREFIX}@BandV{index}C{column}"
        for index in range(len(BAND_VARIANTS))
        for column in BAND_COLUMNS
    )
    messages = probe.run_nodes(ordered, label="bands")
    _print_messages(messages)
    readings = _readings(messages)
    print("\n=== 下排查名条候选对比 ===")
    for index, (y, height) in enumerate(BAND_VARIANTS):
        cells = []
        for column in BAND_COLUMNS:
            item = readings.get(f"BandV{index}C{column}") or {}
            cells.append(f"{column}:{item.get('text') or '—'}")
        print(f"- y={y} h={height}: " + "  ".join(cells))
    return messages


#: 标定用的下排列（真机上读不出来的那几列）。
BAND_COLUMNS: tuple[int, ...] = (0, 3, 6, 7)

#: 读数时顺手存下的整屏截图（W7 取证用）。
DEFAULT_IMAGE = Path("/tmp/m36_detail_screen.png")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default=DEFAULT_ADB)
    parser.add_argument("--address", default=DEFAULT_ADDRESS)
    parser.add_argument("--kernel", default=str(DEFAULT_KERNEL))
    parser.add_argument(
        "--step",
        choices=("box", "detail", "read", "swipe", "back", "names", "bands", "all"),
        default="all",
        help="跑哪一步",
    )
    parser.add_argument("--name", default="", help="按卡名挑一个干员点开（不填就用 --row/--column）")
    parser.add_argument("--row", choices=st.ROW_KEYS, default="top")
    parser.add_argument("--column", type=int, default=0, help="第几列（0 起）")
    parser.add_argument("--report", default="/tmp/m36_probe_report.json")
    args = parser.parse_args()

    probe = Probe(adb=args.adb, address=args.address, kernel=Path(args.kernel))
    report: dict[str, Any] = {}
    if args.step == "names":
        probe.extra_tasks = raw_name_nodes()
    if args.step == "bands":
        probe.extra_tasks = band_nodes(BAND_COLUMNS)
    try:
        probe.start()
        if args.step == "names":
            report["names"] = step_names(probe)
        if args.step == "bands":
            report["bands"] = step_bands(probe)
        if args.step == "back":
            report["back"] = step_back(probe)
        if args.step in ("box", "all", "detail", "read"):
            box_messages, names = step_box(probe)
            report["box"] = box_messages
            row, column = args.row, args.column
            if args.name:
                place = locate(names, args.name)
                if place is None:
                    raise SystemExit(f"这一屏没有「{args.name}」，先看上面的卡名表")
                row, column = place
                print(f"\n[probe] 「{args.name}」在 {row} 第 {column + 1} 列")
            if args.step in ("detail", "all"):
                report["detail"] = step_detail(probe, row=row, column=column)
            if args.step == "read":
                report["read"] = step_read(probe, row=row, column=column)
        if args.step in ("swipe", "all"):
            report["swipe"] = step_swipe(probe)
            if args.step == "all":
                _, names = read_box(probe, label="box2")
                print_box(names)
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n[probe] 原始回调已写入 {args.report}")
    finally:
        probe.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
