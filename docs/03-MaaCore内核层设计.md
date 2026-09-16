> 本文档定义 MaaCore 内核的 FFI 封装、子进程 worker 与生命周期管理。决策依据见 [README 决策速查表](./README.md#决策速查表)，架构位置见 [02-系统架构设计](./02-系统架构设计.md)。

# MaaCore 内核层设计

## 1. C API 全景与现状差距

以下清单基于本地 MaaCore v6.17.x 的 `AsstCaller.h`、`AsstCallerExtra.h` 与两个平台库文件的实际导出符号核实得出，不是从文档推测的。

### 1.1 跨平台通用的 26 个 C API

| C API | 现状 | 签名要点 |
|---|---|---|
| `AsstSetUserDir` | 已封装（在 `load()` 内） | `(const char* path) -> bool` |
| `AsstLoadResource` | 已封装（在 `load()` 内） | `(const char* path) -> bool` |
| `AsstSetStaticOption` | 已封装 | `(key, const char* value) -> bool` |
| `AsstCreate` | 已封装（`__init__`） | `() -> handle` |
| `AsstCreateEx` | 已封装（`__init__`） | `(callback, void* arg) -> handle` |
| `AsstDestroy` | 已封装（`__del__`） | `(handle) -> void` |
| `AsstSetInstanceOption` | 已封装 | `(handle, key, const char* value) -> bool` |
| `AsstConnect` | 已封装，**官方已废弃** | `(handle, adb_path, address, config) -> bool` |
| `AsstAsyncConnect` | **仅声明 argtypes，无 Python 方法** | `(handle, adb_path, address, config, bool block) -> AsstAsyncCallId` |
| `AsstSetConnectionExtras` | 已封装 | `(const char* name, const char* extras) -> void` |
| `AsstAppendTask` | 已封装 | `(handle, type, params) -> AsstTaskId` |
| `AsstSetTaskParams` | 已封装 | `(handle, task_id, params) -> bool` |
| `AsstStart` / `AsstStop` / `AsstRunning` | 已封装 | `(handle) -> bool` |
| `AsstConnected` | **缺失** | `(handle) -> bool` |
| `AsstBackToHome` | **缺失** | `(handle) -> bool` |
| `AsstAsyncClick` | **缺失** | `(handle, int32 x, int32 y, bool block) -> AsstAsyncCallId` |
| `AsstAsyncScreencap` | **缺失** | `(handle, bool block) -> AsstAsyncCallId` |
| `AsstGetImage` | 已封装 | `(handle, void* buff, size) -> size` |
| `AsstGetImageBgr` | **缺失** | `(handle, void* buff, size) -> size` |
| `AsstGetUUID` | **缺失** | `(handle, char* buff, size) -> size` |
| `AsstGetTasksList` | **缺失** | `(handle, AsstTaskId* buff, size) -> size` |
| `AsstGetNullSize` | **缺失** | `() -> size` |
| `AsstGetVersion` | 已封装 | `() -> const char*` |
| `AsstLog` | 已封装 | `(level, message) -> void` |

`AsstAttachWindow` 与 `AsstAsyncAttachWindow` 仅在 `_WIN32` 下编译，本项目部署在 macOS 与 Linux，不纳入封装。

### 1.2 平台差异：两个 macOS 独有的 API

核实两个库文件的导出符号后发现，API 表面在平台间**并不一致**：

| API | macOS `libMaaCore.dylib` | Linux `libMaaCore.so` | 头文件声明 |
|---|---|---|---|
| `AsstGetMapLevelKey` | 导出 | **未导出** | `AsstCallerExtra.h`，标注实验性 |
| `AsstGetItemName` | 导出 | **未导出** | **无声明** |

原因是 `AsstCallerExtra.h` 的 `ASST_WITH_EXTRA_CALLERS` 条件编译，头文件自己写明这批接口"主要为 MaaMacGui 开发，不属于受支持的公共 API，可能随时变更或移除"。

这个差异有实际后果：本项目当前在 macOS 上运行（见 `scripts/start-maa-api.sh`），但 `Dockerfile` 基于 `python:3.11.9-slim` 即 Linux。任何依赖这两个 API 的功能在容器部署时会直接失效。

处置方式：

- `AsstGetMapLevelKey` 封装为**能力探测 + 优雅降级**。它对 agent 把"切城区那张图"这类模糊描述解析成 MAA 关卡编号很有价值，但不能成为硬依赖。不可用时回退到基于 `resource/lib/maa/*/resource/tasks/Stages/` 目录下关卡定义文件的本地索引查询
- `AsstGetItemName` **不纳入首版**。头文件没有它的声明，参数与返回语义只能靠逆向推测，编造签名会直接段错误。掉落物品名称改从 MAA 资源目录的 `item_index.json` 读取（`task.py` 中 `FightTask` 的 `drops` 参数文档已经指向该文件）

能力探测的实现方式：

```python
class Asst:
    @staticmethod
    def _has_symbol(name: str) -> bool:
        """判断当前平台的库是否导出该符号，用于实验性 API 的降级"""
        return hasattr(Asst.__lib, name)

    def get_map_level_key(self, key: str) -> dict | None:
        """关卡名 / code / stageId / levelId 互查。
        仅 macOS 构建导出此符号，其他平台返回 None，调用方需回退到本地索引。
        """
        if not Asst._has_symbol("AsstGetMapLevelKey"):
            return None
        ...
```

`ctypes` 在符号不存在时会在**首次属性访问**抛 `AttributeError`，而不是加载时失败，所以 `hasattr` 探测是可行的。但 `__set_lib_properties()` 里给所有函数统一设置 `restype` / `argtypes` 的现有写法会在 Linux 上因触及缺失符号而报错，必须把实验性 API 的属性设置包在条件分支里。

## 2. asst.py 的封装规范

### 2.1 新增方法签名

```python
def connect_async(self, adb_path: str, address: str,
                  config: str = "General", block: bool = True) -> int:
    """异步连接设备，返回 async_call_id。
    结果通过 Message.AsyncCallInfo 回调送达，不看返回值判成败。
    block=True 时内核内部串行化该调用，但 Python 侧立即返回。
    """

def connected(self) -> bool:
    """设备当前是否处于连接状态。比 running() 更适合做设备健康判定。"""

def back_to_home(self) -> bool:
    """回到游戏主界面。agent 的卡死救援动作，比自己找返回按钮可靠。"""

def click(self, x: int, y: int, block: bool = True) -> int:
    """点击指定坐标，返回 async_call_id。坐标系为设备原始分辨率。"""

def screencap(self, block: bool = True) -> int:
    """触发一次截图，返回 async_call_id。
    截图结果不由本方法返回，需随后调用 get_image() 取回。
    """

def get_image_bgr(self, size: int) -> bytes | None:
    """取最近一次截图的 BGR 原始字节。size 需为 width*height*3。"""

def get_uuid(self) -> str | None:
    """设备唯一码。多设备扩展时用于识别设备身份。"""

def get_tasks_list(self) -> list[int]:
    """内核任务队列中的 task id 列表，用于校对我们的映射是否与内核一致。"""

@staticmethod
def get_null_size() -> int:
    """内核约定的"无效尺寸"哨兵值。get_image 等接口以此判定失败。"""
```

### 2.2 两个必须修正的现有缺陷

**`set_static_option` 漏了 `staticmethod` 装饰器。** 现在的定义是 `def set_static_option(option_type, option_value)`，没有 `self` 也没有 `@staticmethod`，实例调用时 `option_type` 会被 `self` 占位。`set_connection_extras` 有同样的问题。两者都应显式标为 `@staticmethod`。

**`get_image` 的缓冲区写法无效。** 现有代码：

```python
buffer = buffer_type()
buffer.value = b'\000' * size   # ctypes 数组没有 .value，此处应报错
```

`ctypes.c_byte * size` 实例没有 `value` 属性。这行要么抛异常要么被上层吞掉，说明该路径从未被真正执行过（项目实际截图走的是 `adb_service.py` 的 adbutils）。重写为直接传数组并按返回长度截断：

```python
def get_image(self, size: int | None = None, bgr: bool = False) -> bytes | None:
    size = size or self._expected_image_size()
    buffer = (ctypes.c_char * size)()
    fn = Asst.__lib.AsstGetImageBgr if bgr else Asst.__lib.AsstGetImage
    got = fn(self.__ptr, buffer, size)
    if got == 0 or got == Asst.get_null_size():
        return None
    return buffer.raw[:got]
```

`_expected_image_size()` 从 `ResolutionGot` 回调缓存的分辨率推算，避免调用方硬编码 `1280*720*3`。

### 2.3 枚举补全

`InstanceOptionType` 当前只有 4 项（`touch_type=2`、`deployment_with_pause=3`、`adblite_enabled=4`、`kill_on_adb_exit=5`），缺 `ClientType`。`AsstCaller.h` 在 `AsstSetInstanceOption` 上方注明：

> `InstanceOptionKey::ClientType` 仅在所选连接配置的 connect 阶段命令依赖 `[PackageName]` 时需要预先设置；当前内置配置中仅 Androws / WSA 的 displayId 查询依赖该值。

核实内核的 `config.json` 后确认：`[PackageName]` 共出现 5 处，其中只有 `Androws.displayId` 与 `WSA.displayId` 属于 connect 阶段命令，`General.start` / `stop` 是任务执行期命令，那时内核能从任务参数的 `client_type` 解析包名。本项目用的 `General`（macOS 上是 `CompatMac → Compatible → General` 整条链）都不依赖它。

也就是说对本项目的常规 ADB 连接场景，这个选项**不需要设置**。渠道（官服 / B 服）的实际生效途径是各任务的 `client_type` 参数，而非实例级选项。具体见 [08-ADB连接与账号渠道](./08-ADB连接与账号渠道.md)。枚举仍应补全以备将来（实测值为 `ClientType = 6`，注意不要按现有枚举的数值分布猜成 1，那个位置是已废弃的 `MinitouchEnabled`），但不在连接流程里默认设置它。

`Message` 枚举已完整（含 `AllTasksCompleted`、`AsyncCallInfo`、`Destroyed`、`TaskChainStopped`），但 `callback_handler.py` 只处理了其中 5 类。新增处理见 [06-实时日志与WebSocket](./06-实时日志与WebSocket.md)。

## 3. 子进程 worker

`core/worker.py` 是子进程入口，由 `multiprocessing.Process` 以 `spawn` 方式启动。

选 `spawn` 而非 `fork`：macOS 上 Python 3.8+ 已默认 `spawn`，而 `fork` 在父进程持有线程（uvicorn 的 event loop、APScheduler 线程池）时复制状态极易死锁。代价是传给子进程的参数必须可 pickle，因此 worker 只接收纯数据（路径字符串、配置 dict、两个 Queue），不接收任何服务对象。

### 3.1 启动序列

```python
def core_worker_main(cmd_queue, event_queue, boot_config: dict):
    # 1. 建立子进程自己的日志出口：所有 logger 输出转成 LOG 事件
    _install_log_bridge(event_queue)

    try:
        # 2. 加载 native 库与基础资源
        Asst.load(path=boot_config["maa_path"],
                  user_dir=boot_config.get("user_dir"))

        # 3. 按序叠加增量资源层，顺序即优先级（后加载覆盖同名定义）
        for layer in boot_config.get("incremental_paths", ()):
            if not (layer_root := Path(layer) / "resource").is_dir():
                continue          # 该层尚未下载过，跳过而非报错
            if not Asst.load(path=layer, incremental=True):
                raise ResourceLoadError(f"增量资源层加载失败: {layer}")

        # 4. 创建实例并绑定回调
        asst = Asst(callback=_callback_bridge, arg=event_queue)

        # 5. 应用实例级选项
        for key, value in boot_config.get("instance_options", {}).items():
            asst.set_instance_option(InstanceOptionType(key), value)

        # 6. 上报就绪，此时才允许接受命令
        event_queue.put({"type": "READY",
                         "payload": {"version": asst.get_version(), "pid": os.getpid()}})
    except Exception as exc:
        event_queue.put({"type": "FATAL",
                         "payload": {"error": str(exc), "traceback": traceback.format_exc()}})
        return

    # 7. 进入命令循环
    _command_loop(asst, cmd_queue, event_queue)
```

注意 worker **不做版本检查与下载**。现有 `asst_manager.load_asst()` 把"校验 MAA 版本 → 下载更新 → 加载资源 → 连接设备"揉在一起，导致启动被网络 IO 阻塞。重构后版本检查与下载归 `UpdateService`（主进程，见 [07-热更新方案](./07-热更新方案.md)），worker 只管加载本地已有的文件。设备连接也不在启动序列里，由主进程在 `READY` 之后显式下发 `CONNECT` 命令，这样连接失败不影响内核就绪状态。

### 3.2 回调桥接

MaaCore 回调发生在内核自己的线程上，要求回调函数尽快返回。桥接函数只做最小工作：

```python
@Asst.CallBackType
def _callback_bridge(msg, details, arg):
    """运行在 MaaCore 的回调线程上，必须尽快返回，不做任何解释与 IO"""
    try:
        queue = ctypes.cast(arg, ctypes.py_object).value
        queue.put({
            "type": "CALLBACK",
            "ts": time.time(),
            "payload": {"msg": int(msg), "details": json.loads(details.decode("utf-8"))},
        })
    except Exception:
        pass  # 回调线程内绝不抛异常，否则可能直接崩内核
```

与现有 `callback_handler.handle_message` 的关键差异：不在回调线程里做业务判断。现在的实现会在回调线程内更新流水线状态、拼接日志、甚至 `raise ResponseException`（见 `handle_task_status` 里任务链不匹配时抛异常）——异常穿过 C 调用边界是未定义行为。新设计把所有解释逻辑搬到主进程。

`arg` 传递沿用现有的 `ctypes.c_void_p(id(arg))` 手法，但要注意这依赖 `arg` 对象在整个生命周期内不被 GC 回收。worker 里 `event_queue` 是模块级持有的，满足条件；现有代码把 `CallbackHandler` 实例传进去同样成立，但这个隐式契约值得在代码里写明。

### 3.3 命令循环

```python
def _command_loop(asst, cmd_queue, event_queue):
    while True:
        cmd = cmd_queue.get()               # 阻塞等待
        if cmd["type"] == "SHUTDOWN":
            _graceful_stop(asst, cmd["payload"].get("graceful", True))
            return
        try:
            data = _dispatch(asst, cmd["type"], cmd.get("payload", {}))
            event_queue.put({"type": "CMD_RESULT", "payload":
                             {"cmd_id": cmd["cmd_id"], "ok": True, "data": data}})
        except Exception as exc:
            event_queue.put({"type": "CMD_RESULT", "payload":
                             {"cmd_id": cmd["cmd_id"], "ok": False,
                              "error": {"code": _map_error(exc), "message": str(exc)}}})
```

命令循环是单线程串行的，天然保证了对 `Asst` 实例的调用不并发。这也意味着一个慢命令（如 `LOAD_RESOURCE` 解析全部资源）会阻塞后续命令，所以主进程侧对不同命令设置了差异化超时（见 [02-系统架构设计 §3.4](./02-系统架构设计.md)）。

`GET_IMAGE` 的落盘处理在 `_dispatch` 内完成，返回文件路径而非字节，理由见 [02-系统架构设计 §3.3](./02-系统架构设计.md)。

## 4. CoreSupervisor

`core/supervisor.py` 管理子进程生命周期，状态机定义见 [02-系统架构设计 §4](./02-系统架构设计.md)。这里补充实现要点。

### 4.1 心跳与崩溃判定

两条独立的判据，任一触发即认定崩溃：

**心跳**：每 5 秒发 `PING(seq)`，期望在 5 秒内收到同 `seq` 的 `PONG`。连续 3 次失败（约 15 秒）判定失联。心跳走与业务命令相同的 `cmd_queue`，因此它同时也在检测命令循环是否被长任务卡死 —— 这是有意的：一个卡死 15 秒以上的内核对使用者而言与崩溃无异。但 `LOAD_RESOURCE` 这类已知的长命令执行期间需要临时放宽心跳阈值，否则会误判。

**进程存活**：`Process.is_alive()` 轮询，捕获段错误、OOM kill 这类不给任何事件的硬崩溃。同时 `exitcode` 能区分正常退出（0）、信号杀死（负值，如 -11 是 SIGSEGV）与异常退出，写进崩溃记录便于排查。

### 4.2 崩溃恢复流程

```
检测到崩溃
  → 记录 exitcode 与最近 N 条 CALLBACK 事件（崩溃现场）落库
  → 当前 RUNNING 的 pipeline 与 task 置 FAILED，error_code=CORE_CRASHED
  → 队列中 PENDING 条目保持不动，不丢弃
  → WS 广播 core_status{state: "crashed"}
  → NotifyService 推送告警
  → 按退避策略重启（5s → 15s → 60s，连续 5 次失败后转 FAILED 停止自动重启）
  → 重启成功收到 READY
      → 重新下发 CONNECT
      → WS 广播 core_status{state: "ready"}
      → PipelineRunner 恢复消费队列
```

退避与次数上限是必要的：如果崩溃原因是资源文件损坏（例如一次失败的活动资源更新写坏了 `tasks.json`），无退避的自动重启会变成每秒一次的崩溃风暴，日志表被瞬间打满。转入 `FAILED` 后前端显示明确的"内核不可用，请检查资源或重装内核"，并提供重装入口。

### 4.3 与热更新的协作

`UpdateService` 在替换内核文件前后需要精确控制子进程，`CoreSupervisor` 为此暴露：

```python
async def stop(self, graceful: bool = True, timeout: float = 10.0) -> None
async def start(self, wait_ready: bool = True, timeout: float = 120.0) -> None
async def restart(self, boot_config: dict | None = None) -> None
def acquire_maintenance(self) -> AsyncContextManager  # 维护窗口
```

`acquire_maintenance()` 是一个互斥的维护窗口：进入后 `PipelineRunner` 停止取新流水线，崩溃自动重启被临时关闭（因为此时的"停止"是预期行为而非崩溃），退出时恢复。热更新、重装内核、修改 MAA 路径这三个场景都在维护窗口内执行。没有这个机制的话，更新时主动停掉子进程会被崩溃检测误判为故障并触发重启，与更新流程打架。

## 5. CoreClient

`core/client.py` 是主进程侧的异步代理，把 IPC 细节对上层完全隐藏。上层代码看到的是普通的 async 方法：

```python
class CoreClient:
    async def append_task(self, type_name: str, params: dict) -> int: ...
    async def start(self) -> bool: ...
    async def stop(self) -> bool: ...
    async def click(self, x: int, y: int) -> None: ...
    async def screencap(self) -> Path: ...
    async def back_to_home(self) -> bool: ...
    async def connected(self) -> bool: ...
    async def resolve_stage(self, key: str) -> dict | None: ...
```

实现要点：

**事件消费线程。** `multiprocessing.Queue.get()` 是阻塞的同步调用，不能直接在 event loop 里用。开一个 `threading.Thread` 死循环 `get()`，拿到事件后用 `loop.call_soon_threadsafe` 投递回事件循环处理。不用 `run_in_executor` 是因为它基于线程池，一个长期占用的阻塞任务会污染默认 executor。

**cmd_id 到 Future 的映射。** 发命令时创建 `asyncio.Future` 存入 `dict[cmd_id, Future]`，收到 `CMD_RESULT` 时 pop 并 `set_result`。超时后 pop 并 `set_exception`，同时要处理"超时后迟到的结果"—— 此时 `cmd_id` 已不在映射里，直接丢弃并记一条 warning。

**内核未就绪时的行为。** 子进程处于 `STARTING` / `RESTARTING` / `CRASHED` / `FAILED` 时，所有业务命令直接抛领域异常（对应 `CORE_NOT_READY` / `CORE_RESTARTING` / `CORE_CRASHED` 错误码），不进队列等待。理由是让调用方立刻得到明确状态，而不是挂在那里等一个可能永远不就绪的内核。唯一例外是 `PipelineRunner`，它会等待内核就绪再取流水线，队列中的条目不因内核短暂重启而失败。

**回调事件的分派。** 非 `CMD_RESULT` 的事件按类型分派：`CALLBACK` 交给 `LogHub` 与 `PipelineRunner` 的回调处理器，`LOG` 交给 `LogHub`，`READY` / `FATAL` / `PONG` 交给 `CoreSupervisor`。分派表在 `CoreClient` 注册，避免各服务直接触碰 Queue。

## 6. 任务完成的判定

现有 `executor.py` 等待任务完成的方式是循环 `self.asst.running()` 加 5 秒 `Condition.wait`，这有两个问题：`running()` 反映的是整个内核实例是否在跑任务链，不是某个特定任务；5 秒粒度的轮询让状态更新有明显延迟。

新设计以回调事件为准。每个任务在 `APPEND_TASK` 后拿到内核 `task_id`，建立 `maa_task_id → 我们的 task.id` 映射（这部分沿用现有 `CallbackHandler.register_task` 的思路），然后等待该 `task_id` 的终态事件：

| 回调 | 判定 |
|---|---|
| `TaskChainCompleted` | 任务成功 |
| `TaskChainError` | 任务失败，进入重试 |
| `TaskChainStopped` | 任务被中止（用户停止或内核停止），不重试 |
| `AllTasksCompleted` | 整条任务链结束，用于校对 |

辅以超时兜底：某任务超过阈值（按任务类型配置，肉鸽与生息演算显著长于领奖励）仍无终态事件，则主动 `STOP` 并记为失败。现有代码没有这层兜底，一个卡住的任务会让流水线永久挂起。

现有 `handle_task_status` 在 `task.type_name != taskchain` 时抛 `ResponseException`，新实现改为记一条 error 级日志并按 `taskchain` 为准继续，因为回调线程里抛异常危害远大于类型不匹配本身。

## 7. 资源加载与增量资源

`AsstLoadResource` 可重复调用，后加载的增量资源覆盖同名定义。这是活动资源能真正热更新的基础，也是内核库文件无法热替换的对照 —— 资源是内核读取的数据，库是进程映射的代码。

`AsstLoadResource` 的 C 签名是 `(const char* path) -> bool`，**一次只接受一个路径**。而实际加载链有三个增量层（通道 B 的仓库同步、通道 A 的官方 OTA、自定义资源），所以 `LOAD_RESOURCE` 命令携带的是一个有序路径列表，由子进程拆成多次调用：

```python
def handle_load_resource(path: str, incremental_paths: list[str]) -> None:
    if not Asst.load(path=path, user_dir=user_dir):   # 基础资源，必须最先
        raise ResourceLoadError(f"基础资源加载失败: {path}")
    for layer in incremental_paths:                   # 按列表顺序逐层叠加
        if not (Path(layer) / "resource").is_dir():
            continue                                  # 该层尚未下载过，跳过而非报错
        if not Asst.load(path=layer, incremental=True):
            raise ResourceLoadError(f"增量资源层加载失败: {layer}")
```

三条顺序约束都不能违反：

- **基础资源必须是第一次调用。** 除了"后加载覆盖先加载"这条合并语义（见 [07-热更新方案 §3.5](./07-热更新方案.md)），更硬的原因是 `AsstLoadResource` 内部只在内核的资源根目录为空时设置一次，由第一次调用永久钉死；第一次若传增量目录，资源根就指向那个残缺目录，之后所有按路径解析的查找都会错且不报错
- **增量层之间保持列表顺序。** 顺序即优先级，颠倒会让基础层反过来覆盖活动定义，具体的层间排序理由见 [07-热更新方案 §3.7](./07-热更新方案.md)
- **跳过不存在的层，但不跳过失败的层。** 某层没下载过是正常状态（首次启动时 `resource/maa-layers/` 下的 `repo/` `cache/` `custom/` 都不存在），而返回 `false` 是真实故障，必须中断并上报

失败时的恢复要求整条链重新加载而不是从断点续上：内核此刻的资源状态是"加载了前半条链"，既不是更新前也不是更新后。调用方（`UpdateService` 或自定义资源注入）应把出问题的那一层从磁盘上换回旧版本，再完整下发一次 `LOAD_RESOURCE`。

原设计的单个 `incremental_path` 参数装不下这条链。这不是纳入通道 B 才产生的问题——早在要求同时加载官方 OTA 与自定义资源两个目录时就已经不够用了，只是当时没暴露。现有 `asst_manager.py` 只加载一层，需要按上面的形式改写。

运行中重载资源的约束：必须在任务链空闲时进行，否则正在执行的任务可能读到半新半旧的定义。`UpdateService` 在下发 `LOAD_RESOURCE` 前需确认 `RUNNING` 为假，或先停止当前流水线。

自定义 task 注入（agent 的最强自定义能力，见 [11-Agent模块设计](./11-Agent模块设计.md)）走同一机制：把自定义 task 定义写入独立的增量资源目录，再触发 `LOAD_RESOURCE`。这条路径风险最高 —— 格式错误的 task JSON 可能让内核加载失败甚至崩溃，因此注入前必须做 schema 校验，且自定义资源目录与官方 OTA 缓存目录分离，便于一键清空回滚。

## 8. 测试策略

内核层是唯一直接触碰 native 库的部分，也是最难测的部分。

**`FakeAsst` 替身。** 定义 `AsstProtocol`（Python `Protocol`）描述全部方法，worker 依赖 protocol 而非具体类。测试时注入 `FakeAsst`，它按脚本产生回调事件（可模拟任务成功、失败、卡死、连接断开），使 `PipelineRunner`、`LogHub`、重试逻辑、超时兜底全部可在无真机无内核的环境下测试。这是把执行层从内核解耦出来的最大收益之一。

**IPC 协议的契约测试。** 命令与事件的序列化用真实 `multiprocessing.Queue` 测试，但两端都用替身，验证所有消息类型都能正确 pickle 往返。`spawn` 模式下不可 pickle 的对象会在运行时才暴露，契约测试能提前发现。

**崩溃恢复的故障注入。** 让 `FakeAsst` 主动 `os._exit(-11)` 模拟段错误，验证 `CoreSupervisor` 的检测、落库、退避、重启全链路。

**真机冒烟测试。** 少量必须连真实内核与设备的用例（加载资源、连接、截图、点击、提交一个最短任务），标记为需要硬件的测试，不进常规 CI。
