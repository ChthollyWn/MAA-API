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

## 验证命令的陷阱

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
