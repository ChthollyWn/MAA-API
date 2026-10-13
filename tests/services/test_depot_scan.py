"""M45 W1：仓库整页识别器（网格拟合 + masked NCC + 双阈值）。

**为什么不依赖真机**：合成图自己造 —— 把假模板按已知格线贴到灰底面板上，真值就是
「贴了哪些 id、贴在哪个格子」，于是几何、命中、阈值、误接受都能断言。真机回归单独一条，
用入库存档的 1280×720 截图（``fixtures/depot/depot_all_tab_1280x720.jpg``，162 KB）跑，
本机没有内核模板库（``resource/`` 不进版本库）时整条 skip。

W0 台账（``docs/superpowers/ledgers/2026-10-13-m45-depot-selfscan.md``）里那条「半格错位
24 个位置误接受 0/24」在这里用 4 个方向的半格错位 + 一个整页错位复现。
"""

from __future__ import annotations

import json
import time

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from PIL import Image

from maa_api.services.account import depot_scan as ds

PANEL_W, PANEL_H = ds.DEFAULT_PANEL

#: 合成页用的格线：故意与模块默认值不同，逼出「几何是算出来的、不是写死的」。
SYNTH_GRID = ds.ItemGrid(x_first=90.0, y_first=150.0, x_pitch=130.0, y_pitch=158.0)

#: 合成模板里的正样本 id 与它们的近重复兄弟（margin 会被压到阈值以下）。
SYNTH_IDS = tuple(f"test_item_{index:02d}" for index in range(1, 9))
DUPLICATE_IDS = ("test_dup_a", "test_dup_b")

FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "depot" / "depot_all_tab_1280x720.jpg"
)

#: 真机帧的真值（逐格肉眼核对过，见本波台账）：贴的是「1-based 列行 → item_id」。
REAL_FRAME_TRUTH = {
    (1, 1): "4004",
    (2, 1): "EPGS_COIN",
    (5, 1): "7003",
    (6, 1): "7002",
    (8, 1): "randomMaterial_act42side",
    (1, 2): "4005",
    (2, 2): "REP_COIN",
    (4, 2): "EXTERMINATION_AGENT",
    (5, 2): "classic_gacha",
    (8, 2): "randomMaterial_message1",
    (1, 3): "4006",
    (2, 3): "CRISIS_SHOP_COIN_V2",
    (4, 3): "EXTERMINATION_AGENT",
    (5, 3): "7001",
    (8, 3): "randomMaterial_amiya",
}

#: 真机帧上按 W0 量出来的格心（1066 空间，21 件道具的局部 NCC 峰回归）。
REAL_FRAME_GRID = {"x_first": 86.95, "x_pitch": 129.57, "y_first": 155.05, "y_pitch": 157.79}


