"""保留策略与后台清理（docs/04 §10）。

只做清理：按 docs/04 §10.2/§10.3 的分级策略删日志、流水线、截图文件与
``<resource>/temp/screencap/`` 下的 IPC 中转图，每轮结束后做增量 vacuum。不负责日志采集，
也不负责 ``maacore_debug`` 的落库级别过滤（配置项 ``log.persist_maacore_debug_level``），
那是 ``LogHub`` 的职责；这里只对已经落库的数据执行保留策略。

接线方式：APScheduler 每天 :data:`RETENTION_CRON`（04:00）触发一次，另在应用启动后
:data:`STARTUP_DELAY_SECONDS` 秒补跑一次，覆盖「长期停机后重启」的场景（docs/04 §10.4）。
:func:`run_retention` 是普通协程，由调用方决定 job 的注册方式：

.. code-block:: python

    scheduler.add_job(run_retention, CronTrigger.from_crontab(RETENTION_CRON), ...)
    scheduler.add_job(run_retention, "date", run_date=启动时刻 + 30 秒, ...)

每次运行自行通过 ``session_factory`` 开新会话（后台任务不共享会话，docs/04 §2），清理失败
要让 APScheduler 记下来、下一轮重试，不吞异常。

保留口径（数值都提到模块级常量，便于按运行数据回调）：

- 日志（docs/04 §10.2）：``maa_task`` 30 天 / 300,000 条、``server`` 7 天 / 200,000 条、
  ``maacore_debug`` 3 天 / 50,000 条。天数与条数两个维度同时生效，取更严格的一方：先按天
  删，删完仍超上限就按 ``id`` 升序删到上限以内。
- 流水线（docs/04 §10.3）：90 天或最近 500 条，先触发者生效；``task`` 随外键级联，
  ``log_entry`` / ``screenshot`` 的关联字段置 NULL 后各由自己的策略处理，不保留墓碑记录。
- 截图（docs/04 §10.3）：文件保留 7 天，并受总大小上限（默认 512 MB）约束；超限时按
  ``created_at`` 升序删文件并给记录打 ``deleted_at``，记录本身跟着 90 天的流水线历史清理
  （``ScreenshotRepository.purge_before``）。
- IPC 中转图：``<resource>/temp/screencap/`` 下 mtime 超过 1 小时的文件直接删，不进
  ``screenshot`` 表（docs/02 §3.3）。
- 作业资源（M29，规格 §9.11）：``kind=COPILOT`` 的 :class:`~maa_api.db.models.ResourceAsset`
  同时满足五条才删——``meta.source ∈ {ai, site}``、未设 ``retain``、没有成功的尝试、
  创建超过 ``agent.copilot_retention_days`` 天（默认 7）、没有被进行中的授权 / 定时任务 /
  排队中的任务引用。用户上传的作业与点过「保留」的作业永不自动删除。入口是
  :func:`cleanup_copilots`（可单跑），:func:`run_retention` 把它并进同一轮，结果落在
  返回值的 ``copilots_deleted`` 上。作业文件（``resource_asset.path``）在记录提交之后
  才删：先删文件再删记录，中途失败会留下一行指向不存在的文件，那比留一个孤儿文件更糟。

删十万行的一条 DELETE 会长时间持有 SQLite 写锁，期间 ``LogHub`` 刷盘只能在
``busy_timeout`` 上排队。因此日志走 ``LogRepository.purge``（内部每批 ``LIMIT 5000``、
每批自成事务），服务层再按 docs/04 §10.4 的 ``while True`` 循环反复调用，非零轮之间
``await asyncio.sleep(BATCH_PAUSE_SECONDS)`` 让出写锁；截图文件也按
:data:`SCREENSHOT_FILE_BATCH_SIZE` 分批。空间回收只做增量 vacuum
``PRAGMA incremental_vacuum(1000)``（依赖初始迁移设好的 ``auto_vacuum=INCREMENTAL``，
落点见 ``db/migrations/env.py``）；完整 ``VACUUM`` 需要重写整库并独占，只作为设置页的手动
维护操作。``screenshot.path`` 是相对 ``resource/`` 的相对路径（docs/04 §5.4），而
``resource/`` 由 ``session.DB_PATH`` 的父目录推导，测试把 ``session.DB_PATH`` 指到 tmp
目录即可整库整目录隔离。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.db import session
from maa_api.db.models import (
    Pipeline,
    ResourceAsset,
    Schedule,
    Screenshot,
    Task,
    utcnow,
)
from maa_api.db.repositories.log import LogRepository, ScreenshotRepository
from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.db.repositories.resource import ResourceAssetRepository
from maa_api.domain.enums import (
    LogSource,
    PipelineStatus,
    ResourceAssetKind,
    TaskStatus,
)
from maa_api.settings import get_settings

logger = logging.getLogger(__name__)

# lifespan 按这两个常量注册 APScheduler job（docs/04 §10.4）。
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

# --- 作业资源（M29，规格 §9.11）------------------------------------------
#: 兜底保留天数；实际取值是 ``agent.copilot_retention_days``（1–365，默认 7）。
COPILOT_RETENTION_DAYS = 7
#: 只有这两种来源会被自动清理；``user`` 与没有来源标记的作业永不自动删除。
COPILOT_AUTO_DELETE_SOURCES = frozenset({"ai", "site"})
#: 记为「成功」的结果值（§9.7 的表）：三星一定算成功，二星与「结算界面出现」按
#: 「通关即可」也按成功处理。判定不了成功标准时保守处理：宁可留，不误删。
COPILOT_SUCCESS_OUTCOMES = frozenset(
    {"three_star", "two_star", "clear", "success", "succeeded"}
)
#: 尝试记录里可能承载结果的键（``meta.attempts[]`` 的字段名由草稿服务写，见 §9.5）
#: 与显式的成功布尔键。任何一处正面证据都算成功。
COPILOT_ATTEMPT_RESULT_KEYS = ("result", "outcome", "status")
COPILOT_ATTEMPT_SUCCESS_KEYS = ("success", "succeeded", "is_success")
#: 引用判定要读的授权表（M29 由另一条工作流建；表还没落库时这一维自动跳过）。
COPILOT_GRANT_TABLE = "copilot_grant"


async def run_retention(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, int]:
    """执行一轮完整清理，返回各阶段删除计数。

    ``session_factory`` 为 ``None`` 时取 ``maa_api.db.session`` 模块的
    :data:`session.session_factory`（运行时取模块属性，测试可注入临时库）。
    资源根目录同样在运行时从 ``session.DB_PATH.parent`` 推导。

    返回 dict 含这五个 key（前四个是 M2 起的既有形状，lifespan 与测试都依赖）：
    ``logs_deleted`` / ``pipelines_deleted`` / ``screenshots_deleted`` /
    ``temp_files_deleted``，都是 int；M29 起多一个 ``copilots_deleted``
    （作业资源，:func:`cleanup_copilots` 的同一段逻辑），既有四个 key 的语义不变。
    其中 ``screenshots_deleted`` 计的是
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
        "copilots_deleted": 0,
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
                "保留策略：已删除 %d 条 %s 日志（超过 %d 天或超过 %d 条的部分）",
                deleted,
                source.value,
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
        logger.info("保留策略：已删除 %d 条流水线记录", result["pipelines_deleted"])
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
            "保留策略：已清理 %d 个截图文件、%d 条过期记录",
            result["screenshots_deleted"],
            purged_records,
        )
        await _incremental_vacuum(db)

        # 4) IPC 中转图：不进 screenshot 表，按 mtime 直接删（docs/04 §10.3）。
        result["temp_files_deleted"] = _purge_temp_files(resource_root)
        logger.info(
            "保留策略：已删除 %d 个临时中转截图", result["temp_files_deleted"]
        )
        await _incremental_vacuum(db)

        # 5) 作业资源：AI 草稿与作业站副本同时满足五个条件才删（规格 §9.11）。
        copilot_outcome, copilot_files = await _cleanup_copilots(
            db, cutoff=now - timedelta(days=_copilot_retention_days())
        )
        result["copilots_deleted"] = len(copilot_outcome["deleted"])
        await db.commit()
        _unlink_asset_files(resource_root, copilot_files)
        logger.info(
            "保留策略：已删除 %d 份作业资源（%d 份因来源/保留/成功/未到期/被引用而保留）",
            result["copilots_deleted"],
            len(copilot_outcome["skipped"]),
        )
        await _incremental_vacuum(db)

    logger.info(
        "保留策略执行完成：日志 %s 条、流水线 %s 条、截图 %s 个、临时文件 %s 个、作业 %s 份",
        result["logs_deleted"],
        result["pipelines_deleted"],
        result["screenshots_deleted"],
        result["temp_files_deleted"],
        result["copilots_deleted"],
    )
    return result


