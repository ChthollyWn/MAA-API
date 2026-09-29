"""全量路由清单契约（docs/05 §6 与 web/openapi.json 的一致性门禁）。

docs/05 §6 是路由清单，`web/openapi.json` 是同一批路由的机器可读投影（由
``scripts/dump_openapi.py --check`` 守着，实现改了而快照没重新生成时它先红）。
两者本该永远一致，但清单是**手写**的：M10 时期的行一路被改名、改形、删除之后
没有任何东西回头看它一眼，于是积累了 21 条实现里不存在的端点、6 条实现里有而
文档没有的端点，以及三处对不上的「（N 条）」与总数。这类漂移不会让任何测试变红，
只会让照文档写代码的调用方（和 agent）每天踩空。

本模块是那道回头看的门禁。四条断言：

1. **集合相等**：§6.1、§6.3–§6.16、§6.18 的行集合与 ``web/openapi.json`` 的全部
   operation 集合完全相等（路径里的参数名归一成 ``{}``，``{id}`` 与 ``{update_id}``
   不算差异）。§6.2（WebSocket）与 §6.17（静态与文档入口）不在 OpenAPI 里，排除在
   集合比对之外，只比对行数。
2. **小节计数**：每个小节的「（N 条）」等于该小节表格里的实际行数。
3. **总数**：§6 开头那句声明的 HTTP 端点总数，同时等于 API 小节行数之和与
   ``len(operations)``；同一句里的 WebSocket 与静态入口条数也对上 §6.2 / §6.17。
4. **格式**：方法一律大写、路径一律以 ``/`` 开头且不带尾斜杠（``redirect_slashes=False``
   下写尾斜杠会把调用方引向 404）。

**纯文件读取，不 import ``maa_api.main``**（``tests/api/conftest.py`` 的同一条纪律：
import 期会读真实配置、建目录、连库）。失败信息按「文档有、实现没有」与「实现有、
文档没有」两组逐条列出并带行号，照着删 / 补即可修复。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

#: 仓库根：tests/domain/test_route_inventory.py → parents[2]。不依赖 CWD。
REPO_ROOT = Path(__file__).resolve().parents[2]
API_SPEC_PATH = REPO_ROOT / "docs" / "05-API规范与路由清单.md"
OPENAPI_PATH = REPO_ROOT / "web" / "openapi.json"

#: §6 的小节总数（6.1–6.18）；解析结果对不上说明文档结构变了、门禁已经失效。
SECTION_COUNT = 18
#: 参与集合比对的小节：§6.2 是 WebSocket、§6.17 是静态与文档入口，都不在 OpenAPI 里。
API_SECTIONS = (
    "6.1",
    "6.3",
    "6.4",
    "6.5",
    "6.6",
    "6.7",
    "6.8",
    "6.9",
    "6.10",
    "6.11",
    "6.12",
    "6.13",
    "6.14",
    "6.15",
    "6.16",
    "6.18",
)
WS_SECTION = "6.2"
STATIC_SECTION = "6.17"

#: WebSocket 升级路由（FastAPI 的 ``@router.websocket``，不进 OpenAPI 的 paths）。
WS_ROUTE = ("GET", "/api/ws")

#: 表格「方法」列的合法取值：一律大写。
HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
#: WebSocket 行在清单里也用 ``GET`` 记（升级握手），额外允许一个 ``WS`` 写法。
DOC_METHODS = HTTP_METHODS | {"WS"}

#: 小节标题形如 ``### 6.7 tasks``。
_SECTION_RE = re.compile(r"^### (6\.\d+) (.+)$")
#: 小节计数形如 ``（7 条）``（全角括号）。
_DECLARED_RE = re.compile(r"^（(\d+) 条）$")
#: §6 开头那句：``共 101 条 HTTP 端点……另有 1 条 WebSocket（§6.2）与 4 条静态与文档入口（§6.17）。``
_TOTAL_RE = re.compile(r"共 (\d+) 条 HTTP 端点")
_WS_TOTAL_RE = re.compile(r"另有 (\d+) 条 WebSocket")
_STATIC_TOTAL_RE = re.compile(r"(\d+) 条静态与文档入口")
#: 路径参数：``{sha256}`` / ``{path:path}`` / ``{id}`` 一律归一成 ``{}``。
_PARAM_RE = re.compile(r"\{[^}]*\}")
#: 一格里并排多条路径时的分隔符（``/docs`` · ``/redoc`` · ``/openapi.json``）。
_PATH_SPLIT_RE = re.compile(r"\s*[·、,]\s*|\s*或\s*")
#: 表格数据行：``| GET | `/api/system/health` | …… |``。
_ROW_RE = re.compile(r"^\|(?P<method>[^|]*)\|(?P<path>[^|]*)\|")


@dataclass(frozen=True)
class RouteRow:
    """清单里的一条（方法 + 路径），``line`` 是它在 docs/05 里的行号（1 起）。"""

    method: str
    path: str
    line: int


@dataclass(frozen=True)
class Section:
    """§6 的一个小节：标题、表格行、声明的「（N 条）」。"""

    number: str
    title: str
    rows: tuple[RouteRow, ...]
    table_rows: int
    declared: int | None
    declared_line: int | None
    line: int

    @property
    def label(self) -> str:
        return f"§{self.number} {self.title}"


@dataclass(frozen=True)
class Inventory:
    """§6 的解析结果：所有小节 + 开头声明的总数。"""

    sections: tuple[Section, ...]
    total: int
    total_line: int
    ws_total: int
    static_total: int

    def section(self, number: str) -> Section:
        for item in self.sections:
            if item.number == number:
                return item
        raise AssertionError(f"docs/05 §6 里找不到小节 {number}（文档结构变了？）")

    @property
    def api_sections(self) -> tuple[Section, ...]:
        return tuple(item for item in self.sections if item.number in API_SECTIONS)


def normalize_path(path: str) -> str:
    """把路径里的参数名抹平：``/api/updates/{update_id}`` → ``/api/updates/{}``。"""
    return _PARAM_RE.sub("{}", path)


def load_operations() -> set[tuple[str, str]]:
    """读 ``web/openapi.json`` 的全部 operation（method + 归一化路径）。"""
    spec = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    operations: set[tuple[str, str]] = set()
    for path, item in spec["paths"].items():
        for method in item:
            if method.upper() in HTTP_METHODS:
                operations.add((method.upper(), normalize_path(path)))
    return operations


def parse_route_inventory() -> Inventory:
    """解析 docs/05 §6 的十八个小节、每节的表格行与计数声明，以及开头的总数句。"""
    lines = API_SPEC_PATH.read_text(encoding="utf-8").splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith("## 6. ")),
        None,
    )
    assert start is not None, "docs/05 里找不到 `## 6. 全量路由清单`"
    end = next(
        (i for i, line in enumerate(lines[start + 1 :], start + 1) if line.startswith("## 7. ")),
        len(lines),
    )

    total = ws_total = static_total = None
    total_line = None
    for i in range(start, end):
        text = lines[i]
        match = _TOTAL_RE.search(text)
        if match:
            total, total_line = int(match.group(1)), i + 1
        match = _WS_TOTAL_RE.search(text)
        if match:
            ws_total = int(match.group(1))
        match = _STATIC_TOTAL_RE.search(text)
        if match:
            static_total = int(match.group(1))
    assert total is not None, "docs/05 §6 开头没有 `共 N 条 HTTP 端点` 的总数声明"
    assert ws_total is not None, "docs/05 §6 开头的总数句里没有 WebSocket 条数"
    assert static_total is not None, "docs/05 §6 开头的总数句里没有静态与文档入口条数"

    sections: list[Section] = []
    current: dict[str, object] | None = None

    def flush() -> None:
        if current is None:
            return
        sections.append(
            Section(
                number=str(current["number"]),
                title=str(current["title"]),
                rows=tuple(current["rows"]),  # type: ignore[arg-type]
                table_rows=int(current["table_rows"]),  # type: ignore[arg-type]
                declared=current["declared"],  # type: ignore[arg-type]
                declared_line=current["declared_line"],  # type: ignore[arg-type]
                line=int(current["line"]),  # type: ignore[arg-type]
            )
        )

    #: 代码块里的 ``|`` 不是表格行（§6.9 的 JSON 示例就在块里），整块跳过。
    in_fence = False
    for i in range(start, end):
        line = lines[i]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _SECTION_RE.match(line)
        if match:
            flush()
            current = {
                "number": match.group(1),
                "title": match.group(2).strip(),
                "rows": [],
                "table_rows": 0,
                "declared": None,
                "declared_line": None,
                "line": i + 1,
            }
            continue
        if current is None:
            continue
        text = line.strip()
        match = _DECLARED_RE.match(text)
        if match:
            assert current["declared"] is None, (
                f"docs/05 §{current['number']} 有两条「（N 条）」声明（第 "
                f"{current['declared_line']} 行与第 {i + 1} 行）"
            )
            current["declared"] = int(match.group(1))
            current["declared_line"] = i + 1
            continue
        match = _ROW_RE.match(line)
        if not match:
            continue
        method = match.group("method").strip()
        cell = match.group("path").strip()
        if not method or set(method) <= set("-: ") or method == "方法":
            continue  # 分隔行与表头
        paths = [
            part.strip().strip("`").strip()
            for part in _PATH_SPLIT_RE.split(cell)
            if part.strip().strip("`").strip()
        ]
        if not paths:
            continue
        current["table_rows"] = int(current["table_rows"]) + 1
        rows = current["rows"]
        assert isinstance(rows, list)
        for path in paths:
            rows.append(RouteRow(method=method, path=path, line=i + 1))
    flush()

    return Inventory(
        sections=tuple(sections),
        total=total,
        total_line=total_line,
        ws_total=ws_total,
        static_total=static_total,
    )


def _format_diff(doc_only: list[tuple[str, RouteRow]], ops_only: list[tuple[str, str]]) -> str:
    """把两组差异渲染成可直接照做的修复清单。"""
    parts = ["docs/05 §6 与 web/openapi.json 的 operation 集合不一致。"]
    if doc_only:
        parts.append(f"\n文档有、实现没有（{len(doc_only)} 条，删掉或改成真实端点）：")
        parts.extend(
            f"  {row.method} {row.path}  ← 第 {row.line} 行（§{number}）"
            for number, row in doc_only
        )
    if ops_only:
        parts.append(f"\n实现有、文档没有（{len(ops_only)} 条，补进语义正确的小节）：")
        parts.extend(f"  {method} {path}" for method, path in ops_only)
    parts.append(
        "\n修好后同步改小节的「（N 条）」与 §6 开头的总数句；"
        "路径参数名不算差异（{id} 与 {update_id} 等价）。"
    )
    return "\n".join(parts)


# ----------------------------------------------------------------------
# 解析自身
# ----------------------------------------------------------------------


def test_inventory_parses_every_section() -> None:
    """解析本身要 fail loud：小节数、计数声明、非空表格缺一不可。"""
    inventory = parse_route_inventory()
    assert len(inventory.sections) == SECTION_COUNT, (
        f"docs/05 §6 应解析出 {SECTION_COUNT} 个小节，实际 {len(inventory.sections)} 个："
        f"{[item.number for item in inventory.sections]}"
    )
    for section in inventory.sections:
        assert section.declared is not None, f"{section.label} 缺少「（N 条）」声明"
        assert section.table_rows > 0, f"{section.label} 的表格是空的"
        assert len(section.rows) >= section.table_rows, f"{section.label} 的路径列解析异常"


# ----------------------------------------------------------------------
# 集合相等：API 小节 ↔ OpenAPI operation
# ----------------------------------------------------------------------


def test_api_sections_match_openapi_operations() -> None:
    """API 小节的（方法 + 路径）集合必须与 OpenAPI 的 operation 集合完全相等。"""
    inventory = parse_route_inventory()
    operations = load_operations()

    documented: dict[tuple[str, str], tuple[str, RouteRow]] = {}
    for section in inventory.api_sections:
        for row in section.rows:
            key = (row.method, normalize_path(row.path))
            assert key not in documented, (
                f"{key[0]} {key[1]} 在 {inventory.section(documented[key][0]).label}"
                f"（第 {documented[key][1].line} 行）与 {section.label}（第 {row.line} 行）"
                "各记了一次，清单里一条端点只能有一行"
            )
            documented[key] = (section.number, row)

    doc_only = sorted(
        ((number, row) for key, (number, row) in documented.items() if key not in operations),
        key=lambda item: (item[1].path, item[1].method),
    )
    ops_only = sorted(operations - set(documented), key=lambda item: (item[1], item[0]))

    assert not doc_only and not ops_only, _format_diff(doc_only, ops_only)


# ----------------------------------------------------------------------
# 行数、小节计数与总数
# ----------------------------------------------------------------------


def test_section_counts_match_table_rows() -> None:
    """每个小节的「（N 条）」等于该小节表格里的行数。"""
    inventory = parse_route_inventory()
    problems = [
        f"{section.label}：声明「（{section.declared} 条）」在第 {section.declared_line} 行，"
        f"表格实际 {section.table_rows} 行"
        for section in inventory.sections
        if section.declared != section.table_rows
    ]
    assert not problems, "docs/05 §6 的小节计数与表格行数不一致：\n" + "\n".join(problems)


def test_declared_total_matches_api_rows_and_openapi() -> None:
    """§6 开头的总数同时等于 API 小节行数之和与 OpenAPI 的 operation 数。"""
    inventory = parse_route_inventory()
    operations = load_operations()
    api_rows = sum(len(section.rows) for section in inventory.api_sections)
    assert inventory.total == api_rows == len(operations), (
        f"docs/05 第 {inventory.total_line} 行声明 {inventory.total} 条 HTTP 端点，"
        f"API 小节实际 {api_rows} 行，web/openapi.json 有 {len(operations)} 条 operation —— "
        "三处必须相等（总数句里还要带上 WebSocket 与静态入口的条数）"
    )


def test_total_sentence_counts_ws_and_static_sections() -> None:
    """总数句里的 WebSocket 与静态入口条数也要对上 §6.2 / §6.17。"""
    inventory = parse_route_inventory()
    ws = inventory.section(WS_SECTION)
    static = inventory.section(STATIC_SECTION)
    assert inventory.ws_total == ws.declared == ws.table_rows, (
        f"总数句声明 {inventory.ws_total} 条 WebSocket，§6.2 声明「（{ws.declared} 条）」、"
        f"表格 {ws.table_rows} 行"
    )
    assert inventory.static_total == static.declared == static.table_rows, (
        f"总数句声明 {inventory.static_total} 条静态与文档入口，§6.17 声明"
        f"「（{static.declared} 条）」、表格 {static.table_rows} 行"
    )


# ----------------------------------------------------------------------
# 非 API 小节：WebSocket 与静态入口
# ----------------------------------------------------------------------


def test_ws_section_holds_only_the_websocket_route() -> None:
    """§6.2 只有 WebSocket 升级一条，且它不出现在 OpenAPI 的 paths 里。"""
    inventory = parse_route_inventory()
    ws = inventory.section(WS_SECTION)
    rows = [(row.method, normalize_path(row.path)) for row in ws.rows]
    assert rows == [WS_ROUTE], f"{ws.label} 应只有 {WS_ROUTE[0]} {WS_ROUTE[1]}，实际 {rows}"
    assert WS_ROUTE not in load_operations(), (
        f"{WS_ROUTE[1]} 是 WebSocket 升级路由，不该出现在 web/openapi.json 里"
    )


def test_static_section_is_not_in_openapi() -> None:
    """§6.17 是静态与文档入口：一行可以并排多条路径，集合比对按表格行数算。"""
    inventory = parse_route_inventory()
    static = inventory.section(STATIC_SECTION)
    assert static.declared == static.table_rows, (
        f"{static.label} 声明「（{static.declared} 条）」，表格 {static.table_rows} 行"
    )
    operations = load_operations()
    for row in static.rows:
        assert row.method == "GET", f"{static.label} 第 {row.line} 行的方法应是 GET"
        assert (row.method, normalize_path(row.path)) not in operations, (
            f"{static.label} 的 {row.path}（第 {row.line} 行）是静态挂载或 SPA 回落，"
            "不该出现在 web/openapi.json 里"
        )


# ----------------------------------------------------------------------
# 表格里的方法大小写与路径格式
# ----------------------------------------------------------------------


def test_methods_and_paths_are_well_formed() -> None:
    """方法一律大写；路径一律以 ``/`` 开头、不带尾斜杠（与 ``redirect_slashes=False`` 一致）。"""
    inventory = parse_route_inventory()
    problems: list[str] = []
    for section in inventory.sections:
        for row in section.rows:
            if row.method not in DOC_METHODS:
                problems.append(f"第 {row.line} 行的方法 {row.method!r} 不是大写 HTTP 方法")
            if not row.path.startswith("/"):
                problems.append(f"第 {row.line} 行的路径 {row.path!r} 没有以 / 开头")
            elif row.path != "/" and row.path.endswith("/"):
                problems.append(f"第 {row.line} 行的路径 {row.path!r} 带了尾斜杠")
            if row.path != row.path.strip() or " " in row.path:
                problems.append(f"第 {row.line} 行的路径 {row.path!r} 含空白")
    assert not problems, "docs/05 §6 的表格格式有问题：\n" + "\n".join(problems)
