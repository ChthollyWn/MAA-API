"""事件规则的生效值、匹配、覆盖语义与服务（M81 规格 §4.2、08 §3–§4）。

三层：

- :class:`EffectiveRule` 是「内置规则 + 覆盖行 + 用户规则」合并后的效果；
- :class:`RuleSet` 持有某一时刻的全部生效规则，负责匹配与版本哈希；
- :class:`TriggerRuleService` 读写覆盖层、做校验、失效缓存并通知监听者。

覆盖口径照 ``KnowledgeService.put_entry``：覆盖行只写与内置不同的列，``NULL``
表示「这一列跟随内置」；改回与内置完全一致且启用时删掉覆盖行（「恢复默认」）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatchcase
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.agent.events.builtin_rules import BUILTIN_RULES, BuiltinRule
from maa_api.agent.events.kinds import KNOWN_KINDS
from maa_api.agent.specs.schema import LEGACY_SPEC_NAMES
from maa_api.db.models import AgentTrigger, Ticket, TriggerRule, utcnow
from maa_api.db.repositories.triggers import (
    TriggerObservationRepository,
    TriggerRuleRepository,
)
from maa_api.domain.enums import EventSource, TriggerMode
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.schedule_service import cron_trigger

logger = logging.getLogger(__name__)

__all__ = [
    "CONDITION_KEY_PATTERN",
    "RULE_CRON_TIMEZONE",
    "RULE_ID_PATTERN",
    "EffectiveRule",
    "RuleSet",
    "RuleStats",
    "TriggerRuleOut",
    "TriggerRuleService",
    "TriggerRuleWrite",
    "match_conditions",
    "merge_rules",
]

#: 规则 id：2–48 字符，小写字母、数字与连字符（含内置规则的 id）。
RULE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")
#: 定时规则的时区，与 ``ScheduleWrite.timezone`` 的默认值一致。
RULE_CRON_TIMEZONE = "Asia/Shanghai"
#: 条件键：payload 字段名，可用点号取嵌套字段。
CONDITION_KEY_PATTERN = re.compile(r"^[a-z_][a-z0-9_.]{0,63}$")
#: 合法比较运算。
_COMPARE_OPS = frozenset({"gte", "lte", "ne"})
#: 条件写法只允许标量、列表、比较、存在性四种。
_MAX_CONDITIONS_JSON = 8000
_MISSING = object()
_BUILTIN_BY_ID: dict[str, BuiltinRule] = {rule.id: rule for rule in BUILTIN_RULES}


@dataclass(frozen=True, slots=True)
class EffectiveRule:
    """合并后的规则；内置规则带 :attr:`builtin`，用户规则为 ``None``。"""

    id: str
    title: str
    source: str | None
    kind_pattern: str
    conditions: Mapping[str, Any]
    mode: TriggerMode
    worker: str | None
    policy: str
    cooldown_seconds: int
    cron: str | None
    enabled: bool
    origin: Literal["builtin", "override", "user"]
    builtin: BuiltinRule | None

    def effective_mode(self, settings: Any) -> TriggerMode:
        """停用、或总开关为假时返回 ``OFF``，否则返回规则自己的档位。"""
        if not self.enabled:
            return TriggerMode.OFF
        if self.builtin is not None and self.builtin.gate_setting:
            if not _setting_value(settings, self.builtin.gate_setting):
                return TriggerMode.OFF
        return self.mode


def _setting_value(settings: Any, dotted_key: str) -> Any:
    """按 ``agent.auto_diagnose_failures`` 读设置；取不到按「开」处理。"""
    value: Any = settings
    for part in dotted_key.split("."):
        if isinstance(value, Mapping):
            if part not in value:
                return True
            value = value[part]
        else:
            value = getattr(value, part, None)
        if value is None:
            return True
    return value


def _lookup(payload: Mapping[str, Any], path: str) -> Any:
    """按点号取嵌套字段；不存在返回 :data:`_MISSING`。"""
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool))


def match_conditions(
    conditions: Mapping[str, Any], payload: Mapping[str, Any]
) -> bool:
    """按 §4.2 的四种写法匹配 payload；未知写法按不满足处理。"""
    for key, expected in conditions.items():
        actual = _lookup(payload, str(key))
        if isinstance(expected, Mapping):
            op = expected.get("op")
            value = expected.get("value")
            if op == "exists":
                if (actual is not _MISSING) != bool(value):
                    return False
                continue
            if op not in _COMPARE_OPS or actual is _MISSING:
                return False
            try:
                if op == "gte" and not actual >= value:  # type: ignore[operator]
                    return False
                if op == "lte" and not actual <= value:  # type: ignore[operator]
                    return False
                if op == "ne" and not actual != value:
                    return False
            except TypeError:
                return False
            continue
        if isinstance(expected, (list, tuple, set, frozenset)):
            if actual is _MISSING or actual not in expected:
                return False
            continue
        if actual is _MISSING or actual != expected:
            return False
    return True


class RuleSet:
    """某一时刻的全部生效规则：内置规则按声明顺序在前，用户规则按 id 在后。"""

    def __init__(self, rules: Sequence[EffectiveRule]) -> None:
        self._rules: tuple[EffectiveRule, ...] = tuple(rules)
        self._version = _version_of(self._rules)

    @property
    def version(self) -> str:
        """合并后规则的规范 JSON（按 id 排序）的 sha256 前 12 位。"""
        return self._version

    def all(self) -> list[EffectiveRule]:
        return list(self._rules)

    def get(self, rule_id: str) -> EffectiveRule | None:
        for rule in self._rules:
            if rule.id == rule_id:
                return rule
        return None

    def match(self, trigger: AgentTrigger, settings: Any) -> EffectiveRule | None:
        """返回第一条满足全部条件的规则（§4.2）。"""
        payload = trigger.payload or {}
        for rule in self._rules:
            if rule.effective_mode(settings) == TriggerMode.OFF:
                continue
            if rule.source is not None and rule.source != str(trigger.source):
                continue
            if not fnmatchcase(str(trigger.kind), rule.kind_pattern):
                continue
            if not match_conditions(rule.conditions, payload):
                continue
            if rule.source == EventSource.SCHEDULE.value:
                if str(payload.get("rule_id")) != rule.id:
                    continue
            return rule
        return None


def _version_of(rules: Sequence[EffectiveRule]) -> str:
    canonical = [
        {
            "id": rule.id,
            "title": rule.title,
            "source": rule.source,
            "kind_pattern": rule.kind_pattern,
            "conditions": dict(rule.conditions),
            "mode": str(rule.mode),
            "worker": rule.worker,
            "policy": rule.policy,
            "cooldown_seconds": rule.cooldown_seconds,
            "cron": rule.cron,
            "enabled": rule.enabled,
            "origin": rule.origin,
        }
        for rule in sorted(rules, key=lambda item: item.id)
    ]
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def _merge_builtin(builtin: BuiltinRule, row: TriggerRule | None) -> EffectiveRule:
    """覆盖行里为 ``None`` 的列沿用内置值。"""
    def column(name: str) -> Any:
        if row is None:
            return None
        return getattr(row, name)

    mode = builtin.mode if row is None or row.mode is None else TriggerMode(row.mode)
    conditions = column("conditions")
    return EffectiveRule(
        id=builtin.id,
        title=column("title") if column("title") is not None else builtin.title,
        source=builtin.source,
        kind_pattern=builtin.kind_pattern,
        conditions=builtin.conditions if conditions is None else conditions,
        mode=mode,
        worker=column("worker") if column("worker") is not None else builtin.worker,
        policy=column("policy") if column("policy") is not None else builtin.policy,
        cooldown_seconds=(
            column("cooldown_seconds")
            if column("cooldown_seconds") is not None
            else builtin.cooldown_seconds
        ),
        cron=column("cron"),
        enabled=True if row is None else bool(row.enabled),
        origin="builtin" if row is None else "override",
        builtin=builtin,
    )


def _merge_user(row: TriggerRule, default_mode: TriggerMode) -> EffectiveRule:
    mode = TriggerMode(row.mode) if row.mode else default_mode
    return EffectiveRule(
        id=row.id,
        title=row.title or row.id,
        source=row.source,
        kind_pattern=row.kind_pattern or "*",
        conditions=row.conditions or {},
        mode=mode,
        worker=row.worker,
        policy=row.policy or "",
        cooldown_seconds=row.cooldown_seconds or 0,
        cron=row.cron,
        enabled=bool(row.enabled),
        origin="user",
        builtin=None,
    )


def merge_rules(
    builtins: Sequence[BuiltinRule],
    rows: Sequence[TriggerRule],
    *,
    default_mode: TriggerMode,
) -> RuleSet:
    """把覆盖行与用户规则合并成 :class:`RuleSet`（顺序见类文档）。"""
    rows_by_id = {row.id: row for row in rows}
    merged: list[EffectiveRule] = []
    for builtin in builtins:
        merged.append(_merge_builtin(builtin, rows_by_id.pop(builtin.id, None)))
    for rule_id in sorted(rows_by_id):
        merged.append(_merge_user(rows_by_id[rule_id], default_mode))
    return RuleSet(merged)


class TriggerRuleWrite(BaseModel):
    """``PUT /api/agent/trigger-rules/{rule_id}`` 的入参：写完整的生效内容。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=64)
    source: EventSource | None = None
    kind_pattern: str = Field(min_length=1, max_length=64)
    conditions: dict[str, Any] = Field(default_factory=dict)
    mode: TriggerMode | None = None
    worker: str | None = Field(default=None, max_length=32)
    policy: str = Field(default="", max_length=2000)
    cooldown_seconds: int = Field(default=0, ge=0, le=604800)
    cron: str | None = Field(default=None, max_length=64)
    enabled: bool = True


