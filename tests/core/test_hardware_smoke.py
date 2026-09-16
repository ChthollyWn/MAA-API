"""M1-14 真机冒烟测试：真实 MaaCore + 真实设备的加载 / 连接 / 截图 / 原子操作 / 最短任务。

本文件把 M1-13 的命令行脚本（``scripts/core_smoke.py``）固化成可重复执行的 pytest 用例：
每一环真连内核与设备，用 M1-08 的 :class:`~maa_api.core.supervisor.CoreSupervisor` 与
M1-09 的 :class:`~maa_api.core.client.CoreClient`（真实 spawn 子进程 + 真实 worker +
真实 ``asst_factory='maa_api.core.asst:Asst'``），断言到「内核状态 / 回调 / 落盘产物」这一层。

覆盖 4 条链路（卡面「真机链路的每一环各一个」）：

1. 启动与资源加载：``sup.start(wait_ready=True, timeout=180)`` → ``state=READY`` →
   ``get_version()`` 非空字符串（``READY`` 本身即代表 dlopen + 全量资源加载成功）；
2. 异步连接：``client.connect(ADB_PATH, ADB_ADDRESS)`` 返回 ``True`` —— 两级 Future 的
   第二级只认 ``msg=4`` 的 ``AsyncCallInfo``，载荷层级照 M1-01 真机实测（顶层
   ``async_call_id`` / ``what``，成功标志在 ``details.details.ret`` 双层嵌套）；
   ``msg=2`` 的 ``ConnectionInfo(Connected)`` 只是先到的中间态，不得拿它当结果；
3. 截图落盘：``client.screencap(save_to=...)`` 得到存在的非空文件，``client.get_image()``
   返回含 ``path`` / ``size`` / ``encoding`` 的引用；``CMD_RESULT`` 里不得出现图像字节
   （落盘传引用，docs/02 §3.3）；
4. 原子操作与最短任务：``click`` → ``back_to_home`` → ``append_task('StartUp')`` →
   ``start`` → 在 120 秒内收到 ``TaskChainStart`` 与
   ``TaskChainCompleted`` / ``TaskChainError`` / ``TaskChainStopped`` 中的终态回调；
   用例结束 ``client.stop()`` + ``sup.stop()``，不留子进程（finally + autouse 兜底）。

**默认全部 skip**：模块级读环境变量 ``MAA_HW_TESTS``，每个用例同时标
``@pytest.mark.hardware`` 与 ``@pytest.mark.skipif(not os.environ.get('MAA_HW_TESTS'), ...)``。
闸门关闭时 ``.venv/bin/python -m pytest -q`` 不碰内核、不连设备，秒级全绿（skip，exit 0）。

手动真机运行（本机实测可用的那组默认值：内核 ``resource/lib/maa/Darwin``、
adb ``/opt/homebrew/bin/adb``、设备 ``127.0.0.1:5555``）::

    MAA_HW_TESTS=1 DYLD_LIBRARY_PATH=$PWD/resource/lib/maa/Darwin \\
        .venv/bin/python -m pytest -q -m hardware tests/core/test_hardware_smoke.py

macOS 上 ``DYLD_LIBRARY_PATH`` 由本模块按 ``MAA_PATH`` 兜底补齐（M1-02 实测它其实是冗余
保险：按绝对路径 ``ctypes.CDLL(<dir>/libMaaCore.dylib)`` 不依赖它，但显式 export 仍是文档口径）。

可用环境变量覆盖（默认值是上机实测的那组）：

- ``MAA_PATH``：内核目录（含 ``libMaaCore.*`` 与 ``resource/``），默认
  ``resource/lib/maa/Darwin``（先按 CWD 解析，再退回仓库根）；
- ``ADB_PATH``：adb 可执行文件，默认 ``/opt/homebrew/bin/adb``；
- ``ADB_ADDRESS``：设备地址，默认 ``127.0.0.1:5555``；
- ``MAA_USER_DIR``：内核用户数据目录（日志 / ``crash.log``），默认每个用例在
  pytest ``tmp_path`` 下新建（``AsstSetUserDir`` 要求目录已存在，M1-02 实测）。

已知边界（照实测，不照文档推测）：

- ``StartUp`` 缺 ``client_type`` 时内核静默返回 ``task_id=0``（不抛异常、无回调，
  M1-02 实测），所以本文件提交 ``StartUp`` 时补 ``{"client_type": "Official"}``；
- ``AsstMsg`` 取值不是连号：``ConnectionInfo=2`` / ``AsyncCallInfo=4`` /
  ``TaskChain*=10000..10004``（M1-02 实测），用 :class:`~maa_api.core.enums.Message`
  还原回调名，不硬编码数字；
- 失败信息统一经 :meth:`_HardwareCore.diag` 带上子进程状态、事件序列、回调统计、命令回执、
  崩溃现场与内核日志尾部，保证真机上失败时原因可见。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import multiprocessing
import os
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator, Optional

import pytest

from maa_api.core.client import CoreClient
from maa_api.core.enums import Message
from maa_api.core.supervisor import CoreState, CoreSupervisor

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# 硬件闸门（模块级读取：闸门关闭时用例在 collection 期就标为 skip）
# ---------------------------------------------------------------------------

#: 环境变量闸门：只有显式设成非空（惯用 ``MAA_HW_TESTS=1``）才可能真连内核与设备。
HW_GATE_ENV = "MAA_HW_TESTS"
HW_SKIP_REASON = "需要真实 MaaCore 与设备；设 MAA_HW_TESTS=1 开启"
HW_ENABLED = bool(os.environ.get(HW_GATE_ENV))

# ---------------------------------------------------------------------------
# 路径 / 地址 / 任务（都可用环境变量覆盖，默认值 = 本机实测可用的那组）
# ---------------------------------------------------------------------------

DEFAULT_MAA_PATH = "resource/lib/maa/Darwin"
DEFAULT_ADB_PATH = "/opt/homebrew/bin/adb"
DEFAULT_ADB_ADDRESS = "127.0.0.1:5555"

#: boot_config 的真实内核工厂（M1-07 契约；替身只用于无真机的 M1-11）。
REAL_ASST_FACTORY = "maa_api.core.asst:Asst"

#: 收到即视为任务链结束的三条终态回调；只有 TaskChainCompleted 算成功（M1-13 口径）。
TERMINAL_CALLBACKS = frozenset({"TaskChainCompleted", "TaskChainError", "TaskChainStopped"})
SUCCESS_CALLBACK = "TaskChainCompleted"

#: 最短任务与其必填参数（缺参数内核静默返回 task_id=0，M1-02 实测）。
SHORTEST_TASK = "StartUp"
SHORTEST_TASK_PARAMS = {"client_type": "Official"}

#: GET_IMAGE 的 CMD_RESULT.data 允许的键（M1-11 实测 {path,size,width,height,encoding}）。
GET_IMAGE_DATA_KEYS = frozenset({"path", "size", "width", "height", "encoding"})

#: ``CMD_RESULT`` 里图像元信息的上界：真图 JPEG 约 0.6 MB，任何 base64/裸字节夹带都远超它。
CMD_RESULT_SIZE_LIMIT = 64 * 1024

# 真机超时（比无内核单测宽得多；180s 是卡面指定值，启动含 dlopen + 全量资源加载）。
START_TIMEOUT = 180.0
CONNECT_TIMEOUT = 60.0
ACCEPT_TIMEOUT = 30.0
TASK_TERMINAL_TIMEOUT = 120.0
STOP_TIMEOUT = 30.0
POLL_INTERVAL = 0.05

#: CoreSupervisor 给子进程起的名字；残留检查与兜底回收靠它匹配。
WORKER_PROCESS_NAME = "maa-core"


def _resolve_maa_path(raw: str) -> Path:
    """把内核目录解析成绝对路径：先按 CWD，再退回仓库根（相对默认值的常见位置）。"""
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return candidate
    if (Path.cwd() / candidate).exists():
        return (Path.cwd() / candidate).resolve()
    return (REPO_ROOT / candidate).resolve()


MAA_PATH = _resolve_maa_path(os.environ.get("MAA_PATH", DEFAULT_MAA_PATH))
ADB_PATH = os.environ.get("ADB_PATH", DEFAULT_ADB_PATH)
ADB_ADDRESS = os.environ.get("ADB_ADDRESS", DEFAULT_ADB_ADDRESS)
MAA_USER_DIR = os.environ.get("MAA_USER_DIR") or None


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _ensure_dyld_library_path(maa_path: Path) -> None:
    """macOS：把内核目录补进 ``DYLD_LIBRARY_PATH``，spawn 子进程会继承这份环境。

    M1-02 实测按绝对路径 ``ctypes.CDLL`` 本不依赖它（依赖走 ``@loader_path``/``@rpath``），
    但它是文档口径的保险；只在缺失时前置，不改写用户已设的值。
    """
    if sys.platform != "darwin":
        return
    entries = [item for item in os.environ.get("DYLD_LIBRARY_PATH", "").split(os.pathsep) if item]
    if str(maa_path) not in entries:
        os.environ["DYLD_LIBRARY_PATH"] = os.pathsep.join([str(maa_path), *entries])


def _resolve_user_dir(tmp_path: Path) -> Path:
    """内核用户数据目录：``MAA_USER_DIR`` 优先，否则用 pytest 的 ``tmp_path``。

    ``AsstSetUserDir`` 要求目录已存在（否则返回 False，M1-02 实测），这里先 mkdir。
    用户显式指定 ``MAA_USER_DIR`` 时归用户所有，本文件不删；默认目录随 ``tmp_path``
    由 pytest 管理，失败时保留日志便于排查。
    """
    user_dir = Path(MAA_USER_DIR).expanduser() if MAA_USER_DIR else tmp_path / "maa-user"
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def _live_core_children() -> list[int]:
    """当前进程名下还活着的 ``maa-core`` 子进程 pid（残留检查用）。"""
    return [
        int(child.pid)
        for child in multiprocessing.active_children()
        if child.name == WORKER_PROCESS_NAME and child.is_alive()
    ]


def _assert_no_image_bytes(value: Any, path: str, diag: Callable[[str], str]) -> None:
    """递归断言 ``value`` 里没有二进制图像数据（图像只能落盘传引用，docs/02 §3.3）。"""
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise AssertionError(
            diag(
                f"{path} 出现二进制图像数据（{type(value).__name__}，{len(value)} 字节）："
                "GET_IMAGE 必须落盘后只回路径与元信息"
            )
        )
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_no_image_bytes(item, f"{path}.{key}", diag)
    elif isinstance(value, (list, tuple, set)):
        for index, item in enumerate(value):
            _assert_no_image_bytes(item, f"{path}[{index}]", diag)


# ---------------------------------------------------------------------------
# 真机会话：真实 CoreSupervisor + 真实 CoreClient + 观测记录
# ---------------------------------------------------------------------------


class _HardwareCore:
    """一次真机用例的内核会话，构造时不碰内核，``start()`` 才 spawn。

    观测全部走公开扩展点：``CoreClient.on`` 的分派表与 ``CoreSupervisor`` 的
    ``on_crash`` / ``on_state_change`` 钩子（不读 Queue、不碰私有状态）。失败信息统一由
    :meth:`diag` 汇总，保证「失败信息带子进程事件与 state」。
    """

    def __init__(self, maa_path: Path, user_dir: Path) -> None:
        self.maa_path = maa_path
        self.user_dir = user_dir

        self.order: list[str] = []
        self.callbacks: list[dict] = []
        self.cmd_results: list[dict] = []
        self.ready_payloads: list[dict] = []
        self.logs: list[str] = []
        self.fatals: list[dict] = []
        self.state_changes: list[str] = []
        self.crashes: list[dict] = []
        #: cmd_id → 命令类型（CMD_RESULT 本身不带类型，靠包装 track_command 补全）。
        self.command_types: dict[str, str] = {}
        self.start_seconds: Optional[float] = None
        self.connect_seconds: Optional[float] = None

        self.supervisor = CoreSupervisor(
            self._boot_config(),
            on_crash=self._record_crash,
            on_state_change=self._record_state,
        )
        self.client = CoreClient(
            self.supervisor,
            connect_timeout=CONNECT_TIMEOUT,
            accept_timeout=ACCEPT_TIMEOUT,
        )
        self._instrument_track_command()

        # 卡面契约：READY / FATAL / PONG 接进 supervisor 状态机（CoreClient 构造时已默认
        # 接好，重复注册幂等无害；显式写出来让接线可见）。观测处理器注册在其后。
        self.client.on("READY", self.supervisor.handle_ready)
        self.client.on("FATAL", self.supervisor.handle_fatal)
        self.client.on("PONG", self.supervisor.handle_pong)
        self.client.on("READY", self._record_ready)
        self.client.on("CMD_RESULT", self._record_cmd_result)
        self.client.on("CALLBACK", self._record_callback)
        self.client.on("LOG", self._record_log)
        self.client.on("FATAL", self._record_fatal)

    def _boot_config(self) -> dict:
        """M1-07 的 boot_config 契约：纯数据、可 pickle、真实内核工厂。"""
        return {
            "maa_path": str(self.maa_path),
            "user_dir": str(self.user_dir),
            "incremental_paths": [],
            "instance_options": {},
            "asst_factory": REAL_ASST_FACTORY,
            "asst_factory_kwargs": {},
        }

    # ---- 观测 ----

    def _instrument_track_command(self) -> None:
        """包装 ``supervisor.track_command`` 记录 cmd_id → 命令类型（公开契约方法）。"""
        original = self.supervisor.track_command

        def track(command: dict) -> None:
            if isinstance(command, dict) and command.get("cmd_id"):
                self.command_types[str(command["cmd_id"])] = str(command.get("type"))
            original(command)

        self.supervisor.track_command = track  # type: ignore[method-assign]

    def _record_ready(self, payload: dict) -> None:
        self.ready_payloads.append(dict(payload))
        self.order.append("READY")

    def _record_cmd_result(self, payload: dict) -> None:
        cmd_id = str(payload.get("cmd_id"))
        kind = self.command_types.get(cmd_id, f"UNKNOWN({cmd_id})")
        self.cmd_results.append(
            {"type": kind, "ok": payload.get("ok"), "data": payload.get("data")}
        )
        self.order.append(f"CMD_RESULT:{kind}")

    def _record_callback(self, payload: dict) -> None:
        raw_msg = payload.get("msg")
        try:
            name = Message(int(raw_msg)).name
        except (TypeError, ValueError):
            name = f"UNKNOWN({raw_msg!r})"
        self.callbacks.append({"msg": raw_msg, "name": name, "details": payload.get("details")})
        self.order.append(f"CALLBACK:{name}")

    def _record_log(self, payload: dict) -> None:
        self.logs.append(str(payload.get("content")))

    def _record_fatal(self, payload: dict) -> None:
        self.fatals.append(dict(payload))
        self.order.append("FATAL")

    def _record_crash(self, record: dict) -> None:
        self.crashes.append(dict(record))

    def _record_state(self, state: Any) -> None:
        self.state_changes.append(getattr(state, "value", str(state)))

    # ---- 观测读取 ----

    def callback_names(self) -> list[str]:
        return [event["name"] for event in self.callbacks]

    def callback_index(self, name: str, what: Optional[str] = None) -> Optional[int]:
        """按回调名（可选 ``details.what``）取首次出现的下标，没有返回 ``None``。"""
        for index, event in enumerate(self.callbacks):
            if event["name"] != name:
                continue
            details = event.get("details")
            if what is None or (isinstance(details, dict) and details.get("what") == what):
                return index
        return None

    def cmd_data(self, type_name: str) -> list[Any]:
        """某类命令的 ``CMD_RESULT.data`` 列表（按发生顺序）。"""
        return [item["data"] for item in self.cmd_results if item["type"] == type_name]

    def callback_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for name in self.callback_names():
            counts[name] = counts.get(name, 0) + 1
        return counts

    def diag(self, note: str = "") -> str:
        """失败信息：子进程事件序列 + 状态 + 回调 + 命令回执 + 崩溃现场 + 日志尾部。"""
        return (
            f"{note} | state={self.supervisor.state.value} pid={self.supervisor.pid} "
            f"exitcode={self.supervisor.exitcode} state_changes={self.state_changes} "
            f"last_crash={self.supervisor.last_crash} crashes={self.crashes} "
            f"events={self.order[-40:]} callbacks={self.callback_counts()} "
            f"cmd_results={[(item['type'], item['ok']) for item in self.cmd_results]} "
            f"fatals={self.fatals} logs={self.logs[-8:]} user_dir={self.user_dir}"
        )

    # ---- 等待 ----

    async def wait_for(
        self,
        predicate: Callable[[], bool],
        note: str,
        timeout: float,
        interval: float = POLL_INTERVAL,
    ) -> None:
        """轮询等待谓词成立；超时抛带 :meth:`diag` 的 ``AssertionError``。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if predicate():
                return
            await asyncio.sleep(interval)
        raise AssertionError(self.diag(note))

    # ---- 生命周期 ----

    async def start(self, *, connect: bool = False) -> None:
        """``start_consumer()`` → ``sup.start(wait_ready=True, timeout=180)``（可选连接）。"""
        self.client.start_consumer()
        started = time.monotonic()
        await self.supervisor.start(wait_ready=True, timeout=START_TIMEOUT)
        self.start_seconds = time.monotonic() - started
        if connect:
            await self.connect()

    async def connect(self) -> bool:
        """两级 Future 的异步连接，返回 ``AsyncCallInfo`` 的结果（msg=4 才算数）。"""
        started = time.monotonic()
        connected = await self.client.connect(
            ADB_PATH, ADB_ADDRESS, "General", timeout=CONNECT_TIMEOUT
        )
        self.connect_seconds = time.monotonic() - started
        return bool(connected)

    async def shutdown(self) -> None:
        """``client.stop()`` → ``sup.stop()`` → ``client.close()`` + 队列收尾（finally 兜底）。"""
        if self.client is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.client.stop(), timeout=STOP_TIMEOUT)

        try:
            await asyncio.wait_for(
                self.supervisor.stop(graceful=True, timeout=STOP_TIMEOUT),
                timeout=STOP_TIMEOUT + 5.0,
            )
        except Exception:  # noqa: BLE001 - 优雅停失败必须走强制路径，不能留子进程
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    self.supervisor.stop(graceful=False, timeout=5.0), timeout=10.0
                )

        if self.client is not None:
            with contextlib.suppress(Exception):
                self.client.close()

        for queue in (self.supervisor.cmd_queue, self.supervisor.event_queue):
            # 子进程已停、消费线程已退出：收尾当前代的 Queue，避免 SemLock 留给 GC。
            with contextlib.suppress(Exception):
                queue.cancel_join_thread()
                queue.close()


