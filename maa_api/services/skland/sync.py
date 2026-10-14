"""森空岛同步编排（M42 规格 §4.5）：取数、护栏、写库、摘要。

一次同步的全部步骤都在 :meth:`SklandSyncService.sync` 里，**同步执行**（实测单次取数
141 ms，不值得为它做异步任务与进度轮询）：

1. 频率护栏：全局互斥锁（忙时 409 ``SKLAND_SYNC_BUSY``）+ 冷却
   （``SKLAND_MIN_SYNC_INTERVAL_SECONDS`` 秒内重复提交回 429 ``RATE_LIMITED``）；
2. 读凭据（没绑定 409 ``SKLAND_NOT_BOUND``）→ ``auth/refresh`` 换新签名密钥并落库；
3. ``binding`` 取角色（多角色取 ``isDefault``）→ ``player/info`` 取全量干员；
4. **数量护栏**：``len(chars) < SKLAND_MIN_CHARS`` 时整轮不写库、不收缩所有权
   （半截响应把整箱干员标成「未拥有」是这套流程里代价最大的事故）；
5. 归一（:mod:`maa_api.services.skland.mapping`）→ ``apply_skland_sync`` 单事务写库；
6. 记一条 ``skland`` 识别记录、刷新角色的 ``synced_at``、返回摘要。

失败处理：任何异常都会记进 ``last_error``（状态接口可见）后原样抛出，**绑定关系不动**
——上游偶发失败不该让用户重新扫码。``last_error`` 与 ``last_sync`` 存在进程内存里，
服务重启后归零（库里仍有 ``synced_at`` 与识别记录，页面不会因此显示假数据）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from maa_api.db.models import utcnow
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.account.box import OperatorBoxService
from maa_api.services.account.rules import iso_z
from maa_api.services.skland.client import SklandClient, SklandCredential
from maa_api.services.skland.mapping import to_operator_updates
from maa_api.services.skland.store import SklandStore, StoredRole
from maa_api.settings import (
    SKLAND_MIN_CHARS,
    SKLAND_MIN_SYNC_INTERVAL_SECONDS,
    Settings,
    get_settings,
)

if TYPE_CHECKING:  # pragma: no cover - 只为注解；运行时走 default_catalog 的延迟 import
    from maa_api.services.account.capture import OperatorMeta

__all__ = ["SKLAND_QR_LOOP_TTL_SECONDS", "SklandSyncService", "default_catalog"]

logger = logging.getLogger(__name__)

#: 扫码会话有效期（秒）。上游 ``scan_status`` 失效（``status=102``）时会话立即作废，
#: 这个 TTL 只是兜底，免得内存里堆积无人认领的会话。
SKLAND_QR_LOOP_TTL_SECONDS = 180


def default_catalog() -> dict[str, OperatorMeta]:
    """本机 ``battle_data.json`` → ``{char_id: OperatorMeta}``（读不到给空表）。

    **延迟 import ``capture``**：它为了 ``Message`` 常量在模块级 import
    ``maa_api.core.enums``，而本模块被 ``main.py`` 装配路径拉进来；顶层 import 会让
    ``import maa_api.main`` 连带把内核层拉进内存（``tests/api/test_app_skeleton.py`` 的
    隔离门禁会红，这正是 M36 的 ``scan.py`` 踩过的同一个坑）。
    """
    from maa_api.services.account.capture import load_operator_catalog

    return load_operator_catalog()


class SklandSyncService:
    """森空岛 → 账号干员练度的同步服务。"""

    def __init__(
        self,
        *,
        store: SklandStore,
        box: OperatorBoxService,
        client_factory: Callable[..., SklandClient] = SklandClient,
        catalog_loader: Callable[[], dict[str, OperatorMeta]] = default_catalog,
        settings_provider: Callable[[], Settings] = get_settings,
        now: Callable[[], datetime] = utcnow,
    ) -> None:
        self._store = store
        self._box = box
        self._client_factory = client_factory
        self._catalog_loader = catalog_loader
        self._settings_provider = settings_provider
        self._now = now
        self._lock = asyncio.Lock()
        self._last_finished_at: datetime | None = None
        self._last_sync: dict[str, Any] | None = None
        self._last_error: str | None = None
        self._catalog: dict[str, OperatorMeta] | None = None

    # -- 对外 -------------------------------------------------------------
    async def sync(self) -> dict[str, Any]:
        """跑一轮同步；返回摘要（见模块文档字符串第 6 步）。"""
        settings = self._settings_provider()
        # 先看忙态再看冷却：正在跑的那一轮比冷却更能解释「为什么现在不行」。
        if self._lock.locked():
            raise AppError(
                ErrorCode.SKLAND_SYNC_BUSY, "已有一次森空岛同步在进行中，等它跑完再试"
            )
        self._check_cooldown()
        started = time.monotonic()
        async with self._lock:
            try:
                summary = await self._run(settings, started)
            except Exception as exc:  # noqa: BLE001 - 记进状态接口后原样抛
                self._last_error = getattr(exc, "message", None) or str(exc)
                raise
            self._last_error = None
            self._last_sync = summary
            self._last_finished_at = self._now()
            return summary

    async def status(self) -> dict[str, Any]:
        """绑定与同步状态（``GET /api/skland/status`` 的数据源）。"""
        settings = self._settings_provider()
        bound = await self._store.has_credential()
        roles = await self._store.list_roles() if bound else []
        account = _pick_role(roles)
        return {
            "bound": bound,
            "key_configured": self._store.key_configured,
            "account": _role_wire(account) if account is not None else None,
            "roles": [_role_wire(role) for role in roles],
            "last_sync_at": iso_z(account.synced_at) if account is not None else None,
            "last_sync": self._last_sync,
            "last_error": self._last_error,
            "min_sync_interval_seconds": SKLAND_MIN_SYNC_INTERVAL_SECONDS,
            "min_chars": SKLAND_MIN_CHARS,
            "auto_sync_enabled": settings.skland.auto_sync_interval_minutes > 0,
            "auto_sync_interval_minutes": settings.skland.auto_sync_interval_minutes,
        }

    # -- 内部 -------------------------------------------------------------
    def _check_cooldown(self) -> None:
        if self._last_finished_at is None:
            return
        elapsed = (self._now() - self._last_finished_at).total_seconds()
        if elapsed < SKLAND_MIN_SYNC_INTERVAL_SECONDS:
            wait = int(SKLAND_MIN_SYNC_INTERVAL_SECONDS - elapsed) + 1
            raise AppError(
                ErrorCode.RATE_LIMITED,
                f"同步太频繁了：{SKLAND_MIN_SYNC_INTERVAL_SECONDS} 秒内只允许一次，请 {wait} 秒后再试",
                {"retry_after_seconds": wait},
                headers={"Retry-After": str(wait)},
            )

    async def _run(self, settings: Settings, started: float) -> dict[str, Any]:
        stored = await self._store.load_credential()
        if stored is None:
            raise AppError(
                ErrorCode.SKLAND_NOT_BOUND, "还没有绑定森空岛账号，先在账号页扫码绑定"
            )
        moment = self._now()

        client = self._client_factory(
            timeout=settings.skland.request_timeout_seconds,
            # 代理只能显式配（`app.proxy`）；客户端一律 `trust_env=False`，不读系统代理。
            proxy=settings.proxy or None,
        )
        try:
            credential = SklandCredential(cred=stored.cred, token=stored.token)
            try:
                refreshed = await client.refresh(credential)
            except AppError as exc:
                # 续期只是「换一份更新的签名密钥」，不是同步的前置条件：偶发失败（网络抖动、
                # 上游限流）不该让整轮同步失败。真过期时下一步的签名调用会自己报 401。
                logger.warning("森空岛密钥续期失败（%s），改用已存的密钥继续", exc.code)
            else:
                await self._store.touch_credential(refreshed, refreshed_at=moment)
                credential = refreshed

            roles = await client.binding(credential)
            if not roles:
                raise AppError(
                    ErrorCode.SKLAND_UPSTREAM_FAILED,
                    "森空岛账号里没有绑定《明日方舟》角色",
                    {"endpoint": "binding"},
                )
            await self._store.replace_roles(roles)
            role = _pick_role(await self._store.list_roles())
            if role is None:
                raise AppError(
                    ErrorCode.SKLAND_UPSTREAM_FAILED,
                    "森空岛账号里没有绑定《明日方舟》角色",
                    {"endpoint": "binding"},
                )

            data = await client.player_info(credential, role.uid)
            chars = data.get("chars")
            if not isinstance(chars, list):
                raise AppError(
                    ErrorCode.SKLAND_UPSTREAM_FAILED,
                    "森空岛返回的干员数据形状不对（没有 chars 列表）",
                    {"endpoint": "player_info"},
                )
            if len(chars) < SKLAND_MIN_CHARS:
                raise AppError(
                    ErrorCode.SKLAND_UPSTREAM_FAILED,
                    f"这一轮只拿到 {len(chars)} 名干员（低于护栏 {SKLAND_MIN_CHARS}），整轮不写库",
                    {
                        "endpoint": "player_info",
                        "chars": len(chars),
                        "min_chars": SKLAND_MIN_CHARS,
                    },
                )

            updates, dropped = to_operator_updates(
                chars,
                catalog=self._catalog_map(),
                char_info_map=data.get("charInfoMap"),
                equipment_info_map=data.get("equipmentInfoMap"),
            )
            applied = await self._box.apply_skland_sync(
                updates, clear_missing=True, synced_at=moment
            )
            written = int(applied["created"]) + int(applied["updated"]) + int(
                applied["ownership_cleared"]
            )
            await self._box.record_skland_sync(item_count=written, synced_at=moment)
            await self._store.mark_synced(role.uid, moment)
        finally:
            await client.aclose()

        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "森空岛同步完成：角色=%s 干员=%d 丢弃字段=%d 新建=%d 更新=%d 未变=%d 清所有权=%d 耗时=%dms",
            role.uid[-6:],
            applied["total"],
            dropped,
            applied["created"],
            applied["updated"],
            applied["unchanged"],
            applied["ownership_cleared"],
            duration_ms,
        )
        return {
            "status": "succeeded",
            "role": _role_wire(role),
            "synced_at": iso_z(moment),
            "total": int(applied["total"]),
            "created": int(applied["created"]),
            "updated": int(applied["updated"]),
            "unchanged": int(applied["unchanged"]),
            "ownership_cleared": int(applied["ownership_cleared"]),
            "dropped_invalid": dropped,
            "duration_ms": duration_ms,
        }

    def _catalog_map(self) -> dict[str, OperatorMeta]:
        if self._catalog is None:
            self._catalog = self._catalog_loader() or {}
        return self._catalog


def _pick_role(roles: list[StoredRole]) -> StoredRole | None:
    """多角色取 ``is_default``，没有就取第一个（上游顺序即绑定顺序）。"""
    for role in roles:
        if role.is_default:
            return role
    return roles[0] if roles else None


def _role_wire(role: StoredRole) -> dict[str, Any]:
    return {
        "uid": role.uid,
        "app_code": role.app_code,
        "channel_master_id": role.channel_master_id,
        "nickname": role.nickname,
        "level": role.level,
        "is_default": role.is_default,
        "synced_at": iso_z(role.synced_at),
    }
