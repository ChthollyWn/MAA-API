"""零依赖二维码编码器的金标与规格对照测试。

**金标矩阵来自 ``python-qrcode``**（byte 模式、``optimize=0``、``border=0``）：它按
ISO/IEC 18004 §7.4.10 正确实现填充码字，且实测与 ``segno`` 生成的模块矩阵完全一致。
本项目 §4.1 原文写的预言机是 ``segno``，但 ``segno`` 在 byte 模式下有个填充偏差——
``write_padding_bits`` 把 ``8 - length % 8`` 无条件写成 8 位，于是数据位流恰好落在
字节边界上时（例如 ``"hello"``/M 的 4 + 8 + 40 + 4 = 56 bit）会多插一个 ``0x00``
码字，再从下一个字节才开始 ``0xEC``/``0x11`` 交替填充；实测从它的矩阵里解出的数据
码字第 7 个是 ``0x00`` 而非规格要求的 ``0xEC``。因此金标改用同样零依赖、填充正确的
``python-qrcode`` 生成（两个预言机都只在本机临时 venv 里用过，不进 ``pyproject.toml``）。

金标用 4 个向量，前三个是「掩码选择与 ``python-qrcode`` 一致、可逐模块比对」的：

=====================  =====  ======  ======
payload                ECC    版本    掩码
=====================  =====  ======  ======
``hello``              Q      1       1
``hypergryph://…``     M      5       5
185 字节长串            L      8       2
``hello``              M      1       0（金标）
=====================  =====  ======  ======

第 4 个向量（``hello``/M）单列：``python-qrcode`` 的罚分函数与 ISO 表 11 有出入，它选
掩码 0，而本实现按 §8.8.2 选掩码 7（用 ``segno`` 的 ``mask_scores``——一份逐字对照
表 11 的实现——复核过：掩码 7 得 1078 分、掩码 0 得 1094 分，掩码 7 确实更低）。所以
这个向量断言「把掩码换成金标那份（mask 0）后，数据区与格式信息逐模块一致」，掩码本身
的正确性由 :func:`test_chosen_mask_has_lowest_penalty` 用独立罚分复核。

规范对照项（格式信息 BCH、版本信息 BCH、RS 生成多项式、掩码条件）直接按
ISO/IEC 18004 的表 C.1 / 附录 A / 表 11 硬编码，不依赖任何第三方库。
"""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree

import pytest

from maa_api.util import qr as qr_module
from maa_api.util.qr import QrEncodeError, qr_matrix, qr_svg

SCAN_URL = "hypergryph://scan_login?scanId=e2264fb214643b5df50c9a820c4c1b9f"
LONG_PAYLOAD = "M42-W1 long vector: " + "0123456789abcdef" * 10 + "-tail"

# ---------------------------------------------------------------------------
# 金标矩阵（python-qrcode 生成，逐模块比对）
# ---------------------------------------------------------------------------

HELLO_Q_ROWS = (
    '#######...#...#######',
    '#.....#...##..#.....#',
    '#.###.#..####.#.###.#',
    '#.###.#.##....#.###.#',
    '#.###.#...#...#.###.#',
    '#.....#.#..#..#.....#',
    '#######.#.#.#.#######',
    '........##.#.........',
    '.##...#..##.#.##.#...',
    '######...#######.#..#',
    '......##.#...#.##.#.#',
    '######..#..#####.#...',
    '#..#.##..###.#####.#.',
    '........#...#....##.#',
    '#######...##..#..##.#',
    '#.....#..#.#....##.#.',
    '#.###.#...###.#..#..#',
    '#.###.#..#.#.##..##..',
    '#.###.#.#..###.######',
    '#.....#.#.##.#####...',
    '#######..#.####..#..#',
)