# ---------------------------------------------------------------------------
# 用例外壳：闸门兜底、异常补 diag、永远收尾、残留检查
# ---------------------------------------------------------------------------


def _run_hardware_case(
    case: Callable[[_HardwareCore], Awaitable[None]],
    tmp_path: Path,
) -> None:
    """跑一个真机用例：显式闸门兜底 → 建会话 → 跑 case → 收尾 → 检查无残留子进程。

    ``case`` 里的 ``AssertionError`` 原样抛出（消息已带 diag）；其它异常包装一层，
    补上子进程状态与事件序列，避免真机失败只剩一句 ``AppError``。
    """
    if not HW_ENABLED:
        pytest.skip(HW_SKIP_REASON)

    if not MAA_PATH.is_dir():
        pytest.fail(f"内核目录不存在：{MAA_PATH}（用 MAA_PATH 覆盖）", pytrace=False)
    if not any(MAA_PATH.glob("libMaaCore.*")):
        pytest.fail(f"内核目录里没有 libMaaCore.*：{MAA_PATH}", pytrace=False)

    _ensure_dyld_library_path(MAA_PATH)
    core = _HardwareCore(MAA_PATH, _resolve_user_dir(tmp_path))

    async def runner() -> None:
        try:
            await case(core)
        except AssertionError:
            raise
        except Exception as exc:  # noqa: BLE001 - 真机失败必须带上现场
            raise AssertionError(
                core.diag(f"未预期异常 {type(exc).__name__}: {exc}")
            ) from exc
        finally:
            await core.shutdown()

    asyncio.run(runner())

    leftovers = _live_core_children()
    assert not leftovers, core.diag(f"用例结束后仍有残留 {WORKER_PROCESS_NAME} 子进程：{leftovers}")


