"""一次作业尝试：执行、结果判定、计数与记录（M29，规格 §9.7）。

``run()`` 的四步（规格 §9.7 的「执行」）：

1. 提交作业任务 ``Copilot(asset_id, formation=true, support_unit_usage=0|1)``；
2. 作业任务结束后（**不论成功还是失败**）跑结果识别（``Custom`` + ``MaaApi@CopilotResult…``，
   见 :mod:`maa_api.services.copilot.result`）；
3. 退出结算，回到「开始行动」界面，为下一次尝试做准备；
4. 阻塞等待，最长 20 分钟（``ATTEMPT_TIMEOUT_SECONDS``，超时记 ``error``）。

结果与计数（规格 §9.7 的表）::

    outcome                计入次数  后续
    three_star / two_star / clear / failed / support_unavailable / unknown   是
    sanity_insufficient / not_started / error / interrupted                  否（退还次数）

三条本模块的实现裁决（规格没写死，写进报告）：

- **退还而不是不记**：``consume`` 在开始尝试时先加 ``used``，不计次的结果由
  :meth:`~maa_api.services.copilot.grant.CopilotGrantService.refund` 退回来，
  这样「已经开始的尝试」在库里始终可见，中断/崩溃后也能看出耗到了第几次。
- **流水线失败与识别结果的关系**：流水线 ``failed`` / 崩溃但识别给出了明确的战斗结果
  （``failed`` 等）时以识别结果为准（打输了就是 ``failed``，计次）；流水线失败且识别
  不出结果时记 ``error``（不烧次数）。超时（还没结束）与 ``cancelled``（用户中断本轮）
  分别记 ``error`` / ``interrupted``，都不再跑识别。
- **什么时候跑退出结算**：只有「画面停在结算界面或弹窗上」的结果才跑
  （三/二星、``clear``、``failed``、``unknown``、``sanity_insufficient``）。没开打的
  ``not_started`` / ``support_unavailable``、还没结束的超时、被中断的流水线都不跑：
  那些情况下画面还在编队页或战斗里，``Copilot@ClickCornerUntilStartButton`` 会乱点。

记录落在作业资源的 ``meta["attempts"]``（规格 §9.5 的落点，仓储见
:class:`~maa_api.db.repositories.copilot.CopilotAttemptRepository`）：
``id`` / ``copilot_id`` / ``grant_id`` / ``pipeline_id`` / ``outcome`` / ``judged_by`` /
``counted`` / ``screenshot_id`` / ``evidence`` / ``started_at`` / ``finished_at``。
``judged_by`` 只有 ``core``（内核识别）与 ``model``（``record_copilot_result`` 兜底）
两种；``error`` / ``interrupted`` 不是判定结果，留空。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from maa_api.db.models import utcnow
from maa_api.db.repositories.copilot import CopilotAttemptRepository
from maa_api.db.repositories.log import ScreenshotRepository
from maa_api.db.repositories.resource import ResourceAssetRepository
from maa_api.domain.enums import ResourceAssetKind
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.copilot.grant import DEFAULT_SUPPORT, is_success
from maa_api.services.copilot.result import (
    EXIT_TASK_ENTRY,
    callback_signal,
    submit_pipeline,
    wait_pipeline,
)

__all__ = [
    "ACTION_LABELS",
    "ATTEMPT_TIMEOUT_SECONDS",
    "COUNTED_OUTCOMES",
    "EXIT_TIMEOUT_SECONDS",
    "MAX_ACTION_LINES",
    "MAX_EVIDENCE_CHARS",
    "RECOGNITION_TIMEOUT_SECONDS",
    "RECORDABLE_OUTCOMES",
    "UNCOUNTED_OUTCOMES",
    "CopilotAsset",
    "CopilotAttemptService",
    "copilot_action_lines",
]

logger = logging.getLogger(__name__)

#: 一次尝试的阻塞等待上限（规格 §9.7 第 4 步：最长 20 分钟）。
ATTEMPT_TIMEOUT_SECONDS = 20 * 60.0

#: 结果识别自己的超时（看一眼画面，不该等太久）。
RECOGNITION_TIMEOUT_SECONDS = 120.0

#: 退出结算的超时。
EXIT_TIMEOUT_SECONDS = 180.0

#: 计次的结果（规格 §9.7 的「计入次数」列）。
COUNTED_OUTCOMES = frozenset(
    {"three_star", "two_star", "clear", "failed", "support_unavailable", "unknown"}
)

#: 不计次的结果：``sanity_insufficient`` / ``not_started`` / ``error`` / ``interrupted``。
UNCOUNTED_OUTCOMES = frozenset(
    {"sanity_insufficient", "not_started", "error", "interrupted"}
)

#: ``record_copilot_result`` 允许模型补的四个结果（规格 §9.7）。
RECORDABLE_OUTCOMES: tuple[str, ...] = ("three_star", "two_star", "clear", "failed")

#: ``evidence`` 的字数上限（规格 §9.7：≤ 200 字）。
MAX_EVIDENCE_CHARS = 200

#: 动作日志摘要最多给几行（剩下的只报条数）。
MAX_ACTION_LINES = 40

#: 画面停在结算界面或弹窗上的结果 → 跑退出结算。
_EXIT_OUTCOMES = frozenset(
    {"three_star", "two_star", "clear", "failed", "unknown", "sanity_insufficient"}
)

#: 内核 ``CopilotAction`` 的英文动作名 → 中文日志行（``BattleProcessTask::notify_action``）。
ACTION_LABELS: dict[str, str] = {
    "Deploy": "部署",
    "UseSkill": "开技能",
    "Retreat": "撤退",
    "SkillDaemon": "摆完挂机",
    "SwitchSpeed": "二倍速",
    "SkillUsage": "技能用法",
    "BulletTime": "子弹时间",
    "Output": "输出",
    "MoveCamera": "移动镜头",
    "DrawCard": "调配干员",
    "CheckIfStartOver": "检查重开",
    "ResetStopwatch": "重置计时器",
    "Click": "点击",
    "Swipe": "滑动",
    "SetUnitLocation": "设置单位坐标",
}

#: 每个结果的下一步提示（给模型看的一句话）。
_GOAL_HINTS: dict[str, str] = {
    "three_star": "三星完成；已按授权标准记成功。",
    "two_star": "二星通关；「通关即可」的标准下算成功，三星标准下还可以再试一次。",
    "clear": "结算界面出现但星级不明；「通关即可」的标准下算成功。",
    "failed": "行动失败；已计入次数，可以改作业或换一份再试。",
    "support_unavailable": "需要助战补位但没借到；已计入次数，请确认助战位或换一份作业。",
    "sanity_insufficient": "理智不足，授权已停止；请补充理智后重新申请授权。",
    "not_started": "战斗没有开始（不在「开始行动」界面、编队失败或干员缺失）；"
    "这一次不计次，请把游戏停在「开始行动」界面并截图确认后再试。",
    "unknown": "没能识别出结算结果；已计数并附结算截图，"
    "请用 record_copilot_result 记录你从截图里看到的结果。",
    "error": "执行出错或超时；这一次不计次，可以修好之后重试。",
    "interrupted": "本轮被中断；这一次不计次，授权已停止。",
}


def _pipeline_status(pipeline: Any) -> str:
    """流水线终态：ORM 实例读属性，测试/冒烟替身给的是字典。"""
    if isinstance(pipeline, Mapping):
        return str(pipeline.get("status") or "")
    return str(getattr(pipeline, "status", "") or "")


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.isoformat()
    return str(value)


def copilot_action_lines(callbacks: Sequence[Any]) -> tuple[list[str], int]:
    """把 ``CopilotAction`` 回调翻成日志行，返回 ``(行, 总条数)``（规格 §9.7 的返回项）。

    每条形如「部署 凛御银灰」「二倍速」；有 ``doc``（作业作者写的说明）时附在后面。
    ``elapsed_time`` 是内核给的动作时刻，拼在行首帮助对齐时间轴。
    """
    lines: list[str] = []
    total = 0
    for item in callbacks:
        signal = callback_signal(item)
        if signal.what != "CopilotAction":
            continue
        total += 1
        details = signal.details
        action = str(details.get("action") or "")
        label = ACTION_LABELS.get(action, action or "动作")
        target = str(details.get("target") or "").strip()
        text = f"{label} {target}".strip()
        doc = str(details.get("doc") or "").strip()
        if doc:
            text = f"{text}（{doc}）"
        elapsed = details.get("elapsed_time")
        if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool):
            text = f"[{round(float(elapsed) / 1000, 1)}s] {text}"
        if len(lines) < MAX_ACTION_LINES:
            lines.append(text)
    return lines, total


@dataclass(frozen=True, slots=True)
class CopilotAsset:
    """一次尝试要执行的那份作业（从 ``kind=copilot`` 的资源里读出来）。"""

    id: str
    stage_id: str | None
    stage_code: str | None
    title: str | None


class CopilotAttemptService:
    """作业尝试的执行器（规格 §9.7）：提交作业、判结果、算次数、记尝试。"""

    def __init__(
        self,
        session_factory: Any,
        *,
        pipeline_service: Any,
        grant_service: Any,
        result_service: Any,
        draft_service: Any = None,
        box: Any = None,
        now_provider: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session_factory = session_factory
        self._pipeline_service = pipeline_service
        self._grant_service = grant_service
        self._result_service = result_service
        #: 作业资源的门面（规格 §9.5）。目前只在它提供 ``get`` / ``load`` 时优先用它
        #: 读作业；否则直接读资源表——两条路都只读 ``meta`` 与 ``content``。
        self._draft_service = draft_service
        #: 账号干员数据（保留给「助战补位」的展示与后续静态校验；本轮不必需）。
        self._box = box
        self._now_provider = now_provider

    # -- 读作业 -----------------------------------------------------------
    async def _load_asset(self, copilot_id: str) -> CopilotAsset:
        asset: Any = None
        getter = getattr(self._draft_service, "get", None) or getattr(
            self._draft_service, "load", None
        )
        if callable(getter):
            try:
                asset = await getter(copilot_id)
            except Exception:  # noqa: BLE001 - 门面拿不到就退回读表
                asset = None
        if asset is None:
            async with self._session_factory() as session:
                asset = await ResourceAssetRepository(session).get(copilot_id)
        if asset is None or str(getattr(asset, "kind", "")) != ResourceAssetKind.COPILOT.value:
            raise AppError(
                ErrorCode.RESOURCE_ASSET_NOT_FOUND,
                "Copilot 作业不存在，请刷新列表",
                {"asset_id": copilot_id},
            )
        meta = getattr(asset, "meta", None)
        meta = meta if isinstance(meta, Mapping) else {}
        content = getattr(asset, "content", None)
        content = content if isinstance(content, Mapping) else {}
        stage_id = str(meta.get("stage_id") or "").strip() or None
        stage_code = str(meta.get("stage_code") or "").strip() or None
        if stage_code is None:
            name = str(content.get("stage_name") or "").strip()
            stage_code = name or None
        if stage_id is None:
            stage_id = stage_code
        if not stage_id:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                "这份作业没有关卡信息，无法申请授权",
                {"asset_id": copilot_id},
            )
        return CopilotAsset(
            id=str(getattr(asset, "id", copilot_id)),
            stage_id=stage_id,
            stage_code=stage_code,
            title=str(getattr(asset, "name", "") or "") or None,
        )

    # -- 执行 -------------------------------------------------------------
    async def run(
        self,
        *,
        copilot_id: str,
        session_id: str | None = None,
        support: str | None = None,
        stage_id: str | None = None,
        stage_code: str | None = None,
        grant_attempts: int | None = None,
        success: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """跑一次尝试，返回尝试编号、结果、判定方、截图 id、剩余次数与授权状态。

        ``grant_attempts`` 与 ``success`` 只在**需要申请授权**时用于卡片预填
        （规格 §9.7：批准之后以授权里的值为准，这里不再决定次数与标准）。

        ``session_id`` 为空时按 REST 口径处理：不消耗授权（规格 §9.6「REST 保持现状」），
        调用方拿不到剩余次数（``remaining=None``）。
        """
        asset = await self._load_asset(copilot_id)
        resolved_stage_id = stage_id or asset.stage_id
        resolved_stage_code = stage_code or asset.stage_code
        budget = (
            self._default_timeout() if timeout_seconds is None else max(float(timeout_seconds), 0.0)
        )
        started_at = self._now()
        attempt_id = str(uuid.uuid4())

        grant: dict[str, Any] | None = None
        attempt_number: int | None = None
        remaining: int | None = None
        if session_id:
            active = await self._grant_service.get_active(session_id)
            if support is None and active is not None:
                support = str(active.get("support") or DEFAULT_SUPPORT)
            consumed = await self._grant_service.consume(
                session_id,
                resolved_stage_id,
                stage_code=resolved_stage_code,
                prefill={
                    "attempts": grant_attempts,
                    "success": success,
                    "support": support,
                },
            )
            grant = consumed["grant"]
            attempt_number = int(consumed["attempt_number"])
            remaining = int(consumed["remaining"])
        resolved_support = support or DEFAULT_SUPPORT
        usage = 1 if resolved_support == "fill_one" else 0

        # 1) 作业任务。
        pipeline_id = await submit_pipeline(
            self._pipeline_service,
            tasks=[
                {
                    "name": "Copilot",
                    "asset_id": asset.id,
                    "formation": True,
                    "support_unit_usage": usage,
                }
            ],
            title=f"作业尝试：{asset.title or asset.stage_code or asset.id}",
            session_id=session_id,
        )
        deadline = time.monotonic() + budget
        pipeline, finished = await wait_pipeline(
            self._pipeline_service, pipeline_id or "", timeout_seconds=budget
        )
        status = _pipeline_status(pipeline)
        remaining_budget = max(deadline - time.monotonic(), 0.0)

        outcome: str
        judged_by: str | None = None
        recognition_pipeline_id: str | None = None
        screenshot_id: str | None = None
        actions: list[str] = []
        action_count = 0
        if not finished:
            outcome = "error"
            hint_extra = f"（等待超过 {round(budget)} 秒）"
        elif status == "cancelled":
            outcome = "interrupted"
            hint_extra = ""
        else:
            reported = await self._recognize(
                session_id=session_id,
                support=resolved_support,
                budget=remaining_budget,
            )
            outcome = str(reported.get("outcome") or "unknown")
            judged_by = reported.get("judged_by")
            recognition_pipeline_id = reported.get("pipeline_id")
            if outcome == "unknown" and status in {"failed", "crashed"}:
                # 流水线出错且识别不出结果：记 error，不烧次数（规格 §9.7 的 error 行）。
                outcome = "error"
                judged_by = None
            hint_extra = ""

        callbacks = await self._callbacks_for(pipeline_id)
        actions, action_count = copilot_action_lines(callbacks)

        # 3) 退出结算：只在画面停在结算界面/弹窗上时跑（见模块 docstring 的裁决）。
        exit_pipeline_id: str | None = None
        if finished and outcome in _EXIT_OUTCOMES:
            exit_pipeline_id = await self._exit_settlement(session_id=session_id)

        # 计数、终态与记录。
        counted = outcome in COUNTED_OUTCOMES
        if not counted and grant is not None:
            refunded = await self._grant_service.refund(
                session_id, reason=f"尝试结果不计次：{outcome}"
            )
            if refunded is not None:
                grant = refunded
                remaining = int(refunded["remaining"])
        success = bool(grant and counted and is_success(outcome, str(grant.get("success"))))
        if outcome == "sanity_insufficient" and session_id:
            stopped = await self._grant_service.stop(session_id, "理智不足")
            grant = stopped or grant
        elif outcome == "interrupted" and session_id:
            stopped = await self._grant_service.stop(session_id, "用户中断本轮")
            grant = stopped or grant
        elif success and session_id:
            finished_grant = await self._grant_service.finish(session_id, "succeeded")
            grant = finished_grant or grant

        screenshot_id = await self._latest_screenshot(
            recognition_pipeline_id or pipeline_id
        )
        finished_at = self._now()
        attempt = {
            "id": attempt_id,
            "copilot_id": asset.id,
            "grant_id": (grant or {}).get("id"),
            "session_id": session_id,
            "pipeline_id": pipeline_id,
            "recognition_pipeline_id": recognition_pipeline_id,
            "exit_pipeline_id": exit_pipeline_id,
            "outcome": outcome,
            "judged_by": judged_by,
            "counted": counted,
            "success": success,
            "screenshot_id": screenshot_id,
            "evidence": "",
            "support": resolved_support,
            "attempt_number": attempt_number,
            "stage_id": resolved_stage_id,
            "stage_code": resolved_stage_code,
            "pipeline_status": status,
            "started_at": _iso(started_at),
            "finished_at": _iso(finished_at),
        }
        await self._append_attempt(asset.id, attempt)

        hint = _GOAL_HINTS.get(outcome, "") + hint_extra
        return {
            **attempt,
            "attempt_id": attempt_id,
            "remaining": remaining,
            "grant": grant,
            "grant_status": (grant or {}).get("status"),
            "actions": actions,
            "action_count": action_count,
            "hint": hint,
        }

    # -- 模型判定 ---------------------------------------------------------
    async def record(
        self, attempt_id: str, outcome: str, evidence: str = ""
    ) -> dict[str, Any]:
        """模型用 ``record_copilot_result`` 补判一次 ``unknown`` 的尝试（规格 §9.7）。

        只允许 ``outcome=unknown`` 的尝试：内核已经判过的结果不允许被模型改写；
        判定方记 ``model``，前端与通知都标「模型判定」。
        """
        if outcome not in RECORDABLE_OUTCOMES:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"只能记录 {' / '.join(RECORDABLE_OUTCOMES)} 之一，收到 {outcome}",
                {"outcome": outcome, "allowed": list(RECORDABLE_OUTCOMES)},
            )
        text = str(evidence or "")
        if len(text) > MAX_EVIDENCE_CHARS:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"evidence 最多 {MAX_EVIDENCE_CHARS} 字",
                {"length": len(text), "maximum": MAX_EVIDENCE_CHARS},
            )
        async with self._session_factory() as session:
            repo = CopilotAttemptRepository(session)
            found = await repo.find(str(attempt_id or ""))
            if found is None:
                raise AppError(
                    ErrorCode.INVALID_PARAMETER,
                    f"找不到这次尝试：{attempt_id}",
                    {"attempt_id": attempt_id},
                )
            if str(found.data.get("outcome")) != "unknown":
                raise AppError(
                    ErrorCode.INVALID_PARAMETER,
                    "只能记录 outcome=unknown 的尝试："
                    f"这次已经是 {found.data.get('outcome')}",
                    {"attempt_id": attempt_id, "outcome": found.data.get("outcome")},
                )
            updated = await repo.update(
                found.asset_id,
                attempt_id,
                outcome=str(outcome),
                judged_by="model",
                counted=True,
                evidence=text,
                finished_at=_iso(self._now()),
            )
            await session.commit()
        data = dict(updated.data if updated is not None else found.data)

        grant: dict[str, Any] | None = None
        session_id = data.get("session_id")
        criterion = str(data.get("grant_success") or "three_star")
        success = is_success(outcome, criterion)
        if session_id:
            active = await self._grant_service.get_active(session_id)
            if active is not None:
                criterion = str(active.get("success") or criterion)
                success = is_success(outcome, criterion)
                if success:
                    grant = await self._grant_service.finish(session_id, "succeeded")
                else:
                    grant = active
        return {
            "attempt": data,
            "counted": True,
            "success": success,
            "grant": grant,
            "remaining": (grant or {}).get("remaining"),
            "hint": _GOAL_HINTS.get(str(outcome), ""),
        }

    # -- 内部：识别 / 退出 / 截图 / 记录 -----------------------------------
    async def _recognize(
        self, *, session_id: str | None, support: str, budget: float
    ) -> dict[str, Any]:
        """结果识别：失败不抛给调用方（记 ``unknown``，交模型兜底）。"""
        # 结果识别也在同一条 20 分钟预算里（规格 §9.7 第 4 步）：作业跑完得早时
        # 预算还剩很多，作业卡到超时的那一刻就不再跑识别（``run`` 直接记 error）。
        timeout = min(RECOGNITION_TIMEOUT_SECONDS, max(budget, 1.0))
        try:
            return await self._result_service.recognize(
                pipeline_service=self._pipeline_service,
                session_id=session_id,
                support=support,
                timeout_seconds=timeout,
            )
        except Exception as exc:  # noqa: BLE001 - 识别本身失败不该把尝试变成异常
            logger.warning("作业结果识别失败，按 unknown 处理：%s", exc, exc_info=True)
            return {"outcome": "unknown", "judged_by": None, "pipeline_id": None}

    async def _exit_settlement(self, *, session_id: str | None) -> str | None:
        """退出结算（规格 §9.7 第 3 步）：失败只记日志，不影响本次结论。"""
        try:
            return await submit_pipeline(
                self._pipeline_service,
                tasks=[{"name": "Custom", "task_names": [EXIT_TASK_ENTRY]}],
                title="作业退出结算",
                session_id=session_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("提交退出结算任务失败：%s", exc, exc_info=True)
            return None

    async def _callbacks_for(self, pipeline_id: str | None) -> list[Any]:
        reader = getattr(self._result_service, "callbacks_for", None)
        if pipeline_id is None or not callable(reader):
            return []
        try:
            return list(await reader(pipeline_id))
        except Exception:  # noqa: BLE001 - 摘要拿不到不影响结论
            logger.debug("读取作业动作日志失败", exc_info=True)
            return []

    async def _latest_screenshot(self, pipeline_id: str | None) -> str | None:
        """这条流水线最近一张截图（``unknown`` 时给模型看结算画面，规格 §9.7）。"""
        if not pipeline_id or self._session_factory is None:
            return None
        try:
            async with self._session_factory() as session:
                page = await ScreenshotRepository(session).list_page(
                    pipeline_id=pipeline_id, page=1, size=1
                )
        except Exception:  # noqa: BLE001
            logger.debug("查询结算截图失败", exc_info=True)
            return None
        items = list(getattr(page, "items", []) or [])
        if not items:
            return None
        return str(getattr(items[0], "id", "") or "") or None

    async def _append_attempt(self, asset_id: str, attempt: Mapping[str, Any]) -> None:
        async with self._session_factory() as session:
            await CopilotAttemptRepository(session).append(asset_id, attempt)
            await session.commit()

    def _default_timeout(self) -> float:
        """一次尝试的等待上限：``agent.copilot_attempt_timeout_minutes``（默认 20 分钟）。

        规格 §9.7 写死「最长 20 分钟」；设置项（1–60）是主 agent 加的运维旋钮，读不到或
        越界时回退 20 分钟。
        """
        try:
            from maa_api.settings import get_settings

            agent = getattr(get_settings(), "agent", None)
            minutes = getattr(agent, "copilot_attempt_timeout_minutes", None)
            if (
                isinstance(minutes, int)
                and not isinstance(minutes, bool)
                and 1 <= minutes <= 60
            ):
                return float(minutes) * 60.0
        except Exception:  # noqa: BLE001 - 设置读不到就用规格默认值
            pass
        return ATTEMPT_TIMEOUT_SECONDS

    def _now(self) -> datetime:
        moment = self._now_provider()
        return moment.replace(tzinfo=None) if moment.tzinfo else moment
