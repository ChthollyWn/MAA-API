"""场景测试：用户问「我有多少合成玉」（M18 规格 §6、§4.6 的 `account-resources` 剧本）。

链路走的是生产件：`build_registry()` 的真实工具表、真实 `PolicyEngine` /
`ConfirmationService` / `AgentRuntime`、真实 `PipelineRunner` + `QueueService` +
`KnowledgeService`（内置条目从 `maa_api/agent/knowledge/builtin/` 加载）。只有三个
进程边界换成假件：模型（`FakeLLM`）、MaaCore（`FakeAsst`）、设备（返回 16:9 图片的
`_FakeDeviceManager`）。不联网、不加载内核、不碰真机。

剧本断言（缺一不可）：
- 五个工具按 §4.6 的顺序执行，全程没有 `confirmation_required`；
- `wait_pipeline` 返回 `finished: true`，流水线是 `agent` 来源并关联本会话；
- 最后一次模型请求带 `image_url`；
- 每次模型请求的 system 消息里都有 `account-resources` 的索引行与 `now`。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表到 SQLModel.metadata
from maa_api import settings as settings_module
from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.knowledge import KnowledgeService
from maa_api.agent.policy import PolicyEngine
from maa_api.agent.runtime import AgentRuntime
from maa_api.agent.tools import build_registry
from maa_api.core.enums import Message
from maa_api.core.registry import DEFAULT_CORE_ID, CoreRegistry
from maa_api.db import session as db_session_module
from maa_api.db.models import AgentSession, Pipeline, Screenshot
from maa_api.db.repositories.agent import AgentSessionRepository
from maa_api.db.repositories.log import ScreenshotRepository
from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.domain.enums import PipelineSource, ScreenshotTrigger
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.device_service import ScreenshotResult
from maa_api.services.pipeline_runner import PipelineRunner
from maa_api.services.queue_service import QueueService
from maa_api.settings import LLMSettings, Settings
from tests.fakes.fake_asst import SUCCESS_SCRIPT, FakeAsst
from tests.fakes.fake_llm import (
    FakeLLM,
    FakeResponse,
    content_text,
    text_response,
    tool_call,
    tool_response,
    turn_events,
)

MODEL = "test-model"
BASE_URL = "https://llm.test/v1"
SESSION_ID = "session-account-resources"
QUESTION = "我有多少合成玉？"
#: 假设备返回 16:9 画面；截图归档的宽高按这个尺寸核对。
IMAGE_SIZE = (1280, 720)
ANSWER = "合成玉 12,000、龙门币 3,456,789、至纯源石 12；依据是本轮首页截图。"
#: 回归时宁可快速失败也不要挂满生产默认的 600 秒确认超时。
CONFIRMATION_TIMEOUT_SECONDS = 5


class _ReadySupervisor:
    """流水线预检只看内核就绪状态；本场景不加载真实内核。"""

    state = "ready"
    generation = 1


class _FakeCoreClient:
    """把 FakeAsst 的回调桥接成 CoreClient 的异步接口。"""

    def __init__(self, asst: FakeAsst) -> None:
        self.asst = asst
        self._handlers: dict[str, Any] = {}
        self._loop = asyncio.get_running_loop()
        asst.set_callback(self._on_callback)

    def on(self, event_type: str, handler: Any) -> None:
        self._handlers[event_type] = handler

    def _on_callback(self, message: int, details: bytes, _arg: Any) -> None:
        # FakeAsst 在后台线程回调，必须切回事件循环线程再交给 PipelineRunner。
        handler = self._handlers.get("CALLBACK")
        if handler is not None:
            payload = {"msg": message, "details": json.loads(details)}
            self._loop.call_soon_threadsafe(handler, payload)

    async def append_task(self, type_name: str, params: dict[str, Any]) -> int:
        task_id = int(self.asst.append_task(type_name, params))
        if task_id <= 0:
            raise AppError(
                ErrorCode.CORE_COMMAND_FAILED, f"MaaCore 拒绝任务 {type_name!r}"
            )
        return task_id

    async def start(self) -> bool:
        return self.asst.start()

    async def stop(self) -> bool:
        return self.asst.stop()


class _FakeDeviceManager:
    """设备边界：预检、回首页、截图。只有场景用到的三个方法。"""

    def __init__(self, image: Image.Image) -> None:
        self._image = image
        self.screenshots = 0
        self.back_to_home_calls = 0

    async def ensure_available(self, *, timeout: float = 10.0) -> bool:
        del timeout
        return True

    def snapshot(self) -> dict[str, Any]:
        return {
            "core_id": DEFAULT_CORE_ID,
            "state": "connected",
            "address": "fake:5555",
        }

    async def screenshot(self, backend: str = "adb") -> ScreenshotResult:
        self.screenshots += 1
        captured = "core" if backend == "core" else "adb"
        return ScreenshotResult(self._image.copy(), captured)

    async def back_to_home(self) -> bool:
        self.back_to_home_calls += 1
        return True


@dataclass(slots=True)
class _Flow:
    """一次完整场景跑完之后的现场。"""

    chunks: list[str]
    llm: FakeLLM
    device: _FakeDeviceManager
    asst: FakeAsst
    session_id: str
    pipeline: Pipeline | None
    screenshot: Screenshot | None
    #: 该流水线的留档截图（M23 §3.9：任务终态各留一张，本条场景只有一个 StartUp）。
    task_snapshots: list[Screenshot]


def _tool_result_value(
    messages: list[dict[str, Any]], tool_call_id: str, key: str
) -> Any:
    """从对话历史里读某个工具结果字段：真实模型也是这么拿到 pipeline_id 的。"""
    for message in reversed(messages):
        if message.get("role") != "tool":
            continue
        if message.get("tool_call_id") != tool_call_id:
            continue
        payload = json.loads(message["content"])
        return payload["result"][key]
    raise AssertionError(f"消息里没有 {tool_call_id} 的工具结果")


def _wait_pipeline_response(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pipeline_id = _tool_result_value(messages, "call-submit", "pipeline_id")
    return tool_response(
        tool_call(
            "call-wait",
            "wait_pipeline",
            {"pipeline_id": pipeline_id, "timeout_seconds": 60},
        )
    )


def _flow_responses() -> list[FakeResponse]:
    """§4.6 剧本：读剧本 → 提交 StartUp → 等它结束 → 回首页 → 截图 → 文字回答。"""
    return [
        tool_response(
            tool_call(
                "call-knowledge", "read_knowledge", {"entry_id": "account-resources"}
            )
        ),
        tool_response(
            tool_call(
                "call-submit",
                "submit_pipeline",
                {"tasks": [{"name": "StartUp", "start_game_enabled": True}]},
            )
        ),
        _wait_pipeline_response,
        tool_response(tool_call("call-home", "back_to_home")),
        tool_response(tool_call("call-screenshot", "get_screenshot")),
        text_response(ANSWER),
    ]


async def _run_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Flow:
    """把一个临时库、一条生产链路和一个 FakeAsst 接起来，跑完整轮对话。"""
    # 截图档案与配置都落在 tmp_path 下：绝不碰仓库的 resource/ 与 config.yaml。
    monkeypatch.setattr(
        db_session_module, "DB_PATH", tmp_path / "resource" / "maa_api.db"
    )
    for env_name in settings_module.ENV_OVERRIDES.values():
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setattr(settings_module, "DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    settings = Settings(
        llm=LLMSettings(base_url=BASE_URL, api_key="flow-secret", model=MODEL)
    )
    # 连进程内缓存一起钉住：否则 get_settings()（截图质量、渠道默认值）会去读开发者
    # 本机的 config.yaml，用例结果就随环境变了。
    monkeypatch.setattr(settings_module, "_CACHE", settings)

    engine = db_session_module.make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'account-resources.db'}", poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    asst = FakeAsst(script=SUCCESS_SCRIPT, delay_scale=0)
    confirmations: ConfirmationService | None = None
    runner: PipelineRunner | None = None
    try:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

        registry = build_registry()
        confirmations = ConfirmationService(
            factory,
            registry,
            policy=PolicyEngine(),
            confirmation_timeout_seconds=CONFIRMATION_TIMEOUT_SECONDS,
        )
        # 与生产 lifespan 同一顺序：先 reset 再开过期扫描。带确认的回归会在
        # CONFIRMATION_TIMEOUT_SECONDS 后收流并让断言失败，而不是把用例挂住。
        await confirmations.start(scan_interval_seconds=0.05)
        knowledge = KnowledgeService(factory)

        device = _FakeDeviceManager(Image.new("RGB", IMAGE_SIZE, (32, 64, 128)))
        queue_service = QueueService(factory)
        supervisor = _ReadySupervisor()
        core_registry = CoreRegistry()
        core_registry.register(DEFAULT_CORE_ID, _FakeCoreClient(asst))
        runner = PipelineRunner(
            core_registry,
            supervisor,
            factory,
            queue_service=queue_service,
            device_manager=device,
        )
        queue_service.bind_runner(runner.wake, runner.operation_lock)
        await runner.start()

        state = SimpleNamespace(
            core_supervisor=supervisor,
            device_manager=device,
            pipeline_runner=runner,
            queue_service=queue_service,
            knowledge_service=knowledge,
        )
        request = SimpleNamespace(app=SimpleNamespace(state=state))

        llm = FakeLLM(_flow_responses())
        # 与生产一致：运行时只拿 session_factory 与确认服务，知识库走
        # request.app.state.knowledge_service（Task 5 的接法）。
        runtime = AgentRuntime(
            factory,
            confirmations,
            settings_provider=lambda _request: settings,
            client_factory=lambda _settings: llm,
        )
        # 能力探测（一次真实上游往返）与视觉能力在本用例里直接给定：探测本身已有
        # 专门用例，场景测试关心的是拿到视觉能力之后的链路。
        runtime._capability_cache[(MODEL, BASE_URL)] = {
            "model": MODEL,
            "tool_calling": True,
            "vision": True,
            "ready": True,
            "status": "ready",
            "degraded_reason": None,
        }
        async with factory() as session:
            await AgentSessionRepository(session).create(
                AgentSession(id=SESSION_ID, model=MODEL, base_url=BASE_URL)
            )
            await session.commit()

        assert await runtime.acquire_session(SESSION_ID) is True
        chunks = [
            chunk
            async for chunk in runtime.stream_message(SESSION_ID, QUESTION, request)
        ]

        events = turn_events(chunks)
        # 这里只做「能取到什么就取什么」的现场收集，判定全部留给下面的断言：
        # 回归（例如多出一次确认）应该在断言处给出明确原因，而不是在这里迷路。
        pipeline = await _load_pipeline(factory, events)
        screenshot = await _load_screenshot(factory, events)
        snapshots = (
            await _load_task_snapshots(factory, pipeline.id) if pipeline else []
        )
        return _Flow(
            chunks=chunks,
            llm=llm,
            device=device,
            asst=asst,
            session_id=SESSION_ID,
            pipeline=pipeline,
            screenshot=screenshot,
            task_snapshots=snapshots,
        )
    finally:
        if runner is not None:
            await runner.stop()
        if confirmations is not None:
            await confirmations.close()
        asst.stop()
        await engine.dispose()


def _tool_result(
    events: list[tuple[str, dict[str, Any]]], tool_name: str
) -> dict[str, Any]:
    """取某个工具的结果；带 error 的结果直接判失败（场景里不该出现）。"""
    for event, data in events:
        if event == "tool_result" and data.get("name") == tool_name:
            assert "error" not in data, data
            return data["result"]
    raise AssertionError(f"事件流里没有 {tool_name} 的 tool_result")


def _maybe_result(
    events: list[tuple[str, dict[str, Any]]], tool_name: str
) -> dict[str, Any] | None:
    """容错版取结果：缺失或报错时返回 None，交给断言去解释。"""
    for event, data in events:
        if event == "tool_result" and data.get("name") == tool_name:
            return None if "error" in data else data["result"]
    return None


async def _load_pipeline(
    factory: async_sessionmaker[AsyncSession], events: list[tuple[str, dict[str, Any]]]
) -> Pipeline | None:
    submit = _maybe_result(events, "submit_pipeline") or {}
    pipeline_id = submit.get("pipeline_id")
    if not pipeline_id:
        return None
    async with factory() as session:
        return await PipelineRepository(session).get(pipeline_id, with_tasks=True)


async def _load_screenshot(
    factory: async_sessionmaker[AsyncSession], events: list[tuple[str, dict[str, Any]]]
) -> Screenshot | None:
    screenshot_id = (_maybe_result(events, "get_screenshot") or {}).get("screenshot_id")
    if not screenshot_id:
        return None
    async with factory() as session:
        return await ScreenshotRepository(session).get(screenshot_id)


async def _load_task_snapshots(
    factory: async_sessionmaker[AsyncSession], pipeline_id: str
) -> list[Screenshot]:
    """该流水线的留档截图；M23 §3.9 起任务终态会自动留一张，不需要 agent 开口。"""
    async with factory() as session:
        rows = await ScreenshotRepository(session).list_by_pipeline(pipeline_id)
    return [row for row in rows if str(row.trigger) == ScreenshotTrigger.TASK_SNAPSHOT.value]


def _image_urls(messages: list[dict[str, Any]]) -> list[str]:
    """一条请求里所有 tool 消息携带的图片 URL。"""
    urls: list[str] = []
    for message in messages:
        content = message.get("content")
        if message.get("role") != "tool" or not isinstance(content, list):
            continue
        urls += [
            part["image_url"]["url"]
            for part in content
            if isinstance(part, dict) and part.get("type") == "image_url"
        ]
    return urls


def test_account_resources_flow_reads_the_playbook_then_reports_home_currencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flow = asyncio.run(_run_flow(tmp_path, monkeypatch))
    events = turn_events(flow.chunks)
    turns = flow.llm.turns

    # 1. 全程不需要人工确认：真实 PolicyEngine 下这五个工具都不触发确认。
    assert [data for event, data in events if event == "confirmation_required"] == []

    # 2. §4.6 的五个步骤按顺序执行，每一步都成功。
    assert [data["name"] for event, data in events if event == "tool_start"] == [
        "read_knowledge",
        "submit_pipeline",
        "wait_pipeline",
        "back_to_home",
        "get_screenshot",
    ]
    assert [data.get("error") for event, data in events if event == "tool_result"] == [
        None
    ] * 5

    # 3. read_knowledge 真的读回了内置剧本全文。
    knowledge = _tool_result(events, "read_knowledge")
    assert knowledge["id"] == "account-resources"
    assert knowledge["source"] == "builtin"
    assert knowledge["body"].startswith("## 前置判断")
    assert "wait_pipeline" in knowledge["body"]

    # 4. StartUp 真的下到了内核边界（FakeAsst）并正常完成。
    assert [call[1][0] for call in flow.asst.calls_of("append_task")] == ["StartUp"]
    assert flow.asst.records_of(Message.TaskChainCompleted)
    assert flow.asst.callback_errors == []

    # 5. 等到终态，且流水线是 agent 来源、关联本会话。
    wait = _tool_result(events, "wait_pipeline")
    assert wait["finished"] is True
    assert wait["pipeline"]["status"] == "completed"
    assert flow.pipeline is not None
    assert flow.pipeline.source == PipelineSource.AGENT.value
    assert flow.pipeline.agent_session_id == flow.session_id
    assert [str(task.status) for task in flow.pipeline.tasks] == ["completed"]

    # 6. 每次模型请求的 system 消息都带知识库索引行与 now（每轮开头生成一次）。
    assert len(turns) == 6, [turn[1][0]["content"][:80] for turn in turns]
    for _model, messages, _tools in turns:
        system = messages[0]
        assert system["role"] == "system"
        assert "- account-resources｜查询账号资源：" in system["content"]
        assert '"now":' in system["content"]

    # 7. 最后一次请求带 16:9 截图；之前的请求都没有图。
    assert [_image_urls(turn[1]) for turn in turns[:-1]] == [[]] * (len(turns) - 1)
    images = _image_urls(turns[-1][1])
    assert len(images) == 1
    assert images[0].startswith("data:image/jpeg;base64,")
    # 图像与 JSON 摘要并进同一条 tool 消息（多模态 content 里既有 image_url 也有文本）。
    screenshot_text = "".join(
        content_text(message["content"])
        for message in turns[-1][1]
        if message.get("role") == "tool"
    )
    assert "screenshot_id" in screenshot_text
    assert flow.screenshot is not None
    assert (flow.screenshot.width, flow.screenshot.height) == IMAGE_SIZE
    # 设备被抓了两次：agent 自己的 get_screenshot 一次，加上 §3.9 的任务终态留档一次
    # ——留档对 agent 来源的流水线没有豁免（docs/13 §8.29）。
    assert flow.device.screenshots == 2
    assert len(flow.task_snapshots) == 1
    snapshot = flow.task_snapshots[0]
    assert snapshot.trigger == ScreenshotTrigger.TASK_SNAPSHOT.value
    assert snapshot.pipeline_id == flow.pipeline.id
    assert [task.id for task in flow.pipeline.tasks] == [snapshot.task_id]
    assert flow.device.back_to_home_calls == 1

    # 8. 最后给出文字回答并正常收流。
    assert flow.llm.responses == []
    assert ANSWER in "".join(
        data["text"] for event, data in events if event == "text_delta"
    )
    assert events[-1][0] == "message_end"
