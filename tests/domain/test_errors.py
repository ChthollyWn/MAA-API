"""内核层最小错误码契约（M1-03，docs/05 §4.4）。

未登记的错误码按 **fail loud** 处理：``AppError`` 在构造时查 ``ERROR_HTTP_STATUS``，
查不到就抛 ``KeyError``，不返回"默认 500"。选抛异常而不是默认值，是因为未登记属于
编码缺陷，应当在与错误码定义同一次改动里暴露，而不是等线上把一个本该是 503 的
场景伪装成 500。
"""

import json
import pickle
from enum import StrEnum

import pytest

from maa_api.domain.errors import ERROR_HTTP_STATUS, AppError, ErrorCode

# docs/05 §4.4 内核状态表：错误码 → 固定 HTTP 状态码。
KERNEL_HTTP_STATUS = {
    ErrorCode.CORE_NOT_READY: 503,
    ErrorCode.CORE_RESTARTING: 503,
    ErrorCode.CORE_CRASHED: 503,
    ErrorCode.CORE_START_FAILED: 500,
    ErrorCode.CORE_COMMAND_FAILED: 502,
    ErrorCode.CORE_COMMAND_TIMEOUT: 504,
}


class _UnregisteredCode(StrEnum):
    """模拟 M3 才会登记的错误码，用来验证查表 fail loud。"""

    NOT_IN_TABLE = "NOT_IN_TABLE"


# ----------------------------------------------------------------------
# ErrorCode
# ----------------------------------------------------------------------

def test_error_code_is_strenum_with_name_equal_value():
    assert issubclass(ErrorCode, StrEnum)
    assert issubclass(ErrorCode, str)
    assert all(c.name == c.value for c in ErrorCode)


def test_only_kernel_codes_are_defined_in_this_card():
    """M1-03 只登记内核层 6 条；其余错误码归 M3，不要提前抄全表。"""
    assert {c.name for c in ErrorCode} == {
        "CORE_NOT_READY",
        "CORE_RESTARTING",
        "CORE_CRASHED",
        "CORE_START_FAILED",
        "CORE_COMMAND_FAILED",
        "CORE_COMMAND_TIMEOUT",
    }


def test_error_code_serializes_as_plain_string():
    """响应体里的 error.code 是字符串（ADR-04），JSON 序列化不能带枚举包装。"""
    assert json.dumps({"code": ErrorCode.CORE_CRASHED}) == '{"code": "CORE_CRASHED"}'


# ----------------------------------------------------------------------
# ERROR_HTTP_STATUS
# ----------------------------------------------------------------------

def test_http_status_table_is_complete_and_matches_docs():
    assert set(ERROR_HTTP_STATUS) == set(ErrorCode)
    assert dict(ERROR_HTTP_STATUS) == KERNEL_HTTP_STATUS


@pytest.mark.parametrize(
    ("code", "status"),
    list(KERNEL_HTTP_STATUS.items()),
    ids=[c.name for c in KERNEL_HTTP_STATUS],
)
def test_http_status_per_kernel_code(code, status):
    assert ERROR_HTTP_STATUS[code] == status
    assert AppError(code, "x").http_status == status


def test_http_status_table_is_lookupable_by_plain_string():
    """StrEnum 成员与同值 str 的 hash/eq 相同，M3 组装错误体时可直接用字符串查表。"""
    assert ERROR_HTTP_STATUS["CORE_NOT_READY"] == 503
    assert ERROR_HTTP_STATUS["CORE_COMMAND_TIMEOUT"] == 504


# ----------------------------------------------------------------------
# AppError
# ----------------------------------------------------------------------

def test_app_error_exposes_code_message_details_and_http_status():
    err = AppError(
        ErrorCode.CORE_NOT_READY, "starting", {"state": "STARTING"}
    )
    assert err.code is ErrorCode.CORE_NOT_READY
    assert err.message == "starting"
    assert err.details == {"state": "STARTING"}
    assert err.http_status == 503


def test_app_error_details_defaults_to_none():
    err = AppError(ErrorCode.CORE_CRASHED, "boom")
    assert err.details is None
    assert err.http_status == 503


def test_app_error_str_is_code_colon_message():
    err = AppError(ErrorCode.CORE_START_FAILED, "restart budget exhausted")
    assert str(err) == "CORE_START_FAILED: restart budget exhausted"
    assert err.message in str(err) and err.code in str(err)


def test_app_error_accepts_keyword_arguments():
    err = AppError(
        code=ErrorCode.CORE_COMMAND_FAILED,
        message="AsstAppendTask returned 0",
        details={"cmd": "APPEND_TASK"},
    )
    assert err.http_status == 502
    assert err.details == {"cmd": "APPEND_TASK"}


def test_app_error_is_raisable_and_catchable():
    with pytest.raises(AppError) as excinfo:
        raise AppError(ErrorCode.CORE_COMMAND_TIMEOUT, "no CMD_RESULT in 30s")
    assert excinfo.value.code is ErrorCode.CORE_COMMAND_TIMEOUT
    assert excinfo.value.http_status == 504
    assert isinstance(excinfo.value, Exception)


def test_app_error_pickle_roundtrip():
    """异常会进 IPC / 队列，序列化往返必须保真（只持有 str/dict/枚举）。"""
    original = AppError(
        ErrorCode.CORE_COMMAND_FAILED, "append failed", {"cmd": "APPEND_TASK"}
    )
    restored = pickle.loads(pickle.dumps(original))
    assert type(restored) is AppError
    assert restored.code is ErrorCode.CORE_COMMAND_FAILED
    assert restored.message == "append failed"
    assert restored.details == {"cmd": "APPEND_TASK"}
    assert restored.http_status == 502
    assert str(restored) == str(original)


# ----------------------------------------------------------------------
# fail loud
# ----------------------------------------------------------------------

def test_unregistered_error_code_fails_loud():
    """未登记的错误码：抛 KeyError，不返回默认状态码。"""
    with pytest.raises(KeyError, match="未登记"):
        AppError(_UnregisteredCode.NOT_IN_TABLE, "boom")


def test_unknown_string_code_fails_loud():
    with pytest.raises(KeyError, match="未登记"):
        AppError("NOT_A_REAL_CODE", "boom")
