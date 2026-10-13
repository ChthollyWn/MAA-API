"""M45 W3a：``0021_depot_sources`` 给仓库两路合并留下的四列。

测试纪律与 ``tests/db/test_operator_card_fields_migration.py`` 一致：全程用 ``tmp_path``
的临时库，只用 Alembic 的 ``upgrade`` / ``downgrade`` 建库与演进，绝不碰
``resource/maa_api.db``。

四条断言：

1. 升级后 ``account_depot_item.sources`` 与 ``account_recognition.detail`` 存在且可空；
2. ``account_depot_item.count`` 从 NOT NULL 放宽到可空（自识别只保证身份时写 NULL）；
3. **不回填**：0020 时就存在的行升级后两新列仍是 ``NULL``，``count`` 原值不动；
4. 降级：两新列消失，``count`` 里的 ``NULL`` 折算成 0（旧代码只有这一种表达），
   非空数量原样保留。
"""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
PRE_REVISION = "0020_operator_card_fields"
HEAD_REVISION = "0021_depot_sources"


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
    return {row["name"]: row for row in conn.execute(f"pragma table_info({table})")}


def _seed(conn: sqlite3.Connection) -> None:
    """插一行 0020 形状的仓库行与一条识别记录（数量写具体值，验升级不动它）。"""
    conn.execute(
        "insert into account_depot_item (item_id, name, count, category, recognized_at)"
        " values ('3003', '赤金', 3092, 'MATERIAL', '2026-10-01 00:00:00')"
    )
    conn.execute(
        "insert into account_recognition (kind, item_count, recognized_at)"
        " values ('depot', 1, '2026-10-01 00:00:00')"
    )
    conn.commit()


def test_upgrade_adds_two_nullable_columns_and_relaxes_count(tmp_path: Path) -> None:
    db_path = tmp_path / "depot_sources_up.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        depot = _columns(conn, "account_depot_item")
        assert "sources" in depot and depot["sources"]["notnull"] == 0
        assert depot["count"]["notnull"] == 0, "count 必须放宽到可空（NULL ＝ 未识别）"
        recognition = _columns(conn, "account_recognition")
        assert "detail" in recognition and recognition["detail"]["notnull"] == 0
        # 0020 建的东西一个都没被本迁移碰过。
        assert {"item_id", "name", "category", "recognized_at"} <= set(depot)
        assert {"kind", "pipeline_id", "task_id", "client_type", "item_count"} <= set(
            recognition
        )
    finally:
        conn.close()


def test_existing_rows_keep_both_columns_null(tmp_path: Path) -> None:
    db_path = tmp_path / "depot_sources_backfill.db"
    cfg = _config(db_path)
    command.upgrade(cfg, PRE_REVISION)

    conn = _connect(db_path)
    try:
        _seed(conn)
    finally:
        conn.close()

    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        row = conn.execute(
            "select count, sources from account_depot_item where item_id = '3003'"
        ).fetchone()
        # 升级不制造事实：新列留在 NULL，既有数量原样。
        assert row["sources"] is None
        assert row["count"] == 3092
        detail = conn.execute(
            "select detail from account_recognition where kind = 'depot'"
        ).fetchone()
        assert detail["detail"] is None
    finally:
        conn.close()


def test_null_count_is_accepted_after_the_upgrade(tmp_path: Path) -> None:
    """自识别独有的道具写 NULL（＝未识别）—— 这正是放宽 ``count`` 的目的。"""
    db_path = tmp_path / "depot_sources_null.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        conn.execute(
            "insert into account_depot_item (item_id, name, count, category, sources,"
            " recognized_at) values ('7003', '寻访凭证', NULL, 'NORMAL', ?,"
            " '2026-10-13 00:00:00')",
            ('{"7003": "self_scan"}',),
        )
        conn.commit()
        row = conn.execute(
            "select count, sources from account_depot_item where item_id = '7003'"
        ).fetchone()
        assert row["count"] is None
        assert row["sources"] == '{"7003": "self_scan"}'
    finally:
        conn.close()


def test_downgrade_drops_the_two_columns_and_folds_null_to_zero(tmp_path: Path) -> None:
    db_path = tmp_path / "depot_sources_down.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        _seed(conn)
        conn.execute(
            "insert into account_depot_item (item_id, count, recognized_at)"
            " values ('7003', NULL, '2026-10-13 00:00:00')"
        )
        conn.commit()
    finally:
        conn.close()

    command.downgrade(cfg, PRE_REVISION)

    conn = _connect(db_path)
    try:
        depot = _columns(conn, "account_depot_item")
        assert "sources" not in depot
        assert depot["count"]["notnull"] == 1, "降级后 count 回到 NOT NULL"
        assert "detail" not in _columns(conn, "account_recognition")
        rows = {
            row["item_id"]: row["count"]
            for row in conn.execute("select item_id, count from account_depot_item")
        }
        # 旧代码把 NULL 当 0 读，降级照旧口径折算；非空值不动。
        assert rows == {"3003": 3092, "7003": 0}
    finally:
        conn.close()
