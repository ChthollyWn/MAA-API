"""导航图端点（M86 spec §4.3.2）：读图、写界面、写跳转与删跳转。

四个端点沿用 ``agent`` tag，前缀 ``/api/agent/nav``，都要求鉴权。路由只做形状校验
（界面 id、跳转 id、互斥样本来源）、样本归档的编排与 404/400/410/503 的出口；图本身
的读写、样本去重与 8 个上限、记账都在 :class:`NavGraphService`。

样本缩略图按截图归档记录的 ``path`` 末段 sha 拼 ``/api/images/{sha}/thumb``（与
``screenshots`` 路由同一套内容寻址路径）；接口只读设备时调 ``device_manager.screenshot``，
不取租约（REST 导航的租约口径见 §4.5.5）。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi import Path as PathParam
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.agent.nav import NavGraphService
from maa_api.agent.nav.graph import NODE_ID_PATTERN
from maa_api.agent.nav.recognizer import ModelVisionClient, signature
from maa_api.api.deps import get_session, require_auth
from maa_api.api.errors import error_responses
from maa_api.api.schemas.agent_v3 import (
    NavEdgeOut,
    NavEdgePut,
    NavExemplarOut,
    NavGraphOut,
    NavNodeOut,
    NavNodePut,
    NavRecognizerModel,
)
from maa_api.db import session as db_session
from maa_api.db.models import NavEdge, NavNode, Screenshot
from maa_api.db.repositories.log import ScreenshotRepository
from maa_api.domain.enums import ScreenshotBackend, ScreenshotTrigger
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.screenshot_archive import archive_screenshot
from maa_api.settings import get_settings

__all__ = ["router"]

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent/nav", tags=["agent"])


# ----------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------
def _service(request: Request) -> NavGraphService:
    service = getattr(request.app.state, "nav_service", None)
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "界面导航图服务还在启动中")
    return service


def _device_manager(request: Request) -> Any:
    manager = getattr(request.app.state, "device_manager", None)
    if manager is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "设备管理服务尚未启动")
    return manager


def _iso_z(value: datetime | None) -> str | None:
    """库里是 naive UTC；出参统一成带 ``Z`` 的 ISO 8601。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _recognizer_model(raw: Any) -> NavRecognizerModel:
    """把节点里的 ``recognizer`` JSON 收成响应模型；缺项用默认值。"""
    data = raw if isinstance(raw, Mapping) else {}
    return NavRecognizerModel(
        rois=data.get("rois") or [[0.0, 0.0, 1.0, 1.0]],
        max_distance=data.get("max_distance", 10),
        description=str(data.get("description") or ""),
        generic=bool(data.get("generic", False)),
    )


def _rois(recognizer: Any) -> list[list[float]]:
    return [list(item) for item in _recognizer_model(recognizer).rois]


def _root() -> Path:
    return Path(db_session.DB_PATH).parent.resolve()


def _read_image(screenshot: Screenshot) -> Image.Image | None:
    """读归档截图文件；路径越界、文件丢失或损坏都返回 ``None``。"""
    root = _root()
    candidate = (root / screenshot.path).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    try:
        with Image.open(candidate) as handle:
            return handle.convert("RGB")
    except Exception:  # noqa: BLE001 - 坏图按样本不可用处理
        logger.warning("读取导航样本截图失败：%s", screenshot.path, exc_info=True)
        return None


def _thumb_url(screenshot: Screenshot) -> str | None:
    sha = Path(screenshot.path).stem
    return f"/api/images/{sha}/thumb" if sha else None


async def _exemplar_out(
    repository: ScreenshotRepository, raw: Mapping[str, Any]
) -> NavExemplarOut:
    screenshot_id = raw.get("screenshot_id")
    thumb = None
    if isinstance(screenshot_id, str) and screenshot_id:
        screenshot = await repository.get(screenshot_id)
        if screenshot is not None and screenshot.deleted_at is None:
            thumb = _thumb_url(screenshot)
    added_at = raw.get("added_at")
    return NavExemplarOut(
        phash=str(raw.get("phash") or ""),
        screenshot_id=str(screenshot_id) if screenshot_id else None,
        source=str(raw.get("source") or ""),
        added_at=str(added_at) if added_at else None,
        thumb_url=thumb,
    )


async def _node_out(
    repository: ScreenshotRepository, node: NavNode
) -> NavNodeOut:
    exemplars = [
        await _exemplar_out(repository, item)
        for item in node.exemplars or []
        if isinstance(item, Mapping)
    ]
    return NavNodeOut(
        id=node.id,
        title=node.title,
        kind=node.kind,
        origin=node.origin,
        recognizer=_recognizer_model(node.recognizer),
        exemplars=exemplars,
        created_at=_iso_z(node.created_at) or "",
        updated_at=_iso_z(node.updated_at) or "",
    )


