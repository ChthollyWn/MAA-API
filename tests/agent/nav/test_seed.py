"""种子图（M86 spec §4.5.6、§6 T3）：15 个界面、36 条跳转、坐标全部归一化。

用例只读 ``ensure_seed()`` 写出的库行；坐标出处由 spec §7.2 的 MAA 资源核对脚本
单独验证（本文件不重复两份坐标表）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from maa_api.agent.nav.graph import NavGraphService
from tests.fakes.v3_db import make_session_factory

#: ``ReturnButton`` 的中心坐标（UiTheme/*.json，1280×720 基准）。
RETURN_BUTTON = (0.0703, 0.0556)

#: 15 个种子界面的 id（spec §4.5.6 表）。
SEED_NODE_IDS = frozenset(
    {
        "home",
        "terminal",
        "main_theme",
        "main_episode",
        "activity_hub",
        "stage_start",
        "infrast",
        "recruit",
        "store",
        "depot",
        "operator_list",
        "headhunt",
        "friends",
        "mail",
        "quick_menu",
    }
)


def _run(tmp_path: Path, scenario) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "seed.db"))

    async def runner() -> None:
        await scenario(NavGraphService(factory))

    try:
        asyncio.run(runner())
    finally:
        engine.sync_engine.dispose()


def test_seed_counts_and_idempotence(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService) -> None:
        assert await service.ensure_seed() == 15

        nodes, edges = await service.graph()
        assert len(nodes) == 15
        assert len(edges) == 36
        assert {node.id for node in nodes} == SEED_NODE_IDS
        assert {node.origin for node in nodes} == {"seed"}
        assert {edge.origin for edge in edges} == {"seed"}

        # 第二次调用看到非空表就直接返回 0，不重复写入。
        assert await service.ensure_seed() == 0
        nodes, edges = await service.graph()
        assert (len(nodes), len(edges)) == (15, 36)

    _run(tmp_path, scenario)


def test_seed_coordinates_are_normalized(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService) -> None:
        await service.ensure_seed()
        nodes, edges = await service.graph()

        return_edges = 0
        for edge in edges:
            action = edge.action
            kind = action["type"]
            if kind == "tap":
                assert 0.0 <= action["x"] <= 1.0, edge.id
                assert 0.0 <= action["y"] <= 1.0, edge.id
                if (action["x"], action["y"]) == RETURN_BUTTON:
                    return_edges += 1
            elif kind == "locate":
                assert isinstance(action["query"], str) and action["query"]
            else:  # 种子里只有 tap 与三条 locate
                raise AssertionError(f"未预期的种子动作：{action!r}")

        # 8 条「各界面回主界面」+ 3 条「主题曲/章节/活动回终端」。
        assert return_edges == 11

        store = next(node for node in nodes if node.id == "store")
        rois = store.recognizer["rois"]
        assert any(roi[0] == 0.7812 for roi in rois if roi != [0, 0, 1, 1])

        stage_start = next(node for node in nodes if node.id == "stage_start")
        assert stage_start.recognizer["rois"] == [[0.7891, 0.8681, 0.2031, 0.0847]]
        assert stage_start.recognizer["generic"] is True
        assert stage_start.kind == "stage"

        # 每个节点都带特征描述（视觉分类候选行的第三列），种子节点 max_distance=10。
        for node in nodes:
            assert node.recognizer["max_distance"] == 10
            assert node.recognizer["description"]

    _run(tmp_path, scenario)


def test_seed_has_no_stage_specific_nodes(tmp_path: Path) -> None:
    async def scenario(service: NavGraphService) -> None:
        await service.ensure_seed()
        nodes, _edges = await service.graph()

        assert not [node for node in nodes if node.id.startswith("stage:")]

    _run(tmp_path, scenario)
