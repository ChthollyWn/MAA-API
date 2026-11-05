"""每日简报的 Markdown 渲染（M84 规格 §4.5.11）。

三份输出：

- :func:`render_facts_markdown`：事实部分，给 analyst 读，也是简报正文的数据部分。
- :func:`render_brief_markdown`：产物全文，在事实之后按状态写「今日建议」。
- :func:`render_ticket_brief`：建 analyst 工单时用的委托说明，全文 ≤4000 字，
  事实超长时从尾部截断。

小节的省略规则：数据为空的小节整节省略，「理智」与「今日建议」永远保留。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from maa_api.agent.brief.facts import BriefFacts

__all__ = [
    "BRIEF_MAX_CHARS",
    "NO_SANITY_TEXT",
    "SUGGESTION_FACTS_ONLY",
    "SUGGESTION_PENDING",
    "render_brief_markdown",
    "render_facts_markdown",
    "render_ticket_brief",
]

#: analyst 工单说明的长度上限（规格 §4.5.11）。
BRIEF_MAX_CHARS = 4000
NO_SANITY_TEXT = "没有理智记录（跑过一次刷理智后才有）"
SUGGESTION_PENDING = "生成中"
SUGGESTION_FACTS_ONLY = "这次没有生成建议"

_SPEND_ORDER: tuple[tuple[str, str], ...] = (
    ("medicine", "理智药"),
    ("stone", "源石"),
    ("expedite", "加急许可"),
)
_CURRENCY_UNITS: dict[str, str] = {"CNY": "元"}


def _month_day(value: date) -> str:
    return f"{value.month} 月 {value.day} 日"


def _title(facts: BriefFacts) -> str:
    return f"# 每日简报 · {_month_day(facts.date)}（{facts.weekday}）"


def _sanity_section(facts: BriefFacts) -> str:
    sanity = facts.sanity
    if not sanity:
        return f"## 理智\n{NO_SANITY_TEXT}"
    estimated = sanity.get("estimated")
    maximum = sanity.get("max")
    full_at = sanity.get("full_at_local") or "时间未知"
    return (
        "## 理智\n"
        f"- 估算当前 {estimated}/{maximum}，约 {full_at} 回满"
        "（按每 6 分钟恢复 1 点估算，没算吃药与碎石）"
    )


def _schedules_section(facts: BriefFacts) -> str | None:
    items = facts.schedules_today or []
    if not items:
        return None
    parts: list[str] = []
    for item in items:
        name = str(item.get("name") or "").strip()
        at = str(item.get("at_local") or "").strip()
        text = f"{at} {name}".strip()
        if item.get("fight"):
            text += "（刷理智）"
        if text:
            parts.append(text)
    if not parts:
        return None
    return "## 今天的定时任务\n- " + "；".join(parts)


def _activity_section(facts: BriefFacts) -> str | None:
    lines = [line for line in (facts.activity_lines or []) if line]
    if not lines:
        return None
    return "## 当期活动与关卡\n" + "\n".join(lines)


def _gacha_section(facts: BriefFacts) -> str | None:
    lines = [line for line in (facts.gacha_lines or []) if line]
    if not lines:
        return None
    return "## 卡池\n" + "\n".join(lines)


def _plan_line(plan: dict[str, Any]) -> str:
    name = str(plan.get("name") or "").strip()
    kinds = int(plan.get("missing_kinds") or 0)
    total = int(plan.get("missing_total") or 0)
    line = f"- 养成计划「{name}」"
    if total > 0:
        line += f"：还差 {kinds} 种材料，共 {total} 个"
    else:
        line += "：材料已齐"
    days = plan.get("days_estimated")
    if isinstance(days, (int, float)) and days > 0:
        text = f"{days:.1f}".rstrip("0").rstrip(".")
        line += f"，预计 {text} 天"
    return line


def _goal_line(goal: dict[str, Any]) -> str:
    title = str(goal.get("title") or "").strip()
    total = int(goal.get("total") or 0)
    done = int(goal.get("done") or 0)
    line = f"- 目标「{title}」"
    if total > 0:
        line += f"：里程碑 {done}/{total}"
    deadline = goal.get("deadline_local")
    if deadline:
        line += f"，截止 {deadline}"
    return line


def _plans_goals_section(facts: BriefFacts) -> str | None:
    lines = [_plan_line(plan) for plan in (facts.plans or [])]
    lines.extend(_goal_line(goal) for goal in (facts.goals or []))
    if not lines:
        return None
    return "## 养成与目标\n" + "\n".join(lines)


def _cost_text(costs: dict[str, int]) -> str:
    parts: list[str] = []
    for currency, micros in costs.items():
        unit = _CURRENCY_UNITS.get(currency, currency)
        parts.append(f"{int(micros) / 1_000_000:.2f} {unit}")
    return "、".join(parts)


def _pipeline_line(pipelines: dict[str, Any]) -> str | None:
    parts: list[str] = []
    for key, label in (
        ("completed", "完成"),
        ("failed", "失败"),
        ("cancelled", "取消"),
    ):
        count = int(pipelines.get(key) or 0)
        if count > 0:
            parts.append(f"{label} {count} 条")
    if not parts:
        return None
    line = "- 流水线" + "，".join(parts)
    failed_items = pipelines.get("failed_items") or []
    details: list[str] = []
    for item in failed_items[:3]:
        title = str(item.get("title") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if title and reason:
            details.append(f"「{title}」{reason}")
        elif title:
            details.append(f"「{title}」")
        elif reason:
            details.append(reason)
    if details:
        line += "：" + "；".join(details)
    return line


def _drops_line(drops: list[dict[str, Any]]) -> str | None:
    parts = [
        f"{item.get('item_name')} ×{int(item.get('quantity') or 0)}"
        for item in drops
        if item.get("item_name")
    ]
    if not parts:
        return None
    return "- 掉落：" + "、".join(parts)


def _ticket_line(tickets: dict[str, Any]) -> str | None:
    parts: list[str] = []
    for key, label in (
        ("done", "完成"),
        ("failed", "失败"),
        ("cancelled", "取消"),
    ):
        count = int(tickets.get(key) or 0)
        if count > 0:
            parts.append(f"{label} {count} 张")
    titles = [
        str(title) for title in (tickets.get("titles") or []) if title
    ]
    if not parts and not titles:
        return None
    line = "- 工单" + "、".join(parts)
    if titles:
        line += "：" + "".join(f"「{title}」" for title in titles[:3])
    return line


def _yesterday_section(facts: BriefFacts) -> str | None:
    lines: list[str] = []
    pipeline = _pipeline_line(facts.pipelines_yesterday or {})
    if pipeline:
        lines.append(pipeline)
    drops = _drops_line(facts.drops_yesterday or [])
    if drops:
        lines.append(drops)
    ticket = _ticket_line(facts.tickets_yesterday or {})
    if ticket:
        lines.append(ticket)
    if facts.cost_yesterday:
        costs = _cost_text(facts.cost_yesterday)
        if costs:
            lines.append(f"- 模型花费 {costs}")
    if not lines:
        return None
    return "## 昨天\n" + "\n".join(lines)


def _today_spend_text(item: dict[str, Any]) -> str:
    kind = str(item.get("kind") or "")
    label = str(item.get("label") or "")
    for key, name in _SPEND_ORDER:
        if key == kind:
            label = label or name
            break
    label = label or kind
    used = int(item.get("used") or 0)
    limit = item.get("limit")
    limit = -1 if limit is None else int(limit)
    if limit == -1:
        return f"{label} {used}（不限）"
    return f"{label} {used}/{limit}"


def _spend_section(facts: BriefFacts) -> str | None:
    today = facts.spend_today or []
    if not today:
        return None
    lines = [
        "## 消耗（按任务参数上限计）",
        "- 今日：" + "，".join(_today_spend_text(item) for item in today),
    ]
    yesterday = facts.spend_yesterday or {}
    if yesterday:
        lines.append(
            "- 昨日："
            + "，".join(
                f"{name} {int(yesterday.get(key) or 0)}"
                for key, name in _SPEND_ORDER
            )
        )
    return "\n".join(lines)


def _pending_section(facts: BriefFacts) -> str | None:
    pending = facts.pending or {}
    count_parts: list[str] = []
    confirmations = int(pending.get("confirmations") or 0)
    questions = int(pending.get("questions") or 0)
    if confirmations > 0:
        count_parts.append(f"{confirmations} 条确认")
    if questions > 0:
        count_parts.append(f"{questions} 个提问")
    groups: list[str] = []
    if count_parts:
        groups.append("、".join(count_parts))

    tickets_total = int(pending.get("tickets_total") or 0)
    titles = [str(title) for title in (pending.get("tickets") or []) if title]
    if tickets_total > 0:
        if titles:
            text = "工单" + "".join(f"「{title}」" for title in titles[:3])
            if tickets_total > len(titles[:3]):
                text += f"等 {tickets_total} 张在等你决定"
            else:
                text += "在等你决定"
        else:
            text = f"{tickets_total} 张工单在等你决定"
        groups.append(text)

    for key, unit in (("alerts", "条严重告警"), ("memories", "条待审记忆")):
        count = int(pending.get(key) or 0)
        if count > 0:
            groups.append(f"{count} {unit}")
    observations = int(pending.get("observations") or 0)
    if observations > 0:
        groups.append(f"{observations} 条观察待审阅")
    if not groups:
        return None
    return "## 待处理\n- " + "；".join(groups)


#: 数据小节按模板顺序排列；返回 ``None`` 的表示该节为空、整节省略。
_SECTION_BUILDERS = (
    _schedules_section,
    _activity_section,
    _gacha_section,
    _plans_goals_section,
    _yesterday_section,
    _spend_section,
    _pending_section,
)


def _sections(facts: BriefFacts) -> list[str]:
    out = [_sanity_section(facts)]
    for builder in _SECTION_BUILDERS:
        section = builder(facts)
        if section:
            out.append(section)
    return out


def _advice_section(status: str, suggestions: list[str]) -> str:
    lines = ["## 今日建议"]
    if status == "pending":
        lines.append(SUGGESTION_PENDING)
    elif status == "facts_only":
        lines.append(SUGGESTION_FACTS_ONLY)
    else:
        items = [
            str(item).strip()
            for item in (suggestions or [])
            if str(item).strip()
        ][:5]
        if items:
            lines.extend(f"{index}. {item}" for index, item in enumerate(items, 1))
        else:
            lines.append(SUGGESTION_FACTS_ONLY)
    return "\n".join(lines)


def render_facts_markdown(facts: BriefFacts) -> str:
    """事实部分的 Markdown；不含「今日建议」（工单说明会另给写作要求）。"""
    return "\n\n".join([_title(facts), *_sections(facts)])


def render_brief_markdown(
    facts: BriefFacts,
    summary: str | None,
    suggestions: list[str],
    status: str,
) -> str:
    """产物全文：标题、可选的 summary、事实小节、按状态渲染的「今日建议」。"""
    parts = [_title(facts)]
    text = (summary or "").strip()
    if text:
        parts.append(text)
    parts.extend(_sections(facts))
    parts.append(_advice_section(status, suggestions))
    return "\n\n".join(parts)


def render_ticket_brief(facts_md: str, artifact_id: str, day: date) -> str:
    """analyst 工单的委托说明；总长 ≤4000 字，超长时截事实的尾部。"""
    head = (
        f"【每日简报】{day.isoformat()}\n"
        "根据下面整理好的数据写今日建议。数值以这份数据为准；"
        "不提交流水线，不操作设备。\n\n"
    )
    tail = (
        "\n\n要做的事：\n"
        "1. 需要时用 get_drop_rates、get_stage、get_item、"
        "list_upgrade_plans、read_knowledge 补充查询；查不到的就不写。\n"
        "2. 写 1 到 5 条今日建议，按优先级排，每条不超过 80 字，"
        "说清做什么、为什么；引用数值时写出处。\n"
        '3. 用 report 收尾：status 为 ready；summary 用三到五句概括今天，'
        "不超过 600 字；details 写 "
        f'{{"brief_artifact_id": "{artifact_id}", "suggestions": ["…"]}}。\n'
        "4. 数据缺失的部分在 summary 里如实说明，不要补。"
    )
    budget = BRIEF_MAX_CHARS - len(head) - len(tail)
    body = str(facts_md)
    if budget <= 0:
        body = ""
    elif len(body) > budget:
        body = body[:budget]
    return head + body + tail
