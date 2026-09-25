"""``AgentSession`` / ``AgentMessage`` / ``AgentAudit`` / ``Confirmation`` 仓储测试（M2-10）。

覆盖 docs/04 §5.7 / §5.8 / §5.9 / §5.10 / §9 的硬约束：

- **审计入库前必须裁剪**：``arguments`` 里任何超过 1 KB 的字符串值（截图 base64
  的形态）递归替换为 ``{"__truncated__": true, "len": n}``，1024 字符不动；
  ``result_summary`` 截断到 2048 字符；裁剪**不修改入参**（调用方还要拿原始参数
  去执行/回显）；裁剪后的值真的落到库里。
- **审计查询**：可选 ``caller`` / ``tool_name`` 过滤，按 ``created_at DESC``
  分页，``Page.total`` 是过滤后的总数。
- **会话列表**：``list_recent`` 按 ``last_message_at DESC`` 且 NULL 排最后。
- **会话级授权只是持久化**：``update_grant`` 两个字段一起写、``clear_grant``
  两个字段一起置空（不是把时间改成过去）；判定语义归 M11，本卡不测策略。
- **消息按 ``seq`` 升序**读取，``(session_id, seq)`` 唯一约束在库层兜底。
- **确认终态不可再变**：``resolve`` 只接受 ``PENDING → APPROVED / REJECTED /
  EXPIRED``，重复处理返回 ``False``；``payload`` 完整落库（不是引用）。
- **``expire_overdue`` 返回被置为过期的 id 列表**而不是条数，且幂等；已批准 /
  未到期的记录不受影响。
- 四个仓储**都不 commit**：回滚后写入一起消失。

用例形态：同步测试函数 + ``asyncio.run(scenario())``（本仓无 pytest-asyncio），
临时库与会话来自 ``tests/db/conftest.py``。
"""

import asyncio
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
import pytest

from maa_api.db.models import (
    AgentIdempotency,
    AgentAudit,
    AgentMessage,
    AgentSession,
    Confirmation,
    utcnow,
)
from maa_api.db.repositories.agent import (
    AgentIdempotencyRepository,
    AgentMessageRepository,
    AgentSessionRepository,
)
from maa_api.db.repositories.audit import (
    MAX_ARGUMENT_STRING_CHARS,
    MAX_RESULT_SUMMARY_CHARS,
    AuditRepository,
    ConfirmationRepository,
    truncate_long_strings,
    truncate_result_summary,
)
from maa_api.domain.enums import (
    AgentRole,
    AgentSessionStatus,
    AuditStatus,
    CallerType,
    ConfirmationStatus,
    RiskLevel,
)


def _session(**overrides) -> AgentSession:
    fields: dict[str, Any] = {"model": "gpt-x", "status": AgentSessionStatus.ACTIVE}
    fields.update(overrides)
    return AgentSession(**fields)


def _message(session_id: str, seq: int, **overrides) -> AgentMessage:
    fields: dict[str, Any] = {
        "session_id": session_id,
        "seq": seq,
        "role": AgentRole.USER,
        "content": f"m{seq}",
    }
    fields.update(overrides)
    return AgentMessage(**fields)


def _audit(**overrides) -> AgentAudit:
    fields: dict[str, Any] = {
        "caller": CallerType.MCP,
        "tool_name": "click",
        "arguments": {},
        "status": AuditStatus.SUCCESS,
        "risk_level": RiskLevel.NONE,
    }
    fields.update(overrides)
    return AgentAudit(**fields)


def _confirmation(**overrides) -> Confirmation:
    fields: dict[str, Any] = {
        "action": "submit_pipeline",
        "risk_level": RiskLevel.CONSUME,
        "reason": "消耗类操作",
        "payload": {"a": 1},
        "requested_by": CallerType.MCP,
        "expires_at": utcnow() + timedelta(minutes=10),
    }
    fields.update(overrides)
    return Confirmation(**fields)


