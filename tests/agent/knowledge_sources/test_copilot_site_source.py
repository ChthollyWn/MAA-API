"""作业站来源（M77 spec §4.5、§6）。

覆盖关卡合并去重与每关查询参数、卡片不含 ``actions`` 与计数（只放 ``meta``）、
当期活动失败不删旧卡、配置校验，以及 ``build_external_sources`` 的装配与
``default_activity_service()`` 回落。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from maa_api.agent.knowledge.sources import build_external_sources
from maa_api.agent.knowledge.sources.copilot_site import CopilotSiteCorpusSource
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.game.activities import ActivityService
from tests.agent.knowledge_sources.support import (
    SettingsStub,
    async_test,
    copilot_rows,
    documents,
    make_service,
    source_row,
)

STAGE = "act1_01"


class FakeCopilot:
    """按关卡回夹具里的两行作业；记录每次查询参数。"""

    def __init__(self, rows_by_stage: dict[str, list] | None = None) -> None:
        self.rows_by_stage = rows_by_stage or {}
        self.calls: list[dict] = []

    async def query(self, *, stage: str, order: str = "hot", page: int = 1, limit: int = 5):
        self.calls.append(
            {"stage": stage, "order": order, "page": page, "limit": limit}
        )
        return SimpleNamespace(rows=list(self.rows_by_stage.get(stage, ())))


class FakeActivity:
    """当期活动服务桩；``error`` 非空时 ``current`` 抛错。"""

    def __init__(self, report=None, error: Exception | None = None) -> None:
        self.report = report
        self.error = error
        self.calls: list[str] = []

    async def current(self, *, detail: str = "summary"):
        self.calls.append(detail)
        if self.error is not None:
            raise self.error
        return self.report


def report(*groups: tuple[str, ...]):
    return SimpleNamespace(
        activities=tuple(
            SimpleNamespace(
                stages=tuple(SimpleNamespace(stage_id=stage) for stage in stages)
            )
            for stages in groups
        )
    )


@async_test
async def test_stage_ids_from_activity_and_config() -> None:
    """关卡 id 合并去重，按每关 ``per_stage_limit`` 查询。"""
    activity = FakeActivity(report(("act1_01", "act1_02"), ("act1_03", "act1_01")))
    client = FakeCopilot()
    source = CopilotSiteCorpusSource(client, activity_service=activity)
    refs = await source.list_refs(
        {"extra_stage_ids": ["extra_1", "act1_01"], "per_stage_limit": 7}
    )
    assert refs == []
    assert activity.calls == ["full"]
    assert [call["stage"] for call in client.calls] == [
        "act1_01",
        "act1_02",
        "act1_03",
        "extra_1",
    ]
    assert all(
        call == {"stage": call["stage"], "order": "hot", "page": 1, "limit": 7}
        for call in client.calls
    )


@async_test
async def test_cards_exclude_actions_and_counters() -> None:
    """正文没有 ``actions`` 内容、没有浏览量与好评率；``meta`` 里有。"""
    activity = FakeActivity(report((STAGE,)))
    client = FakeCopilot({STAGE: copilot_rows()})
    source = CopilotSiteCorpusSource(client, activity_service=activity)
    refs = await source.list_refs({"per_stage_limit": 10})
    docs = await source.fetch(refs, {"per_stage_limit": 10})
    by_id = {document.external_id: document for document in docs}
    assert set(by_id) == {"copilot:9001", "copilot:9002"}

    first = by_id["copilot:9001"]
    assert first.title == "低配 3 人"
    assert first.text == (
        "# 低配 3 人\n"
        "- 关卡：act1_01（act1_01）\n"
        "- 作业编号：9001；上传者：作业作者甲；上传时间：2026-10-01T12:00:00\n"
        "- 干员：先锋甲（技能 1）、狙击乙（技能 2）\n"
        "- 干员组：高台组：术师丙（技能 3）\n"
        "- 说明：先放先锋，再放狙击。\n末尾开技能。"
    )
    assert first.url == "https://prts.maa.plus/copilot/9001"
    assert first.meta == {
        "stage_id": STAGE,
        "site_id": 9001,
        "views": 1234,
        "rating_ratio": 0.98,
        "upload_time": "2026-10-01T12:00:00",
    }

    second = by_id["copilot:9002"]
    assert second.title == "作业 #9002"
    assert second.text == (
        "# 作业 #9002\n"
        "- 关卡：act1_01（act1_01）\n"
        "- 作业编号：9002；上传者：作业作者乙；上传时间：2026-10-02T08:30:00"
    )

    joined = "\n".join(document.text for document in docs)
    for forbidden in (
        "actions",
        "Deploy",
        "SpeedUp",
        "SkillDaemon",
        "1234",
        "0.98",
        "5678",
        "0.87",
        "location",
    ):
        assert forbidden not in joined


@async_test
async def test_activity_failure_raises_and_keeps_docs(db_session_factory) -> None:
    """当期活动抛错时同步失败、文档不删。"""
    activity = FakeActivity(report((STAGE,)))
    client = FakeCopilot({STAGE: copilot_rows()})
    source = CopilotSiteCorpusSource(client, activity_service=activity)
    service = make_service(db_session_factory, source, SettingsStub())
    try:
        await service.sync_source("copilot-site")
        assert len(await documents(db_session_factory, "copilot-site")) == 2
        activity.error = RuntimeError("PRTS 当期活动取不到")
        summary = await service.sync_source("copilot-site")
        assert summary.rebuilt == 0
        assert len(await documents(db_session_factory, "copilot-site")) == 2
        row = await source_row(db_session_factory, "copilot-site")
        assert row is not None and row.status == "error"
        assert "当期活动" in (row.error or "")
    finally:
        await service.close()


def test_config_validation() -> None:
    """``extra_stage_ids`` / ``per_stage_limit`` 的形状与边界。"""
    source = CopilotSiteCorpusSource(FakeCopilot(), activity_service=FakeActivity())
    assert source.validate_config({}) == {
        "extra_stage_ids": [],
        "per_stage_limit": 10,
    }
    assert source.validate_config(
        {"extra_stage_ids": ["a-1", "a-1", "b#2"], "per_stage_limit": 1}
    ) == {"extra_stage_ids": ["a-1", "b#2"], "per_stage_limit": 1}
    for config in (
        {"extra_stage_ids": "a-1"},
        {"extra_stage_ids": [123]},
        {"extra_stage_ids": ["中文关卡"]},
        {"extra_stage_ids": [f"stage-{index}" for index in range(31)]},
        {"per_stage_limit": 0},
        {"per_stage_limit": 21},
        {"per_stage_limit": "10"},
    ):
        with pytest.raises(AppError) as info:
            source.validate_config(config)
        assert info.value.code is ErrorCode.VALIDATION_ERROR
        assert info.value.details["field"] in {
            "extra_stage_ids",
            "per_stage_limit",
        }


def test_build_external_sources_wires_defaults_and_activity() -> None:
    """装配四个来源；活动服务注入时原样使用，``None`` 时来源内部回落默认门面。"""
    activity = FakeActivity()
    sources = build_external_sources(
        session_factory=object(),
        settings_provider=lambda: SettingsStub(),
        maa_docs=object(),
        prts=object(),
        gamedata=object(),
        copilot_site=FakeCopilot(),
        activity_service=activity,
    )
    assert [source.source_id for source in sources] == [
        "maa-docs",
        "prts",
        "gamedata",
        "copilot-site",
    ]
    for source in sources:
        assert source.trusted is False
        assert source.searchable_by_default is True
        assert source.scheduled_refresh is True
    assert sources[3].activity_service is activity
    assert CopilotSiteCorpusSource(FakeCopilot()).activity_service is not None
    assert isinstance(
        CopilotSiteCorpusSource(FakeCopilot()).activity_service, ActivityService
    )
