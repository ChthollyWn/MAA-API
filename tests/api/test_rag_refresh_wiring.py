"""M77 T6：外部语料装配与定时刷新的 lifespan 接线（spec §4.5、§5 T6、§6）。

从生产 ``create_app()`` 的真实 lifespan 断言三件事：``maa-rag-refresh`` 按
``rag.refresh_cron`` 注册、从没建过索引且开关为真的来源各有一次 120 秒后的首次
同步、``GET /api/agent/retrieval/sources`` 列出六个来源且外部四个的
``setting_enabled`` 与设置一致。

首次同步、定时刷新与索引写库共用每来源锁，用例先等启动索引的后台任务收尾再读
接口，避免为抢 SQLite 写锁造成的假失败（M76 wiring 用例同款做法）。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from apscheduler.triggers.date import DateTrigger
from fastapi.testclient import TestClient

from maa_api.agent.knowledge.index.indexer import KnowledgeIndexService
from maa_api.main import create_app
from maa_api.settings import get_settings

#: 后台首次同步的等待上限；正常几百毫秒内完成，留足余量只防慢机器。
_INDEX_TIMEOUT_SECONDS = 20.0

#: 四个外部语料的来源 id（spec §4.2 的表）。
_EXTERNAL_IDS = ("maa-docs", "prts", "gamedata", "copilot-site")


def _wait_for_startup_index(index_service: KnowledgeIndexService) -> None:
    """等 ``start()`` 起的后台索引任务收尾（它只同步 builtin / user）。"""
    deadline = time.monotonic() + _INDEX_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        pending = [task for task in index_service._tasks if not task.done()]
        if not pending:
            return
        time.sleep(0.05)
    raise AssertionError("启动索引没有在超时内结束")


def test_refresh_job_registered_with_cron(tmp_settings, isolated_db, monkeypatch) -> None:
    """``maa-rag-refresh`` 存在，且触发器来自 ``rag.refresh_cron``（不是写死的默认值）。"""
    monkeypatch.setenv("MAA_RAG_REFRESH_CRON", "30 5 * * *")
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    with client:
        scheduler = getattr(app.state, "update_scheduler", None)
        assert scheduler is not None, "lifespan 没有挂 app.state.update_scheduler"
        job = scheduler.get_job("maa-rag-refresh")
        assert job is not None, "lifespan 没有注册 maa-rag-refresh"
        trigger = str(job.trigger)
        # 默认值是 0 4 * * *；环境变量层改成 30 5 后两个字段都必须跟着变。
        assert "hour='5'" in trigger, trigger
        assert "minute='30'" in trigger, trigger


def test_first_sync_jobs_for_never_indexed_sources(
    tmp_settings, isolated_db, monkeypatch
) -> None:
    """没建过索引且开关为真的四个外部来源各有一个 120 秒后的首次同步任务。"""
    monkeypatch.setenv("MAA_RAG_COPILOT_SITE_ENABLED", "true")
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    before = datetime.now(timezone.utc)
    with client:
        index_service = getattr(app.state, "knowledge_index_service", None)
        assert isinstance(index_service, KnowledgeIndexService), (
            "lifespan 没有设置 app.state.knowledge_index_service"
        )
        _wait_for_startup_index(index_service)
        scheduler = app.state.update_scheduler
        jobs = {
            job.id: job
            for job in scheduler.get_jobs()
            if job.id.startswith("maa-rag-first-sync-")
        }
    after = datetime.now(timezone.utc)

    expected = {f"maa-rag-first-sync-{source_id}" for source_id in _EXTERNAL_IDS}
    assert set(jobs) == expected
    for source_id in _EXTERNAL_IDS:
        job = jobs[f"maa-rag-first-sync-{source_id}"]
        assert isinstance(job.trigger, DateTrigger)
        assert job.args == (source_id,)
        assert job.func == index_service.sync_source
        # 排期是启动后的第 120 秒；给启动与注册留一点余量。
        assert (
            before + timedelta(seconds=110)
            <= job.trigger.run_date
            <= after + timedelta(seconds=130)
        )


def test_sources_list_shows_external_corpora(tmp_settings, isolated_db) -> None:
    """真实 lifespan 后来源列表有六个来源，外部四个的 ``setting_enabled`` 与设置一致。"""
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    with client:
        index_service = getattr(app.state, "knowledge_index_service", None)
        assert isinstance(index_service, KnowledgeIndexService)
        _wait_for_startup_index(index_service)
        response = client.get("/api/agent/retrieval/sources")
        assert response.status_code == 200
        payload = response.json()
        items = {item["id"]: item for item in payload["items"]}
        scheduler = app.state.update_scheduler
        first_sync_ids = {
            job.id
            for job in scheduler.get_jobs()
            if job.id.startswith("maa-rag-first-sync-")
        }
        settings = get_settings()

    # M79 起另有 history/memory 两个推送式来源（默认不参与检索）。
    assert len(payload["items"]) == 8, sorted(items)
    assert set(items) == {"builtin", "user", *_EXTERNAL_IDS, "history", "memory"}
    # 默认 copilot_site_enabled=false：首次同步任务只给另外三个开关为真的来源；
    # 关闭的来源由 never_indexed() 过滤（不是晚点再排）。
    assert first_sync_ids == {
        "maa-rag-first-sync-maa-docs",
        "maa-rag-first-sync-prts",
        "maa-rag-first-sync-gamedata",
    }
    expected = {
        "maa-docs": (settings.rag.maa_docs_enabled, "maa_docs"),
        "prts": (settings.rag.prts_enabled, "prts"),
        "gamedata": (settings.rag.gamedata_enabled, "gamedata"),
        "copilot-site": (settings.rag.copilot_site_enabled, "copilot_site"),
    }
    for source_id, (enabled, kind) in expected.items():
        assert items[source_id]["setting_enabled"] is enabled
        assert items[source_id]["kind"] == kind
        assert items[source_id]["license"]
