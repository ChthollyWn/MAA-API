"""M43 §4.1：0020 给 ``account_operator`` 加可空的 ``skill_ids`` / ``module_details``。

测试纪律（与 ``tests/db/test_skland_migration.py`` 一致）：全程用 ``tmp_path`` 的临时库，
只用 Alembic 的 ``upgrade`` / ``downgrade`` 建库与演进，绝不碰 ``resource/maa_api.db``。

三条断言：

1. 升级到 0020 后两列存在、可空，且 0019 建的表与列都还在；
2. **不回填**：0019 时就存在的行升级后两列仍是 ``NULL``（＝不知道，与练度六列同口径）；
3. 降级回 0019 后两列消失，其余数据不受影响。
"""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
PRE_REVISION = "0019_skland_account"
HEAD_REVISION = "0020_operator_card_fields"

NEW_COLUMNS = ("skill_ids", "module_details")


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


def _seed_operator(conn: sqlite3.Connection, char_id: str) -> None:
    """插一行 0019 形状的账号干员（练度与信赖都写具体值，用来验证升级不动它们）。"""
    conn.execute(
        "insert into account_operator (char_id, name, rarity, profession, own, elite,"
        " level, potential, skill_level, masteries, modules, favor_percent,"
        " recognized_values, field_sources, recognized_at, updated_at)"
        " values (?, ?, 6, 'SNIPER', 1, 2, 90, 6, 7, ?, ?, 200, ?, ?, NULL,"
        " '2026-10-01 00:00:00')",
        (char_id, char_id, '{"1": 3, "2": 3, "3": 0}', '{"X": 3}', "{}", "{}"),
    )
    conn.commit()


def test_upgrade_adds_two_nullable_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "card_fields_up.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        operator = _columns(conn, "account_operator")
        for name in NEW_COLUMNS:
            assert name in operator, name
            assert operator[name]["notnull"] == 0, name
        # 0019 建的东西一个都没被本迁移碰过。
        assert "favor_percent" in operator
        assert operator["favor_percent"]["notnull"] == 0
        tables = {
            row["name"]
            for row in conn.execute("select name from sqlite_master where type = 'table'")
        }
        assert {"skland_credential", "skland_role"} <= tables
    finally:
        conn.close()


def test_existing_rows_keep_both_columns_null(tmp_path: Path) -> None:
    db_path = tmp_path / "card_fields_backfill.db"
    cfg = _config(db_path)
    command.upgrade(cfg, PRE_REVISION)

    conn = _connect(db_path)
    try:
        _seed_operator(conn, "char_002_amiya")
    finally:
        conn.close()

    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        row = conn.execute(
            "select skill_ids, module_details, favor_percent, modules"
            " from account_operator where char_id = 'char_002_amiya'"
        ).fetchone()
        # 升级不制造事实：两列留在 NULL＝不知道，既有数据原样。
        assert row["skill_ids"] is None
        assert row["module_details"] is None
        assert row["favor_percent"] == 200
        assert row["modules"] == '{"X": 3}'
    finally:
        conn.close()


def test_downgrade_drops_only_the_two_new_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "card_fields_down.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        _seed_operator(conn, "char_002_amiya")
    finally:
        conn.close()

    command.downgrade(cfg, PRE_REVISION)

    conn = _connect(db_path)
    try:
        operator = _columns(conn, "account_operator")
        for name in NEW_COLUMNS:
            assert name not in operator, name
        assert "favor_percent" in operator
        row = conn.execute(
            "select favor_percent, modules from account_operator where char_id = 'char_002_amiya'"
        ).fetchone()
        assert row["favor_percent"] == 200
        assert row["modules"] == '{"X": 3}'
    finally:
        conn.close()
