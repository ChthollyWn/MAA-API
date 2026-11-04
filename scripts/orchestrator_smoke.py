#!/usr/bin/env python3
"""M73 总控与自动诊断端到端硬验收：建单、委托、诊断与去重。

使用生产 ``create_app()`` 装配与应用 lifespan、真实 REST routers、真实 Ticket /
Worker / Orchestrator 服务与临时 SQLite；MaaCore、ADB 与外部网络不启动。总控与
诊断 Worker 的模型调用换成脚本化 Fake LLM（注入 ``AgentRuntime.client_factory``），
诊断用的失败流水线由本脚本直接写一条终态失败行，再调用
``AgentIncidentService.pipeline_failed``。

成功时最后一行严格为 ``SMOKE OK``；失败输出 ``[orchestrator_smoke][FAIL]`` 并返回 1。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.fakes.lifespan import (
    FAKE_LIFESPAN_STATE_NAMES,
    install_fake_lifespan,
)

TOKEN = "m73-orchestrator-smoke-token"
TEMP_PREFIX = "maa-api-orchestrator-smoke-"
FAILED_PIPELINE_ID = "m73-smoke-failed-pipeline"

#: M67 自动能力探查的固定请求文本（与 ``maa_api/agent/llm/profiles.py`` 一致）。
_JSON_SCHEMA_PROBE_PROMPT = "Return a JSON object with ok set to true."
_TAIL_PROBE_SYSTEM = "You are a probe. Reply with OK."


class SmokeFailure(RuntimeError):
    """An acceptance assertion failed."""


def _content_text(content: object) -> str:
    """一条消息的文本内容（多模态数组只取 text 部分）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _system_text(messages: list[dict[str, Any]]) -> str:
    return "\n".join(
        _content_text(message.get("content"))
        for message in messages
        if str(message.get("role")) == "system"
    )


def _caller(messages: list[dict[str, Any]]) -> str:
    """按系统提示里的身份标记区分总控、诊断 Worker 与其他 Worker。"""
    text = _system_text(messages)
    if "你是 MAA-API 的总控" in text:
        return "orchestrator"
    if "## 工单卡" in text:
        return "diagnose" if "自动诊断" in text else "worker"
    raise SmokeFailure(
        "无法从系统提示识别调用方："
        f"第一条 system={_content_text((messages[0] or {}).get('content'))[:160]!r}"
    )


def _text_response(
    text: str, *, prompt_tokens: int = 7, completion_tokens: int = 2
) -> list[dict[str, Any]]:
    return [
        {
            "choices": [
                {"index": 0, "delta": {"content": text}, "finish_reason": None}
            ]
        },
        {
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        },
    ]


def _tool_response(
    call_id: str, name: str, arguments: dict[str, Any]
) -> list[dict[str, Any]]:
    return [
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": json.dumps(
                                        arguments, ensure_ascii=False
                                    ),
                                },
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        },
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    ]


