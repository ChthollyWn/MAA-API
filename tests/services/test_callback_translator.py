"""MaaCore callback translation is pure, exhaustive for known values, and lossless.

The payloads here mirror what MaaCore v6.17.5 actually sends (sampled from
``resource/maa-api/debug/asst.log`` and ``tests/fixtures/spike_subprocess_findings.md``):
``details`` is flat, the task name lives in ``details.task``, and a ProcessTask
step additionally carries ``details.action``. ``details.details`` never holds a
``task`` key on this kernel, which is exactly what the first implementation got
wrong — it read the name from there and labelled every subtask "未映射回调".
"""

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


def _confirmation(task: str) -> dict:
    """具名确认回调：真正的任务名在内层 ``details.details.task``。

    真实日志里的 ``StartButton2`` 就是这种形状：``details`` 里没有 ``action``，
    只有内层 ``details.details`` 带任务名与 ``exec_times``。
    """

    return {
        "class": "asst::ProcessTask",
        "details": {
            "algorithm": "JustReturn",
            "details": {"task": task, "exec_times": 3},
        },
        "first": ["StartAtHome"],
        "subtask": "ProcessTask",
        "taskchain": "Fight",
        "taskid": 5,
        "uuid": "device-uuid",
    }


def _step(action: str = "ClickSelf") -> dict:
    """动作级步骤：ProcessTask 的每一次匹配与点击，带 ``details.action``。"""

    return {
        "class": "asst::ProcessTask",
        "details": {
            "action": action,
            "algorithm": "MatchTemplate",
            "exec_times": 1,
            "max_times": 2147483647,
            "result": {"rect": [434, 655, 148, 45], "score": 0.999959},
            "task": "InfrastClearButton",
        },
        "first": ["StartAtHome"],
        "subtask": "ProcessTask",
        "taskchain": "Infrast",
        "taskid": 5,
        "uuid": "device-uuid",
    }


def _payload(what: str) -> dict:
    """按 ``what`` 归属的消息类型构造一份形状正确的载荷。"""

    if what in CONNECTION_MAP:
        flat = {
            "reason": "offline",
            "height": 1440,
            "width": 2560,
            "cost": 42,
            "avg": 670,
            "max": 1686,
            "min": 148,
            "fps": 60,
            "refresh_period_ns": 16666666,
        }
        if what == "FastestWayToScreencap":
            flat.pop("height"), flat.pop("width")
        return {"what": what, "uuid": "device-uuid", "why": "Normal", "details": flat}

    flat = {
        "task": "StartUpBegin",
        "exec_times": 3,
        "max_times": 5,
        "facility": "Power",
        "index": 2,
        "product": "Drone",
        "name": "1-7",
        "tags": ["削弱", "生存"],
        "tag": "高级资深干员",
        "level": 5,
        "current_sanity": 120,
        "max_sanity": 135,
        "series": 10,
        "times_finished": 0,
        "sanity_cost": 60,
        "stage": {"stageCode": "1-7"},
        "stars": 3,
        "stats": [
            {"itemName": "龙门币", "quantity": 10, "addQuantity": 2},
            {"itemName": "作战记录", "quantity": 4, "addQuantity": 0},
        ],
    }
    return {
        "class": "asst::ProcessTask",
        "what": what,
        "details": flat,
        "subtask": "ProcessTask",
        "taskchain": "Fight",
        "taskid": 5,
        "uuid": "device-uuid",
    }


def _operbox_payload(
    *,
    all_opers: list | None = None,
    own_opers: list | None = None,
    done: bool = True,
) -> dict:
    """``OperBoxInfo`` 的 ``SubTaskExtraInfo`` 载荷；没给的两个列表就不放进 ``details``。"""

    details: dict = {"done": done}
    if all_opers is not None:
        details["all_opers"] = all_opers
    if own_opers is not None:
        details["own_opers"] = own_opers
    return {"what": "OperBoxInfo", "details": details}


