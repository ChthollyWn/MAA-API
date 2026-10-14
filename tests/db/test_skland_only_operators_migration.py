"""M47：``0022_skland_only_operators`` 清空 ``account_operator``。

测试纪律与 ``tests/db/test_depot_sources_migration.py`` 一致：全程用 ``tmp_path`` 的临时
库，只用 Alembic 的 ``upgrade`` / ``downgrade`` 建库与演进，绝不碰
``resource/maa_api.db``。

四条断言：

1. 空库从 ``0021_depot_sources`` 升级到 ``0022`` 不炸，干员表仍是空的；
2. 有干员行时升级后 ``account_operator`` **清空**，而 ``account_operator_diff`` 与
   ``account_recognition`` 的行一个不少、内容不变（这两处历史按迁移文档保留）；
3. 离线 ``upgrade --sql`` 的渲染里含 ``DELETE FROM account_operator``，且不建库文件；
4. ``downgrade`` 是显式 no-op：不炸、也不把删掉的行变回来。
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
PRE_REVISION = "0021_depot_sources"
HEAD_REVISION = "0022_skland_only_operators"


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


def _seed(conn: sqlite3.Connection) -> None:
    """插 0021 形状的三张表的行：干员（两条通道写出来的）、差异、识别记录。"""
    conn.execute(
        "insert into account_operator (char_id, name, rarity, profession, own, elite,"
        " level, potential, skill_level, masteries, modules, favor_percent, skill_ids,"
        " module_details, recognized_values, field_sources, recognized_at, updated_at)"
        " values ('char_002_amiya', '阿米娅', 5, '术师', 1, 2, 80, 6, 7,"
        " '{\"1\": 3, \"2\": 0, \"3\": 0}', '{\"X\": 2}', 200, '[\"skchr_amiya_2\"]',"
        " NULL, '{\"own\": true, \"elite\": 2}', '{\"level\": \"manual\"}',"
        " '2026-10-10 03:00:00', '2026-10-10 03:00:00')"
    )
    conn.execute(
        "insert into account_operator (char_id, name, own, field_sources, updated_at)"
        " values ('char_103_angel', '能天使', 1, '{}', '2026-10-11 03:00:00')"
    )
    conn.execute(
        "insert into account_operator_diff (char_id, field, manual_value,"
        " recognized_value, created_at, dismissed_at)"
        " values ('char_002_amiya', 'level', '90', '80', '2026-10-10 04:00:00', NULL)"
    )
    conn.execute(
        "insert into account_operator_diff (char_id, field, manual_value,"
        " recognized_value, created_at, dismissed_at)"
        " values ('char_002_amiya', 'elite', '2', '1', '2026-10-10 05:00:00',"
        " '2026-10-10 06:00:00')"
    )
    for kind in ("operbox", "skland", "depot"):
        conn.execute(
            "insert into account_recognition (kind, item_count, recognized_at)"
            " values (?, 312, '2026-10-10 07:00:00')",
            (kind,),
        )
    conn.commit()


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in conn.execute("select name from sqlite_master where type = 'table'")
    }


def test_upgrade_on_an_empty_database_succeeds(tmp_path: Path) -> None:
    db_path = tmp_path / "skland_only_empty.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        # 干员表还在（迁移只删行、不改结构），且是空的。
        assert "account_operator" in _table_names(conn)
        assert conn.execute("select count(*) as n from account_operator").fetchone()["n"] == 0
    finally:
        conn.close()


def test_upgrade_clears_operators_and_keeps_the_other_two_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "skland_only_data.db"
    cfg = _config(db_path)
    command.upgrade(cfg, PRE_REVISION)

    conn = _connect(db_path)
    try:
        _seed(conn)
        assert conn.execute("select count(*) as n from account_operator").fetchone()["n"] == 2
    finally:
        conn.close()

    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        assert conn.execute("select count(*) as n from account_operator").fetchone()["n"] == 0

        # 差异表：两行历史都在，值、时间、dismissed_at 一个不改。
        # （``manual_value`` / ``recognized_value`` 是 JSON 列，读回来是原类型。）
        diffs = conn.execute(
            "select char_id, field, manual_value, recognized_value, created_at,"
            " dismissed_at from account_operator_diff order by field"
        ).fetchall()
        assert [
            (row["char_id"], row["field"], row["manual_value"], row["recognized_value"],
             row["created_at"], row["dismissed_at"])
            for row in diffs
        ] == [
            ("char_002_amiya", "elite", 2, 1, "2026-10-10 05:00:00",
             "2026-10-10 06:00:00"),
            ("char_002_amiya", "level", 90, 80, "2026-10-10 04:00:00", None),
        ]

        # 识别记录：operbox / skland / depot 三条审计历史全部保留。
        kinds = [
            row["kind"]
            for row in conn.execute(
                "select kind from account_recognition order by kind"
            )
        ]
        assert kinds == ["depot", "operbox", "skland"]

        # 结构没动：列还在（field_sources / recognized_values 仍是来源台账）。
        columns = {
            row["name"] for row in conn.execute("pragma table_info(account_operator)")
        }
        assert {"field_sources", "recognized_values", "recognized_at"} <= columns
    finally:
        conn.close()


def test_offline_render_carries_the_delete_sql(tmp_path: Path) -> None:
    """离线 ``upgrade head --sql`` 没有真连接，DELETE 也要能直接渲染出来。"""
    db_path = tmp_path / "skland_only_offline.db"
    cfg = _config(db_path)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        command.upgrade(cfg, HEAD_REVISION, sql=True)
    sql = buffer.getvalue()

    assert "DELETE FROM account_operator" in sql
    # 只删行、不改结构：本迁移自己的语句里没有 batch 重建。
    own_sql = sql.split("Running upgrade 0021_depot_sources -> 0022_skland_only_operators")[-1]
    assert "_alembic_tmp_account_operator" not in own_sql
    assert not db_path.exists(), "离线模式不应创建数据库文件"


def test_downgrade_is_an_explicit_no_op(tmp_path: Path) -> None:
    db_path = tmp_path / "skland_only_down.db"
    cfg = _config(db_path)
    command.upgrade(cfg, PRE_REVISION)

    conn = _connect(db_path)
    try:
        _seed(conn)
    finally:
        conn.close()

    command.upgrade(cfg, HEAD_REVISION)
    command.downgrade(cfg, PRE_REVISION)  # 不炸

    conn = _connect(db_path)
    try:
        assert _revision(conn) == PRE_REVISION
        # 降级不会把行变回来：这不是遗漏，是迁移文档写明的语义。
        assert conn.execute("select count(*) as n from account_operator").fetchone()["n"] == 0
        # 但可以重新写入（重新同步就是把行写回来的路径）。
        conn.execute(
            "insert into account_operator (char_id, name, own, field_sources, updated_at)"
            " values ('char_002_amiya', '阿米娅', 1, '{\"level\": \"skland\"}',"
            " '2026-10-14 03:00:00')"
        )
        conn.commit()
        row = conn.execute(
            "select name, field_sources from account_operator where char_id = 'char_002_amiya'"
        ).fetchone()
        assert row["name"] == "阿米娅"
        assert row["field_sources"] == '{"level": "skland"}'
    finally:
        conn.close()
