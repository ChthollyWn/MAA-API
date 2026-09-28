#!/usr/bin/env python3
"""真内核探针：测定「任务参数缺省时内核要什么」与「全默认参数能否被接受」。

背景：``AsstAppendTask`` 校验必填参数，缺参数时**静默返回 0**（docs/ENVIRONMENT.md
「MaaCore 内核」节、tests/fixtures/spike_subprocess_findings.md §7.2）。我们要给
每个任务参数补 schema ``default`` 并让前端预填下发，就必须先确认：

1. 哪些任务类型「空参数就能 append」（即内核真的不需要显式参数）；
2. 哪些类型有必填项，缺了就返回 0（这些字段必须补默认值或标必填）；
3. 拟定的一组默认值（``--defaults``，JSON 文件）能否被真内核接受。

不需要连设备：append 只走 ``set_params`` 校验，不触碰 ADB。

用法::

    DYLD_LIBRARY_PATH=$PWD/resource/lib/maa/Darwin \\
        .venv/bin/python scripts/probe_task_defaults.py

    DYLD_LIBRARY_PATH=$PWD/resource/lib/maa/Darwin \\
        .venv/bin/python scripts/probe_task_defaults.py --model-defaults

    # 追加校验一组拟定的默认值（JSON: {"StartUp": {...}, "Fight": {...}}）
    DYLD_LIBRARY_PATH=$PWD/resource/lib/maa/Darwin \\
        .venv/bin/python scripts/probe_task_defaults.py --defaults /tmp/defaults.json

``task_id == 0`` 即被内核拒绝。脚本不改任何仓库文件，成功打印 ``PROBE OK``。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

#: 每个候选参数集都 append 一次；顺序即输出顺序。
#: 第一组（空参数）用来暴露必填项，后面的组用来定位「到底缺哪个」。
CANDIDATES: list[tuple[str, str, dict]] = [
    ("StartUp", "空参数", {}),
    ("StartUp", "只给 client_type", {"client_type": "Official"}),
    ("StartUp", "client_type + enable", {"client_type": "Official", "enable": True}),
    ("CloseDown", "空参数", {}),
    ("CloseDown", "只给 client_type", {"client_type": "Official"}),
    ("Fight", "空参数", {}),
    ("Fight", "stage 空串", {"stage": ""}),
    ("Fight", "stage CE-6", {"stage": "CE-6"}),
    ("Recruit", "空参数", {}),
    ("Recruit", "只给 times", {"times": 4}),
    ("Recruit", "只给 select", {"select": [1, 4, 5]}),
    ("Recruit", "只给 confirm", {"confirm": [2, 3, 4, 5]}),
    ("Recruit", "select + confirm", {"select": [1, 4, 5], "confirm": [2, 3, 4, 5]}),
    ("Recruit", "select + confirm + times", {"select": [1, 4, 5], "confirm": [2, 3, 4, 5], "times": 4}),
    ("Infrast", "空参数", {}),
    ("Infrast", "只给 facility", {"facility": ["Mfg"]}),
    ("Infrast", "facility + mode", {"facility": ["Mfg", "Trade", "Power", "Control", "Reception", "Office", "Dorm"], "mode": 0}),
    ("Mall", "空参数", {}),
    ("Award", "空参数", {}),
    ("Roguelike", "空参数", {}),
    ("Roguelike", "theme + mode", {"theme": "Phantom", "mode": 0}),
    ("Reclamation", "空参数", {}),
    ("Reclamation", "theme + mode", {"theme": "Tales", "mode": 0}),
    ("Copilot", "空参数", {}),
    ("Copilot", "filename 不存在", {"filename": "__probe_missing__.json"}),
]


def model_defaults() -> dict[str, dict]:
    """把服务端模型的全默认参数导出成候选集（模拟「前端预填并显式下发」）。

    ``client_type`` / ``server`` 在模型里是 ``None``（服务端按全局渠道默认值注入），
    但 M17 之后前端会把生效值显式下发，所以这里补上 ``ChannelDefaults()`` 的值，
    否则 ``StartUp`` 会因为没有 ``client_type`` 被内核拒掉 —— 那验证的就不是
    真实提交体了。``Copilot`` 的 ``asset_id`` 是必填且由服务层映射成 ``filename``，
    没有可独立构造的全默认提交体，跳过。
    """
    from maa_api.domain.task import TASK_MODELS, ChannelDefaults

    channel = ChannelDefaults()
    exported: dict[str, dict] = {}
    for type_name, model in TASK_MODELS.items():
        if type_name == "Copilot":
            continue
        try:
            params = model().model_dump(exclude={"name"}, exclude_none=True, by_alias=True)
        except Exception:  # noqa: BLE001 - 必填字段没法用「全默认」构造，如实跳过
            continue
        if "client_type" in model.model_fields:
            params["client_type"] = channel.client_type
        if "server" in model.model_fields:
            params["server"] = channel.server
        exported[type_name] = params
    return exported


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用真内核测定任务参数缺省行为")
    parser.add_argument(
        "--maa-path",
        default=os.environ.get("MAA_PATH") or str(REPO_ROOT / "resource/lib/maa" / platform.system()),
        help="内核目录（含 libMaaCore.* 与 resource/），默认 resource/lib/maa/<系统>",
    )
    parser.add_argument("--defaults", type=Path, default=None, help="追加校验的默认值 JSON")
    parser.add_argument(
        "--model-defaults",
        action="store_true",
        help="追加校验服务端模型的全默认参数（含渠道默认值，跳过 Copilot）",
    )
    parser.add_argument("--json", type=Path, default=None, help="把结果写进这个 JSON 文件")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # 延迟 import：跑 --help 时不 dlopen 内核，也不触发 config 的目录副作用。
    from maa_api.core.asst import Asst

    maa_path = Path(args.maa_path)
    if not maa_path.is_dir():
        print(f"内核目录不存在：{maa_path}", file=sys.stderr)
        return 1

    user_dir = Path(tempfile.mkdtemp(prefix="maa-probe-"))
    if not Asst.load(path=maa_path, user_dir=user_dir):
        print("内核与资源加载失败", file=sys.stderr)
        return 1

    candidates = list(CANDIDATES)
    if args.model_defaults:
        for type_name, params in model_defaults().items():
            candidates.append((type_name, "模型全默认", params))
    if args.defaults is not None:
        payload = json.loads(args.defaults.read_text(encoding="utf-8"))
        for type_name, params in payload.items():
            candidates.append((type_name, "拟定默认值", params))

    asst = Asst()
    results: list[dict] = []
    for type_name, note, params in candidates:
        try:
            task_id = asst.append_task(type_name, params)
        except Exception as exc:  # noqa: BLE001 - 探针要如实记录任何异常
            task_id, note = -1, f"{note}（异常：{exc!r}）"
        results.append(
            {
                "type": type_name,
                "note": note,
                "params": params,
                "task_id": task_id,
                "accepted": task_id != 0,
            }
        )
        flag = "OK " if task_id != 0 else "拒绝"
        print(f"{flag} {type_name:<12} {note:<24} task_id={task_id}  params={json.dumps(params, ensure_ascii=False)}")

    if args.json is not None:
        args.json.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n汇总（被拒绝的候选）：")
    rejected = [row for row in results if not row["accepted"]]
    for row in rejected:
        print(f"  {row['type']:<12} {row['note']}")
    if not rejected:
        print("  （无）")
    print("PROBE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
