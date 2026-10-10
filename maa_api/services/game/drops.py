"""掉落率领域层（规格 §6.5、§6.6）：从企鹅物流的整表 matrix 里筛材料 / 关卡。

一次查询只取三张表——整表 matrix、`/stages`、`/items`——都在 ``static`` 档缓存里，按单个
材料或关卡查询时从整表里筛，不额外请求（规格 §6.6）。

规则（规格 §6.5）：

- ``times = 0`` 的行剔除（防除零）；``times < 100`` 标 ``small_sample``，排在样本足够的行
  之后，并且不参与 ``top_rates`` 的推荐；
- 排序键：``small_sample`` 升序 → ``sanity_per_item``（单件期望理智）升序，缺理智估计的
  排在次键最后；同档按样本数降序保证结果稳定；
- 关卡 id 的 ``_perm``（常驻化）/ ``_rep``（复刻）后缀在**基础 id 也出现在 `/stages` 里**
  时对回基础 id（实测三者 ``code`` 相同），否则原样显示；同一个变体组里同一材料的多次统计
  只留样本数最多的那一行；
- 掉率没有本地兜底（§3.5）：matrix 取不到就抛错，``/stages`` 与 ``/items`` 取不到只降级
  对应字段组并写进 ``unavailable``。

来源信封注明许可 CC BY-NC 4.0（``PenguinData.source_ref``）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, Sequence

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.envelope import SourceRef, build_envelope
from maa_api.services.external.penguin import PenguinClient, PenguinData
from maa_api.settings import Settings, get_settings

__all__ = [
    "MAX_LIMIT",
    "SMALL_SAMPLE_TIMES",
    "DropRow",
    "DropService",
    "default_drop_service",
]

#: 样本数阈值：低于它标「样本少」，不参与排序推荐（规格 §6.5）。
SMALL_SAMPLE_TIMES = 100

#: `item_rates` / `stage_rates` 的 limit 上限（规格 §6.5：1–20，默认 8）。
MAX_LIMIT = 20

#: 变体后缀（实测：`_perm` 常驻化、`_rep` 复刻；与基础 id 的 `code` 相同）。
_VARIANTS = ("_perm", "_rep")

#: 取不到 `/stages` 时缺的字段组（进信封的 `unavailable`）。
_STAGE_GROUPS = ("stage_code", "ap_cost", "open")

#: 可以降级的错误：只影响对应的字段组（规格 §3.5）。
_DEGRADABLE = frozenset(
    {
        ErrorCode.EXTERNAL_SOURCE_DISABLED,
        ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
        ErrorCode.EXTERNAL_SOURCE_RATE_LIMITED,
    }
)


@dataclass(frozen=True, slots=True)
class DropRow:
    """一行掉率统计（规格 §6.5 的每行字段）。"""

    stage_id: str
    stage_code: str | None
    item_id: str
    item_name: str | None
    times: int
    quantity: int
    rate: float | None  # quantity / times
    ap_cost: int | None
    sanity_per_item: float | None  # ap_cost / rate
    small_sample: bool  # times < 100
    started_at: str | None  # 本地日期 YYYY-MM-DD
    ended_at: str | None  # null = 至今
    open: bool | None

    def as_dict(self) -> dict[str, Any]:
        """wire 形状；不四舍五入，`rate` 与 `sanity_per_item` 是原样的除法结果。"""
        return {
            "stage_id": self.stage_id,
            "stage_code": self.stage_code,
            "item_id": self.item_id,
            "item_name": self.item_name,
            "times": self.times,
            "quantity": self.quantity,
            "rate": self.rate,
            "ap_cost": self.ap_cost,
            "sanity_per_item": self.sanity_per_item,
            "small_sample": self.small_sample,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "open": self.open,
        }


@dataclass(frozen=True, slots=True)
class _Tables:
    """一次查询取到的三张表与来源元信息。"""

    matrix: tuple[Mapping[str, Any], ...]
    stages: tuple[Mapping[str, Any], ...]
    #: `/items` 取不到时是 ``None``：只有 matrix 里的 itemId 可用。
    items: tuple[Mapping[str, Any], ...] | None
    refs: tuple[SourceRef, ...]
    unavailable: tuple[str, ...]


class DropService:
    """掉率的领域服务；``penguin`` 与设置来源可注入（测试与多来源装配）。"""

    def __init__(
        self,
        *,
        penguin: PenguinClient | None = None,
        settings_provider: Callable[[], Settings] = get_settings,
    ) -> None:
        self._penguin = penguin
        self._settings_provider = settings_provider

    @property
    def penguin(self) -> PenguinClient:
        """企鹅物流客户端；缺省用同一个设置来源建一个（底层共享默认 `ExternalClient`）。"""
        if self._penguin is None:
            self._penguin = PenguinClient(settings_provider=self._settings_provider)
        return self._penguin

    # -- 对外查询 ---------------------------------------------------------
    async def item_rates(
        self,
        item: str,
        *,
        limit: int = 8,
        include_closed: bool = False,
        server: str | None = None,
    ) -> dict[str, Any]:
        """按材料查掉率表；`item` 可以是名称或 itemId（规格 §6.5）。

        :raises AppError: `GAME_DATA_NOT_FOUND`（解析不到材料）、`INVALID_PARAMETER`
            （多候选或 limit 越界）、外部来源的 `EXTERNAL_*`。
        """
        limit = _check_limit(limit)
        server = _server_or_default(server, self._settings_provider())
        tables = await self._load(server=server, include_closed=include_closed)
        record = self._resolve_item_record(item, tables)
        rows = self._rows(tables, server=server)
        item_id = str(record.get("itemId") or "")
        selected = _sort_rows(
            _dedupe_variants(row for row in rows if row.item_id == item_id)
        )
        return {
            "query": item,
            "item": _item_brief(record),
            "rows": [row.as_dict() for row in selected[:limit]],
            "rows_total": len(selected),
            "include_closed": include_closed,
            "server": server,
            **_envelope(tables),
        }

    async def stage_rates(
        self,
        stage: str,
        *,
        limit: int = 8,
        include_closed: bool = False,
        server: str | None = None,
    ) -> dict[str, Any]:
        """按关卡查掉率表；`stage` 可以是代号（`R8-11`）或 stageId（`main_08-13`）。"""
        limit = _check_limit(limit)
        server = _server_or_default(server, self._settings_provider())
        tables = await self._load(server=server, include_closed=include_closed)
        stages_by_id = _index(tables.stages, "stageId")
        if not stages_by_id:
            raise _stages_unavailable()
        chosen, group = _resolve_stage(stage, stages_by_id)
        rows = self._rows(tables, server=server)
        selected = _rows_of_group(rows, group, stages_by_id)
        return {
            "query": stage,
            "stage": _stage_brief(chosen, stages_by_id=stages_by_id, server=server),
            "rows": [row.as_dict() for row in selected[:limit]],
            "rows_total": len(selected),
            "include_closed": include_closed,
            "server": server,
            **_envelope(tables),
        }

    async def top_rates(
        self,
        item_name: str,
        *,
        limit: int = 5,
        server: str | None = None,
    ) -> list[dict[str, Any]]:
        """`get_item` 用的掉率前 N：只算样本足够的行，按单件期望理智升序。

        材料不在企鹅物流的表里时返回空列表——`get_item` 已经用 PRTS 定位了材料，
        不该因为企鹅物流没收录就让整个工具失败。
        """
        limit = _check_limit(limit)
        server = _server_or_default(server, self._settings_provider())
        tables = await self._load(server=server, include_closed=False)
        try:
            record = self._resolve_item_record(item_name, tables)
        except AppError:
            return []
        rows = self._rows(tables, server=server)
        item_id = str(record.get("itemId") or "")
        selected = _sort_rows(
            _dedupe_variants(
                row
                for row in rows
                if row.item_id == item_id and not row.small_sample
            )
        )
        return [row.as_dict() for row in selected[:limit]]

    async def stage_drop_rates(
        self,
        stage_id: str,
        *,
        limit: int = 5,
        server: str | None = None,
    ) -> list[dict[str, Any]]:
        """`get_stage` 的掉率组：一关的全部掉落行（样本不足的照常返回并标记）。"""
        limit = _check_limit(limit)
        server = _server_or_default(server, self._settings_provider())
        tables = await self._load(server=server, include_closed=False)
        stages_by_id = _index(tables.stages, "stageId")
        if not stages_by_id:
            raise _stages_unavailable()
        raw_id = _find_stage_id(stage_id, stages_by_id)
        if raw_id is None:
            raise AppError(
                ErrorCode.GAME_DATA_NOT_FOUND,
                f"企鹅物流的关卡表里没有「{stage_id}」",
                {"query": stage_id},
            )
        _, group = _group_for(stages_by_id[raw_id], stages_by_id)
        rows = self._rows(tables, server=server)
        selected = _rows_of_group(rows, group, stages_by_id)
        return [row.as_dict() for row in selected[:limit]]

    # -- 取数与装配 -------------------------------------------------------
    async def _load(self, *, server: str, include_closed: bool) -> _Tables:
        """取三张表；matrix 是必需的，`/stages` 与 `/items` 只降级对应字段组。"""
        client = self.penguin
        matrix_data = await client.matrix(server=server, include_closed=include_closed)
        collected: list[PenguinData] = [matrix_data]
        unavailable: list[str] = []

        try:
            stages_data = await client.stages(server=server)
        except AppError as exc:
            if exc.code not in _DEGRADABLE:
                raise
            stages: tuple[Mapping[str, Any], ...] = ()
            unavailable.extend(_STAGE_GROUPS)
        else:
            stages = _records(stages_data.data)
            collected.append(stages_data)

        try:
            items_data = await client.items()
        except AppError as exc:
            if exc.code not in _DEGRADABLE:
                raise
            items: tuple[Mapping[str, Any], ...] | None = None
            unavailable.append("item_name")
        else:
            items = _records(items_data.data)
            collected.append(items_data)

        return _Tables(
            matrix=_records(_matrix_payload(matrix_data.data)),
            stages=stages,
            items=items,
            refs=_source_refs(collected),
            unavailable=tuple(unavailable),
        )

    def _resolve_item_record(self, item: str, tables: _Tables) -> Mapping[str, Any]:
        """解析材料查询词；`/items` 取不到时用 matrix 里的 itemId 兜底。"""
        records = (
            tables.items
            if tables.items is not None
            else _matrix_item_records(tables.matrix)
        )
        try:
            return _resolve_item(item, records)
        except AppError as exc:
            if tables.items is None and exc.code is ErrorCode.GAME_DATA_NOT_FOUND:
                raise _items_unavailable() from exc
            raise

    def _rows(self, tables: _Tables, *, server: str) -> list[DropRow]:
        return _build_rows(
            tables.matrix,
            stages_by_id=_index(tables.stages, "stageId"),
            items_by_id=_index(tables.items or (), "itemId"),
            server=server,
            now_ms=time.time() * 1000,
        )


# ---------------------------------------------------------------------------
# 行构造与排序
# ---------------------------------------------------------------------------
def _build_rows(
    matrix_rows: Sequence[Mapping[str, Any]],
    *,
    stages_by_id: Mapping[str, Mapping[str, Any]],
    items_by_id: Mapping[str, Mapping[str, Any]],
    server: str,
    now_ms: float,
) -> list[DropRow]:
    """整表 matrix → 行；`times = 0` 与缺字段的行直接剔除。"""
    rows: list[DropRow] = []
    for raw in matrix_rows:
        times = _int(raw.get("times"))
        if times is None or times <= 0:
            continue  # times = 0 剔除（防除零）
        item_id = _text(raw.get("itemId"))
        raw_stage_id = _text(raw.get("stageId"))
        if item_id is None or raw_stage_id is None:
            continue
        quantity = _int(raw.get("quantity"))
        quantity = 0 if quantity is None or quantity < 0 else quantity
        stage_id = _display_stage_id(raw_stage_id, stages_by_id)
        stage = stages_by_id.get(stage_id) or stages_by_id.get(raw_stage_id)
        item = items_by_id.get(item_id)
        rate = quantity / times
        ap_cost = _int(stage.get("apCost")) if stage is not None else None
        rows.append(
            DropRow(
                stage_id=stage_id,
                stage_code=_text(stage.get("code")) if stage is not None else None,
                item_id=item_id,
                item_name=_text(item.get("name")) if item is not None else None,
                times=times,
                quantity=quantity,
                rate=rate,
                ap_cost=ap_cost,
                # 掉率为 0 时没有「单件期望理智」（除零保护）。
                sanity_per_item=ap_cost / rate if ap_cost is not None and rate > 0 else None,
                small_sample=times < SMALL_SAMPLE_TIMES,
                started_at=_local_date(_int(raw.get("start"))),
                ended_at=_local_date(_int(raw.get("end"))),
                open=_stage_open(stage, server=server, now_ms=now_ms),
            )
        )
    return rows


def _sort_rows(rows: Iterable[DropRow]) -> list[DropRow]:
    """样本足够的在前，再按单件期望理智升序；缺理智估计的排次键最后。"""
    return sorted(
        rows,
        key=lambda row: (
            row.small_sample,
            row.sanity_per_item is None,
            row.sanity_per_item if row.sanity_per_item is not None else 0.0,
            -row.times,
            row.stage_id,
        ),
    )


def _dedupe_variants(rows: Iterable[DropRow]) -> list[DropRow]:
    """同一（显示）关卡的同一材料只留样本最多的一行（`_perm` / `_rep` 与基础 id 合并）。"""
    best: dict[tuple[str, str], DropRow] = {}
    order: list[tuple[str, str]] = []
    for row in rows:
        key = (row.stage_id, row.item_id)
        current = best.get(key)
        if current is None:
            best[key] = row
            order.append(key)
        elif row.times > current.times:
            best[key] = row
    return [best[key] for key in order]


def _rows_of_group(
    rows: Sequence[DropRow],
    group: Sequence[str],
    stages_by_id: Mapping[str, Mapping[str, Any]],
) -> list[DropRow]:
    """一关（含变体）的掉率行：按显示 id 归并后排序。"""
    display_ids = {_display_stage_id(raw_id, stages_by_id) for raw_id in group}
    return _sort_rows(_dedupe_variants(row for row in rows if row.stage_id in display_ids))


# ---------------------------------------------------------------------------
# 查询词解析
# ---------------------------------------------------------------------------
def _resolve_item(term: str, records: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """itemId 精确匹配 → 名称精确匹配 → 去空白后的唯一子串；多候选报 `INVALID_PARAMETER`。"""
    query = _key(term)
    if not query:
        raise _item_not_found(term)
    for record in records:
        if _key(record.get("itemId") or "") == query:
            return record
    for record in records:
        if _key(record.get("name") or "") == query:
            return record
    matches = [record for record in records if query in _key(record.get("name") or "")]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"「{term}」匹配到多个材料，请写完整名称或 itemId",
            {"query": term, "candidates": [_item_brief(record) for record in matches]},
        )
    raise _item_not_found(term)


def _resolve_stage(
    term: str, stages_by_id: Mapping[str, Mapping[str, Any]]
) -> tuple[Mapping[str, Any], tuple[str, ...]]:
    """stageId 精确匹配 → 代号精确匹配 → 代号子串；同一关卡的变体归成一组。"""
    query = _key(term)
    stages = list(stages_by_id.values())
    for stage in stages:
        if query and _key(stage.get("stageId") or "") == query:
            return _group_for(stage, stages_by_id)
    exact_code = [stage for stage in stages if query and _key(stage.get("code") or "") == query]
    matches = exact_code or [
        stage for stage in stages if query and query in _key(stage.get("code") or "")
    ]
    if not matches:
        raise AppError(
            ErrorCode.GAME_DATA_NOT_FOUND,
            f"企鹅物流的关卡表里没有「{term}」",
            {"query": term},
        )
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for stage in matches:
        groups.setdefault(_variant_key(_text(stage.get("stageId")) or ""), []).append(stage)
    if len(groups) > 1:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"「{term}」匹配到多个关卡，请写完整代号或 stageId",
            {
                "query": term,
                "candidates": [
                    _stage_candidate(_pick_variant(group), stages_by_id)
                    for group in groups.values()
                ],
            },
        )
    return _group_for(matches[0], stages_by_id)


def _group_for(
    stage: Mapping[str, Any], stages_by_id: Mapping[str, Mapping[str, Any]]
) -> tuple[Mapping[str, Any], tuple[str, ...]]:
    """一个关卡的变体组：选中（基础 > `_perm` > `_rep`）与组内全部原始 id。"""
    key = _variant_key(_text(stage.get("stageId")) or "")
    group = [
        member
        for member in stages_by_id.values()
        if _variant_key(_text(member.get("stageId")) or "") == key
    ]
    ordered = sorted(
        group,
        key=lambda member: (
            _variant_rank(_text(member.get("stageId")) or ""),
            _text(member.get("stageId")) or "",
        ),
    )
    return _pick_variant(ordered), tuple(_text(member.get("stageId")) or "" for member in ordered)


def _pick_variant(group: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    return min(
        group,
        key=lambda member: (
            _variant_rank(_text(member.get("stageId")) or ""),
            _text(member.get("stageId")) or "",
        ),
    )


def _find_stage_id(
    stage_id: str, stages_by_id: Mapping[str, Mapping[str, Any]]
) -> str | None:
    query = _key(stage_id)
    for raw_id in stages_by_id:
        if _key(raw_id) == query:
            return raw_id
    return None


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _item_brief(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "item_id": _text(record.get("itemId")),
        "name": _text(record.get("name")),
        "rarity": _int(record.get("rarity")),
        "item_type": _text(record.get("itemType")),
    }


def _stage_brief(
    stage: Mapping[str, Any],
    *,
    stages_by_id: Mapping[str, Mapping[str, Any]],
    server: str,
) -> dict[str, Any]:
    raw_id = _text(stage.get("stageId")) or ""
    return {
        "stage_id": _display_stage_id(raw_id, stages_by_id),
        "code": _text(stage.get("code")),
        "ap_cost": _int(stage.get("apCost")),
        "open": _stage_open(stage, server=server, now_ms=time.time() * 1000),
        "zone_id": _text(stage.get("zoneId")),
        "stage_type": _text(stage.get("stageType")),
    }


def _stage_candidate(
    stage: Mapping[str, Any], stages_by_id: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    raw_id = _text(stage.get("stageId")) or ""
    return {"stage_id": _display_stage_id(raw_id, stages_by_id), "code": _text(stage.get("code"))}


def _envelope(tables: _Tables) -> dict[str, Any]:
    """来源信封：许可说明在 `SourceRef.title` 里；`region_note` 恒为 null。

    企鹅物流的数据本来就按 `server` 取（§4.3 要求 `server` 跟随 `channel.server`），
    不是「国服数据」，所以不写外服提示。
    """
    return build_envelope(
        list(tables.refs), unavailable=list(tables.unavailable), region_note=None
    )


def _source_refs(datas: Iterable[PenguinData]) -> tuple[SourceRef, ...]:
    """三张表的出处按 (kind, url, title) 去重：许可说明相同，列一条就够。"""
    refs: list[SourceRef] = []
    seen: set[tuple[str, str, str]] = set()
    for data in datas:
        ref = data.source_ref()
        marker = (ref.kind, ref.url or "", ref.title or "")
        if marker in seen:
            continue
        seen.add(marker)
        refs.append(ref)
    return tuple(refs)


def _matrix_item_records(matrix_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """`/items` 取不到时的兜底：matrix 里出现过的 itemId（没有名字）。"""
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in matrix_rows:
        item_id = _text(raw.get("itemId"))
        if item_id is None or item_id in seen:
            continue
        seen.add(item_id)
        records.append({"itemId": item_id})
    return records


def _records(payload: Any) -> tuple[Mapping[str, Any], ...]:
    """list 响应 → 只留对象元素（访问层已经确认过顶层是数组）。"""
    if not isinstance(payload, Sequence):
        return ()
    return tuple(item for item in payload if isinstance(item, Mapping))


def _matrix_payload(payload: Any) -> Any:
    """整表 matrix 的响应是 ``{"matrix": [...]}``（访问层已经确认过形状）。"""
    return payload.get("matrix") if isinstance(payload, Mapping) else None


def _index(
    records: Sequence[Mapping[str, Any]], key: str
) -> dict[str, Mapping[str, Any]]:
    """按某个字段建索引；缺字段的跳过（重复键以第一条为准）。"""
    indexed: dict[str, Mapping[str, Any]] = {}
    for record in records:
        value = _text(record.get(key))
        if value is not None and value not in indexed:
            indexed[value] = record
    return indexed


def _check_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"limit 必须在 1–{MAX_LIMIT} 之间",
            {"limit": limit},
        )
    return limit


def _server_or_default(server: str | None, settings: Settings) -> str:
    """`server` 缺省跟 `settings.channel.server`（规格 §4.3）；合法性由客户端校验。"""
    value = settings.channel.server if server is None else server
    return str(value).strip().upper()


def _variant_key(stage_id: str) -> str:
    """变体组键：去掉 `_perm` / `_rep` 后缀（实测三者是同一关）。"""
    for suffix in _VARIANTS:
        if stage_id.endswith(suffix):
            return stage_id[: -len(suffix)]
    return stage_id


def _variant_rank(stage_id: str) -> int:
    """变体优先级：基础 id 0、`_perm` 1、`_rep` 2、其它 3。"""
    for index, suffix in enumerate(_VARIANTS, start=1):
        if stage_id.endswith(suffix):
            return index
    return 0


def _display_stage_id(stage_id: str, stages_by_id: Mapping[str, Any]) -> str:
    """展示用 stageId：基础 id 也在 `/stages` 里时对回基础 id，否则原样。"""
    base = _variant_key(stage_id)
    if base != stage_id and base in stages_by_id:
        return base
    return stage_id


def _stage_open(
    stage: Mapping[str, Any] | None, *, server: str, now_ms: float
) -> bool | None:
    """关卡是否开放：看 `existence.<server>` 的 `openTime` / `closeTime`。

    `exist` 是「这个服有没有这关」（实测 CN 全为 true），不是「现在开不开」；没有该服的
    记录时返回 ``None``（未知）。
    """
    if stage is None:
        return None
    existence = stage.get("existence")
    entry = existence.get(server) if isinstance(existence, Mapping) else None
    if not isinstance(entry, Mapping) or "exist" not in entry:
        return None
    if not entry.get("exist"):
        return False
    close = _int(entry.get("closeTime"))
    if close is not None and close <= now_ms:
        return False
    opened = _int(entry.get("openTime"))
    if opened is not None and opened > now_ms:
        return False
    return True


def _local_date(milliseconds: int | None) -> str | None:
    """毫秒时间戳 → 本机时区的 `YYYY-MM-DD`；`null` 或解析不了时返回 ``None``。"""
    if milliseconds is None:
        return None
    try:
        return datetime.fromtimestamp(milliseconds / 1000).astimezone().strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None


def _key(value: Any) -> str:
    """比较键：去掉全部空白并统一大小写（「固源岩 组」与 `固源岩组` 等价）。"""
    return "".join(str(value).split()).casefold()


def _text(value: Any) -> str | None:
    """非空字符串或 ``None``（数字 id 也接受）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    return None


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.isdigit() or (cleaned.startswith("-") and cleaned[1:].isdigit()):
            return int(cleaned)
    return None


def _item_not_found(term: str) -> AppError:
    return AppError(
        ErrorCode.GAME_DATA_NOT_FOUND,
        f"企鹅物流的材料表里没有「{term}」",
        {"query": term},
    )


def _items_unavailable() -> AppError:
    return AppError(
        ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
        "企鹅物流的材料表取不到，无法按名字解析材料；可以改用 itemId 查",
        {"kind": "penguin", "endpoint": "items"},
    )


def _stages_unavailable() -> AppError:
    return AppError(
        ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
        "企鹅物流的关卡表取不到，无法解析关卡；稍后再试",
        {"kind": "penguin", "endpoint": "stages"},
    )


#: 进程内共享的默认门面（`get_item` / `get_stage` 缺省用它）。
_DEFAULT: DropService | None = None


def default_drop_service() -> DropService:
    """默认掉率服务；进程内单例，底层用默认 `ExternalClient`。"""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = DropService()
    return _DEFAULT
