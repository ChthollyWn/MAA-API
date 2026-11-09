"""M88 T4：刷图取数与合并（规格 §4.5.1–§4.5.2）。

- ``load_events``：当期活动 → ``EventInfo``（PRTS 活动页里的代币、掉落、商店与截止时间）；
- ``read_depot``：仓库计数（``item_id`` 与名字都建索引，键用 ``_key``）；
- ``medicine_pool``：``item_table.json`` 的 ``apSupplies`` + ``items`` 与仓库数量 → 用药池；
- ``merge``：常驻关 + 活动关 + 商店 + 累计截止预算 → ``FarmingModel``。

外部依赖全部由调用方注入；测试用假对象，不联网。说明文字沿用 ``FarmingInputs.notes``
这条通道（``merge`` 把「MAA 还不能自动导航」等活动级说明追加进去，服务统一收口）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from maa_api.services.account import planner
from maa_api.services.account.planner import FarmingInputs, StageOption
from maa_api.services.economy.model import (
    EventInfo,
    FarmingModel,
    FarmingRequest,
    MedicineItem,
    MedicinePool,
    ShopGood,
)
from maa_api.services.prts_parse.activity import parse_activity_page
from maa_api.services.prts_parse.event_shop import (
    parse_event_shop,
    parse_token_drops,
    regular_token,
)

__all__ = [
    "load_events",
    "medicine_pool",
    "merge",
    "read_depot",
]

#: 仓库读不到时的说明。
_DEPOT_FAILED_NOTE = "仓库数据读取失败，库存按 0 计"
#: 常量表读不到时的说明。
_MEDICINE_FAILED_NOTE = "没有读到理智药的恢复量，方案里不计用药"


async def load_events(
    *, activities: Any, prts: Any, now: datetime
) -> tuple[list[EventInfo], list[dict[str, Any]], list[str]]:
    """当期活动 → ``EventInfo``。返回 ``(活动, 来源信封, notes)``；单个活动失败只写 notes 并跳过。"""
    notes: list[str] = []
    report = await activities.current(detail="full")
    reason = getattr(report, "fallback_reason", None)
    if reason:
        notes.append(f"当期活动没有取到（{reason}），只算常驻关与已有代币")
        return [], [], notes

    events: list[EventInfo] = []
    sources: list[dict[str, Any]] = []
    for activity in report.activities:
        if activity.end_at is not None and activity.end_at <= now:
            continue
        try:
            batch = await prts.wikitext([activity.page])
        except Exception:  # noqa: BLE001 - 单个活动取不到不该让整份方案失败
            notes.append(f"活动「{activity.name}」的页面读不到，没有计入活动关与商店")
            continue
        page = batch.pages[0] if getattr(batch, "pages", ()) else None
        if page is None or getattr(page, "missing", False) or not (page.content or "").strip():
            notes.append(f"活动「{activity.name}」的页面读不到，没有计入活动关与商店")
            continue
        content = page.content
        try:
            parsed = parse_activity_page(content, page_title=page.title)
            shop = parse_event_shop(content)
            token = shop.token or regular_token(content)
            drops = parse_token_drops(content, token) if token else {}
        except Exception:  # noqa: BLE001 - 解析口径变了也只跳过这一个活动
            notes.append(f"活动「{activity.name}」的页面读不到，没有计入活动关与商店")
            continue

        if not shop.goods and not drops:
            notes.append(
                f"活动「{activity.name}」的页面没有商店小节，也没有常规代币掉落，活动没有计入"
            )
            continue
        if not shop.goods:
            notes.append(f"活动「{activity.name}」的页面没有商店小节，商店没有计入")

        events.append(
            EventInfo(
                activity=page.title,
                token=token,
                end_at=activity.end_at or parsed.end_at,
                exchange_end_at=parsed.exchange_end_at,
                navigable=activity.navigation == "available",
                drops=drops,
                shop=shop.goods,
                stage_ids={
                    stage.code: stage.stage_id
                    for stage in activity.stages
                    if stage.stage_id
                },
            )
        )
        sources.extend(ref.as_dict() for ref in batch.source_refs())
    return events, sources, notes


async def read_depot(depot: Any) -> tuple[dict[str, int], list[str]]:
    """``depot.list_items(limit=1000, offset=0)`` → ``{item_id 与 name: 数量}``；读不到按 0 计。"""
    try:
        listing = await depot.list_items(limit=1000, offset=0)
        rows = listing.get("items") if isinstance(listing, Mapping) else None
    except Exception:  # noqa: BLE001 - 读不到仓库就按「都没备齐」算
        return {}, [_DEPOT_FAILED_NOTE]
    counts: dict[str, int] = {}
    for item in rows or ():
        if not isinstance(item, Mapping):
            continue
        try:
            count = int(item.get("count") or 0)
        except (TypeError, ValueError):
            continue
        if item.get("item_id"):
            counts[planner._key(item["item_id"])] = count
        if item.get("name"):
            counts[planner._key(item["name"])] = count
    return counts, []


async def medicine_pool(
    *,
    gamedata: Any,
    depot_counts: Mapping[str, int],
    use_medicine: bool,
    sources: list[dict[str, Any]] | None = None,
) -> MedicinePool:
    """``item_table.json`` 的 ``apSupplies`` + ``items`` 与仓库数量 → 用药池；``counted_sanity`` 恒为 0。"""
    try:
        data = await gamedata.excel("item_table.json")
        payload = data.data
    except Exception:  # noqa: BLE001 - 取不到常量表就按没有药处理
        return _medicine_fallback()

    items = payload.get("items") if isinstance(payload, Mapping) else None
    supplies = payload.get("apSupplies") if isinstance(payload, Mapping) else None
    if not isinstance(items, Mapping) or not isinstance(supplies, Mapping):
        return _medicine_fallback()

    ap_by_name: dict[str, int] = {}
    for item_id, supply in supplies.items():
        item = items.get(item_id)
        if not isinstance(item, Mapping) or item.get("itemType") != "AP_SUPPLY":
            continue
        if not isinstance(supply, Mapping):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        try:
            ap = int(supply.get("ap"))
        except (TypeError, ValueError):
            continue
        ap_by_name[name] = ap

    medicine_items = [
        MedicineItem(name=name, ap=ap_by_name[name], count=_depot_count(depot_counts, name))
        for name in sorted(ap_by_name)
        if _depot_count(depot_counts, name) > 0
    ]
    owned_sanity = sum(item.ap * item.count for item in medicine_items)

    if use_medicine:
        if medicine_items:
            note = (
                "仓库不提供每瓶理智药的到期时间，天数按不用药估算；要让内核吃 48 小时内过期的药，"
                "把 Fight 的 expiring_medicine 设为 999"
            )
        else:
            note = (
                "仓库没有理智药；expiring_medicine 设为 999，内核吃药时按到期先后处理，"
                "天数仍按不用药估算"
            )
        expiring_medicine = 999
    else:
        note = "没有用药：用户没有同意用理智药"
        if not medicine_items:
            note = "没有用药：用户没有同意用理智药；仓库没有理智药"
        expiring_medicine = 0

    if sources is not None:
        sources.append(data.source_ref(title="item_table.json").as_dict())

    return MedicinePool(
        items=medicine_items,
        owned_sanity=owned_sanity,
        counted_sanity=0,
        scope="no_expiry_data",
        expiring_medicine=expiring_medicine,
        note=note,
    )


def _medicine_fallback() -> MedicinePool:
    return MedicinePool(
        items=[],
        owned_sanity=0,
        counted_sanity=0,
        scope="no_expiry_data",
        expiring_medicine=0,
        note=_MEDICINE_FAILED_NOTE,
    )


def _depot_count(depot_counts: Mapping[str, int], name: str) -> int:
    """仓库计数按原名与 ``_key`` 两种键都查一次。"""
    raw = depot_counts.get(name, depot_counts.get(planner._key(name), 0))
    try:
        return max(int(raw or 0), 0)
    except (TypeError, ValueError):
        return 0


def merge(
    inputs: FarmingInputs,
    *,
    depot: Mapping[str, int],
    events: Sequence[EventInfo],
    request: FarmingRequest,
    medicine: MedicinePool,
    now: datetime,
) -> FarmingModel:
    """纯数据合并：常驻关 + 活动关 + 商店 + 累计截止预算（含 ``medicine.counted_sanity``）。"""
    options = list(inputs.options)
    by_code: dict[str, StageOption] = {}
    for option in inputs.options:
        if option.stage_code:
            by_code.setdefault(option.stage_code, option)

    event_of: dict[str, str] = {}
    deadline_of: dict[str, datetime] = {}
    tokens_of: dict[str, tuple[str, int]] = {}
    navigable_of: dict[str, bool] = {option.stage_id: True for option in options}

    for event in events:
        if not event.navigable and not request.include_unnavigable:
            inputs.notes.append(
                f"活动「{event.activity}」MAA 还不能自动导航，活动关没有计入"
            )
            excluded = {option.stage_id for option in options if option.stage_code in event.drops}
            if excluded:
                options = [option for option in options if option.stage_id not in excluded]
                for stage_id in excluded:
                    navigable_of.pop(stage_id, None)
            continue
        for code, drop in event.drops.items():
            option = by_code.get(code)
            if option is not None:
                stage_id = option.stage_id
            else:
                stage_id = event.stage_ids.get(code) or f"{event.activity}:{code}"
                option = StageOption(stage_id=stage_id, stage_code=code, ap_cost=drop.ap_cost)
                options.append(option)
                by_code[code] = option
            event_of[stage_id] = event.activity
            if event.end_at is not None:
                deadline_of[stage_id] = event.end_at
            navigable_of[stage_id] = event.navigable
            if event.token:
                tokens_of[stage_id] = (event.token, drop.tokens)

    wanted = _wanted_keys(inputs)
    shop: list[ShopGood] = []
    for event in events:
        for parsed in event.shop:
            key = planner._key(parsed.item)
            if key not in wanted:
                continue
            if any(marker in parsed.item for marker in planner.REPORT_ONLY_NAMES):
                continue
            shop.append(
                ShopGood(
                    activity=event.activity,
                    token=event.token or "",
                    item=parsed.item,
                    key=key,
                    bundle=parsed.bundle,
                    price=parsed.price,
                    stock=parsed.stock,
                    section=parsed.section,
                    unit_sanity=_unit_sanity(inputs.options, key),
                )
            )

    deadline_budgets: dict[datetime, float] = {}
    for event in events:
        if event.end_at is None:
            continue
        horizon = event.end_at
        if request.window_days is not None:
            horizon = min(horizon, now + timedelta(days=float(request.window_days)))
        days = max((horizon - now).total_seconds() / 86400, 0.0)
        deadline_budgets[horizon] = (
            float(request.current_sanity)
            + days * planner.SANITY_PER_DAY
            + float(medicine.counted_sanity)
        )

    return FarmingModel(
        needs=dict(inputs.needs),
        depot={planner._key(key): int(value) for key, value in depot.items()},
        display=dict(inputs.display),
        options=options,
        event_of=event_of,
        deadline_of=deadline_of,
        navigable_of=navigable_of,
        tokens_of=tokens_of,
        recipes=list(inputs.recipes),
        shop=shop,
        owned_tokens=dict(request.owned_tokens),
        deadline_budgets=sorted(deadline_budgets.items(), key=lambda item: item[0]),
        now=now,
    )


def _wanted_keys(inputs: FarmingInputs) -> set[str]:
    """需求或配方涉及的材料键集合（商店只收这些材料）。"""
    wanted = set(inputs.needs)
    for recipe in inputs.recipes:
        wanted.add(recipe.product)
        wanted.update(key for key, _ in recipe.materials)
    return wanted


def _unit_sanity(options: Sequence[StageOption], key: str) -> float | None:
    """候选关明细里该材料的最低单件理智；取不到为 ``None``。"""
    values: list[float] = []
    for option in options:
        for detail in option.details:
            if planner._key(detail.get("item_name") or "") != key:
                continue
            try:
                values.append(float(detail["sanity_per_item"]))
            except (KeyError, TypeError, ValueError):
                continue
    return min(values) if values else None
