"""M45 W2/W3a：把整页识别器接到真实链路上（逐屏扫描 → 合并 → 结果 + 统计）。

**为什么单独一层**：:mod:`maa_api.services.account.depot_scan` 是纯函数（一帧图进、
一页结论出），按设计不碰设备、网络与数据库；而「帧从哪儿来」在仓库里已经有现成通路
—— :meth:`maa_api.services.device_service.DeviceManager.screenshot` 默认走 ADB 全分辨率
截图，ADB 通道失败时自己退回内核 ``AsstAsyncScreencap`` + ``GET_IMAGE``（M43/M44 的
道具图与 M36 的练度扫描都在用这条路）。本层只做三件事：**取帧、归一成识别器要的
``ndarray``、调识别器并给出统计**，不新造截图通道、不写库。

**W3a 加了逐屏扫描**（:meth:`DepotScanService.scan_all_pages`）：「全部」页横滑一页一屏
（W2 真机实测：竖向怎么划都不动），每屏的**横向相位都会漂**（实测 ``x_first`` 77.91–114.33，
先验 86.6），所以每屏都要独立取帧 + 独立重新拟合格线，再把各屏结果按 id 合并成一轮。
翻页**不用 adb 硬滑**，走内核链 ``MaaApi@DepotSelfScan@SwipeNextPage``
（:mod:`maa_api.core.depot_selfscan_overlay`），由调用方注入的「翻页器」实现
（:class:`DepotPager`）—— 单测注入假的离线翻页器，真机由
:class:`DepotChainPager` 提交 ``Custom`` 链。

**为什么取帧失败要抛异常**：空结果会被下游当成「这次仓库就是空的」。M44 的下限护栏
（``agent.depot_min_items``）与 M45 的接受格数下限（``min_accepted``）防的都是同一类
事故：一次半截识别把整表冲掉（真机实测：只读到 26 件的那次覆盖了 77 行的表）。
因此本层**绝不静默返回空结果**：

- 设备未连接 / 截图通道失败：原样抛设备层的 ``AppError``（``DEVICE_NOT_CONNECTED`` /
  ``SCREENSHOT_FAILED``），取帧器抛别的异常则包成 ``SCREENSHOT_FAILED`` 并带上原因；
- 帧解不出图像：抛 :class:`DepotScanError`；
- 识别未启用：抛 :class:`DepotScanDisabled`（调用方应先看 :attr:`DepotScanService.enabled`）。

**翻页失败不是致命错误**（W3a 的裁决）：翻不动只说明「这一轮的屏到此为止」，已经扫到的
几屏照样有效，轮次记 ``stop_reason="page_turn_failed"`` 并把异常类型写进 warning/失败列表；
取帧失败仍然是致命的（没有帧就没有结论，而且往往意味着设备掉了）。逐屏扫描到
:data:`DEFAULT_PAGE_LIMIT` 屏、或**连续** ``no_new_pages`` 屏没有新增 id
（:data:`DEFAULT_NO_NEW_PAGES`）、或连续两屏「整屏不可用」都会主动收手 —— 横滑到底会进
干员信物区，那里的格线不是 8 列 × 129.92（W2 实测拟合直接 ``insufficient``）。

**接受格数不足不在这里拦**：识别器只如实报告 ``below_min_accepted``（规格 §4.4），
闸门按**整轮聚合**结果判（W2 实测末页接受数会低到 7，按屏判会把最后几页整屏丢掉），
与 M44 的条目数下限一起放在写库路径上（:mod:`maa_api.services.account.depot`）——
本层照常返回结果，由调用方决定写不写。

**数量不做 OCR（W3b 的实测裁决）**：W3b 用探针节点（``baseTask: NumberOcrReplace``、roi 按
**当屏拟合出来的格线**现算）在真机上取了两轮证，结论是**不能写**：

- 手工按格微调的 roi（6 格 × 3 种 roi 变体 ＝ 18 个节点）：纯数字读数 15/15 与画面一致；
  万/亿缩写 3/3 全错（``472.2万`` → ``472.25`` / ``472.27`` / ``2.2F``）。
- **生产形状的 roi**（当屏拟合格心 + 固定偏移 ``110×40``，24 格）：13 对 / 6 错 / 5 读不出。
  错的那 6 格里最要命的一条是 ``1302 → 302``（掉首位，分数仍有 **0.9995**）—— 说明
  「分数门槛 + 纯数字白名单」这套安全网**拦不住**这种错。
- 原因就是规格 §5 风险表那条：内核 OCR 前会 ``subtract(item_templ * 0.41)`` 把图标美术
  减掉（``DepotImageAnalyzer`` 的 ``match_quantity``），任务 JSON 做不到；roi 一放大到
  能兜住逐屏相位漂移，图标美术就进了 OCR 的输入。

写错一个数量比不写更糟（页面会把错数字当事实显示），所以自识别独有的 id 一律
**不写数量**（``NULL`` ＝ 未识别，页面显示「—」），数量仍以内核 ``DepotInfo`` 那一趟为准。
取证数字与日志行号见台账 W3b-A 节；探针文件是临时的，不入库。
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import io
import logging
import threading
import time

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, runtime_checkable

import numpy as np

from PIL import Image

from maa_api.core.depot_selfscan_overlay import (
    DEPOT_SELF_SCAN_PREFIX,
    DEPOT_SELF_SCAN_START,
)
from maa_api.core.enums import Message
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.account.depot_scan import (
    FIRST_SEARCH_MAX,
    DepotScanConfig,
    ItemBank,
    ItemGrid,
    Match,
    PanelScan,
    flatten_scan,
    load_item_bank,
    scan_panel,
)
from maa_api.settings import Settings, get_settings

__all__ = [
    "DEFAULT_MIN_PAGE_ACCEPTED",
    "DEFAULT_NO_NEW_PAGES",
    "DEFAULT_PAGE_LIMIT",
    "CapturedFrame",
    "DepotChainPager",
    "DepotFrameSource",
    "DepotPageTurn",
    "DepotPager",
    "DepotScanDisabled",
    "DepotScanError",
    "DepotScanOutcome",
    "DepotScanRun",
    "DepotScanService",
    "DepotSweepConfig",
    "ScannedItem",
    "frame_to_array",
]

logger = logging.getLogger(__name__)

#: 逐屏扫描的页数上限（默认，可由 ``account.depot_scan_max_pages`` 覆盖）。
#: 10 屏 ≈ 80 列道具，足够覆盖一个账号的材料 + 凭证区；横滑到底会进干员信物区
#: （那里的格线不是 8 列 × 129.92，W2 实测拟合 ``insufficient``），所以必须有硬上限。
DEFAULT_PAGE_LIMIT = 10

#: 「连续这么多屏没有新增 id 就停」的默认值（可由 ``account.depot_scan_no_new_pages`` 覆盖）。
#: W2 的结论是**不能按固定翻页次数走**：一划一页是真的，但划到底会进另一个格线体系，
#: 而那一区的 24 格基本全是未知 ⇒ 「没有新增」是最可靠的收手信号。取 2 而不是 1：
#: 单屏没有新增可能只是这一屏恰好都是已见过的道具（滑回上一页、页面卡住不动）。
DEFAULT_NO_NEW_PAGES = 2

#: 单屏「整屏不可用」的判据（默认 3）：接受格数低于它就认为这一屏不是道具格线区。
#: 与整轮闸门 ``min_accepted``（默认 8）是两件事：W2 实测末页接受格数会低到 7，
#: 用 8 当「这一屏没有格线」的判据会把真实存在的末页误伤成「滑进了信物区」。
DEFAULT_MIN_PAGE_ACCEPTED = 3


class DepotScanError(RuntimeError):
    """自识别链路自身失败（帧解不出图像、识别器拒绝这一帧）。

    与设备层的 ``AppError`` 分开：取帧的失败原样带设备错误码上来，本异常只表示
    「帧有了，但识别这一步做不了」。W3 的写库路径按它决定不写，不需要额外的错误码。
    """


class DepotScanDisabled(DepotScanError):
    """自识别未启用（``account.depot_scan_enabled`` 为假）。

    调用方应当先看 :attr:`DepotScanService.enabled`，直接调 :meth:`DepotScanService.scan`
    才走这条异常：它是「调用姿势不对」的护栏，不是运行期故障。
    """


@dataclass(frozen=True, slots=True)
class DepotSweepConfig:
    """逐屏扫描的参数（纯数据，可整套注入；默认值都引台账数字）。

    :param page_limit: 页数上限。默认 :data:`DEFAULT_PAGE_LIMIT` ＝ 10：一屏 8 列 × 3 行
        ＝ 24 格，10 屏 ≈ 80 列，够覆盖一个账号的仓库；横滑到底会进干员信物区
        （那里的格线不是 8 列 × 129.92，W2 实测拟合 ``insufficient``），所以有硬上限。
    :param no_new_pages: 连续这么多屏没有新增 id 就停（默认 :data:`DEFAULT_NO_NEW_PAGES` ＝ 2）。
    :param first_search_max: 每屏相位拟合的搜索半径（先验格心 ± px），默认
        :data:`~maa_api.services.account.depot_scan.FIRST_SEARCH_MAX` ＝ 40
        （W2 实测横向漂 36 px、单帧偏 +27.7 px ⇒ 至少 40）。
    :param unusable_pages: 连续这么多屏「整屏不可用」就收手（默认 2）。
    :param min_page_accepted: 单屏「整屏不可用」的判据：接受格数低于它就认为这一屏
        不是道具格线区（默认 :data:`DEFAULT_MIN_PAGE_ACCEPTED` ＝ 3，可由注入覆盖）。
        故意与整轮闸门的 ``min_accepted`` 分开：那是「整轮够不够写库」的口径
        （W2 实测末页接受格数会低到 7），拿它当「这块区域根本不是道具格线」的判据
        会把真实存在的末页误伤。
    """

    page_limit: int = DEFAULT_PAGE_LIMIT
    no_new_pages: int = DEFAULT_NO_NEW_PAGES
    first_search_max: float = FIRST_SEARCH_MAX
    unusable_pages: int = 2
    min_page_accepted: int = DEFAULT_MIN_PAGE_ACCEPTED


@dataclass(frozen=True, slots=True)
class CapturedFrame:
    """一帧画面 + 供应商（``backend`` 是 ``adb`` / ``core``，内存帧为 ``None``）。"""

    image: Any
    backend: str | None = None


@dataclass(frozen=True, slots=True)
class ScannedItem:
    """一屏上接受的一格：道具 id + 分数 + 全局格坐标（与 :func:`flatten_scan` 同形状）。

    ``page`` / ``col`` / ``row`` 都是 1 基（``page`` 从 1 起），与 ``flat["items"]`` 的
    ``{item_id: 分数}`` 一一对应，用来回答「这个 id 是哪一屏哪一格认出来的」。
    """

    item_id: str
    score: float
    page: int
    col: int
    row: int
    cx: int
    cy: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "score": round(self.score, 5),
            "page": self.page,
            "col": self.col,
            "row": self.row,
            "cx": self.cx,
            "cy": self.cy,
        }


@dataclass(frozen=True, slots=True)
class DepotPageTurn:
    """翻页器的一屏结果：翻页后的那一帧 + 供应商（``next_page()`` 可以返回帧）。"""

    frame: Any
    backend: str | None = None


@runtime_checkable
class DepotPager(Protocol):
    """翻页器：把画面横向翻到下一页（可选地直接把新帧带回来）。

    ``next_page()`` 返回值放宽成三种：``None``（翻页器只管翻，调用方自己再取帧）、
    :class:`DepotPageTurn`（翻页器顺带给了帧）或裸帧。真机实现
    :class:`DepotChainPager` 返回 ``None``（帧仍走 :class:`DepotFrameSource`，与 W2 的
    取帧通路一致）；离线回放的假翻页器返回 :class:`DepotPageTurn`，于是「帧序列」这件事
    只由翻页器回答（W3 的验收口径：同一批帧同一结果）。
    """

    async def next_page(self) -> DepotPageTurn | Any | None:
        ...  # pragma: no cover - Protocol 只声明形状


@dataclass(frozen=True, slots=True)
class _CellRecord:
    """一屏上的一格（含未接受的）：合并与统计用。"""

    page: int
    col: int
    row: int
    status: str
    item_id: str | None
    top1: float
    margin: float
    cx: int
    cy: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "col": self.col,
            "row": self.row,
            "cx": self.cx,
            "cy": self.cy,
            "status": self.status,
            "item_id": self.item_id,
            "top1": None if self.top1 != self.top1 else round(self.top1, 5),
            "margin": None if self.margin != self.margin else round(self.margin, 5),
        }


@dataclass(frozen=True, slots=True)
class DepotScanRun:
    """逐屏扫描整轮的结果（纯数据，可直接进日志与识别记录）。

    ``flat`` 与 :func:`~maa_api.services.account.depot_scan.flatten_scan` 同形状，
    但 ``items`` 是**整轮并集**（同一 id 取分数最高那次）、计数是各屏之和；
    ``pages`` 保留逐屏明细（排障与「同一批帧同一结果」的复核）。

    ``stop_reason`` 的四个取值：``page_limit``（到页数上限）/ ``no_new``（连续 N 屏无新增）/
    ``page_turn_failed``（翻页器抛异常）/ ``unusable``（连续两屏整屏不可用，多为滑进
    干员信物区那种「格线不是 8 列」的区域）。
    """

    items: Mapping[str, float]
    pages: tuple[DepotScanOutcome, ...]
    accepted: int
    ambiguous: int
    unknown: int
    below_min_accepted: bool
    stop_reason: str
    pages_scanned: int
    elapsed_ms: int
    warnings: tuple[str, ...] = field(default_factory=tuple)
    failures: tuple[str, ...] = field(default_factory=tuple)
    scanned_items: tuple[ScannedItem, ...] = ()
    cells: tuple[_CellRecord, ...] = ()
    backends: tuple[str, ...] = ()

    @property
    def frame(self) -> tuple[int, int]:
        """最后一屏的尺寸（``scan(force=True)`` 的兼容口径）；没有屏时是 ``(0, 0)``。"""
        return self.pages[-1].frame if self.pages else (0, 0)

    def cells_on_page(self, page: int) -> tuple[_CellRecord, ...]:
        """某一屏的全部格子（1 基页号）。"""
        return tuple(cell for cell in self.cells if cell.page == page)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pages_scanned": self.pages_scanned,
            "stop_reason": self.stop_reason,
            "elapsed_ms": self.elapsed_ms,
            "accepted": self.accepted,
            "ambiguous": self.ambiguous,
            "unknown": self.unknown,
            "below_min_accepted": self.below_min_accepted,
            "items": dict(self.items),
            "scanned_items": [item.to_dict() for item in self.scanned_items],
            "grids": [page.scan.to_dict()["grid"] for page in self.pages],
            "backends": list(self.backends),
            "warnings": list(self.warnings),
            "failures": list(self.failures),
        }


@dataclass(frozen=True, slots=True)
class DepotScanOutcome:
    """一次识别的结果与统计（单帧 :meth:`DepotScanService.scan_frame` 与逐屏扫描共用）。

    ``flat`` 是 :func:`~maa_api.services.account.depot_scan.flatten_scan` 的纯 dict
    （``items`` / ``ambiguous`` / ``unknown`` / ``below_min_accepted``），W3 的合并入库
    直接吃它；``scan`` 保留完整的逐格结论（排障与识别记录用）。逐屏扫描（``run`` 非空）
    时 ``flat`` 换成整轮聚合并集（:class:`DepotScanRun`），``accepted`` 等计数与
    ``items`` 都按整轮口径 —— 与 W2 的结论一致：**闸门按整轮聚合判，不按屏判**。
    """

    scan: PanelScan | None
    flat: Mapping[str, Any]
    elapsed_ms: int
    backend: str | None = None
    frame: tuple[int, int] = (0, 0)
    run: DepotScanRun | None = None

    @property
    def items(self) -> dict[str, float]:
        """``{item_id: 分数}``，只有 ``accepted`` 的格子。"""
        return dict(self.flat.get("items") or {})

    @property
    def accepted(self) -> int:
        return int(self.flat.get("accepted") or 0)

    @property
    def ambiguous(self) -> int:
        return len(self.flat.get("ambiguous") or ())

    @property
    def unknown(self) -> int:
        return len(self.flat.get("unknown") or ())

    @property
    def below_min_accepted(self) -> bool:
        return bool(self.flat.get("below_min_accepted"))

    def to_dict(self) -> dict[str, Any]:
        """对外/落日志的扁平形状（计数是计数，明细带 ``_cells`` 后缀，不重名）。

        ``grid`` / ``scale`` 取第一屏（单帧识别就是它自己）：逐屏扫描时每屏的格线都不同，
        逐屏明细在 ``run.grids`` 里。
        """
        first = self.scan
        return {
            "backend": self.backend,
            "frame": {"width": self.frame[0], "height": self.frame[1]},
            "elapsed_ms": self.elapsed_ms,
            "grid": first.to_dict()["grid"] if first is not None else None,
            "scale": first.scale if first is not None else None,
            "accepted": self.accepted,
            "ambiguous": self.ambiguous,
            "unknown": self.unknown,
            "min_accepted": first.min_accepted if first is not None else 0,
            "below_min_accepted": self.below_min_accepted,
            "items": dict(self.flat.get("items") or {}),
            "ambiguous_cells": list(self.flat.get("ambiguous") or ()),
            "unknown_cells": list(self.flat.get("unknown") or ()),
            "warnings": list(self.flat.get("warnings") or ()),
            "run": None if self.run is None else self.run.to_dict(),
        }


def frame_to_array(frame: Any) -> np.ndarray:
    """任意一帧 → 识别器入口要的 ``ndarray``（``(H, W)`` 或 ``(H, W, 3)``）。

    **解码留在这一层**：设备通路给的可能是 PIL 图（ADB 截图、内核 ``GET_IMAGE`` 落盘的
    JPEG）或已编码字节（JPEG/PNG），识别核心只认像素数组，所以在这里归一，不让
    ``depot_scan`` 变成「什么都得能解」的杂物间（规格 §3.1 的分工）。
    """
    if isinstance(frame, np.ndarray):
        array = frame
    elif isinstance(frame, Image.Image):
        array = np.asarray(frame.convert("RGB"))
    elif isinstance(frame, (bytes, bytearray, memoryview)):
        try:
            with Image.open(io.BytesIO(bytes(frame))) as image:
                image.load()
                array = np.asarray(image.convert("RGB"))
        except Exception as exc:  # noqa: BLE001 - 坏字节只该降级成明确的识别错误
            raise DepotScanError(f"截图字节无法解码成图像：{exc!r}") from exc
    else:
        raise DepotScanError(f"取帧器返回的类型无法解码成图像：{type(frame).__name__}")
    if array.ndim == 3 and array.shape[2] == 4:
        array = array[:, :, :3]
    if array.ndim not in (2, 3) or array.size == 0:
        raise DepotScanError(f"帧的形状不受支持：{array.shape}")
    return np.ascontiguousarray(array)


class DepotFrameSource:
    """默认取帧器：走设备管理器现成的截图通路。

    ``backend="adb"`` 与 ``GET /api/device/screenshot`` 的默认值一致：ADB 给的是设备
    原始分辨率（本机模拟器 2560×1440），失败时 :meth:`DeviceManager.screenshot` 自己
    退回内核通道。识别器会把任意分辨率先缩到 1280×720 再缩到面板，所以两条通道都可用；
    真正供货的那条记在 :attr:`DepotScanOutcome.backend` 里，便于排障。
    """

    def __init__(self, device_manager: Any, *, backend: str = "adb") -> None:
        self._manager = device_manager
        self._backend = backend

    async def __call__(self) -> CapturedFrame:
        result = await self._manager.screenshot(backend=self._backend)
        return CapturedFrame(
            image=getattr(result, "image", result),
            backend=getattr(result, "backend", self._backend),
        )


class DepotScanService:
    """取帧 + 识别 + 统计（无数据库、无 HTTP，只有一帧的来路）。

    :param frame_source: 取帧器：无参可调用对象，返回帧（``PIL.Image`` / ``ndarray`` /
        已编码 ``bytes``），也可以是 :class:`CapturedFrame` 或带 ``image`` 属性的对象
        （如 ``ScreenshotResult``）；异步可调用同样接受。生产用 :class:`DepotFrameSource`，
        测试注入假取帧器。
    :param settings_provider: 现读配置（每次调用都读，改设置立即生效）。
    :param bank_provider: 模板库来源，默认 :func:`~...depot_scan.load_item_bank`
        （进程内缓存，换资源后由 ``depot_scan.clear_caches`` 清）。
    :param pager: 默认翻页器（``scan_all_pages`` 不显式传时用它）。生产在 ``main.py``
        注入 :class:`DepotChainPager`（提交内核链上的翻页节点）；测试注入离线假翻页器。
        ``None`` ＝ 不翻页（只扫当前这一屏）。
    """

    def __init__(
        self,
        frame_source: Callable[[], Any],
        *,
        settings_provider: Callable[[], Settings] = get_settings,
        bank_provider: Callable[..., ItemBank] | None = None,
        pager: DepotPager | None = None,
    ) -> None:
        self._frame_source = frame_source
        self._settings_provider = settings_provider
        self._bank_provider = bank_provider or load_item_bank
        self._pager = pager

    # -- 配置 -------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        """``account.depot_scan_enabled``；每次调用现读。"""
        return bool(getattr(self._settings_provider().account, "depot_scan_enabled", False))

    def config_from_settings(self, *, grid: ItemGrid | None = None) -> DepotScanConfig:
        """设置里的四个旋钮 + 相位搜索半径 → :class:`DepotScanConfig`（默认值＝台账实测值）。

        ``scales`` / ``max_candidates`` / ``top_k`` / ``first_search_max`` 不进设置：
        前三者是识别器内部的安全网与排障参数，没有产品语义（W1 台账「偏离台账的地方」）；
        ``first_search_max`` 是 W3a 的取证结论（默认 40 px，见
        :data:`~maa_api.services.account.depot_scan.FIRST_SEARCH_MAX`），要改走参数注入
        （``scan_frame(config=…)``）而不是给用户一个会调坏识别的旋钮。
        """
        account = self._settings_provider().account
        return DepotScanConfig(
            top1_min=float(getattr(account, "depot_scan_top1_min", 0.75)),
            margin_min=float(getattr(account, "depot_scan_margin_min", 0.15)),
            min_accepted=max(int(getattr(account, "depot_scan_min_accepted", 8)), 0),
            grid=grid,
        )

    def sweep_from_settings(self) -> DepotSweepConfig:
        """逐屏扫描的三个参数（页数上限 / 无新增阈值 / 搜索半径），每次调用现读设置。

        ``FIRST_SEARCH_MAX`` 的默认值是 **40 px**：W2 真机逐屏实测横向相位在
        ``77.91–114.33`` 之间漂（先验 86.6，极差 36 px），实测过 +27.7 px 的帧 ——
        旧的 ±24 兜不住（复用先验格线时整屏 0 接受），所以 W3a 把它放宽到 40。
        """
        account = self._settings_provider().account
        return DepotSweepConfig(
            page_limit=max(int(getattr(account, "depot_scan_max_pages", DEFAULT_PAGE_LIMIT)), 1),
            no_new_pages=max(
                int(getattr(account, "depot_scan_no_new_pages", DEFAULT_NO_NEW_PAGES)), 1
            ),
        )

    def _page_config(self, sweep: DepotSweepConfig, grid: ItemGrid | None = None) -> DepotScanConfig:
        """逐屏扫描每一屏用的识别参数（半径来自 sweep，其余来自设置）。"""
        return replace(
            self.config_from_settings(grid=grid),
            first_search_max=sweep.first_search_max,
        )

    # -- 入口 -------------------------------------------------------------
    async def scan(
        self,
        *,
        config: DepotScanConfig | None = None,
        force: bool = False,
    ) -> DepotScanOutcome:
        """取一帧真机画面并识别（未启用时抛 :class:`DepotScanDisabled`）。

        取帧与识别各有一次异常包装：取帧失败一律是设备层的错误码（能明确告诉用户
        是设备没连还是截图通道坏了），识别失败是本模块的 :class:`DepotScanError`。

        :param force: 即使 ``depot_scan_enabled`` 为假也照跑。给真机验收与离线回放用
            （W4 的验收要先在默认关的状态下把链路跑通），正常调用路径不该传。
        """
        if not force and not self.enabled:
            raise DepotScanDisabled(
                "仓库自识别未启用（account.depot_scan_enabled=false）"
            )
        config = config or self.config_from_settings()
        captured = await self._capture()
        return await self.scan_frame(captured.image, config=config, backend=captured.backend)

    async def scan_all_pages(
        self,
        *,
        pager: DepotPager | None = None,
        config: DepotScanConfig | None = None,
        sweep: DepotSweepConfig | None = None,
        force: bool = False,
    ) -> DepotScanOutcome:
        """逐屏扫描仓库「全部」页，直到该收手为止。

        **入口与停止条件**（W3a 的口径，全部可注入/可配）：

        1. 先取一帧扫第一屏；每屏都用 :meth:`scan_frame` 单独识别（**每屏重新拟合格线**，
           W2 的结论：格距可复用、横向相位逐屏漂 78–114 px，复用先验格线会出现整屏 0 接受）；
        2. 扫完一屏叫一次 ``pager.next_page()``（真机走内核链
           ``MaaApi@DepotSelfScan@SwipeNextPage``，**不用 adb 硬滑**）。翻页器返回帧时
           直接用它当下一屏（离线回放），返回 ``None`` 时再取一帧（真机）；
        3. 停在三者之一 —— ``page_limit`` 屏（默认 :data:`DEFAULT_PAGE_LIMIT` ＝ 10）、
           **连续** ``no_new_pages`` 屏没有新增 id（默认 :data:`DEFAULT_NO_NEW_PAGES` ＝ 2）、
           翻页器抛异常（``page_turn_failed``，已扫到的屏照常有效）；
        4. 逐屏结果按 id 并成一轮：``items`` 是并集（同一 id 取分数最高那次），
           ``accepted`` / ``ambiguous`` / ``unknown`` 是各屏之和，``below_min_accepted``
           按**整轮**判 —— W2 实测末页接受格数会低到 7，按屏判会把最后几页整屏丢掉。

        参数默认值都引台账数字：``first_search_max`` 40 px（W2-B 相位极差 36 px）、
        页数上限 10、无新增阈值 2（滑到底会进干员信物区，那里的格线不是 8 列 × 129.92）。

        :param pager: 翻页器；``None`` 时用装配时注入的那个（``self._pager``），两个都
            没有就只扫当前这一屏（等价 :meth:`scan`，用于「不想翻页」的调用方与离线回放）。
            协议见 :class:`DepotPager`。
        :param config: 识别参数（``None`` ＝ 按设置现读）。
        :param sweep: 逐屏扫描参数（``None`` ＝ 按设置现读）。
        :param force: 同 :meth:`scan`，给真机验收与离线回放用。
        :raises DepotScanDisabled: 未启用且 ``force=False``。
        :raises AppError: 取帧失败（设备层错误码原样上抛）。
        :raises DepotScanError: 帧解不出图像。
        """
        if not force and not self.enabled:
            raise DepotScanDisabled(
                "仓库自识别未启用（account.depot_scan_enabled=false）"
            )
        sweep = sweep or self.sweep_from_settings()
        pager = pager or self._pager
        # 每一屏都用同一份参数（含 first_search_max）现读一次，翻页期间改设置也生效。
        page_config = config or self._page_config(sweep)
        started = time.perf_counter()
        pages: list[DepotScanOutcome] = []
        failures: list[str] = []
        stop_reason = ""
        seen: set[str] = set()
        no_new = 0
        unusable = 0
        captured = await self._capture()
        while True:
            outcome = await self.scan_frame(
                captured.image, config=page_config, backend=captured.backend
            )
            pages.append(outcome)
            novel = set(outcome.items) - seen
            seen |= set(outcome.items)
            no_new = 0 if novel else no_new + 1
            # 「整屏不可用」= 连下限都没到（横滑到干员信物区时 24 格基本全未知，
            # 拟合也会报 insufficient）。连着两屏这样就不再往下划了。
            unusable = unusable + 1 if outcome.accepted < sweep.min_page_accepted else 0
            # 先判「没新增」再判页数上限：两者同时成立时，真正的原因是画面没再往前走。
            if no_new >= sweep.no_new_pages:
                stop_reason = "no_new"
                break
            if unusable >= sweep.unusable_pages:
                stop_reason = "unusable"
                break
            if len(pages) >= sweep.page_limit:
                stop_reason = "page_limit"
                break
            if pager is None:
                stop_reason = "no_pager"
                break
            turn = await self._turn_page(pager, page=len(pages), failures=failures)
            if turn is _TURN_FAILED:
                stop_reason = "page_turn_failed"
                break
            if turn is None:
                captured = await self._capture()
            else:
                captured = turn
        run = _merge_pages(pages, stop_reason=stop_reason, failures=failures)
        elapsed_ms = int(round((time.perf_counter() - started) * 1000))
        warnings = list(run.warnings)
        if not run.items:
            warnings.append("整轮没有识别到任何道具")
        flat: dict[str, Any] = {
            "items": dict(run.items),
            "accepted": run.accepted,
            "ambiguous": [cell.to_dict() for cell in run.cells if cell.status == "ambiguous"],
            "unknown": [cell.to_dict() for cell in run.cells if cell.status == "unknown"],
            "below_min_accepted": run.below_min_accepted,
            "warnings": warnings,
        }
        outcome = DepotScanOutcome(
            scan=pages[0].scan if pages else None,
            flat=flat,
            elapsed_ms=elapsed_ms,
            backend=captured.backend,
            # **不能用 ``captured.image``**：设备通路给的是 ``PIL.Image``（真机就是这样），
            # 只有 ``scan_frame`` 解码后的数组才有 ``shape``。逐屏结果里已经有解码后的
            # 尺寸（``DepotScanOutcome.frame``），直接取最后一屏的。
            frame=pages[-1].frame if pages else (0, 0),
            run=replace(run, elapsed_ms=elapsed_ms, warnings=tuple(warnings)),
        )
        logger.info(
            "仓库自识别逐屏扫描：%d 屏（%s），接受 %d / 歧义 %d / 未知 %d（下限 %d，%s），"
            "耗时 %d ms",
            run.pages_scanned,
            stop_reason,
            run.accepted,
            run.ambiguous,
            run.unknown,
            outcome.scan.min_accepted if outcome.scan is not None else 0,
            "未达标" if run.below_min_accepted else "达标",
            elapsed_ms,
        )
        return outcome

    async def scan_frame(
        self,
        frame: Any,
        *,
        config: DepotScanConfig | None = None,
        backend: str | None = None,
    ) -> DepotScanOutcome:
        """识别一帧已经在内存里的画面（离线回放 / 单测用；不碰设备）。"""
        config = config or self.config_from_settings()
        array = frame_to_array(frame)
        height, width = array.shape[:2]
        bank = self._bank_provider()
        started = time.perf_counter()
        # 单页 24 格 × 479 模板 ≈ 1.2 s，纯 CPU：走线程，别卡住事件循环（规格 §3.2-4）。
        scan = await asyncio.to_thread(scan_panel, array, bank, config=config)
        elapsed_ms = int(round((time.perf_counter() - started) * 1000))
        outcome = DepotScanOutcome(
            scan=scan,
            flat=flatten_scan(scan),
            elapsed_ms=elapsed_ms,
            backend=backend,
            frame=(width, height),
        )
        logger.info(
            "仓库自识别：接受 %d / 歧义 %d / 未知 %d（下限 %d，%s），耗时 %d ms",
            outcome.accepted,
            outcome.ambiguous,
            outcome.unknown,
            scan.min_accepted,
            "未达标" if outcome.below_min_accepted else "达标",
            elapsed_ms,
        )
        return outcome

    # -- 内部 -------------------------------------------------------------
    async def _capture(self) -> CapturedFrame:
        """取一帧；把取帧器的任何失败归一成明确的设备/截图错误。"""
        try:
            value = self._frame_source()
            if isinstance(value, Awaitable):
                value = await value
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001 - 取帧失败必须有明确错误码，不能静默
            raise AppError(
                ErrorCode.SCREENSHOT_FAILED,
                "取一帧设备画面失败，请确认设备已连接后重试",
                {"stage": "depot_self_scan", "error": repr(exc)},
            ) from exc
        return _normalize_capture(value)

    async def _turn_page(
        self,
        pager: DepotPager,
        *,
        page: int,
        failures: list[str],
    ) -> CapturedFrame | None | _TurnFailed:
        """叫一次翻页器；失败**不致命**（已扫到的屏照常有效），返回哨兵让调用方收手。

        翻页失败按「这一轮到头了」处理而不是整轮失败：W2 实测滑到底会进干员信物区，
        翻不动/滑不动本身是预期内的情况；真出问题（内核没起来、链没生成）也只会少扫
        几屏，不会写出错数据 —— 写库还有两道闸门。
        """
        for attempt in (1, 2):
            try:
                value = pager.next_page()
                if inspect.isawaitable(value):
                    value = await value
            except Exception as exc:  # noqa: BLE001 - 翻页失败只影响本轮能扫几屏
                failures.append(f"第 {page} 屏后翻页失败（第 {attempt} 次）：{exc!r}")
                continue
            if value is None:
                return None
            if isinstance(value, DepotPageTurn):
                return CapturedFrame(image=value.frame, backend=value.backend)
            if isinstance(value, CapturedFrame):
                return value
            return CapturedFrame(image=value)
        return _TURN_FAILED


class _TurnFailed:
    """翻页失败的哨兵（`None` 已经是「翻页器只管翻、帧另外取」的合法返回值）。"""


_TURN_FAILED = _TurnFailed()


@dataclass(slots=True)
class _ChainWait:
    """一趟链的落点：``event`` 被置位表示内核已经报了结束，``error`` 记失败原因。"""

    event: asyncio.Event
    error: str | None = None


class _ChainCompletionTap:
    """等内核 ``TaskChainCompleted``（把「滑动何时落定」从睡固定时长改成事件驱动）。

    **为什么可靠**：内核在整条 ``Custom`` 链跑完时才发 ``Message.TaskChainCompleted``
    （``details.taskid`` 就是 ``append_task`` 的返回值），而链上的 ``@SwipeNextPage``
    自带 ``postDelay: 1500``、``@Done`` 在它之后 —— 也就是说**这个信号到达时滑动已经
    落定**。W3a 之前只能「睡 1.5 s 赌它落定了」，现在是等事实（真机实测：W3b 一轮
    ``@SwipeNextPage`` 从提交到完成约 2.2 s，比 1.5 s 的赌法更晚、更准）。

    两条等价信号都收：``TaskChainCompleted``（正常结束）与 ``AllTasksCompleted``
    （内核把这一批任务全跑完，``details.finished_tasks`` 里能看到我们的 taskid）；
    ``TaskChainError`` / ``TaskChainStopped`` 记进 :attr:`_ChainWait.error`。

    内核回调跑在 ``CoreClient`` 的消费线程里，所以唤醒事件循环必须走
    ``loop.call_soon_threadsafe``（同 ``pipeline_runner`` 的做法）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._waits: dict[int, _ChainWait] = {}

    def attach(self, client: Any) -> bool:
        """订阅 ``CALLBACK``；客户端没有 ``on``（测试替身）时返回 ``False``。"""
        on = getattr(client, "on", None)
        if not callable(on):
            return False
        try:
            on("CALLBACK", self._on_callback)
        except Exception:  # noqa: BLE001 - 订阅不上就退回「等落定」，不影响翻页
            logger.warning("订阅内核回调失败，翻页退回等固定时长落定", exc_info=True)
            return False
        return True

    def expect(self, task_id: int) -> _ChainWait:
        """登记「我在等这个 taskid 结束」（必须在事件循环里调）。"""
        wait = _ChainWait(event=asyncio.Event())
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._waits[task_id] = wait
        return wait

    def discard(self, task_id: int) -> None:
        with self._lock:
            self._waits.pop(task_id, None)

    def _on_callback(self, payload: Any) -> None:
        """（内核消费线程）收到回调 → 投回事件循环唤醒等待者。"""
        if not isinstance(payload, Mapping):
            return
        try:
            message = Message(int(payload.get("msg")))
        except (TypeError, ValueError):
            return
        if message not in (
            Message.TaskChainCompleted,
            Message.TaskChainError,
            Message.TaskChainStopped,
            Message.AllTasksCompleted,
        ):
            return
        details = payload.get("details")
        details = details if isinstance(details, Mapping) else {}
        ids: list[int] = []
        raw_id = details.get("taskid")
        if isinstance(raw_id, int):
            ids.append(raw_id)
        finished = details.get("finished_tasks")
        if message is Message.AllTasksCompleted and isinstance(finished, (list, tuple)):
            ids.extend(int(value) for value in finished if isinstance(value, int))
        error = None
        if message is Message.TaskChainError:
            error = str(details.get("taskchain") or details.get("task") or "Custom")
        with self._lock:
            loop = self._loop
            targets = [
                self._waits[task_id]
                for task_id in ids
                if task_id in self._waits
            ]
            if error is not None:
                for wait in targets:
                    wait.error = f"内核报告任务链失败（{error}）"
        if loop is None:
            return
        for wait in targets:
            with contextlib.suppress(RuntimeError):  # 事件循环已关闭：等待者自己会超时
                loop.call_soon_threadsafe(wait.event.set)


