"""作业资源（``ResourceAsset`` kind=COPILOT）的保留与清理（M29，规格 §9.11）。

五个条件逐条正反例：``meta.source ∈ {ai, site}``、未设 ``retain``、没有成功的尝试、
创建超过保留天数（默认 7，可由 ``agent.copilot_retention_days`` 改）、没有被进行中的
授权 / 定时任务 / 排队中的任务引用。用户上传的作业（``meta.source`` 缺失或为 ``user``）
与点过「保留」的作业永不自动删除。

「引用中」的三个来源都在库里现查：``copilot_grant``（表由 C 工作流建，这里用
``DROP + CREATE`` 造最小表，表不存在时清理逻辑必须照常工作）、``schedule.template``
里的 ``asset_id``、未结束流水线里未结束的 Copilot 任务 ``params/raw_params.asset_id``。

用例形态：同步测试函数 + ``asyncio.run(scenario())``（本仓无 pytest-asyncio），
临时资源根 / 库 / 会话工厂来自 ``tests/services/conftest.py``。
"""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text

from maa_api.db.models import (
    Pipeline,
    ResourceAsset,
    Schedule,
    Task,
    utcnow,
)
from maa_api.domain.enums import (
    PipelineSource,
    PipelineStatus,
    Priority,
    ResourceAssetKind,
    TaskStatus,
)
from maa_api.services import retention_service as r

#: 最小 ``copilot_grant`` 表：只保留引用判定要读的列（附录 E 的子集）。
GRANT_TABLE_SQL = """
CREATE TABLE copilot_grant (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    stage_id TEXT,
    stage_code TEXT,
    status TEXT
)
"""


# ---------------------------------------------------------------------------
# 构造 helper
# ---------------------------------------------------------------------------
def _copilot(
    asset_id: str,
    *,
    source: str | None = "ai",
    retain: bool | None = None,
    attempts: list | None = None,
    created_at: datetime | None = None,
    name: str | None = None,
    path: str | None = None,
    stage_id: str | None = "main_01-07",
    session_id: str | None = "sess-1",
    kind: ResourceAssetKind = ResourceAssetKind.COPILOT,
) -> ResourceAsset:
    """造一条作业资源；``source=None`` 模拟没有来源标记的用户上传。"""
    meta: dict = {}
    if source is not None:
        meta["source"] = source
    if retain is not None:
        meta["retain"] = retain
    if attempts is not None:
        meta["attempts"] = attempts
    if stage_id is not None:
        meta["stage_id"] = stage_id
    if session_id is not None:
        meta["session_id"] = session_id
    return ResourceAsset(
        id=asset_id,
        kind=kind,
        name=name or f"作业 {asset_id}",
        content=None if path else {"stage_name": "1-7"},
        path=path,
        meta=meta,
        created_at=created_at or utcnow(),
    )


def _old(asset_id: str, **kwargs) -> ResourceAsset:
    """默认「全部条件都满足」的候选：ai 来源、8 天前创建、无成功、无引用。"""
    kwargs.setdefault("created_at", utcnow() - timedelta(days=8))
    return _copilot(asset_id, **kwargs)


def _pipeline(pipeline_id: str = "p-1", *, status: PipelineStatus = PipelineStatus.PENDING) -> Pipeline:
    return Pipeline(
        id=pipeline_id,
        source=PipelineSource.AGENT,
        priority=int(Priority.AGENT),
        status=status,
        task_count=1,
    )


def _task(
    asset_id: str,
    *,
    task_id: str = "t-1",
    pipeline_id: str = "p-1",
    status: TaskStatus = TaskStatus.PENDING,
) -> Task:
    return Task(
        id=task_id,
        pipeline_id=pipeline_id,
        order_index=0,
        type_name="Copilot",
        task_name="Copilot",
        params={"asset_id": asset_id, "formation": True},
        status=status,
    )


async def _insert(factory, rows) -> None:
    async with factory() as db:
        db.add_all(rows)
        await db.commit()


