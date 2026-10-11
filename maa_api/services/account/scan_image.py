"""M36：详情页截图的像素读数（专精等级，规格 §4.4）。

内核那条路读不出专精（多模板节点只认数组第一项、单个亮点模板又没有区分度，
实测见 :mod:`maa_api.services.account.scan_tasks` 里那一段），所以这一块自己看像素：

* 详情页每个技能徽章里有三个圆点，**亮 ≈ 255、暗 ≈ 71**（同一块深色底），
  三个点全亮就是专精三——内核自己的四个徽章模板也印证了这个约定
  （``OperFilesSkillMaster1`` 只亮上点、``…Master2`` 亮上点与右下点、
  ``…Master3`` 三个全亮）；
* 取样框是内核坐标里的 7x7 小框（真机标定，见 ``scan_tasks.MASTER_PIP_ROIS``），
  框里取**最亮的那 25%** 像素求均值再比阈值——实测这样亮/暗分得干干净净
  （风笛 技能一 71/71/71 → 0 级；技能二、三 255/255/255 → 3 级；
  凛御银灰 九个点全 255 → 3/3/3）。

截图就是内核 ``AsstGetImage`` 的那一张（与同一次读数里的 OCR 节点同一帧），
按宽度换算到截图坐标，所以 720p / 1080p / 1440p 都能用。
"""

from __future__ import annotations

from PIL import Image, ImageChops, ImageStat

from maa_api.services.account import scan_tasks as st

#: 专精等级的取值范围（0 = 没专精，3 = 专精三）。
MIN_MASTERY_LEVEL = 0
MAX_MASTERY_LEVEL = 3
#: 技能槽号（1/2/3）。
SKILL_SLOTS: tuple[int, ...] = (1, 2, 3)

#: M39：「画面动了没有」的判据——两帧平均灰度差 ≥ 这个值就算换了画面。
#: 真机实测（规格 §2.4）：划不动 0.02–0.05，详情页换人 24.20–48.57，列表翻页 41.92–67.53，
#: 取 2.0 与噪声、真实变化各留约两个数量级余量。
FRAME_CHANGED_MIN_DELTA = 2.0


def mastery_levels_from_image(image: Image.Image) -> dict[int, int]:
    """从详情页整屏截图里数出三个技能槽的专精等级。

    :param image: ``AsstGetImage`` 拿到的那张截图（PNG/JPEG 打开后的对象）。
    :returns: ``{技能槽: 等级}``，槽号 1/2/3，等级 0-3。
    """
    gray = image.convert("L")
    scale = gray.width / st.KERNEL_WIDTH
    levels: dict[int, int] = {}
    for slot in SKILL_SLOTS:
        bright = 0
        for position in st.MASTER_PIP_POSITIONS:
            box = scale_box(st.MASTER_PIP_ROIS[(slot, position)], scale)
            if is_bright_pip(gray.crop(box)):
                bright += 1
        levels[slot] = min(bright, MAX_MASTERY_LEVEL)
    return levels


def scale_box(
    box: tuple[int, int, int, int], scale: float
) -> tuple[int, int, int, int]:
    """内核坐标的框 → 截图坐标的框（``scale = 截图宽 / 1280``）。

    内核里的框是 ``(x, y, 宽, 高)``，返回的是 **PIL ``crop`` 要的**
    ``(左, 上, 右, 下)``——两边的第四个数含义不同，别混用。
    """
    x, y, width, height = box
    left = int(round(x * scale))
    top = int(round(y * scale))
    right = max(int(round((x + width) * scale)), left + 1)
    bottom = max(int(round((y + height) * scale)), top + 1)
    return left, top, right, bottom


def is_bright_pip(pixels: Image.Image) -> bool:
    """一个圆点取样框里「亮没亮」：取最亮的那部分像素求均值比阈值。"""
    values = sorted(_values(pixels), reverse=True)
    if not values:
        return False
    take = max(1, int(len(values) * st.PIP_SAMPLE_RATIO))
    return sum(values[:take]) / take >= st.PIP_BRIGHT_THRESHOLD


def frame_delta(before: Image.Image | None, after: Image.Image | None) -> float | None:
    """两帧的平均灰度差（0–255）。缺一帧或尺寸不一致就没法比，返回 ``None``。"""
    if before is None or after is None or before.size != after.size:
        return None
    difference = ImageChops.difference(before.convert("L"), after.convert("L"))
    return float(ImageStat.Stat(difference).mean[0])


def frame_changed(before: Image.Image | None, after: Image.Image | None) -> bool | None:
    """画面换没换人：``True`` 换了、``False`` 没动、``None`` 比不出来（缺帧或尺寸不一致）。

    少一帧时**不猜**——调用方按「没动」处理（宁可少写一条读数，也不把读数写到错的人头上）。
    """
    if before is None or after is None:
        return None
    delta = frame_delta(before, after)
    if delta is None:
        return None
    return delta >= FRAME_CHANGED_MIN_DELTA


def _values(pixels: Image.Image) -> list[int]:
    """取样框里的灰度值（``L`` 模式下 ``getdata()`` 给的就是一串 int）。"""
    return [int(value) for value in pixels.getdata()]
