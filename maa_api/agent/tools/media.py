"""M23 媒体工具：把截图、物品图标、关卡示意图与图表卡发给用户。

五个工具都声明 ``ToolRisk.SAFE``（政策引擎直接放行、不弹确认，与 ``get_screenshot``
同级；调用照常进审计），组 ``media``。``annotate_screenshot`` 会写文件与数据库行，
所以 ``read_only=False``（批内串行）；其余四个只读。

REST 调用与内置会话走同一条注册表路径：五个工具的返回值都不依赖 runtime 的
``on_audit_prepare`` 钩子，所以两边拿到同样的 JSON（``get_screenshot`` 的归档例外，
它只发生在 runtime 钩子里，本模块不动它）。
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.agent import media as media_contract
from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.db import session as db_session
from maa_api.db.models import Screenshot
from maa_api.db.repositories.agent import AgentMessageRepository
from maa_api.db.repositories.log import ScreenshotRepository
from maa_api.domain.enums import ScreenshotBackend, ScreenshotTrigger
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.resource_index import (
    find_by_name,
    load_item_index,
    resolve_icon_path,
)
from maa_api.services.stage_layout import load_stage_layout
from maa_api.services.stage_resolver import StageResolver
from maa_api.util.image import render_annotation, store_screenshot

__all__ = [
    "AnnotateScreenshotParams",
    "SendChartParams",
    "SendItemIconsParams",
    "SendScreenshotParams",
    "SendStageMapParams",
    "register_tools",
]

#: 圈选产物的落盘质量。**必须显式给**：默认会取 ``adb.screenshot_quality``（25），
#: 放大图会被二次压缩毁掉（规格 §3.3，有意的偏离，代价是图比普通截图大）。
ANNOTATION_QUALITY = 90
#: 图表规格的两级上限（规格 §3.5）：总点数与序列化字节数。
MAX_CHART_POINTS = 600
MAX_CHART_BYTES = 24 * 1024
#: 单人会话上下文里最多保留几张卡（规格 §3.1 的前端上限，后端按项数已限死）。
MAX_ITEM_ICONS = 8
MAX_CHART_SERIES = 6
MAX_CHART_CATEGORIES = 200

_IMAGE_PATH_SUFFIXES = (".jpg", ".jpeg", ".png")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
#: 一条消息里搜 ``screenshot_id`` 的最大深度（``{"result": {"media": [ {...} ]}}``）。
_SCREENSHOT_SEARCH_DEPTH = 4


class SendScreenshotParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    screenshot_id: str | None = Field(default=None, min_length=1, max_length=36)
    pipeline_id: str | None = Field(default=None, min_length=1, max_length=36)
    trigger: Literal["agent", "manual", "task_snapshot", "crash", "update"] | None = None
    caption: str | None = Field(default=None, max_length=300)


class CropParams(BaseModel):
    """原图归一化矩形（0–1，原点左上）。"""

    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(ge=0.01, le=1)
    height: float = Field(ge=0.01, le=1)


class BoxParams(CropParams):
    label: str | None = Field(default=None, max_length=32)


class AnnotateScreenshotParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    screenshot_id: str | None = Field(default=None, min_length=1, max_length=36)
    #: 与 ``send_screenshot`` 同一套源图解析（规格 §3.2：「两个工具都是这条规则」）。
    pipeline_id: str | None = Field(default=None, min_length=1, max_length=36)
    trigger: Literal["agent", "manual", "task_snapshot", "crash", "update"] | None = None
    crop: CropParams | None = None
    zoom: float = Field(default=1.0, ge=1, le=8)
    boxes: list[BoxParams] = Field(default_factory=list, max_length=16)
    caption: str | None = Field(default=None, max_length=300)


class SendItemIconsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(
        min_length=1, max_length=MAX_ITEM_ICONS
    )
    caption: str | None = Field(default=None, max_length=300)


class SendStageMapParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str = Field(min_length=1, max_length=64)
    caption: str | None = Field(default=None, max_length=300)


class ChartSeriesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=40)
    #: ``null`` 表示断点（折线断开），非有限数值在处理器里被拒。
    values: list[float | None] = Field(min_length=1, max_length=MAX_CHART_CATEGORIES)


class SendChartParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chart_type: Literal["line", "bar", "pie"]
    title: str = Field(min_length=1, max_length=80)
    categories: list[Annotated[str, Field(min_length=1, max_length=40)]] = Field(
        min_length=1, max_length=MAX_CHART_CATEGORIES
    )
    series: list[ChartSeriesParams] = Field(
        min_length=1, max_length=MAX_CHART_SERIES
    )
    stacked: bool = False
    x_label: str | None = Field(default=None, max_length=40)
    y_label: str | None = Field(default=None, max_length=40)
    unit: str | None = Field(default=None, max_length=16)
    caption: str | None = Field(default=None, max_length=300)
    source: str | None = Field(default=None, max_length=200)


@dataclass(frozen=True, slots=True)
class _SourceScreenshot:
    """一张可读的源截图：记录 + 内容哈希（拿不到时为 ``None``）+ 已校验的文件路径。"""

    record: Screenshot
    sha256: str | None
    path: Path


def register_tools(registry: ToolRegistry) -> None:
    """注册 ``media`` 组的五个工具。"""
    registry.register(
        ToolDefinition(
            name="send_screenshot",
            group="media",
            risk=ToolRisk.SAFE,
            description=(
                "把一张已有截图作为图片卡发给用户：给 screenshot_id 就用它，"
                "只给 pipeline_id 就取该流水线最新一张，都不给＝本会话最近一张截图。"
            ),
            params_model=SendScreenshotParams,
            handler=send_screenshot,
            read_only=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="annotate_screenshot",
            group="media",
            risk=ToolRisk.SAFE,
            description=(
                "按原图归一化坐标（0–1）裁剪、放大截图并画红色标注框，生成一张新图"
                "发给用户；文字说不清画面时用它把关键区域圈出来。"
                "源图给 screenshot_id，或用 pipeline_id 取该流水线最新一张"
                "（可用 trigger 过滤）；都不给＝本会话最近一张截图。"
            ),
            params_model=AnnotateScreenshotParams,
            handler=annotate_screenshot,
            read_only=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="send_item_icons",
            group="media",
            risk=ToolRisk.SAFE,
            description="按物品 ID 或中文名发送 1–8 个材料图标，展示给用户。",
            params_model=SendItemIconsParams,
            handler=send_item_icons,
            read_only=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="send_stage_map",
            group="media",
            risk=ToolRisk.SAFE,
            description=(
                "按关卡编号（如 1-7）或 stageId 发送关卡布局示意图，"
                "数据来自本地 tile 资源。"
            ),
            params_model=SendStageMapParams,
            handler=send_stage_map,
            read_only=True,
        )
    )
    registry.register(
        ToolDefinition(
            name="send_chart",
            group="media",
            risk=ToolRisk.SAFE,
            description=(
                "把结构化图表规格（line / bar / pie）发给用户；"
                "数据必须来自工具真实返回，并在 source 里写明出处。"
            ),
            params_model=SendChartParams,
            handler=send_chart,
            read_only=True,
        )
    )


async def send_screenshot(
    params: SendScreenshotParams, context: ToolContext
) -> dict[str, Any]:
    """把一张已有截图发给用户（规格 §3.2）。"""
    source = await _resolve_source(
        context,
        screenshot_id=params.screenshot_id,
        pipeline_id=params.pipeline_id,
        trigger=params.trigger,
        rest_hint="screenshot_id 或 pipeline_id",
    )
    card = _image_card(source, caption=params.caption, alt=params.caption or "游戏截图")
    return media_contract.media_result([card])


async def annotate_screenshot(
    params: AnnotateScreenshotParams, context: ToolContext
) -> dict[str, Any]:
    """裁剪 / 放大 / 画框，生成新图并落一条 ``trigger=agent`` 的截图记录。"""
    source = await _resolve_source(
        context,
        screenshot_id=params.screenshot_id,
        pipeline_id=params.pipeline_id,
        trigger=params.trigger,
        rest_hint="screenshot_id 或 pipeline_id",
    )
    rendered = await asyncio.to_thread(
        render_annotation,
        source.path,
        crop=params.crop.model_dump() if params.crop is not None else None,
        zoom=params.zoom,
        boxes=[box.model_dump() for box in params.boxes],
    )
    stored = await asyncio.to_thread(
        store_screenshot, rendered.image, quality=ANNOTATION_QUALITY
    )
    session = _required_session(context)
    record = await ScreenshotRepository(session).create(
        Screenshot(
            trigger=ScreenshotTrigger.AGENT,
            backend=_backend(source.record.backend),
            path=_stored_relative_path(stored["sha256"]),
            format="jpg",
            width=int(stored["width"]),
            height=int(stored["height"]),
            size_bytes=int(stored["bytes"]),
        )
    )
    # 工具处理器的会话由确认服务创建、不替它提交：产物行必须在这里落库，
    # 否则返回的 screenshot_id 指向一条随会话关闭回滚的记录（规格 §3.3）。
    await session.commit()
    card = media_contract.image_card(
        screenshot_id=record.id,
        sha256=stored["sha256"],
        caption=_annotated_caption(params.caption, rendered.zoom_applied),
        alt="圈选截图",
        width=int(stored["width"]),
        height=int(stored["height"]),
    )
    return media_contract.media_result([card])


async def send_item_icons(
    params: SendItemIconsParams, context: ToolContext
) -> dict[str, Any]:
    """按 ``item_id`` 或中文名解析物品并发送图标卡（规格 §3.2）。"""
    resolved, missing = await asyncio.to_thread(_resolve_items, params.items)
    if missing:
        raise AppError(
            ErrorCode.RESOURCE_ASSET_NOT_FOUND,
            f"物品不存在或没有可用图标：{'、'.join(missing)}",
            {"missing": missing},
        )
    return media_contract.media_result(
        [media_contract.item_icons_card(resolved, caption=params.caption)]
    )


async def send_stage_map(
    params: SendStageMapParams, context: ToolContext
) -> dict[str, Any]:
    """按本地 tile 数据渲染关卡布局；未命中时再用既有别名解析兜底（规格 §3.4）。"""
    stage: dict[str, Any] | None = None
    try:
        stage = await asyncio.to_thread(load_stage_layout, params.stage)
    except AppError as exc:
        if exc.code is not ErrorCode.RESOURCE_ASSET_NOT_FOUND:
            raise
    if stage is None:
        alias = await _resolve_stage_alias(context, params.stage)
        if alias is not None:
            try:
                stage = await asyncio.to_thread(load_stage_layout, alias)
            except AppError:
                stage = None
    if stage is None:
        raise AppError(
            ErrorCode.RESOURCE_ASSET_NOT_FOUND,
            f"关卡不存在：{params.stage}",
            {"stage": params.stage},
        )
    card = media_contract.stage_map_card(stage, caption=params.caption)
    return media_contract.media_result([card])


async def send_chart(params: SendChartParams, context: ToolContext) -> dict[str, Any]:
    """校验并转发图表规格；服务端不做聚合、不补数据（规格 §3.5）。"""
    chart = _chart_spec(params)
    card = media_contract.chart_card(chart, caption=params.caption)
    return media_contract.media_result([card])


def _chart_spec(params: SendChartParams) -> dict[str, Any]:
    """把参数收成卡片里的 ``chart`` 对象，并做 §3.5 的语义校验。"""
    for index, series in enumerate(params.series):
        for value in series.values:
            if value is not None and not math.isfinite(value):
                raise AppError(
                    ErrorCode.INVALID_PARAMETER,
                    f"series[{index}].values 含非有限数值：{value!r}；"
                    "NaN / Infinity 不允许，断点请用 null",
                    {"series": index, "value": repr(value)},
                )
    if params.chart_type in {"line", "bar"}:
        for index, series in enumerate(params.series):
            if len(series.values) != len(params.categories):
                raise AppError(
                    ErrorCode.INVALID_PARAMETER,
                    f"series[{index}].values 有 {len(series.values)} 个点，"
                    f"categories 有 {len(params.categories)} 项，两者必须相等",
                    {
                        "series": index,
                        "values": len(series.values),
                        "categories": len(params.categories),
                    },
                )
    total_points = sum(len(series.values) for series in params.series)
    if total_points > MAX_CHART_POINTS:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"总点数 {total_points} 超过上限 {MAX_CHART_POINTS}，请减少类目或系列",
            {"points": total_points, "limit": MAX_CHART_POINTS},
        )

    chart = {
        "chart_type": params.chart_type,
        "title": params.title,
        "categories": list(params.categories),
        "series": [
            {"name": series.name, "values": list(series.values)}
            for series in params.series
        ],
        "stacked": params.stacked,
        "x_label": params.x_label,
        "y_label": params.y_label,
        "unit": params.unit,
        "source": params.source,
    }
    encoded = len(
        json.dumps(
            {**chart, "caption": params.caption},
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )
    if encoded > MAX_CHART_BYTES:
        raise AppError(
            ErrorCode.INVALID_PARAMETER,
            f"图表规格序列化后 {encoded} 字节，超过上限 {MAX_CHART_BYTES} 字节",
            {"bytes": encoded, "limit": MAX_CHART_BYTES},
        )
    return chart


async def _resolve_source(
    context: ToolContext,
    *,
    screenshot_id: str | None = None,
    pipeline_id: str | None = None,
    trigger: str | None = None,
    rest_hint: str,
) -> _SourceScreenshot:
    """定位源截图并按规格 §3.2 报错：查不到 / 已清理 / REST 缺 id 三种收场。"""
    session = _required_session(context)
    repository = ScreenshotRepository(session)

    record: Screenshot | None = None
    if screenshot_id:
        record = await repository.get(screenshot_id)
        if record is None:
            raise AppError(
                ErrorCode.SCREENSHOT_NOT_FOUND,
                f"截图不存在：{screenshot_id}",
                {"screenshot_id": screenshot_id},
            )
    elif pipeline_id:
        page = await repository.list_page(
            pipeline_id=pipeline_id, trigger=trigger, page=1, size=1
        )
        if not page.items:
            raise AppError(
                ErrorCode.SCREENSHOT_NOT_FOUND,
                f"流水线 {pipeline_id} 下没有符合条件的截图",
                {"pipeline_id": pipeline_id, "trigger": trigger},
            )
        record = page.items[0]
    else:
        if not context.session_id:
            raise AppError(
                ErrorCode.INVALID_PARAMETER,
                f"REST 调用没有会话上下文，必须显式提供 {rest_hint}",
            )
        found = await _latest_session_screenshot_id(session, context.session_id)
        if found is None:
            raise AppError(
                ErrorCode.SCREENSHOT_NOT_FOUND,
                "本会话还没有截图，请先调用 get_screenshot 后再发图",
                {"session_id": context.session_id},
            )
        record = await repository.get(found)
        if record is None:
            raise AppError(
                ErrorCode.SCREENSHOT_NOT_FOUND,
                f"截图不存在：{found}",
                {"screenshot_id": found},
            )

    if record.deleted_at is not None:
        raise AppError(
            ErrorCode.SCREENSHOT_EXPIRED,
            "截图文件已过期清理",
            {"screenshot_id": record.id},
        )
    path = _screenshot_file(record)
    if path is None:
        raise AppError(
            ErrorCode.SCREENSHOT_EXPIRED,
            "截图文件不存在或已过期清理",
            {"screenshot_id": record.id},
        )
    return _SourceScreenshot(
        record=record, sha256=_sha256_from_path(record.path), path=path
    )


async def _latest_session_screenshot_id(
    session: AsyncSession, session_id: str
) -> str | None:
    """从本会话的消息里倒着找第一条带 ``screenshot_id`` 的工具消息。

    ``screenshot`` 表没有 session 列，所以「本会话最近一张截图」只能从消息里推：
    ``get_screenshot`` / ``annotate_screenshot`` / ``send_screenshot`` 的结果里都带 id。
    """
    messages = await AgentMessageRepository(session).list_by_session(session_id)
    for message in reversed(messages):
        if str(getattr(message, "role", "")) != "tool":
            continue
        found = _find_screenshot_id(message.content)
        if found is not None:
            return found
    return None


def _find_screenshot_id(content: Any) -> str | None:
    payload = content
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return None
    return _search_screenshot_id(payload, 0)


def _search_screenshot_id(value: Any, depth: int) -> str | None:
    if depth > _SCREENSHOT_SEARCH_DEPTH:
        return None
    if isinstance(value, Mapping):
        candidate = value.get("screenshot_id")
        if isinstance(candidate, str) and candidate:
            return candidate
        for key in ("result", "media"):
            if key in value:
                found = _search_screenshot_id(value[key], depth + 1)
                if found is not None:
                    return found
        return None
    if isinstance(value, (list, tuple)):
        for item in value:
            found = _search_screenshot_id(item, depth + 1)
            if found is not None:
                return found
    return None


def _screenshot_file(record: Screenshot) -> Path | None:
    """把 ``Screenshot.path``（相对 ``resource/``）解析成允许目录内的真实文件。

    与 ``api/routers/screenshots.py`` 的读取规则一致，但额外把范围收在
    ``image/screenshot`` 之内：工具只该读截图存储，不该被记录里的路径带去别处。
    """
    root = Path(db_session.DB_PATH).parent
    if not isinstance(record.path, str) or not record.path:
        return None
    try:
        allowed = (root / "image" / "screenshot").resolve()
        candidate = (root / record.path).resolve()
        candidate.relative_to(allowed)
    except (OSError, RuntimeError, ValueError):
        return None
    if not candidate.is_file():
        return None
    return candidate


def _sha256_from_path(path: str) -> str | None:
    """内容寻址存储的文件名就是 sha256（``<sha>.jpg``）；不符合就返回 ``None``。"""
    if not isinstance(path, str):
        return None
    name = Path(path).name
    for suffix in _IMAGE_PATH_SUFFIXES:
        if name.lower().endswith(suffix):
            stem = name[: -len(suffix)]
            return stem if _SHA256_PATTERN.fullmatch(stem) else None
    return None


def _stored_relative_path(sha256: str) -> str:
    return f"image/screenshot/{sha256[:2]}/{sha256}.jpg"


def _image_card(
    source: _SourceScreenshot, *, caption: str | None, alt: str | None
) -> dict[str, Any]:
    record = source.record
    return media_contract.image_card(
        screenshot_id=record.id,
        sha256=source.sha256,
        caption=caption,
        alt=alt,
        width=record.width,
        height=record.height,
    )


def _backend(value: Any) -> ScreenshotBackend:
    try:
        return ScreenshotBackend(str(value))
    except ValueError:
        return ScreenshotBackend.ADB


def _annotated_caption(caption: str | None, zoom_applied: float) -> str:
    note = f"实际倍数 ×{round(zoom_applied, 2):g}"
    base = caption.strip() if isinstance(caption, str) and caption.strip() else "圈选截图"
    return f"{base}（{note}）"


def _resolve_items(
    queries: Sequence[str],
) -> tuple[list[dict[str, str]], list[str]]:
    """先按 ``item_id`` 再按中文名解析；返回命中的图标项与没命中的原始写法。"""
    index = load_item_index()
    resolved: list[dict[str, str]] = []
    missing: list[str] = []
    for raw in queries:
        query = raw.strip()
        item_id = query if query in index else find_by_name(query)
        if item_id is None or item_id not in index:
            missing.append(raw)
            continue
        try:
            resolve_icon_path(item_id)
        except AppError:
            missing.append(raw)
            continue
        resolved.append({"item_id": item_id, "name": index[item_id]["name"]})
    return resolved, missing


async def _resolve_stage_alias(context: ToolContext, query: str) -> str | None:
    """用既有 :class:`StageResolver` 试别名；没有可用的服务/内核就跳过。

    取服务的顺序与 ``resolve_stage`` 工具一致：``app.state.stage_resolver`` 优先，
    缺它就用 ``app.state.core_registry`` 现建一个（``StageResolver(None)`` 仍能查本地
    ``stages.json``）。别名只是兜底，任何失败都按「没解析出来」处理。
    """
    state = getattr(getattr(context.request, "app", None), "state", None)
    if state is None:
        return None
    resolver = getattr(state, "stage_resolver", None)
    if resolver is None:
        resolver = StageResolver(getattr(state, "core_registry", None))
    try:
        result = await resolver.resolve(query)
    except Exception:  # noqa: BLE001 - 兜底路径不该把「没查到」变成报错
        return None
    resolved = result.get("resolved") if isinstance(result, Mapping) else None
    if not isinstance(resolved, Mapping):
        return None
    for key in ("stage_id", "level_id", "code"):
        value = resolved.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _required_session(context: ToolContext) -> AsyncSession:
    session = context.db_session
    if session is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, "工具上下文未提供数据库会话")
    return session
