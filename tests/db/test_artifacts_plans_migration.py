"""M75：``0029_artifacts_plans`` 迁移验收（spec §4.1、§6）。

测试纪律与 ``test_compaction_migration.py`` 一致：全程用 ``tmp_path`` 的临时库，
只用 Alembic 的 ``upgrade`` / ``downgrade`` 建库与演进，绝不碰
``resource/maa_api.db``。

三条断言：

1. 升级到 0029 后 ``artifact`` / ``run_plan`` 的列名、DDL 类型、可空性与 spec §4.1
   逐列一致；主键、外键的 ``ondelete``（SET NULL / CASCADE）与三条索引都在，
   ``uq_run_plan_session_id_active`` 是带谓词的部分唯一索引；
2. 部分唯一索引的语义成立：同一会话可以留多条终态历史计划，但未结束计划只能有一条；
3. 降回 0028 后两张表与索引都消失，会话数据不受影响；再升级后表回来且是空的。
"""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
PRE_REVISION = "0028_compaction"
HEAD_REVISION = "0029_artifacts_plans"

#: ``artifact`` 列 → (DDL 类型, 是否可空)。顺序与 §8.2 一致。
EXPECTED_ARTIFACT_COLUMNS = {
    "id": ("VARCHAR(36)", False),
    "session_id": ("VARCHAR(36)", True),
    "ticket_id": ("VARCHAR(36)", True),
    "run_id": ("VARCHAR(36)", True),
    "kind": ("VARCHAR(24)", False),
    "title": ("VARCHAR(120)", False),
    "mime": ("VARCHAR(64)", False),
    "size_bytes": ("INTEGER", False),
    "storage": ("VARCHAR(16)", False),
    "content": ("TEXT", True),
    "path": ("VARCHAR(255)", True),
    "sha256": ("VARCHAR(64)", False),
    "meta": ("JSON", False),
    "pinned": ("BOOLEAN", False),
    "created_at": ("DATETIME", False),
    "updated_at": ("DATETIME", False),
}

#: ``run_plan`` 列 → (DDL 类型, 是否可空)。顺序与 §8.3 一致。
EXPECTED_RUN_PLAN_COLUMNS = {
    "id": ("VARCHAR(36)", False),
    "session_id": ("VARCHAR(36)", False),
    "ticket_id": ("VARCHAR(36)", True),
    "title": ("VARCHAR(120)", False),
    "items": ("JSON", False),
    "status": ("VARCHAR(16)", False),
    "approved_by": ("VARCHAR(32)", True),
    "approved_at": ("DATETIME", True),
    "created_at": ("DATETIME", False),
    "updated_at": ("DATETIME", False),
}

#: 列 → (目标表, ondelete)。产物三个关联都 SET NULL；计划随会话删除，工单删除只解除关联。
EXPECTED_ARTIFACT_FOREIGN_KEYS = {
    "session_id": ("agent_session", "SET NULL"),
    "ticket_id": ("ticket", "SET NULL"),
    "run_id": ("agent_run", "SET NULL"),
}
EXPECTED_RUN_PLAN_FOREIGN_KEYS = {
    "session_id": ("agent_session", "CASCADE"),
    "ticket_id": ("ticket", "SET NULL"),
}

ARTIFACT_INDEXES = {
    "ix_artifact_ticket_id_created_at": ("ticket_id", "created_at"),
    "ix_artifact_session_id_created_at": ("session_id", "created_at"),
}
RUN_PLAN_INDEX = "uq_run_plan_session_id_active"
RUN_PLAN_PREDICATE = "status IN ('draft','awaiting_approval','active')"


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
    rows = conn.execute(
        "select name from sqlite_master where type = 'table'"
    ).fetchall()
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
    """插一行既有形状的会话（只写 NOT NULL 且无服务端默认值的列）。"""
    conn.execute(
        "insert into agent_session (id, status, model, message_count, prompt_tokens,"
        " completion_tokens, created_at, updated_at)"
        " values (?, 'active', 'test-model', 0, 0, 0,"
        " '2026-11-01 00:00:00', '2026-11-01 00:00:00')",
        (session_id,),
    )
    conn.commit()


def _insert_plan(
    conn: sqlite3.Connection, plan_id: str, session_id: str, status: str
) -> None:
    conn.execute(
        "insert into run_plan (id, session_id, ticket_id, title, items, status,"
        " approved_by, approved_at, created_at, updated_at)"
        " values (?, ?, NULL, '三步计划', '[]', ?, NULL, NULL,"
        " '2026-11-01 00:00:00', '2026-11-01 00:00:00')",
        (plan_id, session_id, status),
    )


