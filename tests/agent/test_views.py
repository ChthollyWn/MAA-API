"""M90 T1：视图模型、附加规则与提交渲染（规格 §4.3.1、§4.5.2、§4.5.3、§6）。"""

from __future__ import annotations

import json

import pytest

from maa_api.agent.artifacts.views import (
    ChartComponent,
    HeadingComponent,
    NumberField,
    SelectField,
    StatsComponent,
    SubmitComponent,
    TableComponent,
    TextComponent,
    TextField,
    ToggleField,
    ViewInvalid,
    render_submission,
    validate_submission,
    validate_view,
    view_fields,
    view_json_schema,
    view_kind,
)

#: 规格 §4.3.1 的例子（规划师让用户挑关卡）。
EXAMPLE_VIEW = {
    "version": 1,
    "title": "这周刷哪一关",
    "components": [
        {
            "type": "text",
            "text": "按企鹅物流近 30 天的掉落率算出每件固源岩的期望理智，越低越划算。",
        },
        {
            "type": "table",
            "columns": [
                {"key": "stage", "label": "关卡"},
                {"key": "cost", "label": "每件理智", "align": "right", "format": "number"},
            ],
            "rows": [
                {"stage": "1-7", "cost": 13.4},
                {"stage": "S4-1", "cost": 15.2},
            ],
        },
        {
            "type": "field",
            "field": "select",
            "name": "stage",
            "label": "关卡",
            "options": [
                {"value": "1-7", "label": "1-7"},
                {"value": "S4-1", "label": "S4-1"},
            ],
        },
        {
            "type": "field",
            "field": "number",
            "name": "times",
            "label": "次数",
            "min": 1,
            "max": 99,
            "default": 10,
        },
        {
            "type": "field",
            "field": "toggle",
            "name": "use_medicine",
            "label": "可以用理智药",
        },
        {"type": "submit", "label": "就这么刷"},
    ],
}


def _view(components: list[dict], *, title: str = "视图", **extra: object) -> dict:
    return {"version": 1, "title": title, "components": components, **extra}


def _field(**values: object) -> dict:
    return {"type": "field", **values}


def _form(fields: list[dict], *, title: str = "表单") -> dict:
    return _view([*[_field(**field) for field in fields], {"type": "submit"}], title=title)


def _chart_view(
    categories: list[str], series: list[list], *, chart_type: str = "line"
) -> dict:
    return _view(
        [
            {
                "type": "chart",
                "chart": {
                    "chart_type": chart_type,
                    "title": "趋势",
                    "categories": categories,
                    "series": [
                        {"name": f"系列{index}", "values": values}
                        for index, values in enumerate(series)
                    ],
                },
            }
        ]
    )


FORM_VIEW = _view(
    [
        _field(
            field="select",
            name="stage",
            label="关卡",
            options=[
                {"value": "1-7", "label": "1-7"},
                {"value": "S4-1", "label": "S4-1"},
            ],
        ),
        _field(
            field="select",
            name="tags",
            label="标签",
            multiple=True,
            options=[
                {"value": "a", "label": "A"},
                {"value": "b", "label": "B"},
            ],
        ),
        _field(
            field="select",
            name="note",
            label="备注",
            required=False,
            options=[{"value": "x", "label": "X"}],
        ),
        _field(field="number", name="times", label="次数", min=1, max=99, unit="次"),
        _field(field="number", name="ratio", label="比例", required=False),
        _field(field="text", name="comment", label="说明", max_length=5),
        _field(field="text", name="optional_text", label="选填", required=False),
        _field(field="toggle", name="use_medicine", label="可以用理智药"),
        _field(field="toggle", name="extra_toggle", label="额外", required=False),
        {"type": "submit"},
    ],
    title="测试表单",
)

_SUBMISSION_BASE = {"stage": "1-7", "tags": ["a"], "times": 10, "comment": "好"}
_MISSING = object()


def _values(**overrides: object) -> dict:
    values = dict(_SUBMISSION_BASE)
    for key, value in overrides.items():
        if value is _MISSING:
            values.pop(key, None)
        else:
            values[key] = value
    return values


