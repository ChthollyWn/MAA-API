"""M84：``0037_spend_ledger`` 迁移验收（spec §4.1、§5 T1、§6）。

测试纪律与 ``tests/db/test_watchdog_migration.py`` 一致：全程用 ``tmp_path`` 的临时库，
只用 Alembic 的 ``upgrade`` / ``downgrade`` 建库与演进，绝不碰
``resource/maa_api.db``。

覆盖四件事：

1. 空库升级到 head，``spend_ledger`` 的 9 列、可空性、索引与三条 ``SET NULL`` 外键
   与 §4.1 逐项一致；``id`` 在 DDL 里真的是 ``INTEGER PRIMARY KEY AUTOINCREMENT``；
2. 可以只写必填列插入账本行，``actual`` 与三个外键可空；
3. 降级回到 0036 的形状：表与索引消失，再升级可重放且是空表；
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
PREVIOUS_REVISION = "0036_watchdog"
HEAD_REVISION = "0037_spend_ledger"
TIMESTAMP = "2026-11-05 08:00:00"

#: 列名 → (DDL 类型, 是否 NOT NULL)。顺序与 03 §11.6 一致。
EXPECTED_COLUMNS = {
    "id": ("INTEGER", True),
    "pipeline_id": ("VARCHAR(36)", False),
    "task_id": ("VARCHAR(36)", False),
    "kind": ("VARCHAR(16)", True),
    "planned": ("INTEGER", True),
    "actual": ("INTEGER", False),
    "source": ("VARCHAR(16)", True),
    "ticket_id": ("VARCHAR(36)", False),
    "created_at": ("DATETIME", True),
}

#: 索引名 → (列序, 是否唯一)。
EXPECTED_INDEXES = {
    "ix_spend_ledger_kind_created_at": (("kind", "created_at"), False),
}

#: 本地列 → (目标表, 目标列, on_delete)。三条都只解除关联，不带走账本行。
EXPECTED_FOREIGN_KEYS = {
    "pipeline_id": ("pipeline", "id", "SET NULL"),
    "task_id": ("task", "id", "SET NULL"),
    "ticket_id": ("ticket", "id", "SET NULL"),
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


def _foreign_keys(
    conn: sqlite3.Connection, table: str
) -> dict[str, tuple[str, str, str]]:
    return {
        row["from"]: (row["table"], row["to"], row["on_delete"])
        for row in conn.execute(f"pragma foreign_key_list({table})")
    }


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


def _insert_ledger(
    conn: sqlite3.Connection,
    *,
    kind: str = "medicine",
    planned: int = 2,
    source: str = "agent",
    pipeline_id: str | None = None,
    task_id: str | None = None,
    ticket_id: str | None = None,
    actual: int | None = None,
) -> None:
    conn.execute(
        'insert into spend_ledger'
        ' (pipeline_id, task_id, kind, planned, actual, source, ticket_id, created_at)'
        " values (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            pipeline_id,
            task_id,
            kind,
            planned,
            actual,
            source,
            ticket_id,
            TIMESTAMP,
        ),
    )


def test_upgrade_creates_spend_ledger(tmp_path: Path) -> None:
    db_path = tmp_path / "spend_upgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        assert "spend_ledger" in _table_names(conn)
        assert _columns(conn, "spend_ledger") == EXPECTED_COLUMNS
        assert _indexes(conn, "spend_ledger") == EXPECTED_INDEXES
        assert _foreign_keys(conn, "spend_ledger") == EXPECTED_FOREIGN_KEYS
        table_sql = _table_sql(conn, "spend_ledger")
        assert "AUTOINCREMENT" in table_sql
        assert "PRIMARY KEY" in table_sql
        # 命名约定与模型一致（docs/04 §3.2）；SQLite 的整数主键是行内写法，
        # 不落命名约束，主键名只在模型元数据里。
        for name in (
            "FK_SPEND_LEDGER_PIPELINE_ID_PIPELINE",
            "FK_SPEND_LEDGER_TASK_ID_TASK",
            "FK_SPEND_LEDGER_TICKET_ID_TICKET",
        ):
            assert name in table_sql

        # 三个外键与 actual 都可空；必填列齐了就能插。
        _insert_ledger(conn)
        _insert_ledger(conn, kind="stone", planned=1, source="scheduled", actual=1)
        conn.commit()
        rows = conn.execute(
            "select id, pipeline_id, task_id, kind, planned, actual, source,"
            " ticket_id from spend_ledger order by id"
        ).fetchall()
        assert [row["id"] for row in rows] == [1, 2]
        assert rows[0]["pipeline_id"] is None
        assert rows[0]["task_id"] is None
        assert rows[0]["ticket_id"] is None
        assert rows[0]["actual"] is None
        assert rows[0]["kind"] == "medicine"
        assert rows[0]["planned"] == 2
        assert rows[0]["source"] == "agent"
        assert rows[1]["actual"] == 1
        assert rows[1]["source"] == "scheduled"
    finally:
        conn.close()


def test_downgrade_drops_table_and_upgrade_replays(tmp_path: Path) -> None:
    db_path = tmp_path / "spend_downgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        _insert_ledger(conn, kind="expedite", planned=4, source="manual")
        conn.commit()
    finally:
        conn.close()

    command.downgrade(cfg, PREVIOUS_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == PREVIOUS_REVISION
        assert "spend_ledger" not in _table_names(conn)
        # 0036 的表还在，降级只删本迁移的表。
        assert {"watchdog_alert", "goal", "ticket", "pipeline", "task"} <= (
            _table_names(conn)
        )
    finally:
        conn.close()

    command.upgrade(cfg, HEAD_REVISION)
    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        assert "spend_ledger" in _table_names(conn)
        assert conn.execute("select count(*) from spend_ledger").fetchone()[0] == 0
        # 纯新增：重放不假装回填历史流水线的消耗。
        _insert_ledger(conn)
        conn.commit()
        assert conn.execute("select id from spend_ledger").fetchone()["id"] == 1
    finally:
        conn.close()


def test_offline_render(tmp_path: Path) -> None:
    db_path = tmp_path / "spend_offline.db"
    cfg = _config(db_path)

    upgrade_sql = io.StringIO()
    with contextlib.redirect_stdout(upgrade_sql):
        command.upgrade(cfg, f"{PREVIOUS_REVISION}:{HEAD_REVISION}", sql=True)
    upgrade_text = upgrade_sql.getvalue()

    assert "CREATE TABLE spend_ledger" in upgrade_text
    assert "AUTOINCREMENT" in upgrade_text
    assert "kind VARCHAR(16) NOT NULL" in upgrade_text
    assert "planned INTEGER NOT NULL" in upgrade_text
    assert "ix_spend_ledger_kind_created_at" in upgrade_text
    assert "ON DELETE SET NULL" in upgrade_text
    assert not db_path.exists(), "离线模式不应创建数据库文件"

    downgrade_sql = io.StringIO()
    with contextlib.redirect_stdout(downgrade_sql):
        command.downgrade(cfg, f"{HEAD_REVISION}:{PREVIOUS_REVISION}", sql=True)
    downgrade_text = downgrade_sql.getvalue()

    assert "DROP INDEX ix_spend_ledger_kind_created_at" in downgrade_text
    assert "DROP TABLE spend_ledger" in downgrade_text
    assert not db_path.exists(), "离线模式不应创建数据库文件"
