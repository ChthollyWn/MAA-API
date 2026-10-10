"""M35 §4.2：0018 把六列练度改成可空，并按台账把「没识别过」的默认值回填成 NULL。

测试纪律（与 ``tests/db/test_initial_migration.py`` 一致）：全程用 ``tmp_path`` 的临时库，
只用 Alembic 的 ``upgrade`` / ``downgrade`` 建库与演进，绝不碰 ``resource/maa_api.db``。
"""

import contextlib
import io
import json
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
PRE_REVISION = "0017_copilot_grant"
HEAD_REVISION = "0018_unknown_training"

TRAINING_COLUMNS = (
    "elite",
    "level",
    "potential",
    "skill_level",
    "masteries",
    "modules",
)
#: 0016 建表时 NOT NULL 列自带的默认值——「没识别过」在旧模型里就长这样。
LEGACY_DEFAULTS = {
    "elite": 0,
    "level": 0,
    "potential": 0,
    "skill_level": 1,
    "masteries": {"1": 0, "2": 0, "3": 0},
    "modules": {},
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


def _seed(
    conn: sqlite3.Connection,
    char_id: str,
    *,
    sources,
    recognized=None,
    raw_sources: str | None = None,
    raw_recognized: str | None = None,
    **values,
) -> None:
    """插一行 0016 形状的账号干员：六列都给 0016 的默认值，除非显式覆盖。"""
    row = {**LEGACY_DEFAULTS, **values}
    conn.execute(
        "insert into account_operator (char_id, name, rarity, profession, own, elite,"
        " level, potential, skill_level, masteries, modules, recognized_values,"
        " field_sources, recognized_at, updated_at)"
        " values (?, ?, 6, 'SNIPER', 1, ?, ?, ?, ?, ?, ?, ?, ?, NULL,"
        " '2026-10-01 00:00:00')",
        (
            char_id,
            char_id,
            row["elite"],
            row["level"],
            row["potential"],
            row["skill_level"],
            json.dumps(row["masteries"]),
            json.dumps(row["modules"]),
            raw_recognized
            if raw_recognized is not None
            else json.dumps(recognized or {}),
            raw_sources if raw_sources is not None else json.dumps(sources),
        ),
    )


JSON_COLUMNS = ("masteries", "modules", "recognized_values", "field_sources")


def _decode(raw):
    """JSON 列读成 Python 对象；坏 JSON（本文件专门构造了一行）原样返回。"""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def _rows(db_path: Path) -> dict[str, dict]:
    with contextlib.closing(_connect(db_path)) as conn:
        rows = {
            row["char_id"]: dict(row)
            for row in conn.execute(
                "select char_id, elite, level, potential, skill_level, masteries,"
                " modules, recognized_values, field_sources from account_operator"
            )
        }
    for row in rows.values():
        for column in JSON_COLUMNS:
            row[column] = _decode(row[column])
    return rows


def _training(row: dict) -> dict:
    return {column: row[column] for column in TRAINING_COLUMNS}


def _notnull(db_path: Path, column: str) -> bool:
    with contextlib.closing(_connect(db_path)) as conn:
        for entry in conn.execute("pragma table_info(account_operator)"):
            if entry["name"] == column:
                return bool(entry["notnull"])
    raise AssertionError(f"account_operator 没有 {column} 列")


def _seed_real_shapes(db_path: Path) -> None:
    """五种真机/边界形状（规格 §4.2 的判定表），都从 0016 的默认值起步。"""
    with contextlib.closing(_connect(db_path)) as conn:
        # 识别写全的：识别确实给过 elite/level/potential。
        _seed(
            conn,
            "char_recognition_full",
            sources={
                "own": "recognition",
                "elite": "recognition",
                "level": "recognition",
                "potential": "recognition",
            },
            recognized={"own": True, "elite": 2, "level": 90, "potential": 6},
            elite=2,
            level=90,
            potential=6,
        )
        # M33 那三名「已拥有但没有详情」：台账里只有 own。
        _seed(
            conn,
            "char_own_only",
            sources={"own": "recognition"},
            recognized={"own": True},
        )
        # 人工/Agent 写过的三列：识别永不写它们，必须原样保留。
        _seed(
            conn,
            "char_manual_training",
            sources={
                "own": "recognition",
                "skill_level": "manual",
                "masteries": "manual",
                "modules": "agent",
            },
            recognized={"own": True},
            skill_level=7,
            masteries={"1": 3, "2": 3, "3": 0},
            modules={"X": 3},
        )
        # 只靠 recognized_values（field_sources 残缺）的历史行：识别写过也要保留。
        _seed(
            conn,
            "char_recognized_values_only",
            sources={},
            recognized={"elite": 1, "level": 55},
            elite=1,
            level=55,
        )
        # 来源种类不认识、JSON 坏掉：一律按「没有依据」置 NULL，且不能炸。
        _seed(
            conn,
            "char_unusable_ledger",
            sources={"elite": "import"},
            raw_sources="{not json",
            raw_recognized="[]",
        )
        conn.commit()


def test_upgrade_turns_defaults_without_a_ledger_into_null(tmp_path):
    db_path = tmp_path / "unknown_training.db"
    cfg = _config(db_path)
    command.upgrade(cfg, PRE_REVISION)
    _seed_real_shapes(db_path)

    command.upgrade(cfg, HEAD_REVISION)
    rows = _rows(db_path)

    assert _training(rows["char_recognition_full"]) == {
        "elite": 2,
        "level": 90,
        "potential": 6,
        "skill_level": None,
        "masteries": None,
        "modules": None,
    }
    assert _training(rows["char_own_only"]) == dict.fromkeys(TRAINING_COLUMNS)
    assert _training(rows["char_manual_training"]) == {
        "elite": None,
        "level": None,
        "potential": None,
        "skill_level": 7,
        "masteries": {"1": 3, "2": 3, "3": 0},
        "modules": {"X": 3},
    }
    assert _training(rows["char_recognized_values_only"]) == {
        "elite": 1,
        "level": 55,
        "potential": None,
        "skill_level": None,
        "masteries": None,
        "modules": None,
    }
    assert _training(rows["char_unusable_ledger"]) == dict.fromkeys(TRAINING_COLUMNS)
    # 台账两列是诚实的来源记录，回填不该动它们。
    assert rows["char_manual_training"]["field_sources"] == {
        "own": "recognition",
        "skill_level": "manual",
        "masteries": "manual",
        "modules": "agent",
    }
    assert rows["char_recognized_values_only"]["recognized_values"] == {
        "elite": 1,
        "level": 55,
    }
    assert rows["char_unusable_ledger"]["field_sources"] == "{not json"
    assert rows["char_unusable_ledger"]["recognized_values"] == []
    for column in TRAINING_COLUMNS:
        assert not _notnull(db_path, column), f"{column} 在 head 必须是可空的"


def test_downgrade_restores_not_null_defaults_and_upgrade_is_repeatable(tmp_path):
    db_path = tmp_path / "downgrade.db"
    cfg = _config(db_path)
    command.upgrade(cfg, PRE_REVISION)
    _seed_real_shapes(db_path)
    command.upgrade(cfg, HEAD_REVISION)

    command.downgrade(cfg, PRE_REVISION)
    for column in TRAINING_COLUMNS:
        assert _notnull(db_path, column), f"{column} 回退后必须恢复 NOT NULL"
    legacy = _rows(db_path)
    assert _training(legacy["char_own_only"]) == LEGACY_DEFAULTS
    assert _training(legacy["char_manual_training"]) == {
        "elite": 0,
        "level": 0,
        "potential": 0,
        "skill_level": 7,
        "masteries": {"1": 3, "2": 3, "3": 0},
        "modules": {"X": 3},
    }

    # 回退再升级：人工写过的值不会被回填误伤，剩下的又都变回未知。
    command.upgrade(cfg, HEAD_REVISION)
    again = _rows(db_path)
    assert _training(again["char_manual_training"]) == {
        "elite": None,
        "level": None,
        "potential": None,
        "skill_level": 7,
        "masteries": {"1": 3, "2": 3, "3": 0},
        "modules": {"X": 3},
    }
    assert _training(again["char_own_only"]) == dict.fromkeys(TRAINING_COLUMNS)


def test_offline_render_carries_the_backfill_sql(tmp_path):
    """离线 ``upgrade head --sql`` 没有真连接，回填必须也是可直接渲染的 SQL。"""
    db_path = tmp_path / "offline.db"
    cfg = _config(db_path)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        command.upgrade(cfg, "head", sql=True)
    sql = buffer.getvalue()

    assert "account_operator" in sql
    for column in TRAINING_COLUMNS:
        assert f"SET {column} = NULL" in sql
    assert "json_extract" in sql
    assert not db_path.exists(), "离线模式不应创建数据库文件"
