"""M2-03 表定义验收：13 张业务表、命名约定、§6 索引、约束语义（docs/04 §3/§5/§6/§7）。

测试纪律：

- 用同步测试函数 + 临时库（``create_all`` 只用于测试；生产建表永远走 Alembic，
  docs/04 §8.3），绝不写仓库 ``resource/``。
- 元数据断言（表/列/索引/外键）与 DDL 断言（``sqlite_master``）各查一遍：前者
  保证模型声明正确，后者保证 SQLite 真的落下了 ``AUTOINCREMENT``、部分索引谓词、
  ``ON DELETE`` 与 ``CHECK`` —— 这几个失败模式都不会报错，只会静默少东西。
- 约束语义用真插入验证：级联删除、部分唯一互斥、CHECK 二选一。
"""

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import (
    JSON,
    CheckConstraint,
    Enum,
    Integer,
    UniqueConstraint,
    create_engine,
    event,
    select,
)
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel

from maa_api.db import models
from maa_api.db.models import utcnow

MD = SQLModel.metadata
SQLITE_DIALECT = sqlite_dialect.dialect()

BUSINESS_TABLES = {
    "pipeline",
    "task",
    "log_entry",
    "screenshot",
    "schedule",
    "setting",
    "agent_session",
    "agent_message",
    "agent_audit",
    "confirmation",
    "update_record",
    "notify_channel",
    "resource_asset",
}
UUID_PK_TABLES = [
    "pipeline",
    "task",
    "screenshot",
    "schedule",
    "confirmation",
    "update_record",
    "notify_channel",
    "agent_session",
    "resource_asset",
]
AUTOINCREMENT_TABLES = ["log_entry", "agent_message", "agent_audit"]

# §6 索引清单：名字 → (表, 列序, 是否唯一)。列序=等值→范围→排序，照抄不重排。
EXPECTED_INDEXES = {
    "ix_pipeline_dequeue": (
        "pipeline",
        ("core_id", "status", "priority", "created_at"),
        False,
    ),
    "ix_pipeline_status_created_at": ("pipeline", ("status", "created_at"), False),
    "ix_pipeline_source_created_at": ("pipeline", ("source", "created_at"), False),
    "ix_pipeline_schedule_id_created_at": (
        "pipeline",
        ("schedule_id", "created_at"),
        False,
    ),
    "ix_log_entry_source_created_at": ("log_entry", ("source", "created_at"), False),
    "ix_log_entry_pipeline_id_id": ("log_entry", ("pipeline_id", "id"), False),
    "ix_screenshot_created_at": ("screenshot", ("created_at",), False),
    "ix_screenshot_pipeline_id_created_at": (
        "screenshot",
        ("pipeline_id", "created_at"),
        False,
    ),
    "ix_schedule_enabled_next_run_at": ("schedule", ("enabled", "next_run_at"), False),
    "ix_confirmation_status_expires_at": (
        "confirmation",
        ("status", "expires_at"),
        False,
    ),
    "ix_agent_audit_caller_created_at": ("agent_audit", ("caller", "created_at"), False),
    "ix_agent_audit_tool_name_created_at": (
        "agent_audit",
        ("tool_name", "created_at"),
        False,
    ),
    "ix_agent_session_last_message_at": (
        "agent_session",
        ("last_message_at",),
        False,
    ),
    "ix_update_record_target_created_at": (
        "update_record",
        ("target", "created_at"),
        False,
    ),
    "ix_resource_asset_kind_enabled": ("resource_asset", ("kind", "enabled"), False),
    "uq_update_record_running_target": ("update_record", ("target",), True),
}

# §5 唯一约束：名字 → (表, 列)
EXPECTED_UNIQUE_CONSTRAINTS = {
    "uq_pipeline_idempotency_key": ("pipeline", ("idempotency_key",)),
    "uq_task_pipeline_order": ("task", ("pipeline_id", "order_index")),
    "uq_task_pipeline_maa_task": ("task", ("pipeline_id", "maa_task_id")),
    "uq_schedule_name": ("schedule", ("name",)),
    "uq_agent_message_session_seq": ("agent_message", ("session_id", "seq")),
    "uq_notify_channel_type_name": ("notify_channel", ("type", "name")),
    "uq_resource_asset_kind_name": ("resource_asset", ("kind", "name")),
}

