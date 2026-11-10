"""把一次套件评测结果发布到应用库（M99 规格 §4.2 第 9 条、§4.5.3）。

``scripts/agent_eval.py --publish`` 在写完 ``resource/evals/`` 文件后调用这里：

- 用同步引擎 + ``Session``（``SYNC_URL`` 的注释就是「Alembic 与维护脚本使用」），
  与服务进程的异步会话互不干扰；
- 一个事务内按主键覆盖 ``eval_run``，先删旧 cases 再写新 cases，重复发布同一
  ``result.id`` 不会留旧行；
- 只发布 ``variant == "target"`` 的用例；``metrics``/``tags``/``title`` 取传入
  场景的快照，场景文件已删除时退化为空标签与场景 id；
- 表不存在等写库失败统一抛 :class:`PublishError`，调用方只打印警告，不影响
  评测退出码。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import create_engine, delete
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from maa_api.agent.evals.graders import GradeResult
from maa_api.agent.evals.runner import CaseResult, SuiteResult
from maa_api.agent.evals.scenario import Scenario
from maa_api.db.models import EvalCaseResult, EvalRun
from maa_api.db.session import CONNECT_ARGS, SYNC_URL

__all__ = ["PublishError", "publish_suite_result"]

#: 表不存在时的可读提示；CLI 只把 ``str(exc)`` 打到 stderr。
UNAVAILABLE_MESSAGE = "评测结果库不可用：先启动一次服务或执行 alembic upgrade head"

_USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "cached_tokens",
    "reasoning_tokens",
    "cost_micros",
)
_MAX_GRADER_DETAILS = 5
_MAX_DETAIL_CHARS = 256


class PublishError(RuntimeError):
    """结果写库失败：缺表、时间字段不可解析、事务失败等。"""


def publish_suite_result(
    result: SuiteResult,
    scenarios: Mapping[str, Scenario],
    *,
    db_url: str | None = None,
) -> str:
    """把一次套件结果写入应用库；返回 ``eval_run.id``。幂等：同 id 先清旧 cases 再覆盖。"""
    run = _build_run(result)
    cases = _build_cases(result, scenarios)
    engine = create_engine(db_url or SYNC_URL, connect_args=dict(CONNECT_ARGS))
    try:
        try:
            with Session(engine) as session, session.begin():
                # ``merge`` 按主键决定插入还是覆盖；delete 触发的 autoflush 会先落 run。
                session.merge(run)
                session.execute(
                    delete(EvalCaseResult).where(
                        EvalCaseResult.eval_run_id == result.id
                    )
                )
                session.add_all(cases)
        except SQLAlchemyError as exc:
            raise _wrap_db_error(exc) from exc
    finally:
        engine.dispose()
    return result.id


def _build_run(result: SuiteResult) -> EvalRun:
    """汇总行：``summary()`` 的计数 + target 用例的 usage。"""
    started_at = _parse_time(result.started_at)
    finished_at = _parse_time(result.finished_at)
    target_cases = [case for case in result.cases if case.variant == "target"]
    usage = _sum_usage(target_cases)
    summary = result.summary()
    return EvalRun(
        id=result.id,
        suite=result.suite,
        mode=result.mode,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=_duration_ms(started_at, finished_at),
        total=int(summary["total"]),
        passed=int(summary["passed"]),
        failed=int(summary["failed"]),
        error=int(summary["error"]),
        skipped=int(summary["skipped"]),
        pass_rate=float(summary["pass_rate"]),
        model_calls=sum(case.model_calls for case in target_cases),
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        cached_tokens=usage["cached_tokens"],
        reasoning_tokens=usage["reasoning_tokens"],
        cost_micros=usage["cost_micros"],
        currency=usage["currency"],
        git=result.git,
        model=result.model,
        filters=result.filters,
    )


def _build_cases(
    result: SuiteResult, scenarios: Mapping[str, Scenario]
) -> list[EvalCaseResult]:
    """每个 target 用例一行；场景快照优先于 ``CaseResult`` 上的副本。"""
    return [
        _build_case(result, case, scenarios.get(case.scenario))
        for case in result.cases
        if case.variant == "target"
    ]


def _build_case(
    result: SuiteResult, case: CaseResult, scenario: Scenario | None
) -> EvalCaseResult:
    usage = case.usage or {}
    return EvalCaseResult(
        eval_run_id=result.id,
        scenario=case.scenario,
        title=scenario.title if scenario is not None else case.scenario,
        suite=case.suite,
        agent=case.agent,
        metrics=[str(metric) for metric in scenario.metrics] if scenario else [],
        tags=list(scenario.tags) if scenario is not None else [],
        status=case.status,
        score=None,
        attempt=case.attempt,
        variant=case.variant,
        duration_ms=case.duration_ms,
        model_calls=case.model_calls,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        cached_tokens=int(usage.get("cached_tokens") or 0),
        reasoning_tokens=int(usage.get("reasoning_tokens") or 0),
        cost_micros=int(usage.get("cost_micros") or 0),
        currency=usage.get("currency") or None,
        graders=_grader_payload(case.graders),
        error=case.error,
    )


def _sum_usage(cases: Iterable[CaseResult]) -> dict[str, Any]:
    """token/成本逐列求和；币种取第一个非空值（规格 §4.2 第 9 条）。"""
    totals: dict[str, Any] = {key: 0 for key in _USAGE_KEYS}
    currency: str | None = None
    for case in cases:
        usage = case.usage or {}
        for key in _USAGE_KEYS:
            totals[key] += int(usage.get(key) or 0)
        if currency is None and usage.get("currency"):
            currency = str(usage["currency"])
    totals["currency"] = currency
    return totals


def _grader_payload(graders: Sequence[GradeResult]) -> list[dict[str, Any]]:
    """判分器快照：details 最多 5 条、每条截 256 字（规格 §4.5.3）。"""
    return [
        {
            "grader": item.grader,
            "status": item.status,
            "score": item.score,
            "details": [
                str(detail)[:_MAX_DETAIL_CHARS]
                for detail in list(item.details)[:_MAX_GRADER_DETAILS]
            ],
        }
        for item in graders
    ]


def _parse_time(value: str) -> datetime:
    """解析带 ``Z`` 的 ISO 时间并转成库内统一的 naive UTC。"""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise PublishError(f"评测结果时间字段不可解析：{value!r}") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _duration_ms(started_at: datetime, finished_at: datetime) -> int:
    delta = finished_at - started_at
    milliseconds = (
        delta.days * 86_400_000
        + delta.seconds * 1000
        + delta.microseconds // 1000
    )
    return max(0, milliseconds)


def _wrap_db_error(exc: SQLAlchemyError) -> PublishError:
    if "no such table" in str(exc).lower():
        return PublishError(UNAVAILABLE_MESSAGE)
    return PublishError(f"评测结果写库失败：{exc}")
