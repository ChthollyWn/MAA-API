"""森空岛凭据与角色的持久化（M42 规格 §4.4）。

两张表都在 ``db/models.py`` 里（``skland_credential`` 单行、``skland_role`` 按 ``uid``），
这一层回答三件事：密文怎么加、单行怎么覆盖、角色怎么整表替换。**策略不在这里**：
什么时候算绑好、什么时候允许清 ``own``、频率护栏，都在
:class:`~maa_api.services.skland.sync.SklandSyncService` 与
:class:`~maa_api.services.skland.login.SklandLoginService` 里。

密钥取配置项 ``skland.credential_key``（Fernet key，与 ``llm.api_key`` 同款做法）：
缺失或不是合法 key 时抛 409 ``SKLAND_ENCRYPTION_KEY_INVALID``，**且只在真的要读/写凭据时
抛**——没绑定过的库不该因为没配密钥就让状态接口报错。

与 ``maa_api/agent/secrets.py`` 的关系：那边是 LLM 密钥的同类实现（同样的 Fernet），
但错误码与语义不同（``LLM_ENCRYPTION_KEY_INVALID``），所以这里独立实现而不是复用，
免得森空岛的密钥问题在页面上表现为「LLM 密钥无效」。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select

from maa_api.db.models import SklandCredential, SklandRole, utcnow
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.skland.client import BindingRole, SklandCredential as PlainCredential
from maa_api.settings import Settings, get_settings

__all__ = ["SklandStore", "StoredCredential", "StoredRole"]

#: 单行表的固定主键。
CREDENTIAL_ID = 1


@dataclass(frozen=True, slots=True)
class StoredCredential:
    """解密后的凭据；只在服务进程内存里存在。"""

    cred: str
    token: str
    bound_at: datetime | None = None
    refreshed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StoredRole:
    """``skland_role`` 的一行（页面展示与「取哪个角色同步」的依据）。"""

    uid: str
    app_code: str = "arknights"
    channel_master_id: str | None = None
    nickname: str | None = None
    level: int | None = None
    is_default: bool = False
    synced_at: datetime | None = None


class SklandStore:
    """凭据与角色的读写；每次调用开一个会话（与 ``OperatorBoxService`` 同款）。"""

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        settings_provider: Callable[[], Settings] = get_settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings_provider = settings_provider

    # -- 密钥 -------------------------------------------------------------
    @property
    def key_configured(self) -> bool:
        """配置里有没有一个**能用**的 Fernet key（状态接口据此给前端提示）。"""
        return self._fernet_or_none() is not None

    def _fernet_or_none(self) -> Fernet | None:
        key = (self._settings_provider().skland.credential_key or "").strip()
        if not key:
            return None
        try:
            return Fernet(key.encode("ascii"))
        except (ValueError, TypeError):
            return None

    def _fernet(self) -> Fernet:
        fernet = self._fernet_or_none()
        if fernet is None:
            raise AppError(
                ErrorCode.SKLAND_ENCRYPTION_KEY_INVALID,
                "森空岛凭据加密密钥缺失或不是合法 Fernet key：请在 config.yaml 配 skland.credential_key",
                {"setting": "skland.credential_key"},
            )
        return fernet

    # -- 凭据 -------------------------------------------------------------
    async def has_credential(self) -> bool:
        """库里有没有凭据行；**不解密**，因此不受密钥是否配好影响（状态接口用它）。"""
        async with self._session_factory() as session:
            return await session.get(SklandCredential, CREDENTIAL_ID) is not None

    async def load_credential(self) -> StoredCredential | None:
        """读凭据；没绑定返回 ``None``。有行但密钥不可用时抛 409。"""
        async with self._session_factory() as session:
            row = await session.get(SklandCredential, CREDENTIAL_ID)
            if row is None:
                return None
            fernet = self._fernet()
            try:
                cred = fernet.decrypt(row.cred_encrypted.encode("ascii")).decode("utf8")
                token = fernet.decrypt(row.token_encrypted.encode("ascii")).decode("utf8")
            except (InvalidToken, ValueError):
                raise AppError(
                    ErrorCode.SKLAND_ENCRYPTION_KEY_INVALID,
                    "已保存的森空岛凭据无法用当前 skland.credential_key 解密：密钥换过了，请重新扫码绑定",
                    {"setting": "skland.credential_key"},
                ) from None
            return StoredCredential(
                cred=cred,
                token=token,
                bound_at=row.bound_at,
                refreshed_at=row.refreshed_at,
            )

    async def save_credential(
        self,
        credential: PlainCredential,
        *,
        bound_at: datetime | None = None,
        refreshed_at: datetime | None = None,
    ) -> StoredCredential:
        """整行覆盖单行表（换绑就是覆盖），返回入库后的形状。"""
        fernet = self._fernet()
        moment = utcnow()
        bound = bound_at or moment
        async with self._session_factory() as session:
            row = await session.get(SklandCredential, CREDENTIAL_ID)
            if row is None:
                row = SklandCredential(id=CREDENTIAL_ID)
                session.add(row)
            row.cred_encrypted = fernet.encrypt(credential.cred.encode("utf8")).decode("ascii")
            row.token_encrypted = fernet.encrypt(credential.token.encode("utf8")).decode("ascii")
            row.bound_at = bound
            row.refreshed_at = refreshed_at
            row.updated_at = moment
            await session.commit()
        return StoredCredential(
            cred=credential.cred,
            token=credential.token,
            bound_at=bound,
            refreshed_at=refreshed_at,
        )

    async def touch_credential(
        self, credential: PlainCredential, *, refreshed_at: datetime | None = None
    ) -> StoredCredential:
        """续期：换掉密文与 ``refreshed_at``，``bound_at`` 不动。"""
        fernet = self._fernet()
        moment = refreshed_at or utcnow()
        async with self._session_factory() as session:
            row = await session.get(SklandCredential, CREDENTIAL_ID)
            if row is None:
                row = SklandCredential(id=CREDENTIAL_ID, bound_at=moment)
                session.add(row)
            row.cred_encrypted = fernet.encrypt(credential.cred.encode("utf8")).decode("ascii")
            row.token_encrypted = fernet.encrypt(credential.token.encode("utf8")).decode("ascii")
            row.refreshed_at = moment
            row.updated_at = moment
            await session.commit()
        return StoredCredential(
            cred=credential.cred, token=credential.token, bound_at=row.bound_at, refreshed_at=moment
        )

    async def clear(self) -> bool:
        """解绑：删凭据与角色行，**不动 ``account_operator``**（练度是账号数据）。

        :returns: 原本是否绑定过（供接口区分「解绑了」与「本来就没绑」）。
        """
        async with self._session_factory() as session:
            row = await session.get(SklandCredential, CREDENTIAL_ID)
            await session.execute(delete(SklandRole))
            if row is not None:
                await session.delete(row)
            await session.commit()
            return row is not None

    # -- 角色 -------------------------------------------------------------
    async def list_roles(self) -> list[StoredRole]:
        async with self._session_factory() as session:
            result = await session.execute(select(SklandRole).order_by(SklandRole.uid))
            return [_to_stored_role(row) for row in result.scalars().all()]

    async def replace_roles(self, roles: Sequence[BindingRole | StoredRole]) -> list[StoredRole]:
        """整表替换：上游给的就是全集（绑定接口一次返回全部角色）。"""
        moment = utcnow()
        async with self._session_factory() as session:
            existing = {
                row.uid: row
                for row in (await session.execute(select(SklandRole))).scalars().all()
            }
            wanted: set[str] = set()
            for role in roles:
                uid = str(getattr(role, "uid", "") or "")
                if not uid:
                    continue
                wanted.add(uid)
                row = existing.get(uid)
                if row is None:
                    row = SklandRole(uid=uid, created_at=moment, updated_at=moment)
                    session.add(row)
                row.app_code = str(getattr(role, "app_code", "arknights") or "arknights")
                channel = getattr(role, "channel_master_id", None)
                row.channel_master_id = (
                    str(channel) if channel is not None else None
                ) or None
                nickname = getattr(role, "nickname", None)
                row.nickname = str(nickname) if nickname else None
                level = getattr(role, "level", None)
                row.level = int(level) if isinstance(level, int) else None
                row.is_default = bool(getattr(role, "is_default", False))
                row.updated_at = moment
            for uid, row in existing.items():
                if uid not in wanted:
                    await session.delete(row)
            await session.commit()
        return await self.list_roles()

    async def mark_synced(self, uid: str, when: datetime | None = None) -> None:
        """记下「这个角色刚同步成功」。"""
        moment = when or utcnow()
        async with self._session_factory() as session:
            row = await session.get(SklandRole, uid)
            if row is None:
                return
            row.synced_at = moment
            row.updated_at = moment
            await session.commit()


def _to_stored_role(row: SklandRole) -> StoredRole:
    return StoredRole(
        uid=row.uid,
        app_code=row.app_code,
        channel_master_id=row.channel_master_id,
        nickname=row.nickname,
        level=row.level,
        is_default=bool(row.is_default),
        synced_at=row.synced_at,
    )