async def _make_grant_table(factory, rows: list[dict]) -> None:
    """造最小 ``copilot_grant`` 表并插入授权行（表不存在时清理必须照常工作）。"""
    async with factory() as db:
        await db.execute(text("DROP TABLE IF EXISTS copilot_grant"))
        await db.execute(text(GRANT_TABLE_SQL))
        for row in rows:
            await db.execute(
                text(
                    "INSERT INTO copilot_grant (id, session_id, stage_id, stage_code, status) "
                    "VALUES (:id, :session_id, :stage_id, :stage_code, :status)"
                ),
                row,
            )
        await db.commit()


async def _assets(factory) -> dict[str, ResourceAsset]:
    async with factory() as db:
        rows = (await db.execute(select(ResourceAsset))).scalars().all()
    return {row.id: row for row in rows}


def _reasons(outcome: dict) -> dict[str, str]:
    return {item["id"]: item["reason"] for item in outcome["skipped"]}


def _cleanup(factory, **kwargs) -> dict:
    return asyncio.run(r.cleanup_copilots(session_factory=factory, **kwargs))


# ---------------------------------------------------------------------------
# 条件 1：来源 ai / site 才自动删
# ---------------------------------------------------------------------------
def test_sources_ai_and_site_are_cleaned_user_uploads_are_not(
    retention_session_factory,
) -> None:
    """ai / site 副本会删；用户上传（source=user 或没有来源标记）永不自动删除。"""
    now = utcnow()
    assets = [
        _old("asset-ai", source="ai", created_at=now - timedelta(days=8)),
        _old("asset-site", source="site", created_at=now - timedelta(days=8)),
        _old("asset-user", source="user", created_at=now - timedelta(days=8)),
        _old("asset-none", source=None, created_at=now - timedelta(days=8)),
    ]

    async def scenario():
        await _insert(retention_session_factory, assets)
        return await r.cleanup_copilots(
            now=now, session_factory=retention_session_factory
        )

    outcome = asyncio.run(scenario())

    assert sorted(outcome["deleted"]) == ["asset-ai", "asset-site"], outcome
    assert _reasons(outcome) == {"asset-user": "source", "asset-none": "source"}
    remaining = asyncio.run(_assets(retention_session_factory))
    assert sorted(remaining) == ["asset-none", "asset-user"]


# ---------------------------------------------------------------------------
# 条件 2：retain
# ---------------------------------------------------------------------------
def test_retain_protects_and_false_is_still_cleaned(retention_session_factory) -> None:
    now = utcnow()

    async def scenario():
        await _insert(
            retention_session_factory,
            [
                _old("asset-retain", retain=True, created_at=now - timedelta(days=30)),
                _old("asset-no-retain", retain=False, created_at=now - timedelta(days=8)),
            ],
        )
        return await r.cleanup_copilots(
            now=now, session_factory=retention_session_factory
        )

    outcome = asyncio.run(scenario())

    assert outcome["deleted"] == ["asset-no-retain"], outcome
    assert _reasons(outcome) == {"asset-retain": "retained"}


# ---------------------------------------------------------------------------
# 条件 3：没有成功的尝试
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "attempt",
    [
        {"id": "a1", "result": "three_star", "judged_by": "core"},
        {"id": "a1", "result": "two_star", "judged_by": "core"},
        {"id": "a1", "result": "clear", "judged_by": "model"},
        {"id": "a1", "outcome": "succeeded", "judged_by": "core"},
        {"id": "a1", "status": "success", "judged_by": "core"},
        {"id": "a1", "success": True, "result": "unknown"},
        # 记录自相矛盾时，任何一处正面证据都算成功（宁可保留，不误删）
        {"id": "a1", "success": False, "result": "three_star"},
        # C 工作流的真实写法：CopilotAttemptRepository 把 §9.7 的 outcome 存进 meta
        {
            "id": "attempt-1", "copilot_id": "asset-ok", "grant_id": "grant-1",
            "pipeline_id": "p-1", "outcome": "three_star", "judged_by": "core",
            "counted": True, "attempt_number": 2,
        },
    ],
)
def test_successful_attempt_protects(retention_session_factory, attempt) -> None:
    """任何一条成功记录都让作业永不自动删除（结果 / 状态 / 显式 success 三种写法）。"""
    now = utcnow()

    async def scenario():
        await _insert(
            retention_session_factory,
            [_old("asset-ok", attempts=[attempt], created_at=now - timedelta(days=60))],
        )
        return await r.cleanup_copilots(
            now=now, session_factory=retention_session_factory
        )

    outcome = asyncio.run(scenario())

    assert outcome["deleted"] == [], outcome
    assert _reasons(outcome) == {"asset-ok": "succeeded"}