# 外键级联：列 → ondelete（docs/04 §3.4）
EXPECTED_FK_CASCADE = {
    ("task", "pipeline_id"),
    ("agent_message", "session_id"),
}
EXPECTED_FK_SET_NULL = {
    ("pipeline", "schedule_id"),
    ("pipeline", "agent_session_id"),
    ("pipeline", "retry_of_id"),
    ("log_entry", "pipeline_id"),
    ("log_entry", "task_id"),
    ("screenshot", "pipeline_id"),
    ("screenshot", "task_id"),
    ("schedule", "last_pipeline_id"),
    ("agent_session", "atomic_grant_id"),
    ("agent_message", "audit_id"),
    ("agent_audit", "confirmation_id"),
    ("agent_audit", "authorized_by_id"),
    ("agent_audit", "session_id"),
    ("confirmation", "audit_id"),
}

EXPECTED_TEXT_COLUMNS = {
    ("pipeline", "error_message"),
    ("task", "error_message"),
    ("log_entry", "content"),
    ("agent_message", "content"),
    ("agent_audit", "result_summary"),
    ("confirmation", "reason"),
    ("confirmation", "resolved_reason"),
    ("update_record", "error_message"),
    ("update_record", "log"),
    ("notify_channel", "last_error"),
    ("resource_asset", "description"),
}

EXPECTED_JSON_COLUMNS = {
    ("task", "params"),
    ("task", "raw_params"),
    ("log_entry", "meta"),
    ("schedule", "template"),
    ("setting", "value"),
    ("agent_message", "tool_calls"),
    ("agent_audit", "arguments"),
    ("agent_audit", "result_ref"),
    ("confirmation", "payload"),
    ("notify_channel", "config"),
    ("notify_channel", "events"),
    ("resource_asset", "content"),
    ("resource_asset", "meta"),
}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def _enable_foreign_keys(dbapi_conn, _record=None) -> None:
    """SQLite 默认关闭外键约束，级联语义测试必须显式打开。"""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "models.db"


@pytest.fixture
def engine(db_path):
    eng = create_engine(f"sqlite:///{db_path}")
    event.listen(eng, "connect", _enable_foreign_keys)
    MD.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def conn(engine, db_path):
    raw = sqlite3.connect(db_path)
    raw.execute("PRAGMA foreign_keys=ON")
    yield raw
    raw.close()