class RuleStats(BaseModel):
    """规则列表附带的 30 天统计与升降档建议（§4.5.6）。"""

    observations_30d: int = 0
    reviewed_30d: int = 0
    good_30d: int = 0
    bad_30d: int = 0
    tickets_30d: int = 0
    tickets_bad_30d: int = 0
    suggest_auto: bool = False
    suggest_observe: bool = False


class TriggerRuleOut(BaseModel):
    """规则列表 / 写入返回的一条生效规则（§4.3）。"""

    id: str
    title: str
    source: str | None
    kind_pattern: str
    conditions: dict[str, Any]
    mode: TriggerMode
    effective_mode: TriggerMode
    worker: str | None
    policy: str
    cooldown_seconds: int
    cron: str | None
    enabled: bool
    origin: Literal["builtin", "override", "user"]
    modified: bool
    gated_by: str | None
    next_fire_at: datetime | None
    stats: RuleStats


def _invalid(field: str, reason: str) -> AppError:
    return AppError(
        ErrorCode.TRIGGER_RULE_INVALID,
        f"事件规则不合法：{reason}",
        {"field": field, "reason": reason},
    )


def _validate_conditions(conditions: Mapping[str, Any]) -> None:
    if len(json.dumps(conditions, ensure_ascii=False, default=str)) > _MAX_CONDITIONS_JSON:
        raise _invalid("conditions", "条件太大")
    for key, value in conditions.items():
        if not isinstance(key, str) or CONDITION_KEY_PATTERN.fullmatch(key) is None:
            raise _invalid("conditions", f"条件字段名不合法：{key}")
        if isinstance(value, Mapping):
            if set(value) != {"op", "value"}:
                raise _invalid("conditions", f"条件 {key} 的写法不合法")
            op = value.get("op")
            if op == "exists":
                if not isinstance(value.get("value"), bool):
                    raise _invalid("conditions", f"条件 {key} 的 exists 值必须是布尔")
            elif op in _COMPARE_OPS:
                if not _is_scalar(value.get("value")):
                    raise _invalid("conditions", f"条件 {key} 的比较值必须是标量")
            else:
                raise _invalid("conditions", f"条件 {key} 的运算符不合法：{op}")
        elif isinstance(value, (list, tuple)):
            if not value or not all(_is_scalar(item) for item in value):
                raise _invalid("conditions", f"条件 {key} 的列表值不合法")
        elif not _is_scalar(value):
            raise _invalid("conditions", f"条件 {key} 的值不合法")


