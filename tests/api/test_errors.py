"""统一错误体与四个异常处理器（M3-04；docs/05 §1–§3、§11.2，ADR-04）。

被测 app 全部在本文件里现搭（``FastAPI()`` + :func:`register_exception_handlers`），
不 import ``maa_api.main``（M3-09 之前它还是旧装配）；判别联合也用本文件内定义的小
模型（StartUp / Fight / Risky 三个分支），不 import ``maa_api.domain.task``（M3-05）。

``RiskyTask`` 的 ``model_validator`` 同时覆盖两条校验路径，用来钉住 M3-01 的实测结论：

- ``ValueError`` → 被 pydantic 包装成 ``type="value_error"`` → 422 ``VALIDATION_ERROR``
- ``AppError``（非 ``ValueError`` 子类）→ 原样传播，直达 app 级处理器 → 接法 A

M3-11 追加 ``AppError.headers`` 的覆盖：带头的抛法、不带头的回归、429 与 503 的
``Retry-After`` 真的到达客户端、401 头的合并语义，以及 ``error_responses()`` 把
``Retry-After`` 声明进 OpenAPI。
"""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

import maa_api.settings as settings_module
from maa_api.api import errors as errors_module
from maa_api.api.errors import (
    ErrorBody,
    ErrorDetail,
    error_responses,
    register_exception_handlers,
)
from maa_api.db import session as db_session
from maa_api.domain.errors import ERROR_HTTP_STATUS, AppError, ErrorCode

#: 仓库根：tests/api/test_errors.py → parents[2]。不依赖 CWD。
REPO_ROOT = Path(__file__).resolve().parents[2]
_CJK = re.compile(r"[\u4e00-\u9fff]")
_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")


# ----------------------------------------------------------------------
# 本文件内的小模型（判别联合 2~3 个分支）
# ----------------------------------------------------------------------


class StartUpTask(BaseModel):
    name: Literal["StartUp"] = "StartUp"
    client_type: Literal["Official", "Bilibili"] = "Official"


class FightTask(BaseModel):
    name: Literal["Fight"] = "Fight"
    stage: str
    times: int = Field(default=1, ge=1, le=99)


class RiskyTask(BaseModel):
    """第三个分支：同一模型里对照 ``ValueError``（422）与 ``AppError``（接法 A）。"""

    name: Literal["Risky"] = "Risky"
    stone: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _check(self) -> "RiskyTask":
        if self.stone > 10:
            raise ValueError("stone 最多 10")
        if self.stone > 0:
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                "源石数量必须为 0",
                {"stone": self.stone},
            )
        return self


TaskInput = Annotated[StartUpTask | FightTask | RiskyTask, Field(discriminator="name")]


# ----------------------------------------------------------------------
# 被测 app
# ----------------------------------------------------------------------


def _add_routes(app: FastAPI) -> None:
    @app.get("/app-error/{code}")
    async def raise_app_error(
        code: str, message: str = "示例消息", details: str = "none"
    ) -> None:
        payload: dict[str, Any] | None = None
        if details == "empty":
            payload = {}
        elif details == "nested-none":
            payload = {"a": None}
        elif details != "none":
            payload = {"hint": details}
        raise AppError(ErrorCode(code), message, payload)

    @app.get("/app-error-headers/{code}")
    async def raise_app_error_with_headers(
        code: str,
        message: str | None = None,
        retry_after: str = "30",
        header: str = "Retry-After",
    ) -> None:
        """M3-11 的响应头通道；``details`` 固定为 None 以便对照错误体形状。"""
        raise AppError(ErrorCode(code), message, None, {header: retry_after})

    @app.post("/tasks")
    async def submit_task(task: TaskInput) -> dict[str, str]:
        return {"name": task.name}

    @app.get("/http-error/{status_code}")
    async def raise_http(status_code: int, detail: str | None = None) -> None:
        raise HTTPException(status_code=status_code, detail=detail)

    @app.get("/kaboom")
    async def kaboom() -> None:
        raise RuntimeError("kaboom at /secret/do-not-leak.py")

    @app.get(
        "/documented",
        responses=error_responses("PIPELINE_NOT_FOUND", "QUEUE_FULL", "UNAUTHORIZED"),
    )
    async def documented() -> dict[str, str]:
        return {"ok": "yes"}


