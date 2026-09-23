"""应用装配：生命周期、路由注册、CORS、OpenAPI 元数据（docs/02 §6–§7，docs/05 §11）。

本模块是 :mod:`maa_api` 的唯一装配入口（``uvicorn maa_api.main:app``）。它只做
「接线」，不含任何业务逻辑；配置加载、数据库迁移与 M4 日志生命周期在此装配。

启动顺序（docs/02 §7 的九步）
=============================

:func:`lifespan` 按文档顺序组织，M3 只落地第 1–2 步，其余步骤是**显式的带注释
扩展位**：``# M4+:`` 之后的每一步都归属后续里程碑（LogHub 归 M4，CoreSupervisor 归 M5，
PipelineRunner 归 M5，DeviceManager / APScheduler 归 M6，ToolRegistry / MCP 归 M11–M12），
**本卡刻意不 import 尚不存在或还是空壳的模块** —— 提前接线只会让 M3 的「可交付状态」
（docs/12 M3：业务端点尚未接内核）名不副实。

第 4 步（启动 CoreSupervisor **不阻塞等待**）是与现状最大的行为差异：服务进入可用
状态不再依赖内核就绪，因此 M3 起 ``/api/system/health`` 就必须能如实返回服务自身
状态；lifespan 里任何一步都不允许阻塞等待子进程。

为什么是 ``lifespan``
=====================

废弃的启动事件钩子在 fastapi 0.141 上仍可用但会发 deprecation 警告，且与
「启动 / 关闭成对书写」的语义不匹配；docs/02 §7 明确要求改用
``@asynccontextmanager`` 的 lifespan。关闭顺序（docs/02 §7：停止接受新请求 → 停
APScheduler → 取消 PipelineRunner → 关闭子进程 → 刷日志落库 → 关数据库）在 M3
M4 起退出段会按依赖顺序关闭 WebSocket、日志 tailer 并刷新日志缓冲。

CORS（docs/05 §5.1）
====================

旧装配把「通配符 origin + ``allow_credentials=True``」放在一起，浏览器会直接拒绝
（带凭据的响应不允许通配符 origin），等于旧配置实际失效。这里改为**显式白名单**
（本机 8002 两个回环写法）+ ``allow_origin_regex`` 覆盖 RFC 1918 局域网网段，
保留 ``allow_credentials=True``，方法/头也显式列出而不是通配。

局域网地址「可在设置页添加」归 M6：middleware 在应用构造时装配，M6 接设置页时要么
在 :func:`create_app` 之前读出白名单，要么换成按请求查设置的动态 middleware；本卡
把默认白名单留成模块级常量（:data:`CORS_ORIGINS` / :data:`CORS_ORIGIN_REGEX`）作为
唯一真源。

OpenAPI（docs/05 §11）
======================

- ``openapi_tags=TAGS``：15 条占位分组，顺序即 ``/docs`` 展示顺序；``ws`` 与将来的
  ``mcp`` 不能在 Swagger UI 里调试，但必须保留条目说明协议与鉴权方式。
- ``generate_unique_id_function=custom_operation_id``：operationId 统一成
  ``{tag}_{函数名}``（typescript 客户端方法名依赖这条约定）。
- ``redirect_slashes=False``：所有路由不带尾斜杠，尾斜杠一律 404，避免 307 干扰
  前端与 MCP 客户端（docs/05 §2.5）。

日志
====

一律 stdlib :func:`logging.getLogger`。``maa_api.log`` 的 import 无副作用，lifespan
中显式安装控制台、文件与 LogHub handler。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from importlib.metadata import version as distribution_version
from typing import Protocol

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import logs, screenshots, system, tasks
from maa_api.api.ws import router as ws_router
from maa_api.db.migrate import ensure_schema
from maa_api.services.log_hub import LogHub, set_log_hub
from maa_api.services.log_wiring import (
    install_service_logging,
    start_core_debug_tailer,
    stop_logging,
)
from maa_api.settings import get_settings, load_settings, set_settings

__all__ = [
    "CORS_HEADERS",
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
    "service_version",
    "warn_if_auth_disabled",
]

logger = logging.getLogger(__name__)

#: 服务标题与描述；描述会原样进入 ``/openapi.json`` 的 ``info``。
TITLE = "MAA-API"
DESCRIPTION = (
    "MaaAssistantArknights 的 Web 服务（重构版）。\n\n"
    "- **鉴权**：单一 `access_token`，四渠道按优先级取值 —— "
    "`Authorization: Bearer` → `X-Token` → `?token=` → cookie `maa_token`；"
    "未配置 `access_token` 时全部端点免鉴权\n"
    "- **错误体**：所有非 2xx 响应都是 "
    '`{"error": {"code", "message", "details"}}`，`code` 是可枚举的错误码\n'
    "- **尾斜杠**：全部路由不带尾斜杠，带尾斜杠一律 404（`redirect_slashes=False`）\n"
    "- `ws` 与 `mcp` 分组只在本文档占位说明协议与鉴权方式，不能在 Swagger UI 中调试"
)

#: 读不到分发元数据时的回退版本（与 pyproject.toml 一致）；与
#: :func:`maa_api.api.routers.system.health` 的取法相同：只读 importlib.metadata，
#: 不 import 内核，也不 import 本模块（避免循环依赖）。
_FALLBACK_VERSION = "0.1.0"

#: ``openapi_tags``：与路由分组一一对应，顺序即 ``/docs`` 展示顺序（docs/05 §11.1 原文）。
TAGS: list[dict[str, str]] = [
    {"name": "system", "description": "健康检查、服务信息、日志查询"},
    {"name": "pipelines", "description": "流水线提交、查询与取消"},
    {"name": "queue", "description": "队列快照与优先级调整"},
    {"name": "tasks", "description": "任务类型与参数 schema"},
    {"name": "device", "description": "设备状态、重连与原子操作"},
    {"name": "core", "description": "MaaCore 子进程状态与原生能力"},
    {"name": "screenshots", "description": "截图归档与读取"},
    {"name": "updates", "description": "内核 / 资源 / 游戏三种热更新"},
    {"name": "schedules", "description": "定时任务"},
    {"name": "settings", "description": "可视化配置"},
    {"name": "notifications", "description": "多通道通知"},
    {"name": "resources", "description": "Copilot 作业、基建方案、自定义 task"},
    {"name": "agent", "description": "工具清单、会话与审计"},
    {"name": "confirmations", "description": "高风险操作的人工确认"},
    {"name": "ws", "description": "WebSocket 实时通道（仅文档说明，不可在此调试）"},
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

#: 显式方法白名单（含 ``OPTIONS``：浏览器预检本身就是 OPTIONS）。
CORS_METHODS: list[str] = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

#: 显式请求头白名单：覆盖四渠道鉴权（``Authorization`` / ``X-Token``）、JSON 请求体与
#: 幂等键（docs/05 §10.1 的 ``Idempotency-Key``）。starlette 会自动并入 safelisted 头。
CORS_HEADERS: list[str] = [
    "Authorization",
    "X-Token",
    "Content-Type",
    "Idempotency-Key",
]


class OperationIdRoute(Protocol):
    """``custom_operation_id`` 需要的最小路由契约。

    fastapi 0.141 的 ``generate_unique_id_function`` 对 ``@app.get`` 直挂的路由收到
    ``APIRoute``，对 ``include_router`` 进来的路由收到 ``_EffectiveRouteContext``
    包装对象（实测见 .refactor/ENVIRONMENT.md）：两者都有 ``name`` / ``tags``。
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

    独立成函数是为了可单测：直接调用即可断言文案，不必依赖 ``TestClient`` 线程里的
    日志捕获（M3-09 实测 caplog 也能捕到，但直接调用更稳、更快）。这条文案是前端与
    运维判断鉴权模式的依据，必须有用例钉住。
    """
    if not get_settings().access_token:
        logger.warning(AUTH_DISABLED_WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动 / 关闭序列（docs/02 §7 九步的 M3 落地版）。

    启动（进入时）：

    1. 加载配置 —— ``config.yaml`` → DB 覆盖项 → 环境变量（M6 才接 DB 层）
    2. 初始化数据库引擎并执行 Alembic 迁移到 head（:func:`ensure_schema`，失败即启动失败）
    3. 记录 ``app.state.started_at``（UTC ISO 8601 字符串，M3-07 的 health 读它）
    4. 其余步骤见下面的 ``# M4+:`` 扩展位

    关闭（退出时）：M3 没有可关闭的组件，关闭顺序留给后续里程碑。
    """
    # 第 1 步：加载配置。整份配置的读侧入口是 maa_api.settings.get_settings()，
    # lifespan 只负责在启动时刷新一次进程内缓存（文件改动不会自动生效）。
    set_settings(load_settings())
    # 免鉴权模式必须显式可见，不能静默（docs/05 §5.2）。
    warn_if_auth_disabled()

    # 第 2 步：初始化数据库引擎并迁移到 head。ensure_schema 内部经 asyncio.to_thread
    # 执行阻塞的 alembic upgrade；失败原样抛出、中止启动（docs/04 §8.3）。
    await ensure_schema()

    # 第 9 步的可见结果：进程启动时间写入 app.state，health 端点读取它。
    app.state.started_at = datetime.now(timezone.utc).isoformat()

    # 第 3 步（M4）：启动 LogHub、Python logging handler 与 MaaCore debug 日志 tail。
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

    # M5+: 第 4 步 启动 CoreSupervisor，**不阻塞等待**（后台完成资源加载与连接）
    # M5+: 第 5 步 启动 PipelineRunner 消费循环
    # M6+: 第 6 步 启动 DeviceManager 的连接监控
    # M6+: 第 7 步 从 DB 装载定时任务，启动 APScheduler
    # M11+/M12+: 第 8 步 注册 ToolRegistry，挂载 MCP endpoint

    # 第 9 步：yield 之后 uvicorn 才开始接受 HTTP 请求。M4 不启动或等待内核进程。
    try:
        yield
    finally:
        # M4 关闭顺序：关闭 WS，取消 tailer，再刷新 LogHub 的落库批次。
        await stop_logging(tailer_task, hub)
        if hasattr(app.state, "log_hub"):
            del app.state.log_hub


# ----------------------------------------------------------------------
# 应用装配
# ----------------------------------------------------------------------


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

    # 统一错误体与四个异常处理器（M3-04）：必须发生在第一个请求之前，注册本身幂等。
    register_exception_handlers(app)

    # 路由：前缀与 tag 都写在各自模块里，这里只 include。
    # 旧 router/adb、router/maa、router/template 一律不注册（M3 起废弃，docs/02 §9）；
    # 旧 static 挂载也不做 —— SPA catch-all 归 M8。
    app.include_router(system.router)
    app.include_router(tasks.router)
    app.include_router(logs.router)
    app.include_router(screenshots.router)
    app.include_router(ws_router)

    # CORS：显式白名单 + 局域网正则，保留 allow_credentials=True（docs/05 §5.1）。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_origin_regex=CORS_ORIGIN_REGEX,
        allow_credentials=True,
        allow_methods=CORS_METHODS,
        allow_headers=CORS_HEADERS,
    )
    return app


#: uvicorn / verify 依赖的模块级实例（``uvicorn maa_api.main:app``）。
app = create_app()
