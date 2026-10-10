"""一次作业尝试的执行与计数规则（M29 规格 §9.7）。

用真库（临时 SQLite）+ 假流水线 / 假结果服务：真库让授权的次数、终态与
``meta["attempts"]`` 都是真的，假流水线让「20 分钟超时」「被取消」这类分支几毫秒
就能构造出来。

覆盖规格 §9.7 的表逐行：三条星级、``clear``、``failed``、``sanity_insufficient``、
``not_started``、``unknown``、``error`` / ``interrupted``，以及它们的**计数规则**
（只有 ``sanity_insufficient`` / ``not_started`` / ``error`` / ``interrupted`` 不计次），
外加 ``record_copilot_result`` 只能落在 ``unknown`` 上这一条硬规则。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表
from maa_api.db.models import AgentSession, Pipeline, ResourceAsset, Task, utcnow
from maa_api.db.session import make_engine
from maa_api.domain.enums import ResourceAssetKind
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.copilot.attempt import (
    ATTEMPT_TIMEOUT_SECONDS,
    COUNTED_OUTCOMES,
    UNCOUNTED_OUTCOMES,
    CopilotAttemptService,
)
from maa_api.services.copilot.grant import CopilotGrantService

NOW = datetime(2026, 10, 10, 3, 0, 0)
ASSET_ID = "a" * 32
SESSION_ID = "session-1"
STAGE_ID = "main_01-07"
STAGE_CODE = "1-7"


@pytest.fixture
def attempt_session_factory(tmp_path):
    engine = make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'copilot_attempt.db'}", poolclass=NullPool
    )

    async def create_schema():
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create_schema())
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    engine.sync_engine.dispose()


class _FakePipelineService:
    """记录提交的流水线，并按预设给出终态。"""

    def __init__(self, statuses: list[str] | None = None) -> None:
        self.statuses = list(statuses or [])
        self.submitted: list[dict[str, Any]] = []
        self.waited: list[tuple[str, float]] = []
        self._counter = 0

    async def submit(self, payload, **kwargs):
        self._counter += 1
        pipeline_id = f"pipeline-{self._counter}"
        self.submitted.append(
            {
                "id": pipeline_id,
                "tasks": [task.model_dump(exclude_unset=True) for task in payload.tasks],
                "title": payload.title,
                "kwargs": kwargs,
            }
        )
        return {"id": pipeline_id}

    async def wait_terminal(self, pipeline_id, timeout_seconds, **kwargs):
        self.waited.append((pipeline_id, timeout_seconds))
        index = int(pipeline_id.split("-")[1]) - 1
        status = self.statuses[index] if index < len(self.statuses) else "completed"
        if status == "timeout":
            return {"id": pipeline_id, "status": "running"}, False
        return {"id": pipeline_id, "status": status}, True


class _FakeResultService:
    """结果识别替身：直接给出 outcome，并保留调用参数。"""

    def __init__(self, outcome: str = "three_star", callbacks: list | None = None) -> None:
        self.outcome = outcome
        self.callbacks = list(callbacks or [])
        self.calls: list[dict[str, Any]] = []

    async def recognize(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "outcome": self.outcome,
            "judged_by": None if self.outcome == "unknown" else "core",
            "pipeline_id": "recognition-1",
            "finished": True,
            "callbacks": [],
        }

    async def callbacks_for(self, pipeline_id, *, session=None):
        return list(self.callbacks)


def _seed_asset(factory, *, asset_id: str = ASSET_ID, stage_id: str = STAGE_ID) -> None:
    """一份已保存的作业资源（含 §9.5 的 meta 形状）。

    资源名带 id 后缀：``uq_resource_asset_kind_name`` 不允许两份同名作业（同一份作业的
    版本链靠 ``meta.parent_id``，不是靠重名）。
    """

    async def scenario():
        async with factory() as session:
            if await session.get(ResourceAsset, asset_id) is None:
                session.add(
                    ResourceAsset(
                        id=asset_id,
                        kind=ResourceAssetKind.COPILOT,
                        name=f"1-7 三星 {asset_id[:4]}",
                        content={
                            "stage_name": STAGE_CODE,
                            "minimum_required": "v4.0",
                            "actions": [],
                        },
                        meta={
                            "source": "ai",
                            "stage_id": stage_id,
                            "attempts": [],
                            "retain": False,
                        },
                    )
                )
            if await session.get(AgentSession, SESSION_ID) is None:
                session.add(AgentSession(id=SESSION_ID, model="gpt-test"))
            await session.commit()

    asyncio.run(scenario())


def _grant(factory, **kwargs) -> dict:
    service = CopilotGrantService(factory, now_provider=lambda: NOW)

    async def scenario():
        return await service.approve(SESSION_ID, stage_id=STAGE_ID, stage_code=STAGE_CODE, **kwargs)

    return asyncio.run(scenario())


def _service(factory, *, pipeline, result, **kwargs) -> CopilotAttemptService:
    return CopilotAttemptService(
        factory,
        pipeline_service=pipeline,
        grant_service=CopilotGrantService(factory, now_provider=lambda: NOW),
        result_service=result,
        draft_service=None,
        now_provider=lambda: NOW,
        **kwargs,
    )


def _run(factory, *, pipeline, result, **kwargs) -> dict:
    service = _service(factory, pipeline=pipeline, result=result)
    return asyncio.run(service.run(copilot_id=ASSET_ID, session_id=SESSION_ID, **kwargs))


def _attempts(factory, asset_id: str = ASSET_ID) -> list[dict]:
    from maa_api.db.repositories.copilot import CopilotAttemptRepository

    async def scenario():
        async with factory() as session:
            return await CopilotAttemptRepository(session).list_for_asset(asset_id)

    return asyncio.run(scenario())


def _grant_wire(factory) -> dict | None:
    service = CopilotGrantService(factory, now_provider=lambda: NOW)

    async def scenario():
        return await service.get_active(SESSION_ID)

    return asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 正常路径与三条星级
# ---------------------------------------------------------------------------
def test_run_submits_copilot_recognition_and_exit(attempt_session_factory):
    """执行顺序：作业任务 → 结果识别（Custom）→ 退出结算（规格 §9.7 第 1–3 步）。"""
    _seed_asset(attempt_session_factory)
    _grant(attempt_session_factory, attempts=3)
    pipeline = _FakePipelineService()
    result = _FakeResultService("three_star")

    report = _run(attempt_session_factory, pipeline=pipeline, result=result)

    # 结果识别由 result_service 负责（替身不提交流水线），这里看到的是作业任务 + 退出结算。
    assert [item["tasks"][0]["name"] for item in pipeline.submitted] == [
        "Copilot",
        "Custom",
    ]
    assert pipeline.submitted[1]["tasks"][0]["task_names"] == ["MaaApi@CopilotExit@Start"]
    copilot = pipeline.submitted[0]["tasks"][0]
    assert copilot["asset_id"] == ASSET_ID
    assert copilot["formation"] is True
    assert copilot["support_unit_usage"] == 1  # 授权允许助战
    assert result.calls[0]["support"] == "fill_one"
    assert report["outcome"] == "three_star"
    assert report["judged_by"] == "core"
    assert report["counted"] is True
    assert report["success"] is True
    assert report["attempt_number"] == 1
    assert report["remaining"] == 2
    assert report["grant"]["status"] == "succeeded"
    assert report["pipeline_id"] == "pipeline-1"
    assert report["recognition_pipeline_id"] == "recognition-1"
    assert report["exit_pipeline_id"] == "pipeline-2"


def test_run_records_the_attempt_in_the_asset_meta(attempt_session_factory):
    _seed_asset(attempt_session_factory)
    _grant(attempt_session_factory, attempts=3)
    report = _run(
        attempt_session_factory,
        pipeline=_FakePipelineService(),
        result=_FakeResultService("two_star"),
    )
    rows = _attempts(attempt_session_factory)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == report["attempt_id"]
    assert row["copilot_id"] == ASSET_ID
    assert row["outcome"] == "two_star"
    assert row["judged_by"] == "core"
    assert row["counted"] is True
    assert row["pipeline_id"] == "pipeline-1"
    assert row["support"] == "fill_one"
    assert row["started_at"] and row["finished_at"]


def test_two_star_counts_as_success_only_for_the_clear_criterion(attempt_session_factory):
    _seed_asset(attempt_session_factory)
    _grant(attempt_session_factory, attempts=2, success="clear")
    report = _run(
        attempt_session_factory,
        pipeline=_FakePipelineService(),
        result=_FakeResultService("two_star"),
    )
    assert report["success"] is True
    assert report["grant"]["status"] == "succeeded"

    _seed_asset(attempt_session_factory, asset_id="b" * 32)
    _grant(attempt_session_factory, attempts=2, success="three_star")
    strict = asyncio.run(
        _service(
            attempt_session_factory,
            pipeline=_FakePipelineService(),
            result=_FakeResultService("two_star"),
        ).run(copilot_id="b" * 32, session_id=SESSION_ID)
    )
    assert strict["success"] is False
    assert strict["grant"]["status"] == "active"


def test_failed_counts_and_the_grant_stays_active(attempt_session_factory):
    _seed_asset(attempt_session_factory)
    _grant(attempt_session_factory, attempts=2)
    report = _run(
        attempt_session_factory,
        pipeline=_FakePipelineService(),
        result=_FakeResultService("failed"),
    )
    assert report["counted"] is True
    assert report["success"] is False
    assert report["remaining"] == 1
    assert report["grant"]["status"] == "active"


# ---------------------------------------------------------------------------
# 不计次的四种
# ---------------------------------------------------------------------------
def test_sanity_insufficient_stops_the_grant_and_refunds(attempt_session_factory):
    _seed_asset(attempt_session_factory)
    _grant(attempt_session_factory, attempts=3)
    report = _run(
        attempt_session_factory,
        pipeline=_FakePipelineService(),
        result=_FakeResultService("sanity_insufficient"),
    )
    assert report["outcome"] == "sanity_insufficient"
    assert report["counted"] is False
    assert report["remaining"] == 3  # 退还了这一次
    assert report["grant"]["status"] == "stopped"
    assert "理智" in report["hint"]


def test_not_started_refunds_and_keeps_the_grant(attempt_session_factory):
    _seed_asset(attempt_session_factory)
    _grant(attempt_session_factory, attempts=3)
    report = _run(
        attempt_session_factory,
        pipeline=_FakePipelineService(),
        result=_FakeResultService("not_started"),
    )
    assert report["counted"] is False
    assert report["remaining"] == 3
    assert report["grant"]["status"] == "active"
    assert "开始行动" in report["hint"]


def test_unknown_counts_and_waits_for_the_model(attempt_session_factory):
    _seed_asset(attempt_session_factory)
    _grant(attempt_session_factory, attempts=2)
    report = _run(
        attempt_session_factory,
        pipeline=_FakePipelineService(),
        result=_FakeResultService("unknown"),
    )
    assert report["outcome"] == "unknown"
    assert report["judged_by"] is None
    assert report["counted"] is True
    assert report["remaining"] == 1
    assert report["hint"] and "record_copilot_result" in report["hint"]


def test_timeout_is_an_error_and_does_not_count(attempt_session_factory):
    """阻塞等待最长 20 分钟：超时记 error（规格 §9.7）。"""
    _seed_asset(attempt_session_factory)
    _grant(attempt_session_factory, attempts=2)
    pipeline = _FakePipelineService(["timeout", "completed", "completed"])
    result = _FakeResultService("three_star")
    report = _run(
        attempt_session_factory,
        pipeline=pipeline,
        result=result,
        timeout_seconds=0.5,
    )
    assert report["outcome"] == "error"
    assert report["counted"] is False
    assert report["remaining"] == 2
    assert report["grant"]["status"] == "active"
    assert pipeline.waited[0][1] == 0.5
    assert not result.calls, "超时后不再跑结果识别"
    assert [item["tasks"][0]["name"] for item in pipeline.submitted] == ["Copilot"]


def test_default_timeout_is_twenty_minutes():
    assert ATTEMPT_TIMEOUT_SECONDS == 20 * 60


def test_cancelled_pipeline_is_interrupted_and_stops_the_grant(attempt_session_factory):
    _seed_asset(attempt_session_factory)
    _grant(attempt_session_factory, attempts=2)
    report = _run(
        attempt_session_factory,
        pipeline=_FakePipelineService(["cancelled"]),
        result=_FakeResultService("three_star"),
    )
    assert report["outcome"] == "interrupted"
    assert report["counted"] is False
    assert report["remaining"] == 2
    assert report["grant"]["status"] == "stopped"


def test_failed_pipeline_with_unknown_recognition_is_an_error(attempt_session_factory):
    """流水线出错且识别不出结果：不烧次数（规格 §9.7 的 error 行）。"""
    _seed_asset(attempt_session_factory)
    _grant(attempt_session_factory, attempts=2)
    report = _run(
        attempt_session_factory,
        pipeline=_FakePipelineService(["failed"]),
        result=_FakeResultService("unknown"),
    )
    assert report["outcome"] == "error"
    assert report["counted"] is False
    assert report["remaining"] == 2


def test_failed_pipeline_with_a_definite_result_keeps_the_result(attempt_session_factory):
    """流水线失败但结算看得很清楚（打输了）：按识别结果算，照样计次。"""
    _seed_asset(attempt_session_factory)
    _grant(attempt_session_factory, attempts=2)
    report = _run(
        attempt_session_factory,
        pipeline=_FakePipelineService(["failed"]),
        result=_FakeResultService("failed"),
    )
    assert report["outcome"] == "failed"
    assert report["counted"] is True
    assert report["remaining"] == 1


def test_counted_and_uncounted_outcomes_are_disjoint():
    assert COUNTED_OUTCOMES & UNCOUNTED_OUTCOMES == set()
    assert COUNTED_OUTCOMES | UNCOUNTED_OUTCOMES == {
        "three_star",
        "two_star",
        "clear",
        "failed",
        "support_unavailable",
        "unknown",
        "sanity_insufficient",
        "not_started",
        "error",
        "interrupted",
    }


# ---------------------------------------------------------------------------
# 授权前置与 REST 口径
# ---------------------------------------------------------------------------
def test_run_without_a_grant_asks_for_one_and_submits_nothing(attempt_session_factory):
    _seed_asset(attempt_session_factory)
    pipeline = _FakePipelineService()
    service = _service(
        attempt_session_factory, pipeline=pipeline, result=_FakeResultService()
    )

    async def scenario():
        with pytest.raises(AppError) as info:
            await service.run(copilot_id=ASSET_ID, session_id=SESSION_ID)
        return info.value

    error = asyncio.run(scenario())
    assert error.code == ErrorCode.CONFIRMATION_REQUIRED
    assert error.details["confirmation_action"] == "grant_copilot_attempts"
    assert pipeline.submitted == []
    assert _attempts(attempt_session_factory) == []


def test_run_without_a_session_skips_the_grant(attempt_session_factory):
    """REST 口径：没有内置会话时不消耗授权（规格 §9.6「REST 保持现状」）。"""
    _seed_asset(attempt_session_factory)
    pipeline = _FakePipelineService()
    report = asyncio.run(
        _service(
            attempt_session_factory, pipeline=pipeline, result=_FakeResultService("three_star")
        ).run(copilot_id=ASSET_ID, support="none")
    )
    assert report["grant"] is None
    assert report["remaining"] is None
    assert report["outcome"] == "three_star"
    copilot = pipeline.submitted[0]["tasks"][0]
    assert copilot["support_unit_usage"] == 0  # 显式 none


def test_run_honours_the_explicit_support_override(attempt_session_factory):
    _seed_asset(attempt_session_factory)
    _grant(attempt_session_factory, attempts=2, support="fill_one")
    pipeline = _FakePipelineService()
    report = _run(
        attempt_session_factory,
        pipeline=pipeline,
        result=_FakeResultService("three_star"),
        support="none",
    )
    assert pipeline.submitted[0]["tasks"][0]["support_unit_usage"] == 0
    assert report["support"] == "none"


def test_run_on_a_missing_asset_is_a_resource_error(attempt_session_factory):
    service = _service(
        attempt_session_factory,
        pipeline=_FakePipelineService(),
        result=_FakeResultService(),
    )

    async def scenario():
        with pytest.raises(AppError) as info:
            await service.run(copilot_id="missing", session_id=SESSION_ID)
        return info.value

    assert asyncio.run(scenario()).code == ErrorCode.RESOURCE_ASSET_NOT_FOUND


def test_run_on_another_stage_reports_a_grant_mismatch(attempt_session_factory):
    _seed_asset(attempt_session_factory)
    _grant(attempt_session_factory)
    pipeline = _FakePipelineService()
    service = _service(
        attempt_session_factory, pipeline=pipeline, result=_FakeResultService()
    )

    async def scenario():
        with pytest.raises(AppError) as info:
            await service.run(
                copilot_id=ASSET_ID,
                session_id=SESSION_ID,
                stage_id="main_02-01",
                stage_code="2-1",
            )
        return info.value

    error = asyncio.run(scenario())
    assert error.code == ErrorCode.COPILOT_GRANT_STAGE_MISMATCH
    assert pipeline.submitted == []


# ---------------------------------------------------------------------------
# record_copilot_result
# ---------------------------------------------------------------------------
def _unknown_attempt(factory) -> dict:
    _seed_asset(factory)
    _grant(factory, attempts=2)
    return _run(factory, pipeline=_FakePipelineService(), result=_FakeResultService("unknown"))


def test_record_judges_an_unknown_attempt_with_the_model(attempt_session_factory):
    report = _unknown_attempt(attempt_session_factory)
    service = _service(
        attempt_session_factory,
        pipeline=_FakePipelineService(),
        result=_FakeResultService(),
    )

    async def scenario():
        return await service.record(
            report["attempt_id"], "three_star", "结算界面三颗星，掉落面板已出现"
        )

    recorded = asyncio.run(scenario())
    assert recorded["attempt"]["outcome"] == "three_star"
    assert recorded["attempt"]["judged_by"] == "model"
    assert recorded["attempt"]["evidence"].startswith("结算界面三颗星")
    assert recorded["counted"] is True
    assert recorded["success"] is True
    assert recorded["grant"]["status"] == "succeeded"
    # 计数不重复：已经计过一次（unknown），模型判定不再加一次。
    assert recorded["grant"]["used"] == 1


def test_record_is_rejected_on_a_decided_attempt(attempt_session_factory):
    _seed_asset(attempt_session_factory)
    _grant(attempt_session_factory, attempts=2)
    report = _run(
        attempt_session_factory,
        pipeline=_FakePipelineService(),
        result=_FakeResultService("three_star"),
    )
    service = _service(
        attempt_session_factory,
        pipeline=_FakePipelineService(),
        result=_FakeResultService(),
    )

    async def scenario():
        with pytest.raises(AppError) as info:
            await service.record(report["attempt_id"], "failed", "看错了")
        return info.value

    error = asyncio.run(scenario())
    assert error.code == ErrorCode.INVALID_PARAMETER
    assert "unknown" in error.message


def test_record_rejects_unknown_attempts_and_bad_arguments(attempt_session_factory):
    _seed_asset(attempt_session_factory)
    service = _service(
        attempt_session_factory,
        pipeline=_FakePipelineService(),
        result=_FakeResultService(),
    )

    async def scenario():
        errors = []
        for args in (
            ("missing-id", "three_star", ""),
            ("missing-id", "sanity_insufficient", ""),
            ("missing-id", "three_star", "x" * 201),
        ):
            with pytest.raises(AppError) as info:
                await service.record(*args)
            errors.append(info.value)
        return errors

    errors = asyncio.run(scenario())
    assert errors[0].code == ErrorCode.INVALID_PARAMETER  # 找不到这次尝试
    assert errors[1].code == ErrorCode.INVALID_PARAMETER  # 模型不能记这些 outcome
    assert errors[2].code == ErrorCode.INVALID_PARAMETER  # evidence 超长


# ---------------------------------------------------------------------------
# 动作日志摘要
# ---------------------------------------------------------------------------
def test_actions_summary_translates_copilot_callbacks(attempt_session_factory):
    _seed_asset(attempt_session_factory)
    _grant(attempt_session_factory, attempts=2)
    callbacks = [
        {
            "msg": 20003,
            "details": {
                "what": "CopilotAction",
                "details": {"action": "Deploy", "target": "凛御银灰", "elapsed_time": 1000},
            },
        },
        {
            "msg": 20003,
            "details": {
                "what": "CopilotAction",
                "details": {"action": "UseSkill", "target": "遥", "elapsed_time": 30000},
            },
        },
        {
            "msg": 20003,
            "details": {
                "what": "CopilotAction",
                "details": {"action": "SwitchSpeed", "target": ""},
            },
        },
    ]
    report = _run(
        attempt_session_factory,
        pipeline=_FakePipelineService(),
        result=_FakeResultService("three_star", callbacks=callbacks),
    )
    assert report["action_count"] == 3
    assert report["actions"] == [
        "[1.0s] 部署 凛御银灰",
        "[30.0s] 开技能 遥",
        "二倍速",
    ]
