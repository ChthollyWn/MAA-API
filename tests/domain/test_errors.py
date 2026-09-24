"""全量错误码契约（M3-02，docs/05 §4.1–§4.15 十五张表）。

三组断言：

1. **形状**：``ErrorCode`` 是 StrEnum 且成员名 == 取值；全量 94 条；``ERROR_HTTP_STATUS``
   93 条且 ``UPDATE_INTERRUPTED`` 是唯一没有 HTTP 表达的例外。M1-03 建立的内核层 6 条
   取值与状态码在这一卡里零改动（``KERNEL_HTTP_STATUS`` 是那 6 条的原始硬编码）。
2. **文档一致性**：重新解析 docs/05 §4 的十五张表（路径从本文件推导，不依赖 CWD），
   断言 code 集合与状态码映射和代码完全一致、HTTP 列为 ``—`` 的只有 ``UPDATE_INTERRUPTED``。
   文档改了而代码没跟上时这条会红 —— 这是本卡「表外的码一个都不许加、表内的码一个都不许漏」
   的机械门禁。
3. **``AppError`` 既有契约**：构造签名（位置/关键字）、``__str__``/``__repr__``、
   未登记码 fail loud（抛 ``KeyError``，不返回"默认 500"）、可 pickle、``error.code``
   序列化成纯字符串（ADR-04）；M3-11 追加可选的 ``headers`` 通道（默认 None、
   复制、进 pickle 往返、非 str / 非 latin-1 值 fail loud）。

选抛异常而不是默认值，是因为未登记属于编码缺陷，应当在与错误码定义同一次改动里暴露，
而不是等线上把一个本该是 503 的场景伪装成 500。
"""

import ast
import json
import pickle
import re
from enum import StrEnum
from pathlib import Path

import pytest

from maa_api.domain.errors import ERROR_HTTP_STATUS, AppError, ErrorCode

# ----------------------------------------------------------------------
# 常量与文档解析
# ----------------------------------------------------------------------

#: 仓库根：tests/domain/test_errors.py → parents[2]。不依赖 CWD。
REPO_ROOT = Path(__file__).resolve().parents[2]
ERRORS_MODULE_PATH = REPO_ROOT / "maa_api" / "domain" / "errors.py"
API_SPEC_PATH = REPO_ROOT / "docs" / "05-API规范与路由清单.md"

#: docs/05 §4 的十五条分节标题形如 ``### 4.7 流水线与任务``。
_DOC_SECTION_RE = re.compile(r"^### 4\.(\d+) (.+)$")
#: 表格数据行形如 ``| `CODE` | 404 | 含义…… |``；表头与文字段落都不匹配。
_DOC_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
#: ``UPDATE_INTERRUPTED`` 的 HTTP 列。
_DOC_NO_HTTP = "—"
#: 模块里枚举成员的写法：``    CODE = "CODE"``。
_SOURCE_MEMBER_RE = re.compile(r'^\s*([A-Z][A-Z0-9_]*) = "\1"\s*$')
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

#: 总数与绑定数（docs/05 §4 正文：「共 94 条，其中 UPDATE_INTERRUPTED 只落库不返回」）。
ALL_CODE_COUNT = 94
HTTP_BOUND_CODE_COUNT = 93
SECTION_COUNT = 15

#: M1-03 内核层 6 条的原始绑定，本卡一个不动。
KERNEL_HTTP_STATUS = {
    "CORE_NOT_READY": 503,
    "CORE_RESTARTING": 503,
    "CORE_CRASHED": 503,
    "CORE_START_FAILED": 500,
    "CORE_COMMAND_FAILED": 502,
    "CORE_COMMAND_TIMEOUT": 504,
}

#: 唯一只落库、不占 HTTP 表达的错误码。
NO_HTTP_CODE = "UPDATE_INTERRUPTED"