def test_view_accepts_every_component() -> None:
    example = validate_view(EXAMPLE_VIEW)
    assert view_kind(example) == "form"
    assert [field.name for field in view_fields(example)] == [
        "stage",
        "times",
        "use_medicine",
    ]

    spec = validate_view(
        _view(
            [
                {"type": "heading", "text": "标题"},
                {"type": "text", "text": "正文"},
                {"type": "stats", "items": [{"label": "场次", "value": 3}]},
                {
                    "type": "table",
                    "columns": [{"key": "stage", "label": "关卡"}],
                    "rows": [{"stage": "1-7"}],
                },
                {
                    "type": "chart",
                    "chart": {
                        "chart_type": "line",
                        "title": "趋势",
                        "categories": ["一"],
                        "series": [{"name": "系列", "values": [1]}],
                    },
                },
                _field(
                    field="select",
                    name="choice",
                    label="选择",
                    options=[{"value": "a", "label": "A"}],
                ),
                {"type": "submit"},
            ]
        )
    )
    assert [type(component) for component in spec.components] == [
        HeadingComponent,
        TextComponent,
        StatsComponent,
        TableComponent,
        ChartComponent,
        SelectField,
        SubmitComponent,
    ]
    assert view_kind(spec) == "form"
    assert [field.name for field in view_fields(spec)] == ["choice"]

    dashboard = validate_view(
        _view([{"type": "stats", "items": [{"label": "场次", "value": 3}]}])
    )
    assert view_kind(dashboard) == "dashboard"
    assert view_fields(dashboard) == []


def test_view_rejects_unknown_component_and_extra_keys() -> None:
    with pytest.raises(ViewInvalid) as caught:
        validate_view(_view([{"type": "html"}]))
    assert caught.value.errors[0] == {
        "path": "components.0",
        "message": "取值不在允许范围",
    }

    with pytest.raises(ViewInvalid) as caught:
        validate_view(_view([{"type": "heading", "text": "标题", "bogus": 1}]))
    assert caught.value.errors[0] == {
        "path": "components.0.bogus",
        "message": "不认识的字段",
    }


def test_fields_require_exactly_one_trailing_submit() -> None:
    field = _field(
        field="select",
        name="choice",
        label="选择",
        options=[{"value": "a", "label": "A"}],
    )
    views = [
        _view([field]),
        _view([field, {"type": "submit"}, {"type": "submit"}]),
        _view([field, {"type": "submit"}, {"type": "text", "text": "尾巴"}]),
        _view([{"type": "submit"}]),
    ]
    for view in views:
        with pytest.raises(ViewInvalid) as caught:
            validate_view(view)
        assert any(error["path"] == "components" for error in caught.value.errors)


def test_field_names_must_be_unique() -> None:
    with pytest.raises(ViewInvalid) as caught:
        validate_view(
            _form(
                [
                    _field(field="toggle", name="same", label="甲"),
                    _field(field="toggle", name="same", label="乙"),
                ]
            )
        )
    assert any(error["path"] == "components" for error in caught.value.errors)

    fields = [
        _field(field="toggle", name=f"flag_{index}", label=f"开关{index}")
        for index in range(21)
    ]
    with pytest.raises(ViewInvalid) as caught:
        validate_view(_form(fields))
    assert any(
        error["path"] == "components" and "20" in error["message"]
        for error in caught.value.errors
    )


def test_table_rows_only_use_declared_columns() -> None:
    columns = [
        {"key": "stage", "label": "关卡"},
        {"key": "cost", "label": "理智"},
    ]
    with pytest.raises(ViewInvalid) as caught:
        validate_view(
            _view(
                [
                    {
                        "type": "table",
                        "columns": columns,
                        "rows": [
                            {"stage": "1-7"},
                            {"stage": "S4-1", "cost": 15.2, "extra": 1},
                        ],
                    }
                ]
            )
        )
    assert caught.value.errors[0]["path"] == "components.0.rows.1"

    # 缺的键不补、不报错；前端按 null 处理。
    validate_view(
        _view([{"type": "table", "columns": columns, "rows": [{"stage": "1-7"}]}])
    )


