"""Parameter-aware confirmation and session authorization policy for agent tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from maa_api.agent.preconditions import missing_start_up_warning
from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRisk
from maa_api.db.repositories.agent import AgentSessionRepository
from maa_api.db.repositories.audit import ConfirmationRepository
from maa_api.db.repositories.pipeline import TaskRepository
from maa_api.domain.enums import AgentSessionStatus, CallerType, RiskLevel
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.domain.task import RISK_FIELDS, TASK_MODELS

DEFAULT_ATOMIC_GRANT_MINUTES = 15
MAX_ATOMIC_GRANT_MINUTES = 60

#: 计划预演失败时写进确认 payload 的短字符串上限（规格 §7.7：失败只降级，不挡确认）。
PLAN_PREVIEW_ERROR_LIMIT = 200
#: 计划预演的提供者，形状与 ``UpgradePlanService.preview`` 一致：
#: ``await provider(name=..., targets=..., run_times=..., context=...)``。
PlanPreviewProvider = Callable[..., Awaitable[Mapping[str, Any]]]

#: 运行中改参数时无法确定任务类型与现有参数（拿不到持久化行）时的确认理由。
CONSUMPTION_UNDETERMINED_REASON = (
    "set_task_params 无法确定运行中任务的类型与现有参数，按保守策略要求确认"
)

ATOMIC_TOOL_NAMES = frozenset(
    {"click", "swipe", "long_press", "input_text", "key_event"}
)
#: 每次调用都要人工确认的账号写入（规格 §7.4、§7.7）：风险是 NONE，但要用户看见改动。
ACCOUNT_WRITE_TOOLS = frozenset(
    {
        "start_upgrade_plan",
        "update_user_profile",
        # M42：森空岛同步会覆盖整表练度（M47 起是干员数据的唯一来源），每次都要用户看见。
        "sync_skland_training",
    }
)

SAFE_TOOL_NAMES = frozenset(
    {
        "stop_pipeline",
        "trigger_screencap",
        "back_to_home",
        "upload_copilot",
        "delete_schedule",
        "get_skland_status",
    }
)

_CONSUMPTION_FIELD_ORDER: dict[str, tuple[str, ...]] = {
    "Fight": ("stone", "medicine", "expiring_medicine"),
    "Recruit": ("expedite",),
    "Mall": ("shopping",),
    "Roguelike": ("investment_enabled",),
}
_CONSUMPTION_COUNT_FIELDS = frozenset(
    {"stone", "medicine", "expiring_medicine"}
)
_CONSUMPTION_FIELD_NAMES = frozenset(
    field for fields in RISK_FIELDS.values() for field in fields
)

#: 预演里的冲突提示前缀（规格 §7.7 末条：确认卡片要提示与现有定时任务争抢理智）。
CONFLICT_REASON_PREFIX = "与现有定时任务争抢理智："


def _conflict_reasons(preview: Mapping[str, Any]) -> list[str]:
    """预演结果里的定时任务冲突 → 确认理由（最多合成一条）。

    冲突条目是服务层 ``_conflicting_schedules`` 的形状 ``{"schedule_id", "name"}``；
    多条用「、」连成一行，字段缺一个时降级成另一边，全缺就跳过这条冲突。
    """
    conflicts = preview.get("conflicts")
    if not isinstance(conflicts, list):
        return []
    labels: list[str] = []
    for item in conflicts:
        if isinstance(item, Mapping):
            name = str(item.get("name") or "").strip()
            schedule_id = str(item.get("schedule_id") or "").strip()
        else:
            name, schedule_id = str(item).strip(), ""
        if name and schedule_id:
            labels.append(f"{name}（{schedule_id}）")
        elif name or schedule_id:
            labels.append(name or schedule_id)
    if not labels:
        return []
    return [f"{CONFLICT_REASON_PREFIX}{'、'.join(labels)}"]


def _preview_error_text(exc: BaseException) -> str:
    """预演异常 → 确认 payload 里的短字符串（没有消息时退回异常类名）。"""
    text = str(exc).strip() or exc.__class__.__name__
    return text[:PLAN_PREVIEW_ERROR_LIMIT]


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Resolved risk, confirmation route, and any existing grant attribution."""

    requires_confirmation: bool
    risk: RiskLevel
    reasons: tuple[str, ...] = ()
    confirmation_action: str | None = None
    authorized_by: str | None = None
    window_seconds: int | None = None
    #: 写进确认 payload 的附加内容（如 `update_user_profile` 的改动前后内容）。
    extra_payload: Mapping[str, Any] | None = None


