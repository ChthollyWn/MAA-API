"""M70 T4：共用约定、角色卡与系统提示分层（规格 §4.5.9、§4.5.10、§6）。

五条用例覆盖：Worker 目录只列非 legacy 定义、工单卡与任务卡的内容、三种收件箱
前缀、``legacy_chat`` 系统提示逐字节不变、``background`` 不拼共用约定。
"""

from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

from maa_api.agent.harness.builtin_middleware import ContextLayoutMiddleware
from maa_api.agent.harness.context import ModelRequest, RunContext
from maa_api.agent.specs.registry import (
    builtin_common_prompt,
    builtin_spec,
    builtin_specs,
)
from maa_api.agent.specs.schema import AgentSpec
from maa_api.agent.workers.cards import (
    effective_ceiling,
    render_inbox_item,
    task_card,
    ticket_card,
    worker_catalog_card,
)
from maa_api.db.models import AgentInbox, Mandate, Ticket
from maa_api.domain.enums import DynamicContextPosition, InboxKind

#: 改动前 ``legacy_chat`` 组装出的 system 消息（固定知识段、状态摘要与历史）的 sha256。
LEGACY_PROMPT_SHA = "efe14f204c53b54ef50a6057254d95b75bea84545f0e565b648af2a2e0ea3bb8"

_LEGACY_KNOWLEDGE = "知识库：M70 快照的固定指引。"
_LEGACY_STATUS = {"now": "2026-01-01T00:00:00+00:00", "sessions": 1, "pending": []}


class _FakeRegistry:
    """只实现 ``cards`` 用到的 ``list`` / ``get``。"""

    def __init__(self, specs: list[AgentSpec]) -> None:
        self._specs = {spec.name: spec for spec in specs}

    def list(self) -> list[AgentSpec]:
        return list(self._specs.values())

    def get(self, name: str) -> AgentSpec:
        return self._specs[name]


def _spec(
    name: str,
    *,
    kind: str,
    title: str,
    description: str = "测试定义",
    body: str | None = None,
    delegates: tuple[str, ...] = (),
    risk_ceiling: str = "none",
    model_role: str = "worker",
    output_schema: str | None = None,
) -> AgentSpec:
    return AgentSpec(
        name=name,
        title=title,
        kind=kind,  # type: ignore[arg-type]
        description=description,
        model_role=model_role,  # type: ignore[arg-type]
        tools=(),
        deferred_groups=(),
        device="none",
        risk_ceiling=risk_ceiling,  # type: ignore[arg-type]
        max_steps=0,
        delegates=delegates,
        skills=(),
        output_schema=output_schema,
        version=1,
        body=body or f"你是{title}。",
        spec_version="0" * 12,
        source_file=f"{name}.md",
    )


def _subagents() -> list[AgentSpec]:
    return [
        _spec("navigator", kind="subagent", title="界面导航", model_role="subagent"),
        _spec("recon", kind="subagent", title="侦察", model_role="subagent"),
        _spec("researcher", kind="subagent", title="资料检索", model_role="subagent"),
        _spec("verifier", kind="subagent", title="结果核对", model_role="subagent"),
    ]


def _system_prompt(spec: AgentSpec, *, role_card: str | None = None) -> str:
    """按 ``ContextLayoutMiddleware`` 的非 legacy 路径组装 system 正文。"""
    ctx = RunContext(
        run_id="run-cards",
        root_run_id="run-cards",
        parent_run_id=None,
        session_id="session-cards",
        ticket_id="ticket-cards",
        spec=spec,
        vision_enabled=False,
    )
    ctx.state["model"] = {
        "profile": SimpleNamespace(
            key="cards-test",
            dynamic_context_position=DynamicContextPosition.TAIL_SYSTEM.value,
        )
    }
    ctx.state[ContextLayoutMiddleware.name] = {
        "stable": "",
        "dynamic": "",
        "status": {"now": "2026-01-01T00:00:00+00:00"},
    }
    if role_card is not None:
        ctx.state["role_card"] = role_card
    updated = asyncio.run(
        ContextLayoutMiddleware().before_model(
            ctx, ModelRequest(messages=[], tools=[])
        )
    )
    return updated.messages[0]["content"]


def test_worker_catalog_lists_only_non_legacy_workers():
    general = _spec(
        "general",
        kind="worker",
        title="通用执行",
        description="什么都能做的执行代理",
        risk_ceiling="destructive",
    )
    card = worker_catalog_card(
        _FakeRegistry([*builtin_specs().values(), general])
    )

    assert card == (
        "## 可用的 Worker\n\n"
        "建工单时 kind 从下面选：\n"
        "- general（通用执行）：什么都能做的执行代理"
    )
    assert "legacy_chat" not in card
    assert "legacy_incident" not in card
    assert "recon" not in card


