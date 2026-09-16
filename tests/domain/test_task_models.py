"""``maa_api/domain/task.py`` 的契约测试。

覆盖 docs/05 §7（请求体设计）、§8（schema 导出）、§9（渠道默认值注入）、
docs/09 §7.2（x-* 契约）、docs/13 §4（x-risk 与 x-widget 扩充）与 docs/12 M3
（参数说明必须从 ``task.py`` docstring 完整迁移）的硬约束：

- **参数覆盖度 parity**：从 ``maa_api/model/core/task.py`` 的 9 个类 docstring 解析
  参数名，断言新模型字段集合是它的超集，且每个字段的 ``x-label``/``description``
  非空。解析结果为空直接失败，防止正则失效让测试空转。
- **三态提交**：类型/范围/Literal 错与 extra 字段 → ``ValidationError``；未知 ``name``
  → 判别联合错误；跨字段违规 → ``AppError(TASK_PARAM_INVALID/TASK_PARAM_DEPRECATED)``。
- **导出与注入**：``to_core_params`` 的别名与 None 剔除；``normalize`` 的
  缺失注入 / 显式 null 不注入 / 显式值原样三态，以及 ``raw_params`` 的 by_alias 键名。
- **schema 元信息**：x-risk / x-widget / x-enum-labels / x-depends-on 与
  ``RISK_FIELDS`` 的一致性。
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import AliasChoices, TypeAdapter, ValidationError

from maa_api.domain.enums import RiskLevel
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.domain.task import (
    RISK_CONSUME,
    RISK_FIELDS,
    RUNTIME_IMMUTABLE,
    TASK_LABELS,
    TASK_MODELS,
    AwardInput,
    ChannelDefaults,
    CloseDownInput,
    FightInput,
    InfrastInput,
    MaaField,
    MallInput,
    NormalizedTask,
    PipelineCreate,
    RecruitInput,
    ReclamationInput,
    RoguelikeInput,
    StartUpInput,
    TaskInput,
    TaskInputBase,
    normalize,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_TASK_PATH = REPO_ROOT / "maa_api" / "model" / "core" / "task.py"

MODEL_TYPES: tuple[type[TaskInputBase], ...] = (
    StartUpInput,
    CloseDownInput,
    FightInput,
    RecruitInput,
    InfrastInput,
    MallInput,
    AwardInput,
    RoguelikeInput,
    ReclamationInput,
)
TYPE_NAMES: tuple[str, ...] = tuple(TASK_LABELS)
MODEL_IDS = [model.__name__ for model in MODEL_TYPES]

ADAPTER = TypeAdapter(TaskInput)

#: 旧 ``task.py`` 类名 → 新模型 discriminator 取值。
CORE_CLASS_BY_TYPE: dict[str, str] = {
    "StartUp": "StartUpTask",
    "CloseDown": "CloseDownTask",
    "Fight": "FightTask",
    "Recruit": "RecruitTask",
    "Infrast": "InfrastTask",
    "Mall": "MallTask",
    "Award": "AwardTask",
    "Roguelike": "RoguelikeTask",
    "Reclamation": "ReclamationTask",
}

#: 每类 docstring 至少应解析出的参数条数（卡面给出的下限；实际 docstring 只会更多）。
#: 它是「正则失效导致 parity 测试空转」的哨兵，不是精确断言。
MIN_DOCSTRING_PARAMS: dict[str, int] = {
    "StartUp": 4,
    "CloseDown": 2,
    "Fight": 13,
    "Recruit": 14,
    "Infrast": 9,
    "Mall": 5,
    "Award": 7,
    "Roguelike": 21,
    "Reclamation": 5,
}


# --------------------------------------------------------------------------- #
# docstring 解析（AST，不 import 旧模块，避免 import 期副作用）
# --------------------------------------------------------------------------- #


def _init_docstring(tree: ast.Module, class_name: str) -> str:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                    doc = ast.get_docstring(item)
                    assert doc, f"{class_name}.__init__ 没有 docstring"
                    return doc
            raise AssertionError(f"{class_name} 没有 __init__")
    raise AssertionError(f"task.py 中没有类 {class_name}")


@lru_cache(maxsize=1)
def _docstring_params() -> dict[str, list[str]]:
    """从旧 ``task.py`` 的「参数:」段解析出每类的参数名。"""
    tree = ast.parse(CORE_TASK_PATH.read_text(encoding="utf-8"))
    parsed: dict[str, list[str]] = {}
    for type_name, class_name in CORE_CLASS_BY_TYPE.items():
        doc = _init_docstring(tree, class_name)
        assert "参数:" in doc, f"{class_name} docstring 缺「参数:」段"
        section = doc.split("参数:", 1)[1]
        names = [
            match.group(1)
            for line in section.splitlines()
            if (match := re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*) \([^)]*\):", line))
        ]
        parsed[type_name] = names
    return parsed


@lru_cache(maxsize=1)
def _core_task_names() -> dict[str, str]:
    """从旧 ``task.py`` 的 ``super().__init__(task_name=..., type_name=...)`` 解析中文任务名。"""
    tree = ast.parse(CORE_TASK_PATH.read_text(encoding="utf-8"))
    names: dict[str, str] = {}
    for type_name, class_name in CORE_CLASS_BY_TYPE.items():
        for node in tree.body:
            if not (isinstance(node, ast.ClassDef) and node.name == class_name):
                continue
            for item in ast.walk(node):
                if isinstance(item, ast.Call) and getattr(item.func, "attr", "") == "__init__":
                    kwargs = {kw.arg: kw.value for kw in item.keywords}
                    if "task_name" in kwargs and "type_name" in kwargs:
                        names[ast.literal_eval(kwargs["type_name"])] = ast.literal_eval(
                            kwargs["task_name"]
                        )
    return names


def _submittable_names(model: type[TaskInputBase]) -> set[str]:
    """模型接受的字段名集合：Python 字段名 + ``validation_alias`` 别名。"""
    names: set[str] = set()
    for field_name, field in model.model_fields.items():
        names.add(field_name)
        alias = field.validation_alias
        if isinstance(alias, AliasChoices):
            names.update(str(choice) for choice in alias.choices)
        elif isinstance(alias, str):
            names.add(alias)
    return names


def _schema(model: type[TaskInputBase]) -> dict[str, Any]:
    return model.model_json_schema(ref_template="#/$defs/{model}")


def _non_null_branch(prop: dict[str, Any]) -> dict[str, Any]:
    branches = [branch for branch in prop.get("anyOf", []) if branch.get("type") != "null"]
    assert len(branches) == 1, f"期望恰好一个非 null 分支，实际 {prop!r}"
    return branches[0]


def _case_id(value: Any) -> str:
    """参数化用例的可读 id：``任务类型:字段…``（非 payload 取值直接 str）。"""
    if isinstance(value, dict):
        fields = "-".join(sorted(set(value) - {"name"}))
        return f"{value['name']}:{fields}"
    return str(value)


# --------------------------------------------------------------------------- #
# 1. 判别联合、标签与公共契约
# --------------------------------------------------------------------------- #


def test_task_input_union_has_exactly_nine_ordered_types() -> None:
    models = get_args(get_args(TaskInput)[0])
    assert [model.model_fields["name"].default for model in models] == list(TYPE_NAMES)
    assert set(TASK_MODELS) == set(TYPE_NAMES)


def test_task_labels_are_exactly_the_core_task_names() -> None:
    expected = _core_task_names()
    assert expected, "旧 task.py 的 task_name 解析为空，正则可能失效"
    assert TASK_LABELS == expected
    assert TASK_LABELS["Fight"] == "刷理智"
    assert TASK_LABELS["Reclamation"] == "生息演算"


def test_field_metadata_maps_cover_exactly_the_nine_types() -> None:
    assert set(RUNTIME_IMMUTABLE) == set(TYPE_NAMES)
    assert set(RISK_FIELDS) == set(TYPE_NAMES)


def test_risk_fields_match_docs_13_exactly() -> None:
    assert set(RISK_FIELDS["Fight"]) == {"stone", "medicine", "expiring_medicine"}
    assert set(RISK_FIELDS["Recruit"]) == {"expedite"}
    assert set(RISK_FIELDS["Mall"]) == {"shopping"}
    assert set(RISK_FIELDS["Roguelike"]) == {"investment_enabled"}
    for type_name in ("StartUp", "CloseDown", "Infrast", "Award", "Reclamation"):
        assert RISK_FIELDS[type_name] == frozenset()
    assert RISK_CONSUME == "consume" == RiskLevel.CONSUME


def test_runtime_immutable_matches_docstring_markers() -> None:
    assert RUNTIME_IMMUTABLE["Fight"] == ["stage"]
    assert set(RUNTIME_IMMUTABLE["Infrast"]) == {"facility", "filename", "plan_index"}
    assert set(RUNTIME_IMMUTABLE["Mall"]) == {"shopping", "buy_first", "blacklist"}
    for type_name, fields in RUNTIME_IMMUTABLE.items():
        model = TASK_MODELS[type_name]
        for field_name in fields:
            description = model.model_fields[field_name].description or ""
            assert "不支持运行中设置" in description, (
                f"{type_name}.{field_name} 被标为运行中不可变，description 必须写明"
            )


def test_maafield_attaches_all_x_keys_without_collision() -> None:
    field = MaaField(
        default=None,
        label="标签",
        group="分组",
        widget="select",
        enum_labels={1: "一", "2": "二"},
        risk=RISK_CONSUME,
        depends_on={"a": True},
        x_extra={"x-future": "保留"},
        description="说明",
    )
    assert field.json_schema_extra == {
        "x-label": "标签",
        "x-group": "分组",
        "x-widget": "select",
        "x-enum-labels": {"1": "一", "2": "二"},
        "x-risk": "consume",
        "x-depends-on": {"a": True},
        "x-future": "保留",
    }


def test_task_input_base_config_contract() -> None:
    config = TaskInputBase.model_config
    assert config["extra"] == "forbid"
    assert config["populate_by_name"] is True
    assert config["validate_assignment"] is True
    # 子类继承收紧项（实测 extra_forbidden 来自 extra="forbid"）。
    assert FightInput.model_config["extra"] == "forbid"


def test_channel_defaults_and_normalized_task_shape() -> None:
    defaults = ChannelDefaults()
    assert defaults.client_type == "Bilibili"
    assert defaults.server == "CN"
    assert ChannelDefaults(client_type="Official", server="JP").server == "JP"
    normalized = normalize(FightInput(name="Fight"), defaults)
    assert isinstance(normalized, NormalizedTask)
    assert (normalized.type_name, normalized.task_name) == ("Fight", "刷理智")
    assert set(NormalizedTask.model_fields) == {"type_name", "task_name", "params", "raw_params"}


# --------------------------------------------------------------------------- #
# 2. 参数覆盖度 parity（docs/12 M3）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("type_name", TYPE_NAMES)
def test_model_is_superset_of_docstring_params(type_name: str) -> None:
    parsed = _docstring_params()[type_name]
    assert parsed, f"{type_name} 的 docstring 未解析出任何参数，正则可能失效"
    assert len(parsed) >= MIN_DOCSTRING_PARAMS[type_name], (
        f"{type_name} 只解析出 {len(parsed)} 个参数，少于卡面下限，正则可能失效"
    )
    missing = set(parsed) - _submittable_names(TASK_MODELS[type_name])
    assert not missing, f"{type_name} 缺少 docstring 里的参数: {sorted(missing)}"


@pytest.mark.parametrize("model", MODEL_TYPES, ids=MODEL_IDS)
def test_every_field_has_label_and_description(model: type[TaskInputBase]) -> None:
    for field_name, field in model.model_fields.items():
        extra = field.json_schema_extra or {}
        assert extra.get("x-label"), f"{model.__name__}.{field_name} 缺 x-label"
        assert (field.description or "").strip(), f"{model.__name__}.{field_name} 缺 description"


def test_docstring_parity_covers_the_expected_total() -> None:
    parsed = _docstring_params()
    expected_total = sum(len(names) for names in parsed.values())
    # 旧 task.py 9 个任务类 docstring 实测共 89 条参数（每个类型各计一次，
    # 分布：StartUp 4 / CloseDown 2 / Fight 13 / Recruit 17 / Infrast 10 /
    # Mall 7 / Award 7 / Roguelike 23 / Reclamation 6）。
    assert expected_total == 89, {k: len(v) for k, v in parsed.items()}


@pytest.mark.parametrize("type_name", TYPE_NAMES)
def test_to_core_params_only_exports_keys_the_kernel_knows(type_name: str) -> None:
    """导出的每个键都必须是旧 docstring 记录过的参数名。

    这条钉住 docs/05 §7.6 的两类拼写缺陷：旧 ``failename`` 与「变量当键」都不可能
    再出现在下发给 ``AsstAppendTask`` 的字典里（``DrGrandet`` 是 docstring 的原名）。
    """
    model = TASK_MODELS[type_name]
    for example in model.model_config["json_schema_extra"]["examples"]:
        params = model.model_validate(example).to_core_params()
        unknown = set(params) - set(_docstring_params()[type_name])
        assert not unknown, f"{type_name} 下发了内核不认识的键: {sorted(unknown)}"


# --------------------------------------------------------------------------- #
# 3. 三态提交：pydantic 原生 422 / extra / 未知类型 / 跨字段 AppError
# --------------------------------------------------------------------------- #


INVALID_FIELD_PAYLOADS: list[dict[str, Any]] = [
    {"name": "Fight", "stage": 123},
    {"name": "Fight", "series": 7},
    {"name": "Fight", "series": -2},
    {"name": "Fight", "medicine": -1},
    {"name": "Fight", "stone": -1},
    {"name": "Fight", "client_type": "Nope"},
    {"name": "Fight", "server": "EU"},
    {"name": "Fight", "drops": {"3001": "很多"}},
    {"name": "Fight", "enable": "maybe"},
    {"name": "Recruit", "select": [0]},
    {"name": "Recruit", "confirm": [7]},
    {"name": "Recruit", "extra_tags_mode": 3},
    {"name": "Recruit", "recruitment_time": {3: 540}},
    {"name": "Recruit", "times": -1},
    {"name": "Infrast", "mode": 5000},
    {"name": "Infrast", "threshold": 1.5},
    {"name": "Infrast", "threshold": -0.1},
    {"name": "Infrast", "drones": "Nope"},
    {"name": "Infrast", "facility": ["Mfg", "Nope"]},
    {"name": "Infrast", "plan_index": -1},
    {"name": "Mall", "reserve_max_credit": "maybe"},
    {"name": "Award", "award": 2},
    {"name": "Roguelike", "theme": "Nope"},
    {"name": "Roguelike", "difficulty": -1},
    {"name": "Roguelike", "mode": 6},
    {"name": "Reclamation", "theme": "Nope"},
    {"name": "Reclamation", "mode": 2},
    {"name": "Reclamation", "increment_mode": 2},
    {"name": "Reclamation", "num_craft_batches": 0},
    {"name": "StartUp", "start_game_enabled": "maybe"},
    {"name": "CloseDown", "client_type": "Nope"},
]


@pytest.mark.parametrize("payload", INVALID_FIELD_PAYLOADS, ids=_case_id)
def test_field_type_range_literal_errors_are_validation_errors(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python(payload)


def test_extra_field_is_forbidden_not_silently_dropped() -> None:
    with pytest.raises(ValidationError) as excinfo:
        ADAPTER.validate_python({"name": "Fight", "theme": "Sami"})
    assert [error["type"] for error in excinfo.value.errors()] == ["extra_forbidden"]


def test_extra_field_error_is_inherited_by_every_model() -> None:
    for type_name, model in TASK_MODELS.items():
        with pytest.raises(ValidationError) as excinfo:
            model.model_validate({"name": type_name, "definitely_not_a_param": 1})
        assert excinfo.value.errors()[0]["type"] == "extra_forbidden"


def test_unknown_task_type_is_union_tag_invalid() -> None:
    with pytest.raises(ValidationError) as excinfo:
        ADAPTER.validate_python({"name": "Nope"})
    assert excinfo.value.errors()[0]["type"] == "union_tag_invalid"


def test_missing_task_type_is_union_tag_not_found() -> None:
    with pytest.raises(ValidationError) as excinfo:
        ADAPTER.validate_python({"stage": "1-7"})
    assert excinfo.value.errors()[0]["type"] == "union_tag_not_found"


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"name": "Roguelike", "mode": 2}, ErrorCode.TASK_PARAM_DEPRECATED),
        ({"name": "Roguelike", "mode": 3}, ErrorCode.TASK_PARAM_INVALID),
        ({"name": "Roguelike", "mode": 5}, ErrorCode.TASK_PARAM_INVALID),
        ({"name": "Roguelike", "mode": 5, "theme": "Mizuki"}, ErrorCode.TASK_PARAM_INVALID),
        ({"name": "Roguelike", "difficulty": 3}, ErrorCode.TASK_PARAM_INVALID),
        ({"name": "Roguelike", "difficulty": 3, "theme": "Phantom"}, ErrorCode.TASK_PARAM_INVALID),
        (
            {"name": "Roguelike", "stop_at_final_boss": True, "theme": "Phantom"},
            ErrorCode.TASK_PARAM_INVALID,
        ),
        (
            {"name": "Roguelike", "refresh_trader_with_dice": True, "theme": "Sami"},
            ErrorCode.TASK_PARAM_INVALID,
        ),
        (
            {"name": "Roguelike", "first_floor_foldartal": "x", "theme": "Mizuki"},
            ErrorCode.TASK_PARAM_INVALID,
        ),
        (
            {"name": "Roguelike", "use_foldartal": True, "theme": "Mizuki"},
            ErrorCode.TASK_PARAM_INVALID,
        ),
        (
            {"name": "Roguelike", "theme": "Sami", "mode": 1, "start_foldartal_list": ["x"]},
            ErrorCode.TASK_PARAM_INVALID,
        ),
        (
            {"name": "Roguelike", "theme": "Sarkaz", "check_collapsal_paradigms": True},
            ErrorCode.TASK_PARAM_INVALID,
        ),
        (
            {"name": "Roguelike", "theme": "Sami", "mode": 1, "double_check_collapsal_paradigms": True},
            ErrorCode.TASK_PARAM_INVALID,
        ),
        (
            {"name": "Roguelike", "theme": "Sami", "mode": 1, "expected_collapsal_paradigms": ["x"]},
            ErrorCode.TASK_PARAM_INVALID,
        ),
        (
            {"name": "Roguelike", "mode": 0, "only_start_with_elite_two": True},
            ErrorCode.TASK_PARAM_INVALID,
        ),
        (
            {"name": "Roguelike", "mode": 4, "only_start_with_elite_two": True},
            ErrorCode.TASK_PARAM_INVALID,
        ),
        (
            {"name": "Roguelike", "use_nonfriend_support": True},
            ErrorCode.TASK_PARAM_INVALID,
        ),
        ({"name": "Recruit", "expedite_times": 3}, ErrorCode.TASK_PARAM_INVALID),
        ({"name": "Recruit", "expedite": False, "expedite_times": 3}, ErrorCode.TASK_PARAM_INVALID),
        ({"name": "Infrast", "mode": 10000}, ErrorCode.TASK_PARAM_INVALID),
        (
            {"name": "Infrast", "mode": 10000, "filename": "plan.json"},
            ErrorCode.TASK_PARAM_INVALID,
        ),
        (
            {"name": "Infrast", "mode": 10000, "plan_index": 0},
            ErrorCode.TASK_PARAM_INVALID,
        ),
    ],
    ids=_case_id,
)
def test_cross_field_violations_raise_app_error(payload: dict[str, Any], code: ErrorCode) -> None:
    with pytest.raises(AppError) as excinfo:
        ADAPTER.validate_python(payload)
    assert excinfo.value.code == code
    assert excinfo.value.http_status == (400 if code is ErrorCode.TASK_PARAM_DEPRECATED else 422)


def test_mode_2_is_deprecated_with_400() -> None:
    with pytest.raises(AppError) as excinfo:
        RoguelikeInput.model_validate({"name": "Roguelike", "mode": 2})
    assert excinfo.value.code is ErrorCode.TASK_PARAM_DEPRECATED
    assert excinfo.value.http_status == 400


VALID_CROSS_FIELD_PAYLOADS: list[dict[str, Any]] = [
    {"name": "Roguelike", "theme": "Sami", "mode": 5, "check_collapsal_paradigms": True},
    {
        "name": "Roguelike",
        "theme": "Sami",
        "mode": 5,
        "expected_collapsal_paradigms": ["目空一些"],
        "check_collapsal_paradigms": True,
    },
    {"name": "Roguelike", "theme": "Sami", "mode": 4, "start_foldartal_list": ["木的辨别"]},
    {"name": "Roguelike", "theme": "Sami", "difficulty": 12, "use_foldartal": True},
    {"name": "Roguelike", "theme": "Mizuki", "mode": 1, "refresh_trader_with_dice": True},
    {"name": "Roguelike", "mode": 4, "start_with_elite_two": True, "only_start_with_elite_two": True},
    {"name": "Roguelike", "use_support": True, "use_nonfriend_support": True},
    {"name": "Roguelike", "theme": "Sami", "mode": 1, "double_check_collapsal_paradigms": False},
    {"name": "Recruit", "expedite": True, "expedite_times": 3},
    {"name": "Infrast", "mode": 10000, "filename": "plan.json", "plan_index": 0},
    {"name": "Infrast", "mode": 0},
]


@pytest.mark.parametrize("payload", VALID_CROSS_FIELD_PAYLOADS, ids=_case_id)
def test_cross_field_rules_accept_the_valid_combinations(payload: dict[str, Any]) -> None:
    assert ADAPTER.validate_python(payload).name == payload["name"]


def test_assignment_revalidates_cross_field_rules() -> None:
    recruit = RecruitInput(name="Recruit")
    with pytest.raises(AppError) as excinfo:
        recruit.expedite_times = 3
    assert excinfo.value.code is ErrorCode.TASK_PARAM_INVALID

    fight = FightInput(name="Fight")
    with pytest.raises(ValidationError):
        fight.series = 99


def test_model_fields_set_distinguishes_the_three_states() -> None:
    assert FightInput(name="Fight").model_fields_set == {"name"}
    assert "client_type" in FightInput(name="Fight", client_type=None).model_fields_set
    assert "client_type" in FightInput(name="Fight", client_type="Official").model_fields_set


# --------------------------------------------------------------------------- #
# 4. to_core_params：别名与 None 剔除（docs/05 §7.2/§7.6）
# --------------------------------------------------------------------------- #


def test_to_core_params_uses_maa_alias_and_drops_none() -> None:
    fight = FightInput(name="Fight", dr_grandet=True, stage="1-7")
    assert fight.to_core_params() == {"DrGrandet": True, "stage": "1-7"}
    assert FightInput(name="Fight").to_core_params() == {}
    assert FightInput(name="Fight", stage=None, series=None).to_core_params() == {}
    assert FightInput(name="Fight", enable=False).to_core_params() == {"enable": False}


def test_to_core_params_accepts_both_alias_spellings() -> None:
    assert FightInput(name="Fight", DrGrandet=True).to_core_params() == {"DrGrandet": True}
    assert FightInput(name="Fight", dr_grandet=True).to_core_params() == {"DrGrandet": True}


def test_award_does_not_preset_true_defaults() -> None:
    assert AwardInput(name="Award").to_core_params() == {}
    assert AwardInput(name="Award", award=True).to_core_params() == {"award": True}
    for field_name in ("mail", "recruit", "orundum", "mining", "specialaccess"):
        assert AwardInput.model_fields[field_name].default is None


def test_infrast_has_no_server_side_defaults_and_uses_filename() -> None:
    assert InfrastInput.model_fields["threshold"].default is None
    assert "0.3" in (InfrastInput.model_fields["threshold"].description or "")
    assert InfrastInput(name="Infrast", threshold=0.3).to_core_params() == {"threshold": 0.3}
    params = InfrastInput(
        name="Infrast", mode=10000, filename="plans/custom.json", plan_index=1, threshold=0.3
    ).to_core_params()
    assert params == {
        "mode": 10000,
        "filename": "plans/custom.json",
        "plan_index": 1,
        "threshold": 0.3,
    }
    assert "failename" not in params
    assert "failename" not in _submittable_names(InfrastInput)


def test_reclamation_params_are_string_keys() -> None:
    params = ReclamationInput(
        name="Reclamation", theme="Fire", mode=1, num_craft_batches=16
    ).to_core_params()
    assert params == {"theme": "Fire", "mode": 1, "num_craft_batches": 16}
    assert all(isinstance(key, str) for key in params)
    assert ReclamationInput(name="Reclamation").to_core_params() == {}


def test_fight_and_recruit_times_are_independent_fields() -> None:
    assert FightInput(name="Fight", times=3).to_core_params() == {"times": 3}
    assert RecruitInput(name="Recruit", times=5).to_core_params() == {"times": 5}
    assert FightInput.model_fields["times"] is not RecruitInput.model_fields["times"]


# --------------------------------------------------------------------------- #
# 5. normalize：三态注入与 raw_params 键名（docs/05 §9）
# --------------------------------------------------------------------------- #


def test_normalize_injects_global_defaults_when_field_is_missing() -> None:
    normalized = normalize(FightInput(name="Fight"), ChannelDefaults())
    assert normalized.params["client_type"] == "Bilibili"
    assert normalized.params["server"] == "CN"
    assert "client_type" not in normalized.raw_params
    assert "server" not in normalized.raw_params


def test_normalize_does_not_inject_on_explicit_null() -> None:
    normalized = normalize(FightInput(name="Fight", client_type=None, server=None), ChannelDefaults())
    assert "client_type" not in normalized.params
    assert "server" not in normalized.params
    assert normalized.raw_params["client_type"] is None
    assert normalized.raw_params["server"] is None


def test_normalize_keeps_explicit_values() -> None:
    normalized = normalize(
        FightInput(name="Fight", client_type="Official", server="JP"), ChannelDefaults()
    )
    assert normalized.params["client_type"] == "Official"
    assert normalized.params["server"] == "JP"
    assert normalized.raw_params["client_type"] == "Official"


def test_normalize_uses_custom_channel_defaults() -> None:
    normalized = normalize(FightInput(name="Fight"), ChannelDefaults(client_type="YoStarEN", server="US"))
    assert normalized.params["client_type"] == "YoStarEN"
    assert normalized.params["server"] == "US"


def test_normalize_only_touches_fields_the_model_declares() -> None:
    normalized = normalize(MallInput(name="Mall", shopping=True), ChannelDefaults())
    assert normalized.params == {"shopping": True}
    assert "client_type" not in normalized.params
    assert "server" not in normalized.params


def test_normalize_raw_params_uses_alias_keys() -> None:
    normalized = normalize(FightInput(name="Fight", dr_grandet=True), ChannelDefaults())
    assert normalized.raw_params == {"DrGrandet": True}
    assert "name" not in normalized.raw_params


# --------------------------------------------------------------------------- #
# 6. schema 元信息（docs/05 §8.2、docs/09 §7.2、docs/13 §4）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("model", MODEL_TYPES, ids=MODEL_IDS)
def test_every_model_forbids_extra_in_schema(model: type[TaskInputBase]) -> None:
    assert _schema(model).get("additionalProperties") is False


def test_discriminated_union_schema_lists_all_nine_types() -> None:
    schema = ADAPTER.json_schema(ref_template="#/$defs/{model}")
    assert schema["discriminator"]["propertyName"] == "name"
    assert set(schema["discriminator"]["mapping"]) == set(TASK_LABELS)


def test_stone_is_marked_with_consume_risk() -> None:
    prop = _schema(FightInput)["properties"]["stone"]
    assert prop["x-risk"] == RISK_CONSUME == "consume"
    assert prop["x-risk"] == RiskLevel.CONSUME


@pytest.mark.parametrize("type_name", TYPE_NAMES)
def test_x_risk_schema_markers_and_risk_fields_are_the_same_definition(type_name: str) -> None:
    model = TASK_MODELS[type_name]
    marked = {
        field_name
        for field_name, field in model.model_fields.items()
        if (field.json_schema_extra or {}).get("x-risk")
    }
    assert marked == set(RISK_FIELDS[type_name])
    for field_name in marked:
        extra = model.model_fields[field_name].json_schema_extra or {}
        assert extra["x-risk"] == RISK_CONSUME


def test_series_widget_and_enum_labels() -> None:
    prop = _schema(FightInput)["properties"]["series"]
    assert prop["x-widget"] == "select"
    assert prop["x-enum-labels"]["-1"] == "禁用切换"
    assert prop["x-enum-labels"]["0"] == "自动选择最大可用次数"
    assert prop["x-enum-labels"]["6"] == "6 次"
    branch = _non_null_branch(prop)
    assert (branch["minimum"], branch["maximum"]) == (-1, 6)


def test_structured_widgets_are_declared() -> None:
    fight = _schema(FightInput)["properties"]
    assert fight["drops"]["x-widget"] == "item-count-map"
    drops = _non_null_branch(fight["drops"])
    assert drops["type"] == "object"
    assert drops["additionalProperties"] == {"type": "integer"}

    recruit = _schema(RecruitInput)["properties"]
    assert recruit["recruitment_time"]["x-widget"] == "duration-map"
    duration = _non_null_branch(recruit["recruitment_time"])
    assert duration["type"] == "object"
    assert duration["additionalProperties"] == {"type": "integer"}

    facility = _schema(InfrastInput)["properties"]["facility"]
    assert facility["x-widget"] == "ordered-list"
    items = _non_null_branch(facility)["items"]["enum"]
    assert set(items) == {"Mfg", "Trade", "Power", "Control", "Reception", "Office", "Dorm"}


def test_expedite_times_declares_its_dependency() -> None:
    prop = _schema(RecruitInput)["properties"]["expedite_times"]
    assert prop["x-depends-on"] == {"expedite": True}
    assert prop["x-label"] == "加急次数"


def test_depends_on_is_exported_for_theme_specific_fields() -> None:
    props = _schema(RoguelikeInput)["properties"]
    assert props["use_nonfriend_support"]["x-depends-on"] == {"use_support": True}
    assert props["start_foldartal_list"]["x-depends-on"] == {"theme": "Sami", "mode": 4}
    assert props["refresh_trader_with_dice"]["x-depends-on"] == {"theme": "Mizuki"}


def test_range_constraints_are_carried_by_json_schema() -> None:
    assert _non_null_branch(_schema(InfrastInput)["properties"]["threshold"]) == {
        "maximum": 1.0,
        "minimum": 0.0,
        "type": "number",
    }
    assert _non_null_branch(_schema(RecruitInput)["properties"]["select"])["items"] == {
        "maximum": 6,
        "minimum": 1,
        "type": "integer",
    }


# --------------------------------------------------------------------------- #
# 7. examples 与 PipelineCreate（docs/05 §11.3/§7.4）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("model", MODEL_TYPES + (PipelineCreate,), ids=MODEL_IDS + ["PipelineCreate"])
def test_examples_are_present_and_self_validating(model: type[Any]) -> None:
    examples = (model.model_config.get("json_schema_extra") or {}).get("examples")
    assert examples, f"{model.__name__} 缺 json_schema_extra['examples']"
    for example in examples:
        model.model_validate(example)


def test_pipeline_create_example_runs_through_the_discriminated_union() -> None:
    example = PipelineCreate.model_config["json_schema_extra"]["examples"][0]
    pipeline = PipelineCreate.model_validate(example)
    assert [task.name for task in pipeline.tasks] == [
        "StartUp",
        "Infrast",
        "Fight",
        "Recruit",
        "Mall",
        "Award",
        "CloseDown",
    ]
    assert pipeline.notify_on_finish is True
    assert pipeline.priority is None
    assert pipeline.title == "日常"


def test_pipeline_create_constraints() -> None:
    with pytest.raises(ValidationError) as excinfo:
        PipelineCreate(tasks=[])
    assert excinfo.value.errors()[0]["type"] == "too_short"

    with pytest.raises(ValidationError) as excinfo:
        PipelineCreate(tasks=[{"name": "Fight"}] * 33)
    assert excinfo.value.errors()[0]["type"] == "too_long"

    with pytest.raises(ValidationError) as excinfo:
        PipelineCreate(tasks=[{"name": "Fight"}], title="x" * 65)
    assert excinfo.value.errors()[0]["type"] == "string_too_long"

    with pytest.raises(ValidationError) as excinfo:
        PipelineCreate(tasks=[{"name": "Fight"}], priority=3)
    assert excinfo.value.errors()[0]["type"] == "less_than_equal"

    with pytest.raises(ValidationError) as excinfo:
        PipelineCreate(tasks=[{"name": "Fight"}], source="manual")
    assert excinfo.value.errors()[0]["type"] == "extra_forbidden"


def test_pipeline_create_nested_errors_keep_their_types() -> None:
    with pytest.raises(ValidationError) as excinfo:
        PipelineCreate(tasks=[{"name": "Nope"}])
    assert excinfo.value.errors()[0]["type"] == "union_tag_invalid"

    with pytest.raises(ValidationError) as excinfo:
        PipelineCreate(tasks=[{"name": "Fight", "theme": "Sami"}])
    assert excinfo.value.errors()[0]["type"] == "extra_forbidden"


def test_pipeline_create_propagates_cross_field_app_error() -> None:
    with pytest.raises(AppError) as excinfo:
        PipelineCreate(tasks=[{"name": "Roguelike", "mode": 2}])
    assert excinfo.value.code is ErrorCode.TASK_PARAM_DEPRECATED


# --------------------------------------------------------------------------- #
# 8. 轻量 import（domain 层无 IO，docs/02 §6）
# --------------------------------------------------------------------------- #


def test_import_is_lightweight_and_side_effect_free(tmp_path: Path) -> None:
    code = (
        "import sys\n"
        "import maa_api.domain.task as task\n"
        "assert task.TASK_LABELS['Fight'] == '刷理智'\n"
        "for forbidden in ('maa_api.db', 'sqlalchemy', 'sqlmodel'):\n"
        "    assert forbidden not in sys.modules, forbidden\n"
        "print('LIGHT OK')\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().endswith("LIGHT OK")
    assert list(tmp_path.iterdir()) == []