async def _count(session, table: str, where: str = "", params: dict | None = None) -> int:
    sql = f"select count(*) from {table}" + (f" where {where}" if where else "")
    return (await session.execute(text(sql), params or {})).scalar_one()


async def _raw(session, sql: str, params: dict | None = None):
    return (await session.execute(text(sql), params or {})).all()


# ---------------------------------------------------------------------------
# 裁剪纯函数：边界与递归
# ---------------------------------------------------------------------------


def test_truncate_long_strings_boundaries_and_recursion():
    """1024 字符原样保留，1025 字符替换为 __truncated__ 结构；嵌套逐层处理。"""
    keep = "k" * MAX_ARGUMENT_STRING_CHARS
    drop = "d" * (MAX_ARGUMENT_STRING_CHARS + 1)

    assert truncate_long_strings(keep) == keep
    assert truncate_long_strings(drop) == {
        "__truncated__": True,
        "len": MAX_ARGUMENT_STRING_CHARS + 1,
    }

    # 递归：dict 的值、list/tuple 的元素都要裁剪；键与非字符串标量不动
    payload = {
        "image": "x" * 5000,
        "small": "ok",
        "nested": {"shot": "y" * 4096, "n": 7, "flag": True, "none": None},
        "items": ["z" * 2000, "short", {"deep": "w" * 3000}],
    }
    out = truncate_long_strings(payload)
    assert out["image"] == {"__truncated__": True, "len": 5000}
    assert out["small"] == "ok"
    assert out["nested"]["shot"] == {"__truncated__": True, "len": 4096}
    assert out["nested"]["n"] == 7 and out["nested"]["flag"] is True
    assert out["nested"]["none"] is None
    assert out["items"][0] == {"__truncated__": True, "len": 2000}
    assert out["items"][1] == "short"
    assert out["items"][2]["deep"] == {"__truncated__": True, "len": 3000}

    # 不修改入参：调用方还要拿原始 base64 去执行/回显
    assert payload["image"] == "x" * 5000
    assert payload["items"][2]["deep"] == "w" * 3000


def test_truncate_result_summary_caps_and_keeps_none():
    """``result_summary`` 上限 2048 字符；``None`` 原样返回。"""
    assert truncate_result_summary(None) is None
    assert truncate_result_summary("short") == "short"
    assert len(truncate_result_summary("y" * 5000)) == MAX_RESULT_SUMMARY_CHARS
    exact = "z" * MAX_RESULT_SUMMARY_CHARS
    assert truncate_result_summary(exact) == exact


# ---------------------------------------------------------------------------
# agent_audit：裁剪落在仓储内，且真的入库
# ---------------------------------------------------------------------------


def test_audit_create_truncates_and_persists(db_session_factory):
    """仓储在写入路径完成 §5.9 裁剪，库里存的就是裁剪后的值。"""
    big = "b" * 412_300

    async def scenario():
        async with db_session_factory() as session:
            repo = AuditRepository(session)
            source = _audit(
                tool_name="screenshot",
                arguments={"image": big, "small": "ok", "nested": {"shot": "s" * 2048}},
                result_summary="r" * 5000,
            )
            row = await repo.create(source)
            assert row.id is not None  # flush 后自增 id 已落到返回值
            await session.commit()

            assert row.arguments["image"] == {"__truncated__": True, "len": 412_300}
            assert row.arguments["small"] == "ok"
            assert row.arguments["nested"]["shot"] == {"__truncated__": True, "len": 2048}
            assert len(row.result_summary) == MAX_RESULT_SUMMARY_CHARS
            # 入参不被修改
            assert source.arguments["image"] == big
            assert len(source.result_summary) == 5000

        # 跨会话读回：库里的确是被裁剪过的 JSON，不是只在内存里换了形态
        async with db_session_factory() as session:
            got = await AuditRepository(session).get(row.id)
            assert got is not None
            assert got.arguments["image"] == {"__truncated__": True, "len": 412_300}
            assert len(got.result_summary) == MAX_RESULT_SUMMARY_CHARS

    asyncio.run(scenario())


