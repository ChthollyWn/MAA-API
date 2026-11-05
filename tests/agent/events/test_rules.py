"""事件规则的合并、覆盖、校验、匹配与版本（M81 规格 §4.2、§4.5.6、§6）。

覆盖 §6 的十一条：覆盖行 NULL 继承、恢复默认删行、删除内置幂等、删除未知 404、
worker/cron/kind_pattern/内置 source 校验、用户规则默认档位、内置优先/用户按 id、
四种条件写法与点号嵌套、定时规则只匹配自己的事件、总开关与版本哈希。

临时库用 ``tests/fakes/v3_db.py`` 的 ``make_session_factory``；规则服务用真实的
``AgentSpecRegistry`` 校验执行代理，设置用最小替身。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from maa_api.agent.events.builtin_rules import BUILTIN_RULES
from maa_api.agent.events.rules import (
    RULE_CRON_TIMEZONE,
    RULE_ID_PATTERN,
    TriggerRuleService,
    TriggerRuleWrite,
    match_conditions,
)
from maa_api.agent.specs.registry import AgentSpecRegistry
from maa_api.agent.tools import build_registry
from maa_api.db.models import AgentTrigger
from maa_api.db.repositories.triggers import TriggerRuleRepository
from maa_api.domain.enums import EventSource, TriggerMode
from maa_api.domain.errors import AppError, ErrorCode
from tests.fakes.v3_db import make_session_factory
from tests.db.conftest import (  # noqa: F401  # pytest 从模块命名空间发现 fixture
    db_engine,
    db_path,
    db_session,
    db_session_factory,
)

RULE_BY_ID = {rule.id: rule for rule in BUILTIN_RULES}


class _Settings:
    def __init__(
        self,
        *,
        default_mode: str = "observe",
        auto_diagnose: bool = True,
        enabled: bool = True,
    ) -> None:
        self.triggers = SimpleNamespace(
            enabled=enabled,
            default_mode=default_mode,
            eval_daily_limit=200,
        )
        self.agent = SimpleNamespace(auto_diagnose_failures=auto_diagnose)


@pytest.fixture
def factory(db_path):
    engine, session_factory = asyncio.run(make_session_factory(db_path))
    yield session_factory
    engine.sync_engine.dispose()


@pytest.fixture
def spec_registry():
    registry = AgentSpecRegistry(build_registry())
    registry.load()
    return registry


def _service(factory, spec_registry, settings) -> TriggerRuleService:
    return TriggerRuleService(
        factory,
        spec_registry=spec_registry,
        settings_provider=lambda: settings,
    )


def _builtin_payload(rule_id: str, **overrides) -> TriggerRuleWrite:
    rule = RULE_BY_ID[rule_id]
    values = dict(
        title=rule.title,
        source=EventSource(rule.source) if rule.source is not None else None,
        kind_pattern=rule.kind_pattern,
        conditions=dict(rule.conditions),
        mode=rule.mode,
        worker=rule.worker,
        policy=rule.policy,
        cooldown_seconds=rule.cooldown_seconds,
        cron=None,
        enabled=True,
    )
    values.update(overrides)
    return TriggerRuleWrite(**values)


def _user_payload(**overrides) -> TriggerRuleWrite:
    values = dict(
        title="自定义规则",
        source=EventSource.SYSTEM,
        kind_pattern="system.update_available",
        conditions={},
        mode=TriggerMode.AUTO,
        worker="ops",
        policy="有更新就处理",
        cooldown_seconds=0,
        cron=None,
        enabled=True,
    )
    values.update(overrides)
    return TriggerRuleWrite(**values)


async def _row(factory, rule_id: str):
    async with factory() as session:
        return await TriggerRuleRepository(session).get(rule_id)


def _trigger(**overrides) -> AgentTrigger:
    values = dict(
        source="system",
        kind="system.sanity_full",
        thread_key="system:sanity",
        payload={},
    )
    values.update(overrides)
    return AgentTrigger(**values)


def _assert_invalid(exc: pytest.ExceptionInfo[AppError], field: str) -> None:
    assert exc.value.code is ErrorCode.TRIGGER_RULE_INVALID
    assert exc.value.http_status == 400
    assert (exc.value.details or {}).get("field") == field
    assert (exc.value.message or "").startswith("事件规则不合法")


def test_merge_override_inherits_null_columns(factory, spec_registry):
    async def scenario():
        settings = _Settings()
        service = _service(factory, spec_registry, settings)
        await service.put_rule(
            "sanity-full", _builtin_payload("sanity-full", mode=TriggerMode.AUTO)
        )

        row = await _row(factory, "sanity-full")
        assert row is not None
        assert row.origin == "override"
        assert row.mode == "auto"
        # 只写不同的列，其余全是 NULL（跟随内置）。
        assert row.title is None
        assert row.source is None
        assert row.kind_pattern is None
        assert row.conditions is None
        assert row.worker is None
        assert row.policy is None
        assert row.cooldown_seconds is None
        assert row.cron is None

        rule = (await service.rule_set()).get("sanity-full")
        assert rule is not None
        assert rule.mode == TriggerMode.AUTO
        assert rule.origin == "override"
        assert rule.title == RULE_BY_ID["sanity-full"].title
        assert rule.worker == "daily_ops"
        assert rule.policy == RULE_BY_ID["sanity-full"].policy
        assert rule.cooldown_seconds == 21600
        assert rule.enabled

    asyncio.run(scenario())


def test_put_identical_to_builtin_deletes_override_row(factory, spec_registry):
    async def scenario():
        service = _service(factory, spec_registry, _Settings())
        await service.put_rule(
            "sanity-full", _builtin_payload("sanity-full", mode=TriggerMode.AUTO)
        )
        assert await _row(factory, "sanity-full") is not None

        returned = await service.put_rule(
            "sanity-full", _builtin_payload("sanity-full")
        )
        assert await _row(factory, "sanity-full") is None
        assert returned.origin == "builtin"
        assert not returned.modified
        assert returned.effective_mode == TriggerMode.OBSERVE

        rule = (await service.rule_set()).get("sanity-full")
        assert rule is not None
        assert rule.origin == "builtin"
        assert rule.mode == TriggerMode.OBSERVE

    asyncio.run(scenario())


def test_delete_builtin_without_override_is_noop(factory, spec_registry):
    async def scenario():
        service = _service(factory, spec_registry, _Settings())
        await service.delete_rule("sanity-full")
        await service.delete_rule("sanity-full")
        assert await _row(factory, "sanity-full") is None
        rule = (await service.rule_set()).get("sanity-full")
        assert rule is not None
        assert rule.origin == "builtin"

    asyncio.run(scenario())


def test_delete_unknown_rule_raises_not_found(factory, spec_registry):
    async def scenario():
        service = _service(factory, spec_registry, _Settings())
        with pytest.raises(AppError) as excinfo:
            await service.delete_rule("no-such-rule")
        assert excinfo.value.code is ErrorCode.TRIGGER_RULE_NOT_FOUND
        assert excinfo.value.http_status == 404
        assert excinfo.value.message == "没有这条事件规则"

    asyncio.run(scenario())


def test_put_rejects_invalid_worker_cron_pattern_and_builtin_source_change(
    factory, spec_registry
):
    async def scenario():
        service = _service(factory, spec_registry, _Settings())

        with pytest.raises(AppError) as excinfo:
            await service.put_rule("my-rule", _user_payload(worker="ghost"))
        _assert_invalid(excinfo, "worker")

        with pytest.raises(AppError) as excinfo:
            await service.put_rule(
                "my-rule",
                _user_payload(
                    source=EventSource.SCHEDULE,
                    kind_pattern="schedule.fired",
                    cron="not a cron",
                ),
            )
        _assert_invalid(excinfo, "cron")

        with pytest.raises(AppError) as excinfo:
            await service.put_rule("my-rule", _user_payload(kind_pattern="nope.*"))
        _assert_invalid(excinfo, "kind_pattern")

        with pytest.raises(AppError) as excinfo:
            await service.put_rule(
                "sanity-full", _builtin_payload("sanity-full", source=EventSource.DEVICE)
            )
        _assert_invalid(excinfo, "source")

        with pytest.raises(AppError) as excinfo:
            await service.put_rule(
                "sanity-full",
                _builtin_payload("sanity-full", kind_pattern="device.unavailable"),
            )
        _assert_invalid(excinfo, "kind_pattern")

        # 定时规则必须带 cron，且种类固定是 schedule.fired。
        with pytest.raises(AppError) as excinfo:
            await service.put_rule(
                "my-rule",
                _user_payload(
                    source=EventSource.SCHEDULE,
                    kind_pattern="schedule.fired",
                    cron=None,
                ),
            )
        _assert_invalid(excinfo, "cron")
        with pytest.raises(AppError) as excinfo:
            await service.put_rule(
                "my-rule",
                _user_payload(
                    source=EventSource.SCHEDULE,
                    kind_pattern="system.sanity_full",
                    cron="0 * * * *",
                ),
            )
        _assert_invalid(excinfo, "kind_pattern")

        # 非定时来源不能写 cron；用户规则必须指定执行代理。
        with pytest.raises(AppError) as excinfo:
            await service.put_rule("my-rule", _user_payload(cron="0 * * * *"))
        _assert_invalid(excinfo, "cron")
        with pytest.raises(AppError) as excinfo:
            await service.put_rule("my-rule", _user_payload(worker=None))
        _assert_invalid(excinfo, "worker")

        # 不合法规则没有落库。
        assert await _row(factory, "my-rule") is None

    asyncio.run(scenario())


def test_user_rule_without_mode_uses_default_mode_setting(factory, spec_registry):
    async def scenario():
        settings = _Settings(default_mode="auto")
        service = _service(factory, spec_registry, settings)
        await service.put_rule("my-rule", _user_payload(mode=None))

        row = await _row(factory, "my-rule")
        assert row is not None
        assert row.origin == "user"
        assert row.mode == "auto"
        rule = (await service.rule_set()).get("my-rule")
        assert rule is not None
        assert rule.origin == "user"
        assert rule.mode == TriggerMode.AUTO

    asyncio.run(scenario())


def test_match_order_builtin_first_then_user_by_id(factory, spec_registry):
    async def scenario():
        settings = _Settings()
        service = _service(factory, spec_registry, settings)
        await service.put_rule(
            "zzz-rule",
            _user_payload(
                source=EventSource.PIPELINE,
                kind_pattern="pipeline.completed",
                worker="diagnose",
            ),
        )
        await service.put_rule(
            "aaa-rule",
            _user_payload(
                source=EventSource.PIPELINE,
                kind_pattern="pipeline.completed",
                worker="diagnose",
            ),
        )
        rule_set = await service.rule_set()
        # 同一事件两条用户规则都匹配时取 id 小的；用户规则永远排在内置之后。
        matched = rule_set.match(
            _trigger(
                source="pipeline",
                kind="pipeline.completed",
                thread_key="pipeline:p1",
            ),
            settings,
        )
        assert matched is not None
        assert matched.id == "aaa-rule"

        # 内置规则优先于用户规则：sanity-full 是 observe，仍然先命中。
        await service.put_rule(
            "aaa-rule",
            _user_payload(
                source=EventSource.SYSTEM,
                kind_pattern="system.sanity_full",
                worker="ops",
            ),
        )
        rule_set = await service.rule_set()
        matched = rule_set.match(_trigger(), settings)
        assert matched is not None
        assert matched.id == "sanity-full"
        # all() 顺序：内置五条按声明顺序，其后用户规则按 id。
        ids = [rule.id for rule in rule_set.all()]
        assert ids == [rule.id for rule in BUILTIN_RULES] + ["aaa-rule", "zzz-rule"]

    asyncio.run(scenario())


def test_conditions_equality_membership_compare_exists():
    payload = {
        "target": "core",
        "retry": 3,
        "last_error": {"stage": "load"},
    }

    assert match_conditions({}, {})
    assert match_conditions({"target": "core"}, payload)
    assert not match_conditions({"target": "resource"}, payload)
    assert match_conditions({"target": ["core", "resource"]}, payload)
    assert not match_conditions({"target": ["resource"]}, payload)
    assert match_conditions({"retry": {"op": "gte", "value": 3}}, payload)
    assert not match_conditions({"retry": {"op": "gte", "value": 4}}, payload)
    assert match_conditions({"retry": {"op": "lte", "value": 3}}, payload)
    assert not match_conditions({"retry": {"op": "lte", "value": 2}}, payload)
    assert match_conditions({"retry": {"op": "ne", "value": 4}}, payload)
    assert not match_conditions({"retry": {"op": "ne", "value": 3}}, payload)
    assert match_conditions({"target": {"op": "exists", "value": True}}, payload)
    assert not match_conditions({"target": {"op": "exists", "value": False}}, payload)
    assert match_conditions({"missing": {"op": "exists", "value": False}}, payload)
    assert not match_conditions({"missing": {"op": "exists", "value": True}}, payload)
    # 点号取嵌套字段；比较类条件字段缺失时不满足。
    assert match_conditions({"last_error.stage": "load"}, payload)
    assert not match_conditions({"last_error.stage": "fight"}, payload)
    assert not match_conditions({"last_error.missing": {"op": "ne", "value": 1}}, payload)
    # 未知写法按不满足处理。
    assert not match_conditions({"target": {"op": "regex", "value": ".*"}}, payload)


def test_schedule_rule_matches_only_its_own_fired_event(factory, spec_registry):
    async def scenario():
        settings = _Settings()
        service = _service(factory, spec_registry, settings)
        returned = await service.put_rule(
            "cron-rule",
            _user_payload(
                source=EventSource.SCHEDULE,
                kind_pattern="schedule.fired",
                conditions={},
                cron="0 * * * *",
                worker="daily_ops",
            ),
        )
        assert returned.cron == "0 * * * *"
        rule_set = await service.rule_set()
        fired = _trigger(
            source="schedule",
            kind="schedule.fired",
            thread_key="schedule:cron-rule",
            payload={"rule_id": "cron-rule", "scheduled_for": "2026-11-05T09:00:00+08:00"},
        )
        matched = rule_set.match(fired, settings)
        assert matched is not None
        assert matched.id == "cron-rule"

        other = _trigger(
            source="schedule",
            kind="schedule.fired",
            thread_key="schedule:other-rule",
            payload={"rule_id": "other-rule"},
        )
        assert rule_set.match(other, settings) is None
        assert RULE_CRON_TIMEZONE == "Asia/Shanghai"
        assert RULE_ID_PATTERN.fullmatch("cron-rule")

        # 定时规则给出下次触发时间；停用后为 null（§4.5.7）。
        outputs = {item.id: item for item in await service.list_rules()}
        assert outputs["cron-rule"].next_fire_at is not None
        assert outputs["cron-rule"].next_fire_at.tzinfo is not None
        await service.put_rule(
            "cron-rule",
            _user_payload(
                source=EventSource.SCHEDULE,
                kind_pattern="schedule.fired",
                conditions={},
                cron="0 * * * *",
                worker="daily_ops",
                enabled=False,
            ),
        )
        outputs = {item.id: item for item in await service.list_rules()}
        assert outputs["cron-rule"].next_fire_at is None

    asyncio.run(scenario())


def test_gate_setting_off_makes_diagnose_rules_off(factory, spec_registry):
    async def scenario():
        settings = _Settings(auto_diagnose=False)
        service = _service(factory, spec_registry, settings)
        rule_set = await service.rule_set()
        diagnose = rule_set.get("diagnose-pipeline-failure")
        assert diagnose is not None
        assert diagnose.effective_mode(settings) == TriggerMode.OFF
        # 覆盖成 observe 也压不过总开关。
        await service.put_rule(
            "diagnose-pipeline-failure",
            _builtin_payload("diagnose-pipeline-failure", mode=TriggerMode.OBSERVE),
        )
        diagnose = (await service.rule_set()).get("diagnose-pipeline-failure")
        assert diagnose is not None
        assert diagnose.mode == TriggerMode.OBSERVE
        assert diagnose.effective_mode(settings) == TriggerMode.OFF

        outputs = {item.id: item for item in await service.list_rules()}
        out = outputs["diagnose-pipeline-failure"]
        assert out.gated_by == "agent.auto_diagnose_failures"
        assert out.effective_mode == TriggerMode.OFF
        assert out.modified

        settings.agent.auto_diagnose_failures = True
        assert diagnose.effective_mode(settings) == TriggerMode.OBSERVE

    asyncio.run(scenario())


def test_rule_set_version_changes_when_rule_changes(factory, spec_registry):
    async def scenario():
        service = _service(factory, spec_registry, _Settings())
        first = (await service.rule_set()).version
        assert len(first) == 12

        await service.put_rule("aaa-rule", _user_payload())
        second = (await service.rule_set()).version
        assert second != first

        await service.delete_rule("aaa-rule")
        third = (await service.rule_set()).version
        assert third == first

    asyncio.run(scenario())


def test_rule_change_notifies_listener(factory, spec_registry):
    async def scenario():
        service = _service(factory, spec_registry, _Settings())
        seen = []
        service.add_listener(seen.append)

        await service.put_rule("aaa-rule", _user_payload())
        assert len(seen) == 1
        assert seen[0].get("aaa-rule") is not None

        await service.delete_rule("aaa-rule")
        assert len(seen) == 2
        assert seen[1].get("aaa-rule") is None

    asyncio.run(scenario())
