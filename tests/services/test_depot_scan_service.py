"""M45 W2：取帧接线的服务层契约（``DepotScanService``）。

用 W1 入库的那张真机帧当假取帧器的返回值，所以这里跑的是**真识别器**（15 格接受、
目标 id 全部命中，真值与 ``test_depot_scan.py`` 的 ``REAL_FRAME_TRUTH`` 同一份）；
错误路径（取帧失败 / 帧解不出 / 未启用）由假取帧器驱动，不占设备。

服务只做「取帧 → 归一 → 识别 → 统计」：不写库（W3）、不读数量（W2 只做身份）。
"""

from __future__ import annotations

import asyncio

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from PIL import Image

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.account import depot_scan as ds

# 用 ``import module`` 而不是 ``from ... import``：``clear_caches`` 这类模块级缓存
# 在用例之间要能被看见（与 test_depot_scan.py 同一套写法）。
from maa_api.services.account import depot_scan_service as service_module  # noqa: E402
from maa_api.services.account.depot_scan_service import (  # noqa: E402
    CapturedFrame,
    DepotFrameSource,
    DepotScanDisabled,
    DepotScanError,
    DepotScanService,
    frame_to_array,
)
from maa_api.settings import Settings, resolve_settings  # noqa: E402

FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "depot"
    / "depot_all_tab_1280x720.jpg"
)

#: W1 真机帧的结论（与 ``test_depot_scan.py`` 的 REAL_FRAME_TRUTH 一致）。
REAL_FRAME_ACCEPTED = 15
REAL_FRAME_AMBIGUOUS = 6
REAL_FRAME_UNKNOWN = 3
REAL_FRAME_TARGETS = ("7003", "7002", "7001", "4004", "4005", "4006")
REAL_FRAME_SIZE = (1280, 720)


def _settings(**account: Any) -> Settings:
    """默认设置 + 若干 ``account.*`` 覆盖（服务每次调用现读它）。"""
    settings = resolve_settings(env={})
    for key, value in account.items():
        setattr(settings.account, key, value)
    return settings


def _frame() -> np.ndarray:
    return np.asarray(Image.open(FIXTURE).convert("RGB"))


def _service(provider: Any, **account: Any) -> DepotScanService:
    return DepotScanService(provider, settings_provider=lambda: _settings(**account))


class _ScreenshotLike:
    """``ScreenshotResult`` 的最小替身：带 ``image`` 与 ``backend`` 两个属性。"""

    def __init__(self, image: Any, backend: str) -> None:
        self.image = image
        self.backend = backend


# --------------------------------------------------------------------------- #
# 正常路径：真识别器 + 假取帧器
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def default_outcome() -> service_module.DepotScanOutcome:
    """默认设置下跑一次真识别（模块级复用，省掉重复的 1.2 s）。"""
    service = _service(_frame, depot_scan_enabled=True)
    return asyncio.run(service.scan())


def test_service_scans_a_frame_from_the_provider(
    default_outcome: service_module.DepotScanOutcome,
) -> None:
    """服务装配：假取帧器给真机帧 → 真识别器 → 结果与统计。"""
    outcome = default_outcome
    assert outcome.accepted == REAL_FRAME_ACCEPTED
    assert outcome.ambiguous == REAL_FRAME_AMBIGUOUS
    assert outcome.unknown == REAL_FRAME_UNKNOWN
    assert outcome.frame == REAL_FRAME_SIZE
    assert outcome.backend is None, "内存帧没有通道"
    assert outcome.elapsed_ms > 0
    # 默认下限 8，这一帧 15 格接受 ⇒ 达标（写不写库是 W3 的闸门）。
    assert outcome.scan.min_accepted == 8
    assert outcome.below_min_accepted is False

    items = outcome.items
    for item_id in REAL_FRAME_TARGETS:
        assert item_id in items, f"{item_id} 应当在接受的格子里"
    # 未知道具如实列出，不猜 id。
    assert all(entry["top1"] is not None for entry in outcome.flat["unknown"])
    assert all("item_id" not in entry for entry in outcome.flat["unknown"])


def test_outcome_to_dict_is_flat_and_unambiguous(
    default_outcome: service_module.DepotScanOutcome,
) -> None:
    """对外形状：计数是计数、明细带 ``_cells`` 后缀，字段不重名。"""
    payload = default_outcome.to_dict()
    assert payload["accepted"] == REAL_FRAME_ACCEPTED
    assert payload["ambiguous"] == REAL_FRAME_AMBIGUOUS
    assert payload["unknown"] == REAL_FRAME_UNKNOWN
    assert len(payload["ambiguous_cells"]) == REAL_FRAME_AMBIGUOUS
    assert len(payload["unknown_cells"]) == REAL_FRAME_UNKNOWN
    assert payload["frame"] == {"width": 1280, "height": 720}
    assert payload["grid"]["confidence"] in {"prior", "fitted", "insufficient"}
    assert set(payload["items"]) >= set(REAL_FRAME_TARGETS)


def test_backend_of_the_frame_source_is_reported() -> None:
    """取帧器给的是 ``ScreenshotResult`` 形状时，通道名要跟着结果走。"""
    service = _service(lambda: _ScreenshotLike(_frame(), "core"), depot_scan_enabled=True)
    outcome = asyncio.run(service.scan())
    assert outcome.backend == "core"
    assert outcome.frame == REAL_FRAME_SIZE


def test_encoded_jpeg_bytes_are_accepted() -> None:
    """设备通道可能给已编码字节（内核 GET_IMAGE 落盘的 JPEG）：解码在服务层做。"""
    service = _service(FIXTURE.read_bytes, depot_scan_enabled=True)
    outcome = asyncio.run(service.scan())
    assert outcome.accepted == REAL_FRAME_ACCEPTED
    assert outcome.frame == REAL_FRAME_SIZE


