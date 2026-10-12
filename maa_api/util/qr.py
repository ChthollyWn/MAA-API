"""零依赖二维码编码器（ISO/IEC 18004 byte 模式，版本 1–10）。

本模块只依赖标准库，出自 M42 §4.1：不引入 ``segno`` / ``qrcode``，也不依赖
``Pillow``，只为把 ``hypergryph://scan_login?scanId=…`` 这类短串编码成可直接塞进
API 响应的 ``<svg>``。实现拆成四层：

1. :func:`encode_bitstream`——byte 模式的数据码字（模式指示符、字符计数、终止符、填充）；
2. :func:`reed_solomon_remainder`——GF(256) 上的 RS 纠错（生成多项式按 EC codewords 现算）；
3. :func:`qr_matrix`——功能图形、分块交织、数据布置、格式/版本信息、8 掩码罚分选优；
4. :func:`qr_svg`——把矩阵拼成一条 ``<path>`` 的紧凑 SVG。

``True`` 表示深色模块；矩阵是行优先、不含静区的 ``size × size`` 列表。
"""

from __future__ import annotations

__all__ = [
    "QrEncodeError",
    "encode_bitstream",
    "qr_matrix",
    "qr_svg",
    "reed_solomon_remainder",
]

#: GF(256) 的本原多项式（模 0x11D），用于 RS 纠错（规格 §4.1）。
_GF_PRIMITIVE = 0x11D

#: 格式信息 BCH(15,5) 的掩码常量（ISO/IEC 18004 §8.9）。
_FORMAT_MASK = 0x5412
#: 格式信息 BCH(15,5) 的生成多项式 x^10+x^8+x^5+x^4+x^2+x+1。
_FORMAT_GENERATOR = 0b101_0011_0111
#: 版本信息 BCH(18,6) 的生成多项式 x^12+x^11+x^10+x^9+x^8+x^5+x^2+1（无掩码常量）。
_VERSION_GENERATOR = 0b1_1111_0010_0101
#: 纠错级 → 格式信息 2 bit 载荷（ISO/IEC 18004 表 25：L=01、M=00、Q=11、H=10）。
_ECC_FORMAT_BITS: dict[str, int] = {
    "L": 0b01,
    "M": 0b00,
    "Q": 0b11,
    "H": 0b10,
}

#: 版本 → 对齐图形中心坐标（ISO/IEC 18004 附录 E，只列 V1–V10）。
_ALIGNMENT_CENTERS: dict[int, tuple[int, ...]] = {
    1: (),
    2: (6, 18),
    3: (6, 22),
    4: (6, 26),
    5: (6, 30),
    6: (6, 34),
    7: (6, 22, 38),
    8: (6, 24, 42),
    9: (6, 26, 46),
    10: (6, 28, 50),
}

#: 版本 → 每块 EC codewords 数（ISO/IEC 18004 表 13–22；表按「块多者在前」排列，
#: 每级第 1 块是短块或等长块，第 2 块是长块，仅 V3-Q/V3-H/V4-H 有 1 字节的差）。
_EC_PER_BLOCK: dict[int, dict[str, tuple[int, int]]] = {
    1: {"L": (7, 1), "M": (10, 1), "Q": (13, 1), "H": (17, 1)},
    2: {"L": (10, 1), "M": (16, 1), "Q": (22, 1), "H": (28, 1)},
    3: {"L": (15, 1), "M": (26, 1), "Q": (18, 2), "H": (22, 2)},
    4: {"L": (20, 1), "M": (18, 2), "Q": (26, 2), "H": (16, 4)},
    5: {"L": (26, 1), "M": (24, 2), "Q": (18, 4), "H": (22, 4)},
    6: {"L": (18, 2), "M": (16, 4), "Q": (24, 4), "H": (28, 4)},
    7: {"L": (20, 2), "M": (18, 4), "Q": (18, 6), "H": (26, 5)},
    8: {"L": (24, 2), "M": (22, 4), "Q": (22, 6), "H": (26, 6)},
    9: {"L": (30, 2), "M": (22, 5), "Q": (20, 8), "H": (24, 8)},
    10: {"L": (18, 4), "M": (26, 5), "Q": (24, 8), "H": (28, 8)},
}

