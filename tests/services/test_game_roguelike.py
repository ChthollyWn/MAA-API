"""集成战略领域层（规格 §8.4）：`RoguelikeService.info` 的回答形状。

全夹具 + `httpx.MockTransport`，不打网（规格 §4.9）。夹具按 2026-10-10 的 PRTS 实况
裁剪：章节表与「开始探索」节的 wikitext 取自 `沉沦者的黑流树海` 与 `岁的界园志异`
（都是真实响应的子集，行结构与模板原样保留），藏品页取自 `热水壶` 的
`{{收藏品/common}}`。异步用例用 `asyncio.run` 跑（本仓库不装 pytest-asyncio）。

重点钉两条纪律：

1. **不要整页拉**：取正文只发一次 `action=parse&section=N&prop=wikitext|revid`，
   不发 `action=query&prop=revisions`（主题页 170–261 KB，规格 §8.4）；
2. **主题默认最新**：`主题` 常量表的最后一个是 BlackFlow（`分类:集成战略` 里最新的一页），
   结果里必须带「上游策略尚不完整」的说明（规格 §2.5、§8.4）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.http import ExternalClient
from maa_api.services.external.prts import PrtsClient, validate_title
from maa_api.services.game.roguelike import (
    DEFAULT_THEME,
    ROGUELIKE_THEMES,
    THEME_LABELS,
    TEXT_MAX_CHARS,
    TOPICS,
    RoguelikeService,
    default_roguelike_service,
)
from maa_api.settings import Settings, SourcesSettings


def async_test(func):
    """本仓库不装 pytest-asyncio：异步用例统一用 `asyncio.run` 跑。"""

    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    wrapper.__name__ = func.__name__
    return wrapper


# --------------------------------------------------------------------------- #
# 夹具（真实响应的裁剪）
# --------------------------------------------------------------------------- #
def _section(level: str, line: str, index: str, number: str, title: str) -> dict[str, Any]:
    return {
        "toclevel": int(level) - 1,
        "level": level,
        "line": line,
        "number": number,
        "index": index,
        "fromtitle": title,
        "byteoffset": 1036,
        "anchor": line,
        "linkAnchor": line,
    }


BLACKFLOW_TITLE = "沉沦者的黑流树海"
JIEGARDEN_TITLE = "岁的界园志异"

#: `action=parse&prop=sections&page=沉沦者的黑流树海`（头条目裁剪，索引与层级照实）。
BLACKFLOW_SECTIONS: dict[str, Any] = {
    "parse": {
        "title": BLACKFLOW_TITLE,
        "pageid": 94004,
        "sections": [
            _section("2", "本主题下的独特机制与机制调整", "1", "1", BLACKFLOW_TITLE),
            _section("2", "活动专属道具", "2", "2", BLACKFLOW_TITLE),
            _section("2", "主题更新记录", "3", "3", BLACKFLOW_TITLE),
            _section("2", "开始探索", "4", "4", BLACKFLOW_TITLE),
            _section("3", "常规行动", "5", "4.1", BLACKFLOW_TITLE),
            _section("3", "实践者列表", "6", "4.2", BLACKFLOW_TITLE),
            _section("2", "区域", "7", "7", BLACKFLOW_TITLE),
            _section("2", "拟造物质编目（收藏品一览）", "15", "15", BLACKFLOW_TITLE),
            _section("2", "关卡一览", "17", "17", BLACKFLOW_TITLE),
            _section("2", "注释与链接", "20", "20", BLACKFLOW_TITLE),
        ],
    }
}

#: `action=parse&prop=sections&page=岁的界园志异`：只用得到「收藏品一览」那一节的索引。
JIEGARDEN_SECTIONS: dict[str, Any] = {
    "parse": {
        "title": JIEGARDEN_TITLE,
        "pageid": 80563,
        "sections": [
            _section("2", "开始探索", "4", "4", JIEGARDEN_TITLE),
            _section("2", "古今学识", "15", "15", JIEGARDEN_TITLE),
            _section("2", "珍玩集册（收藏品一览）", "17", "17", JIEGARDEN_TITLE),
            _section("2", "藏钱木盒（通宝一览）", "18", "18", JIEGARDEN_TITLE),
        ],
    }
}

#: 「开始探索」节里与分队表有关的部分（真实行的结构与模板原样保留，行数裁到 4）。
BLACKFLOW_START = (
    "==开始探索==\n"
    "'''1. 选择分队'''\n"
    "\n"
    "选择具有全局效果的分队。\n"
    "\n"
    "※部分分队可在【[[#生命游戏|生命游戏]]】中点亮节点以解锁和提升效果。\n"
    '{{#vardefine:newbadge|<span style="background:linear-gradient(90deg, #116b62, #29288b, #751d18); '
    'color: white; padding: 0px 5px; border-radius: 2px;">NEW</span>}}\n'
    '{| class="wikitable mw-collapsible mw-collapsed" style="text-align:center;white-space:normal"\n'
    "|-\n"
    "! 分队 !! 效果 !! 解锁条件\n"
    "|-\n"
    '|style="background-color:#212121;color:white;"|[[文件:集成战略 分队 38.png|50px|link=]]<br>'
    "{{#var:newbadge}} 文明开化分队\n"
    "|消除实托邦中的理想源后获得2点行动力和1{{color|#5e8505|''' ① {{mdi|arrow-right}} 2'''}}个随机收藏品"
    " || ''初始解锁''\n"
    "|-\n"
    '|style="background-color:#212121;color:white;"|[[文件:集成战略 分队 1.png|50px|link=]]<br>指挥分队\n'
    "|目标生命上限+2，每次战斗结束后额外回复1目标生命 || ''初始解锁''\n"
    "|-\n"
    '|style="background-color:#212121;color:white;"|[[文件:集成战略 分队 2.png|50px|link=]]<br>特勤分队\n'
    "|可携带干员+2，可同时部署人数+2<br>初始招募五名罗德岛预备干员和"
    "{{:集成战略/招募标签|临时招募|color=blue|icon=蓝临时}}[[机械师|机械师]]"
    "|| ''初始解锁''\n"
    "|-\n"
    '|style="background-color:#212121;color:white;"|[[文件:集成战略 分队 5.png|50px|link=]]<br>突击战术分队\n'
    "|招募和进阶4星以上的【近卫】【先锋】干员时希望降低2点和1点 || 拥有合计至少5名先锋或近卫干员完成游戏结局\n"
    "|}\n"
    "<references/>\n"
)

#: 前言（`section=0`）：真主题页的 `{{活动信息}}` 与开场白（后半段裁掉）。
BLACKFLOW_LEAD = (
    "{{活动信息\n"
    "|类型=集成战略\n"
    "|隐藏=是\n"
    "|名称=集成战略「沉沦者的黑流树海」\n"
    "|名称缩短=沉沦者的黑流树海\n"
    "|副标题=THE BLACK FLOW OF THE DROWNING SEEKERS\n"
    "|活动开始时间=2026-07-17 16:00:00\n"
    "|活动结束时间=2027-03-10 16:00:00\n"
    "|兑换结束时间=2027-03-10 16:00:00\n"
    "|标题图文件名=主题图 沉沦者的黑流树海.png\n"
    "|官网链接=\n"
    "}}\n"
    "<center>{{集成战略导航}}</center>\n"
    "'''沉沦者的黑流树海'''是《明日方舟》集成战略的第六个主题。\n"
)

#: `query&prop=revisions&titles=热水壶`：`{{收藏品/common}}` 的真实字段（主题裁到 2 个）。
RELIC_HOT_WATER = (
    "{{Cbox2|mdi=true|icon=information-variant-circle|text='''在非标准模式下……'''}}\n"
    "\n"
    "{{收藏品/common\n"
    "|名称=热水壶\n"
    "|iconId=rogue_6_relic_legacy_1\n"
    "|稀有度=0\n"
    "|描述=罗德岛办公室里的同款热水壶，有人经常大半夜用热水壶煮速食面吃，这种生活习惯不是很健康......\n"
    "|主题1=傀影与猩红孤钻\n"
    "|角标1={{收藏品/角标|noshop}}\n"
    "|售价1=8\n"
    "|效果1=立即获得目标生命+2，希望+1\n"
    "|获取条件1=【常规行动】模式开局的行动奖励中选择“随手拿点啥”选项\n"
    "|解锁条件1=\n"
    "|主题2=岁的界园志异\n"
    "|角标2={{收藏品/角标|seedreward}}\n"
    "|售价2=8\n"
    "|效果2=目标生命上限+1，希望+1\n"
    "|获取条件2=【常规行动】模式开局的行动奖励中选择“随手拿点啥”选项\n"
    "|解锁条件2=\n"
    "}}\n"
)

#: 一个不是藏品的页面（没有 `{{收藏品/common}}`）。
NOT_A_RELIC = "{{干员信息|名称=测试干员}}\n'''测试干员'''是一名干员。\n"


def _wikitext_page(title: str, content: str, *, revid: int = 432729) -> dict[str, Any]:
    return {
        "batchcomplete": True,
        "query": {
            "pages": [
                {
                    "pageid": 1,
                    "ns": 0,
                    "title": title,
                    "revisions": [
                        {"revid": revid, "timestamp": "2026-10-10T02:00:00Z", "slots": {"main": {"content": content}}}
                    ],
                }
            ]
        },
    }


def _parse_section(title: str, section: str, wikitext: str, *, revid: int = 432729) -> dict[str, Any]:
    return {"parse": {"title": title, "pageid": 94004, "revid": revid, "wikitext": wikitext, "section": section}}


def dispatch(routes: dict[Any, Any], *, calls: list[httpx.Request] | None = None):
    """按 `action` + `prop`（+ `section`）分派夹具；没安排的端点回 404。

    键：``sections`` → `prop=sections`；``("section", "4")`` → 取第 4 节；
    ``("revisions", 标题)`` → 整页 wikitext（只该在藏品页上出现）。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        params = dict(request.url.params)
        action = params.get("action", "")
        prop = params.get("prop", "")
        route: Any = None
        if action == "parse" and prop == "sections":
            route = routes.get("sections")
        elif action == "parse" and "wikitext" in prop:
            route = routes.get(("section", params.get("section")))
        elif action == "query" and prop == "revisions":
            route = routes.get(("revisions", params.get("titles")))
        if route is None:
            return httpx.Response(404, content=b"{}")
        if callable(route):
            route = route(params)
        return httpx.Response(200, content=json.dumps(route, ensure_ascii=False).encode("utf-8"))

    return httpx.MockTransport(handler)


