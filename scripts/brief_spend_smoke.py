#!/usr/bin/env python3
"""M84 简报与消耗护栏端到端硬验收（spec §5 T10）。

使用生产 ``create_app()`` 装配与应用 lifespan、真实 REST routers、真实
``SpendGuard`` / ``BriefService`` / 队列与临时 SQLite；MaaCore、ADB 与外部网络不
启动。总控与 analyst 的模型调用换成脚本化 Fake LLM（注入
``AgentRuntime.client_factory``）：analyst 从工单说明里取出产物 id，回一条
``report(ready, details={brief_artifact_id, suggestions})``。简报收尾靠真实的
30 秒巡检任务，所以脚本最多等 60 秒，产物 ``meta.status`` 直接只读临时 SQLite。

成功时最后一行严格为 ``SMOKE OK``；失败输出
``[brief_spend_smoke][FAIL]`` 并返回 1。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.fakes.fake_llm import text_response, tool_call, tool_response
from tests.fakes.lifespan import (
    FAKE_LIFESPAN_STATE_NAMES,
    install_fake_lifespan,
)

TOKEN = "m84-brief-spend-smoke-token"
TEMP_PREFIX = "maa-api-brief-spend-smoke-"
MEDICINE_LIMIT = 2
BRIEF_READY_TIMEOUT_SECONDS = 60.0

#: M67 自动探查的固定请求文本（与 ``maa_api/agent/llm/profiles.py`` 一致）。
_JSON_SCHEMA_PROBE_PROMPT = "Return a JSON object with ok set to true."
_TAIL_PROBE_SYSTEM = "You are a probe. Reply with OK."
#: 工单说明里 analyst 写回用的产物 id（``brief.render.render_ticket_brief``）。
_BRIEF_ARTIFACT_RE = re.compile(r'"brief_artifact_id"\s*:\s*"([0-9a-f-]{36})"')
#: 记忆整理代理系统提示的身份标记（``memory_curator`` 定义正文首句）。
_MEMORY_CURATOR_MARKER = "只抽出用户明确说过"


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


def _message_text(messages: list[dict[str, Any]]) -> str:
    return "\n".join(_content_text(message.get("content")) for message in messages)


class _SmokeLLM:
    """能力探针照常应答；analyst 回一条带建议的 report，总控只收尾。"""

    def __init__(self) -> None:
        self.requests: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]] = []
        self.brief_artifact_ids: set[str] = set()
        self._report_sent = False
        self.tool_probe_response = tool_response(
            tool_call("smoke-capability-probe", "capability_probe", {})
        )
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
        text = _message_text(messages)
        # 记忆抽取也走结构化输出（带 response_format），必须排在通用探针之前，
        # 否则会被 JSON Schema 探针的固定应答截走。
        if _MEMORY_CURATOR_MARKER in text:
            return text_response('{"items": []}')
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
        self.requests.append(("", list(messages), list(tools)))
        probe = self._probe(messages, tools, options)
        if probe is not None:
            for chunk in probe:
                yield chunk
            return

        system = _system_text(messages)
        body = _message_text(messages)
        if "你是 MAA-API 的总控" in system:
            self.requests[-1] = ("orchestrator", list(messages), list(tools))
            chunks = text_response("简报工单的汇报已收到。")
        elif "brief_artifact_id" in body:
            self.requests[-1] = ("analyst", list(messages), list(tools))
            if self._report_sent:
                raise SmokeFailure("analyst 重复请求，冒烟只准备了一轮")
            match = _BRIEF_ARTIFACT_RE.search(body)
            if match is None:
                raise SmokeFailure(
                    f"analyst 工单说明里没有产物 id：{body[-400:]!r}"
                )
            artifact_id = match.group(1)
            self.brief_artifact_ids.add(artifact_id)
            self._report_sent = True
            chunks = tool_response(
                tool_call(
                    "smoke-brief-report",
                    "report",
                    {
                        "status": "ready",
                        "summary": (
                            "今日简报已生成：理智药已计 3，手动提交成功；"
                            "代理与定时任务都被消耗上限拦下。"
                        ),
                        "details": {
                            "brief_artifact_id": artifact_id,
                            "suggestions": [
                                "先把今日日常做完，理智药留给活动。",
                                "明早再决定是否调高理智药上限。",
                            ],
                        },
                    },
                )
            )
        elif not system:
            # 会话标题是首轮收流前的额外调用（无系统提示、无工具）。
            self.requests[-1] = ("title", list(messages), list(tools))
            chunks = text_response("简报")
        else:
            raise SmokeFailure(
                "无法从系统提示识别调用方："
                f"system={system[:160]!r}，"
                f"tools={[item.get('function', {}).get('name') for item in tools]!r}"
            )
        for chunk in chunks:
            yield chunk

    def request_summary(self) -> list[tuple[str, int]]:
        """失败诊断用：调用方与消息条数，不含正文。"""
        return [(name, len(messages)) for name, messages, _tools in self.requests]


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


def _check_error(
    response: Any,
    status_code: int,
    code: str,
    label: str,
) -> dict[str, Any]:
    """断言 HTTP 状态与统一错误码，返回 ``error`` 对象。"""
    body = _check_response(response, status_code, label)
    error = body.get("error") or {}
    _require(
        error.get("code") == code,
        f"{label} 错误码应为 {code}，实际 {error.get('code')!r}：{response.text[:400]}",
    )
    return error


def _settings_value(payload: dict[str, Any], key: str) -> Any:
    for item in payload.get("items", []):
        if item.get("key") == key:
            return item.get("value")
    raise SmokeFailure(f"设置响应里没有 {key}：{payload!r}")


def _read_artifact_meta(db_path: Path, artifact_id: str) -> dict[str, Any] | None:
    """只读临时 SQLite，返回产物的 ``meta``；库暂时被写锁占住时返回 None。"""
    uri = db_path.resolve().as_uri() + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=5) as connection:
            row = connection.execute(
                "SELECT meta FROM artifact WHERE id = ?", (artifact_id,)
            ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    value = row[0]
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _wait_for_brief_ready(
    db_path: Path,
    artifact_id: str,
    fake_llm: _SmokeLLM,
) -> dict[str, Any]:
    """等真实的 30 秒巡检把 ``meta.status`` 收尾成 ``ready``（最多 60 秒）。"""
    deadline = time.monotonic() + BRIEF_READY_TIMEOUT_SECONDS
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = _read_artifact_meta(db_path, artifact_id)
        status = (last or {}).get("status")
        if status == "ready":
            return last or {}
        if status == "facts_only":
            raise SmokeFailure(
                "简报被收尾成 facts_only（analyst 的 ready 汇报没被巡检认到）："
                f"meta={last!r}，LLM 请求={fake_llm.request_summary()!r}"
            )
        time.sleep(0.2)
    raise SmokeFailure(
        f"等待简报产物 ready 超时（{BRIEF_READY_TIMEOUT_SECONDS:.0f}s）："
        f"meta={last!r}，LLM 请求={fake_llm.request_summary()!r}"
    )


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

        # M67 的替换点：总控与 Worker 解析档案时都经 ModelRouter.client_for
        # 读 ``AgentRuntime.client_factory``（与既有冒烟同一注入方式）。
        fake_llm = _SmokeLLM()
        main_module.app.state.agent_runtime.client_factory = lambda _settings: fake_llm

        # 动态知识段会去 PRTS 取活动与卡池；简报事实也会读卡池。冒烟不依赖外网。
        async def _empty_dynamic() -> str:
            return ""

        main_module.app.state.knowledge_service.render_dynamic_context = _empty_dynamic
        main_module.app.state.gacha_service = None

        headers = {"X-Token": TOKEN}
        pipeline_body = {
            "title": "M84 冒烟：三瓶理智药",
            "tasks": [{"name": "Fight", "stage": "1-7", "medicine": 3}],
        }

        # 1. 设置页把理智药上限设为 2（hot_action=none，立即生效）。
        updated = _check_response(
            client.put(
                "/api/settings",
                json={"items": {"spend.daily_medicine_limit": MEDICINE_LIMIT}},
                headers=headers,
            ),
            200,
            "修改理智药上限",
        )
        _require(
            _settings_value(updated, "spend.daily_medicine_limit") == MEDICINE_LIMIT,
            f"设置没有写进生效配置：{updated.get('items')!r}",
        )
        print(f"[ok] 设置 spend.daily_medicine_limit={MEDICINE_LIMIT}")

        # 2. REST 调 submit_pipeline：确认之前就被 403 拦下，不建确认卡，审计 rejected。
        invoked = client.post(
            "/api/agent/tools/submit_pipeline/invoke",
            json={"arguments": pipeline_body, "mode": "sync"},
            headers=headers,
        )
        error = _check_error(
            invoked, 403, "SPEND_CAP_EXCEEDED", "REST 调用 submit_pipeline"
        )
        details = error.get("details") or {}
        _require(
            details.get("limit") == MEDICINE_LIMIT
            and details.get("requested") == 3
            and details.get("used") == 0
            and details.get("source") == "agent",
            f"拦截详情不对：{details!r}",
        )
        pending = _check_response(
            client.get(
                "/api/confirmations",
                params={"status": "pending"},
                headers=headers,
            ),
            200,
            "待确认列表",
        )
        _require(
            int(pending.get("total", -1)) == 0,
            f"超额预检不应建确认卡：{pending!r}",
        )
        audits = _check_response(
            client.get("/api/agent/audits", params={"size": 1}, headers=headers),
            200,
            "审计列表",
        )
        latest = (audits.get("items") or [None])[0]
        _require(
            latest is not None
            and latest.get("status") == "rejected"
            and latest.get("error_code") == "SPEND_CAP_EXCEEDED"
            and latest.get("tool_name") == "submit_pipeline"
            and latest.get("caller") == "rest",
            f"最新一条审计不是消耗拦截：{latest!r}",
        )
        print(
            "[ok] REST 调 submit_pipeline 403 SPEND_CAP_EXCEEDED；"
            "确认面板 0，审计 rejected"
        )

        # 3. 同一条流水线手动提交：manual 不设限但计入用量。
        submitted = _check_response(
            client.post("/api/pipelines", json=pipeline_body, headers=headers),
            202,
            "手动提交流水线",
        )
        _require(
            isinstance(submitted.get("pipeline_id"), str)
            and bool(submitted.get("pipeline_id")),
            f"手动提交没有流水线 id：{submitted!r}",
        )
        spend = _check_response(
            client.get("/api/agent/spend", headers=headers), 200, "消耗汇总"
        )
        medicine = next(
            (row for row in spend.get("today", []) if row.get("kind") == "medicine"),
            None,
        )
        _require(
            medicine is not None
            and medicine.get("used") == 3
            and medicine.get("limit") == MEDICINE_LIMIT,
            f"手动提交后理智药用量不对：{spend.get('today')!r}",
        )
        print("[ok] 手动提交同样流水线成功；GET /api/agent/spend 理智药 used=3")

        # 4. 定时任务「立即执行」：scheduled 来源，已计 3 再加 1 瓶，入队时被拦。
        schedule = _check_response(
            client.post(
                "/api/schedules",
                json={
                    "name": "M84 冒烟：一瓶理智药",
                    "cron": "0 7 * * *",
                    "timezone": "UTC",
                    "enabled": False,
                    "template": [
                        {"name": "Fight", "stage": "1-7", "medicine": 1}
                    ],
                },
                headers=headers,
            ),
            201,
            "新建定时任务",
        )
        schedule_id = schedule.get("id")
        _require(
            isinstance(schedule_id, str) and bool(schedule_id),
            f"新建定时任务没有 id：{schedule!r}",
        )
        scheduled = client.post(
            f"/api/schedules/{schedule_id}/run", json={}, headers=headers
        )
        scheduled_error = _check_error(
            scheduled, 403, "SPEND_CAP_EXCEEDED", "定时任务立即执行"
        )
        _require(
            (scheduled_error.get("details") or {}).get("source") == "scheduled",
            f"定时任务拦截来源不对：{scheduled_error.get('details')!r}",
        )
        print("[ok] 定时任务「立即执行」403 SPEND_CAP_EXCEEDED")

        # 5. 手动简报：202，analyst 经 Fake LLM 回 ready，真实巡检在 60 秒内收尾。
        brief = _check_response(
            client.post("/api/agent/brief/run", headers=headers),
            202,
            "立即生成简报",
        )
        brief_ticket_id = brief.get("ticket_id")
        brief_artifact_id = brief.get("artifact_id")
        _require(
            isinstance(brief_ticket_id, str)
            and bool(brief_ticket_id)
            and isinstance(brief_artifact_id, str)
            and bool(brief_artifact_id),
            f"简报响应缺少 id：{brief!r}",
        )
        meta = _wait_for_brief_ready(db_path, brief_artifact_id, fake_llm)
        _require(
            meta.get("ticket_id") == brief_ticket_id,
            f"产物 meta.ticket_id 与响应不一致：{meta!r} vs {brief!r}",
        )
        _require(
            fake_llm.brief_artifact_ids == {brief_artifact_id},
            "analyst 从工单说明里取到的产物 id 与响应不一致："
            f"{fake_llm.brief_artifact_ids!r} vs {brief_artifact_id}",
        )
        print("[ok] POST /api/agent/brief/run 202；60 秒内产物 meta.status=ready")

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
        print(f"[brief_spend_smoke][FAIL] {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
    finally:
        if client is not None:
            try:
                client.__exit__(None, None, None)
            except BaseException as exc:
                if failure is None:
                    failure = exc
                print(
                    f"[brief_spend_smoke][FAIL] 退出 app lifespan 异常："
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
                f"[brief_spend_smoke][FAIL] 仓库 DB/config 产物发生变化：{after!r}",
                flush=True,
            )
        if keep_temp or failure is not None:
            print(f"[brief_spend_smoke] 临时现场：{temp_dir}", flush=True)
        else:
            shutil.rmtree(temp_dir, ignore_errors=True)

    if failure is not None:
        raise SmokeFailure(str(failure)) from failure


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brief_spend_smoke.py",
        description="M84 消耗护栏与每日简报端到端硬验收（spec §5 T10）。",
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
        print(f"[brief_spend_smoke][FAIL] {exc}", flush=True)
        return 1
    print("SMOKE OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
