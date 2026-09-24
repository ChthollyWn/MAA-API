"""无内核端到端联调：FakeAsst 子进程 + CoreSupervisor + CoreClient 全链路（M1-11）。

本文件是 M1 的「不用真机也能跑通全链路」验收（docs/03 §8）：三段接线**全是真的**，
只把内核换成替身 ——

- ``CoreSupervisor`` 用 ``multiprocessing.get_context("spawn")`` 真起一个子进程，
  真跑「SHUTDOWN 等超时 → terminate → kill」的关机路径；
- 子进程跑真实的 ``maa_api.core.worker.core_worker_main``（启动序列、回调桥接、命令循环、
  ``GET_IMAGE`` 落盘），唯一替换的是 ``boot_config["asst_factory"]`` =
  ``tests.fakes.fake_asst:FakeAsst``（M1-05 交付，本卡不改）：不 dlopen 内核、不连设备；
- 主进程跑真实的 ``CoreClient``：消费线程是 ``event_queue`` 的唯一消费者
  （``set_dispatcher`` 由它声明，M1-08 的启动门禁因此通过），两级 Future、命令超时、
  事件分派全部走真实实现。

覆盖 5 条链路：① 启动 → READY → ``get_version``；② ``CONNECT`` 两级 Future（受理结果 +
``msg=4`` 的 ``AsyncCallInfo``）；③ ``APPEND_TASK`` → ``START`` → TaskChainStart /
TaskChainCompleted / AllTasksCompleted → ``running()`` 变假；④ ``SCREENCAP`` + ``GET_IMAGE``
落盘传引用（``CMD_RESULT`` 里没有任何图像字节）；⑤ 优雅关闭（退出码 0）+ 消费线程退出 +
二次启动仍 READY 且未误触发退避。

**实测口径（实测优先于任务卡措辞，已记入 ``docs/ENVIRONMENT.md``）**：

1. ``FakeAsst.connect_async`` 在 worker 的 ``_dispatch`` 内**同步**发回调，``CMD_RESULT``
   要等 ``_dispatch`` 返回后才入队（M1-07 代码事实 / M1-09 实测），因此父进程先处理
   ``AsyncCallInfo``、后处理 ``CONNECT`` 的 ``CMD_RESULT``——与卡面写的顺序相反。
   这正是 ``CoreClient._early_async`` 的缓存兑现路径：回调先到 → 缓存 → 受理结果到达时立即
   兑现，否则 ``connect()`` 会白等一个完整超时（见 :func:`test_connect_two_level_future`）。
2. ``msg=2`` 的 ``ConnectionInfo``（``what == "Connected"``）先于 ``msg=4`` 的
   ``AsyncCallInfo`` 到达，它只是中间态；异步连接成败只认 ``details.details.ret``
   （M1-01 真机实测的层级：关联键 ``async_call_id`` 与 ``what`` 在顶层，``ret`` 双层嵌套）。

不重复 M1-10（``tests/core/test_crash_recovery.py``）已覆盖的崩溃注入 / 退避序列 / 维护窗口
/ 伪崩溃断言：本文件只跑正常路径。

纪律：所有等待 ≤ :data:`WAIT_TIMEOUT`（5 秒），失败信息里带已收到的事件序列
（:meth:`_Harness.diag`）；每个用例结束都停消费线程、停子进程并收尾队列，autouse fixture
再兜底回收 ``maa-core`` 子进程。
"""

from __future__ import annotations

import asyncio
import contextlib
import multiprocessing
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

import pytest

from maa_api.core.client import CoreClient
from maa_api.core.enums import Message
from maa_api.core.supervisor import CoreState, CoreSupervisor
from tests.fakes.fake_asst import FakeAsst

#: 所有等待的上限（秒）：用例纪律要求 ≤5 秒。
WAIT_TIMEOUT = 5.0

#: 轮询间隔（秒）。
POLL_INTERVAL = 0.01

