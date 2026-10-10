"""作业授权（M29，规格 §9.6、附录 E）：``grant_copilot_attempts`` 的状态机。

一次授权说的是「这个会话、这一关、几次尝试、什么算成功、到什么时候有效」。它只对
**INTERNAL 调用方**生效（策略分支在 ``agent/policy.py``，本模块不判断调用方）：

- ``run_copilot_attempt`` 与含 Copilot 任务的 ``submit_pipeline`` 必须先有授权；
- 没有授权时抛 ``CONFIRMATION_REQUIRED``，``details`` 里带
  ``confirmation_action="grant_copilot_attempts"`` 与预填参数（``request()`` 的产物），
  主 agent 的 policy 分支按它弹卡片；
- 关卡不符抛 ``COPILOT_GRANT_STAGE_MISMATCH``，次数用完抛 ``COPILOT_GRANT_EXHAUSTED``。

状态机（七态，规格 §9.6）::

    active ──成功──────▶ succeeded      （某次尝试达到 success 标准）
       │────次数用完────▶ exhausted      （最后一次也没成功）
       │────用户撤销────▶ revoked        （授权条 / DELETE 端点；新授权取代旧的也走这里）
       │────用户中断────▶ stopped        （M22 中断本轮，或理智不足 §9.7）
       │────有效期到────▶ expired        （剩余次数作废）
       └────服务重启────▶ interrupted    （mark_stale_active）

四条硬规则：

1. **有效期只在开始下一次尝试时检查**：已经在跑的那次跑完不因超时中断；过期判定发生在
   :meth:`CopilotGrantService.get_active` / :meth:`CopilotGrantService.consume`，
   两处都会把过期的 ``active`` 落到 ``expired``。
2. **每个会话同时最多一个 ``active``**：``copilot_grant`` 上的部分唯一索引兜底，服务层
   在批准新授权时先把旧的标 ``revoked``（同一事务），并发批准不会留下两条。
3. **``used`` 记「已开始的尝试」**：计数规则见规格 §9.7 ——
   ``sanity_insufficient`` / ``not_started`` / ``error`` / ``interrupted`` 不计次，
   由 :meth:`CopilotGrantService.refund` 退还。
4. **失败不静默**：状态机只在本模块一处实现；仓储不判断状态含义。

有效期时长取 ``agent.copilot_grant_minutes``（附录 B：默认 60，范围 10–120）。该设置项
由主 agent 加进 ``settings.py`` / ``settings_schema.py`` / ``config.template.yaml``；
在它落地之前，本模块用 ``getattr`` 容错读取并回退到 60，不因缺字段炸掉授权。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.db.models import CopilotGrant, utcnow
from maa_api.db.repositories.copilot import (
    GRANT_TERMINAL_STATUSES,
    CopilotGrantRepository,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import Settings, get_settings

__all__ = [
    "DEFAULT_ATTEMPTS",
    "DEFAULT_SUPPORT",
    "DEFAULT_SUCCESS",
    "DEFAULT_WINDOW_MINUTES",
    "GRANT_ACTION",
    "GRANT_STATUSES",
    "MAX_ATTEMPTS",
    "MAX_WINDOW_MINUTES",
    "MIN_ATTEMPTS",
    "MIN_WINDOW_MINUTES",
    "SUCCESS_CRITERIA",
    "SUPPORT_MODES",
    "TERMINAL_STATUSES",
    "CopilotGrantService",
    "grant_wire",
    "is_success",
    "session_wire",
]

logger = logging.getLogger(__name__)

#: 确认动作名（规格 §9.6）。
GRANT_ACTION = "grant_copilot_attempts"

#: 授权的七个状态（规格 §9.6）。
GRANT_STATUSES: tuple[str, ...] = (
    "active",
    "succeeded",
    "exhausted",
    "stopped",
    "expired",
    "revoked",
    "interrupted",
)

#: 终态（与仓储同一份常量，服务层再导出一遍给调用方）。
TERMINAL_STATUSES = frozenset(GRANT_TERMINAL_STATUSES)

#: 成功标准：``three_star``（默认）或 ``clear``（两星也算过）。
SUCCESS_CRITERIA: tuple[str, ...] = ("three_star", "clear")

#: 助战：``fill_one`` 缺一名时补位 / ``none`` 不允许助战。
SUPPORT_MODES: tuple[str, ...] = ("fill_one", "none")

DEFAULT_ATTEMPTS = 3
MIN_ATTEMPTS = 1
MAX_ATTEMPTS = 10

DEFAULT_SUCCESS = "three_star"
DEFAULT_SUPPORT = "fill_one"

DEFAULT_WINDOW_MINUTES = 60
#: 有效期范围。规格附录 B 写 10–120，但 ``agent.copilot_grant_minutes`` 的实现取值域
#: 放宽到 1–1440（设置页可配，见主 agent 在 docs/13 §8.44 记的偏离）：这里跟随设置，
#: 否则用户把窗口设成 240 分钟时服务会静默退回 60。
MIN_WINDOW_MINUTES = 1
MAX_WINDOW_MINUTES = 1440

#: 「这次结果算不算达标」用的结果集合（规格 §9.7 的 outcome）。
_THREE_STAR_OUTCOMES = frozenset({"three_star"})
_CLEAR_OUTCOMES = frozenset({"three_star", "two_star", "clear"})


def is_success(outcome: str, success: str) -> bool:
    """这次尝试的结果是否达到授权的成功标准（规格 §9.7 的「后续」列）。

    - 标准 ``three_star``：只有 ``three_star`` 算成功；
    - 标准 ``clear``：``three_star`` / ``two_star`` / ``clear`` 都算（“通关即可”）。
    """
    criterion = str(success or DEFAULT_SUCCESS)
    if criterion == "clear":
        return str(outcome) in _CLEAR_OUTCOMES
    return str(outcome) in _THREE_STAR_OUTCOMES


def _iso(value: Any) -> str | None:
    """wire 里的时间：ISO 8601 字符串（naive UTC 补成 +00:00，policy 侧按它算剩余秒数）。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.isoformat()
    return str(value)


