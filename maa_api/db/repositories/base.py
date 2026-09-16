"""仓储基类与统一分页封装（docs/04 §9）。

三条职责纪律（docs/04 §9，M2-07）：

- **把查询语句挡在服务层之外，不做业务决策。** 服务层不出现 ``select()``，
  仓储里不出现「``if 状态 == X then 发通知``」这种状态机分支；仓储只负责
  「怎么读写」，不负责「读写意味着什么」。
- **仓储方法不自己 ``commit()``**，事务边界由调用方决定 —— 一次流水线提交
  要在同一事务里写 ``pipeline`` 与全部 ``task``，仓储各自提交就做不到原子。
  唯一例外是 ``LogRepository.bulk_insert()`` 与清理方法（M2-08/M2-12），
  它们自成事务。
- **读方法返回 ORM 实例或轻量 dataclass**，不返回 ``Row`` / 元组；分页统一
  返回 :class:`Page`，字段与 API 的分页响应体一一对应，路由层不做二次转换。
"""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """统一分页返回体：``items`` / ``total`` / ``page`` / ``size``（docs/04 §9）。

    四个字段名不要改：与 API 的分页响应体结构一致，M3 的路由层直接返回本对象。
    ``total`` 是**过滤后的总数**（不是当前页条数），前端据此算总页数。
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    items: list[T]
    total: int
    page: int
    size: int


class BaseRepository:
    """所有仓储的基类：持有会话 + 分页封装。子类继承构造函数 ``Repo(session)``。

    会话由调用方注入（而不是仓储内部 ``session_factory()``），这样一次请求的
    多个仓储共享同一事务；仓储不关闭会话、不提交事务。
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _normalize(page: int, size: int) -> tuple[int, int]:
        """把越界分页参数夹到合法区间。

        负 offset / limit 在 SQLite 上不会报错，只会静默返回错误结果；路由层
        虽已用 ``Query(ge=1)`` 校验，仓储仍兜一层，避免内部调用方踩坑。
        """
        return max(int(page), 1), max(int(size), 1)

    async def count(self, count_stmt: Select[Any]) -> int:
        """执行 ``select(func.count())...`` 形式的计数语句，返回 int。"""
        return int((await self.session.execute(count_stmt)).scalar_one())

    async def paginate(
        self,
        items_stmt: Select[Any],
        count_stmt: Select[Any],
        *,
        page: int = 1,
        size: int = 20,
    ) -> Page[Any]:
        """先计数、再取当前页；``offset`` / ``limit`` 由这里统一追加。

        ``items_stmt`` 与 ``count_stmt`` 的过滤条件必须由调用方保持一致（本方法
        无法从语句里反推）；排序写在 ``items_stmt`` 里，计数语句不需要排序。
        """
        page, size = self._normalize(page, size)
        total = await self.count(count_stmt)
        result = await self.session.execute(
            items_stmt.offset((page - 1) * size).limit(size)
        )
        return Page[Any](
            items=list(result.scalars().all()),
            total=total,
            page=page,
            size=size,
        )
