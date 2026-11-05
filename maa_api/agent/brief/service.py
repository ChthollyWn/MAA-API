"""每日简报服务：到点建单、收尾建议与推送（M84 规格 §4.5.9、§4.5.12）。

与事件管线（B1 裁决）无关，本服务直接建 analyst 工单：

1. ``on_cron`` 按 ``brief.cron`` 到点触发：先看 ``brief.enabled``，再看今天
   （北京日历日）是否已经有一份 ``origin=schedule`` 的简报产物；都没有才准备。
2. 准备步骤先收集事实、建一份 ``status=pending`` 的 ``report`` 产物，再用
   :meth:`TicketService.create` 建 analyst 工单并写回 ``meta.ticket_id``。
3. 每 30 秒巡检 pending 简报：分析师的汇报（``details.brief_artifact_id``）到了
   就写回产物并推送；超过 30 分钟没有汇报收尾成 ``facts_only``。
4. ``POST /api/agent/brief/run`` 走 ``run_now``，与到点共用准备步骤，但工单来源
   是 web 的显式委托，且不受「一天一张」限制。

推送只在 ``push``、``brief.push_web`` 都为真且没有可见页面时发生；推送失败只记
日志，不影响产物收尾。服务本身不写总控收件箱：标准汇报转发（05 §4.1）已经覆盖。
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.brief.facts import collect_facts
from maa_api.agent.brief.render import (
    render_brief_markdown,
    render_facts_markdown,
    render_ticket_brief,
)
from maa_api.db.models import Artifact, Ticket, utcnow
from maa_api.domain.enums import (
    EventSource,
    MandateStrength,
    OutboxKind,
    RiskLevel,
    TicketOrigin,
)
from maa_api.services.schedule_service import cron_trigger
from maa_api.settings import Settings, get_settings

__all__ = [
    "BRIEF_FINALIZE_TIMEOUT_MINUTES",
    "BRIEF_JOB_ID",
    "BRIEF_SCOPE",
    "BRIEF_TIMEZONE",
    "BRIEF_WATCH_SECONDS",
    "BriefRunResult",
    "BriefService",
]

logger = logging.getLogger(__name__)

#: 简报按北京时间到点与分日（spec §4.5.9）。
BRIEF_TIMEZONE = "Asia/Shanghai"
#: 调度器里的 job id（spec §4.5.9）。
BRIEF_JOB_ID = "maa-brief-daily"
#: analyst 工单的委托范围（spec §4.5.9）。
BRIEF_SCOPE = "生成今日简报：只读查询数据并写建议，不提交流水线，不操作设备"
#: 等汇报的上限；超过就收尾成 facts_only（spec §4.5.12）。
BRIEF_FINALIZE_TIMEOUT_MINUTES = 30
#: 巡检间隔（spec §4.5.12）。
BRIEF_WATCH_SECONDS = 30

#: 收尾状态（spec §4.5.9 的 ``finalize`` 签名）。
BriefFinalizeStatus = Literal["ready", "facts_only"]

_TZ = ZoneInfo(BRIEF_TIMEZONE)

#: pending 简报产物；``json_extract`` 在 SQLite 里把 JSON true 取成 1。
_PENDING_BRIEF_SQL = text(
    "SELECT id, created_at, meta FROM artifact "
    "WHERE json_extract(meta, '$.brief') = 1 "
    "AND json_extract(meta, '$.status') = 'pending' "
    "ORDER BY created_at ASC"
)

#: 今天（北京时间日历日）是否已有自动生成的简报产物。
_SCHEDULE_BRIEF_SQL = text(
    "SELECT 1 FROM artifact "
    "WHERE json_extract(meta, '$.brief') = 1 "
    "AND json_extract(meta, '$.origin') = 'schedule' "
    "AND json_extract(meta, '$.brief_date') = :brief_date "
    "LIMIT 1"
)

#: 指向某份简报产物的最新汇报；只认 analyst 工单（spec §4.5.12）。
_REPORT_SQL = text(
    "SELECT r.ticket_id AS ticket_id, r.status AS status, "
    "r.summary AS summary, r.details AS details, t.kind AS kind "
    "FROM ticket_report r JOIN ticket t ON t.id = r.ticket_id "
    "WHERE json_extract(r.details, '$.brief_artifact_id') = :artifact_id "
    "AND r.status IN ('ready', 'failed', 'blocked') "
    "ORDER BY r.id DESC LIMIT 1"
)

_FACTS_ONLY_PUSH_BODY = "今天的简报只生成了数据部分"


@dataclass(frozen=True, slots=True)
class BriefRunResult:
    """``POST /api/agent/brief/run`` 的返回值（G6 的响应形状）。"""

    ticket_id: str
    artifact_id: str


def _beijing_day(now: datetime) -> date:
    """把 naive/aware 时间换成北京日历日。"""
    moment = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    return moment.astimezone(_TZ).date()


def _as_naive_utc(value: Any) -> datetime | None:
    """把 ``text()`` 查询里可能原样返回的时间解析成 naive UTC。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    if moment.tzinfo is not None:
        return moment.astimezone(UTC).replace(tzinfo=None)
    return moment


