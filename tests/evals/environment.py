"""评测用的假世界：把一次场景变成隔离的临时应用（M68 §4.5.4）。

与 ``scripts/agent_smoke.py`` 同一套隔离做法：临时数据库、临时配置、假内核、假外部
HTTP；区别是这里的参数来自场景（world.seed / world.http / world.screen），并且
模型客户端固定接评测传输层。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import httpx
from PIL import Image  # noqa: F401 - 保证假截图依赖在导入期可见

from maa_api.agent.evals.runner import EvalApp, EvalEnvironment, EvalOptions, LlmEndpoint
from maa_api.agent.evals.scenario import Scenario
from maa_api.agent.llm.client import OpenAICompatibleClient
from maa_api.db import session as db_session
from maa_api.db.models import LogEntry, Pipeline, Task, utcnow
from maa_api.domain.enums import PipelineStatus
from maa_api.services.external import http as external_http
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
            "question_timeout_seconds": 5,
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
        for spec in seed.pipelines:
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
    "FakeWorldEnvironment",
    "build_default_environment",
    "make_workdir",
]

# 未被使用但保留：评测选项的默认工作方式由 runner 决定。
_ = (EvalOptions, timezone, asyncio)