SCAN_URL_M_ROWS = (
    '#######..###..#.####.#.####...#######',
    '#.....#.#..####..###.#........#.....#',
    '#.###.#.#...#.###.#####.#.###.#.###.#',
    '#.###.#.#.#.##.#####.#...#..#.#.###.#',
    '#.###.#...#.####...#...#....#.#.###.#',
    '#.....#..#.....#..#..##..#..#.#.....#',
    '#######.#.#.#.#.#.#.#.#.#.#.#.#######',
    '........#.####..#..#.#.#..##.........',
    '#.....#.#.#.#.#...#.######.####..###.',
    '.#.##...##..#.......#.#..#.###..#.##.',
    '..##.##..######..#..#..######.#######',
    '.#.....#..#..####.#...##...#..#....##',
    '##....##.#.##.#...##.....#.##.#....#.',
    '..#....#.####.##.#.#...##.###..#.....',
    '##.##.#.####.###.#..##..#..##..#.#.##',
    '...#.....###.#...##....#...####..###.',
    '#.#..##...#.#.###....#.#.###.##.###.#',
    '..#..#.#####......#.#.#.##.##..#.#.#.',
    '...##.#.##.#.###.#..####...###.##.#.#',
    '###.#..###....#...###.#.#.#...#..#..#',
    '##...##..#####.###.#..#.#####.#####..',
    '..##...###.#.#.##.##.###..##.#.#####.',
    '.#.#..##.#...####.#.#.#..#.##.#.#...#',
    '.....#.###..##......##..#..####..#..#',
    '.######.#...#.#####..#..#.#..##.....#',
    '#.#....#.#..#.#.#....#...###.#...#...',
    '#.#..#####...##..#..#.###.##...#.#.##',
    '#.#.##..###.#..#..#.#.##..###..#####.',
    '#.#.#.#.#####...##..################.',
    '........#..#.#.#.###...#.#..#...#..#.',
    '#######..##.#..##..#####.##.#.#.##.##',
    '#.....#..##.####.##.#.##..#.#...##...',
    '#.###.#..###.####....#..#############',
    '#.###.#..#..#...#.#..#..####..##.###.',
    '#.###.#...#....#####.#..###.#..#.##.#',
    '#.....#..#...#....###.##..###..##...#',
    '#######.#..##.##..#.#..##..####.#...#',
)

LONG_L_ROWS = (
    '#######...#.####.#.....#...#..#....#....#.#######',
    '#.....#.#..####.#..####.###..#..###.#.###.#.....#',
    '#.###.#..##.#..#..##.....#..#..###.....##.#.###.#',
    '#.###.#.##.##..##.####.##..#..#...###..#..#.###.#',
    '#.###.#..#.......#...######.#.###....#....#.###.#',
    '#.....#.###..#.....##.#...##.#.#.##.###...#.....#',
    '#######.#.#.#.#.#.#.#.#.#.#.#.#.#.#.#.#.#.#######',
    '..........#....##.##..#...#.#..##......#.........',
    '#####.###..##...#.##########.##..#.###..##.#.#.#.',
    '.#.###.###..####.#.....#....#####......#..#...##.',
    '#..#.##..#..#####...##..####...#.##.####.#..#...#',
    '####.#...#.#..##.###.....#..#..##......#.####....',
    '#..##.#...#..#############.#.##..#.###..#..#...##',
    '#..##..##.###..............####.#..##..#.##..#...',
    '.####.##.###.#......#...####...#.##.####.....#..#',
    '#....#.##.####.#.###.....#..#.###......#...###...',
    '#..#..#.###.....##########.#.##....##.#.###...#.#',
    '##..#..##.#..###...........####.#..##..#.####....',
    '#.########.#......##....####...#.##.#####..#..###',
    '...#.#.###..##.#.###.....#..#.###........##.##.##',
    '.#..####....#..##########.##.##....##.#####...###',
    '..#.#..#...###.........##....###...##..#.#####.#.',
    '###.#######..#.#..##..######...#.##..########...#',
    '###.#...#.#...#..###.##...#.#.###.......#...##..#',
    '###.#.#.##......#######.#.##.....########.#.#.###',
    '#..##...#.#..###.....##...#..###.#.##...#...##...',
    '#..#######...###..#...######...#..#..########..##',
    '........#..##.##...#.##.###.#.###..#....#..#.#..#',
    '#######.##.####.#####.#..#.#.....##.#####.#######',
    '...##...####.........#.##.##.##..#..#..####..#.#.',
    '#...#.###.#.##....#....##..#...#..#..######.###.#',
    '#...##.#....###.#..#.##.###.#.####.#.#.....#.#.##',
    '#.#.###..##.....######...#.#.#...#..#..#..#.#.##.',
    '#####..###...###.....#.#####.##..#..#....#.#.#...',
    '#.#####.#..#...##.###..##..#...#..#..###.##.##.##',
    '##.###.##...##...#...##..##.#.####.#.#..##.##..#.',
    '.####.####..#..#######...#.#.#...#..#..#..#####.#',
    '###.#..#...#.#.#.....#.###########.#...##....#..#',
    '.#...###..#...###.###..##.........##########..###',
    '.###....#.##.....#...##..##.######.#.#..#..#.#.##',
    '###...#.#......#############..#.....##.######.#..',
    '........#.#####......##...########.#....#...#.#..',
    '#######.#..####..##.#.#.#.#.......#####.#.#.##.##',
    '#.....#...#.#.#...#..##...########.#.#..#...#....',
    '#.###.#.#.#################...#.....##..#######.#',
    '#.###.#.#...#.........##..#..##.##.....####.#..#.',
    '#.###.#.##.##.#..##.##..#.........#####...##.#...',
    '#.....#.#.#.##....#.....#..#######.#.#..######..#',
    '#######.##...#.#######...##..##..##.#.##...##.###',
)