@pytest.mark.parametrize("what", sorted(CONNECTION_MAP))
def test_every_connection_mapping_is_reachable(
    translator: CallbackTranslator, what: str
) -> None:
    payload = _payload(what)

    records = translator.translate(Message.ConnectionInfo, payload)

    assert len(records) == 1
    assert records[0].content
    assert "{details}" not in records[0].content
    assert records[0].source == "task"
    assert records[0].level in {"DEBUG", "INFO", "WARNING"}
    assert records[0].raw["msg"] == int(Message.ConnectionInfo)
    assert records[0].raw["details"] == payload
    assert records[0].raw.get("what") == what


@pytest.mark.parametrize("what", sorted(EXTRA_INFO_MAP))
def test_every_extra_info_mapping_is_reachable(
    translator: CallbackTranslator, what: str
) -> None:
    payload = _payload(what)

    records = translator.translate(Message.SubTaskExtraInfo, payload)

    assert len(records) == 1
    assert records[0].content
    assert not any(f"{{{key}}}" in records[0].content for key in ("task", "tags", "product"))
    assert records[0].source == "task"
    assert records[0].raw["msg"] == int(Message.SubTaskExtraInfo)
    assert records[0].raw["details"] == payload
    assert records[0].raw.get("what") == what


@pytest.mark.parametrize("task", sorted(SUB_TASK_START_MAP))
def test_every_subtask_start_mapping_is_reachable(
    translator: CallbackTranslator, task: str
) -> None:
    records = translator.translate(Message.SubTaskStart, _confirmation(task))

    assert len(records) == 1
    assert records[0].content == SUB_TASK_START_MAP[task].format(exec_times=3)
    assert records[0].level == "INFO"


def test_confirmations_read_the_flat_details_task_key(
    translator: CallbackTranslator,
) -> None:
    """真实载荷里任务名在 ``details.task``；读 ``details.details.task`` 会全表落空。"""

    payload = {
        "class": "asst::ProcessTask",
        "details": {"task": "InfrastDormDoubleConfirmButton", "exec_times": 1},
        "subtask": "ProcessTask",
        "uuid": "device-uuid",
    }

    record = translator.translate(Message.SubTaskStart, payload)[0]

    assert record.content == "请进行基建宿舍的二次确认"
    assert record.level == "INFO"


def test_process_task_steps_are_silent(translator: CallbackTranslator) -> None:
    """动作级步骤不产记录：一遍日常流水线有数千条，且对使用者没有语义。"""

    for action in ("ClickSelf", "Stop", "DoNothing", "Swipe", "ClickRect"):
        assert translator.translate(Message.SubTaskStart, _step(action)) == []
        assert translator.translate(Message.SubTaskCompleted, _step(action)) == []


def test_named_subtasks_keep_their_name_and_level(
    translator: CallbackTranslator,
) -> None:
    start = translator.translate(
        Message.SubTaskStart,
        {
            "class": "asst::StartGameTaskPlugin",
            "details": {},
            "subtask": "StartGameTask",
            "taskchain": "StartUp",
            "uuid": "device-uuid",
        },
    )[0]
    completed = translator.translate(Message.SubTaskCompleted, _confirmation("FightTimesTask"))[0]

    assert start.content == "子任务开始：StartGameTask"
    assert start.level == "DEBUG"
    assert completed.content == "子任务完成：FightTimesTask"
    assert completed.level == "DEBUG"


def test_subtask_error_names_the_failed_task_from_first(
    translator: CallbackTranslator,
) -> None:
    """SubTaskError 的载荷里 ``details`` 是空的，失败步骤在顶层 ``first``。"""

    payload = {
        "class": "asst::ProcessTask",
        "details": {},
        "first": ["InfrastOperListTabSkillUnClicked"],
        "pre_task": "",
        "subtask": "ProcessTask",
        "taskchain": "Infrast",
        "taskid": 5,
        "uuid": "device-uuid",
    }

    record = translator.translate(Message.SubTaskError, payload)[0]

    assert record.content == "子任务出错 [InfrastOperListTabSkillUnClicked]"
    assert record.level == "WARNING"
    assert record.raw["details"] == payload


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