def _iso_z(value: Any) -> str | None:
    """会话接口口径的 ISO 时间：UTC 用 ``Z`` 结尾（前端直接 ``new Date()``）。"""
    text = _iso(value)
    if text is None:
        return None
    return text.replace("+00:00", "Z")


def session_wire(row: CopilotGrant) -> dict[str, Any]:
    """``GET /api/agent/sessions/{id}`` 里的 ``copilot_grant``（与 ``atomic_grant`` 并列）。

    字段名是前端授权条读的那一套（``attempts_total`` / ``attempts_used``，附录 E 的原始
    命名），与工具/策略读的 :func:`grant_wire` 分开：两边的消费方不同，混用会让前端
    拿到 ``attempts``、工具拿到 ``attempts_total`` 而互相看不懂。
    """
    attempts = int(getattr(row, "attempts", 0) or 0)
    used = int(getattr(row, "used", 0) or 0)
    return {
        "grant_id": str(getattr(row, "id", "")),
        "stage_id": getattr(row, "stage_id", None),
        "stage_code": getattr(row, "stage_code", None),
        "attempts_total": attempts,
        "attempts_used": used,
        "success": str(getattr(row, "success", DEFAULT_SUCCESS)),
        "support": str(getattr(row, "support", DEFAULT_SUPPORT)),
        "status": str(getattr(row, "status", "")),
        "expires_at": _iso_z(getattr(row, "expires_at", None)),
    }


