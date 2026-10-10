"""抓取 M26 的夹具（PRTS 干员/敌人/材料页 + 企鹅物流接口），规格 §6.2 的 T1。

夹具是**提交进仓库的裁剪快照**，单测一律读夹具、不打网；这个脚本只是把快照重新抓一遍。

用法::

    .venv/bin/python scripts/fetch_m26_fixtures.py

抓完记得更新 `tests/fixtures/prts/README.md` 与 `tests/fixtures/penguin/README.md` 的日期。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx

USER_AGENT = "MAA-API/0.1.0 (+https://github.com/ChthollyWn/MAA-API)"
PRTS_API = "https://prts.wiki/api.php"
PENGUIN_HOSTS = ("penguin-stats.io", "penguin-stats.cn")

#: 干员页：银灰（有模组、三技能）、史尔特尔（专精与模组材料）、克洛丝（3★）。
OPERATOR_TITLES = {"page_silverash.json": "银灰", "page_surtr.json": "史尔特尔", "page_kroos.json": "克洛丝"}
#: 敌人页：源石虫（字段少、两级）、爱国者（技能与天赋多、两级）。
ENEMY_TITLES = {"page_originium_slug.json": "源石虫", "page_patriot.json": "爱国者"}
#: 材料页：固源岩组、聚酸酯组。
ITEM_TITLES = {"page_item_30013.json": "固源岩组", "page_item_30033.json": "聚酸酯组"}

#: 夹具里保留的掉落材料：固源岩组(30013)、聚源岩(30012)、聚酸酯组(30033)。
MATRIX_ITEMS = {"30013", "30012", "30033"}
#: 夹具里保留的关卡：常驻主线/物资关 + `_perm` / `_rep` 变体 + 一条已关闭的活动关。
MATRIX_STAGES = {
    "main_08-13",
    "main_15-08",
    "tough_12-15",
    "a001_01_perm",
    "a001_01_rep",
    "act21side_08_rep",
    # 覆盖 `_perm` / `_rep` 后缀对回基础 id 的规则与小样本行（实测 `times=0` 的行当前不存在）。
    "act12side_04_perm",
    "act11d0_04_rep",
    "act13d0_05",
}


def _get(url: str, params: dict[str, Any] | None = None, *, timeout: float = 60.0) -> Any:
    """GET 一次并返回 JSON；走 httpx（venv 的 urllib 对这两个站点的证书链不可用）。"""
    response = httpx.get(
        url, params=params, headers={"User-Agent": USER_AGENT}, timeout=timeout
    )
    response.raise_for_status()
    return response.json()


def _prts_wikitext(title: str) -> dict[str, Any]:
    return _get(
        PRTS_API,
        {
            "action": "query",
            "prop": "revisions",
            "rvprop": "content|ids|timestamp",
            "rvslots": "main",
            "redirects": "1",
            "titles": title,
            "format": "json",
            "formatversion": "2",
        },
    )


def _write(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


def capture_prts(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, title in {**OPERATOR_TITLES, **ENEMY_TITLES, **ITEM_TITLES}.items():
        _write(out / name, _prts_wikitext(title))


def capture_penguin(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    base = f"https://{PENGUIN_HOSTS[0]}/PenguinStats/api/v2"
    matrix = _get(f"{base}/result/matrix", {"server": "CN"})["matrix"]
    closed = _get(
        f"{base}/result/matrix", {"server": "CN", "show_closed_zones": "true"}
    )["matrix"]
    stages = _get(f"{base}/stages", {"server": "CN"})
    items = _get(f"{base}/items")
    period = _get(f"{base}/period")

    kept = _trim_matrix(matrix)
    kept_closed = _trim_matrix(closed, closed_only=True)
    _write(out / "matrix_subset.json", {"matrix": kept})
    _write(out / "matrix_closed_subset.json", {"matrix": kept_closed})
    # 只留被裁剪后的 matrix 真正引用到的关卡，控制夹具体积。
    wanted = {row["stageId"] for row in kept} | {row["stageId"] for row in kept_closed}
    wanted |= MATRIX_STAGES
    _write(
        out / "stages_subset.json",
        [stage for stage in stages if stage.get("stageId") in wanted],
    )
    _write(
        out / "items_subset.json",
        [
            item
            for item in items
            if item.get("itemId") in MATRIX_ITEMS
            or item.get("itemId") in {"2001", "2002", "30011", "30012"}
        ],
    )
    _write(out / "period_subset.json", period[:3])


def _trim_matrix(rows: list[dict[str, Any]], *, closed_only: bool = False) -> list[dict[str, Any]]:
    """裁到目标材料与关卡；每类保底留一条 `times<100`、一条 `times=0` 的行。"""
    kept = [
        row
        for row in rows
        if row.get("itemId") in MATRIX_ITEMS
        and (row.get("stageId") in MATRIX_STAGES or not closed_only)
    ]
    if closed_only:
        kept = [row for row in kept if row.get("stageId") in MATRIX_STAGES]
    else:
        # 常规变体只留样本足够的行 + 每个材料各一条小样本行，控制夹具体积。
        kept = _limit_rows(kept)
    return kept[:60]


def _limit_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """每个材料留样本最多的 40 行，另加第一条 `times<100` 的行。"""
    by_item: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_item.setdefault(str(row.get("itemId")), []).append(row)
    kept: list[dict[str, Any]] = []
    for item_rows in by_item.values():
        item_rows.sort(key=lambda row: -int(row.get("times") or 0))
        kept.extend(item_rows[:40])
        small = next((row for row in item_rows if int(row.get("times") or 0) < 100), None)
        if small is not None and small not in kept:
            kept.append(small)
    return kept


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prts-out", default="tests/fixtures/prts")
    parser.add_argument("--penguin-out", default="tests/fixtures/penguin")
    parser.add_argument("--only", choices=["prts", "penguin"], default=None)
    args = parser.parse_args()
    if args.only in (None, "prts"):
        capture_prts(Path(args.prts_out))
        print(f"PRTS fixtures written to {args.prts_out}")
    if args.only in (None, "penguin"):
        capture_penguin(Path(args.penguin_out))
        print(f"Penguin fixtures written to {args.penguin_out}")


if __name__ == "__main__":
    main()
