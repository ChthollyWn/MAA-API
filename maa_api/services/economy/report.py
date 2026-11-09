"""M88 T4：刷图方案的 Markdown 报告与产物标题（规格 §4.5.6）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

__all__ = ["render_report", "report_title"]

#: 产物标题上限（与产物服务的 title 上限一致）。
_TITLE_LIMIT = 120


def report_title(result: Mapping[str, Any]) -> str:
    """需求为空 / 一种 / 多种三种标题规则；截到 120 字。"""
    requirements = result.get("requirements") or {}
    names = list(requirements)
    if not names:
        title = "刷图方案：没有剩余需求"
    elif len(names) == 1:
        title = f"刷图方案：{names[0]}"
    else:
        title = f"刷图方案：{names[0]} 与另外 {len(names) - 1} 种材料"
    return title[:_TITLE_LIMIT]


def render_report(result: Mapping[str, Any], *, generated_at: datetime) -> str:
    """§4.5.6 的 Markdown；没有数据的小节整节省略。"""
    lines: list[str] = ["# 刷图方案", ""]

    requirements = result.get("requirements") or {}
    if requirements:
        lines.append(
            "- 需求：" + "、".join(f"{name} ×{count}" for name, count in requirements.items())
        )
    explanation = str(result.get("explanation") or "")
    if explanation:
        lines.append("- 结论：" + _first_sentence(explanation))
    lines.append("")

    stages = result.get("stages") or []
    if stages:
        lines += [
            "## 刷图清单",
            "",
            "| 关卡 | 次数 | 每次理智 | 合计理智 | 主要产出 | 截止 |",
            "|---|---|---|---|---|---|",
        ]
        for row in stages:
            outputs = "、".join(
                f"{item.get('item_name')} ×{_number(item.get('rate'))}"
                for item in (row.get("items") or [])
                if item.get("item_name")
            )
            lines.append(
                f"| {row.get('stage_code') or row.get('stage_id')} | {row.get('runs')} "
                f"| {row.get('sanity_per_run')} | {row.get('sanity')} "
                f"| {outputs or '—'} | {row.get('deadline') or '—'} |"
            )
        lines.append("")

    shop = result.get("shop") or []
    if shop:
        lines += [
            "## 商店兑换",
            "",
            "| 物品 | 份数 | 每份 | 单价（代币） | 合计代币 | 库存 | 折合理智 |",
            "|---|---|---|---|---|---|---|",
        ]
        for row in shop:
            stock = row.get("stock")
            value = row.get("value_sanity")
            lines.append(
                f"| {row.get('item')} | {_portions(row)} | {row.get('bundle')} "
                f"| {row.get('price')} | {row.get('tokens')} "
                f"| {'∞' if stock is None else stock} "
                f"| {'—' if value is None else _number(value)} |"
            )
        lines.append("")

    medicine = result.get("medicine") or {}
    if medicine.get("note"):
        lines += ["## 用药", "", str(medicine["note"]), ""]

    synthesis = result.get("synthesis") or []
    if synthesis:
        lines += [
            "## 合成",
            "",
            "| 产物 | 次数 | 龙门币 |",
            "|---|---|---|",
        ]
        for row in synthesis:
            lines.append(
                f"| {row.get('product')} | {row.get('count')} | {row.get('lmd_total')} |"
            )
        lines.append("")

    notes = result.get("notes") or []
    if notes:
        lines += ["## 说明", ""]
        lines += [f"- {note}" for note in notes]
        lines.append("")

    source_lines = _source_lines(result.get("sources") or [], generated_at)
    if source_lines:
        lines += ["## 数据来源", ""]
        lines += [f"- {line}" for line in source_lines]
        lines.append("")

    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def _first_sentence(text: str) -> str:
    head = text.split("。", 1)[0].strip()
    return head + "。" if head else ""


def _portions(row: Mapping[str, Any]) -> Any:
    """商店「份数」列：``quantity`` 是份数 × ``bundle``，这里除回份数。"""
    quantity = row.get("quantity")
    try:
        return int(quantity) // max(int(row.get("bundle") or 1), 1)
    except (TypeError, ValueError):
        return quantity


def _number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:g}"


def _source_lines(sources: Any, generated_at: datetime) -> list[str]:
    """数据来源三行：只在对应来源存在时写；有取数时间就附上（``%Y-%m-%d %H:%M``）。"""
    fallback = generated_at.strftime("%Y-%m-%d %H:%M")
    lines: list[str] = []
    for source in sources:
        kind = source.get("kind")
        stamp = _stamp(source.get("fetched_at"), fallback)
        if kind == "penguin":
            lines.append(f"企鹅物流掉率{stamp}")
        elif kind == "prts":
            lines.append(f"PRTS「{source.get('title') or '活动页'}」{stamp}")
        elif kind == "gamedata":
            lines.append(f"拆包数据 {source.get('title') or 'item_table.json'}{stamp}")
    return lines


def _stamp(value: Any, fallback: str) -> str:
    if not value:
        return f"（{fallback}）"
    text = str(value)
    if "T" in text:
        text = text[:16].replace("T", " ")
    return f"（{text}）"