def grant_wire(row: CopilotGrant) -> dict[str, Any]:
    """授权行 → 工具 / 路由 / policy 读的形状（``remaining`` 由这里算一次）。"""
    attempts = int(getattr(row, "attempts", 0) or 0)
    used = int(getattr(row, "used", 0) or 0)
    return {
        "id": str(getattr(row, "id", "")),
        "session_id": getattr(row, "session_id", None),
        "stage_id": getattr(row, "stage_id", None),
        "stage_code": getattr(row, "stage_code", None),
        "status": str(getattr(row, "status", "")),
        "attempts": attempts,
        "used": used,
        "remaining": max(attempts - used, 0),
        "success": str(getattr(row, "success", DEFAULT_SUCCESS)),
        "support": str(getattr(row, "support", DEFAULT_SUPPORT)),
        "expires_at": _iso(getattr(row, "expires_at", None)),
        "created_at": _iso(getattr(row, "created_at", None)),
        "updated_at": _iso(getattr(row, "updated_at", None)),
        "finished_at": _iso(getattr(row, "finished_at", None)),
    }


class CopilotGrantService:
    """作业授权的读写与状态机；外部依赖（时钟、设置）可注入，便于测试。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | Callable[[], Any],
        *,
        settings_provider: Callable[[], Settings] = get_settings,
        now_provider: Callable[[], datetime] = utcnow,
        notifier: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings_provider = settings_provider
        self._now_provider = now_provider
        #: 授权进终态时的通知（规格 §9.9）。装配时接到 NotifyService + Web Push；
        #: 注入失败或未装配都不影响状态机本身。
        self._notifier = notifier
        #: 批准 / 消耗 / 退还串行化：一个进程内不做乐观重试，直接互斥。
        self._lock = asyncio.Lock()

    # -- 配置 -------------------------------------------------------------
    def window_minutes(self) -> int:
        """有效期时长（分钟）：``agent.copilot_grant_minutes``，缺失或越界回退 60。"""
        try:
            settings = self._settings_provider()
        except Exception:  # noqa: BLE001 - 设置读不到不该让授权不可用
            return DEFAULT_WINDOW_MINUTES
        agent = getattr(settings, "agent", None)
        value = getattr(agent, "copilot_grant_minutes", None)
        if isinstance(value, bool) or not isinstance(value, int):
            return DEFAULT_WINDOW_MINUTES
        if not MIN_WINDOW_MINUTES <= value <= MAX_WINDOW_MINUTES:
            return DEFAULT_WINDOW_MINUTES
        return value

    def _now(self) -> datetime:
        moment = self._now_provider()
        return moment.replace(tzinfo=None) if moment.tzinfo else moment

    @staticmethod
    def _window_minutes(value: Any) -> int:
        if value is None:
            return 0
        if isinstance(value, bool) or not isinstance(value, int):
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                "有效期必须是整数分钟",
                {"window_minutes": value},
            )
        if not MIN_WINDOW_MINUTES <= value <= MAX_WINDOW_MINUTES:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"有效期必须在 {MIN_WINDOW_MINUTES}–{MAX_WINDOW_MINUTES} 分钟之间",
                {"window_minutes": value},
            )
        return value

    # -- 预填参数 ---------------------------------------------------------
    def request(
        self,
        *,
        stage_id: str,
        stage_code: str | None = None,
        session_id: str | None = None,
        attempts: Any = None,
        success: Any = None,
        support: Any = None,
        window_minutes: Any = None,
        tool_name: str | None = None,
    ) -> dict[str, Any]:
        """没有授权时给确认卡片的预填参数（规格 §9.6 的 payload）。

        与 policy 的同名逻辑一样是**收敛口径**：越界或未知取值一律回退默认，卡片上
        不会出现「0 次」「two_star」这种非法选择。有效期只展示，不在卡片上改。
        """
        try:
            minutes = self._window_minutes(window_minutes)
        except AppError:
            minutes = 0
        window = minutes or self.window_minutes()
        return {
            "action": GRANT_ACTION,
            "session_id": session_id,
            "stage_id": stage_id,
            "stage_code": stage_code,
            "attempts": _clamp_attempts(attempts),
            "success": success if success in SUCCESS_CRITERIA else DEFAULT_SUCCESS,
            "support": support if support in SUPPORT_MODES else DEFAULT_SUPPORT,
            "window_minutes": window,
            "expires_at": _iso(self._now() + timedelta(minutes=window)),
            "tool_name": tool_name,
        }

    # -- 读 ---------------------------------------------------------------
    async def get_active(self, session_id: str | None) -> dict[str, Any] | None:
        """当前会话的有效授权；没有、已过期（顺手落 ``expired``）都返回 ``None``。

        ``agent/policy.py`` 的 ``_active_copilot_grant`` 直接读这个字典的
        ``id`` / ``stage_id`` / ``attempts`` / ``used`` / ``expires_at``。
        """
        if not session_id:
            return None
        async with self._lock:
            return await self._active_wire(session_id)

    async def session_wire(self, session_id: str | None) -> dict[str, Any] | None:
        """会话接口里的 ``copilot_grant``（过期的授权顺势落 ``expired`` 并返回 ``None``）。"""
        if not session_id:
            return None
        async with self._lock:
            async with self._session_factory() as session:
                repo = CopilotGrantRepository(session)
                row = await repo.active_for_session(session_id)
                if row is None:
                    return None
                now = self._now()
                if _is_expired(row, now):
                    await repo.update(row.id, status="expired", finished_at=now)
                    await session.commit()
                    return None
                return session_wire(row)

    async def history(self, session_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """会话的授权历史，新的在前（前端授权区与排障用）。"""
        async with self._session_factory() as session:
            rows = await CopilotGrantRepository(session).by_session(
                session_id, limit=limit
            )
        return [grant_wire(row) for row in rows]

    # -- 批准 -------------------------------------------------------------
    async def approve(
        self,
        session_id: str,
        *,
        stage_id: str,
        stage_code: str | None = None,
        attempts: Any = None,
        success: Any = None,
        support: Any = None,
        window_minutes: Any = None,
        grant_id: str | None = None,
        overrides: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """批准一次作业授权（用户可改尝试次数 / 成功标准 / 是否允许助战）。

        ``grant_id`` 就是那条确认的 id（附录 E 的「id（= 确认 id）」）：同一条确认重复
        批准只留一行（幂等），因此确认重放不会多出一条授权。

        ``overrides`` 是确认接口给用户改的三项（规格 §9.6 末：确认接口为这个动作接受
        ``attempts`` / ``success`` / ``support`` 的覆盖参数）；它以关键字参数为准、
        只认这三项，其它键忽略（``retain`` 这类拼错的键不该改到授权上）。
        """
        if overrides:
            if attempts is None and "attempts" in overrides:
                attempts = overrides["attempts"]
            if success is None and "success" in overrides:
                success = overrides["success"]
            if support is None and "support" in overrides:
                support = overrides["support"]
        resolved_attempts = _require_attempts(attempts)
        resolved_success = _require_choice(success, SUCCESS_CRITERIA, "success")
        resolved_support = _require_choice(support, SUPPORT_MODES, "support")
        minutes = self._window_minutes(window_minutes) or self.window_minutes()
        if not str(stage_id or "").strip():
            raise AppError(
                ErrorCode.INVALID_PARAMETER, "授权必须指定关卡", {"stage_id": stage_id}
            )
        now = self._now()
        expires_at = now + timedelta(minutes=minutes)

        async with self._lock:
            async with self._session_factory() as session:
                repo = CopilotGrantRepository(session)
                if grant_id:
                    existing = await repo.get(grant_id)
                    if existing is not None:
                        row = await repo.update(
                            grant_id,
                            status="active",
                            stage_id=str(stage_id).strip(),
                            stage_code=stage_code,
                            attempts=resolved_attempts,
                            success=resolved_success,
                            support=resolved_support,
                            expires_at=expires_at,
                            finished_at=None,
                        )
                        await session.commit()
                        return grant_wire(row or existing)
                superseded = await repo.active_for_session(session_id)
                if superseded is not None:
                    await repo.update(
                        superseded.id,
                        status="revoked",
                        finished_at=now,
                    )
                row = CopilotGrant(
                    id=grant_id or _new_uuid(),
                    session_id=session_id,
                    stage_id=str(stage_id).strip(),
                    stage_code=stage_code,
                    status="active",
                    attempts=resolved_attempts,
                    used=0,
                    success=resolved_success,
                    support=resolved_support,
                    expires_at=expires_at,
                    created_at=now,
                    updated_at=now,
                )
                created = await repo.create(row)
                await session.commit()
                stored = await repo.get(created.id) or created
        return grant_wire(stored)

    # -- 消耗与退还 -------------------------------------------------------
    async def consume(
        self,
        session_id: str | None,
        stage_id: str,
        *,
        stage_code: str | None = None,
        prefill: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """开始下一次尝试：校验有效期、关卡与剩余次数，``used`` 加一。

        返回 ``{"grant": wire, "attempt_number": int, "remaining": int}``。
        三种拒绝口径与规格 §9.6 一致：

        - 没有有效授权（含已过期）→ ``CONFIRMATION_REQUIRED`` + 预填参数；
        - 关卡不符 → ``COPILOT_GRANT_STAGE_MISMATCH``；
        - 次数用完 → ``COPILOT_GRANT_EXHAUSTED``（顺手把授权标 ``exhausted``）。
        """
        if not session_id:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                "作业授权只对内置会话生效，缺少 session_id",
                {"stage_id": stage_id},
            )
        async with self._lock:
            async with self._session_factory() as session:
                repo = CopilotGrantRepository(session)
                row = await repo.active_for_session(session_id)
                now = self._now()
                if row is None or _is_expired(row, now):
                    if row is not None:
                        await repo.update(row.id, status="expired", finished_at=now)
                        await session.commit()
                    raise self._needs_grant(
                        session_id=session_id,
                        stage_id=stage_id,
                        stage_code=stage_code,
                        prefill=prefill,
                    )
                if not _stage_matches(row, stage_id, stage_code):
                    raise AppError(
                        ErrorCode.COPILOT_GRANT_STAGE_MISMATCH,
                        f"授权只覆盖关卡 {row.stage_code or row.stage_id}，"
                        f"不能用于 {stage_code or stage_id}",
                        {
                            "grant_id": row.id,
                            "grant_stage_id": row.stage_id,
                            "grant_stage_code": row.stage_code,
                            "stage_id": stage_id,
                            "stage_code": stage_code,
                        },
                    )
                if int(row.used) >= int(row.attempts):
                    await repo.update(row.id, status="exhausted", finished_at=now)
                    await session.commit()
                    raise AppError(
                        ErrorCode.COPILOT_GRANT_EXHAUSTED,
                        "本次作业授权的尝试次数已用完，需要重新申请",
                        {"grant_id": row.id, "attempts": int(row.attempts)},
                    )
                updated = await repo.update(row.id, used=int(row.used) + 1)
                await session.commit()
                stored = updated or row
                wire = grant_wire(stored)
        return {
            "grant": wire,
            "attempt_number": wire["used"],
            "remaining": wire["remaining"],
        }

    async def refund(self, session_id: str | None, *, reason: str = "") -> dict[str, Any] | None:
        """退还一次「不计次」的尝试（规格 §9.7：理智不足 / 没开打 / 出错 / 被中断）。

        只对仍然 ``active`` 的授权生效；没有可退的就返回 ``None``。
        """
        if not session_id:
            return None
        async with self._lock:
            async with self._session_factory() as session:
                repo = CopilotGrantRepository(session)
                row = await repo.active_for_session(session_id)
                if row is None or int(row.used) <= 0:
                    return None
                updated = await repo.update(row.id, used=int(row.used) - 1)
                await session.commit()
                stored = updated or row
        if reason:
            logger.info(
                "作业授权退还一次尝试：grant_id=%s，原因=%s", stored.id, reason
            )
        return grant_wire(stored)

    # -- 终态 -------------------------------------------------------------
    async def revoke(self, session_id: str | None) -> dict[str, Any] | None:
        """撤销当前有效授权（前端授权条 / ``DELETE /copilot-grant``）。"""
        return await self._terminate(session_id, "revoked", reason="用户撤销作业授权")

    async def stop(self, session_id: str | None, reason: str = "") -> dict[str, Any] | None:
        """停止授权：用户中断本轮（M22），或理智不足（规格 §9.7）。"""
        return await self._terminate(session_id, "stopped", reason=reason)

    async def finish(self, session_id: str | None, status: str) -> dict[str, Any] | None:
        """把授权落成一个终态（``succeeded`` / ``exhausted`` …）。"""
        if status not in TERMINAL_STATUSES:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"不是授权的终态：{status}",
                {"status": status, "allowed": sorted(TERMINAL_STATUSES)},
            )
        return await self._terminate(session_id, status, reason="")

    async def _terminate(
        self, session_id: str | None, status: str, *, reason: str
    ) -> dict[str, Any] | None:
        if not session_id:
            return None
        async with self._lock:
            async with self._session_factory() as session:
                repo = CopilotGrantRepository(session)
                row = await repo.active_for_session(session_id)
                if row is None:
                    return None
                updated = await repo.update(
                    row.id, status=status, finished_at=self._now()
                )
                await session.commit()
                stored = updated or row
        if reason:
            logger.info(
                "作业授权进入终态：grant_id=%s，status=%s，原因=%s",
                stored.id,
                status,
                reason,
            )
        await self._notify_terminal(stored)
        return grant_wire(stored)

    # -- 通知 -------------------------------------------------------------
    async def _notify_terminal(self, row: Any) -> None:
        """授权进终态后发 `copilot_research_finished`（规格 §9.9）。

        通知失败只记日志：授权状态已经落库，不该因为通道不可用而回滚。
        """
        notifier = self._notifier
        if notifier is None:
            return
        try:
            from maa_api.services.copilot.notify import (
                build_copilot_research_notification,
            )

            event, payload = build_copilot_research_notification(
                str(getattr(row, "status", "") or ""),
                stage_id=str(getattr(row, "stage_id", "") or ""),
                stage_code=str(getattr(row, "stage_code", "") or ""),
                attempts_used=int(getattr(row, "used", 0) or 0),
                attempts_total=int(getattr(row, "attempts", 0) or 0),
                session_id=str(getattr(row, "session_id", "") or ""),
            )
            result = notifier({"event": event, "payload": payload})
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - 通知不能影响授权状态机
            logger.warning("作业授权终态通知发送失败", exc_info=True)

    # -- 启动清扫 ---------------------------------------------------------
    async def mark_stale_active(self) -> list[dict[str, Any]]:
        """服务重启：把仍为 ``active`` 的授权全标 ``interrupted``（规格 §9.6 末）。

        返回被改动的授权 wire（调用方据此发通知；通知本身在
        ``services/copilot/notify.py`` 与主 agent 的装配里）。
        """
        now = self._now()
        changed: list[dict[str, Any]] = []
        async with self._lock:
            async with self._session_factory() as session:
                repo = CopilotGrantRepository(session)
                rows = await repo.all_active()
                for row in rows:
                    updated = await repo.update(
                        row.id,
                        status="interrupted",
                        finished_at=now,
                    )
                    changed.append(grant_wire(updated or row))
                await session.commit()
        if changed:
            logger.info("服务重启：%d 条作业授权被标为 interrupted", len(changed))
        # 规格 §9.6 末：重启作废的授权要发通知。
        for wire in changed:
            try:
                async with self._session_factory() as session:
                    row = await CopilotGrantRepository(session).get(str(wire["id"]))
            except Exception:  # noqa: BLE001 - 通知失败不影响清扫
                row = None
            if row is not None:
                await self._notify_terminal(row)
        return changed

    async def expire_due(self) -> list[dict[str, Any]]:
        """把有效期已过的 ``active`` 标 ``expired``（清扫任务用），返回改动过的授权。"""
        now = self._now()
        changed: list[dict[str, Any]] = []
        async with self._lock:
            async with self._session_factory() as session:
                repo = CopilotGrantRepository(session)
                rows = await repo.all_active()
                for row in rows:
                    if not _is_expired(row, now):
                        continue
                    updated = await repo.update(
                        row.id, status="expired", finished_at=now
                    )
                    changed.append(grant_wire(updated or row))
                await session.commit()
        return changed

    # -- 内部 -------------------------------------------------------------
    async def _active_wire(self, session_id: str) -> dict[str, Any] | None:
        """有效授权的 wire；过期的顺手落 ``expired`` 并当作没有授权。"""
        async with self._session_factory() as session:
            repo = CopilotGrantRepository(session)
            row = await repo.active_for_session(session_id)
            if row is None:
                return None
            now = self._now()
            if _is_expired(row, now):
                await repo.update(row.id, status="expired", finished_at=now)
                await session.commit()
                return None
            return grant_wire(row)

    def _needs_grant(
        self,
        *,
        session_id: str,
        stage_id: str,
        stage_code: str | None,
        prefill: Mapping[str, Any] | None = None,
    ) -> AppError:
        """「没有授权」的统一错误：``CONFIRMATION_REQUIRED`` + 卡片预填参数。

        ``prefill`` 是调用方（例如 ``run_copilot_attempt`` 的 ``grant_attempts`` /
        ``success``）希望带上卡片的取值；仍然走 :meth:`request` 的收敛口径，不直接透传。
        """
        extra = prefill or {}
        payload = self.request(
            session_id=session_id,
            stage_id=stage_id,
            stage_code=stage_code,
            attempts=extra.get("attempts"),
            success=extra.get("success"),
            support=extra.get("support"),
        )
        return AppError(
            ErrorCode.CONFIRMATION_REQUIRED,
            "执行作业尝试需要先申请授权",
            {**payload, "confirmation_action": GRANT_ACTION},
        )


def _clamp_attempts(value: Any) -> int:
    """预填口径：非法或越界的次数收敛到默认 3，而不是抛错。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_ATTEMPTS
    return max(MIN_ATTEMPTS, min(int(value), MAX_ATTEMPTS))


