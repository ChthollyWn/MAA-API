"""``resource_asset`` 仓储：自定义资源与远端资源的版本基准（docs/04 §5.13、§6、§9）。

三条硬性语义：

- **``content`` 与 ``path`` 二选一**，由 CHECK 约束
  ``ck_resource_asset_content_or_path``（``content IS NOT NULL OR path IS NOT NULL``）
  在数据库层强制：小作业直接入库，大作业落文件走 ``path``。``create`` 与
  ``upsert_by_kind_name`` 都不在服务层查重/查空，冲突与违反 CHECK 时把
  ``IntegrityError`` 原样抛出，由服务层转成 4xx。
- **``(kind, name)`` 唯一**（``uq_resource_asset_kind_name``）：远端版本基准的读取
  就走这条唯一索引（:meth:`ResourceAssetRepository.get_by_kind_name`）。
  ``ota_resource`` 每个清单条目一行、``repo_resource`` 固定一行
  （``name='MaaResource'``）；:meth:`ResourceAssetRepository.upsert_by_kind_name`
  是 ``UpdateService`` 写这两类行的主入口。
- **``repo_resource`` 这一行是通道 B 版本判定的唯一权威来源**，不能改读磁盘上的
  ``version.json``（内核更新会把 ``<maa_path>/resource/version.json`` 换回发行包
  自带的旧值，而 ``<layers>/repo/`` 里的那份不受影响）。本仓储只保证
  ``remote_version`` 列的存取，判定逻辑在 ``UpdateService``（M7）。

两条容易写错的列语义：

- ``updated_at`` 是「上次内容真的变了」，``last_checked_at`` 是「上次向远端确认过
  版本」，两者不能合并（docs/04 §5.13）。所以 :meth:`upsert_by_kind_name` **只在
  内容/配置字段出现时刷新 ``updated_at``**：只带 ``last_checked_at`` 的调用是
  「问了一次远端，什么都没变」，不刷新，否则资源页会把「5 分钟前检查过」显示成
  「刚刚更新」。调用方仍可显式传 ``updated_at`` 覆盖这一规则。
- ``enabled`` 对 ``ota_resource`` / ``repo_resource`` 无意义（加载链是否包含某一层
  取决于目录是否存在），恒为默认值 ``1``；远端两行的增删改由 ``UpdateService``
  独占（用户接口不允许），这是服务层纪律，仓储不代替它做角色判断。

远端两行为什么不能走 :meth:`ResourceAssetRepository.create` 的完整实例路径：
``UpdateService`` 只掌握「这一条远端记录现在是什么」的增量字段，用
``upsert_by_kind_name(kind, name, **fields)`` 只写变化的列，其余列保持原值
（先 UPDATE、没有行再 INSERT，见该方法文档里的 SQLite CHECK 实测）；用完整实例
``merge()`` 会把没传的列覆盖成 Python 默认值。

事务纪律（docs/04 §9）：所有方法都不 ``commit()``，由服务层决定事务边界。
"""

from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from maa_api.db.models import ResourceAsset, utcnow
from maa_api.db.repositories.base import BaseRepository
from maa_api.domain.enums import ResourceAssetKind

# upsert 允许写的列：主键与业务键（kind/name）之外的可变列。
# 反射自表定义而不是手抄，模型加列后这里不会漏。
_IMMUTABLE_UPSERT_FIELDS = frozenset({"id", "kind", "name", "created_at"})
_UPSERTABLE_FIELDS = frozenset(
    column.name for column in ResourceAsset.__table__.columns
) - _IMMUTABLE_UPSERT_FIELDS

# 只带这些字段的 upsert 视为「只是问了一次远端」，不刷新 updated_at
# （``last_checked_at`` 与 ``updated_at`` 是两件事，docs/04 §5.13）。
_CHECK_ONLY_FIELDS = frozenset({"last_checked_at", "updated_at"})


