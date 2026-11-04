"""评测运行器：把场景、录制、临时假世界与判分器串成一次可复现的运行（M68 §4.2.6/§4.5.4/§4.5.7）。

运行器只依赖 ``EvalEnvironment`` 协议；真实的假世界在 ``tests/evals/environment.py``，
生产进程里不导入本模块。
"""

from __future__ import annotations

import asyncio
import json
import secrets
import subprocess
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx

from maa_api.agent.evals.cassette import (
    CapturedRequest,
    Cassette,
    CassetteError,
    CassetteHeader,
    CassetteTransport,
    CountingTransport,
    RecordingTransport,
)
from maa_api.agent.evals.graders import (
    CaseObservation,
    GradeResult,
    JudgeContext,
    ObservedToolCall,
    case_status,
    grade_case,
)
from maa_api.agent.evals.scenario import AnswerSpec, Scenario, Suite
from maa_api.agent.legacy_turn import stream_legacy_turn
from maa_api.agent.llm.client import OpenAICompatibleClient
from maa_api.db.models import AgentMessage, AgentRun, AgentSession
from maa_api.db.repositories.agent import AgentSessionRepository
from maa_api.settings import get_settings

EvalMode = Literal["replay", "record", "live"]
RESULT_FORMAT = 1
INDEX_FILE_NAME = "index.jsonl"
EVAL_TOKEN = "eval-token"
EVAL_LLM_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class LlmEndpoint:
    """评测用的模型端点；replay 用一个不会被真正访问的地址。"""

    base_url: str
    api_key: str
    model: str


REPLAY_ENDPOINT = LlmEndpoint("https://llm.invalid/v1", "eval-replay", "eval-model")


@dataclass(frozen=True)
class EvalApp:
    """一次用例运行期间可用的应用句柄。"""

    app: Any
    client: Any
    portal: Any
    token: str
    workdir: Path


class EvalEnvironment(Protocol):
    """把场景世界变成可运行的应用；测试与未来实现共用这个协议。"""

    def open(
        self,
        scenario: Scenario,
        *,
        llm: LlmEndpoint,
        workdir: Path,
        transport: httpx.AsyncBaseTransport,
    ) -> AbstractContextManager[EvalApp]: ...


@dataclass(frozen=True)
class EvalVariant:
    """同一场景的一个变体；``setup`` 在驱动前跑，用来改设置或预置数据。"""

    name: str = "target"
    setup: Callable[[EvalApp], Awaitable[None]] | None = None


@dataclass
class EvalOptions:
    """一次评测运行的选项。"""

    mode: EvalMode
    cassette_dir: Path
    out_dir: Path
    real_llm: LlmEndpoint | None = None
    judge_model: str | None = None
    repeat: int = 1
    strict: bool = False
    keep_temp: bool = False
    variants: Sequence[EvalVariant] = (EvalVariant(),)


