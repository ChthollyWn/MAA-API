"""``maa_api/db/migrate.py``：自动迁移到 head、迁移前备份、失败中止启动。

测试纪律：

- 所有库文件都落在 ``tmp_path``：靠 monkeypatch ``maa_api.db.session`` 的模块属性
  （``DB_PATH`` / ``SYNC_URL`` / ``ASYNC_URL``）把运行库指过去，绝不碰仓库里的
  ``resource/maa_api.db``。
- 本仓没有 pytest-asyncio：全部是同步测试函数 + ``asyncio.run(...)``。
- 建库只走 ``ensure_schema`` 的 Alembic 路径（首次建库也是 ``upgrade head``），
  不用任何模型元数据的建表快捷方式 —— 本卡验的就是生产路径本身。
- ``ensure_schema`` 按仓库根相对路径解析 ``alembic.ini``，autouse fixture 固定 cwd。
"""

import asyncio
import contextlib
import inspect
import re
import shutil
import sqlite3
import threading
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlmodel import SQLModel

import maa_api.db.migrate as migrate
import maa_api.db.models  # noqa: F401  必须 import 才能注册全部表供比对
from maa_api.db import session

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _repo_root_cwd(monkeypatch):
    """``Config("alembic.ini")`` 与 ``script_location`` 都相对仓库根解析。"""
    monkeypatch.chdir(REPO_ROOT)


@pytest.fixture
def db_path(tmp_path, monkeypatch) -> Path:
    """把三条运行库路径指到 tmp 目录；ensure_schema 在调用时读模块属性。"""
    path = tmp_path / "maa_api.db"
    monkeypatch.setattr(session, "DB_PATH", path)
    monkeypatch.setattr(session, "SYNC_URL", f"sqlite:///{path}")
    monkeypatch.setattr(session, "ASYNC_URL", f"sqlite+aiosqlite:///{path}")
    return path


def alembic_cfg(db_path: Path) -> Config:
    """测试侧直接驱动 Alembic 用的配置（造出「有待应用迁移」的状态）。"""
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "maa_api" / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def head_revision() -> str:
    """脚本目录里的 head（只读 alembic.ini 与 versions/，不连库）。"""
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "maa_api" / "db" / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def backups(db_path: Path) -> list[Path]:
    backup_dir = db_path.parent / "backup"
    return sorted(backup_dir.glob("*.bak")) if backup_dir.exists() else []


def table_names(path: Path) -> set[str]:
    with contextlib.closing(sqlite3.connect(path)) as conn:
        return {
            row[0]
            for row in conn.execute("select name from sqlite_master where type='table'")
        }


def version(path: Path) -> str | None:
    with contextlib.closing(sqlite3.connect(path)) as conn:
        rows = conn.execute("select version_num from alembic_version").fetchall()
    return rows[0][0] if rows else None


def make_pending(db_path: Path) -> None:
    """建到 head 再退回一个版本：库存在、有待应用迁移，正是备份的触发条件。"""
    command.downgrade(alembic_cfg(db_path), "-1")
    assert version(db_path) != head_revision()
    assert migrate._has_pending_migrations() is True


# ---------------------------------------------------------------------------
# 首次建库 / 无待应用迁移
# ---------------------------------------------------------------------------
def test_first_start_creates_schema_without_backup(db_path):
    """新库直接 upgrade head：13 张表 + 版本行都在，且不产生备份。"""
    assert not db_path.exists()

    asyncio.run(migrate.ensure_schema())

    assert db_path.exists()
    names = table_names(db_path)
    assert set(SQLModel.metadata.tables) <= names, sorted(
        set(SQLModel.metadata.tables) - names
    )
    assert "alembic_version" in names
    assert version(db_path) == head_revision()
    assert backups(db_path) == []
    # 没有待应用迁移就不该建备份目录，避免空目录与每次重启多一份文件
    assert not (db_path.parent / "backup").exists()


def test_second_start_without_pending_migrations_backs_up_nothing(db_path):
    """已是最新版本时重复 ensure_schema 是空操作，版本不变、无新备份。"""
    asyncio.run(migrate.ensure_schema())
    assert migrate._has_pending_migrations() is False

    asyncio.run(migrate.ensure_schema())

    assert backups(db_path) == []
    assert version(db_path) == head_revision()


# ---------------------------------------------------------------------------
# 有待应用迁移：先备份
# ---------------------------------------------------------------------------
def test_pending_migration_backs_up_before_upgrade(db_path):
    """备份必须发生在 upgrade 之前：备份里是迁移前的版本，主库随后到 head。"""
    asyncio.run(migrate.ensure_schema())
    make_pending(db_path)          # downgrade -1：版本回到 0001，head 是 0002

    asyncio.run(migrate.ensure_schema())

    found = backups(db_path)
    assert len(found) == 1, [path.name for path in found]
    backup = found[0]
    assert re.fullmatch(
        rf"{re.escape(db_path.name)}\.\d{{8}}-\d{{6}}-\d{{6}}\.bak", backup.name
    ), backup.name

    # 备份是 upgrade 之前的快照（0001），且是完整可打开的 SQLite 库
    assert version(backup) == "0001"
    assert "alembic_version" in table_names(backup)
    assert set(SQLModel.metadata.tables) <= table_names(backup)

    # 主库已迁到 head，业务表仍齐全
    assert version(db_path) == head_revision()
    assert set(SQLModel.metadata.tables) <= table_names(db_path)