def make_service(routes: dict[Any, Any], *, calls: list[httpx.Request] | None = None) -> RoguelikeService:
    external = ExternalClient(
        transport=dispatch(routes, calls=calls),
        settings_provider=lambda: Settings(
            sources=SourcesSettings(use_proxy=False, prts_rate_per_minute=120)
        ),
    )
    return RoguelikeService(prts=PrtsClient(external))


def section_params(calls: list[httpx.Request]) -> list[dict[str, str]]:
    return [
        dict(call.url.params)
        for call in calls
        if dict(call.url.params).get("action") == "parse"
        and "wikitext" in dict(call.url.params).get("prop", "")
    ]


# --------------------------------------------------------------------------- #
# 常量与默认主题
# --------------------------------------------------------------------------- #
def test_theme_constants_end_with_the_latest_theme() -> None:
    """主题表按上线顺序；默认取最后一个（规格 §8.4「默认最新主题」）。"""

    assert ROGUELIKE_THEMES == ("Phantom", "Mizuki", "Sami", "Sarkaz", "JieGarden", "BlackFlow")
    assert DEFAULT_THEME == "BlackFlow"
    assert DEFAULT_THEME == ROGUELIKE_THEMES[-1]
    assert set(THEME_LABELS) == set(ROGUELIKE_THEMES)
    assert THEME_LABELS["BlackFlow"] == BLACKFLOW_TITLE
    assert TOPICS == ("overview", "squads", "relic", "section")
    assert TEXT_MAX_CHARS == 4000


