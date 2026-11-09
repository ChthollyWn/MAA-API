"""``Recognizer``（spec §4.5.2、§6 T2）：样本近邻、歧义、generic 例外与视觉分类。

假图只实现 ``graph()`` 与 ``get_node()`` 两个方法（T3 的 ``NavGraphService`` 落地前
识别器只依赖这两处）；识别器不得写图。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from maa_api.agent.nav.recognizer import Recognizer, signature
from maa_api.db.models import NavNode
from tests.fakes.nav import FakeVision, make_screen

FULL_ROI = [[0.0, 0.0, 1.0, 1.0]]


def _exemplar(image_name: str, *, rois: list[list[float]] = FULL_ROI) -> dict[str, Any]:
    return {
        "phash": signature(make_screen(image_name), rois),
        "screenshot_id": None,
        "source": "seed",
        "added_at": "2026-11-09T00:00:00",
    }


def _node(
    node_id: str,
    *,
    exemplar_names: tuple[str, ...] = (),
    rois: list[list[float]] = FULL_ROI,
    generic: bool = False,
    max_distance: int = 10,
) -> NavNode:
    recognizer: dict[str, Any] = {
        "rois": rois,
        "max_distance": max_distance,
        "description": f"{node_id} 的特征",
    }
    if generic:
        recognizer["generic"] = True
    return NavNode(
        id=node_id,
        title=node_id,
        kind="stage" if node_id.startswith("stage:") else "screen",
        origin="seed",
        recognizer=recognizer,
        exemplars=[_exemplar(name, rois=rois) for name in exemplar_names],
    )


def _graph(nodes: list[NavNode]) -> SimpleNamespace:
    async def graph() -> tuple[list[NavNode], list[Any]]:
        return list(nodes), []

    async def get_node(node_id: str) -> NavNode | None:
        return next((node for node in nodes if node.id == node_id), None)

    return SimpleNamespace(graph=graph, get_node=get_node)


def _recognizer(nodes: list[NavNode], vision: FakeVision) -> Recognizer:
    return Recognizer(_graph(nodes), vision)


def test_nearest_exemplar_wins() -> None:
    nodes = [
        _node("alpha", exemplar_names=("alpha",)),
        _node("beta", exemplar_names=("beta",)),
    ]
    result = asyncio.run(_recognizer(nodes, FakeVision()).recognize(make_screen("alpha")))

    assert result.node_id == "alpha"
    assert result.method == "hash"
    assert result.distance == pytest.approx(0.0)
    assert result.confidence == pytest.approx(1.0)
    assert result.candidates[0][0] == "alpha"
    assert result.candidates[0][1] == pytest.approx(0.0)
    assert len(result.candidates) <= 3


def test_candidates_include_nodes_below_threshold() -> None:
    """``candidates`` 是诊断用的前 3 名，与哈希是否过阈值无关（spec §4.3.1）。"""
    nodes = [
        _node("alpha", exemplar_names=("alpha",)),
        _node("beta", exemplar_names=("beta",)),
    ]
    result = asyncio.run(_recognizer(nodes, FakeVision()).recognize(make_screen("alpha")))

    assert result.node_id == "alpha"
    candidate_ids = [node_id for node_id, _distance in result.candidates]
    assert candidate_ids == ["alpha", "beta"]
    assert result.candidates[1][1] > _node("beta").recognizer["max_distance"]


def test_ambiguous_match_falls_back_to_vision() -> None:
    nodes = [
        _node("alpha", exemplar_names=("alpha",)),
        _node("beta", exemplar_names=("alpha",)),
    ]
    vision = FakeVision(classify_result=("alpha", 0.9, "看起来是 alpha"))
    result = asyncio.run(_recognizer(nodes, vision).recognize(make_screen("alpha")))

    assert vision.classify_calls == 1
    assert result.node_id == "alpha"
    assert result.method == "vision"
    assert result.distance is None
    assert result.confidence == pytest.approx(0.9)


def test_generic_node_yields_to_specific_stage() -> None:
    nodes = [
        _node("stage_start", exemplar_names=("stage-page",), generic=True),
        _node("stage:1-7", exemplar_names=("stage-page",)),
    ]
    vision = FakeVision(classify_result=("stage_start", 0.99, "不该被调用"))
    result = asyncio.run(_recognizer(nodes, vision).recognize(make_screen("stage-page")))

    assert result.node_id == "stage:1-7"
    assert result.method == "hash"
    assert vision.classify_calls == 0


def test_vision_below_confidence_is_unknown() -> None:
    nodes = [
        _node("alpha", exemplar_names=("alpha",)),
        _node("beta", exemplar_names=("alpha",)),
    ]
    vision = FakeVision(classify_result=("alpha", 0.5, "拿不准"))
    result = asyncio.run(_recognizer(nodes, vision).recognize(make_screen("alpha")))

    assert result.node_id is None
    assert result.method == "none"
    assert result.distance is None
    assert result.confidence == pytest.approx(0.0)
    assert vision.classify_calls == 1


def test_vision_unavailable_returns_unknown() -> None:
    nodes = [
        _node("alpha", exemplar_names=("alpha",)),
        _node("beta", exemplar_names=("alpha",)),
    ]
    vision = FakeVision(classify_result=("alpha", 0.9, "不该被调用"), available_result=False)
    result = asyncio.run(_recognizer(nodes, vision).recognize(make_screen("alpha")))

    assert result.node_id is None
    assert result.method == "none"
    assert vision.available_calls == 1
    assert vision.classify_calls == 0


def test_vision_answer_outside_candidates_is_rejected() -> None:
    nodes = [
        _node("alpha", exemplar_names=("alpha",)),
        _node("beta", exemplar_names=("alpha",)),
        # depot 在图里但没有样本，不会进视觉候选。
        _node("depot"),
    ]
    vision = FakeVision(classify_result=("depot", 0.95, "仓库"))
    result = asyncio.run(_recognizer(nodes, vision).recognize(make_screen("alpha")))

    assert result.node_id is None
    assert result.method == "none"
    assert vision.classify_calls == 1
    candidate_ids = [node.id for node in vision.classify_candidates[0]]
    assert "depot" not in candidate_ids


def test_allow_vision_false_skips_vision() -> None:
    nodes = [
        _node("alpha", exemplar_names=("alpha",)),
        _node("beta", exemplar_names=("alpha",)),
    ]
    vision = FakeVision(classify_result=("alpha", 0.9, "不该被调用"))
    result = asyncio.run(
        _recognizer(nodes, vision).recognize(make_screen("alpha"), allow_vision=False)
    )

    assert result.node_id is None
    assert result.method == "none"
    assert vision.available_calls == 0
    assert vision.classify_calls == 0


def test_recognize_does_not_write_graph() -> None:
    nodes = [
        _node("alpha", exemplar_names=("alpha",)),
        _node("beta", exemplar_names=("alpha",)),
    ]
    before = [(node.id, [dict(item) for item in node.exemplars]) for node in nodes]
    vision = FakeVision(classify_result=("alpha", 0.9, "看起来是 alpha"))

    asyncio.run(_recognizer(nodes, vision).recognize(make_screen("alpha")))

    after = [(node.id, [dict(item) for item in node.exemplars]) for node in nodes]
    assert after == before
