"""M30 T4：「无数值」检查测试（规格 §10.3、§10.1）。

规格 ``docs/superpowers/specs/2026-10-09-m25-prts-knowledge.md`` §10.1 把内置知识条目的
数值口径定成「数值删掉，换成工具或 PRTS 页面指引」：条目里只留**去哪查、查什么、怎么
判断**，不留具体游戏数值。本测试用正则守住这一点：正文里凡是「数字 + 游戏单位」的组合
（``135 理智``、``600 合成玉``、``30%``、``7 天``…）或裸百分比都算命中，只有在白名单里的
操作类数字才豁免。

白名单的来源与逐条理由写在 :data:`WHITELIST` 每条规则的注释里；豁免的判据是「命中片段
整个落在某条白名单区间内」，所以白名单可以写得**窄**：写窄了顶多多报几条假阳性（那时
再把常驻白名单放宽），写宽了会放过真数值。

除扫描外还有两组反向用例，证明检查不是永真：

- :func:`test_reverse_samples_are_caught`：把 T1 复核报告 ``disposition-*.json`` 里
  ``numbers_to_remove`` 的**真实片段**拼进假条目正文，逐条断言被扫出来；
- :func:`test_handwritten_reverse_samples_are_caught`：任务书点名形态的手写反例；
- :func:`test_reverse_fragments_are_not_whitelisted`：同一批片段不被任何白名单整段豁免。

最后是 `render_context()` 索引行的用例（规格 §10.3 验收第 2 条）。

本目录没有 pytest-asyncio（不要为此加插件），异步用例写成「同步测试函数 +
``asyncio.run(scenario())``」。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from maa_api.agent.knowledge import (
    INDEX_HEADER,
    KnowledgeService,
    builtin_entries,
    load_builtins,
)
from maa_api.db import session as db_session
from maa_api.domain.errors import AppError, ErrorCode

REPO_ROOT = Path(__file__).resolve().parents[2]
SDD_DIR = REPO_ROOT / ".superpowers" / "sdd" / "2026-10-10-m30-knowledge-convergence"

# 33 个内置条目（M30 T1 复核时的 32 条 + M32 新增的 task-preconditions）；
# 数量对不上说明条目被增删，扫描口径要重核。
EXPECTED_ENTRY_COUNT = 33
FULLTEXT_ENTRY_COUNT = 2  # glossary 与 glossary-terms：全文注入，不占索引行

# 反例来源：两份复核报告的逐条处置表。仓库内只留 `id` 与 `numbers_to_remove` 两列的快照
# （`tests/fixtures/knowledge/m30_disposition.json`，见同目录 README）；SDD 工作区是
# gitignore 的临时目录，只在夹具缺失时当回退读，免得清理工作区把这条反向用例一起带走。
DISPOSITION_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "knowledge" / "m30_disposition.json"
DISPOSITION_FILES = ("disposition-a.json", "disposition-b.json")


# ---------------------------------------------------------------------------
# 数字与单位
# ---------------------------------------------------------------------------
# 「数字」：阿拉伯数字、全角数字、千分位、小数、区间（~ / ～ / - / － / —）。
# 千分位用 `(,[0-9]{3})*` 而不是「数字与逗号随便排」：后者会让 `15/30/50/80 笔` 里的
# 逗号把整串吞掉，反而是漏报。
NUMBER = (
    r"(?:(?<![\d０-９])[0-9０-９](?:[0-9０-９]*[0-9０-９])?(?:,[0-9０-９]{3})*)"
    r"(?:\.[0-9０-９]+)?"
    r"(?:(?:\s*(?:~|～|-|－|—)\s*)[0-9０-９]+(?:\.[0-9０-９]+)?)*"
)

# 「游戏单位」：规格 §10.3 的判据。单位要贴着一个数字（中间只允许空白或顿号/斜杠/叉号
# 之类的枚举分隔符），中间夹了汉字就不算——`135 点理智` 这类会漏报，宁可漏报。
UNIT_WORDS = (
    # 资源与货币
    r"理智|合成玉|至纯源石|源石|龙门币|凭证|信用|经验|电力|心情|氛围值"
    # 百分比
    r"|百分比"
    # 时长
    r"|分钟|小时|天|周|月"
    # 上限与次数
    r"|上限|次数上限"
    # 抽数：headhunt-rules / glossary-terms 里的「前 10 抽必出 5★」是保底档位，属数值。
    # 只加「抽」，「次」「笔」「名」「级」一概不加——它们太常出现在工具参数与机制描述里
    # （`一次流水线 1~32 个任务`、`再获得 5 次即可满潜`），加进去会把机制计数也算命中。
    r"|抽"
)
_SEP = r"[\s、，,×xX/／和与或~～\-－—+＋]*"
_UNIT = rf"(?:{UNIT_WORDS})"

# 命中判据（两个方向都算）：
# - 「数字 + 单位」：`135 理智`、`3000 合成玉/周`、`20/40/80 名…恢复心情` 里的那个数字；
# - 「单位 + 数字」：`上限 1800`、`每周 1800`、`至纯源石 ×2`、`龙门币 3000`；
# - 裸百分比：`30%`、`+2%`、`掉率 2.00%`。
# 单位表见上：`次`/`笔`/`名`/`级` 故意不在里面，避免把工具参数与机制计数也算命中。
NUMBER_UNIT = re.compile(
    rf"{NUMBER}{_SEP}{_UNIT}"
    rf"|{_UNIT}{_SEP}{NUMBER}"
    rf"|[0-9０-９]+(?:\.[0-9０-９]+)?\s*%"
)
# 候选生成分两层，这是本扫描器的关键设计：
# - **判定层**（CORE_PATTERNS）决定什么是「数字 + 游戏单位」——只有它们才可能成为命中。
#   触发 = `NUMBER_UNIT`（带单位）∪ 界面尺寸（叉号即单位）∪ 等级序号（`级` 是后缀）。
# - **观察层**（_WIDE_CANDIDATE）在判定层之外还认裸数字、比例、时间、版本号、日期、连字符
#   id，它们本身**不是**命中判据，只是为了让白名单区间有东西可豁免——白名单规则若从不
#   生效就是死代码，用例 test_whitelist_rules_actually_exempt_something_in_the_builtins
#   靠这一层来统计各规则的豁免次数。
UI_DIMENSION = re.compile(r"\d+\s*[×xX]\s*\d+")
LEVEL_NUMBER = re.compile(
    rf"(?:{_UNIT}{_SEP})?[0-9０-９]+\s*级(?!\d)|级\s*[0-9０-９]+"
)
CORE_PATTERNS: tuple[re.Pattern[str], ...] = (NUMBER_UNIT, UI_DIMENSION, LEVEL_NUMBER)
# 观察层宽匹配（时间放最前，避免 `9:00:00` 被拆成 `9`/`00`/`00` 三段）。
_WIDE_CANDIDATE = re.compile(
    rf"\d{{1,2}}:\d{{2}}(?::\d{{2}})?"
    rf"|{NUMBER}(?:\s*%)?"
    rf"|\d+\s*[×xX]\s*\d+"
    rf"|\d+\s*级"
    rf"|级\s*\d+"
    rf"|\b[a-z][a-z0-9]*(?:-[a-z0-9]+)+\b"
    rf"|\bv\d+(?:\.\d+)*"
)
# 反例取样判据：片段必须以「数字 + 单位」或裸百分比**收尾**。复核报告的
# `numbers_to_remove` 是描述性短语（「公开招募 10/20 次」「让 20/40/80 名干员在宿舍恢复
# 心情」），用宽松判据会把它们当反例收进来，可它们本来就不该被扫描器命中。
SNIPPET_PREDICATE = re.compile(
    rf"(?:{NUMBER}{_SEP})?[0-9０-９]+(?:\.[0-9０-９]+)?\s*{_UNIT}\s*$"
    rf"|[0-9０-９]+(?:\.[0-9０-９]+)?\s*%\s*$"
)
# 反例里不许出现的注释性成分。
SNIPPET_REJECT = ("→", "（", "）", "；", "：", "删", "去掉", "改成", "换成")


# ---------------------------------------------------------------------------
# 白名单
# ---------------------------------------------------------------------------
_MARKER_MATCH = re.compile(r"^\s*(?:#{1,6}\s+|\d+[.、)]\s+|[-*+>]\s+)")


def _rule(pid: str, pattern: str, why: str) -> tuple[str, re.Pattern[str], str]:
    return pid, re.compile(pattern), why


# 界面比例与坐标（白名单第 b 条）。单独提出来是因为 `_scan_line` 要拿它算豁免区间。
UI_RATIO = re.compile(
    r"[横纵]向\s*\d+(?:\.\d+)?\s*%(?:\s*[~～\-－—–]\s*\d+(?:\.\d+)?\s*%)?"
    r"|(?:\d+\s*%)?\s*[~～\-－—–]\s*\d+\s*%\s*的?(?:比例|坐标|位置)"
    r"|\d+\s*%\s*的(?:比例|坐标|位置)"
    r"|[宽高]\s*\d+\s*%"
    r"|\d+\s*[×xX]\s*\d+"
)

WHITELIST: tuple[tuple[str, re.Pattern[str], str], ...] = (
    _rule(
        "ui_dimension",
        r"\d+\s*[×xX]\s*\d+",
        "界面尺寸：`669×231` 这类「宽×高」是 UI 图片/区域的像素尺寸，不是游戏数值，"
        "删掉就没法核对截图（T1-b §5 风险 1）。",
    ),
    _rule(
        "ui_ratio",
        UI_RATIO.pattern,
        "界面比例与坐标：screen-* 用「约占横向 50%–93%、纵向 4%–14%」「横向 93%、纵向 89%」"
        "描述可点区域，是操作坐标而非游戏数值（T1-b §5 风险 1 明确要求豁免）。判据就是"
        "任务书写的「界面描述语境」：必须带 `横向/纵向/宽/高` 或 `的比例/的坐标`；裸 `30%`、"
        "`攻击力 120%`、`掉率 2.00%` 一律不豁免。",
    ),
    _rule(
        "refresh_time",
        r"每日?\s*\d{1,2}:\d{2}|\d{1,2}:\d{2}",
        "界面刷新时刻：`凌晨 4:00`、`每日 4:00 与 16:00`、`每周一 4:00` 是服务端日常/周常的"
        "刷新时刻，属于操作参数（T1-b §5 风险 1 第 b 条）。",
    ),
    _rule(
        "operation_delay",
        r"(?:约|大约|等|等待|延迟|延时|间隔)\s*\d+\s*(?:~|～|-|－|—)?\s*\d*\s*(?:秒|毫秒|s\b|S\b)",
        "操作延时：「进入后等约 1~2 秒再截图」是点按节奏，不是游戏数值（T1-b §5 风险 1）。",
    ),
    _rule(
        "backtick_span",
        r"`[^`\n]*\d[^`\n]*`",
        "行内代码：backtick 包起来的一律是工具名、字段名、条目 id 或参数值"
        "（`get_prts_page`、`threshold`、`series`、`mode`、`medicine`、`plan_index`、"
        "`screen-home`、`times=0`）。这是本仓的 Markdown 约定：正文数值写进自然语言，"
        "不会进 backtick；真·游戏数值（`135 理智`、`600 合成玉`）因此仍会被扫到。",
    ),
    _rule(
        "kernel_parameter",
        r"(?:取值|默认|参数|阈值|本服务)\s*[^。；\n]{0,12}?\d",
        "内核与服务参数默认值/参数域：`series` 的 -1/0/1~6、`times` 默认 0、`threshold` 取值"
        " 0~1、`mode` 0/10000/20000、`timeout_seconds`、一次流水线 1~32 个任务（T1-b §5 风险 1"
        " 第 c/d 条）。删了这些，工具就没法用。",
    ),
    _rule(
        "command_parameter",
        r"[\w-]+=(?:\d+|True|False|None)",
        "工具参数键值对：`include_closed=True`、`times=0`、`plan_index=0`。",
    ),
    _rule(
        "recruit_time_slot",
        r"\d{1,2}:\d{2}:\d{2}",
        "MAA 操作参数里的固定招募时长（Recruit 固定填 `9:00:00`，T1-a §5 第 6 条豁免它）。"
        "注意「时长档位 → 稀有度下限」那张表 T1-a §5 明确**不豁免**：`1:00:00` 也不在白名单"
        "里（除非它出现在 backtick 参数里）。",
    ),
    _rule(
        "rarity_or_stage",
        r"(?<![\d.])[1-6]\s*(?:~|～|-|－|—)\s*[1-6]\s*(?:★|星|级)"
        r"|(?<![\d.])[1-6]\s*★"
        r"|(?<![\d.])[1-6]\s*星(?:通关|评价|都算|及)?"
        r"|精英?阶段\s*[0-2](?:\s*[/、]\s*[0-2])*"
        r"|(?<!专)精\s*[0-2一二三](?:\s*\d{1,3}\s*级)?"
        r"|精\s*[0-2一二三]\s*(?:上限|等级上限)"
        r"|专\s*[一二三]"
        r"|(?:技能|技能等级|等级|模组等级|模组)\s*[0-9一二三](?!\d)(?:\s*[/、]\s*[0-9一二三](?!\d))*(?:\s*级)?(?:\s*[和与、]\s*[0-9一二三](?!\d)(?:\s*级)?)*"
        r"|专精\s*[0-9一二三](?:\s*[/、]\s*[0-9一二三])*(?:\s*级)?"
        r"|Rank\s*[0-9]"
        r"|Lv\.?\s*N",
        "星级/稀有度/精英阶段/技能等级序号：`1★–6★`、`6★`、`3 星通关`、`精英阶段 0 / 1 / 2`、"
        "`精一 / 精二`、`专一~专三`、`技能 1/2/3`、`Lv.` 后跟符号占位。它们是分类标签而非数值；"
        "T1-a §5 把 `★` 列成待裁决项，这里按「豁免」处理，否则 certificate-shops、"
        "operator-classes、operator-progression、headhunt-rules 会大面积误报且不可读。",
    ),
    _rule(
        "level_transition",
        r"[等]级(?:回到|为|是|达到|升到)\s*[0-9０-９]+\s*级"
        r"|从\s*[0-9０-９]+\s*级(?:拉到|练到|升到|养到)"
        r"|潜能\s*[1-6](?:[、,，/]\s*[1-6])*"
        r"|(?:提升|增加|强化|解锁)\s*[0-9０-９]+\s*级潜能"
        r"|(?:专属)?模组[^。；\n]{0,6}?[0-9０-９]+\s*级(?:[^。；\n]{0,12}级)?"
        r"|(?:(?:[0-9０-９]+\s*级)?\s*[和与、]\s*)?[0-9０-９]+\s*级"
        r"|在\s*[0-9０-９]+\s*级基础上"
        r"|精\s*[0-2一二三]\s*(?:上限|等级上限)",
        "等级/潜能/模组的**阶段序号**（T1-a §5 第 2 条）：`精英化后等级回到 1 级`、"
        "`提升 1 级潜能`、`【专属模组】1 级只修改分支特性`、`2 级和 3 级在 1 级基础上强化天赋`。"
        "它们是「精英化会重置等级」这类机制说明的组成部分，不是要删的数值。"
        "注意这条只吃紧贴阶段名的 `N 级`：`需要的经验 2000`、`每周 1800 合成玉` 仍然是命中。",
    ),
    _rule(
        "stage_code",
        r"[A-Za-z]{1,6}[-－—]\d{1,2}(?![0-9A-Za-z])"
        r"|\d{1,2}[-－—]\d{1,2}(?![0-9A-Za-z])"
        r"|<[^>]*>[0-9]*"
        r"|\b[A-Z]\d\b"
        r"|\b\d[A-Z]\b",
        "关卡代号与内部 id：`1-7`、`CE-6`、`main_07-16`、`act25side_08`、`B1/B2/B3`、`1F`、"
        "`XX-01`（T1-a §5 第 3 条）。它们是查询键，改掉就查不到关。",
    ),
    _rule(
        "version_and_date",
        r"v\d+(?:\.\d+)*|\d{4}-\d{2}-\d{2}",
        "版本号与出处日期：`v6.8.0`、`v6.17.5`、`2020-06-09 修订`，说明「这页是哪一版对过的」"
        "（T1-b §5 第 e 条）。T1-a §5 只豁免版本号与出处：纯日期型游戏机制数值"
        "（`2024-05-15 更新后…`）不在豁免内。",
    ),
    _rule(
        "markdown_marker",
        r"^\s*(?:#{1,6}\s+|\d+[.、)]\s+|[-*+>]\s+)",
        "Markdown 列表项与标题里的序号（`1.`、`-`、`## 第 1 步`）：属于排版而不是数值。"
        "只豁免行首那截标记，`- 135 理智` 这种行里的数值仍会命中。",
    ),
    _rule(
        "cross_reference",
        r"\b[a-z][a-z0-9-]*-[a-z0-9-]+\b",
        "条目 id 与交叉引用（`screen-home`、`playbook-drop-lookup`）：连字符不是区间，"
        "数字是 id 的一部分。",
    ),
)

# 每条白名单规则在现有正文（或形态相近的样本）里必须命中一次。加了规则却没写样本、或
# 样本改了之后匹配不上，用例就会失败——避免白名单变成没人维护的垃圾桶。
WHITELIST_PROBES: dict[str, str] = {
    "ui_dimension": "那只是属性区的局部放大图（669×231），既不是整屏",
    "ui_ratio": "顶栏货币：右上方一行，约占横向 50%–93%、纵向 4%–14%。",
    "refresh_time": "每日 4:00 与 16:00 刷新，凌晨 4:00 结算前一天",
    "operation_delay": "进入后等约 1~2 秒再截图，避免截到转场。",
    "backtick_span": "用 `get_stage` 的 `sanity` 算，`series` 的 `-1` 禁用切换",
    "kernel_parameter": "- `threshold` 工作心情阈值，取值 0~1，内核默认 0.3",
    "command_parameter": "`get_drop_rates(include_closed=True)` 才会带已关闭关卡",
    "recruit_time_slot": "Recruit 任务的招募时长固定填 `9:00:00`",
    "rarity_or_stage": "1★–6★ 与 3 星通关；精英阶段 0 / 1 / 2 各有上限；技能 1/2/3",
    "stage_code": "1-7 与 CE-6 都能打，活动关是 act25side_08；B1、1F 是楼层",
    "version_and_date": "从 v6.8.0 起支持（2020-06-09 修订）",
    "markdown_marker": "1. 先 get_screenshot\n- 再核对",
    "cross_reference": "界面位置见 screen-home，撞名见 playbook-infrast-shift。",
    "level_transition": "精英化后等级回到 1 级；【专属模组】2 级和 3 级在 1 级基础上强化天赋。",
}


# ---------------------------------------------------------------------------
# 扫描器
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Hit:
    """一条命中（``rule_id`` 为 None）或一条被白名单豁免的候选（``rule_id`` 非 None）。"""

    entry_id: str
    line_no: int | None
    fragment: str
    token: str
    rule_id: str | None

    def describe(self) -> str:
        where = f"{self.entry_id}:{self.line_no}" if self.line_no else self.entry_id
        suffix = f"（白名单 {self.rule_id}）" if self.rule_id else ""
        return f"{where} {self.fragment}{suffix}"


def _whitelist_intervals(line: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for rule_id, pattern, _why in WHITELIST:
        if rule_id in ("markdown_marker", "backtick_span", "ui_ratio"):
            continue  # 这三条在 _scan_line 里单独处理
        for match in pattern.finditer(line):
            spans.append((match.start(), match.end(), rule_id))
    return spans


def _candidates(line: str) -> list[tuple[int, int, str, bool]]:
    """生成行内候选 ``(起点, 终点, token, 是否属于判定层)``。

    同一处的多个重叠匹配按「长且核心优先」合并：判定层先出、``(是否核心, 长度)`` 排序取最大，
    否则 `669` 这类短匹配会盖掉 `669×231`、`9` 会盖掉 `9:00:00`。
    判定层之外的候选只是为了让白名单区间有东西可豁免，``_scan_line`` 会把它们丢掉。
    """
    merged: dict[int, tuple[int, int, str, bool]] = {}
    layers = [(pattern, True) for pattern in CORE_PATTERNS] + [(_WIDE_CANDIDATE, False)]
    for pattern, core in layers:
        for match in pattern.finditer(line):
            best = merged.get(match.start())
            rank = (core, match.end() - match.start())
            if best is None or rank > (best[3], best[1] - best[0]):
                merged[match.start()] = (
                    match.start(),
                    match.end(),
                    match.group(0).strip(),
                    core,
                )
    return [merged[start] for start in sorted(merged)]


def _scan_line(entry_id: str, line_no: int | None, line: str) -> list[Hit]:
    """扫一行，返回**全部**候选；被豁免的候选 ``rule_id`` 非 None，其余是命中。

    保留两类「整行豁免」：围栏代码块与跨行代码（奇数个反引号）的行，内容是工具调用
    示例而不是知识正文；这类行的候选统一记到对应规则名下，便于白名单空转检查。
    Markdown 标记行不再整行豁免，只豁免行首那截标记（见 marker_spans）。
    """
    if line.lstrip().startswith(("```", "~~~")):
        return [
            Hit(entry_id, line_no, line.strip(), token, "backtick_span")
            for _start, _end, token, _core in _candidates(line)
        ]
    if line.count("`") % 2 == 1:
        return [
            Hit(entry_id, line_no, line.strip(), token, "backtick_span")
            for _start, _end, token, _core in _candidates(line)
        ]

    marker_spans: list[tuple[int, int, str]] = []
    marker = _MARKER_MATCH.search(line)
    if marker:
        # 只豁免标记本身（`1. `、`## `、`- `），不能圈住整行：否则 `- 135 理智` 会被放过。
        marker_spans = [(marker.start(), marker.end(), "markdown_marker")]
    backtick_spans = [
        (match.start(), match.end(), "backtick_span")
        for match in re.finditer(r"`[^`\n]*`", line)
    ]
    ratio_spans = [
        (match.start(), match.end(), "ui_ratio") for match in UI_RATIO.finditer(line)
    ]
    dimension_spans = [
        (match.start(), match.end(), "ui_dimension")
        for match in UI_DIMENSION.finditer(line)
    ]
    spans = (
        backtick_spans
        + dimension_spans
        + ratio_spans
        + marker_spans
        + _whitelist_intervals(line)
    )

    hits: list[Hit] = []
    for start, end, token, core in _candidates(line):
        # 白名单区间按「短区间优先」判定：`9:00:00` 既在 recruit_time_slot 里，也在裸数字
        # 观察层的范围里，取最短那条才说明它被哪条规则真正豁免。
        covering = next(
            (
                rule_id
                for span_start, span_end, rule_id in sorted(
                    spans, key=lambda span: span[1] - span[0]
                )
                if start >= span_start and end <= span_end
            ),
            None,
        )
        if covering is None and "%" in token:
            # `50%–93%` 这类区间：白名单匹配的是前半段，命中却覆盖整段，按覆盖度放宽。
            covering = next(
                (
                    rule_id
                    for span_start, _span_end, rule_id in spans
                    if rule_id == "ui_ratio" and span_start <= start
                ),
                None,
            )
        if covering is None and not core:
            continue  # 观察层候选：用来证明白名单有效，但不是命中
        hits.append(Hit(entry_id, line_no, line.strip(), token, covering))
    return hits


def scan_text(entry_id: str, text: str) -> list[Hit]:
    """扫一段文本，返回**未**被白名单豁免的命中。"""
    hits: list[Hit] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        hits += [hit for hit in _scan_line(entry_id, line_no, line) if hit.rule_id is None]
    return hits


def scan_entries() -> dict[str, list[Hit]]:
    return {
        entry_id: scan_text(entry_id, entry.body) for entry_id, entry in load_builtins().items()
    }


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------
async def _create_all(engine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


class _NoActivity:
    """默认桩：读不到活动资源，索引用例与本机装的是哪一期活动解耦。"""

    def snapshot(self):
        raise AppError(ErrorCode.RESOURCE_LOAD_FAILED, "测试里没有活动资源")


@pytest.fixture
def harness(tmp_path):
    engine = db_session.make_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'knowledge.db'}", poolclass=NullPool
    )
    asyncio.run(_create_all(engine))
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield KnowledgeService(factory, activity_index=_NoActivity()), factory
    engine.sync_engine.dispose()


def _disposition_records() -> list[tuple[str, dict]]:
    """``(来源标签, 条目)`` 序列：优先仓库内夹具，夹具缺失时回退 SDD 工作区的原始报告。"""
    if DISPOSITION_FIXTURE.is_file():
        data = json.loads(DISPOSITION_FIXTURE.read_text(encoding="utf-8"))
        assert isinstance(data, list) and data, DISPOSITION_FIXTURE
        return [(str(item.get("source") or DISPOSITION_FIXTURE.name), item) for item in data]
    records: list[tuple[str, dict]] = []
    for name in DISPOSITION_FILES:
        path = SDD_DIR / name
        if not path.is_file():
            raise AssertionError(
                f"找不到反例来源：既没有仓库内夹具 {DISPOSITION_FIXTURE}，也没有 M30 的 SDD "
                f"工作区报告 {SDD_DIR / name}。夹具是 T1 逐条处置表的快照（只含 id 与 "
                f"numbers_to_remove），随台账归档；请恢复其一，别把这条反向用例悄悄删掉。"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, list) and data, name
        records.extend((f"{name}:{item['id']}", item) for item in data)
    return records


def _disposition_snippets() -> list[tuple[str, str]]:
    """``numbers_to_remove`` 里挑出来的真实数值片段 ``(来源, 片段)``。

    复核报告这些字段是描述性短语，必须挑掉带注释（→、括号、分号）与不以数值为主体的
    条目，只留下「数字 + 单位」或裸百分比形态的片段——它们必须被扫描器命中，否则说明
    检查漏了一整类数值。
    """
    picked: list[tuple[str, str]] = []
    for source, item in _disposition_records():
        for raw in item.get("numbers_to_remove") or []:
            snippet = raw.strip()
            if not snippet or len(snippet) > 60:
                continue
            if any(mark in snippet for mark in SNIPPET_REJECT):
                continue
            if not SNIPPET_PREDICATE.search(snippet):
                continue
            picked.append((f"{source}:{item['id']}", snippet))
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for source, snippet in picked:
        if snippet in seen:
            continue
        seen.add(snippet)
        unique.append((source, snippet))
    return unique


# 手写反例：任务书点名与 T1 报告点名的形态，覆盖单位表的每一类。
HANDWRITTEN_REVERSE = (
    "135 理智",
    "600 合成玉",
    "至纯源石 ×2",
    "龙门币 3000",
    "凭证 120",
    "信用 300",
    "经验 5000",
    "电力 60",
    "心情 +2",
    "氛围值 5000",
    "30%",
    "掉率 2.00%",
    "7 天",
    "30 分钟",
    "12 小时",
    "每周 1800",
    "周上限 1200",
    "信用上限 300",
    "次数上限 3 次",
    "每天 240 理智",
    "一抽 600 合成玉",
    "连续 50 次没出 6★ 后 +2%",
    "前 10 抽必出 5★",
    "1200+1800=3000 合成玉/周",
)


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------
def test_builtin_entries_are_scannable():
    entries = load_builtins()
    assert len(entries) == EXPECTED_ENTRY_COUNT, sorted(entries)
    assert all(entry.body.strip() for entry in entries.values())


def test_whitelist_patterns_match_their_probes():
    by_id = {rule_id: pattern for rule_id, pattern, _why in WHITELIST}
    assert set(WHITELIST_PROBES) == set(by_id), (
        "每条白名单规则都要有样本，样本指向的规则必须存在："
        f"缺样本={sorted(set(by_id) - set(WHITELIST_PROBES))}，"
        f"多余样本={sorted(set(WHITELIST_PROBES) - set(by_id))}"
    )
    for rule_id, probe in WHITELIST_PROBES.items():
        assert by_id[rule_id].search(probe), f"白名单 {rule_id} 的样本不再匹配：{probe!r}"


def test_whitelist_rules_actually_exempt_something_in_the_builtins():
    """白名单不许「空转」：每条规则都要在收录的文本样本里真的豁免掉至少一个候选。"""
    transcripts = [
        *(entry.body for entry in load_builtins().values()),
        *(entry.when_to_use for entry in load_builtins().values()),
        *HANDWRITTEN_REVERSE,
        *WHITELIST_PROBES.values(),
    ]
    body = "\n".join(transcripts)
    used: dict[str, int] = {rule_id: 0 for rule_id, _p, _w in WHITELIST}
    for line in body.splitlines():
        for hit in _scan_line("census", None, line):
            if hit.rule_id:
                used[hit.rule_id] += 1
    unused = sorted(rule_id for rule_id, count in used.items() if count == 0)
    assert not unused, f"这些白名单规则没有豁免任何命中，要么删掉要么写清理由：{unused}"


def test_builtin_bodies_hold_no_number_plus_game_unit():
    """规格 §10.3 T4：正文不得出现「数字 + 游戏单位」。"""
    report = {entry_id: hits for entry_id, hits in sorted(scan_entries().items()) if hits}
    if report:
        lines = [f"{sum(len(v) for v in report.values())} 处命中："]
        for _entry_id, hits in report.items():
            lines += [f"- {hit.describe()}" for hit in hits]
        pytest.fail("内置条目里还有「数字 + 游戏单位」，请改成工具或 PRTS 页面指引：\n" + "\n".join(lines))


def test_when_to_use_holds_no_number_plus_game_unit():
    """`when_to_use` 会进索引行，和正文一样不能带数值。"""
    report = [
        hit
        for entry_id, entry in sorted(builtin_entries().items())
        for hit in scan_text(entry_id, entry.when_to_use)
    ]
    assert not report, "\n".join(hit.describe() for hit in report)


def test_render_context_index_lines_hold_no_number_plus_game_unit(harness):
    """规格 §10.3 验收第 2 条：`render_context()` 的索引部分没有数值。"""
    service, _factory = harness

    async def scenario() -> str:
        context = await service.render_context()
        assert context.startswith(INDEX_HEADER)
        boundary = min(
            (
                context.index(f"\n\n{marker}")
                for marker in ("当期活动", "当期卡池", "账号", "术语表", "用户档案")
                if f"\n\n{marker}" in context
            ),
            default=len(context),
        )
        return context[:boundary]

    index_section = asyncio.run(scenario())
    index_lines = [line for line in index_section.splitlines()[1:] if line.strip()]
    assert len(index_lines) == EXPECTED_ENTRY_COUNT - FULLTEXT_ENTRY_COUNT, (
        f"索引行 {len(index_lines)} 条，期望 {EXPECTED_ENTRY_COUNT - FULLTEXT_ENTRY_COUNT} 条"
        f"（{EXPECTED_ENTRY_COUNT} 个条目去掉 {FULLTEXT_ENTRY_COUNT} 个全文注入的 glossary/profile）"
    )
    hits = scan_text("<index>", index_section)
    assert not hits, "\n".join(hit.describe() for hit in hits)


def test_reverse_samples_are_caught():
    """反向用例：复核报告的 numbers_to_remove 真实片段必须被扫出来。"""
    samples = _disposition_snippets()
    assert len(samples) >= 10, f"只挑到 {len(samples)} 条反例，取样规则太严：{samples}"
    body = "\n".join(snippet for _source, snippet in samples)
    hits = scan_text("fake-entry", body)
    missed = [
        (source, snippet)
        for source, snippet in samples
        if not any(snippet in hit.fragment for hit in hits)
    ]
    assert not missed, f"这些真数值漏报了：{missed}"


def test_handwritten_reverse_samples_are_caught():
    body = "\n".join(HANDWRITTEN_REVERSE)
    hits = scan_text("fake-entry", body)
    missed = [
        snippet
        for snippet in HANDWRITTEN_REVERSE
        if not any(snippet in hit.fragment for hit in hits)
    ]
    assert not missed, f"这些真数值漏报了：{missed}"


def test_reverse_fragments_are_not_whitelisted():
    """反向用例：真数值不能整段落进白名单区间（防止白名单被写宽）。"""
    samples = [snippet for _source, snippet in _disposition_snippets()]
    leaked = [
        snippet
        for snippet in (*samples, *HANDWRITTEN_REVERSE)
        if any(
            hit.rule_id is not None and hit.token == snippet
            for hit in _scan_line("fake-entry", None, snippet)
        )
    ]
    assert not leaked, f"这些真数值被白名单整段豁免了：{leaked}"
