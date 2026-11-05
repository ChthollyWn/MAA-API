"""六条内置事件规则与工单草稿（M81 规格 §4.2、M82 规格 §4.7、08 §3.2/§4.7）。

内置规则是代码里的常量，表 ``trigger_rule`` 只存用户对它们的覆盖与用户自建规则。
声明顺序即 08 §3.2 的顺序，也是 :meth:`RuleSet.match` 优先取内置规则的顺序。

两条诊断规则（``diagnose-pipeline-failure``、``diagnose-core-crash``）不研判：
``policy`` 为空串，命中即按 accept 建诊断工单，草稿复用 M73 诊断上下文
（``AgentIncidentService._pipeline_context`` / ``_core_context``），与 v2 自动诊断
看到的是同一份现场。

其余四条规则的 ``policy`` 是给 ``trigger_evaluator`` 的研判提示，全文照 §4.7。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from maa_api.agent.events.kinds import summarize
from maa_api.domain.enums import TriggerMode

if TYPE_CHECKING:  # pragma: no cover - 只给类型检查用，避免运行时循环导入
    from maa_api.agent.events.rules import EffectiveRule
    from maa_api.agent.llm.schemas.trigger_decision import TriggerDecision
    from maa_api.db.models import AgentTrigger

__all__ = [
    "BUILTIN_RULES",
    "BuiltinRule",
    "DraftBuilder",
    "DraftContext",
    "TicketDraft",
    "default_draft",
    "diagnose_core_draft",
    "diagnose_pipeline_draft",
    "goal_review_draft",
]

#: 工单草稿三段的上限（与 ``TicketService.create`` / ``attach_worker_session`` 一致）。
MAX_DRAFT_TITLE_CHARS = 64
MAX_DRAFT_BRIEF_CHARS = 4000
MAX_DRAFT_SCOPE_CHARS = 2000

#: 诊断工单的范围，与 v2 自动诊断同口径。
DIAGNOSE_SCOPE = "诊断这次失败：查日志、截图与任务状态，说明原因；只用不需要确认的恢复动作"
#: 诊断上下文说明的前缀；后面跟 ``_pipeline_context`` / ``_core_context`` 的 JSON。
DIAGNOSE_CONTEXT_HEADER = "【诊断上下文】"


@dataclass(frozen=True, slots=True)
class TicketDraft:
    """一条事件本来会建的工单。"""

    title: str
    brief: str
    scope: str


@dataclass(frozen=True, slots=True)
class DraftContext:
    """生成草稿需要的全部输入。"""

    trigger: "AgentTrigger"
    rule: "EffectiveRule"
    #: 不研判的规则为 ``None``。
    decision: "TriggerDecision | None"
    #: 取 ``app.state`` 上的服务（诊断草稿用 ``agent_incident_service``）。
    app: Any


DraftBuilder = Callable[[DraftContext], Awaitable[TicketDraft]]


@dataclass(frozen=True, slots=True)
class BuiltinRule:
    """一条写在代码里的内置事件规则。"""

    id: str
    title: str
    source: str | None
    kind_pattern: str
    conditions: Mapping[str, Any]
    mode: TriggerMode
    worker: str
    #: 为空串表示不研判：命中即按 accept 处理。
    policy: str
    cooldown_seconds: int
    #: 指向 ``settings`` 上的总开关；为假时生效档位按 off 处理。
    gate_setting: str | None = None
    #: 不研判的规则自带草稿；为空时用 :func:`default_draft`。
    draft: DraftBuilder | None = None


def _truncate(text: str, limit: int) -> str:
    return text[:limit]


def _json(value: Any, limit: int | None = None) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = "{}"
    return text if limit is None else text[:limit]


async def default_draft(ctx: DraftContext) -> TicketDraft:
    """研判通过（或规则不研判）时按研判结果生成草稿，缺失的部分用事件信息补。"""
    trigger = ctx.trigger
    rule = ctx.rule
    decision = ctx.decision
    summary = summarize(trigger.kind, trigger.payload)

    title = (
        decision.title
        if decision is not None and decision.title
        else f"{rule.title}：{summary}"
    )

    if decision is not None and decision.brief:
        brief = decision.brief
    else:
        lines = [
            f"事件规则「{rule.title}」命中了一条事件。",
            f"摘要：{summary}",
            f"话题：{trigger.thread_key}",
            f"payload：{_json(trigger.payload, 3000)}",
        ]
        if rule.policy:
            lines.append(f"研判提示：{rule.policy}")
        brief = "\n".join(lines)

    if decision is not None and decision.scope:
        scope = decision.scope
    else:
        scope = (
            f"处理事件 #{trigger.id}（{summary}）：调查并汇报，只做不需要确认的操作"
        )

    return TicketDraft(
        title=_truncate(title, MAX_DRAFT_TITLE_CHARS),
        brief=_truncate(brief, MAX_DRAFT_BRIEF_CHARS),
        scope=_truncate(scope, MAX_DRAFT_SCOPE_CHARS),
    )


def _incident_service(app: Any) -> Any:
    """取 M73 的诊断适配器；测试或启动早期可能没有，取不到就只用 payload。"""
    return getattr(getattr(app, "state", None), "agent_incident_service", None)


def _context_brief(context: Mapping[str, Any]) -> str:
    """说明的第一段是诊断上下文 JSON，M73 的现场字段原样带过去。"""
    return f"{DIAGNOSE_CONTEXT_HEADER}\n{_json(context)}"


def _pipeline_id(trigger: "AgentTrigger") -> str:
    explicit = trigger.payload.get("pipeline_id")
    if explicit:
        return str(explicit)
    thread_key = trigger.thread_key
    return thread_key.split(":", 1)[1] if ":" in thread_key else thread_key


async def diagnose_pipeline_draft(ctx: DraftContext) -> TicketDraft:
    """流水线失败的诊断草稿：标题带流水线名，说明带 M73 的失败现场。"""
    trigger = ctx.trigger
    pipeline_id = _pipeline_id(trigger)
    service = _incident_service(ctx.app)
    context: Mapping[str, Any] | None = None
    if service is not None:
        context = await service._pipeline_context(pipeline_id)
    if context is None:
        # 流水线行已不存在（例如已被清理）时仍给一张可读的工单。
        context = {
            "incident_type": "pipeline_failure",
            "failure_object": {
                "pipeline_id": pipeline_id,
                "title": trigger.payload.get("title"),
                "status": trigger.payload.get("status"),
                "error_code": trigger.payload.get("error_code"),
                "summary": trigger.payload.get("error_message"),
            },
        }
    failure = context.get("failure_object") or {}
    name = failure.get("title") or trigger.payload.get("title") or pipeline_id
    return TicketDraft(
        title=_truncate(f"诊断：流水线「{name}」失败", MAX_DRAFT_TITLE_CHARS),
        brief=_truncate(_context_brief(context), MAX_DRAFT_BRIEF_CHARS),
        scope=DIAGNOSE_SCOPE,
    )


def _crash_record(trigger: "AgentTrigger") -> dict[str, Any]:
    """把 ``core.crashed`` 的 payload 折成 ``_core_context`` 认的 record。"""
    keys = ("reason", "exitcode", "attempt", "pid", "at", "detail", "recent_events")
    return {key: trigger.payload[key] for key in keys if key in trigger.payload}


async def diagnose_core_draft(ctx: DraftContext) -> TicketDraft:
    """空闲内核崩溃的诊断草稿：标题带代数，说明带 M73 的崩溃现场。"""
    trigger = ctx.trigger
    generation = trigger.payload.get("generation")
    if generation is None and ":" in trigger.thread_key:
        generation = trigger.thread_key.rsplit(":", 1)[-1]
    generation = int(generation) if generation is not None else 0
    service = _incident_service(ctx.app)
    context: Mapping[str, Any] | None = None
    if service is not None:
        context = await service._core_context(generation, _crash_record(trigger))
    if context is None:
        context = {
            "incident_type": "idle_core_crash",
            "failure_object": {
                "core_id": trigger.payload.get("core_id"),
                "generation": generation,
                "status": "crashed",
                "summary": _crash_record(trigger),
            },
        }
    return TicketDraft(
        title=_truncate(f"诊断：内核第 {generation} 代崩溃", MAX_DRAFT_TITLE_CHARS),
        brief=_truncate(_context_brief(context), MAX_DRAFT_BRIEF_CHARS),
        scope=DIAGNOSE_SCOPE,
    )


#: 三条研判规则的提示全文，逐字照 08 §4.7（与 08 §3.2 的要点一致）。
SANITY_FULL_POLICY = (
    "理智估计已满。没有排队或运行中的流水线、接下来一小时没有会用理智的定时任务时 accept："
    "建议提交一条只用现有理智的刷图流水线，关卡沿用最近一次刷的关卡，不用理智药，不碎石。"
    "其余情况 ignore。"
)
DEVICE_UNAVAILABLE_POLICY = (
    "设备重连多次都失败。有排队的流水线、或一小时内会到点的定时任务时 accept："
    "建议建运维工单检查设备与模拟器，只做重连设备、查设备状态这类不需要确认的操作，"
    "查不出原因就在汇报里请用户处理模拟器。没有待执行的工作时 ignore。"
)
UPDATE_AVAILABLE_POLICY = (
    "内核或资源有新版本。没有流水线在运行时 accept："
    "建议建运维工单读取更新说明，在汇报里列出要点并请用户决定是否更新；"
    "更新本身需要确认，这张工单不要尝试更新。有流水线在运行时 ignore。"
)

#: 复盘规则的研判提示，逐字照 M82 规格 §4.7。
GOAL_REVIEW_POLICY = (
    "这是一个目标的定时复盘，payload.snapshot 是程序算好的进度。\n"
    "进度正常、没有新逾期的里程碑、养成计划在跑、定时任务都在时 ignore。\n"
    "有新逾期的里程碑、养成计划已完成或已取消、定时任务被删或停用、截止前不到 3 天而里程碑没过半时 accept：\n"
    "建复盘工单，brief 写清哪里不对，scope 写「复盘目标「标题」并调整为它建立的计划与定时任务，只做不需要确认的操作」。"
)

#: 复盘草稿的固定一段：推断委托边界与 ``details.goal_review`` 格式（M82 §4.7）。
GOAL_REVIEW_BRIEF_TAIL = (
    "这是推断委托的复盘工单：只能做不需要确认的操作（查询、重算、建或停用不用理智药与源石的定时任务）；需要用户决定的事写进汇报的 human_action。\n"
    "目标 id：{goal_id}。完成后 report 汇报 ready，details 写：\n"
    '{"goal_review": {"milestones": [{"ord": 1, "status": "done", "note": "…"}], "summary": "…", "goal_status": null}}\n'
    '目标已经全部达成时 goal_status 写 "done"。没有需要告诉用户的变化时汇报 dropped。'
)

#: 研判没给范围时复盘工单的默认范围。
GOAL_REVIEW_SCOPE = (
    "复盘目标「{title}」并调整为它建立的计划与定时任务，只做不需要确认的操作"
)

#: 两条诊断规则的总开关（v2 键，默认开）。
DIAGNOSE_GATE_SETTING = "agent.auto_diagnose_failures"


async def goal_review_draft(ctx: DraftContext) -> TicketDraft:
    """复盘草稿：研判的标题/说明/范围，末尾附固定的一段汇报格式（M82 §4.7）。"""
    trigger = ctx.trigger
    payload = trigger.payload
    decision = ctx.decision
    goal_title = str(payload.get("title") or "未知")

    title = (
        decision.title
        if decision is not None and decision.title
        else f"复盘：{goal_title}"
    )
    if decision is not None and decision.brief:
        brief = decision.brief
    else:
        # 研判没给说明（或规则不研判）时用事件摘要与程序算好的快照。
        brief = (
            f"{summarize(trigger.kind, payload)}\n"
            f"复盘快照：{_json(payload.get('snapshot'), 3000)}"
        )
    tail = GOAL_REVIEW_BRIEF_TAIL.replace(
        "{goal_id}", str(payload.get("goal_id") or "未知")
    )
    scope = (
        decision.scope
        if decision is not None and decision.scope
        else GOAL_REVIEW_SCOPE.format(title=goal_title)
    )
    # 固定的汇报格式必须留在末尾：先给前文让出位置，再拼尾巴。
    separator = "\n\n"
    lead_limit = max(0, MAX_DRAFT_BRIEF_CHARS - len(tail) - len(separator))
    brief = _truncate(brief, lead_limit)
    return TicketDraft(
        title=_truncate(title, MAX_DRAFT_TITLE_CHARS),
        brief=_truncate(f"{brief}{separator}{tail}", MAX_DRAFT_BRIEF_CHARS),
        scope=_truncate(scope, MAX_DRAFT_SCOPE_CHARS),
    )


#: 08 §3.2 中里程碑为 M81 的五条与 M82 的一条，顺序即声明顺序。
BUILTIN_RULES: tuple[BuiltinRule, ...] = (
    BuiltinRule(
        id="diagnose-pipeline-failure",
        title="诊断流水线失败",
        source="pipeline",
        kind_pattern="pipeline.failed",
        conditions={},
        mode=TriggerMode.AUTO,
        worker="diagnose",
        policy="",
        cooldown_seconds=0,
        gate_setting=DIAGNOSE_GATE_SETTING,
        draft=diagnose_pipeline_draft,
    ),
    BuiltinRule(
        id="diagnose-core-crash",
        title="诊断内核崩溃",
        source="core",
        kind_pattern="core.crashed",
        conditions={},
        mode=TriggerMode.AUTO,
        worker="diagnose",
        policy="",
        cooldown_seconds=0,
        gate_setting=DIAGNOSE_GATE_SETTING,
        draft=diagnose_core_draft,
    ),
    BuiltinRule(
        id="sanity-full",
        title="理智已满",
        source="system",
        kind_pattern="system.sanity_full",
        conditions={},
        mode=TriggerMode.OBSERVE,
        worker="daily_ops",
        policy=SANITY_FULL_POLICY,
        cooldown_seconds=21600,
    ),
    BuiltinRule(
        id="device-unavailable",
        title="设备不可用",
        source="device",
        kind_pattern="device.unavailable",
        conditions={},
        mode=TriggerMode.OBSERVE,
        worker="ops",
        policy=DEVICE_UNAVAILABLE_POLICY,
        cooldown_seconds=3600,
    ),
    BuiltinRule(
        id="update-available",
        title="检查更新",
        source="system",
        kind_pattern="system.update_available",
        conditions={"target": ["core", "resource"]},
        mode=TriggerMode.OBSERVE,
        worker="ops",
        policy=UPDATE_AVAILABLE_POLICY,
        cooldown_seconds=86400,
    ),
    BuiltinRule(
        id="goal-review",
        title="目标复盘",
        source="goal",
        kind_pattern="goal.review_due",
        conditions={},
        mode=TriggerMode.AUTO,
        worker="planner",
        policy=GOAL_REVIEW_POLICY,
        cooldown_seconds=0,
        draft=goal_review_draft,
    ),
)
