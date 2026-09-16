"""应用启动时的自动迁移（docs/04 §8.3）。

``ensure_schema()`` 是 lifespan 的第 2 步（docs/02 §7），必须早于 ``LogHub`` 与
``CoreSupervisor``：schema 不匹配时服务能起来但每个请求都在报 ``no such column``，
比直接起不来更难排查，所以迁移失败一律让异常冒泡、中止启动。

四条纪律（docs/04 §8.3，逐条落到实现里）：

- **迁移跑在线程池里**。``command.upgrade`` 是同步阻塞调用，直接在 lifespan 的
  协程里跑会冻结事件循环，连 uvicorn 的信号处理都会一起卡住（Ctrl-C 杀不掉），
  因此统一经 :func:`asyncio.to_thread` 进入。
- **有待应用的迁移时先备份**。用 ``VACUUM INTO`` 而不是文件拷贝，前者在 WAL
  存在时也能拿到一致快照；只在确有待应用迁移时备份（否则每次重启都多一份），
  并且只保留最近 :data:`BACKUP_KEEP` 份。
- **首次建库也走 ``upgrade head``**。两条建表路径混用（迁移 + 模型元数据的建表
  快捷方式）会让 ``alembic_version`` 与真实 schema 漂移：快捷方式建出的是当前
  代码的最新结构，版本表却是空的，下次启动从头重放并撞上 table already exists。
- **迁移失败中止启动**。异常原样抛出，同时在 stderr 打印迁移前备份的路径，
  人工回滚有据可依（备份路径只在真的产生了备份时才存在）。

**import 策略：本模块只依赖标准库**，``alembic`` / ``sqlalchemy`` 与
``maa_api.db.session`` 都延迟到调用点 import。两个原因：

1. ``maa_api.db.session`` 在 import 期就会 ``make_engine()`` 造一个异步引擎；
   只做迁移入口的进程没必要背上这个副作用，延迟 import 让本模块 import 期零副作用。
2. 本模块的契约（``ensure_schema`` 是 async、升级经线程池、不混第二条建表路径）
   因此可以在**没有数据库栈的解释器**上静态检查 —— 验收命令用的是裸 ``python3``。

可测性契约（M2-05 与后续里程碑依赖）：所有路径都在**调用时**从
``maa_api.db.session`` 的模块属性读取（``session.DB_PATH`` / ``session.SYNC_URL``），
不固化取值；``alembic.ini`` 与 ``script_location`` 按仓库根相对路径解析，
调用方 cwd 必须是仓库根。
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - 只给类型标注用，运行期不 import 数据库栈
    from alembic.config import Config

#: Alembic 配置与迁移目录，按仓库根相对路径解析（调用方 cwd = 仓库根）。
ALEMBIC_INI = "alembic.ini"
SCRIPT_LOCATION = "maa_api/db/migrations"

#: 迁移前备份保留份数（docs/04 §8.3：最近 5 份）。
BACKUP_KEEP = 5
#: 备份文件后缀；备份目录是 ``DB_PATH.parent / "backup"``（默认 ``resource/backup``）。
BACKUP_SUFFIX = ".bak"
#: 备份文件名里的时间戳。带微秒是刻意的：``VACUUM INTO`` 拒绝写入已存在的目标，
#: 而秒级时间戳在同一秒内二次备份会撞名；微秒精度同时保证文件名字典序 = 创建顺序，
#: 保留策略因此可以只按名字排序，不依赖 mtime 的精度。
TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S-%f"


def _session():
    """取 ``maa_api.db.session`` 模块本身；**调用时**才 import 并读模块属性。

    测试靠 monkeypatch 该模块的 ``DB_PATH`` / ``SYNC_URL`` 把运行库指到 tmp 目录，
    所以任何取值都必须走这里，不能在模块顶层固化。
    """
    from maa_api.db import session

    return session


def _sync_engine():
    """造一个指向当前运行库的同步引擎（Alembic 与备份都用 ``sqlite:///``）。"""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    return create_engine(_session().SYNC_URL, poolclass=NullPool)


