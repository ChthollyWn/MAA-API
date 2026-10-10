"""抓取 M25 的 PRTS 夹具（真实响应的裁剪快照，规格 §4.9）。

夹具是**提交进仓库的快照**，单测一律读夹具、不打网；这个脚本只是把快照重新抓一遍的
入口。PRTS 是外部不可信数据，结构可能变，重抓后测试会先红。

用法::

    .venv/bin/python scripts/fetch_prts_fixtures.py [--out tests/fixtures/prts]

抓完记得更新 ``tests/fixtures/prts/README.md`` 的日期。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import httpx

USER_AGENT = "MAA-API/0.1 (+https://github.com/ChthollyWn/MAA-API)"
API = "https://prts.wiki/api.php"

#: 活动列表：只留前 4 条，够覆盖「按时间筛选 + 倒序 + 在开判定」。
ACTIVITY_KEEP = 4


def _get(params: list[tuple[str, str]], *, timeout: float = 45.0) -> bytes:
    # 走 httpx 而不是 urllib：本机 venv 的 urllib 用系统证书链，对 prts.wiki 报
    # CERTIFICATE_VERIFY_FAILED；httpx 用 certifi，与应用出站用的是同一套。
    response = httpx.get(
        API, params=params, headers={"User-Agent": USER_AGENT}, timeout=timeout
    )
    response.raise_for_status()
    return response.content


def _json(params: list[tuple[str, str]]) -> dict:
    return json.loads(_get(params).decode("utf-8"))


def _write_json(out: Path, name: str, payload: dict) -> None:
    (out / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


def _base(action: str) -> list[tuple[str, str]]:
    return [("action", action), ("format", "json"), ("formatversion", "2")]


def _trim_widget_style(text: str) -> str:
    """活动页里的 ``{{#widget:style|style=…}}`` 是一大段 CSS，夹具里换成占位。

    这段 CSS 里含 ``}`` 与换行，所以匹配到「行首的 ``}}``」为止。
    """
    return re.sub(
        r"\{\{#widget:style\|.*?\n\}\}",
        "{{#widget:style|style=/* trimmed */}}",
        text,
        flags=re.DOTALL,
    )


def capture(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)

    # 1. 在开活动列表（SMW）
    activities = _json(
        _base("ask")
        + [
            (
                "query",
                "[[分类:有活动信息的页面]][[!分类:集成战略]]"
                "|?名称缩短|?活动开始时间|?活动结束时间|sort=活动开始时间|order=desc",
            )
        ]
    )
    results = activities.get("query", {}).get("results", {})
    activities["query"]["results"] = dict(list(results.items())[:ACTIVITY_KEEP])
    _write_json(out, "ask_activities.json", activities)

    # 2. 关卡 id 反查（两个条件，实测 OR 上限是 8）
    _write_json(
        out,
        "ask_stage_by_id.json",
        _json(
            _base("ask")
            + [
                (
                    "query",
                    "[[关卡id::act3break_01]] OR [[关卡id::act3break_08]]"
                    "|?关卡id|?关卡名称|?敌人数量|limit=20",
                )
            ]
        ),
    )

    # 3. 单关的 SMW 属性
    _write_json(
        out,
        "ask_stage_info.json",
        _json(
            _base("ask")
            + [
                (
                    "query",
                    "[[7-18 爱国者之死]]|?关卡id|?关卡名称|?推荐等级|?部署上限|?初始COST"
                    "|?目标点耐久|?敌人数量|?地图大小|?作战消耗",
                )
            ]
        ),
    )

    # 4. OR 条件超限时的真实错误响应（用 >8 个条件触发）
    too_large = " OR ".join(f"[[关卡id::act3break_{i:02d}]]" for i in range(1, 13))
    _write_json(out, "ask_error_too_large.json", _json(_base("ask") + [("query", too_large)]))

    # 5. 活动页 wikitext（两个：带 stageId 提示的、只写代号的）
    for name, title in (("page_vec03.json", "矢量突破/03"), ("page_zuori.json", "昨日海")):
        payload = _json(
            _base("query")
            + [
                ("prop", "revisions"),
                ("rvprop", "content|ids|timestamp"),
                ("rvslots", "main"),
                ("redirects", "1"),
                ("titles", title),
            ]
        )
        for page in payload.get("query", {}).get("pages", []):
            for revision in page.get("revisions", []):
                content = revision["slots"]["main"]["content"]
                revision["slots"]["main"]["content"] = _trim_widget_style(content)
        _write_json(out, name, payload)

    # 6. 关卡页 wikitext：主线（重定向）与活动关各一个
    for name, title in (
        ("page_7-18.json", "7-18"),
        ("page_vec08.json", "VEC-08 磷脂层破坏"),
    ):
        _write_json(
            out,
            name,
            _json(
                _base("query")
                + [
                    ("prop", "revisions"),
                    ("rvprop", "content|ids|timestamp"),
                    ("rvslots", "main"),
                    ("redirects", "1"),
                    ("titles", title),
                ]
            ),
        )

    # 7. 代号重定向解析（只写代号的活动页要靠它拿页面标题）
    _write_json(
        out,
        "resolve_codes.json",
        _json(
            _base("query")
            + [
                ("titles", "YW-1|YW-2|YW-ST-1|YW-EX-1"),
                ("redirects", "1"),
                ("prop", "info"),
            ]
        ),
    )

    # 8. 搜索
    _write_json(
        out,
        "search_ajz.json",
        _json(
            _base("query")
            + [
                ("list", "search"),
                ("srsearch", "爱国者"),
                ("srnamespace", "0"),
                ("srlimit", "5"),
                ("srprop", "snippet|titlesnippet|redirecttitle"),
            ]
        ),
    )

    # 9. 分类查询（判断页面 kind）
    _write_json(
        out,
        "categories.json",
        _json(
            _base("query")
            + [
                ("prop", "categories"),
                ("clshow", "!hidden"),
                ("cllimit", "50"),
                ("titles", "7-18 爱国者之死|矢量突破/03|爱国者"),
            ]
        ),
    )

    # 10. 章节表
    _write_json(
        out,
        "sections.json",
        _json(_base("parse") + [("prop", "sections"), ("page", "矢量突破/03")]),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", default="tests/fixtures/prts", help="夹具输出目录（默认 tests/fixtures/prts）"
    )
    args = parser.parse_args()
    capture(Path(args.out))
    print(f"fixtures written to {args.out}")


if __name__ == "__main__":
    main()