def ddl(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute("select sql from sqlite_master where name=?", (name,)).fetchone()
    assert row is not None and row[0], f"sqlite_master 里没有 {name}"
    return row[0].upper()


def sql_type(column) -> str:
    """列在 SQLite 上的 DDL 类型，如 ``VARCHAR(36)`` / ``TEXT`` / ``JSON``。

    SQLModel 的字符串列是 ``AutoString``（``TypeDecorator``），不是 ``String``
    子类，``isinstance`` 判断会失败；按编译结果断言最可靠。
    """
    return str(column.type.compile(dialect=SQLITE_DIALECT)).upper()


def make_pipeline(session: Session, **kwargs) -> models.Pipeline:
    kwargs.setdefault("source", "manual")
    kwargs.setdefault("priority", 0)
    kwargs.setdefault("task_count", 1)
    pipeline = models.Pipeline(**kwargs)
    session.add(pipeline)
    session.commit()
    return pipeline


# ---------------------------------------------------------------------------
# 表集合与主键形态
# ---------------------------------------------------------------------------
def test_metadata_has_exactly_13_business_tables():
    """13 张业务表；alembic_version 由 Alembic 维护，不计入。"""
    assert set(MD.tables) == BUSINESS_TABLES


@pytest.mark.parametrize("table", UUID_PK_TABLES)
def test_uuid_primary_key_is_varchar_36(table):
    col = MD.tables[table].c["id"]
    assert col.primary_key
    assert sql_type(col) == "VARCHAR(36)"
    assert MD.tables[table].dialect_options["sqlite"].get("autoincrement") is not True


@pytest.mark.parametrize("table", AUTOINCREMENT_TABLES)
def test_high_frequency_tables_use_autoincrement(table):
    table_obj = MD.tables[table]
    col = table_obj.c["id"]
    assert col.primary_key
    assert isinstance(col.type, Integer)
    # __table_args__ 里的 sqlite_autoincrement 是 DDL 落下关键字的唯一开关（M2-01 实测）
    assert table_obj.dialect_options["sqlite"].get("autoincrement") is True


@pytest.mark.parametrize("table", AUTOINCREMENT_TABLES)
def test_autoincrement_reaches_ddl(conn, table):
    assert "AUTOINCREMENT" in ddl(conn, table)


def test_ids_are_not_reused_after_tail_delete(conn):
    """清理删到尾部后 id 不回退 —— WebSocket 续传游标的前提（docs/04 §3.1）。"""
    for content in ("one", "two", "three"):
        conn.execute(
            "insert into log_entry (source, level, content, created_at) values (?,?,?,?)",
            ("server", "info", content, "2026-01-01 00:00:00"),
        )
    conn.commit()
    last = conn.execute("select max(id) from log_entry").fetchone()[0]
    conn.execute("delete from log_entry where id = ?", (last,))
    conn.commit()
    conn.execute(
        "insert into log_entry (source, level, content, created_at) values (?,?,?,?)",
        ("server", "info", "after-cleanup", "2026-01-01 00:00:01"),
    )
    conn.commit()
    new_id = conn.execute("select max(id) from log_entry").fetchone()[0]
    assert new_id > last  # 没有复用被删掉的 id
    assert conn.execute(
        "select name from sqlite_master where name='sqlite_sequence'"
    ).fetchone() is not None


def test_uuid_primary_keys_are_unique_36_char_strings(engine):
    with Session(engine) as session:
        first = make_pipeline(session)
        second = make_pipeline(session)
        assert first.id != second.id
        assert len(first.id) == 36
        uuid.UUID(first.id)  # 可解析


# ---------------------------------------------------------------------------
# 命名约定（docs/04 §8.1）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("table", sorted(BUSINESS_TABLES))
def test_primary_key_follows_naming_convention(table):
    assert MD.tables[table].primary_key.name == f"pk_{table}"


def test_foreign_key_names_follow_naming_convention():
    for table in MD.tables.values():
        for fk in table.foreign_keys:
            expected = (
                f"fk_{table.name}_{fk.parent.name}_{fk.column.table.name}"
            )
            assert fk.constraint.name == expected, (table.name, fk.parent.name)


def test_check_constraint_follows_naming_convention():
    names = {
        c.name
        for c in MD.tables["resource_asset"].constraints
        if isinstance(c, CheckConstraint)
    }
    assert names == {"ck_resource_asset_content_or_path"}


# ---------------------------------------------------------------------------
# 索引与唯一约束（docs/04 §6）
# ---------------------------------------------------------------------------
def test_index_inventory_matches_spec_exactly():
    """21 条索引逐条核对名字、表、列序、唯一性；且没有多建任何一条。"""
    actual = {}
    for table in MD.tables.values():
        for index in table.indexes:
            actual[index.name] = (
                table.name,
                tuple(col.name for col in index.columns),
                bool(index.unique),
            )
    assert actual == EXPECTED_INDEXES


def test_unique_constraints_match_spec():
    actual = {}
    for table in MD.tables.values():
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint):
                actual[constraint.name] = (
                    table.name,
                    tuple(col.name for col in constraint.columns),
                )
    assert actual == EXPECTED_UNIQUE_CONSTRAINTS


def test_no_standalone_low_cardinality_indexes():
    """status / source / level / kind 这类低基数单列只作复合索引首列（§6 末尾）。"""
    low_cardinality = {"status", "source", "level", "kind", "type", "enabled"}
    offenders = [
        index.name
        for table in MD.tables.values()
        for index in table.indexes
        if len(index.columns) == 1 and next(iter(index.columns)).name in low_cardinality
    ]
    assert offenders == []


