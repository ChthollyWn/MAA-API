"""结果识别与判定（M29 规格 §9.7）：识别任务的定义、回调序列 → ``outcome``。

三块内容：

1. ``copilot_result_tasks()`` / ``copilot_exit_tasks()`` 的任务定义：只读、可被内核加载、
   每个识别项都有一个 **``@Hit`` 标记任务**（``maxTimes: 1`` + ``next: ["#self"]``，
   第二次执行触发内核的 ``ExceededLimit`` 回调）——这是「内核识别到了哪一项」在
   ``log_entry`` 里留下的持久信号，不依赖进程内订阅；
2. ``judge_callbacks`` / ``CopilotResultService.judge``：按 §9.7 的表把回调序列判成
   ``three_star`` / ``two_star`` / ``clear`` / ``failed`` / ``support_unavailable`` /
   ``sanity_insufficient`` / ``not_started`` / ``unknown``；
3. ``ensure_tasks`` 把独立文件写进 custom 层（与 ``ResourceService`` 聚合的
   ``tasks.json`` 分开），``callbacks_for`` 从 ``log_entry`` 读回识别信号。

用例全部离线：没有内核、没有网络；``ensure_tasks`` 写到 ``tmp_path``。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表
from maa_api.db.models import LogEntry
from maa_api.db.session import make_engine
from maa_api.services.copilot.result import (
    EXIT_TASK_ENTRY,
    EXIT_TASK_PREFIX,
    HIT_SUFFIX,
    JUDGEABLE_OUTCOMES,
    RESULT_TASK_ENTRY,
    RESULT_TASK_FILE,
    RESULT_TASK_PREFIX,
    CopilotResultService,
    copilot_exit_tasks,
    copilot_result_tasks,
    result_task_path,
)

NOW = datetime(2026, 10, 10, 3, 0, 0)


def make_pipeline(pipeline_id: str):
    """``log_entry.pipeline_id`` 指向 ``pipeline``：回读日志前先建行。"""
    from maa_api.db.models import Pipeline

    return Pipeline(id=pipeline_id, source="agent", priority=1, task_count=1)


@pytest.fixture
def result_session_factory(tmp_path):
    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'copilot_result.db'}", poolclass=NullPool
    )

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_schema())
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    engine.sync_engine.dispose()


class _FakeResourceService:
    """只实现 ``ensure_tasks`` 用到的两个公开属性：``resource_root`` 与重载回调。"""

    def __init__(self, root) -> None:
        self.resource_root = root
        self.reloads = 0

    def reload_resources(self) -> None:
        self.reloads += 1


class _FakeClient:
    """记下 ``on`` 注册的处理器，测试里手动触发（``off`` 内核侧没有，靠 tap 失效）。"""

    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def on(self, event_type: str, handler: Any) -> None:
        assert event_type == "CALLBACK"
        self.handlers.append(handler)

    def fire(self, payload: dict) -> None:
        for handler in list(self.handlers):
            handler(payload)


def _subtask_start(task: str) -> dict:
    return {
        "msg": 20001,
        "details": {"uuid": "u", "what": "", "details": {"task": task, "action": "DoNothing"}},
    }


def _exceeded(task: str) -> dict:
    return {
        "msg": 20003,
        "details": {
            "uuid": "u",
            "what": "ExceededLimit",
            "details": {"task": task, "exec_times": 1, "max_times": 1},
        },
    }


def _extra(what: str, **details: Any) -> dict:
    return {"msg": 20003, "details": {"uuid": "u", "what": what, "details": details}}


# ---------------------------------------------------------------------------
# 任务定义
# ---------------------------------------------------------------------------
def test_result_tasks_are_read_only_and_have_one_entry():
    tasks = copilot_result_tasks()
    assert RESULT_TASK_ENTRY in tasks
    entry = tasks[RESULT_TASK_ENTRY]
    assert entry["algorithm"] == "JustReturn"
    # 入口把全部识别项按顺序串起来，最后一项是兜底（永远匹配）。
    assert entry["next"][0] == f"{RESULT_TASK_PREFIX}@ThreeStar"
    assert entry["next"][-1] == f"{RESULT_TASK_PREFIX}@Unknown"
    for name, task in tasks.items():
        assert task.get("action", "DoNothing") == "DoNothing", name
        assert "ClickSelf" not in json.dumps(task)
        assert "ClickRect" not in json.dumps(task)


def test_every_case_has_a_hit_marker_for_a_persisted_signal():
    """每个识别项都带 ``@Hit``：第二次执行触发内核的 ExceededLimit（持久化到日志）。"""
    tasks = copilot_result_tasks()
    cases = [
        name
        for name in tasks
        if name.startswith(f"{RESULT_TASK_PREFIX}@")
        and name != RESULT_TASK_ENTRY
        and not name.endswith(HIT_SUFFIX)
    ]
    markers = [name for name in tasks if name.endswith(HIT_SUFFIX)]
    assert markers, "至少要有一个 @Hit 标记任务"
    assert sorted(markers) == sorted(f"{name}{HIT_SUFFIX}" for name in cases)
    for name in cases:
        assert tasks[name]["next"] == [f"{name}{HIT_SUFFIX}"]
        marker = tasks[f"{name}{HIT_SUFFIX}"]
        assert marker["algorithm"] == "JustReturn"
        assert marker["maxTimes"] == 1
        assert marker["next"] == ["#self"]


def test_result_cases_cover_every_judgeable_outcome():
    tasks = copilot_result_tasks()
    for case, outcome in (
        ("ThreeStar", "three_star"),
        ("Adverse", "three_star"),
        ("TwoStar", "two_star"),
        ("EndOfAction", "clear"),
        ("Failed", "failed"),
        ("SanityInsufficient", "sanity_insufficient"),
        ("StartButton", "not_started"),
        ("Unknown", "unknown"),
    ):
        assert f"{RESULT_TASK_PREFIX}@{case}" in tasks, case
        assert outcome in JUDGEABLE_OUTCOMES


def test_failed_case_recognizes_both_failure_texts():
    tasks = copilot_result_tasks()
    failed = tasks[f"{RESULT_TASK_PREFIX}@Failed"]
    assert failed["algorithm"] == "OcrDetect"
    assert set(failed["text"]) >= {"行动失败", "任务失败"}


def test_start_button_case_uses_the_kernel_start_texts():
    tasks = copilot_result_tasks()
    start = tasks[f"{RESULT_TASK_PREFIX}@StartButton"]
    assert start["algorithm"] == "OcrDetect"
    assert {"开始行动", "开始作战"} <= set(start["text"])
    # 与内核 StartButton1 的 roi 同一片区域（规格 §9.7 的 not_started）。
    assert start["roi"] == [1010, 625, 260, 61]


def test_exit_tasks_return_to_the_start_screen_and_can_close_the_popup():
    tasks = copilot_exit_tasks()
    assert EXIT_TASK_ENTRY in tasks
    assert tasks[EXIT_TASK_ENTRY]["next"]  # 入口串起退出链
    names = set(tasks)
    assert f"{EXIT_TASK_PREFIX}@BackToStart" in names
    assert f"{EXIT_TASK_PREFIX}@ClosePopup" in names
    assert tasks[f"{EXIT_TASK_PREFIX}@BackToStart"]["baseTask"] == (
        "Copilot@ClickCornerUntilStartButton"
    )


def test_result_task_path_lives_in_the_custom_layer(tmp_path):
    path = result_task_path(tmp_path)
    assert path.name == RESULT_TASK_FILE
    assert path.parent.as_posix().endswith("maa-layers/custom/resource/tasks")


def test_ensure_tasks_writes_one_independent_file_and_reloads(tmp_path):
    resource = _FakeResourceService(tmp_path)
    service = CopilotResultService(resource_service=resource)

    async def scenario():
        first = await service.ensure_tasks()
        second = await service.ensure_tasks()
        return first, second

    first, second = asyncio.run(scenario())
    assert first["written"] is True and first["reloaded"] is True
    assert first["cached"] is False
    # 第二次走进程内缓存：文件内容没变，也不再重载内核资源（每次尝试都重载太贵）。
    assert second["written"] is False and second["cached"] is True
    assert resource.reloads == 1
    path = tmp_path / "maa-layers" / "custom" / "resource" / "tasks" / RESULT_TASK_FILE
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert RESULT_TASK_ENTRY in payload
    # 与用户的 Custom_* 任务分开：这个文件里没有 Custom_ 前缀的定义。
    assert not [name for name in payload if name.startswith("Custom_")]
    # 退出结算任务也在同一个独立文件体系里，但不与识别任务混在一个文件。
    assert (tmp_path / "maa-layers" / "custom" / "resource" / "tasks").is_dir()


def test_ensure_tasks_without_resource_service_raises():
    from maa_api.domain.errors import AppError, ErrorCode

    service = CopilotResultService()

    async def scenario():
        with pytest.raises(AppError) as info:
            await service.ensure_tasks()
        return info.value

    assert asyncio.run(scenario()).code == ErrorCode.SERVICE_UNAVAILABLE


# ---------------------------------------------------------------------------
# judge：§9.7 的表逐条
# ---------------------------------------------------------------------------
def test_judge_three_star_from_the_marker_and_from_the_case():
    service = CopilotResultService()
    assert service.judge([_exceeded(f"{RESULT_TASK_PREFIX}@ThreeStar{HIT_SUFFIX}")]) == "three_star"
    assert service.judge([_subtask_start(f"{RESULT_TASK_PREFIX}@ThreeStar")]) == "three_star"
    # Adverse（突袭）模板同样算三星（规格 §9.7 第一行）。
    assert service.judge([_exceeded(f"{RESULT_TASK_PREFIX}@Adverse{HIT_SUFFIX}")]) == "three_star"


def test_judge_two_star_and_clear():
    service = CopilotResultService()
    assert service.judge([_exceeded(f"{RESULT_TASK_PREFIX}@TwoStar{HIT_SUFFIX}")]) == "two_star"
    assert (
        service.judge([_exceeded(f"{RESULT_TASK_PREFIX}@EndOfAction{HIT_SUFFIX}")])
        == "clear"
    )


def test_judge_failed_sanity_and_not_started():
    service = CopilotResultService()
    assert service.judge([_exceeded(f"{RESULT_TASK_PREFIX}@Failed{HIT_SUFFIX}")]) == "failed"
    assert (
        service.judge([_exceeded(f"{RESULT_TASK_PREFIX}@SanityInsufficient{HIT_SUFFIX}")])
        == "sanity_insufficient"
    )
    assert (
        service.judge([_exceeded(f"{RESULT_TASK_PREFIX}@StartButton{HIT_SUFFIX}")])
        == "not_started"
    )


def test_judge_unknown_when_nothing_matched_or_nothing_arrived():
    service = CopilotResultService()
    assert service.judge([_exceeded(f"{RESULT_TASK_PREFIX}@Unknown{HIT_SUFFIX}")]) == "unknown"
    assert service.judge([]) == "unknown"
    assert service.judge(None) == "unknown"
    # 只有作业动作这类无关回调时也是 unknown（要模型兜底）。
    assert (
        service.judge([_extra("CopilotAction", action="Deploy", target="凛御银灰")])
        == "unknown"
    )


def test_judge_reads_the_kernels_own_copilot_chain():
    """内核 Copilot 任务自己的结算链也是信号（``Copilot@StageDrops-Stars-3`` 等）。"""
    service = CopilotResultService()
    assert service.judge([_subtask_start("Copilot@StageDrops-Stars-3")]) == "three_star"
    assert service.judge([_subtask_start("Copilot@StageDrops-Stars-Adverse")]) == "three_star"
    assert service.judge([_subtask_start("Copilot@EndOfAction")]) == "clear"
    assert service.judge([_subtask_start("FightMissionFailed")]) == "failed"


def test_judge_support_unavailable_from_formation_signals():
    """需要补位但没借到（Q42）：编队回调 + 授权里允许助战。"""
    service = CopilotResultService()
    signal = _extra("BattleFormationOperUnavailable", oper_name="黍", requirement_type="level")
    assert service.judge([signal], support="fill_one") == "support_unavailable"
    # 不允许助战时同样的信号只是「没开打」。
    assert service.judge([signal], support="none") == "not_started"

    missing = {
        "msg": 20000,
        "details": {"uuid": "u", "what": "", "why": "OperatorMissing", "details": {}},
    }
    assert service.judge([missing], support="fill_one") == "support_unavailable"
    assert service.judge([missing], support="none") == "not_started"


def test_judge_prefers_the_recognition_marker_over_context():
    service = CopilotResultService()
    callbacks = [
        _extra("BattleFormationOperUnavailable", oper_name="黍"),
        _subtask_start(f"{RESULT_TASK_PREFIX}@ThreeStar"),
    ]
    assert service.judge(callbacks, support="fill_one") == "three_star"


def test_judge_accepts_strings_and_log_entries():
    from maa_api.db.models import LogEntry

    service = CopilotResultService()
    assert service.judge([f"{RESULT_TASK_PREFIX}@TwoStar{HIT_SUFFIX}"]) == "two_star"
    entry = LogEntry(
        source="task",
        level="info",
        content="已达到次数上限：MaaApi@CopilotResult@EndOfAction@Hit 1/1",
        meta={
            "msg": 20003,
            "what": "ExceededLimit",
            "details": {"task": f"{RESULT_TASK_PREFIX}@EndOfAction{HIT_SUFFIX}"},
        },
    )
    assert service.judge([entry]) == "clear"


def test_judge_first_marker_in_sequence_order_wins():
    """识别链只会命中一项；万一日志里出现多个，取先出现的那个。"""
    service = CopilotResultService()
    callbacks = [
        _subtask_start(f"{RESULT_TASK_PREFIX}@Failed"),
        _subtask_start(f"{RESULT_TASK_PREFIX}@ThreeStar"),
    ]
    assert service.judge(callbacks) == "failed"


# ---------------------------------------------------------------------------
# 回调采集：live tap 与日志回读
# ---------------------------------------------------------------------------
def test_live_tap_collects_callbacks_and_stops_when_closed():
    client = _FakeClient()
    service = CopilotResultService()
    with service.tap(client) as tap:
        client.fire(_subtask_start(f"{RESULT_TASK_PREFIX}@ThreeStar"))
        assert len(tap.callbacks) == 1
    client.fire(_subtask_start(f"{RESULT_TASK_PREFIX}@TwoStar"))
    assert len(tap.callbacks) == 1  # 退订后不再收集（内核没有 off，靠失效标记）
    assert service.judge(tap.callbacks) == "three_star"


def test_callbacks_for_reads_the_pipeline_logs(result_session_factory):
    async def seed():
        async with result_session_factory() as session:
            session.add(make_pipeline("pipeline-1"))
            session.add(make_pipeline("pipeline-2"))
            await session.commit()  # 外键要求流水线先落库（Session 不按裸外键排序 INSERT）
            session.add(
                LogEntry(
                    source="task",
                    level="info",
                    content="已达到次数上限：MaaApi@CopilotResult@ThreeStar@Hit 1/1",
                    pipeline_id="pipeline-1",
                    meta=_exceeded(f"{RESULT_TASK_PREFIX}@ThreeStar{HIT_SUFFIX}")["details"],
                )
            )
            session.add(
                LogEntry(
                    source="task",
                    level="info",
                    content="别的流水线",
                    pipeline_id="pipeline-2",
                    meta=_exceeded(f"{RESULT_TASK_PREFIX}@Failed{HIT_SUFFIX}")["details"],
                )
            )
            await session.commit()

    asyncio.run(seed())
    service = CopilotResultService(result_session_factory)

    async def scenario():
        return await service.callbacks_for("pipeline-1")

    callbacks = asyncio.run(scenario())
    assert service.judge(callbacks) == "three_star"


def test_recognize_submits_the_custom_task_and_judges(result_session_factory, tmp_path):
    """跑通「提交 Custom 识别任务 → 读回识别信号 → 判定」这一条链。"""
    submitted: list[dict] = []

    class _FakePipeline:
        def __init__(self) -> None:
            self.waited: list[str] = []

        async def submit(self, payload, **kwargs):
            submitted.append({"payload": payload, "kwargs": kwargs})
            return {"id": f"pipeline-{len(submitted)}"}

        async def wait_terminal(self, pipeline_id, timeout_seconds, **kwargs):
            return {"id": pipeline_id, "status": "completed"}, True

    async def seed():
        async with result_session_factory() as session:
            session.add(make_pipeline("pipeline-1"))
            await session.commit()
            session.add(
                LogEntry(
                    source="task",
                    level="info",
                    content="已达到次数上限：MaaApi@CopilotResult@ThreeStar@Hit 1/1",
                    pipeline_id="pipeline-1",
                    meta=_exceeded(f"{RESULT_TASK_PREFIX}@ThreeStar{HIT_SUFFIX}")["details"],
                )
            )
            await session.commit()

    asyncio.run(seed())
    resource = _FakeResourceService(tmp_path)
    service = CopilotResultService(
        result_session_factory, resource_service=resource
    )
    pipeline = _FakePipeline()

    async def scenario():
        return await service.recognize(pipeline_service=pipeline, timeout_seconds=30)

    report = asyncio.run(scenario())
    assert report["outcome"] == "three_star"
    assert report["judged_by"] == "core"
    assert report["pipeline_id"] == "pipeline-1"
    assert submitted, "必须提交一条 Custom 识别流水线"
    tasks = submitted[0]["payload"].tasks
    assert tasks[0].name == "Custom"
    assert tasks[0].task_names == [RESULT_TASK_ENTRY]
    assert resource.reloads >= 1
