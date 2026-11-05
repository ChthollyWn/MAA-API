"""每日简报的事实与渲染（M84 规格 §4.5.10、§4.5.11）。

``BriefService``（G5）只从这里取 ``collect_facts`` 与三个渲染函数；对外契约
（``BriefFacts`` 的字段、模板与 4000 字上限）由本包的测试钉住。
"""

from __future__ import annotations

from maa_api.agent.brief.facts import BriefFacts, collect_facts
from maa_api.agent.brief.render import (
    render_brief_markdown,
    render_facts_markdown,
    render_ticket_brief,
)

__all__ = [
    "BriefFacts",
    "collect_facts",
    "render_brief_markdown",
    "render_facts_markdown",
    "render_ticket_brief",
]
