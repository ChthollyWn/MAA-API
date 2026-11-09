"""生成式视图 v1：白名单组件的模型、附加规则、表单提交的校验与渲染。"""

from __future__ import annotations

import json
import math
import re
from typing import Annotated, Any, Literal, Mapping, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

VIEW_MIME = "application/vnd.maa.view+json"
VIEW_VERSION = 1
MAX_COMPONENTS = 40
MAX_FIELDS = 20
MAX_VIEW_BYTES = 64 * 1024
MAX_SUBMISSION_BYTES = 4 * 1024
MAX_SUBMISSIONS_KEPT = 5
MAX_CHART_POINTS = 600
MAX_CHART_BYTES = 24 * 1024
FIELD_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"

ShortText = Annotated[str, Field(min_length=1, max_length=80)]
Label = Annotated[str, Field(min_length=1, max_length=40)]
Name = Annotated[str, Field(pattern=FIELD_NAME_PATTERN)]
Description = Annotated[str, Field(max_length=120)]
Finite = Annotated[float, Field(allow_inf_nan=False)]


class ViewInvalid(ValueError):
    """视图或提交不合法；``errors`` 每项是 ``{"path": "...", "message": "..."}``，最多 20 项。"""

    def __init__(self, errors: list[dict[str, str]]) -> None:
        self.errors = errors[:20]
        super().__init__("；".join(f"{item['path']}：{item['message']}" for item in self.errors))


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class HeadingComponent(_Base):
    type: Literal["heading"] = "heading"
    text: ShortText
    level: Literal[2, 3] = 2


class TextComponent(_Base):
    type: Literal["text"] = "text"
    text: Annotated[str, Field(min_length=1, max_length=2000)]
    tone: Literal["default", "muted", "warning"] = "default"


class StatItem(_Base):
    label: Label
    value: Union[Finite, Annotated[str, Field(min_length=1, max_length=24)]]
    unit: Annotated[str | None, Field(max_length=16)] = None
    delta: Finite | None = None
    hint: Annotated[str | None, Field(max_length=80)] = None


class StatsComponent(_Base):
    type: Literal["stats"] = "stats"
    items: Annotated[list[StatItem], Field(min_length=1, max_length=6)]


class TableColumn(_Base):
    key: Name
    label: Label
    align: Literal["left", "right"] = "left"
    format: Literal["text", "number", "percent"] = "text"


CellValue = Union[Annotated[str, Field(max_length=200)], Finite, bool, None]


class TableComponent(_Base):
    type: Literal["table"] = "table"
    title: ShortText | None = None
    columns: Annotated[list[TableColumn], Field(min_length=1, max_length=12)]
    rows: Annotated[list[dict[Name, CellValue]], Field(max_length=200)]
    caption: Annotated[str | None, Field(max_length=300)] = None


class ChartSeries(_Base):
    name: Label
    values: Annotated[list[Finite | None], Field(min_length=1, max_length=200)]


class ChartSpec(_Base):
    chart_type: Literal["line", "bar", "pie"]
    title: ShortText
    categories: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=40)]],
        Field(min_length=1, max_length=200),
    ]
    series: Annotated[list[ChartSeries], Field(min_length=1, max_length=6)]
    stacked: bool = False
    x_label: Annotated[str | None, Field(max_length=40)] = None
    y_label: Annotated[str | None, Field(max_length=40)] = None
    unit: Annotated[str | None, Field(max_length=16)] = None
    source: Annotated[str | None, Field(max_length=200)] = None


class ChartComponent(_Base):
    type: Literal["chart"] = "chart"
    chart: ChartSpec
    caption: Annotated[str | None, Field(max_length=300)] = None


class SelectOption(_Base):
    value: Annotated[str, Field(min_length=1, max_length=64)]
    label: Label
    description: Description | None = None


class SelectField(_Base):
    type: Literal["field"] = "field"
    field: Literal["select"] = "select"
    name: Name
    label: Label
    description: Description | None = None
    required: bool = True
    options: Annotated[list[SelectOption], Field(min_length=1, max_length=20)]
    multiple: bool = False
    default: str | list[str] | None = None


