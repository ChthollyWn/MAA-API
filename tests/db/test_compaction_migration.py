"""M74：``0028_compaction`` 迁移验收（spec §4.1、§6）。

测试纪律与 ``test_device_lease_migration.py`` 一致：全程用 ``tmp_path`` 的临时库，
只用 Alembic 的 ``upgrade`` / ``downgrade`` 建库与演进，绝不碰
``resource/maa_api.db``。

三条断言：

1. 升级到 0028 后 ``compaction_checkpoint`` 的列名、DDL 类型、可空性与 §4.1 逐列
   一致；主键、两个外键的 ``ondelete``（CASCADE / SET NULL）与
   ``ix_compaction_checkpoint_session_id_created_at`` 都在；
2. **不回填**：0027 上先写入的会话升级后原样保留，检查点表是空的（历史会话没有
   检查点 = 全量历史，不需要补行）；
3. 降回 0027 后表与索引都消失，会话数据不受影响；再升级后表回来且仍是空的。
"""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
PRE_REVISION = "0027_device_lease"
HEAD_REVISION = "0028_compaction"

#: ``compaction_checkpoint`` 列 → (DDL 类型, 是否可空)。顺序与 §4.1 一致。
EXPECTED_COLUMNS = {
    "id": ("VARCHAR(36)", False),
    "session_id": ("VARCHAR(36)", False),
    "run_id": ("VARCHAR(36)", True),
    "upto_seq": ("INTEGER", False),
    "summary": ("TEXT", False),
    "summary_json": ("JSON", False),
    "durable": ("JSON", False),
    "tokens_before": ("INTEGER", False),
    "tokens_after": ("INTEGER", False),
    "model": ("VARCHAR(128)", True),
    "trigger": ("VARCHAR(16)", False),
    "status": ("VARCHAR(16)", False),
    "created_at": ("DATETIME", False),
}

#: 列 → (目标表, ondelete)。会话删了检查点一起删；运行删了只解除关联。
EXPECTED_FOREIGN_KEYS = {
    "session_id": ("agent_session", "CASCADE"),
    "run_id": ("agent_run", "SET NULL"),
}

INDEX_NAME = "ix_compaction_checkpoint_session_id_created_at"


def _config(db_path: Path) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
    rows = conn.execute(f"pragma table_info({table})").fetchall()
    return {row["name"]: row for row in rows}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("select name from sqlite_master where type = 'table'").fetchall()
    return {row["name"] for row in rows}


def _foreign_keys(conn: sqlite3.Connection, table: str) -> dict[str, tuple[str, str]]:
    rows = conn.execute(f"pragma foreign_key_list({table})").fetchall()
    return {row["from"]: (row["table"], row["on_delete"]) for row in rows}


def _index_columns(conn: sqlite3.Connection, table: str) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for row in conn.execute(f"pragma index_list({table})"):
        info = conn.execute(f"pragma index_info({row['name']})").fetchall()
        result[row["name"]] = tuple(item["name"] for item in info)
    return result


def _seed_session(conn: sqlite3.Connection, session_id: str) -> None:
    """插一行 0027 形状的会话（只写 NOT NULL 且无服务端默认值的列）。"""
    conn.execute(
        "insert into agent_session (id, status, model, message_count, prompt_tokens,"
        " completion_tokens, created_at, updated_at)"
        " values (?, 'active', 'test-model', 0, 0, 0,"
        " '2026-11-01 00:00:00', '2026-11-01 00:00:00')",
        (session_id,),
    )
    conn.commit()


def test_upgrade_creates_the_compaction_table(tmp_path: Path) -> None:
    db_path = tmp_path / "compaction_up.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        assert "compaction_checkpoint" in _table_names(conn)

        columns = _columns(conn, "compaction_checkpoint")
        assert tuple(columns) == tuple(EXPECTED_COLUMNS)
        for name, (ddl_type, nullable) in EXPECTED_COLUMNS.items():
            assert columns[name]["type"] == ddl_type, name
            assert columns[name]["notnull"] == (0 if nullable else 1), name
        assert columns["id"]["pk"] == 1

        foreign_keys = _foreign_keys(conn, "compaction_checkpoint")
        assert foreign_keys == EXPECTED_FOREIGN_KEYS

        indexes = _index_columns(conn, "compaction_checkpoint")
        assert indexes[INDEX_NAME] == ("session_id", "created_at")
    finally:
        conn.close()


def test_upgrade_does_not_backfill_checkpoints(tmp_path: Path) -> None:
    db_path = tmp_path / "compaction_backfill.db"
    cfg = _config(db_path)
    command.upgrade(cfg, PRE_REVISION)

    conn = _connect(db_path)
    try:
        _seed_session(conn, "s-legacy")
    finally:
        conn.close()

    command.upgrade(cfg, HEAD_REVISION)
    conn = _connect(db_path)
    try:
        # 历史会话不生成检查点：没有检查点就是「全量历史」，不需要补行。
        count = conn.execute("select count(*) from compaction_checkpoint").fetchone()[0]
        assert count == 0
        session = conn.execute(
            "select id, model from agent_session where id = 's-legacy'"
        ).fetchone()
        assert tuple(session) == ("s-legacy", "test-model")
    finally:
        conn.close()


def test_downgrade_removes_the_compaction_table(tmp_path: Path) -> None:
    db_path = tmp_path / "compaction_down.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        _seed_session(conn, "s-down")
        conn.execute(
            "insert into compaction_checkpoint (id, session_id, run_id, upto_seq,"
            " summary, summary_json, durable, tokens_before, tokens_after, model,"
            " trigger, status, created_at)"
            " values ('cp-1', 's-down', NULL, 12, '【压缩摘要】', '{}', '{}',"
            " 1000, 400, NULL, 'auto', 'active', '2026-11-01 00:00:00')"
        )
        conn.commit()
    finally:
        conn.close()

    command.downgrade(cfg, PRE_REVISION)
    conn = _connect(db_path)
    try:
        tables = _table_names(conn)
        assert "compaction_checkpoint" not in tables
        # 既有会话不受降级影响。
        session = conn.execute(
            "select id, model from agent_session where id = 's-down'"
        ).fetchone()
        assert tuple(session) == ("s-down", "test-model")
    finally:
        conn.close()

    # 再升级：表回来且是空的。
    command.upgrade(cfg, HEAD_REVISION)
    conn = _connect(db_path)
    try:
        assert "compaction_checkpoint" in _table_names(conn)
        count = conn.execute("select count(*) from compaction_checkpoint").fetchone()[0]
        assert count == 0
    finally:
        conn.close()
