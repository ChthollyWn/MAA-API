#!/usr/bin/env python
"""抽取「用户可能看到的所有文本」，并做 AI 腔规则扫描。

用法：
    .venv/bin/python scripts/copy_inventory.py            # 生成清单与扫描结果
    .venv/bin/python scripts/copy_inventory.py --web-only # 只跑前端抽取

产物（默认写到 docs/copy-audit/）：
    inventory.json  机器可读的全量记录
    inventory.md    按表面分组的清单（含 file:line）
    ai-tells.md     规则命中汇总（改写前的靶子）

表面划分：
    frontend     web/src 里的界面文案（由 web/scripts/copy-inventory.mjs 抽取）
    api_error    后端抛给用户看的错误 message
    openapi      OpenAPI 的 summary / description / 字段说明（/docs 与调试台可见）
    notification 通知与 Web Push 的标题正文
    agent        Agent 系统提示词、工具描述、前端状态提示
    knowledge    内置知识库条目（进系统提示、设置页可读）
    cli          脚本输出
    internal     其余中文常量（可见性待判，多为内部/开发向）
    docs         README 与 docs/*.md 的标题骨架（正文不逐条列）

注释不是 AST 节点，因此不会混进上面任何一类；注释与文档正文单独做规则扫描。
"""

from __future__ import annotations

import argparse
import ast
import inspect
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CJK = re.compile(r"[\u2e80-\u303f\u3040-\u9fff\uf900-\ufaff\ufe30-\ufe4f\uff00-\uffef]")

# ---------------------------------------------------------------------------
# AI 腔规则表
# ---------------------------------------------------------------------------

#: (规则 id, 正则, 说明, 严重度)。high = 对外文案里几乎总是 AI 腔；
#: medium = 视语境；low = 只是提示，多数是正常写法。
AI_TELL_RULES: list[tuple[str, str, str, str]] = [
    ("em-dash", r"——", "破折号当冒号用", "high"),
    ("contrast", r"(?:而不是|而非|才是|才是真正)", "「A 而不是 B」对照腔", "high"),
    ("not-but", r"(?:不是|并非)[^，。；！？]{0,16}(?:而是|，是)", "「不是…而是…」对照句", "high"),
    ("not-only", r"不(?:仅|止)[^，。；！？]{0,16}(?:而且|更是|还)", "「不仅…而且…」递进句", "high"),
    ("worth-noting", r"(?:值得(?:一提|注意)的是|需要(?:注意|说明|指出)的是|应当指出)", "「值得注意的是」式过渡", "high"),
    ("summary-tone", r"(?:综上所述|总而言之|总的来说|一言以蔽之|一句话[,，])", "总结腔", "high"),
    ("first-then", r"首先[^。！？]{0,40}(?:其次|然后|接着)", "「首先…其次…」罗列腔", "high"),
    ("marketing", r"(?:旨在|致力于|赋能|助力|打造|一站式|全方位|保驾护航|最佳实践|极大地提升)", "营销/OKR 腔", "high"),
    ("hype", r"(?:轻松|丝滑|极致|无忧|便捷)", "宣传词", "medium"),
    ("lets", r"(?:让我们|咱们|一起来(?:看看|了解)|快来)", "口播腔", "high"),
    ("tipsy", r"(?:温馨提示|小贴士|友情提示)", "贴士腔", "high"),
    ("apology", r"(?:很抱歉|抱歉[，。！]|感谢您的|感谢您|请您|敬请)", "客服/道歉腔", "high"),
    ("absolutes", r"(?:一律|绝不|永远不|务必|切记|一定要|严禁|不得)", "绝对化规定腔", "medium"),
    ("self-ref", r"(?:本模块|本函数|本类|本文档|这里要|这里不|下面这|上面这)", "自指式讲解腔", "medium"),
    ("jike", r"即可", "「…即可」机器腔", "medium"),
    ("note-that", r"请注意|注意：", "提示腔", "medium"),
    ("bold", r"\*\*", "粗体强调", "medium"),
    ("emoji", r"[\U0001f300-\U0001faff\u2705\u274c\u26a0\ufe0f\u2757\u2753\u2b50]", "emoji/符号装饰", "high"),
    ("exclaim", r"！", "感叹号", "low"),
    ("ellipsis", r"……", "省略号", "medium"),
    ("english-ai", r"(?i)\b(?:note that|please note|in summary|let's|oops|something went wrong|please try again|try again later|seamless|effortless|comprehensive|dive into|unlock|empower|leverage)\b", "英文 AI 惯用语", "medium"),
    ("em-dash-en", r"[A-Za-z\u4e00-\u9fff]—[A-Za-z\u4e00-\u9fff]", "夹在词中间的破折号", "medium"),
]

