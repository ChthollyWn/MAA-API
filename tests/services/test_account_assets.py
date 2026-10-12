"""M43 后续：游戏资源图（头像 / 技能图标）的后端代理与磁盘缓存（服务层）。

路由层的鉴权/响应头在 ``tests/api/test_account_assets_router.py``（那边有 ``tmp_settings``
与 ``make_client`` 两个夹具）。


**为什么需要它**：官方 CDN `web.hycdn.cn` 有防盗链——同一张头像，不带 `Referer` 是 200，
带上就是 **403**（2026-10-12 实测，页面上头像全变灰占位就是这个原因）。浏览器一定会带
`Referer`，所以图片只能由后端取、前端再从本机读。

六组断言：

1. 命中磁盘缓存后**不再打上游**（同一张图取两次只有一次 HTTP）；
2. `asset_id` 白名单：路径穿越、超长、带斜杠一律 404，不把代理变成任意抓取器；
3. 未知 `kind` 直接 404；
4. 上游 404 → `ACCOUNT_DATA_NOT_FOUND`；
5. 上游返回非 PNG（防盗链回 HTML 的现场）→ 当作没有这张图，**不写缓存**。
"""

import asyncio
from pathlib import Path

import httpx
import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.account.assets import GameAssetService

PNG = b"\x89PNG\r\n\x1a\nfake-png-bytes"
AVATAR_ID = "char_002_amiya"


def _png_response(request: httpx.Request) -> httpx.Response:
    assert request.url.path.endswith(f"/char/avatar/{AVATAR_ID}.png")
    return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})


def test_second_get_hits_the_disk_cache(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return _png_response(request)

    service = GameAssetService(transport=httpx.MockTransport(handler), cache_dir=tmp_path)

    async def scenario() -> None:
        first = await service.get("avatar", AVATAR_ID)
        second = await service.get("avatar", AVATAR_ID)
        assert first == second == PNG
        # 并发取同一张图也只打一次上游（按 key 加锁 + 二次查盘）。
        results = await asyncio.gather(*[service.get("avatar", AVATAR_ID) for _ in range(5)])
        assert results == [PNG] * 5

    asyncio.run(scenario())
    assert calls["n"] == 1
    assert (tmp_path / "avatar" / f"{AVATAR_ID}.png").read_bytes() == PNG


@pytest.mark.parametrize(
    "asset_id",
    (
        "../etc/passwd",
        "char_002_amiya/../../secret",
        "a" * 65,
        "char 002 amiya",
        "",
    ),
)
def test_asset_id_whitelist_rejects_everything_odd(tmp_path: Path, asset_id: str) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不该被调用
        calls["n"] += 1
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    service = GameAssetService(transport=httpx.MockTransport(handler), cache_dir=tmp_path)

    async def scenario() -> None:
        with pytest.raises(AppError) as excinfo:
            await service.get("avatar", asset_id)
        assert excinfo.value.code is ErrorCode.ACCOUNT_DATA_NOT_FOUND

    asyncio.run(scenario())
    assert calls["n"] == 0


def test_unknown_kind_is_not_found(tmp_path: Path) -> None:
    service = GameAssetService(transport=httpx.MockTransport(_png_response), cache_dir=tmp_path)

    async def scenario() -> None:
        with pytest.raises(AppError) as excinfo:
            await service.get("sticker", AVATAR_ID)
        assert excinfo.value.code is ErrorCode.ACCOUNT_DATA_NOT_FOUND

    asyncio.run(scenario())


def test_upstream_404_becomes_data_not_found(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"nope", headers={"content-type": "text/html"})

    service = GameAssetService(transport=httpx.MockTransport(handler), cache_dir=tmp_path)

    async def scenario() -> None:
        with pytest.raises(AppError) as excinfo:
            await service.get("skill", "skchr_missing_1")
        assert excinfo.value.code is ErrorCode.ACCOUNT_DATA_NOT_FOUND

    asyncio.run(scenario())
    assert not (tmp_path / "skill").exists()


def test_non_png_body_is_not_cached(tmp_path: Path) -> None:
    """防盗链回 HTML 的现场：宁可当作没有，也不要把一页 HTML 当图片写进缓存。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>403</html>", headers={"content-type": "text/html"})

    service = GameAssetService(transport=httpx.MockTransport(handler), cache_dir=tmp_path)

    async def scenario() -> None:
        with pytest.raises(AppError) as excinfo:
            await service.get("avatar", AVATAR_ID)
        assert excinfo.value.code is ErrorCode.ACCOUNT_DATA_NOT_FOUND

    asyncio.run(scenario())
    assert not (tmp_path / "avatar").exists()