HELLO_M_ROWS = (
    '#######..##...#######',
    '#.....#.##....#.....#',
    '#.###.#..#.##.#.###.#',
    '#.###.#...##..#.###.#',
    '#.###.#.##..#.#.###.#',
    '#.....#.....#.#.....#',
    '#######.#.#.#.#######',
    '..........###........',
    '#.#.#.#..#.#....#..#.',
    '..#.##....#...#....##',
    '.#.#..#.###.#...#####',
    '##..#.........#....#.',
    '.##.#.##..#.#.#.#....',
    '........####.#.#..###',
    '#######...##.###..###',
    '#.....#...####.##....',
    '#.###.#.#.##.###...##',
    '#.###.#..#....##..##.',
    '#.###.#.###.#...#.#.#',
    '#.....#..#....#.#..#.',
    '#######.###.#.##...##',
)


def _to_matrix(rows: tuple[str, ...]) -> list[list[bool]]:
    """``'#...'`` 行常量 → 布尔矩阵（``#`` = 深色）。"""
    return [[cell == "#" for cell in row] for row in rows]


#: 三个「掩码也与金标一致」的向量：(payload, ecc, 版本, 掩码, 行常量)。
GOLDEN_VECTORS = (
    ("hello", "Q", 1, 1, HELLO_Q_ROWS),
    (SCAN_URL, "M", 5, 5, SCAN_URL_M_ROWS),
    (LONG_PAYLOAD, "L", 8, 2, LONG_L_ROWS),
)


def _mask_from_matrix(matrix: list[list[bool]]) -> int:
    """从矩阵左上角那份格式信息读回掩码编号。"""
    bits = 0
    for column in range(6):
        bits |= int(matrix[8][column]) << (14 - column)
    bits |= int(matrix[8][7]) << 8
    bits |= int(matrix[8][8]) << 7
    bits |= int(matrix[7][8]) << 6
    for row in range(6):
        bits |= int(matrix[row][8]) << row
    table = {
        qr_module._format_bits(level, mask): mask
        for level in "LMQH"
        for mask in range(8)
    }
    return table[bits]


@pytest.mark.parametrize(
    ("payload", "ecc", "version", "mask", "rows"),
    GOLDEN_VECTORS,
    ids=("hello", "scan-url", "long-185B"),
)
def test_golden_matrix_module_by_module(
    payload: str, ecc: str, version: int, mask: int, rows: tuple[str, ...]
) -> None:
    """金标：逐模块比对三段 payload 的完整矩阵（版本 8 覆盖版本信息 BCH）。"""
    expected = _to_matrix(rows)
    actual = qr_matrix(payload, ecc=ecc)

    assert len(actual) == len(expected) == 4 * version + 17
    assert actual == expected
    assert _mask_from_matrix(actual) == mask


@pytest.mark.parametrize(
    ("payload", "ecc", "version", "mask", "rows"),
    GOLDEN_VECTORS,
    ids=("hello", "scan-url", "long-185B"),
)
def test_golden_vectors_choose_expected_version(
    payload: str, ecc: str, version: int, mask: int, rows: tuple[str, ...]
) -> None:
    """金标向量的版本号与字节数对得上（byte 模式 + 最小版本自适应）。"""
    assert qr_module._choose_version(len(payload.encode("utf-8")), ecc) == version
    assert qr_module._module_count(version) == len(rows)


