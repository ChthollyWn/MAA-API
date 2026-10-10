"""M28 四条基建 / 档案工具（规格 §8.3、§7.4、Q44）：参数、注册与分节写入。

`get_building_skills`（game）、`get_infrast_candidates`（account）、`list_infrast_plans`
（resource）都是 SAFE 只读；`update_user_profile`（account）每次确认，写入自定义知识条目
`agent-profile`，按 `## <section>` 分节（`replace` 覆盖该节、`append` 追加到该节，节不存在
就新建）。

不打网：领域服务用假件放在 `app.state`；`update_user_profile` 用真实 `KnowledgeService`
（一次性 SQLite 文件）验证真实写入与分节语义；确认策略由 `agent/policy.py` 负责（主 agent
已把本工具加进 `_ACCOUNT_WRITE_TOOLS`），本文件只测 handler。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表
from maa_api.agent.knowledge import (
    PROFILE_ENTRY_ID,
    PROFILE_ENTRY_TITLE,
    KnowledgeService,
    split_profile_sections,
)
from maa_api.agent.registry import ToolContext, ToolRegistry, ToolRisk
from maa_api.agent.tools import estate
from maa_api.db import session as db_session
from maa_api.domain.enums import CallerType
from maa_api.domain.errors import AppError, ErrorCode


def context(**state: Any) -> ToolContext:
    return ToolContext(
        CallerType.INTERNAL,
        None,
        None,
        SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state))),
        None,
    )


def registry() -> ToolRegistry:
    value = ToolRegistry()
    estate.register_tools(value)
    return value


def call(name: str, arguments: dict[str, Any], ctx: ToolContext) -> Any:
    """按工具名走注册表的 handler（参数先过 params_model，与真实调用一致）。"""

    async def scenario() -> Any:
        definition = registry().get(name)
        return await definition.handler(
            definition.params_model.model_validate(arguments), ctx
        )

    return asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 注册与参数
# --------------------------------------------------------------------------- #
def test_tools_are_registered_with_the_spec_groups_and_risks() -> None:
    table = {item.name: item for item in registry().list()}
    assert set(table) == {
        "get_building_skills",
        "get_infrast_candidates",
        "list_infrast_plans",
        "update_user_profile",
    }
    assert table["get_building_skills"].group == "game"
    assert table["get_building_skills"].read_only is True
    assert table["get_building_skills"].risk is ToolRisk.SAFE
    assert table["get_infrast_candidates"].group == "account"
    assert table["get_infrast_candidates"].risk is ToolRisk.SAFE
    assert table["list_infrast_plans"].group == "resource"
    assert table["list_infrast_plans"].read_only is True
    assert table["update_user_profile"].group == "account"
    assert table["update_user_profile"].read_only is False
    # 描述里写清「每次确认」与「三选一」。
    assert "确认" in table["update_user_profile"].description
    assert "三选一" in table["get_building_skills"].description


def test_params_models_validate_bounds() -> None:
    skills = registry().get("get_building_skills").params_model
    with pytest.raises(ValidationError):
        skills.model_validate({"operator": "银灰", "room": "贸易站", "skill": "喀兰之主"})
    with pytest.raises(ValidationError):
        skills.model_validate({"skill": "x" * 64})
    candidates = registry().get("get_infrast_candidates").params_model
    with pytest.raises(ValidationError):
        candidates.model_validate({"layout": "243", "shifts": 5})
    with pytest.raises(ValidationError):
        candidates.model_validate({"layout": "243", "shifts": 1})
    with pytest.raises(ValidationError):
        candidates.model_validate({"layout": "999", "shifts": 2})
    with pytest.raises(ValidationError):
        candidates.model_validate({"shifts": 2})
    profile = registry().get("update_user_profile").params_model
    with pytest.raises(ValidationError):
        profile.model_validate({"section": "", "content": "x"})
    with pytest.raises(ValidationError):
        profile.model_validate({"section": "x" * 33, "content": "x"})
    with pytest.raises(ValidationError):
        profile.model_validate({"section": "布局", "content": "x" * 2001})
    with pytest.raises(ValidationError):
        profile.model_validate({"section": "布局", "content": "x", "mode": "delete"})
    parsed = profile.model_validate({"section": "布局", "content": "243"})
    assert parsed.mode == "replace"


def test_selector_must_be_exactly_one_for_building_skills() -> None:
    with pytest.raises(ValidationError):
        registry().get("get_building_skills").params_model.model_validate({})


# --------------------------------------------------------------------------- #
# handler：三个只读工具
# --------------------------------------------------------------------------- #
class FakeBuildingService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def skills(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("skills", kwargs))
        return {"skills": [{"name": "喀兰之主"}], "total": 1}

    async def candidates(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("candidates", kwargs))
        return {"layout": kwargs["layout"], "rooms": []}

    async def plans(self) -> dict[str, Any]:
        self.calls.append(("plans", {}))
        return {"plans": [], "total": 0}


def test_handlers_delegate_to_the_domain_services() -> None:
    building = FakeBuildingService()
    ctx = context(building_service=building)

    skills = call("get_building_skills", {"operator": "银灰"}, ctx)
    assert skills["total"] == 1
    assert building.calls[-1] == ("skills", {"operator": "银灰", "room": None, "skill": None})

    candidates = call(
        "get_infrast_candidates", {"layout": "243", "shifts": 2, "rooms": ["制造站"]}, ctx
    )
    assert candidates["layout"] == "243"
    assert building.calls[-1] == (
        "candidates",
        {"layout": "243", "shifts": 2, "rooms": ["制造站"]},
    )

    plans = call("list_infrast_plans", {}, ctx)
    assert plans["total"] == 0
    assert building.calls[-1] == ("plans", {})


def test_handlers_fall_back_to_the_default_facade(monkeypatch) -> None:
    """没装配服务时用模块默认门面（按注入对象身份复用）。"""

    resolved: list[str] = []

    class FakeDefault:
        def __init__(self, **kwargs: Any) -> None:
            resolved.append("built")

        async def plans(self) -> dict[str, Any]:
            return {"plans": [], "total": 0}

    monkeypatch.setattr(
        "maa_api.services.game.building.default_building_service",
        lambda **kwargs: FakeDefault(**kwargs),
    )
    assert call("list_infrast_plans", {}, context()) == {"plans": [], "total": 0}
    assert resolved == ["built"]


# --------------------------------------------------------------------------- #
# update_user_profile：分节写入
# --------------------------------------------------------------------------- #
@pytest.fixture
def knowledge(tmp_path):
    engine = db_session.make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'estate.db'}", poolclass=NullPool
    )

    async def create() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)

    asyncio.run(create())
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield KnowledgeService(factory)
    engine.sync_engine.dispose()


def test_update_user_profile_creates_sections_in_order(knowledge) -> None:
    ctx = context(knowledge_service=knowledge)
    first = call(
        "update_user_profile",
        {"section": "基建布局", "content": "243", "mode": "replace"},
        ctx,
    )
    assert first["entry_id"] == PROFILE_ENTRY_ID
    assert first["kind"] == "profile"
    assert first["title"] == PROFILE_ENTRY_TITLE
    assert first["section"] == "基建布局"
    assert first["mode"] == "replace"
    assert split_profile_sections(first["body"]) == {"基建布局": "243"}

    second = call(
        "update_user_profile",
        {"section": "班次", "content": "早班 08:00、晚班 20:00"},
        ctx,
    )
    assert list(split_profile_sections(second["body"])) == ["基建布局", "班次"]

    entry = asyncio.run(knowledge.get_entry(PROFILE_ENTRY_ID))
    assert entry.kind == "profile"
    assert entry.title == PROFILE_ENTRY_TITLE
    assert entry.source == "custom"
    assert entry.when_to_use
    assert split_profile_sections(entry.body) == {
        "基建布局": "243",
        "班次": "早班 08:00、晚班 20:00",
    }


def test_update_user_profile_append_and_replace_touch_only_one_section(knowledge) -> None:
    ctx = context(knowledge_service=knowledge)
    call(
        "update_user_profile",
        {"section": "班次", "content": "早班 08:00", "mode": "replace"},
        ctx,
    )
    call(
        "update_user_profile",
        {"section": "基建布局", "content": "243", "mode": "replace"},
        ctx,
    )
    appended = call(
        "update_user_profile",
        {"section": "班次", "content": "晚班 20:00", "mode": "append"},
        ctx,
    )
    assert split_profile_sections(appended["body"]) == {
        "班次": "早班 08:00\n晚班 20:00",
        "基建布局": "243",
    }
    # append 到不存在的节 = 新建。
    created = call(
        "update_user_profile",
        {"section": "碎石方针", "content": "不碎石", "mode": "append"},
        ctx,
    )
    assert split_profile_sections(created["body"])["碎石方针"] == "不碎石"
    # replace 覆盖该节，其余节原样保留。
    replaced = call(
        "update_user_profile",
        {"section": "班次", "content": "只留早班 08:00", "mode": "replace"},
        ctx,
    )
    sections = split_profile_sections(replaced["body"])
    assert sections["班次"] == "只留早班 08:00"
    assert sections["基建布局"] == "243"
    assert sections["碎石方针"] == "不碎石"
    # 返回值带节名清单与正文字数，确认卡片之外也能自查。
    assert replaced["sections"] == ["班次", "基建布局", "碎石方针"]
    assert replaced["body_chars"] == len(replaced["body"])


def test_update_user_profile_needs_the_knowledge_service() -> None:
    with pytest.raises(AppError) as exc_info:
        call("update_user_profile", {"section": "布局", "content": "243"}, context())
    assert exc_info.value.code == ErrorCode.INTERNAL_ERROR
