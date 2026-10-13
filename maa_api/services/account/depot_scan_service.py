"""M45 W2：把整页识别器接到真实链路上（取帧 → 识别 → 结果 + 统计）。

**为什么单独一层**：:mod:`maa_api.services.account.depot_scan` 是纯函数（一帧图进、
一页结论出），按设计不碰设备、网络与数据库；而「帧从哪儿来」在仓库里已经有现成通路
—— :meth:`maa_api.services.device_service.DeviceManager.screenshot` 默认走 ADB 全分辨率
截图，ADB 通道失败时自己退回内核 ``AsstAsyncScreencap`` + ``GET_IMAGE``（M43/M44 的
道具图与 M36 的练度扫描都在用这条路）。本层只做三件事：**取一帧、归一成识别器要的
``ndarray``、调识别器并给出统计**，不新造截图通道、不写库。

**为什么取帧失败要抛异常**：空结果会被下游当成「这次仓库就是空的」。M44 的下限护栏
（``agent.depot_min_items``）与 M45 的接受格数下限（``min_accepted``）防的都是同一类
事故：一次半截识别把整表冲掉（真机实测：只读到 26 件的那次覆盖了 77 行的表）。
因此本层**绝不静默返回空结果**：

- 设备未连接 / 截图通道失败：原样抛设备层的 ``AppError``（``DEVICE_NOT_CONNECTED`` /
  ``SCREENSHOT_FAILED``），取帧器抛别的异常则包成 ``SCREENSHOT_FAILED`` 并带上原因；
- 帧解不出图像：抛 :class:`DepotScanError`；
- 识别未启用：抛 :class:`DepotScanDisabled`（调用方应先看 :attr:`DepotScanService.enabled`）。

**接受格数不足不在这里拦**：识别器只如实报告 ``below_min_accepted``（规格 §4.4），
闸门与 M44 的条目数下限一起放在 W3 的写库路径上——本层照常返回结果，由调用方决定。

**数量不在这里读**：W2 只做身份（规格 §3.1-3、§4.1 的「先拿身份、数量分批补」）。
数量要等内核 OCR 节点那一步（24 个 ``NumberOcrReplace`` 节点）落地后由 W3 合并。
"""

from __future__ import annotations

import asyncio
import io
import logging
import time

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from PIL import Image

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.account.depot_scan import (
    DepotScanConfig,
    ItemBank,
    ItemGrid,
    PanelScan,
    flatten_scan,
    load_item_bank,
    scan_panel,
)
from maa_api.settings import Settings, get_settings

__all__ = [
    "CapturedFrame",
    "DepotFrameSource",
    "DepotScanDisabled",
    "DepotScanError",
    "DepotScanOutcome",
    "DepotScanService",
    "frame_to_array",
]

logger = logging.getLogger(__name__)


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
class CapturedFrame:
    """一帧画面 + 供应商（``backend`` 是 ``adb`` / ``core``，内存帧为 ``None``）。"""

    image: Any
    backend: str | None = None


@dataclass(frozen=True, slots=True)
class DepotScanOutcome:
    """一次识别的结果与统计。

    ``flat`` 是 :func:`~maa_api.services.account.depot_scan.flatten_scan` 的纯 dict
    （``items`` / ``ambiguous`` / ``unknown`` / ``below_min_accepted``），W3 的合并入库
    直接吃它；``scan`` 保留完整的逐格结论（排障与识别记录用）。
    """

    scan: PanelScan
    flat: Mapping[str, Any]
    elapsed_ms: int
    backend: str | None = None
    frame: tuple[int, int] = (0, 0)

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
        """对外/落日志的扁平形状（计数是计数，明细带 ``_cells`` 后缀，不重名）。"""
        return {
            "backend": self.backend,
            "frame": {"width": self.frame[0], "height": self.frame[1]},
            "elapsed_ms": self.elapsed_ms,
            "grid": self.scan.to_dict()["grid"],
            "scale": self.scan.scale,
            "accepted": self.accepted,
            "ambiguous": self.ambiguous,
            "unknown": self.unknown,
            "min_accepted": self.scan.min_accepted,
            "below_min_accepted": self.below_min_accepted,
            "items": dict(self.flat.get("items") or {}),
            "ambiguous_cells": list(self.flat.get("ambiguous") or ()),
            "unknown_cells": list(self.flat.get("unknown") or ()),
            "warnings": list(self.flat.get("warnings") or ()),
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
    """

    def __init__(
        self,
        frame_source: Callable[[], Any],
        *,
        settings_provider: Callable[[], Settings] = get_settings,
        bank_provider: Callable[..., ItemBank] | None = None,
    ) -> None:
        self._frame_source = frame_source
        self._settings_provider = settings_provider
        self._bank_provider = bank_provider or load_item_bank

    # -- 配置 -------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        """``account.depot_scan_enabled``；每次调用现读。"""
        return bool(getattr(self._settings_provider().account, "depot_scan_enabled", False))

    def config_from_settings(self, *, grid: ItemGrid | None = None) -> DepotScanConfig:
        """设置里的四个旋钮 → :class:`DepotScanConfig`（默认值＝W1 台账实测值）。

        ``scales`` / ``max_candidates`` / ``top_k`` 不进设置：前三者是识别器内部的安全网
        与排障参数，没有产品语义（W1 台账「偏离台账的地方」）。
        """
        account = self._settings_provider().account
        return DepotScanConfig(
            top1_min=float(getattr(account, "depot_scan_top1_min", 0.75)),
            margin_min=float(getattr(account, "depot_scan_margin_min", 0.15)),
            min_accepted=max(int(getattr(account, "depot_scan_min_accepted", 8)), 0),
            grid=grid,
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
