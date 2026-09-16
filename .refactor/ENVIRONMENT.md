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

### M2-02 实测：greenlet 平台标记与异步引擎（第 3 次尝试追加）

- **`platform.machine()` 在 macOS arm64 上是 `"arm64"`，不是 `"aarch64"`**（实测 Python 3.13.3）。SQLAlchemy 给
  greenlet 的 marker 只列了 `aarch64 / ppc64le / x86_64 / amd64 / AMD64 / win32 / WIN32`，因此
  `poetry.lock` 里**本来就有 greenlet 3.5.6，但它带 marker 且在本机不成立**，`poetry install` 不会装它。
  修法：把 `greenlet` 写成 `[tool.poetry.dependencies]` 的直接依赖（本次 `greenlet = "^3.1"`），
  `poetry lock` 后该条目的 marker 行被删除、`content-hash` 更新 —— **锁文件 diff 仅此两处**，其它 56 个包零变动。
- **`POETRY_CACHE_DIR=$(mktemp -d) poetry lock` 在本机可用**（Poetry 2.1.3，走 aliyun 主源，Poetry 2.x 默认只锁新依赖、
  不升级已锁包），无网络失败；重锁后 `poetry.lock` 未发生任何版本漂移。
- **装 greenlet 用 `.venv/bin/python -m pip install --no-cache-dir greenlet==3.5.6` 即可**（与锁文件同版本），
  不需要 `poetry install`（后者可能顺带同步/升级其它包）。
- **六个 PRAGMA 在本机 SQLite 3.49.1 的真实取值**（M2-02 的 `db/session.py` + tmp cwd 实测）：
  `journal_mode='wal'`、`foreign_keys=1`、`busy_timeout=15000`、`synchronous=1`、`temp_store=2`、`cache_size=-16000`。
  注意 `synchronous` 的 NORMAL 是 **1**（FULL=2、OFF=0），`temp_store` 的 MEMORY 是 **2**（FILE=0）。
- **`async_sessionmaker` 没有 `.bind` 属性**：取绑定引擎要读 `session_factory.kw["bind"]`，
  会话类在 `session_factory.class_`。想在测试里复用模块级 `session_factory` 的配置（含 `expire_on_commit=False`）
  但指向 tmp 库，直接 `session_factory(bind=<临时引擎>)` 即可 —— `__call__` 会把 local_kw 合并进 `kw`；
  `sqlalchemy.inspect(obj).expired` 是验证 `expire_on_commit` 的有效信号（提交后 True = 被过期，
  False = 仍可直接读字段做 WebSocket 广播）。

### M2-03 实测：AutoString / JSON none_as_null / 循环外键（第 4 次尝试追加）

- **SQLModel 的字符串列是 `sqlmodel.sql.sqltypes.AutoString`（`TypeDecorator` 子类），不是 `sa.String` 子类**：
  `isinstance(AutoString(length=16), sa.String)` 为 **False**，且 `AutoString.python_type` 抛
  `NotImplementedError`。断言列类型别用 `isinstance`，用编译结果最稳：
  `str(col.type.compile(dialect=sqlite.dialect()))` → `VARCHAR(16)` / `TEXT` / `JSON`（本卡测试的 `sql_type()` 即此写法）。
- **`sa.JSON` 默认 `none_as_null=False`：Python `None` 会被序列化成 JSON 字面量字符串 `'null'`，不是 SQL NULL。**
  后果是「可空 JSON 列」里躺的是非 NULL 的 `'null'`：`WHERE col IS NULL` 查不到，且
  `resource_asset` 的 `CHECK (content IS NOT NULL OR path IS NOT NULL)` 被静默绕过（M2-03 实测：
  ORM 插入 NULL content 不报错）。修法 `Column(name, JSON(none_as_null=True), ...)`。
  **`none_as_null` 必须传给 `JSON(...)` 构造器**；传给 `Column(..., none_as_null=True)` 会被当成 dialect 参数
  （按首个下划线拆成 dialect `none`），只发一条 `SAWarning: Can't validate argument 'none_as_null'; can't locate
  any SQLAlchemy dialect named 'none'` 然后静默不生效 —— 本卡第一次就是这么写错的，靠测试输出的 warning 摘要注意到。
- **命名约定不会改写显式约束名**：约定里没有 `%(constraint_name)s` 时，`UniqueConstraint(..., name="uq_x")`
  原样保留；`ck` 约定 `ck_%(table_name)s_%(constraint_name)s` 会把显式名加上表名前缀
  （`name="content_or_path"` → `ck_resource_asset_content_or_path`）。`Index(name, ...)` 显式命名时约定完全不介入。
- **SQLite 上循环外键可以直接 `create_all`**：`pipeline↔schedule`、`confirmation↔agent_audit`、
  `agent_session→confirmation→agent_audit→agent_session` 这些环在 SQLite 全部内联成
  `CONSTRAINT ... FOREIGN KEY ... REFERENCES ...`，不报 `CircularDependencyError`，无需 `use_alter`。
- **`screenshot.trigger` 与 `setting.key` 是 SQLite 保留字**，SQLAlchemy 会自动加引号（DDL 里是
  `"trigger"` / `"key"`），能正常建表与读写；写 DDL 断言时要按带引号的形态匹配。

### M2-04 实测：auto_vacuum 放置的必需补丁 / alembic.ini / 反射断言（第 5 次尝试追加）