# --------------------------------------------------------------------------- #
# 合成夹具
# --------------------------------------------------------------------------- #
def _pattern(seed: int, size: int = 122) -> np.ndarray:
    """一张假道具图：底色 + 几块大色块 —— mask 全覆盖、方差够大、两两可区分。

    用大色块而不是逐像素噪声：真图标是低频结构，错几像素分数只掉一点（真机帧上相位
    偏 3 px 仍有 0.90），噪声图一错位就掉到 0.2，会把「窗口余量」这件事测歪。
    """
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
    """把 ``patch`` 按中心 ``(cx, cy)`` 贴进 ``page``（越界就裁，和真机一样）。"""
    height, width = patch.shape[:2]
    left, top = cx - width // 2, cy - height // 2
    x0, y0 = max(left, 0), max(top, 0)
    x1, y1 = min(left + width, page.shape[1]), min(top + height, page.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    page[y0:y1, x0:x1] = patch[y0 - top:y1 - top, x0 - left:x1 - left]


def _build_bank(root: Path, patches: dict[str, np.ndarray]) -> ds.ItemBank:
    """把假模板写成 ``<root>/template/items/<id>.png`` + ``item_index.json`` 再加载。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "template" / "items").mkdir(parents=True, exist_ok=True)
    catalog: dict[str, dict[str, str]] = {}
    for item_id, patch in patches.items():
        icon = f"{item_id}.png"
        Image.fromarray(patch).save(root / "template" / "items" / icon)
        catalog[item_id] = {"icon": icon, "name": f"合成道具 {item_id}"}
    (root / "item_index.json").write_text(
        json.dumps(catalog, ensure_ascii=False), encoding="utf8"
    )
    return ds.load_item_bank(roots=[root], catalog=catalog, use_cache=False)


#: 画面上有、模板库里没有的花色（造 unknown 用）。
OUTLIER_ID = "test_outlier"


@pytest.fixture(scope="module")
def synth(tmp_path_factory: pytest.TempPathFactory) -> SimpleNamespace:
    """一库小模板：8 个正样本 + 1 对近重复；另留一张**不进库**的花色给 unknown 用。"""
    root = tmp_path_factory.mktemp("depot-synth") / "resource"
    patches = {item_id: _pattern(index + 1) for index, item_id in enumerate(SYNTH_IDS)}
    base = _pattern(101)
    twin = base.copy()
    twin[24:32, 24:32] = 255 - twin[24:32, 24:32]  # 只改一小块 —— NCC 仍会很高
    patches[DUPLICATE_IDS[0]] = base
    patches[DUPLICATE_IDS[1]] = twin
    patches[OUTLIER_ID] = _pattern(999)
    in_library = {key: value for key, value in patches.items() if key != OUTLIER_ID}
    bank = _build_bank(root, in_library)
    return SimpleNamespace(root=root, bank=bank, patches=patches)


def _page(
    synth: SimpleNamespace,
    placed: dict[str, tuple[int, int]],
    *,
    grid: ds.ItemGrid = SYNTH_GRID,
    background: int = 128,
) -> np.ndarray:
    """按 ``{item_id: (col, row)}``（0 基）在灰底面板上贴出一页。"""
    page = np.full((PANEL_H, PANEL_W, 3), background, np.uint8)
    centers = {(col, row): (x, y) for col, row, x, y in grid.centers()}
    for item_id, (col, row) in placed.items():
        _paste(page, synth.patches[item_id], *centers[(col, row)])
    return page


def _scan(
    synth: SimpleNamespace,
    page: np.ndarray,
    *,
    grid: ds.ItemGrid = SYNTH_GRID,
    fit_grid: bool = False,
    **config: object,
) -> ds.PanelScan:
    return ds.scan_panel(
        page,
        synth.bank,
        config=ds.DepotScanConfig(grid=grid, fit_grid=fit_grid, **config),  # type: ignore[arg-type]
    )


def _accepted(scan: ds.PanelScan) -> dict[tuple[int, int], str]:
    """``{(1-based 列, 1-based 行): item_id}``，只收 accepted。"""
    return {
        (cell.col + 1, cell.row + 1): cell.matches[0].item_id
        for cell in scan.cells
        if cell.status == "accepted" and cell.matches
    }


# --------------------------------------------------------------------------- #
# 几何与命中
# --------------------------------------------------------------------------- #
def test_synthetic_page_hits_every_pasted_item(synth: SimpleNamespace) -> None:
    placed = {item_id: (index % 4, index // 4) for index, item_id in enumerate(SYNTH_IDS)}
    page = _page(synth, placed)

    scan = _scan(synth, page)

    expected = {
        (col + 1, row + 1): item_id for item_id, (col, row) in placed.items()
    }
    assert _accepted(scan) == expected
    # 没贴东西的格子必须是 unknown（灰底上没有任何模板），而不是硬猜一个 id。
    assert scan.accepted == len(placed)
    assert scan.ambiguous == 0
    assert scan.unknown == 24 - len(placed)
    assert all(cell.matches == () or cell.top1 < 0.75 for cell in scan.cells if cell.status == "unknown")


def test_geometry_is_derived_not_hardcoded(synth: SimpleNamespace) -> None:
    """换一套格线（相位 + 格距都不同）照样能对上 —— 几何是从参数来的。"""
    other = ds.ItemGrid(x_first=104.0, y_first=170.0, x_pitch=125.0, y_pitch=150.0)
    placed = {SYNTH_IDS[0]: (0, 0), SYNTH_IDS[3]: (7, 0), SYNTH_IDS[5]: (2, 2)}
    page = _page(synth, placed, grid=other)

    scan = _scan(synth, page, grid=other)

    assert _accepted(scan) == {(0 + 1, 0 + 1): SYNTH_IDS[0], (7 + 1, 0 + 1): SYNTH_IDS[3], (2 + 1, 2 + 1): SYNTH_IDS[5]}


def test_eighth_column_is_clipped_not_dropped(synth: SimpleNamespace) -> None:
    """第 8 列贴着右边界：内核按「匹配窗口整块在面板内」判会把整列丢掉，我们只裁不丢。

    第 8 列格心 1005 时道具本身完整落在面板里（右边界 1066），但 162 宽的匹配窗口会越界
    19 px —— 内核 `DepotImageAnalyzer::analyze_base_rect` 的 ``include()`` 就是按后者判的，
    于是它只扫 21 格。我们用「单元格裁掉 ≤ :data:`CELL_MAX_CLIP` px 就照常识别」。
    """
    kernel_like = ds.ItemGrid(x_first=101.6, y_first=158.0, x_pitch=129.0, y_pitch=158.0)
    page = _page(synth, {SYNTH_IDS[7]: (7, 0)}, grid=kernel_like)

    scan = _scan(synth, page, grid=kernel_like)
    cell = next(item for item in scan.cells if item.col == 7 and item.row == 0)
    cx = int(round(kernel_like.x_first + 7 * kernel_like.x_pitch))

    assert kernel_like.window_in_panel(cx, 158) is False  # 内核口径：整列丢
    assert kernel_like.in_panel(cx, 158) is True  # 单元格没被裁
    assert cell.clipped is True
    assert cell.item_id == SYNTH_IDS[7]


def test_clipped_cell_is_still_evaluated(synth: SimpleNamespace) -> None:
    """道具被面板边缘裁 3 px（内核相位 105 时第 8 列就是这样）也照样打分，不整格丢掉。"""
    cut = ds.ItemGrid(x_first=105.0, y_first=158.0, x_pitch=129.0, y_pitch=158.0)
    page = _page(synth, {SYNTH_IDS[7]: (7, 0)}, grid=cut)

    scan = _scan(synth, page, grid=cut)
    cell = next(item for item in scan.cells if item.col == 7 and item.row == 0)
    cx = int(round(cut.x_first + 7 * cut.x_pitch))

    assert cut.cell_cut(cx, 158) == 3
    assert cut.in_panel(cx, 158) is False  # 一格都不许裁 ⇒ 判死
    assert cut.in_panel(cx, 158, max_cut=ds.CELL_MAX_CLIP) is True  # 我们的口径 ⇒ 照常评估
    assert cell.matches != ()  # 打过分了（不是「面板外」那一支直接扔掉）


def test_cell_outside_panel_stays_unknown(synth: SimpleNamespace) -> None:
    """整列跑到面板外时不猜：状态 unknown、没有候选。"""
    outside = SYNTH_GRID.replace(x_first=float(PANEL_W + 40))

    scan = _scan(synth, _page(synth, {}), grid=outside)

    assert scan.accepted == 0
    assert scan.unknown == 24
    assert all(cell.matches == () for cell in scan.cells)


def test_grid_fit_recovers_a_scrolled_page(synth: SimpleNamespace) -> None:
    """画面整体滚过（相位偏 20/14 px）时，先验扫不出东西、拟合能拉回来。"""
    shifted = SYNTH_GRID.replace(x_first=SYNTH_GRID.x_first + 12, y_first=SYNTH_GRID.y_first + 20)
    placed = {item_id: (index, index % 2) for index, item_id in enumerate(SYNTH_IDS)}
    page = _page(synth, placed, grid=shifted)

    blind = _scan(synth, page)
    assert blind.accepted <= 1  # 先验格线偏出窗口余量 ⇒ 基本扫不到

    fitted = _scan(synth, page, fit_grid=True)

    assert fitted.grid_confidence == "fitted"
    assert _accepted(fitted) == {(col + 1, row + 1): item_id for item_id, (col, row) in placed.items()}
    # 判据是「新格线能不能解释画面上真正贴了东西的那些行」，不是参数逐字相等：
    # 分数面在窗口余量内是平台，行距/相位可以差几像素仍然等效（横向余量 ±20 px 更宽，
    # 所以拟合不动 x 也是对的 —— 平台内并列时先验胜出，改格线要重扫一遍，不值得）。
    for row in sorted({row for _col, row in placed.values()}):
        truth_y = shifted.y_first + shifted.y_pitch * row
        assert abs(fitted.grid.y_first + fitted.grid.y_pitch * row - truth_y) <= 8
    assert abs(fitted.grid.x_first - SYNTH_GRID.x_first) <= ds.FIRST_SEARCH_MAX


def test_grid_fit_keeps_prior_when_it_is_already_right(synth: SimpleNamespace) -> None:
    """先验本来就在分数平台上时不许乱动格线（改了要重扫一遍，白花时间）。"""
    page = _page(synth, {item_id: (index, 1) for index, item_id in enumerate(SYNTH_IDS)})

    scan = _scan(synth, page, fit_grid=True)

    assert scan.grid_confidence == "prior"
    assert (scan.grid.x_first, scan.grid.x_pitch) == (SYNTH_GRID.x_first, SYNTH_GRID.x_pitch)
    assert (scan.grid.y_first, scan.grid.y_pitch) == (SYNTH_GRID.y_first, SYNTH_GRID.y_pitch)


def test_grid_fit_without_landmarks_is_insufficient(synth: SimpleNamespace) -> None:
    """空页上没有 landmark ⇒ 明确报「没拟合」，而不是假装拟合成功。"""
    scan = _scan(synth, _page(synth, {}), fit_grid=True)

    assert scan.grid_confidence == "insufficient"
    assert any("landmark" in warning for warning in scan.warnings)


# --------------------------------------------------------------------------- #
# 误接受：半格错位
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "offset",
    [
        (SYNTH_GRID.x_pitch / 2, 0.0),
        (-SYNTH_GRID.x_pitch / 2, 0.0),
        (0.0, SYNTH_GRID.y_pitch / 2),
        (0.0, -SYNTH_GRID.y_pitch / 2),
    ],
    ids=["right", "left", "down", "up"],
)
def test_half_cell_offset_is_never_accepted(synth: SimpleNamespace, offset: tuple[float, float]) -> None:
    """W0 的「半格错位」对照：每个格子只看到半件道具，一个都不许接受。"""
    shifted = SYNTH_GRID.replace(
        x_first=SYNTH_GRID.x_first + offset[0], y_first=SYNTH_GRID.y_first + offset[1]
    )
    placed = {item_id: (index % 4, index // 4) for index, item_id in enumerate(SYNTH_IDS)}
    page = _page(synth, placed, grid=shifted)

    scan = _scan(synth, page)

    assert scan.accepted == 0
    assert _accepted(scan) == {}


def test_half_cell_offset_is_never_accepted_with_fit(synth: SimpleNamespace) -> None:
    """错位超过拟合搜索半径（±24 px）时，拟合也不许把它当成新格线。"""
    shifted = SYNTH_GRID.replace(y_first=SYNTH_GRID.y_first + SYNTH_GRID.y_pitch / 2)
    placed = {item_id: (index % 4, index // 4) for index, item_id in enumerate(SYNTH_IDS)}
    page = _page(synth, placed, grid=shifted)

    scan = _scan(synth, page, fit_grid=True)

    assert scan.accepted == 0


# --------------------------------------------------------------------------- #
# 双阈值与三种结论
# --------------------------------------------------------------------------- #
def _matches(*scores: float) -> tuple[ds.Match, ...]:
    return tuple(
        ds.Match(item_id=f"m{rank}", name=f"m{rank}", score=score, rank=rank)
        for rank, score in enumerate(scores)
    )


@pytest.mark.parametrize(
    ("scores", "expected"),
    [
        ((0.76, 0.60), "accepted"),  # 0.76 / margin 0.16 ⇒ 接受
        ((0.75, 0.60), "accepted"),  # 恰好卡在下限上：两个比较都是 >=
        ((0.74, 0.58), "unknown"),  # 0.74 / margin 0.16 ⇒ 分数不够
        ((0.76, 0.62), "ambiguous"),  # 0.76 / margin 0.14 ⇒ 差得不够
        ((0.99, 0.98), "ambiguous"),  # 分数很高但近重复 ⇒ 仍然不猜
        ((0.60,), "unknown"),  # 只有一个候选且分数低
        ((), "unknown"),  # 没有候选
    ],
)
def test_accept_rule_boundaries(scores: tuple[float, ...], expected: str) -> None:
    assert (
        ds._classify(  # noqa: SLF001 - 阈值判定就是这个模块的核心契约
            _matches(*scores),
            top1_min=ds.DEFAULT_TOP1_MIN,
            margin_min=ds.DEFAULT_MARGIN_MIN,
        )
        == expected
    )


def test_thresholds_are_configurable_end_to_end(synth: SimpleNamespace) -> None:
    """两个阈值都真的走到判决：放宽它们时，本来被拦下的格子会变成 accepted。"""
    page = np.full((PANEL_H, PANEL_W, 3), 128, np.uint8)
    centers = {(col, row): (x, y) for col, row, x, y in SYNTH_GRID.centers()}
    _paste(page, synth.patches[OUTLIER_ID], *centers[(0, 0)])

    default = _scan(synth, page)
    assert default.cells[0].status == "unknown"  # 分数 0.5 左右，够不到 0.75

    relaxed = _scan(
        synth, page, top1_min=default.cells[0].top1 - 0.05, margin_min=0.0
    )
    assert relaxed.cells[0].status == "accepted"
    assert relaxed.cells[0].item_id is not None

    twin_page = _page(synth, {DUPLICATE_IDS[0]: (0, 0)})
    ambiguous = _scan(synth, twin_page)
    assert ambiguous.cells[0].status == "ambiguous"

    no_margin = _scan(synth, twin_page, margin_min=0.0)
    assert no_margin.cells[0].status == "accepted"


def test_near_duplicate_templates_are_ambiguous(synth: SimpleNamespace) -> None:
    """近重复模板（W0 的 ``ap_supply_lt_*`` 家族）判「歧义」，不给一个错答案。"""
    page = _page(synth, {DUPLICATE_IDS[0]: (0, 0)})

    scan = _scan(synth, page)
    cell = scan.cells[0]

    assert cell.status == "ambiguous"
    assert cell.top1 >= 0.75
    assert cell.margin < ds.DEFAULT_MARGIN_MIN
    assert {match.item_id for match in cell.matches[:2]} == set(DUPLICATE_IDS)


def test_item_missing_from_library_is_unknown(synth: SimpleNamespace) -> None:
    """画面上的美术不在模板库里 ⇒ unknown，不挑一个「最像的」写进去。"""
    page = np.full((PANEL_H, PANEL_W, 3), 128, np.uint8)
    centers = {(col, row): (x, y) for col, row, x, y in SYNTH_GRID.centers()}
    _paste(page, synth.patches["test_outlier"], *centers[(0, 0)])

    scan = _scan(synth, page)
    cell = scan.cells[0]

    assert cell.status == "unknown"
    assert cell.item_id is None
    assert cell.top1 < ds.DEFAULT_TOP1_MIN


# --------------------------------------------------------------------------- #
# 报告形状与下限护栏
# --------------------------------------------------------------------------- #
def test_flatten_scan_reports_pure_dict(synth: SimpleNamespace) -> None:
    placed = {SYNTH_IDS[0]: (0, 0), DUPLICATE_IDS[0]: (1, 0), OUTLIER_ID: (2, 0)}
    scan = _scan(synth, _page(synth, placed))

    flat = ds.flatten_scan(scan)

    assert set(flat) == {"items", "accepted", "ambiguous", "unknown", "below_min_accepted", "warnings"}
    assert list(flat["items"]) == [SYNTH_IDS[0]]
    assert flat["items"][SYNTH_IDS[0]] >= ds.DEFAULT_TOP1_MIN
    assert flat["accepted"] == 1
    # 22 个空格子（灰底上什么都没有）也是 unknown —— 「没贴东西」与「贴了但库里没有」
    # 在结论上是同一件事：不猜。
    assert len(flat["ambiguous"]) == 1 and len(flat["unknown"]) == 22
    assert flat["ambiguous"][0]["col"] == 2 and flat["unknown"][0]["col"] == 3
    assert flat["below_min_accepted"] is True  # 默认下限 8，这页只有 1 格


def test_min_accepted_guard_is_reported_not_enforced(synth: SimpleNamespace) -> None:
    """下限只在结果里如实报告 + 记 warning；写不写库由调用方决定（W3）。"""
    page = _page(synth, {SYNTH_IDS[0]: (0, 0), SYNTH_IDS[1]: (1, 0)})

    below = _scan(synth, page)
    assert below.accepted == 2 and below.below_min_accepted is True
    assert any("低于下限" in warning for warning in below.warnings)

    relaxed = _scan(synth, page, min_accepted=2)
    assert relaxed.below_min_accepted is False
    assert not any("低于下限" in warning for warning in relaxed.warnings)

    off = _scan(synth, page, min_accepted=0)
    assert off.below_min_accepted is False

    assert ds.flatten_scan(off)["below_min_accepted"] is False


def test_candidate_narrowing_keeps_the_answer(synth: SimpleNamespace) -> None:
    """颜色预筛（候选集收窄）只决定「谁参与打分」，不改变答案。"""
    placed = {item_id: (index, index % 2) for index, item_id in enumerate(SYNTH_IDS)}
    page = _page(synth, placed)

    full = _scan(synth, page)
    narrowed = _scan(synth, page, max_candidates=4)

    assert _accepted(narrowed) == _accepted(full)
    # 收窄本身：limit 小于库大小时只回 limit 个下标，0/超界时回全部。
    fingerprint = ds.fingerprint_rgb(page[0:122, 0:122], None)
    assert len(ds.narrow_candidates(page[0:122, 0:122], (fingerprint,) * 3, 2)) == 2
    assert len(ds.narrow_candidates(page[0:122, 0:122], (fingerprint,) * 3, 0)) == 3


def test_load_item_bank_without_templates_raises(tmp_path: Path) -> None:
    """一张模板都没有 ⇒ 抛错让调用方判「整轮失败」，而不是拿空库当「什么都没有」。"""
    with pytest.raises(ValueError):
        ds.load_item_bank(roots=[tmp_path], catalog={"7003": {"icon": "7003.png"}}, use_cache=False)


# --------------------------------------------------------------------------- #
# 真机帧回归（本机有内核模板库时才跑）
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def real_bank() -> ds.ItemBank:
    try:
        return ds.load_item_bank()
    except ValueError as exc:  # pragma: no cover - 干净检出里没有 resource/
        pytest.skip(f"本机没有内核道具模板库：{exc}")


def test_real_frame_fixture_stays_small() -> None:
    """真机截图入库必须裁小（AGENTS.md：大产物不进版本库）。"""
    assert FIXTURE.is_file()
    assert FIXTURE.stat().st_size < 200 * 1024


def test_real_frame_scan_stays_fast(real_bank: ds.ItemBank) -> None:
    """整页一次（全库 479 模板 + 拟合）本机实测 ≈ 1.1 s；这里只钉住别退化到几十秒。"""
    frame = np.asarray(Image.open(FIXTURE).convert("RGB"))

    started = time.perf_counter()
    scan = ds.scan_panel(frame, real_bank)
    cost = time.perf_counter() - started

    assert scan.accepted >= 14
    assert cost < 6.0


def test_real_frame_regression(real_bank: ds.ItemBank) -> None:
    """2026-10-13 09:22 的真机帧：24 格全部有结论，15 格接受且与肉眼核对一致。

    这一条同时钉住 M45 的验收点：内核白名单拿不到的 ``4004/4005/7001/7002/7003``
    必须出现在接受列表里。
    """
    frame = np.asarray(Image.open(FIXTURE).convert("RGB"))

    scan = ds.scan_panel(frame, real_bank)

    assert len(scan.cells) == 24
    assert {cell.status for cell in scan.cells} <= {"accepted", "ambiguous", "unknown"}
    assert _accepted(scan) == REAL_FRAME_TRUTH
    assert scan.accepted == 15
    assert {"4004", "4005", "7001", "7002", "7003"} <= set(scan.item_ids)
    # 拟合这条帧上只是确认先验（不该把格线推走），且格心与实测值在一个小窗口内。
    assert scan.grid_confidence in {"prior", "fitted"}
    for name, expected in REAL_FRAME_GRID.items():
        assert abs(getattr(scan.grid, name) - expected) <= 8, name
    # 歧义格全是 margin < 0.15 的近重复（含 ap_supply 家族），不接受也不给答案。
    ambiguous = [cell for cell in scan.cells if cell.status == "ambiguous"]
    assert len(ambiguous) == 6
    assert all(cell.margin < ds.DEFAULT_MARGIN_MIN for cell in ambiguous)
    assert any("ap_supply_lt" in cell.matches[0].item_id for cell in ambiguous)
    # 未知格是画面上有、479 张模板里没有的美术（top1 明显低于下限）。
    unknown = [cell for cell in scan.cells if cell.status == "unknown"]
    assert len(unknown) == 3
    assert all(cell.item_id is None and cell.top1 < ds.DEFAULT_TOP1_MIN for cell in unknown)