def test_audit_create_keeps_short_payload_and_none_summary(db_session_factory):
    """短参数原样落库；``result_summary`` 的 ``None`` 仍是 NULL，不写成空串。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = AuditRepository(session)
            row = await repo.create(_audit(arguments={"stage": "1-7", "times": 3}))
            await session.commit()
            assert row.arguments == {"stage": "1-7", "times": 3}
            assert row.result_summary is None
            raw = (
                await _raw(
                    session,
                    "select result_summary from agent_audit where id = :i",
                    {"i": row.id},
                )
            )[0][0]
            assert raw is None

    asyncio.run(scenario())


def test_audit_scopes_round_trip_in_order_and_legacy_rows_remain_null(db_session_factory):
    """审计 scope 按给定顺序持久化，未带 scope 的旧式记录仍为 NULL。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = AuditRepository(session)
            scoped = await repo.create(
                _audit(scopes=["status", "ops", "raw"])
            )
            legacy = await repo.create(_audit())
            await session.commit()
            scoped_id = scoped.id
            legacy_id = legacy.id

        async with db_session_factory() as session:
            scoped = await AuditRepository(session).get(scoped_id)
            legacy = await AuditRepository(session).get(legacy_id)
            assert scoped is not None
            assert scoped.scopes == ["status", "ops", "raw"]
            assert legacy is not None
            assert legacy.scopes is None

    asyncio.run(scenario())


def test_audit_get_and_list_filters_paginate(db_session_factory):
    """``list`` 按 ``created_at DESC`` 分页，``caller`` / ``tool_name`` 过滤生效。"""
    now = utcnow()

    async def scenario():
        async with db_session_factory() as session:
            repo = AuditRepository(session)
            # 故意乱序写入，靠显式 created_at 让顺序可判别
            await repo.create(
                _audit(
                    caller=CallerType.MCP,
                    tool_name="click",
                    created_at=now - timedelta(minutes=3),
                )
            )
            await repo.create(
                _audit(
                    caller=CallerType.REST,
                    tool_name="click",
                    created_at=now - timedelta(minutes=2),
                )
            )
            await repo.create(
                _audit(
                    caller=CallerType.MCP,
                    tool_name="screenshot",
                    created_at=now - timedelta(minutes=1),
                )
            )
            await session.commit()

            page = await repo.list()
            assert page.total == 3 and page.page == 1 and page.size == 20
            assert [a.tool_name for a in page.items] == ["screenshot", "click", "click"]
            assert page.items[0].caller == CallerType.MCP

            only_mcp = await repo.list(caller=CallerType.MCP)
            assert only_mcp.total == 2
            assert all(a.caller == CallerType.MCP for a in only_mcp.items)

            only_click = await repo.list(tool_name="click")
            assert only_click.total == 2
            both = await repo.list(caller=CallerType.MCP, tool_name="click")
            assert both.total == 1

            # 分页：Page.total 是过滤后的总数，不是当前页条数
            first = await repo.list(page=1, size=2)
            second = await repo.list(page=2, size=2)
            assert first.total == 3 and len(first.items) == 2
            assert second.total == 3 and len(second.items) == 1
            assert first.items[0].id == page.items[0].id
            assert {a.id for a in first.items} | {a.id for a in second.items} == {
                a.id for a in page.items
            }

            assert await repo.get(page.items[0].id) is not None
            assert await repo.get(999_999) is None

    asyncio.run(scenario())


def test_agent_audit_persists_request_id(db_session_factory):
    async def scenario():
        async with db_session_factory() as session:
            row = await AuditRepository(session).create(
                _audit(request_id="request-correlation-1")
            )
            await session.commit()
            stored = await AuditRepository(session).get(row.id)
            assert stored.request_id == "request-correlation-1"

    asyncio.run(scenario())


