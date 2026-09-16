"""``setting`` 仓储：配置覆盖层（docs/04 §5.6、§9）。

``setting`` 表只存**被显式覆盖过**的配置项，配置解析优先级为
环境变量 > ``setting`` 表 > ``config.yaml`` > 代码内默认值（docs/04 §5.6）。
所以本仓储只有四件事：读一个 key、upsert 一个 key、读全表、删一个 key，
**绝不把代码内默认值灌进表里**——表里没有某个 key 就意味着「用下层的值」，
升级后新增的默认值会自动生效，不需要数据迁移。

``value`` 是 JSON 列而不是 TEXT（docs/04 §5.6）。:meth:`SettingRepository.get`
与 :meth:`SettingRepository.all` 返回 JSON 反序列化后的**原始 Python 类型**：
``25`` 仍是 ``int``、``"25"`` 仍是 ``str``，调用方（如 ``adb.screenshot_quality``
要传给 PIL）不需要在读取侧再维护一张类型表。SQLite 的 JSON 声明类型是 NUMERIC
亲和，纯数字的 JSON 文本会被 SQLite 存成 INTEGER/REAL；SQLAlchemy 的 SQLite
方言对此有专门处理（``_SQliteJson.result_processor`` 对 ``numbers.Number``
原样返回），整型/浮点仍能原样读回，本模块的测试钉住了这条。

**不做的事**（归属后续里程碑）：字段元信息（中文标签、类型、取值范围、是否敏感、
热生效动作）不进表，定义在代码里的 ``settings_schema.py``；敏感项读接口返回
``"***"``、写接口收到 ``"***"`` / 空串视为「不变更」，以及「环境变量 > 表 >
yaml > 默认值」的解析链，都在 ``SettingService``（M2-12 及之后）。本仓储只负责
存取，不做脱敏、不查类型表、不读 ``config.yaml``。

事务纪律（docs/04 §9）：所有方法都不 ``commit()``，由调用方决定事务边界。
"""

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from maa_api.db.models import Setting, utcnow
from maa_api.db.repositories.base import BaseRepository


class SettingRepository(BaseRepository):
    """配置覆盖项的读写（docs/04 §5.6、§9）。"""

    async def get(self, key: str) -> Any | None:
        """返回 ``key`` 的覆盖值；表里没有这个 key 时返回 ``None``。

        直接 ``select`` 列而不是 ORM 实体：返回值本就是 JSON 解出的原始类型，
        不需要实例；也顺带避开「身份映射里已有陈旧实例时 ``session.get()``
        不读库」的坑（M2-08 实测）。返回 ``None`` 的含义只有一个：**没有覆盖，
        请用下层的值**（见 :meth:`set` 对 ``None`` 的约束）。
        """
        result = await self.session.execute(
            select(Setting.value).where(Setting.key == key)
        )
        return result.scalar_one_or_none()

    async def set(
        self, key: str, value: Any, *, updated_by: str | None = None
    ) -> None:
        """upsert 一个覆盖项：存在则更新 ``value`` / ``updated_by`` / ``updated_at``。

        用 SQLite 的 ``INSERT ... ON CONFLICT(key) DO UPDATE`` 一条语句完成
        （``key`` 是主键），而不是「先查后写」：后者在并发写入时要么丢更新、
        要么抛 ``IntegrityError``，而配置写入恰恰可能来自 REST 与内置 agent
        两条路径。同一 key 永远只有一行。

        ``value`` 不能是 Python ``None``：列是 NOT NULL，且 JSON 的 ``null`` 与
        「表里没有这一行」在 :meth:`get` 的返回值上无法区分——而「表里没有」
        正是「用下层的值」的判据（docs/04 §5.6）。需要清除覆盖请用
        :meth:`delete`。``updated_by`` 是 ``manual`` / ``agent`` / ``system``
        之一（docs/04 §5.6），列宽 16，仓储不做枚举校验。
        """
        if value is None:
            raise ValueError(
                f"setting {key!r} 的值不能是 None：列 NOT NULL，且 JSON null 与"
                "「没有覆盖项」无法区分；要清除覆盖请用 delete()"
            )
        now = utcnow()
        stmt = sqlite_insert(Setting).values(
            key=key, value=value, updated_by=updated_by, updated_at=now
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Setting.key],
            set_={
                "value": stmt.excluded.value,
                "updated_by": stmt.excluded.updated_by,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await self.session.execute(stmt)

    async def all(self) -> dict[str, Any]:
        """全表覆盖项，``{key: 原始类型值}``（配置解析链的「表」这一层）。

        按 ``key`` 升序返回：覆盖顺序对解析结果没有影响，但稳定顺序让日志与
        测试更可读。
        """
        result = await self.session.execute(
            select(Setting.key, Setting.value).order_by(Setting.key.asc())
        )
        return {key: value for key, value in result.all()}

    async def delete(self, key: str) -> bool:
        """删除一个覆盖项，返回是否真的删掉了（``False`` = 表里本来就没有）。

        删掉即回到下层取值，不需要写回任何默认值；重复删除幂等。
        """
        result = await self.session.execute(delete(Setting).where(Setting.key == key))
        return result.rowcount > 0
