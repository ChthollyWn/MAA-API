"""截图归档公共函数（M86 spec §4.2、§4.5.2；M89 复用）。

把一张已解码的 Pillow 图落成内容寻址的 JPEG 文件（:func:`maa_api.util.image.store_screenshot`），
再写一条 ``screenshot`` 行；**不 commit**，事务边界归调用方：

- ``Navigator`` 加样本时归档后立即提交，再写样本（spec §4.5.2）；
- 学习路径归档失败只记 warning、``screenshot_id`` 记 null，不阻断导航。

路径固定 ``image/screenshot/<sha 前两位>/<sha>.jpg``，格式 jpg；行里写
width/height/size_bytes，返回给调用方的 dict 只带四个键。
"""

from __future__ import annotations

import asyncio
from typing import Any

from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from maa_api.db.models import Screenshot
from maa_api.db.repositories.log import ScreenshotRepository
from maa_api.domain.enums import ScreenshotBackend, ScreenshotTrigger
from maa_api.util.image import store_screenshot

__all__ = ["archive_screenshot"]


async def archive_screenshot(
    session: AsyncSession,
    image: Image.Image,
    *,
    trigger: ScreenshotTrigger,
    backend: ScreenshotBackend,
) -> tuple[Screenshot, dict[str, Any]]:
    """落文件 + 建 ``screenshot`` 行（不 commit），返回 ``(行, 附件信息)``。

    附件信息含 ``{"screenshot_id", "sha256", "full_url", "thumb_url"}``。编码 JPEG
    与生成缩略图是同步 IO，放到线程里跑，避免阻塞事件循环。
    """
    stored = await asyncio.to_thread(store_screenshot, image)
    sha256 = str(stored["sha256"])
    relative_path = f"image/screenshot/{sha256[:2]}/{sha256}.jpg"
    screenshot = await ScreenshotRepository(session).create(
        Screenshot(
            trigger=trigger,
            backend=backend,
            path=relative_path,
            format="jpg",
            width=int(stored["width"]),
            height=int(stored["height"]),
            size_bytes=int(stored["bytes"]),
        )
    )
    return screenshot, {
        "screenshot_id": screenshot.id,
        "sha256": sha256,
        "full_url": str(stored["full_url"]),
        "thumb_url": str(stored["thumb_url"]),
    }