@pytest.mark.parametrize(
    "attempt",
    [
        {"id": "a1", "result": "failed"},
        {"id": "a1", "result": "support_unavailable"},
        {"id": "a1", "result": "sanity_insufficient"},
        {"id": "a1", "result": "unknown"},
        {"id": "a1", "result": "error"},
        {"id": "a1", "result": "not_started"},
        {"id": "a1", "success": False},
        {"id": "a1", "succeeded": False},
        # C 工作流的真实写法：没打过的 outcome 不构成成功
        {
            "id": "attempt-1", "copilot_id": "asset-bad", "grant_id": "grant-1",
            "outcome": "support_unavailable", "judged_by": "core", "counted": True,
        },
        "不是字典",
    ],
)
def test_unsuccessful_or_unreadable_attempts_do_not_protect(
    retention_session_factory, attempt
) -> None:
    now = utcnow()

    async def scenario():
        await _insert(
            retention_session_factory,
            [_old("asset-bad", attempts=[attempt], created_at=now - timedelta(days=8))],
        )
        return await r.cleanup_copilots(
            now=now, session_factory=retention_session_factory
        )

    outcome = asyncio.run(scenario())

    assert outcome["deleted"] == ["asset-bad"], outcome


# ---------------------------------------------------------------------------
# 条件 4：创建超过保留天数（默认 7）
# ---------------------------------------------------------------------------
def test_created_at_seven_day_boundary(retention_session_factory) -> None:
    """刚满 7 天（``created_at == now - 7d``）不算「超过 7 天」，多 1 秒才删。"""
    now = utcnow()

    async def scenario():
        await _insert(
            retention_session_factory,
            [
                _old("asset-exactly-7d", created_at=now - timedelta(days=7)),
                _old("asset-over-7d", created_at=now - timedelta(days=7, seconds=1)),
            ],
        )
        return await r.cleanup_copilots(
            now=now, session_factory=retention_session_factory
        )

    outcome = asyncio.run(scenario())

    assert outcome["deleted"] == ["asset-over-7d"], outcome
    assert _reasons(outcome) == {"asset-exactly-7d": "too_new"}


def test_default_retention_days_is_seven(monkeypatch) -> None:
    """代码默认值 7 天：不依赖仓库里的 config.yaml 与进程内缓存。"""
    from maa_api.settings import Settings

    monkeypatch.setattr(r, "get_settings", lambda: Settings())
    assert r.COPILOT_RETENTION_DAYS == 7
    assert r._copilot_retention_days() == 7


def test_retention_days_follow_settings(retention_session_factory, monkeypatch) -> None:
    """``agent.copilot_retention_days`` 改成 3 后，4 天前的删、2 天前的留。"""
    from maa_api.settings import AgentSettings, Settings

    monkeypatch.setattr(
        r, "get_settings", lambda: Settings(agent=AgentSettings(copilot_retention_days=3))
    )
    now = utcnow()

    async def scenario():
        await _insert(
            retention_session_factory,
            [
                _old("asset-4d", created_at=now - timedelta(days=4)),
                _old("asset-2d", created_at=now - timedelta(days=2)),
            ],
        )
        return await r.cleanup_copilots(
            now=now, session_factory=retention_session_factory
        )

    outcome = asyncio.run(scenario())

    assert outcome["deleted"] == ["asset-4d"], outcome
    assert _reasons(outcome) == {"asset-2d": "too_new"}


