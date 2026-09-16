"""保留策略与后台清理（docs/04 §10、docs/12 M2）。

职责边界
--------
本模块只做**清理**：按 docs/04 §10.2/§10.3 的分级策略删日志、流水线、截图文件与
``<resource>/temp/screencap/`` 下的 IPC 中转图，每轮结束后做增量 vacuum。它**不**
负责日志采集，也不负责 ``maacore_debug`` 的落库级别过滤（配置项
``log.persist_maacore_debug_level``）—— 那是 ``LogHub`` 的职责（M4），本模块只对
已经落库的数据执行保留策略（docs/04 §10.2）。

接线方式（M3 的 lifespan）
--------------------------
APScheduler 每天 :data:`RETENTION_CRON`（04:00）触发一次，另在应用启动后
:data:`STARTUP_DELAY_SECONDS` 秒补跑一次，覆盖「长期停机后重启」的场景
（docs/04 §10.4）。``run_retention`` 是普通协程，由调用方决定 job 的注册方式：

.. code-block:: python

    scheduler.add_job(run_retention, CronTrigger.from_crontab(RETENTION_CRON), ...)
    scheduler.add_job(run_retention, "date", run_date=启动时刻 + 30 秒, ...)

每次运行自行通过 ``session_factory`` 开新会话（后台任务不共享会话，docs/04 §2），
异常**不吞**：清理失败要让 APScheduler 记下来，下一轮重试。

保留口径（数值都提到模块级常量，便于按运行数据回调）
----------------------------------------------------
- 日志（docs/04 §10.2）：``maa_task`` 30 天 / 300,000 条、``server`` 7 天 /
  200,000 条、``maacore_debug`` 3 天 / 50,000 条。天数与条数两个维度同时生效，
  取更严格的一方：先按天删，删完仍超上限就按 ``id`` 升序删到上限以内。
- 流水线（docs/04 §10.3）：90 天或最近 500 条，先触发者生效；``task`` 随外键
  级联，``log_entry`` / ``screenshot`` 的关联字段置 NULL 后各由自己的策略处理，
  不保留墓碑记录。
- 截图（docs/04 §10.3）：文件保留 7 天，并受总大小上限（默认 512 MB）约束；
  超限时按 ``created_at`` 升序删文件并给记录打 ``deleted_at``，记录本身跟着
  90 天的流水线历史清理（``ScreenshotRepository.purge_before``）。
- IPC 中转图：``<resource>/temp/screencap/`` 下 mtime 超过 1 小时的文件，直接删，
  不进 ``screenshot`` 表（docs/02 §3.3）。

分批与空间回收（docs/04 §10.4）
--------------------------------
删十万行的一条 DELETE 会长时间持有 SQLite 写锁，期间 ``LogHub`` 刷盘只能在
``busy_timeout`` 上排队。因此：日志走 ``LogRepository.purge``（内部每批
``LIMIT 5000``、每批自成事务），服务层再按 docs/04 §10.4 的 ``while True`` 循环
反复调用，非零轮之间 ``await asyncio.sleep(BATCH_PAUSE_SECONDS)`` 让出写锁；
截图文件也按 ``SCREENSHOT_FILE_BATCH_SIZE`` 分批。空间回收只做增量 vacuum
``PRAGMA incremental_vacuum(1000)``（依赖初始迁移设好的
``auto_vacuum=INCREMENTAL``，M2-01 实测落点见 ``db/migrations/env.py``）；完整
``VACUUM`` 需要重写整库并独占，只作为设置页的手动维护操作，不进自动任务。

文件路径约定
------------
``screenshot.path`` 是相对 ``resource/`` 的相对路径（docs/04 §5.4），而
``resource/`` 由 ``session.DB_PATH`` 的父目录推导（不硬编码 ``resource/``），
测试把 ``session.DB_PATH`` 指到 tmp 目录即可整库整目录隔离。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.db import session
from maa_api.db.models import Screenshot, utcnow
from maa_api.db.repositories.log import LogRepository, ScreenshotRepository
from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.domain.enums import LogSource

logger = logging.getLogger(__name__)

# M3 的 lifespan 按这两个常量注册 APScheduler job（docs/04 §10.4）。
RETENTION_CRON = "0 4 * * *"
STARTUP_DELAY_SECONDS = 30

# 批与批之间让出写锁的时长（docs/04 §10.4 的 0.05 s）。
BATCH_PAUSE_SECONDS = 0.05
# 增量 vacuum 每次归还的页数；不写完整 VACUUM（docs/04 §10.4）。
VACUUM_PAGES = 1000


@dataclass(frozen=True)
class LogRetention:
    """单一日志来源的两维保留口径（docs/04 §10.2）。"""

    days: int
    max_entries: int


# 分级保留表：两个维度同时生效，取更严格的一方。数值待运行数据校准（docs/13 §6），
# 校准前的准绳是 docs/04 §10.2，不是旧的整表口径（14 天 / 200 万条）。
LOG_RETENTION: dict[LogSource, LogRetention] = {
    LogSource.MAA_TASK: LogRetention(days=30, max_entries=300_000),
    LogSource.SERVER: LogRetention(days=7, max_entries=200_000),
    LogSource.MAACORE_DEBUG: LogRetention(days=3, max_entries=50_000),
}

PIPELINE_RETENTION_DAYS = 90
PIPELINE_KEEP_LATEST = 500
SCREENSHOT_RETENTION_DAYS = 7
SCREENSHOT_MAX_TOTAL_BYTES = 512 * 1024 * 1024
# 每批处理的截图条数（文件 IO 与 DB 更新都按批提交，避免长时间持锁）。
SCREENSHOT_FILE_BATCH_SIZE = 500
TEMP_SCREENCAP_RETENTION_SECONDS = 3600
# 相对 <resource>/ 的 IPC 中转图目录（docs/02 §3.3）；截图目录由
# screenshot.path 自带（docs/04 §5.4），不在这里拼。
TEMP_SCREENCAP_DIR = Path("temp") / "screencap"


async def run_retention(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, int]:
    """执行一轮完整清理，返回各阶段删除计数。

    ``session_factory`` 为 ``None`` 时取 ``maa_api.db.session`` 模块的
    :data:`session.session_factory`（运行时取模块属性，测试可注入临时库）。
    资源根目录同样在运行时从 ``session.DB_PATH.parent`` 推导。

    返回 dict 至少含四个 key（M3 的 lifespan 与测试都依赖这个形状）：
    ``logs_deleted`` / ``pipelines_deleted`` / ``screenshots_deleted`` /
    ``temp_files_deleted``，都是 int。其中 ``screenshots_deleted`` 计的是
    **文件被清理并打了 ``deleted_at``** 的截图条数，不含随后按 90 天口径删掉的
    记录条数（后者只进日志）。异常原样抛出，不在本层吞掉。
    """
    factory = session.session_factory if session_factory is None else session_factory
    resource_root = Path(session.DB_PATH).parent
    now = utcnow()
    result = {
        "logs_deleted": 0,
        "pipelines_deleted": 0,
        "screenshots_deleted": 0,
        "temp_files_deleted": 0,
    }

    async with factory() as db:
        # 1) 日志：每个来源各一轮，天数与条数上限两维取严（docs/04 §10.2）。
        log_repo = LogRepository(db)
        for source, policy in LOG_RETENTION.items():
            deleted = await _purge_log_source(
                log_repo,
                source,
                before=now - timedelta(days=policy.days),
                keep_max=policy.max_entries,
            )
            result["logs_deleted"] += deleted
            logger.info(
                "保留策略：%s 日志删除 %d 条（>%d 天或超 %d 条）",
                source.value,
                deleted,
                policy.days,
                policy.max_entries,
            )
        await _incremental_vacuum(db)

        # 2) 流水线：90 天或最近 500 条，先触发者生效；task 随外键级联，
        #    log_entry / screenshot 的关联字段置 NULL（docs/04 §10.3）。
        pipeline_repo = PipelineRepository(db)
        result["pipelines_deleted"] = await pipeline_repo.purge_before(
            cutoff=now - timedelta(days=PIPELINE_RETENTION_DAYS),
            keep_latest=PIPELINE_KEEP_LATEST,
        )
        await db.commit()
        logger.info("保留策略：流水线删除 %d 条", result["pipelines_deleted"])
        if result["pipelines_deleted"]:
            await asyncio.sleep(BATCH_PAUSE_SECONDS)
        await _incremental_vacuum(db)

        # 3) 截图：7 天 + 总大小上限，超限按 created_at 升序删文件并 mark_deleted；
        #    记录本身跟着 90 天的流水线历史清理（docs/04 §10.3）。
        screenshot_repo = ScreenshotRepository(db)
        result["screenshots_deleted"] = await _purge_screenshot_files(
            screenshot_repo, db, resource_root, now
        )
        purged_records = await screenshot_repo.purge_before(
            cutoff=now - timedelta(days=PIPELINE_RETENTION_DAYS)
        )
        await db.commit()
        logger.info(
            "保留策略：截图文件清理 %d 个，过期记录删除 %d 条",
            result["screenshots_deleted"],
            purged_records,
        )
        await _incremental_vacuum(db)

        # 4) IPC 中转图：不进 screenshot 表，按 mtime 直接删（docs/04 §10.3）。
        result["temp_files_deleted"] = _purge_temp_files(resource_root)
        logger.info(
            "保留策略：temp/screencap 中转图删除 %d 个", result["temp_files_deleted"]
        )
        await _incremental_vacuum(db)

    logger.info("保留策略完成：%s", result)
    return result


async def _purge_log_source(
    repo: LogRepository,
    source: LogSource,
    *,
    before: datetime,
    keep_max: int,
) -> int:
    """按 docs/04 §10.4 的循环清理单一来源，返回删除条数。

    ``repo.purge`` 内部每批 ``LIMIT 5000`` 且每批自成事务（提交即释放写锁），
    这里再按文档的 ``while True`` 形态循环到没有可删的行，非零轮之间
    ``await asyncio.sleep`` 让出写锁给 ``LogHub`` 刷盘。
    """
    deleted_total = 0
    while True:
        deleted = await repo.purge(source, before=before, keep_max=keep_max)
        if deleted == 0:
            break
        deleted_total += deleted
        await asyncio.sleep(BATCH_PAUSE_SECONDS)
    return deleted_total


async def _purge_screenshot_files(
    repo: ScreenshotRepository,
    db: AsyncSession,
    resource_root: Path,
    now: datetime,
) -> int:
    """删过期/超限的截图文件并给记录打 ``deleted_at``，返回处理的条数。

    两个维度依次执行：先按 7 天删（``created_at < now - 7d``），再在总大小超过
    512 MB 时按 ``created_at`` 升序继续删最旧的，直到降到上限以内。文件先删、
    记录后标记：中途崩溃时下一轮会把「文件已不在」的记录补上 ``deleted_at``，
    不会留下指向空文件的记录。两个维度都分批提交并让出写锁。
    """
    deleted = 0

    # 第一维：超过 7 天（docs/04 §10.3）。
    cutoff = now - timedelta(days=SCREENSHOT_RETENTION_DAYS)
    while True:
        records = await repo.list_older_than(cutoff)
        if not records:
            break
        batch = records[:SCREENSHOT_FILE_BATCH_SIZE]
        marked = _unlink_screenshot_files(batch, resource_root)
        if not marked:
            break  # 路径越界/权限失败，一条都没处理；再循环也是同一批，防死循环
        await repo.mark_deleted(marked)
        await db.commit()
        deleted += len(marked)
        await asyncio.sleep(BATCH_PAUSE_SECONDS)
        if len(batch) < SCREENSHOT_FILE_BATCH_SIZE:
            break

    # 第二维：总大小上限，按 created_at 升序删最旧的（docs/04 §10.3）。
    # list_older_than(now) 即「当前全部未清理记录，最旧优先」；now 是本次运行
    # 的起点，created_at 在它之后的记录（时钟回拨等异常）不参与本轮淘汰。
    while True:
        total = await repo.total_size_active()
        if total <= SCREENSHOT_MAX_TOTAL_BYTES:
            break
        records = await repo.list_older_than(now)
        if not records:
            break
        # 只取「刚好降到上限以内」所需的最旧几条，避免一次删掉整批 500 条。
        overflow = total - SCREENSHOT_MAX_TOTAL_BYTES
        batch: list[Screenshot] = []
        freed = 0
        for shot in records[:SCREENSHOT_FILE_BATCH_SIZE]:
            batch.append(shot)
            freed += shot.size_bytes
            if freed >= overflow:
                break
        marked = _unlink_screenshot_files(batch, resource_root)
        if not marked:
            break  # 同上：无可进展就停，避免死循环
        await repo.mark_deleted(marked)
        await db.commit()
        deleted += len(marked)
        await asyncio.sleep(BATCH_PAUSE_SECONDS)

    return deleted


def _unlink_screenshot_files(
    records: Sequence[Screenshot], resource_root: Path
) -> list[str]:
    """删除一批截图文件，返回**成功处理（文件已删或本就不存在）**的记录 id。

    只返回成功处理的 id：调用方据此 ``mark_deleted``，失败（越界路径、权限）
    的记录留到下一轮重试，不会出现「记录说文件已清理、文件却还在」的错位。
    文件本就不存在也算处理成功 —— 接口应返回 410「截图已过期」而不是让前端
    去撞一个 404。
    """
    marked: list[str] = []
    for shot in records:
        path = _resolve_under(resource_root, shot.path)
        if path is None:
            logger.warning("保留策略：截图路径越出 resource 根，跳过 %s", shot.path)
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("保留策略：删除截图文件失败 %s（%s）", path, exc)
            continue
        marked.append(shot.id)
    return marked


def _purge_temp_files(resource_root: Path) -> int:
    """删 ``<resource>/temp/screencap/`` 下 mtime 超过 1 小时的文件，返回个数。

    mtime 与 ``time.time()`` 比较：``utcnow()`` 是 naive UTC，直接
    ``.timestamp()`` 会被解释成本地时间而差出时区偏移。目录不存在（从未截过图）
    直接返回 0，不是错误。
    """
    directory = resource_root / TEMP_SCREENCAP_DIR
    if not directory.is_dir():
        return 0
    cutoff = time.time() - TEMP_SCREENCAP_RETENTION_SECONDS
    deleted = 0
    for entry in directory.iterdir():
        if not entry.is_file():
            continue
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
            entry.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning("保留策略：删除中转图失败 %s（%s）", entry, exc)
            continue
        deleted += 1
    return deleted


def _resolve_under(root: Path, relative: str) -> Path | None:
    """把库里的相对路径解析到 ``root`` 之下；越界（``..``/绝对路径/逃逸软链）返回 None。

    防御的是脏数据把清理任务变成「删库外文件」的工具：``path`` 来自
    ``screenshot`` 表，理论上都是 ``resource/`` 下的相对路径（docs/04 §5.4），
    但清理是不可逆操作，值得多一道边界检查。
    """
    root_resolved = root.resolve()
    candidate = (root_resolved / relative).resolve()
    if not candidate.is_relative_to(root_resolved):
        return None
    return candidate


async def _incremental_vacuum(db: AsyncSession) -> None:
    """归还已释放的页（docs/04 §10.4）；``auto_vacuum`` 未开启时是 no-op。

    必须在清理事务提交之后调用：不写完整 ``VACUUM``（重写整库且独占），只做
    ``PRAGMA incremental_vacuum(VACUUM_PAGES)``。
    """
    await db.execute(text(f"PRAGMA incremental_vacuum({VACUUM_PAGES})"))
    await db.commit()
