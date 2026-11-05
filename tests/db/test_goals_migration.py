"""M82：``0035_goals`` 迁移验收（spec §4.1、§6）。

测试纪律与 ``tests/db/test_triggers_migration.py`` 一致：全程用 ``tmp_path`` 的临时库，
只用 Alembic 的 ``upgrade`` / ``downgrade`` 建库与演进，绝不碰
``resource/maa_api.db``。

覆盖四件事：

1. 升级建出 ``goal`` / ``goal_milestone``，列、可空、索引与 ``ondelete`` 都与 §4.1
   一致；``ticket`` 多出可空的 ``goal_id``，已有一行工单的数据逐列不变；
2. 循环外键真的生效：删目标级联删里程碑并把工单的 ``goal_id`` 置空；删工单把目标的
   ``planning_ticket_id`` 置空；
3. 降级回到 0034 的形状：两张表与 ``ticket.goal_id`` 消失，M81 的
   ``source_trigger_id`` 与工单其它数据不丢；再升级可重放；
4. 离线 ``--sql`` 能渲染升级与降级的 batch 重建，且不建库文件。
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
PREVIOUS_REVISION = "0034_triggers"
HEAD_REVISION = "0035_goals"
TIMESTAMP = "2026-01-01 00:00:00"

#: 表名 → 列名 → (DDL 类型, 是否可空)。顺序与 §4.1 一致。
EXPECTED_COLUMNS = {
    "goal": {
        "id": ("VARCHAR(36)", True),
        "title": ("VARCHAR(64)", True),
        "description": ("TEXT", False),
        "target": ("JSON", True),
        "deadline": ("DATETIME", False),
        "status": ("VARCHAR(16)", True),
        "planning_ticket_id": ("VARCHAR(36)", False),
        "upgrade_plan_id": ("VARCHAR(36)", False),
        "schedule_ids": ("JSON", True),
        "review_cron": ("VARCHAR(64)", True),
        "last_review_at": ("DATETIME", False),
        "progress": ("JSON", True),
        "created_at": ("DATETIME", True),
        "updated_at": ("DATETIME", True),
        "ended_at": ("DATETIME", False),
    },
    "goal_milestone": {
        "id": ("VARCHAR(36)", True),
        "goal_id": ("VARCHAR(36)", True),
        "ord": ("INTEGER", True),
        "title": ("VARCHAR(120)", True),
        "due_at": ("DATETIME", False),
        "status": ("VARCHAR(16)", True),
        "metrics": ("JSON", True),
        "created_at": ("DATETIME", True),
        "updated_at": ("DATETIME", True),
    },
}

#: 表名 → {索引名: (列序, 是否唯一)}。
EXPECTED_INDEXES = {
    "goal": {
        "ix_goal_status_updated_at": (("status", "updated_at"), False),
    },
    "goal_milestone": {
        "ix_goal_milestone_goal_id_ord": (("goal_id", "ord"), False),
    },
}

#: 表名 → {(列, 目标表, ondelete)}。
EXPECTED_FOREIGN_KEYS = {
    "goal": {
        ("planning_ticket_id", "ticket", "SET NULL"),
        ("upgrade_plan_id", "upgrade_plan", "SET NULL"),
    },
    "goal_milestone": {("goal_id", "goal", "CASCADE")},
}

#: 升级前先写一行工单；升级/降级后逐列比对，证明 batch 重建没有碰数据。
TICKET_SEED = {
    "id": "ticket-m82",
    "title": "旧工单",
    "kind": "daily_ops",
    "status": "waiting",
    "origin": "user",
    "priority": 2,
    "summary": "升级前的说明",
    "created_at": "2026-01-01 00:00:00",
    "updated_at": "2026-01-01 00:01:00",
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


def _indexes(conn: sqlite3.Connection, table: str) -> dict[str, tuple[tuple[str, ...], bool]]:
    result: dict[str, tuple[tuple[str, ...], bool]] = {}
    for row in conn.execute(f"pragma index_list({table})"):
        # 文本主键由 SQLite 自动建 sqlite_autoindex_*，不属于 §4.1 的索引清单。
        if row["name"].startswith("sqlite_autoindex"):
            continue
        columns = tuple(
            info["name"] for info in conn.execute(f"pragma index_info({row['name']})")
        )
        result[row["name"]] = (columns, bool(row["unique"]))
    return result


def _foreign_keys(conn: sqlite3.Connection, table: str) -> set[tuple[str, str, str]]:
    return {
        (row["from"], row["table"], row["on_delete"])
        for row in conn.execute(f"pragma foreign_key_list({table})")
    }


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in conn.execute("select name from sqlite_master where type='table'")
    }


def _seed_ticket(conn: sqlite3.Connection) -> None:
    columns = ", ".join(TICKET_SEED)
    placeholders = ", ".join("?" * len(TICKET_SEED))
    conn.execute(
        f"insert into ticket ({columns}) values ({placeholders})",
        tuple(TICKET_SEED.values()),
    )
    conn.commit()


def _insert_goal(
    conn: sqlite3.Connection,
    goal_id: str,
    *,
    planning_ticket_id: str | None = None,
) -> None:
    conn.execute(
        "insert into goal (id, title, target, status, planning_ticket_id,"
        " schedule_ids, review_cron, progress, created_at, updated_at)"
        " values (?, '银灰精二', '{}', 'active', ?, '[]', '0 22 * * *', '{}', ?, ?)",
        (goal_id, planning_ticket_id, TIMESTAMP, TIMESTAMP),
    )


def _insert_milestone(
    conn: sqlite3.Connection, milestone_id: str, goal_id: str
) -> None:
    conn.execute(
        "insert into goal_milestone (id, goal_id, ord, title, status, metrics,"
        " created_at, updated_at)"
        " values (?, ?, 1, '精二', 'pending', '{}', ?, ?)",
        (milestone_id, goal_id, TIMESTAMP, TIMESTAMP),
    )


def test_upgrade_adds_goal_tables_and_ticket_goal_id(tmp_path: Path) -> None:
    db_path = tmp_path / "goals_upgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, PREVIOUS_REVISION)

    conn = _connect(db_path)
    try:
        _seed_ticket(conn)
    finally:
        conn.close()

    command.upgrade(cfg, "head")

    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        assert set(EXPECTED_COLUMNS) <= _table_names(conn)

        for table, expected in EXPECTED_COLUMNS.items():
            assert _columns(conn, table) == expected, table

        ticket_columns = _columns(conn, "ticket")
        assert ticket_columns["goal_id"] == ("VARCHAR(36)", False)
        # 工单原有列一个不少，新列对旧行是 NULL。
        assert {
            "id",
            "title",
            "kind",
            "status",
            "origin",
            "priority",
            "summary",
            "created_at",
            "updated_at",
            "source_trigger_id",
        } <= set(ticket_columns)

        for table, expected in EXPECTED_INDEXES.items():
            assert _indexes(conn, table) == expected, table
        assert set(_indexes(conn, "ticket")) == {
            "ix_ticket_status_updated_at",
            "ix_ticket_kind_created_at",
        }

        for table, expected in EXPECTED_FOREIGN_KEYS.items():
            assert _foreign_keys(conn, table) == expected, table
        assert ("goal_id", "goal", "SET NULL") in _foreign_keys(conn, "ticket")

        row = conn.execute(
            "select title, kind, status, origin, priority, summary, created_at,"
            " updated_at, source_trigger_id, goal_id from ticket where id='ticket-m82'"
        ).fetchone()
        assert tuple(row) == (
            "旧工单",
            "daily_ops",
            "waiting",
            "user",
            2,
            "升级前的说明",
            TIMESTAMP,
            "2026-01-01 00:01:00",
            None,
            None,
        )

        # 语义：循环外键两条边都生效。
        conn.execute("pragma foreign_keys = on")
        _insert_goal(conn, "goal-1", planning_ticket_id="ticket-m82")
        _insert_milestone(conn, "milestone-1", "goal-1")
        conn.execute("update ticket set goal_id = 'goal-1' where id='ticket-m82'")
        conn.commit()

        conn.execute("delete from goal where id = 'goal-1'")
        conn.commit()
        assert conn.execute("select count(*) from goal_milestone").fetchone()[0] == 0
        assert conn.execute(
            "select goal_id from ticket where id='ticket-m82'"
        ).fetchone()[0] is None

        # 删工单把目标的规划工单引用置空，目标本身留着。
        _insert_goal(conn, "goal-2", planning_ticket_id="ticket-m82")
        conn.commit()
        conn.execute("delete from ticket where id='ticket-m82'")
        conn.commit()
        assert conn.execute(
            "select planning_ticket_id from goal where id='goal-2'"
        ).fetchone()[0] is None
        assert conn.execute("select count(*) from goal").fetchone()[0] == 1
    finally:
        conn.close()


def test_downgrade_removes_them(tmp_path: Path) -> None:
    db_path = tmp_path / "goals_downgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, PREVIOUS_REVISION)

    conn = _connect(db_path)
    try:
        _seed_ticket(conn)
    finally:
        conn.close()

    command.upgrade(cfg, "head")

    conn = _connect(db_path)
    try:
        conn.execute("pragma foreign_keys = on")
        conn.execute(
            "insert into agent_trigger (id, source, kind, thread_key, admission,"
            " eval_status, created_at)"
            " values (7, 'pipeline', 'pipeline.failed', 'pipeline:7', 'accepted',"
            " 'pending', ?)",
            (TIMESTAMP,),
        )
        _insert_goal(conn, "goal-9", planning_ticket_id="ticket-m82")
        _insert_milestone(conn, "milestone-9", "goal-9")
        conn.execute(
            "update ticket set source_trigger_id = 7, goal_id = 'goal-9'"
            " where id='ticket-m82'"
        )
        conn.commit()
    finally:
        conn.close()

    command.downgrade(cfg, PREVIOUS_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == PREVIOUS_REVISION
        names = _table_names(conn)
        assert not (set(EXPECTED_COLUMNS) & names)
        ticket_columns = _columns(conn, "ticket")
        assert "goal_id" not in ticket_columns
        # M81 加的列与外键在 batch 重建后仍然在，值也没丢。
        assert "source_trigger_id" in ticket_columns
        row = conn.execute(
            "select title, kind, status, origin, priority, summary, created_at,"
            " updated_at, source_trigger_id from ticket where id='ticket-m82'"
        ).fetchone()
        assert tuple(row) == (
            "旧工单",
            "daily_ops",
            "waiting",
            "user",
            2,
            "升级前的说明",
            TIMESTAMP,
            "2026-01-01 00:01:00",
            7,
        )
    finally:
        conn.close()

    # 降到底再升级一次：可重放，两张表是空的（没有假装回填历史目标）。
    command.upgrade(cfg, "head")
    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        assert set(EXPECTED_COLUMNS) <= _table_names(conn)
        for table in EXPECTED_COLUMNS:
            assert conn.execute(f"select count(*) from {table}").fetchone()[0] == 0
        assert "goal_id" in _columns(conn, "ticket")
    finally:
        conn.close()


def test_offline_render_keeps_copy_from(tmp_path: Path) -> None:
    """离线 ``--sql`` 没有连接可反射，``copy_from`` 必须让 batch 重建可渲染。"""
    db_path = tmp_path / "goals_offline.db"
    cfg = _config(db_path)

    upgrade_sql = io.StringIO()
    with contextlib.redirect_stdout(upgrade_sql):
        command.upgrade(cfg, f"{PREVIOUS_REVISION}:{HEAD_REVISION}", sql=True)
    upgrade_text = upgrade_sql.getvalue()

    assert "CREATE TABLE goal " in upgrade_text
    assert "CREATE TABLE goal_milestone" in upgrade_text
    assert "ix_goal_status_updated_at" in upgrade_text
    assert "ix_goal_milestone_goal_id_ord" in upgrade_text
    assert "fk_goal_planning_ticket_id_ticket" in upgrade_text
    assert "fk_goal_upgrade_plan_id_upgrade_plan" in upgrade_text
    assert "fk_goal_milestone_goal_id_goal" in upgrade_text
    assert "fk_ticket_goal_id_goal" in upgrade_text
    assert "_alembic_tmp_ticket" in upgrade_text
    assert not db_path.exists(), "离线模式不应创建数据库文件"

    downgrade_sql = io.StringIO()
    with contextlib.redirect_stdout(downgrade_sql):
        command.downgrade(cfg, f"{HEAD_REVISION}:{PREVIOUS_REVISION}", sql=True)
    downgrade_text = downgrade_sql.getvalue()

    # 降级的目标表没有 goal_id、保留 M81 的 source_trigger_id：batch 用 copy_from
    # 生成搬运 SQL，INSERT 的列清单里也不该再有 goal_id。
    assert "source_trigger_id" in downgrade_text
    assert "CREATE TABLE _alembic_tmp_ticket" in downgrade_text
    insert_line = next(
        line
        for line in downgrade_text.splitlines()
        if line.startswith("INSERT INTO _alembic_tmp_ticket")
    )
    assert "source_trigger_id" in insert_line
    assert "goal_id" not in insert_line
    assert "DROP TABLE goal_milestone" in downgrade_text
    assert "DROP TABLE goal" in downgrade_text
    assert not db_path.exists(), "离线模式不应创建数据库文件"