class PolicyEngine:
    """Evaluate tool risk against arguments and persisted internal-session grants."""

    def __init__(
        self,
        *,
        atomic_grant_minutes: int = DEFAULT_ATOMIC_GRANT_MINUTES,
        plan_preview: PlanPreviewProvider | None = None,
    ):
        if isinstance(atomic_grant_minutes, bool) or not isinstance(
            atomic_grant_minutes, int
        ):
            raise TypeError("atomic_grant_minutes must be an integer")
        if not 1 <= atomic_grant_minutes <= MAX_ATOMIC_GRANT_MINUTES:
            raise ValueError(
                f"atomic_grant_minutes must be between 1 and {MAX_ATOMIC_GRANT_MINUTES}"
            )
        if plan_preview is not None and not callable(plan_preview):
            raise TypeError("plan_preview must be a callable")
        self.atomic_grant_minutes = atomic_grant_minutes
        #: 可选的计划预演提供者（`UpgradePlanService.preview`）；None 时确认卡片
        #: 与 M27 一致，不含 `preview` / `preview_error`。
        self.plan_preview = plan_preview

    async def evaluate(
        self,
        definition: ToolDefinition,
        arguments: Any,
        context: ToolContext,
    ) -> PolicyDecision:
        """Return whether a tool call needs confirmation and why.

        Only an INTERNAL caller can reuse the time-bounded grant attached to its
        persisted agent session. REST requests are always evaluated
        per call and do not load or inherit internal session authorization.
        """
        name = definition.name
        declared_risk = _tool_risk(definition.risk)

        if name in SAFE_TOOL_NAMES:
            return PolicyDecision(False, RiskLevel.NONE)

        if name in ATOMIC_TOOL_NAMES:
            return await self._evaluate_atomic(definition, context)

        if name in ACCOUNT_WRITE_TOOLS:
            return await self._evaluate_account_write(name, arguments, context)

        if name == "run_copilot_attempt" or (
            name == "submit_pipeline" and _submits_a_copilot(_as_json_value(arguments))
        ):
            return await self._evaluate_copilot_attempt(name, arguments, context)

        if name in {
            "submit_pipeline",
            "set_task_params",
            "create_schedule",
            "update_schedule",
        }:
            reasons = await _consumption_reasons(
                arguments, tool_name=name, context=context
            )
            if reasons:
                return PolicyDecision(
                    True,
                    RiskLevel.CONSUME,
                    reasons=_with_precondition_reason(name, arguments, reasons),
                    confirmation_action=name,
                )
            return PolicyDecision(False, RiskLevel.NONE)

        if declared_risk is ToolRisk.SAFE:
            return PolicyDecision(False, RiskLevel.NONE)

        if declared_risk is ToolRisk.DANGEROUS:
            return PolicyDecision(
                True,
                RiskLevel.DESTRUCTIVE,
                reasons=(f"{name} 属于需确认的高风险操作",),
                confirmation_action=name,
            )

        # A CONDITIONAL tool without a registered argument rule must fail closed.
        return PolicyDecision(
            True,
            RiskLevel.DESTRUCTIVE,
            reasons=(f"{name} 尚未配置参数风险规则",),
            confirmation_action=name,
        )

    async def _evaluate_copilot_attempt(
        self, name: str, value: Any, context: ToolContext
    ) -> PolicyDecision:
        """作业授权（规格 §9.6）：INTERNAL 调用方必须先有该会话、该关卡的有效授权。

        没有授权不是「报错」而是「弹授权卡片」：`confirmation_action` 用
        `grant_copilot_attempts`，尝试次数与成功标准从工具参数预填。REST 调用方保持
        现状（逐次评估、无消耗字段即放行）。
        """
        if context.caller is not CallerType.INTERNAL:
            return PolicyDecision(False, RiskLevel.NONE)
        value = _as_json_value(value)
        grant = await _active_copilot_grant(context)
        if grant is None:
            attempts = value.get("grant_attempts") if isinstance(value, Mapping) else None
            success = value.get("success") if isinstance(value, Mapping) else None
            support = value.get("support") if isinstance(value, Mapping) else None
            attempts = int(attempts) if isinstance(attempts, int) and not isinstance(attempts, bool) else 3
            success = str(success or "three_star")
            stage_id, stage_code = await _copilot_stage(
                value.get("copilot_id") if isinstance(value, Mapping) else None, context
            )
            payload: dict[str, Any] = {
                "attempts": max(1, min(attempts, 10)),
                "success": success if success in {"three_star", "clear"} else "three_star",
                "support": str(support) if support in {"fill_one", "none"} else "fill_one",
                "tool_name": name,
                "target_kind": "copilot",
                # 确认接口批准时要写进授权行，所以关卡与会话必须在 payload 里（§9.6）。
                "session_id": context.session_id,
                "stage_id": stage_id,
                "stage_code": stage_code,
            }
            return PolicyDecision(
                True,
                RiskLevel.CONSUME,
                reasons=(f"执行作业尝试需要授权：{payload['attempts']} 次以内",),
                confirmation_action="grant_copilot_attempts",
                extra_payload=payload,
            )
        stage_id = value.get("stage_id") if isinstance(value, Mapping) else None
        if stage_id and str(stage_id) != str(grant.get("stage_id")):
            raise AppError(
                ErrorCode.COPILOT_GRANT_STAGE_MISMATCH,
                f"授权只覆盖关卡 {grant.get('stage_code') or grant.get('stage_id')}，"
                f"不能用于 {stage_id}",
                {"grant_stage_id": grant.get("stage_id"), "stage_id": stage_id},
            )
        remaining = int(grant.get("attempts") or 0) - int(grant.get("used") or 0)
        if remaining <= 0:
            raise AppError(
                ErrorCode.COPILOT_GRANT_EXHAUSTED,
                "本次作业授权的尝试次数已用完，需要重新申请",
                {"grant_id": grant.get("id")},
            )
        return PolicyDecision(
            False,
            RiskLevel.NONE,
            authorized_by=str(grant.get("id")),
            window_seconds=_seconds_until(grant.get("expires_at")),
        )

    async def _evaluate_account_write(
        self, name: str, arguments: Any, context: ToolContext
    ) -> PolicyDecision:
        """账号写入的确认：风险 NONE，但每次都弹卡片，并带上改动前后的差异。"""
        value = _as_json_value(arguments, exclude_unset=False)
        if name == "update_user_profile" and isinstance(value, Mapping):
            section = str(value.get("section") or "")
            mode = str(value.get("mode") or "replace")
            action = "追加" if mode == "append" else "替换"
            summary = f"更新用户档案「{section}」：{action} {len(str(value.get('content') or ''))} 字"
            return PolicyDecision(
                True,
                RiskLevel.NONE,
                reasons=(summary,),
                confirmation_action=name,
                extra_payload={
                    "summary": summary,
                    "section": section,
                    "mode": mode,
                    "diff": await _profile_diff(value, context),
                },
            )
        if name == "start_upgrade_plan" and isinstance(value, Mapping):
            targets = value.get("targets")
            count = len(targets) if isinstance(targets, list) else 0
            times = value.get("run_times")
            schedule = "、".join(str(item) for item in times) if isinstance(times, list) else ""
            summary = f"启动养成计划「{value.get('name')}」：{count} 个目标"
            if schedule:
                summary += f"，每日 {schedule}"
            payload = {
                "summary": summary,
                "targets": targets if isinstance(targets, list) else [],
                "run_times": times if isinstance(times, list) else [],
            }
            reasons = [summary]
            preview, preview_error = await self._plan_preview(value, context)
            if preview is not None:
                payload["preview"] = dict(preview)
                # 规格 §7.7 末条：确认时就告诉用户会和哪些定时任务争抢理智。
                reasons.extend(_conflict_reasons(preview))
            elif preview_error is not None:
                # 预演拿不到只是卡片少一段内容，确认照弹（风险与确认频率都不变）。
                payload["preview_error"] = preview_error
            return PolicyDecision(
                True,
                RiskLevel.NONE,
                reasons=tuple(reasons),
                confirmation_action=name,
                extra_payload=payload,
            )
        if name == "sync_skland_training":
            summary = "从森空岛同步全部干员练度"
            return PolicyDecision(
                True,
                RiskLevel.NONE,
                reasons=(
                    summary,
                    "会用森空岛账号数据覆盖整表练度（精英化、等级、潜能、技能等级、"
                    "专精、模组与信赖），这是账号干员数据的唯一来源",
                ),
                confirmation_action=name,
                extra_payload={"summary": summary},
            )
        return PolicyDecision(
            True,
            RiskLevel.NONE,
            reasons=(f"{name} 需要人工确认",),
            confirmation_action=name,
        )

    async def _plan_preview(
        self, value: Mapping[str, Any], context: ToolContext
    ) -> tuple[Mapping[str, Any] | None, str | None]:
        """问一次计划预演，返回 ``(preview, preview_error)``（规格 §7.7）。

        提供者按 ``name`` / ``targets`` / ``run_times`` / ``context`` 四个关键字调用，
        与 :meth:`~maa_api.services.account.plans.UpgradePlanService.preview` 同签名，
        装配时可以直接注入服务方法。没有注入提供者、参数不齐、返回值不是映射、
        提供者抛异常——四种情况都只是「拿不到预演」，绝不拦下这次确认。
        """
        provider = self.plan_preview
        if provider is None:
            return None, None
        name = value.get("name")
        targets = value.get("targets")
        run_times = value.get("run_times")
        if not isinstance(name, str) or not name.strip():
            return None, None
        if not isinstance(targets, list) or not targets:
            return None, None
        if not isinstance(run_times, list) or not run_times:
            return None, None
        try:
            preview = await provider(
                name=name, targets=targets, run_times=run_times, context=context
            )
        except Exception as exc:  # noqa: BLE001 - 预演失败只降级，不拦确认
            return None, _preview_error_text(exc)
        if not isinstance(preview, Mapping):
            return None, f"计划预演返回的不是映射：{type(preview).__name__}"
        return preview, None

    async def _evaluate_atomic(
        self, definition: ToolDefinition, context: ToolContext
    ) -> PolicyDecision:
        caller = _caller(context.caller)
        if caller is CallerType.REST:
            return _atomic_confirmation(definition.name)
        if caller is not CallerType.INTERNAL:
            raise AppError(ErrorCode.FORBIDDEN, "原子操作调用方身份无效")

        if not context.session_id or context.db_session is None:
            raise AppError(
                ErrorCode.AGENT_SESSION_NOT_FOUND,
                "内部 Agent 原子操作必须属于有效会话",
            )

        session = await AgentSessionRepository(context.db_session).get(
            context.session_id
        )
        if session is None or str(session.status) != AgentSessionStatus.ACTIVE.value:
            raise AppError(
                ErrorCode.AGENT_SESSION_NOT_FOUND,
                "内部 Agent 会话不存在或已结束，不能授权原子操作",
            )

        grant_id = getattr(session, "atomic_grant_id", None)
        expires_at = getattr(session, "atomic_grant_expires_at", None)
        if (
            grant_id
            and expires_at is not None
            and _is_future(expires_at)
            and await self._is_approved_grant(grant_id, context)
        ):
            return PolicyDecision(
                False,
                RiskLevel.NONE,
                authorized_by=grant_id,
            )

        return PolicyDecision(
            True,
            RiskLevel.NONE,
            reasons=("当前会话尚未获得有效的原子操作授权",),
            confirmation_action="grant_atomic_ops",
            window_seconds=self.atomic_grant_minutes * 60,
        )

    async def _is_approved_grant(
        self, grant_id: str, context: ToolContext
    ) -> bool:
        if context.db_session is None:
            return False
        confirmation = await ConfirmationRepository(context.db_session).get(
            grant_id
        )
        return bool(
            confirmation is not None
            and str(confirmation.status) == "approved"
            and confirmation.action == "grant_atomic_ops"
            and str(confirmation.requested_by) == CallerType.INTERNAL.value
            and confirmation.payload.get("session_id") == context.session_id
        )


