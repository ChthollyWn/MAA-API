"""生成前端的 favicon 与 PWA 图标。

品牌标是石墨底上的一笔靛蓝单线「M」。几何只在这里定义一次：SVG 直接写出路径，
PNG 用 Pillow 以 8 倍超采样绘制后缩小，得到平滑边缘。

用法：``.venv/bin/python scripts/generate_pwa_icons.py``（写入 ``web/public/``）。
``--check`` 只比较生成结果与已提交文件是否一致，不写盘。
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "web" / "public"

BACKGROUND = "#15181D"
MARK = "#8B95F6"
# 64×64 设计网格中的折线与线宽。
MARK_POINTS = [(19.0, 45.0), (19.0, 19.0), (32.0, 35.0), (45.0, 19.0), (45.0, 45.0)]
STROKE = 5.5
SUPERSAMPLE = 8


def svg_icon() -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">\n'
        f'  <rect width="64" height="64" rx="14" fill="{BACKGROUND}"/>\n'
        f'  <path d="M19 45V19l13 16 13-16v26" fill="none" stroke="{MARK}" '
        f'stroke-width="{STROKE}" stroke-linecap="round" stroke-linejoin="round"/>\n'
        "</svg>\n"
    )


def _draw_mark(draw: ImageDraw.ImageDraw, size: int, scale: float) -> None:
    """在 ``size`` 像素的画布中央绘制品牌标，``scale`` 为相对 64 网格的缩放。"""

    unit = size / 64 * scale
    offset = (size - 64 * unit) / 2
    points = [(offset + x * unit, offset + y * unit) for x, y in MARK_POINTS]
    width = STROKE * unit
    draw.line(points, fill=MARK, width=round(width), joint="curve")
    radius = width / 2
    for x, y in (points[0], points[-1]):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=MARK)


def png_icon(size: int, *, rounded: bool, mark_scale: float = 1.0) -> bytes:
    big = size * SUPERSAMPLE
    mode = "RGBA" if rounded else "RGB"
    canvas = Image.new(mode, (big, big), (0, 0, 0, 0) if rounded else BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    if rounded:
        draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=round(big * 14 / 64), fill=BACKGROUND)
    _draw_mark(draw, big, mark_scale)
    image = canvas.resize((size, size), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def outputs() -> dict[str, bytes]:
    return {
        "favicon.svg": svg_icon().encode("utf-8"),
        # iOS 自己加圆角，透明角会被填成黑色，所以用满幅方块。
        "apple-touch-icon.png": png_icon(180, rounded=False),
        "pwa-192x192.png": png_icon(192, rounded=True),
        "pwa-512x512.png": png_icon(512, rounded=True),
        # maskable 由系统裁成任意形状，标志缩进 80% 安全区。
        "pwa-maskable-512x512.png": png_icon(512, rounded=False, mark_scale=0.82),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="只检查已提交的图标是否与生成结果一致")
    args = parser.parse_args()

    drift = []
    for name, content in outputs().items():
        target = PUBLIC / name
        if args.check:
            if not target.is_file() or target.read_bytes() != content:
                drift.append(name)
            continue
        target.write_bytes(content)
        print(f"wrote {target.relative_to(ROOT)}")
    if drift:
        print(f"icons out of date: {', '.join(drift)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