#: boot_config 契约里的假分辨率与假截图：4×4 RGB = 48 字节（与 FakeAsst 的
#: ``resolution`` / ``screenshot`` 对齐，worker 侧据此走 Pillow JPEG 编码分支）。
FAKE_RESOLUTION = (4, 4)
FAKE_SCREENSHOT = b"\x00" * (FAKE_RESOLUTION[0] * FAKE_RESOLUTION[1] * 3)

#: FakeAsst 自报的版本字符串（``tests/fakes/fake_asst.py`` 的 ``_version``）。
FAKE_VERSION = "FakeAsst/0.1"

#: ``GET_IMAGE`` 的 ``CMD_RESULT.data`` 允许出现的键：只有路径与图像元信息（docs/02 §3.3）。
GET_IMAGE_DATA_KEYS = frozenset({"path", "size", "width", "height", "encoding"})


def _boot_config(user_dir: Path) -> dict:
    """最小 boot_config（纯数据、可 pickle）：FakeAsst 不读文件系统、不加载动态库。"""
    return {
        "maa_path": ".",
        "user_dir": str(user_dir),
        "incremental_paths": [],
        "instance_options": {},
        "asst_factory": "tests.fakes.fake_asst:FakeAsst",
        "asst_factory_kwargs": {
            "script": "success",
            "screenshot": FAKE_SCREENSHOT,
            "resolution": FAKE_RESOLUTION,
        },
    }


class _Harness:
    """真实 supervisor + 真实 CoreClient + 观测记录，保证用例结束不留子进程。

    观测钩子全部经 ``CoreClient.on`` 的公开分派表注册（不碰 Queue、不读 supervisor 私有
    状态）：``order`` 记录事件被**处理**的顺序（READY / CALLBACK / CMD_RESULT），
    ``callbacks`` 保留跨进程过来的 CALLBACK 原始载荷供断言。
    """

    def __init__(self, user_dir: Path, **supervisor_kwargs: Any) -> None:
        self.order: list[str] = []
        self.callbacks: list[dict] = []
        self.cmd_results: list[dict] = []
        self.ready_payloads: list[dict] = []
        self.logs: list[str] = []
        self.fatals: list[dict] = []
        #: cmd_id → 命令类型（CMD_RESULT 本身不带类型，靠 track_command 补全）。
        self.command_types: dict[str, str] = {}

        user_dir.mkdir(parents=True, exist_ok=True)
        self.supervisor = CoreSupervisor(_boot_config(user_dir), **supervisor_kwargs)
        self.client = CoreClient(
            self.supervisor,
            connect_timeout=WAIT_TIMEOUT,
            accept_timeout=WAIT_TIMEOUT,
        )
        self._instrument_track_command()

        # 卡面契约：显式把 READY / FATAL / PONG 接进 supervisor 状态机（CoreClient 构造时
        # 已默认接好，重复注册幂等无害；显式写出来让接线可见、也验证这条契约本身）。
        self.client.on("READY", self.supervisor.handle_ready)
        self.client.on("FATAL", self.supervisor.handle_fatal)
        self.client.on("PONG", self.supervisor.handle_pong)
        # 观测处理器（注册在状态机之后，不改 supervisor 的时序）。
        self.client.on("READY", self._record_ready)
        self.client.on("CMD_RESULT", self._record_cmd_result)
        self.client.on("CALLBACK", self._record_callback)
        self.client.on("LOG", self._record_log)
        self.client.on("FATAL", self._record_fatal)

    # ---- 观测 ----

    def _instrument_track_command(self) -> None:
        """包装 ``supervisor.track_command`` 记录 cmd_id → 命令类型。

        ``CoreClient._send`` 在 put 前调用这个**公开契约方法**，包装它不需要动
        ``maa_api/`` 下的任何实现。告警 / 失败时用它把 CMD_RESULT 还原成命令名。
        """
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

    def callback_names(self) -> list[str]:
        return [event["name"] for event in self.callbacks]

    def consumer_thread(self) -> Optional[threading.Thread]:
        """``CoreClient`` 的消费线程（没有公开读法，仅用于断言 ``close()`` 后线程退出）。"""
        return getattr(self.client, "_thread", None)

    def diag(self, note: str = "") -> str:
        """失败信息：已收到的事件序列 + 当前状态 + 崩溃记录（保证超时可见原因）。"""
        return (
            f"{note} | state={self.supervisor.state} pid={self.supervisor.pid} "
            f"exitcode={self.supervisor.exitcode} last_crash={self.supervisor.last_crash} "
            f"order={self.order} callbacks={self.callback_names()} "
            f"cmd_results={[(item['type'], item['ok']) for item in self.cmd_results]} "
            f"fatals={self.fatals} logs={self.logs[-6:]}"
        )

    # ---- 等待（全部 ≤WAIT_TIMEOUT，超时抛带 diag 的 AssertionError） ----

    async def wait_for(
        self, predicate: Callable[[], bool], note: str, timeout: float = WAIT_TIMEOUT
    ) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if predicate():
                return
            await asyncio.sleep(POLL_INTERVAL)
        raise AssertionError(self.diag(note))

    # ---- 生命周期 ----

    async def start(self) -> None:
        """卡面契约：先 ``start_consumer()``（在事件循环内调用以立刻绑定 loop）再 start()。"""
        self.client.start_consumer()
        await self.supervisor.start(timeout=WAIT_TIMEOUT)
        await self.wait_for(
            lambda: self.supervisor.state is CoreState.READY, "supervisor.start() 后未到 READY"
        )

    async def stop(self, *, graceful: bool = True) -> float:
        """停子进程并返回耗时（秒）；等待上限 5 秒，失败信息里带事件序列。"""
        started = time.monotonic()
        try:
            await asyncio.wait_for(
                self.supervisor.stop(graceful=graceful, timeout=WAIT_TIMEOUT),
                timeout=WAIT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise AssertionError(self.diag("supervisor.stop() 超过 5 秒未返回")) from None
        return time.monotonic() - started

    async def __aenter__(self) -> "_Harness":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                self.supervisor.stop(graceful=False, timeout=2.0), timeout=WAIT_TIMEOUT
            )
        self.client.close()
        for queue in (self.supervisor.cmd_queue, self.supervisor.event_queue):
            # 子进程已停、消费线程已退出，收尾当前代的 Queue，避免 SemLock 留给 GC。
            with contextlib.suppress(Exception):
                queue.cancel_join_thread()
                queue.close()