- **M2-01 的 auto_vacuum 放置「方向对但不完整」，直接照抄会丢 `alembic_version` 行（重要）**：
  `conn.exec_driver_sql("PRAGMA auto_vacuum=INCREMENTAL")` 会在连接上 **autobegin** 一个 SQLAlchemy
  事务；`MigrationContext.configure(connection=conn)` 见到连接已有事务就把它当「外部事务」，
  `context.begin_transaction()` 退化成 no-op、迁移结束也不 commit。结果：DDL 因 pysqlite 不把
  DDL 包进事务而留在库里（表都建好了），但 `INSERT INTO alembic_version` 随连接关闭回滚 ——
  **表在、版本为空**，下次 `upgrade head` 从 0001 重放并撞 `table already exists`。
  M2-01 探针只查了 `"alembic_version" in tables`（probe 第 568 行），没查行，所以没暴露。
  修法：pragma 之后、`context.configure` 之前补一句 `conn.commit()`（M2-04 env.py 实测：
  `PRAGMA auto_vacuum` 仍是 2，`select * from alembic_version` 返回 `[('0001',)]`，`downgrade base` 正常）。
- **`alembic.ini` 需要 `path_separator = os`**：Alembic 1.20 对 `prepend_sys_path` 的旧式分隔符解析
  会发 `DeprecationWarning: No path_separator found in configuration`（每个 command 一条），显式写上即消失。
- **手写迁移里 `op.create_table(..., sqlite_autoincrement=True)` 有效**：DDL 落下 `AUTOINCREMENT`，
  与 autogenerate 产物一致；`downgrade base` 后 `sqlite_sequence` 会作为内部表留下（正常，不影响断言）。
- **SQLite 反射断言的三个形态**：`inspect(engine).get_indexes(t)` 里
  `dialect_options["sqlite_where"]` 是 **TextClause**（不是 str，要比 `str(...)`）；`get_indexes` **不返回**
  唯一约束的隐式 autoindex，具名 UNIQUE 走 `get_unique_constraints(t)` —— 所以「索引全量清单」要取两者并集；
  外键 `ondelete` 在 `get_foreign_keys(t)[i]["options"]["ondelete"]`。
- **离线模式可在进程内验**：`contextlib.redirect_stdout` 包住 `command.upgrade(cfg, "head", sql=True)`
  即可拿到 SQL 文本，且不会创建库文件（env.py 的 `run_migrations_offline()` 分支即由此覆盖）。
- **`Config("alembic.ini")` 的 `script_location` 相对 CWD 解析**：测试里不要依赖 cwd，直接把
  `script_location` 与 `sqlalchemy.url` 都 `set_main_option` 成绝对路径/临时库 URL。

### M2-05 实测：head 前移 / Alembic 离线模式的执行参数与 SELECT

- **新增一条迁移 = head 前移，硬编码 head 的断言必然失效**：0002 一出现，M2-04 的
  `tests/db/test_initial_migration.py` 里 `HEAD_REVISION = "0001"` 就报
  `assert [('0002',)] == [('0001',)]`。M2-05 已把它改成
  `ScriptDirectory.from_config(cfg).get_current_head()`；后续涉及 head 的测试都走
  ScriptDirectory，不要写死序号。
- **离线模式（`alembic upgrade head --sql`）会丢弃 `execute()` 的执行参数**：
  `MigrationContext._stdout_connection()` 造的 mock connection 只把 construct 交给
  `impl._exec`；而 `DefaultImpl._exec` 在 `as_sql` 下对带参调用直接抛
  `TypeError("SQL parameters not allowed with as_sql")`。数据迁移要把值内联进 Core 语句
  （`sa.insert(t).values(...)`、表达式里的字面量），离线脚本才会渲染出可执行的字面量 SQL。
- **离线模式下读表的 SELECT 拿不到结果**：mock connection 的 `execute()` 返回 `None`（不是
  Result），`bind.execute(select(...)).fetchall()` 会 `AttributeError`。凡是 SELECT 做幂等
  判断的迁移，都要用 `op.get_context().as_sql` 跳过（0002 的「同名记录已存在」检查即如此）。
- **数据迁移不必手动 commit**：INSERT 随 Alembic 的迁移事务一起提交（env.py 已让 Alembic 自己
  开事务）；`upgrade → downgrade -1 → upgrade` 跑完行数稳定、同名不重复（0002 实测）。

### M2-06 实测：编排器门禁环境没有 venv / VACUUM INTO 的判别性测法（第 6 次尝试追加）

- **卡面 verify 里的裸 `python3` 跑在编排器门禁环境里，PATH 没有 `.venv/bin`。**
  `.refactor/orchestrator-src/lib/index.js` 的 `runVerify` 走
  `run('bash', ['-lc', cmd])`，而 `run()` 固定 `cwd = 仓库根` + `baseEnv()`（web 进程环境；
  PATH 实测为 `/Users/chtholly/.local/bin:/opt/homebrew/bin:/usr/local/bin:...`，没有 venv、
  没有 PYTHONPATH/VIRTUAL_ENV）。该环境下 `python3 = /usr/local/bin/python3`，
  `import sqlalchemy` / `import alembic` / `import sqlmodel` 全是 ModuleNotFoundError。
  **worker 自测时 `PATH=$PWD/.venv/bin:$PATH python3 -c ...` 能过，不等于门禁能过** ——
  M2-06 实测同一条 verify 在门禁环境里死在 `from alembic import command`。
  对策二选一：verify 写 `.venv/bin/python`；或让被 import 的模块顶层只依赖标准库
  （M2-06 的 `db/migrate.py` 取后者：alembic / sqlalchemy / `maa_api.db.session` 全部延迟到
  函数内 import，顺带避免 import 期 `make_engine()` 的副作用）。
