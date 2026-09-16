#!/usr/bin/env python3
"""M2-13 命令行冒烟脚本：空库建库 → 迁移到 head → 各仓储族读写往返 → 保留策略清理。

这正是 docs/12 给 M2 点名的「可交付状态」：**数据库可创建、可迁移、可读写，仓储层
有单元测试覆盖**（docs/12 §M2、docs/04 §8.3/§9/§10）。单测逐个覆盖仓储的行为；
本脚本在**真库**上把整条链路串起来跑一遍，给出可执行的验收证据：

1. 在 ``tempfile.mkdtemp()`` 里建全新 SQLite 库（``<tmp>/maa_api.db``），把
   ``maa_api.db.session`` 的三条路径、引擎与会话工厂全部指过去；绝不使用、也不触碰
   仓库里的 ``resource/maa_api.db``。退出时清理临时目录（``--keep-temp`` 保留现场）。
2. 空库 → ``db/migrate.py::ensure_schema()`` 的自动迁移机制（Alembic 驱动，
   **不是** ``create_all``，docs/04 §8.3）到 head：断言 13 张业务表、``alembic_version``、
   ``auto_vacuum=INCREMENTAL(2)``（迁移 ``env.py`` 的放置，M2-01/M2-04 实测），
   且新库首次建库不产生迁移前备份。
3. ``upgrade head → downgrade -1 → （自动迁移）upgrade head`` 往返：验证 docs/12 §4
   要求的 downgrade 路径可用，以及「有待应用迁移时先 ``VACUUM INTO`` 备份」的行为
   （备份里是迁移前的版本）。当前 head 是「0001 建表 + 0002 数据」两条，尚无 batch
   重建表的迁移；本往返是通用形态，将来新增迁移自动被它覆盖。
4. 七族仓储各做一次真实的写 → 读 → 改 → 删往返（pipeline/task、log/screenshot、
   schedule、setting、agent/audit/confirmation、update/notify、resource），断言读回
   的值与写入一致，并顺带钉住库级约束：JSON 列保型、终态不可再变、状态机
   ``rowcount`` 判定、``update_record`` 的部分唯一索引、``resource_asset`` 的 CHECK、
   ``agent_audit`` 的入库前裁剪等。
5. 触发一次保留策略清理（``services/retention_service.py``），断言该删的删了、
   该留的留了：日志两维（超 30 天的 ``maa_task`` 删、新日志留）、流水线 90 天、
   截图文件 7 天 + ``deleted_at`` 标记、``temp/screencap`` 中转图 1 小时。
6. 退出码即结论：全部检查通过 exit 0 并打印 ``SMOKE OK``；任何一项失败 exit 1 并把
   失败项打在 checklist 里，绝不伪造成功。

运行约束：不 import ``maa_api.main``、不加载内核、不连设备、不需要网络；只依赖数据库栈
（SQLAlchemy / SQLModel / Alembic / aiosqlite）与标准库。本仓没有 pytest-asyncio，
脚本用 ``asyncio.run()`` 驱动异步仓储调用。

已知接口事实（照仓库现状，与卡面描述的差异以实际接口为准）：

- ``agent_session`` / ``agent_message`` / ``agent_audit`` / ``confirmation`` 四张表
  **没有删除接口**（审计按定义长期留存，docs/04 §10.3；会话删除归 M11）：本族的
  「删」以终态流转与「过期扫描 / 清除授权」表达，不做物理删除。
- ``pipeline`` 仓储没有单条 ``delete()``，删除口径是保留策略的 ``purge_before()``；
  ``task`` 随它被显式删除（不依赖调用方连接是否开了 ``PRAGMA foreign_keys``）。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback
from collections.abc import Callable, Coroutine
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

#: 直接执行脚本时 ``sys.path[0]`` 是 ``scripts/``；仓库根进 sys.path 后
#: 即使解释器没装 editable 包也能 import ``maa_api``。
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: 临时库文件名与临时目录前缀（``--keep-temp`` 时目录保留，便于排查）。
DB_FILENAME = "maa_api.db"
TEMP_PREFIX = "maa-db-smoke-"

#: ``auto_vacuum`` 的 INCREMENTAL 取值（docs/04 §2/§10.4；其余取值：0=NONE、1=FULL）。
AUTO_VACUUM_INCREMENTAL = 2

_EPILOG = """\
示例:
  # 完整冒烟 (门禁用的那条: 几秒内结束, 不需要网络/内核/设备)
  .venv/bin/python scripts/db_smoke.py

  # 失败时保留临时目录 (库文件 / 迁移备份 / 失败现场) 以便排查
  .venv/bin/python scripts/db_smoke.py --keep-temp

