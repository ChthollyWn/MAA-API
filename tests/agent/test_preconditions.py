"""M32 任务前提护栏（规格 §4.1、§4.5）：判定、工具层提醒与策略层确认理由。

护栏只提醒、不拦：``submit_pipeline`` 照常提交，只是结果里多一条可选的
``warnings``；``wait_pipeline`` 在 ``CORE_COMMAND_FAILED`` 失败时多一条可选的
``hint``；策略层只在**本来就要弹确认卡片**时把同一条警告追加到 ``reasons`` 末尾。

本文件不引入 pytest-asyncio（仓库没有这个依赖）：异步路径一律写成同步测试函数
加 ``asyncio.run(...)``，与 ``tests/agent/test_pipeline_tools.py``
（``ToolContext`` + ``registry.execute``）、``tests/agent/test_policy_account_write.py``
（``definition.params_model`` + ``PolicyEngine.evaluate``）保持同一套 harness。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from sqlmodel import SQLModel

from maa_api.agent.policy import PolicyEngine
from maa_api.agent.preconditions import (
    FAILURE_HINT,
    GAME_RUNNING_TASK_TYPES,
    MISSING_START_UP_WARNING,
    failure_hint,
    missing_start_up_warning,
    needs_running_game,
    task_type_names,
)
from maa_api.agent.registry import ToolContext
from maa_api.agent.tools import build_registry
from maa_api.db.models import Pipeline, utcnow
from maa_api.domain.enums import (
    CallerType,
    PipelineSource,
    PipelineStatus,
    Priority,
    RiskLevel,
)
from maa_api.domain.errors import ErrorCode
from maa_api.domain.task import (
    TASK_MODELS,
    FightInput,
    DepotInput,
    StartUpInput,
)

def _context(*, state: object | None = None, session_id: str | None = "session-1") -> ToolContext:
    return ToolContext(
        caller=CallerType.INTERNAL,
        session_id=session_id,
        request_id="preconditions-test",
        request=SimpleNamespace(app=SimpleNamespace(state=state or SimpleNamespace())),
        db_session=None,
    )


def _pipeline(**overrides: Any) -> Pipeline:
    values: dict[str, Any] = {
        "id": "pipeline-1",
        "source": PipelineSource.AGENT,
        "priority": Priority.AGENT,
        "status": PipelineStatus.PENDING,
        "task_count": 1,
        "title": "前提护栏测试",
        "created_at": utcnow(),
    }
    values.update(overrides)
    return Pipeline(**values)


def _terminal(**overrides: Any) -> SimpleNamespace:
    """``wait_pipeline`` 拿到的终态行：只带 ``_pipeline_wire`` 真正读的字段。"""
    values: dict[str, Any] = {
        "id": "pipeline-1",
        "core_id": "default",
        "source": PipelineSource.AGENT,
        "priority": Priority.AGENT,
        "status": PipelineStatus.FAILED,
        "title": "前提护栏测试",
        "task_count": 1,
        "notify_on_finish": False,
        "schedule_id": None,
        "agent_session_id": "session-1",
        "retry_of_id": None,
        "error_code": None,
        "error_message": None,
        "created_at": utcnow(),
        "started_at": None,
        "finished_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


async def _submit(arguments: dict[str, Any]) -> tuple[dict[str, Any], list[Any]]:
    """走真实注册表提交，返回（工具结果，Queue 收到的载荷列表）。"""
    row = _pipeline()
    received: list[Any] = []

    class Queue:
        async def submit(self, payload: Any, **_kwargs: Any) -> tuple[Any, bool]:
            received.append(payload)
            return row, False

    class Runner:
        async def publish_pipeline(self, _pipeline_id: str) -> None:
            return None

        async def publish_queue_changed(self) -> None:
            return None

    result = await build_registry().execute(
        "submit_pipeline",
        arguments,
        _context(
            state=SimpleNamespace(queue_service=Queue(), pipeline_runner=Runner())
        ),
    )
    return result, received


async def _wait(row: Any) -> dict[str, Any]:
    class Runner:
        async def wait_terminal(
            self, pipeline_id: str, _timeout_seconds: int, **_kwargs: Any
        ) -> tuple[Any, bool]:
            assert pipeline_id == "pipeline-1"
            return row, True

    return await build_registry().execute(
        "wait_pipeline",
        {"pipeline_id": "pipeline-1"},
        _context(state=SimpleNamespace(pipeline_runner=Runner())),
    )


async def _decide(arguments: dict[str, Any]) -> Any:
    """生产路径：参数先过注册表里的模型，再交给策略引擎。"""
    definition = build_registry().get("submit_pipeline")
    params = definition.params_model.model_validate(arguments)
    return await PolicyEngine().evaluate(definition, params, _context())


async def _decide_raw(arguments: Any) -> Any:
    """把手写的 JSON 值直接交给策略引擎：真实链路里 arguments 也可能是 dict。"""
    definition = build_registry().get("submit_pipeline")
    return await PolicyEngine().evaluate(definition, arguments, _context())


# --------------------------------------------------------------------------- #
# 判定纯函数（规格 §4.1）
# --------------------------------------------------------------------------- #


def test_game_running_task_types_are_derived_from_task_models() -> None:
    assert GAME_RUNNING_TASK_TYPES == frozenset(TASK_MODELS) - {"StartUp", "CloseDown"}
    assert {"Fight", "Roguelike", "Custom", "Copilot", "Depot"} <= (
        GAME_RUNNING_TASK_TYPES
    )


@pytest.mark.parametrize("name", sorted(GAME_RUNNING_TASK_TYPES))
def test_every_game_running_task_type_warns_without_start_up(name: str) -> None:
    tasks = [{"name": name}]

    assert needs_running_game(tasks) is True
    assert missing_start_up_warning(tasks) == MISSING_START_UP_WARNING


def test_start_up_satisfies_the_running_game_precondition() -> None:
    tasks = [{"name": "StartUp", "start_game_enabled": True}, {"name": "Depot"}]

    assert needs_running_game(tasks) is True
    assert missing_start_up_warning(tasks) is None


def test_close_down_alone_needs_no_running_game() -> None:
    for tasks in ([{"name": "CloseDown"}], [{"name": "StartUp"}]):
        assert needs_running_game(tasks) is False
        assert missing_start_up_warning(tasks) is None


def test_empty_or_unrecognizable_entries_do_not_warn() -> None:
    for tasks in (
        [],
        None,
        [{}],
        [{"name": None}],
        [{"nope": "Fight"}],
        ["Depot", 42],
    ):
        assert needs_running_game(tasks) is False
        assert missing_start_up_warning(tasks) is None


def test_name_matching_ignores_case_and_surrounding_whitespace() -> None:
    assert missing_start_up_warning([{"name": " depot "}]) == MISSING_START_UP_WARNING
    assert missing_start_up_warning([{"name": "DEPOT"}]) == MISSING_START_UP_WARNING
    assert missing_start_up_warning([{"name": " startup "}, {"name": "fight"}]) is None


def test_custom_copilot_and_roguelike_also_warn() -> None:
    for name in ("Custom", "Copilot", "Roguelike"):
        assert missing_start_up_warning([{"name": name}]) == MISSING_START_UP_WARNING


def test_task_type_names_reads_dicts_and_models_and_skips_the_rest() -> None:
    tasks = [
        {"name": " Fight "},
        StartUpInput(),
        DepotInput(),
        {"name": None},
        {"nope": "Fight"},
        "Depot",
        None,
    ]

    assert task_type_names(tasks) == ["Fight", "StartUp", "Depot"]


def test_sqlmodel_rows_are_read_through_the_same_name_attribute() -> None:
    class DepotRow(SQLModel):
        name: str = "Depot"

    assert task_type_names([DepotRow()]) == ["Depot"]
    assert missing_start_up_warning([DepotRow()]) == MISSING_START_UP_WARNING


def test_task_models_and_plain_dicts_are_equivalent_inputs() -> None:
    assert missing_start_up_warning([DepotInput()]) == MISSING_START_UP_WARNING
    assert missing_start_up_warning([FightInput(medicine=2)]) == MISSING_START_UP_WARNING
    assert missing_start_up_warning([StartUpInput(), DepotInput()]) is None


def test_failure_hint_only_matches_core_command_failed() -> None:
    assert failure_hint("CORE_COMMAND_FAILED") == FAILURE_HINT
    assert failure_hint(ErrorCode.CORE_COMMAND_FAILED) == FAILURE_HINT
    for code in (None, "", "CORE_CRASHED", "PIPELINE_NOT_FOUND", "core_command_failed"):
        assert failure_hint(code) is None


# --------------------------------------------------------------------------- #
# 工具层（规格 §4.5：提醒不拦，未命中不出现可选键）
# --------------------------------------------------------------------------- #


def test_submit_pipeline_warns_but_still_submits() -> None:
    result, received = asyncio.run(_submit({"tasks": [{"name": "Depot"}]}))

    assert result["warnings"] == [MISSING_START_UP_WARNING]
    assert set(result) == {
        "pipeline_id",
        "status",
        "created_at",
        "reused",
        "warnings",
    }
    assert result["pipeline_id"] == "pipeline-1"
    assert result["reused"] is False
    # 提醒不拦：任务原样进入流水线服务，没有被改写或补一条 StartUp。
    assert [task.name for task in received[0].tasks] == ["Depot"]


def test_submit_pipeline_with_start_up_has_no_warnings_key() -> None:
    arguments = {
        "tasks": [
            {"name": "StartUp", "start_game_enabled": True},
            {"name": "Depot"},
        ]
    }

    result, _received = asyncio.run(_submit(arguments))

    assert "warnings" not in result
    assert set(result) == {"pipeline_id", "status", "created_at", "reused"}


def test_submit_pipeline_description_states_the_precondition() -> None:
    description = build_registry().get("submit_pipeline").description

    assert "StartUp" in description
    assert "主界面" in description


def test_wait_pipeline_hints_after_the_core_command_failure() -> None:
    row = _terminal(
        status=PipelineStatus.FAILED,
        error_code=ErrorCode.CORE_COMMAND_FAILED,
        error_message="内核报告任务失败",
    )

    result = asyncio.run(_wait(row))

    assert result["hint"] == FAILURE_HINT
    assert result["pipeline"]["error"]["code"] == "CORE_COMMAND_FAILED"
    assert set(result) == {"finished", "waited_seconds", "pipeline", "hint"}


def test_wait_pipeline_success_or_other_errors_have_no_hint_key() -> None:
    completed = _terminal(status=PipelineStatus.COMPLETED)
    crashed = _terminal(
        status=PipelineStatus.FAILED,
        error_code=ErrorCode.CORE_CRASHED,
        error_message="内核中断",
    )

    for row in (completed, crashed):
        result = asyncio.run(_wait(row))

        assert "hint" not in result
        assert set(result) == {"finished", "waited_seconds", "pipeline"}


def test_wait_pipeline_description_mentions_the_screenshot_check() -> None:
    description = build_registry().get("wait_pipeline").description

    assert "截图" in description
    assert "没启动" in description


# --------------------------------------------------------------------------- #
# 策略层（规格 §4.5：只在确实要确认时追加理由）
# --------------------------------------------------------------------------- #


def test_policy_appends_the_warning_to_the_confirmation_reasons() -> None:
    decision = asyncio.run(_decide({"tasks": [{"name": "Fight", "medicine": 2}]}))

    assert decision.requires_confirmation is True
    assert decision.risk is RiskLevel.CONSUME
    assert decision.confirmation_action == "submit_pipeline"
    assert decision.reasons == ("Fight.medicine=2", MISSING_START_UP_WARNING)


def test_policy_appends_the_warning_for_plain_argument_mappings() -> None:
    decision = asyncio.run(
        _decide_raw({"tasks": [{"name": "Fight", "medicine": 2}]})
    )

    assert decision.requires_confirmation is True
    assert decision.reasons == ("Fight.medicine=2", MISSING_START_UP_WARNING)


def test_policy_keeps_the_card_clean_when_start_up_is_present() -> None:
    arguments = {
        "tasks": [
            {"name": "StartUp", "start_game_enabled": True},
            {"name": "Fight", "medicine": 2},
        ]
    }

    decision = asyncio.run(_decide(arguments))

    assert decision.requires_confirmation is True
    assert decision.reasons == ("Fight.medicine=2",)


def test_policy_does_not_confirm_a_plain_depot_submission() -> None:
    decision = asyncio.run(_decide({"tasks": [{"name": "Depot"}]}))

    # 提醒不拦：没有消耗类参数就不弹确认卡片，警告只留在工具结果里。
    assert decision.requires_confirmation is False
    assert decision.risk is RiskLevel.NONE
    assert decision.reasons == ()