def parse_docs_error_tables() -> list[tuple[str, str, str]]:
    """解析 docs/05 §4 的十五张表，返回 ``[(code, http_cell, section), ...]``。

    只看 ``## 4. 错误码表`` 到下一个 ``## `` 之间的 ``### 4.x`` 小节里的三列表格行：
    表头（``错误码``）与分隔行（``---|---|---``）都不匹配 ``| `CODE` |`` 形态。
    """
    rows: list[tuple[str, str, str]] = []
    in_section = False
    section = ""
    for line in API_SPEC_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            in_section = line.startswith("## 4. 错误码表")
            continue
        if not in_section:
            continue
        matched = _DOC_SECTION_RE.match(line)
        if matched:
            section = matched.group(0).strip()
            continue
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        code = cells[0].strip("`")
        if not _DOC_CODE_RE.fullmatch(code):
            continue
        rows.append((code, cells[1], section))
    return rows


DOC_ROWS = parse_docs_error_tables()
DOC_HTTP_STATUS = {
    code: int(http) for code, http, _ in DOC_ROWS if http != _DOC_NO_HTTP
}


class _UnregisteredCode(StrEnum):
    """未登记在 docs/05 §4 里的错误码，用来验证查表 fail loud。"""

    NOT_IN_TABLE = "NOT_IN_TABLE"


# ----------------------------------------------------------------------
# ErrorCode：形状与条数
# ----------------------------------------------------------------------

def test_error_code_is_strenum_with_name_equal_value():
    assert issubclass(ErrorCode, StrEnum)
    assert issubclass(ErrorCode, str)
    assert all(c.name == c.value for c in ErrorCode)


def test_all_94_documented_codes_are_defined():
    """docs/05 §4 共 94 条：表外的码一个都不许加，表内的码一个都不许漏。"""
    assert len(list(ErrorCode)) == ALL_CODE_COUNT
    assert len({c.value for c in ErrorCode}) == ALL_CODE_COUNT


def test_kernel_codes_are_unchanged_from_this_card():
    """M1-03 的内核层 6 条取值与状态码在本卡零改动（只扩充、不改形）。"""
    for name, status in KERNEL_HTTP_STATUS.items():
        member = getattr(ErrorCode, name)
        assert member.value == name
        assert ERROR_HTTP_STATUS[member] == status


def test_error_code_serializes_as_plain_string():
    """响应体里的 error.code 是字符串（ADR-04），JSON 序列化不能带枚举包装。"""
    assert json.dumps({"code": ErrorCode.CORE_CRASHED}) == '{"code": "CORE_CRASHED"}'
    assert json.dumps({"code": ErrorCode.LLM_TIMEOUT}) == '{"code": "LLM_TIMEOUT"}'


def test_every_code_has_a_chinese_meaning_comment():
    """每条错误码上方必须有一行中文注释：后续里程碑直接按码取用，不能只写码不写含义。"""
    source_lines = ERRORS_MODULE_PATH.read_text(encoding="utf-8").splitlines()
    members = [
        node
        for node in ast.walk(ast.parse("\n".join(source_lines)))
        if isinstance(node, ast.ClassDef) and node.name == "ErrorCode"
    ]
    assert len(members) == 1
    code_names = [
        target.id
        for node in members[0].body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    ]
    assert len(code_names) == ALL_CODE_COUNT
    missing = []
    for lineno in (
        node.lineno
        for node in members[0].body
        if isinstance(node, ast.Assign)
    ):
        previous = source_lines[lineno - 2]  # lineno 是 1-based；上一行即其前一行
        if not (previous.strip().startswith("#") and _CJK_RE.search(previous)):
            missing.append((source_lines[lineno - 1], previous))
    assert not missing, missing


# ----------------------------------------------------------------------
# ERROR_HTTP_STATUS：固定绑定
# ----------------------------------------------------------------------

def test_http_status_table_covers_every_code_except_the_documented_exception():
    assert len(ERROR_HTTP_STATUS) == HTTP_BOUND_CODE_COUNT
    assert set(ERROR_HTTP_STATUS) == set(ErrorCode) - {ErrorCode.UPDATE_INTERRUPTED}


