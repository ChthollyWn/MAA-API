"""干员编辑约束与 wire 归一（M27 规格 §7.3、§7.5）：不依赖数据库的纯逻辑契约。

这些用例跑在 ``maa_api.services.account.rules`` 上，因此不 import 仓储与 ORM：
字段级约束（精英化上限、专精前置、模组类型与等级、技能数）在这里逐条钉死，
``tests/services/test_account_box.py`` 再从服务层的角度验证同一组约束的接线。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.account.rules import (
    EDITABLE_FIELDS,
    MAX_LEVEL,
    MAX_MODULE_LEVEL,
    MODULE_TYPES,
    RECOGNIZED_FIELDS,
    SKILL_COUNT_BY_RARITY,
    box_row_wire,
    diff_wire,
    elite_cap,
    iso_z,
    normalize_masteries,
    normalize_modules,
    require_bool,
    require_int,
    skill_count,
)


# ---------------------------------------------------------------------------
# 稀有度派生的上限与默认值
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rarity", "cap"),
    [(1, 0), (2, 0), (3, 1), (4, 2), (5, 2), (6, 2), (None, 2), (99, 2)],
)
def test_elite_cap_follows_rarity(rarity, cap):
    """1–2★ 精零、3★ 精一、4–6★ 精二；未知稀有度按宽档。"""
    assert elite_cap(rarity) == cap


@pytest.mark.parametrize(
    ("rarity", "slots"),
    [(1, 1), (2, 1), (3, 1), (4, 2), (5, 2), (6, 3), (None, 1), (99, 1)],
)
def test_skill_count_follows_rarity(rarity, slots):
    """3★ 1 个、4–5★ 2 个、6★ 3 个（规格 §7.5）。"""
    assert skill_count(rarity) == slots
    assert SKILL_COUNT_BY_RARITY[6] == 3


def test_editable_and_recognized_field_sets_are_frozen():
    assert EDITABLE_FIELDS == (
        "own",
        "elite",
        "level",
        "potential",
        "skill_level",
        "masteries",
        "modules",
    )
    assert RECOGNIZED_FIELDS == ("own", "elite", "level", "potential")
    assert MODULE_TYPES == ("X", "Y", "D", "A")


# ---------------------------------------------------------------------------
# 逐字段校验
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "minimum", "maximum"),
    [
        ("elite", 3, 0, 2),
        ("level", 91, 0, MAX_LEVEL),
        ("potential", 7, 0, 6),
        ("skill_level", 8, 1, 7),
    ],
)
def test_require_int_rejects_out_of_range(field, value, minimum, maximum):
    with pytest.raises(AppError) as caught:
        require_int(field, value, minimum=minimum, maximum=maximum)
    err = caught.value
    assert err.code == ErrorCode.VALIDATION_ERROR
    assert err.http_status == 422
    assert err.details == {
        "field": field,
        "reason": "超出取值范围",
        "allowed": {"minimum": minimum, "maximum": maximum},
    }


def test_require_int_rejects_bools_and_non_integers():
    """``True`` 是 ``int`` 子类，不能让它变成「等级=1」。"""
    for value in (True, False, 1.5, "3", None):
        with pytest.raises(AppError) as caught:
            require_int("level", value, minimum=0, maximum=MAX_LEVEL)
        assert caught.value.details["reason"] == "必须是整数"


def test_require_bool_rejects_truthy_non_bools():
    with pytest.raises(AppError) as caught:
        require_bool("own", 1)
    assert caught.value.details == {"field": "own", "reason": "必须是布尔值"}
    assert require_bool("own", True) is True
    assert require_bool("own", False) is False


# ---------------------------------------------------------------------------
# 专精
# ---------------------------------------------------------------------------


def test_normalize_masteries_fills_all_three_slots():
    assert normalize_masteries({"1": 3}, slots=3) == {"1": 3, "2": 0, "3": 0}


def test_normalize_masteries_zeroes_slots_beyond_the_skill_count():
    """4★ 只有两个技能：第 3 槽写 3 也归零，不报错。"""
    assert normalize_masteries({"1": 3, "2": 2, "3": 3}, slots=2) == {
        "1": 3,
        "2": 2,
        "3": 0,
    }
    assert normalize_masteries({"3": 3}, slots=1) == {"1": 0, "2": 0, "3": 0}


@pytest.mark.parametrize("value", [{"1": 4}, {"1": -1}, {"1": 1.5}, {"0": 1}, {"4": 1}])
def test_normalize_masteries_rejects_bad_values_and_keys(value):
    with pytest.raises(AppError) as caught:
        normalize_masteries(value, slots=3)
    assert caught.value.code == ErrorCode.VALIDATION_ERROR


def test_normalize_masteries_rejects_non_mappings():
    for value in ([3, 0, 0], "1:3", None):
        with pytest.raises(AppError) as caught:
            normalize_masteries(value, slots=3)
        assert caught.value.details["field"] == "masteries"


def test_normalize_masteries_accepts_int_keys():
    """wire 是字符串键，但 JSON 里出现整数键（``{1: 3}``）也要能读。"""
    assert normalize_masteries({1: 3}, slots=3) == {"1": 3, "2": 0, "3": 0}


# ---------------------------------------------------------------------------
# 模组
# ---------------------------------------------------------------------------


def test_normalize_modules_accepts_fixed_types_and_levels():
    assert normalize_modules({"X": 1, "Y": 2, "D": 3, "A": 1}) == {
        "X": 1,
        "Y": 2,
        "D": 3,
        "A": 1,
    }
    assert normalize_modules({}) == {}


@pytest.mark.parametrize(
    "value",
    [{"Z": 1}, {"x": 1}, {"": 1}, {"X": 0}, {"X": 4}, {"X": 1.5}, {"X": True}],
)
def test_normalize_modules_rejects_bad_type_or_level(value):
    with pytest.raises(AppError) as caught:
        normalize_modules(value)
    assert caught.value.code == ErrorCode.VALIDATION_ERROR
    assert caught.value.details["field"].startswith("modules")


def test_normalize_modules_rejects_non_mappings():
    with pytest.raises(AppError) as caught:
        normalize_modules([{"type": "X", "level": 1}])
    assert caught.value.details == {
        "field": "modules",
        "reason": "必须是模组类型到等级的映射",
    }


def test_module_level_bounds_are_pinned():
    assert MAX_MODULE_LEVEL == 3


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


def test_box_row_wire_passes_unknown_training_through_as_none():
    """M35：没识别过、也没人写过的行，六个练度字段就是 ``None``。

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


def test_diff_wire_carries_the_operator_name_and_times():
    row = SimpleNamespace(
        id=7,
        char_id="char_x",
        name="测试",
        field="level",
        manual_value=55,
        recognized_value=90,
        created_at=datetime(2026, 10, 10, 3, 0, 0),
        dismissed_at=None,
    )
    assert diff_wire(row) == {
        "id": 7,
        "char_id": "char_x",
        "name": "测试",
        "field": "level",
        "manual_value": 55,
        "recognized_value": 90,
        "created_at": "2026-10-10T03:00:00Z",
        "dismissed_at": None,
    }


def test_iso_z_normalizes_timezone_aware_values():
    aware = datetime(2026, 10, 10, 11, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    assert iso_z(aware) == "2026-10-10T03:00:00Z"
    assert iso_z(datetime(2026, 10, 10, 3, 0, 0, tzinfo=UTC)) == "2026-10-10T03:00:00Z"
    assert iso_z(None) is None