class NumberField(_Base):
    type: Literal["field"] = "field"
    field: Literal["number"] = "number"
    name: Name
    label: Label
    description: Description | None = None
    required: bool = True
    min: Finite | None = None
    max: Finite | None = None
    step: Annotated[Finite | None, Field(gt=0)] = None
    unit: Annotated[str | None, Field(max_length=16)] = None
    default: Finite | None = None


class TextField(_Base):
    type: Literal["field"] = "field"
    field: Literal["text"] = "text"
    name: Name
    label: Label
    description: Description | None = None
    required: bool = True
    max_length: Annotated[int, Field(ge=1, le=500)] = 200
    multiline: bool = False
    placeholder: Annotated[str | None, Field(max_length=60)] = None
    default: Annotated[str | None, Field(max_length=500)] = None


class ToggleField(_Base):
    type: Literal["field"] = "field"
    field: Literal["toggle"] = "toggle"
    name: Name
    label: Label
    description: Description | None = None
    required: bool = False
    default: bool = False


FieldBlock = Annotated[
    Union[SelectField, NumberField, TextField, ToggleField], Field(discriminator="field")
]


class SubmitComponent(_Base):
    type: Literal["submit"] = "submit"
    label: Annotated[str, Field(min_length=1, max_length=12)] = "提交"


Component = Annotated[
    Union[
        HeadingComponent,
        TextComponent,
        StatsComponent,
        TableComponent,
        ChartComponent,
        FieldBlock,
        SubmitComponent,
    ],
    Field(discriminator="type"),
]


class ViewSpec(_Base):
    version: Literal[1]
    title: ShortText
    description: Annotated[str | None, Field(max_length=300)] = None
    components: Annotated[list[Component], Field(min_length=1, max_length=MAX_COMPONENTS)]


_VIEW_ADAPTER = TypeAdapter(ViewSpec)

_FIELD_TYPES = frozenset({"select", "number", "text", "toggle"})
_COMPONENT_TYPES = frozenset({"heading", "text", "stats", "table", "chart", "submit"})

#: Pydantic 错误类型到中文原因的映射（规格 §4.3.1）。
_REASONS: dict[str, str] = {
    "missing": "必填",
    "extra_forbidden": "不认识的字段",
    "union_tag_invalid": "取值不在允许范围",
    "union_tag_not_found": "取值不在允许范围",
    "literal_error": "取值不在允许范围",
    "string_type": "需要字符串",
    "string_pattern_mismatch": "格式不对",
    "string_too_short": "太短",
    "too_short": "太短",
    "string_too_long": "太长",
    "too_long": "太长",
    "float_parsing": "需要数字",
    "int_parsing": "需要数字",
    "float_type": "需要数字",
    "int_type": "需要数字",
    "bool_type": "需要布尔值",
    "bool_parsing": "需要布尔值",
    "greater_than": "超出范围",
    "greater_than_equal": "超出范围",
    "less_than": "超出范围",
    "less_than_equal": "超出范围",
}

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _loc_path(loc: tuple[Any, ...]) -> str:
    """把 Pydantic 的 ``loc`` 清洗成视图路径（丢掉判别值段与 ``[key]``）。"""
    parts = [
        part
        for part in loc
        if not (isinstance(part, str) and part.startswith("[") and part.endswith("]"))
    ]
    if len(parts) >= 3 and parts[0] == "components" and isinstance(parts[1], int):
        if parts[2] == "field":
            del parts[2]
            if len(parts) >= 3 and parts[2] in _FIELD_TYPES:
                del parts[2]
        elif parts[2] in _COMPONENT_TYPES:
            del parts[2]
    return ".".join(str(part) for part in parts)


def _number_text(value: float | int) -> str:
    """数字的展示文本：整数不带小数点。"""
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)


def validate_view(raw: Any) -> ViewSpec:
    """先 Pydantic 校验，再查 §4.3.1 的附加规则；失败抛 ``ViewInvalid``。"""
    try:
        spec = _VIEW_ADAPTER.validate_python(raw)
    except ValidationError as exc:
        raise ViewInvalid(
            [
                {
                    "path": _loc_path(error["loc"]),
                    "message": _REASONS.get(error["type"], error["msg"]),
                }
                for error in exc.errors()
            ]
        ) from None
    errors = _check_rules(spec)
    if errors:
        raise ViewInvalid(errors)
    return spec


