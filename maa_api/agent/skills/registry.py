"""技能注册表：内置技能加载、覆盖合并、校验、读写、索引与加载（M78 规格 §4.2）。

四条纪律：

- **内置技能随代码发布**，放在 ``builtin/<id>/SKILL.md``；启动时加载一次，目录名与
  ``id`` 不符、frontmatter 字段缺失或多余、长度不合法、``id`` 重复都 fail loud
  （``SkillBuiltinError``），绝不静默跳过。
- **``agent_skill`` 表只存覆盖层**：覆盖行只写与内置不同的列，NULL 继承内置值；
  与内置完全一致且启用时不留行（「恢复默认」＝删行）。用户技能与生成草稿是独立行。
- **校验集中在写入侧**：id / 标题 / 说明 / 正文的长度、工具与代理的注册情况都在
  :meth:`SkillRegistry.put_skill` 里检查，不合法统一报 ``SKILL_INVALID``；
  REST 模型层只负责形状（类型、多余字段），不重复限长。
- **读侧对代理做过滤**：``index_for`` 与 ``load`` 都看 ``skills.enabled``、
  ``enabled``、``status`` 与 ``agents``；``load`` 的失败统一报 ``SKILL_NOT_FOUND``，
  用不同消息区分「不存在 / 停用」「不对该代理开放」「设置里关闭」。

模块内还有一个不导出的 ``_BuiltinSkillTable``：``load_builtin_skills`` 返回的 dict
额外带 ``root`` 属性，让测试注入的临时内置目录也能被 :meth:`SkillRegistry.read_file`
读到附属文件；它仍是 ``dict``，对外契约不变。
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from maa_api.agent.specs.schema import AgentSpec
from maa_api.db.models import AgentSkill, utcnow
from maa_api.db.repositories.agent_skill import AgentSkillRepository
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import Settings, get_settings

logger = logging.getLogger(__name__)

__all__ = [
    "BUILTIN_DIRNAME",
    "BUILTIN_PACKAGE",
    "FRONTMATTER_FIELDS",
    "INDEX_HEADER",
    "MAX_BODY_CHARS",
    "MAX_DESCRIPTION_CHARS",
    "MAX_INJECTED_BODY_CHARS",
    "MAX_SKILL_FILE_BYTES",
    "MAX_TITLE_CHARS",
    "SKILL_FILE_PATTERN",
    "SKILL_ID_PATTERN",
    "BuiltinSkill",
    "LoadedSkill",
    "SkillBuiltinError",
    "SkillBuiltinView",
    "SkillDetail",
    "SkillIndexEntry",
    "SkillRegistry",
    "SkillSummary",
    "SkillWrite",
    "builtin_skills",
    "load_builtin_skills",
]

#: ``id`` 是 URL 路径段与技能目录名，只允许小写字母、数字与中划线，2–48 个字符。
SKILL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")
MAX_TITLE_CHARS, MAX_DESCRIPTION_CHARS, MAX_BODY_CHARS = 32, 200, 20_000
#: 单个附属文件的上限（读的时候也复查）。``SKILL.md`` 本身不走这一条。
MAX_SKILL_FILE_BYTES = 64 * 1024
#: 附属文件相对路径：根目录一层，或一个子目录一层；扩展名限定为文本。
SKILL_FILE_PATTERN = re.compile(
    r"^(?:[a-z0-9_-]+/)?[a-z0-9][a-z0-9._-]{0,63}\.(?:md|json|yaml|yml|txt)$"
)
#: ``SKILL.md`` 的 frontmatter 恰好这 6 个字段。
FRONTMATTER_FIELDS = frozenset({"id", "title", "description", "tools", "agents", "version"})
#: 预加载与压缩后补回的正文合计上限（中间件用；注册表只导出常量）。
MAX_INJECTED_BODY_CHARS = 12_000

BUILTIN_PACKAGE = "maa_api.agent.skills"
BUILTIN_DIRNAME = "builtin"

#: 技能索引段的标题，逐字照抄规格 §4.2。
INDEX_HEADER = (
    "技能索引（需要时用 load_skill 加载全文；预加载与压缩前加载过的技能正文附在后面）："
)

#: 技能能挂到的代理种类：总控与 Worker（子代理、后台任务不加载技能）。
_SKILL_AGENT_KINDS = frozenset({"orchestrator", "worker"})

_FRONTMATTER = re.compile(
    r"\A---[ \t]*\r?\n(?P<meta>.*?)\r?\n---[ \t]*(?:\r?\n(?P<body>.*))?\Z",
    re.DOTALL,
)


class SkillBuiltinError(RuntimeError):
    """内置技能不合法或预加载配置不成立：启动时 fail loud，绝不静默跳过。"""


@dataclass(frozen=True, slots=True)
class BuiltinSkill:
    """一个内置技能：随代码发布的只读内容与附属文件清单。"""

    id: str
    title: str
    description: str
    tools: tuple[str, ...]
    agents: tuple[str, ...]
    version: int
    body: str
    #: 同目录除 ``SKILL.md`` 外、匹配 :data:`SKILL_FILE_PATTERN` 的相对路径。
    files: tuple[str, ...]


class _BuiltinSkillTable(dict[str, BuiltinSkill]):
    """按 id 索引的内置技能表，附带来源目录（``read_file`` 找附属文件用）。

    对外类型仍是 ``dict[str, BuiltinSkill]``；只在 ``load_builtin_skills`` 里构造，
    让 ``SkillRegistry`` 能读测试注入的临时目录，而不是永远回退到包内路径。
    """

    __slots__ = ("root",)

    def __init__(self, root: Traversable) -> None:
        super().__init__()
        self.root = root


@dataclass(frozen=True, slots=True)
class SkillIndexEntry:
    """系统提示里的一行技能索引。"""

    id: str
    title: str
    description: str


class SkillSummary(BaseModel):
    """列表项：不含正文，只给字数与来源（规格 §4.2 的 REST 列表）。"""

    id: str
    title: str
    description: str
    tools: list[str]
    agents: list[str]
    source: Literal["builtin", "user", "generated"]
    modified: bool
    status: Literal["draft", "active"]
    enabled: bool
    body_chars: int
    files: list[str]
    version: int | None
    updated_at: datetime | None


class SkillBuiltinView(BaseModel):
    """内置技能的原文快照，供界面对比与恢复。"""

    id: str
    title: str
    description: str
    tools: list[str]
    agents: list[str]
    body: str
    version: int


class SkillDetail(SkillSummary):
    """详情：生效正文 + 内置原文快照（自定义技能为 ``None``）。"""

    body: str
    builtin: SkillBuiltinView | None


class SkillWrite(BaseModel):
    """PUT 的完整生效内容；不在模型层限长，校验失败统一报 ``SKILL_INVALID``。"""

    title: str
    description: str
    body: str
    tools: list[str] = []
    agents: list[str] = []
    enabled: bool = True


class LoadedSkill(BaseModel):
    """``load_skill`` 的返回：生效全文、工具与附属文件清单。"""

    id: str
    title: str
    description: str
    body: str
    tools: list[str]
    files: list[str]
    #: ``sha256(body + "\n" + ",".join(tools))`` 的前 12 位。
    version: str


@dataclass(frozen=True, slots=True)
class _EffectiveSkill:
    """合并后的内部表示：``body`` 已是生效正文。"""

    id: str
    title: str
    description: str
    body: str
    tools: tuple[str, ...]
    agents: tuple[str, ...]
    source: Literal["builtin", "user", "generated"]
    modified: bool
    status: Literal["draft", "active"]
    enabled: bool
    files: tuple[str, ...]
    version: int | None
    updated_at: datetime | None


# ---------------------------------------------------------------------------
# 内置技能加载
# ---------------------------------------------------------------------------
def _field_problems(
    skill_id: str, title: str, description: str, body: str
) -> list[tuple[str, str]]:
    """返回 ``[(字段, 原因)]``；空列表表示全部通过。"""
    problems: list[tuple[str, str]] = []
    if SKILL_ID_PATTERN.fullmatch(skill_id) is None:
        problems.append(("id", "id 必须匹配 ^[a-z0-9][a-z0-9-]{1,47}$"))
    if not 1 <= len(title) <= MAX_TITLE_CHARS:
        problems.append(("title", f"title 长度必须是 1–{MAX_TITLE_CHARS} 字"))
    if not 1 <= len(description) <= MAX_DESCRIPTION_CHARS:
        problems.append(
            ("description", f"description 长度必须是 1–{MAX_DESCRIPTION_CHARS} 字")
        )
    if not 1 <= len(body) <= MAX_BODY_CHARS:
        problems.append(("body", f"body 长度必须是 1–{MAX_BODY_CHARS} 字"))
    return problems


def _string_tuple(name: str, field: str, value: object) -> tuple[str, ...]:
    """frontmatter 里的 ``tools`` / ``agents``：字符串数组；形状不对就 fail loud。"""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise SkillBuiltinError(f"内置技能 {name} 的 {field} 必须是字符串数组")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise SkillBuiltinError(f"内置技能 {name} 的 {field} 里有非字符串或空项")
        items.append(item)
    return tuple(items)


def _parse_builtin(
    directory_name: str, file_name: str, text: str, files: tuple[str, ...]
) -> BuiltinSkill:
    """解析一个 ``SKILL.md``；任何不合法都抛 :class:`SkillBuiltinError`。"""
    match = _FRONTMATTER.match(text)
    if match is None:
        raise SkillBuiltinError(
            f"内置技能 {file_name} 缺 frontmatter（--- 包起来的 YAML 头）"
        )
    try:
        meta = YAML(typ="safe").load(match.group("meta"))
    except YAMLError as exc:
        raise SkillBuiltinError(
            f"内置技能 {file_name} 的 frontmatter 不是合法 YAML: {exc}"
        ) from exc
    if not isinstance(meta, Mapping):
        raise SkillBuiltinError(f"内置技能 {file_name} 的 frontmatter 不是键值映射")

    unknown = sorted(str(key) for key in set(meta) - FRONTMATTER_FIELDS)
    if unknown:
        raise SkillBuiltinError(f"内置技能 {file_name} 的 frontmatter 有未知字段: {unknown}")
    missing = sorted(FRONTMATTER_FIELDS - set(meta))
    if missing:
        raise SkillBuiltinError(f"内置技能 {file_name} 的 frontmatter 缺字段: {missing}")

    for field in ("id", "title", "description"):
        if not isinstance(meta[field], str):
            raise SkillBuiltinError(f"内置技能 {file_name} 的 {field} 必须是字符串")
    version = meta["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise SkillBuiltinError(f"内置技能 {file_name} 的 version 必须是正整数")

    skill_id = meta["id"]
    if skill_id != directory_name:
        raise SkillBuiltinError(
            f"内置技能目录名 {directory_name!r} 与 frontmatter 的 id {skill_id!r} 不一致"
        )
    body = (match.group("body") or "").strip()
    problems = _field_problems(skill_id, meta["title"], meta["description"], body)
    if problems:
        field, reason = problems[0]
        raise SkillBuiltinError(f"内置技能 {file_name} 的 {field} 不合法：{reason}")
    return BuiltinSkill(
        id=skill_id,
        title=meta["title"],
        description=meta["description"],
        tools=_string_tuple(file_name, "tools", meta["tools"]),
        agents=_string_tuple(file_name, "agents", meta["agents"]),
        version=version,
        body=body,
        files=files,
    )


def _files_for(directory: Traversable) -> tuple[str, ...]:
    """列出技能目录里除 ``SKILL.md`` 外的附属文件（根目录或一层子目录）。"""
    names: list[str] = []
    for item in sorted(directory.iterdir(), key=lambda path: path.name):
        if item.name == "SKILL.md":
            continue
        if item.is_file() and SKILL_FILE_PATTERN.fullmatch(item.name):
            names.append(item.name)
            continue
        if not item.is_dir():
            continue
        for child in sorted(item.iterdir(), key=lambda path: path.name):
            relative = f"{item.name}/{child.name}"
            if child.is_file() and SKILL_FILE_PATTERN.fullmatch(relative):
                names.append(relative)
    return tuple(sorted(names))


def load_builtin_skills(root: Traversable | None = None) -> dict[str, BuiltinSkill]:
    """读 ``builtin/*/SKILL.md``（或测试注入的目录），按 id 建索引。

    目录名不等于 id、frontmatter 字段缺失或多余、长度不合法、id 重复都抛
    :class:`SkillBuiltinError`；一个技能都没有也算错（打包漏了数据文件时不能静默
    变成「没有技能」）。
    """
    directory = (
        root
        if root is not None
        else resources.files(BUILTIN_PACKAGE).joinpath(BUILTIN_DIRNAME)
    )
    table = _BuiltinSkillTable(directory)
    sources: dict[str, str] = {}
    for item in sorted(directory.iterdir(), key=lambda path: path.name):
        if not item.is_dir():
            continue
        skill_file = item.joinpath("SKILL.md")
        file_name = f"{item.name}/SKILL.md"
        if not skill_file.is_file():
            raise SkillBuiltinError(f"内置技能目录 {item.name!r} 缺 SKILL.md")
        try:
            text = skill_file.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise SkillBuiltinError(
                f"内置技能 {file_name} 不是合法 UTF-8：{exc}"
            ) from exc
        skill = _parse_builtin(item.name, file_name, text, _files_for(item))
        if skill.id in table:
            raise SkillBuiltinError(
                f"内置技能 id 重复: {skill.id!r}（{sources[skill.id]} 与 {file_name}）"
            )
        table[skill.id] = skill
        sources[skill.id] = file_name
    if not table:
        raise SkillBuiltinError(f"内置技能目录里没有技能: {directory}")
    return table


@lru_cache(maxsize=1)
def builtin_skills() -> dict[str, BuiltinSkill]:
    """进程内唯一的内置技能表；启动时（第一次构造注册表）加载一次。"""
    return load_builtin_skills()


# ---------------------------------------------------------------------------
# 合并
# ---------------------------------------------------------------------------
def _merge(
    rows: Iterable[AgentSkill], builtins: Mapping[str, BuiltinSkill]
) -> dict[str, _EffectiveSkill]:
    """把覆盖行合并到内置技能上，返回 ``{id: 生效技能}``。

    覆盖行只存与内置不同的列：列为 ``None`` 就是「这一列跟随内置」（``tools`` /
    ``agents`` 为 NULL 也继承内置值）。``origin='override'`` 却找不到内置 id 的行
    （新版本删了内置技能）跳过并记一条 warning，不让整个列表读不出来。
    """
    merged = {
        skill_id: _EffectiveSkill(
            id=skill_id,
            title=builtin.title,
            description=builtin.description,
            body=builtin.body,
            tools=builtin.tools,
            agents=builtin.agents,
            source="builtin",
            modified=False,
            status="active",
            enabled=True,
            files=builtin.files,
            version=builtin.version,
            updated_at=None,
        )
        for skill_id, builtin in builtins.items()
    }
    for row in rows:
        builtin = builtins.get(row.id)
        if builtin is None:
            if row.origin == "override":
                logger.warning("技能覆盖行 %r 没有对应的内置技能，已跳过", row.id)
                continue
            missing = [
                field
                for field in ("title", "description", "body")
                if getattr(row, field) is None
            ]
            if missing:
                raise RuntimeError(
                    f"自定义技能 {row.id!r} 的字段不完整：{missing}；"
                    "agent_skill 被外部改过，请删掉这一行"
                )
            merged[row.id] = _EffectiveSkill(
                id=row.id,
                title=row.title,
                description=row.description,
                body=row.body,
                tools=tuple(row.tools or ()),
                agents=tuple(row.agents or ()),
                source="generated" if row.origin == "generated" else "user",
                modified=False,
                status=row.status if row.status in ("draft", "active") else "active",
                enabled=bool(row.enabled),
                files=(),
                version=None,
                updated_at=row.updated_at,
            )
            continue

        title = row.title if row.title is not None else builtin.title
        description = (
            row.description if row.description is not None else builtin.description
        )
        body = row.body if row.body is not None else builtin.body
        tools = tuple(row.tools) if row.tools is not None else builtin.tools
        agents = tuple(row.agents) if row.agents is not None else builtin.agents
        merged[row.id] = _EffectiveSkill(
            id=row.id,
            title=title,
            description=description,
            body=body,
            tools=tools,
            agents=agents,
            source="builtin",
            modified=(
                title != builtin.title
                or description != builtin.description
                or body != builtin.body
                or tools != builtin.tools
                or agents != builtin.agents
                or not row.enabled
            ),
            status=row.status if row.status in ("draft", "active") else "active",
            enabled=bool(row.enabled),
            files=builtin.files,
            version=builtin.version,
            updated_at=row.updated_at,
        )
    return merged


def _summary(effective: _EffectiveSkill) -> SkillSummary:
    return SkillSummary(
        id=effective.id,
        title=effective.title,
        description=effective.description,
        tools=list(effective.tools),
        agents=list(effective.agents),
        source=effective.source,
        modified=effective.modified,
        status=effective.status,
        enabled=effective.enabled,
        body_chars=len(effective.body),
        files=list(effective.files),
        version=effective.version,
        updated_at=effective.updated_at,
    )


def _detail(
    effective: _EffectiveSkill, builtins: Mapping[str, BuiltinSkill]
) -> SkillDetail:
    builtin = builtins.get(effective.id)
    return SkillDetail(
        **_summary(effective).model_dump(),
        body=effective.body,
        builtin=(
            SkillBuiltinView(
                id=builtin.id,
                title=builtin.title,
                description=builtin.description,
                tools=list(builtin.tools),
                agents=list(builtin.agents),
                body=builtin.body,
                version=builtin.version,
            )
            if builtin is not None
            else None
        ),
    )


def _not_found(skill_id: str) -> AppError:
    return AppError(
        ErrorCode.SKILL_NOT_FOUND, "技能不存在", {"skill_id": skill_id}
    )


def _override_row(
    skill_id: str,
    payload: SkillWrite,
    builtin: BuiltinSkill,
    *,
    now: datetime,
    existing: AgentSkill | None,
) -> AgentSkill | None:
    """把一次 PUT 折算成覆盖行；与内置完全一致且启用时返回 ``None``（删行）。"""
    title = None if payload.title == builtin.title else payload.title
    description = (
        None if payload.description == builtin.description else payload.description
    )
    body = None if payload.body == builtin.body else payload.body
    tools = None if list(payload.tools) == list(builtin.tools) else list(payload.tools)
    agents = (
        None if list(payload.agents) == list(builtin.agents) else list(payload.agents)
    )
    if (
        title is None
        and description is None
        and body is None
        and tools is None
        and agents is None
        and payload.enabled
    ):
        return None
    return AgentSkill(
        id=skill_id,
        title=title,
        description=description,
        body=body,
        tools=tools,
        agents=agents,
        origin="override",
        status="active",
        enabled=payload.enabled,
        created_at=existing.created_at if existing is not None else now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------
class SkillRegistry:
    """技能门面：启动时加载内置技能、校验复用注册表，运行时合并覆盖层。

    ``builtins`` 只为测试注入；``settings_provider`` 与运行时其它组件同款，
    每次现读，``skills.enabled`` 关闭时索引与加载都不可用。
    """

    def __init__(
        self,
        session_factory,
        *,
        tool_names: Collection[str],
        agent_kinds: Mapping[str, str],
        builtins: Mapping[str, BuiltinSkill] | None = None,
        settings_provider: Callable[[], Settings] = get_settings,
    ) -> None:
        self._session_factory = session_factory
        self._tool_names = frozenset(tool_names)
        self._agent_kinds = dict(agent_kinds)
        self._builtins: Mapping[str, BuiltinSkill] = (
            builtins if builtins is not None else builtin_skills()
        )
        # 注入的表可能来自临时目录：load_builtin_skills 返回的 dict 带 root。
        self._builtins_root: Traversable = getattr(
            self._builtins, "root", None
        ) or resources.files(BUILTIN_PACKAGE).joinpath(BUILTIN_DIRNAME)
        self._settings_provider = settings_provider
        self._validate_builtins()

    def _validate_builtins(self) -> None:
        """内置技能的工具必须都在注册表里，代理必须是总控或 Worker。"""
        for skill in self._builtins.values():
            unknown_tools = sorted(set(skill.tools) - self._tool_names)
            if unknown_tools:
                raise SkillBuiltinError(
                    f"内置技能 {skill.id!r} 引用了未注册的工具: {unknown_tools}"
                )
            unknown_agents = sorted(
                agent
                for agent in skill.agents
                if self._agent_kinds.get(agent) not in _SKILL_AGENT_KINDS
            )
            if unknown_agents:
                raise SkillBuiltinError(
                    f"内置技能 {skill.id!r} 的 agents 只能是 orchestrator 或 worker "
                    f"定义，无效: {unknown_agents}"
                )

    def _settings_enabled(self) -> bool:
        """``skills.enabled``；这一设置段尚未装配时按开启处理（G6 前）。"""
        skills = getattr(self._settings_provider(), "skills", None)
        return True if skills is None else bool(skills.enabled)

    async def _rows(self) -> list[AgentSkill]:
        async with self._session_factory() as session:
            return await AgentSkillRepository(session).all()

    # -- 读 ---------------------------------------------------------------
    async def list_skills(self) -> list[SkillSummary]:
        """全部技能（含停用与草稿），按 id 排序。"""
        merged = _merge(await self._rows(), self._builtins)
        return [_summary(merged[skill_id]) for skill_id in sorted(merged)]

    async def get_skill(self, skill_id: str) -> SkillDetail:
        """一条技能的完整生效内容；未知 id 抛 ``SKILL_NOT_FOUND``。"""
        effective = _merge(await self._rows(), self._builtins).get(skill_id)
        if effective is None:
            raise _not_found(skill_id)
        return _detail(effective, self._builtins)

    # -- 写 ---------------------------------------------------------------
    async def put_skill(self, skill_id: str, payload: SkillWrite) -> SkillDetail:
        """写入完整生效内容：新建自定义技能，或修改、停用内置技能。"""
        self._validate(skill_id, payload)
        builtin = self._builtins.get(skill_id)
        now = utcnow()
        async with self._session_factory() as session:
            repository = AgentSkillRepository(session)
            existing = await repository.get(skill_id)
            if builtin is not None:
                proposed = _override_row(
                    skill_id, payload, builtin, now=now, existing=existing
                )
                if proposed is None:
                    await repository.delete(skill_id)
                else:
                    await repository.upsert(proposed)
            else:
                await repository.upsert(
                    AgentSkill(
                        id=skill_id,
                        title=payload.title,
                        description=payload.description,
                        body=payload.body,
                        tools=list(payload.tools),
                        agents=list(payload.agents),
                        origin="user",
                        status="active",
                        enabled=payload.enabled,
                        created_at=existing.created_at if existing is not None else now,
                        updated_at=now,
                    )
                )
            # 在提交前合并：同一会话里看得到刚写入的行，返回写入后的生效内容。
            effective = _merge(await repository.all(), self._builtins)[skill_id]
            await session.commit()
        return _detail(effective, self._builtins)

    async def delete_skill(self, skill_id: str) -> None:
        """删除自定义技能，或撤销对内置技能的修改（恢复默认）。"""
        async with self._session_factory() as session:
            repository = AgentSkillRepository(session)
            existing = await repository.get(skill_id)
            if skill_id in self._builtins:
                if existing is None:
                    raise AppError(
                        ErrorCode.SKILL_BUILTIN_READONLY,
                        "内置技能没有覆盖可删；要停用它请写入 enabled=false",
                        {"skill_id": skill_id},
                    )
            elif existing is None:
                raise _not_found(skill_id)
            await repository.delete(skill_id)
            await session.commit()

    # -- 运行时 -----------------------------------------------------------
    async def index_for(self, agent_name: str) -> list[SkillIndexEntry]:
        """该代理可用的技能索引；``skills.enabled`` 关闭时为空。"""
        if not self._settings_enabled():
            return []
        merged = _merge(await self._rows(), self._builtins)
        return [
            SkillIndexEntry(
                id=effective.id,
                title=effective.title,
                description=effective.description,
            )
            for effective in sorted(merged.values(), key=lambda item: item.id)
            if effective.enabled
            and effective.status == "active"
            and (not effective.agents or agent_name in effective.agents)
        ]

    async def load(self, skill_id: str, *, agent_name: str | None) -> LoadedSkill:
        """加载一份技能全文；五种不可用情况统一报 ``SKILL_NOT_FOUND``。"""
        details = {"skill_id": skill_id, "agent": agent_name}
        if not self._settings_enabled():
            raise AppError(
                ErrorCode.SKILL_NOT_FOUND, "技能已在设置里关闭", details
            )
        effective = _merge(await self._rows(), self._builtins).get(skill_id)
        if effective is None or not effective.enabled or effective.status != "active":
            raise AppError(
                ErrorCode.SKILL_NOT_FOUND, "技能不存在或已停用", details
            )
        if agent_name is not None and effective.agents and agent_name not in effective.agents:
            raise AppError(
                ErrorCode.SKILL_NOT_FOUND, "这个技能不对当前代理开放", details
            )
        payload = effective.body + "\n" + ",".join(effective.tools)
        return LoadedSkill(
            id=effective.id,
            title=effective.title,
            description=effective.description,
            body=effective.body,
            tools=list(effective.tools),
            files=list(effective.files),
            version=hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12],
        )

    def read_file(self, skill_id: str, path: str) -> str:
        """读内置技能的附属文件；任何不合规都报 ``SKILL_NOT_FOUND``。"""
        builtin = self._builtins.get(skill_id)
        if builtin is None:
            raise _file_not_found(skill_id, path, "只有内置技能有附属文件")
        if (
            not isinstance(path, str)
            or ".." in path
            or SKILL_FILE_PATTERN.fullmatch(path) is None
        ):
            raise _file_not_found(skill_id, path, "路径不合法")
        skill_dir = self._builtins_root.joinpath(skill_id)
        try:
            base = Path(skill_dir)
        except TypeError:
            # 非文件系统资源（压缩包）没有软链接概念，直接用 Traversable 读。
            target = skill_dir.joinpath(*path.split("/"))
            if not target.is_file():
                raise _file_not_found(skill_id, path, "文件不存在") from None
            data = target.read_bytes()
        else:
            resolved_base = base.resolve()
            try:
                resolved = (base / path).resolve()
            except OSError:
                raise _file_not_found(skill_id, path, "文件不存在") from None
            if not resolved.is_relative_to(resolved_base):
                raise _file_not_found(skill_id, path, "路径不合法")
            if not resolved.is_file():
                raise _file_not_found(skill_id, path, "文件不存在")
            data = resolved.read_bytes()
        if len(data) > MAX_SKILL_FILE_BYTES:
            raise _file_not_found(skill_id, path, "文件太大")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise _file_not_found(skill_id, path, "不是 UTF-8 文本") from None

    def validate_preloads(self, specs: Iterable[AgentSpec]) -> None:
        """代理定义预加载的技能必须是内置技能且对它开放；否则启动失败。"""
        for spec in specs:
            for skill_id in spec.skills:
                builtin = self._builtins.get(skill_id)
                if builtin is None:
                    raise SkillBuiltinError(
                        f"代理定义 {spec.name!r} 预加载的技能 {skill_id!r} 不是内置技能"
                    )
                if builtin.agents and spec.name not in builtin.agents:
                    raise SkillBuiltinError(
                        f"代理定义 {spec.name!r} 预加载的技能 {skill_id!r} 不对它开放"
                        f"（agents={list(builtin.agents)}）"
                    )

    @staticmethod
    def render_index(entries: Sequence[SkillIndexEntry]) -> str:
        """技能索引段；没有可用技能时返回空串（整段不出现）。"""
        rows = [
            f"- {entry.id}｜{entry.title}：{entry.description}" for entry in entries
        ]
        if not rows:
            return ""
        return "\n".join([INDEX_HEADER, *rows])

    # -- 校验 -------------------------------------------------------------
    def _validate(self, skill_id: str, payload: SkillWrite) -> None:
        """写入侧校验；不合法统一报 ``SKILL_INVALID``。"""
        if SKILL_ID_PATTERN.fullmatch(skill_id) is None:
            raise _invalid(skill_id, "id", "id 必须匹配 ^[a-z0-9][a-z0-9-]{1,47}$")
        if not 1 <= len(payload.title) <= MAX_TITLE_CHARS:
            raise _invalid(
                skill_id, "title", f"title 长度必须是 1–{MAX_TITLE_CHARS} 字"
            )
        if not 1 <= len(payload.description) <= MAX_DESCRIPTION_CHARS:
            raise _invalid(
                skill_id,
                "description",
                f"description 长度必须是 1–{MAX_DESCRIPTION_CHARS} 字",
            )
        if not 1 <= len(payload.body) <= MAX_BODY_CHARS:
            raise _invalid(skill_id, "body", f"body 长度必须是 1–{MAX_BODY_CHARS} 字")
        unknown_tools = sorted(set(payload.tools) - self._tool_names)
        if unknown_tools:
            raise _invalid(
                skill_id,
                "tools",
                f"未知工具 {unknown_tools}",
                unknown=unknown_tools,
            )
        unknown_agents = sorted(
            agent
            for agent in payload.agents
            if self._agent_kinds.get(agent) not in _SKILL_AGENT_KINDS
        )
        if unknown_agents:
            raise _invalid(
                skill_id,
                "agents",
                f"未知或不可用的代理 {unknown_agents}",
                unknown=unknown_agents,
            )


def _invalid(
    skill_id: str, field: str, reason: str, *, unknown: list[str] | None = None
) -> AppError:
    details: dict[str, object] = {
        "skill_id": skill_id,
        "field": field,
        "reason": reason,
    }
    if unknown is not None:
        details["unknown"] = unknown
    return AppError(ErrorCode.SKILL_INVALID, f"技能内容不合法：{reason}", details)


def _file_not_found(skill_id: str, path: str, reason: str) -> AppError:
    return AppError(
        ErrorCode.SKILL_NOT_FOUND,
        f"技能文件不可读：{reason}",
        {"skill_id": skill_id, "path": path, "reason": reason},
    )
