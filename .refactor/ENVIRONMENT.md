# 环境事实（跨会话共享）

> 本文件是**已实测确认**的环境事实，供拆卡者与 worker 共用。
> 目的：不让每个全新上下文的 agent 重复发现同一件事。
> 每条都必须来自实测，推测请标注「未验证」。
>
> **worker 可以在本文件对应小节末尾追加新发现的事实**（只追加、不改写既有条目、
> 不 `git add`，编排器会随台账一起提交）。这是 worker 被允许触碰 `.refactor/` 的
> 唯一例外 —— 踩过的坑如果不写下来，下一个里程碑的 worker 会再踩一次。

## 基础工具链

- **`python` 不在 PATH，只有 `python3`（3.13.3）。** verify 命令里写 `python -c ...` 会永远失败。
- 仓库内 venv：`.venv/`（M0-01 建立），解释器 `.venv/bin/python`，已 `pip install -e .`。
- `poetry lock` 若在受限沙箱下需要 `POETRY_CACHE_DIR=$(mktemp -d)`（默认缓存目录在仓库外）。
- Node v23.11.0；pnpm 11.1.3。adb 36.0.0 于 `/opt/homebrew/bin/adb`。

## MaaCore 内核

- 本地库：`resource/lib/maa/Darwin/`（v6.17.5）与 `resource/lib/maa/Linux/`（v6.17.2）。
- macOS 加载需 `DYLD_LIBRARY_PATH=/Users/chtholly/Developer/WorkSpace/MAA-API/resource/lib/maa/Darwin`。
- 真机/模拟器已连接：`127.0.0.1:5555`（`adb devices` 可见）。
- **`AsstMsg` 取值不是 1..12 连号**（v6.17.5 实测）：`InternalError=0`、`InitFailed=1`、`ConnectionInfo=2`、`AllTasksCompleted=3`、`AsyncCallInfo=4`、`Destroyed=5`、`TaskChain*=10000..10004`、`SubTask*=20000..20004`。照连号硬编码会把 `ConnectionInfo`(2) 当成 `TaskChainError`。
- **`AsstAppendTask` 校验任务必填参数，缺参数时静默返回 0**（不抛异常、无回调）。实测：`("StartUp", {})`→0、`("StartUp", {"client_type":"Official"})`→1；`("Infrast", {})`→0、带 `{"facility":["Mfg"]}`→2；`("Recruit", {})`/`{"times":4}` 均→0；`Award`/`Fight`/`Mall`/`Roguelike`/`OperBox`/`Depot` 空参数即可。`task_id == 0` 必须当失败处理。
- **`AsstSetUserDir` 要求目录已存在**，否则返回 False；旧封装 `Asst.load()` 把它 `&=` 进返回值，表现为「内核加载失败」。
- **按绝对路径 `ctypes.CDLL(<dir>/libMaaCore.dylib)` 不依赖 `DYLD_LIBRARY_PATH`**：`env -u DYLD_LIBRARY_PATH` 实测也能 dlopen（依赖走 @loader_path/@rpath）。那条环境变量是冗余保险。
- **`AsstGetTasksList(handle)`（`restype=c_char_p`）在本内核上直接段错误**：`<user_dir>/crash.log` 记 `SIGSEGV`，进程退出码是 **1**（不是 139/-11）。即 MaaCore 把 native 崩溃转成 exit 1 + crash.log，supervisor 不能靠「exitcode 为负」判崩溃。
- **`import maa_api.model.core.asst` 有 import 期副作用**：连带 import `maa_api.config.config`，后者 mkdir `static/ resource/lib resource/log resource/temp` 并可能拷贝 `daily_task_template.json`（CWD 非仓库根时 RuntimeError）。子进程启动序列别走这条链。
- **spawn 子进程 + `ctx.Queue()` 已验证可用**：入口必须是模块级函数、回调的 `event_queue` 靠子进程模块级全局持有（`ctypes.c_void_p(id(arg))` 指针法跨进程无效）；子进程继承父进程环境变量。实测 spawn→内核 READY 约 0.16–0.29s，SIGKILL→exitcode -9 约 0.05s。
- **`AsstGetImage` 返回的是 PNG 编码字节，不是裸 RGB**（M1-04 真机实测，v6.17.5 + 127.0.0.1:5555，2560x1440）：magic `\x89PNG\r\n\x1a\n`、本次 637585 字节；`size` 只是缓冲区容量上界，`get_image` 必须按返回值截断，落盘方应把它当**已编码图像**直接写文件，不要再做 JPEG 编码。
- **`AsstGetImageBgr` 返回裸 BGR，但长度不等于 `w*h*3`**：同一次连接（ResolutionGot=2560x1440，`_expected_image_size()=11059200`）实测返回 2764800 字节 = 1280x720x3（疑似内核内部缩放图），首字节 `\x1b\x1b\x1b`。任何消费方都不能假设返回长度等于分辨率推算值。
- **`AsstGetTasksList` 用正确的三参签名 `(handle, int32* buff, size)` 调用是安全的**：M1-04 在未连接状态下实测返回 0（无段错误）；此前记录的段错误来自错误的 `restype=c_char_p` / 缺参数调用，不是该 API 本身的缺陷。
- **`AsstGetMapLevelKey` 用 `ctypes.Structure` 作 restype 在 macOS 可用**（M1-04 实测）：`AsstGetMapLevelKey("1-7")` 返回 `{'stage_id': 'main_01-07#f#', 'code': '1-7', 'level_id': 'obt/main/level_main_01-07', 'name': '暴君'}`；查不到时四字段全 NULL。`_has_symbol` 探测 + 条件绑定在任何平台都不会因缺符号报错。
- **异步连接回调序列实测**（M1-04 走 `maa_api/core/asst.py` 复现 M1-01）：`connect_async(...)` 返回 1，回调序列 `[2, 2, 2, 2, 2, 4]`，msg=4 载荷顶层 `async_call_id=1`、`details.details.ret=true`、顶层 `what="Connect"`；`ResolutionGot` 的 `what` 与 `width/height` 分别在载荷顶层与 `details` 里，缓存后 `last_resolution()==(2560,1440)` 成立。
- **CFUNCTYPE 包装的回调桥接无法接受 Python 对象作 arg**（M1-07 实测，Python 3.13.3）：`Asst.CallBackType` 的第三参是 `c_void_p`，`wrapped(msg, b"...", event_queue)` 会在**进入函数体之前**抛 `ctypes.ArgumentError: 'Queue' object cannot be interpreted as ctypes.c_void_p`；而 `FakeAsst` 这类纯 Python 替身恰恰是直接把队列对象当 arg 调用桥接函数（`_emit` 里 `callback(raw_message, raw_details, self.arg)`）。所以 `maa_api/core/worker.py::_callback_bridge` 不能装饰 `@Asst.CallBackType`：真实 `Asst.__init__` 自带一层 CFUNCTYPE trampoline（回调最终拿到的是整数指针），替身则直接调裸函数。桥接内 `ctypes.cast(arg, ctypes.py_object).value` 对 `int` 与 `ctypes.c_void_p` 两种指针形态都可用（实测），对普通对象抛 `ArgumentError`，据此回退模块级 `_EVENT_QUEUE` 即可同时满足两条路径。
- **`multiprocessing.Queue` 不能跨子进程代际复用**（M1-08 实测，Python 3.13.3）：`Queue.get(timeout=None)` 在阻塞读期间**持有内部读锁**（源码是 `with self._rlock: self._recv_bytes()`），子进程被 SIGKILL / OOM 杀死时锁不会释放；把同一对 Queue 再交给下一个子进程，现象是「新进程 READY 正常上报，但永远收不到任何命令」（`get()` 卡在锁上）。子进程侧 `Queue.put` 的 feeder 线程也可能在对端已死后因 BrokenPipeError 退出，此后 put 只进本地 buffer 不再上线。结论：**每个内核进程代际新建一对 Queue**，旧 `cmd_queue` 用 `cancel_join_thread()+close()`，旧 `event_queue` 只丢引用、不主动 close（消费者可能正阻塞在它的 `get()` 上）。`CoreSupervisor` 已按此实现（`_rotate_queues`），因此**消费循环必须每轮重新读 `supervisor.event_queue`，不能把 Queue 对象缓存在局部变量里**（M1-09 的 CoreClient 务必照此写，否则 M1-11/M1-13 会在内核重启后静默收不到事件与命令回执）。
- **丢弃旧 Queue 后要显式 `gc.collect()`**：SemLock 析构会在 `multiprocessing.resource_tracker` 里 unregister，若被后续任意一次分配触发的 GC 撞上 tracker 内部锁，会刷 `UserWarning: ResourceTracker called reentrantly ... might leak`（M1-08 实测，每轮换队列约 2 条）。丢掉引用后立刻 `gc.collect()` 可完全消除。
- **`CoreSupervisor` 的启动门禁与退避语义**（M1-08 交付，M1-09/M1-10/M1-11 依赖）：`start()/restart()` 之前必须 `supervisor.set_dispatcher(fn)` 声明 `event_queue` 的唯一消费者，否则抛 `AppError(CORE_START_FAILED)`（M1-09 的 CoreClient 必须在 `start_consumer()` 或构造时调用它，M1-11/M1-13 才不会被门禁挡住）。退避预算 `_restart_attempts` **跨自动重启累计**（崩溃 → 重启 → READY → 再崩 继续计数），只在手动 `start()/restart()` 时复位；`max_restart_attempts=5` 表示允许 5 次自动重启，第 6 次崩溃才转 FAILED——M1-10 的注入测试按这个语义断言。
- **`AsyncCallInfo` 回调可能先于 `CMD_RESULT` 到达，消费方必须缓存兑现**（M1-09 实测 + M1-07 代码事实）：`FakeAsst.connect_async/click/screencap` 在 `_dispatch` 内**同步** `_emit(AsyncCallInfo)`，而 worker 的 `CMD_RESULT` 要等 `_dispatch` 返回后才入队，因此父进程先收到 msg=4 的 `CALLBACK`、后收到带 `async_call_id` 的受理结果。`CoreClient` 用 `_early_async`（call_id→ret，TTL 30s）缓存并在登记第二级 Future 时立即兑现，否则 `connect()` 会白等一个完整超时（M1-11 的全链路用例正是这个时序）。真机上回调来自内核回调线程，与命令循环并发，同样存在这个窗口。
- **`CoreClient` 的分派契约**（M1-09 交付，M1-11/M1-13 依赖）：`client.on(type, handler)` 的 handler 收到的是事件 **payload** dict（不是完整事件），因此 `client.on('READY'|'PONG'|'FATAL', sup.handle_*)` 可直接接线（`handle_*` 的入参就是 payload）；`READY/PONG/FATAL` 已被 CoreClient 默认转发给 supervisor（重复注册一遍幂等无害）；`start_consumer()` 必须在 async 上下文里调用（或构造时传 `loop=`），否则事件会先进延迟缓存、等第一次 `_send` 绑定 loop 后才补投；`close()` 之后可再次 `start_consumer()` 重启消费。
- **`FakeAsst` 的 `os._exit(-11)` 崩溃注入，父进程观测到的 exitcode 是 245**（M1-10 实测，Python 3.13.3/macOS）：POSIX 退出状态只保留低 8 位（`-11 & 0xFF = 0xF5 = 245`），`multiprocessing.Process.exitcode` 既不是 -11 也不是负值；docs/03 §8 与 M1-10 卡面写的 `last_crash["exitcode"] == -11` 是文档推测。要拿到真正的 `-11`（SIGSEGV 语义）应让子进程对自己发信号（`os.kill(os.getpid(), signal.SIGSEGV)`），改 FakeAsst 归 M1-05。另：`os._exit` 不跑清理，会丢掉 Queue feeder 尚未 flush 的事件——crash 剧本在 exit 前发出的那条 `TaskChainStart` CALLBACK 实测到不了父进程，不能拿它断言「崩溃现场含 CALLBACK」；用「先让剧本产出回调并被消费、再 SIGKILL」的路径才确定（`tests/core/test_crash_recovery.py` 即如此）。
- **`CoreSupervisor.restart()` 在旧子进程存活时会误报崩溃**（M1-10 实测，M1-08 缺陷，未修）：`_spawn_process` 不停上一代监控线程，旧 `_liveness_loop` 持有旧 `Process` 引用，把 `restart()` 的优雅停止判成 `process_exit`/`exitcode=0`，记一条伪崩溃并调度自动重启；伪重启的 `_reap_process()` 会 `terminate()` 刚起来的新进程（`CoreClient` 侧表现为 `AppError(CORE_START_FAILED, "内核子进程在 READY 之前退出（exitcode=-15）")`），或至少多换一代进程。包在 `acquire_maintenance()` 里也一样：窗口内 `_handle_crash` 被抑制（旧线程只置 `reported=True`），窗口一退出立刻补记伪崩溃。规避：只在 `CRASHED`/`STOPPED`（旧进程已死）后 `restart()`；修复应由 M1-08 在 `_spawn_process` 里先 `_stop_monitors_locked()`。M1-10 已用非严格 xfail 钉住（`tests/core/test_crash_recovery.py::test_restart_from_ready_records_no_spurious_crash`）。
- **`stop()` → `start()` 是安全路径，可放心用于「关内核再拉起」**（M1-11 实测，`tests/core/test_core_pipeline.py`）：`CoreSupervisor.stop()` 走 SHUTDOWN 让 worker 正常返回，父进程实测 `exitcode == 0`（terminate 是 -15、kill 是 -9）；`CoreClient.close()` 之后必须再 `start_consumer()` 才能继续消费事件（否则 READY 无人处理、`start()` 必然超时），随后 `start()` 能再次 READY，`last_crash is None`——退避/重启计数不会被误触发。M1-08 的 `restart()` 伪崩溃缺陷只在旧进程仍存活时触发，stop→start 不经过那条路。全链路（真实 worker + FakeAsst + 真实 supervisor/client）单用例 0.5–1.0s，5 个用例约 3.2–3.4s，无残留子进程。
- **`GET_IMAGE` 的 `CMD_RESULT.data` 实测键集**（M1-11 实测）：分辨率已知时 worker 走 Pillow JPEG 分支，恰好返回 `{path, size, width, height, encoding="jpeg"}`，`size` 等于落盘文件字节数（magic `FF D8`）；`screencap()` 与 `get_image()` 各发一条 GET_IMAGE，data 里没有任何图像 bytes（落盘传引用契约，docs/02 §3.3）。M4 的截图接口按这个键集取用，不要再假设 data 是裸字节。
- **M1-13 命令行冒烟脚本实测（真机 v6.17.5 + `127.0.0.1:5555`，macOS）**：`DYLD_LIBRARY_PATH=$PWD/resource/lib/maa/Darwin .venv/bin/python scripts/core_smoke.py --kill` 全链路 exit 0、最后一行 `SMOKE OK`。分段耗时：READY 0.29s（dlopen+资源）、`AsstAsyncConnect` 的 msg=4 结果 1.26s、`StartUp` + `{"client_type":"Official"}` 的 `TaskChainCompleted` 1.83s、全程 9.5s。维护窗口内 SIGKILL 后状态保持 READY 且 `last_crash=None`（窗口内确实不重启、不记崩溃）；**CRASHED 是退出窗口时由 `acquire_maintenance()` 的 `_reconcile_after_maintenance()` 补记的**（窗口内存活轮询被抑制、只置 `reported=True`），随后 5.0s 退避（默认 `BACKOFF_SECONDS[0]`）→ RESTARTING → READY，新 pid 与旧 pid 不同，`sup.stop()` 后 exitcode 0、无残留子进程。窗口外对旧 pid「再杀一次」实测是 `ProcessLookupError`（`is_alive()`/poll 已回收进程），只能是幂等确认，CRASHED 不可能由这一枪产生。`--fake` 同路径 0.6s，`--fake --kill` 6.2s。

