"""MaaCore callback translation is pure, exhaustive for known values, and lossless."""

from __future__ import annotations

import time

import pytest

from maa_api.core.enums import Message
from maa_api.services.callback_translator import (
    CONNECTION_MAP,
    EXTRA_INFO_MAP,
    SUB_TASK_START_MAP,
    CallbackTranslator,
)


@pytest.fixture
def translator() -> CallbackTranslator:
    return CallbackTranslator()


def _payload(what: str) -> dict:
    return {
        "what": what,
        "uuid": "device-uuid",
        "why": "Normal",
        "details": {
            "task": what,
            "exec_times": 3,
            "height": 1440,
            "width": 2560,
            "cost": 42,
            "current_sanity": 120,
            "max_sanity": 135,
            "tags": "先锋, 近卫",
            "tag": "高级资深干员",
            "level": 5,
            "facility": "贸易站",
            "index": 2,
            "name": "1-7",
            "stage": {"stageCode": "1-7"},
            "stars": 3,
            "stats": [
                {"itemName": "龙门币", "quantity": 10, "addQuantity": 2},
                {"itemName": "作战记录", "quantity": 4, "addQuantity": 0},
            ],
        },
    }


@pytest.mark.parametrize("what", sorted(CONNECTION_MAP))
def test_every_connection_mapping_is_reachable(
    translator: CallbackTranslator, what: str
) -> None:
    payload = _payload(what)

    records = translator.translate(Message.ConnectionInfo, payload)

    assert len(records) == 1
    assert records[0].content
    assert records[0].source == "task"
    assert records[0].level in {"DEBUG", "INFO", "WARNING"}
    assert records[0].raw["msg"] == int(Message.ConnectionInfo)
    assert records[0].raw["details"] == payload
    assert records[0].raw.get("what") == what


@pytest.mark.parametrize("task", sorted(SUB_TASK_START_MAP))
def test_every_subtask_start_mapping_is_reachable(
    translator: CallbackTranslator, task: str
) -> None:
    payload = {"what": "SubTaskStart", "details": {"task": task, "exec_times": 3}}

    records = translator.translate(Message.SubTaskStart, payload)

    assert len(records) == 1
    assert records[0].content == SUB_TASK_START_MAP[task].format(exec_times=3)
    assert records[0].level == "INFO"


@pytest.mark.parametrize("what", sorted(EXTRA_INFO_MAP))
def test_every_extra_info_mapping_is_reachable(
    translator: CallbackTranslator, what: str
) -> None:
    payload = _payload(what)

    records = translator.translate(Message.SubTaskExtraInfo, payload)

    assert len(records) == 1
    assert records[0].content
    assert records[0].source == "task"
    assert records[0].raw["msg"] == int(Message.SubTaskExtraInfo)
    assert records[0].raw["details"] == payload
    assert records[0].raw.get("what") == what


def test_connect_failed_aliases_share_the_correct_mapping(
    translator: CallbackTranslator,
) -> None:
    assert CONNECTION_MAP["ConnectFailed"] == CONNECTION_MAP["ConnectFaild"]
    canonical = translator.translate(
        Message.ConnectionInfo,
        {"what": "ConnectFailed", "details": {"reason": "offline"}},
    )[0]
    legacy = translator.translate(
        Message.ConnectionInfo,
        {"what": "ConnectFaild", "details": {"reason": "offline"}},
    )[0]
    assert canonical.content == legacy.content == "模拟器连接失败 {'reason': 'offline'}"
    assert canonical.level == legacy.level == "WARNING"


def test_connection_resolution_uses_nested_height_and_width(
    translator: CallbackTranslator,
) -> None:
    resolution = translator.translate(
        Message.ConnectionInfo,
        {
            "what": "ResolutionGot",
            "details": {"height": 1440, "width": 2560},
        },
    )[0]
    resolution_info = translator.translate(
        Message.ConnectionInfo,
        {
            "what": "ResolutionInfo",
            "details": {"height": 1440, "width": 2560},
            "why": "Normal",
        },
    )[0]

    assert resolution.content == "已获取到模拟器分辨率 1440*2560"
    assert resolution_info.content == "分辨率信息 1440*2560（Normal）"
    assert resolution_info.level == "DEBUG"


