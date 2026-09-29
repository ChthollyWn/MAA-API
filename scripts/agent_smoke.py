#!/usr/bin/env python3
"""Agent 端到端硬验收：REST、安全确认、授权、SSE 与资源回滚。

使用生产 ``create_app()`` 装配与应用 lifespan、真实 REST routers、ToolRegistry、
ConfirmationService、ResourceService 和临时 SQLite。MaaCore、ADB 与外部网络不启动；
测试用生命周期适配器只替代 MaaCore 与设备边界。一个仅在本脚本注册且不进入 OpenAPI 的路由，
用于验证内部原子工具授权；内置 LLM 会话由 Fake LLM 驱动。

成功时最后一行严格为 ``SMOKE OK``；失败返回非零并输出 ``[FAIL]``。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

from maa_api.agent.runtime import INCIDENT_ALLOWED_TOOLS
from maa_api.core.enums import Message
from maa_api.services.pipeline_runner import PipelineRunner
from tests.fakes.fake_asst import FakeAsst, SUCCESS_SCRIPT

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TOKEN = "m11-agent-smoke-token"
TEMP_PREFIX = "maa-api-agent-smoke-"


class SmokeFailure(RuntimeError):
    """An acceptance assertion failed."""


class _SmokeLLM:
    """Small OpenAI-compatible stream for capability probes and a final reply."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]] = []
        self.responses = [
            [
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "smoke-capability-probe",
                                        "type": "function",
                                        "function": {
                                            "name": "capability_probe",
                                            "arguments": "{}",
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            ],
            [
                {
                    "choices": [
                        {
                            "index": 0,
                            # 视觉探查问的是「这张图是什么颜色」，探针图是纯蓝 64×64 PNG；
                            # 运行时按回复里有没有 `blue` 判定模型是否真能读图
                            # （maa_api/agent/runtime.py 的 `_VISION_PROBE_MARKER`）。
                            "delta": {"content": "blue"},
                            "finish_reason": None,
                        }
                    ]
                }
            ],
            [
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "Fake LLM 已完成本轮 smoke 对话。"},
                            "finish_reason": None,
                        }
                    ]
                },
                {
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 6},
                },
            ],
            [
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "作业未找到，已停止执行并保留诊断现场。"},
                            "finish_reason": None,
                        }
                    ]
                },
                {
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 19, "completion_tokens": 10},
                },
            ],
        ]

    async def stream_chat_completion(self, model, messages, tools):
        self.requests.append((model, list(messages), list(tools)))
        if not self.responses:
            raise SmokeFailure("Fake LLM response budget exhausted")
        for chunk in self.responses.pop(0):
            yield chunk


def _snapshot_repo_artifacts() -> dict[str, tuple[int, int]]:
    """Fingerprint repo DB/config artifacts to detect accidental writes."""
    skip = {".git", ".venv", ".pytest_cache", "__pycache__", "node_modules"}
    found: dict[str, tuple[int, int]] = {}
    for directory, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = sorted(name for name in dirnames if name not in skip)
        for name in filenames:
            if not name.endswith((".db", ".db-wal", ".db-shm")) and name != "config.yaml":
                continue
            path = Path(directory) / name
            stat = path.stat()
            found[str(path.relative_to(REPO_ROOT))] = (stat.st_size, stat.st_mtime_ns)
    return found


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


class _SmokeCoreSupervisor:
    """Lifespan adapter that reports READY without loading MaaCore."""

    def __init__(self, _config: Any, *, on_crash: Any, on_state_change: Any) -> None:
        del on_crash
        from maa_api.core.supervisor import CoreState

        self.state = CoreState.READY
        self.generation = 1
        self.pid = 11011
        self._on_state_change = on_state_change

    async def start(self, **_kwargs: Any) -> None:
        self._on_state_change(self.state)

    async def stop(self, **_kwargs: Any) -> None:
        return None

    class _Maintenance:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *_exc: Any) -> None:
            return None

    def acquire_maintenance(self) -> _Maintenance:
        return self._Maintenance()

    async def restart(self) -> None:
        return None


