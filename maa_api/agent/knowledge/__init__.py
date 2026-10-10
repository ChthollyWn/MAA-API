"""Agent 知识库：内置 Markdown + 用户覆盖的合并与注入（规格 §4.1）。

三件事，其余都别往这里塞：

- **内置条目**随代码发布，放在 ``builtin/*.md``，frontmatter 用 ruamel.yaml 解析
  （与 ``config.yaml`` / ``settings.py`` 同一套依赖）。启动时加载一次，
  文件缺失、id 重复或字段不合法都 fail loud：静默跳过会让 Agent 少一条剧本
  而没人发现。
- **覆盖语义**：``agent_knowledge`` 表里 ``id`` 与内置相同的行是覆盖行，只存与
  内置**不同**的列，其余为 NULL，读取时继承内置值；``kind`` 不可覆盖。写入时若
  没有任何字段与内置不同且处于启用状态，就删掉这一行（「恢复默认」）。
- **注入**：:meth:`KnowledgeService.render_context` 返回拼进 system 消息的那段
  文本：索引（playbook / screen / rule，先按 kind 再按 id 排序）+ 术语表全文 +
  用户档案全文。

写入只做**字段校验**（格式与长度在请求模型里拦成 422，这里的同名校验是兜底）与
内置 ``kind`` 的覆盖保护（409 ``KNOWLEDGE_BUILTIN_READONLY``）。条目数与全文段
字数**不设上限**（注入多少知识以模型自身的上下文窗口为准）。

信任边界（规格 §4.1「信任边界」）：知识库内容由持有访问令牌的人编写，当作操作
指引；截图、日志和其他工具结果仍是不可信数据。Agent 没有写知识库的通道。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from importlib import resources
from importlib.resources.abc import Traversable
from typing import Protocol

from pydantic import BaseModel
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.db import session as db_session
from maa_api.db.models import AgentKnowledge, utcnow
from maa_api.db.repositories.agent_knowledge import AgentKnowledgeRepository
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.activity_index import ActivitySnapshot, default_activity_index
from maa_api.services.game.activities import (
    ActivityReport,
    CurrentActivity,
    report_from_snapshot,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BUILTIN_PACKAGE",
    "ENTRY_ID_PATTERN",
    "FULLTEXT_KINDS",
    "INDEX_KINDS",
    "INDEX_HEADER",
    "ACTIVITY_HEADER",
    "ACTIVITY_HEADER_FALLBACK",
    "ACTIVITY_HEADER_FOREIGN",
    "ACTIVITY_HEADER_PRTS",
    "ACTIVITY_INJECTION_BUDGET_SECONDS",
    "MAX_ACTIVITY_LINES",
    "render_activity_lines",
    "render_activity_section",
    "GLOSSARY_HEADER",
    "PROFILE_HEADER",
    "KNOWLEDGE_KINDS",
    "MAX_BODY_CHARS",
    "MAX_TITLE_CHARS",
    "MAX_WHEN_TO_USE_CHARS",
    "ActivityReader",
    "BuiltinEntry",
    "KnowledgeBuiltinError",
    "KnowledgeBuiltinView",
    "KnowledgeDetail",
    "KnowledgeEntry",
    "KnowledgeService",
    "KnowledgeSummary",
    "KnowledgeWrite",
    "builtin_entries",
    "load_builtins",
    "render_activity_line",
]

#: 内置条目所在包与子目录（``maa_api/agent/knowledge/builtin/*.md``）。
BUILTIN_PACKAGE = "maa_api.agent.knowledge"
BUILTIN_DIRNAME = "builtin"

#: kind 的取值域与展示顺序（索引顺序即此前三项）。
KNOWLEDGE_KINDS = ("playbook", "screen", "rule", "glossary", "profile")
INDEX_KINDS = ("playbook", "screen", "rule")
FULLTEXT_KINDS = ("glossary", "profile")

#: ``id`` 是 URL 路径段，只允许小写字母、数字与中划线，2–48 个字符。
ENTRY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")

#: 单条字段的长度上限：列宽与表单的约束，不是上下文预算。
MAX_TITLE_CHARS = 32
MAX_WHEN_TO_USE_CHARS = 120
MAX_BODY_CHARS = 20_000

#: 注入段的三行标题，照抄规格 §4.1 的示例；第四行是 M24 加的当期活动段。
INDEX_HEADER = (
    "知识库索引（开发者与用户维护，可作为操作指引；需要时用 read_knowledge 读取全文）："
)
#: M24：当期活动段。它排在索引之后、术语表之前。
#: M25 起国服走 PRTS（多个在开活动各一行），取不到时退回 M24 的那一行。
ACTIVITY_HEADER = "当期活动（来自本机活动资源，随资源更新自动变化）："
#: M25：PRTS 可用时的标题，`{time}` 是抓取时刻（本机时区，HH:MM）。规格 §5.9。
ACTIVITY_HEADER_PRTS = "当期活动（PRTS，{time} 抓取）："
#: M25：PRTS 不可用时的降级标题（正文沿用 M24 的那一行）。
ACTIVITY_HEADER_FALLBACK = "当期活动（本机资源包推断，PRTS 暂不可用）："
#: M25：外服的降级标题（规格 §4.3）。
ACTIVITY_HEADER_FOREIGN = "当期活动（本机资源包推断；外服进度与国服不同）："
#: 每轮注入的最多活动行数（Q13、规格 §4.5）。
MAX_ACTIVITY_LINES = 5
#: 注入取数的预算：超时就用缓存旧值或本地兜底，不能拖慢开场（S29、规格 §4.5）。
ACTIVITY_INJECTION_BUDGET_SECONDS = 3.0
#: 当期活动那一行（不含标题）的长度上限。规格定的是「每轮成本 ≤ 120 字」，所以正常
#: 活动名（真数据 101 字）不会触发裁剪；活动名由上游决定、可能很长，超限时按这个数
#: 裁剪才不会让每轮的固定成本失控（规格 §3.1、§4.3）。
MAX_ACTIVITY_LINE_CHARS = 120
GLOSSARY_HEADER = "术语表（开发者与用户维护）："
PROFILE_HEADER = "用户档案（用户维护）："

#: 段落为空时的占位（规格只对档案写了「暂无」，索引与术语表同样处理）。
EMPTY_SECTION = "暂无"

_FRONTMATTER_FIELDS = frozenset({"id", "kind", "title", "when_to_use"})
_FRONTMATTER = re.compile(
    r"\A---[ \t]*\r?\n(?P<meta>.*?)\r?\n---[ \t]*(?:\r?\n(?P<body>.*))?\Z",
    re.DOTALL,
)
_KIND_ORDER = {kind: index for index, kind in enumerate(KNOWLEDGE_KINDS)}


class KnowledgeBuiltinError(RuntimeError):
    """内置知识文件不合法：启动时 fail loud，绝不静默跳过。"""


class ActivityReader(Protocol):
    """注入段需要的活动数据源（结构上就是 :class:`ActivityIndex`）。"""

    def snapshot(self) -> ActivitySnapshot: ...


def render_activity_line(snapshot: ActivitySnapshot) -> str:
    """把活动快照折成一行（规格 §4.3）。

    只放活动名、开始日期、代号区间与关数、以及「同名代号可能是别的期次」这句消歧提醒。
    推不出代号时如实写「未知」，并说明可以用 ``get_activity_stages`` 带关卡代号去查；
    这句话是模型知道「自己不知道」的唯一来源。

    整行按 :data:`MAX_ACTIVITY_LINE_CHARS` 收尾：活动名由上游决定、可能很长，超预算时
    先裁活动名再退掉代号区间，保证每轮多花的那点上下文有上界。
    """
    started = f"，{snapshot.started_at.isoformat()} 开始" if snapshot.started_at else ""
    code_range = snapshot.code_range
    if snapshot.stage_id_prefix and code_range:
        stages = f"；关卡代号 {code_range[0]}…{code_range[1]}（共 {len(snapshot.stages)} 关）"
    elif snapshot.stage_id_prefix:
        stages = f"；关卡代号前缀 {snapshot.stage_id_prefix}（共 {len(snapshot.stages)} 关）"
    else:
        stages = "；关卡代号未知，问到具体关卡时用 get_activity_stages 带代号查"
    tail = "；同名代号可能属于别的期次，提交前用 get_activity_stages 核对 stageId。"

    name = snapshot.name
    fixed = len(started) + len(stages) + len(tail)
    budget = MAX_ACTIVITY_LINE_CHARS - fixed
    if budget < len(name):
        # 预算不够就先退掉代号区间（信息量最小的一段），再裁活动名。
        if code_range or snapshot.stage_id_prefix:
            stages = "；关卡代号未知，问到具体关卡时用 get_activity_stages 带代号查"
            fixed = len(started) + len(stages) + len(tail)
            budget = MAX_ACTIVITY_LINE_CHARS - fixed
        if budget < len(name):
            name = name[: max(budget - 1, 1)] + "…"
    return f"{name}{started}{stages}{tail}"


def render_activity_lines(report: ActivityReport) -> list[str]:
    """PRTS 报告 → 每个在开活动一行（§5.9）。

    行格式：``- 名称｜至 MM-DD HH:MM｜VEC-01…VEC-12（12 关）``。超长时先省略代号区间，
    再截活动名——与 M24 的裁剪顺序一致，保证每行仍是 :data:`MAX_ACTIVITY_LINE_CHARS` 以内。
    """
    lines: list[str] = []
    for activity in report.activities[:MAX_ACTIVITY_LINES]:
        lines.append(_activity_row(activity))
    return lines


def _activity_row(activity: CurrentActivity) -> str:
    """一个活动一行。"""
    name = activity.name
    tail = _row_tail(activity)
    budget = MAX_ACTIVITY_LINE_CHARS - len(tail)
    if len(name) > budget:
        # 先省掉代号区间，再截活动名。
        if activity.code_range:
            tail = _row_tail(activity, with_range=False)
            budget = MAX_ACTIVITY_LINE_CHARS - len(tail)
        if len(name) > budget:
            name = name[: max(budget - 1, 1)] + "…"
    return f"- {name}{tail}"


def _row_tail(activity: CurrentActivity, *, with_range: bool = True) -> str:
    end = activity.end_at.astimezone().strftime("%m-%d %H:%M") if activity.end_at else "时间未知"
    parts = [f"｜至 {end}"]
    code_range = activity.code_range
    if with_range and code_range:
        parts.append(
            f"｜{code_range['first']}…{code_range['last']}（{code_range['count']} 关）"
        )
    elif not code_range:
        parts.append("｜代号未知，用 get_activity_stages 查")
    return "".join(parts)


def render_activity_section(report: ActivityReport | None) -> list[str]:
    """注入段的两行：标题 + 正文行（取不到时返回空列表，整段不插）。

    - PRTS 可用（任一条 ``source=prts``）：标题带抓取时刻，每个在开活动一行；
    - 降级（``source=resource``）：标题说明来路，正文沿用 M24 的那一行。
    """
    if report is None or not report.activities:
        return []
    first = report.activities[0]
    if first.source == "resource":
        header = (
            ACTIVITY_HEADER_FOREIGN if report.region_note else ACTIVITY_HEADER_FALLBACK
        )
        return [header, _fallback_line(first)]
    fetched = next(
        (source.fetched_at for source in report.sources if source.fetched_at), None
    )
    stamp = fetched.astimezone().strftime("%H:%M") if fetched else "刚刚"
    return [
        ACTIVITY_HEADER_PRTS.format(time=stamp),
        *render_activity_lines(report),
    ]


def _fallback_line(activity: CurrentActivity) -> str:
    """降级时沿用 M24 的那一行（活动名、开始日期、代号区间、消歧提醒）。"""
    started = (
        f"，{activity.start_at.astimezone().date().isoformat()} 开始"
        if activity.start_at
        else ""
    )
    code_range = activity.code_range
    if activity.stage_id_prefix and code_range:
        stages = (
            f"；关卡代号 {code_range['first']}…{code_range['last']}"
            f"（共 {len(activity.stages)} 关）"
        )
    elif activity.stage_id_prefix:
        stages = f"；关卡代号前缀 {activity.stage_id_prefix}（共 {len(activity.stages)} 关）"
    else:
        stages = "；关卡代号未知，问到具体关卡时用 get_activity_stages 带代号查"
    tail = "；同名代号可能属于别的期次，提交前用 get_activity_stages 核对 stageId。"
    name = activity.name
    fixed = len(started) + len(stages) + len(tail)
    budget = MAX_ACTIVITY_LINE_CHARS - fixed
    if budget < len(name):
        if code_range or activity.stage_id_prefix:
            stages = "；关卡代号未知，问到具体关卡时用 get_activity_stages 带代号查"
            budget = MAX_ACTIVITY_LINE_CHARS - len(started) - len(stages) - len(tail)
        if budget < len(name):
            name = name[: max(budget - 1, 1)] + "…"
    return f"{name}{started}{stages}{tail}"


# ---------------------------------------------------------------------------
# 内置条目
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BuiltinEntry:
    """一条内置知识：随代码发布的只读内容。"""

    id: str
    kind: str
    title: str
    when_to_use: str
    body: str


def _field_problems(
    entry_id: str,
    kind: str,
    title: str,
    when_to_use: str,
    body: str,
) -> list[tuple[str, str]]:
    """返回 ``[(字段, 原因)]``；空列表表示全部通过。

    ``id`` 的长度约束写在正则里（2–48 字符），与规格 §4.1 的
    ``^[a-z0-9][a-z0-9-]{1,47}$`` 逐字一致。
    """
    problems: list[tuple[str, str]] = []
    if ENTRY_ID_PATTERN.fullmatch(entry_id) is None:
        problems.append(("id", "id 必须匹配 ^[a-z0-9][a-z0-9-]{1,47}$"))
    if kind not in KNOWLEDGE_KINDS:
        problems.append(("kind", f"kind 必须是 {list(KNOWLEDGE_KINDS)} 之一"))
    if not 1 <= len(title) <= MAX_TITLE_CHARS:
        problems.append(("title", f"title 长度必须是 1–{MAX_TITLE_CHARS} 字"))
    if not 1 <= len(when_to_use) <= MAX_WHEN_TO_USE_CHARS:
        problems.append(
            ("when_to_use", f"when_to_use 长度必须是 1–{MAX_WHEN_TO_USE_CHARS} 字")
        )
    if not 1 <= len(body) <= MAX_BODY_CHARS:
        problems.append(("body", f"body 长度必须是 1–{MAX_BODY_CHARS} 字"))
    return problems


def _parse_builtin(name: str, text: str) -> BuiltinEntry:
    """解析一个内置 Markdown 文件；任何不合法都抛 :class:`KnowledgeBuiltinError`。"""
    match = _FRONTMATTER.match(text)
    if match is None:
        raise KnowledgeBuiltinError(f"内置知识 {name} 缺 frontmatter（--- 包起来的 YAML 头）")
    try:
        meta = YAML(typ="safe").load(match.group("meta"))
    except YAMLError as exc:
        raise KnowledgeBuiltinError(f"内置知识 {name} 的 frontmatter 不是合法 YAML: {exc}") from exc
    if not isinstance(meta, Mapping):
        raise KnowledgeBuiltinError(f"内置知识 {name} 的 frontmatter 不是键值映射")

    unknown = sorted(str(key) for key in set(meta) - _FRONTMATTER_FIELDS)
    if unknown:
        raise KnowledgeBuiltinError(f"内置知识 {name} 的 frontmatter 有未知字段: {unknown}")
    missing = sorted(_FRONTMATTER_FIELDS - set(meta))
    if missing:
        raise KnowledgeBuiltinError(f"内置知识 {name} 的 frontmatter 缺字段: {missing}")

    values: dict[str, str] = {}
    for field in ("id", "kind", "title", "when_to_use"):
        value = meta[field]
        if not isinstance(value, str):
            raise KnowledgeBuiltinError(f"内置知识 {name} 的 {field} 必须是字符串")
        values[field] = value

    body = (match.group("body") or "").strip()
    problems = _field_problems(
        values["id"], values["kind"], values["title"], values["when_to_use"], body
    )
    if problems:
        field, reason = problems[0]
        raise KnowledgeBuiltinError(f"内置知识 {name} 的 {field} 不合法：{reason}")
    return BuiltinEntry(body=body, **values)


def load_builtins(root: Traversable | None = None) -> dict[str, BuiltinEntry]:
    """读一个目录下的全部 ``*.md``，按 frontmatter 的 id 建索引。

    ``root`` 只为测试注入；默认读包内的 ``builtin/``。目录里一个 ``.md`` 都没有
    也算错（打包漏了数据文件时不能静默变成「知识库为空」）。
    """
    directory = root if root is not None else resources.files(BUILTIN_PACKAGE).joinpath(BUILTIN_DIRNAME)
    entries: dict[str, BuiltinEntry] = {}
    sources: dict[str, str] = {}
    for item in sorted(directory.iterdir(), key=lambda path: path.name):
        if not item.name.endswith(".md"):
            continue
        entry = _parse_builtin(item.name, item.read_text(encoding="utf-8"))
        if entry.id in entries:
            raise KnowledgeBuiltinError(
                f"内置知识 id 重复: {entry.id!r} 同时出现在 {sources[entry.id]} 与 {item.name}"
            )
        entries[entry.id] = entry
        sources[entry.id] = item.name
    if not entries:
        raise KnowledgeBuiltinError(f"内置知识目录里没有 .md 条目: {directory}")
    return entries


@lru_cache(maxsize=1)
def builtin_entries() -> dict[str, BuiltinEntry]:
    """进程内唯一的内置条目表；启动时（第一次构造服务）加载一次。"""
    return load_builtins()


# ---------------------------------------------------------------------------
# 视图模型（REST 与工具共用；Task 5 的请求模型可直接复用 KnowledgeWrite）
# ---------------------------------------------------------------------------
class KnowledgeBuiltinView(BaseModel):
    """内置条目的原文快照，供界面对比与恢复。"""

    id: str
    kind: str
    title: str
    when_to_use: str
    body: str


class KnowledgeWrite(BaseModel):
    """PUT 的入参：**完整的生效内容**，不是「本次改动的列」。

    调用方读详情、改字段、整份写回，服务层再算哪些列与内置不同。
    """

    kind: str
    title: str
    when_to_use: str
    body: str
    enabled: bool = True


class KnowledgeSummary(BaseModel):
    """列表项：不含正文，只给字数（规格 §4.1 的 REST 列表）。"""

    id: str
    kind: str
    title: str
    when_to_use: str
    enabled: bool
    source: str
    modified: bool
    body_chars: int
    updated_at: datetime | None = None


class KnowledgeEntry(BaseModel):
    """生效后的完整条目（内置或自定义）。"""

    id: str
    kind: str
    title: str
    when_to_use: str
    body: str
    enabled: bool
    source: str
    modified: bool
    updated_at: datetime | None = None


class KnowledgeDetail(KnowledgeEntry):
    """详情：内置条目另附原文快照（``builtin``），自定义条目为 ``None``。"""

    builtin: KnowledgeBuiltinView | None = None


@dataclass(frozen=True)
class _EffectiveEntry:
    """合并后的内部表示；``body`` 已是生效正文。"""

    id: str
    kind: str
    title: str
    when_to_use: str
    body: str
    enabled: bool
    source: str
    modified: bool
    updated_at: datetime | None


# ---------------------------------------------------------------------------
# 合并
# ---------------------------------------------------------------------------
def _merge(
    rows: Iterable[AgentKnowledge], builtins: Mapping[str, BuiltinEntry]
) -> dict[str, _EffectiveEntry]:
    """把覆盖行合并到内置条目上，返回 ``{id: 生效条目}``。

    覆盖行只存与内置不同的列：列为 ``None`` 就是「这一列跟随内置」。``kind``
    永远取内置值（不可覆盖）：上一版本留下的、与内置同 id 的自定义行也走这条
    路径，于是「新版本新增了同名内置条目」时用户内容优先、类型仍以内置为准。
    """
    merged = {
        entry_id: _EffectiveEntry(
            id=entry_id,
            kind=builtin.kind,
            title=builtin.title,
            when_to_use=builtin.when_to_use,
            body=builtin.body,
            enabled=True,
            source="builtin",
            modified=False,
            updated_at=None,
        )
        for entry_id, builtin in builtins.items()
    }
    for row in rows:
        builtin = builtins.get(row.id)
        if builtin is None:
            if None in (row.kind, row.title, row.when_to_use, row.body):
                raise RuntimeError(
                    f"自定义知识条目 {row.id!r} 的字段不完整；"
                    "agent_knowledge 被外部改过，请删掉这一行"
                )
            merged[row.id] = _EffectiveEntry(
                id=row.id,
                kind=row.kind,
                title=row.title,
                when_to_use=row.when_to_use,
                body=row.body,
                enabled=bool(row.enabled),
                source="custom",
                modified=False,
                updated_at=row.updated_at,
            )
            continue
        title = row.title if row.title is not None else builtin.title
        when_to_use = (
            row.when_to_use if row.when_to_use is not None else builtin.when_to_use
        )
        body = row.body if row.body is not None else builtin.body
        merged[row.id] = _EffectiveEntry(
            id=row.id,
            kind=builtin.kind,
            title=title,
            when_to_use=when_to_use,
            body=body,
            enabled=bool(row.enabled),
            source="builtin",
            modified=(
                title != builtin.title
                or when_to_use != builtin.when_to_use
                or body != builtin.body
                or not row.enabled
            ),
            updated_at=row.updated_at,
        )
    return merged


def _sorted(entries: Iterable[_EffectiveEntry]) -> list[_EffectiveEntry]:
    """展示顺序：先按 kind（playbook、screen、rule、glossary、profile），再按 id。"""
    return sorted(
        entries, key=lambda entry: (_KIND_ORDER.get(entry.kind, len(KNOWLEDGE_KINDS)), entry.id)
    )


def _summary(entry: _EffectiveEntry) -> KnowledgeSummary:
    return KnowledgeSummary(
        id=entry.id,
        kind=entry.kind,
        title=entry.title,
        when_to_use=entry.when_to_use,
        enabled=entry.enabled,
        source=entry.source,
        modified=entry.modified,
        body_chars=len(entry.body),
        updated_at=entry.updated_at,
    )


def _full(entry: _EffectiveEntry) -> KnowledgeEntry:
    return KnowledgeEntry(
        id=entry.id,
        kind=entry.kind,
        title=entry.title,
        when_to_use=entry.when_to_use,
        body=entry.body,
        enabled=entry.enabled,
        source=entry.source,
        modified=entry.modified,
        updated_at=entry.updated_at,
    )


def _detail(
    entry: _EffectiveEntry, builtins: Mapping[str, BuiltinEntry]
) -> KnowledgeDetail:
    builtin = builtins.get(entry.id)
    return KnowledgeDetail(
        **_full(entry).model_dump(),
        builtin=(
            KnowledgeBuiltinView(
                id=builtin.id,
                kind=builtin.kind,
                title=builtin.title,
                when_to_use=builtin.when_to_use,
                body=builtin.body,
            )
            if builtin is not None
            else None
        ),
    )


def _not_found(entry_id: str) -> AppError:
    return AppError(
        ErrorCode.KNOWLEDGE_NOT_FOUND,
        "知识条目不存在",
        {"entry_id": entry_id},
    )


def _override_row(
    entry_id: str, payload: KnowledgeWrite, builtin: BuiltinEntry, now: datetime
) -> AgentKnowledge | None:
    """把一次 PUT 折算成覆盖行；与内置完全一致且启用时返回 ``None``（删行）。"""
    if (
        payload.title == builtin.title
        and payload.when_to_use == builtin.when_to_use
        and payload.body == builtin.body
        and payload.enabled
    ):
        return None
    return AgentKnowledge(
        id=entry_id,
        kind=None,  # kind 不可覆盖
        title=None if payload.title == builtin.title else payload.title,
        when_to_use=(
            None if payload.when_to_use == builtin.when_to_use else payload.when_to_use
        ),
        body=None if payload.body == builtin.body else payload.body,
        enabled=payload.enabled,  # NOT NULL 列，只能存实际生效值
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------
class KnowledgeService:
    """知识库门面：构造时注入会话工厂，启动时加载一次内置条目。

    ``builtins`` 只为测试注入（例如验证「新版本新增内置条目」的迁移语义）；
    ``activity_index`` 是 M24 加的当期活动数据源。它与知识条目不同，读的是本机活动
    资源，会随资源更新变化，所以每轮现读、读不到就不插这一段。
    生产装配用 ``KnowledgeService(db_session.session_factory)``。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        *,
        builtins: Mapping[str, BuiltinEntry] | None = None,
        activity_index: "ActivityReader | None" = None,
        activity_service: "ActivityReader | None" = None,
    ) -> None:
        self._session_factory = session_factory or db_session.session_factory
        # 构造即加载：内置文件坏了要在装配处（启动）就炸，而不是等第一次请求
        self._builtins = dict(builtins) if builtins is not None else builtin_entries()
        self._activity_index = activity_index
        #: M25：外部活动服务（PRTS 优先）。没装时退回 M24 的本机快照口径。
        self._activity_service = activity_service
        #: 「当期活动读不到」的 warning 只在第一次失败时打一次（见 :meth:`_activity_section`）。
        self._activity_failure_logged = False

    # -- 读 ---------------------------------------------------------------
    async def list_entries(self) -> list[KnowledgeSummary]:
        """全部条目的摘要（含未启用的），按 kind、id 排序。"""
        async with self._session_factory() as session:
            rows = await AgentKnowledgeRepository(session).all()
        merged = _merge(rows, self._builtins)
        return [_summary(entry) for entry in _sorted(merged.values())]

    async def get_entry(self, entry_id: str) -> KnowledgeDetail:
        """一条条目的完整生效内容；未知 id 抛 ``KNOWLEDGE_NOT_FOUND``。"""
        entry = await self._load(entry_id)
        if entry is None:
            raise _not_found(entry_id)
        return _detail(entry, self._builtins)

    async def read_for_agent(self, entry_id: str) -> KnowledgeEntry:
        """给 ``read_knowledge`` 工具用：未启用与未知一样，都当不存在。"""
        entry = await self._load(entry_id)
        if entry is None or not entry.enabled:
            raise AppError(
                ErrorCode.KNOWLEDGE_NOT_FOUND,
                "知识条目不存在或已停用",
                {"entry_id": entry_id},
            )
        return _full(entry)

    async def render_context(self) -> str:
        """拼进 system 消息的知识库段（格式见规格 §4.1、M24 §4.3）。

        段落顺序：索引 → **当期活动**（M24，读不到就整段不插）→ 术语表 → 用户档案。
        索引与全文都取生效值；``glossary`` / ``profile`` 有多条启用时按 id 顺序全部注入。
        索引、术语表、用户档案为空时写「暂无」。
        """
        async with self._session_factory() as session:
            rows = await AgentKnowledgeRepository(session).all()
        enabled = [entry for entry in _merge(rows, self._builtins).values() if entry.enabled]

        index_lines = [
            f"- {entry.id}｜{entry.title}：{entry.when_to_use}"
            for entry in _sorted(item for item in enabled if item.kind in INDEX_KINDS)
        ]
        sections = (
            (GLOSSARY_HEADER, FULLTEXT_KINDS[0]),
            (PROFILE_HEADER, FULLTEXT_KINDS[1]),
        )
        lines = [INDEX_HEADER, *(index_lines or [EMPTY_SECTION])]
        activity = await self._activity_section()
        if activity:
            lines += ["", *activity]
        for header, kind in sections:
            bodies = [
                entry.body
                for entry in _sorted(item for item in enabled if item.kind == kind)
            ]
            lines += ["", header, "\n\n".join(bodies) if bodies else EMPTY_SECTION]
        return "\n".join(lines)

    async def _activity_section(self) -> list[str]:
        """当期活动段（标题 + 正文行）；读不出来时返回空列表（整段不插）。

        与「知识库读失败降级为『知识库暂不可用』」不同：这里没有内容可展示，插一行
        「暂无」只花 token 不提供信息（规格 §4.3）。

        读失败只记一次 warning：没装活动资源是干净检出的常态，每轮带栈刷屏会把日志淹掉；
        同一进程里失败过一次之后降为 debug。资源装好后第一次成功会重置这个标记。
        """
        try:
            report = await self._activity_report()
        except Exception:  # noqa: BLE001 - 活动数据只是可选上下文
            if not self._activity_failure_logged:
                self._activity_failure_logged = True
                logger.warning("当期活动数据读取失败，本轮起不再注入这一段", exc_info=True)
            else:
                logger.debug("当期活动数据仍然读不到，继续不注入这一段")
            return []
        self._activity_failure_logged = False
        return render_activity_section(report)

    async def _activity_report(self) -> ActivityReport | None:
        """取当期活动：装了外部服务就走 PRTS（3 s 预算），否则用本机快照。"""
        service = self._activity_service
        if service is not None and hasattr(service, "current"):
            report = await service.current(
                detail="summary",
                budget_seconds=ACTIVITY_INJECTION_BUDGET_SECONDS,
            )
            if report is not None:
                return report
        index = self._activity_index
        if index is None:
            index = default_activity_index()
        return report_from_snapshot(index.snapshot())

    # -- 写 ---------------------------------------------------------------
    async def put_entry(self, entry_id: str, payload: KnowledgeWrite) -> KnowledgeDetail:
        """写入完整的生效内容：新建自定义条目，或修改、停用内置条目。"""
        problems = _field_problems(
            entry_id, payload.kind, payload.title, payload.when_to_use, payload.body
        )
        if problems:
            field, reason = problems[0]
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                f"知识条目字段不合法：{reason}",
                {"entry_id": entry_id, "field": field, "reason": reason},
            )

        builtin = self._builtins.get(entry_id)
        async with self._session_factory() as session:
            repository = AgentKnowledgeRepository(session)
            row = await repository.get(entry_id)
            now = utcnow()
            if builtin is not None:
                if payload.kind != builtin.kind:
                    raise AppError(
                        ErrorCode.KNOWLEDGE_BUILTIN_READONLY,
                        "内置条目的类型不可修改",
                        {
                            "entry_id": entry_id,
                            "kind": payload.kind,
                            "builtin_kind": builtin.kind,
                        },
                    )
                proposed = _override_row(entry_id, payload, builtin, now)
                if proposed is not None and row is not None:
                    proposed.created_at = row.created_at
            else:
                proposed = AgentKnowledge(
                    id=entry_id,
                    kind=payload.kind,
                    title=payload.title,
                    when_to_use=payload.when_to_use,
                    body=payload.body,
                    enabled=payload.enabled,
                    created_at=row.created_at if row is not None else now,
                    updated_at=now,
                )

            # 读全表是为了返回**写入后的生效内容**（合并内置值），不再有上限判定：
            # 条目数与全文段字数交给模型自身的上下文窗口。
            rows = await repository.all()
            others = [item for item in rows if item.id != entry_id]
            effective = _merge(
                [*others, proposed] if proposed is not None else others, self._builtins
            )

            if proposed is None:
                await repository.delete(entry_id)
            else:
                await repository.upsert(proposed)
            await session.commit()

        return _detail(effective[entry_id], self._builtins)

    async def delete_entry(self, entry_id: str) -> None:
        """删除自定义条目，或撤销对内置条目的修改（恢复默认）。"""
        async with self._session_factory() as session:
            repository = AgentKnowledgeRepository(session)
            row = await repository.get(entry_id)
            if entry_id in self._builtins:
                if row is None:
                    raise AppError(
                        ErrorCode.KNOWLEDGE_BUILTIN_READONLY,
                        "内置条目还没被修改过，不能删除；要停用它请写入 enabled=false",
                        {"entry_id": entry_id},
                    )
            elif row is None:
                raise _not_found(entry_id)
            await repository.delete(entry_id)
            await session.commit()

    # -- 内部 -------------------------------------------------------------
    async def _load(self, entry_id: str) -> _EffectiveEntry | None:
        async with self._session_factory() as session:
            row = await AgentKnowledgeRepository(session).get(entry_id)
        merged = _merge([row] if row is not None else [], self._builtins)
        return merged.get(entry_id)