@pytest.fixture(autouse=True)
def _reap_core_children() -> Iterator[None]:
    """兜底：任何用例（含失败/中断）结束后都不允许残留 maa-core 子进程。"""
    yield
    for child in multiprocessing.active_children():
        if child.name == WORKER_PROCESS_NAME and child.is_alive():
            child.kill()
            child.join(timeout=2.0)


# ---------------------------------------------------------------------------
# ① 启动与资源加载：READY + state==READY + get_version() 非空
# ---------------------------------------------------------------------------


@pytest.mark.hardware
@pytest.mark.skipif(
    not os.environ.get("MAA_HW_TESTS"), reason="需要真实 MaaCore 与设备；设 MAA_HW_TESTS=1 开启"
)
def test_start_loads_resource_and_reports_version(tmp_path: Path) -> None:
    async def case(core: _HardwareCore) -> None:
        await core.start()

        assert core.supervisor.state is CoreState.READY, core.diag(
            "sup.start(wait_ready=True, timeout=180) 返回后 state 应为 READY"
        )
        assert core.supervisor.pid is not None, core.diag("READY 后必须有子进程 pid")
        assert core.ready_payloads, core.diag("READY 事件必须经消费线程被处理")

        payload = core.ready_payloads[-1]
        assert payload.get("pid") == core.supervisor.pid, core.diag(
            "READY.payload.pid 应为子进程 pid"
        )
        assert isinstance(payload.get("version"), str) and payload["version"].strip(), core.diag(
            f"READY.payload.version 应为非空字符串，实际 {payload.get('version')!r}"
        )

        # 命令链路：主进程 put → 子进程命令循环 → CMD_RESULT → Future。
        version = await core.client.get_version()
        assert isinstance(version, str) and version.strip(), core.diag(
            f"get_version() 应返回非空字符串，实际 {version!r}"
        )
        assert version == payload["version"], core.diag(
            "get_version() 应与 READY 上报的版本一致（同一次 dlopen）"
        )
        assert "CMD_RESULT:GET_VERSION" in core.order, core.diag("GET_VERSION 回执未被处理")
        print(
            f"[hardware] READY：pid={core.supervisor.pid} version={version!r} "
            f"启动耗时 {core.start_seconds:.2f}s（含 dlopen + 全量资源加载）",
            flush=True,
        )

    _run_hardware_case(case, tmp_path)


