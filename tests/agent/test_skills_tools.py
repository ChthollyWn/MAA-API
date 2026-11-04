"""M78 T4：技能工具（规格 §6 的两条用例）。

``load_skill`` 返回正文、工具、附属文件清单与 12 位版本；两个工具都 ``rest_visible=False``，
REST 目录不列、调用报 ``TOOL_NOT_FOUND``。附属文件用临时技能目录注入，不依赖真实
内置技能里有没有 ``notes/`` 文件。
"""

from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401  # 注册全部表
from maa_api.agent.confirmation import ConfirmationService
from maa_api.agent.policy import PolicyEngine
from maa_api.agent.registry import ToolContext
from maa_api.agent.skills.registry import SkillRegistry, load_builtin_skills
from maa_api.agent.tools import build_registry
from maa_api.api.deps import get_session
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import agent
from maa_api.db import session as db_session
from maa_api.domain.enums import CallerType
from maa_api.settings import LLMSettings, Settings, set_settings
from tests.fakes.v3_db import make_session_factory

SKILL_MD = """---
id: demo-skill
title: 演示技能
description: 演示用：加载后会返回附属文件清单
tools: [get_screenshot]
agents: [general]
version: 3
---

## 正文

按这个步骤做。
"""

CHECKLIST = "# 清单\n\n- 第一步\n"


def _write_skill(root: Any) -> None:
    skill_dir = root / "demo-skill"
    (skill_dir / "notes").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (skill_dir / "notes" / "checklist.md").write_text(CHECKLIST, encoding="utf-8")


def _registry_from(root: Any, factory: Any) -> SkillRegistry:
    builtins = load_builtin_skills(root)
    return SkillRegistry(
        factory,
        tool_names={"get_screenshot"},
        agent_kinds={"general": "worker"},
        builtins=builtins,
        settings_provider=lambda: SimpleNamespace(
            skills=SimpleNamespace(enabled=True)
        ),
    )


def _context(registry: SkillRegistry) -> ToolContext:
    state = SimpleNamespace(skill_registry=registry)
    return ToolContext(
        caller=CallerType.INTERNAL,
        session_id=None,
        request_id=None,
        request=SimpleNamespace(app=SimpleNamespace(state=state)),
        db_session=None,
    )


def test_load_skill_returns_body_tools_files(tmp_path) -> None:
    """返回正文、工具、附属文件清单与 12 位版本。"""

    async def scenario() -> None:
        engine, factory = await make_session_factory(tmp_path / "skills-tools.db")
        try:
            _write_skill(tmp_path)
            registry = _registry_from(tmp_path, factory)
            definition = build_registry().get("load_skill")
            params = definition.params_model.model_validate(
                {"skill_id": "demo-skill"}
            )

            result = await definition.handler(params, _context(registry))

            assert result["id"] == "demo-skill"
            assert result["title"] == "演示技能"
            assert result["body"].startswith("## 正文")
            assert result["tools"] == ["get_screenshot"]
            assert result["files"] == ["notes/checklist.md"]
            assert len(result["version"]) == 12
            expected = hashlib.sha256(
                (result["body"] + "\nget_screenshot").encode("utf-8")
            ).hexdigest()[:12]
            assert result["version"] == expected

            # 附属文件工具：内容与字数。
            file_definition = build_registry().get("read_skill_file")
            file_params = file_definition.params_model.model_validate(
                {"skill_id": "demo-skill", "path": "notes/checklist.md"}
            )
            read = await file_definition.handler(file_params, _context(registry))
            assert read == {
                "skill_id": "demo-skill",
                "path": "notes/checklist.md",
                "content": CHECKLIST,
                "chars": len(CHECKLIST),
            }
        finally:
            engine.sync_engine.dispose()

    asyncio.run(scenario())


def test_tools_are_hidden_from_rest() -> None:
    """REST 目录没有这两个工具；REST 调用返回 ``TOOL_NOT_FOUND``。"""

    from maa_api.agent.tools import build_registry as build

    registry = build()

    async def scenario() -> None:
        engine = db_session.make_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with engine.begin() as connection:
            await connection.run_sync(SQLModel.metadata.create_all)
        service = ConfirmationService(factory, registry, policy=PolicyEngine())
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(agent.router)
        app.state.confirmation_service = service

        async def get_test_session():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_session] = get_test_session
        set_settings(
            Settings(
                llm=LLMSettings(
                    base_url="https://llm.test/v1", api_key="k", model="m"
                )
            )
        )
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        try:
            listed = client.get("/api/agent/tools")
            assert listed.status_code == 200
            names = {item["name"] for item in listed.json()["items"]}
            assert "load_skill" not in names
            assert "read_skill_file" not in names

            invoked = client.post(
                "/api/agent/tools/load_skill/invoke",
                json={"arguments": {"skill_id": "playbook-recruit"}},
            )
            assert invoked.status_code == 404, invoked.text
            assert invoked.json()["error"]["code"] == "TOOL_NOT_FOUND"
        finally:
            client.__exit__(None, None, None)
            await engine.dispose()

    asyncio.run(scenario())