async def cleanup_copilots(
    now: datetime | None = None,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> dict[str, list[Any]]:
    """清理满足全部条件的作业资源，返回 ``{"deleted": [...], "skipped": [...]}``。

    删除条件（规格 §9.11，五条同时成立）：

    1. ``kind=COPILOT``，且 ``meta.source ∈ {ai, site}``；
    2. 未设 ``meta.retain``（用户点过「保留」）；
    3. ``meta.attempts`` 里没有成功的尝试（§9.7 的结果口径）；
    4. ``created_at`` 早于 ``now - agent.copilot_retention_days`` 天（默认 7）；
    5. 没有被进行中的授权、定时任务或排队中的任务引用。

    ``deleted`` 是删掉的资源 id；``skipped`` 是保留的候选，每项 ``{"id", "reason"}``，
    ``reason`` 取五条里第一条不满足的：``source`` / ``retained`` / ``succeeded`` /
    ``too_new`` / ``referenced``。其它 kind 的资源完全不在候选里，不出现在两个列表中。

    ``session_factory`` 为 ``None`` 时取 ``maa_api.db.session`` 的模块属性，作业文件
    的根目录同样从 ``session.DB_PATH.parent`` 推导；``now`` 为 ``None`` 时取
    :func:`maa_api.db.models.utcnow`。异常原样抛出，不在本层吞掉。
    """
    factory = session.session_factory if session_factory is None else session_factory
    resource_root = Path(session.DB_PATH).parent
    cutoff = (now or utcnow()) - timedelta(days=_copilot_retention_days())
    async with factory() as db:
        outcome, files = await _cleanup_copilots(db, cutoff=cutoff)
        await db.commit()
    # 记录已落库才删文件：失败顺序反过来会留下指向空文件的记录（见模块说明）。
    _unlink_asset_files(resource_root, files)
    logger.info(
        "保留策略：已删除 %d 份作业资源（%d 份保留）",
        len(outcome["deleted"]),
        len(outcome["skipped"]),
    )
    return outcome


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
    文件本就不存在也算处理成功：接口应返回 410「截图已过期」而不是让前端
    去撞一个 404。
    """
    marked: list[str] = []
    for shot in records:
        path = _resolve_under(resource_root, shot.path)
        if path is None:
            # 库里的相对路径越界属脏数据：跳过并留一条 debug 即可，不影响这轮清理。
            logger.debug("保留策略：截图路径不在资源目录内，已跳过 %s", shot.path)
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("保留策略：删除截图文件失败（%s）：%s", path, exc)
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
            logger.warning("保留策略：删除临时中转截图失败（%s）：%s", entry, exc)
            continue
        deleted += 1
    return deleted


@dataclass(frozen=True)
class _CopilotReference:
    """一轮清理前收集的「引用中」索引（每个维度各查一次库，不按资源逐条查）。

    ``asset_ids`` 是定时任务模板与未结束任务里出现的 ``asset_id``；其余三个集合来自
    ``copilot_grant`` 表里状态为 ``active`` 的行——作业草稿的 ``meta`` 里带
    ``session_id`` / ``stage_id``，与同一会话或同一关卡的进行中授权算引用。授权表
    还没落库时三个集合都是空的（这一维自动跳过，不挡其它四条）。
    """

    asset_ids: frozenset[str] = frozenset()
    sessions: frozenset[str] = frozenset()
    stage_ids: frozenset[str] = frozenset()
    stage_codes: frozenset[str] = frozenset()

    def matches_meta(self, meta: Mapping[str, Any]) -> bool:
        """作业草稿的 ``meta`` 是否落在进行中的授权上。"""
        for key, values in (
            ("session_id", self.sessions),
            ("stage_id", self.stage_ids),
            ("stage_code", self.stage_codes),
        ):
            value = _text(meta.get(key))
            if value and value in values:
                return True
        return False


def _copilot_retention_days() -> int:
    """``agent.copilot_retention_days``（规格 §9.11）；配置缺失/非法时退回默认值。"""
    days = getattr(
        get_settings().agent, "copilot_retention_days", COPILOT_RETENTION_DAYS
    )
    return days if isinstance(days, int) and days > 0 else COPILOT_RETENTION_DAYS


def _text(value: Any) -> str:
    """只认非空字符串：``meta`` 来自 JSON 列，字段可能是数字或 None。"""
    return value.strip() if isinstance(value, str) else ""


def _referenced_asset_ids(value: Any) -> Iterator[str]:
    """递归摘出任务参数 / 定时任务模板里的 ``asset_id``。

    Copilot 任务的 ``asset_id`` 是 :class:`~maa_api.db.models.ResourceAsset` 的主键
    （``CopilotInput.to_core_params`` 特意保留它，见 pipeline_runner 的解析点）；
    模板是用户提交的嵌套结构，递归比逐层猜形状更稳。
    """
    if isinstance(value, Mapping):
        asset_id = _text(value.get("asset_id"))
        if asset_id:
            yield asset_id
        for item in value.values():
            yield from _referenced_asset_ids(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _referenced_asset_ids(item)


def _attempt_succeeded(attempt: Any) -> bool:
    """一条尝试记录是否构成「成功」。

    任何一处正面证据都算成功（显式布尔键优先看值，结果键按 §9.7 的结果表匹配）：
    清理是不可逆操作，记录自相矛盾时选择保留。
    """
    if not isinstance(attempt, Mapping):
        return False
    for key in COPILOT_ATTEMPT_SUCCESS_KEYS:
        if key in attempt and attempt[key] is True:
            return True
    for key in COPILOT_ATTEMPT_RESULT_KEYS:
        result = _text(attempt.get(key)).lower()
        if result and result in COPILOT_SUCCESS_OUTCOMES:
            return True
    return False


def _has_successful_attempt(meta: Mapping[str, Any]) -> bool:
    """``meta.attempts``（§9.5：每次尝试的 id、结果、判定方、时间）里有没有成功。"""
    attempts = meta.get("attempts")
    if not isinstance(attempts, (list, tuple)):
        return False
    return any(_attempt_succeeded(item) for item in attempts)


def _copilot_skip_reason(
    asset: ResourceAsset, *, cutoff: datetime, references: _CopilotReference
) -> str | None:
    """五条删除条件里第一条不满足的 reason；全都满足返回 ``None``（该删）。"""
    meta = asset.meta if isinstance(asset.meta, Mapping) else {}
    if _text(meta.get("source")) not in COPILOT_AUTO_DELETE_SOURCES:
        return "source"  # 用户上传（``user`` / 没有来源标记）永不自动删除
    if bool(meta.get("retain")):
        return "retained"
    if _has_successful_attempt(meta):
        return "succeeded"
    if asset.created_at is None or asset.created_at >= cutoff:
        return "too_new"
    if asset.id in references.asset_ids or references.matches_meta(meta):
        return "referenced"
    return None


async def _cleanup_copilots(
    db: AsyncSession, *, cutoff: datetime
) -> tuple[dict[str, list[Any]], list[tuple[str, str]]]:
    """删掉该删的作业资源，返回（``{"deleted", "skipped"}``，待删文件列表）。

    只删数据库行；``(资源 id, 相对路径)`` 交给调用方在**提交之后**删文件（见模块说明）。
    不提交事务：单跑由 :func:`cleanup_copilots` 提交，并入每日任务时由
    :func:`run_retention` 在同一个会话里提交。
    """
    repo = ResourceAssetRepository(db)
    assets = await repo.list_by_kind(ResourceAssetKind.COPILOT)
    deleted: list[str] = []
    skipped: list[dict[str, str]] = []
    files: list[tuple[str, str]] = []
    if not assets:
        return {"deleted": deleted, "skipped": skipped}, files

    references = await _collect_copilot_references(db)
    for asset in assets:
        reason = _copilot_skip_reason(asset, cutoff=cutoff, references=references)
        if reason is not None:
            skipped.append({"id": asset.id, "reason": reason})
            continue
        await repo.delete(asset.id)
        deleted.append(asset.id)
        if asset.path:
            files.append((asset.id, asset.path))
    return {"deleted": deleted, "skipped": skipped}, files


async def _collect_copilot_references(db: AsyncSession) -> _CopilotReference:
    """收集引用中的资源 id、会话与关卡（见 :class:`_CopilotReference`）。"""
    sessions: set[str] = set()
    stage_ids: set[str] = set()
    stage_codes: set[str] = set()
    asset_ids: set[str] = set()

    # 1) 进行中的作业授权（``status='active'``）。表由 M29 的另一条工作流建，
    #    这里只按附录 E 的列名读，且不 import 那个模块：清理任务要能在表落库前后都跑。
    if await _table_exists(db, COPILOT_GRANT_TABLE):
        columns = await _table_columns(db, COPILOT_GRANT_TABLE)
        wanted = [
            name
            for name in ("session_id", "stage_id", "stage_code")
            if name in columns
        ]
        if "status" not in columns or not wanted:
            logger.warning(
                "保留策略：%s 表的列不符合预期，本轮跳过授权引用检查",
                COPILOT_GRANT_TABLE,
            )
        else:
            rows = (
                await db.execute(
                    text(
                        f"SELECT {', '.join(wanted)} FROM {COPILOT_GRANT_TABLE} "
                        "WHERE status = 'active'"
                    )
                )
            ).mappings()
            for row in rows:
                if "session_id" in row:
                    _add_text(sessions, row["session_id"])
                if "stage_id" in row:
                    _add_text(stage_ids, row["stage_id"])
                if "stage_code" in row:
                    _add_text(stage_codes, row["stage_code"])

    # 2) 定时任务模板：停用的也算引用（用户随时可能重新启用），与 enabled 无关。
    for template in (await db.execute(select(Schedule.template))).scalars().all():
        asset_ids.update(_referenced_asset_ids(template))

    # 3) 未结束流水线里未结束的 Copilot 任务：``params`` 保留着 ``asset_id``
    #    （pipeline_runner 在 append 前才把它换成临时文件的 ``filename``）。
    queued = (
        await db.execute(
            select(Task.params, Task.raw_params)
            .join(Pipeline, Pipeline.id == Task.pipeline_id)
            .where(
                Task.status.in_(
                    (TaskStatus.PENDING.value, TaskStatus.RUNNING.value)
                ),
                Pipeline.status.in_(
                    (PipelineStatus.PENDING.value, PipelineStatus.RUNNING.value)
                ),
            )
        )
    ).all()
    for params, raw_params in queued:
        asset_ids.update(_referenced_asset_ids(params))
        asset_ids.update(_referenced_asset_ids(raw_params))

    return _CopilotReference(
        asset_ids=frozenset(asset_ids),
        sessions=frozenset(sessions),
        stage_ids=frozenset(stage_ids),
        stage_codes=frozenset(stage_codes),
    )


def _add_text(values: set[str], value: Any) -> None:
    text_value = _text(value)
    if text_value:
        values.add(text_value)


async def _table_exists(db: AsyncSession, name: str) -> bool:
    """表是否存在（SQLite 的 ``sqlite_master``；库本身固定是 SQLite）。"""
    row = (
        await db.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :name"
            ),
            {"name": name},
        )
    ).first()
    return row is not None


async def _table_columns(db: AsyncSession, name: str) -> frozenset[str]:
    """表的列名集合；表名先做标识符校验，避免把脏数据拼进 PRAGMA。"""
    if not name.isidentifier():
        return frozenset()
    rows = (await db.execute(text(f"PRAGMA table_info({name})"))).all()
    return frozenset(str(row[1]) for row in rows)


def _unlink_asset_files(
    resource_root: Path, deletions: Sequence[tuple[str, str]]
) -> None:
    """删已删除作业的文件；失败只记日志。

    越界路径只跳过文件（库里是脏数据，但它不该让整轮清理失效）。记录已经删掉了，
    所以这里删不掉只会留下孤儿文件，不影响用户看到的结果。
    """
    for asset_id, relative in deletions:
        path = _resolve_under(resource_root, relative)
        if path is None:
            logger.debug("保留策略：作业路径不在资源目录内，已跳过 %s", relative)
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning(
                "保留策略：删除作业文件失败（asset_id=%s，%s）：%s",
                asset_id,
                path,
                exc,
            )


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