def build_app(*, register: bool = True) -> FastAPI:
    """现搭一个装了统一处理器的 app；``register=False`` 供幂等性用例对照。"""
    app = FastAPI()
    if register:
        register_exception_handlers(app)
    _add_routes(app)
    return app


# ----------------------------------------------------------------------
# AppError → 统一体
# ----------------------------------------------------------------------

APP_ERROR_CASES = [
    ("UNAUTHORIZED", 401),
    ("FORBIDDEN", 403),
    ("PIPELINE_NOT_FOUND", 404),
    ("PIPELINE_ALREADY_RUNNING", 409),
    ("TASK_PARAM_INVALID", 422),
    ("QUEUE_FULL", 429),
    ("INTERNAL_ERROR", 500),
    ("CORE_NOT_READY", 503),
]


@pytest.mark.parametrize(("code", "status"), APP_ERROR_CASES)
def test_app_error_unified_body(make_client, code: str, status: int) -> None:
    client = make_client(build_app())
    resp = client.get(
        f"/app-error/{code}", params={"message": "出问题了", "details": "filled"}
    )
    assert resp.status_code == status
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == {
        "error": {"code": code, "message": "出问题了", "details": {"hint": "filled"}}
    }


def test_error_code_is_serialized_as_plain_string(make_client) -> None:
    """钉住 M3-02 的实测坑：``ErrorCode`` 的 repr 是 ``<ErrorCode.X: 'X'>``，线上必须是裸值。"""
    client = make_client(build_app())
    resp = client.get("/app-error/PIPELINE_NOT_FOUND", params={"message": "流水线不存在"})
    assert '"code":"PIPELINE_NOT_FOUND"' in resp.text
    assert "ErrorCode" not in resp.text


def test_app_error_omits_details_when_none(make_client) -> None:
    client = make_client(build_app())
    body = client.get(
        "/app-error/PIPELINE_NOT_FOUND", params={"message": "流水线不存在"}
    ).json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}


def test_app_error_keeps_empty_and_nested_none_details(make_client) -> None:
    client = make_client(build_app())
    empty = client.get("/app-error/PIPELINE_NOT_FOUND", params={"details": "empty"}).json()
    assert empty["error"]["details"] == {}
    nested = client.get(
        "/app-error/PIPELINE_NOT_FOUND", params={"details": "nested-none"}
    ).json()
    # details 里的 None 值不能被 exclude_none 递归抹掉（它不是"没有上下文"）。
    assert nested["error"]["details"] == {"a": None}


def test_app_error_401_sets_www_authenticate(make_client) -> None:
    client = make_client(build_app())
    resp = client.get("/app-error/UNAUTHORIZED", params={"message": "token 无效"})
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


def test_error_detail_model_coerces_code_and_rejects_unknown() -> None:
    detail = ErrorDetail(code="PIPELINE_NOT_FOUND", message="流水线不存在")
    assert detail.code is ErrorCode.PIPELINE_NOT_FOUND
    assert detail.details is None
    assert ErrorBody(error=detail).error.message == "流水线不存在"
    # 表外的码 fail loud，不被当成字符串发出去（处理器不自造码）。
    with pytest.raises(ValidationError):
        ErrorDetail(code="NOT_A_REAL_CODE", message="x")


# ----------------------------------------------------------------------
# AppError 的响应头通道（M3-11；docs/05 §2 的 Retry-After）
# ----------------------------------------------------------------------


def test_app_error_headers_are_emitted(make_client) -> None:
    """带 headers 的 AppError：头真的进响应，错误体形状一个字节都不变。"""
    client = make_client(build_app())
    resp = client.get(
        "/app-error-headers/RATE_LIMITED",
        params={"message": "带响应头", "retry_after": "42"},
    )
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "42"
    assert resp.json() == {"error": {"code": "RATE_LIMITED", "message": "带响应头"}}


def test_app_error_without_message_falls_back_to_status_text(make_client) -> None:
    """``message`` 可省略（验收命令的构造形式）：兜底成状态码的中文说明，不是英文码。"""
    client = make_client(build_app())
    resp = client.get("/app-error-headers/QUEUE_FULL", params={"retry_after": "9"})
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "9"
    err = resp.json()["error"]
    assert err["code"] == "QUEUE_FULL"
    assert _CJK.search(err["message"])


