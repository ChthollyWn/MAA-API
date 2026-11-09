"""评测用的假世界：把一次场景变成隔离的临时应用（M68 §4.5.4）。

与 ``scripts/agent_smoke.py`` 同一套隔离做法：临时数据库、临时配置、假内核、假外部
HTTP；区别是这里的参数来自场景（world.seed / world.http / world.screen），并且
模型客户端固定接评测传输层。
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

import httpx
from PIL import Image  # noqa: F401 - 保证假截图依赖在导入期可见

from maa_api.agent.evals.runner import EvalApp, EvalEnvironment, EvalOptions, LlmEndpoint
from maa_api.agent.evals.scenario import Scenario
from maa_api.agent.llm.client import OpenAICompatibleClient
from maa_api.db import session as db_session
from maa_api.db.models import (
    AgentSession,
    LogEntry,
    Pipeline,
    ResourceAsset,
    Task,
    utcnow,
)
from maa_api.db.repositories.copilot import CopilotAttemptRepository
from maa_api.domain.enums import (
    AgentSessionKind,
    PipelineStatus,
    ResourceAssetKind,
)
from maa_api.services.copilot.attempt import (
    COUNTED_OUTCOMES,
    RECORDABLE_OUTCOMES,
    _GOAL_HINTS,
)
from maa_api.services.copilot.grant import CopilotGrantService, is_success
from maa_api.services.external import http as external_http
from maa_api.services.game.copilots import TIERS
from maa_api.settings import get_settings
from tests.fakes.fake_asst import FakeAsst
from tests.fakes.fake_core import fake_core_factories

EVAL_TOKEN = "eval-token"
CORE_READY_PID = 11011


def _merge_settings(config: dict[str, Any], overrides: dict[str, Any]) -> None:
    """把点分键展开写进嵌套配置。"""
    for key, value in overrides.items():
        parts = key.split(".")
        target = config
        for part in parts[:-1]:
            child = target.get(part)
            if not isinstance(child, dict):
                child = {}
                target[part] = child
            target = child
        target[parts[-1]] = value


def _build_config(scenario: Scenario, llm: LlmEndpoint, core_path: Path, resource: Path) -> dict[str, Any]:
    config: dict[str, Any] = {
        "app": {
            "access_token": EVAL_TOKEN,
            "maa_core_path": str(core_path),
        },
        "llm": {
            "base_url": llm.base_url,
            "api_key": llm.api_key,
            "model": llm.model,
        },
        "agent": {
            "auto_diagnose_failures": False,
            "confirmation_timeout_seconds": 5,
        },
    }
    del resource
    _merge_settings(config, scenario.settings)
    return config


def _read_fixture(fixtures_dir: Path, name: str) -> bytes:
    path = fixtures_dir / name
    if not path.is_file():
        raise FileNotFoundError(f"评测 fixture 不存在：{path}")
    return path.read_bytes()


class ScriptedCopilotSearch:
    """``search_copilots`` 的假作业站：返回场景里写死的作业，不访问网络。"""

    def __init__(self, copilots: list[dict[str, Any]]) -> None:
        self._copilots = [dict(row) for row in copilots]

    async def search(
        self,
        *,
        stage: str,
        limit: int = 10,
        order: str = "hot",
        only_feasible: bool = True,
        include_content: bool = True,
        compact: bool = False,
    ) -> dict[str, Any]:
        """返回 ``CopilotService.search`` 的紧凑投影；只取前 ``limit`` 条。"""
        rows = [
            {
                "site_id": str(row["id"]),
                "title": str(row.get("title") or row["id"]),
                "uploader": "eval",
                "views": 0,
                "rating_level": None,
                "upload_time": None,
                "stage_name": str(row.get("stage_code") or row["stage_id"]),
                "difficulty": None,
                "feasible": True,
                "tier": "ok",
                "missing_count": 0,
                "missing": [],
                "note": None,
                "link": None,
                "operators": [],
            }
            for row in self._copilots[: max(int(limit), 0)]
        ]
        total = len(self._copilots)
        return {
            "query": {
                "stage": stage,
                "stage_id": None,
                "stage_code": stage,
                "matched_by": "eval",
                "order": order,
                "only_feasible": only_feasible,
                "limit": limit,
                "include_content": include_content,
                "compact": compact,
            },
            "results": rows,
            "total": total,
            "candidates": total,
            "site_total": total,
            "site_page": 1,
            "has_next": False,
            "tiers": {tier: (total if tier == "ok" else 0) for tier in TIERS},
            "note": None,
            "hint": None,
            "sources": [],
            "untrusted": True,
            "unavailable": [],
            "region_note": None,
        }


class ScriptedCopilotAttempt:
    """``run_copilot_attempt`` / ``record_copilot_result`` 的假实现。

    结果按 ``world.copilot_results`` 顺序消费；不提交流水线、不消耗授权（``remaining``
    固定 ``None``）。每条尝试仍写进作业资源的 ``meta["attempts"]``，让
    ``record_copilot_result`` 能按 id 找回。
    """

    def __init__(
        self,
        session_factory: Any,
        copilots: list[dict[str, Any]],
        results: list[str],
    ) -> None:
        self._session_factory = session_factory
        self._copilots = [dict(row) for row in copilots]
        self._results = list(results)
        self._used = 0

    async def run(
        self,
        *,
        copilot_id: str,
        session_id: str | None = None,
        support: str | None = None,
        stage_id: str | None = None,
        stage_code: str | None = None,
        grant_attempts: int | None = None,
        success: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        del grant_attempts, success, timeout_seconds
        if self._used >= len(self._results):
            raise RuntimeError(
                f"copilot_results 用完了：第 {len(self._results) + 1} 次 run_copilot_attempt"
            )
        row = next(
            (item for item in self._copilots if str(item["id"]) == copilot_id), None
        )
        if row is None:
            raise RuntimeError(f"world.copilots 里没有作业 {copilot_id}")
        outcome = str(self._results[self._used])
        self._used += 1

        grant = await CopilotGrantService(self._session_factory).get_active(session_id)
        criterion = str((grant or {}).get("success") or "three_star")
        counted = outcome in COUNTED_OUTCOMES
        attempt_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        resolved_stage_id = stage_id or str(row["stage_id"])
        resolved_stage_code = stage_code or str(
            row.get("stage_code") or row["stage_id"]
        )
        attempt = {
            "id": attempt_id,
            "copilot_id": copilot_id,
            "grant_id": (grant or {}).get("id"),
            "session_id": session_id,
            "pipeline_id": None,
            "recognition_pipeline_id": None,
            "exit_pipeline_id": None,
            "outcome": outcome,
            "judged_by": None if outcome == "unknown" else "core",
            "counted": counted,
            "success": counted and is_success(outcome, criterion),
            "screenshot_id": None,
            "evidence": "",
            "support": support or "none",
            "attempt_number": 0,
            "stage_id": resolved_stage_id,
            "stage_code": resolved_stage_code,
            "pipeline_status": "completed",
            "started_at": now,
            "finished_at": now,
        }
        async with self._session_factory() as session:
            repo = CopilotAttemptRepository(session)
            prior = await repo.list_for_asset(copilot_id)
            attempt["attempt_number"] = 1 + sum(
                1
                for item in prior
                if str(item.get("session_id")) == str(session_id)
            )
            await repo.append(copilot_id, attempt)
            await session.commit()
        return {
            **attempt,
            "attempt_id": attempt_id,
            "remaining": None,
            "grant": grant,
            "grant_status": (grant or {}).get("status"),
            "actions": [],
            "action_count": 0,
            "hint": _GOAL_HINTS.get(outcome, ""),
        }

    async def record(
        self, attempt_id: str, outcome: str, evidence: str = ""
    ) -> dict[str, Any]:
        if outcome not in RECORDABLE_OUTCOMES:
            raise RuntimeError(
                f"只能记录 {' / '.join(RECORDABLE_OUTCOMES)} 之一，收到 {outcome}"
            )
        async with self._session_factory() as session:
            repo = CopilotAttemptRepository(session)
            found = await repo.find(str(attempt_id or ""))
            if found is None:
                raise RuntimeError(f"找不到这次尝试：{attempt_id}")
            if str(found.data.get("outcome")) != "unknown":
                raise RuntimeError(
                    "只能记录 outcome=unknown 的尝试："
                    f"这次已经是 {found.data.get('outcome')}"
                )
            updated = await repo.update(
                found.asset_id,
                attempt_id,
                outcome=str(outcome),
                judged_by="model",
                counted=True,
                evidence=str(evidence or ""),
                finished_at=datetime.now(UTC).isoformat(),
            )
            await session.commit()
        data = dict(updated.data if updated is not None else found.data)
        return {
            "attempt": data,
            "counted": True,
            "success": is_success(str(outcome), "three_star"),
            "grant": None,
            "remaining": None,
            "hint": "",
        }


class FakeFarmingOptimizer:
    """``optimize_farming`` 的假刷图服务（M88 规格 §4.5.9）。

    直接返回夹具里的结果：不联网、不写报告产物，``report_artifact_id`` 保持 null。
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def optimize(
        self,
        request: Any,
        *,
        write_report: bool,
        session_id: str | None,
        ticket_id: str | None,
    ) -> dict[str, Any]:
        del request, write_report, session_id, ticket_id
        return copy.deepcopy(self._payload)