@pytest.mark.parametrize("table", AUTOINCREMENT_TABLES)
def test_hot_write_tables_keep_index_budget(table):
    """三张高频写表索引总数 ≤ 3（索引 + 唯一约束）。"""
    table_obj = MD.tables[table]
    uniques = sum(
        1 for c in table_obj.constraints if isinstance(c, UniqueConstraint)
    )
    assert len(table_obj.indexes) + uniques <= 3


def test_update_record_uses_partial_unique_index(conn):
    table = MD.tables["update_record"]
    running = [i for i in table.indexes if i.name == "uq_update_record_running_target"]
    assert len(running) == 1 and running[0].unique
    assert running[0].dialect_options["sqlite"].get("where") is not None
    assert "WHERE" in ddl(conn, "uq_update_record_running_target")
    assert "STATUS = 'RUNNING'" in ddl(conn, "uq_update_record_running_target")
    # target 上不能另有普通唯一索引/约束，否则历史记录只能留一条
    assert [
        i.name for i in table.indexes if i.unique and i.name != "uq_update_record_running_target"
    ] == []
    assert [
        c.name for c in table.constraints if isinstance(c, UniqueConstraint)
    ] == []


# ---------------------------------------------------------------------------
# 外键与级联（docs/04 §3.4）
# ---------------------------------------------------------------------------
def test_foreign_key_ondelete_map():
    ondelete = {
        (table.name, fk.parent.name): fk.ondelete
        for table in MD.tables.values()
        for fk in table.foreign_keys
    }
    for key in EXPECTED_FK_CASCADE:
        assert ondelete[key] == "CASCADE", (key, ondelete.get(key))
    for key in EXPECTED_FK_SET_NULL:
        assert ondelete[key] == "SET NULL", (key, ondelete.get(key))


def test_ondelete_reaches_ddl(conn):
    assert "ON DELETE CASCADE" in ddl(conn, "task")
    assert "ON DELETE SET NULL" in ddl(conn, "log_entry")
    assert "ON DELETE SET NULL" in ddl(conn, "screenshot")
    assert "ON DELETE CASCADE" in ddl(conn, "agent_message")


def test_deleting_pipeline_cascades_tasks_and_nulls_logs(engine):
    with Session(engine) as session:
        pipeline = make_pipeline(session)
        task = models.Task(
            pipeline_id=pipeline.id,
            order_index=0,
            type_name="Fight",
            task_name="刷理智",
        )
        session.add(task)
        session.commit()
        log = models.LogEntry(
            source="server",
            level="info",
            content="hello",
            pipeline_id=pipeline.id,
            task_id=task.id,
        )
        session.add(log)
        session.commit()
        pipeline_id, task_id, log_id = pipeline.id, task.id, log.id

        session.delete(pipeline)
        session.commit()

    with Session(engine) as session:
        assert session.get(models.Pipeline, pipeline_id) is None
        assert session.get(models.Task, task_id) is None
        orphan = session.get(models.LogEntry, log_id)
        assert orphan is not None  # 日志比主体活得久
        assert orphan.pipeline_id is None
        assert orphan.task_id is None


def test_deleting_agent_session_cascades_messages(engine):
    with Session(engine) as session:
        agent_session = models.AgentSession(model="gpt-test")
        session.add(agent_session)
        session.commit()
        message = models.AgentMessage(
            session_id=agent_session.id, seq=0, role="user", content="hi"
        )
        session.add(message)
        session.commit()
        session_id, message_id = agent_session.id, message.id

        session.delete(agent_session)
        session.commit()

    with Session(engine) as session:
        assert session.get(models.AgentSession, session_id) is None
        assert session.get(models.AgentMessage, message_id) is None


