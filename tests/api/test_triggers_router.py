"""事件 REST 契约（M81 规格 §4.3、§6 的 ``test_triggers_router.py``）。

四个用例覆盖 §6 列出的断言，并补 503 与时间 ``Z``：

- 事件记录按来源 / 种类 / 规则过滤、``id`` 倒序分页，分页越界是 400
  ``INVALID_PAGINATION``；
- 规则列表带 30 天统计与「可以改为自动」建议、规则集版本哈希与 ``next_fire_at``；
- 规则写入的语义错是 400 ``TRIGGER_RULE_INVALID``（``details.field`` 指字段）、
  请求体形状错是 422 ``VALIDATION_ERROR``，删除未知规则是 404；
- 观察记录可重复改判，不存在是 404 ``OBSERVATION_NOT_FOUND``。

应用按生产装配的最小集现搭：只装事件路由、统一错误处理器与三个 ``app.state``
（事件服务、规则服务、会话工厂）；数据写进 ``isolated_db`` 的临时库。所有数据库
操作都经 ``TestClient`` 的 portal 跑在同一个事件循环里。
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表，create_all 才能建出三张事件表
from maa_api.agent.events.rules import TriggerRuleService
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import triggers as triggers_router
from maa_api.db import session as db_session
from maa_api.db.models import AgentTrigger, TriggerObservation, utcnow
from maa_api.domain.errors import AppError, ErrorCode


class _SpecRegistry:
    """最小代理定义注册表；只有 ``ops`` 是合法 Worker。"""

    def get(self, name: str) -> SimpleNamespace:
        if name != "ops":
            raise AppError(
                ErrorCode.AGENT_SPEC_NOT_FOUND, f"没有名为 {name} 的代理定义"
            )
        return SimpleNamespace(name="ops", kind="worker", risk_ceiling="none")


async def _create_schema(engine: Any) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


def _build_app(isolated_db, make_client, settings):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(triggers_router.router)
    app.state.session_factory = db_session.session_factory
    app.state.trigger_service = object()  # 路由只把它当「已装配」的闸门
    app.state.trigger_rule_service = TriggerRuleService(
        db_session.session_factory,
        spec_registry=_SpecRegistry(),
        settings_provider=lambda: settings,
    )
    client = make_client(app)
    client.portal.call(_create_schema, isolated_db)
    return app, client


def _error(response):
    return response.json()["error"]


def _seed_trigger(client, **overrides: Any) -> int:
    fields: dict[str, Any] = {
        "source": "pipeline",
        "kind": "pipeline.failed",
        "thread_key": "pipeline:0f8fad5b-d9cb-469f-a165-708677289501",
        "payload": {"title": "日常", "error_message": "任务失败"},
        "admission": "accepted",
        "eval_status": "skipped",
        "routed_to": "none",
    }
    fields.update(overrides)

    async def create(factory):
        async with factory() as db:
            row = AgentTrigger(**fields)
            db.add(row)
            await db.commit()
            return int(row.id)

    return client.portal.call(create, db_session.session_factory)


def _seed_observation(
    client,
    trigger_id: int,
    *,
    rule_id: str = "sanity-full",
    verdict: str | None = None,
    created_at: Any = None,
) -> int:
    async def create(factory):
        async with factory() as db:
            now = utcnow()
            row = TriggerObservation(
                trigger_id=trigger_id,
                rule_id=rule_id,
                decision={"decision": "accept", "title": "处理"},
                verdict=verdict,
                reviewed_at=now if verdict else None,
                created_at=created_at or now,
            )
            db.add(row)
            await db.commit()
            return int(row.id)

    return client.portal.call(create, db_session.session_factory)


# ---------------------------------------------------------------------------
# 事件记录
# ---------------------------------------------------------------------------
def test_list_triggers_filters_and_paginates(isolated_db, make_client, tmp_settings):
    _app, client = _build_app(isolated_db, make_client, tmp_settings)
    failed = _seed_trigger(client)
    sanity = _seed_trigger(
        client,
        source="system",
        kind="system.sanity_full",
        thread_key="system:sanity",
        payload={"estimated": 135, "max_sanity": 135},
    )
    _seed_trigger(
        client,
        source="device",
        kind="device.unavailable",
        thread_key="device:emulator-1",
        payload={"address": "emulator-1", "retry_attempt": 3},
        rule_id="device-unavailable",
    )

    page = client.get("/api/agent/triggers", params={"page": 1, "size": 2})
    assert page.status_code == 200
    body = page.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["size"] == 2
    assert len(body["items"]) == 2
    assert body["items"][0]["id"] > body["items"][1]["id"]
    assert body["items"][0]["created_at"].endswith("Z")
    assert body["items"][0]["summary"].startswith("设备重连")

    by_source = client.get("/api/agent/triggers", params={"source": "system"}).json()
    assert [item["id"] for item in by_source["items"]] == [sanity]
    by_kind = client.get(
        "/api/agent/triggers", params={"kind": "pipeline.failed"}
    ).json()
    assert [item["id"] for item in by_kind["items"]] == [failed]
    by_rule = client.get(
        "/api/agent/triggers", params={"rule_id": "device-unavailable"}
    ).json()
    assert by_rule["total"] == 1

    for params in ({"page": 0}, {"size": 0}, {"size": 201}):
        bad = client.get("/api/agent/triggers", params=params)
        assert bad.status_code == 400
        assert _error(bad)["code"] == "INVALID_PAGINATION"
    assert failed > 0


# ---------------------------------------------------------------------------
# 规则
# ---------------------------------------------------------------------------
def test_rules_list_includes_stats_and_suggestions(
    isolated_db, make_client, tmp_settings
):
    _app, client = _build_app(isolated_db, make_client, tmp_settings)
    now = utcnow()
    for index in range(10):
        trigger_id = _seed_trigger(
            client,
            source="system",
            kind="system.sanity_full",
            thread_key="system:sanity",
            payload={"estimated": 135, "max_sanity": 135},
        )
        _seed_observation(
            client,
            trigger_id,
            verdict="good",
            created_at=now - timedelta(days=index),
        )

    response = client.get("/api/agent/trigger-rules")
    assert response.status_code == 200
    body = response.json()
    assert body["version"]
    rules = {item["id"]: item for item in body["items"]}
    rule = rules["sanity-full"]
    assert rule["origin"] == "builtin"
    assert rule["modified"] is False
    assert rule["effective_mode"] == "observe"
    assert rule["next_fire_at"] is None
    assert rule["stats"]["observations_30d"] == 10
    assert rule["stats"]["reviewed_30d"] == 10
    assert rule["stats"]["good_30d"] == 10
    assert rule["stats"]["bad_30d"] == 0
    assert rule["stats"]["suggest_auto"] is True
    assert rule["stats"]["suggest_observe"] is False


def test_put_rule_errors_map_to_400_and_422(isolated_db, make_client, tmp_settings):
    _app, client = _build_app(isolated_db, make_client, tmp_settings)

    missing = client.put("/api/agent/trigger-rules/my-rule", json={})
    assert missing.status_code == 422
    assert _error(missing)["code"] == "VALIDATION_ERROR"

    bad_worker = client.put(
        "/api/agent/trigger-rules/my-rule",
        json={
            "title": "我的规则",
            "source": "device",
            "kind_pattern": "device.connected",
            "conditions": {},
            "worker": "nope",
            "policy": "",
            "cooldown_seconds": 0,
        },
    )
    assert bad_worker.status_code == 400
    error = _error(bad_worker)
    assert error["code"] == "TRIGGER_RULE_INVALID"
    assert error["details"]["field"] == "worker"

    malformed_id = client.put(
        "/api/agent/trigger-rules/Bad_Id",
        json={
            "title": "我的规则",
            "source": "device",
            "kind_pattern": "device.connected",
            "conditions": {},
            "worker": "ops",
            "policy": "",
            "cooldown_seconds": 0,
        },
    )
    assert malformed_id.status_code == 422

    created = client.put(
        "/api/agent/trigger-rules/my-rule",
        json={
            "title": "我的规则",
            "source": "device",
            "kind_pattern": "device.connected",
            "conditions": {"address": "emulator-1"},
            "worker": "ops",
            "policy": "",
            "cooldown_seconds": 0,
        },
    )
    assert created.status_code == 200
    rule = created.json()
    assert rule["id"] == "my-rule"
    assert rule["origin"] == "user"
    assert rule["mode"] == "observe"
    assert rule["effective_mode"] == "observe"
    assert "my-rule" in {
        item["id"] for item in client.get("/api/agent/trigger-rules").json()["items"]
    }

    assert client.delete("/api/agent/trigger-rules/sanity-full").status_code == 204
    missing_rule = client.delete("/api/agent/trigger-rules/no-such-rule")
    assert missing_rule.status_code == 404
    assert _error(missing_rule)["code"] == "TRIGGER_RULE_NOT_FOUND"


# ---------------------------------------------------------------------------
# 观察记录
# ---------------------------------------------------------------------------
def test_review_observation_and_change_verdict(
    isolated_db, make_client, tmp_settings
):
    _app, client = _build_app(isolated_db, make_client, tmp_settings)
    trigger_id = _seed_trigger(
        client,
        source="system",
        kind="system.sanity_full",
        thread_key="system:sanity",
        payload={"estimated": 135, "max_sanity": 135},
    )
    observation_id = _seed_observation(client, trigger_id)

    pending = client.get("/api/agent/observations", params={"reviewed": "false"})
    assert pending.status_code == 200
    assert pending.json()["total"] == 1
    item = pending.json()["items"][0]
    assert item["verdict"] is None
    assert item["trigger_summary"].startswith("理智估计已满")
    assert item["thread_key"] == "system:sanity"
    assert item["created_at"].endswith("Z")

    first = client.post(
        f"/api/agent/observations/{observation_id}/verdict", json={"verdict": "good"}
    )
    assert first.status_code == 200
    assert first.json()["verdict"] == "good"
    assert first.json()["reviewed_at"].endswith("Z")

    second = client.post(
        f"/api/agent/observations/{observation_id}/verdict", json={"verdict": "bad"}
    )
    assert second.status_code == 200
    assert second.json()["verdict"] == "bad"
    reviewed = client.get("/api/agent/observations", params={"reviewed": "true"})
    assert {entry["verdict"] for entry in reviewed.json()["items"]} == {"bad"}
    assert (
        client.get("/api/agent/observations", params={"rule_id": "sanity-full"}).json()[
            "total"
        ]
        == 1
    )

    missing = client.post(
        "/api/agent/observations/9999/verdict", json={"verdict": "good"}
    )
    assert missing.status_code == 404
    assert _error(missing)["code"] == "OBSERVATION_NOT_FOUND"

    invalid = client.post(
        f"/api/agent/observations/{observation_id}/verdict", json={"verdict": "meh"}
    )
    assert invalid.status_code == 422
    assert _error(invalid)["code"] == "VALIDATION_ERROR"


def test_unassembled_trigger_service_returns_503(
    isolated_db, make_client, tmp_settings
):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(triggers_router.router)
    app.state.session_factory = db_session.session_factory
    client = make_client(app)
    client.portal.call(_create_schema, isolated_db)

    response = client.get("/api/agent/triggers")
    assert response.status_code == 503
    assert _error(response)["code"] == "SERVICE_UNAVAILABLE"

    rules = client.get("/api/agent/trigger-rules")
    assert rules.status_code == 503
    assert _error(rules)["code"] == "SERVICE_UNAVAILABLE"
