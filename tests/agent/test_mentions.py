"""M72 T3：用户消息里的 @ 提及展开（规格 §4.2、§6）。

工单与流水线用临时库里的真行，知识 / 干员 / 关卡用替身；每条用例只断言
``render_reference_block`` 的对外输出，不碰路由与模型。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表
from maa_api.agent.mentions import (
    MAX_ITEM_CHARS,
    MentionContext,
    render_reference_block,
)
from maa_api.db import session as db_session
from maa_api.db.models import Pipeline, Task, Ticket, new_uuid
from maa_api.domain.errors import AppError, ErrorCode


# ----------------------------------------------------------------------
# 替身
# ----------------------------------------------------------------------
def _mention(mention_type: str, mention_id: str, label: str) -> SimpleNamespace:
    return SimpleNamespace(type=mention_type, id=mention_id, label=label)


def _knowledge_entry(
    entry_id: str, title: str, when_to_use: str = "需要时", body: str = "正文"
) -> SimpleNamespace:
    return SimpleNamespace(
        id=entry_id, title=title, when_to_use=when_to_use, body=body
    )


class _KnowledgeService:
    """按 id 返回条目；``failures`` 里的 id 抛普通异常。"""

    def __init__(self, entries: dict[str, Any] | None = None, failures=()) -> None:
        self.entries = dict(entries or {})
        self.failures = set(failures)
        self.calls: list[str] = []

    async def read_for_agent(self, entry_id: str) -> Any:
        self.calls.append(entry_id)
        if entry_id in self.failures:
            raise RuntimeError("知识库读取失败")
        entry = self.entries.get(entry_id)
        if entry is None:
            raise AppError(ErrorCode.KNOWLEDGE_NOT_FOUND, "知识条目不存在或已停用")
        return entry


class _BoxService:
    """按 char_id 返回干员 wire；未知的干员抛 ``ACCOUNT_DATA_NOT_FOUND``。"""

    def __init__(self, operators: dict[str, Any] | None = None) -> None:
        self.operators = dict(operators or {})

    async def get_operator(self, char_id: str) -> Any:
        value = self.operators.get(char_id)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise AppError(
                ErrorCode.ACCOUNT_DATA_NOT_FOUND, "账号数据里没有这名干员的记录"
            )
        return value


class _StageResolver:
    """按 key 返回预置的 resolve 结果；没预置的当找不到。"""

    def __init__(self, results: dict[str, dict[str, Any]] | None = None) -> None:
        self.results = dict(results or {})
        self.calls: list[str] = []

    async def resolve(self, key: str) -> dict[str, Any]:
        self.calls.append(key)
        return self.results.get(
            key,
            {
                "query": key,
                "source": "resource",
                "resolved": None,
                "candidates": [],
                "candidates_total": 0,
            },
        )


# ----------------------------------------------------------------------
# 临时库与上下文
# ----------------------------------------------------------------------
@pytest.fixture
def context_factory():
    """``build(knowledge=..., box=..., resolver=...)`` → ``(MentionContext, factory)``。"""
    engines = []

    def build(**state: Any) -> tuple[MentionContext, async_sessionmaker[AsyncSession]]:
        engine = db_session.make_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        factory = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )

        async def create() -> None:
            async with engine.begin() as connection:
                await connection.run_sync(SQLModel.metadata.create_all)

        asyncio.run(create())
        engines.append(engine)
        app_state = SimpleNamespace(
            knowledge_service=state.get("knowledge") or _KnowledgeService(),
            box_service=state.get("box") or _BoxService(),
            stage_resolver=state.get("resolver") or _StageResolver(),
        )
        return MentionContext(app_state, factory), factory

    yield build
    for engine in engines:
        engine.sync_engine.dispose()


async def _seed_ticket(factory, **overrides: Any) -> Ticket:
    values = {
        "id": "ticket-1",
        "title": "日常",
        "kind": "daily_ops",
        "status": "open",
        "origin": "user",
        "summary": "最近一次汇报",
    }
    values.update(overrides)
    ticket = Ticket(**values)
    async with factory() as session:
        session.add(ticket)
        await session.commit()
    return ticket


async def _seed_pipeline(factory, **overrides: Any) -> Pipeline:
    values = {
        "title": "日常流水线",
        "source": "manual",
        "priority": 1,
        "task_count": 2,
        "status": "completed",
        "created_at": datetime(2026, 11, 5, 8, 0, 0),
    }
    values.update(overrides)
    pipeline = Pipeline(id=str(values.pop("id", new_uuid())), **values)
    async with factory() as session:
        session.add(pipeline)
        for index, status in enumerate(("completed", "failed")):
            session.add(
                Task(
                    pipeline_id=pipeline.id,
                    order_index=index,
                    type_name="Fight",
                    task_name=f"任务{index}",
                    status=status,
                )
            )
        await session.commit()
    return pipeline


def _lines(block: str) -> list[str]:
    return [line for line in block.splitlines() if line.startswith("- ")]


# ----------------------------------------------------------------------
# §6 的七条
# ----------------------------------------------------------------------
def test_render_reference_block_formats_each_type(context_factory):
    knowledge = _KnowledgeService(
        {
            "k-1": _knowledge_entry(
                "k-1", "刷关知识", "刷关前用", "第一行\n第二行"
            )
        }
    )
    box = _BoxService(
        {
            "char_136_hsguma": {
                "name": "星熊",
                "char_id": "char_136_hsguma",
                "own": True,
                "elite": 2,
                "level": 90,
                "potential": 6,
                "skill_level": 7,
                "masteries": {"1": 2, "2": 0, "3": 3},
                "modules": {"X": 2},
                "recognized_at": "2026-11-01T00:00:00Z",
            }
        }
    )
    resolver = _StageResolver(
        {
            "1-7": {
                "query": "1-7",
                "source": "resource",
                "resolved": {
                    "code": "1-7",
                    "stage_id": "main_01-07",
                    "name": "暴君",
                },
                "candidates": [
                    {"code": "1-7", "stage_id": "main_01-07", "name": "暴君"}
                ],
                "candidates_total": 1,
            }
        }
    )
    ctx, factory = context_factory(
        knowledge=knowledge, box=box, resolver=resolver
    )

    async def scenario():
        ticket = await _seed_ticket(factory)
        pipeline = await _seed_pipeline(factory)
        mentions = [
            _mention("ticket", ticket.id, "日常"),
            _mention("knowledge", "k-1", "刷关知识"),
            _mention("operator", "char_136_hsguma", "星熊"),
            _mention("stage", "1-7", "1-7"),
            _mention("pipeline", pipeline.id, "日常流水线"),
        ]
        return pipeline, await render_reference_block(mentions, ctx)

    pipeline, block = asyncio.run(scenario())

    assert block.startswith("\n\n【引用】\n")
    lines = _lines(block)
    assert len(lines) == 5
    assert [line[:4] for line in lines] == [
        "- 工单",
        "- 知识",
        "- 干员",
        "- 关卡",
        "- 流水",
    ]
    assert lines[0] == "- 工单「日常」（open）：最近一次汇报"
    assert lines[1] == (
        "- 知识「刷关知识」（k-1）：刷关前用。正文节选：第一行 第二行"
    )
    assert lines[2] == (
        "- 干员 星熊（char_136_hsguma）：持有 是，精英化 2，等级 90，"
        "潜能 6，技能等级 7，专精 2/0/3，模组 X2，"
        "练度同步于 2026-11-01T00:00:00Z"
    )
    assert lines[3] == "- 关卡 1-7（暴君，main_01-07）"
    assert lines[4] == (
        f"- 流水线 日常流水线（{pipeline.id[:8]}，completed）："
        "1/2 个任务完成；创建于 2026-11-05T08:00:00Z"
    )


def test_failed_expansion_reads_as_read_failed(context_factory):
    knowledge = _KnowledgeService(
        {"k-ok": _knowledge_entry("k-ok", "好知识")}, failures={"k-boom"}
    )
    box = _BoxService(
        {
            "char_1": {
                "name": "阿米娅",
                "char_id": "char_1",
                "own": False,
            }
        }
    )
    ctx, _factory = context_factory(knowledge=knowledge, box=box)
    mentions = [
        _mention("ticket", "missing-ticket", "幽灵工单"),
        _mention("knowledge", "k-boom", "坏知识"),
        _mention("operator", "char_1", "阿米娅"),
    ]

    block = asyncio.run(render_reference_block(mentions, ctx))

    lines = _lines(block)
    assert lines[0] == "- 工单「幽灵工单」（读取失败）"
    assert lines[1] == "- 知识「坏知识」（读取失败）"
    assert lines[2].startswith("- 干员 阿米娅（char_1）：持有 否")


def test_operator_not_in_account_data(context_factory):
    ctx, _factory = context_factory()
    mention = _mention("operator", "char_003_kalts", "凯尔希")

    block = asyncio.run(render_reference_block([mention], ctx))

    assert _lines(block) == ["- 干员 凯尔希：账号数据里没有这名干员"]


def test_stage_candidates_and_not_found(context_factory):
    resolver = _StageResolver(
        {
            "1-7": {
                "query": "1-7",
                "source": "resource",
                "resolved": None,
                "candidates": [
                    {"stage_id": "s1"},
                    {"stage_id": "s2"},
                ],
                "candidates_total": 2,
            }
        }
    )
    ctx, _factory = context_factory(resolver=resolver)

    block = asyncio.run(
        render_reference_block(
            [
                _mention("stage", "1-7", "1-7"),
                _mention("stage", "unknown", "不存在"),
            ],
            ctx,
        )
    )

    assert _lines(block) == [
        "- 关卡 1-7：有 2 个同名候选（s1、s2），需要时用 stageId 再查",
        "- 关卡 不存在：本地关卡索引里找不到",
    ]


def test_mentions_are_deduplicated_and_capped(context_factory):
    entries = {
        f"k-{index}": _knowledge_entry(f"k-{index}", f"知识{index}")
        for index in range(1, 11)
    }
    knowledge = _KnowledgeService(entries)
    ctx, _factory = context_factory(knowledge=knowledge)
    mentions = [_mention("knowledge", "k-1", "知识1")]
    mentions += [_mention("knowledge", f"k-{index}", f"知识{index}") for index in range(2, 11)]

    block = asyncio.run(render_reference_block(mentions, ctx))

    assert knowledge.calls == [f"k-{index}" for index in range(1, 9)]
    lines = _lines(block)
    assert len(lines) == 9
    assert lines[-1] == "- 另有 2 条引用没有展开"
    assert block.count("（k-1）") == 1


def test_empty_mentions_return_empty_block(context_factory):
    ctx, _factory = context_factory()

    assert asyncio.run(render_reference_block(None, ctx)) == ""
    assert asyncio.run(render_reference_block([], ctx)) == ""


def test_long_line_is_truncated(context_factory):
    entry = _knowledge_entry(
        "k-long",
        "长标题" * 100,
        "使用时机" * 100,
        "正文" * 300,
    )
    ctx, _factory = context_factory(knowledge=_KnowledgeService({"k-long": entry}))

    block = asyncio.run(
        render_reference_block([_mention("knowledge", "k-long", "长知识")], ctx)
    )

    lines = _lines(block)
    assert len(lines) == 1
    assert lines[0].endswith("…")
    assert len(lines[0][2:]) == MAX_ITEM_CHARS
