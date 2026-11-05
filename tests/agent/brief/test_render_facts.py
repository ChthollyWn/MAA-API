"""M84 T6：简报渲染（spec §4.5.11、§6）。

钉住四件事：空小节整节省略（只剩「理智」与「今日建议」）、模板逐字、成本与
状态文案、analyst 工单说明 ≤4000 字且从尾部截断事实。渲染结果不含感叹号。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from maa_api.agent.brief import (
    BriefFacts,
    render_brief_markdown,
    render_facts_markdown,
    render_ticket_brief,
)
from maa_api.agent.brief.render import (
    NO_SANITY_TEXT,
    SUGGESTION_FACTS_ONLY,
    SUGGESTION_PENDING,
)

DAY = date(2026, 11, 2)


def _facts(**overrides) -> BriefFacts:
    base = BriefFacts(
        date=DAY,
        weekday="周一",
        sanity={
            "estimated": 85,
            "max": 135,
            "full_at_local": "15:20",
            "sampled_at_local": "11-02 08:00",
        },
        schedules_today=[],
        activity_lines=[],
        gacha_lines=[],
        plans=[],
        goals=[],
        pipelines_yesterday={
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
            "failed_items": [],
        },
        drops_yesterday=[],
        tickets_yesterday={"done": 0, "failed": 0, "cancelled": 0, "titles": []},
        cost_yesterday={},
        spend_today=[],
        spend_yesterday={},
        pending={
            "confirmations": 0,
            "questions": 0,
            "tickets": [],
            "tickets_total": 0,
            "alerts": 0,
            "memories": 0,
            "observations": 0,
        },
    )
    return replace(base, **overrides)


def _headings(text: str) -> set[str]:
    return {line for line in text.splitlines() if line.startswith("## ")}


def test_empty_sections_are_omitted() -> None:
    text = render_brief_markdown(_facts(), None, [], "pending")

    assert _headings(text) == {"## 理智", "## 今日建议"}
    assert text.startswith("# 每日简报 · 11 月 2 日（周一）")
    assert "估算当前 85/135，约 15:20 回满" in text
    assert SUGGESTION_PENDING in text
    for marker in (
        "## 今天的定时任务",
        "## 当期活动与关卡",
        "## 卡池",
        "## 养成与目标",
        "## 昨天",
        "## 消耗",
        "## 待处理",
    ):
        assert marker not in text
    assert "!" not in text
    assert "！" not in text


def test_missing_sanity_keeps_the_section() -> None:
    facts = _facts(sanity=None)

    text = render_facts_markdown(facts)

    assert "## 理智" in _headings(text)
    assert NO_SANITY_TEXT in text
    assert "今日建议" not in text
    assert "!" not in text and "！" not in text


def test_full_template_and_status_texts() -> None:
    facts = _facts(
        schedules_today=[
            {"name": "日常", "at_local": "12:00", "fight": True},
            {"name": "基建换班", "at_local": "20:00", "fight": False},
        ],
        activity_lines=["- 活动：`局部坏死` 至 11-14"],
        gacha_lines=["- 限定寻访｜UP：六星/五星｜至 11-15 03:59"],
        plans=[
            {
                "name": "精二 xx",
                "missing_kinds": 12,
                "missing_total": 340,
                "days_estimated": 6.0,
            }
        ],
        goals=[
            {
                "title": "打通主线",
                "deadline_local": "11 月 30 日",
                "done": 2,
                "total": 5,
            }
        ],
        pipelines_yesterday={
            "completed": 3,
            "failed": 1,
            "cancelled": 0,
            "failed_items": [{"title": "日常", "reason": "内核崩溃"}],
        },
        drops_yesterday=[{"item_name": "固源岩", "quantity": 42}],
        tickets_yesterday={
            "done": 2,
            "failed": 1,
            "cancelled": 0,
            "titles": ["清理智"],
        },
        cost_yesterday={"CNY": 1_320_000},
        spend_today=[
            {
                "kind": "medicine",
                "label": "理智药",
                "used": 0,
                "limit": 5,
                "remaining": 5,
            },
            {
                "kind": "stone",
                "label": "源石",
                "used": 0,
                "limit": -1,
                "remaining": None,
            },
            {
                "kind": "expedite",
                "label": "加急许可",
                "used": 1,
                "limit": 3,
                "remaining": 2,
            },
        ],
        spend_yesterday={"medicine": 2, "stone": 0, "expedite": 1},
        pending={
            "confirmations": 2,
            "questions": 1,
            "tickets": ["批准计划"],
            "tickets_total": 1,
            "alerts": 1,
            "memories": 0,
            "observations": 0,
        },
    )

    text = render_brief_markdown(facts, "今天状态不错。", ["先刷 1-7", "再补材料"], "ready")

    assert text.startswith("# 每日简报 · 11 月 2 日（周一）\n\n今天状态不错。")
    assert "## 今天的定时任务\n- 12:00 日常（刷理智）；20:00 基建换班" in text
    assert "## 当期活动与关卡\n- 活动：`局部坏死` 至 11-14" in text
    assert "## 卡池\n- 限定寻访" in text
    assert "## 养成与目标\n- 养成计划「精二 xx」：还差 12 种材料，共 340 个，预计 6 天" in text
    assert "- 目标「打通主线」：里程碑 2/5，截止 11 月 30 日" in text
    assert (
        "## 昨天\n- 流水线完成 3 条，失败 1 条：「日常」内核崩溃\n"
        "- 掉落：固源岩 ×42\n"
        "- 工单完成 2 张、失败 1 张：「清理智」\n"
        "- 模型花费 1.32 元"
    ) in text
    assert (
        "## 消耗（按任务参数上限计）\n"
        "- 今日：理智药 0/5，源石 0（不限），加急许可 1/3\n"
        "- 昨日：理智药 2，源石 0，加急许可 1"
    ) in text
    assert "## 待处理\n- 2 条确认、1 个提问；工单「批准计划」在等你决定；1 条严重告警" in text
    assert "## 今日建议\n1. 先刷 1-7\n2. 再补材料" in text
    assert "!" not in text and "！" not in text

    # pending / facts_only 两种固定文案。
    pending = render_brief_markdown(_facts(), None, [], "pending")
    assert "## 今日建议\n" + SUGGESTION_PENDING in pending
    facts_only = render_brief_markdown(_facts(), None, [], "facts_only")
    assert "## 今日建议\n" + SUGGESTION_FACTS_ONLY in facts_only


def test_ticket_brief_is_verbatim_and_fits_4000_chars() -> None:
    artifact_id = "artifact-1"
    short = render_ticket_brief("事实正文", artifact_id, DAY)
    assert short == (
        "【每日简报】2026-11-02\n"
        "根据下面整理好的数据写今日建议。数值以这份数据为准；不提交流水线，不操作设备。\n"
        "\n"
        "事实正文\n"
        "\n"
        "要做的事：\n"
        "1. 需要时用 get_drop_rates、get_stage、get_item、list_upgrade_plans、"
        "read_knowledge 补充查询；查不到的就不写。\n"
        "2. 写 1 到 5 条今日建议，按优先级排，每条不超过 80 字，"
        "说清做什么、为什么；引用数值时写出处。\n"
        "3. 用 report 收尾：status 为 ready；summary 用三到五句概括今天，"
        "不超过 600 字；details 写 "
        '{"brief_artifact_id": "artifact-1", "suggestions": ["…"]}。\n'
        "4. 数据缺失的部分在 summary 里如实说明，不要补。"
    )

    huge_facts = "# 每日简报\n" + "数" * 5000
    long = render_ticket_brief(huge_facts, artifact_id, DAY)
    assert len(long) <= 4000
    assert long.startswith("【每日简报】2026-11-02\n")
    assert long.endswith("4. 数据缺失的部分在 summary 里如实说明，不要补。")
    # 从尾部截断事实：开头的标题保留，结尾的「要做的事」原样保留。
    assert "# 每日简报\n" in long
    assert "4. 数据缺失的部分" in long
    assert "!" not in long and "！" not in long