@pytest.fixture(autouse=True)
def _reap_core_children() -> Iterator[None]:
    """兜底：任何用例结束后都不允许残留 maa-core 子进程。"""
    yield
    for child in multiprocessing.active_children():
        if child.name == "maa-core" and child.is_alive():
            child.kill()
            child.join(timeout=2.0)


# ---------------------------------------------------------------------------
# ① 启动与就绪：READY 到达 + state=READY + get_version 返回 FakeAsst 的版本
# ---------------------------------------------------------------------------


def test_start_ready_and_version(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _Harness(tmp_path) as core:
            await core.start()

            assert core.supervisor.state is CoreState.READY, core.diag("启动后 state 应为 READY")
            assert core.supervisor.pid is not None, core.diag("READY 后必须有子进程 pid")
            assert core.ready_payloads, core.diag("READY 事件必须经消费线程被处理")
            payload = core.ready_payloads[-1]
            assert payload.get("pid") == core.supervisor.pid, core.diag(
                "READY.payload.pid 应为子进程 pid"
            )
            assert payload.get("version") == FAKE_VERSION, core.diag(
                f"READY.payload.version 应为 {FAKE_VERSION}"
            )

            # 命令链路：主进程 put → 子进程命令循环 → CMD_RESULT → Future。
            version = await core.client.get_version()
            assert version == FAKE_VERSION, core.diag(f"get_version 应返回 {FAKE_VERSION}")
            assert core.order[0] == "READY", core.diag("READY 应是第一条被处理的事件")
            assert "CMD_RESULT:GET_VERSION" in core.order, core.diag("GET_VERSION 回执未处理")

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# ② CONNECT 两级 Future：第一级受理结果 + 第二级 AsyncCallInfo
# ---------------------------------------------------------------------------


def test_connect_two_level_future(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _Harness(tmp_path) as core:
            await core.start()

            connected = await core.client.connect("adb", "127.0.0.1:5555")
            assert connected is True, core.diag("FakeAsst 的 connect_async 回 ret=true")

            # 第二级回调：跨进程过来的原始形态（子进程只做 json.loads，不翻译语义），
            # 层级照 M1-01 真机实测：async_call_id / what 在顶层，ret 在 details.details.ret。
            infos = [event for event in core.callbacks if event["name"] == "AsyncCallInfo"]
            assert len(infos) == 1, core.diag("应恰好一条 AsyncCallInfo")
            info = infos[0]
            assert info["msg"] == int(Message.AsyncCallInfo) == 4, core.diag(
                "AsyncCallInfo 的 msg 整数值必须是 4"
            )
            details = info["details"]
            assert details["what"] == "Connect", core.diag("调用类型在顶层 what")
            assert details["details"]["ret"] is True, core.diag("成功标志在 details.details.ret")
            call_id = details["async_call_id"]
            assert isinstance(call_id, int), core.diag("关联键 async_call_id 在顶层且是 int")

            # 第一级受理结果：CONNECT 的 CMD_RESULT.data 只回受理编号（非成功判据）。
            receipts = [item for item in core.cmd_results if item["type"] == "CONNECT"]
            assert receipts and receipts[0]["ok"] is True, core.diag("CONNECT 应有 ok 回执")
            assert receipts[0]["data"] == {"async_call_id": call_id}, core.diag(
                "CMD_RESULT.data 应只含受理编号"
            )

            # 事件处理顺序：实测（M1-07 代码事实 / M1-09 实测，已记入 ENVIRONMENT.md）FakeAsst
            # 在 _dispatch 内同步发回调，CMD_RESULT 随后才入队，所以 AsyncCallInfo 先被处理
            # ——与卡面写的「CMD_RESULT 先于 AsyncCallInfo」相反，按实测断言。这条顺序正是
            # CoreClient._early_async 的缓存兑现路径（回调先到 → 缓存 → 受理结果到达时立即
            # 兑现），也是本用例能秒回而不是白等一个完整超时的原因。
            async_at = core.order.index("CALLBACK:AsyncCallInfo")
            receipt_at = core.order.index("CMD_RESULT:CONNECT")
            assert async_at < receipt_at, core.diag(
                "实测顺序应为 AsyncCallInfo 先于 CONNECT 的 CMD_RESULT（_early_async 路径）"
            )
            # msg=2 的 ConnectionInfo 先报 Connected，但只是中间态：连接成败只认 msg=4。
            assert "CALLBACK:ConnectionInfo" in core.order, core.diag("应有中间态 ConnectionInfo")
            assert core.callbacks[0]["name"] == "ConnectionInfo", core.diag(
                "第一条回调应是中间态 ConnectionInfo"
            )
            assert core.callbacks[0]["details"].get("what") == "Connected", core.diag(
                "中间态 ConnectionInfo 的 what 应为 Connected"
            )
            assert (
                core.order.index("CALLBACK:ConnectionInfo") < async_at
            ), core.diag("Connected 只是中间态，必须先于 AsyncCallInfo 出现")

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# ③ 任务回调全链路：APPEND_TASK → START → 三条回调 → running() 变假
# ---------------------------------------------------------------------------


def test_task_callback_pipeline(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _Harness(tmp_path) as core:
            await core.start()

            task_id = await core.client.append_task("Fight", {"stage": "1-7", "times": 1})
            assert isinstance(task_id, int) and task_id > 0, core.diag(
                "APPEND_TASK 应返回非零内核 task id"
            )

            started = await core.client.start()
            assert started is True, core.diag("START 应返回 True")

            await core.wait_for(
                lambda: len(core.callbacks) >= 3,
                "未收到 TaskChainStart/TaskChainCompleted/AllTasksCompleted 三条回调",
            )
            names = core.callback_names()
            assert names[:3] == [
                "TaskChainStart",
                "TaskChainCompleted",
                "AllTasksCompleted",
            ], core.diag("任务链回调必须按剧本顺序到达")
            msgs = [event["msg"] for event in core.callbacks[:3]]
            assert msgs == [
                int(Message.TaskChainStart),
                int(Message.TaskChainCompleted),
                int(Message.AllTasksCompleted),
            ], core.diag("msg 整数值必须与 maa_api.core.enums.Message 逐项一致")
            # AllTasksCompleted 实测是 3（不是 1..12 连号的 12）：照连号硬编码会认错类型。
            assert msgs[2] == 3, core.diag("AllTasksCompleted 的实测整数值是 3")

            # CALLBACK 跨进程后仍是原始形态：msg 是 int，details 是纯 dict。
            for event in core.callbacks[:3]:
                assert isinstance(event["msg"], int) and not isinstance(
                    event["msg"], bool
                ), core.diag("CALLBACK.msg 应是内核原始的 int 取值")
                assert isinstance(event["details"], dict), core.diag(
                    "CALLBACK.details 应是 json.loads 出来的纯 dict"
                )

            # details 里的 taskid 与 append 返回一致（FakeAsst 只给 TaskChain* 补 taskid；
            # AllTasksCompleted 由剧本原样转发，M1-05 交付不改）。
            for event in core.callbacks[:2]:
                assert event["details"].get("taskid") == task_id, core.diag(
                    "TaskChain* 的 details.taskid 应等于 append_task 的返回值"
                )
                assert event["details"].get("taskchain") == "Fight", core.diag(
                    "TaskChain* 的 details.taskchain 应是 append 的 'Fight'"
                )

            # 任务链跑完后 running() 变假（查 FakeAsst 实例状态，经真实命令往返）。
            running = True
            loop = asyncio.get_running_loop()
            deadline = loop.time() + WAIT_TIMEOUT
            while loop.time() < deadline:
                running = await core.client.running()
                if running is False:
                    break
                await asyncio.sleep(POLL_INTERVAL)
            assert running is False, core.diag("任务链结束后 running() 应为 False")

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# ④ GET_IMAGE 落盘传引用：文件存在且非空，CMD_RESULT 里没有图像字节
# ---------------------------------------------------------------------------


def test_screencap_and_get_image_write_to_disk(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _Harness(tmp_path) as core:
            await core.start()

            shot = tmp_path / "shot.jpg"
            path = await core.client.screencap(save_to=shot)
            assert path == shot, core.diag("screencap 应返回 worker 回传的落盘路径")
            assert path.exists(), core.diag("截图必须真的落盘")
            first_size = path.stat().st_size
            assert first_size > 0, core.diag("截图文件不得为空")
            # worker 在分辨率已知时用 Pillow 编码 JPEG（magic FF D8）。
            assert path.read_bytes()[:2] == b"\xff\xd8", core.diag("已知分辨率应落 JPEG")

            again = tmp_path / "again.jpg"
            data = await core.client.get_image(save_to=again)
            assert isinstance(data, dict), core.diag("get_image 应返回 worker 的 data dict")
            assert set(data) >= {"path", "size", "encoding"}, core.diag(
                "data 至少含 path/size/encoding"
            )
            assert Path(data["path"]) == again and again.exists(), core.diag(
                "get_image 落盘路径不符"
            )
            assert (data.get("width"), data.get("height")) == FAKE_RESOLUTION, core.diag(
                "图像元信息应带 FakeAsst 的分辨率"
            )
            assert again.stat().st_size > 0, core.diag("get_image 落盘文件不得为空")
            assert data["size"] == again.stat().st_size, core.diag("size 应是落盘文件字节数")
            assert data["encoding"] == "jpeg", core.diag("分辨率已知时 encoding 应为 jpeg")

            # 落盘传引用（docs/02 §3.3）：CMD_RESULT 只回路径与元信息，绝不能带图像字节。
            results = [item for item in core.cmd_results if item["type"] == "GET_IMAGE"]
            assert len(results) == 2, core.diag("screencap 与 get_image 各发一条 GET_IMAGE")
            for item in results:
                payload = item["data"]
                assert isinstance(payload, dict), core.diag("GET_IMAGE 的 data 应是 dict")
                assert set(payload) <= GET_IMAGE_DATA_KEYS, core.diag(
                    f"GET_IMAGE 只允许回 {sorted(GET_IMAGE_DATA_KEYS)}，实际 {sorted(payload)}"
                )
                assert all(
                    isinstance(value, (str, int, float, bool)) for value in payload.values()
                ), core.diag("GET_IMAGE 的 data 不得含 bytes 等二进制载荷")
                assert isinstance(payload["path"], str) and payload["path"], core.diag(
                    "GET_IMAGE 必须回非空 path"
                )
                assert payload["size"] == Path(payload["path"]).stat().st_size, core.diag(
                    "GET_IMAGE 的 size 应与落盘文件字节数一致"
                )

            # screencap 的第二级 Future 同样由 AsyncCallInfo 兑现（what 字段区分调用类型）。
            whats = [
                event["details"].get("what")
                for event in core.callbacks
                if event["name"] == "AsyncCallInfo"
            ]
            assert "Screencap" in whats, core.diag(
                f"SCREENCAP 的第二级结果应由 AsyncCallInfo 兑现，实测 what={whats}"
            )

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# ⑤ 优雅关闭 + 二次启动：STOPPED / 进程退出 / 消费线程退出 / 再 READY 无伪崩溃
# ---------------------------------------------------------------------------


def test_graceful_shutdown_then_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _Harness(tmp_path) as core:
            await core.start()
            first_pid = core.supervisor.pid
            assert isinstance(first_pid, int), core.diag("首启应有 pid")
            consumer = core.consumer_thread()
            assert consumer is not None and consumer.is_alive(), core.diag("消费线程应在跑")

            # 优雅关闭：SHUTDOWN 命令走通（退出码 0；terminate 是 -15、kill 是 -9）。
            elapsed = await core.stop()
            assert core.supervisor.state is CoreState.STOPPED, core.diag("stop() 后应为 STOPPED")
            assert core.supervisor.exitcode == 0, core.diag(
                "SHUTDOWN 应让 worker 正常退出（exitcode=0），而不是被 terminate/kill"
            )
            assert elapsed < WAIT_TIMEOUT, core.diag(f"优雅关闭耗时 {elapsed:.2f}s，超过 5 秒")
            assert first_pid not in {
                child.pid for child in multiprocessing.active_children()
            }, core.diag("关闭后不得残留子进程")

            core.client.close()
            assert not consumer.is_alive(), core.diag("client.close() 后消费线程必须退出")
            assert core.consumer_thread() is None, core.diag("close() 应清理线程引用")

            # 二次启动：close() 之后可重新 start_consumer()，队列按代轮换由 supervisor 负责；
            # 手动 start() 复位退避预算，不得留下任何伪崩溃记录。
            core.client.start_consumer()
            await core.supervisor.start(timeout=WAIT_TIMEOUT)
            await core.wait_for(
                lambda: core.supervisor.state is CoreState.READY,
                "二次启动未回到 READY（退避/重启计数被误触发？）",
            )
            assert core.supervisor.pid != first_pid, core.diag("二次启动应换新子进程")
            assert core.supervisor.last_crash is None, core.diag(
                "stop→start 的正常路径不得记崩溃、不得触发退避"
            )
            assert await core.client.get_version() == FAKE_VERSION, core.diag(
                "二次启动后命令链路应恢复"
            )

    asyncio.run(scenario())