- **`tests/core/test_hardware_smoke.py` 默认全 skip**（M1-14 交付）：闸门是环境变量 `MAA_HW_TESTS`（非空才开）+ `@pytest.mark.hardware`，真机跑法 `MAA_HW_TESTS=1 DYLD_LIBRARY_PATH=$PWD/resource/lib/maa/Darwin .venv/bin/python -m pytest -q -m hardware tests/core/test_hardware_smoke.py`。真机实测分段：`sup.start()` → READY **0.53s**、`connect()` 两级 Future **1.5–3.1s**、截图用例（连接 + screencap + get_image）**1.6–3.5s**；默认真机参数即本文件上面那组（`resource/lib/maa/Darwin` / `/opt/homebrew/bin/adb` / `127.0.0.1:5555`）。
- **真机 `StartUp` 的成败取决于游戏当前画面，不是 IPC 缺陷**（M1-14 实测，2026-09-16 晚，同一台 `127.0.0.1:5555`）：模拟器停在首页 + 「任务/报酬已领取」半透明覆盖层时，`StartUp` 的 `StartUpBegin` 重试 50 次（每次约 2.08s，合计 **104–106s**）后报 `TaskChainError`（`SubTaskError` 的 `details={}`，无原因）；M1-13 的 `scripts/core_smoke.py`（不点击）与 M1-14 的用例（click → back_to_home → StartUp）观测**完全一致**，说明与按键序列无关。真机冒烟对「最短任务」的判据应是「收到 TaskChainStart + Completed/Error 终态回调」，游戏内成功与否由人工看画面；要拿 `TaskChainCompleted` 需先把游戏退回可识别的干净首页。
- **`AsstAsyncClick` 也会发 `msg=4` 的 `AsyncCallInfo`**（M1-14 实测，v6.17.5 + `127.0.0.1:5555`）：一次连接会话实收 2 条 AsyncCallInfo（`what='Connect'` 与 click 那条），`CoreClient.click(1,1)` 的两级 Future 因此正常返回；`CLICK` 的 `CMD_RESULT.data == {"async_call_id": <非零 int>}`。截图链路同样成立：`screencap(save_to=...)` 落盘非空 JPEG（本次 1280x720 / 56286 字节，注意 `AsstGetImage` 的 PNG 实际是 1280x720 而不是 ResolutionGot 的 2560x1440），`get_image()` 的 `CMD_RESULT.data` 真机上仍是 `{path,size,width,height,encoding}` 且无图像字节，与 M1-11 的 FakeAsst 键集一致。
- **上面那条「`restart()` 误报崩溃（未修）」已由 M1-15 修复**：现在 `restart()` / `stop()` / `_spawn_process()` 都在动旧进程（SHUTDOWN / terminate / kill）**之前**先置位监控 stop_event，且 `_spawn_process()` 会 `self._generation += 1`；心跳 / 存活线程绑定自己启动时的代际，换代即自杀，`_handle_crash` 作废旧代际的迟到判定。实测回归：`tests/core/test_crash_recovery.py -k restart_from_ready` 两条用例（维护窗口内 + 窗口外）在移除修复后都会以 `CORE_START_FAILED: ... exitcode=-15` 失败，加上修复后通过。以后新增监控判定时不要让它脱离代际，也不要在停进程之后才停监控线程。

