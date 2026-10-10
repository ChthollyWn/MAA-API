"""账号数据概要读取器（规格 §7.8）：把干员与仓库的概要拼成注入段要的形状。

干员的 `summary()` 给的是「总数 / 精二数 / 识别时间 / 是否过期」，注入段要的是
「识别到多少名干员、精二多少、什么时候识别的」——两者一一对应，这里只做翻译与合并。
"""

from __future__ import annotations

from typing import Any, Callable

from maa_api.settings import Settings, get_settings

__all__ = ["AccountSummaryReader", "default_account_summary_reader"]


class AccountSummaryReader:
    """给 `KnowledgeService` 用的只读门面（有 `async summary()` 即可）。"""

    def __init__(
        self,
        *,
        box: Any = None,
        depot: Any = None,
        settings_provider: Callable[[], Settings] = get_settings,
    ) -> None:
        self._box = box
        self._depot = depot
        self._settings_provider = settings_provider

    async def summary(self) -> dict[str, Any]:
        """干员与仓库的概要；某一边读不到就那一半按「未识别」处理。"""
        return {
            "operators": await self._operators(),
            "depot": await self._depot_summary(),
        }

    async def _operators(self) -> dict[str, Any]:
        box = self._box
        if box is None:
            from maa_api.services.account.box import OperatorBoxService
            from maa_api.db import session as db_session

            box = OperatorBoxService(db_session.session_factory)
        try:
            raw = await box.summary()
        except Exception:  # noqa: BLE001 - 注入段读不到就如实说「尚未识别」
            return {"recognized": False}
        recognized_at = raw.get("recognized_at")
        return {
            "recognized": bool(recognized_at),
            "total": raw.get("total"),
            "elite2": raw.get("elite_two", raw.get("elite2")),
            "recognized_at": recognized_at,
            "stale": bool(raw.get("stale")),
            "pending_diffs": raw.get("pending_diffs"),
        }

    async def _depot_summary(self) -> dict[str, Any]:
        depot = self._depot
        if depot is None:
            from maa_api.services.account.depot import DepotService
            from maa_api.db import session as db_session

            depot = DepotService(db_session.session_factory)
        try:
            raw = await depot.summary()
        except Exception:  # noqa: BLE001
            return {"recognized": False}
        recognized_at = raw.get("recognized_at")
        return {
            "recognized": bool(recognized_at),
            "total": raw.get("total"),
            "recognized_at": recognized_at,
            "stale": bool(raw.get("stale")),
        }


#: 进程内共享的默认门面。
_DEFAULT: AccountSummaryReader | None = None


def default_account_summary_reader() -> AccountSummaryReader:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = AccountSummaryReader()
    return _DEFAULT