def test_screencap_cost_and_emulator_fps_are_mapped(
    translator: CallbackTranslator,
) -> None:
    cost = translator.translate(
        Message.ConnectionInfo,
        {"what": "ScreencapCost", "details": {"avg": 670, "max": 1686, "min": 148}},
    )[0]
    fps = translator.translate(
        Message.ConnectionInfo,
        {"what": "EmulatorFPS", "details": {"fps": 60, "refresh_period_ns": 16666666}},
    )[0]

    assert cost.content == "截图耗时 670ms（148/1686）"
    assert cost.level == "INFO"
    assert fps.content == "模拟器帧率 60"
    assert fps.level == "INFO"


def test_recruit_tags_render_from_a_list(
    translator: CallbackTranslator,
) -> None:
    """v6.17.5 的 ``RecruitTagsSelected`` 发数组，模板不能把 list 直接格式化进去。"""

    record = translator.translate(
        Message.SubTaskExtraInfo,
        {"what": "RecruitTagsSelected", "details": {"tags": ["削弱"]}},
    )[0]

    assert record.content == "已选择Tags：削弱"
    assert record.level == "INFO"


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


def test_operbox_count_reads_own_from_all_opers_not_own_opers(
    translator: CallbackTranslator,
) -> None:
    """真机 2026-10-10 17:20 那次：``all_opers`` 463 条里 ``own=true`` 383 条、``own_opers`` 380 条。

    「已拥有多少名」只能看 ``all_opers`` 的 ``own``：那 3 名已拥有、却因为内核没给练度详情
    而从不进 ``own_opers``（阿米娅的两个形态与暮落）。按 ``own_opers`` 数会少报 3 名。
    """

    detail_less = ["char_002_amiya", "char_1037_amiya3", "char_512_aprot"]
    owned = detail_less + [f"char_{index:04d}_owned" for index in range(380)]
    all_opers = [
        {"id": char_id, "name": char_id, "own": True, "rarity": 3} for char_id in owned
    ] + [
        {"id": f"char_{index:04d}_missing", "name": f"未拥有{index}", "own": False, "rarity": 3}
        for index in range(463 - len(owned))
    ]
    own_opers = [
        {"id": char_id, "own": True, "elite": 0, "level": 1, "potential": 1, "rarity": 3}
        for char_id in owned
        if char_id not in detail_less
    ]
    assert len(all_opers) == 463 and len(own_opers) == 380

    record = translator.translate(
        Message.SubTaskExtraInfo,
        _operbox_payload(all_opers=all_opers, own_opers=own_opers),
    )[0]

    assert record.content == "识别到 383 名干员"
    assert record.level == "INFO"


def test_operbox_count_is_zero_when_nothing_is_owned(
    translator: CallbackTranslator,
) -> None:
    """游戏没启动的那次失败尝试：``all_opers`` 每条 ``own`` 都是 false → 0。"""

    all_opers = [
        {"id": f"char_{index:04d}_missing", "name": f"未拥有{index}", "own": False, "rarity": 3}
        for index in range(463)
    ]
    record = translator.translate(
        Message.SubTaskExtraInfo,
        _operbox_payload(all_opers=all_opers, own_opers=[]),
    )[0]

    assert record.content == "识别到 0 名干员"
    assert record.level == "INFO"

    # 只要 all_opers 带 own 字段，计数就只看 own，不再退回 own_opers 的条数
    # （真实数据里 own_opers ⊆ all_opers 的 own=true，这种形状只为固定口径）。
    fallback_free = translator.translate(
        Message.SubTaskExtraInfo,
        _operbox_payload(all_opers=all_opers, own_opers=[{"id": "char_002_amiya", "own": True}]),
    )[0]

    assert fallback_free.content == "识别到 0 名干员"


