#!/usr/bin/env python3
"""M3-01 方案前置实测：pydantic v2 判别联合、x-* schema 导出与最小 FastAPI 装配。

本脚本是**丢弃式探针**，不是生产代码，也不产出任何业务模块。它把
``docs/05-API规范与路由清单.md`` §2.2／§7／§8／§9／§11 与 ``docs/02-系统架构设计.md`` §7
里依赖 pydantic / FastAPI 具体行为的假设逐条跑一遍，结论供 M3 后续卡
（``domain/task.py`` 的 9 个任务模型、``api/errors.py`` 的异常处理器、``main.py`` 的装配）
直接引用。**本脚本不 import 任何 ``maa_api`` 模块，也不读写仓库真实库文件。**

做三组实验：

A. **判别联合与 400/422 分界**：3 个 ``extra="forbid"`` 的小模型 +
   ``Annotated[A|B|C, Field(discriminator="name")]``，分别经 ``TypeAdapter`` 与
   FastAPI 请求体两条路径，跑 6 种情形（未知 name、缺 name、多余字段、字段类型错、
   ``model_validator`` 抛 ``ValueError``、抛非 ``ValueError`` 的自定义异常）。
   记录每种情形的 ``ValidationError.errors()`` 形状与 HTTP 状态码/响应体；给出
   **可编程区分 400/422 的推荐实现**（按错误 ``type`` 串映射）；并做
   ``extra`` 默认 ``ignore`` 的反向对照。

B. **x-* 与 JSON Schema 导出**：``model_json_schema(ref_template="#/$defs/{model}")``
   的顶层键、``additionalProperties`` 位置、``anyOf`` + null、数值 ``enum``、
   ``x-*`` 原样保留；``AliasChoices`` + ``serialization_alias`` 的 property 名与
   ``model_dump(by_alias=...)`` 键名；``get_args(get_args(TaskInput)[0])`` 与
   ``model_fields["name"].default``；``model_fields_set`` 三态与
   ``model_dump(exclude_unset=True, by_alias=True, exclude={"name"})``。

C. **最小 FastAPI 装配**：``redirect_slashes=False`` 的 404（对照默认 307）、
   ``generate_unique_id_function`` 收到的 route 对象、``openapi_tags`` 顺序、
   ``HTTPException`` 头透传、``Exception`` 处理器 + ``raise_server_exceptions``、
   同步 ``TestClient`` 里的 ``lifespan`` 与 starlette/httpx 的 deprecation 警告原文。

产物（默认写入 ``tests/fixtures/``）：

- ``api_probe_result.json``：机器可读的实测结果（含各验收布尔项）；
- ``api_probe_findings.md``：人读结论，含 ``## 必读结论`` 一节（后续卡引用锚点）。

用法::

    .venv/bin/python scripts/probe_api_skeleton.py
    .venv/bin/python scripts/probe_api_skeleton.py --out-json /tmp/r.json --out-md /tmp/r.md
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import traceback
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal, get_args

import fastapi
import httpx
import pydantic
import starlette
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE_SCRIPT = Path(__file__).resolve()
DEFAULT_OUT_JSON = REPO_ROOT / "tests" / "fixtures" / "api_probe_result.json"
DEFAULT_OUT_MD = REPO_ROOT / "tests" / "fixtures" / "api_probe_findings.md"


def _warning_record(item: warnings.WarningMessage) -> dict[str, Any]:
    """把捕获到的 warning 变成可 JSON 序列化的记录（含原文）。"""
    return {
        "category": getattr(item.category, "__name__", str(item.category)),
        "message": str(item.message),
        "filename": item.filename,
        "lineno": item.lineno,
    }


# `fastapi.testclient` 在 import 期就会发 starlette 的 deprecation 警告，必须先于任何其它
# import 捕获（本脚本因此把它放在这里而不是文件顶部）。
TESTCLIENT_IMPORT_WARNINGS: list[dict[str, Any]] = []
with warnings.catch_warnings(record=True) as _captured:
    warnings.simplefilter("always")
    from fastapi.testclient import TestClient

TESTCLIENT_IMPORT_WARNINGS.extend(_warning_record(w) for w in _captured)


# ======================================================================================
# A. 判别联合与错误分界：探针用模型（全部定义在本脚本内部）
# ======================================================================================


class ProbeAppError(Exception):
    """模拟 ``maa_api.api.errors.AppError``：**普通 Exception 子类**，不是 ValueError/AssertionError。

    这正是 docs/05 §2.2 里「拦不住的语义问题抛 400」要依赖的异常类型。
    """

    default_code = "TASK_PARAM_INVALID"

    def __init__(self, message: str, *, code: str | None = None, **extra: Any) -> None:
        super().__init__(message)
        self.code = code or self.default_code
        self.message = message
        self.extra = extra


class _ProbeForbidBase(BaseModel):
    """docs/05 §7.2 ``TaskInputBase`` 的最小复刻：子类不重复声明 model_config。"""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        validate_assignment=True,
    )

    enable: bool | None = Field(default=None)


class ProbeAlpha(_ProbeForbidBase):
    name: Literal["Alpha"] = "Alpha"
    count: int | None = Field(default=None, ge=0)


class ProbeBeta(_ProbeForbidBase):
    name: Literal["Beta"] = "Beta"
    flag: bool | None = None


class ProbeGamma(_ProbeForbidBase):
    """两个校验器触发点：``mode=0`` 抛 ``ValueError``；``mode=1`` 抛 ``ProbeAppError``。"""

    name: Literal["Gamma"] = "Gamma"
    mode: Literal[0, 1] | None = None

    @model_validator(mode="after")
    def _check_mode(self) -> "ProbeGamma":
        if self.mode == 0:
            raise ValueError("mode=0 不允许（validator 抛 ValueError）")
        if self.mode == 1:
            raise ProbeAppError("mode=1 不允许（validator 抛非 ValueError）", risk="consume")
        return self


ProbeTaskInput = Annotated[
    ProbeAlpha | ProbeBeta | ProbeGamma,
    Field(discriminator="name"),
]


class ProbePipelineCreate(BaseModel):
    """docs/05 §7.4 ``PipelineCreate`` 的最小复刻：联合嵌在 list 里。"""

    model_config = ConfigDict(extra="forbid")

    tasks: list[ProbeTaskInput] = Field(min_length=1, max_length=32)
    title: str | None = Field(default=None, max_length=64)


class ProbeIgnoreAlpha(BaseModel):
    """A4 反向对照：``extra`` 用默认值（ignore），字段与 ProbeAlpha 一致。"""

    model_config = ConfigDict(populate_by_name=True)

    name: Literal["Alpha"] = "Alpha"
    count: int | None = Field(default=None, ge=0)


class ProbeIgnoreBeta(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: Literal["Beta"] = "Beta"
    flag: bool | None = None


ProbeIgnoreTaskInput = Annotated[
    ProbeIgnoreAlpha | ProbeIgnoreBeta,
    Field(discriminator="name"),
]


# docs/05 §2.2 要求的 400/422 分界，落成一张「错误 type 串 → (status, code)」表。
# 这张表的键**全部来自本脚本的实测**（见 result["cases"]），不是照抄文档。
PROBE_STATUS_BY_ERROR_TYPE: dict[str, tuple[int, str]] = {
    # discriminator 取值不在已知任务类型内 → 400（docs/05 §2.2 明确要求）
    "union_tag_invalid": (400, "UNKNOWN_TASK_TYPE"),
    # 请求体不是合法 JSON → 400 MALFORMED_JSON（docs/05 §4.2）
    "json_invalid": (400, "MALFORMED_JSON"),
}
PROBE_DEFAULT_ERROR_STATUS = (422, "VALIDATION_ERROR")


def classify_validation_errors(errors: list[dict[str, Any]]) -> tuple[int, str]:
    """推荐给 ``api/errors.py`` 的 400/422 判据（可直接照抄）。

    规则：

    - 任一错误的 ``type`` 命中 ``PROBE_STATUS_BY_ERROR_TYPE`` → 用表中的状态码；
    - ``json_invalid`` 只在**请求体解码失败**时才算 400：实测 FastAPI 对坏 JSON 给出
      ``loc=("body", <int>)``（字符偏移），而字段级 JSON 解析失败是 ``loc=("body", "<字段名>")``；
    - 其余（``extra_forbidden`` / ``int_parsing`` / ``value_error`` / ``union_tag_not_found``
      / ``missing`` …）→ 422 ``VALIDATION_ERROR``。
    """
    for err in errors:
        err_type = err.get("type")
        loc = list(err.get("loc") or ())
        if err_type == "union_tag_invalid":
            return PROBE_STATUS_BY_ERROR_TYPE["union_tag_invalid"]
        if err_type == "json_invalid" and len(loc) == 2 and isinstance(loc[1], int):
            return PROBE_STATUS_BY_ERROR_TYPE["json_invalid"]
    return PROBE_DEFAULT_ERROR_STATUS


def _error_summary(err: dict[str, Any]) -> dict[str, Any]:
    """把 ``ValidationError.errors()`` 的一项压成可 JSON 序列化的形状。"""
    ctx = err.get("ctx") or {}
    ctx_error = ctx.get("error")
    return {
        "type": err.get("type"),
        "loc": list(err.get("loc") or ()),
        "msg": err.get("msg"),
        "ctx_keys": sorted(ctx.keys()),
        "ctx_error_type": type(ctx_error).__name__ if ctx_error is not None else None,
        "input_present": "input" in err,
        "url_present": "url" in err,
    }


def _raw_error_record(exc: ValidationError) -> list[dict[str, Any]]:
    """保留原始 errors() 的键名顺序与 ctx 值类型（去掉不可序列化的异常实例）。"""
    out: list[dict[str, Any]] = []
    for err in exc.errors():
        item: dict[str, Any] = {}
        for key, value in err.items():
            if key == "ctx":
                item["ctx"] = {k: (f"<{type(v).__name__}>" if isinstance(v, BaseException) else v) for k, v in (value or {}).items()}
            elif key == "input":
                try:
                    json.dumps(value, ensure_ascii=False)
                    item["input"] = value
                except TypeError:
                    item["input"] = f"<{type(value).__name__}>"
            elif key == "url":
                continue
            else:
                item[key] = value
        out.append(item)
    return out


def _type_adapter_case(adapter: TypeAdapter[Any], payload: Any, *, label: str) -> dict[str, Any]:
    rec: dict[str, Any] = {"path": "TypeAdapter.validate_python", "input": payload}
    try:
        obj = adapter.validate_python(payload)
        rec.update(outcome="validated", model=type(obj).__name__)
    except ValidationError as exc:
        rec.update(
            outcome="validation_error",
            exception="ValidationError",
            error_types=[e.get("type") for e in exc.errors()],
            errors=[_error_summary(e) for e in exc.errors()],
            raw_errors=_raw_error_record(exc),
            json_serializable=_json_dumps_ok(exc.errors()),
        )
    except BaseException as exc:  # noqa: BLE001 - 探针要记录一切抛出形态
        rec.update(
            outcome="raised",
            exception=type(exc).__name__,
            exception_module=type(exc).__module__,
            message=str(exc),
            is_probe_app_error=isinstance(exc, ProbeAppError),
            probe_app_error_code=getattr(exc, "code", None),
            probe_app_error_extra=getattr(exc, "extra", None),
            wrapped_in_validation_error=isinstance(exc, ValidationError),
        )
    rec["case"] = label
    return rec


def _json_dumps_ok(value: Any) -> bool:
    try:
        json.dumps(value, ensure_ascii=False)
        return True
    except TypeError:
        return False


def _client_case(client: TestClient, path: str, payload: Any, *, raw_body: str | None = None) -> dict[str, Any]:
    body = raw_body if raw_body is not None else json.dumps(payload, ensure_ascii=False)
    response = client.post(path, content=body, headers={"content-type": "application/json"})
    rec: dict[str, Any] = {"path": path, "status_code": response.status_code}
    try:
        rec["body"] = response.json()
    except (ValueError, json.JSONDecodeError):
        rec["body_text"] = response.text
    if response.status_code >= 500:
        rec["body_text"] = response.text
    return rec


def build_raw_union_app() -> FastAPI:
    """没有任何自定义处理器的对照组：看 FastAPI 的出厂行为。"""
    app = FastAPI()

    @app.post("/api/probe/task")
    async def create_task(payload: ProbeTaskInput) -> dict[str, Any]:
        return {"name": payload.name}

    @app.post("/api/probe/pipeline")
    async def create_pipeline(payload: ProbePipelineCreate) -> dict[str, Any]:
        return {"names": [task.name for task in payload.tasks]}

    return app


def build_mapped_union_app(state: dict[str, Any]) -> FastAPI:
    """按推荐接法装配：AppError 由 app 级处理器接住，校验错误按 error type 映射 400/422。"""
    app = FastAPI()

    @app.exception_handler(ProbeAppError)
    async def _app_error(request: Request, exc: ProbeAppError) -> JSONResponse:
        state.setdefault("app_error_handler_calls", []).append(
            {
                "type": type(exc).__name__,
                "message": exc.message,
                "code": exc.code,
                "extra": exc.extra,
                "is_validation_error": isinstance(exc, ValidationError),
            }
        )
        return JSONResponse(
            status_code=400,
            content={"code": exc.code, "message": exc.message, "details": exc.extra},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        status, code = classify_validation_errors(errors)
        state.setdefault("request_validation_calls", []).append(
            {
                "error_types": [e.get("type") for e in errors],
                "locs": [list(e.get("loc") or ()) for e in errors],
                "ctx_error_types": [
                    type((e.get("ctx") or {}).get("error")).__name__
                    for e in errors
                    if (e.get("ctx") or {}).get("error") is not None
                ],
                "classified": [status, code],
                "jsonable_encoder_ok": True,
            }
        )
        return JSONResponse(
            status_code=status,
            content={"code": code, "details": jsonable_encoder(errors)},
        )

    @app.post("/api/probe/task")
    async def create_task(payload: ProbeTaskInput) -> dict[str, Any]:
        return {"name": payload.name}

    @app.post("/api/probe/pipeline")
    async def create_pipeline(payload: ProbePipelineCreate) -> dict[str, Any]:
        return {"names": [task.name for task in payload.tasks]}

    return app


A_CASES: dict[str, dict[str, Any]] = {
    "unknown_tag": {"name": "Nope"},
    "missing_tag": {},
    "extra_field": {"name": "Alpha", "nope": 1},
    "field_type_error": {"name": "Alpha", "count": "abc"},
    "validator_value_error": {"name": "Gamma", "mode": 0},
    "validator_app_error": {"name": "Gamma", "mode": 1},
}
A_PIPELINE_CASES: dict[str, dict[str, Any]] = {
    "pipeline_unknown_tag_in_list": {"tasks": [{"name": "Nope"}]},
    "pipeline_extra_field_in_list": {"tasks": [{"name": "Alpha", "nope": 1}]},
    "pipeline_empty_list": {"tasks": []},
    "pipeline_tasks_not_list": {"tasks": "x"},
}


def probe_union_and_errors() -> dict[str, Any]:
    """A 组：判别联合与 400/422 分界。"""
    adapter = TypeAdapter(ProbeTaskInput)

    adapter_cases = {name: _type_adapter_case(adapter, payload, label=name) for name, payload in A_CASES.items()}
    adapter_cases["malformed_json"] = _type_adapter_case_json(adapter)

    raw_app = build_raw_union_app()
    raw_client = TestClient(raw_app, raise_server_exceptions=False)
    raw_cases = {name: _client_case(raw_client, "/api/probe/task", payload) for name, payload in A_CASES.items()}
    raw_cases["malformed_json"] = _client_case(raw_client, "/api/probe/task", None, raw_body="{not json")
    raw_pipeline_cases = {
        name: _client_case(raw_client, "/api/probe/pipeline", payload) for name, payload in A_PIPELINE_CASES.items()
    }

    # `raise_server_exceptions` 默认 True：验证它是否直接抛出（C10 的一部分，放在这里一起测）。
    default_client = TestClient(raw_app)
    default_raise: dict[str, Any] = {}
    try:
        default_client.post(
            "/api/probe/task",
            content=json.dumps(A_CASES["validator_app_error"]),
            headers={"content-type": "application/json"},
        )
        default_raise.update({"raised": False})
    except BaseException as exc:  # noqa: BLE001
        default_raise.update(
            {
                "raised": True,
                "exception": type(exc).__name__,
                "message": str(exc),
                "is_probe_app_error": isinstance(exc, ProbeAppError),
                "wrapped_in_validation_error": isinstance(exc, ValidationError),
            }
        )

    mapped_state: dict[str, Any] = {}
    mapped_app = build_mapped_union_app(mapped_state)
    mapped_client = TestClient(mapped_app, raise_server_exceptions=False)
    mapped_cases = {name: _client_case(mapped_client, "/api/probe/task", payload) for name, payload in A_CASES.items()}
    mapped_cases["malformed_json"] = _client_case(mapped_client, "/api/probe/task", None, raw_body="{not json")
    mapped_pipeline_cases = {
        name: _client_case(mapped_client, "/api/probe/pipeline", payload) for name, payload in A_PIPELINE_CASES.items()
    }

    # 反向对照：extra 用默认 ignore 时，同样两个模型不再因多余字段报错。
    ignore_adapter = TypeAdapter(ProbeIgnoreTaskInput)
    ignore_type_adapter = {
        "extra_field": _type_adapter_case(ignore_adapter, {"name": "Alpha", "nope": 1}, label="ignore_extra_field"),
        "field_type_error": _type_adapter_case(ignore_adapter, {"name": "Alpha", "count": "abc"}, label="ignore_type_error"),
    }
    ignore_app = FastAPI()

    @ignore_app.post("/api/probe/task")
    async def ignore_create_task(payload: ProbeIgnoreTaskInput) -> dict[str, Any]:
        return {"name": payload.name}

    ignore_client = TestClient(ignore_app, raise_server_exceptions=False)
    ignore_fastapi = {
        "extra_field": _client_case(ignore_client, "/api/probe/task", {"name": "Alpha", "nope": 1}),
        "field_type_error": _client_case(ignore_client, "/api/probe/task", {"name": "Alpha", "count": "abc"}),
    }

    return {
        "app_error_class": {
            "name": ProbeAppError.__name__,
            "mro_minus_self": [c.__name__ for c in ProbeAppError.__mro__[1:]],
            "is_value_error_subclass": issubclass(ProbeAppError, ValueError),
            "is_assertion_error_subclass": issubclass(ProbeAppError, AssertionError),
        },
        "forbid_inherited_by_subclass": ProbeAlpha.model_config.get("extra") == "forbid"
        and ProbeGamma.model_config.get("extra") == "forbid",
        "type_adapter_cases": adapter_cases,
        "fastapi_raw_cases": raw_cases,
        "fastapi_raw_pipeline_cases": raw_pipeline_cases,
        "fastapi_raw_raise_server_exceptions_default": default_raise,
        "fastapi_mapped_cases": mapped_cases,
        "fastapi_mapped_pipeline_cases": mapped_pipeline_cases,
        "mapped_handler_state": mapped_state,
        "status_map": {k: list(v) for k, v in PROBE_STATUS_BY_ERROR_TYPE.items()},
        "default_status": list(PROBE_DEFAULT_ERROR_STATUS),
        "extra_ignore_control": {
            "type_adapter": ignore_type_adapter,
            "fastapi": ignore_fastapi,
            "config_extra": ProbeIgnoreAlpha.model_config.get("extra"),
        },
    }


def _type_adapter_case_json(adapter: TypeAdapter[Any]) -> dict[str, Any]:
    rec: dict[str, Any] = {"path": "TypeAdapter.validate_json", "case": "malformed_json"}
    try:
        adapter.validate_json("{not json")
        rec.update(outcome="validated")
    except ValidationError as exc:
        rec.update(
            outcome="validation_error",
            exception="ValidationError",
            error_types=[e.get("type") for e in exc.errors()],
            errors=[_error_summary(e) for e in exc.errors()],
            raw_errors=_raw_error_record(exc),
        )
    except BaseException as exc:  # noqa: BLE001
        rec.update(outcome="raised", exception=type(exc).__name__, message=str(exc))
    return rec


# ======================================================================================
# B. x-* 与 JSON Schema 导出：探针用模型
# ======================================================================================


def MaaField(
    *,
    label: str,
    group: str = "基础",
    widget: str | None = None,
    enum_labels: dict[str, str] | None = None,
    x_extra: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """docs/05 §7.2 ``MaaField`` 的复刻 + 一个 ``x_extra`` 逃生口（见 B 组实测第 5 条）。"""
    extra: dict[str, Any] = {"x-label": label, "x-group": group}
    if widget:
        extra["x-widget"] = widget
    if enum_labels:
        extra["x-enum-labels"] = enum_labels
    if x_extra:
        extra.update(x_extra)
    return Field(**kwargs, json_schema_extra=extra)


def docs_maafield_extra_collision() -> dict[str, Any]:
    """实测 docs/05 §7.2 的 ``MaaField`` 能否直接承载 ``x-risk`` / ``x-depends-on``。

    文档原样写法是「内部构造 ``json_schema_extra`` + ``**kwargs`` 直接转给 ``Field``」，
    因此调用方只要再传一个 ``json_schema_extra``（承载 x-risk / x-depends-on 的唯一入口）就会撞车。
    """
    rec: dict[str, Any] = {"path": "docs/05 §7.2 MaaField(**kwargs 里带 json_schema_extra)"}

    def docs_maafield(*, label: str, **kwargs: Any) -> Any:
        extra = {"x-label": label, "x-group": "基础"}
        return Field(**kwargs, json_schema_extra=extra)

    try:
        docs_maafield(label="连战次数", json_schema_extra={"x-risk": "consume"})
        rec.update(collision_reproduced=False)
    except TypeError as exc:
        rec.update(collision_reproduced=True, collision_message=str(exc))
    return rec


class _ProbeSchemaBase(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        validate_assignment=True,
    )

    enable: bool | None = MaaField(default=None, label="启用本任务")


class ProbeNestedSettings(BaseModel):
    """嵌套模型：用于确认 ``$defs`` 位置与 ``$ref`` 形态。"""

    model_config = ConfigDict(extra="forbid")

    client_type: Literal["Official", "Bilibili", "txwy"] | None = MaaField(
        default=None, label="客户端版本", group="账号"
    )


class ProbeStartUpInput(_ProbeSchemaBase):
    name: Literal["StartUp"] = "StartUp"

    account_name: str | None = MaaField(
        default=None,
        label="切换账号",
        group="账号",
        description="仅支持切换至已登录账号，按登录名模糊查找",
        examples=["123****4567", "张三"],
    )
    settings: ProbeNestedSettings | None = MaaField(default=None, label="渠道设置", group="账号")


class ProbeFightInput(_ProbeSchemaBase):
    name: Literal["Fight"] = "Fight"

    stage: str | None = MaaField(
        default=None,
        label="关卡名",
        description="留空则识别当前/上次关卡。剿灭作战须填 Annihilation",
        examples=["1-7", "S3-2", "Annihilation", "CE-6Hard"],
    )
    series: int | None = MaaField(
        default=None,
        label="连战次数",
        ge=-1,
        le=6,
        widget="select",
        enum_labels={"-1": "禁用切换", "0": "自动选择最大可用次数", "1": "1 次"},
        x_extra={"x-risk": "consume"},
    )
    medicine: int | None = MaaField(default=None, label="最大理智药数", group="资源消耗", ge=0)
    drops: dict[str, int] | None = MaaField(
        default=None, label="指定掉落数量", group="高级", description="key 为 item_id"
    )
    dr_grandet: bool | None = MaaField(
        default=None,
        label="节省理智碎石模式",
        group="资源消耗",
        validation_alias=AliasChoices("DrGrandet", "dr_grandet"),
        serialization_alias="DrGrandet",
    )
    times: int | None = MaaField(
        default=None,
        label="指定次数",
        validation_alias=AliasChoices("Times", "times"),
        x_extra={"x-depends-on": {"stage": True}},
    )


class ProbeInfrastInput(_ProbeSchemaBase):
    name: Literal["Infrast"] = "Infrast"

    mode: Literal[0, 10000, 20000] | None = MaaField(
        default=None,
        label="换班工作模式",
        widget="select",
        enum_labels={"0": "默认换班", "10000": "自定义换班", "20000": "一键轮换"},
    )
    facility: list[Literal["Mfg", "Trade", "Power", "Dorm"]] | None = MaaField(
        default=None, label="要换班的设施（有序）"
    )
    threshold: float | None = MaaField(default=None, label="工作心情阈值", ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_custom_mode(self) -> "ProbeInfrastInput":
        if self.mode == 10000 and self.threshold is None:
            raise ValueError("自定义换班模式（mode=10000）必须提供 threshold")
        return self


ProbeSchemaTaskInput = Annotated[
    ProbeStartUpInput | ProbeFightInput | ProbeInfrastInput,
    Field(discriminator="name"),
]

X_KEYS_UNDER_TEST = ("x-label", "x-group", "x-widget", "x-enum-labels", "x-risk", "x-depends-on")


def probe_schema_export() -> dict[str, Any]:
    """B 组：x-* 与 JSON Schema 导出。"""
    fight_schema = ProbeFightInput.model_json_schema(ref_template="#/$defs/{model}")
    startup_schema = ProbeStartUpInput.model_json_schema(ref_template="#/$defs/{model}")
    infrast_schema = ProbeInfrastInput.model_json_schema(ref_template="#/$defs/{model}")

    properties = fight_schema.get("properties", {})
    x_keys_by_field = {
        field: sorted(key for key in prop if key.startswith("x-")) for field, prop in properties.items()
    }
    nested_ref = None
    settings_prop = startup_schema.get("properties", {}).get("settings", {})
    for candidate in settings_prop.get("anyOf", []) + settings_prop.get("allOf", []):
        if "$ref" in candidate:
            nested_ref = candidate["$ref"]
    if "properties" in properties.get("settings", {}):
        nested_ref = "<inline>"

    alias_probe = ProbeFightInput.model_validate({"DrGrandet": True, "Times": 3, "stage": "1-7"})
    python_name_probe = ProbeFightInput.model_validate({"dr_grandet": True, "times": 3, "stage": "1-7"})

    # model_fields_set 三态。
    state_missing = ProbeFightInput.model_validate({"stage": "1-7"})
    state_explicit_null = ProbeFightInput.model_validate({"stage": None, "DrGrandet": None})
    state_explicit_value = ProbeFightInput.model_validate({"stage": "CE-6", "DrGrandet": False})

    def _state(model: ProbeFightInput) -> dict[str, Any]:
        return {
            "model_fields_set": sorted(model.model_fields_set),
            "dump_exclude_unset_by_alias": model.model_dump(
                exclude_unset=True, by_alias=True, exclude={"name"}
            ),
            "dump_exclude_none_by_alias": model.model_dump(exclude_none=True, by_alias=True, exclude={"name"}),
            "dump_default_by_alias": model.model_dump(by_alias=True, exclude={"name"}),
            "to_core_params_like": model.model_dump(
                exclude={"name"}, exclude_none=True, by_alias=True
            ),
        }

    union_adapter = TypeAdapter(ProbeSchemaTaskInput)
    union_schema = union_adapter.json_schema(ref_template="#/$defs/{model}")
    union_schema_default_template = union_adapter.json_schema()
    branch_models = list(get_args(get_args(ProbeSchemaTaskInput)[0]))

    return {
        "fight_schema": {
            "top_level_keys": sorted(fight_schema.keys()),
            "additional_properties": fight_schema.get("additionalProperties"),
            "additional_properties_in_properties": any(
                "additionalProperties" in prop for prop in properties.values()
            ),
            "has_defs": "$defs" in fight_schema,
            "properties": sorted(properties),
            "name_property": properties.get("name"),
            "series_property": properties.get("series"),
            "medicine_property": properties.get("medicine"),
            "drops_property": properties.get("drops"),
            "x_keys_by_field": x_keys_by_field,
            "x_labels_verbatim": {f: properties[f].get("x-label") for f in properties},
        },
        "startup_schema": {
            "top_level_keys": sorted(startup_schema.keys()),
            "defs_keys": sorted(startup_schema.get("$defs", {})),
            "nested_ref": nested_ref,
            "account_name_property": startup_schema["properties"].get("account_name"),
        },
        "infrast_schema": {
            "mode_property": infrast_schema["properties"].get("mode"),
            "facility_property": infrast_schema["properties"].get("facility"),
            "threshold_property": infrast_schema["properties"].get("threshold"),
        },
        "x_keys_under_test": list(X_KEYS_UNDER_TEST),
        "alias": {
            "schema_property_names": sorted(properties),
            "dr_grandet_schema_property_exists": "DrGrandet" in properties,
            "times_schema_property_exists": "Times" in properties,
            "times_python_property_exists": "times" in properties,
            "validation_both_spellings_ok": (
                alias_probe.dr_grandet is True and python_name_probe.dr_grandet is True
            ),
            "dump_by_alias_exclude_none_exclude_name": alias_probe.model_dump(
                by_alias=True, exclude_none=True, exclude={"name"}
            ),
            "dump_by_alias_exclude_unset_exclude_name": alias_probe.model_dump(
                by_alias=True, exclude_unset=True, exclude={"name"}
            ),
            "dump_no_alias_exclude_unset": python_name_probe.model_dump(exclude_unset=True, exclude={"name"}),
            "schema_by_alias_false_properties": sorted(
                ProbeFightInput.model_json_schema(by_alias=False)["properties"]
            ),
        },
        "union_introspection": {
            "get_args_get_args_first_len": len(branch_models),
            "branches": [m.__name__ for m in branch_models],
            "name_defaults": {m.__name__: m.model_fields["name"].default for m in branch_models},
            "type_adapter_schema_top_keys": sorted(union_schema.keys()),
            "type_adapter_discriminator": union_schema.get("discriminator"),
            "type_adapter_one_of_refs": [item.get("$ref") for item in union_schema.get("oneOf", [])],
            "type_adapter_defs_keys": sorted(union_schema.get("$defs", {})),
            "type_adapter_has_top_level_properties": "properties" in union_schema,
            "per_model_top_keys": {
                m.__name__: sorted(m.model_json_schema(ref_template="#/$defs/{model}").keys())
                for m in branch_models
            },
            "per_model_has_discriminator": {
                m.__name__: "discriminator" in m.model_json_schema(ref_template="#/$defs/{model}")
                for m in branch_models
            },
            "default_ref_template_top_keys": sorted(union_schema_default_template.keys()),
            "default_ref_template_ref": union_schema_default_template.get("oneOf", [{}])[0].get("$ref"),
            "model_fields_name_default": ProbeFightInput.model_fields["name"].default,
        },
        "model_fields_set": {
            "missing": _state(state_missing),
            "explicit_null": _state(state_explicit_null),
            "explicit_value": _state(state_explicit_value),
        },
        "docs_maafield_x_extra": docs_maafield_extra_collision(),
        "cross_field_validator": {
            "trigger": _type_adapter_case(
                TypeAdapter(ProbeInfrastInput), {"name": "Infrast", "mode": 10000}, label="infrast_cross_field"
            ),
        },
    }


# ======================================================================================
# C. 最小 FastAPI 装配
# ======================================================================================

PROBE_OPENAPI_TAGS: list[dict[str, str]] = [
    {"name": "system", "description": "健康检查、服务信息、日志查询"},
    {"name": "tasks", "description": "任务类型与参数 schema"},
    {"name": "pipelines", "description": "流水线提交、查询与取消"},
]

ROUTE_OBSERVATIONS: list[dict[str, Any]] = []
LIFESPAN_LOG: list[str] = []


@asynccontextmanager
async def probe_lifespan(app: FastAPI):
    LIFESPAN_LOG.append("enter")
    yield
    LIFESPAN_LOG.append("exit")


def _probe_operation_id(route: Any) -> str:
    """docs/05 §11.4 的 ``custom_operation_id``：实测 route 对象的属性形状。"""
    ROUTE_OBSERVATIONS.append(
        {
            "object_type": type(route).__name__,
            "object_module": type(route).__module__,
            "has_name": hasattr(route, "name"),
            "has_tags": hasattr(route, "tags"),
            "name": getattr(route, "name", None),
            "tags": list(getattr(route, "tags", []) or []),
            "path": getattr(route, "path", None),
            "methods": sorted(getattr(route, "methods", []) or []),
            "is_api_route_instance": isinstance(route, APIRoute),
            "is_annotated_as_api_route_in_docs": True,
        }
    )
    tag = route.tags[0] if route.tags else "default"
    return f"{tag}_{route.name}"


def build_probe_router() -> APIRouter:
    router = APIRouter(prefix="/api/probe", tags=["system"])

    @router.get("/no-slash", name="read_probe", summary="读探针")
    async def read_probe() -> dict[str, Any]:
        return {"ok": True}

    @router.get("/unauthorized", name="unauthorized")
    async def unauthorized() -> dict[str, Any]:
        raise HTTPException(
            status_code=401,
            detail="token 缺失或不匹配",
            headers={"WWW-Authenticate": "Bearer"},
        )

    @router.get("/boom", name="boom")
    async def boom() -> dict[str, Any]:
        raise RuntimeError("kaboom")

    return router


def build_assembly_app() -> FastAPI:
    app = FastAPI(
        title="maa-api probe",
        generate_unique_id_function=_probe_operation_id,
        redirect_slashes=False,
        openapi_tags=PROBE_OPENAPI_TAGS,
        lifespan=probe_lifespan,
    )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"code": "INTERNAL_ERROR", "type": type(exc).__name__, "message": str(exc)},
        )

    @app.get("/ping", name="ping")
    async def ping() -> dict[str, Any]:
        return {"pong": True}

    app.include_router(build_probe_router())
    return app


def probe_app_assembly() -> dict[str, Any]:
    """C 组：最小 FastAPI 装配行为。"""
    ROUTE_OBSERVATIONS.clear()
    LIFESPAN_LOG.clear()

    app = build_assembly_app()
    client = TestClient(app, raise_server_exceptions=False)

    trailing_slash = {
        "disabled_status": client.get("/api/probe/no-slash/", follow_redirects=False).status_code,
        "no_slash_status": client.get("/api/probe/no-slash", follow_redirects=False).status_code,
    }

    control_app = FastAPI()
    control_app.include_router(build_probe_router())
    control_client = TestClient(control_app, raise_server_exceptions=False)
    control_raw = control_client.get("/api/probe/no-slash/", follow_redirects=False)
    trailing_slash.update(
        {
            "control_default_status": control_raw.status_code,
            "control_location": control_raw.headers.get("location"),
            "control_followed_status": control_client.get("/api/probe/no-slash/").status_code,
        }
    )

    unauthorized = client.get("/api/probe/unauthorized")
    http_exception = {
        "status_code": unauthorized.status_code,
        "headers": {k.lower(): v for k, v in unauthorized.headers.items()},
        "www_authenticate": unauthorized.headers.get("www-authenticate"),
        "body": unauthorized.json(),
    }

    boom = client.get("/api/probe/boom")
    internal_error = {"status_code": boom.status_code, "body": boom.json()}

    # raise_server_exceptions 默认 True 时的行为。
    strict_client = TestClient(app)
    raise_default: dict[str, Any] = {}
    try:
        strict_client.get("/api/probe/boom")
        raise_default.update({"raised": False})
    except BaseException as exc:  # noqa: BLE001
        raise_default.update({"raised": True, "exception": type(exc).__name__, "message": str(exc)})

    spec = app.openapi()
    openapi = {
        "tags_order": [tag["name"] for tag in spec.get("tags", [])],
        "tag_descriptions": {tag["name"]: tag.get("description") for tag in spec.get("tags", [])},
        "paths": sorted(spec.get("paths", {})),
        "operation_ids": {
            path: {method: op.get("operationId") for method, op in ops.items()}
            for path, ops in spec.get("paths", {}).items()
        },
        "route_observations": list(ROUTE_OBSERVATIONS),
    }

    # lifespan：同步测试里能否正常进入/退出；不用 context manager 时会怎样。
    LIFESPAN_LOG.clear()
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        with TestClient(app, raise_server_exceptions=False) as ctx_client:
            inside_status = ctx_client.get("/ping").status_code
            log_inside = list(LIFESPAN_LOG)
        lifecycle_warnings = [_warning_record(w) for w in captured]
    lifespan = {
        "log_inside_context": log_inside,
        "log_after_context": list(LIFESPAN_LOG),
        "entered": "enter" in log_inside,
        "exited": LIFESPAN_LOG.count("exit") >= 1 and LIFESPAN_LOG[-1] == "exit",
        "inside_request_status": inside_status,
        "warnings_during_context": lifecycle_warnings,
        "no_context_manager": _lifespan_without_context(app),
    }

    return {
        "redirect_slashes": trailing_slash,
        "http_exception": http_exception,
        "exception_handler_500": internal_error,
        "raise_server_exceptions_default": raise_default,
        "openapi": openapi,
        "lifespan": lifespan,
        "starlette_deprecations": {
            "import_time": list(TESTCLIENT_IMPORT_WARNINGS),
            "during_use": _deprecation_probe(client),
        },
    }


def _lifespan_without_context(app: FastAPI) -> dict[str, Any]:
    LIFESPAN_LOG.clear()
    client = TestClient(app, raise_server_exceptions=False)
    status = client.get("/ping").status_code
    return {"status_code": status, "log": list(LIFESPAN_LOG), "entered": "enter" in LIFESPAN_LOG}


def _deprecation_probe(client: TestClient) -> list[dict[str, Any]]:
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        client.get("/ping")
        records = [_warning_record(w) for w in captured]
    # 只保留 deprecation 类，其它 warning 不属本卡观测面。
    return [r for r in records if "Deprecation" in r["category"] or "deprecat" in r["message"].lower()]


# ======================================================================================
# 汇总：required_checks 与结果 JSON
# ======================================================================================


def _case_types(cases: dict[str, Any], name: str) -> list[str]:
    """取某个情形的错误 ``type`` 列表：TypeAdapter 记录直接用，HTTP 记录从响应体 ``detail`` 抽。"""
    rec = cases.get(name) or {}
    types = list(rec.get("error_types") or [])
    if not types:
        detail = (rec.get("body") or {}).get("detail")
        if isinstance(detail, list):
            types = [item.get("type") for item in detail if isinstance(item, dict)]
    return types


def _case_status(cases: dict[str, Any], name: str) -> Any:
    return (cases.get(name) or {}).get("status_code")


def run_all_probes() -> dict[str, Any]:
    result: dict[str, Any] = {
        "probe_script": str(PROBE_SCRIPT.relative_to(REPO_ROOT)),
        "python_version": sys.version.split()[0],
        "pydantic_version": pydantic.VERSION,
        "fastapi_version": fastapi.__version__,
        "starlette_version": starlette.__version__,
        "httpx_version": httpx.__version__,
        "platform": platform.platform(),
        "machine": platform.machine(),
    }
    errors: list[str] = []

    try:
        result["group_a"] = probe_union_and_errors()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"group_a: {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        result["group_a"] = {}
    try:
        result["group_b"] = probe_schema_export()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"group_b: {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        result["group_b"] = {}
    try:
        result["group_c"] = probe_app_assembly()
    except Exception as exc:  # noqa: BLE001
        errors.append(f"group_c: {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        result["group_c"] = {}

    a = result.get("group_a", {})
    b = result.get("group_b", {})
    c = result.get("group_c", {})

    ta_cases = a.get("type_adapter_cases", {})
    raw_cases = a.get("fastapi_raw_cases", {})
    mapped_cases = a.get("fastapi_mapped_cases", {})
    raw_pipeline = a.get("fastapi_raw_pipeline_cases", {})
    mapped_pipeline = a.get("fastapi_mapped_pipeline_cases", {})
    ignore_control = a.get("extra_ignore_control", {})
    fight = b.get("fight_schema", {})
    startup = b.get("startup_schema", {})
    infrast = b.get("infrast_schema", {})
    alias = b.get("alias", {})
    union = b.get("union_introspection", {})
    states = b.get("model_fields_set", {})
    redirect = c.get("redirect_slashes", {})
    openapi = c.get("openapi", {})
    lifespan = c.get("lifespan", {})
    http_exception = c.get("http_exception", {})

    versions_ok = all(
        isinstance(result.get(key), str) and result[key].strip()
        for key in ("python_version", "pydantic_version", "fastapi_version", "starlette_version", "httpx_version")
    )

    required_checks = {
        # --- A 组 ---
        "union_tag_invalid_type_confirmed": _case_types(ta_cases, "unknown_tag") == ["union_tag_invalid"]
        and _case_types(raw_cases, "unknown_tag") == ["union_tag_invalid"],
        "union_tag_not_found_distinct_from_invalid": _case_types(ta_cases, "missing_tag") == ["union_tag_not_found"]
        and "union_tag_not_found" not in _case_types(ta_cases, "unknown_tag"),
        "app_error_propagates_unwrapped": (
            (ta_cases.get("validator_app_error") or {}).get("outcome") == "raised"
            and (ta_cases.get("validator_app_error") or {}).get("is_probe_app_error") is True
            and (ta_cases.get("validator_app_error") or {}).get("wrapped_in_validation_error") is False
            and (a.get("mapped_handler_state", {}).get("app_error_handler_calls") or [{}])[0].get("is_validation_error")
            is False
            and _case_status(mapped_cases, "validator_app_error") == 400
        ),
        "value_error_wrapped_with_ctx_error": _case_types(ta_cases, "validator_value_error") == ["value_error"]
        and (ta_cases["validator_value_error"].get("errors") or [{}])[0].get("ctx_error_type") == "ValueError",
        "ctx_error_not_json_serializable_recorded": (ta_cases.get("validator_value_error") or {}).get(
            "json_serializable"
        )
        is False,
        "fastapi_422_for_model_level_errors": all(
            _case_status(raw_cases, name) == 422
            for name in ("extra_field", "field_type_error", "validator_value_error")
        ),
        "handler_maps_error_types_to_400_422": _case_status(mapped_cases, "unknown_tag") == 400
        and _case_status(mapped_cases, "malformed_json") == 400
        and _case_status(mapped_cases, "extra_field") == 422
        and _case_status(mapped_cases, "field_type_error") == 422
        and _case_status(mapped_cases, "validator_value_error") == 422
        and _case_status(mapped_pipeline, "pipeline_unknown_tag_in_list") == 400
        and _case_status(mapped_pipeline, "pipeline_extra_field_in_list") == 422,
        "malformed_json_default_is_422_json_invalid": _case_status(raw_cases, "malformed_json") == 422
        and _case_types(raw_cases, "malformed_json") == ["json_invalid"]
        and isinstance(((raw_cases.get("malformed_json") or {}).get("body") or {}).get("detail", [{}])[0].get("loc", [None])[-1], int),
        "pipeline_union_loc_records_index": (raw_pipeline.get("pipeline_unknown_tag_in_list") or {}).get("body", {})
        .get("detail", [{}])[0]
        .get("loc")
        == ["body", "tasks", 0],
        "legacy_extra_ignore_control": (ignore_control.get("config_extra") is None or ignore_control.get("config_extra") == "ignore")
        and (ignore_control.get("type_adapter", {}).get("extra_field", {}) or {}).get("outcome") == "validated"
        and _case_status(ignore_control.get("fastapi", {}), "extra_field") == 200
        and _case_status(ignore_control.get("fastapi", {}), "field_type_error") == 422,
        # docs/05 §7.2：子类不重复声明 model_config，extra="forbid" 必须从基类继承下来。
        "forbid_config_inherited_by_subclass": a.get("forbid_inherited_by_subclass") is True,
        "raise_server_exceptions_default_reraises": (a.get("fastapi_raw_raise_server_exceptions_default") or {}).get(
            "raised"
        )
        is True
        and (a.get("fastapi_raw_raise_server_exceptions_default") or {}).get("is_probe_app_error") is True,
        # --- B 组 ---
        "schema_x_extensions_preserved": all(
            key in (fight.get("x_keys_by_field", {}).get("series") or [])
            for key in ("x-label", "x-group", "x-widget", "x-enum-labels", "x-risk")
        )
        # 只给 validation_alias 的字段（times）在 schema 里的 property 名是别名首选项 "Times"。
        and "x-depends-on" in (fight.get("x_keys_by_field", {}).get("Times") or [])
        and all(
            (fight.get("x_keys_by_field", {}).get(field) or []) == ["x-group", "x-label"]
            for field in ("stage", "medicine", "drops")
        ),
        "schema_object_shape_ok": fight.get("additional_properties") is False
        and fight.get("additional_properties_in_properties") is False
        and fight.get("has_defs") is False
        and sorted(fight.get("properties", [])) == sorted(["enable", "name", "stage", "series", "medicine", "drops", "DrGrandet", "Times"]),
        "schema_nullable_and_numeric_enum_ok": (fight.get("series_property", {}).get("anyOf") or [{}, {}])[1]
        == {"type": "null"}
        and (fight.get("series_property", {}).get("anyOf") or [{}])[0].get("minimum") == -1
        and (fight.get("series_property", {}).get("anyOf") or [{}])[0].get("maximum") == 6
        # Literal[0, 10000, 20000] | None：enum 落在 anyOf[0] 里（不是裸的顶层 enum）。
        and ((infrast.get("mode_property", {}).get("anyOf") or [{}])[0].get("enum")) == [0, 10000, 20000]
        and (infrast.get("mode_property", {}).get("anyOf") or [{}, {}])[1] == {"type": "null"}
        and (infrast.get("threshold_property", {}).get("anyOf") or [{}])[0].get("minimum") == 0.0
        and (infrast.get("threshold_property", {}).get("anyOf") or [{}])[0].get("maximum") == 1.0,
        "schema_defs_and_ref_shape_ok": startup.get("nested_ref") == "#/$defs/ProbeNestedSettings"
        and startup.get("defs_keys") == ["ProbeNestedSettings"]
        and (startup.get("account_name_property", {}).get("examples") or []) != [],
        "alias_schema_and_dump_keys_ok": alias.get("dr_grandet_schema_property_exists") is True
        and alias.get("times_schema_property_exists") is True
        and alias.get("times_python_property_exists") is False
        and alias.get("validation_both_spellings_ok") is True
        # 关键分叉：只有 validation_alias 的字段，schema property 是 "Times"，但 by_alias dump 仍是 "times"。
        and alias.get("dump_by_alias_exclude_none_exclude_name")
        == {"stage": "1-7", "DrGrandet": True, "times": 3}
        and alias.get("dump_by_alias_exclude_unset_exclude_name")
        == {"stage": "1-7", "DrGrandet": True, "times": 3}
        and alias.get("dump_no_alias_exclude_unset") == {"stage": "1-7", "dr_grandet": True, "times": 3}
        and "dr_grandet" in (alias.get("schema_by_alias_false_properties") or [])
        and "times" in (alias.get("schema_by_alias_false_properties") or []),
        "type_branch_introspection_ok": union.get("get_args_get_args_first_len") == 3
        and union.get("branches") == ["ProbeStartUpInput", "ProbeFightInput", "ProbeInfrastInput"]
        and union.get("name_defaults")
        == {"ProbeStartUpInput": "StartUp", "ProbeFightInput": "Fight", "ProbeInfrastInput": "Infrast"}
        and union.get("model_fields_name_default") == "Fight"
        and union.get("type_adapter_schema_top_keys") == ["$defs", "discriminator", "oneOf"]
        and (union.get("type_adapter_discriminator") or {}).get("propertyName") == "name"
        and sorted((union.get("type_adapter_discriminator") or {}).get("mapping", {}))
        == ["Fight", "Infrast", "StartUp"]
        and union.get("type_adapter_one_of_refs")
        == ["#/$defs/ProbeStartUpInput", "#/$defs/ProbeFightInput", "#/$defs/ProbeInfrastInput"]
        and union.get("per_model_has_discriminator") == {
            "ProbeStartUpInput": False,
            "ProbeFightInput": False,
            "ProbeInfrastInput": False,
        },
        "model_fields_set_three_states": states.get("missing", {}).get("model_fields_set") == ["stage"]
        and states.get("explicit_null", {}).get("model_fields_set") == ["dr_grandet", "stage"]
        and states.get("explicit_value", {}).get("model_fields_set") == ["dr_grandet", "stage"]
        and states.get("missing", {}).get("dump_exclude_unset_by_alias") == {"stage": "1-7"}
        and states.get("explicit_null", {}).get("dump_exclude_unset_by_alias") == {"stage": None, "DrGrandet": None}
        and states.get("explicit_null", {}).get("to_core_params_like") == {}
        and states.get("explicit_value", {}).get("to_core_params_like") == {"stage": "CE-6", "DrGrandet": False},
        "docs_maafield_extra_collision_confirmed": (b.get("docs_maafield_x_extra", {}) or {}).get(
            "collision_reproduced"
        )
        is True,
        # --- C 组 ---
        "redirect_slashes_disabled": redirect.get("disabled_status") == 404
        and redirect.get("no_slash_status") == 200
        and redirect.get("control_default_status") == 307
        and redirect.get("control_followed_status") == 200,
        "tags_metadata_order_preserved": openapi.get("tags_order") == [t["name"] for t in PROBE_OPENAPI_TAGS],
        "operation_id_from_route_name_and_tag": all(
            obs.get("has_name") and obs.get("has_tags") for obs in openapi.get("route_observations", [])
        )
        and openapi.get("operation_ids", {}).get("/api/probe/no-slash", {}).get("get") == "system_read_probe"
        and openapi.get("operation_ids", {}).get("/ping", {}).get("get") == "default_ping",
        "route_object_type_recorded": all(
            isinstance(obs.get("object_type"), str) and isinstance(obs.get("is_api_route_instance"), bool)
            for obs in openapi.get("route_observations", [])
        )
        # 实测：@app.get 直挂 → APIRoute；include_router 进来 → _EffectiveRouteContext 包装对象。
        and {obs["object_type"] for obs in openapi.get("route_observations", [])}
        == {"APIRoute", "_EffectiveRouteContext"}
        and {obs["is_api_route_instance"] for obs in openapi.get("route_observations", [])} == {True, False},
        "http_exception_headers_passthrough": http_exception.get("status_code") == 401
        and http_exception.get("www_authenticate") == "Bearer",
        "exception_handler_500_body_ok": c.get("exception_handler_500", {}).get("status_code") == 500
        and (c.get("exception_handler_500", {}).get("body") or {}).get("code") == "INTERNAL_ERROR"
        and (c.get("raise_server_exceptions_default") or {}).get("raised") is True,
        "testclient_lifespan_ok": lifespan.get("entered") is True
        and lifespan.get("exited") is True
        and lifespan.get("inside_request_status") == 200
        and lifespan.get("log_after_context") == ["enter", "exit"]
        and (lifespan.get("no_context_manager") or {}).get("entered") is False,
        "starlette_httpx_deprecation_recorded": len((c.get("starlette_deprecations", {}) or {}).get("import_time", []))
        >= 1
        and all(
            isinstance(item.get("message"), str) and item["message"].strip()
            for item in (c.get("starlette_deprecations", {}) or {}).get("import_time", [])
        ),
        "versions_recorded": versions_ok,
    }

    result["required_checks"] = required_checks
    result["errors"] = errors
    result["probe_ok"] = all(required_checks.values()) and not errors
    return result


# ======================================================================================
# 人读结论
# ======================================================================================


def _fmt(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _brief_errors(errors: list[dict[str, Any]] | None) -> str:
    """把错误列表压成一行：`type` + loc + ctx 键（TA 记录用 ctx_keys，HTTP 记录用 ctx）。"""
    parts = []
    for err in errors or []:
        ctx_keys = err.get("ctx_keys")
        if ctx_keys is None:
            ctx_keys = sorted((err.get("ctx") or {}))
        parts.append(f"`{err.get('type')}` loc={list(err.get('loc') or ())} ctx={sorted(ctx_keys)}")
    return "; ".join(parts) or "—"


def _case_types_from_body(rec: dict[str, Any]) -> list[str]:
    types = list(rec.get("error_types") or [])
    if not types:
        detail = (rec.get("body") or {}).get("detail")
        if isinstance(detail, list):
            types = [item.get("type") for item in detail if isinstance(item, dict)]
    return types


def _case_body_text(rec: dict[str, Any], limit: int = 160) -> str:
    if rec.get("body") is not None:
        return _fmt(rec.get("body"))[:limit]
    return _fmt(rec.get("body_text"))[:limit]


def _kv_block(mapping: dict[str, Any], keys: list[str]) -> str:
    lines = []
    for key in keys:
        lines.append(f"- `{key}` = {_fmt(mapping.get(key))}")
    return "\n".join(lines)


def build_findings_md(result: dict[str, Any]) -> str:
    a = result.get("group_a", {})
    b = result.get("group_b", {})
    c = result.get("group_c", {})
    ta_cases = a.get("type_adapter_cases", {})
    raw_cases = a.get("fastapi_raw_cases", {})
    mapped_cases = a.get("fastapi_mapped_cases", {})
    raw_pipeline = a.get("fastapi_raw_pipeline_cases", {})
    mapped_pipeline = a.get("fastapi_mapped_pipeline_cases", {})
    ignore_control = a.get("extra_ignore_control", {})
    fight = b.get("fight_schema", {})
    startup = b.get("startup_schema", {})
    infrast = b.get("infrast_schema", {})
    alias = b.get("alias", {})
    union = b.get("union_introspection", {})
    states = b.get("model_fields_set", {})
    redirect = c.get("redirect_slashes", {})
    openapi = c.get("openapi", {})
    lifespan = c.get("lifespan", {})
    http_exception = c.get("http_exception", {})
    checks = result.get("required_checks", {})

    def case_row(cases: dict[str, Any], name: str, response: bool = True) -> str:
        rec = cases.get(name) or {}
        types = ", ".join(f"`{t}`" for t in _case_types_from_body(rec)) or "—"
        if response:
            return f"| `{name}` | {rec.get('status_code')} | {types} | {_case_body_text(rec)} |"
        return f"| `{name}` | {rec.get('outcome')} | {types} | {rec.get('exception') or ''} |"

    check_lines = "\n".join(
        f"| `{name}` | {'✅' if ok else '❌'} |" for name, ok in checks.items()
    )

    md: list[str] = []
    md.append("# M3-01 方案前置实测结论：pydantic 判别联合、x-* schema 导出与最小 FastAPI 装配\n")
    md.append(
        "本文件由 `scripts/probe_api_skeleton.py` 生成，全部结论来自本机实测（脚本内定义的探针模型与探针 app，"
        "不依赖网络、内核、真实设备与仓库真实库文件）。原始数据见 `tests/fixtures/api_probe_result.json`。\n"
    )
    md.append(
        f"实测环境：Python {result.get('python_version')} / pydantic {result.get('pydantic_version')} / "
        f"fastapi {result.get('fastapi_version')} / starlette {result.get('starlette_version')} / "
        f"httpx {result.get('httpx_version')} / {result.get('platform')}\n"
    )

    md.append("## 必读结论\n")
    md.append("### 1. 400 与 422 的可编程判据（`api/errors.py` 直接照抄）\n")
    md.append(
        "判据是 `RequestValidationError.errors()` 里每一项的 **`type` 字符串**，不是状态码也不是 `msg`：\n"
    )
    md.append("| 实测 `type` | 触发情形 | 实测 FastAPI 出厂行为 | 建议 |")
    md.append("|---|---|---|---|")
    md.append(
        f"| `union_tag_invalid` | discriminator 取值不在已知任务类型内（实测 {_brief_errors((ta_cases.get('unknown_tag') or {}).get('errors'))}） "
        f"| {raw_cases.get('unknown_tag', {}).get('status_code')} | **400 `UNKNOWN_TASK_TYPE`**（docs/05 §2.2 要求） |"
    )
    md.append(
        f"| `json_invalid` | 请求体不是合法 JSON（`loc=('body', <int>)`） | {raw_cases.get('malformed_json', {}).get('status_code')} | "
        "**400 `MALFORMED_JSON`**（docs/05 §4.2 要求；**出厂行为是 422，必须特判**） |"
    )
    md.append(
        f"| `extra_forbidden` | `extra=\"forbid\"` 下的多余字段 | {raw_cases.get('extra_field', {}).get('status_code')} | 422 `VALIDATION_ERROR` |"
    )
    md.append(
        f"| `int_parsing` / `value_error` / `union_tag_not_found` / `too_short` / `list_type` | 字段类型、跨字段规则、缺 discriminator、list 长度 | "
        f"{raw_cases.get('field_type_error', {}).get('status_code')} | 422 `VALIDATION_ERROR` |"
    )
    md.append("")
    md.append(
        "**缺 discriminator 与未知 discriminator 不是同一个 `type`**：实测分别是 `union_tag_not_found` 与 "
        f"`union_tag_invalid`（见下表）。建议只把 `union_tag_invalid` 判成 400；`union_tag_not_found` 是「必填的 discriminator 缺失」，"
        "与其它缺字段同类，留在 422，前端可高亮类型选择器。\n"
    )
    md.append(
        "可直接照抄的映射函数（探针里那份就是它，`classify_validation_errors`）：\n"
    )
    md.append("```python")
    md.append("STATUS_BY_ERROR_TYPE = {")
    md.append('    "union_tag_invalid": (400, "UNKNOWN_TASK_TYPE"),')
    md.append('    "json_invalid": (400, "MALFORMED_JSON"),')
    md.append("}")
    md.append("DEFAULT_ERROR_STATUS = (422, \"VALIDATION_ERROR\")")
    md.append("")
    md.append("")
    md.append("def classify_validation_errors(errors: list[dict]) -> tuple[int, str]:")
    md.append("    for err in errors:")
    md.append('        loc = list(err.get("loc") or ())')
    md.append('        if err.get("type") == "union_tag_invalid":')
    md.append('            return 400, "UNKNOWN_TASK_TYPE"')
    md.append('        if err.get("type") == "json_invalid" and len(loc) == 2 and isinstance(loc[1], int):')
    md.append('            return 400, "MALFORMED_JSON"')
    md.append("    return 422, \"VALIDATION_ERROR\"")
    md.append("```")
    md.append("")
    md.append(
        "`json_invalid` 必须带 `loc` 形状判断：请求体本身解码失败时 `loc=('body', <int 字符偏移>)`，"
        "而字段级 JSON 解析失败是 `loc=('body', '<字段名>')` —— 只按 `type` 判会把后者的 422 误判成 400。\n"
    )
    md.append("### 2. `AppError` 的接法：**A（校验器直接抛，app 级处理器接住）**\n")
    md.append(
        "实测结论（两种路径都试过）：`model_validator` 里抛**非 ValueError 的自定义异常**时，"
        "pydantic 2.11 **不做任何包装**——`TypeAdapter.validate_python` 与 FastAPI 请求体校验都把它原样抛出：\n"
    )
    md.append(
        f"- TypeAdapter 路径：`outcome={ (ta_cases.get('validator_app_error') or {}).get('outcome') }`，"
        f"异常类型 `{(ta_cases.get('validator_app_error') or {}).get('exception')}`，"
        f"`wrapped_in_validation_error={(ta_cases.get('validator_app_error') or {}).get('wrapped_in_validation_error')}`，"
        f"随异常带出的 `extra={(ta_cases.get('validator_app_error') or {}).get('probe_app_error_extra')}` 完整保留。"
    )
    md.append(
        f"- FastAPI 路径：请求体校验函数（`fastapi/dependencies/utils.py::request_body_to_args` → "
        f"`fastapi/_compat/v2.py` → `TypeAdapter.validate_python`）只 `except ValidationError`，"
        f"自定义异常穿过路由层直达 `@app.exception_handler(ProbeAppError)`（实测 status="
        f"{mapped_cases.get('validator_app_error', {}).get('status_code')}，"
        f"响应体 {_fmt(mapped_cases.get('validator_app_error', {}).get('body'))}），"
        f"`RequestValidationError` 处理器**没有**被调用。"
    )
    md.append(
        "- 对照：`ValueError` 被包装成 `ValidationError`，`type=\"value_error\"`、`ctx` 里带 `error` 键且保留原实例"
        f"（实测 `ctx_error_type={(ta_cases.get('validator_value_error', {}).get('errors') or [{}])[0].get('ctx_error_type')}`）。"
    )
    md.append("")
    md.append(
        "**一句话结论**：走 A。`api/errors.py` 只需注册 `AppError`（普通 `Exception` 子类）与 "
        "`RequestValidationError` 两个处理器；不要为了换 400 而去 `ctx.error` 里捞 AppError——"
        "那条路（B）只在 AppError 是 `ValueError` 子类时才存在，而实测证明根本不需要把 AppError 变成 ValueError。"
    )
    md.append("")
    md.append(
        "附带一条实测坑：`ValueError` 的 `errors()` 里 `ctx.error` 是**异常实例**，"
        f"`json.dumps(exc.errors())` 直接 `TypeError`（实测 `json_serializable={(ta_cases.get('validator_value_error') or {}).get('json_serializable')}`）。"
        "处理器里必须走 `fastapi.encoders.jsonable_encoder`（它把 `ctx.error` 渲染成 `{}`）或只挑 `type/loc/msg` 三键。\n"
    )
    md.append("### 3. schema 导出（docs/05 §8.1）可直接照抄的三条\n")
    md.append(
        f"- `get_args(get_args(TaskInput)[0])` 稳定给出全部分支且**保持定义顺序**："
        f"{_fmt(union.get('branches'))}；`model_fields[\"name\"].default` 就是类型名字符串：{_fmt(union.get('name_defaults'))}。"
    )
    md.append(
        f"- `model_json_schema(ref_template=\"#/$defs/{'{model}'}\")` 只有嵌套模型时才出现顶层 `$defs`，"
        f"`$ref` 形态实测为 `{startup.get('nested_ref')}`；对象层的 `additionalProperties: false` 与 `properties` **同级**"
        f"（实测 `additionalProperties={fight.get('additional_properties')}`，`properties` 里没有它）。"
    )
    md.append(
        f"- `x-*` 原样保留：实测 `series` 上保留了 {_fmt(fight.get('x_keys_by_field', {}).get('series'))}；"
        f"只给 `validation_alias` 的字段在 schema 里会改用别名首选项做 property 名"
        f"（`times` 字段在 schema 里叫 `Times`，上面挂着 {_fmt(fight.get('x_keys_by_field', {}).get('Times'))}）。"
    )
    md.append("")
    md.append(
        f"- ⚠️ docs/05 §7.2 的 `MaaField` 写不下 `x-depends-on` / `x-risk`：它的签名没有 `json_schema_extra` 入口，"
        f"而 `**kwargs` 直接转给 `Field` 会撞车（实测 `{(b.get('docs_maafield_x_extra') or {}).get('collision_message')}`）。"
        "实现时必须给 `MaaField` 加一个额外 x-键参数（本探针叫 `x_extra`）。"
    )
    md.append("")
    md.append("### 4. `model_fields_set` 三态（docs/05 §9 的默认值注入）\n")
    md.append("| 提交 | `model_fields_set` | `model_dump(exclude_unset=True, by_alias=True, exclude={\"name\"})` | `to_core_params()`（`exclude_none=True`） |")
    md.append("|---|---|---|---|")
    for key, label in (("missing", "不传字段"), ("explicit_null", "显式传 null"), ("explicit_value", "显式传值")):
        st = states.get(key, {})
        md.append(
            f"| {label} | {_fmt(st.get('model_fields_set'))} | {_fmt(st.get('dump_exclude_unset_by_alias'))} | {_fmt(st.get('to_core_params_like'))} |"
        )
    md.append("")
    md.append(
        "三态可区分，`normalize()` 可以照 docs/05 §9 写：`\"client_type\" not in task.model_fields_set` 判定「用户完全没提」，"
        "显式 `null` 会出现在 `model_fields_set` 里但被 `to_core_params()` 的 `exclude_none=True` 剔除。\n"
    )
    md.append("### 5. 最小装配要点（docs/02 §7、docs/05 §2.5／§11）\n")
    md.append(
        f"- `redirect_slashes=False`：带尾斜杠实测 **{redirect.get('disabled_status')}**；对照默认配置 "
        f"**{redirect.get('control_default_status')}** → `{redirect.get('control_location')}`（`TestClient` 默认跟随重定向，"
        f"跟随后的状态是 {redirect.get('control_followed_status')}，所以断言必须带 `follow_redirects=False`）。"
    )
    route_types = sorted({obs.get("object_type") for obs in openapi.get("route_observations", [])})
    route_instances = sorted(
        {obs.get("is_api_route_instance") for obs in openapi.get("route_observations", [])}, key=str
    )
    md.append(
        f"- `generate_unique_id_function` 收到的 route 对象实测有 {len(openapi.get('route_observations', []))} 个、类型为 {_fmt(route_types)}"
        f"（`isinstance(route, APIRoute)` 实测取值 {_fmt(route_instances)}）：`@app.get` 直挂的路由是 `APIRoute`，"
        "`include_router` 进来的路由在 fastapi 0.141 是 `_EffectiveRouteContext` 包装对象。"
        "两者都有 `.name` / `.tags`，docs/05 §11.4 的函数体可用；但**类型注解别写 `APIRoute`**，也不要对 route 做 `isinstance` 判断。"
    )
    md.append(
        f"- `openapi_tags` 顺序原样进入 `app.openapi()[\"tags\"]`：实测 {_fmt(openapi.get('tags_order'))}；"
        f"operationId 实测 {_fmt(openapi.get('operation_ids', {}).get('/api/probe/no-slash'))}。"
    )
    md.append(
        f"- `HTTPException(401, headers={{\"WWW-Authenticate\": \"Bearer\"}})` 头透传（实测 `www-authenticate="
        f"{http_exception.get('www_authenticate')}`）—— docs/05 §2 的 401 要求可直接满足。"
    )
    md.append(
        f"- `@app.exception_handler(Exception)` + `TestClient(app, raise_server_exceptions=False)` 能拿到 500 响应体"
        f"（实测 {_fmt(c.get('exception_handler_500'))}）；`raise_server_exceptions` **默认 True 会直接把异常抛进测试**"
        f"（实测 `{(c.get('raise_server_exceptions_default') or {}).get('exception')}`），本仓的 500 测试必须显式传 False。"
    )
    md.append(
        f"- `lifespan` 在同步 `TestClient` 的 `with` 块里正常进入/退出（实测 {_fmt(lifespan.get('log_after_context'))}），"
        f"本仓不需要 `pytest-asyncio`；但**不用 context manager 时 lifespan 不执行**"
        f"（实测 {_fmt((lifespan.get('no_context_manager') or {}).get('log'))}）。"
    )
    md.append("")
    md.append("### 6. 与文档不一致 / 文档未覆盖的实测项\n")
    md.append("| # | 文档说法 | 实测 |")
    md.append("|---|---|---|")
    md.append(
        f"| 1 | docs/05 §2.2：请求体不是合法 JSON → 400 | FastAPI 出厂是 **{raw_cases.get('malformed_json', {}).get('status_code')}**，"
        f"`type=\"{(_case_types(raw_cases, 'malformed_json') or ['—'])[0]}\"`、`loc=(\"body\", <int>)`；"
        "必须在 `RequestValidationError` 处理器里按 `type` 特判成 400 `MALFORMED_JSON` |"
    )
    md.append(
        f"| 2 | docs/05 §2.2：discriminator 取值不在已知任务类型内 → 400 | 出厂是 **{raw_cases.get('unknown_tag', {}).get('status_code')}**"
        f"（`type=\"union_tag_invalid\"`）；同样需要处理器特判。文档给的是目标行为，不是框架默认行为 |"
    )
    md.append(
        "| 3 | docs/05 §7.2 的 `MaaField` 能承载 §8.2 的 `x-depends-on` | 不能，签名与 `**kwargs` 冲突（TypeError），需加参数 |"
    )
    md.append(
        f"| 4 | docs/05 §7.3：`validation_alias=AliasChoices(\"DrGrandet\", \"dr_grandet\")` 的字段「两种写法都能提交」 | 成立；"
        f"但**只给 `validation_alias` 不给 `serialization_alias`** 时 schema 属性名会变成别名首选项"
        f"（实测 `times` 的 property 名是 `Times`（`times` 已不在 properties 里），`model_dump(by_alias=True)` 却仍输出 `times`）"
        "—— 两者分叉。要一致必须同时给 `serialization_alias`（`DrGrandet` 就是这样，两处都是 `DrGrandet`） |"
    )
    md.append(
        f"| 5 | docs/02 §7：用 `lifespan` 替代 `on_event` | 实测 `on_event` 在 fastapi {result.get('fastapi_version')} 仍可用但发 FastAPI deprecation 警告；"
        "`lifespan` + 同步 `TestClient` 可用，无需 `pytest-asyncio` |"
    )
    md.append(
        f"| 6 | docs/05 §7.4：`PipelineCreate.tasks: list[TaskInput]` | 成立；联合嵌 list 时错误 `loc` 形如 "
        f"{_fmt((raw_pipeline.get('pipeline_unknown_tag_in_list', {}).get('body') or {}).get('detail', [{}])[0].get('loc'))}，"
        "`union_tag_invalid` 依旧可在任意嵌套层级被 `type` 命中 |"
    )
    md.append("")
    md.append(
        f"另：`fastapi.testclient` 在本机会发 starlette 弃用警告，原文 "
        f"{_fmt([(item.get('category'), item.get('message')) for item in (c.get('starlette_deprecations', {}) or {}).get('import_time', [])])}"
        "（`httpx 0.27.2` 尚未换 `httpx2`）。测试里若要 `filterwarnings = error` 需先处理这两条。\n"
    )

    md.append("## 1. A 组：判别联合与错误分界原始记录\n")
    md.append("### 1.1 `TypeAdapter.validate_python`（探针模型 `ProbeTaskInput`）\n")
    md.append("| 情形 | 结局 | 错误 `type` | 其余观测 |")
    md.append("|---|---|---|---|")
    for name in list(A_CASES) + ["malformed_json"]:
        rec = ta_cases.get(name) or {}
        types = ", ".join(f"`{t}`" for t in (rec.get("error_types") or [])) or "—"
        detail = {
            "loc": [(e.get("loc")) for e in (rec.get("errors") or [])],
            "ctx_keys": [(e.get("ctx_keys")) for e in (rec.get("errors") or [])],
            "ctx_error_type": [(e.get("ctx_error_type")) for e in (rec.get("errors") or [])],
            "exception": rec.get("exception"),
            "wrapped": rec.get("wrapped_in_validation_error"),
        }
        md.append(f"| `{name}` | {rec.get('outcome')} | {types} | {_fmt(detail)} |")
    md.append("")
    md.append("### 1.2 FastAPI 请求体（无自定义处理器的对照组，`raise_server_exceptions=False`）\n")
    md.append("| 情形 | 状态码 | 错误 `type` | 响应体（截断） |")
    md.append("|---|---|---|---|")
    for name in list(A_CASES) + ["malformed_json"]:
        md.append(case_row(raw_cases, name))
    md.append("")
    md.append("### 1.3 `PipelineCreate.tasks: list[TaskInput]` 嵌套路径\n")
    md.append("| 情形 | 状态码 | 错误 `type` | 响应体（截断） |")
    md.append("|---|---|---|---|")
    for name in A_PIPELINE_CASES:
        md.append(case_row(raw_pipeline, name))
    md.append("")
    md.append("### 1.4 按 `type` 映射 400/422 的处理器（推荐接法）\n")
    md.append("| 情形 | 状态码 | 响应体 |")
    md.append("|---|---|---|")
    for name in list(A_CASES) + ["malformed_json"]:
        rec = mapped_cases.get(name) or {}
        md.append(f"| `{name}` | {rec.get('status_code')} | {_fmt(rec.get('body'))[:200]} |")
    for name in A_PIPELINE_CASES:
        rec = mapped_pipeline.get(name) or {}
        md.append(f"| `{name}`（嵌套） | {rec.get('status_code')} | {_fmt(rec.get('body'))[:200]} |")
    md.append("")
    md.append("### 1.5 反向对照：`extra` 用默认 ignore\n")
    md.append(
        f"- `ProbeIgnoreAlpha.model_config[\"extra\"] = {_fmt(ignore_control.get('config_extra'))}`"
        f"（`None` 即 pydantic 默认的 ignore，未显式设置）；"
        f"多余字段实测 `outcome={(ignore_control.get('type_adapter', {}).get('extra_field', {}) or {}).get('outcome')}`、"
        f"FastAPI 状态码 {_case_status(ignore_control.get('fastapi', {}), 'extra_field')}。"
    )
    md.append(
        f"- 同组字段类型错仍然被拦：状态码 {_case_status(ignore_control.get('fastapi', {}), 'field_type_error')}。"
        "→ 前面的 422 `extra_forbidden` 确实来自 `extra=\"forbid\"`，不是别的原因。"
    )
    md.append("")
    md.append("### 1.6 validator 抛异常的两条路径对比\n")
    md.append(
        f"- `ValueError`：TypeAdapter `{_fmt(ta_cases.get('validator_value_error', {}).get('error_types'))}`"
        f"（`ctx_keys={(ta_cases.get('validator_value_error', {}).get('errors') or [{}])[0].get('ctx_keys')}`）；"
        f"FastAPI 出厂 {raw_cases.get('validator_value_error', {}).get('status_code')}。"
    )
    md.append(
        f"- 自定义异常 `ProbeAppError`（MRO：{_fmt(a.get('app_error_class', {}).get('mro_minus_self'))}，"
        f"`is_value_error_subclass={a.get('app_error_class', {}).get('is_value_error_subclass')}`）："
        f"TypeAdapter `{_fmt(ta_cases.get('validator_app_error', {}).get('outcome'))}`、"
        f"FastAPI 出厂对照 {raw_cases.get('validator_app_error', {}).get('status_code')}（无处理器时是 starlette 的裸 500），"
        f"推荐接法下 {mapped_cases.get('validator_app_error', {}).get('status_code')}。"
    )

    md.append("\n## 2. B 组：x-* 与 JSON Schema 导出原始记录\n")
    md.append("### 2.1 `ProbeFightInput.model_json_schema(ref_template=\"#/$defs/{model}\")`\n")
    md.append(_kv_block(fight, [
        "top_level_keys",
        "additional_properties",
        "additional_properties_in_properties",
        "has_defs",
        "properties",
        "x_keys_by_field",
        "x_labels_verbatim",
    ]))
    md.append("")
    md.append("`series` 字段原文：\n\n```json")
    md.append(json.dumps(fight.get("series_property"), ensure_ascii=False, indent=2, sort_keys=True))
    md.append("```\n")
    md.append("`name` 字段原文：\n\n```json")
    md.append(json.dumps(fight.get("name_property"), ensure_ascii=False, indent=2, sort_keys=True))
    md.append("```\n")
    md.append("`drops`（`dict[str, int]`）字段原文：\n\n```json")
    md.append(json.dumps(fight.get("drops_property"), ensure_ascii=False, indent=2, sort_keys=True))
    md.append("```\n")
    md.append("### 2.2 嵌套模型的 `$defs` / `$ref`\n")
    md.append(_kv_block(startup, ["top_level_keys", "defs_keys", "nested_ref"]))
    md.append("")
    md.append("### 2.3 `Literal` 数值枚举与 `ge`/`le`\n")
    md.append("`ProbeInfrastInput.mode`：\n\n```json")
    md.append(json.dumps(infrast.get("mode_property"), ensure_ascii=False, indent=2, sort_keys=True))
    md.append("```\n")
    md.append("`ProbeInfrastInput.threshold`：\n\n```json")
    md.append(json.dumps(infrast.get("threshold_property"), ensure_ascii=False, indent=2, sort_keys=True))
    md.append("```\n")
    md.append("### 2.4 别名：schema property 名与 dump 键名\n")
    md.append(_kv_block(alias, [
        "schema_property_names",
        "dr_grandet_schema_property_exists",
        "times_schema_property_exists",
        "times_python_property_exists",
        "validation_both_spellings_ok",
        "dump_by_alias_exclude_none_exclude_name",
        "dump_by_alias_exclude_unset_exclude_name",
        "dump_no_alias_exclude_unset",
        "schema_by_alias_false_properties",
    ]))
    md.append("")
    md.append("### 2.5 `TaskInput` 的联合内省与两种 schema 导出对比\n")
    md.append(_kv_block(union, [
        "get_args_get_args_first_len",
        "branches",
        "name_defaults",
        "model_fields_name_default",
        "type_adapter_schema_top_keys",
        "type_adapter_discriminator",
        "type_adapter_one_of_refs",
        "type_adapter_defs_keys",
        "type_adapter_has_top_level_properties",
        "per_model_top_keys",
        "per_model_has_discriminator",
        "default_ref_template_top_keys",
        "default_ref_template_ref",
    ]))
    md.append("")
    md.append("### 2.6 `model_fields_set` 三态\n")
    for key, label in (("missing", "字段缺失"), ("explicit_null", "显式传 null"), ("explicit_value", "显式传值")):
        md.append(f"**{label}**（`{key}`）\n")
        md.append(_kv_block(states.get(key, {}), [
            "model_fields_set",
            "dump_exclude_unset_by_alias",
            "dump_exclude_none_by_alias",
            "dump_default_by_alias",
            "to_core_params_like",
        ]))
        md.append("")
    md.append("### 2.7 `MaaField` 与 `x_extra` 逃生口\n")
    md.append(_kv_block(b.get("docs_maafield_x_extra", {}), ["path", "collision_reproduced", "collision_message"]))
    md.append("")
    md.append("### 2.8 `model_validator` 跨字段规则（`ProbeInfrastInput`）\n")
    md.append("```json")
    md.append(json.dumps(b.get("cross_field_validator"), ensure_ascii=False, indent=2, sort_keys=True)[:1200])
    md.append("```\n")

    md.append("\n## 3. C 组：最小 FastAPI 装配原始记录\n")
    md.append("### 3.1 尾斜杠\n")
    md.append(_kv_block(redirect, [
        "disabled_status",
        "no_slash_status",
        "control_default_status",
        "control_location",
        "control_followed_status",
    ]))
    md.append("")
    md.append("### 3.2 operationId 与 route 对象\n")
    md.append(
        f"route 对象实测（每条路由一条，共 {len(openapi.get('route_observations', []))} 条，"
        f"类型集合 {_fmt(sorted({obs.get('object_type') for obs in openapi.get('route_observations', [])}))}）：\n\n```json"
    )
    md.append(json.dumps(openapi.get("route_observations"), ensure_ascii=False, indent=2, sort_keys=True))
    md.append("```\n")
    md.append(f"operationId 实测：{_fmt(openapi.get('operation_ids'))}\n")
    md.append("### 3.3 tag 顺序\n")
    md.append(f"`app.openapi()[\"tags\"]` 顺序：{_fmt(openapi.get('tags_order'))}\n")
    md.append("### 3.4 HTTPException 与异常处理器\n")
    md.append(_kv_block(http_exception, ["status_code", "www_authenticate", "body"]))
    md.append("")
    md.append(_kv_block(c.get("exception_handler_500", {}), ["status_code", "body"]))
    md.append("")
    md.append(_kv_block(c.get("raise_server_exceptions_default", {}), ["raised", "exception", "message"]))
    md.append("")
    md.append("### 3.5 lifespan 与 deprecation 警告\n")
    md.append(_kv_block(lifespan, [
        "log_inside_context",
        "log_after_context",
        "entered",
        "exited",
        "inside_request_status",
        "no_context_manager",
        "warnings_during_context",
    ]))
    md.append("")
    md.append("import 期捕获到的 starlette/httpx 弃用警告原文：\n\n```json")
    md.append(json.dumps((c.get("starlette_deprecations", {}) or {}).get("import_time"), ensure_ascii=False, indent=2, sort_keys=True))
    md.append("```\n")
    md.append("使用期捕获：\n\n```json")
    md.append(json.dumps((c.get("starlette_deprecations", {}) or {}).get("during_use"), ensure_ascii=False, indent=2, sort_keys=True))
    md.append("```\n")

    md.append("\n## 4. required_checks 一览\n")
    md.append("| check | 结果 |")
    md.append("|---|---|")
    md.append(check_lines)
    md.append("")
    if result.get("errors"):
        md.append("## 5. 探针自身错误\n")
        for item in result["errors"]:
            md.append(f"```\n{item}\n```\n")
    md.append("## 6. 复现方式\n")
    md.append("```bash")
    md.append(".venv/bin/python scripts/probe_api_skeleton.py")
    md.append("```\n")
    md.append(
        "脚本内定义了全部探针模型与探针 app，不 import `maa_api`，不访问网络/内核/真实设备/仓库库文件；"
        "重跑会覆盖 `tests/fixtures/api_probe_result.json` 与 `tests/fixtures/api_probe_findings.md`。"
    )
    return "\n".join(md) + "\n"


# ======================================================================================
# main
# ======================================================================================


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="probe_api_skeleton.py",
        description=(
            "M3-01 方案前置实测：pydantic v2 判别联合与 x-* schema 导出、FastAPI 400/422 分界、"
            "最小装配行为。全部实验在脚本内部完成，不 import maa_api，不触碰仓库真实数据。"
        ),
        epilog="产物默认写入 tests/fixtures/api_probe_result.json 与 tests/fixtures/api_probe_findings.md。",
    )
    parser.add_argument(
        "--out-json",
        default=str(DEFAULT_OUT_JSON),
        help="实测结果 JSON 路径（默认 tests/fixtures/api_probe_result.json）",
    )
    parser.add_argument(
        "--out-md",
        default=str(DEFAULT_OUT_MD),
        help="人读结论 Markdown 路径（默认 tests/fixtures/api_probe_findings.md）",
    )
    args = parser.parse_args(argv)

    out_json = Path(args.out_json)
    out_md = Path(args.out_md)
    if not out_json.is_absolute():
        out_json = REPO_ROOT / out_json
    if not out_md.is_absolute():
        out_md = REPO_ROOT / out_md

    result = run_all_probes()

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    out_md.write_text(build_findings_md(result), encoding="utf-8")
    print(f"[probe] wrote {out_json}", flush=True)
    print(f"[probe] wrote {out_md}", flush=True)

    if result.get("errors"):
        for item in result["errors"]:
            print(f"[probe] ERROR {item}", file=sys.stderr, flush=True)

    checks = result.get("required_checks", {})
    failed = [name for name, ok in checks.items() if not ok]
    print(f"[probe] required_checks: {len(checks)} 条，通过 {len(checks) - len(failed)} 条", flush=True)
    if failed:
        print(f"[probe] FAILED required checks: {failed}", file=sys.stderr, flush=True)
        return 1
    print("PROBE OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