def _json_dict(value: Any) -> dict[str, Any]:
    """把 JSON 列（驱动可能给 dict，也可能给字符串）解析成映射。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        try:
            data = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def _extract_suggestions(details: Any) -> list[str]:
    """``details.suggestions`` 里的字符串，最多 5 条，每条截到 120 字。"""
    raw = _json_dict(details).get("suggestions")
    if not isinstance(raw, list):
        return []
    suggestions: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        item_text = item.strip()
        if item_text:
            suggestions.append(item_text[:120])
        if len(suggestions) >= 5:
            break
    return suggestions


def _advice_markdown(status: str, suggestions: list[str]) -> str:
    """「今日建议」小节，与 ``render._advice_section`` 同口径（spec §4.5.11）。"""
    lines = ["## 今日建议"]
    if status == "pending":
        lines.append("生成中")
    elif status == "facts_only":
        lines.append("这次没有生成建议")
    else:
        items = [
            str(item).strip()
            for item in (suggestions or [])
            if str(item).strip()
        ][:5]
        if items:
            lines.extend(f"{index}. {item}" for index, item in enumerate(items, 1))
        else:
            lines.append("这次没有生成建议")
    return "\n".join(lines)


def _compose_brief_body(
    facts_markdown: str,
    summary: str | None,
    suggestions: list[str],
    status: str,
) -> str:
    """把建产物时存下的事实正文与收尾时的摘要/建议拼成全文。

    ``facts_markdown`` 是 ``render_facts_markdown`` 的输出（标题 + 数据小节），
    与 ``render_brief_markdown`` 同构：摘要放在标题之后、数据之前，建议在最后。
    """
    parts: list[str] = []
    head, _separator, tail = str(facts_markdown or "").partition("\n\n")
    if head:
        parts.append(head)
    summary_text = (summary or "").strip()
    if summary_text:
        parts.append(summary_text)
    if tail:
        parts.append(tail)
    parts.append(_advice_markdown(status, suggestions))
    return "\n\n".join(parts)


class BriefService:
    """到点生成、巡检收尾与推送每日简报；调度器与依赖由装配方注入。"""

    def __init__(
        self,
        app: Any,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        has_visible_client: Callable[[], bool],
        settings_provider: Callable[[], Settings] = get_settings,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._app = app
        self._session_factory = session_factory
        self._has_visible_client = has_visible_client
        self._settings_provider = settings_provider
        self._clock = clock
        self._scheduler: AsyncIOScheduler | None = None
        self._watch_task: asyncio.Task[Any] | None = None

    # ------------------------------------------------------------------
    # 装配与生命周期
    # ------------------------------------------------------------------
    def attach_scheduler(self, scheduler: AsyncIOScheduler) -> None:
        """按 ``brief.cron`` 注册每日简报 job；cron 写错只记 error、不拦启动。"""
        cron = self._settings_provider().brief.cron
        try:
            trigger = cron_trigger(cron, BRIEF_TIMEZONE)
        except ValueError:
            logger.error(
                "brief.cron 无法解析，本次不注册每日简报：%s", cron, exc_info=True
            )
            return
        scheduler.add_job(
            self.on_cron,
            trigger,
            id=BRIEF_JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self._scheduler = scheduler

    async def start(self) -> None:
        """恢复重启前留下的 pending 简报，然后起 30 秒巡检任务。"""
        try:
            await self._recover_stale()
        except Exception:  # noqa: BLE001 - 恢复失败不能拦启动
            logger.warning("简报启动恢复失败", exc_info=True)
        if self._watch_task is None or self._watch_task.done():
            self._watch_task = asyncio.create_task(
                self._watch_loop(), name="maa-brief-watch"
            )

    async def stop(self) -> None:
        """取消巡检任务。"""
        task, self._watch_task = self._watch_task, None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            try:
                await task
            except Exception:  # noqa: BLE001 - 关停阶段不再向上抛
                logger.warning("简报巡检任务退出时出错", exc_info=True)

    # ------------------------------------------------------------------
    # 到点与手动
    # ------------------------------------------------------------------
    async def on_cron(self) -> None:
        """``brief.cron`` 到点：同一天自动生成的至多一张（spec §4.5.9）。"""
        try:
            if not self._settings_provider().brief.enabled:
                return
            brief_day = _beijing_day(self._clock())
            if await self._has_schedule_brief(brief_day):
                return
            await self._prepare(
                origin="schedule",
                ticket_origin=TicketOrigin.SYSTEM,
                strength=MandateStrength.INFERRED,
                source=EventSource.SCHEDULE,
                sender="brief",
            )
        except Exception:  # noqa: BLE001 - 定时 job 抛错不能拖垮调度器
            logger.exception("每日简报生成失败")

    async def run_now(self) -> BriefRunResult:
        """手动生成一份；不受每日一张限制（spec §4.5.9 第 5 步）。"""
        artifact, ticket = await self._prepare(
            origin="manual",
            ticket_origin=TicketOrigin.USER,
            strength=MandateStrength.EXPLICIT,
            source=EventSource.WEB,
            sender="web",
        )
        return BriefRunResult(
            ticket_id=str(ticket.id), artifact_id=str(artifact.id)
        )

    async def _prepare(
        self,
        *,
        origin: str,
        ticket_origin: TicketOrigin,
        strength: MandateStrength,
        source: EventSource,
        sender: str,
    ) -> tuple[Artifact, Ticket]:
        """``on_cron`` 与 ``run_now`` 共用的准备步骤（spec §4.5.9）。"""
        now = self._clock()
        facts = await collect_facts(self._app, now)
        facts_markdown = render_facts_markdown(facts)
        body = render_brief_markdown(facts, None, [], "pending")
        meta: dict[str, Any] = {
            "brief": True,
            "brief_date": _beijing_day(now).isoformat(),
            "status": "pending",
            "origin": origin,
            "ticket_id": None,
            "facts_markdown": facts_markdown,
        }
        artifact_service = self._artifact_service()
        artifact = await artifact_service.put(
            kind="report",
            title=f"每日简报 · {facts.date.month} 月 {facts.date.day} 日",
            body=body.encode("utf-8"),
            mime="text/markdown",
            meta=meta,
        )
        ticket, _mandate = await self._ticket_service().create(
            kind="analyst",
            title=f"每日简报 {facts.date.month:02d}-{facts.date.day:02d}",
            scope=BRIEF_SCOPE,
            origin=ticket_origin,
            source=source,
            sender=sender,
            strength=strength,
            risk_ceiling=RiskLevel.NONE,
            brief=render_ticket_brief(facts_markdown, str(artifact.id), facts.date),
        )
        meta["ticket_id"] = str(ticket.id)
        await artifact_service.update(str(artifact.id), content=body, meta=meta)
        return artifact, ticket

    async def _has_schedule_brief(self, brief_day: date) -> bool:
        async with self._session_factory() as db:
            result = await db.execute(
                _SCHEDULE_BRIEF_SQL, {"brief_date": brief_day.isoformat()}
            )
            return result.first() is not None

    # ------------------------------------------------------------------
    # 巡检与收尾
    # ------------------------------------------------------------------
    async def watch_once(self) -> int:
        """扫一遍 pending 简报并收尾；返回本次收尾的条数（测试直接调用）。"""
        rows = await self._pending_briefs()
        now = self._clock()
        cutoff = now - timedelta(minutes=BRIEF_FINALIZE_TIMEOUT_MINUTES)
        handled = 0
        for row in rows:
            mapping = row._mapping
            artifact_id = str(mapping["id"])
            try:
                report = await self._latest_report(artifact_id)
                if report is not None and report["kind"] == "analyst":
                    suggestions = _extract_suggestions(report["details"])
                    status: BriefFinalizeStatus = (
                        "ready"
                        if report["status"] == "ready" and suggestions
                        else "facts_only"
                    )
                    await self.finalize(
                        artifact_id,
                        ticket_id=report["ticket_id"],
                        status=status,
                        summary=report["summary"],
                        suggestions=suggestions,
                    )
                    handled += 1
                else:
                    created_at = _as_naive_utc(mapping["created_at"])
                    if created_at is None or created_at >= cutoff:
                        continue
                    meta = _json_dict(mapping["meta"])
                    await self.finalize(
                        artifact_id,
                        ticket_id=meta.get("ticket_id"),
                        status="facts_only",
                        summary=None,
                        suggestions=[],
                    )
                    handled += 1
            except Exception:  # noqa: BLE001 - 单条失败不影响其他简报
                logger.warning("简报收尾失败：artifact=%s", artifact_id, exc_info=True)
        return handled

    async def finalize(
        self,
        artifact_id: str,
        *,
        ticket_id: str | None,
        status: BriefFinalizeStatus,
        summary: str | None,
        suggestions: list[str],
        push: bool = True,
    ) -> None:
        """重新渲染全文写回产物，并按 §4.5.12 决定是否推送。"""
        artifact_service = self._artifact_service()
        detail = await artifact_service.get(artifact_id)
        meta = dict(detail.artifact.meta or {})
        body = _compose_brief_body(
            str(meta.get("facts_markdown") or ""), summary, suggestions, status
        )
        meta["status"] = status
        meta["ticket_id"] = ticket_id
        await artifact_service.update(artifact_id, content=body, meta=meta)
        if not push:
            return
        settings = self._settings_provider()
        state = getattr(self._app, "state", None)
        push_body = (
            _FACTS_ONLY_PUSH_BODY
            if status == "facts_only"
            else str(summary or "")[:120]
        )
        payload = {
            "type": "notification",
            "title": "每日简报",
            "body": push_body,
            "data": {
                "kind": "brief",
                "artifact_id": artifact_id,
                "ticket_id": str(ticket_id) if ticket_id else None,
                "url": f"/tickets/{ticket_id}" if ticket_id else "/",
            },
        }
        if settings.brief.push_web and not self._has_visible_client():
            # M85：先写 outbox，投递由 OutboxWorker 的 webpush 发送器完成。
            outbox = getattr(state, "webpush_outbox", None)
            if outbox is not None:
                try:
                    await outbox.enqueue(
                        payload,
                        kind=OutboxKind.BRIEF,
                        dedupe_key=f"brief:{artifact_id}",
                        ref_ticket_id=str(ticket_id) if ticket_id else None,
                    )
                except Exception:  # noqa: BLE001 - 推送失败不影响产物收尾
                    logger.warning(
                        "简报推送失败：artifact=%s", artifact_id, exc_info=True
                    )
        if settings.brief.push_im:
            channel_service = getattr(state, "channel_service", None)
            if channel_service is not None:
                if ticket_id:
                    text = f"每日简报\n{push_body}\n/tickets/{ticket_id}"
                else:
                    text = f"每日简报\n{push_body}"
                try:
                    await channel_service.enqueue_for_bindings(
                        OutboxKind.BRIEF,
                        artifact_id,
                        text,
                        {
                            "artifact_id": artifact_id,
                            "ticket_id": str(ticket_id) if ticket_id else None,
                            "url": f"/tickets/{ticket_id}" if ticket_id else "/",
                        },
                        ref_ticket_id=str(ticket_id) if ticket_id else None,
                    )
                except Exception:  # noqa: BLE001 - 出站失败不影响产物收尾
                    logger.warning(
                        "简报写 IM 发件箱失败：artifact=%s", artifact_id, exc_info=True
                    )

    async def _recover_stale(self) -> None:
        """启动恢复：pending 且超过 30 分钟的简报标 facts_only，不推送。"""
        rows = await self._pending_briefs()
        if not rows:
            return
        cutoff = self._clock() - timedelta(minutes=BRIEF_FINALIZE_TIMEOUT_MINUTES)
        for row in rows:
            mapping = row._mapping
            created_at = _as_naive_utc(mapping["created_at"])
            if created_at is None or created_at >= cutoff:
                continue
            meta = _json_dict(mapping["meta"])
            try:
                await self.finalize(
                    str(mapping["id"]),
                    ticket_id=meta.get("ticket_id"),
                    status="facts_only",
                    summary=None,
                    suggestions=[],
                    push=False,
                )
            except Exception:  # noqa: BLE001 - 单条恢复失败不拦启动
                logger.warning(
                    "简报启动恢复失败：artifact=%s", mapping["id"], exc_info=True
                )

    async def _pending_briefs(self) -> list[Any]:
        async with self._session_factory() as db:
            result = await db.execute(_PENDING_BRIEF_SQL)
            return list(result.all())

    async def _latest_report(self, artifact_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as db:
            result = await db.execute(_REPORT_SQL, {"artifact_id": artifact_id})
            row = result.first()
        if row is None:
            return None
        mapping = row._mapping
        return {
            "ticket_id": str(mapping["ticket_id"]),
            "status": str(mapping["status"]),
            "summary": mapping["summary"],
            "details": mapping["details"],
            "kind": str(mapping["kind"]),
        }

    async def _watch_loop(self) -> None:
        while True:
            try:
                await self.watch_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 巡检失败下一轮重试
                logger.warning("简报巡检失败，下一轮重试", exc_info=True)
            await asyncio.sleep(BRIEF_WATCH_SECONDS)

    # ------------------------------------------------------------------
    # 依赖
    # ------------------------------------------------------------------
    def _artifact_service(self) -> Any:
        service = getattr(getattr(self._app, "state", None), "artifact_service", None)
        if service is None:
            raise RuntimeError("BriefService 没有装配 artifact_service")
        return service

    def _ticket_service(self) -> Any:
        service = getattr(getattr(self._app, "state", None), "ticket_service", None)
        if service is None:
            raise RuntimeError("BriefService 没有装配 ticket_service")
        return service
