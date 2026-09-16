"""子进程入口：启动序列、回调桥接、命令循环与 GET_IMAGE 落盘（docs/03 §3）。

worker 由 ``multiprocessing.Process`` 以 **spawn** 方式启动（docs/13 ADR-03），因此
只接收纯数据：路径字符串、配置 dict 与两条 ``multiprocessing.Queue``，不接收任何
服务对象（logger / asyncio loop / Asst 实例都不可 pickle）。本模块只做子进程内部：

- 启动序列（docs/03 §3.1）：装日志出口 → 加载基础资源 → 逐层叠加增量资源 →
  建实例并绑定回调 → 应用实例选项 → 上报 ``READY`` → 进入命令循环。
  **不做版本检查与下载**（归主进程 ``UpdateService``），**不连设备**
  （主进程在 ``READY`` 之后显式下发 ``CONNECT``），启动路径上没有网络 IO。
- 回调桥接（docs/03 §3.2）：MaaCore 回调跑在内核自己的线程上，桥接只做
  ``decode`` + ``json.loads`` + ``put``，整段吞掉异常 —— 异常穿过 C 调用边界
  是未定义行为（可能直接崩内核）。
- 命令循环（docs/03 §3.3）：单线程串行，天然保证对 ``Asst`` 的调用不并发；
  ``PING`` 不进内核，``SHUTDOWN`` 收尾退出，其余命令经 :func:`_dispatch` 分派，
  结果统一回 ``CMD_RESULT``。
- ``GET_IMAGE`` 在 :func:`_dispatch` 内落盘，只回文件路径与图像元信息，不把
  2.7 MB 的截图字节塞进 Queue（docs/02 §3.3）。

进程生命周期（心跳、崩溃判定、重启、杀进程）归 M1-08 ``CoreSupervisor``；
主进程侧的代理（Future 匹配、超时、事件分派）归 M1-09 ``CoreClient``。

``boot_config`` 契约（纯 dict、可 pickle，M1-08 / M1-11 / M1-13 依赖）::

    {
        "maa_path": str,                 # 内核目录（含 libMaaCore.* 与 resource/）
        "user_dir": str | None,          # 用户数据目录，None 表示不改写
        "incremental_paths": [str, ...], # 有序增量资源层，顺序即优先级
        "instance_options": {str: str},  # InstanceOptionKey 成员名 → 值
        "asst_factory": "pkg.mod:Class", # 缺省 "maa_api.core.asst:Asst"
        "asst_factory_kwargs": dict,     # 缺省 {}，测试用它注入 FakeAsst 剧本
    }

``asst_factory`` 用字符串而不是类对象：spawn 不能传类对象，也不该让父子进程共享
模块状态；子进程内用 :mod:`importlib` 重新解析。
"""

from __future__ import annotations

import ctypes
import gc
import importlib
import io
import json
import logging
import math
import os
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

from maa_api.core.enums import InstanceOptionKey, StaticOptionKey
from maa_api.domain.errors import ErrorCode

__all__ = ["core_worker_main"]

logger = logging.getLogger(__name__)

#: ``boot_config["asst_factory"]`` 的缺省值：真实内核封装。
DEFAULT_ASST_FACTORY = "maa_api.core.asst:Asst"

# ``event_queue`` 的模块级持有（隐式契约，不要删）：内核注册回调时只保存 arg 的
# **指针值**（``ctypes.c_void_p(id(arg))``，见 maa_api/core/asst.py），对象一旦被
# GC 回收，回调线程里的 ``ctypes.cast`` 就会解引用悬垂地址 —— 直接崩内核。
# core_worker_main 在创建实例之前把它钉在这里，进程生命周期内不得清空。
_EVENT_QUEUE: Any = None

#: 已安装的日志 handler，供 ``_install_log_bridge`` 幂等替换。
_LOG_BRIDGE: Optional["_QueueLogHandler"] = None

#: 防日志递归的线程局部标志：handler 自己入队路径上的日志不再进 handler。
_LOG_STATE = threading.local()


class CoreWorkerError(RuntimeError):
    """worker 内可上报的错误：启动故障与命令执行失败都用它。

    ``core_worker_main`` 在启动期捕获它并转成 ``FATAL`` 事件；
    ``_command_loop`` 在命令期捕获它并转成
    ``CMD_RESULT{ok: False, error: {code: CORE_COMMAND_FAILED, ...}}``。
    """


