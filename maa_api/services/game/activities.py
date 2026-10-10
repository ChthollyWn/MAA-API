"""当期活动（规格 §5.6）：PRTS 优先，多个在开活动全部列出，本机资源包兜底。

流程（国服）：

1. SMW 查询最近 20 个活动，按时间筛出在开的，倒序，最多 5 个（缓存档 ``live``）；
2. 每个活动取活动页 wikitext（``static``），从 ``{{活动信息}}`` 与正文提取代号与 stageId；
3. 代号是重定向时用 ``redirects=1`` 解析页面标题；有 stageId 提示时直接按 ``[[关卡id::…]]``
   反查。SMW 的 OR 条件实测上限是 8，这里按 6 个一块分块；
4. 与本机 ``overview.json`` 关联出 ``levelId``、导航文件与整组关卡表；
5. 第 3–4 步取不到时退回「代号 + 中文名」本地精确匹配，标 ``matched_by``。

两级取数：``summary`` 只到活动页解析（注入段用，代价小）；``full`` 再做 stageId 解析与
本地关联（``get_activity_stages`` 用）。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Callable, Iterable, Mapping, Sequence

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.activity_index import (
    MAX_STAGES,
    ActivityIndex,
    ActivityStage,
    ActivitySnapshot,
    default_activity_index,
)
from maa_api.services.external.envelope import SourceRef
from maa_api.services.external.prts import (
    PrtsClient,
    WikiTextBatch,
    id_condition,
    page_condition,
)
from maa_api.services.prts_parse.activity import ActivityPage, StageRef, parse_activity_page
from maa_api.settings import Settings, get_settings

__all__ = [
    "ACTIVITY_QUERY",
    "report_from_snapshot",
    "CN_CLIENTS",
    "FOREIGN_REGION_NOTE",
    "MAX_ACTIVITIES",
    "SMW_CHUNK",
    "ActivityReport",
    "ActivityService",
    "CurrentActivity",
    "default_activity_service",
    "is_cn_client",
]

#: SMW 的活动查询条件与打印属性（规格 §5.6 第 1 步）。
ACTIVITY_CONDITIONS = ("[[分类:有活动信息的页面]]", "[[!分类:集成战略]]")
ACTIVITY_PRINTOUTS = ("名称缩短", "活动开始时间", "活动结束时间")
ACTIVITY_QUERY = " / ".join(ACTIVITY_CONDITIONS)

#: 一次返回的在开活动数上限（Q13、规格 §4.5）。
MAX_ACTIVITIES = 5

#: SMW 的 OR 条件分块大小。实测 8 个可用、12 个报「查询大小或深度」超限，取 6 留余量。
SMW_CHUNK = 6

#: 国服渠道（Q22、规格 §4.3）。
CN_CLIENTS = frozenset({"Official", "Bilibili"})

#: 外服结果的提示语（规格 §4.3）。
FOREIGN_REGION_NOTE = "国服数据，外服可能尚未实装或数值不同；外服进度与国服不同"

#: 主关代号：``VEC-01``。与 ``activity_index`` 的口径一致（那边是 ``XX-数字``）。
_MAIN_CODE = re.compile(r"^[A-Z]+-\d+$")

#: stageId 前缀：``act3break_01`` → ``act3break``。
def _prefix_of(stage_id: str) -> str:
    return stage_id.split("_", 1)[0]


@dataclass(frozen=True, slots=True)
class StageEntry:
    """活动里的一关：代号、名称、stageId 与本地 levelId。"""

    code: str
    name: str | None
    stage_id: str | None
    level_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "stage_id": self.stage_id,
            "level_id": self.level_id,
        }


@dataclass(frozen=True, slots=True)
class CurrentActivity:
    """一个在开活动（规格 §5.6 的返回形状）。"""

    name: str
    page: str
    start_at: datetime | None
    end_at: datetime | None
    stage_id_prefix: str | None
    stages: tuple[StageEntry, ...]
    navigation: str
    matched_by: str
    source: str
    confidence: str | None = None
    alternatives: tuple[dict[str, Any], ...] = ()
    related: tuple[str, ...] = ()
    resource_updated_at: str | None = None

    @property
    def code_range(self) -> dict[str, Any] | None:
        """主关代号区间；没有主关时为 ``None``。"""
        codes = [stage.code for stage in self.stages if _MAIN_CODE.match(stage.code)]
        if not codes:
            return None
        ordered = sorted(codes, key=_code_sort_key)
        return {"first": ordered[0], "last": ordered[-1], "count": len(ordered)}

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "page": self.page,
            "start_at": self.start_at.isoformat() if self.start_at else None,
            "end_at": self.end_at.isoformat() if self.end_at else None,
            "stage_id_prefix": self.stage_id_prefix,
            "code_range": self.code_range,
            "stages": [stage.as_dict() for stage in self.stages[:MAX_STAGES]],
            "stages_total": len(self.stages),
            "navigation": self.navigation,
            "matched_by": self.matched_by,
            "source": self.source,
        }

    def fallback_dict(self) -> dict[str, Any]:
        """本地兜底时多给 M24 的 `confidence` / `alternatives` / `related`。"""
        payload = self.as_dict()
        payload.update(
            {
                "confidence": self.confidence,
                "alternatives": list(self.alternatives),
                "related": list(self.related),
                "resource_updated_at": self.resource_updated_at,
            }
        )
        return payload


@dataclass(frozen=True, slots=True)
class ActivityReport:
    """一次当期活动查询的结果。"""

    activities: tuple[CurrentActivity, ...]
    sources: tuple[SourceRef, ...] = ()
    fallback_reason: str | None = None
    region_note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """工具与注入段共用的 wire 形状。

        本地兜底的那一条用 :meth:`CurrentActivity.fallback_dict`：规格 §5.6 要求降级结果
        带上 M24 的 `confidence`、`alternatives`、`related` 与 `resource_updated_at`。
        """
        return {
            "activities": [
                item.fallback_dict() if item.source == "resource" else item.as_dict()
                for item in self.activities
            ],
            "fallback_reason": self.fallback_reason,
            "region_note": self.region_note,
        }


def _code_sort_key(code: str) -> tuple[int, int, str]:
    """主关按数字排在前，特殊关排在后（与 ``activity_index`` 同口径）。"""
    if _MAIN_CODE.match(code) is not None:
        return (0, int(code.rsplit("-", 1)[1]), code)
    digits = re.search(r"(\d+)", code)
    return (1, int(digits.group(1)) if digits else 10**6, code)


def is_cn_client(client_type: str | None) -> bool:
    """渠道是否国服（规格 §4.3）。缺省按国服处理。"""
    if not client_type:
        return True
    return client_type in CN_CLIENTS


class ActivityService:
    """当期活动的领域服务；``prts`` 与 ``index`` 可注入（测试与多来源装配）。"""

    def __init__(
        self,
        *,
        prts: PrtsClient | None = None,
        index: ActivityIndex | None = None,
        settings_provider: Callable[[], Settings] = get_settings,
    ) -> None:
        self._prts = prts
        self._index = index
        self._settings_provider = settings_provider

    @property
    def prts(self) -> PrtsClient:
        return self._prts or PrtsClient()

    @property
    def index(self) -> ActivityIndex:
        return self._index or default_activity_index()

    # -- 取数 -------------------------------------------------------------
    async def current(
        self,
        *,
        detail: str = "summary",
        client_type: str | None = None,
        budget_seconds: float | None = None,
    ) -> ActivityReport:
        """当期活动；`:param detail:` 为 ``summary``（只到活动页）或 ``full``（含 stageId 解析）。"""
        if detail not in {"summary", "full"}:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"detail 只能是 summary 或 full，收到 {detail}",
                {"detail": detail},
            )
        client = client_type if client_type is not None else self._settings_provider().channel.client_type
        if not is_cn_client(client):
            return self._fallback(region_note=FOREIGN_REGION_NOTE, reason="外服不使用 PRTS 的当期活动")

        try:
            if budget_seconds is None:
                return await self._from_prts(detail=detail)
            async with asyncio.timeout(budget_seconds):
                return await self._from_prts(detail=detail)
        except TimeoutError:
            return self._fallback(reason="PRTS 取数超过注入预算")
        except AppError as exc:
            return self._fallback(reason=f"PRTS 不可用（{exc.code}）")

    async def _from_prts(self, *, detail: str) -> ActivityReport:
        rows = await self._ask_activities()
        now = datetime.now().astimezone()
        open_rows = [row for row in rows if _is_open(row, now)]
        open_rows.sort(key=lambda row: row["start_at"], reverse=True)
        open_rows = open_rows[:MAX_ACTIVITIES]
        if not open_rows:
            return ActivityReport(activities=())

        sources: list[SourceRef] = []
        activities: list[CurrentActivity] = []
        for row in open_rows:
            loaded = await self._page(row["page"])
            if loaded is not None:
                parsed, batch = loaded
                sources.extend(batch.source_refs())
                activity = await self._build_activity(row, parsed, detail=detail)
            else:
                activity = await self._build_activity(row, None, detail=detail)
            if activity is not None:
                activities.append(activity)
        return ActivityReport(activities=tuple(activities), sources=tuple(sources))

    async def _ask_activities(self) -> list[dict[str, Any]]:
        """SMW 查询最近 20 个活动并换算成时间点。"""
        result = await self.prts.ask(
            list(ACTIVITY_CONDITIONS),
            printouts=list(ACTIVITY_PRINTOUTS),
            sort="活动开始时间",
            order="desc",
            limit=20,
        )
        rows: list[dict[str, Any]] = []
        for page in self.prts.smw_pages(result):
            start = _printout_time(page.printouts.get("活动开始时间"))
            end = _printout_time(page.printouts.get("活动结束时间"))
            rows.append(
                {
                    "name": _first_text(page.printouts.get("名称缩短")) or page.title,
                    "page": page.title,
                    "start_at": start,
                    "end_at": end,
                }
            )
        return [row for row in rows if row["start_at"] is not None and row["end_at"] is not None]

    async def _page(self, title: str) -> tuple[ActivityPage, WikiTextBatch] | None:
        """取活动页并解析；取不到返回 ``None``（这个活动就跳过）。"""
        try:
            batch = await self.prts.wikitext([title])
        except AppError:
            return None
        page = batch.by_title(title)
        if page is None or page.missing or not page.content:
            return None
        return parse_activity_page(page.content, page_title=page.title), batch

    async def _build_activity(
        self, row: Mapping[str, Any], parsed: ActivityPage | None, *, detail: str
    ) -> CurrentActivity | None:
        """把一行 SMW 结果 + 活动页解析结果合成一个 :class:`CurrentActivity`。"""
        name = str(row["name"])
        if parsed is None:
            # 活动页取不到：仍然报名字与时间，但标注「关卡信息未知」。
            return CurrentActivity(
                name=name,
                page=str(row["page"]),
                start_at=row["start_at"],
                end_at=row["end_at"],
                stage_id_prefix=None,
                stages=(),
                navigation="unknown",
                matched_by="none",
                source="prts",
            )
        if _is_roguelike_theme(parsed):
            # 实测（2026-10-10）：`[[!分类:集成战略]]` 拦不住「沉沦者的黑流树海」——
            # 它的分类来自模板转写，SMW 的条件看不到。它的 `{{活动信息}}|类型=集成战略`
            # 是可靠信号，而集成战略主题不是「当期活动」（一开就是几个月）。
            return None
        start = row["start_at"] or parsed.start_at
        end = row["end_at"] or parsed.end_at
        entries, prefix, matched_by = await self._resolve_stages(parsed, detail=detail)
        local = self._local_stages(prefix)
        stages = _merge_stages(entries, local, prefix=prefix)
        navigation = self._navigation(prefix, local)
        return CurrentActivity(
            name=parsed.name or name,
            page=parsed.page_title,
            start_at=start,
            end_at=end,
            stage_id_prefix=prefix,
            stages=stages,
            navigation=navigation,
            matched_by=matched_by,
            source="prts",
        )

    async def _resolve_stages(
        self, parsed: ActivityPage, *, detail: str
    ) -> tuple[list[StageEntry], str | None, str]:
        """解析代号 → stageId；``detail="summary"`` 时只用页面上的信息，不打站点。

        本期前缀按「页面上的 stageId 提示 → PRTS 反查到的 stageId」的顺序定，再拿它把
        结果收敛到本期：同一批代号在往期活动里也有页面（实测 `VEC-SP01` 在 PRTS 上指向
        `act1break_sp01`），不收敛就会把往期的关卡算进本期；本机索引里同名代号同样撞车，
        所以没有前缀时**不猜**，只保留页面自己写的 stageId。
        """
        refs = list(parsed.stages)
        if not refs:
            return [], None, "none"
        prefix = _prefix_from_hints(refs)
        if detail == "summary":
            entries = [StageEntry(code=ref.code, name=None, stage_id=ref.stage_id) for ref in refs]
            return entries, prefix, "stage_page_hint" if prefix else "stage_code"

        resolved = await self._resolve_via_prts(refs)
        if prefix is None:
            prefix = _prefix_from_entries(list(resolved.values()))
        entries: list[StageEntry] = []
        matched_by = "none"
        for ref in refs:
            hit = resolved.get(ref.code)
            in_period = hit is not None and hit.stage_id and (
                prefix is None or _prefix_of(hit.stage_id) == prefix
            )
            if in_period:
                entries.append(hit)
                matched_by = "stage_page_id"
                continue
            entries.append(StageEntry(code=ref.code, name=None, stage_id=ref.stage_id))
        local_entries, _local_prefix, local_matched = _local_match(refs, self.index, prefix=prefix)
        if matched_by == "none" and local_entries:
            matched_by = local_matched
        # 本地能给出 levelId 与名称的，补进去（PRTS 说没有的字段用本地兜底）。
        entries = _enrich_with_local(entries, local_entries)
        return entries, prefix, matched_by

    async def _resolve_via_prts(self, refs: Sequence[StageRef]) -> dict[str, StageEntry]:
        """按 stageId 反查或按代号重定向解析，拿到 ``代号 → StageEntry``。"""
        resolved: dict[str, StageEntry] = {}
        hinted = [ref for ref in refs if ref.stage_id]
        if hinted:
            for chunk in _chunks(hinted, SMW_CHUNK):
                result = await self.prts.ask(
                    [id_condition("关卡id", str(ref.stage_id)) for ref in chunk],
                    printouts=["关卡id", "关卡名称"],
                    limit=50,
                    combine="or",
                )
                by_id = _stage_entries_by_id(self.prts.smw_pages(result))
                for ref in chunk:
                    entry = by_id.get(str(ref.stage_id))
                    if entry is not None:
                        resolved[ref.code] = StageEntry(
                            code=ref.code, name=entry.name, stage_id=entry.stage_id
                        )

        remaining = [ref for ref in refs if ref.code not in resolved]
        if not remaining:
            return resolved
        titles = await self._resolve_titles([ref.code for ref in remaining])
        title_to_code = {title: code for code, title in titles.items()}
        for chunk in _chunks(list(title_to_code), SMW_CHUNK):
            result = await self.prts.ask(
                [page_condition(title) for title in chunk],
                printouts=["关卡id", "关卡名称"],
                limit=50,
                combine="or",
            )
            for page in self.prts.smw_pages(result):
                code = title_to_code.get(page.title)
                if code is None:
                    continue
                stage_id = _first_text(page.printouts.get("关卡id"))
                if not stage_id:
                    continue
                resolved[code] = StageEntry(
                    code=code,
                    name=_first_text(page.printouts.get("关卡名称")),
                    stage_id=stage_id,
                )
        return resolved

    async def _resolve_titles(self, codes: Sequence[str]) -> dict[str, str]:
        """代号 → 页面标题；只写代号的活动页靠它（代号是重定向）。"""
        mapping: dict[str, str] = {}
        for chunk in _chunks(list(codes), 20):
            try:
                batch = await self.prts.wikitext(list(chunk))
            except AppError:
                continue
            for page in batch.pages:
                if page.missing:
                    continue
                if page.redirect_from is not None:
                    mapping[page.redirect_from] = page.title
                else:
                    mapping.setdefault(page.title, page.title)
        return mapping

    # -- 本地兜底 ---------------------------------------------------------
    def _local_stages(self, prefix: str | None) -> list[ActivityStage]:
        if not prefix:
            return []
        try:
            return self.index.stages_of_prefix(prefix)
        except AppError:
            return []

    def _navigation(self, prefix: str | None, stages: Sequence[ActivityStage]) -> str:
        if not prefix:
            return "unknown"
        try:
            snapshot = self.index.snapshot()
        except AppError:
            return "unknown"
        if snapshot.stage_id_prefix == prefix:
            return snapshot.navigation
        # 别的期次：本机导航文件按前缀判存在性，规则同 M24。
        return "available" if _has_navigation(prefix) else "missing"

    def _fallback(self, *, reason: str, region_note: str | None = None) -> ActivityReport:
        """PRTS 不可用或外服：返回 M24 的本地快照（``source=resource``）。"""
        try:
            snapshot = self.index.snapshot()
        except AppError as exc:
            raise AppError(
                ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
                f"PRTS 不可用，本机活动资源也读不到：{exc.message}",
                {"reason": reason},
            ) from exc
        return ActivityReport(
            activities=(_from_snapshot(snapshot),),
            sources=(),
            fallback_reason=reason,
            region_note=region_note,
        )

    async def snapshot_dict(self) -> dict[str, Any]:
        """本地快照（`source=resource`）的工具形状；读不到资源时抛错。"""
        return self.index.snapshot().as_dict()


def report_from_snapshot(
    snapshot: ActivitySnapshot, *, reason: str = "本机资源包推断", region_note: str | None = None
) -> ActivityReport:
    """M24 的本地快照 → 单条 :class:`ActivityReport`（``source=resource``）。

    知识库注入段与工具在没有外部来源时都用它，保证降级口径一致（§3.5、§5.9）。
    """
    return ActivityReport(
        activities=(_from_snapshot(snapshot),),
        sources=(),
        fallback_reason=reason,
        region_note=region_note,
    )


def _from_snapshot(snapshot: ActivitySnapshot) -> CurrentActivity:
    """M24 的本地快照 → :class:`CurrentActivity`（``source=resource``）。"""
    return CurrentActivity(
        name=snapshot.name,
        page="",
        # M24 的快照只给日期：补成当天 00:00 的本地时间点，渲染与 as_dict 才一致。
        start_at=(
            datetime.combine(snapshot.started_at, time.min).astimezone()
            if snapshot.started_at
            else None
        ),
        end_at=None,
        stage_id_prefix=snapshot.stage_id_prefix,
        stages=tuple(
            StageEntry(code=stage.code, name=stage.name, stage_id=stage.stage_id, level_id=stage.level_id)
            for stage in snapshot.stages
        ),
        navigation=snapshot.navigation,
        matched_by=snapshot.matched_by,
        source="resource",
        confidence=snapshot.confidence,
        alternatives=tuple(item.as_dict() for item in snapshot.alternatives),
        related=snapshot.related,
        resource_updated_at=snapshot.resource_updated_at,
    )


#: `{{活动信息}}|类型` 里的集成战略主题标识（实测）。
ROGUELIKE_TYPES = frozenset({"集成战略", "集成战略主题", "肉鸽"})


def _is_roguelike_theme(parsed: ActivityPage) -> bool:
    """是不是集成战略主题页（不是「当期活动」）。"""
    return (parsed.type or "").strip() in ROGUELIKE_TYPES


def _is_open(row: Mapping[str, Any], now: datetime) -> bool:
    """开始 ≤ 现在 < 结束（边界时刻算在开）。"""
    start = row.get("start_at")
    end = row.get("end_at")
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        return False
    return start <= now < end


def _printout_time(values: Any) -> datetime | None:
    """SMW 时间属性 → 本机时区的 datetime。"""
    from maa_api.services.external.prts import first_timestamp

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


def _chunks(items: Sequence[Any], size: int) -> Iterable[list[Any]]:
    for index in range(0, len(items), size):
        yield list(items[index : index + size])


def _stage_entries_by_id(pages: Sequence[Any]) -> dict[str, StageEntry]:
    """SMW 结果 → ``stageId → StageEntry``（代号留空，调用方按请求顺序补）。"""
    mapping: dict[str, StageEntry] = {}
    for page in pages:
        stage_id = _first_text(page.printouts.get("关卡id"))
        if not stage_id:
            continue
        mapping[stage_id] = StageEntry(
            code="", name=_first_text(page.printouts.get("关卡名称")), stage_id=stage_id
        )
    return mapping


def _local_match(
    refs: Sequence[StageRef], index: ActivityIndex, *, prefix: str | None = None
) -> tuple[list[StageEntry], str | None, str]:
    """PRTS 取不到时：用「代号 + 名称」在本机 overview 里精确匹配（M24 规则）。

    ``prefix`` 已知时只在那一期里找：同名代号跨期撞车（``VEC-01`` 在 ``act1break`` 与
    ``act3break`` 里都有），不限定就会挑到往期那条。
    """
    entries: list[StageEntry] = []
    prefixes: list[str] = []
    for ref in refs:
        entry = _local_entry(ref, index, prefix=prefix)
        if entry is None:
            entries.append(StageEntry(code=ref.code, name=None, stage_id=ref.stage_id))
            continue
        entries.append(entry)
        if entry.stage_id:
            prefixes.append(_prefix_of(entry.stage_id))
    prefix = _majority(prefixes)
    matched = "local_code_name" if any(entry.level_id for entry in entries) else "none"
    return entries, prefix, matched


def _local_entry(
    ref: StageRef, index: ActivityIndex, *, prefix: str | None = None
) -> StageEntry | None:
    """单个代号在本机索引里的精确匹配。

    同名代号跨期撞车（``VEC-01``、``VEC-SP01`` 在 ``act1break`` 与 ``act3break`` 里都有），
    所以：有 stageId 就按 stageId 命中；限定了本期前缀就只认那一期；两条都不满足时**不猜**。
    """
    if ref.stage_id:
        try:
            result = index.find_stage(ref.stage_id)
        except AppError:
            return None
        for match in result.get("matches") or []:
            if match.get("stage_id") == ref.stage_id:
                return _entry_of(ref.code, match)
        return None
    if prefix is None:
        return None
    try:
        result = index.find_stage(ref.code)
    except AppError:
        return None
    for match in result.get("matches") or []:
        if _prefix_of(str(match.get("stage_id") or "")) == prefix:
            return _entry_of(ref.code, match)
    return None


def _entry_of(code: str, match: Mapping[str, Any]) -> StageEntry:
    return StageEntry(
        code=code,
        name=match.get("name"),
        stage_id=match.get("stage_id"),
        level_id=match.get("level_id"),
    )


def _enrich_with_local(
    entries: Sequence[StageEntry], local: Sequence[StageEntry]
) -> list[StageEntry]:
    """本机能给的名称、stageId、levelId 补进 PRTS 的结果里。"""
    by_code = {entry.code: entry for entry in local}
    by_stage_id = {entry.stage_id: entry for entry in local if entry.stage_id}
    enriched: list[StageEntry] = []
    for entry in entries:
        local_hit = by_code.get(entry.code) or (
            by_stage_id.get(entry.stage_id) if entry.stage_id else None
        )
        if local_hit is None:
            enriched.append(entry)
            continue
        stage_id = entry.stage_id or local_hit.stage_id
        enriched.append(
            StageEntry(
                code=entry.code,
                name=entry.name or local_hit.name,
                stage_id=stage_id,
                # levelId 只在两条记录是同一关时带上，避免张冠李戴。
                level_id=local_hit.level_id if local_hit.stage_id == stage_id else None,
            )
        )
    return enriched


def _merge_stages(
    entries: Sequence[StageEntry],
    local: Sequence[ActivityStage],
    *,
    prefix: str | None = None,
) -> tuple[StageEntry, ...]:
    """PRTS 的条目 + 本机整组关卡（补上 PRTS 页面没列的 EX/突袭关）。

    本机整组是**本期**的权威名单：前缀不同的条目（往期的同代号页面）丢掉；本机已有同代号
    的条目也丢掉——本地那条带着正确的 stageId 与 levelId，留着 PRTS 的反而会多出一关。
    """
    merged: dict[str, StageEntry] = {}
    local_codes = {stage.code for stage in local}
    for entry in entries:
        if entry.stage_id and prefix and _prefix_of(entry.stage_id) != prefix:
            continue
        if entry.stage_id is None and entry.code in local_codes:
            continue
        key = entry.stage_id or entry.code
        merged[key] = StageEntry(
            code=entry.code,
            name=entry.name,
            stage_id=entry.stage_id,
            level_id=entry.level_id,
        )
    for stage in local:
        existing = merged.get(stage.stage_id)
        if existing is None:
            merged[stage.stage_id] = StageEntry(
                code=stage.code, name=stage.name, stage_id=stage.stage_id, level_id=stage.level_id
            )
            continue
        merged[stage.stage_id] = StageEntry(
            code=existing.code or stage.code,
            name=existing.name or stage.name,
            stage_id=existing.stage_id or stage.stage_id,
            level_id=existing.level_id or stage.level_id,
        )
    ordered = sorted(merged.values(), key=lambda entry: _code_sort_key(entry.code or "ZZZ"))
    return tuple(ordered)


def _prefix_from_hints(refs: Sequence[StageRef]) -> str | None:
    """页面自己写的 stageId 提示里出现最多的前缀（权威，来自活动页的 `{{关卡链接}}`）。"""
    return _majority([_prefix_of(ref.stage_id) for ref in refs if ref.stage_id])


def _prefix_from_entries(entries: Sequence[StageEntry]) -> str | None:
    return _majority([_prefix_of(entry.stage_id) for entry in entries if entry.stage_id])


def _majority(values: Sequence[str]) -> str | None:
    """出现次数最多的值；平票取字典序最小的，保证结果稳定。"""
    if not values:
        return None
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _has_navigation(prefix: str) -> bool:
    """本机 `tasks/Stages/<前缀>.json` 是否存在（只判存在性，不读内容）。"""
    from maa_api.services.activity_index import NAVIGATION_DIRECTORY
    from maa_api.services.resource_index import iter_resource_roots
    from pathlib import Path

    for root in iter_resource_roots():
        if (Path(root) / NAVIGATION_DIRECTORY / f"{prefix}.json").is_file():
            return True
    return False


#: 进程内共享的默认门面；工具与注入段都用它。
_DEFAULT: ActivityService | None = None


def default_activity_service() -> ActivityService:
    """默认门面（进程内单例）。"""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = ActivityService()
    return _DEFAULT
