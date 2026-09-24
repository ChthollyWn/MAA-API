"""system 组：健康检查与 token↔cookie 的换取／清除（docs/05 §6.1、§5.2–§5.4、docs/13 §4）。

本模块只装配三条路由，不承载业务：

``GET /api/system/health``（**免鉴权**，docs/05 §5.3）
    返回服务自身状态，以及从 ``app.state`` 读取的内核、设备和队列状态。运行时尚未
    装配时仍返回 200，未就绪的运行态对象为 ``null``。

``POST /api/system/auth/cookie``
    用头部 token 换 ``HttpOnly; SameSite=Lax`` 的 ``maa_token`` cookie，供浏览器
    WebSocket 握手自动携带（docs/05 §5.4）。写方法，必须经头部 token 鉴权。

``DELETE /api/system/auth/cookie``
    服务端清除该 cookie（docs/13 §4）：cookie 是 HttpOnly，前端 JS 删不掉，
    没有这个端点「登出」后 WebSocket 仍能连上。

路由前缀
========

前缀写在 :data:`router` 自己身上（``APIRouter(prefix="/api/system")``）：M3-09 只
``include_router(router)``，**不在挂载处拼前缀**。卡级 verify 以无前缀方式 include
本模块并断言完整路径，模块自带前缀才能独立测试、被 M3-09 直接装配。

鉴权边界
========

豁免由 :mod:`maa_api.api.deps` 的豁免清单承担：``/api/system/health`` 已在
``EXEMPT_PATH_PREFIXES`` 里，路由侧**刻意不再挂** ``require_auth`` —— 挂了会把健康
检查挡在 401 外，与 docs/05 §5.3 冲突。两个写方法才挂 ``Depends(require_auth)``；
仅凭 cookie 调它们会由 :func:`~maa_api.api.deps.require_auth` 判 403（cookie 渠道
收窄，docs/05 §5.1）。

204 响应的写法（实测，见 docs/ENVIRONMENT.md）
==================================================

**不能**在注入的 ``response: Response`` 上 ``set_cookie()`` 之后
``return Response(status_code=204)``：FastAPI 只在「返回值不是 Response 实例」时才把
注入响应的头并进最终响应，直接返回 Response 会把 ``Set-Cookie`` 整个丢掉（实测空头）。
因此这里统一用 ``status_code=204`` + ``response_class=Response`` + ``return None``：
无体、无 ``Content-Type``，注入响应的 ``Set-Cookie`` 正常保留。
"""

from __future__ import annotations

from importlib.metadata import version

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from maa_api.api.deps import COOKIE_NAME, auth_enabled, require_auth
from maa_api.api.errors import error_responses
from maa_api.settings import get_settings

__all__ = ["HealthResponse", "router"]

#: system 组路由；前缀与 tag 都写在模块里（M3-09 只 include_router，不再拼前缀）。
router = APIRouter(prefix="/api/system", tags=["system"])

#: 读不到分发元数据时的回退版本（与 pyproject.toml 的 ``version`` 一致）。
_FALLBACK_VERSION = "0.1.0"


def _service_version() -> str:
    """服务版本：读已安装分发包 ``maa-api`` 的元数据，失败回退 :data:`_FALLBACK_VERSION`。

    **只读 ``importlib.metadata``**，不 import ``maa_api.main``、不碰内核（M3 阶段
    尚未接内核，健康检查不能因为内核缺失而 500）。元数据缺失（源码目录直接跑、
    打包异常）不该让健康检查挂掉，所以这里吞掉任何取版本异常。
    """
    try:
        return version("maa-api")
    except Exception:  # noqa: BLE001 - 健康检查不得因元数据异常而 500
        return _FALLBACK_VERSION


class CoreHealth(BaseModel):
    """内核监督器的最小首页状态。"""

    state: str
    pid: int | None
    generation: int


class DeviceRetryHealth(BaseModel):
    """复用 ``DeviceManager.snapshot()`` 的重试状态。"""

    attempt: int
    max: int
    next_at: float | None


class DeviceHealth(BaseModel):
    """复用 ``DeviceManager.snapshot()`` 的设备状态。"""

    core_id: str = "default"
    state: str = "disconnected"
    address: str | None = None
    uuid: str | None = None
    resolution: dict[str, int] | None = None
    last_connected_at: float | None = None
    retry: DeviceRetryHealth
    last_error: dict[str, object] | None = None


class QueueHealth(BaseModel):
    """队列概览，不暴露流水线或任务条目。"""

    pending: int
    running: int
    paused: bool


