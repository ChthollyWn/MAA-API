"""从品牌徽章生成 favicon 与 PWA 图标。

品牌标是 ``web/public/logo.webp``（512×512、圆外透明的徽章，画稿源存档在
``docs/assets/logo-badge-1254.webp``）。本脚本把它合成到品牌底色上，再按各平台需要导出：

- ``pwa-192x192.png`` / ``pwa-512x512.png``：圆角，供 manifest 的普通图标使用。
- ``pwa-maskable-512x512.png``：满幅方块，四周多留安全区，供 Android 裁切。
- ``apple-touch-icon.png``：满幅方块（iOS 自己加圆角；透明角会被它填成黑色，
  所以底色必须画满）。
- ``favicon.png``：浏览器标签页的小图标。

用法：``.venv/bin/python scripts/generate_pwa_icons.py``（写入 ``web/public/``）。
``--check`` 只比较生成结果与已提交文件是否一致，不写盘。
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "web" / "public"
LOGO = PUBLIC / "logo.webp"

#: 品牌底色：徽章白边的冷白（取色 #F4F7FC 一档）。
#: 只出现在圆角外的四角与 maskable 的安全区里；浅底能把深蓝外环衬托出来。
BACKGROUND = (244, 247, 252)
#: 圆角半径，按 512 画布计（与旧的 64 网格 rx=14 同比例）。
CORNER_RATIO = 14 / 64
#: maskable 的安全区缩放：内容缩到 90%，四周留出被系统裁掉的余量。
#: 徽章本身是圆形，0.9 能保证外环完整落在 Android 的圆形安全区内。
MASKABLE_SCALE = 0.9


def _load_logo() -> Image.Image:
    if not LOGO.is_file():
        raise SystemExit(f"缺少品牌插画：{LOGO}")
    return Image.open(LOGO).convert("RGBA")


def _rounded_mask(size: int) -> Image.Image:
    """圆角矩形遮罩；用 4 倍超采样画圆角，避免锯齿边。"""
    from PIL import ImageDraw

    scale = 4
    big = size * scale
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, big - 1, big - 1), radius=round(big * CORNER_RATIO), fill=255
    )
    return mask.resize((size, size), Image.Resampling.LANCZOS)


def _flatten(size: int, *, corner: str) -> Image.Image:
    """把插画合成到 ``size`` 见方的品牌底上；``corner`` 为 ``round`` / ``square``。"""
    logo = _load_logo()
    if logo.size != (size, size):
        logo = logo.resize((size, size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (*BACKGROUND, 255))
    canvas.alpha_composite(logo)
    if corner == "round":
        flattened = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        flattened.paste(canvas, (0, 0), _rounded_mask(size))
        return flattened
    return canvas


def _encode(image: Image.Image) -> bytes:
    """量化为 256 色调色板后再存 PNG。

    插画类图像直接存真彩 PNG 会让 512 图标涨到 200KB 以上，而它会被 Service
    Worker 预缓存、被设备存成桌面图标。256 色的平均色差实测约 2.7/255，肉眼不可辨，
    体积降到约 1/5。底色是不透明的，量化不会碰到透明度问题。
    """
    quantized = image.convert("RGBA").quantize(
        colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE
    )
    buffer = io.BytesIO()
    quantized.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def png_icon(size: int, *, rounded: bool) -> bytes:
    return _encode(_flatten(size, corner="round" if rounded else "square"))


def maskable_icon(size: int) -> bytes:
    """内容缩到安全区内居中，底色铺满，剩余部分交给系统裁切。"""
    inner = round(size * MASKABLE_SCALE)
    logo = _load_logo().resize((inner, inner), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (*BACKGROUND, 255))
    offset = (size - inner) // 2
    canvas.alpha_composite(logo, (offset, offset))
    return _encode(canvas)


def outputs() -> dict[str, bytes]:
    return {
        "favicon.png": png_icon(64, rounded=False),
        # iOS 自己加圆角，这里用满幅方块。
        "apple-touch-icon.png": png_icon(180, rounded=False),
        "pwa-192x192.png": png_icon(192, rounded=True),
        "pwa-512x512.png": png_icon(512, rounded=True),
        "pwa-maskable-512x512.png": maskable_icon(512),
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
