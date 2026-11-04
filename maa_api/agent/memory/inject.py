"""记忆注入中间件（M79 规格 §4.5、07 §5.4、05 §5.2 第 4 位）。

只作用于总控与 Worker。每轮 ``before_model`` 把选中的记忆写进 system 稳定段的
``memory`` 槽（``SYSTEM_SEGMENT_ORDER`` 的第 4 段）：

1. 候选是 ``status=active`` 且未过期的记忆；选择前先让
   :meth:`MemoryService.active_for_injection` 做一次过期扫描。
2. 必选组：``semantic`` 且 ``subject`` 为 ``preference`` / ``account``，按
   ``confidence``、``updated_at`` 排序，最多占预算的 40%。
3. 相关组：:meth:`Retriever.search` 显式查 ``memory`` 来源。Worker 的查询词是工单
   标题、委托范围与任务说明；总控是当前检查点摘要加最近三条用户消息。
4. 先必选再相关，token 用 M67 的 :func:`estimate_text` 累加，超过
   ``memory.max_injection_tokens``（默认 1500，0–8000）就停。
5. 第 4 段开头逐字是 :data:`MEMORY_HEADER`；每条写种类、日期与内容。
6. 只在**重新选择**时刷新被注入行的 ``last_used_at`` / ``use_count``。缓存命中时
   第 4 段字节不变，不再刷用量。

缓存按 ``session_id``：同一工单的 Worker 沿用第一轮的选择；总控在新会话（新 key）、
每次压缩后（``Compactor`` 调 :meth:`MemoryInjectionCache.invalidate`）、或生效记忆
有增删改时（选择指纹变化）重新选择。

可观测：按 docs/10 记 ``memory.inject`` span（属性 ``maa.memory.items`` /
``maa.memory.tokens``）。仓库目前没有指标设施（没有 OTel meter / Prometheus
registry），``maa.memory.items`` / ``maa.memory.tokens`` 两个指标以结构化日志
（``event=memory.inject``）落地，等指标设施落地后再接。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from maa_api.agent.context.layout import (
    SYSTEM_SEGMENT_ORDER,
    ContextLayout,
    Segment,
    put_segment,
)
from maa_api.agent.context.tokens import TokenEstimator, estimate_text
from maa_api.agent.harness.context import ModelRequest, RunContext
from maa_api.agent.harness.middleware import Middleware
from maa_api.agent.specs.schema import LEGACY_SPEC_NAMES
from maa_api.agent.trace import SpanHandle
from maa_api.db.models import MemoryItem
from maa_api.db.repositories.agent import AgentMessageRepository
from maa_api.db.repositories.compaction import CompactionCheckpointRepository
from maa_api.db.repositories.ticket import MandateRepository, TicketRepository
from maa_api.domain.enums import MemoryKind, SpanKind
from maa_api.settings import get_settings

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_MAX_INJECTION_TOKENS",
    "MAX_INJECTION_TOKENS",
    "MEMORY_HEADER",
    "MEMORY_SEGMENT",
    "MIN_INJECTION_TOKENS",
    "REQUIRED_BUDGET_RATIO",
    "MemoryInjectionCache",
    "MemoryInjectionMiddleware",
    "MemorySelection",
]

#: 第 4 段的段名（``SYSTEM_SEGMENT_ORDER`` 的 ``memory`` 槽）。
MEMORY_SEGMENT = "memory"
#: 第 4 段固定开头，逐字照抄规格 §4.5 / 07 §5.4。
MEMORY_HEADER = "长期记忆（用户审核过；与工具现查结果或用户档案冲突时以它们为准）"
#: ``memory.max_injection_tokens`` 的默认值与范围（规格 §4.4）。
DEFAULT_MAX_INJECTION_TOKENS = 1500
MIN_INJECTION_TOKENS = 0
MAX_INJECTION_TOKENS = 8000
#: 必选组最多占预算的比例（规格 §4.5）。
REQUIRED_BUDGET_RATIO = 0.4
#: 相关组最多取多少条检索命中；预算通常先到顶，留个上限避免无谓遍历。
MAX_RELEVANT_HITS = 30
#: 检索词上限与检索器校验一致（500 字）。
MAX_QUERY_CHARS = 500
#: 每次查询带最近几条用户消息（规格 §4.5）。
RECENT_USER_MESSAGES = 3
#: 单条用户消息进查询词前的截断长度。
MAX_QUERY_MESSAGE_CHARS = 300

#: 必选组的分类键（07 §5.1）。
REQUIRED_SUBJECTS = frozenset({"preference", "account"})
#: 渲染用的中文种类名；规格允许写中文名或 ``semantic`` 等（M79 §4.5 第 5 条）。
KIND_LABELS = {
    MemoryKind.SEMANTIC.value: "事实",
    MemoryKind.EPISODIC.value: "经历",
    MemoryKind.PROCEDURAL.value: "套路",
}


@dataclass(frozen=True, slots=True)
class MemorySelection:
    """一次选中的第 4 段正文与缓存指纹；``items`` / ``tokens`` 供 span 与日志用。"""

    section: str
    fingerprint: tuple[Any, ...]
    items: int
    tokens: int


class MemoryInjectionCache:
    """按 ``session_id`` 缓存选择结果；命中时第 4 段字节级不变。"""

    def __init__(self) -> None:
        self._entries: dict[str, MemorySelection] = {}

    def get(self, session_id: str) -> MemorySelection | None:
        return self._entries.get(session_id)

    def put(self, session_id: str, selection: MemorySelection) -> None:
        self._entries[session_id] = selection

    def invalidate(self, session_id: str) -> None:
        """压缩后由 ``Compactor`` 调用：下一轮重新选择。"""
        self._entries.pop(session_id, None)

    def clear(self) -> None:
        self._entries.clear()


def _app_state(ctx: RunContext) -> Any:
    """运行上下文里的应用状态；``ctx.app`` 优先，测试的 ``ctx.request`` 兜底。"""
    state = getattr(getattr(ctx, "app", None), "state", None)
    if state is None:
        state = getattr(
            getattr(getattr(ctx, "request", None), "app", None), "state", None
        )
    return state


def _memory_service(ctx: RunContext) -> Any:
    return getattr(_app_state(ctx), "memory_service", None)


def _retriever(ctx: RunContext) -> Any:
    return getattr(_app_state(ctx), "retriever", None)


def _layout_service(ctx: RunContext) -> ContextLayout:
    """优先用进程内的 ``app.state.context_layout``，测试里退回本轮本地实例。"""
    service = getattr(_app_state(ctx), "context_layout", None)
    if isinstance(service, ContextLayout):
        return service
    local = ctx.state.get("memory.context_layout")
    if isinstance(local, ContextLayout):
        return local
    local = ContextLayout(TokenEstimator())
    ctx.state["memory.context_layout"] = local
    return local


def _content_text(value: Any) -> str:
    """消息正文归一成文本：字符串原样，多模态数组取 text 部分之和。"""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        parts: list[str] = []
        for part in value:
            if isinstance(part, Mapping) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
        return "".join(parts)
    return ""


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit]


def _date_of(memory: MemoryItem) -> str:
    moment = getattr(memory, "created_at", None) or getattr(memory, "updated_at", None)
    if isinstance(moment, datetime):
        return moment.strftime("%Y-%m-%d")
    return ""


def _line_for(memory: MemoryItem) -> str:
    """一条记忆的渲染：``- 种类｜YYYY-MM-DD｜内容``。"""
    kind = str(getattr(memory, "kind", ""))
    label = KIND_LABELS.get(kind, kind or "记忆")
    content = " ".join(str(getattr(memory, "content", "") or "").split())
    return f"- {label}｜{_date_of(memory)}｜{content}"


def _render(lines: Sequence[str]) -> str:
    return "\n".join([MEMORY_HEADER, *lines]) if lines else ""


def _budget_of(settings_provider: Callable[[], Any]) -> int:
    """``memory.max_injection_tokens``：缺段按 1500，越界夹到 0–8000。"""
    try:
        settings = settings_provider()
        memory = getattr(settings, "memory", None)
        value = getattr(memory, "max_injection_tokens", DEFAULT_MAX_INJECTION_TOKENS)
        budget = int(value)
    except (TypeError, ValueError, AttributeError):
        budget = DEFAULT_MAX_INJECTION_TOKENS
    return max(MIN_INJECTION_TOKENS, min(MAX_INJECTION_TOKENS, budget))


def _fingerprint(active: Sequence[MemoryItem], budget: int) -> tuple[Any, ...]:
    """缓存指纹：生效集合、每条的更新时间与状态，外加预算。

    生效记忆有增删改（``updated_at`` 变）、过期扫描改了状态、或预算改了，都会让
    指纹变化并触发重选；同一工单的普通轮次指纹不变，命中缓存后字节不变。
    """
    return (
        budget,
        tuple(
            (
                str(getattr(memory, "id", "")),
                (
                    memory.updated_at.isoformat()
                    if isinstance(getattr(memory, "updated_at", None), datetime)
                    else ""
                ),
                str(getattr(memory, "status", "")),
            )
            for memory in active
        ),
    )


def _sort_required(memories: Sequence[MemoryItem]) -> list[MemoryItem]:
    """必选组按 ``confidence`` 降序、``updated_at`` 降序、``id`` 升序，稳定可测。"""

    def key(memory: MemoryItem) -> tuple[float, float, str]:
        confidence = getattr(memory, "confidence", None)
        confidence_value = float(confidence) if confidence is not None else 0.0
        updated = getattr(memory, "updated_at", None)
        stamp = updated.timestamp() if isinstance(updated, datetime) else 0.0
        return (-confidence_value, -stamp, str(getattr(memory, "id", "")))

    return sorted(memories, key=key)


def _memory_id_of(document_id: Any) -> str:
    """``memory:<id>`` → ``<id>``；兼容旧索引里直接写 id 的文档。"""
    text = str(document_id or "")
    return text[len("memory:") :] if text.startswith("memory:") else text


class MemoryInjectionMiddleware(Middleware):
    """05 §5.2 第 4 位：把选中的长期记忆写进 system 第 4 段。"""

    name = "memory_inject"
    applies_to = frozenset({"orchestrator", "worker"})

    def __init__(
        self,
        *,
        session_factory: Any = None,
        settings_provider: Callable[[], Any] = get_settings,
        cache: MemoryInjectionCache | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings_provider = settings_provider
        self._cache = cache
        #: 没有装配进程级缓存时（局部测试、回放）退回实例缓存。
        self._local_cache = cache or MemoryInjectionCache()

    # ------------------------------------------------------------------
    # 模型调用前：选择、渲染、写段
    # ------------------------------------------------------------------
    async def before_model(self, ctx: RunContext, req: ModelRequest) -> ModelRequest:
        if ctx.spec.name in LEGACY_SPEC_NAMES:
            return req
        parts = req.meta.get("layout_parts")
        if parts is None:
            return req
        service = _memory_service(ctx)
        if service is None or service.memory_disabled():
            return req
        budget = _budget_of(self._settings_provider)
        if budget <= 0:
            return req

        active = await service.active_for_injection()
        fingerprint = _fingerprint(active, budget)
        cache = self._cache_for(ctx)
        session_key = ctx.session_id
        selection = cache.get(session_key) if session_key else None
        fresh = selection is None or selection.fingerprint != fingerprint
        if fresh:
            query = await self._query_for(ctx)
            chosen = await self._select(ctx, active, query, budget)
            section = _render([_line_for(memory) for memory in chosen])
            selection = MemorySelection(
                section=section,
                fingerprint=fingerprint,
                items=len(chosen),
                tokens=estimate_text(section),
            )
            if session_key:
                cache.put(session_key, selection)
            if chosen:
                try:
                    await service.record_use([str(memory.id) for memory in chosen])
                except Exception:  # noqa: BLE001 - 用量刷新失败不能影响注入
                    logger.warning("刷新记忆用量失败", exc_info=True)
            self._log(ctx, selection, cache_hit=False)
        else:  # pragma: no branch - 分支只为日志/可读性
            self._log(ctx, selection, cache_hit=True)

        if not selection.section:
            return req
        profile = self._profile(ctx, req)
        if profile is None:
            return req
        put_segment(
            parts.system, Segment(MEMORY_SEGMENT, selection.section), SYSTEM_SEGMENT_ORDER
        )
        request = _layout_service(ctx).apply(ctx, req, parts, profile=profile)
        self._record_span(ctx, selection)
        return request

    # ------------------------------------------------------------------
    # 选择
    # ------------------------------------------------------------------
    async def _select(
        self,
        ctx: RunContext,
        active: Sequence[MemoryItem],
        query: str,
        budget: int,
    ) -> list[MemoryItem]:
        """先必选组（≤40% 预算），再按检索名次放相关组，token 超预算即停。"""
        total = estimate_text(MEMORY_HEADER)
        selected: list[MemoryItem] = []
        seen: set[str] = set()
        required_cap = int(budget * REQUIRED_BUDGET_RATIO)
        required_used = 0

        required = [
            memory
            for memory in active
            if str(getattr(memory, "kind", "")) == MemoryKind.SEMANTIC.value
            and str(getattr(memory, "subject", "") or "") in REQUIRED_SUBJECTS
        ]
        for memory in _sort_required(required):
            line = _line_for(memory)
            cost = estimate_text(line)
            if required_used + cost > required_cap:
                break
            if total + cost > budget:
                break
            selected.append(memory)
            seen.add(str(memory.id))
            required_used += cost
            total += cost

        by_id = {str(memory.id): memory for memory in active}
        for memory_id in await self._relevant_ids(ctx, query):
            if memory_id in seen:
                continue
            memory = by_id.get(memory_id)
            if memory is None:
                continue
            line = _line_for(memory)
            cost = estimate_text(line)
            if total + cost > budget:
                break
            selected.append(memory)
            seen.add(memory_id)
            total += cost
        return selected

    async def _relevant_ids(self, ctx: RunContext, query: str) -> list[str]:
        """检索 ``memory`` 来源，按融合名次返回记忆 id；检索失败只记日志。"""
        retriever = _retriever(ctx)
        if retriever is None or not query.strip():
            return []
        try:
            result = await retriever.search(_clip(query, MAX_QUERY_CHARS), sources=["memory"])
        except Exception:  # noqa: BLE001 - 检索失败只影响相关组
            logger.warning("记忆注入检索失败，只放必选组", exc_info=True)
            return []
        ids: list[str] = []
        for hit in getattr(result, "hits", ()) or ():
            memory_id = _memory_id_of(getattr(hit, "document_id", ""))
            if memory_id and memory_id not in ids:
                ids.append(memory_id)
            if len(ids) >= MAX_RELEVANT_HITS:
                break
        return ids

    # ------------------------------------------------------------------
    # 查询词
    # ------------------------------------------------------------------
    async def _query_for(self, ctx: RunContext) -> str:
        """Worker 取工单标题、委托与任务说明；总控取检查点摘要与最近三条用户消息。"""
        if self._session_factory is None:
            return ""
        try:
            if str(ctx.spec.kind) == "worker":
                return await self._worker_query(ctx)
            return await self._orchestrator_query(ctx)
        except Exception:  # noqa: BLE001 - 查询词读不到就只放必选组
            logger.warning("组装记忆检索词失败", exc_info=True)
            return ""

    async def _worker_query(self, ctx: RunContext) -> str:
        parts: list[str] = []
        async with self._session_factory() as session:
            if ctx.ticket_id:
                ticket = await TicketRepository(session).get(ctx.ticket_id)
                if ticket is not None:
                    parts.append(_clip(ticket.title, MAX_QUERY_MESSAGE_CHARS))
                mandate = await MandateRepository(session).get_by_ticket(ctx.ticket_id)
                if mandate is not None:
                    parts.append(_clip(mandate.scope, MAX_QUERY_MESSAGE_CHARS))
        latest = await self._recent_user_messages(ctx, limit=1)
        parts.extend(latest)
        return "\n".join(part for part in parts if part.strip())

    async def _orchestrator_query(self, ctx: RunContext) -> str:
        parts: list[str] = []
        async with self._session_factory() as session:
            if ctx.session_id:
                checkpoint = await CompactionCheckpointRepository(
                    session
                ).active_for_session(ctx.session_id)
                if checkpoint is not None:
                    parts.append(
                        _clip(str(checkpoint.summary or ""), MAX_QUERY_MESSAGE_CHARS)
                    )
        parts.extend(await self._recent_user_messages(ctx, limit=RECENT_USER_MESSAGES))
        return "\n".join(part for part in parts if part.strip())

    async def _recent_user_messages(self, ctx: RunContext, *, limit: int) -> list[str]:
        if not ctx.session_id:
            return []
        async with self._session_factory() as session:
            messages = await AgentMessageRepository(session).list_by_session(
                ctx.session_id
            )
        texts: list[str] = []
        for message in reversed(messages):
            if len(texts) >= limit:
                break
            if str(getattr(message, "role", "")) != "user":
                continue
            text = _clip(_content_text(getattr(message, "content", "")), MAX_QUERY_MESSAGE_CHARS)
            if text:
                texts.append(text)
        texts.reverse()
        return texts

    # ------------------------------------------------------------------
    # 装配依赖与观测
    # ------------------------------------------------------------------
    def _profile(self, ctx: RunContext, req: ModelRequest) -> Any:
        profile = req.profile
        if profile is None:
            model_state = ctx.state.get("model")
            if isinstance(model_state, Mapping):
                profile = model_state.get("profile")
        return profile

    def _cache_for(self, ctx: RunContext) -> MemoryInjectionCache:
        cache = getattr(_app_state(ctx), "memory_injection_cache", None)
        if isinstance(cache, MemoryInjectionCache):
            return cache
        cache = self._cache or self._local_cache
        return cache

    def _log(self, ctx: RunContext, selection: MemorySelection, *, cache_hit: bool) -> None:
        """``maa.memory.items`` / ``maa.memory.tokens`` 的结构化日志（暂无指标设施）。"""
        level = logging.DEBUG if cache_hit else logging.INFO
        logger.log(
            level,
            "memory.inject items=%d tokens=%d session=%s cache_hit=%s",
            selection.items,
            selection.tokens,
            ctx.session_id,
            cache_hit,
            extra={
                "event": "memory.inject",
                "maa.memory.items": selection.items,
                "maa.memory.tokens": selection.tokens,
                "session_id": ctx.session_id,
                "cache_hit": cache_hit,
            },
        )

    def _record_span(self, ctx: RunContext, selection: MemorySelection) -> None:
        """记 ``memory.inject`` span（10 §2.2、§2.5）；失败只记日志。"""
        try:
            recorder = getattr(_app_state(ctx), "span_recorder", None)
            if recorder is None or not bool(getattr(recorder, "enabled", False)):
                return
            parent = ctx.state.get("trace.run_span")
            handle = recorder.start_span(
                "memory.inject",
                SpanKind.MEMORY,
                trace_id=(
                    parent.trace_id
                    if isinstance(parent, SpanHandle)
                    else ctx.root_run_id
                ),
                run_id=ctx.run_id,
                parent=parent if isinstance(parent, SpanHandle) else None,
                attributes={
                    "maa.memory.items": selection.items,
                    "maa.memory.tokens": selection.tokens,
                },
            )
            handle.end()
        except Exception:  # noqa: BLE001 - 追踪失败不能影响注入
            logger.warning("记录 memory.inject span 失败", exc_info=True)