## 验证命令的陷阱

- **`scripts/core_smoke.py --help` 不加载内核**：内核层 import（`maa_api.core.*`）全部延迟到 `main()` 解析参数之后，`python -X importtime scripts/core_smoke.py --help` 实测没有任何 `maa_api` / `tests.fakes` 模块被 import。`--fake` 子进程能 import 到 `tests.fakes.fake_asst` 靠的是脚本模块顶层把仓库根插入 `sys.path`（spawn 子进程继承父进程 `sys.path`），直接执行脚本时 `sys.path[0]` 是 `scripts/` 而不是仓库根。
- **`import maa_api.main` 不能当门禁。** 它在 import 期就走 `Updater().update()` 与内核 `dlopen`，实测约 2.5 分钟且依赖网络与本地资源。verify 请用轻量 import（如 `import maa_api.domain.task`）。
- verify 必须能在「改动前失败、改动后通过」两个方向上真正区分，否则是无意义的门禁。
- **旧 `maa_api/model/util/utils.py` 的 `Message` 原来是普通 `Enum`，`int(成员)` 抛 `TypeError`**（非 `IntEnum` 没有 `__int__`/`__index__`）。M1-03 的 verify 第 2 条用 `int(Old.成员)` 做 ABI 逐项比对，实测因此**永远无法通过**（第 1 次尝试即卡死在这条）。处置：M1-03 把该枚举改为 `IntEnum`（取值一个未动；`Message(msg)`、成员间比较、全量 pytest 语义不变）并随卡提交，M3 删除该模块时一并消失。**以后写涉及旧枚举的 verify 请用 `m.value` 而不是 `int(m)`**，除非确认它是 `IntEnum`。
- **`typing.Protocol` 会在类创建时注入 `__init__ = _no_init_or_replace_init`（一个普通函数）**，`vars(协议类)` 因此天然多出一个「方法」；`@runtime_checkable` 还会把非 callable 的成员记进 `__non_callable_proto_members__`（使 `issubclass()` 抛 TypeError）。所以「协议方法集合 ⊆ 某实现类公开方法」这类按名检查（M1-05 verify 第 3 条）若要求通过，必须把 `__init__` 与只作声明用的成员（如 `call`）换成 **callable 占位对象**——非 function、非 staticmethod，但仍出现在 `vars()` 里、仍让 `callable()` 为真。M1-05 在 Python 3.13.3 实测。
- **macOS 上没有 `timeout` 命令**（GNU coreutils 未装）：`timeout 600 .venv/bin/python -m pytest` 直接 `command not found`（exit 127）。限时请用工具侧超时参数。
- **pytest 测试模块可以直接当 spawn 子进程 target 的宿主模块**（M1-06 实测，Python 3.13.3）：`ctx.Process(target=<模块级函数>)` 时子进程按限定名重新 import `tests.core.test_ipc_contract`，`tests/` 是 package 且 `tests/conftest.py` 已把仓库根插进 `sys.path`（spawn 会继承 `sys.path`），因此无需额外 sys.path 设置即可跑通。代价是**测试模块顶层不能有副作用**（fixture 体内的才安全）。M1-10 做崩溃注入子进程测试可照抄这个形态。
- **spawn 子进程里不要 `cancel_join_thread()`**：父进程侧关闭队列用 `close()+cancel_join_thread()` 防阻塞，但子进程写完必须让 feeder 线程自然 flush（默认退出时 join），否则入队消息可能丢失。