def _require_attempts(value: Any) -> int:
    """批准口径：非法次数是调用方的错，抛 400。"""
    if value is None:
        return DEFAULT_ATTEMPTS
    if isinstance(value, bool) or not isinstance(value, int):
        raise AppError(
            ErrorCode.INVALID_PARAMETER, "尝试次数必须是整数", {"attempts": value}
        )
    if not MIN_ATTEMPTS <= value <= MAX_ATTEMPTS:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"尝试次数必须在 {MIN_ATTEMPTS}–{MAX_ATTEMPTS} 之间",
            {"attempts": value},
        )
    return int(value)


def _require_choice(value: Any, allowed: tuple[str, ...], field: str) -> str:
    if value is None:
        return allowed[0]
    if value not in allowed:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"{field} 只能是 {' / '.join(allowed)}",
            {field: value},
        )
    return str(value)


def _is_expired(row: CopilotGrant, now: datetime) -> bool:
    expires_at = getattr(row, "expires_at", None)
    if expires_at is None:
        return False
    if expires_at.tzinfo is not None:
        expires_at = expires_at.replace(tzinfo=None)
    return expires_at <= now


def _stage_matches(row: CopilotGrant, stage_id: str | None, stage_code: str | None) -> bool:
    """关卡是否与授权一致：stageId 与 code 任一相等即算同一关（作业可能只给得出一个）。"""
    granted = {
        str(value).strip()
        for value in (getattr(row, "stage_id", None), getattr(row, "stage_code", None))
        if value
    }
    asked = {
        str(value).strip() for value in (stage_id, stage_code) if value
    }
    if not asked:
        return False
    return bool(granted & asked)


def _new_uuid() -> str:
    """授权行的主键：没有确认 id 时（例如 REST 直批）新生成一个 36 字符 UUID。"""
    return str(uuid.uuid4())