def test_agent_idempotency_repository_expires_and_uniquely_scopes_keys(db_session_factory):
    from sqlalchemy.exc import IntegrityError

    async def scenario():
        async with db_session_factory() as session:
            audit = await AuditRepository(session).create(_audit(status=AuditStatus.PENDING))
            old = await AgentIdempotencyRepository(session).create(
                AgentIdempotency(
                    caller=CallerType.REST,
                    key="retry-key",
                    request_hash="a" * 64,
                    audit_id=audit.id,
                    created_at=utcnow() - timedelta(hours=25),
                )
            )
            new = await AgentIdempotencyRepository(session).create(
                AgentIdempotency(
                    caller=CallerType.REST,
                    key="live-key",
                    request_hash="b" * 64,
                    audit_id=audit.id,
                )
            )
            await session.commit()
            repo = AgentIdempotencyRepository(session)
            assert (await repo.get(CallerType.REST, "live-key")).id == new.id
            assert await repo.delete_expired(utcnow() - timedelta(hours=24)) == 1
            assert await repo.get(CallerType.REST, "retry-key") is None
            assert await repo.get(CallerType.REST, "live-key") is not None
            duplicate = AgentIdempotency(
                caller=CallerType.REST,
                key="live-key",
                request_hash="c" * 64,
                audit_id=audit.id,
            )
            with pytest.raises(IntegrityError):
                await repo.create(duplicate)
            await session.rollback()

            await repo.create(
                AgentIdempotency(
                    caller=CallerType.REST,
                    key="orphan-reservation",
                    request_hash="d" * 64,
                    audit_id=None,
                )
            )
            await session.commit()
            assert await repo.delete_unlinked() == 1
            await session.commit()
            assert await repo.get(CallerType.REST, "orphan-reservation") is None

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# agent_session / agent_message
# ---------------------------------------------------------------------------


def test_agent_session_create_get_and_list_recent_nulls_last(db_session_factory):
    """``list_recent`` 按 ``last_message_at DESC``，没发过消息的会话排最后。"""
    now = utcnow()

    async def scenario():
        async with db_session_factory() as session:
            repo = AgentSessionRepository(session)
            newest = await repo.create(
                _session(title="最新", last_message_at=now - timedelta(minutes=1))
            )
            older = await repo.create(
                _session(title="较早", last_message_at=now - timedelta(minutes=5))
            )
            fresh = await repo.create(_session(title="刚建"))  # last_message_at 为 NULL
            await session.commit()

            assert await repo.get(newest.id) is not None
            assert await repo.get("missing") is None

            recent = await repo.list_recent()
            assert [s.id for s in recent] == [newest.id, older.id, fresh.id]
            assert await repo.list_recent(limit=2) != []
            assert [s.id for s in await repo.list_recent(limit=2)] == [
                newest.id,
                older.id,
            ]

            # 刚建的会话发出第一条消息后就应升到最前（last_message_at 由服务层写）
            fresh.last_message_at = now
            await session.commit()
            assert [s.id for s in await repo.list_recent()] == [
                fresh.id,
                newest.id,
                older.id,
            ]

    asyncio.run(scenario())


def test_agent_session_pages_include_message_cursor_and_delete_cascades(db_session_factory):
    """REST session/message pages retain stable ordering and deleting removes messages."""

    async def scenario():
        async with db_session_factory() as session:
            sessions = AgentSessionRepository(session)
            messages = AgentMessageRepository(session)
            first = await sessions.create(_session(title="first"))
            second = await sessions.create(_session(title="second"))
            for seq in range(4):
                await messages.create(_message(first.id, seq, content=f"m{seq}"))
            await session.commit()

            page = await sessions.list_page(page=1, size=1)
            assert page.total == 2 and len(page.items) == 1
            assert page.items[0].id == second.id
            after = await messages.list_page(first.id, after_seq=1, page=1, size=2)
            assert after.total == 2
            assert [message.seq for message in after.items] == [2, 3]

            assert await sessions.delete(first.id)
            await session.commit()
            assert await messages.list_by_session(first.id) == []
            assert not await sessions.delete("missing")

    asyncio.run(scenario())