## 编排与执行环境

- 分支 `refactor/v2`；`dev` 停在 `139c4bc`，是回滚锚点。
- worker 由编排器以 `dsh --profile headless` 一次性进程执行，每个 worker 是**全新上下文**。
- Node 23 下官方 `dsh` bin 因 `import.meta.main` 为 undefined 而静默 no-op，必须走包装器 `~/.local/bin/dsh-start.mjs`。
- worker 以 `DSH_PERMISSION_MODE=danger-full-access` 运行（无人值守不能卡审批：headless 没有审批应答方，`approval: ask` 会 fail closed）。

## git 注意事项

- `config.yaml` 已从跟踪中移除（本地保留），模板为 `config.template.yaml`。
- `.refactor/logs/`、`.refactor/state.json`、`.refactor/PAUSE`、`.refactor/TRIGGER`、`.refactor/orchestrator.lock`、`.refactor/orchestrator-src/` 均已 gitignore。
- **worker 只 `git add` 自己的 deliverables**，绝不 `git add -A`：`.refactor/` 下的台账由编排器负责提交。
- **改动共享文件前必须重新读取当前内容，不要基于旧印象整文件重写。** 实测教训：M0-05 卡的
  deliverables 含 `.gitignore`，它把编排器刚追加的 `.refactor/orchestrator.lock` 规则一起覆盖掉了。
  那条规则失效后，失败回滚的 `git clean -fd` 会删掉锁文件，进而可能让两个编排器实例同时驱动同一个
  仓库。凡 deliverables 含被多方改动的文件（`.gitignore`、`pyproject.toml`、`config*.yaml`），
  一律「读当前内容 + 追加/局部替换」，不要整体重写。