- **`VACUUM INTO` 与裸文件拷贝的差别可以做成判别性测试**（M2-06 实测，SQLite 3.49.1）：
  库处于 WAL 且有一条连接保持打开时，刚提交的行只在 `-wal` 里 —— `shutil.copyfile` 出来的
  副本读不到该行，原库与 `VACUUM INTO` 快照都能读到。注意最后一条连接关闭会触发 checkpoint，
  所以「裸拷贝读不到」的对照断言必须让那条 WAL 连接活着。
- **SQLite 的 `JSON` 声明类型是 NUMERIC 亲和**：类型名不含 INT/CHAR/TEXT/BLOB/REAL/FLOA/DOUB，
  于是用裸 `sqlite3` 往 JSON 列插字符串 `'1'` 会按数字存成整数 `1`（M2-06 写 `setting.value`
  测试时踩到）。测试若要断言原文，插非数字字符串（如 `wal-marker`）。

### M2-07 实测：async ORM 过期实例 / merge / CASCADE / RETURNING（第 7 次尝试追加）

- **`AsyncSession.expire_all()` 连主键一起作废**：之后在 async 上下文外读 ORM 实例的**任何**字段
  （包括 `obj.id`）都抛 `MissingGreenlet`（SQLAlchemy 2.0.54 实测；traceback 落在
  `attributes.py __get__ → _load_expired`，与仓储实现无关）。`session.get(Entity, pk, populate_existing=True)`
  只能让 *get 的返回值* 刷新，救不了作为调用参数的 `obj.id`（参数先求值）。
  M2-07 卡面 verify 正是 `s.expire_all(); await repo.get(high.id)`，所以 `PipelineRepository.create()`
  用 `Session.merge()` 而不是 `session.add()`：merge 把状态复制进受会话管理的副本返回，**入参保持 transient**、
  字段永远可读（代价：对入参的后续修改不会被提交，调用方要用返回值/仓储方法）。改成 `add()` 该 verify 必挂。
- **裸 `create_async_engine("sqlite+aiosqlite:///...")` 的库 `PRAGMA foreign_keys=0`**，`ON DELETE CASCADE`
  静默不生效（实测：删 pipeline 后 task 仍在）；只有 `db/session.py` 的 `make_engine` 才把它置 1。
  清理方法不能假设调用方连接开了 PRAGMA（`PipelineRepository.purge_before` 因此显式删子表）；
  写「级联删除」断言的测试要先确认自己用的是哪个引擎。
- **SQLModel `table=True` 实例禁止 `setattr` 未声明字段**：`ValueError: "Pipeline" object has no field "tasks"`
  （pydantic v2）。要把额外数据挂到 ORM 实例上（如 `get(with_tasks=True)` 的 `tasks`）只能
  `object.__setattr__(obj, "tasks", ...)`。
- **ORM 实体的 `update(...).returning(col)` 在 aiosqlite 上可用且会同步会话内对象**：
  `values(retry_count=Task.retry_count + 1).returning(Task.retry_count)` 实测返回新值，身份映射里的同一实例
  的 `retry_count` 也随之更新（默认 `synchronize_session='auto'` 走 fetch）；条件更新的 `result.rowcount`
  同样可靠，可直接作「原子领取 / 状态机是否接受」的判据。

### M2-08 实测：SQLite 无 DELETE LIMIT / synchronize_session=False 的陈旧实例（第 8 次尝试追加）

- **本机 SQLite 3.49.1 没有 `SQLITE_ENABLE_UPDATE_DELETE_LIMIT`**：`.venv/bin/python -c "import sqlite3;
  c=sqlite3.connect(':memory:'); print([r for r in c.execute('pragma compile_options') if 'DELETE' in r[0] or 'UPDATE' in r[0]])"`
  实测输出 `[]`，`DELETE ... LIMIT` 语法不可用。清理语句必须写成
  `DELETE FROM log_entry WHERE id IN (SELECT id FROM log_entry WHERE source = :s AND created_at < :t ORDER BY id LIMIT 5000)`
  （M2-08 实测在 aiosqlite 上可用；用 monkeypatch 把模块常量 `PURGE_BATCH_SIZE` 缩到 2 即可低成本逼出多批路径）。
- **`synchronize_session=False` 的 Core UPDATE 不会刷新身份映射，`session.get()` 会返回陈旧实例**：会话是
  `expire_on_commit=False` 时，UPDATE 之后同 id 实例的字段保持旧值、`session.get(Entity, pk)` 也直接返回它
  （实测 `deleted_at` 仍是 `None`），必须 `populate_existing=True` 才读到库里的真值；改成默认
  `synchronize_session`（条件是简单 `IN` 时走 evaluate）会同步会话内实例。若会话是 `expire_on_commit=True`，
  提交后在 async 上下文外读字段直接 `MissingGreenlet`（与 M2-07 那条同源）。批量清理一律用
  `synchronize_session=False` + 需要真值时 `populate_existing=True` 的组合。

### M2-09 实测：JSON 列的类型保型 / 裸 text() 不套类型处理器（第 9 次尝试追加）

- **走 SQLAlchemy 的 JSON 列时标量类型能原样读回，不要被 M2-06 那条「JSON 是 NUMERIC 亲和」误导**：
  `setting.value` 写入 int `25` 后 `get()` 返回 `int`、写入 str `"25"` 后返回 `str`（M2-09 实测）。
  机制有两条：①字符串被 `json.dumps` 序列化成**带引号**的 JSON 文本 `'"25"'`，不是合法数字，SQLite 的
  NUMERIC 亲和不会把它改成数字；②纯数字的 JSON 文本会被 SQLite 存成 INTEGER/REAL，SQLAlchemy 的
  SQLite 方言 `_SQliteJson.result_processor` 对 `numbers.Number` 原样返回，所以 int/float 不会在
  `json.loads(int)` 上炸。要断言「库里到底存了什么」就用裸 `text("select value from setting")`：
  实测 int 行返回 Python `int`、str 行返回带引号文本 `'"25"'`。
