"""作业授权服务的生命周期契约（M29 规格 §9.6、附录 E）。

全部离线：临时 SQLite（``make_engine`` + ``create_all`` + ``NullPool``，让同一个 engine
被用例里多个 ``asyncio.run`` 复用），时间用手工构造的 naive UTC 值，因此「过期」不需要
冻结时钟。覆盖规格 §9.6 点名的六种终态与三条硬规则：

- 批准（含用户在卡片上改尝试次数 / 成功标准 / 是否允许助战）；
- 有效期只在**开始下一次尝试**时检查，过期后剩余次数作废；
- 每个会话同时最多一个 ``active``（数据库部分唯一索引兜底）；
- 撤销、用完、成功、重启标 ``interrupted``；
- 不计次的尝试退还次数（``refund``，规格 §9.7 的计数规则）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表
from maa_api.db.session import make_engine
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.copilot.grant import (
    DEFAULT_ATTEMPTS,
    DEFAULT_SUPPORT,
    DEFAULT_WINDOW_MINUTES,
    GRANT_ACTION,
    CopilotGrantService,
    grant_wire,
    is_success,
)

NOW = datetime(2026, 10, 10, 3, 0, 0)


@pytest.fixture
def grant_session_factory(tmp_path):
    """临时库 + ``NullPool`` 会话工厂（跨多个 ``asyncio.run`` 复用同一个 engine）。"""
    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'copilot_grant.db'}", poolclass=NullPool
    )

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_schema())
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    engine.sync_engine.dispose()


def _service(factory, *, minutes: int | None = None) -> CopilotGrantService:
    """固定「现在」与有效期的服务实例（不读真实 config.yaml 的 agent 段）。"""

    class _Agent:
        copilot_grant_minutes = minutes

    class _Settings:
        agent = _Agent()

    service = CopilotGrantService(
        factory,
        settings_provider=lambda: _Settings(),
        now_provider=lambda: NOW,
    )
    return service


def _seed_session(factory, session_id: str) -> None:
    """``copilot_grant.session_id`` 是指向 ``agent_session`` 的外键：先建会话行。

    生产里授权总是从某个真实会话的确认卡片批准出来的，所以这个前置条件与线上一致；
    测试直接写库时必须自己补上，否则撞的是外键而不是业务逻辑。
    """
    from maa_api.db.models import AgentSession

    async def scenario():
        async with factory() as session:
            if await session.get(AgentSession, session_id) is None:
                session.add(AgentSession(id=session_id, model="gpt-test"))
                await session.commit()

    asyncio.run(scenario())


def _approve(factory, *, session_id="session-1", stage_id="main_01-07", **kwargs):
    """同步跑一次批准，返回 wire。"""
    _seed_session(factory, session_id)

    async def scenario():
        service = kwargs.pop("service", None) or _service(factory)
        return await service.approve(
            session_id,
            stage_id=stage_id,
            stage_code=kwargs.pop("stage_code", "1-7"),
            **kwargs,
        )

    return asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 批准与预填参数
# ---------------------------------------------------------------------------
def test_request_builds_confirmation_prefill(grant_session_factory):
    """没有授权时给确认卡片的预填参数（规格 §9.6 的 payload）。"""
    service = _service(grant_session_factory)
    payload = service.request(
        session_id="session-1",
        stage_id="main_01-07",
        stage_code="1-7",
        attempts=5,
        success="clear",
        support="none",
    )
    assert payload["action"] == GRANT_ACTION
    assert payload["session_id"] == "session-1"
    assert payload["stage_id"] == "main_01-07"
    assert payload["stage_code"] == "1-7"
    assert payload["attempts"] == 5
    assert payload["success"] == "clear"
    assert payload["support"] == "none"
    assert payload["window_minutes"] == DEFAULT_WINDOW_MINUTES
    # 越界与未知取值收敛：次数夹到 1–10（与 policy 同一口径），枚举回退默认。
    fallback = service.request(
        session_id="session-1",
        stage_id="main_01-07",
        attempts=99,
        success="two_star",
        support="random",
    )
    assert fallback["attempts"] == 10
    assert fallback["success"] == "three_star"
    assert fallback["support"] == DEFAULT_SUPPORT
    empty = service.request(
        session_id="session-1", stage_id="main_01-07", attempts="many"
    )
    assert empty["attempts"] == DEFAULT_ATTEMPTS


def test_approve_creates_active_grant_and_defaults(grant_session_factory):
    wire = _approve(grant_session_factory)
    assert wire["status"] == "active"
    assert wire["attempts"] == DEFAULT_ATTEMPTS
    assert wire["used"] == 0
    assert wire["remaining"] == DEFAULT_ATTEMPTS
    assert wire["success"] == "three_star"
    assert wire["support"] == DEFAULT_SUPPORT
    assert wire["stage_id"] == "main_01-07"
    assert wire["stage_code"] == "1-7"
    assert wire["finished_at"] is None
    expires = datetime.fromisoformat(wire["expires_at"]).replace(tzinfo=None)
    assert expires - NOW == timedelta(minutes=DEFAULT_WINDOW_MINUTES)


def test_approve_accepts_the_users_overrides(grant_session_factory):
    """用户在卡片上改尝试次数、成功标准与是否允许助战（规格 §9.6）。"""
    wire = _approve(
        grant_session_factory, attempts=7, success="clear", support="none"
    )
    assert (wire["attempts"], wire["success"], wire["support"]) == (7, "clear", "none")


def test_approve_rejects_invalid_overrides(grant_session_factory):
    async def scenario(case):
        service = _service(grant_session_factory)
        with pytest.raises(AppError) as info:
            await service.approve("session-1", stage_id="main_01-07", **case)
        assert info.value.code == ErrorCode.INVALID_PARAMETER

    for case in (
        {"attempts": 0},
        {"attempts": 11},
        {"success": "two_star"},
        {"support": "maybe"},
        # 有效期范围跟随 agent.copilot_grant_minutes 的实现取值域 1–1440。
        {"window_minutes": 0},
        {"window_minutes": 1441},
    ):
        asyncio.run(scenario(case))


def test_approve_is_idempotent_for_the_same_confirmation(grant_session_factory):
    """``id`` 就是确认 id：同一条确认重复落库只留一行（附录 E）。"""
    first = _approve(grant_session_factory, grant_id="confirmation-1")
    second = _approve(grant_session_factory, grant_id="confirmation-1", attempts=9)
    assert first["id"] == second["id"] == "confirmation-1"
    assert second["attempts"] == 9


def test_a_new_grant_supersedes_the_previous_active_one(grant_session_factory):
    """每个会话同时最多一个 active：旧的被取代并进终态（规格 §9.6）。"""
    first = _approve(grant_session_factory, grant_id="confirmation-1")
    second = _approve(grant_session_factory, grant_id="confirmation-2")

    async def scenario():
        service = _service(grant_session_factory)
        rows = await service.history("session-1")
        return rows

    rows = asyncio.run(scenario())
    assert [row["id"] for row in rows] == ["confirmation-2", "confirmation-1"]
    assert rows[0]["status"] == "active"
    assert rows[1]["status"] == "revoked"
    assert rows[1]["finished_at"] is not None
    assert first["id"] == "confirmation-1" and second["id"] == "confirmation-2"


def test_only_one_active_row_survives_at_the_database_level(grant_session_factory):
    """部分唯一索引兜底：绕过服务直接插第二条 active 会被拒绝（规格 §9.6）。"""
    from sqlalchemy.exc import IntegrityError

    from maa_api.db.models import CopilotGrant

    _approve(grant_session_factory, grant_id="confirmation-1")

    async def scenario():
        async with grant_session_factory() as session:
            session.add(
                CopilotGrant(
                    id="confirmation-2",
                    session_id="session-1",
                    stage_id="main_01-07",
                    expires_at=NOW + timedelta(minutes=30),
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 消耗、有效期与次数
# ---------------------------------------------------------------------------
def test_get_active_is_none_without_a_grant(grant_session_factory):
    async def scenario():
        service = _service(grant_session_factory)
        return await service.get_active("session-1")

    assert asyncio.run(scenario()) is None


def test_get_active_returns_the_wire_shape_policy_reads(grant_session_factory):
    """``policy._active_copilot_grant`` 按字典读 id/attempts/used/stage_id/expires_at。"""
    _approve(grant_session_factory, grant_id="confirmation-1", attempts=4)

    async def scenario():
        service = _service(grant_session_factory)
        return await service.get_active("session-1")

    wire = asyncio.run(scenario())
    assert wire["id"] == "confirmation-1"
    assert wire["attempts"] == 4 and wire["used"] == 0 and wire["remaining"] == 4
    assert wire["stage_id"] == "main_01-07" and wire["stage_code"] == "1-7"
    assert isinstance(wire["expires_at"], str) and wire["expires_at"]


def test_consume_counts_an_attempt_and_reports_the_number(grant_session_factory):
    _approve(grant_session_factory, grant_id="confirmation-1", attempts=3)

    async def scenario():
        service = _service(grant_session_factory)
        first = await service.consume("session-1", "main_01-07", stage_code="1-7")
        second = await service.consume("session-1", "main_01-07", stage_code="1-7")
        return first, second

    first, second = asyncio.run(scenario())
    assert first["attempt_number"] == 1 and first["remaining"] == 2
    assert second["attempt_number"] == 2 and second["remaining"] == 1
    assert second["grant"]["used"] == 2
    assert second["grant"]["status"] == "active"


def test_consume_still_runs_after_the_deadline_once_started(grant_session_factory):
    """有效期只在开始下一次尝试时检查：已经开始的那次跑完不中断（规格 §9.6）。"""
    _approve(grant_session_factory, grant_id="confirmation-1", attempts=2)

    async def scenario():
        service = _service(grant_session_factory)
        await service.consume("session-1", "main_01-07")
        # 时钟推进到有效期之后，但这一次已经在跑了。
        later = CopilotGrantService(
            grant_session_factory,
            now_provider=lambda: NOW + timedelta(hours=2),
        )
        finished = await later.finish("session-1", "succeeded")
        return finished

    finished = asyncio.run(scenario())
    assert finished["status"] == "succeeded"


def test_expired_grant_is_marked_and_refuses_the_next_attempt(grant_session_factory):
    """次数没用完但有效期到了：标 expired、剩余次数作废、下一次要重新申请。"""
    _approve(grant_session_factory, grant_id="confirmation-1", attempts=10)

    async def scenario():
        later = CopilotGrantService(
            grant_session_factory,
            now_provider=lambda: NOW + timedelta(minutes=DEFAULT_WINDOW_MINUTES + 1),
        )
        active = await later.get_active("session-1")
        with pytest.raises(AppError) as info:
            await later.consume("session-1", "main_01-07")
        rows = await later.history("session-1")
        return active, info.value, rows

    active, error, rows = asyncio.run(scenario())
    assert active is None
    assert error.code == ErrorCode.CONFIRMATION_REQUIRED
    assert error.details["confirmation_action"] == GRANT_ACTION
    assert rows[0]["status"] == "expired"
    assert rows[0]["remaining"] == 10  # 展示口径：剩下的次数仍可读出来，但已作废


def test_consume_without_any_grant_asks_for_a_new_one(grant_session_factory):
    async def scenario():
        service = _service(grant_session_factory)
        with pytest.raises(AppError) as info:
            await service.consume("session-1", "main_01-07")
        return info.value

    error = asyncio.run(scenario())
    assert error.code == ErrorCode.CONFIRMATION_REQUIRED
    assert error.details["confirmation_action"] == GRANT_ACTION
    assert error.details["stage_id"] == "main_01-07"


def test_consume_on_another_stage_is_a_stage_mismatch(grant_session_factory):
    _approve(grant_session_factory, grant_id="confirmation-1", stage_id="main_01-07")

    async def scenario():
        service = _service(grant_session_factory)
        with pytest.raises(AppError) as info:
            await service.consume("session-1", "main_02-01", stage_code="2-1")
        return info.value

    error = asyncio.run(scenario())
    assert error.code == ErrorCode.COPILOT_GRANT_STAGE_MISMATCH
    assert error.details["grant_stage_id"] == "main_01-07"
    assert error.details["stage_id"] == "main_02-01"


def test_stage_matches_when_either_id_or_code_is_equal(grant_session_factory):
    """作业只给得出 stage_code 时也能对上同一个关卡（授权存了两个标识）。"""
    _approve(
        grant_session_factory,
        grant_id="confirmation-1",
        stage_id="main_01-07",
        stage_code="1-7",
    )

    async def scenario():
        service = _service(grant_session_factory)
        by_code = await service.consume("session-1", "1-7")
        return by_code

    assert asyncio.run(scenario())["grant"]["used"] == 1


def test_exhausted_grant_refuses_the_next_attempt(grant_session_factory):
    _approve(grant_session_factory, grant_id="confirmation-1", attempts=1)

    async def scenario():
        service = _service(grant_session_factory)
        await service.consume("session-1", "main_01-07")
        with pytest.raises(AppError) as info:
            await service.consume("session-1", "main_01-07")
        rows = await service.history("session-1")
        return info.value, rows[0]

    error, row = asyncio.run(scenario())
    assert error.code == ErrorCode.COPILOT_GRANT_EXHAUSTED
    assert row["status"] == "exhausted"
    assert row["finished_at"] is not None


def test_refund_gives_back_an_uncounted_attempt(grant_session_factory):
    """``sanity_insufficient`` / ``not_started`` / ``error`` / ``interrupted`` 不计次。"""
    _approve(grant_session_factory, grant_id="confirmation-1", attempts=2)

    async def scenario():
        service = _service(grant_session_factory)
        consumed = await service.consume("session-1", "main_01-07")
        refunded = await service.refund("session-1", reason="理智不足")
        return consumed, refunded

    consumed, refunded = asyncio.run(scenario())
    assert consumed["grant"]["used"] == 1
    assert refunded["used"] == 0 and refunded["remaining"] == 2


def test_refund_without_a_grant_is_a_noop(grant_session_factory):
    async def scenario():
        service = _service(grant_session_factory)
        return await service.refund("session-1", reason="没有授权")

    assert asyncio.run(scenario()) is None


# ---------------------------------------------------------------------------
# 终态：撤销 / 停止 / 成功 / 重启
# ---------------------------------------------------------------------------
def test_revoke_marks_the_active_grant_revoked(grant_session_factory):
    _approve(grant_session_factory, grant_id="confirmation-1")

    async def scenario():
        service = _service(grant_session_factory)
        revoked = await service.revoke("session-1")
        again = await service.revoke("session-1")
        active = await service.get_active("session-1")
        return revoked, again, active

    revoked, again, active = asyncio.run(scenario())
    assert revoked["status"] == "revoked" and revoked["finished_at"] is not None
    assert again is None
    assert active is None


def test_stop_marks_the_grant_stopped(grant_session_factory):
    _approve(grant_session_factory, grant_id="confirmation-1")

    async def scenario():
        service = _service(grant_session_factory)
        return await service.stop("session-1", "用户中断本轮")

    stopped = asyncio.run(scenario())
    assert stopped["status"] == "stopped"
    assert stopped["finished_at"] is not None


def test_finish_sets_success(grant_session_factory):
    _approve(grant_session_factory, grant_id="confirmation-1")

    async def scenario():
        service = _service(grant_session_factory)
        return await service.finish("session-1", "succeeded")

    finished = asyncio.run(scenario())
    assert finished["status"] == "succeeded"
    assert finished["finished_at"] is not None


def test_finish_rejects_unknown_status(grant_session_factory):
    _approve(grant_session_factory, grant_id="confirmation-1")

    async def scenario():
        service = _service(grant_session_factory)
        with pytest.raises(AppError) as info:
            await service.finish("session-1", "active")
        return info.value

    assert asyncio.run(scenario()).code == ErrorCode.INVALID_PARAMETER


def test_mark_stale_active_interrupts_every_active_grant(grant_session_factory):
    """服务重启：仍为 active 的授权标 interrupted（规格 §9.6 末），终态行不动。"""
    _approve(grant_session_factory, grant_id="confirmation-1", session_id="session-1")
    _approve(grant_session_factory, grant_id="confirmation-2", session_id="session-2")

    async def scenario():
        service = _service(grant_session_factory)
        await service.revoke("session-2")
        changed = await service.mark_stale_active()
        rows = await service.history("session-1")
        return changed, rows

    changed, rows = asyncio.run(scenario())
    assert [item["id"] for item in changed] == ["confirmation-1"]
    assert changed[0]["status"] == "interrupted"
    assert rows[0]["status"] == "interrupted"
    assert rows[0]["finished_at"] is not None


def test_expire_due_sweeps_only_past_deadline_rows(grant_session_factory):
    _approve(grant_session_factory, grant_id="confirmation-1", session_id="session-1")
    _approve(grant_session_factory, grant_id="confirmation-2", session_id="session-2")

    async def scenario():
        later = CopilotGrantService(
            grant_session_factory,
            now_provider=lambda: NOW + timedelta(minutes=90),
        )
        await later.revoke("session-2")
        return await later.expire_due()

    expired = asyncio.run(scenario())
    assert [item["id"] for item in expired] == ["confirmation-1"]


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------
def test_is_success_follows_the_grant_criterion():
    assert is_success("three_star", "three_star") is True
    assert is_success("clear", "three_star") is False
    assert is_success("two_star", "three_star") is False
    assert is_success("three_star", "clear") is True
    assert is_success("two_star", "clear") is True
    assert is_success("clear", "clear") is True
    assert is_success("failed", "clear") is False
    assert is_success("sanity_insufficient", "clear") is False


def test_approve_accepts_the_confirmation_overrides(grant_session_factory):
    """确认接口的 overrides（规格 §9.6 末）：只认三项，其它键忽略。"""
    wire = _approve(
        grant_session_factory,
        grant_id="confirmation-1",
        overrides={"attempts": 5, "success": "clear", "support": "none", "retain": True},
    )
    assert (wire["attempts"], wire["success"], wire["support"]) == (5, "clear", "none")


def test_session_wire_matches_the_frontend_shape(grant_session_factory):
    """会话接口的 ``copilot_grant`` 形状（前端授权条读它，字段名照附录 E）。"""
    _approve(grant_session_factory, grant_id="confirmation-1", attempts=4)

    async def scenario():
        service = _service(grant_session_factory)
        return await service.session_wire("session-1")

    wire = asyncio.run(scenario())
    assert wire == {
        "grant_id": "confirmation-1",
        "stage_id": "main_01-07",
        "stage_code": "1-7",
        "attempts_total": 4,
        "attempts_used": 0,
        "success": "three_star",
        "support": "fill_one",
        "status": "active",
        "expires_at": wire["expires_at"],
    }
    assert wire["expires_at"].endswith("Z")


def test_session_wire_hides_expired_grants(grant_session_factory):
    _approve(grant_session_factory, grant_id="confirmation-1")

    async def scenario():
        later = CopilotGrantService(
            grant_session_factory,
            now_provider=lambda: NOW + timedelta(hours=3),
        )
        return await later.session_wire("session-1"), await later.history("session-1")

    wire, rows = asyncio.run(scenario())
    assert wire is None
    assert rows[0]["status"] == "expired"


def test_grant_wire_renders_timestamps_as_iso_strings(grant_session_factory):
    from maa_api.db.models import CopilotGrant

    row = CopilotGrant(
        id="confirmation-1",
        session_id="session-1",
        stage_id="main_01-07",
        expires_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    wire = grant_wire(row)
    assert wire["created_at"] == NOW.replace(tzinfo=timezone.utc).isoformat()
    assert wire["finished_at"] is None