class _SmokeCoreClient:
    def __init__(self, _supervisor: Any, **_kwargs: Any) -> None:
        self.handlers: dict[str, list[Any]] = {}
        self.loop = asyncio.get_running_loop()
        self.asst = FakeAsst(script=SUCCESS_SCRIPT, delay_scale=0)
        self.copilot_call: dict[str, Any] | None = None
        self.copilot_calls: list[dict[str, Any]] = []
        self.asst.set_callback(self._on_asst_callback)

    def on(self, event: str, handler: Any) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def _on_asst_callback(self, message: int, details: bytes, _arg: Any) -> None:
        payload = {"msg": message, "details": json.loads(details)}
        for handler in self.handlers.get("CALLBACK", []):
            self.loop.call_soon_threadsafe(handler, payload)

    def start_consumer(self) -> None:
        return None

    async def _send(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"ret": 0}

    async def append_task(self, type_name: str, params: dict[str, Any]) -> int:
        if type_name == "Copilot":
            filename = Path(params["filename"])
            self.copilot_call = {
                "type_name": type_name,
                "params": dict(params),
                "path": filename,
                "content": json.loads(filename.read_text(encoding="utf-8")),
                "existed_at_append": filename.is_file(),
            }
            self.copilot_calls.append(self.copilot_call)
        task_id = self.asst.append_task(type_name, params)
        if task_id <= 0:
            raise RuntimeError("FakeAsst rejected task")
        return task_id

    async def start(self) -> bool:
        return self.asst.start()

    async def stop(self) -> bool:
        return self.asst.stop()

    def close(self) -> None:
        self.asst.stop()


class _SmokeDeviceManager:
    def __init__(self, _settings: Any, _client: Any, *, broadcast: Any, core_id: str) -> None:
        self.broadcast = broadcast
        self.core_id = core_id
        self.address = "smoke:5555"
        self.common_ports: list[int] = []
        self.reconnect_retry_attempts = 1
        self.clicks: list[tuple[int, int]] = []
        self.closed = False

    async def connect_with_retry(self, **_kwargs: Any) -> bool:
        return False

    async def ensure_available(self, *, timeout: float = 10.0) -> bool:
        del timeout
        return True

    def on_core_connection_event(self, _what: str, _details: dict[str, Any]) -> None:
        return None

    def snapshot(self) -> dict[str, Any]:
        return {"core_id": self.core_id, "state": "disconnected", "address": self.address}

    async def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))

    async def close(self) -> None:
        self.closed = True


class _SmokePipelineRunner(PipelineRunner):
    """Production queue/runner path backed by FakeAsst instead of MaaCore."""

    async def publish_pipeline(self, _pipeline_id: str) -> None:
        return None

    async def publish_queue_changed(self) -> None:
        return None


def _install_internal_invoke_probe(app: Any) -> None:
    """Expose the future INTERNAL tool caller only to this smoke process."""
    from fastapi import Depends, Request, Response, status
    from pydantic import BaseModel, ConfigDict

    from maa_api.agent.registry import ToolContext
    from maa_api.api.deps import get_session, require_auth
    from maa_api.domain.enums import CallerType
    from maa_api.services.log_hub import current_request_id

    class ProbeInvokeRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        arguments: dict[str, Any]
        mode: str = "async"

    async def invoke_internal(
        session_id: str,
        name: str,
        payload: ProbeInvokeRequest,
        request: Request,
        response: Response,
        session: Any = Depends(get_session),
        _authenticated: Any = Depends(require_auth),
    ) -> dict[str, Any]:
        del _authenticated
        service = request.app.state.confirmation_service
        context = ToolContext(
            CallerType.INTERNAL,
            session_id,
            current_request_id.get(),
            request,
            session,
        )
        result = await service.invoke(name, payload.arguments, context, mode=payload.mode)
        if payload.mode == "async" or result.get("status") == "awaiting_confirmation":
            response.status_code = status.HTTP_202_ACCEPTED
        return result

    # With postponed annotations, locally defined request/model types are not
    # resolvable through FastAPI's module-global namespace. Bind their concrete
    # types before the endpoint is registered.
    invoke_internal.__annotations__ = {
        "session_id": str,
        "name": str,
        "payload": ProbeInvokeRequest,
        "request": Request,
        "response": Response,
        "session": Any,
        "_authenticated": Any,
        "return": dict[str, Any],
    }

    app.add_api_route(
        "/__smoke/internal-sessions/{session_id}/tools/{name}/invoke",
        invoke_internal,
        methods=["POST"],
        include_in_schema=False,
    )


