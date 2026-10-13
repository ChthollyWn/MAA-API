"""M45 仓库道具识别核心：自己拟合网格 + 全库 masked NCC + 双阈值判定。

**为什么要自己识别**：内核 `DepotRecognitionTask` 只能给出 ``classifyType == "MATERIAL"``
的 95 个材料，加上 ``analyze_basic_items()`` 里硬编码的 5 个 id（``4002/4003/4001/3003/4006``，
`DepotRecognitionTask.cpp:45`）。寻访凭证 `7003`、十连寻访凭证 `7004`、高级凭证 `4004`、
资质凭证 `4005`、家具零件 `3401`、源石碎片 `3141` 全是 ``NORMAL``，**内核永远拿不到**
（M45 规格 §2.1）。本模块只做「一页画面 → 道具身份」：不碰设备、网络与数据库。

## 坐标空间

内核 ``DepotMatchData.roi = [0, 0, 1066, 599]``（规格 §2.6）：截图先缩到 ``1066×599``，
模板用**原生尺度**（实测 ``0.95/1.0/1.05`` 三档 24/24 都是 1.0 胜，台账 W0-b），
本模块的一切几何（格心、格距、匹配窗口）都在这个空间里。真机 2560×1440 是它的 2.402×。

## 几何：格距跟内核、相位逐帧拟合

- **格距（pitch）来自本机内核 ``tasks.json``**：``DepotBaseRect.roi = [0, 190, 156, 190]``
  @1280×720，注释写明 ``[x_first(unknown), y_first, x_period, y_period]`` ⇒
  ``x_period = 156 * 1066/1280 = 129.92``、``y_first = y_period = 190 * 599/720 = 158.07``。
  W1 在一张真机帧（``resource/maa-api/debug/interface/2026.10.13-09.22.57.703_raw.png``，
  1280×720、仓库「全部」页）上对 21 个道具做**局部 NCC 全局峰**回归，量到
  ``x: first 86.95 / pitch 129.57``（残差 ≤ 1.92 px）、``y: first 155.05 / pitch 157.79``
  （残差 ≤ 2.67 px）⇒ 格距与内核换算值只差 0.3 px 级，**不需要逐帧重估**。
- **相位（first）必须自己拟合，而且不能抄内核**：内核自己的相位法在这张帧上给
  ``x_first = 105``（偏右约 18 px）⇒ 第 8 列格心 ``1008``、cell roi 右边界
  ``1069 > 1066``，被 `DepotImageAnalyzer::analyze_base_rect` 的 ``include()`` 丢掉，
  内核那一趟**只扫 21 格**（规格 §3.1/§4.3）。按量出来的相位，第 8 列格心是 ``993``、
  右边界 ``1054``，本来就在面板里 ⇒ **拟合之后第 8 列自然成立**，边界钳制只作兜底。
  第一列的实测格心是 ``87``，而内核给的是 ``105``：相位误差 18 px 已经吃掉窗口
  横向 ±20 px 余量的一大半，所以本模块逐帧量相位（:func:`fit_lattice`）。
  > 历史坑：本模块早期把 ``x_first`` 默认值写成 ``104.0``，那其实是 **1280×720 空间**
  > 的数（内核 105）被当成了 1066 空间的数用 —— ``104 * 1066/1280 = 86.6`` 才是本空间的值。
  > 靠窗口余量勉强不漏格，但分数与第 8 列的余量都被白吃掉。现已改成实测值。
- 拟合用的是**画面自己的 NCC 分数面**：先验格线定标 + 在 ``(first, pitch)`` 网格上找
  「验证格最高分之和」最大的那条格线；landmark 不足（< :data:`FIT_MIN_LANDMARKS`）就
  退回先验并在 ``PanelScan.grid_confidence`` 里记 ``insufficient`` —— 不拿一个可疑的格子去猜。

## 匹配：mask 是决定性的

- **mask = 模板灰度 ≥ 1 的像素**（等价内核 ``DepotMatchData.maskRange = [1, 255]``，
  即去掉纯黑角），再抹掉模板**右下 80×50**（等价 ``prepare_cached_templates``）。
  同一格同一位置：带 mask 的 NCC ``0.8978``，去掉 mask 只剩 ``0.1126``（台账 W0-b）。
- 匹配窗口 ``162×132``（宽×高）= 单元格外扩 ``(20,5,20,5)``，与内核 ``match_item`` 的
  ``with_enlarge`` 同口径；窗口越界时**裁剪**不补边（补边会造出「完美匹配」的假高分）。
- 每格对全库 479 张模板打分；模板按中心放进同一个 ``118×117`` 框批量矩阵乘
  （框外 mask 为 0，所以小模板不吃亏）。
- 格子被面板边缘裁掉超过 :data:`CELL_MAX_CLIP` 就记 ``unknown``，不猜（第 8 列只裁
  3–13 px，照常识别；这正是内核丢列与我们不丢列的分界）。

## 判定：双阈值，宁可记「歧义/未知」也不猜

``接受 ⇔ top1 ≥ 0.75 且 (top1 − top2) ≥ 0.15``（默认，可配）。理由（W0-b 实测）：
单看分数分不开 —— 真格最低 ``0.7876`` < 半格伪峰最高 ``0.7907``；加上 margin 才干净分开
—— 真格最低 ``0.1664`` > 半格最高 ``0.1540``。``ap_supply_lt_60/80/100/120`` 这类近重复
模板正是被 margin 拦成「歧义」，而不是给一个错答案。

三种结论：``accepted`` / ``ambiguous``（有候选但没达到双阈值）/ ``unknown``（全库最高分
低于 ``top1_min``，即画面上的美术在模板库里没有对应条目）。

整页接受格数低于 ``DepotScanConfig.min_accepted``（默认 8）时 :class:`PanelScan` 记
``below_min_accepted`` 并加一条 warning —— **本模块只如实报告，写不写库由调用方决定**
（M45 W3 与 M44 的 `agent.depot_min_items` 一起做闸门）。

## 性能

单页 24 格 × 479 模板：**0.82 s**（本机实测，窗口 162×132、单尺度；台账 W0-b 记 0.733 s），
网格拟合再加 **≈ 0.35 s**（分数面 + 查表，见 :func:`fit_lattice`；拟合真的改了格线会再扫一遍，
相位偏得多时为了找 landmark 还要先多扫一遍 ≈ 2.6 s）。
:func:`narrow_candidates` 按格心颜色把候选收窄（W0-b：60 候选 0.314 s），由
:class:`DepotScanConfig` 的 ``max_candidates`` 控制，``0`` ＝ 不筛（默认，保召回）。

## 偏离台账的地方

- **尺度只跑 1.0**（``DEFAULT_SCALES = (1.0,)``）：W0-b 三档实测 24/24 都是 1.0 胜，
  三档是 3 倍代价；``DepotScanConfig.scales`` 仍可传 ``(0.95, 1.0, 1.05)`` 当安全网。
- **阈值/下限还没进设置 API**：全部由 :class:`DepotScanConfig` 承载（W2 接线时再决定
  要不要暴露成 ``account.depot_scan_*``，免得现在加一批没人读的设置项）。
"""

from __future__ import annotations

import json
import logging
import math
import platform

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from maa_api.settings import REPO_ROOT

logger = logging.getLogger(__name__)

__all__ = [
    "CELL_MAX_CLIP",
    "DEFAULT_BOTTOM_RIGHT_CUT",
    "DEFAULT_CELL_ENLARGE",
    "DEFAULT_MARGIN_MIN",
    "DEFAULT_PANEL",
    "DEFAULT_SCALES",
    "DEFAULT_TOP1_MIN",
    "CellResult",
    "ColorFingerprint",
    "DepotScanConfig",
    "ItemBank",
    "ItemGrid",
    "Match",
    "PanelScan",
    "clear_caches",
    "flatten_scan",
    "fit_lattice",
    "load_grid_geometry",
    "load_item_bank",
    "match_window",
    "narrow_candidates",
    "panel_of_frame",
    "scan_panel",
    "score_surface",
]

# --------------------------------------------------------------------------- #
# 常量：来源见模块文档；全部可被 :class:`ItemGrid` / :class:`DepotScanConfig` 覆盖
# --------------------------------------------------------------------------- #

#: 内核工作分辨率（``DepotMatchData.roi``）：本模块的坐标空间。
DEFAULT_PANEL: tuple[int, int] = (1066, 599)

#: 格心先验（@1066×599）。格距从 ``DepotBaseRect`` 换算（156/190 @1280×720）；
#: 相位里 ``x_first`` 内核自己标着 unknown，用真机实测值（见模块文档「几何」）。
DEFAULT_X_FIRST = 86.6
DEFAULT_Y_FIRST = 158.07
DEFAULT_X_PITCH = 129.92
DEFAULT_Y_PITCH = 158.07

