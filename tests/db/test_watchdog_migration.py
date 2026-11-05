"""M83：``0036_watchdog`` 迁移验收（spec §4.1、§5 T1）。

测试纪律与 ``tests/db/test_goals_migration.py`` 一致：全程用 ``tmp_path`` 的临时库，
只用 Alembic 的 ``upgrade`` / ``downgrade`` 建库与演进，绝不碰
``resource/maa_api.db``。

覆盖四件事：

1. 空库升级到 head，``watchdog_alert`` 的列、可空、两条索引与 §4.1 逐项一致；
   ``id`` 在 DDL 里真的是 ``INTEGER PRIMARY KEY AUTOINCREMENT``；
2. 可重复插入（同一目标允许留历史行），``evidence`` 不写时默认 ``{}``；
3. 降级回到 0035 的形状：表与索引消失，再升级可重放且是空表；
4. 离线 ``--sql`` 能渲染升级与降级，且不建库文件。
"""

from __future__ import annotations

import contextlib
import io
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
PREVIOUS_REVISION = "0035_goals"
HEAD_REVISION = "0036_watchdog"
TIMESTAMP = "2026-03-01 12:00:00"

#: 列名 → (DDL 类型, 是否 NOT NULL)。顺序与 03 §11.5 一致。
EXPECTED_COLUMNS = {
    "id": ("INTEGER", True),
    "check": ("VARCHAR(48)", True),
    "severity": ("VARCHAR(16)", True),
    "target_kind": ("VARCHAR(16)", True),
    "target_id": ("VARCHAR(64)", False),
    "message": ("TEXT", True),
    "evidence": ("JSON", True),
    "action": ("VARCHAR(32)", False),
    "action_status": ("VARCHAR(16)", False),
    "created_at": ("DATETIME", True),
    "resolved_at": ("DATETIME", False),
}

#: 索引名 → (列序, 是否唯一)。
EXPECTED_INDEXES = {
    "ix_watchdog_alert_created_at": (("created_at",), False),
    "ix_watchdog_alert_target_kind_target_id": (("target_kind", "target_id"), False),
}


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


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, tuple[str, bool]]:
    return {
        row["name"]: (row["type"], bool(row["notnull"]))
        for row in conn.execute(f"pragma table_info({table})")
    }


def _indexes(
    conn: sqlite3.Connection, table: str
) -> dict[str, tuple[tuple[str, ...], bool]]:
    result: dict[str, tuple[tuple[str, ...], bool]] = {}
    for row in conn.execute(f"pragma index_list({table})"):
        if row["name"].startswith("sqlite_autoindex"):
            continue
        columns = tuple(
            info["name"] for info in conn.execute(f"pragma index_info({row['name']})")
        )
        result[row["name"]] = (columns, bool(row["unique"]))
    return result


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in conn.execute("select name from sqlite_master where type='table'")
    }


