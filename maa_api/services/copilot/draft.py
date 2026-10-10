"""作业草稿的保存与静态校验（M29，规格 §9.5）。

:meth:`CopilotDraftService.save` 把一份作业（AI 写的 ``content`` 或作业站导入的
``site_id``）存成 ``kind=COPILOT`` 的 :class:`~maa_api.db.models.ResourceAsset`，
元数据按规格 §9.5 写进 ``meta``；协议校验与静态检查在
:mod:`maa_api.services.copilot.rules` 里（纯函数，可脱离数据库与文件系统验证）。

存储走既有的 :class:`~maa_api.services.resource_service.ResourceService.upload_copilot`
（它负责 ≤ 2 MB 的内容校验、内联/落盘的选择与 checksum），随后由本模块把 §9.5 要求的
``meta`` 补上——``ResourceService`` 自己的 ``meta``（``stage_name`` / ``title``）保留，
资源列表按它们筛选与展示。

校验失败的口径（本模块裁决，写进报告）：

- **协议**不过（不是 JSON 对象、缺 ``stage_name`` / ``minimum_required``、未知顶层字段、
  类型不符）→ 抛 ``COPILOT_JSON_INVALID``，不存；
- **静态**检查不过（坐标出界、地块不可部署、近战/远程与地块不符、干员不在游戏数据里、
  助战名额超了、部署数超上限、技能序号超了）→ 同样抛 ``COPILOT_JSON_INVALID``
  （``details.violations`` 列出每一条），不存；
- **环境**导致跑不了的检查（本机没有该关的地块文件、没有干员基础数据）→ **不算失败**，
  记进 ``meta`` 与返回值里的 ``skipped``，如实告诉模型「这项没查」。
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Mapping
from typing import Any, MutableMapping

from maa_api.domain.enums import ResourceAssetKind
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.copilot import rules
from maa_api.services.external.copilot_site import CopilotSiteClient, default_copilot_site_client

__all__ = ["CopilotDraftService", "MAX_NOTE_CHARS", "SOURCES"]

#: ``note`` 的长度上限（规格 §9.5）。
MAX_NOTE_CHARS = 200

#: ``meta.source`` 的取值域（规格 §9.5）。
SOURCES: tuple[str, ...] = ("user", "site", "ai")

#: 校验里「查不动」的环境原因（不是作业的问题），按 §3.5 如实报，不算失败。
_ENVIRONMENTAL_CODES = frozenset({"map_unavailable"})

#: 规格 §9.5 点名的 ``meta`` 键：即使没有值也写 null，读的人不用猜。
_ALWAYS_PRESENT_META = frozenset(
    {"source", "stage_id", "site_id", "parent_id", "session_id", "note", "attempts", "retain"}
)

_STAGE_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


class CopilotDraftService:
    """作业草稿：校验 + 存资源 + 版本链的父指针（规格 §9.5）。

    :param session_factory: ``async_sessionmaker``；只用来补写 ``meta``（存储本身经
        ``resource_service``）。
    :param resource_service: :class:`~maa_api.services.resource_service.ResourceService`；
        缺省从应用装配取（工具层传入）。
    :param box: :class:`~maa_api.services.account.box.OperatorBoxService`；缺省
        ``app.state.operator_box_service``。
    :param stage_level: ``StageLevelService``（并行工作流交付）；缺省
        ``app.state.stage_level_service``，取不到时跳过「部署数 ≤ 上限」这项检查。
    :param site_client: 作业站客户端；``site_id`` 导入时用它拉完整作业。
    :param layout_provider: ``stage_id -> 关卡布局``；缺省用
        :func:`~maa_api.services.stage_layout.load_stage_layout`（本机
        ``Arknights-Tile-Pos``）。
    :param operators_provider: ``() -> {名字或 char_id: 干员基础数据}``；缺省读本机
        ``battle_data.json``（``position`` 判近战/远程，``rarity`` 推技能数）。
    """

    def __init__(
        self,
        session_factory: Any,
        *,
        resource_service: Any = None,
        box: Any = None,
        stage_level: Any = None,
        site_client: CopilotSiteClient | None = None,
        layout_provider: Callable[[str], Mapping[str, Any] | None] | None = None,
        operators_provider: Callable[[], Mapping[str, Mapping[str, Any]] | None] | None = None,
        battle_data_path: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._resource_service = resource_service
        self._box = box
        self._stage_level = stage_level
        self._site_client = site_client
        self._layout_provider = layout_provider
        self._operators_provider = operators_provider
        self._battle_data_path = battle_data_path
        self._operators_cache: Mapping[str, Mapping[str, Any]] | None = None

    # -- 依赖 -------------------------------------------------------------
    @property
    def resource_service(self) -> Any:
        if self._resource_service is None:
            raise AppError(
                ErrorCode.SERVICE_UNAVAILABLE,
                "资源服务尚未装配（缺少 resource_service）",
            )
        return self._resource_service

    @property
    def site_client(self) -> CopilotSiteClient:
        return self._site_client or default_copilot_site_client()

    # -- 校验 -------------------------------------------------------------
    async def check(
        self,
        content: Any,
        *,
        allow_support: bool = True,
        stage: str | None = None,
    ) -> dict[str, Any]:
        """只校验不落库；返回 ``{ok, protocol, static, violations, skipped}``。

        协议问题与静态问题分开列，便于调用方决定「不存」还是「存下来但提示」。
        """
        protocol: list[dict[str, Any]] = []
        normalized: Mapping[str, Any] | None = None
        try:
            normalized = rules.normalize_content(content)
        except AppError as exc:
            protocol.append(
                {"code": str(exc.code), "message": exc.message or "", **(exc.details or {})}
            )
        if normalized is None:
            return {
                "ok": False,
                "protocol": protocol,
                "static": None,
                "violations": protocol,
                "skipped": [],
            }
        static = await self._static_report(normalized, allow_support=allow_support, stage=stage)
        return {
            "ok": not protocol and bool(static["ok"]),
            "protocol": protocol,
            "static": static,
            "violations": [*protocol, *static["violations"]],
            "skipped": static["skipped"],
        }

    async def _static_report(
        self,
        content: Mapping[str, Any],
        *,
        allow_support: bool,
        stage: str | None = None,
    ) -> dict[str, Any]:
        """跑静态检查：把账号、地块、干员基础数据与本关部署上限凑齐再交给 rules。"""
        stage_id = stage or str(content.get("stage_name") or "")
        layout = self._layout(stage_id)
        operators = self._operators()
        box = await self._box_index()
        deploy_limit = await self._deploy_limit(stage_id)
        return rules.static_check(
            content,
            layout=layout,
            operators=operators,
            box=box,
            deploy_limit=deploy_limit,
            allow_support=allow_support,
        )

    def _layout(self, stage_id: str) -> Mapping[str, Any] | None:
        if self._layout_provider is not None:
            try:
                return self._layout_provider(stage_id)
            except AppError:
                return None
        if not stage_id:
            return None
        from maa_api.services.stage_layout import load_stage_layout

        try:
            return load_stage_layout(stage_id)
        except AppError:
            return None

    def _operators(self) -> Mapping[str, Mapping[str, Any]] | None:
        if self._operators_provider is not None:
            return self._operators_provider()
        if self._operators_cache is not None:
            return self._operators_cache
        self._operators_cache = _load_battle_data(self._battle_data_path)
        return self._operators_cache

    async def _box_index(self) -> dict[str, Mapping[str, Any]] | None:
        """账号干员表索引：名字与 char_id 都能查到同一行；取不到时返回 ``None``。"""
        service = self._box
        if service is None:
            service = _default_box_service(self._session_factory)
        if service is None:
            return None
        try:
            listing = await service.list_operators(owned_only=False, limit=5000)
        except AppError:
            return None
        return _index_box(listing.get("items") or [])

    async def _deploy_limit(self, stage_id: str) -> int | None:
        service = self._stage_level
        if service is None or not stage_id:
            return None
        getter = getattr(service, "level_data", None)
        if not callable(getter):
            return None
        try:
            data = await getter(stage_id)
        except AppError:
            return None
        except Exception:  # noqa: BLE001 - 关卡数据是外部来源，坏在这不该拦住草稿
            return None
        if not isinstance(data, Mapping):
            return None
        options = data.get("options")
        if not isinstance(options, Mapping):
            return None
        limit = options.get("deploy_limit")
        return limit if isinstance(limit, int) and not isinstance(limit, bool) else None

    # -- 保存 -------------------------------------------------------------
    async def save(
        self,
        *,
        content: Mapping[str, Any] | str | None = None,
        site_id: int | str | None = None,
        parent_id: str | None = None,
        note: str = "",
        session_id: str | None = None,
        source: str | None = None,
        allow_support: bool = True,
    ) -> dict[str, Any]:
        """存一份作业草稿（规格 §9.5）；返回资源与校验摘要。

        ``content`` 与 ``site_id`` 二选一：给 ``site_id`` 时先经作业站客户端拉完整作业
        （列表里的 ``content`` 不含 ``actions``，不能拿来执行）。
        """
        text = _note(note)
        if (content is None) == (site_id is None):
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                "content 与 site_id 必须二选一",
                {"has_content": content is not None, "has_site_id": site_id is not None},
            )
        fetched: dict[str, Any] | None = None
        if site_id is not None:
            number = _site_number(site_id)
            fetched = await self._fetch(number)
            content = fetched["content"]
        parsed = _parse_content(content)
        normalized = rules.normalize_content(parsed)
        report = await self._static_report(normalized, allow_support=allow_support)
        blocking = [
            item
            for item in report["violations"]
            if item.get("code") not in _ENVIRONMENTAL_CODES
        ]
        if blocking:
            raise AppError(
                ErrorCode.COPILOT_JSON_INVALID,
                "作业校验未通过：" + "；".join(item["message"] for item in blocking),
                {
                    "violations": blocking,
                    "skipped": report["skipped"],
                    "protocol": rules.PROTOCOL_NOTE,
                },
            )

        stage_id = _stage_id_of(fetched, normalized)
        source_kind = _source_of(source, site_id, session_id)
        title = (fetched or {}).get("title") or _doc_title(normalized)
        meta: dict[str, Any] = {
            "source": source_kind,
            "stage_id": stage_id,
            "site_id": _site_number(site_id) if site_id is not None else None,
            "parent_id": parent_id,
            "session_id": session_id,
            "note": text,
            "attempts": [],
            "retain": False,
        }
        if fetched is not None:
            meta["site_link"] = fetched.get("link")
            meta["uploader"] = fetched.get("uploader")
            meta["rating_ratio"] = fetched.get("rating_ratio")
            meta["views"] = fetched.get("views")
        if report["skipped"]:
            meta["checks_skipped"] = list(report["skipped"])
        asset = await self.resource_service.upload_copilot(
            name=_asset_name(stage_id, site_id, title),
            description=text or title,
            content=normalized,
        )
        asset = await self._write_meta(str(asset.get("id")), meta)
        return {
            **asset,
            "source": source_kind,
            "stage_id": stage_id,
            "site_id": meta["site_id"],
            "parent_id": parent_id,
            "title": title,
            "link": meta.get("site_link"),
            "checks": {
                "ok": True,
                "violations": [],
                "skipped": list(report["skipped"]),
                "deploy_count": report["deploy_count"],
                "deploy_limit": report["deploy_limit"],
                "protocol": rules.PROTOCOL_NOTE,
            },
        }

    async def _fetch(self, site_id: int) -> dict[str, Any]:
        """作业站导入：拉完整作业并解出 ``content``。"""
        record = await self.site_client.get(site_id)
        rows = list(record.rows)
        if not rows:
            raise AppError(
                ErrorCode.EXTERNAL_RESPONSE_INVALID,
                f"作业站没有返回编号 {site_id} 的作业内容",
                {"site_id": site_id},
            )
        row = rows[0]
        parsed = row.parse_content()
        if parsed is None:
            raise AppError(
                ErrorCode.EXTERNAL_RESPONSE_INVALID,
                f"作业 {site_id} 的 content 不是合法 JSON 对象",
                {"site_id": site_id},
            )
        return {
            "content": parsed,
            "title": _doc_title(parsed),
            "link": row.as_dict().get("link"),
            "uploader": row.uploader,
            "rating_ratio": row.rating_ratio,
            "views": row.views,
        }

    async def _write_meta(self, asset_id: str, meta: Mapping[str, Any]) -> dict[str, Any]:
        """把 §9.5 的 ``meta`` 写进资源行（保留 ``ResourceService`` 原有的键）。"""
        from maa_api.db.repositories.resource import ResourceAssetRepository
        from maa_api.services.resource_service import _asset_wire

        async with self._session_factory() as session:
            repo = ResourceAssetRepository(session)
            asset = await repo.get(asset_id)
            if asset is None:
                raise AppError(
                    ErrorCode.RESOURCE_ASSET_NOT_FOUND,
                    "草稿刚写入就不见了，请重试",
                    {"asset_id": asset_id},
                )
            if str(asset.kind) != ResourceAssetKind.COPILOT.value:
                raise AppError(
                    ErrorCode.INVALID_PARAMETER,
                    "目标资源不是 Copilot 作业",
                    {"asset_id": asset_id, "kind": str(asset.kind)},
                )
            merged: MutableMapping[str, Any] = dict(asset.meta or {})
            for key, value in meta.items():
                # 规格 §9.5 点名的键即使没有值也留 null：读的人不用区分「没这个键」与
                # 「有键但没值」；站点那几项（链接、上传者）没抓到就不写。
                if value is None and str(key) not in _ALWAYS_PRESENT_META:
                    merged.pop(key, None)
                else:
                    merged[str(key)] = value
            asset.meta = dict(merged)
            await session.merge(asset)
            await session.commit()
            stored = await repo.get(asset_id)
        assert stored is not None
        return _asset_wire(stored, include_content=False)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _parse_content(content: Any) -> Any:
    """``content`` 可以是对象或 JSON 字符串（模型更常给字符串）。"""
    if content is None:
        raise AppError(ErrorCode.INVALID_PARAMETER, "缺少作业内容")
    if isinstance(content, Mapping):
        return content
    if isinstance(content, str):
        try:
            return json.loads(content)
        except ValueError as exc:
            raise AppError(
                ErrorCode.COPILOT_JSON_INVALID,
                "content 不是合法 JSON",
                {"reason": str(exc)[:120]},
            ) from exc
    raise AppError(
        ErrorCode.COPILOT_JSON_INVALID,
        "content 必须是作业对象或 JSON 字符串",
        {"type": type(content).__name__},
    )


def _note(note: Any) -> str:
    text = str(note or "").strip()
    if len(text) > MAX_NOTE_CHARS:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"note 最多 {MAX_NOTE_CHARS} 字",
            {"chars": len(text), "max": MAX_NOTE_CHARS},
        )
    return text


def _source_of(source: str | None, site_id: Any, session_id: str | None) -> str:
    if source is not None:
        cleaned = str(source).strip().lower()
        if cleaned not in SOURCES:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"source 只能是 {'、'.join(SOURCES)}",
                {"source": cleaned[:16]},
            )
        return cleaned
    if site_id is not None:
        return "site"
    return "ai" if session_id else "user"


def _stage_id_of(fetched: Mapping[str, Any] | None, content: Mapping[str, Any]) -> str | None:
    """草稿的关卡标识：作业站的 ``stage_name`` 就是 stageId。"""
    stage = content.get("stage_name")
    if isinstance(stage, str) and stage.strip():
        return stage.strip()
    return None


def _doc_title(content: Mapping[str, Any]) -> str | None:
    doc = content.get("doc")
    if isinstance(doc, Mapping):
        title = doc.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()[:64]
    return None


def _site_number(site_id: Any) -> int:
    """作业站编号：只接受数字（客户端那层同样会再校验一次路径）。"""
    text = str(site_id).strip()
    if not text.isdigit() or len(text) > 12:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"site_id 必须是作业站编号（数字），收到 {text[:16]!r}",
            {"site_id": text[:16]},
        )
    return int(text)


def _asset_name(stage_id: str | None, site_id: Any, title: str | None) -> str:
    """资源名：``作业站 #<id>`` / ``作业 <stage>`` + 短随机后缀（``(kind, name)`` 唯一）。"""
    if site_id is not None:
        head = f"作业站 #{_site_number(site_id)}"
    elif stage_id:
        head = f"作业 {_STAGE_SAFE.sub('-', stage_id)[:32]}"
    else:
        head = "作业草稿"
    if title:
        head = f"{head} {title}"
    return f"{head[:48]}·{uuid.uuid4().hex[:6]}"