- **裸 `text()` 查询不套 SQLAlchemy 的类型处理器**：`select updated_at from setting` 拿到的是 ISO
  **字符串**（对字符串调 `.year` 抛 `AttributeError`），DATETIME/JSON 列要用 ORM 列表达式查、或在
  测试里 `datetime.fromisoformat(...)`。M2-09 写 upsert 刷新 `updated_at` 的断言时踩到。
- **SQLite 的 `INSERT ... ON CONFLICT(key) DO UPDATE` 在 aiosqlite 上可直接做 upsert**（M2-09 实测）：
  `sqlalchemy.dialects.sqlite.insert(Setting).values(...).on_conflict_do_update(index_elements=[Setting.key],
  set_={...: stmt.excluded.x})` 连续写同一 key 只有一行，JSON 绑定参数在 `excluded` 里同样按列类型序列化；
  比「先查后写」少一次竞态。`schedule` / `setting` 的仓储事务纪律与 M2-07/M2-08 一致：不 commit，
  由调用方决定边界。

### M2-10 实测：批量 UPDATE...RETURNING / FK 约束下的授权字段 / model_copy 裁剪（第 10 次尝试追加）

- **ORM 批量 `UPDATE ... RETURNING id` 在 aiosqlite 上可用，且能同时完成「置终态」与「取回被翻转的 id」**：
  `session.execute(update(Confirmation).where(status=='pending', expires_at < now).values(...).returning(Confirmation.id),
  execution_options={"synchronize_session": False})` 实测返回的正是本次真正翻转的 id（`expire_overdue` 的实现形态），
  第二次调用同一 `now` 返回 `[]`。注意 `synchronize_session=False` 不会刷新身份映射：同一会话里 `session.get()`
  仍返回陈旧实例（status 还是 pending），仓储的 `get()` 必须带 `populate_existing=True` 才读到真值 —— M2-08 那条
  在「批量 RETURNING + 仓储 get」组合下再次复现。`resolve()` 用 `UPDATE ... WHERE id=? AND status='pending'` 的
  `rowcount` 判定流转是否被接受，默认 `synchronize_session`（条件为简单等值，走 evaluate）会同步会话内实例。
- **`agent_session.atomic_grant_id` 有指向 `confirmation.id` 的外键，而 `tests/db/conftest.py` 的引擎开了
  `PRAGMA foreign_keys=ON`**：仓储测试里给 `update_grant()` 传一个杜撰的 confirmation id 会抛
  `IntegrityError: FOREIGN KEY constraint failed`（原始 SQL 是那条 UPDATE）。要让授权字段的用例通过，必须先
  `ConfirmationRepository.create()` 一条真实的 `grant_atomic_ops` 确认并 commit，再用它的 id。
- **SQLModel `table=True` 实例支持 `model_copy(update={...})`**：`AuditRepository.create()` 用它生成「裁剪后的副本」
  再 `merge()`，入参保持原始 base64 不被修改（调用方还要拿原始参数回显/执行），实测副本落库、原对象字段不变。

### M2-11 实测：SQLite 先判 upsert 候选行的 CHECK / sa_column 默认值只在实例构造时存在（第 11 次尝试追加）

- **SQLite 3.49.1 对 `INSERT ... ON CONFLICT ... DO UPDATE` 先求值「插入候选行」的 CHECK，冲突判定在其后**：
  `resource_asset` 带 `CHECK (content IS NOT NULL OR path IS NOT NULL)`，当目标行已存在、而 upsert 的插入列里既没有
  `content` 也没有 `path`（例如只更新 `remote_version` / `etag` / `last_checked_at`）时，即使 `ON CONFLICT (kind, name)`
  本会命中已有行，SQLite 仍直接抛 `IntegrityError: CHECK constraint failed: ck_resource_asset_content_or_path`，
  `DO UPDATE` 根本不执行（对照：把 `content`/`path` 之一放进插入列后同一条语句正常走 UPDATE）。
  复现：create_all 建好临时库后插入一行含 `path` 的记录，再执行
  `INSERT INTO resource_asset (id,kind,name,checksum,enabled,remote_version,etag,created_at,updated_at) VALUES (...)
  ON CONFLICT (kind, name) DO UPDATE SET remote_version = ?, ...`（省略 content/path）。
  **结论：`resource_asset` 的 upsert 必须写成「先 UPDATE、`rowcount == 0` 再 INSERT」**，让 CHECK 在真实行上判定；
  INSERT 路径的并发冲突仍由 `uq_resource_asset_kind_name` 兜底。`SettingRepository.set` 的 ON CONFLICT 写法不受影响
  （`setting` 表没有 CHECK）。
- **SQLModel `Field(default_factory=...)` 配 `sa_column` 时不会变成 Column 级默认值**：`ResourceAsset.created_at` /
  `updated_at` 是 `Field(default_factory=utcnow, sa_column=datetime_column(...))`，ORM 路径（实例构造 + merge/flush）没问题
  （pydantic 构造时已填好字段）；但 Core `sqlite_insert(ResourceAsset).values(kind=..., name=...)` 生成的 INSERT 列清单
  里**没有** `created_at` / `updated_at`，直接 `NOT NULL constraint failed: resource_asset.created_at`
  （实测 SQL：`INSERT INTO resource_asset (id, kind, name, enabled, remote_version) VALUES (...)`；不带 `sa_column` 的
  `id` / `enabled` 两个 Field 默认值倒是有）。Core 插入这类表时必须显式带全 NOT NULL 的 sa_column 时间戳。