## 数据层（M2-01 实测）

实测环境：SQLAlchemy 2.0.54 / SQLModel 0.0.42 / Alembic 1.20.0 / SQLite 3.49.1 / Python 3.13.3。
完整产物：`scripts/probe_sqlmodel_alembic.py`、`tests/fixtures/db_probe_result.json`、
`tests/fixtures/db_probe_findings.md`（全部实验在 `tempfile.mkdtemp()` 内完成，仓库不留 `alembic.ini` / `migrations/` / `*.db`）。

- **`greenlet` 不在 `.venv` 里**（`sqlalchemy` 是裸装，greenlet 只在 `sqlalchemy[asyncio]` extra 里）：
  `create_async_engine('sqlite+aiosqlite:///...')` 后真跑 `connect + execute('select 1')` 抛
  `ValueError: the greenlet library is required to use this function. No module named 'greenlet'`。
  补依赖归 M2-02（M2-01 未改 `pyproject.toml`）。异步测试用同步函数 + `asyncio.run(...)` 即可，无需新增 pytest 插件。
- **`PRAGMA auto_vacuum=INCREMENTAL` 写在初始迁移 `upgrade()` 首行是静默无效的**（M2-01 实测 upgrade 后
  `PRAGMA auto_vacuum` 仍为 0，无任何报错）：Alembic 在跑第一个迁移之前已建好 `alembic_version`，库已非空，
  SQLite 只在空库上立即接受该变更，否则要整库 `VACUUM`。`op.get_context().autocommit_block()` 同样无效。
  **唯一实测生效的放置**：`env.py` 的 `run_migrations_online()` 里、`context.begin_transaction()` 之前，
  于 connection 上执行 `conn.exec_driver_sql("PRAGMA auto_vacuum=INCREMENTAL")`（实测值 2）。