class _SmokeLLM:
    """按调用方作答的 OpenAI 兼容流；总控走脚本，Worker 只给一条收尾文本。"""

    def __init__(self) -> None:
        self.requests: list[
            tuple[str, list[dict[str, Any]], list[dict[str, Any]]]
        ] = []
        self.orchestrator = [
            _tool_response(
                "smoke-create-ticket",
                "create_ticket",
                {
                    "kind": "daily_ops",
                    "title": "日常",
                    "brief": "把今天的日常做完。用户原话：把日常做了。",
                    "scope": "完成今天的日常",
                },
            ),
            _text_response("日常的工单已经交给执行官。", prompt_tokens=11, completion_tokens=6),
        ]
        self.tool_probe_response = [
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
        ]
        self.vision_probe_response = [
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "blue"},
                        "finish_reason": None,
                    }
                ]
            }
        ]
        self.json_schema_probe_response = [
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": '{"ok": true}'},
                        "finish_reason": None,
                    }
                ]
            },
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        self.tail_probe_response = [
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "OK"},
                        "finish_reason": None,
                    }
                ]
            },
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]

    def _probe(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        options: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        tool_names = {
            str(tool.get("function", {}).get("name"))
            for tool in tools
            if isinstance(tool, dict)
        }
        text = "\n".join(_content_text(message.get("content")) for message in messages)
        if "capability_probe" in tool_names:
            return self.tool_probe_response
        if any(
            isinstance(message.get("content"), list)
            and any(
                isinstance(part, dict) and part.get("type") == "image_url"
                for part in message["content"]
            )
            for message in messages
        ):
            return self.vision_probe_response
        if options.get("response_format") is not None or (
            _JSON_SCHEMA_PROBE_PROMPT in text
        ):
            return self.json_schema_probe_response
        if _TAIL_PROBE_SYSTEM in text:
            return self.tail_probe_response
        return None

    async def stream_chat_completion(self, model, messages, tools, **options):
        del model
        probe = self._probe(messages, tools, options)
        if probe is not None:
            for chunk in probe:
                yield chunk
            return

        caller = _caller(messages)
        self.requests.append((caller, list(messages), list(tools)))
        if caller == "orchestrator":
            if not self.orchestrator:
                raise SmokeFailure("总控的 Fake LLM 脚本已用完")
            chunks = self.orchestrator.pop(0)
        elif caller == "diagnose":
            chunks = _text_response("已按冒烟脚本结束这次诊断。")
        else:
            chunks = _text_response("已按冒烟脚本结束这一轮。")
        for chunk in chunks:
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


def _check_response(response: Any, status_code: int, label: str) -> dict[str, Any]:
    _require(
        response.status_code == status_code,
        f"{label} 应返回 {status_code}，实际 {response.status_code}: {response.text[:400]}",
    )
    return response.json() if response.content else {}


def _parse_events(text: str) -> list[tuple[str, dict[str, Any]]]:
    """把 SSE 响应体解析成 ``(事件名, 数据)``；跳过 keepalive 注释帧。"""
    events: list[tuple[str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if ": " in line:
                key, value = line.split(": ", 1)
                fields[key] = value
        if "event" in fields and "data" in fields:
            events.append((fields["event"], json.loads(fields["data"])))
    return events


async def _insert_failed_pipeline(service: Any, pipeline_id: str) -> None:
    """写一条终态失败的 pipeline 行；只用于让 ``_pipeline_context`` 有数据。"""
    from maa_api.db.models import Pipeline, utcnow

    now = utcnow()
    async with service.session_factory() as session:
        session.add(
            Pipeline(
                id=pipeline_id,
                core_id="default",
                source="manual",
                priority=0,
                status="failed",
                title="M73 冒烟失败流水线",
                task_count=0,
                error_code="TASK_FAILED",
                error_message="冒烟注入的终态失败",
                created_at=now,
                started_at=now,
                finished_at=now,
            )
        )
        await session.commit()


async def _inbox_count(service: Any, session_id: str) -> int:
    from sqlalchemy import func, select

    from maa_api.db.models import AgentInbox

    async with service.session_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(AgentInbox)
            .where(AgentInbox.session_id == session_id)
        )
    return int(count or 0)


def _diagnose_total(client: Any, headers: dict[str, str]) -> int:
    page = _check_response(
        client.get(
            "/api/agent/tickets",
            params={"kind": "diagnose", "size": 200},
            headers=headers,
        ),
        200,
        "诊断工单列表",
    )
    return int(page.get("total", 0))


def run(*, keep_temp: bool = False) -> None:
    before = _snapshot_repo_artifacts()
    temp_dir = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX)).resolve()
    failure: BaseException | None = None
    client = None

    import maa_api.main as main_module
    import maa_api.settings as settings_module
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy.pool import NullPool

    from maa_api.db import session as db_session

    db_path = temp_dir / "resource" / "maa_api.db"
    config_path = temp_dir / "config.yaml"
    app_root = temp_dir / "app-root"
    core_path = app_root / "core"
    core_path.mkdir(parents=True)
    app_root.mkdir(parents=True, exist_ok=True)
    # 指向本机 discard 端口：启动期若真的尝试探查模型，连接立即被拒绝，不出外网。
    config_path.write_text(
        "app:\n"
        f"  access_token: {TOKEN}\n"
        f"  maa_core_path: {core_path}\n"
        "llm:\n"
        "  base_url: http://127.0.0.1:9/v1\n"
        "  api_key: smoke-only\n"
        "  model: smoke-model\n",
        encoding="utf8",
    )

    prior_db = (
        db_session.DB_PATH,
        db_session.ASYNC_URL,
        db_session.SYNC_URL,
        db_session.engine,
        db_session.session_factory,
    )
    old_default_config = settings_module.DEFAULT_CONFIG_PATH
    old_main_root = main_module.REPO_ROOT
    old_state: dict[str, Any] = {}
    old_env: dict[str, str | None] = {}
    temp_engine = None

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

        # Lifespan's configuration reload and all hard-coded app paths are isolated.
        for env_name in settings_module.ENV_OVERRIDES.values():
            old_env[env_name] = os.environ.get(env_name)
            os.environ.pop(env_name, None)
        settings_module.DEFAULT_CONFIG_PATH = config_path
        main_module.REPO_ROOT = app_root
        settings_module.set_settings(settings_module.load_settings(config_path))

        for name in FAKE_LIFESPAN_STATE_NAMES:
            old_state[name] = getattr(main_module.app.state, name, None)
        install_fake_lifespan(main_module.app)

        client = TestClient(main_module.app, raise_server_exceptions=False)
        client.__enter__()

        # M67 的替换点：总控、Worker 与子代理解析档案时都经 ModelRouter.client_for
        # 读 ``AgentRuntime.client_factory``（与 agent_smoke.py 同一注入方式）。
        fake_llm = _SmokeLLM()
        main_module.app.state.agent_runtime.client_factory = lambda _settings: fake_llm

        # 动态知识段会去 PRTS 取当期活动；冒烟不依赖外网，置空。
        async def _empty_dynamic() -> str:
            return ""

        main_module.app.state.knowledge_service.render_dynamic_context = _empty_dynamic
        headers = {"X-Token": TOKEN}
        incident_service = main_module.app.state.agent_incident_service

        # 1. 总控状态：真实 lifespan 已经建好唯一活动会话。
        state = _check_response(
            client.get("/api/agent/orchestrator", headers=headers),
            200,
            "总控状态",
        )
        session_id = state.get("session_id")
        _require(
            isinstance(session_id, str) and bool(session_id),
            f"总控状态缺少 session_id：{state!r}",
        )
        print("[ok] orchestrator")

        # 2. 脚本化假客户端让总控对「把日常做了」建 daily_ops 工单。
        response = client.post(
            "/api/agent/orchestrator/messages",
            json={"content": "把日常做了"},
            headers=headers,
        )
        _require(
            response.status_code == 200
            and response.headers.get("content-type", "").startswith(
                "text/event-stream"
            ),
            f"总控消息应返回 SSE：{response.status_code} {response.text[:400]}",
        )
        events = _parse_events(response.text)
        created = [data for name, data in events if name == "ticket_created"]
        _require(
            len(created) == 1,
            f"总控应恰好建一张工单，实际 ticket_created={created!r}",
        )
        ticket_id = created[0].get("ticket_id")
        _require(
            isinstance(ticket_id, str) and bool(ticket_id),
            f"ticket_created 缺少 ticket_id：{created!r}",
        )
        _require(
            any(name == "run_end" for name, _data in events),
            "总控 SSE 没有 run_end",
        )
        _require(
            "日常的工单已经交给执行官。" in response.text,
            "总控 SSE 未返回 Fake LLM 的收尾文本",
        )
        print("[ok] ticket")

        # 3. 工单详情：种类与显式委托。
        detail = _check_response(
            client.get(f"/api/agent/tickets/{ticket_id}", headers=headers),
            200,
            "总控工单详情",
        )
        _require(
            detail.get("ticket", {}).get("kind") == "daily_ops",
            f"总控选错 Worker 种类：{detail.get('ticket', {}).get('kind')!r}",
        )
        _require(
            detail.get("mandate", {}).get("strength") == "explicit",
            f"用户原话建单不是显式委托：{detail.get('mandate', {}).get('strength')!r}",
        )
        print("[ok] mandate")

        # 4. 直接对一条终态失败流水线调用诊断入口，得到 diagnose 工单。
        client.portal.call(_insert_failed_pipeline, incident_service, FAILED_PIPELINE_ID)
        diagnosed = client.portal.call(
            incident_service.pipeline_failed, FAILED_PIPELINE_ID
        )
        _require(
            diagnosed.created is True and diagnosed.session_id is not None,
            f"失败流水线没有开出诊断工单：{diagnosed!r}",
        )
        diagnose_ticket_id = diagnosed.session_id
        diagnose_detail = _check_response(
            client.get(f"/api/agent/tickets/{diagnose_ticket_id}", headers=headers),
            200,
            "诊断工单详情",
        )
        _require(
            diagnose_detail.get("ticket", {}).get("kind") == "diagnose",
            f"自动建单种类不是 diagnose：{diagnose_detail.get('ticket', {}).get('kind')!r}",
        )
        _require(
            diagnose_detail.get("mandate", {}).get("strength") == "inferred",
            f"诊断工单委托不是推断：{diagnose_detail.get('mandate', {}).get('strength')!r}",
        )
        diagnose_worker_session = diagnose_detail.get("worker_session_id")
        _require(
            isinstance(diagnose_worker_session, str)
            and bool(diagnose_worker_session),
            f"诊断工单没有 Worker 会话：{diagnose_detail!r}",
        )
        print("[ok] diagnose")

        # 5. 同一 pipeline 再失败：不新建工单，只在 Worker 收件箱追加一条。
        before_tickets = _diagnose_total(client, headers)
        before_inbox = int(
            client.portal.call(_inbox_count, incident_service, diagnose_worker_session)
        )
        replay = client.portal.call(
            incident_service.pipeline_failed, FAILED_PIPELINE_ID
        )
        after_tickets = _diagnose_total(client, headers)
        after_inbox = int(
            client.portal.call(_inbox_count, incident_service, diagnose_worker_session)
        )
        _require(
            replay.created is False,
            f"同一流水线重复失败又建了新工单：{replay!r}",
        )
        _require(
            after_tickets == before_tickets,
            f"诊断工单数变化：{before_tickets} → {after_tickets}",
        )
        _require(
            after_inbox == before_inbox + 1,
            f"Worker 收件箱应只多一条：{before_inbox} → {after_inbox}",
        )
        print("[ok] dedupe")

        _require(
            db_path.exists() and db_path.is_relative_to(temp_dir),
            f"临时 SQLite 不在隔离目录：{db_path}",
        )
        _require(
            before == _snapshot_repo_artifacts(),
            "冒烟写入了仓库 DB/config 产物",
        )
        print(
            f"[ok] 临时 SQLite 隔离；仓库 DB/config 指纹未改变"
            f"（{db_path.stat().st_size} bytes）"
        )

    except BaseException as exc:
        failure = exc
        print(f"[orchestrator_smoke][FAIL] {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
    finally:
        if client is not None:
            try:
                client.__exit__(None, None, None)
            except BaseException as exc:
                if failure is None:
                    failure = exc
                print(
                    f"[orchestrator_smoke][FAIL] 退出 app lifespan 异常："
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )

        if temp_engine is not None:
            try:
                temp_engine.sync_engine.dispose()
            except Exception:
                pass
        (
            db_session.DB_PATH,
            db_session.ASYNC_URL,
            db_session.SYNC_URL,
            db_session.engine,
            db_session.session_factory,
        ) = prior_db
        settings_module.DEFAULT_CONFIG_PATH = old_default_config
        settings_module.set_settings(None)
        main_module.REPO_ROOT = old_main_root
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
            failure = failure or SmokeFailure(
                "冒烟退出时仓库 DB/config 产物指纹发生变化"
            )
            print(
                f"[orchestrator_smoke][FAIL] 仓库 DB/config 产物发生变化：{after!r}",
                flush=True,
            )
        if keep_temp or failure is not None:
            print(f"[orchestrator_smoke] 临时现场：{temp_dir}", flush=True)
        else:
            shutil.rmtree(temp_dir, ignore_errors=True)

    if failure is not None:
        raise SmokeFailure(str(failure)) from failure


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orchestrator_smoke.py",
        description="M73 总控选种类、显式委托、自动诊断工单与同键去重硬验收。",
    )
    parser.add_argument(
        "--keep-temp", action="store_true", help="通过时保留临时库与资源目录"
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        run(keep_temp=args.keep_temp)
    except SmokeFailure as exc:
        print(f"[orchestrator_smoke][FAIL] {exc}", flush=True)
        return 1
    print("SMOKE OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