@pytest.mark.parametrize(
    ("payload", "ecc", "version", "mask", "rows"),
    GOLDEN_VECTORS,
    ids=("hello", "scan-url", "long-185B"),
)
def test_golden_matrix_data_region_only(
    payload: str, ecc: str, version: int, mask: int, rows: tuple[str, ...]
) -> None:
    """金标的数据区单独比对：金标失配时用来区分「数据排布错」与「格式信息错」。"""
    expected = _to_matrix(rows)
    actual = qr_matrix(payload, ecc=ecc)
    reserved = qr_module._is_reserved_matrix(len(expected), version)

    mismatched = [
        (row, column)
        for row in range(len(expected))
        for column in range(len(expected))
        if not reserved[row][column] and actual[row][column] != expected[row][column]
    ]
    assert mismatched == []


def test_golden_hello_m_with_oracle_mask() -> None:
    """``hello``/M：把掩码换成金标那份（mask 0）后，整块矩阵与金标逐模块一致。

    本实现按 §8.8.2 给 ``hello``/M 选的是掩码 7（罚分 1078 < 掩码 0 的 1094，
    用独立罚分复核），``python-qrcode`` 的罚分口径不同、选了 0。这里只钉住「除掩码
    选择外的全部编码结果」——数据、RS、分块交织、功能图形、格式信息——都与金标一致。
    """
    expected = _to_matrix(HELLO_M_ROWS)
    version = 1
    blocks = qr_module._data_blocks(b"hello", version, "M")
    symbols = qr_module._place_data(
        qr_module._interleave(blocks, qr_module._EC_PER_BLOCK[version]["M"][0]),
        *qr_module._function_patterns(version),
    )
    assert qr_module._draw_format_bits(symbols, "M", 0) == expected
    assert _mask_from_matrix(expected) == 0


def test_writable_module_count_matches_codeword_capacity() -> None:
    """每个版本的可写模块数 = 码字总数 × 8 + 剩余位（V2–V6 有 7 位、V7–V10 没有）。"""
    remainder = {1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7, 7: 0, 8: 0, 9: 0, 10: 0}
    for version in range(1, 11):
        size = qr_module._module_count(version)
        writable = sum(
            1
            for row in range(size)
            for column in range(size)
            if not qr_module._is_reserved(row, column, size, version)
        )
        assert writable == qr_module._total_codewords(version) * 8 + remainder[version]


# ---------------------------------------------------------------------------
# 格式信息 BCH(15,5)：32 条对照 ISO/IEC 18004 表 C.1
# ---------------------------------------------------------------------------

#: ISO/IEC 18004 表 C.1，按 L / M / Q / H 分组、每组 8 个掩码，MSB 在左。
FORMAT_INFO_TABLE: dict[str, tuple[str, ...]] = {
    "L": (
        "111011111000100",
        "111001011110011",
        "111110110101010",
        "111100010011101",
        "110011000101111",
        "110001100011000",
        "110110001000001",
        "110100101110110",
    ),
    "M": (
        "101010000010010",
        "101000100100101",
        "101111001111100",
        "101101101001011",
        "100010111111001",
        "100000011001110",
        "100111110010111",
        "100101010100000",
    ),
    "Q": (
        "011010101011111",
        "011000001101000",
        "011111100110001",
        "011101000000110",
        "010010010110100",
        "010000110000011",
        "010111011011010",
        "010101111101101",
    ),
    "H": (
        "001011010001001",
        "001001110111110",
        "001110011100111",
        "001100111010000",
        "000011101100010",
        "000001001010101",
        "000110100001100",
        "000100000111011",
    ),
}


@pytest.mark.parametrize("level", ("L", "M", "Q", "H"))
def test_format_info_matches_iso_table(level: str) -> None:
    """格式信息 15 bit 串逐条对照 ISO 表（4 × 8 = 32 条全覆盖）。"""
    for mask, expected in enumerate(FORMAT_INFO_TABLE[level]):
        actual = qr_module._format_bits(level, mask)
        assert format(actual, "015b") == expected, f"{level}/mask{mask}"


def test_format_info_mask_constant_is_applied() -> None:
    """M/掩码 0 的格式信息等于掩码常量本身（BCH 余数为 0 时的特例）。"""
    assert qr_module._format_bits("M", 0) == 0x5412