# ---------------------------------------------------------------------------
# ② 异步连接：两级 Future，只看 msg=4 的 AsyncCallInfo
# ---------------------------------------------------------------------------


@pytest.mark.hardware
@pytest.mark.skipif(
    not os.environ.get("MAA_HW_TESTS"), reason="需要真实 MaaCore 与设备；设 MAA_HW_TESTS=1 开启"
)
def test_connect_async_result_comes_from_async_call_info(tmp_path: Path) -> None:
    async def case(core: _HardwareCore) -> None:
        await core.start()
        connected = await core.connect()

        assert connected is True, core.diag(
            f"client.connect({ADB_PATH!r}, {ADB_ADDRESS!r}) 应返回 True"
            f"（AsyncCallInfo.details.details.ret），耗时 {core.connect_seconds!r}s"
        )

        # 第二级结果只能认 msg=4：关联键在顶层、成功标志双层嵌套（M1-01 真机实测）。
        info_index = core.callback_index("AsyncCallInfo", "Connect")
        assert info_index is not None, core.diag(
            "必须收到 what='Connect' 的 msg=4 AsyncCallInfo 作为连接结果"
        )
        details = core.callbacks[info_index]["details"]
        assert isinstance(details, dict), core.diag(f"AsyncCallInfo.details 应是 dict：{details!r}")
        assert details.get("async_call_id"), core.diag(
            f"async_call_id 应在回调 JSON 顶层且非零，实际 {details.get('async_call_id')!r}"
        )
        nested = details.get("details")
        assert isinstance(nested, dict) and nested.get("ret") is True, core.diag(
            f"成功标志应在 details.details.ret（双层嵌套）且为 True，实际 {nested!r}"
        )

        # msg=2 的 ConnectionInfo(Connected) 只是先到的中间态，不得拿它当结果。
        state_index = core.callback_index("ConnectionInfo", "Connected")
        assert state_index is not None, core.diag("应观测到 ConnectionInfo(Connected) 中间态")
        assert state_index < info_index, core.diag(
            "ConnectionInfo(Connected) 应作为中间态先于 msg=4 到达（M1-01 实测序列）"
        )

        # 回退路径的正确性：同步查询 CONNECTED（AsstConnected）也应为真。
        assert await core.client.connected() is True, core.diag(
            "connect() 之后 CONNECTED 同步查询应为 True（AsyncCallInfo 缺失时的回退判据）"
        )
        print(
            f"[hardware] CONNECT：{ADB_PATH} → {ADB_ADDRESS} 结果 True，"
            f"耗时 {core.connect_seconds:.2f}s（两级 Future，只认 msg=4）",
            flush=True,
        )

    _run_hardware_case(case, tmp_path)