#: 命中这些表面的算「用户可见」，其余算开发向。
VISIBLE_SURFACES = (
    "frontend/",
    "static-meta",
    "schema-ui",
    "api_error",
    "openapi",
    "notification",
    "agent",
    "knowledge",
    "log",
)

#: 扫描器自身不该进结果。
SKIP_FILES = ("scripts/copy_inventory.py", "web/scripts/copy-inventory.mjs")


@dataclass
class Record:
    surface: str
    file: str
    line: int
    kind: str
    context: str
    text: str
    user_visible: bool | None = True

    def as_dict(self) -> dict:
        return {
            "surface": self.surface,
            "file": self.file,
            "line": self.line,
            "kind": self.kind,
            "context": self.context,
            "text": self.text,
            "user_visible": self.user_visible,
        }


# ---------------------------------------------------------------------------
# 后端（Python AST）
# ---------------------------------------------------------------------------


def _node_text(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - 极端语法
        return ""


def _const_text(node: ast.AST) -> str | None:
    """把字符串常量（含 f-string）渲染成可读文本。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                out.append(part.value)
            elif isinstance(part, ast.FormattedValue):
                out.append("{" + _node_text(part.value) + "}")
        return "".join(out)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _const_text(node.left), _const_text(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _target_names(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return _node_text(node)


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return _node_text(node)


ROUTE_DECORATORS = {"get", "post", "put", "patch", "delete", "head", "options", "api_route"}
SCHEMA_CALLS = {"Field", "Query", "Path", "Body", "Header", "Form", "Cookie", "Depends"}
#: 后端 schema 里写给界面用的键：任务表单（MaaField）与设置页（dict(...)）。
SCHEMA_UI_KWARGS = {"label", "group", "description", "title", "unit", "enum_labels"}
SCHEMA_UI_CALLS = {"MaaField", "dict", "Field"}
#: 这些模块里的中文常量是给界面/日志页用的，不是内部黑话。
UI_CONST_FILES = ("domain/task.py", "services/settings_schema.py")
LOG_FILES = ("callback_translator.py",)
ERROR_CALLS = ("AppError", "HTTPException", "StarletteHTTPException", "ValueError", "TypeError", "KeyError", "RuntimeError")
NOTIFY_FILES = ("notify", "push", "webpush", "smtp")


def _is_docstring(node: ast.Constant, parents: tuple[ast.AST, ...]) -> tuple[bool, str]:
    if not parents:
        return False, ""
    expr = parents[-1]
    owner = parents[-2] if len(parents) >= 2 else None
    if not isinstance(expr, ast.Expr) or expr.value is not node:
        return False, ""
    if not isinstance(owner, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return False, ""
    if not owner.body or owner.body[0] is not expr:
        return False, ""
    if isinstance(owner, ast.Module):
        return True, "<module>"
    if isinstance(owner, ast.ClassDef):
        return True, owner.name
    return True, owner.name


def _enclosing_function(parents: tuple[ast.AST, ...]) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in reversed(parents):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    return None


#: Pydantic 模型类名后缀：这些类的 docstring 会成为 JSON Schema 的 description，
#: 经 /openapi.json 出现在 /docs、Redoc 与内置调试台里。
MODEL_BASE_SUFFIXES = (
    "BaseModel", "Base", "Input", "Output", "Response", "Request", "Payload", "Model", "Schema",
)


def _enclosing_class(parents: tuple[ast.AST, ...]) -> ast.ClassDef | None:
    for node in reversed(parents):
        if isinstance(node, ast.ClassDef):
            return node
    return None


def _is_pydantic_model(node: ast.ClassDef | None) -> bool:
    if node is None:
        return False
    for base in node.bases:
        name = _call_name(base)
        if name.split(".")[-1].endswith(MODEL_BASE_SUFFIXES):
            return True
    return False


def _owning_assignment_name(parents: tuple[ast.AST, ...]) -> str:
    for node in reversed(parents):
        if isinstance(node, ast.Assign):
            return ",".join(_target_names(t) for t in node.targets)
        if isinstance(node, ast.AnnAssign):
            return _target_names(node.target)
    return ""


#: 这些模块级常量直接进 OpenAPI 文档头，用户在 /docs 与调试台看到。
OPENAPI_CONSTS = {"TITLE", "DESCRIPTION", "TAGS"}
#: 启动时打印的固定文案，用户在服务日志里看到。
LOG_CONSTS = {"AUTH_DISABLED_WARNING"}


def _is_route(func: ast.FunctionDef | ast.AsyncFunctionDef | None) -> bool:
    if func is None:
        return False
    for decorator in func.decorator_list:
        if isinstance(decorator, ast.Call):
            name = _call_name(decorator.func)
            if name.split(".")[-1] in ROUTE_DECORATORS:
                return True
        elif isinstance(decorator, ast.Attribute) and decorator.attr in ROUTE_DECORATORS:
            return True
    return False


def extract_python(path: Path, repo_rel: str) -> list[Record]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:  # pragma: no cover
        print(f"跳过（语法错误）{repo_rel}: {exc}", file=sys.stderr)
        return []

    records: list[Record] = []

    def emit(node: ast.AST, kind: str, context: str, text: str, surface: str, visible: bool | None = True):
        value = text.strip()
        if not value:
            return
        records.append(
            Record(
                surface=surface,
                file=repo_rel,
                line=getattr(node, "lineno", 0),
                kind=kind,
                context=context,
                text=value,
                user_visible=visible,
            )
        )

    def walk(node: ast.AST, parents: tuple[ast.AST, ...] = ()):
        # 先处理字符串常量 / f-string，再递归（避免重复收拼接出来的碎片）。
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            handle_str(node, parents)
        elif isinstance(node, ast.JoinedStr):
            handle_str(node, parents)
            return
        elif isinstance(node, ast.BinOp) and _const_text(node) is not None:
            handle_str(node, parents)
            return
        for child in ast.iter_child_nodes(node):
            walk(child, parents + (node,))

    def handle_str(node: ast.AST, parents: tuple[ast.AST, ...]):
        text = _const_text(node)
        if text is None:
            return
        has_cjk = bool(CJK.search(text))

        parent = parents[-1] if parents else None
        grand = parents[-2] if len(parents) >= 2 else None

        is_doc, scope = _is_docstring(node, parents)  # type: ignore[arg-type]

        # 文档字符串。FastAPI 用 inspect.cleandoc 处理缩进，这里保持一致，
        # 否则多行 docstring 与 /openapi.json 里的实际文本对不上。
        if is_doc:
            if not has_cjk:
                return
            text = inspect.cleandoc(text)
            func = _enclosing_function(parents)
            klass = _enclosing_class(parents)
            if func is not None and _is_route(func):
                emit(node, "docstring", f"{scope}()", text, "openapi", True)
            elif klass is not None and _is_pydantic_model(klass):
                # 模型 docstring 就是 JSON Schema 的 description，API 调用者会看到。
                emit(node, "model-docstring", f"{scope}", text, "openapi", True)
            else:
                emit(node, "docstring", f"{scope}()", text, "docstring", False)
            return

        # 关键字参数
        if isinstance(parent, ast.keyword) and parent.arg:
            key = parent.arg
            call = grand if isinstance(grand, ast.Call) else None
            call_name = _call_name(call.func) if call is not None else ""
            if key in {"summary", "description", "response_description"}:
                if call_name.split(".")[-1] in ROUTE_DECORATORS or call_name in SCHEMA_CALLS:
                    emit(node, "openapi", f"{call_name}({key}=)", text, "openapi", True)
                    return
            if key in {"description", "title"} and call_name in SCHEMA_CALLS:
                emit(node, "openapi", f"{call_name}({key}=)", text, "openapi", True)
                return
            if key in {"title", "body", "message", "content"} and any(
                token in repo_rel for token in NOTIFY_FILES
            ):
                emit(node, "notify", f"{call_name}({key}=)", text, "notification", True)
                return
            if key in {"detail", "reason"} and call_name.endswith(("HTTPException",)):
                emit(node, "error-message", f"{call_name}({key}=)", text, "api_error", True)
                return
            if (
                key in SCHEMA_UI_KWARGS
                and call_name.split(".")[-1] in SCHEMA_UI_CALLS
            ):
                emit(node, "schema-label", f"{call_name}({key}=)", text, "schema-ui", True)
                return

        # 关键字参数里的字典值（enum_labels={"CN": "国服"}）
        if isinstance(parent, ast.Dict) and isinstance(grand, ast.keyword):
            if grand.arg in {"enum_labels", "enum_descriptions"}:
                emit(node, "schema-label", f"{grand.arg}[]", text, "schema-ui", True)
                return

        # 位置参数
        if isinstance(parent, ast.Call) and node in parent.args:
            call_name = _call_name(parent.func)
            short = call_name.split(".")[-1]
            index = parent.args.index(node)
            if short in {"AppError", "HTTPException", "StarletteHTTPException"} and index >= 1:
                emit(node, "error-message", f"{short}()", text, "api_error", True)
                return
            if isinstance(grand, ast.Raise):
                # 非领域异常：消息不会进响应体，只在日志/堆栈里，属开发者可见。
                surface = "api_error" if short in {"AppError", "HTTPException"} else "internal"
                emit(node, "raise", f"raise {short}()", text, surface, surface == "api_error")
                return
            if call_name.startswith(("logger.", "log.")):
                if has_cjk:
                    emit(node, "log", f"{call_name}()", text, "log", True)
                return
            if short == "print" and repo_rel.startswith("scripts/") and has_cjk:
                emit(node, "cli", "print()", text, "cli", True)
                return
            if not has_cjk:
                return
            emit(node, "arg", f"{short}()", text, "internal", False)
            return

        if not has_cjk:
            return

        owner_const = _owning_assignment_name(parents)
        if owner_const in OPENAPI_CONSTS and repo_rel.endswith("main.py"):
            emit(node, "openapi-meta", owner_const, text, "openapi", True)
            return
        if owner_const in LOG_CONSTS and repo_rel.endswith("main.py"):
            emit(node, "log", owner_const, text, "log", True)
            return

        # 变量赋值
        if isinstance(parent, ast.Assign) or (isinstance(parent, ast.AnnAssign) and parent.value is node):
            targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
            names = ", ".join(_target_names(t) for t in targets)
            surface = "internal"
            visible: bool | None = False
            if repo_rel.startswith("maa_api/agent/"):
                surface, visible = "agent", True
            elif repo_rel.startswith("scripts/"):
                surface, visible = "cli", True
            elif repo_rel.startswith("tests/") or "tests/" in repo_rel:
                surface, visible = "test-copy", False
            elif any(token in repo_rel for token in LOG_FILES):
                surface, visible = "log", True
            elif any(repo_rel.endswith(token) for token in UI_CONST_FILES) and "_LABELS" in names:
                surface, visible = "schema-ui", True
            emit(node, "const", names, text, surface, visible)
            return

        # 其余位置的中文常量
        surface = "internal"
        visible = False
        if repo_rel.startswith("maa_api/agent/"):
            surface, visible = "agent", True
        elif repo_rel.startswith("scripts/"):
            surface, visible = "cli", True
        elif repo_rel.startswith("tests/"):
            surface, visible = "test-copy", False
        elif any(token in repo_rel for token in LOG_FILES):
            surface, visible = "log", True
        elif any(token in repo_rel for token in NOTIFY_FILES):
            surface, visible = "notification", True
        emit(node, "literal", "", text, surface, visible)

    walk(tree)
    return records


# ---------------------------------------------------------------------------
# 前端 / 知识库 / 文档
# ---------------------------------------------------------------------------


def extract_frontend() -> tuple[list[Record], int]:
    script = REPO_ROOT / "web" / "scripts" / "copy-inventory.mjs"
    out = REPO_ROOT / ".copy-inventory-web.json"
    subprocess.run(
        ["node", str(script), "--out", str(out)],
        cwd=REPO_ROOT / "web",
        check=True,
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    out.unlink(missing_ok=True)
    records = [
        Record(
            surface=f"frontend/{item['surface']}",
            file=item["file"],
            line=item["line"],
            kind=f"{item['position']}:{item['context']}" if item["context"] else item["position"],
            context=item["context"],
            text=item["text"],
            user_visible=True,
        )
        for item in payload["strings"]
    ]
    return records, payload["file_count"]


def extract_knowledge() -> list[Record]:
    records = []
    root = REPO_ROOT / "maa_api" / "agent" / "knowledge" / "builtin"
    for path in sorted(root.glob("*.md")):
        rel = str(path.relative_to(REPO_ROOT))
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("|"):
                text = stripped.strip("|").replace(" | ", " · ").strip()
            elif stripped.startswith(("#", "-", "*", ">", "+")) or re.match(r"^\d+[.)]\s", stripped):
                # 只剥 Markdown 的结构前缀，保留 ** 等强调标记（它们是正文的一部分）。
                text = re.sub(r"^(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*)", "", stripped).strip()
            else:
                continue
            if text:
                records.append(Record("knowledge", rel, index, "md-line", "", text, True))
    return records


def extract_docs() -> tuple[list[Record], list[Record]]:
    """文档标题进清单；正文不进清单，只进规则扫描（11985 行，逐行统计）。"""
    headings: list[Record] = []
    body: list[Record] = []
    paths = sorted(REPO_ROOT.glob("*.md")) + sorted((REPO_ROOT / "docs").rglob("*.md"))
    for path in paths:
        rel = str(path.relative_to(REPO_ROOT))
        if rel.startswith("docs/copy-audit/"):
            continue
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            if line.startswith("#"):
                text = line.lstrip("#").strip()
                if text:
                    headings.append(Record("docs-heading", rel, index, "heading", "", text, True))
                continue
            if not CJK.search(stripped):
                continue
            body.append(
                Record(
                    "docs-body",
                    rel,
                    index,
                    "md-line",
                    "",
                    re.sub(r"^\s*(?:[-*+>]\s+|\d+[.)]\s+)", "", stripped),
                    False,
                )
            )
    return headings, body


COMMENT_PATTERNS = [
    (re.compile(r"^\s*#\s?(.*)$"), ".py"),
    (re.compile(r"^\s*//\s?(.*)$"), ".ts"),
    (re.compile(r"^\s*\*\s?(.*)$"), ".ts-doc"),
]


def extract_comments() -> list[Record]:
    """源码注释：不进清单，只用来做规则扫描（维护者能看到的文本）。"""
    records = []
    exts = {".py", ".ts", ".tsx", ".mjs", ".sh", ".yaml", ".yml", ".json", ".toml", ".html", ".css", ".ini", ".md"}
    roots = [REPO_ROOT / "maa_api", REPO_ROOT / "scripts", REPO_ROOT / "web" / "src", REPO_ROOT / "web" / "scripts", REPO_ROOT / "tests"]
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix not in exts:
                continue
            if any(part in {"node_modules", "__pycache__", ".venv"} for part in path.parts):
                continue
            rel = str(path.relative_to(REPO_ROOT))
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for index, line in enumerate(lines, start=1):
                for pattern, _kind in COMMENT_PATTERNS:
                    match = pattern.match(line)
                    if match and match.group(1).strip():
                        records.append(
                            Record("comment", rel, index, "comment", "", match.group(1).strip(), False)
                        )
                        break
    return records


# ---------------------------------------------------------------------------
# 规则扫描
# ---------------------------------------------------------------------------


def scan_ai_tells(records: list[Record]) -> dict[str, list[dict]]:
    hits: dict[str, list[dict]] = defaultdict(list)
    compiled = [(rid, re.compile(pattern), label, severity) for rid, pattern, label, severity in AI_TELL_RULES]
    for record in records:
        if record.file in SKIP_FILES:
            continue
        for rid, pattern, _label, _severity in compiled:
            if pattern.search(record.text):
                hits[rid].append(
                    {
                        "surface": record.surface,
                        "file": record.file,
                        "line": record.line,
                        "text": record.text,
                        "visible": record.surface.startswith(VISIBLE_SURFACES),
                    }
                )
    return dict(hits)


GROUPS: list[tuple[str, str, tuple[str, ...]]] = [
    ("清单-1-界面.md", "界面文案（前端源码 + 后端 schema 里的标签）", ("frontend/", "static-meta", "schema-ui")),
    ("清单-2-对外消息.md", "对外消息（错误 / 日志 / OpenAPI / 通知）", ("api_error", "log", "openapi", "notification")),
    ("清单-3-Agent与知识库.md", "Agent 与知识库", ("agent", "knowledge")),
    ("清单-4-脚本与文档.md", "脚本输出与文档标题", ("cli", "docs-heading")),
    ("清单-5-开发向文案.md", "开发向（常量、内部异常、docstring）", ("internal", "docstring")),
]


def extract_html_templates() -> list[Record]:
    """邮件模板里的可见文字（用户在邮件里读到）。"""
    import html as html_module
    import re as _re

    records = []
    for path in sorted((REPO_ROOT / "maa_api" / "templates").glob("*.html")):
        rel = str(path.relative_to(REPO_ROOT))
        raw = path.read_text(encoding="utf-8")
        raw = _re.sub(r"<style.*?</style>", " ", raw, flags=_re.S)
        raw = _re.sub(r"<script.*?</script>", " ", raw, flags=_re.S)
        for match in _re.finditer(r">([^<>]+)<", raw):
            text = html_module.unescape(match.group(1)).strip()
            if not text or not CJK.search(text):
                continue
            line = raw[: match.start()].count("\n") + 1
            records.append(Record("notification", rel, line, "html-text", "", text, True))
    return records


def truncate(text: str, limit: int = 160) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    if len(flat) <= limit:
        return flat
    return flat[:limit] + f"…（共 {len(flat)} 字）"


def _render_group(records: list[Record], title: str) -> list[str]:
    by_surface: dict[str, list[Record]] = defaultdict(list)
    for record in records:
        by_surface[record.surface].append(record)
    lines = [f"# {title}", "", f"共 {len(records)} 条。", ""]
    for surface in sorted(by_surface, key=lambda s: (-len(by_surface[s]), s)):
        group = sorted(by_surface[surface], key=lambda r: (r.file, r.line))
        lines.append(f"## {surface}（{len(group)}）")
        lines.append("")
        current_file = None
        for record in group:
            if record.file != current_file:
                current_file = record.file
                lines.append("")
                lines.append(f"**{current_file}**")
                lines.append("")
            lines.append(f"- `{record.file}:{record.line}` [{record.kind}] {truncate(record.text)}")
        lines.append("")
    return lines


def write_markdown(records: list[Record], root: Path) -> dict[str, int]:
    written: dict[str, int] = {}
    for filename, title, prefixes in GROUPS:
        selected = [
            record
            for record in records
            if any(record.surface.startswith(prefix) for prefix in prefixes)
        ]
        written[filename] = len(selected)
        (root / filename).write_text("\n".join(_render_group(selected, title)) + "\n", encoding="utf-8")
    return written


def write_ai_tells(all_records: list[Record], root: Path, title: str, filename: str, sample: int = 8) -> None:
    hits = scan_ai_tells(all_records)
    rules = {rid: (label, severity) for rid, _p, label, severity in AI_TELL_RULES}
    lines = [
        f"# {title}",
        "",
        f"总计 {len(all_records)} 条文本。命中按规则汇总，「用户可见」只统计界面/对外文案，",
        "「开发向」是注释、docstring、内部常量（维护者看得到，终端用户看不到）。",
        "",
    ]
    overview = sorted(hits.items(), key=lambda kv: -len(kv[1]))
    lines += ["| 规则 | 严重度 | 用户可见 | 开发向 | 合计 |", "|---|---|---|---|---|"]
    for rid, items in overview:
        label, severity = rules[rid]
        visible = sum(1 for item in items if item["visible"])
        lines.append(f"| {label} | {severity} | {visible} | {len(items) - visible} | {len(items)} |")
    lines.append("")
    for rid, items in overview:
        label, severity = rules[rid]
        visible_items = [item for item in items if item["visible"]]
        dev_items = [item for item in items if not item["visible"]]
        lines.append(f"## {label}（{severity}｜用户可见 {len(visible_items)} / 开发向 {len(dev_items)}）")
        lines.append("")
        for caption, bucket in (("用户可见", visible_items), ("开发向", dev_items)):
            if not bucket:
                continue
            lines.append(f"**{caption}**")
            lines.append("")
            seen: set[str] = set()
            shown = 0
            for item in bucket:
                key = item["text"][:60]
                if key in seen:
                    continue
                seen.add(key)
                lines.append(f"- `{item['file']}:{item['line']}` {truncate(item['text'], 120)}")
                shown += 1
                if shown >= sample:
                    break
            if len(bucket) > shown:
                lines.append(f"- …… 另有 {len(bucket) - shown} 条，见 inventory.json")
            lines.append("")
    (root / filename).write_text("\n".join(lines) + "\n", encoding="utf-8")


def ratchet_counts(
    records: list[Record], docs_body: list[Record], comments: list[Record]
) -> dict[str, int]:
    """按「组 × 规则」统计命中，用作防回潮的棘轮基线。"""
    groups = {
        "用户可见": [r for r in records if r.surface.startswith(VISIBLE_SURFACES)],
        "开发向": [
            r for r in records if not r.surface.startswith(VISIBLE_SURFACES)
        ],
        "docs正文": docs_body,
        "注释": comments,
    }
    counts: dict[str, int] = {}
    for name, bucket in groups.items():
        for rule_id, items in scan_ai_tells(bucket).items():
            counts[f"{name}/{rule_id}"] = len(items)
    return counts


def run_ratchet(
    records: list[Record],
    docs_body: list[Record],
    comments: list[Record],
    out_dir: Path,
    *,
    write: bool,
) -> int:
    """防回潮闸门：命中数只许降不许升（`--write-baseline` 重设基线）。"""
    baseline_path = out_dir / "lint-baseline.json"
    current = ratchet_counts(records, docs_body, comments)
    if write or not baseline_path.exists():
        baseline_path.write_text(
            json.dumps(current, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"基线已写入 {baseline_path}（{len(current)} 项）", file=sys.stderr)
        if write:
            return 0
        return 0
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    regressions = [
        (key, baseline[key], current.get(key, 0))
        for key in sorted(baseline)
        if current.get(key, 0) > baseline[key]
    ]
    improved = sum(1 for key in baseline if current.get(key, 0) < baseline[key])
    if regressions:
        print("文案 AI 腔回潮：", file=sys.stderr)
        for key, was, now in regressions:
            print(f"  {key}: {was} → {now}", file=sys.stderr)
        print("按规范改写，或确认无误后用 --write-baseline 重设基线。", file=sys.stderr)
        return 1
    print(f"通过：无回潮，{improved} 项较基线下降", file=sys.stderr)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="docs/copy-audit")
    parser.add_argument("--web-only", action="store_true")
    parser.add_argument("--check", action="store_true", help="命中数比基线上升就失败")
    parser.add_argument("--write-baseline", action="store_true", help="把当前命中数写为新基线")
    args = parser.parse_args()

    out_dir = REPO_ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    frontend, file_count = extract_frontend()
    print(f"前端：{len(frontend)} 条 / {file_count} 个文件", file=sys.stderr)
    if args.web_only:
        return

    backend: list[Record] = []
    for path in sorted((REPO_ROOT / "maa_api").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        backend += extract_python(path, str(path.relative_to(REPO_ROOT)))
    for path in sorted((REPO_ROOT / "scripts").glob("*.py")):
        backend += extract_python(path, str(path.relative_to(REPO_ROOT)))
    tests: list[Record] = []
    for path in sorted((REPO_ROOT / "tests").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tests += extract_python(path, str(path.relative_to(REPO_ROOT)))
    print(f"后端：{len(backend)} 条；测试里的中文断言：{len(tests)} 条", file=sys.stderr)

    knowledge = extract_knowledge()
    docs, docs_body = extract_docs()
    comments = extract_comments()
    templates = extract_html_templates()
    print(
        f"知识库：{len(knowledge)} 条；文档标题：{len(docs)} 条；文档正文行：{len(docs_body)} 条；注释：{len(comments)} 条",
        file=sys.stderr,
    )

    records = frontend + backend + knowledge + docs + templates
    (out_dir / "inventory.json").write_text(
        json.dumps(
            {
                "counts": {
                    "frontend": len(frontend),
                    "backend": len(backend),
                    "knowledge": len(knowledge),
                    "docs_headings": len(docs),
                    "comments": len(comments),
                    "test_copy": len(tests),
                },
                "records": [r.as_dict() for r in records],
                "test_copy": [r.as_dict() for r in tests],
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    written = write_markdown(records, out_dir)
    write_ai_tells(records, out_dir, "AI 腔规则命中：用户可见文案", "ai-tells.md")
    write_ai_tells(comments, out_dir, "AI 腔规则命中：源码注释（维护者可见）", "ai-tells-comments.md", sample=6)
    write_ai_tells(docs_body, out_dir, "AI 腔规则命中：docs 与 README 正文（逐行统计）", "ai-tells-docs.md", sample=6)
    write_ai_tells(tests, out_dir, "AI 腔规则命中：测试内的中文断言（改文案的连带影响面）", "ai-tells-tests.md", sample=6)
    for filename, count in written.items():
        print(f"→ {out_dir}/{filename}（{count} 条）", file=sys.stderr)

    counts = Counter(r.surface for r in records)
    for surface, count in counts.most_common():
        print(f"  {count:5d}  {surface}", file=sys.stderr)

    if args.check or args.write_baseline:
        raise SystemExit(run_ratchet(records, docs_body, comments, out_dir, write=args.write_baseline))


if __name__ == "__main__":
    main()