async def _seed_copilots(app: Any, scenario: Scenario) -> None:
    """把 world.copilots 写成 kind=copilot 的 ResourceAsset 行。"""
    async with app.state.session_factory() as session:
        for row in scenario.world.copilots:
            session.add(
                ResourceAsset(
                    id=str(row["id"]),
                    kind=ResourceAssetKind.COPILOT.value,
                    name=str(row.get("title") or row["id"]),
                    content={"actions": []},
                    meta={
                        "stage_id": str(row["stage_id"]),
                        "stage_code": str(row.get("stage_code") or row["stage_id"]),
                        "source": "eval",
                    },
                    enabled=True,
                )
            )
        await session.commit()


def _install_copilot_fakes(app: Any, scenario: Scenario) -> None:
    """按 world 字段替换 app.state.copilot_service / copilot_attempt_service。"""
    world = scenario.world
    if world.copilots:
        app.state.copilot_service = ScriptedCopilotSearch(world.copilots)
    if world.copilot_results:
        app.state.copilot_attempt_service = ScriptedCopilotAttempt(
            app.state.session_factory, world.copilots, world.copilot_results
        )


@contextmanager
def _open_world(
    scenario: Scenario,
    *,
    llm: LlmEndpoint,
    workdir: Path,
    transport: httpx.AsyncBaseTransport,
    repo_root: Path,
    fixtures_dir: Path,
) -> Iterator[EvalApp]:
    del repo_root
    resource_path = workdir / "resource"
    app_root = workdir / "app-root"
    core_path = app_root / "core"
    app_resource = app_root / "resource"
    for path in (resource_path, core_path, app_resource):
        path.mkdir(parents=True, exist_ok=True)
    config = _build_config(scenario, llm, core_path, resource_path)
    config_path = workdir / "config.yaml"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf8")

    import maa_api.main as main_module
    import maa_api.settings as settings_module
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy.pool import NullPool

    db_path = resource_path / "maa_api.db"
    async_url = f"sqlite+aiosqlite:///{db_path}"
    sync_url = f"sqlite:///{db_path}"
    prior_db = (
        db_session.DB_PATH,
        db_session.ASYNC_URL,
        db_session.SYNC_URL,
        db_session.engine,
        db_session.session_factory,
    )
    old_default_config = settings_module.DEFAULT_CONFIG_PATH
    old_main_root = main_module.REPO_ROOT
    old_settings = settings_module.get_settings()
    old_env: dict[str, str | None] = {}
    old_external = external_http._DEFAULT
    engine = None
    client = None
    http_client = None

    def handler(request: httpx.Request) -> httpx.Response:
        for fixture in scenario.world.http:
            if fixture.match in str(request.url):
                body: Any
                if fixture.json_body is not None:
                    body = fixture.json_body
                else:
                    assert fixture.body_file is not None
                    body = _read_fixture(fixtures_dir, fixture.body_file)
                if isinstance(body, (dict, list)):
                    return httpx.Response(fixture.status, json=body)
                return httpx.Response(fixture.status, content=body)
        raise httpx.ConnectError("评测环境不访问外部网络", request=request)

    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_session.DB_PATH = db_path
        db_session.ASYNC_URL = async_url
        db_session.SYNC_URL = sync_url
        engine = db_session.make_engine(async_url, poolclass=NullPool)
        db_session.engine = engine
        db_session.session_factory = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )

        for env_name in settings_module.ENV_OVERRIDES.values():
            old_env[env_name] = os.environ.get(env_name)
            os.environ.pop(env_name, None)
        settings_module.DEFAULT_CONFIG_PATH = config_path
        main_module.REPO_ROOT = app_root
        settings_module.set_settings(settings_module.load_settings(config_path))

        external_http._DEFAULT = external_http.ExternalClient(
            transport=httpx.MockTransport(handler)
        )

        app = main_module.create_app()
        # M88 §4.5.9：在 client 进入前替换刷图服务，评测不碰真实 PRTS/企鹅物流/拆包。
        if scenario.world.farming_optimizer:
            payload = json.loads(
                _read_fixture(fixtures_dir, scenario.world.farming_optimizer)
            )
            app.state.farming_optimizer = FakeFarmingOptimizer(payload)
        for name, value in fake_core_factories(
            script=scenario.world.core_script,
            device_connected=scenario.world.device_connected,
            screen=scenario.world.screen,
        ).items():
            setattr(app.state, name, value)
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()

        http_client = httpx.AsyncClient(transport=transport, timeout=120.0)
        eval_client = OpenAICompatibleClient(
            base_url=llm.base_url, api_key=llm.api_key, http_client=http_client
        )
        app.state.agent_runtime.client_factory = lambda _settings: eval_client

        if scenario.world.copilots:
            client.portal.call(_seed_copilots, app, scenario)
        _install_copilot_fakes(app, scenario)

        # M86 §5 T11：world.nav 换成假导航设备；导航服务由启动装配写入 app.state。
        nav = scenario.world.nav
        if nav is not None:
            from tests.fakes.nav import FakeNavDevice

            nav_service = getattr(app.state, "nav_service", None)
            if nav_service is None:
                raise RuntimeError(
                    "world.nav 需要启动装配提供的 app.state.nav_service，但找不到它"
                )
            device = FakeNavDevice(transitions=dict(nav.transitions), start=nav.start)
            nav_service.device_factory = lambda _context: device

        if scenario.world.seed.pipelines:
            client.portal.call(_seed_world, app, scenario)
        yield EvalApp(
            app=app,
            client=client,
            portal=client.portal,
            token=EVAL_TOKEN,
            workdir=workdir,
        )
    finally:
        if client is not None:
            try:
                if http_client is not None:
                    client.portal.call(http_client.aclose)
            finally:
                try:
                    client.__exit__(None, None, None)
                except Exception:  # noqa: BLE001 - 收尾尽力而为
                    pass
        external_http._DEFAULT = old_external
        settings_module.DEFAULT_CONFIG_PATH = old_default_config
        settings_module.set_settings(old_settings)
        main_module.REPO_ROOT = old_main_root
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        (
            db_session.DB_PATH,
            db_session.ASYNC_URL,
            db_session.SYNC_URL,
            db_session.engine,
            db_session.session_factory,
        ) = prior_db
        if engine is not None:
            engine.sync_engine.dispose()
        FakeAsst.reset_class_state()