- **`upsert_by_kind_name` 的 `kind` / `name` 是位置参数，传不进 `**fields`**：`upsert(kind, name, kind="copilot")`
  在 Python 层就是 `TypeError: got multiple values for argument 'kind'`，仓储不需要（也无法）为业务键写 ValueError 分支；
  只有 `id` / `created_at` / 未知列名这类键能进 `**fields`，在那上面校验即可。

### M2-12 实测：incremental_vacuum 在 auto_vacuum=NONE 的库上是静默 no-op（第 12 次尝试追加）

- **`PRAGMA incremental_vacuum(1000)` 在没有 `auto_vacuum=INCREMENTAL` 的库上不报错、也不回收任何页**：
  裸 `sqlite3`（SQLite 3.49.1）实测，删空 200 行后再执行该 pragma，
  auto_vacuum=NONE 的库 `(page_count, freelist_count)` 从 `(52, 50)` 原地不动；
  auto_vacuum=INCREMENTAL 的对照库从 `(53, 50)` 变成 `(52, 49)`（真的归还了页）。
  意义：`create_all` 建的临时测试库全是 auto_vacuum=0（INCREMENTAL 由迁移 `env.py` 设置，M2-04），
  所以 M2-12 的清理用例跑过、甚至断言「vacuum 不抛异常」，都**不能**证明生产库真在回收空间；
  要验证回收必须比对 `page_count` / `freelist_count` 的差值，且库得先由 Alembic 迁移建出来。
- **`incremental_vacuum` 可以在 SQLAlchemy 异步会话的隐式事务里执行**：`await db.execute(text("PRAGMA incremental_vacuum(1000)"))`
  + `await db.commit()` 在 `sqlite+aiosqlite` 上实测无报错（与完整 `VACUUM` 不同，后者在事务里会失败）；
  清理服务因此可以复用同一个 `AsyncSession`，不必为 vacuum 另开连接。

### M2-13 实测：整套数据层冒烟 0.5s / 只改三条路径不足以隔离临时库（第 13 次尝试追加）

- **`scripts/db_smoke.py` 全链路实测 0.5s**（空库迁移 + upgrade/downgrade/upgrade + 7 族仓储写读改删 +
  一轮 `run_retention`），12/12 项检查通过、末行 `SMOKE OK`、exit 0，不需要网络/内核/设备。
  门禁把它放进 acceptance 的成本可以忽略。
- **隔离临时库只改 `session.DB_PATH` / `SYNC_URL` / `ASYNC_URL` 不够（重要）**：
  `session.engine` 与 `session.session_factory` 是 **import 期**用当时的 URL 造好的，之后改模块属性
  不会重建它们 —— 实测 `session.session_factory.kw["bind"].url` 与 `session.engine.url` 仍指向
  `sqlite+aiosqlite:///resource/maa_api.db`。`retention_service.run_retention(session_factory=None)`
  取的正是这个模块级工厂，所以「只 patch 三条路径」的脚本/测试会让保留策略连上**真实库**
  （SQLite 连库即建文件）。正确做法是把 `session.engine` / `session.session_factory` 也一并替换
  （db_smoke 即如此）；`migrate.ensure_schema()` 不受影响，它每次调用都读 `session.SYNC_URL`
  （M2-05/M2-06 的可测性契约）。
- **全新迁移库上 `auto_vacuum=INCREMENTAL(2)` 在 `ensure_schema()` 之后立即成立**（env.py 的
  pragma+commit 放置，M2-04）；且 `downgrade -1` 之后再调 `ensure_schema()` 会走「检测到待应用迁移 →
  `VACUUM INTO` 备份 → `upgrade head`」，备份文件里的版本正是降级后的那一个（0001），
  重放 0002 后 schedule 行数与首次 upgrade 相同。

### M2-14 实测：pragma 值生效 ≠ 迁移可用，只有版本行断言能挡住「不 commit」（第 14 次尝试追加）

- **`PRAGMA auto_vacuum` 读回 2 不能证明该放置可用（重要，门禁设计用）**：M2-14 在 M2-01 探针里新增
  `env_py_before_begin_transaction_no_commit` 对照场景（pragma 后不 `conn.commit()`）实测：
  pragma 值仍是 **2**（`exec_driver_sql("PRAGMA ...")` 在 pysqlite 层不触发 BEGIN，空库上立即写入库头），
  但 `alembic_version` 表 **0 行**，第二次 `upgrade head` 报
  `OperationalError: (sqlite3.OperationalError) table probe_min already exists`；同一场景补一句
  `conn.commit()` 后实测 2 / `['0001']` / 第二次 upgrade 无操作。**所以断言「表存在」或「pragma == 2」
  都会放行这个缺陷，必须查 `alembic_version` 行。**
- 探针现在有两条对应 `required_checks`：`auto_vacuum_version_row_ok`（推荐落点升级后
  `alembic_version` 行 == 预期 revision）与 `auto_vacuum_no_commit_control_reproduced`（不 commit 的对照
  必须被识别为「版本行缺失 + 第二次 upgrade 失败」）。负向验证：把 `PLACEMENT_ENV_PRAGMA` 临时换回
  不带 commit 的写法再跑 `probe_auto_vacuum()`，两条门禁都变红（`alembic_version_rows == []`），
  即旧文档写法今天会被探针直接挡住。