- **`sqlite_autoincrement` 必须显式写**：SQLModel 里只写 `id: int | None = Field(default=None, primary_key=True)`
  不够，要加 `__table_args__ = {"sqlite_autoincrement": True}`，DDL 才是
  `id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT`；不带时 DDL 没有 `AUTOINCREMENT`（末尾行被删后 id 会复用）。
  带 AUTOINCREMENT 的库会多一张内部表 `sqlite_sequence`，可作生效旁证。
- **命名约定必须在任何表定义之前设置**：`SQLModel.metadata.naming_convention = {...}` 若在 `table=True` 模型定义
  之后才赋值，实测无名 `Index` / `UniqueConstraint` / 外键 / 主键全部不跟随约定名（缺失 `ix_`/`uq_`/`fk_`/`pk_`）。
- **batch 重建会静默丢 `AUTOINCREMENT`（重要）**：`op.batch_alter_table(..., recreate="always")` 靠 SQLAlchemy 反射
  重建表，而 SQLite 反射不还原 `sqlite_autoincrement` 表选项 —— upgrade 与 downgrade 之后表都退化成
  `id INTEGER NOT NULL, PRIMARY KEY (id)`。实测解法：给 `batch_alter_table(..., copy_from=<Table>)`，且 `copy_from`
  必须匹配**该方向的迁移前结构**（upgrade 用模型 Table；downgrade 用 `模型表.to_metadata(MetaData())` 再补上新增列），
  两个方向都传才双向保住 AUTOINCREMENT。命名外键与数据在 rebuild 后都保留（匿名约束会丢名）。