def _alembic_config() -> Config:
    """构造指向当前运行库的 Alembic 配置（URL 每次都重新读，见 :func:`_session`）。"""
    from alembic.config import Config

    cfg = Config(ALEMBIC_INI)
    cfg.set_main_option("script_location", SCRIPT_LOCATION)
    cfg.set_main_option("sqlalchemy.url", _session().SYNC_URL)
    return cfg


def _current_revision() -> str | None:
    """读库里的 ``alembic_version``；空库（没有版本表）返回 ``None``。

    用 Alembic 自己的 :class:`MigrationContext` 而不是裸 SQL：版本表名与多分支
    语义归 Alembic 管，读法要跟它一致。
    """
    from alembic.runtime.migration import MigrationContext

    engine = _sync_engine()
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def _has_pending_migrations() -> bool:
    """库里是否有尚未应用的迁移；版本与 head 一致时为 ``False``。"""
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    return _current_revision() != head


def _backup_dir() -> Path:
    """备份目录由库文件位置推导（默认 ``resource/backup/``），不硬编码。"""
    return _session().DB_PATH.parent / "backup"


def _new_backup_path() -> Path:
    """下一个备份文件名：``<db name>.<YYYYmmdd-HHMMSS-ffffff>.bak``。"""
    stamp = datetime.now().strftime(TIMESTAMP_FORMAT)
    return _backup_dir() / f"{_session().DB_PATH.name}.{stamp}{BACKUP_SUFFIX}"


def _prune_backups(keep: int = BACKUP_KEEP) -> list[Path]:
    """只保留最近 ``keep`` 份备份，返回被删掉的文件。

    时间戳精度到微秒，所以名字排序就是创建顺序；glob 限定当前库的名字前缀，
    不会误删备份目录里其他来源的文件。
    """
    pattern = f"{_session().DB_PATH.name}.*{BACKUP_SUFFIX}"
    backups = sorted(_backup_dir().glob(pattern))
    stale = backups[:-keep] if keep > 0 else backups
    for path in stale:
        path.unlink()
    return stale


def _backup_database() -> Path:
    """在迁移写库之前用 ``VACUUM INTO`` 拿一份一致快照，并裁剪旧备份。

    不用文件拷贝：库处于 WAL 模式时，已提交但尚未 checkpoint 的数据还在
    ``-wal`` 里，只拷 ``.db`` 会得到过期快照甚至损坏文件；``VACUUM INTO``
    由 SQLite 自己在一致读事务里导出，天然包含 WAL 内容（docs/04 §8.3）。
    """
    backup_dir = _backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = _new_backup_path()
    # SQLite 字符串字面量里只有单引号需要转义（双写），反斜杠不是转义符。
    escaped = str(target).replace("'", "''")

    engine = _sync_engine()
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql(f"VACUUM INTO '{escaped}'")
    finally:
        engine.dispose()

    _prune_backups()
    return target


def _upgrade_head() -> None:
    """同步执行 ``alembic upgrade head``。

    这是阻塞调用，只能经 :func:`asyncio.to_thread` 进入，不要在协程里直接调。
    """
    from alembic import command

    command.upgrade(_alembic_config(), "head")


async def ensure_schema() -> None:
    """建目录 → 有需要则先备份 → 在线程池里迁移到 head；失败即中止启动。

    启动路径上的唯一入口：新库直接 ``upgrade head`` 建表（不走任何建表快捷方式），
    已有库只在检测到待应用迁移时备份，迁移异常原样抛出。
    """
    db_path = _session().DB_PATH      # 调用时读取，勿固化（见模块 docstring）
    db_path.parent.mkdir(parents=True, exist_ok=True)

    backup: Path | None = None
    if db_path.exists() and _has_pending_migrations():
        backup = _backup_database()

    try:
        await asyncio.to_thread(_upgrade_head)
    except Exception:
        # 只补一条人工回滚所需的提示，然后原样抛出：绝不 try/except 后继续启动，
        # 否则服务能起来但每个请求都报 no such column（docs/04 §8.3）。
        if backup is None:
            message = (
                "[maa-api] 数据库迁移失败，启动中止；"
                "本次没有迁移前备份（首次建库或无需备份）。"
            )
        else:
            message = f"[maa-api] 数据库迁移失败，启动中止；迁移前备份：{backup}"
        print(message, file=sys.stderr)
        raise