def test_only_update_interrupted_has_no_http_status():
    """UPDATE_INTERRUPTED 只落库不返回，是 94 条里唯一的例外。"""
    assert ErrorCode.UPDATE_INTERRUPTED.value == NO_HTTP_CODE
    assert ErrorCode.UPDATE_INTERRUPTED not in ERROR_HTTP_STATUS
    no_http = {code for code, http, _ in DOC_ROWS if http == _DOC_NO_HTTP}
    assert no_http == {NO_HTTP_CODE}
    assert set(DOC_HTTP_STATUS) == {c.value for c in ErrorCode} - {NO_HTTP_CODE}


@pytest.mark.parametrize(
    ("code", "status"),
    list(KERNEL_HTTP_STATUS.items()),
    ids=list(KERNEL_HTTP_STATUS),
)
def test_http_status_per_kernel_code(code, status):
    member = ErrorCode(code)
    assert ERROR_HTTP_STATUS[member] == status
    assert AppError(member, "x").http_status == status


def test_http_status_table_is_lookupable_by_plain_string():
    """M3-04 组装错误体时可直接用字符串查表。"""
    assert ERROR_HTTP_STATUS["CORE_NOT_READY"] == 503
    assert ERROR_HTTP_STATUS["CORE_COMMAND_TIMEOUT"] == 504
    assert ERROR_HTTP_STATUS["CONFIRMATION_REQUIRED"] == 202


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("CONFIRMATION_REQUIRED", 202),  # §4.10 唯一出现在 2xx 里的码
        ("UNAUTHORIZED", 401),
        ("FORBIDDEN", 403),
        ("RATE_LIMITED", 429),
        ("ENDPOINT_REMOVED", 410),
        ("MALFORMED_JSON", 400),
        ("VALIDATION_ERROR", 422),
        ("NOT_FOUND", 404),
        ("SERVICE_UNAVAILABLE", 503),
        ("QUEUE_FULL", 429),
        ("SETTING_READONLY", 403),
        ("CUSTOM_TASK_INVALID", 422),
        ("GAME_INSTALL_FAILED", 502),
        # docs/13 §3：判断依据是「有没有收到答复」而不是「失败得有多严重」。
        ("CORE_COMMAND_TIMEOUT", 504),
        ("LLM_TIMEOUT", 504),
        ("UPDATE_DOWNLOAD_FAILED", 502),
    ],
)
def test_fixed_status_bindings_spot_check(code, status):
    assert ERROR_HTTP_STATUS[ErrorCode(code)] == status
    assert AppError(ErrorCode(code), "x").http_status == status


# ----------------------------------------------------------------------
# 与 docs/05 §4 的文档一致性
# ----------------------------------------------------------------------

def test_docs_section_4_parses_into_fifteen_tables():
    """解析器本身的门禁：十五条分节、94 行、无重复码。"""
    assert len(DOC_ROWS) == ALL_CODE_COUNT
    assert len({code for code, _, _ in DOC_ROWS}) == ALL_CODE_COUNT
    assert len({section for _, _, section in DOC_ROWS}) == SECTION_COUNT


def test_error_codes_match_docs_exactly():
    assert {code for code, _, _ in DOC_ROWS} == {c.value for c in ErrorCode}


def test_http_status_matches_docs_exactly():
    assert DOC_HTTP_STATUS == {
        code.value: status for code, status in ERROR_HTTP_STATUS.items()
    }


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


def test_app_error_message_defaults_to_none():
    """M3-11 的构造形式允许省略 ``message``（只带 headers 抛）；``str`` 不打印 None，
    错误体里的中文兜底文案由 api 层按状态码补。"""
    err = AppError(ErrorCode.RATE_LIMITED, headers={"Retry-After": "42"})
    assert err.message is None
    assert err.headers == {"Retry-After": "42"}
    assert str(err) == "RATE_LIMITED"


def test_app_error_str_is_code_colon_message():
    err = AppError(ErrorCode.CORE_START_FAILED, "restart budget exhausted")
    assert str(err) == "CORE_START_FAILED: restart budget exhausted"
    assert err.message in str(err) and err.code in str(err)


