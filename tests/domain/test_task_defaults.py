"""M17 · 任务参数 schema ``default`` 的逐字段契约。

唯一真源是 ``docs/superpowers/specs/2026-09-28-m17-task-param-defaults.md`` §4 的
「逐字段默认值表」。本文件把那张表逐行钉住，防止以后被无意改掉：

- **全默认可实例化**：每种任务类型只用 ``name``（Copilot 还要它唯一必填的 ``asset_id``）
  构造后 ``normalize`` 必须通过。这一步专门挡住「默认值违反 ``model_validator``」这类
  错误——``Roguelike.difficulty`` / ``expected_collapsal_paradigms`` 一旦被错补默认值，
  或 ``Recruit.expedite_times`` 被补默认值，都会在这里抛 ``AppError``。
- **写了默认值的字段**：``Model.model_fields[field].default`` 等于规格 §4 的值。
- **规格标 ``skip`` 的字段**与 **``overlay`` 字段**（``client_type`` / ``server``）：
  ``default is None``（overlay 由服务端按 ``model_fields_set`` 注入，见规格 §3.4）。
- **默认值必须真的出现在 JSON Schema 里**：pydantic v2 的 ``default_factory`` 不输出
  ``default``，所以可变默认值只能写字面量；这条断言就是防止有人「优化」成工厂。
- ``Infrast.facility`` 的中文标签挂在 **items** 上（前端 ``arrayItemOptions()`` 读的是
  items 的 ``x-enum-labels``，字段级标签对数组不生效）。
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter

from maa_api.domain.task import (
    TASK_MODELS,
    ChannelDefaults,
    FightInput,
    TaskInput,
    TaskInputBase,
    normalize,
)

#: 11 种内核任务 + Copilot 的联合顺序（与 ``TaskInput`` 定义顺序一致）。
TYPE_NAMES: tuple[str, ...] = (
    "StartUp",
    "CloseDown",
    "Fight",
    "Recruit",
    "Infrast",
    "Mall",
    "Award",
    "Roguelike",
    "Reclamation",
    "OperBox",
    "Depot",
    "Copilot",
)

#: 规格 §4 里「schema ``default``」列写明具体值的字段（``name`` 除外）。
#: 值必须与规格表格逐字一致。
DEFAULTS: dict[str, dict[str, Any]] = {
    "StartUp": {
        "enable": True,
        "start_game_enabled": False,
    },
    "CloseDown": {
        "enable": True,
    },
    "Fight": {
        "enable": True,
        "series": 1,
        "medicine": 0,
        "expiring_medicine": 0,
        "stone": 0,
        "dr_grandet": False,
        "report_to_penguin": False,
        "drops": {},
    },
    "Recruit": {
        "enable": True,
        "refresh": False,
        "select": [1, 4, 5],
        "confirm": [2, 3, 4, 5],
        "first_tags": [],
        "extra_tags_mode": 0,
        "times": 0,
        "set_time": True,
        "expedite": False,
        "skip_robot": False,
        "recruitment_time": {"3": 540, "4": 540, "5": 540, "6": 540},
        "report_to_penguin": False,
        "report_to_yituliu": False,
    },
    "Infrast": {
        "enable": True,
        "mode": 0,
        "facility": ["Mfg", "Trade", "Power", "Control", "Reception", "Office", "Dorm"],
        "drones": "_NotUse",
        "threshold": 0.3,
        "replenish": False,
        "dorm_notstationed_enabled": False,
        "dorm_trust_enabled": False,
        "plan_index": 0,
    },
    "Mall": {
        "enable": True,
        "shopping": True,
        "buy_first": [],
        "blacklist": [],
        "force_shopping_if_credit_full": False,
        "only_buy_discount": False,
        "reserve_max_credit": False,
    },
    "Award": {
        "enable": True,
        "award": True,
        "mail": False,
        "recruit": False,
        "orundum": False,
        "mining": False,
        "specialaccess": False,
    },
    "Roguelike": {
        "enable": True,
        "theme": "Phantom",
        "mode": 0,
        "use_support": False,
        "use_nonfriend_support": False,
        "start_with_elite_two": False,
        "only_start_with_elite_two": False,
        "stop_at_final_boss": False,
        "stop_at_max_level": False,
        "investment_enabled": True,
        "stop_when_investment_full": False,
        "investment_with_more_score": False,
        "refresh_trader_with_dice": False,
        "collectible_mode_shopping": False,
    },
    "Reclamation": {
        "enable": True,
        "theme": "Tales",
        "mode": 0,
        "tools_to_craft": [],
        "increment_mode": 0,
        "num_craft_batches": 16,
    },
    # M27 的账号识别任务没有参数，只有基类的 enable。
    "OperBox": {
        "enable": True,
    },
    "Depot": {
        "enable": True,
    },
    "Custom": {
        "enable": True,
    },
    "Copilot": {
        "formation": False,
        "support_unit_usage": 0,
    },
}

#: 规格 §3.2「不写默认值的四类」+ §4 表里标 ``skip`` 的字段：``default`` 必须仍是 ``None``。
#: 注意 ``Roguelike.difficulty`` / ``expected_collapsal_paradigms`` 也在这里——给它们补
#: 静态默认值会在 Phantom 主题下直接触发本服务的 422。
SKIPPED: dict[str, tuple[str, ...]] = {
    "StartUp": ("account_name",),
    "CloseDown": (),
    "Fight": ("stage", "times", "penguin_id"),
    "Recruit": ("expedite_times", "penguin_id", "yituliu_id"),
    "Infrast": ("filename",),
    "Mall": (),
    "Award": (),
    "Roguelike": (
        "squad",
        "roles",
        "core_char",
        # D5：core_char_list 与 core_char 同传时以前者为准，默认保持 None（不指定顺位）。
        "core_char_list",
        "starts_count",
        "difficulty",
        "first_floor_foldartal",
        "start_foldartal_list",
        "use_foldartal",
        "investments_count",
        "check_collapsal_paradigms",
        "double_check_collapsal_paradigms",
        "expected_collapsal_paradigms",
        # D5：条件默认值（随主题／模式变），一律保持 None。
        "collectible_mode_squad",
        "collectible_mode_start_list",
        "start_with_seed",
        "monthly_squad_auto_iterate",
        "monthly_squad_check_comms",
        "deep_exploration_auto_iterate",
        "blackflow_strategy",
        "blackflow_cultivation_target",
        "find_playTime_target",
    ),
    "Reclamation": (),
    "OperBox": (),
    "Depot": (),
    "Copilot": (),
}

#: 规格 §3.4 的 overlay 字段：模型里保持 ``default=None``，由服务端注入全局渠道默认值。
OVERLAY: dict[str, tuple[str, ...]] = {
    "StartUp": ("client_type",),
    "CloseDown": ("client_type",),
    "Fight": ("server", "client_type"),
    "Recruit": ("server",),
    "Infrast": (),
    "Mall": (),
    "Award": (),
    "Roguelike": (),
    "Reclamation": (),
    "OperBox": (),
    "Depot": (),
    "Copilot": (),
}

#: 唯一真正必填的字段：Copilot 的 ``asset_id``（§4：不设默认值，由服务层映射为 filename）。
REQUIRED: dict[str, tuple[str, ...]] = {
    type_name: (("asset_id",) if type_name == "Copilot" else ()) for type_name in TYPE_NAMES
}

#: 规格 §5.1 / 计划 Task 1 的中文标签，加 M28（D4）补的加工站与训练室：
#: 九个设施，顺序即换班顺序（`facility` 的默认值仍是前七个，见 DEFAULTS）。
FACILITY_LABELS: dict[str, str] = {
    "Mfg": "制造站",
    "Trade": "贸易站",
    "Power": "发电站",
    "Control": "控制中枢",
    "Reception": "会客室",
    "Office": "办公室",
    "Dorm": "宿舍",
    "Processing": "加工站",
    "Training": "训练室",
}

_CJK = re.compile(r"[\u4e00-\u9fff]")


def _payload(type_name: str) -> dict[str, Any]:
    """「只给 ``name``」的构造载荷；Copilot 必须额外给它唯一必填的 ``asset_id``。"""
    payload: dict[str, Any] = {"name": type_name}
    if type_name == "Copilot":
        payload["asset_id"] = "asset-1"
    return payload


# --------------------------------------------------------------------------- #
# 1. 全默认可实例化（挡住「默认值违反 model_validator」）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("type_name", TYPE_NAMES)
def test_only_name_normalizes_without_error(type_name: str) -> None:
    task = TypeAdapter(TaskInput).validate_python(_payload(type_name))

    normalized = normalize(task, ChannelDefaults())

    assert normalized.type_name == type_name
    assert normalized.task_name


def test_copilot_asset_id_is_the_only_required_field() -> None:
    """Copilot 不属于 ``TaskInputBase``，没有任何字段带默认值可豁免 ``asset_id``。"""
    for type_name, required in REQUIRED.items():
        model = TASK_MODELS[type_name]
        declared = {name for name, field in model.model_fields.items() if field.is_required()}
        assert declared == set(required), f"{type_name} 的必填字段集合与规格 §4 不符"


@pytest.mark.parametrize("type_name", TYPE_NAMES)
def test_defaults_and_skips_cover_every_declared_field(type_name: str) -> None:
    """§4 表必须把每个字段归类，否则新字段会静默逃过默认值约束。"""
    model = TASK_MODELS[type_name]
    classified = (
        set(DEFAULTS[type_name])
        | set(SKIPPED[type_name])
        | set(OVERLAY[type_name])
        | set(REQUIRED[type_name])
    )
    assert classified == set(model.model_fields) - {"name"}


# --------------------------------------------------------------------------- #
# 2. 逐字段断言规格 §4 的表
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("type_name", TYPE_NAMES)
def test_field_defaults_match_spec_table(type_name: str) -> None:
    model = TASK_MODELS[type_name]
    for field_name, expected in DEFAULTS[type_name].items():
        actual = model.model_fields[field_name].default
        assert actual == expected, (
            f"{type_name}.{field_name} 的 default 应为 {expected!r}，实际 {actual!r}"
        )


@pytest.mark.parametrize("type_name", TYPE_NAMES)
def test_skipped_fields_keep_none_default(type_name: str) -> None:
    model = TASK_MODELS[type_name]
    for field_name in SKIPPED[type_name]:
        assert model.model_fields[field_name].default is None, (
            f"{type_name}.{field_name} 按规格 §3.2/§4 必须保持 default=None"
        )


@pytest.mark.parametrize("type_name", TYPE_NAMES)
def test_overlay_fields_keep_none_default(type_name: str) -> None:
    model = TASK_MODELS[type_name]
    for field_name in OVERLAY[type_name]:
        assert model.model_fields[field_name].default is None, (
            f"{type_name}.{field_name} 是渠道 overlay 字段，模型里必须保持 default=None"
        )


def test_enable_default_is_declared_once_on_the_base_model() -> None:
    assert TaskInputBase.model_fields["enable"].default is True
    assert "enable" in TaskInputBase.__dict__.get("__annotations__", {})
    for type_name in TYPE_NAMES:
        model = TASK_MODELS[type_name]
        if type_name == "Copilot":
            assert "enable" not in model.model_fields
            continue
        assert model.model_fields["enable"].default is True
        assert "enable" not in model.__dict__.get("__annotations__", {}), (
            f"{type_name} 自己重复声明了 enable；只应在 TaskInputBase 上改一次"
        )


# --------------------------------------------------------------------------- #
# 3. 默认值必须真的进 JSON Schema（default_factory 不会输出 default）
# --------------------------------------------------------------------------- #


def _property(model: type[BaseModel], field_name: str) -> dict[str, Any]:
    """取字段在 ``model_json_schema()`` 里的属性对象（``by_alias=True``，键是序列化别名）。"""
    field = model.model_fields[field_name]
    key = field.serialization_alias or field_name
    properties = model.model_json_schema()["properties"]
    assert key in properties, f"{model.__name__} 的 schema 里没有 {key!r}（字段 {field_name}）"
    return properties[key]


@pytest.mark.parametrize("type_name", TYPE_NAMES)
def test_json_schema_carries_every_default(type_name: str) -> None:
    model = TASK_MODELS[type_name]
    for field_name, expected in DEFAULTS[type_name].items():
        prop = _property(model, field_name)
        assert "default" in prop, (
            f"{type_name}.{field_name} 的 JSON Schema 里没有 default——"
            "可变默认值不能写成 default_factory"
        )
        assert prop["default"] == expected


@pytest.mark.parametrize("type_name", TYPE_NAMES)
def test_json_schema_has_no_real_default_for_skipped_and_overlay(type_name: str) -> None:
    """``default=None`` 字段在 schema 里是 ``"default": null``（pydantic 的既有行为）。

    这里断言的是「没有被塞进真实默认值」——前端把 ``null`` 当作「无默认值」处理。
    """
    model = TASK_MODELS[type_name]
    for field_name in SKIPPED[type_name] + OVERLAY[type_name]:
        assert _property(model, field_name).get("default") is None, (
            f"{type_name}.{field_name} 按规格不该有真实默认值"
        )


def test_mutable_defaults_are_not_shared_between_instances() -> None:
    """字面量可变默认值由 pydantic 按实例深拷贝，实例之间不会串。"""
    from maa_api.domain.task import RecruitInput

    first = RecruitInput(name="Recruit")
    second = RecruitInput(name="Recruit")
    first.select.append(6)
    first.recruitment_time["3"] = 60
    assert second.select == [1, 4, 5]
    assert second.recruitment_time == {"3": 540, "4": 540, "5": 540, "6": 540}


# --------------------------------------------------------------------------- #
# 4. Infrast.facility 的中文标签挂在 items 上
# --------------------------------------------------------------------------- #


def _facility_items() -> dict[str, Any]:
    prop = TASK_MODELS["Infrast"].model_json_schema()["properties"]["facility"]
    branches = [branch for branch in prop.get("anyOf", [prop]) if branch.get("type") == "array"]
    assert len(branches) == 1, f"facility 的 schema 结构变了：{prop!r}"
    return branches[0]["items"]


def test_facility_items_carry_chinese_enum_labels() -> None:
    items = _facility_items()

    labels = items.get("x-enum-labels")
    assert labels, "facility 的 items 缺 x-enum-labels（前端 arrayItemOptions() 读的是这里）"
    assert labels == FACILITY_LABELS
    assert set(labels) == set(items["enum"]) == set(FACILITY_LABELS)
    for value in labels.values():
        assert _CJK.search(value), f"设施标签 {value!r} 不是中文"


# --------------------------------------------------------------------------- #
# 5. 别名不被默认值改动碰掉
# --------------------------------------------------------------------------- #


def test_fight_dr_grandet_alias_survives_the_default() -> None:
    field = FightInput.model_fields["dr_grandet"]
    assert field.serialization_alias == "DrGrandet"
    assert field.default is False
    assert FightInput(name="Fight", DrGrandet=True).to_core_params()["DrGrandet"] is True
    assert FightInput(name="Fight").to_core_params()["DrGrandet"] is False


def test_models_are_pydantic_models() -> None:
    """防止 ``TASK_MODELS`` 被换成 schema dict 之类的结构（上面对它取了 model_fields）。"""
    for model in TASK_MODELS.values():
        assert issubclass(model, BaseModel)
