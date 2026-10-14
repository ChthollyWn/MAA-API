"""规格 §10.3 T4 的「页面指引可达性」夹具测试（§10.1 改写口径的守护）。

§10.1 要求把删掉的数值换成「工具调用或 PRTS 页面指引」，本文件只做**可达性**断言，
不打网、不读网络缓存：

1. 从 34 个内置条目正文里解析出所有 `get_prts_page(title="…")`（或裸 `title="…"`）
   形式指向的 PRTS 页面标题，断言每个标题都在 `tests/fixtures/prts/page_titles.json`
   白名单里 —— 该白名单是 T1 复核报告用 `action=query&redirects=1` 实测「页面存在」
   的结论（抓取日期 2026-10-10，见 tests/fixtures/prts/README.md）。
2. 从同一批正文里解析出所有工具名（判据：小写标识符紧跟左括号），断言每个都在
   `build_registry()` 里真实注册。
3. 两个解析结果都必须非空，且把解析到的数量写进断言消息，避免「解析器写坏 →
   集合为空 → 断言永真」的假绿。

本仓没有 pytest-asyncio（见 tests/agent/test_knowledge_service.py 的文件头约定），
本文件全部是同步用例，不需要异步。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from maa_api.agent.knowledge import load_builtins
from maa_api.agent.tools import build_registry

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "prts" / "page_titles.json"

# `get_prts_page(title="理智")` 与裸 `title="理智"` 两种写法都要认：
# T1-b 复核时实测正文里 77 处 get_prts_page 全部带 title，但不排除以后有人照
# 工具 schema 写成别的调用形式，所以这里放宽到「任意 title 关键字实参」。
PAGE_TITLE = re.compile(r"""title\s*=\s*["']([^"']+)["']""")

# 工具名判据：小写标识符紧跟左括号。字段名（start_game_enabled、plan_index、
# series、medicine…）后面不跟括号，因此不会误报；T1 实测 0 误报。
TOOL_CALL = re.compile(r"\b([a-z][a-z0-9_]*)\s*\(")


def _bodies() -> dict[str, str]:
    entries = load_builtins()
    assert len(entries) == 34, f"内置条目数应为 34，实际 {len(entries)}"
    return {entry_id: entry.body for entry_id, entry in entries.items()}


def _page_titles() -> dict[str, str]:
    """页面标题 -> 命中它的条目 id（多个条目用 / 连接，便于失败时定位）。"""
    found: dict[str, list[str]] = {}
    for entry_id, body in sorted(_bodies().items()):
        for title in PAGE_TITLE.findall(body):
            found.setdefault(title, []).append(entry_id)
    return {title: "/".join(ids) for title, ids in sorted(found.items())}


def _tool_names() -> dict[str, str]:
    found: dict[str, list[str]] = {}
    for entry_id, body in sorted(_bodies().items()):
        for name in TOOL_CALL.findall(body):
            found.setdefault(name, []).append(entry_id)
    return {name: "/".join(ids) for name, ids in sorted(found.items())}


def test_page_title_pointers_are_non_empty_and_all_whitelisted():
    """T4：正文里的 PRTS 页面指引必须指向 T1 实测存在的页面。"""
    titles = _page_titles()
    assert titles, "没有从内置条目正文里解析到任何 title=\"…\"，解析器可能已失效"

    allowed = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    whitelist = allowed["titles"]
    unknown = {t: where for t, where in titles.items() if t not in whitelist}
    assert not unknown, (
        f"解析到 {len(titles)} 个 PRTS 页面标题，其中 {len(unknown)} 个不在夹具白名单 "
        f"{FIXTURE_PATH.relative_to(REPO_ROOT)}（来源见 tests/fixtures/prts/README.md）里："
        f"{unknown}；若确认页面存在，请把标题补进夹具。"
    )


def test_page_title_pointers_cover_the_reviewed_frontier():
    """夹具白名单不能比正文指引大得离谱：至少有一半实测标题仍被引用。

    这条同时是「白名单不是随手抄的」的反向约束：白名单应当来自 T1 实测，而不是
    为了过测试把正文里的标题全塞进去。正文改动删掉指引时，这里会提醒同步夹具。
    """
    titles = _page_titles()
    allowed = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["titles"]
    reused = set(titles) & set(allowed)
    assert len(reused) >= len(allowed) // 2, (
        f"夹具里 {len(allowed)} 个实测标题只有 {len(reused)} 个仍被正文引用："
        f"{sorted(set(allowed) - reused)}；正文或夹具需要同步。"
    )


def test_tool_name_pointers_exist_in_the_registry():
    """T4：正文里的工具指引必须指向真实注册的工具。"""
    names = _tool_names()
    assert names, "没有从内置条目正文里解析到任何工具调用，解析器可能已失效"

    registry = build_registry()
    available = {tool.name for tool in registry.list()}
    assert len(available) >= 68, f"工具箱应至少有 68 个工具，实际 {len(available)}"

    unknown = {n: where for n, where in names.items() if n not in available}
    assert not unknown, (
        f"解析到 {len(names)} 个工具名，其中 {len(unknown)} 个不在 build_registry() "
        f"（共 {len(available)} 个工具）里：{unknown}"
    )


def test_pointer_counts_are_reported_for_future_readers():
    """把解析规模写进断言消息：规模骤降说明 T2 改写把指引改没了。"""
    titles = _page_titles()
    names = _tool_names()
    # T1 复核实测：正文里 get_prts_page 77 处、唯一标题 27 个、工具名 32 个。
    # 这里留出余量，只守住「指引没被整体删掉」。
    assert len(titles) >= 10, f"只剩 {len(titles)} 个页面标题指引：{sorted(titles)}"
    assert len(names) >= 10, f"只剩 {len(names)} 个工具指引：{sorted(names)}"