def _index_box(rows: Any) -> dict[str, dict[str, Any]]:
    """账号干员行 → ``{名字: 行, char_id: 行}``（作业里两种写法都可能出现）。"""
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        item = dict(row)
        name = item.get("name")
        if isinstance(name, str) and name:
            index.setdefault(name, item)
            index.setdefault(name.casefold(), item)
        char_id = item.get("char_id")
        if isinstance(char_id, str) and char_id:
            index.setdefault(char_id, item)
    return index


def _default_box_service(session_factory: Any) -> Any:
    """没有装配时按需现建一个账号干员服务；会话工厂都没有就返回 ``None``。"""
    if session_factory is None:
        return None
    from maa_api.services.account.box import OperatorBoxService

    return OperatorBoxService(session_factory)


def _load_battle_data(path: str | None = None) -> Mapping[str, Mapping[str, Any]] | None:
    """读本机 ``battle_data.json`` 的 ``chars``，做 ``{名字/char_id: {position, rarity}}``。

    只用两个字段：``position``（近战/远程）与 ``rarity``（推技能数）。读不到就返回
    ``None``，对应的检查记进 ``skipped``（规格 §3.5：不冒充）。
    """
    from pathlib import Path

    from maa_api.services.resource_index import iter_resource_roots

    candidates = (
        [Path(path)] if path else [root / "battle_data.json" for root in iter_resource_roots()]
    )
    for candidate in candidates:
        try:
            payload = json.loads(Path(candidate).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        chars = payload.get("chars") if isinstance(payload, Mapping) else None
        if not isinstance(chars, Mapping):
            continue
        index: dict[str, Mapping[str, Any]] = {}
        for char_id, record in chars.items():
            if not isinstance(record, Mapping):
                continue
            entry = {
                "char_id": str(char_id),
                "name": record.get("name"),
                "position": record.get("position"),
                "rarity": record.get("rarity"),
                "skills": _skill_count_of(record),
            }
            for key in (entry["name"], str(char_id)):
                if isinstance(key, str) and key:
                    index.setdefault(key, entry)
                    index.setdefault(key.casefold(), entry)
        return index
    return None


def _skill_count_of(record: Mapping[str, Any]) -> int | None:
    """干员能带几个技能：``skills`` 数组长度（``battle_data.json`` 里叫 ``skills``）。"""
    skills = record.get("skills")
    if isinstance(skills, list) and skills:
        return len(skills)
    return None