def _table_sql(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute(
        "select sql from sqlite_master where type='table' and name=?", (table,)
    ).fetchone()
    assert row is not None and row["sql"], f"sqlite_master 里没有 {table}"
    return row["sql"].upper()


def _insert_alert(
    conn: sqlite3.Connection,
    *,
    check: str,
    severity: str,
    target_kind: str,
    target_id: str | None,
    message: str = "告警",
    evidence: str | None = None,
    action: str | None = None,
    action_status: str | None = None,
    resolved_at: str | None = None,
) -> None:
    columns = [
        '"check"',
        "severity",
        "target_kind",
        "target_id",
        "message",
        "action",
        "action_status",
        "created_at",
        "resolved_at",
    ]
    values: list[object] = [
        check,
        severity,
        target_kind,
        target_id,
        message,
        action,
        action_status,
        TIMESTAMP,
        resolved_at,
    ]
    if evidence is not None:
        columns.append("evidence")
        values.append(evidence)
    placeholders = ", ".join("?" * len(columns))
    conn.execute(
        f"insert into watchdog_alert ({', '.join(columns)}) values ({placeholders})",
        tuple(values),
    )


def test_upgrade_creates_watchdog_alert(tmp_path: Path) -> None:
    db_path = tmp_path / "watchdog_upgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        assert "watchdog_alert" in _table_names(conn)
        assert _columns(conn, "watchdog_alert") == EXPECTED_COLUMNS
        assert _indexes(conn, "watchdog_alert") == EXPECTED_INDEXES
        table_sql = _table_sql(conn, "watchdog_alert")
        assert "AUTOINCREMENT" in table_sql
        assert "PRIMARY KEY" in table_sql

        # evidence 有 server default；id 是自增整数。
        _insert_alert(
            conn,
            check="run_heartbeat_stale",
            severity="warn",
            target_kind="run",
            target_id="run-1",
        )
        _insert_alert(
            conn,
            check="run_heartbeat_stale",
            severity="warn",
            target_kind="run",
            target_id="run-1",
            resolved_at=TIMESTAMP,
        )
        conn.commit()
        rows = conn.execute(
            "select id, evidence, action, action_status, resolved_at"
            " from watchdog_alert order by id"
        ).fetchall()
        assert [row["id"] for row in rows] == [1, 2]
        assert rows[0]["evidence"] == "{}"
        assert rows[0]["action"] is None
        assert rows[0]["action_status"] is None
        assert rows[1]["resolved_at"] == TIMESTAMP
    finally:
        conn.close()


def test_downgrade_removes_table_and_upgrade_replays(tmp_path: Path) -> None:
    db_path = tmp_path / "watchdog_downgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        _insert_alert(
            conn,
            check="pipeline_no_callback",
            severity="critical",
            target_kind="device",
            target_id="device-1",
            action="stop_pipeline",
            action_status="done",
        )
        conn.commit()
    finally:
        conn.close()

    command.downgrade(cfg, PREVIOUS_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == PREVIOUS_REVISION
        assert "watchdog_alert" not in _table_names(conn)
        # 0035 的表还在，降级只删本迁移的表。
        assert {"goal", "goal_milestone"} <= _table_names(conn)
    finally:
        conn.close()

    command.upgrade(cfg, HEAD_REVISION)
    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        assert "watchdog_alert" in _table_names(conn)
        assert conn.execute("select count(*) from watchdog_alert").fetchone()[0] == 0
        # 自增序列随建表重新开始。
        _insert_alert(
            conn,
            check="queue_stuck",
            severity="critical",
            target_kind="queue",
            target_id="default",
        )
        conn.commit()
        assert (
            conn.execute("select id from watchdog_alert").fetchone()["id"] == 1
        )
    finally:
        conn.close()


def test_offline_render(tmp_path: Path) -> None:
    db_path = tmp_path / "watchdog_offline.db"
    cfg = _config(db_path)

    upgrade_sql = io.StringIO()
    with contextlib.redirect_stdout(upgrade_sql):
        command.upgrade(cfg, f"{PREVIOUS_REVISION}:{HEAD_REVISION}", sql=True)
    upgrade_text = upgrade_sql.getvalue()

    assert "CREATE TABLE watchdog_alert" in upgrade_text
    assert "AUTOINCREMENT" in upgrade_text
    assert '"check" VARCHAR(48) NOT NULL' in upgrade_text
    assert "ix_watchdog_alert_created_at" in upgrade_text
    assert "ix_watchdog_alert_target_kind_target_id" in upgrade_text
    assert not db_path.exists(), "离线模式不应创建数据库文件"

    downgrade_sql = io.StringIO()
    with contextlib.redirect_stdout(downgrade_sql):
        command.downgrade(cfg, f"{HEAD_REVISION}:{PREVIOUS_REVISION}", sql=True)
    downgrade_text = downgrade_sql.getvalue()

    assert "DROP INDEX ix_watchdog_alert_target_kind_target_id" in downgrade_text
    assert "DROP INDEX ix_watchdog_alert_created_at" in downgrade_text
    assert "DROP TABLE watchdog_alert" in downgrade_text
    assert not db_path.exists(), "离线模式不应创建数据库文件"