async def _seed_world(app: Any, scenario: Scenario) -> None:
    """按场景的世界种子直接写流水线、任务与日志。"""
    seed = scenario.world.seed
    now = utcnow()
    async with app.state.session_factory() as session:
        ensured_sessions: set[str] = set()
        for spec in seed.pipelines:
            # 流水线的 agent_session_id 是外键；YAML 里的固定 id 没有现成会话时先补一条
            # 占位会话（外键开着，不补 seed 会直接失败）。重复 seed 不新增第二行。
            session_id = spec.agent_session_id
            if session_id and session_id not in ensured_sessions:
                ensured_sessions.add(session_id)
                if await session.get(AgentSession, session_id) is None:
                    session.add(
                        AgentSession(
                            id=session_id,
                            title="评测种子会话",
                            kind=AgentSessionKind.WORKER.value,
                            model=get_settings().llm.model,
                            base_url=get_settings().llm.base_url or None,
                        )
                    )
                    await session.flush()
            created_at = now - timedelta(minutes=spec.minutes_ago)
            terminal = spec.status in (
                PipelineStatus.COMPLETED,
                PipelineStatus.FAILED,
                PipelineStatus.CANCELLED,
            )
            pipeline = Pipeline(
                source="manual",
                priority=0,
                status=spec.status,
                title=spec.title,
                task_count=len(spec.tasks),
                error_code=spec.error_code,
                error_message=spec.error_message,
                agent_session_id=session_id,
                created_at=created_at,
                started_at=created_at,
                finished_at=now if terminal else None,
            )
            session.add(pipeline)
            await session.flush()
            task_ids = []
            for index, task_spec in enumerate(spec.tasks):
                task = Task(
                    pipeline_id=pipeline.id,
                    order_index=index,
                    type_name=task_spec.type_name,
                    task_name=task_spec.type_name,
                    params=dict(task_spec.params),
                    raw_params=dict(task_spec.params),
                    status=task_spec.status,
                    error_code=task_spec.error_code,
                    error_message=task_spec.error_message,
                )
                session.add(task)
                await session.flush()
                task_ids.append(task.id)
            for log_spec in spec.logs:
                task_id = task_ids[log_spec.task] if task_ids and log_spec.task < len(task_ids) else None
                session.add(
                    LogEntry(
                        source="maa_task",
                        level=log_spec.level,
                        content=log_spec.content,
                        pipeline_id=pipeline.id,
                        task_id=task_id,
                    )
                )
        await session.commit()


