"""``NavGraphService``（M86 spec §4.2、§4.5.3、§6）：动作模型、最短路径与记账。

临时库走 ``tests/fakes/v3_db.make_session_factory``（``SQLModel.metadata.create_all``，
与生产 Alembic 等价）；用例结束 ``engine.sync_engine.dispose()``。异步场景统一写成
「同步测试函数 + ``asyncio.run(scenario())``」，与 ``tests/agent/tickets`` 同口径。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from sqlalchemy import func, select

from maa_api.agent.nav.graph import (
    HOME_NODE_ID,
    NavGraphService,
    TapAction,
    action_hash,
    validate_action,
)
from maa_api.db.models import AgentAudit
from maa_api.domain.errors import AppError, ErrorCode
from tests.fakes.v3_db import make_session_factory, seed_session

TAP = {"type": "tap", "x": 0.5, "y": 0.5}


def _run(
    tmp_path: Path, scenario: Callable[[NavGraphService], Awaitable[None]]
) -> None:
    """建临时库、跑场景、最后销毁同步引擎（不依赖事件循环）。"""
    engine, factory = asyncio.run(make_session_factory(tmp_path / "graph.db"))
    service = NavGraphService(factory)

    async def runner() -> None:
        await scenario(service)

    try:
        asyncio.run(runner())
    finally:
        engine.sync_engine.dispose()


async def _node(service: NavGraphService, node_id: str) -> None:
    await service.upsert_node(node_id, title=node_id, kind="screen", origin="seed")


async def _edge(
    service: NavGraphService,
    from_node: str,
    to_node: str,
    action: dict[str, object] | None = None,
    *,
    cost_ms: int | None = None,
) -> str:
    edge, _created = await service.upsert_edge(
        from_node=from_node,
        to_node=to_node,
        action=dict(action or TAP),
        origin="seed",
    )
    if cost_ms is not None:
        await service.update_edge(edge.id, cost_ms=cost_ms)
    return edge.id


def test_action_hash_ignores_wait_and_rounds_coordinates() -> None:
    """``wait_ms`` 不参与哈希，坐标保留 3 位；差 0.002 必须区分（spec §4.2）。"""
    first = TapAction(type="tap", x=0.5, y=0.5, wait_ms=1200)
    second = TapAction(type="tap", x=0.5001, y=0.5001, wait_ms=9000)
    third = TapAction(type="tap", x=0.502, y=0.5)

    assert action_hash(first) == action_hash(second)
    assert action_hash(first) != action_hash(third)


def test_validate_action_rejects_unknown_fields() -> None:
    action = validate_action(dict(TAP))
    assert isinstance(action, TapAction)
    assert action.wait_ms == 1200

    with pytest.raises(AppError) as error:
        validate_action({"type": "tap", "x": 0.5, "y": 0.5, "shot": True})
    assert error.value.code is ErrorCode.INVALID_PARAMETER


def test_shortest_path_prefers_cheaper_edges(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService) -> None:
        for node in ("a1", "b1", "c1"):
            await _node(service, node)
        direct = await _edge(service, "a1", "b1", cost_ms=5000)
        first = await _edge(service, "a1", "c1", cost_ms=1000)
        second = await _edge(service, "c1", "b1", cost_ms=1000)

        plan = await service.shortest_path("a1", "b1", vision=True)

        assert plan is not None
        assert plan.blocked_by_vision is False
        assert [step.edge_id for step in plan.steps] == [first, second]
        assert [step.to_node for step in plan.steps] == ["c1", "b1"]
        assert direct not in {step.edge_id for step in plan.steps}

    _run(tmp_path, scenario)


def test_disabled_and_excluded_edges_are_skipped(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService) -> None:
        for node in ("a1", "b1", "c1"):
            await _node(service, node)
        direct = await _edge(service, "a1", "b1", cost_ms=1000)
        first = await _edge(service, "a1", "c1", cost_ms=1000)
        second = await _edge(service, "c1", "b1", cost_ms=1000)

        plan = await service.shortest_path("a1", "b1", vision=True)
        assert plan is not None
        assert [step.edge_id for step in plan.steps] == [direct]

        await service.update_edge(direct, enabled=False)
        plan = await service.shortest_path("a1", "b1", vision=True)
        assert plan is not None
        assert [step.edge_id for step in plan.steps] == [first, second]

        await service.update_edge(direct, enabled=True)
        plan = await service.shortest_path(
            "a1", "b1", vision=True, exclude={direct}
        )
        assert plan is not None
        assert [step.edge_id for step in plan.steps] == [first, second]

    _run(tmp_path, scenario)


def test_implicit_home_edge_connects_everything(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService) -> None:
        await _node(service, "island")

        plan = await service.shortest_path("island", HOME_NODE_ID, vision=True)

        assert plan is not None
        assert plan.blocked_by_vision is False
        assert len(plan.steps) == 1
        step = plan.steps[0]
        assert step.edge_id is None
        assert step.action == {"type": "home"}
        assert step.origin == "implicit"
        assert step.from_node == "island"
        assert step.to_node == HOME_NODE_ID
        # 反向没有隐式边：home 不会凭空走到 island。
        assert await service.shortest_path(HOME_NODE_ID, "island", vision=True) is None

    _run(tmp_path, scenario)


def test_locate_edges_need_vision(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService) -> None:
        for node in ("a1", "b1"):
            await _node(service, node)
        locate = await _edge(
            service,
            "a1",
            "b1",
            {"type": "locate", "query": "右下角的开始行动按钮"},
        )

        plan = await service.shortest_path("a1", "b1", vision=True)
        assert plan is not None
        assert [step.edge_id for step in plan.steps] == [locate]

        blocked = await service.shortest_path("a1", "b1", vision=False)
        assert blocked is not None
        assert blocked.steps == ()
        assert blocked.blocked_by_vision is True

    _run(tmp_path, scenario)


def test_shortest_path_returns_none_without_any_path(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService) -> None:
        await _node(service, HOME_NODE_ID)
        await _node(service, "island")

        assert await service.shortest_path(HOME_NODE_ID, "island", vision=True) is None

    _run(tmp_path, scenario)


def test_same_node_returns_empty_plan(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService) -> None:
        await _node(service, HOME_NODE_ID)

        plan = await service.shortest_path(HOME_NODE_ID, HOME_NODE_ID, vision=False)

        assert plan is not None
        assert plan.steps == ()
        assert plan.blocked_by_vision is False

    _run(tmp_path, scenario)


def test_three_failures_disable_edge(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService) -> None:
        await _node(service, "a1")
        await _node(service, "b1")
        edge_id = await _edge(service, "a1", "b1")

        assert await service.record_failure(edge_id) is False
        assert await service.record_failure(edge_id) is False
        assert await service.record_failure(edge_id) is True

        edge = await service.get_edge(edge_id)
        assert edge is not None
        assert (edge.fail_count, edge.success_count, edge.enabled) == (3, 0, False)
        # 已经停用的边再失败不重复「触发停用」。
        assert await service.record_failure(edge_id) is False

    _run(tmp_path, scenario)


def test_failures_do_not_disable_a_proven_edge(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService) -> None:
        await _node(service, "a1")
        await _node(service, "b1")
        edge_id = await _edge(service, "a1", "b1")
        for _ in range(5):
            await service.record_success(edge_id, elapsed_ms=1000)

        results = [await service.record_failure(edge_id) for _ in range(3)]

        edge = await service.get_edge(edge_id)
        assert results == [False, False, False]
        assert edge is not None
        assert (edge.fail_count, edge.success_count, edge.enabled) == (3, 5, True)

    _run(tmp_path, scenario)


def test_success_updates_cost_moving_average(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService) -> None:
        await _node(service, "a1")
        await _node(service, "b1")
        edge_id = await _edge(service, "a1", "b1")

        await service.record_success(edge_id, elapsed_ms=1000)
        edge = await service.get_edge(edge_id)
        assert edge is not None
        assert edge.cost_ms == 1000
        assert edge.success_count == 1
        assert edge.last_used_at is not None

        await service.record_success(edge_id, elapsed_ms=2000)
        edge = await service.get_edge(edge_id)
        assert edge is not None
        assert edge.cost_ms == 1300
        assert edge.success_count == 2

    _run(tmp_path, scenario)


def test_reenable_resets_fail_count(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService) -> None:
        await _node(service, "a1")
        await _node(service, "b1")
        edge_id = await _edge(service, "a1", "b1")
        for _ in range(3):
            await service.record_failure(edge_id)

        edge = await service.update_edge(edge_id, enabled=True)

        assert edge.enabled is True
        assert edge.fail_count == 0

    _run(tmp_path, scenario)


def test_exemplars_dedupe_and_cap(tmp_path: Path) -> None:
    """近重复不加；第 9 个挤掉最早的非 user 样本；source 与 ISO Z 时间落 JSON。"""

    async def scenario(service: NavGraphService) -> None:
        await _node(service, "home")
        first = "01" * 8
        # 只差最后 1 bit：每个 ROI 距离 1，落在 DUPLICATE_DISTANCE 内。
        near = "01" * 7 + "00"

        assert (
            await service.add_exemplar(
                "home", phash=first, screenshot_id="shot-1", source="edge"
            )
            is True
        )
        assert (
            await service.add_exemplar(
                "home", phash=near, screenshot_id="shot-2", source="agent"
            )
            is False
        )

        for index in range(2, 10):
            # 每个字节相同、不同样本之间的汉明距离是 8 的倍数，必然 > 3。
            phash = f"{index:02x}" * 8
            assert (
                await service.add_exemplar(
                    "home", phash=phash, screenshot_id=None, source="edge"
                )
                is True
            )

        node = await service.get_node("home")
        assert node is not None
        assert len(node.exemplars) == 8
        phashes = [item["phash"] for item in node.exemplars]
        assert first not in phashes
        assert len(set(phashes)) == 8
        for item in node.exemplars:
            assert set(item) == {"phash", "screenshot_id", "source", "added_at"}
            assert item["source"] == "edge"
            assert str(item["added_at"]).endswith("Z")

    _run(tmp_path, scenario)


def test_last_seen_cursor_advances(tmp_path: Path) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "audit.db"))
    service = NavGraphService(factory)

    async def scenario() -> None:
        session_id = await seed_session(factory)
        async with factory() as session:
            session.add(
                AgentAudit(
                    caller="builtin",
                    tool_name="click",
                    status="success",
                    session_id=session_id,
                )
            )
            await session.commit()
            session.add(
                AgentAudit(
                    caller="builtin",
                    tool_name="swipe",
                    status="success",
                    session_id=session_id,
                )
            )
            await session.commit()
            max_id = await session.scalar(select(func.max(AgentAudit.id)))

        await service.note_seen(session_id, "home")

        assert max_id is not None
        assert service.last_seen.get(session_id) == ("home", int(max_id))

    try:
        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()
