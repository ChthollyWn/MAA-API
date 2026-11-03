"""``model_profile`` 仓储：模型档案的持久化（v3 docs/03 §4.1、docs/04 §9）。

这张表只存**用户显式创建**的档案；``default`` 档案由 ``llm.*`` 从
``llm.model`` / ``llm.base_url`` 等设置合成，**不入表**，所以本仓储不合成、
不特判、也不回填默认档案。名称的唯一性由 ``uq_model_profile_name`` 在数据库层
兜底，本仓储不先查后写：冲突时 ``IntegrityError`` 原样抛出，由服务层转
``MODEL_PROFILE_EXISTS``。

事务纪律（docs/04 §9）：所有方法都不 ``commit()``，由调用方决定事务边界。
"""

from sqlalchemy import delete, select

from maa_api.db.models import ModelProfile
from maa_api.db.repositories.base import BaseRepository


class ModelProfileRepository(BaseRepository):
    """模型档案的读写；加解密、探查与只读判定都不在这里。"""

    async def get_by_name(self, name: str) -> ModelProfile | None:
        """按 ``name`` 取档案；不存在返回 ``None``（``default`` 也不在表里）。"""
        result = await self.session.execute(
            select(ModelProfile).where(ModelProfile.name == name)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[ModelProfile]:
        """全部档案，按 ``name`` 升序。是否把 ``default`` 排在最前由服务层决定。"""
        result = await self.session.execute(
            select(ModelProfile).order_by(ModelProfile.name.asc())
        )
        return list(result.scalars().all())

    async def create(self, profile: ModelProfile) -> ModelProfile:
        """写入一条档案，返回受会话管理的副本；只 ``flush`` 不 ``commit``。

        与其它仓储的 ``create`` 一样用 ``Session.merge()`` 接收入参：入参保持
        transient，提交后字段仍可直接读；对入参的后续修改不会被提交。
        """
        stored = await self.session.merge(profile)
        await self.session.flush()
        return stored

    async def delete(self, profile: ModelProfile) -> None:
        """删除一条档案；只 ``flush`` 不 ``commit``，不存在时 ``delete`` 是空操作。"""
        await self.session.delete(profile)
        await self.session.flush()
