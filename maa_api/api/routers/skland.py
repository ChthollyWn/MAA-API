"""森空岛账号端点（M42 规格 §4.6）：扫码登录、手动兜底绑定、状态、同步、解绑。

六个端点全部要鉴权（``Depends(require_auth)``，与 ``/api/account`` 同一层）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | ``/api/skland/login/qrcode`` | 生成扫码会话（返回内联 SVG 二维码） |
| GET | ``/api/skland/login/qrcode/{session_id}`` | 轮询一次（四态：waiting/succeeded/expired/failed） |
| POST | ``/api/skland/login/manual`` | 手动粘贴 ``cred`` / 签名密钥 |
| GET | ``/api/skland/status`` | 绑定与同步状态 |
| POST | ``/api/skland/sync`` | 同步练度（**同步执行**，几百毫秒到几秒） |
| DELETE | ``/api/skland/binding`` | 解绑（删凭据，保留练度） |

路由层只做三件事：解析请求体、取服务、把服务返回的字典按响应模型透出。**响应模型是
写全的**：这些字段前端要用，写进 OpenAPI 才能由 ``pnpm gen:api`` 生成类型，免得
前端再手写一层覆盖断言。

服务未装配（lifespan 还在启动）时统一回 503 ``SERVICE_UNAVAILABLE``，与其它路由同款。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from maa_api.api.deps import require_auth
from maa_api.api.errors import error_responses
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.skland.login import SklandLoginService
from maa_api.services.skland.sync import SklandSyncService

__all__ = [
    "SklandBindResult",
    "SklandManualBindRequest",
    "SklandQrPollResult",
    "SklandQrStartResult",
    "SklandRoleRow",
    "SklandStatusResult",
    "SklandSyncResult",
    "router",
]

router = APIRouter(prefix="/api/skland", tags=["skland"])

#: 会话 id 的形状：``uuid4().hex``（32 位十六进制）。用约束挡在路由层，服务层不必猜。
SessionId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$", description="扫码会话 id")]


class SklandRoleRow(BaseModel):
    """一个游戏角色（``bindingList`` 的一条）。"""

    uid: str = Field(description="游戏角色 uid，`player/info` 用它取数")
    app_code: str = Field(description="归属应用，目前只有 `arknights`")
    channel_master_id: str | None = Field(default=None, description="渠道主 id，上游不给就是 null")
    nickname: str | None = Field(default=None, description="角色昵称")
    level: int | None = Field(default=None, description="角色等级")
    is_default: bool = Field(description="上游的 isDefault：多角色时同步取它")
    synced_at: str | None = Field(default=None, description="最近一次成功同步这个角色的时间")


class SklandQrStartResult(BaseModel):
    """扫码会话：把 ``qr_svg`` 直接塞进 ``<img src="data:image/svg+xml;…">`` 即可。"""

    session_id: str
    qr_svg: str = Field(description="完整 SVG 文档（后端零依赖生成）")
    payload: str = Field(description="二维码里的登录串 hypergryph://scan_login?scanId=…")
    expires_at: str = Field(description="会话失效时间（UTC，ISO8601）")
    poll_interval_ms: int = Field(description="建议轮询间隔（毫秒）")


class SklandQrPollResult(BaseModel):
    """轮询结果；``state`` 只有四态，上游没有「已扫码待确认」的中间态。"""

    state: str = Field(description="waiting 等待扫码 / succeeded / expired / failed")
    message: str | None = Field(default=None, description="失败或过期时的说明")
    account: SklandRoleRow | None = Field(default=None, description="成功时的默认角色")
    roles: list[SklandRoleRow] | None = Field(default=None, description="成功时的全部角色")


class SklandManualBindRequest(BaseModel):
    """手动兜底绑定：扫码走不通时粘贴鹰角通行证算出来的 ``cred`` 与签名密钥。"""

    model_config = ConfigDict(extra="forbid")

    cred: str = Field(min_length=1, max_length=512, description="森空岛 cred")
    token: str = Field(min_length=1, max_length=512, description="签名密钥")


class SklandSyncResult(BaseModel):
    """一轮同步的摘要；``status`` 目前恒为 ``succeeded``（失败走错误码）。"""

    status: str
    role: SklandRoleRow
    synced_at: str
    total: int = Field(description="这一轮拿到的干员数")
    created: int = Field(description="新建的行数")
    updated: int = Field(description="有字段变化的行数")
    unchanged: int = Field(description="值没变、只刷新同步时间的行数")
    ownership_cleared: int = Field(description="被收缩成未拥有的行数")
    diffs: int = Field(description="识别覆盖手动值、记了差异的字段数")
    dropped_invalid: int = Field(description="越界或映射不出、被丢掉的字段项数")
    duration_ms: int


class SklandStatusResult(BaseModel):
    """绑定与同步状态。"""

    bound: bool = Field(description="库里有没有凭据")
    key_configured: bool = Field(description="skland.credential_key 是否已配置且合法")
    account: SklandRoleRow | None = None
    roles: list[SklandRoleRow] = Field(default_factory=list)
    last_sync_at: str | None = None
    last_sync: SklandSyncResult | None = None
    last_error: str | None = None
    min_sync_interval_seconds: int
    min_chars: int
    auto_sync_enabled: bool
    auto_sync_interval_minutes: int


class SklandBindResult(BaseModel):
    """解绑结果。"""

    bound: bool = False


def _login_service(request: Request) -> SklandLoginService:
    service = getattr(request.app.state, "skland_login_service", None)
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "森空岛登录服务尚未装配")
    return service


def _sync_service(request: Request) -> SklandSyncService:
    service = getattr(request.app.state, "skland_sync_service", None)
    if service is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "森空岛同步服务尚未装配")
    return service


@router.post(
    "/login/qrcode",
    dependencies=[Depends(require_auth)],
    response_model=SklandQrStartResult,
    responses=error_responses(
        ErrorCode.SERVICE_UNAVAILABLE,
        ErrorCode.SKLAND_ENCRYPTION_KEY_INVALID,
        ErrorCode.SKLAND_UPSTREAM_FAILED,
    ),
    summary="生成森空岛扫码登录二维码",
)
async def start_skland_qr_login(request: Request) -> dict[str, Any]:
    """生成一张二维码；用户用森空岛 App 扫它并在 App 里确认。"""
    return await _login_service(request).start()


@router.get(
    "/login/qrcode/{session_id}",
    dependencies=[Depends(require_auth)],
    response_model=SklandQrPollResult,
    responses=error_responses(
        ErrorCode.SERVICE_UNAVAILABLE,
        ErrorCode.SKLAND_QR_SESSION_NOT_FOUND,
        ErrorCode.SKLAND_UPSTREAM_FAILED,
    ),
    summary="轮询扫码结果",
)
async def poll_skland_qr_login(request: Request, session_id: SessionId) -> dict[str, Any]:
    """轮询一次；``succeeded`` 时凭据已经落库，前端刷新 ``GET /api/skland/status`` 即可。"""
    return await _login_service(request).poll(session_id)


@router.post(
    "/login/manual",
    dependencies=[Depends(require_auth)],
    response_model=SklandStatusResult,
    responses=error_responses(
        ErrorCode.SERVICE_UNAVAILABLE,
        ErrorCode.SKLAND_LOGIN_FAILED,
        ErrorCode.SKLAND_ENCRYPTION_KEY_INVALID,
        ErrorCode.SKLAND_UPSTREAM_FAILED,
    ),
    summary="手动粘贴 cred 绑定（扫码的兜底）",
)
async def bind_skland_manual(
    request: Request, payload: SklandManualBindRequest
) -> dict[str, Any]:
    """先用 ``binding`` 验一次凭据，再落库；返回与 ``GET /status`` 同形状。"""
    service = _login_service(request)
    await service.bind_manual(cred=payload.cred, token=payload.token)
    return await _sync_service(request).status()


@router.get(
    "/status",
    dependencies=[Depends(require_auth)],
    response_model=SklandStatusResult,
    responses=error_responses(ErrorCode.SERVICE_UNAVAILABLE),
    summary="森空岛绑定与同步状态",
)
async def get_skland_status(request: Request) -> dict[str, Any]:
    """没绑定也返回 200：``bound=false`` 是状态而不是错误。"""
    return await _sync_service(request).status()


@router.post(
    "/sync",
    dependencies=[Depends(require_auth)],
    response_model=SklandSyncResult,
    responses=error_responses(
        ErrorCode.SERVICE_UNAVAILABLE,
        ErrorCode.SKLAND_NOT_BOUND,
        ErrorCode.SKLAND_SYNC_BUSY,
        ErrorCode.RATE_LIMITED,
        ErrorCode.SKLAND_UPSTREAM_FAILED,
        ErrorCode.SKLAND_ENCRYPTION_KEY_INVALID,
    ),
    summary="同步森空岛练度（同步执行）",
)
async def sync_skland_training(request: Request) -> dict[str, Any]:
    """跑一轮同步；返回摘要。冷却期内的重复提交回 429，已有同步在跑回 409。"""
    return await _sync_service(request).sync()


@router.delete(
    "/binding",
    dependencies=[Depends(require_auth)],
    response_model=SklandBindResult,
    responses=error_responses(ErrorCode.SERVICE_UNAVAILABLE),
    summary="解绑森空岛账号",
)
async def unbind_skland(request: Request) -> dict[str, Any]:
    """删凭据与角色行；**干员练度保留**（它是账号数据，不随绑定关系消失）。"""
    await _login_service(request).unbind()
    return {"bound": False}
