"""M66：``0023_agent_runs`` 迁移验收。

测试纪律与 ``tests/db/test_skland_only_operators_migration.py`` 一致：全程用
``tmp_path`` 的临时库，只用 Alembic 的 ``upgrade`` / ``downgrade`` 建库与演进，
绝不碰 ``resource/maa_api.db``。

覆盖六件事（spec §6）：

1. 升级建出 ``agent_run``，``incident_key`` 非空的诊断会话 ``kind='incident'``；
2. 消息行与 ``AUTOINCREMENT`` / ``sqlite_sequence`` 在 batch 重建后不丢；
3. 四个新外键的 ``ON DELETE`` 与规格一致；
4. 活动运行的部分唯一索引的语义（终态历史可堆积、NULL 会话不受限）；
5. 降级回到 0022 的形状，行数与序列不变；
6. 离线 ``--sql`` 能渲染出本迁移的语句且不建库文件。

另有一条「重复升级 / 降级到底再升级」的回归用例：迁移必须可重放。
"""

import contextlib
import io
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
PRE_REVISION = "0022_skland_only_operators"
HEAD_REVISION = "0023_agent_runs"

#: 0022 上这两张表的完整列序；降级与重建后必须逐字回到这个形状。
AGENT_SESSION_0022_COLUMNS = [
    "id",
    "title",
    "status",
    "model",
    "base_url",
    "message_count",
    "prompt_tokens",
    "completion_tokens",
    "atomic_grant_id",
    "atomic_grant_expires_at",
    "created_at",
    "updated_at",
    "last_message_at",
    "incident_key",
]
AGENT_MESSAGE_0022_COLUMNS = [
    "id",
    "session_id",
    "seq",
    "role",
    "content",
    "tool_calls",
    "tool_call_id",
    "tool_name",
    "audit_id",
    "prompt_tokens",
    "completion_tokens",
    "finish_reason",
    "latency_ms",
    "created_at",
    "reasoning_content",
]
#: 0023 建出的 agent_run 列序。M69 的 0026 会给它加 ``ticket_id``；本用例只验
#: 0023 这一步的形状，所以按当时的历史清单钉死，不跟当前模型 metadata 漂移。
AGENT_RUN_0023_COLUMNS = [
    "id",
    "session_id",
    "parent_run_id",
    "root_run_id",
    "agent_name",
    "spec_version",
    "kind",
    "status",
    "trigger_seq",
    "model",
    "model_profile",
    "prompt_tokens",
    "completion_tokens",
    "cached_tokens",
    "reasoning_tokens",
    "cost_micros",
    "currency",
    "steps",
    "finish_reason",
    "error_code",
    "error_message",
    "boot_id",
    "heartbeat_at",
    "started_at",
    "finished_at",
    "meta",
]

#: agent_run 的非空列（无 server_default），测试里按它拼 INSERT。
AGENT_RUN_REQUIRED_COLUMNS = (
    "id",
    "root_run_id",
    "agent_name",
    "kind",
    "status",
    "boot_id",
    "prompt_tokens",
    "completion_tokens",
    "cached_tokens",
    "reasoning_tokens",
    "cost_micros",
    "steps",
    "started_at",
)


def _config(db_path: Path) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _revision(conn: sqlite3.Connection) -> str:
    return conn.execute("select version_num from alembic_version").fetchone()[
        "version_num"
    ]


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"pragma table_info({table})")]


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in conn.execute("select name from sqlite_master where type = 'table'")
    }


