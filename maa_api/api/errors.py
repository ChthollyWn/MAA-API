"""统一错误体、全局异常处理器与 ``error_responses()`` 辅助（docs/05 §1–§3、§11.2）。

设计要点
========

**错误体只有一种形状**（docs/05 §1）：任何非 2xx 响应的 body 都是

.. code-block:: json

    {"error": {"code": "PIPELINE_NOT_FOUND", "message": "流水线不存在", "details": {...}}}

``code`` 是可枚举的大写下划线字符串（:class:`~maa_api.domain.errors.ErrorCode`
的成员名），这是 ADR-04 选自定义结构的核心价值 —— agent 据此做确定性分支；
``message`` 是中文，直接展示给用户；``details`` 可选，为 ``None`` 时**整个键省略**
（docs/05 §1 写明 details 可选），非 ``None`` 时里面的 ``None`` 值保持原样。
处理器**不自造错误码**、也不把码降级成字符串常量表：状态码一律查
:data:`~maa_api.domain.errors.ERROR_HTTP_STATUS`。

**不返回堆栈**（docs/05 §1、§4.3）：未捕获异常只把 ``uuid4().hex`` 写进
``details.trace_id``，堆栈由 :func:`logging.Logger.exception` 写进服务日志。

四个处理器（:func:`register_exception_handlers`）
================================================

============================  ====================================================
异常                           行为
============================  ====================================================
``AppError``                  ``exc.http_status`` + 统一体；401 补
                              ``WWW-Authenticate: Bearer``（docs/05 §2）
``RequestValidationError``    按下面的实测判据分 400 / 422；``details.fields`` 是
                              ``exc.errors()`` 的原生字段级列表（经
                              ``jsonable_encoder``，因为 ``ctx.error`` 是异常实例）
``StarletteHTTPException``    按状态码映射到表内通用码后输出统一体，保留原状态码与
                              原 ``headers``；映射表外的状态码**保持框架默认响应**
``Exception``                 500 ``INTERNAL_ERROR``，``details`` 只有 ``trace_id``
============================  ====================================================

400 与 422 的分界（依据 M3-01 实测，见 tests/fixtures/api_probe_findings.md §1）
=============================================================================

判据是 ``RequestValidationError.errors()[i]["type"]`` 字符串，不是状态码也不是 ``msg``：

- ``union_tag_invalid`` → 400 ``UNKNOWN_TASK_TYPE``（discriminated union 的
  discriminator 取值不在已知任务类型内）。FastAPI 出厂对它是 422，必须特判。
- ``json_invalid`` 且 ``loc`` 形如 ``("body", <int>)`` → 400 ``MALFORMED_JSON``
  （请求体本身解码失败）。**字段级** JSON 解析失败的 ``loc`` 是
  ``("body", "<字段名>")``，留在 422，所以必须带 loc 形状判断。
- 其余（``union_tag_not_found`` / ``extra_forbidden`` / ``int_parsing`` /
  ``value_error`` / ``too_short`` / ``list_type`` …）→ 422 ``VALIDATION_ERROR``。

缺 discriminator（``union_tag_not_found``）与未知 discriminator
（``union_tag_invalid``）是**两个不同的 type**，前者留在 422。

``AppError`` 的接法：**A（model_validator 直接抛，app 级处理器接住）**。pydantic 2.11
实测：``model_validator`` 里抛非 ``ValueError`` 的自定义异常不做任何包装，
``TypeAdapter.validate_python`` 与 FastAPI 请求体校验都原样抛出，直达
``@app.exception_handler(AppError)``；只有 ``ValueError`` 会被包装成
``type="value_error"`` 的 ``ValidationError``。因此本模块**不需要**从
``RequestValidationError.errors()[i]["ctx"]["error"]`` 里往回捞 AppError（接法 B）。

例外：405 与 307（docs/05 §2.5）
================================

``405 Method Not Allowed`` 与 ``307 Temporary Redirect``（尾斜杠重定向）由 Starlette
产生，**不进错误码表**：表内没有 405 的码，所以本模块对映射表外的状态码原样交给
``fastapi.exception_handlers.http_exception_handler``，保持框架默认的
``{"detail": ...}`` 响应；307 是 ``RedirectResponse``，根本不经过异常处理器。
映射表内必须有码的状态码是 404 / 400 / 401 / 403 / 410 / 422 / 429 / 500 / 503。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from http import HTTPStatus
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import (
    http_exception_handler as _default_http_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from maa_api.domain.errors import ERROR_HTTP_STATUS, AppError, ErrorCode

__all__ = [
    "ErrorBody",
    "ErrorDetail",
    "classify_validation_errors",
    "error_responses",
    "register_exception_handlers",
]

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 统一错误体
# ----------------------------------------------------------------------


class ErrorDetail(BaseModel):
    """统一错误体里的 ``error`` 对象（docs/05 §1）。

    ``code`` 用 :class:`~maa_api.domain.errors.ErrorCode` 校验：表外的码在这里
    fail loud（pydantic ``ValidationError``），而不是被当成字符串发出去。
    """

    code: ErrorCode
    message: str
    details: dict[str, Any] | None = None


class ErrorBody(BaseModel):
    """任何非 2xx 响应的 body：``{"error": {...}}``。"""

    error: ErrorDetail


def _dump_error_body(detail: ErrorDetail) -> dict[str, Any]:
    """把 :class:`ErrorDetail` 渲染成响应体 dict；``details is None`` 时省略该键。

    ``exclude_none=True`` 只作用于模型字段，不会递归删掉 ``details`` 里的 ``None``
    值（pydantic 2.11 实测），因此 ``{"a": None}`` 这样的结构化上下文保持原样。
    """
    return ErrorBody(error=detail).model_dump(mode="json", exclude_none=True)


def _error_response(
    detail: ErrorDetail,
    *,
    status_code: int,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """统一体的唯一出口：所有处理器都从这里构造响应。

    401 一律补 ``WWW-Authenticate: Bearer``（docs/05 §2），已有该头时不覆盖。
    """
    merged = dict(headers or {})
    if status_code == 401:
        merged.setdefault("WWW-Authenticate", "Bearer")
    return JSONResponse(
        status_code=status_code,
        content=_dump_error_body(detail),
        headers=merged or None,
    )


# ----------------------------------------------------------------------
# 状态码 → 中文说明 / 表内通用码
# ----------------------------------------------------------------------

#: HTTP 状态码 → 中文说明。用于 ``error_responses()`` 的 description 与
#: ``StarletteHTTPException`` 兜底时的 message（默认 detail 是英文，不能直接给用户看）。
_STATUS_MESSAGE: dict[int, str] = {
    200: "成功",
    201: "已创建",
    202: "已受理",
    204: "无内容",
    400: "请求不成立",
    401: "token 缺失或不匹配",
    403: "身份有效但动作被拒绝",
    404: "资源不存在",
    405: "方法不允许",
    409: "与当前状态冲突",
    410: "资源已永久移除",
    422: "参数校验失败",
    429: "请求过于频繁",
    500: "服务端内部错误",
    502: "下游依赖失败",
    503: "服务暂时不可用",
    504: "下游依赖超时",
}

#: 取不到具体说明时的兜底文案。
_FALLBACK_MESSAGE = "请求失败"

#: ``StarletteHTTPException`` 的状态码 → 表内通用码（全部是 docs/05 §4 已有的码，
#: 一个都不新增）。**405 刻意不在表内**：docs/05 §2.5 规定它由 Starlette 产生、
#: 不进错误码表，处理器对它保持框架默认响应。
_HTTP_STATUS_CODE: dict[int, ErrorCode] = {
    400: ErrorCode.INVALID_PARAMETER,
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    410: ErrorCode.ENDPOINT_REMOVED,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMITED,
    500: ErrorCode.INTERNAL_ERROR,
    503: ErrorCode.SERVICE_UNAVAILABLE,
}


def _status_message(status_code: int) -> str:
    """状态码的中文说明；未知状态码回落到通用文案。"""
    return _STATUS_MESSAGE.get(status_code, _FALLBACK_MESSAGE)


def _default_phrase(status_code: int) -> str | None:
    """Starlette 给 ``HTTPException`` 填的英文默认 detail（``HTTPStatus.phrase``）。"""
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:  # 非标准状态码（如 499）
        return None


# ----------------------------------------------------------------------
# 校验错误：400 / 422 分界
# ----------------------------------------------------------------------


def classify_validation_errors(
    errors: Sequence[Mapping[str, Any]],
) -> tuple[int, ErrorCode]:
    """按 M3-01 实测的 ``type`` 判据给校验错误分类（详见模块文档字符串）。

    :param errors: ``RequestValidationError.errors()``（或经 ``jsonable_encoder``
        后的同形列表）。
    :returns: ``(http_status, error_code)``。默认 422 ``VALIDATION_ERROR``。
    """
    for err in errors:
        loc = list(err.get("loc") or ())
        if err.get("type") == "union_tag_invalid":
            return 400, ErrorCode.UNKNOWN_TASK_TYPE
        # 必须带 loc 形状判断：请求体本身解码失败是 ("body", <int>)，
        # 字段级 JSON 解析失败是 ("body", "<字段名>")，后者留在 422。
        if err.get("type") == "json_invalid" and len(loc) == 2 and isinstance(loc[1], int):
            return 400, ErrorCode.MALFORMED_JSON
    return 422, ErrorCode.VALIDATION_ERROR


def _first_ctx_value(
    errors: Sequence[Mapping[str, Any]], error_type: str, key: str
) -> Any:
    """取第一条指定 ``type`` 的错误里 ``ctx[key]`` 的值（用于把未知类型名写进 message）。"""
    for err in errors:
        if err.get("type") == error_type:
            ctx = err.get("ctx")
            if isinstance(ctx, Mapping):
                return ctx.get(key)
    return None


def _validation_message(code: ErrorCode, fields: Sequence[Mapping[str, Any]]) -> str:
    """校验错误的中文 message（原生英文 msg 留在 details.fields 里）。"""
    if code is ErrorCode.UNKNOWN_TASK_TYPE:
        tag = _first_ctx_value(fields, "union_tag_invalid", "tag")
        return f"未知的任务类型：{tag}" if tag else "未知的任务类型"
    if code is ErrorCode.MALFORMED_JSON:
        return "请求体不是合法 JSON"
    return f"请求参数校验失败（{len(fields)} 处）"


# ----------------------------------------------------------------------
# 四个处理器
# ----------------------------------------------------------------------


async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """``AppError`` → ``exc.http_status`` + 统一体（接法 A，见模块文档字符串）。"""
    return _error_response(
        ErrorDetail(code=exc.code, message=exc.message, details=exc.details),
        status_code=exc.http_status,
    )


async def _validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """``RequestValidationError`` → 400 / 422 统一体，``details.fields`` 带原生列表。"""
    # ctx.error 是异常实例（value_error 情形），json.dumps 会 TypeError；jsonable_encoder
    # 会把它渲染成 {}（M3-01 实测），并保证 loc/input 里的非 JSON 值可序列化。
    fields = jsonable_encoder(exc.errors())
    status_code, code = classify_validation_errors(fields)
    return _error_response(
        ErrorDetail(
            code=code,
            message=_validation_message(code, fields),
            details={"fields": fields},
        ),
        status_code=status_code,
    )


async def _http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> Response:
    """``StarletteHTTPException`` → 表内通用码的统一体，保留原状态码与 headers。

    表外状态码（如 405）交给 FastAPI 的默认处理器，保持 ``{"detail": ...}``
    形状（docs/05 §2.5）。
    """
    code = _HTTP_STATUS_CODE.get(exc.status_code)
    if code is None:
        return await _default_http_exception_handler(request, exc)

    detail = exc.detail
    if isinstance(detail, str) and detail and detail != _default_phrase(exc.status_code):
        # 抛错方给了自定义 detail（本仓一律中文），直接当 message，信息不丢。
        message = detail
        details = None
    else:
        message = _status_message(exc.status_code)
        # detail 不是字符串时（少见，如 dict/list），原样放进 details 便于排查。
        details = None if isinstance(detail, str) else {"detail": detail}
    return _error_response(
        ErrorDetail(code=code, message=message, details=details),
        status_code=exc.status_code,
        headers=exc.headers,
    )


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底：500 ``INTERNAL_ERROR``，``details`` 只有 ``trace_id``，堆栈只进日志。"""
    trace_id = uuid4().hex
    logger.exception("未捕获异常 trace_id=%s", trace_id)
    return _error_response(
        ErrorDetail(
            code=ErrorCode.INTERNAL_ERROR,
            message="服务端内部错误",
            details={"trace_id": trace_id},
        ),
        status_code=500,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """注册四类异常处理器（AppError / 校验错误 / HTTPException / 兜底 Exception）。

    处理器都是模块级函数，``add_exception_handler`` 只是字典赋值，因此本函数
    **可重复调用**：第二次注册覆盖的是同一批函数对象，行为幂等、不会叠加包装。
    """
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)


