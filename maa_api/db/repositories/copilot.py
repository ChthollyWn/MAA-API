"""M29 作业授权与尝试记录的仓储（规格 §9.6、§9.7、附录 E、docs/04 §9）。

两个仓储、两种存储：

- :class:`CopilotGrantRepository` 读写 ``copilot_grant`` 表（M29 迁移 0017）。它只回答
  「怎么读写」：「哪个状态算终态、什么时候退还次数、什么时候标 expired」都在
  :class:`~maa_api.services.copilot.grant.CopilotGrantService` 里。
- :class:`CopilotAttemptRepository` 读写**作业资源 ``meta["attempts"]``**（规格 §9.5
  规定的落点），不是独立表：附录 E 的 ``copilot_attempt`` 是建议形态，本里程碑把每次
  尝试的 id / 结果 / 判定方 / 时间记进作业资源的元数据（``CopilotDraftService`` 建资源
  时已经把 ``attempts`` 初始化成空数组），前端「作业」页与 ``record_copilot_result``
  都从这里读。整表（JSON 列）整体赋值，不做原地修改（docs/04 §7）。

两条纪律与其它仓储一致：不 ``commit()``（事务边界由服务层决定），不返回 ``Row`` /
元组（尝试记录返回 :class:`AttemptRecord` 这个轻量 dataclass）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy import func

from maa_api.db.models import CopilotGrant, ResourceAsset, utcnow
from maa_api.db.repositories.base import BaseRepository
from maa_api.domain.enums import ResourceAssetKind

__all__ = [
    "ATTEMPTS_META_KEY",
    "AttemptRecord",
    "CopilotAttemptRepository",
    "CopilotGrantRepository",
    "GRANT_TERMINAL_STATUSES",
]

#: 作业资源 ``meta`` 里放尝试记录的键（规格 §9.5）。
ATTEMPTS_META_KEY = "attempts"

#: 授权的终态（规格 §9.6）：进入终态后不再消耗次数，也不再出现在「有效授权」里。
GRANT_TERMINAL_STATUSES = frozenset(
    {"succeeded", "exhausted", "stopped", "expired", "revoked", "interrupted"}
)

#: ``CopilotGrantRepository.update`` 允许写的列。``id`` / ``created_at`` 是身份列，
#: 改它等于换一行；未知列名多半是调用方拼错，静默忽略会写出「看起来成功了但没更新」
#: 的假象（与 ``ResourceAssetRepository.upsert_by_kind_name`` 同一口径）。
_GRANT_WRITABLE_FIELDS = frozenset(
    {
        "session_id",
        "stage_id",
        "stage_code",
        "status",
        "attempts",
        "used",
        "success",
        "support",
        "expires_at",
        "finished_at",
    }
)

#: ``CopilotAttemptRepository.update`` 允许改的尝试字段。
_ATTEMPT_WRITABLE_FIELDS = frozenset(
    {
        "outcome",
        "judged_by",
        "counted",
        "screenshot_id",
        "evidence",
        "pipeline_id",
        "recognition_pipeline_id",
        "finished_at",
        "support",
        "stage_id",
        "grant_id",
        "note",
    }
)


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """一条尝试记录 + 它属于哪份作业资源。

    ``data`` 与 ``meta["attempts"]`` 里的对象一一对应（id / copilot_id / grant_id /
    pipeline_id / outcome / judged_by / counted / screenshot_id / evidence /
    started_at / finished_at / attempt_number）。``asset_id`` 单独给出来：
    ``record_copilot_result`` 拿到 ``attempt_id`` 之后要能写回同一份资源。
    """

    asset_id: str
    data: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.data.get("id") or "")


class CopilotGrantRepository(BaseRepository):
    """``copilot_grant`` 表的读写（不 commit、不做状态机判定）。"""

    async def create(self, grant: CopilotGrant) -> CopilotGrant:
        """插入一条授权并 flush。

        ``id`` 由调用方给（= 那条确认的 id，附录 E），重复主键抛 ``IntegrityError``：
        批准接口重放时应当先 :meth:`get` 判存在，而不是吞掉冲突。
        """
        self.session.add(grant)
        await self.session.flush()
        return grant

    async def get(self, grant_id: str) -> CopilotGrant | None:
        """按主键取一条授权，没有返回 ``None``。"""
        return await self.session.get(CopilotGrant, grant_id)

    async def active_for_session(self, session_id: str) -> CopilotGrant | None:
        """某个会话当前的 ``active`` 授权（部分唯一索引保证至多一条）。"""
        if not session_id:
            return None
        return await self.session.scalar(
            select(CopilotGrant)
            .where(
                CopilotGrant.session_id == session_id,
                CopilotGrant.status == "active",
            )
            .order_by(CopilotGrant.created_at.desc())
            .limit(1)
        )

    async def list_active(
        self, *, session_id: str | None = None, now: datetime | None = None
    ) -> list[CopilotGrant]:
        """``status='active'`` 的授权；给 ``session_id`` 时限定会话，给 ``now`` 时滤掉已过期。

        ``now`` 是**判定用**参数：调用方（服务层）决定「过期」意味着什么，仓储只做
        ``expires_at > now`` 的比较。
        """
        conditions = [CopilotGrant.status == "active"]
        if session_id is not None:
            conditions.append(CopilotGrant.session_id == session_id)
        if now is not None:
            conditions.append(CopilotGrant.expires_at > now)
        result = await self.session.execute(
            select(CopilotGrant)
            .where(*conditions)
            .order_by(CopilotGrant.created_at.desc())
        )
        return list(result.scalars().all())

    async def all_active(self) -> list[CopilotGrant]:
        """全表 ``status='active'``（不看有效期）：服务重启时标 ``interrupted`` 用。"""
        result = await self.session.execute(
            select(CopilotGrant)
            .where(CopilotGrant.status == "active")
            .order_by(CopilotGrant.created_at.asc())
        )
        return list(result.scalars().all())

    async def by_session(self, session_id: str, *, limit: int = 20) -> list[CopilotGrant]:
        """某个会话的授权历史，新的在前（页面授权区与排障用）。"""
        result = await self.session.execute(
            select(CopilotGrant)
            .where(CopilotGrant.session_id == session_id)
            .order_by(CopilotGrant.created_at.desc(), CopilotGrant.id.desc())
            .limit(max(int(limit), 1))
        )
        return list(result.scalars().all())

    async def update(
        self, grant_id: str, **changes: Any
    ) -> CopilotGrant | None:
        """改一条授权的若干列并刷新 ``updated_at``；不存在返回 ``None``。

        只改 ``changes`` 里给出的列。``finished_at`` 允许显式传 ``None``（把行从终态
        拉回 ``active`` 时用，例如「退还次数后重新开始」），因此这里逐列判断「键在不在」
        而不是「值是不是 None」。
        """
        unknown = set(changes) - _GRANT_WRITABLE_FIELDS - {"updated_at"}
        if unknown:
            raise ValueError(f"copilot_grant 不接受这些字段：{sorted(unknown)}")
        values = dict(changes)
        values["updated_at"] = utcnow()
        await self.session.execute(
            update(CopilotGrant).where(CopilotGrant.id == grant_id).values(**values)
        )
        await self.session.flush()
        return await self.get(grant_id)

    async def count_by_status(self) -> dict[str, int]:
        """按状态计数（状态条与冒烟自检用；不参与状态机）。"""
        result = await self.session.execute(
            select(CopilotGrant.status, func.count()).group_by(CopilotGrant.status)
        )
        return {str(status): int(count) for status, count in result.all()}


class CopilotAttemptRepository(BaseRepository):
    """作业尝试记录：存在 ``kind=copilot`` 资源的 ``meta["attempts"]`` 里（规格 §9.5）。"""

    async def _asset(self, asset_id: str) -> ResourceAsset | None:
        return await self.session.get(
            ResourceAsset, asset_id, populate_existing=True
        )

    @staticmethod
    def _meta(row: ResourceAsset) -> dict[str, Any]:
        meta = row.meta if isinstance(row.meta, Mapping) else {}
        return {str(key): value for key, value in meta.items()}

    @staticmethod
    def _attempts(meta: Mapping[str, Any]) -> list[dict[str, Any]]:
        rows = meta.get(ATTEMPTS_META_KEY)
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            return []
        return [dict(item) for item in rows if isinstance(item, Mapping)]

    async def _write_attempts(
        self, asset_id: str, meta: Mapping[str, Any], attempts: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """整列赋值写回 ``meta``（JSON 列的原地修改不被 ORM 追踪，docs/04 §7）。"""
        payload = {**meta, ATTEMPTS_META_KEY: attempts}
        await self.session.execute(
            update(ResourceAsset)
            .where(ResourceAsset.id == asset_id)
            .values(meta=payload, updated_at=utcnow())
        )
        await self.session.flush()
        return attempts

    async def append(
        self, asset_id: str, attempt: Mapping[str, Any]
    ) -> AttemptRecord:
        """追加一条尝试记录；资源不存在抛 ``LookupError``（调用方翻成业务错误）。"""
        row = await self._asset(asset_id)
        if row is None or str(row.kind) != ResourceAssetKind.COPILOT.value:
            raise LookupError(f"作业资源不存在：{asset_id}")
        meta = self._meta(row)
        attempts = self._attempts(meta)
        attempts.append(dict(attempt))
        await self._write_attempts(asset_id, meta, attempts)
        return AttemptRecord(asset_id=asset_id, data=dict(attempts[-1]))

    async def update(
        self, asset_id: str, attempt_id: str, **changes: Any
    ) -> AttemptRecord | None:
        """改一条尝试记录；找不到返回 ``None``。``id`` 不可改。"""
        unknown = set(changes) - _ATTEMPT_WRITABLE_FIELDS
        if unknown:
            raise ValueError(f"作业尝试不接受这些字段：{sorted(unknown)}")
        row = await self._asset(asset_id)
        if row is None:
            return None
        meta = self._meta(row)
        attempts = self._attempts(meta)
        for index, item in enumerate(attempts):
            if str(item.get("id")) != attempt_id:
                continue
            updated = {**item, **changes}
            attempts[index] = updated
            await self._write_attempts(asset_id, meta, attempts)
            return AttemptRecord(asset_id=asset_id, data=updated)
        return None

    async def list_for_asset(self, asset_id: str) -> list[dict[str, Any]]:
        """一份作业的全部尝试，写入顺序（时间序）。"""
        row = await self._asset(asset_id)
        if row is None:
            return []
        return self._attempts(self._meta(row))

    async def find(self, attempt_id: str) -> AttemptRecord | None:
        """按 ``attempt_id`` 找尝试：扫 ``kind=copilot`` 的资源元数据。

        ``record_copilot_result`` 只拿到尝试 id（模型从 ``run_copilot_attempt`` 的返回里
        抄回来），所以按 id 反查归属必须扫一遍；作业资源是几十份量级，一次全扫可接受，
        也避免为尝试记录单开一张表（本里程碑的取舍，见模块 docstring）。
        """
        if not attempt_id:
            return None
        result = await self.session.execute(
            select(ResourceAsset).where(
                ResourceAsset.kind == ResourceAssetKind.COPILOT.value
            )
        )
        for row in result.scalars().all():
            for item in self._attempts(self._meta(row)):
                if str(item.get("id")) == attempt_id:
                    return AttemptRecord(asset_id=str(row.id), data=item)
        return None
