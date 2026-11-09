"""M88 T4：刷图方案的 Markdown 报告与产物标题（规格 §4.5.6）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from maa_api.services.economy.report import render_report, report_title

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone(timedelta(hours=8)))


def sample_result() -> dict[str, Any]:
    return {
        "requirements": {"异铁块": 10, "聚酸酯组": 5},
        "explanation": (
            "凑齐需要 300 理智，按当前理智 0 与每天 240 的自然恢复约 1.3 天。"
            "主要刷 1-7 ×10。"
        ),
        "stages": [
            {
                "stage_id": "main_01-07",
                "stage_code": "1-7",
                "runs": 10,
                "sanity_per_run": 30,
                "sanity": 300,
                "event": False,
                "activity": None,
                "deadline": None,
                "navigable": True,
                "tokens_per_run": None,
                "items": [{"item_name": "异铁块", "rate": 1.0, "sanity_per_item": 30.0}],
            }
        ],
        "shop": [
            {
                "activity": "昨日海",
                "item": "异铁块",
                "quantity": 10,
                "bundle": 1,
                "price": 40,
                "tokens": 400,
                "stock": 10,
                "section": "金色阶段",
                "value_sanity": 300.0,
            }
        ],
        "synthesis": [],
        "medicine": {"note": "没有用药：用户没有同意用理智药"},
        "notes": ["按每天自然恢复 240 理智估算，不含月卡与活动赠送"],
        "sources": [
            {"kind": "penguin", "label": "企鹅物流"},
            {
                "kind": "prts",
                "label": "PRTS",
                "title": "昨日海",
                "fetched_at": NOW.isoformat(),
            },
            {
                "kind": "gamedata",
                "label": "拆包数据",
                "title": "item_table.json",
                "fetched_at": NOW.isoformat(),
            },
        ],
    }


def test_report_sections_and_no_raw_json() -> None:
    body = render_report(sample_result(), generated_at=NOW)

    assert "刷图清单" in body
    assert "商店兑换" in body
    assert "1-7" in body
    assert '{"' not in body


def test_empty_sections_are_omitted() -> None:
    result = sample_result()
    result["shop"] = []
    result["synthesis"] = []

    body = render_report(result, generated_at=NOW)

    assert "商店兑换" not in body
    assert "合成" not in body


def test_shop_portions_divide_total_quantity_by_bundle() -> None:
    result = sample_result()
    result["shop"] = [
        {
            "activity": "昨日海",
            "item": "异铁块",
            "quantity": 20,
            "bundle": 10,
            "price": 40,
            "tokens": 80,
            "stock": 2,
            "section": "金色阶段",
            "value_sanity": 600.0,
        }
    ]

    body = render_report(result, generated_at=NOW)

    row = next(line for line in body.splitlines() if line.startswith("| 异铁块 |"))
    assert row == "| 异铁块 | 2 | 10 | 40 | 80 | 2 | 600 |"


def test_empty_needs_title() -> None:
    assert report_title({"requirements": {}}) == "刷图方案：没有剩余需求"
    assert report_title({"requirements": {"异铁块": 10}}) == "刷图方案：异铁块"
    assert (
        report_title({"requirements": {"异铁块": 10, "聚酸酯组": 5}})
        == "刷图方案：异铁块 与另外 1 种材料"
    )


def test_data_sources_render_only_existing() -> None:
    result = sample_result()

    body = render_report(result, generated_at=NOW)

    assert "企鹅物流掉率" in body
    assert "PRTS「昨日海」" in body
    assert "拆包数据 item_table.json" in body

    result["sources"] = [{"kind": "penguin", "label": "企鹅物流"}]
    only_penguin = render_report(result, generated_at=NOW)
    assert "PRTS「" not in only_penguin
    assert "拆包数据" not in only_penguin