def test_format_info_written_twice_with_same_value() -> None:
    """两份格式信息内容相同：左上角那份与左下/右上那份读回同一个 15 bit 值。"""
    matrix = qr_matrix("hello", ecc="Q")
    size = len(matrix)

    top_left = 0
    for column in range(6):
        top_left |= int(matrix[8][column]) << (14 - column)
    top_left |= int(matrix[8][7]) << 8
    top_left |= int(matrix[8][8]) << 7
    top_left |= int(matrix[7][8]) << 6
    for row in range(6):
        top_left |= int(matrix[row][8]) << row

    other = 0
    for index in range(8):
        other |= int(matrix[8][size - 1 - index]) << index
    for index in range(7):
        other |= int(matrix[size - 1 - index][8]) << (14 - index)

    assert top_left == other == qr_module._format_bits("Q", _mask_from_matrix(matrix))


# ---------------------------------------------------------------------------
# 版本信息 BCH(18,6)：V7–V10 对照 ISO 表
# ---------------------------------------------------------------------------

VERSION_INFO_TABLE: dict[int, str] = {
    7: "000111110010010100",
    8: "001000010110111100",
    9: "001001101010011001",
    10: "001010010011010011",
}


@pytest.mark.parametrize(("version", "expected"), sorted(VERSION_INFO_TABLE.items()))
def test_version_info_matches_iso_table(version: int, expected: str) -> None:
    """版本信息 18 bit 串对照 ISO 表（版本信息没有额外的异或掩码）。"""
    assert format(qr_module._version_bits(version), "018b") == expected


@pytest.mark.parametrize("version", (7, 8, 9, 10))
def test_version_info_placed_in_both_corners(version: int) -> None:
    """V7+ 的版本信息写两处（右上 3×6、左下 6×3），且两份内容一致。"""
    size = qr_module._module_count(version)
    matrix = qr_matrix("z" * (qr_module._data_codewords(version, "H") - 3), ecc="H")
    assert len(matrix) == size

    bits = qr_module._version_bits(version)
    for index in range(18):
        expect = (bits >> index) & 1 == 1
        row, column = index // 3, index % 3
        assert matrix[row][size - 11 + column] == expect
        assert matrix[size - 11 + column][row] == expect


def test_short_versions_have_no_version_info_area() -> None:
    """V1–V6 没有版本信息区：右上 (6, size-11) 一带是可写数据，不是固定图案。"""
    size = qr_module._module_count(6)
    assert not qr_module._is_reserved(0, size - 11, size, 6)
    assert qr_module._is_reserved(0, size - 11, size, 7)


# ---------------------------------------------------------------------------
# RS 纠错：生成多项式与 ISO 附录 A 的系数表一致
# ---------------------------------------------------------------------------

GENERATOR_DEGREES = (7, 10, 13, 18, 26)


def _generator(degree: int) -> list[int]:
    """按升幂返回 degree 次生成多项式的系数（规格 §4.1 要求现算）。"""
    generator = [1]
    for power in range(degree):
        generator = qr_module._poly_mul(generator, [1, qr_module._gf_pow(2, power)])
    return generator


@pytest.mark.parametrize("degree", GENERATOR_DEGREES)
def test_generator_polynomial_degree_and_constant_term(degree: int) -> None:
    """生成多项式的次数等于 EC codewords 数，常数项恒为 α^0 = 1。"""
    generator = _generator(degree)
    assert len(generator) == degree + 1
    assert generator[0] == 1


@pytest.mark.parametrize(
    ("degree", "leading"),
    (
        (7, [1, 127, 122, 154, 164, 11, 68, 117]),
        (10, [1, 216, 194, 159, 111, 199, 94, 95, 113, 157, 193]),
        (13, [1, 137, 73, 227, 17, 177, 17, 52, 13, 46, 43, 83, 132, 120]),
    ),
)
def test_generator_polynomial_coefficients(degree: int, leading: list[int]) -> None:
    """degree 7/10/13 的生成多项式系数逐项对照 ISO 附录 A（按升幂，``[0]`` 是常数项）。"""
    assert _generator(degree) == leading


@pytest.mark.parametrize("degree", GENERATOR_DEGREES)
def test_reed_solomon_remainder_shape_and_stability(degree: int) -> None:
    """RS 余数长度等于 degree、取值 0–255，且同样输入稳定复现。"""
    data = [64, 86, 134, 86, 198, 198, 240, 236, 17, 236, 17, 236, 17, 236, 17, 236, 17, 236, 17]
    first = qr_module.reed_solomon_remainder(data, degree)
    assert qr_module.reed_solomon_remainder(data, degree) == first
    assert len(first) == degree
    assert all(0 <= value <= 255 for value in first)


