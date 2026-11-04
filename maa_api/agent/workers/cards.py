"""Worker 与总控的卡片、收件箱渲染与落库（M70 规格 §4.5.9、§4.5.10）。

``worker_catalog_card`` 给总控列出可以建单的 Worker；``ticket_card`` / ``task_card``
分别是 Worker 与子代理的稳定角色卡。``render_inbox_item`` 优先复用 M69
``InboxService.render`` 的渲染规则，卡片层再补上工单种类的标题；同一函数的落库
版本 ``persist_inbox_messages`` 把本轮领取到的项转成 ``agent_message``。

这些卡片只依赖定义与工单的静态字段：同一代理、同一工单的正文逐字节不变，系统
提示的前三段因此可以命中前缀缓存（v3 06 §2）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.harness.events import RunEventBus
from maa_api.agent.harness.loop import MESSAGE_PERSISTED_EVENT
from maa_api.agent.specs.registry import AgentSpecError, builtin_spec
from maa_api.agent.specs.schema import AgentSpec
from maa_api.agent.tickets.inbox import InboxService
from maa_api.db.models import AgentInbox, AgentMessage, Mandate, Ticket, utcnow
from maa_api.db.repositories.agent import AgentMessageRepository, AgentRunRepository
from maa_api.domain.enums import InboxKind

__all__ = [
    "RISK_RANK",
    "effective_ceiling",
    "persist_inbox_messages",
    "render_inbox_item",
    "task_card",
    "ticket_card",
    "worker_catalog_card",
]

#: 风险上限由低到高（M70 §4.5.10）。
RISK_RANK: dict[str, int] = {"none": 0, "consume": 1, "destructive": 2}

_ORIGIN_LABELS: dict[str, str] = {
    "user": "用户",
    "trigger": "事件",
    "goal": "目标",
    "watchdog": "巡检",
    "system": "系统",
}
_STRENGTH_LABELS: dict[str, str] = {
    "explicit": "用户明确要求",
    "inferred": "系统推断",
}
_CEILING_LABELS: dict[str, str] = {
    "none": "无需确认：只能做不需要用户确认的操作，需要消耗或破坏时报 blocked，请用户另起工单",
    "consume": "消耗类：可以发起消耗类确认，不能发起破坏类",
    "destructive": "破坏类：消耗类与破坏类都可以发起，每次仍要用户在确认卡上确认",
}


def _ceiling_label(ceiling: str) -> str:
    try:
        return _CEILING_LABELS[ceiling]
    except KeyError:
        raise ValueError(f"未知的风险上限：{ceiling!r}") from None


def effective_ceiling(spec_ceiling: str, mandate_ceiling: str | None) -> str:
    """代理定义上限与委托上限取较低者；委托没有上限时以定义上限为准。"""
    if spec_ceiling not in RISK_RANK:
        raise ValueError(f"未知的风险上限：{spec_ceiling!r}")
    if mandate_ceiling is None:
        return spec_ceiling
    if mandate_ceiling not in RISK_RANK:
        raise ValueError(f"未知的风险上限：{mandate_ceiling!r}")
    return (
        mandate_ceiling
        if RISK_RANK[mandate_ceiling] < RISK_RANK[spec_ceiling]
        else spec_ceiling
    )


def worker_catalog_card(registry: Any) -> str:
    """总控的角色卡：列出非 legacy 的 ``worker`` 定义，按 name 排序。"""
    workers = sorted(
        (
            spec
            for spec in registry.list()
            if spec.kind == "worker" and not spec.name.startswith("legacy_")
        ),
        key=lambda spec: spec.name,
    )
    lines = ["## 可用的 Worker", "", "建工单时 kind 从下面选："]
    lines.extend(
        f"- {spec.name}（{spec.title}）：{spec.description}" for spec in workers
    )
    return "\n".join(lines)


def _definition(registry: Any, name: str) -> AgentSpec | None:
    """按名字取定义；注册表只提供 ``list`` 时退回线性查找。"""
    getter = getattr(registry, "get", None)
    if callable(getter):
        try:
            return getter(name)
        except (KeyError, AgentSpecError):
            return None
    for spec in registry.list():
        if spec.name == name:
            return spec
    return None


def ticket_card(ticket: Ticket, mandate: Mandate, spec: AgentSpec, registry: Any) -> str:
    """Worker 的角色卡：工单、风险上限、汇报要求与可委派的子代理。"""
    origin = _ORIGIN_LABELS.get(str(ticket.origin), str(ticket.origin))
    strength = _STRENGTH_LABELS.get(str(mandate.strength), str(mandate.strength))
    ceiling = effective_ceiling(spec.risk_ceiling, mandate.risk_ceiling)
    lines = [
        "## 工单卡",
        "",
        f"- 工单：「{ticket.title}」，ticket_id={ticket.id}",
        f"- 来源：{origin}；委托：{strength}",
        f"- 委托范围：{mandate.scope}",
        f"- 风险上限：{_ceiling_label(ceiling)}",
        "",
        "## 汇报",
        "",
        "每一轮都以一次 report 收尾，report 放在这一批工具调用的最后。status 这样选：",
        "- progress：还在推进，例如流水线已提交、在等结果。总控收不到它。",
        "- waiting：要用户回答或确认才能继续；human_action 写清要用户做什么。",
        "- ready：做完了，结果已经用工具核实；summary 写结论与关键数字。",
        "- blocked：靠你自己解决不了，例如超出委托范围、缺资源、工具返回 "
        "MANDATE_SCOPE_EXCEEDED；human_action 写清需要谁做什么，需要另开工单时写明开什么工单。",
        "- failed：确定做不成；summary 写原因。",
        "- dropped：用户要求放弃。",
        "summary 不超过 600 字，写结论，不写过程。在等流水线或事件时报 progress 结束本轮，"
        "不要空等；流水线结束后系统会用【事件】叫醒你。",
    ]
    if spec.delegates:
        lines.extend(
            [
                "",
                "## 可委派的子代理",
                "",
                "用 delegate 把一段独立的子任务交给下面的子代理。子代理看不到这个工单的对话："
                "task 写清目标与完成标准，context 写它需要知道的事实（关卡代号、流水线 id、"
                "已知数字）。互不依赖的只读委派放进同一批调用，会并行执行。",
            ]
        )
        for name in spec.delegates:
            target = _definition(registry, name)
            if target is None:
                continue
            lines.append(f"- {target.name}（{target.title}）：{target.description}")
    return "\n".join(lines)


def task_card(
    task: str, context: str | None, *, ticket_title: str, parent_title: str
) -> str:
    """子代理的任务卡：任务、已知事实与来源。"""
    facts = (context or "").strip() or "无"
    return "\n".join(
        [
            "## 任务卡",
            "",
            f"- 任务：{task}",
            f"- 已知事实：{facts}",
            f"- 来自：工单「{ticket_title}」的{parent_title}",
        ]
    )


#: ``InboxService.render`` 是纯函数，不读实例字段；这里直接按未绑定方法调用。
_BASE_RENDER = InboxService.render


def _ticket_kind_title(kind: str) -> str:
    """工单种类的中文标题；定义还没加载时退回种类名。"""
    if not kind:
        return kind
    try:
        return builtin_spec(kind).title
    except AgentSpecError:
        return kind


def render_inbox_item(item: AgentInbox) -> str | None:
    """把一条收件箱项转成消息正文（M70 §4.5.9）；``user_message`` 返回 ``None``。

    基础格式沿用 ``InboxService.render``；``【工单汇报】`` 里的种类在卡片层换成
    定义标题（例如 general → 通用执行），M69 的载荷只带种类名。
    """
    rendered = _BASE_RENDER(None, item)  # type: ignore[arg-type]
    if rendered is None or str(item.kind) != InboxKind.REPORT:
        return rendered
    payload = item.payload or {}
    kind = str(payload.get("ticket_kind") or payload.get("kind") or "")
    title = _ticket_kind_title(kind)
    if title and title != kind:
        rendered = rendered.replace(f"（{kind}，", f"（{title}，", 1)
    return rendered


async def persist_inbox_messages(
    session_factory: async_sessionmaker[AsyncSession],
    event_bus: RunEventBus | None,
    session_id: str,
    run_id: str,
    items: Sequence[AgentInbox],
) -> int:
    """把本轮领取的项落成 ``agent_message`` 并补发 ``message_persisted``。

    跳过 ``user_message``（它们的正文在写入时已经落库）；其余每项按 id 顺序渲染、
    追加一条 ``role='user'`` 的消息，``seq`` 用会话内 ``next_seq``。运行行的
    ``trigger_seq`` 写本轮最大 ``seq``（``user_message`` 取 ``ref_id``）。返回这个
    最大 ``seq``；没有可用的序号时返回 0。
    """
    ordered = sorted(items, key=lambda item: int(item.id or 0))
    max_seq: int | None = None
    persisted: list[tuple[int | None, int, int | None, str]] = []
    async with session_factory() as session:
        runs = AgentRunRepository(session)
        stored_run = await runs.get(run_id)
        run_data = (
            (
                stored_run.agent_name,
                stored_run.kind,
                stored_run.ticket_id,
            )
            if stored_run is not None
            else (None, None, None)
        )
        messages = AgentMessageRepository(session)
        for item in ordered:
            if str(item.kind) == InboxKind.USER_MESSAGE:
                ref_id = item.ref_id
                if ref_id is not None and str(ref_id).isdigit():
                    seq = int(ref_id)
                    max_seq = seq if max_seq is None else max(max_seq, seq)
                continue
            content = render_inbox_item(item)
            if content is None:
                continue
            seq = await messages.next_seq(session_id)
            message = await messages.create(
                AgentMessage(
                    session_id=session_id,
                    seq=seq,
                    role="user",
                    content=content,
                    run_id=run_id,
                    inbox_id=item.id,
                    created_at=utcnow(),
                )
            )
            max_seq = seq if max_seq is None else max(max_seq, seq)
            persisted.append((message.id, seq, item.id, content))
        if max_seq is not None:
            await runs.set_trigger_seq_if_missing(run_id, max_seq)
        await session.commit()

    if event_bus is not None:
        agent, kind, ticket_id = run_data
        for message_id, seq, inbox_id, content in persisted:
            await event_bus.emit(
                MESSAGE_PERSISTED_EVENT,
                {
                    "session_id": session_id,
                    "ticket_id": ticket_id,
                    "agent": agent,
                    "kind": kind,
                    "message": {
                        "id": message_id,
                        "seq": seq,
                        "role": "user",
                        "inbox_id": inbox_id,
                        "preview": content[:200],
                    },
                },
            )
    return max_seq if max_seq is not None else 0
