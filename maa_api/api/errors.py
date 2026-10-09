"""统一错误体、全局异常处理器与 ``error_responses()`` 辅助（docs/05 §1–§3、§11.2）。

非 2xx 响应的 body 只有一种形状：``{"error": {"code", "message", "details"}}``。
``code`` 取 :class:`~maa_api.domain.errors.ErrorCode` 成员名，``message`` 是中文，
``details`` 为 ``None`` 时省略整个键。未捕获异常只把 ``uuid4().hex`` 写进
``details.trace_id``，堆栈由 :func:`logging.Logger.exception` 写进服务日志。

:func:`register_exception_handlers` 注册四个处理器：

============================  ====================================================
异常                           行为
============================  ====================================================
``AppError``                  ``exc.http_status`` + 统一体；``exc.headers`` 原样
                              透传（429/503 的 ``Retry-After`` 由此发出），401 补
                              ``WWW-Authenticate: Bearer``（docs/05 §2）
``RequestValidationError``    按 ``errors()[i]["type"]`` 分 400 / 422；``details.fields``
                              是 ``exc.errors()`` 的原生字段级列表（经
                              ``jsonable_encoder``，因为 ``ctx.error`` 是异常实例）
``StarletteHTTPException``    按状态码映射到表内通用码后输出统一体，保留原状态码与
                              原 ``headers``；映射表外的状态码保持框架默认响应
``Exception``                 500 ``INTERNAL_ERROR``，``details`` 只有 ``trace_id``
============================  ====================================================

400 / 422 的分界看 ``errors()[i]["type"]``：``union_tag_invalid``（未知 discriminator）
与 ``json_invalid`` 且 ``loc`` 形如 ``("body", <int>)``（请求体本身解码失败）是 400，
其余是 422；字段级 JSON 解析失败的 ``loc`` 是 ``("body", "<字段名>")``，留在 422。
``model_validator`` 里抛 ``AppError`` 不做包装，直达 ``AppError`` 处理器。

307 由 ``RedirectResponse`` 产生，不经过异常处理器。:func:`error_responses()` 按
:data:`ERROR_RESPONSE_HEADERS` 把每个码可能带的响应头写进 OpenAPI 的 ``headers``，
只描述文档。
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
    "ERROR_RESPONSE_HEADERS",
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
    """统一错误体里的 ``error`` 对象。

    ``code`` 是错误码枚举成员名，``message`` 可直接展示给用户，``details`` 可选。
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
    值（pydantic 2.11 的行为），因此 ``{"a": None}`` 这样的结构化上下文保持原样。
    """
    return ErrorBody(error=detail).model_dump(mode="json", exclude_none=True)


def _error_response(
    detail: ErrorDetail,
    *,
    status_code: int,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """统一体的唯一出口：所有处理器都从这里构造响应。

    401 补 ``WWW-Authenticate: Bearer``（docs/05 §2），已有该头时不覆盖。
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
    """按）。

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
        return (
            f"未知的任务类型：{tag}，可选值见 GET /api/tasks/types"
            if tag
            else "未知的任务类型，可选值见 GET /api/tasks/types"
        )
    if code is ErrorCode.MALFORMED_JSON:
        return "请求体不是合法 JSON，请检查 Content-Type 与 JSON 格式"
    return f"请求参数校验失败（{len(fields)} 处），请对照 details.fields 修正后重试"


# ----------------------------------------------------------------------
# 四个处理器
# ----------------------------------------------------------------------


async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """``AppError`` → ``exc.http_status`` + 统一体 + ``exc.headers``（接法 A）。

    ``headers`` 走 :func:`_error_response` 的同一条合并逻辑：401 补
    ``WWW-Authenticate: Bearer``，其余头原样透传。错误体只由 code/message/details
    决定，与头无关。``message`` 省略（``None`` 或空串）时回落到状态码的中文说明，
    与 ``_http_exception_handler`` 用同一份 :data:`_STATUS_MESSAGE`，不会出现英文码
    当 message 的情况。
    """
    return _error_response(
        ErrorDetail(
            code=exc.code,
            message=exc.message or _status_message(exc.http_status),
            details=exc.details,
        ),
        status_code=exc.http_status,
        headers=exc.headers,
    )


async def _validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """``RequestValidationError`` → 400 / 422 统一体，``details.fields`` 带原生列表。"""
    # ctx.error 是异常实例（value_error 情形），json.dumps 会 TypeError；jsonable_encoder
    # 会把它渲染成 {}，并保证 loc/input 里的非 JSON 值可序列化。
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
        # 抛错方给了自定义 detail（本仓消息均为中文）时直接当 message，不改写。
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
            message="服务端内部错误，请稍后重试；详情见日志页",
            details={"trace_id": trace_id},
        ),
        status_code=500,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """注册四类异常处理器（AppError / 校验错误 / HTTPException / 兜底 Exception）。

    处理器都是模块级函数，``add_exception_handler`` 只是字典赋值，因此这里
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