# ---------------------------------------------------------------------------
# 条件 5a：进行中的授权
# ---------------------------------------------------------------------------
def test_active_grant_reference_protects(retention_session_factory) -> None:
    """同一会话或同一关卡的 active 授权都算引用；终态授权不算。"""
    now = utcnow()

    async def scenario():
        await _make_grant_table(
            retention_session_factory,
            [
                {"id": "g-1", "session_id": "sess-1", "stage_id": "main_01-07",
                 "stage_code": "1-7", "status": "active"},
                {"id": "g-2", "session_id": "sess-finished", "stage_id": "main_01-08",
                 "stage_code": "1-8", "status": "succeeded"},
            ],
        )
        await _insert(
            retention_session_factory,
            [
                _old("asset-grant-session", session_id="sess-1", stage_id="main_09-01",
                     created_at=now - timedelta(days=8)),
                _old("asset-grant-stage", session_id="sess-other", stage_id="main_01-07",
                     created_at=now - timedelta(days=8)),
                _old("asset-grant-finished", session_id="sess-finished",
                     stage_id="main_01-08", created_at=now - timedelta(days=8)),
                _old("asset-grant-none", session_id="sess-none", stage_id="main_02-01",
                     created_at=now - timedelta(days=8)),
            ],
        )
        return await r.cleanup_copilots(
            now=now, session_factory=retention_session_factory
        )

    outcome = asyncio.run(scenario())

    assert sorted(outcome["deleted"]) == ["asset-grant-finished", "asset-grant-none"], outcome
    assert _reasons(outcome) == {
        "asset-grant-session": "referenced",
        "asset-grant-stage": "referenced",
    }


def test_active_grant_in_real_table_protects(retention_session_factory) -> None:
    """与真表联调：列名取 ``CopilotGrant`` 模型，而不是上面自造的最小表。

    只按 ``stage_id`` 匹配（``session_id`` 为空，避开对 ``agent_session`` 的外键），
    覆盖「作业只给得出关卡」的情形。
    """
    from maa_api.db.models import CopilotGrant

    now = utcnow()

    async def scenario():
        await _insert(
            retention_session_factory,
            [
                CopilotGrant(
                    id="grant-real",
                    session_id=None,
                    stage_id="main_01-07",
                    stage_code="1-7",
                    status="active",
                    expires_at=now + timedelta(minutes=60),
                ),
                _old(
                    "asset-real-grant",
                    session_id=None,
                    stage_id="main_01-07",
                    created_at=now - timedelta(days=8),
                ),
            ],
        )
        return await r.cleanup_copilots(
            now=now, session_factory=retention_session_factory
        )

    outcome = asyncio.run(scenario())

    assert outcome["deleted"] == [], outcome
    assert _reasons(outcome) == {"asset-real-grant": "referenced"}


def test_missing_grant_table_does_not_block_cleanup(retention_session_factory) -> None:
    """``copilot_grant`` 还没落库（C 工作流未合并 / 库正在迁移）时清理照常工作。"""
    now = utcnow()

    async def scenario():
        async with retention_session_factory() as db:
            await db.execute(text("DROP TABLE IF EXISTS copilot_grant"))
            await db.commit()
        await _insert(
            retention_session_factory,
            [_old("asset-no-table", created_at=now - timedelta(days=8))],
        )
        return await r.cleanup_copilots(
            now=now, session_factory=retention_session_factory
        )

    outcome = asyncio.run(scenario())

    assert outcome["deleted"] == ["asset-no-table"], outcome


