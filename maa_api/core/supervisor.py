"""CoreSupervisor：内核子进程的状态机、心跳、崩溃检测、退避重启与维护窗口（M1-08）。

职责边界（docs/03 §4、docs/02 §4、docs/13 ADR-02/ADR-03）：

- **本模块只管子进程生命周期**：spawn / 就绪等待 / 心跳 / 硬崩溃检测 / 退避重启 /
  维护窗口 / 优雅关闭。
- **不落库、不广播、不通知、不改流水线状态**（docs/03 §4.2 的第 1/3/4 步归
  M2/M4/M5）。崩溃现场（``last_crash``）与 ``on_crash`` / ``on_state_change`` 钩子
  就是给它们接的入口。
- **不读 ``event_queue``**：事件队列只允许一个消费者（M1-09 ``CoreClient`` 的消费
  线程）。supervisor 靠 :meth:`CoreSupervisor.set_dispatcher` 声明的消费者把
  ``READY`` / ``PONG`` / ``FATAL`` 回调进 :meth:`handle_ready` / :meth:`handle_pong`
  / :meth:`handle_fatal`；未声明消费者就 :meth:`start` 会 fail loud（抛
  ``AppError(CORE_START_FAILED)``），避免两个消费者抢事件。

状态机::

    STOPPED --start()--> STARTING --READY--> READY
    READY   --心跳失联 / 进程退出 / FATAL--> CRASHED
    CRASHED --退避到期--> RESTARTING --READY--> READY
    STARTING / CRASHED --失败预算耗尽 / 启动失败--> FAILED
    FAILED  --手动 start()/restart()--> STARTING / RESTARTING

崩溃判据是两条**独立**通道，任一触发即认定崩溃（docs/03 §4.1）：

1. **心跳**：每 :data:`HEARTBEAT_INTERVAL` 秒向 ``cmd_queue`` 投一条 ``PING(seq)``，
   连续 :data:`HEARTBEAT_FAILURES` 次没收到同 ``seq`` 的 ``PONG``（默认约 15 秒）判失联。
   心跳与业务命令共用同一条队列，因此它同时检测「命令循环被长任务卡死」——这是有意
   的：卡死 15 秒以上的内核对使用者与崩溃无异。已知长命令（:data:`LONG_COMMANDS` =
   ``LOAD_RESOURCE`` / ``CONNECT``）执行期间（``track_command`` 后尚未
   ``release_command``）跳过判定且不累计失败，否则会误判。
2. **进程存活**：轮询 ``Process.is_alive()`` 捕获段错误、OOM kill 这类不给事件的硬
   崩溃；``exitcode`` 区分正常退出（0）、信号杀死（负值，如 -9 = SIGKILL）与异常
   退出（MaaCore 会把 native 崩溃转成 exit 1 + ``crash.log``，M1-02 实测，因此判据
   是「非预期退出」而不是「exitcode 为负」）。

崩溃恢复（docs/02 §4、docs/03 §4.2）：置 ``CRASHED``、记 ``last_crash``（exitcode、
时间、最近 :data:`CRASH_EVENT_BUFFER` 条事件现场、恢复尝试序号）、在检测线程上调用
``on_crash`` 钩子（异常吞掉并记日志），然后按 ``backoff`` 退避重启：第 N 次重启用
``backoff[min(N-1, len-1)]``（默认 5s → 15s → 60s）。**连续失败预算
``max_restart_attempts`` 用尽后转 ``FAILED`` 并停止自动重启**，等前端手动
``start()`` / ``restart()``。没有退避的自动重启会因坏资源变成崩溃风暴。

退避预算只在**手动** ``start()`` / ``restart()`` 时复位，自动重启成功（READY）不复位
——「连续崩溃」要跨自动重启累计，否则「崩溃 → 重启 → 又崩」的循环永远到不了上限。
手动 ``restart(boot_config=...)`` 同时用于换掉 ``boot_config``（例如修复资源后重来）。

维护窗口（docs/03 §4.3）：``async with supervisor.acquire_maintenance():`` 是互斥的
（``asyncio.Lock``，并发进入串行化）。进入后 ``in_maintenance=True``、取消待执行的重启
定时、心跳失败计数清零；窗口内「子进程停止」是预期行为，不进 ``CRASHED``、不触发
重启。退出时恢复检测：若窗口内子进程已经死掉且不是主动 ``stop()``，立刻按崩溃处理
（``CRASHED`` → 退避 → ``RESTARTING``）。热更新、重装内核、修改 MAA 路径都在窗口内执行。

关机：先发 ``SHUTDOWN``，等 ``timeout``（默认 10 秒）→ ``terminate()`` 再等 5 秒 →
``kill()``，最后置 ``STOPPED``（docs/02 §4）。
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import logging
import multiprocessing
import threading
import time
from collections import deque
from enum import StrEnum
from typing import Any, AsyncIterator, Callable, Optional

from maa_api.core.protocol import LONG_COMMANDS, make_command
from maa_api.core.worker import core_worker_main
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "BACKOFF_SECONDS",
    "CRASH_EVENT_BUFFER",
    "CoreState",
    "CoreSupervisor",
    "HEARTBEAT_FAILURES",
    "HEARTBEAT_INTERVAL",
    "LONG_COMMANDS",
    "MAX_RESTART_ATTEMPTS",
]

logger = logging.getLogger(__name__)

#: 重启退避序列（秒）：第 N 次重启用 ``backoff[min(N-1, len(backoff)-1)]``（docs/02 §4）。
BACKOFF_SECONDS: tuple[float, ...] = (5.0, 15.0, 60.0)

#: 连续失败上限：自动重启用满这个次数后转 FAILED，停止自动重启（docs/02 §4）。
MAX_RESTART_ATTEMPTS: int = 5

#: 心跳间隔（秒）与连续失败阈值：5 秒一次 PING，连续 3 次没有同 seq 的 PONG（约 15 秒）判失联。
HEARTBEAT_INTERVAL: float = 5.0
HEARTBEAT_FAILURES: int = 3

#: 崩溃现场保留的最近事件条数。
CRASH_EVENT_BUFFER: int = 50

#: ``start()`` / ``restart()`` 等 READY 的默认上限（docs/03 §4.3 的 120 秒）。
START_TIMEOUT_SECONDS: float = 120.0

#: ``stop()`` 等 SHUTDOWN 的默认上限（docs/02 §4 的 10 秒）。
STOP_TIMEOUT_SECONDS: float = 10.0

#: ``terminate()`` 之后、``kill()`` 之前的等待（docs/02 §4 的 5 秒）。
TERMINATE_GRACE_SECONDS: float = 5.0

#: 重启前回收残留进程时的 terminate 等待上限。
REAP_TERMINATE_TIMEOUT: float = 2.0

#: 进程存活轮询间隔：SIGKILL 后约 0.05 秒内可观测到 exitcode（M1-02 实测）。
LIVENESS_POLL_INTERVAL: float = 0.05

#: 等待 READY 时的轮询间隔（async 轮询，不占用默认 executor）。
READY_POLL_INTERVAL: float = 0.02


class CoreState(StrEnum):
    """内核子进程状态（docs/02 §4 的状态机）。

    ``StrEnum`` 取值是小写短字符串，便于直接进 ``core_status`` 事件与错误
    ``details.state``（M1-09 的未就绪门禁按成员比较，不依赖字符串）。
    """

    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    CRASHED = "crashed"
    RESTARTING = "restarting"
    FAILED = "failed"


class CoreSupervisor:
    """MaaCore 子进程的生命周期管理者（docs/03 §4）。

    :param boot_config: 纯 dict（可 pickle），字段见
        :mod:`maa_api.core.worker` 的模块 docstring；``restart(boot_config=...)``
        可以整体换掉它（热更新 / 改 MAA 路径修复后重来）。
    :param context: ``multiprocessing`` 上下文，缺省 ``get_context("spawn")``；测试可注入。
    :param worker_target: 子进程入口，缺省 :func:`maa_api.core.worker.core_worker_main`；
        必须是模块级函数（spawn 只传限定名）。
    :param backoff: 重启退避序列，默认 :data:`BACKOFF_SECONDS`；测试可缩短到毫秒级。
    :param max_restart_attempts: 连续失败上限，默认 :data:`MAX_RESTART_ATTEMPTS`。
    :param heartbeat_interval: 心跳间隔，默认 :data:`HEARTBEAT_INTERVAL`。
    :param heartbeat_failures: 连续心跳失败阈值，默认 :data:`HEARTBEAT_FAILURES`。
    :param on_crash: 崩溃钩子，签名 ``fn(record: dict)``，在检测到崩溃的线程上同步
        调用；``record`` 就是 :attr:`last_crash` 的内容（``reason`` / ``exitcode`` /
        ``at`` / ``recent_events`` / ``attempt`` / ``pid`` / ``detail``）。异常会被
        吞掉并记日志——崩溃处理路径绝不能被钩子打断。M2 落库、M4 广播、M5 通知接这里。
    :param on_state_change: 状态变更钩子，签名 ``fn(state: CoreState)``；同样同步调用、
        异常吞掉。广播 ``core_status`` 的接入点。

    **队列按代轮换**：每次 spawn 都用全新的一对 ``Queue``（原因见
    :meth:`_rotate_queues`：``Queue.get()`` 阻塞期间持有内部读锁，子进程被 SIGKILL
    后锁不会释放，复用同一对队列会让下一个子进程永远收不到命令）。因此
    ``cmd_queue`` / ``event_queue`` 是「当前代」的队列，**消费线程必须每轮重新读
    ``supervisor.event_queue``（属性访问），不要缓存 Queue 对象**；状态切到
    STARTING / RESTARTING 时也应该重新读一次。旧一代残留的 ``PONG`` /
    ``CMD_RESULT`` 随旧队列一起被丢弃，新一代从空队列开始。
    """

    def __init__(
        self,
        boot_config: dict,
        *,
        context: Any = None,
        worker_target: Callable[..., None] = core_worker_main,
        backoff: tuple[float, ...] = BACKOFF_SECONDS,
        max_restart_attempts: int = MAX_RESTART_ATTEMPTS,
        heartbeat_interval: float = HEARTBEAT_INTERVAL,
        heartbeat_failures: int = HEARTBEAT_FAILURES,
        on_crash: Optional[Callable[[dict], None]] = None,
        on_state_change: Optional[Callable[[CoreState], None]] = None,
    ) -> None:
        if not isinstance(boot_config, dict) or not boot_config:
            raise ValueError("boot_config 必须是非空 dict（纯数据，见 worker 模块 docstring）")
        if not backoff:
            raise ValueError("backoff 不能为空：至少要有一档退避时长")
        if max_restart_attempts < 0:
            raise ValueError(f"max_restart_attempts 不能为负: {max_restart_attempts}")
        if heartbeat_interval <= 0 or heartbeat_failures <= 0:
            raise ValueError(
                f"心跳参数必须为正: interval={heartbeat_interval}, failures={heartbeat_failures}"
            )

        self._boot_config: dict = dict(boot_config)
        self._ctx = context if context is not None else multiprocessing.get_context("spawn")
        self._worker_target = worker_target
        self._backoff = tuple(float(item) for item in backoff)
        self._max_restart_attempts = int(max_restart_attempts)
        self._heartbeat_interval = float(heartbeat_interval)
        self._heartbeat_failures = int(heartbeat_failures)
        self._on_crash = on_crash
        self._on_state_change = on_state_change

        # 身份稳定的两条队列：消费者（M1-09）在启动时抓住 event_queue，不能换。
        self._cmd_queue = self._ctx.Queue()
        self._event_queue = self._ctx.Queue()

        self._lock = threading.RLock()
        self._state: CoreState = CoreState.STOPPED
        self._process: Optional[Any] = None
        self._dispatcher: Optional[Callable[[dict], Any]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # 最近事件环形缓冲（崩溃现场）：由 handle_* / note_event 填充。
        self._crash_events: deque[dict] = deque(maxlen=CRASH_EVENT_BUFFER)
        self._last_crash: Optional[dict] = None

        # 启动握手（dispatcher 线程写、start() 协程读）。
        self._ready_flag: Optional[threading.Event] = None
        self._ready_payload: Optional[dict] = None
        self._startup_failure: Optional[dict] = None
        self._awaiting_ready = False

        # 心跳状态。
        self._last_ping_seq: Optional[int] = None
        self._last_pong_seq: Optional[int] = None
        self._heartbeat_failure_count = 0
        self._in_flight: dict[str, Optional[str]] = {}

        # 监控线程（READY 后启动，崩溃 / 停止时结束）。
        self._monitor_stop: Optional[threading.Event] = None
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._liveness_thread: Optional[threading.Thread] = None
        # 子进程代际：每次 spawn 自增（见 :meth:`_spawn_process`）。监控线程绑定自己
        # 启动时的代际，换代后旧线程的迟到判定一律作废——这是 M1-15 修复的伪崩溃根因：
        # restart() 停掉旧进程时，旧 liveness 线程盯着的旧 ``Process`` 变成 dead，
        # 它会把这**次主动停止**记成 ``process_exit`` 并调度伪重启。
        self._generation = 0
        self._monitor_generation: Optional[int] = None

        # 退避重启定时器与维护窗口。
        self._restart_cancel: Optional[threading.Event] = None
        self._restart_thread: Optional[threading.Thread] = None
        self._restart_attempts = 0
        self._stop_requested = False
        self._in_maintenance = False
        self._maintenance_lock: Optional[asyncio.Lock] = None
        self._maintenance_lock_loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    # 公开属性
    # ------------------------------------------------------------------

    @property
    def cmd_queue(self) -> Any:
        """主 → 子命令队列（``multiprocessing.Queue``，身份跨重启稳定）。"""
        return self._cmd_queue

    @property
    def event_queue(self) -> Any:
        """子 → 主事件队列（``multiprocessing.Queue``，**只允许一个消费者**）。"""
        return self._event_queue

    @property
    def state(self) -> CoreState:
        """当前状态（线程 / 协程安全读）。"""
        with self._lock:
            return self._state

    @property
    def pid(self) -> Optional[int]:
        """当前（或最后一个）子进程 pid；从未启动过时为 ``None``。"""
        process = self._process
        return process.pid if process is not None else None

    @property
    def exitcode(self) -> Optional[int]:
        """子进程退出码；进程仍存活或从未启动过时为 ``None``。

        符号语义：0 = 正常退出、负值 = 信号杀死（-9 SIGKILL、-11 SIGSEGV）、正值 =
        异常退出（MaaCore 把 native 崩溃转成 exit 1 + crash.log，M1-02 实测）。
        """
        process = self._process
        return process.exitcode if process is not None else None

    @property
    def generation(self) -> int:
        """Monotonic subprocess generation number (used as ``pipeline.core_epoch``)."""
        with self._lock:
            return self._generation

    @property
    def last_crash(self) -> Optional[dict]:
        """最近一次崩溃记录（副本）或 ``None``。

        键：``reason`` / ``exitcode`` / ``at``（``time.time()``）/ ``recent_events``
        （崩溃前最近 :data:`CRASH_EVENT_BUFFER` 条事件摘要）/ ``attempt``（这是第几次
        恢复尝试，预算耗尽时可能大于 ``max_restart_attempts``）/ ``pid`` / ``detail``。
        """
        with self._lock:
            record = self._last_crash
            return dict(record) if record is not None else None

    @property
    def in_maintenance(self) -> bool:
        """是否处于维护窗口内（窗口内不取新流水线、不自动重启）。"""
        with self._lock:
            return self._in_maintenance

    # ------------------------------------------------------------------
    # 事件入口（唯一消费者回调进来）
    # ------------------------------------------------------------------

    def set_dispatcher(self, fn: Callable[[dict], Any]) -> None:
        """声明事件队列的唯一消费者（M1-09 ``CoreClient`` 的消费线程）。

        supervisor 自己不读 ``event_queue``；``fn`` 必须接收每条被消费的事件 dict。
        本类把它当作「已有消费者」的凭据：``start()`` / ``restart()`` 前没调用过就抛
        ``AppError(CORE_START_FAILED)``（fail loud），避免出现第二个消费者抢事件。
        消费者在消费每条事件后，应把 ``READY`` / ``PONG`` / ``FATAL`` 转给
        :meth:`handle_ready` / :meth:`handle_pong` / :meth:`handle_fatal`；其余事件
        （``CALLBACK`` / ``LOG`` / ``CMD_RESULT``）若希望进崩溃现场，转给
        :meth:`note_event`（supervisor 不解释语义）。

        **队列按代轮换**：消费循环必须每轮重新读 ``supervisor.event_queue``（不要
        把 Queue 对象缓存在局部变量里），否则内核重启后会一直阻塞在旧队列上。
        """
        if not callable(fn):
            raise TypeError(f"dispatcher 必须可调用: {fn!r}")
        with self._lock:
            self._dispatcher = fn

    def note_event(self, event: dict) -> None:
        """把一条事件摘要放进崩溃现场环形缓冲（不解释语义、不触发状态变更）。

        供消费者对 ``CALLBACK`` / ``LOG`` / ``CMD_RESULT`` 等事件调用；``handle_*``
        内部已自动记录对应事件，重复调用会重复计数。
        """
        if not isinstance(event, dict):
            return
        summary = {
            "type": event.get("type"),
            "ts": event.get("ts", time.time()),
            "payload": event.get("payload") or {},
        }
        with self._lock:
            self._crash_events.append(summary)

    def handle_ready(self, payload: Optional[dict]) -> None:
        """收到 ``READY``（消费者调用）：置 READY、启动心跳与存活监控线程。"""
        payload = dict(payload or {})
        self.note_event({"type": "READY", "ts": time.time(), "payload": payload})
        with self._lock:
            process = self._process
            ready_pid = payload.get("pid")
            if process is not None and ready_pid is not None and ready_pid != process.pid:
                logger.warning("忽略过期 READY：payload.pid=%s 当前 pid=%s", ready_pid, process.pid)
                return
            self._ready_payload = payload
            if self._ready_flag is not None:
                self._ready_flag.set()
            self._awaiting_ready = False
            if self._state in (CoreState.STARTING, CoreState.RESTARTING):
                self._set_state_locked(CoreState.READY)
            elif self._state is not CoreState.READY:
                # STOPPED / FAILED / CRASHED：迟到的 READY，不复活。
                logger.warning("状态 %s 下收到 READY，忽略", self._state.value)
                return
            self._heartbeat_failure_count = 0
            self._start_monitors_locked()
        logger.info("内核就绪：pid=%s version=%s", self.pid, payload.get("version"))

    def handle_fatal(self, payload: Optional[dict]) -> None:
        """收到 ``FATAL``（消费者调用）：启动期交给 ``start()``，运行期按崩溃处理。"""
        payload = dict(payload or {})
        self.note_event({"type": "FATAL", "ts": time.time(), "payload": payload})
        with self._lock:
            awaiting = self._awaiting_ready
            state = self._state
            if awaiting:
                # start() / restart() 正在等 READY：把失败交给等待方（由它决定置
                # FAILED 并抛 AppError，或按自动重启预算继续）。
                self._startup_failure = payload
                return
        if state in (CoreState.READY, CoreState.RESTARTING, CoreState.STARTING):
            self._handle_crash(reason="fatal", exitcode=self.exitcode, detail=payload.get("error"))

    def handle_pong(self, payload: Optional[dict]) -> None:
        """收到 ``PONG``（消费者调用）：seq 与本轮 PING 一致时清零失败计数。"""
        seq = (payload or {}).get("seq")
        self.note_event({"type": "PONG", "ts": time.time(), "payload": {"seq": seq}})
        with self._lock:
            self._last_pong_seq = seq
            if self._last_ping_seq is not None and seq == self._last_ping_seq:
                self._heartbeat_failure_count = 0

    # ------------------------------------------------------------------
    # 长命令登记（心跳放宽）
    # ------------------------------------------------------------------

    def track_command(self, cmd: dict) -> None:
        """登记一条已投递的命令（M1-09 在 ``put`` 前调用）。

        只有 :data:`LONG_COMMANDS` 里的类型会影响心跳判定：只要还有一条未
        ``release_command`` 的长命令，本轮心跳检查直接跳过且不累计失败。
        """
        if not isinstance(cmd, dict):
            return
        cmd_id = cmd.get("cmd_id")
        if not cmd_id:
            return
        with self._lock:
            self._in_flight[str(cmd_id)] = cmd.get("type")

    def release_command(self, cmd_id: Optional[str]) -> None:
        """注销一条命令（收到 ``CMD_RESULT`` 或超时后由 M1-09 调用）。"""
        if not cmd_id:
            return
        with self._lock:
            self._in_flight.pop(str(cmd_id), None)

    def _long_command_in_flight(self) -> bool:
        with self._lock:
            return any(kind in LONG_COMMANDS for kind in self._in_flight.values())

    # ------------------------------------------------------------------
    # 生命周期：start / stop / restart
    # ------------------------------------------------------------------

    async def start(self, wait_ready: bool = True, timeout: float = START_TIMEOUT_SECONDS) -> None:
        """spawn 子进程并（默认）等 ``READY``。

        手动 ``start()`` 会复位退避预算。已经 READY 时幂等返回。

        :raises AppError: 未设置 dispatcher、并发启动、启动失败（超时 / ``FATAL`` /
            子进程提前退出）都抛 ``CORE_START_FAILED``，并把状态置 ``FAILED``。
        """
        self._require_dispatcher()
        with self._lock:
            if (
                self._state is CoreState.READY
                and self._process is not None
                and self._process.is_alive()
            ):
                logger.info("内核已处于 READY，start() 幂等返回")
                return
            if self._awaiting_ready:
                raise AppError(
                    ErrorCode.CORE_START_FAILED,
                    "内核正在启动中，拒绝并发 start()",
                    {"state": self._state.value},
                )
            self._loop = asyncio.get_running_loop()
            self._restart_attempts = 0
        self._cancel_restart_timer()
        try:
            await self._bring_up(restarting=False, wait_ready=wait_ready, timeout=timeout)
        except AppError as exc:
            self._record_start_failure(exc)
            self._set_state(CoreState.FAILED)
            raise

    async def stop(self, graceful: bool = True, timeout: float = STOP_TIMEOUT_SECONDS) -> None:
        """关闭子进程并置 ``STOPPED``。

        先发 ``SHUTDOWN`` 等 ``timeout`` 秒；超时 ``terminate()`` 再等 5 秒；再超时
        ``kill()``（docs/02 §4）。主动停止不会被崩溃检测当成故障，也会取消待执行的
        自动重启。
        """
        with self._lock:
            self._stop_requested = True
        self._cancel_restart_timer()
        # 先让监控线程确定性退出，再动子进程：stop_event 先于任何进程操作置位，监控
        # 线程从 ``wait()`` 返回 True 就直接退出、不再进入判定体，因此主动停止不可能
        # 被自己这一代的监控线程判成崩溃。
        self._stop_monitors()
        await self._stop_process(graceful=graceful, timeout=timeout)
        self._set_state(CoreState.STOPPED)
        logger.info("内核已停止：pid=%s exitcode=%s", self.pid, self.exitcode)

    async def restart(self, boot_config: Optional[dict] = None) -> None:
        """手动重启：停掉当前子进程、换 ``boot_config``（可选）、复位退避预算、再启动。

        热更新 / 重装内核 / 修改 MAA 路径应在 ``acquire_maintenance()`` 窗口内调用
        本方法（或先 ``stop()`` 再 ``start()``），避免被崩溃检测误判。
        """
        self._require_dispatcher()
        if boot_config is not None:
            if not isinstance(boot_config, dict) or not boot_config:
                raise ValueError("boot_config 必须是非空 dict")
            with self._lock:
                self._boot_config = dict(boot_config)
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._restart_attempts = 0
            self._stop_requested = False
            self._set_state_locked(CoreState.RESTARTING)
        self._cancel_restart_timer()
        # 关键顺序（M1-15 修复）：先停上一代监控线程，再优雅停止旧进程。旧 liveness
        # 线程持有旧 ``Process`` 引用，若它在旧进程退出（exitcode=0）后仍存活，就会把
        # 这次主动停止记成 ``process_exit`` 并调度伪重启，``_reap_process()`` 进而
        # terminate() 掉刚起来的新进程；包在维护窗口里也只是被推迟到窗口退出后补记。
        # 这里不靠等待/sleep：stop_event 置位先于 SHUTDOWN 下发，线程一旦从 wait()
        # 返回就不会再看 ``is_alive()``。
        self._stop_monitors()
        await self._stop_process(graceful=True, timeout=STOP_TIMEOUT_SECONDS)
        try:
            await self._bring_up(restarting=True, wait_ready=True, timeout=START_TIMEOUT_SECONDS)
        except AppError as exc:
            self._record_start_failure(exc)
            self._set_state(CoreState.FAILED)
            raise

    # ------------------------------------------------------------------
    # 维护窗口
    # ------------------------------------------------------------------

    @contextlib.asynccontextmanager
    async def acquire_maintenance(self) -> AsyncIterator["CoreSupervisor"]:
        """互斥的维护窗口（docs/03 §4.3）。

        语义：进入后调用方应停止取新流水线，supervisor 同时关闭崩溃自动重启——
        窗口内主动停 / 杀子进程是**预期行为而非崩溃**，不进 ``CRASHED``、不调
        ``on_crash``、不重启；待执行的重启定时被取消，心跳失败计数清零。热更新、
        重装内核、修改 MAA 路径都必须在窗口内执行。

        并发进入会串行化（``asyncio.Lock``）。退出时恢复自动重启：若窗口内子进程
        已经死掉且不是主动 ``stop()``，立即按崩溃处理（``CRASHED`` → 退避 →
        ``RESTARTING``）；窗口内已经 ``stop()``（状态 ``STOPPED``）或已经重新
        ``start()``（状态 ``READY``）的，保持调用方的结果不动。
        """
        lock = self._maintenance_lock_for_loop()
        async with lock:
            with self._lock:
                self._in_maintenance = True
                self._heartbeat_failure_count = 0
            self._cancel_restart_timer()
            logger.info("进入内核维护窗口：暂停崩溃自动重启（热更新 / 重装内核 / 改 MAA 路径）")
            try:
                yield self
            finally:
                with self._lock:
                    self._in_maintenance = False
                logger.info("退出内核维护窗口：恢复崩溃检测与自动重启")
                self._reconcile_after_maintenance()

    def _maintenance_lock_for_loop(self) -> asyncio.Lock:
        """按当前事件循环取维护锁（同一实例跨 ``asyncio.run`` 复用时重建）。"""
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._maintenance_lock is None or self._maintenance_lock_loop is not loop:
                self._maintenance_lock = asyncio.Lock()
                self._maintenance_lock_loop = loop
            return self._maintenance_lock

    def _reconcile_after_maintenance(self) -> None:
        """退出窗口时把「窗口内死掉的子进程」补记为崩溃，恢复自动重启。"""
        process = self._process
        with self._lock:
            if self._stop_requested:
                return
            if self._state in (CoreState.STOPPED, CoreState.FAILED, CoreState.CRASHED):
                return
        if process is not None and not process.is_alive():
            self._handle_crash(
                reason="process_exit",
                exitcode=process.exitcode,
                detail="维护窗口内子进程已退出（窗口退出后按崩溃处理）",
            )

    # ------------------------------------------------------------------
    # 崩溃处理与退避重启
    # ------------------------------------------------------------------

    def _handle_crash(
        self,
        *,
        reason: str,
        exitcode: Optional[int],
        detail: Any = None,
        generation: Optional[int] = None,
    ) -> bool:
        """认定崩溃：置 ``CRASHED``、记现场、调钩子、按预算调度重启。

        :param generation: 上报这条判定的监控线程所绑定的内核代际；``None`` 表示
            「当前代际」（``handle_fatal`` 与维护窗口退出时的补记）。旧代际的迟到
            判定直接作废：它盯着的 ``Process`` 已被换代回收，不代表当前生命周期。
        :return: ``True`` = 已按崩溃处理；``False`` = 被抑制（主动停止中 / 维护窗口 /
            状态已经失效 / 旧代际迟到判定）。存活轮询线程据此决定是否继续轮询
            （维护窗口内要继续）。
        """
        record: Optional[dict] = None
        hook: Optional[Callable[[dict], None]] = None
        with self._lock:
            if generation is not None and generation != self._generation:
                logger.debug(
                    "忽略第 %s 代监控线程的迟到崩溃判定（当前第 %s 代）：%s",
                    generation,
                    self._generation,
                    reason,
                )
                return False
            if self._stop_requested:
                return False
            if self._in_maintenance:
                logger.info("维护窗口内检测到子进程停止（预期行为，不按崩溃处理）：%s", reason)
                return False
            if self._state in (CoreState.STOPPED, CoreState.FAILED, CoreState.CRASHED):
                return False
            self._stop_monitors_locked()
            attempt = self._restart_attempts + 1
            record = self._build_crash_record_locked(reason, exitcode, detail, attempt)
            self._last_crash = record
            self._set_state_locked(CoreState.CRASHED)
            if self._restart_attempts >= self._max_restart_attempts:
                logger.error(
                    "内核连续失败已达上限 %s，停止自动重启（FAILED）：%s",
                    self._max_restart_attempts,
                    record,
                )
                self._set_state_locked(CoreState.FAILED)
            else:
                self._restart_attempts = attempt
                delay = self._backoff[min(attempt - 1, len(self._backoff) - 1)]
                self._start_restart_timer_locked(attempt, delay)
            hook = self._on_crash
        if hook is not None:
            self._call_hook(hook, record, "on_crash")
        return True

    def _register_failed_restart(self, *, detail: Any, exitcode: Optional[int] = None) -> None:
        """自动重启没能起到 READY（超时 / FATAL / 起不来）：再记一次失败并决定是否继续。"""
        record: Optional[dict] = None
        hook: Optional[Callable[[dict], None]] = None
        with self._lock:
            if self._stop_requested or self._in_maintenance:
                return
            if self._state in (CoreState.STOPPED, CoreState.FAILED, CoreState.CRASHED):
                return
            self._stop_monitors_locked()
            record = self._build_crash_record_locked(
                "restart_failed", exitcode, detail, self._restart_attempts
            )
            self._last_crash = record
            self._set_state_locked(CoreState.CRASHED)
            if self._restart_attempts >= self._max_restart_attempts:
                logger.error(
                    "自动重启连续失败达上限 %s，转 FAILED：%s", self._max_restart_attempts, detail
                )
                self._set_state_locked(CoreState.FAILED)
            else:
                self._restart_attempts += 1
                attempt = self._restart_attempts
                delay = self._backoff[min(attempt - 1, len(self._backoff) - 1)]
                self._start_restart_timer_locked(attempt, delay)
            hook = self._on_crash
        if hook is not None:
            self._call_hook(hook, record, "on_crash")

    def _record_start_failure(self, exc: AppError) -> None:
        """手动 ``start()`` / ``restart()`` 失败：留一条崩溃记录供落库 / 告警。"""
        details = exc.details if isinstance(exc.details, dict) else {}
        record: Optional[dict] = None
        hook: Optional[Callable[[dict], None]] = None
        with self._lock:
            record = self._build_crash_record_locked(
                details.get("reason", "start_failed"),
                details.get("exitcode"),
                exc.message,
                self._restart_attempts + 1,
            )
            self._last_crash = record
            hook = self._on_crash
        if hook is not None:
            self._call_hook(hook, record, "on_crash")

    def _build_crash_record_locked(
        self, reason: str, exitcode: Optional[int], detail: Any, attempt: int
    ) -> dict:
        return {
            "reason": reason,
            "exitcode": exitcode,
            "at": time.time(),
            "recent_events": list(self._crash_events),
            "attempt": attempt,
            "pid": self.pid,
            "detail": detail,
        }

    @staticmethod
    def _call_hook(hook: Callable[..., Any], argument: Any, name: str) -> None:
        try:
            hook(argument)
        except Exception:  # noqa: BLE001 - 崩溃路径绝不能被钩子打断
            logger.exception("%s 钩子异常（已吞掉）", name)

    def _start_restart_timer_locked(self, attempt: int, delay: float) -> None:
        cancel = threading.Event()
        self._restart_cancel = cancel
        thread = threading.Thread(
            target=self._restart_waiter,
            args=(attempt, delay, cancel),
            name="core-restart-timer",
            daemon=True,
        )
        self._restart_thread = thread
        thread.start()

    def _restart_waiter(self, attempt: int, delay: float, cancel: threading.Event) -> None:
        """退避到点后在事件循环上拉起 :meth:`_auto_restart`（可被维护窗口取消）。"""
        if cancel.wait(delay):
            logger.info("待执行的内核重启（第 %s 次）已取消", attempt)
            return
        with self._lock:
            if self._restart_cancel is not cancel:
                return
            if self._stop_requested or self._in_maintenance:
                return
            loop = self._loop
        if loop is None or loop.is_closed():
            logger.warning("事件循环不可用，放弃自动重启（第 %s 次）", attempt)
            return
        try:
            asyncio.run_coroutine_threadsafe(self._auto_restart(attempt), loop)
        except RuntimeError:
            logger.warning("事件循环已关闭，放弃自动重启（第 %s 次）", attempt)

    def _cancel_restart_timer(self) -> None:
        with self._lock:
            cancel = self._restart_cancel
            self._restart_cancel = None
        if cancel is not None:
            cancel.set()

    async def _auto_restart(self, attempt: int) -> None:
        """退避到点后的自动重启：成功即 READY（不重新下发 CONNECT，那是 M6 的事）。"""
        with self._lock:
            if self._stop_requested or self._in_maintenance:
                return
            if self._state is CoreState.FAILED:
                return
            delay = self._backoff[min(attempt - 1, len(self._backoff) - 1)]
        logger.info("开始自动重启内核子进程（第 %s 次，退避 %.1fs）", attempt, delay)
        try:
            await self._bring_up(restarting=True, wait_ready=True, timeout=START_TIMEOUT_SECONDS)
        except AppError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            self._register_failed_restart(
                detail=f"{exc.message}（第 {attempt} 次自动重启失败）",
                exitcode=details.get("exitcode"),
            )
            return
        logger.info("自动重启完成：pid=%s", self.pid)

    # ------------------------------------------------------------------
    # spawn 与 READY 握手
    # ------------------------------------------------------------------

    async def _bring_up(self, *, restarting: bool, wait_ready: bool, timeout: float) -> None:
        """spawn +（可选）等 READY；失败抛 ``AppError``，**不改状态**（调用方决定）。"""
        process = await self._spawn_process(restarting=restarting, wait_ready=wait_ready)
        if not wait_ready:
            return
        try:
            ok, info = await self._await_ready(process, timeout)
        finally:
            with self._lock:
                self._awaiting_ready = False
        if ok:
            return
        await self._reap_process()
        reason = info.get("reason", "start_failed")
        message = {
            "timeout": f"内核在 {timeout:.1f}s 内未上报 READY",
            "fatal": f"内核启动失败（FATAL）：{info.get('detail')}",
            "process_exit": f"内核子进程在 READY 之前退出（exitcode={info.get('exitcode')}）",
        }.get(reason, f"内核启动失败：{reason}")
        raise AppError(
            ErrorCode.CORE_START_FAILED,
            message,
            {
                "state": self.state.value,
                "reason": reason,
                "exitcode": info.get("exitcode"),
                "fatal": info.get("fatal"),
            },
        )

    async def _spawn_process(self, *, restarting: bool, wait_ready: bool) -> Any:
        """换新一代队列、回收残留进程后 spawn 子进程，状态置 STARTING / RESTARTING。"""
        # 换代的第一步：让上一代监控线程确定性退出。它们可能在 ``_reap_process()``
        # 里看着残留进程被 terminate/kill；stop_event 先行置位保证它们不会把回收动作
        # 判成崩溃（M1-15）。随后自增代际，让任何已经越过 stop_event 检查的迟到判定
        # 在 ``_handle_crash`` 里被识别为旧代际并作废。
        self._stop_monitors()
        await self._reap_process()
        previous = self._rotate_queues()
        self._discard_generation_queues(previous)
        del previous
        # 立刻回收上一代队列的 SemLock：让 resource_tracker.unregister 在安全点执行，
        # 否则它们会在后续任意一次分配触发的 GC 里析构，撞上 tracker 内部锁而告警
        # （"ResourceTracker called reentrantly ... might leak"）。
        gc.collect()
        with self._lock:
            self._generation += 1
            self._stop_requested = False
            self._startup_failure = None
            self._ready_payload = None
            self._ready_flag = threading.Event()
            self._awaiting_ready = wait_ready
            self._last_ping_seq = None
            self._last_pong_seq = None
            self._heartbeat_failure_count = 0
            self._set_state_locked(CoreState.RESTARTING if restarting else CoreState.STARTING)
            process = self._ctx.Process(
                target=self._worker_target,
                args=(self._cmd_queue, self._event_queue, dict(self._boot_config)),
                name="maa-core",
                daemon=True,
            )
        process.start()
        with self._lock:
            self._process = process
        logger.info("内核子进程已启动：pid=%s state=%s", process.pid, self.state.value)
        return process

    def _rotate_queues(self) -> tuple[Any, Any]:
        """为新一代子进程换一对全新的 Queue，返回上一代（调用方负责收尾）。

        为什么必须换（M1-08 实测，Python 3.13.3）：

        - ``Queue.get()`` 在 ``timeout=None`` 的阻塞读期间**持有内部读锁**
          （``with self._rlock: self._recv_bytes()``）。子进程被 SIGKILL / OOM
          杀死时锁不会释放（没有 finally 可执行），把同一对队列再交给下一个子进程，
          它的 ``cmd_queue.get()`` 会永远卡在锁上——现象是「重启后 READY 正常上报，
          但新内核再也收不到任何命令」。
        - 子进程侧 ``Queue.put`` 的 feeder 线程也可能在对端已死后因 BrokenPipeError
          退出，之后所有 ``put`` 只进本地 buffer、不再上线。

        因此每代子进程都配新队列：旧 ``cmd_queue`` 立刻 ``cancel_join_thread()`` +
        ``close()``（别让 feeder 卡在坏管道上），旧 ``event_queue`` 只丢引用、不
        主动 close（消费者可能正阻塞在它的 ``get()`` 上，close 会把 fd 从底下抽走）。
        """
        with self._lock:
            previous = (self._cmd_queue, self._event_queue)
            self._cmd_queue = self._ctx.Queue()
            self._event_queue = self._ctx.Queue()
        return previous

    @staticmethod
    def _discard_generation_queues(queues: tuple[Any, Any]) -> None:
        """收尾上一代队列：命令队列没人再读，直接关掉；事件队列交给 GC。"""
        cmd_queue, _event_queue = queues
        with contextlib.suppress(Exception):
            cmd_queue.cancel_join_thread()
            cmd_queue.close()

    async def _await_ready(self, process: Any, timeout: float) -> tuple[bool, dict]:
        """轮询等 READY / FATAL / 进程退出 / 超时（异步轮询，不占用默认 executor）。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(float(timeout), 0.0)
        while True:
            if self._ready_flag is not None and self._ready_flag.is_set():
                return True, {}
            with self._lock:
                failure = self._startup_failure
            if failure is not None:
                return False, {
                    "reason": "fatal",
                    "detail": failure.get("error") or str(failure),
                    "fatal": failure,
                }
            if not process.is_alive():
                return False, {"reason": "process_exit", "exitcode": process.exitcode}
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False, {"reason": "timeout"}
            await asyncio.sleep(min(READY_POLL_INTERVAL, remaining))

    async def _reap_process(self) -> Optional[int]:
        """确保没有残留子进程：活着就 terminate → 等 2 秒 → kill，返回最后的 exitcode。"""
        process = self._process
        if process is None:
            return None
        if process.is_alive():
            process.terminate()
            if not await self._wait_process_exit(process, REAP_TERMINATE_TIMEOUT):
                process.kill()
                await self._wait_process_exit(process, REAP_TERMINATE_TIMEOUT)
        exitcode = process.exitcode
        with self._lock:
            if (
                self._last_crash is not None
                and self._last_crash.get("exitcode") is None
                and exitcode is not None
            ):
                self._last_crash["exitcode"] = exitcode
        return exitcode

    async def _stop_process(self, *, graceful: bool, timeout: float) -> None:
        """SHUTDOWN → terminate → kill 的三段式关机（docs/02 §4），不改状态。"""
        process = self._process
        if process is None or not process.is_alive():
            return
        try:
            self._cmd_queue.put(make_command("SHUTDOWN", graceful=bool(graceful)))
        except Exception:  # noqa: BLE001 - 队列已坏也必须走 kill 兜底
            logger.exception("SHUTDOWN 命令入队失败，直接 terminate()")
        else:
            if await self._wait_process_exit(process, timeout):
                return
            logger.warning("SHUTDOWN 等待 %.1fs 超时，terminate()", timeout)
        process.terminate()
        if await self._wait_process_exit(process, TERMINATE_GRACE_SECONDS):
            return
        logger.warning("terminate() 等待 %.1fs 超时，kill()", TERMINATE_GRACE_SECONDS)
        process.kill()
        await self._wait_process_exit(process, TERMINATE_GRACE_SECONDS)

    @staticmethod
    async def _wait_process_exit(process: Any, timeout: float) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(float(timeout), 0.0)
        while process.is_alive():
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(READY_POLL_INTERVAL, remaining))
        return True

    # ------------------------------------------------------------------
    # 心跳与存活监控（daemon 线程）
    # ------------------------------------------------------------------

    def _start_monitors_locked(self) -> None:
        generation = self._generation
        if (
            self._monitor_generation == generation
            and self._monitor_stop is not None
            and not self._monitor_stop.is_set()
            and self._heartbeat_thread is not None
            and self._heartbeat_thread.is_alive()
        ):
            return
        stop_event = threading.Event()
        self._monitor_stop = stop_event
        # 监控线程绑定启动时的代际：换代后它们必须自杀，绝不监控不属于自己的进程。
        self._monitor_generation = generation
        process = self._process
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(stop_event, generation),
            name="core-heartbeat",
            daemon=True,
        )
        self._liveness_thread = threading.Thread(
            target=self._liveness_loop,
            args=(stop_event, process, generation),
            name="core-liveness",
            daemon=True,
        )
        self._heartbeat_thread.start()
        self._liveness_thread.start()

    def _stop_monitors_locked(self) -> None:
        if self._monitor_stop is not None:
            self._monitor_stop.set()

    def _stop_monitors(self) -> None:
        with self._lock:
            self._stop_monitors_locked()

    def _heartbeat_loop(self, stop_event: threading.Event, generation: int) -> None:
        """每 ``heartbeat_interval`` 发一条 ``PING(seq)``，连续失败阈值次判失联。

        ``generation`` 是线程启动时的内核代际；换代后本线程会往新代的 ``cmd_queue``
        发 PING、用的却是旧代的 seq，必须直接退出。
        """
        seq = 0
        while not stop_event.wait(self._heartbeat_interval):
            if not self._is_current_generation(generation):
                logger.debug("第 %s 代心跳线程退出：内核已换代", generation)
                return
            if self._long_command_in_flight():
                # 已知长命令期间临时放宽：跳过本轮且不累计失败（docs/03 §4.1）。
                with self._lock:
                    self._heartbeat_failure_count = 0
                logger.debug("长命令执行中，跳过一次心跳判定")
                continue
            with self._lock:
                ping = self._last_ping_seq
                if ping is None:
                    pass
                elif self._last_pong_seq == ping:
                    self._heartbeat_failure_count = 0
                else:
                    self._heartbeat_failure_count += 1
                failures = self._heartbeat_failure_count
            if failures >= self._heartbeat_failures:
                handled = self._handle_crash(
                    reason="heartbeat_timeout",
                    exitcode=None,
                    detail=f"连续 {failures} 次未收到同 seq 的 PONG（seq={ping}）",
                    generation=generation,
                )
                if handled or not self._in_maintenance:
                    return
                # 维护窗口内：清零后继续轮询，窗口退出即恢复判定。
                with self._lock:
                    self._heartbeat_failure_count = 0
                continue
            seq += 1
            with self._lock:
                self._last_ping_seq = seq
            try:
                self._cmd_queue.put(make_command("PING", seq=seq))
            except Exception:  # noqa: BLE001 - 队列坏掉等同于一次心跳失败
                logger.exception("PING 入队失败（seq=%s）", seq)

    def _liveness_loop(self, stop_event: threading.Event, process: Any, generation: int) -> None:
        """轮询 ``is_alive()`` 捕获不带事件的硬崩溃（段错误 / OOM kill）。

        ``generation`` 是本线程启动时的内核代际。换代（``restart()`` / 自动重启 / 手动
        ``start()``）后本线程盯着的 ``process`` 已被回收，它观测到的「退出」属于上一代、
        不代表当前生命周期，必须直接退出——否则会把优雅停止（``exitcode=0``）记成
        崩溃并调度伪重启，``_reap_process()`` 会 terminate 掉刚起来的新进程（M1-15）。
        """
        if process is None:
            return
        reported = False
        while not stop_event.wait(LIVENESS_POLL_INTERVAL):
            if not self._is_current_generation(generation):
                logger.debug("第 %s 代存活监控线程退出：内核已换代", generation)
                return
            if process.is_alive():
                continue
            exitcode = process.exitcode
            if reported and self._in_maintenance:
                continue
            handled = self._handle_crash(
                reason="process_exit", exitcode=exitcode, generation=generation
            )
            if handled or not self._in_maintenance:
                return
            reported = True  # 维护窗口内只提示一次，窗口退出后由 reconcile 补记

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _is_current_generation(self, generation: int) -> bool:
        """本线程绑定的代际是否仍是当前代际（换代后旧监控线程必须自杀）。"""
        with self._lock:
            return generation == self._generation

    def _require_dispatcher(self) -> None:
        with self._lock:
            dispatcher = self._dispatcher
            state = self._state
        if dispatcher is None:
            raise AppError(
                ErrorCode.CORE_START_FAILED,
                "start()/restart() 之前必须先 set_dispatcher()：supervisor 自身不消费 "
                "event_queue，必须由唯一消费者（CoreClient）声明并回调 handle_*，"
                "否则会出现两个消费者抢事件",
                {"state": state.value},
            )

    def _set_state(self, state: CoreState) -> None:
        with self._lock:
            self._set_state_locked(state)

    def _set_state_locked(self, state: CoreState) -> bool:
        """置状态并同步调用状态钩子（在调用线程上执行，必须快速返回）。"""
        if self._state is state:
            return False
        self._state = state
        hook = self._on_state_change
        if hook is not None:
            self._call_hook(hook, state, "on_state_change")
        return True