## API 骨架（M3-01 实测）

实测环境：pydantic 2.11.10 / fastapi 0.141.1 / starlette 1.6.0 / httpx 0.27.2 / Python 3.13.3。
完整产物：`scripts/probe_api_skeleton.py`、`tests/fixtures/api_probe_result.json`、
`tests/fixtures/api_probe_findings.md`（29 条 `required_checks`；脚本不 import `maa_api`、不碰仓库库文件）。

- **`fastapi.testclient` 在本机 import 期就发两条弃用警告**（探针捕获原文）：
  `StarletteDeprecationWarning: Using \`httpx\` with \`starlette.testclient\` is deprecated; install \`httpx2\` instead.`
  与 `DeprecationWarning: The anyio.abc.BlockingPortal alias is deprecated, use anyio.from_thread.BlockingPortal instead.`。
  以后若要给 `pytest.ini` 加 `filterwarnings = error`，这两条必须先处理，否则任何用 `TestClient` 的测试都会红。
- **`TestClient` 默认 `follow_redirects=True`**（httpx 本身默认 False，是 starlette 的 TestClient 改了默认值）：
  断言「尾斜杠 307」必须显式 `follow_redirects=False`，否则拿到的是跟随后的 200，测不出重定向。
- **`TestClient` 默认 `raise_server_exceptions=True`**：500 路径的测试必须显式传 `raise_server_exceptions=False`
  才能拿到响应体；配 `@app.exception_handler(Exception)` 时默认值会把原异常直接抛进测试。
- **FastAPI 对「非法 JSON 请求体」与「未知 discriminator」的出厂状态码都是 422**（`json_invalid` /
  `union_tag_invalid`），而 docs/05 §2.2／§4.2 要求 400。两者都必须在 `RequestValidationError`
  处理器里按 `errors()[i]["type"]` 特判；`json_invalid` 还要看 `loc` 形状（请求体解码失败是
  `("body", <int>)`，字段级 JSON 解析失败是 `("body", "<字段名>")`）。
- **`ValidationError.errors()` 里的 `ctx.error` 是异常实例**（`value_error` 情形），
  `json.dumps(exc.errors())` 直接 `TypeError`；处理器里必须走 `fastapi.encoders.jsonable_encoder`
  或只挑 `type/loc/msg`。
- **`generate_unique_id_function` 收到的 route 对象不一定是 `APIRoute`**：`@app.get` 直挂的是 `APIRoute`，
  经 `include_router` 进来的是 `_EffectiveRouteContext` 包装对象（fastapi 0.141 实测），两者都有
  `.name` / `.tags`。所以 `isinstance(route, APIRoute)` 会 False，注解也别写死 `APIRoute`。
- **`lifespan` + 同步 `TestClient` 可用**（`with TestClient(app) as c:` 里 enter/exit 都触发），
  本仓不需要 `pytest-asyncio`；但不用 context manager 时 lifespan 不执行。
- **`model_validator` 抛非 `ValueError` 的自定义异常时 pydantic 不做包装**（2.11 实测）：
  `TypeAdapter.validate_python` 与 FastAPI 请求体校验都原样抛出，可被
  `@app.exception_handler(AppError)` 直接接住；`ValueError` 才会变成 `type="value_error"` 的
  `ValidationError`（`ctx.error` 保留原实例）。

### M3-02 实测：StrEnum 的 repr / docs/05 §4 的机器可解析性与一致性门禁

- **`StrEnum` 成员的 `repr()` 是 `<ErrorCode.NOT_FOUND: 'NOT_FOUND'>`，不是 `'NOT_FOUND'`**（Python 3.13.3 实测）：
  `str()` 与 `json.dumps()` 才是裸值（`"NOT_FOUND"`）。所以 `repr(AppError)` 里嵌的是尖括号形态
  （`AppError(code=<ErrorCode.NOT_FOUND: 'NOT_FOUND'>, message=..., details=...)`），
  M3-04 写错误体或任何 repr 快照断言时别按 `code='NOT_FOUND'` 写。
- **docs/05 §4 可以机械解析**：从 `## 4. 错误码表` 到下一个 `## `，`### 4.x` 小节里形如
  ``| `CODE` | 404 | 含义…… |`` 的行共 **92** 行 / 14 张表，HTTP 列为 `—` 的只有 `UPDATE_INTERRUPTED`。
  表头（`错误码`）与分隔行都不匹配该形态，用「首列去反引号后 fullmatch `[A-Z][A-Z0-9_]*`」即可过滤。
- **`tests/domain/test_errors.py` 现在带文档一致性门禁**（`test_*_match_docs_exactly` 等，路径由
  `Path(__file__).resolve().parents[2]` 推导，不依赖 CWD）：只改 `docs/05 §4` 而不改
  `maa_api/domain/errors.py`（或反之）会直接红；后续里程碑新增错误码必须先补文档表。另有
  `test_every_code_has_a_chinese_meaning_comment` 用 AST 钉住「每条码上方一行中文注释」。

### M3-03 实测：config.yaml 的 null 形态 / 默认路径锚点 / ruamel 空文件语义

- **本机 `config.yaml`（与 `config.template.yaml` 同形）的 `app.access_token`、`app.maa_core_path`、
  `app.proxy` 三项都是 `null`**（ruamel safe loader 读回 `None`），`adb` 三项有值。所以
  `Settings.model_validate(yaml_dict)` 这种直连写法会因为 `None` 撞 `str` 字段而
  `ValidationError`（`Input should be a valid string`），服务启动即挂。M3-03 的约定是
  **值为 `None` 的键＝这层没配，跳过该键**（取下层/默认值）。后续卡（M3-06 鉴权、M3-07 health、
  M3-09 lifespan）读 token 一律走 `maa_api.settings.get_settings()`，不要自己解析 yaml。