def test_backup_retention_keeps_latest_five(db_path):
    """备份只保留最近 5 份：最旧的一份被裁掉，新备份保留。"""
    assert migrate.BACKUP_KEEP == 5
    asyncio.run(migrate.ensure_schema())
    backup_dir = db_path.parent / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)

    seeded = []
    for index in range(1, 6):       # 5 份「旧」备份，名字的字典序即新旧顺序
        path = backup_dir / f"{db_path.name}.20000101-00000{index}-000000.bak"
        path.write_bytes(b"seeded")
        seeded.append(path)

    make_pending(db_path)
    asyncio.run(migrate.ensure_schema())

    remaining = backups(db_path)
    assert len(remaining) == migrate.BACKUP_KEEP, [path.name for path in remaining]
    assert seeded[0] not in remaining, "最旧的一份必须被裁掉"
    assert remaining[:-1] == seeded[1:]
    assert remaining[-1] not in seeded, "刚创建的备份不能被裁掉"
    assert version(remaining[-1]) == "0001"


# ---------------------------------------------------------------------------
# 失败路径：异常原样抛出，不吞掉
# ---------------------------------------------------------------------------
def test_migration_failure_propagates_and_names_backup(db_path, monkeypatch, capsys):
    """迁移失败必须中止启动：异常原样上抛，stderr 指出迁移前备份路径。"""
    asyncio.run(migrate.ensure_schema())
    make_pending(db_path)

    def boom() -> None:
        raise RuntimeError("alembic upgrade 炸了")

    monkeypatch.setattr(migrate, "_upgrade_head", boom)

    with pytest.raises(RuntimeError, match="alembic upgrade 炸了"):
        asyncio.run(migrate.ensure_schema())

    found = backups(db_path)
    assert len(found) == 1, [path.name for path in found]
    stderr = capsys.readouterr().err
    assert str(found[0]) in stderr, stderr
    assert "启动中止" in stderr
    # 没有被吞掉后「继续跑」：主库停在迁移前版本
    assert version(db_path) == "0001"


def test_first_start_failure_raises_without_backup(db_path, monkeypatch, capsys):
    """首次建库就失败时没有备份可用，但异常同样必须上抛（不吞、不建半成品库）。"""
    def boom() -> None:
        raise RuntimeError("first start boom")

    monkeypatch.setattr(migrate, "_upgrade_head", boom)

    with pytest.raises(RuntimeError, match="first start boom"):
        asyncio.run(migrate.ensure_schema())

    assert backups(db_path) == []
    stderr = capsys.readouterr().err
    assert "没有迁移前备份" in stderr, stderr


# ---------------------------------------------------------------------------
# 硬约束的机制验证
# ---------------------------------------------------------------------------
def test_upgrade_runs_in_worker_thread_not_event_loop(db_path, monkeypatch):
    """``command.upgrade`` 必须跑在线程池里（docs/04 §8.3），不冻结事件循环。"""
    seen: dict[str, object] = {}

    def probe() -> None:
        seen["thread"] = threading.current_thread()

    monkeypatch.setattr(migrate, "_upgrade_head", probe)

    asyncio.run(migrate.ensure_schema())

    assert inspect.iscoroutinefunction(migrate.ensure_schema)
    assert seen["thread"] is not threading.main_thread()
    assert seen["thread"].ident != threading.main_thread().ident


def test_backup_captures_wal_content_instead_of_naive_file_copy(db_path):
    """备份用 VACUUM INTO：还在 ``-wal`` 里、未 checkpoint 的已提交数据也要在。

    做法是保持一条 WAL 连接不关（最后一条连接关闭会触发 checkpoint，那样就区分不出
    ``VACUUM INTO`` 与文件拷贝了），在它上面提交一行 setting，再触发一次带迁移的
    ``ensure_schema``。用例里同时做一次裸文件拷贝作对照，证明这个场景确实能区分两种
    实现：只拷 ``.db`` 的实现拿不到还在 ``-wal`` 里的这行。
    """
    asyncio.run(migrate.ensure_schema())
    make_pending(db_path)

    wal_conn = sqlite3.connect(db_path)
    try:
        assert wal_conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        wal_conn.execute(
            "insert into setting (key, value, updated_at) values (?, ?, ?)",
            ("probe_wal", "wal-marker", "2026-01-01 00:00:00"),
        )
        wal_conn.commit()           # 连接保持打开：数据留在 -wal 里

        naive_copy = db_path.parent / "naive-copy.db"
        shutil.copyfile(db_path, naive_copy)
        with contextlib.closing(sqlite3.connect(naive_copy)) as conn:
            missing = conn.execute(
                "select value from setting where key = 'probe_wal'"
            ).fetchall()
        assert missing == [], "对照失效：裸拷贝也拿到了 -wal 数据，本用例不再能区分实现"

        asyncio.run(migrate.ensure_schema())
    finally:
        wal_conn.close()

    found = backups(db_path)
    assert len(found) == 1, [path.name for path in found]
    with contextlib.closing(sqlite3.connect(found[0])) as conn:
        rows = conn.execute(
            "select value from setting where key = 'probe_wal'"
        ).fetchall()
    assert rows == [("wal-marker",)], "VACUUM INTO 快照必须包含 -wal 里的已提交数据"


def test_module_keeps_the_two_build_path_disciplines():
    """源码级纪律：线程池调用在、模型元数据建表捷径不在（docs/04 §8.3）。"""
    source = inspect.getsource(migrate)
    assert "to_thread" in source
    assert "VACUUM INTO" in source
    assert "create_all" not in source