# ---------------------------------------------------------------------------
# ③ 截图落盘：文件存在非空 + CMD_RESULT 只回引用，不含图像字节
# ---------------------------------------------------------------------------


@pytest.mark.hardware
@pytest.mark.skipif(
    not os.environ.get("MAA_HW_TESTS"), reason="需要真实 MaaCore 与设备；设 MAA_HW_TESTS=1 开启"
)
def test_screencap_writes_file_and_returns_reference(tmp_path: Path) -> None:
    async def case(core: _HardwareCore) -> None:
        await core.start(connect=True)
        assert await core.client.connected() is True, core.diag("截图前设备应处于连接状态")

        shot = tmp_path / "shot.jpg"
        path = await core.client.screencap(save_to=shot)
        assert path.exists(), core.diag(f"screencap 落盘文件不存在：{path}")
        assert path.stat().st_size > 0, core.diag(f"screencap 落盘文件为空：{path}")

        data = await core.client.get_image(save_to=shot)
        assert isinstance(data, dict), core.diag(f"get_image() 应返回 dict，实际 {data!r}")
        for key in ("path", "size", "encoding"):
            assert data.get(key) not in (None, ""), core.diag(
                f"get_image() 的 data 缺 {key!r}：{data!r}"
            )

        stored = Path(str(data["path"]))
        assert stored.exists() and stored.stat().st_size > 0, core.diag(
            f"data['path'] 指向的文件不存在或为空：{stored}"
        )
        assert int(data["size"]) == stored.stat().st_size, core.diag(
            f"data['size'] 应等于落盘文件字节数：{data['size']} != {stored.stat().st_size}"
        )

        # 落盘传引用（docs/02 §3.3）：CMD_RESULT 里不得出现图像字节 / base64 大块。
        image_results = core.cmd_data("GET_IMAGE")
        assert image_results, core.diag("没有记录到 GET_IMAGE 的 CMD_RESULT")
        for index, result in enumerate(image_results):
            _assert_no_image_bytes(result, f"GET_IMAGE.data[{index}]", core.diag)
            payload_size = len(json.dumps(result, ensure_ascii=False, default=str))
            assert payload_size < CMD_RESULT_SIZE_LIMIT, core.diag(
                f"GET_IMAGE 的 CMD_RESULT 过大（{payload_size} 字节），疑似夹带图像数据"
            )
            assert set(result) <= GET_IMAGE_DATA_KEYS, core.diag(
                f"GET_IMAGE 的 data 出现非元信息键：{sorted(set(result) - GET_IMAGE_DATA_KEYS)}"
            )

        # 产物自洽：分辨率已知时 worker 走 JPEG 编码分支（M1-11 / ENVIRONMENT 实测）。
        if data["encoding"] == "jpeg":
            assert stored.read_bytes()[:2] == b"\xff\xd8", core.diag(
                f"encoding=jpeg 但文件 magic 不是 JPEG：{stored}"
            )
        print(
            f"[hardware] SCREENCAP：{data['path']} size={data['size']} "
            f"encoding={data['encoding']}",
            flush=True,
        )

    _run_hardware_case(case, tmp_path)


