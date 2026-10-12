"""游戏美术资源（干员头像 / 技能图标）的后端代理与磁盘缓存（M43 后续）。

**为什么不让浏览器直连官方 CDN**：`web.hycdn.cn` 有防盗链——同一张头像，不带 ``Referer``
是 200，带上 ``Referer`` 立刻 403（2026-10-12 实测，正是页面里头像全变灰占位的原因）。
浏览器一定会发 ``Referer``，所以图片只能由后端取、前端再从本机服务读。

三条约束：

- **只代理两类固定资源**：``char/avatar/{charId}.png`` 与 ``char_skill/{skillId}.png``。
  ``asset_id`` 走严格白名单正则，URL 里不允许出现 ``/``、``..`` 之类的东西——这不是通用
  代理，拿不到第三个上游路径。
- **落盘缓存**：命中就直发，不再打上游；缓存目录跟着 ``resource/`` 走（与截图、日志同处），
  换机/清理不心疼，重新拉一次即可。
- **同一个 key 只拉一次**：进程内按 key 加锁，385 名干员首屏并发请求同一张图时不会重复打
  上游（也顺手降低被上游限流的概率）。
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import httpx

from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "ASSET_KINDS",
    "MAX_ASSET_BYTES",
    "GameAssetService",
    "asset_cache_dir",
]

logger = logging.getLogger(__name__)

#: 资源类型 → 上游路径前缀。顺序即白名单，别的一律 404。
ASSET_KINDS: dict[str, str] = {
    "avatar": "char/avatar",
    "skill": "char_skill",
}

#: 上游基址（与 ``docs/13`` §8.63 五的记录同一份事实）。
ASSET_BASE_URL = "https://web.hycdn.cn/arknights/game/assets"

#: ``asset_id`` 白名单：字母数字下划线，外加技能 id 里真的会出现的 ``[]().``——多一个字符
#: 都不收，杜绝路径穿越与把代理变成任意 URL 抓取器。
ASSET_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\[\]().\-]{1,64}$")

#: 单张图上限：头像 65 KB、技能图标 17 KB 量级，1 MB 足够挡住异常响应。
MAX_ASSET_BYTES = 1024 * 1024

#: 上游取图超时。
ASSET_TIMEOUT_SECONDS = 15.0


def asset_cache_dir() -> Path:
    """缓存目录：``<仓库根>/resource/assets-cache``。

    与 ``services/account`` 里其它落盘物一样放 ``resource/`` 下——它是运行期产物，
    不进版本库，清掉只会让下一次请求重新拉。
    """
    return Path(__file__).resolve().parents[3] / "resource" / "assets-cache"


class GameAssetService:
    """取一张游戏资源图：先看磁盘，再打上游，最后写缓存。

    :param transport: 测试注入用（``httpx.MockTransport``）；生产为 ``None``。
    :param cache_dir: 缓存根目录，默认 :func:`asset_cache_dir`。
    """

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self._cache_dir = cache_dir or asset_cache_dir()
        self._locks: dict[str, asyncio.Lock] = {}
        # 与森空岛客户端同一条教训（M42 真机）：**不读系统代理**。代理不可达时 httpx 会
        # 安静地走它并在超时后失败，而直连明明能用。
        self._client = httpx.AsyncClient(
            timeout=ASSET_TIMEOUT_SECONDS,
            transport=transport,
            trust_env=False,
            headers={"User-Agent": "MAA-API/asset-proxy"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- 对外 -------------------------------------------------------------
    async def get(self, kind: str, asset_id: str) -> bytes:
        """取一张图；上游没有这张图时抛 ``ACCOUNT_DATA_NOT_FOUND``（对前端就是 404）。"""
        prefix = ASSET_KINDS.get(kind)
        if prefix is None or not ASSET_ID_PATTERN.match(asset_id):
            raise self._not_found(kind, asset_id)

        path = self._cache_dir / kind / f"{asset_id}.png"
        cached = await asyncio.to_thread(_read_bytes, path)
        if cached is not None:
            return cached

        lock = self._locks.setdefault(f"{kind}/{asset_id}", asyncio.Lock())
        async with lock:
            # 等锁期间别人可能已经拉回来了：再查一次盘，避免重复打上游。
            cached = await asyncio.to_thread(_read_bytes, path)
            if cached is not None:
                return cached
            payload = await self._download(prefix, kind, asset_id)
            await asyncio.to_thread(_write_bytes, path, payload)
            return payload

    # -- 内部 -------------------------------------------------------------
    async def _download(self, prefix: str, kind: str, asset_id: str) -> bytes:
        url = f"{ASSET_BASE_URL}/{prefix}/{asset_id}.png"
        try:
            response = await self._client.get(url)
        except httpx.HTTPError as exc:
            logger.warning("取游戏资源失败 kind=%s id=%s：%s", kind, asset_id, exc)
            raise AppError(
                ErrorCode.SKLAND_UPSTREAM_FAILED,
                "游戏资源下载失败，请稍后重试",
                {"kind": kind, "asset_id": asset_id},
            ) from exc

        if response.status_code == 404:
            raise self._not_found(kind, asset_id)
        if response.status_code >= 400:
            raise AppError(
                ErrorCode.SKLAND_UPSTREAM_FAILED,
                f"游戏资源上游返回 {response.status_code}",
                {"kind": kind, "asset_id": asset_id},
            )

        content_type = (response.headers.get("content-type") or "").split(";")[0].strip()
        if content_type != "image/png":
            # 防盗链会回 HTML（带 Referer 时就是 403 + text/html）：宁可当作没有，
            # 也不要把一页 HTML 当图片塞进缓存。
            logger.warning(
                "游戏资源内容不是 PNG kind=%s id=%s type=%s", kind, asset_id, content_type
            )
            raise self._not_found(kind, asset_id)

        payload = response.content
        if not payload or len(payload) > MAX_ASSET_BYTES:
            logger.warning("游戏资源大小异常 kind=%s id=%s bytes=%d", kind, asset_id, len(payload))
            raise AppError(
                ErrorCode.SKLAND_UPSTREAM_FAILED,
                "游戏资源大小异常",
                {"kind": kind, "asset_id": asset_id},
            )
        return payload

    @staticmethod
    def _not_found(kind: str, asset_id: str) -> AppError:
        return AppError(
            ErrorCode.ACCOUNT_DATA_NOT_FOUND,
            "没有这张游戏资源图",
            {"kind": kind, "asset_id": asset_id},
        )


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:  # pragma: no cover - 磁盘异常只影响缓存命中率
        logger.warning("读资源缓存失败 %s：%s", path, exc)
        return None


def _write_bytes(path: Path, payload: bytes) -> None:
    """原子写：先写临时文件再改名，避免半个文件被后续请求当成缓存命中。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".png.tmp")
        tmp.write_bytes(payload)
        tmp.replace(path)
    except OSError as exc:  # pragma: no cover - 写不进缓存不影响本次响应
        logger.warning("写资源缓存失败 %s：%s", path, exc)
