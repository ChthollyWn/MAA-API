"""M84 T4：REST 调消耗工具时的确认前预检（spec §4.5.6、§6）。

``invoke_tool`` 在确认服务之前调 ``precheck_tool_call``：超额时返回 403
``SPEND_CAP_EXCEEDED``、不建确认卡，并落一条 ``rejected`` 审计。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import maa_api.agent.tools.pipeline as pipeline_tools
from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.policy import PolicyEngine
from maa_api.agent.registry import ToolRegistry
from maa_api.agent.spend import SpendGuard
from maa_api.api.deps import get_session
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import agent, confirmations
from maa_api.db.models import SpendLedger
from maa_api.settings import Settings, SpendSettings, set_settings
from tests.fakes.v3_db import make_session_factory

#: 北京 2026-11-05 20:00（naive UTC），消耗日 2026-11-05。
NOW = datetime(2026, 11, 5, 12, 0)


class _Limits:
    def __init__(self, *, medicine: int = 5) -> None:
        self.medicine = medicine

    def __call__(self) -> Settings:
        return Settings(
            spend=SpendSettings(daily_medicine_limit=self.medicine)
        )


async def _seed_used_medicine(factory) -> None:
    async with factory() as session:
        session.add(
            SpendLedger(
                kind="medicine",
                planned=4,
                actual=4,
                source="manual",
                created_at=NOW,
            )
        )
        await session.commit()


def test_rest_invoke_over_cap_returns_403_without_confirmation(
    tmp_path: Path,
) -> None:
    async def prepare():
        engine, factory = await make_session_factory(tmp_path / "rest-spend.db")
        guard = SpendGuard(
            factory, settings_provider=_Limits(medicine=5), clock=lambda: NOW
        )
        await _seed_used_medicine(factory)
        return engine, factory, guard

    engine, factory, guard = asyncio.run(prepare())

    registry = ToolRegistry()
    pipeline_tools.register_tools(registry)
    service = ConfirmationService(factory, registry, policy=PolicyEngine())
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(agent.router)
    app.include_router(confirmations.router)
    app.state.confirmation_service = service
    app.state.session_factory = factory
    app.state.spend_guard = guard

    async def get_test_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = get_test_session
    set_settings(Settings())
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()
    try:
        response = client.post(
            "/api/agent/tools/submit_pipeline/invoke",
            json={
                "arguments": {
                    "tasks": [
                        {"name": "Fight", "stage": "1-7", "medicine": 2}
                    ]
                },
                "mode": "sync",
            },
        )
        assert response.status_code == 403
        error = response.json()["error"]
        assert error["code"] == "SPEND_CAP_EXCEEDED"
        assert error["details"]["kind"] == "medicine"
        assert error["details"]["source"] == "agent"
        assert error["details"]["limit"] == 5
        assert error["details"]["used"] == 4
        assert error["details"]["requested"] == 2

        pending = client.get("/api/confirmations?status=pending")
        assert pending.status_code == 200
        assert pending.json()["total"] == 0

        audits = client.get("/api/agent/audits")
        assert audits.status_code == 200
        body = audits.json()
        assert body["total"] == 1
        audit = body["items"][0]
        assert audit["status"] == "rejected"
        assert audit["error_code"] == "SPEND_CAP_EXCEEDED"
        assert audit["caller"] == "rest"
        assert audit["tool_name"] == "submit_pipeline"
    finally:
        client.__exit__(None, None, None)
        set_settings(None)
        engine.sync_engine.dispose()