class FakeWorldEnvironment(EvalEnvironment):
    """为每个场景建一个临时应用；测试与脚本共用。"""

    def __init__(self, *, repo_root: Path, fixtures_dir: Path) -> None:
        self.repo_root = repo_root
        self.fixtures_dir = fixtures_dir

    def open(
        self,
        scenario: Scenario,
        *,
        llm: LlmEndpoint,
        workdir: Path,
        transport: httpx.AsyncBaseTransport,
    ) -> Any:
        return _open_world(
            scenario,
            llm=llm,
            workdir=workdir,
            transport=transport,
            repo_root=self.repo_root,
            fixtures_dir=self.fixtures_dir,
        )


__all__ = ["EVAL_TOKEN", "FakeWorldEnvironment"]


def build_default_environment(repo_root: Path) -> FakeWorldEnvironment:
    """按仓库约定路径组一个环境；脚本用。"""
    fixtures = repo_root / "tests" / "evals" / "fixtures"
    return FakeWorldEnvironment(repo_root=repo_root, fixtures_dir=fixtures)


def make_workdir(prefix: str = "eval-") -> Path:
    """供脚本/测试使用的临时目录。"""
    return Path(tempfile.mkdtemp(prefix=prefix))


__all__ = [
    "EVAL_TOKEN",
    "FakeFarmingOptimizer",
    "FakeWorldEnvironment",
    "build_default_environment",
    "make_workdir",
]

# 未被使用但保留：评测选项的默认工作方式由 runner 决定。
_ = (EvalOptions, timezone, asyncio)