#: 单元格边长与匹配窗口外扩（内核 ``templ_size = 183*720//1080 = 122``；
#: 窗口外扩 ``(左, 上, 右, 下)`` 照 ``match_item`` 的 ``with_enlarge``）。
DEFAULT_CELL_SIZE = 122
DEFAULT_CELL_ENLARGE: tuple[int, int, int, int] = (20, 5, 20, 5)

#: 单元格允许被面板边缘裁掉多少像素（超过就记 ``unknown``，不猜）。
#: W0 用内核相位时第 8 列只超 3 px 仍以 ``0.9159`` 命中 ``7002``；
#: 12 px ≈ 单元格的 10%，够容下相位误差，又能挡住真正跑到面板外的列。
CELL_MAX_CLIP = 12

#: mask 抹掉的右下角（模板原生尺度，等价内核 ``prepare_cached_templates``）。
DEFAULT_BOTTOM_RIGHT_CUT: tuple[int, int] = (80, 50)

#: 双阈值（W0-b 实测，见模块文档「判定」）。
DEFAULT_TOP1_MIN = 0.75
DEFAULT_MARGIN_MIN = 0.15

#: 尺度安全网。W0-b 实测 24/24 都是 1.0 胜，默认**只跑 1.0**（三档是 3 倍代价）。
DEFAULT_SCALES: tuple[float, ...] = (1.0,)

#: 拟合时允许的格距偏差（先验的 ±%）：列 ±7.8 px、行 ±9.5 px。
PITCH_TOLERANCE = 0.06
#: 相位搜索半径（先验格心 ± px）与搜索步长（px）。
#: 24 px 是「画面整体滚动」的量级：横向窗口余量本来就有 ±20 px，纵向只有 ±4~8 px，
#: 所以纵向滚动超过几像素就必须靠拟合找回来（见 :func:`_landmarks` 的兜底扫描）。
FIRST_SEARCH_MAX = 24.0
FIRST_STEP = 1.0
PITCH_STEP = 0.5
#: 兜底找 landmark 时把匹配窗口向外扩这么多像素：窗口一大，整体滚动就被窗口自己
#: 吸收掉，分数照样能过 :data:`FIT_LANDMARK_MIN`（代价是窗口面积变大）。
FIT_DISCOVERY_PAD = 24
#: landmark 门槛：低于它就不认为「这一格有模板库里的道具」（真机图上 21/24 及格）。
FIT_LANDMARK_MIN = 0.80
#: landmark 池上限（[`_diverse`] 按「每列最好的 + 每行最好的」挑，去重后取分数最高的这些）。
FIT_LANDMARK_MAX = 12
#: 少于这么多张 landmark 就不拟合，直接用先验。
FIT_MIN_LANDMARKS = 3
#: 每个验证组（一列/一行）最多用几个 landmark。
FIT_VERIFY_PER_GROUP = 2
#: 一条轴最多用几个验证格（每个验证格一次分数面）。
FIT_VERIFY_MAX = 5
#: 采纳拟合值的门槛：验证格最高分之和至少要提高这么多（4 格 ⇒ 每格 0.0125）。
#: masked NCC 在窗口内取最大 ⇒ 分数面是平台，没有门槛就会为 0.0001 的抖动改掉格线。
FIT_IMPROVE_MIN = 0.05

#: 颜色预筛：只有灰度 ≥ 该值的像素参与颜色统计（与匹配 mask 同一口径）。
COLOR_PIXEL_MIN = 1
#: 预筛距离里色相（0–1 循环）与亮度/饱和度的权重。
COLOR_HUE_WEIGHT = 1.0
COLOR_SV_WEIGHT = 0.35

_TEMPLATE_SUBDIR = ("template", "items")
_ITEM_INDEX_NAME = "item_index.json"
_TASKS_RELPATH = Path("tasks") / "tasks.json"


# --------------------------------------------------------------------------- #
# 几何
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ItemGrid:
    """一页道具格子的几何（``1066×599`` 空间，见模块文档「坐标空间」）。"""

    x_first: float = DEFAULT_X_FIRST
    y_first: float = DEFAULT_Y_FIRST
    x_pitch: float = DEFAULT_X_PITCH
    y_pitch: float = DEFAULT_Y_PITCH
    cols: int = 8
    rows: int = 3
    cell_size: int = DEFAULT_CELL_SIZE
    enlarge: tuple[int, int, int, int] = DEFAULT_CELL_ENLARGE
    panel: tuple[int, int] = DEFAULT_PANEL

    @property
    def window(self) -> tuple[int, int]:
        """匹配窗口 ``(宽, 高)``。"""
        left, top, right, bottom = self.enlarge
        return (self.cell_size + left + right, self.cell_size + top + bottom)

    def centers(self) -> list[tuple[int, int, int, int]]:
        """全部格心 ``[(col, row, x, y)]``，``col``/``row`` 从 0 起。"""
        return [
            (
                col,
                row,
                int(round(self.x_first + self.x_pitch * col)),
                int(round(self.y_first + self.y_pitch * row)),
            )
            for row in range(self.rows)
            for col in range(self.cols)
        ]

    def replace(self, **changes: Any) -> ItemGrid:
        """返回改了若干字段的新几何（显式包一层 ``dataclasses.replace``）。"""
        return replace(self, **changes)

    def cell_box(self, x: int, y: int) -> tuple[int, int, int, int]:
        """以 ``(x, y)`` 为格心的**单元格（道具图）**在面板里的框 ``(左, 上, 右, 下)``。"""
        half = self.cell_size // 2
        return (x - half, y - half, x + self.cell_size - half, y + self.cell_size - half)

    def cell_cut(self, x: int, y: int) -> int:
        """单元格被面板边缘裁掉的最大像素数（0 ＝ 完整落在面板里）。"""
        left, top, right, bottom = self.cell_box(x, y)
        return max(-left, -top, right - self.panel[0], bottom - self.panel[1], 0)

    def in_panel(self, x: int, y: int, *, max_cut: int = 0) -> bool:
        """单元格是否落在面板内；``max_cut`` 允许被裁多少像素。

        注意判的是**单元格**（122×122）而不是匹配窗口（162×132）：第 8 列贴右边界，
        按窗口判会把整列判死（这正是内核 ``include()`` 丢列的口径，见模块文档「几何」）。
        """
        return self.cell_cut(x, y) <= max_cut

    def window_in_panel(self, x: int, y: int) -> bool:
        """匹配窗口是否整块落在面板内（不满足时 :func:`match_window` 会裁剪）。"""
        win_w, win_h = self.window
        return (
            x - win_w // 2 >= 0
            and y - win_h // 2 >= 0
            and x + win_w - win_w // 2 <= self.panel[0]
            and y + win_h - win_h // 2 <= self.panel[1]
        )


def _round_int(value: Any, fallback: int) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return fallback


def geometry_from_kernel(payload: Mapping[str, Any]) -> ItemGrid | None:
    """内核 ``tasks.json`` → :class:`ItemGrid`；缺字段返回 ``None``。

    两个格距是**算出来**的：比例取 ``DepotMatchData.roi`` 与游戏坐标（1280×720）之比。
    ``x_first`` 内核注释原文就是 ``unknown``，用实测值兜底、交给拟合修正。
    """
    match = payload.get("DepotMatchData")
    base = payload.get("DepotBaseRect")
    if not isinstance(match, Mapping) or not isinstance(base, Mapping):
        return None
    panel_box = match.get("roi")
    base_box = base.get("roi")
    if not isinstance(panel_box, Sequence) or not isinstance(base_box, Sequence):
        return None
    if len(panel_box) < 4 or len(base_box) < 4:
        return None
    panel_w = _round_int(panel_box[2], 0)
    panel_h = _round_int(panel_box[3], 0)
    # DepotBaseRect.roi 的注释：``[x_first (unknown), y_first, x_period, y_period]``，
    # 即 roi[2] 是列间距、roi[3] 是行间距（两者在 1280×720 空间都是整格尺寸）。
    base_period_x = _round_int(base_box[2], 0)
    base_period_y = _round_int(base_box[3], 0)
    if min(panel_w, panel_h, base_period_x, base_period_y) <= 0:
        return None
    scale_x = panel_w / 1280.0
    scale_y = panel_h / 720.0
    y_first = _round_int(base_box[1], 0) * scale_y
    return ItemGrid(
        x_first=DEFAULT_X_FIRST,
        y_first=float(y_first) if y_first > 0 else DEFAULT_Y_FIRST,
        x_pitch=float(base_period_x * scale_x),
        y_pitch=float(base_period_y * scale_y),
        panel=(panel_w, panel_h),
    )


def _kernel_folder() -> str:
    return {"Darwin": "Darwin", "Linux": "Linux", "Windows": "Win32"}.get(
        platform.system(), platform.system()
    )