_MIN_VERSION = 1
_MAX_VERSION = 10


class QrEncodeError(ValueError):
    """payload 超出本模块支持的上限（版本 10 的容量）时抛出。"""


def qr_matrix(payload: str, *, ecc: str = "M") -> list[list[bool]]:
    """把 ``payload`` 编码成不含静区的模块矩阵（``True`` = 深色）。

    byte 模式 + 版本 1–10 自适应：选能装下的最小版本；四个纠错级都支持。
    ``ecc`` 非法抛 :class:`ValueError`，payload 连版本 10 都装不下抛
    :class:`QrEncodeError`（消息里给出字节数与上限）。8 个掩码按 §8.8.2 罚分选最优，
    平手取编号小的。
    """
    level = _resolve_ecc(ecc)
    data = payload.encode("utf-8")
    version = _choose_version(len(data), level)
    blocks = _data_blocks(data, version, level)
    return _build_matrix(blocks, version, level)


def qr_svg(
    payload: str, *, ecc: str = "M", scale: int = 6, border: int = 4
) -> str:
    """把 ``payload`` 渲染成单条 ``<path>`` 的紧凑 SVG 字符串。

    ``viewBox`` 的边长是 ``模块数 + 2 × border``（模块坐标，1 模块 = 1 用户单位），
    ``scale`` 只影响 ``width`` / ``height`` 两个属性；白色静区用一个 ``<rect>`` 铺底，
    深色模块合成一条 ``<path>``（整数坐标 + ``crispEdges``）。不带 XML 声明与换行。
    """
    if scale < 1:
        raise ValueError("scale 必须是大于 0 的整数")
    if border < 0:
        raise ValueError("border 不能是负数")

    matrix = qr_matrix(payload, ecc=ecc)
    modules = len(matrix)
    extent = modules + 2 * border

    path: list[str] = []
    for row_index, row in enumerate(matrix):
        for column_index, dark in enumerate(row):
            if dark:
                # 每个深色模块画成 1×1 的方框，坐标向内加 border 个模块的静区。
                path.append(
                    f"M{column_index + border} {row_index + border}h1v1h-1z"
                )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {extent} {extent}"'
        f' width="{extent * scale}" height="{extent * scale}"'
        f' shape-rendering="crispEdges">'
        f'<rect width="{extent}" height="{extent}" fill="#fff"/>'
        f'<path d="{"".join(path)}" fill="#000"/>'
        f"</svg>"
    )


def encode_bitstream(data: bytes, version: int, *, ecc: str = "M") -> list[int]:
    """byte 模式的数据码字（含终止符与 0xEC/0x11 交替填充）。

    调用方负责保证 ``data`` 能装进该版本；装不下时抛 :class:`QrEncodeError`。
    """
    level = _resolve_ecc(ecc)
    capacity = _data_codewords(version, level)
    if len(data) + 2 > capacity:
        raise QrEncodeError(
            f"payload {len(data)} 字节装不进版本 {version}-{level}"
            f"（数据码字上限 {capacity}）"
        )

    bits = _BitBuffer()
    # byte 模式：模式指示符 0100 → 字符计数（V1–9 用 8 bit，V10+ 用 16 bit）→ 数据。
    bits.append(0b0100, 4)
    bits.append(len(data), _char_count_bits(version))
    for byte in data:
        bits.append(byte, 8)
    # 终止符最多 4 个 0；补到字节边界；再交替填充 0xEC / 0x11。
    bits.append(0, min(4, capacity * 8 - bits.length))
    bits.append(0, (-bits.length) % 8)
    codewords = bits.to_codewords()
    padding = (0xEC, 0x11)
    index = 0
    while len(codewords) < capacity:
        codewords.append(padding[index % 2])
        index += 1
    return codewords


