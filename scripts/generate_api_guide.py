"""Generate the marked error-code and OpenAPI-tag tables in docs/14."""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping
import re
import sys
from pathlib import Path

from maa_api.domain.errors import ERROR_HTTP_STATUS, ErrorCode
from maa_api.main import TAGS

REPO_ROOT = Path(__file__).resolve().parents[1]
GUIDE_PATH = REPO_ROOT / "docs" / "14-开放API接入指南.md"
ERROR_SPEC_PATH = REPO_ROOT / "docs" / "05-API规范与路由清单.md"
_ERROR_ROW = re.compile(
    r"^\|\s*`([A-Z0-9_]+)`\s*\|\s*\d{3}\s*\|\s*(.+?)\s*\|\s*$"
)
_RESERVED_ERROR_PREFIXES = ("CONFIRMATION_", "AGENT_", "TOOL_", "LLM_")
_RESERVED_ERROR_DESCRIPTION = "预留错误码；对应功能未交付（M11/M13）"


def parse_error_descriptions(markdown: str) -> dict[str, str]:
    """Read the authoritative human descriptions from docs/05 tables."""
    descriptions: dict[str, str] = {}
    for line in markdown.splitlines():
        match = _ERROR_ROW.match(line)
        if match:
            descriptions[match.group(1)] = match.group(2).replace("|", r"\|")
    return descriptions


def error_codes_and_statuses(
    codes: Iterable[ErrorCode], statuses: Mapping[ErrorCode, int]
) -> tuple[list[str], dict[str, int]]:
    """Require a status mapping for every HTTP error code before rendering."""
    exposed_codes = [code for code in codes if code is not ErrorCode.UPDATE_INTERRUPTED]
    missing = [code for code in exposed_codes if code not in statuses]
    if missing:
        names = ", ".join(code.value for code in missing)
        raise ValueError(f"错误码缺少 HTTP 状态码映射：{names}")
    unexpected = [code for code in statuses if code not in exposed_codes]
    if unexpected:
        names = ", ".join(code.value for code in unexpected)
        raise ValueError(f"存在未对应 HTTP 错误码的状态映射：{names}")
    return (
        [code.value for code in exposed_codes],
        {code.value: statuses[code] for code in exposed_codes},
    )


def render_error_table(
    *,
    codes: list[str],
    statuses: dict[str, int],
    source_rows: dict[str, str],
) -> str:
    """Render every exposed HTTP error with its contract description."""
    missing = [code for code in codes if code not in source_rows]
    if missing:
        raise ValueError(f"缺少错误码说明（docs/05）：{', '.join(missing)}")
    lines = ["| 错误码 | HTTP | 含义与触发场景 |", "|---|---:|---|"]
    for code in codes:
        description = source_rows[code]
        if code.startswith(_RESERVED_ERROR_PREFIXES):
            description = _RESERVED_ERROR_DESCRIPTION
        lines.append(f"| `{code}` | {statuses[code]} | {description} |")
    return "\n".join(lines)


def render_tag_table(tags: list[dict[str, str]]) -> str:
    lines = ["| OpenAPI tag | 说明 |", "|---|---|"]
    for tag in tags:
        description = tag.get("description", "").replace("|", "\\|")
        lines.append(f"| `{tag['name']}` | {description} |")
    return "\n".join(lines)


def replace_generated_section(source: str, name: str, content: str) -> str:
    """Replace only one uniquely marked generated block, preserving both markers."""
    start = f"<!-- GENERATED:{name}:START -->"
    end = f"<!-- GENERATED:{name}:END -->"
    if source.count(start) != 1 or source.count(end) != 1:
        raise ValueError(f"文档必须且只能包含一组 {name} 生成标记")
    start_index = source.index(start) + len(start)
    end_index = source.index(end)
    if end_index < start_index:
        raise ValueError(f"{name} 生成标记顺序错误")
    return source[:start_index] + "\n" + content.rstrip() + "\n" + source[end_index:]


def generated_guide(source: str, error_spec: str) -> str:
    descriptions = parse_error_descriptions(error_spec)
    codes, statuses = error_codes_and_statuses(ErrorCode, ERROR_HTTP_STATUS)
    updated = replace_generated_section(
        source,
        "ERROR-CODES",
        render_error_table(
            codes=codes,
            statuses=statuses,
            source_rows=descriptions,
        ),
    )
    return replace_generated_section(updated, "OPENAPI-TAGS", render_tag_table(TAGS))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if docs/14 is stale")
    parser.add_argument("guide", nargs="?", type=Path, default=GUIDE_PATH)
    args = parser.parse_args()
    guide_path = args.guide if args.guide.is_absolute() else REPO_ROOT / args.guide
    current = guide_path.read_text(encoding="utf-8")
    error_spec = ERROR_SPEC_PATH.read_text(encoding="utf-8")
    expected = generated_guide(current, error_spec)
    if args.check:
        if current != expected:
            print(f"API guide generated sections are stale: {guide_path}", file=sys.stderr)
            return 1
        print(f"API guide generated sections are current: {guide_path.relative_to(REPO_ROOT)}")
        return 0
    guide_path.write_text(expected, encoding="utf-8")
    print(f"Updated generated sections in {guide_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