def test_app_error_repr_is_stable():
    err = AppError(ErrorCode.NOT_FOUND, "缺失", {"id": "x"})
    assert repr(err) == (
        "AppError(code=<ErrorCode.NOT_FOUND: 'NOT_FOUND'>, "
        "message='缺失', details={'id': 'x'})"
    )


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


def test_app_error_pickle_roundtrip_for_every_registered_code():
    """94 条里除例外外每一条都能走完 pickle 往返，状态码不丢。"""
    for code in ErrorCode:
        if code not in ERROR_HTTP_STATUS:
            continue
        restored = pickle.loads(pickle.dumps(AppError(code, "m", {"k": code.value})))
        assert restored.code is code
        assert restored.http_status == ERROR_HTTP_STATUS[code]


# ----------------------------------------------------------------------
# AppError.headers（M3-11）
# ----------------------------------------------------------------------


def test_app_error_headers_default_to_none():
    assert AppError(ErrorCode.RATE_LIMITED, "slow down").headers is None
    # 空映射等价于"没有额外头"；这与 details={} 不同，后者在错误体里是有意义的形状。
    assert AppError(ErrorCode.RATE_LIMITED, "slow down", headers={}).headers is None


def test_app_error_headers_accept_mapping_and_copy_it():
    headers = {"Retry-After": "42"}
    err = AppError(ErrorCode.QUEUE_FULL, "队列已满", headers=headers)
    headers["Retry-After"] = "1"
    assert err.headers == {"Retry-After": "42"}  # 已复制，外部改动不影响异常
    assert err.http_status == 429


def test_app_error_repr_includes_headers_only_when_present():
    err = AppError(ErrorCode.QUEUE_FULL, "满", None, {"Retry-After": "7"})
    assert repr(err) == (
        "AppError(code=<ErrorCode.QUEUE_FULL: 'QUEUE_FULL'>, message='满', "
        "details=None, headers={'Retry-After': '7'})"
    )


def test_app_error_pickle_roundtrip_keeps_headers():
    """头也要进 IPC / 队列往返（headers 是第 4 个位置参数，纯 dict 可序列化）。"""
    original = AppError(
        ErrorCode.RATE_LIMITED, "slow down", {"ip": "1.2.3.4"}, {"Retry-After": "42"}
    )
    restored = pickle.loads(pickle.dumps(original))
    assert restored.headers == {"Retry-After": "42"}
    assert restored.code is ErrorCode.RATE_LIMITED
    assert restored.details == {"ip": "1.2.3.4"}
    assert restored.http_status == 429


def test_app_error_rejects_non_string_header_value():
    """fail loud：头值不是 str 会在 Starlette 写响应时炸成 500，构造处就该拦住。"""
    with pytest.raises(TypeError, match="str→str"):
        AppError(ErrorCode.RATE_LIMITED, "x", headers={"Retry-After": 42})


def test_app_error_rejects_non_latin1_header_value():
    with pytest.raises(ValueError, match="latin-1"):
        AppError(ErrorCode.RATE_LIMITED, "x", headers={"X-Note": "稍后重试"})


# ----------------------------------------------------------------------
# fail loud
# ----------------------------------------------------------------------

def test_unregistered_error_code_fails_loud():
    """未登记的错误码：抛 KeyError，不返回默认状态码。"""
    with pytest.raises(KeyError, match="未登记"):
        AppError(_UnregisteredCode.NOT_IN_TABLE, "boom")


def test_update_interrupted_fails_loud_because_it_has_no_http_status():
    """唯一只落库的码也不许被赋予状态码：落库路径直接写字符串值。"""
    with pytest.raises(KeyError, match="未登记"):
        AppError(ErrorCode.UPDATE_INTERRUPTED, "服务被强杀导致更新中断")


def test_unknown_string_code_fails_loud():
    with pytest.raises(KeyError, match="未登记"):
        AppError("NOT_A_REAL_CODE", "boom")
