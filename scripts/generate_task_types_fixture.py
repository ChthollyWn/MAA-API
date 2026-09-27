#!/usr/bin/env python
"""从后端 schema 源重新生成前端的任务类型 fixture。

``web/src/features/tasks/task-types.fixture.json`` 必须与 ``GET /api/tasks/types``
返回的结构完全一致，否则前端测试验证的是一份生产永远不会返回的契约 —— M13 复审
就发现 Copilot 条目缺 ``required``/``runtime_immutable`` 且没有 per-field 描述，
导致「运行中不可修改 asset_id」与必填语义在 fixture 层面完全没有覆盖。

用法::

    .venv/bin/python scripts/generate_task_types_fixture.py            # 写入
    .venv/bin/python scripts/generate_task_types_fixture.py --check    # 只校验漂移
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from maa_api.api.routers.tasks import TASK_MODELS, export_type_schema  # noqa: E402

TARGET = ROOT / "web" / "src" / "features" / "tasks" / "task-types.fixture.json"


def build_payload() -> dict[str, object]:
    items = [export_type_schema(model) for model in TASK_MODELS.values()]
    return {"items": items, "total": len(items), "page": 1, "size": len(items)}


def render(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验 fixture 是否与后端 schema 一致，不写文件",
    )
    args = parser.parse_args()

    expected = render(build_payload())
    if args.check:
        actual = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if actual != expected:
            print(
                f"{TARGET.relative_to(ROOT)} 与后端任务 schema 不一致，"
                "请运行 .venv/bin/python scripts/generate_task_types_fixture.py",
                file=sys.stderr,
            )
            return 1
        print(f"{TARGET.relative_to(ROOT)} is current")
        return 0

    TARGET.write_text(expected, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