def resource_roots() -> list[Path]:
    """按「增量层优先、内核层兜底、repo 快照最后」给出资源根（只读）。

    ``maa-layers/repo`` 是资源更新留下的本机快照，故意不在
    ``services/resource_index.py`` 的运行时链里；模板与索引在干净检出里只可能出现在
    它下面，所以这里做最后兜底。
    """
    layers = REPO_ROOT / "resource" / "maa-layers"
    candidates = [
        layers / "custom" / "resource",
        layers / "cache" / "resource",
        REPO_ROOT / "resource" / "lib" / "maa" / _kernel_folder() / "resource",
        layers / "repo" / "resource",
    ]
    unique: list[Path] = []
    for root in candidates:
        if root not in unique:
            unique.append(root)
    return unique


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:  # pragma: no cover - 坏文件只该降级
        logger.warning("读取内核任务定义失败 %s：%s", path, exc)
        return None


_GEOMETRY_CACHE: dict[str, ItemGrid] = {}


def clear_caches() -> None:
    """清空几何与模板库缓存（换资源、换设置后或测试里用）。"""
    _GEOMETRY_CACHE.clear()
    _BANK_CACHE.clear()


def load_grid_geometry(*, roots: Sequence[Path] | None = None) -> ItemGrid:
    """从本机内核 ``tasks.json`` 读几何；读不到退回实测常量并记 warning。

    退回常量而不是整轮失败：实测常量在本机 v6.18.0 上残差 ≤ 2.3 px。
    """
    cache_key = "default" if roots is None else "custom"
    if cache_key in _GEOMETRY_CACHE and roots is None:
        return _GEOMETRY_CACHE[cache_key]
    for root in roots if roots is not None else resource_roots():
        payload = _read_json(root / _TASKS_RELPATH)
        if isinstance(payload, Mapping):
            grid = geometry_from_kernel(payload)
            if grid is not None:
                if roots is None:
                    _GEOMETRY_CACHE[cache_key] = grid
                return grid
    logger.warning("读不到内核 Depot 几何，退回本机实测常量（1066×599 / 129 / 158）")
    grid = ItemGrid()
    if roots is None:
        _GEOMETRY_CACHE[cache_key] = grid
    return grid


# --------------------------------------------------------------------------- #
# 模板库
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ColorFingerprint:
    """模板的颜色指纹（候选集收窄用）。

    ``hue`` 是 :data:`COLOR_BINS` 桶的饱和度加权色相直方图，``gray`` 是「有内容」
    像素的平均灰度。用直方图而不是单个均值色：图标常有渐变与描边，均值会把两种
    颜色平均成画面里根本不存在的第三种。
    """

    hue: np.ndarray
    gray: float
    pixels: int
    #: 低饱和度时色相不可靠，退化成灰度比较。
    chromatic: bool


@dataclass(frozen=True, slots=True)
class ItemBank:
    """一库模板：灰度、mask 与 NCC 的预计算量。

    ``gray`` / ``mask`` 形状 ``(n, hh, ww)``：``hh×ww`` 是全库最大模板尺寸，模板按
    中心放进同一个框，批量矩阵乘才能对齐。``fps`` 是每张模板的颜色指纹。
    """

    ids: tuple[str, ...]
    names: tuple[str, ...]
    gray: np.ndarray
    mask: np.ndarray
    msum: np.ndarray
    t_bar: np.ndarray
    var_t: np.ndarray
    fps: tuple[ColorFingerprint, ...] = ()
    scale: float = 1.0

    def __len__(self) -> int:
        return len(self.ids)

    @property
    def pad(self) -> tuple[int, int]:
        return int(self.mask.shape[1]), int(self.mask.shape[2])


#: 色相直方图桶数。
COLOR_BINS = 12


def fingerprint_rgb(rgb: np.ndarray, mask: np.ndarray | None = None) -> ColorFingerprint:
    """RGB 图（+ 可选 mask）→ :class:`ColorFingerprint`。

    ``mask`` 为 ``None`` 时用「灰度 ≥ :data:`COLOR_PIXEL_MIN`」的像素，和匹配用的
    mask 同一口径：黑角留白不参与颜色，否则每张模板都会被自己的黑边稀释成同一个颜色。
    """
    array = np.asarray(rgb, dtype=np.float32)
    if array.ndim == 2:
        array = np.repeat(array[:, :, None], 3, axis=2)
    gray = array.max(axis=2)
    keep = gray >= COLOR_PIXEL_MIN if mask is None else np.asarray(mask, dtype=bool)
    total = int(keep.sum())
    hue = np.zeros(COLOR_BINS, np.float64)
    if total == 0:
        return ColorFingerprint(hue=hue, gray=0.0, pixels=0, chromatic=False)
    red = array[:, :, 0][keep]
    green = array[:, :, 1][keep]
    blue = array[:, :, 2][keep]
    high = np.maximum(np.maximum(red, green), blue)
    low = np.minimum(np.minimum(red, green), blue)
    delta = high - low
    sat = np.where(high > 0, delta / np.maximum(high, 1e-6), 0.0)
    weight = sat * (high / 255.0)
    hue_raw = np.zeros_like(delta)
    is_red = (high == red) & (delta > 1e-6)
    is_green = (high == green) & (delta > 1e-6) & ~is_red
    is_blue = (high == blue) & (delta > 1e-6) & ~is_red & ~is_green
    hue_raw[is_red] = ((green - blue)[is_red] / delta[is_red]) % 6.0
    hue_raw[is_green] = ((blue - red)[is_green] / delta[is_green]) + 2.0
    hue_raw[is_blue] = ((red - green)[is_blue] / delta[is_blue]) + 4.0
    index = np.clip((hue_raw / 6.0 * COLOR_BINS).astype(np.int64), 0, COLOR_BINS - 1)
    np.add.at(hue, index, weight)
    if hue.sum() > 0:
        hue /= hue.sum()
    return ColorFingerprint(
        hue=hue,
        gray=float((0.299 * red + 0.587 * green + 0.114 * blue).mean()),
        pixels=total,
        chromatic=bool((sat > 0.25).mean() > 0.05),
    )


def _hue_distance(left: np.ndarray, right: np.ndarray) -> float:
    """两个归一化色相直方图的循环推土距离（闭式解，桶数 −1 归一）。"""
    diff = np.cumsum(left - right)
    return float(np.abs(diff).sum()) / max(len(left) - 1, 1)


def narrow_candidates(
    cell_rgb: np.ndarray,
    templates: Sequence[ColorFingerprint],
    limit: int,
) -> list[int]:
    """按格心颜色把候选模板收窄到 ``limit`` 个；``limit <= 0`` 时返回全部。

    分数仍然是全 mask NCC，预筛只决定**谁参与打分**（W0-b：60 候选 314 ms vs 全库 733 ms）。
    默认关闭：收窄是召回率的赌注，只有实测确认不掉召回才适合打开。
    """
    total = len(templates)
    if limit <= 0 or limit >= total:
        return list(range(total))
    cell = fingerprint_rgb(cell_rgb, None)
    if cell.pixels == 0:
        return list(range(total))
    scored: list[tuple[float, int]] = []
    for index, template in enumerate(templates):
        if template.pixels == 0:
            scored.append((float("inf"), index))
            continue
        if cell.chromatic and template.chromatic:
            distance = COLOR_HUE_WEIGHT * _hue_distance(cell.hue, template.hue)
        else:
            distance = 0.0
        distance += COLOR_SV_WEIGHT * abs(cell.gray - template.gray) / 255.0
        scored.append((distance, index))
    scored.sort()
    return sorted(index for _distance, index in scored[:limit])


def _item_catalog(roots: Sequence[Path]) -> dict[str, dict[str, Any]]:
    """第一份能读的 ``item_index.json``（增量层优先，与 ``capture.load_item_catalog`` 同序）。"""
    for root in roots:
        payload = _read_json(root / _ITEM_INDEX_NAME)
        if isinstance(payload, Mapping) and payload:
            return {
                str(key): dict(value)
                for key, value in payload.items()
                if isinstance(value, Mapping)
            }
    return {}


def _find_template(roots: Sequence[Path], icon: str) -> Path | None:
    """模板文件：第一个存在的层胜出（custom 覆盖内核，与内核加载资源同序）。"""
    if not icon or "/" in icon or "\\" in icon or ".." in icon:
        return None
    for root in roots:
        candidate = root.joinpath(*_TEMPLATE_SUBDIR, icon)
        if candidate.is_file():
            return candidate
    return None


_BANK_CACHE: dict[tuple[float, str], ItemBank] = {}


