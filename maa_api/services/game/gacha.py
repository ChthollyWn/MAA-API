"""卡池（规格 §8.2、§4.3）：SMW 取数 + 本机 ``battle_data.json`` 的星级区分。

一次 SMW 查询拿最近 20 个卡池（``[[寻访开启时间cn::+]]``，打印 ``寻访名cn`` /
``寻访开启时间cn`` / ``寻访关闭时间cn`` / ``出率提升干员``，按开启时间倒序），再按时间筛：

- ``status="current"``：开始 ≤ 现在 < 关闭；
- ``status="recent"``：最近若干个**已关闭**的（倒序）。

``出率提升干员`` 里 6★ 与 5★ 混在一起，用本机 ``battle_data.json`` 的 ``rarity`` 区分
（规格 §8.2：星级是稳定事实，标 ``source=resource``）。实测 ``battle_data.json`` 的
``rarity`` 是**星数**（``银灰=6``、``克洛丝=3``，而 PRTS 页面上的 ``稀有度`` 是从 0 开始的）；
同一个名字可能同时有干员与 ``trap_`` 记录（``阿米娅`` 的陷阱记录是 1★），取「非 trap 记录里
的最高星」。

规格没写死、由本模块裁决的实现细节（报告里会列全）：

1. ``page`` 是 SMW 命中的页面标题，实测卡池数据挂在 ``文件:xxx.jpg`` 这类文件页上
   （``{{寻访信息}}`` 模板），所以它带 ``文件:`` 前缀——那是数据所在页，不是主命名空间的
   剧情/设定页；
2. ``pools_total`` 是该 ``status`` 命中的全部条数，不受 ``limit`` 影响（§4.4）；
3. 两个时间有一个解析不出来就整条丢掉：既判不了在开，也判不了已关闭；
4. 查不到星级的干员**原样保留**在 ``up_five`` 里、``rarity`` 为 ``null``、``source`` 也为
   ``null``（不冒充本机资源）；只有 6★ 进 ``up_six``，其余（5★ 及零星的 1–4★）都进
   ``up_five``，``rarity`` 字段照实给；
5. PRTS 取不到时降级成空列表 + ``unavailable: ["pools"]``（§4.8：可以降级的工具不抛错）；
6. 外服结果带 ``region_note``（§4.3），口径同 ``stages._region_note``。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.envelope import build_envelope
from maa_api.services.external.prts import PrtsClient, first_timestamp
from maa_api.services.game.activities import FOREIGN_REGION_NOTE, is_cn_client
from maa_api.services.resource_index import iter_resource_roots
from maa_api.settings import Settings, get_settings

__all__ = [
    "GACHA_CONDITION",
    "GACHA_PRINTOUTS",
    "MAX_LIMIT",
    "SMW_LIMIT",
    "STATUS_CURRENT",
    "STATUS_RECENT",
    "GachaPool",
    "GachaService",
    "UpOperator",
    "default_gacha_service",
]

#: SMW 条件（规格 §8.2）：只要有开启时间的卡池。
GACHA_CONDITION = "[[寻访开启时间cn::+]]"

#: SMW 打印属性（规格 §8.2）。
GACHA_PRINTOUTS = ("寻访名cn", "寻访开启时间cn", "寻访关闭时间cn", "出率提升干员")

#: 排序属性（规格 §8.2：按开启时间倒序）。
GACHA_SORT = "寻访开启时间cn"

#: SMW 一次取多少个卡池（规格 §8.2：最近 20 个；SMW 的 limit 上限是 500）。
SMW_LIMIT = 20

#: `limit` 的上限（规格 §8.2 的工具参数是 1–10）。
MAX_LIMIT = 10

STATUS_CURRENT = "current"
STATUS_RECENT = "recent"
STATUSES = (STATUS_CURRENT, STATUS_RECENT)

#: 6★ 的门槛（`battle_data.json` 的 `rarity` 就是星数）。
UP_SIX = 6

#: 本机干员资料的路径（相对资源根）。
_BATTLE_DATA = Path("battle_data.json")

#: `出率提升干员` 可能一条里塞多个名字：逐个拆开。
_UP_SEPARATORS = re.compile(r"[,，、;；]")

#: 可以降级的外部错误：返回空列表 + `unavailable`，不把整个调用变成错误（§3.5、§4.8）。
_DEGRADABLE = frozenset(
    {
        ErrorCode.EXTERNAL_SOURCE_DISABLED,
        ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
        ErrorCode.EXTERNAL_SOURCE_RATE_LIMITED,
        ErrorCode.EXTERNAL_RESPONSE_INVALID,
    }
)


@dataclass(frozen=True, slots=True)
class UpOperator:
    """UP 干员：名字来自 PRTS，星级来自本机资源（查不到时 ``rarity`` / ``source`` 都是空）。"""

    name: str
    rarity: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "rarity": self.rarity,
            "source": "resource" if self.rarity is not None else None,
        }


@dataclass(frozen=True, slots=True)
class GachaPool:
    """一个卡池（规格 §8.2 的返回形状）。"""

    name: str
    page: str
    start_at: datetime
    end_at: datetime
    status: str
    up_six: tuple[UpOperator, ...] = ()
    up_five: tuple[UpOperator, ...] = ()
    source: str = "prts"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "page": self.page,
            "start_at": self.start_at.isoformat(),
            "end_at": self.end_at.isoformat(),
            "status": self.status,
            "source": self.source,
            "up_six": [item.as_dict() for item in self.up_six],
            "up_five": [item.as_dict() for item in self.up_five],
        }


class GachaService:
    """卡池的领域服务；``prts`` / ``battle_data_path`` / ``now_provider`` 都可注入。

    ``settings_provider`` 有两个用处（与 ``StageService`` / ``ActivityService`` 同一形状）：

    - 没给 ``client_type`` 时按它判定渠道（注入段就是这么调的）；
    - 判定为外服时**整段不注入**：``pools()`` 不带 ``client_type`` 的调用返回空列表 +
      ``region_note``（规格 §4.3「当期卡池：不注入；``get_gacha_pools`` 仍可查，但结果带
      ``region_note``」）。工具层总是显式传 ``client_type``，所以外服照常查得到卡池。
    """

    def __init__(
        self,
        *,
        prts: PrtsClient | None = None,
        battle_data_path: str | Path | None = None,
        now_provider: Callable[[], datetime] | None = None,
        settings_provider: Callable[[], Settings] = get_settings,
    ) -> None:
        self._prts = prts
        self._battle_path = Path(battle_data_path) if battle_data_path else None
        self._now_provider = now_provider
        self._settings_provider = settings_provider
        self._rarities: dict[str, int] | None = None

    @property
    def prts(self) -> PrtsClient:
        """PRTS 客户端；缺省现建一个（底层走进程内单例的连接）。"""
        return self._prts or PrtsClient()

    # -- 取数 -------------------------------------------------------------
    async def pools(
        self,
        *,
        status: str = STATUS_CURRENT,
        limit: int = 5,
        client_type: str | None = None,
    ) -> dict[str, Any]:
        """当期或近期卡池；``client_type`` 只影响外服的 ``region_note`` 与外服不注入（§4.3）。"""
        self._validate(status=status, limit=limit)
        resolved = self._resolved_client(client_type)
        if client_type is None and not is_cn_client(resolved):
            # 注入段（不带 client_type）在外服整段不插：外服卡池进度与国服不同。
            return self._empty(status=status, limit=limit, client_type=resolved)
        try:
            result = await self.prts.ask(
                [GACHA_CONDITION],
                printouts=list(GACHA_PRINTOUTS),
                sort=GACHA_SORT,
                order="desc",
                limit=SMW_LIMIT,
                combine="and",
            )
        except AppError as exc:
            if exc.code in _DEGRADABLE:
                return self._empty(
                    status=status, limit=limit, client_type=resolved, unavailable=["pools"]
                )
            raise

        now = self._now()
        rows = [row for row in (_row(page) for page in self.prts.smw_pages(result)) if row]
        selected = [row for row in rows if _matches(row, status=status, now=now)]
        selected.sort(key=lambda row: row["start_at"], reverse=True)

        rarities = self._rarity_map()
        pools = [
            _pool(row, status=status, rarities=rarities) for row in selected[:limit]
        ]
        payload: dict[str, Any] = {
            "query": {"status": status, "limit": limit},
            "pools": [pool.as_dict() for pool in pools],
            "pools_total": len(selected),
        }
        payload.update(
            build_envelope(
                [result.source_ref()],
                region_note=_region_note(resolved),
            )
        )
        return payload

    def _empty(
        self,
        *,
        status: str,
        limit: int,
        client_type: str | None,
        unavailable: Sequence[str] = (),
    ) -> dict[str, Any]:
        """空结果：外服不注入与 PRTS 取不到共用同一个形状（§4.3、§4.8）。"""
        payload: dict[str, Any] = {
            "query": {"status": status, "limit": limit},
            "pools": [],
            "pools_total": 0,
        }
        payload.update(
            build_envelope(
                unavailable=list(unavailable), region_note=_region_note(client_type)
            )
        )
        return payload

    @staticmethod
    def _validate(*, status: str, limit: int) -> None:
        if status not in STATUSES:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"status 只能是 {' 或 '.join(STATUSES)}，收到 {status}",
                {"status": str(status)[:40]},
            )
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"limit 必须是 1–{MAX_LIMIT} 之间的整数",
                {"limit": str(limit)[:40]},
            )
        if not 1 <= limit <= MAX_LIMIT:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"limit 必须在 1–{MAX_LIMIT} 之间，收到 {limit}",
                {"limit": limit},
            )

    def _now(self) -> datetime:
        if self._now_provider is not None:
            return self._now_provider()
        return datetime.now().astimezone()

    def _resolved_client(self, client_type: str | None) -> str | None:
        """没给 ``client_type`` 时按设置判渠道；读不到设置按国服处理（缺省即国服）。"""
        if client_type is not None:
            return client_type
        try:
            return self._settings_provider().channel.client_type
        except Exception:  # noqa: BLE001 - 渠道只影响提示语与外服不注入
            return None

    # -- 本机星级 ---------------------------------------------------------
    def _rarity_map(self) -> dict[str, int]:
        """``干员名（casefold） → 星数``；读不到资源就是空表（星级全为 ``null``）。"""
        if self._rarities is not None:
            return self._rarities
        self._rarities = _load_rarities(self._battle_path)
        return self._rarities


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------
def _row(page: Any) -> dict[str, Any] | None:
    """一条 SMW 结果 → 卡池行；两个时间有一个取不到就返回 ``None``（整条丢掉）。"""
    start = _printout_time(page.printouts.get("寻访开启时间cn"))
    end = _printout_time(page.printouts.get("寻访关闭时间cn"))
    if start is None or end is None:
        return None
    return {
        "name": _first_text(page.printouts.get("寻访名cn")) or page.title,
        "page": page.title,
        "start_at": start,
        "end_at": end,
        "up": _up_names(page.printouts.get("出率提升干员")),
    }


def _matches(row: Mapping[str, Any], *, status: str, now: datetime) -> bool:
    """时间筛选：``current`` = 开始 ≤ 现在 < 关闭；``recent`` = 已关闭。"""
    start = row["start_at"]
    end = row["end_at"]
    if status == STATUS_CURRENT:
        return start <= now < end
    return end <= now


def _pool(
    row: Mapping[str, Any], *, status: str, rarities: Mapping[str, int]
) -> GachaPool:
    """一行 SMW 结果 → :class:`GachaPool`：按本机星级分成 6★ 与其余。"""
    six: list[UpOperator] = []
    five: list[UpOperator] = []
    for name in row["up"]:
        rarity = rarities.get(name.casefold())
        operator = UpOperator(name=name, rarity=rarity)
        (six if rarity == UP_SIX else five).append(operator)
    return GachaPool(
        name=str(row["name"]),
        page=str(row["page"]),
        start_at=row["start_at"],
        end_at=row["end_at"],
        status=status,
        up_six=tuple(six),
        up_five=tuple(five),
    )


def _load_rarities(path: Path | None) -> dict[str, int]:
    """读本机 ``battle_data.json``：``名字 → 星数``（同名取非 trap 记录里的最高星）。"""
    roots: Sequence[Path] = (
        [path] if path is not None else [Path(root) / _BATTLE_DATA for root in iter_resource_roots()]
    )
    payload: Mapping[str, Any] = {}
    for candidate in roots:
        if candidate is None or not candidate.is_file():
            continue
        try:
            loaded = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(loaded, dict) and isinstance(loaded.get("chars"), dict):
            payload = loaded
            break
    best: dict[str, tuple[tuple[int, int], int]] = {}
    for char_id, raw in (payload.get("chars") or {}).items():
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        rarity = raw.get("rarity")
        if not isinstance(name, str) or not name or not isinstance(rarity, int):
            continue
        # 排序键：非 trap 记录优先，其次星数高的优先（实测「阿米娅」也有 1★ 的 trap 记录）。
        rank = (0 if not str(char_id).startswith("trap_") else 1, -rarity)
        key = name.casefold()
        if key not in best or rank < best[key][0]:
            best[key] = (rank, rarity)
    return {name: rarity for name, (_rank, rarity) in best.items()}


def _printout_time(values: Any) -> datetime | None:
    """SMW 时间属性 → 本机时区的 datetime（`first_timestamp` 取 Unix 秒）。"""
    if not isinstance(values, (list, tuple)):
        values = [values]
    seconds = first_timestamp(list(values))
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds).astimezone()


def _first_text(values: Any) -> str | None:
    """SMW 打印属性里的第一个非空字符串。"""
    if not isinstance(values, (list, tuple)):
        values = [values]
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _up_names(values: Any) -> list[str]:
    """`出率提升干员` → 名字列表（一条里塞多个名字时按分隔符拆开、去重保序）。"""
    if not isinstance(values, (list, tuple)):
        values = [values]
    names: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        for part in _UP_SEPARATORS.split(value):
            cleaned = part.strip()
            if cleaned and cleaned not in names:
                names.append(cleaned)
    return names


def _region_note(client_type: str | None) -> str | None:
    """外服提示语（§4.3）；国服（或渠道未知）为 ``None``。口径同 ``stages._region_note``。"""
    return None if is_cn_client(client_type) else FOREIGN_REGION_NOTE


#: 进程内共享的默认门面；工具与注入段都用它。
_DEFAULT: GachaService | None = None


def default_gacha_service() -> GachaService:
    """默认门面（进程内单例）。"""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = GachaService()
    return _DEFAULT
