"""Export or verify the stable OpenAPI snapshot consumed by the frontend."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from maa_api.main import app


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "web" / "openapi.json"


def serialized_schema() -> str:
    return json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail without writing when the checked snapshot differs from the backend",
    )
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    content = serialized_schema()

    if args.check:
        try:
            current = output.read_text(encoding="utf-8")
        except FileNotFoundError:
            current = ""
        if current != content:
            print(f"OpenAPI snapshot is stale: {output.relative_to(REPO_ROOT)}", file=sys.stderr)
            return 1
        print(f"OpenAPI snapshot is current: {output.relative_to(REPO_ROOT)}")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(f"Wrote {output.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
