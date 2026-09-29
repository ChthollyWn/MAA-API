"""Agent 知识库：内置 Markdown + 用户覆盖的合并与注入（M18，规格 §4.1）。

三件事，其余都别往这里塞：

- **内置条目**随代码发布，放在 ``builtin/*.md``，frontmatter 用 ruamel.yaml 解析
  （与 ``config.yaml`` / ``settings.py`` 同一套依赖）。启动时加载一次，
  文件缺失、id 重复或字段不合法都 fail loud —— 静默跳过会让 Agent 少一条剧本
  而没人发现。
- **覆盖语义**：``agent_knowledge`` 表里 ``id`` 与内置相同的行是覆盖行，只存与
  内置**不同**的列，其余为 NULL，读取时继承内置值；``kind`` 不可覆盖。写入时若
  没有任何字段与内置不同且处于启用状态，就删掉这一行（「恢复默认」）。
- **注入**：:meth:`KnowledgeService.render_context` 返回拼进 system 消息的那段
  文本：索引（playbook / screen / rule，先按 kind 再按 id 排序）+ 术语表全文 +
  用户档案全文。

上限在写入时按**写入后的生效状态**判定（不是按请求体）：启用条目 ≤ 50、
启用的 glossary + profile 正文合计 ≤ 3,000 字。违反的返回
``KNOWLEDGE_LIMIT_EXCEEDED``（409），``details`` 写明是哪条限制；字段格式与长度
在请求模型里拦成 422，这里的同名校验是兜底。

信任边界（规格 §4.1「信任边界」）：知识库内容由持有访问令牌的人编写，当作操作
指引；截图、日志和其他工具结果仍是不可信数据。本模块不给 Agent 任何写入通道。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from importlib import resources
from importlib.resources.abc import Traversable

from pydantic import BaseModel
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.db import session as db_session
from maa_api.db.models import AgentKnowledge, utcnow
from maa_api.db.repositories.agent_knowledge import AgentKnowledgeRepository
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "BUILTIN_PACKAGE",
    "ENTRY_ID_PATTERN",
    "FULLTEXT_KINDS",
    "INDEX_KINDS",
    "INDEX_HEADER",
    "GLOSSARY_HEADER",
    "PROFILE_HEADER",
    "KNOWLEDGE_KINDS",
    "MAX_BODY_CHARS",
    "MAX_ENABLED_ENTRIES",
    "MAX_FULLTEXT_CHARS",
    "MAX_TITLE_CHARS",
    "MAX_WHEN_TO_USE_CHARS",
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

MAX_TITLE_CHARS = 32
MAX_WHEN_TO_USE_CHARS = 120
MAX_BODY_CHARS = 20_000
MAX_ENABLED_ENTRIES = 50
MAX_FULLTEXT_CHARS = 3_000

#: 注入段的三行标题，照抄规格 §4.1 的示例。
INDEX_HEADER = (
    "知识库索引（开发者与用户维护，可作为操作指引；需要时用 read_knowledge 读取全文）："
)
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
    永远取内置值（不可覆盖）——上一版本留下的、与内置同 id 的自定义行也走这条
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


def _check_limits(entries: Mapping[str, _EffectiveEntry]) -> None:
    """按写入后的生效状态判上限；违反哪条就在 ``details`` 里写哪条。"""
    enabled = [entry for entry in entries.values() if entry.enabled]
    if len(enabled) > MAX_ENABLED_ENTRIES:
        raise AppError(
            ErrorCode.KNOWLEDGE_LIMIT_EXCEEDED,
            f"启用的知识条目不能超过 {MAX_ENABLED_ENTRIES} 条",
            {
                "limit": "enabled_entries",
                "max": MAX_ENABLED_ENTRIES,
                "actual": len(enabled),
            },
        )
    fulltext_chars = sum(
        len(entry.body) for entry in enabled if entry.kind in FULLTEXT_KINDS
    )
    if fulltext_chars > MAX_FULLTEXT_CHARS:
        raise AppError(
            ErrorCode.KNOWLEDGE_LIMIT_EXCEEDED,
            f"术语表与用户档案的正文合计不能超过 {MAX_FULLTEXT_CHARS} 字",
            {
                "limit": "fulltext_chars",
                "max": MAX_FULLTEXT_CHARS,
                "actual": fulltext_chars,
            },
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
    生产装配用 ``KnowledgeService(db_session.session_factory)``。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        *,
        builtins: Mapping[str, BuiltinEntry] | None = None,
    ) -> None:
        self._session_factory = session_factory or db_session.session_factory
        # 构造即加载：内置文件坏了要在装配处（启动）就炸，而不是等第一次请求
        self._builtins = dict(builtins) if builtins is not None else builtin_entries()

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
        """拼进 system 消息的知识库段（格式见规格 §4.1）。

        索引与全文都取生效值；``glossary`` / ``profile`` 有多条启用时按 id 顺序
        全部注入（字数上限正是按它们的正文合计算的，只注入一条会让上限算到看不见
        的内容上）。空段落写「暂无」。
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
        for header, kind in sections:
            bodies = [
                entry.body
                for entry in _sorted(item for item in enabled if item.kind == kind)
            ]
            lines += ["", header, "\n\n".join(bodies) if bodies else EMPTY_SECTION]
        return "\n".join(lines)

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

            # 上限按写入后的生效状态判定：先把这一行换成本次的结果，再看全表
            rows = await repository.all()
            others = [item for item in rows if item.id != entry_id]
            effective = _merge(
                [*others, proposed] if proposed is not None else others, self._builtins
            )
            _check_limits(effective)

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
