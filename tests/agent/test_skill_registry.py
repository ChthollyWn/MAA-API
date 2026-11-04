"""M78 T3：技能注册表与仓储（规格 §4.2、§6）。

本文件覆盖 §6 列出的十一条 ``tests/agent/test_skill_registry.py`` 用例：

1. 真实内置目录的 9 个技能、frontmatter 与映射表；
2. 内置加载器的五种 fail loud；
3. 构造时拒绝未知工具与子代理；
4. 覆盖合并与恢复默认；
5. 用户技能写入与字段校验；
6. 删除规则（内置只读 / 恢复默认 / 用户技能 / 未知 id）；
7. 索引按代理、状态与 ``skills.enabled`` 过滤；
8. ``load`` 的三种失败消息；
9. ``read_file`` 的路径、软链接与大小护栏；
10. 预加载校验；
11. 孤儿覆盖行跳过。

本目录没有 pytest-asyncio（不要为此加插件），异步用例统一写成「同步测试函数 +
``asyncio.run(scenario())``」。临时库复用 ``tests/db/conftest.py`` 的 ``create_all``
夹具；内置目录用 ``tmp_path`` 下的假技能树注入。
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from ruamel.yaml import YAML

from maa_api.agent.skills import (
    SkillRegistry,
    builtin_skills,
    load_builtin_skills,
)
from maa_api.agent.skills.registry import (
    FRONTMATTER_FIELDS,
    MAX_BODY_CHARS,
    MAX_DESCRIPTION_CHARS,
    MAX_SKILL_FILE_BYTES,
    MAX_TITLE_CHARS,
    SkillBuiltinError,
    SkillWrite,
)
from maa_api.db.models import AgentSkill
from maa_api.db.repositories.agent_skill import AgentSkillRepository
from maa_api.domain.errors import AppError, ErrorCode
from tests.db.conftest import (  # noqa: F401  # pytest 从模块命名空间发现 fixture
    db_engine,
    db_path,
    db_session,
    db_session_factory,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILTIN_DIR = REPO_ROOT / "maa_api" / "agent" / "skills" / "builtin"

#: 与真实 ``ToolRegistry`` 对齐的最小子集；假技能只引用这些工具。
TOOL_NAMES = frozenset(
    {
        "get_screenshot",
        "submit_pipeline",
        "wait_pipeline",
        "get_pipeline",
        "ask_user",
        "get_depot",
        "plan_upgrade",
    }
)
#: 代理定义种类：``researcher`` / ``nobody`` 用来验证「只能挂总控与 Worker」。
AGENT_KINDS = {
    "orchestrator": "orchestrator",
    "general": "worker",
    "daily_ops": "worker",
    "copilot": "worker",
    "planner": "worker",
    "researcher": "subagent",
}

#: 规格 §4.2 映射表里的 tools / agents（顺序照抄）。
EXPECTED_BUILTINS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "account-resources": (
        ("get_screenshot", "back_to_home", "submit_pipeline", "wait_pipeline"),
        ("general", "daily_ops"),
    ),
    "playbook-daily-weekly": (
        ("submit_pipeline", "get_prts_page"),
        ("orchestrator", "general", "daily_ops"),
    ),
    "playbook-drop-lookup": (
        (
            "resolve_stage",
            "get_drop_rates",
            "get_drop_stats",
            "search_prts",
            "get_prts_page",
        ),
        ("general", "daily_ops", "planner", "analyst"),
    ),
    "playbook-infrast-shift": (
        (
            "list_infrast_plans",
            "set_infrast_plan",
            "submit_pipeline",
            "wait_pipeline",
            "get_screenshot",
            "ask_user",
        ),
        ("general", "daily_ops", "planner"),
    ),
    "playbook-promotion-planning": (
        (
            "get_operator",
            "get_depot",
            "plan_upgrade",
            "get_drop_rates",
            "get_skland_status",
            "sync_skland_training",
            "submit_pipeline",
            "ask_user",
        ),
        ("general", "planner"),
    ),
    "playbook-recruit": (
        (
            "list_task_types",
            "submit_pipeline",
            "wait_pipeline",
            "get_pipeline",
            "get_screenshot",
            "ask_user",
        ),
        ("general", "daily_ops"),
    ),
    "playbook-sanity-planning": (
        (
            "get_activity_stages",
            "get_stage",
            "resolve_stage",
            "get_drop_stats",
            "submit_pipeline",
            "wait_pipeline",
            "ask_user",
        ),
        ("general", "daily_ops", "planner"),
    ),
    "playbook-stage-clear": (
        (
            "search_copilots",
            "get_stage_level_data",
            "save_copilot_draft",
            "run_copilot_attempt",
            "record_copilot_result",
            "search_prts",
            "get_stage",
            "resolve_stage",
            "update_resource",
            "wait_update",
            "ask_user",
        ),
        ("general", "copilot"),
    ),
    "task-preconditions": (
        ("get_screenshot", "back_to_home", "submit_pipeline"),
        ("general", "daily_ops", "copilot", "diagnose"),
    ),
}

_FRONTMATTER = re.compile(
    r"\A---[ \t]*\r?\n(?P<meta>.*?)\r?\n---[ \t]*(?:\r?\n(?P<body>.*))?\Z",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# 假内置目录
# ---------------------------------------------------------------------------
def _meta(
    skill_id: str,
    *,
    title: str = "测试技能",
    description: str = "用户需要测试时",
    tools: tuple[str, ...] = ("get_screenshot",),
    agents: tuple[str, ...] = (),
    version: int = 1,
) -> dict[str, object]:
    return {
        "id": skill_id,
        "title": title,
        "description": description,
        "tools": list(tools),
        "agents": list(agents),
        "version": version,
    }


def _write_skill(
    root: Path,
    skill_id: str,
    *,
    title: str = "测试技能",
    description: str = "用户需要测试时",
    body: str = "## 测试\n正文。\n",
    tools: tuple[str, ...] = ("get_screenshot",),
    agents: tuple[str, ...] = (),
    version: int = 1,
    frontmatter: dict[str, object] | None = None,
    directory_name: str | None = None,
    files: dict[str, str] | None = None,
) -> Path:
    """写一个 ``<dir>/SKILL.md``（可带附属文件）；``frontmatter`` 用于构造非法输入。"""
    directory = root / (directory_name or skill_id)
    directory.mkdir(parents=True)
    meta = (
        frontmatter
        if frontmatter is not None
        else _meta(
            skill_id,
            title=title,
            description=description,
            tools=tools,
            agents=agents,
            version=version,
        )
    )
    lines = ["---"]
    for key, value in meta.items():
        if isinstance(value, list):
            lines.append(f"{key}: [{', '.join(str(item) for item in value)}]")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    (directory / "SKILL.md").write_text("\n".join(lines) + "\n" + body, encoding="utf-8")
    for relative, content in (files or {}).items():
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return directory


ALPHA_TITLE = "甲技能"
ALPHA_DESCRIPTION = "用户要做甲事情时"
ALPHA_TOOLS = ("get_screenshot", "submit_pipeline")
ALPHA_AGENTS = ("general",)
ALPHA_BODY = "## 甲\n按步骤做。"


@pytest.fixture
def builtin_root(tmp_path: Path) -> Path:
    """四个假内置技能：alpha（general）、beta（planner）、gamma（所有代理）、delta（general）。"""
    root = tmp_path / "builtin"
    root.mkdir()
    _write_skill(
        root,
        "alpha",
        title=ALPHA_TITLE,
        description=ALPHA_DESCRIPTION,
        body=ALPHA_BODY,
        tools=ALPHA_TOOLS,
        agents=ALPHA_AGENTS,
        files={
            "notes/checklist.md": "清单正文\n",
            "big.txt": "x" * (MAX_SKILL_FILE_BYTES + 1),
        },
    )
    _write_skill(root, "beta", title="乙技能", description="乙说明", agents=("planner",))
    _write_skill(root, "gamma", title="丙技能", description="丙说明", tools=(), agents=())
    _write_skill(
        root, "delta", title="丁技能", description="丁说明", agents=("general",)
    )
    return root


def _settings(enabled: bool) -> SimpleNamespace:
    """``Settings`` 的最小替身：注册表只读 ``skills.enabled``。"""
    return SimpleNamespace(skills=SimpleNamespace(enabled=enabled))


def _registry(
    root: Path,
    session_factory,
    *,
    enabled: bool = True,
) -> SkillRegistry:
    return SkillRegistry(
        session_factory,
        tool_names=TOOL_NAMES,
        agent_kinds=AGENT_KINDS,
        builtins=load_builtin_skills(root),
        settings_provider=lambda: _settings(enabled),
    )


def _write(skill_id: str, **overrides) -> SkillWrite:
    """一份合法的写入内容；``overrides`` 覆盖字段。"""
    payload = {
        "title": "自定义技能",
        "description": "用户自建时使用",
        "body": "## 步骤\n先做这个，再做那个。\n",
        "tools": ["get_screenshot"],
        "agents": ["general"],
        "enabled": True,
    }
    payload.update(overrides)
    return SkillWrite(**payload)


# ---------------------------------------------------------------------------
# 1. 真实内置技能
# ---------------------------------------------------------------------------
def test_builtin_loader_reads_nine_skills_with_exact_frontmatter() -> None:
    skills = load_builtin_skills()
    assert set(skills) == set(EXPECTED_BUILTINS)
    assert len(skills) == 9
    assert len(builtin_skills()) == 9

    for skill_id, (tools, agents) in EXPECTED_BUILTINS.items():
        skill = skills[skill_id]
        assert skill.tools == tools
        assert skill.agents == agents
        assert skill.version == 1
        assert skill.files == ()

        raw = (BUILTIN_DIR / skill_id / "SKILL.md").read_text(encoding="utf-8")
        match = _FRONTMATTER.match(raw)
        assert match is not None, f"{skill_id} 缺 frontmatter"
        meta = YAML(typ="safe").load(match.group("meta"))
        assert set(meta) == FRONTMATTER_FIELDS
        assert meta["id"] == skill_id
        assert meta["title"] == skill.title
        assert meta["description"] == skill.description


# ---------------------------------------------------------------------------
# 2. 加载器 fail loud
# ---------------------------------------------------------------------------
def test_builtin_loader_fails_loud(tmp_path: Path) -> None:
    def expect_error(name: str, build) -> None:
        root = tmp_path / name
        root.mkdir()
        build(root)
        with pytest.raises(SkillBuiltinError):
            load_builtin_skills(root)

    def directory_mismatch(root: Path) -> None:
        _write_skill(root, "real-id", directory_name="other-dir")

    def extra_field(root: Path) -> None:
        meta = _meta("extra-field")
        meta["unexpected"] = "x"
        _write_skill(root, "extra-field", frontmatter=meta)

    def missing_field(root: Path) -> None:
        meta = _meta("missing-field")
        del meta["description"]
        _write_skill(root, "missing-field", frontmatter=meta)

    def overlong_title(root: Path) -> None:
        _write_skill(root, "long-title", title="x" * (MAX_TITLE_CHARS + 1))

    def duplicate_id(root: Path) -> None:
        _write_skill(root, "dup")
        _write_skill(root, "dup-copy", frontmatter=_meta("dup"))

    def invalid_version(root: Path) -> None:
        _write_skill(root, "bad-version", frontmatter=_meta("bad-version", version=0))

    def empty_body(root: Path) -> None:
        _write_skill(root, "empty-body", body="")

    expect_error("dir-mismatch", directory_mismatch)
    expect_error("extra-field", extra_field)
    expect_error("missing-field", missing_field)
    expect_error("overlong-title", overlong_title)
    expect_error("duplicate-id", duplicate_id)
    expect_error("invalid-version", invalid_version)
    expect_error("empty-body", empty_body)


# ---------------------------------------------------------------------------
# 3. 构造校验
# ---------------------------------------------------------------------------
def test_registry_rejects_unknown_tools_or_subagent_agents(
    tmp_path: Path, db_session_factory
) -> None:
    root = tmp_path / "bad-tools"
    root.mkdir()
    _write_skill(root, "bad-tool", tools=("no-such-tool",))
    with pytest.raises(SkillBuiltinError) as error:
        _registry(root, db_session_factory)
    assert "no-such-tool" in str(error.value)

    root = tmp_path / "bad-subagent"
    root.mkdir()
    _write_skill(root, "bad-subagent", agents=("researcher",))
    with pytest.raises(SkillBuiltinError) as error:
        _registry(root, db_session_factory)
    assert "researcher" in str(error.value)

    root = tmp_path / "bad-agent"
    root.mkdir()
    _write_skill(root, "bad-agent", agents=("nobody",))
    with pytest.raises(SkillBuiltinError) as error:
        _registry(root, db_session_factory)
    assert "nobody" in str(error.value)


# ---------------------------------------------------------------------------
# 4. 覆盖合并与恢复默认
# ---------------------------------------------------------------------------
def test_override_merge_and_restore_default(
    db_session_factory, builtin_root: Path
) -> None:
    async def scenario() -> None:
        registry = _registry(builtin_root, db_session_factory)
        detail = await registry.put_skill(
            "alpha",
            _write(
                "alpha",
                title="改过的标题",
                description=ALPHA_DESCRIPTION,
                body=ALPHA_BODY,
                tools=list(ALPHA_TOOLS),
                agents=list(ALPHA_AGENTS),
            ),
        )
        assert detail.modified is True
        assert detail.title == "改过的标题"
        assert detail.source == "builtin"
        assert detail.version == 1
        assert detail.builtin is not None and detail.builtin.title == ALPHA_TITLE

        async with db_session_factory() as session:
            row = await AgentSkillRepository(session).get("alpha")
            assert row is not None
            assert row.origin == "override"
            assert row.title == "改过的标题"
            # 只存与内置不同的列，其余为 NULL 继承。
            assert row.description is None
            assert row.body is None
            assert row.tools is None
            assert row.agents is None
            assert row.enabled is True

        listed = {item.id: item for item in await registry.list_skills()}
        assert listed["alpha"].modified is True
        assert listed["alpha"].title == "改过的标题"

        restored = await registry.put_skill(
            "alpha",
            _write(
                "alpha",
                title=ALPHA_TITLE,
                description=ALPHA_DESCRIPTION,
                body=ALPHA_BODY,
                tools=list(ALPHA_TOOLS),
                agents=list(ALPHA_AGENTS),
            ),
        )
        assert restored.modified is False
        assert restored.title == ALPHA_TITLE
        async with db_session_factory() as session:
            assert await AgentSkillRepository(session).get("alpha") is None

        # 只停用：所有内容列都继承内置，但 enabled=false 要留行。
        disabled = await registry.put_skill(
            "alpha",
            _write(
                "alpha",
                title=ALPHA_TITLE,
                description=ALPHA_DESCRIPTION,
                body=ALPHA_BODY,
                tools=list(ALPHA_TOOLS),
                agents=list(ALPHA_AGENTS),
                enabled=False,
            ),
        )
        assert disabled.modified is True
        assert disabled.enabled is False
        async with db_session_factory() as session:
            row = await AgentSkillRepository(session).get("alpha")
            assert row is not None
            assert row.title is None
            assert row.enabled is False

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 5. 用户技能与校验
# ---------------------------------------------------------------------------
def test_put_user_skill_and_validation_errors(
    db_session_factory, builtin_root: Path
) -> None:
    async def scenario() -> None:
        registry = _registry(builtin_root, db_session_factory)
        detail = await registry.put_skill("my-skill", _write("my-skill"))
        assert detail.source == "user"
        assert detail.modified is False
        assert detail.version is None
        assert detail.builtin is None
        assert detail.files == []
        assert detail.body_chars == len("## 步骤\n先做这个，再做那个。\n")

        async with db_session_factory() as session:
            row = await AgentSkillRepository(session).get("my-skill")
            assert row is not None
            assert row.origin == "user"
            assert row.status == "active"
            assert row.title == "自定义技能"
            assert row.tools == ["get_screenshot"]
            assert row.agents == ["general"]

        cases = [
            ("Bad_ID", _write("x"), "id", None),
            ("bad-title", _write("x", title=""), "title", None),
            ("long-title", _write("x", title="x" * (MAX_TITLE_CHARS + 1)), "title", None),
            (
                "bad-description",
                _write("x", description="x" * (MAX_DESCRIPTION_CHARS + 1)),
                "description",
                None,
            ),
            ("empty-body", _write("x", body=""), "body", None),
            (
                "long-body",
                _write("x", body="x" * (MAX_BODY_CHARS + 1)),
                "body",
                None,
            ),
            ("bad-tools", _write("x", tools=["nope"]), "tools", ["nope"]),
            (
                "bad-agents",
                _write("x", agents=["researcher"]),
                "agents",
                ["researcher"],
            ),
            ("unknown-agents", _write("x", agents=["nobody"]), "agents", ["nobody"]),
        ]
        for skill_id, payload, field, unknown in cases:
            with pytest.raises(AppError) as captured:
                await registry.put_skill(skill_id, payload)
            error = captured.value
            assert error.code is ErrorCode.SKILL_INVALID
            assert error.http_status == 400
            assert error.details is not None
            assert error.details["skill_id"] == skill_id
            assert error.details["field"] == field
            assert error.details["reason"]
            if unknown is not None:
                assert error.details["unknown"] == unknown

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 6. 删除规则
# ---------------------------------------------------------------------------
def test_delete_rules(db_session_factory, builtin_root: Path) -> None:
    async def scenario() -> None:
        registry = _registry(builtin_root, db_session_factory)

        with pytest.raises(AppError) as captured:
            await registry.delete_skill("alpha")
        assert captured.value.code is ErrorCode.SKILL_BUILTIN_READONLY
        assert captured.value.http_status == 409

        await registry.put_skill("alpha", _write("alpha", title="改过"))
        await registry.delete_skill("alpha")
        assert (await registry.get_skill("alpha")).modified is False

        await registry.put_skill("my-skill", _write("my-skill"))
        await registry.delete_skill("my-skill")
        with pytest.raises(AppError) as captured:
            await registry.get_skill("my-skill")
        assert captured.value.code is ErrorCode.SKILL_NOT_FOUND
        assert captured.value.http_status == 404

        with pytest.raises(AppError) as captured:
            await registry.delete_skill("no-such-skill")
        assert captured.value.code is ErrorCode.SKILL_NOT_FOUND

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 7. 索引过滤
# ---------------------------------------------------------------------------
def test_index_for_filters_agents_status_and_switch(
    db_session_factory, builtin_root: Path
) -> None:
    async def scenario() -> None:
        registry = _registry(builtin_root, db_session_factory)
        # delta 停用。
        await registry.put_skill(
            "delta",
            _write(
                "delta",
                title="丁技能",
                description="丁说明",
                body="## 测试\n正文。\n",
                tools=["get_screenshot"],
                agents=["general"],
                enabled=False,
            ),
        )
        # 草稿（M95 才会有写入方）不该出现在索引里。
        async with db_session_factory() as session:
            await AgentSkillRepository(session).upsert(
                AgentSkill(
                    id="draft-skill",
                    title="草稿技能",
                    description="还没启用",
                    body="草稿正文",
                    tools=[],
                    agents=["planner"],
                    origin="generated",
                    status="draft",
                    enabled=True,
                )
            )
            await session.commit()

        planner = await registry.index_for("planner")
        assert [entry.id for entry in planner] == ["beta", "gamma"]
        assert planner[0].title == "乙技能"
        assert planner[0].description == "乙说明"

        general = await registry.index_for("general")
        assert [entry.id for entry in general] == ["alpha", "gamma"]

        assert await _registry(builtin_root, db_session_factory, enabled=False).index_for(
            "planner"
        ) == []

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 8. load 的失败消息
# ---------------------------------------------------------------------------
def test_load_errors_have_reasons(db_session_factory, builtin_root: Path) -> None:
    async def scenario() -> None:
        registry = _registry(builtin_root, db_session_factory)
        messages: list[str] = []

        with pytest.raises(AppError) as captured:
            await _registry(builtin_root, db_session_factory, enabled=False).load(
                "alpha", agent_name="general"
            )
        messages.append(captured.value.message or "")
        assert captured.value.details == {"skill_id": "alpha", "agent": "general"}

        with pytest.raises(AppError) as captured:
            await registry.load("no-such-skill", agent_name="general")
        messages.append(captured.value.message or "")

        await registry.put_skill(
            "alpha",
            _write(
                "alpha",
                title=ALPHA_TITLE,
                description=ALPHA_DESCRIPTION,
                body=ALPHA_BODY,
                tools=list(ALPHA_TOOLS),
                agents=list(ALPHA_AGENTS),
                enabled=False,
            ),
        )
        with pytest.raises(AppError) as captured:
            await registry.load("alpha", agent_name="general")
        messages.append(captured.value.message or "")

        with pytest.raises(AppError) as captured:
            await registry.load("beta", agent_name="general")
        messages.append(captured.value.message or "")

        assert messages == [
            "技能已在设置里关闭",
            "技能不存在或已停用",
            "技能不存在或已停用",
            "这个技能不对当前代理开放",
        ]
        assert len({messages[0], messages[1], messages[3]}) == 3

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 9. read_file 护栏
# ---------------------------------------------------------------------------
def test_read_file_guards_paths(
    tmp_path: Path, db_session_factory, builtin_root: Path
) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("外部内容\n", encoding="utf-8")
    (builtin_root / "alpha" / "notes" / "escape.md").symlink_to(outside)

    async def scenario() -> None:
        registry = _registry(builtin_root, db_session_factory)
        assert registry.read_file("alpha", "notes/checklist.md") == "清单正文\n"

        cases = [
            ("../SKILL.md", "路径不合法"),
            ("/etc/passwd", "路径不合法"),
            ("notes/escape.md", "路径不合法"),
            ("big.txt", "文件太大"),
            ("missing.md", "文件不存在"),
        ]
        for path, reason in cases:
            with pytest.raises(AppError) as captured:
                registry.read_file("alpha", path)
            assert captured.value.code is ErrorCode.SKILL_NOT_FOUND
            assert captured.value.details is not None
            assert captured.value.details["reason"] == reason

        await registry.put_skill("my-skill", _write("my-skill"))
        with pytest.raises(AppError) as captured:
            registry.read_file("my-skill", "notes/checklist.md")
        assert captured.value.code is ErrorCode.SKILL_NOT_FOUND
        assert captured.value.details is not None
        assert captured.value.details["reason"] == "只有内置技能有附属文件"

        with pytest.raises(AppError) as captured:
            registry.read_file("no-such-skill", "notes/checklist.md")
        assert captured.value.code is ErrorCode.SKILL_NOT_FOUND

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 10. 预加载校验
# ---------------------------------------------------------------------------
def test_validate_preloads(db_session_factory, builtin_root: Path) -> None:
    registry = _registry(builtin_root, db_session_factory)

    def spec(name: str, skills: tuple[str, ...]) -> SimpleNamespace:
        return SimpleNamespace(name=name, skills=skills)

    registry.validate_preloads([spec("general", ("alpha",))])
    registry.validate_preloads([spec("planner", ("beta",))])
    registry.validate_preloads([spec("general", ("gamma",))])  # 空 agents = 所有代理

    with pytest.raises(SkillBuiltinError) as captured:
        registry.validate_preloads([spec("general", ("no-such-skill",))])
    assert "general" in str(captured.value)
    assert "no-such-skill" in str(captured.value)

    with pytest.raises(SkillBuiltinError) as captured:
        registry.validate_preloads([spec("general", ("beta",))])
    assert "general" in str(captured.value)
    assert "beta" in str(captured.value)


# ---------------------------------------------------------------------------
# 11. 孤儿覆盖行
# ---------------------------------------------------------------------------
def test_orphan_override_row_is_skipped(
    db_session_factory, builtin_root: Path, caplog
) -> None:
    async def scenario() -> None:
        registry = _registry(builtin_root, db_session_factory)
        async with db_session_factory() as session:
            await AgentSkillRepository(session).upsert(
                AgentSkill(
                    id="ghost-skill",
                    title="幽灵",
                    description="内置里已经没有它",
                    body="幽灵正文",
                    tools=[],
                    agents=[],
                    origin="override",
                    status="active",
                    enabled=True,
                )
            )
            await session.commit()

        listed = await registry.list_skills()
        assert [item.id for item in listed] == ["alpha", "beta", "delta", "gamma"]
        assert "ghost-skill" not in {item.id for item in listed}

    with caplog.at_level(logging.WARNING, logger="maa_api.agent.skills.registry"):
        asyncio.run(scenario())
    assert "ghost-skill" in caplog.text