# ----------------------------------------------------------------------
# OpenAPI responses 辅助
# ----------------------------------------------------------------------


def _default_description(status_code: int, codes: Sequence[ErrorCode]) -> str:
    """默认 description：中文说明 + 该状态码下的全部错误码（docs/05 §11.2）。"""
    return f"{_status_message(status_code)}：{'、'.join(str(code) for code in codes)}"


def error_responses(
    *codes: str | ErrorCode,
    description: str | None = None,
) -> dict[int, dict[str, Any]]:
    """从错误码枚举生成路由装饰器的 ``responses=``（docs/05 §11.2）。

    :param codes: 错误码，接受 ``ErrorCode`` 成员或等值字符串；**表外的码 fail
        loud**（``ValueError``），不静默生成一个不存在的码。重复的码只保留一次。
    :param description: 自定义 description；``None`` 时按状态码生成
        ``"资源不存在：PIPELINE_NOT_FOUND"`` 形态。多个状态码分组共用同一段文本
        （通常一次只传同一状态码的码）。
    :returns: ``{状态码: {"description": ..., "content": {"application/json":
        {"example": {"error": {...}}}}}}``，按状态码升序；同一状态码内保持传入顺序。

    用法见 docs/05 §11.2::

        @router.post("/api/pipelines", responses=error_responses(
            "PIPELINE_EMPTY", "UNKNOWN_TASK_TYPE", "QUEUE_FULL",
        ))
    """
    grouped: dict[int, list[ErrorCode]] = {}
    for raw in codes:
        try:
            code = ErrorCode(raw)
        except ValueError:
            raise ValueError(
                f"error_responses() 收到表外错误码：{raw!r}（只能在 docs/05 §4 的枚举内取值）"
            ) from None
        try:
            status_code = ERROR_HTTP_STATUS[code]
        except KeyError:
            # UPDATE_INTERRUPTED 只落库、不对应任何 HTTP 响应，不能进 responses=。
            raise ValueError(
                f"error_responses() 收到没有 HTTP 表达的码：{code!r}"
                "（UPDATE_INTERRUPTED 只作为 update_record.error_code 落库）"
            ) from None
        bucket = grouped.setdefault(status_code, [])
        if code not in bucket:
            bucket.append(code)

    return {
        status_code: {
            "description": (
                description
                if description is not None
                else _default_description(status_code, bucket)
            ),
            "content": {
                "application/json": {
                    # 示例带上 details（哪怕是空对象），让文档读者看到统一体的完整形状；
                    # 真实响应在 details is None 时会省略该键。
                    "example": _dump_error_body(
                        ErrorDetail(
                            code=bucket[0],
                            message=_status_message(status_code),
                            details={},
                        )
                    )
                }
            },
        }
        for status_code, bucket in sorted(grouped.items())
    }
