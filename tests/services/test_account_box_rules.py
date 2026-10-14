"""干员 wire 归一（M27 规格 §7.3、§7.5）：不依赖数据库的纯逻辑契约。

这些用例跑在 ``maa_api.services.account.rules`` 上，因此不 import 仓储与 ORM。
**M47 起这一层只剩「读」**：编辑约束（精英化上限、专精前置、模组类型与等级、技能数）
随手动编辑 / Agent 补录通道一起下线（docs/13 §8.70），``MAX_*`` / ``MODULE_TYPES`` 仍
保留，因为森空岛映射层用它们判越界值。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from maa_api.services.account.rules import (
    BOX_FIELDS,
    KNOWN_SOURCES,
    MAX_LEVEL,
    MAX_MASTERY_LEVEL,
    MAX_MODULE_LEVEL,
    MAX_POTENTIAL,
    MAX_SKILL_LEVEL,
    MIN_MODULE_LEVEL,
    MODULE_TYPES,
    RECOGNIZED_FIELDS,
    SKLAND_FIELDS,
    SKLAND_SOURCE,
    UNKNOWN_TRAINING_FIELDS,
    box_row_wire,
    iso_z,
)


# ---------------------------------------------------------------------------
# 冻结的字段集合与常量
# ---------------------------------------------------------------------------


def test_field_sets_are_frozen():
    assert RECOGNIZED_FIELDS == ("own", "elite", "level", "potential")
    assert SKLAND_FIELDS == (
        "own",
        "elite",
        "level",
        "potential",
        "skill_level",
        "masteries",
        "modules",
        "favor_percent",
        "skill_ids",
        "module_details",
    )
    assert UNKNOWN_TRAINING_FIELDS == (
        "elite",
        "level",
        "potential",
        "skill_level",
        "masteries",
        "modules",
    )
    assert MODULE_TYPES == ("X", "Y", "D", "A")
    # 可编辑字段清单（EDITABLE_FIELDS）随编辑通道删除；对外形状仍在 BOX_FIELDS 里。
    assert "own" in BOX_FIELDS and "module_details" in BOX_FIELDS
    assert (MAX_MASTERY_LEVEL, MAX_MODULE_LEVEL, MIN_MODULE_LEVEL) == (3, 3, 1)
    assert (MAX_SKILL_LEVEL, MAX_LEVEL, MAX_POTENTIAL) == (7, 90, 6)


def test_known_sources_keep_the_legacy_values_for_old_rows():
    """M47 起只会新写 ``skland``；三个历史取值仍留在白名单里让老行读得出来。"""
    assert KNOWN_SOURCES == ("recognition", "manual", "agent", "skland")
    assert SKLAND_SOURCE == "skland"


def test_editing_constraint_helpers_are_gone():
    """M47：编辑通道下线，约束函数与差异 wire 一起删除（docs/13 §8.70）。"""
    from maa_api.services.account import rules

    for name in (
        "EDITABLE_FIELDS",
        "RECOGNITION_SOURCE",
        "WRITE_SOURCES",
        "MASTERY_ELITE_REQUIRED",
        "SKILL_COUNT_BY_RARITY",
        "elite_cap",
        "skill_count",
        "require_int",
        "require_bool",
        "normalize_masteries",
        "normalize_modules",
        "diff_wire",
    ):
        assert not hasattr(rules, name), name


# ---------------------------------------------------------------------------
# wire 渲染
# ---------------------------------------------------------------------------


def _row(**fields):
    defaults = dict(
        char_id="char_x",
        name="测试",
        rarity=6,
        profession="近卫",
        own=True,
        elite=2,
        level=90,
        potential=6,
        skill_level=7,
        masteries={1: 3, "2": 0, 3: "3", "4": 1},
        modules={"X": 3, "Z": 2},
        field_sources={"elite": "recognition", "level": "manual", "nope": "manual", "own": "x"},
        recognized_at=datetime(2026, 10, 10, 3, 0, 0),
        updated_at=None,
    )
    defaults.update(fields)
    return SimpleNamespace(**defaults)


def test_box_row_wire_normalizes_json_columns_and_times():
    wire = box_row_wire(_row())
    # 专精：三个槽位总在（没值的补 0），只接受 1–3 的**整数**项
    # （``"3"`` 这种字符串值丢弃），键统一成字符串。
    assert wire["masteries"] == {"1": 3, "2": 0, "3": 0}
    # 模组：未知类型丢弃。
    assert wire["modules"] == {"X": 3}
    # 字段来源：未知字段与未知来源取值丢弃。
    assert wire["field_sources"] == {"elite": "recognition", "level": "manual"}
    assert wire["recognized_at"] == "2026-10-10T03:00:00Z"
    assert wire["updated_at"] is None


def test_box_row_wire_normalizes_skill_ids_and_module_details():
    """M43 的两列卡片资源：非列表 ⇒ ``None``（不知道），认不出的条目丢掉。"""
    wire = box_row_wire(
        _row(
            skill_ids=["skchr_amiya_2", 42, None],
            module_details=[
                {"type": "X", "level": 3, "equip_id": "uniequip_002_amiya", "branch": "CCR-X"},
                {"type": "Y"},  # 缺 level ⇒ 丢
                {"level": 2},  # 缺 type ⇒ 丢
                "junk",
            ],
        )
    )
    assert wire["skill_ids"] == ["skchr_amiya_2"]
    assert wire["module_details"] == [
        {
            "type": "X",
            "level": 3,
            "equip_id": "uniequip_002_amiya",
            "branch": "CCR-X",
            "label": None,
            "type_icon": None,
        }
    ]

    unknown = box_row_wire(_row(skill_ids=None, module_details="nope"))
    assert unknown["skill_ids"] is None
    assert unknown["module_details"] is None


def test_box_row_wire_passes_unknown_training_through_as_none():
    """M35：没同步过（也没人写过的历史行），六个练度字段就是 ``None``。

    M35 之前这里返回 ``elite=0`` / 技能 1 级 / 三个 0 专精槽 / 空模组，等于把
    「未识别」渲染成「精零 1 级、无专精、无模组」——Agent 就是这么回答用户的。
    """
    wire = box_row_wire(SimpleNamespace(char_id="char_y"))
    assert wire == {
        "char_id": "char_y",
        "name": None,
        "rarity": None,
        "profession": None,
        "own": False,
        "elite": None,
        "level": None,
        "potential": None,
        "skill_level": None,
        "masteries": None,
        "modules": None,
        # M42 §4.5：信赖与六列同口径，没有同步过就是 None。
        "favor_percent": None,
        # M43 §4.3：卡片用的两列同样是「不知道就是 None」。
        "skill_ids": None,
        "module_details": None,
        "field_sources": {},
        "recognized_at": None,
        "updated_at": None,
    }


def test_iso_z_normalizes_timezone_aware_values():
    aware = datetime(2026, 10, 10, 11, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    assert iso_z(aware) == "2026-10-10T03:00:00Z"
    assert iso_z(datetime(2026, 10, 10, 3, 0, 0, tzinfo=UTC)) == "2026-10-10T03:00:00Z"
    assert iso_z(None) is None


@pytest.mark.parametrize("source", ["recognition", "manual", "agent", "skland"])
def test_field_sources_wire_accepts_every_known_source(source):
    """老行里的三个历史来源仍要读得出来，否则整行来源会被丢掉。"""
    wire = box_row_wire(_row(field_sources={"level": source}))
    assert wire["field_sources"] == {"level": source}
