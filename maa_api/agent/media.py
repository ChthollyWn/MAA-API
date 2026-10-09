"""媒体卡契约：``result.media`` 信封与四类卡片的**唯一定义处**。

行为以 ``docs/superpowers/specs/2026-09-29-m23-agent-media-cards.md`` §3.1 为准。
产生卡片的工具统一返回::

    {"media": [<卡片>...], "caption": "整批的说明（可选）"}

落库后就是 ``{"result": {"media": [...], "caption": ...}}``（tool 消息的 content）。
同源地址的三种拼法（图片原图 / 缩略图 / 截图记录）都在这里拼出，前端白名单认的
就是它们；物品图标地址由 :func:`item_icon_url` 一处拼出，同样在白名单里。

:func:`compact_media_result` 是 §3.6 的模型上下文占位：媒体结果回灌模型时只留
「发过什么卡」，避免图表规格与 tile 网格每轮重复占 token。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

__all__ = [
    "MEDIA_TOOL_NAMES",
    "MEDIA_CARD_TYPES",
    "MAX_MEDIA_CARDS",
    "compact_media_result",
    "image_card",
    "item_icon_url",
    "item_icons_card",
    "media_result",
    "stage_map_card",
    "chart_card",
    "thumb_url_for_sha256",
    "url_for_sha256",
    "url_for_screenshot",
]

#: 产生媒体卡的工具；runtime 的 :func:`build_model_context` 按它压缩历史结果。
MEDIA_TOOL_NAMES = frozenset(
    {
        "send_screenshot",
        "annotate_screenshot",
        "send_item_icons",
        "send_stage_map",
        "send_chart",
    }
)

MEDIA_CARD_TYPES = frozenset({"image", "item_icons", "stage_map", "chart"})

#: 一批结果里最多几张卡（规格 §3.1：一次 ≤ 8 张图）。工具层已经按项数限死，
#: 这里是契约层的兜底裁剪。
MAX_MEDIA_CARDS = 8

#: 卡片标题/摘要进模型占位时的长度上限：占位只用来让模型知道「发过什么」。
_SUMMARY_TITLE_CHARS = 80

#: §3.6 的占位说明原文（成功路径）。
_MEDIA_SENT_NOTE = "媒体卡已发给用户，历史内容不再重复"
#: 没有卡片可保留（工具返回错误或空结果）时的占位说明。
_MEDIA_EMPTY_NOTE = "媒体工具结果已省略，历史内容不再重复"
#: 媒体工具失败时的占位说明：错误必须原样回灌，否则模型看不到越界原因、无法改一版重发。
_MEDIA_ERROR_NOTE = "媒体工具调用失败，错误原样保留"


def url_for_sha256(sha256: str) -> str:
    """内容寻址原图地址（``GET /api/images/{sha256}/full``）。"""
    return f"/api/images/{sha256}/full"


def thumb_url_for_sha256(sha256: str) -> str:
    """内容寻址缩略图地址（``GET /api/images/{sha256}/thumb``）。"""
    return f"/api/images/{sha256}/thumb"


def url_for_screenshot(screenshot_id: str) -> str:
    """截图记录地址（``GET /api/screenshots/{id}``）。"""
    return f"/api/screenshots/{screenshot_id}"


def item_icon_url(item_id: str) -> str:
    """物品图标地址（``GET /api/resources/items/icon?item_id=``）。"""
    return f"/api/resources/items/icon?item_id={quote(item_id, safe='')}"


def image_card(
    *,
    screenshot_id: str,
    sha256: str | None = None,
    caption: str | None = None,
    alt: str | None = None,
    width: int | None = None,
    height: int | None = None,
) -> dict[str, Any]:
    """构造一张 ``image`` 卡（规格 §3.1）。

    有 ``sha256`` 时用内容寻址地址；没有时回落到截图记录地址：两种都在前端白名单里。
    """
    if not isinstance(screenshot_id, str) or not screenshot_id:
        raise ValueError("image_card 需要 screenshot_id")
    hashed = sha256 if isinstance(sha256, str) and sha256 else None
    if hashed:
        url = url_for_sha256(hashed)
        thumb_url = thumb_url_for_sha256(hashed)
    else:
        # 没有内容哈希时两张图都走截图记录端点：它也在前端白名单里，
        # 比塞 null 更安全（前端对「类型不对」的字段会整卡丢弃）。
        url = url_for_screenshot(screenshot_id)
        thumb_url = url
    return {
        "type": "image",
        "screenshot_id": screenshot_id,
        "sha256": hashed,
        "url": url,
        "thumb_url": thumb_url,
        "alt": alt,
        "caption": caption,
        "width": width,
        "height": height,
    }


def item_icons_card(
    items: Sequence[Mapping[str, Any]], *, caption: str | None = None
) -> dict[str, Any]:
    """构造一张 ``item_icons`` 卡；每项取 ``item_id`` 与 ``name``，图标地址现拼。"""
    normalized: list[dict[str, Any]] = []
    for item in items:
        item_id = item.get("item_id")
        name = item.get("name")
        if not isinstance(item_id, str) or not item_id:
            raise ValueError("item_icons_card 的每一项都需要 item_id")
        normalized.append(
            {
                "item_id": item_id,
                "name": name if isinstance(name, str) else item_id,
                "icon_url": item_icon_url(item_id),
            }
        )
    return {"type": "item_icons", "caption": caption, "items": normalized}


def stage_map_card(stage: Mapping[str, Any], *, caption: str | None = None) -> dict[str, Any]:
    """构造一张 ``stage_map`` 卡；``stage`` 即 :func:`load_stage_layout` 的结果。"""
    return {"type": "stage_map", "caption": caption, "stage": dict(stage)}


def chart_card(chart: Mapping[str, Any], *, caption: str | None = None) -> dict[str, Any]:
    """构造一张 ``chart`` 卡；``chart`` 是服务端只做校验与转发的图表规格。"""
    return {"type": "chart", "caption": caption, "chart": dict(chart)}


def media_result(
    cards: Sequence[Mapping[str, Any]] | None, caption: str | None = None
) -> dict[str, Any]:
    """把卡片包进工具结果的 ``media`` 信封（``caption`` 为空时不出现该键）。"""
    body: dict[str, Any] = {"media": [dict(card) for card in (cards or ())]}
    if caption is not None:
        body["caption"] = caption
    return body


def compact_media_result(tool_name: str, body: Any) -> str:
    """把媒体工具结果压成一行占位 JSON（规格 §3.6），返回**字符串**。

    ``body`` 既接受工具处理器的返回值（``{"media": [...]}``），也接受落库后的 tool
    消息 content（``{"result": {"media": [...]}}``）与它的 JSON 文本。

    三种收场：有卡就保留卡片类型与标题；工具报错就把 ``error`` 原样留下（模型要据此
    改一版重发）；两者都没有就给一句「已省略」。
    """
    payload = _unwrap_result(body)
    error = _payload_error(payload)
    if error is not None:
        return _dump({"result": {"error": error, "note": _MEDIA_ERROR_NOTE}})

    summaries = _card_summaries(payload)
    if not summaries:
        return _dump(
            {
                "result": {
                    "media_sent": [],
                    "note": f"{_MEDIA_EMPTY_NOTE}（{tool_name}）",
                }
            }
        )
    return _dump({"result": {"media_sent": summaries, "note": _MEDIA_SENT_NOTE}})


def _unwrap_result(body: Any) -> Any:
    """剥掉 ``{"result": ...}`` 信封与 JSON 文本，得到处理器返回值。"""
    payload = body
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return None
    if isinstance(payload, Mapping) and "result" in payload:
        inner = payload["result"]
        # 工具结果里的 result 也可能是 str（参数校验失败的旧形态），此时没有卡片可谈。
        return inner if isinstance(inner, Mapping) else None
    return payload


def _payload_error(payload: Any) -> Any:
    if isinstance(payload, Mapping):
        error = payload.get("error")
        if error:
            return error
    return None


def _card_summaries(payload: Any) -> list[dict[str, str]]:
    if not isinstance(payload, Mapping):
        return []
    cards = payload.get("media")
    if not isinstance(cards, list):
        return []
    summaries: list[dict[str, str]] = []
    for card in cards[:MAX_MEDIA_CARDS]:
        summary = _card_summary(card)
        if summary is not None:
            summaries.append(summary)
    return summaries


def _card_summary(card: Any) -> dict[str, str] | None:
    if not isinstance(card, Mapping):
        return None
    card_type = card.get("type")
    if not isinstance(card_type, str) or card_type not in MEDIA_CARD_TYPES:
        return None
    summary = {"type": card_type}
    title = _card_title(card_type, card)
    if title:
        summary["title"] = title[:_SUMMARY_TITLE_CHARS]
    return summary


def _card_title(card_type: str, card: Mapping[str, Any]) -> str | None:
    if card_type == "chart":
        chart = card.get("chart")
        return _text(chart.get("title")) if isinstance(chart, Mapping) else None
    if card_type == "stage_map":
        stage = card.get("stage")
        if isinstance(stage, Mapping):
            code = _text(stage.get("code"))
            name = _text(stage.get("name"))
            if code and name:
                return f"{code} {name}"
            if code or name:
                return code or name
        return _text(card.get("caption"))
    if card_type == "item_icons":
        items = card.get("items")
        if isinstance(items, list):
            names = [
                _text(item.get("name"))
                for item in items
                if isinstance(item, Mapping) and _text(item.get("name"))
            ]
            if names:
                return "、".join(names)
        return _text(card.get("caption"))
    # image
    return _text(card.get("caption")) or _text(card.get("alt"))


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
