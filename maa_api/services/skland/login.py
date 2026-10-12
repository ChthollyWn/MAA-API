"""森空岛扫码登录会话与手动兜底绑定（M42 规格 §4.2、§4.6）。

流程（上游三步，本模块把后两步也接上，前端只轮询一次）：

1. ``gen_scan/login`` 拿 ``scanId`` 与 ``scanUrl``（``hypergryph://scan_login?scanId=…``），
   用仓库内的零依赖编码器（:mod:`maa_api.util.qr`）把 ``scanUrl`` 画成 SVG 返回；
2. 前端按 ``poll_interval_ms`` 轮询本模块；上游 ``status=100`` 是「还没扫」，
   ``0`` 才会给出 ``scanCode``，``102`` 是会话失效（**真机实测：生成后约 3 分钟失效**，
   所以 TTL 取 180 秒是实测值而不是拍的）；
3. 拿到 ``scanCode`` 后一次性走完 ``token_by_scan_code`` → ``grant`` → ``generate_cred``
   → ``binding``，把凭据加密落库、角色整表替换，返回 ``succeeded``。

会话表在**进程内存**里（``session_id`` → ``scan_id`` + 创建时间 + 轮询次数）：扫码是
几十秒内的事，值得为它建表的东西没有；服务重启会话即失效，前端提示重新生成即可。
每次访问都顺手清掉过期会话，所以内存不会堆积。

上游**没有**「已扫码待确认」这个中间态（未确认时一直回 ``100``），所以状态机只有
``waiting`` / ``succeeded`` / ``expired`` / ``failed`` 四态，不发明状态。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from maa_api.db.models import utcnow
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.account.rules import iso_z
from maa_api.services.skland.client import (
    BindingRole,
    SklandClient,
    SklandCredential as PlainCredential,
)
from maa_api.services.skland.store import SklandStore
from maa_api.services.skland.sync import SKLAND_QR_LOOP_TTL_SECONDS
from maa_api.settings import Settings, get_settings
from maa_api.util.qr import qr_svg

__all__ = ["SklandLoginService"]

logger = logging.getLogger(__name__)

#: 轮询间隔（毫秒，前端用）。上游 ``scan_status`` 很轻（实测 ~100 ms），2 秒足够。
SKLAND_QR_POLL_INTERVAL_MS = 2000
#: 单个会话最多被轮询多少次（180 秒 / 2 秒 = 90，留些余量）：超过就当过期，
#: 免得有人拿着一个 session_id 无限轮询。
SKLAND_QR_MAX_POLLS = 120


@dataclass
class _QrSession:
    session_id: str
    scan_id: str
    created_at: datetime
    polls: int = 0


@dataclass(frozen=True, slots=True)
class _BindResult:
    """扫码成功后的绑定结果。"""

    account: BindingRole
    roles: tuple[BindingRole, ...] = field(default_factory=tuple)


class SklandLoginService:
    """扫码会话 + 手动兜底绑定 + 解绑。"""

    def __init__(
        self,
        *,
        store: SklandStore,
        client_factory: Callable[..., SklandClient] = SklandClient,
        settings_provider: Callable[[], Settings] = get_settings,
        now: Callable[[], datetime] = utcnow,
        renderer: Callable[[str], str] = qr_svg,
    ) -> None:
        self._store = store
        self._client_factory = client_factory
        self._settings_provider = settings_provider
        self._now = now
        self._renderer = renderer
        self._sessions: dict[str, _QrSession] = {}

    # -- 扫码 -------------------------------------------------------------
    async def start(self) -> dict[str, Any]:
        """生成一张二维码：返回 ``session_id`` / ``qr_svg`` / ``payload`` / ``expires_at``。"""
        settings = self._settings_provider()
        client = self._client_factory(timeout=settings.skland.request_timeout_seconds)
        try:
            login = await client.generate_scan()
        finally:
            await client.aclose()

        self._prune()
        session_id = uuid.uuid4().hex
        moment = self._now()
        self._sessions[session_id] = _QrSession(
            session_id=session_id, scan_id=login.scan_id, created_at=moment
        )
        expires_at = moment + timedelta(seconds=SKLAND_QR_LOOP_TTL_SECONDS)
        return {
            "session_id": session_id,
            "qr_svg": self._renderer(login.scan_url),
            "payload": login.scan_url,
            "expires_at": iso_z(expires_at),
            "poll_interval_ms": SKLAND_QR_POLL_INTERVAL_MS,
        }

    async def poll(self, session_id: str) -> dict[str, Any]:
        """轮询一次；确认后走完后半段并落库。四态见模块文档字符串。"""
        self._prune()
        session = self._sessions.get(session_id)
        if session is None:
            raise AppError(
                ErrorCode.SKLAND_QR_SESSION_NOT_FOUND,
                "扫码会话不存在或已过期，请重新生成二维码",
                {"session_id": session_id},
            )
        if session.polls >= SKLAND_QR_MAX_POLLS:
            self._sessions.pop(session_id, None)
            return {"state": "expired", "message": "二维码已失效，请重新生成"}

        session.polls += 1
        settings = self._settings_provider()
        client = self._client_factory(timeout=settings.skland.request_timeout_seconds)
        try:
            status, scan_code, message = await client.scan_status(session.scan_id)
            if status == 100:
                return {"state": "waiting"}
            if status == 102:
                self._sessions.pop(session_id, None)
                return {"state": "expired", "message": message or "二维码已失效，请重新生成"}
            if status != 0 or not scan_code:
                return {
                    "state": "failed",
                    "message": f"森空岛返回了意外的状态（status={status}）：{message}",
                }
            try:
                result = await self._complete_bind(client, scan_code)
            except AppError as exc:
                return {"state": "failed", "message": exc.message}
        finally:
            await client.aclose()

        self._sessions.pop(session_id, None)
        return {
            "state": "succeeded",
            "account": _role_wire(result.account),
            "roles": [_role_wire(role) for role in result.roles],
        }

    async def _complete_bind(self, client: SklandClient, scan_code: str) -> _BindResult:
        """``scanCode`` → 凭据 → 角色 → 落库。"""
        token = await client.token_by_scan_code(scan_code)
        code = await client.grant(token)
        credential = await client.generate_cred(code)
        roles = await client.binding(credential)
        if not roles:
            raise AppError(
                ErrorCode.SKLAND_UPSTREAM_FAILED,
                "这个森空岛账号没有绑定《明日方舟》角色，无法同步练度",
                {"endpoint": "binding"},
            )
        moment = self._now()
        await self._store.save_credential(credential, bound_at=moment)
        await self._store.replace_roles(roles)
        account = next((role for role in roles if role.is_default), roles[0])
        logger.info(
            "森空岛绑定成功：角色数=%d 默认角色尾号=%s", len(roles), account.uid[-6:]
        )
        return _BindResult(account=account, roles=tuple(roles))

    # -- 手动兜底 ---------------------------------------------------------
    async def bind_manual(self, *, cred: str, token: str) -> list[BindingRole]:
        """手动粘贴 ``cred`` / 签名密钥：先用 ``binding`` 验一次再落库。"""
        settings = self._settings_provider()
        client = self._client_factory(timeout=settings.skland.request_timeout_seconds)
        credential = PlainCredential(cred=cred.strip(), token=token.strip())
        try:
            roles = await client.binding(credential)
        finally:
            await client.aclose()
        if not roles:
            raise AppError(
                ErrorCode.SKLAND_UPSTREAM_FAILED,
                "这个森空岛账号没有绑定《明日方舟》角色，无法同步练度",
                {"endpoint": "binding"},
            )
        moment = self._now()
        await self._store.save_credential(credential, bound_at=moment)
        await self._store.replace_roles(roles)
        logger.info("森空岛手动绑定成功：角色数=%d", len(roles))
        return list(roles)

    async def unbind(self) -> bool:
        """解绑：删凭据与角色行；干员练度保留（它是账号数据）。"""
        removed = await self._store.clear()
        self._sessions.clear()
        return removed

    # -- 内部 -------------------------------------------------------------
    def _prune(self) -> None:
        now = self._now()
        ttl = timedelta(seconds=SKLAND_QR_LOOP_TTL_SECONDS)
        for session_id in [
            key for key, session in self._sessions.items() if now - session.created_at > ttl
        ]:
            self._sessions.pop(session_id, None)

    #: 供测试与诊断：当前存活会话数。
    @property
    def active_sessions(self) -> int:
        self._prune()
        return len(self._sessions)


def _role_wire(role: BindingRole | Any) -> dict[str, Any]:
    return {
        "uid": str(getattr(role, "uid", "")),
        "app_code": str(getattr(role, "app_code", "arknights")),
        "channel_master_id": getattr(role, "channel_master_id", None),
        "nickname": getattr(role, "nickname", None),
        "level": getattr(role, "level", None),
        "is_default": bool(getattr(role, "is_default", False)),
        "synced_at": iso_z(getattr(role, "synced_at", None)),
    }