def reed_solomon_remainder(data: list[int], degree: int) -> list[int]:
    """把 ``data`` 除以生成多项式，返回余数（EC codewords）。

    生成多项式现算：``∏(x - α^i)``，``i`` 从 0 到 ``degree - 1``，系数按升幂排列
    （``generator[0]`` 是常数项 α^0 = 1、``generator[-1]`` 是 x^degree 的系数）。
    返回的余数与 ``degree`` 等长，也按升幂。
    """
    if degree < 0:
        raise ValueError("degree 不能是负数")
    if degree == 0:
        return []

    # 现算生成多项式（规格 §4.1 要求按 EC codewords 数即时推导，不查预置表）。
    generator = [1]
    for power in range(degree):
        generator = _poly_mul(generator, [1, _gf_pow(2, power)])

    # 余数 = data·x^degree mod generator（按升幂对齐，最高次项在末尾）。
    remainder = [0] * degree
    for value in data:
        factor = value ^ remainder[0]
        remainder = remainder[1:] + [0]
        for index in range(degree):
            remainder[index] ^= _gf_mul(generator[index + 1], factor)
    return remainder


def _resolve_ecc(ecc: str) -> str:
    """校验纠错级并返回大写形式。"""
    if not isinstance(ecc, str):
        raise ValueError(f"ecc 必须是字符串，收到 {type(ecc).__name__}")
    level = ecc.upper()
    if level not in _ECC_FORMAT_BITS:
        raise ValueError(f"ecc 必须是 L/M/Q/H 之一，收到 {ecc!r}")
    return level


def _char_count_bits(version: int) -> int:
    """byte 模式的字符计数指示符位宽（ISO/IEC 18004 表 3）。"""
    return 8 if version <= 9 else 16


def _choose_version(byte_length: int, level: str) -> int:
    """选能装下 ``byte_length`` 字节的最小版本；都装不下抛 :class:`QrEncodeError`。

    判定与 :func:`encode_bitstream` 完全一致（同一个 ``len(data) + 2 > 容量`` 口径），
    避免出现「选版本说能装、真正编码时又报装不下」的裂缝。
    """
    for version in range(_MIN_VERSION, _MAX_VERSION + 1):
        if byte_length + 2 <= _data_codewords(version, level):
            return version
    limit = _data_codewords(_MAX_VERSION, level)
    raise QrEncodeError(
        f"payload {byte_length} 字节超过上限：ECC {level} 下版本 "
        f"{_MAX_VERSION} 最多容纳 {limit - 2} 字节"
    )


def _data_codewords(version: int, level: str) -> int:
    """该版本 + 纠错级的**数据**码字总数（总码字 - 全部 EC 码字）。"""
    return _total_codewords(version) - _ec_codewords(version, level)


def _ec_codewords(version: int, level: str) -> int:
    """该版本 + 纠错级的纠错码字总数（每块 EC 数 × 块数）。"""
    per_block, blocks = _EC_PER_BLOCK[version][level]
    return per_block * blocks


def _total_codewords(version: int) -> int:
    """整个符号的码字总数（ISO/IEC 18004 表 1 的公式：数据模块数 // 8）。"""
    modules = (16 * version + 128) * version + 64
    if version >= 2:
        centers = len(_ALIGNMENT_CENTERS[version])
        modules -= (25 * centers - 10) * centers - 55
        if version >= 7:
            modules -= 36
    return modules // 8


def _module_count(version: int) -> int:
    """符号边长（模块数）：``4 × 版本 + 17``。"""
    return version * 4 + 17


