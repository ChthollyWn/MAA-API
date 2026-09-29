"""M18 Task 4: the knowledge store merges built-in Markdown with user overrides.

规格 §4.1。三条纪律：

- 内置条目随代码发布，``agent_knowledge`` 表只存用户的改动；覆盖行只保存与内置
  **不同**的字段，其余列为 NULL，读取时继承内置值。
- 各项上限按**写入后的生效状态**判定（不是按请求体）：启用条目 ≤ 50、启用的
  glossary + profile 正文合计 ≤ 3,000 字。
- 内置文件解析失败要 fail loud，不能静默跳过某一条。

本目录没有 pytest-asyncio（不要为此加插件），异步用例统一写成「同步测试函数 +
``asyncio.run(scenario())``」。
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from maa_api.agent.knowledge import (
    BuiltinEntry,
    KnowledgeBuiltinError,
    KnowledgeService,
    KnowledgeWrite,
    builtin_entries,
    load_builtins,
)
from maa_api.db import session as db_session
from maa_api.db.models import AgentKnowledge
from maa_api.domain.errors import AppError, ErrorCode

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = (
    REPO_ROOT / "docs" / "superpowers" / "specs" / "2026-09-29-m18-agent-knowledge.md"
)

BUILTIN_IDS = [
    "account-resources",
    "screen-home",
    "screen-depot",
    "gacha-conversion",
    "glossary",
]

INDEX_HEADER = (
    "知识库索引（开发者与用户维护，可作为操作指引；需要时用 read_knowledge 读取全文）："
)
GLOSSARY_HEADER = "术语表（开发者与用户维护）："
PROFILE_HEADER = "用户档案（用户维护）："

#: 五条内置条目在 ``list_entries()`` 里的顺序：先 kind（playbook/screen/rule/
#: glossary/profile），同 kind 内按 id 升序。
BUILTIN_LIST_ORDER = [
    "account-resources",
    "screen-depot",
    "screen-home",
    "gacha-conversion",
    "glossary",
]

MAX_ENABLED_ENTRIES = 50
MAX_FULLTEXT_CHARS = 3_000
FIELD_PAYLOAD = {"kind": "rule", "title": "一", "when_to_use": "一", "body": "正文"}


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------
async def _create_all(engine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


@pytest.fixture
def harness(tmp_path):
    """``(service, session_factory)``，绑定在一次性 SQLite 文件上。"""
    engine = db_session.make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'knowledge.db'}", poolclass=NullPool
    )
    asyncio.run(_create_all(engine))
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield KnowledgeService(factory), factory
    engine.sync_engine.dispose()


async def _rows(factory) -> dict[str, AgentKnowledge]:
    async with factory() as session:
        result = await session.execute(select(AgentKnowledge))
        return {row.id: row for row in result.scalars().all()}


async def _enabled(service) -> int:
    return sum(1 for item in await service.list_entries() if item.enabled)


async def _seed_row(factory, **values) -> None:
    """直接写一行，模拟「上一个版本留下的自定义条目」。"""
    async with factory() as session:
        session.add(AgentKnowledge(**values))
        await session.commit()


def _write(fields: dict[str, object], body: str = "正文") -> str:
    lines = ["---"]
    lines += [f"{key}: {value}" for key, value in fields.items()]
    lines += ["---", body]
    return "\n".join(lines) + "\n"


def _spec_blocks() -> dict[str, str]:
    """规格 §4.6 的五个 markdown 代码块，按 frontmatter 里的 id 索引。"""
    text = SPEC_PATH.read_text(encoding="utf-8")
    section = text[text.index("### 4.6") : text.index("## 5. 错误与兼容")]
    blocks = re.findall(r"```markdown\n(.*?)```", section, re.DOTALL)
    assert len(blocks) == len(BUILTIN_IDS)
    keyed: dict[str, str] = {}
    for block in blocks:
        match = re.search(r"^id: (\S+)$", block, re.MULTILINE)
        assert match is not None, block[:40]
        keyed[match.group(1)] = block
    return keyed


def _builtin_path(entry_id: str) -> Path:
    return REPO_ROOT / "maa_api" / "agent" / "knowledge" / "builtin" / f"{entry_id}.md"


# ---------------------------------------------------------------------------
# 内置条目：内容、加载与 fail loud
# ---------------------------------------------------------------------------
def test_builtin_files_are_copied_verbatim_from_the_spec():
    """§4.6 是内置内容的原文：frontmatter 与正文（含标点与层级）逐字一致。"""
    for entry_id, block in _spec_blocks().items():
        path = _builtin_path(entry_id)
        assert path.exists(), path
        assert path.read_text(encoding="utf-8").rstrip("\n") == block.rstrip("\n"), entry_id


def test_builtin_entries_are_loaded_once_with_the_documented_ids():
    entries = builtin_entries()
    assert sorted(entries) == sorted(BUILTIN_IDS)
    assert builtin_entries() is entries  # 启动时加载一次，不每次读盘
    for entry_id, entry in entries.items():
        assert isinstance(entry, BuiltinEntry)
        assert entry.id == entry_id
        assert entry.kind in {"playbook", "screen", "rule", "glossary", "profile"}
        assert 1 <= len(entry.title) <= 32
        assert 1 <= len(entry.when_to_use) <= 120
        assert 1 <= len(entry.body) <= 20_000
        assert entry.body == entry.body.strip()


def test_builtin_files_stay_within_the_fulltext_budget():
    entries = builtin_entries()
    fulltext = sum(
        len(entry.body)
        for entry in entries.values()
        if entry.kind in {"glossary", "profile"}
    )
    assert fulltext <= MAX_FULLTEXT_CHARS
    assert len(entries) <= MAX_ENABLED_ENTRIES


@pytest.mark.parametrize(
    "files",
    [
        pytest.param(
            {
                "a.md": _write(
                    {"id": "dup-entry", "kind": "rule", "title": "一", "when_to_use": "一"}
                ),
                "b.md": _write(
                    {"id": "dup-entry", "kind": "rule", "title": "二", "when_to_use": "二"}
                ),
            },
            id="duplicate-id",
        ),
        pytest.param({"a.md": "没有 frontmatter 的正文\n"}, id="missing-frontmatter"),
        pytest.param(
            {
                "a.md": _write(
                    {"id": "demo-entry", "kind": "diary", "title": "一", "when_to_use": "一"}
                )
            },
            id="unknown-kind",
        ),
        pytest.param(
            {
                "a.md": _write(
                    {"id": "Bad_Id", "kind": "rule", "title": "一", "when_to_use": "一"}
                )
            },
            id="invalid-id",
        ),
        pytest.param(
            {
                "a.md": _write(
                    {
                        "id": "demo-entry",
                        "kind": "rule",
                        "title": "题" * 33,
                        "when_to_use": "一",
                    }
                )
            },
            id="title-too-long",
        ),
        pytest.param(
            {
                "a.md": _write(
                    {"id": "demo-entry", "kind": "rule", "title": "一", "when_to_use": "一"},
                    body="",
                )
            },
            id="empty-body",
        ),
        pytest.param(
            {
                "a.md": _write(
                    {
                        "id": "demo-entry",
                        "kind": "rule",
                        "title": "一",
                        "when_to_use": "一",
                        "enabled": "false",
                    }
                )
            },
            id="unknown-frontmatter-key",
        ),
        pytest.param(
            {"a.md": _write({"id": "demo-entry", "kind": "rule", "title": "一"})},
            id="missing-when-to-use",
        ),
        pytest.param({"a.md": "---\n- 一\n- 二\n---\n正文\n"}, id="frontmatter-not-a-mapping"),
    ],
)
def test_builtin_loader_fails_loud_on_broken_files(tmp_path, files):
    for name, content in files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    with pytest.raises(KnowledgeBuiltinError):
        load_builtins(tmp_path)


def test_builtin_loader_ignores_non_markdown_files(tmp_path):
    (tmp_path / "README.txt").write_text("不是知识条目\n", encoding="utf-8")
    (tmp_path / "demo.md").write_text(
        _write({"id": "demo-entry", "kind": "rule", "title": "一", "when_to_use": "一"}),
        encoding="utf-8",
    )
    assert sorted(load_builtins(tmp_path)) == ["demo-entry"]


# ---------------------------------------------------------------------------
# 列表与详情
# ---------------------------------------------------------------------------
def test_list_entries_exposes_builtins_without_writing_rows(harness):
    service, factory = harness

    async def scenario():
        summaries = await service.list_entries()
        assert [item.id for item in summaries] == BUILTIN_LIST_ORDER
        builtin = builtin_entries()
        for item in summaries:
            entry = builtin[item.id]
            assert item.kind == entry.kind
            assert item.title == entry.title
            assert item.when_to_use == entry.when_to_use
            assert item.enabled is True
            assert item.source == "builtin"
            assert item.modified is False
            assert item.body_chars == len(entry.body)
            assert item.updated_at is None
        assert await _rows(factory) == {}

    asyncio.run(scenario())


def test_get_entry_returns_the_body_and_the_builtin_snapshot(harness):
    service, _ = harness

    async def scenario():
        entry = await service.get_entry("screen-home")
        builtin = builtin_entries()["screen-home"]
        assert entry.id == "screen-home"
        assert entry.kind == builtin.kind
        assert entry.body == builtin.body
        assert entry.enabled is True
        assert entry.source == "builtin"
        assert entry.modified is False
        assert entry.updated_at is None
        assert entry.builtin is not None
        assert entry.builtin.title == builtin.title
        assert entry.builtin.when_to_use == builtin.when_to_use
        assert entry.builtin.body == builtin.body

    asyncio.run(scenario())


def test_get_entry_raises_not_found_for_unknown_ids(harness):
    service, _ = harness

    async def scenario():
        with pytest.raises(AppError) as info:
            await service.get_entry("no-such-entry")
        assert info.value.code is ErrorCode.KNOWLEDGE_NOT_FOUND
        assert info.value.http_status == 404
        assert info.value.details == {"entry_id": "no-such-entry"}

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 覆盖：NULL 字段继承、停用、恢复默认
# ---------------------------------------------------------------------------
def test_override_row_stores_only_the_changed_fields(harness):
    service, factory = harness

    async def scenario():
        builtin = builtin_entries()["screen-home"]
        await service.put_entry(
            "screen-home",
            KnowledgeWrite(
                kind="screen",
                title="首页布局（改）",
                when_to_use=builtin.when_to_use,
                body=builtin.body,
                enabled=True,
            ),
        )
        row = (await _rows(factory))["screen-home"]
        assert row.title == "首页布局（改）"
        assert row.kind is None  # kind 不可覆盖，覆盖行里永远是 NULL
        assert row.when_to_use is None
        assert row.body is None
        assert row.enabled is True

        entry = await service.get_entry("screen-home")
        assert entry.title == "首页布局（改）"
        assert entry.when_to_use == builtin.when_to_use  # 未覆盖的字段继承内置
        assert entry.body == builtin.body
        assert entry.modified is True
        assert entry.updated_at is not None
        assert entry.builtin is not None and entry.builtin.body == builtin.body

    asyncio.run(scenario())


def test_put_entry_back_to_the_builtin_content_restores_the_default(harness):
    service, factory = harness

    async def scenario():
        builtin = builtin_entries()["screen-home"]
        # 原文写回、且没有覆盖行时不留行（「没有任何字段与内置不同」）
        await service.put_entry(
            "screen-home",
            KnowledgeWrite(
                kind="screen",
                title=builtin.title,
                when_to_use=builtin.when_to_use,
                body=builtin.body,
                enabled=True,
            ),
        )
        assert await _rows(factory) == {}

        await service.put_entry(
            "screen-home",
            KnowledgeWrite(
                kind="screen",
                title="首页布局（改）",
                when_to_use=builtin.when_to_use,
                body=builtin.body,
                enabled=True,
            ),
        )
        assert "screen-home" in await _rows(factory)

        detail = await service.put_entry(
            "screen-home",
            KnowledgeWrite(
                kind="screen",
                title=builtin.title,
                when_to_use=builtin.when_to_use,
                body=builtin.body,
                enabled=True,
            ),
        )
        assert await _rows(factory) == {}
        assert detail.title == builtin.title
        assert detail.modified is False
        assert detail.updated_at is None

    asyncio.run(scenario())


def test_builtin_updates_flow_through_to_unoverridden_fields(harness):
    """内置内容随代码更新：覆盖过的字段仍以用户为准，其余跟随新版本。"""
    service, factory = harness
    updated = BuiltinEntry(
        id="screen-home",
        kind="screen",
        title="首页布局",
        when_to_use="新版的使用时机",
        body="新版的正文",
    )

    async def scenario():
        await _seed_row(factory, id="screen-home", title="我的标题", enabled=True)
        upgraded = KnowledgeService(
            factory, builtins={**builtin_entries(), updated.id: updated}
        )
        entry = await upgraded.get_entry("screen-home")
        assert entry.title == "我的标题"
        assert entry.when_to_use == "新版的使用时机"
        assert entry.body == "新版的正文"

    asyncio.run(scenario())


def test_disabling_a_builtin_writes_an_enabled_only_row(harness):
    service, factory = harness

    async def scenario():
        builtin = builtin_entries()["screen-home"]
        await service.put_entry(
            "screen-home",
            KnowledgeWrite(
                kind="screen",
                title=builtin.title,
                when_to_use=builtin.when_to_use,
                body=builtin.body,
                enabled=False,
            ),
        )
        row = (await _rows(factory))["screen-home"]
        assert row.enabled is False
        assert (row.kind, row.title, row.when_to_use, row.body) == (None, None, None, None)

        summary = {item.id: item for item in await service.list_entries()}["screen-home"]
        assert summary.enabled is False
        assert summary.modified is True

        assert "screen-home" not in await service.render_context()

        with pytest.raises(AppError) as info:
            await service.read_for_agent("screen-home")
        assert info.value.code is ErrorCode.KNOWLEDGE_NOT_FOUND

    asyncio.run(scenario())


def test_deleting_an_unmodified_builtin_entry_is_readonly(harness):
    service, factory = harness

    async def scenario():
        with pytest.raises(AppError) as info:
            await service.delete_entry("screen-home")
        assert info.value.code is ErrorCode.KNOWLEDGE_BUILTIN_READONLY
        assert info.value.http_status == 409
        assert info.value.details == {"entry_id": "screen-home"}
        assert await _rows(factory) == {}

    asyncio.run(scenario())


def test_deleting_a_modified_builtin_entry_restores_the_default(harness):
    service, factory = harness

    async def scenario():
        builtin = builtin_entries()["gacha-conversion"]
        await service.put_entry(
            "gacha-conversion",
            KnowledgeWrite(
                kind="rule",
                title=builtin.title,
                when_to_use=builtin.when_to_use,
                body=builtin.body,
                enabled=False,
            ),
        )
        assert "gacha-conversion" in await _rows(factory)

        await service.delete_entry("gacha-conversion")
        assert await _rows(factory) == {}
        entry = await service.get_entry("gacha-conversion")
        assert entry.enabled is True
        assert entry.modified is False

    asyncio.run(scenario())


def test_changing_the_kind_of_a_builtin_entry_is_readonly(harness):
    service, factory = harness

    async def scenario():
        builtin = builtin_entries()["screen-home"]
        with pytest.raises(AppError) as info:
            await service.put_entry(
                "screen-home",
                KnowledgeWrite(
                    kind="rule",
                    title=builtin.title,
                    when_to_use=builtin.when_to_use,
                    body=builtin.body,
                    enabled=True,
                ),
            )
        assert info.value.code is ErrorCode.KNOWLEDGE_BUILTIN_READONLY
        assert info.value.http_status == 409
        assert info.value.details["kind"] == "rule"
        assert info.value.details["builtin_kind"] == "screen"
        assert await _rows(factory) == {}  # 拒绝时不留半成品

    asyncio.run(scenario())


def test_a_row_with_a_builtin_id_is_resolved_as_an_override(harness):
    """上一版本留下的自定义条目，遇到同名内置条目后自动变成覆盖行。"""
    service, factory = harness

    async def scenario():
        await _seed_row(
            factory,
            id="screen-home",
            kind="rule",
            title="我的首页笔记",
            when_to_use="我自己看得懂就行",
            body="先看顶栏。",
            enabled=True,
        )
        entry = await service.get_entry("screen-home")
        assert entry.kind == "screen"  # 内置的 kind 赢
        assert entry.title == "我的首页笔记"  # 用户写过的那几列赢
        assert entry.when_to_use == "我自己看得懂就行"
        assert entry.body == "先看顶栏。"
        assert entry.source == "builtin"
        assert entry.modified is True
        assert entry.builtin is not None
        assert entry.builtin.title == builtin_entries()["screen-home"].title

        context = await service.render_context()
        assert context.count("- screen-home｜") == 1
        assert "我的首页笔记" in context

    asyncio.run(scenario())


def test_a_new_builtin_turns_a_matching_custom_row_into_an_override(harness):
    service, factory = harness
    new_builtin = BuiltinEntry(
        id="new-screen",
        kind="rule",
        title="新规则",
        when_to_use="命中新规则时",
        body="内置正文",
    )

    async def scenario():
        await _seed_row(
            factory,
            id="new-screen",
            kind="screen",
            title="我的叫法",
            when_to_use="我自己写的",
            body="用户正文",
            enabled=True,
        )
        extended = KnowledgeService(
            factory, builtins={**builtin_entries(), new_builtin.id: new_builtin}
        )
        entry = await extended.get_entry("new-screen")
        assert entry.source == "builtin"
        assert entry.modified is True
        assert entry.kind == "rule"  # 用户不能覆盖 kind
        assert entry.title == "我的叫法"
        assert entry.body == "用户正文"
        assert entry.builtin is not None and entry.builtin.body == "内置正文"

    asyncio.run(scenario())


def test_custom_entry_round_trip_and_delete(harness):
    service, factory = harness

    async def scenario():
        payload = KnowledgeWrite(
            kind="playbook",
            title="刷 1-7",
            when_to_use="用户要刷固源岩时",
            body="## 步骤\n1. submit_pipeline",
        )
        created = await service.put_entry("farm-1-7", payload)
        assert created.source == "custom"
        assert created.modified is False
        assert created.builtin is None
        assert created.body == payload.body

        row = (await _rows(factory))["farm-1-7"]
        assert row.kind == "playbook"  # 自定义条目所有字段必填
        assert row.title == payload.title
        assert row.when_to_use == payload.when_to_use
        assert row.body == payload.body
        assert row.enabled is True

        summary = {item.id: item for item in await service.list_entries()}["farm-1-7"]
        assert summary.source == "custom"
        assert summary.body_chars == len(payload.body)

        await service.delete_entry("farm-1-7")
        assert await _rows(factory) == {}

        with pytest.raises(AppError) as info:
            await service.delete_entry("farm-1-7")
        assert info.value.code is ErrorCode.KNOWLEDGE_NOT_FOUND
        assert info.value.http_status == 404

    asyncio.run(scenario())


def test_custom_entries_are_readable_by_the_agent(harness):
    service, _ = harness

    async def scenario():
        await service.put_entry(
            "farm-1-7",
            KnowledgeWrite(
                kind="playbook", title="刷 1-7", when_to_use="用户要刷固源岩时", body="正文"
            ),
        )
        entry = await service.read_for_agent("farm-1-7")
        assert entry.body == "正文"
        assert entry.source == "custom"

        with pytest.raises(AppError) as info:
            await service.read_for_agent("unknown-entry")
        assert info.value.code is ErrorCode.KNOWLEDGE_NOT_FOUND

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 字段校验（服务层兜底；REST 的 422 由 Task 5 的请求模型给）
# ---------------------------------------------------------------------------
def test_write_payload_requires_every_field():
    with pytest.raises(ValidationError):
        KnowledgeWrite(kind="rule", title="一", when_to_use="一")  # type: ignore[call-arg]
    assert KnowledgeWrite(**FIELD_PAYLOAD).enabled is True


@pytest.mark.parametrize(
    "entry_id",
    ["", "a", "Bad-Id", "-leading", "has_underscore", "字" * 2, "x" * 49],
)
def test_put_entry_rejects_invalid_ids(harness, entry_id):
    service, factory = harness
    payload = KnowledgeWrite(**FIELD_PAYLOAD)

    async def scenario():
        with pytest.raises(AppError) as info:
            await service.put_entry(entry_id, payload)
        assert info.value.code is ErrorCode.VALIDATION_ERROR
        assert info.value.http_status == 422
        assert info.value.details["field"] == "id"
        assert await _rows(factory) == {}

    asyncio.run(scenario())


def test_put_entry_accepts_the_shortest_and_longest_valid_ids(harness):
    service, _ = harness

    async def scenario():
        payload = KnowledgeWrite(**FIELD_PAYLOAD)
        assert (await service.put_entry("ab", payload)).id == "ab"
        longest = "a" + "b" * 47
        assert (await service.put_entry(longest, payload)).id == longest

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"kind": "diary"}, id="unknown-kind"),
        pytest.param({"title": ""}, id="empty-title"),
        pytest.param({"title": "题" * 33}, id="title-too-long"),
        pytest.param({"when_to_use": ""}, id="empty-when-to-use"),
        pytest.param({"when_to_use": "用" * 121}, id="when-to-use-too-long"),
        pytest.param({"body": ""}, id="empty-body"),
        pytest.param({"body": "文" * 20_001}, id="body-too-long"),
    ],
)
def test_put_entry_rejects_invalid_fields(harness, overrides):
    service, factory = harness
    values = {**FIELD_PAYLOAD, **overrides}
    payload = KnowledgeWrite(**values)

    async def scenario():
        with pytest.raises(AppError) as info:
            await service.put_entry("demo-entry", payload)
        assert info.value.code is ErrorCode.VALIDATION_ERROR
        assert info.value.http_status == 422
        assert await _rows(factory) == {}

    asyncio.run(scenario())


def test_put_entry_accepts_the_exact_field_limits(harness):
    service, _ = harness

    async def scenario():
        entry = await service.put_entry(
            "limits-entry",
            KnowledgeWrite(
                kind="playbook",
                title="题" * 32,
                when_to_use="用" * 120,
                body="文" * 20_000,
            ),
        )
        assert len(entry.title) == 32
        assert len(entry.when_to_use) == 120
        assert len(entry.body) == 20_000

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 上限：条目数与 glossary + profile 正文合计
# ---------------------------------------------------------------------------
async def _fill(service, count: int) -> None:
    for index in range(count):
        await service.put_entry(
            f"custom-{index:02d}",
            KnowledgeWrite(
                kind="rule", title=f"自定义 {index}", when_to_use="需要时", body="正文"
            ),
        )


def test_the_51st_enabled_entry_is_rejected(harness):
    service, factory = harness

    async def scenario():
        await _fill(service, MAX_ENABLED_ENTRIES - await _enabled(service))
        assert await _enabled(service) == MAX_ENABLED_ENTRIES

        payload = KnowledgeWrite(kind="rule", title="第 51 条", when_to_use="需要时", body="正文")
        with pytest.raises(AppError) as info:
            await service.put_entry("custom-99", payload)
        assert info.value.code is ErrorCode.KNOWLEDGE_LIMIT_EXCEEDED
        assert info.value.http_status == 409
        assert info.value.details["limit"] == "enabled_entries"
        assert info.value.details["max"] == MAX_ENABLED_ENTRIES
        assert info.value.details["actual"] == MAX_ENABLED_ENTRIES + 1
        assert "custom-99" not in await _rows(factory)

        # 停用的条目不占名额，也不写入正文
        disabled = payload.model_copy(update={"enabled": False})
        assert (await service.put_entry("custom-99", disabled)).enabled is False
        assert await _enabled(service) == MAX_ENABLED_ENTRIES

        # 删掉一条腾出的名额可以用
        await service.delete_entry("custom-00")
        assert (await service.put_entry("custom-99", payload)).enabled is True
        assert await _enabled(service) == MAX_ENABLED_ENTRIES

    asyncio.run(scenario())


def test_fulltext_budget_covers_enabled_glossary_and_profile_bodies(harness):
    service, _ = harness
    budget = MAX_FULLTEXT_CHARS - len(builtin_entries()["glossary"].body)

    async def scenario():
        profile = KnowledgeWrite(
            kind="profile",
            title="我的档案",
            when_to_use="回答账号问题时",
            body="甲" * budget,
        )
        assert (await service.put_entry("profile", profile)).enabled is True

        with pytest.raises(AppError) as info:
            await service.put_entry(
                "profile", profile.model_copy(update={"body": "甲" * (budget + 1)})
            )
        assert info.value.code is ErrorCode.KNOWLEDGE_LIMIT_EXCEEDED
        assert info.value.http_status == 409
        assert info.value.details["limit"] == "fulltext_chars"
        assert info.value.details["max"] == MAX_FULLTEXT_CHARS
        assert info.value.details["actual"] == MAX_FULLTEXT_CHARS + 1

        # 停用的条目不计入字数合计
        disabled = profile.model_copy(
            update={"body": "甲" * (budget + 1), "enabled": False}
        )
        assert (await service.put_entry("profile", disabled)).enabled is False

        # 另一条启用的 glossary 与 profile 共享同一份预算
        await service.put_entry(
            "custom-glossary",
            KnowledgeWrite(kind="glossary", title="临时术语", when_to_use="需要时", body="乙"),
        )
        with pytest.raises(AppError) as info:
            await service.put_entry("profile", profile)
        assert info.value.code is ErrorCode.KNOWLEDGE_LIMIT_EXCEEDED
        assert info.value.details["limit"] == "fulltext_chars"

    asyncio.run(scenario())


def test_fulltext_budget_ignores_other_kinds(harness):
    service, factory = harness
    budget = MAX_FULLTEXT_CHARS - len(builtin_entries()["glossary"].body)

    async def scenario():
        await service.put_entry(
            "big-rule",
            KnowledgeWrite(kind="rule", title="大规则", when_to_use="需要时", body="丙" * 20_000),
        )
        await service.put_entry(
            "profile",
            KnowledgeWrite(kind="profile", title="我的档案", when_to_use="需要时", body="甲" * budget),
        )
        assert len((await _rows(factory))["big-rule"].body) == 20_000

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# render_context：段落顺序、索引格式与排序
# ---------------------------------------------------------------------------
def _render_expected(
    index_lines: list[str], glossary_body: str | None, profile_body: str | None
) -> str:
    return "\n".join(
        [
            INDEX_HEADER,
            *(index_lines or ["暂无"]),
            "",
            GLOSSARY_HEADER,
            glossary_body if glossary_body is not None else "暂无",
            "",
            PROFILE_HEADER,
            profile_body if profile_body is not None else "暂无",
        ]
    )


def test_render_context_orders_the_index_by_kind_then_id(harness):
    service, _ = harness

    async def scenario():
        await service.put_entry(
            "zzz-playbook",
            KnowledgeWrite(kind="playbook", title="最后一条剧本", when_to_use="需要时", body="正文"),
        )
        await service.put_entry(
            "aaa-screen",
            KnowledgeWrite(kind="screen", title="第一条界面", when_to_use="需要时", body="正文"),
        )
        await service.put_entry(
            "mmm-rule",
            KnowledgeWrite(kind="rule", title="中间一条规则", when_to_use="需要时", body="正文"),
        )
        builtin = builtin_entries()
        entries = await service.list_entries()
        assert [
            item.id for item in entries if item.kind in {"playbook", "screen", "rule"}
        ] == [
            "account-resources",
            "zzz-playbook",
            "aaa-screen",
            "screen-depot",
            "screen-home",
            "gacha-conversion",
            "mmm-rule",
        ]

        expected = _render_expected(
            [
                f"- account-resources｜{builtin['account-resources'].title}："
                f"{builtin['account-resources'].when_to_use}",
                "- zzz-playbook｜最后一条剧本：需要时",
                "- aaa-screen｜第一条界面：需要时",
                f"- screen-depot｜{builtin['screen-depot'].title}：{builtin['screen-depot'].when_to_use}",
                f"- screen-home｜{builtin['screen-home'].title}：{builtin['screen-home'].when_to_use}",
                f"- gacha-conversion｜{builtin['gacha-conversion'].title}："
                f"{builtin['gacha-conversion'].when_to_use}",
                "- mmm-rule｜中间一条规则：需要时",
            ],
            builtin["glossary"].body,
            None,
        )
        context = await service.render_context()
        assert context == expected
        assert not context.endswith("\n")

    asyncio.run(scenario())


def test_render_context_injects_the_profile_verbatim(harness):
    service, _ = harness

    async def scenario():
        await service.put_entry(
            "profile",
            KnowledgeWrite(
                kind="profile",
                title="我的档案",
                when_to_use="回答账号问题时",
                body="默认账号：官服\n偏好：先报合成玉",
            ),
        )
        context = await service.render_context()
        assert context.endswith(f"{PROFILE_HEADER}\n默认账号：官服\n偏好：先报合成玉")
        glossary = builtin_entries()["glossary"].body
        assert f"{GLOSSARY_HEADER}\n{glossary}\n\n{PROFILE_HEADER}" in context

    asyncio.run(scenario())


def test_render_context_says_none_for_every_empty_section(harness):
    service, _ = harness

    async def scenario():
        builtin = builtin_entries()
        for entry_id in BUILTIN_IDS:
            entry = builtin[entry_id]
            await service.put_entry(
                entry_id,
                KnowledgeWrite(
                    kind=entry.kind,
                    title=entry.title,
                    when_to_use=entry.when_to_use,
                    body=entry.body,
                    enabled=False,
                ),
            )
        assert await service.render_context() == _render_expected([], None, None)

    asyncio.run(scenario())


def test_render_context_injects_every_enabled_glossary(harness):
    """多条启用的 glossary 全部注入：字数上限算的就是它们的正文合计。"""
    service, _ = harness

    async def scenario():
        await service.put_entry(
            "aaa-glossary",
            KnowledgeWrite(kind="glossary", title="补充术语", when_to_use="需要时", body="补充一"),
        )
        context = await service.render_context()
        glossary = builtin_entries()["glossary"].body
        assert f"{GLOSSARY_HEADER}\n补充一\n\n{glossary}\n\n{PROFILE_HEADER}" in context

    asyncio.run(scenario())


def test_render_context_excludes_disabled_index_entries(harness):
    service, _ = harness

    async def scenario():
        builtin = builtin_entries()["screen-depot"]
        await service.put_entry(
            "screen-depot",
            KnowledgeWrite(
                kind=builtin.kind,
                title=builtin.title,
                when_to_use=builtin.when_to_use,
                body=builtin.body,
                enabled=False,
            ),
        )
        context = await service.render_context()
        assert "screen-depot" not in context
        assert "screen-home" in context

    asyncio.run(scenario())


def test_read_for_agent_returns_the_effective_body(harness):
    service, _ = harness

    async def scenario():
        await service.put_entry(
            "screen-home",
            KnowledgeWrite(
                kind="screen",
                title="首页布局（改）",
                when_to_use="需要读货币时",
                body="改过的正文",
            ),
        )
        entry = await service.read_for_agent("screen-home")
        assert entry.body == "改过的正文"
        assert entry.when_to_use == "需要读货币时"
        assert entry.modified is True

    asyncio.run(scenario())
