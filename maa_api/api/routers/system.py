"""system 组：健康检查与 token↔cookie 的换取／清除（docs/05 §6.1、§5.2–§5.4、docs/13 §4）。

只装配三条路由，不承载业务：

``GET /api/system/health``（免鉴权，docs/05 §5.3）
    返回服务自身状态，以及从 ``app.state`` 读取的内核、设备和队列状态。运行时尚未装配
    时仍返回 200，未就绪的运行态对象为 ``null``。

``POST /api/system/auth/cookie``
    用头部 token 换 ``HttpOnly; SameSite=Lax`` 的 ``maa_token`` cookie，供浏览器
    WebSocket 握手自动携带（docs/05 §5.4）。写方法，必须经头部 token 鉴权。

``DELETE /api/system/auth/cookie``
    服务端清除该 cookie（docs/13 §4）：cookie 是 HttpOnly，前端 JS 删不掉，没有这个
    端点「登出」后 WebSocket 仍能连上。

前缀写在 :data:`router` 自己身上（``APIRouter(prefix="/api/system")``），不在挂载处拼，
模块才能独立挂载与测试。``/api/system/health`` 已在 :mod:`maa_api.api.deps` 的豁免清单
里，路由侧不再挂 ``require_auth``，挂了会把健康检查挡在 401 外（docs/05 §5.3）。两个写
方法挂 ``Depends(require_auth)``，仅凭 cookie 调它们由
:func:`~maa_api.api.deps.require_auth` 判 403（cookie 渠道收窄，docs/05 §5.1）。

204 响应不能先在注入的 ``response: Response`` 上 ``set_cookie()`` 再
``return Response(status_code=204)``：FastAPI 只在返回值不是 Response 实例时才把注入响应
的头并进最终响应，直接返回 Response 会把 ``Set-Cookie`` 整个丢掉。统一用
``status_code=204`` + ``response_class=Response`` + ``return None``：无体、无
``Content-Type``，注入响应的 ``Set-Cookie`` 正常保留。
"""

from __future__ import annotations

from importlib.metadata import version

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from maa_api.api.deps import COOKIE_NAME, auth_enabled, require_auth
from maa_api.util.http import request_is_https
from maa_api.api.errors import error_responses
from maa_api.settings import get_settings

__all__ = ["HealthResponse", "router"]

#: system 组路由；前缀与 tag 都写在模块里。
router = APIRouter(prefix="/api/system", tags=["system"])

#: 读不到分发元数据时的回退版本（与 pyproject.toml 的 ``version`` 一致）。
_FALLBACK_VERSION = "0.1.0"


def _service_version() -> str:
    """服务版本：读已安装分发包 ``maa-api`` 的元数据，失败回退 :data:`_FALLBACK_VERSION`。

    **只读 ``importlib.metadata``**，不 import ``maa_api.main``、不碰内核（
    尚未接内核，健康检查不能因为内核缺失而 500）。元数据缺失（源码目录直接跑、
    打包异常）不该让健康检查挂掉，所以这里吞掉任何取版本异常。
    """
    try:
        return version("maa-api")
    except Exception:  # noqa: BLE001 - 健康检查不能因元数据异常而 500
        return _FALLBACK_VERSION


class CoreHealth(BaseModel):
    """内核状态：运行状态、进程 pid 与重启代际。"""

    state: str
    pid: int | None
    generation: int


class DeviceRetryHealth(BaseModel):
    """自动重连状态：已尝试次数、上限与下次重连时间（epoch 秒）。"""

    attempt: int
    max: int
    next_at: float | None


class DeviceHealth(BaseModel):
    """设备连接状态与最近一次连接信息。"""

    core_id: str = "default"
    state: str = "disconnected"
    address: str | None = None
    uuid: str | None = None
    resolution: dict[str, int] | None = None
    last_connected_at: float | None = None
    retry: DeviceRetryHealth
    last_error: dict[str, object] | None = None


class QueueHealth(BaseModel):
    """队列计数与暂停状态。"""

    pending: int
    running: int
    paused: bool


class HealthResponse(BaseModel):
    """``GET /api/system/health`` 的响应体。

    ``core`` / ``device`` / ``queue`` 在运行时对象尚未就绪时为 ``null``，健康探测仍返回 200。
    ``started_at`` 是进程启动时间，尚未写入时为 ``null``（键始终存在）。
    """

    status: str = "ok"
    auth_enabled: bool
    version: str
    started_at: str | None = None
    core: CoreHealth | None = None
    device: DeviceHealth | None = None
    queue: QueueHealth | None = None


