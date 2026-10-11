"""M36 干员详情练度扫描的**只读**自定义节点定义（规格 §4.1）。

M36 只用「看」，不用「点」：所有节点全部 ``action: DoNothing``（返回箭头与翻屏
两个导航节点除外），写进一层独立的自定义资源
``maa-layers/custom/resource/tasks/MaaApi@OperatorScan.json``。

读数全靠内核自带的识别能力，因此有几条真机实测出来的硬约束（W1 标定）：

1. **一次只回一条结果**：``SubTask*`` 回调里 ``details.details.result`` 是单个对象，
   不是一个列表。所以「一行一个宽 ROI」读不全 8 个名字，必须**按列切开**逐列读；
2. **每个节点的出路都要带 ``Done`` 兜底**（真机实测）：``Custom`` 任务会沿着提交节点的
   ``next`` 一路往下走——只提交 ``CardNameTopCol0`` 时，内核就把 16 个卡名节点全跑了一遍。
   但候选里没有「必定命中」的节点时，一个读不出来的节点会让内核重试 20 次（约半分钟）
   再报 ``TaskChainError``，而 app 侧的流水线不把这条当终态，这一步只能耗到超时。所以
   每个节点的 ``next`` / ``exceededNext`` 都是「**这条链剩下的全部节点** + :data:`DONE`」
   （``JustReturn`` + ``action: Stop``，必定命中）：内核按顺序试候选，读得出来的节点依次
   跑完、读不出来的当场跳过（后面还排着别的节点），链最后干净收尾；服务层提交时还会把
   ``Done`` 追加进 ``task_names``，链首节点读不出来时也不会卡住；
3. **不吃缓存**（没有 ``cache``）：一个节点一次识别，缓存没有意义；
4. **回调里的任务名是短名**：节点 ``MaaApi@OperatorScan@CardNameTopCol0`` 回调里报
   ``CardNameTopCol0``（取最后一段），解析见 :func:`node_key`。

坐标一律是内核基准 1280x720（真机 2560x1440 由内核自己换算）。网格几何是真机标定值：
一屏 8 列 × 2 行，列距 160，上排卡名带 y≈354、下排 y≈673。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from maa_api.services.account.rules import MAX_MODULE_LEVEL, MIN_MODULE_LEVEL, MODULE_TYPES

#: 所有节点的名字前缀（也是自定义资源文件的基名）。
SCAN_TASK_PREFIX = "MaaApi@OperatorScan"

#: 写进资源层的任务文件名。
TASK_FILE_NAME = f"{SCAN_TASK_PREFIX}.json"

#: 资源层里任务文件所在的两级目录（相对资源层的 ``resource/``）。
TASK_LAYER_SUBDIR: tuple[str, ...] = ("tasks",)


def scan_task_path(root: str | Path) -> Path:
    """扫描节点文件的绝对路径（``<资源层>/tasks/MaaApi@OperatorScan.json``）。

    与 M29 的结果识别任务同一个落地位置（``maa-layers/custom/resource/tasks/``），
    只是各自一个独立文件——内核按名字合并所有任务文件，同名才互相覆盖。
    """
    return Path(root).joinpath("maa-layers", "custom", "resource", *TASK_LAYER_SUBDIR, TASK_FILE_NAME)

# ---------------------------------------------------------------------------
# 网格几何（内核 1280x720 基准；真机标定见模块 docstring）
# ---------------------------------------------------------------------------

#: 一屏可见的列数与行数。
GRID_COLUMNS = 8
GRID_ROWS = 2
#: 列距。
COLUMN_PITCH = 160
#: 两行的代号（与节点名、回调解析一致）。
ROW_KEYS: tuple[str, str] = ("top", "bottom")
#: 卡名条的内核 y 与高度（按行）。
NAME_BAND_Y: dict[str, int] = {"top": 344, "bottom": 666}
NAME_BAND_HEIGHT = 30
#: 点开某一列要用到的卡面中心 y（按行）：卡面在名字条上方约 130 像素处。
CARD_CENTER_Y: dict[str, int] = {"top": 225, "bottom": 500}

#: 模组编号（如 ``CHG X``）与模组等级（``STAGE 3``）——内核没有现成节点，自己标。
#: 真机标定（2560x1440，设备像素）：``CHG X`` 在设备 (2182,1162)-(2270,1187)，
#: ``STAGE 3`` 在设备 (2335,1090)-(2495,1122)；没有模组的干员这块显示灰字「模组」，
#: 我们的读数正则会把它判成未识别（不写库），所以同一个 roi 可以两种形态共用。
#:
#: STAGE 的 roi 只框**数字**那一小段：给整段 ``STAGE 3`` 时 OCR 会把单词本身当成
#: 一个文本块回给我们（真机实测回 ``'STAGE'``，rect [1173,548,46,19]），数字就丢了。
MODULE_CODE_ROI = (1085, 576, 60, 24)
MODULE_STAGE_ROI = (1222, 542, 32, 26)
#: 详情页左上角的返回箭头（与内核 ``Return`` 同 ROI）。
BACK_ROI = (0, 0, 180, 80)

# ---------------------------------------------------------------------------
# 读数节点（roi 与模板取自内核 ``OperProgress@*``，但**自己声明一份**）
# ---------------------------------------------------------------------------
#
# 内核自带 ``OperProgress@CurrentSkillLevel`` / ``…Skill{1,2,3}MasterLevel`` /
# ``…CurrentElite{0,1,2}``，roi 与模板都是现成的，但它们的定义里没有 ``next``——
# 直接引用会让「命中即链尾」的链式读法断在第一个读数上，而自定义层里给内核节点补
# ``next`` 又要覆盖内核定义（影响面不必要地大）。所以这里把 roi/模板抄一份，用自己的
# 名字声明，链完全由本文件的 :func:`_chain` 接管。

#: ``RANK n`` 的技能等级（``baseTask: NumberOcrReplace``，roi 抄自内核）。
SKILL_LEVEL_ROI = (1215, 359, 14, 22)
#: 精英化徽记 roi（抄自内核）。
ELITE_ROI = (866, 235, 119, 88)

# ---------------------------------------------------------------------------
# 身份核对：详情页左下角那一行**拉丁名**
# ---------------------------------------------------------------------------
#
# 详情页左下角是「★ 行 + 拉丁名 + 中文美术字」三行。中文那个大名字是艺术字，识别不出来
# （M36 实测裸 OCR 只回一个「画」，score 0.17），但**拉丁名是规规矩矩的白字描边**，内核
# ASCII OCR 读得动（M40 真机实测：`Bagpipe` 0.94、`Saileach` 1.00、`Ch'en the Dawnstreak`
# 0.98）。全量扫描不回列表就想知道「眼前这一页是谁」，只能靠它。
#
#: 拉丁名那一行的只读 roi（内核 1280x720 基准）。
#:
#: 真机取证（2026-10-11，设备 2560x1440，`/tmp/m39_name_zoom.png`）：白字带在
#: 设备 x 20–1036、y 912–968，于是内核基准 (10, 456)–(518, 484)。**左边缘留余量**：
#: 一开始取 x=8，真机上 `Ines` 读成 `nes`、`SilverAsh…` 读成 `ilverAsh…`——都是首字母被
#: 切掉，把左边放到 0、上下各加 4 像素之后这两例都读全了（M40 真机复核）。
OPER_NAME_EN_ROI = (0, 452, 524, 32)

# ---------------------------------------------------------------------------
# 专精：内核那条路读不准，改成**在 Python 里数亮点**
# ---------------------------------------------------------------------------
#
# 内核的 ``OperProgress@CurrentSkill{1,2,3}MasterLevel`` 是「一个 roi + 四个模板
# （``OperFilesSkillMaster0..3.png``）」的多模板节点，真机实测不可用：
# 徽章满亮（三个圆点全白）时它回的是数组第一项 ``OperFilesSkillMaster0.png``
# （score 0.9899），单模板节点里 ``…Master3`` 反而更高（0.9904）——内核只认数组第一项。
# 换成「从 M3 徽章上裁一个亮圆点当模板、每个圆点一个只读节点」也不行：真机上
# 暗圆点一样能匹配上 0.83-0.99（暗点实测 风笛 技能一：三个点 RGB≈71，模板照样命中），
# 完全没有区分度。
#
# 真正干净的做法是**直接看像素**：亮圆点 RGB≈255、暗圆点 RGB≈71（同一块背景），
# 取每个圆点框里最亮那 25% 像素的均值，71 与 255 分得干干净净（真机实测：
# 风笛 技能一 71/71/71 → 专精 0，技能二、三 255×3 → 专精 3）。
# 截图走 ``client.get_image()``（就是内核 ``AsstGetImage`` 那一张，与 OCR 节点同一帧），
# 用 PIL 打开（仓库生产代码已经在用 PIL，见 ``maa_api/util/image.py``）。
#: 三个圆点的位置名（徽章里从上到下、从左到右）。
MASTER_PIP_POSITIONS: tuple[str, ...] = ("Top", "BottomRight", "BottomLeft")
#: (技能槽, 圆点位置) → 该圆点的取样框（内核坐标，7x7；真机标定：内核模板匹配位置）。
MASTER_PIP_ROIS: dict[tuple[int, str], tuple[int, int, int, int]] = {
    (1, "Top"): (891, 336, 7, 7),
    (1, "BottomRight"): (895, 343, 7, 7),
    (1, "BottomLeft"): (887, 343, 7, 7),
    (2, "Top"): (979, 336, 7, 7),
    (2, "BottomRight"): (983, 343, 7, 7),
    (2, "BottomLeft"): (975, 343, 7, 7),
    (3, "Top"): (1067, 336, 7, 7),
    (3, "BottomRight"): (1071, 343, 7, 7),
    (3, "BottomLeft"): (1062, 343, 7, 7),
}
#: 取样框越大就越可能吃到背景，所以只取最亮的一小部分像素来判「这点亮没亮」。
PIP_SAMPLE_RATIO = 0.25
#: 亮/暗分界（亮 255 / 暗 71，取中间值留足余量）。
PIP_BRIGHT_THRESHOLD = 160.0
#: 详情页基准宽度：截图按它换算比例（内核坐标 → 截图坐标）。
KERNEL_WIDTH = 1280

#: 精英化徽记模板名 → 精英化等级（三个徽记美术互不相同，可以一个模板一个节点）。
ELITE_TEMPLATES: dict[str, int] = {
    "OperFilesElite2.png": 2,
    "OperFilesElite1.png": 1,
    "OperFilesElite0.png": 0,
}


def elite_level_of(template: str | None) -> int | None:
    """模板名 → 精英化等级；不认识的名字（给 ``None``）。"""
    if not template:
        return None
    return ELITE_TEMPLATES.get(template)


def module_type_of(text: str | None) -> str | None:
    """模组编号文本 → 模组类型（``CHG X`` → ``X``）；认不出来给 ``None``。

    库里那份 ``modules`` 的键是单个类型字母（X/Y/D/A，见 ``rules.normalize_modules``），
    所以这里把编号里的分支码（CHG/MER/…）丢掉，只留类型。
    """
    match = MODULE_CODE_PATTERN.match(text or "")
    if match is None:
        return None
    module_type = match.group(2)[:1]
    return module_type if module_type in MODULE_TYPES else None


def module_stage_of(text: str | None) -> int | None:
    """模组等级文本（``STAGE 3`` 或 OCR 出来的 ``3``）→ 1..3；认不出来给 ``None``。"""
    match = MODULE_STAGE_PATTERN.search(text or "")
    if match is None:
        return None
    level = int(match.group(1))
    return level if MIN_MODULE_LEVEL <= level <= MAX_MODULE_LEVEL else None


#: 干员名 OCR 基任务：内核 OperBox 用的那张表（``CharsNameOcrReplace`` + ``specialParams``），
#: 卡名复用它，才吃得到那张表里的错字修正。
CARD_NAME_BASE_TASK = "OperBoxNameOCR"

# ---------------------------------------------------------------------------
# 解析用的正则
# ---------------------------------------------------------------------------

#: 模组编号的形态：分支码 + 类型字母（如 ``CHG X`` / ``MER Y``）。
MODULE_CODE_PATTERN = re.compile(r"^\s*([A-Z]{2,4})\s*([A-Z]{1,4})\s*$")
#: 模组 ``STAGE n`` 里的等级数字。
MODULE_STAGE_PATTERN = re.compile(r"(\d+)")


# ---------------------------------------------------------------------------
# 节点名
# ---------------------------------------------------------------------------


def _full(short: str) -> str:
    """短名 → 完整节点名。"""
    return f"{SCAN_TASK_PREFIX}@{short}"


def node_key(reported: str | None) -> str:
    """回调里的任务名 → 短名（内核报的是最后一段）。"""
    return (reported or "").rpartition("@")[2]


#: 每一步的「必定命中」出口：``JustReturn`` 一定匹配，``action: Stop`` 就地收尾。
#:
#: 每个节点的 ``next`` / ``exceededNext`` 都指向它（读完一个节点就干净结束），服务层
#: 提交时还会把它追加在 ``task_names`` 末尾兜底（这一步的节点读不出来时也不卡住）。
#: 两条都是真机实测出来的硬约束，理由见模块 docstring 第 2 条。
DONE_SHORT = "Done"
DONE = _full(DONE_SHORT)


def card_name_node(row: str, column: int) -> str:
    """某一格卡名节点的完整名（``CardNameTopCol3`` 之类）。"""
    if row not in ROW_KEYS:
        raise ValueError(f"行只能是 {ROW_KEYS}，收到 {row!r}")
    if not 0 <= column < GRID_COLUMNS:
        raise ValueError(f"列号只能是 0..{GRID_COLUMNS - 1}，收到 {column!r}")
    return _full(f"CardName{row.title()}Col{column}")


def parse_card_name_node(reported: str | None) -> tuple[str, int] | None:
    """卡名节点的短名 → ``(行, 列)``；不是卡名节点则给 ``None``。"""
    match = re.fullmatch(r"CardName(Top|Bottom)Col(\d+)", node_key(reported))
    if match is None:
        return None
    row = match.group(1).lower()
    column = int(match.group(2))
    if column >= GRID_COLUMNS:
        return None
    return row, column


def card_center(row: str, column: int) -> tuple[int, int]:
    """某一格的卡面中心（内核坐标），点开详情页用。"""
    card_name_node(row, column)  # 复用参数校验
    return COLUMN_PITCH * column + COLUMN_PITCH // 2, CARD_CENTER_Y[row]


def click_cell_node(row: str, column: int) -> str:
    """点开某一格的节点完整名（``ClickTopCol3`` 之类）。"""
    card_name_node(row, column)  # 复用参数校验
    return _full(f"Click{row.title()}Col{column}")


#: 详情页读一轮要跑的节点（顺序即执行顺序，短名）。
#:
#: 顺序有讲究：链上某个节点识别不出东西就会**截断整条链**（真机实测），所以
#: 「可能读不到」的节点排在后面——模组只有解锁了才有（没解锁时面板是一行灰字
#: 「模组」），拉丁名放**最后**（它是核对身份用的，读不到就只是这一张不核对）。
#:
#: 「详情页干员名」= 中文那个大名字是艺术字（真机实测裸 OCR 只回一个「画」，score 0.17），
#: 认不出来，所以干员身份仍以**列表页卡名**为准（M36 规格 §4.3 裁决）；``OperNameEn``
#: 读的是同一行位置上的**拉丁名**（M40）：全量扫描不回列表，靠它核对「眼前这一页是谁」。
DETAIL_NODE_KEYS: tuple[str, ...] = (
    "SkillLevel",
    "Elite2",
    "Elite1",
    "Elite0",
    "ModuleCode",
    "ModuleStage",
    "OperNameEn",
)
#: 短名 → 完整节点名（全是我们自己声明的那一套，见下面「读数节点」一节）。
DETAIL_NODE_NAMES: dict[str, str] = {key: _full(key) for key in DETAIL_NODE_KEYS}

#: 详情页读数节点（供服务层直接用）。
MODULE_CODE = DETAIL_NODE_NAMES["ModuleCode"]
MODULE_STAGE = DETAIL_NODE_NAMES["ModuleStage"]
#: 拉丁名节点（M40）：详情页左下角那一行，用来核对这一页是谁。
OPER_NAME_EN = DETAIL_NODE_NAMES["OperNameEn"]
#: 精英化节点 → 等级（三个都跑，分最高的那个就是读到的精英化）。
ELITE_NODE_LEVELS: dict[str, int] = {
    DETAIL_NODE_NAMES[f"Elite{level}"]: level for level in (2, 1, 0)
}
#: 精英化命中之后换一条更短的尾巴（M38 提速，docs/13 §8.53）。
#:
#: 三个徽记模板互斥，命中一个就没必要再试另外两个：精二命中 ``Elite2`` 后直接进模组，
#: 精一/精零命中后跳过模组——模组系统要求精二，低精英化试 ``ModuleCode``/``ModuleStage``
#: 只会白跑两次识别（taskDelay=100 时每次约 0.4 秒）。
#:
#: M40：尾巴一律接到 :data:`OPER_NAME_EN`（拉丁名）上——它是**核对身份**用的，任何精英化
#: 都要读，所以捷径不能再把它跳过去。
ELITE_SHORTCUTS: dict[str, tuple[str, ...]] = {
    DETAIL_NODE_NAMES["Elite2"]: (MODULE_CODE, MODULE_STAGE, OPER_NAME_EN),
    DETAIL_NODE_NAMES["Elite1"]: (OPER_NAME_EN,),
    DETAIL_NODE_NAMES["Elite0"]: (OPER_NAME_EN,),
}
#: 进干员列表页：内核自己的 ``OperBoxBegin`` 链（顺带按「等级」排序）。
ENTER_BOX = _full("EnterBox")
#: 返回箭头与翻到下一屏（两个导航节点，不消耗资源）。
BACK = _full("Back")
SWIPE_NEXT_PAGE = _full("SwipeNextPage")
#: M39 全量扫描：详情页左滑到下一名干员（不回列表）。
SWIPE_NEXT_DETAIL = _full("SwipeNextDetail")

#: 翻屏的起点／终点（内核坐标，4 元组：起点、终点各是一块 100x100 的落点）与时长。
#: 真机实测：干员列表是**横向翻页**的——手指从右往左划才换屏；竖着划（朝上朝下都试过，
#: ``adb shell input swipe`` 与内核 ``action: Swipe`` 一样）屏幕一格都不动。所以这里照抄
#: 内核自己的 ``SlowlySwipeToTheRight`` 的横向形状（``algorithm: JustReturn`` + ``action: Swipe``）。
#: 2 元组的 rect 会被内核判成非法节点、连带整条链提交失败（实测）。
SWIPE_FROM = (1080, 350, 100, 100)
SWIPE_TO = (100, 350, 100, 100)
SWIPE_PARAMS = [400, 1, 37, 1]

#: M39：详情页左滑到下一名干员。起点必须落在**立绘**上——两侧是属性/技能面板，从那里起手
#: 一格都不动（真机实测 mean 0.00–0.05）；起点再往右（内核 x ≥ 1100）会拖到右侧职业筛选栏上，
#: 一划就把筛选栏拉开、列表被过滤（探针里踩过一次）。终点选 (250,400)：划完手指停在左半边，
#: 不会碰到返回箭头，也不会碰到左下的名字美术字。
DETAIL_SWIPE_FROM = (650, 400, 100, 100)
DETAIL_SWIPE_TO = (250, 400, 100, 100)
#: 换人之后画面多久稳定：真机实测 0.9 秒与 1.7 秒两帧逐像素相同，取 900 毫秒。
DETAIL_SWIPE_POST_DELAY = 900

#: 点开某一格时落点方块的大小：内核里的固定点点击都用小方块（``ClickCorner`` 是 10x10）。
CLICK_SIZE = 10
#: 点完等游戏把详情页推出来。M38 把详情节点并进同一条链之后，这一段是**唯一**的渲染等待
#: （合并前那次流水线之间的 app 往返顺带等掉了渲染），所以只从 800 压到 500——真机上再短就
#: 有机会在推页动画里 OCR（实测 500 可用，见 docs/13 §8.53）。
CLICK_POST_DELAY = 500


# ---------------------------------------------------------------------------
# 定义
# ---------------------------------------------------------------------------


def _ascii_node(roi: tuple[int, int, int, int], *, doc: str) -> dict[str, Any]:
    """只读 ASCII OCR 节点（自己声明，不走 ``baseTask``）。

    ``NumberOcrReplace`` 那张表会把 ``O``→``0``、``I/L``→``1``、``B``→``8``、空格删掉，
    用来读模组编号（``CHG X``）会把字母改坏；``LETTERS`` 这个名字内核里根本不存在，
    引用它会**整条链提交失败**（真机实测：``append_task`` 返回 0）。所以这里内联声明。
    """
    return {
        "algorithm": "OcrDetect",
        "isAscii": True,
        "text": [],
        "roi": list(roi),
        "action": "DoNothing",
        "doc": doc,
    }


def _ocr_node(
    roi: tuple[int, int, int, int],
    *,
    base_task: str,
    doc: str,
) -> dict[str, Any]:
    """一个只读 OCR 节点：识别 ROI 里的文字，不做任何点击。"""
    return {"text": [], "roi": list(roi), "action": "DoNothing", "baseTask": base_task, "doc": doc}


def box_screen_nodes() -> tuple[str, ...]:
    """读一屏卡名的节点链（16 个，按 上排 8 列 → 下排 8 列 的顺序）。"""
    return tuple(card_name_node(row, column) for row in ROW_KEYS for column in range(GRID_COLUMNS))


def detail_screen_nodes(*lead: str) -> tuple[str, ...]:
    """读一张详情页的节点链（6 个，顺序见 :data:`DETAIL_NODE_KEYS`）。

    ``lead`` 是链首的导航节点（M38：把「点开这一格」并进同一条链，省一次提交与终态
    往返，真机上每次约 0.8 秒）。
    """
    return (*lead, *(DETAIL_NODE_NAMES[key] for key in DETAIL_NODE_KEYS))


def _chain(
    nodes: dict[str, dict[str, Any]],
    names: list[str],
    *,
    shortcuts: Mapping[str, Sequence[str]] | None = None,
) -> None:
    """把一串只读节点接成一条链，每个节点的出路都带 ``Done`` 兜底。

    - ``maxTimes: 1``：不重试；
    - ``next`` / ``exceededNext`` = 「**这条链剩下的全部节点** + :data:`DONE`」：内核在同一
      轮里按顺序依次试这些候选，谁先匹配上就走谁。于是命中的节点继续往下走、**识别不出来
      的节点被直接跳过**（候选里排着后面的节点），链最后必定落在 ``Done``
      （``JustReturn`` + ``Stop``）上干净收尾。
    - ``shortcuts`` 给某些节点换一条更短的尾巴（M38 的精英化短路，见
      :data:`ELITE_SHORTCUTS`）：键是节点名，值是它命中后接着要试的节点。
      真机实测（11:56 那轮）：候选只放**紧邻的下一个**节点时，精二的干员在 ``Elite2`` 命中
      后试 ``Elite1`` 不中、就直接落到 ``Done``——后面的 ``ModuleCode`` / ``ModuleStage``
      一次都没跑（模组读数空缺），所以候选必须是整条尾巴。
    - 候选里没有 ``Done`` 时，一个读不出来的节点会让内核重试 20 次再报 ``TaskChainError``，
      而 app 侧的流水线不把这条当终态，这一步就只能耗到超时（11:42 那轮卡 90 秒就是这么来的）。
    """
    for index, name in enumerate(names):
        node = nodes[name]
        node["maxTimes"] = 1
        override = (shortcuts or {}).get(name)
        # 空元组是「到此为止」的有效取值，不能和「没配捷径」混为一谈。
        following = list(names[index + 1 :] if override is None else override)
        node["next"] = [*following, DONE]
        node["exceededNext"] = [*following, DONE]


def operator_scan_tasks() -> dict[str, dict[str, Any]]:
    """M36 那只读自定义资源层的全部节点定义（可直接 ``json.dump``）。"""
    tasks: dict[str, dict[str, Any]] = {
        DONE: {
            "algorithm": "JustReturn",
            "action": "Stop",
            "doc": "链尾出口：必定命中，就地结束这一步（不读、不点）",
        },
        ENTER_BOX: {
            "algorithm": "JustReturn",
            # 进干员列表不自己点坐标：内核 ``OperBoxBegin`` 认得入口，顺带点「等级」
            # 页签（``OperBoxSortTabSelect``），列表顺序与内核既有流程一致。
            "next": ["OperBoxBegin"],
            "doc": "进干员列表页（复用内核 OperBoxBegin 链，顺带按等级排序）",
        },
        BACK: {
            "baseTask": "Return",
            # 内核自己带两个返回模板（``ReturnButton`` 节点用的就是这两个）：深浅主题
            # 各一张，一起用免得换主题之后点不动。
            "template": ["Return.png", "Return-White.png"],
            "templThreshold": 0.7,
            # M38：1500 → 900。返回箭头点完到列表可读之间没有别的等待，900 是真机复核过的下限。
            "postDelay": 900,
            "roi": list(BACK_ROI),
            "doc": "回干员列表的返回箭头（导航，不消耗资源）",
            "maxTimes": 1,
            "next": [DONE],
            "exceededNext": [DONE],
        },
        SWIPE_NEXT_PAGE: {
            "algorithm": "JustReturn",
            "action": "Swipe",
            "specificRect": list(SWIPE_FROM),
            "rectMove": list(SWIPE_TO),
            "specialParams": list(SWIPE_PARAMS),
            # M38：1200 → 800。翻页动画比返回略长，800 是真机复核过的下限。
            "postDelay": 800,
            "doc": "列表翻到下一屏（导航；只翻页不点干员，横向从右往左划）",
            "maxTimes": 1,
            "next": [DONE],
            "exceededNext": [DONE],
        },
        SWIPE_NEXT_DETAIL: {
            "algorithm": "JustReturn",
            "action": "Swipe",
            "specificRect": list(DETAIL_SWIPE_FROM),
            "rectMove": list(DETAIL_SWIPE_TO),
            "specialParams": list(SWIPE_PARAMS),
            "postDelay": DETAIL_SWIPE_POST_DELAY,
            "doc": "详情页左滑到下一位干员（导航；起点落在立绘上，不回列表）",
            "maxTimes": 1,
            "next": [DONE],
            "exceededNext": [DONE],
        },
        MODULE_CODE: _ascii_node(
            MODULE_CODE_ROI,
            doc="只读：模组编号（如 CHG X）——大写字母，解析成 X/Y/D/A 交给 Python 侧",
        ),
        MODULE_STAGE: _ocr_node(
            MODULE_STAGE_ROI,
            base_task="NumberOcrReplace",
            doc="只读：模组等级「STAGE n」里的数字",
        ),
        OPER_NAME_EN: _ascii_node(
            OPER_NAME_EN_ROI,
            doc="只读：详情页左下角的拉丁名（如 Bagpipe）——全量扫描用它核对这一页是谁",
        ),
        DETAIL_NODE_NAMES["SkillLevel"]: _ocr_node(
            SKILL_LEVEL_ROI,
            base_task="NumberOcrReplace",
            doc="只读：右下「RANK n」的技能等级（1-7）",
        ),
    }

    for template, level in ELITE_TEMPLATES.items():
        tasks[DETAIL_NODE_NAMES[f"Elite{level}"]] = {
            "template": template,
            # 真机实测：E2 干员身上 E1 的模板也能打到 0.8736，所以阈值抬到 0.9，
            # 再由服务层取分数最高的那个（见 docs/13 §8.51）。
            "templThreshold": 0.9,
            "roi": list(ELITE_ROI),
            "action": "DoNothing",
            "doc": f"只读：精英化 {level} 的徽记（三个徽记美术不同，一个模板一个节点）",
        }

    box_names = list(box_screen_nodes())
    for row in ROW_KEYS:
        for column in range(GRID_COLUMNS):
            name = card_name_node(row, column)
            tasks[name] = _ocr_node(
                (COLUMN_PITCH * column, NAME_BAND_Y[row], COLUMN_PITCH, NAME_BAND_HEIGHT),
                base_task=CARD_NAME_BASE_TASK,
                doc=f"只读：{row} 第 {column + 1} 列卡名",
            )
    _chain(tasks, box_names)

    # 点开某一格：坐标是卡面中心，走内核自带的 ``ClickRect`` 动作——内核自己点屏幕，
    # 不需要第二个 MaaCore 实例；`algorithm: JustReturn` 表示「一定命中」。
    #
    # M38：点开与「读这张详情页」并成同一条链（``next`` 直接排详情节点），省掉一次提交、
    # 排队与终态确认（真机每次约 0.8 秒）。``postDelay`` 仍是详情页推出来的等待时间，
    # 所以它不能跟着压——压的就是这一段。
    detail_names = list(detail_screen_nodes())
    for row in ROW_KEYS:
        for column in range(GRID_COLUMNS):
            x, y = card_center(row, column)
            tasks[click_cell_node(row, column)] = {
                "algorithm": "JustReturn",
                "action": "ClickRect",
                "specificRect": [x, y, CLICK_SIZE, CLICK_SIZE],
                "postDelay": CLICK_POST_DELAY,
                "doc": f"点开 {row} 第 {column + 1} 列干员并读这张详情页",
                "maxTimes": 1,
                "next": [*detail_names, DONE],
                "exceededNext": [*detail_names, DONE],
            }

    _chain(tasks, detail_names, shortcuts=ELITE_SHORTCUTS)
    return tasks