def test_agent_session_grant_update_and_clear(db_session_factory):
    """``update_grant`` 写两个字段，``clear_grant`` 把两个字段一起置空。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = AgentSessionRepository(session)
            ses = await repo.create(_session())
            # atomic_grant_id 有外键，测试引擎开了 PRAGMA foreign_keys=ON，
            # 授权必须指向一条真实的确认记录（生产里是 grant_atomic_ops 那条）
            grant = await ConfirmationRepository(session).create(
                _confirmation(
                    action="grant_atomic_ops",
                    payload={"session_id": ses.id, "window_seconds": 900},
                )
            )
            await session.commit()
            assert ses.atomic_grant_id is None
            assert ses.atomic_grant_expires_at is None

            # 把 updated_at 退到过去，让「刷新」可判别
            await session.execute(
                text(
                    "update agent_session set updated_at = '2000-01-01 00:00:00' "
                    "where id = :i"
                ),
                {"i": ses.id},
            )
            await session.commit()

            window = utcnow() + timedelta(minutes=15)
            assert (
                await repo.update_grant(
                    ses.id, confirmation_id=grant.id, expires_at=window
                )
                is True
            )
            await session.commit()
            got = await repo.get(ses.id)
            assert got.atomic_grant_id == grant.id
            assert got.atomic_grant_expires_at == window
            # M2-08 纪律：条件更新后必须 populate_existing 才能读到真值
            assert got.updated_at.year > 2000

            # 撤销 = 两个字段一起置空，而不是把 expires_at 改成过去的时刻
            assert await repo.clear_grant(ses.id) is True
            await session.commit()
            cleared = await repo.get(ses.id)
            assert cleared.atomic_grant_id is None
            assert cleared.atomic_grant_expires_at is None
            assert (
                await _count(
                    session,
                    "agent_session",
                    "atomic_grant_id is not null",
                )
                == 0
            )

            # 不存在的会话：返回 False，不抛
            assert (
                await repo.update_grant(
                    "missing", confirmation_id="c", expires_at=window
                )
                is False
            )
            assert await repo.clear_grant("missing") is False

    asyncio.run(scenario())


def test_agent_message_create_and_list_by_session(db_session_factory):
    """``list_by_session`` 按 ``seq`` 升序、只含本会话，``(session_id, seq)`` 唯一。"""

    async def scenario():
        async with db_session_factory() as session:
            sessions = AgentSessionRepository(session)
            messages = AgentMessageRepository(session)
            ses = await sessions.create(_session())
            other = await sessions.create(_session(title="另一个会话"))
            await session.commit()

            # 乱序写入，读出来必须是 seq 升序
            second = await messages.create(
                _message(ses.id, 1, role=AgentRole.ASSISTANT, content="yo")
            )
            first = await messages.create(
                _message(
                    ses.id,
                    0,
                    tool_calls=[{"id": "call-1", "type": "function"}],
                )
            )
            await messages.create(_message(other.id, 0, content="other"))
            await session.commit()

            assert second.id is not None and first.id is not None
            rows = await messages.list_by_session(ses.id)
            assert [m.seq for m in rows] == [0, 1]
            assert [m.content for m in rows] == ["m0", "yo"]
            assert rows[0].tool_calls == [{"id": "call-1", "type": "function"}]

            # 唯一约束在数据库层兜底：同会话同 seq 直接 IntegrityError
            try:
                await messages.create(_message(ses.id, 1, content="dup"))
                await session.commit()
            except IntegrityError:
                await session.rollback()
            else:  # pragma: no cover - 走到这里说明唯一约束丢了
                raise AssertionError("uq_agent_message_session_seq 未生效")

            assert await _count(session, "agent_message") == 3

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# confirmation：payload 完整、终态不可变、超时扫描
# ---------------------------------------------------------------------------


def test_confirmation_create_persists_full_payload(db_session_factory):
    """``payload`` 存完整参数而不是引用：跨会话读回仍是原样。"""
    payload = {
        "pipeline": {
            "source": "agent",
            "tasks": [{"name": "Fight", "params": {"stage": "1-7", "times": 3}}],
        },
        "force": True,
    }

    async def scenario():
        async with db_session_factory() as session:
            repo = ConfirmationRepository(session)
            row = await repo.create(
                _confirmation(action="submit_pipeline", payload=payload)
            )
            await session.commit()

        async with db_session_factory() as session:
            got = await ConfirmationRepository(session).get(row.id)
            assert got is not None
            assert got.payload == payload
            assert got.status == ConfirmationStatus.PENDING
            assert got.resolved_at is None

    asyncio.run(scenario())


def test_confirmation_list_pending_orders_by_expires_at(db_session_factory):
    """待办列表只含 pending，且最快过期的排最前。"""
    now = utcnow()

    async def scenario():
        async with db_session_factory() as session:
            repo = ConfirmationRepository(session)
            soon = await repo.create(
                _confirmation(action="click", expires_at=now + timedelta(seconds=30))
            )
            later = await repo.create(
                _confirmation(action="submit_pipeline", expires_at=now + timedelta(minutes=9))
            )
            done = await repo.create(
                _confirmation(action="delete", expires_at=now + timedelta(minutes=5))
            )
            await session.commit()
            await repo.resolve(done.id, ConfirmationStatus.APPROVED, resolved_by="web")
            await session.commit()

            assert [c.id for c in await repo.list_pending()] == [soon.id, later.id]

    asyncio.run(scenario())


def test_confirmation_resolve_is_terminal_and_rejects_pending(db_session_factory):
    """重复处理 / 处理终态 / ``PENDING`` 目标都返回 ``False``，首次批准写入处理信息。"""

    async def scenario():
        async with db_session_factory() as session:
            repo = ConfirmationRepository(session)
            row = await repo.create(_confirmation(action="click"))
            await session.commit()

            # PENDING 不是合法流转目标
            assert (
                await repo.resolve(
                    row.id, ConfirmationStatus.PENDING, resolved_by="web"
                )
                is False
            )
            await session.commit()
            assert (await repo.get(row.id)).status == ConfirmationStatus.PENDING

            assert (
                await repo.resolve(
                    row.id,
                    ConfirmationStatus.APPROVED,
                    resolved_by="web",
                    reason="用户批准",
                )
                is True
            )
            await session.commit()

            # 终态不可再变：再批 / 改拒 / 改过期全部被 WHERE status='pending' 拒绝
            assert (
                await repo.resolve(
                    row.id, ConfirmationStatus.APPROVED, resolved_by="web"
                )
                is False
            )
            assert (
                await repo.resolve(
                    row.id, ConfirmationStatus.REJECTED, resolved_by="web"
                )
                is False
            )
            assert (
                await repo.resolve(
                    row.id, ConfirmationStatus.EXPIRED, resolved_by="system"
                )
                is False
            )
            assert (
                await repo.resolve("missing", ConfirmationStatus.APPROVED, resolved_by="web")
                is False
            )
            await session.commit()

            got = await repo.get(row.id)
            assert got.status == ConfirmationStatus.APPROVED
            assert got.resolved_by == "web"
            assert got.resolved_reason == "用户批准"
            assert got.resolved_at is not None

    asyncio.run(scenario())


def test_confirmation_expire_overdue_returns_ids_and_is_idempotent(db_session_factory):
    """``expire_overdue`` 返回被置为过期的 id 列表、幂等，且不碰已批准 / 未到期记录。"""
    now = utcnow()

    async def scenario():
        async with db_session_factory() as session:
            repo = ConfirmationRepository(session)
            first = await repo.create(
                _confirmation(action="a", expires_at=now - timedelta(seconds=30))
            )
            second = await repo.create(
                _confirmation(action="b", expires_at=now - timedelta(seconds=1))
            )
            future = await repo.create(
                _confirmation(action="c", expires_at=now + timedelta(minutes=5))
            )
            approved_in_time = await repo.create(
                _confirmation(action="d", expires_at=now - timedelta(seconds=1))
            )
            await session.commit()
            assert (
                await repo.resolve(
                    approved_in_time.id, ConfirmationStatus.APPROVED, resolved_by="web"
                )
                is True
            )
            await session.commit()

            expired_ids = await repo.expire_overdue(now)
            # 返回 id 列表（服务层据此广播并唤醒阻塞调用），不是条数
            assert set(expired_ids) == {first.id, second.id}
            await session.commit()

            assert (await repo.get(first.id)).status == ConfirmationStatus.EXPIRED
            assert (await repo.get(second.id)).status == ConfirmationStatus.EXPIRED
            got = await repo.get(second.id)
            assert got.resolved_by == "system"
            assert got.resolved_at == now
            # 已批准的不被超时扫描改写；未到期的仍是 pending
            assert (
                await repo.get(approved_in_time.id)
            ).status == ConfirmationStatus.APPROVED
            assert (await repo.get(future.id)).status == ConfirmationStatus.PENDING

            # 幂等：第二次没有可翻转的记录
            assert await repo.expire_overdue(now) == []
            assert [c.id for c in await repo.list_pending()] == [future.id]

    asyncio.run(scenario())


def test_confirmation_expire_all_pending_includes_far_future_rows(db_session_factory):
    """Restart cleanup must expire every pending row regardless of expiry horizon."""
    now = utcnow()

    async def scenario():
        async with db_session_factory() as session:
            repo = ConfirmationRepository(session)
            near = await repo.create(_confirmation(expires_at=now + timedelta(minutes=2)))
            far = await repo.create(_confirmation(expires_at=now + timedelta(days=40000)))
            resolved = await repo.create(_confirmation())
            await session.commit()
            assert await repo.resolve(
                resolved.id, ConfirmationStatus.APPROVED, resolved_by="web"
            )
            await session.commit()

            assert set(await repo.expire_all_pending(now)) == {near.id, far.id}
            await session.commit()
            assert (await repo.get(near.id)).status == ConfirmationStatus.EXPIRED
            assert (await repo.get(far.id)).status == ConfirmationStatus.EXPIRED
            assert (await repo.get(resolved.id)).status == ConfirmationStatus.APPROVED
            assert await repo.expire_all_pending(now) == []

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 事务纪律：四个仓储都不 commit
# ---------------------------------------------------------------------------


def test_repositories_do_not_commit(db_session_factory):
    """不 commit 的写入对另一个会话不可见；回滚后四张表都干净。"""

    async def scenario():
        async with db_session_factory() as session:
            sessions = AgentSessionRepository(session)
            messages = AgentMessageRepository(session)
            audits = AuditRepository(session)
            confirmations = ConfirmationRepository(session)

            ses = await sessions.create(_session())
            await messages.create(_message(ses.id, 0))
            await audits.create(_audit(arguments={"k": "v"}))
            await confirmations.create(_confirmation())
            # 四个 create 只 flush，不 commit

            async with db_session_factory() as other:
                assert await _count(other, "agent_session") == 0
                assert await _count(other, "agent_message") == 0
                assert await _count(other, "agent_audit") == 0
                assert await _count(other, "confirmation") == 0

            await session.rollback()

        async with db_session_factory() as session:
            assert await _count(session, "agent_session") == 0
            assert await _count(session, "agent_message") == 0
            assert await _count(session, "agent_audit") == 0
            assert await _count(session, "confirmation") == 0

    asyncio.run(scenario())
