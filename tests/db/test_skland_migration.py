"""M42 §4.4：0019 新增两张森空岛表，并给 ``account_operator`` 加可空的 ``favor_percent``。

测试纪律（与 ``tests/db/test_unknown_training_migration.py`` 一致）：全程用 ``tmp_path``
的临时库，只用 Alembic 的 ``upgrade`` / ``downgrade`` 建库与演进，绝不碰
``resource/maa_api.db``。

三条断言：

1. 升级到 0019 后两张新表存在、列集合与模型一致，``favor_percent`` 是可空列；
2. **不回填**：0018 之前就存在的行升级后 ``favor_percent`` 仍是 ``NULL``（＝未知，与
   练度六列同口径，M35 §4.1）；
3. 降级回 0018 后两张表与那一列都消失，既有数据不受影响。
"""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
PRE_REVISION = "0018_unknown_training"
HEAD_REVISION = "0019_skland_account"

CREDENTIAL_COLUMNS = (
    "id",
    "cred_encrypted",
    "token_encrypted",
    "bound_at",
    "refreshed_at",
    "updated_at",
)
ROLE_COLUMNS = (
    "uid",
    "app_code",
    "channel_master_id",
    "nickname",
    "level",
    "is_default",
    "synced_at",
    "created_at",
    "updated_at",
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


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
    rows = conn.execute(f"pragma table_info({table})").fetchall()
    return {row["name"]: row for row in rows}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("select name from sqlite_master where type = 'table'").fetchall()
    return {row["name"] for row in rows}


def _seed_operator(conn: sqlite3.Connection, char_id: str) -> None:
    """插一行 0018 形状的账号干员（六列练度都写具体值，用来验证升级不动它们）。"""
    conn.execute(
        "insert into account_operator (char_id, name, rarity, profession, own, elite,"
        " level, potential, skill_level, masteries, modules, recognized_values,"
        " field_sources, recognized_at, updated_at)"
        " values (?, ?, 6, 'SNIPER', 1, 2, 90, 6, 7, ?, ?, ?, ?, NULL,"
        " '2026-10-01 00:00:00')",
        (char_id, char_id, '{"1": 3, "2": 3, "3": 0}', '{"X": 3}', "{}", "{}"),
    )
    conn.commit()


def test_upgrade_creates_the_two_skland_tables_and_the_favor_column(tmp_path: Path) -> None:
    db_path = tmp_path / "skland_up.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        tables = _table_names(conn)
        assert {"skland_credential", "skland_role"} <= tables

        credential = _columns(conn, "skland_credential")
        assert tuple(credential) == CREDENTIAL_COLUMNS
        assert credential["id"]["pk"] == 1
        for name in ("cred_encrypted", "token_encrypted", "bound_at", "updated_at"):
            assert credential[name]["notnull"] == 1, name
        assert credential["refreshed_at"]["notnull"] == 0

        role = _columns(conn, "skland_role")
        assert tuple(role) == ROLE_COLUMNS
        assert role["uid"]["pk"] == 1
        for name in ("channel_master_id", "nickname", "level", "synced_at"):
            assert role[name]["notnull"] == 0, name
        assert role["is_default"]["notnull"] == 1

        operator = _columns(conn, "account_operator")
        assert "favor_percent" in operator
        assert operator["favor_percent"]["notnull"] == 0
        # 六列练度一个都没被本迁移碰过。
        for name in ("elite", "level", "potential", "skill_level", "masteries", "modules"):
            assert operator[name]["notnull"] == 0, name
    finally:
        conn.close()


def test_existing_rows_keep_null_favor_percent(tmp_path: Path) -> None:
    db_path = tmp_path / "skland_backfill.db"
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
            "select elite, level, potential, masteries, favor_percent"
            " from account_operator where char_id = 'char_002_amiya'"
        ).fetchone()
        assert row["favor_percent"] is None
        assert row["elite"] == 2
        assert row["level"] == 90
        assert row["potential"] == 6
        assert row["masteries"] == '{"1": 3, "2": 3, "3": 0}'
    finally:
        conn.close()


def test_downgrade_removes_both_tables_and_the_column(tmp_path: Path) -> None:
    db_path = tmp_path / "skland_down.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        _seed_operator(conn, "char_002_amiya")
        conn.execute(
            "insert into skland_credential (id, cred_encrypted, token_encrypted, bound_at,"
            " refreshed_at, updated_at) values (1, 'cipher', 'cipher',"
            " '2026-10-12 00:00:00', NULL, '2026-10-12 00:00:00')"
        )
        conn.execute(
            "insert into skland_role (uid, app_code, channel_master_id, nickname, level,"
            " is_default, synced_at, created_at, updated_at) values"
            " ('1000', 'arknights', '1', '博士', 120, 1, NULL,"
            " '2026-10-12 00:00:00', '2026-10-12 00:00:00')"
        )
        conn.commit()
    finally:
        conn.close()

    command.downgrade(cfg, PRE_REVISION)
    conn = _connect(db_path)
    try:
        tables = _table_names(conn)
        assert "skland_credential" not in tables
        assert "skland_role" not in tables
        assert "favor_percent" not in _columns(conn, "account_operator")
        # 既有账号行不受降级影响。
        row = conn.execute(
            "select level, masteries from account_operator where char_id = 'char_002_amiya'"
        ).fetchone()
        assert row["level"] == 90
        assert row["masteries"] == '{"1": 3, "2": 3, "3": 0}'
    finally:
        conn.close()