- **`load_settings()` 的默认路径锚在仓库根**（`maa_api/settings.py` → `parents[1] / "config.yaml"`），
  与 CWD 无关；旧 `config/config.py` 的 `Path() / "config.yaml"` 是 CWD 相对。测试要隔离真实配置
  就 monkeypatch `maa_api.settings.DEFAULT_CONFIG_PATH`（M3-03 的 `tests/test_settings.py` 即此法，
  从不读仓库根真实 config.yaml）。
- **ruamel.yaml 实测**（0.18.17）：`YAML(typ="safe").load()` 对空文件/全注释文件返回 `None`（不是 `{}`）；
  非法 yaml 抛 `YAMLError` 子类（`ScannerError` / `ParserError`）；`Path.open()` 对不存在的文件抛
  `FileNotFoundError`。settings 把 `None` 当空配置、把 `YAMLError` 包成带文件路径的 `SettingsError`。
- **`import maa_api.settings` 无 import 期副作用可通过子进程验证**：`cd $(mktemp -d)` +
  `PYTHONPATH=$ROOT` 下 import 后 `os.listdir(os.getcwd()) == []`（`__pycache__` 落在源码目录、
  不在 CWD）；子进程另断言未 import `maa_api.db` / `sqlalchemy` / `sqlmodel`。

### M3-04 实测：处理器注册时机 / 405 默认路径 / exclude_none 不递归 dict（第 15 次尝试追加）

- **`app.add_exception_handler` 在 fastapi 0.141.1 与 starlette 1.6.0 里都只是字典赋值**
  （`self.exception_handlers[key] = handler`，读了两处源码）：用同一批模块级函数重复注册天然幂等
  （后一次覆盖的是同一个函数对象），`register_exception_handlers()` 可被多个装配入口重复调用。
  但**注册必须发生在第一个请求之前**：middleware stack 在首个请求时才构建，实测「先发一次请求、
  再注册 AppError 处理器」第二次请求仍是 `500 Internal Server Error`（裸文本，不走统一体）。
- **405 的框架默认响应对映射表外的状态码可以直接复用** `fastapi.exception_handlers.http_exception_handler
  (request, exc)`：Starlette 路由层对方法不匹配抛 `HTTPException(405, headers={"Allow": ...})`，
  默认 body `{"detail": "Method Not Allowed"}` 且带 `Allow` 头（实测 `allow=GET`）。docs/05 §2.5 的
  405 例外不必手写响应，把 `StarletteHTTPException` 处理器里"表外状态码"分支委托给这个官方函数即可
  （它同时处理 204/304 的 no-body 语义）。另：`raise HTTPException(404)` 的默认 detail 是英文
  `HTTPStatus.phrase`（"Not Found"），可直接用 `HTTPStatus(status).phrase` 判别"是否被抛错方自定义过"。
- **pydantic `model_dump(mode="json", exclude_none=True)` 不递归删除普通 dict 里的 None**（2.11.10 实测）：
  `ErrorDetail(details=None)` 的 `details` 键被省略，而 `details={"a": None}` 原样保留 `{"a": None}`。
  统一错误体因此可以直接用它实现「details 可选、None 时省略」，不用手工 pop；后续卡若用
  `exclude_none=True` 做默认值剔除，记住它只删模型字段，不删 dict 值里的 null。
- **`TestClient.__enter__()` / `__exit__(None, None, None)` 可以手工配对**：夹具里用工厂函数
  `make_client(app)` 先 `__enter__` 再统一 `__exit__`，lifespan 照常进入/退出（M3-01 对照结论：
  不套 context manager 时 lifespan 完全不执行），`tests/api/conftest.py` 即此写法。

### M3-05 实测：pydantic bool 宽松强转 / validate_assignment 会跑 model_validator（第 16 次尝试追加）

- **pydantic 2.11.10 的 bool 字段在 lax 模式下接受一大批字符串**：`"yes"` / `"no"` / `"true"` /
  `"false"` / `"on"` / `"off"` / `1` / `0` 全部通过，只有 `"maybe"`、`2` 这类才是
  `bool_parsing`。写「类型错误 → ValidationError」的负向用例时不要拿 `"yes"` 当非法值
  （M3-05 首轮 4 条用例因此 DID NOT RAISE）。
- **`validate_assignment=True` 时 `@model_validator(mode="after")` 会在赋值时重跑**：
  `recruit.expedite_times = 3` 实测抛出校验器里的 `AppError`（不是 ValidationError），
  字段级约束则抛 `ValidationError`。M5「运行中改参数」可以依赖这条，不必绕过模型直接改
  `__dict__`。
- **`dict[str, int]` 在 lax 模式下拒绝整数键**：`{"recruitment_time": {3: 540}}` 实测
  `type="string_type"`（不会把 `3` 强转成 `"3"`），所以 docs/05 §7.5 的「JSON 键必须是
  字符串」不需要额外校验器。
- **`AppError` 穿过判别联合与嵌套 list 的路径在 2.11 上同时成立**：`TypeAdapter(TaskInput)`
  对未知 `name` 给 `union_tag_invalid`、对跨字段违规原样抛 `AppError`；
  `PipelineCreate(tasks=[{...}])` 的嵌套校验同样原样抛出 `AppError`（未被包成
  `ValidationError`），M3-04 的 `AppError` 处理器可以直接接住流水线提交路径。

