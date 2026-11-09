"""``ModelVisionClient``（spec §4.2、§4.5.2）：依赖取法、可用性与结构化调用参数。

``complete_structured`` 在模块上 monkeypatch，不真的请求上游。
"""

from __future__ import annotations

import asyncio
import base64
import io
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from maa_api.agent.llm.schemas.nav_classification import NavClassification
from maa_api.agent.llm.schemas.nav_locate import NavLocate
from maa_api.agent.nav import recognizer as recognizer_module
from maa_api.agent.nav.recognizer import ModelVisionClient
from maa_api.db.models import NavNode
from maa_api.domain.errors import AppError, ErrorCode
from tests.fakes.nav import make_screen


class _Router:
    def __init__(self, supports_vision: bool | None, *, error: Exception | None = None):
        self.supports_vision = supports_vision
        self.error = error
        self.roles: list[str] = []

    async def resolve(self, role: str) -> Any:
        self.roles.append(role)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(supports_vision=self.supports_vision)


def _client(
    *, router: Any = None, ledger: Any = None, run_id: str | None = None,
    session_id: str | None = None,
) -> ModelVisionClient:
    app_state = SimpleNamespace(
        model_router=router, cost_ledger=ledger if ledger is not None else object()
    )
    return ModelVisionClient(app_state, run_id=run_id, session_id=session_id)


def _node(node_id: str = "home") -> NavNode:
    return NavNode(
        id=node_id,
        title="主界面" if node_id == "home" else node_id,
        kind="screen",
        origin="seed",
        recognizer={"description": "游戏首页"},
    )


def _decode_image(url: str) -> Image.Image:
    header, _, encoded = url.partition(",")
    assert header == "data:image/jpeg;base64"
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as opened:
        return opened.copy()


def test_available_requires_supports_vision() -> None:
    for value, expected in ((True, True), (False, False), (None, False)):
        router = _Router(value)
        client = _client(router=router, ledger=object())
        assert asyncio.run(client.available()) is expected
        assert router.roles == ["vision"]


def test_available_is_false_without_router_or_ledger() -> None:
    for app_state in (
        SimpleNamespace(),
        SimpleNamespace(model_router=object()),
        SimpleNamespace(cost_ledger=object()),
    ):
        client = ModelVisionClient(app_state)
        assert asyncio.run(client.available()) is False


def test_available_is_false_when_profile_resolution_fails() -> None:
    router = _Router(
        None, error=AppError(ErrorCode.MODEL_PROFILE_NOT_FOUND, "没有视觉档案")
    )
    client = ModelVisionClient(SimpleNamespace(model_router=router, cost_ledger=object()))
    assert asyncio.run(client.available()) is False


def test_classify_and_locate_pass_agent_name_and_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_complete_structured(**kwargs: Any) -> Any:
        calls.append(kwargs)
        if kwargs["schema_name"] == "nav_classification":
            return SimpleNamespace(
                value=NavClassification(node_id="home", confidence=0.9, reason="主界面")
            )
        return SimpleNamespace(
            value=NavLocate(found=True, x=0.25, y=0.75, confidence=0.8)
        )

    monkeypatch.setattr(recognizer_module, "complete_structured", fake_complete_structured)

    app_state = SimpleNamespace(model_router=object(), cost_ledger=object())
    client = ModelVisionClient(app_state, run_id="run-1", session_id="session-1")
    image = make_screen("home").resize((640, 360))

    async def scenario() -> tuple[Any, Any]:
        classified = await client.classify(image, [_node("home")])
        located = await client.locate(image, "终端")
        return classified, located

    classified, located = asyncio.run(scenario())
    assert classified == ("home", 0.9, "主界面")
    assert located == (0.25, 0.75, 0.8)

    classify_kwargs, locate_kwargs = calls
    for kwargs, schema_name in (
        (classify_kwargs, "nav_classification"),
        (locate_kwargs, "nav_locate"),
    ):
        assert kwargs["agent_name"] == "navigator"
        assert kwargs["role"] == "vision"
        assert kwargs["schema_name"] == schema_name
        assert kwargs["run_id"] == "run-1"
        assert kwargs["session_id"] == "session-1"
        assert kwargs["router"] is app_state.model_router
        assert kwargs["ledger"] is app_state.cost_ledger

    classify_content = classify_kwargs["messages"][0]["content"]
    assert classify_content[0]["type"] == "text"
    assert "home｜主界面｜游戏首页" in classify_content[0]["text"]
    assert classify_content[1]["type"] == "image_url"
    image_url = classify_content[1]["image_url"]["url"]
    assert image_url.startswith("data:image/jpeg;base64,")
    # 640×360 的输入被缩放到 1280×720。
    assert _decode_image(image_url).size == (1280, 720)

    locate_text = locate_kwargs["messages"][0]["content"][0]["text"]
    assert "「终端」" in locate_text


@pytest.mark.parametrize(
    "error",
    [
        AppError(ErrorCode.STRUCTURED_OUTPUT_INVALID, "两次都不合法"),
        RuntimeError("上游炸了"),
    ],
)
def test_structured_failure_returns_none(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    async def broken(**_kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr(recognizer_module, "complete_structured", broken)
    client = _client(router=object(), ledger=object())

    async def scenario() -> tuple[Any, Any]:
        return (
            await client.classify(make_screen("home"), [_node("home")]),
            await client.locate(make_screen("home"), "终端"),
        )

    classified, located = asyncio.run(scenario())
    assert classified is None
    assert located is None