def _split_blocks(
    codewords: list[int], per_block: int, block_count: int
) -> list[list[int]]:
    """把数据码字按「块多者在前」切成 group1（短）/group2（长），长度只差 0 或 1。"""
    total = len(codewords)
    short_blocks = block_count - total % block_count if total % block_count else 0
    long_length = -(-total // block_count)

    blocks: list[list[int]] = []
    offset = 0
    for index in range(block_count):
        length = long_length if index >= short_blocks else long_length - 1
        blocks.append(codewords[offset : offset + length])
        offset += length
    return blocks


def _data_blocks(data: bytes, version: int, level: str) -> list[list[int]]:
    """数据码字 → 版本规定的分块（group1/group2），每块末尾接上自己的 EC 码字。"""
    codewords = encode_bitstream(data, version, ecc=level)
    per_block, block_count = _EC_PER_BLOCK[version][level]
    return [
        block + reed_solomon_remainder(block, per_block)
        for block in _split_blocks(codewords, per_block, block_count)
    ]


def _interleave(blocks: list[list[int]], ec_per_block: int) -> list[int]:
    """按 ISO/IEC 18004 §8.6 交织：先数据码字（逐列、跳过短块），再 EC 码字。"""
    result: list[int] = []
    data_length = max(len(block) for block in blocks) - ec_per_block
    for index in range(data_length):
        for block in blocks:
            if index < len(block) - ec_per_block:
                result.append(block[index])
    for index in range(ec_per_block):
        for block in blocks:
            result.append(block[len(block) - ec_per_block + index])
    return result


def _build_matrix(blocks: list[list[int]], version: int, level: str) -> list[list[bool]]:
    """画功能图形 → 铺数据 → 逐个掩码打分，返回罚分最低的那个矩阵。"""
    ec_per_block = _EC_PER_BLOCK[version][level][0]
    functions, reserved = _function_patterns(version)
    symbols = _place_data(_interleave(blocks, ec_per_block), functions, reserved)

    best_mask = 0
    best_penalty: int | None = None
    # 从 0 到 7 迭代、只在严格更优时替换，平手即取编号小的掩码（规格 §4.1）。
    # 打分**不含**格式/版本信息（ISO/IEC 18004 §7.8.3 的注：评估在写入格式信息之前做）。
    for mask in range(8):
        candidate = _apply_mask(symbols, version, mask)
        penalty = _penalty(candidate)
        if best_penalty is None or penalty < best_penalty:
            best_mask, best_penalty = mask, penalty

    matrix = _apply_mask(symbols, version, best_mask)
    _draw_format_info(matrix, level, best_mask)
    if version >= 7:
        _draw_version_info(matrix, version)
    return matrix


def _apply_mask(symbols: list[list[bool]], version: int, mask: int) -> list[list[bool]]:
    """复制一份符号矩阵，只对非保留模块套用掩码。"""
    reserved = _is_reserved_matrix(len(symbols), version)
    return [
        [
            row[column] ^ _mask_condition(mask, row_index, column)
            if not reserved[row_index][column]
            else row[column]
            for column in range(len(row))
        ]
        for row_index, row in enumerate(symbols)
    ]


def _function_patterns(version: int) -> tuple[list[list[bool]], list[list[bool]]]:
    """功能图形与数据区之外模块的「保留」标记（True = 不可写数据）。"""
    size = _module_count(version)
    matrix = [[False] * size for _ in range(size)]
    reserved = [[False] * size for _ in range(size)]
    for row in range(size):
        for column in range(size):
            reserved[row][column] = _is_reserved(row, column, size, version)

    # finder 7×7 + 1 模块宽的 separator（三个角）：切比雪夫距离 0/1 深色，
    # 距离 2 是 separator（浅色），距离 3 是矩阵边界外的静区、不画。
    for row, column in (
        (3, 3),
        (3, size - 4),
        (size - 4, 3),
    ):
        for dy in range(-3, 4):
            for dx in range(-3, 4):
                matrix[row + dy][column + dx] = max(abs(dx), abs(dy)) != 2

    # timing 图形：第 6 行 / 第 6 列，从 8 起、到 size - 9 止，偶数坐标为深色。
    for index in range(8, size - 8):
        matrix[index][6] = index % 2 == 0
        matrix[6][index] = index % 2 == 0

    # alignment 图形 5×5：与 finder 区（含 separator）重叠的中心不画。
    for row, column in _alignment_centers_to_draw(version):
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                matrix[row + dy][column + dx] = max(abs(dx), abs(dy)) != 1

    # 固定深色模块：第 (4 × version + 9, 8) 个模块永远为深色。
    matrix[size - 8][8] = True
    return matrix, reserved


def _in_finder(row: int, column: int, size: int) -> bool:
    """``(row, column)`` 是否落在三个 8×8 的 finder ＋ separator 方块里。"""
    return (
        (row < 8 and column < 8)
        or (row < 8 and column >= size - 8)
        or (row >= size - 8 and column < 8)
    )


def _alignment_centers_to_draw(version: int) -> list[tuple[int, int]]:
    """真正要画的对齐图形中心：去掉与三个 finder 区重叠的那些（ISO/IEC 18004 §6.3.6）。

    重叠判据是「5×5 图案与 8×8 的 finder ＋ separator 方块有交集」，不是「中心离角落
    近」——后者会把 (4, 16) 这类图案外、但切比雪夫距离 ≤ 2 的模块误判成保留。
    """
    size = _module_count(version)
    centers: list[tuple[int, int]] = []
    for row in _ALIGNMENT_CENTERS[version]:
        for column in _ALIGNMENT_CENTERS[version]:
            overlaps = (
                (row - 2 <= 7 and column - 2 <= 7)
                or (row - 2 <= 7 and column + 2 >= size - 8)
                or (row + 2 >= size - 8 and column - 2 <= 7)
            )
            if not overlaps:
                centers.append((row, column))
    return centers


def _is_reserved(row: int, column: int, size: int, version: int) -> bool:
    """判断 ``(row, column)`` 是否属于功能图形或格式/版本信息区（不可写数据）。

    注意格式信息只占第 8 行 / 第 8 列自身，不能把整个第 9 行、第 9 列都划进来，
    否则会白丢 8 个数据模块（版本 1 的可写模块数会从 208 变成 200）。
    """
    if _in_finder(row, column, size):
        return True
    # 格式信息：第 8 行左 9 格 + 右 8 格，第 8 列上 9 格 + 下 8 格。
    if row == 8 and (column <= 8 or column >= size - 8):
        return True
    if column == 8 and (row <= 8 or row >= size - 8):
        return True
    if row == 6 or column == 6:
        return True
    # 版本信息（仅 V7+ 真的有）：右上角 6 行 × 3 列、左下角 3 行 × 6 列，各自紧贴
    # 右上 / 左下 finder 的内侧（列 size-11…size-9）。
    if version >= 7 and (
        (row < 6 and size - 11 <= column < size - 8)
        or (column < 6 and size - 11 <= row < size - 8)
    ):
        return True
    return any(
        abs(row - center_row) <= 2 and abs(column - center_column) <= 2
        for center_row, center_column in _alignment_centers_to_draw(version)
    )


def _place_data(
    codewords: list[int], matrix: list[list[bool]], reserved: list[list[bool]]
) -> list[list[bool]]:
    """把码字按 zigzag 从右下角铺进非保留模块；位序从最高位开始（§8.7）。"""
    size = len(matrix)
    index = 0
    direction = -1  # -1 = 向上、+1 = 向下
    row = size - 1
    for column in range(size - 1, 0, -2):
        if column <= 6:
            # 第 6 列是 timing，数据不写；列对整体左移一列（(6,5) → (5,4)）。
            column -= 1
        while True:
            for pair_column in (column, column - 1):
                if reserved[row][pair_column]:
                    continue
                dark = index < len(codewords) * 8 and (
                    (codewords[index >> 3] >> (7 - (index & 7))) & 1
                ) == 1
                matrix[row][pair_column] = bool(dark)
                index += 1
            row += direction
            if row < 0 or row >= size:
                # 撞到上下边界：退回一格、把方向反过来，换下一列对。
                row -= direction
                direction = -direction
                break
    return matrix


def _mask_condition(mask: int, row: int, column: int) -> bool:
    """8 个掩码条件（ISO/IEC 18004 §8.8.1），返回 True 表示该模块要取反。"""
    if mask == 0:
        return (row + column) % 2 == 0
    if mask == 1:
        return row % 2 == 0
    if mask == 2:
        return column % 3 == 0
    if mask == 3:
        return (row + column) % 3 == 0
    if mask == 4:
        return (row // 2 + column // 3) % 2 == 0
    if mask == 5:
        return row * column % 2 + row * column % 3 == 0
    if mask == 6:
        return (row * column % 2 + row * column % 3) % 2 == 0
    return ((row + column) % 2 + row * column % 3) % 2 == 0


def _draw_format_bits(symbols: list[list[bool]], level: str, mask: int) -> list[list[bool]]:
    """复制一份符号矩阵，套用掩码（只碰非保留模块）并写入格式/版本信息。

    仅供测试与自检使用：正式流程走 ``_build_matrix``（先选掩码再写格式信息）。
    """
    size = len(symbols)
    version = (size - 17) // 4
    matrix = _apply_mask(symbols, version, mask)
    _draw_format_info(matrix, level, mask)
    if version >= 7:
        _draw_version_info(matrix, version)
    return matrix


def _is_reserved_matrix(size: int, version: int) -> list[list[bool]]:
    """缓存一份保留标记矩阵（``_draw_format_bits`` 每个掩码都要用一次）。"""
    cached = _RESERVED_CACHE.get((size, version))
    if cached is None:
        cached = [
            [_is_reserved(row, column, size, version) for column in range(size)]
            for row in range(size)
        ]
        _RESERVED_CACHE[(size, version)] = cached
    return cached


#: ``(size, version)`` → 保留标记矩阵；版本只有 10 个，缓存无上限问题。
_RESERVED_CACHE: dict[tuple[int, int], list[list[bool]]] = {}


def _draw_format_info(matrix: list[list[bool]], level: str, mask: int) -> None:
    """写两处格式信息（BCH(15,5) + 掩码常量 0x5412，§8.9）。

    两处内容相同、方向互为倒序（bit14 都在靠近 timing 的一端）：

    * 左上：bit14→bit7 沿第 8 行从 ``(8,0)`` 走到 ``(8,7)``（跳过第 6 列），
      bit6→bit0 沿第 8 列从 ``(7,8)`` 往上走到 ``(0,8)``；
    * 左下 + 右上：bit0→bit7 沿第 8 行从最右列往左走到 ``(8,size-8)``，
      bit8→bit14 沿第 8 列从 ``(size-1,8)`` 往上走到 ``(size-8,8)``。
    """
    size = len(matrix)
    bits = _format_bits(level, mask)

    def bit(index: int) -> bool:
        return (bits >> index) & 1 == 1

    # 左上角那一份：bit14→bit9 在第 8 行第 0–5 列，bit8 在 (8,7)，bit7 在 (8,8)，
    # bit6 在 (7,8)，bit5→bit0 在第 8 列第 5–0 行（第 6 列/行是 timing，跳过）。
    for column in range(6):
        matrix[8][column] = bit(14 - column)
    matrix[8][7] = bit(8)
    matrix[8][8] = bit(7)
    matrix[7][8] = bit(6)
    for row in range(6):
        matrix[row][8] = bit(row)

    # 另一份：bit0→bit7 在第 8 行右侧（从最右列往左），bit14→bit8 在第 8 列下方
    # （从最底行往上）。
    for index in range(8):
        matrix[8][size - 1 - index] = bit(index)
    for index in range(7):
        matrix[size - 1 - index][8] = bit(14 - index)


def _format_bits(level: str, mask: int) -> int:
    """格式信息 15 bit：5 bit 载荷（2 bit 纠错级 + 3 bit 掩码）经 BCH(15,5) 编码、
    再异或 :data:`_FORMAT_MASK`（§8.9）。"""
    payload = (_ECC_FORMAT_BITS[level] << 3) | mask
    return _bch_codeword(payload, 10, _FORMAT_GENERATOR) ^ _FORMAT_MASK


def _draw_version_info(matrix: list[list[bool]], version: int) -> None:
    """写两处版本信息（BCH(18,6) + 掩码常量 0x1F25，§8.10，仅 V7+）。"""
    size = len(matrix)
    bits = _version_bits(version)
    for index in range(18):
        dark = (bits >> index) & 1 == 1
        row, column = index // 3, index % 3
        matrix[size - 11 + column][row] = dark
        matrix[row][size - 11 + column] = dark


def _version_bits(version: int) -> int:
    """版本信息 18 bit：6 bit 版本号经 BCH(18,6) 编码（**没有再异或掩码常量**，§8.10）。"""
    return _bch_codeword(version, 12, _VERSION_GENERATOR)


def _bch_codeword(payload: int, shift: int, generator: int) -> int:
    """BCH 系统码：``(payload << shift) ^ (payload·x^shift mod generator)``。

    格式信息（5 bit 载荷 + 10 bit 校验）与版本信息（6 + 12）共用本函数；注意是
    **异或**而不是按位或——校验位与被移位的载荷在同一段 bit 上，两者必须相加（模 2）。
    """
    shifted = payload << shift
    return shifted ^ _bch_remainder(shifted, generator)


def _bch_remainder(data: int, generator: int) -> int:
    """按位做多项式除法，返回 ``data`` 模 ``generator`` 的余数（BCH 用）。

    每一步把生成多项式左移到与被除数最高次对齐再异或，直到剩余次数低于生成多项式的
    次数（格式信息 10、版本信息 12）。
    """
    degree = generator.bit_length() - 1
    remainder = data
    while remainder.bit_length() - 1 >= degree:
        remainder ^= generator << (remainder.bit_length() - 1 - degree)
    return remainder


def _penalty(matrix: list[list[bool]]) -> int:
    """§8.8.2 的掩码罚分（N1=3 / N2=3 / N3=40 / N4=10），越小越好。

    三条实现细节都按 ISO/IEC 18004 表 11 逐字对齐：

    * N1：连续 ``n ≥ 5`` 个同色模块计 ``n - 2`` 分（等价于 ``3 + (n - 5)``）；
    * N3：每行/列先找 1011101，命中后从 ``idx + 7`` 继续找（不重复计重叠窗口），
      只要**任一侧**是 4 个浅色模块（或贴到符号边界）就计 40 分；
    * N4：先取深色比例偏离 50% 的整数百分点，再按 5% 一档向下取整。
    """
    size = len(matrix)
    n1 = n2 = n3 = 0
    dark = 0
    column = bytearray(size)
    previous_row: list[bool] | None = None

    for index in range(size):
        row = matrix[index]
        row_module = column_module = -1
        row_run = column_run = 0
        for offset in range(size):
            current = row[offset]
            current_column = matrix[offset][index]
            column[offset] = current_column
            dark += current

            if current == row_module:
                row_run += 1
            else:
                if row_run >= 5:
                    n1 += row_run - 2
                row_run = 1
            if current_column == column_module:
                column_run += 1
            else:
                if column_run >= 5:
                    n1 += column_run - 2
                column_run = 1

            if (
                previous_row is not None
                and offset
                and current == row_module == previous_row[offset] == previous_row[offset - 1]
            ):
                n2 += 3
            row_module, column_module = current, current_column

        previous_row = row
        n3 += _finder_like_patterns(row)
        n3 += _finder_like_patterns(column)
        if row_run >= 5:
            n1 += row_run - 2
        if column_run >= 5:
            n1 += column_run - 2

    # 规则 4：深色比例。偏离 50% 的整数百分点再按 5% 分档，每档 10 分。
    percent = dark / (size * size)
    n4 = 10 * int(abs(percent * 100 - 50) / 5)
    return n1 + n2 + n3 + n4


def _finder_like_patterns(line: list[bool]) -> int:
    """一行/一列里的 N3 罚分：1011101 且一侧有 4 个浅色模块（贴边也算）。"""
    found = 0
    text = "".join("1" if module else "0" for module in line)
    size = len(text)
    position = text.find("1011101")
    while position != -1:
        after = position + 7
        # 贴到符号边界也算「那一侧是 4 个浅色模块」。
        if (
            position in (0, size - 7)
            or "1" not in text[max(position - 4, 0) : position]
            or "1" not in text[after : after + 4]
        ):
            found += 40
            position = text.find("1011101", after)
        else:
            # 两侧都不是浅色：这个窗口不算，但可能还有共用中间三连的重叠窗口。
            position = text.find("1011101", position + 4)
    return found


def _gf_pow(base: int, exponent: int) -> int:
    """GF(256) 上的幂；指数取模 255。"""
    return _GF_EXP[(exponent % 255) * _GF_LOG[base]]


def _gf_mul(left: int, right: int) -> int:
    """GF(256) 上的乘法（模 :data:`_GF_PRIMITIVE`）。"""
    if left == 0 or right == 0:
        return 0
    return _GF_EXP[_GF_LOG[left] + _GF_LOG[right]]


def _poly_mul(left: list[int], right: list[int]) -> list[int]:
    """GF(256) 上的多项式乘法，系数按升幂（``left[0]`` 是常数项）。"""
    product = [0] * (len(left) + len(right) - 1)
    for left_index, left_value in enumerate(left):
        if left_value == 0:
            continue
        for right_index, right_value in enumerate(right):
            if right_value:
                product[left_index + right_index] ^= _gf_mul(left_value, right_value)
    return product


def _gf_tables() -> tuple[list[int], list[int]]:
    """预生成 GF(256) 的对数/反对数表（本原多项式 :data:`_GF_PRIMITIVE`）。"""
    exponent = [0] * 512
    logarithm = [0] * 256
    value = 1
    for power in range(255):
        exponent[power] = value
        logarithm[value] = power
        value <<= 1
        if value & 0x100:
            value ^= _GF_PRIMITIVE
    # 乘法要把两个对数相加（最大 508），后半段复制一份免去取模。
    for power in range(255, 512):
        exponent[power] = exponent[power - 255]
    return exponent, logarithm


_GF_EXP, _GF_LOG = _gf_tables()


class _BitBuffer:
    """按位累积 bit，最后打包成大端字节序列。"""

    __slots__ = ("_bits", "_length")

    def __init__(self) -> None:
        self._bits = 0
        self._length = 0

    @property
    def length(self) -> int:
        """已写入的 bit 数。"""
        return self._length

    def append(self, value: int, width: int) -> None:
        """追加 ``width`` 个 bit（``value`` 的高位在前）。"""
        if width <= 0:
            return
        self._bits = (self._bits << width) | (value & ((1 << width) - 1))
        self._length += width

    def to_codewords(self) -> list[int]:
        """按 8 bit 一组打包；长度不是 8 的倍数时左移补齐（调用方保证已经补过）。"""
        padded = self._bits << ((-self._length) % 8)
        total = (self._length + 7) // 8
        return [(padded >> (8 * (total - 1 - index))) & 0xFF for index in range(total)]