def test_reed_solomon_zero_degree_returns_empty() -> None:
    """degree = 0 的退化输入返回空列表，负数抛 ValueError。"""
    assert qr_module.reed_solomon_remainder([1, 2, 3], 0) == []
    with pytest.raises(ValueError):
        qr_module.reed_solomon_remainder([1, 2, 3], -1)


# ---------------------------------------------------------------------------
# 掩码选择：稳定复现 + 罚分最低
# ---------------------------------------------------------------------------


def test_mask_selection_is_stable() -> None:
    """同一 payload 多次编码得到完全相同的矩阵（掩码选择是确定性的）。"""
    for payload, ecc in (("hello", "M"), (SCAN_URL, "M"), (LONG_PAYLOAD, "L")):
        first = qr_matrix(payload, ecc=ecc)
        assert qr_matrix(payload, ecc=ecc) == first
        assert qr_matrix(payload, ecc=ecc.lower()) == first  # 纠错级大小写不敏感


@pytest.mark.parametrize(
    ("payload", "ecc"),
    (
        ("hello", "M"),
        (SCAN_URL, "M"),
        (LONG_PAYLOAD, "L"),
        ("x" * 60, "L"),
        ("y" * 100, "Q"),
        ("z" * 118, "H"),
    ),
)
def test_chosen_mask_has_lowest_penalty(payload: str, ecc: str) -> None:
    """选中的掩码确实是 8 个候选里罚分最低的，且平手时取编号小的（规格 §4.1）。"""
    version = qr_module._choose_version(len(payload.encode("utf-8")), ecc)
    blocks = qr_module._data_blocks(payload.encode("utf-8"), version, ecc)
    symbols = qr_module._place_data(
        qr_module._interleave(blocks, qr_module._EC_PER_BLOCK[version][ecc][0]),
        *qr_module._function_patterns(version),
    )

    penalties = [
        qr_module._penalty(qr_module._apply_mask(symbols, version, mask))
        for mask in range(8)
    ]
    chosen = _mask_from_matrix(qr_matrix(payload, ecc=ecc))

    assert penalties[chosen] == min(penalties)
    # 平手取编号小的：比它小的候选必须严格更差。
    assert all(penalties[mask] > penalties[chosen] for mask in range(chosen))


def _rule1(matrix: list[list[bool]]) -> int:
    """独立复算 N1（连续 ≥5 个同色模块记 n - 2 分）。"""
    size = len(matrix)
    total = 0
    for index in range(size):
        for line in (matrix[index], [matrix[row][index] for row in range(size)]):
            run, previous = 1, line[0]
            for module in line[1:]:
                if module == previous:
                    run += 1
                else:
                    if run >= 5:
                        total += run - 2
                    run, previous = 1, module
            if run >= 5:
                total += run - 2
    return total


def _rule2(matrix: list[list[bool]]) -> int:
    """独立复算 N2（每个 2×2 同色块 3 分）。"""
    size = len(matrix)
    return sum(
        3
        for row in range(size - 1)
        for column in range(size - 1)
        if matrix[row][column]
        == matrix[row][column + 1]
        == matrix[row + 1][column]
        == matrix[row + 1][column + 1]
    )


def test_penalty_counts_runs_blocks_and_patterns() -> None:
    """罚分的四条规则各自能被一个最小例子触发。"""
    # N1：6×6 全深色 → 6 行 + 6 列各记 4 分 = 48。
    assert qr_module._penalty([[True] * 6 for _ in range(6)]) >= 48

    # N2：2×2 全深色 → 3 分（N1 在该规模下不触发，N4 = 10）。
    assert qr_module._penalty([[True] * 2 for _ in range(2)]) == 3 + 100

    # N3：1011101 且一侧有 4 个浅色模块 → 40 分；贴到符号边界也算。
    assert qr_module._finder_like_patterns(
        [True, False, True, True, True, False, True, False, False, False, False]
    ) == 40
    assert qr_module._finder_like_patterns([True, False, True, True, True, False, True]) == 40
    # 两侧都不是 4 个浅色模块（紧邻处有深色）→ 不计分。
    assert qr_module._finder_like_patterns(
        [True, True, False, True, True, True, False, True, True, True, True, True]
    ) == 0

    # N4：棋盘格深色占比正好 50% → 0 分；全深色 → 10 档 × 10 分。
    checkerboard = [[row % 2 == column % 2 for column in range(10)] for row in range(10)]
    assert qr_module._penalty(checkerboard) == 0
    assert qr_module._penalty([[True] * 10 for _ in range(10)]) >= 100