async def _profile_diff(
    value: Mapping[str, Any], context: ToolContext
) -> list[dict[str, Any]]:
    """用户档案的改动前后差异：``[{"section", "from", "to"}]``。

    读不到现有条目（没有会话、条目不存在、服务异常）时 ``from`` 为 ``None``：确认卡片
    仍显示将要写入的内容。
    """
    section = str(value.get("section") or "")
    content = str(value.get("content") or "")
    mode = str(value.get("mode") or "replace")
    before = await _current_profile_section(section, context)
    after = f"{before}{content}" if mode == "append" and before else content
    if before == after:
        return []
    return [{"section": section, "from": before, "to": after, "mode": mode}]


async def _current_profile_section(
    section: str, context: ToolContext
) -> str | None:
    """读档案条目里某一节的现有内容；任何失败都返回 ``None``。"""
    if not section or context.db_session is None:
        return None
    try:
        from maa_api.agent.knowledge import PROFILE_ENTRY_ID, split_profile_sections

        session = context.db_session

        def factory():  # pragma: no cover - 复用调用方已有的会话
            class _Existing:
                async def __aenter__(self_inner):
                    return session

                async def __aexit__(self_inner, *exc):
                    return False

            return _Existing()

        service = KnowledgeService(factory)
        entry = await service.read_for_agent(PROFILE_ENTRY_ID)
        return split_profile_sections(entry.body).get(section)
    except Exception:  # noqa: BLE001 - 差异只是确认卡片的附加信息
        return None


