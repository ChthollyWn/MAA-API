"""M23 §3.1–§3.6：媒体卡契约与五个媒体工具。

没有 pytest-asyncio：异步用例统一写成「同步测试函数 + ``asyncio.run(scenario())``」。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import platform
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册 13 张表到 SQLModel.metadata
from maa_api.agent import media as media_contract
from maa_api.agent.registry import ToolContext
from maa_api.agent.tools import build_registry
from maa_api.agent.tools import media as media_tools
from maa_api.db import session as db_session
from maa_api.db.models import AgentMessage, AgentSession, Pipeline, Screenshot
from maa_api.db.repositories.agent import AgentMessageRepository, AgentSessionRepository
from maa_api.db.repositories.log import ScreenshotRepository
from maa_api.db.session import make_engine
from maa_api.domain.enums import (
    CallerType,
    PipelineSource,
    PipelineStatus,
    Priority,
    ScreenshotBackend,
    ScreenshotTrigger,
)
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services import resource_index
from maa_api.util.image import store_screenshot

REPO_ROOT = Path(__file__).resolve().parents[2]


def _local_resource_root() -> Path:
    folder = {"Darwin": "Darwin", "Linux": "Linux", "Windows": "Win32"}.get(
        platform.system(), platform.system()
    )
    return REPO_ROOT / "resource" / "lib" / "maa" / folder / "resource"


LOCAL_RESOURCE = _local_resource_root()


# ----------------------------------------------------------------------
# 夹具：临时 resource 根 + 内存库 + 工具调用
# ----------------------------------------------------------------------


@pytest.fixture
def resource_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """临时 ``<resource>/``：``store_screenshot`` 与截图文件解析都按 DB_PATH 推导。"""
    root = tmp_path / "resource"
    root.mkdir()
    monkeypatch.setattr(db_session, "DB_PATH", root / "maa_api.db")
    return root


@pytest.fixture
def local_items(monkeypatch: pytest.MonkeyPatch) -> None:
    if not (LOCAL_RESOURCE / "item_index.json").is_file():
        pytest.skip("仓库未自带 MaaCore 资源")
    monkeypatch.setattr(resource_index, "resource_root", lambda: LOCAL_RESOURCE)


@contextlib.asynccontextmanager
async def _engine() -> AsyncIterator[AsyncEngine]:
    engine = make_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


def _context(
    session: AsyncSession | None = None,
    *,
    session_id: str | None = None,
    state: Any = None,
) -> ToolContext:
    request = (
        SimpleNamespace(app=SimpleNamespace(state=state))
        if state is not None
        else None
    )
    return ToolContext(
        caller=CallerType.INTERNAL if session_id else CallerType.REST,
        session_id=session_id,
        request_id="media-test",
        request=request,
        db_session=session,
    )


async def _call(
    factory: async_sessionmaker[AsyncSession],
    name: str,
    arguments: dict[str, Any],
    *,
    session_id: str | None = None,
    state: Any = None,
):
    definition = build_registry().get(name)
    params = definition.params_model.model_validate(arguments)
    async with factory() as session:
        context = _context(session, session_id=session_id, state=state)
        return await definition.handler(params, context)


def _params(name: str, arguments: dict[str, Any]) -> Any:
    return build_registry().get(name).params_model.model_validate(arguments)


async def _store(
    root: Path,
    *,
    color: tuple[int, int, int] = (10, 20, 30),
    size: tuple[int, int] = (320, 200),
) -> dict[str, Any]:
    image = Image.new("RGB", size, color)
    return await asyncio.to_thread(store_screenshot, image, quality=80)


async def _screenshot_row(
    session: AsyncSession,
    stored: dict[str, Any],
    *,
    trigger: ScreenshotTrigger = ScreenshotTrigger.AGENT,
    pipeline_id: str | None = None,
    backend: str = ScreenshotBackend.ADB.value,
    deleted_at: Any = None,
    created_at: Any = None,
) -> Screenshot:
    record = Screenshot(
        trigger=trigger,
        backend=backend,
        path=f"image/screenshot/{stored['sha256'][:2]}/{stored['sha256']}.jpg",
        format="jpg",
        width=int(stored["width"]),
        height=int(stored["height"]),
        size_bytes=int(stored["bytes"]),
        pipeline_id=pipeline_id,
        deleted_at=deleted_at,
        **({"created_at": created_at} if created_at is not None else {}),
    )
    stored_record = await ScreenshotRepository(session).create(record)
    await session.commit()
    return stored_record


async def _agent_session(session: AsyncSession, session_id: str = "session-1") -> str:
    stored = await AgentSessionRepository(session).create(
        AgentSession(id=session_id, model="test-model", base_url="https://llm.test/v1")
    )
    await session.commit()
    return stored.id


async def _tool_message(
    session: AsyncSession,
    session_id: str,
    *,
    seq: int,
    content: Any,
    tool_name: str = "send_screenshot",
) -> None:
    await AgentMessageRepository(session).create(
        AgentMessage(
            session_id=session_id,
            seq=seq,
            role="tool",
            content=content if isinstance(content, str) else json.dumps(content),
            tool_call_id=f"call-{seq}",
            tool_name=tool_name,
        )
    )
    await session.commit()


# ----------------------------------------------------------------------
# 卡契约（§3.1）
# ----------------------------------------------------------------------


def test_media_tool_names_match_the_registered_media_group() -> None:
    registry = build_registry()

    assert media_contract.MEDIA_TOOL_NAMES == {
        "send_screenshot",
        "annotate_screenshot",
        "send_item_icons",
        "send_stage_map",
        "send_chart",
    }
    assert {definition.name for definition in registry.list("media")} == set(
        media_contract.MEDIA_TOOL_NAMES
    )


def test_media_tools_are_safe_and_only_annotate_writes() -> None:
    registry = build_registry()

    for name in media_contract.MEDIA_TOOL_NAMES:
        definition = registry.get(name)
        assert definition.group == "media"
        assert definition.risk.value == "SAFE"
        assert definition.model_visible is True
        assert definition.read_only is (name != "annotate_screenshot")


def test_screenshot_sending_descriptions_gate_on_an_explicit_user_request() -> None:
    """本轮裁决：两个「发截图给用户」的工具只在用户明确要求时才该被调用。"""

    registry = build_registry()

    send = registry.get("send_screenshot").description
    assert "只在用户要求看画面、要一张截图时才调用" in send
    assert "自己为了判断而截的图不要发给用户" in send

    annotate = registry.get("annotate_screenshot").description
    assert "只在用户要求把某处圈出 / 放大时才调用，不要主动发" in annotate


def test_same_origin_url_builders_are_the_three_whitelisted_shapes() -> None:
    sha = "a" * 64

    assert media_contract.url_for_sha256(sha) == f"/api/images/{sha}/full"
    assert media_contract.thumb_url_for_sha256(sha) == f"/api/images/{sha}/thumb"
    assert media_contract.url_for_screenshot("shot-1") == "/api/screenshots/shot-1"
    assert (
        media_contract.item_icon_url("2001")
        == "/api/resources/items/icon?item_id=2001"
    )
    assert (
        media_contract.item_icon_url("act1/2")
        == "/api/resources/items/icon?item_id=act1%2F2"
    )


def test_image_card_fields_and_caption_optionality() -> None:
    card = media_contract.image_card(
        screenshot_id="shot-1", sha256="b" * 64, alt="游戏截图", width=1920, height=1080
    )

    assert card == {
        "type": "image",
        "screenshot_id": "shot-1",
        "sha256": "b" * 64,
        "url": f"/api/images/{'b' * 64}/full",
        "thumb_url": f"/api/images/{'b' * 64}/thumb",
        "alt": "游戏截图",
        "caption": None,
        "width": 1920,
        "height": 1080,
    }
    # 没有内容哈希时两张图都退回截图记录端点（也在前端白名单里）。
    fallback = media_contract.image_card(screenshot_id="shot-2")
    assert fallback["url"] == fallback["thumb_url"] == "/api/screenshots/shot-2"

    assert media_contract.media_result([card]) == {"media": [card]}
    assert media_contract.media_result([card], caption="整批说明") == {
        "media": [card],
        "caption": "整批说明",
    }
    with pytest.raises(ValueError):
        media_contract.image_card(screenshot_id="")


def test_item_icons_and_stage_map_and_chart_card_shapes() -> None:
    icons = media_contract.item_icons_card(
        [{"item_id": "2001", "name": "基础作战记录"}], caption="材料"
    )
    assert icons == {
        "type": "item_icons",
        "caption": "材料",
        "items": [
            {
                "item_id": "2001",
                "name": "基础作战记录",
                "icon_url": "/api/resources/items/icon?item_id=2001",
            }
        ],
    }

    stage = {
        "code": "1-7",
        "name": "暴君",
        "stage_id": "main_01-07",
        "width": 11,
        "height": 7,
        "candidates": 2,
        "tiles": [["#00", ".10"]],
        "start": [[1, 0]],
        "end": [],
        "legend": [{"code": "#", "label": "阻挡"}],
    }
    stage_card = media_contract.stage_map_card(stage)
    assert stage_card["type"] == "stage_map"
    assert stage_card["caption"] is None
    assert stage_card["stage"] == stage

    chart = {"chart_type": "bar", "title": "1-7 掉落", "categories": ["固源岩"]}
    chart_card = media_contract.chart_card(chart, caption="说明")
    assert chart_card == {"type": "chart", "caption": "说明", "chart": chart}
    with pytest.raises(ValueError):
        media_contract.item_icons_card([{"name": "没有 id"}])


# ----------------------------------------------------------------------
# §3.6 上下文占位
# ----------------------------------------------------------------------


def _placeholder(content: str) -> dict[str, Any]:
    return json.loads(content)


def test_compact_media_result_keeps_types_and_titles() -> None:
    body = {
        "result": {
            "media": [
                {
                    "type": "image",
                    "screenshot_id": "s1",
                    "caption": "理智条",
                },
                {
                    "type": "item_icons",
                    "items": [
                        {"item_id": "2001", "name": "基础作战记录"},
                        {"item_id": "30011", "name": "源岩"},
                    ],
                },
                {
                    "type": "stage_map",
                    "stage": {"code": "1-7", "name": "暴君"},
                },
                {"type": "chart", "chart": {"title": "1-7 掉落"}},
                {"type": "unknown", "anything": True},
            ],
            "caption": "整批",
        }
    }

    payload = _placeholder(media_contract.compact_media_result("send_screenshot", body))

    assert payload["result"]["note"] == "媒体卡已发给用户，历史内容不再重复"
    assert payload["result"]["media_sent"] == [
        {"type": "image", "title": "理智条"},
        {"type": "item_icons", "title": "基础作战记录、源岩"},
        {"type": "stage_map", "title": "1-7 暴君"},
        {"type": "chart", "title": "1-7 掉落"},
    ]


def test_compact_media_result_caps_cards_and_handles_other_shapes() -> None:
    cards = [{"type": "chart", "chart": {"title": f"图 {index}"}} for index in range(12)]

    # 落库后的 JSON 文本、处理器的返回值两种输入都接受。
    from_text = _placeholder(
        media_contract.compact_media_result("send_chart", json.dumps({"media": cards}))
    )
    from_body = _placeholder(
        media_contract.compact_media_result("send_chart", {"media": cards})
    )
    assert from_text == from_body
    assert len(from_text["result"]["media_sent"]) == media_contract.MAX_MEDIA_CARDS

    # 工具报错时错误原样保留（模型要据此改一版重发），不能假装「卡片已发出」。
    error = {"error": {"code": "INVALID_PARAMETER", "message": "点数超限"}}
    payload = _placeholder(
        media_contract.compact_media_result("send_chart", {"result": error})
    )
    assert payload["result"]["error"] == error["error"]
    assert "失败" in payload["result"]["note"]

    empty = _placeholder(media_contract.compact_media_result("send_chart", {"media": []}))
    assert empty["result"]["media_sent"] == []
    assert "send_chart" in empty["result"]["note"]

    # 非 JSON 文本不该抛异常。
    assert "媒体" in media_contract.compact_media_result("send_chart", "not json")


# ----------------------------------------------------------------------
# send_screenshot（§3.2）
# ----------------------------------------------------------------------


def test_send_screenshot_by_id_returns_an_image_card(resource_root: Path) -> None:
    async def scenario() -> None:
        async with _engine() as engine:
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            stored = await _store(resource_root)
            async with factory() as session:
                record = await _screenshot_row(session, stored)
            result = await _call(
                factory, "send_screenshot", {"screenshot_id": record.id, "caption": "现在"}
            )
            # 单卡批次不再重复一遍 caption：说明挂在卡片上。
            assert set(result) == {"media"}
            assert result["media"][0]["caption"] == "现在"
            card = result["media"][0]
            assert card["type"] == "image"
            assert card["screenshot_id"] == record.id
            assert card["sha256"] == stored["sha256"]
            assert card["url"] == f"/api/images/{stored['sha256']}/full"
            assert card["thumb_url"] == f"/api/images/{stored['sha256']}/thumb"
            assert (card["width"], card["height"]) == (320, 200)
            assert card["caption"] == "现在"

    asyncio.run(scenario())


def test_send_screenshot_by_pipeline_takes_the_newest_and_filters_trigger(
    resource_root: Path,
) -> None:
    async def scenario() -> None:
        async with _engine() as engine:
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            first = await _store(resource_root, color=(1, 2, 3))
            second = await _store(resource_root, color=(4, 5, 6))
            manual = await _store(resource_root, color=(7, 8, 9))
            async with factory() as session:
                pipeline = Pipeline(
                    id="pipeline-1",
                    source=PipelineSource.AGENT,
                    priority=Priority.AGENT,
                    status=PipelineStatus.COMPLETED,
                    task_count=1,
                )
                session.add(pipeline)
                await session.commit()
                older = await _screenshot_row(
                    session, first, pipeline_id="pipeline-1", created_at=_at(1)
                )
                newer = await _screenshot_row(
                    session, second, pipeline_id="pipeline-1", created_at=_at(2)
                )
                await _screenshot_row(
                    session,
                    manual,
                    pipeline_id="pipeline-1",
                    trigger=ScreenshotTrigger.MANUAL,
                    created_at=_at(3),
                )
            assert older.id != newer.id
            # 不带 trigger：取该流水线最新一张（manual 那张最新）。
            unfiltered = await _call(
                factory, "send_screenshot", {"pipeline_id": "pipeline-1"}
            )
            assert unfiltered["media"][0]["sha256"] == manual["sha256"]
            # trigger 过滤只影响流水线查询。
            filtered = await _call(
                factory,
                "send_screenshot",
                {"pipeline_id": "pipeline-1", "trigger": "agent"},
            )
            assert filtered["media"][0]["screenshot_id"] == newer.id

    asyncio.run(scenario())


def test_send_screenshot_defaults_to_the_latest_session_screenshot(
    resource_root: Path,
) -> None:
    async def scenario() -> None:
        async with _engine() as engine:
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            older = await _store(resource_root, color=(11, 12, 13))
            newer = await _store(resource_root, color=(14, 15, 16))
            async with factory() as session:
                session_id = await _agent_session(session)
                old_row = await _screenshot_row(session, older)
                new_row = await _screenshot_row(session, newer)
                await _tool_message(
                    session,
                    session_id,
                    seq=0,
                    content={"result": {"screenshot_id": old_row.id, "sha256": older["sha256"]}},
                    tool_name="get_screenshot",
                )
                # 最近的是一条媒体卡（image 卡里带 screenshot_id）。
                await _tool_message(
                    session,
                    session_id,
                    seq=1,
                    content={
                        "result": {
                            "media": [
                                {"type": "image", "screenshot_id": new_row.id},
                            ]
                        }
                    },
                )
                # 更近的图表卡没有 screenshot_id，必须被跳过。
                await _tool_message(
                    session,
                    session_id,
                    seq=2,
                    content={"result": {"media": [{"type": "chart", "chart": {}}]}},
                    tool_name="send_chart",
                )
            result = await _call(
                factory, "send_screenshot", {}, session_id=session_id
            )
            assert result["media"][0]["screenshot_id"] == new_row.id

    asyncio.run(scenario())


def test_send_screenshot_requires_an_explicit_id_without_a_session(
    resource_root: Path,
) -> None:
    async def scenario() -> None:
        async with _engine() as engine:
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            with pytest.raises(AppError) as error:
                await _call(factory, "send_screenshot", {})
            assert error.value.code is ErrorCode.INVALID_PARAMETER
            assert "screenshot_id" in error.value.message
            assert "pipeline_id" in error.value.message

    asyncio.run(scenario())


def test_send_screenshot_reports_missing_and_expired_screenshots(
    resource_root: Path,
) -> None:
    async def scenario() -> None:
        async with _engine() as engine:
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            async with factory() as session:
                session_id = await _agent_session(session)
            # 会话里还没有任何截图。
            with pytest.raises(AppError) as empty:
                await _call(factory, "send_screenshot", {}, session_id=session_id)
            assert empty.value.code is ErrorCode.SCREENSHOT_NOT_FOUND
            assert "get_screenshot" in empty.value.message

            with pytest.raises(AppError) as unknown:
                await _call(factory, "send_screenshot", {"screenshot_id": "nope"})
            assert unknown.value.code is ErrorCode.SCREENSHOT_NOT_FOUND

            with pytest.raises(AppError) as no_pipeline_shot:
                await _call(factory, "send_screenshot", {"pipeline_id": "missing-pipeline"})
            assert no_pipeline_shot.value.code is ErrorCode.SCREENSHOT_NOT_FOUND

            # 记录还在但已被清理（deleted_at 非空）。
            stored = await _store(resource_root)
            async with factory() as session:
                deleted = await _screenshot_row(
                    session, stored, deleted_at=_at(5)
                )
            with pytest.raises(AppError) as expired:
                await _call(factory, "send_screenshot", {"screenshot_id": deleted.id})
            assert expired.value.code is ErrorCode.SCREENSHOT_EXPIRED

            # 记录没打标记但文件已经不在磁盘上。
            stray = await _store(resource_root, color=(21, 22, 23))
            async with factory() as session:
                stray_row = await _screenshot_row(session, stray)
            _screenshot_path(resource_root, stray).unlink()
            with pytest.raises(AppError) as missing_file:
                await _call(factory, "send_screenshot", {"screenshot_id": stray_row.id})
            assert missing_file.value.code is ErrorCode.SCREENSHOT_EXPIRED

    asyncio.run(scenario())


def test_send_screenshot_parameter_limits() -> None:
    with pytest.raises(ValidationError):
        _params("send_screenshot", {"trigger": "scheduled"})
    with pytest.raises(ValidationError):
        _params("send_screenshot", {"caption": "x" * 301})
    with pytest.raises(ValidationError):
        _params("send_screenshot", {"screenshot_id": "a" * 37})
    with pytest.raises(ValidationError):
        _params("send_screenshot", {"unknown": 1})


# ----------------------------------------------------------------------
# annotate_screenshot（§3.3）
# ----------------------------------------------------------------------


def test_annotate_screenshot_stores_a_new_row_and_card(resource_root: Path) -> None:
    async def scenario() -> None:
        async with _engine() as engine:
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            source = await _store(resource_root, size=(400, 400), color=(200, 200, 200))
            async with factory() as session:
                record = await _screenshot_row(
                    session, source, backend=ScreenshotBackend.CORE.value
                )
            result = await _call(
                factory,
                "annotate_screenshot",
                {
                    "screenshot_id": record.id,
                    "crop": {"x": 0.5, "y": 0.5, "width": 0.5, "height": 0.5},
                    "zoom": 2,
                    "boxes": [
                        {
                            "x": 0.5,
                            "y": 0.5,
                            "width": 0.25,
                            "height": 0.25,
                            "label": "理智",
                        }
                    ],
                    "caption": "理智条位置",
                },
            )
            card = result["media"][0]
            assert card["type"] == "image"
            assert card["screenshot_id"] != record.id
            assert card["caption"].startswith("理智条位置（实际倍数 ×2")
            assert (card["width"], card["height"]) == (400, 400)
            assert card["url"] == f"/api/images/{card['sha256']}/full"

            # 新行必须已提交（换一个会话查得到），后端沿用源记录的后端、trigger=agent。
            async with factory() as session:
                stored_row = await ScreenshotRepository(session).get(card["screenshot_id"])
            assert stored_row is not None
            assert stored_row.trigger == ScreenshotTrigger.AGENT.value
            assert stored_row.backend == ScreenshotBackend.CORE.value
            assert stored_row.format == "jpg"
            assert stored_row.path == (
                f"image/screenshot/{card['sha256'][:2]}/{card['sha256']}.jpg"
            )
            assert (stored_row.width, stored_row.height) == (400, 400)
            assert stored_row.size_bytes > 0
            stored_file = resource_root / stored_row.path
            assert stored_file.is_file()
            with Image.open(stored_file) as opened:
                assert opened.size == (400, 400)

    asyncio.run(scenario())


def test_annotate_screenshot_reports_the_capped_zoom(resource_root: Path) -> None:
    async def scenario() -> None:
        async with _engine() as engine:
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            source = await _store(resource_root, size=(1600, 1200))
            async with factory() as session:
                record = await _screenshot_row(session, source)
            result = await _call(
                factory, "annotate_screenshot", {"screenshot_id": record.id, "zoom": 8}
            )
            card = result["media"][0]
            # 1600×1200 放大 8 倍会到 12800，被 2048 上限压到 1.28 倍。
            assert (card["width"], card["height"]) == (2048, 1536)
            assert "×1.28" in card["caption"]

    asyncio.run(scenario())


def test_annotate_screenshot_defaults_to_the_session_screenshot_and_requires_id_for_rest(
    resource_root: Path,
) -> None:
    async def scenario() -> None:
        async with _engine() as engine:
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            source = await _store(resource_root, size=(120, 90))
            async with factory() as session:
                session_id = await _agent_session(session)
                record = await _screenshot_row(session, source)
                await _tool_message(
                    session,
                    session_id,
                    seq=0,
                    content={"result": {"screenshot_id": record.id}},
                    tool_name="get_screenshot",
                )
            result = await _call(
                factory, "annotate_screenshot", {"zoom": 2}, session_id=session_id
            )
            assert (result["media"][0]["width"], result["media"][0]["height"]) == (
                240,
                180,
            )

            with pytest.raises(AppError) as error:
                await _call(factory, "annotate_screenshot", {"zoom": 2})
            assert error.value.code is ErrorCode.INVALID_PARAMETER
            assert "screenshot_id" in error.value.message
            assert "pipeline_id" in error.value.message

    asyncio.run(scenario())


def test_annotate_screenshot_accepts_pipeline_id_and_trigger(resource_root: Path) -> None:
    """与 send_screenshot 对称：pipeline_id(+trigger) → 本会话最近一张（规格 §3.2）。"""

    async def scenario() -> None:
        async with _engine() as engine:
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            agent_shot = await _store(resource_root, size=(100, 80), color=(9, 8, 7))
            manual_shot = await _store(resource_root, size=(200, 160), color=(6, 5, 4))
            async with factory() as session:
                session.add(
                    Pipeline(
                        id="pipeline-2",
                        source=PipelineSource.AGENT,
                        priority=Priority.AGENT,
                        status=PipelineStatus.COMPLETED,
                        task_count=1,
                    )
                )
                await session.commit()
                await _screenshot_row(
                    session,
                    agent_shot,
                    pipeline_id="pipeline-2",
                    created_at=_at(1),
                )
                manual_row = await _screenshot_row(
                    session,
                    manual_shot,
                    pipeline_id="pipeline-2",
                    trigger=ScreenshotTrigger.MANUAL,
                    created_at=_at(2),
                )

            # trigger 过滤用 agent 那张（100×80）：新图尺寸跟着源图走，id 必然是新的。
            filtered = await _call(
                factory,
                "annotate_screenshot",
                {"pipeline_id": "pipeline-2", "trigger": "agent"},
            )
            filtered_card = filtered["media"][0]
            assert (filtered_card["width"], filtered_card["height"]) == (100, 80)
            assert filtered_card["screenshot_id"] != manual_row.id

            # 不带 trigger 时取该流水线最新一张（manual 的 200×160）。
            newest = await _call(
                factory, "annotate_screenshot", {"pipeline_id": "pipeline-2"}
            )
            newest_card = newest["media"][0]
            assert (newest_card["width"], newest_card["height"]) == (200, 160)
            assert newest_card["screenshot_id"] != manual_row.id

            # 新行照规格 §3.3 只带 trigger=agent（源是 manual 也一样）。
            async with factory() as session:
                stored_row = await ScreenshotRepository(session).get(
                    newest_card["screenshot_id"]
                )
            assert stored_row is not None
            assert stored_row.trigger == ScreenshotTrigger.AGENT.value
            assert stored_row.path.endswith(f"{newest_card['sha256']}.jpg")

    asyncio.run(scenario())


def test_annotate_screenshot_parameter_bounds() -> None:
    with pytest.raises(ValidationError):
        _params("annotate_screenshot", {"trigger": "scheduled"})
    with pytest.raises(ValidationError):
        _params("annotate_screenshot", {"pipeline_id": "a" * 37})
    with pytest.raises(ValidationError):
        _params("annotate_screenshot", {"zoom": 0.5})
    with pytest.raises(ValidationError):
        _params("annotate_screenshot", {"zoom": 9})
    with pytest.raises(ValidationError):
        _params(
            "annotate_screenshot",
            {"crop": {"x": 0, "y": 0, "width": 0.001, "height": 0.5}},
        )
    with pytest.raises(ValidationError):
        _params(
            "annotate_screenshot",
            {"crop": {"x": 1.5, "y": 0, "width": 0.5, "height": 0.5}},
        )
    with pytest.raises(ValidationError):
        _params(
            "annotate_screenshot",
            {
                "boxes": [
                    {"x": 0, "y": 0, "width": 0.1, "height": 0.1, "label": "x" * 33}
                ]
            },
        )
    with pytest.raises(ValidationError):
        _params(
            "annotate_screenshot",
            {
                "boxes": [
                    {"x": 0, "y": 0, "width": 0.1, "height": 0.1}
                    for _ in range(17)
                ]
            },
        )


# ----------------------------------------------------------------------
# send_item_icons（§3.2 + 资源索引）
# ----------------------------------------------------------------------


def test_send_item_icons_resolves_ids_and_names(local_items: None) -> None:
    async def scenario() -> None:
        async with _engine() as engine:
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            result = await _call(
                factory,
                "send_item_icons",
                {"items": ["2001", " 源岩 "], "caption": "材料"},
            )
            card = result["media"][0]
            assert card["type"] == "item_icons"
            assert card["caption"] == "材料"
            assert card["items"] == [
                {
                    "item_id": "2001",
                    "name": "基础作战记录",
                    "icon_url": "/api/resources/items/icon?item_id=2001",
                },
                {
                    "item_id": "30011",
                    "name": "源岩",
                    "icon_url": "/api/resources/items/icon?item_id=30011",
                },
            ]

            # 中文名与物品 ID 解析到同一条索引记录（按输入顺序输出）。
            by_name = await _call(
                factory, "send_item_icons", {"items": ["基础作战记录", "RMA70-12"]}
            )
            assert [item["item_id"] for item in by_name["media"][0]["items"]] == [
                "2001",
                "30103",
            ]

    asyncio.run(scenario())


def test_find_by_name_prefers_exact_then_casefold_and_breaks_ties_by_item_id(
    local_items: None,
) -> None:
    # 中文名精确匹配。
    assert resource_index.find_by_name("基础作战记录") == "2001"
    # 大小写不敏感的精确匹配。
    assert resource_index.find_by_name("rma70-12") == "30103"
    # 同名 26 条时取 item_id 字典序第一个。
    assert resource_index.find_by_name("寻访数据契约") == "LMTGS_COIN_1401"
    assert resource_index.find_by_name("  ") is None
    assert resource_index.find_by_name("不存在的物品") is None


def test_load_item_index_and_resolve_icon_path(local_items: None) -> None:
    index = resource_index.load_item_index()

    # 实施时本地索引 474 条；只断言量级，资源更新后不必改测试。
    assert 400 < len(index) < 1000
    assert index["2001"] == {"name": "基础作战记录", "icon_filename": "2001.png"}
    icon = resource_index.resolve_icon_path("2001")
    assert icon.name == "2001.png"
    assert icon.parent.name == "items"
    with pytest.raises(AppError) as error:
        resource_index.resolve_icon_path("不存在的物品")
    assert error.value.code is ErrorCode.RESOURCE_ASSET_NOT_FOUND


def test_resource_index_rejects_unsafe_icon_records(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "resource"
    (root / "template" / "items").mkdir(parents=True)
    (root / "template" / "items" / "good.png").write_bytes(b"png")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evil.png").write_bytes(b"png")
    (root / "item_index.json").write_text(
        json.dumps(
            {
                "good": {"name": "好图标", "icon": "good.png"},
                "traversal": {"name": "越界", "icon": "../outside/evil.png"},
                "directory": {"name": "目录", "icon": ".."},
                "notpng": {"name": "非 PNG", "icon": "good.jpg"},
                "noicon": {"name": "无图标"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(resource_index, "resource_root", lambda: root)

    assert resource_index.resolve_icon_path("good").name == "good.png"
    for item_id in ("traversal", "directory", "notpng", "noicon"):
        with pytest.raises(AppError) as error:
            resource_index.resolve_icon_path(item_id)
        assert error.value.code is ErrorCode.RESOURCE_ASSET_NOT_FOUND


@pytest.mark.parametrize(
    "payload",
    ["{ not json", "[]", '{"2001": {"name": ""}}', '{"2001": "not-a-record"}'],
)
def test_load_item_index_reports_corrupt_indexes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: str
) -> None:
    root = tmp_path / "resource"
    root.mkdir()
    (root / "item_index.json").write_text(payload, encoding="utf-8")
    monkeypatch.setattr(resource_index, "resource_root", lambda: root)

    with pytest.raises(AppError) as error:
        resource_index.load_item_index()
    assert error.value.code is ErrorCode.RESOURCE_LOAD_FAILED
    assert error.value.details == {"resource": "item_index.json"}


def test_iter_resource_roots_follows_the_core_load_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(resource_index, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        resource_index, "resource_root", lambda: tmp_path / "core" / "resource"
    )

    roots = resource_index.iter_resource_roots()

    # 基础层在前、增量层在后（后写的覆盖先写的）；maa-layers/repo 不是运行时层。
    assert [root.relative_to(tmp_path).as_posix() for root in roots] == [
        "core/resource",
        "resource/maa-layers/cache/resource",
        "resource/maa-layers/custom/resource",
    ]


def test_send_item_icons_reports_every_missing_name(local_items: None) -> None:
    async def scenario() -> None:
        async with _engine() as engine:
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            with pytest.raises(AppError) as error:
                await _call(
                    factory,
                    "send_item_icons",
                    {"items": ["2001", "不存在的物品", "另一个也没有"]},
                )
            assert error.value.code is ErrorCode.RESOURCE_ASSET_NOT_FOUND
            assert "不存在的物品" in error.value.message
            assert "另一个也没有" in error.value.message

    asyncio.run(scenario())


def test_send_item_icons_parameter_limits() -> None:
    with pytest.raises(ValidationError):
        _params("send_item_icons", {"items": []})
    with pytest.raises(ValidationError):
        _params("send_item_icons", {"items": [f"item-{index}" for index in range(9)]})
    with pytest.raises(ValidationError):
        _params("send_item_icons", {"items": [""]})
    with pytest.raises(ValidationError):
        _params("send_item_icons", {"items": ["x" * 65]})


# ----------------------------------------------------------------------
# send_stage_map（§3.4）
# ----------------------------------------------------------------------


@pytest.fixture
def local_tiles(monkeypatch: pytest.MonkeyPatch) -> None:
    if not (LOCAL_RESOURCE / "Arknights-Tile-Pos" / "overview.json").is_file():
        pytest.skip("仓库未自带 Arknights-Tile-Pos 资源")
    monkeypatch.setattr(
        resource_index, "resource_root", lambda: LOCAL_RESOURCE
    )


def test_send_stage_map_returns_the_local_layout(local_tiles: None) -> None:
    async def scenario() -> None:
        async with _engine() as engine:
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            result = await _call(
                factory, "send_stage_map", {"stage": "1-7", "caption": "布局"}
            )
            card = result["media"][0]
            assert card["type"] == "stage_map"
            assert card["caption"] == "布局"
            stage = card["stage"]
            assert (stage["code"], stage["name"], stage["stage_id"]) == (
                "1-7",
                "暴君",
                "main_01-07",
            )
            assert (stage["width"], stage["height"]) == (11, 7)
            assert stage["candidates"] == 2
            assert stage["start"] == [[10, 1], [10, 3], [10, 5]]
            assert stage["end"] == [[0, 3]]
            assert len(stage["tiles"]) == 7
            assert all(len(row) == 11 for row in stage["tiles"])

    asyncio.run(scenario())


def test_send_stage_map_reports_unknown_stages(local_tiles: None) -> None:
    async def scenario() -> None:
        async with _engine() as engine:
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            with pytest.raises(AppError) as error:
                await _call(factory, "send_stage_map", {"stage": "绝对不是关卡-999"})
            assert error.value.code is ErrorCode.RESOURCE_ASSET_NOT_FOUND
            assert "绝对不是关卡-999" in error.value.message

    asyncio.run(scenario())


def test_send_stage_map_falls_back_to_the_alias_resolver(local_tiles: None) -> None:
    class Resolver:
        def __init__(self) -> None:
            self.queries: list[str] = []

        async def resolve(self, key: str) -> dict[str, Any]:
            self.queries.append(key)
            return {"query": key, "source": "core", "resolved": {"stage_id": "main_01-07"}}

    async def scenario() -> None:
        async with _engine() as engine:
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            resolver = Resolver()
            state = SimpleNamespace(stage_resolver=resolver)
            result = await _call(
                factory, "send_stage_map", {"stage": "1-7 别名"}, state=state
            )
            assert resolver.queries == ["1-7 别名"]
            assert result["media"][0]["stage"]["stage_id"] == "main_01-07"

    asyncio.run(scenario())


def test_send_stage_map_propagates_index_load_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(resource_index, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        resource_index, "resource_root", lambda: tmp_path / "missing" / "resource"
    )

    async def scenario() -> None:
        async with _engine() as engine:
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            with pytest.raises(AppError) as error:
                await _call(factory, "send_stage_map", {"stage": "1-7"})
            assert error.value.code is ErrorCode.RESOURCE_LOAD_FAILED

    asyncio.run(scenario())


def test_send_stage_map_parameter_limits() -> None:
    with pytest.raises(ValidationError):
        _params("send_stage_map", {"stage": ""})
    with pytest.raises(ValidationError):
        _params("send_stage_map", {"stage": "x" * 65})
    with pytest.raises(ValidationError):
        _params("send_stage_map", {"stage": "1-7", "caption": "x" * 301})


# ----------------------------------------------------------------------
# send_chart（§3.5）
# ----------------------------------------------------------------------


def _bar_arguments(**overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "chart_type": "bar",
        "title": "1-7 掉落",
        "categories": ["固源岩", "源岩"],
        "series": [{"name": "数量", "values": [12, 3]}],
        "x_label": "物品",
        "y_label": "个数",
        "unit": "个",
        "caption": "最近一次统计",
        "source": "get_drop_stats(stage=1-7)",
    }
    arguments.update(overrides)
    return arguments


def test_send_chart_forwards_the_validated_spec() -> None:
    async def scenario() -> None:
        async with _engine() as engine:
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            result = await _call(factory, "send_chart", _bar_arguments(stacked=True))
            card = result["media"][0]
            assert card == {
                "type": "chart",
                "caption": "最近一次统计",
                "chart": {
                    "chart_type": "bar",
                    "title": "1-7 掉落",
                    "categories": ["固源岩", "源岩"],
                    "series": [{"name": "数量", "values": [12, 3]}],
                    "stacked": True,
                    "x_label": "物品",
                    "y_label": "个数",
                    "unit": "个",
                    "source": "get_drop_stats(stage=1-7)",
                },
            }

            # null 是合法断点，必须原样保留（前端不断成 0）。
            line = await _call(
                factory,
                "send_chart",
                _bar_arguments(chart_type="line", series=[{"name": "理智", "values": [1, None]}]),
            )
            assert line["media"][0]["chart"]["series"][0]["values"] == [1, None]

            # pie 只用第一条系列是前端规则，后端原样转发。
            pie = await _call(
                factory,
                "send_chart",
                _bar_arguments(
                    chart_type="pie",
                    series=[
                        {"name": "一", "values": [1, 2]},
                        {"name": "二", "values": [3, 4]},
                    ],
                ),
            )
            assert [series["name"] for series in pie["media"][0]["chart"]["series"]] == [
                "一",
                "二",
            ]

    asyncio.run(scenario())


def test_send_chart_rejects_non_finite_values() -> None:
    async def scenario() -> None:
        async with _engine() as engine:
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            for value in (float("nan"), float("inf"), float("-inf")):
                with pytest.raises(AppError) as error:
                    await _call(
                        factory,
                        "send_chart",
                        _bar_arguments(series=[{"name": "数量", "values": [value, 1]}]),
                    )
                assert error.value.code is ErrorCode.INVALID_PARAMETER
                assert "非有限" in error.value.message

    asyncio.run(scenario())


def test_send_chart_rejects_length_mismatch() -> None:
    async def scenario() -> None:
        async with _engine() as engine:
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            with pytest.raises(AppError) as error:
                await _call(
                    factory,
                    "send_chart",
                    _bar_arguments(series=[{"name": "数量", "values": [1]}]),
                )
            assert error.value.code is ErrorCode.INVALID_PARAMETER
            assert "1 个点" in error.value.message and "2 项" in error.value.message

    asyncio.run(scenario())


def test_send_chart_rejects_too_many_points_and_oversized_payloads() -> None:
    oversized_categories = ["测" * 40] * 200
    huge_values = [1.7976931348623157e308] * 100

    async def scenario() -> None:
        async with _engine() as engine:
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            with pytest.raises(AppError) as points:
                await _call(
                    factory,
                    "send_chart",
                    _bar_arguments(
                        categories=["岩"] * 200,
                        series=[
                            {"name": f"系列{index}", "values": [1] * 200}
                            for index in range(6)
                        ],
                    ),
                )
            assert points.value.code is ErrorCode.INVALID_PARAMETER
            assert points.value.details == {
                "points": 1200,
                "limit": media_tools.MAX_CHART_POINTS,
            }

            with pytest.raises(AppError) as size:
                await _call(
                    factory,
                    "send_chart",
                    _bar_arguments(
                        chart_type="pie",
                        categories=oversized_categories,
                        series=[
                            {"name": f"系列{index}", "values": huge_values}
                            for index in range(6)
                        ],
                    ),
                )
            assert size.value.code is ErrorCode.INVALID_PARAMETER
            assert size.value.details["bytes"] > media_tools.MAX_CHART_BYTES
            assert "字节" in size.value.message

    asyncio.run(scenario())


def test_send_chart_schema_limits_are_expressed_in_the_parameter_model() -> None:
    with pytest.raises(ValidationError):
        _params("send_chart", _bar_arguments(categories=["岩"] * 201))
    with pytest.raises(ValidationError):
        _params(
            "send_chart",
            _bar_arguments(
                series=[{"name": f"系列{index}", "values": [1, 2]} for index in range(7)]
            ),
        )
    with pytest.raises(ValidationError):
        _params(
            "send_chart",
            _bar_arguments(categories=["岩"] * 201, series=[{"name": "数量", "values": [1] * 201}]),
        )
    with pytest.raises(ValidationError):
        _params("send_chart", _bar_arguments(title="x" * 81))
    with pytest.raises(ValidationError):
        _params("send_chart", _bar_arguments(categories=["岩" * 41, "岩"]))
    with pytest.raises(ValidationError):
        _params("send_chart", _bar_arguments(source="x" * 201))
    with pytest.raises(ValidationError):
        _params("send_chart", _bar_arguments(chart_type="scatter"))
    with pytest.raises(ValidationError):
        _params("send_chart", _bar_arguments(unit="x" * 17))
    with pytest.raises(ValidationError):
        _params("send_chart", _bar_arguments(extra=True))


def test_schema_violations_surface_as_tool_args_invalid_through_the_registry() -> None:
    """字段级超限走既有校验链路（``TOOL_ARGS_INVALID``）；语义级越界才是 ``INVALID_PARAMETER``。"""
    registry = build_registry()

    with pytest.raises(AppError) as error:
        registry.validate("send_chart", _bar_arguments(categories=["岩"] * 201))
    assert error.value.code is ErrorCode.TOOL_ARGS_INVALID


# ----------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------


def _at(minute: int) -> Any:
    from datetime import datetime, timedelta

    return datetime(2026, 9, 29, 0, 0, 0) + timedelta(minutes=minute)


def _screenshot_path(root: Path, stored: dict[str, Any]) -> Path:
    return (
        root
        / "image"
        / "screenshot"
        / stored["sha256"][:2]
        / f"{stored['sha256']}.jpg"
    )


def test_media_tools_are_callable_over_the_rest_catalog(local_items: None) -> None:
    """规格 §3.2：REST 与内置会话走同一条注册表路径，返回值不依赖 runtime 钩子。"""

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from maa_api.agent.confirmation import ConfirmationService
    from maa_api.agent.policy import PolicyEngine
    from maa_api.api.deps import get_session
    from maa_api.api.errors import register_exception_handlers
    from maa_api.api.routers import agent
    from maa_api.settings import LLMSettings, Settings, set_settings

    async def scenario() -> None:
        async with _engine() as engine:
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            service = ConfirmationService(factory, build_registry(), policy=PolicyEngine())
            app = FastAPI()
            register_exception_handlers(app)
            app.include_router(agent.router)
            app.state.confirmation_service = service

            async def get_test_session():
                async with factory() as session:
                    yield session

            app.dependency_overrides[get_session] = get_test_session
            set_settings(
                Settings(
                    llm=LLMSettings(
                        base_url="https://llm.test/v1", api_key="k", model="m"
                    )
                )
            )
            client = TestClient(app, raise_server_exceptions=False)
            client.__enter__()
            try:
                listed = client.get("/api/agent/tools")
                assert listed.status_code == 200
                names = {item["name"] for item in listed.json()["items"]}
                assert media_contract.MEDIA_TOOL_NAMES <= names

                invoked = client.post(
                    "/api/agent/tools/send_item_icons/invoke",
                    json={"arguments": {"items": ["源岩"]}},
                )
                assert invoked.status_code == 200, invoked.text
                payload = invoked.json()
                assert payload["status"] == "success"
                assert payload["audit_id"] is not None
                assert payload["result"] == {
                    "media": [
                        {
                            "type": "item_icons",
                            "caption": None,
                            "items": [
                                {
                                    "item_id": "30011",
                                    "name": "源岩",
                                    "icon_url": "/api/resources/items/icon?item_id=30011",
                                }
                            ],
                        }
                    ]
                }
            finally:
                client.__exit__(None, None, None)

    asyncio.run(scenario())