#: ``Retry-After`` 的 OpenAPI 描述。取值统一是整数秒：鉴权限流 429 用剩余冷却秒数
#: （``api/deps.py`` 的 ``math.ceil``），因此同一状态码下不会出现第二种口径
#: （HTTP-date 形式本 API 不使用）。
_RETRY_AFTER_SPEC: dict[str, Any] = {
    "description": "建议的重试等待秒数（整数）",
    "schema": {"type": "integer", "minimum": 0},
}

#: 会带 ``Retry-After`` 的错误码：docs/05 §2 点名的 429 三类，加 503 里"依赖暂时
#: 不可用、稍后可重试"的那些（内核未就绪/重启中/已崩溃、设备未连接、LLM 未配置、
#: 服务启动或关闭中）。同为 503 但重试无意义的码（``MAP_LEVEL_KEY_UNAVAILABLE``
#: 缺符号、``DEVICE_RESOLUTION_UNSUPPORTED`` 硬件不支持、``AGENT_DISABLED``
#: 模块未启用）刻意不在其中：它们要改配置或换设备，不是"等一等再来"。
_RETRY_AFTER_CODES: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.RATE_LIMITED,
        ErrorCode.QUEUE_FULL,
        ErrorCode.LLM_RATE_LIMITED,
        ErrorCode.SERVICE_UNAVAILABLE,
        ErrorCode.CORE_NOT_READY,
        ErrorCode.CORE_RESTARTING,
        ErrorCode.CORE_CRASHED,
        ErrorCode.DEVICE_NOT_CONNECTED,
        ErrorCode.LLM_NOT_CONFIGURED,
    }
)

#: 错误码 → 该码的响应可能带的头（OpenAPI ``headers`` 对象）。
#: **只用于文档生成**：运行时响应头由抛错方经 ``AppError.headers`` 决定，本表不
#: 自动补头。新增带头的错误码时改这一张表，:func:`error_responses()` 全同步。
ERROR_RESPONSE_HEADERS: dict[ErrorCode, dict[str, dict[str, Any]]] = {
    code: {"Retry-After": _RETRY_AFTER_SPEC} for code in sorted(_RETRY_AFTER_CODES)
}


def _merged_response_headers(codes: Sequence[ErrorCode]) -> dict[str, dict[str, Any]]:
    """同一状态码下多个错误码声明的头取并集（OpenAPI 的 ``responses`` 按状态码分桶）。

    同名头的声明必须一致（例如 429 的三个码共用同一份 ``Retry-After`` 规格）；
    不一致说明 :data:`ERROR_RESPONSE_HEADERS` 写错了，fail loud 而不是悄悄取第一个。
    """
    merged: dict[str, dict[str, Any]] = {}
    for code in codes:
        for name, spec in ERROR_RESPONSE_HEADERS.get(code, {}).items():
            if name in merged and merged[name] != spec:
                raise ValueError(
                    f"错误码 {code!r} 对响应头 {name!r} 的声明与同状态码下其它码冲突"
                )
            merged[name] = dict(spec)
    return merged


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
    :returns: ``{状态码: {"description": ..., ["headers": ...], "content":
        {"application/json": {"example": {"error": {...}}}}}}``，按状态码升序；
        同一状态码内保持传入顺序。``headers`` 只在桶里至少一个码登记在
        :data:`ERROR_RESPONSE_HEADERS` 时出现，描述该状态码可能带的响应头
        （如 429/503 的 ``Retry-After``）；没有登记头的状态码条目形状完全
        一致。

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

    responses: dict[int, dict[str, Any]] = {}
    for status_code, bucket in sorted(grouped.items()):
        entry: dict[str, Any] = {
            "description": (
                description
                if description is not None
                else _default_description(status_code, bucket)
            )
        }
        headers = _merged_response_headers(bucket)
        if headers:
            entry["headers"] = headers
        entry["content"] = {
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
        }
        responses[status_code] = entry
    return responses
