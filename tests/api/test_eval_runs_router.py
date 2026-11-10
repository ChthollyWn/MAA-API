"""评测运行列表与详情端点的 REST 契约（M99 规格 §4.3 端点 5、6，§4.5.3、§4.5.5）。

两个只读端点都从请求作用域的会话构造 ``EvalRunRepository`` / ``EvalCaseRepository``；
服务未装配时仍走 ``_insights_service`` 的 503 出口。app 按生产装配的最小集现搭：
只装 insights 路由、统一错误处理器与 ``app.state.insights_service``；表建在
``isolated_db`` 的临时库里，不碰真实 ``resource/maa_api.db``。

覆盖：列表的排序 / 分页 / suite 与 mode 过滤 / 非法分页 400、详情 summary 的
``Z`` 时间与字段、``metric_rates`` 按 run 自身全部 case 现算（幻觉率取失败占比、
skipped/error 不进分母、``metrics=[]`` 的 case 不参与）、cases 按
``(attempt, scenario)`` 升序、超过 500 条截断、grader ``details`` 截断、未知 run
404 ``EVAL_RUN_NOT_FOUND``、服务未装配 503 与鉴权。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表，create_all 才能建出评测两张表
from maa_api.api import deps
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import insights as insights_router
from maa_api.db import session as db_session
from maa_api.db.models import EvalCaseResult, EvalRun
from maa_api.services.insights import InsightsService

#: 固定的 naive UTC 时刻（与库里的口径一致），避免用例依赖当前时间。
T1 = datetime(2026, 11, 10, 8, 0, 0)

#: ``EvalRunSummaryOut`` 的 15 个字段（规格 §4.3）。
SUMMARY_FIELDS = {
    "id",
    "suite",
    "mode",
    "started_at",
    "finished_at",
    "duration_ms",
    "total",
    "passed",
    "failed",
    "error",
    "skipped",
    "pass_rate",
    "model_calls",
    "cost_micros",
    "currency",
}
#: ``EvalRunDetailOut`` 在 summary 之外多出的 3 个字段。
DETAIL_FIELDS = SUMMARY_FIELDS | {"metric_rates", "cases", "cases_truncated"}
#: ``EvalCaseOut`` 的 15 个字段。
CASE_FIELDS = {
    "scenario",
    "title",
    "suite",
    "agent",
    "metrics",
    "tags",
    "status",
    "score",
    "attempt",
    "duration_ms",
    "model_calls",
    "cost_micros",
    "currency",
    "graders",
    "error",
}
#: ``EvalMetricRateOut`` 的 6 个字段。
RATE_FIELDS = {"metric", "passed", "total", "rate", "ci_low", "ci_high"}


def _run(
    run_id: str,
    *,
    suite: str = "capability",
    mode: str = "live",
    finished_minutes: int = 5,
    **overrides: Any,
) -> EvalRun:
    """一条最小可用的评测汇总；``finished_at`` 用固定时刻平移。"""
    fields: dict[str, Any] = {
        "id": run_id,
        "suite": suite,
        "mode": mode,
        "started_at": T1,
        "finished_at": T1 + timedelta(minutes=finished_minutes),
        "duration_ms": 1500,
        "total": 1,
        "passed": 1,
        "failed": 0,
        "error": 0,
        "skipped": 0,
        "pass_rate": 1.0,
        "model_calls": 1,
        "cost_micros": 10,
        "currency": "CNY",
    }
    fields.update(overrides)
    return EvalRun(**fields)


def _case(
    run_id: str,
    scenario: str,
    *,
    attempt: int = 1,
    metrics: tuple[str, ...] = ("accuracy",),
    status: str = "passed",
    tags: tuple[str, ...] = (),
    graders: list[dict[str, Any]] | None = None,
    **overrides: Any,
) -> EvalCaseResult:
    """一条最小可用的评测用例；字段默认值可被 ``overrides`` 覆盖。"""
    fields: dict[str, Any] = {
        "eval_run_id": run_id,
        "scenario": scenario,
        "title": f"title-{scenario}",
        "suite": "capability",
        "agent": "legacy_chat",
        "metrics": list(metrics),
        "tags": list(tags),
        "status": status,
        "score": 1.0 if status == "passed" else 0.0,
        "attempt": attempt,
        "duration_ms": 10,
        "model_calls": 2,
        "cost_micros": 5,
        "currency": "CNY",
        "graders": list(graders or []),
        "error": None,
    }
    fields.update(overrides)
    return EvalCaseResult(**fields)


def _build_app(
    isolated_db,
    make_client,
    *,
    with_service: bool = True,
):
    """建路由与（可选）服务装配，返回已进入上下文的客户端。"""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(insights_router.router)
    if with_service:
        app.state.insights_service = InsightsService(db_session.session_factory)
    client = make_client(app)

    async def create_schema() -> None:
        async with isolated_db.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    client.portal.call(create_schema)
    return client


def _seed(
    client,
    *,
    runs: tuple[EvalRun, ...] = (),
    cases: tuple[EvalCaseResult, ...] = (),
) -> None:
    """在 TestClient 的事件循环里播种真实的 ``eval_run`` / ``eval_case_result`` 行。"""

    async def seed() -> None:
        async with db_session.session_factory() as session:
            session.add_all(list(runs))
            await session.flush()
            session.add_all(list(cases))
            await session.commit()

    client.portal.call(seed)


def _error(response):
    return response.json()["error"]


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------
def test_list_filters_and_pagination(isolated_db, make_client, tmp_settings):
    """默认按 ``finished_at DESC, id DESC``；分页回显；suite/mode 过滤；总数为过滤后总数。"""
    client = _build_app(isolated_db, make_client)
    _seed(
        client,
        runs=(
            _run("run-a", finished_minutes=30),
            _run("run-b", suite="regression", mode="replay", finished_minutes=20),
            _run("run-c", mode="replay", finished_minutes=30),
            _run("run-d", finished_minutes=10),
        ),
    )

    response = client.get("/api/agent/evals/runs")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 4
    assert body["page"] == 1
    assert body["size"] == 20
    assert [item["id"] for item in body["items"]] == [
        "run-c",  # 同一 finished_at 时 id 大的在前
        "run-a",
        "run-b",
        "run-d",
    ]
    summary = body["items"][0]
    assert set(summary) == SUMMARY_FIELDS
    assert summary["mode"] == "replay"
    assert summary["started_at"].endswith("Z")
    assert summary["finished_at"].endswith("Z")
    assert summary["pass_rate"] == 1.0

    page2 = client.get("/api/agent/evals/runs", params={"page": 2, "size": 2})
    assert page2.status_code == 200, page2.text
    assert page2.json()["page"] == 2
    assert page2.json()["size"] == 2
    assert page2.json()["total"] == 4
    assert [item["id"] for item in page2.json()["items"]] == ["run-b", "run-d"]

    capability = client.get(
        "/api/agent/evals/runs", params={"suite": "capability"}
    )
    assert capability.status_code == 200, capability.text
    assert capability.json()["total"] == 3
    assert [item["id"] for item in capability.json()["items"]] == [
        "run-c",
        "run-a",
        "run-d",
    ]

    live = client.get("/api/agent/evals/runs", params={"mode": "live"})
    assert live.status_code == 200, live.text
    assert live.json()["total"] == 2
    assert [item["id"] for item in live.json()["items"]] == ["run-a", "run-d"]

    both = client.get(
        "/api/agent/evals/runs", params={"suite": "capability", "mode": "live"}
    )
    assert both.status_code == 200, both.text
    assert [item["id"] for item in both.json()["items"]] == ["run-a", "run-d"]

    empty = client.get("/api/agent/evals/runs", params={"suite": "nope"})
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"items": [], "total": 0, "page": 1, "size": 20}


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},
        {"page": -1},
        {"size": 0},
        {"size": 51},
        {"page": 0, "size": 10},
    ],
)
def test_list_invalid_pagination_400(
    isolated_db, make_client, tmp_settings, params
):
    """``page`` < 1 或 ``size`` 越出 1–50 → 400 ``INVALID_PAGINATION``。"""
    client = _build_app(isolated_db, make_client)

    response = client.get("/api/agent/evals/runs", params=params)
    assert response.status_code == 400, response.text
    assert _error(response)["code"] == "INVALID_PAGINATION"


def test_list_unknown_mode_is_422(isolated_db, make_client, tmp_settings):
    """``mode`` 由 ``EvalRunMode`` 校验，未知取值是 422。"""
    client = _build_app(isolated_db, make_client)

    response = client.get("/api/agent/evals/runs", params={"mode": "bogus"})
    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "VALIDATION_ERROR"


# ---------------------------------------------------------------------------
# 详情
# ---------------------------------------------------------------------------
def test_detail_metric_rates_and_cases(isolated_db, make_client, tmp_settings):
    """summary + 现算的 metric_rates + 按 (attempt, scenario) 升序的 cases。"""
    client = _build_app(isolated_db, make_client)
    run_id = "20261110-081530-capability-live-3f2a"
    _seed(
        client,
        runs=(
            _run(
                run_id,
                duration_ms=4321,
                total=7,
                passed=3,
                failed=2,
                error=1,
                skipped=1,
                pass_rate=0.6,
                model_calls=9,
                cost_micros=4200,
            ),
        ),
        cases=(
            # 插入顺序故意打乱：返回前必须按 (attempt, scenario) 升序。
            _case(
                run_id,
                "b-second-attempt",
                attempt=2,
                metrics=("accuracy",),
                status="passed",
                tags=("capability",),
            ),
            _case(
                run_id,
                "a-failed",
                metrics=("accuracy", "hallucination_rate"),
                status="failed",
            ),
            _case(run_id, "c-skipped", metrics=("accuracy",), status="skipped"),
            _case(
                run_id,
                "d-error",
                metrics=("hallucination_rate",),
                status="error",
                error="driver crashed",
            ),
            _case(run_id, "e-no-metrics", metrics=(), status="passed"),
            _case(
                run_id,
                "f-safety-passed",
                metrics=("safety_rate",),
                status="passed",
                tags=("safety-attack",),
            ),
            _case(
                run_id,
                "g-safety-failed",
                metrics=("safety_rate",),
                status="failed",
                tags=("safety-control",),
            ),
        ),
    )

    response = client.get(f"/api/agent/evals/runs/{run_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == DETAIL_FIELDS
    assert body["id"] == run_id
    assert body["suite"] == "capability"
    assert body["mode"] == "live"
    assert body["duration_ms"] == 4321
    assert body["total"] == 7
    assert body["passed"] == 3
    assert body["failed"] == 2
    assert body["error"] == 1
    assert body["skipped"] == 1
    assert body["pass_rate"] == 0.6
    assert body["model_calls"] == 9
    assert body["cost_micros"] == 4200
    assert body["currency"] == "CNY"
    assert body["started_at"] == "2026-11-10T08:00:00Z"
    assert body["finished_at"] == "2026-11-10T08:05:00Z"
    assert body["cases_truncated"] is False

    # 指标率按这次运行自身的全部 case 现算：分母只含 passed/failed；
    # 幻觉率取失败占比；metrics=[] 的 case 不参与。顺序照目录（离线指标）。
    assert body["metric_rates"] == [
        {
            "metric": "accuracy",
            "passed": 1,
            "total": 2,
            "rate": 0.5,
            "ci_low": pytest.approx(0.09452865480086614),
            "ci_high": pytest.approx(0.9054713451991339),
        },
        {
            "metric": "hallucination_rate",
            "passed": 0,
            "total": 1,
            "rate": 1.0,
            "ci_low": pytest.approx(0.20654329147389294),
            "ci_high": pytest.approx(1.0),
        },
        {
            "metric": "safety_rate",
            "passed": 1,
            "total": 2,
            "rate": 0.5,
            "ci_low": pytest.approx(0.09452865480086614),
            "ci_high": pytest.approx(0.9054713451991339),
        },
    ]
    assert all(set(rate) == RATE_FIELDS for rate in body["metric_rates"])

    cases = body["cases"]
    assert [case["scenario"] for case in cases] == [
        "a-failed",
        "c-skipped",
        "d-error",
        "e-no-metrics",
        "f-safety-passed",
        "g-safety-failed",
        "b-second-attempt",
    ]
    assert all(set(case) == CASE_FIELDS for case in cases)
    assert cases[0]["status"] == "failed"
    assert cases[0]["metrics"] == ["accuracy", "hallucination_rate"]
    assert cases[0]["tags"] == []
    assert cases[1]["status"] == "skipped"
    assert cases[2]["error"] == "driver crashed"
    assert cases[3]["metrics"] == []
    assert cases[4]["tags"] == ["safety-attack"]
    assert cases[-1]["attempt"] == 2


def test_detail_without_cases(isolated_db, make_client, tmp_settings):
    """没有 case 的运行也正常返回：metric_rates/cases 为空、不截断。"""
    client = _build_app(isolated_db, make_client)
    _seed(
        client,
        runs=(_run("run-empty", total=0, passed=0, pass_rate=0.0),),
    )

    response = client.get("/api/agent/evals/runs/run-empty")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["metric_rates"] == []
    assert body["cases"] == []
    assert body["cases_truncated"] is False


def test_grader_details_truncated(isolated_db, make_client, tmp_settings):
    """grader 的 details 最多 5 条、每条最多 256 字（读侧再兜一层）。"""
    client = _build_app(isolated_db, make_client)
    run_id = "run-graders"
    long_detail = "长" * 300
    _seed(
        client,
        runs=(_run(run_id),),
        cases=(
            _case(
                run_id,
                "scenario-grader",
                graders=[
                    {
                        "grader": "tool_sequence",
                        "status": "passed",
                        "score": 1.0,
                        "details": [long_detail] + [f"detail-{i}" for i in range(6)],
                    }
                ],
            ),
        ),
    )

    response = client.get(f"/api/agent/evals/runs/{run_id}")
    assert response.status_code == 200, response.text
    grader = response.json()["cases"][0]["graders"][0]
    assert grader["grader"] == "tool_sequence"
    assert grader["status"] == "passed"
    assert grader["score"] == 1.0
    assert len(grader["details"]) == 5
    assert grader["details"][0] == "长" * 256
    assert all(len(detail) <= 256 for detail in grader["details"])


def test_unknown_run_404(isolated_db, make_client, tmp_settings):
    """评测 id 不存在时报 404 ``EVAL_RUN_NOT_FOUND``，``details`` 带原 id。"""
    client = _build_app(isolated_db, make_client)

    response = client.get("/api/agent/evals/runs/nope")
    assert response.status_code == 404, response.text
    error = _error(response)
    assert error["code"] == "EVAL_RUN_NOT_FOUND"
    assert error["details"]["run_id"] == "nope"


# ---------------------------------------------------------------------------
# cases 截断
# ---------------------------------------------------------------------------
def test_cases_truncated_at_500(isolated_db, make_client, tmp_settings):
    """501 条 case 只返回排序后的前 500 条，``cases_truncated=true``。

    插入顺序与 ``(attempt, scenario)`` 相反：若先截断再排序，首条会是
    ``scenario-001`` 而不是 ``scenario-000``。指标率仍按全部 501 条现算。
    """
    client = _build_app(isolated_db, make_client)
    run_id = "run-many-cases"
    total = 501
    _seed(
        client,
        runs=(_run(run_id, total=total, passed=total, pass_rate=1.0),),
        cases=tuple(
            _case(run_id, f"scenario-{index:03d}", status="passed")
            for index in reversed(range(total))
        ),
    )

    response = client.get(f"/api/agent/evals/runs/{run_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cases_truncated"] is True
    assert len(body["cases"]) == 500
    assert body["cases"][0]["scenario"] == "scenario-000"
    assert body["cases"][-1]["scenario"] == "scenario-499"
    assert body["metric_rates"] == [
        {
            "metric": "accuracy",
            "passed": total,
            "total": total,
            "rate": 1.0,
            "ci_low": pytest.approx(0.9923904844608684),
            "ci_high": pytest.approx(1.0),
        }
    ]


# ---------------------------------------------------------------------------
# 鉴权与服务装配
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tmp_settings", ["eval-runs-secret"], indirect=True)
def test_requires_auth(isolated_db, make_client, tmp_settings):
    """两个端点未带 token 都是 401 ``UNAUTHORIZED``；带上就 200。"""
    client = _build_app(isolated_db, make_client)
    run_id = "run-auth"
    _seed(client, runs=(_run(run_id),), cases=(_case(run_id, "scenario-auth"),))
    deps.reset_rate_limiter()
    try:
        for path in (
            "/api/agent/evals/runs",
            f"/api/agent/evals/runs/{run_id}",
        ):
            response = client.get(path)
            assert response.status_code == 401, path
            assert _error(response)["code"] == "UNAUTHORIZED"

            response = client.get(path, headers={"X-Token": "eval-runs-secret"})
            assert response.status_code == 200, f"{path}: {response.text}"
    finally:
        deps.reset_rate_limiter()


def test_service_unavailable_503(isolated_db, make_client, tmp_settings):
    """服务尚未装配（只装路由）时两个端点都是 503 ``SERVICE_UNAVAILABLE``。"""
    client = _build_app(isolated_db, make_client, with_service=False)

    for path in ("/api/agent/evals/runs", "/api/agent/evals/runs/whatever"):
        response = client.get(path)
        assert response.status_code == 503, path
        assert _error(response)["code"] == "SERVICE_UNAVAILABLE"