### M3-06 实测：hmac 的 str 限制 / httpx 拒发非 ASCII 头 / FastAPI 依赖能收到路由异常（第 17 次尝试追加）

- **`hmac.compare_digest` 对含非 ASCII 字符的 `str` 直接抛 `TypeError: comparing strings with non-ASCII characters is not supported`**（Python 3.13.3 实测）。token / cookie / 签名比较一律先 `encode("utf-8")` 再比（字节形态任意内容都安全）；把客户端可控字符串原样喂进去就是一个可触发的 500。M3-06 的 `token_matches` 即按字节比较。
- **httpx（`TestClient` 底层）拒发非 ASCII 的请求头值**：`client.get(url, headers={"X-Token": "秘密令牌"})` 实测在 `httpx/_utils.py` 抛 `UnicodeEncodeError: 'ascii' codec can't encode characters...`（HTTP/1.1 头按 ascii 编码）。非 ASCII token 的端到端用例只能走 query 参数（httpx 会 percent-encode）。另：per-request 的 `cookies=` 会发 `DeprecationWarning: Setting per-request cookies=<...> is being deprecated`（httpx 0.27.2），将来给 pytest 加 `filterwarnings = error` 时这条要与 M3-01 记的两条一起处理。
- **FastAPI 0.141.1 的 `yield` 依赖能收到路由抛出的异常**：在 `get_session` 的 `except Exception: await session.rollback()` 上挂 spy 实测，路由里 `raise AppError(...)` 会经 `AsyncExitStack` 的 `athrow` 进入依赖生成器（`rollbacks` 非空），所以「依赖里回滚、事务边界归调用方」的纪律可用；`AsyncSession.bind.url` 与 `get_bind().url` 都能读到绑定 URL，断言会话连的是哪个库用前者最直接。
- **`AppError` 处理器不透传响应头（M3-04 缺口，已记 DEFECTS.md）**：429/503 的 `Retry-After` 用 AppError 表达不出来；M3-06 的鉴权限流 429 绕行 `StarletteHTTPException(429, headers={"Retry-After": ...})`（同一处理器保留 headers、body 仍是 `RATE_LIMITED` 统一体）。后续给 `QUEUE_FULL` / `CORE_NOT_READY` 之类补 Retry-After 时不要重复踩。

### M3-07 实测：fastapi 0.141 的 `include_router` 是惰性的（verify 命令陷阱，不是实现缺陷）

- **`app.include_router(router)` 之后 `app.routes` 里没有拍平后的 `APIRoute`**：只有 4 条内置 `Route`
  （openapi / docs / docs-oauth2-redirect / redoc）加**一个** `fastapi.routing._IncludedRouter` 包装对象；
  该私有类**没有 `.path` 属性**（展开发生在请求匹配与 OpenAPI 生成时，内部走 `effective_candidates()`）。
  因此 `{r.path for r in app.routes}` 形态的断言在 0.141.1 上对**任何**实现都必然抛
  `AttributeError: '_IncludedRouter' object has no attribute 'path'` —— 与是否给 router 加 prefix、
  是否 import 本仓无关。实测 exit 1。
- 最小复现（不 import 本仓）：
  `.venv/bin/python -c "from fastapi import FastAPI, APIRouter; r=APIRouter(prefix='/api/system'); r.get('/health')(lambda: {}); a=FastAPI(); a.include_router(r); print([type(x).__name__ for x in a.routes]); a.routes[-1].path"`
  → 打印 `['Route', 'Route', 'Route', 'Route', '_IncludedRouter']`，随后 AttributeError。
- 取有效路径的两条可靠通道（实测都给出 `/api/system/health` 与 `/api/system/auth/cookie`）：
  `set(app.openapi()["paths"])`，或 `{ctx.path for ctx in fastapi.routing.iter_route_contexts(app.routes)}`
  （后者还含 `/docs`、`/openapi.json` 等内置路径）。要断言「路由自身挂没挂依赖」，用模块级
  `router.routes` 里的 `APIRoute`：它的 `.path` 仍是带前缀的完整路径（`/api/system/health`），
  `.dependencies` 就是装饰器上那份。
- 这是 M3-01 记的「`generate_unique_id_function` 收到 `_EffectiveRouteContext`」的另一面：0.141 起
  include 全面惰性化，别再按旧版（`self.routes.extend(...)` 拍平）的语义写 verify。
- **受影响卡片**：M3-07 verify #1 与 M3-08 verify #1 都是 `{r.path for r in a.routes}` 形态，需要替换。
  M3-07 的等价命令（实测 exit 0）：
  `.venv/bin/python -c "import maa_api.api.routers.system as s; from fastapi import FastAPI; a=FastAPI(); a.include_router(s.router); paths=set(a.openapi()['paths']); assert '/api/system/health' in paths and '/api/system/auth/cookie' in paths, sorted(paths); print(sorted(p for p in paths if p.startswith('/api')))"`
  另：M3-07 的 `GET /api/system/health` 是豁免路径，**路由侧刻意不挂 `require_auth`**（挂了会把健康检查
  挡在 401 外）；`POST`/`DELETE /api/system/auth/cookie` 才挂。cookie 实测属性：
  `maa_token=<token>; HttpOnly; Path=/; SameSite=lax`（`secure=False` 归 M15），清除为 `Max-Age=0` 且
  `expires` 被 Python `http.cookies._getdate(0)` 渲染成**当前时刻**（不是 1970），断言清 cookie 只认
  `Max-Age=0`。
