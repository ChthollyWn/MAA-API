"""``agent_trigger`` / ``trigger_rule`` / ``trigger_observation`` 三张表的仓储（M81 规格 §4.1）。

三条纪律照 ``base.py`` 与既有仓储：

- **不 commit**：事务边界归调用方（``TriggerService`` 要把事件入表、研判结果、
  建单与收件箱写在同一次事务里）。``insert`` / ``create`` 只 ``flush()``。
- **业务决策不在这里**：准入、规则匹配、冷却、日限额、路由与 observer 的「有就
  复用」都在服务层；仓储只回答问题、做整行写入。
- **读方法返回 ORM 实例**，分页统一返回 :class:`Page`。

几个查询的边界值得记住：

- ``page`` 按 ``id DESC``（事件是追加写，id 顺序就是到达顺序），过滤条件都可选。
- ``recent_on_thread`` 的 ``before_id`` 是**严格小于**：事件卡要的是当前这条之前
  的近期事件，不包含它自己。
- ``last_routed`` 只认 ``routed_to`` 为 ticket / observation 的行：只记录与交总控
  不算「处理过同一话题」，不该压住规则的下一次触发。
- ``purge`` 只删「没有工单、也没有仍在观察保留期内的观察记录」的旧事件；观察记录
  自己由 ``TriggerObservationRepository.purge`` 按 90 天清。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, exists, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from maa_api.db.models import AgentTrigger, TriggerObservation, TriggerRule
from maa_api.db.repositories.base import BaseRepository, Page


class TriggerRepository(BaseRepository):
    """``agent_trigger`` 的读写（M81 规格 §4.1）。"""

    async def insert(self, row: AgentTrigger) -> AgentTrigger:
        """写入一条事件，只 ``flush`` 不 ``commit``。

        不做「查重再插入」：``dedupe_key`` 的部分唯一索引是并发下唯一可靠的闸门，
        冲突时让 ``IntegrityError`` 抛给服务层，由它按 ``get_by_dedupe_key`` 取既有
        行并按重复处理。
        """
        self.session.add(row)
        await self.session.flush()
        return row

    async def get(self, trigger_id: int) -> AgentTrigger | None:
        """按主键取事件；不存在返回 ``None``。"""
        return await self.session.get(AgentTrigger, trigger_id)

    async def get_by_dedupe_key(self, key: str) -> AgentTrigger | None:
        """按 ``dedupe_key`` 取事件；没有时返回 ``None``。"""
        result = await self.session.execute(
            select(AgentTrigger).where(AgentTrigger.dedupe_key == key)
        )
        return result.scalar_one_or_none()

    async def page(
        self,
        *,
        source: str | None = None,
        kind: str | None = None,
        rule_id: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> Page[AgentTrigger]:
        """按来源 / 种类 / 规则过滤，``id DESC`` 分页；三个条件都可选。"""
        conditions = []
        if source is not None:
            conditions.append(AgentTrigger.source == source)
        if kind is not None:
            conditions.append(AgentTrigger.kind == kind)
        if rule_id is not None:
            conditions.append(AgentTrigger.rule_id == rule_id)
        statement = (
            select(AgentTrigger)
            .where(*conditions)
            .order_by(AgentTrigger.id.desc())
        )
        count_statement = (
            select(func.count()).select_from(AgentTrigger).where(*conditions)
        )
        return await self.paginate(statement, count_statement, page=page, size=size)

    async def recent_on_thread(
        self, thread_key: str, *, limit: int = 5, before_id: int
    ) -> list[AgentTrigger]:
        """同一话题、``id`` 严格小于 ``before_id`` 的近期事件，``id DESC`` 最多 ``limit`` 条。"""
        result = await self.session.execute(
            select(AgentTrigger)
            .where(
                AgentTrigger.thread_key == thread_key,
                AgentTrigger.id < before_id,
            )
            .order_by(AgentTrigger.id.desc())
            .limit(max(int(limit), 1))
        )
        return list(result.scalars().all())

    async def last_routed(
        self, *, rule_id: str, thread_key: str, since: datetime
    ) -> AgentTrigger | None:
        """``since`` 之后该规则在同一话题上真正处理过的事件（建单或写观察）。"""
        result = await self.session.execute(
            select(AgentTrigger)
            .where(
                AgentTrigger.rule_id == rule_id,
                AgentTrigger.thread_key == thread_key,
                AgentTrigger.routed_to.in_(("ticket", "observation")),
                AgentTrigger.created_at >= since,
            )
            .order_by(AgentTrigger.id.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def count_evaluated_since(self, since: datetime) -> int:
        """``since`` 之后真正调用过研判的事件数（``eval_run_id IS NOT NULL``）。"""
        statement = (
            select(func.count())
            .select_from(AgentTrigger)
            .where(
                AgentTrigger.created_at >= since,
                AgentTrigger.eval_run_id.is_not(None),
            )
        )
        return await self.count(statement)

    async def unprocessed(self) -> list[AgentTrigger]:
        """还没处理完的事件（``processed_at IS NULL``），``id`` 升序供恢复按到达顺序补处理。"""
        result = await self.session.execute(
            select(AgentTrigger)
            .where(AgentTrigger.processed_at.is_(None))
            .order_by(AgentTrigger.id.asc())
        )
        return list(result.scalars().all())

    async def purge(self, *, before: datetime, keep_observed_since: datetime) -> int:
        """删 ``before`` 之前的旧事件；有工单、或有较新观察记录引用的不删。

        返回真正删掉的行数。观察记录的保留期比事件长：一条旧事件只要还挂着
        ``keep_observed_since`` 之后的观察记录，就必须留着让用户审阅。
        """
        recent_observation = exists().where(
            TriggerObservation.trigger_id == AgentTrigger.id,
            TriggerObservation.created_at >= keep_observed_since,
        )
        result = await self.session.execute(
            delete(AgentTrigger).where(
                AgentTrigger.created_at < before,
                AgentTrigger.ticket_id.is_(None),
                ~recent_observation,
            )
        )
        return result.rowcount


class TriggerRuleRepository(BaseRepository):
    """``trigger_rule`` 的读写；覆盖与合并语义在服务层（M81 规格 §4.2）。"""

    async def all(self) -> list[TriggerRule]:
        """全表行，按 ``id`` 升序；规则表规模很小，每轮全量读。"""
        result = await self.session.execute(
            select(TriggerRule).order_by(TriggerRule.id.asc())
        )
        return list(result.scalars().all())

    async def get(self, rule_id: str) -> TriggerRule | None:
        """按主键取规则覆盖行 / 用户规则；没有时返回 ``None``。"""
        return await self.session.get(TriggerRule, rule_id)

    async def upsert(self, rule: TriggerRule) -> None:
        """整行写入：存在则覆盖可写列，不存在则插入。

        ``created_at`` 不在更新列里：首次写入的时间要留住。``set_`` 里的 NULL
        是有意义的（覆盖行只写与内置不同的列），所以不能跳过 NULL 列。
        """
        statement = sqlite_insert(TriggerRule).values(
            id=rule.id,
            title=rule.title,
            source=rule.source,
            kind_pattern=rule.kind_pattern,
            conditions=rule.conditions,
            mode=rule.mode,
            worker=rule.worker,
            policy=rule.policy,
            cooldown_seconds=rule.cooldown_seconds,
            cron=rule.cron,
            enabled=rule.enabled,
            origin=rule.origin,
            created_at=rule.created_at,
            updated_at=rule.updated_at,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[TriggerRule.id],
            set_={
                "title": statement.excluded.title,
                "source": statement.excluded.source,
                "kind_pattern": statement.excluded.kind_pattern,
                "conditions": statement.excluded.conditions,
                "mode": statement.excluded.mode,
                "worker": statement.excluded.worker,
                "policy": statement.excluded.policy,
                "cooldown_seconds": statement.excluded.cooldown_seconds,
                "cron": statement.excluded.cron,
                "enabled": statement.excluded.enabled,
                "origin": statement.excluded.origin,
                "updated_at": statement.excluded.updated_at,
            },
        )
        await self.session.execute(statement)

    async def delete(self, rule_id: str) -> bool:
        """删除一行，返回是否真的删掉了（``False`` = 表里本来就没有）。"""
        result = await self.session.execute(
            delete(TriggerRule).where(TriggerRule.id == rule_id)
        )
        return result.rowcount > 0


class TriggerObservationRepository(BaseRepository):
    """``trigger_observation`` 的读写与统计（M81 规格 §4.1、§4.5.6）。"""

    async def create(self, observation: TriggerObservation) -> TriggerObservation:
        """写入一条观察记录，只 ``flush`` 不 ``commit``。"""
        self.session.add(observation)
        await self.session.flush()
        return observation

    async def get(self, observation_id: int) -> TriggerObservation | None:
        """按主键取观察记录；不存在返回 ``None``。"""
        return await self.session.get(TriggerObservation, observation_id)

    async def get_by_trigger(self, trigger_id: int) -> TriggerObservation | None:
        """取某条事件已有的观察记录；恢复时用它避免重复写（一条事件至多一条）。"""
        result = await self.session.execute(
            select(TriggerObservation)
            .where(TriggerObservation.trigger_id == trigger_id)
            .order_by(TriggerObservation.id.asc())
            .limit(1)
        )
        return result.scalars().first()

    async def page(
        self,
        *,
        rule_id: str | None = None,
        reviewed: bool | None = None,
        page: int = 1,
        size: int = 20,
    ) -> Page[TriggerObservation]:
        """按规则 / 是否已审阅过滤，``created_at DESC, id DESC`` 分页。"""
        conditions = []
        if rule_id is not None:
            conditions.append(TriggerObservation.rule_id == rule_id)
        if reviewed is not None:
            if reviewed:
                conditions.append(TriggerObservation.reviewed_at.is_not(None))
            else:
                conditions.append(TriggerObservation.reviewed_at.is_(None))
        statement = (
            select(TriggerObservation)
            .where(*conditions)
            .order_by(
                TriggerObservation.created_at.desc(), TriggerObservation.id.desc()
            )
        )
        count_statement = (
            select(func.count()).select_from(TriggerObservation).where(*conditions)
        )
        return await self.paginate(statement, count_statement, page=page, size=size)

    async def purge(self, before: datetime) -> int:
        """删 ``before`` 之前的观察记录，返回真正删掉的行数（保留 90 天）。"""
        result = await self.session.execute(
            delete(TriggerObservation).where(TriggerObservation.created_at < before)
        )
        return result.rowcount

    async def stats_since(
        self, since: datetime
    ) -> dict[str, tuple[int, int, int, int]]:
        """按规则汇总 ``since`` 之后的观察：``rule_id → (观察数, 已审阅数, good 数, 最近 5 条审阅里的 bad 数)``。

        「最近 5 条」按 ``reviewed_at`` 降序、``id`` 降序取。观察记录只有 90 天
        保留期，逐行取回在 Python 里聚合即可；调用方一次取全部规则，不按规则发
        查询。
        """
        result = await self.session.execute(
            select(
                TriggerObservation.rule_id,
                TriggerObservation.id,
                TriggerObservation.verdict,
                TriggerObservation.reviewed_at,
            )
            .where(TriggerObservation.created_at >= since)
            .order_by(TriggerObservation.rule_id.asc(), TriggerObservation.id.asc())
        )

        observed: dict[str, int] = {}
        reviewed: dict[str, int] = {}
        good: dict[str, int] = {}
        reviews: dict[str, list[tuple[datetime, int, str | None]]] = {}
        for rule_id, observation_id, verdict, reviewed_at in result.all():
            key = str(rule_id)
            observed[key] = observed.get(key, 0) + 1
            if reviewed_at is None:
                continue
            reviewed[key] = reviewed.get(key, 0) + 1
            if verdict == "good":
                good[key] = good.get(key, 0) + 1
            reviews.setdefault(key, []).append(
                (reviewed_at, int(observation_id), verdict)
            )

        stats: dict[str, tuple[int, int, int, int]] = {}
        for key, total in observed.items():
            latest = sorted(
                reviews.get(key, []),
                key=lambda item: (item[0], item[1]),
                reverse=True,
            )[:5]
            bad = sum(1 for _reviewed_at, _id, verdict in latest if verdict == "bad")
            stats[key] = (
                total,
                reviewed.get(key, 0),
                good.get(key, 0),
                bad,
            )
        return stats