def _ddl(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute("select sql from sqlite_master where name = ?", (name,)).fetchone()
    assert row is not None and row["sql"], f"sqlite_master 里没有 {name}"
    return row["sql"].upper()


def _seed_sessions(conn: sqlite3.Connection) -> None:
    """一个普通对话、一个诊断会话（``incident_key`` 非空）。"""
    for session_id, incident_key in (
        ("session-chat", None),
        ("session-incident", "incident-1"),
    ):
        conn.execute(
            "insert into agent_session (id, status, model, message_count,"
            " prompt_tokens, completion_tokens, created_at, updated_at, incident_key)"
            " values (?, 'active', 'gpt-test', 0, 0, 0,"
            " '2026-01-01 00:00:00', '2026-01-01 00:00:00', ?)",
            (session_id, incident_key),
        )
    conn.commit()


def _seed_messages(conn: sqlite3.Connection, count: int = 3) -> None:
    for seq in range(count):
        conn.execute(
            "insert into agent_message (session_id, seq, role, content, created_at)"
            " values ('session-chat', ?, 'user', ?, '2026-01-01 00:00:00')",
            (seq, f"message-{seq}"),
        )
    conn.commit()


def _insert_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    session_id: str | None = None,
    status: str = "running",
    root_run_id: str | None = None,
) -> None:
    values = (
        run_id,
        root_run_id or run_id,
        "legacy_chat",
        "legacy_turn",
        status,
        "boot-1",
        0,
        0,
        0,
        0,
        0,
        0,
        "2026-01-01 00:00:00",
    )
    columns = ", ".join(AGENT_RUN_REQUIRED_COLUMNS)
    placeholders = ", ".join("?" for _ in AGENT_RUN_REQUIRED_COLUMNS)
    conn.execute(
        f"insert into agent_run ({columns}, session_id) values ({placeholders}, ?)",
        (*values, session_id),
    )


# ---------------------------------------------------------------------------
# 升级
# ---------------------------------------------------------------------------
def test_upgrade_creates_agent_run_and_marks_incident_sessions(tmp_path: Path) -> None:
    db_path = tmp_path / "agent_runs_upgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, PRE_REVISION)

    conn = _connect(db_path)
    try:
        _seed_sessions(conn)
    finally:
        conn.close()

    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        kinds = {
            row["id"]: row["kind"]
            for row in conn.execute("select id, kind from agent_session")
        }
        assert kinds == {"session-chat": "chat", "session-incident": "incident"}
        assert _columns(conn, "agent_run") == AGENT_RUN_0023_COLUMNS
    finally:
        conn.close()


def test_upgrade_keeps_messages_and_autoincrement_sequence(tmp_path: Path) -> None:
    db_path = tmp_path / "agent_runs_sequence.db"
    cfg = _config(db_path)
    command.upgrade(cfg, PRE_REVISION)

    conn = _connect(db_path)
    try:
        _seed_sessions(conn)
        _seed_messages(conn, count=4)
        # 删掉尾部 id：序列停在 4，重建时若不先存后还会退回 3。
        conn.execute("delete from agent_message where id = 4")
        conn.commit()
        assert [
            row["id"] for row in conn.execute("select id from agent_message order by id")
        ] == [1, 2, 3]
        assert conn.execute(
            "select seq from sqlite_sequence where name = 'agent_message'"
        ).fetchone()["seq"] == 4
    finally:
        conn.close()

    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        assert [
            row["id"] for row in conn.execute("select id from agent_message order by id")
        ] == [1, 2, 3]
        assert "AUTOINCREMENT" in _ddl(conn, "agent_message")
        assert conn.execute(
            "select seq from sqlite_sequence where name = 'agent_message'"
        ).fetchone()["seq"] == 4
        conn.execute(
            "insert into agent_message (session_id, seq, role, content, created_at)"
            " values ('session-chat', 3, 'assistant', 'after', '2026-01-01 00:00:01')"
        )
        conn.commit()
        assert conn.execute("select max(id) from agent_message").fetchone()[0] == 5
    finally:
        conn.close()


def test_upgrade_adds_foreign_keys_with_expected_ondelete(tmp_path: Path) -> None:
    db_path = tmp_path / "agent_runs_foreign_keys.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        def foreign_keys(table: str) -> set[tuple[str, str, str]]:
            return {
                (row["from"], row["table"], row["on_delete"])
                for row in conn.execute(f"pragma foreign_key_list({table})")
            }

        assert ("parent_session_id", "agent_session", "SET NULL") in foreign_keys(
            "agent_session"
        )
        assert ("run_id", "agent_run", "SET NULL") in foreign_keys("agent_message")
        assert ("session_id", "agent_session", "CASCADE") in foreign_keys("agent_run")
        assert ("parent_run_id", "agent_run", "SET NULL") in foreign_keys("agent_run")
    finally:
        conn.close()


