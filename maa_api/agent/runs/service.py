"""运行的建立、心跳、结束与按树查询。

``agent_run`` 的状态机（docs/v3/specs/m66-durable-runs §4.5.1）：``start`` 落
``running``；等待确认/提问时切 ``waiting_human``，恢复执行再切回 ``running``；
``finish``（或重启恢复）写终态。同一会话至多一条活动运行，由部分唯一索引
``uq_agent_run_active_session`` 最终保证——:meth:`RunService.start` 撞上它时先
判断占着的那条是否已经失联（§4.5.3），失联就收尾并重试一次。

两条时钟约定：``clock`` 与 ``sleep`` 都由构造参数注入，测试可以拨表、也可以
精确控制心跳循环的每一步；生产默认用 :func:`maa_api.db.models.utcnow` 与
``asyncio.sleep``。
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from maa_api.agent.redaction import redact_remote_data
from maa_api.agent.runs.recovery import RunRecovery
from maa_api.db.models import AgentRun, new_uuid, utcnow
from maa_api.db.repositories.agent import (
    TERMINAL_RUN_STATUSES,
    AgentRunRepository,
    AgentSessionRepository,
)
from maa_api.db.repositories.base import Page
from maa_api.domain.enums import AgentRunKind, AgentRunStatus
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import Settings, get_settings

logger = logging.getLogger(__name__)

#: ``error_message`` 落库前的长度上限（字符）。
ERROR_MESSAGE_MAX_CHARS = 2000
#: 失联阈值至少是心跳间隔的这么多倍：配置把两者设得太近时，不至于把还活着的运行当成失联。
STALE_HEARTBEAT_FACTOR = 3


@dataclass
class RunNode:
    """运行树的一个节点：一行 ``agent_run`` 加它的直接子运行。"""

    run: AgentRun
    children: list[RunNode] = field(default_factory=list)


class RunService:
    """运行生命周期的唯一入口：建立、心跳、状态切换、结束与查询。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        boot_id: str,
        settings_provider: Callable[[], Settings] = get_settings,
        clock: Callable[[], datetime] = utcnow,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        """``boot_id`` 标识本进程；``_live`` 是本进程正在跟踪的 ``run_id -> session_id``。

        ``_live`` 只用于两件事：心跳刷哪些行、以及判断「本进程的运行但没人收尾」
        的孤儿。它不参与跨进程判定——那是 ``boot_id`` + ``heartbeat_at`` 的事。
        """
        self.session_factory = session_factory
        self.boot_id = boot_id
        self.recovery = RunRecovery(session_factory, boot_id=boot_id, clock=clock)
        self._settings_provider = settings_provider
        self._clock = clock
        self._sleep = sleep
        self._live: dict[str, str | None] = {}
        self._heartbeat_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    async def start(
        self,
        *,
        agent_name: str,
        kind: AgentRunKind,
        session_id: str | None = None,
        parent_run_id: str | None = None,
        run_id: str | None = None,
        spec_version: str | None = None,
        trigger_seq: int | None = None,
        ticket_id: str | None = None,
        model: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> AgentRun:
        """建一条 ``running`` 运行并纳入心跳，返回已提交的行。

        - ``run_id`` 为空时生成；给了就用它（M65 的 ``RunContext`` 可能已经有 id）。
        - ``parent_run_id`` 不存在：``RUN_NOT_FOUND``；``root_run_id`` 取父运行的
          ``root_run_id``，没有父运行时等于自身 id。
        - 会话上已有活动运行：先按 §4.5.3 判断它是否已失效，失效就收尾并重试一次；
          仍然冲突则 ``RUN_ALREADY_ACTIVE``（``details`` 带占着的那条）。

        ``_live`` **在插入之前**登记：并发的 ``active_run()`` 看到这条会话时，
        不能把本进程正要插入的运行当成孤儿收尾掉。
        """
        now = self._clock()
        run_id = run_id or new_uuid()
        root_run_id = run_id
        if parent_run_id is not None:
            async with self.session_factory() as session:
                parent = await AgentRunRepository(session).get(parent_run_id)
            if parent is None:
                raise AppError(
                    ErrorCode.RUN_NOT_FOUND,
                    "父运行不存在",
                    {"run_id": parent_run_id},
                )
            root_run_id = parent.root_run_id
        self._live[run_id] = session_id
        stored: AgentRun | None = None
        for attempt in (1, 2):
            try:
                async with self.session_factory() as session:
                    stored = await AgentRunRepository(session).create(
                        AgentRun(
                            id=run_id,
                            session_id=session_id,
                            parent_run_id=parent_run_id,
                            root_run_id=root_run_id,
                            agent_name=agent_name,
                            spec_version=spec_version,
                            kind=kind,
                            status=AgentRunStatus.RUNNING,
                            trigger_seq=trigger_seq,
                            ticket_id=ticket_id,
                            model=model,
                            meta=meta,
                            boot_id=self.boot_id,
                            heartbeat_at=now,
                            started_at=now,
                        )
                    )
                    await session.commit()
                break
            except IntegrityError:
                blocking = None
                if session_id is not None:
                    async with self.session_factory() as session:
                        blocking = await AgentRunRepository(
                            session
                        ).get_active_for_session(session_id)
                if blocking is None:
                    # 不是活动运行冲突（例如会话不存在导致外键失败），原样抛。
                    self._live.pop(run_id, None)
                    raise
                if attempt == 1 and await self._reap_if_dead(blocking):
                    continue
                self._live.pop(run_id, None)
                raise AppError(
                    ErrorCode.RUN_ALREADY_ACTIVE,
                    "该会话已有运行在进行",
                    {"session_id": session_id, "run_id": blocking.id},
                ) from None
        if stored is None:  # pragma: no cover - 循环要么 break 要么 raise
            raise AssertionError("unreachable")
        self._ensure_heartbeat_loop()
        return stored

    async def set_status(self, run_id: str, status: AgentRunStatus) -> bool:
        """只接受 ``RUNNING`` 与 ``WAITING_HUMAN``；运行已结束或不存在时返回 ``False``。"""
        if status not in (AgentRunStatus.RUNNING, AgentRunStatus.WAITING_HUMAN):
            raise ValueError(f"不是活动态: {status!r}")
        async with self.session_factory() as session:
            changed = await AgentRunRepository(session).set_status_if_active(
                run_id, str(status)
            )
            await session.commit()
        return changed

    async def finish(
        self,
        run_id: str,
        *,
        status: AgentRunStatus,
        finish_reason: str,
        error_code: str | None = None,
        error_message: str | None = None,
        steps: int | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> AgentRun:
        """写终态，返回该行。

        - ``status`` 必须是终态，否则 ``ValueError``；运行不存在 ``RUN_NOT_FOUND``。
        - **幂等**：已是终态时原样返回，不改任何列（收尾路径可能重复调用）。
        - 三个计数为 ``None`` 时用 :meth:`AgentRunRepository.usage_totals` 汇总。
        - 有 ``session_id`` 时把 ``model`` 刷成会话当前的 model（诊断会话的 model
          可能在本轮里从 ``unconfigured`` 改成真实模型）。
        - ``error_message`` 先过 :func:`redact_remote_data` 再截到
          :data:`ERROR_MESSAGE_MAX_CHARS`。
        - ``finished_at = heartbeat_at = clock()``。
        - 不论成功与否，最后都把 ``run_id`` 移出 ``self._live``（``finally``）：
          写库失败后这条运行在本进程里就是孤儿，下一次 ``active_run`` 会发现并收尾。
        """
        if status not in TERMINAL_RUN_STATUSES:
            raise ValueError(f"不是终态: {status!r}")
        try:
            async with self.session_factory() as session:
                runs = AgentRunRepository(session)
                run = await runs.get(run_id)
                if run is None:
                    raise AppError(
                        ErrorCode.RUN_NOT_FOUND,
                        "没有这个运行",
                        {"run_id": run_id},
                    )
                if run.status in TERMINAL_RUN_STATUSES:
                    return run
                if steps is None or prompt_tokens is None or completion_tokens is None:
                    totals = await runs.usage_totals(run_id)
                    if prompt_tokens is None:
                        prompt_tokens = totals[0]
                    if completion_tokens is None:
                        completion_tokens = totals[1]
                    if steps is None:
                        steps = totals[2]
                now = self._clock()
                run.status = status
                run.finish_reason = finish_reason
                run.error_code = error_code
                if error_message is not None:
                    redacted = redact_remote_data(error_message)
                    run.error_message = str(redacted)[:ERROR_MESSAGE_MAX_CHARS]
                run.steps = steps
                run.prompt_tokens = prompt_tokens
                run.completion_tokens = completion_tokens
                run.finished_at = now
                run.heartbeat_at = now
                if run.session_id is not None:
                    session_row = await AgentSessionRepository(session).get(
                        run.session_id
                    )
                    if session_row is not None:
                        run.model = session_row.model
                await session.commit()
                return run
        finally:
            self._live.pop(run_id, None)

    # ------------------------------------------------------------------
    # 心跳与失联
    # ------------------------------------------------------------------
    async def heartbeat(self, run_ids: Collection[str] | None = None) -> int:
        """把给定运行（缺省为 ``self._live`` 里的全部）的心跳刷成 ``clock()``。

        只改活动态的行，返回改动行数——已经结束的行留在 ``_live`` 里（例如
        ``finish`` 还没执行到）时不会被误刷。
        """
        ids = list(self._live) if run_ids is None else [str(item) for item in run_ids]
        if not ids:
            return 0
        now = self._clock()
        async with self.session_factory() as session:
            changed = await AgentRunRepository(session).touch_heartbeat(ids, now)
            await session.commit()
        return changed

    def is_live(self, run_id: str) -> bool:
        """本进程是否还在跟踪这条运行（进程内快路径，不查库）。"""
        return run_id in self._live

    def stale_after_seconds(self) -> int:
        """``max(agent.run_stale_seconds, STALE_HEARTBEAT_FACTOR * agent.run_heartbeat_seconds)``。"""
        settings = self._settings_provider()
        return max(
            settings.agent.run_stale_seconds,
            STALE_HEARTBEAT_FACTOR * settings.agent.run_heartbeat_seconds,
        )

    async def active_run(self, session_id: str) -> AgentRun | None:
        """该会话当前的活动运行；它已失效（§4.5.3）时先收尾，返回 ``None``。"""
        async with self.session_factory() as session:
            run = await AgentRunRepository(session).get_active_for_session(session_id)
        if run is None:
            return None
        if await self._reap_if_dead(run):
            return None
        return run

    async def _reap_if_dead(self, run: AgentRun) -> bool:
        """收尾一条已经不可能再继续的活动运行；返回是否收了尾。

        两种失联（§4.5.3）：

        - 本进程的行但不在 ``_live``：``finish`` 写库失败后被移出跟踪的孤儿，
          以 ``failed / error / INTERNAL_ERROR`` 收尾。
        - 别的进程的行且心跳早于 ``stale_after_seconds()``：上一进程留下的，
          交给 :meth:`RunRecovery.recover_run`，与启动恢复同样收尾（补桩 + 恢复提示）。
          心跳还新鲜的视为另一个进程在用，返回 ``False``。
        """
        if run.boot_id == self.boot_id:
            if run.id in self._live:
                return False
            await self.finish(
                run.id,
                status=AgentRunStatus.FAILED,
                finish_reason="error",
                error_code=str(ErrorCode.INTERNAL_ERROR),
                error_message="运行没有正常结束，已在会话下次使用前收尾",
            )
            return True
        last_heartbeat = run.heartbeat_at or run.started_at
        if self._clock() - last_heartbeat < timedelta(seconds=self.stale_after_seconds()):
            return False
        return await self.recovery.recover_run(run.id)

    def _ensure_heartbeat_loop(self) -> None:
        """任务为 ``None`` 或已结束时重建；已有一个在跑就复用它。"""
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(), name="agent-run-heartbeat"
            )

    async def _heartbeat_loop(self) -> None:
        """每 ``run_heartbeat_seconds`` 刷一次 ``_live``；没有活动运行时退出。

        ``while`` 判断与退出之间没有 ``await``：最后一条运行结束时新运行刚登记，
        要么任务还在跑（下一次循环会看到它），要么任务已结束（``start`` 会重建）。
        """
        while self._live:
            await self._sleep(self._settings_provider().agent.run_heartbeat_seconds)
            if not self._live:
                break
            try:
                await self.heartbeat()
            except Exception:  # noqa: BLE001 - 心跳失败不能让循环死掉
                logger.warning("运行心跳写库失败", exc_info=True)
        self._heartbeat_task = None

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    async def get(self, run_id: str) -> AgentRun | None:
        """按主键取运行；不存在返回 ``None``。"""
        async with self.session_factory() as session:
            return await AgentRunRepository(session).get(run_id)

    async def get_tree(self, run_id: str, *, max_depth: int = 3) -> RunNode:
        """以 ``run_id`` 为根的子树。

        一次 ``list_tree(root_run_id)`` 取同一棵树的全部运行，按 ``parent_run_id``
        在内存里挂接：深度 0 是 ``run_id`` 本身，深度 ``max_depth`` 的节点不再展开。
        ``run_id`` 不存在时抛 ``RUN_NOT_FOUND``。
        """
        root = await self.get(run_id)
        if root is None:
            raise AppError(
                ErrorCode.RUN_NOT_FOUND, "没有这个运行", {"run_id": run_id}
            )
        async with self.session_factory() as session:
            rows = await AgentRunRepository(session).list_tree(root.root_run_id)
        nodes = {row.id: RunNode(run=row) for row in rows}
        nodes.setdefault(root.id, RunNode(run=root))
        children_of: dict[str, list[AgentRun]] = defaultdict(list)
        for row in rows:
            if row.parent_run_id is not None and row.parent_run_id in nodes:
                children_of[row.parent_run_id].append(row)

        def attach(node: RunNode, depth: int) -> None:
            if depth >= max_depth:
                return
            for child_run in children_of.get(node.run.id, []):
                child = RunNode(run=child_run)
                node.children.append(child)
                attach(child, depth + 1)

        tree = nodes[root.id]
        attach(tree, 0)
        return tree

    async def list_runs(
        self,
        *,
        session_id: str | None = None,
        root_only: bool = True,
        page: int = 1,
        size: int = 20,
    ) -> Page[AgentRun]:
        """运行分页查询：可按会话过滤、只看根节点，按 ``started_at`` 倒序。"""
        async with self.session_factory() as session:
            return await AgentRunRepository(session).list_page(
                session_id=session_id,
                root_only=root_only,
                page=page,
                size=size,
            )

    async def close(self) -> None:
        """取消心跳任务并等它退出，清空 ``self._live``；不改库里的运行。"""
        task = self._heartbeat_task
        self._heartbeat_task = None
        if task is not None:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._live.clear()