def test_app_error_without_headers_emits_none(make_client) -> None:
    """回归：普通 AppError（不带 headers）不会凭空多出任何响应头。"""
    client = make_client(build_app())
    resp = client.get("/app-error/QUEUE_FULL", params={"message": "队列已满"})
    assert resp.status_code == 429
    assert "retry-after" not in resp.headers
    assert resp.json() == {"error": {"code": "QUEUE_FULL", "message": "队列已满"}}


@pytest.mark.parametrize(
    ("code", "status", "retry_after"),
    [
        ("RATE_LIMITED", 429, "42"),
        ("QUEUE_FULL", 429, "30"),
        ("LLM_RATE_LIMITED", 429, "5"),
        ("SERVICE_UNAVAILABLE", 503, "10"),
        ("CORE_NOT_READY", 503, "3"),
        ("DEVICE_NOT_CONNECTED", 503, "15"),
    ],
)
def test_retry_after_reaches_client_for_429_and_503(
    make_client, code: str, status: int, retry_after: str
) -> None:
    """docs/05 §2：429 与 503 的 Retry-After 都要真的发得出去（整数秒）。"""
    client = make_client(build_app())
    resp = client.get(f"/app-error-headers/{code}", params={"retry_after": retry_after})
    assert resp.status_code == status
    assert resp.headers["retry-after"] == retry_after
    assert resp.headers["retry-after"].isdigit()
    assert resp.json()["error"]["code"] == code


def test_app_error_401_keeps_explicit_www_authenticate(make_client) -> None:
    """401 兜底用 setdefault：调用方显式给的头优先，缺失时才补 Bearer。"""
    client = make_client(build_app())
    override = client.get(
        "/app-error-headers/UNAUTHORIZED",
        params={"header": "WWW-Authenticate", "retry_after": "Bearer realm=maa"},
    )
    assert override.status_code == 401
    assert override.headers["www-authenticate"] == "Bearer realm=maa"

    defaulted = client.get(
        "/app-error-headers/UNAUTHORIZED",
        params={"header": "X-Other", "retry_after": "1"},
    )
    assert defaulted.headers["www-authenticate"] == "Bearer"
    assert defaulted.headers["x-other"] == "1"


# ----------------------------------------------------------------------
# 校验错误：400 / 422 分界（依据 api_probe_findings.md §1）
# ----------------------------------------------------------------------


def test_validation_error_is_422_with_native_fields(make_client) -> None:
    client = make_client(build_app())
    resp = client.post("/tasks", json={"name": "Fight", "stage": "1-7", "times": "abc"})
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert _CJK.search(err["message"])
    fields = err["details"]["fields"]
    assert isinstance(fields, list) and len(fields) == 1
    assert fields[0]["type"] == "int_parsing"
    assert fields[0]["loc"][0] == "body" and fields[0]["loc"][-1] == "times"
    assert "msg" in fields[0]


def test_unknown_discriminator_is_400_unknown_task_type(make_client) -> None:
    client = make_client(build_app())
    resp = client.post("/tasks", json={"name": "Nope"})
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "UNKNOWN_TASK_TYPE"
    assert _CJK.search(err["message"]) and "Nope" in err["message"]
    types = [field["type"] for field in err["details"]["fields"]]
    assert "union_tag_invalid" in types


def test_missing_discriminator_stays_422(make_client) -> None:
    client = make_client(build_app())
    resp = client.post("/tasks", json={"stage": "1-7"})
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    types = [field["type"] for field in err["details"]["fields"]]
    assert "union_tag_not_found" in types