def test_ticket_card_shows_scope_ceiling_and_delegates():
    registry = _FakeRegistry(
        [
            # 先放真实定义，再用测试替身覆盖同名项（M70 起内置定义里已有同名子代理）。
            *builtin_specs().values(),
            _spec(
                "general",
                kind="worker",
                title="通用执行",
                description="什么都能做的执行代理",
                delegates=("recon", "researcher", "navigator", "verifier"),
                risk_ceiling="consume",
            ),
            *_subagents(),
        ]
    )
    spec = registry.get("general")
    ticket = Ticket(
        id="ticket-1",
        title="刷 1-7 五次",
        kind="general",
        origin="user",
    )
    mandate = Mandate(
        ticket_id="ticket-1",
        scope="只刷 1-7，五次",
        strength="explicit",
        source="web",
        risk_ceiling="consume",
    )

    card = ticket_card(ticket, mandate, spec, registry)

    assert "- 工单：「刷 1-7 五次」，ticket_id=ticket-1" in card
    assert "- 来源：用户；委托：用户明确要求" in card
    assert "- 委托范围：只刷 1-7，五次" in card
    assert "- 风险上限：消耗类：可以发起消耗类确认，不能发起破坏类" in card
    for name, title in (
        ("recon", "侦察"),
        ("researcher", "资料检索"),
        ("navigator", "界面导航"),
        ("verifier", "结果核对"),
    ):
        assert f"- {name}（{title}）：" in card

    inferred_ticket = Ticket(
        id="ticket-2",
        title="巡检失败",
        kind="general",
        origin="trigger",
    )
    inferred = Mandate(
        ticket_id="ticket-2",
        scope="查清失败原因",
        strength="inferred",
        source="pipeline",
        risk_ceiling="none",
    )
    inferred_card = ticket_card(inferred_ticket, inferred, spec, registry)
    assert "- 来源：事件；委托：系统推断" in inferred_card
    assert "- 风险上限：无需确认：只能做不需要用户确认的操作" in inferred_card

    assert effective_ceiling("destructive", "consume") == "consume"
    assert effective_ceiling("consume", None) == "consume"
    assert effective_ceiling("none", "destructive") == "none"

    task = task_card(
        "查一下 1-7 的掉率",
        None,
        ticket_title="刷 1-7 五次",
        parent_title="通用执行",
    )
    assert task == (
        "## 任务卡\n\n"
        "- 任务：查一下 1-7 的掉率\n"
        "- 已知事实：无\n"
        "- 来自：工单「刷 1-7 五次」的通用执行"
    )


def test_inbox_rendering_uses_the_three_prefixes():
    report = AgentInbox(
        session_id="orchestrator",
        kind=InboxKind.REPORT,
        thread_key="ticket:ticket-1",
        ref_id="3",
        payload={
            "ticket_id": "ticket-1",
            "ticket_title": "刷 1-7 五次",
            "ticket_kind": "legacy_chat",
            "report_id": 3,
            "status": "ready",
            "summary": "完成 5 次",
            "human_action": {"type": "question", "prompt": "要继续吗"},
        },
    )
    event = AgentInbox(
        session_id="orchestrator",
        kind=InboxKind.THREAD_EVENT,
        thread_key="pipeline:42",
        payload={"summary": "流水线已完成"},
    )
    system = AgentInbox(
        session_id="orchestrator",
        kind=InboxKind.SYSTEM,
        payload={"text": "用户在工单里说了话", "passive": True},
    )
    user = AgentInbox(
        session_id="orchestrator",
        kind=InboxKind.USER_MESSAGE,
        payload={"text": "你好"},
    )

    rendered_report = render_inbox_item(report)
    assert rendered_report is not None
    assert rendered_report.startswith(
        "【工单汇报】工单「刷 1-7 五次」（旧版会话，ticket_id=ticket-1）已完成：完成 5 次"
    )
    assert rendered_report.endswith("\n需要用户处理（回答）：要继续吗")
    assert render_inbox_item(event) == "【事件】pipeline:42：流水线已完成"
    assert render_inbox_item(system) == "【系统】用户在工单里说了话"
    assert render_inbox_item(user) is None


def test_legacy_chat_system_prompt_is_unchanged():
    ctx = RunContext(
        run_id="run-legacy",
        root_run_id="run-legacy",
        parent_run_id=None,
        session_id="session-legacy",
        ticket_id=None,
        spec=builtin_spec("legacy_chat"),
        vision_enabled=True,
    )
    ctx.state[ContextLayoutMiddleware.name] = {
        "knowledge_context": _LEGACY_KNOWLEDGE,
        "status_summary": _LEGACY_STATUS,
        "max_context_chars": None,
    }

    updated = asyncio.run(
        ContextLayoutMiddleware().before_model(
            ctx, ModelRequest(messages=[{"role": "user", "content": "你好"}], tools=[])
        )
    )
    content = updated.messages[0]["content"]

    assert hashlib.sha256(content.encode("utf-8")).hexdigest() == LEGACY_PROMPT_SHA
    assert "## 共用约定" not in content
    assert content.startswith(builtin_spec("legacy_chat").body)


def test_background_agents_skip_common_prompt():
    common = builtin_common_prompt()
    titler = _spec(
        "titler",
        kind="background",
        title="标题生成",
        model_role="title",
        output_schema="ticket_title",
        body="你是标题生成器。",
    )

    background_prompt = _system_prompt(titler)
    assert background_prompt.startswith("你是标题生成器。")
    assert "## 共用约定" not in background_prompt
    assert common not in background_prompt

    general = _spec(
        "general",
        kind="worker",
        title="通用执行",
        description="什么都能做的执行代理",
    )
    role_card = "## 工单卡\n\n- 工单：「刷 1-7 五次」，ticket_id=ticket-1"
    worker_prompt = _system_prompt(general, role_card=role_card)
    assert worker_prompt.startswith(f"{general.body}\n\n{common}\n\n{role_card}")