def test_upgrade_creates_artifacts_and_plans_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "artifacts_plans_up.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        tables = _table_names(conn)
        assert "artifact" in tables
        assert "run_plan" in tables

        artifact_columns = _columns(conn, "artifact")
        assert tuple(artifact_columns) == tuple(EXPECTED_ARTIFACT_COLUMNS)
        for name, (ddl_type, nullable) in EXPECTED_ARTIFACT_COLUMNS.items():
            assert artifact_columns[name]["type"] == ddl_type, name
            assert artifact_columns[name]["notnull"] == (0 if nullable else 1), name
        assert artifact_columns["id"]["pk"] == 1
        assert _foreign_keys(conn, "artifact") == EXPECTED_ARTIFACT_FOREIGN_KEYS
        artifact_indexes = _index_columns(conn, "artifact")
        for name, columns in ARTIFACT_INDEXES.items():
            assert artifact_indexes[name] == columns

        plan_columns = _columns(conn, "run_plan")
        assert tuple(plan_columns) == tuple(EXPECTED_RUN_PLAN_COLUMNS)
        for name, (ddl_type, nullable) in EXPECTED_RUN_PLAN_COLUMNS.items():
            assert plan_columns[name]["type"] == ddl_type, name
            assert plan_columns[name]["notnull"] == (0 if nullable else 1), name
        assert plan_columns["id"]["pk"] == 1
        assert _foreign_keys(conn, "run_plan") == EXPECTED_RUN_PLAN_FOREIGN_KEYS

        run_plan_index = {
            row["name"]: row for row in conn.execute("pragma index_list(run_plan)")
        }[RUN_PLAN_INDEX]
        assert run_plan_index["unique"] == 1
        assert run_plan_index["partial"] == 1
        assert _index_columns(conn, "run_plan")[RUN_PLAN_INDEX] == ("session_id",)
        ddl = conn.execute(
            "select sql from sqlite_master where name = ?", (RUN_PLAN_INDEX,)
        ).fetchone()[0]
        assert RUN_PLAN_PREDICATE in ddl
    finally:
        conn.close()


def test_partial_unique_index_allows_terminal_history_for_one_session(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "artifacts_plans_unique.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        _seed_session(conn, "s-plan")
        # 终态历史可以堆积。
        _insert_plan(conn, "p-done", "s-plan", "done")
        _insert_plan(conn, "p-abandoned", "s-plan", "abandoned")
        # 未结束计划只能有一条。
        _insert_plan(conn, "p-active-1", "s-plan", "active")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            _insert_plan(conn, "p-draft", "s-plan", "draft")
        conn.rollback()

        # 上一条结束后可以开新的未结束计划。
        conn.execute("update run_plan set status = 'done' where id = 'p-active-1'")
        _insert_plan(conn, "p-awaiting", "s-plan", "awaiting_approval")
        conn.commit()
        count = conn.execute("select count(*) from run_plan").fetchone()[0]
        assert count == 4
    finally:
        conn.close()


def test_downgrade_removes_the_tables_and_upgrade_restores_them(tmp_path: Path) -> None:
    db_path = tmp_path / "artifacts_plans_down.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        _seed_session(conn, "s-down")
        conn.execute(
            "insert into artifact (id, session_id, ticket_id, run_id, kind, title,"
            " mime, size_bytes, storage, content, path, sha256, meta, pinned,"
            " created_at, updated_at)"
            " values ('a-1', 's-down', NULL, NULL, 'note', '笔记', 'text/markdown',"
            " 6, 'db', '# 笔记', NULL, ?, '{}', 0,"
            " '2026-11-01 00:00:00', '2026-11-01 00:00:00')",
            ("a" * 64,),
        )
        _insert_plan(conn, "p-1", "s-down", "active")
        conn.commit()
    finally:
        conn.close()

    command.downgrade(cfg, PRE_REVISION)
    conn = _connect(db_path)
    try:
        tables = _table_names(conn)
        assert "artifact" not in tables
        assert "run_plan" not in tables
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
        assert {"artifact", "run_plan"} <= _table_names(conn)
        assert conn.execute("select count(*) from artifact").fetchone()[0] == 0
        assert conn.execute("select count(*) from run_plan").fetchone()[0] == 0
    finally:
        conn.close()
