"""作业站筛选（M29，规格 §9.4）：按账号数据把作业分成三档。

:meth:`CopilotService.search` 的分工：

1. 把 ``stage``（代号、stageId、中文名都行）先在本机关卡索引里解析成站点认识的
   stageId——作业站的 ``level_keyword`` 收的就是 stageId（实测 ``main_01-07``）；
2. 调 :class:`~maa_api.services.external.copilot_site.CopilotSiteClient` 拿一页作业；
3. 用 :mod:`maa_api.services.copilot.rules` 把每份作业的 ``opers`` / ``groups`` 与
   账号干员表逐项比对，算缺口数并分档：``ok``（0 个缺口）、``missing_one``（缺一名，
   可借助战补位）、``infeasible``（缺两名及以上）；
4. 排序「缺口数 → 好评率 → 热度」后取前 ``limit`` 条。

返回的列表**不带动作**（``content`` 里的 ``actions`` 会剥掉，规格 §9.4：省体积）；
要执行或细看时用 ``save_copilot_draft(site_id=…)`` 拉完整作业。作业站不可用时按
§3.5 降级：不抛错，返回空列表加 ``unavailable: ["copilot_site"]`` 与说明。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.copilot import rules
from maa_api.services.external.copilot_site import (
    CopilotRow,
    CopilotSiteClient,
    copilot_link,
    default_copilot_site_client,
)
from maa_api.services.external.envelope import build_envelope

__all__ = ["CopilotService", "DEFAULT_LIMIT", "MAX_LIMIT", "MAX_RESULT_BYTES", "TIERS"]

#: ``limit`` 的取值范围（规格 §9.4：1–10，默认 5）。
DEFAULT_LIMIT = 5
MAX_LIMIT = 10

#: 单个工具结果的字节上限（规格 §4.4）：≤ 24 KB，超了先删校验细节、再砍条数。
MAX_RESULT_BYTES = 24 * 1024

#: 三档（规格 §9.4）。
TIERS = ("ok", "missing_one", "infeasible")

#: 按缺口排序时，一份作业最多向站点取多少条候选（``only_feasible`` 会过滤掉一些）。
_CANDIDATE_FACTOR = 4
_CANDIDATE_CAP = 50

#: 可以降级的外部错误（规格 §3.5）：返回空列表 + ``unavailable``，不把工具调用变错误。
_DEGRADABLE = frozenset(
    {
        ErrorCode.EXTERNAL_SOURCE_DISABLED,
        ErrorCode.EXTERNAL_SOURCE_UNAVAILABLE,
        ErrorCode.EXTERNAL_SOURCE_RATE_LIMITED,
        ErrorCode.EXTERNAL_RESPONSE_INVALID,
    }
)


class CopilotService:
    """作业站作业的筛选服务（规格 §9.4）。"""

    def __init__(
        self,
        session_factory: Any = None,
        *,
        site_client: CopilotSiteClient | None = None,
        box: Any = None,
        stage_index: Any = None,
        settings_provider: Callable[[], Any] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._site_client = site_client
        self._box = box
        self._stage_index = stage_index
        self._settings_provider = settings_provider

    # -- 依赖 -------------------------------------------------------------
    @property
    def site_client(self) -> CopilotSiteClient:
        return self._site_client or default_copilot_site_client()

    @property
    def box(self) -> Any:
        """账号干员服务；缺省按注入的会话工厂现建一个，没有工厂时返回 ``None``。"""
        if self._box is not None:
            return self._box
        if self._session_factory is None:
            return None
        from maa_api.services.account.box import OperatorBoxService

        self._box = OperatorBoxService(self._session_factory)
        return self._box

    @property
    def stage_index(self) -> Any:
        """本机关卡索引（``ActivityIndex``）；缺省用进程内默认门面。"""
        if self._stage_index is None:
            from maa_api.services.activity_index import default_activity_index

            self._stage_index = default_activity_index()
        return self._stage_index

    # -- 入口 -------------------------------------------------------------
    async def search(
        self,
        *,
        stage: str,
        limit: int = DEFAULT_LIMIT,
        order: str = "hot",
        only_feasible: bool = True,
    ) -> dict[str, Any]:
        """按关卡搜作业并按账号干员筛选（规格 §9.4）。"""
        page_size = _limit(limit)
        only_ok = bool(only_feasible)
        query_stage = str(stage or "").strip()
        if not query_stage:
            raise AppError(ErrorCode.INVALID_PARAMETER, "stage 不能为空")
        resolved = self._resolve_stage(query_stage)
        keyword = resolved["stage_id"] or query_stage
        fetch = min(max(page_size * _CANDIDATE_FACTOR, page_size), _CANDIDATE_CAP)

        unavailable: list[str] = []
        note: str | None = None
        try:
            data = await self.site_client.query(
                stage=keyword, order=order, page=1, limit=fetch
            )
        except AppError as exc:
            if exc.code not in _DEGRADABLE:
                raise
            # 规格 §3.5：作业站不可用时不抛错，标 unavailable 让模型说「查不到」。
            return self._degraded(
                stage=query_stage,
                resolved=resolved,
                limit=page_size,
                order=order,
                only_feasible=only_ok,
                reason=str(exc.code),
                message=exc.message or "",
            )

        box = await self._box_index()
        evaluated = [_evaluate_row(row, box=box) for row in data.rows]
        # tiers 报的是**候选**里的分布（过滤之前），这样 only_feasible=true 时也能看出
        # 站点上到底有几份是缺人的、几份是打不了的。
        tiers = {tier: sum(1 for item in evaluated if item["tier"] == tier) for tier in TIERS}
        rows = [
            item for item in evaluated if not (only_ok and item["tier"] == "infeasible")
        ]
        rows.sort(
            key=lambda item: (
                item["missing_count"],
                -(item["rating_ratio"] if item["rating_ratio"] is not None else -1.0),
                -(item["hot_score"] if item["hot_score"] is not None else -1.0),
            )
        )
        total = len(rows)
        results = rows[:page_size]
        if box is None:
            unavailable.append("account_box")
            note = "账号干员数据取不到：这一页按「未拥有」算，只做参考"

        payload: dict[str, Any] = {
            "query": {
                "stage": query_stage,
                "stage_id": resolved["stage_id"],
                "stage_code": resolved["stage_code"],
                "matched_by": resolved["matched_by"],
                "order": order,
                "only_feasible": only_ok,
                "limit": page_size,
            },
            "results": results,
            "total": total,
            "candidates": len(data.rows),
            "site_total": data.total,
            "site_page": data.page,
            "has_next": data.has_next,
            "tiers": tiers,
            "note": note,
            "hint": (
                "列表里的 content 不含 actions（省体积）；要执行或细看用 "
                "save_copilot_draft(site_id=…) 拉完整作业。"
            ),
            **build_envelope([data.source_ref()], unavailable=unavailable),
        }
        return _shrink(payload)

    def _degraded(
        self,
        *,
        stage: str,
        resolved: Mapping[str, Any],
        limit: int,
        order: str,
        only_feasible: bool,
        reason: str,
        message: str,
    ) -> dict[str, Any]:
        return {
            "query": {
                "stage": stage,
                "stage_id": resolved.get("stage_id"),
                "stage_code": resolved.get("stage_code"),
                "matched_by": resolved.get("matched_by"),
                "order": order,
                "only_feasible": only_feasible,
                "limit": limit,
            },
            "results": [],
            "total": 0,
            "candidates": 0,
            "site_total": None,
            "site_page": None,
            "has_next": False,
            "tiers": {tier: 0 for tier in TIERS},
            "note": f"作业站取不到作业（{reason}）：{message}。可以稍后重试，"
            "或用 get_stage_level_data 自己设计一份作业。",
            "hint": None,
            **build_envelope(unavailable=["copilot_site"]),
        }

    # -- 内部 -------------------------------------------------------------
    def _resolve_stage(self, stage: str) -> dict[str, Any]:
        """本机关卡索引解析：代号 / 中文名 / levelId → stageId（站点要的就是它）。"""
        result: Mapping[str, Any] = {}
        try:
            result = self.stage_index.find_stage(stage) or {}
        except AppError:
            result = {}
        except Exception:  # noqa: BLE001 - 本机索引缺失不该拦住搜索
            result = {}
        matches = [item for item in (result.get("matches") or []) if isinstance(item, Mapping)]
        chosen = matches[0] if matches else {}
        return {
            "stage_id": chosen.get("stage_id"),
            "stage_code": chosen.get("code"),
            "matched_by": result.get("matched_field"),
            "candidates": len(matches),
        }

    async def _box_index(self) -> dict[str, Mapping[str, Any]] | None:
        service = self.box
        if service is None:
            return None
        try:
            listing = await service.list_operators(owned_only=False, limit=5000)
        except AppError:
            return None
        index: dict[str, Mapping[str, Any]] = {}
        for row in listing.get("items") or []:
            if not isinstance(row, Mapping):
                continue
            name = row.get("name")
            if isinstance(name, str) and name:
                index.setdefault(name, row)
                index.setdefault(name.casefold(), row)
            char_id = row.get("char_id")
            if isinstance(char_id, str) and char_id:
                index.setdefault(char_id, row)
        return index or None


def _limit(limit: Any) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"limit 必须是 {1}–{MAX_LIMIT} 的整数，收到 {limit!r}",
            {"limit": limit},
        )
    if not 1 <= limit <= MAX_LIMIT:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"limit 必须是 1–{MAX_LIMIT} 的整数，收到 {limit}",
            {"limit": limit},
        )
    return limit


def _evaluate_row(
    row: CopilotRow, *, box: Mapping[str, Mapping[str, Any]] | None
) -> dict[str, Any]:
    """一行作业 → 工具返回里的一条结果（不含动作）。"""
    content = row.parse_content()
    checks: tuple[rules.OperatorCheck, ...] = ()
    if content is None:
        tier = "infeasible"
        missing: list[str] = []
        note = "作业内容不是合法 JSON，无法核对干员"
    else:
        checks = rules.evaluate_content(content, box=box)
        missing = rules.missing_operator_names(checks)
        tier = rules.tier_of(len(missing))
        note = rules.SUPPORT_NOTE if tier == "missing_one" else None
    summary = _content_summary(content)
    return {
        "site_id": row.site_id,
        "title": summary.get("title"),
        "uploader": row.uploader,
        "views": row.views,
        "rating_ratio": row.rating_ratio,
        "rating_level": row.rating_level,
        "not_enough_rating": row.not_enough_rating,
        "hot_score": row.hot_score,
        "upload_time": row.upload_time,
        "available": row.available,
        "link": copilot_link(row.site_id),
        "stage_name": summary.get("stage_name"),
        "difficulty": summary.get("difficulty"),
        "minimum_required": summary.get("minimum_required"),
        "feasible": tier != "infeasible",
        "tier": tier,
        "missing_count": len(missing),
        "missing": missing,
        "note": note,
        "operators": [check.as_dict() for check in checks],
        "content": summary.get("content"),
    }


def _content_summary(content: Mapping[str, Any] | None) -> dict[str, Any]:
    """列表里的 ``content``：**不含** ``actions``（规格 §9.4）。"""
    if content is None:
        return {"content": None}
    doc = content.get("doc")
    title = None
    if isinstance(doc, Mapping) and isinstance(doc.get("title"), str):
        title = doc["title"]
    trimmed = {key: value for key, value in content.items() if key != "actions"}
    return {
        "title": title,
        "stage_name": content.get("stage_name"),
        "difficulty": content.get("difficulty"),
        "minimum_required": content.get("minimum_required"),
        "content": trimmed,
    }


def _size(payload: Any) -> int:
    return len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))


def _shrink(payload: dict[str, Any]) -> dict[str, Any]:
    """体积超 24 KB 时按「先砍校验细节、再砍条数」的顺序收敛（规格 §4.4）。"""
    if _size(payload) <= MAX_RESULT_BYTES:
        return payload
    for item in payload.get("results") or []:
        for operator in item.get("operators") or []:
            operator["requirements"] = []
    if _size(payload) <= MAX_RESULT_BYTES:
        payload["truncated"] = {
            "field": "operators.requirements",
            "note": "结果超出 24 KB，逐项练度要求已省略；只看 satisfied / missing。",
        }
        return payload
    while len(payload.get("results") or []) > 1 and _size(payload) > MAX_RESULT_BYTES:
        payload["results"] = payload["results"][:-1]
    payload["truncated"] = {
        "field": "results",
        "total": payload.get("total"),
        "returned": len(payload.get("results") or []),
        "note": "结果超出 24 KB，只返回靠前的若干条；要更多请调小 limit 或换 order。",
    }
    return payload


#: 进程内共享的默认门面。
_DEFAULT: CopilotService | None = None


def default_copilot_service() -> CopilotService:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = CopilotService()
    return _DEFAULT