class DepotChainPager:
    """真机翻页器：提交内核链 ``MaaApi@DepotSelfScan@*``（**不用 adb 硬滑**）。

    W2 的裁决是翻页必须走链上的节点：``@SwipeNextPage`` 的几何（``x 1150 → 110``、
    ``y 340``、200 ms 慢拖）与内核自带的 ``DepotSlowlySwipeToTheRight`` 同参数，一划一页
    （真机实测连划 4 次拿到 4 个互不重叠的页）；``@Start`` 则负责「已经在仓库页就点
    「全部」，否则用内核 ``DepotBegin`` 导航进仓库」——两个都由内核执行，Python 侧只等它跑完。

    **完成判据（W3b 改）**：优先等内核 ``TaskChainCompleted``（:class:`_ChainCompletionTap`，
    事件驱动）；拿不到这个信号（客户端没有 ``on``、回调丢失）时**保留超时兜底** ——
    等到 :attr:`completion_seconds` 还不见信号就退回「睡 :attr:`settle_seconds` 落定」，
    宁可多等一下也不把「还没落定的画面」交出去取帧。链本身报错（``TaskChainError`` /
    ``TaskChainStopped``）不再当成「翻好了」：抛 :class:`DepotScanError`，由扫描循环记成
    ``page_turn_failed`` 并保留已经扫到的屏。

    两种提交方式：

    - ``submitter``（推荐）：``await submitter(task_name)``，由调用方把链包成一次常规流水线
      提交（复用流水线的状态机、日志与并发控制）；
    - ``client``（兜底）：直接 ``append_task("Custom", {"task_names": [...]})`` + ``start()``，
      再等上面那条完成判据。

    :param client: ``CoreClient``（或提供 ``append_task`` / ``start`` / 可选 ``on`` 的替身）。
    :param ready: 完成判据的外部注入点（异步可调用，返回 ``True`` ＝ 这一趟跑完）。
        给了它就完全按它判（单测与离线回放用），不再看内核回调。
    :param settle_seconds: 兜底等待时长：链上的 ``@SwipeNextPage`` 自带 ``postDelay: 1500``，
        滑动落定后才会写 ``TaskChainCompleted``（W2 的「取帧太早会拿到过渡态」）。
    :param completion_seconds: 等 ``TaskChainCompleted`` 的上限；超时就退回
        ``settle_seconds``（真机实测一趟翻页链约 2.2 s，默认 6 s 留足余量）。
    :param retry_delay: 内核忙着别的任务（``start()`` 被拒）时的退避间隔，默认 0.5 s；
        重试总预算仍是 :attr:`timeout_seconds`（W3b-C2 实测：同流水线的下一个任务会抢设备）。
    :param timeout_seconds: 整趟链的硬上限（含提交重试、等信号与兜底），超时抛出。
    """

    #: 链头（进仓库页 + 点「全部」，幂等：已在「全部」页时点一下没有副作用）。
    START_TASK = DEPOT_SELF_SCAN_START
    #: 翻页节点（一划一页）。
    NEXT_PAGE_TASK = DEPOT_SELF_SCAN_PREFIX + "SwipeNextPage"

    def __init__(
        self,
        client: Any = None,
        *,
        submitter: Callable[[str], Awaitable[Any]] | None = None,
        ready: Callable[[], Awaitable[bool]] | None = None,
        settle_seconds: float = 1.5,
        completion_seconds: float = 6.0,
        retry_delay: float = 0.5,
        timeout_seconds: float = 30.0,
    ) -> None:
        if client is None and submitter is None:
            raise ValueError("DepotChainPager 需要 client 或 submitter 之一")
        self._client = client
        self._submitter = submitter
        self._ready = ready
        self._settle_seconds = max(float(settle_seconds), 0.0)
        self._completion_seconds = max(float(completion_seconds), 0.0)
        self._retry_delay = max(float(retry_delay), 0.0)
        self._timeout_seconds = max(float(timeout_seconds), 0.1)
        self._completion: _ChainCompletionTap | None = None
        if client is not None:
            tap = _ChainCompletionTap()
            if tap.attach(client):
                self._completion = tap

    async def goto_pager_start(self) -> None:
        """先把设备带到仓库「全部」页（链头 ``@Start``）；失败原样抛出。"""
        await self._run_chain(self.START_TASK)

    async def next_page(self) -> None:
        """横向翻一页（链上的 ``@SwipeNextPage``）；失败原样抛出，由扫描循环收手。"""
        await self._run_chain(self.NEXT_PAGE_TASK)

    async def _run_chain(self, task_name: str) -> None:
        if self._submitter is not None:
            await asyncio.wait_for(self._submitter(task_name), self._timeout_seconds)
            return
        task_id = await self._submit(task_name)
        await asyncio.wait_for(self._wait_ready(task_id), self._timeout_seconds)

    async def _submit(self, task_name: str) -> int:
        """把链交给内核；内核正忙着别的任务时**退避重试**，别当场认输。

        W3b 真机实测（台账 W3b-C2）：同一个流水线的下一个任务会在自识别跑完之前就抢走
        设备 —— 14:24:31 `Award` 开始跑，14:24:39 我们的两次提交都被内核拒
        （``start()`` 返回 false），旧实现立刻抛错、逐屏扫描当场收成 ``page_turn_failed``：
        只扫到 1 屏，自识别独有的 id 从 19 个掉到 11 个（96 → 88 行）。Award 9 s 后就跑完
        了，等一等完全来得及，所以在 :attr:`timeout_seconds` 预算内退避重试。
        **只重试 ``start()``、不重复 ``append_task``**：内核拒的是「启动」，
        已入队的任务还在队列里（重复 append 会把同一趟链排两遍、多滑一页）。
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout_seconds
        task_id = 0
        while True:
            if not task_id:
                task_id = await self._client.append_task(
                    "Custom", {"task_names": [task_name]}
                )
                if not task_id:
                    raise DepotScanError(f"内核没有接受自识别任务 {task_name}")
            if await self._client.start():
                return task_id
            if loop.time() >= deadline:
                raise DepotScanError(
                    f"内核没有启动自识别任务 {task_name}（设备被别的任务占着）"
                )
            await asyncio.sleep(self._retry_delay)

    async def _wait_ready(self, task_id: int | None) -> None:
        """等这一趟链跑完：注入的 ``ready`` → 内核完成回调 → 兜底睡落定。"""
        if self._ready is not None:
            while not await self._ready():
                await asyncio.sleep(0.05)
            return
        if task_id is None or self._completion is None:
            await asyncio.sleep(self._settle_seconds)
            return
        wait = self._completion.expect(task_id)
        try:
            await asyncio.wait_for(wait.event.wait(), self._completion_seconds)
        except TimeoutError:
            logger.warning(
                "等内核任务链结束超时（taskid=%s，%.1f s），退回等 %.1f s 落定",
                task_id,
                self._completion_seconds,
                self._settle_seconds,
            )
            await asyncio.sleep(self._settle_seconds)
            return
        finally:
            self._completion.discard(task_id)
        if wait.error:
            raise DepotScanError(wait.error)


def _merge_pages(
    pages: Sequence[DepotScanOutcome],
    *,
    stop_reason: str,
    failures: Sequence[str] = (),
) -> DepotScanRun:
    """逐屏结果 → 整轮结果（并集 + 计数求和 + 逐格明细带页号）。

    同一 id 在多屏出现（滑回上一页、半页横滑）时取**分数最高**那次，与
    :func:`~maa_api.services.account.depot_scan.flatten_scan` 的单屏口径一致。
    """
    items: dict[str, float] = {}
    scanned: list[ScannedItem] = []
    cells: list[_CellRecord] = []
    accepted = ambiguous = unknown = 0
    warnings: list[str] = []
    backends: list[str] = []
    for page_number, outcome in enumerate(pages, start=1):
        accepted += outcome.accepted
        ambiguous += outcome.ambiguous
        unknown += outcome.unknown
        scan = outcome.scan
        if scan is not None:
            if scan.grid_confidence == "insufficient":
                warnings.append(
                    f"第 {page_number} 屏可用 landmark 不足，网格沿用先验（未拟合）"
                )
            for cell in scan.cells:
                top = cell.matches[0] if cell.matches else None
                cells.append(
                    _CellRecord(
                        page=page_number,
                        col=cell.col + 1,
                        row=cell.row + 1,
                        status=cell.status,
                        item_id=cell.item_id,
                        top1=cell.top1,
                        margin=cell.margin,
                        cx=cell.cx,
                        cy=cell.cy,
                    )
                )
                if cell.status != "accepted" or top is None:
                    continue
                scanned.append(
                    ScannedItem(
                        item_id=top.item_id,
                        score=top.score,
                        page=page_number,
                        col=cell.col + 1,
                        row=cell.row + 1,
                        cx=cell.cx,
                        cy=cell.cy,
                    )
                )
                current = items.get(top.item_id)
                if current is None or top.score > current:
                    items[top.item_id] = round(top.score, 5)
        if outcome.backend and outcome.backend not in backends:
            backends.append(outcome.backend)
    # 整轮闸门：接受格数按各屏之和（W2 实测末页低到 7，按屏判会把最后几页整屏丢掉）。
    min_accepted = pages[0].scan.min_accepted if pages and pages[0].scan is not None else 0
    below = bool(min_accepted) and accepted < min_accepted
    if below:
        warnings.append(f"整轮接受的格数 {accepted} 低于下限 {min_accepted}")
    return DepotScanRun(
        items=items,
        pages=tuple(pages),
        accepted=accepted,
        ambiguous=ambiguous,
        unknown=unknown,
        below_min_accepted=below,
        stop_reason=stop_reason,
        pages_scanned=len(pages),
        elapsed_ms=sum(page.elapsed_ms for page in pages),
        warnings=tuple(warnings),
        failures=tuple(failures),
        scanned_items=tuple(scanned),
        cells=tuple(cells),
        backends=tuple(backends),
    )


def _normalize_capture(value: Any) -> CapturedFrame:
    """取帧器的返回值 → :class:`CapturedFrame`。

    三形态：``CapturedFrame`` 原样、带 ``image`` 的对象取图与 ``backend``（设备管理器
    的 ``ScreenshotResult``）、裸帧当内存帧。
    """
    if isinstance(value, CapturedFrame):
        return value
    image = getattr(value, "image", None)
    if image is not None:
        return CapturedFrame(image=image, backend=getattr(value, "backend", None))
    return CapturedFrame(image=value)