def test_malformed_json_is_400_malformed_json(make_client) -> None:
    client = make_client(build_app())
    resp = client.post(
        "/tasks",
        content=b'{"name": "Fight",}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "MALFORMED_JSON"
    assert _CJK.search(err["message"])
    types = [field["type"] for field in err["details"]["fields"]]
    assert "json_invalid" in types


def test_value_error_in_validator_stays_422(make_client) -> None:
    """普通 ``ValueError`` 会被包装成 ``value_error``；``ctx.error`` 是异常实例，
    处理器必须经 ``jsonable_encoder`` 才能序列化（M3-01 实测坑）。"""
    client = make_client(build_app())
    resp = client.post("/tasks", json={"name": "Risky", "stone": 11})
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "VALIDATION_ERROR"
    assert err["details"]["fields"][0]["type"] == "value_error"
    assert "stone 最多 10" in err["details"]["fields"][0]["msg"]


def test_model_validator_app_error_reaches_app_error_handler(make_client) -> None:
    """接法 A：``model_validator`` 抛 ``AppError`` 原样传播，不经过校验错误处理器。"""
    client = make_client(build_app())
    resp = client.post("/tasks", json={"name": "Risky", "stone": 3})
    assert resp.status_code == 422  # TASK_PARAM_INVALID 绑定 422
    err = resp.json()["error"]
    assert err["code"] == "TASK_PARAM_INVALID"
    assert err["message"] == "源石数量必须为 0"
    assert err["details"] == {"stone": 3}
    # 走的是 AppError 处理器而不是 RequestValidationError：details 里没有 fields。
    assert "fields" not in err["details"]


# ----------------------------------------------------------------------
# StarletteHTTPException → 表内通用码
# ----------------------------------------------------------------------

HTTP_EXCEPTION_CASES = [
    (400, "INVALID_PARAMETER"),
    (401, "UNAUTHORIZED"),
    (403, "FORBIDDEN"),
    (404, "NOT_FOUND"),
    (410, "ENDPOINT_REMOVED"),
    (422, "VALIDATION_ERROR"),
    (429, "RATE_LIMITED"),
    (500, "INTERNAL_ERROR"),
    (503, "SERVICE_UNAVAILABLE"),
]


@pytest.mark.parametrize(("status", "code"), HTTP_EXCEPTION_CASES)
def test_http_exception_maps_to_table_code(
    make_client, status: int, code: str
) -> None:
    client = make_client(build_app())
    resp = client.get(f"/http-error/{status}")
    assert resp.status_code == status
    err = resp.json()["error"]
    assert err["code"] == code
    assert _CJK.search(err["message"])
    assert "details" not in err


def test_http_exception_401_sets_www_authenticate(make_client) -> None:
    client = make_client(build_app())
    resp = client.get("/http-error/401")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


def test_http_exception_custom_detail_becomes_message(make_client) -> None:
    client = make_client(build_app())
    resp = client.get("/http-error/404", params={"detail": "流水线不存在"})
    assert resp.status_code == 404
    assert resp.json() == {"error": {"code": "NOT_FOUND", "message": "流水线不存在"}}


def test_unmatched_path_is_unified_not_found(make_client) -> None:
    client = make_client(build_app())
    resp = client.get("/no-such-path")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_405_keeps_framework_default_response(make_client) -> None:
    """docs/05 §2.5：405 由 Starlette 产生、表内没有对应码，保持框架默认响应。"""
    client = make_client(build_app())
    resp = client.post("/app-error/UNAUTHORIZED")  # 该路由只注册了 GET
    assert resp.status_code == 405
    assert resp.json() == {"detail": "Method Not Allowed"}
    assert resp.headers["allow"] == "GET"


def test_http_status_map_uses_only_table_codes() -> None:
    """映射表里一个自造码都不许有，且每个码的领域绑定必须等于该状态码。"""
    assert set(errors_module._HTTP_STATUS_CODE) == {
        400,
        401,
        403,
        404,
        410,
        422,
        429,
        500,
        503,
    }
    for status, code in errors_module._HTTP_STATUS_CODE.items():
        assert isinstance(code, ErrorCode)
        assert ERROR_HTTP_STATUS[code] == status


# ----------------------------------------------------------------------
# 兜底 Exception → 500
# ----------------------------------------------------------------------


def test_unhandled_exception_is_500_with_trace_id_only(make_client, caplog) -> None:
    client = make_client(build_app())
    with caplog.at_level(logging.ERROR, logger="maa_api.api.errors"):
        resp = client.get("/kaboom")
    assert resp.status_code == 500
    err = resp.json()["error"]
    assert err["code"] == "INTERNAL_ERROR"
    assert _CJK.search(err["message"])
    assert set(err["details"]) == {"trace_id"}
    assert _TRACE_ID.match(err["details"]["trace_id"])

    # 绝不返回堆栈、异常文本、文件路径（docs/05 §1、§4.3）。
    raw = resp.text
    for leak in ("Traceback", "kaboom", "do-not-leak.py", "errors.py", str(REPO_ROOT)):
        assert leak not in raw

    # 堆栈只进日志：logger.exception 带着 exc_info。
    records = [record for record in caplog.records if record.name == "maa_api.api.errors"]
    assert len(records) == 1
    assert records[0].exc_info is not None
    assert records[0].exc_info[0] is RuntimeError


def test_trace_id_is_unique_per_failure(make_client) -> None:
    client = make_client(build_app())
    first = client.get("/kaboom").json()["error"]["details"]["trace_id"]
    second = client.get("/kaboom").json()["error"]["details"]["trace_id"]
    assert first != second


# ----------------------------------------------------------------------
# error_responses()
# ----------------------------------------------------------------------


def test_error_responses_groups_by_status() -> None:
    responses = error_responses("PIPELINE_NOT_FOUND", "QUEUE_FULL", "UNAUTHORIZED")
    assert sorted(responses) == [401, 404, 429]
    assert set(responses[404]) == {"description", "content"}
    assert set(responses[404]["content"]) == {"application/json"}
    assert responses[404]["description"].startswith("资源不存在")
    assert "PIPELINE_NOT_FOUND" in responses[404]["description"]
    example = responses[404]["content"]["application/json"]["example"]
    assert example["error"]["code"] == "PIPELINE_NOT_FOUND"
    assert _CJK.search(example["error"]["message"])
    assert example["error"]["details"] == {}


def test_error_responses_accepts_enum_dedupes_and_sorts() -> None:
    responses = error_responses(
        ErrorCode.PIPELINE_NOT_FOUND, "PIPELINE_NOT_FOUND", "TASK_NOT_FOUND"
    )
    assert sorted(responses) == [404]
    assert responses[404]["description"].count("PIPELINE_NOT_FOUND") == 1
    assert "TASK_NOT_FOUND" in responses[404]["description"]
    assert error_responses("CORE_NOT_READY", "QUEUE_FULL").keys() == {429, 503}


def test_error_responses_custom_description() -> None:
    responses = error_responses("NOT_FOUND", description="资源不存在或已被清理")
    assert responses[404]["description"] == "资源不存在或已被清理"


def test_error_responses_rejects_codes_outside_enum() -> None:
    with pytest.raises(ValueError):
        error_responses("NOT_A_REAL_CODE")
    # UPDATE_INTERRUPTED 在枚举内但没有 HTTP 表达（只落库），同样 fail loud。
    with pytest.raises(ValueError):
        error_responses("UPDATE_INTERRUPTED")


def test_error_responses_declares_retry_after_headers() -> None:
    """M3-11：429/503 的 Retry-After 进 OpenAPI 的 headers（整数秒口径）。"""
    responses = error_responses("QUEUE_FULL", "CORE_NOT_READY", "PIPELINE_NOT_FOUND")
    assert set(responses[429]) == {"description", "headers", "content"}
    spec = responses[429]["headers"]["Retry-After"]
    assert spec["schema"] == {"type": "integer", "minimum": 0}
    assert spec["description"]
    assert "Retry-After" in responses[503]["headers"]
    # 没登记头的状态码条目形状与 M3-04 完全一致，不凭空长出 headers 键。
    assert set(responses[404]) == {"description", "content"}


def test_error_responses_merges_headers_within_one_status_bucket() -> None:
    """429 的三个码都声明 Retry-After：同状态码下取并集，不重复、不冲突。"""
    responses = error_responses("RATE_LIMITED", "QUEUE_FULL", "LLM_RATE_LIMITED")
    assert set(responses[429]["headers"]) == {"Retry-After"}


def test_error_responses_skips_retry_after_for_non_retryable_503() -> None:
    """503 里重试无意义的码（缺符号 / 硬件不支持 / 模块未启用）不声明 Retry-After。"""
    responses = error_responses(
        "MAP_LEVEL_KEY_UNAVAILABLE", "DEVICE_RESOLUTION_UNSUPPORTED", "AGENT_DISABLED"
    )
    assert set(responses[503]) == {"description", "content"}


def test_response_headers_table_keys_are_table_codes() -> None:
    """声明表只允许 docs/05 §4 内的码，且都有 HTTP 表达（否则 error_responses 会炸）。"""
    for code in errors_module.ERROR_RESPONSE_HEADERS:
        assert isinstance(code, ErrorCode)
        assert code in ERROR_HTTP_STATUS


def test_error_responses_land_in_openapi(make_client) -> None:
    client = make_client(build_app())
    spec = client.get("/openapi.json").json()
    responses = spec["paths"]["/documented"]["get"]["responses"]
    assert responses["404"]["content"]["application/json"]["example"] == {
        "error": {"code": "PIPELINE_NOT_FOUND", "message": "资源不存在", "details": {}}
    }
    assert set(responses) >= {"401", "404", "429"}
    # 声明的头要真的出现在 OpenAPI 里（/documented 传了 QUEUE_FULL）。
    assert responses["429"]["headers"]["Retry-After"]["schema"]["type"] == "integer"


# ----------------------------------------------------------------------
# 处理器注册
# ----------------------------------------------------------------------


def test_register_exception_handlers_is_idempotent(make_client) -> None:
    app = FastAPI()
    register_exception_handlers(app)
    keys = (AppError, RequestValidationError, StarletteHTTPException, Exception)
    first = {key: app.exception_handlers[key] for key in keys}
    register_exception_handlers(app)
    assert {key: app.exception_handlers[key] for key in keys} == first

    _add_routes(app)
    client = make_client(app)
    assert client.get("/app-error/PIPELINE_NOT_FOUND").json()["error"]["code"] == (
        "PIPELINE_NOT_FOUND"
    )
    assert client.get("/no-such-path").json()["error"]["code"] == "NOT_FOUND"


# ----------------------------------------------------------------------
# tests/api 公共夹具自身
# ----------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _tmp_settings_cache_restored():
    """``tmp_settings`` 的清理承诺：模块跑完后进程内缓存必须还原为 ``None``。

    模块级 finalizer 在所有函数级夹具（含 tmp_settings）之后执行，所以这里读到的
    是清理后的状态。
    """
    yield
    assert settings_module._CACHE is None


def test_tmp_settings_default_is_no_auth(tmp_settings, tmp_path) -> None:
    assert tmp_settings.access_token == ""  # 空串＝免鉴权（docs/05 §5.2）
    assert settings_module.get_settings() is tmp_settings
    assert settings_module.DEFAULT_CONFIG_PATH == tmp_path / "config.yaml"
    assert tmp_settings.adb.address == "127.0.0.1:5555"


@pytest.mark.parametrize("tmp_settings", ["s3cret"], indirect=True)
def test_tmp_settings_token_is_parametrizable(tmp_settings, tmp_path) -> None:
    assert tmp_settings.access_token == "s3cret"
    assert settings_module.get_settings().access_token == "s3cret"
    assert "s3cret" in (tmp_path / "config.yaml").read_text(encoding="utf8")


def test_isolated_db_rebuilds_engine_and_factory(isolated_db, tmp_path) -> None:
    assert db_session.DB_PATH == tmp_path / "maa_api.db"
    assert str(tmp_path) in db_session.ASYNC_URL
    assert "resource" not in db_session.ASYNC_URL
    assert db_session.engine is isolated_db
    assert db_session.session_factory.kw["bind"] is isolated_db


def test_isolated_db_connects_only_to_tmp_file(isolated_db, tmp_path) -> None:
    async def scenario() -> None:
        async with db_session.engine.connect() as conn:
            assert (await conn.execute(text("select 1"))).scalar() == 1
            # 与生产同一个 make_engine，六个 PRAGMA 应已挂上。
            assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar() == 1

    asyncio.run(scenario())
    assert (tmp_path / "maa_api.db").exists()


def test_make_client_does_not_reraise_server_errors(make_client) -> None:
    client = make_client(build_app())
    assert client.get("/kaboom").status_code == 500  # 不抛 RuntimeError


def test_make_client_accepts_testclient_kwargs(make_client) -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/token")
    async def read_token(request: Request) -> dict[str, str | None]:
        return {"token": request.headers.get("x-token")}

    client = make_client(app, headers={"X-Token": "s3cret"})
    assert client.get("/token").json() == {"token": "s3cret"}


def test_make_client_enters_lifespan(make_client) -> None:
    events: list[str] = []

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        events.append("enter")
        yield
        events.append("exit")

    app = FastAPI(lifespan=lifespan)
    register_exception_handlers(app)
    client = make_client(app)
    assert events == ["enter"]  # 不用 with 块时 lifespan 不执行（M3-01 实测）
    assert client.get("/openapi.json").status_code == 200