@dataclass
class CaseResult:
    """一个场景一次尝试的结果。"""

    scenario: str
    suite: Suite
    agent: str
    attempt: int
    variant: str
    status: Literal["passed", "failed", "error", "skipped"]
    graders: list[GradeResult]
    duration_ms: int
    model_calls: int
    usage: dict[str, Any]
    run_ids: list[str]
    warnings: list[str]
    error: str | None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class SuiteResult:
    """一次套件运行的结果；``to_json`` 是写进 result.json 的形状。"""

    id: str
    suite: str
    mode: EvalMode
    started_at: str
    finished_at: str
    git: dict[str, Any] | None
    model: dict[str, Any]
    repeat: int
    filters: dict[str, Any]
    target: dict[str, Any] | None
    baseline: dict[str, Any] | None
    cases: list[CaseResult]

    def summary(self, variant: str = "target") -> dict[str, Any]:
        """只统计指定变体的用例。"""
        selected = [case for case in self.cases if case.variant == variant]
        total = len(selected)
        passed = sum(1 for case in selected if case.status == "passed")
        skipped = sum(1 for case in selected if case.status == "skipped")
        failed = sum(1 for case in selected if case.status == "failed")
        errored = sum(1 for case in selected if case.status == "error")
        rate = (passed / total) if total else 0.0
        return {
            "variant": variant,
            "total": total,
            "passed": passed,
            "failed": failed,
            "error": errored,
            "skipped": skipped,
            "pass_rate": round(rate, 4),
        }

    def variants(self) -> dict[str, Any]:
        """每个变体名的汇总。"""
        names: list[str] = []
        for case in self.cases:
            if case.variant not in names:
                names.append(case.variant)
        return {name: self.summary(name) for name in names}

    def to_json(self) -> dict[str, Any]:
        return {
            "format": RESULT_FORMAT,
            "id": self.id,
            "suite": self.suite,
            "mode": self.mode,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "git": self.git,
            "model": self.model,
            "repeat": self.repeat,
            "filters": self.filters,
            "target": self.target,
            "baseline": self.baseline,
            "summary": self.summary(),
            "variants": self.variants(),
            "cases": [
                {
                    "scenario": case.scenario,
                    "suite": case.suite,
                    "agent": case.agent,
                    "attempt": case.attempt,
                    "variant": case.variant,
                    "status": case.status,
                    "graders": [
                        {
                            "grader": item.grader,
                            "status": item.status,
                            "score": item.score,
                            "details": list(item.details),
                        }
                        for item in case.graders
                    ],
                    "duration_ms": case.duration_ms,
                    "model_calls": case.model_calls,
                    "usage": case.usage,
                    "run_ids": case.run_ids,
                    "warnings": case.warnings,
                    "error": case.error,
                }
                for case in self.cases
            ],
        }


@dataclass
class CaseRecorder:
    """驱动过程中收集的事件与会话 id。"""

    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    session_ids: list[str] = field(default_factory=list)
    turns: list[str] = field(default_factory=list)


DriveFn = Callable[[EvalApp, Scenario, CaseRecorder], Awaitable[None]]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class _AppRequest:
    """驱动只需 ``request.app``；这是最小替身。"""

    def __init__(self, app: Any) -> None:
        self.app = app


def _parse_event_frames(frames: Sequence[str]) -> list[tuple[str, dict[str, Any]]]:
    """把 SSE 帧解析成（事件名, data）；忽略 keep-alive 与注释。"""
    events: list[tuple[str, dict[str, Any]]] = []
    for frame in frames:
        name: str | None = None
        data_line: str | None = None
        for line in frame.splitlines():
            if line.startswith(":"):
                continue
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data_line = line[len("data: ") :]
        if name is None or data_line is None:
            continue
        try:
            data = json.loads(data_line)
        except json.JSONDecodeError:
            continue
        events.append((name, data if isinstance(data, dict) else {"value": data}))
    return events


def _split_frames(raw: str) -> list[str]:
    return [part.strip() for part in raw.split("\n\n") if part.strip()]


async def drive_legacy_session(
    eval_app: EvalApp, scenario: Scenario, recorder: CaseRecorder
) -> None:
    """用 ``stream_legacy_turn`` 驱动一个 v2 会话，按场景策略处理人机交互。"""
    app = eval_app.app
    runtime = app.state.agent_runtime
    confirmations = app.state.confirmation_service
    questions = app.state.question_service
    request = _AppRequest(app)
    settings = get_settings()

    async with app.state.session_factory() as session:
        stored = await AgentSessionRepository(session).create(
            AgentSession(
                title=f"评测 {scenario.id}",
                model=settings.llm.model,
                base_url=settings.llm.base_url or None,
            )
        )
        await session.commit()
        session_id = stored.id
    recorder.session_ids.append(session_id)

    for turn in scenario.turns:
        recorder.turns.append(turn.user)
        human = turn.human
        interrupted = False
        if not await runtime.acquire_session(session_id):
            raise RuntimeError(f"评测会话没有拿到轮次锁：{session_id}")
        try:
            await runtime.ensure_configured(request, session_id)
            pending = ""
            user_answered = False
            async for chunk in stream_legacy_turn(
                runtime, session_id, turn.user, request
            ):
                pending += chunk
                while "\n\n" in pending:
                    frame, pending = pending.split("\n\n", 1)
                    for name, data in _parse_event_frames([frame]):
                        recorder.events.append((name, data))
                        if name == "confirmation_required":
                            if human.confirmations in ("approve", "reject"):
                                await confirmations.resolve(
                                    str(data.get("confirmation_id")),
                                    approved=human.confirmations == "approve",
                                    resolved_by="eval",
                                    reason="评测脚本的裁决",
                                )
                        elif name == "question_required":
                            question_id = str(data.get("question_id"))
                            spec = human.questions
                            if spec == "skip":
                                await questions.skip(question_id, answered_by="eval")
                            elif isinstance(spec, AnswerSpec):
                                if not user_answered:
                                    user_answered = True
                                    await questions.answer(
                                        question_id,
                                        selected=list(spec.answer),
                                        answered_by="eval",
                                    )
                        interrupt = human.interrupt
                        if (
                            interrupt is not None
                            and not interrupted
                            and name == interrupt.on
                            and data.get("name") == interrupt.tool
                        ):
                            interrupted = True
                            asyncio.create_task(
                                _interrupt_later(runtime, session_id, interrupt.delay_seconds)
                            )
        finally:
            runtime.release_session(session_id)