def view_kind(spec: ViewSpec) -> Literal["form", "dashboard"]:
    """有至少一个表单字段返回 ``form``，否则 ``dashboard``。"""
    return "form" if view_fields(spec) else "dashboard"


def view_fields(spec: ViewSpec) -> list[SelectField | NumberField | TextField | ToggleField]:
    """按组件顺序返回全部表单字段。"""
    return [
        component
        for component in spec.components
        if isinstance(component, (SelectField, NumberField, TextField, ToggleField))
    ]


def view_json_schema() -> dict[str, Any]:
    """``TypeAdapter(ViewSpec).json_schema()``；只给测试与文档核对，不进工具声明。"""
    return _VIEW_ADAPTER.json_schema()


def _check_rules(spec: ViewSpec) -> list[dict[str, str]]:
    """按规格 §4.3.1 的顺序检查附加规则 1–12，返回中文错误列表。"""
    errors: list[dict[str, str]] = []
    fields = view_fields(spec)

    # 1. 字段名互不相同；字段总数不超过 20。
    seen: set[str] = set()
    for field in fields:
        if field.name in seen:
            errors.append({"path": "components", "message": f"字段名 {field.name} 重复"})
        seen.add(field.name)
    if len(fields) > MAX_FIELDS:
        errors.append({"path": "components", "message": f"字段不能超过 {MAX_FIELDS} 个"})

    # 2. 有字段时恰好一个 submit 且在最后；没有字段时不能有 submit。
    submit_indexes = [
        index for index, component in enumerate(spec.components) if component.type == "submit"
    ]
    if fields:
        if len(submit_indexes) != 1:
            errors.append(
                {"path": "components", "message": "有表单字段时必须恰好有一个 submit"}
            )
        elif submit_indexes[0] != len(spec.components) - 1:
            errors.append({"path": "components", "message": "submit 必须是最后一个组件"})
    elif submit_indexes:
        errors.append({"path": "components", "message": "没有表单字段时不能有 submit"})

    # 3. 表格行的键都必须是已声明的列。
    for index, component in enumerate(spec.components):
        if not isinstance(component, TableComponent):
            continue
        declared = {column.key for column in component.columns}
        for row_index, row in enumerate(component.rows):
            undeclared = [key for key in row if key not in declared]
            if undeclared:
                errors.append(
                    {
                        "path": f"components.{index}.rows.{row_index}",
                        "message": f"未声明的列 {undeclared[0]}",
                    }
                )

    # 4. 每条系列的长度等于分类数；饼图只能有一条系列。
    for index, component in enumerate(spec.components):
        if not isinstance(component, ChartComponent):
            continue
        category_count = len(component.chart.categories)
        if any(len(series.values) != category_count for series in component.chart.series):
            errors.append(
                {
                    "path": f"components.{index}.chart",
                    "message": "每条系列的长度必须等于分类数",
                }
            )
        if component.chart.chart_type == "pie" and len(component.chart.series) > 1:
            errors.append(
                {"path": f"components.{index}.chart", "message": "饼图只能有一条系列"}
            )

    # 5–7. 数字、选择、文本字段的默认值。
    for index, component in enumerate(spec.components):
        if isinstance(component, NumberField):
            if (
                component.min is not None
                and component.max is not None
                and component.min > component.max
            ):
                errors.append(
                    {"path": f"components.{index}.min", "message": "min 不能大于 max"}
                )
            if component.default is not None:
                if component.min is not None and component.default < component.min:
                    errors.append(
                        {"path": f"components.{index}.default", "message": "默认值小于 min"}
                    )
                if component.max is not None and component.default > component.max:
                    errors.append(
                        {"path": f"components.{index}.default", "message": "默认值大于 max"}
                    )
        elif isinstance(component, SelectField):
            option_values = [option.value for option in component.options]
            if len(option_values) != len(set(option_values)):
                errors.append(
                    {"path": f"components.{index}.options", "message": "选项值不能重复"}
                )
            if component.default is not None:
                defaults = (
                    component.default
                    if isinstance(component.default, list)
                    else [component.default]
                )
                if any(value not in option_values for value in defaults):
                    errors.append(
                        {
                            "path": f"components.{index}.default",
                            "message": "默认值不在可选项里",
                        }
                    )
                if component.multiple and not isinstance(component.default, list):
                    errors.append(
                        {
                            "path": f"components.{index}.default",
                            "message": "多选字段的默认值需要是字符串数组",
                        }
                    )
                if not component.multiple and not isinstance(component.default, str):
                    errors.append(
                        {
                            "path": f"components.{index}.default",
                            "message": "单选字段的默认值需要是字符串",
                        }
                    )
        elif isinstance(component, TextField):
            if (
                component.default is not None
                and len(component.default) > component.max_length
            ):
                errors.append(
                    {"path": f"components.{index}.default", "message": "默认值太长"}
                )

    # 8. 控制字符与换行白名单。
    errors.extend(_check_strings(spec))

    # 9. 数值一律有限（Pydantic 已拦，这里兜底）。
    errors.extend(_check_finite_numbers(spec))

    # 10. 序列化后不超过 64 KiB。
    body = spec.model_dump_json(exclude_none=True)
    if len(body.encode("utf-8")) > MAX_VIEW_BYTES:
        errors.append(
            {"path": "", "message": f"视图序列化后不能超过 {MAX_VIEW_BYTES // 1024} KiB"}
        )

    # 11–12. 图表总点数与紧凑序列化大小。
    for index, component in enumerate(spec.components):
        if not isinstance(component, ChartComponent):
            continue
        total_points = sum(len(series.values) for series in component.chart.series)
        if total_points > MAX_CHART_POINTS:
            errors.append(
                {
                    "path": f"components.{index}.chart",
                    "message": f"图表总点数不能超过 {MAX_CHART_POINTS}",
                }
            )
        chart_payload = {
            **component.chart.model_dump(exclude_none=True),
            "caption": component.caption,
        }
        chart_bytes = len(
            json.dumps(
                chart_payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        )
        if chart_bytes > MAX_CHART_BYTES:
            errors.append(
                {
                    "path": f"components.{index}.chart",
                    "message": f"图表序列化后不能超过 {MAX_CHART_BYTES // 1024} KiB",
                }
            )

    return errors


def _check_strings(spec: ViewSpec) -> list[dict[str, str]]:
    """规则 8：禁止字符；``\\n`` 只允许出现在 text 组件与文本字段默认值里。"""
    errors: list[dict[str, str]] = []

    def walk(value: Any, path: str, *, allow_newline: bool) -> None:
        if isinstance(value, str):
            if _CONTROL_CHARS.search(value):
                errors.append({"path": path, "message": "包含不允许的控制字符"})
            elif "\n" in value and not allow_newline:
                errors.append({"path": path, "message": "不能包含换行"})
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(item, f"{path}.{key}" if path else str(key), allow_newline=False)
        elif isinstance(value, list):
            for item_index, item in enumerate(value):
                walk(
                    item,
                    f"{path}.{item_index}" if path else str(item_index),
                    allow_newline=False,
                )

    walk(spec.title, "title", allow_newline=False)
    if spec.description is not None:
        walk(spec.description, "description", allow_newline=False)
    for index, component in enumerate(spec.components):
        for key, value in component.model_dump().items():
            allow_newline = (component.type == "text" and key == "text") or (
                component.type == "field"
                and getattr(component, "field", None) == "text"
                and key == "default"
            )
            walk(value, f"components.{index}.{key}", allow_newline=allow_newline)
    return errors


def _check_finite_numbers(spec: ViewSpec) -> list[dict[str, str]]:
    """规则 9：``model_dump()`` 里不允许出现 NaN / Infinity。"""
    errors: list[dict[str, str]] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, float):
            if not math.isfinite(value):
                errors.append({"path": path, "message": "数字必须是有限值"})
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(item, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for item_index, item in enumerate(value):
                walk(item, f"{path}.{item_index}" if path else str(item_index))

    for index, component in enumerate(spec.components):
        walk(component.model_dump(), f"components.{index}")
    return errors


def validate_submission(
    spec: ViewSpec, values: Mapping[str, Any]
) -> dict[str, Any]:
    """按字段定义校验并规整提交值；失败抛 ``ViewInvalid``，path 是字段名。"""
    fields = view_fields(spec)
    field_names = {field.name for field in fields}
    errors: list[dict[str, str]] = []
    result: dict[str, Any] = {}

    for field in fields:
        present = field.name in values and values[field.name] is not None
        if not present:
            if field.required:
                errors.append({"path": field.name, "message": "必填"})
            elif isinstance(field, ToggleField):
                result[field.name] = False
            continue

        raw = values[field.name]
        if isinstance(field, SelectField):
            option_values = [option.value for option in field.options]
            if field.multiple:
                if not isinstance(raw, list) or not all(
                    isinstance(item, str) for item in raw
                ):
                    errors.append({"path": field.name, "message": "选项不合法"})
                elif not raw:
                    if field.required:
                        errors.append({"path": field.name, "message": "必填"})
                    else:
                        result[field.name] = []
                elif any(item not in option_values for item in raw):
                    errors.append({"path": field.name, "message": "不在可选项里"})
                elif len(set(raw)) != len(raw):
                    errors.append({"path": field.name, "message": "不能重复选择"})
                else:
                    result[field.name] = list(raw)
            elif not isinstance(raw, str):
                errors.append({"path": field.name, "message": "选项不合法"})
            elif raw not in option_values:
                errors.append({"path": field.name, "message": "不在可选项里"})
            else:
                result[field.name] = raw
        elif isinstance(field, NumberField):
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                errors.append({"path": field.name, "message": "需要填数字"})
            elif not math.isfinite(float(raw)):
                errors.append({"path": field.name, "message": "需要填数字"})
            elif field.min is not None and raw < field.min:
                errors.append(
                    {
                        "path": field.name,
                        "message": f"不能小于 {_number_text(field.min)}",
                    }
                )
            elif field.max is not None and raw > field.max:
                errors.append(
                    {
                        "path": field.name,
                        "message": f"不能大于 {_number_text(field.max)}",
                    }
                )
            elif isinstance(raw, int):
                result[field.name] = int(raw)
            elif raw.is_integer():
                result[field.name] = int(raw)
            else:
                result[field.name] = float(raw)
        elif isinstance(field, TextField):
            if not isinstance(raw, str):
                errors.append({"path": field.name, "message": "需要是文本"})
            else:
                text = raw.strip()
                if field.required and not text:
                    errors.append({"path": field.name, "message": "必填"})
                elif len(text) > field.max_length:
                    errors.append(
                        {
                            "path": field.name,
                            "message": f"不能超过 {field.max_length} 字",
                        }
                    )
                else:
                    result[field.name] = text
        elif isinstance(field, ToggleField):
            if not isinstance(raw, bool):
                errors.append({"path": field.name, "message": "需要是开或关"})
            else:
                result[field.name] = raw

    for key in values:
        if key not in field_names:
            errors.append({"path": str(key), "message": "表单里没有这一项"})

    if errors:
        raise ViewInvalid(errors)
    return result


def _render_value(
    field: SelectField | NumberField | TextField | ToggleField, value: Any
) -> str:
    if isinstance(field, SelectField):
        labels = {option.value: option.label for option in field.options}
        if field.multiple:
            items = [str(item) for item in value]
            if not items:
                return "（未填）"
            return "、".join(
                f"{labels.get(item, item)}（{item}）" for item in items
            )
        text = str(value)
        return f"{labels.get(text, text)}（{text}）"
    if isinstance(field, NumberField):
        unit = field.unit or ""
        return f"{_number_text(value)}{unit}"
    if isinstance(field, ToggleField):
        return "是" if value else "否"
    return str(value).replace("\n", "\n  ")


def render_submission(spec: ViewSpec, values: Mapping[str, Any], *, artifact_id: str) -> str:
    """写给 Worker 的那条用户消息正文，格式见 §4.5.3。"""
    lines = [f"【表单提交】{spec.title}（产物 {artifact_id[:8]}）"]
    for field in view_fields(spec):
        rendered = (
            _render_value(field, values[field.name])
            if field.name in values
            else "（未填）"
        )
        lines.append(f"- {field.label}：{rendered}")
    return "\n".join(lines)