def load_item_bank(
    *,
    scale: float = 1.0,
    roots: Sequence[Path] | None = None,
    catalog: Mapping[str, Mapping[str, Any]] | None = None,
    use_cache: bool = True,
) -> ItemBank:
    """加载 ``item_index.json`` 登记的全部道具模板（**只读**，不拷贝进版本库）。

    :param scale: 模板缩放；``1.0`` ＝ 原生尺度（W0-b 实测最优）。
    :param roots: 覆盖资源根顺序（测试用）。
    :param catalog: 覆盖物品索引（测试用；键是 ``item_id``，值给 ``icon`` / ``name``）。
    :param use_cache: 进程内缓存（默认开；换资源后调 :func:`clear_caches`）。
    :raises ValueError: 一张模板都没找到 —— 调用方据此判「整轮失败」而不是空手入库。
    """
    cache_key = (float(scale), "default")
    cacheable = use_cache and roots is None and catalog is None
    if cacheable and cache_key in _BANK_CACHE:
        return _BANK_CACHE[cache_key]
    search_roots = list(roots) if roots is not None else resource_roots()
    entries = dict(catalog) if catalog is not None else _item_catalog(search_roots)
    ids: list[str] = []
    names: list[str] = []
    grays: list[np.ndarray] = []
    masks: list[np.ndarray] = []
    fps: list[ColorFingerprint] = []
    for item_id in sorted(entries):
        entry = entries[item_id]
        icon = str(entry.get("icon") or f"{item_id}.png")
        path = _find_template(search_roots, icon)
        if path is None:
            continue
        image = Image.open(path).convert("RGB")
        if scale != 1.0:
            image = image.resize(
                (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                Image.Resampling.BILINEAR,
            )
        rgb = np.asarray(image, dtype=np.float32)
        gray = np.asarray(image.convert("L"), dtype=np.float32).copy()
        height, width = gray.shape
        cut_w, cut_h = DEFAULT_BOTTOM_RIGHT_CUT
        if height > cut_h and width > cut_w:
            # 右下角是游戏里的数量角标区，模板与真机不完全一致（W0-b 口径）。
            gray[height - cut_h:, width - cut_w:] = 0.0
        mask = (gray >= 1.0).astype(np.float32)
        ids.append(item_id)
        name = entry.get("name")
        names.append(str(name) if name else item_id)
        grays.append(gray)
        masks.append(mask)
        fps.append(fingerprint_rgb(rgb, mask))
    if not grays:
        raise ValueError("没有加载到任何道具模板：检查本机 resource/ 是否含 template/items")
    hh = max(item.shape[0] for item in grays)
    ww = max(item.shape[1] for item in grays)
    gray_box = np.zeros((len(grays), hh, ww), np.float32)
    mask_box = np.zeros((len(grays), hh, ww), np.float32)
    for index, (gray, mask) in enumerate(zip(grays, masks)):
        oy, ox = (hh - gray.shape[0]) // 2, (ww - gray.shape[1]) // 2
        gray_box[index, oy:oy + gray.shape[0], ox:ox + gray.shape[1]] = gray
        mask_box[index, oy:oy + gray.shape[0], ox:ox + gray.shape[1]] = mask
    gray_box *= mask_box
    flat_mask = mask_box.reshape(len(grays), -1)
    flat_gray = gray_box.reshape(len(grays), -1)
    msum = flat_mask.sum(axis=1).astype(np.float64)
    t_bar = flat_gray.sum(axis=1).astype(np.float64) / msum
    var_t = (flat_gray.astype(np.float64) ** 2).sum(axis=1) - msum * t_bar * t_bar
    bank = ItemBank(
        ids=tuple(ids),
        names=tuple(names),
        gray=gray_box,
        mask=mask_box,
        msum=msum,
        t_bar=t_bar,
        var_t=var_t,
        fps=tuple(fps),
        scale=scale,
    )
    if cacheable:
        _BANK_CACHE[cache_key] = bank
    return bank


# --------------------------------------------------------------------------- #
# 打分
# --------------------------------------------------------------------------- #
def match_window(
    frame: np.ndarray, cx: int, cy: int, window: tuple[int, int]
) -> np.ndarray | None:
    """以 ``(cx, cy)`` 为中心裁匹配窗口；越界**裁剪**（不补边），完全越界返回 ``None``。"""
    win_w, win_h = window
    left, top = cx - win_w // 2, cy - win_h // 2
    height, width = frame.shape[:2]
    x0, y0 = max(left, 0), max(top, 0)
    x1, y1 = min(left + win_w, width), min(top + win_h, height)
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return None
    return np.ascontiguousarray(frame[y0:y1, x0:x1])


def _size_groups(bank: ItemBank) -> list[np.ndarray]:
    """按 mask 尺寸分组：同尺寸模板共用一份滑窗矩阵（本机归一后通常只有一组）。"""
    groups: dict[tuple[int, int], list[int]] = {}
    for index in range(len(bank.ids)):
        groups.setdefault(bank.pad, []).append(index)
    return [np.asarray(members, dtype=np.int64) for members in groups.values()]


def _ncc_matrix(window: np.ndarray, bank: ItemBank, members: np.ndarray) -> np.ndarray:
    """``(窗口内可放置位置数, len(members))`` 的 masked NCC 矩阵。

    位置是**模板左上角**在窗口里的全部落点（模板必须整块落在窗口内，不补边）。
    """
    hh, ww = bank.pad
    win_h, win_w = window.shape
    if win_h < hh or win_w < ww:
        return np.full((0, len(members)), -np.inf)
    positions = (win_h - hh + 1) * (win_w - ww + 1)
    patches = np.lib.stride_tricks.as_strided(
        window,
        shape=(win_h - hh + 1, win_w - ww + 1, hh, ww),
        strides=(window.strides[0], window.strides[1], window.strides[0], window.strides[1]),
        writeable=False,
    ).reshape(positions, hh * ww)
    flat_mask = bank.mask.reshape(len(bank.ids), -1)[members]
    flat_gray = bank.gray.reshape(len(bank.ids), -1)[members]
    msum = bank.msum[members]
    t_bar = bank.t_bar[members]
    var_t = bank.var_t[members]
    sum_i = patches @ flat_mask.T
    sum_i2 = (patches * patches) @ flat_mask.T
    dots = patches @ flat_gray.T
    i_bar = sum_i / msum
    var_i = sum_i2 - msum * i_bar * i_bar
    numerator = dots - msum * i_bar * t_bar
    denominator = np.sqrt(np.maximum(var_i, 1e-9) * np.maximum(var_t, 1e-9))
    return numerator / denominator


def _selected_indices(bank: ItemBank, candidates: Sequence[int] | None) -> np.ndarray:
    if candidates is None:
        return np.arange(len(bank.ids), dtype=np.int64)
    return np.asarray(sorted({int(index) for index in candidates}), dtype=np.int64)


def score_templates(
    window: np.ndarray,
    bank: ItemBank,
    candidates: Sequence[int] | None = None,
) -> np.ndarray:
    """全库（或收窄后的候选集）分数向量；未参与打分的模板是 ``-inf``。"""
    scores = np.full(len(bank.ids), -np.inf)
    selected = _selected_indices(bank, candidates)
    if selected.size == 0:
        return scores
    wanted = np.zeros(len(bank.ids), dtype=bool)
    wanted[selected] = True
    for members in _size_groups(bank):
        keep = members[wanted[members]]
        if keep.size:
            scores[keep] = _ncc_matrix(window, bank, keep).max(axis=0)
    return scores


def score_surface(
    window: np.ndarray,
    bank: ItemBank,
    candidates: Sequence[int] | None = None,
) -> np.ndarray:
    """窗口内**每个可放置位置**上的全模板最高分，形状 ``(行, 列)``。

    拟合用：一张分数面就能读出「格心放在哪儿分数最高」，不必为每条候选格线重跑一遍
    NCC（见 :func:`fit_lattice`）。空白位置（模板放不下）是 ``-inf``。
    """
    hh, ww = bank.pad
    shape = (window.shape[0] - hh + 1, window.shape[1] - ww + 1)
    surface = np.full(shape, -np.inf, dtype=np.float64)
    if shape[0] <= 0 or shape[1] <= 0:
        return surface
    selected = _selected_indices(bank, candidates)
    if selected.size == 0:
        return surface
    wanted = np.zeros(len(bank.ids), dtype=bool)
    wanted[selected] = True
    for members in _size_groups(bank):
        keep = members[wanted[members]]
        if keep.size:
            per_position = _ncc_matrix(window, bank, keep).max(axis=1)
            surface = np.maximum(surface, per_position.reshape(shape))
    return surface


@dataclass(frozen=True, slots=True)
class Match:
    """一条候选（``rank`` 从 0 起）。"""

    item_id: str
    name: str
    score: float
    rank: int


def top_matches(scores: np.ndarray, bank: ItemBank, *, count: int = 3) -> tuple[Match, ...]:
    """分数向量 → 前 ``count`` 条；非有限分数（未参与打分）不出现。"""
    finite = np.isfinite(scores)
    if not finite.any():
        return ()
    order = np.argsort(-np.where(finite, scores, -np.inf))[:count]
    return tuple(
        Match(
            item_id=bank.ids[int(index)],
            name=bank.names[int(index)],
            score=float(scores[int(index)]),
            rank=rank,
        )
        for rank, index in enumerate(order)
    )


def _merge_matches(groups: Sequence[Sequence[Match]], count: int) -> tuple[Match, ...]:
    """多尺度候选合并：同一 ``item_id`` 取最高分，再重排名次。"""
    best: dict[str, Match] = {}
    for group in groups:
        for match in group:
            current = best.get(match.item_id)
            if current is None or match.score > current.score:
                best[match.item_id] = Match(match.item_id, match.name, match.score, 0)
    ordered = sorted(best.values(), key=lambda item: -item.score)[:count]
    return tuple(
        Match(item.item_id, item.name, item.score, rank) for rank, item in enumerate(ordered)
    )


def _classify(
    matches: Sequence[Match],
    *,
    top1_min: float,
    margin_min: float,
) -> str:
    """双阈值判定：见模块文档「判定」。候选为空也算 ``unknown``。"""
    if not matches:
        return "unknown"
    top1 = matches[0].score
    if top1 < top1_min:
        return "unknown"
    if len(matches) < 2:
        return "accepted"
    return "accepted" if top1 - matches[1].score >= margin_min else "ambiguous"


# --------------------------------------------------------------------------- #
# 网格拟合：量相位（内核不给），印证格距（内核给，但换版本要能跟着走）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class _Landmark:
    """拟合用的「画面里确实有」的道具：模板索引 + 它在先验格线上的格子 + 分数。"""

    index: int
    col: int
    row: int
    score: float


def fit_lattice(
    frame: np.ndarray,
    bank: ItemBank,
    grid: ItemGrid,
    *,
    cells: Sequence[CellResult] | None = None,
) -> tuple[ItemGrid, str, tuple[dict[str, Any], ...]]:
    """拟合格心相位与格距；返回 ``(几何, 置信度, 明细)``。

    置信度三档：``fitted``（改了格线）/ ``prior``（先验本来就是最优，或改进不达门槛）/
    ``insufficient``（landmark 不够，没拟合）。

    **验证格就是 landmark 自己所在的那几格**，目标值 = 这些道具在候选格线上的最高分之和
    （左边一列 + 右边一列 / 每行一格，见 :func:`_verify_landmarks`）。这样每格都是
    「一个确实在画面里的模板」，而不是拿一堆不相干的模板去凑分。

    **为什么是「分数之和」而不是「找峰再回归」**：masked NCC 在窗口内取全位置最大，
    一个道具的「格心 → 最高分」是一条**平台**（宽度 = 窗口余量：横向 ±20 px、纵向 ±4~8 px）
    而不是尖峰，对平台做回归会得到任意斜率；直接问「哪条格线的总分最高」没有这个问题 ——
    平台内部并列时先验靠 :data:`FIT_IMPROVE_MIN` 胜出，只有先验真的跳出平台（画面滚动、
    分辨率变化、内核换布局）才会改。

    **每格只跑一遍 NCC**：先用 :func:`_axis_profile` 把验证格的分数面压成「格心 → 最高分」
    的剖面，再在 ``(first, pitch)`` 网格上查表。逐候选重跑 NCC 是 49×32 个候选 × 4 个
    验证格 ≈ 6000 次全格打分（本机实测几十秒），查表版本与「只算先验那一条格线」同量级，
    实测 ≈ 0.15 s。

    :param frame: 面板灰度图（``599×1066``，见 :func:`panel_of_frame`）—— 几何都在这个空间。
    :param cells: :func:`scan_panel` 第一趟的结果（landmark 从中挑，省掉一次全页扫描）；
        挑不够时仍会自己扫（见 :func:`_landmarks`）。
    """
    landmarks = _landmarks(frame, bank, grid, cells=cells)
    if len(landmarks) < FIT_MIN_LANDMARKS:
        return (
            grid,
            "insufficient",
            (
                {
                    "axis": "both",
                    "reason": f"分数 ≥ {FIT_LANDMARK_MIN} 的道具只有 {len(landmarks)} 个，不够拟合",
                    "adjusted": False,
                },
            ),
        )
    details: list[dict[str, Any]] = []
    confidence = "prior"
    # 先竖后横：横向验证窗要落在已经对齐的行心上。
    for axis in ("y", "x"):
        grid, entry = _fit_axis(frame, bank, grid, landmarks, axis=axis)
        details.append(entry)
        if entry["adjusted"]:
            confidence = "fitted"
    return grid, confidence, tuple(details)


def _landmarks(
    frame: np.ndarray,
    bank: ItemBank,
    grid: ItemGrid,
    *,
    cells: Sequence[CellResult] | None = None,
    limit: int = FIT_LANDMARK_MAX,
) -> tuple[_Landmark, ...]:
    """「画面里确实有」的道具 —— 拟合的探针（landmark），分数高的优先。

    三级取证，越往后越贵、只在前面凑不够时才走：

    1. 先验格心的 top-1 分数 ≥ :data:`FIT_LANDMARK_MIN`（:func:`scan_panel` 会把第一趟的
       结果直接传进来，这一级免费；独立调用时自己整页扫一遍，≈ 0.9 s）；
    2. 还不够 ⇒ 说明相位偏了（纵向窗口余量只有 ±4~8 px）。把匹配窗口向外扩
       :data:`FIT_DISCOVERY_PAD`、只扫首/中/末三列 × 全部行：窗口一大，整体滚动被窗口
       自己吸收，分数照样过门槛（≈ 2.6 s，只在相位偏了的时候付）；
    3. 还是不够 ⇒ 交给调用方按「拟合不出来」处理（``insufficient``），不硬凑。

    按名字随手抽模板当探针不行：W1 实测抽出来的 12 张全不在画面上，分数面平在 0.45，
    拿去搜格子只会搜到噪声。
    """
    found = _landmarks_from_cells(cells, bank) if cells is not None else []
    # 传了 cells（:func:`scan_panel` 的第一趟）就等于已经做过第一级，不再重扫一遍。
    if len(found) < FIT_MIN_LANDMARKS and cells is None:
        found.extend(_landmarks_by_scan(frame, bank, grid, rows=range(grid.rows)))
    if len(found) < FIT_MIN_LANDMARKS:
        found.extend(
            _landmarks_by_scan(
                frame,
                bank,
                grid,
                rows=range(grid.rows),
                cols=_discovery_cols(grid),
                pad=FIT_DISCOVERY_PAD,
            )
        )
    best: dict[int, _Landmark] = {}
    for landmark in found:
        if landmark.index not in best or landmark.score > best[landmark.index].score:
            best[landmark.index] = landmark
    return tuple(_diverse(best.values(), limit=limit))


def _diverse(landmarks: Iterable[_Landmark], *, limit: int) -> list[_Landmark]:
    """挑位置**分散**的 landmark：每列分数最高的 + 每行分数最高的，去重后取前 ``limit``。

    分散是硬要求，不是锦上添花：横向窗口余量有 ±20 px，只有列号相差 ≥ 3 的两个
    landmark 才能把格距差异放大到分数看得见；行距则要靠首末两行。
    """
    chosen: dict[tuple[int, int], _Landmark] = {}
    for attribute in ("col", "row"):
        groups: dict[int, list[_Landmark]] = {}
        for landmark in landmarks:
            groups.setdefault(getattr(landmark, attribute), []).append(landmark)
        for group in groups.values():
            top = max(group, key=lambda lm: lm.score)
            chosen[(top.col, top.row)] = top
    ordered = sorted(chosen.values(), key=lambda lm: (-lm.score, lm.col, lm.row))
    return ordered[:limit]


def _landmarks_from_cells(
    cells: Sequence[CellResult], bank: ItemBank
) -> list[_Landmark]:
    """扫过的格子结果 → landmark（只留 top-1 分数 ≥ :data:`FIT_LANDMARK_MIN` 的）。"""
    index_of = {item_id: index for index, item_id in enumerate(bank.ids)}
    found: list[_Landmark] = []
    for cell in cells:
        if not cell.matches or cell.matches[0].score < FIT_LANDMARK_MIN:
            continue
        top = cell.matches[0]
        index = index_of.get(top.item_id)
        if index is not None:
            found.append(_Landmark(index=index, col=cell.col, row=cell.row, score=top.score))
    return found


def _discovery_cols(grid: ItemGrid) -> tuple[int, ...]:
    """兜底扫描用的列：首/中/末三列 —— 相邻列给不出格距的力臂（见 :func:`_diverse`）。"""
    return tuple(sorted({0, grid.cols // 2, max(grid.cols - 1, 0)}))


def _landmarks_by_scan(
    frame: np.ndarray,
    bank: ItemBank,
    grid: ItemGrid,
    *,
    rows: Sequence[int],
    cols: Sequence[int] | None = None,
    pad: int = 0,
) -> list[_Landmark]:
    """按给定行（窗口外扩 ``pad``）扫一遍，返回分数 ≥ :data:`FIT_LANDMARK_MIN` 的格子。

    分数高只说明「画面里有这个道具」，位置仍然按先验格线记 —— 相位偏了正是拟合要修的，
    窗口外扩到 ``pad`` 量级时即使整体滚动过也照样能认出来。
    """
    found: list[_Landmark] = []
    win_w, win_h = grid.window
    for row in rows:
        cy = int(round(grid.y_first + grid.y_pitch * row))
        for col in range(grid.cols) if cols is None else cols:
            cx = int(round(grid.x_first + grid.x_pitch * col))
            if not grid.in_panel(cx, cy, max_cut=CELL_MAX_CLIP + pad):
                continue
            cell = match_window(frame, cx, cy, (win_w + 2 * pad, win_h + 2 * pad))
            if cell is None:
                continue
            scores = score_templates(cell, bank)
            finite = np.isfinite(scores)
            if not finite.any():
                continue
            index = int(np.argmax(np.where(finite, scores, -np.inf)))
            score = float(scores[index])
            if score >= FIT_LANDMARK_MIN:
                found.append(_Landmark(index=index, col=col, row=row, score=score))
    return found


def _verify_landmarks(
    landmarks: Sequence[_Landmark], *, axis: str
) -> list[_Landmark]:
    """挑出用来定这条轴的验证格。

    - ``axis="x"``：取**列号最小与最大**的两列（格距的力臂最长），每列最多
      :data:`FIT_VERIFY_PER_GROUP` 个（不同行，抵抗单格噪声）；只有一列时也照样拟合相位，
      只是格距失去力臂、大概率维持先验。
    - ``axis="y"``：每行各取一个（最多 :data:`FIT_VERIFY_PER_GROUP` 个取分数最高的），
      行距由首末两行定。
    """
    groups: dict[int, list[_Landmark]] = {}
    for landmark in landmarks:
        groups.setdefault(landmark.col if axis == "x" else landmark.row, []).append(landmark)
    keys = sorted(groups)
    if axis == "y" or len(keys) == 1:
        return [max(groups[key], key=lambda lm: lm.score) for key in keys]
    picks = _top_of(groups[keys[0]]) + _top_of(groups[keys[-1]])
    if len(keys) > 2:
        middle = keys[len(keys) // 2]
        picks += _top_of(groups[middle])[:1]
    return picks[:FIT_VERIFY_MAX]


def _top_of(group: Sequence[_Landmark]) -> list[_Landmark]:
    return sorted(group, key=lambda lm: -lm.score)[:FIT_VERIFY_PER_GROUP]


def _probe_bank(bank: ItemBank, probes: Sequence[int]) -> ItemBank:
    """只留探针的临时 :class:`ItemBank`（分数面一个验证格只需一张模板）。"""
    members = np.asarray(sorted(set(int(index) for index in probes)), dtype=np.int64)
    return ItemBank(
        ids=tuple(bank.ids[index] for index in members),
        names=tuple(bank.names[index] for index in members),
        gray=bank.gray[members],
        mask=bank.mask[members],
        msum=bank.msum[members],
        t_bar=bank.t_bar[members],
        var_t=bank.var_t[members],
        fps=tuple(bank.fps[index] for index in members),
        scale=bank.scale,
    )


def _sliding_max_1d(values: np.ndarray, width: int) -> np.ndarray:
    """滑动窗口最大值，长度 ``len(values) - width + 1``；``width`` 放不下时给空数组。"""
    if width <= 0 or values.size < width:
        return np.full(0, -np.inf)
    return np.lib.stride_tricks.sliding_window_view(values, width).max(axis=-1)


def _axis_profile(
    frame: np.ndarray,
    bank: ItemBank,
    *,
    axis: str,
    window: tuple[int, int],
    fixed_center: int,
    moving_lo: int,
    moving_hi: int,
) -> tuple[np.ndarray, int]:
    """一个验证格的「格心 → 该格最高分」剖面：``profile[k]`` 对应格心 ``origin + k``。

    ``axis="x"`` 时格心横坐标在 ``[moving_lo, moving_hi]`` 里走、纵坐标固定为
    ``fixed_center``（``axis="y"`` 反之）。做法是先算一块分数面（:func:`score_surface`），
    再沿固定方向取最大、沿移动方向做滑动最大 —— 于是**任意**格心的分数都只是一次查表，
    候选格线再多也不用重跑 NCC。

    窗口被面板边缘裁掉时，剖面里对应位置的分数自然变低（可用位置变少），完全放不下是
    ``-inf``，调用方据此淘汰该候选。
    """
    hh, ww = bank.pad
    win_w, win_h = window
    count = moving_hi - moving_lo + 1
    if count <= 0:
        return np.full(0, -np.inf), moving_lo
    if axis == "x":
        span, collapse_axis = win_w - ww + 1, 0
        y_lo, n_y = fixed_center - win_h // 2, win_h - hh + 1
        x_lo, n_x = moving_lo - win_w // 2, count + win_w - ww
    else:
        span, collapse_axis = win_h - hh + 1, 1
        x_lo, n_x = fixed_center - win_w // 2, win_w - ww + 1
        y_lo, n_y = moving_lo - win_h // 2, count + win_h - hh
    values = np.full((n_y, n_x), -np.inf, dtype=np.float64)
    vy0, vy1 = max(y_lo, 0), min(y_lo + n_y, frame.shape[0] - hh + 1)
    vx0, vx1 = max(x_lo, 0), min(x_lo + n_x, frame.shape[1] - ww + 1)
    if vy1 > vy0 and vx1 > vx0:
        region = frame[vy0:vy1 + hh - 1, vx0:vx1 + ww - 1]
        values[vy0 - y_lo:vy1 - y_lo, vx0 - x_lo:vx1 - x_lo] = score_surface(region, bank)
    return _sliding_max_1d(values.max(axis=collapse_axis), span), moving_lo


def _first_candidates(prior_first: float) -> list[float]:
    """相位候选：先验 ±:data:`FIRST_SEARCH_MAX`，步长 :data:`FIRST_STEP`。"""
    offsets = np.arange(-FIRST_SEARCH_MAX, FIRST_SEARCH_MAX + 1e-9, FIRST_STEP)
    return [round(float(prior_first + offset), 2) for offset in offsets]


def _pitch_candidates(prior_pitch: float) -> list[float]:
    """格距候选：先验 ±:data:`PITCH_TOLERANCE`，步长 :data:`PITCH_STEP`。"""
    values = np.arange(
        prior_pitch * (1.0 - PITCH_TOLERANCE),
        prior_pitch * (1.0 + PITCH_TOLERANCE) + 1e-9,
        PITCH_STEP,
    )
    return [round(float(value), 3) for value in values]


def _fit_axis(
    frame: np.ndarray,
    bank: ItemBank,
    grid: ItemGrid,
    landmarks: Sequence[_Landmark],
    *,
    axis: str,
) -> tuple[ItemGrid, dict[str, Any]]:
    """一个轴的 ``(first, pitch)`` 搜索；只有比先验高过 :data:`FIT_IMPROVE_MIN` 才采纳。"""
    if axis == "x":
        prior_first, prior_pitch = grid.x_first, grid.x_pitch
        index_of = lambda landmark: landmark.col  # noqa: E731 - 轴上的下标
    else:
        prior_first, prior_pitch = grid.y_first, grid.y_pitch
        index_of = lambda landmark: landmark.row  # noqa: E731
    probes: list[tuple[int, int, np.ndarray, int]] = []
    for landmark in _verify_landmarks(landmarks, axis=axis):
        index = index_of(landmark)
        if axis == "x":
            fixed = int(round(grid.y_first + grid.y_pitch * landmark.row))
        else:
            fixed = int(round(grid.x_first + grid.x_pitch * landmark.col))
        lo = int(prior_first - FIRST_SEARCH_MAX + index * prior_pitch * (1.0 - PITCH_TOLERANCE))
        hi = int(prior_first + FIRST_SEARCH_MAX + index * prior_pitch * (1.0 + PITCH_TOLERANCE))
        profile, origin = _axis_profile(
            frame,
            _probe_bank(bank, (landmark.index,)),
            axis=axis,
            window=grid.window,
            fixed_center=fixed,
            moving_lo=lo,
            moving_hi=hi,
        )
        if profile.size:
            probes.append((index, fixed, profile, origin))

    def evaluate(first: float, pitch: float) -> float:
        total = 0.0
        for index, fixed, profile, origin in probes:
            moving = int(round(first + pitch * index))
            cx, cy = (moving, fixed) if axis == "x" else (fixed, moving)
            # 格子跑到面板外（裁掉超过 CELL_MAX_CLIP）就不算这条格线 —— 内核丢第 8 列
            # 就是这么丢的，我们只在真的放不下时才放弃。
            if not grid.in_panel(cx, cy, max_cut=CELL_MAX_CLIP):
                return -math.inf
            offset = moving - origin
            if offset < 0 or offset >= profile.size:
                return -math.inf
            value = float(profile[offset])
            if not math.isfinite(value):
                return -math.inf
            total += value
        return total

    prior_score = evaluate(prior_first, prior_pitch)
    best_score, best_first, best_pitch = prior_score, prior_first, prior_pitch
    for pitch in _pitch_candidates(prior_pitch):
        for first in _first_candidates(prior_first):
            score = evaluate(first, pitch)
            if score > best_score + 1e-9:
                best_score, best_first, best_pitch = score, first, pitch
    if not math.isfinite(best_score):
        improved, adjusted = 0.0, False
    else:
        improved = math.inf if not math.isfinite(prior_score) else best_score - prior_score
        adjusted = improved >= FIT_IMPROVE_MIN and (
            best_first != prior_first or best_pitch != prior_pitch
        )
    entry: dict[str, Any] = {
        "axis": axis,
        "prior_first": round(prior_first, 3),
        "prior_pitch": round(prior_pitch, 3),
        "first": round(best_first if adjusted else prior_first, 3),
        "pitch": round(best_pitch if adjusted else prior_pitch, 3),
        "prior_score": round(prior_score, 4) if math.isfinite(prior_score) else None,
        "best_score": round(best_score, 4) if math.isfinite(best_score) else None,
        "improved": round(improved, 4) if math.isfinite(improved) else None,
        "cells": len(probes),
        "adjusted": adjusted,
    }
    if not adjusted:
        return grid, entry
    if axis == "x":
        return grid.replace(x_first=best_first, x_pitch=best_pitch), entry
    return grid.replace(y_first=best_first, y_pitch=best_pitch), entry


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class CellResult:
    """一格的结论（``status`` 见模块文档「判定」）。"""

    col: int
    row: int
    cx: int
    cy: int
    status: str
    matches: tuple[Match, ...] = ()
    in_bounds: bool = True
    clipped: bool = False
    scale: float = 1.0

    @property
    def item_id(self) -> str | None:
        return self.matches[0].item_id if self.status == "accepted" and self.matches else None

    @property
    def top1(self) -> float:
        return self.matches[0].score if self.matches else float("nan")

    @property
    def margin(self) -> float:
        if len(self.matches) < 2:
            return float("nan")
        return self.matches[0].score - self.matches[1].score

    def to_dict(self) -> dict[str, Any]:
        return {
            "col": self.col + 1,
            "row": self.row + 1,
            "cx": self.cx,
            "cy": self.cy,
            "status": self.status,
            "in_bounds": self.in_bounds,
            "clipped": self.clipped,
            "item_id": self.item_id,
            "top1": None if not self.matches else round(self.top1, 5),
            "margin": None if len(self.matches) < 2 else round(self.margin, 5),
            "scale": self.scale,
            "top": [
                {"item_id": m.item_id, "name": m.name, "score": round(m.score, 5)}
                for m in self.matches
            ],
        }


@dataclass(frozen=True, slots=True)
class PanelScan:
    """一页的识别结果（纯数据，可直接进日志与识别记录）。"""

    grid: ItemGrid
    grid_confidence: str
    cells: tuple[CellResult, ...] = ()
    scale: float = 1.0
    #: 配置里的整页接受格数下限（``0`` ＝ 不检查）。
    min_accepted: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def _count(self, status: str) -> int:
        return sum(1 for cell in self.cells if cell.status == status)

    @property
    def accepted(self) -> int:
        return self._count("accepted")

    @property
    def ambiguous(self) -> int:
        return self._count("ambiguous")

    @property
    def unknown(self) -> int:
        return self._count("unknown")

    @property
    def below_min_accepted(self) -> bool:
        """接受格数是否低于下限 —— **调用方据此决定不写库**（本模块只报告）。"""
        return bool(self.min_accepted) and self.accepted < self.min_accepted

    @property
    def item_ids(self) -> list[str]:
        """接受的 ``item_id``（按分数降序，重复取最高分那次）。"""
        scored: dict[str, float] = {}
        for cell in self.cells:
            if cell.status == "accepted" and cell.matches:
                current = scored.get(cell.matches[0].item_id)
                if current is None or cell.matches[0].score > current:
                    scored[cell.matches[0].item_id] = cell.matches[0].score
        return [item_id for item_id, _score in sorted(scored.items(), key=lambda kv: -kv[1])]

    def to_dict(self) -> dict[str, Any]:
        """对外形状：``grid`` / ``cells`` / ``accepted`` / ``ambiguous`` / ``unknown``。"""
        return {
            "grid": {
                "x_first": self.grid.x_first,
                "y_first": self.grid.y_first,
                "x_pitch": self.grid.x_pitch,
                "y_pitch": self.grid.y_pitch,
                "cols": self.grid.cols,
                "rows": self.grid.rows,
                "panel": list(self.grid.panel),
                "window": list(self.grid.window),
                "confidence": self.grid_confidence,
            },
            "scale": self.scale,
            "accepted": self.accepted,
            "ambiguous": self.ambiguous,
            "unknown": self.unknown,
            "min_accepted": self.min_accepted,
            "below_min_accepted": self.below_min_accepted,
            "item_ids": self.item_ids,
            "cells": [cell.to_dict() for cell in self.cells],
            "warnings": list(self.warnings),
        }


def flatten_scan(scan: PanelScan) -> dict[str, Any]:
    """一页结果 → 「按道具」的纯 dict（入库/合并用）。

    :returns: ``{"items": {item_id: 分数}, "accepted": 接受格数, "ambiguous": [...],
        "unknown": [...], "below_min_accepted": bool, "warnings": [...]}``。
        ``items`` 只放 ``accepted`` 的格子（同一 id 取最高分那次）；``ambiguous`` /
        ``unknown`` 如实列出候选分数与坐标，**绝不猜 id**。
    """
    items: dict[str, float] = {}
    ambiguous: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for cell in scan.cells:
        entry = {
            "col": cell.col + 1,
            "row": cell.row + 1,
            "cx": cell.cx,
            "cy": cell.cy,
            "top1": None if not cell.matches else round(cell.top1, 5),
            "margin": None if len(cell.matches) < 2 else round(cell.margin, 5),
            "top": [
                {"item_id": m.item_id, "name": m.name, "score": round(m.score, 5)}
                for m in cell.matches
            ],
        }
        if cell.status == "accepted" and cell.matches:
            item_id = cell.matches[0].item_id
            current = items.get(item_id)
            if current is None or cell.matches[0].score > current:
                items[item_id] = round(cell.matches[0].score, 5)
        elif cell.status == "unknown":
            unknown.append(entry)
        else:
            ambiguous.append(entry)
    return {
        "items": items,
        "accepted": scan.accepted,
        "ambiguous": ambiguous,
        "unknown": unknown,
        "below_min_accepted": scan.below_min_accepted,
        "warnings": list(scan.warnings),
    }


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class DepotScanConfig:
    """识别参数（纯数据，可整套注入；接线时再决定哪些进设置 API）。

    W1 不读设置：接线（W2）与入库（W3）还没落，先把参数放在这里，等真的需要热改
    再登记 ``account.depot_scan_*``（免得多一批没人读的设置项）。
    """

    #: 接受的分数下限（W0-b：0.75）。
    top1_min: float = DEFAULT_TOP1_MIN
    #: 接受的「与第二名差距」下限（W0-b：0.15，拦近重复模板）。
    margin_min: float = DEFAULT_MARGIN_MIN
    #: 整页接受的格数下限（默认 8，规格 §4.4）；低于它 :class:`PanelScan` 记
    #: ``below_min_accepted`` 与一条 warning，**写不写库由调用方决定**。
    min_accepted: int = 8
    #: 尺度列表；第一项是默认尺度，其余只在默认尺度**判不出接受**时才尝试（安全网）。
    scales: tuple[float, ...] = DEFAULT_SCALES
    #: 候选集上限（颜色预筛）；``0`` ＝ 全库（默认，保召回）。
    max_candidates: int = 0
    #: 直接给定几何（``None`` ＝ 用 :func:`load_grid_geometry` 的结果）。
    grid: ItemGrid | None = None
    #: 是否用画面分数面拟合格距与相位。
    fit_grid: bool = True
    #: 每格保留的候选数（够算 margin 与排障）。
    top_k: int = 3


def panel_of_frame(frame: np.ndarray, grid: ItemGrid) -> np.ndarray:
    """截图 → 面板灰度图（先到 1280×720 再到面板尺寸，与 W0-b 同口径）。

    :param frame: ``(H, W)`` 或 ``(H, W, 3)``。内核 ``GET_IMAGE`` 给 1280×720，
        真机/模拟器截图是 2560×1440；两条路都先按比例缩到游戏坐标再缩到面板。
    """
    array = np.asarray(frame)
    if array.ndim not in (2, 3):
        raise ValueError(f"不支持的帧形状：{array.shape}")
    panel = _to_panel_rgb(array, grid)
    return np.asarray(
        Image.fromarray(panel.astype(np.uint8), mode="RGB").convert("L"), dtype=np.float32
    )


def scan_panel(
    frame: np.ndarray,
    bank: ItemBank,
    *,
    config: DepotScanConfig | None = None,
) -> PanelScan:
    """一页画面 → 识别结果（纯函数：无设备、无网络、无数据库）。

    两趟：先按先验格线整页扫一遍（这一趟的 top-1 顺带充当拟合的 landmark），再量相位/
    格距；**只有格线真的改了才重扫**（拟合只做定位，判决要用全库分数重来一遍）。

    :param frame: 面板灰度图（``599×1066``）、游戏坐标图（``720×1280``）或原始截图；
        不是面板尺寸时按 :func:`panel_of_frame` 的口径缩放。
    :param bank: :func:`load_item_bank` 的模板库。
    :param config: 参数，默认见 :class:`DepotScanConfig`。
    :returns: :class:`PanelScan`；对外纯 dict 用 :func:`flatten_scan`。
    """
    config = config or DepotScanConfig()
    prior_grid = config.grid or load_grid_geometry()
    grid = prior_grid
    panel, panel_rgb = _as_panel(frame, grid)
    warnings: list[str] = []
    scales = tuple(float(scale) for scale in config.scales) or (1.0,)
    banks: dict[float, ItemBank] = {1.0: bank}
    cells = _scan_cells(panel, panel_rgb, banks, scales, grid, config)
    confidence = "prior"
    if config.fit_grid:
        grid, confidence, _fit_detail = fit_lattice(panel, bank, grid, cells=cells)
        if confidence == "insufficient":
            warnings.append("画面里可用 landmark 不足，网格沿用先验（未拟合）")
        elif grid != prior_grid:
            # 拟合改了格线：先前那一趟的格心已经不作数，用新格线重扫。
            cells = _scan_cells(panel, panel_rgb, banks, scales, grid, config)
    scan = PanelScan(
        grid=grid,
        grid_confidence=confidence,
        cells=cells,
        scale=scales[0],
        min_accepted=max(int(config.min_accepted), 0),
        warnings=tuple(warnings),
    )
    if scan.below_min_accepted:
        scan = replace(
            scan,
            warnings=scan.warnings
            + (f"接受的格数 {scan.accepted} 低于下限 {scan.min_accepted}",),
        )
    return scan


def _scan_cells(
    panel: np.ndarray,
    panel_rgb: np.ndarray,
    banks: dict[float, ItemBank],
    scales: Sequence[float],
    grid: ItemGrid,
    config: DepotScanConfig,
) -> tuple[CellResult, ...]:
    """按给定格线扫完整页（24 格）。"""
    return tuple(
        _scan_cell(
            panel,
            panel_rgb,
            banks,
            scales,
            grid=grid,
            col=col,
            row=row,
            cx=cx,
            cy=cy,
            config=config,
        )
        for col, row, cx, cy in grid.centers()
    )


def _as_panel(frame: np.ndarray, grid: ItemGrid) -> tuple[np.ndarray, np.ndarray]:
    """把任意输入归一成 ``(灰度面板, RGB 面板)``；同尺寸不重复缩放。

    灰度给匹配（masked NCC 只用亮度），RGB 给候选预筛的颜色指纹 —— 两件事要的是
    不同的东西，所以一起返回而不是让调用方再缩一次。
    """
    array = np.asarray(frame, dtype=np.float32)
    panel_w, panel_h = grid.panel
    if array.ndim not in (2, 3):
        raise ValueError(f"不支持的帧形状：{array.shape}")
    if array.ndim == 3 and array.shape[0] == panel_h and array.shape[1] == panel_w:
        rgb = np.ascontiguousarray(array[:, :, :3].astype(np.uint8))
    else:
        rgb = np.ascontiguousarray((_to_panel_rgb(array, grid)).astype(np.uint8))
    gray = np.asarray(Image.fromarray(rgb, mode="RGB").convert("L"), dtype=np.float32)
    return gray, np.ascontiguousarray(rgb.astype(np.float32))


def _to_panel_rgb(array: np.ndarray, grid: ItemGrid) -> np.ndarray:
    """任意帧 → 面板尺寸的 RGB（先到 1280×720 再到面板，与 W0-b 同口径）。"""
    if array.ndim == 2:
        image = Image.fromarray(array.astype(np.uint8), mode="L").convert("RGB")
    else:
        image = Image.fromarray(array[:, :, :3].astype(np.uint8), mode="RGB")
    if image.size != (1280, 720):
        image = image.resize((1280, 720), Image.Resampling.BOX)
    panel_w, panel_h = grid.panel
    if image.size != (panel_w, panel_h):
        image = image.resize((panel_w, panel_h), Image.Resampling.BOX)
    return np.asarray(image, dtype=np.float32)


def _scan_cell(
    panel: np.ndarray,
    panel_rgb: np.ndarray,
    banks: dict[float, ItemBank],
    scales: Sequence[float],
    *,
    grid: ItemGrid,
    col: int,
    row: int,
    cx: int,
    cy: int,
    config: DepotScanConfig,
) -> CellResult:
    """一格：裁窗口 → 颜色预筛 → masked NCC → 双阈值；默认尺度判不出时才试别的尺度。

    格子被面板边缘裁掉超过 :data:`CELL_MAX_CLIP` 就记 ``unknown``：这种格子的分数不可比，
    宁可如实报「不知道」。第 8 列只裁几像素，照常识别（见模块文档「几何」）。
    """
    window = match_window(panel, cx, cy, grid.window)
    in_bounds = grid.in_panel(cx, cy)
    if window is None or not grid.in_panel(cx, cy, max_cut=CELL_MAX_CLIP):
        return CellResult(
            col=col,
            row=row,
            cx=cx,
            cy=cy,
            status="unknown",
            in_bounds=False,
            clipped=True,
            scale=scales[0],
        )
    win_w, win_h = grid.window
    clipped = window.shape[0] != win_h or window.shape[1] != win_w
    rounds: list[tuple[float, tuple[Match, ...]]] = []
    primary = scales[0]
    for index, scale in enumerate(scales):
        if index and rounds and rounds[0][1] and _classify(
            rounds[0][1], top1_min=config.top1_min, margin_min=config.margin_min
        ) == "accepted":
            # 默认尺度已经接受：不必为同一格再花 ±5% 的代价（W0-b 实测 1.0 全胜）。
            break
        scale_bank = banks.get(scale)
        if scale_bank is None:
            scale_bank = load_item_bank(scale=scale, use_cache=True)
            banks[scale] = scale_bank
        candidates = _candidates_for(panel_rgb, cx, cy, scale_bank, grid, config)
        scores = score_templates(window, scale_bank, candidates)
        rounds.append((scale, top_matches(scores, scale_bank, count=config.top_k)))
    matches = _merge_matches([group for _scale, group in rounds], config.top_k)
    status = _classify(matches, top1_min=config.top1_min, margin_min=config.margin_min)
    used_scale = rounds[0][0] if rounds else primary
    if status == "accepted" and len(rounds) > 1:
        best = max(rounds, key=lambda item: item[1][0].score if item[1] else -np.inf)
        used_scale = best[0]
    return CellResult(
        col=col,
        row=row,
        cx=cx,
        cy=cy,
        status=status,
        matches=matches,
        in_bounds=in_bounds,
        clipped=clipped,
        scale=used_scale,
    )


def _candidates_for(
    panel_rgb: np.ndarray,
    cx: int,
    cy: int,
    bank: ItemBank,
    grid: ItemGrid,
    config: DepotScanConfig,
) -> list[int]:
    """候选集：``max_candidates`` 关闭时就是全库。

    颜色指纹取**单元格那一块**（比匹配窗口小）：匹配窗口为了容错向四周扩了
    20/5 px，会把邻居格的颜色算进「这一格的颜色」。
    """
    total = len(bank.ids)
    if config.max_candidates <= 0 or config.max_candidates >= total:
        return list(range(total))
    left, top, right, bottom = grid.enlarge
    window = match_window(
        panel_rgb, cx, cy, (grid.cell_size + left + right, grid.cell_size + top + bottom)
    )
    if window is None:  # pragma: no cover - 调用方已经裁过一次匹配窗口
        return list(range(total))
    return narrow_candidates(window, bank.fps, config.max_candidates)