class TriggerRuleService:
    """事件规则的读写、校验、合并与通知（§4.2、§4.5.6）。"""

    def __init__(
        self,
        session_factory: Any,
        *,
        spec_registry: Any,
        settings_provider: Callable[[], Any],
    ) -> None:
        self._session_factory = session_factory
        self._spec_registry = spec_registry
        self._settings_provider = settings_provider
        self._cached: RuleSet | None = None
        self._listeners: list[Callable[[RuleSet], None]] = []

    # -- 读取 -----------------------------------------------------------
    async def rule_set(self) -> RuleSet:
        """进程内缓存的生效规则；写入或删除后失效。"""
        if self._cached is None:
            async with self._session_factory() as session:
                rows = await TriggerRuleRepository(session).all()
            self._cached = merge_rules(
                BUILTIN_RULES,
                rows,
                default_mode=self._default_mode(),
            )
        return self._cached

    async def list_rules(self) -> list[TriggerRuleOut]:
        """全部规则（含内置），附 30 天统计、建议、下次触发与总开关信息。"""
        rule_set = await self.rule_set()
        stats, tickets = await self._period_stats()
        settings = self._settings_provider()
        now = datetime.now(UTC)
        return [
            self._rule_out(
                rule,
                settings,
                stats.get(rule.id, (0, 0, 0, 0)),
                tickets.get(rule.id, (0, 0)),
                now,
            )
            for rule in rule_set.all()
        ]

    def add_listener(self, listener: Callable[[RuleSet], None]) -> None:
        """规则变化后通知（G3 用来重排定时 job）。"""
        self._listeners.append(listener)

    # -- 写入 -----------------------------------------------------------
    async def put_rule(self, rule_id: str, payload: TriggerRuleWrite) -> TriggerRuleOut:
        """写入完整的生效内容：覆盖内置规则，或新建/修改用户规则。"""
        if RULE_ID_PATTERN.fullmatch(rule_id) is None:
            raise _invalid("rule_id", "id 必须匹配 ^[a-z0-9][a-z0-9-]{1,47}$")
        builtin = _BUILTIN_BY_ID.get(rule_id)
        worker = self._validate(rule_id, payload, builtin)

        now = utcnow()
        async with self._session_factory() as session:
            repository = TriggerRuleRepository(session)
            existing = await repository.get(rule_id)
            if builtin is not None:
                proposed = self._override_row(rule_id, payload, builtin, existing, now)
                if proposed is None:
                    await repository.delete(rule_id)
                else:
                    await repository.upsert(proposed)
            else:
                await repository.upsert(
                    self._user_row(rule_id, payload, worker, existing, now)
                )
            await session.commit()
        await self._notify_changed()
        for rule in await self.list_rules():
            if rule.id == rule_id:
                return rule
        raise RuntimeError(f"规则写入后读不到：{rule_id}")

    async def delete_rule(self, rule_id: str) -> None:
        """删除用户规则；内置规则只删除覆盖行（幂等），其余抛 404。"""
        builtin = _BUILTIN_BY_ID.get(rule_id)
        async with self._session_factory() as session:
            repository = TriggerRuleRepository(session)
            row = await repository.get(rule_id)
            if builtin is not None:
                if row is not None:
                    await repository.delete(rule_id)
            elif row is None or row.origin != "user":
                raise AppError(
                    ErrorCode.TRIGGER_RULE_NOT_FOUND,
                    "没有这条事件规则",
                    {"rule_id": rule_id},
                )
            else:
                await repository.delete(rule_id)
            await session.commit()
        await self._notify_changed()

    # -- 校验 -----------------------------------------------------------
    def _default_mode(self) -> TriggerMode:
        raw = getattr(getattr(self._settings_provider(), "triggers", None), "default_mode", None)
        try:
            return TriggerMode(str(raw)) if raw else TriggerMode.OBSERVE
        except ValueError:
            return TriggerMode.OBSERVE

    def _validate(
        self,
        rule_id: str,
        payload: TriggerRuleWrite,
        builtin: BuiltinRule | None,
    ) -> str | None:
        """校验 PUT 的语义；返回归一后的 worker（未给时保持 ``None``）。"""
        if builtin is not None:
            if _source_text(payload.source) != builtin.source:
                raise _invalid("source", "内置规则的来源不能改")
            if payload.kind_pattern != builtin.kind_pattern:
                raise _invalid("kind_pattern", "内置规则的事件种类不能改")

        if not any(
            fnmatchcase(kind, payload.kind_pattern) for kind in KNOWN_KINDS
        ):
            raise _invalid(
                "kind_pattern", "事件种类模式必须能匹配一个已登记的事件种类"
            )

        _validate_conditions(payload.conditions)

        worker = payload.worker.strip() if payload.worker else None
        if worker is None:
            if builtin is None:
                raise _invalid("worker", "用户规则必须指定执行代理")
        else:
            self._validate_worker(worker)

        is_schedule = _source_text(payload.source) == EventSource.SCHEDULE.value
        if is_schedule:
            if not payload.cron:
                raise _invalid("cron", "定时规则必须写 cron")
            if payload.kind_pattern != "schedule.fired":
                raise _invalid("kind_pattern", "定时规则的事件种类必须是 schedule.fired")
            try:
                cron_trigger(payload.cron, RULE_CRON_TIMEZONE)
            except (ValueError, TypeError):
                raise _invalid("cron", f"cron 解析不了：{payload.cron}") from None
        elif payload.cron is not None:
            raise _invalid("cron", "只有定时来源的规则能写 cron")
        return worker

    def _validate_worker(self, worker: str) -> None:
        try:
            spec = self._spec_registry.get(worker)
        except AppError:
            raise _invalid(
                "worker", f"执行代理 {worker} 不存在，请从列表里选一个"
            ) from None
        if getattr(spec, "kind", None) != "worker" or spec.name in LEGACY_SPEC_NAMES:
            raise _invalid("worker", f"执行代理 {worker} 不能负责工单")

    def _override_row(
        self,
        rule_id: str,
        payload: TriggerRuleWrite,
        builtin: BuiltinRule,
        existing: TriggerRule | None,
        now: datetime,
    ) -> TriggerRule | None:
        """把一次 PUT 折算成覆盖行；与内置完全一致且启用时返回 ``None``（删行）。"""
        conditions = dict(payload.conditions)
        builtin_conditions = dict(builtin.conditions)
        mode_same = payload.mode is None or payload.mode == builtin.mode
        worker_same = payload.worker is None or payload.worker == builtin.worker
        identical = (
            payload.title == builtin.title
            and mode_same
            and worker_same
            and payload.policy == builtin.policy
            and payload.cooldown_seconds == builtin.cooldown_seconds
            and conditions == builtin_conditions
        )
        if identical and payload.enabled:
            return None
        return TriggerRule(
            id=rule_id,
            title=None if payload.title == builtin.title else payload.title,
            source=None,
            kind_pattern=None,
            conditions=None if conditions == builtin_conditions else conditions,
            mode=(
                None
                if payload.mode is None or payload.mode == builtin.mode
                else str(payload.mode)
            ),
            worker=(
                None
                if payload.worker is None or payload.worker == builtin.worker
                else payload.worker
            ),
            policy=None if payload.policy == builtin.policy else payload.policy,
            cooldown_seconds=(
                None
                if payload.cooldown_seconds == builtin.cooldown_seconds
                else payload.cooldown_seconds
            ),
            cron=None,
            enabled=payload.enabled,
            origin="override",
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )

    def _user_row(
        self,
        rule_id: str,
        payload: TriggerRuleWrite,
        worker: str | None,
        existing: TriggerRule | None,
        now: datetime,
    ) -> TriggerRule:
        mode = payload.mode or self._default_mode()
        return TriggerRule(
            id=rule_id,
            title=payload.title,
            source=_source_text(payload.source),
            kind_pattern=payload.kind_pattern,
            conditions=dict(payload.conditions),
            mode=str(mode),
            worker=worker,
            policy=payload.policy,
            cooldown_seconds=payload.cooldown_seconds,
            cron=payload.cron,
            enabled=payload.enabled,
            origin="user",
            created_at=existing.created_at if existing is not None else now,
            updated_at=now,
        )

    # -- 通知 -----------------------------------------------------------
    async def _notify_changed(self) -> None:
        self._cached = None
        listeners = list(self._listeners)
        if not listeners:
            return
        rule_set = await self.rule_set()
        for listener in listeners:
            try:
                listener(rule_set)
            except Exception:  # noqa: BLE001 - 一个监听者失败不影响写入结果
                logger.warning("事件规则监听者执行失败", exc_info=True)

    # -- 统计与输出 -----------------------------------------------------
    async def _period_stats(
        self,
    ) -> tuple[dict[str, tuple[int, int, int, int]], dict[str, tuple[int, int]]]:
        since = utcnow() - timedelta(days=30)
        async with self._session_factory() as session:
            observations = await TriggerObservationRepository(session).stats_since(since)
            tickets = await _ticket_counts(session, since)
        return observations, tickets

    def _rule_out(
        self,
        rule: EffectiveRule,
        settings: Any,
        observation_stats: tuple[int, int, int, int],
        ticket_stats: tuple[int, int],
        now: datetime,
    ) -> TriggerRuleOut:
        observed, reviewed, good, bad = observation_stats
        tickets, bad_tickets = ticket_stats
        effective = rule.effective_mode(settings)
        stats = RuleStats(
            observations_30d=observed,
            reviewed_30d=reviewed,
            good_30d=good,
            bad_30d=bad,
            tickets_30d=tickets,
            tickets_bad_30d=bad_tickets,
            suggest_auto=(
                effective == TriggerMode.OBSERVE
                and reviewed >= 10
                and good / reviewed >= 0.9
                and bad == 0
            ),
            suggest_observe=(
                effective == TriggerMode.AUTO
                and tickets >= 5
                and bad_tickets / tickets >= 0.4
            ),
        )
        return TriggerRuleOut(
            id=rule.id,
            title=rule.title,
            source=rule.source,
            kind_pattern=rule.kind_pattern,
            conditions=dict(rule.conditions),
            mode=rule.mode,
            effective_mode=effective,
            worker=rule.worker,
            policy=rule.policy,
            cooldown_seconds=rule.cooldown_seconds,
            cron=rule.cron,
            enabled=rule.enabled,
            origin=rule.origin,
            modified=rule.origin == "override",
            gated_by=self._gated_by(rule, settings),
            next_fire_at=self._next_fire_at(rule, effective, now),
            stats=stats,
        )

    @staticmethod
    def _gated_by(rule: EffectiveRule, settings: Any) -> str | None:
        if rule.builtin is None or not rule.builtin.gate_setting:
            return None
        if _setting_value(settings, rule.builtin.gate_setting):
            return None
        return rule.builtin.gate_setting

    @staticmethod
    def _next_fire_at(
        rule: EffectiveRule, effective: TriggerMode, now: datetime
    ) -> datetime | None:
        if rule.source != EventSource.SCHEDULE.value or not rule.cron:
            return None
        if effective == TriggerMode.OFF:
            return None
        try:
            trigger = cron_trigger(rule.cron, RULE_CRON_TIMEZONE)
            return trigger.get_next_fire_time(None, now)
        except (ValueError, TypeError):
            return None


def _source_text(source: EventSource | str | None) -> str | None:
    if source is None:
        return None
    return source.value if isinstance(source, EventSource) else str(source)


async def _ticket_counts(
    session: AsyncSession, since: datetime
) -> dict[str, tuple[int, int]]:
    """按规则统计 30 天内以事件建的工单数与坏结局数（§4.5.6）。"""
    statement = (
        select(
            AgentTrigger.rule_id,
            func.count(Ticket.id),
            func.sum(case((Ticket.status.in_(_BAD_TICKET_STATUSES), 1), else_=0)),
        )
        .select_from(Ticket)
        .join(AgentTrigger, Ticket.source_trigger_id == AgentTrigger.id)
        .where(Ticket.created_at >= since, AgentTrigger.rule_id.is_not(None))
        .group_by(AgentTrigger.rule_id)
    )
    rows = (await session.execute(statement)).all()
    return {
        str(rule_id): (int(total or 0), int(bad or 0))
        for rule_id, total, bad in rows
        if rule_id is not None
    }


#: 工单的坏结局：用户取消或执行失败（08 §4.5）。
_BAD_TICKET_STATUSES = ("cancelled", "failed")
