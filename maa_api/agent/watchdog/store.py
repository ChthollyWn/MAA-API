"""``watchdog_alert`` 的落库助手：未解除告警去重与条件消失解除（M83 spec §4.1）。

T1 只提供两个不依赖服务编排的数据库操作，G2 的 ``WatchdogService`` 直接调用：

- :func:`upsert_open_alert`：同一 ``check`` + ``target_kind`` + ``target_id`` 且
  ``resolved_at IS NULL`` 时不插第二行；保留首次 ``created_at`` 与首次证据，
  后一次命中的证据写进 ``evidence["latest"]``，并刷新 ``evidence["last_seen_at"]``。
- :func:`resolve_condition_gone`：条件消失后由服务层调用，把本轮检查过、但目标
  已不在活跃条件集合里的未解除告警写入 ``resolved_at``。只处理本轮实际跑过的
  检查（``checked_checks``），避免误伤语义检查或本轮没跑的检查。

去重靠查询实现而不是部分唯一索引：``resolved_at`` 非空的历史行允许同目标重复，
部分唯一索引会把历史行一起挡住。

同步版本给同步 SQLite 测试库；生产服务的 ``async_sessionmaker`` 用
``upsert_open_alert_async`` / ``resolve_condition_gone_async``，两套语义逐字一致。
"""

from __future__ import annotations

from datetime import datetime
from typing import Collection, TypeAlias

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session, select

from maa_api.agent.watchdog.checks import AlertDraft
from maa_api.db.models import WatchdogAlert, utcnow

#: 去重键：``(check, target_kind, target_id)``。
AlertKey: TypeAlias = tuple[str, str, str | None]


def condition_key(check: str, target_kind: str, target_id: str | None) -> AlertKey:
    """构造去重键；``target_id`` 可以为 ``None``（如调用方没解析出设备 id）。"""
    return (str(check), str(target_kind), target_id)


def draft_key(draft: AlertDraft) -> AlertKey:
    """一条告警草稿的去重键。"""
    return condition_key(draft.check, draft.target_kind, draft.target_id)


def upsert_open_alert(
    session: Session, draft: AlertDraft, *, now: datetime | None = None
) -> tuple[WatchdogAlert, bool]:
    """写一条告警；返回 ``(行, 是否新插入)``。

    重复命中只更新 ``evidence``：首次 ``created_at``、级别、正文与动作字段都不动
    （spec §4.1「后一次检查只更新 evidence 与 created_at 不改」）。
    """
    stamp = now or utcnow()
    existing = session.exec(
        select(WatchdogAlert)
        .where(
            WatchdogAlert.check == draft.check,
            WatchdogAlert.target_kind == str(draft.target_kind),
            WatchdogAlert.target_id == draft.target_id,
            WatchdogAlert.resolved_at.is_(None),
        )
        .order_by(WatchdogAlert.id)
    ).first()
    if existing is not None:
        evidence = dict(existing.evidence or {})
        evidence["latest"] = dict(draft.evidence)
        evidence["last_seen_at"] = stamp.isoformat()
        existing.evidence = evidence
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing, False

    alert = WatchdogAlert(
        check=draft.check,
        severity=str(draft.severity),
        target_kind=str(draft.target_kind),
        target_id=draft.target_id,
        message=draft.message,
        evidence=dict(draft.evidence),
        created_at=stamp,
    )
    session.add(alert)
    session.commit()
    session.refresh(alert)
    return alert, True


def resolve_condition_gone(
    session: Session,
    *,
    checked_checks: Collection[str],
    active_keys: Collection[AlertKey],
    now: datetime | None = None,
) -> list[WatchdogAlert]:
    """把本轮检查过、但目标已不在 ``active_keys`` 的未解除告警标为已解除。

    ``checked_checks`` 必须是本轮真正跑过的检查名；语义检查与未运行的检查不在
    其列，它们的告警不会被这一轮误解除。
    """
    stamp = now or utcnow()
    if not checked_checks:
        return []
    active = set(active_keys)
    alerts = session.exec(
        select(WatchdogAlert).where(
            WatchdogAlert.resolved_at.is_(None),
            WatchdogAlert.check.in_(list(checked_checks)),
        )
    ).all()
    resolved: list[WatchdogAlert] = []
    for alert in alerts:
        if condition_key(alert.check, alert.target_kind, alert.target_id) in active:
            continue
        alert.resolved_at = stamp
        session.add(alert)
        resolved.append(alert)
    if resolved:
        session.commit()
        for alert in resolved:
            session.refresh(alert)
    return resolved


# ---------------------------------------------------------------------------
# 异步变体：生产服务用 async_sessionmaker 的 AsyncSession；同步版本留给
# ``tests/agent/test_watchdog_checks.py`` 的同步 SQLite 库。两边语义逐字一致。
# ---------------------------------------------------------------------------
async def upsert_open_alert_async(
    session: AsyncSession,
    draft: AlertDraft,
    *,
    now: datetime | None = None,
) -> tuple[WatchdogAlert, bool]:
    """:func:`upsert_open_alert` 的异步版本；去重键与 evidence 更新规则相同。"""
    stamp = now or utcnow()
    existing = (
        await session.execute(
            select(WatchdogAlert)
            .where(
                WatchdogAlert.check == draft.check,
                WatchdogAlert.target_kind == str(draft.target_kind),
                WatchdogAlert.target_id == draft.target_id,
                WatchdogAlert.resolved_at.is_(None),
            )
            .order_by(WatchdogAlert.id)
        )
    ).scalars().first()
    if existing is not None:
        evidence = dict(existing.evidence or {})
        evidence["latest"] = dict(draft.evidence)
        evidence["last_seen_at"] = stamp.isoformat()
        existing.evidence = evidence
        session.add(existing)
        await session.commit()
        await session.refresh(existing)
        return existing, False

    alert = WatchdogAlert(
        check=draft.check,
        severity=str(draft.severity),
        target_kind=str(draft.target_kind),
        target_id=draft.target_id,
        message=draft.message,
        evidence=dict(draft.evidence),
        created_at=stamp,
    )
    session.add(alert)
    await session.commit()
    await session.refresh(alert)
    return alert, True


async def resolve_condition_gone_async(
    session: AsyncSession,
    *,
    checked_checks: Collection[str],
    active_keys: Collection[AlertKey],
    now: datetime | None = None,
) -> list[WatchdogAlert]:
    """:func:`resolve_condition_gone` 的异步版本。"""
    stamp = now or utcnow()
    if not checked_checks:
        return []
    active = set(active_keys)
    alerts = (
        await session.execute(
            select(WatchdogAlert).where(
                WatchdogAlert.resolved_at.is_(None),
                WatchdogAlert.check.in_(list(checked_checks)),
            )
        )
    ).scalars().all()
    resolved: list[WatchdogAlert] = []
    for alert in alerts:
        if condition_key(alert.check, alert.target_kind, alert.target_id) in active:
            continue
        alert.resolved_at = stamp
        session.add(alert)
        resolved.append(alert)
    if resolved:
        await session.commit()
        for alert in resolved:
            await session.refresh(alert)
    return resolved
