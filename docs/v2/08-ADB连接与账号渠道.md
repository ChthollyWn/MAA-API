> 本文档定义统一的设备连接管理（状态机、四时机重试、双 ADB 路径职责划分）与账号渠道配置。决策依据见 [README 决策速查表](./README.md#决策速查表)，架构位置见 [02-系统架构设计](./02-系统架构设计.md)。

# ADB 连接与账号渠道

## 1. 现状的三个问题

### 1.1 两条独立的 ADB 连接路径，状态不同步

项目里有两处彼此不知道对方存在的 ADB 连接。

**MaaCore 内部的连接**在 `asst_manager.py` 里通过 `asst.connect(adb_path, adb_address)` 建立，底层是 `AsstConnect`。MaaCore 拿到 adb 可执行文件路径与地址后，自己 fork 出 adb 进程执行命令——从 `resource/config.json` 的连接配置可以看到它执行的就是常规 adb 命令，例如 `General` 配置里截图是 `[Adb] -s [AdbSerial] exec-out screencap -p`、点击是 `[Adb] -s [AdbSerial] shell input tap [x] [y]`。

**adbutils 的连接**在 `adb_service.py` 里通过 `adbutils.adb.connect(adb_address)` 建立，用于截图（`/api/adb/screenshot`）。

两者各自维护连接状态，互不通报。后果是：MaaCore 报告 `Disconnect` 时 `adb_service` 毫不知情，截图接口仍然会尝试连接并抛出运行时错误；反过来 adbutils 连接失败也不会触发 MaaCore 侧的任何处理。前端拿到的"设备状态"取决于它问的是哪个接口，两个答案可能矛盾。

### 1.2 配置在模块加载时读取一次

`adb_service.py` 第 11 行：

```python
adb_address = Config.get_config("adb", "address")
```

这行在**模块被 import 时**执行，之后 `adb_connect()` 始终使用这个快照值。`ConfigManager` 虽然提供了 `reload_config()`，但重载后 `adb_service` 里的 `adb_address` 不会更新。按 [README 决策速查表](./README.md#决策速查表)，设置页要做到"改 ADB 地址自动重连"，现有结构下这个需求根本无法实现。

同类问题还有 `HttpUtils` 的 `proxy_path`（`utils.py` 里同样是类属性级别的一次性读取）。这是模块级配置快照的通病，重构时所有配置读取都要改为经 `SettingService` 动态获取。

### 1.3 重试逻辑在进程之外

`scripts/start-maa-api.sh` 里有一段等待 ADB 就绪的循环：最多 60 次、每次间隔 5 秒（合计 5 分钟），期间反复执行 `adb connect` 并用 `adb -s <addr> shell echo ok` 验证连通性，超时则 `exit 1` 不启动服务。

这段逻辑本身是对的——模拟器启动比服务慢是常态。但放在启动脚本里有三个问题：只在启动时有效，运行中断线不管；失败就完全不启动服务，用户连看一眼状态的界面都没有；且它与服务内部的连接逻辑重复，参数改了两边要同步。

重构要把它搬进服务，并按决策改为**不阻塞服务启动**。

## 2. DeviceManager

三个问题统一到一个 `services/device_service.py` 里的 `DeviceManager` 解决。它是设备连接的唯一事实来源，对上层暴露状态查询与操作接口，对下管理 MaaCore 与 adbutils 两条路径。

```python
class DeviceManager:
    """设备连接的唯一事实来源。按 core_id 维护独立状态（多实例扩展口）。"""

    # 状态查询
    @property
    def state(self) -> DeviceState: ...
    def snapshot(self) -> DeviceInfo: ...        # 供 REST 与 WS 广播

    # 连接操作
    async def connect(self, *, reason: str) -> bool: ...
    async def disconnect(self) -> None: ...
    async def ensure_available(self, timeout: float) -> bool: ...   # 任务前预检

    # 设备侧能力（adbutils 路径）
    async def screenshot(self) -> ScreenshotResult: ...
    async def swipe(self, x1, y1, x2, y2, duration_ms: int) -> None: ...
    async def install_apk(self, path: Path, **kw) -> InstallResult: ...
    async def force_stop(self, package: str) -> None: ...

    # 设备发现
    async def list_devices(self) -> list[DeviceCandidate]: ...

    # 回调钩子（由 CoreClient 分派 CALLBACK 事件时调用）
    def on_core_connection_event(self, what: str, details: dict) -> None: ...
```

`DeviceManager` 按 `core_id` 维护独立状态，首版恒为 `"default"`（见 [02-系统架构设计 §5.4](./02-系统架构设计.md) 的多实例扩展口）。

所有配置读取经 `SettingService`，不做模块级快照。ADB 地址或路径变更时 `SettingService` 触发 `DeviceManager.reconfigure()`，后者断开旧连接并按新配置重连。

## 3. 连接状态机

```
                      ┌──────────────┐
          ┌──────────►│ DISCONNECTED │◄──────────┐
          │           └──────┬───────┘           │
          │                  │ connect()         │ disconnect()
          │                  ▼                   │
          │           ┌──────────────┐           │
          │  连接失败  │  CONNECTING  │           │
          │  且重试未尽└──────┬───────┘           │
          │                  │ 连接成功           │
          │                  ▼                   │
          │           ┌──────────────┐           │
          └───────────┤  CONNECTED   ├───────────┘
                      └──────┬───────┘
                             │ Reconnecting / Disconnect
                             │ / ScreencapFailed / 预检失败
                             ▼
                      ┌──────────────┐  重连成功
                      │ RECONNECTING ├──────────► CONNECTED
                      └──────┬───────┘
                             │ 重试次数耗尽
                             ▼
                      ┌──────────────┐
                      │ UNAVAILABLE  │
                      └──────┬───────┘
                             │ 手动重连 / 定期探测成功
                             └──────────► CONNECTING
```

五个状态的语义与转移条件：

| 状态 | 含义 | 进入条件 | 离开条件 |
|---|---|---|---|
| `DISCONNECTED` | 未连接，也未在尝试 | 初始状态；主动断开后 | 调用 `connect()` |
| `CONNECTING` | 首次连接尝试中 | 从 `DISCONNECTED` 或 `UNAVAILABLE` 发起连接 | 成功转 `CONNECTED`；失败且重试未尽则原地重试；重试耗尽转 `UNAVAILABLE` |
| `CONNECTED` | 连接正常可用 | 连接成功 | 断线事件转 `RECONNECTING`；主动断开转 `DISCONNECTED` |
| `RECONNECTING` | 曾经连上，现在断了，正在恢复 | 收到断线类回调或健康检查失败 | 恢复转 `CONNECTED`；重试耗尽转 `UNAVAILABLE` |
| `UNAVAILABLE` | 重试耗尽，放弃自动恢复 | `CONNECTING` 或 `RECONNECTING` 重试耗尽 | 手动重连或低频后台探测成功转 `CONNECTING` |

`CONNECTING` 与 `RECONNECTING` 分开是有意的，二者在三个方面不同：重试参数不同（见 §4）；`RECONNECTING` 意味着有任务可能正在执行、需要通知 `PipelineRunner`；前端展示文案不同（"正在连接设备" vs "设备断开，正在重连"）。

`UNAVAILABLE` 不是终态。进入后启动一个低频后台探测（默认每 60 秒一次 `adb shell echo ok`），成功即自动转回 `CONNECTING`。这覆盖了"用户过了半小时才打开模拟器"的场景——没有这条，用户必须手动点重连才能恢复。

每次状态变更都：落库一条设备状态历史（用于排查"昨晚是几点断的"）、WebSocket 广播 `device_status` 消息（结构见 [06-实时日志与WebSocket §7.3](./06-实时日志与WebSocket.md)）、产一条 `service` 源日志。

## 4. 四个重试时机

四个时机共用同一套连接原语，区别在触发条件、重试参数与失败后的行为。

| 时机 | 触发条件 | 重试次数 | 间隔 | 退避 | 失败后行为 |
|---|---|---|---|---|---|
| 启动等待 | 服务启动 | 60 | 5 秒 | 否 | 转 `UNAVAILABLE`，服务照常运行 |
| 任务前预检 | `PipelineRunner` 取到流水线 | 3 | 2 秒 | 否 | 流水线置 `FAILED` 或延后（见 §4.2） |
| 运行中重连 | MaaCore 断线回调 | 5 | 3 秒起 | 指数（3/6/12/24/48s） | 转 `UNAVAILABLE`，当前流水线置 `FAILED` |
| 手动重连 | 前端点击 | 1 | — | — | 立即返回失败原因 |

### 4.1 服务启动时的等待

把 `start-maa-api.sh` 的等待逻辑原样搬进服务，参数保持 60 次 × 5 秒不变（这个组合经过实际使用验证，覆盖了模拟器冷启动的耗时）。

**但按决策不阻塞服务启动。** `lifespan` 里只是创建一个后台任务就返回：

```python
async def lifespan(app: FastAPI):
    ...
    device_manager = DeviceManager(settings, core_registry, log_hub, ws_manager)
    app.state.device_task = asyncio.create_task(
        device_manager.connect_with_retry(
            attempts=settings.adb.startup_retry_attempts,   # 60
            interval=settings.adb.startup_retry_interval,   # 5.0
            reason="startup",
        )
    )
    yield
    app.state.device_task.cancel()
```

这与 [02-系统架构设计 §7](./02-系统架构设计.md) 的启动顺序一致：服务秒起，设备在后台连。此时 `GET /api/system/health` 如实返回设备 `CONNECTING`，前端展示"正在连接设备（第 3 / 60 次）"并提供跳过按钮。

`start-maa-api.sh` 相应简化，删掉整个等待循环，只保留环境变量设置与 `exec uvicorn`。

**连接的顺序很重要。** 后台任务里要做的不只是 `adb connect`：

```
① adb connect <address>           建立 adb 层连接
② adb -s <address> shell echo ok  验证真正可用（connect 成功不代表设备可交互）
③ 等 CoreSupervisor 就绪（READY 事件）
④ 向子进程下发 CONNECT 命令       建立 MaaCore 侧连接
⑤ 等 AsyncCallInfo 回执确认        见 §5
```

第 ② 步不能省。`adb connect` 对一个端口开着但 adbd 未就绪的模拟器也会返回成功，此时 `device_list()` 里设备状态是 `offline`，后续所有操作都会失败。原脚本里用 `shell echo ok` 验证是正确的做法，保留。

第 ③ 步的依赖关系决定了设备连接不能早于内核就绪。但反过来，**内核就绪不依赖设备连接**——子进程启动序列里不做连接（见 [03-MaaCore内核层设计 §3.1](./03-MaaCore内核层设计.md)），连接由主进程在 `READY` 之后显式下发。这样设备连不上时内核仍是 `READY` 状态，可以正常响应版本查询等命令。

### 4.2 任务执行前预检

`PipelineRunner` 从队列取到流水线后、下发第一条 `APPEND_TASK` 之前，先确认设备可用：

```python
async def _run_pipeline(self, pipeline: Pipeline) -> None:
    if not await self.devices.ensure_available(timeout=10.0):
        await self._handle_device_unavailable(pipeline)
        return
    ...
```

`ensure_available()` 的行为按当前状态分支：

| 当前状态 | 行为 |
|---|---|
| `CONNECTED` | 做一次轻量健康检查（`adb shell echo ok`，超时 3 秒），通过即返回 `True` |
| `CONNECTING` / `RECONNECTING` | 等待最多 `timeout`，期间状态转 `CONNECTED` 则返回 `True` |
| `DISCONNECTED` / `UNAVAILABLE` | 立即发起一轮短重试（3 次 × 2 秒） |

**不可用时是等待还是失败，取决于流水线来源**，这是个需要区分对待的判断：

- `source == "scheduled"`（定时任务）：**延后而非失败**。把流水线退回队列并记录 `deferred_until = now + 5min`，最多延后 6 次（合计 30 分钟）。理由是定时任务往往在凌晨触发，此时模拟器可能还没起来，直接判失败会让用户第二天早上看到一堆失败记录，而实际上晚几分钟就能跑
- `source == "manual"`（前端手动）：**立即失败**。用户就在屏幕前等着，让他等 30 分钟毫无意义。置 `FAILED`，`error_code` 为 `DEVICE_UNAVAILABLE`，前端弹出带"重连设备"按钮的错误提示
- `source == "agent"`：**立即失败**，但错误信息要写得让 agent 能理解并自行决策（例如提示它可以调用重连 tool 后重试）。agent 的重试策略由它自己掌握，不由我们代劳

预检的健康检查用 adbutils 而非 MaaCore，因为它更轻量、超时可控，且不会干扰内核状态。

### 4.3 运行中断线自动重连

#### 钩子挂接

MaaCore 的 `ConnectionInfo` 回调里已经有完整的断线事件，`callback_handler.py` 的 `connection_map` 里都有对应文案（完整映射表见 [06-实时日志与WebSocket §3.2](./06-实时日志与WebSocket.md)）。相关的五个 `what` 值：

| `what` | MaaCore 的含义 | `DeviceManager` 的响应 |
|---|---|---|
| `Reconnecting` | 内核检测到断线，**正在自行重连** | 状态转 `RECONNECTING`，**我们不动手** |
| `Reconnected` | 内核自行重连成功 | 状态转回 `CONNECTED`，重置我们的重试计数 |
| `Disconnect` | 内核重连失败，**放弃了** | 接管，启动我们的重连流程 |
| `ScreencapFailed` | 截图失败（adb / 模拟器异常） | 计数器累加，连续 3 次才转 `RECONNECTING` |
| `ConnectFailed` | 连接尝试失败 | 按 §4.1 / §4.4 的上下文决定重试 |

事件分派路径：子进程转发 `CALLBACK` 事件 → 主进程 `CoreClient` 分派 → 同时交给 `LogHub`（产日志）与 `DeviceManager.on_core_connection_event()`（驱动状态机）。两者互不干扰，日志文案的调整不会影响状态机行为。

注意 `ConnectFailed` 的拼写。现有代码写的是 `ConnectFaild`（少一个 `e`），而 MaaCore v6.17.5 实际发出的是 `ConnectFailed`——这个键从未匹配成功过。详见 [06-实时日志与WebSocket §3.2](./06-实时日志与WebSocket.md)，新实现两个拼写都接。

#### 两套重连机制如何不打架

**MaaCore 自己有重连机制**，这从 `Reconnecting` / `Reconnected` / `Disconnect` 这组三段式事件就能看出：内核发现断线后自行尝试，成功发 `Reconnected`，失败发 `Disconnect`。我们也要有重连机制（因为内核放弃后总得有人接手）。两套机制同时动手会互相干扰——我们在内核重连期间执行 `adb disconnect` + `adb connect`，很可能正好打断内核那次尝试。

避免冲突的原则是**明确的接管时机**：

```
Reconnecting  → 内核在处理，我们只更新状态与 UI，不执行任何 adb 操作
                启动一个看门狗计时器（默认 90 秒）
Reconnected   → 内核搞定了，取消看门狗，状态回 CONNECTED
Disconnect    → 内核放弃了，取消看门狗，我们接管
看门狗超时     → 内核既没成功也没明确放弃（可能内核自己卡住了）
                此时才主动接管，并记一条 WARNING 日志
```

看门狗是必要的兜底。`Reconnecting` 之后如果既不来 `Reconnected` 也不来 `Disconnect`（内核线程卡住、或事件在 IPC 中丢失），没有看门狗的话状态会永久停在 `RECONNECTING`。

我们接管后的动作序列：

```
① adb disconnect <address>        清理可能的半死连接
② adb connect <address>
③ adb -s <address> shell echo ok  验证
④ 向子进程下发 CONNECT 命令        重建 MaaCore 侧连接
⑤ 等 AsyncCallInfo 回执
失败 → 指数退避后回到 ①（3/6/12/24/48 秒，共 5 次）
5 次耗尽 → 转 UNAVAILABLE
```

第 ① 步的 `adb disconnect` 不能省。adb server 可能持有一个状态为 `offline` 的设备条目，不先清掉的话 `connect` 会直接返回"already connected"而实际不可用。

#### 对正在执行的流水线的影响

断线时正在跑的流水线怎么办，取决于重连是否成功。

重连在 `RECONNECTING` 期间成功（无论是内核自己还是我们接管）：**流水线继续**。MaaCore 的任务链在连接恢复后通常能继续执行，强行中断反而浪费了已完成的进度。但要在日志里留下断线记录，便于事后判断任务结果是否可信。

重连失败转 `UNAVAILABLE`：**当前流水线置 `FAILED`**，`error_code` 为 `DEVICE_DISCONNECTED`，队列中未开始的条目保持 `PENDING`（与内核崩溃的处理一致，见 [02-系统架构设计 §4](./02-系统架构设计.md)）。设备恢复后队列自动继续消费。

无论哪种情况都要走通知通道推送——用户可能不在电脑前，需要知道任务因为设备问题中断了。

`ScreencapFailed` 单独说明：它不代表连接已断，可能只是一次偶发的截图超时。连续 3 次才升级为断线处理，避免因为一次抖动就中断任务。计数器在任意一次成功截图后清零。

### 4.4 前端手动重连

接口：

```
POST /api/device/reconnect
```

请求体可选覆盖地址（不传则用当前配置）：

```json
{ "address": "127.0.0.1:5555", "adb_path": null }
```

响应为 `200` 带最新的设备快照，或 `409` / `503` 带错误详情：

```json
{
  "state": "connected",
  "address": "127.0.0.1:5555",
  "uuid": "f7c1c4ced5e96a23",
  "resolution": { "width": 2560, "height": 1440 },
  "elapsed_ms": 3987
}
```

手动重连**只试一次**，不做重试。用户点了按钮就在等结果，失败了让他看到具体原因比让他等 5 次退避重试有价值得多。失败响应里的 `details` 要带上实际执行的命令与原始输出：

```json
{
  "error": {
    "code": "ADB_CONNECT_FAILED",
    "message": "无法连接到 127.0.0.1:5555",
    "details": {
      "stage": "adb_connect",
      "command": "adb connect 127.0.0.1:5555",
      "output": "failed to connect to '127.0.0.1:5555': Connection refused",
      "hint": "请确认模拟器已启动，且 ADB 调试端口为 5555"
    }
  }
}
```

`hint` 按失败阶段给出针对性建议，这比通用的"连接失败"有用得多：

| 失败阶段 | 常见原因 | hint |
|---|---|---|
| `adb_binary` | adb 路径不对 | 请在设置页检查 ADB 路径，或将 adb 加入 PATH |
| `adb_connect` | 模拟器未启动 / 端口不对 | 请确认模拟器已启动，且 ADB 调试端口正确 |
| `adb_shell` | 设备 offline | 设备已连接但无响应，请尝试重启模拟器 |
| `core_connect` | MaaCore 侧失败 | 内核无法连接设备，可能是分辨率不受支持或触控方案不兼容 |

**并发保护**：手动重连与自动重连共用一把 `asyncio.Lock`。手动重连请求到来时若自动重连正在进行，不排队等待，而是返回 `409` 并带上当前重试进度，前端展示"正在自动重连（第 2 / 5 次）"。

设置页改 ADB 地址时**自动触发**一次重连（决策要求的"改配置热生效"），走的是同一个 `DeviceManager.reconfigure()` 路径。

## 5. 从 AsstConnect 迁移到 AsstAsyncConnect

### 5.1 签名差异

两个 API 在 `AsstCaller.h` 中的声明：

```c
// 同步连接，功能已完全被异步连接取代
// FIXME: 5.0 版本将废弃此接口
/* deprecated */ AsstBool ASSTAPI AsstConnect(
    AsstHandle handle, const char* adb_path, const char* address, const char* config);

/* Async with AsstMsg::AsyncCallInfo Callback*/
AsstAsyncCallId ASSTAPI AsstAsyncConnect(
    AsstHandle handle, const char* adb_path, const char* address, const char* config,
    AsstBool block);
```

两处差异：多一个 `block` 参数；返回 `AsstAsyncCallId`（`int32_t`）而非 `AsstBool`。

**返回值语义完全不同，这是迁移时最容易踩的坑。** `AsstConnect` 返回 `true` 表示连接成功；`AsstAsyncConnect` 返回的是一个调用编号，它只表示"请求已受理"，**非零不代表连接成功**。现有代码 `if not asst.connect(...): raise RuntimeError(...)` 这种写法直接平移到异步接口会得到错误的结论。

`block` 参数的含义是内核内部是否串行化该调用（等待前面的异步调用完成），**不是** Python 侧阻塞。无论 `block` 取值如何，Python 调用都立即返回。连接场景下取 `block=True`，避免与其他异步调用交错执行。

`asst.py` 里已经声明了 `AsstAsyncConnect` 的 `argtypes` 与 `restype`，但没有对应的 Python 方法（见 [03-MaaCore内核层设计 §1.1](./03-MaaCore内核层设计.md) 的差距清单）。新增方法签名见该文档 §2.1 的 `connect_async`。

`config` 参数保持默认 `"General"`。可选值是 `resource/config.json` 里 `connection` 数组的 `configName`，实测 v6.17.5 提供 16 种：`General`、`CapWithShell`、`BlueStacks`、`MuMuEmulator12`、`LDPlayer`、`Androws`、`Nox`、`XYAZ`、`WSA`、`Compatible`、`SecondResolution`、`CompatMac`、`CompatPOSIXShell`、`Waydroid`、`AVD`。设置页把这个列表做成下拉选择（从 `config.json` 动态读取而非硬编码），默认 `General`，遇到特定模拟器的兼容问题时由用户切换。

### 5.2 通过 AsyncCallInfo 获知结果

异步连接的结果经 `Message.AsyncCallInfo`（枚举值 4）回调送达，载荷里带 `async_call_id` 与调用结果。流程：

```
主进程 CoreClient.connect()
  → 投递 CONNECT 命令（带 cmd_id）
子进程
  → 调用 AsstAsyncConnect，拿到 async_call_id
  → 立即回 CMD_RESULT { cmd_id, ok: true, data: { async_call_id } }
  → 注册 async_call_id 到本地待决表
MaaCore 内核线程
  → 连接完成，触发 AsyncCallInfo 回调
子进程回调桥接
  → 照常转发 CALLBACK 事件（含 async_call_id 与结果）
主进程 CoreClient
  → 按 async_call_id 找到等待中的 Future，set_result
  → DeviceManager 据此推进状态机
```

关键设计点是**两级 Future**。`CMD_RESULT` 只确认"命令送达并拿到了调用编号"，真正的连接结果要等 `AsyncCallInfo`。`CoreClient` 里维护两张映射表：

```python
self._pending_cmds: dict[str, asyncio.Future]    # cmd_id → Future
self._pending_async: dict[int, asyncio.Future]   # async_call_id → Future
```

`connect()` 的完整实现需要串起两级：

```python
async def connect(self, adb_path: str, address: str,
                  config: str = "General", timeout: float = 60.0) -> bool:
    result = await self._send("CONNECT", {
        "adb_path": adb_path, "address": address,
        "config": config, "block": True,
    }, timeout=10.0)                       # 第一级：命令受理，很快

    call_id = result["async_call_id"]
    fut = self._loop.create_future()
    self._pending_async[call_id] = fut
    try:
        return await asyncio.wait_for(fut, timeout)   # 第二级：连接结果，可能很慢
    except asyncio.TimeoutError:
        raise DomainError("ADB_CONNECT_TIMEOUT", f"连接超时（{timeout}s）")
    finally:
        self._pending_async.pop(call_id, None)
```

两级各有各的超时：命令受理 10 秒（只是 IPC 往返），连接结果 60 秒（要跑 adb 连接、分辨率探测、截图方式测速等，实测一次成功连接耗时约 4 秒，失败时可能更久）。

**`AsyncCallInfo` 的具体载荷结构需要实测确认。** 本地 `asst.log` 的样本里没有捕获到 `AsyncCallInfo` 回调（现有代码用的是同步 `AsstConnect`），因此 `async_call_id` 的确切字段名与成功标志的表示方式暂时无法从实测数据确证。实现时的稳妥做法是先写一个最小验证脚本，打印一次 `AsstAsyncConnect` 触发的完整回调 JSON，据此再定解析逻辑。这一条列入 §11 的开放问题。

在此之前，解析器要写得宽容：按几个可能的字段名依次尝试取值，都取不到则记录完整原文并回退到用 `AsstConnected` 轮询判定连接状态（`AsstConnected` 是一个简单的同步查询，见 [03-MaaCore内核层设计 §1.1](./03-MaaCore内核层设计.md) 的缺失 API 清单）。这个回退路径本身也有价值——它给了我们一个不依赖回调结构的连接状态判定手段。

### 5.3 IPC 协议中的表达

`CONNECT` 命令的 payload 在 [02-系统架构设计 §3.1](./02-系统架构设计.md) 已定义为 `adb_path`、`address`、`config`、`block`。`CMD_RESULT` 的 `data` 增加 `async_call_id` 字段：

```python
# 主 → 子
{"cmd_id": "uuid4", "type": "CONNECT",
 "payload": {"adb_path": "/opt/homebrew/bin/adb", "address": "127.0.0.1:5555",
             "config": "General", "block": True}}

# 子 → 主（命令受理）
{"type": "CMD_RESULT",
 "payload": {"cmd_id": "uuid4", "ok": True, "data": {"async_call_id": 3}}}

# 子 → 主（连接结果，原始回调）
{"type": "CALLBACK", "ts": 1758000004.5,
 "payload": {"msg": 4, "details": {"async_call_id": 3, "...": "实测后补全"}}}
```

`AsstAsyncClick` 与 `AsstAsyncScreencap` 走完全相同的两级模式，`CLICK` 与 `SCREENCAP` 命令的实现可以共用同一个 `_await_async_call()` 辅助方法。

## 6. 两条 ADB 路径的职责划分

统一到 `DeviceManager` 之后，MaaCore 与 adbutils 两条路径并不合并为一条——它们能力不同，各有不可替代的部分。

### 6.1 分工

| 能力 | 承担方 | 原因 |
|---|---|---|
| 任务执行期间的全部设备交互 | MaaCore | 内核自己管理，我们不介入 |
| 连接建立与断线重连 | MaaCore（主）+ adbutils（辅） | 内核负责自身连接，adbutils 做前置的连通性验证 |
| 截图（前端查看、agent 视觉） | adbutils | 见 §6.2 |
| 点击 | MaaCore（`AsstAsyncClick`） | 内核原生支持，坐标系与内核一致 |
| 回主界面 | MaaCore（`AsstBackToHome`） | 内核有专门实现，比自己找按钮可靠 |
| 滑动 / 长按 / 输入文本 / 按键 | adbutils | MaaCore **无对应 C API** |
| APK 安装 | adbutils | MaaCore 不提供 |
| 停止游戏进程 | adbutils | MaaCore 不提供独立接口 |
| 读取已安装版本（`dumpsys`） | adbutils | MaaCore 不提供 |
| 设备列表扫描 | adbutils | MaaCore 不提供 |

**关于滑动需要澄清一个容易混淆的点。** MaaCore 内部**是能滑动的**——`resource/config.json` 的 `General` 配置里明确定义了 `"swipe": "[Adb] -s [AdbSerial] shell input swipe [x1] [y1] [x2] [y2] [duration]"`，内核在执行 `tasks.json` 定义的任务时会用到它。但**没有任何 C API 能让我们主动触发一次滑动**：导出符号里只有 `AsstAsyncClick`，不存在 `AsstAsyncSwipe`。这就是 [README 关键技术约束](./README.md#关键技术约束)里"MaaCore 只有点击没有滑动"的准确含义——是 API 表面的限制，不是内核能力的缺失。因此 agent 的滑动类原子操作只能走 adbutils 自己拼 `adb shell input swipe`。

### 6.2 为什么截图走 adbutils

MaaCore 有 `AsstAsyncScreencap` + `AsstGetImage`，功能上够用，但截图走 adbutils 更合适，理由有三。

**不占用内核。** 子进程的命令循环是单线程串行的（见 [03-MaaCore内核层设计 §3.3](./03-MaaCore内核层设计.md)），一次截图命令会阻塞后续命令。前端日志页每隔几秒刷新一张截图、agent 的视觉循环连续截图，这些都会挤占内核的命令处理能力。adbutils 路径完全独立，不与内核争抢。

**任务执行期间也能用。** 流水线运行时内核正忙，走内核截图要么排队要么与内核自己的截图交错。adbutils 路径可以随时截，这对"任务卡住了想看看屏幕上是什么"的排查场景是刚需。

**数据传输路径更短。** 走内核需要"子进程取图 → 编码写盘 → IPC 传路径 → 主进程读盘"（见 [02-系统架构设计 §3.3](./02-系统架构设计.md)），走 adbutils 直接在主进程拿到 PIL Image。

保留 MaaCore 截图路径作为备选：adbutils 失败时（例如 adb 层出问题但内核连接还在）可以退一步用内核截图。两条路径产出的图片统一交给 `util/image.py` 的 `store_screenshot()` 处理，落盘与缩略图策略见 [06-实时日志与WebSocket §8](./06-实时日志与WebSocket.md)。

### 6.3 两个 adb 客户端能否共存

**能，而且这是 adb 的常规工作方式。**

关键在于 adb 的三层架构：客户端（`adb` 命令行）、服务端（adb server，本机 5037 端口的常驻进程）、设备端（adbd）。同一台机器上所有 adb 客户端共享**同一个 adb server**，server 再与设备通信。MaaCore fork 出的 adb 进程与 adbutils 都是客户端，它们连的是同一个 server。server 本身就是为多客户端并发设计的。

有三个实际需要注意的点。

**adb server 的启动竞争。** server 未运行时，第一个客户端会自动拉起它。两个客户端同时首次连接可能都尝试启动 server，产生短暂的端口竞争，表现为偶发的 `cannot connect to daemon`。规避方式是在 `DeviceManager` 初始化时主动执行一次 `adb start-server` 并等待完成，把 server 拉起这件事收敛到一处。

**adb 可执行文件版本不一致。** MaaCore 用的是 `config.yaml` 里 `adb.path` 指定的那个（当前为 `/opt/homebrew/bin/adb`），adbutils 用的是它自己查找到的（可能是 PATH 里的另一个，甚至是 adbutils 内置的）。**不同版本的 adb 客户端连同一个 server 会导致 server 被强制重启**——adb 客户端发现 server 版本与自己不匹配时会 `kill-server` 再重新拉起，正在进行的所有连接全部断开。这是一个很隐蔽的故障源。

处置方式是**强制两者使用同一个 adb 二进制**：

```python
# DeviceManager 初始化时
adbutils.adb  # adbutils 通过 ADB_PATH 环境变量或显式参数指定
os.environ["ADB"] = str(resolved_adb_path)
```

并在启动时校验：执行 `<adb_path> version` 记录版本号，与 adbutils 实际使用的二进制比对，不一致则记 `WARNING` 并在设置页给出提示。这个校验很便宜，但能避免一类极难排查的问题。

**并发命令的交错。** 两个客户端同时对同一设备发命令，adb server 会串行化，但顺序不保证。实际影响很小（我们的操作本身就是异步的），唯一需要小心的是"截图 + 点击"这类要求时序的组合——agent 先截图看到某个按钮、再点击那个坐标，中间内核可能已经把界面点走了。这不是 adb 层的问题而是并发语义问题，处置方式是 [02-系统架构设计 §5.3](./02-系统架构设计.md) 定义的原子操作并发规则：流水线运行中的原子操作返回 `409`，除非显式 `force=true`。

## 7. 设备扫描

现状只能在 `config.yaml` 里手填地址。改为提供设备列表接口，前端做成下拉选择 + 手动输入的组合。

```
GET /api/device/candidates
```

```json
{
  "current": "127.0.0.1:5555",
  "devices": [
    { "serial": "127.0.0.1:5555", "state": "device", "model": "MuMu",
      "is_current": true, "label": "MuMu (127.0.0.1:5555)" },
    { "serial": "emulator-5554", "state": "device", "model": "sdk_gphone64_arm64",
      "is_current": false, "label": "sdk_gphone64_arm64 (emulator-5554)" },
    { "serial": "127.0.0.1:7555", "state": "offline", "model": null,
      "is_current": false, "label": "127.0.0.1:7555（离线）" }
  ],
  "common_ports": [5555, 5556, 7555, 16384, 21503, 62001]
}
```

实现用 `adbutils.adb.list()` 拿到设备列表（它返回 serial 与 state，比 `device_list()` 更轻量——后者会为每个设备建立连接对象）。`model` 通过 `getprop ro.product.model` 获取，对 `state != "device"` 的设备跳过这一步以免超时。

`state` 的取值直接透传 adb 的原始值：`device`（可用）、`offline`（已连接但无响应）、`unauthorized`（未授权，真机常见）。前端对 `unauthorized` 给出"请在设备上确认 USB 调试授权"的提示。

**`common_ports` 是给"设备没出现在列表里"的情况用的。** `adb devices` 只显示已连接过的网络设备，一个刚启动的模拟器不会自动出现。前端提供一个"扫描常用端口"按钮，对列表里的端口逐个尝试 `adb connect`（并发执行，每个超时 2 秒），把成功的加入候选。端口清单是常见模拟器的默认值：

| 端口 | 模拟器 |
|---|---|
| 5555 | 通用 / MuMu / Waydroid |
| 5556 | 部分多开实例 |
| 7555 | MuMu 12 |
| 16384 | 网易 MuMu（旧版） |
| 21503 | 逍遥模拟器 |
| 62001 | 夜神模拟器 |

这个清单做成配置项而非硬编码，用户可以增补。扫描是用户主动触发的动作，不在后台自动执行——对局域网内其他设备的端口做自动扫描是不礼貌的行为。

## 8. 截图质量与性能

### 8.1 screenshot_quality 的作用

`config.yaml` 里 `adb.screenshot_quality: 25`，在 `adb_service.py` 中作为 PIL 的 JPEG 保存质量参数：

```python
pil_image.save(_path, quality=screenshot_quality)
```

取值范围 1-95（PIL 的约定：超过 95 时 JPEG 编码器的行为不再可控，且文件体积急剧增大而观感无提升，所以上限卡在 95）。当前值 25 是相当激进的压缩。

需要明确的是**这个参数只影响编码质量，不影响截图本身的分辨率**。`device.screenshot()` 拿到的始终是设备原始分辨率的位图（实测本机设备为 2560×1440），`quality` 只决定 JPEG 编码时丢弃多少高频信息。

### 8.2 移动端的尺寸与压缩策略

2560×1440 的图，即使 `quality=25` 也有数百 KB，在移动网络下加载一张要好几秒。而前端日志页会连续展示多张。

策略是**按用途分档**，与 [06-实时日志与WebSocket §8.3](./06-实时日志与WebSocket.md) 的缩略图规格统一：

| 用途 | 尺寸 | 质量 | 典型体积 |
|---|---|---|---|
| 日志内联缩略图 | 长边 320 px | 60 | 15~30 KB |
| 移动端查看 | 长边 1280 px | 沿用配置值 | 80~150 KB |
| 桌面端查看 / 下载原图 | 原始尺寸 | 沿用配置值 | 300~600 KB |

**缩放比降低 JPEG 质量更有效。** 从 2560 缩到 1280，像素数降到 1/4，体积降幅远大于把 quality 从 50 调到 25 的效果，而且观感损失小得多。因此建议把默认 `screenshot_quality` 从 25 上调到 **60**，同时对移动端启用 1280 px 的尺寸限制——最终体积更小，画质反而更好。这个调整需要在设置页说明，避免用户困惑于"质量数值变大了为什么加载更快"。

档位选择由客户端通过查询参数指定（`?size=thumb|mobile|full`），服务端不做 UA 嗅探——UA 判断在平板、桌面浏览器缩小窗口等场景下都会判错，让客户端自己声明更可靠。

**agent 的截图**是另一种情况：多模态模型需要看清界面细节，过度压缩会直接影响判断准确率。agent 的截图 tool 默认返回 `full` 档，且质量不低于 70。这条在 tool 描述里写明，让 agent 知道它拿到的是高质量图。相关约定见 [11-Agent模块设计](./11-Agent模块设计.md)。

### 8.3 截图路径的性能

adbutils 的 `device.screenshot()` 底层走 `adb exec-out screencap -p`，2560×1440 的设备上一次约 0.5~1.5 秒，主要耗时在设备侧截图与数据传输。这是同步阻塞调用，必须放到线程池：

```python
async def screenshot(self) -> ScreenshotResult:
    img = await asyncio.to_thread(self._device.screenshot)
    return await asyncio.to_thread(store_screenshot, img, quality=self._quality)
```

不这么做的话每次截图会把整个事件循环卡住一秒多，所有 HTTP 请求与 WebSocket 推送一起停摆。现有 `adb_service.py` 是同步函数，被 FastAPI 的同步路由（`def` 而非 `async def`）调用时 FastAPI 会自动放进线程池，所以现状没有暴露问题；重构成 `async def` 后必须显式 `to_thread`，否则反而引入回归。

**并发截图要做去重。** 多个客户端同时请求截图时，不必真的截多次。用一个"进行中的截图"Future 做合并：已有截图在进行则等待其结果，而不是发起新的。这个优化对 agent 的视觉循环 + 前端同时查看的场景效果明显。

`resource/image/screenshot/screenshot.jpeg` 这个固定文件名要废弃——多个请求并发写同一个文件会读到半截图像。改为按内容哈希命名（见 [06-实时日志与WebSocket §8.2](./06-实时日志与WebSocket.md)）。

## 9. 账号渠道

### 9.1 官服与 B 服的差异汇总

按决策只支持这两个渠道，默认 B 服。全部差异点：

| 项 | 官服 | B 服 |
|---|---|---|
| `client_type` 字符串 | `Official` | `Bilibili` |
| 包名 | `com.hypergryph.arknights` | `com.hypergryph.arknights.bilibili` |
| APK 下载源 | `https://ak.hypergryph.com/downloads/android_lastest` | biligame 接口返回的 `android_download_link` |
| 远端版本号 | 无接口，只能用包体更新时间近似 | 可查（见 [07-热更新方案 §4.1](./07-热更新方案.md)） |
| `account_name` 格式 | 手机号掩码，如 `123****4567` | 用户名，如 `张三` |

`client_type` 的字符串取值来自 `task.py` 中 `StartUpTask` / `CloseDownTask` / `FightTask` 的文档字符串，完整可选值为 `Official`、`Bilibili`、`txwy`、`YoStarEN`、`YoStarJP`、`YoStarKR`。本项目只开放前两个，但**校验时接受全部六个**——如果用户通过开放 API 传了外服值，没有理由拦（内核支持），只是我们的界面不提供选项、更新与版本检测功能不覆盖。

包名映射与 MaaCore 自己的认知一致。`resource/config.json` 顶层有一个 `packageName` 字典：

```json
{
  "Official": "com.hypergryph.arknights",
  "Bilibili": "com.hypergryph.arknights.bilibili",
  "YoStarEN": "com.YoStarEN.Arknights",
  "YoStarJP": "com.YoStarJP.Arknights",
  "YoStarKR": "com.YoStarKR.Arknights",
  "txwy": "tw.txwy.and.arknights"
}
```

我们的包名常量应当**从这个文件读取而非硬编码**，这样 MAA 更新后新增渠道或改包名时自动跟随。读不到文件时回退到硬编码的两个国服包名。

`account_name` 的匹配规则来自 `StartUpTask` 的文档字符串，两服的差异值得在前端表单里明确提示：

> 仅支持切换至已登录的账号，使用登录名进行查找，保证输入内容在所有已登录账号中唯一即可。
> 官服示例：`123****4567`，可输入 `123****4567`、`4567`、`123`、`3****4567`。
> B 服示例：`张三`，可输入 `张三`、`张`、`三`。

也就是说它是**子串匹配**而非精确匹配，只要在已登录账号中唯一即可。前端在渠道为官服时把输入框的占位符设为手机号掩码样例，B 服时设为用户名样例。按决策只做单账号，所以这个字段通常留空（不切换账号），只在用户确有多账号时填写。

### 9.2 三层优先级

```
① 单任务显式指定的 client_type       最高
② 全局默认配置 settings.game.client_type
③ 硬编码兜底 "Bilibili"              最低
```

第 ③ 层的存在是为了保证任何情况下都有确定值——配置文件损坏、数据库未初始化、新装未配置，都不应该让任务因为渠道为空而失败。兜底值取 `Bilibili` 与决策的"默认 B 服"一致。

**注入发生在哪一层**是个需要明确的设计决定：**在 `domain/task.py` 的任务参数构建层，即落库之前**。

```python
def resolve_client_type(explicit: str | None, settings: Settings) -> str:
    return explicit or settings.game.client_type or DEFAULT_CLIENT_TYPE
```

选这一层而非更靠后（例如在 `PipelineRunner` 下发 `APPEND_TASK` 时注入）的理由有三：

**落库的参数就是最终执行的参数。** 流水线记录里 `client_type` 有确定值，事后排查"这次是用哪个渠道跑的"不需要再去翻当时的全局配置是什么。如果在下发时才注入，历史记录里会是 `null`，而全局配置可能早就改了。

**前端能在提交前看到实际生效值。** 创建流水线的表单里渠道字段显示当前全局默认值作为预填，用户明确知道会用哪个渠道，而不是提交一个"空"然后祈祷。

**避免执行期的意外变化。** 一条流水线在队列里等了半小时，期间用户改了全局默认渠道——若在下发时注入，这条流水线会用新渠道执行，与用户提交时的预期不符。提交时固化避免了这种时序问题。

代价是全局配置变更不会影响已在队列中的流水线。这是**有意的**，且前端会在设置页改渠道时提示"队列中已有 N 条流水线使用旧渠道，是否一并更新"，把选择权交给用户。

### 9.3 哪些任务类型有 client_type

从 `task.py` 的任务定义看，三个任务类型有这个参数，且用途各不相同：

| 任务 | 参数说明 |
|---|---|
| `StartUp`（开始唤醒） | 客户端版本，可选，默认为空。决定启动哪个包 |
| `CloseDown`（关闭游戏） | 客户端版本，**必选，填空则不执行** |
| `Fight`（刷理智） | 客户端版本，可选，默认为空。**用于游戏崩溃时重启并连回去继续刷，若为空则不启用该功能** |

三者对空值的处理完全不同，注入策略也应当区分：

- `CloseDown` 空值直接导致任务不执行——这是静默失败，用户会困惑于"为什么游戏没关"。必须注入
- `Fight` 空值只是不启用崩溃恢复，任务本身正常跑。注入后能白捡一个崩溃自愈能力，应当注入
- `StartUp` 空值时 MaaCore 的行为是不主动指定包名（依赖当前前台应用或默认行为）。注入后行为更确定，应当注入

结论是三者都注入，但 `CloseDown` 的注入是**功能正确性要求**而非优化。这一点要在代码注释里写明，避免后来者觉得"反正有默认值"而把注入逻辑简化掉。

其余任务类型（`Recruit`、`Infrast`、`Mall`、`Award`、`Roguelike`、`Reclamation`）没有 `client_type` 参数，不做注入。注意 `Fight` 与 `Recruit` 有一个**不同的** `server` 参数（可选值 `CN` / `US` / `JP` / `KR`），它影响的是掉落识别与数据上报的服务器归属，与 `client_type` 是两个维度——官服和 B 服都是 `server: "CN"`。不要把两者混为一谈。

### 9.4 参数 schema 的显式化

现有 `request.py` 是一个巨型扁平的 `TaskRequest`，按 [02-系统架构设计 §9](./02-系统架构设计.md) 会废弃。新的按任务类型分离的 Pydantic 模型里，`client_type` 定义为：

```python
ClientType = Literal["Official", "Bilibili", "txwy",
                     "YoStarEN", "YoStarJP", "YoStarKR"]

class StartUpParams(BaseModel):
    enable: bool | None = None
    client_type: ClientType | None = Field(
        default=None,
        description="客户端版本。留空时自动套用全局默认渠道（当前为 Bilibili）",
    )
    start_game_enabled: bool | None = None
    account_name: str | None = Field(
        default=None,
        description="切换账号，留空不切换。子串匹配，需在已登录账号中唯一。"
                    "官服为手机号掩码（如 123****4567），B 服为用户名（如 张三）",
    )
```

`description` 会出现在 OpenAPI 文档、前端自研调试面板与 agent 的 tool schema 里（见 [10-开放API与调试台](./10-开放API与调试台.md) 与 [11-Agent模块设计](./11-Agent模块设计.md)），所以写清楚是有实际收益的——agent 完全依赖这些描述来决定传什么值。

## 10. AsstSetInstanceOption 的 ClientType 选项

### 10.1 头文件怎么说

`resource/lib/maa/Linux/AsstCaller.h` 在 `AsstSetInstanceOption` 声明上方有一段注释：

```c
// 设置实例级参数。
// InstanceOptionKey::ClientType 仅在所选连接配置的 connect 阶段命令依赖 [PackageName] 时需要预先设置；
// 当前内置配置中仅 Androws / WSA 的 displayId 查询依赖该值。
AsstBool ASSTAPI
    AsstSetInstanceOption(AsstHandle handle, AsstInstanceOptionKey key, const char* value);
```

现有 `InstanceOptionType` 枚举（`maa_api/model/util/utils.py`）只有四项，缺 `ClientType`：

```python
class InstanceOptionType(IntEnum):
    touch_type = 2
    deployment_with_pause = 3
    adblite_enabled = 4
    kill_on_adb_exit = 5
```

**`ClientType` 的枚举值是 `6`。** `AsstCaller.h` 本身只把 `AsstInstanceOptionKey` 定义为 `int32_t` 的 typedef，不含具体数值；数值定义在 MaaCore 的内部头文件 `src/MaaCore/Common/AsstTypes.h` 中：

```cpp
enum class InstanceOptionKey
{
    Invalid = 0,
    /* Deprecated */         // MinitouchEnabled = 1,
    TouchMode = 2,
    DeploymentWithPause = 3,
    AdbLiteEnabled = 4,
    KillAdbOnExit = 5,
    ClientType = 6,          // 客户端类型（游戏渠道）。仅当连接配置在 connect 阶段需要 [PackageName] 时使用，
                             // 当前内置配置为 Androws / WSA；不替代 StartUpTask 的 client_type 参数。
};
```

注意 `1` 是已废弃的 `MinitouchEnabled` 留下的空位，不可复用——按值的分布猜测 `ClientType = 1` 是错的。

**核实过程中发现一个需要留意的上游分支差异。** MAA 的 `dev` 分支上 `AsstTypes.h` **没有** `ClientType` 成员，其 `AsstCaller.h` 也没有上面那段注释；`ClientType = 6` 与该注释同时存在于 `dev-v2` 分支线上。我们本地 v6.17.5 随库分发的 `AsstCaller.h` 带有这段注释，因此**本地内核属于 `dev-v2` 构建线，`ClientType = 6` 对它成立**。

这个差异的实际影响是：将来若切换到 `dev` 线的构建，`6` 会变成一个未定义的 key。MaaCore 的 `set_instance_option` 对未知 key 的处理是走 `default` 分支、记一条 `Unknown key or value` 错误日志并返回 `false`——不会崩溃，但会静默失效。因此设置该选项时必须检查返回值，失败只记 `WARNING` 而不中断连接流程。

### 10.2 实际作用范围

注释说的是"连接配置的 **connect 阶段**命令依赖 `[PackageName]` 时"。对 `resource/config.json` 做全文检索，`[PackageName]` 占位符出现在五处：

| 位置 | 命令 | 是否 connect 阶段 |
|---|---|---|
| `General.start` | `am start -n [PackageName]/com.u8.sdk.U8UnityContext` | 否 |
| `General.stop` | `am force-stop [PackageName]` | 否 |
| `Androws.start` | `am start --windowingMode 4 -n [PackageName]/...` | 否 |
| `Androws.displayId` | `dumpsys activity activities \| awk '... packageName=[PackageName] ...'` | **是** |
| `WSA.displayId` | `dumpsys display \| grep mUniqueId=virtual:...:[PackageName]` | **是** |

这与头文件注释完全吻合：`displayId` 查询发生在建立连接的过程中（要先知道往哪个显示屏发指令），此时内核还没有任何任务，无从得知渠道，所以必须靠实例级选项预先告知。而 `start` / `stop` 是任务执行期间的命令，那时内核可以从任务参数的 `client_type` 解析出包名。

**结论：本项目不需要设置这个选项。** 我们使用的连接配置是 `General`（默认值），macOS 下可能用到 `CompatMac`。后者的继承链是 `CompatMac → Compatible → General`，逐层展开后三者都**没有** `displayId` 项——`CompatMac` 只覆盖了 `ncAddress` 与 `screencapRawWithGzip`，`Compatible` 只覆盖了 `uuid`。整条链的 connect 阶段命令都不依赖 `[PackageName]`。

只有当用户把连接配置切到 `Androws`（Windows Subsystem for Android 的一种）或 `WSA` 时才需要。这两个配置面向的是 Windows 上的 Android 子系统，与本项目的部署形态（macOS / Linux 连模拟器或真机）无关。

### 10.3 那么渠道是怎么生效的

渠道的实际生效途径是**各任务的 `client_type` 参数**，不是实例级选项。`StartUp` 任务拿到 `client_type: "Bilibili"` 后，内核查 `config.json` 的 `packageName` 字典得到 `com.hypergryph.arknights.bilibili`，代入 `General.start` 命令模板执行。

这也解释了为什么现有代码从没设置过 `ClientType` 却能正常工作——它压根不在关键路径上。

### 10.4 处置

**枚举补全，但不在连接流程里默认设置。**

补全的理由是完整性：`core/enums.py` 应当忠实反映内核的 API 表面，缺项会让后来者误以为内核不支持。同时留一个条件分支，当用户选择 `Androws` / `WSA` 连接配置时自动设置该选项——虽然当前部署形态用不到，但实现成本只是几行。

```python
class InstanceOptionKey(IntEnum):
    Invalid = 0
    # 1 为已废弃的 MinitouchEnabled，保留空位
    TouchMode = 2
    DeploymentWithPause = 3
    AdbLiteEnabled = 4
    KillAdbOnExit = 5
    ClientType = 6


# 在子进程的启动序列里，应用实例级选项时
PACKAGE_NAME_CONFIGS = ("Androws", "WSA")

if boot_config.get("connection_config") in PACKAGE_NAME_CONFIGS:
    ok = asst.set_instance_option(InstanceOptionKey.ClientType,
                                  boot_config["client_type"])
    if not ok:
        logger.warning("内核不支持 ClientType 实例选项（可能为 dev 线构建），"
                       "Androws / WSA 的 displayId 查询可能失败")
```

必须检查返回值。按 §10.1 提到的分支差异，`dev` 线构建不认识 key `6`，此时 `set_instance_option` 返回 `false` 并在 `asst.log` 里留一条 `Unknown key or value`。记 `WARNING` 而不抛异常——这个选项失效只影响 Androws / WSA 这两个我们不用的配置。

不默认设置（即不对 `General` / `CompatMac` 也设一遍）的理由是收益为零而噪音为正：内核会在日志里记录每次 `set_instance_option` 调用，设一个不产生任何效果的选项只是给排查添乱。

其余三个已在用的选项保持现状：`touch_type = "maatouch"`、`deployment_with_pause = "1"`（见 `asst_manager.py`）。`adblite_enabled` 与 `kill_on_adb_exit` 未使用，在设置页暴露为高级选项。

## 11. 渠道切换的联动影响

切换渠道改变的是包名，而包名渗透到了很多地方。全部受影响的点：

**游戏版本检测**（[07-热更新方案 §4.1](./07-热更新方案.md)）。`dumpsys package <包名>` 的目标包名变了，已安装版本与安装时间要重新查询。切换后前端版本面板必须立即刷新，否则会显示另一个渠道的版本号——这是会误导用户做出错误更新决策的。

**APK 下载源**。官服走直链，B 服走 biligame 接口。切换后"最新版本"的查询逻辑、可用性、展示形态（官服是降级形态）全部不同。已经下载到 `resource/temp/` 的另一渠道 APK **不能复用**，且应当在切换时提示用户是否清理（1.8 GB 不小）。

**游戏启动与关闭**。`StartUp` / `CloseDown` 任务的 `client_type` 参数、以及 `DeviceManager.force_stop()` 用的包名都要跟随。特别注意 §9.3 提到的 `CloseDown` 空值不执行的行为。

**队列中已有的流水线**。按 §9.2 的设计，它们的 `client_type` 在提交时已固化，不受切换影响。前端在切换时提示并提供批量更新选项。

**定时任务配置**。定时任务的流水线模板存在数据库里，其中的 `client_type` 如果是显式值则不受影响，如果当初是留空由全局注入的，则下次触发时会用新渠道。这个行为是合理的，但要在设置页说明，避免用户切换渠道后对定时任务的行为感到意外。

**两个渠道的游戏可以共存**。包名不同，Android 视其为两个独立应用，可以同时安装。因此切换渠道**不需要卸载重装**，前提是目标渠道的游戏已经装过。切换后若检测到目标包名未安装，前端应明确提示"当前设备未安装 B 服客户端"并提供安装入口，而不是让后续任务莫名其妙地失败。

**账号数据不互通**。官服与 B 服是不同的服务器，账号、进度完全独立。切换渠道不是"换个皮肤"而是"换个游戏"。界面上的渠道切换入口要有足够的视觉分量，不能像切换主题那样轻描淡写。

**MaaCore 的资源**。国服两个渠道共用同一份资源（`resource/tasks/` 与 OTA 的 `tasks.json` 都是国服内容），切换渠道**不需要重新加载资源**。这与外服不同——切到外服需要加载 `resource/global/<服务器>/` 下的增量资源。

这一点有上游实现佐证：MAA 桌面客户端在渠道变更时会调用一次 `LoadResource()`，但其 `NeedRestartAfterClientTypeChange()` 判定函数里对官服与 B 服之间的互切显式返回 `false`，注释写明"官服 <-> B服 之间切换不需要重启"。我们的结论与之一致。由于本项目只支持国服两渠道，这条不构成实际约束，但 [07-热更新方案 §3.3](./07-热更新方案.md) 的资源清单里保留了 `client_types` 字段为将来留口。

切换动作本身经 `SettingService` 执行，流程为：校验目标渠道合法 → 落库 → 广播 `device_status` 与版本信息失效事件 → 前端刷新版本面板。**不需要重连设备，也不需要重启子进程。**

## 12. 待确认的开放问题

**`AsyncCallInfo` 回调的载荷结构。** §5.2 的两级 Future 设计依赖从回调中取出 `async_call_id` 与调用结果，但本地 `asst.log` 样本里没有该回调的实例（现有代码走的是同步 `AsstConnect`），字段名与成功标志的表示方式未经实测确证。实现前需要写一个最小脚本调用一次 `AsstAsyncConnect` 并打印完整回调 JSON。在确证之前，解析器按 §5.2 的方案写成宽容形式并保留 `AsstConnected` 轮询作为回退。

**本地内核属于哪条上游分支线。** §10.1 的核实发现 `ClientType = 6` 只存在于 `dev-v2` 线，`dev` 线没有这个枚举成员。本地 v6.17.5 随库分发的头文件带有对应注释，据此判断本地是 `dev-v2` 构建。这个判断值得确认，因为它可能还影响其他 API 的可用性（例如 [03-MaaCore内核层设计 §1.2](./03-MaaCore内核层设计.md) 记录的 macOS / Linux 之间导出符号不一致，也可能与分支线有关）。确认方式是在真机上实际调用一次 `set_instance_option(6, "Bilibili")` 看返回值。

**`ScreencapFailed` 的升级阈值。** §4.3 定为连续 3 次。这个值是凭经验取的，实际合适与否取决于模拟器的稳定性。建议上线后统计 `ScreencapFailed` 的实际分布（偶发单次 vs 连续多次）再校准。

**`screenshot_quality` 默认值的调整。** §8.2 建议从 25 上调到 60 并配合 1280 px 尺寸限制。这个组合的实际体积与观感需要在真实设备上验证后再定，尤其是要确认 1280 px 下 agent 的多模态模型仍能准确识别界面元素。

**定时任务在设备不可用时的延后上限。** §4.2 定为最多 6 次、每次 5 分钟（合计 30 分钟）。这个上限对"用户睡前设了定时任务但忘了开模拟器"的场景是否够用，需要根据实际使用反馈调整。

**adbutils 与 MaaCore 的 adb 二进制一致性校验的具体实现。** §6.3 提出要强制两者用同一个二进制，但 adbutils 选择 adb 路径的逻辑（环境变量、内置 fallback）需要确认其当前版本的实际行为，才能确定用哪种方式强制指定最可靠。
