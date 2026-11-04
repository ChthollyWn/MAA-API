"""用户消息里 @ 提及的服务端展开（05 §2.5）：写入用户消息时附在正文后面。

五类提及各有自己的读取路径；``render_reference_block`` 负责去重、取前
:data:`MAX_MENTIONS` 条、单条截断，并把任何一条的读取失败折成
「{类型}「{label}」（读取失败）」——展开失败不阻塞发送。工单消息给总控的
passive 摘要由调用方传原文，不经过这个模块。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.db.models import Ticket
from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.domain.enums import TaskStatus
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "EXPANDERS",
    "MAX_ITEM_CHARS",
    "MAX_MENTIONS",
    "MentionContext",
    "MentionLike",
    "READ_FAILED",
    "REFERENCE_HEADER",
    "TYPE_LABELS",
    "expand_knowledge",
    "expand_operator",
    "expand_pipeline",
    "expand_stage",
    "expand_ticket",
    "render_reference_block",
]

logger = logging.getLogger(__name__)

#: 引用段的标题；正文与它之间空一行。
REFERENCE_HEADER = "【引用】"
#: 一次最多展开的提及条数（去重之后）。
MAX_MENTIONS = 8
#: 单条展开结果的上限；超过时截成 399 字加省略号。
MAX_ITEM_CHARS = 400
#: 读取失败时追加在标签后的说明。
READ_FAILED = "（读取失败）"
#: 提及类型 → 中文名；未知类型原样写。
TYPE_LABELS = {
    "ticket": "工单",
    "knowledge": "知识",
    "operator": "干员",
    "stage": "关卡",
    "pipeline": "流水线",
}


class MentionLike(Protocol):
    """请求模型里的 ``Mention`` 与测试替身共用的最小形状。"""

    type: str
    id: str
    label: str


@dataclass(frozen=True)
class MentionContext:
    """展开一条提及时能读到的运行时上下文（``request.app.state`` 与库会话工厂）。"""

    app_state: Any
    session_factory: async_sessionmaker[AsyncSession]


# ----------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------
def _field(source: Any, name: str, default: Any = None) -> Any:
    """兼容 Pydantic / ``SimpleNamespace`` / dict 三类提及与数据行。"""
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _text(value: Any) -> str:
    """``None`` 一律写「未知」；其余原样转字符串。"""
    return "未知" if value is None else str(value)


def _truncate(text: str) -> str:
    """超过 :data:`MAX_ITEM_CHARS` 时截成 399 字加 ``…``。"""
    if len(text) <= MAX_ITEM_CHARS:
        return text
    return text[: MAX_ITEM_CHARS - 1] + "…"


def _iso_z(value: datetime | None) -> str:
    """库里的 naive UTC 时间 → ``...Z``（与 REST 时间字段同口径）。"""
    if value is None:
        return "未知"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _masteries(value: Any) -> str:
    """专精映射 ``{"1":2,"2":0,"3":3}`` → ``2/0/3``；未知写「未知」。"""
    if value is None:
        return "未知"
    if not isinstance(value, Mapping):
        return str(value)
    return "/".join(str(value.get(slot, 0)) for slot in ("1", "2", "3"))


def _modules(value: Any) -> str:
    """模组映射 ``{"X":2}`` → ``X2``；``{}`` 写「无」；未知写「未知」。"""
    if value is None:
        return "未知"
    if not isinstance(value, Mapping):
        return str(value)
    if not value:
        return "无"
    return "/".join(f"{key}{value[key]}" for key in sorted(value, key=str))


def _owned(value: Any) -> str:
    """持有列的「是 / 否」；``None`` 是未知。"""
    if value is None:
        return "未知"
    return "是" if value else "否"


# ----------------------------------------------------------------------
# 五类展开
# ----------------------------------------------------------------------
async def expand_ticket(mention: MentionLike, ctx: MentionContext) -> str:
    """工单：标题、状态与最近一次汇报摘要。"""
    async with ctx.session_factory() as session:
        ticket_id = str(_field(mention, "id", ""))
        ticket = await session.get(Ticket, ticket_id)
        if ticket is None:
            raise LookupError(f"工单 {ticket_id} 不存在")
        summary = str(ticket.summary or "").strip() or "还没有汇报"
        return f"工单「{ticket.title}」（{ticket.status}）：{summary}"


async def expand_knowledge(mention: MentionLike, ctx: MentionContext) -> str:
    """知识：标题、id、使用时机与正文前 200 字（换行换成空格）。"""
    service = ctx.app_state.knowledge_service
    entry = await service.read_for_agent(str(_field(mention, "id", "")))
    body = str(_field(entry, "body", "") or "")
    excerpt = body[:200].replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return (
        f"知识「{_field(entry, 'title', '')}」（{_field(entry, 'id', '')}）："
        f"{_field(entry, 'when_to_use', '')}。正文节选：{excerpt}"
    )


async def expand_operator(mention: MentionLike, ctx: MentionContext) -> str:
    """干员：持有、练度与专精 / 模组；读不到时由调用方按错误码处理。"""
    data = await ctx.app_state.box_service.get_operator(
        str(_field(mention, "id", ""))
    )
    return (
        f"干员 {_text(_field(data, 'name'))}（{_text(_field(data, 'char_id'))}）："
        f"持有 {_owned(_field(data, 'own'))}，"
        f"精英化 {_text(_field(data, 'elite'))}，"
        f"等级 {_text(_field(data, 'level'))}，"
        f"潜能 {_text(_field(data, 'potential'))}，"
        f"技能等级 {_text(_field(data, 'skill_level'))}，"
        f"专精 {_masteries(_field(data, 'masteries'))}，"
        f"模组 {_modules(_field(data, 'modules'))}，"
        f"练度同步于 {_text(_field(data, 'recognized_at'))}"
    )


async def expand_stage(mention: MentionLike, ctx: MentionContext) -> str:
    """关卡：唯一命中写代号与 stageId，多候选写前三个 stage_id，空写找不到。"""
    label = str(_field(mention, "label", ""))
    resolver = getattr(ctx.app_state, "stage_resolver", None)
    if resolver is None:
        # 延迟 import：模块顶层拉 ``StageResolver`` 会把 ``maa_api.core`` 带进
        # ``import maa_api.main`` 的进程（M3 的 import 隔离用例禁止）。
        from maa_api.services.stage_resolver import StageResolver

        resolver = StageResolver(getattr(ctx.app_state, "core_registry", None))
    result = await resolver.resolve(str(_field(mention, "id", "")))
    total = int(result.get("candidates_total") or 0)
    candidates = list(result.get("candidates") or [])
    if total == 1:
        item = result.get("resolved") or (candidates[0] if candidates else {})
        return (
            f"关卡 {_text(_field(item, 'code'))}"
            f"（{_text(_field(item, 'name'))}，{_text(_field(item, 'stage_id'))}）"
        )
    if total > 1:
        ids = "、".join(str(_field(item, "stage_id")) for item in candidates[:3])
        return (
            f"关卡 {label}：有 {total} 个同名候选（{ids}），"
            "需要时用 stageId 再查"
        )
    return f"关卡 {label}：本地关卡索引里找不到"


async def expand_pipeline(mention: MentionLike, ctx: MentionContext) -> str:
    """流水线：标题、短 id、状态、任务完成数与创建时间。"""
    async with ctx.session_factory() as session:
        pipeline_id = str(_field(mention, "id", ""))
        pipeline = await PipelineRepository(session).get(
            pipeline_id, with_tasks=True
        )
        if pipeline is None:
            raise LookupError(f"流水线 {pipeline_id} 不存在")
        tasks = list(getattr(pipeline, "tasks", []) or [])
        done = sum(
            1
            for task in tasks
            if str(getattr(task, "status", "")) == TaskStatus.COMPLETED.value
        )
        title = str(pipeline.title) if pipeline.title else "「无标题」"
        line = (
            f"流水线 {title}（{str(pipeline.id)[:8]}，{pipeline.status}）："
            f"{done}/{pipeline.task_count} 个任务完成；"
            f"创建于 {_iso_z(pipeline.created_at)}"
        )
        if pipeline.error_message:
            line += f"；错误：{pipeline.error_message}"
        return line


EXPANDERS: dict[str, Callable[[MentionLike, MentionContext], Awaitable[str]]] = {
    "ticket": expand_ticket,
    "knowledge": expand_knowledge,
    "operator": expand_operator,
    "stage": expand_stage,
    "pipeline": expand_pipeline,
}


# ----------------------------------------------------------------------
# 引用段
# ----------------------------------------------------------------------
def _failure_line(mention_type: str, label: str) -> str:
    type_label = TYPE_LABELS.get(mention_type, mention_type)
    return f"{type_label}「{label}」{READ_FAILED}"


async def render_reference_block(
    mentions: Sequence[MentionLike] | None, ctx: MentionContext
) -> str:
    """按 (type, id) 去重、只展开前 8 个；返回引用段，没有提及返回空串。

    单个展开函数抛任何异常都写成「{类型}「{label}」（读取失败）」并记
    warning，不向外抛；``ACCOUNT_DATA_NOT_FOUND`` 是唯一特例。
    """
    if not mentions:
        return ""

    unique: list[MentionLike] = []
    seen: set[tuple[str, str]] = set()
    for mention in mentions:
        mention_type = str(_field(mention, "type", ""))
        mention_id = str(_field(mention, "id", ""))
        key = (mention_type, mention_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(mention)

    lines: list[str] = []
    for mention in unique[:MAX_MENTIONS]:
        mention_type = str(_field(mention, "type", ""))
        label = str(_field(mention, "label", ""))
        expander = EXPANDERS.get(mention_type)
        if expander is None:
            logger.warning("未知的提及类型 %s（%s），写入读取失败行", mention_type, label)
            lines.append(_failure_line(mention_type, label))
            continue
        try:
            text = await expander(mention, ctx)
        except AppError as exc:
            account_missing = getattr(exc, "code", None)
            if mention_type == "operator" and (
                account_missing is ErrorCode.ACCOUNT_DATA_NOT_FOUND
                or str(account_missing) == ErrorCode.ACCOUNT_DATA_NOT_FOUND.value
            ):
                lines.append(f"干员 {label}：账号数据里没有这名干员")
            else:
                logger.warning(
                    "展开 %s 提及 %s 失败，写入读取失败行",
                    mention_type,
                    _field(mention, "id", ""),
                    exc_info=True,
                )
                lines.append(_failure_line(mention_type, label))
        except Exception:  # noqa: BLE001 - 单条展开失败不能阻塞发送
            logger.warning(
                "展开 %s 提及 %s 失败，写入读取失败行",
                mention_type,
                _field(mention, "id", ""),
                exc_info=True,
            )
            lines.append(_failure_line(mention_type, label))
        else:
            lines.append(_truncate(text))

    omitted = len(unique) - MAX_MENTIONS
    if omitted > 0:
        lines.append(f"另有 {omitted} 条引用没有展开")

    return "\n\n" + REFERENCE_HEADER + "\n" + "\n".join(f"- {line}" for line in lines)
