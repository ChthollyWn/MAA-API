"""M45 W3a：逐屏扫描循环（``DepotScanService.scan_all_pages``）与相位搜索半径。

三块契约：

1. **相位搜索半径 40 px**（W2 真机实测横向相位在 ``77.91–114.33`` 之间漂，先验 86.6，
   极差 36 px，其中一帧真实偏移 +27.7 px）：同一张画面横移 30 px 时，旧的 ±24 px
   兜不住、40 px 能救回来；同时**不许趟过半个格子**（半格错位的伪峰仍然是 0 接受）。
2. **逐屏扫描循环**（台账 W2 的裁决）：每屏重新取帧 + 重新拟合格线；翻页走注入的
   「翻页器」（真机是内核链 ``MaaApi@DepotSelfScan@SwipeNextPage``，**不用 adb 硬滑**）；
   连续 N 屏没有新增 id 就停（横滑到底会进干员信物区，那里的格线不是 8 列 × 129.92）；
   页数上限是硬兜底；翻页失败只收手、不作废已经扫到的屏。
3. **整轮聚合闸门**：接受格数按各屏之和判，不按屏判 —— W2 实测末页接受格数会低到 7，
   按屏判会把最后几页整屏丢掉。

离线回放：假翻页器直接返回下一帧（:class:`~...DepotScanOutcome` 的
``DepotPageTurn``），所以整条用例不碰设备、不碰内核。合成夹具与
``test_depot_scan.py`` 同一套画法（大色块 + 灰底），不重复解释。
"""

from __future__ import annotations

import asyncio
import json

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from PIL import Image

from maa_api.services.account import depot_scan as ds
from maa_api.services.account import depot_scan_service as service_module
from maa_api.services.account.depot_scan_service import (
    DEFAULT_NO_NEW_PAGES,
    DEFAULT_PAGE_LIMIT,
    CapturedFrame,
    DepotChainPager,
    DepotPageTurn,
    DepotScanError,
    DepotScanService,
    DepotSweepConfig,
)
from maa_api.settings import Settings, resolve_settings

PANEL_W, PANEL_H = ds.DEFAULT_PANEL
#: 合成格线：与 test_depot_scan.py 同量级（列 130 / 行 158），但相位不同 —— 前提是
#: 几何全部从参数进来，任何一条格线都能对上。
SYNTH_GRID = ds.ItemGrid(x_first=90.0, y_first=150.0, x_pitch=130.0, y_pitch=158.0)
SYNTH_IDS = tuple(f"page_item_{index:02d}" for index in range(1, 9))


# --------------------------------------------------------------------------- #
# 合成夹具（画法与 test_depot_scan.py 一致）
# --------------------------------------------------------------------------- #
def _pattern(seed: int, size: int = 122) -> np.ndarray:
    rng = np.random.default_rng(seed)
    canvas = np.tile(rng.integers(20, 120, 3), (size, size, 1)).astype(np.uint8)
    for _ in range(5):
        height = int(rng.integers(30, 70))
        width = int(rng.integers(30, 70))
        top = int(rng.integers(0, size - height))
        left = int(rng.integers(0, size - width))
        canvas[top:top + height, left:left + width] = rng.integers(60, 256, 3)
    return canvas