def _atomic_confirmation(name: str) -> PolicyDecision:
    return PolicyDecision(
        True,
        RiskLevel.NONE,
        reasons=(f"{name} 是绕过内核识别与容错的原子操作",),
        confirmation_action=name,
    )


def _tool_risk(value: ToolRisk | str) -> ToolRisk:
    if isinstance(value, ToolRisk):
        return value
    return ToolRisk(str(value).upper())


def _caller(value: CallerType | str) -> CallerType | None:
    try:
        return CallerType(value)
    except ValueError:
        return None


def _is_future(value: datetime) -> bool:
    """Compare database UTC-naive and API timezone-aware timestamp values safely."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value > datetime.now(UTC)


def _as_json_value(value: Any, *, exclude_unset: bool = True) -> Any:
    """Serialize an argument model for inspection.

    ``exclude_unset=False`` keeps every schema default, which is what the kernel
    receives once the payload is normalized (D1).
    """
    if isinstance(value, BaseModel):
        return value.model_dump(
            mode="python", by_alias=True, exclude_unset=exclude_unset
        )
    return value


def _with_precondition_reason(
    name: str, arguments: Any, reasons: tuple[str, ...]
) -> tuple[str, ...]:
    """要确认的 ``submit_pipeline`` 再附一条「没有 StartUp」的提醒（规格 §4.5）。

    只在**确实要确认**（已经有消耗类理由）时加：不需要确认的提交由工具结果里的
    ``warnings`` 提醒，策略层不制造新的确认理由——用户裁决是「提醒，不拦」，
    确认卡片上不出现这条警告的提交照样会走到用户面前。
    """
    if name != "submit_pipeline":
        return reasons
    value = _as_json_value(arguments)
    tasks = value.get("tasks") if isinstance(value, Mapping) else None
    warning = missing_start_up_warning(tasks)
    if warning is None or warning in reasons:
        return reasons
    return (*reasons, warning)


def _submits_a_copilot(value: Any) -> bool:
    """提交内容里有没有 Copilot 任务（规格 §9.6 的第二种入口）。"""
    if not isinstance(value, Mapping):
        return False
    tasks = value.get("tasks")
    if not isinstance(tasks, list):
        return False
    for task in tasks:
        if isinstance(task, Mapping) and str(task.get("name") or "") == "Copilot":
            return True
    return False


async def _copilot_stage(
    copilot_id: Any, context: ToolContext
) -> tuple[str | None, str | None]:
    """从作业资源读关卡（`run_copilot_attempt` 的参数里没有关卡）。

    读不到（没有会话、资源不存在、meta 里没写）时返回两个 `None`：确认卡片照弹，
    批准时由确认服务按「关卡缺失」处理，不会写一条空授权。
    """
    if not isinstance(copilot_id, str) or not copilot_id or context.db_session is None:
        return None, None
    try:
        from sqlmodel import select

        from maa_api.db.models import ResourceAsset

        session = context.db_session
        row = (
            await session.execute(
                select(ResourceAsset).where(ResourceAsset.id == copilot_id)
            )
        ).scalars().first()
        meta = (getattr(row, "meta", None) or {}) if row is not None else {}
        stage_id = meta.get("stage_id")
        stage_code = meta.get("stage_code") or stage_id
        return (
            str(stage_id) if stage_id else None,
            str(stage_code) if stage_code else None,
        )
    except Exception:  # noqa: BLE001 - 关卡只是确认卡片的附加信息
        return None, None


async def _active_copilot_grant(context: ToolContext) -> dict[str, Any] | None:
    """读当前会话的有效作业授权；读不到就当作「没有授权」（fail closed 到弹卡片）。"""
    if context.db_session is None:
        return None
    try:
        from maa_api.services.copilot.grant import CopilotGrantService

        session = context.db_session

        def factory():  # pragma: no cover - 复用调用方已有的会话
            class _Existing:
                async def __aenter__(self_inner):
                    return session

                async def __aexit__(self_inner, *exc):
                    return False

            return _Existing()

        return await CopilotGrantService(factory).get_active(context.session_id)
    except Exception:  # noqa: BLE001 - 授权服务不可用时按「没有授权」处理
        return None


def _seconds_until(value: Any) -> int | None:
    """授权剩余秒数（给确认卡片与审计用）。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(int((moment - datetime.now(UTC)).total_seconds()), 0)