# ---------------------------------------------------------------------------
# ④ 原子操作与最短任务：click / back_to_home / append_task / start → 终态回调
# ---------------------------------------------------------------------------


@pytest.mark.hardware
@pytest.mark.skipif(
    not os.environ.get("MAA_HW_TESTS"), reason="需要真实 MaaCore 与设备；设 MAA_HW_TESTS=1 开启"
)
def test_atomic_ops_and_shortest_task_reach_terminal_callback(tmp_path: Path) -> None:
    async def case(core: _HardwareCore) -> None:
        await core.start(connect=True)
        assert await core.client.connected() is True, core.diag("提交任务前设备应处于连接状态")

        # 原子操作：异步点击的受理编号来自 CLICK 的 CMD_RESULT（内核返回值非零才算受理）。
        await core.client.click(1, 1)
        clicks = core.cmd_data("CLICK")
        assert clicks, core.diag("没有记录到 CLICK 的 CMD_RESULT")
        call_id = clicks[-1].get("async_call_id") if isinstance(clicks[-1], dict) else None
        assert isinstance(call_id, int) and call_id != 0, core.diag(
            f"click 的异步受理编号应是非零 int（AsstAsyncClick 的返回值），实际 {call_id!r}"
        )

        assert await core.client.back_to_home() is True, core.diag(
            "back_to_home() 应返回 True（AsstBackToHome）"
        )

        # StartUp 缺 client_type 时内核静默返回 0（M1-02 实测），必须补必填参数。
        task_id = await core.client.append_task(SHORTEST_TASK, dict(SHORTEST_TASK_PARAMS))
        assert isinstance(task_id, int) and task_id > 0, core.diag(
            f"append_task({SHORTEST_TASK!r}, {SHORTEST_TASK_PARAMS!r}) 应返回非零 task_id，"
            f"实际 {task_id!r}"
        )
        assert await core.client.start() is True, core.diag("start() 应返回 True")

        # 一次等待同时覆盖 TaskChainStart 与终态，避免两段超时叠加成 240s。
        await core.wait_for(
            lambda: "TaskChainStart" in core.callback_names()
            and any(name in TERMINAL_CALLBACKS for name in core.callback_names()),
            f"未在 {TASK_TERMINAL_TIMEOUT:g}s 内同时收到 TaskChainStart 与终态回调"
            f"（{'/'.join(sorted(TERMINAL_CALLBACKS))}）",
            TASK_TERMINAL_TIMEOUT,
        )

        terminal = next(
            event for event in core.callbacks if event["name"] in TERMINAL_CALLBACKS
        )
        # 卡面口径：收到 TaskChainCompleted / TaskChainError 中的终态回调即证明回调链路通。
        # 任务链在游戏内是否成功取决于设备当前画面（StartUp 会重试 50 次后报
        # TaskChainError，实测约 104s），不把它当作内核层冒烟的失败；这里把终态名字
        # 打出来，成功与否由人工看回执。
        print(
            f"[hardware] {SHORTEST_TASK} 终态回调：{terminal['name']}"
            f"（成功口径 {SUCCESS_CALLBACK}；task_id={task_id}，details={terminal['details']!r}）",
            flush=True,
        )
        assert terminal["name"] in TERMINAL_CALLBACKS, core.diag(
            f"终态回调应属于 {'/'.join(sorted(TERMINAL_CALLBACKS))}，实际 {terminal['name']}"
        )
        details = terminal["details"]
        assert isinstance(details, dict) and details.get("taskchain") == SHORTEST_TASK, core.diag(
            f"终态回调应属于本次提交的 {SHORTEST_TASK} 任务链，实际 details={details!r}"
        )

        stopped = await core.client.stop()
        assert stopped is True, core.diag("任务结束后 client.stop() 应返回 True")

    _run_hardware_case(case, tmp_path)
