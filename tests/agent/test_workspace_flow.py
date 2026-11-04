"""M75 §7 脚本化验收：外置指针与计划批准前后的工具闸门。

两条验收用例合成一条真实 Worker 运行链（规格 §7）：

1. FakeLLM 让 Worker 调用一个返回 20 000 字的工具；下一次模型请求里的 tool 消息
   必须是产物指针（含 ``artifact_id``），不含那 20 000 字全文。
2. 同一用例里 4 步 ``update_plan`` 进入 ``awaiting_approval``；批准前
   ``submit_pipeline`` 在计划中间件被拦成 ``PLAN_APPROVAL_REQUIRED`` 且不建确认行，
   经真实 ``approve_plan`` 端点批准后同一调用成功。

运行走 ``maa_api.agent.workers.pump.run_agent``：真实临时库、真实 Worker 中间件链、
真实 ``ArtifactService`` / ``PlanService`` / 确认服务；模型与流水线服务用替身，
不访问外网、不 sleep 真实时间。临时库沿用 ``tests/fakes.v3_db``。
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from maa_api.agent.artifacts.service import ArtifactService
from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.context.layout import ContextLayout
from maa_api.agent.context.tokens import TokenEstimator
from maa_api.agent.harness.events import RunEventBus
from maa_api.agent.harness.plan import PLAN_APPROVAL_REQUIRED
from maa_api.agent.plans.service import PlanService
from maa_api.agent.registry import ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.runs.service import RunService
from maa_api.agent.runtime import AgentRuntime
from maa_api.agent.specs.registry import builtin_spec
from maa_api.agent.tickets.inbox import InboxService
from maa_api.agent.tools import build_registry
from maa_api.agent.workers.pump import run_agent
from maa_api.api.routers.plans import approve_plan
from maa_api.api.schemas.agent_v3 import PlanApprovalRequest
from maa_api.db.models import (
    AgentAudit,
    AgentInbox,
    AgentMessage,
    AgentRun,
    AgentSession,
    Confirmation,
)
from maa_api.settings import Settings
from tests.fakes.fake_llm import FakeLLM, text_response, tool_call, tool_response
from tests.fakes.v3_db import make_session_factory, seed_run, seed_session

#: 20 000 字的工具输出；指针里的预览只含它的头尾片段，不能出现整段。
LONG_TEXT = "0123456789abcdef" * 1250
PLAN_TITLE = "刷 1-7 到 50 个固源岩"
PLAN_ITEMS = [
    {"title": "查当前库存与理智"},
    {"title": "提交 1-7 刷图流水线"},
    {"title": "等待流水线结束"},
    {"title": "核对固源岩掉落"},
]
SUBMIT_ARGS = {
    "title": "刷 1-7",
    "tasks": [{"name": "Fight", "stage": "1-7", "times": 1}],
}
#: 没有工单委托时 MandateMiddleware 会 fail closed；本用例只测计划闸门，给最高上限。
MANDATE_STATE = {"mandate.ceiling": "destructive"}


class _LongParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def _long_handler(_params: _LongParams, _ctx: Any) -> dict[str, str]:
    return {"text": LONG_TEXT}


def _long_tool() -> ToolDefinition:
    return ToolDefinition(
        name="get_long_text",
        group="test",
        risk=ToolRisk.SAFE,
        description="测试用长输出工具",
        params_model=_LongParams,
        handler=_long_handler,
        read_only=True,
    )


def _registry() -> ToolRegistry:
    """只装本用例会调用的真实定义，外加一个返回 20 000 字的测试工具。"""
    real = build_registry()
    registry = ToolRegistry()
    for name in ("update_plan", "read_artifact", "submit_pipeline"):
        registry.register(real.get(name))
    registry.register(_long_tool())
    return registry


class _QueueService:
    """``submit_pipeline`` 的替身：记录提交，返回一条不会真跑的流水线。"""

    def __init__(self) -> None:
        self.submissions: list[Any] = []

    async def submit(self, params: Any, *, source: Any, agent_session_id: str | None):
        self.submissions.append((params, source, agent_session_id))
        pipeline = SimpleNamespace(
            id="pipeline-t5b",
            status="queued",
            created_at=datetime.now(UTC),
        )
        return pipeline, False


class _PipelineRunner:
    """只记录发布调用，不接真实内核。"""

    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish_pipeline(self, pipeline_id: str) -> None:
        self.published.append(pipeline_id)

    async def publish_queue_changed(self) -> None:
        return None


class _ModelHandle:
    def __init__(self, llm: FakeLLM) -> None:
        self.client = llm

    async def aclose(self) -> None:
        return None


class _ModelRouter:
    """把 Worker 的模型调用交给 FakeLLM；档案只用于布局与记账。"""

    profile = SimpleNamespace(
        name="default",
        kind="chat",
        provider="openai_compatible",
        model="test-model",
        base_url="https://llm.test/v1",
        api_key="sk-test",
        context_window=None,
        max_output_tokens=None,
        supports_vision=True,
        supports_tools=True,
        supports_json_schema=True,
        dynamic_context_position="tail_system",
        price_input=None,
        price_cached_input=None,
        price_output=None,
        currency="USD",
        embedding_dim=None,
        local_model_path=None,
        readonly=False,
        key=("default", "test-model"),
    )

    def __init__(self, llm: FakeLLM) -> None:
        self.llm = llm

    async def resolve_for_spec(self, _spec: Any, **_kwargs: Any) -> Any:
        return self.profile

    def client_for(self, _profile: Any) -> _ModelHandle:
        return _ModelHandle(self.llm)


async def _seed_user_message(factory, session_id: str) -> None:
    """给 Worker 会话一条触发消息；真实运行从收件箱领取，这里只需要历史。"""
    async with factory() as db:
        db.add(
            AgentMessage(
                session_id=session_id,
                seq=1,
                role="user",
                content="先看库存，再提交 1-7 刷图。",
            )
        )
        await db.commit()


async def _seed_worker_run(factory, session_id: str) -> tuple[AgentSession, AgentRun]:
    run_id = await seed_run(factory, session_id=session_id)
    async with factory() as db:
        session = await db.get(AgentSession, session_id)
        run = await db.get(AgentRun, run_id)
    assert session is not None and run is not None
    return session, run


async def _count(factory, model: Any) -> int:
    async with factory() as db:
        return int(
            (await db.execute(select(func.count()).select_from(model))).scalar_one()
        )


def _tool_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [message for message in messages if message.get("role") == "tool"]


def _content(message: dict[str, Any]) -> str:
    content = message.get("content")
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def _json_tool_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [json.loads(_content(message)) for message in _tool_messages(messages)]


def test_spill_pointer_and_plan_approval_flow(tmp_path, monkeypatch) -> None:
    # 外置阈值固定成默认 8000，不读本机 config.yaml（它不进版本库）。
    monkeypatch.setattr(
        "maa_api.agent.workers.pump.get_settings", lambda: Settings()
    )

    async def scenario() -> None:
        engine, factory = await make_session_factory(
            tmp_path / "workspace-flow.db"
        )
        try:
            session_id = await seed_session(factory, kind="worker")
            await _seed_user_message(factory, session_id)
            registry = _registry()
            confirmations = ConfirmationService(factory, registry)
            runtime = AgentRuntime(factory, confirmations, question_service=None)
            plan_service = PlanService(
                factory, approval_mode="complex", complex_min_steps=4
            )
            artifacts = ArtifactService(factory, resource_root=tmp_path / "resource")
            inbox = InboxService(factory)
            wakes: list[str] = []
            queue = _QueueService()
            runner = _PipelineRunner()
            llm = FakeLLM(
                [
                    # 1. 长输出 + 4 步计划同一批返回。
                    tool_response(
                        tool_call("call-long", "get_long_text", index=0),
                        tool_call(
                            "call-plan",
                            "update_plan",
                            {"title": PLAN_TITLE, "items": PLAN_ITEMS},
                            index=1,
                        ),
                    ),
                    text_response("计划已提交，等待批准。"),
                    # 2. 批准前提交流水线：被计划中间件拦下。
                    tool_response(
                        tool_call("call-submit", "submit_pipeline", SUBMIT_ARGS)
                    ),
                    text_response("计划还没批准，先等用户。"),
                    # 3. 批准后同一个调用：真正到达工具 handler。
                    tool_response(
                        tool_call("call-submit", "submit_pipeline", SUBMIT_ARGS)
                    ),
                    text_response("流水线已提交。"),
                ]
            )
            state = SimpleNamespace(
                session_factory=factory,
                agent_runtime=runtime,
                model_router=_ModelRouter(llm),
                run_event_bus=RunEventBus(),
                run_service=RunService(factory, boot_id="boot-test"),
                plan_service=plan_service,
                artifact_service=artifacts,
                confirmation_service=confirmations,
                inbox_service=inbox,
                worker_service=SimpleNamespace(wake=wakes.append),
                queue_service=queue,
                pipeline_runner=runner,
                context_layout=ContextLayout(TokenEstimator()),
            )
            app = SimpleNamespace(state=state)
            base_spec = builtin_spec("general")
            spec = base_spec.model_copy(
                update={"tools": (*base_spec.tools, "get_long_text")}
            )

            async def run_once() -> Any:
                session, run = await _seed_worker_run(factory, session_id)
                return await run_agent(
                    app,
                    spec,
                    session,
                    run,
                    inbox_items=[],
                    cancel_event=asyncio.Event(),
                    cancel_reason=lambda: "interrupted",
                    state=dict(MANDATE_STATE),
                )

            # ---- 1. 外置：下一次模型请求只有指针 ----
            outcome = await run_once()
            assert outcome.status == "succeeded"

            # 第一次请求（索引 0）发工具调用；第二次请求（索引 1）带 tool 消息。
            assert len(llm.requests) == 2
            second_request = llm.requests[1][1]
            pointer_text = next(
                _content(message)
                for message in _tool_messages(second_request)
                if "artifact_id" in _content(message)
            )
            pointer = json.loads(pointer_text)
            assert pointer["spilled"] is True
            assert pointer["tool"] == "get_long_text"
            assert pointer["artifact_id"]
            assert pointer["next_offset"] == 3000
            assert pointer["total_chars"] == len(
                json.dumps({"text": LONG_TEXT}, ensure_ascii=False)
            )
            # 规格 §7：tool 消息含 artifact_id，且不含那 20 000 字原文。
            assert LONG_TEXT not in pointer_text
            assert len(pointer_text) < 20_000

            stored = await artifacts.get(pointer["artifact_id"])
            assert stored.artifact.kind == "spill"
            assert stored.content == json.dumps(
                {"text": LONG_TEXT}, ensure_ascii=False
            )

            plan = await plan_service.active_for_session(session_id)
            assert plan is not None
            assert plan.status == "awaiting_approval"
            assert len(plan.items) == 4
            assert all(item["status"] == "pending" for item in plan.items)

            # ---- 2. 批准前：同一 submit_pipeline 失败且不建确认 ----
            assert (await run_once()).status == "succeeded"
            assert len(llm.requests) == 4
            blocked_request = llm.requests[3][1]
            blocked = _json_tool_messages(blocked_request)[-1]
            assert blocked["error"]["code"] == PLAN_APPROVAL_REQUIRED
            assert blocked["error"]["details"]["plan_id"] == str(plan.id)
            assert "pipeline_id" not in _content(_tool_messages(blocked_request)[-1])
            assert queue.submissions == []
            assert await _count(factory, Confirmation) == 0
            async with factory() as db:
                audits = list(
                    (await db.execute(select(AgentAudit))).scalars().all()
                )
            assert "submit_pipeline" not in {audit.tool_name for audit in audits}

            # ---- 3. 真实批准端点：计划转 active，Worker 收件箱收到 system ----
            request = SimpleNamespace(app=app)
            async with factory() as db:
                approved = await approve_plan(
                    request,
                    PlanApprovalRequest(approved=True),
                    session_id=session_id,
                    session=db,
                )
            assert approved.status == "active"
            assert approved.approved_by == "user"
            assert wakes == [session_id]
            async with factory() as db:
                inbox_items = list(
                    (
                        await db.execute(
                            select(AgentInbox).where(
                                AgentInbox.session_id == session_id
                            )
                        )
                    ).scalars().all()
                )
            assert len(inbox_items) == 1
            assert inbox_items[0].kind == "system"
            assert f"用户已批准计划 {plan.id}" in inbox_items[0].payload["text"]

            # ---- 4. 批准后：同一个调用成功到达 handler ----
            assert (await run_once()).status == "succeeded"
            assert len(llm.requests) == 6
            allowed_request = llm.requests[5][1]
            allowed = _json_tool_messages(allowed_request)[-1]
            assert allowed["result"]["pipeline_id"] == "pipeline-t5b"
            assert allowed["result"]["status"] == "queued"
            assert len(queue.submissions) == 1
            assert runner.published == ["pipeline-t5b"]
            assert await _count(factory, Confirmation) == 0
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())
