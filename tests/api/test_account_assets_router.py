"""M43 后续：游戏资源路由（`GET /api/account/assets/{kind}/{asset_id}`）。

服务层的取图/缓存/白名单在 `tests/services/test_account_assets.py`；这里只管路由：
鉴权（`<img>` 带不了 token，所以前端用 fetch + blob）、响应类型与长缓存头、越界参数 404。
"""

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import account as account_router
from maa_api.services.account.assets import GameAssetService

PNG = b"\x89PNG\r\n\x1a\nfake-png-bytes"
AVATAR_ID = "char_002_amiya"


@pytest.mark.parametrize("tmp_settings", ["secret-token"], indirect=True)
def test_asset_route_serves_png_with_cache_headers_and_requires_auth(tmp_path, make_client, tmp_settings):
    """`tmp_settings` 显式给一个 token：留空时鉴权是关的（开发模式），401 就测不出来。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    app = FastAPI()
    # 统一错误体必须注册：少了它，AppError 会直接冒成 500（鉴权失败也测不出来）。
    register_exception_handlers(app)
    app.include_router(account_router.router)
    app.state.game_asset_service = GameAssetService(
        transport=httpx.MockTransport(handler), cache_dir=tmp_path
    )
    client = make_client(app)

    ok = client.get(f"/api/account/assets/avatar/{AVATAR_ID}", headers={"X-Token": "secret-token"})
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "image/png"
    # 一周：游戏资源只随版本更新，id 相同内容不会变。
    assert ok.headers["cache-control"] == "public, max-age=604800"
    assert ok.content == PNG

    # 没有 token → 401（前端用带 token 的 fetch 取 blob，`<img src>` 带不了头）。
    assert client.get(f"/api/account/assets/avatar/{AVATAR_ID}").status_code == 401
    # 未知 kind 与越界 id 都是 404，不是 500。
    assert client.get("/api/account/assets/sticker/x", headers={"X-Token": "secret-token"}).status_code == 404
    assert (
        client.get("/api/account/assets/avatar/..%2Fetc", headers={"X-Token": "secret-token"}).status_code
        == 404
    )
