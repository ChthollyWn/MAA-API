"""评测结果发布（M99 规格 §4.2 第 9 条、§4.5.3）。

用临时 sqlite 建真实表（``SQLModel.metadata.create_all``），构造真实的
``SuiteResult``/``CaseResult`` 跑发布，不 mock 数据库。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlmodel import SQLModel

from maa_api.agent.evals.graders import GradeResult
from maa_api.agent.evals.publish import PublishError, publish_suite_result
from maa_api.agent.evals.runner import CaseResult, SuiteResult
from maa_api.agent.evals.scenario import Scenario
from maa_api.db.models import EvalCaseResult, EvalRun

RUN_ID = "20261110-081530-capability-replay-3f2a"
STARTED_AT = "2026-11-10T08:15:30Z"
FINISHED_AT = "2026-11-10T08:15:40.500Z"


def _db_url(tmp_path: Path) -> str:
    """建一个带 M99 三表的临时库，返回同步 URL。"""
    url = f"sqlite:///{tmp_path / 'evals.db'}"
    engine = create_engine(url)
    try:
        SQLModel.metadata.create_all(engine)
    finally:
        engine.dispose()
    return url


def _scenario(
    scenario_id: str = "demo",
    *,
    title: str = "演示场景",
    metrics: list[str] | None = None,
    tags: list[str] | None = None,
) -> Scenario:
    return Scenario.model_validate(
        {
            "id": scenario_id,
            "title": title,
            "suite": "capability",
            "agent": "legacy_chat",
            "metrics": ["accuracy"] if metrics is None else metrics,
            "tags": ["legacy"] if tags is None else tags,
            "entry": {"kind": "legacy_session"},
            "turns": [{"user": "你好"}],
            "expect": [{"grader": "text_contains", "any_of": ["你好"]}],
        }
    )


def _case(scenario_id: str = "demo", **overrides: Any) -> CaseResult:
    values: dict[str, Any] = {
        "scenario": scenario_id,
        "suite": "capability",
        "agent": "legacy_chat",
        "attempt": 1,
        "variant": "target",
        "status": "passed",
        "graders": [
            GradeResult(
                grader="text_contains",
                status="passed",
                score=1.0,
                details=["命中"],
            )
        ],
        "duration_ms": 120,
        "model_calls": 2,
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "cached_tokens": 2,
            "reasoning_tokens": 1,
            "cost_micros": 7,
            "currency": "USD",
        },
        "run_ids": ["run-1"],
        "warnings": [],
        "error": None,
        "detail": {"messages": []},
    }
    values.update(overrides)
    return CaseResult(**values)


def _suite(
    cases: list[CaseResult],
    *,
    repeat: int = 1,
    started_at: str = STARTED_AT,
    finished_at: str = FINISHED_AT,
) -> SuiteResult:
    return SuiteResult(
        id=RUN_ID,
        suite="capability",
        mode="replay",
        started_at=started_at,
        finished_at=finished_at,
        git={"commit": "abc1234", "dirty": False},
        model={
            "base_url": "https://llm.invalid/v1",
            "model": "eval-model",
            "judge_model": "eval-model",
        },
        repeat=repeat,
        filters={"scenario": ["demo"]},
        target=None,
        baseline=None,
        cases=list(cases),
    )


def _read_run(
    db_url: str, run_id: str = RUN_ID
) -> tuple[EvalRun | None, list[EvalCaseResult]]:
    engine = create_engine(db_url)
    try:
        with Session(engine) as session:
            run = session.get(EvalRun, run_id)
            cases = list(
                session.scalars(select(EvalCaseResult).order_by(EvalCaseResult.id))
            )
    finally:
        engine.dispose()
    return run, cases


def _counts(db_url: str) -> tuple[int, int]:
    engine = create_engine(db_url)
    try:
        with Session(engine) as session:
            runs = session.scalar(select(func.count()).select_from(EvalRun))
            cases = session.scalar(select(func.count()).select_from(EvalCaseResult))
    finally:
        engine.dispose()
    return int(runs or 0), int(cases or 0)


def test_publish_writes_run_and_cases(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    result = _suite([_case()])
    scenario = _scenario("demo", title="演示场景", metrics=["accuracy"], tags=["legacy"])

    assert publish_suite_result(result, {"demo": scenario}, db_url=db_url) == result.id

    run, cases = _read_run(db_url)
    assert run is not None
    assert run.id == RUN_ID
    assert run.suite == "capability"
    assert run.mode == "replay"
    assert run.duration_ms == 10500
    assert (run.total, run.passed, run.failed, run.error, run.skipped) == (
        1,
        1,
        0,
        0,
        0,
    )
    assert run.pass_rate == 1.0
    assert run.model_calls == 2
    assert run.git == {"commit": "abc1234", "dirty": False}
    assert run.model["model"] == "eval-model"
    assert run.filters == {"scenario": ["demo"]}

    assert len(cases) == 1
    case = cases[0]
    assert case.eval_run_id == result.id
    assert case.scenario == "demo"
    assert case.title == "演示场景"
    assert case.suite == "capability"
    assert case.agent == "legacy_chat"
    assert case.metrics == ["accuracy"]
    assert case.tags == ["legacy"]
    assert case.status == "passed"
    assert case.attempt == 1
    assert case.variant == "target"
    assert case.duration_ms == 120
    assert case.model_calls == 2
    assert case.prompt_tokens == 10
    assert case.completion_tokens == 5
    assert case.cached_tokens == 2
    assert case.reasoning_tokens == 1
    assert case.cost_micros == 7
    assert case.currency == "USD"
    assert case.graders == [
        {
            "grader": "text_contains",
            "status": "passed",
            "score": 1.0,
            "details": ["命中"],
        }
    ]
    assert case.error is None


def test_publish_is_idempotent(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    result = _suite([_case()])
    scenarios = {"demo": _scenario()}

    publish_suite_result(result, scenarios, db_url=db_url)
    publish_suite_result(result, scenarios, db_url=db_url)

    assert _counts(db_url) == (1, 1)
    _, cases = _read_run(db_url)
    assert [case.scenario for case in cases] == ["demo"]


def test_publish_keeps_multiple_attempts_of_the_same_scenario(
    tmp_path: Path,
) -> None:
    """``repeat>1`` 时同一场景的多个 attempt 各写一行，重复发布不合并。"""
    db_url = _db_url(tmp_path)
    result = _suite(
        [_case("demo", attempt=1), _case("demo", attempt=2)],
        repeat=2,
    )
    scenarios = {"demo": _scenario()}

    publish_suite_result(result, scenarios, db_url=db_url)

    assert _counts(db_url) == (1, 2)
    run, cases = _read_run(db_url)
    assert run is not None
    assert run.total == 2
    assert [(case.scenario, case.attempt) for case in cases] == [
        ("demo", 1),
        ("demo", 2),
    ]
    assert len({case.attempt for case in cases}) == 2

    # 同一 result 再发布一次：先删后写，两个 attempt 仍各一行。
    publish_suite_result(result, scenarios, db_url=db_url)
    assert _counts(db_url) == (1, 2)
    _, cases = _read_run(db_url)
    assert [case.attempt for case in cases] == [1, 2]


def test_publish_missing_table_reports_error(tmp_path: Path) -> None:
    result = _suite([_case()])
    db_url = f"sqlite:///{tmp_path / 'missing.db'}"

    with pytest.raises(PublishError) as excinfo:
        publish_suite_result(result, {"demo": _scenario()}, db_url=db_url)

    message = str(excinfo.value)
    assert "评测结果库不可用" in message
    assert "alembic upgrade head" in message


def test_publish_unknown_scenario_keeps_empty_metrics(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    case = _case(
        "deleted-scene",
        title="旧标题",
        metrics=["accuracy"],
        tags=["stale"],
    )
    result = _suite([case])

    publish_suite_result(result, {}, db_url=db_url)

    _, cases = _read_run(db_url)
    row = cases[0]
    assert row.scenario == "deleted-scene"
    assert row.title == "deleted-scene"
    assert row.metrics == []
    assert row.tags == []


def test_publish_aggregates_usage_and_duration(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    target_one = _case(
        "alpha",
        usage={
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "cached_tokens": 2,
            "reasoning_tokens": 1,
            "cost_micros": 7,
            "currency": "USD",
        },
    )
    target_two = _case(
        "beta",
        model_calls=3,
        usage={
            "prompt_tokens": 3,
            "completion_tokens": 4,
            "cost_micros": 2,
            "currency": None,
        },
    )
    baseline = _case(
        "alpha",
        variant="baseline",
        model_calls=100,
        usage={
            "prompt_tokens": 1000,
            "completion_tokens": 1000,
            "cost_micros": 999,
            "currency": "EUR",
        },
    )
    result = _suite([target_one, target_two, baseline])
    scenarios = {"alpha": _scenario("alpha"), "beta": _scenario("beta")}

    publish_suite_result(result, scenarios, db_url=db_url)

    run, cases = _read_run(db_url)
    assert run is not None
    assert run.total == 2
    assert run.duration_ms == 10500
    assert run.model_calls == 5
    assert run.prompt_tokens == 13
    assert run.completion_tokens == 9
    assert run.cached_tokens == 2
    assert run.reasoning_tokens == 1
    assert run.cost_micros == 9
    assert run.currency == "USD"
    # 只发布 target 变体。
    assert [(case.scenario, case.variant) for case in cases] == [
        ("alpha", "target"),
        ("beta", "target"),
    ]
    assert [case.prompt_tokens for case in cases] == [10, 3]


def test_publish_snapshots_tags(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    scenario = _scenario(
        "demo",
        title="场景标题",
        metrics=["accuracy"],
        tags=["safety-attack", "security"],
    )
    case = _case(
        "demo",
        title="用例标题",
        metrics=["safety_rate"],
        tags=["stale-tag"],
    )
    result = _suite([case])

    publish_suite_result(result, {"demo": scenario}, db_url=db_url)

    _, cases = _read_run(db_url)
    row = cases[0]
    assert row.title == "场景标题"
    assert row.metrics == ["accuracy"]
    assert row.tags == ["safety-attack", "security"]


def test_publish_truncates_grader_details(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    long_detail = "长" * 300
    grader = GradeResult(
        grader="text_contains",
        status="passed",
        score=1.0,
        details=[long_detail] + [f"detail-{index}" for index in range(6)],
    )
    result = _suite([_case(graders=[grader])])

    publish_suite_result(result, {"demo": _scenario()}, db_url=db_url)

    _, cases = _read_run(db_url)
    details = cases[0].graders[0]["details"]
    assert len(details) == 5
    assert len(details[0]) == 256
    assert details[0] == long_detail[:256]


def test_publish_unparsable_timestamp_reports_error(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    result = _suite([_case()], started_at="not-a-time")

    with pytest.raises(PublishError) as excinfo:
        publish_suite_result(result, {"demo": _scenario()}, db_url=db_url)

    assert "时间" in str(excinfo.value)
    assert _counts(db_url) == (0, 0)
