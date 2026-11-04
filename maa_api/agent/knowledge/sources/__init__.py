"""M77 四个外部语料来源（spec §4.2、§4.5）。

四个来源都进入 M76 的检索索引：``maa-docs`` 拉 MAA 官方文档、``prts`` 拉种子页
并接收随读推送、``gamedata`` 把拆包实体生成卡片、``copilot-site`` 把作业站
元数据生成卡片。正文只存本机库（V3-D43），日志不记外部正文。

:func:`build_external_sources` 是装配入口；客户端可以注入（测试与多来源装配），
不注入时用各模块的默认门面。作业站的当期活动服务由来源内部回落
``default_activity_service()``——仓库没有 ``app.state.activity_service``（M77
计划接缝裁决 2），装配处仍按 spec 传 ``getattr(app.state, "activity_service", None)``。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.knowledge.index.indexer import CorpusSource
from maa_api.agent.knowledge.sources.copilot_site import CopilotSiteCorpusSource
from maa_api.agent.knowledge.sources.gamedata import GamedataCorpusSource
from maa_api.agent.knowledge.sources.maa_docs import MaaDocsSource
from maa_api.agent.knowledge.sources.prts import PrtsCorpusSource
from maa_api.services.external.copilot_site import (
    CopilotSiteClient,
    default_copilot_site_client,
)
from maa_api.services.external.gamedata import GamedataClient, default_gamedata_client
from maa_api.services.external.maa_docs import MaaDocsClient, default_maa_docs_client
from maa_api.services.external.prts import PrtsClient

__all__ = [
    "CopilotSiteCorpusSource",
    "GamedataCorpusSource",
    "MaaDocsSource",
    "PrtsCorpusSource",
    "build_external_sources",
]


def build_external_sources(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings_provider: Callable[[], Any],
    maa_docs: MaaDocsClient | None = None,
    prts: PrtsClient | None = None,
    gamedata: GamedataClient | None = None,
    copilot_site: CopilotSiteClient | None = None,
    activity_service: Any = None,
) -> list[CorpusSource]:
    """构造四个外部语料来源（顺序即接口里的展示顺序）。

    :param session_factory: 随读页与 ``prune`` 要读本机库，只有 ``prts`` 用。
    :param settings_provider: 每次现读设置；PRTS 随读推送用它检查开关。
    :param activity_service: 作业站取当期关卡用；``None`` 时由来源内部回落
        :func:`~maa_api.services.game.activities.default_activity_service`。
    """
    return [
        MaaDocsSource(maa_docs or default_maa_docs_client()),
        PrtsCorpusSource(
            prts or PrtsClient(),
            session_factory=session_factory,
            settings_provider=settings_provider,
        ),
        GamedataCorpusSource(gamedata or default_gamedata_client()),
        CopilotSiteCorpusSource(
            copilot_site or default_copilot_site_client(),
            activity_service=activity_service,
        ),
    ]
