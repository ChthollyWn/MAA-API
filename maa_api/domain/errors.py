"""领域错误码与领域异常。

错误码来自 docs/05 §4.1–§4.14 的十四张表，全量 92 条，**与那份表一一对应**：
``ErrorCode`` 的成员名即响应体 ``error.code`` 的线上取值（ADR-04 选自定义错误体的
核心价值就是这个可枚举字符串，agent 据此做确定性分支）；每个错误码在
:data:`ERROR_HTTP_STATUS` 里绑定**固定**的 HTTP 状态码，路由层不再自行决定状态码——
这保证了同一个错误在不同端点上表现一致。

唯一的例外是 ``UPDATE_INTERRUPTED``：它只作为 ``update_record.error_code`` 落库
（服务被强杀导致更新中断，由启动时的残留记录清理写入），不对应任何 HTTP 响应，
因此不登记在绑定表里，用它会 fail loud。

``maa_api/api/errors.py``（M3-04）注册异常处理器，把领域异常翻译成绑定表里的状态码
与统一错误体 ``{"error": {"code": ..., "message": ..., "details": {...}}}``。

本模块保持 M1-03 建立的契约：成员名 == 取值、绑定表查表 fail loud、异常可 pickle、
``error.code`` 以纯字符串序列化。

M3-11 起 ``AppError`` 还能携带可选的响应头（``headers``），供 429 / 503 这类
docs/05 §2 要求带 ``Retry-After`` 的响应使用。领域层只负责携带与校验，把
"头怎么进响应"留给 ``maa_api/api/errors.py`` 的处理器 —— 错误体 JSON 形状不受影响。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

__all__ = ["AppError", "ERROR_HTTP_STATUS", "ErrorCode"]


class ErrorCode(StrEnum):
    """与领域异常一一对应的错误码；成员名即 ``error.code`` 的线上取值。"""

    # ---- docs/05 §4.1 鉴权 ----
    # token 缺失或与 `access_token` 不匹配。四个渠道都没取到有效 token 时返回
    UNAUTHORIZED = "UNAUTHORIZED"

    # 身份有效但动作被策略拒绝：MCP 只读工具集调用了写操作；非同源请求试图仅凭 cookie 执行写操作
    FORBIDDEN = "FORBIDDEN"

    # 同一来源 IP 连续鉴权失败超过阈值（默认 10 次/分钟）后的冷却期
    RATE_LIMITED = "RATE_LIMITED"

    # ---- docs/05 §4.2 参数与请求格式 ----
    # 请求体不是合法 JSON，或 `Content-Type` 与实际内容不符
    MALFORMED_JSON = "MALFORMED_JSON"

    # Pydantic 字段校验失败。`details.fields` 为 FastAPI 原生的字段级错误数组
    VALIDATION_ERROR = "VALIDATION_ERROR"

    # 结构合法但语义不成立：未知的设置项 key、互斥参数同时出现、枚举字符串不在取值域
    INVALID_PARAMETER = "INVALID_PARAMETER"

    # `page < 1`、`size` 超过 200，或同时传了 `page` 与 `after_id`
    INVALID_PAGINATION = "INVALID_PAGINATION"

    # ---- docs/05 §4.3 通用 ----
    # 未被更具体的码覆盖的资源缺失，兜底用
    NOT_FOUND = "NOT_FOUND"

    # 访问了重构前的旧端点，见 §12
    ENDPOINT_REMOVED = "ENDPOINT_REMOVED"

    # 未捕获异常。`details` 只带一个 `trace_id`，堆栈进日志表
    INTERNAL_ERROR = "INTERNAL_ERROR"

    # SQLite 写锁超时、磁盘写满、schema 不匹配
    DATABASE_ERROR = "DATABASE_ERROR"

    # 服务处于启动中或关闭中，尚未/不再接受业务请求
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"

    # ---- docs/05 §4.4 内核状态 ----
    # 子进程处于 `STOPPED` / `STARTING`，资源尚未加载完。`details.state` 带具体状态
    CORE_NOT_READY = "CORE_NOT_READY"

    # 子进程处于 `RESTARTING`，通常发生在内核热更新期间
    CORE_RESTARTING = "CORE_RESTARTING"

    # 子进程已崩溃且未恢复。同时用作被中断流水线的 `error_code`
    CORE_CRASHED = "CORE_CRASHED"

    # 连续崩溃达到退避上限进入 `FAILED`，自动重启已停止，需人工介入
    CORE_START_FAILED = "CORE_START_FAILED"

    # 命令投递到子进程但执行失败（如 `AsstAppendTask` 返回 0）。`details.cmd` 带命令类型
    CORE_COMMAND_FAILED = "CORE_COMMAND_FAILED"

    # 命令在超时内未收到 `CMD_RESULT`。超时不代表子进程已死，仍由心跳判定；命令可能仍在执行，客户端应查状态而非重试
    CORE_COMMAND_TIMEOUT = "CORE_COMMAND_TIMEOUT"

    # `core_id` 不存在。首版只有 `default`，为多实例扩展预留
    CORE_NOT_FOUND = "CORE_NOT_FOUND"

    # `LOAD_RESOURCE` 失败，通常是 MAA 资源目录缺失或损坏
    RESOURCE_LOAD_FAILED = "RESOURCE_LOAD_FAILED"

    # `AsstGetMapLevelKey` 不可用。该 API 属实验性接口，本地内核未导出或调用异常时优雅降级
    MAP_LEVEL_KEY_UNAVAILABLE = "MAP_LEVEL_KEY_UNAVAILABLE"

    # ---- docs/05 §4.5 设备连接 ----
    # 设备状态机不在已连接态。任务执行前预检失败也报这个
    DEVICE_NOT_CONNECTED = "DEVICE_NOT_CONNECTED"

    # `adb connect` 或 `AsstAsyncConnect` 失败。`details` 带 `address` 与已尝试次数
    ADB_CONNECT_FAILED = "ADB_CONNECT_FAILED"

    # 配置的 `adb.path` 不存在或不可执行，属本地环境问题
    ADB_NOT_FOUND = "ADB_NOT_FOUND"

    # ADB 原子操作（swipe / 长按 / 输入文本 / 按键）执行失败
    ADB_COMMAND_FAILED = "ADB_COMMAND_FAILED"

    # `adb devices` 列举失败，通常是 adb server 未启动
    DEVICE_SCAN_FAILED = "DEVICE_SCAN_FAILED"

    # 设备已连上但分辨率不被内核支持，自动化无法进行。`details` 带实际分辨率
    DEVICE_RESOLUTION_UNSUPPORTED = "DEVICE_RESOLUTION_UNSUPPORTED"

    # ---- docs/05 §4.6 截图 ----
    # 内核 `AsstAsyncScreencap` 或 ADB 截屏失败。`details.backend` 指明是哪条通道
    SCREENSHOT_FAILED = "SCREENSHOT_FAILED"

    # 截图 id 不存在
    SCREENSHOT_NOT_FOUND = "SCREENSHOT_NOT_FOUND"

    # 记录存在但文件已被保留策略清理（`screenshot.deleted_at` 非空）
    SCREENSHOT_EXPIRED = "SCREENSHOT_EXPIRED"

    # ---- docs/05 §4.7 流水线与任务 ----
    # id 不存在，或已被 90 天/500 条保留策略清理
    PIPELINE_NOT_FOUND = "PIPELINE_NOT_FOUND"

    # 流水线运行中执行原子操作（未带 `force=true`），或试图启动第二条流水线
    PIPELINE_ALREADY_RUNNING = "PIPELINE_ALREADY_RUNNING"

    # 目标流水线已处于终态，无可取消
    PIPELINE_NOT_CANCELLABLE = "PIPELINE_NOT_CANCELLABLE"

    # 提交的 `tasks` 数组为空
    PIPELINE_EMPTY = "PIPELINE_EMPTY"

    # 单条流水线任务数超过 32
    PIPELINE_TOO_MANY_TASKS = "PIPELINE_TOO_MANY_TASKS"

    # 任务 id 不存在
    TASK_NOT_FOUND = "TASK_NOT_FOUND"

    # `name` 字段不在 9 种任务类型内，discriminated union 匹配失败
    UNKNOWN_TASK_TYPE = "UNKNOWN_TASK_TYPE"

    # 跨字段规则不满足：`mode=10000` 缺 `filename`、`mode=5` 但 `theme != "Sami"`、`series` 越界
    TASK_PARAM_INVALID = "TASK_PARAM_INVALID"

    # 使用了内核已弃用的参数值，如 `Roguelike.mode=2`
    TASK_PARAM_DEPRECATED = "TASK_PARAM_DEPRECATED"

    # 对运行中的任务修改了标注"不支持运行中设置"的参数（`stage`、`facility`、`shopping` 等）
    TASK_NOT_RUNTIME_MUTABLE = "TASK_NOT_RUNTIME_MUTABLE"

    # 同一 `Idempotency-Key` 被用于内容不同的两次提交
    IDEMPOTENCY_KEY_CONFLICT = "IDEMPOTENCY_KEY_CONFLICT"

    # ---- docs/05 §4.8 队列 ----
    # 待执行流水线数超过上限（默认 50）。带 `Retry-After`
    QUEUE_FULL = "QUEUE_FULL"

    # 队列被暂停（维护或更新期间）时提交，需先 resume
    QUEUE_PAUSED = "QUEUE_PAUSED"

    # 调整优先级或移出队列的目标已不是 `PENDING` 状态
    QUEUE_ITEM_NOT_PENDING = "QUEUE_ITEM_NOT_PENDING"

    # ---- docs/05 §4.9 三种热更新 ----
    # 更新记录 id 不存在
    UPDATE_NOT_FOUND = "UPDATE_NOT_FOUND"

    # 同一 `target` 已有进行中的更新，由部分唯一索引在库层拦住
    UPDATE_ALREADY_RUNNING = "UPDATE_ALREADY_RUNNING"

    # 队列非空或有流水线运行中，且请求未带 `force=true`
    UPDATE_BLOCKED_BY_PIPELINE = "UPDATE_BLOCKED_BY_PIPELINE"

    # 当前已是最新版本且未带 `force=true`
    ALREADY_LATEST_VERSION = "ALREADY_LATEST_VERSION"

    # 版本清单接口不可达（内核版本 API、OTA 资源清单、游戏版本接口）
    UPDATE_MANIFEST_UNAVAILABLE = "UPDATE_MANIFEST_UNAVAILABLE"

    # 下载中断或返回非 2xx
    UPDATE_DOWNLOAD_FAILED = "UPDATE_DOWNLOAD_FAILED"

    # 下载产物校验和不符，判定为传输损坏或源被污染
    UPDATE_CHECKSUM_MISMATCH = "UPDATE_CHECKSUM_MISMATCH"

    # 解压或覆盖文件失败，通常是磁盘空间或权限问题
    UPDATE_EXTRACT_FAILED = "UPDATE_EXTRACT_FAILED"

    # 更新失败后回滚也失败，内核目录可能处于不一致状态，必须人工处理
    UPDATE_ROLLBACK_FAILED = "UPDATE_ROLLBACK_FAILED"

    # 更新已进入不可中断阶段（覆盖文件、重启内核）后请求取消
    UPDATE_NOT_CANCELLABLE = "UPDATE_NOT_CANCELLABLE"

    # 下载前的磁盘预检不通过，按 §2 归入"本地环境故障"。`details` 带 `required_bytes` 与 `available_bytes`，让用户自己判断要清理多少
    UPDATE_DISK_INSUFFICIENT = "UPDATE_DISK_INSUFFICIENT"

    # 等待队列空闲超时（默认 30 分钟）且请求未带 `force_interrupt=true`
    UPDATE_QUEUE_BUSY_TIMEOUT = "UPDATE_QUEUE_BUSY_TIMEOUT"

    # 服务被强杀导致更新中断。只作为 `update_record.error_code` 落库，由启动时的残留记录清理写入，不对应任何 HTTP 响应
    UPDATE_INTERRUPTED = "UPDATE_INTERRUPTED"

    # `adb install` 返回失败，`details.adb_output` 带原始输出
    GAME_INSTALL_FAILED = "GAME_INSTALL_FAILED"

    # `dumpsys` 解析不出已安装版本，无法做版本对比
    GAME_VERSION_UNKNOWN = "GAME_VERSION_UNKNOWN"

    # ---- docs/05 §4.10 人工确认 ----
    # 操作命中消耗类或破坏类策略，已创建确认请求。**唯一出现在 2xx 响应中的码**，位于 202 的正常响应体而非错误体；MCP 同步调用时作为工具结果的 `code` 返回
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"

    # 确认请求 id 不存在
    CONFIRMATION_NOT_FOUND = "CONFIRMATION_NOT_FOUND"

    # 超时未响应，已自动拒绝。默认超时按风险分级：消耗类与破坏类 10 分钟，原子操作会话授权 120 秒
    CONFIRMATION_EXPIRED = "CONFIRMATION_EXPIRED"

    # 用户明确拒绝。`details.reason` 带拒绝原因
    CONFIRMATION_REJECTED = "CONFIRMATION_REJECTED"

    # 重复批准或拒绝已进入终态的确认请求
    CONFIRMATION_ALREADY_RESOLVED = "CONFIRMATION_ALREADY_RESOLVED"

    # ---- docs/05 §4.11 Agent 与 LLM ----
    # Agent 模块未启用
    AGENT_DISABLED = "AGENT_DISABLED"

    # 会话 id 不存在
    AGENT_SESSION_NOT_FOUND = "AGENT_SESSION_NOT_FOUND"

    # 该会话上一轮 tool-calling 循环尚未结束
    AGENT_SESSION_BUSY = "AGENT_SESSION_BUSY"

    # `ToolRegistry` 中没有该工具名
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"

    # 工具参数不满足其 JSON Schema
    TOOL_ARGS_INVALID = "TOOL_ARGS_INVALID"

    # 工具实现内部抛出未预期异常。被工具调用的下游错误（内核、设备）按其本身的码原样上抛，不包成这个码
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"

    # `base_url` / `api_key` / `model` 三项未配齐
    LLM_NOT_CONFIGURED = "LLM_NOT_CONFIGURED"

    # 上游返回非 2xx。`details.upstream_status` 与 `details.upstream_code` 透传
    LLM_REQUEST_FAILED = "LLM_REQUEST_FAILED"

    # 上游在超时内未返回
    LLM_TIMEOUT = "LLM_TIMEOUT"

    # 上游 429 透传，`Retry-After` 沿用上游值
    LLM_RATE_LIMITED = "LLM_RATE_LIMITED"

    # 会话历史超出模型上下文窗口，需新建会话或裁剪历史
    LLM_CONTEXT_OVERFLOW = "LLM_CONTEXT_OVERFLOW"

    # ---- docs/05 §4.12 定时任务 ----
    # 定时任务 id 不存在
    SCHEDULE_NOT_FOUND = "SCHEDULE_NOT_FOUND"

    # cron 表达式 APScheduler 无法解析，或 `timezone` 不是合法 IANA 时区名
    SCHEDULE_CRON_INVALID = "SCHEDULE_CRON_INVALID"

    # 名称重复，撞 `schedule.name` 唯一约束
    SCHEDULE_NAME_CONFLICT = "SCHEDULE_NAME_CONFLICT"

    # ---- docs/05 §4.13 设置与通知 ----
    # key 不在 `settings_schema` 内
    SETTING_KEY_UNKNOWN = "SETTING_KEY_UNKNOWN"

    # 值类型或取值范围不符合该项的 schema
    SETTING_VALUE_INVALID = "SETTING_VALUE_INVALID"

    # 试图修改只能从 `config.yaml` 或环境变量设置的项（`app.access_token`、`app.maa_core_path`、`adb.path`）
    SETTING_READONLY = "SETTING_READONLY"

    # 值已入库但热生效动作失败，如改完 ADB 地址后重连失败。`details.applied` 标明是否已落库
    SETTING_APPLY_FAILED = "SETTING_APPLY_FAILED"

    # 通道 id 不存在
    NOTIFY_CHANNEL_NOT_FOUND = "NOTIFY_CHANNEL_NOT_FOUND"

    # `(type, name)` 重复
    NOTIFY_CHANNEL_CONFLICT = "NOTIFY_CHANNEL_CONFLICT"

    # 通道配置不满足该类型的 schema，如 webhook 缺 `url`、bark 缺 `device_key`
    NOTIFY_CONFIG_INVALID = "NOTIFY_CONFIG_INVALID"

    # 测试发送或实际推送失败。`details` 带上游响应
    NOTIFY_SEND_FAILED = "NOTIFY_SEND_FAILED"

    # ---- docs/05 §4.14 资源 ----
    # Copilot 作业 / 基建方案 / 自定义 task 不存在
    RESOURCE_ASSET_NOT_FOUND = "RESOURCE_ASSET_NOT_FOUND"

    # `(kind, name)` 重复
    RESOURCE_ASSET_CONFLICT = "RESOURCE_ASSET_CONFLICT"

    # 作业 JSON 缺必需字段或结构不符
    COPILOT_JSON_INVALID = "COPILOT_JSON_INVALID"

    # 基建方案 JSON 结构不符，或引用了不存在的设施名
    INFRAST_PLAN_INVALID = "INFRAST_PLAN_INVALID"

    # 自定义 task 定义不符合内核 `tasks.json` 的结构约定
    CUSTOM_TASK_INVALID = "CUSTOM_TASK_INVALID"

    # 单个资源超过 2 MB 上限
    ASSET_TOO_LARGE = "ASSET_TOO_LARGE"


#: 错误码 → 固定 HTTP 状态码。与 :class:`ErrorCode` 一一对应，**唯一例外**是
#: ``UPDATE_INTERRUPTED``（只落库不返回，见模块文档字符串），故此处 91 条。
#: 新增错误码必须同时登记到 ``ErrorCode`` 与这里；未登记的错误码在 ``AppError``
#: 构造时 fail loud。状态码分界见 docs/05 §2：400/422 看 Pydantic 能不能表达，
#: 409/422 看「这个请求本身不对」还是「这个请求现在不行」，502/504 看下游有没有答复。
ERROR_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.MALFORMED_JSON: 400,
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.INVALID_PARAMETER: 400,
    ErrorCode.INVALID_PAGINATION: 400,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.ENDPOINT_REMOVED: 410,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.DATABASE_ERROR: 500,
    ErrorCode.SERVICE_UNAVAILABLE: 503,
    ErrorCode.CORE_NOT_READY: 503,
    ErrorCode.CORE_RESTARTING: 503,
    ErrorCode.CORE_CRASHED: 503,
    ErrorCode.CORE_START_FAILED: 500,
    ErrorCode.CORE_COMMAND_FAILED: 502,
    ErrorCode.CORE_COMMAND_TIMEOUT: 504,
    ErrorCode.CORE_NOT_FOUND: 404,
    ErrorCode.RESOURCE_LOAD_FAILED: 500,
    ErrorCode.MAP_LEVEL_KEY_UNAVAILABLE: 503,
    ErrorCode.DEVICE_NOT_CONNECTED: 503,
    ErrorCode.ADB_CONNECT_FAILED: 502,
    ErrorCode.ADB_NOT_FOUND: 500,
    ErrorCode.ADB_COMMAND_FAILED: 502,
    ErrorCode.DEVICE_SCAN_FAILED: 502,
    ErrorCode.DEVICE_RESOLUTION_UNSUPPORTED: 503,
    ErrorCode.SCREENSHOT_FAILED: 502,
    ErrorCode.SCREENSHOT_NOT_FOUND: 404,
    ErrorCode.SCREENSHOT_EXPIRED: 410,
    ErrorCode.PIPELINE_NOT_FOUND: 404,
    ErrorCode.PIPELINE_ALREADY_RUNNING: 409,
    ErrorCode.PIPELINE_NOT_CANCELLABLE: 409,
    ErrorCode.PIPELINE_EMPTY: 400,
    ErrorCode.PIPELINE_TOO_MANY_TASKS: 400,
    ErrorCode.TASK_NOT_FOUND: 404,
    ErrorCode.UNKNOWN_TASK_TYPE: 400,
    ErrorCode.TASK_PARAM_INVALID: 422,
    ErrorCode.TASK_PARAM_DEPRECATED: 400,
    ErrorCode.TASK_NOT_RUNTIME_MUTABLE: 409,
    ErrorCode.IDEMPOTENCY_KEY_CONFLICT: 409,
    ErrorCode.QUEUE_FULL: 429,
    ErrorCode.QUEUE_PAUSED: 409,
    ErrorCode.QUEUE_ITEM_NOT_PENDING: 409,
    ErrorCode.UPDATE_NOT_FOUND: 404,
    ErrorCode.UPDATE_ALREADY_RUNNING: 409,
    ErrorCode.UPDATE_BLOCKED_BY_PIPELINE: 409,
    ErrorCode.ALREADY_LATEST_VERSION: 409,
    ErrorCode.UPDATE_MANIFEST_UNAVAILABLE: 502,
    ErrorCode.UPDATE_DOWNLOAD_FAILED: 502,
    ErrorCode.UPDATE_CHECKSUM_MISMATCH: 502,
    ErrorCode.UPDATE_EXTRACT_FAILED: 500,
    ErrorCode.UPDATE_ROLLBACK_FAILED: 500,
    ErrorCode.UPDATE_NOT_CANCELLABLE: 409,
    ErrorCode.UPDATE_DISK_INSUFFICIENT: 500,
    ErrorCode.UPDATE_QUEUE_BUSY_TIMEOUT: 409,
    ErrorCode.GAME_INSTALL_FAILED: 502,
    ErrorCode.GAME_VERSION_UNKNOWN: 502,
    ErrorCode.CONFIRMATION_REQUIRED: 202,
    ErrorCode.CONFIRMATION_NOT_FOUND: 404,
    ErrorCode.CONFIRMATION_EXPIRED: 409,
    ErrorCode.CONFIRMATION_REJECTED: 403,
    ErrorCode.CONFIRMATION_ALREADY_RESOLVED: 409,
    ErrorCode.AGENT_DISABLED: 503,
    ErrorCode.AGENT_SESSION_NOT_FOUND: 404,
    ErrorCode.AGENT_SESSION_BUSY: 409,
    ErrorCode.TOOL_NOT_FOUND: 404,
    ErrorCode.TOOL_ARGS_INVALID: 422,
    ErrorCode.TOOL_EXECUTION_FAILED: 500,
    ErrorCode.LLM_NOT_CONFIGURED: 503,
    ErrorCode.LLM_REQUEST_FAILED: 502,
    ErrorCode.LLM_TIMEOUT: 504,
    ErrorCode.LLM_RATE_LIMITED: 429,
    ErrorCode.LLM_CONTEXT_OVERFLOW: 400,
    ErrorCode.SCHEDULE_NOT_FOUND: 404,
    ErrorCode.SCHEDULE_CRON_INVALID: 400,
    ErrorCode.SCHEDULE_NAME_CONFLICT: 409,
    ErrorCode.SETTING_KEY_UNKNOWN: 400,
    ErrorCode.SETTING_VALUE_INVALID: 422,
    ErrorCode.SETTING_READONLY: 403,
    ErrorCode.SETTING_APPLY_FAILED: 500,
    ErrorCode.NOTIFY_CHANNEL_NOT_FOUND: 404,
    ErrorCode.NOTIFY_CHANNEL_CONFLICT: 409,
    ErrorCode.NOTIFY_CONFIG_INVALID: 422,
    ErrorCode.NOTIFY_SEND_FAILED: 502,
    ErrorCode.RESOURCE_ASSET_NOT_FOUND: 404,
    ErrorCode.RESOURCE_ASSET_CONFLICT: 409,
    ErrorCode.COPILOT_JSON_INVALID: 422,
    ErrorCode.INFRAST_PLAN_INVALID: 422,
    ErrorCode.CUSTOM_TASK_INVALID: 422,
    ErrorCode.ASSET_TOO_LARGE: 400,

}


def _normalize_headers(headers: Mapping[str, str] | None) -> dict[str, str] | None:
    """校验并复制响应头；``None`` 与空映射都归一成 ``None``。

    两条 fail loud 的校验都是为了把错误暴露在构造处而不是响应构造期：Starlette
    写响应头时用 ``.encode("latin-1")``，头名/值不是 ``str``（如误传整数 42）
    或含非 latin-1 字符都会在那一刻炸成 500。空映射等价于"没有额外头"，与
    ``details={}`` 不同 —— 后者在错误体里是有意义的形状，前者不是。
    """
    if headers is None:
        return None
    normalized: dict[str, str] = {}
    for name, value in headers.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise TypeError(f"AppError.headers 必须是 str→str：{name!r}={value!r}")
        try:
            name.encode("latin-1")
            value.encode("latin-1")
        except UnicodeEncodeError:
            raise ValueError(
                f"AppError.headers 的名与值都必须能编成 latin-1（HTTP 头约束）："
                f"{name!r}={value!r}"
            ) from None
        normalized[name] = value
    return normalized or None


class AppError(Exception):
    """领域异常：携带错误码、人类可读消息、固定 HTTP 状态码与可选响应头。

    ``http_status`` 在构造时由 :data:`ERROR_HTTP_STATUS` 派生。未登记的错误码
    **fail loud**：立即抛 ``KeyError``，不返回"默认 500" —— 未登记说明调用方用错了
    错误码，是编码缺陷，应当在与错误码定义同一次改动里暴露，而不是等线上把
    一个本该是 503 的场景伪装成 500。``UPDATE_INTERRUPTED`` 是唯一被刻意排除在
    绑定表外的码（它只落库），传入同样会 fail loud —— 落库路径请直接写字符串值。

    ``headers``（M3-11）是可选的响应头通道，写法则与 ``details`` 无关：它**不参与
    错误体**，只被 ``maa_api/api/errors.py`` 的处理器原样写进响应。docs/05 §2 的
    429 / 503 用它携带 ``Retry-After``（整数秒，与鉴权限流 429 同口径）；
    ``None``（或空映射）表示不带额外头。

    ``message`` 同样可省略（``None``）：统一错误体里的中文兜底文案由
    ``api/errors.py`` 按 HTTP 状态码补（与 ``StarletteHTTPException`` 的兜底同一份
    文本），领域层不复制那张表。

    实例只持有 ``ErrorCode`` / ``str`` / ``dict``，因此可被 pickle
    （异常对象会进多进程 IPC 与任务队列，序列化往返必须保真）。
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        details: dict[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        try:
            http_status = ERROR_HTTP_STATUS[code]
        except KeyError:
            raise KeyError(f"未登记的 ErrorCode，无法派生 HTTP 状态码: {code!r}") from None

        normalized_headers = _normalize_headers(headers)

        # args 保存完整的构造参数，Exception 默认的 __reduce__ 依赖它实现 pickle 往返
        # （headers 是第 4 个位置参数，归一后的纯 dict 保证往返可序列化）。
        super().__init__(code, message, details, normalized_headers)
        self.code = code
        self.message = message
        self.details = details
        self.headers = normalized_headers
        self.http_status = http_status

    def __str__(self) -> str:
        # message 省略时只报码，不打印 "None"。
        if self.message is None:
            return str(self.code)
        return f"{self.code}: {self.message}"

    def __repr__(self) -> str:
        parts = [
            f"code={self.code!r}",
            f"message={self.message!r}",
            f"details={self.details!r}",
        ]
        if self.headers is not None:
            parts.append(f"headers={self.headers!r}")
        return f"AppError({', '.join(parts)})"
