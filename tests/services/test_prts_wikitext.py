"""M25 Task 4：PRTS wikitext 通用解析（规格 §5.5）的边界穷举。

纯函数测试：不打网、不读仓库以外的文件。真实活动页正文见
``tests/fixtures/prts/activity_vec03.wikitext``（矢量突破/03）与
``activity_zuori.wikitext``（昨日海），来源与抓取日期见同目录 README.md。

测试里出现的所有「wikitext」都是真实页面里出现过的写法（``{{color}}`` 套在链接显示名里、
``#vardefine`` 里套 ``{{mdi}}``、``活动里程碑2/style`` 与 ``活动里程碑2`` 的前缀干扰、
``<拟生态制衡奖章>`` 这种非 ASCII 装饰尖括号等）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maa_api.services.prts_parse.wikitext import (
    Section,
    Template,
    TemplateArg,
    find_templates,
    parse_templates,
    split_sections,
    strip_markup,
    template_param,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "prts"


@pytest.fixture(scope="module")
def vec03() -> str:
    """矢量突破/03 活动页正文（裁剪快照）。"""
    return (FIXTURES / "activity_vec03.wikitext").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def zuori() -> str:
    """昨日海活动页正文（裁剪快照）。"""
    return (FIXTURES / "activity_zuori.wikitext").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# parse_templates / find_templates / template_param
# --------------------------------------------------------------------------- #


def test_parse_templates_preserves_arg_order_and_kind() -> None:
    (template,) = parse_templates("{{a|1|k=v|2}}")
    assert template.name == "a"
    assert template.args == (
        TemplateArg(key=None, value="1"),
        TemplateArg(key="k", value="v"),
        TemplateArg(key=None, value="2"),
    )
    assert template.positional() == ("1", "2")
    assert template.param("k") == "v"
    assert template.param("缺") is None
    assert template.param("缺", "默认") == "默认"
    assert template.arg(0) == "1"
    assert template.arg(9) is None
    assert template.arg(9, "默认") == "默认"
    assert template.args[0].is_positional and not template.args[1].is_positional


def test_template_param_accepts_name_and_index() -> None:
    (template,) = parse_templates("{{a|甲|k=乙}}")
    assert template_param(template, "k") == "乙"
    assert template_param(template, 0) == "甲"
    assert template_param(template, "缺") is None
    assert template_param(template, "缺", "默认") == "默认"
    assert template_param(template, 5, "默认") == "默认"


def test_parse_templates_multiple_in_document_order() -> None:
    assert [t.name for t in parse_templates("x{{a|1}}y{{b|2}}z")] == ["a", "b"]
    assert [t.param("k") for t in parse_templates("{{a|k=1}}{{a|k=2}}")] == ["1", "2"]


def test_parse_templates_nested_keeps_raw_value_and_exposes_inner() -> None:
    text = "{{a|{{b|c}}}}"
    outer, inner = parse_templates(text)
    assert outer.name == "a" and inner.name == "b"
    # 外层参数值里是内层模板的**原文**，可以再喂给 parse_templates
    assert outer.args == (TemplateArg(key=None, value="{{b|c}}"),)
    assert parse_templates(outer.arg(0) or "") == [inner]
    assert inner.positional() == ("c",)
    # 内层同时能单独取到：扁平列表、Template.nested()、find_templates() 三条路
    assert outer.nested() == [inner]
    assert outer.nested("b") == [inner]
    assert outer.nested("缺") == []
    assert find_templates(text, "b") == [inner]
    assert parse_templates(text, include_nested=False) == [outer]
    assert find_templates(text, "b", include_nested=False) == []


def test_parse_templates_deep_nesting() -> None:
    templates = parse_templates("{{a|{{b|{{c|1}}}}}}")
    assert [t.name for t in templates] == ["a", "b", "c"]
    assert templates[-1].positional() == ("1",)
    # 内层模板的值保持原样，链条可以逐层往下取
    assert templates[0].nested("c") == [templates[2]]


def test_parse_templates_pipe_inside_link_is_not_a_separator() -> None:
    (template,) = parse_templates("{{a|[[页面|显示名]]|尾}}")
    assert template.positional() == ("[[页面|显示名]]", "尾")


def test_parse_templates_equals_inside_nested_template_is_not_a_key() -> None:
    template = parse_templates("{{#vardefine:trimallow|{{mdi|star-box|color=#2a4e97}}}}")[0]
    assert template.name == "#vardefine:trimallow"
    assert template.args == (
        TemplateArg(key=None, value="{{mdi|star-box|color=#2a4e97}}"),
    )
    assert template.nested("mdi")[0].param("color") == "#2a4e97"


def test_parse_templates_missing_and_empty_args() -> None:
    assert parse_templates("{{a}}") == [Template(name="a")]
    (empty,) = parse_templates("{{a|}}")
    assert empty.args == (TemplateArg(key=None, value=""),)
    (named_empty,) = parse_templates("{{a|k=}}")
    assert named_empty.param("k") == ""
    (trailing,) = parse_templates("{{a|1||2}}")
    assert trailing.positional() == ("1", "", "2")


def test_parse_templates_empty_key_falls_back_to_positional() -> None:
    # MediaWiki 的旧行为：空键不算命名参数，整段（含 =）是位置参数
    (template,) = parse_templates("{{a|=x|k=2}}")
    assert template.args == (TemplateArg(key=None, value="=x"), TemplateArg(key="k", value="2"))


def test_parse_templates_strips_name_and_arg_whitespace() -> None:
    (template,) = parse_templates("{{ 活动信息\n| 名称缩短 = 昨日海 \n|参加条件关卡=[[1-10|主线关卡1-10]]\n|类型=支线故事 }}")
    assert template.name == "活动信息"
    assert template.param("名称缩短") == "昨日海"
    assert template.param("参加条件关卡") == "[[1-10|主线关卡1-10]]"
    assert template.args[0] == TemplateArg(key="名称缩短", value="昨日海")


def test_parse_templates_name_keeps_inner_whitespace() -> None:
    assert [t.name for t in parse_templates("{{a b|1}}{{a  b|1}}")] == ["a b", "a  b"]


@pytest.mark.parametrize(
    "text",
    [
        "{{{1|默认值}}}",
        "a{{{b}}}c",
        "值：{{{未闭合",
        "{{",
        "{{a|没闭合",
        "{{a|}}}}}",
    ],
)
def test_parse_templates_triple_braces_and_unbalanced_do_not_raise(text: str) -> None:
    # 三花括号当普通文本，不炸；未闭合的双花括号也不炸（宁少不误）
    templates = parse_templates(text)
    assert all(isinstance(t, Template) for t in templates)


def test_parse_templates_finds_templates_inside_triple_braces() -> None:
    # {{{ 当普通文本，但花括号里面的真模板照样解析（默认值里的调用）
    assert [t.name for t in parse_templates("{{{1|{{color|#fff|文字}}}}}")] == ["color"]


def test_parse_templates_ignores_html_comments() -> None:
    text = "a<!-- {{关卡链接|VEC-SP01}} -->b{{关卡链接|VEC-01}}"
    assert [t.name for t in parse_templates(text)] == ["关卡链接"]
    assert parse_templates(text)[0].positional() == ("VEC-01",)
    assert parse_templates("{{a|<!--{{b}}-->c}}") == [
        Template(name="a", args=(TemplateArg(key=None, value="<!--{{b}}-->c"),))
    ]


def test_parse_templates_plain_text_and_empty() -> None:
    assert parse_templates("") == []
    assert parse_templates("没有任何模板，只有 [[链接]] 和 <ref>脚注</ref>") == []


def test_find_templates_exact_name_and_whitespace_normalised() -> None:
    text = "{{活动里程碑2|1}}{{活动里程碑2/style|bg=x}}{{活动里程碑2|2}}{{活动里程碑}}"
    assert [t.param("exp") for t in find_templates(text, "活动里程碑2")] == [None, None]
    assert [t.positional() for t in find_templates(text, "活动里程碑2")] == [("1",), ("2",)]
    assert find_templates(text, " 活动里程碑2 ") == find_templates(text, "活动里程碑2")
    assert find_templates(text, "活动里程碑2/style")[0].param("bg") == "x"
    assert find_templates(text, "活动") == []  # 前缀/子串不算命中
    assert find_templates("", "a") == []


# --------------------------------------------------------------------------- #
# strip_markup：链接
# --------------------------------------------------------------------------- #


def test_strip_markup_links() -> None:
    assert strip_markup("[[页面|显示名]]") == "显示名"
    assert strip_markup("[[页面]]") == "页面"
    assert strip_markup("①[[塑能基质|塑能基质]]是核心物质") == "①塑能基质是核心物质"
    assert strip_markup("[[寻访模拟/海渊巡游|海渊巡游]]") == "海渊巡游"
    assert strip_markup("[[A|B|C]]") == "C"
    assert strip_markup("[[A|]]") == "A"
    assert strip_markup("[[情报处理室#昨日海|本次活动的剧情]]") == "本次活动的剧情"


def test_strip_markup_non_text_namespace_links_are_dropped() -> None:
    assert strip_markup("[[分类:存在蚀刻章备注的活动]]") == ""
    assert strip_markup("[[文件:图标 矢量突破-3.png|x64px|link=]]后续") == "后续"
    assert strip_markup("[[Category:X]]·[[File:y.png|40px]]·[[特殊:最近更改]]") == "··"


def test_strip_markup_link_label_containing_template() -> None:
    assert strip_markup("[[塑能基质|{{color|#b4e4ff|塑能基质}}]]") == "塑能基质"
    assert strip_markup("{{color|#b4e4ff|[[塑能基质|塑能基质]]}}") == "塑能基质"


# --------------------------------------------------------------------------- #
# strip_markup：模板
# --------------------------------------------------------------------------- #


def test_strip_markup_display_templates_take_their_text() -> None:
    assert strip_markup("{{color|#b4e4ff|无线供能}}") == "无线供能"
    assert strip_markup("{{color|darkgrey|NO NEED TO WORRY!}}") == "NO NEED TO WORRY!"
    assert strip_markup("{{异常效果|眩晕}}") == "眩晕"
    assert strip_markup("{{术语|阻挡|阻挡}}") == "阻挡"
    assert strip_markup("{{变动数值|+10%}}") == "+10%"
    assert strip_markup("{{变动数值lite|up|蓝|-50%}}") == "-50%"
    assert strip_markup("{{mdi|star-box|color=#2a4e97}}") == "star-box"
    assert strip_markup("{{#var:x|默认值}}") == "默认值"
    assert strip_markup("{{#show:7-18 爱国者之死|?关卡id}}") == "?关卡id"


def test_strip_markup_unknown_template_takes_last_positional() -> None:
    assert strip_markup("{{自定义|第一|第二|命名=第三}}") == "第二"
    assert strip_markup("{{自定义|命名=第三}}") == ""
    assert strip_markup("{{自定义}}") == ""
    # 位置参数里的模板先解析，再取最后一个
    assert strip_markup("{{自定义|甲|{{color|#fff|乙}}}}") == "乙"


def test_strip_markup_stage_link_shows_the_code() -> None:
    # 裁决：{{关卡链接|代号|stageId}} 的展示文本是第一个位置参数（页面上显示代号），
    # 取最后一个会得到 act3break 01 这种机器名
    assert strip_markup("{{关卡链接|VEC-01|act3break 01}}") == "VEC-01"
    assert strip_markup("{{关卡链接|YW-1}}") == "YW-1"
    assert strip_markup("{{关卡链接|VEC-08|act3break 08|style=color:#fff;}}") == "VEC-08"


def test_strip_markup_silent_parser_functions_emit_nothing() -> None:
    assert strip_markup("{{#vardefine:trimallow|{{mdi|star-box|color=#fff}}}}") == ""
    assert strip_markup("{{#widget:style|style=a{b:c};}}") == ""
    assert strip_markup("{{#Widget:倒计时/jsmini}}") == ""
    assert strip_markup("{{#invoke:MusicTable|table|标题1=A|标题2=B}}") == ""
    # 变量名本身不是展示文本
    assert strip_markup("{{#var:trimallow}}") == ""


def test_strip_markup_nested_display_templates() -> None:
    assert strip_markup("{{color|#fff|{{color|#000|文字}}}}") == "文字"
    assert strip_markup("前{{异常效果|{{术语|眩晕|眩晕}}}}后") == "前眩晕后"


def test_strip_markup_template_outside_known_list_keeps_text() -> None:
    assert strip_markup("{{活动信息|名称=昨日海|类型=支线故事}}") == ""
    assert strip_markup("{{参阅|情报处理室#昨日海|本次活动的剧情}}") == "本次活动的剧情"


# --------------------------------------------------------------------------- #
# strip_markup：标签、注释与空白
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("br", ["<br>", "<br/>", "<br />", "<BR>", "<br  />"])
def test_strip_markup_normalises_br(br: str) -> None:
    assert strip_markup(f"上行{br}下行") == "上行\n下行"


def test_strip_markup_removes_refs_and_comments() -> None:
    assert strip_markup("正文<ref>来源{{cite}}</ref>后续") == "正文后续"
    assert strip_markup('正文<ref name="a" />后续') == "正文后续"
    assert strip_markup("<references/>正文") == "正文"
    assert strip_markup("<references>正文") == "正文"
    assert strip_markup("前<!-- 注释 {{未闭合 -->后") == "前后"
    assert strip_markup("前<!--\n== 假标题 ==\n{{未闭合\n-->后") == "前后"


def test_strip_markup_removes_other_tags_but_keeps_text() -> None:
    assert strip_markup('<span class="x-small">10/9 3:59 止</span>') == "10/9 3:59 止"
    assert strip_markup("<center>{{矢量突破导航}}</center>") == ""
    assert strip_markup('<section begin="enemyinfo" />正文<section end="enemyinfo" />') == "正文"
    assert strip_markup("<ul><li>第一条</li></ul>") == "第一条"


def test_strip_markup_keeps_non_ascii_decoration_angles() -> None:
    # <拟生态制衡奖章> 不是 HTML 标签（浏览器也只当普通字符），必须原样保留
    text = "※蚀刻章<拟生态制衡奖章>可通过失败的作战积累进度。"
    assert strip_markup(text) == text


def test_strip_markup_entities_and_emphasis() -> None:
    assert strip_markup("前缀&nbsp;后缀") == "前缀 后缀"
    assert strip_markup("A&amp;B") == "A&B"
    assert strip_markup("累积经验'''(+升至此级所需经验)'''") == "累积经验(+升至此级所需经验)"
    assert strip_markup("''斜体''正文") == "斜体正文"


def test_strip_markup_collapses_blank_lines_and_trims() -> None:
    assert strip_markup("  {{color|#fff|文字}}  ") == "文字"
    assert strip_markup("a<br><br><br>b") == "a\n\nb"
    assert strip_markup("a  <br>b") == "a\nb"
    assert strip_markup("") == ""
    assert strip_markup("{{#widget:x|y}}") == ""


def test_strip_markup_triple_braces_are_literal() -> None:
    assert strip_markup("值：{{{1|默认}}}") == "值：{{{1|默认}}}"
    # 花括号是普通文本，但里面的真模板照常解析
    assert strip_markup("{{{1|{{color|#fff|文字}}}}}") == "{{{1|文字}}}"


def test_strip_markup_unbalanced_template_does_not_raise() -> None:
    assert strip_markup("{{a|没闭合") == "{{a|没闭合"


# --------------------------------------------------------------------------- #
# split_sections
# --------------------------------------------------------------------------- #


def test_split_sections_levels_preamble_and_bodies() -> None:
    text = "前言一行\n\n==第一节==\n正文一\n===第二节===\n正文二\n=====深度=====\n正文三\n==末节==\n正文四\n"
    sections = split_sections(text)
    assert [(s.level, s.title) for s in sections] == [
        (0, ""),
        (1, "第一节"),
        (2, "第二节"),
        (4, "深度"),
        (1, "末节"),
    ]
    assert sections[0].body == "前言一行"
    assert sections[1].body == "正文一"
    assert sections[2].body == "正文二"
    assert sections[3].body == "正文三"
    assert sections[4].body == "正文四"
    assert sections[1] == Section(level=1, title="第一节", body="正文一")


def test_split_sections_body_excludes_heading_line() -> None:
    sections = split_sections("==A==\n\n正文\n\n===B===\n")
    assert sections[1].body == "正文"
    assert "A" not in sections[1].body.split("\n")[0]
    assert sections[2].body == ""


def test_split_sections_tolerates_whitespace_in_heading() -> None:
    sections = split_sections("== 关卡信息 ==\n正文\n")
    assert [(s.level, s.title) for s in sections] == [(0, ""), (1, "关卡信息")]


def test_split_sections_ignores_headings_inside_comments_and_inline_text() -> None:
    text = "<!--\n== 注释里的标题 ==\n-->\n==真标题==\n=== 不是标题 == x\n正文\n"
    assert [(s.level, s.title) for s in split_sections(text)] == [(0, ""), (1, "真标题")]


def test_split_sections_without_headings_is_one_preamble() -> None:
    assert split_sections("只有正文\n第二行") == [Section(level=0, title="", body="只有正文\n第二行")]


def test_split_sections_empty_page() -> None:
    assert split_sections("") == [Section(level=0, title="", body="")]


def test_redirect_only_page() -> None:
    text = "#REDIRECT [[7-18 爱国者之死]]\n[[分类:重定向]]"
    assert parse_templates(text) == []
    sections = split_sections(text)
    assert [(s.level, s.title) for s in sections] == [(0, "")]
    assert sections[0].body.startswith("#REDIRECT")
    assert strip_markup(text) == "#REDIRECT 7-18 爱国者之死"


# --------------------------------------------------------------------------- #
# 真实活动页冒烟（矢量突破/03、昨日海）
# --------------------------------------------------------------------------- #


def test_fixture_vec03_activity_info(vec03: str) -> None:
    infos = find_templates(vec03, "活动信息")
    assert len(infos) == 1
    info = infos[0]
    assert info.param("类型") == "矢量突破"
    assert info.param("名称缩短") == "矢量突破 #3"
    assert info.param("活动开始时间") == "2026-09-29 16:00"
    assert info.param("活动结束时间") == "2026-10-20 03:59"
    assert info.param("参加条件关卡") == "[[2-10|主线关卡2-10]]"
    assert info.param("不存在") is None
    assert strip_markup(info.param("参加条件关卡") or "") == "主线关卡2-10"


def test_fixture_vec03_stage_links(vec03: str) -> None:
    links = find_templates(vec03, "关卡链接")
    assert [t.positional()[0] for t in links] == [f"VEC-{i:02d}" for i in range(1, 13)]
    assert [t.positional()[1] for t in links] == [f"act3break {i:02d}" for i in range(1, 13)]
    # 后 5 关多了 style 命名参数，位置参数不受影响
    assert links[0].param("style") is None and len(links[0].args) == 2
    assert links[7].param("style") == "color:#fff;" and len(links[7].args) == 3
    # 注释掉的表格块（夹具已裁掉）与 #invoke 里的同名模板都不该混进来
    assert all(t.name == "关卡链接" for t in links)


def test_fixture_vec03_milestones_and_nested_icons(vec03: str) -> None:
    # 6 处出现，但 5 个是模板本体，1 个是 {{活动里程碑2/style}}（精确匹配不认前缀）
    milestones = find_templates(vec03, "活动里程碑2")
    assert len(milestones) == 5
    assert [t.param("exp") for t in milestones] == ["40", "40", "40", "40", "40"]
    assert milestones[0].positional() == ("1", "龙门币", "10000", "{{道具图标|龙门币||48px}}")
    icons = milestones[0].nested("道具图标")
    assert len(icons) == 1
    assert icons[0].positional() == ("龙门币", "", "48px")
    assert find_templates(vec03, "活动里程碑2/style")[0].param("bg", "").startswith("linear-gradient")


def test_fixture_vec03_sections(vec03: str) -> None:
    sections = split_sections(vec03)
    assert [(s.level, s.title) for s in sections] == [
        (0, ""),
        (1, "关卡信息"),
        (1, "突破里程碑"),
        (1, "蚀刻章套组"),
        (1, "音乐鉴赏"),
    ]
    assert "{{活动信息" in sections[0].body
    assert "VEC-12" in sections[1].body
    assert "VEC-12" not in sections[2].body
    assert "{{蚀刻章展示|medalgroup=medalGroupActivityAct3break}}" in sections[3].body
    assert "#invoke:MusicTable" in sections[4].body


def test_fixture_vec03_strip_markup(vec03: str) -> None:
    text = strip_markup(vec03)
    assert "{{" not in text and "}}" not in text
    assert "[[" not in text and "]]" not in text
    assert "<!--" not in text and "-->" not in text
    assert "裁剪快照" not in text  # 夹具横幅是 HTML 注释，一并剥掉
    assert "<br" not in text
    assert "\n\n\n" not in text
    assert "VEC-01" in text
    assert "NO NEED TO WORRY!" in text
    assert "Neo-vector" not in text  # {{#invoke:MusicTable|…}} 的命名参数不是正文
    assert "蚀刻章<拟生态制衡奖章>可通过失败的作战积累进度。" in text
    assert "分类:存在蚀刻章备注的活动" not in text


def test_fixture_zuori_activity_info(zuori: str) -> None:
    infos = find_templates(zuori, "活动信息")
    assert len(infos) == 1
    info = infos[0]
    assert info.param("类型") == "支线故事"
    assert info.param("名称") == "SideStory「昨日海」"
    assert info.param("名称缩短") == "昨日海"
    assert info.param("活动开始时间") == "2026-10-09 12:00"
    assert info.param("活动结束时间") == "2026-10-30 03:59"
    assert info.param("理智本消耗") == "21"
    assert info.param("引用") == "yes"


def test_fixture_zuori_stage_links_have_only_the_code(zuori: str) -> None:
    links = find_templates(zuori, "关卡链接")
    assert [t.positional() for t in links] == [("YW-ST-1",), ("YW-1",), ("YW-TR-1",), ("YW-2",)]
    assert all(t.param("style") is None for t in links)


def test_fixture_zuori_environment_text(zuori: str) -> None:
    (enemy,) = find_templates(zuori, "登场敌人")
    raw = enemy.param("环境信息")
    assert raw is not None
    assert "<br>" in raw and "{{color|" in raw
    assert "[[能量束发射台|{{color|#b4e4ff|能量束发射台}}]]" in raw

    text = strip_markup(raw)
    assert text.startswith("作战场地（暂名）：伊比利亚 重浮港都\n")
    assert "借由阿戈尔技术对各类器械无线供能的景象" in text
    assert "\n①塑能基质是炼金术师操作各类器械的核心物质" in text
    assert "能量束发射台以向前发射一段距离的能量束" in text
    assert "{{" not in text and "}}" not in text
    assert "[[" not in text and "]]" not in text
    assert "<br" not in text
    assert "\n\n" not in text
    assert text.count("\n") == raw.count("<br>")


def test_fixture_zuori_sections(zuori: str) -> None:
    sections = split_sections(zuori)
    assert [(s.level, s.title) for s in sections] == [
        (0, ""),
        (1, "关卡信息"),
        (1, "打捞方案"),
        (1, "劳工配给所"),
        (1, "蚀刻章套组"),
        (1, "活动剧情"),
        (1, "音乐鉴赏"),
    ]
    assert "{{活动信息" in sections[0].body
    assert "登场敌人" in sections[1].body
    assert "活动信赖获取提升干员" in sections[1].body
    assert "==打捞方案==" not in sections[1].body
    assert sections[2].body == ""
    assert "{{参阅|情报处理室#昨日海|本次活动的剧情}}" in sections[5].body


def test_fixture_zuori_strip_markup(zuori: str) -> None:
    text = strip_markup(zuori)
    assert "{{" not in text and "}}" not in text
    assert "[[" not in text and "]]" not in text
    assert "分类:" not in text and "文件:" not in text
    assert "MedalShowcase" not in text and "#invoke" not in text
    assert "本次活动的剧情" in text
    assert "音乐鉴赏" in text
    assert "YW-ST-1" in text
    assert "作战场地" not in text  # 环境信息是命名参数，正文里不该出现
