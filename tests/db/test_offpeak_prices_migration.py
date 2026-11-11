"""T2：``0044_offpeak_prices`` 迁移验收（spec §4.1、§5 T2）。

测试纪律与 ``tests/db/test_capability_insights_migration.py`` 一致：全程用
``tmp_path`` 的临时库，只用 Alembic 的 ``upgrade`` / ``downgrade`` 建库与演进，
绝不碰 ``resource/maa_api.db``。

覆盖三件事：

1. 空库升级到 0044：``model_profile`` 新增 ``price_input_offpeak`` /
   ``price_cached_input_offpeak`` / ``price_output_offpeak`` 三列，类型 ``FLOAT``、
   可空；既有 22 列的名称、类型与可空性逐项不变；
2. 升级**不回填**：0043 就存在的行升级后三列为 ``NULL``，既有列的值原样保留；
3. 降级回 0043 后三列消失（既有行与既有列的值不受影响）；再升级三列回来，
   既有行仍在、三列重新为 ``NULL``（降级丢弃的空闲价不会恢复）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
PREVIOUS_REVISION = "0043_capability_insights"
HEAD_REVISION = "0044_offpeak_prices"

#: 新增列 → (DDL 类型, 是否可空)。spec §4.1：三列全可空、无默认、不回填。
EXPECTED_NEW_COLUMNS = {
    "price_input_offpeak": ("FLOAT", True),
    "price_cached_input_offpeak": ("FLOAT", True),
    "price_output_offpeak": ("FLOAT", True),
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
    """列名 → (DDL 类型, 是否可空)；与 ``pragma table_info`` 的 ``notnull`` 互斥。"""
    return {
        row["name"]: (row["type"], not bool(row["notnull"]))
        for row in conn.execute(f"pragma table_info({table})")
    }


def _defaults(conn: sqlite3.Connection, table: str) -> dict[str, object]:
    """列名 → DDL 默认值（``pragma table_info.dflt_value``，无默认时是 ``None``）。"""
    return {
        row["name"]: row["dflt_value"]
        for row in conn.execute(f"pragma table_info({table})")
    }


def _row(conn: sqlite3.Connection, profile_id: str) -> sqlite3.Row:
    row = conn.execute(
        "select * from model_profile where id = ?", (profile_id,)
    ).fetchone()
    assert row is not None, profile_id
    return row


def test_upgrade_adds_three_nullable_price_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "offpeak_upgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, PREVIOUS_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == PREVIOUS_REVISION
        baseline = _columns(conn, "model_profile")
        # 0043 上还没有这三列（迁移只加列，不重构既有列）。
        assert not (set(EXPECTED_NEW_COLUMNS) & set(baseline))
        conn.execute(
            "insert into model_profile ("
            " id, name, kind, provider, model, supports_tools,"
            " dynamic_context_position, currency, enabled, created_at, updated_at,"
            " price_input, price_output"
            ") values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "preexisting",
                "既有档案",
                "chat",
                "openai_compatible",
                "gpt-4o",
                1,
                "tail_system",
                "CNY",
                1,
                "2026-11-11 00:00:00",
                "2026-11-11 00:00:00",
                1.5,
                2.5,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        columns = _columns(conn, "model_profile")
        # 既有 22 列的名称、类型与可空性一列不变。
        assert {
            name: spec
            for name, spec in columns.items()
            if name not in EXPECTED_NEW_COLUMNS
        } == baseline
        # 三列类型 FLOAT、可空、无默认值。SQLite 原生 ADD COLUMN 追加在表尾，物理
        # 顺序与模型字段顺序（price_output 之后）不同，只断言列定义。
        defaults = _defaults(conn, "model_profile")
        for name, expected in EXPECTED_NEW_COLUMNS.items():
            assert columns[name] == expected, name
            assert defaults[name] is None, name
        # 不回填：既有行保留，既有价格列的值原样，新增三列为 NULL。
        row = _row(conn, "preexisting")
        assert row["name"] == "既有档案"
        assert row["price_input"] == 1.5
        assert row["price_output"] == 2.5
        for name in EXPECTED_NEW_COLUMNS:
            assert row[name] is None, name
    finally:
        conn.close()


def test_downgrade_drops_them_and_reupgrade_is_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "offpeak_downgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        head_columns = _columns(conn, "model_profile")
        conn.execute(
            "insert into model_profile ("
            " id, name, kind, provider, model, supports_tools,"
            " dynamic_context_position, currency, enabled, created_at, updated_at,"
            " price_input, price_output,"
            " price_input_offpeak, price_cached_input_offpeak, price_output_offpeak"
            ") values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "preexisting",
                "既有档案",
                "chat",
                "openai_compatible",
                "gpt-4o",
                1,
                "tail_system",
                "CNY",
                1,
                "2026-11-11 00:00:00",
                "2026-11-11 00:00:00",
                1.5,
                2.5,
                3.5,
                4.5,
                5.5,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    command.downgrade(cfg, PREVIOUS_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == PREVIOUS_REVISION
        columns = _columns(conn, "model_profile")
        # 三列消失，其余列与升级后逐项一致。
        assert {
            name: spec
            for name, spec in head_columns.items()
            if name not in EXPECTED_NEW_COLUMNS
        } == columns
        assert not (set(EXPECTED_NEW_COLUMNS) & set(columns))
        # 降级只删三列：既有行与既有价格列的值不受影响。
        row = _row(conn, "preexisting")
        assert row["name"] == "既有档案"
        assert row["price_input"] == 1.5
        assert row["price_output"] == 2.5
    finally:
        conn.close()

    # 再升级：三列回来、既有行仍在；不回填也不恢复降级前的空闲价 ⇒ 均为 NULL。
    command.upgrade(cfg, HEAD_REVISION)

    conn = _connect(db_path)
    try:
        assert _revision(conn) == HEAD_REVISION
        columns = _columns(conn, "model_profile")
        defaults = _defaults(conn, "model_profile")
        for name, expected in EXPECTED_NEW_COLUMNS.items():
            assert columns[name] == expected, name
            assert defaults[name] is None, name
        assert conn.execute("select count(*) from model_profile").fetchone()[0] == 1
        row = _row(conn, "preexisting")
        assert row["price_input"] == 1.5
        assert row["price_output"] == 2.5
        for name in EXPECTED_NEW_COLUMNS:
            assert row[name] is None, name
    finally:
        conn.close()