def test_penalty_total_is_sum_of_the_four_rules() -> None:
    """``_penalty`` 的总分等于「独立复算的 N1 + N2 + N3 + N4」，避免某项漏算或重算。"""
    for payload, ecc in (("hello", "M"), (SCAN_URL, "M"), (LONG_PAYLOAD, "L")):
        matrix = qr_matrix(payload, ecc=ecc)
        size = len(matrix)
        rule3 = sum(
            qr_module._finder_like_patterns(matrix[index])
            + qr_module._finder_like_patterns([matrix[row][index] for row in range(size)])
            for index in range(size)
        )
        dark = sum(1 for row in matrix for module in row if module)
        rule4 = 10 * int(abs(dark / (size * size) * 100 - 50) / 5)
        assert qr_module._penalty(matrix) == _rule1(matrix) + _rule2(matrix) + rule3 + rule4


def test_mask_conditions_cover_all_eight_patterns() -> None:
    """8 个掩码条件互不相同，且第 0 个就是棋盘格（ISO/IEC 18004 §8.8.1）。"""
    samples = [(row, column) for row in range(8) for column in range(8)]
    signatures = {
        mask: tuple(qr_module._mask_condition(mask, row, column) for row, column in samples)
        for mask in range(8)
    }
    assert len(set(signatures.values())) == 8
    assert signatures[0] == tuple((row + column) % 2 == 0 for row, column in samples)


# ---------------------------------------------------------------------------
# SVG 输出
# ---------------------------------------------------------------------------


def _parse_svg(svg: str) -> ElementTree.Element:
    return ElementTree.fromstring(svg)


def _path_cells(d: str) -> set[tuple[int, int]]:
    """把 ``M{x} {y}h1v1h-1z`` 串解析成模块坐标集合 (列, 行)。"""
    cells: set[tuple[int, int]] = set()
    for command in d.split("M")[1:]:
        head = command.split("h", 1)[0].strip()
        column, row = head.split(" ")
        cells.add((int(column), int(row)))
    return cells


@pytest.mark.parametrize("border", (0, 4))
def test_svg_viewbox_matches_module_count(border: int) -> None:
    """``viewBox`` = 模块数 + 2×border，``scale`` 只影响 width/height。"""
    matrix = qr_matrix("hello", ecc="M")
    svg = qr_svg("hello", ecc="M", scale=7, border=border)
    root = _parse_svg(svg)
    extent = len(matrix) + 2 * border

    assert root.tag.endswith("svg")
    assert root.get("viewBox") == f"0 0 {extent} {extent}"
    assert root.get("width") == str(extent * 7)
    assert root.get("height") == str(extent * 7)
    assert root.get("shape-rendering") == "crispEdges"


@pytest.mark.parametrize("border", (0, 4))
def test_svg_dark_modules_match_matrix(border: int) -> None:
    """``<path>`` 里的每个方框与 ``qr_matrix`` 的深色模块一一对应（含静区偏移）。"""
    matrix = qr_matrix(SCAN_URL, ecc="M")
    root = _parse_svg(qr_svg(SCAN_URL, ecc="M", border=border))
    path = root.find("{http://www.w3.org/2000/svg}path")
    assert path is not None
    cells = _path_cells(path.get("d", ""))

    expected = {
        (column + border, row + border)
        for row, line in enumerate(matrix)
        for column, dark in enumerate(line)
        if dark
    }
    assert cells == expected
    assert len(cells) == sum(1 for line in matrix for dark in line if dark)


def test_svg_has_white_background_and_no_xml_declaration() -> None:
    """白底矩形铺满 viewBox，不带 XML 声明、不带换行。"""
    svg = qr_svg("hello", ecc="M")
    assert not svg.startswith("<?xml")
    assert "\n" not in svg
    rect = _parse_svg(svg).find("{http://www.w3.org/2000/svg}rect")
    assert rect is not None and rect.get("fill") == "#fff"