def test_chart_series_must_match_categories() -> None:
    with pytest.raises(ViewInvalid) as caught:
        validate_view(_chart_view(["a", "b"], [[1]]))
    assert caught.value.errors[0]["path"].startswith("components.0.chart")

    with pytest.raises(ViewInvalid) as caught:
        validate_view(_chart_view(["a"], [[1], [2]], chart_type="pie"))
    assert caught.value.errors[0]["path"].startswith("components.0.chart")

    # 600 点通过：150 个分类 × 4 条系列。
    categories = [f"c{index}" for index in range(150)]
    valid = validate_view(_chart_view(categories, [[0] * 150 for _ in range(4)]))
    assert len(valid.components[0].chart.series) == 4

    # 604 点报错：151 个分类 × 4 条系列。
    categories = [f"c{index}" for index in range(151)]
    with pytest.raises(ViewInvalid) as caught:
        validate_view(_chart_view(categories, [[0] * 151 for _ in range(4)]))
    assert any(
        error["path"] == "components.0.chart" and "600" in error["message"]
        for error in caught.value.errors
    )

    # 总点数正好 600，但紧凑序列化超过 24 KiB。
    categories = ["分类" * 20 for _ in range(200)]
    raw_chart = {
        "chart_type": "line",
        "title": "趋势",
        "categories": categories,
        "series": [
            {"name": f"系列{index}", "values": [0] * 200} for index in range(3)
        ],
    }
    assert len(
        json.dumps(
            {**raw_chart, "caption": None},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ) > 24 * 1024
    with pytest.raises(ViewInvalid) as caught:
        validate_view(_chart_view(categories, [[0] * 200 for _ in range(3)]))
    assert any(
        error["path"] == "components.0.chart" and "24" in error["message"]
        for error in caught.value.errors
    )


def test_number_and_select_defaults_are_checked() -> None:
    bad_fields = [
        {"field": "number", "name": "n", "label": "N", "min": 10, "max": 5},
        {"field": "number", "name": "n", "label": "N", "min": 1, "max": 9, "default": 10},
        {
            "field": "select",
            "name": "s",
            "label": "S",
            "options": [{"value": "a", "label": "A"}],
            "default": "b",
        },
        {
            "field": "select",
            "name": "s",
            "label": "S",
            "options": [{"value": "a", "label": "A"}],
            "multiple": True,
            "default": "a",
        },
        {
            "field": "select",
            "name": "s",
            "label": "S",
            "options": [{"value": "a", "label": "A"}],
            "multiple": False,
            "default": ["a"],
        },
    ]
    for field in bad_fields:
        with pytest.raises(ViewInvalid) as caught:
            validate_view(_form([field]))
        assert caught.value.errors[0]["path"].startswith("components.0")


def test_view_rejects_nan_control_characters_and_oversize() -> None:
    for value in (float("nan"), float("inf")):
        with pytest.raises(ViewInvalid):
            validate_view(
                _view([{"type": "stats", "items": [{"label": "数字", "value": value}]}])
            )

    for title in ("标\x01题", "标\n题"):
        with pytest.raises(ViewInvalid) as caught:
            validate_view(_view([{"type": "text", "text": "正文"}], title=title))
        assert caught.value.errors[0]["path"] == "title"

    # 换行只允许出现在 text 组件的 text 与文本字段的 default 里。
    spec = validate_view(
        _view(
            [
                {"type": "text", "text": "第一行\n第二行"},
                _field(
                    field="text",
                    name="comment",
                    label="说明",
                    default="第一行\n第二行",
                ),
                {"type": "submit"},
            ]
        )
    )
    assert spec.components[0].text == "第一行\n第二行"
    assert spec.components[1].default == "第一行\n第二行"

    # 200 行 × 12 列 × 200 字符，序列化远超 64 KiB。
    columns = [{"key": f"c{index}", "label": f"C{index}"} for index in range(12)]
    rows = [
        {f"c{index}": "x" * 200 for index in range(12)} for _ in range(200)
    ]
    with pytest.raises(ViewInvalid) as caught:
        validate_view(_view([{"type": "table", "columns": columns, "rows": rows}]))
    assert caught.value.errors[0]["path"] == ""


def test_error_paths_skip_discriminator_segments() -> None:
    with pytest.raises(ViewInvalid) as caught:
        validate_view(
            _view(
                [
                    {"type": "heading", "text": "标题"},
                    {"type": "text", "text": "正文"},
                    {"type": "stats", "items": [{"label": "场次", "value": 3}]},
                    {
                        "type": "field",
                        "field": "select",
                        "name": "choice",
                        "label": "选择",
                        "options": [{"value": "a"}],
                    },
                    {"type": "submit"},
                ]
            )
        )
    assert caught.value.errors[0]["path"] == "components.3.options.0.label"


def test_json_schema_lists_only_whitelisted_types() -> None:
    schema = view_json_schema()

    mapping = schema["properties"]["components"]["items"]["discriminator"]["mapping"]
    assert set(mapping) == {
        "heading",
        "text",
        "stats",
        "table",
        "chart",
        "field",
        "submit",
    }
    field_mapping = mapping["field"]["discriminator"]["mapping"]
    assert set(field_mapping) == {"select", "number", "text", "toggle"}
    assert schema["required"] == ["version", "title", "components"]


def test_validate_submission_checks_each_field_type() -> None:
    spec = validate_view(FORM_VIEW)

    valid = validate_submission(
        spec, _values(times=10.0, comment="  说明  ", tags=["b", "a"])
    )
    assert valid == {
        "stage": "1-7",
        "tags": ["b", "a"],
        "times": 10,
        "comment": "说明",
        "use_medicine": False,
        "extra_toggle": False,
    }
    assert isinstance(valid["times"], int)
    for absent in ("note", "ratio", "optional_text"):
        assert absent not in valid

    cases = [
        ({"stage": "nope"}, "stage", "不在可选项里"),
        ({"stage": 1}, "stage", "选项不合法"),
        ({"tags": ["a", "a"]}, "tags", "不能重复选择"),
        ({"tags": []}, "tags", "必填"),
        ({"tags": "a"}, "tags", "选项不合法"),
        ({"tags": ["zzz"]}, "tags", "不在可选项里"),
        ({"times": True}, "times", "需要填数字"),
        ({"times": 0}, "times", "不能小于 1"),
        ({"times": 100}, "times", "不能大于 99"),
        ({"times": _MISSING}, "times", "必填"),
        ({"stage": _MISSING}, "stage", "必填"),
        ({"comment": "abcdef"}, "comment", "不能超过 5 字"),
        ({"comment": "   "}, "comment", "必填"),
        ({"comment": 5}, "comment", "需要是文本"),
        ({"use_medicine": "yes"}, "use_medicine", "需要是开或关"),
        ({"extra": 1}, "extra", "表单里没有这一项"),
    ]
    for overrides, path, message in cases:
        with pytest.raises(ViewInvalid) as caught:
            validate_submission(spec, _values(**overrides))
        assert caught.value.errors[0] == {"path": path, "message": message}


def test_render_submission_uses_labels_and_values() -> None:
    spec = validate_view(EXAMPLE_VIEW)
    values = validate_submission(
        spec, {"stage": "1-7", "times": 10, "use_medicine": False}
    )

    assert render_submission(spec, values, artifact_id="1a2b3c4dxyz") == (
        "【表单提交】这周刷哪一关（产物 1a2b3c4d）\n"
        "- 关卡：1-7（1-7）\n"
        "- 次数：10\n"
        "- 可以用理智药：否"
    )

    extra = validate_view(
        _view(
            [
                _field(
                    field="select",
                    name="tags",
                    label="标签",
                    multiple=True,
                    options=[
                        {"value": "a", "label": "甲"},
                        {"value": "b", "label": "乙"},
                    ],
                ),
                _field(field="number", name="ratio", label="比例", unit="%"),
                _field(field="text", name="comment", label="说明", required=False),
                _field(field="text", name="empty", label="空的", required=False),
                {"type": "submit"},
            ],
            title="补充",
        )
    )
    values = validate_submission(
        extra,
        {"tags": ["a", "b"], "ratio": 2.5, "comment": "第一行\n第二行"},
    )
    assert render_submission(extra, values, artifact_id="abcdefgh123") == (
        "【表单提交】补充（产物 abcdefgh）\n"
        "- 标签：甲（a）、乙（b）\n"
        "- 比例：2.5%\n"
        "- 说明：第一行\n"
        "  第二行\n"
        "- 空的：（未填）"
    )