def _started_at_text(value: object) -> str | None:
    """把 ``app.state.started_at`` 归一成字符串（``datetime`` → ISO 8601）。

    字段契约是 ``str | None``，但写入方是 lifespan，这里无法约束它写
    ``str`` 还是 ``datetime``；健康检查是容器探活端点，不能因为写入方给了
    ``datetime`` 就 pydantic 校验失败 500。``None`` 原样返回（响应里是 ``null``）。
    """
    if value is None or isinstance(value, str):
        return value
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


@router.get(
    "/health",
    summary="获取健康状态",
    description=(
        "返回服务自身状态与运行态概览：内核、设备、队列。免鉴权，无需携带凭据。\n\n"
        "- `auth_enabled` 由是否配置 `access_token` 决定；为 `false` 时前端跳过登录页\n"
        "- `started_at` 是进程启动时间（ISO 8601），尚未写入时为 `null`\n"
        "- 运行时对象尚未就绪时 `core` / `device` / `queue` 为 `null`，仍返回 200"
    ),
    response_model=HealthResponse,
)
async def health(request: Request) -> HealthResponse:
    """读取运行态快照；不初始化任何运行时依赖。"""
    state = request.app.state
    core_supervisor = getattr(state, "core_supervisor", None)
    core = None
    if core_supervisor is not None:
        core_state = core_supervisor.state
        core = CoreHealth(
            state=str(getattr(core_state, "value", core_state)),
            pid=core_supervisor.pid,
            generation=core_supervisor.generation,
        )

    device_manager = getattr(state, "device_manager", None)
    if device_manager is not None:
        device_snapshot = device_manager.snapshot()
        device_snapshot.setdefault("retry", {"attempt": 0, "max": 0, "next_at": None})
        device = DeviceHealth.model_validate(device_snapshot)
    else:
        device = None

    queue_service = getattr(state, "queue_service", None)
    queue = None
    if queue_service is not None:
        snapshot = await queue_service.snapshot()
        counts = snapshot.get("counts", {})
        queue = QueueHealth(
            pending=counts.get("pending", 0),
            running=counts.get("running", 0),
            paused=snapshot.get("paused", False),
        )

    return HealthResponse(
        status="ok",
        auth_enabled=auth_enabled(),
        version=_service_version(),
        started_at=_started_at_text(getattr(state, "started_at", None)),
        core=core,
        device=device,
        queue=queue,
    )


@router.post(
    "/auth/cookie",
    status_code=204,
    response_class=Response,
    summary="换取认证 cookie",
    description=(
        "用请求头里的 token 换取一枚 `HttpOnly; SameSite=Lax` 的 `maa_token` cookie，"
        "供浏览器 WebSocket 握手自动携带，响应无体（204）。\n\n"
        "- 副作用：下发 `Set-Cookie`，值即 `access_token`、`Path=/`；"
        "`Secure` 由请求的 `X-Forwarded-Proto` 决定\n"
        "- 免鉴权模式（未配置 `access_token`）返回 204 但不下发 cookie\n"
        "- 仅头部 token 可调用：仅凭 cookie 返回 403，缺少 token 或不匹配返回 401"
    ),
    responses=error_responses("UNAUTHORIZED", "FORBIDDEN"),
    dependencies=[Depends(require_auth)],
)
def exchange_cookie(request: Request, response: Response) -> None:
    """下发 ``maa_token`` cookie；免鉴权模式下无秘密可发，直接 204。

    ``Secure`` 按 ``X-Forwarded-Proto`` 判定：``tailscale serve`` 这类反向代理负责
    终止 HTTPS，只看连接本身就会在 HTTPS 站点上漏掉 ``Secure``。
    """
    token = get_settings().access_token
    if not token:
        return
    response.set_cookie(
        COOKIE_NAME,
        token,
        path="/",
        httponly=True,
        samesite="lax",
        secure=request_is_https(request),
    )


@router.delete(
    "/auth/cookie",
    status_code=204,
    response_class=Response,
    summary="清除认证 cookie",
    description=(
        "清除 `maa_token` cookie，供前端「登出」时调用，响应无体（204）。\n\n"
        "- 副作用：下发 `Set-Cookie`（空值 + `Max-Age=0`），浏览器随后删除该 cookie\n"
        "- 仅头部 token 可调用：仅凭 cookie 返回 403，缺少 token 或不匹配返回 401\n"
        "- 幂等：没有 cookie 时调用同样返回 204"
    ),
    responses=error_responses("UNAUTHORIZED", "FORBIDDEN"),
    dependencies=[Depends(require_auth)],
)
def clear_cookie(response: Response) -> None:
    """清除 ``maa_token`` cookie（``Max-Age=0``）。"""
    response.delete_cookie(COOKIE_NAME, path="/")
