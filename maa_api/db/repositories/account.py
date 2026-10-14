"""M27 账号数据的三个仓储（规格 §7.3、附录 E、docs/04 §9）。

分工与纪律（docs/04 §9）：仓储只回答「怎么读写」，不判断「读写意味着什么」。

- **合并规则不在这一层**：森空岛同步写哪些字段、什么时候收缩所有权，都在
  :class:`maa_api.services.account.box.OperatorBoxService`（服务层）里；仓储只提供
  ``new_row`` / ``upsert`` 这样的建行与整行写入原语。
- **不 ``commit()``**：事务边界由调用方（服务层）决定，一次同步要么整体落库、
  要么整体回滚。
- **差异表（``account_operator_diff``）只留历史行**：M47 删掉手动 / Agent 写入与
  ``OperBox`` 识别之后，不再有任何写入者，差异的读写端点与仓储方法一并下线；
  表与 ORM 模型保留，供老库里的历史行自证（docs/13 §8.70）。

三个仓储的调用方是 ``services/account/box.py``、``depot.py`` 与 ``capture.py``；
方法名与关键字参数是与它们约定好的接口，改签名要同步改这三处。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update

from maa_api.db.models import (
    AccountDepotItem,
    AccountOperator,
    AccountRecognition,
    utcnow,
)
from maa_api.db.repositories.base import BaseRepository

__all__ = [
    "RECOGNITION_KIND_DEPOT",
    "RECOGNITION_KIND_SKLAND",
    "RECOGNITION_KIND_TRAINING",
    "AccountDepotItemRepository",
    "AccountOperatorRepository",
    "AccountRecognitionRepository",
]

#: ``account_recognition.kind`` 的取值（规格 §7.3、M36 规格 §4.5、M42 规格 §4.5）。
#: ``operbox``（内核干员识别）自 M47 起不再产生新行，历史行仍留在表里——``kind`` 是
#: 纯字符串列，没有枚举约束，也不回填。
RECOGNITION_KIND_DEPOT = "depot"
#: 干员详情练度扫描（M36）：``item_count`` 是真的写进库的干员数。M42 起不再产生新行，
#: 历史行保留（``kind`` 是纯字符串列，没有枚举约束）。
RECOGNITION_KIND_TRAINING = "training"
#: 森空岛账号数据同步（M42）：``item_count`` 是写进库的干员数 + 清掉的所有权数。
RECOGNITION_KIND_SKLAND = "skland"


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


class AccountDepotItemRepository(BaseRepository):
    """账号仓库表：整表替换、按分类 / 名字读取。"""

    async def replace_all(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """整表替换：先清空再写入本次识别的全部物品。

        ``rows`` 每项给 ``item_id``、``name``、``count``、``category``、
        ``recognized_at``，可选 ``source``（``sources`` 列的历史取值 ``"kernel"`` /
        ``"self_scan"``）。同一个 ``item_id`` 出现多次时**取最后一条**（识别结果的
        后者为准），返回的是去重后真正写入的行数——它同时是识别记录的 ``item_count``。

        **``count`` 允许为 ``None``**：``NULL`` ＝ 未识别（历史上是自识别只保证身份、
        数量没读到的那批行）。旧写法 ``int(row.get("count") or 0)`` 会把「不知道」压成 0，
        而「仓库里没有这件道具」与「不知道有几件」是两件事，所以这里只对非空值做整数归一。

        ``sources`` 列按行收集成 ``{item_id: source}``；一行都没有来源时留 ``NULL``。
        该列自 2026-10-14 起**保留为历史、当前无人写**（写它的自识别已删除实现，
        见 `docs/13` §8.68）：调用方不传 ``source`` 时整列就是 ``NULL``，与 0021 之前的
        旧行同形，读侧按「来源未知」处理。
        """
        # ``sources`` 的值是整份快照共用的 ``{item_id: 来源}``（每行存一份，读侧按行取
        # 自己那一项 —— 将来一行一个来源地混写也不会读串）。所以先收齐再写。
        sources: dict[str, str] = {}
        deduped: dict[str, dict[str, Any]] = {}
        for row in rows:
            item_id = str(row.get("item_id") or "").strip()
            if not item_id:
                continue
            count = row.get("count")
            if count is not None and not isinstance(count, bool):
                try:
                    count = int(str(count).strip())
                except (TypeError, ValueError):
                    count = None
            elif isinstance(count, bool):
                count = int(count)
            source = str(row.get("source") or "").strip()
            if source:
                sources[item_id] = source
            deduped[item_id] = {
                "item_id": item_id,
                "name": row.get("name"),
                "count": None if count is None else max(int(count), 0),
                "category": row.get("category"),
                "recognized_at": row.get("recognized_at") or utcnow(),
            }
        for fields in deduped.values():
            fields["sources"] = dict(sources) if sources else None
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
        only_counted: bool = False,
    ) -> list[AccountDepotItem]:
        """仓库行列表，按「分类 → item_id」排序（页面按分类分组）。

        ``only_counted=True`` 时排除 ``count IS NULL`` 的行（历史上自识别只认了身份、
        数量没读到的那批）。页面只显示有明确数量的物品，读侧用这个开关拿数；
        写库路径（``replace_all`` 的下降护栏）**不传**，它数的是整表行数。
        """
        statement = (
            select(AccountDepotItem)
            .where(*_depot_conditions(category=category, query=query, only_counted=only_counted))
            .order_by(AccountDepotItem.category.asc(), AccountDepotItem.item_id.asc())
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
        category: str | None = None,
        query: str | None = None,
        only_counted: bool = False,
    ) -> int:
        """筛选后的物品数（页面的 ``total`` 与概要的种类数）。

        默认数整表（含历史残留的 ``count IS NULL`` 行）；页面口径传
        ``only_counted=True``，与 :meth:`list` 保持一致。
        """
        statement = (
            select(func.count())
            .select_from(AccountDepotItem)
            .where(*_depot_conditions(category=category, query=query, only_counted=only_counted))
        )
        return int(await self.session.scalar(statement) or 0)

    async def categories(self, *, only_counted: bool = False) -> list[str]:
        """整份快照里出现过的分类，按名字排序（页面分类下拉，不受筛选影响）。

        ``NULL`` 与空白的分类不返回：它们进不了页面的分类分组，也不该出现在下拉里。
        ``only_counted=True`` 时只统计有数量的行，与列表口径一致 —— 否则下拉里会出现
        一个点进去空无一物的分类。
        """
        conditions: list[Any] = [AccountDepotItem.category.is_not(None)]
        if only_counted:
            conditions.append(AccountDepotItem.count.is_not(None))
        statement = (
            select(AccountDepotItem.category)
            .where(*conditions)
            .distinct()
            .order_by(AccountDepotItem.category.asc())
        )
        result = await self.session.execute(statement)
        return [
            text
            for value in result.scalars().all()
            if (text := str(value or "").strip())
        ]


def _depot_conditions(
    *, category: str | None, query: str | None, only_counted: bool = False
) -> list[Any]:
    conditions: list[Any] = []
    if only_counted:
        # 历史行写 NULL ＝ 未识别（0021）。用户口径：仓库页只显示有明确数量的物品。
        conditions.append(AccountDepotItem.count.is_not(None))
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
        detail: Mapping[str, Any] | None = None,
    ) -> AccountRecognition:
        """写一条识别记录（时间、流水线、任务、渠道、数量、过程事实）。

        同 ``kind`` 同 ``recognized_at`` 的已有记录不重复插，而是把非空的上下文补上：
        写库的服务拿不到流水线 / 任务 / 渠道，``capture.py`` 随后带着这三个值再调一次，
        两次说的是同一次识别。
        补的时候只用非空值，不拿 NULL 覆盖已有值。

        ``detail``（M45 W3a）是这次识别的过程事实（仓库识别放来源表与未识别清单），
        同样只在非空时覆盖：第二次调用（capture 层补上下文）不该把第一次写的详情抹掉。
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
                detail=dict(detail) if detail else None,
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
        if detail:
            existing.detail = dict(detail)
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
