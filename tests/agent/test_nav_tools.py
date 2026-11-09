"""``nav`` 工具组（M86 spec §4.3.1、§4.5.5、§6）：目录契约、纯读、授权与显式记录。

三个工具注册在 ``nav`` 组，只给 ``navigator``：``where_am_i`` 是 ``read`` 档的纯读，
``remember_screen`` 是 ``none`` 档的显式学习，``navigate_to`` 是 ``exclusive`` 档的
原子操作。设备用 ``tests/fakes/nav.py`` 的 ``FakeNavDevice``，库走
``tests/fakes/v3_db.make_session_factory`` 的临时 SQLite。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select

from maa_api.agent.nav import NavGraphService
from maa_api.agent.nav.recognizer import signature
from maa_api.agent.policy import PolicyEngine
from maa_api.agent.registry import ToolContext
from maa_api.agent.tools import build_registry
from maa_api.agent.tools import nav as nav_tools
from maa_api.db import session as db_session
from maa_api.db.models import AgentSession, Confirmation, Screenshot, utcnow
from maa_api.domain.enums import CallerType, ConfirmationStatus, RiskLevel
from maa_api.domain.errors import AppError, ErrorCode
from tests.fakes.nav import FakeNavDevice, make_screen
from tests.fakes.v3_db import make_session_factory


def _context(
    service: NavGraphService,
    *,
    caller: CallerType = CallerType.INTERNAL,
    session_id: str | None = None,
    db: Any = None,
) -> ToolContext:
    """最小工具上下文：``app.state`` 只装 ``nav_service``（视觉依赖缺省不可用）。"""
    state = SimpleNamespace(nav_service=service)
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    return ToolContext(caller, session_id, "request-nav", request, db)


def _params(name: str, arguments: dict[str, Any]) -> Any:
    return build_registry().get(name).params_model.model_validate(arguments)


def _run(tmp_path: Path, scenario) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "nav-tools.db"))
    service = NavGraphService(factory)

    async def runner() -> None:
        await service.ensure_seed()
        await scenario(service, factory)

    try:
        asyncio.run(runner())
    finally:
        engine.sync_engine.dispose()


@pytest.fixture(autouse=True)
def _archive_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """截图归档落在临时目录，不碰仓库的 resource/。"""
    monkeypatch.setattr(db_session, "DB_PATH", tmp_path / "nav-tools.db")


# ----------------------------------------------------------------------
# 目录契约
# ----------------------------------------------------------------------


def test_nav_tools_registered_with_extension_fields() -> None:
    """§4.3.1：组 ``nav``；device 为 read/none/exclusive；扩展字段逐项照表。"""

    registry = build_registry()
    definitions = {definition.name: definition for definition in registry.list("nav")}

    assert set(definitions) == {"where_am_i", "remember_screen", "navigate_to"}

    where = definitions["where_am_i"]
    assert where.risk.value == "SAFE"
    assert where.device == "read"
    assert where.read_only is True
    assert where.refetchable is True
    assert where.rest_visible is True

    remember = definitions["remember_screen"]
    assert remember.risk.value == "SAFE"
    assert remember.device == "none"
    assert remember.read_only is False
    assert remember.refetchable is False
    assert remember.rest_visible is False

    navigate = definitions["navigate_to"]
    assert navigate.risk.value == "DANGEROUS"
    assert navigate.device == "exclusive"
    assert navigate.read_only is False
    assert navigate.refetchable is False
    assert navigate.rest_visible is True

    # 参数模型都是 extra="forbid"，字段照 §4.3.1。
    with pytest.raises(Exception):
        _params("where_am_i", {"unexpected": 1})
    with pytest.raises(Exception):
        _params("remember_screen", {"node_id": "home", "title": "主界面", "x": 1})
    with pytest.raises(Exception):
        _params("navigate_to", {"target": "home", "x": 1})


# ----------------------------------------------------------------------
# where_am_i
# ----------------------------------------------------------------------


def test_where_am_i_is_pure_read(tmp_path: Path) -> None:
    """§4.3.1：截图识别不写库、不写文件；节点、样本与截图行都不变。"""

    async def scenario(service: NavGraphService, factory) -> None:
        service.device_factory = lambda _context: FakeNavDevice(start="home")
        context = _context(service, session_id="session-nav")

        before_nodes, before_edges = await service.graph()
        before = [
            (node.id, list(node.exemplars or []), dict(node.recognizer or {}))
            for node in before_nodes
        ]
        async with factory() as db:
            before_screenshots = await db.scalar(
                select(func.count()).select_from(Screenshot)
            )

        result = await build_registry().get("where_am_i").handler(
            _params("where_am_i", {}), context
        )

        after_nodes, after_edges = await service.graph()
        after = [
            (node.id, list(node.exemplars or []), dict(node.recognizer or {}))
            for node in after_nodes
        ]
        async with factory() as db:
            after_screenshots = await db.scalar(
                select(func.count()).select_from(Screenshot)
            )

        assert before == after
        assert len(before_edges) == len(after_edges)
        assert before_screenshots == 0 == after_screenshots
        # 种子没有样本，认不出；返回形状仍是 §4.3.1 的 JSON。
        assert result["node"] is None
        assert result["method"] == "none"
        assert result["distance"] is None
        assert result["confidence"] == 0
        assert isinstance(result["candidates"], list)
        # note_seen 只在进程内，库里没有痕迹。
        assert service.last_seen.get("session-nav") is None

    _run(tmp_path, scenario)


def test_where_am_i_returns_node_confidence_and_candidates(tmp_path: Path) -> None:
    """认出节点时按 §4.3.1 返回 node/candidates，hash 置信度夹在 0–1。"""

    async def scenario(service: NavGraphService, factory) -> None:
        home = await service.get_node("home")
        assert home is not None
        rois = (home.recognizer or {}).get("rois") or [[0.0, 0.0, 1.0, 1.0]]
        await service.add_exemplar(
            "home",
            phash=signature(make_screen("home"), rois),
            screenshot_id=None,
            source="edge",
        )
        service.device_factory = lambda _context: FakeNavDevice(start="home")
        context = _context(service, session_id="session-nav")

        result = await build_registry().get("where_am_i").handler(
            _params("where_am_i", {}), context
        )

        assert result["node"]["id"] == "home"
        assert result["node"]["title"] == "主界面"
        assert result["node"]["kind"] == "screen"
        assert result["method"] == "hash"
        assert result["distance"] == 0
        assert 0 <= result["confidence"] <= 1
        assert result["confidence"] == 1
        assert [item["id"] for item in result["candidates"]][:1] == ["home"]
        assert set(result["candidates"][0]) == {"id", "title", "distance"}

    _run(tmp_path, scenario)


# ----------------------------------------------------------------------
# 授权（§4.5.5）
# ----------------------------------------------------------------------


def test_navigate_to_uses_atomic_grant(tmp_path: Path) -> None:
    """内置调用：没有有效授权 → ``grant_atomic_ops``；有授权 → 直接放行。"""

    async def scenario(_service: NavGraphService, factory) -> None:
        definition = build_registry().get("navigate_to")
        now = utcnow()
        async with factory() as db:
            db.add(AgentSession(id="nav-session", model="test-model"))
            await db.commit()
            context = _context(
                _service, session_id="nav-session", db=db
            )

            pending = await PolicyEngine().evaluate(definition, {}, context)
            assert pending.requires_confirmation is True
            assert pending.confirmation_action == "grant_atomic_ops"

            grant = Confirmation(
                id="nav-atomic-grant",
                action="grant_atomic_ops",
                risk_level=RiskLevel.NONE,
                reason="允许本次会话直接操作游戏界面",
                payload={"session_id": "nav-session", "window_seconds": 900},
                status=ConfirmationStatus.APPROVED,
                requested_by=CallerType.INTERNAL,
                expires_at=now + timedelta(minutes=2),
            )
            db.add(grant)
            await db.commit()
            agent_session = await db.get(AgentSession, "nav-session")
            assert agent_session is not None
            agent_session.atomic_grant_id = grant.id
            agent_session.atomic_grant_expires_at = now + timedelta(minutes=15)
            db.add(agent_session)
            await db.commit()

            allowed = await PolicyEngine().evaluate(definition, {}, context)
            assert allowed.requires_confirmation is False
            assert allowed.authorized_by == grant.id

    _run(tmp_path, scenario)


def test_rest_navigate_to_needs_confirmation_each_time(
    tmp_path: Path,
) -> None:
    """REST 调用逐次确认：两次评估都要确认，动作是 ``navigate_to`` 本身。"""

    async def scenario(service: NavGraphService, _factory) -> None:
        definition = build_registry().get("navigate_to")
        context = _context(service, caller=CallerType.REST)
        engine = PolicyEngine()

        first = await engine.evaluate(definition, {}, context)
        second = await engine.evaluate(definition, {}, context)

        for decision in (first, second):
            assert decision.requires_confirmation is True
            assert decision.confirmation_action == "navigate_to"

    _run(tmp_path, scenario)


def test_where_am_i_is_safe(tmp_path: Path) -> None:
    """``where_am_i`` 是 SAFE：REST 与内置调用都不确认。"""

    async def scenario(service: NavGraphService, _factory) -> None:
        definition = build_registry().get("where_am_i")
        engine = PolicyEngine()

        for caller in (CallerType.REST, CallerType.INTERNAL):
            decision = await engine.evaluate(
                definition, {}, _context(service, caller=caller)
            )
            assert decision.requires_confirmation is False
            assert decision.risk.value == "none"

    _run(tmp_path, scenario)


# ----------------------------------------------------------------------
# remember_screen
# ----------------------------------------------------------------------


def test_remember_screen_creates_node_and_exemplar(tmp_path: Path) -> None:
    """§4.5.5：节点 ``origin=learned``、第一个样本 ``source=agent``、``created=true``。"""

    async def scenario(service: NavGraphService, factory) -> None:
        service.device_factory = lambda _context: FakeNavDevice(start="stage-1-7")
        async with factory() as db:
            context = _context(service, session_id="session-remember", db=db)
            result = await build_registry().get("remember_screen").handler(
                _params(
                    "remember_screen",
                    {"node_id": "stage:1-7", "title": "1-7"},
                ),
                context,
            )

        assert result["node"]["id"] == "stage:1-7"
        assert result["node"]["title"] == "1-7"
        assert result["node"]["kind"] == "stage"
        assert result["node"]["created"] is True
        assert result["exemplar_added"] is True
        assert result["screenshot_id"] is not None
        assert result["edge"] is None
        assert result["skipped_reason"] == "no_last_seen"

        node = await service.get_node("stage:1-7")
        assert node is not None
        assert node.origin == "learned"
        assert node.kind == "stage"
        assert node.exemplars[0]["source"] == "agent"
        assert node.exemplars[0]["phash"]

    _run(tmp_path, scenario)


# ----------------------------------------------------------------------
# nav.enabled=false
# ----------------------------------------------------------------------


def test_nav_disabled_raises_service_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§4.3.1：三个工具都抛 ``SERVICE_UNAVAILABLE``，``details.setting`` 指开关。"""

    monkeypatch.setattr(
        nav_tools,
        "get_settings",
        lambda: SimpleNamespace(
            nav=SimpleNamespace(enabled=False, learn=True, max_steps=12)
        ),
    )

    async def scenario(service: NavGraphService, _factory) -> None:
        context = _context(service)
        cases = {
            "where_am_i": {},
            "remember_screen": {"node_id": "home", "title": "主界面"},
            "navigate_to": {"target": "home"},
        }
        for name, arguments in cases.items():
            definition = build_registry().get(name)
            with pytest.raises(AppError) as excinfo:
                await definition.handler(_params(name, arguments), context)
            assert excinfo.value.code == ErrorCode.SERVICE_UNAVAILABLE
            assert excinfo.value.details["setting"] == "nav.enabled"

    _run(tmp_path, scenario)
