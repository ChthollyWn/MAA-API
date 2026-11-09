"""感知哈希（spec §4.5.2、§6 T2）：dHash、签名、距离与性能。

全部用例只读图像，不碰数据库与设备。
"""

from __future__ import annotations

import re
import statistics
import time

import numpy as np
from PIL import Image

from maa_api.agent.nav.recognizer import (
    DEFAULT_ROIS,
    dhash,
    distances,
    signature,
)
from tests.fakes.nav import make_screen

#: 两个 ROI：整屏 + 左上角四分之一（覆盖多 ROI 的拼接与逐段距离）。
ROIS = (
    (0.0, 0.0, 1.0, 1.0),
    (0.25, 0.25, 0.25, 0.25),
)

SEGMENT = re.compile(r"^[0-9a-f]{16}$")


def test_identical_images_have_zero_distance() -> None:
    image = make_screen("a")
    sig = signature(image, ROIS)
    assert distances(sig, sig) == [0] * len(ROIS)


def test_signature_has_one_segment_per_roi() -> None:
    sig = signature(make_screen("a"), ROIS)
    segments = sig.split(":")
    assert len(segments) == len(ROIS)
    assert all(SEGMENT.fullmatch(segment) for segment in segments)
    # 单 ROI 的返回值就是一个 16 位十六进制段。
    single = signature(make_screen("a"), DEFAULT_ROIS)
    assert SEGMENT.fullmatch(single)
    assert isinstance(dhash(make_screen("a"), DEFAULT_ROIS[0]), int)


def test_small_noise_stays_within_threshold() -> None:
    """逐像素 ±3 的确定性灰度噪声不改变判定（每段距离 ≤ 10）。"""
    image = make_screen("a")
    pixels = np.asarray(image, dtype=np.int16)
    rng = np.random.default_rng(20261109)
    noise = rng.integers(-3, 4, pixels.shape)
    noisy = Image.fromarray(np.clip(pixels + noise, 0, 255).astype(np.uint8))

    for distance in distances(signature(image, ROIS), signature(noisy, ROIS)):
        assert distance <= 10


def test_different_layouts_exceed_threshold() -> None:
    """不同名字的假图不只是颜色不同：色块位置不同，整屏 dHash 距离 > 20。"""
    first = signature(make_screen("a"), DEFAULT_ROIS)
    second = signature(make_screen("b"), DEFAULT_ROIS)
    (distance,) = distances(first, second)
    assert distance > 20


def test_roi_count_mismatch_returns_none() -> None:
    single = signature(make_screen("a"), DEFAULT_ROIS)
    double = signature(make_screen("a"), ROIS)
    assert distances(single, double) is None
    assert distances(double, single) is None


def test_hash_recognition_stays_under_200ms() -> None:
    """1280×720 的整屏签名重复 20 次，中位数 ≤ 200 ms（spec §6）。"""
    image = make_screen("a")
    durations = []
    for _ in range(20):
        started = time.perf_counter()
        signature(image, DEFAULT_ROIS)
        durations.append(time.perf_counter() - started)
    assert statistics.median(durations) <= 0.200