async def _consumption_reasons(
    arguments: Any, *, tool_name: str, context: ToolContext | None = None
) -> tuple[str, ...]:
    """Return the risk fields that the kernel would actually receive as truthy.

    The values are the effective ones: schema defaults count, and for
    ``set_task_params`` the persisted task parameters are merged with this
    patch before judging. Anything that cannot be resolved is judged by the
    request alone, which can only add confirmations, never remove one.
    """
    reasons: list[str] = []
    value = _as_json_value(arguments, exclude_unset=False)
    if not isinstance(value, Mapping):
        return ()

    existing: Mapping[str, Any] | None = None
    existing_model: type[BaseModel] | None = None
    if tool_name == "set_task_params":
        patch = _set_task_params_patch(value)
        resolved = await _existing_task_params(value.get("task_id"), context)
        if resolved is None:
            return _unresolved_patch_reasons(patch)
        existing, existing_model = resolved
        task_values = (_merged_params(patch, existing, existing_model),)
    else:
        tasks = value.get("tasks", value.get("template", ()))
        task_values = tasks if isinstance(tasks, (list, tuple)) else (tasks,)

    for task in task_values:
        effective = _effective_task_params(task)
        if effective is None:
            continue
        reasons.extend(_task_consumption_reasons(effective, _task_type_name(effective)))

    return tuple(dict.fromkeys(reasons))


