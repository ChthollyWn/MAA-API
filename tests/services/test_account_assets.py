"""M43 后续 / M44 §4.3：游戏资源图（头像 / 技能图标 / 道具图标）的后端代理与磁盘缓存。

路由层的鉴权/响应头在 ``tests/api/test_account_assets_router.py``（那边有 ``tmp_settings``
与 ``make_client`` 两个夹具）。


**为什么需要它**：官方 CDN `web.hycdn.cn` 有防盗链——同一张头像，不带 `Referer` 是 200，
带上就是 **403**（2026-10-12 实测，页面上头像全变灰占位就是这个原因）。浏览器一定会带
`Referer`，所以图片只能由后端取、前端再从本机读。

**道具图（M44）**：接口收的是 **item_id**（`3003`），图标文件名 `3003.png` 由服务端从本机
`item_index.json` 的 `icon` 字段解析（用例注入 `{itemId: icon}` 替身，不依赖真资源）；
解析不到就是 404，连上游都不打；拼上游 URL 时 `.png` 不重复补。

八组断言：

1. 命中磁盘缓存后**不再打上游**（同一张图取两次只有一次 HTTP）；
2. `asset_id` 白名单：路径穿越、超长、带斜杠一律 404，不把代理变成任意抓取器；
3. 未知 `kind` 直接 404；
4. 上游 404 → `ACCOUNT_DATA_NOT_FOUND`；
5. 上游返回非 PNG（防盗链回 HTML 的现场）→ 当作没有这张图，**不写缓存**；
6. 道具：item_id → icon 解析后取图，上游 URL 是 `item/3003.png`（不是 `.png.png`），
   缓存落在 `item/3003.png`；
7. 道具：`item_index.json` 里没有这个 id → 404，且**没打上游**；
8. 道具：第二次取同一件走磁盘缓存，不重复打上游。
"""

import asyncio
from pathlib import Path

import httpx
import pytest

from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.account import capture as capture_module
from maa_api.services.account.assets import GameAssetService

PNG = b"\x89PNG\r\n\x1a\nfake-png-bytes"
AVATAR_ID = "char_002_amiya"
#: 本机 ``item_index.json`` 里的两条真实映射（一条纯数字、一条带下划线），用来验
#: 「item_id → icon」这一段真的接上了。
ITEM_ICONS = {"3003": "3003.png", "mod_unlock_token": "mod_unlock_token.png"}


@pytest.fixture
def item_icons(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """把道具图标目录换成替身：用例不读真 ``item_index.json``。

    补丁打在 ``capture`` 上（不是 ``assets``）：解析用的 ``load_item_icons`` 是函数内
    导入的，这样打才盖得住（顺带也说明那处导入只在真的取道具图时才拉）。
    """
    monkeypatch.setattr(capture_module, "load_item_icons", lambda: dict(ITEM_ICONS))
    return ITEM_ICONS


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


# --------------------------------------------------------------------------- #
# 道具图（M44 §4.3）
# --------------------------------------------------------------------------- #
def test_item_id_resolves_to_icon_and_appends_png_once(
    tmp_path: Path, item_icons: dict[str, str]
) -> None:
    """`3003` 取回来的是 `item/3003.png`：icon 自带后缀，不能再补一次。"""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    service = GameAssetService(transport=httpx.MockTransport(handler), cache_dir=tmp_path)

    async def scenario() -> None:
        assert await service.get("item", "3003") == PNG
        assert await service.get("item", "mod_unlock_token") == PNG

    asyncio.run(scenario())
    assert seen == [
        "https://web.hycdn.cn/arknights/game/assets/item/3003.png",
        "https://web.hycdn.cn/arknights/game/assets/item/mod_unlock_token.png",
    ]
    # 缓存名也跟着 icon 走（不是 `3003.png.png`）。
    assert (tmp_path / "item" / "3003.png").read_bytes() == PNG


def test_unknown_item_id_is_not_found_without_hitting_upstream(
    tmp_path: Path, item_icons: dict[str, str]
) -> None:
    """索引里没有这个 id（M44：甚至可能是别的 kind 的 id）→ 404，不打上游。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不该被调用
        calls["n"] += 1
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    service = GameAssetService(transport=httpx.MockTransport(handler), cache_dir=tmp_path)

    async def scenario() -> None:
        for asset_id in ("99999", "char_002_amiya", "..%2Fetc"):
            with pytest.raises(AppError) as excinfo:
                await service.get("item", asset_id)
            assert excinfo.value.code is ErrorCode.ACCOUNT_DATA_NOT_FOUND
            assert excinfo.value.details["kind"] == "item"

    asyncio.run(scenario())
    assert calls["n"] == 0
    assert not (tmp_path / "item").exists()


def test_item_second_get_hits_the_disk_cache(
    tmp_path: Path, item_icons: dict[str, str]
) -> None:
    """道具图同样落盘缓存：第二次取不再打上游。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    service = GameAssetService(transport=httpx.MockTransport(handler), cache_dir=tmp_path)

    async def scenario() -> None:
        assert await service.get("item", "3003") == PNG
        assert await service.get("item", "3003") == PNG

    asyncio.run(scenario())
    assert calls["n"] == 1

