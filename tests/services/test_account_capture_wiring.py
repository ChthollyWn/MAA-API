"""M33 W1：账号识别回调的接线契约（规格 §4.1）。

接线只做「调度」：解析与落库在 :class:`~maa_api.services.account.capture.AccountCaptureService`
里（它自己吞异常）。这里用假 client / 假采集服务验证：

- 只有 ``SubTaskExtraInfo`` 会进采集，其余消息（任务链、连接事件、坏 payload）直接放行；
- 流水线归属来自 ``context_provider``（真身是 ``PipelineRunner.log_context``），查不到就
  记 ``None``，但**不能**因此不落库；
- 渠道（``client_type``）来自注入的 provider，读失败只少一个字段；
- 采集任务抛异常不得冒泡到内核回调线程；
- 没有运行中的事件循环时只记 warning，不抛。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from maa_api.core.enums import Message
from maa_api.services.account.callback_wiring import install_account_capture


DEPOT_ENVELOPE: dict[str, Any] = {
    "taskid": 4,
    "class": "asst::DepotRecognitionTask",
    "what": "DepotInfo",
    "details": {"data": '{"30012": 120, "4001": 15}', "done": True},
}


class FakeClient:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}

    def on(self, event_type: str, handler: Any) -> None:
        self.handlers[event_type] = handler


class FakeCapture:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error = error

    async def handle_callback(self, callback: Any, **kwargs: Any) -> None:
        self.calls.append({"callback": callback, **kwargs})
        if self.error is not None:
            raise self.error


def _dispatch(client: FakeClient, envelope: dict[str, Any]) -> None:
    client.handlers["CALLBACK"]({"msg": int(Message.SubTaskExtraInfo), "details": envelope})


def test_dispatch_passes_envelope_and_pipeline_context():
    client = FakeClient()
    capture = FakeCapture()
    install_account_capture(
        client,
        capture,
        context_provider=lambda msg, details: ("pipeline-1", "task-1"),
        client_type_provider=lambda: "Bilibili",
    )

    async def scenario() -> None:
        _dispatch(client, DEPOT_ENVELOPE)
        # 接线只调度：回调返回时采集还没跑，等事件循环把任务跑完。
        assert capture.calls == []
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert len(capture.calls) == 1
    call = capture.calls[0]
    assert call["callback"] is DEPOT_ENVELOPE
    assert call["message"] is Message.SubTaskExtraInfo
    assert call["pipeline_id"] == "pipeline-1"
    assert call["task_id"] == "task-1"
    assert call["client_type"] == "Bilibili"
    # ``created_at`` 不给：采集服务自己按 utcnow 记识别时间。
    assert "created_at" not in call


def test_dispatch_ignores_other_messages_and_bad_payloads():
    client = FakeClient()
    capture = FakeCapture()
    contexts: list[Any] = []

    def context_provider(msg: Any, details: Any) -> tuple[str, str] | None:
        contexts.append(msg)
        return None

    install_account_capture(client, capture, context_provider=context_provider)
    handler = client.handlers["CALLBACK"]

    async def scenario() -> None:
        handler({"msg": int(Message.TaskChainStart), "details": {"taskid": 1}})
        handler({"msg": int(Message.AllTasksCompleted), "details": {}})
        handler("not-a-mapping")
        handler({"msg": int(Message.SubTaskExtraInfo)})  # 没有 details
        handler({"msg": int(Message.SubTaskExtraInfo), "details": "oops"})
        handler({"msg": "not-an-int", "details": DEPOT_ENVELOPE})
        handler({"details": DEPOT_ENVELOPE})  # 没有 msg
        await asyncio.sleep(0)
        assert capture.calls == []
        assert contexts == []
        # 只有 SubTaskExtraInfo 会去问上下文。
        _dispatch(client, DEPOT_ENVELOPE)
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert contexts == [Message.SubTaskExtraInfo]
    assert len(capture.calls) == 1
    # 上下文查不到（流水线不在跑）→ 归属为空，但照样落库。
    assert capture.calls[0]["pipeline_id"] is None
    assert capture.calls[0]["task_id"] is None
    assert capture.calls[0]["client_type"] is None


def test_provider_failures_degrade_without_skipping_capture(caplog):
    client = FakeClient()
    capture = FakeCapture()

    def boom_context(msg: Any, details: Any) -> Any:
        raise RuntimeError("context exploded")

    def boom_client_type() -> str:
        raise RuntimeError("settings exploded")

    install_account_capture(
        client,
        capture,
        context_provider=boom_context,
        client_type_provider=boom_client_type,
    )

    async def scenario() -> None:
        with caplog.at_level(logging.ERROR):
            _dispatch(client, DEPOT_ENVELOPE)
            await asyncio.sleep(0)

    asyncio.run(scenario())
    assert len(capture.calls) == 1
    assert capture.calls[0]["pipeline_id"] is None
    assert capture.calls[0]["client_type"] is None
    assert "查找账号识别回调的流水线上下文失败" in caplog.text
    assert "读取当前渠道（client_type）失败" in caplog.text


def test_capture_failure_does_not_escape_the_callback(caplog):
    client = FakeClient()
    capture = FakeCapture(error=RuntimeError("db exploded"))
    install_account_capture(client, capture)

    async def scenario() -> None:
        with caplog.at_level(logging.WARNING):
            _dispatch(client, DEPOT_ENVELOPE)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

    asyncio.run(scenario())
    assert len(capture.calls) == 1
    assert "账号识别采集任务异常（已忽略，不影响流水线）" in caplog.text


def test_without_event_loop_only_warns(caplog):
    client = FakeClient()
    capture = FakeCapture()
    install_account_capture(client, capture)

    with caplog.at_level(logging.WARNING):
        _dispatch(client, DEPOT_ENVELOPE)  # 同步调用：没有运行中的事件循环
    assert capture.calls == []
    assert "账号识别回调没有可调度的事件循环，本次不落库" in caplog.text


def test_install_requires_callback_registration():
    with pytest.raises(TypeError, match=r"on\(event_type, handler\)"):
        install_account_capture(object(), FakeCapture())