class ResourceAssetRepository(BaseRepository):
    """``resource_asset`` 表的读写：用户资产 CRUD 与远端版本基准 upsert。"""

    async def create(self, asset: ResourceAsset) -> ResourceAsset:
        """写入一条资源，返回受会话管理的副本；只 ``flush`` 不 ``commit``。

        ``content`` 与 ``path`` 都为空时，``flush`` 会以 ``IntegrityError``
        抛出 CHECK 约束失败——这正是想要的失败时机：服务层在同一个 ``try`` 里
        把「资源既没内容也没文件」转成 422，而不是等提交时才炸。

        与 :meth:`PipelineRepository.create` 同样用 ``Session.merge()`` 接收入参
        （M2-07 实测）：入参保持 transient，提交后字段仍可直接读。
        """
        stored = await self.session.merge(asset)
        await self.session.flush()
        return stored

    async def get(self, asset_id: str) -> ResourceAsset | None:
        """按主键取资源，没有返回 ``None``。

        ``populate_existing=True``：``set_enabled`` 与 ``upsert_by_kind_name``
        都会改同一行，不强制刷新会返回身份映射里的陈旧实例（M2-08 实测）。
        """
        return await self.session.get(
            ResourceAsset, asset_id, populate_existing=True
        )

    async def get_by_kind_name(
        self, kind: ResourceAssetKind | str, name: str
    ) -> ResourceAsset | None:
        """按 ``(kind, name)`` 取唯一一行，没有返回 ``None``。

        复用 ``uq_resource_asset_kind_name``（docs/04 §6）：远端版本基准的读取
        （``repo_resource`` 的 ``remote_version``、``ota_resource`` 的
        ``etag`` + ``last_modified`` + ``checksum``）每次只取一到两行。
        """
        result = await self.session.execute(
            select(ResourceAsset)
            .where(ResourceAsset.kind == kind, ResourceAsset.name == name)
            .limit(1)
            .execution_options(populate_existing=True)
        )
        return result.scalars().first()

    async def upsert_by_kind_name(
        self, kind: ResourceAssetKind | str, name: str, **fields: Any
    ) -> ResourceAsset:
        """按 ``(kind, name)`` 插入或更新一行，返回落库后的实例。

        实现是**先 UPDATE、``rowcount == 0`` 再 INSERT**，不是
        ``INSERT ... ON CONFLICT DO UPDATE``。原因是 M2-11 实测的 SQLite 行为：
        CHECK 约束在**插入候选行**上先于冲突判定求值 ——
        ``INSERT INTO resource_asset (kind, name, remote_version) VALUES (...)
        ON CONFLICT (kind, name) DO UPDATE ...`` 即使目标行已存在，候选行仍是
        ``content IS NULL AND path IS NULL``，直接撞
        ``ck_resource_asset_content_or_path``，DO UPDATE 根本没机会执行
        （见 ``docs/ENVIRONMENT.md``）。UPDATE 先行则让 SQLite 在**真实行**
        上判定 CHECK，语义也正好是「更新后的这一行必须仍有 content 或 path」。

        ``fields`` 之外的键（``id`` / ``created_at``）与未知列名一律
        ``ValueError``：``id`` / ``created_at`` 是身份列，改它等于换一行；
        未知列名多半是调用方拼错，静默忽略会写出「看起来成功了但字段没更新」的
        假象。（``kind`` / ``name`` 是位置参数，传不进 ``fields``。）

        ``created_at`` / ``updated_at`` 在插入路径上**显式赋值**：Core
        ``insert().values(...)`` 不会应用 SQLModel ``Field(default_factory=utcnow)``
        配 ``sa_column`` 的那些 Python 侧默认值（M2-11 实测，`created_at` 直接
        NOT NULL 失败），不能依赖列默认。更新路径的 ``updated_at`` 按「内容真的
        变了」才刷新（见模块文档），显式传入的 ``updated_at`` 优先。

        并发语义：UPDATE 与 INSERT 是两条语句，不是一条原子 upsert；并发写者在
        这个间隙插进同一 ``(kind, name)`` 时，由 ``uq_resource_asset_kind_name``
        兜底抛 ``IntegrityError``（与 ``create`` 一致），由调用方决定是否重试。
        远端两行本来就是 ``UpdateService`` 独占写入，不存在真实竞争。
        """
        unknown = set(fields) - _UPSERTABLE_FIELDS
        if unknown:
            raise ValueError(
                f"resource_asset 不接受这些 upsert 字段：{sorted(unknown)}；"
                f"可写列为 {sorted(_UPSERTABLE_FIELDS)}"
            )

        now = utcnow()
        values = dict(fields)
        if "updated_at" not in values and (set(fields) - _CHECK_ONLY_FIELDS):
            values["updated_at"] = now

        updated = 0
        if values:
            result = await self.session.execute(
                update(ResourceAsset)
                .where(ResourceAsset.kind == kind, ResourceAsset.name == name)
                .values(**values)
            )
            updated = result.rowcount

        if updated == 0:
            # 没有可更新的行（或本来就没有可写列）：走插入。
            # CHECK 与唯一约束都在这一条语句上照常生效。
            await self.session.execute(
                sqlite_insert(ResourceAsset).values(
                    kind=kind,
                    name=name,
                    created_at=now,
                    updated_at=now,
                    **fields,
                )
            )

        stored = await self.get_by_kind_name(kind, name)
        if stored is None:  # pragma: no cover - 上面两条路径都必然留下行
            raise RuntimeError(f"upsert 后找不到 {kind!r}/{name!r} 的资源行")
        return stored

    async def list_by_kind(
        self,
        kind: ResourceAssetKind | str,
        *,
        enabled: bool | None = None,
    ) -> list[ResourceAsset]:
        """某类资源的列表：可选 ``enabled`` 过滤，按 ``name`` 升序。

        ``WHERE kind = ? [AND enabled = ?]`` 复用 ``ix_resource_asset_kind_enabled``
        （docs/04 §6 的资源页与增量注入形态）。不返回 ``Page``：单类资源是几十到
        几百行的小集合，资源页与注入都一次取全；排序按 ``name`` 让前端选择器稳定，
        同秒创建也不会换位。
        """
        conditions = [ResourceAsset.kind == kind]
        if enabled is not None:
            conditions.append(ResourceAsset.enabled == bool(enabled))

        result = await self.session.execute(
            select(ResourceAsset)
            .where(*conditions)
            .order_by(ResourceAsset.name.asc(), ResourceAsset.id.asc())
        )
        return list(result.scalars().all())

    async def set_enabled(self, asset_id: str, enabled: bool) -> bool:
        """启用/停用资源，返回是否存在这条记录。

        只对用户侧资产（``copilot`` / ``infrast_plan`` / ``custom_task``）有意义：
        ``custom_task`` 的 ``enabled`` 决定它是否参与增量资源注入。远端两行的
        ``enabled`` 恒为默认值，服务层不应对它们调用本方法（仓储不做角色判断）。
        条件更新 + ``rowcount``，顺带刷新 ``updated_at``；重复设置同一值也算成功。
        """
        result = await self.session.execute(
            update(ResourceAsset)
            .where(ResourceAsset.id == asset_id)
            .values(enabled=bool(enabled), updated_at=utcnow())
        )
        return result.rowcount > 0

    async def delete(self, asset_id: str) -> bool:
        """删除资源，返回是否真的删掉了（``False`` = 记录不存在）。

        物理删除：用户侧资产的内容要么在 ``content`` 列里、要么在 ``resource/``
        下的文件里，行删掉即从加载链移除；文件清理由服务层决定（仓储不碰磁盘）。
        远端两行不允许用户删除，是服务层纪律。
        """
        result = await self.session.execute(
            delete(ResourceAsset).where(ResourceAsset.id == asset_id)
        )
        return result.rowcount > 0
