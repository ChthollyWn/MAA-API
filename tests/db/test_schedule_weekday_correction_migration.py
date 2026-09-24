"""Regression coverage for the 0004 legacy weekday-convention repair."""

import contextlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from alembic import command
from alembic.config import Config

from maa_api.services.schedule_service import cron_trigger


REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
PRE_REPAIR_REVISION = "0003_pipeline_device_deferral"
HEAD_REVISION = "0004_fix_schedule_weekday"
NAME_PREFIX = "daily_task_weekday_"


def _config(db_path: Path, source_path: Path) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    cfg.set_main_option("daily_task_json", str(source_path))
    return cfg


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(db_path: Path) -> dict[str, dict]:
    with contextlib.closing(_connect(db_path)) as conn:
        return {
            row["name"]: dict(row)
            for row in conn.execute(
                "select id, name, cron, timezone, enabled, template, priority,"
                " misfire_grace_seconds, catch_up, skip_if_running"
                " from schedule order by name"
            )
        }


def _seed_schedule(
    conn: sqlite3.Connection,
    *,
    name: str,
    cron: str,
    template: str = '[{"name":"StartUp","custom":{"keep":true}}]',
    timezone_name: str = "Asia/Shanghai",
    enabled: int = 1,
    priority: int = 2,
) -> None:
    conn.execute(
        "insert into schedule (id, name, cron, timezone, enabled, template,"
        " priority, misfire_grace_seconds, catch_up, skip_if_running,"
        " created_at, updated_at)"
        " values (?, ?, ?, ?, ?, ?, ?, 300, 0, 1,"
        " '2026-01-01 00:00:00', '2026-01-01 00:00:00')",
        (f"id-{name}", name, cron, timezone_name, enabled, template, priority),
    )


def _legacy_cron(weekday: int) -> str:
    return f"0 7,19 * * {weekday}"


def _posix_weekday(legacy_weekday: int) -> int:
    return (legacy_weekday + 1) % 7


def _assert_next_fire_day(cron: str, legacy_weekday: int) -> None:
    """M9-07 cron parsing must fire on the Python weekday intended by old data."""
    zone = ZoneInfo("Asia/Shanghai")
    preceding_day = {
        0: datetime(2026, 9, 20, 6, 59, tzinfo=zone),  # Sunday before Monday
        5: datetime(2026, 9, 25, 6, 59, tzinfo=zone),  # Friday before Saturday
        6: datetime(2026, 9, 19, 6, 59, tzinfo=zone),  # Saturday before Sunday
    }[legacy_weekday]
    next_fire = cron_trigger(cron, "Asia/Shanghai").get_next_fire_time(
        None, preceding_day
    )
    assert next_fire is not None
    assert next_fire.weekday() == legacy_weekday
    assert (next_fire.hour, next_fire.minute) == (7, 0)