def _wait_for_audit(client: Any, audit_id: int, *, timeout: float = 8.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(
            f"/api/agent/audits/{audit_id}", headers={"X-Token": TOKEN}
        )
        _require(response.status_code == 200, f"读取审计 {audit_id} 失败：{response.status_code} {response.text[:240]}")
        last = response.json()
        if last.get("status") != "pending":
            return last
        time.sleep(0.025)
    raise SmokeFailure(f"审计 {audit_id} 未在 {timeout:.0f}s 内结束：{last!r}")


def _check_response(response: Any, status_code: int, label: str) -> dict[str, Any]:
    _require(
        response.status_code == status_code,
        f"{label} 应返回 {status_code}，实际 {response.status_code}: {response.text[:400]}",
    )
    return response.json() if response.content else {}


def _wait_for_pipeline(client: Any, pipeline_id: str, headers: dict[str, str]) -> dict[str, Any]:
    deadline = time.monotonic() + 12.0
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/pipelines/{pipeline_id}", headers=headers)
        _require(response.status_code == 200, f"流水线回查失败：{response.status_code} {response.text[:240]}")
        last = response.json()
        if str(last.get("status", "")).lower() in {"completed", "failed", "cancelled"}:
            return last
        time.sleep(0.1)
    raise SmokeFailure(f"流水线未在 12s 内进入终态：{last!r}")


def _check_builtin_conversation(client: Any, headers: dict[str, str], fake_llm: _SmokeLLM) -> None:
    capability = _check_response(
        client.post("/api/agent/capabilities/check", headers=headers),
        200,
        "内置 Agent 模型能力自检",
    )
    _require(
        capability.get("tool_calling") is True and capability.get("vision") is True,
        f"Fake LLM 能力自检结果不匹配：{capability!r}",
    )
    session = _check_response(
        client.post(
            "/api/agent/sessions",
            json={"title": "M13 built-in Agent smoke"},
            headers=headers,
        ),
        201,
        "内置 Agent 会话创建",
    )
    session_id = session["session_id"]
    response = client.post(
        f"/api/agent/sessions/{session_id}/messages",
        json={"content": "请简短确认本轮 smoke 对话。"},
        headers=headers,
    )
    _require(
        response.status_code == 200
        and response.headers.get("content-type", "").startswith("text/event-stream"),
        f"内置 Agent 消息应返回 SSE：{response.status_code} {response.text[:400]}",
    )
    stream = response.text
    for event_name in ("message_start", "text_delta", "message_end"):
        _require(f"event: {event_name}\n" in stream, f"Agent SSE 缺少 {event_name}")
    # 计划要求验证 SSE 事件顺序，而不只是成员存在：断言三个事件的首次出现位置递增。
    positions = [stream.index(f"event: {name}\n") for name in ("message_start", "text_delta", "message_end")]
    _require(
        positions == sorted(positions) and len(set(positions)) == 3,
        f"Agent SSE 事件顺序不正确：{positions}",
    )
    _require("Fake LLM 已完成本轮 smoke 对话。" in stream, "Agent SSE 未返回 Fake LLM 文本")

    messages = _check_response(
        client.get(
            f"/api/agent/sessions/{session_id}/messages",
            params={"page": 1, "size": 20},
            headers=headers,
        ),
        200,
        "内置 Agent 消息历史",
    )
    _require(
        [item.get("role") for item in messages.get("items", [])]
        == ["user", "assistant"],
        f"Agent 消息历史顺序不正确：{messages!r}",
    )
    stored = _check_response(
        client.get(f"/api/agent/sessions/{session_id}", headers=headers),
        200,
        "内置 Agent token 用量回查",
    )
    _require(
        stored.get("prompt_tokens") == 11
        and stored.get("completion_tokens") == 6
        and len(fake_llm.requests) == 3,
        f"Agent token 用量或 Fake LLM 往返次数错误：{stored!r}",
    )


def _check_copilot_pipeline_and_schedule(client: Any, headers: dict[str, str]) -> None:
    operation = {
        "minimum_required": "v4.0.0",
        "stage_name": "1-7",
        "doc": {"title": "M13 smoke", "details": "safe queue contract"},
        "opers": [{"name": "阿米娅", "skill": 1}],
        "actions": [
            {"type": "Deploy", "name": "阿米娅", "location": [3, 4], "direction": "Left"}
        ],
    }
    uploaded = _check_response(
        client.post(
            "/api/agent/tools/upload_copilot/invoke",
            json={
                "arguments": {"name": "smoke-copilot", "content": operation},
                "mode": "sync",
            },
            headers=headers,
        ),
        200,
        "Copilot 作业验证并上传",
    )
    asset_id = uploaded.get("result", {}).get("id")
    _require(isinstance(asset_id, str) and asset_id, f"上传结果缺少 asset id：{uploaded!r}")
    catalog = _check_response(
        client.get("/api/resources/copilots", headers=headers),
        200,
        "Copilot 作业列表",
    )
    _require(
        any(item.get("id") == asset_id for item in catalog.get("items", [])),
        "已保存 Copilot 未出现在资源列表",
    )

    submitted = _check_response(
        client.post(
            "/api/pipelines",
            json={
                "title": "M13 Copilot smoke",
                "tasks": [{"name": "Copilot", "asset_id": asset_id, "formation": False}],
            },
            headers=headers,
        ),
        202,
        "Copilot 共享流水线入队",
    )
    detail = _wait_for_pipeline(client, submitted["pipeline_id"], headers)
    task = detail.get("tasks", [{}])[0]
    _require(
        str(detail.get("status", "")).lower() == "completed"
        and task.get("status") == "completed"
        and task.get("type_name") == "Copilot"
        and task.get("params", {}).get("asset_id") == asset_id
        and task.get("params", {}).get("formation") is False,
        f"FakeAsst Copilot 执行或 asset_id/formation 不匹配：{detail!r}",
    )
    schedule = _check_response(
        client.post(
            "/api/schedules",
            json={
                "name": "M13 Copilot smoke schedule",
                "cron": "0 7 * * *",
                "timezone": "UTC",
                "enabled": False,
                "template": [
                    {"name": "Copilot", "asset_id": asset_id, "formation": False}
                ],
            },
            headers=headers,
        ),
        201,
        "Copilot schedule 模板持久化",
    )
    _require(
        schedule.get("template", [{}])[0].get("name") == "Copilot"
        and schedule["template"][0].get("asset_id") == asset_id,
        f"schedule 未保存 Copilot asset_id：{schedule!r}",
    )
    scheduled_run = _check_response(
        client.post(
            f"/api/schedules/{schedule['id']}/run",
            json={},
            headers=headers,
        ),
        202,
        "Copilot schedule 队列执行",
    )
    scheduled_pipeline = _wait_for_pipeline(
        client, scheduled_run["pipeline_id"], headers
    )
    _require(
        str(scheduled_pipeline.get("status", "")).lower() == "completed"
        and scheduled_pipeline.get("tasks", [{}])[0].get("status") == "completed",
        f"schedule Copilot 流水线没有成功完成：{scheduled_pipeline!r}",
    )
    core_client = client.app.state.core_client
    _require(
        len(core_client.copilot_calls) == 2
        and all(
            call.get("type_name") == "Copilot"
            and call.get("params", {}).get("formation") is False
            and call.get("path").name == "operation.json"
            and call.get("existed_at_append") is True
            and call.get("content") == operation
            and not call.get("path").exists()
            for call in core_client.copilot_calls
        )
        and core_client.copilot_calls[0]["path"]
        != core_client.copilot_calls[1]["path"],
        f"FakeAsst 未收到流水线/schedule 各自独立的短暂 Copilot 快照：{core_client.copilot_calls!r}",
    )


def _check_failure_incident(client: Any, headers: dict[str, str], fake_llm: _SmokeLLM) -> None:
    failed = _check_response(
        client.post(
            "/api/pipelines",
            json={
                "title": "M13 missing Copilot smoke",
                "tasks": [
                    {"name": "Copilot", "asset_id": "f" * 32, "formation": False}
                ],
            },
            headers=headers,
        ),
        202,
        "缺失 Copilot 故障流水线入队",
    )
    pipeline = _wait_for_pipeline(client, failed["pipeline_id"], headers)
    _require(
        str(pipeline.get("status", "")).lower() == "failed"
        and pipeline.get("error", {}).get("code") == "RESOURCE_ASSET_NOT_FOUND",
        f"缺失 Copilot 未产生稳定失败状态：{pipeline!r}",
    )
    deadline = time.monotonic() + 8.0
    incident_session: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        sessions = _check_response(
            client.get("/api/agent/sessions", params={"page": 1, "size": 50}, headers=headers),
            200,
            "自动诊断会话列表",
        )
        incident_session = next(
            (
                item
                for item in sessions.get("items", [])
                if item.get("incident_key") == f"pipeline:{failed['pipeline_id']}"
            ),
            None,
        )
        if (
            incident_session is not None
            and incident_session.get("message_count", 0) >= 2
            and len(fake_llm.requests) >= 4
        ):
            # 诊断轮次以 assistant 结论结束；Fake LLM 已收到第 4 次请求说明该轮
            # 已跑完。此前这里等的是 message_count >= 3，普通文本回复的会话只有
            # 「user 现场 + assistant 结论」两条，永远达不到，白等满 8 秒。
            break
        time.sleep(0.05)
    _require(incident_session is not None, "流水线失败没有创建自动诊断会话")
    history = _check_response(
        client.get(
            f"/api/agent/sessions/{incident_session['session_id']}/messages",
            params={"page": 1, "size": 20},
            headers=headers,
        ),
        200,
        "自动诊断消息回查",
    )
    initial = json.loads(history.get("items", [{}])[0].get("content") or "{}")
    _require(
        initial.get("failure_object", {}).get("error_code") == "RESOURCE_ASSET_NOT_FOUND",
        f"自动诊断现场未包含稳定错误码：{initial!r}",
    )
    _require(
        len(fake_llm.requests) == 4 and bool(fake_llm.requests[-1][2]),
        "自动诊断没有调用受限安全工具集的 Fake LLM 往返",
    )
    # 断言精确集合而不是「排除三个危险工具」：只排除几个名字的话，click / swipe /
    # restart_core / delete_schedule 之类的工具即使泄漏进诊断 schema 也测不出来。
    incident_tool_names = {
        item["function"]["name"] for item in fake_llm.requests[-1][2]
    }
    _require(
        incident_tool_names == set(INCIDENT_ALLOWED_TOOLS),
        "自动诊断工具集与 INCIDENT_ALLOWED_TOOLS 不一致："
        f"缺少={sorted(set(INCIDENT_ALLOWED_TOOLS) - incident_tool_names)} "
        f"多出={sorted(incident_tool_names - set(INCIDENT_ALLOWED_TOOLS))}",
    )
    print("[ok] 流水线失败自动诊断、稳定错误上下文与安全工具 schema")


def run(*, keep_temp: bool = False) -> None:
    before = _snapshot_repo_artifacts()
    temp_dir = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX)).resolve()
    failure: BaseException | None = None
    client = None

    # All app-owned paths point outside the checkout before the real lifespan starts.
    import maa_api.main as main_module
    import maa_api.settings as settings_module
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy.pool import NullPool

    from maa_api.db import session as db_session
    from maa_api.domain.enums import CallerType
    from maa_api.services import resource_service as resource_service_module

    db_path = temp_dir / "resource" / "maa_api.db"
    config_path = temp_dir / "config.yaml"
    app_root = temp_dir / "app-root"
    core_path = app_root / "core"
    resource_path = app_root / "resource"
    core_path.mkdir(parents=True)
    app_root.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        "app:\n"
        f"  access_token: {TOKEN}\n"
        f"  maa_core_path: {core_path}\n"
        "llm:\n"
        "  base_url: https://llm.invalid/v1\n"
        "  api_key: smoke-only\n"
        "  model: smoke-model\n",
        encoding="utf8",
    )

    prior_db = (db_session.DB_PATH, db_session.ASYNC_URL, db_session.SYNC_URL,
                db_session.engine, db_session.session_factory)
    old_default_config = settings_module.DEFAULT_CONFIG_PATH
    old_main_root = main_module.REPO_ROOT
    old_state: dict[str, Any] = {}
    old_env: dict[str, str | None] = {}
    old_resource_service = resource_service_module.ResourceService
    temp_engine = None

    class SmokeResourceService(old_resource_service):
        """Fail the first resource reload once, then allow rollback reload to recover."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.smoke_reload_calls = 0

            async def reload_resources() -> None:
                self.smoke_reload_calls += 1
                if self.smoke_reload_calls == 1:
                    raise RuntimeError("injected M11 resource reload failure")

            kwargs["reload_resources"] = reload_resources
            super().__init__(*args, **kwargs)

    try:
        # Isolate every import-time database singleton as well as its URLs.
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async_url = f"sqlite+aiosqlite:///{db_path}"
        sync_url = f"sqlite:///{db_path}"
        db_session.DB_PATH = db_path
        db_session.ASYNC_URL = async_url
        db_session.SYNC_URL = sync_url
        temp_engine = db_session.make_engine(async_url, poolclass=NullPool)
        db_session.engine = temp_engine
        db_session.session_factory = async_sessionmaker(
            temp_engine, expire_on_commit=False, class_=AsyncSession
        )

        # Lifespan's configuration reload and all hard-coded app resource paths are isolated.
        for env_name in settings_module.ENV_OVERRIDES.values():
            old_env[env_name] = os.environ.get(env_name)
            os.environ.pop(env_name, None)
        settings_module.DEFAULT_CONFIG_PATH = config_path
        main_module.REPO_ROOT = app_root
        resource_service_module.ResourceService = SmokeResourceService
        settings_module.set_settings(settings_module.load_settings(config_path))

        for name, value in {
            "core_supervisor_factory": _SmokeCoreSupervisor,
            "core_client_factory": _SmokeCoreClient,
            "device_manager_factory": _SmokeDeviceManager,
            "pipeline_runner_factory": _SmokePipelineRunner,
        }.items():
            old_state[name] = getattr(main_module.app.state, name, None)
            setattr(main_module.app.state, name, value)

        _install_internal_invoke_probe(main_module.app)
        client = TestClient(main_module.app, raise_server_exceptions=False)
        client.__enter__()

        # Exercise the production SSE router/runtime with an offline model; no
        # external endpoint or MaaCore is contacted by this smoke path.
        fake_llm = _SmokeLLM()
        main_module.app.state.agent_runtime.client_factory = lambda _settings: fake_llm
        _check_builtin_conversation(client, {"X-Token": TOKEN}, fake_llm)
        print("[ok] 内置 Agent Fake LLM 能力自检、SSE 对话、消息持久化与 token 用量")
        _check_copilot_pipeline_and_schedule(client, {"X-Token": TOKEN})
        print("[ok] Copilot 验证上传、资源列表、共享流水线入队与 schedule 持久化")
        _check_failure_incident(client, {"X-Token": TOKEN}, fake_llm)

        # 1. Real REST tool catalog and a SAFE tool invocation, with auth contract.
        catalog_response = client.get("/api/agent/tools")
        _require(
            catalog_response.status_code == 401
            and catalog_response.json().get("error", {}).get("code") == "UNAUTHORIZED",
            "REST Agent 工具清单缺少现有鉴权语义",
        )
        headers = {"X-Token": TOKEN, "X-Request-Id": "m11-smoke-rest-call"}
        catalog = _check_response(
            client.get("/api/agent/tools", headers=headers), 200, "Agent 工具清单"
        )
        tool_names = {item["name"] for item in catalog.get("items", [])}
        _require("run_copilot" not in tool_names, "工具清单错误发布了未实现的 run_copilot")
        _require("get_device_status" in tool_names, "真实工具清单缺少 get_device_status")
        task_catalog = _check_response(
            client.get("/api/tasks/types", headers=headers), 200, "共享任务类型"
        )
        _require(
            any(item.get("name") == "Copilot" for item in task_catalog.get("items", [])),
            "共享任务类型缺少 Copilot",
        )

        unauthenticated_invoke = client.post(
            "/api/agent/tools/get_device_status/invoke", json={"arguments": {}}
        )
        _require(
            unauthenticated_invoke.status_code == 401
            and unauthenticated_invoke.json().get("error", {}).get("code") == "UNAUTHORIZED",
            "REST 工具调用未执行当前鉴权契约",
        )
        safe_result = _check_response(
            client.post(
                "/api/agent/tools/get_device_status/invoke",
                json={"arguments": {}},
                headers=headers,
            ),
            200,
            "REST SAFE 工具调用",
        )
        _require(
            safe_result.get("status") == "success"
            and safe_result.get("result", {}).get("address") == "smoke:5555",
            f"REST SAFE 工具结果不匹配：{safe_result!r}",
        )
        safe_audit = _check_response(
            client.get(
                f"/api/agent/audits/{safe_result['audit_id']}", headers=headers
            ),
            200,
            "REST 工具审计回查",
        )
        _require(
            safe_audit.get("status") == "success"
            and safe_audit.get("request_id") == "m11-smoke-rest-call"
            and safe_audit.get("caller") == CallerType.REST.value,
            f"REST 审计字段不匹配：{safe_audit!r}",
        )
        print("[ok] REST 工具鉴权、真实 SAFE 调用、run_copilot 缺席与审计回查")

        # 2. Create a persisted internal session through the production REST API.
        session_body = _check_response(
            client.post(
                "/api/agent/sessions",
                json={"title": "M11 smoke authorization"},
                headers=headers,
            ),
            201,
            "Agent 会话创建",
        )
        session_id = session_body["session_id"]

        # The initial internal atomic call requests a session grant. Approval still
        # travels through the actual confirmations REST endpoint.
        grant_pending = _check_response(
            client.post(
                f"/__smoke/internal-sessions/{session_id}/tools/click/invoke",
                json={"arguments": {"x": 17, "y": 29}, "mode": "async"},
                headers=headers,
            ),
            202,
            "内部 atomic grant 请求",
        )
        _require(grant_pending.get("status") == "awaiting_confirmation", f"授权未进入等待：{grant_pending!r}")
        grant_id = grant_pending["confirmation_id"]
        grant_detail = _check_response(
            client.get(f"/api/confirmations/{grant_id}", headers=headers),
            200,
            "会话授权确认详情",
        )
        _require(
            grant_detail.get("action") == "grant_atomic_ops"
            and grant_detail.get("audit_id") == grant_pending["audit_id"],
            f"会话授权确认内容不匹配：{grant_detail!r}",
        )
        _check_response(
            client.post(
                f"/api/confirmations/{grant_id}",
                json={"approved": True},
                headers=headers,
            ),
            200,
            "REST 批准会话授权",
        )
        granted_audit = _wait_for_audit(client, grant_pending["audit_id"])
        _require(
            granted_audit.get("status") == "success"
            and granted_audit.get("session_id") == session_id
            and granted_audit.get("authorized_by_id") == grant_id
            and granted_audit.get("confirmation_id") is None,
            f"授权后的首个 atomic audit 未记录授权来源：{granted_audit!r}",
        )
        granted_session = _check_response(
            client.get(f"/api/agent/sessions/{session_id}", headers=headers),
            200,
            "已授权会话详情",
        )
        _require(
            granted_session.get("atomic_grant", {}).get("granted") is True
            and granted_session["atomic_grant"].get("grant_id") == grant_id,
            f"会话授权未持久化：{granted_session.get('atomic_grant')!r}",
        )

        # The internal caller reuses its grant and audit records the authorizer.
        internal_result = _check_response(
            client.post(
                f"/__smoke/internal-sessions/{session_id}/tools/click/invoke",
                json={"arguments": {"x": 31, "y": 41}, "mode": "sync"},
                headers=headers,
            ),
            200,
            "会话授权复用的内部工具调用",
        )
        _require(internal_result.get("status") == "success", f"内部授权调用失败：{internal_result!r}")
        reused_audit = _check_response(
            client.get(f"/api/agent/audits/{internal_result['audit_id']}", headers=headers),
            200,
            "授权复用审计详情",
        )
        _require(
            reused_audit.get("authorized_by_id") == grant_id
            and reused_audit.get("session_id") == session_id,
            f"授权复用审计未关联 grant：{reused_audit!r}",
        )

        # REST remains per-call confirmed even when an INTERNAL session has a grant.
        rest_atomic = _check_response(
            client.post(
                "/api/agent/tools/click/invoke",
                json={"arguments": {"x": 3, "y": 5}, "mode": "async"},
                headers=headers,
            ),
            202,
            "REST atomic 工具确认",
        )
        rest_confirmation = _check_response(
            client.get(
                f"/api/confirmations/{rest_atomic['confirmation_id']}", headers=headers
            ),
            200,
            "REST atomic 确认详情",
        )
        _require(
            rest_confirmation.get("action") == "click"
            and rest_confirmation.get("payload", {}).get("grant") is False,
            f"REST 工具调用未按每次确认处理：{rest_confirmation!r}",
        )
        _check_response(
            client.post(
                f"/api/confirmations/{rest_atomic['confirmation_id']}",
                json={"approved": False, "reason": "smoke rejection"},
                headers=headers,
            ),
            200,
            "REST 拒绝 atomic 确认",
        )
        rejected_audit = _wait_for_audit(client, rest_atomic["audit_id"])
        _require(
            rejected_audit.get("status") == "rejected"
            and rejected_audit.get("confirmation_id") == rest_atomic["confirmation_id"],
            f"REST 拒绝未终结审计：{rejected_audit!r}",
        )
        _require(
            main_module.app.state.device_manager.clicks == [(17, 29), (31, 41)],
            f"原子操作执行次数错误（REST 被拒绝调用不应执行）：{main_module.app.state.device_manager.clicks!r}",
        )
        print("[ok] 确认 REST 往返、会话授权持久化/复用、REST 每次确认与拒绝审计")

        # 3. Inject an actual custom task through REST; first MaaCore reload fails.
        custom_content = {"action": "Click", "template": ["m11-smoke.png"]}
        custom_pending = _check_response(
            client.post(
                "/api/agent/tools/register_custom_task/invoke",
                json={
                    "arguments": {
                        "name": "Custom_M11_Smoke",
                        "description": "M11 rollback smoke",
                        "content": custom_content,
                    },
                    "mode": "async",
                },
                headers=headers,
            ),
            202,
            "自定义 task 注入确认",
        )
        _require(custom_pending.get("status") == "awaiting_confirmation", f"注入缺少确认：{custom_pending!r}")
        _check_response(
            client.post(
                f"/api/confirmations/{custom_pending['confirmation_id']}",
                json={"approved": True},
                headers=headers,
            ),
            200,
            "批准自定义 task 注入",
        )
        custom_audit = _wait_for_audit(client, custom_pending["audit_id"])
        _require(
            custom_audit.get("status") == "failed"
            and custom_audit.get("error_code") == "RESOURCE_LOAD_FAILED",
            f"重载失败未形成 RESOURCE_LOAD_FAILED 审计：{custom_audit!r}",
        )
        custom_confirmation = _check_response(
            client.get(
                f"/api/confirmations/{custom_pending['confirmation_id']}", headers=headers
            ),
            200,
            "自定义 task 终态确认回查",
        )
        _require(
            custom_confirmation.get("execution", {}).get("status") == "failed"
            and custom_confirmation["execution"].get("audit_id") == custom_pending["audit_id"],
            f"确认详情未回查失败执行结果：{custom_confirmation!r}",
        )
        list_result = _check_response(
            client.post(
                "/api/agent/tools/list_custom_tasks/invoke",
                json={"arguments": {"page": 1, "size": 20}},
                headers=headers,
            ),
            200,
            "自定义 task 回滚后列表",
        )
        _require(
            list_result.get("status") == "success"
            and list_result.get("result", {}).get("total") == 0,
            f"失败注入后数据库资产未回滚：{list_result!r}",
        )
        resource_service = main_module.app.state.resource_service
        task_file = resource_service._custom_task_path()
        _require(not task_file.exists(), f"失败注入后 tasks.json 仍存在：{task_file}")
        _require(
            resource_service.smoke_reload_calls == 2,
            f"应调用一次失败重载和一次回滚重载，实际 {resource_service.smoke_reload_calls} 次",
        )
        audit_list = _check_response(
            client.get(
                "/api/agent/audits?caller=rest&tool_name=register_custom_task&status=failed",
                headers=headers,
            ),
            200,
            "失败 task 的审计过滤回查",
        )
        _require(
            any(item["audit_id"] == custom_pending["audit_id"] for item in audit_list.get("items", [])),
            f"过滤后的审计列表缺少注入失败记录：{audit_list!r}",
        )
        print("[ok] 自定义 task 校验、重载失败、数据库/文件回滚及确认/审计回查")

        _require(db_path.exists() and db_path.is_relative_to(temp_dir), f"临时 SQLite 不在隔离目录：{db_path}")
        after = _snapshot_repo_artifacts()
        _require(before == after, f"冒烟写入了仓库 DB/config 产物：before={before!r} after={after!r}")
        print(f"[ok] 临时 SQLite 隔离；仓库 DB/config 指纹未改变（{db_path.stat().st_size} bytes）")

    except BaseException as exc:
        failure = exc
        print(f"[agent_smoke][FAIL] {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
    finally:
        if client is not None:
            try:
                client.__exit__(None, None, None)
            except BaseException as exc:
                if failure is None:
                    failure = exc
                print(f"[agent_smoke][FAIL] 退出 app lifespan 异常：{type(exc).__name__}: {exc}", flush=True)

        # Restore process globals even on assertion/startup failure.
        if temp_engine is not None:
            try:
                temp_engine.sync_engine.dispose()
            except Exception:
                pass
        db_session.DB_PATH, db_session.ASYNC_URL, db_session.SYNC_URL, db_session.engine, db_session.session_factory = prior_db
        settings_module.DEFAULT_CONFIG_PATH = old_default_config
        settings_module.set_settings(None)
        main_module.REPO_ROOT = old_main_root
        resource_service_module.ResourceService = old_resource_service
        for name, previous in old_state.items():
            if previous is None:
                if hasattr(main_module.app.state, name):
                    delattr(main_module.app.state, name)
            else:
                setattr(main_module.app.state, name, previous)
        for env_name, value in old_env.items():
            if value is None:
                os.environ.pop(env_name, None)
            else:
                os.environ[env_name] = value

        after = _snapshot_repo_artifacts()
        if before != after:
            failure = failure or SmokeFailure("冒烟退出时仓库 DB/config 产物指纹发生变化")
            print(f"[agent_smoke][FAIL] 仓库 DB/config 产物发生变化：{after!r}", flush=True)
        if keep_temp or failure is not None:
            print(f"[agent_smoke] 临时现场：{temp_dir}", flush=True)
        else:
            shutil.rmtree(temp_dir, ignore_errors=True)

    if failure is not None:
        raise SmokeFailure(str(failure)) from failure


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent_smoke.py",
        description="M11 Agent REST、确认、会话授权、审计与自定义 task 回滚硬验收。",
    )
    parser.add_argument("--keep-temp", action="store_true", help="通过时保留临时库与资源目录")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        run(keep_temp=args.keep_temp)
    except SmokeFailure as exc:
        print(f"[agent_smoke][FAIL] {exc}", flush=True)
        return 1
    print("SMOKE OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
