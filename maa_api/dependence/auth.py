from typing import Optional
from fastapi import Request, Cookie, Header
from maa_api.config.config import Config
from maa_api.model.request.response import ResponseCode
from maa_api.exception.response_exception import ResponseException

def token_auth(
    request: Request,
    token: Optional[str] = None,                   # 支持通过 URL query 传递
    x_token: Optional[str] = Header(None),         # 支持通过 Header X-Token 传递
    auth_cookie: Optional[str] = Cookie(None, alias="token")  # 支持通过 Cookie token 传递
):
    """
    统一的多渠道鉴权方案。
    支持从 Query Parameter, HTTP Header (X-Token / Authorization), Cookie 中读取 token。
    """
    access_token = Config.get_config("app", "access_token")
    if not access_token:
        # 如果未配置，免鉴权放行
        return

    # 提取 Bearer Token（前端请求最规范的做法）
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        bearer_token = auth_header.split(" ")[1]
    else:
        bearer_token = None

    # 优先级：Header(Bearer) > Header(X-Token) > Query(token) > Cookie(token)
    client_token = bearer_token or x_token or token or auth_cookie

    if not client_token or client_token != access_token:
        raise ResponseException(code=ResponseCode.UNAUTHORIZED.value, message="未授权访问，请提供有效的鉴权 Token")