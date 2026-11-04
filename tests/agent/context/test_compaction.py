"""M74 T6：压缩的纯函数与检查点累积（spec §4.2.5、§4.5.5、§6 的六条用例）。

前五条只测纯函数；最后一条用真临时库 + 假摘要模型，钉住「检查点是累积的、同一
会话只有一个 active」这两条事务性质。异步用例沿用 ``asyncio.run(scenario())``。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sqlalchemy import select

from maa_api.agent.context.compaction import (
    MIN_KEEP_TOOL_ROUNDS,
    PROTECTED_MAX_RATIO,
    Compactor,
    complete_refs,
    mechanical_summary,
    render_checkpoint_message,
    select_upto_seq,
)
from maa_api.agent.context.durable import DurableState
from maa_api.agent.context.layout import ContextLayout
from maa_api.agent.context.tokens import TokenEstimator
from maa_api.agent.llm.profiles import ResolvedProfile
from maa_api.agent.llm.schemas.compaction_summary import CompactionSummary, Ref
from maa_api.agent.specs.registry import builtin_spec
from maa_api.db.models import AgentMessage, CompactionCheckpoint
from maa_api.db.repositories.compaction import CompactionCheckpointRepository
from tests.fakes.v3_db import make_session_factory, seed_session

#: 本文件所有压缩用例共用的档案：窗口足够大，避免短历史被长单轮保护挡下。
_PROFILE = ResolvedProfile(
    name="compaction",
    kind="chat",
    provider="openai_compatible",
    base_url="https://llm.test/v1",
    api_key="sk-test",
    model="compaction-model",
    context_window=100_000,
    max_output_tokens=None,
    supports_vision=None,
    supports_tools=True,
    supports_json_schema=None,
    dynamic_context_position="tail_system",
    price_input=None,
    price_cached_input=None,
    price_output=None,
    currency="CNY",
    embedding_dim=None,
    local_model_path=None,
    readonly=False,
)


def _settings(*, keep_recent_turns: int = 2):
    return SimpleNamespace(
        context=SimpleNamespace(
            fallback_context_tokens=128_000,
            compaction_ratio=0.75,
            keep_recent_turns=keep_recent_turns,
            clear_tool_results_after=3,
        )
    )


def _message(seq: int, role: str, content: str | None = None, **extra) -> AgentMessage:
    return AgentMessage(session_id="session-compaction", seq=seq, role=role, content=content, **extra)


def _tool_round(seq: int, *, tool: str = "get_logs", content: str = "x" * 3000):
    """一个工具往返：assistant(tool_calls) + tool；返回两条消息与新 seq。"""
    call_id = f"call-{seq}"
    assistant = _message(
        seq,
        "assistant",
        content=None,
        tool_calls=[
            {
                "id": call_id,
                "type": "function",
                "function": {"name": tool, "arguments": "{}"},
            }
        ],
    )
    reply = _message(seq + 1, "tool", content=content, tool_call_id=call_id, tool_name=tool)
    return [assistant, reply], seq + 2


def test_select_keeps_recent_turns_and_tool_pairs() -> None:
    messages: list[AgentMessage] = []
    seq = 0
    for turn in range(3):
        messages.append(_message(seq, "user", f"第 {turn} 轮"))
        seq += 1
        round_messages, seq = _tool_round(seq)
        messages.extend(round_messages)

    upto_seq, active = select_upto_seq(
        messages,
        keep_recent_turns=2,
        window=_PROFILE.context_window,
        estimator=TokenEstimator(),
    )
    # 3 个用户轮次、保留最近 2 轮：边界是第二个用户轮次之前的那条消息。
    second_user_index = 3
    assert upto_seq == messages[second_user_index - 1].seq
    assert active is None
    # 边界之后的第一条是 user（边界不拆开工具配对）。
    boundary = next(index for index, message in enumerate(messages) if message.seq == upto_seq)
    assert messages[boundary + 1].role == "user"
    assert messages[boundary + 1].content == "第 1 轮"


def test_long_single_turn_shrinks_protection() -> None:
    messages: list[AgentMessage] = [_message(0, "user", "简报：刷 1-7 到 50 个固源岩")]
    seq = 1
    for _ in range(30):
        round_messages, seq = _tool_round(seq)
        messages.extend(round_messages)

    upto_seq, active = select_upto_seq(
        messages,
        keep_recent_turns=2,
        # 30 个 3 000 字工具结果远超 0.5 × 5 000。
        window=5_000,
        estimator=TokenEstimator(),
    )
    assert active is messages[0]
    assert active.content.startswith("简报")
    # 只保护最近 4 个完整工具往返，边界落在第 4 个往返之前的 tool 消息上。
    kept_rounds = MIN_KEEP_TOOL_ROUNDS
    start = len(messages) - kept_rounds * 2
    assert upto_seq == messages[start - 1].seq
    assert messages[start].role == "assistant"
    assert messages[start].tool_calls
    assert 0.5 == PROTECTED_MAX_RATIO


def test_complete_refs_adds_missing_ids() -> None:
    summary = CompactionSummary(
        summary="已经做过一些事",
        refs=[Ref(kind="ticket", id="t-1", label="create_ticket")],
    )
    messages = [
        _message(0, "user", "开始"),
        _message(1, "assistant", content=None),
        _message(
            2,
            "tool",
            content='{"result": {"pipeline_id": "p-1", "confirmation_id": "c-1"}}',
            tool_call_id="call-1",
            tool_name="submit_pipeline",
        ),
        _message(
            3,
            "tool",
            content='{"result": {"ticket_id": "t-1"}}',
            tool_call_id="call-2",
            tool_name="create_ticket",
        ),
        _message(
            4,
            "tool",
            content='{"result": {"screenshot_id": "shot-1", "artifact_id": "a-1"}}',
            tool_call_id="call-3",
            tool_name="get_screenshot",
        ),
    ]

    completed = complete_refs(summary, messages)
    pairs = {(ref.kind, ref.id) for ref in completed.refs}
    assert ("pipeline", "p-1") in pairs
    assert ("confirmation", "c-1") in pairs
    assert ("screenshot", "shot-1") in pairs
    assert ("artifact", "a-1") in pairs
    # 已有的 ticket t-1 不重复。
    assert ("ticket", "t-1") in pairs
    assert len(completed.refs) == 5
    assert {ref.label for ref in completed.refs if ref.id == "p-1"} == {"submit_pipeline"}


def test_mechanical_summary_shape() -> None:
    messages = [
        _message(0, "user", "刷 1-7 到 50 个固源岩"),
        _message(1, "assistant", content=None),
        _message(
            2,
            "tool",
            content='{"result": {"pipeline_id": "p-1"}}',
            tool_call_id="call-1",
            tool_name="submit_pipeline",
        ),
        _message(
            3,
            "tool",
            content='{"error": {"code": "UPDATE_FAILED", "message": "更新失败"}}',
            tool_call_id="call-2",
            tool_name="update_core",
        ),
        _message(
            4,
            "tool",
            content='{"result": "ok"}',
            tool_call_id="call-3",
            tool_name="get_logs",
        ),
        _message(
            5,
            "tool",
            content='{"result": {"question_id": "q-1"}}',
            tool_call_id="call-4",
            tool_name="ask_user",
        ),
    ]

    summary = mechanical_summary(messages, is_refetchable=lambda name: name == "get_logs")
    assert isinstance(summary, CompactionSummary)
    assert summary.summary == "较早的消息已按规则压缩，细节可以按 ID 重新查询"
    assert [(item.seq, item.text, item.status) for item in summary.user_requests] == [
        (0, "刷 1-7 到 50 个固源岩", "pending")
    ]
    # 只有有副作用的工具进 actions；可重取的 get_logs 与 ask_user 不进。
    assert [action.tool for action in summary.actions] == ["submit_pipeline", "update_core"]
    assert [action.outcome for action in summary.actions] == ["ok", "error"]


def test_checkpoint_message_rendering() -> None:
    summary_json = {
        "summary": "已经刷完 1-7，正在等用户确认是否用源石。",
        "user_requests": [
            {"seq": 0, "text": "刷 1-7", "status": "in_progress"},
            # 空列表的小节不出现。
            {"seq": None, "text": "", "status": "pending"},
        ],
        "facts": [{"text": "1-7 掉固源岩", "source": "get_stage", "seq": 3}],
        "refs": [{"kind": "ticket", "id": "t-1", "label": "create_ticket"}],
        "next_step": "等用户回答",
    }
    durable = DurableState(
        captured_at="2026-11-04T12:00:00+08:00",
        ticket={"id": "t-1", "title": "刷 1-7", "status": "open"},
    )

    text = render_checkpoint_message(summary_json, durable, upto_seq=12)
    assert text.startswith("【压缩摘要】")
    assert "seq ≤ 12" in text
    assert "【压缩时的状态】" in text
    assert "1-7 掉固源岩" in text
    assert "ticket t-1" in text
    assert "下一步：等用户回答" in text
    # 没有内容的小节整节不写。
    assert "已定的事" not in text
    assert "操作" not in text
    assert "错误" not in text

    empty = render_checkpoint_message(
        {"summary": "只有叙述"}, DurableState(captured_at="2026-11-04T12:00:00+08:00"), 3
    )
    assert empty.startswith("【压缩摘要】")
    assert "【压缩时的状态】" in empty
    assert "下一步" not in empty


class _Registry:
    def __init__(self, refetchable: set[str]) -> None:
        self._refetchable = set(refetchable)

    def get(self, name: str) -> SimpleNamespace:
        return SimpleNamespace(refetchable=name in self._refetchable)


class _Router:
    def __init__(self, profile: ResolvedProfile) -> None:
        self.profile = profile

    async def resolve(self, role: str) -> ResolvedProfile:
        assert role == "summarizer"
        return self.profile

    async def resolve_for_spec(self, spec: object, **_: object) -> ResolvedProfile:
        return self.profile


def _app_state(factory, router, registry) -> SimpleNamespace:
    return SimpleNamespace(
        session_factory=factory,
        context_layout=ContextLayout(TokenEstimator()),
        model_router=router,
        cost_ledger=None,
        run_service=None,
        agent_spec_registry=SimpleNamespace(get=builtin_spec),
        confirmation_service=SimpleNamespace(registry=registry),
    )


async def _seed_messages(factory, session_id: str, messages: list[dict]) -> None:
    async with factory() as db:
        for row in messages:
            db.add(AgentMessage(session_id=session_id, **row))
        await db.commit()


def _turns(session_id: str, start_seq: int, count: int) -> tuple[list[dict], int]:
    rows: list[dict] = []
    seq = start_seq
    for turn in range(count):
        rows.append({"seq": seq, "role": "user", "content": f"第 {turn} 轮要求"})
        seq += 1
        rows.append(
            {
                "seq": seq,
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call-{seq}",
                        "type": "function",
                        "function": {"name": "get_logs", "arguments": "{}"},
                    }
                ],
            }
        )
        seq += 1
        rows.append(
            {
                "seq": seq,
                "role": "tool",
                "content": '{"result": "ok"}',
                "tool_call_id": f"call-{seq - 1}",
                "tool_name": "get_logs",
            }
        )
        seq += 1
    return rows, seq


def test_checkpoints_are_cumulative(tmp_path, monkeypatch) -> None:
    engine, factory = asyncio.run(make_session_factory(tmp_path / "cumulative.db"))

    async def scenario() -> None:
        session_id = await seed_session(factory, kind="worker")
        rows, seq = _turns(session_id, 0, 4)
        await _seed_messages(factory, session_id, rows)

        settings = _settings()
        calls: list[dict] = []

        async def fake_complete_structured(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                value=CompactionSummary(summary=f"第 {len(calls)} 批摘要")
            )

        monkeypatch.setattr(
            "maa_api.agent.context.compaction.get_settings", lambda: settings
        )
        monkeypatch.setattr(
            "maa_api.agent.context.compaction.complete_structured",
            fake_complete_structured,
        )

        broadcasts: list[tuple[str, object]] = []
        state = _app_state(
            factory,
            _Router(_PROFILE),
            _Registry({"get_logs"}),
        )
        compactor = Compactor(
            state, factory, broadcast=lambda message_type, data: broadcasts.append((message_type, data))
        )

        first = await compactor.compact(
            session_id=session_id,
            session_kind="worker",
            ticket_id=None,
            spec=builtin_spec("general"),
            profile=_PROFILE,
            trigger="auto",
        )
        assert first.skipped is False
        assert first.checkpoint_id is not None
        assert "第 0 轮要求" in calls[0]["messages"][1]["content"]

        # 第一次压缩之后又来了两个用户轮次：现在有 4 个轮次可压缩到旧的摘要里。
        extra, _seq = _turns(session_id, seq, 2)
        await _seed_messages(factory, session_id, extra)

        second = await compactor.compact(
            session_id=session_id,
            session_kind="worker",
            ticket_id=None,
            spec=builtin_spec("general"),
            profile=_PROFILE,
            trigger="auto",
        )
        assert second.skipped is False
        assert second.checkpoint_id != first.checkpoint_id
        # 第二次摘要的输入带上了第一次的摘要（累积）。
        second_input = calls[1]["messages"][1]["content"]
        assert "【已有摘要】" in second_input
        assert "第 1 批摘要" in second_input

        async with factory() as db:
            repo = CompactionCheckpointRepository(db)
            active = await repo.active_for_session(session_id)
            assert active is not None
            assert active.id == second.checkpoint_id
            assert active.summary_json["previous_checkpoint_id"] == first.checkpoint_id
            all_checkpoints = list(
                (await db.execute(select(CompactionCheckpoint))).scalars().all()
            )
            assert len(all_checkpoints) == 2
            assert sorted(str(item.status) for item in all_checkpoints) == [
                "active",
                "superseded",
            ]
        # 自动与手动都发 WS 事件。
        assert [item[0] for item in broadcasts] == ["agent_event", "agent_event"]
        assert all(item[1]["event"] == "compaction_created" for item in broadcasts)

    try:
        asyncio.run(scenario())
    finally:
        engine.sync_engine.dispose()