def test_default_facade_is_a_singleton() -> None:
    assert default_roguelike_service() is default_roguelike_service()


# --------------------------------------------------------------------------- #
# squads：按章节取，不整页拉
# --------------------------------------------------------------------------- #
@async_test
async def test_squads_reads_one_section_and_parses_the_table() -> None:
    calls: list[httpx.Request] = []
    service = make_service(
        {
            "sections": BLACKFLOW_SECTIONS,
            ("section", "4"): _parse_section(BLACKFLOW_TITLE, "4", BLACKFLOW_START),
        },
        calls=calls,
    )

    result = await service.info(topic="squads")

    assert result["theme"] == "BlackFlow"
    assert result["theme_label"] == BLACKFLOW_TITLE
    assert result["title"] == BLACKFLOW_TITLE
    assert result["topic"] == "squads"
    assert result["section"] == "开始探索"
    assert result["section_index"] == "4"
    assert result["revision"] == 432729
    assert result["fields"]["type"] == "squads"
    assert result["fields"]["squads"] == [
        {
            "name": "文明开化分队",
            # `{{mdi|arrow-right}}` 是图标，`strip_markup` 按「取最后一个位置参数」渲染成图标名
            # （与 `get_prts_page` 同一口径，不在本层特判）。
            "effect": "消除实托邦中的理想源后获得2点行动力和1 ① arrow-right 2个随机收藏品",
            "unlock": "初始解锁",
        },
        {"name": "指挥分队", "effect": "目标生命上限+2，每次战斗结束后额外回复1目标生命", "unlock": "初始解锁"},
        {
            "name": "特勤分队",
            "effect": "可携带干员+2，可同时部署人数+2\n初始招募五名罗德岛预备干员和临时招募机械师",
            "unlock": "初始解锁",
        },
        {
            "name": "突击战术分队",
            "effect": "招募和进阶4星以上的【近卫】【先锋】干员时希望降低2点和1点",
            "unlock": "拥有合计至少5名先锋或近卫干员完成游戏结局",
        },
    ]
    # text 只给分队表前的说明；行内容在 fields.squads 里，不重复罗列。
    assert "1. 选择分队" in result["text"]
    assert "分队表" not in result["text"]
    assert "references" not in result["text"]
    assert result["truncated"] is None
    assert result["untrusted"] is True
    assert result["unavailable"] == []
    assert result["sources"] and result["sources"][0]["title"] == BLACKFLOW_TITLE
    assert result["sources"][0]["url"] == f"https://prts.wiki/w/{BLACKFLOW_TITLE}"
    assert result["sources"][0]["revision"] == 432729
    # 章节表随结果一起给，模型据此再按 section 取。
    assert {"index": "15", "level": 2, "title": "拟造物质编目（收藏品一览）"} in result["sections"]

    # 只两个请求：章节表 + 第 4 节；没有整页 `prop=revisions`。
    assert len(calls) == 2
    assert [params["section"] for params in section_params(calls)] == ["4"]
    assert section_params(calls)[0]["page"] == BLACKFLOW_TITLE
    assert section_params(calls)[0]["prop"] == "wikitext|revid"
    assert all(dict(call.url.params).get("prop") != "revisions" for call in calls)