def test_upgrade_downgrade_upgrade_repairs_only_untouched_legacy_defaults(tmp_path):
    db_path = tmp_path / "schedules.db"
    # Keep 0002 from reading the real resource file. Insert known old rows after
    # reaching 0003, which also represents databases already deployed there.
    source_path = tmp_path / "daily_task.json"
    cfg = _config(db_path, source_path)
    command.upgrade(cfg, PRE_REPAIR_REVISION)

    with contextlib.closing(_connect(db_path)) as conn:
        for weekday in range(7):
            _seed_schedule(
                conn,
                name=f"{NAME_PREFIX}{weekday}",
                # Simulate an existing installation where key 2's exact migrated
                # row has already received a user-edited cron.
                cron=_legacy_cron(weekday) if weekday != 2 else "15 9 * * 3",
                template=json.dumps(
                    [{"name": "StartUp", "weekday": weekday, "preserve": [1, 2]}]
                ),
                enabled=weekday % 2,
                priority=weekday % 3,
            )
        # Similar names and non-migrated schedules are outside the exact
        # name + default-cron pair the repair is allowed to touch.
        _seed_schedule(
            conn,
            name=f"{NAME_PREFIX}1-edited",
            cron=_legacy_cron(1),
            template='[{"name":"custom","keep":"template"}]',
        )
        _seed_schedule(
            conn,
            name="weekly-user-schedule",
            cron=_legacy_cron(0),
            template='[{"name":"custom-user"}]',
            timezone_name="UTC",
            enabled=0,
            priority=1,
        )
        _seed_schedule(
            conn,
            name=f"{NAME_PREFIX}7",
            cron=_legacy_cron(7),
            template='[{"name":"outside-supported-range"}]',
        )
        conn.commit()

    before = _rows(db_path)
    command.upgrade(cfg, HEAD_REVISION)
    repaired = _rows(db_path)

    for weekday in range(7):
        name = f"{NAME_PREFIX}{weekday}"
        if weekday == 2:
            assert repaired[name] == before[name]
            continue
        expected_cron = f"0 7,19 * * {_posix_weekday(weekday)}"
        assert repaired[name]["cron"] == expected_cron
        assert {key: value for key, value in repaired[name].items() if key != "cron"} == {
            key: value for key, value in before[name].items() if key != "cron"
        }
    for name in (f"{NAME_PREFIX}1-edited", "weekly-user-schedule", f"{NAME_PREFIX}7"):
        assert repaired[name] == before[name]

    # Check across the offset boundary using M9-07's POSIX parser: Monday=legacy
    # 0, Saturday=5, Sunday=6.
    for weekday in (0, 5, 6):
        _assert_next_fire_day(repaired[f"{NAME_PREFIX}{weekday}"]["cron"], weekday)

    # A user edit after upgrade must survive downgrade. Rows still at the corrected
    # default return to their 0002 values, then a later upgrade repairs them again.
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute(
            "update schedule set cron=? where name=?",
            ("5 11 * * 1", f"{NAME_PREFIX}0"),
        )
        conn.commit()
    command.downgrade(cfg, PRE_REPAIR_REVISION)
    downgraded = _rows(db_path)

    assert downgraded[f"{NAME_PREFIX}0"]["cron"] == "5 11 * * 1"
    for weekday in range(1, 7):
        expected = "15 9 * * 3" if weekday == 2 else _legacy_cron(weekday)
        assert downgraded[f"{NAME_PREFIX}{weekday}"]["cron"] == expected
    for name in (f"{NAME_PREFIX}1-edited", "weekly-user-schedule", f"{NAME_PREFIX}7"):
        assert downgraded[name] == before[name]

    command.upgrade(cfg, HEAD_REVISION)
    upgraded_again = _rows(db_path)
    assert upgraded_again[f"{NAME_PREFIX}0"]["cron"] == "5 11 * * 1"
    for weekday in range(1, 7):
        name = f"{NAME_PREFIX}{weekday}"
        if weekday == 2:
            assert upgraded_again[name] == before[name]
            continue
        assert upgraded_again[name]["cron"] == f"0 7,19 * * {_posix_weekday(weekday)}"
        if weekday in (5, 6):
            _assert_next_fire_day(upgraded_again[name]["cron"], weekday)
    for name in (f"{NAME_PREFIX}1-edited", "weekly-user-schedule", f"{NAME_PREFIX}7"):
        assert upgraded_again[name] == before[name]


def test_fresh_migration_preserves_old_weekday_intent(tmp_path):
    """The real 0002 → 0003 → 0004 chain corrects rows sourced from daily_task.json."""
    db_path = tmp_path / "fresh.db"
    source_path = tmp_path / "daily_task.json"
    source_path.write_text(
        json.dumps(
            {
                "task_dict": {"routine": [{"name": "StartUp"}]},
                "weekday_task": {"0": "routine", "5": "routine", "6": "routine"},
            }
        ),
        encoding="utf-8",
    )
    cfg = _config(db_path, source_path)

    command.upgrade(cfg, HEAD_REVISION)
    got = _rows(db_path)

    for legacy_weekday in (0, 5, 6):
        name = f"{NAME_PREFIX}{legacy_weekday}"
        assert got[name]["cron"] == f"0 7,19 * * {_posix_weekday(legacy_weekday)}"
        _assert_next_fire_day(got[name]["cron"], legacy_weekday)