def test_stage_drops_formatting_and_sanity_mapping(
    translator: CallbackTranslator,
) -> None:
    drops = translator.translate(Message.SubTaskExtraInfo, _payload("StageDrops"))[0]
    sanity = translator.translate(Message.SubTaskExtraInfo, _payload("SanityBeforeStage"))[0]

    assert drops.content == (
        "3⭐通关1-7 \n掉落统计: \n"
        "龙门币: 10(+2)\n作战记录: 4(+0)"
    )
    assert sanity.content == "当前理智：120/135"
    assert sanity.source == "task" and sanity.level == "INFO"


def test_unknown_callback_names_and_subtask_error_keep_raw_payload(
    translator: CallbackTranslator,
) -> None:
    unknown = {"what": "NewTaskAddedByMaa", "details": {"task": "unknown"}}
    fallback = translator.translate(Message.SubTaskStart, unknown)[0]
    error = translator.translate(
        Message.SubTaskError,
        {"what": "SubTaskError", "details": {"task": "AnnihilationTask"}},
    )[0]

    assert fallback.content == "未映射回调 SubTaskStart.unknown"
    assert fallback.level == "DEBUG"
    assert fallback.raw["msg"] == int(Message.SubTaskStart)
    assert fallback.raw["details"] == unknown
    assert fallback.raw.get("what") == "NewTaskAddedByMaa"
    assert error.content == "子任务出错 [AnnihilationTask]"
    assert error.level == "WARNING"


@pytest.mark.parametrize(
    ("message", "payload", "expected_content", "expected_level"),
    [
        (Message.TaskChainStopped, {"taskchain": "Fight"}, "任务已中止 [Fight]", "WARNING"),
        (Message.TaskChainStart, {"taskchain": "Fight"}, "开始任务 [Fight]", "INFO"),
        (Message.TaskChainCompleted, {"taskchain": "Fight"}, "完成任务 [Fight]", "INFO"),
        (Message.TaskChainError, {"taskchain": "Fight"}, "任务失败 [Fight]", "ERROR"),
        (Message.AllTasksCompleted, {}, "全部任务已完成", "INFO"),
        (Message.InternalError, {"message": "native failure"}, "MaaCore 内部错误：native failure", "ERROR"),
        (Message.InitFailed, {}, "MaaCore 初始化失败", "ERROR"),
        (Message.Destroyed, {}, "MaaCore 实例已销毁", "DEBUG"),
        (Message.TaskChainExtraInfo, {"what": "ignored"}, "TaskChainExtraInfo ignored", "DEBUG"),
        (Message.SubTaskCompleted, {}, "SubTaskCompleted", "DEBUG"),
        (Message.SubTaskStopped, {}, "SubTaskStopped", "DEBUG"),
        (Message.AsyncCallInfo, {"what": "AsstAsyncConnect"}, "异步调用完成 AsstAsyncConnect", "DEBUG"),
    ],
)
def test_additional_message_types(
    translator: CallbackTranslator,
    message: Message,
    payload: dict,
    expected_content: str,
    expected_level: str,
) -> None:
    record = translator.translate(message, payload)[0]

    assert record.content == expected_content
    assert record.level == expected_level
    assert record.raw["msg"] == int(message)
    assert record.raw["details"] == payload


def test_translation_uses_event_arrival_time_and_unknown_what_is_visible(
    translator: CallbackTranslator,
) -> None:
    before = time.time()
    record = translator.translate(
        Message.SubTaskExtraInfo,
        {"what": "NewExtra", "details": {"opaque": 1}},
    )[0]

    assert before <= record.ts <= time.time()
    assert record.content == "未映射回调 SubTaskExtraInfo.NewExtra"
    assert record.level == "DEBUG"
    assert record.raw["msg"] == int(Message.SubTaskExtraInfo)
    assert record.raw["details"] == {
        "what": "NewExtra",
        "details": {"opaque": 1},
    }
    assert record.raw.get("what") == "NewExtra"
