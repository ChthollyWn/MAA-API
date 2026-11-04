"""历史索引：把工单、压缩检查点与 v2 诊断会话写进 ``history`` 检索来源（M79 规格 §4.5、07 §6）。

三种文档的 ``external_id`` 与正文按 07 §6 的表：

===============  ==========================  ====================================
external_id      何时写                      正文
===============  ==========================  ====================================
``ticket:<id>``  工单进入终态                标题、种类、终态、委托、汇报、结果
``checkpoint:<id>``  检查点生成              检查点摘要正文
``incident:<session_id>``  启动扫一次 v2 诊断会话  标题与最后一条助手回答（≤4000 字）
===============  ==========================  ====================================

纪律：

- 写入前一律过 :func:`~maa_api.agent.redaction.redact_remote_data`；历史正文里可能
  有工具输出转述的凭据。
- 写索引失败重试 :data:`INDEX_ATTEMPTS` 次，仍失败只记日志，绝不把异常抛给工单
  终态提交、压缩结果或启动流程。
- 只处理总控与 Worker 的会话；诊断会话只扫一次（``main.py`` 在启动时调用
  :meth:`HistoryIndexer.index_incidents`），之后不再新增。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.redaction import redact_remote_data
from maa_api.db.models import (
    AgentSession,
    CompactionCheckpoint,
    Mandate,
    Ticket,
    TicketReport,
)
from maa_api.db.repositories.agent import AgentMessageRepository
from maa_api.db.repositories.ticket import (
    MandateRepository,
    TicketReportRepository,
    TicketRepository,
)
from maa_api.domain.enums import AgentSessionKind

logger = logging.getLogger(__name__)

__all__ = [
    "CHECKPOINT_EXTERNAL_PREFIX",
    "HISTORY_SOURCE_ID",
    "INCIDENT_EXTERNAL_PREFIX",
    "INDEX_ATTEMPTS",
    "MAX_INCIDENT_CHARS",
    "TICKET_EXTERNAL_PREFIX",
    "HistoryIndexer",
]

#: 写入的检索来源 id（07 §3、§6）。
HISTORY_SOURCE_ID = "history"
#: 三类文档的 ``external_id`` 前缀（07 §6）。
TICKET_EXTERNAL_PREFIX = "ticket:"
CHECKPOINT_EXTERNAL_PREFIX = "checkpoint:"
INCIDENT_EXTERNAL_PREFIX = "incident:"
#: 索引写入的最多尝试次数（含第一次）：失败重试三次后记日志。
INDEX_ATTEMPTS = 3
#: 诊断会话文档正文上限（07 §6）。
MAX_INCIDENT_CHARS = 4000
#: 工单汇报最多带多少条；与抽取的历史输入同一量级。
MAX_REPORTS = 100
#: 结果关键字段 JSON 的长度上限。
MAX_RESULT_CHARS = 2000
#: 单条汇报摘要的截断长度。
MAX_SUMMARY_CHARS = 1000


def _text(value: Any) -> str:
    """消息正文归一成文本：字符串原样，多模态数组取 text 部分之和。"""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        parts: list[str] = []
        for part in value:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
        return "".join(parts)
    return ""


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def render_ticket_document(
    ticket: Ticket,
    mandate: Mandate | None,
    reports: Sequence[TicketReport],
) -> str:
    """``ticket:<id>`` 的正文（07 §6）：标题、种类与终态、委托、全部汇报、结果字段。"""
    lines = [
        f"工单：{ticket.title}",
        f"种类：{ticket.kind}；终态：{ticket.status}",
    ]
    if mandate is not None:
        lines.append(f"委托范围：{mandate.scope}")
    if reports:
        lines.append("汇报：")
        for report in list(reports)[-MAX_REPORTS:]:
            lines.append(
                f"- [{report.status}] {_clip(str(report.summary), MAX_SUMMARY_CHARS)}"
            )
    result = ticket.result or {}
    if result:
        try:
            rendered = json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):  # pragma: no cover - 结果本应可序列化
            rendered = str(result)
        lines.append(f"结果关键字段：{_clip(rendered, MAX_RESULT_CHARS)}")
    return "\n".join(lines)


def render_incident_document(session: AgentSession, answer: str) -> str:
    """``incident:<session_id>`` 的正文：标题与最后一条助手回答，不超过 4000 字。"""
    title = (session.title or "").strip() or f"诊断会话 {session.id}"
    body = f"标题：{title}\n\n最后回答：\n{answer.strip()}"
    return body[:MAX_INCIDENT_CHARS]


class HistoryIndexer:
    """把本机历史整理成 ``history`` 来源文档；不持有全局状态。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        index_service: Any = None,
    ) -> None:
        self._session_factory = session_factory
        self._index = index_service

    # -- 三个写入入口 ---------------------------------------------------
    async def on_ticket_ended(self, ended: Any) -> bool:
        """工单终态监听者：只取 ``ticket_id``，其余按库里的最终状态重读。"""
        ticket_id = str(getattr(ended, "ticket_id", "") or "")
        if not ticket_id:
            logger.warning("历史索引收到没有 ticket_id 的终态通知，跳过")
            return False
        return await self.index_ticket(ticket_id)

    async def index_ticket(self, ticket_id: str) -> bool:
        """写 ``ticket:<id>``；读不到工单时只记日志。"""
        try:
            ticket, mandate, reports = await self._load_ticket(ticket_id)
        except Exception:  # noqa: BLE001 - 读库失败不能影响终态提交
            logger.warning("历史索引读取工单失败：ticket=%s", ticket_id, exc_info=True)
            return False
        if ticket is None:
            logger.warning("历史索引找不到工单：ticket=%s", ticket_id)
            return False
        body = redact_remote_data(render_ticket_document(ticket, mandate, reports))
        return await self._push(
            [
                self._document(
                    external_id=f"{TICKET_EXTERNAL_PREFIX}{ticket_id}",
                    title=ticket.title,
                    text=body,
                    version=_iso(ticket.updated_at),
                    fetched_at=ticket.updated_at,
                    meta={"kind": "ticket", "ticket_kind": ticket.kind, "status": ticket.status},
                )
            ]
        )

    async def index_checkpoint(self, checkpoint_id: str) -> bool:
        """写 ``checkpoint:<id>``；检查点正文是渲染后的摘要。"""
        try:
            async with self._session_factory() as session:
                checkpoint = await session.get(
                    CompactionCheckpoint, checkpoint_id, populate_existing=True
                )
        except Exception:  # noqa: BLE001 - 读库失败不能影响压缩
            logger.warning(
                "历史索引读取检查点失败：checkpoint=%s", checkpoint_id, exc_info=True
            )
            return False
        if checkpoint is None:
            logger.warning("历史索引找不到检查点：checkpoint=%s", checkpoint_id)
            return False
        body = redact_remote_data(str(checkpoint.summary or ""))
        return await self._push(
            [
                self._document(
                    external_id=f"{CHECKPOINT_EXTERNAL_PREFIX}{checkpoint_id}",
                    title="压缩检查点",
                    text=body,
                    version=_iso(checkpoint.created_at),
                    fetched_at=checkpoint.created_at,
                    meta={
                        "kind": "checkpoint",
                        "session_id": checkpoint.session_id,
                        "upto_seq": checkpoint.upto_seq,
                    },
                )
            ]
        )

    async def index_incidents(self) -> int:
        """启动扫描 v2 诊断会话，返回写入的文档数；只在这个入口扫一次。"""
        try:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(AgentSession)
                    .where(AgentSession.kind == AgentSessionKind.INCIDENT.value)
                    .order_by(AgentSession.created_at.asc(), AgentSession.id.asc())
                )
                sessions = list(result.scalars().all())
        except Exception:  # noqa: BLE001 - 启动扫描失败只记日志
            logger.warning("历史索引扫描诊断会话失败", exc_info=True)
            return 0

        documents: list[Any] = []
        for session_row in sessions:
            try:
                document = await self._incident_document(session_row)
            except Exception:  # noqa: BLE001 - 单个会话读失败不影响其余
                logger.warning(
                    "历史索引读取诊断会话失败：session=%s",
                    session_row.id,
                    exc_info=True,
                )
                continue
            if document is not None:
                documents.append(document)
        if not documents:
            return 0
        ok = await self._push(documents)
        return len(documents) if ok else 0

    # -- 内部 -----------------------------------------------------------
    async def _load_ticket(
        self, ticket_id: str
    ) -> tuple[Ticket | None, Mandate | None, list[TicketReport]]:
        async with self._session_factory() as session:
            ticket = await TicketRepository(session).get(ticket_id)
            if ticket is None:
                return None, None, []
            mandate = await MandateRepository(session).get_by_ticket(ticket_id)
            page = await TicketReportRepository(session).list(
                ticket_id, page=1, size=MAX_REPORTS
            )
            # 仓储按 created_at 降序分页；正文按时间正序读起来更自然。
            reports = list(reversed(page.items))
        return ticket, mandate, reports

    async def _incident_document(self, session_row: AgentSession) -> Any | None:
        async with self._session_factory() as session:
            messages = await AgentMessageRepository(session).list_by_session(
                session_row.id
            )
        answer = ""
        for message in reversed(messages):
            if str(message.role) != "assistant":
                continue
            text = _text(message.content).strip()
            if text:
                answer = text
                break
        if not answer:
            return None
        body = redact_remote_data(render_incident_document(session_row, answer))
        return self._document(
            external_id=f"{INCIDENT_EXTERNAL_PREFIX}{session_row.id}",
            title=(session_row.title or "").strip() or f"诊断会话 {session_row.id}",
            text=body,
            version=_iso(session_row.updated_at),
            fetched_at=session_row.updated_at,
            meta={"kind": "incident", "session_id": session_row.id},
        )

    @staticmethod
    def _document(
        *,
        external_id: str,
        title: str,
        text: str,
        version: str | None,
        fetched_at: datetime | None,
        meta: dict[str, Any],
    ) -> Any:
        # 延迟导入：本模块在 main 的检索层装配之后才被用到，导入期少一层依赖。
        from maa_api.agent.knowledge.index.indexer import SourceDocument

        return SourceDocument(
            external_id=external_id,
            title=title,
            text=text,
            version=version,
            fetched_at=fetched_at,
            meta=meta,
        )

    async def _push(self, documents: Sequence[Any]) -> bool:
        """写 ``history`` 来源；失败最多尝试 ``INDEX_ATTEMPTS`` 次，仍失败只记日志。"""
        if self._index is None:
            logger.warning(
                "历史索引未装配，跳过 %d 篇文档：%s",
                len(documents),
                [getattr(document, "external_id", "") for document in documents],
            )
            return False
        last_error: BaseException | None = None
        for attempt in range(1, INDEX_ATTEMPTS + 1):
            try:
                await self._index.push_documents(HISTORY_SOURCE_ID, list(documents))
                return True
            except Exception as exc:  # noqa: BLE001 - 索引失败不能影响调用方
                last_error = exc
                logger.debug(
                    "历史索引写入失败（第 %d 次）：%s",
                    attempt,
                    [getattr(document, "external_id", "") for document in documents],
                    exc_info=True,
                )
        logger.warning(
            "历史索引写入失败，已尝试 %d 次：%s",
            INDEX_ATTEMPTS,
            [getattr(document, "external_id", "") for document in documents],
            exc_info=(
                (type(last_error), last_error, last_error.__traceback__)
                if last_error is not None
                else None
            ),
        )
        return False