# ---------------------------------------------------------------------------
# 部分唯一索引与 CHECK 的语义
# ---------------------------------------------------------------------------
def test_partial_unique_index_blocks_concurrent_updates(engine):
    """同一 target 同时只允许一条 running；终态历史可以堆积（docs/04 §5.11）。"""
    with Session(engine) as session:
        session.add(
            models.UpdateRecord(target="core", status="running", triggered_by="manual")
        )
        session.commit()

    with Session(engine) as session:
        session.add(
            models.UpdateRecord(target="core", status="running", triggered_by="manual")
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    with Session(engine) as session:
        session.add(models.UpdateRecord(target="core", status="success", triggered_by="manual"))
        session.add(models.UpdateRecord(target="core", status="failed", triggered_by="manual"))
        session.add(models.UpdateRecord(target="game", status="running", triggered_by="agent"))
        session.commit()

    with Session(engine) as session:
        rows = session.scalars(select(models.UpdateRecord)).all()
        assert len(rows) == 4
        assert sum(1 for r in rows if r.status == "running") == 2  # 不同 target 可并行


def test_resource_asset_requires_content_or_path(engine, conn):
    with Session(engine) as session:
        session.add(models.ResourceAsset(kind="custom_task", name="empty"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    with Session(engine) as session:
        session.add(
            models.ResourceAsset(kind="custom_task", name="inline", content={"a": 1})
        )
        session.add(
            models.ResourceAsset(kind="custom_task", name="on-disk", path="copilot/x.json")
        )
        session.commit()
        assert session.scalars(select(models.ResourceAsset)).all()

    assert "CHECK" in ddl(conn, "resource_asset")


# ---------------------------------------------------------------------------
# 列类型与 JSON 写法（docs/04 §3.3、§7）
# ---------------------------------------------------------------------------
def test_no_sa_enum_columns():
    for table in MD.tables.values():
        for column in table.columns:
            assert not isinstance(column.type, Enum), (table.name, column.name)


@pytest.mark.parametrize("table", sorted(BUSINESS_TABLES))
def test_string_columns_declare_documented_widths(table):
    """状态/来源/级别 16、阶段/角色 24、错误码 48 的列宽是给 Alembic 的文档（§3.3）。"""
    widths = {
        "status": 16,
        "source": 16,
        "level": 16,
        "risk_level": 16,
        "target": 16,
        "type": 16,
        "caller": 16,
        "trigger": 16,
        "backend": 16,
        "role": 16,
        "phase": 24,
        "finish_reason": 24,
        "error_code": 48,
    }
    for name, width in widths.items():
        if name in MD.tables[table].c:
            column = MD.tables[table].c[name]
            assert sql_type(column) == f"VARCHAR({width})", (
                table,
                name,
                sql_type(column),
            )


def test_text_columns_use_text_type():
    actual = {
        (table.name, column.name)
        for table in MD.tables.values()
        for column in table.columns
        if sql_type(column) == "TEXT"
    }
    assert actual == EXPECTED_TEXT_COLUMNS


def test_json_columns_use_json_type():
    actual = {
        (table.name, column.name)
        for table in MD.tables.values()
        for column in table.columns
        if isinstance(column.type, JSON)
    }
    assert actual == EXPECTED_JSON_COLUMNS


def test_json_columns_roundtrip_whole_assignment(engine):
    """JSON 列整体赋值后能原样读回（§7：不做原地修改）。"""
    with Session(engine) as session:
        pipeline = make_pipeline(session)
        task = models.Task(
            pipeline_id=pipeline.id,
            order_index=0,
            type_name="Fight",
            task_name="刷理智",
            params={"stage": "1-7", "medicine": 0},
            raw_params={"stage": "1-7"},
        )
        session.add(task)
        session.commit()
        task_id = task.id

    with Session(engine) as session:
        loaded = session.get(models.Task, task_id)
        assert loaded.params == {"stage": "1-7", "medicine": 0}
        assert loaded.raw_params == {"stage": "1-7"}
        loaded.params = {**loaded.params, "times": 3}
        session.add(loaded)
        session.commit()

    with Session(engine) as session:
        assert session.get(models.Task, task_id).params["times"] == 3


def test_nullable_json_none_is_sql_null_not_json_literal(engine, conn):
    """可空 JSON 列的 Python None 必须落成 SQL NULL。

    SQLAlchemy 的 ``JSON`` 默认 ``none_as_null=False``，会把 None 序列化成
    JSON 字面量 ``'null'`` 字符串 —— 那样 ``IS NULL`` 查不到，``resource_asset``
    的 ``content IS NOT NULL OR path IS NOT NULL`` 也会被绕过（M2-03 实测）。
    """
    with Session(engine) as session:
        session.add(
            models.ResourceAsset(kind="custom_task", name="on-disk", path="a/b.json")
        )
        session.add(models.LogEntry(source="server", level="info", content="x"))
        session.commit()

    assert conn.execute(
        "select content, meta from resource_asset where name = 'on-disk'"
    ).fetchone() == (None, None)
    assert (
        conn.execute("select meta from log_entry where content = 'x'").fetchone()[0]
        is None
    )
    assert conn.execute("select count(*) from resource_asset where content is null").fetchone()[0] == 1

    with Session(engine) as session:
        loaded = session.scalars(select(models.ResourceAsset)).one()
        assert loaded.content is None and loaded.meta is None


def test_setting_json_preserves_scalar_types(engine):
    with Session(engine) as session:
        session.add(models.Setting(key="adb.screenshot_quality", value=25))
        session.add(models.Setting(key="adb.address", value="127.0.0.1:5555"))
        session.commit()

    with Session(engine) as session:
        assert session.get(models.Setting, "adb.screenshot_quality").value == 25
        assert session.get(models.Setting, "adb.address").value == "127.0.0.1:5555"


def test_task_params_has_sql_server_default(conn, engine):
    """params 是 NOT NULL JSON，DDL 层带 DEFAULT '{}'（docs/04 §7 示例）。"""
    with Session(engine) as session:
        pipeline = make_pipeline(session)
        pipeline_id = pipeline.id
    conn.execute(
        "insert into task (id, pipeline_id, order_index, type_name, task_name, status,"
        " retry_count, max_retries, retry_delay, created_at)"
        " values (?,?,?,?,?,?,?,?,?,?)",
        (
            str(uuid.uuid4()),
            pipeline_id,
            0,
            "StartUp",
            "开始唤醒",
            "pending",
            0,
            3,
            30,
            "2026-01-01 00:00:00",
        ),
    )
    conn.commit()
    params = conn.execute("select params from task").fetchone()[0]
    assert params == "{}"


# ---------------------------------------------------------------------------
# 默认值与时间戳
# ---------------------------------------------------------------------------
def test_utcnow_is_naive_utc():
    before = datetime.now(UTC).replace(tzinfo=None)
    value = utcnow()
    after = datetime.now(UTC).replace(tzinfo=None)
    assert value.tzinfo is None
    assert before - timedelta(seconds=1) <= value <= after + timedelta(seconds=1)


def test_pipeline_orm_defaults(engine):
    with Session(engine) as session:
        pipeline = models.Pipeline(source="scheduled", priority=2, task_count=3)
        session.add(pipeline)
        session.commit()
        assert pipeline.status == "pending"
        assert pipeline.core_id == "default"
        assert pipeline.notify_on_finish is True
        assert pipeline.core_epoch is None
        assert pipeline.started_at is None
        assert pipeline.finished_at is None
        assert pipeline.created_at.tzinfo is None
        assert (
            datetime.now(UTC).replace(tzinfo=None) - pipeline.created_at
        ).total_seconds() < 10


def test_schedule_and_agent_session_defaults(engine):
    with Session(engine) as session:
        schedule = models.Schedule(name="每日常规", cron="0 7,19 * * *", template=[])
        session.add(schedule)
        agent_session = models.AgentSession(model="gpt-test")
        session.add(agent_session)
        session.commit()
        assert schedule.timezone == "Asia/Shanghai"
        assert schedule.enabled is True
        assert schedule.priority == 2
        assert schedule.misfire_grace_seconds == 300
        assert schedule.catch_up is False
        assert schedule.skip_if_running is True
        assert schedule.updated_at.tzinfo is None
        assert agent_session.status == "active"
        assert agent_session.message_count == 0
        assert agent_session.prompt_tokens == 0
        assert agent_session.completion_tokens == 0
        assert agent_session.atomic_grant_id is None
        assert agent_session.last_message_at is None


def test_screenshot_trigger_column_is_quoted_in_ddl(conn):
    """``trigger`` 是 SQLite 保留字，必须被引用；否则建表就炸。"""
    assert '"TRIGGER" VARCHAR(16) NOT NULL' in ddl(conn, "screenshot")