- **`alembic revision --autogenerate` 的产物开箱即用会炸**：SQLModel 的 `Field(max_length=...)` 被渲染成
  `sqlmodel.sql.sqltypes.AutoString(length=...)`，但 Alembic 不会自动补 `import sqlmodel`，
  生成的迁移一执行就 `NameError: name 'sqlmodel' is not defined`；把 `import sqlmodel` 写进 `script.py.mako`
  后产物可直接 `upgrade head`（实测）。另：本环境实测 autogenerate **能**保留 `sqlite_where` 部分索引谓词、
  `sqlite_autoincrement=True`、JSON `server_default`、`op.f()` 约定名与外键 `ondelete` —— docs/04 §8.2
  「检测不到部分索引」在本环境未能复现；对已由 `create_all` 建好的库跑 autogenerate 无任何噪声操作。
- **verify 命令里的探针会写 `tests/fixtures/` 两个产物**：`db_probe_result.json` 每次运行都会刷新
  （含时间戳与临时目录路径），需要稳定内容的场景不要直接 diff 该文件。

### M2-01 复跑与「verify 失败但无输出」的排查（第 2 次尝试追加）

- **复跑结论**：第 2 次尝试从 commit `030af1c` 恢复上一轮产物后，完整 verify 链（含 `.venv/bin/python -m pytest -q`）
  全绿；pytest 另单跑 6/6 全绿，并在 10 个 `yes > /dev/null` 占满 CPU 的情况下再跑 1 次仍全绿（本机 10 核）。
  上一轮编排器侧「verify 失败：`.venv/bin/python -m pytest -q`」**不可复现**，且没有失败用例细节留存，按瞬时抖动处置，
  不是产物缺陷（未记入 DEFECTS.md）。
- **编排器 verify 失败时不保留命令输出**：`runVerify()` 逐条以 `bash -lc <cmd>`（cwd=仓库根、无超时）执行，
  失败时只把**命令字符串**写进 `blocked_reason`，捕获到的 stdout/stderr（截尾 600 字符）既不落盘也不进
  `.refactor/logs/orchestrator.log`。所以看到「verify 失败：<命令>」时，第一动作是把该命令原样重跑并自己看输出，
  不要根据卡面描述猜失败原因。
