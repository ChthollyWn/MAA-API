"""M88 资源策略师：把活动关卡与商店纳入的刷图方案（规格 §4.2）。"""

from __future__ import annotations

from maa_api.services.economy.model import (
    FarmingModel,
    FarmingRequest,
    MedicinePool,
    ShopGood,
)
from maa_api.services.economy.service import FarmingOptimizer

__all__ = [
    "FarmingModel",
    "FarmingOptimizer",
    "FarmingRequest",
    "MedicinePool",
    "ShopGood",
]
