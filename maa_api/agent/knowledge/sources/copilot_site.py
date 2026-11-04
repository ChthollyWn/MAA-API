"""作业站作业来源（M77 spec §4.1、§4.5）。

清单 = 当期活动的全部关卡 ``stages[].stage_id`` + 配置里的额外关卡，去重保序；
每个关卡按 ``order="hot"`` 取第 1 页、每页 ``per_stage_limit`` 条。当期活动取不到时
整体抛错——不能把「取不到」当成「没有关卡」去删掉已有作业卡。

卡片只用元数据（标题、关卡、上传者、干员/干员组、说明），不写 ``actions``，也不
把浏览量、好评率写进正文（它们每天变，会把分块与向量全部重建），只放 ``meta``。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any

from maa_api.agent.knowledge.index.indexer import DocumentRef, SourceDocument
from maa_api.db.models import utcnow
from maa_api.domain.enums import KnowledgeSourceKind
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.copilot_site import (
    CopilotRow,
    CopilotSiteClient,
    copilot_link,
)
from maa_api.services.game.activities import default_activity_service

__all__ = ["CopilotSiteCorpusSource"]

#: ``extra_stage_ids`` 的形状（与作业站客户端的关卡关键字同一口径）。
_STAGE_ID = re.compile(r"^[A-Za-z0-9_#\-/]{1,64}$")

MAX_EXTRA_STAGE_IDS = 30
MIN_PER_STAGE_LIMIT = 1
MAX_PER_STAGE_LIMIT = 20


class CopilotSiteCorpusSource:
    """作业站来源；活动服务可注入，不注入时用进程内默认门面。"""

    source_id = "copilot-site"
    kind = KnowledgeSourceKind.COPILOT_SITE
    title = "作业站作业"
    trusted = False
    license = "作业版权属于各自的作业作者"
    searchable_by_default = True
    setting_key = "rag.copilot_site_enabled"
    full_listing = True
    scheduled_refresh = True

    def __init__(
        self,
        client: CopilotSiteClient,
        *,
        activity_service: Any = None,
    ) -> None:
        self._client = client
        self._activity_service = activity_service
        self._pending: dict[str, SourceDocument] = {}

    @property
    def activity_service(self) -> Any:
        """当期活动服务；仓库没有 ``app.state.activity_service``，所以这里回落默认门面。"""
        return self._activity_service or default_activity_service()

    # -- 配置 -----------------------------------------------------------
    def validate_config(self, config: Mapping[str, Any]) -> dict[str, Any]:
        """校验 ``extra_stage_ids`` / ``per_stage_limit`` 并补齐默认值。"""
        values = dict(config or {})
        extra = values.get("extra_stage_ids", [])
        if not isinstance(extra, (list, tuple)) or not all(
            isinstance(item, str) for item in extra
        ):
            raise _invalid("extra_stage_ids", "extra_stage_ids 必须是字符串列表")
        if len(extra) > MAX_EXTRA_STAGE_IDS:
            raise _invalid(
                "extra_stage_ids", f"额外关卡不能超过 {MAX_EXTRA_STAGE_IDS} 个"
            )
        cleaned: list[str] = []
        for stage_id in extra:
            if not _STAGE_ID.match(stage_id):
                raise _invalid("extra_stage_ids", f"关卡 id 不合法：{stage_id[:64]}")
            if stage_id not in cleaned:
                cleaned.append(stage_id)
        limit = values.get("per_stage_limit", 10)
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise _invalid("per_stage_limit", "per_stage_limit 必须是整数")
        if not MIN_PER_STAGE_LIMIT <= limit <= MAX_PER_STAGE_LIMIT:
            raise _invalid(
                "per_stage_limit",
                f"per_stage_limit 必须在 {MIN_PER_STAGE_LIMIT}–{MAX_PER_STAGE_LIMIT} 之间",
            )
        return {"extra_stage_ids": cleaned, "per_stage_limit": limit}

    # -- 清单与正文 -----------------------------------------------------
    async def list_refs(self, config: Mapping[str, Any]) -> list[DocumentRef]:
        """当期活动关卡 + 额外关卡去重，逐关卡查 hot 第 1 页并生成卡片。"""
        values = self.validate_config(config)
        self._pending.clear()
        report = await self.activity_service.current(detail="full")
        stage_ids = _stage_ids(report, values["extra_stage_ids"])
        now = utcnow()
        refs: list[DocumentRef] = []
        for stage_id in stage_ids:
            page = await self._client.query(
                stage=stage_id,
                order="hot",
                page=1,
                limit=values["per_stage_limit"],
            )
            for row in page.rows:
                document = _document(row, stage_id, now)
                if document is None or document.external_id in self._pending:
                    continue
                self._pending[document.external_id] = document
                refs.append(
                    DocumentRef(
                        external_id=document.external_id,
                        title=document.title,
                        version=document.version,
                    )
                )
        return refs

    async def fetch(
        self, refs: Sequence[DocumentRef], config: Mapping[str, Any]
    ) -> list[SourceDocument]:
        """弹出请求到的暂存卡片；没被请求的留着（下次 ``list_refs`` 会重置）。"""
        del config
        documents: list[SourceDocument] = []
        for ref in refs:
            document = self._pending.pop(ref.external_id, None)
            if document is not None:
                documents.append(document)
        return documents

    async def prune(self, config: Mapping[str, Any]) -> int:
        """同步成功后的收尾：丢掉本批没被取走的暂存卡片（不删任何文档）。"""
        del config
        self._pending.clear()
        return 0


def _stage_ids(report: Any, extra: Sequence[str]) -> list[str]:
    """活动里的 ``stages[].stage_id`` 加额外关卡，去掉空值并保序去重。"""
    ordered: list[str] = []
    for activity in getattr(report, "activities", ()) or ():
        for stage in getattr(activity, "stages", ()) or ():
            stage_id = getattr(stage, "stage_id", None)
            if isinstance(stage_id, str) and stage_id and stage_id not in ordered:
                ordered.append(stage_id)
    for stage_id in extra:
        if stage_id not in ordered:
            ordered.append(stage_id)
    return ordered


def _document(row: CopilotRow, stage_id: str, fetched_at: Any) -> SourceDocument | None:
    """一行作业 → 一张卡片；没有编号的行丢弃。"""
    if not row.site_id:
        return None
    payload = row.parse_content() or {}
    doc = payload.get("doc") if isinstance(payload.get("doc"), Mapping) else {}
    title = _text(doc.get("title")) or f"作业 #{row.site_id}"
    stage_name = (
        _text(payload.get("stage_name"))
        or _text(row.raw.get("stage_name"))
        or stage_id
    )
    lines = [f"# {title}", f"- 关卡：{stage_name}（{stage_id}）"]
    meta_parts = [f"作业编号：{row.site_id}"]
    if row.uploader:
        meta_parts.append(f"上传者：{row.uploader}")
    if row.upload_time:
        meta_parts.append(f"上传时间：{row.upload_time}")
    lines.append("- " + "；".join(meta_parts))
    opers = _opers(payload.get("opers"))
    if opers:
        lines.append("- 干员：" + "、".join(opers))
    groups = _groups(payload.get("groups"))
    if groups:
        lines.append("- 干员组：" + "；".join(groups))
    details = _text(doc.get("details"))
    if details:
        lines.append(f"- 说明：{details}")
    text = "\n".join(lines)
    return SourceDocument(
        external_id=f"copilot:{row.site_id}",
        title=title,
        text=text,
        url=copilot_link(row.site_id),
        version=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
        fetched_at=fetched_at,
        meta={
            "stage_id": stage_id,
            "site_id": row.site_id,
            "views": row.views,
            "rating_ratio": row.rating_ratio,
            "upload_time": row.upload_time,
        },
    )


def _opers(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        name = _oper(item)
        if name:
            names.append(name)
    return names


def _oper(item: Mapping[str, Any]) -> str:
    name = _text(item.get("name"))
    if not name:
        return ""
    skill = item.get("skill")
    if isinstance(skill, int) and not isinstance(skill, bool):
        return f"{name}（技能 {skill}）"
    return name


def _groups(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    groups: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        name = _text(item.get("name"))
        members = _opers(item.get("opers"))
        if not name:
            continue
        groups.append(f"{name}：{'、'.join(members)}" if members else name)
    return groups


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _invalid(field: str, reason: str) -> AppError:
    return AppError(
        ErrorCode.VALIDATION_ERROR,
        f"作业站来源的 {field} 不合法",
        {"field": field, "reason": reason},
    )