@async_test
async def test_squad_cells_strip_single_bracket_external_links() -> None:
    """黑流树海把职业名写成 `[https://prts.wiki/w/… 本源术师]`：只留显示名。"""

    body = (
        "==开始探索==\n"
        "'''1. 选择分队'''\n"
        '{| class="wikitable"\n'
        "|-\n! 分队 !! 效果 !! 解锁条件\n"
        "|-\n"
        '|style="background-color:#212121;color:white;"|[[文件:集成战略 分队 37.png|50px|link=]]<br>本源研修分队\n'
        "|招募4星以上【[https://prts.wiki/w/%E5%B9%B2%E5%91%98%E4%B8%80%E8%A7%88 本源术师]】干员时希望降低2点"
        " || ''初始解锁''\n"
        "|}\n"
    )
    service = make_service(
        {
            "sections": BLACKFLOW_SECTIONS,
            ("section", "4"): _parse_section(BLACKFLOW_TITLE, "4", body),
        }
    )

    result = await service.info(topic="squads")

    assert result["fields"]["squads"] == [
        {"name": "本源研修分队", "effect": "招募4星以上【本源术师】干员时希望降低2点", "unlock": "初始解锁"}
    ]


@async_test
async def test_squads_falls_back_to_the_start_section_of_the_requested_theme() -> None:
    """没有标题里带「分队」的章节时退到「开始探索」；分队表找不到就标 unavailable。"""

    sections = {
        "parse": {
            "title": JIEGARDEN_TITLE,
            "pageid": 80563,
            "sections": [_section("2", "开始探索", "4", "4", JIEGARDEN_TITLE)],
        }
    }
    service = make_service(
        {
            "sections": sections,
            ("section", "4"): _parse_section(JIEGARDEN_TITLE, "4", "==开始探索==\n本主题没有分队表。\n"),
        }
    )

    result = await service.info(theme="JieGarden", topic="squads")

    assert result["theme"] == "JieGarden"
    assert result["theme_label"] == JIEGARDEN_TITLE
    assert result["fields"] == {"type": "squads", "squads": []}
    assert result["unavailable"] == ["squads"]
    assert any("分队" in note for note in result["notes"])