def test_async_frame_provider_is_awaited() -> None:
    """取帧器可以是协程函数（设备管理器就是 async）。"""

    async def provider() -> CapturedFrame:
        return CapturedFrame(image=_frame(), backend="adb")

    service = _service(provider, depot_scan_enabled=True)
    outcome = asyncio.run(service.scan())
    assert (outcome.accepted, outcome.backend) == (REAL_FRAME_ACCEPTED, "adb")


def test_device_frame_source_uses_the_device_manager() -> None:
    """默认取帧器只是 ``DeviceManager.screenshot`` 的一层壳，不新造通道。"""

    class _Manager:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def screenshot(self, backend: str = "adb") -> _ScreenshotLike:
            self.calls.append(backend)
            return _ScreenshotLike(_frame(), "adb")

    manager = _Manager()
    captured = asyncio.run(DepotFrameSource(manager)())
    assert captured.backend == "adb"
    assert captured.image.shape == (720, 1280, 3)
    assert manager.calls == ["adb"]


# --------------------------------------------------------------------------- #
# 错误路径：绝不静默返回空结果
# --------------------------------------------------------------------------- #
def test_frame_provider_failure_is_a_screenshot_error() -> None:
    """取帧器抛别的异常 → ``SCREENSHOT_FAILED``，带原因，不是空结果。"""

    def provider() -> Any:
        raise RuntimeError("adb 掉了")

    service = _service(provider, depot_scan_enabled=True)
    with pytest.raises(AppError) as caught:
        asyncio.run(service.scan())
    assert caught.value.code == ErrorCode.SCREENSHOT_FAILED
    assert "adb 掉了" in str(caught.value.details.get("error"))
    assert caught.value.details["stage"] == "depot_self_scan"


def test_device_error_passes_through_unchanged() -> None:
    """设备层的错误码原样上抛：调用方要靠它区分「没连设备」和「截图坏了」。"""

    def provider() -> Any:
        raise AppError(ErrorCode.DEVICE_NOT_CONNECTED, "设备当前未连接")

    service = _service(provider, depot_scan_enabled=True)
    with pytest.raises(AppError) as caught:
        asyncio.run(service.scan())
    assert caught.value.code == ErrorCode.DEVICE_NOT_CONNECTED


def test_undecodable_frame_raises_scan_error() -> None:
    """帧解不出图像时给明确的识别错误，不做「零格结果」。"""
    service = _service(lambda: object(), depot_scan_enabled=True)
    with pytest.raises(DepotScanError):
        asyncio.run(service.scan())


def test_disabled_scan_refuses_to_run() -> None:
    """默认关闭：直接调 ``scan`` 抛 ``DepotScanDisabled``，一帧都不取。"""
    calls: list[int] = []

    def provider() -> Any:
        calls.append(1)
        return _frame()

    service = _service(provider)  # enabled 默认 false
    assert service.enabled is False
    with pytest.raises(DepotScanDisabled):
        asyncio.run(service.scan())
    assert calls == [], "未启用时不该去取帧"


def test_force_bypasses_the_enabled_switch() -> None:
    """``force=True`` 给真机验收用：开关关着也能把链路跑通。"""
    service = _service(_frame)
    outcome = asyncio.run(service.scan(force=True))
    assert outcome.accepted == REAL_FRAME_ACCEPTED


def test_frame_to_array_rejects_unsupported_types() -> None:
    """解码器的接受面：ndarray / PIL / 已编码字节，别的一律明确报错。"""
    assert frame_to_array(_frame()).shape == (720, 1280, 3)
    assert frame_to_array(Image.open(FIXTURE)).shape == (720, 1280, 3)
    assert frame_to_array(FIXTURE.read_bytes()).shape == (720, 1280, 3)
    rgba = np.zeros((4, 4, 4), dtype=np.uint8)
    assert frame_to_array(rgba).shape == (4, 4, 3)
    for bad in (None, 3, "x", np.zeros((0,), dtype=np.uint8)):
        with pytest.raises(DepotScanError):
            frame_to_array(bad)


# --------------------------------------------------------------------------- #
# 设置接线：改配置立即生效
# --------------------------------------------------------------------------- #
def test_settings_thresholds_change_the_verdict() -> None:
    """阈值与下限现读设置：同一帧在严格阈值下接受更少、在下限之上判定不达标。"""
    loose = asyncio.run(
        _service(_frame, depot_scan_enabled=True).scan()
    )
    strict = asyncio.run(
        _service(
            _frame,
            depot_scan_enabled=True,
            depot_scan_top1_min=0.95,
            depot_scan_min_accepted=24,
        ).scan()
    )
    assert strict.accepted < loose.accepted
    assert strict.scan.min_accepted == 24
    assert strict.below_min_accepted is True
    assert any("低于下限" in warning for warning in strict.scan.warnings)


def test_config_from_settings_reads_every_knob() -> None:
    """四个旋钮 → ``DepotScanConfig`` 的映射（不跑识别，省时间）。"""
    service = _service(
        _frame,
        depot_scan_enabled=True,
        depot_scan_top1_min=0.8,
        depot_scan_margin_min=0.2,
        depot_scan_min_accepted=12,
    )
    config = service.config_from_settings()
    assert (config.top1_min, config.margin_min, config.min_accepted) == (0.8, 0.2, 12)
    assert config.scales == ds.DEFAULT_SCALES
    assert service.enabled is True
