"""M88 资源策略师：刷图规划的数据形状（规格 §4.2）。

这里只放数据类：求解器（``solver``）、取数与合并（``data``）、服务（``service``）共用。
``TokenDrop`` 与 ``ParsedShopGood`` 定义在 :mod:`maa_api.services.prts_parse.event_shop`
（T2 不依赖 economy 包），这里导入并再导出。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

from maa_api.services.account.planner import Recipe, StageOption
from maa_api.services.prts_parse.event_shop import ParsedShopGood, TokenDrop

__all__ = [
    "EventInfo",
    "FarmingModel",
    "FarmingRequest",
    "MedicineItem",
    "MedicinePool",
    "ParsedShopGood",
    "Recipe",
    "ShopGood",
    "StageOption",
    "TokenDrop",
]


@dataclass(frozen=True, slots=True)
class ShopGood:
    """活动商店里的一项商品（``merge`` 时从 ``ParsedShopGood`` 映射过来）。"""

    activity: str                  # 活动页标题
    token: str                     # 代币名，如 “未来瞻望镜”
    item: str                      # 物品显示名
    key: str                       # _key(item)
    bundle: int                    # 每份数量（「×5000」→ 5000，缺省 1）
    price: int                     # 每份代币价
    stock: int | None              # 份数上限；∞ 为 None
    section: str                   # 「金色阶段」「无限库存区」；没有分区标题时写 ""
    unit_sanity: float | None      # 该材料的最低单件理智；merge 时从候选关明细取最小值，取不到为 None


@dataclass(slots=True)
class EventInfo:
    """一个当期活动：代币、截止时间、能否导航、掉落与商店解析结果。"""

    activity: str
    token: str | None
    end_at: datetime | None
    exchange_end_at: datetime | None
    navigable: bool
    drops: dict[str, TokenDrop]                 # 关卡代号 → 代币掉落
    shop: tuple[ParsedShopGood, ...]            # 解析出的原始商品；merge 时再映射成 ShopGood
    stage_ids: dict[str, str]                   # 关卡代号 → stage_id


@dataclass(frozen=True, slots=True)
class FarmingRequest:
    """``optimize_farming`` 的领域输入（工具层参数的映射）。"""

    materials: Mapping[str, int] | None
    plan_id: str | None
    use_depot: bool = True
    current_sanity: int = 0
    window_days: float | None = None
    use_medicine: bool = False
    include_events: bool = True
    include_unnavigable: bool = False
    owned_tokens: Mapping[str, int] = field(default_factory=dict)
    server: str | None = None


@dataclass(slots=True)
class FarmingModel:
    """求解器的输入：常驻关 + 活动关 + 商店 + 累计截止预算。"""

    needs: dict[str, int]
    depot: dict[str, int]
    display: dict[str, str]
    options: list[StageOption]                 # 含活动关；活动关的 rates 可以为空
    event_of: dict[str, str]                   # stage_id → 活动页标题（常驻关不在里面）
    deadline_of: dict[str, datetime]           # stage_id → 活动结束时间
    navigable_of: dict[str, bool]              # stage_id → MAA 能否导航
    tokens_of: dict[str, tuple[str, int]]      # stage_id → (代币名, 每次代币)
    recipes: list[Recipe]
    shop: list[ShopGood]
    owned_tokens: dict[str, int]
    deadline_budgets: list[tuple[datetime, float]]   # 按截止时间升序的累计预算
    now: datetime


@dataclass(frozen=True, slots=True)
class MedicineItem:
    """仓库里的一种理智药（折合理智与瓶数）。"""

    name: str
    ap: int
    count: int


@dataclass(slots=True)
class MedicinePool:
    """用药池：仓库药的参考信息；本工具不把药折算进天数。"""

    items: list[MedicineItem]
    owned_sanity: int
    counted_sanity: int            # 本工具计入天数的药理智；仓库没有到期时间，恒为 0
    scope: str                     # "no_expiry_data"
    expiring_medicine: int         # 建议写进 Fight 的值：use_medicine 时 999，否则 0
    note: str
