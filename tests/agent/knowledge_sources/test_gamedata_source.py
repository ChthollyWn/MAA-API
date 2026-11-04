"""拆包实体卡来源（M77 spec §4.5、§6）。

覆盖筛选（不可获得干员、``NONE`` 类道具、图鉴隐藏敌人都不出卡）、模板逐字、
富文本清理与无属性数值、卡片版本未变不重建、单表失败不删旧卡与配置校验。
夹具是手写的精简三表，不放真实大表。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from maa_api.agent.knowledge.sources.gamedata import GamedataCorpusSource
from maa_api.domain.errors import AppError, ErrorCode
from tests.agent.knowledge_sources.support import (
    SettingsStub,
    async_test,
    documents,
    fixture,
    make_service,
    source_row,
)

TABLES = {
    "character_table.json": fixture("character_table.json"),
    "item_table.json": fixture("item_table.json"),
    "enemy_handbook_table.json": fixture("enemy_handbook_table.json"),
}


class FakeGamedata:
    """按文件名回夹具；``fail`` 里的表抛来源不可用。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail: set[str] = set()

    async def corpus_table(self, name: str):
        self.calls.append(name)
        if name in self.fail:
            raise AppError(ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE, "拆包表下载超时")
        return SimpleNamespace(data=TABLES[name])


EXPECTED = {
    "char:char_1001_alpha": (
        "干员 阿尔法",
        "# 阿尔法（Alpha）\n"
        "- 稀有度：5★\n"
        "- 职业：近卫；分支：sub_warrior_1\n"
        "- 位置：近战位\n"
        "- 标签：近战位、输出\n"
        "- 特性：可以阻挡两个敌人\n"
        "- 获取方式：公开招募、关卡掉落\n"
        "- 简介：用途第一行\n"
        "用途第二行",
    ),
    "char:char_1002_beta": (
        "干员 贝塔",
        "# 贝塔\n"
        "- 稀有度：4★\n"
        "- 职业：狙击\n"
        "- 位置：远程位\n"
        "- 特性：优先攻击空中单位\n"
        "- 简介：简介只有一行",
    ),
    "char:char_1003_gamma": (
        "干员 伽马",
        "# 伽马（Gamma）\n"
        "- 稀有度：6★\n"
        "- 职业：术师；分支：sub_caster_1\n"
        "- 位置：远程位\n"
        "- 标签：远程位、输出\n"
        "- 获取方式：仅限活动",
    ),
    "item:3001": (
        "道具 固源岩",
        "# 固源岩\n"
        "- 稀有度：2★\n"
        "- 分类：材料\n"
        "- 用途：基础材料\n"
        "用于合成\n"
        "- 说明：常见的矿石材料。\n"
        "- 获取方式：关卡掉落",
    ),
    "item:3002": (
        "道具 应急理智顶液",
        "# 应急理智顶液\n"
        "- 稀有度：4★\n"
        "- 分类：消耗品\n"
        "- 用途：恢复理智\n"
        "- 获取方式：采购中心",
    ),
    "item:3003": ("道具 源岩", "# 源岩\n- 分类：材料"),
    "enemy:enemy_1001_slime": (
        "敌人 源石虫",
        "# 源石虫（01）\n"
        "- 级别：普通\n"
        "- 伤害类型：物理\n"
        "- 攻击方式：MELEE\n"
        "- 能力：缓慢移动；受到伤害时分裂\n"
        "- 描述：常见的源石虫。",
    ),
    "enemy:enemy_1002_mage": (
        "敌人 术师",
        "# 术师（02）\n"
        "- 级别：精英\n"
        "- 伤害类型：法术、不造成伤害\n"
        "- 攻击方式：RANGED\n"
        "- 能力：远程法术攻击\n"
        "- 描述：会释放法术的敌人。",
    ),
    "enemy:enemy_1003_boss": (
        "敌人 领袖",
        "# 领袖（03）\n"
        "- 级别：领袖\n"
        "- 伤害类型：物理、法术、治疗\n"
        "- 攻击方式：MELEE",
    ),
}


def _source() -> tuple[GamedataCorpusSource, FakeGamedata]:
    fake = FakeGamedata()
    return GamedataCorpusSource(fake), fake


@async_test
async def test_cards_follow_templates_and_filters() -> None:
    """夹具里不可获得的干员、``NONE`` 类道具、图鉴隐藏的敌人都不出卡片；卡片逐字匹配模板。"""
    source, fake = _source()
    refs = await source.list_refs({})
    docs = await source.fetch(refs, {})
    assert fake.calls == [
        "character_table.json",
        "item_table.json",
        "enemy_handbook_table.json",
    ]
    assert {doc.external_id for doc in docs} == set(EXPECTED)
    assert not any(
        external_id.startswith(("char:char_1004", "item:3004", "enemy:enemy_1004"))
        for external_id in {doc.external_id for doc in docs}
    )
    for document in docs:
        title, text = EXPECTED[document.external_id]
        assert document.title == title
        assert document.text == text


@async_test
async def test_markup_cleaned_and_no_stat_numbers() -> None:
    """没有 ``<@``、``</>``、字面量 ``\\n``；卡片不含 ``phases`` / ``attributes`` 数值。"""
    source, _ = _source()
    docs = await source.fetch(await source.list_refs({}), {})
    joined = "\n".join(document.text for document in docs)
    assert "<@" not in joined
    assert "</>" not in joined
    assert "\\n" not in joined
    for forbidden in ("phases", "attributes", "attributesKeyFrames", "maxHp"):
        assert forbidden not in joined
    assert "用途第一行\n用途第二行" in joined


@async_test
async def test_unchanged_cards_are_skipped(db_session_factory) -> None:
    """第二次同步 ``rebuilt == 0``。"""
    source, fake = _source()
    service = make_service(db_session_factory, source, SettingsStub())
    try:
        first = await service.sync_source("gamedata")
        assert first.rebuilt == 9
        second = await service.sync_source("gamedata")
        assert second.rebuilt == 0
        assert second.fetched == 0
        assert len(fake.calls) == 6
        assert len(await documents(db_session_factory, "gamedata")) == 9
    finally:
        await service.close()


@async_test
async def test_table_failure_deletes_nothing(db_session_factory) -> None:
    """一张表失败时抛错、旧卡片都在。"""
    source, fake = _source()
    service = make_service(db_session_factory, source, SettingsStub())
    try:
        await service.sync_source("gamedata")
        before = set(await documents(db_session_factory, "gamedata"))
        fake.fail.add("item_table.json")
        summary = await service.sync_source("gamedata")
        assert summary.rebuilt == 0
        assert set(await documents(db_session_factory, "gamedata")) == before
        assert len(before) == 9
        row = await source_row(db_session_factory, "gamedata")
        assert row is not None and row.status == "error"
        assert "超时" in (row.error or "")
    finally:
        await service.close()


def test_config_validation() -> None:
    """``tables`` 必须是非空子集；缺省是 ``character/item/enemy``。"""
    source, _ = _source()
    assert source.validate_config({}) == {"tables": ["character", "item", "enemy"]}
    assert source.validate_config({"tables": ["item"]}) == {"tables": ["item"]}
    assert source.validate_config({"tables": ["enemy", "enemy"]}) == {
        "tables": ["enemy"]
    }
    for config in (
        {"tables": []},
        {"tables": "item"},
        {"tables": ["item", "skill"]},
        {"tables": [123]},
    ):
        with pytest.raises(AppError) as info:
            source.validate_config(config)
        assert info.value.code is ErrorCode.VALIDATION_ERROR
        assert info.value.details["field"] == "tables"