# ----------------------------------------------------------------------
# 日志出口
# ----------------------------------------------------------------------


class _QueueLogHandler(logging.Handler):
    """把 logging 记录转成 ``LOG`` 事件塞进 ``event_queue``。

    payload 固定为 ``{"level": str, "content": str}``（docs/02 §3.2）；handler
    自己只做格式化 + 入队，绝不反过来调用 logging，避免自我递归。
    """

    def __init__(self, event_queue: Any) -> None:
        super().__init__(level=logging.DEBUG)
        self._event_queue = event_queue
        self.setFormatter(logging.Formatter("%(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        # 防递归：同一线程里由本 handler 触发的日志（含 logging 内部错误）直接丢弃。
        if getattr(_LOG_STATE, "emitting", False):
            return
        _LOG_STATE.emitting = True
        try:
            self._event_queue.put(
                {
                    "type": "LOG",
                    "ts": time.time(),
                    "payload": {
                        "level": record.levelname,
                        "content": self.format(record),
                    },
                }
            )
        except Exception:
            pass  # 日志出口永远不能反过来打断 worker（例如队列已关闭）
        finally:
            _LOG_STATE.emitting = False


def _install_log_bridge(event_queue: Any) -> _QueueLogHandler:
    """建立子进程自己的日志出口：所有 logger 输出转成 ``LOG`` 事件。

    必须是启动序列的第一步，这样后续每一步（资源加载、实例创建、命令失败）的
    日志都能被主进程看到。装在**根** logger 上；子进程里没有其它 handler，
    因此不会重复输出；重复调用会替换上一个 handler（幂等）。
    """
    global _LOG_BRIDGE

    handler = _QueueLogHandler(event_queue)
    root = logging.getLogger()
    if _LOG_BRIDGE is not None:
        root.removeHandler(_LOG_BRIDGE)
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    _LOG_BRIDGE = handler
    return handler


# ----------------------------------------------------------------------
# 回调桥接
# ----------------------------------------------------------------------


def _callback_bridge(msg: int, details: Optional[bytes], arg: Any) -> None:
    """MaaCore 回调 → ``CALLBACK`` 事件（运行在内核自己的回调线程上）。

    只做最小工作：``decode`` + ``json.loads`` + ``put``；不解释语义、不做 IO、
    整段包在 ``try/except`` 里静默吞掉 —— 异常穿过 C 调用边界是未定义行为。

    ``arg`` 按 ``c_void_p(id(event_queue))`` 解释（``Asst.__init__`` 就是这么
    注册的）。纯 Python 替身（``tests/fakes/fake_asst.py``）会把队列对象本身
    当 ``arg`` 传进来，``ctypes.cast`` 对这种对象抛 ``ArgumentError``；此时回退到
    模块级持有的同一支队列（``_EVENT_QUEUE``），两条路径指向同一个对象。

    本函数刻意**不加** ``@Asst.CallBackType`` 装饰：真实的 ``Asst`` 在
    ``__init__`` 里已经用 ``Asst.CallBackType`` 包了一层 trampoline，而替身直接
    以队列对象为 ``arg`` 调用本函数，CFUNCTYPE 的 ``c_void_p`` 参数转换会先抛
    ``ArgumentError``，回调反而到不了这里（Python 3.13.3 实测）。
    """
    try:
        try:
            queue = ctypes.cast(arg, ctypes.py_object).value
        except Exception:
            queue = _EVENT_QUEUE
        if queue is None:
            return
        raw = details or b""
        queue.put(
            {
                "type": "CALLBACK",
                "ts": time.time(),
                "payload": {
                    "msg": int(msg),
                    "details": json.loads(raw.decode("utf-8")) if raw else {},
                },
            }
        )
    except Exception:
        pass  # 回调线程内绝不抛异常


# ----------------------------------------------------------------------
# 子进程入口
# ----------------------------------------------------------------------


def core_worker_main(cmd_queue: Any, event_queue: Any, boot_config: dict) -> None:
    """spawn 子进程入口：启动序列 + 命令循环（docs/03 §3.1 / §3.3）。

    :param cmd_queue: 主 → 子命令队列，``cmd_queue.get()`` 阻塞等待。
    :param event_queue: 子 → 主事件队列（``READY`` / ``CMD_RESULT`` /
        ``CALLBACK`` / ``LOG`` / ``PONG`` / ``FATAL``）。
    :param boot_config: 纯 dict，字段见模块 docstring。

    任何启动异常都会转成 ``FATAL`` 事件后正常返回（进程退出码 0），绝不裸崩：
    主进程靠 ``FATAL`` + 退出事件判因，而不是靠一段 stderr 回溯。
    """
    global _EVENT_QUEUE

    _EVENT_QUEUE = event_queue  # 必须在注册回调之前钉住，见 _EVENT_QUEUE 注释
    _install_log_bridge(event_queue)
    logger.info("worker 启动：pid=%s", os.getpid())

    try:
        asst = _bootstrap(event_queue, boot_config)
    except BaseException as exc:  # noqa: BLE001 - 启动期任何异常都转 FATAL
        logger.exception("worker 启动失败")
        _emit_fatal(event_queue, exc)
        return

    try:
        _command_loop(asst, cmd_queue, event_queue)
    except Exception as exc:  # noqa: BLE001 - 循环级异常也要留下 FATAL 再退出
        logger.exception("worker 命令循环异常退出")
        _emit_fatal(event_queue, exc)
        return

    logger.info("worker 退出：pid=%s", os.getpid())


def _emit_fatal(event_queue: Any, exc: BaseException) -> None:
    """上报不可恢复错误（``FATAL``），调用点必须在 ``except`` 块内。

    连事件队列都写不进去时也只能静默退出：supervisor 会把「非预期退出」当崩溃处理，
    比让异常逃出 ``core_worker_main`` 留下一段无人接收的回溯更可控。
    """
    payload = {
        "error": f"{type(exc).__name__}: {exc}",
        "traceback": traceback.format_exc(),
    }
    try:
        event_queue.put({"type": "FATAL", "ts": time.time(), "payload": payload})
    except Exception:  # noqa: BLE001 - 队列已坏，无从上报
        logger.exception("FATAL 事件入队失败")


def _resolve_asst_factory(spec: str) -> Callable[..., Any]:
    """在子进程内解析 ``"pkg.mod:Class"``（spawn 不能传类对象）。"""
    module_name, separator, attr_name = spec.partition(":")
    if not separator or not module_name or not attr_name:
        raise CoreWorkerError(f"asst_factory 必须是 'pkg.mod:Class' 形式，得到 {spec!r}")
    try:
        factory = getattr(importlib.import_module(module_name), attr_name)
    except (ImportError, AttributeError) as exc:
        raise CoreWorkerError(f"无法解析 asst_factory {spec!r}: {exc}") from exc
    if not callable(factory):
        raise CoreWorkerError(f"asst_factory {spec!r} 不是可调用对象")
    return factory


def _option_key(enum_type: Any, key: Any) -> Any:
    """把 IPC 传来的选项键还原成枚举成员：接受成员名（契约）或整数值。"""
    if isinstance(key, str):
        member = enum_type.__members__.get(key)
        if member is not None:
            return member
        return enum_type(int(key))  # 数字字符串，例如 "2"
    return enum_type(int(key))


def _bootstrap(event_queue: Any, boot_config: dict) -> Any:
    """启动序列（docs/03 §3.1 的 2–6 步），返回绑定好回调的 ``Asst`` 实例。

    失败一律抛 :class:`CoreWorkerError`，由 :func:`core_worker_main` 转成
    ``FATAL``；这里不做版本检查与下载，也不连设备。
    """
    factory = _resolve_asst_factory(
        boot_config.get("asst_factory") or DEFAULT_ASST_FACTORY
    )

    maa_path = boot_config["maa_path"]
    if not factory.load(path=maa_path, user_dir=boot_config.get("user_dir")):
        raise CoreWorkerError(f"基础资源加载失败: {maa_path}")

    for layer in boot_config.get("incremental_paths") or ():
        if not (Path(layer) / "resource").is_dir():
            # 该层尚未下载过是正常状态（首次启动时 maa-layers 下的目录都不存在），
            # 跳过而非报错（docs/03 §7）。
            logger.info("增量资源层不存在，跳过: %s", layer)
            continue
        if not factory.load(path=layer, incremental=True):
            # 返回 false 是真实故障：内核此刻的资源状态半新半旧，必须中断上报。
            raise CoreWorkerError(f"增量资源层加载失败: {layer}")

    asst = factory(
        callback=_callback_bridge,
        arg=event_queue,
        **(boot_config.get("asst_factory_kwargs") or {}),
    )

    for name, value in (boot_config.get("instance_options") or {}).items():
        asst.set_instance_option(_option_key(InstanceOptionKey, name), value)

    version = asst.get_version()
    event_queue.put(
        {
            "type": "READY",
            "ts": time.time(),
            "payload": {"version": version, "pid": os.getpid()},
        }
    )
    logger.info("worker 就绪：version=%s pid=%s", version, os.getpid())
    return asst


# ----------------------------------------------------------------------
# 命令循环
# ----------------------------------------------------------------------


def _command_loop(asst: Any, cmd_queue: Any, event_queue: Any) -> None:
    """单线程串行命令循环（docs/03 §3.3），直到 ``SHUTDOWN`` 或队列关闭。"""
    while True:
        try:
            command = cmd_queue.get()  # 阻塞等待
        except (EOFError, OSError):
            logger.warning("命令队列已关闭，worker 退出")
            _graceful_stop(asst, graceful=False)
            return

        type_name = command.get("type") if isinstance(command, dict) else None
        payload = (command.get("payload") or {}) if isinstance(command, dict) else {}
        cmd_id = command.get("cmd_id") if isinstance(command, dict) else None

        if type_name == "SHUTDOWN":
            logger.info("收到 SHUTDOWN（graceful=%s）", payload.get("graceful", True))
            _graceful_stop(asst, bool(payload.get("graceful", True)))
            return

        if type_name == "PING":
            # 心跳不进内核：PING 只证明命令循环还活着，任何 asst 调用都会引入
            # 无关延迟甚至被慢命令阻塞（docs/02 §3.4 的心跳判定依赖这一点）。
            event_queue.put(
                {
                    "type": "PONG",
                    "ts": time.time(),
                    "payload": {"seq": payload.get("seq")},
                }
            )
            continue

        try:
            data = _dispatch(asst, type_name, payload)
        except Exception as exc:  # noqa: BLE001 - 单条命令失败不能带崩 worker
            logger.exception("命令 %s 执行失败", type_name)
            event_queue.put(
                {
                    "type": "CMD_RESULT",
                    "ts": time.time(),
                    "payload": {
                        "cmd_id": cmd_id,
                        "ok": False,
                        "data": None,
                        "error": {
                            "code": ErrorCode.CORE_COMMAND_FAILED.value,
                            "message": str(exc),
                        },
                    },
                }
            )
        else:
            event_queue.put(
                {
                    "type": "CMD_RESULT",
                    "ts": time.time(),
                    "payload": {
                        "cmd_id": cmd_id,
                        "ok": True,
                        "data": data,
                        "error": None,
                    },
                }
            )


def _graceful_stop(asst: Any, graceful: bool = True) -> None:
    """退出前的收尾：graceful 时先 ``asst.stop()``，再释放实例。

    ``Asst`` 没有公开的 destroy 方法 —— ``AsstDestroy`` 在 ``__del__`` 里调用，
    所以「销毁实例」只能是释放引用 + ``gc.collect()``；命令循环在调用本函数后
    立即返回，它自己那份引用也随之释放。FakeAsst 的剧本线程是 daemon，真实内核
    线程随进程退出而终止，因此 worker 不会被非 daemon 线程挂住。
    """
    if graceful:
        try:
            asst.stop()
        except Exception:  # noqa: BLE001 - 停止失败也必须退出
            logger.exception("优雅停止 asst 失败，仍继续退出")
    del asst
    gc.collect()


# ----------------------------------------------------------------------
# 命令分派（docs/02 §3.1 全表）
# ----------------------------------------------------------------------


def _dispatch(asst: Any, type_name: Optional[str], payload: dict) -> Any:
    """执行一条命令，返回放进 ``CMD_RESULT.data`` 的纯数据。"""
    if type_name == "LOAD_RESOURCE":
        return _load_resource(asst, payload)

    if type_name == "SET_INSTANCE_OPTION":
        return asst.set_instance_option(
            _option_key(InstanceOptionKey, payload["key"]), payload["value"]
        )

    if type_name == "SET_STATIC_OPTION":
        return asst.set_static_option(
            _option_key(StaticOptionKey, payload["key"]), payload["value"]
        )

    if type_name == "SET_CONNECTION_EXTRAS":
        asst.set_connection_extras(payload["name"], payload["extras"])
        return None

    if type_name == "CONNECT":
        # 返回值是 AsstAsyncCallId（受理编号），**不是连接结果**：成败只能认
        # msg=4 的 AsyncCallInfo（M1-01 真机实测）。msg=2 的 ConnectionInfo 会先
        # 报出 what == "Connected"，那只是中间态，不得拿它代替。
        return {
            "async_call_id": asst.connect_async(
                adb_path=payload["adb_path"],
                address=payload["address"],
                config=payload.get("config", "General"),
                block=payload.get("block", True),
            )
        }

    if type_name == "CONNECTED":
        return asst.connected()

    if type_name == "APPEND_TASK":
        task_id = asst.append_task(payload["type_name"], payload.get("params"))
        if not task_id:
            # 内核校验必填参数，缺参数时静默返回 0（不抛异常、无回调，M1-02 实测）：
            # task_id == 0 必须当失败上报，不能当作正常结果。
            raise CoreWorkerError(
                f"内核拒绝任务 {payload['type_name']!r}（task_id=0，缺少必填参数？）"
            )
        return task_id

    if type_name == "SET_TASK_PARAMS":
        return asst.set_task_params(payload["task_id"], payload["params"])

    if type_name == "START":
        return asst.start()

    if type_name == "STOP":
        return asst.stop()

    if type_name == "RUNNING":
        return asst.running()

    if type_name == "CLICK":
        return {
            "async_call_id": asst.click(
                payload["x"], payload["y"], payload.get("block", True)
            )
        }

    if type_name == "SCREENCAP":
        return {"async_call_id": asst.screencap(payload.get("block", True))}

    if type_name == "GET_IMAGE":
        return _get_image(asst, payload)

    if type_name == "BACK_TO_HOME":
        return asst.back_to_home()

    if type_name == "GET_UUID":
        return asst.get_uuid()

    if type_name == "GET_TASKS_LIST":
        return asst.get_tasks_list()

    if type_name == "GET_MAP_LEVEL_KEY":
        return asst.get_map_level_key(payload["key"])

    if type_name == "GET_VERSION":
        return asst.get_version()

    raise CoreWorkerError(f"未知命令类型: {type_name!r}")


def _load_resource(asst: Any, payload: dict) -> dict[str, list[str]]:
    """``LOAD_RESOURCE``：基础资源 + 有序增量层（docs/03 §7）。

    基础资源必须是第一次调用：``AsstLoadResource`` 只在内核资源根目录为空时设置
    一次，第一次若传增量目录，资源根会被永久钉在残缺目录上。增量层按列表顺序
    逐层叠加，顺序即优先级；不存在的层跳过，返回 false 的层中断上报。

    返回 ``{"loaded": [...], "skipped": [...]}``（都按输入顺序），供主进程与测试
    观测到底加载了哪几层。
    """
    base = payload["path"]
    loaded: list[str] = []
    skipped: list[str] = []

    if not asst.load(path=base, user_dir=payload.get("user_dir")):
        raise CoreWorkerError(f"基础资源加载失败: {base}")
    loaded.append(str(base))

    for layer in payload.get("incremental_paths") or ():
        if not (Path(layer) / "resource").is_dir():
            logger.info("增量资源层不存在，跳过: %s", layer)
            skipped.append(str(layer))
            continue
        if not asst.load(path=layer, incremental=True):
            raise CoreWorkerError(f"增量资源层加载失败: {layer}")
        loaded.append(str(layer))

    return {"loaded": loaded, "skipped": skipped}


def _get_image(asst: Any, payload: dict) -> dict[str, Any]:
    """``GET_IMAGE``：取图并落盘，只回文件路径与元信息（docs/02 §3.3）。"""
    bgr = bool(payload.get("bgr", False))
    data = asst.get_image(size=payload.get("size"), bgr=bgr)
    if not data:
        raise CoreWorkerError("GET_IMAGE 失败：内核未返回图像数据")
    save_to = payload.get("save_to")
    if not save_to:
        raise CoreWorkerError(
            "GET_IMAGE 缺少 save_to：截图必须落盘后传引用（docs/02 §3.3）"
        )
    return _write_image(asst, data, save_to, bgr)


def _write_image(asst: Any, data: bytes, save_to: str, bgr: bool) -> dict[str, Any]:
    """把截图字节写进 ``save_to``，返回 ``CMD_RESULT.data``。

    分辨率已知（``last_resolution()``）时用 Pillow 编码 JPEG，返回
    ``{"path", "size", "width", "height", "encoding": "jpeg"}``；分辨率未知时
    无法重建像素，原样写盘（无损），按 ``bgr`` 加 ``.bgr`` / ``.rgb`` 后缀并返回
    ``{"path", "size", "encoding": "raw"}``。父目录不存在时自动创建。
    """
    path = Path(save_to)
    path.parent.mkdir(parents=True, exist_ok=True)

    resolution = asst.last_resolution()
    if resolution:
        width, height = int(resolution[0]), int(resolution[1])
        image = _to_pil_image(data, width, height, bgr)
        image.save(path, format="JPEG")
        return {
            "path": str(path),
            "size": path.stat().st_size,
            "width": image.width,
            "height": image.height,
            "encoding": "jpeg",
        }

    raw_path = path.with_name(path.name + (".bgr" if bgr else ".rgb"))
    raw_path.write_bytes(data)
    return {"path": str(raw_path), "size": len(data), "encoding": "raw"}


def _to_pil_image(data: bytes, width: int, height: int, bgr: bool):
    """把 ``get_image`` 的字节还原成 Pillow 图像。

    两种输入形态（M1-04 真机实测）：``bgr=False`` 时真实内核返回的是 **PNG 编码
    字节**（magic ``\\x89PNG``），先按已编码图像解码，解不开才当裸 RGB；``bgr=True``
    时返回裸 BGR，且长度**不一定**等于 ``w*h*3``（实测 2560x1440 的会话给的是
    1280x720x3 的内部缩放图），因此裸字节按数据长度推断真实像素尺寸。
    """
    from PIL import Image  # 延迟 import：只在 GET_IMAGE 落盘时付这份 import 成本

    if not bgr:
        try:
            image = Image.open(io.BytesIO(data))
            image.load()
            return image.convert("RGB")
        except Exception:  # noqa: BLE001 - 不是已编码图像，走裸字节分支
            logger.debug("截图字节不是已编码图像，按裸 RGB 处理")

    pixels_w, pixels_h = _resolve_raw_size(len(data), width, height)
    expected = pixels_w * pixels_h * 3
    if len(data) < expected:
        raise CoreWorkerError(f"截图字节数不足：需要 {expected}，实际 {len(data)}")
    return Image.frombytes(
        "RGB", (pixels_w, pixels_h), data[:expected], "raw", "BGR" if bgr else "RGB"
    )


def _resolve_raw_size(data_len: int, width: int, height: int) -> tuple[int, int]:
    """按字节数还原裸截图的像素尺寸；反推不出来就报错，不猜。

    常规情况 ``data_len == width*height*3``；内核返回内部缩放图时按整数倍缩放
    （实测 2560x1440 → 1280x720，即 1/2）从像素数反推。
    """
    if data_len == width * height * 3:
        return width, height
    if data_len > 0 and data_len % 3 == 0:
        pixels = data_len // 3
        area = width * height
        if pixels > 0 and area % pixels == 0:
            factor = math.isqrt(area // pixels)
            if (
                factor > 1
                and width % factor == 0
                and height % factor == 0
                and (width // factor) * (height // factor) == pixels
            ):
                logger.info(
                    "截图字节数 %s 对应 %sx%s 的缩放图（内核内部尺寸），按 %sx%s 编码",
                    data_len,
                    width,
                    height,
                    width // factor,
                    height // factor,
                )
                return width // factor, height // factor
    raise CoreWorkerError(
        f"截图字节数 {data_len} 与分辨率 {width}x{height} 不匹配"
        "（既不是 w*h*3，也不是整数倍缩放）"
    )