def _paste(page: np.ndarray, patch: np.ndarray, cx: int, cy: int) -> None:
    height, width = patch.shape[:2]
    left, top = cx - width // 2, cy - height // 2
    x0, y0 = max(left, 0), max(top, 0)
    x1, y1 = min(left + width, page.shape[1]), min(top + height, page.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    page[y0:y1, x0:x1] = patch[y0 - top:y1 - top, x0 - left:x1 - left]


@pytest.fixture(scope="module")
def synth(tmp_path_factory: pytest.TempPathFactory) -> SimpleNamespace:
    """一库 8 张合成模板 + ``item_index.json``（不碰仓库的 ``resource/``）。"""
    root = tmp_path_factory.mktemp("depot-pages") / "resource"
    (root / "template" / "items").mkdir(parents=True, exist_ok=True)
    catalog: dict[str, dict[str, str]] = {}
    patches: dict[str, np.ndarray] = {}
    for index, item_id in enumerate(SYNTH_IDS):
        patch = _pattern(index + 11)
        patches[item_id] = patch
        icon = f"{item_id}.png"
        Image.fromarray(patch).save(root / "template" / "items" / icon)
        catalog[item_id] = {"icon": icon, "name": f"逐屏道具 {item_id}"}
    (root / "item_index.json").write_text(
        json.dumps(catalog, ensure_ascii=False), encoding="utf8"
    )
    return SimpleNamespace(
        root=root,
        patches=patches,
        bank=ds.load_item_bank(roots=[root], catalog=catalog, use_cache=False),
    )


def _page(
    synth: SimpleNamespace,
    placed: dict[str, tuple[int, int]],
    *,
    grid: ds.ItemGrid = SYNTH_GRID,
) -> np.ndarray:
    """按 ``{item_id: (col, row)}``（0 基）在灰底面板上贴出一页。"""
    page = np.full((PANEL_H, PANEL_W, 3), 128, np.uint8)
    centers = {(col, row): (x, y) for col, row, x, y in grid.centers()}
    for item_id, (col, row) in placed.items():
        _paste(page, synth.patches[item_id], *centers[(col, row)])
    return page


def _settings(**account: Any) -> Settings:
    settings = resolve_settings(env={})
    for key, value in account.items():
        setattr(settings.account, key, value)
    return settings


def _service(synth: SimpleNamespace, frames: Any, **account: Any) -> DepotScanService:
    """逐屏扫描服务：假取帧器（真机上它是 :class:`DepotFrameSource`）+ 真识别器。

    ``fit_grid=False`` 让每屏只扫一遍：这一组用例考的是**循环的编排**（什么时候翻页、
    什么时候收手、结果怎么并），相位拟合另有专门用例；`scan_all_pages` 的格线重拟合
    在 ``test_every_page_is_fitted_on_its_own`` 里单独验。
    """
    provider = frames if callable(frames) else _frames_of(frames)
    return DepotScanService(
        provider,
        settings_provider=lambda: _settings(depot_scan_enabled=True, **account),
        bank_provider=lambda **_: synth.bank,
    )


def _frames_of(frames: list[np.ndarray]) -> Any:
    """帧序列取帧器：第一次取帧给第一屏，之后每次取帧给下一帧；用完重复最后一帧。

    「用完重复最后一帧」是刻意的：真机上截图总会成功，重复的帧会让扫描循环看到
    「没有新增」而收手（也正是 W2 说的「画面卡住不动」那种情形）。
    """
    remaining = list(frames)

    def provider() -> np.ndarray:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return provider


class _FakePager:
    """离线翻页器：直接返回下一帧（真机返回 ``None``，帧另外取）。

    :param exhausted_error: 帧用完之后抛「滑不动了」（＝真机滑到底），而不是返回 ``None``。
        默认 ``False``（返回 ``None`` ⇒ 由取帧器供货，用来验「取帧器只在该取的时候被调」）；
        想验「扫到某一屏为止」的用例要显式打开它，那样页数是确定的，不依赖
        「重复的帧被识别成什么样」。
    """

    def __init__(
        self,
        frames: list[np.ndarray],
        *,
        fail_after: int | None = None,
        exhausted_error: bool = False,
    ) -> None:
        self.frames = list(frames)
        self.fail_after = fail_after
        self.exhausted_error = exhausted_error
        self.turns = 0

    async def next_page(self) -> DepotPageTurn | None:
        self.turns += 1
        if self.fail_after is not None and self.turns > self.fail_after:
            raise DepotScanError("滑不动了")
        if not self.frames:
            if self.exhausted_error:
                raise DepotScanError("滑不动了")
            return None
        return DepotPageTurn(frame=self.frames.pop(0), backend="adb")


def _run(service: DepotScanService, pager: Any, sweep: DepotSweepConfig | None = None) -> Any:
    return asyncio.run(service.scan_all_pages(pager=pager, sweep=sweep, force=True))


# --------------------------------------------------------------------------- #
# A1：相位搜索半径 40 px（W2 实测横向漂 36 px）
# --------------------------------------------------------------------------- #
#: 满屏布局：8 个模板轮着铺满 24 格（真机就是这样 —— 一整列都是同一种材料很常见）。
FULL_LAYOUT = tuple((col, row) for row in range(3) for col in range(8))


def _full_page(synth: SimpleNamespace, shift: float) -> np.ndarray:
    """相位整体横移 ``shift`` px 的满屏画面（格距不变，只动相位）。"""
    shifted = SYNTH_GRID.replace(x_first=SYNTH_GRID.x_first + shift)
    page = np.full((PANEL_H, PANEL_W, 3), 128, np.uint8)
    centers = {(col, row): (x, y) for col, row, x, y in shifted.centers()}
    for index, (col, row) in enumerate(FULL_LAYOUT):
        _paste(page, synth.patches[SYNTH_IDS[index % len(SYNTH_IDS)]], *centers[(col, row)])
    return page


def _scan_page(
    synth: SimpleNamespace,
    page: np.ndarray,
    *,
    search_max: float,
    fit_grid: bool = True,
) -> ds.PanelScan:
    return ds.scan_panel(
        page,
        synth.bank,
        config=ds.DepotScanConfig(
            grid=SYNTH_GRID, fit_grid=fit_grid, first_search_max=search_max
        ),
    )


def test_the_phase_search_radius_covers_the_measured_drift(synth: SimpleNamespace) -> None:
    """W2 实测相位在 78–114 px 之间漂（先验 86.6）⇒ 默认半径必须 ≥ 40 px。"""
    assert ds.FIRST_SEARCH_MAX >= 40.0
    assert ds.DepotScanConfig().first_search_max == ds.FIRST_SEARCH_MAX
    assert DepotSweepConfig().first_search_max == ds.FIRST_SEARCH_MAX


@pytest.mark.parametrize("shift", [0.0, 20.0, 30.0, 40.0])
def test_horizontal_phase_drift_is_recovered_by_the_fit(
    synth: SimpleNamespace, shift: float
) -> None:
    """横向相位漂到 40 px 也能拟合回来（W2 实测极差 36 px、单帧偏 +27.7 px）。"""
    scan = _scan_page(synth, _full_page(synth, shift), search_max=ds.FIRST_SEARCH_MAX)

    # 满屏 24 格都贴着道具，拟合后应当认得出绝大多数（边界格允许掉 1–2 个：
    # 相位修正后第 8 列会被面板边缘裁一点）。
    assert scan.accepted >= 21, f"偏移 {shift} px 只认出 {scan.accepted} 格"
    assert scan.grid_confidence in {"fitted", "prior"}


def test_the_search_radius_is_what_recovers_the_measured_offset(
    synth: SimpleNamespace,
) -> None:
    """实测过的 +28 px 偏移只有搜索半径够大才量得准（真机是 +27.7 px，台账 W2-B）。

    分数面在模板能滑动的范围里是平台，拟合取的是**并列解的中心**，而平台的跨度被搜索
    半径截断 ⇒ 量出来的相位**总是慢半拍**（实测：半径 20 px 时误差 ≈ 15 px，半径 40 px
    时误差 ≤ 8 px）。真机那一帧正是栽在半径上：复用先验格线整屏 0 接受。
    """
    page = _full_page(synth, 28.0)
    truth = SYNTH_GRID.x_first + 28.0

    narrow = _scan_page(synth, page, search_max=20.0)
    wide = _scan_page(synth, page, search_max=ds.FIRST_SEARCH_MAX)

    narrow_error = abs(narrow.grid.x_first - truth)
    wide_error = abs(wide.grid.x_first - truth)
    assert wide_error <= 8.0, f"±40 px 的误差应当只有几像素，实际 {wide_error:.1f}"
    assert narrow_error > wide_error, "半径不够时相位量不准（这就是放宽半径的理由）"
    assert wide.accepted >= 21, f"±40 px 应当扫得出绝大多数格子，实际 {wide.accepted}"


def test_phase_fit_never_crosses_half_a_cell(synth: SimpleNamespace) -> None:
    """放宽半径不等于允许把格线挪半个格子：半格错位仍然是 0 接受。"""
    aligned = _scan_page(synth, _full_page(synth, 0.0), search_max=ds.FIRST_SEARCH_MAX)
    assert aligned.accepted >= 21  # 对照：不偏时认得出来

    # 画面纵向错开半行（79 px）：拟合半径 40 px 也够不到，且双阈值不许猜。
    shifted = SYNTH_GRID.replace(y_first=SYNTH_GRID.y_first + SYNTH_GRID.y_pitch / 2)
    page = np.full((PANEL_H, PANEL_W, 3), 128, np.uint8)
    centers = {(col, row): (x, y) for col, row, x, y in shifted.centers()}
    for index, (col, row) in enumerate(FULL_LAYOUT):
        _paste(page, synth.patches[SYNTH_IDS[index % len(SYNTH_IDS)]], *centers[(col, row)])
    scan = _scan_page(synth, page, search_max=40.0)

    assert scan.accepted == 0


def test_same_template_on_several_cells_is_kept_as_several_landmarks(
    synth: SimpleNamespace,
) -> None:
    """同一张模板铺在多行时，每个格子都是一份证据（W3a 修的 landmark 去重口径）。

    只按 ``(col, row)`` 去重会把后写的那一行顶掉前面那一行：24 格同模板的满屏画面
    曾经只剩 2 个 landmark ⇒ 判成 ``insufficient``、相位偏了整屏救不回来。
    """
    page = np.full((PANEL_H, PANEL_W, 3), 128, np.uint8)
    centers = {(col, row): (x, y) for col, row, x, y in SYNTH_GRID.centers()}
    for col, row in FULL_LAYOUT:
        _paste(page, synth.patches[SYNTH_IDS[0]], *centers[(col, row)])
    panel = ds.panel_of_frame(page, SYNTH_GRID)

    landmarks = ds._landmarks(panel, synth.bank, SYNTH_GRID)

    assert len(landmarks) >= 3
    assert len({(item.col, item.row) for item in landmarks}) == len(landmarks)


# --------------------------------------------------------------------------- #
# A2：逐屏扫描的编排
# --------------------------------------------------------------------------- #
def test_two_pages_are_merged_into_one_round(synth: SimpleNamespace) -> None:
    """正常两屏：并集成一轮，id 是两屏的并集，逐屏明细留页号。"""
    first = _page(synth, {item_id: (index, 0) for index, item_id in enumerate(SYNTH_IDS[:4])})
    second = _page(synth, {item_id: (index, 1) for index, item_id in enumerate(SYNTH_IDS[4:])})
    # 取帧器给 1、2 两帧；翻页器只给得出第二屏，第三次翻页就滑不动了 ⇒ 停在两屏。
    service = _service(synth, [first, second], depot_scan_min_accepted=4)

    outcome = _run(
        service,
        _FakePager([second], exhausted_error=True),
        DepotSweepConfig(page_limit=5, min_page_accepted=4),
    )

    run = outcome.run
    assert run is not None
    assert run.pages_scanned == 2
    assert run.stop_reason == "page_turn_failed"
    assert set(outcome.items) == set(SYNTH_IDS)
    assert outcome.accepted == 8
    assert run.below_min_accepted is False  # 8 ≥ 下限 4
    pages = {item.item_id: item.page for item in run.scanned_items}
    assert set(pages.values()) == {1, 2}
    assert all(item_id in pages for item_id in SYNTH_IDS)
    # 逐格明细带页号与格心，便于回答「这个 id 是哪一屏哪一格认出来的」。
    assert len(run.cells) == 48
    assert {cell.page for cell in run.cells} == {1, 2}
    assert outcome.to_dict()["run"]["pages_scanned"] == 2


def test_two_consecutive_pages_without_new_ids_stop_the_sweep(
    synth: SimpleNamespace,
) -> None:
    """**连续两屏没有新增 id 就停**（W2：横滑到底会进干员信物区，那里的格线不同）。"""
    placed = {item_id: (index, 0) for index, item_id in enumerate(SYNTH_IDS)}
    page = _page(synth, placed)
    empty = _page(synth, {})  # 「干员信物区」的替身：本库模板一个都认不出
    service = _service(synth, [page])

    outcome = _run(
        service,
        _FakePager([empty, empty, empty]),
        DepotSweepConfig(page_limit=10, no_new_pages=2),
    )

    run = outcome.run
    assert run is not None
    # 第 1 屏 8 个新增 ⇒ 第 2、3 屏连续 0 新增 ⇒ 停。翻页器只被叫了两次。
    assert run.pages_scanned == 3
    assert run.stop_reason == "no_new"
    assert set(outcome.items) == set(SYNTH_IDS)
    assert outcome.accepted == 8  # 空白屏不贡献接受格


def test_one_empty_page_is_not_enough_to_stop(synth: SimpleNamespace) -> None:
    """阈值是可配的：``no_new_pages=1`` 时一屏无新增就停（默认 2 屏更保守）。"""
    first = _page(synth, {SYNTH_IDS[0]: (0, 0)})
    empty = _page(synth, {})
    assert DEFAULT_NO_NEW_PAGES == 2

    strict = _run(
        _service(synth, [first]),
        _FakePager([empty, empty]),
        DepotSweepConfig(no_new_pages=1),
    )

    assert strict.run is not None
    assert strict.run.pages_scanned == 2  # 第 2 屏无新增 ⇒ 立刻停
    assert strict.run.stop_reason == "no_new"


def test_page_limit_is_a_hard_cap(synth: SimpleNamespace) -> None:
    """页数上限（默认 10）是硬兜底：每一屏都有新增时也一定会停。"""
    assert DEFAULT_PAGE_LIMIT == 10
    # 每屏给不同的 id，保证「一直有新增」，只有页数上限能让它停。
    pages = [
        _page(synth, {SYNTH_IDS[index]: (0, 0)}) for index in range(len(SYNTH_IDS))
    ]
    service = _service(synth, [pages[0]])

    outcome = _run(
        service,
        _FakePager(list(pages[1:])),
        DepotSweepConfig(page_limit=3, min_page_accepted=1),
    )

    assert outcome.run is not None
    assert outcome.run.pages_scanned == 3
    assert outcome.run.stop_reason == "page_limit"
    assert len(outcome.items) == 3


def test_page_turn_failure_keeps_what_was_scanned(synth: SimpleNamespace) -> None:
    """翻页失败**不致命**：已经扫到的屏照常有效，轮次记 ``page_turn_failed``。"""
    first = _page(synth, {item_id: (index, 0) for index, item_id in enumerate(SYNTH_IDS[:4])})
    second = _page(synth, {item_id: (index, 1) for index, item_id in enumerate(SYNTH_IDS[4:])})
    pager = _FakePager([second], fail_after=1)
    service = _service(synth, [first])

    outcome = _run(service, pager, DepotSweepConfig(page_limit=10, min_page_accepted=1))

    run = outcome.run
    assert run is not None
    assert run.pages_scanned == 2
    assert run.stop_reason == "page_turn_failed"
    assert set(outcome.items) == set(SYNTH_IDS)  # 两屏的结果都保住了
    assert run.failures and "翻页失败" in run.failures[0]
    # 翻页失败会重试两次，然后在第二轮上收手（失败不入库这件事与它无关）。
    assert pager.turns == 3


def test_unusable_pages_stop_the_sweep(synth: SimpleNamespace) -> None:
    """连续两屏「整屏不可用」（低于接受格数下限）也收手：那是滑进别的格线区了。"""
    first = _page(synth, {item_id: (index, 0) for index, item_id in enumerate(SYNTH_IDS[:4])})
    empty = _page(synth, {})
    # 取帧器给 1 帧后固定重复空白帧；no_new_pages 调大，让「不可用」成为唯一的收手理由。
    service = _service(synth, [first, empty], depot_scan_min_accepted=6)

    outcome = _run(
        service,
        _FakePager([empty]),
        DepotSweepConfig(page_limit=10, no_new_pages=9),
    )

    assert outcome.run is not None
    assert outcome.run.pages_scanned == 3  # 第 2、3 屏连续不可用（各 0 格 < 3）
    assert outcome.run.stop_reason == "unusable"


def test_round_gate_aggregates_pages_not_each_page(synth: SimpleNamespace) -> None:
    """闸门按**整轮聚合**判（W2 实测末页接受格数会低到 7，按屏判会把最后几页丢掉）。"""
    first = _page(synth, {item_id: (index, 0) for index, item_id in enumerate(SYNTH_IDS[:4])})
    second = _page(synth, {item_id: (index, 1) for index, item_id in enumerate(SYNTH_IDS[4:])})
    service = _service(synth, [first], depot_scan_min_accepted=12, depot_scan_max_pages=2)

    outcome = _run(
        service,
        _FakePager([second]),
        DepotSweepConfig(page_limit=2, min_page_accepted=1),
    )

    run = outcome.run
    assert run is not None
    # 单屏 4 格 < 12，整轮 8 格也 < 12 ⇒ 整轮不达标（单屏判的话第一屏就会被丢掉）。
    assert run.accepted == 8
    assert run.below_min_accepted is True
    assert any("整轮接受的格数" in warning for warning in run.warnings)


def test_every_page_is_fitted_on_its_own(synth: SimpleNamespace) -> None:
    """每屏都重新拟合：第二屏的相位偏出窗口余量（25 px）也得认出来。

    第二屏刻意只放**分散的 3 格**（拟合至少要 3 个 landmark）：稀疏的一屏最能说明
    「这一屏是自己量的相位」，而不是靠上一屏的格线蒙对。
    """
    first = _page(synth, {SYNTH_IDS[0]: (0, 0)})
    drift = SYNTH_GRID.replace(x_first=SYNTH_GRID.x_first + 25)
    second = _page(
        synth,
        {SYNTH_IDS[1]: (0, 1), SYNTH_IDS[2]: (3, 0), SYNTH_IDS[3]: (6, 2)},
        grid=drift,
    )
    # 这一组显式打开拟合（`_service` 的默认是关的，见它的 docstring），并把先验格线
    # 指到合成格线（否则会去读本机内核的几何，那属于真机资源，不该出现在这组用例里）。
    service = DepotScanService(
        _frames_of([first, second]),
        settings_provider=lambda: _settings(depot_scan_enabled=True),
        bank_provider=lambda **_: synth.bank,
    )
    config = service.config_from_settings(grid=SYNTH_GRID)

    outcome = asyncio.run(
        service.scan_all_pages(
            pager=_FakePager([second]),
            config=config,
            sweep=DepotSweepConfig(page_limit=2, min_page_accepted=1),
            force=True,
        )
    )

    # 第二屏那 3 个道具在偏了 25 px 的画面上，靠「这一屏自己拟合出来的相位」认出来。
    assert {SYNTH_IDS[1], SYNTH_IDS[2], SYNTH_IDS[3]} <= set(outcome.items)
    assert set(outcome.items) >= {SYNTH_IDS[0]}
    assert outcome.run is not None
    assert outcome.run.pages_scanned == 2
    second_scan = outcome.run.pages[1].scan
    assert second_scan is not None
    assert second_scan.grid_confidence == "fitted"
    assert second_scan.grid.x_first >= SYNTH_GRID.x_first + 15


def test_sweep_reads_the_two_new_settings(synth: SimpleNamespace) -> None:
    """两个收手条件现读设置（``account.depot_scan_max_pages`` / ``_no_new_pages``）。"""
    service = _service(synth, [_page(synth, {})], depot_scan_max_pages=4, depot_scan_no_new_pages=3)

    sweep = service.sweep_from_settings()

    assert (sweep.page_limit, sweep.no_new_pages) == (4, 3)
    assert sweep.first_search_max == ds.FIRST_SEARCH_MAX


# --------------------------------------------------------------------------- #
# A3：真机翻页器（提交内核链，不用 adb 硬滑）
# --------------------------------------------------------------------------- #
class _FakeClient:
    """``CoreClient`` 的最小替身：记下每一次 APPEND_TASK / START。"""

    def __init__(self) -> None:
        self.appended: list[tuple[str, dict[str, Any]]] = []
        self.starts = 0

    async def append_task(self, type_name: str, params: dict[str, Any] | None = None) -> int:
        self.appended.append((type_name, dict(params or {})))
        return len(self.appended)

    async def start(self) -> bool:
        self.starts += 1
        return True


def test_chain_pager_submits_the_overlay_chain() -> None:
    """翻页器提交的是 ``Custom`` + 链上的节点名（``@Start`` / ``@SwipeNextPage``）。"""
    client = _FakeClient()
    pager = DepotChainPager(client, settle_seconds=0.0)

    async def scenario() -> None:
        await pager.goto_pager_start()
        await pager.next_page()

    asyncio.run(scenario())

    assert client.appended == [
        ("Custom", {"task_names": [DepotChainPager.START_TASK]}),
        ("Custom", {"task_names": [DepotChainPager.NEXT_PAGE_TASK]}),
    ]
    assert DepotChainPager.START_TASK == "MaaApi@DepotSelfScan@Start"
    assert DepotChainPager.NEXT_PAGE_TASK == "MaaApi@DepotSelfScan@SwipeNextPage"
    assert client.starts == 2


def test_chain_pager_reports_a_rejected_task() -> None:
    """内核不收任务时明确报错（翻页失败由扫描循环兜成「停在上一屏」）。"""

    class _Refusing(_FakeClient):
        async def append_task(self, type_name: str, params: dict[str, Any] | None = None) -> int:
            return 0

    pager = DepotChainPager(_Refusing(), settle_seconds=0.0)

    with pytest.raises(DepotScanError):
        asyncio.run(pager.next_page())


def test_chain_pager_waits_for_the_injected_readiness() -> None:
    """``ready`` 注入时按它收尾（W3b 真机可接内核的 TaskChainCompleted 回调）。"""
    client = _FakeClient()
    checks: list[int] = []

    async def ready() -> bool:
        checks.append(1)
        return len(checks) >= 2

    pager = DepotChainPager(client, ready=ready, settle_seconds=0.0)
    asyncio.run(pager.next_page())

    assert len(checks) == 2


def test_pager_requires_a_transport() -> None:
    """既没有 client 也没有 submitter 时立刻报错，而不是运行期才炸。"""
    with pytest.raises(ValueError):
        DepotChainPager()


def test_sweep_without_a_pager_scans_one_page(synth: SimpleNamespace) -> None:
    """不传翻页器时只扫当前一屏（离线回放/«不想翻页»的调用方）。"""
    frame = _page(synth, {SYNTH_IDS[0]: (0, 0)})
    service = _service(synth, [frame])

    outcome = asyncio.run(service.scan_all_pages(force=True))

    assert outcome.run is not None
    assert (outcome.run.pages_scanned, outcome.run.stop_reason) == (1, "no_pager")
    assert outcome.frame == (PANEL_W, PANEL_H)


def test_sweep_capture_failure_stays_fatal(synth: SimpleNamespace) -> None:
    """取帧失败仍然是致命的（翻页失败才是「收手」）：一屏都取不到就不该有结论。"""
    provider = lambda: (_ for _ in ()).throw(RuntimeError("adb 掉了"))  # noqa: E731
    service = DepotScanService(
        provider,
        settings_provider=lambda: _settings(depot_scan_enabled=True),
        bank_provider=lambda **_: synth.bank,
    )

    from maa_api.domain.errors import AppError, ErrorCode

    with pytest.raises(AppError) as caught:
        asyncio.run(service.scan_all_pages(force=True))
    assert caught.value.code == ErrorCode.SCREENSHOT_FAILED


def test_sweep_never_returns_an_empty_result_silently(synth: SimpleNamespace) -> None:
    """一屏都认不出时如实记 warning（空结果不能被下游当成「仓库是空的」）。"""
    empty = _page(synth, {})
    service = _service(synth, [empty], depot_scan_min_accepted=1)

    outcome = _run(service, None)

    assert outcome.items == {}
    assert any("整轮没有识别到任何道具" in warning for warning in outcome.flat["warnings"])


def test_frames_are_only_captured_when_the_pager_has_none(synth: SimpleNamespace) -> None:
    """翻页器给了帧就不再取帧；给了 ``None`` 才走取帧器（真机的分工）。"""
    calls: list[int] = []
    frames = [
        _page(synth, {SYNTH_IDS[0]: (0, 0)}),
        _page(synth, {SYNTH_IDS[1]: (0, 0)}),
    ]

    def provider() -> CapturedFrame:
        calls.append(1)
        return CapturedFrame(image=frames[min(len(calls) - 1, 1)], backend="core")

    service = DepotScanService(
        provider,
        settings_provider=lambda: _settings(depot_scan_enabled=True),
        bank_provider=lambda **_: synth.bank,
    )
    pager = _FakePager([])  # 永远返回 None ⇒ 每一屏都要自己取帧

    outcome = asyncio.run(
        service.scan_all_pages(pager=pager, sweep=DepotSweepConfig(page_limit=2), force=True)
    )

    assert len(calls) == 2
    assert outcome.run is not None
    assert outcome.run.backends == ("core",)
    assert outcome.backend == "core"


def test_service_module_exports_the_new_contract() -> None:
    """新公开面（逐屏扫描的配置、结果与翻页器）都在 ``__all__`` 里。"""
    for name in ("DepotSweepConfig", "DepotScanRun", "DepotPager", "DepotChainPager"):
        assert name in service_module.__all__, name