def test_svg_scale_only_affects_width_and_height() -> None:
    """``scale`` 改了 width/height，但 viewBox 与 ``d`` 完全不变。"""
    small = _parse_svg(qr_svg("hello", ecc="M", scale=1))
    large = _parse_svg(qr_svg("hello", ecc="M", scale=10))

    assert small.get("viewBox") == large.get("viewBox")
    assert small.find("{http://www.w3.org/2000/svg}path").get("d") == (
        large.find("{http://www.w3.org/2000/svg}path").get("d")
    )
    assert int(large.get("width")) == 10 * int(small.get("width"))


def test_svg_rejects_invalid_scale_and_border() -> None:
    """``scale < 1`` 与 ``border < 0`` 抛 ValueError。"""
    with pytest.raises(ValueError):
        qr_svg("hello", scale=0)
    with pytest.raises(ValueError):
        qr_svg("hello", border=-1)


# ---------------------------------------------------------------------------
# 边界：空串、超长、非法纠错级
# ---------------------------------------------------------------------------


def test_empty_payload_encodes_as_version_1() -> None:
    """空串是合法的 byte 模式输入，落到版本 1；SVG 的静区随默认 border=4。"""
    matrix = qr_matrix("")
    assert len(matrix) == 21
    assert _parse_svg(qr_svg("")).get("viewBox") == "0 0 29 29"


@pytest.mark.parametrize("ecc", ("L", "M", "Q", "H"))
def test_payload_at_version_10_capacity_is_accepted(ecc: str) -> None:
    """恰好装进版本 10 的 payload 不报错，再多 1 字节抛 QrEncodeError。"""
    # 选版本与真正编码共用同一个口径：可用字节数 = 数据码字数 - 2。
    capacity = qr_module._data_codewords(10, ecc) - 2
    assert qr_module._choose_version(capacity, ecc) == 10
    assert len(qr_matrix("a" * capacity, ecc=ecc)) == 57

    with pytest.raises(QrEncodeError) as error:
        qr_matrix("a" * (capacity + 1), ecc=ecc)
    message = str(error.value)
    assert str(capacity + 1) in message and str(capacity) in message


def test_error_message_reports_byte_count_and_limit() -> None:
    """超长报错写清实际字节数与上限（规格 §4.1）。"""
    with pytest.raises(QrEncodeError) as error:
        qr_matrix("a" * 400, ecc="H")
    message = str(error.value)
    assert "400" in message
    # H 级版本 10 最多容纳 120 字节（122 个数据码字 - 2 字节固定开销）。
    assert "120" in message


def test_qr_encode_error_is_value_error() -> None:
    """QrEncodeError 继承 ValueError，调用方只 catch ValueError 也接得住。"""
    assert issubclass(QrEncodeError, ValueError)
    with pytest.raises(ValueError):
        qr_matrix("a" * 400, ecc="H")


@pytest.mark.parametrize("ecc", ("", "X", "lm", "1", "LOW"))
def test_invalid_ecc_raises_value_error(ecc: str) -> None:
    """非法纠错级抛 ValueError。"""
    with pytest.raises(ValueError):
        qr_matrix("hello", ecc=ecc)


def test_matrix_size_grows_with_payload() -> None:
    """版本自适应：矩阵边长随字节数单调不减。"""
    sizes = [len(qr_matrix("a" * count, ecc="L")) for count in (0, 1, 17, 100, 200)]
    assert sizes == sorted(sizes)
    assert sizes[0] == 21


def test_utf8_payload_counts_bytes_not_characters() -> None:
    """字符计数写的是 UTF-8 字节数（中文按 3 字节算）。"""
    # 前 12 bit = 模式指示符 0100 + 8 bit 字符计数，计数域应等于 3（不是 1 个字符）。
    first, second = qr_module.encode_bitstream("中".encode("utf-8"), 1, ecc="L")[:2]
    assert first >> 4 == 0b0100  # 模式指示符：byte 模式
    assert ((first & 0x0F) << 4) | (second >> 4) == 3  # 字符计数 = 3 字节
    # 9 个汉字 = 27 字节，与 27 个 ASCII 字符同版本。
    assert len(qr_matrix("零依赖二维码编码器")) == len(qr_matrix("a" * 27))
