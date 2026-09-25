"""``agent_audit`` 与 ``confirmation`` 两个仓储（docs/04 §5.9、§5.10、§9）。

``agent_audit`` 是 REST / 内置 Agent 调用方的共同审计落点，因此写入侧
有一条**不可下放给调用方**的硬约束：**入库前必须裁剪** ``arguments`` 与
``result_summary``（docs/04 §5.9）。截图工具的返回值是 base64 字符串，一张
1280×720 的 JPEG 编码后有几十万字符，原样落库会让审计表比日志表更早爆掉。
裁剪放在仓储内（:func:`truncate_arguments` / :func:`truncate_result_summary`
做成模块级纯函数，便于单独测试），三个调用方谁都不用记得这件事。

``confirmation`` 的两条状态机纪律同样在仓储层用 SQL 收口（docs/04 §5.10/§9）：

- **状态流转只允许 ``PENDING → APPROVED / REJECTED / EXPIRED``，终态不可再变。**
  :meth:`ConfirmationRepository.resolve` 用 ``UPDATE ... WHERE id=? AND
  status='pending'`` 的 ``rowcount`` 判定「状态机是否接受了这次流转」，返回
  ``False`` 由服务层转 ``409``；重复批准、批准已过期的记录都在数据库层被拒。
- :meth:`ConfirmationRepository.expire_overdue` 批量把 ``status='pending' AND
  expires_at < now`` 置 ``EXPIRED``，并返回**被置为过期的 id 列表**而不是条数：
  服务层要据此广播 WebSocket 事件并唤醒对应的阻塞调用（docs/04 §9）。

``confirmation.payload`` 存的是**待执行动作的完整参数，不是引用**（docs/04
§5.10），这样批准后不依赖发起方进程还活着。仓储原样存取，不做「按 id 回查」的
任何尝试 —— 服务重启后扫出 ``PENDING`` 记录一律置 ``EXPIRED`` 的能力就建立在
这一点上。

``agent_audit.confirmation_id`` 与 ``authorized_by_id`` 互斥（docs/04 §5.9）
是**写入方（M11 的 PolicyEngine）的纪律**，仓储两列原样存储、各自可查：拆成
两列正是为了让「这个授权窗口内一共执行了哪些操作」成为一次简单的
``WHERE authorized_by_id = ?`` 查询。仓储不替写入方改数据，也不因此拒绝写审计
（审计记录的是既成事实，不能因为策略记账有问题就丢记录）。

事务纪律（docs/04 §9）：本模块所有方法都**不 commit**，由调用方决定事务边界。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update

from maa_api.db.models import AgentAudit, Confirmation, utcnow
from maa_api.db.repositories.base import BaseRepository, Page
from maa_api.domain.enums import AuditStatus, CallerType, ConfirmationStatus, RiskLevel

# 单个字符串值的上限：超过 1 KB 的字符串值替换为 __truncated__ 结构（docs/04 §5.9）。
# 按字符数而不是 UTF-8 字节数计：裁剪的直接目的是不让几十万字符的 base64 落库，
# 记在 len 字段里的也正是 len() 本身（docs 的例子是 412300 个 base64 字符）。
MAX_ARGUMENT_STRING_CHARS = 1024

# result_summary 上限 2 KB（docs/04 §5.9）。TEXT 列不是 JSON，超长只能截断，
# 没有 __truncated__ 标记可放（加标记反而会把长度顶过上限）。
MAX_RESULT_SUMMARY_CHARS = 2048

# resolve 接受的终态：PENDING 不是合法目标（它没有「流转到 pending」这回事）。
RESOLVABLE_STATUSES = (
    ConfirmationStatus.APPROVED,
    ConfirmationStatus.REJECTED,
    ConfirmationStatus.EXPIRED,
)


def truncate_long_strings(
    value: Any, *, max_chars: int = MAX_ARGUMENT_STRING_CHARS
) -> Any:
    """递归裁剪 JSON 值里超长的字符串，返回**新对象**，不修改入参。

    规则（docs/04 §5.9）：任何超过 ``max_chars`` 的字符串值替换为
    ``{"__truncated__": true, "len": n}``；嵌套的 dict / list 逐层处理，
    非字符串标量原样返回。``ref``（如 ``"screenshot_id"``）是可选的上下文信息，
    通用递归拿不到它，故不写这个键 —— 需要 ref 的调用方应先用 ``screenshot_id``
    替换掉 base64 再交给仓储。

    入参是 ``None`` 或空容器时也安全：``arguments`` 是 NOT NULL 列，``None``
    会在 flush 时被数据库拒绝，这里不做类型校验（那是调用方的契约）。
    """
    if isinstance(value, str):
        if len(value) > max_chars:
            return {"__truncated__": True, "len": len(value)}
        return value
    if isinstance(value, dict):
        return {
            key: truncate_long_strings(item, max_chars=max_chars)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [truncate_long_strings(item, max_chars=max_chars) for item in value]
    return value


def truncate_result_summary(
    text: str | None, *, max_chars: int = MAX_RESULT_SUMMARY_CHARS
) -> str | None:
    """把 ``result_summary`` 截断到 ``max_chars`` 字符；``None`` 原样返回。

    只做截断、不加省略号标记：列是 TEXT，标记会占掉正文额度甚至把长度顶过
    2 KB 上限；调用方若需要知道被截断，应自己把标记写进摘要正文。
    """
    if text is None:
        return None
    return text[:max_chars]


class AuditRepository(BaseRepository):
    """全量工具调用审计的写入与查询（docs/04 §5.9）。"""

    async def create(self, audit: AgentAudit) -> AgentAudit:
        """写入一条审计，返回受会话管理的实例（只 ``flush`` 不 ``commit``）。

        在仓储内完成 §5.9 的裁剪：``arguments`` 递归替换超长字符串、
        ``result_summary`` 截断到 2 KB。用 ``model_copy()`` 生成裁剪后的副本再
        ``merge()``，**不修改入参**：调用方可能还要拿原始参数去执行/回显，
        审计的裁剪不该有这种副作用。

        ``merge()``（而不是 ``add()``）的理由同 ``PipelineRepository.create``
        （M2-07 实测）：入参保持 transient、字段永远可读。
        """
        sanitized = audit.model_copy(
            update={
                "arguments": truncate_long_strings(audit.arguments),
                "result_summary": truncate_result_summary(audit.result_summary),
            }
        )
        stored = await self.session.merge(sanitized)
        await self.session.flush()
        return stored

    async def get(self, audit_id: int) -> AgentAudit | None:
        """按主键取一条审计；不存在返回 ``None``。

        审计是只追加表，没有 UPDATE 路径，所以这里不需要 ``populate_existing``。
        """
        return await self.session.get(AgentAudit, audit_id)

    async def set_terminal(
        self,
        audit_id: int,
        status: AuditStatus | str,
        *,
        result_summary: str | None = None,
        result_ref: dict[str, Any] | None = None,
        error_code: str | None = None,
        duration_ms: int | None = None,
        confirmation_id: str | None = None,
        authorized_by_id: str | None = None,
    ) -> bool:
        """Transition a pending call audit once after its async work resolves.

        Tool invocations can remain pending through human approval. This is the
        only permitted update path for `agent_audit`; terminal rows stay immutable.
        """
        target = AuditStatus(status)
        if target is AuditStatus.PENDING:
            return False
        values: dict[str, Any] = {
            "status": target,
            "result_summary": truncate_result_summary(result_summary),
            "result_ref": result_ref,
            "error_code": error_code,
        }
        if duration_ms is not None:
            values["duration_ms"] = max(int(duration_ms), 0)
        if confirmation_id is not None or authorized_by_id is not None:
            values["confirmation_id"] = confirmation_id
            values["authorized_by_id"] = authorized_by_id
        result = await self.session.execute(
            update(AgentAudit)
            .where(AgentAudit.id == audit_id, AgentAudit.status == AuditStatus.PENDING)
            .values(**values)
        )
        return result.rowcount > 0

    async def list(
        self,
        *,
        caller: CallerType | str | None = None,
        tool_name: str | None = None,
        status: AuditStatus | str | None = None,
        risk_level: RiskLevel | str | None = None,
        since: datetime | None = None,
        page: int = 1,
        size: int = 20,
    ) -> Page[AgentAudit]:
        """审计分页：可选 ``caller`` / ``tool_name`` 过滤，按 ``created_at DESC``。

        排序与两个过滤条件刻意贴着 ``ix_agent_audit_caller_created_at`` /
        ``ix_agent_audit_tool_name_created_at``（docs/04 §6 的审计页形态）来写，
        不额外发明查询形态；同秒并列用 ``id DESC`` 兜稳定次序。
        """
        conditions: list[Any] = []
        if caller is not None:
            conditions.append(AgentAudit.caller == caller)
        if tool_name is not None:
            conditions.append(AgentAudit.tool_name == tool_name)
        if status is not None:
            conditions.append(AgentAudit.status == AuditStatus(status))
        if risk_level is not None:
            conditions.append(AgentAudit.risk_level == RiskLevel(risk_level))
        if since is not None:
            conditions.append(AgentAudit.created_at >= since)

        items_stmt = (
            select(AgentAudit)
            .where(*conditions)
            .order_by(AgentAudit.created_at.desc(), AgentAudit.id.desc())
        )
        count_stmt = select(func.count()).select_from(AgentAudit).where(*conditions)
        return await self.paginate(items_stmt, count_stmt, page=page, size=size)


class ConfirmationRepository(BaseRepository):
    """人工确认请求的写入、待办列表与终态流转（docs/04 §5.10）。"""

    async def create(self, confirmation: Confirmation) -> Confirmation:
        """写入一条确认请求，返回受会话管理的实例（只 ``flush`` 不 ``commit``）。

        ``payload`` 原样落库：它存的是待执行动作的完整参数而不是引用，批准后
        不依赖发起方进程还活着（docs/04 §5.10）。仓储不校验也不解析它。
        """
        stored = await self.session.merge(confirmation)
        await self.session.flush()
        return stored

    async def get(self, confirmation_id: str) -> Confirmation | None:
        """按主键取一条确认；不存在返回 ``None``。

        ``populate_existing=True``：:meth:`resolve` / :meth:`expire_overdue` 都走
        Core ``UPDATE``（``expire_overdue`` 为批量还显式关了
        ``synchronize_session``），不刷新会返回身份映射里的陈旧实例（M2-08 实测）。
        """
        return await self.session.get(
            Confirmation, confirmation_id, populate_existing=True
        )

    async def list_pending(self) -> list[Confirmation]:
        """全部待确认记录，按 ``expires_at`` 升序（最快过期的排前面）。

        复用 ``ix_confirmation_status_expires_at(status, expires_at)``（docs/04
        §6 的待办卡片形态）；同刻并列用 ``created_at, id`` 兜稳定次序。
        """
        result = await self.session.execute(
            select(Confirmation)
            .where(Confirmation.status == ConfirmationStatus.PENDING)
            .order_by(
                Confirmation.expires_at.asc(),
                Confirmation.created_at.asc(),
                Confirmation.id.asc(),
            )
        )
        return list(result.scalars().all())

    async def list(
        self,
        *,
        status: ConfirmationStatus | str | None = ConfirmationStatus.PENDING,
        page: int = 1,
        size: int = 20,
    ) -> Page[Confirmation]:
        conditions = [] if status is None else [Confirmation.status == ConfirmationStatus(status)]
        items_stmt = (
            select(Confirmation)
            .where(*conditions)
            .order_by(Confirmation.created_at.desc(), Confirmation.id.desc())
        )
        count_stmt = select(func.count()).select_from(Confirmation).where(*conditions)
        return await self.paginate(items_stmt, count_stmt, page=page, size=size)

    async def resolve(
        self,
        confirmation_id: str,
        status: ConfirmationStatus,
        *,
        resolved_by: str,
        reason: str | None = None,
        resolved_at: datetime | None = None,
    ) -> bool:
        """把待确认记录置入终态，返回状态机是否接受了这次流转（docs/04 §9）。

        ``WHERE status='pending'`` 在数据库层保证「终态不可再变」：重复批准、
        处理一条已 ``EXPIRED`` 的记录都拿到 ``rowcount == 0`` → ``False``，由
        服务层转 ``409``。``status`` 只接受三个终态，传 ``PENDING`` 直接
        ``False``；传枚举外的值抛 ``ValueError``（与 ``mark_terminal`` 一致）。
        ``reason`` 写入 ``resolved_reason``（拒绝原因等），``resolved_at`` 取
        本次流转时刻。
        """
        target = ConfirmationStatus(status)
        if target not in RESOLVABLE_STATUSES:
            return False

        result = await self.session.execute(
            update(Confirmation)
            .where(
                Confirmation.id == confirmation_id,
                Confirmation.status == ConfirmationStatus.PENDING,
            )
            .values(
                status=target,
                resolved_by=resolved_by,
                resolved_reason=reason,
                resolved_at=resolved_at or utcnow(),
            )
        )
        return result.rowcount > 0

    async def expire_overdue(self, now: datetime) -> list[str]:
        """把 ``expires_at < now`` 的待确认记录批量置 ``EXPIRED``，返回其 id 列表。

        返回值是 **id 列表而不是条数**（docs/04 §9）：服务层要据此广播 WebSocket
        事件并唤醒对应的阻塞调用。用 ``UPDATE ... RETURNING id`` 一条语句同时完成
        「置终态」与「取回 id」：先 SELECT 再 UPDATE 会有并发窗口，可能广播一个
        实际没被翻转的 id。``resolved_by`` 记 ``system``（docs/04 §5.10：超时由
        扫描任务处理），``resolved_at`` 用传入的 ``now``（扫描任务的判定时刻，
        不是每条各自的 ``expires_at``）。

        ``synchronize_session=False`` 让批量更新不逐个同步身份映射（M2-08 纪律）；
        同一会话里要读这些记录的**真值**请走 :meth:`get`（它刷新），不要直接读
        手里的旧实例。
        """
        result = await self.session.execute(
            update(Confirmation)
            .where(
                Confirmation.status == ConfirmationStatus.PENDING,
                Confirmation.expires_at < now,
            )
            .values(
                status=ConfirmationStatus.EXPIRED,
                resolved_by="system",
                resolved_at=now,
            )
            .returning(Confirmation.id),
            execution_options={"synchronize_session": False},
        )
        return list(result.scalars().all())

    async def expire_all_pending(self, now: datetime) -> list[str]:
        """Expire every pending confirmation for process-restart recovery."""
        result = await self.session.execute(
            update(Confirmation)
            .where(Confirmation.status == ConfirmationStatus.PENDING)
            .values(
                status=ConfirmationStatus.EXPIRED,
                resolved_by="system",
                resolved_reason="服务重启，未完成的确认已失效",
                resolved_at=now,
            )
            .returning(Confirmation.id),
            execution_options={"synchronize_session": False},
        )
        return list(result.scalars().all())

    async def reject_pending_grants_for_session(
        self, session_id: str, *, resolved_by: str, reason: str, now: datetime
    ) -> list[Confirmation]:
        """Reject outstanding grant confirmations for one internal session."""
        rows = (
            await self.session.execute(
                select(Confirmation)
                .where(
                    Confirmation.status == ConfirmationStatus.PENDING,
                    Confirmation.action == "grant_atomic_ops",
                )
                .order_by(Confirmation.created_at.asc(), Confirmation.id.asc())
            )
        ).scalars().all()
        resolved: list[Confirmation] = []
        for row in rows:
            if row.payload.get("session_id") != session_id:
                continue
            if await self.resolve(
                row.id,
                ConfirmationStatus.REJECTED,
                resolved_by=resolved_by,
                reason=reason,
                resolved_at=now,
            ):
                resolved.append(row)
        return resolved
