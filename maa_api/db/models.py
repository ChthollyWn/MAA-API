"""SQLModel 表定义：25 张业务表（docs/04 §3、§5、§6、§7）。

约束（每条都有取证依据）：

- **命名约定必须在任何表类定义之前设置。** 表定义之后再设置时，无名
  ``Index`` / ``UniqueConstraint`` / 外键不会跟随约定名（见 ``tests/fixtures/db_probe_findings.md`` §6.1）。
- **主键分两类。** 对外暴露的资源用 36 字符 UUID 字符串；高频追加表
  （``log_entry`` / ``agent_message`` / ``agent_audit``）用
  ``INTEGER PRIMARY KEY AUTOINCREMENT``，靠 ``__table_args__`` 里的
  ``{'sqlite_autoincrement': True}`` 才会在 DDL 里落下 ``AUTOINCREMENT``
  关键字：只写 ``primary_key=True`` 是不够的。缺了它，
  日志表按天清理删到尾部后 id 会复用，WebSocket 续传游标漏记录。
- **时间戳统一 UTC 的 naive datetime**，用 :func:`utcnow`，不用已废弃的
  ``datetime.utcnow()``；列名只用 ``created_at`` / ``updated_at`` /
  ``started_at`` / ``finished_at`` / ``resolved_at`` / ``expires_at``。
- **枚举存纯字符串列**，不用 ``sa.Enum``（SQLite 删不掉 CHECK 约束，枚举演进
  要 batch 重建整表）。列宽：状态/来源/级别 16，阶段/角色 24，错误码 48。
- **JSON 列用 :func:`json_column` 工厂**：``sa_column`` 一旦使用，
  ``Field()`` 上的列级参数（nullable/index/unique/default/max_length）全部失效，
  必须写进 ``Column(...)``；而 ``Column`` 实例不能被两张表共用（§7）。JSON 列的
  原地修改不被 ORM 追踪，本项目统一整体赋值。
- **外键级联**按「日志能否比主体活得久」分：``task``→``pipeline``、
  ``agent_message``→``agent_session`` 用 ``ON DELETE CASCADE``；日志、截图、
  审计、确认的关联字段用 ``ON DELETE SET NULL``（§3.4）。
- **索引照 §6 表逐条落地**，不额外发明；``log_entry`` / ``agent_message`` /
  ``agent_audit`` 三张高频写表各自不超过 3 条。``status`` / ``source`` /
  ``level`` / ``kind`` 这类低基数列不建独立索引，只作复合索引首列。

生产建表走 Alembic（docs/04 §8.3）；``create_all`` 只用于测试。
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlmodel import Field, SQLModel

from maa_api.domain.enums import (
    AgentSessionStatus,
    ConfirmationStatus,
    PipelineStatus,
    Priority,
    QuestionStatus,
    RiskLevel,
    TaskStatus,
    UpdateStatus,
)

# ---------------------------------------------------------------------------
# 命名约定：必须在任何 table=True 的模型定义之前设置（docs/04 §8.1）。
# SQLite 无法删除匿名约束，Alembic 的 batch 模式重建表时要按名字引用它们。
# ---------------------------------------------------------------------------
SQLModel.metadata.naming_convention = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """库内统一时间源：UTC 的 naive datetime（docs/04 §3.2）。

    不要换成已废弃的 ``datetime.utcnow()``：它在 3.12 起会发 DeprecationWarning，
    且语义同样是 naive UTC，没有理由再用。
    """
    return datetime.now(UTC).replace(tzinfo=None)


def new_uuid() -> str:
    """36 字符 UUID4 字符串，对外暴露资源的主键默认值（docs/04 §3.1）。"""
    return str(uuid.uuid4())


def json_column(
    name: str,
    *,
    nullable: bool = True,
    server_default: str | None = None,
) -> Column:
    """新建一个 JSON 列（工厂函数，不共用 ``Column`` 实例，docs/04 §7）。

    ``server_default`` 传 SQL 字面量原文，如 ``"'{}'"``；只在 docs 明确要求
    默认值的列上传。

    ``none_as_null=True`` 不能省：SQLAlchemy 的 ``JSON`` 默认把 Python ``None``
    序列化成 **JSON 字面量 ``'null'`` 字符串**（``none_as_null=False``），于是
    "可空 JSON 列"里躺的是非 NULL 的 ``'null'``：``IS NULL`` 查不到，
    ``resource_asset`` 的 ``content IS NOT NULL OR path IS NOT NULL`` 也会被
    静默绕过。开了之后 Python ``None`` 才真的落成 SQL NULL。
    """
    kwargs: dict[str, Any] = {"nullable": nullable}
    if server_default is not None:
        kwargs["server_default"] = text(server_default)
    return Column(name, JSON(none_as_null=True), **kwargs)


def text_column(name: str, *, nullable: bool = True) -> Column:
    """新建一个 TEXT 列（同上，每次返回新实例）。"""
    return Column(name, Text, nullable=nullable)


def datetime_column(name: str, *, nullable: bool = False) -> Column:
    """新建一个 DATETIME 列（同上，每次返回新实例）。

    Python 侧默认值仍由 ``Field(default_factory=utcnow)`` 提供：用了
    ``sa_column`` 之后 ``Field()`` 的列级参数失效，但 Python 侧默认值不失效。
    """
    return Column(name, DateTime, nullable=nullable)


# ---------------------------------------------------------------------------
# 5.1 pipeline — 流水线执行记录
# ---------------------------------------------------------------------------
class Pipeline(SQLModel, table=True):
    """队列的唯一事实来源（docs/04 §5.1）。"""

    __tablename__ = "pipeline"

    id: str = Field(default_factory=new_uuid, primary_key=True, max_length=36)
    core_id: str = Field(default="default", max_length=32)
    core_epoch: int | None = Field(default=None)
    source: str = Field(max_length=16)
    priority: int
    status: str = Field(default=PipelineStatus.PENDING, max_length=16)
    title: str | None = Field(default=None, max_length=64)
    task_count: int
    error_code: str | None = Field(default=None, max_length=48)
    error_message: str | None = Field(
        default=None, sa_column=text_column("error_message")
    )
    schedule_id: str | None = Field(
        default=None,
        foreign_key="schedule.id",
        ondelete="SET NULL",
        max_length=36,
    )
    agent_session_id: str | None = Field(
        default=None,
        foreign_key="agent_session.id",
        ondelete="SET NULL",
        max_length=36,
    )
    retry_of_id: str | None = Field(
        default=None,
        foreign_key="pipeline.id",
        ondelete="SET NULL",
        max_length=36,
    )
    idempotency_key: str | None = Field(default=None, max_length=64)
    notify_on_finish: bool = Field(default=True)
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("created_at")
    )
    started_at: datetime | None = Field(
        default=None, sa_column=datetime_column("started_at", nullable=True)
    )
    finished_at: datetime | None = Field(
        default=None, sa_column=datetime_column("finished_at", nullable=True)
    )
    deferred_until: datetime | None = Field(
        default=None, sa_column=datetime_column("deferred_until", nullable=True)
    )
    defer_count: int = Field(
        default=0,
        sa_column=Column("defer_count", Integer, nullable=False, server_default=text("0")),
    )

    __table_args__ = (
        # 取下一条待执行 / 当前运行中（复用前缀）：等值 core_id+status → 排序 priority,created_at
        Index("ix_pipeline_dequeue", "core_id", "status", "priority", "created_at"),
        # 流水线列表：可选 status 过滤，按 created_at DESC 分页
        Index("ix_pipeline_status_created_at", "status", "created_at"),
        Index("ix_pipeline_source_created_at", "source", "created_at"),
        # 幂等提交去重
        UniqueConstraint("idempotency_key", name="uq_pipeline_idempotency_key"),
        # 定时任务的历史实例：schedule_id=? ORDER BY created_at DESC
        Index("ix_pipeline_schedule_id_created_at", "schedule_id", "created_at"),
    )


# ---------------------------------------------------------------------------
# 5.2 task — 单个 MAA 任务
# ---------------------------------------------------------------------------
class Task(SQLModel, table=True):
    """流水线内的单个 MAA 任务（docs/04 §5.2）。"""

    __tablename__ = "task"

    id: str = Field(default_factory=new_uuid, primary_key=True, max_length=36)
    pipeline_id: str = Field(
        foreign_key="pipeline.id", ondelete="CASCADE", max_length=36
    )
    order_index: int
    type_name: str = Field(max_length=32)
    task_name: str = Field(max_length=32)
    # 下发给内核的参数原文（已注入默认值、已剔除 None）
    params: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=json_column("params", nullable=False, server_default="'{}'"),
    )
    # 客户端原始提交（exclude_unset 后），供重放
    raw_params: dict[str, Any] | None = Field(
        default=None, sa_column=json_column("raw_params")
    )
    status: str = Field(default=TaskStatus.PENDING, max_length=16)
    error_code: str | None = Field(default=None, max_length=48)
    error_message: str | None = Field(
        default=None, sa_column=text_column("error_message")
    )
    retry_count: int = Field(default=0)
    max_retries: int = Field(default=3)
    retry_delay: int = Field(default=30)
    maa_task_id: int | None = Field(default=None)
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("created_at")
    )
    started_at: datetime | None = Field(
        default=None, sa_column=datetime_column("started_at", nullable=True)
    )
    finished_at: datetime | None = Field(
        default=None, sa_column=datetime_column("finished_at", nullable=True)
    )

    __table_args__ = (
        # 任务列表：pipeline_id=? ORDER BY order_index（唯一，兼作索引）
        UniqueConstraint("pipeline_id", "order_index", name="uq_task_pipeline_order"),
        # 内核 task id 反查：pipeline_id=? AND maa_task_id=?（NULL 允许，提交失败时没有内核 id）
        UniqueConstraint(
            "pipeline_id", "maa_task_id", name="uq_task_pipeline_maa_task"
        ),
    )


# ---------------------------------------------------------------------------
# Structured MaaCore callback statistics (docs/06 §3.4)
# ---------------------------------------------------------------------------
class StageDrop(SQLModel, table=True):
    """One item from a structured ``StageDrops`` callback."""

    __tablename__ = "stage_drop"

    id: int | None = Field(default=None, primary_key=True)
    callback_id: str = Field(max_length=36)
    pipeline_id: str | None = Field(
        default=None,
        foreign_key="pipeline.id",
        ondelete="SET NULL",
        max_length=36,
    )
    task_id: str | None = Field(
        default=None, foreign_key="task.id", ondelete="SET NULL", max_length=36
    )
    stage_code: str | None = Field(default=None, max_length=24)
    stars: int | None = Field(default=None)
    item_id: str | None = Field(default=None, max_length=64)
    item_name: str | None = Field(default=None, sa_column=text_column("item_name"))
    quantity: int
    add_quantity: int
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("created_at")
    )

    __table_args__ = (
        Index("ix_stage_drop_stage_created_at", "stage_code", "created_at"),
        Index("ix_stage_drop_callback_id", "callback_id"),
    )


class SanityObservation(SQLModel, table=True):
    """One structured ``SanityBeforeStage`` callback sample."""

    __tablename__ = "sanity_observation"

    id: int | None = Field(default=None, primary_key=True)
    pipeline_id: str | None = Field(
        default=None,
        foreign_key="pipeline.id",
        ondelete="SET NULL",
        max_length=36,
    )
    task_id: str | None = Field(
        default=None, foreign_key="task.id", ondelete="SET NULL", max_length=36
    )
    stage_code: str | None = Field(default=None, max_length=24)
    current_sanity: int
    max_sanity: int
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("created_at")
    )

    __table_args__ = (
        Index("ix_sanity_observation_stage_created_at", "stage_code", "created_at"),
    )


# ---------------------------------------------------------------------------
# 5.3 log_entry — 三路日志
# ---------------------------------------------------------------------------
class LogEntry(SQLModel, table=True):
    """三路日志的统一落点；id 单调递增，兼作 WebSocket 续传游标（docs/04 §5.3）。"""

    __tablename__ = "log_entry"

    # 高频追加表：必须是 INTEGER PRIMARY KEY AUTOINCREMENT（§3.1）
    id: int | None = Field(default=None, primary_key=True)
    source: str = Field(max_length=16)
    level: str = Field(max_length=16)
    content: str = Field(sa_column=text_column("content", nullable=False))
    meta: dict[str, Any] | None = Field(default=None, sa_column=json_column("meta"))
    pipeline_id: str | None = Field(
        default=None,
        foreign_key="pipeline.id",
        ondelete="SET NULL",
        max_length=36,
    )
    task_id: str | None = Field(
        default=None, foreign_key="task.id", ondelete="SET NULL", max_length=36
    )
    core_id: str | None = Field(default=None, max_length=32)
    # 事件发生时间，不是入库时间（LogHub 攒批刷盘会晚几百毫秒）
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("created_at")
    )

    __table_args__ = (
        # 日志分页 + 日志清理（source=? AND created_at < ?）共用
        Index("ix_log_entry_source_created_at", "source", "created_at"),
        # 流水线日志：pipeline_id=? ORDER BY id
        Index("ix_log_entry_pipeline_id_id", "pipeline_id", "id"),
        {"sqlite_autoincrement": True},
    )


# ---------------------------------------------------------------------------
# 5.4 screenshot — 截图归档
# ---------------------------------------------------------------------------
class Screenshot(SQLModel, table=True):
    """截图归档；文件清理后保留记录并打 deleted_at（docs/04 §5.4）。"""

    __tablename__ = "screenshot"

    id: str = Field(default_factory=new_uuid, primary_key=True, max_length=36)
    pipeline_id: str | None = Field(
        default=None,
        foreign_key="pipeline.id",
        ondelete="SET NULL",
        max_length=36,
    )
    task_id: str | None = Field(
        default=None, foreign_key="task.id", ondelete="SET NULL", max_length=36
    )
    trigger: str = Field(max_length=16)
    backend: str = Field(max_length=16)
    # 相对 resource/ 的路径
    path: str = Field(max_length=255)
    format: str = Field(max_length=8)
    width: int
    height: int
    size_bytes: int
    deleted_at: datetime | None = Field(
        default=None, sa_column=datetime_column("deleted_at", nullable=True)
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("created_at")
    )

    __table_args__ = (
        # 截图清理：created_at < ?
        Index("ix_screenshot_created_at", "created_at"),
        # 截图列表 / 清理：pipeline_id=?
        Index("ix_screenshot_pipeline_id_created_at", "pipeline_id", "created_at"),
    )


# ---------------------------------------------------------------------------
# 5.5 schedule — 定时任务
# ---------------------------------------------------------------------------
class Schedule(SQLModel, table=True):
    """定时任务；template 直接存提交请求体的 tasks 数组（docs/04 §5.5）。"""

    __tablename__ = "schedule"

    id: str = Field(default_factory=new_uuid, primary_key=True, max_length=36)
    name: str = Field(max_length=64)
    cron: str = Field(max_length=64)
    timezone: str = Field(default="Asia/Shanghai", max_length=48)
    enabled: bool = Field(default=True)
    template: list[Any] = Field(
        default_factory=list, sa_column=json_column("template", nullable=False)
    )
    priority: int = Field(default=Priority.SCHEDULED)
    misfire_grace_seconds: int = Field(default=300)
    catch_up: bool = Field(default=False)
    skip_if_running: bool = Field(default=True)
    last_run_at: datetime | None = Field(
        default=None, sa_column=datetime_column("last_run_at", nullable=True)
    )
    next_run_at: datetime | None = Field(
        default=None, sa_column=datetime_column("next_run_at", nullable=True)
    )
    last_pipeline_id: str | None = Field(
        default=None,
        foreign_key="pipeline.id",
        ondelete="SET NULL",
        max_length=36,
    )
    last_result: str | None = Field(default=None, max_length=16)
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("created_at")
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("updated_at")
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_schedule_name"),
        # 装载定时任务：enabled=1，按 next_run_at 排序
        Index("ix_schedule_enabled_next_run_at", "enabled", "next_run_at"),
    )


# ---------------------------------------------------------------------------
# 5.6 setting — 可视化配置项
# ---------------------------------------------------------------------------
class Setting(SQLModel, table=True):
    """只存被显式覆盖过的配置项（docs/04 §5.6）。"""

    __tablename__ = "setting"

    key: str = Field(primary_key=True, max_length=64)
    # 任意类型都包进 JSON，包括标量，以区分 "25" 与 25
    value: Any = Field(sa_column=json_column("value", nullable=False))
    updated_by: str | None = Field(default=None, max_length=16)
    updated_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("updated_at")
    )


# ---------------------------------------------------------------------------
# 5.7 agent_session — 内置 agent 会话
# ---------------------------------------------------------------------------
class AgentSession(SQLModel, table=True):
    """内置 agent 会话；model / base_url 存创建时快照（docs/04 §5.7）。"""

    __tablename__ = "agent_session"

    id: str = Field(default_factory=new_uuid, primary_key=True, max_length=36)
    title: str | None = Field(default=None, max_length=64)
    status: str = Field(default=AgentSessionStatus.ACTIVE, max_length=16)
    model: str = Field(max_length=64)
    base_url: str | None = Field(default=None, max_length=255)
    incident_key: str | None = Field(default=None, max_length=160)
    message_count: int = Field(default=0)
    prompt_tokens: int = Field(default=0)
    completion_tokens: int = Field(default=0)
    atomic_grant_id: str | None = Field(
        default=None,
        foreign_key="confirmation.id",
        ondelete="SET NULL",
        max_length=36,
    )
    atomic_grant_expires_at: datetime | None = Field(
        default=None, sa_column=datetime_column("atomic_grant_expires_at", nullable=True)
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("created_at")
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("updated_at")
    )
    last_message_at: datetime | None = Field(
        default=None, sa_column=datetime_column("last_message_at", nullable=True)
    )

    __table_args__ = (
        # 会话列表：ORDER BY last_message_at DESC
        Index("ix_agent_session_last_message_at", "last_message_at"),
        # An incident event is durable across process restarts; multiple normal
        # conversations keep NULL and remain unrestricted by SQLite uniqueness.
        Index("uq_agent_session_incident_key", "incident_key", unique=True),
    )


# ---------------------------------------------------------------------------
# 5.8 agent_message — 会话消息与工具调用轨迹
# ---------------------------------------------------------------------------
class AgentMessage(SQLModel, table=True):
    """会话消息；字段结构贴近 OpenAI Chat Completions（docs/04 §5.8）。"""

    __tablename__ = "agent_message"

    # 高频追加表：必须是 INTEGER PRIMARY KEY AUTOINCREMENT（§3.1）
    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(
        foreign_key="agent_session.id", ondelete="CASCADE", max_length=36
    )
    seq: int
    role: str = Field(max_length=16)
    # 纯 tool_calls 的 assistant 消息此列为 NULL
    content: str | None = Field(default=None, sa_column=text_column("content"))
    # 思考模式模型的 `delta.reasoning_content`。必须原样回传：历史里后面不跟 user
    # 消息的 assistant 消息缺了它，上游会以 400 拒绝（见 migration 0013）。
    reasoning_content: str | None = Field(
        default=None, sa_column=text_column("reasoning_content")
    )
    tool_calls: list[Any] | None = Field(
        default=None, sa_column=json_column("tool_calls")
    )
    tool_call_id: str | None = Field(default=None, max_length=64)
    tool_name: str | None = Field(default=None, max_length=64)
    audit_id: int | None = Field(
        default=None, foreign_key="agent_audit.id", ondelete="SET NULL"
    )
    prompt_tokens: int | None = Field(default=None)
    completion_tokens: int | None = Field(default=None)
    finish_reason: str | None = Field(default=None, max_length=24)
    latency_ms: int | None = Field(default=None)
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("created_at")
    )

    __table_args__ = (
        # 会话消息：session_id=? ORDER BY seq（唯一，兼作索引）
        UniqueConstraint("session_id", "seq", name="uq_agent_message_session_seq"),
        {"sqlite_autoincrement": True},
    )


# ---------------------------------------------------------------------------
# 5.9 agent_audit — 全量工具调用审计
# ---------------------------------------------------------------------------
class AgentAudit(SQLModel, table=True):
    """REST 与内置 Agent 共用的工具调用审计落点（docs/04 §5.9）。"""

    __tablename__ = "agent_audit"

    # 高频追加表：必须是 INTEGER PRIMARY KEY AUTOINCREMENT（§3.1）
    id: int | None = Field(default=None, primary_key=True)
    caller: str = Field(max_length=16)
    caller_detail: str | None = Field(default=None, max_length=128)
    tool_name: str = Field(max_length=64)
    request_id: str | None = Field(default=None, max_length=128)
    # Retained for schema compatibility with already-applied migration 0010.
    # New Agent calls do not populate or expose this legacy audit metadata.
    scopes: list[str] | None = Field(
        default=None, sa_column=json_column("scopes", nullable=True)
    )
    # 入库前已裁剪（超过 1 KB 的字符串值替换为 __truncated__ 结构）
    arguments: dict[str, Any] = Field(
        default_factory=dict, sa_column=json_column("arguments", nullable=False)
    )
    result_summary: str | None = Field(
        default=None, sa_column=text_column("result_summary")
    )
    result_ref: dict[str, Any] | None = Field(
        default=None, sa_column=json_column("result_ref")
    )
    status: str = Field(max_length=16)
    error_code: str | None = Field(default=None, max_length=48)
    risk_level: str = Field(default=RiskLevel.NONE, max_length=16)
    forced: bool = Field(default=False)
    # 逐次确认与会话级授权互斥：前者有值或后者有值，或两者皆空
    confirmation_id: str | None = Field(
        default=None,
        foreign_key="confirmation.id",
        ondelete="SET NULL",
        max_length=36,
    )
    authorized_by_id: str | None = Field(
        default=None,
        foreign_key="confirmation.id",
        ondelete="SET NULL",
        max_length=36,
    )
    session_id: str | None = Field(
        default=None,
        foreign_key="agent_session.id",
        ondelete="SET NULL",
        max_length=36,
    )
    duration_ms: int = Field(default=0)
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("created_at")
    )

    __table_args__ = (
        # 审计查询：可选 caller / tool_name 过滤，按 created_at DESC
        Index("ix_agent_audit_caller_created_at", "caller", "created_at"),
        Index("ix_agent_audit_tool_name_created_at", "tool_name", "created_at"),
        {"sqlite_autoincrement": True},
    )


class AgentIdempotency(SQLModel, table=True):
    """24-hour REST invoke replay keys, scoped to a caller and linked audit."""

    __tablename__ = "agent_idempotency"

    id: int | None = Field(default=None, primary_key=True)
    caller: str = Field(max_length=16)
    key: str = Field(max_length=64)
    request_hash: str = Field(max_length=64)
    request_mode: str | None = Field(default=None, max_length=8)
    audit_id: int | None = Field(
        default=None, foreign_key="agent_audit.id", ondelete="CASCADE"
    )
    response_status: int | None = Field(default=None)
    response_body: dict[str, Any] | None = Field(
        default=None, sa_column=json_column("response_body")
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("created_at")
    )

    __table_args__ = (
        UniqueConstraint("caller", "key", name="uq_agent_idempotency_caller_key"),
        Index("ix_agent_idempotency_created_at", "created_at"),
        {"sqlite_autoincrement": True},
    )


# ---------------------------------------------------------------------------
# 5.10 confirmation — 人工确认请求
# ---------------------------------------------------------------------------
class Confirmation(SQLModel, table=True):
    """人工确认；payload 存待执行动作的完整参数而不是引用（docs/04 §5.10）。"""

    __tablename__ = "confirmation"

    id: str = Field(default_factory=new_uuid, primary_key=True, max_length=36)
    action: str = Field(max_length=64)
    risk_level: str = Field(max_length=16)
    reason: str = Field(sa_column=text_column("reason", nullable=False))
    payload: dict[str, Any] = Field(
        default_factory=dict, sa_column=json_column("payload", nullable=False)
    )
    status: str = Field(default=ConfirmationStatus.PENDING, max_length=16)
    requested_by: str = Field(max_length=16)
    audit_id: int | None = Field(
        default=None, foreign_key="agent_audit.id", ondelete="SET NULL"
    )
    resolved_by: str | None = Field(default=None, max_length=32)
    resolved_reason: str | None = Field(
        default=None, sa_column=text_column("resolved_reason")
    )
    # 超时时刻，按 risk_level 分级（消耗/破坏类 10 分钟，原子授权 120 秒）
    expires_at: datetime = Field(
        sa_column=datetime_column("expires_at", nullable=False)
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("created_at")
    )
    resolved_at: datetime | None = Field(
        default=None, sa_column=datetime_column("resolved_at", nullable=True)
    )

    __table_args__ = (
        # 待确认列表与超时扫描共用：status='pending' [AND expires_at < ?]
        Index("ix_confirmation_status_expires_at", "status", "expires_at"),
    )


# ---------------------------------------------------------------------------
# 5.11 update_record — 三种热更新的历史
# ---------------------------------------------------------------------------
class UpdateRecord(SQLModel, table=True):
    """热更新历史；同一 target 同时只允许一条 running（docs/04 §5.11）。"""

    __tablename__ = "update_record"

    id: str = Field(default_factory=new_uuid, primary_key=True, max_length=36)
    target: str = Field(max_length=16)
    # 含义随 target 而定；game 恒为 NULL
    channel: str | None = Field(default=None, max_length=16)
    from_version: str | None = Field(default=None, max_length=64)
    to_version: str | None = Field(default=None, max_length=64)
    status: str = Field(default=UpdateStatus.PENDING, max_length=16)
    phase: str | None = Field(default=None, max_length=24)
    progress: int = Field(default=0)
    bytes_total: int | None = Field(default=None)
    bytes_done: int | None = Field(default=None)
    error_code: str | None = Field(default=None, max_length=48)
    error_message: str | None = Field(
        default=None, sa_column=text_column("error_message")
    )
    log: str | None = Field(default=None, sa_column=text_column("log"))
    triggered_by: str = Field(max_length=16)
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("created_at")
    )
    started_at: datetime | None = Field(
        default=None, sa_column=datetime_column("started_at", nullable=True)
    )
    finished_at: datetime | None = Field(
        default=None, sa_column=datetime_column("finished_at", nullable=True)
    )

    __table_args__ = (
        # 更新历史：target=? ORDER BY created_at DESC
        Index("ix_update_record_target_created_at", "target", "created_at"),
        # 同 target 并发拦截：部分唯一索引，只有 running 行互斥。
        # 不能写成无条件的 UNIQUE(target)，否则历史记录只能留一条。
        Index(
            "uq_update_record_running_target",
            "target",
            unique=True,
            sqlite_where=text("status = 'running'"),
        ),
    )


# ---------------------------------------------------------------------------
# 5.12 notify_channel — 多通道通知配置
# ---------------------------------------------------------------------------
class NotifyChannel(SQLModel, table=True):
    """通知通道；config 结构按 type 分支（docs/04 §5.12）。"""

    __tablename__ = "notify_channel"

    id: str = Field(default_factory=new_uuid, primary_key=True, max_length=36)
    type: str = Field(max_length=16)
    name: str = Field(max_length=64)
    enabled: bool = Field(default=True)
    config: dict[str, Any] = Field(
        default_factory=dict, sa_column=json_column("config", nullable=False)
    )
    events: list[Any] = Field(
        default_factory=list, sa_column=json_column("events", nullable=False)
    )
    last_sent_at: datetime | None = Field(
        default=None, sa_column=datetime_column("last_sent_at", nullable=True)
    )
    last_status: str | None = Field(default=None, max_length=16)
    last_error: str | None = Field(default=None, sa_column=text_column("last_error"))
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("created_at")
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("updated_at")
    )

    __table_args__ = (
        UniqueConstraint("type", "name", name="uq_notify_channel_type_name"),
    )


# ---------------------------------------------------------------------------
# 5.13 resource_asset — 自定义资源与远端资源的版本基准
# ---------------------------------------------------------------------------
class ResourceAsset(SQLModel, table=True):
    """自定义资源与远端资源的版本基准（docs/04 §5.13）。"""

    __tablename__ = "resource_asset"

    id: str = Field(default_factory=new_uuid, primary_key=True, max_length=36)
    kind: str = Field(max_length=16)
    name: str = Field(max_length=64)
    description: str | None = Field(
        default=None, sa_column=text_column("description")
    )
    # 小作业直接入库（≤ 64 KB），大作业落文件走 path
    content: Any = Field(default=None, sa_column=json_column("content"))
    path: str | None = Field(default=None, max_length=255)
    # 关卡名、作业作者、方案数量等摘要信息
    meta: dict[str, Any] | None = Field(default=None, sa_column=json_column("meta"))
    checksum: str | None = Field(default=None, max_length=64)
    enabled: bool = Field(default=True)
    remote_version: str | None = Field(default=None, max_length=64)
    etag: str | None = Field(default=None, max_length=128)
    last_modified: str | None = Field(default=None, max_length=64)
    last_checked_at: datetime | None = Field(
        default=None, sa_column=datetime_column("last_checked_at", nullable=True)
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("created_at")
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("updated_at")
    )

    __table_args__ = (
        # 远端版本基准读取：kind=? AND name=?（唯一，兼作索引）
        UniqueConstraint("kind", "name", name="uq_resource_asset_kind_name"),
        # content 与 path 二选一
        CheckConstraint(
            "content IS NOT NULL OR path IS NOT NULL", name="content_or_path"
        ),
        # 资源查询：kind=? AND enabled=1
        Index("ix_resource_asset_kind_enabled", "kind", "enabled"),
    )


# ---------------------------------------------------------------------------
# 5.14 api_snippet — named API-console requests
# ---------------------------------------------------------------------------
class ApiSnippet(SQLModel, table=True):
    """A reusable API-console request with credentials removed (docs/04 §5.14)."""

    __tablename__ = "api_snippet"

    id: str = Field(default_factory=new_uuid, primary_key=True, max_length=36)
    name: str = Field(min_length=1, max_length=64)
    method: str = Field(max_length=8)
    path: str = Field(max_length=2048)
    path_params: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=json_column("path_params", nullable=False),
    )
    query: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=json_column("query", nullable=False),
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        sa_column=json_column("headers", nullable=False),
    )
    body: Any | None = Field(default=None, sa_column=json_column("body"))
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("created_at")
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("updated_at")
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_api_snippet_name"),
        Index("ix_api_snippet_updated_at", "updated_at"),
    )


class PushSubscription(SQLModel, table=True):
    """Web Push 订阅（M14，docs/09 §13.1）。

    ``endpoint`` 是推送服务签发的完整 URL，作为幂等键：浏览器重复订阅同一
    Service Worker 会拿到相同 endpoint，因此重放注册应当更新而不是新增行。
    ``endpoint_host`` 单独存一份，供列表接口展示设备来源 —— 列表**不回显**
    ``endpoint`` 全文与两个密钥字段，它们与凭据同级。

    失效订阅保留行并置 ``disabled_at`` 而不是删除：用户需要看到「某台设备的
    订阅已失效」并有机会重新启用，静默删行会让通知无声无息地消失。
    """

    __tablename__ = "push_subscription"

    id: str = Field(default_factory=new_uuid, primary_key=True, max_length=36)
    endpoint: str = Field(max_length=2048)
    endpoint_host: str = Field(max_length=255)
    p256dh: str = Field(max_length=255)
    auth: str = Field(max_length=255)
    user_agent: str | None = Field(default=None, max_length=255)
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("created_at")
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("updated_at")
    )
    last_success_at: datetime | None = Field(
        default=None, sa_column=datetime_column("last_success_at", nullable=True)
    )
    disabled_at: datetime | None = Field(
        default=None, sa_column=datetime_column("disabled_at", nullable=True)
    )

    __table_args__ = (
        UniqueConstraint("endpoint", name="uq_push_subscription_endpoint"),
        Index("ix_push_subscription_disabled_at", "disabled_at"),
    )


# ---------------------------------------------------------------------------
# 5.16 agent_knowledge — Agent 知识库的用户改动（规格 §4.1）
# ---------------------------------------------------------------------------
class AgentKnowledge(SQLModel, table=True):
    """Agent 知识库的覆盖层：只存与内置条目**不同**的字段。

    内置条目随代码发布（``maa_api/agent/knowledge/builtin/*.md``），这张表里
    没有某一行就意味着「用内置的原文」。``id`` 与内置条目相同时是覆盖行：

    - 只写与内置不同的列，其余列为 NULL，读取时继承内置值；
    - ``kind`` 不可覆盖，覆盖行里恒为 NULL；
    - 与内置完全一致且启用时不留行（「恢复默认」＝删行）；停用内置条目就是写
      一行只有 ``enabled = false`` 的覆盖行。

    其余行是自定义条目，所有列必填。读侧合并算法见
    :mod:`maa_api.agent.knowledge`；本表最多几十行、每轮全量读，因此不建索引。
    """

    __tablename__ = "agent_knowledge"

    id: str = Field(primary_key=True, max_length=48)
    kind: str | None = Field(default=None, max_length=16)
    title: str | None = Field(default=None, max_length=32)
    when_to_use: str | None = Field(default=None, max_length=120)
    body: str | None = Field(default=None, sa_column=text_column("body"))
    enabled: bool = Field(default=True)
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("created_at")
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("updated_at")
    )


# ---------------------------------------------------------------------------
# 5.17 agent_question — Agent 主动提问与参数选择卡片（规格 §3.2）
# ---------------------------------------------------------------------------
class AgentQuestion(SQLModel, table=True):
    """Agent 主动提问：与 ``confirmation`` **并列**的一等交互（M21 规格 §3.2）。

    刻意不复用 ``confirmation`` 表，也不给它加 ``question`` 分支：确认是「授权一次
    有风险的操作」（有风险等级、能授权、占待确认计数、要 Web Push），提问只是「在
    若干选项里取一个答案」（无风险、不授权、不打扰用户）。两者的生命周期、可见范围
    与终态语义都不同，混在一张表里会让「待确认」计数把提问算进去。

    ``options`` 与 ``selection`` 存的是 label 文本而不是索引：卡片渲染与答案回填
    都以 label 为准，模型拿到的 ``selected`` 也是 label 列表（规格 §3.3），存索引会
    在选项顺序变化时把历史答案指向别的选项。

    ``expires_at`` 在创建时按 ``agent.question_timeout_seconds`` 算好落库（而不是
    靠进程内计时器）：超时是**持久化事实**，进程重启后由 :meth:`expire_due` 兜底，
    刷新页面看到的倒计时也来自这一列。
    """

    __tablename__ = "agent_question"

    id: str = Field(default_factory=new_uuid, primary_key=True, max_length=36)
    session_id: str = Field(
        foreign_key="agent_session.id", ondelete="CASCADE", max_length=36
    )
    #: 模型那次 ``ask_user`` 调用的 id，前端据此把卡片挂到对应的工具条上。
    tool_call_id: str = Field(max_length=64)
    question: str = Field(sa_column=text_column("question", nullable=False))
    options: list[Any] = Field(
        default_factory=list, sa_column=json_column("options", nullable=False)
    )
    multiple: bool = Field(default=False)
    status: str = Field(default=QuestionStatus.PENDING, max_length=16)
    #: 选中项 label 列表；未回答时为 NULL。
    selection: list[Any] | None = Field(
        default=None, sa_column=json_column("selection")
    )
    #: ``web``（用户点选/跳过）或 ``system``（超时、重启清理）。
    answered_by: str | None = Field(default=None, max_length=32)
    audit_id: int | None = Field(
        default=None, foreign_key="agent_audit.id", ondelete="SET NULL"
    )
    expires_at: datetime = Field(
        sa_column=datetime_column("expires_at", nullable=False)
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("created_at")
    )
    answered_at: datetime | None = Field(
        default=None, sa_column=datetime_column("answered_at", nullable=True)
    )

    __table_args__ = (
        # 超时扫描：status='pending' [AND expires_at < ?]
        Index("ix_agent_question_status_expires_at", "status", "expires_at"),
        # 会话待答列表：session_id=? AND status=? ORDER BY created_at
        Index(
            "ix_agent_question_session_status_created_at",
            "session_id",
            "status",
            "created_at",
        ),
    )


# ---------------------------------------------------------------------------
# M27 账号数据：account_operator / account_operator_diff / account_depot_item /
# account_recognition / upgrade_plan（规格 §7.3、附录 E）
#
# 字段比附录 E 多两列，都是页面与工具已经在用的：``account_operator.profession``
# （列表与工具的 profession 筛选、编辑抽屉不回改）与 ``account_depot_item.category``
# （仓库页按分类分组、``get_depot`` 的 category 筛选）。识别回调本身给不出职业，
# 由 capture 层用本机 ``battle_data.json`` 补。
# ---------------------------------------------------------------------------


def empty_masteries() -> dict[str, int]:
    """专精列的默认值：三个技能槽都在、都为 0（规格 §7.5 的 wire 形状）。"""
    return {"1": 0, "2": 0, "3": 0}


class AccountOperator(SQLModel, table=True):
    """账号里的一名干员（规格 §7.3、附录 E）。

    一行同时承载三种来源的值：识别（``own`` / ``elite`` / ``level`` / ``potential``）、
    手动编辑、Agent 写入。``field_sources`` 记每个字段最后一次是谁写的，
    ``recognized_values`` 记最近一次识别值：识别覆盖时用它判断「这个字段在上次识别
    之后有没有被手动改过」，见 :meth:`~maa_api.services.account.box.OperatorBoxService.apply_recognition`。

    ``name`` / ``rarity`` / ``profession`` 允许为空：``all_opers`` 里只有 id、
    又没有被本机资料认出来的干员行仍然要留（``own=false``），等后续识别或资源补全。
    """

    __tablename__ = "account_operator"

    char_id: str = Field(primary_key=True, max_length=64)
    name: str | None = Field(default=None, max_length=64)
    rarity: int | None = Field(default=None)
    #: 中文职业（近卫 / 术师 …），来自本机 ``battle_data.json``。
    profession: str | None = Field(default=None, max_length=16)
    own: bool = Field(default=False)
    elite: int = Field(default=0)
    level: int = Field(default=0)
    potential: int = Field(default=0)
    skill_level: int = Field(default=1)
    masteries: dict[str, int] = Field(
        default_factory=empty_masteries,
        sa_column=json_column("masteries", nullable=False),
    )
    modules: dict[str, int] = Field(
        default_factory=dict, sa_column=json_column("modules", nullable=False)
    )
    #: 最近一次识别的值（只含识别能覆盖的四个字段）；从未识别为 NULL。
    recognized_values: dict[str, Any] | None = Field(
        default=None, sa_column=json_column("recognized_values")
    )
    #: 字段 → 来源（``recognition`` / ``manual`` / ``agent``）。
    field_sources: dict[str, str] = Field(
        default_factory=dict, sa_column=json_column("field_sources", nullable=False)
    )
    recognized_at: datetime | None = Field(
        default=None, sa_column=datetime_column("recognized_at", nullable=True)
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("updated_at")
    )


class AccountOperatorDiff(SQLModel, table=True):
    """识别覆盖手动值的差异（规格 §7.3、附录 E）。

    只在「字段被手动 / Agent 改过、且手动值与这次识别值不同」时写一条；用户在页面
    点「知道了」置 ``dismissed_at``（行保留，不删）。
    """

    __tablename__ = "account_operator_diff"

    id: int | None = Field(default=None, primary_key=True)
    char_id: str = Field(max_length=64)
    field: str = Field(max_length=32)
    #: JSON 列而不是字符串：bool 与 int 要保留原类型（等级 90 与 True 不是一回事）。
    manual_value: Any | None = Field(default=None, sa_column=json_column("manual_value"))
    recognized_value: Any | None = Field(
        default=None, sa_column=json_column("recognized_value")
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("created_at")
    )
    dismissed_at: datetime | None = Field(
        default=None, sa_column=datetime_column("dismissed_at", nullable=True)
    )

    __table_args__ = (
        # 待处理差异列表：dismissed_at IS NULL ORDER BY created_at
        Index(
            "ix_account_operator_diff_dismissed_at_created_at",
            "dismissed_at",
            "created_at",
        ),
    )


class AccountDepotItem(SQLModel, table=True):
    """账号仓库里的一种物品（规格 §7.3、附录 E）。

    整表替换语义：一次 ``DepotInfo``（``done=true``）把这张表换成本次识别到的
    物品与数量，数量归零的物品直接消失。
    """

    __tablename__ = "account_depot_item"

    item_id: str = Field(primary_key=True, max_length=64)
    #: 本机 ``item_index.json`` 的名字；映射不到时保留 ``item_id``。
    name: str | None = Field(default=None, max_length=64)
    count: int = Field(default=0)
    #: ``item_index.json`` 的 ``classifyType``（MATERIAL / NORMAL …）；映射不到为 NULL。
    category: str | None = Field(default=None, max_length=16)
    recognized_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("recognized_at")
    )


class AccountRecognition(SQLModel, table=True):
    """一次账号识别的记录（规格 §7.3、附录 E）：时间、流水线、任务、渠道、数量。"""

    __tablename__ = "account_recognition"

    id: int | None = Field(default=None, primary_key=True)
    #: ``operbox`` / ``depot``。
    kind: str = Field(max_length=16)
    pipeline_id: str | None = Field(
        default=None,
        foreign_key="pipeline.id",
        ondelete="SET NULL",
        max_length=36,
    )
    task_id: str | None = Field(
        default=None, foreign_key="task.id", ondelete="SET NULL", max_length=36
    )
    client_type: str | None = Field(default=None, max_length=16)
    item_count: int = Field(default=0)
    recognized_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("recognized_at")
    )

    __table_args__ = (
        # 最近一次识别：kind=? ORDER BY recognized_at DESC LIMIT 1
        Index("ix_account_recognition_kind_recognized_at", "kind", "recognized_at"),
    )


class UpgradePlan(SQLModel, table=True):
    """养成计划（规格 §7.7、附录 E）：今天先打、剩余写成每日定时任务。

    ``status`` 三态：``active`` / ``completed`` / ``cancelled``（规格 §7.7）。
    ``requirements`` 是规划算出的需求，``progress`` 是每次 ``Depot`` 识别后的剩余
    需求与剩余场次；两者都是 JSON，结构由规划层定义，表不做约束。
    """

    __tablename__ = "upgrade_plan"

    id: str = Field(default_factory=new_uuid, primary_key=True, max_length=36)
    name: str = Field(max_length=64)
    status: str = Field(default="active", max_length=16)
    targets: list[Any] = Field(
        default_factory=list, sa_column=json_column("targets", nullable=False)
    )
    requirements: dict[str, Any] = Field(
        default_factory=dict, sa_column=json_column("requirements", nullable=False)
    )
    progress: dict[str, Any] = Field(
        default_factory=dict, sa_column=json_column("progress", nullable=False)
    )
    schedule_id: str | None = Field(
        default=None,
        foreign_key="schedule.id",
        ondelete="SET NULL",
        max_length=36,
    )
    session_id: str | None = Field(
        default=None,
        foreign_key="agent_session.id",
        ondelete="SET NULL",
        max_length=36,
    )
    created_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("created_at")
    )
    updated_at: datetime = Field(
        default_factory=utcnow, sa_column=datetime_column("updated_at")
    )
    completed_at: datetime | None = Field(
        default=None, sa_column=datetime_column("completed_at", nullable=True)
    )

    __table_args__ = (
        # 计划列表：status=? ORDER BY created_at
        Index("ix_upgrade_plan_status_created_at", "status", "created_at"),
    )