@async_test
async def test_blackflow_carries_the_upstream_incomplete_note() -> None:
    service = make_service(
        {
            "sections": BLACKFLOW_SECTIONS,
            ("section", "4"): _parse_section(BLACKFLOW_TITLE, "4", BLACKFLOW_START),
        }
    )

    result = await service.info(topic="squads")

    assert any("BlackFlow" in note or "黑流树海" in note for note in result["notes"])
    assert any("不完整" in note or "前置" in note for note in result["notes"])


@async_test
async def test_jiegarden_has_no_blackflow_note() -> None:
    service = make_service(
        {
            "sections": JIEGARDEN_SECTIONS,
            ("section", "4"): _parse_section(JIEGARDEN_TITLE, "4", BLACKFLOW_START),
        }
    )

    result = await service.info(theme="JieGarden", topic="squads")

    assert not any("不完整" in note for note in result["notes"])


# --------------------------------------------------------------------------- #
# overview：前言 + `{{活动信息}}`
# --------------------------------------------------------------------------- #
@async_test
async def test_overview_parses_activity_info_from_the_lead_section() -> None:
    calls: list[httpx.Request] = []
    service = make_service(
        {
            "sections": BLACKFLOW_SECTIONS,
            ("section", "0"): _parse_section(BLACKFLOW_TITLE, "0", BLACKFLOW_LEAD),
        },
        calls=calls,
    )

    result = await service.info()

    assert result["topic"] == "overview"
    assert result["section"] == "前言"
    assert result["section_index"] == "0"
    assert result["fields"]["type"] == "activity"
    assert result["fields"]["activity"]["name"] == "沉沦者的黑流树海"
    assert result["fields"]["activity"]["type"] == "集成战略"
    assert result["fields"]["activity"]["start_at"] == "2026-07-17T16:00:00+08:00"
    assert result["fields"]["activity"]["end_at"] == "2027-03-10T16:00:00+08:00"
    assert "沉沦者的黑流树海" in result["text"]
    assert "活动信息" not in result["text"]
    assert [params["section"] for params in section_params(calls)] == ["0"]


@async_test
async def test_overview_keeps_going_without_an_activity_template() -> None:
    service = make_service(
        {
            "sections": BLACKFLOW_SECTIONS,
            ("section", "0"): _parse_section(BLACKFLOW_TITLE, "0", "只有一句开场白。\n"),
        }
    )

    result = await service.info(topic="overview")

    assert result["fields"] is None
    assert result["text"] == "只有一句开场白。"
    assert result["unavailable"] == ["activity"]


