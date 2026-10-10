"""应用装配：生命周期、路由注册、CORS、OpenAPI 元数据（docs/02 §6–§7，docs/05 §11）。

:mod:`maa_api` 的唯一装配入口（``uvicorn maa_api.main:app``），只做接线，不含业务逻辑。

:func:`lifespan` 按 docs/02 §7 的九步组织启动顺序。第 4 步启动 CoreSupervisor 不阻塞等待：
服务进入可用状态不依赖内核就绪，``/api/system/health`` 如实返回服务自身状态，内核加载与
设备连接都在后台进行。关闭按依赖关系倒序执行：取消 PipelineRunner → 关闭子进程 → 关闭
WebSocket 与日志 tailer → 刷新日志缓冲。用 ``lifespan`` 而不是废弃的启动事件钩子：后者在
fastapi 0.141 上仍可用但会发 deprecation 警告，且与「启动 / 关闭成对书写」的语义不匹配。

CORS（docs/05 §5.1）：旧装配把「通配符 origin + ``allow_credentials=True``」放在一起，
浏览器会直接拒绝（带凭据的响应不允许通配符 origin），等于旧配置实际失效。这里改为显式白
名单（本机 8002 两个回环写法）+ ``allow_origin_regex`` 覆盖 RFC 1918 局域网网段，保留
``allow_credentials=True``，方法/头也显式列出。默认白名单是模块级常量
（:data:`CORS_ORIGINS` / :data:`CORS_ORIGIN_REGEX`），局域网地址「可在设置页添加」属于
middleware 装配期的取舍：要么在 :func:`create_app` 之前读出白名单，要么换成按请求查设置的
动态 middleware。

OpenAPI（docs/05 §11）：``openapi_tags=TAGS`` 的 16 条分组顺序即 ``/docs`` 展示顺序，
``ws`` 条目保留用于说明协议与鉴权方式；``generate_unique_id_function=custom_operation_id``
把 operationId 统一成 ``{tag}_{函数名}``（typescript 客户端方法名依赖这条约定）；
``redirect_slashes=False`` 让所有路由不带尾斜杠，避免 307 干扰前端（docs/05 §2.5）。

日志统一用 stdlib :func:`logging.getLogger`；``maa_api.log`` 的 import 无副作用，lifespan 中
显式安装控制台、文件与 LogHub handler。
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import time
import uuid
from pathlib import Path
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from importlib.metadata import version as distribution_version
from types import SimpleNamespace
from typing import Any, Protocol

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx

from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import (
    account,
    agent,
    atomic,
    confirmations,
    device,
    knowledge,
    logs,
    notifications,
    pipelines,
    queue,
    questions,
    resources,
    screenshots,
    snippets,
    settings as settings_router,
    schedules,
    sources,
    system,
    tasks,
    updates,
)
from maa_api.api.ws import manager as ws_manager
from maa_api.api.ws import router as ws_router
from maa_api.db.migrate import ensure_schema
from maa_api.db.repositories.pipeline import PipelineRepository
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.log_hub import LogHub, current_request_id, set_log_hub
from maa_api.services.activity_index import ActivityIndex
from maa_api.services.api_snippet_service import ApiSnippetService
from maa_api.services.log_wiring import (
    install_core_logging,
    install_service_logging,
    start_core_debug_tailer,
    stop_logging,
)
from maa_api.settings import (
    REPO_ROOT,
    core_user_dir,
    get_settings,
    load_settings,
    set_settings,
)

__all__ = [
    "CORS_HEADERS",
    "CORS_EXPOSE_HEADERS",
    "CORS_METHODS",
    "CORS_ORIGIN_REGEX",
    "CORS_ORIGINS",
    "DESCRIPTION",
    "TAGS",
    "TITLE",
    "app",
    "create_app",
    "custom_operation_id",
    "lifespan",
    "request_trace_headers",
    "service_version",
    "warn_if_auth_disabled",
]

logger = logging.getLogger(__name__)


async def _wait_for_pipeline_idle(
    queue_service: Any,
    pipeline_runner: Any,
    session_factory: Any,
    core_id: str,
    *,
    target: Any = None,
    options: dict[str, Any] | None = None,
    confirmation_policy: Any = None,
    timeout_seconds: float = 30 * 60,
    poll_interval: float = 0.1,
) -> bool:
    """Drain queued work, then pause claims atomically before maintenance.

    The return value tells the caller whether this function paused the queue,
    so the caller can resume it after its update record reaches a terminal state.
    """
    opts = options or {}
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    cancel_requested = False
    changed_pause = False
    try:
        while True:
            if opts.get("force_interrupt") and not cancel_requested:
                if confirmation_policy is None:
                    raise AppError(
                        ErrorCode.FORBIDDEN,
                        "这次强制中断没有确认渠道，已拒绝；请在更新页手动重试",
                    )
                approved = confirmation_policy(target, dict(opts))
                if asyncio.iscoroutine(approved):
                    approved = await approved
                if not approved:
                    raise AppError(
                        ErrorCode.FORBIDDEN,
                        "强制中断没有获得确认，操作已取消；如确需中断，请在更新页确认后重试",
                    )
                async with session_factory() as session:
                    active_record = await PipelineRepository(session).current(core_id)
                if active_record is not None:
                    await pipeline_runner.request_cancel(active_record.id)
                cancel_requested = True

            active = getattr(pipeline_runner, "_active", None)
            op_locked = pipeline_runner.operation_lock.locked()
            async with session_factory() as session:
                repository = PipelineRepository(session)
                running_pipeline = await repository.current(core_id)
                pending_count = await repository.count_pending(core_id)
            idle = (
                active is None
                and running_pipeline is None
                and pending_count == 0
                and not op_locked
            )

            if idle and not queue_service.paused:
                changed_pause = await queue_service.pause()
                active = getattr(pipeline_runner, "_active", None)
                op_locked = pipeline_runner.operation_lock.locked()
                async with session_factory() as session:
                    repository = PipelineRepository(session)
                    running_pipeline = await repository.current(core_id)
                    pending_count = await repository.count_pending(core_id)
                idle = (
                    active is None
                    and running_pipeline is None
                    and pending_count == 0
                    and not op_locked
                )
                if idle:
                    return changed_pause
                if changed_pause:
                    await queue_service.resume()
                    changed_pause = False
            elif idle:
                return changed_pause

            if loop.time() >= deadline:
                raise AppError(
                    ErrorCode.UPDATE_QUEUE_BUSY_TIMEOUT,
                    "流水线一直没有空闲，等待超时；请稍后重试，或先停止正在执行的流水线",
                )
            await asyncio.sleep(poll_interval)
    except BaseException:
        if changed_pause:
            await queue_service.resume()
        raise


def confirm_manual_interrupt(_target: Any, options: dict[str, Any]) -> bool:
    """Treat explicit manual REST consent as confirmation; other callers fail closed.

    Agent/internal callers require an injected confirmation-policy callback.
    """
    return options.get("_caller") == "manual"


async def request_trace_headers(request: Request, call_next):
    """Attach request correlation metadata for responses and service logs."""
    request_id = request.headers.get("X-Request-Id", "").strip() or uuid.uuid4().hex
    started = time.perf_counter()
    token = current_request_id.set(request_id)
    try:
        failed = False
        try:
            response = await call_next(request)
        except Exception as exc:
            # Starlette's ServerErrorMiddleware handles uncaught exceptions outside
            # user middleware. Render its registered handler here while this request's
            # context is still active, so both its error log and response retain tracing.
            exception_handler = request.app.exception_handlers.get(Exception)
            if exception_handler is None:
                raise
            response = await exception_handler(request, exc)
            failed = True
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Response-Time-Ms"] = (
            f"{(time.perf_counter() - started) * 1000:.3f}"
        )
        if not failed:
            logger.info("HTTP %s %s completed", request.method, request.url.path)
        return response
    finally:
        current_request_id.reset(token)

#: 服务标题与描述；描述会原样进入 ``/openapi.json`` 的 ``info``。
TITLE = "MAA-API"
DESCRIPTION = (
    "MaaAssistantArknights 的 Web 服务。\n\n"
    "- 鉴权：单一 `access_token`，按优先级依次取 `Authorization: Bearer`、"
    "`X-Token`、`?token=`、cookie `maa_token`；未配置 `access_token` 时全部端点免鉴权\n"
    "- 错误体：所有非 2xx 响应都是 "
    '`{"error": {"code", "message", "details"}}`，`code` 是可枚举的错误码\n'
    "- 尾斜杠：全部路由不带尾斜杠，带尾斜杠返回 404\n"
    "- `ws` 分组只说明协议与鉴权方式，不能在调试台里直接调用"
)

#: 读不到分发元数据时的回退版本（与 pyproject.toml 一致）；与
#: :func:`maa_api.api.routers.system.health` 的取法相同：只读 importlib.metadata，
#: 不 import 内核，也不 import ``maa_api.main``（避免循环依赖）。
_FALLBACK_VERSION = "0.1.0"

#: ``openapi_tags``：与路由分组一一对应，顺序即 ``/docs`` 展示顺序（docs/05 §11.1 原文）。
TAGS: list[dict[str, str]] = [
    {"name": "system", "description": "健康检查、服务信息、日志查询"},
    {"name": "pipelines", "description": "流水线提交、查询与取消"},
    {"name": "queue", "description": "队列快照与优先级调整"},
    {"name": "tasks", "description": "任务类型与参数 schema"},
    {"name": "device", "description": "设备状态、重连与原子操作"},
    {"name": "core", "description": "内核状态与原生能力"},
    {"name": "screenshots", "description": "截图归档与读取"},
    {"name": "updates", "description": "内核、资源与游戏热更新"},
    {"name": "schedules", "description": "定时任务"},
    {"name": "settings", "description": "可视化配置"},
    {"name": "sources", "description": "外部数据源状态与缓存"},
    {"name": "account", "description": "账号数据（干员、仓库与养成计划）"},
    {"name": "notifications", "description": "多通道通知"},
    {"name": "resources", "description": "Copilot 作业、基建方案、自定义 task"},
    {"name": "snippets", "description": "API 调试台收藏请求"},
    {"name": "agent", "description": "工具清单、会话与审计"},
    {"name": "confirmations", "description": "高风险操作的人工确认"},
    {"name": "ws", "description": "WebSocket 实时通道（文档说明，不可调试）"},
]

#: 免鉴权启动日志的固定文案：前端与运维按它判断服务处于免鉴权模式（docs/05 §5.2）。
AUTH_DISABLED_WARNING = "未配置 access_token，API 处于免鉴权模式"

# ----------------------------------------------------------------------
# CORS（docs/05 §5.1）
# ----------------------------------------------------------------------

#: 显式 origin 白名单：默认只放本机前端的两个回环写法。
CORS_ORIGINS: list[str] = ["http://localhost:8002", "http://127.0.0.1:8002"]

#: 局域网 origin 正则（RFC 1918 三段私网地址 + 任意端口，http / https 都放行）：
#: 覆盖 ``192.168.*.*``、``10.*.*.*``、``172.16–31.*.*``。用 :func:`re.fullmatch`
#: 匹配整条 origin（starlette 的实现），所以不会误放行 ``http://192.168.1.1.evil.com``。
CORS_ORIGIN_REGEX = (
    r"https?://(?:"
    r"192\.168\.\d{1,3}\.\d{1,3}"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r")(?::\d{1,5})?"
)

def effective_cors_origins(configured: list[str] | None = None) -> list[str]:
    """默认白名单 + ``deployment.cors_origins`` 追加项（去重、保持顺序）。

    部署到 ``tailscale serve`` 后前端来源是 ``https://<host>.<tailnet>.ts.net``，
    它取决于 tailnet 名与主机名，只能由部署者配置，不能硬编码。
    """
    merged = list(CORS_ORIGINS)
    for origin in configured or []:
        candidate = origin.strip().rstrip("/")
        if not candidate or candidate in merged:
            continue
        merged.append(candidate)
    return merged


def effective_cors_origin_regex(configured: str = "") -> str:
    """局域网正则 + 可选的 ``deployment.cors_origin_regex``（两者取并集）。"""
    extra = (configured or "").strip()
    if not extra:
        return CORS_ORIGIN_REGEX
    return f"(?:{CORS_ORIGIN_REGEX})|(?:{extra})"


def resolve_forwarded_allow_ips(value: str | None = None) -> str:
    """uvicorn ``--forwarded-allow-ips`` 的取值。

    缺省只信任回环：``*`` 会让任何客户端都能伪造 ``X-Forwarded-For`` 与
    ``X-Forwarded-Proto``，既绕过按来源限流，也让 cookie 的 ``Secure`` 判定失真。
    """
    candidate = (value or "").strip()
    return candidate or "127.0.0.1"


#: 显式方法白名单（含 ``OPTIONS``：浏览器预检本身就是 OPTIONS）。
CORS_METHODS: list[str] = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

#: 显式请求头白名单：覆盖四渠道鉴权（``Authorization`` / ``X-Token``）、JSON 请求体与
#: 幂等键（docs/05 §10.1 的 ``Idempotency-Key``）。starlette 会自动并入 safelisted 头。
CORS_HEADERS: list[str] = [
    "Authorization",
    "X-Token",
    "X-Request-Id",
    "Content-Type",
    "Idempotency-Key",
]

#: Browser clients need to read the request correlation and elapsed time from
#: cross-origin responses; neither header is CORS-safelisted by default.
CORS_EXPOSE_HEADERS: list[str] = ["X-Request-Id", "X-Response-Time-Ms", "Location"]


class OperationIdRoute(Protocol):
    """``custom_operation_id`` 需要的最小路由契约。

    fastapi 0.141 的 ``generate_unique_id_function`` 对 ``@app.get`` 直挂的路由收到
    ``APIRoute``，对 ``include_router`` 进来的路由收到 ``_EffectiveRouteContext``
    包装对象（见 docs/ENVIRONMENT.md）：两者都有 ``name`` / ``tags``。
    因此这里按结构取属性，**不做 isinstance 判断、注解也不写死 ``APIRoute``**。
    """

    name: str
    tags: Sequence[str]


def custom_operation_id(route: OperationIdRoute) -> str:
    """operationId 规范化为 ``{tag}_{函数名}``（docs/05 §11.4）。

    没有 tag 的路由（内置的 ``/docs``、``/openapi.json`` 等）回退 ``default`` 前缀，
    保证任何一条路由都有稳定的 method 名，typescript 客户端生成不会撞名。
    """
    tag = route.tags[0] if route.tags else "default"
    return f"{tag}_{route.name}"


def service_version() -> str:
    """服务版本：读已安装分发包 ``maa-api`` 的元数据，失败回退 :data:`_FALLBACK_VERSION`。

    与 :func:`maa_api.api.routers.system.health` 同源（同一份元数据）；元数据缺失
    （源码目录直接跑）不该让应用构造失败，所以吞掉任何取版本异常。
    """
    try:
        return distribution_version("maa-api")
    except Exception:  # noqa: BLE001 - 版本只影响文档展示，不能阻断启动
        return _FALLBACK_VERSION


# ----------------------------------------------------------------------
# 生命周期（docs/02 §7）
# ----------------------------------------------------------------------


def warn_if_auth_disabled() -> None:
    """免鉴权模式时打印固定文案的 warning（docs/05 §5.2）。

    独立成函数以便单测：直接调用断言文案，不必依赖 ``TestClient`` 线程里的日志捕获。
    这条文案是前端与运维判断鉴权模式的依据，用测试钉住。
    """
    if not get_settings().access_token:
        logger.warning(AUTH_DISABLED_WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动 / 关闭序列（docs/02 §7）。

    启动（进入时）：

    1. 加载配置：``config.yaml`` → DB 覆盖项 → 环境变量
    2. 初始化数据库引擎并执行 Alembic 迁移到 head（:func:`ensure_schema`，失败即启动失败）
    3. 记录 ``app.state.started_at``（UTC ISO 8601 字符串， health 读它）
    4. 启动 LogHub 与日志来源
    5. 启动 MaaCore 子进程与 PipelineRunner；连接设备不阻塞启动

    关闭（退出时）：先收尾流水线，再关闭 MaaCore，最后刷新并关闭日志服务。
    """
    # 第 1 步：加载配置。整份配置的读侧入口是 maa_api.settings.get_settings()，
    # lifespan 只负责在启动时刷新一次进程内缓存（文件改动不会自动生效）。
    set_settings(load_settings())
    # 第 2 步：初始化数据库引擎并迁移到 head。ensure_schema 内部经 asyncio.to_thread
    # 执行阻塞的 alembic upgrade；失败原样抛出、中止启动（docs/04 §8.3）。
    await ensure_schema()

    # 设置服务在 schema 就绪后加载 DB 覆盖，并成为后续服务的配置提供者。
    import maa_api.db.session as db_session
    import maa_api.settings as settings_module
    from maa_api.services.setting_service import SettingService

    setting_service_factory = getattr(
        app.state, "setting_service_factory", SettingService
    )
    setting_service = setting_service_factory(
        db_session.session_factory,
        settings_path=settings_module.DEFAULT_CONFIG_PATH,
    )
    await setting_service.refresh()
    app.state.setting_service = setting_service
    # 免鉴权模式必须显式可见，且应反映 DB 覆盖后的有效配置。
    warn_if_auth_disabled()

    # 第 9 步的可见结果：进程启动时间写入 app.state，health 端点读取它。
    app.state.started_at = datetime.now(timezone.utc).isoformat()

    # 第 3 步：启动 LogHub、Python logging handler 与 MaaCore debug 日志 tail。
    # tailer 任务只建立任务，不等待子进程，也不要求 asst.log 已存在。
    hub = LogHub()
    await hub.start()
    app.state.log_hub = hub
    set_log_hub(hub)
    tailer_task = None
    try:
        install_service_logging(hub)
        tailer_task = start_core_debug_tailer(hub)
    except BaseException:
        await stop_logging(tailer_task, hub)
        del app.state.log_hub
        raise

    # 第 4–6 步：装配 MaaCore、设备管理与单消费者流水线执行器。
    core_supervisor = None
    core_client = None
    device_manager = None
    pipeline_runner = None
    update_service = None
    game_update_service = None
    update_http_client = None
    update_scheduler = None
    schedule_service = None
    resource_service = None
    confirmation_service = None
    question_service = None
    agent_runtime = None
    incident_service = None
    device_start_task: asyncio.Task[Any] | None = None
    device_start_generation: int | None = None
    lifecycle_tasks: set[asyncio.Task[Any]] = set()
    shutdown_started = False

    def track_lifecycle_task(coroutine: Any, *, name: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine, name=name)
        lifecycle_tasks.add(task)

        def forget(completed: asyncio.Task[Any]) -> None:
            lifecycle_tasks.discard(completed)
            if not completed.cancelled():
                error = completed.exception()
                if error is not None:
                    logger.warning(
                        "生命周期后台任务失败：%s：%s",
                        completed.get_name(),
                        type(error).__name__,
                    )

        task.add_done_callback(forget)
        return task
    try:
        from maa_api.core.client import CoreClient
        from maa_api.core.enums import Message
        from maa_api.core.registry import DEFAULT_CORE_ID, CoreRegistry
        from maa_api.core.supervisor import CoreState, CoreSupervisor
        from maa_api.services.device_service import DeviceManager
        from maa_api.services.pipeline_runner import PipelineRunner
        from maa_api.services.queue_service import QueueService
        from maa_api.services.resource_service import ResourceService

        current_settings = get_settings()
        system_name = platform.system()
        maa_folder = {"Darwin": "Darwin", "Linux": "Linux", "Windows": "Win32"}.get(
            system_name, system_name
        )
        configured_core_path = Path(current_settings.maa_core_path).expanduser()
        maa_path = (
            configured_core_path
            if current_settings.maa_core_path
            else REPO_ROOT / "resource" / "lib" / "maa" / maa_folder
        )
        if not maa_path.is_absolute():
            maa_path = (REPO_ROOT / maa_path).resolve()
        user_dir = core_user_dir()
        layers_root = REPO_ROOT / "resource" / "maa-layers"
        layers_root.mkdir(parents=True, exist_ok=True)
        boot_config = {
            "maa_path": str(maa_path),
            "user_dir": str(user_dir),
            # MaaCore's packaged resource is loaded first by Asst.load(). The repo
            # overlay is already copied into that tree; only OTA/custom remain additive.
            "incremental_paths": [
                str(layers_root / "cache"),
                str(layers_root / "custom"),
            ],
            "instance_options": {},
            "asst_factory": "maa_api.core.asst:Asst",
            "asst_factory_kwargs": {},
        }
        loop = asyncio.get_running_loop()
        runtime: dict[str, Any] = {}

        def on_core_crash(record: dict[str, Any]) -> None:
            supervisor = runtime.get("supervisor")
            generation = record.get("generation")
            if not isinstance(generation, int):
                generation = getattr(supervisor, "generation", None)
            if generation is None:
                return
            crash_record = dict(record)
            crash_record["generation"] = int(generation)

            def dispatch_core_crash() -> None:
                if shutdown_started:
                    return
                runner = runtime.get("runner")
                if runner is not None:
                    runner.notify_core_crash(crash_record)
                    if (
                        getattr(runner, "active_pipeline_id", None) is not None
                        and getattr(runner, "active_pipeline_epoch", None)
                        == int(generation)
                    ):
                        return
                incidents = runtime.get("incidents")
                if incidents is not None:
                    track_lifecycle_task(
                        incidents.core_crashed(int(generation), crash_record),
                        name=f"agent-core-incident-{DEFAULT_CORE_ID}-{generation}",
                    )

            try:
                # CoreSupervisor invokes this synchronous hook from monitor
                # threads. Marshal all state/DB work to the app loop and track
                # the task so lifespan shutdown can close it deterministically.
                loop.call_soon_threadsafe(dispatch_core_crash)
            except RuntimeError:
                logger.debug("应用事件循环已关闭，忽略 MaaCore 崩溃事件")

        def publish_core_state(state: Any) -> None:
            core = runtime.get("supervisor")
            value = getattr(state, "value", str(state))
            if core is not None:
                ws_manager.broadcast(
                    "core_status",
                    {
                        "core_id": DEFAULT_CORE_ID,
                        "state": value,
                        "pid": core.pid,
                        "generation": core.generation,
                    },
                )
            runner = runtime.get("runner")
            if runner is not None:
                runner.notify_core_state(state)
            if value == CoreState.READY.value:
                schedule_connection()
                confirm_loaded = runtime.get("confirm_resource_load")
                if confirm_loaded is not None:
                    task = asyncio.create_task(confirm_loaded())
                    lifecycle_tasks.add(task)
                    task.add_done_callback(lifecycle_tasks.discard)
            elif device_start_task is not None and not device_start_task.done():
                device_start_task.cancel()

        def on_core_state_change(state: Any) -> None:
            try:
                loop.call_soon_threadsafe(publish_core_state, state)
            except RuntimeError:
                logger.debug("应用事件循环已关闭，忽略 MaaCore 状态更新")

        supervisor_factory = getattr(
            app.state, "core_supervisor_factory", CoreSupervisor
        )
        core_supervisor = supervisor_factory(
            boot_config,
            on_crash=on_core_crash,
            on_state_change=on_core_state_change,
        )
        runtime["supervisor"] = core_supervisor
        client_factory = getattr(app.state, "core_client_factory", CoreClient)
        core_client = client_factory(
            core_supervisor,
            screencap_dir=REPO_ROOT / "resource" / "temp" / "screencap",
        )
        core_registry = CoreRegistry()
        core_registry.register(DEFAULT_CORE_ID, core_client)

        def publish_device_status(_message_type: str, data: dict[str, Any]) -> None:
            ws_manager.broadcast("device_status", data)

        device_manager_factory = getattr(
            app.state, "device_manager_factory", DeviceManager
        )
        device_manager = device_manager_factory(
            lambda: get_settings(),
            core_client,
            broadcast=publish_device_status,
            core_id=DEFAULT_CORE_ID,
        )
        setting_service.bind_device_manager(device_manager)

        def on_core_callback(payload: dict[str, Any]) -> None:
            if not isinstance(payload, dict):
                return
            details = payload.get("details")
            if not isinstance(details, dict):
                details = {}
            try:
                message = Message(int(payload.get("msg")))
            except (TypeError, ValueError):
                return
            if message is Message.ConnectionInfo:
                device_manager.on_core_connection_event(
                    str(details.get("what", "")), details
                )

        core_client.on("CALLBACK", on_core_callback)

        queue_service = QueueService(db_session.session_factory)
        resource_service = ResourceService(
            db_session.session_factory,
            resource_root=REPO_ROOT / "resource",
            reload_resources=lambda: core_client._send(
                "LOAD_RESOURCE",
                {
                    "path": str(maa_path),
                    "incremental_paths": boot_config["incremental_paths"],
                },
            ),
        )
        app.state.resource_service = resource_service
        runner_factory = getattr(app.state, "pipeline_runner_factory", PipelineRunner)
        pipeline_runner = runner_factory(
            core_registry,
            core_supervisor,
            db_session.session_factory,
            queue_service=queue_service,
            device_manager=device_manager,
            resource_service=resource_service,
            broadcast=lambda message_type, data: ws_manager.broadcast(
                message_type, data
            ),
        )
        runtime["runner"] = pipeline_runner
        queue_service.bind_runner(
            pipeline_runner.wake,
            operation_lock=pipeline_runner.operation_lock,
        )
        install_core_logging(
            core_client,
            hub,
            context_provider=pipeline_runner.log_context,
        )
        app.state.core_supervisor = core_supervisor
        app.state.core_client = core_client
        app.state.core_registry = core_registry
        app.state.device_manager = device_manager
        app.state.pipeline_runner = pipeline_runner
        app.state.queue_service = queue_service

        def schedule_connection() -> None:
            """Retry device connection asynchronously for each READY generation."""
            nonlocal device_start_task, device_start_generation
            if shutdown_started or core_supervisor.state is not CoreState.READY:
                return
            generation = core_supervisor.generation
            if (
                device_start_task is not None
                and not device_start_task.done()
                and device_start_generation == generation
            ):
                return
            previous_task = device_start_task
            is_initial_generation = device_start_generation is None

            async def connect_on_startup() -> None:
                try:
                    if previous_task is not None and not previous_task.done():
                        previous_task.cancel()
                        await asyncio.gather(previous_task, return_exceptions=True)
                    if is_initial_generation:
                        # DeviceManager owns the documented 60 × 5 second
                        # startup retry defaults; the API lifespan never waits.
                        await device_manager.connect_with_retry(reason="startup")
                    else:
                        await device_manager.connect_with_retry(
                            attempts=getattr(
                                device_manager, "reconnect_retry_attempts", 5
                            ),
                            interval=0,
                            reason="reconnect",
                        )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "MaaCore generation %s startup device retries failed",
                        generation,
                    )
                finally:
                    pipeline_runner.notify_core_state(core_supervisor.state)

            device_start_generation = generation
            device_start_task = asyncio.create_task(
                connect_on_startup(), name=f"maa-device-startup-{generation}"
            )
            lifecycle_tasks.add(device_start_task)
            device_start_task.add_done_callback(lifecycle_tasks.discard)

        # Update workflows share a lifespan-owned HTTP client and the exact
        # DeviceManager safe APK installer. Importing their modules is side-effect free.
        from maa_api.services.core_update import CoreUpdateWorkflow
        from maa_api.services.game_update import GameUpdateService
        from maa_api.services.notify_service import NotifyService
        from maa_api.services.push_subscription_service import (
            PushSubscriptionService,
        )
        from maa_api.services.webpush_service import WebPushService
        from maa_api.services.resource_update import ResourceUpdateWorkflow
        from maa_api.services.retention_service import (
            RETENTION_CRON,
            STARTUP_DELAY_SECONDS,
            run_retention,
        )
        from maa_api.services.update_service import UpdateService

        current_settings = get_settings()
        update_http_client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=30.0,
            proxy=current_settings.proxy or None,
        )
        update_temp = REPO_ROOT / "resource" / "temp" / "updates"
        update_temp.mkdir(parents=True, exist_ok=True)

        async def reconnect_after_core_change(client: Any) -> bool:
            del client  # DeviceManager reconnects through the current CoreClient.
            return await device_manager.connect_with_retry(
                attempts=getattr(device_manager, "reconnect_retry_attempts", 5),
                interval=0,
                reason="reconnect",
            )

        async def wait_for_pipeline_idle(
            target: Any = None, options: dict[str, Any] | None = None
        ) -> None:
            # Explicit force interruption converges on the injectable policy
            # boundary; only a permitted action reaches PipelineRunner.
            changed_pause = await _wait_for_pipeline_idle(
                queue_service,
                pipeline_runner,
                db_session.session_factory,
                DEFAULT_CORE_ID,
                target=target,
                options=options,
                confirmation_policy=getattr(
                    app.state,
                    "update_interrupt_confirmation_policy",
                    confirm_manual_interrupt,
                ),
            )
            if changed_pause and update_service is not None:
                async def resume_queue_after_update() -> None:
                    try:
                        while update_service._lock.locked():
                            await asyncio.sleep(0.1)
                    finally:
                        await queue_service.resume()

                task = asyncio.create_task(
                    resume_queue_after_update(), name="maa-update-queue-resume"
                )
                lifecycle_tasks.add(task)
                task.add_done_callback(lifecycle_tasks.discard)
            elif changed_pause:
                await queue_service.resume()

        resource_workflow = ResourceUpdateWorkflow(
            http_client=update_http_client,
            maa_path=maa_path,
            layers_root=layers_root,
            temp_root=REPO_ROOT / "resource" / "temp" / "resource-update",
            download_prefix=current_settings.updates.download_prefix or None,
            restart_core=lambda: _restart_core_and_reconnect(),
            reload_resources=lambda: core_client._send(
                "LOAD_RESOURCE",
                {
                    "path": str(maa_path),
                    "incremental_paths": boot_config["incremental_paths"],
                },
            ),
            wait_for_idle=wait_for_pipeline_idle,
        )

        # The first MaaCore resource tree is the packaged baseline. If a repo
        # layer exists, merge it before any process can load resources.
        original_reapply = resource_workflow.reapply_overlay

        def safe_reapply_overlay(core_path: Path | str | None = None) -> None:
            repo_resource = layers_root / "repo" / "resource"
            if repo_resource.is_dir():
                original_reapply(core_path or maa_path)

        resource_workflow.reapply_overlay = safe_reapply_overlay
        safe_reapply_overlay(maa_path)

        async def _restart_core_and_reconnect() -> None:
            async with core_supervisor.acquire_maintenance():
                await core_supervisor.restart()
            await reconnect_after_core_change(core_client)

        core_workflow = CoreUpdateWorkflow(
            http_client=update_http_client,
            target_path=maa_path,
            temp_root=(
                update_temp / "core"
                if (update_temp / "core").parent.stat().st_dev
                == maa_path.parent.stat().st_dev
                else maa_path.parent / f".{maa_path.name}-maa-api-core-update"
            ),
            supervisor=core_supervisor,
            core_client=core_client,
            reconnect=reconnect_after_core_change,
            ready_version=lambda client: client.get_version(),
            current_version=lambda client: client.get_version(),
            before_start=resource_workflow.reapply_overlay,
            download_prefix=current_settings.updates.download_prefix or None,
        )
        game_update_service = GameUpdateService(
            device_manager,
            # Production DeviceManager always provides the safe installer;
            # lifecycle tests may substitute a manager with no APK surface.
            installer=getattr(device_manager, "install_apk_safe", None),
            http_client=update_http_client,
        )
        notify_service = NotifyService(db_session.session_factory)
        api_snippet_service = ApiSnippetService(db_session.session_factory)
        push_subscription_service = PushSubscriptionService(
            db_session.session_factory
        )
        webpush_service = WebPushService(push_subscription_service)

        async def resource_layers_loaded() -> str | None:
            if getattr(core_supervisor.state, "value", core_supervisor.state) != "ready":
                return None
            return f"{os.getpid()}:{core_supervisor.generation}"

        async def authorize_preparation(target: Any, options: dict[str, Any]) -> None:
            await wait_for_pipeline_idle(target, options)

        update_service_factory = getattr(app.state, "update_service_factory", UpdateService)
        update_service = update_service_factory(
            db_session.session_factory,
            core_workflow=core_workflow,
            resource_workflow=resource_workflow,
            game_workflow=game_update_service,
            broadcast=lambda kind, data: ws_manager.broadcast(kind, data),
            notify=notify_service,
            prepare_update=authorize_preparation,
            resource_layers_loaded=resource_layers_loaded,
            temp_root=update_temp,
        )
        app.state.update_service = update_service
        app.state.notify_service = notify_service
        # 流水线终态通知（docs/09 §13.1 的保底层）：执行器只负责在终态触发，
        # 发给谁由通道配置决定。注入是后置的，因为 runner 先于 NotifyService 构造。
        bind_notify_service = getattr(pipeline_runner, "bind_notify_service", None)
        if callable(bind_notify_service):
            bind_notify_service(notify_service)
        # 养成计划（M27）：今天的流水线与每日定时任务在确认之后由计划服务提交；流水线
        # 进终态时重算进度（规格 §7.7）。导入放在用它的分支里：账号服务的装配在另一个
        # 条件分支（没有数据库时整段跳过），模块级导入在这里可能还没绑定。
        from maa_api.services.account.plans import UpgradePlanService

        upgrade_plans = UpgradePlanService(
            db_session.session_factory,
            pipeline_service=pipeline_runner,
            schedule_service=app.state.schedule_service
            if hasattr(app.state, "schedule_service")
            else None,
            notify_service=notify_service,
            # 账号服务在本函数后面才装配（通知绑定更早）；拿不到就由计划服务按会话工厂
            # 现建，语义一致。
            box=getattr(app.state, "box_service", None),
            depot=getattr(app.state, "depot_service", None),
        )
        app.state.upgrade_plan_service = upgrade_plans
        bind_finish_hook = getattr(pipeline_runner, "bind_finish_hook", None)
        if callable(bind_finish_hook):
            bind_finish_hook(upgrade_plans.on_pipeline_finished)
        # 任务终态留档截图的日志附件（docs/12 §3.10）：stdlib 日志渠道只搬运
        # pipeline/task/request 上下文，**不搬运 attachment**，附件只能走 hub 的
        # ``offer``，所以这里把 hub 后置注入给 runner（同 bind_notify_service）。
        bind_log_hub = getattr(pipeline_runner, "bind_log_hub", None)
        if callable(bind_log_hub):
            bind_log_hub(hub)
        app.state.push_subscription_service = push_subscription_service
        app.state.webpush_service = webpush_service
        app.state.api_snippet_service = api_snippet_service
        app.state.game_update_service = game_update_service
        await update_service.recover_interrupted()

        from maa_api.agent.confirmation import ConfirmationService
        from maa_api.agent.knowledge import KnowledgeService
        from maa_api.agent.policy import PolicyEngine
        from maa_api.agent.tools import build_registry
        from maa_api.services.agent_ops_service import AgentOpsService

        agent_ops_service = AgentOpsService(
            core_supervisor=core_supervisor,
            update_service=update_service,
            queue_service=queue_service,
            pipeline_runner=pipeline_runner,
            session_factory=db_session.session_factory,
            reconnect=lambda: device_manager.connect_with_retry(
                attempts=getattr(device_manager, "reconnect_retry_attempts", 5),
                interval=0,
                reason="reconnect",
            ),
        )
        app.state.agent_ops_service = agent_ops_service
        # 活动索引：读本机活动资源的只读门面。工具与知识库注入段共用同一个实例，
        # 签名缓存因此只需维护一份（M24）。
        activity_index = ActivityIndex()
        app.state.activity_index = activity_index
        # 知识库：构造时把随代码发布的内置条目加载一次（文件坏了要在启动处就炸），
        # 用户改动存在 agent_knowledge 表里。runtime 与本路由共用这一个实例。
        # 账号数据（M27）：干员表、仓库、养成规划与计划。工具、REST 与注入段共用这些
        # 实例；识别结果由回调采集服务写入（见 AccountCaptureService）。
        from maa_api.services.account.box import OperatorBoxService
        from maa_api.services.account.capture import AccountCaptureService
        from maa_api.services.account.depot import DepotService
        from maa_api.services.account.plans import UpgradePlanService
        from maa_api.services.account.summary import AccountSummaryReader

        box_service = OperatorBoxService(db_session.session_factory)
        depot_service = DepotService(db_session.session_factory)
        app.state.box_service = box_service
        app.state.depot_service = depot_service
        app.state.account_summary = AccountSummaryReader(
            box=box_service, depot=depot_service
        )
        account_capture = AccountCaptureService(
            db_session.session_factory,
            box_service=box_service,
            depot_service=depot_service,
        )
        app.state.account_capture = account_capture
        # 当期卡池（M28）：注入段用；外服不注入（进度与国服不同），由卡池服务自己判。
        from maa_api.services.game.gacha import GachaService

        app.state.gacha_service = GachaService(
            settings_provider=lambda: get_settings()
        )
        # 集成战略（M28）：按章节取主题页，供 get_roguelike_info 使用。
        from maa_api.services.game.roguelike import RoguelikeService

        app.state.roguelike_service = RoguelikeService()
        app.state.knowledge_service = KnowledgeService(
            db_session.session_factory,
            activity_index=activity_index,
            account_reader=app.state.account_summary,
            gacha_reader=app.state.gacha_service,
        )
        confirmation_factory = getattr(
            app.state, "confirmation_service_factory", ConfirmationService
        )
        confirmation_service = confirmation_factory(
            db_session.session_factory,
            build_registry(),
            policy=PolicyEngine(
                atomic_grant_minutes=current_settings.agent.atomic_grant_minutes
            ),
            broadcast=lambda message_type, data: ws_manager.broadcast(message_type, data),
            notify=notify_service,
            push=webpush_service,
            # 只有「用户没在看页面」时才值得推送（docs/09 §13.1 第 2 步）。
            # 用 has_visible_client 而不是「有没有连接」：后台标签页的 WebSocket
            # 往往还活着，按连接判断会导致该推送的时候不推送。
            has_active_client=ws_manager.has_visible_client,
            confirmation_timeout_seconds=current_settings.agent.confirmation_timeout_seconds,
            grant_confirmation_timeout_seconds=(
                current_settings.agent.grant_confirmation_timeout_seconds
            ),
        )
        app.state.confirmation_service = confirmation_service
        await confirmation_service.start()

        from maa_api.agent.incidents import AgentIncidentService
        from maa_api.agent.questions import QuestionService
        from maa_api.agent.runtime import AgentRuntime, call_runtime_factory

        # 提问与确认是两个独立的交互（规格 §2.2）：独立的表、独立的服务、
        # 独立的超时配置，这里只是并排装配。question_service_factory 与
        # confirmation_service_factory 同一用途：测试/冒烟脚本可替换成替身。
        question_factory = getattr(
            app.state, "question_service_factory", QuestionService
        )
        question_service = question_factory(
            db_session.session_factory,
            broadcast=lambda message_type, data: ws_manager.broadcast(message_type, data),
            timeout_seconds=current_settings.agent.question_timeout_seconds,
        )
        app.state.question_service = question_service
        # start() 内部已经清理过一次重启遗留（幂等），这里显式写出来是为了让两个
        # 服务的启动顺序在 lifespan 里一眼可见：先清遗留，再起扫描。
        await question_service.reset_after_restart()
        await question_service.start()

        agent_runtime_factory = getattr(app.state, "agent_runtime_factory", None)
        agent_runtime = (
            call_runtime_factory(
                agent_runtime_factory, confirmation_service, question_service
            )
            if agent_runtime_factory is not None
            else AgentRuntime(
                db_session.session_factory,
                confirmation_service,
                question_service=question_service,
            )
        )
        app.state.agent_runtime = agent_runtime
        incident_factory = getattr(app.state, "agent_incident_service_factory", None)
        incident_options = {
            "settings_provider": setting_service.refresh,
            "pipeline_runner": pipeline_runner,
            "broadcast": lambda message_type, data: ws_manager.broadcast(
                message_type, data
            ),
            "request": SimpleNamespace(app=app),
            "core_id": DEFAULT_CORE_ID,
        }
        incident_service = (
            incident_factory(db_session.session_factory, agent_runtime, **incident_options)
            if incident_factory is not None
            else AgentIncidentService(
                db_session.session_factory, agent_runtime, **incident_options
            )
        )
        app.state.agent_incident_service = incident_service
        runtime["incidents"] = incident_service
        bind_incident_service = getattr(pipeline_runner, "bind_incident_service", None)
        if callable(bind_incident_service):
            bind_incident_service(incident_service)

        # A successful READY means the worker loaded the base and each present
        # incremental layer. Let UpdateService clear a deferred reload marker.
        async def confirm_resource_load() -> None:
            service = runtime.get("update_service")
            if service is not None:
                await service._get_reload_pending()

        runtime["update_service"] = update_service
        runtime["confirm_resource_load"] = confirm_resource_load

        core_client.start_consumer()
        await pipeline_runner.start()
        for recovered_pipeline_id in getattr(
            pipeline_runner, "recovered_pipeline_ids", ()
        ):
            schedule_incident = getattr(pipeline_runner, "schedule_pipeline_incident", None)
            if callable(schedule_incident):
                schedule_incident(recovered_pipeline_id)
            else:
                track_lifecycle_task(
                    incident_service.pipeline_failed(recovered_pipeline_id),
                    name=f"agent-recovered-pipeline-incident-{recovered_pipeline_id}",
                )
        try:
            await core_supervisor.start(wait_ready=False)
        except Exception:  # noqa: BLE001 - service stays available while core recovers
            logger.exception("MaaCore 子进程启动失败；HTTP 服务继续启动")
        if core_supervisor.state is CoreState.READY:
            schedule_connection()
            await confirm_resource_load()

        # One local-time scheduler owns both daily availability checks and the
        # existing retention jobs. Checks never download or block pipeline work.
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger

        async def check_updates_at_configured_hour() -> None:
            # Poll the active setting each hour so changing updates.check_hour
            # takes effect without requiring an application restart. The body
            # still runs at most once daily for the selected local hour.
            local_now = datetime.now().astimezone()
            if local_now.hour == get_settings().updates.check_hour:
                await update_service.daily_check()

        update_scheduler = AsyncIOScheduler()
        update_scheduler.add_job(
            check_updates_at_configured_hour,
            CronTrigger(minute=0),
            id="maa-update-daily-check",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        update_scheduler.add_job(
            run_retention,
            CronTrigger.from_crontab(RETENTION_CRON),
            kwargs={"session_factory": db_session.session_factory},
            id="maa-retention-daily",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        update_scheduler.add_job(
            run_retention,
            "date",
            run_date=datetime.now(timezone.utc)
            + timedelta(seconds=STARTUP_DELAY_SECONDS),
            kwargs={"session_factory": db_session.session_factory},
            id="maa-retention-startup",
            replace_existing=True,
        )
        update_scheduler.start()
        app.state.update_scheduler = update_scheduler
        from maa_api.services.schedule_service import ScheduleService

        schedule_service_factory = getattr(
            app.state, "schedule_service_factory", ScheduleService
        )
        schedule_service = schedule_service_factory(
            db_session.session_factory,
            update_scheduler,
            queue_service,
            pipeline_runner,
        )
        await schedule_service.load_enabled()
        app.state.schedule_service = schedule_service
    except BaseException:
        shutdown_started = True
        if question_service is not None:
            await question_service.close()
        if confirmation_service is not None:
            await confirmation_service.close()
        if schedule_service is not None:
            await schedule_service.close()
        if update_scheduler is not None and update_scheduler.running:
            update_scheduler.shutdown(wait=False)
        if update_service is not None:
            pending_updates = list(getattr(update_service, "_tasks", {}).values())
            if pending_updates:
                await asyncio.gather(*pending_updates, return_exceptions=True)
        if game_update_service is not None:
            await game_update_service.aclose()
        if update_http_client is not None:
            await update_http_client.aclose()
        if device_start_task is not None and not device_start_task.done():
            device_start_task.cancel()
            await asyncio.gather(device_start_task, return_exceptions=True)
        if device_manager is not None:
            await device_manager.close()
        for task in tuple(lifecycle_tasks):
            task.cancel()
        if lifecycle_tasks:
            await asyncio.gather(*lifecycle_tasks, return_exceptions=True)
        if pipeline_runner is not None:
            await pipeline_runner.stop()
        if core_supervisor is not None:
            try:
                await core_supervisor.stop()
            except Exception:
                logger.exception("清理启动失败的 MaaCore 子进程时出错")
        if core_client is not None:
            core_client.close()
        if hasattr(app.state, "setting_service"):
            del app.state.setting_service
        for attribute in (
            "update_service",
            "notify_service",
            "api_snippet_service",
            "game_update_service",
            "schedule_service",
            "update_scheduler",
            "confirmation_service",
            "question_service",
            "agent_runtime",
            "agent_incident_service",
            "agent_ops_service",
            "resource_service",
        ):
            if hasattr(app.state, attribute):
                delattr(app.state, attribute)
        await stop_logging(tailer_task, hub)
        raise

    # 第 6 步通过 CoreSupervisor READY hook 异步启动 DeviceManager 的 startup retries。
    # 第 7 步：从 DB 装载定时任务，启动 APScheduler
    # 第 8 步：注册 ToolRegistry，并装配 Agent 工具服务。

    # 第 9 步：yield 之后 uvicorn 才开始接受 HTTP 请求；子进程 READY 与设备连接
    # 均在后台发生，不阻塞 HTTP 服务的启动。
    try:
        yield
    finally:
        # 关闭顺序：停止消费 / 收尾当前任务 → 关闭子进程 → 关闭 WS/tailer → 刷盘。
        shutdown_started = True
        if question_service is not None:
            await question_service.close()
        if confirmation_service is not None:
            await confirmation_service.close()
        if schedule_service is not None:
            await schedule_service.close()
        if update_scheduler is not None and update_scheduler.running:
            update_scheduler.shutdown(wait=False)
        if update_service is not None:
            pending_updates = list(getattr(update_service, "_tasks", {}).values())
            if pending_updates:
                await asyncio.gather(*pending_updates, return_exceptions=True)
        if device_start_task is not None and not device_start_task.done():
            device_start_task.cancel()
            await asyncio.gather(device_start_task, return_exceptions=True)
        if device_manager is not None:
            await device_manager.close()
        for task in tuple(lifecycle_tasks):
            task.cancel()
        if lifecycle_tasks:
            await asyncio.gather(*lifecycle_tasks, return_exceptions=True)
        if pipeline_runner is not None:
            await pipeline_runner.stop()
        if core_supervisor is not None:
            try:
                await core_supervisor.stop()
            except Exception:
                logger.exception("关闭 MaaCore 子进程失败")
        if core_client is not None:
            core_client.close()
        if game_update_service is not None:
            await game_update_service.aclose()
        if update_http_client is not None:
            await update_http_client.aclose()
        await stop_logging(tailer_task, hub)
        import maa_api.db.session as db_session

        await db_session.engine.dispose()
        for attribute in (
            "core_supervisor",
            "core_client",
            "core_registry",
            "device_manager",
            "pipeline_runner",
            "queue_service",
            "setting_service",
            "log_hub",
            "update_service",
            "notify_service",
            "game_update_service",
            "schedule_service",
            "update_scheduler",
            "confirmation_service",
            "question_service",
            "agent_runtime",
            "agent_incident_service",
            "agent_ops_service",
            "resource_service",
        ):
            if hasattr(app.state, attribute):
                delattr(app.state, attribute)


# ----------------------------------------------------------------------
# 应用装配
# ----------------------------------------------------------------------

# ``static/`` is generated by the Vite build and the process may start from any
# working directory (for example, under systemd or a container entrypoint).
STATIC_PATH = Path(__file__).resolve().parents[1] / "static"


def create_app() -> FastAPI:
    """构造应用：OpenAPI 元数据 → 异常处理器 → 路由 → CORS。

    工厂与模块级 :data:`app` 并存：verify 与 uvicorn 依赖 ``maa_api.main:app``，
    测试需要一份独立实例时可以直接再调 :func:`create_app`（它没有全局副作用）。
    """
    app = FastAPI(
        title=TITLE,
        version=service_version(),
        description=DESCRIPTION,
        openapi_tags=TAGS,
        generate_unique_id_function=custom_operation_id,
        redirect_slashes=False,
        lifespan=lifespan,
    )

    # 统一错误体与四个异常处理器：必须发生在第一个请求之前，注册本身幂等。
    register_exception_handlers(app)

    # 路由：前缀与 tag 都写在各自模块里，这里只 include。
    # 旧 router/adb、router/maa、router/template 不注册；
    # 旧 static 挂载也不做：SPA catch-all 服务前端路由。
    app.include_router(system.router)
    app.include_router(tasks.router)
    app.include_router(device.router)
    app.include_router(settings_router.router)
    app.include_router(pipelines.router)
    app.include_router(queue.router)
    app.include_router(resources.router)
    app.include_router(atomic.router)
    app.include_router(logs.router)
    app.include_router(screenshots.router)
    app.include_router(updates.router)
    app.include_router(notifications.router)
    app.include_router(schedules.router)
    app.include_router(snippets.router)
    app.include_router(agent.router)
    app.include_router(knowledge.router)
    app.include_router(sources.router)
    app.include_router(account.router)
    app.include_router(confirmations.router)
    app.include_router(questions.router)
    app.include_router(ws_router)

    # Frontend assets are generated by ``vite build``. Keep this low-priority
    # route group after the API/WebSocket routes, and permit app construction
    # before the frontend build has populated ``static/``.
    app.frontend("/", directory=STATIC_PATH, fallback="index.html", check_dir=False)

    @app.middleware("http")
    async def frontend_cache_headers(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if (
            path == "/api"
            or path.startswith("/api/")
        ) and response.status_code < 400 and response.headers.get(
            "content-type", ""
        ).startswith("text/html"):
            # app.frontend is intentionally a low-priority route, but browser navigation
            # accepts HTML and can therefore reach the SPA fallback for an unknown API
            # path. Reserved API paths must remain JSON 404s even with that Accept.
            return JSONResponse(
                status_code=404,
                content={
                    "error": {
                        "code": "NOT_FOUND",
                        "message": "请求的 API 路由不存在",
                    }
                },
            )
        if path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif (
            response.headers.get("content-type", "").startswith("text/html")
            or path == "/"
            or path.endswith(".html")
            or path.endswith(("/sw.js", "/manifest.webmanifest"))
        ):
            response.headers["Cache-Control"] = "no-cache"
        return response

    # CORS：显式白名单 + 局域网正则 + 部署附加项，保留 allow_credentials=True
    # （docs/05 §5.1）。部署项来自 settings，`tailscale serve` 的 *.ts.net 域名
    # 只能在部署时确定。
    cors_settings = get_settings().deployment
    app.add_middleware(
        CORSMiddleware,
        allow_origins=effective_cors_origins(cors_settings.cors_origins),
        allow_origin_regex=effective_cors_origin_regex(cors_settings.cors_origin_regex),
        allow_credentials=True,
        allow_methods=CORS_METHODS,
        allow_headers=CORS_HEADERS,
        expose_headers=CORS_EXPOSE_HEADERS,
    )

    app.middleware("http")(request_trace_headers)
    return app


#: uvicorn / verify 依赖的模块级实例（``uvicorn maa_api.main:app``）。
app = create_app()