def test_active_run_partial_unique_index_allows_history(tmp_path: Path) -> None:
    db_path = tmp_path / "agent_runs_partial_index.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        _seed_sessions(conn)
        # 同一会话：一条终态历史 + 一条活动运行，允许。
        _insert_run(conn, run_id="run-history", session_id="session-chat", status="succeeded")
        _insert_run(conn, run_id="run-active", session_id="session-chat")
        conn.commit()

        # 第二条活动运行（waiting_human 也算活动）被部分唯一索引拒绝。
        with pytest.raises(sqlite3.IntegrityError):
            _insert_run(conn, run_id="run-waiting", session_id="session-chat", status="waiting_human")
        conn.rollback()

        # session_id 为空的运行没有会话归属，多条同时 running 合法。
        _insert_run(conn, run_id="run-global-1")
        _insert_run(conn, run_id="run-global-2")
        conn.commit()
        assert conn.execute(
            "select count(*) from agent_run where session_id is null"
        ).fetchone()[0] == 2
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 降级与离线渲染
# ---------------------------------------------------------------------------
def test_downgrade_restores_0022_shape(tmp_path: Path) -> None:
    db_path = tmp_path / "agent_runs_downgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, PRE_REVISION)

    conn = _connect(db_path)
    try:
        _seed_sessions(conn)
        _seed_messages(conn, count=3)
    finally:
        conn.close()

    conn = _connect(db_path)
    try:
        before_counts = (
            conn.execute("select count(*) from agent_session").fetchone()[0],
            conn.execute("select count(*) from agent_message").fetchone()[0],
        )
        before_ids = [
            row["id"] for row in conn.execute("select id from agent_message order by id")
        ]
        before_sequence = conn.execute(
            "select seq from sqlite_sequence where name = 'agent_message'"
        ).fetchone()["seq"]
    finally:
        conn.close()

    command.upgrade(cfg, HEAD_REVISION)
    command.downgrade(cfg, PRE_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == PRE_REVISION
        assert "agent_run" not in _table_names(conn)
        assert _columns(conn, "agent_session") == AGENT_SESSION_0022_COLUMNS
        assert _columns(conn, "agent_message") == AGENT_MESSAGE_0022_COLUMNS
        assert (
            conn.execute("select count(*) from agent_session").fetchone()[0],
            conn.execute("select count(*) from agent_message").fetchone()[0],
        ) == before_counts
        assert [
            row["id"] for row in conn.execute("select id from agent_message order by id")
        ] == before_ids
        assert conn.execute(
            "select seq from sqlite_sequence where name = 'agent_message'"
        ).fetchone()["seq"] == before_sequence
    finally:
        conn.close()


def test_offline_render_includes_agent_run(tmp_path: Path) -> None:
    """离线 ``upgrade 0022:0023 --sql`` 没有连接可反射，``copy_from`` 必须生效。"""
    db_path = tmp_path / "agent_runs_offline.db"
    cfg = _config(db_path)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        command.upgrade(cfg, f"{PRE_REVISION}:{HEAD_REVISION}", sql=True)
    sql = buffer.getvalue()

    assert "CREATE TABLE agent_run" in sql
    assert "SET kind = 'incident'" in sql
    # batch 重建的临时表与 AUTOINCREMENT 关键字都在渲染里。
    assert "_alembic_tmp_agent_message" in sql
    assert "AUTOINCREMENT" in sql
    assert not db_path.exists(), "离线模式不应创建数据库文件"


# ---------------------------------------------------------------------------
# 可重放性
# ---------------------------------------------------------------------------
def test_repeated_upgrade_and_downgrade_to_base_then_upgrade(tmp_path: Path) -> None:
    """升级可重复；降级到底再升级能恢复 schema（docs/12 §4）。"""
    db_path = tmp_path / "agent_runs_repeat.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)
    command.upgrade(cfg, HEAD_REVISION)  # 幂等：不炸 table already exists

    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        assert "agent_run" in _table_names(conn)
    finally:
        conn.close()

    command.downgrade(cfg, "base")
    conn = _connect(db_path)
    try:
        assert conn.execute("select version_num from alembic_version").fetchall() == []
        assert "agent_run" not in _table_names(conn)
        assert "agent_session" not in _table_names(conn)
    finally:
        conn.close()

    command.upgrade(cfg, HEAD_REVISION)
    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        assert _columns(conn, "agent_run") == AGENT_RUN_0023_COLUMNS
        assert "AUTOINCREMENT" in _ddl(conn, "agent_message")
    finally:
        conn.close()
