"""M30 T3：删除内置条目后，把幸存下来的用户覆盖行迁移成自定义条目。

为什么需要冻结
    ``agent_knowledge`` 的覆盖行只存与内置**不同**的列，其余为 NULL，读的时候
    继承内置值（见 :mod:`maa_api.agent.knowledge` 的 ``_merge``）。内置条目一旦从
    ``builtin/*.md`` 删掉，这些 NULL 列就再也没有来源：``_merge`` 会对它抛
    ``RuntimeError``（「自定义知识条目字段不完整」），**整个知识库都读不出来**。
    而「最后那一版内置正文」在文件被删之后就再也读不到了——正文还随版本不断改写，
    所以只能在删除的同时把它冻进代码（:data:`FROZEN_BUILTIN_ENTRIES`）。

迁移做什么
    对每条「在冻结表里、但当前 :func:`~maa_api.agent.knowledge.load_builtins` 里
    已经不存在」的 id：内存里已有的那一行就地补全 —— ``kind`` 取冻结值（kind 不
    可覆盖，与 ``_merge`` 的口径一致），``title`` / ``when_to_use`` / ``body`` 里
    为 NULL 的列取冻结值，用户改过的列原样保留，``enabled`` 是 NOT NULL 列、维持
    原值。补全后这行就是一条字段完整的**自定义条目**：列表里 ``source == "custom"``，
    内容等于「冻结内容 + 用户的改动」，用户的知识没有因为上游删条目而丢。

    用户**没覆盖过**的被删条目在表里本来就没有行，迁移不会凭空造一条出来——「内置
    删掉」就是对所有人删掉，只有改过它的人值得留一份。

什么时候跑
    每次启动跑一次（装配在 lifespan 里，与建表、其它启动清理并列）。它只读
    ``agent_knowledge`` 全表，表最多几十行，没有额外开销。

幂等性怎么保证
    迁移过一次之后，那一行的 id 仍然会被候选判定选中（内置已删、冻结表里还在、
    表里也有行），但 :func:`_patch` 会先看「四个字段是否已经齐全」：齐全就不写库、
    不计入 ``migrated``。所以第二次运行是彻底的 no-op——连 ``updated_at`` 都不动
    （这些 id 进 ``skipped``）。

失败怎么办
    任何异常都不抛给启动路径：记一条 warning 并返回已有结果。迁移是尽力而为的
    修补，知识库读不出来不该拦住整个服务启动。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Literal, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.knowledge import (
    MAX_BODY_CHARS,
    MAX_TITLE_CHARS,
    MAX_WHEN_TO_USE_CHARS,
    BuiltinEntry,
)
from maa_api.db.models import AgentKnowledge, utcnow
from maa_api.db.repositories.agent_knowledge import AgentKnowledgeRepository

logger = logging.getLogger(__name__)

__all__ = ["FROZEN_BUILTIN_ENTRIES", "FrozenEntry", "migrate_deleted_overrides"]


class FrozenEntry(TypedDict):
    """一条被删内置条目的最后一版内容；``_merge`` 口径下能覆盖的全部四个字段。"""

    kind: Literal["playbook", "screen", "rule", "glossary", "profile"]
    title: str
    when_to_use: str
    body: str


#: 被删内置条目的最后一版内容：``id -> {kind, title, when_to_use, body}``。
#:
#: TODO(M30 收尾)：主 agent 在删除 ``builtin/*.md`` 条目时，把每一条的**最后一版**
#: 四个字段抄到这里。步骤：
#:
#: 1. ``git show <删除前的 commit>:maa_api/agent/knowledge/builtin/<id>.md``；
#: 2. 抄 frontmatter 的 ``kind`` / ``title`` / ``when_to_use``，正文抄 ``---`` 之后
#:    的全文（首尾空行去掉，与 ``_parse_builtin`` 的 ``body`` 一致）；
#: 3. 只放**真的被删掉**的 id：还在 ``builtin/*.md` 里的条目写进来会被
#:    :func:`migrate_deleted_overrides` 当作「内置仍在」跳过，白占地方。
#:
#: 格式（value 必须四个键齐全、都是非空字符串）::
#:
#:     FROZEN_BUILTIN_ENTRIES: dict[str, FrozenEntry] = {
#:         "operator-classes": {
#:             "kind": "rule",
#:             "title": "干员职业与分支",
#:             "when_to_use": "问干员职业、分支、特性时",
#:             "body": "……最后一版正文全文……",
#:         },
#:     }
#:
#: 冻结内容是启动路径上的数据：写坏的条目只会被跳过（记 warning），不会拦住启动。
FROZEN_BUILTIN_ENTRIES: dict[str, FrozenEntry] = {}


class _Candidate(TypedDict):
    """待迁移的一行：从库里读到的行 + 冻结内容。"""

    row: AgentKnowledge
    frozen: FrozenEntry


def _too_long(entry_id: str, frozen: Mapping[str, str]) -> bool:
    """冻结内容有没有超出列宽；超了就记 warning 并跳过这一条。

    只查列宽（写进去会报错的那种），**不查** ``kind`` 的取值域：这里唯一的任务是
    把用户的覆盖行补全成完整行，冻结的 kind 就算写歪了也仍然比「留一行残缺的自定义
    行、整个知识库读不出来」好，而且 kind 在 ``_merge`` 里本来就不可覆盖。
    """
    limits = {"title": MAX_TITLE_CHARS, "when_to_use": MAX_WHEN_TO_USE_CHARS}
    for field, limit in limits.items():
        if len(frozen[field]) > limit:
            logger.warning(
                "冻结的内置条目 %r 的 %s 有 %d 字，超出 %d 的列宽，这一条不迁移",
                entry_id,
                field,
                len(frozen[field]),
                limit,
            )
            return True
    if len(frozen["body"]) > MAX_BODY_CHARS:
        logger.warning(
            "冻结的内置条目 %r 的 body 有 %d 字，超出 %d 的列宽，这一条不迁移",
            entry_id,
            len(frozen["body"]),
            MAX_BODY_CHARS,
        )
        return True
    return False


def _known_frozen(
    frozen: Mapping[str, Mapping[str, str]],
) -> tuple[dict[str, FrozenEntry], list[str]]:
    """拆冻结表：``(可用的条目, 写坏了被丢弃的 id)``（坏的各记一条 warning）。

    在这里挡住坏数据：后面每个候选都假定 ``frozen`` 四键齐全、不超列宽。被丢弃的
    id 由调用方记进 ``skipped``——「冻结表里有、但这次迁移没管」正是 ``skipped``
    要回答的问题，启动日志里也才看得出是哪几条写坏了。
    """
    known: dict[str, FrozenEntry] = {}
    dropped: list[str] = []
    for entry_id, content in frozen.items():
        missing = [
            field
            for field in ("kind", "title", "when_to_use", "body")
            if not isinstance(content.get(field), str) or not content.get(field)
        ]
        if missing:
            logger.warning(
                "冻结的内置条目 %r 少字段 %s，这一条不迁移", entry_id, missing
            )
            dropped.append(entry_id)
            continue
        if _too_long(entry_id, content):
            dropped.append(entry_id)
            continue
        known[entry_id] = FrozenEntry(
            kind=content["kind"],  # type: ignore[typeddict-item] - 只保证是非空字符串
            title=content["title"],
            when_to_use=content["when_to_use"],
            body=content["body"],
        )
    return known, dropped


def _patch(entry_id: str, row: AgentKnowledge, frozen: FrozenEntry) -> bool:
    """把覆盖行就地补全成自定义条目；已经是完整行时返回 ``False``（幂等点）。

    只补 NULL 列，用户改过的列不动。``kind`` 一律取冻结值：覆盖行里恒为 NULL，
    上一版留下的历史行可能带着别的值，而 kind 从来不可覆盖（``_merge`` 的口径）。
    """
    if None not in (row.kind, row.title, row.when_to_use, row.body):
        # 已经是完整行（上一轮迁移过，或用户后来把它写全了）。
        return False
    row.kind = frozen["kind"]
    row.title = row.title if row.title is not None else frozen["title"]
    row.when_to_use = (
        row.when_to_use if row.when_to_use is not None else frozen["when_to_use"]
    )
    row.body = row.body if row.body is not None else frozen["body"]
    row.updated_at = utcnow()
    logger.info("知识条目 %r 的内置版本已删除，覆盖行迁移为自定义条目", entry_id)
    return True


async def migrate_deleted_overrides(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    frozen: Mapping[str, Mapping[str, str]] | None = None,
    builtins: Mapping[str, BuiltinEntry] | None = None,
) -> dict[str, list[str]]:
    """把「内置已删、但用户覆盖过」的行补全成自定义条目，返回 ``{migrated, skipped}``。

    ``frozen`` 默认取 :data:`FROZEN_BUILTIN_ENTRIES`；``builtins`` 默认取进程内唯一的
    内置表 ``builtin_entries()``（两者都只为测试注入，生产装配只传 ``session_factory``）。

    - ``migrated``：本次真的补全并写库的 id（幂等：第二次运行为空）；
    - ``skipped``：不需要迁移的 id —— 内置仍在（``builtins`` 里有它）、或用户从没
      覆盖过（表里没有这一行）、或已经是完整行（上一轮迁移过）。

    启动路径友好：任何异常都只记 warning，返回 ``{"migrated": [], "skipped": []}``，
    绝不向外抛。
    """
    summary: dict[str, list[str]] = {"migrated": [], "skipped": []}
    content = FROZEN_BUILTIN_ENTRIES if frozen is None else frozen
    if not content:
        return summary

    try:
        known, dropped = _known_frozen(content)
        # 延迟导入：``knowledge/__init__`` 会在装配时加载内置文件，导入期不该有副作用。
        from maa_api.agent.knowledge import builtin_entries

        current = builtin_entries() if builtins is None else builtins
        async with session_factory() as session:
            rows = {row.id: row for row in await AgentKnowledgeRepository(session).all()}
            for entry_id in [*known, *dropped]:
                if entry_id not in known or entry_id in current or entry_id not in rows:
                    summary["skipped"].append(entry_id)
            pending = {
                entry_id: _Candidate(row=rows[entry_id], frozen=known[entry_id])
                for entry_id in known
                if entry_id not in current and entry_id in rows
            }
            for entry_id, candidate in pending.items():
                if _patch(entry_id, candidate["row"], candidate["frozen"]):
                    summary["migrated"].append(entry_id)
                else:
                    summary["skipped"].append(entry_id)
            await session.commit()
    except Exception:  # noqa: BLE001 - 迁移失败不能拦住启动
        logger.warning("知识库覆盖行迁移失败，跳过本次迁移", exc_info=True)
        return {"migrated": [], "skipped": []}
    return summary