# ---------------------------------------------------------------------------
# 条件 5b：定时任务
# ---------------------------------------------------------------------------
def test_schedule_reference_protects(retention_session_factory) -> None:
    now = utcnow()

    async def scenario():
        await _insert(
            retention_session_factory,
            [
                Schedule(
                    id="sched-1",
                    name="每日作业",
                    cron="0 4 * * *",
                    enabled=True,
                    template=[{"name": "Fight", "stage": "1-7"},
                              {"name": "Copilot", "asset_id": "asset-scheduled"}],
                ),
                # 停用的定时任务也算引用：用户可能随时重新启用
                Schedule(
                    id="sched-2",
                    name="停用的作业",
                    cron="0 5 * * *",
                    enabled=False,
                    template=[{"name": "Copilot", "asset_id": "asset-disabled"}],
                ),
                _old("asset-scheduled", created_at=now - timedelta(days=8)),
                _old("asset-disabled", created_at=now - timedelta(days=8)),
                _old("asset-other", created_at=now - timedelta(days=8)),
            ],
        )
        return await r.cleanup_copilots(
            now=now, session_factory=retention_session_factory
        )

    outcome = asyncio.run(scenario())

    assert outcome["deleted"] == ["asset-other"], outcome
    assert _reasons(outcome) == {
        "asset-scheduled": "referenced",
        "asset-disabled": "referenced",
    }


# ---------------------------------------------------------------------------
# 条件 5c：排队中的任务
# ---------------------------------------------------------------------------
def test_queued_task_reference_protects(retention_session_factory) -> None:
    """未结束流水线里未结束的 Copilot 任务算引用；已结束的都不算。"""
    now = utcnow()

    async def scenario():
        await _insert(
            retention_session_factory,
            [
                _pipeline("p-queued", status=PipelineStatus.PENDING),
                _task("asset-queued", pipeline_id="p-queued"),
                _pipeline("p-running", status=PipelineStatus.RUNNING),
                _task("asset-running", task_id="t-2", pipeline_id="p-running"),
                _pipeline("p-done", status=PipelineStatus.COMPLETED),
                _task("asset-done", task_id="t-3", pipeline_id="p-done"),
                _pipeline("p-cancelled", status=PipelineStatus.CANCELLED),
                _task("asset-cancelled", task_id="t-4", pipeline_id="p-cancelled"),
                _old("asset-queued", created_at=now - timedelta(days=8)),
                _old("asset-running", created_at=now - timedelta(days=8)),
                _old("asset-done", created_at=now - timedelta(days=8)),
                _old("asset-cancelled", created_at=now - timedelta(days=8)),
                _old("asset-free", created_at=now - timedelta(days=8)),
            ],
        )
        return await r.cleanup_copilots(
            now=now, session_factory=retention_session_factory
        )

    outcome = asyncio.run(scenario())

    assert sorted(outcome["deleted"]) == [
        "asset-cancelled",
        "asset-done",
        "asset-free",
    ], outcome
    assert _reasons(outcome) == {
        "asset-queued": "referenced",
        "asset-running": "referenced",
    }


def test_finished_task_in_queued_pipeline_does_not_protect(
    retention_session_factory,
) -> None:
    """流水线还在排队、但该任务已结束（重跑等），不再算引用。"""
    now = utcnow()

    async def scenario():
        await _insert(
            retention_session_factory,
            [
                _pipeline("p-1", status=PipelineStatus.PENDING),
                _task("asset-done-task", status=TaskStatus.COMPLETED),
                _old("asset-done-task", created_at=now - timedelta(days=8)),
            ],
        )
        return await r.cleanup_copilots(
            now=now, session_factory=retention_session_factory
        )

    outcome = asyncio.run(scenario())

    assert outcome["deleted"] == ["asset-done-task"], outcome


