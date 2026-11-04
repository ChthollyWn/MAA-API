"""M79 T2：从工单终态或压缩段抽取候选记忆（spec §4.2、§4.5、§7）。

两条调用路径共用本模块：

- 工单进入 ``done`` / ``failed`` / ``cancelled`` 后，``TicketService`` 的结束监听者
  把 :func:`extract_candidates` 放进后台任务（规格 §8：接口先返回，失败只记日志）。
- 总控生成压缩检查点后，:class:`~maa_api.agent.context.compaction.Compactor` 把被
  压缩掉的消息段作为 ``archive`` 传进来，只抽用户消息与总控回答。

抽取本身是一轮后台运行（``agent_run.kind=memory_extract``、代理 ``memory_curator``、
``model_role=memory``）：输入里带工单、汇报、过程摘录、已生效记忆（带 id，供标注
取代关系）与用户档案全文；输出走 ``memory_proposals`` schema。失败（模型报错、两次
都不合 schema、运行起不来）只记日志并返回空列表，绝不把异常抛给终态提交或压缩流
程。候选写入统一走 :meth:`MemoryService.create_candidate`，去重、500 字上限、
``supersedes`` 白名单与事件/索引副作用都在服务里，本模块不重复。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.knowledge import PROFILE_ENTRY_ID
from maa_api.agent.llm.structured import complete_structured
from maa_api.agent.memory.service import MemoryService
from maa_api.agent.specs.registry import builtin_spec
from maa_api.db.models import (
    AgentMessage,
    Mandate,
    MemoryItem,
    Ticket,
    TicketReport,
)
from maa_api.db.repositories.agent import AgentMessageRepository
from maa_api.db.repositories.ticket import (
    MandateRepository,
    TicketReportRepository,
    TicketRepository,
)
from maa_api.domain.enums import AgentRunKind, AgentRunStatus
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import get_settings

logger = logging.getLogger(__name__)

__all__ = [
    "CURATOR_AGENT",
    "CURATOR_SCHEMA",
    "MAX_ACTIVE_MEMORIES",
    "MAX_ARCHIVE_CHARS",
    "MAX_MESSAGE_CHARS",
    "MAX_TOOL_RESULT_CHARS",
    "SUBJECT_KEYS",
    "extract_candidates",
]

#: 后台代理名与输出 schema 名（规格 §4.5、§4.7）。
CURATOR_AGENT = "memory_curator"
CURATOR_SCHEMA = "memory_proposals"

#: ``subject`` 的分类键（07 §5.1），写进输入让 curator 有键可用。
SUBJECT_KEYS = (
    "preference、account、stage:<关卡>、operator:<名字>、"
    "infrast、recruit、schedule、device、copilot"
)
#: 单条消息进输入的截断长度；与 07 §5.2 的「过程摘录」粒度一致。
MAX_MESSAGE_CHARS = 1000
#: 工具结果只留关键片段，避免把整页日志塞进输入。
MAX_TOOL_RESULT_CHARS = 500
#: 压缩段摘录的总长度上限；超出时保留最近的部分。
MAX_ARCHIVE_CHARS = 12_000
#: 一次抽取最多带多少条已生效记忆（同时是 ``supersedes`` 白名单）。
MAX_ACTIVE_MEMORIES = 100
#: 一个会话最多读多少条消息做摘录。
MAX_SESSION_MESSAGES = 80
#: 工单汇报最多带多少条。
MAX_REPORTS = 20
#: 工单 ``result`` 摘录的 JSON 长度上限。
MAX_RESULT_CHARS = 1200

#: ``settings.memory`` 段不在时按规格默认值处理（M79 G3 注册 Settings）。
_DEFAULT_AUTO_EXTRACT = True


def _content_text(value: Any) -> str:
    """消息正文归一成文本：字符串原样，多模态数组取 text 部分之和。"""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        parts: list[str] = []
        for part in value:
            if isinstance(part, Mapping) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
        return "".join(parts)
    return ""


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _field(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _auto_extract_enabled(settings_provider: Callable[[], Any]) -> bool:
    settings = settings_provider()
    memory = getattr(settings, "memory", None)
    return bool(getattr(memory, "auto_extract", _DEFAULT_AUTO_EXTRACT))


def _render_ticket(
    ticket: Ticket,
    mandate: Mandate | None,
    reports: Sequence[TicketReport],
) -> str:
    """工单头、委托范围与全部汇报摘要（07 §5.2 的前三项）。"""
    lines = [
        "【工单】",
        f"标题：{ticket.title}",
        f"种类：{ticket.kind}；终态：{ticket.status}",
    ]
    if mandate is not None:
        lines.append(f"委托范围：{mandate.scope}")
    if reports:
        lines.append("【汇报】")
        for report in reports[-MAX_REPORTS:]:
            lines.append(
                f"- [{report.status}] {_clip(str(report.summary), MAX_MESSAGE_CHARS)}"
            )
    result = ticket.result or {}
    if result:
        try:
            rendered = json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):  # pragma: no cover - 结果本应可序列化
            rendered = str(result)
        lines.append(f"【结果关键字段】{_clip(rendered, MAX_RESULT_CHARS)}")
    return "\n".join(lines)


def _render_messages(
    messages: Sequence[AgentMessage], *, include_tools: bool
) -> str:
    """过程摘录：用户原话、回答与（可选的）关键工具结果。"""
    lines: list[str] = []
    selected = list(messages)[-MAX_SESSION_MESSAGES:]
    for message in selected:
        role = str(_field(message, "role") or "")
        text = _content_text(_field(message, "content"))
        if not text:
            continue
        if role == "user":
            lines.append(f"[用户] {_clip(text, MAX_MESSAGE_CHARS)}")
        elif role == "assistant":
            lines.append(f"[回答] {_clip(text, MAX_MESSAGE_CHARS)}")
        elif role == "tool" and include_tools:
            name = str(_field(message, "tool_name") or "").strip()
            label = f"工具结果 {name}" if name else "工具结果"
            lines.append(f"[{label}] {_clip(text, MAX_TOOL_RESULT_CHARS)}")
    return "\n".join(lines)


def _render_archive(archive: Sequence[Any]) -> str:
    """压缩段：只保留用户消息与总控回答（规格 §4.2）。"""
    lines: list[str] = []
    for message in archive:
        role = str(_field(message, "role") or "")
        if role not in ("user", "assistant"):
            continue
        text = _content_text(_field(message, "content"))
        if not text:
            continue
        label = "用户" if role == "user" else "回答"
        lines.append(f"[{label}] {_clip(text, MAX_MESSAGE_CHARS)}")
    text = "\n".join(lines)
    if len(text) > MAX_ARCHIVE_CHARS:
        # 压缩段本身已受窗口约束；真超限时保留离现在最近的一段。
        text = text[-MAX_ARCHIVE_CHARS:]
    return text


def _render_active_memories(memories: Sequence[MemoryItem]) -> str:
    if not memories:
        return "无"
    return "\n".join(
        f"- id={memory.id}｜kind={memory.kind}｜subject={memory.subject or '无'}"
        f"｜{_clip(str(memory.content), 500)}"
        for memory in memories
    )


def _render_evidence(
    *,
    ticket_text: str,
    archive_text: str,
    session_texts: Sequence[str],
    memories: Sequence[MemoryItem],
    profile_text: str | None,
) -> str:
    """把各节拼成 curator 的一条 user 消息。"""
    blocks: list[str] = [
        "下面是要整理的材料。只根据材料抽取候选记忆，不要补充材料里没有的内容。"
    ]
    if ticket_text:
        blocks.append(ticket_text)
    if archive_text:
        blocks.append(f"【压缩段：用户消息与总控回答】\n{archive_text}")
    if session_texts:
        blocks.append("【过程摘录】\n" + "\n\n".join(session_texts))
    blocks.append(
        "【已生效记忆】（supersedes 只能填下面这些 id；不要重复这里已有的内容）\n"
        + _render_active_memories(memories)
    )
    blocks.append(
        "【用户档案】（不要重复档案里已经有的内容）\n"
        + (profile_text.strip() if profile_text and profile_text.strip() else "无")
    )
    blocks.append(
        "【分类键】subject 用这些之一：" + SUBJECT_KEYS + "。"
    )
    blocks.append(
        "【输出】按 schema 输出 {\"items\": [...]}；没有值得记的就输出空数组。"
    )
    return "\n\n".join(blocks)


async def _read_profile(knowledge_service: Any) -> str | None:
    """读 ``agent-profile`` 条目全文；没装配、条目不存在或读取失败都按无档案处理。"""
    if knowledge_service is None:
        return None
    try:
        entry = await knowledge_service.read_for_agent(PROFILE_ENTRY_ID)
    except AppError as exc:
        if exc.code is not ErrorCode.KNOWLEDGE_NOT_FOUND:
            logger.warning("记忆抽取读取用户档案失败", exc_info=True)
        return None
    except Exception:  # noqa: BLE001 - 档案读不到不能阻断抽取
        logger.warning("记忆抽取读取用户档案失败", exc_info=True)
        return None
    return str(getattr(entry, "body", "") or "")


async def _load_ticket_material(
    session_factory: async_sessionmaker[AsyncSession],
    ticket_id: str,
) -> tuple[Ticket, Mandate | None, list[TicketReport]] | None:
    async with session_factory() as session:
        ticket = await TicketRepository(session).get(ticket_id)
        if ticket is None:
            return None
        mandate = await MandateRepository(session).get_by_ticket(ticket_id)
        page = await TicketReportRepository(session).list(
            ticket_id, page=1, size=MAX_REPORTS
        )
        reports = list(reversed(page.items))
    return ticket, mandate, reports


async def _load_session_texts(
    session_factory: async_sessionmaker[AsyncSession],
    session_ids: Sequence[str],
) -> list[str]:
    """按会话读过程摘录；同一工单的 Worker 与总控会话各一节。"""
    texts: list[str] = []
    async with session_factory() as session:
        repository = AgentMessageRepository(session)
        for identifier in dict.fromkeys(str(item) for item in session_ids if item):
            messages = await repository.list_by_session(identifier)
            text = _render_messages(messages, include_tools=True)
            if text:
                texts.append(text)
    return texts


def _curator_spec(spec_registry: Any) -> Any:
    if spec_registry is not None:
        try:
            return spec_registry.get(CURATOR_AGENT)
        except Exception:  # noqa: BLE001 - 注册表里没有时退回内置定义
            pass
    return builtin_spec(CURATOR_AGENT)


async def _start_run(
    run_service: Any,
    *,
    spec: Any,
    ticket_id: str | None,
    session_id: str | None,
    parent_run_id: str | None,
    trigger: str,
) -> str | None:
    if run_service is None:
        return None
    run = await run_service.start(
        agent_name=CURATOR_AGENT,
        kind=AgentRunKind.MEMORY_EXTRACT,
        # 后台运行不占会话：压缩时总控的回合还在跑，占用会话会撞活动运行唯一索引。
        session_id=None,
        parent_run_id=parent_run_id,
        spec_version=getattr(spec, "spec_version", None),
        ticket_id=ticket_id,
        meta={"target_session_id": session_id, "trigger": trigger},
    )
    return str(getattr(run, "id", None) or "") or None


async def _finish_run(
    run_service: Any,
    run_id: str | None,
    *,
    ok: bool,
    error: BaseException | None = None,
) -> None:
    """收尾抽取运行；运行收尾失败只记日志，不影响候选与调用方。"""
    if run_service is None or run_id is None:
        return
    error_code: str | None = None
    if not ok:
        code = getattr(error, "code", None)
        error_code = str(code) if code is not None else str(ErrorCode.INTERNAL_ERROR)
    try:
        await run_service.finish(
            run_id,
            status=AgentRunStatus.SUCCEEDED if ok else AgentRunStatus.FAILED,
            finish_reason="stop" if ok else "error",
            error_code=error_code,
        )
    except Exception:  # noqa: BLE001 - 运行收尾失败不能改变抽取结果
        logger.warning("记忆抽取运行收尾失败：run=%s", run_id, exc_info=True)


async def extract_candidates(
    memory_service: MemoryService,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    router: Any = None,
    ledger: Any = None,
    run_service: Any = None,
    ticket_id: str | None = None,
    session_id: str | None = None,
    parent_run_id: str | None = None,
    archive: Sequence[Any] | None = None,
    knowledge_service: Any = None,
    spec_registry: Any = None,
    settings_provider: Callable[[], Any] = get_settings,
    trigger: str = "ticket",
) -> list[MemoryItem]:
    """抽取一次候选记忆；任何失败都只记日志并返回 ``[]``。

    ``archive`` 给出时（压缩后抽取）直接用它当过程摘录，只取用户与助手消息；
    否则按 ``ticket_id`` 的 Worker / 总控会话读消息。``session_id`` 为空时依次用
    工单的 Worker 会话与总控会话（规格 §4.5）。
    """
    if router is None or ledger is None:
        logger.warning("记忆抽取缺少模型路由或账本，跳过")
        return []
    if memory_service.memory_disabled():
        return []
    if not _auto_extract_enabled(settings_provider):
        return []

    # 1. 材料：工单、汇报、过程摘录、已生效记忆与用户档案。
    try:
        ticket_material = (
            await _load_ticket_material(session_factory, ticket_id)
            if ticket_id is not None
            else None
        )
    except Exception:  # noqa: BLE001 - 读不到材料就放弃这一次抽取
        logger.warning("记忆抽取读取工单失败：ticket=%s", ticket_id, exc_info=True)
        return []
    if ticket_id is not None and ticket_material is None:
        logger.warning("记忆抽取找不到工单，跳过：%s", ticket_id)
        return []

    ticket: Ticket | None = None
    mandate: Mandate | None = None
    reports: list[TicketReport] = []
    if ticket_material is not None:
        ticket, mandate, reports = ticket_material
    chosen_session = (
        session_id
        or (ticket.worker_session_id if ticket is not None else None)
        or (ticket.orchestrator_session_id if ticket is not None else None)
    )

    try:
        active_memories = (
            await memory_service.list_memories(
                status="active", page=1, size=MAX_ACTIVE_MEMORIES
            )
        ).items
    except Exception:  # noqa: BLE001 - 没有记忆清单时仍可抽取，只是不能标注取代
        logger.warning("记忆抽取读取已生效记忆失败", exc_info=True)
        active_memories = []

    archive_text = _render_archive(archive) if archive is not None else ""
    session_texts: list[str] = []
    if archive is None:
        session_ids = [
            identifier
            for identifier in (
                ticket.worker_session_id if ticket is not None else None,
                ticket.orchestrator_session_id if ticket is not None else None,
                chosen_session,
            )
            if identifier
        ]
        try:
            session_texts = await _load_session_texts(session_factory, session_ids)
        except Exception:  # noqa: BLE001 - 过程摘录读不到时退化为只看工单
            logger.warning("记忆抽取读取会话消息失败", exc_info=True)

    profile_text = await _read_profile(knowledge_service)
    ticket_text = (
        _render_ticket(ticket, mandate, reports) if ticket is not None else ""
    )
    prompt = _render_evidence(
        ticket_text=ticket_text,
        archive_text=archive_text,
        session_texts=session_texts,
        memories=active_memories,
        profile_text=profile_text,
    )

    # 2. 起一轮 memory_extract 运行。
    try:
        spec = _curator_spec(spec_registry)
        run_id = await _start_run(
            run_service,
            spec=spec,
            ticket_id=ticket_id,
            session_id=chosen_session,
            parent_run_id=parent_run_id,
            trigger=trigger,
        )
    except Exception:  # noqa: BLE001 - 运行起不来就不抽，失败只记日志
        logger.warning("记忆抽取运行未能启动", exc_info=True)
        return []

    # 3. 结构化输出；complete_structured 自己会重试一次，仍不合法就抛错。
    try:
        result = await complete_structured(
            router=router,
            ledger=ledger,
            role=str(getattr(spec, "model_role", None) or "memory"),
            schema_name=CURATOR_SCHEMA,
            messages=[
                {"role": "system", "content": spec.body},
                {"role": "user", "content": prompt},
            ],
            agent_name=CURATOR_AGENT,
            run_id=run_id,
            session_id=chosen_session,
        )
    except Exception as exc:  # noqa: BLE001 - 抽取失败不改变工单终态与压缩结果
        logger.warning(
            "记忆抽取失败：ticket=%s session=%s",
            ticket_id,
            chosen_session,
            exc_info=True,
        )
        await _finish_run(run_service, run_id, ok=False, error=exc)
        return []
    await _finish_run(run_service, run_id, ok=True)

    # 4. 写候选；去重、500 字上限与 supersedes 白名单都由服务处理。
    allowed_supersedes = {str(memory.id) for memory in active_memories}
    stored: list[MemoryItem] = []
    for proposal in getattr(result.value, "items", ()) or ():
        try:
            item = await memory_service.create_candidate(
                kind=getattr(proposal, "kind", ""),
                subject=getattr(proposal, "subject", None),
                content=getattr(proposal, "content", ""),
                confidence=getattr(proposal, "confidence", None),
                source_ticket_id=ticket_id,
                source_session_id=chosen_session,
                source_run_id=run_id,
                supersedes_id=getattr(proposal, "supersedes", None),
                allowed_supersedes=allowed_supersedes,
            )
        except Exception:  # noqa: BLE001 - 单条候选写不进去不影响其余候选
            logger.warning(
                "候选记忆写入失败：ticket=%s", ticket_id, exc_info=True
            )
            continue
        if item is not None:
            stored.append(item)
    return stored
