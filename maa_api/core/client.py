"""CoreClient：主进程侧的内核异步命令代理（M1-09，docs/03 §5 / docs/02 §3.4）。

把 ``cmd_queue`` / ``event_queue`` 的 IPC 细节对上层完全隐藏，暴露一组普通的 async
方法。四件事：

1. **事件消费线程。** ``multiprocessing.Queue.get()`` 是阻塞的同步调用，不能直接在
   event loop 里读；也不能用 ``run_in_executor`` —— 长期占用的阻塞任务会污染默认
   executor（docs/03 §5）。本类开一条 daemon 线程循环 ``get(timeout=...)``，拿到事件
   后用 ``loop.call_soon_threadsafe`` 投回事件循环处理。**每轮重新读
   ``supervisor.event_queue``**：队列按子进程代际轮换（M1-08 的 ``_rotate_queues``），
   缓存 Queue 对象会在内核重启后静默收不到事件与命令回执。
2. **cmd_id → Future。** 发命令时建 ``asyncio.Future`` 存进 :attr:`_pending_cmds`，收到
   ``CMD_RESULT`` 时 pop 并 ``set_result``；超时 pop 并 ``set_exception``
   （``CORE_COMMAND_TIMEOUT``），同时 ``supervisor.release_command``。超时后迟到的
   ``CMD_RESULT`` 已不在映射里，**直接丢弃并记一条 warning**，绝不 set 到已结束的
   Future。
3. **两级 Future。** ``CONNECT`` / ``CLICK`` / ``SCREENCAP`` 先拿 ``CMD_RESULT.data``
   里的 ``async_call_id``（受理编号，第一级），再等 ``msg=4`` 的 ``AsyncCallInfo``
   回调兑现结果（第二级）。载荷层级以 M1-01 真机实测为准
   （``tests/fixtures/async_call_info_sample.json``）：关联键 ``async_call_id`` 与调用
   类型 ``what`` 在回调 JSON **顶层**，成功标志在 ``details.ret``（也就是回调事件
   payload 的 ``details.details.ret``）。``msg=2`` 的 ``ConnectionInfo`` 会先报
   ``what == "Connected"``，那只是中间态，连接成败只能认 ``msg=4``。回调可能**先于**
   受理结果到达（子进程里回调是内核线程同步发出的，``CMD_RESULT`` 要等命令循环回
   到循环顶部才入队；``FakeAsst`` 就是同步发回调），此时结果先进
   :attr:`_early_async` 缓存，受理结果到达时立即兑现。
4. **事件分派。** ``on(event_type, handler)`` 注册分派表（一个类型可多处理器，按注册
   顺序调用，处理器异常吞掉并记日志）；``READY`` / ``PONG`` / ``FATAL`` 默认转发给
   ``CoreSupervisor.handle_*``，``CALLBACK`` / ``LOG`` / ``CMD_RESULT`` 默认喂给
   ``supervisor.note_event`` 记入崩溃现场。其他服务只注册处理器，不直接触碰 Queue。

未就绪门禁（docs/03 §5）：``supervisor.state`` 为 STARTING / STOPPED 时所有业务命令
立刻抛 ``AppError(CORE_NOT_READY)``，RESTARTING → ``CORE_RESTARTING``，CRASHED →
``CORE_CRASHED``，FAILED → ``CORE_START_FAILED``（``details["state"]`` 带状态），
**不进队列等待** —— 让调用方立刻拿到明确状态，而不是挂在一个可能永远不就绪的内核上。

超时按命令类型区分（docs/02 §3.4，取自 :data:`maa_api.core.protocol.CMD_TIMEOUTS`）：
查询类 5 秒、``CONNECT`` 60 秒、``LOAD_RESOURCE`` 300 秒、``START`` / ``STOP`` 30 秒。
超时不代表子进程已死，存活判定仍归 supervisor 的心跳，不依赖这里兜底。

本模块只提供 append/start/stop 与事件分派；流水线的任务完成判定归 M5
``PipelineRunner``。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from maa_api.core.enums import Message
from maa_api.core.protocol import (
    CMD_TIMEOUTS,
    DEFAULT_QUERY_TIMEOUT,
    EVENT_TYPES,
    make_command,
)
from maa_api.core.supervisor import CoreState
from maa_api.domain.errors import AppError, ErrorCode

__all__ = ["CoreClient", "DEFAULT_SCREENCAP_DIR"]

logger = logging.getLogger(__name__)

#: 截图缺省落盘目录：``<repo 根>/resource/temp/screencap``（可用构造参数覆盖）。
DEFAULT_SCREENCAP_DIR: Path = (
    Path(__file__).resolve().parents[2] / "resource" / "temp" / "screencap"
)

#: 消费线程阻塞读的超时（秒）。队列按代轮换，靠它定期重读
#: ``supervisor.event_queue`` 并检查停止标志；有事件时 ``get`` 立即返回，不引入延迟。
CONSUMER_POLL_INTERVAL: float = 0.25

#: 消费线程遇到「队列已关闭 / 正在被换掉」时的退避（秒），避免忙等。
CONSUMER_ERROR_SLEEP: float = 0.05

#: ``close()`` 等待消费线程退出的上限（秒）。线程是 daemon，超时不会阻塞进程退出。
CLOSE_JOIN_TIMEOUT: float = 2.0

#: 事件循环尚未绑定时最多缓存多少条事件（超出丢最旧的并记 warning）。
DEFERRED_EVENT_LIMIT: int = 512

#: 异步结果缓存的存活时长与容量（秒 / 条）：只用于跨过「回调先到、受理结果后到」的窗口。
EARLY_ASYNC_TTL: float = 30.0
EARLY_ASYNC_LIMIT: int = 64

#: 未就绪状态 → 领域错误码（docs/03 §5）。READY 不在表内。
_STATE_GATE: dict[CoreState, ErrorCode] = {
    CoreState.STOPPED: ErrorCode.CORE_NOT_READY,
    CoreState.STARTING: ErrorCode.CORE_NOT_READY,
    CoreState.RESTARTING: ErrorCode.CORE_RESTARTING,
    CoreState.CRASHED: ErrorCode.CORE_CRASHED,
    CoreState.FAILED: ErrorCode.CORE_START_FAILED,
}

_STATE_GATE_MESSAGE: dict[ErrorCode, str] = {
    ErrorCode.CORE_NOT_READY: "内核未就绪，命令未下发（等待 READY 或 start() 后再试）",
    ErrorCode.CORE_RESTARTING: "内核正在重启，命令未下发（重启完成后重试）",
    ErrorCode.CORE_CRASHED: "内核已崩溃，命令未下发（等待自动重启或手动 restart()）",
    ErrorCode.CORE_START_FAILED: "内核启动失败（FAILED），命令未下发（需人工介入）",
}

#: 第一级受理结果里的 async_call_id 候选键（实测在 ``CMD_RESULT.data`` 顶层）。
_CALL_ID_KEYS: tuple[str, ...] = ("async_call_id", "call_id", "id")

#: 第二级回调里的 async_call_id 候选路径（按层尝试，实测顶层优先）。
_CALL_ID_PATHS: tuple[tuple[str, ...], ...] = (
    ("async_call_id",),
    ("call_id",),
    ("id",),
    ("details", "async_call_id"),
    ("details", "call_id"),
    ("details", "id"),
)

#: 第二级回调里的成功标志候选路径。第一条是 M1-01 真机实测路径（回调 JSON 的
#: ``details.ret``，即回调事件 payload 的 ``details.details.ret``）；其余为宽容候选。
_RESULT_PATHS: tuple[tuple[str, ...], ...] = (
    ("details", "ret"),
    ("details", "result"),
    ("details", "success"),
    ("details", "ok"),
    ("details", "details", "ret"),
    ("details", "details", "result"),
    ("details", "details", "success"),
    ("details", "details", "ok"),
    ("ret",),
    ("result",),
    ("success",),
    ("ok",),
)


class _AsyncCallUnparsed(Exception):
    """``AsyncCallInfo`` 载荷无法解析（内部信号，让等待方走回退路径）。

    ``connect()`` 捕获它后回退到 ``CONNECTED`` 轮询；``CLICK`` / ``SCREENCAP`` 没有
    轮询退路，转成 ``AppError(CORE_COMMAND_FAILED)``。它不是领域异常，不对外暴露。
    """


class CoreClient:
    """主进程侧的内核异步代理（docs/03 §5）。

    :param supervisor: M1-08 的 :class:`~maa_api.core.supervisor.CoreSupervisor` 或
        结构一致的测试替身；只依赖 ``cmd_queue`` / ``event_queue`` / ``state`` /
        ``track_command(cmd)`` / ``release_command(cmd_id)``，若它还有
        ``set_dispatcher`` / ``handle_ready`` / ``handle_pong`` / ``handle_fatal`` /
        ``note_event``，本类会自动注册（缺失即跳过，便于测试替身）。
    :param loop: 事件循环。缺省 ``None`` 表示延迟到第一次异步调用 / 第一次事件分派时
        绑定当前运行的 loop（``start_consumer()`` 在 async 上下文里调用时即可绑定）。
    :param screencap_dir: 截图缺省落盘目录；``None`` 用 :data:`DEFAULT_SCREENCAP_DIR`。
    :param connect_timeout: 第二级 Future（异步结果回调）的默认超时，默认 60 秒。
    :param accept_timeout: 第一级 Future（命令受理）的默认超时，默认 10 秒。
    :param poll_interval: ``AsyncCallInfo`` 解析失败时回退 ``CONNECTED`` 轮询的间隔。

    线程与事件循环的边界：``_pending_cmds`` / ``_pending_async`` / ``_early_async`` /
    ``_handlers`` 只在事件循环线程里读写；消费线程只做 ``get`` +
    ``call_soon_threadsafe``。``close()`` 可从任意线程调用。
    """

    def __init__(
        self,
        supervisor: Any,
        *,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        screencap_dir: Any = None,
        connect_timeout: float = 60.0,
        accept_timeout: float = 10.0,
        poll_interval: float = 0.5,
    ) -> None:
        for attribute in ("cmd_queue", "event_queue", "state", "track_command", "release_command"):
            if not hasattr(supervisor, attribute):
                raise ValueError(
                    f"supervisor 缺少 {attribute!r}：CoreClient 只依赖 "
                    "cmd_queue / event_queue / state / track_command / release_command"
                )
        if connect_timeout <= 0 or accept_timeout <= 0:
            raise ValueError(
                f"超时必须为正: connect_timeout={connect_timeout}, accept_timeout={accept_timeout}"
            )
        if poll_interval < 0:
            raise ValueError(f"poll_interval 不能为负: {poll_interval}")

        self._supervisor = supervisor
        self._loop = loop
        self._screencap_dir = (
            Path(screencap_dir) if screencap_dir is not None else DEFAULT_SCREENCAP_DIR
        )
        self._connect_timeout = float(connect_timeout)
        self._accept_timeout = float(accept_timeout)
        self._poll_interval = float(poll_interval)

        # 待决表：cmd_id → Future（IPC 往返）与 async_call_id → Future（回调结果）。
        self._pending_cmds: dict[str, asyncio.Future] = {}
        self._pending_async: dict[Any, asyncio.Future] = {}
        # 回调先于受理结果到达时的结果缓存：async_call_id → (ret, 缓存时刻)。
        self._early_async: dict[Any, tuple[Any, float]] = {}

        # 最近一次 ResolutionGot / ResolutionInfo（决定缺省截图文件后缀）。
        self._last_resolution: Optional[tuple[int, int]] = None

        # 分派表：事件类型 → 处理器列表（按注册顺序调用）。内建处理器先注册。
        self._handlers: dict[str, list[Callable[[dict], Any]]] = {
            kind: [] for kind in EVENT_TYPES
        }
        self._note_event: Optional[Callable[[dict], Any]] = None
        self._register_supervisor_handlers()

        # 消费线程状态。
        self._thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None
        self._closing = False
        self._deferred: list[Any] = []
        self._deferred_lock = threading.Lock()

    # ------------------------------------------------------------------
    # 事件分派表
    # ------------------------------------------------------------------

    def on(self, event_type: str, handler: Callable[[dict], Any]) -> None:
        """注册事件处理器（一个类型可多个，按注册顺序调用）。

        ``handler`` 收到的是事件 **payload** dict（不是完整事件）：

        - ``READY`` / ``FATAL`` / ``PONG``：payload 直接就是 ``supervisor.handle_*``
          的入参（``READY`` 至少含 ``version`` / ``pid``）；
        - ``CALLBACK``：``{"msg": int, "details": dict}``（内核原始回调形态）；
        - ``LOG``：``{"level": str, "content": str}``；
        - ``CMD_RESULT``：``{"cmd_id", "ok", "data", "error"}``。

        处理器抛异常会被吞掉并记日志，绝不打断消费循环（回调线程里抛异常危害远大于
        处理器本身的失败）。
        """
        if event_type not in self._handlers:
            raise ValueError(
                f"未知事件类型 {event_type!r}，合法取值见 {sorted(EVENT_TYPES)}"
            )
        if not callable(handler):
            raise TypeError(f"handler 必须可调用: {handler!r}")
        self._handlers[event_type].append(handler)

    def _register_supervisor_handlers(self) -> None:
        """内建分派：READY/PONG/FATAL → supervisor.handle_*，其余 → note_event。

        supervisor 自己**不读** ``event_queue``（单一消费者纪律），它靠消费线程把
        这三类事件回调进状态机。调用方再显式 ``on()`` 注册一遍是幂等无害的
        （``handle_ready`` / ``handle_pong`` 幂等，``handle_fatal`` 在已崩溃状态下
        不再重复处理），所以这里默认接好，避免漏注册导致内核永远停在 STARTING。
        """
        for kind, method_name in (
            ("READY", "handle_ready"),
            ("PONG", "handle_pong"),
            ("FATAL", "handle_fatal"),
        ):
            method = getattr(self._supervisor, method_name, None)
            if callable(method):
                self._handlers[kind].append(method)
        note_event = getattr(self._supervisor, "note_event", None)
        if callable(note_event):
            self._note_event = note_event

    def _call_handlers(self, event_type: Optional[str], payload: dict) -> None:
        for handler in list(self._handlers.get(event_type or "", ())):
            try:
                handler(payload)
            except Exception:  # noqa: BLE001 - 处理器异常不能打断消费循环
                logger.exception(
                    "事件 %s 的处理器 %r 抛异常（已吞掉，消费循环继续）", event_type, handler
                )

    # ------------------------------------------------------------------
    # 消费线程：启动 / 循环 / 关闭
    # ------------------------------------------------------------------

    def start_consumer(self) -> None:
        """启动事件消费线程（幂等；``close()`` 之后可再次调用重启）。

        同时向 supervisor 声明本客户端是 ``event_queue`` 的唯一消费者
        （``set_dispatcher``）：M1-08 的 ``start()`` / ``restart()`` 之前必须先声明，
        否则 fail loud（避免两个消费者抢事件）。
        """
        with self._deferred_lock:
            thread = self._thread
            if thread is not None and thread.is_alive():
                return
            self._closing = False
            stop_event = threading.Event()
            self._stop_event = stop_event
            self._declare_dispatcher()
            # 在 async 上下文（lifespan）里调用时立刻绑定 loop，并补投此前缓存的事件；
            # 否则 READY 会一直躺在 _deferred 里，supervisor 永远等不到就绪。
            loop = self._resolve_loop()
            if loop is not None:
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(self._flush_deferred)
            thread = threading.Thread(
                target=self._consume,
                args=(stop_event,),
                name="core-client-consumer",
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def close(self) -> None:
        """停消费线程、取消所有未决 Future（幂等，可从任意线程调用）。

        停止方式是「置停止标志 + 带超时的 ``get``」：消费线程每
        :data:`CONSUMER_POLL_INTERVAL` 秒醒一次检查标志，不做会泄漏线程的忙等，也不
        往 IPC 队列里塞哨兵（队列按子进程代际轮换，哨兵可能落进已被换掉的队列）。
        """
        self._closing = True
        stop_event = self._stop_event
        if stop_event is not None:
            stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=CLOSE_JOIN_TIMEOUT)
            if thread.is_alive():
                logger.warning(
                    "消费线程在 %.1fs 内未退出（daemon 线程，不阻塞进程退出）",
                    CLOSE_JOIN_TIMEOUT,
                )
        self._thread = None
        self._stop_event = None
        self._cancel_pending()

    def _declare_dispatcher(self) -> None:
        setter = getattr(self._supervisor, "set_dispatcher", None)
        if not callable(setter):
            return
        try:
            setter(self._dispatch_from_consumer)
        except Exception:  # noqa: BLE001 - 声明失败不阻塞消费（测试替身可能没有）
            logger.exception("supervisor.set_dispatcher 调用失败（消费仍会继续）")

    def _consume(self, stop_event: threading.Event) -> None:
        """消费线程主体：阻塞 ``get`` + 投回事件循环（docs/03 §5）。"""
        while not stop_event.is_set():
            try:
                # 每轮重新读属性：supervisor 每次 spawn 子进程都会换一对新 Queue，
                # 缓存 Queue 对象会一直阻塞在旧队列上（M1-08 实测）。
                event_queue = self._supervisor.event_queue
            except Exception:  # noqa: BLE001 - 取队列都失败说明 supervisor 已不可用
                logger.exception("读取 supervisor.event_queue 失败，消费线程退出")
                return
            try:
                event = event_queue.get(timeout=CONSUMER_POLL_INTERVAL)
            except queue.Empty:
                continue
            except (OSError, ValueError, EOFError):
                # 队列被关闭 / 正在被换掉：退避后重读属性，停止标志仍能唤醒。
                time.sleep(CONSUMER_ERROR_SLEEP)
                continue
            self._dispatch_from_consumer(event, stop_event)

    def _dispatch_from_consumer(
        self, event: Any, stop_event: Optional[threading.Event] = None
    ) -> None:
        """消费线程侧入口：把事件投回事件循环。

        同时作为 ``supervisor.set_dispatcher`` 声明的 dispatcher（签名只要求接收事件
        dict）。事件循环尚未绑定时先缓存（:attr:`_deferred`），等第一次异步调用绑定
        loop 后补投 —— 保证 ``start_consumer()`` 早于 ``await supervisor.start()``
        时 READY 不会丢。
        """
        if self._closing or (stop_event is not None and stop_event.is_set()):
            return
        loop = self._resolve_loop()
        if loop is None:
            self._defer_event(event)
            return
        try:
            loop.call_soon_threadsafe(self._handle_event, event)
        except RuntimeError:
            logger.warning("事件循环不可用，丢弃事件：%r", event)

    def _defer_event(self, event: Any) -> None:
        with self._deferred_lock:
            if len(self._deferred) >= DEFERRED_EVENT_LIMIT:
                dropped = self._deferred.pop(0)
                logger.warning("事件循环尚未绑定，延迟缓存已满，丢弃最旧事件：%r", dropped)
            self._deferred.append(event)

    def _flush_deferred(self) -> None:
        with self._deferred_lock:
            pending, self._deferred = self._deferred, []
        if not pending:
            return
        logger.debug("事件循环已绑定，补投 %s 条延迟事件", len(pending))
        for event in pending:
            self._handle_event(event)

    # ------------------------------------------------------------------
    # 事件处理（事件循环线程）
    # ------------------------------------------------------------------

    def _handle_event(self, event: Any) -> None:
        """事件循环侧的统一入口：先内部处理（Future / 异步解析），再走分派表。"""
        if not isinstance(event, dict):
            logger.warning("忽略非法事件（不是 dict）：%r", event)
            return
        event_type = event.get("type")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        if event_type == "CMD_RESULT":
            self._handle_cmd_result(payload)
        elif event_type == "CALLBACK":
            self._handle_callback(payload)
        # READY / PONG / FATAL 已由 handle_* 自己写崩溃现场，避免重复计数。
        if event_type not in ("READY", "PONG", "FATAL") and self._note_event is not None:
            try:
                self._note_event(event)
            except Exception:  # noqa: BLE001 - 崩溃现场记录不能打断分派
                logger.exception("supervisor.note_event 调用失败（已吞掉）")
        self._call_handlers(event_type, payload)

    def _handle_cmd_result(self, payload: dict) -> None:
        """把 ``CMD_RESULT`` 兑现到对应 Future；迟到结果丢弃并记 warning。"""
        cmd_id = payload.get("cmd_id")
        if not isinstance(cmd_id, str):
            logger.warning("CMD_RESULT 缺少合法的 cmd_id，丢弃：%r", payload)
            return
        future = self._pending_cmds.pop(cmd_id, None)
        with contextlib.suppress(Exception):
            self._supervisor.release_command(cmd_id)
        if future is None:
            logger.warning(
                "丢弃迟到的 CMD_RESULT：cmd_id=%s 不在待决表（已超时 / 已取消 / 非本客户端所发）",
                cmd_id,
            )
            return
        if future.done():
            logger.warning("CMD_RESULT 到达时 Future 已结束（cmd_id=%s），丢弃", cmd_id)
            return
        if payload.get("ok"):
            future.set_result(payload.get("data"))
        else:
            future.set_exception(self._command_error(payload))

    def _handle_callback(self, payload: dict) -> None:
        """``CALLBACK``：喂给内部异步解析，并（对 ConnectionInfo）缓存分辨率信息。"""
        details = payload.get("details")
        msg = self._coerce_msg(payload.get("msg"))
        if msg is Message.ConnectionInfo:
            self._note_resolution(details)
        elif msg is Message.AsyncCallInfo:
            self._resolve_async(details)

    @staticmethod
    def _coerce_msg(raw: Any) -> Optional[Message]:
        try:
            return Message(int(raw))
        except (TypeError, ValueError):
            logger.warning("CALLBACK 的 msg 不是已知内核消息类型：%r", raw)
            return None

    def _note_resolution(self, details: Any) -> None:
        """从 ConnectionInfo 的 ResolutionGot / ResolutionInfo 记住分辨率。

        只用于决定缺省截图文件后缀（有分辨率 → ``.jpg``，否则裸字节 ``.bgr`` /
        ``.rgb``）；连接成败不认 ConnectionInfo，那是 ``AsyncCallInfo`` 的事。
        """
        if not isinstance(details, dict):
            return
        if details.get("what") not in ("ResolutionGot", "ResolutionInfo"):
            return
        inner = details.get("details")
        if not isinstance(inner, dict):
            inner = details
        try:
            width, height = int(inner["width"]), int(inner["height"])
        except (KeyError, TypeError, ValueError):
            return
        if width > 0 and height > 0:
            self._last_resolution = (width, height)

    # ------------------------------------------------------------------
    # 两级 Future：第二级的解析与兑现
    # ------------------------------------------------------------------

    def _resolve_async(self, details: Any) -> bool:
        """把一条 ``AsyncCallInfo`` 兑现到 ``_pending_async`` 里的 Future。

        宽容解析（键名以 M1-01 真机实测为准，其余为候选）：关联键取回调 JSON
        **顶层** ``async_call_id``，成功标志取 ``details.ret``（即回调事件 payload 的
        ``details.details.ret``）。都取不到时记**完整原文** warning 并让所有等待方走
        回退路径，绝不猜一个结果。

        :return: ``True`` = 已兑现或已缓存（回调先于受理结果到达）；``False`` = 丢弃。
        """
        raw = self._raw_text(details)
        payload = details
        if isinstance(details, str):
            try:
                payload = json.loads(details)
            except (TypeError, ValueError):
                payload = None
        if not isinstance(payload, dict):
            logger.warning("AsyncCallInfo 载荷不是 JSON 对象，无法解析，完整原文：%s", raw)
            self._fail_async_waiters("AsyncCallInfo 载荷不是 JSON 对象")
            return False

        call_id = self._normalize_call_id(self._pick(payload, _CALL_ID_PATHS))
        if call_id is None:
            logger.warning(
                "AsyncCallInfo 载荷缺少可识别的 async_call_id（候选键 %s），完整原文：%s",
                "/".join(path[-1] for path in _CALL_ID_PATHS),
                raw,
            )
            self._fail_async_waiters("AsyncCallInfo 载荷缺少 async_call_id")
            return False

        result = self._pick(payload, _RESULT_PATHS)
        if result is None:
            logger.warning(
                "AsyncCallInfo 载荷缺少可识别的结果字段（ret/result/success/ok），"
                "async_call_id=%s，完整原文：%s",
                call_id,
                raw,
            )
            self._fail_async_waiters("AsyncCallInfo 载荷缺少结果字段")
            return False

        future = self._pending_async.pop(call_id, None)
        if future is None:
            # 回调先于 CMD_RESULT 到达（子进程同步回调 + 命令循环随后才回受理结果）：
            # 缓存结果，等 _await_async_call / connect 登记等待者时立即兑现。
            self._buffer_early_async(call_id, result)
            logger.debug(
                "AsyncCallInfo 先于命令受理结果到达，已缓存：async_call_id=%s ret=%r what=%r",
                call_id,
                result,
                payload.get("what"),
            )
            return True
        if future.done():
            logger.warning("AsyncCallInfo 到达时 Future 已结束（async_call_id=%s），丢弃", call_id)
            return False
        future.set_result(result)
        return True

    def _fail_async_waiters(self, reason: str) -> None:
        """解析失败时唤醒全部异步等待者，让它们走回退路径（载荷结构不可信则无法路由）。"""
        for call_id in list(self._pending_async):
            future = self._pending_async.pop(call_id)
            if not future.done():
                future.set_exception(_AsyncCallUnparsed(reason))

    def _register_async_waiter(self, call_id: Any, future: asyncio.Future) -> None:
        """登记第二级等待者；若结果已先到，立即用缓存兑现。"""
        buffered = self._pop_early_async(call_id)
        if buffered is not None:
            future.set_result(buffered)
            return
        self._pending_async[call_id] = future

    def _buffer_early_async(self, call_id: Any, result: Any) -> None:
        now = time.monotonic()
        for key in [
            key
            for key, (_, at) in self._early_async.items()
            if now - at > EARLY_ASYNC_TTL
        ]:
            self._early_async.pop(key, None)
        while len(self._early_async) >= EARLY_ASYNC_LIMIT:
            self._early_async.pop(next(iter(self._early_async)), None)
        self._early_async[call_id] = (result, now)

    def _pop_early_async(self, call_id: Any) -> Any:
        entry = self._early_async.pop(call_id, None)
        if entry is None:
            return None
        result, at = entry
        if time.monotonic() - at > EARLY_ASYNC_TTL:
            logger.warning(
                "丢弃过期的异步结果缓存（async_call_id=%s，已缓存 %.1fs）",
                call_id,
                time.monotonic() - at,
            )
            return None
        return result

    # ------------------------------------------------------------------
    # 命令投递：就绪门禁 + cmd_id → Future
    # ------------------------------------------------------------------

    async def _send(
        self, type_name: str, payload: Optional[dict] = None, timeout: Any = None
    ) -> Any:
        """投递一条命令并等 ``CMD_RESULT``（第一级 Future）。

        先做未就绪门禁（不进队列），再 ``make_command`` → ``track_command`` → put →
        ``await`` Future。超时 pop + ``set_exception`` + ``release_command``；
        ``ok=False`` 时按内核回传的错误码抛 ``AppError``。
        """
        self._require_ready(type_name)
        loop = self._ensure_loop()
        command = make_command(type_name, **(payload or {}))
        cmd_id = command["cmd_id"]
        future = loop.create_future()
        self._pending_cmds[cmd_id] = future
        self._supervisor.track_command(command)
        try:
            self._supervisor.cmd_queue.put(command)
        except Exception as exc:  # noqa: BLE001 - 队列坏掉必须还原待决表
            self._pending_cmds.pop(cmd_id, None)
            with contextlib.suppress(Exception):
                self._supervisor.release_command(cmd_id)
            raise AppError(
                ErrorCode.CORE_COMMAND_FAILED,
                f"命令 {type_name} 入队失败：{exc}",
                {"cmd_id": cmd_id, "type": type_name},
            ) from exc

        effective = self._timeout_for(type_name, timeout)
        try:
            # shield：超时由这里显式 set_exception，而不是让 wait_for 取消 Future，
            # 语义与 docs/03 §5「超时后 pop 并 set_exception」一致。
            return await asyncio.wait_for(asyncio.shield(future), effective)
        except asyncio.TimeoutError:
            self._pending_cmds.pop(cmd_id, None)
            with contextlib.suppress(Exception):
                self._supervisor.release_command(cmd_id)
            error = AppError(
                ErrorCode.CORE_COMMAND_TIMEOUT,
                f"内核命令 {type_name} 超时（{effective:g}s）：超时不代表子进程已死，"
                "存活判定归 supervisor 心跳",
                {"cmd_id": cmd_id, "type": type_name, "timeout": effective},
            )
            if not future.done():
                future.set_exception(error)
                # 异常由本协程抛出，Future 侧只是标记终态；取回一次避免 asyncio 的
                # "Future exception was never retrieved" 告警。
                with contextlib.suppress(BaseException):
                    future.exception()
            raise error from None
        except asyncio.CancelledError:
            self._pending_cmds.pop(cmd_id, None)
            with contextlib.suppress(Exception):
                self._supervisor.release_command(cmd_id)
            if not future.done():
                future.cancel()
            raise

    def _require_ready(self, type_name: str) -> None:
        """未就绪门禁：立刻抛领域异常，不进队列等待（docs/03 §5）。"""
        state = self._coerce_state(self._supervisor.state)
        if state is None or state is CoreState.READY:
            return
        code = _STATE_GATE.get(state)
        if code is None:
            return
        raise AppError(
            code,
            f"{_STATE_GATE_MESSAGE[code]}（state={state.value}，命令 {type_name}）",
            {"state": state.value, "type": type_name},
        )

    @staticmethod
    def _coerce_state(state: Any) -> Optional[CoreState]:
        if isinstance(state, CoreState):
            return state
        try:
            return CoreState(state)
        except (TypeError, ValueError):
            logger.warning("无法识别的内核状态 %r，跳过就绪门禁", state)
            return None

    @staticmethod
    def _timeout_for(type_name: str, timeout: Any) -> float:
        if timeout is not None:
            value = float(timeout)
        else:
            value = float(CMD_TIMEOUTS.get(type_name, DEFAULT_QUERY_TIMEOUT))
        if value <= 0:
            raise ValueError(f"命令 {type_name} 的超时必须为正: {value}")
        return value

    @staticmethod
    def _command_error(payload: dict) -> AppError:
        error = payload.get("error")
        if not isinstance(error, dict):
            error = {}
        raw_code = error.get("code")
        code = ErrorCode.CORE_COMMAND_FAILED
        if raw_code:
            try:
                code = ErrorCode(str(raw_code))
            except ValueError:
                logger.warning(
                    "CMD_RESULT 返回未登记的错误码 %r，回退 CORE_COMMAND_FAILED", raw_code
                )
        message = str(error.get("message") or "内核命令执行失败")
        return AppError(code, message, {"cmd_id": payload.get("cmd_id"), "error": error})

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """绑定当前运行的 loop；首次绑定后补投延迟事件。"""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is None:
            loop = self._loop
            if loop is None or loop.is_closed():
                raise AppError(
                    ErrorCode.CORE_NOT_READY,
                    "CoreClient 的异步方法必须在事件循环内调用（或在构造时传入 loop=）",
                    {"state": self._state_value()},
                )
            return loop
        if self._loop is not running:
            if self._loop is not None and not self._loop.is_closed():
                logger.warning("CoreClient 改绑事件循环：旧 loop 与当前运行的 loop 不同")
            self._loop = running
            self._flush_deferred()
        return running

    def _resolve_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        """取可投递的 loop：显式传入的优先，其次当前运行的 loop，都没有则返回 None。"""
        loop = self._loop
        if loop is not None and not loop.is_closed():
            return loop
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        self._loop = loop
        return loop

    def _state_value(self) -> str:
        state = self._supervisor.state
        return state.value if isinstance(state, CoreState) else str(state)

    def _cancel_pending(self) -> None:
        loop = self._loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None and running is loop:
            self._cancel_pending_now()
            return
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._cancel_pending_now)
                return
            except RuntimeError:
                pass
        self._cancel_pending_now()

    def _cancel_pending_now(self) -> None:
        """取消所有未决 Future 并注销长命令登记（必须在 loop 线程执行）。"""
        for cmd_id, future in list(self._pending_cmds.items()):
            self._pending_cmds.pop(cmd_id, None)
            with contextlib.suppress(Exception):
                self._supervisor.release_command(cmd_id)
            if not future.done():
                future.cancel()
        for call_id, future in list(self._pending_async.items()):
            self._pending_async.pop(call_id, None)
            if not future.done():
                future.cancel()
        self._early_async.clear()

    # ------------------------------------------------------------------
    # 两级 Future：通用等待
    # ------------------------------------------------------------------

    async def _await_async_call(
        self,
        type_name: str,
        payload: Optional[dict] = None,
        *,
        accept_timeout: Any = None,
        result_timeout: Any = None,
    ) -> Any:
        """第一级等受理结果（``async_call_id``），第二级等 ``AsyncCallInfo`` 回调。

        ``CLICK`` / ``SCREENCAP`` 复用本方法；``CONNECT`` 因需要解析失败时回退
        ``CONNECTED`` 轮询，单独实现但共享同一套登记 / 缓存逻辑。
        """
        loop = self._ensure_loop()
        accept = self._accept_timeout if accept_timeout is None else float(accept_timeout)
        result = self._connect_timeout if result_timeout is None else float(result_timeout)

        data = await self._send(type_name, payload, timeout=accept)
        call_id = self._normalize_call_id(self._extract_call_id(data))
        if call_id is None:
            raise AppError(
                ErrorCode.CORE_COMMAND_FAILED,
                f"{type_name} 的 CMD_RESULT 未返回 async_call_id：{data!r}",
                {"type": type_name, "data": data},
            )

        future = loop.create_future()
        self._register_async_waiter(call_id, future)
        try:
            return await asyncio.wait_for(future, result)
        except asyncio.TimeoutError:
            raise AppError(
                ErrorCode.CORE_COMMAND_TIMEOUT,
                f"异步调用 {type_name} 的结果回调超时（{result:g}s，async_call_id={call_id}）",
                {"type": type_name, "async_call_id": call_id, "timeout": result},
            ) from None
        except _AsyncCallUnparsed as exc:
            raise AppError(
                ErrorCode.CORE_COMMAND_FAILED,
                f"AsyncCallInfo 载荷无法解析，{type_name} 的结果未知：{exc}",
                {"type": type_name, "async_call_id": call_id},
            ) from exc
        finally:
            self._pending_async.pop(call_id, None)

    @staticmethod
    def _extract_call_id(data: Any) -> Any:
        """从 ``CMD_RESULT.data`` 取受理编号（实测是 ``{"async_call_id": int}``）。"""
        if data is None or isinstance(data, bool):
            return None
        if isinstance(data, int):
            return data
        if isinstance(data, dict):
            return CoreClient._pick(data, tuple((key,) for key in _CALL_ID_KEYS))
        return None

    @staticmethod
    def _normalize_call_id(value: Any) -> Any:
        """受理编号 / 回调编号的规范化：能转 int 就转，保证两级映射键一致。"""
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            text = str(value).strip()
            return text or None

    @staticmethod
    def _pick(mapping: dict, paths: tuple[tuple[str, ...], ...]) -> Any:
        """按候选路径依次取值；取到 ``None`` 视为没取到，继续试下一条。"""
        for path in paths:
            current: Any = mapping
            for key in path:
                if not isinstance(current, dict) or key not in current:
                    current = None
                    break
                current = current[key]
            if current is not None:
                return current
        return None

    @staticmethod
    def _raw_text(details: Any) -> str:
        if isinstance(details, str):
            return details
        try:
            return json.dumps(details, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return repr(details)

    # ------------------------------------------------------------------
    # 公开异步命令（docs/02 §3.1）
    # ------------------------------------------------------------------

    async def connect(
        self,
        adb_path: str,
        address: str,
        config: str = "General",
        timeout: Any = None,
    ) -> bool:
        """异步连接设备（两级 Future，docs/08 §5.2）。

        第一级只确认命令受理（``accept_timeout``，默认 10 秒，拿到 ``async_call_id``）；
        第二级等 ``msg=4`` 的 ``AsyncCallInfo``（``timeout`` 或构造参数
        ``connect_timeout``，默认 60 秒）。**不得用 ``msg=2`` 的 ConnectionInfo 代替
        结果** —— 它会在 AsyncCallInfo 之前就报出 ``what == "Connected"``。

        解析失败（载荷缺 ``async_call_id`` / 结果字段）时记完整原文 warning，并回退为
        按 ``poll_interval`` 轮询 ``CONNECTED`` 命令，上限仍是 ``timeout``。
        """
        result_timeout = self._connect_timeout if timeout is None else float(timeout)
        loop = self._ensure_loop()
        data = await self._send(
            "CONNECT",
            {
                "adb_path": str(adb_path),
                "address": str(address),
                "config": str(config),
                "block": True,
            },
            timeout=self._accept_timeout,
        )
        call_id = self._normalize_call_id(self._extract_call_id(data))
        if call_id is None:
            logger.warning(
                "CONNECT 的 CMD_RESULT 未返回 async_call_id（data=%r），回退 CONNECTED 轮询",
                data,
            )
            return await self._poll_connected(result_timeout)

        future = loop.create_future()
        self._register_async_waiter(call_id, future)
        try:
            try:
                return bool(await asyncio.wait_for(future, result_timeout))
            except asyncio.TimeoutError:
                raise AppError(
                    ErrorCode.CORE_COMMAND_TIMEOUT,
                    f"连接结果回调超时（{result_timeout:g}s，async_call_id={call_id}）",
                    {"async_call_id": call_id, "timeout": result_timeout},
                ) from None
            except _AsyncCallUnparsed as exc:
                logger.warning(
                    "AsyncCallInfo 载荷无法解析（%s），回退 CONNECTED 轮询判定连接状态", exc
                )
                return await self._poll_connected(result_timeout)
        finally:
            self._pending_async.pop(call_id, None)

    async def _poll_connected(self, timeout: float) -> bool:
        """回退路径：按 ``poll_interval`` 轮询 ``CONNECTED``，上限 ``timeout``。

        ``CONNECTED`` 走同步查询命令（``AsstConnected``），不依赖回调结构，是
        ``AsyncCallInfo`` 不可解析时的兜底判定（docs/08 §5.2）。到点仍未连上抛
        ``CORE_COMMAND_TIMEOUT``。
        """
        loop = self._ensure_loop()
        deadline = loop.time() + max(float(timeout), 0.0)
        interval = max(self._poll_interval, 0.0)
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise AppError(
                    ErrorCode.CORE_COMMAND_TIMEOUT,
                    f"连接状态无法确认：AsyncCallInfo 不可解析，CONNECTED 轮询在 {timeout:g}s 内未连上",
                    {"timeout": float(timeout)},
                )
            ok = False
            try:
                ok = bool(
                    await self._send(
                        "CONNECTED", {}, timeout=min(remaining, DEFAULT_QUERY_TIMEOUT)
                    )
                )
            except AppError as exc:
                logger.warning("CONNECTED 轮询失败：%s", exc)
            if ok:
                return True
            remaining = deadline - loop.time()
            if remaining <= 0:
                continue
            await asyncio.sleep(min(interval, remaining) if interval > 0 else 0)

    async def append_task(self, type_name: str, params: Optional[dict] = None) -> int:
        """追加任务，返回内核 ``task_id``。

        ``task_id == 0`` 会在 worker 侧被当成失败上报（缺必填参数时内核静默返回 0，
        M1-02 实测），因此正常返回的 id 一定非零。
        """
        return await self._send(
            "APPEND_TASK", {"type_name": str(type_name), "params": dict(params or {})}
        )

    async def set_task_params(self, task_id: int, params: dict) -> bool:
        """修改运行中任务的参数（内核 ``AsstSetTaskParams``）。"""
        return await self._send(
            "SET_TASK_PARAMS", {"task_id": int(task_id), "params": dict(params or {})}
        )

    async def start(self) -> bool:
        """启动任务链（``START``，超时 30 秒）。"""
        return await self._send("START")

    async def stop(self) -> bool:
        """停止任务链（``STOP``，超时 30 秒）。"""
        return await self._send("STOP")

    async def running(self) -> bool:
        """内核实例是否正在跑任务链（查询类，超时 5 秒）。"""
        return await self._send("RUNNING")

    async def click(self, x: int, y: int, block: bool = True) -> None:
        """异步点击（``AsstAsyncClick``，复用两级 Future）。"""
        await self._await_async_call(
            "CLICK", {"x": int(x), "y": int(y), "block": bool(block)}
        )

    async def screencap(self, save_to: Any = None, bgr: bool = False) -> Path:
        """触发内核截图并落盘，返回实际文件 :class:`~pathlib.Path`。

        先 ``SCREENCAP``（两级 Future 等内核截好），再 ``GET_IMAGE`` 让 worker 取图
        写盘 —— 图像字节不进 IPC（docs/02 §3.3），只回路径与元信息。``save_to`` 缺省
        时用 ``screencap_dir`` + ``uuid4().hex`` 生成，后缀由是否已知分辨率决定
        （已知 → ``.jpg``，未知 → 裸字节 ``.bgr`` / ``.rgb``）；最终以 worker 回传的
        ``data["path"]`` 为准（分辨率未知时 worker 会自行加后缀）。
        """
        save_to = self._prepare_save_to(save_to, bool(bgr))
        await self._await_async_call("SCREENCAP", {"block": True})
        data = await self._send("GET_IMAGE", {"bgr": bool(bgr), "save_to": save_to})
        if not isinstance(data, dict) or not data.get("path"):
            raise AppError(
                ErrorCode.CORE_COMMAND_FAILED,
                f"GET_IMAGE 未返回文件路径：{data!r}",
                {"data": data},
            )
        return Path(str(data["path"]))

    async def get_image(self, save_to: Any = None, bgr: bool = False) -> dict:
        """取最近一帧图并落盘，返回 worker 的 data dict（path/size/encoding/...）。

        只管 ``GET_IMAGE``（取图 + 落盘），不触发新的截图；需要新帧时用
        :meth:`screencap`。
        """
        save_to = self._prepare_save_to(save_to, bool(bgr))
        data = await self._send("GET_IMAGE", {"bgr": bool(bgr), "save_to": save_to})
        if not isinstance(data, dict) or not data.get("path"):
            raise AppError(
                ErrorCode.CORE_COMMAND_FAILED,
                f"GET_IMAGE 未返回文件路径：{data!r}",
                {"data": data},
            )
        return data

    async def back_to_home(self) -> bool:
        """返回主界面（``AsstBackToHome``）。"""
        return await self._send("BACK_TO_HOME")

    async def connected(self) -> bool:
        """同步查询连接状态（``AsstConnected``）；异步连接结果不认它，只用于回退判定。"""
        return await self._send("CONNECTED")

    async def get_uuid(self) -> str:
        """取设备 uuid（``AsstGetUUID``）。"""
        return await self._send("GET_UUID")

    async def get_tasks_list(self) -> Any:
        """取内核可提交的任务清单（``AsstGetTasksList``；本内核可能返回空）。"""
        return await self._send("GET_TASKS_LIST")

    async def get_version(self) -> str:
        """取内核版本字符串（``AsstGetVersion``）。"""
        return await self._send("GET_VERSION")

    async def resolve_stage(self, key: str) -> Optional[dict]:
        """按关卡代号取 ``AsstGetMapLevelKey`` 的结构；查不到返回 ``None``。"""
        return await self._send("GET_MAP_LEVEL_KEY", {"key": str(key)})

    def _prepare_save_to(self, save_to: Any, bgr: bool) -> str:
        """确定 ``GET_IMAGE`` 的落盘路径：显式传入原样用，缺省按规则生成并 mkdir。"""
        if save_to is not None:
            return str(save_to)
        directory = self._screencap_dir
        directory.mkdir(parents=True, exist_ok=True)
        suffix = ".jpg" if self._last_resolution else (".bgr" if bgr else ".rgb")
        return str(directory / f"{uuid.uuid4().hex}{suffix}")