def _edge_out(edge: NavEdge) -> NavEdgeOut:
    return NavEdgeOut(
        id=edge.id,
        from_node=edge.from_node,
        to_node=edge.to_node,
        action=dict(edge.action or {}),
        action_hash=edge.action_hash,
        cost_ms=edge.cost_ms,
        success_count=int(edge.success_count),
        fail_count=int(edge.fail_count),
        last_used_at=_iso_z(edge.last_used_at),
        origin=edge.origin,
        enabled=bool(edge.enabled),
        created_at=_iso_z(edge.created_at) or "",
        updated_at=_iso_z(edge.updated_at) or "",
    )


# ----------------------------------------------------------------------
# GET /api/agent/nav/graph
# ----------------------------------------------------------------------
@router.get(
    "/graph",
    response_model=NavGraphOut,
    summary="读取界面导航图",
    description="返回全部界面与跳转、导航开关以及当前是否有可用的视觉模型。",
    responses=error_responses("SERVICE_UNAVAILABLE", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def get_nav_graph(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> NavGraphOut:
    service = _service(request)
    nodes, edges = await service.graph()
    settings = get_settings().nav
    vision_available = await ModelVisionClient(request.app.state).available()
    repository = ScreenshotRepository(session)
    return NavGraphOut(
        nodes=[await _node_out(repository, node) for node in nodes],
        edges=[_edge_out(edge) for edge in edges],
        enabled=settings.enabled,
        learn=settings.learn,
        vision_available=vision_available,
    )


# ----------------------------------------------------------------------
# PUT /api/agent/nav/nodes/{node_id}
# ----------------------------------------------------------------------
async def _recompute_exemplars(
    repository: ScreenshotRepository,
    node: NavNode,
    rois: Sequence[Sequence[float]],
) -> list[dict[str, Any]]:
    """rois 改了之后按每个样本的截图重算 phash；文件丢失或没有截图的样本删掉。"""
    recomputed: list[dict[str, Any]] = []
    for item in node.exemplars or []:
        if not isinstance(item, Mapping):
            continue
        screenshot_id = item.get("screenshot_id")
        if not isinstance(screenshot_id, str) or not screenshot_id:
            continue
        screenshot = await repository.get(screenshot_id)
        if screenshot is None or screenshot.deleted_at is not None:
            continue
        image = _read_image(screenshot)
        if image is None:
            continue
        entry = dict(item)
        entry["phash"] = signature(image, rois)
        recomputed.append(entry)
    return recomputed


async def _add_current_exemplar(
    request: Request,
    session: AsyncSession,
    service: NavGraphService,
    node_id: str,
    recognizer: Mapping[str, Any],
) -> None:
    """现截一张图（只读设备、不取租约），归档并提交后写成 user 样本。"""
    manager = _device_manager(request)
    result = await manager.screenshot(backend="adb")
    image = result.image
    _row, stored = await archive_screenshot(
        session,
        image,
        trigger=ScreenshotTrigger.AGENT,
        backend=ScreenshotBackend.ADB,
    )
    # 工具/路由的会话不替归档提交（spec §4.5.2）：先提交，再让图服务自己开会话加样本。
    await session.commit()
    await service.add_exemplar(
        node_id,
        phash=signature(image, _rois(recognizer)),
        screenshot_id=str(stored["screenshot_id"]),
        source="user",
    )


async def _add_screenshot_exemplar(
    service: NavGraphService,
    repository: ScreenshotRepository,
    node_id: str,
    screenshot_id: str,
    recognizer: Mapping[str, Any],
) -> None:
    """用已归档的截图加样本；不重新归档，404/410 照截图归档的规则报。"""
    screenshot = await repository.get(screenshot_id)
    if screenshot is None:
        raise AppError(
            ErrorCode.SCREENSHOT_NOT_FOUND,
            "截图不存在",
            {"screenshot_id": screenshot_id},
        )
    if screenshot.deleted_at is not None:
        raise AppError(
            ErrorCode.SCREENSHOT_EXPIRED,
            "截图已过期或已清理",
            {"screenshot_id": screenshot_id},
        )
    image = _read_image(screenshot)
    if image is None:
        raise AppError(
            ErrorCode.SCREENSHOT_EXPIRED,
            "截图文件已清理",
            {"screenshot_id": screenshot_id},
        )
    await service.add_exemplar(
        node_id,
        phash=signature(image, _rois(recognizer)),
        screenshot_id=str(screenshot.id),
        source="user",
    )


@router.put(
    "/nodes/{node_id}",
    response_model=NavNodeOut,
    summary="新建或修改导航界面",
    description=(
        "新建界面时 origin 记为 user，修改已有界面不改 origin；"
        "可以同时替换特征 rois、标记当前画面为样本或按截图 id 加样本。"
    ),
    responses=error_responses(
        "INVALID_PARAMETER",
        "SCREENSHOT_NOT_FOUND",
        "SCREENSHOT_EXPIRED",
        "DEVICE_NOT_CONNECTED",
        "SERVICE_UNAVAILABLE",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def put_nav_node(
    payload: NavNodePut,
    request: Request,
    node_id: str = PathParam(description="界面 id（短 slug 或 stage:<关卡代号>）"),
    session: AsyncSession = Depends(get_session),
) -> NavNodeOut:
    service = _service(request)
    if payload.add_exemplar is not None and payload.add_exemplar_screenshot_id:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "add_exemplar 与 add_exemplar_screenshot_id 只能选一个",
            {"field": "add_exemplar"},
        )
    if NODE_ID_PATTERN.fullmatch(node_id) is None:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "界面 id 不合法",
            {"field": "node_id"},
        )

    existing = await service.get_node(node_id)
    origin = "user" if existing is None else existing.origin
    if payload.recognizer is not None:
        recognizer = payload.recognizer.model_dump(mode="json")
    elif existing is not None:
        recognizer = dict(existing.recognizer or {})
    else:
        recognizer = NavRecognizerModel().model_dump(mode="json")
    rois_changed = (
        existing is not None
        and payload.recognizer is not None
        and _rois(existing.recognizer) != [list(item) for item in payload.recognizer.rois]
    )

    node = await service.upsert_node(
        node_id,
        title=payload.title,
        kind=payload.kind,
        origin=origin,
        recognizer=recognizer,
    )
    repository = ScreenshotRepository(session)
    if rois_changed:
        node = await service.set_exemplars(
            node_id,
            await _recompute_exemplars(repository, node, payload.recognizer.rois),
        )
    if payload.remove_exemplars:
        removed = set(payload.remove_exemplars)
        current = await service.get_node(node_id)
        current_items = list(current.exemplars or []) if current is not None else []
        kept = [
            dict(item)
            for item in current_items
            if isinstance(item, Mapping)
            and str(item.get("phash") or "") not in removed
        ]
        node = await service.set_exemplars(node_id, kept)
    if payload.add_exemplar == "current":
        await _add_current_exemplar(
            request, session, service, node_id, recognizer
        )
    elif payload.add_exemplar_screenshot_id:
        await _add_screenshot_exemplar(
            service,
            repository,
            node_id,
            payload.add_exemplar_screenshot_id,
            recognizer,
        )
    node = await service.get_node(node_id)
    assert node is not None  # upsert 之后一定存在
    return await _node_out(repository, node)


# ----------------------------------------------------------------------
# PUT / DELETE /api/agent/nav/edges/{edge_id}
# ----------------------------------------------------------------------
@router.put(
    "/edges/{edge_id}",
    response_model=NavEdgeOut,
    summary="新建或修改导航跳转",
    description=(
        "不存在时按 from_node/to_node/action 新建（id 取路径里的 UUID）；"
        "存在时只能改 enabled、action 与 cost_ms，改端点要删旧建新。"
    ),
    responses=error_responses(
        "NOT_FOUND", "NAV_NODE_NOT_FOUND", "INVALID_PARAMETER", "UNAUTHORIZED"
    ),
    dependencies=[Depends(require_auth)],
)
async def update_nav_edge(
    payload: NavEdgePut,
    request: Request,
    edge_id: str = PathParam(description="跳转 id（UUID）"),
) -> NavEdgeOut:
    service = _service(request)
    try:
        uuid.UUID(edge_id)
    except ValueError as exc:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "跳转 id 必须是 UUID",
            {"field": "edge_id"},
        ) from exc

    edge = await service.get_edge(edge_id)
    if edge is None:
        if (
            payload.from_node is None
            or payload.to_node is None
            or payload.action is None
        ):
            raise AppError(
                ErrorCode.NOT_FOUND, "跳转不存在", {"edge_id": edge_id}
            )
        for node_id in (payload.from_node, payload.to_node):
            if await service.get_node(node_id) is None:
                raise AppError(
                    ErrorCode.NAV_NODE_NOT_FOUND,
                    f"导航图里没有界面 {node_id}",
                    {"node_id": node_id},
                )
        edge, _created = await service.upsert_edge(
            from_node=payload.from_node,
            to_node=payload.to_node,
            action=payload.action,
            origin="user",
            edge_id=edge_id,
        )
        return _edge_out(edge)

    if payload.from_node is not None and payload.from_node != edge.from_node:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "不能改跳转的起点，请删旧建新",
            {"field": "from_node"},
        )
    if payload.to_node is not None and payload.to_node != edge.to_node:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            "不能改跳转的终点，请删旧建新",
            {"field": "to_node"},
        )
    edge = await service.update_edge(
        edge_id,
        enabled=payload.enabled,
        action=payload.action,
        cost_ms=payload.cost_ms,
    )
    return _edge_out(edge)


@router.delete(
    "/edges/{edge_id}",
    status_code=204,
    response_class=Response,
    summary="删除导航跳转",
    description="物理删除一条跳转；种子跳转删掉后不会在重启时自动写回。",
    responses=error_responses("NOT_FOUND", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def delete_nav_edge(
    request: Request,
    edge_id: str = PathParam(description="跳转 id"),
) -> Response:
    service = _service(request)
    edge = await service.get_edge(edge_id)
    if edge is None:
        raise AppError(ErrorCode.NOT_FOUND, "跳转不存在", {"edge_id": edge_id})
    await service.delete_edge(edge_id)
    return Response(status_code=204)