`--help` 只解析参数: 不 import 数据库栈、不建库、不碰任何文件。
"""


class SmokeFailure(RuntimeError):
    """冒烟步骤失败（脚本内信号，统一转成退出码 1）。"""


# ----------------------------------------------------------------------
# 无副作用的小工具（都在调用时才 import 数据库栈 / 只读文件）
# ----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 解析器（只解析参数，不 import 数据库栈）。"""
    parser = argparse.ArgumentParser(
        prog="db_smoke.py",
        description=(
            "M2-13 数据层命令行冒烟：空库建库 → Alembic 迁移到 head → "
            "upgrade/downgrade/upgrade 往返 → 各仓储族读写往返 → 保留策略清理。"
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="保留临时目录（库文件 / 迁移备份 / 失败现场），默认退出时清理",
    )
    return parser


def _head_revision() -> str:
    """从迁移脚本目录读 head（不连库）。"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "maa_api" / "db" / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def _db_version(path: Path) -> Optional[str]:
    """读库里的 ``alembic_version``；空库（没有版本表）返回 ``None``。"""
    with contextlib.closing(sqlite3.connect(path)) as conn:
        rows = conn.execute("select version_num from alembic_version").fetchall()
    return rows[0][0] if rows else None


def _table_names(path: Path) -> set[str]:
    """库里的表名集合（含 ``alembic_version`` 与 ``sqlite_sequence``）。"""
    with contextlib.closing(sqlite3.connect(path)) as conn:
        return {
            row[0]
            for row in conn.execute("select name from sqlite_master where type='table'")
        }


def _count_rows(path: Path, table: str) -> int:
    """数一张表的行数（``table`` 是脚本里的字面量，不接受外部输入）。"""
    with contextlib.closing(sqlite3.connect(path)) as conn:
        return int(conn.execute(f"select count(*) from {table}").fetchone()[0])


def _pragma(path: Path, name: str) -> Any:
    """读一个库级 PRAGMA 的首列（``name`` 是脚本里的字面量）。"""
    with contextlib.closing(sqlite3.connect(path)) as conn:
        row = conn.execute(f"PRAGMA {name}").fetchone()
    return row[0] if row else None


def _fingerprint(path: Path) -> Optional[tuple[int, int]]:
    """文件指纹（大小 + mtime 纳秒）；不存在返回 ``None``，用于「真实库未被触碰」自检。"""
    if not path.exists():
        return None
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns)


# ----------------------------------------------------------------------
# 冒烟主体
# ----------------------------------------------------------------------


class DbSmoke:
    """一次冒烟运行的编排与断言（构造不 import 数据库栈，``run()`` 里才延迟 import）。"""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.tmp_dir: Optional[Path] = None
        self.db_path: Optional[Path] = None
        self.engine: Any = None
        self.session_factory: Any = None

        #: 硬约束自检：仓库里的真实库在本次冒烟前后必须完全不变。
        self.real_db_path = REPO_ROOT / "resource" / DB_FILENAME
        self.real_db_before = _fingerprint(self.real_db_path)

        self.checks: list[tuple[str, str]] = []
        self.failures: list[str] = []

    # ---- 输出 ----

    @staticmethod
    def step(message: str) -> None:
        print(f"[db_smoke] {message}", flush=True)

    def fail(self, message: str) -> None:
        self.failures.append(message)
        print(f"[db_smoke][FAIL] {message}", flush=True)

    def ok(self, name: str, detail: str) -> None:
        self.checks.append((name, detail))
        self.step(f"[ok] {name}：{detail}")

    @staticmethod
    def require(condition: bool, message: str) -> None:
        """断言失败即抛 :class:`SmokeFailure`（当前步骤中止，记录为一项失败）。"""
        if not condition:
            raise SmokeFailure(message)

    # ---- 准备：临时目录与「把 session 指过去」 ----

    def _prepare_tempdir(self) -> None:
        """建临时目录、切到仓库根（``alembic.ini`` 相对 CWD 解析）。"""
        if Path.cwd() != REPO_ROOT:
            os.chdir(REPO_ROOT)
            self.step(f"已切换工作目录到仓库根：{REPO_ROOT}（alembic.ini 按仓库根相对解析）")
        self.tmp_dir = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX)).resolve()
        self.db_path = self.tmp_dir / DB_FILENAME
        self.step(f"临时目录：{self.tmp_dir}")
        self.step(
            f"全新 SQLite 库：{self.db_path}"
            f"（仓库的 resource/{DB_FILENAME} 不参与本次冒烟）"
        )

    def _bind_session(self) -> None:
        """把 ``maa_api.db.session`` 的路径、引擎、会话工厂全部指到临时库。

        ``db/migrate.py`` 与 ``retention_service`` 都在**调用时**读取这些模块属性
        （M2-05/M2-06 的可测性契约），所以在这里赋值即可整体隔离；模块级
        :data:`session.engine` 也一并替换，避免任何留存的引用写到真实库。
        """
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from maa_api.db import session as db_session
        from maa_api.db.session import make_engine

        assert self.db_path is not None and self.tmp_dir is not None
        self.require(
            self.db_path.is_relative_to(self.tmp_dir) and self.tmp_dir in self.db_path.parents,
            f"临时库路径越界：{self.db_path} 不在 {self.tmp_dir} 之下",
        )

        db_session.DB_PATH = self.db_path
        db_session.SYNC_URL = f"sqlite:///{self.db_path}"
        db_session.ASYNC_URL = f"sqlite+aiosqlite:///{self.db_path}"
        self.engine = make_engine(db_session.ASYNC_URL)
        db_session.engine = self.engine
        self.session_factory = async_sessionmaker(
            self.engine, expire_on_commit=False, class_=AsyncSession
        )
        db_session.session_factory = self.session_factory

        # 自证：迁移与清理都从模块属性读路径，读到的必须是临时库。
        self.require(
            Path(db_session.DB_PATH) == self.db_path
            and db_session.SYNC_URL.endswith(str(self.db_path))
            and db_session.ASYNC_URL.endswith(str(self.db_path)),
            "把 session 指到临时库之后，模块属性读回来不是临时库",
        )
        self.step(f"maa_api.db.session 已指向临时库（DB_PATH/SYNC_URL/ASYNC_URL/engine/session_factory）")

    # ---- 收尾 ----

    async def _teardown(self) -> None:
        """dispose 引擎 + 自检真实库未被触碰。"""
        if self.engine is not None:
            with contextlib.suppress(Exception):
                await self.engine.dispose()
            self.engine = None

        after = _fingerprint(self.real_db_path)
        if after == self.real_db_before:
            state = "不存在" if after is None else "指纹未变"
            self.ok(f"真实库未被触碰", f"resource/{DB_FILENAME} {state}（本次只写临时目录）")
        else:
            self.fail(
                f"真实库被改动：resource/{DB_FILENAME} 指纹 {self.real_db_before!r} → {after!r}；"
                "冒烟脚本必须只写临时目录"
            )

    def _cleanup_tempdir(self) -> None:
        """退出时清理临时目录（``--keep-temp`` 明确要求保留时不删）。"""
        if self.tmp_dir is None:
            return
        if self.args.keep_temp:
            self.step(f"--keep-temp：保留临时目录 {self.tmp_dir}")
            return
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        self.step(f"已清理临时目录：{self.tmp_dir}")
        self.tmp_dir = None

    # ------------------------------------------------------------------
    # A. 空库 → 自动迁移 head
    # ------------------------------------------------------------------

    async def _stage_migration_from_empty(self) -> str:
        from sqlmodel import SQLModel

        import maa_api.db.models  # noqa: F401  必须 import 才注册 13 张表
        from maa_api.db import migrate

        assert self.db_path is not None and self.tmp_dir is not None
        self.require(not self.db_path.exists(), f"库文件不该预先存在：{self.db_path}")
        self.step("空库 → ensure_schema()：走 db/migrate.py 的 Alembic 自动迁移（不是 create_all）")
        await migrate.ensure_schema()

        self.require(self.db_path.exists(), "ensure_schema() 之后库文件仍不存在")
        head = _head_revision()
        version = _db_version(self.db_path)
        self.require(version == head, f"迁移后版本应为 head={head}，实际 {version!r}")

        expected = set(SQLModel.metadata.tables)
        names = _table_names(self.db_path)
        missing = sorted(expected - names)
        self.require(not missing, f"迁移后缺少业务表：{missing}")

        auto_vacuum = _pragma(self.db_path, "auto_vacuum")
        self.require(
            auto_vacuum == AUTO_VACUUM_INCREMENTAL,
            f"auto_vacuum 应为 {AUTO_VACUUM_INCREMENTAL}(INCREMENTAL，env.py 的放置)，"
            f"实际 {auto_vacuum!r}",
        )
        self.require(
            not (self.tmp_dir / "backup").exists(),
            "首次建库不该产生迁移前备份（没有可备份的内容）",
        )
        return (
            f"{len(expected)} 张业务表 + alembic_version={head}，"
            f"auto_vacuum=INCREMENTAL，首次建库无备份"
        )

    # ------------------------------------------------------------------
    # B. upgrade → downgrade -1 → upgrade 往返
    # ------------------------------------------------------------------

    async def _stage_migration_roundtrip(self) -> str:
        from alembic import command

        from maa_api.db import migrate

        assert self.db_path is not None and self.tmp_dir is not None
        head = _head_revision()
        cfg = migrate._alembic_config()  # 指向临时库（调用时读 session 模块属性）
        self.step(f"往返开始：head={head} → downgrade -1 → ensure_schema() 自动 upgrade")
        rows_before = _count_rows(self.db_path, "schedule")

        command.downgrade(cfg, "-1")
        downgraded = _db_version(self.db_path)
        self.require(
            downgraded is not None and downgraded != head,
            f"downgrade -1 之后版本应回退（head={head}），实际 {downgraded!r}",
        )
        self.require(
            _count_rows(self.db_path, "schedule") == 0,
            "0002 的 downgrade 应清掉它插入的 schedule 行",
        )
        self.step(f"downgrade -1 → {downgraded}；自动迁移机制再 upgrade head（应触发迁移前备份）")

        await migrate.ensure_schema()
        self.require(
            _db_version(self.db_path) == head,
            f"自动迁移后版本应为 head={head}，实际 {_db_version(self.db_path)!r}",
        )
        self.require(
            _count_rows(self.db_path, "schedule") == rows_before,
            f"重放 0002 后 schedule 行数应回到 {rows_before}，"
            f"实际 {_count_rows(self.db_path, 'schedule')}",
        )

        backups = sorted((self.tmp_dir / "backup").glob("*.bak"))
        self.require(len(backups) == 1, f"有待应用迁移时应恰好产生 1 份备份，实际 {len(backups)}")
        self.require(
            _db_version(backups[0]) == downgraded,
            f"备份应是迁移前的快照（版本 {downgraded}），实际 {_db_version(backups[0])!r}",
        )
        return (
            f"head={head} → {downgraded} → head 往返成功；"
            f"backup={backups[0].name}（版本 {downgraded}）"
        )

    # ------------------------------------------------------------------
    # C. 各仓储族读写往返
    # ------------------------------------------------------------------

    async def _stage_families(self) -> str:
        families: list[tuple[str, Callable[[], Coroutine[Any, Any, str]]]] = [
            ("pipeline/task", self._family_pipeline),
            ("log/screenshot", self._family_log),
            ("schedule", self._family_schedule),
            ("setting", self._family_setting),
            ("agent/audit/confirmation", self._family_agent_audit),
            ("update/notify", self._family_update_notify),
            ("resource", self._family_resource),
        ]
        passed = 0
        for name, check in families:
            self.step(f"--- 仓储族：{name}")
            try:
                detail = await check()
            except SmokeFailure as exc:
                self.fail(f"仓储族 {name}：{exc}")
            except Exception as exc:  # noqa: BLE001 - 一族失败不影响其余族继续取证
                self.fail(f"仓储族 {name}：未预期异常 {type(exc).__name__}: {exc}")
                traceback.print_exc()
            else:
                self.ok(f"仓储族 {name}", detail)
                passed += 1
        self.require(passed == len(families), f"{len(families) - passed}/{len(families)} 个仓储族失败")
        return f"{passed} 族写→读→改→删往返全部一致"

    async def _family_pipeline(self) -> str:
        """``pipeline`` + ``task``：入队 → 领取 → 状态机 → 重试计数 → ``purge_before``。"""
        from maa_api.db.models import Pipeline, Task, utcnow
        from maa_api.db.repositories.pipeline import PipelineRepository, TaskRepository
        from maa_api.domain.enums import PipelineSource, PipelineStatus, Priority, TaskStatus

        async with self.session_factory() as db:
            pipelines = PipelineRepository(db)
            tasks = TaskRepository(db)
            pipeline = Pipeline(
                source=PipelineSource.MANUAL,
                priority=int(Priority.MANUAL),
                task_count=2,
                title="smoke-pipeline",
                idempotency_key=f"smoke-{uuid4().hex}",
            )
            await pipelines.create(
                pipeline,
                [
                    Task(
                        pipeline_id=pipeline.id,
                        order_index=0,
                        type_name="StartUp",
                        task_name="开始唤醒",
                        params={"client_type": "Official"},
                    ),
                    Task(
                        pipeline_id=pipeline.id,
                        order_index=1,
                        type_name="Fight",
                        task_name="刷理智",
                        params={"stage": "1-7"},
                    ),
                ],
            )
            await db.commit()
            pid = pipeline.id  # merge() 契约：入参保持 transient，id 提交后仍可读

            stored = await pipelines.get(pid, with_tasks=True)
            self.require(stored is not None, "get(with_tasks=True) 返回 None")
            self.require(stored.title == "smoke-pipeline", f"title 回读不一致：{stored.title!r}")
            self.require(
                [item.type_name for item in stored.tasks] == ["StartUp", "Fight"],
                f"task 列表回读不一致：{[item.type_name for item in stored.tasks]!r}",
            )
            page = await pipelines.list(status=PipelineStatus.PENDING, page=1, size=10)
            self.require(
                any(item.id == pid for item in page.items),
                "list(status=pending) 未返回刚写入的流水线",
            )
            self.require(await pipelines.count_pending() == 1, "count_pending 应为 1")

            claimed = await pipelines.claim_next()
            self.require(
                claimed is not None and claimed.id == pid,
                "claim_next 未领取到刚入队的流水线",
            )
            self.require(
                claimed.status == PipelineStatus.RUNNING and claimed.started_at is not None,
                f"领取后应是 running 且有 started_at，实际 {claimed.status!r}",
            )
            self.require(await pipelines.count_pending() == 0, "领取后待执行数应为 0")

            rows = await tasks.list_by_pipeline(pid)
            self.require(len(rows) == 2, f"task 行数应为 2，实际 {len(rows)}")
            first = rows[0]
            self.require(await tasks.increment_retry(first.id) == 1, "increment_retry 应返回 1")
            await tasks.update_status(first.id, TaskStatus.COMPLETED)
            await tasks.bind_maa_task_id(first.id, 4242)
            await db.commit()
            refreshed = await tasks.list_by_pipeline(pid)
            self.require(
                refreshed[0].status == TaskStatus.COMPLETED
                and refreshed[0].maa_task_id == 4242
                and refreshed[0].retry_count == 1,
                f"task 改后回读不一致：status={refreshed[0].status!r} "
                f"maa_task_id={refreshed[0].maa_task_id!r} "
                f"retry_count={refreshed[0].retry_count!r}",
            )

            self.require(
                await pipelines.mark_terminal(pid, PipelineStatus.COMPLETED) is True,
                "mark_terminal(completed) 应被状态机接受",
            )
            self.require(
                await pipelines.mark_terminal(pid, PipelineStatus.FAILED) is False,
                "终态不可再变：第二次 mark_terminal 必须返回 False",
            )
            self.require(
                await pipelines.set_priority(pid, Priority.AGENT) is False,
                "非 PENDING 的流水线不该接受改优先级",
            )
            await db.commit()
            terminal = await pipelines.get(pid)
            self.require(
                terminal.status == PipelineStatus.COMPLETED and terminal.finished_at is not None,
                "终态回读不一致",
            )

            deleted = await pipelines.purge_before(utcnow() + timedelta(days=1), keep_latest=0)
            await db.commit()
            self.require(deleted >= 1, f"purge_before 应至少删 1 条，实际 {deleted}")
            self.require(await pipelines.get(pid) is None, "purge_before 之后流水线仍在")
            self.require(
                await tasks.list_by_pipeline(pid) == [],
                "purge_before 应同时删掉 task（不依赖外键 PRAGMA 是否开启）",
            )
        return "create/get/list/claim_next/状态机/重试计数/purge_before 全部一致"

    async def _family_log(self) -> str:
        """``log_entry`` + ``screenshot``：批量落盘 → 游标查询 → 两维清理；截图标记与清理。"""
        from maa_api.db.models import LogEntry, Pipeline, Screenshot, utcnow
        from maa_api.db.repositories.log import LogRepository, ScreenshotRepository
        from maa_api.db.repositories.pipeline import PipelineRepository
        from maa_api.domain.enums import (
            LogLevel,
            LogSource,
            PipelineSource,
            Priority,
            ScreenshotBackend,
            ScreenshotTrigger,
        )

        async with self.session_factory() as db:
            logs = LogRepository(db)
            now = utcnow()
            entries = [
                LogEntry(
                    source=LogSource.MAA_TASK,
                    level=LogLevel.INFO,
                    content=f"smoke-log-{index}",
                    created_at=now,
                )
                for index in range(3)
            ]
            inserted = await logs.bulk_insert(entries)  # 自成事务并提交
            self.require(inserted == 3, f"bulk_insert 应写入 3 条，实际 {inserted}")
            ids = sorted(int(entry.id) for entry in entries)
            self.require(len(set(ids)) == 3, f"自增 id 应互不相同：{ids!r}")

            page = await logs.query(sources=[LogSource.MAA_TASK], levels=[LogLevel.INFO], size=10)
            self.require(
                page.total == 3 and len(page.items) == 3,
                f"query 应返回 3 条，实际 total={page.total} items={len(page.items)}",
            )
            self.require(
                [item.id for item in page.items] == sorted(ids, reverse=True),
                "query 应按 id DESC 返回",
            )
            cursor = await logs.query(sources=[LogSource.MAA_TASK], after_id=ids[0], size=10)
            self.require(
                cursor.total == 2 and all(item.id > ids[0] for item in cursor.items),
                f"after_id 应是严格大于（游标语义），实际 total={cursor.total}",
            )
            empty = await logs.query(sources=[], size=10)
            self.require(empty.total == 0, "sources=[]（空集合）应返回空页而不是全量")

            purged = await logs.purge(
                LogSource.MAA_TASK, before=utcnow() + timedelta(days=1), keep_max=0
            )
            self.require(purged >= 3, f"purge 应至少删 3 条，实际 {purged}")
            self.require(
                (await logs.query(sources=[LogSource.MAA_TASK])).total == 0,
                "purge 之后该来源应为空",
            )

            # 截图：外键指向真实流水线（临时库开了 PRAGMA foreign_keys=ON，M2-10 实测）
            pipeline = Pipeline(
                source=PipelineSource.MANUAL,
                priority=int(Priority.MANUAL),
                task_count=0,
                title="smoke-screenshot-owner",
            )
            await PipelineRepository(db).create(pipeline, [])
            await db.commit()
            screenshots = ScreenshotRepository(db)
            stored_shot = await screenshots.create(
                Screenshot(
                    pipeline_id=pipeline.id,
                    trigger=ScreenshotTrigger.MANUAL,
                    backend=ScreenshotBackend.CORE,
                    path="image/screenshot/smoke-shot.jpg",
                    format="jpg",
                    width=1280,
                    height=720,
                    size_bytes=4321,
                    created_at=now,
                )
            )
            await db.commit()
            shot_id = stored_shot.id
            got_shot = await screenshots.get(shot_id)
            self.require(
                got_shot is not None
                and got_shot.size_bytes == 4321
                and got_shot.format == "jpg"
                and got_shot.deleted_at is None,
                "截图记录回读不一致",
            )
            listed = await screenshots.list_by_pipeline(pipeline.id)
            self.require([item.id for item in listed] == [shot_id], "list_by_pipeline 未返回该截图")
            self.require(
                await screenshots.total_size_active() >= 4321,
                "total_size_active 应把未清理的截图计入",
            )

            self.require(
                await screenshots.mark_deleted([shot_id]) == 1,
                "mark_deleted 首次应返回 1",
            )
            await db.commit()
            got_shot = await screenshots.get(shot_id)
            self.require(got_shot.deleted_at is not None, "mark_deleted 之后 deleted_at 应为非空")
            self.require(
                await screenshots.mark_deleted([shot_id]) == 0,
                "重复 mark_deleted 应返回 0（不刷新时间戳）",
            )
            self.require(
                await screenshots.list_older_than(utcnow() + timedelta(days=1)) == [],
                "已打 deleted_at 的记录不该再被 list_older_than 返回",
            )
            self.require(
                await screenshots.purge_before(utcnow() + timedelta(days=1)) >= 1,
                "purge_before 应删掉该截图记录",
            )
            await db.commit()
            self.require(await screenshots.get(shot_id) is None, "purge_before 之后记录仍在")
        return "bulk_insert/query(after_id 严格大于)/purge + 截图 create/mark_deleted/purge_before 一致"

    async def _family_schedule(self) -> str:
        """``schedule``：模板存取 → 启用集合 → skip_if_running 判据 → 回写 → 删除解绑。"""
        from maa_api.db.models import Pipeline, Schedule, utcnow
        from maa_api.db.repositories.pipeline import PipelineRepository
        from maa_api.db.repositories.schedule import ScheduleRepository
        from maa_api.domain.enums import PipelineSource, PipelineStatus, Priority

        template = [{"type": "StartUp", "params": {"client_type": "Official"}}]
        async with self.session_factory() as db:
            schedules = ScheduleRepository(db)
            pipelines = PipelineRepository(db)
            name = f"smoke-schedule-{uuid4().hex[:8]}"
            stored = await schedules.create(
                Schedule(name=name, cron="0 7 * * *", template=template)
            )
            await db.commit()
            schedule_id = stored.id

            got = await schedules.get(schedule_id)
            self.require(got is not None, "get 返回 None")
            self.require(
                got.cron == "0 7 * * *" and got.timezone == "Asia/Shanghai" and got.enabled is True,
                f"回读不一致：cron={got.cron!r} timezone={got.timezone!r} enabled={got.enabled!r}",
            )
            self.require(got.template == template, f"template 应原样存取，实际 {got.template!r}")
            self.require(
                name in [item.name for item in await schedules.list_enabled()],
                "list_enabled 未包含刚创建的定时任务",
            )

            pipeline = await pipelines.create(
                Pipeline(
                    source=PipelineSource.SCHEDULED,
                    priority=int(Priority.SCHEDULED),
                    task_count=0,
                    title="smoke-schedule-instance",
                    schedule_id=schedule_id,
                ),
                [],
            )
            await db.commit()
            pipeline_id = pipeline.id
            self.require(
                await schedules.has_unfinished(schedule_id) is True,
                "本 schedule 有 pending/running 实例时 has_unfinished 应为 True",
            )
            self.require(
                await pipelines.mark_terminal(pipeline_id, PipelineStatus.CANCELLED) is True,
                "mark_terminal(cancelled) 应被接受",
            )
            await db.commit()
            self.require(
                await schedules.has_unfinished(schedule_id) is False,
                "实例终态后 has_unfinished 应为 False",
            )

            last_run = utcnow()
            self.require(
                await schedules.record_run(
                    schedule_id,
                    last_run_at=last_run,
                    next_run_at=last_run + timedelta(days=1),
                    pipeline_id=pipeline_id,
                    result=PipelineStatus.CANCELLED,
                )
                is True,
                "record_run 应返回 True",
            )
            self.require(
                await schedules.set_enabled(schedule_id, False) is True,
                "set_enabled(False) 应返回 True",
            )
            await db.commit()
            got = await schedules.get(schedule_id)
            self.require(
                got.enabled is False
                and got.last_result == PipelineStatus.CANCELLED
                and got.last_pipeline_id == pipeline_id
                and got.next_run_at is not None,
                "record_run/set_enabled 之后回读不一致",
            )
            self.require(
                name not in [item.name for item in await schedules.list_enabled()],
                "停用后不该出现在 list_enabled",
            )

            self.require(await schedules.delete(schedule_id) is True, "delete 应返回 True")
            await db.commit()
            self.require(await schedules.get(schedule_id) is None, "delete 之后记录仍在")
            detached = await pipelines.get(pipeline_id)
            self.require(
                detached is not None and detached.schedule_id is None,
                "删除 schedule 应把关联 pipeline.schedule_id 置空（不依赖外键 PRAGMA）",
            )
        return "create/get/list_enabled/has_unfinished/record_run/delete 全部一致"

    async def _family_setting(self) -> str:
        """``setting``：upsert 同一 key 只有一行 → JSON 保型回读 → 删除回到「无覆盖」。"""
        from maa_api.db.repositories.setting import SettingRepository

        async with self.session_factory() as db:
            settings = SettingRepository(db)
            self.require(await settings.get("smoke.key") is None, "全新库里不该有 smoke.key")

            await settings.set("smoke.key", 25, updated_by="manual")
            await settings.set("smoke.other", "25", updated_by="system")
            await db.commit()
            self.require(
                await settings.get("smoke.key") == 25,
                f"int 应原样读回，实际 {await settings.get('smoke.key')!r}",
            )
            self.require(
                await settings.get("smoke.other") == "25",
                f"str 应原样读回（JSON 保型），实际 {await settings.get('smoke.other')!r}",
            )

            await settings.set("smoke.key", {"nested": [1, 2]}, updated_by="agent")
            await db.commit()
            self.require(
                await settings.get("smoke.key") == {"nested": [1, 2]},
                "upsert 同一 key 应更新而不是新增一行",
            )
            all_values = await settings.all()
            self.require(
                all_values.get("smoke.key") == {"nested": [1, 2]}
                and all_values.get("smoke.other") == "25",
                f"all() 回读不一致：{all_values!r}",
            )

            try:
                await settings.set("smoke.none", None)
            except ValueError:
                pass
            else:
                raise SmokeFailure("set(key, None) 应抛 ValueError（JSON null 与「无覆盖」不可区分）")

            self.require(await settings.delete("smoke.key") is True, "delete 已存在的 key 应返回 True")
            self.require(await settings.delete("smoke.key") is False, "重复 delete 应返回 False（幂等）")
            await db.commit()
            self.require(await settings.get("smoke.key") is None, "delete 之后应回到无覆盖")
        return "upsert/get/all/delete（含 int 与 str 的 JSON 保型、None 拒绝）一致"

    async def _family_agent_audit(self) -> str:
        """``confirmation`` + ``agent_session`` + ``agent_message`` + ``agent_audit``。

        本族按设计**没有物理删除接口**（审计长期留存、会话删除归 M11），「删」这一步
        以终态流转（resolve / expire_overdue / clear_grant）表达。
        """
        from maa_api.db.models import AgentAudit, AgentMessage, AgentSession, Confirmation, utcnow
        from maa_api.db.repositories.agent import (
            AgentMessageRepository,
            AgentSessionRepository,
        )
        from maa_api.db.repositories.audit import AuditRepository, ConfirmationRepository
        from maa_api.domain.enums import (
            AgentRole,
            AuditStatus,
            CallerType,
            ConfirmationStatus,
            RiskLevel,
        )

        async with self.session_factory() as db:
            confirmations = ConfirmationRepository(db)
            stored_confirmation = await confirmations.create(
                Confirmation(
                    action="screenshot",
                    risk_level=RiskLevel.NONE,
                    reason="smoke 确认请求",
                    payload={"save_to": "smoke.jpg"},
                    requested_by="rest",
                    expires_at=utcnow() + timedelta(minutes=10),
                )
            )
            overdue = await confirmations.create(
                Confirmation(
                    action="stop_task",
                    risk_level=RiskLevel.DESTRUCTIVE,
                    reason="smoke 超时确认",
                    payload={"task_id": "t-1"},
                    requested_by="agent",
                    expires_at=utcnow() - timedelta(minutes=1),
                )
            )
            await db.commit()
            confirmation_id, overdue_id = stored_confirmation.id, overdue.id

            pending_ids = [item.id for item in await confirmations.list_pending()]
            self.require(
                confirmation_id in pending_ids and overdue_id in pending_ids,
                f"list_pending 应同时含两条待确认：{pending_ids!r}",
            )
            self.require(
                await confirmations.resolve(
                    confirmation_id, ConfirmationStatus.APPROVED, resolved_by="user", reason="ok"
                )
                is True,
                "resolve(approved) 应被状态机接受",
            )
            self.require(
                await confirmations.resolve(
                    confirmation_id, ConfirmationStatus.REJECTED, resolved_by="user"
                )
                is False,
                "终态不可再变：重复 resolve 必须返回 False",
            )
            expired = await confirmations.expire_overdue(utcnow())
            await db.commit()
            self.require(expired == [overdue_id], f"expire_overdue 应只翻转超时那条，实际 {expired!r}")
            self.require(
                await confirmations.expire_overdue(utcnow()) == [],
                "第二次 expire_overdue 应返回空列表（幂等）",
            )
            got_confirmation = await confirmations.get(confirmation_id)
            self.require(
                got_confirmation.status == ConfirmationStatus.APPROVED
                and got_confirmation.resolved_by == "user"
                and got_confirmation.resolved_at is not None,
                "resolve 之后回读不一致",
            )

            sessions = AgentSessionRepository(db)
            stored_session = await sessions.create(AgentSession(title="smoke 会话", model="gpt-4o-mini"))
            await db.commit()
            session_id = stored_session.id
            self.require(
                await sessions.update_grant(
                    session_id,
                    confirmation_id=confirmation_id,
                    expires_at=utcnow() + timedelta(minutes=5),
                )
                is True,
                "update_grant 应返回 True（confirmation_id 必须指向真实确认，FK 已开）",
            )
            await db.commit()
            granted = await sessions.get(session_id)
            self.require(
                granted.atomic_grant_id == confirmation_id
                and granted.atomic_grant_expires_at is not None,
                "授权字段回读不一致",
            )
            self.require(await sessions.clear_grant(session_id) is True, "clear_grant 应返回 True")
            await db.commit()
            cleared = await sessions.get(session_id)
            self.require(
                cleared.atomic_grant_id is None and cleared.atomic_grant_expires_at is None,
                "clear_grant 应把两个字段一起置空",
            )
            self.require(
                session_id in [item.id for item in await sessions.list_recent(limit=20)],
                "list_recent 未包含刚创建的会话",
            )

            messages = AgentMessageRepository(db)
            await messages.create(
                AgentMessage(session_id=session_id, seq=0, role=AgentRole.USER, content="你好")
            )
            await messages.create(
                AgentMessage(
                    session_id=session_id,
                    seq=1,
                    role=AgentRole.ASSISTANT,
                    content=None,
                    tool_calls=[{"id": "call-1", "type": "function"}],
                )
            )
            await db.commit()
            rows = await messages.list_by_session(session_id)
            tool_calls = [{"id": "call-1", "type": "function"}]
            self.require(
                [item.seq for item in rows] == [0, 1] and rows[1].tool_calls == tool_calls,
                f"agent_message 按 seq 回读不一致：{[(item.seq, item.content) for item in rows]!r}",
            )

            audits = AuditRepository(db)
            long_argument = "A" * 5000
            audit = AgentAudit(
                caller=CallerType.REST,
                tool_name="screencap",
                arguments={"image": long_argument, "index": 1},
                result_summary="R" * 3000,
                status=AuditStatus.SUCCESS,
                session_id=session_id,
            )
            stored_audit = await audits.create(audit)
            await db.commit()
            self.require(
                audit.arguments["image"] == long_argument,
                "AuditRepository.create 不该修改入参（裁剪只作用于落库副本）",
            )
            got_audit = await audits.get(stored_audit.id)
            self.require(
                got_audit.arguments["image"] == {"__truncated__": True, "len": 5000}
                and got_audit.arguments["index"] == 1,
                f"arguments 入库前裁剪不一致：{got_audit.arguments!r}",
            )
            self.require(
                len(got_audit.result_summary) == 2048,
                f"result_summary 应截断到 2048，实际 {len(got_audit.result_summary)}",
            )
            audit_page = await audits.list(caller=CallerType.REST, tool_name="screencap")
            self.require(
                audit_page.total >= 1 and audit_page.items[0].tool_name == "screencap",
                "audit list 过滤未命中刚写入的记录",
            )
        return (
            "confirmation 终态流转/超时扫描 + agent_session 授权与撤销 + "
            "agent_message 按 seq + agent_audit 裁剪（本族无删除接口，按设计只追加）一致"
        )

    async def _family_update_notify(self) -> str:
        """``update_record`` + ``notify_channel``：并发互斥 → 进度 → 终态；通道配置增删改查。"""
        from sqlalchemy.exc import IntegrityError

        from maa_api.db.models import NotifyChannel, UpdateRecord, utcnow
        from maa_api.db.repositories.notify import NotifyChannelRepository
        from maa_api.db.repositories.update import UpdateRepository
        from maa_api.domain.enums import (
            NotifyChannelType,
            NotifyEvent,
            UpdatePhase,
            UpdateStatus,
            UpdateTarget,
        )

        async with self.session_factory() as db:
            updates = UpdateRepository(db)
            stored = await updates.create(
                UpdateRecord(
                    target=UpdateTarget.CORE,
                    channel="stable",
                    from_version="1.0.0",
                    to_version="1.1.0",
                    status=UpdateStatus.RUNNING,
                    triggered_by="manual",
                )
            )
            await db.commit()
            update_id = stored.id
            current = await updates.current(UpdateTarget.CORE)
            self.require(
                current is not None and current.id == update_id and current.started_at is not None,
                "current 未返回刚创建的 running 记录（started_at 应自动补）",
            )

            duplicate_rejected = False
            try:
                await updates.create(
                    UpdateRecord(
                        target=UpdateTarget.CORE,
                        status=UpdateStatus.RUNNING,
                        triggered_by="rest",
                    )
                )
            except IntegrityError:
                duplicate_rejected = True
                await db.rollback()
            self.require(
                duplicate_rejected,
                "同 target 第二条 running 应撞部分唯一索引 uq_update_record_running_target",
            )

            self.require(
                await updates.update_progress(
                    update_id,
                    phase=UpdatePhase.DOWNLOADING,
                    progress=42,
                    bytes_total=1000,
                    bytes_done=420,
                )
                is True,
                "update_progress 应返回 True",
            )
            await db.commit()
            progressing = await updates.get(update_id)
            self.require(
                progressing.phase == UpdatePhase.DOWNLOADING
                and progressing.progress == 42
                and progressing.bytes_done == 420,
                "进度回读不一致",
            )
            self.require(
                await updates.mark_terminal(update_id, UpdateStatus.SUCCESS) is True,
                "mark_terminal(success) 应被接受",
            )
            self.require(
                await updates.mark_terminal(update_id, UpdateStatus.FAILED) is False,
                "终态不可再变：第二次 mark_terminal 必须返回 False",
            )
            self.require(
                await updates.update_progress(update_id, progress=99) is False,
                "终态记录不该再接受进度写入",
            )
            await db.commit()
            finished = await updates.get(update_id)
            self.require(
                finished.status == UpdateStatus.SUCCESS
                and finished.finished_at is not None
                and finished.progress == 42,
                "终态回读不一致",
            )
            history = await updates.list(target=UpdateTarget.CORE)
            self.require(
                history.total >= 1 and any(item.id == update_id for item in history.items),
                "更新历史未包含刚写入的记录",
            )

            channels = NotifyChannelRepository(db)
            name = f"smoke-{uuid4().hex[:8]}"
            stored_channel = await channels.create(
                NotifyChannel(
                    type=NotifyChannelType.WEBHOOK,
                    name=name,
                    config={"url": "https://example.invalid/hook"},
                    events=[NotifyEvent.PIPELINE_COMPLETED],
                )
            )
            await db.commit()
            channel_id = stored_channel.id
            got_channel = await channels.get(channel_id)
            self.require(
                got_channel.config == {"url": "https://example.invalid/hook"}
                and got_channel.events == ["pipeline_completed"],
                f"通道 JSON 回读不一致：{got_channel.config!r} {got_channel.events!r}",
            )
            self.require(await channels.set_enabled(channel_id, False) is True, "set_enabled 应返回 True")
            self.require(
                await channels.record_send(channel_id, status="failed", error="boom") is True,
                "record_send 应返回 True",
            )
            await db.commit()
            got_channel = await channels.get(channel_id)
            self.require(
                got_channel.enabled is False
                and got_channel.last_status == "failed"
                and got_channel.last_error == "boom"
                and got_channel.last_sent_at is not None,
                "启停 / 最近发送结果回读不一致",
            )
            disabled = await channels.list(type=NotifyChannelType.WEBHOOK, enabled=False)
            self.require(
                name in [item.name for item in disabled],
                "list(type, enabled) 过滤未命中",
            )
            self.require(await channels.delete(channel_id) is True, "delete 应返回 True")
            self.require(await channels.delete(channel_id) is False, "重复 delete 应返回 False（幂等）")
            await db.commit()
            self.require(await channels.get(channel_id) is None, "delete 之后通道仍在")
        return "update_record 部分唯一索引/进度/终态 + notify_channel 增删改查全部一致"

    async def _family_resource(self) -> str:
        """``resource_asset``：upsert 新行 → 只带检查字段的 upsert（M2-11 的 CHECK 陷阱）→ 删。"""
        from maa_api.db.models import utcnow
        from maa_api.db.repositories.resource import ResourceAssetRepository
        from maa_api.domain.enums import ResourceAssetKind

        async with self.session_factory() as db:
            resources = ResourceAssetRepository(db)
            name = f"smoke-{uuid4().hex[:8]}"
            created = await resources.upsert_by_kind_name(
                ResourceAssetKind.COPILOT,
                name,
                content={"tasks": []},
                description="smoke 资源",
            )
            await db.commit()
            self.require(
                created.content == {"tasks": []} and created.created_at is not None,
                "upsert 插入路径回读不一致",
            )
            asset_id, created_at = created.id, created.created_at

            updated = await resources.upsert_by_kind_name(
                ResourceAssetKind.COPILOT,
                name,
                content={"tasks": [{"type": "Fight"}]},
                checksum="deadbeef",
            )
            await db.commit()
            self.require(
                updated.id == asset_id
                and updated.content == {"tasks": [{"type": "Fight"}]}
                and updated.checksum == "deadbeef"
                and updated.created_at == created_at,
                "upsert 更新路径回读不一致（不该换行或丢字段）",
            )
            checked = await resources.upsert_by_kind_name(
                ResourceAssetKind.COPILOT, name, last_checked_at=utcnow()
            )
            await db.commit()
            self.require(
                checked.id == asset_id
                and checked.content == {"tasks": [{"type": "Fight"}]}
                and checked.last_checked_at is not None,
                "只带 last_checked_at 的 upsert 应更新真实行（M2-11：不能走 ON CONFLICT 候选行）",
            )
            self.require(
                name in [item.name for item in await resources.list_by_kind(ResourceAssetKind.COPILOT)],
                "list_by_kind 未包含刚写入的资源",
            )
            self.require(await resources.set_enabled(asset_id, False) is True, "set_enabled 应返回 True")
            await db.commit()
            got = await resources.get(asset_id)
            self.require(got.enabled is False, "停用后回读不一致")

            self.require(await resources.delete(asset_id) is True, "delete 应返回 True")
            self.require(await resources.delete(asset_id) is False, "重复 delete 应返回 False（幂等）")
            await db.commit()
            self.require(
                await resources.get_by_kind_name(ResourceAssetKind.COPILOT, name) is None,
                "delete 之后资源仍在",
            )
        return "upsert（插入/更新/只检查字段）/list_by_kind/set_enabled/delete 全部一致"

    # ------------------------------------------------------------------
    # D. 保留策略清理
    # ------------------------------------------------------------------

    async def _stage_retention(self) -> str:
        from maa_api.db.models import LogEntry, Pipeline, Screenshot, Task, utcnow
        from maa_api.db.repositories.log import LogRepository, ScreenshotRepository
        from maa_api.db.repositories.pipeline import PipelineRepository, TaskRepository
        from maa_api.domain.enums import (
            LogLevel,
            LogSource,
            PipelineSource,
            Priority,
            ScreenshotBackend,
            ScreenshotTrigger,
        )
        from maa_api.services.retention_service import (
            LOG_RETENTION,
            PIPELINE_RETENTION_DAYS,
            SCREENSHOT_RETENTION_DAYS,
            run_retention,
        )

        assert self.db_path is not None and self.tmp_dir is not None
        resource_root = self.db_path.parent
        now = utcnow()
        log_policy = LOG_RETENTION[LogSource.MAA_TASK]
        old_log_time = now - timedelta(days=log_policy.days + 10)
        old_pipeline_time = now - timedelta(days=PIPELINE_RETENTION_DAYS + 10)
        old_shot_time = now - timedelta(days=SCREENSHOT_RETENTION_DAYS + 3)

        async with self.session_factory() as db:
            logs = LogRepository(db)
            inserted = await logs.bulk_insert(
                [
                    LogEntry(
                        source=LogSource.MAA_TASK,
                        level=LogLevel.INFO,
                        content=f"smoke-retention-old-{index}",
                        created_at=old_log_time,
                    )
                    for index in range(4)
                ]
                + [
                    LogEntry(
                        source=LogSource.MAA_TASK,
                        level=LogLevel.INFO,
                        content=f"smoke-retention-new-{index}",
                        created_at=now,
                    )
                    for index in range(2)
                ]
            )
            self.require(inserted == 6, f"造数应写入 6 条日志，实际 {inserted}")

            pipelines = PipelineRepository(db)
            old_pipeline = Pipeline(
                source=PipelineSource.SCHEDULED,
                priority=int(Priority.SCHEDULED),
                task_count=1,
                title="smoke-retention-old",
                created_at=old_pipeline_time,
            )
            await pipelines.create(
                old_pipeline,
                [
                    Task(
                        pipeline_id=old_pipeline.id,
                        order_index=0,
                        type_name="StartUp",
                        task_name="开始唤醒",
                        params={"client_type": "Official"},
                    )
                ],
            )
            fresh_pipeline = await pipelines.create(
                Pipeline(
                    source=PipelineSource.SCHEDULED,
                    priority=int(Priority.SCHEDULED),
                    task_count=0,
                    title="smoke-retention-fresh",
                    created_at=now,
                ),
                [],
            )
            await db.commit()
            old_pipeline_id, fresh_pipeline_id = old_pipeline.id, fresh_pipeline.id

            screenshots = ScreenshotRepository(db)
            old_shot_rel = Path("image") / "screenshot" / "smoke-retention-old.jpg"
            fresh_shot_rel = Path("image") / "screenshot" / "smoke-retention-fresh.jpg"
            (resource_root / old_shot_rel).parent.mkdir(parents=True, exist_ok=True)
            (resource_root / old_shot_rel).write_bytes(b"old-shot")
            (resource_root / fresh_shot_rel).write_bytes(b"fresh-shot")
            old_shot = await screenshots.create(
                Screenshot(
                    pipeline_id=old_pipeline_id,
                    trigger=ScreenshotTrigger.MANUAL,
                    backend=ScreenshotBackend.CORE,
                    path=str(old_shot_rel),
                    format="jpg",
                    width=100,
                    height=100,
                    size_bytes=8,
                    created_at=old_shot_time,
                )
            )
            fresh_shot = await screenshots.create(
                Screenshot(
                    pipeline_id=fresh_pipeline_id,
                    trigger=ScreenshotTrigger.MANUAL,
                    backend=ScreenshotBackend.CORE,
                    path=str(fresh_shot_rel),
                    format="jpg",
                    width=100,
                    height=100,
                    size_bytes=10,
                    created_at=now,
                )
            )
            await db.commit()
            old_shot_id, fresh_shot_id = old_shot.id, fresh_shot.id

            # IPC 中转图：一个超 1 小时、一个刚写入（mtime 与 time.time() 比较）。
            temp_dir = resource_root / "temp" / "screencap"
            temp_dir.mkdir(parents=True, exist_ok=True)
            old_temp = temp_dir / "smoke-retention-old.jpg"
            fresh_temp = temp_dir / "smoke-retention-fresh.jpg"
            old_temp.write_bytes(b"old-temp")
            fresh_temp.write_bytes(b"fresh-temp")
            stale = time.time() - 2 * 3600
            os.utime(old_temp, (stale, stale))

        self.step("触发一轮保留策略清理：run_retention(session_factory=<临时库工厂>)")
        result = await run_retention(session_factory=self.session_factory)
        self.step(f"run_retention 返回：{result}")
        self.require(
            result["logs_deleted"] >= 4,
            f"超 {log_policy.days} 天的 4 条 maa_task 日志应被删，实际 logs_deleted={result['logs_deleted']}",
        )
        self.require(
            result["pipelines_deleted"] >= 1,
            f"超 {PIPELINE_RETENTION_DAYS} 天的流水线应被删，实际 pipelines_deleted={result['pipelines_deleted']}",
        )
        self.require(
            result["screenshots_deleted"] >= 1,
            f"超 {SCREENSHOT_RETENTION_DAYS} 天的截图文件应被清理，"
            f"实际 screenshots_deleted={result['screenshots_deleted']}",
        )
        self.require(
            result["temp_files_deleted"] == 1,
            f"只应删掉 1 个过期中转图，实际 temp_files_deleted={result['temp_files_deleted']}",
        )

        async with self.session_factory() as db:
            logs = LogRepository(db)
            page = await logs.query(sources=[LogSource.MAA_TASK], size=50)
            contents = sorted(item.content for item in page.items)
            self.require(
                page.total == 2
                and contents == ["smoke-retention-new-0", "smoke-retention-new-1"],
                f"应只留下 2 条新日志，实际 total={page.total} contents={contents!r}",
            )

            pipelines = PipelineRepository(db)
            self.require(await pipelines.get(old_pipeline_id) is None, "超期流水线应被清理")
            self.require(await pipelines.get(fresh_pipeline_id) is not None, "未超期流水线应保留")
            self.require(
                await TaskRepository(db).list_by_pipeline(old_pipeline_id) == [],
                "被清理流水线的 task 应一并删除",
            )

            screenshots = ScreenshotRepository(db)
            old_got = await screenshots.get(old_shot_id)
            fresh_got = await screenshots.get(fresh_shot_id)
            self.require(
                old_got is not None and old_got.deleted_at is not None,
                "超期截图应保留记录并打 deleted_at（docs/04 §10.3）",
            )
            self.require(
                not (resource_root / old_shot_rel).exists(),
                "超期截图文件应被删除",
            )
            self.require(
                fresh_got is not None and fresh_got.deleted_at is None,
                "未超期截图不该被标记 deleted_at",
            )
            self.require((resource_root / fresh_shot_rel).exists(), "未超期截图文件应保留")

        self.require(not old_temp.exists(), "超 1 小时的中转图应被删除")
        self.require(fresh_temp.exists(), "刚写入的中转图应保留")
        self.require(
            _pragma(self.db_path, "auto_vacuum") == AUTO_VACUUM_INCREMENTAL,
            "保留策略的增量 vacuum 需要 auto_vacuum=INCREMENTAL",
        )
        return (
            f"logs_deleted={result['logs_deleted']} pipelines_deleted={result['pipelines_deleted']} "
            f"screenshots_deleted={result['screenshots_deleted']} "
            f"temp_files_deleted={result['temp_files_deleted']}：该删的删了、该留的留了"
        )

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------

    def _print_checklist(self) -> None:
        total = len(self.checks) + len(self.failures)
        self.step(f"检查清单：{len(self.checks)}/{total} 项通过")
        for name, detail in self.checks:
            print(f"  [ok]   {name}：{detail}", flush=True)
        for item in self.failures:
            print(f"  [FAIL] {item}", flush=True)

    async def run(self) -> int:
        stages: list[tuple[str, Callable[[], Coroutine[Any, Any, str]], bool]] = [
            ("A 空库建库 + 自动迁移到 head", self._stage_migration_from_empty, True),
            ("B upgrade→downgrade→upgrade 往返", self._stage_migration_roundtrip, True),
            ("C 各仓储族读写往返", self._stage_families, False),
            ("D 保留策略清理", self._stage_retention, False),
        ]
        blocked_by: Optional[str] = None
        try:
            self._prepare_tempdir()
            self._bind_session()
            for label, check, fatal in stages:
                self.step(f"=== {label}")
                if blocked_by is not None:
                    self.fail(f"{label}：前置阶段「{blocked_by}」失败，未验证")
                    continue
                try:
                    detail = await check()
                except SmokeFailure as exc:
                    self.fail(f"{label}：{exc}")
                    if fatal:
                        blocked_by = label
                except Exception as exc:  # noqa: BLE001 - 冒烟脚本必须自己收尾并给出结论
                    self.fail(f"{label}：未预期异常 {type(exc).__name__}: {exc}")
                    traceback.print_exc()
                    if fatal:
                        blocked_by = label
                else:
                    self.ok(label, detail)
        except SmokeFailure as exc:
            self.fail(f"准备阶段：{exc}")
        except Exception as exc:  # noqa: BLE001 - 同上
            self.fail(f"准备阶段：未预期异常 {type(exc).__name__}: {exc}")
            traceback.print_exc()
        finally:
            with contextlib.suppress(Exception):
                await self._teardown()
            self._cleanup_tempdir()

        self._print_checklist()
        if self.failures:
            print(f"[db_smoke] 失败 {len(self.failures)} 项，退出码 1", flush=True)
            return 1
        self.step("全部检查通过")
        print("SMOKE OK", flush=True)
        return 0


def main(argv: Optional[list[str]] = None) -> int:
    """解析参数并跑一次冒烟；``--help`` 在此直接返回，不 import 数据库栈。"""
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(DbSmoke(args).run())
    except KeyboardInterrupt:
        print("[db_smoke] 收到中断（Ctrl-C），退出", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