# --------------------------------------------------------------------------- #
# section：按名字或序号取一节
# --------------------------------------------------------------------------- #
@async_test
async def test_section_topic_matches_the_title_and_truncates() -> None:
    long_body = "==区域==\n" + "苔藓".join(["一段很长很长的正文"] * 600)
    service = make_service(
        {
            "sections": BLACKFLOW_SECTIONS,
            ("section", "7"): _parse_section(BLACKFLOW_TITLE, "7", long_body),
        }
    )

    result = await service.info(topic="section", name="区域")

    assert result["section"] == "区域"
    assert result["section_index"] == "7"
    assert result["fields"] is None
    assert len(result["text"]) == TEXT_MAX_CHARS
    assert result["truncated"]["field"] == "text"
    assert result["truncated"]["next_offset"] == TEXT_MAX_CHARS
    assert result["truncated"]["total_chars"] > TEXT_MAX_CHARS


@async_test
async def test_section_topic_accepts_a_section_title_with_html_and_an_index() -> None:
    sections = {
        "parse": {
            "title": JIEGARDEN_TITLE,
            "pageid": 80563,
            "sections": [
                _section("2", "<span>珍玩集册（收藏品一览）</span>", "17", "17", JIEGARDEN_TITLE),
            ],
        }
    }
    service = make_service(
        {
            "sections": sections,
            ("section", "17"): _parse_section(JIEGARDEN_TITLE, "17", "===珍玩集册（收藏品一览）===\n珍玩。\n"),
        }
    )

    by_title = await service.info(theme="JieGarden", topic="section", name="珍玩集册（收藏品一览）")
    assert by_title["section"] == "珍玩集册（收藏品一览）"
    assert by_title["section_index"] == "17"

    by_index = await service.info(theme="JieGarden", topic="section", name="17")
    assert by_index["section"] == "珍玩集册（收藏品一览）"
    assert "珍玩。" in by_index["text"]


@async_test
async def test_section_topic_rejects_unknown_name_and_lists_available_sections() -> None:
    calls: list[httpx.Request] = []
    service = make_service({"sections": BLACKFLOW_SECTIONS}, calls=calls)

    with pytest.raises(AppError) as error:
        await service.info(topic="section", name="不存在的章节")

    assert error.value.code is ErrorCode.GAME_DATA_NOT_FOUND
    assert "开始探索" in error.value.message
    assert section_params(calls) == []


@async_test
async def test_section_topic_requires_a_name() -> None:
    calls: list[httpx.Request] = []
    service = make_service({"sections": BLACKFLOW_SECTIONS}, calls=calls)

    with pytest.raises(AppError) as error:
        await service.info(topic="section")

    assert error.value.code is ErrorCode.INVALID_PARAMETER
    assert calls == []


@async_test
async def test_missing_section_response_surfaces_the_source_error() -> None:
    """`{"error": …}` 由访问层翻成 `EXTERNAL_RESPONSE_INVALID`（规格 §3.5）。"""

    service = make_service(
        {
            "sections": BLACKFLOW_SECTIONS,
            ("section", "7"): {"error": {"code": "nosuchsection", "info": "There is no section 7."}},
        }
    )

    with pytest.raises(AppError) as error:
        await service.info(topic="section", name="区域")
    assert error.value.code is ErrorCode.EXTERNAL_RESPONSE_INVALID