def test_operbox_count_falls_back_to_own_opers_without_all_opers(
    translator: CallbackTranslator,
) -> None:
    """旧版内核不带 ``all_opers``：退回 ``own_opers`` 条数。"""

    own_opers = [
        {"id": "char_002_amiya", "own": True, "elite": 0, "level": 1, "potential": 1, "rarity": 3},
        {"id": "char_512_aprot", "own": True, "elite": 1, "level": 50, "potential": 3, "rarity": 5},
    ]

    record = translator.translate(
        Message.SubTaskExtraInfo,
        _operbox_payload(own_opers=own_opers),
    )[0]

    assert record.content == "识别到 2 名干员"
    assert record.level == "INFO"


def test_operbox_count_falls_back_when_all_opers_has_no_own_field(
    translator: CallbackTranslator,
) -> None:
    """``all_opers`` 在、但没有任何条目带 ``own`` 字段：退回 ``own_opers``，不能数成 0。"""

    all_opers = [
        {"id": f"char_{index:04d}_legacy", "name": f"旧内核{index}", "rarity": 3}
        for index in range(463)
    ]
    legacy_own = [{"id": "char_002_amiya", "own": True, "level": 1}]

    with_own_opers = translator.translate(
        Message.SubTaskExtraInfo,
        _operbox_payload(all_opers=all_opers, own_opers=legacy_own),
    )[0]
    only_all_opers = translator.translate(
        Message.SubTaskExtraInfo,
        _operbox_payload(all_opers=all_opers),
    )[0]

    assert with_own_opers.content == "识别到 1 名干员"
    assert only_all_opers.content == "识别到 463 名干员"
    assert with_own_opers.level == only_all_opers.level == "INFO"


def test_operbox_count_ignores_malformed_lists(
    translator: CallbackTranslator,
) -> None:
    """非 list（或列表里混着非 Mapping）按缺省处理：既不数错，也不抛异常。"""

    not_a_list = translator.translate(
        Message.SubTaskExtraInfo,
        _operbox_payload(all_opers="nope", own_opers=[{"id": "char_002_amiya", "own": True}]),
    )[0]
    nothing_usable = translator.translate(
        Message.SubTaskExtraInfo,
        _operbox_payload(all_opers="nope", own_opers="nope"),
    )[0]
    mixed = translator.translate(
        Message.SubTaskExtraInfo,
        _operbox_payload(all_opers=[{"id": "char_002_amiya", "own": True}, "junk"]),
    )[0]

    assert not_a_list.content == "识别到 1 名干员"
    assert nothing_usable.content == "识别到 0 名干员"
    assert mixed.content == "识别到 1 名干员"


def test_unknown_subtask_names_stay_identifiable(
    translator: CallbackTranslator,
) -> None:
    """未见过的具名子任务要能看出叫什么，而不是退化成一条「未映射回调」。"""

    unknown = {
        "class": "asst::NewTaskPlugin",
        "details": {"task": "NewTaskAddedByMaa"},
        "subtask": "NewTaskPlugin",
        "uuid": "device-uuid",
    }

    fallback = translator.translate(Message.SubTaskStart, unknown)[0]

    assert fallback.content == "子任务开始：NewTaskAddedByMaa"
    assert fallback.level == "DEBUG"
    assert fallback.raw["msg"] == int(Message.SubTaskStart)
    assert fallback.raw["details"] == unknown


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
        (Message.SubTaskCompleted, {}, "SubTaskCompleted 完成", "DEBUG"),
        (Message.SubTaskStopped, {}, "SubTaskStopped 完成", "DEBUG"),
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


def test_unknown_message_id_keeps_raw_payload(translator: CallbackTranslator) -> None:
    record = translator.translate(999, {"what": "FromTheFuture"})[0]

    assert record.content == "未映射回调 Message.999"
    assert record.level == "DEBUG"
    assert record.raw["msg"] == 999
    assert record.raw["details"] == {"what": "FromTheFuture"}
