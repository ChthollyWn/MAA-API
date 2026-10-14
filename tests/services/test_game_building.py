"""M28 基建技能与候选干员（规格 §8.3）：Cargo 表 + 干员页 `{{后勤技能}}` + 账号数据。

不打网：Cargo 与 PRTS 走 ``httpx.MockTransport`` 分发的内联夹具（真实响应裁剪，
抓取日期 2026-10-10；本文件按「写范围只有指定文件」的约束把夹具内联，未放
``tests/fixtures/prts/``）；本机资源层（``infrast.json`` / ``battle_data.json``）用
``tmp_path`` 现造；账号数据用假 ``OperatorBoxService``。

``{{后勤技能}}`` 与 Cargo 的对应关系已实测：2026-10-10 抽取 20 名干员（银灰、克洛丝、
森蚺、能天使、迷迭香、黑键、夕、令、絮雨、爱丽丝、车尔尼、帕拉斯、杜宾等），技能名
与 ``building_skill2`` 的 ``name`` 全量对齐（0 例外）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.http import ExternalClient
from maa_api.services.external.prts import PrtsClient
from maa_api.services.game.building import BuildingService
from maa_api.services.game.operators import OperatorService
from maa_api.services.prts_parse.building import (
    BuildingSkill,
    clean_building_description,
    efficiency_hints,
    parse_building_skill_table,
    parse_operator_building_skills,
)
from maa_api.settings import Settings, SourcesSettings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "prts"

# --------------------------------------------------------------------------- #
# 夹具（真实数据裁剪；来源与抓取日期见模块 docstring）
# --------------------------------------------------------------------------- #
#: Cargo `building_skill2` 的裁剪快照（name / room / description 三列）。
CARGO_ROWS: list[dict[str, Any]] = [
    {
        "name": "喀兰贸易·α",
        "room": "贸易站",
        "description": '进驻贸易站时，订单获取效率<span style="color:#0098DC;">+15%</span>，且订单上限<span style="color:#0098DC;">+2</span>',
    },
    {
        "name": "喀兰之主",
        "room": "贸易站",
        "description": '进驻贸易站时，订单获取效率<span style="color:#0098DC;">+20%</span>，且订单上限<span style="color:#0098DC;">+4</span>',
    },
    {
        "name": "慢性子",
        "room": "制造站",
        "description": '进驻制造站后，生产力首小时<span style="color:#0098DC;">+15%</span>，此后每小时<span style="color:#0098DC;">+2%</span>，最终达到<span style="color:#0098DC;">+25%</span>',
    },
    {
        "name": "独处",
        "room": "宿舍",
        "description": '进驻宿舍时，自身心情每小时恢复<span style="color:#0098DC;">+0.7</span>',
    },
    {
        "name": "自动化·α",
        "room": "制造站",
        "description": '进驻制造站时，当前制造站内其他干员提供的生产力<span style="color:#FF6237;">全部归零</span>（不包含根据设施数量提供加成的生产力），每个<span style="color:#00B0FF;">发电站</span>为当前制造站<span style="color:#0098DC;">+5%</span>的生产力',
    },
    {
        "name": "自动化·β",
        "room": "制造站",
        "description": '进驻制造站时，当前制造站内其他干员提供的生产力<span style="color:#FF6237;">全部归零</span>（不包含根据设施数量提供加成的生产力），每个<span style="color:#00B0FF;">发电站</span>为当前制造站<span style="color:#0098DC;">+10%</span>的生产力',
    },
    {
        "name": "我寻思能行",
        "room": "控制中枢",
        "description": '进驻控制中枢时，如果<span style="color:#00B0FF;">Lancet-2</span>进驻在<span style="color:#00B0FF;">发电站</span>，<span style="color:#00B0FF;">发电站</span>额外<span style="color:#0098DC;">+2</span>（仅影响设施数量）',
    },
    # 带隐藏 tooltip 的描述：清洗后不能留下「术语: …」里的解释文字。
    {
        "name": "集群狩猎·α",
        "room": "控制中枢",
        "description": (
            '进驻控制中枢时，控制中枢内所有干员的心情每小时恢复<span style="color:#0098DC;">+0.05</span>；'
            '基建内（不包含副手及活动室使用者）<span class="mc-tooltips"><span class="term" '
            'style="text-decoration:underline ;white-space:nowrap;text-shadow: 0 0 1px;color:inherit;cursor:help;">'
            '<span style="color:#00B0FF;">深海猎人</span></span><span style="display:none" data-size="350" '
            'data-interactive="true" data-append-to="body"><strong>术语: <span style="color:#00B0FF;">深海猎人</span></strong>'
            "<br>包含以下干员<br>歌蕾蒂娅、斯卡蒂、幽灵鲨、安哲拉、乌尔比安</span></span>干员获得"
            '<span class="mc-tooltips"><span class="term" style="text-decoration:underline ;white-space:nowrap;'
            'text-shadow: 0 0 1px;color:inherit;cursor:help;"><span style="color:#F49800;">特殊加成</span></span>'
            '<span style="display:none" data-size="350" data-interactive="true" data-append-to="body">'
            "<strong>术语: <span style=\"color:#F49800;\">特殊加成</span></strong><br>每有1个深海猎人干员进驻制造站，"
            "则控制中枢给每个进驻深海猎人的制造站提供5%生产力，最多给单个制造站提供45%生产力</span></span>"
            "（与部分技能有特殊叠加规则）"
        ),
    },
    {
        "name": "拾荒者",
        "room": "制造站",
        "description": '进驻制造站时，仓库容量上限<span style="color:#0098DC;">+8</span>，心情每小时消耗<span style="color:#0098DC;">-0.25</span>',
    },
    {
        "name": "近卫专精·α",
        "room": "训练室",
        "description": '进驻训练室协助位时，<span style="color:#00B0FF;">近卫</span>干员的专精技能训练速度<span style="color:#0098DC;">+30%</span>',
    },
]

#: 森蚺的 `{{后勤技能}}` 块（同槽位的 α/β 两档 + 另一槽位；2026-10-10 实测原文裁剪）。
SERPENT_WIKITEXT = """{{CharinfoV2
|干员名=森蚺
|干员id=char_416_zumama
}}
==后勤技能==
{{后勤技能
|后勤技能1-1=自动化·α
|后勤技能1-1阶段=精英0
|后勤技能1-2=自动化·β
|后勤技能1-2阶段=精英2
|后勤技能2-1=我寻思能行
|后勤技能2-1阶段=精英2
}}
"""

PAGE_CONTENTS: dict[str, str] = {
    "银灰": json.loads((FIXTURES / "page_silverash.json").read_text(encoding="utf-8"))[
        "query"
    ]["pages"][0]["revisions"][0]["slots"]["main"]["content"],
    "克洛丝": json.loads((FIXTURES / "page_kroos.json").read_text(encoding="utf-8"))[
        "query"
    ]["pages"][0]["revisions"][0]["slots"]["main"]["content"],
    "森蚺": SERPENT_WIKITEXT,
}

#: 本机资源层里的 core 技能表（真实 `infrast.json` 裁剪）：owner 与效率口径。
CORE_INFRAST: dict[str, Any] = {
    "Mfg": {
        "maxNumOfOpers": 3,
        "products": ["CombatRecord", "PureGold", "OriginStone", "Chip"],
        "skills": {
            "bskill_man_spd_add2": {
                "name": ["慢性子", "延时摄影"],
                "operatorIds": ["char_124_kroos", "char_336_folivo"],
                "efficient": {"all": 23},
                "desc": ["进驻制造站后，生产力首小时+15%，此后每小时+2%，最终达到+25%"],
            },
            "bskill_man_spd&power1": {
                "name": ["自动化·α"],
                "operatorIds": ["char_416_zumama", "char_433_windft"],
                "efficient": {"all": -40},
                "desc": ["进驻制造站时，当前制造站内其他干员提供的生产力全部归零"],
            },
        },
    },
    "Trade": {
        "maxNumOfOpers": 3,
        "products": ["Money", "SyntheticJade"],
        "skills": {
            "bskill_tra_spd&limit5": {
                "name": ["喀兰之主"],
                "operatorIds": ["char_172_svrash"],
                "efficient": {"all": 20},
                "desc": ["进驻贸易站时，订单获取效率+20%，且订单上限+4"],
            }
        },
    },
    "Control": {"maxNumOfOpers": 5, "products": ["MoodAddition"], "skills": {}},
    "Dorm": {"skills": {}},
    "Power": {"maxNumOfOpers": 1, "products": ["Drone"], "skills": {}},
    "Reception": {"maxNumOfOpers": 2, "products": ["General"], "skills": {}},
    "Office": {"maxNumOfOpers": 1, "products": ["HR"], "skills": {}},
    "Processing": {"skills": {}},
    "Training": {"skills": {}},
    "Recycle": {"skills": {}},
    "roomType": [
        "Power",
        "Reception",
        "Control",
        "Dorm",
        "Trade",
        "Office",
        "Mfg",
        "Processing",
        "Training",
        "Recycle",
    ],
}

CORE_BATTLE_DATA: dict[str, Any] = {
    "chars": {
        "char_172_svrash": {"name": "银灰", "rarity": 5, "profession": "WARRIOR", "rangeId": "1-1"},
        "char_124_kroos": {"name": "克洛丝", "rarity": 2, "profession": "SNIPER", "rangeId": "0-1"},
        "char_416_zumama": {"name": "森蚺", "rarity": 5, "profession": "TANK", "rangeId": "0-1"},
        "char_336_folivo": {"name": "图耶", "rarity": 4, "profession": "MEDIC", "rangeId": "1-1"},
    },
    "ranges": {"1-1": [[1, 1]]},
}


def write_core_layer(tmp_path: Path) -> Path:
    """一个最小资源层：``infrast.json`` + ``battle_data.json``。"""
    root = tmp_path / "core-resource"
    root.mkdir(parents=True, exist_ok=True)
    (root / "infrast.json").write_text(
        json.dumps(CORE_INFRAST, ensure_ascii=False), encoding="utf-8"
    )
    (root / "battle_data.json").write_text(
        json.dumps(CORE_BATTLE_DATA, ensure_ascii=False), encoding="utf-8"
    )
    return root


# --------------------------------------------------------------------------- #
# 假客户端与假账号数据
# --------------------------------------------------------------------------- #
def _settings() -> Settings:
    return Settings(sources=SourcesSettings(use_proxy=False, prts_rate_per_minute=120))


class _PrtsRoutes:
    """按 action 分发的假 PRTS：记录请求，便于断言「先 ask 再回落干员页」。"""

    def __init__(self, *, ask_rows: dict[str, Any] | None = None) -> None:
        self.calls: list[httpx.Request] = []
        self.ask_rows = ask_rows

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        params = dict(request.url.params)
        action = params.get("action", "")
        if action == "ask":
            if self.ask_rows is None:
                payload: dict[str, Any] = {
                    "query": {"results": {}, "printrequests": []},
                    "limits": {},
                }
            else:
                payload = {"query": {"results": self.ask_rows, "printrequests": []}}
            return httpx.Response(200, content=json.dumps(payload, ensure_ascii=False).encode())
        if action == "cargoquery":
            where = params.get("where", "")
            rows = [
                {"title": row}
                for row in CARGO_ROWS
                if not where or f'room="{row["room"]}"' == where
            ]
            return httpx.Response(
                200, content=json.dumps({"cargoquery": rows}, ensure_ascii=False).encode()
            )
        if action == "query" and params.get("prop") == "revisions":
            titles = [item for item in params.get("titles", "").split("|") if item]
            pages = []
            for index, title in enumerate(titles):
                content = PAGE_CONTENTS.get(title)
                if content is None:
                    pages.append({"pageid": index + 1, "title": title, "missing": True})
                else:
                    pages.append(
                        {
                            "pageid": index + 1,
                            "title": title,
                            "revisions": [
                                {
                                    "revid": 100 + index,
                                    "timestamp": "2026-10-10T00:00:00Z",
                                    "slots": {"main": {"content": content}},
                                }
                            ],
                        }
                    )
            return httpx.Response(
                200,
                content=json.dumps(
                    {"batchcomplete": True, "query": {"pages": pages}}, ensure_ascii=False
                ).encode(),
            )
        return httpx.Response(404, content=b"{}")

    @property
    def cargo_calls(self) -> list[dict[str, str]]:
        return [
            dict(request.url.params)
            for request in self.calls
            if dict(request.url.params).get("action") == "cargoquery"
        ]

    @property
    def revision_titles(self) -> list[str]:
        titles: list[str] = []
        for request in self.calls:
            params = dict(request.url.params)
            if params.get("action") == "query" and params.get("prop") == "revisions":
                titles.extend(item for item in params.get("titles", "").split("|") if item)
        return titles


def make_prts(routes: _PrtsRoutes) -> PrtsClient:
    external = ExternalClient(
        transport=httpx.MockTransport(routes.handler), settings_provider=_settings
    )
    return PrtsClient(external)


class FakeBox:
    """账号干员数据的替身；``rows`` 是 ``box_row_wire`` 形状。"""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    async def list_operators(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        limit = int(kwargs.get("limit", 50))
        offset = int(kwargs.get("offset", 0))
        return {
            "items": self.rows[offset : offset + limit],
            "total": len(self.rows),
            "limit": limit,
            "offset": offset,
        }


def box_row(char_id: str, name: str, *, elite: int, rarity: int = 6, level: int = 1) -> dict[str, Any]:
    return {
        "char_id": char_id,
        "name": name,
        "rarity": rarity,
        "profession": None,
        "own": True,
        "elite": elite,
        "level": level,
    }


def service(
    tmp_path: Path,
    routes: _PrtsRoutes,
    *,
    box: FakeBox | None = None,
) -> BuildingService:
    layer = write_core_layer(tmp_path)
    return BuildingService(
        prts=make_prts(routes),
        box=box,
        operator_service=OperatorService(battle_data_path=layer / "battle_data.json"),
        core_resource_roots=[layer],
    )


# --------------------------------------------------------------------------- #
# 解析层（纯函数）
# --------------------------------------------------------------------------- #
def test_clean_description_drops_hidden_tooltips_and_tags() -> None:
    row = next(item for item in CARGO_ROWS if item["name"] == "集群狩猎·α")
    cleaned = clean_building_description(row["description"])
    assert cleaned == (
        "进驻控制中枢时，控制中枢内所有干员的心情每小时恢复+0.05；"
        "基建内（不包含副手及活动室使用者）深海猎人干员获得特殊加成（与部分技能有特殊叠加规则）"
    )
    assert "术语" not in cleaned
    assert "歌蕾蒂娅" not in cleaned
    assert "45%" not in cleaned


def test_efficiency_hints_read_numbers_from_the_description() -> None:
    assert efficiency_hints(
        "进驻贸易站时，订单获取效率+15%，且订单上限+2", room="贸易站"
    ) == ["贸易站订单获取效率 +15%", "贸易站订单上限 +2"]
    assert efficiency_hints(
        "进驻制造站后，生产力首小时+15%，此后每小时+2%，最终达到+25%", room="制造站"
    ) == [
        "制造站生产力（首小时） +15%",
        "制造站生产力（此后每小时） +2%",
        "制造站生产力（最终） +25%",
    ]
    assert efficiency_hints(
        "进驻制造站时，仓库容量上限+8，心情每小时消耗-0.25", room="制造站"
    ) == ["制造站仓库容量上限 +8", "制造站心情消耗 -0.25/小时"]
    assert efficiency_hints("进驻控制中枢时，控制中枢内所有干员的心情每小时恢复+0.05", room="控制中枢") == [
        "控制中枢心情恢复 +0.05/小时"
    ]
    assert efficiency_hints("这句话里没有数字", room=None) == []


def test_parse_building_skill_table_keeps_name_room_description() -> None:
    table = parse_building_skill_table([{"title": row} for row in CARGO_ROWS])
    by_name = {item.name: item for item in table}
    assert isinstance(by_name["喀兰之主"], BuildingSkill)
    assert by_name["喀兰之主"].room == "贸易站"
    assert by_name["喀兰之主"].description == "进驻贸易站时，订单获取效率+20%，且订单上限+4"
    # 重复行只留第一条，空名丢弃。
    deduped = parse_building_skill_table(
        [{"title": {"name": "慢性子", "room": "制造站", "description": "第一条"}},
         {"title": {"name": "慢性子", "room": "制造站", "description": "第二条"}},
         {"title": {"name": "", "room": "制造站", "description": "空名"}}]
    )
    assert [(item.name, item.description) for item in deduped] == [("慢性子", "第一条")]


def test_parse_operator_building_skills_keeps_slot_and_unlock() -> None:
    entries = parse_operator_building_skills(SERPENT_WIKITEXT)
    assert [(item.name, item.unlock, item.slot) for item in entries] == [
        ("自动化·α", "精英0", "1-1"),
        ("自动化·β", "精英2", "1-2"),
        ("我寻思能行", "精英2", "2-1"),
    ]
    assert entries[1].elite_requirement == 2
    assert entries[0].elite_requirement == 0
    assert parse_operator_building_skills("没有模板") == ()


# --------------------------------------------------------------------------- #
# skills()
# --------------------------------------------------------------------------- #
def test_skills_probe_ask_first_then_falls_back_to_operator_pages(tmp_path: Path) -> None:
    routes = _PrtsRoutes(ask_rows=None)

    async def scenario() -> dict[str, Any]:
        building = service(tmp_path, routes)
        return await building.skills(operator="银灰")

    result = asyncio.run(scenario())
    # 取技能表时先发 Cargo；解析干员对应关系时先试 SMW 反查探针（实测 0 结果 → 回落干员页）。
    actions = [request.url.params["action"] for request in routes.calls]
    assert actions.count("ask") == 1
    ask = next(request for request in routes.calls if request.url.params["action"] == "ask")
    assert "后勤技能名" in ask.url.params["query"]
    assert "银灰" in routes.revision_titles
    skills = {item["name"]: item for item in result["skills"]}
    assert set(skills) == {"喀兰贸易·α", "喀兰之主"}
    assert skills["喀兰之主"]["room"] == "贸易站"
    assert skills["喀兰之主"]["unlock"] == "精英2"
    assert skills["喀兰之主"]["description"] == "进驻贸易站时，订单获取效率+20%，且订单上限+4"
    assert skills["喀兰之主"]["hints"] == ["贸易站订单获取效率 +20%", "贸易站订单上限 +4"]
    assert skills["喀兰之主"]["owners"] == [
        {"name": "银灰", "char_id": "char_172_svrash", "unlock": "精英2", "source": "prts_page"}
    ]
    assert result["operator"]["title"] == "银灰"
    assert result["unavailable"] == []
    assert result["untrusted"] is True


def test_skills_uses_the_smw_mapping_when_the_probe_returns_rows(tmp_path: Path) -> None:
    routes = _PrtsRoutes(
        ask_rows={
            "银灰": {
                "fulltext": "银灰",
                "printouts": {
                    "后勤技能名": ["喀兰贸易·α", "喀兰之主"],
                    "后勤技能阶段": ["精英0", "精英2"],
                },
            }
        }
    )

    async def scenario() -> dict[str, Any]:
        return await service(tmp_path, routes).skills(operator="银灰")

    result = asyncio.run(scenario())
    assert routes.revision_titles == []  # 一次 SMW 查询拿全表，不再取干员页
    assert [item["name"] for item in result["skills"]] == ["喀兰贸易·α", "喀兰之主"]
    assert result["skills"][1]["room"] == "贸易站"
    assert result["skills"][1]["unlock"] == "精英2"


def test_skills_by_room_lists_owners_from_the_core_resource(tmp_path: Path) -> None:
    routes = _PrtsRoutes()

    async def scenario() -> dict[str, Any]:
        return await service(tmp_path, routes).skills(room="制造站")

    result = asyncio.run(scenario())
    by_name = {item["name"]: item for item in result["skills"]}
    assert set(by_name) >= {"慢性子", "自动化·α", "自动化·β", "拾荒者"}
    assert by_name["慢性子"]["hints"][-1] == "制造站生产力（最终） +25%"
    # 慢性子 在 core 表里与「延时摄影」同组：owner 列表按效果组给，标 approximate。
    assert by_name["慢性子"]["owners_approximate"] is True
    assert {owner["name"] for owner in by_name["慢性子"]["owners"]} == {"克洛丝", "图耶"}
    assert all(owner["source"] == "resource" for owner in by_name["慢性子"]["owners"])
    assert result["room"] == "制造站"
    # Cargo 走 static 档；按房间分块取整表（服务端单次上限 500 行）。
    assert len(routes.cargo_calls) >= 2
    assert routes.cargo_calls[0]["tables"] == "building_skill2"
    assert routes.cargo_calls[0]["fields"] == "name,room,description"


def test_skills_by_skill_name_marks_exact_owners(tmp_path: Path) -> None:
    routes = _PrtsRoutes()

    async def scenario() -> dict[str, Any]:
        return await service(tmp_path, routes).skills(skill="喀兰之主")

    result = asyncio.run(scenario())
    row = result["skills"][0]
    assert row["name"] == "喀兰之主"
    assert row["room"] == "贸易站"
    assert row["owners_approximate"] is False
    assert row["owners"] == [{"name": "银灰", "char_id": "char_172_svrash", "source": "resource"}]
    assert row["efficiency"] == {"all": 20}


def test_skills_requires_exactly_one_selector(tmp_path: Path) -> None:
    routes = _PrtsRoutes()

    async def scenario() -> None:
        building = service(tmp_path, routes)
        with pytest.raises(AppError) as none_given:
            await building.skills()
        assert none_given.value.code == ErrorCode.INVALID_PARAMETER
        with pytest.raises(AppError) as two_given:
            await building.skills(operator="银灰", room="贸易站")
        assert two_given.value.code == ErrorCode.INVALID_PARAMETER

    asyncio.run(scenario())


def test_skills_unknown_selector_value_is_game_data_not_found(tmp_path: Path) -> None:
    routes = _PrtsRoutes()

    async def scenario() -> None:
        building = service(tmp_path, routes)
        with pytest.raises(AppError) as unknown_skill:
            await building.skills(skill="不存在的技能")
        assert unknown_skill.value.code == ErrorCode.GAME_DATA_NOT_FOUND
        with pytest.raises(AppError) as unknown_room:
            await building.skills(room="食堂")
        assert unknown_room.value.code == ErrorCode.GAME_DATA_NOT_FOUND
        with pytest.raises(AppError) as unknown_operator:
            await building.skills(operator="查无此人")
        assert unknown_operator.value.code == ErrorCode.GAME_DATA_NOT_FOUND

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# candidates()
# --------------------------------------------------------------------------- #
def test_candidates_splits_by_room_and_respects_unlock(tmp_path: Path) -> None:
    routes = _PrtsRoutes()
    box = FakeBox(
        [
            box_row("char_172_svrash", "银灰", elite=2, rarity=6, level=90),
            box_row("char_124_kroos", "克洛丝", elite=0, rarity=3, level=55),
            box_row("char_416_zumama", "森蚺", elite=1, rarity=6, level=80),
        ]
    )

    async def scenario() -> dict[str, Any]:
        return await service(tmp_path, routes, box=box).candidates(layout="243", shifts=3)

    result = asyncio.run(scenario())
    assert result["layout"] == "243"
    assert result["shifts"] == 3
    rooms = {item["key"]: item for item in result["rooms"]}
    assert rooms["trading"]["count"] == 2
    assert rooms["manufacture"]["count"] == 4
    assert rooms["power"]["count"] == 3
    assert rooms["manufacture"]["slots"] == 3
    assert rooms["manufacture"]["operators_per_shift"] == 12

    trade = {item["name"]: item for item in rooms["trading"]["candidates"]}
    assert set(trade) == {"银灰"}
    assert [skill["name"] for skill in trade["银灰"]["skills"]] == ["喀兰之主"]
    assert trade["银灰"]["skills"][0]["hints"] == ["贸易站订单获取效率 +20%", "贸易站订单上限 +4"]

    mfg = {item["name"]: item for item in rooms["manufacture"]["candidates"]}
    # 克洛丝 E0 解锁慢性子；森蚺 E1 只解锁同槽位的 α（β 要精二）。
    assert set(mfg) == {"克洛丝", "森蚺"}
    assert [skill["name"] for skill in mfg["克洛丝"]["skills"]] == ["慢性子"]
    assert [skill["name"] for skill in mfg["森蚺"]["skills"]] == ["自动化·α"]
    assert mfg["森蚺"]["elite"] == 1
    assert result["owned_total"] == 3

    # 克洛丝的「独处」要精英1（她是 E0），宿舍没有候选。
    assert rooms["dormitory"]["count"] == 4
    assert rooms["dormitory"]["candidates"] == []

    # 只保留请求的房间。
    filtered = asyncio.run(
        service(tmp_path, routes, box=box).candidates(
            layout="153", shifts=2, rooms=["制造站"]
        )
    )
    assert [item["key"] for item in filtered["rooms"]] == ["manufacture"]


def test_candidates_reports_missing_account_data(tmp_path: Path) -> None:
    routes = _PrtsRoutes()
    box = FakeBox([])

    async def scenario() -> dict[str, Any]:
        return await service(tmp_path, routes, box=box).candidates(layout="252", shifts=2)

    result = asyncio.run(scenario())
    assert result["owned_total"] == 0
    assert "account" in result["unavailable"]
    assert result["hint"] == (
        "账号里没有已拥有的干员记录，先用 ask_user 建议用户在账号页绑定森空岛账号并同步练度"
        "再查候选。"
    )
    assert all(item["candidates"] == [] for item in result["rooms"])


def test_candidates_rejects_bad_layout_or_shifts(tmp_path: Path) -> None:
    routes = _PrtsRoutes()

    async def scenario() -> None:
        building = service(tmp_path, routes)
        with pytest.raises(AppError) as bad_layout:
            await building.candidates(layout="999", shifts=2)
        assert bad_layout.value.code == ErrorCode.INVALID_PARAMETER
        with pytest.raises(AppError) as bad_shifts:
            await building.candidates(layout="243", shifts=5)
        assert bad_shifts.value.code == ErrorCode.INVALID_PARAMETER
        with pytest.raises(AppError) as bad_rooms:
            await building.candidates(layout="243", shifts=2, rooms=["食堂"])
        assert bad_rooms.value.code == ErrorCode.INVALID_PARAMETER

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# get_operator 的 building 段（规格 §8.3）
# --------------------------------------------------------------------------- #
def test_operator_building_section_joins_cargo_rooms(tmp_path: Path) -> None:
    routes = _PrtsRoutes()
    layer = write_core_layer(tmp_path)
    operator = OperatorService(
        prts=make_prts(routes),
        battle_data_path=layer / "battle_data.json",
        building=BuildingService(prts=make_prts(routes), core_resource_roots=[layer]),
    )

    async def scenario() -> dict[str, Any]:
        return await operator.get("银灰", sections=["building"])

    result = asyncio.run(scenario())
    rows = result["building"]
    assert [item["name"] for item in rows] == ["喀兰贸易·α", "喀兰之主"]
    assert rows[1]["room"] == "贸易站"
    assert rows[1]["unlock"] == "精英2"
    assert rows[1]["slot"] == "1-2"
    assert rows[1]["description"] == "进驻贸易站时，订单获取效率+20%，且订单上限+4"
    assert rows[1]["hints"] == ["贸易站订单获取效率 +20%", "贸易站订单上限 +4"]
    assert "building" not in result["unavailable"]


# --------------------------------------------------------------------------- #
# plans()
# --------------------------------------------------------------------------- #
class FakeResourceService:
    def __init__(self, items: list[dict[str, Any]], contents: dict[str, dict[str, Any]]) -> None:
        self.items = items
        self.contents = contents
        self.list_calls: list[dict[str, Any]] = []

    async def list_infrast_plans(self, **kwargs: Any) -> dict[str, Any]:
        self.list_calls.append(kwargs)
        return {"items": list(self.items), "total": len(self.items), "page": 1, "size": 200}

    async def read_infrast_plan(self, filename: str) -> dict[str, Any]:
        if filename not in self.contents:
            raise AppError(ErrorCode.TASK_PARAM_INVALID, f"找不到基建方案文件：{filename}")
        return self.contents[filename]


def test_plans_reuses_the_resource_listing_and_adds_periods() -> None:
    resource = FakeResourceService(
        [
            {
                "id": "asset-1",
                "kind": "infrast_plan",
                "name": "243 三班",
                "filename": "custom_infrast/243-three.json",
                "meta": {"plan_count": 3, "filename": "custom_infrast/243-three.json"},
            },
            {"id": "asset-2", "kind": "infrast_plan", "name": "坏的", "filename": None},
        ],
        {
            "custom_infrast/243-three.json": {
                "title": "243-高配3队简化",
                "plans": [
                    {"name": "第1班", "period": [["06:00", "14:00"]]},
                    {"name": "第2班", "period": [["14:00", "22:00"]]},
                    {"name": "第3班", "period": [["22:00", "06:00"]]},
                ],
            }
        },
    )

    async def scenario() -> dict[str, Any]:
        building = BuildingService(resource_service=resource)
        return await building.plans()

    result = asyncio.run(scenario())
    assert result["total"] == 2
    first = result["plans"][0]
    assert first["filename"] == "custom_infrast/243-three.json"
    assert first["name"] == "243 三班"
    assert first["title"] == "243-高配3队简化"
    assert first["plan_count"] == 3
    assert [plan["period"] for plan in first["plans"]] == [
        [["06:00", "14:00"]],
        [["14:00", "22:00"]],
        [["22:00", "06:00"]],
    ]
    second = result["plans"][1]
    assert second["filename"] is None
    assert second["readable"] is False
    assert resource.list_calls == [{"page": 1, "size": 200}]
