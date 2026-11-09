"""``Navigator``（M86 spec §4.5.4、§6）：逐步执行、校验、首达信任与学习。

用例用 ``tests/fakes/nav.py`` 的 ``FakeNavDevice``（动作键 → 界面）与 ``FakeVision``；
临时库走 ``tests/fakes/v3_db.make_session_factory``。等待动作稳定的 ``_sleep_ms`` 在
本文件里被替换成空实现，用例不真的等 1.2 秒。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.nav import navigator as navigator_module
from maa_api.agent.nav.graph import NavGraphService
from maa_api.agent.nav.navigator import Navigator
from maa_api.agent.nav.recognizer import Recognizer, signature
from maa_api.db import session as db_session
from maa_api.db.models import AgentAudit, NavEdge
from maa_api.domain.errors import AppError, ErrorCode
from tests.fakes.nav import FakeNavDevice, FakeVision, make_screen
from tests.fakes.v3_db import make_session_factory, seed_session

FULL_ROI = [[0.0, 0.0, 1.0, 1.0]]
TAP_A = {"type": "tap", "x": 0.8, "y": 0.89}
TAP_B = {"type": "tap", "x": 0.5, "y": 0.5}


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """动作后的稳定等待与未知界面重试都直接跳过。"""

    async def no_sleep(_milliseconds: int) -> None:
        return None

    monkeypatch.setattr(navigator_module, "_sleep_ms", no_sleep)


@pytest.fixture(autouse=True)
def _archive_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """截图归档落在临时目录，不碰仓库的 resource/。"""
    monkeypatch.setattr(db_session, "DB_PATH", tmp_path / "nav.db")


def _run(tmp_path: Path, scenario) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "nav.db"))
    service = NavGraphService(factory)

    async def runner() -> None:
        await scenario(service, factory)

    try:
        asyncio.run(runner())
    finally:
        engine.sync_engine.dispose()


async def _node(service: NavGraphService, node_id: str, *, kind: str = "screen") -> None:
    await service.upsert_node(node_id, title=node_id, kind=kind, origin="seed")


async def _edge(
    service: NavGraphService,
    from_node: str,
    to_node: str,
    action: dict[str, Any],
    *,
    origin: str = "seed",
) -> NavEdge:
    edge, _created = await service.upsert_edge(
        from_node=from_node, to_node=to_node, action=action, origin=origin
    )
    return edge


async def _exemplar(
    service: NavGraphService, node_id: str, screen_name: str
) -> bool:
    node = await service.get_node(node_id)
    assert node is not None
    rois = (node.recognizer or {}).get("rois") or FULL_ROI
    return await service.add_exemplar(
        node_id,
        phash=signature(make_screen(screen_name), rois),
        screenshot_id=None,
        source="edge",
    )


def _tap_key(x: float, y: float, resolution: tuple[int, int] = (1280, 720)) -> str:
    return f"tap:{round(x * resolution[0])},{round(y * resolution[1])}"


def _nav(
    service: NavGraphService,
    device: FakeNavDevice,
    *,
    learn: bool = True,
    max_steps: int = 12,
    vision: FakeVision | None = None,
    db: AsyncSession | None = None,
    session_id: str | None = None,
) -> Navigator:
    vision = vision or FakeVision(available_result=False)
    return Navigator(
        service,
        Recognizer(service, vision),
        device,
        learn=learn,
        max_steps=max_steps,
        db_session=db,
        session_id=session_id,
    )


async def _between(
    service: NavGraphService, from_node: str, to_node: str
) -> list[NavEdge]:
    _nodes, edges = await service.graph()
    return [
        edge
        for edge in edges
        if edge.from_node == from_node and edge.to_node == to_node
    ]


def test_navigate_one_hop_records_success(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService, _factory) -> None:
        await _node(service, "home")
        await _node(service, "infrast")
        edge = await _edge(service, "home", "infrast", TAP_A)
        await _exemplar(service, "home", "home")
        await _exemplar(service, "infrast", "infrast")
        device = FakeNavDevice({_tap_key(0.8, 0.89): "infrast"}, start="home")

        result = await _nav(service, device).navigate("infrast")

        assert result.arrived is True
        assert result.node_id == "infrast"
        assert result.steps == 1
        assert result.trail[-1]["ok"] is True
        assert result.trail[-1]["actual"] == "infrast"
        stored = await service.get_edge(edge.id)
        assert stored is not None
        assert stored.success_count == 1
        assert stored.cost_ms is not None

    _run(tmp_path, scenario)


def test_already_there_does_nothing(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService, _factory) -> None:
        await _node(service, "home")
        await _exemplar(service, "home", "home")
        device = FakeNavDevice(start="home")

        result = await _nav(service, device).navigate("home")

        assert result.arrived is True
        assert result.steps == 0
        assert result.trail == []
        assert [
            call for call in device.calls if call[0] in {"tap", "swipe", "key", "home"}
        ] == []

    _run(tmp_path, scenario)


def test_first_arrival_bootstraps_seed_exemplar(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService, _factory) -> None:
        await service.ensure_seed()
        await _exemplar(service, "home", "home")
        device = FakeNavDevice({_tap_key(0.8164, 0.8750): "infrast"}, start="home")

        result = await _nav(service, device).navigate("infrast")

        assert result.arrived is True
        assert result.steps == 1
        assert result.learned_exemplars == 1
        assert result.learned_edges == 0
        assert result.trail[-1]["ok"] is True
        node = await service.get_node("infrast")
        assert node is not None
        assert len(node.exemplars) == 1
        assert node.exemplars[0]["source"] == "edge"
        _nodes, edges = await service.graph()
        seed_edge = next(
            edge
            for edge in edges
            if edge.from_node == "home" and edge.to_node == "infrast"
        )
        assert seed_edge.origin == "seed"
        assert seed_edge.success_count == 1

    _run(tmp_path, scenario)


def test_bootstrap_skipped_when_learning_off(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService, _factory) -> None:
        await _node(service, "home")
        await _node(service, "infrast")
        await _edge(service, "home", "infrast", TAP_A)
        await _exemplar(service, "home", "home")
        device = FakeNavDevice({_tap_key(0.8, 0.89): "infrast"}, start="home")

        with pytest.raises(AppError) as error:
            await _nav(service, device, learn=False, max_steps=2).navigate(
                "infrast"
            )

        assert error.value.code is ErrorCode.NAV_STEP_FAILED
        assert error.value.details["reason"] == "max_steps"
        node = await service.get_node("infrast")
        assert node is not None
        assert node.exemplars == []
        home = await service.get_node("home")
        assert home is not None
        assert len(home.exemplars) == 1  # 只有测试预置的那条，没有 anchor

    _run(tmp_path, scenario)


def test_wrong_landing_learns_edge_and_replans(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService, _factory) -> None:
        for node in ("home", "infrast", "depot"):
            await _node(service, node)
        original = await _edge(service, "home", "infrast", TAP_A)
        await _edge(service, "depot", "infrast", TAP_B)
        for node in ("home", "infrast", "depot"):
            await _exemplar(service, node, node)
        device = FakeNavDevice(
            {_tap_key(0.8, 0.89): "depot", _tap_key(0.5, 0.5): "infrast"},
            start="home",
        )

        result = await _nav(service, device).navigate("infrast")

        assert result.arrived is True
        assert result.learned_edges == 1
        stored = await service.get_edge(original.id)
        assert stored is not None
        assert stored.fail_count == 1
        learned = await _between(service, "home", "depot")
        assert len(learned) == 1
        assert learned[0].origin == "learned"
        assert (learned[0].action["type"], learned[0].action["x"]) == ("tap", 0.8)

    _run(tmp_path, scenario)


def test_unknown_screen_recovers_via_home(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService, _factory) -> None:
        await _node(service, "home")
        await _node(service, "target")
        # 便宜的那条是坏边（落到认不出的画面）；回主界面后它被 exclude，只剩好边。
        bad = await _edge(service, "home", "target", TAP_A)
        await service.update_edge(bad.id, cost_ms=1000)
        await _edge(service, "home", "target", TAP_B)
        for node in ("home", "target"):
            await _exemplar(service, node, node)
        device = FakeNavDevice(
            {_tap_key(0.8, 0.89): "mystery", _tap_key(0.5, 0.5): "target"},
            start="home",
        )

        result = await _nav(service, device).navigate("target")

        assert result.arrived is True
        assert result.steps == 3
        assert [item["action"] for item in result.trail] == ["tap", "home", "tap"]
        assert result.trail[0]["ok"] is False
        assert result.trail[0]["actual"] is None
        assert result.trail[1]["from"] == "home"
        assert result.trail[1]["expected"] == "home"
        assert result.trail[-1]["ok"] is True
        assert device.calls.count(("screenshot",)) >= 4  # 未知界面重试了一次
        stored = await service.get_edge(bad.id)
        assert stored is not None
        assert stored.fail_count == 1

    _run(tmp_path, scenario)


def test_home_failure_raises_step_failed(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService, _factory) -> None:
        await _node(service, "home")
        await _node(service, "infrast")
        device = FakeNavDevice(start="mystery", home_result=False)

        with pytest.raises(AppError) as error:
            await _nav(service, device).navigate("infrast")

        assert error.value.code is ErrorCode.NAV_STEP_FAILED
        assert error.value.details["reason"] == "home_failed"
        assert error.value.details["current"] is None
        assert error.value.details["trail"] == []
        assert error.value.details["screenshot_id"] is None
        assert device.calls.count(("home",)) == 1

    _run(tmp_path, scenario)


def test_max_steps_raises_step_failed(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService, _factory) -> None:
        for node in ("home", "mid", "target"):
            await _node(service, node)
        await _edge(service, "home", "mid", TAP_A)
        await _edge(service, "mid", "target", TAP_B)
        for node in ("home", "mid"):
            await _exemplar(service, node, node)
        device = FakeNavDevice(
            {_tap_key(0.8, 0.89): "mid", _tap_key(0.5, 0.5): "target"},
            start="home",
        )

        with pytest.raises(AppError) as error:
            await _nav(service, device, max_steps=1).navigate("target")

        assert error.value.code is ErrorCode.NAV_STEP_FAILED
        assert error.value.details["reason"] == "max_steps"
        assert error.value.details["current"] == "mid"
        assert len(error.value.details["trail"]) == 1
        assert error.value.details["trail"][0]["ok"] is True
        assert [call for call in device.calls if call[0].startswith("tap:")] == [
            (_tap_key(0.8, 0.89),)
        ]

    _run(tmp_path, scenario)


def test_missing_target_suggests_container(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService, _factory) -> None:
        await service.ensure_seed()
        device = FakeNavDevice(start="home")

        with pytest.raises(AppError) as error:
            await _nav(service, device).navigate("stage:1-7")

        assert error.value.code is ErrorCode.NAV_NODE_NOT_FOUND
        assert error.value.details == {
            "target": "stage:1-7",
            "nearest": "main_episode",
        }
        assert device.calls == []

    _run(tmp_path, scenario)


def test_no_path_raises_path_not_found(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService, _factory) -> None:
        await _node(service, "home")
        await _node(service, "island")
        await _exemplar(service, "home", "home")
        device = FakeNavDevice(start="home")

        with pytest.raises(AppError) as error:
            await _nav(service, device).navigate("island")

        assert error.value.code is ErrorCode.NAV_PATH_NOT_FOUND
        assert error.value.details == {
            "from": "home",
            "to": "island",
            "reason": "no_edges",
        }

    _run(tmp_path, scenario)


def test_locate_only_path_raises_needs_vision(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService, _factory) -> None:
        await _node(service, "home")
        await _node(service, "target")
        await _edge(
            service,
            "home",
            "target",
            {"type": "locate", "query": "右下角的开始行动按钮"},
        )
        await _exemplar(service, "home", "home")
        device = FakeNavDevice(start="home")

        with pytest.raises(AppError) as error:
            await _nav(
                service, device, vision=FakeVision(available_result=False)
            ).navigate("target")

        assert error.value.code is ErrorCode.NAV_PATH_NOT_FOUND
        assert error.value.details["reason"] == "needs_vision"
        assert [call for call in device.calls if call[0].startswith("tap:")] == []

    _run(tmp_path, scenario)


def test_locate_edge_learns_tap_edge(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService, _factory) -> None:
        await _node(service, "home")
        await _node(service, "target")
        locate = await _edge(
            service,
            "home",
            "target",
            {"type": "locate", "query": "右下角的开始行动按钮"},
        )
        await _exemplar(service, "home", "home")
        device = FakeNavDevice({_tap_key(0.5, 0.5): "target"}, start="home")
        vision = FakeVision(
            available_result=True, locate_result=(0.5, 0.5, 0.9)
        )

        result = await _nav(service, device, vision=vision).navigate("target")

        assert result.arrived is True
        assert result.learned_edges == 1
        assert result.learned_exemplars == 1
        assert vision.locate_calls == 1
        stored = await service.get_edge(locate.id)
        assert stored is not None
        assert stored.success_count == 1
        edges = await _between(service, "home", "target")
        learned = [edge for edge in edges if edge.origin == "learned"]
        assert len(learned) == 1
        assert learned[0].action == {"type": "tap", "x": 0.5, "y": 0.5}

    _run(tmp_path, scenario)


def test_home_anchor_adds_home_exemplar(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService, _factory) -> None:
        await service.ensure_seed()
        device = FakeNavDevice(start="mystery", home_result=True)

        result = await _nav(service, device).navigate("home")

        assert result.arrived is True
        assert result.steps == 1
        assert result.learned_exemplars == 1
        assert result.learned_edges == 0
        node = await service.get_node("home")
        assert node is not None
        assert len(node.exemplars) == 1
        assert node.exemplars[0]["source"] == "anchor"

    _run(tmp_path, scenario)


def test_trail_has_fixed_keys_and_types(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService, _factory) -> None:
        await _node(service, "home")
        await _node(service, "infrast")
        await _edge(service, "home", "infrast", TAP_A)
        for node in ("home", "infrast"):
            await _exemplar(service, node, node)
        device = FakeNavDevice({_tap_key(0.8, 0.89): "infrast"}, start="home")

        result = await _nav(service, device).navigate("infrast")

        assert result.trail
        for item in result.trail:
            assert set(item) == {
                "edge_id",
                "action",
                "from",
                "expected",
                "actual",
                "ok",
                "ms",
            }
            assert isinstance(item["action"], str)
            assert isinstance(item["ok"], bool)
            assert isinstance(item["ms"], int)
            assert item["ms"] >= 0

    _run(tmp_path, scenario)


def test_remember_creates_node_and_sequence_edge(tmp_path: Path) -> None:
    async def scenario(
        service: NavGraphService,
        factory: async_sessionmaker[AsyncSession],
    ) -> None:
        await service.ensure_seed()
        session_id = await seed_session(factory)
        async with factory() as session:
            session.add_all(
                [
                    AgentAudit(
                        caller="builtin",
                        tool_name="click",
                        status="success",
                        session_id=session_id,
                        arguments={"x": 640, "y": 360},
                    ),
                    AgentAudit(
                        caller="builtin",
                        tool_name="swipe",
                        status="success",
                        session_id=session_id,
                        arguments={
                            "x1": 160,
                            "y1": 180,
                            "x2": 480,
                            "y2": 540,
                            "duration_ms": 250,
                        },
                    ),
                ]
            )
            await session.commit()
            max_id = await session.scalar(select(func.max(AgentAudit.id)))
        assert max_id is not None
        service.last_seen.put(session_id, "home", 0)
        device = FakeNavDevice(start="mystery")

        async with factory() as session:
            result = await _nav(
                service, device, db=session, session_id=session_id
            ).remember(node_id="stage:1-7", title="1-7", kind="stage")

        assert result["node"] == {
            "id": "stage:1-7",
            "title": "1-7",
            "kind": "stage",
            "created": True,
        }
        assert result["exemplar_added"] is True
        assert result["screenshot_id"] is not None
        assert result["edge"] is not None
        assert result["edge"]["from"] == "home"
        assert result["edge"]["action"] == "sequence"
        assert result["edge"]["created"] is True
        assert result["skipped_reason"] is None

        node = await service.get_node("stage:1-7")
        assert node is not None
        assert node.origin == "learned"
        assert node.kind == "stage"
        assert len(node.exemplars) == 1
        assert node.exemplars[0]["source"] == "agent"
        assert node.exemplars[0]["screenshot_id"] is not None

        _nodes, edges = await service.graph()
        learned = next(
            edge
            for edge in edges
            if edge.from_node == "home" and edge.to_node == "stage:1-7"
        )
        assert learned.origin == "learned"
        assert learned.action["type"] == "sequence"
        assert learned.action["steps"] == [
            {"type": "tap", "x": 0.5, "y": 0.5},
            {
                "type": "swipe",
                "x1": 0.125,
                "y1": 0.25,
                "x2": 0.375,
                "y2": 0.75,
                "duration_ms": 250,
            },
        ]
        assert service.last_seen.get(session_id) == ("stage:1-7", int(max_id))

    _run(tmp_path, scenario)


def test_remember_skips_when_screen_is_another_node(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService, _factory) -> None:
        await service.ensure_seed()
        await _exemplar(service, "home", "home")
        device = FakeNavDevice(start="home")

        result = await _nav(service, device).remember(
            node_id="stage:1-7", title="1-7", kind="stage"
        )

        assert result["node"] is None
        assert result["exemplar_added"] is False
        assert result["screenshot_id"] is None
        assert result["edge"] is None
        assert result["skipped_reason"] == "screen_is_home"
        assert await service.get_node("stage:1-7") is None
        _nodes, edges = await service.graph()
        assert len(edges) == 36

    _run(tmp_path, scenario)


def test_learning_off_changes_nothing(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService, _factory) -> None:
        for node in ("home", "infrast", "depot"):
            await _node(service, node)
        original = await _edge(service, "home", "infrast", TAP_A)
        for node in ("home", "depot"):
            await _exemplar(service, node, node)
        device = FakeNavDevice({_tap_key(0.8, 0.89): "depot"}, start="home")

        with pytest.raises(AppError) as error:
            await _nav(service, device, learn=False).navigate("infrast")

        assert error.value.code is ErrorCode.NAV_PATH_NOT_FOUND
        assert error.value.details["reason"] == "no_edges"
        stored = await service.get_edge(original.id)
        assert stored is not None
        assert stored.fail_count == 1
        assert await _between(service, "home", "depot") == []
        _nodes, edges = await service.graph()
        assert len(edges) == 1
        depot = await service.get_node("depot")
        assert depot is not None
        assert len(depot.exemplars) == 1

    _run(tmp_path, scenario)
