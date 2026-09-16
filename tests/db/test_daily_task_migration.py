"""M2-05 验收：0002 把 ``resource/daily_task.json`` 逐日展开成 schedule 记录。

纪律：

- 全程 tmp 库 + tmp 源文件：通过 ``daily_task_json`` 配置项把迁移指向 ``tmp_path``
  里的合成配置，绝不改动仓库的 ``resource/daily_task.json``。只有一个用例读真实
  配置，并在前后校验 sha256 不变；``resource/`` 在 ``.gitignore`` 里，文件不在的
  检出上该用例跳过。
- 只走 Alembic 建库/迁移，不用 ``create_all``（docs/04 §8.3）。
- 无 pytest-asyncio，全部同步测试函数；不需要新增 pytest 插件。
"""

import contextlib
import hashlib
import io
import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from maa_api.domain.enums import Priority

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
MIGRATIONS_DIR = REPO_ROOT / "maa_api" / "db" / "migrations"
REAL_SOURCE = REPO_ROOT / "resource" / "daily_task.json"

BASE_REVISION = "0001"
HEAD_REVISION = "0002"
NAME_PREFIX = "daily_task_weekday_"

#: 合成配置：三个星期、三组任务，含空组；组内容刻意不规整，用来证明 template 未被预处理。
SAMPLE = {
    "enable": True,
    "task_dict": {
        "earth": [
            {"name": "StartUp", "client_type": "Bilibili", "start_game_enabled": True},
            {"name": "Fight", "stage": "1-7", "medicine": 0},
        ],
        "money": [{"name": "Fight", "stage": "CE-6"}],
        "empty": [],
    },
    "weekday_task": {"0": "earth", "1": "money", "6": "empty"},
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def write_source(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def make_config(db_path: Path, source_path: Path | None) -> Config:
    """指向 tmp 库的 Alembic 配置；``source_path`` 为 None 时不注入，走默认路径。"""
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    if source_path is not None:
        cfg.set_main_option("daily_task_json", str(source_path))
    return cfg


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def rows(db_path: Path) -> list[dict]:
    """schedule 表内容（不含两个时间戳列，便于跨次 upgrade 全等比较）。"""
    with contextlib.closing(_connect(db_path)) as conn:
        return [
            dict(row)
            for row in conn.execute(
                "select id, name, cron, timezone, enabled, template, priority,"
                " misfire_grace_seconds, catch_up, skip_if_running,"
                " last_pipeline_id, last_result from schedule order by name"
            )
        ]


def version(db_path: Path) -> str | None:
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute("select version_num from alembic_version").fetchone()
        return None if row is None else row[0]


def insert_user_schedule(db_path: Path, name: str) -> None:
    """插一条与迁移无关的用户自建 schedule（用于降级不误删的断言）。"""
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute(
            "insert into schedule (id, name, cron, timezone, enabled, template,"
            " priority, misfire_grace_seconds, catch_up, skip_if_running,"
            " created_at, updated_at)"
            " values (?, ?, '0 8 * * *', 'Asia/Shanghai', 1, '[]', 2, 300, 0, 1,"
            " '2026-01-01 00:00:00', '2026-01-01 00:00:00')",
            (f"user-{name}", name),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# 展开规则
# ---------------------------------------------------------------------------
def test_upgrade_expands_every_weekday(tmp_path):
    """每个 weekday 一条记录：cron 带该星期，template 取对应任务组，其余列用默认值。"""
    source = write_source(tmp_path / "daily_task.json", SAMPLE)
    db = tmp_path / "m.db"
    command.upgrade(make_config(db, source), "head")

    got = {row["name"]: row for row in rows(db)}
    assert set(got) == {
        f"{NAME_PREFIX}0",
        f"{NAME_PREFIX}1",
        f"{NAME_PREFIX}6",
    }
    assert {row["cron"] for row in got.values()} == {
        "0 7,19 * * 0",
        "0 7,19 * * 1",
        "0 7,19 * * 6",
    }

    for weekday, group in SAMPLE["weekday_task"].items():
        row = got[f"{NAME_PREFIX}{weekday}"]
        assert row["timezone"] == "Asia/Shanghai"
        assert row["enabled"] == 1
        assert row["priority"] == int(Priority.SCHEDULED)
        assert row["misfire_grace_seconds"] == 300
        assert row["catch_up"] == 0
        assert row["skip_if_running"] == 1
        assert row["last_pipeline_id"] is None
        assert row["last_result"] is None
        # template 逐字等于 json.dumps(task_dict[group])，不做任何预处理
        assert row["template"] == json.dumps(SAMPLE["task_dict"][group], ensure_ascii=False)
        assert json.loads(row["template"]) == SAMPLE["task_dict"][group]

    with contextlib.closing(_connect(db)) as conn:
        null_ts = conn.execute(
            "select count(*) from schedule where created_at is null or updated_at is null"
        ).fetchone()[0]
    assert null_ts == 0


@pytest.mark.parametrize("enable, expected", [(False, 0), (None, 1)])
def test_enabled_follows_config_enable(tmp_path, enable, expected):
    """enabled 取文件里的 enable；键缺失按 1（开启）。"""
    payload = json.loads(json.dumps(SAMPLE))
    if enable is None:
        payload.pop("enable")
    else:
        payload["enable"] = enable
    source = write_source(tmp_path / "daily_task.json", payload)
    db = tmp_path / "m.db"
    command.upgrade(make_config(db, source), "head")

    got = rows(db)
    assert len(got) == 3
    assert {row["enabled"] for row in got} == {expected}


def test_unknown_task_group_is_skipped(tmp_path):
    """weekday 指向不存在的组：只告警跳过这一天，不阻塞迁移。"""
    payload = {
        "enable": True,
        "task_dict": {"earth": [{"name": "Award"}]},
        "weekday_task": {"0": "earth", "2": "missing", "5": "earth"},
    }
    source = write_source(tmp_path / "daily_task.json", payload)
    db = tmp_path / "m.db"
    command.upgrade(make_config(db, source), "head")

    assert [row["name"] for row in rows(db)] == [
        f"{NAME_PREFIX}0",
        f"{NAME_PREFIX}5",
    ]


# ---------------------------------------------------------------------------
# 可重复升级 / 降级
# ---------------------------------------------------------------------------
def test_upgrade_downgrade_upgrade_does_not_duplicate(tmp_path):
    """upgrade → downgrade → upgrade 回到同一批记录（id 也一致），重复 upgrade 是空操作。"""
    source = write_source(tmp_path / "daily_task.json", SAMPLE)
    db = tmp_path / "m.db"
    cfg = make_config(db, source)

    command.upgrade(cfg, "head")
    first = rows(db)
    assert len(first) == 3
    assert version(db) == HEAD_REVISION

    command.upgrade(cfg, "head")  # Alembic 层幂等
    assert rows(db) == first

    command.downgrade(cfg, "-1")
    assert rows(db) == []  # 表还在，行被删干净
    assert version(db) == BASE_REVISION

    command.upgrade(cfg, "head")
    assert rows(db) == first  # 不产生重复记录，且 uuid5 派生 id 稳定


def test_downgrade_only_removes_migrated_rows(tmp_path):
    """降级按 name 前缀精确删除，不碰用户自建的 schedule。"""
    source = write_source(tmp_path / "daily_task.json", SAMPLE)
    db = tmp_path / "m.db"
    cfg = make_config(db, source)
    command.upgrade(cfg, "head")
    insert_user_schedule(db, "morning-routine")

    command.downgrade(cfg, "-1")
    assert [row["name"] for row in rows(db)] == ["morning-routine"]

    command.upgrade(cfg, "head")
    assert [row["name"] for row in rows(db)] == [
        f"{NAME_PREFIX}0",
        f"{NAME_PREFIX}1",
        f"{NAME_PREFIX}6",
        "morning-routine",
    ]


def test_upgrade_skips_names_that_already_exist(tmp_path):
    """同名前缀的记录已存在时不重复插入，也不撞 uq_schedule_name。"""
    source = write_source(tmp_path / "daily_task.json", SAMPLE)
    db = tmp_path / "m.db"
    cfg = make_config(db, source)
    command.upgrade(cfg, BASE_REVISION)
    insert_user_schedule(db, f"{NAME_PREFIX}0")

    command.upgrade(cfg, "head")
    assert [row["name"] for row in rows(db)] == [
        f"{NAME_PREFIX}0",
        f"{NAME_PREFIX}1",
        f"{NAME_PREFIX}6",
    ]


# ---------------------------------------------------------------------------
# 文件不存在 / 真实配置 / 离线模式
# ---------------------------------------------------------------------------
def test_missing_source_file_is_skipped(tmp_path):
    """源文件不存在：不报错、不插入，迁移链照常推进到 head 并可回退。"""
    db = tmp_path / "m.db"
    cfg = make_config(db, tmp_path / "not-there.json")
    assert not (tmp_path / "not-there.json").exists()

    command.upgrade(cfg, "head")
    assert version(db) == HEAD_REVISION
    assert rows(db) == []

    command.downgrade(cfg, "-1")
    assert version(db) == BASE_REVISION


def test_real_repo_source_is_migrated_and_left_untouched(tmp_path):
    """真实 resource/daily_task.json：7 天 7 条，文件内容与存在性都不变。"""
    if not REAL_SOURCE.is_file():
        pytest.skip("resource/daily_task.json 不在（resource/ 被 .gitignore 忽略）")
    before = hashlib.sha256(REAL_SOURCE.read_bytes()).hexdigest()
    raw = json.loads(REAL_SOURCE.read_text(encoding="utf-8"))

    db = tmp_path / "m.db"
    command.upgrade(make_config(db, None), "head")  # 不注入 → 走默认路径

    got = {row["name"]: row for row in rows(db)}
    assert set(got) == {f"{NAME_PREFIX}{day}" for day in raw["weekday_task"]}
    assert len(got) == 7
    for day, group in raw["weekday_task"].items():
        assert got[f"{NAME_PREFIX}{day}"]["cron"] == f"0 7,19 * * {day}"
        assert json.loads(got[f"{NAME_PREFIX}{day}"]["template"]) == raw["task_dict"][group]

    # 迁移后原文件保留不删、内容不变（回滚依据，docs/04 §11）
    assert REAL_SOURCE.is_file()
    assert hashlib.sha256(REAL_SOURCE.read_bytes()).hexdigest() == before


def test_offline_sql_carries_data_without_creating_db(tmp_path):
    """`alembic upgrade head --sql` 渲染带字面量值的 INSERT，且不连库、不建库文件。

    离线模式的 mock connection 会丢弃 execute 的执行参数，所以值必须内联在语句里
    （见迁移模块 docstring 第 6 条）；这里断言没有 `?` 占位符。
    """
    source = write_source(tmp_path / "daily_task.json", SAMPLE)
    db = tmp_path / "offline.db"
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        command.upgrade(make_config(db, source), "head", sql=True)
    sql = buffer.getvalue()

    inserts = [line for line in sql.splitlines() if "INSERT INTO schedule" in line]
    assert len(inserts) == 3, inserts
    assert all("?" not in line for line in inserts), inserts
    assert f"'{NAME_PREFIX}0'" in sql
    assert "'0 7,19 * * 0'" in sql
    assert not db.exists(), "离线模式不应创建数据库文件"