class HealthResponse(BaseModel):
    """``GET /api/system/health`` 的响应体（docs/05 §6.1、§3.3）。

    内核（``core``）、设备（``device``）与队列（``queue``）只读取 lifespan 装配到
    ``app.state`` 的运行时对象。lifespan 尚未完成或没有装配对象时，这些字段为
    ``null``，健康探测仍返回 200。队列只包含计数与暂停状态，不暴露队列详情。

    ``started_at`` 按 docs/05 §3.3「尚未发生的字段返回 ``null`` 而不是省略键」，
    类型是 ``str | None``、默认 ``None``，响应里始终出现该键；写入方是 M3-09 的
    lifespan（``app.state.started_at``），本模块只负责读取。
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

    字段契约是 ``str | None``，但写入方是 M3-09 的 lifespan，本模块无法约束它写
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
        "返回服务自身的健康状态，供外部监控与容器健康检查探测。\n\n"
        "- **免鉴权**：即使配置了 `access_token` 也不校验（docs/05 §5.3），无需携带凭据\n"
        "- 只返回状态，不返回业务数据；`auth_enabled` 为 `false` 时前端直接跳过登录页\n"
        "- `auth_enabled` 由配置的 `access_token` 是否为空决定（docs/05 §5.2）\n"
        "- `version` 取已安装分发包元数据，读不到时回退 `0.1.0`\n"
        "- `started_at` 是进程启动时间（ISO 8601 字符串），尚未写入时为 `null`（docs/05 §3.3）\n"
        "- `core` 含内核状态、pid 和代际；`device` 复用设备快照；`queue` 只含待处理/运行数和暂停状态\n"
        "- lifespan 尚未装配运行态对象时，`core` / `device` / `queue` 为 `null`，仍返回 200\n"
        "- 不校验 token；队列概览只执行只读查询，不返回队列条目"
    ),
    response_model=HealthResponse,
)
async def health(request: Request) -> HealthResponse:
    """健康检查：从 app.state 读取运行态快照，不初始化运行时依赖。"""
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
        "用请求头里的 token 换取一枚 `HttpOnly; SameSite=Lax` 的 `maa_token` cookie。\n\n"
        "- 用途：浏览器 WebSocket 构造器不能自定义请求头（docs/05 §5.4），"
        "换取后握手自动携带 cookie，token 不进 URL 与访问日志\n"
        "- 副作用：下发 `Set-Cookie`，值即配置的 `access_token`、`Path=/`\n"
        "- 未带 `Secure` 属性：HTTPS 判定（`X-Forwarded-Proto`）归 M15\n"
        "- 免鉴权模式（未配置 `access_token`）下返回 `204` 但**不下发** cookie，"
        "此时没有可下发的秘密，WebSocket 握手也不校验\n"
        "- 写方法：仅凭 cookie 调用本端点同样 403（cookie 渠道收窄，docs/05 §5.1）\n"
        "- `401 UNAUTHORIZED`：缺少 token 或 token 不匹配；"
        "`403 FORBIDDEN`：token 仅来自 cookie\n"
        "- 响应无体（`204`），成功与否只看状态码"
    ),
    responses=error_responses("UNAUTHORIZED", "FORBIDDEN"),
    dependencies=[Depends(require_auth)],
)
def exchange_cookie(response: Response) -> None:
    """下发 ``maa_token`` cookie；免鉴权模式下无秘密可发，直接 204。"""
    token = get_settings().access_token
    if not token:
        return
    response.set_cookie(
        COOKIE_NAME,
        token,
        path="/",
        httponly=True,
        samesite="lax",
        secure=False,  # HTTPS 判定归 M15（docs/13 的 Tailscale Serve）
    )


@router.delete(
    "/auth/cookie",
    status_code=204,
    response_class=Response,
    summary="清除认证 cookie",
    description=(
        "清除 `maa_token` cookie，供前端「登出」时调用。\n\n"
        "- 为什么需要服务端端点：cookie 是 `HttpOnly`，前端 JavaScript 删不掉"
        "（docs/13 §4），不清理则登出后 WebSocket 仍能连上\n"
        "- 副作用：下发 `Set-Cookie`（空值 + `Max-Age=0`），浏览器随后删除该 cookie\n"
        "- 写方法，必须经头部 token 鉴权；仅凭 cookie 调用会 403\n"
        "- 免鉴权模式下无需凭据（本就没有 cookie 可清时调用也无害）\n"
        "- `401 UNAUTHORIZED`：缺少 token 或 token 不匹配；"
        "`403 FORBIDDEN`：token 仅来自 cookie\n"
        "- 响应无体（`204`）"
    ),
    responses=error_responses("UNAUTHORIZED", "FORBIDDEN"),
    dependencies=[Depends(require_auth)],
)
def clear_cookie(response: Response) -> None:
    """清除 ``maa_token`` cookie（``Max-Age=0``）。"""
    response.delete_cookie(COOKIE_NAME, path="/")
