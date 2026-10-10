"""抓取 M29 的拆包关卡夹具（规格 §9.3、§9.2 T1 第①条）。

夹具是**提交进仓库的裁剪快照**，单测一律读夹具、不打网；这个脚本只是把快照重新抓一遍，
并顺便复核 T1 探针的坐标换算结论（作业的部署点是否都落在可部署格子上）。

用法::

    .venv/bin/python scripts/fetch_m29_gamedata_fixtures.py

抓完记得更新 `tests/fixtures/gamedata/README.md` 的日期。

实测（2026-10-10，本机直连）：``fastly.jsdelivr.net`` 通（1-7 的关卡文件 87 KB）；
``cdn.jsdelivr.net`` 是连接超时（42 s 未握手成功）——两个镜像内容一致，客户端按顺序试，
这里只用 ``fastly``；GitHub API 的兜底路径未在抓夹具时用到。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx

USER_AGENT = "MAA-API/0.1.0 (+https://github.com/ChthollyWn/MAA-API)"
GAMEDATA_MIRROR = "https://fastly.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master"
COPILOT_SITE = "https://prts.maa.plus"

#: 关卡文件：1-7（`obt/main/level_main_01-07`）。
LEVEL_IDS = ("obt/main/level_main_01-07",)
#: 常量表：升级经验/龙门币（`gamedata_const.json`）与作战记录折算（`item_table.json` 的 expItems）。
EXCEL_FILES = ("gamedata_const.json", "item_table.json")
#: 常量表里保留的键（其余裁掉：这两张表里我们只用这几个字段）。
CONST_KEYS = ("characterExpMap", "characterUpgradeCostMap", "evolveGoldCost", "maxLevel")
#: item_table 里保留的作战记录 itemId。
CARD_IDS = ("2001", "2002", "2003", "2004")
ITEM_KEYS = ("itemId", "name", "rarity", "itemType", "classifyType", "sortId", "buildingProductList")
#: 作业：1-7 的一份可用作业（稀音单人 61s 速刷）。
COPILOT_ID = 22311
#: 作业响应里保留的字段（`content` 原样保留：它是坐标探针的输入）。
COPILOT_KEYS = (
    "id",
    "type",
    "upload_time",
    "uploader_id",
    "uploader",
    "views",
    "hot_score",
    "available",
    "rating_level",
    "rating_ratio",
    "comments_count",
    "content",
)


def fetch_json(client: httpx.Client, url: str, **params: str) -> Any:
    response = client.get(url, params=params or None)
    response.raise_for_status()
    return response.json()


def take_level(client: httpx.Client, level_id: str) -> dict[str, Any]:
    return fetch_json(client, f"{GAMEDATA_MIRROR}/zh_CN/gamedata/levels/{level_id}.json")


def take_const(client: httpx.Client) -> dict[str, Any]:
    payload = fetch_json(client, f"{GAMEDATA_MIRROR}/zh_CN/gamedata/excel/gamedata_const.json")
    return {key: payload[key] for key in CONST_KEYS if key in payload}


def take_item_table(client: httpx.Client) -> dict[str, Any]:
    payload = fetch_json(client, f"{GAMEDATA_MIRROR}/zh_CN/gamedata/excel/item_table.json")
    items = payload.get("items") or {}
    return {
        "items": {
            item_id: {key: items[item_id][key] for key in ITEM_KEYS if key in items[item_id]}
            for item_id in CARD_IDS
            if item_id in items
        },
        "expItems": payload.get("expItems") or {},
    }


def take_copilot(client: httpx.Client, site_id: int) -> dict[str, Any]:
    payload = fetch_json(client, f"{COPILOT_SITE}/copilot/get/{site_id}")
    data = payload.get("data") or {}
    return {
        "status_code": payload.get("status_code"),
        "data": {key: data[key] for key in COPILOT_KEYS if key in data},
    }


def probe_coordinates(level: dict[str, Any], copilot: dict[str, Any]) -> list[str]:
    """复核 T1 结论：``location = [col, row]``、行自上而下、``map`` 行序与之一致。"""
    rows = (level.get("mapData") or {}).get("map") or []
    tiles = (level.get("mapData") or {}).get("tiles") or []
    job = json.loads((copilot.get("data") or {}).get("content") or "{}")
    lines: list[str] = []
    for action in job.get("actions") or []:
        if action.get("type") != "Deploy":
            continue
        col, row = action["location"]
        tile = tiles[rows[row][col]]
        lines.append(
            f"  {action.get('name')} location={action['location']} "
            f"tile={tile['tileKey']} buildable={tile['buildableType']} height={tile['heightType']}"
        )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="抓取 M29 的拆包关卡夹具")
    parser.add_argument("--out", type=Path, default=Path("tests/fixtures/gamedata"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, follow_redirects=False, timeout=60.0
    ) as client:
        for level_id in LEVEL_IDS:
            payload = take_level(client, level_id)
            name = level_id.rsplit("/", 1)[-1] + ".json"
            (args.out / name).write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            print(f"[level] {name} {len(json.dumps(payload))} chars")
        const = take_const(client)
        (args.out / "gamedata_const.json").write_text(
            json.dumps(const, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"[excel] gamedata_const.json keys={sorted(const)}")
        items = take_item_table(client)
        (args.out / "item_table.json").write_text(
            json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"[excel] item_table.json expItems={items['expItems']}")
        copilot = take_copilot(client, COPILOT_ID)
        (args.out / f"copilot_{COPILOT_ID}.json").write_text(
            json.dumps(copilot, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"[copilot] {COPILOT_ID} keys={sorted(copilot['data'])}")

    level = json.loads((args.out / "level_main_01-07.json").read_text(encoding="utf-8"))
    job = json.loads((args.out / f"copilot_{COPILOT_ID}.json").read_text(encoding="utf-8"))
    print("坐标探针（应当全部 buildable != NONE）：")
    for line in probe_coordinates(level, job):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
