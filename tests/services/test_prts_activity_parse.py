"""M25 Task 5：活动页解析（规格 §5.6）的模板字段与正文关卡引用。

纯函数测试：不打网、不读仓库以外的文件。真实夹具是 ``tests/fixtures/prts/page_*.json``
（PRTS ``action=query&prop=revisions&rvslots=main`` 的裁剪快照，来源与抓取日期见同目录
README.md），正文在 ``query.pages[0].revisions[0].slots.main.content``：

- ``page_vec03.json``：``矢量突破/03``，``{{关卡链接}}`` 第二参数是 stageId；
- ``page_zuori.json``：``昨日海``，``{{关卡链接}}`` 只给代号。

合成用例里的 wikitext 形状都来自实测页面（``act3break 01`` 的空格写法、
``VEC-SP01 重力危机`` 这种「第二参数是页面名」的写法、正文里的 ``[[代号 中文名]]`` 链接）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from maa_api.services.prts_parse.activity import (
    CN_TIMEZONE,
    ActivityPage,
    StageRef,
    is_stage_code,
    parse_activity_page,
    parse_page_time,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "prts"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def wikitext(name: str) -> str:
    """夹具里 ``query.pages[0]`` 的正文（``redirects=1`` 之后的页面）。"""
    return fixture(name)["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"]


def fixture_title(name: str) -> str:
    """夹具里 ``query.pages[0]`` 的标题（可能已被 ``redirects=1`` 改写）。"""
    return fixture(name)["query"]["pages"][0]["title"]


@pytest.fixture(scope="module")
def vec03() -> str:
    """矢量突破/03 活动页正文（裁剪快照）。"""
    return wikitext("page_vec03.json")


@pytest.fixture(scope="module")
def zuori() -> str:
    """昨日海活动页正文（裁剪快照）。"""
    return wikitext("page_zuori.json")


def parse(content: str, *, page_title: str = "测试页") -> ActivityPage:
    return parse_activity_page(content, page_title=page_title)


# --------------------------------------------------------------------------- #
# {{活动信息}}：名称缩短 / 类型 / 三个时间字段
# --------------------------------------------------------------------------- #


def test_activity_info_fields() -> None:
    page = parse(
        "{{活动信息\n"
        "|名称=SideStory「昨日海」\n"
        "|名称缩短=昨日海\n"
        "|类型=支线故事\n"
        "|活动开始时间=2026-10-09 12:00\n"
        "|活动结束时间=2026-10-30 03:59\n"
        "|兑换结束时间=2026-10-31 03:59\n"
        "}}",
        page_title="昨日海",
    )
    assert page.page_title == "昨日海"
    assert page.name == "昨日海"
    assert page.type == "支线故事"
    assert page.start_at == datetime(2026, 10, 9, 12, 0, tzinfo=CN_TIMEZONE)
    assert page.end_at == datetime(2026, 10, 30, 3, 59, tzinfo=CN_TIMEZONE)
    assert page.exchange_end_at == datetime(2026, 10, 31, 3, 59, tzinfo=CN_TIMEZONE)
    assert page.stages == ()
    assert page.codes == ()


@pytest.mark.parametrize(
    "content",
    [
        "{{活动信息|名称=SideStory「昨日海」|类型=支线故事}}",  # 缺 名称缩短
        "{{活动信息|名称=SideStory「昨日海」|名称缩短=|类型=支线故事}}",  # 空的 名称缩短
        "{{活动信息|名称=SideStory「昨日海」|名称缩短=  |类型=支线故事}}",  # 只有空白
    ],
)
def test_name_falls_back_to_full_name(content: str) -> None:
    page = parse(content)
    assert page.name == "SideStory「昨日海」"


def test_name_short_strips_display_markup() -> None:
    page = parse("{{活动信息|名称缩短=[[矢量突破|矢量突破 #3]]}}")
    assert page.name == "矢量突破 #3"


def test_missing_fields_and_info_template_yield_none() -> None:
    page = parse("{{活动信息}}")
    assert (page.name, page.type) == (None, None)
    assert (page.start_at, page.end_at, page.exchange_end_at) == (None, None, None)

    # 没有 {{活动信息}} 也不炸：字段全 None，但正文里的关卡引用照常解析
    bare = parse("{{关卡链接|VEC-01|act3break 01}}")
    assert (bare.name, bare.type, bare.start_at) == (None, None, None)
    assert bare.codes == ("VEC-01",)

    # 时间字段在但值非法：只影响该字段
    partial = parse("{{活动信息|名称缩短=X|活动开始时间=待定|活动结束时间=2026-02-30}}")
    assert partial.name == "X"
    assert (partial.start_at, partial.end_at) == (None, None)


# --------------------------------------------------------------------------- #
# {{关卡链接}} 与正文链接
# --------------------------------------------------------------------------- #


def test_stage_link_second_arg_is_stage_id() -> None:
    # 实测写法：act3break 01（空格在 PRTS 上就是下划线）
    page = parse("{{关卡链接|VEC-01|act3break 01}}")
    assert page.stages == (StageRef(code="VEC-01", stage_id="act3break_01"),)
    assert page.stages[0].page_title is None


def test_stage_link_with_code_only_has_no_hints() -> None:
    page = parse("{{关卡链接|YW-1}}")
    assert page.stages == (StageRef(code="YW-1"),)
    assert page.stages[0].stage_id is None
    assert page.stages[0].page_title is None


@pytest.mark.parametrize(
    "hint",
    [
        "VEC-SP01 重力危机",  # 大写 + 中文：页面名
        "VEC-SP01",  # 全大写：不是 stageId 的形状
        "Act3break 01",  # 首字母大写
        "7-18",  # 数字开头
    ],
)
def test_stage_link_second_arg_page_name_is_not_stage_id(hint: str) -> None:
    page = parse("{{关卡链接|VEC-01|" + hint + "}}")
    assert page.stages == (StageRef(code="VEC-01", page_title=hint),)


def test_body_link_fills_page_title_fallback() -> None:
    # 只给代号的模板没有页面名，正文链接 ``[[代号 中文名]]`` 是兜底来源
    page = parse("{{关卡链接|YW-1}}\n[[YW-2 沉没之城]]\n[[YW-3 另一座城|YW-3]]\n[[YW-9]]\n")
    assert page.stages == (
        StageRef(code="YW-1"),
        StageRef(code="YW-2", page_title="YW-2 沉没之城"),
        StageRef(code="YW-3", page_title="YW-3 另一座城"),
    )
    assert page.codes == ("YW-1", "YW-2", "YW-3")


def test_stage_refs_dedup_keeps_first_and_appearance_order() -> None:
    """同代号取第一次出现的位置与 stageId，但缺的页面名由后面的链接补上。"""
    page = parse(
        "{{关卡链接|VEC-02|act3break 02}}"
        "{{关卡链接|VEC-01|act3break 01}}"
        "{{关卡链接|VEC-02|act3break 99}}"  # 同代号：第一条的 stageId 赢
        "[[VEC-01 寂静螺旋]]"  # 模板已经给了 stageId，只用来补页面名
    )
    assert page.codes == ("VEC-02", "VEC-01")
    assert page.stages == (
        StageRef(code="VEC-02", stage_id="act3break_02"),
        StageRef(code="VEC-01", stage_id="act3break_01", page_title="VEC-01 寂静螺旋"),
    )


def test_stage_refs_fill_page_title_from_body_link() -> None:
    """模板只给代号、正文链接给页面名时，页面名不能丢。"""
    page = parse("{{关卡链接|YW-1}}\n[[YW-1 昨日海之岸]]")
    assert page.stages == (StageRef(code="YW-1", page_title="YW-1 昨日海之岸"),)


@pytest.mark.parametrize(
    "content",
    [
        "[[2026-09-29 活动公告]]",  # 日期形状，不是关卡
        "[[YW-9]]",  # 没有中文名
        "[[act3break_01]]",  # stageId 不是代号
    ],
)
def test_body_links_that_are_not_stages_are_ignored(content: str) -> None:
    assert parse(content).codes == ()


@pytest.mark.parametrize(
    "content",
    [
        "{{关卡链接|2026}}",  # 纯数字
        "{{关卡链接|}}",  # 空代号
        "{{关卡链接|   }}",  # 只有空白
        "{{关卡链接|VEC-01 寂静螺旋}}",  # 代号里带空格
        "{{关卡链接|act3break_01}}",  # stageId 不是代号
        "{{关卡链接}}",  # 没有位置参数
    ],
)
def test_stage_link_skips_values_that_are_not_codes(content: str) -> None:
    assert parse(content).codes == ()


def test_stage_link_ignores_trailing_named_params() -> None:
    # 实测：后 5 关多了 style 命名参数，位置参数只有代号与 stageId
    page = parse("{{关卡链接|VEC-08|act3break 08|style=color:#fff;}}")
    assert page.stages == (StageRef(code="VEC-08", stage_id="act3break_08"),)


# --------------------------------------------------------------------------- #
# is_stage_code
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value", ["7-18", "VEC-01", "VEC-SP01", "YW-1", "YW-ST-1", "YW-EX-1", "1-7", "VEC-A"]
)
def test_is_stage_code_accepts_real_codes(value: str) -> None:
    assert is_stage_code(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "2026",  # 纯数字的年份不是代号
        "03",  # 纯数字
        "9",
        "",  # 空串
        "VEC-01 寂静螺旋",  # 带中文名
        "act3break_01",  # stageId：小写
        "main_07-16",  # levelId：小写
        "VEC-01-",  # 连字符后面没内容
        "VEC-01|VEC-02",
    ],
)
def test_is_stage_code_rejects_non_codes(value: str) -> None:
    assert is_stage_code(value) is False


# --------------------------------------------------------------------------- #
# parse_page_time
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-29 16:00", datetime(2026, 9, 29, 16, 0, tzinfo=CN_TIMEZONE)),
        ("2026-09-29T16:00", datetime(2026, 9, 29, 16, 0, tzinfo=CN_TIMEZONE)),
        ("  2026-09-29 16:00  ", datetime(2026, 9, 29, 16, 0, tzinfo=CN_TIMEZONE)),
        ("2026-9-9 8:05", datetime(2026, 9, 9, 8, 5, tzinfo=CN_TIMEZONE)),
        ("2026-09-29 16:00:30", datetime(2026, 9, 29, 16, 0, 30, tzinfo=CN_TIMEZONE)),
        ("2026-09-29 16:00:00", datetime(2026, 9, 29, 16, 0, 0, tzinfo=CN_TIMEZONE)),
        ("2026-09-29", datetime(2026, 9, 29, 0, 0, tzinfo=CN_TIMEZONE)),  # 只有日期
        ("2026-09-29 00:00", datetime(2026, 9, 29, 0, 0, tzinfo=CN_TIMEZONE)),
    ],
)
def test_parse_page_time_valid(value: str, expected: datetime) -> None:
    assert parse_page_time(value) == expected


def test_parse_page_time_uses_cn_timezone() -> None:
    parsed = parse_page_time("2026-09-29 16:00")
    assert parsed is not None
    assert parsed.tzinfo is CN_TIMEZONE
    assert parsed.utcoffset() == timedelta(hours=8)
    assert parsed.isoformat() == "2026-09-29T16:00:00+08:00"


@pytest.mark.parametrize(
    "value",
    [
        None,  # 缺时间
        "",
        "   ",
        "待定",
        "2026/09/29",
        "2026-09-29 16",  # 缺分钟
        "2026-09-29 16:00 +08:00",  # 带时区后缀（页面不这么写，保守拒绝）
        "2026-13-01 10:00",  # 月份越界
        "2026-02-30",  # 日期不存在
        "2026-09-29 24:00",  # 小时越界
        "2026-09-29 16:60",  # 分钟越界
        "2026-09-29 16:00:99",  # 秒越界
        20260929,  # 不是字符串
    ],
)
def test_parse_page_time_invalid(value: Any) -> None:
    assert parse_page_time(value) is None


# --------------------------------------------------------------------------- #
# 真实夹具：矢量突破/03（第二参数是 stageId）
# --------------------------------------------------------------------------- #


def test_fixture_vec03_activity_info(vec03: str) -> None:
    page = parse_activity_page(vec03, page_title=fixture_title("page_vec03.json"))
    assert page.page_title == "矢量突破/03"
    assert page.name == "矢量突破 #3"
    assert page.type == "矢量突破"
    assert page.start_at == datetime(2026, 9, 29, 16, 0, tzinfo=CN_TIMEZONE)
    assert page.end_at == datetime(2026, 10, 20, 3, 59, tzinfo=CN_TIMEZONE)
    assert page.exchange_end_at == datetime(2026, 10, 23, 3, 59, tzinfo=CN_TIMEZONE)


def test_fixture_vec03_full_code_table(vec03: str) -> None:
    page = parse(vec03)
    assert page.codes == (
        *[f"VEC-{index:02d}" for index in range(1, 13)],
        "VEC-A",
        "VEC-B",
        "VEC-C",
        "VEC-D",
        "VEC-SP01",
        "VEC-SP02",
        "VEC-SP03",
        "VEC-SP04",
        "VEC-SP05",
        "VEC-SP06",
        "VEC-SP07",
        "VEC-SP09",
        "VEC-SP10",
        "VEC-SP11",
        "VEC-SP12",
        "VEC-SP13",
        "VEC-SP14",
    )


def test_fixture_vec03_stage_ref_hints(vec03: str) -> None:
    page = parse(vec03)
    hints = {ref.code: (ref.stage_id, ref.page_title) for ref in page.stages}

    # 常规关：第二参数 "act3break 01" 归一成下划线
    assert hints["VEC-01"] == ("act3break_01", None)
    assert hints["VEC-12"] == ("act3break_12", None)
    # 高难关：代号在页面里写成 {{关卡链接|VEC-<big>'''A'''</big>|act3break h01}}
    assert hints["VEC-A"] == ("act3break_h01", None)
    assert hints["VEC-B"] == ("act3break_h02", None)
    assert hints["VEC-C"] == ("act3break_h03", None)
    assert hints["VEC-D"] == ("act3break_h04", None)
    # 特难关：第二参数是页面名，不是 stageId
    assert hints["VEC-SP01"] == (None, "VEC-SP01 重力危机")
    assert hints["VEC-SP07"] == (None, "VEC-SP07 荒废矿道")
    assert hints["VEC-SP14"] == (None, "VEC-SP14 乙醇暴动")


# --------------------------------------------------------------------------- #
# 真实夹具：昨日海（只给代号）
# --------------------------------------------------------------------------- #


def test_fixture_zuori_activity_info(zuori: str) -> None:
    page = parse_activity_page(zuori, page_title=fixture_title("page_zuori.json"))
    assert page.page_title == "昨日海"
    assert page.name == "昨日海"
    assert page.type == "支线故事"
    assert page.start_at == datetime(2026, 10, 9, 12, 0, tzinfo=CN_TIMEZONE)
    assert page.end_at == datetime(2026, 10, 30, 3, 59, tzinfo=CN_TIMEZONE)
    assert page.exchange_end_at == datetime(2026, 10, 30, 3, 59, tzinfo=CN_TIMEZONE)


def test_fixture_zuori_full_code_table(zuori: str) -> None:
    page = parse(zuori)
    assert page.codes == (
        "YW-ST-1",
        "YW-1",
        "YW-TR-1",
        "YW-2",
        "YW-3",
        "YW-4",
        "YW-ST-2",
        "YW-5",
        "YW-6",
        "YW-7",
        "YW-8",
        "YW-ST-3",
    )
    # 该页只给代号：两种提示都没有（页面正文里没有 [[代号 中文名]] 链接）
    assert page.stages == tuple(StageRef(code=code) for code in page.codes)
