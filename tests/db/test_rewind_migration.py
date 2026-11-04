"""M80：``0033_rewind`` 迁移验收（spec §4.1、§6）。

测试纪律与 ``tests/db/test_agent_runs_migration.py`` 一致：全程用 ``tmp_path``
的临时库，只用 Alembic 的 ``upgrade`` / ``downgrade`` 建库与演进，绝不碰
``resource/maa_api.db``。

覆盖四件事（spec §6）：

1. 升级加出 ``agent_session.forked_from_session_id`` / ``forked_from_seq`` 与
   ``agent_message.superseded_at``，三列都可空，自引用外键是 ``SET NULL``；
2. 降级回到 0032 的形状，三列消失，既有行与数据不变；
3. 外键真的生效：删掉来源会话，分叉出的会话只解除关联；
4. 离线 ``--sql`` 能渲染本迁移的 batch 重建且不建库文件。
"""

import contextlib
import io
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
PREVIOUS_REVISION = "0032_memory"
HEAD_REVISION = "0033_rewind"
TIMESTAMP = "2026-01-01 00:00:00"


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


def _seed(conn: sqlite3.Connection) -> None:
    """一条会话 + 两条消息：升级/降级后逐列比对，证明数据没被碰。"""
    conn.execute(
        "insert into agent_session (id, title, status, model, message_count,"
        " prompt_tokens, completion_tokens, created_at, updated_at)"
        " values ('session-old', '旧会话', 'active', 'gpt-test', 2, 10, 20, ?, ?)",
        (TIMESTAMP, TIMESTAMP),
    )
    for seq, content in ((0, "user-0"), (1, "assistant-1")):
        conn.execute(
            "insert into agent_message (session_id, seq, role, content, created_at)"
            " values ('session-old', ?, 'user', ?, ?)",
            (seq, content, TIMESTAMP),
        )
    conn.commit()


def test_upgrade_adds_rewind_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "rewind_upgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, PREVIOUS_REVISION)

    conn = _connect(db_path)
    try:
        _seed(conn)
    finally:
        conn.close()

    command.upgrade(cfg, "head")

    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        session_columns = {row["name"]: row for row in conn.execute("pragma table_info(agent_session)")}
        message_columns = {row["name"]: row for row in conn.execute("pragma table_info(agent_message)")}

        for name in ("forked_from_session_id", "forked_from_seq"):
            assert name in session_columns
            assert session_columns[name]["notnull"] == 0
            assert session_columns[name]["dflt_value"] is None
        assert "superseded_at" in message_columns
        assert message_columns["superseded_at"]["notnull"] == 0

        foreign_keys = {
            (row["from"], row["table"], row["on_delete"])
            for row in conn.execute("pragma foreign_key_list(agent_session)")
        }
        assert ("forked_from_session_id", "agent_session", "SET NULL") in foreign_keys
        # 0026 的 ticket_id 外键在 batch 重建后不能丢。
        assert ("ticket_id", "ticket", "SET NULL") in foreign_keys

        # 既有数据保持不变；新列对旧行是 NULL。
        session = conn.execute(
            "select title, model, message_count, prompt_tokens, completion_tokens,"
            " forked_from_session_id, forked_from_seq from agent_session"
        ).fetchone()
        assert tuple(session) == (
            "旧会话", "gpt-test", 2, 10, 20, None, None,
        )
        assert [
            tuple(row)
            for row in conn.execute(
                "select seq, content, superseded_at from agent_message order by seq"
            )
        ] == [(0, "user-0", None), (1, "assistant-1", None)]
    finally:
        conn.close()


def test_downgrade_removes_rewind_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "rewind_downgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, PREVIOUS_REVISION)

    conn = _connect(db_path)
    try:
        _seed(conn)
    finally:
        conn.close()

    command.upgrade(cfg, "head")

    conn = _connect(db_path)
    try:
        conn.execute(
            "update agent_session set forked_from_session_id = 'session-old',"
            " forked_from_seq = 1 where id = 'session-old'"
        )
        conn.execute(
            "update agent_message set superseded_at = ? where seq = 1",
            (TIMESTAMP,),
        )
        conn.commit()
    finally:
        conn.close()

    command.downgrade(cfg, PREVIOUS_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == PREVIOUS_REVISION
        assert "forked_from_session_id" not in _columns(conn, "agent_session")
        assert "forked_from_seq" not in _columns(conn, "agent_session")
        assert "superseded_at" not in _columns(conn, "agent_message")
        assert conn.execute("select count(*) from agent_session").fetchone()[0] == 1
        assert [
            tuple(row)
            for row in conn.execute(
                "select seq, content from agent_message order by seq"
            )
        ] == [(0, "user-0"), (1, "assistant-1")]
        assert conn.execute(
            "select title, model from agent_session where id = 'session-old'"
        ).fetchone()[0:2] == ("旧会话", "gpt-test")
    finally:
        conn.close()

    # 降到底再升级一次：可重放。
    command.upgrade(cfg, "head")
    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        assert "forked_from_session_id" in _columns(conn, "agent_session")
    finally:
        conn.close()


def test_forked_from_session_is_set_null_when_source_deleted(tmp_path: Path) -> None:
    db_path = tmp_path / "rewind_set_null.db"
    cfg = _config(db_path)
    command.upgrade(cfg, "head")

    conn = _connect(db_path)
    try:
        conn.execute("pragma foreign_keys = on")
        _seed(conn)
        conn.execute(
            "insert into agent_session (id, status, model, message_count,"
            " prompt_tokens, completion_tokens, created_at, updated_at,"
            " forked_from_session_id, forked_from_seq)"
            " values ('session-fork', 'active', 'gpt-test', 0, 0, 0, ?, ?,"
            " 'session-old', 1)",
            (TIMESTAMP, TIMESTAMP),
        )
        conn.commit()

        conn.execute("delete from agent_session where id = 'session-old'")
        conn.commit()

        row = conn.execute(
            "select forked_from_session_id, forked_from_seq from agent_session"
            " where id = 'session-fork'"
        ).fetchone()
        assert tuple(row) == (None, 1)
        assert conn.execute("select count(*) from agent_message").fetchone()[0] == 0
    finally:
        conn.close()


def test_offline_render_keeps_copy_from(tmp_path: Path) -> None:
    """离线 ``--sql`` 没有连接可反射，``copy_from`` 必须让 batch 重建可渲染。"""
    db_path = tmp_path / "rewind_offline.db"
    cfg = _config(db_path)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        command.upgrade(cfg, f"{PREVIOUS_REVISION}:{HEAD_REVISION}", sql=True)
    sql = buffer.getvalue()

    assert "forked_from_session_id" in sql
    assert "fk_agent_session_forked_from_session_id_agent_session" in sql
    assert "_alembic_tmp_agent_session" in sql
    assert "superseded_at" in sql
    assert not db_path.exists(), "离线模式不应创建数据库文件"