# --------------------------------------------------------------------------- #
# relic：单页优先
# --------------------------------------------------------------------------- #
@async_test
async def test_relic_by_name_parses_the_collectible_template() -> None:
    calls: list[httpx.Request] = []
    service = make_service(
        {
            "sections": BLACKFLOW_SECTIONS,
            ("revisions", "热水壶"): _wikitext_page("热水壶", RELIC_HOT_WATER),
        },
        calls=calls,
    )

    result = await service.info(topic="relic", name="热水壶")

    assert result["theme"] == "BlackFlow"
    assert result["topic"] == "relic"
    assert result["section"] is None
    assert result["section_index"] is None
    assert result["fields"]["type"] == "relic"
    relic = result["fields"]["relic"]
    assert relic["name"] == "热水壶"
    assert relic["icon_id"] == "rogue_6_relic_legacy_1"
    assert relic["rarity"] == 0
    assert relic["description"].startswith("罗德岛办公室里的同款热水壶")
    assert relic["themes"][0]["theme"] == "傀影与猩红孤钻"
    assert relic["themes"][0]["price"] == 8
    assert relic["themes"][0]["effect"] == "立即获得目标生命+2，希望+1"
    assert relic["themes"][0]["unlock"] == ""
    assert relic["themes"][1]["theme"] == JIEGARDEN_TITLE
    # 藏品页是整页取（内容不大），但不该去取主题页正文。
    assert [dict(call.url.params).get("titles") for call in calls] == ["热水壶"]
    assert result["sources"][0]["title"] == "热水壶"
    assert "热水壶" in result["text"]
    assert "收藏品/common" not in result["text"]


@async_test
async def test_relic_by_name_rejects_a_page_that_is_not_a_collectible() -> None:
    service = make_service(
        {
            "sections": BLACKFLOW_SECTIONS,
            ("revisions", "测试干员"): _wikitext_page("测试干员", NOT_A_RELIC),
        }
    )

    with pytest.raises(AppError) as error:
        await service.info(topic="relic", name="测试干员")

    assert error.value.code is ErrorCode.GAME_DATA_NOT_FOUND
    assert "收藏品" in error.value.message


@async_test
async def test_relic_without_name_points_at_the_collectible_index_section() -> None:
    service = make_service(
        {
            "sections": BLACKFLOW_SECTIONS,
            ("section", "15"): _parse_section(
                BLACKFLOW_TITLE,
                "15",
                "==拟造物质编目（收藏品一览）==\n{{参阅|沉沦者的黑流树海/拟造物质编目|详细信息}}\n",
            ),
        }
    )

    result = await service.info(topic="relic")

    assert result["section"] == "拟造物质编目（收藏品一览）"
    assert result["section_index"] == "15"
    assert result["fields"]["type"] == "relic_index"
    assert result["fields"]["index_page"] == "沉沦者的黑流树海/拟造物质编目"
    assert result["unavailable"] == ["relic_list"]
    assert "拟造物质编目" in result["text"]


@async_test
async def test_relic_without_name_and_without_an_index_section_degrades() -> None:
    sections = {
        "parse": {
            "title": BLACKFLOW_TITLE,
            "pageid": 94004,
            "sections": [_section("2", "开始探索", "4", "4", BLACKFLOW_TITLE)],
        }
    }
    service = make_service({"sections": sections})

    result = await service.info(topic="relic")

    assert result["fields"] is None
    assert result["unavailable"] == ["relic"]
    assert result["text"] == ""


# --------------------------------------------------------------------------- #
# 参数校验
# --------------------------------------------------------------------------- #
@async_test
async def test_theme_accepts_the_chinese_label_and_normalizes_spaces() -> None:
    service = make_service(
        {
            "sections": JIEGARDEN_SECTIONS,
            ("section", "4"): _parse_section(JIEGARDEN_TITLE, "4", BLACKFLOW_START),
        }
    )

    result = await service.info(theme=" 岁的界园志异 ", topic="squads")

    assert result["theme"] == "JieGarden"
    assert result["title"] == JIEGARDEN_TITLE


@async_test
async def test_invalid_arguments_are_rejected_before_any_request() -> None:
    calls: list[httpx.Request] = []
    service = make_service({"sections": BLACKFLOW_SECTIONS}, calls=calls)

    for kwargs in ({"theme": "Nope"}, {"topic": "nope"}, {"theme": "官方服"}):
        with pytest.raises(AppError) as error:
            await service.info(**kwargs)
        assert error.value.code is ErrorCode.INVALID_PARAMETER

    assert calls == []


def test_title_validation_helper_matches_the_client_rules() -> None:
    """章节取数复用 `PrtsClient` 的标题白名单（规格 §3.6）。"""

    assert validate_title(" 岁的界园志异 ") == "岁的界园志异"
    with pytest.raises(AppError):
        validate_title("分类:集成战略")