async def _interrupt_later(runtime: Any, session_id: str, delay_seconds: float) -> None:
    """稍后中断；任务由运行中的事件循环持有，不阻塞读流。"""
    await asyncio.sleep(delay_seconds)
    await runtime.interrupt_turn(session_id)


DRIVERS: dict[str, DriveFn] = {"legacy_session": drive_legacy_session}


async def _run_with_timeout(
    driver: DriveFn,
    eval_app: EvalApp,
    scenario: Scenario,
    recorder: CaseRecorder,
    timeout_seconds: float,
) -> None:
    """跑驱动；超时先中断会话再抛 TimeoutError。"""
    try:
        await asyncio.wait_for(driver(eval_app, scenario, recorder), timeout=timeout_seconds)
    except TimeoutError:
        runtime = eval_app.app.state.agent_runtime
        for session_id in recorder.session_ids:
            try:
                await runtime.interrupt_turn(session_id)
            except Exception:  # noqa: BLE001 - 超时后尽力收尾
                continue
        raise


async def _fetch_rows(
    app: Any, query: str, params: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    """用 text SQL 查临时库，返回字典列表（不依赖 ORM 类名）。"""
    from sqlalchemy import text

    async with app.state.session_factory() as session:
        result = await session.execute(text(query), dict(params or {}))
        return [dict(row) for row in result.mappings().all()]


def _loads(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


async def collect_observation(
    eval_app: EvalApp, recorder: CaseRecorder, requests: Sequence[CapturedRequest]
) -> CaseObservation:
    """从临时库取消息、运行、流水线、确认、提问与 span，拼成判分视图。"""
    app = eval_app.app
    session_ids = list(recorder.session_ids)
    placeholders = {
        f"s{index}": session_id for index, session_id in enumerate(session_ids)
    }
    session_filter = ", ".join(f":{name}" for name in placeholders) or "''"

    message_rows = await _fetch_rows(
        app,
        "SELECT session_id, seq, role, content, tool_calls, tool_call_id, tool_name, "
        "finish_reason FROM agent_message WHERE session_id IN ("
        + session_filter
        + ") ORDER BY session_id, seq",
        placeholders,
    )
    run_rows = await _fetch_rows(
        app,
        "SELECT id, agent_name, kind, status, finish_reason, steps, prompt_tokens, "
        "completion_tokens, cached_tokens, reasoning_tokens, cost_micros, currency, "
        "started_at, finished_at FROM agent_run ORDER BY started_at, id",
    )
    pipeline_rows = await _fetch_rows(
        app,
        "SELECT id, status, title, error_code, error_message FROM pipeline "
        "ORDER BY created_at, id",
    )
    task_rows = await _fetch_rows(
        app,
        "SELECT pipeline_id, order_index, type_name, status, params, raw_params, "
        "error_code FROM task ORDER BY pipeline_id, order_index",
    )
    confirmation_rows = await _fetch_rows(
        app, "SELECT id, status FROM confirmation ORDER BY created_at, id"
    )
    question_rows = await _fetch_rows(
        app, "SELECT id, status FROM agent_question ORDER BY created_at, id"
    )
    span_rows = await _fetch_rows(
        app, "SELECT name, status FROM trace_span ORDER BY started_at, id"
    )

    messages: list[dict[str, Any]] = []
    for row in message_rows:
        messages.append(
            {
                "session_id": row["session_id"],
                "seq": row["seq"],
                "role": row["role"],
                "content": row["content"],
                "tool_calls": _loads(row["tool_calls"]),
                "tool_call_id": row["tool_call_id"],
                "tool_name": row["tool_name"],
                "finish_reason": row["finish_reason"],
            }
        )

    tasks_by_pipeline: dict[str, list[dict[str, Any]]] = {}
    for row in task_rows:
        tasks_by_pipeline.setdefault(str(row["pipeline_id"]), []).append(
            {
                "type_name": row["type_name"],
                "status": row["status"],
                "params": _loads(row["params"]),
                "raw_params": _loads(row["raw_params"]),
                "error_code": row["error_code"],
            }
        )
    pipelines = [
        {
            "id": row["id"],
            "status": row["status"],
            "title": row["title"],
            "error_code": row["error_code"],
            "error_message": row["error_message"],
            "tasks": tasks_by_pipeline.get(str(row["id"]), []),
        }
        for row in pipeline_rows
    ]
    runs = [
        {
            "id": row["id"],
            "agent_name": row["agent_name"],
            "kind": row["kind"],
            "status": row["status"],
            "finish_reason": row["finish_reason"],
            "steps": row["steps"],
            "prompt_tokens": row["prompt_tokens"],
            "completion_tokens": row["completion_tokens"],
            "cached_tokens": row["cached_tokens"],
            "reasoning_tokens": row["reasoning_tokens"],
            "cost_micros": row["cost_micros"],
            "currency": row["currency"],
        }
        for row in run_rows
    ]
    tool_calls = _pair_tool_calls(messages)
    final_text, all_text = _texts(messages)
    agent_requests = [item for item in requests if item.channel == "agent"]
    return CaseObservation(
        turns=list(recorder.turns),
        tool_calls=tool_calls,
        final_text=final_text,
        all_text=all_text,
        messages=messages,
        runs=runs,
        pipelines=pipelines,
        confirmations=[
            {"id": row["id"], "status": row["status"]} for row in confirmation_rows
        ],
        questions=[{"id": row["id"], "status": row["status"]} for row in question_rows],
        spans=[{"name": row["name"], "status": row["status"]} for row in span_rows],
        requests=list(requests),
        events=list(recorder.events),
        model_calls=len(agent_requests),
    )


def _pair_tool_calls(messages: Sequence[Mapping[str, Any]]) -> list[ObservedToolCall]:
    results: dict[str, Any] = {}
    for message in messages:
        if message.get("role") == "tool" and message.get("tool_call_id"):
            results[str(message["tool_call_id"])] = _loads(message.get("content"))
    calls: list[ObservedToolCall] = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            raw_arguments = function.get("arguments")
            arguments = _loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
            call_id = str(call.get("id"))
            result = results.get(call_id)
            error = None
            if isinstance(result, Mapping):
                error = result.get("error")
            status = "error" if error else "ok"
            error_code = None
            if isinstance(error, Mapping):
                error_code = error.get("code")
            calls.append(
                ObservedToolCall(
                    id=call_id,
                    name=str(function.get("name") or call.get("name") or ""),
                    arguments=arguments if isinstance(arguments, dict) else {},
                    status=status,
                    error_code=error_code,
                    result=result,
                )
            )
    return calls


def _texts(messages: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    contents = [
        str(message["content"])
        for message in messages
        if message.get("role") == "assistant" and message.get("content")
    ]
    final_text = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            break
        if message.get("role") == "assistant" and message.get("content"):
            final_text = str(message["content"])
            break
    return final_text, "\n".join(contents)


async def _aggregate_usage(eval_app: EvalApp) -> tuple[dict[str, Any], list[str]]:
    """从临时库的 agent_run 汇总 token、成本与运行 id。"""
    rows = await _fetch_rows(
        eval_app.app,
        "SELECT id, prompt_tokens, completion_tokens, cached_tokens, reasoning_tokens, "
        "cost_micros, currency FROM agent_run ORDER BY started_at, id",
    )
    usage = {
        "prompt_tokens": sum(int(row["prompt_tokens"] or 0) for row in rows),
        "completion_tokens": sum(int(row["completion_tokens"] or 0) for row in rows),
        "cached_tokens": sum(int(row["cached_tokens"] or 0) for row in rows),
        "reasoning_tokens": sum(int(row["reasoning_tokens"] or 0) for row in rows),
        "cost_micros": sum(int(row["cost_micros"] or 0) for row in rows),
        "currency": next(
            (row["currency"] for row in rows if row["currency"]), None
        ),
    }
    return usage, [str(row["id"]) for row in rows]


async def _setup_variant(variant: EvalVariant, eval_app: EvalApp) -> None:
    if variant.setup is not None:
        await variant.setup(eval_app)


class EvalRunner:
    """跑一组场景；环境与选项在构造时固定。"""

    def __init__(self, environment: EvalEnvironment, options: EvalOptions) -> None:
        self.environment = environment
        self.options = options

    def run(
        self,
        scenarios: Sequence[Scenario],
        *,
        suite_label: str,
        filters: Mapping[str, Any],
    ) -> SuiteResult:
        started_at = _now_iso()
        started = time.monotonic()
        out_dir = self.options.out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        run_id = (
            datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            + f"-{suite_label}-{self.options.mode}-"
            + secrets.token_hex(2)
        )
        cases: list[CaseResult] = []
        for scenario in scenarios:
            for attempt in range(1, self.options.repeat + 1):
                for variant in self.options.variants:
                    cases.append(
                        self.run_case(scenario, attempt=attempt, variant=variant)
                    )
        finished_at = _now_iso()
        result = SuiteResult(
            id=run_id,
            suite=suite_label,
            mode=self.options.mode,
            started_at=started_at,
            finished_at=finished_at,
            git=git_info(_repo_root()),
            model=self._model_summary(),
            repeat=self.options.repeat,
            filters=dict(filters),
            target=self._baseline(cases),
            baseline=None,
            cases=cases,
        )
        del started
        return result

    def _model_summary(self) -> dict[str, Any]:
        llm = self.options.real_llm or REPLAY_ENDPOINT
        return {
            "base_url": llm.base_url,
            "model": llm.model,
            "judge_model": self.options.judge_model or llm.model,
        }

    def _baseline(self, cases: Sequence[CaseResult]) -> dict[str, Any] | None:
        del cases
        return None

    def run_case(
        self, scenario: Scenario, *, attempt: int, variant: EvalVariant
    ) -> CaseResult:
        started = time.monotonic()
        options = self.options
        llm = options.real_llm or REPLAY_ENDPOINT
        driver = DRIVERS.get(scenario.entry.kind)
        if driver is None:
            return CaseResult(
                scenario=scenario.id,
                suite=scenario.suite,
                agent=scenario.agent,
                attempt=attempt,
                variant=variant.name,
                status="skipped",
                graders=[],
                duration_ms=0,
                model_calls=0,
                usage={},
                run_ids=[],
                warnings=[],
                error=None,
                detail={"reason": "这个入口还没有实现"},
            )

        cassette_path = options.cassette_dir / scenario.cassette_name()
        cassette: Cassette | None = None
        transport: httpx.AsyncBaseTransport
        if options.mode == "replay":
            if not cassette_path.is_file():
                return self._error_case(
                    scenario, attempt, variant, started, "录制文件不存在：" + str(cassette_path)
                )
            cassette = Cassette.load(cassette_path)
            transport = CassetteTransport(
                cassette, max_calls=scenario.limits.max_model_calls, strict=options.strict
            )
        elif options.mode == "record":
            cassette = Cassette.new(
                CassetteHeader(
                    scenario=scenario.id,
                    source="recorded",
                    recorded_at=_now_iso(),
                    model=llm.model,
                    agent=scenario.agent,
                    notes="评测录制",
                )
            )
            cassette_path.parent.mkdir(parents=True, exist_ok=True)
            transport = RecordingTransport(
                cassette, max_calls=scenario.limits.max_model_calls
            )
        else:
            transport = CountingTransport(max_calls=scenario.limits.max_model_calls)

        judge_transport, judge_factory = self._judge_setup(cassette, scenario)
        judge = JudgeContext(
            client_factory=judge_factory,
            model=(options.judge_model or llm.model) if judge_factory else None,
        )
        recorder = CaseRecorder()
        workdir = Path(tempfile.mkdtemp(prefix=f"eval-{scenario.id}-"))
        warnings: list[str] = []
        try:
            with self.environment.open(
                scenario, llm=llm, workdir=workdir, transport=transport
            ) as eval_app:
                try:
                    eval_app.portal.call(_setup_variant, variant, eval_app)
                    eval_app.portal.call(
                        _run_with_timeout,
                        driver,
                        eval_app,
                        scenario,
                        recorder,
                        scenario.limits.timeout_seconds,
                    )
                except TimeoutError as exc:
                    raise RuntimeError(
                        f"超过 {scenario.limits.timeout_seconds:g} 秒没有结束"
                    ) from exc
                eval_app.portal.call(self._flush_spans, eval_app)
                observation = eval_app.portal.call(
                    collect_observation,
                    eval_app,
                    recorder,
                    getattr(transport, "requests", []),
                )
                graders = eval_app.portal.call(grade_case, scenario, observation, judge)
                usage, run_ids = eval_app.portal.call(_aggregate_usage, eval_app)
                detail = self._detail(scenario, observation)
                if isinstance(transport, CassetteTransport):
                    transport.check_unused()
                    if transport.error is not None:
                        raise transport.error
                    warnings.extend(transport.warnings)
        except CassetteError as exc:
            return self._error_case(
                scenario, attempt, variant, started, f"{exc.code}：{exc}", warnings
            )
        except Exception as exc:  # noqa: BLE001 - 一例失败不中断整套
            return self._error_case(
                scenario, attempt, variant, started, f"{type(exc).__name__}：{exc}", warnings
            )
        finally:
            del judge_transport
            if not options.keep_temp:
                import shutil

                shutil.rmtree(workdir, ignore_errors=True)

        status = case_status(graders)
        duration_ms = int((time.monotonic() - started) * 1000)
        result = CaseResult(
            scenario=scenario.id,
            suite=scenario.suite,
            agent=scenario.agent,
            attempt=attempt,
            variant=variant.name,
            status=status,
            graders=list(graders),
            duration_ms=duration_ms,
            model_calls=observation.model_calls,
            usage=usage,
            run_ids=run_ids,
            warnings=warnings,
            error=None,
            detail=detail,
        )
        if options.mode == "record" and cassette is not None and status == "passed":
            cassette.save(cassette_path)
        return result

    async def _flush_spans(self, eval_app: EvalApp) -> None:
        recorder = getattr(eval_app.app.state, "span_recorder", None)
        if recorder is not None:
            await recorder.flush()

    def _judge_setup(
        self, cassette: Cassette | None, scenario: Scenario
    ) -> tuple[httpx.AsyncBaseTransport | None, Callable[[], Any] | None]:
        """构造评审客户端；replay 没有评审录制时返回 None 让判分跳过。"""
        options = self.options
        llm = options.real_llm or REPLAY_ENDPOINT
        model = options.judge_model or llm.model

        def factory() -> Any:
            judge_transport = self._judge_transport(cassette)
            http_client = httpx.AsyncClient(
                transport=judge_transport, timeout=EVAL_LLM_TIMEOUT_SECONDS
            )
            return OpenAICompatibleClient(
                base_url=llm.base_url, api_key=llm.api_key, http_client=http_client
            )

        if options.mode == "replay":
            if cassette is None or cassette.remaining("judge") == 0:
                return None, None
            return None, factory
        del scenario, model
        return None, factory

    def _judge_transport(self, cassette: Cassette | None) -> httpx.AsyncBaseTransport:
        options = self.options
        if options.mode == "replay":
            assert cassette is not None
            return CassetteTransport(
                cassette,
                max_calls=options.real_llm is not None and 30 or 30,
                channel_override="judge",
            )
        if options.mode == "record":
            assert cassette is not None
            return RecordingTransport(
                cassette, max_calls=30, channel_override="judge"
            )
        return CountingTransport(max_calls=30, channel_override="judge")

    def _detail(self, scenario: Scenario, observation: CaseObservation) -> dict[str, Any]:
        del scenario
        return {
            "messages": observation.messages,
            "tool_calls": [
                {
                    "id": call.id,
                    "name": call.name,
                    "arguments": call.arguments,
                    "status": call.status,
                    "error_code": call.error_code,
                }
                for call in observation.tool_calls
            ],
            "events": [{"event": name, "data": data} for name, data in observation.events],
            "spans": observation.spans,
            "requests": [
                {
                    "channel": request.channel,
                    "chars": request.chars,
                    "tool_count": request.tool_count,
                    "reasoning_messages": request.reasoning_messages,
                }
                for request in observation.requests
            ],
        }

    def _error_case(
        self,
        scenario: Scenario,
        attempt: int,
        variant: EvalVariant,
        started: float,
        message: str,
        warnings: Sequence[str] = (),
    ) -> CaseResult:
        return CaseResult(
            scenario=scenario.id,
            suite=scenario.suite,
            agent=scenario.agent,
            attempt=attempt,
            variant=variant.name,
            status="error",
            graders=[],
            duration_ms=int((time.monotonic() - started) * 1000),
            model_calls=0,
            usage={},
            run_ids=[],
            warnings=list(warnings),
            error=message,
            detail={"error": message},
        )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def git_info(repo_root: Path) -> dict[str, Any] | None:
    """当前提交与是否 dirty；没有 git 时返回 None。"""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return {"commit": commit, "dirty": bool(status.strip())}


def write_result(result: SuiteResult, out_dir: Path) -> Path:
    """写 result.json、每例的 cases/*.json 与 index.jsonl，返回 result.json 路径。"""
    run_dir = out_dir / result.id
    cases_dir = run_dir / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    for case in result.cases:
        suffix = "" if case.variant == "target" else f"-{case.variant}"
        path = cases_dir / f"{case.scenario}-{case.attempt}{suffix}.json"
        payload = {
            "scenario": case.scenario,
            "suite": case.suite,
            "agent": case.agent,
            "attempt": case.attempt,
            "variant": case.variant,
            "status": case.status,
            "graders": [
                {
                    "grader": item.grader,
                    "status": item.status,
                    "score": item.score,
                    "details": item.details,
                }
                for item in case.graders
            ],
            "duration_ms": case.duration_ms,
            "model_calls": case.model_calls,
            "usage": case.usage,
            "run_ids": case.run_ids,
            "warnings": case.warnings,
            "error": case.error,
            "detail": case.detail,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf8",
        )
    result_path = run_dir / "result.json"
    result_path.write_text(
        json.dumps(result.to_json(), ensure_ascii=False, indent=2, default=str),
        encoding="utf8",
    )
    index_path = out_dir / INDEX_FILE_NAME
    entry = {
        "id": result.id,
        "path": result_path.relative_to(out_dir).as_posix(),
        "suite": result.suite,
        "mode": result.mode,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "summary": result.summary(),
        "variants": result.variants(),
        "target": result.target,
        "baseline": result.baseline,
    }
    with index_path.open("a", encoding="utf8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    return result_path


__all__ = [
    "DRIVERS",
    "EVAL_TOKEN",
    "INDEX_FILE_NAME",
    "REPLAY_ENDPOINT",
    "RESULT_FORMAT",
    "CaseRecorder",
    "CaseResult",
    "DriveFn",
    "EvalApp",
    "EvalEnvironment",
    "EvalMode",
    "EvalOptions",
    "EvalRunner",
    "EvalVariant",
    "LlmEndpoint",
    "SuiteResult",
    "collect_observation",
    "drive_legacy_session",
    "git_info",
    "write_result",
]
