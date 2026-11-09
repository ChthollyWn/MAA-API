"""M89：``0042_demonstration`` 迁移验收（spec §4.1、§5 T1、§6）。

测试纪律与其它迁移验收用例一致：全程用 ``tmp_path`` 的临时库，只用 Alembic 的
``upgrade`` / ``downgrade`` 建库与演进，绝不碰 ``resource/maa_api.db``。

覆盖三件事：

1. 空库升级到 0042：``demonstration`` 七列的类型与可空性逐项照 spec §4.1，
   主键约束名是 ``pk_demonstration``，没有业务索引（03 §12 不列索引）；
2. 模型默认值：``steps`` 默认空数组、``converted_to`` 默认 ``None``；
   降级回 0041 后表消失，再升级是空表；
3. 离线 ``--sql`` 能渲染升级与降级，且不建库文件。
"""

from __future__ import annotations

import contextlib
import io
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlmodel import Session

from maa_api.db.models import Demonstration
from maa_api.domain.enums import DemonstrationStatus

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
PREVIOUS_REVISION = "0041_nav_graph"
HEAD_REVISION = "0042_demonstration"
TIMESTAMP = "2026-11-10 08:00:00"

#: 列名 → (DDL 类型, 是否 NOT NULL)。顺序与 spec §4.1 一致。
EXPECTED_COLUMNS = {
    "id": ("VARCHAR(36)", True),
    "title": ("VARCHAR(64)", True),
    "status": ("VARCHAR(16)", True),
    "steps": ("JSON", True),
    "converted_to": ("JSON", False),
    "created_at": ("DATETIME", True),
    "updated_at": ("DATETIME", True),
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


def _business_indexes(conn: sqlite3.Connection, table: str) -> dict[str, tuple]:
    """非 SQLite 自建的索引；自增主键的 autoindex 不算业务索引。"""
    result: dict[str, tuple] = {}
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


def test_upgrade_creates_demonstration_table(tmp_path: Path) -> None:
    db_path = tmp_path / "demonstration_upgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        assert "demonstration" in _table_names(conn)
        assert _columns(conn, "demonstration") == EXPECTED_COLUMNS
        # 纯新增表，不建业务索引（03 §12 不列索引）。
        assert _business_indexes(conn, "demonstration") == {}
        assert "CONSTRAINT PK_DEMONSTRATION PRIMARY KEY (ID)" in _table_sql(
            conn, "demonstration"
        )
    finally:
        conn.close()


def test_defaults_and_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "demonstration_defaults.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with Session(engine) as session:
            row = Demonstration(title="示教 11-10 08:00")
            session.add(row)
            session.commit()
            session.refresh(row)
            assert row.steps == []
            assert row.converted_to is None
            assert row.status == DemonstrationStatus.RECORDING
    finally:
        engine.dispose()

    command.downgrade(cfg, PREVIOUS_REVISION)
    conn = _connect(db_path)
    try:
        assert _revision(conn) == PREVIOUS_REVISION
        assert "demonstration" not in _table_names(conn)
        # 0041 的表还在，降级只删本迁移的一张表。
        assert {"nav_node", "nav_edge", "pipeline"} <= _table_names(conn)
    finally:
        conn.close()

    command.upgrade(cfg, HEAD_REVISION)
    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        assert conn.execute("select count(*) from demonstration").fetchone()[0] == 0
        conn.execute(
            "insert into demonstration"
            " (id, title, status, steps, converted_to, created_at, updated_at)"
            " values ('demo-1', '示教 11-10 08:00', 'recording', '[]', null, ?, ?)",
            (TIMESTAMP, TIMESTAMP),
        )
        conn.commit()
        row = conn.execute("select * from demonstration").fetchone()
        assert row["steps"] == "[]"
        assert row["converted_to"] is None
        assert row["created_at"] == TIMESTAMP
    finally:
        conn.close()


def test_offline_sql_renders_both_directions(tmp_path: Path) -> None:
    db_path = tmp_path / "demonstration_offline.db"
    cfg = _config(db_path)

    upgrade_sql = io.StringIO()
    with contextlib.redirect_stdout(upgrade_sql):
        command.upgrade(cfg, f"{PREVIOUS_REVISION}:{HEAD_REVISION}", sql=True)
    upgrade_text = upgrade_sql.getvalue()

    assert "CREATE TABLE demonstration" in upgrade_text
    assert "pk_demonstration" in upgrade_text
    assert "steps JSON NOT NULL" in upgrade_text
    assert "converted_to JSON" in upgrade_text
    assert not db_path.exists(), "离线模式不应创建数据库文件"

    downgrade_sql = io.StringIO()
    with contextlib.redirect_stdout(downgrade_sql):
        command.downgrade(cfg, f"{HEAD_REVISION}:{PREVIOUS_REVISION}", sql=True)
    downgrade_text = downgrade_sql.getvalue()

    assert "DROP TABLE demonstration" in downgrade_text
    assert not db_path.exists(), "离线模式不应创建数据库文件"