# ---------------------------------------------------------------------------
# 文件、其它 kind、并入每日任务
# ---------------------------------------------------------------------------
def test_deleted_asset_file_is_removed_and_escaping_path_is_ignored(
    resource_root, retention_session_factory
) -> None:
    """删记录同时删 ``resource/`` 下的作业文件；越界路径的文件不动。"""
    now = utcnow()
    relative = "agent-assets/copilot/asset-file.json"
    inside = resource_root / relative
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_text("{}", encoding="utf8")
    outside = resource_root.parent / "outside.json"
    outside.write_text("{}", encoding="utf8")

    async def scenario():
        await _insert(
            retention_session_factory,
            [
                _old("asset-file", path=relative, created_at=now - timedelta(days=8)),
                _old("asset-escape", path="../outside.json",
                     created_at=now - timedelta(days=8)),
            ],
        )
        return await r.cleanup_copilots(
            now=now, session_factory=retention_session_factory
        )

    outcome = asyncio.run(scenario())

    assert sorted(outcome["deleted"]) == ["asset-escape", "asset-file"], outcome
    assert not inside.exists()
    assert outside.exists()  # 越界路径只跳过文件，记录照删（脏数据不该让清理失效）
    assert outside.read_text(encoding="utf8") == "{}"


def test_non_copilot_assets_are_untouched(retention_session_factory) -> None:
    """其它 kind 的资源（基建方案、自定义任务…）不在这条清理的范围内。"""
    now = utcnow()

    async def scenario():
        await _insert(
            retention_session_factory,
            [
                _old("asset-infrast", kind=ResourceAssetKind.INFRAST_PLAN,
                     created_at=now - timedelta(days=30)),
                _old("asset-custom", kind=ResourceAssetKind.CUSTOM_TASK,
                     created_at=now - timedelta(days=30)),
            ],
        )
        return await r.cleanup_copilots(
            now=now, session_factory=retention_session_factory
        )

    outcome = asyncio.run(scenario())

    assert outcome == {"deleted": [], "skipped": []}
    assert sorted(asyncio.run(_assets(retention_session_factory))) == [
        "asset-custom",
        "asset-infrast",
    ]


def test_run_retention_merges_copilot_cleanup(retention_session_factory) -> None:
    """每日任务里并入作业清理，既有四个 key 的形状与语义不变。"""
    now = utcnow()

    async def scenario():
        await _insert(
            retention_session_factory,
            [
                _old("asset-old", created_at=now - timedelta(days=8)),
                _old("asset-keep", source="user", created_at=now - timedelta(days=8)),
            ],
        )
        return await r.run_retention(session_factory=retention_session_factory)

    out = asyncio.run(scenario())

    assert out["copilots_deleted"] == 1, out
    assert out["logs_deleted"] == 0, out
    assert out["pipelines_deleted"] == 0, out
    assert out["screenshots_deleted"] == 0, out
    assert out["temp_files_deleted"] == 0, out
    assert sorted(asyncio.run(_assets(retention_session_factory))) == ["asset-keep"]


def test_cleanup_is_idempotent(retention_session_factory) -> None:
    """同一批资源跑两轮：第二轮没有可删的，也不报错。"""
    now = utcnow()

    async def scenario():
        await _insert(
            retention_session_factory,
            [_old("asset-once", created_at=now - timedelta(days=8))],
        )
        first = await r.cleanup_copilots(
            now=now, session_factory=retention_session_factory
        )
        second = await r.cleanup_copilots(
            now=now, session_factory=retention_session_factory
        )
        return first, second

    first, second = asyncio.run(scenario())

    assert first["deleted"] == ["asset-once"], first
    assert second == {"deleted": [], "skipped": []}, second


def test_default_session_factory_and_resource_root_from_module(
    resource_root, retention_session_factory, monkeypatch
) -> None:
    """``session_factory=None`` 时取 session 模块属性，资源根从 ``DB_PATH`` 推导。"""
    from maa_api.db import session as db_session_module

    monkeypatch.setattr(db_session_module, "session_factory", retention_session_factory)
    now = utcnow()
    relative = "agent-assets/copilot/asset-default.json"
    path = resource_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf8")

    async def scenario():
        await _insert(
            retention_session_factory,
            [_old("asset-default", path=relative, created_at=now - timedelta(days=8))],
        )
        return await r.cleanup_copilots(now=now)

    outcome = asyncio.run(scenario())

    assert outcome["deleted"] == ["asset-default"], outcome
    assert not path.exists()