def _set_task_params_patch(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Flatten the request body into the field patch the runner would apply.

    The registered model is ``{"task_id": ..., "params": {...}}``; a wrapper
    ``{"task": {"name": ..., "params": {...}}}`` reaches policy evaluation from
    callers that hand over a whole task definition, so both shapes are folded
    into one flat patch and the inner values win.
    """
    flat: dict[str, Any] = {}
    task = value.get("task")
    if isinstance(task, Mapping):
        flat.update(task)
    for key in ("params", "task_params"):
        nested = task.get(key) if isinstance(task, Mapping) else None
        if nested is None:
            nested = value.get(key)
        if isinstance(nested, Mapping):
            flat.update(nested)
    return flat or value


def _unresolved_patch_reasons(patch: Mapping[str, Any]) -> tuple[str, ...]:
    """Fail closed for a patch whose task type and current values are unknown.

    The decision usually comes from the persisted row, which carries both the
    task type and the parameters the core is running with. Without that row
    there is no way to tell which defaults the core would apply, so the patch
    is confirmed unless its own shape pins down a single, non-consuming task
    type. A patch that names its task type through ``name`` stays free when
    that type consumes nothing; anything ambiguous is confirmed.
    """
    named = _patch_task_name(patch)
    if named is not None:
        task_name, model = named
        params, _ = _resolved_task_params(model, _patch_for_model(patch, model))
        return tuple(_task_consumption_reasons(params, task_name))

    consuming: list[list[str]] = []
    for task_name, model in TASK_MODELS.items():
        applicable = _patch_for_model(patch, model)
        if not applicable:
            continue
        params, _ = _resolved_task_params(model, applicable)
        reasons = _task_consumption_reasons(params, task_name)
        if reasons:
            consuming.append(reasons)
    if len(consuming) == 1:
        return tuple(consuming[0])
    return (CONSUMPTION_UNDETERMINED_REASON,)


def _patch_task_name(patch: Mapping[str, Any]) -> tuple[str, type[BaseModel]] | None:
    """Resolve the task type a patch names through a payload ``name`` field."""
    for key in ("name", "task_name", "type", "type_name"):
        value = patch.get(key)
        if isinstance(value, str) and value in TASK_MODELS:
            return value, TASK_MODELS[value]
    return None


def _patch_for_model(
    patch: Mapping[str, Any], model: type[BaseModel]
) -> dict[str, Any]:
    """Keep the patch fields that ``model`` understands (field name or alias)."""
    accepted: set[str] = set()
    aliases: dict[str, str] = {}
    for name, model_field in model.model_fields.items():
        accepted.add(name)
        alias = model_field.validation_alias
        if isinstance(alias, str):
            accepted.add(alias)
            aliases[name] = alias
    return {
        aliases.get(field, field): value
        for field, value in patch.items()
        if field in accepted
    }


def _effective_task_params(task: Any) -> Mapping[str, Any] | None:
    """Return the kernel-ready parameters of one task definition, if resolvable.

    The kernel receives the *complete* task definition: the fields that the
    payload left out fall back to the schema defaults (Mall ``shopping`` and
    Roguelike ``investment_enabled`` are ``true``).
    """
    dumped = _as_json_value(task, exclude_unset=False)
    if not isinstance(dumped, Mapping):
        return None
    params = dumped.get("params", dumped.get("raw_params", dumped))
    if not isinstance(params, Mapping):
        return None
    task_name = _task_type_name(dumped)
    model = TASK_MODELS.get(task_name) if task_name is not None else None
    return _resolved_task_params(model, params)[0]


def _task_type_name(task: Mapping[str, Any]) -> str | None:
    for key in ("name", "task_name", "type", "type_name"):
        value = task.get(key)
        if isinstance(value, str) and value in TASK_MODELS:
            return value
    return None


def _resolved_task_params(
    model: type[BaseModel] | None, params: Mapping[str, Any]
) -> tuple[Mapping[str, Any], type[BaseModel] | None]:
    """Normalize one parameter map to the values the core would receive.

    Task models reject impossible combinations with ``AppError`` in cross-field
    validators, so both failure kinds fall back to the raw map instead of
    letting a policy evaluation raise.
    """
    if model is None:
        return params, None
    try:
        normalized = model.model_validate(params)
    except (ValidationError, AppError):
        return params, model
    return (
        normalized.model_dump(mode="python", by_alias=True, exclude_unset=False),
        model,
    )


def _task_consumption_reasons(
    params: Mapping[str, Any], task_name: str | None
) -> list[str]:
    """Judge one complete parameter set, preferring the field order per type."""
    field_names = (
        _CONSUMPTION_FIELD_ORDER[task_name]
        if task_name in _CONSUMPTION_FIELD_ORDER
        else tuple(sorted(_CONSUMPTION_FIELD_NAMES))
    )
    if task_name is not None and task_name in RISK_FIELDS:
        field_names = tuple(
            field for field in field_names if field in RISK_FIELDS[task_name]
        )
    reasons: list[str] = []
    for field in field_names:
        if field not in params or not _is_consuming(params[field], field=field):
            continue
        label = f"{task_name}.{field}" if task_name else field
        reasons.append(f"{label}={params[field]}")
    return reasons


def _is_consuming(candidate: Any, *, field: str) -> bool:
    if field in _CONSUMPTION_COUNT_FIELDS:
        return (
            isinstance(candidate, (int, float))
            and not isinstance(candidate, bool)
            and candidate > 0
        )
    return candidate is True


def _merged_params(
    patch: Mapping[str, Any],
    existing: Mapping[str, Any] | None,
    model: type[BaseModel] | None,
) -> Mapping[str, Any]:
    """Merge the current task parameters with this patch.

    A sparse ``Task.params`` row usually lost the core's defaults (only the
    fields that were set at submit time survive), so the row is re-validated
    and the patch is layered on top of the complete view. ``None`` patch
    values are dropped: the runner rejects them before touching the core.
    """
    merged: dict[str, Any] = dict(existing or {})
    for field, value in patch.items():
        if value is None:
            continue
        merged[_normalized_field(field, model)] = value
    return merged


def _normalized_field(field: str, model: type[BaseModel] | None) -> str:
    """Map an alias/field name back to the model field name (Runner parity)."""
    if model is None:
        return field
    for name, model_field in model.model_fields.items():
        if model_field.validation_alias == field:
            return name
    return field


async def _existing_task_params(
    task_id: Any, context: ToolContext | None
) -> tuple[Mapping[str, Any], type[BaseModel] | None] | None:
    """Read the persisted parameters that this runtime patch builds on.

    ``set_task_params`` is the only payload that carries no task type and no
    full parameter set. A missing context, a missing row or a failed read all
    return ``None``; the caller then judges the request as it stands, which can
    only add confirmations (fail closed).
    """
    if context is None or not isinstance(task_id, str) or not task_id:
        return None
    session = context.db_session
    if session is None:
        return None
    try:
        task = await TaskRepository(session).get(task_id)
    except AppError:
        return None
    if task is None:
        return None
    params = getattr(task, "params", None)
    if not isinstance(params, Mapping):
        return None
    task_name = getattr(task, "type_name", None)
    model = TASK_MODELS.get(task_name) if isinstance(task_name, str) else None
    return _resolved_task_params(model, params)
