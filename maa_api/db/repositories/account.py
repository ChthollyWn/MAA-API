"""M27 账号数据的四个仓储（规格 §7.3、附录 E、docs/04 §9）。

分工与纪律（docs/04 §9）：仓储只回答「怎么读写」，不判断「读写意味着什么」。

- **合并规则不在这一层**：识别覆盖哪些字段、什么时候记一条差异、字段来源怎么变，
  都在 :class:`maa_api.services.account.box.OperatorBoxService`（服务层）里；仓储只
  提供 ``new_row`` / ``upsert`` 这样的建行与整行写入原语。
- **不 ``commit()``**：事务边界由调用方（服务层）决定，一次识别要么整体落库、
  要么整体回滚。
- **读方法返回 ORM 实例或轻量 dataclass**：差异列表要带干员名，ORM 实例不允许挂
  未声明的属性（SQLModel 会抛 ``ValueError``），因此
  :class:`AccountOperatorDiffRepository` 返回 :class:`OperatorDiffRow`。

四个仓储的调用方是 ``services/account/box.py``、``depot.py`` 与 ``capture.py``；
方法名与关键字参数是与它们约定好的接口，改签名要同步改这三处。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update

from maa_api.db.models import (
    AccountDepotItem,
    AccountOperator,
    AccountOperatorDiff,
    AccountRecognition,
    utcnow,
)
from maa_api.db.repositories.base import BaseRepository

__all__ = [
    "RECOGNITION_KIND_DEPOT",
    "RECOGNITION_KIND_OPERBOX",
    "RECOGNITION_KIND_TRAINING",
    "AccountDepotItemRepository",
    "AccountOperatorDiffRepository",
    "AccountOperatorRepository",
    "AccountRecognitionRepository",
    "OperatorDiffRow",
]

#: ``account_recognition.kind`` 的取值（规格 §7.3、M36 规格 §4.5）。
RECOGNITION_KIND_OPERBOX = "operbox"
RECOGNITION_KIND_DEPOT = "depot"
#: 干员详情练度扫描（M36）：``item_count`` 是真的写进库的干员数。
RECOGNITION_KIND_TRAINING = "training"


@dataclass(frozen=True, slots=True)
class OperatorDiffRow:
    """一条差异 + 干员名（页面顶部提示条不再回表查名字）。

    字段名与 ``account_operator_diff`` 的列一一对应，另外多一个 ``name``：
    :func:`maa_api.services.account.rules.diff_wire` 按属性名读它。
    """

    id: int
    char_id: str
    name: str | None
    field: str
    manual_value: Any
    recognized_value: Any
    created_at: datetime | None
    dismissed_at: datetime | None


def _text_filter(column: Any, query: str | None) -> Any:
    """名字片段筛选：``LIKE %query%``；空串与空白视为不过滤。

    ``autoescape=True`` 与 ``LogRepository`` 同口径：用户输入的 ``%`` / ``_`` 是字面
    字符，不是通配符。
    """
    if query is None:
        return None
    text = str(query).strip()
    if not text:
        return None
    return column.contains(text, autoescape=True)


class AccountOperatorRepository(BaseRepository):
    """账号干员表：全表读取、按 char_id 读、建行与整行写入。"""

    # -- 建行与写入 -------------------------------------------------------
    def new_row(
        self,
        char_id: str,
        *,
        name: str | None = None,
        rarity: int | None = None,
        profession: str | None = None,
        own: bool = False,
    ) -> AccountOperator:
        """构造一条**未入库**的干员行（默认值见 :class:`~maa_api.db.models.AccountOperator`）。

        同步方法：识别合并要先把行拼好再决定写不写，构造不应该碰会话。练度六列一律
        留 ``None``（＝未识别）：新行来自识别，而识别给不出技能等级 / 专精 / 模组，
        把 ``masteries`` 预填成三个 0 槽就等于替内核撒了谎（M35）。
        """
        row = AccountOperator(char_id=char_id, name=name, rarity=rarity, own=own)
        row.profession = profession
        return row

    async def get(self, char_id: str) -> AccountOperator | None:
        """按主键取一行；没有记录返回 ``None``（不是空行）。"""
        return await self.session.get(AccountOperator, char_id)

    async def upsert(self, row: AccountOperator) -> AccountOperator:
        """整行写入：存在则覆盖除主键外的全部列，不存在则插入。

        「整行」是刻意的：调用方（识别合并 / 手动编辑 / Agent 写入）已经在内存里
        算好了最终值，仓储不该猜哪些列「这次不想改」。新行必须用
        :meth:`new_row` 构造，这样未显式赋值的列拿到模型默认值。
        """
        existing = await self.session.get(AccountOperator, row.char_id)
        if existing is None:
            self.session.add(row)
            await self.session.flush()
            return row
        for column in AccountOperator.__table__.columns.keys():
            if column == "char_id":
                continue
            setattr(existing, column, getattr(row, column))
        await self.session.flush()
        return existing

    async def upsert_many(self, rows: Sequence[AccountOperator]) -> int:
        """批量写入识别值：一次 ``SELECT ... IN`` 取回已有行，再一次 flush。

        识别一次会给几百名干员写值，逐行走 :meth:`upsert` 是几百次往返；
        这里把「已有的取回来改、没有的加进去」压成两条语句。
        :returns: 写入的行数（新增 + 更新）。
        """
        if not rows:
            return 0
        keys = [row.char_id for row in rows]
        result = await self.session.execute(
            select(AccountOperator).where(AccountOperator.char_id.in_(keys))
        )
        existing = {row.char_id: row for row in result.scalars().all()}
        written = 0
        for row in rows:
            current = existing.get(row.char_id)
            if current is None:
                self.session.add(row)
            else:
                for column in AccountOperator.__table__.columns.keys():
                    if column == "char_id":
                        continue
                    setattr(current, column, getattr(row, column))
            written += 1
        await self.session.flush()
        return written

    # -- 读取 -------------------------------------------------------------
    async def list(
        self,
        *,
        query: str | None = None,
        owned_only: bool | None = None,
        rarity_min: int | None = None,
        profession: str | None = None,
        elite_min: int | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AccountOperator]:
        """干员列表，按「稀有度降序 → 等级降序 → char_id」排序。

        ``owned_only`` 是**三态**：``None`` 不过滤、``True`` 只列已拥有（页面默认）、
        ``False`` 只列未拥有——工具层的 ``owned_only=false`` 要的是「全都列出来」，
        所以服务层传下来的是 ``None``，别在这里把 ``False`` 理解成「不过滤」。
        """
        statement = select(AccountOperator).where(
            *_operator_conditions(
                query=query,
                owned_only=owned_only,
                rarity_min=rarity_min,
                profession=profession,
                elite_min=elite_min,
            )
        )
        statement = statement.order_by(
            AccountOperator.rarity.desc(),
            AccountOperator.level.desc(),
            AccountOperator.char_id.asc(),
        )
        if offset:
            statement = statement.offset(max(int(offset), 0))
        if limit is not None:
            statement = statement.limit(max(int(limit), 0))
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def count(
        self,
        *,
        query: str | None = None,
        owned_only: bool | None = None,
        rarity_min: int | None = None,
        profession: str | None = None,
        elite_min: int | None = None,
    ) -> int:
        """筛选后的总数（分页的 ``total``，不是当前页条数）。"""
        statement = select(func.count()).select_from(AccountOperator).where(
            *_operator_conditions(
                query=query,
                owned_only=owned_only,
                rarity_min=rarity_min,
                profession=profession,
                elite_min=elite_min,
            )
        )
        return int(await self.session.scalar(statement) or 0)

    async def last_recognized_at(self) -> datetime | None:
        """最近一次把识别值写进这张表的时刻；从未识别返回 ``None``。

        取 ``max(recognized_at)`` 而不是识别记录表：手动编辑只动 ``updated_at``，
        不会让「识别时间」变新。
        """
        return await self.session.scalar(select(func.max(AccountOperator.recognized_at)))


def _operator_conditions(
    *,
    query: str | None,
    owned_only: bool | None,
    rarity_min: int | None,
    profession: str | None,
    elite_min: int | None,
) -> list[Any]:
    conditions: list[Any] = []
    if owned_only is not None:
        conditions.append(AccountOperator.own.is_(bool(owned_only)))
    if rarity_min is not None:
        conditions.append(AccountOperator.rarity >= int(rarity_min))
    if elite_min is not None:
        conditions.append(AccountOperator.elite >= int(elite_min))
    if profession is not None and str(profession).strip():
        conditions.append(AccountOperator.profession == str(profession).strip())
    name_filter = _text_filter(AccountOperator.name, query)
    if name_filter is not None:
        conditions.append(name_filter)
    return conditions


class AccountOperatorDiffRepository(BaseRepository):
    """识别覆盖手动值的差异：写入、列出、标记已知。"""

    async def create(
        self,
        *,
        char_id: str,
        field: str,
        manual_value: Any,
        recognized_value: Any,
        created_at: datetime | None = None,
    ) -> AccountOperatorDiff:
        """写一条差异；``created_at`` 默认取库内时间源（识别回调会传事件时间）。"""
        row = AccountOperatorDiff(
            char_id=char_id,
            field=field,
            manual_value=manual_value,
            recognized_value=recognized_value,
            created_at=created_at or utcnow(),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list(
        self,
        *,
        include_dismissed: bool = False,
        char_id: str | None = None,
        limit: int | None = None,
    ) -> list[OperatorDiffRow]:
        """差异列表，按时间倒序（最新的覆盖最先看到），带上干员名。

        ``include_dismissed=False``（默认）只列未处理的：页面顶部提示条用它。
        """
        statement = (
            select(AccountOperatorDiff, AccountOperator.name)
            .outerjoin(
                AccountOperator,
                AccountOperator.char_id == AccountOperatorDiff.char_id,
            )
            .order_by(
                AccountOperatorDiff.created_at.desc(),
                AccountOperatorDiff.id.desc(),
            )
        )
        if not include_dismissed:
            statement = statement.where(AccountOperatorDiff.dismissed_at.is_(None))
        if char_id is not None:
            statement = statement.where(AccountOperatorDiff.char_id == char_id)
        if limit is not None:
            statement = statement.limit(max(int(limit), 0))
        result = await self.session.execute(statement)
        return [
            OperatorDiffRow(
                id=int(row.id or 0),
                char_id=row.char_id,
                name=name,
                field=row.field,
                manual_value=row.manual_value,
                recognized_value=row.recognized_value,
                created_at=row.created_at,
                dismissed_at=row.dismissed_at,
            )
            for row, name in result.all()
        ]

    async def count_pending(self, *, char_id: str | None = None) -> int:
        """未处理的差异条数（页面顶部提示条与账号概要读它）。"""
        statement = (
            select(func.count())
            .select_from(AccountOperatorDiff)
            .where(AccountOperatorDiff.dismissed_at.is_(None))
        )
        if char_id is not None:
            statement = statement.where(AccountOperatorDiff.char_id == char_id)
        return int(await self.session.scalar(statement) or 0)

    async def dismiss(
        self, ids: Sequence[int] | None = None, *, now: datetime | None = None
    ) -> int:
        """把差异标记为已知：``ids`` 为 ``None`` 表示全部未处理的。

        :returns: 本次真正改到的条数——已经标记过的不再计数（幂等）。
        """
        stamp = now or utcnow()
        statement = (
            sa_update(AccountOperatorDiff)
            .where(AccountOperatorDiff.dismissed_at.is_(None))
            .values(dismissed_at=stamp)
        )
        if ids is not None:
            selected = [int(item) for item in ids]
            if not selected:
                return 0
            statement = statement.where(AccountOperatorDiff.id.in_(selected))
        result = await self.session.execute(statement)
        return int(result.rowcount or 0)


class AccountDepotItemRepository(BaseRepository):
    """账号仓库表：整表替换、按分类 / 名字读取。"""

    async def replace_all(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """整表替换：先清空再写入本次识别的全部物品。

        ``rows`` 每项给 ``item_id``、``name``、``count``、``category``、
        ``recognized_at``（构造与归一在 :class:`~maa_api.services.account.depot.DepotService`
        与 ``capture.py``）。同一个 ``item_id`` 出现多次时**取最后一条**（识别结果的
        后者为准），返回的是去重后真正写入的行数——它同时是识别记录的 ``item_count``。
        """
        deduped: dict[str, dict[str, Any]] = {}
        for row in rows:
            item_id = str(row.get("item_id") or "").strip()
            if not item_id:
                continue
            deduped[item_id] = {
                "item_id": item_id,
                "name": row.get("name"),
                "count": int(row.get("count") or 0),
                "category": row.get("category"),
                "recognized_at": row.get("recognized_at") or utcnow(),
            }
        await self.session.execute(sa_delete(AccountDepotItem))
        if deduped:
            self.session.add_all(
                [AccountDepotItem(**fields) for fields in deduped.values()]
            )
        await self.session.flush()
        return len(deduped)

    async def list(
        self,
        *,
        category: str | None = None,
        query: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AccountDepotItem]:
        """仓库行列表，按「分类 → item_id」排序（页面按分类分组）。"""
        statement = (
            select(AccountDepotItem)
            .where(*_depot_conditions(category=category, query=query))
            .order_by(AccountDepotItem.category.asc(), AccountDepotItem.item_id.asc())
        )
        if offset:
            statement = statement.offset(max(int(offset), 0))
        if limit is not None:
            statement = statement.limit(max(int(limit), 0))
        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def count(self, *, category: str | None = None, query: str | None = None) -> int:
        """筛选后的物品数（页面的 ``total`` 与概要的种类数）。"""
        statement = (
            select(func.count())
            .select_from(AccountDepotItem)
            .where(*_depot_conditions(category=category, query=query))
        )
        return int(await self.session.scalar(statement) or 0)

    async def categories(self) -> list[str]:
        """整份快照里出现过的分类，按名字排序（页面分类下拉，不受筛选影响）。

        ``NULL`` 与空白的分类不返回：它们进不了页面的分类分组，也不该出现在下拉里。
        """
        statement = (
            select(AccountDepotItem.category)
            .where(AccountDepotItem.category.is_not(None))
            .distinct()
            .order_by(AccountDepotItem.category.asc())
        )
        result = await self.session.execute(statement)
        return [
            text
            for value in result.scalars().all()
            if (text := str(value or "").strip())
        ]


def _depot_conditions(*, category: str | None, query: str | None) -> list[Any]:
    conditions: list[Any] = []
    if category is not None and str(category).strip():
        conditions.append(AccountDepotItem.category == str(category).strip())
    # 名字与 item_id 都参与片段筛选：映射不到的物品 name 就是 item_id，按 id 也能搜到。
    name_filter = _text_filter(AccountDepotItem.name, query)
    if name_filter is not None:
        id_filter = _text_filter(AccountDepotItem.item_id, query)
        conditions.append(or_(name_filter, id_filter) if id_filter is not None else name_filter)
    return conditions


class AccountRecognitionRepository(BaseRepository):
    """识别记录：写入与「最近一次」读取（规格 §7.3）。"""

    async def record(
        self,
        *,
        kind: str,
        item_count: int,
        recognized_at: datetime,
        pipeline_id: str | None = None,
        task_id: str | None = None,
        client_type: str | None = None,
    ) -> AccountRecognition:
        """写一条识别记录（时间、流水线、任务、渠道、数量）。

        同 ``kind`` 同 ``recognized_at`` 的已有记录不重复插，而是把非空的上下文补上：
        识别结果由 ``OperatorBoxService.apply_recognition`` 落库（它拿不到流水线 /
        任务 / 渠道），``capture.py`` 随后带着这三个值再调一次，两次说的是同一次识别。
        补的时候只用非空值，不拿 NULL 覆盖已有值。
        """
        existing = await self.session.scalar(
            select(AccountRecognition)
            .where(
                AccountRecognition.kind == kind,
                AccountRecognition.recognized_at == recognized_at,
            )
            .order_by(AccountRecognition.id.desc())
            .limit(1)
        )
        if existing is None:
            row = AccountRecognition(
                kind=kind,
                pipeline_id=pipeline_id,
                task_id=task_id,
                client_type=client_type,
                item_count=item_count,
                recognized_at=recognized_at,
            )
            self.session.add(row)
            await self.session.flush()
            return row
        if pipeline_id is not None:
            existing.pipeline_id = pipeline_id
        if task_id is not None:
            existing.task_id = task_id
        if client_type is not None:
            existing.client_type = client_type
        if item_count:
            existing.item_count = item_count
        await self.session.flush()
        return existing

    async def latest(self, kind: str | None = None) -> AccountRecognition | None:
        """最近一条识别记录（可按 ``kind`` 限定）；一条都没有返回 ``None``。"""
        statement = select(AccountRecognition)
        if kind is not None:
            statement = statement.where(AccountRecognition.kind == kind)
        statement = statement.order_by(
            AccountRecognition.recognized_at.desc(), AccountRecognition.id.desc()
        ).limit(1)
        return await self.session.scalar(statement)

    async def last_at(self, kind: str) -> datetime | None:
        """某种识别的最近一次识别时间；从未识别返回 ``None``（概要判过期用）。"""
        return await self.session.scalar(
            select(func.max(AccountRecognition.recognized_at)).where(
                AccountRecognition.kind == kind
            )
        )
