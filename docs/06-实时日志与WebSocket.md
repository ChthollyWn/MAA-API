> 本文档定义三路日志的采集、汇聚、持久化与实时下发，以及 WebSocket 的完整协议。决策依据见 [README 决策速查表](./README.md#决策速查表)，架构位置见 [02-系统架构设计](./02-系统架构设计.md)。

# 实时日志与 WebSocket

## 1. 目标与现状差距

当前项目的日志能力分散在三个互不相通的地方：`log.py` 的 `RotatingFileHandler` 把 Python 日志写进 `resource/log/{date}.log`；`callback_handler.py` 把 MaaCore 回调翻译成中文塞进 `TaskPipeline.logs` 这个内存列表；MaaCore 自己往 `debug/asst.log` 写 native 层调试日志，没有任何人读它。前端只能靠轮询拿到内存里那个列表，服务重启即全部丢失。

重构后引入 `LogHub` 作为唯一汇聚点。三路日志经统一的 `LogRecord` 结构进入 `LogHub`，由它同时完成内存环形缓冲、批量落库与 WebSocket 广播。

```
  ① MaaCore 回调任务日志  ─┐
     (子进程 CALLBACK 事件) │
                            │      ┌──────────────┐    环形缓冲（内存，最近 N 条）
  ② Python 服务日志       ─┼─────►│   LogHub     ├──► 批量落库（SQLite）
     (logging.Handler)      │      └──────────────┘    WebSocket 广播
                            │
  ③ MaaCore debug 日志    ─┘
     (tail asst.log)
```

## 2. 统一的日志记录结构

三路日志归一到同一个结构，`source` 字段决定前端落在哪个标签页。

```python
@dataclass
class LogRecord:
    id: int                 # 自增，全局单调，前端断线重连补发的游标
    ts: float               # Unix 时间戳（秒，带小数）
    source: str             # "task" | "service" | "core"
    level: str              # "DEBUG" | "INFO" | "WARNING" | "ERROR"
    content: str            # 已翻译好的展示文案
    pipeline_id: str | None # 关联的流水线，无关联为 None
    task_id: str | None     # 关联的任务
    request_id: str | None  # HTTP 请求日志的 X-Request-Id；其他日志为 None
    logger: str | None      # service 源的 logger 名，如 "uvicorn.access"
    raw: dict | None        # 原始载荷，task 源存 MaaCore 回调原文，便于排查
    attachment: dict | None # 截图等附件引用，见 §8
```

`id` 由 `LogHub` 在内存中自增分配，不依赖数据库主键。这样即使落库是异步批量的，WebSocket 推送也能立刻带上稳定的 `id`，客户端据此做断线补发。服务重启后 `id` 从数据库当前最大值 + 1 继续。

三个 `source` 的取值含义：

| source | 来源 | 典型内容 |
|---|---|---|
| `task` | MaaCore 回调翻译 | 开始任务 [刷理智]、当前理智：120/135 |
| `service` | Python logging | 请求日志、异常栈、服务状态变更 |
| `core` | `asst.log` tail | native 层的 TRC/WRN/ERR 明细 |

`request_id` 只标记 HTTP 请求日志，用于把调试台发出的请求与服务端处理日志对应起来；它不跨越请求生命周期传播到异步作业。响应体含 `pipeline_id` 时，调试台可以继续按该字段筛选后续流水线日志；`confirmation_id` 与 `update_id` 不做类似关联。

## 3. 第一路：MaaCore 回调任务日志

### 3.1 采集链路

按 [02-系统架构设计 §3.2](./02-系统架构设计.md) 的 IPC 约定，子进程只做 `json.loads` 与 `Message(msg)` 枚举转换，把原始回调封成 `CALLBACK` 事件投递给主进程，**不做任何文案翻译**。主进程的 `CoreClient` 消费事件后分派给 `LogHub` 的 `CallbackTranslator`。

这样划分的收益是文案可以随时改而不必重启子进程，而且回调线程里不做字符串拼接与业务判断——现有 `callback_handler.py` 在 MaaCore 的回调线程里更新流水线状态甚至 `raise ResponseException`，异常穿过 C 调用边界是未定义行为。

```python
class CallbackTranslator:
    """把 MaaCore 原始回调翻译成 LogRecord，运行在主进程事件循环里"""

    def translate(self, msg: Message, details: dict) -> list[LogRecord]:
        handler = self._dispatch.get(msg)
        if handler is None:
            return []
        return handler(details)
```

分派表以 `Message` 枚举为键。`Message` 的完整取值（见 `maa_api/model/util/utils.py`，重构后迁至 `core/enums.py`）：

| 枚举值 | 数值 | 当前是否处理 |
|---|---|---|
| `InternalError` | 0 | 否，需新增 |
| `InitFailed` | 1 | 否，需新增 |
| `ConnectionInfo` | 2 | 是 |
| `AllTasksCompleted` | 3 | 否，需新增 |
| `AsyncCallInfo` | 4 | 否，需新增 |
| `Destroyed` | 5 | 否，需新增 |
| `TaskChainError` | 10000 | 是 |
| `TaskChainStart` | 10001 | 是 |
| `TaskChainCompleted` | 10002 | 是 |
| `TaskChainExtraInfo` | 10003 | 否，需新增 |
| `TaskChainStopped` | 10004 | 否，需新增 |
| `SubTaskError` | 20000 | 否，需新增 |
| `SubTaskStart` | 20001 | 是 |
| `SubTaskCompleted` | 20002 | 否 |
| `SubTaskExtraInfo` | 20003 | 是 |
| `SubTaskStopped` | 20004 | 否 |

### 3.2 连接信息映射表（`ConnectionInfo`）

以 `details.what` 为键。这是从 `callback_handler.py` 的 `connection_map` 完整迁移的 12 条，文案逐字保留。表中 `{details}` 指回调里的 `details.details` 子对象。

| `what` | 日志文案 |
|---|---|
| `ConnectFaild` | 模拟器连接失败 `{details}` |
| `Connected` | 模拟器连接成功 |
| `UuidGot` | 已获取到设备唯一码 `{uuid}` |
| `UnsupportedResolution` | 模拟器分辨率不被支持 `{details}` |
| `ResolutionError` | 分辨率获取错误 `{details}` |
| `ResolutionGot` | 已获取到模拟器分辨率 `{details.height}*{details.width}` |
| `Reconnecting` | 模拟器连接断开(adb/模拟器异常) 正在重连 `{details}` |
| `Reconnected` | 模拟器连接断开(adb/模拟器异常) 重连成功 `{details}` |
| `Disconnect` | 模拟器连接断开(adb/模拟器异常) 重连失败 `{details}` |
| `ScreencapFailed` | 截图失败(adb/模拟器异常) `{details}` |
| `FastestWayToScreencap` | 最快截图耗时 `{details.cost}ms` |
| `TouchModeNotAvailable` | 不支持的触控模式 `{details}` |

**这张表里有一个必须修掉的键名错误。** 对本地 `resource/lib/maa/Darwin/debug/asst.log` 做统计，MaaCore v6.17.5 实际发出的 `what` 值是 `ConnectFailed`，而现有代码写的是 `ConnectFaild`（少一个 `e`）。实测样本里 `ConnectFailed` 出现 68 次，也就是说整张表里最关键的那条"连接失败"日志**从未匹配成功过**——这正是连接问题难以排查的原因之一。

新实现保留 `ConnectFaild` 作为兼容别名，同时以 `ConnectFailed` 为准键，两者指向同一文案。这样无论内核哪个版本都能命中。

**还需要补一个 v6.17.5 新增的 `what`。** 同一份 `asst.log` 里 `ResolutionInfo` 与 `ResolutionGot` 各出现 7 次，成对发出，但 `ResolutionInfo` 不在现有表里。它的 `details` 形如 `{"height": 1440, "width": 2560}`，外层还带一个 `why` 字段（实测值 `"Normal"`）。建议映射为：

| `what` | 日志文案 |
|---|---|
| `ResolutionInfo` | 分辨率信息 `{details.height}*{details.width}`（`{why}`） |

考虑到它与 `ResolutionGot` 内容重复，默认级别设为 `DEBUG`，避免前端出现两条几乎一样的行。

`ConnectFailed` / `Disconnect` / `ScreencapFailed` 这几条除了产出日志，还要驱动设备状态机，钩子挂接方式见 [08-ADB连接与账号渠道](./08-ADB连接与账号渠道.md)。

### 3.3 子任务开始映射表（`SubTaskStart`）

以 `details.details.task` 为键，从 `sub_task_info` 完整迁移的 22 条。`{exec_times}` 取自 `details.details.exec_times`。

| `task` | 日志文案 |
|---|---|
| `StartButton2` | 已开始战斗 `{exec_times}` 次 |
| `MedicineConfirm` | 使用理智药 |
| `ExpiringMedicineConfirm` | 使用 48 小时内过期的理智药 |
| `StoneConfirm` | 碎石 |
| `RecruitRefreshConfirm` | 刷新标签 |
| `RecruitConfirm` | 确认招募 |
| `RecruitNowConfirm` | 使用加急许可 |
| `ReportToPenguinStats` | 汇报到企鹅数据统计 |
| `ReportToYituliu` | 汇报到一图流大数据 |
| `InfrastDormDoubleConfirmButton` | 请进行基建宿舍的二次确认 |
| `StartExplore` | 已开始探索 `{exec_times}` 次 |
| `StageTraderInvestConfirm` | 已投资源石锭 |
| `StageTraderInvestSystemFull` | 投资达到了游戏上限 |
| `ExitThenAbandon` | 已放弃本次探索 |
| `MissionCompletedFlag` | 战斗完成 |
| `MissionFailedFlag` | 战斗失败 |
| `MissionFailedFlag2` | 战斗失败 |
| `StageTraderEnter` | 节点：诡异行商 |
| `StageSafeHouseEnter` | 节点：安全的角落 |
| `StageCombatDpsEnter` | 关卡：普通作战 |
| `StageEmergencyDps` | 关卡：紧急作战 |
| `StageDreadfulFoe` | 关卡：险路恶敌 |

这张表里有几项同时是**高风险消耗动作**的实锤信号：`MedicineConfirm`、`ExpiringMedicineConfirm`、`StoneConfirm`、`RecruitNowConfirm`、`StageTraderInvestConfirm`。它们对应 [README 决策速查表](./README.md#决策速查表)里"需人工确认"的消耗类操作。人工确认发生在**任务提交前**（由 `PolicyEngine` 依据参数判定，见 [11-Agent模块设计](./11-Agent模块设计.md)），这些回调是**事后审计证据**——记录实际发生了几次消耗，写进审计表供回溯。两者不可互换：回调到达时消耗已经发生，拦不住了。

### 3.4 子任务额外信息映射表（`SubTaskExtraInfo`）

以 `details.what` 为键，从 `extra_info_map` 完整迁移的 10 条。`{details}` 指 `details.details`。

| `what` | 日志文案 |
|---|---|
| `RecruitTagsDetected` | 公招识别结果：`{details.tags}` |
| `ReCruitSpecialTag` | 识别到特殊Tag：`{details.tag}` |
| `RecruitResult` | `{details.level}` ⭐ Tags |
| `RecruitTagsRefreshed` | 已刷新Tags |
| `EnterFacility` | 当前设施：`{details.facility}` `{details.index}` |
| `StageInfo` | 开始战斗：`{details.name}` |
| `StageInfoError` | 关卡识别错误 |
| `RoguelikeEvent` | 事件：`{details.name}` |
| `SanityBeforeStage` | 当前理智：`{details.current_sanity}`/`{details.max_sanity}` |
| `StageDrops` | `{details.stars}`⭐通关`{details.stage.stageCode}` <br>掉落统计: <br>`{掉落明细}` |

`StageDrops` 的掉落明细由 `details.stats` 数组逐项拼成，每行格式为 `{itemName}: {quantity}(+{addQuantity})`，行间以换行符连接。迁移时保留这个逻辑：

```python
def _format_drops(stats: list[dict]) -> str:
    return "\n".join(
        f"{it.get('itemName', '')}: {it.get('quantity', '')}(+{it.get('addQuantity', '')})"
        for it in stats
    )
```

`StageDrops` 与 `SanityBeforeStage` 除了产日志，还应把结构化数据落进独立的统计表（掉落记录、理智曲线），供 agent 的"读历史数据生成报告"场景使用。日志表只存展示文案，不适合做聚合查询。表结构见 [04-数据模型与持久化](./04-数据模型与持久化.md)。

### 3.5 新增回调类型的处理

现有代码只处理了 5 类回调，剩下的要么静默丢弃要么根本没进分派。以下是新增部分的处理约定。

**`TaskChainStopped`（10004）** 表示任务链被主动中止，与 `TaskChainError` 语义不同：前者是预期内的停止（用户点了停止、`AsstStop` 被调用），后者是执行出错。现有代码把二者混在一起会导致"用户主动停止"被记成失败并触发重试。新实现下它把任务置 `CANCELLED` 且**不进重试**，日志文案 `任务已中止 [{task_name}]`，级别 `WARNING`。

**`AllTasksCompleted`（3）** 表示整条任务链跑完，是流水线终态的校对信号而非判定依据。逐任务的终态仍以各自的 `TaskChainCompleted` / `TaskChainError` / `TaskChainStopped` 为准（见 [03-MaaCore内核层设计 §6](./03-MaaCore内核层设计.md)）。若收到 `AllTasksCompleted` 时仍有任务停留在 `RUNNING`，说明我们的映射与内核不同步，记一条 `ERROR` 日志并强制收敛状态。文案 `全部任务已完成`。

**`AsyncCallInfo`（4）** 是 `AsstAsyncConnect` / `AsstAsyncClick` / `AsstAsyncScreencap` 这批异步调用的结果回执，载荷里带 `async_call_id` 与调用结果。它不是给人看的日志，而是**命令完成信号**：`CoreClient` 需要按 `async_call_id` 唤醒等待中的 Future。因此它在子进程侧就要被识别出来，除了照常转发 `CALLBACK` 事件外，还要让主进程的分派器优先交给 `CoreClient` 的异步调用表，再顺带产一条 `DEBUG` 级日志。具体到连接场景的用法见 [08-ADB连接与账号渠道](./08-ADB连接与账号渠道.md)。

**`InternalError`（0）/ `InitFailed`（1）** 是内核级故障。两者都产 `ERROR` 日志，并额外触发 `core_status` 的 WebSocket 广播与通知推送。`InitFailed` 通常意味着资源损坏，`CoreSupervisor` 应据此直接转 `FAILED` 而非无脑重启——坏资源重启多少次都是坏的。

**`Destroyed`（5）** 是实例销毁回执，只在子进程关闭流程里出现，记 `DEBUG` 日志。

**`SubTaskError`（20000）** 单个子任务出错，但任务链可能会自行重试并继续。它不改变任务状态，只产 `WARNING` 日志，文案取 `details.details.task` 原文（无中文映射时直接展示英文 task 名）。这一条的价值在于排查"任务最终成功了但中间反复出错"的情况。

**`TaskChainExtraInfo`（10003）/ `SubTaskCompleted`（20002）/ `SubTaskStopped`（20004）** 首版只记 `DEBUG` 日志、保留 `raw` 原文，不做文案翻译。等实际使用中发现有价值的字段再逐步补映射。

**未知 `what` 的兜底。** 三张映射表都是白名单，未命中的 `what` 现在是静默丢弃。新实现改为产一条 `DEBUG` 级日志，内容为 `未映射回调 {msg_name}.{what}`，并完整保留 `raw`。这样 MAA 升级新增回调时能被发现，而不是悄无声息地丢掉。

## 4. 第二路：Python 服务日志

### 4.1 自定义 Handler

保留 `log.py` 现有的控制台与文件 handler（`{date}.log` 与 `{date}.error.log` 两个 `RotatingFileHandler`），在其基础上**追加**一个 `LogHubHandler`。文件日志是排障的最后防线，不能因为引入数据库就砍掉。

```python
class LogHubHandler(logging.Handler):
    """把 logging 记录送进 LogHub。绝不阻塞调用方。"""

    def __init__(self, hub: "LogHub"):
        super().__init__()
        self.hub = hub

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.hub.offer(LogRecord(
                ts=record.created,
                source="service",
                level=record.levelname,
                content=self.format(record),
                logger=record.name,
                pipeline_id=getattr(record, "pipeline_id", None),
            ))
        except Exception:
            self.handleError(record)   # 日志系统自身出错绝不能抛给业务
```

两个约束必须守住。**第一，`emit` 不能阻塞。** `LogHub.offer()` 只做入队，队列满时按 §6 的背压策略丢弃而非等待。日志 handler 里做阻塞 IO 会让整个服务的响应时间受日志系统拖累。**第二，绝不能在 `LogHub` 内部或其调用链上再打日志**，否则形成无限递归。`LogHub` 自身的诊断信息直接 `print` 到 stderr，或走一个显式排除了 `LogHubHandler` 的专用 logger。

### 4.2 接住 uvicorn 的日志

`log.py` 现在已经手工给 `uvicorn.access` 与 `uvicorn.error` 加了 handler，这个思路保留但要改进。uvicorn 在启动时会按自己的 `LOGGING_CONFIG` 重新配置这两个 logger，如果我们的 handler 挂载发生在 uvicorn 配置之前，可能被覆盖。稳妥做法是在 FastAPI 的 `lifespan` 启动阶段（此时 uvicorn 已完成日志配置）再挂载：

```python
_TARGET_LOGGERS = ("maa_api", "uvicorn", "uvicorn.access", "uvicorn.error")

def attach_log_hub(hub: LogHub) -> None:
    handler = LogHubHandler(hub)
    handler.setFormatter(logging.Formatter("%(message)s"))
    for name in _TARGET_LOGGERS:
        lg = logging.getLogger(name)
        if not any(isinstance(h, LogHubHandler) for h in lg.handlers):
            lg.addHandler(handler)
```

`uvicorn.access` 的量在前端轮询或 agent 频繁调用时会很大，而且内容对使用者价值低。默认把它的入库级别设为 `WARNING`（即正常 2xx 访问日志只进内存环形缓冲供实时查看，不落库），可在设置页调整。

### 4.3 子进程的 Python 日志

子进程里的 `logger` 输出通过 IPC 的 `LOG` 事件回传（见 [02-系统架构设计 §3.2](./02-系统架构设计.md) 与 [03-MaaCore内核层设计 §3.1](./03-MaaCore内核层设计.md) 的 `_install_log_bridge`）。主进程收到后按 `source="service"` 归一，`logger` 字段加 `core_worker.` 前缀以便区分来源进程。

子进程侧的桥接同样是一个 `logging.Handler`，但它写的是 `event_queue` 而非 `LogHub`：

```python
class QueueLogHandler(logging.Handler):
    def emit(self, record):
        try:
            self.queue.put_nowait({
                "type": "LOG",
                "ts": record.created,
                "payload": {"level": record.levelname,
                            "content": self.format(record),
                            "logger": record.name},
            })
        except Exception:
            pass   # 队列满时直接丢，不能因为日志把子进程卡死
```

用 `put_nowait` 而不是 `put`：`multiprocessing.Queue` 满时 `put` 会阻塞，而子进程的命令循环是单线程的，一旦卡在日志写入上，整个内核就失去响应，心跳超时后会被 `CoreSupervisor` 误判为崩溃。丢日志远比丢内核可接受。

## 5. 第三路：MaaCore 自身的 debug 日志

### 5.1 文件位置与格式

MaaCore 通过 `AsstSetUserDir` 指定的目录写日志，路径固定为 `<user_dir>/debug/asst.log`。本地已存在 `resource/lib/maa/Darwin/debug/asst.log`。

实测格式为单行结构化文本：

```
[2026-09-16 10:25:11.367][TRC][Px26316][Tx65169] MaaCore Process Start
[2026-09-16 11:13:40.919][INF][Px3213][Tx2098] The fastest way is RawByNc , cost: 490 ms
```

四个方括号段依次是毫秒级时间戳、级别、进程号（`Px` 前缀）、线程号（`Tx` 前缀），其后为消息正文。级别全集为 `DBG` / `TRC` / `INF` / `WRN` / `ERR`（依据 MaaCore 的 `Utils/Logger.hpp` 中 `Logger::level` 的五个定义）。解析正则：

```python
LINE_RE = re.compile(
    r"^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\]"
    r"\[(?P<level>DBG|TRC|INF|WRN|ERR)\]"
    r"\[Px(?P<pid>\d+)\]\[Tx(?P<tid>\d+)\] ?(?P<msg>.*)$"
)

LEVEL_MAP = {"DBG": "DEBUG", "TRC": "DEBUG", "INF": "INFO",
             "WRN": "WARNING", "ERR": "ERROR"}
```

`TRC` 映射到 `DEBUG` 而非单独一级，是为了和 Python 的级别体系对齐，前端过滤器不必为 native 日志单独开一档。

不匹配正则的行按**续行**处理，追加到上一条记录的 `content`——MaaCore 偶尔会输出多行的异常栈或 JSON。若尚无上一条记录（例如从文件中段开始读），则整行作为 `INFO` 级记录原样收下。

### 5.2 为什么不用 watchdog

文件监听有现成的库（`watchdog`、`inotify`），但这里坚持用轮询 `seek`，理由有三条。

**跨平台行为不一致。** `watchdog` 在 macOS 走 FSEvents、Linux 走 inotify，两者的事件粒度、合并策略、对 `rename` 的表现都不同。本项目开发在 macOS、容器部署在 Linux（见 [03-MaaCore内核层设计 §1.2](./03-MaaCore内核层设计.md) 提到的平台差异），一个在两端行为不一致的依赖会带来难以复现的问题。

**收益为零。** 文件系统事件的价值是低延迟感知变更，但 MaaCore 的日志是高频连续写入——实测 48 分钟产出 76004 行、7.5 MB。这种写入模式下每 200 ms 轮询一次 `seek` 读取，延迟完全够用，而事件驱动反而会因为事件风暴带来额外开销。

**多一个依赖多一份风险。** 轮询 `seek` 的实现不到 60 行，没有任何外部依赖，行为完全可预测。

### 5.3 轮转处理

MaaCore 在日志超过 **64 MiB** 时执行轮转，方式是 `std::filesystem::rename(asst.log, asst.bak.log)` 后重新创建 `asst.log`（依据 `Utils/Logger.hpp` 的 `rotate()` 实现与 `MaxLogSize = 64LL * 1024 * 1024`）。`rename` 意味着旧文件的 inode 被挪到 `asst.bak.log`，新 `asst.log` 是一个**全新的 inode**。

因此 tail 的正确性依赖两个判据，二者缺一不可：

- **inode 变化**：`os.stat(path).st_ino` 与上次记录的不同 → 文件被轮转，重置偏移为 0
- **尺寸回退**：`st_size < last_offset` → 文件被截断（外部工具清空日志），重置偏移为 0

只看尺寸不够：轮转后的新文件可能在下次轮询前就写超过了旧偏移量，尺寸判据会漏掉。只看 inode 也不够：截断不改变 inode。

轮转发生时，旧文件在 `rename` 到读取之间可能还有未读完的尾部。实现上在检测到 inode 变化后，先把已持有的旧文件句柄读到 EOF，再切换到新文件——因为持有的是文件句柄而非路径，`rename` 不影响继续读取。

### 5.4 实现

```python
class AsstLogTailer:
    """增量读取 MaaCore 的 asst.log。轮询 + inode 检测，无外部依赖。"""

    def __init__(self, path: Path, hub: "LogHub",
                 interval: float = 0.2, min_level: str = "INF"):
        self.path = path
        self.hub = hub
        self.interval = interval
        self.min_level = min_level
        self._fp = None
        self._inode = None
        self._pending = None      # 上一条记录，用于续行合并

    async def run(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self._poll_once)
            except Exception as exc:
                print(f"[AsstLogTailer] {exc}", file=sys.stderr)
                self._close()
            await asyncio.sleep(self.interval)

    def _poll_once(self) -> None:
        if not self.path.exists():
            self._close()
            return

        st = self.path.stat()
        if self._fp is None:
            # 首次打开定位到文件末尾，不回放历史（历史走 §7 的查询接口）
            self._open(seek_to_end=True)
        elif st.st_ino != self._inode:
            self._drain()        # 把轮转前的旧句柄读干净
            self._open(seek_to_end=False)
        elif st.st_size < self._fp.tell():
            self._open(seek_to_end=False)   # 被截断

        self._drain()

    def _drain(self) -> None:
        if self._fp is None:
            return
        for line in self._fp:
            if not line.endswith("\n"):
                # 半行：回退偏移，等下次轮询读到完整行
                self._fp.seek(self._fp.tell() - len(line.encode("utf-8")))
                break
            self._consume(line.rstrip("\n"))
```

`_open()` 用 `errors="replace"` 打开，避免 native 层写出非 UTF-8 字节时整个 tailer 崩掉。

`min_level` 是必须的。实测样本里 `TRC` 占 62%、`WRN` 占 30%，`INF` 只占 5%——MaaCore 的 `WRN` 大量是识别未命中这类正常现象，并非真的警告。默认只采集 `INF` 及以上，并在设置页提供开关；排查问题时临时调到 `TRC`。过滤在 tailer 内完成，不进 `LogHub`，从源头削掉 90% 以上的量。

### 5.5 与子进程的关系

`asst.log` 由子进程里的 MaaCore 写入，但 tail 由**主进程**执行——它只是读一个普通文件，不需要触碰内核。这样子进程崩溃时，崩溃前最后写入的 native 日志仍能被采集到，正好是排查崩溃现场最需要的信息。

内核热更新导致子进程重启时，MaaCore 会重新初始化日志系统并追加到同一个 `asst.log`（`LoadFileStream` 后接 `log_init_info`，可从实测日志开头的 `MaaCore Process Start` 分隔块看出）。tailer 无需感知重启，继续读即可。

## 6. LogHub 设计

### 6.1 三个出口

`LogHub` 收到 `LogRecord` 后同时喂给三个下游，彼此独立、互不阻塞：

```python
class LogHub:
    def __init__(self, ring_size: int = 2000):
        self._ring: deque[LogRecord] = deque(maxlen=ring_size)
        self._db_queue: asyncio.Queue = asyncio.Queue(maxsize=10000)
        self._next_id = 1
        self._seq_lock = threading.Lock()

    def offer(self, record: LogRecord) -> None:
        """唯一入口。线程安全，绝不阻塞。"""
        with self._seq_lock:
            record.id = self._next_id
            self._next_id += 1

        self._ring.append(record)            # ① 环形缓冲，O(1)，自动淘汰
        self._broadcast_nowait(record)       # ② WS 广播，每连接独立队列
        self._enqueue_db(record)             # ③ 入库队列，满则按策略降级
```

`offer()` 会被多个线程调用（logging handler 在任意线程、tailer 在 `to_thread` 线程、IPC 消费线程），所以 id 分配要加锁。`deque(maxlen=N)` 的 `append` 本身是线程安全的。

**① 环形缓冲**保留最近 `ring_size` 条（默认 2000），用于前端首次连接时的回填与断线补发。它是纯内存的，占用有硬上限，2000 条按平均 200 字节算约 400 KB。

**② WebSocket 广播**不直接 `await send()`，而是往每个连接各自的发送队列里 `put_nowait`。一个慢客户端（移动端弱网）绝不能拖住整个日志系统，细节见 §6.3。

**③ 入库**走异步队列 + 批量写入。

### 6.2 批量落库

每条日志开一次事务在 SQLite 上是灾难性的——`INSERT` 加 fsync 单条毫秒级，日志洪峰时直接把事件循环压垮。改为攒批：

```python
async def _db_writer(self) -> None:
    """后台任务：攒批写入。满 batch_size 或超 flush_interval 即落盘。"""
    batch: list[LogRecord] = []
    while True:
        timeout = self.flush_interval if batch else None
        try:
            record = await asyncio.wait_for(self._db_queue.get(), timeout)
            batch.append(record)
        except asyncio.TimeoutError:
            pass    # 超时即触发 flush，保证低流量时日志也能及时落库

        if len(batch) >= self.batch_size or (batch and self._should_flush()):
            await self._flush(batch)
            batch = []
```

参数取 `batch_size = 200`、`flush_interval = 1.0` 秒。这意味着最坏情况下丢失最近 1 秒的日志（进程被 `SIGKILL` 时），可以接受；正常关闭流程里 `lifespan` 的关闭阶段会显式 flush 残留批次（见 [02-系统架构设计 §7](./02-系统架构设计.md) 的关闭顺序）。

SQLite 的配置配合：`journal_mode=WAL` 让读写不互斥（历史查询不会被写入阻塞），`synchronous=NORMAL` 在 WAL 下已足够安全且显著快于 `FULL`。具体设置见 [04-数据模型与持久化](./04-数据模型与持久化.md)。

### 6.3 背压策略

日志洪峰的现实来源有三个：MaaCore 的 `TRC` 级日志（实测每分钟约 1600 行）、内核崩溃重启风暴时的异常栈、agent 高频调用产生的 access log。三个下游各有各的背压方式。

**环形缓冲**天然有界，`deque(maxlen=N)` 自动淘汰最旧的，无需额外处理。

**入库队列**采用**分级丢弃**。队列使用率是唯一的判据：

| 队列使用率 | 行为 |
|---|---|
| < 70% | 全部入库 |
| 70% ~ 90% | 丢弃 `DEBUG`，其余入库 |
| 90% ~ 100% | 只保留 `WARNING` 及以上 |
| 满 | 丢弃当前记录，累加丢弃计数 |

丢弃不是静默的：每累计丢弃 1000 条，`LogHub` 产出一条自身的 `WARNING` 记录（走特殊路径直接进环形缓冲与广播，跳过入库队列以免加剧拥堵），内容为 `日志洪峰，已丢弃 N 条低级别日志`。前端据此提示用户。这条元日志本身要做频率限制，否则会变成新的洪峰。

**WebSocket 发送**采用**每连接独立队列 + 满则断开**。每个连接持有 `asyncio.Queue(maxsize=500)`，广播时 `put_nowait`；抛 `QueueFull` 说明该客户端消费不过来，直接关闭连接（close code `1011`）。客户端会自动重连并带上 `last_seen_id` 补拉遗漏部分（见 §7.4）——这比在服务端无限堆积内存健康得多。慢客户端的代价由它自己承担，不扩散到其他连接。

**数据库总量**通过定期清理控制。后台任务每天执行一次，按两条规则删除：保留期外的（默认 14 天）、以及超过总条数上限的（默认 200 万条，按 `id` 从小到大删）。清理后执行 `PRAGMA incremental_vacuum` 回收空间。保留期与上限可在设置页配置。

## 7. WebSocket 协议

### 7.1 端点与鉴权

端点为 `GET /api/ws`（升级为 WebSocket）。

**浏览器的 WebSocket API 无法设置自定义请求头。** `new WebSocket(url, protocols)` 只接受 URL 与子协议两个参数，没有 headers 参数——这是 W3C 规范的限制，不是某个浏览器的缺陷。因此 [README 决策速查表](./README.md#决策速查表)里约定的四种 token 传递方式，在 WebSocket 上只有两种可用：

| 方式 | WebSocket 可用性 |
|---|---|
| `Authorization: Bearer <token>` | 不可用，浏览器无法设置 header |
| `X-Token: <token>` | 不可用，同上 |
| `?token=<token>` | **可用**，推荐 |
| Cookie | **可用**，浏览器自动携带同源 cookie |

首选 query 参数：`ws://host:8002/api/ws?token=xxx&last_seen_id=1234`。实现简单，不依赖 cookie 的同源与 `SameSite` 策略，也适用于非浏览器客户端（agent、脚本）。调试台连接专用 WebSocket 时，必须从当前调试台的 Base URL 推导 scheme、host 与端口，再拼接 `/api/ws`，不能固定为页面同源地址。

代价是 token 会出现在 URL 里，可能被写进访问日志。缓解措施：`uvicorn.access` 的日志格式化时对 `token` 查询参数做脱敏（替换为 `token=***`），这个脱敏在 `LogHubHandler` 的 formatter 与文件 handler 上都要生效。

Cookie 作为备选，供前端在已通过 HTTP 登录接口设置 cookie 后使用，此时 URL 上不必带 token。服务端两种都接受，按 query → cookie 的顺序取第一个非空值。

鉴权失败时**不要**接受连接后再关闭——直接在握手阶段返回 HTTP 403。FastAPI 里的写法是在 `websocket.accept()` 之前调用 `websocket.close(code=1008)`，或在依赖项里抛 `WebSocketException`。

`access_token` 为空时（`config.yaml` 允许不配）跳过鉴权，与 HTTP 侧行为一致。

### 7.2 消息信封

双向消息统一信封，便于前端做单一分派：

```json
{
  "type": "log",
  "ts": 1758000000.123,
  "data": { }
}
```

服务端推送额外带 `id` 字段（仅 `log` 类型有，用于补发游标）。客户端请求额外带 `req_id`，服务端在对应的应答里原样回带，便于客户端匹配。

### 7.3 服务端推送消息

**`log`** — 单条日志。这是量最大的消息类型。

```json
{
  "type": "log",
  "ts": 1758000000.123,
  "data": {
    "id": 10231,
    "source": "task",
    "level": "INFO",
    "content": "当前理智：120/135",
    "pipeline_id": "3f2a...",
    "task_id": "9c1b...",
    "logger": null,
    "attachment": null
  }
}
```

首次连接与断线补发时会有成批日志，此时改用 `log_batch` 承载，避免上百个小帧：

```json
{
  "type": "log_batch",
  "ts": 1758000000.500,
  "data": {
    "records": [ { "id": 10200, "source": "core", "level": "INFO", "content": "..." } ],
    "truncated": false
  }
}
```

`truncated` 为 `true` 表示补发量超过了环形缓冲的容量，客户端需改走 §7.7 的历史查询接口补齐更早的部分。

**`pipeline_status`** — 流水线状态变更。

```json
{
  "type": "pipeline_status",
  "ts": 1758000001.000,
  "data": {
    "pipeline_id": "3f2a...",
    "status": "running",
    "source": "manual",
    "priority": 0,
    "progress": { "total": 5, "completed": 2, "failed": 0 },
    "started_at": 1758000000.0,
    "finished_at": null,
    "error": null
  }
}
```

`status` 取值与 `TaskPipelineStatus` 对齐：`pending` / `running` / `completed` / `failed` / `cancelled`。

**`task_status`** — 单个任务状态变更。

```json
{
  "type": "task_status",
  "ts": 1758000002.000,
  "data": {
    "pipeline_id": "3f2a...",
    "task_id": "9c1b...",
    "task_name": "刷理智",
    "type_name": "Fight",
    "status": "running",
    "retry_count": 0,
    "max_retries": 3,
    "error": null
  }
}
```

**`queue_changed`** — 队列内容变化（新提交、出队、取消、重排）。只推摘要，前端需要明细时再拉接口。

```json
{
  "type": "queue_changed",
  "ts": 1758000003.000,
  "data": {
    "running": { "pipeline_id": "3f2a...", "name": "日常任务" },
    "pending": [
      { "pipeline_id": "7d4e...", "name": "肉鸽", "source": "agent", "priority": 1 }
    ],
    "counts": { "pending": 1, "running": 1 }
  }
}
```

**`core_status`** — MaaCore 子进程状态，对应 [02-系统架构设计 §4](./02-系统架构设计.md) 的状态机。

```json
{
  "type": "core_status",
  "ts": 1758000004.000,
  "data": {
    "core_id": "default",
    "state": "ready",
    "version": "v6.17.5",
    "pid": 26316,
    "restart_count": 0,
    "last_error": null
  }
}
```

`state` 取值：`stopped` / `starting` / `ready` / `crashed` / `restarting` / `failed`。

**`device_status`** — ADB 设备状态，状态机定义见 [08-ADB连接与账号渠道](./08-ADB连接与账号渠道.md)。

```json
{
  "type": "device_status",
  "ts": 1758000005.000,
  "data": {
    "state": "connected",
    "address": "127.0.0.1:5555",
    "uuid": "f7c1c4ced5e96a23",
    "resolution": { "width": 2560, "height": 1440 },
    "retry": { "attempt": 0, "max": 60, "next_at": null },
    "last_error": null
  }
}
```

**`confirm_request`** — 高风险操作待人工确认，前端据此弹卡片。语义见 [11-Agent模块设计](./11-Agent模块设计.md)。

```json
{
  "type": "confirm_request",
  "ts": 1758000006.000,
  "data": {
    "confirmation_id": "c8a1...",
    "category": "consumable",
    "title": "确认使用碎石？",
    "summary": "刷理智任务将最多碎 3 颗源石",
    "caller": { "kind": "agent", "name": "builtin-chat", "session_id": "s-991" },
    "tool": "submit_pipeline",
    "params": { "tasks": [ { "type": "Fight", "stage": "1-7", "stone": 3 } ] },
    "expires_at": 1758000126.000
  }
}
```

`expires_at` 让前端能自己倒计时，不必等服务端通知。超时后服务端会另发一条 `confirm_resolved`（`resolution: "expired"`）关闭卡片。

**`update_progress`** — 三种热更新的进度，语义见 [07-热更新方案](./07-热更新方案.md)。

```json
{
  "type": "update_progress",
  "ts": 1758000007.000,
  "data": {
    "update_id": "u-4412",
    "target": "core",
    "phase": "downloading",
    "percent": 42.7,
    "downloaded": 82837504,
    "total": 193765923,
    "speed": 3145728,
    "eta": 35,
    "message": "正在下载 MAA-v6.17.5-macos-runtime-universal.zip",
    "error": null
  }
}
```

`target` 取值 `core` / `resource` / `game`。`phase` 取值 `checking` / `downloading` / `verifying` / `waiting_idle` / `applying` / `restarting` / `done` / `failed`。下载几 GB 的游戏 APK 时这条消息会持续很久，服务端做节流：每 500 ms 或每 1% 推一次，取先到者。

**`update_available`** — 检测到有新版本可用。由每日自动检查或手动点「立即检查更新」触发，机制见 [07-热更新方案 §5.6](./07-热更新方案.md)。

```json
{
  "type": "update_available",
  "ts": 1758000009.000,
  "data": {
    "targets": [
      { "target": "core", "current": "v6.17.5", "latest": "v6.18.0" },
      { "target": "resource", "channel": "repo",
        "current": "2026-09-05 11:08:36.000", "latest": "2026-09-14 04:36:14.000" }
    ]
  }
}
```

**一次检查只发一条，`targets` 汇总全部有更新的目标**，不是每个目标发一条。无更新时不发这个事件（前端据此撤下红点要靠 `GET /api/updates/status` 的响应，而不是等一条"没有更新"的消息）。这与 `update_progress` 的关系是：前者说"有新版本"，后者说"正在装"，两者之间隔着用户的一次手动确认 —— 自动检查不会自动触发下载。

**`agent_event`** — 内置 agent 的运行轨迹，前端对话界面据此渲染流式输出与工具调用。

```json
{
  "type": "agent_event",
  "ts": 1758000008.000,
  "data": {
    "session_id": "s-991",
    "event": "tool_call",
    "payload": {
      "call_id": "tc-3",
      "tool": "get_pipeline_status",
      "arguments": { "pipeline_id": "3f2a..." }
    }
  }
}
```

`event` 取值：`message_delta`（LLM 流式 token）、`message_done`、`tool_call`、`tool_result`、`error`、`usage`（token 消耗统计）、`atomic_grant_changed`（原子操作会话级授权生效、撤销或到期）。

`atomic_grant_changed` 的 `payload` 为 `{"granted": bool, "expires_at": float | null, "grant_id": str | null}`。前端据此显示或撤下顶部的授权提示条（机制见 [11-Agent模块设计 §4.3](./11-Agent模块设计.md)）。**到期也要发这个事件**，不能只靠前端本地倒计时——前端算出的到期时刻与服务端的判定可能有偏差，让服务端主动通知才能保证提示条消失的时机与授权真正失效的时机一致。

### 7.4 客户端发送消息

**`subscribe`** — 设置订阅过滤条件。可多次发送，后发的**整体替换**先前的过滤条件（而非叠加），语义更简单。

```json
{
  "type": "subscribe",
  "req_id": "r-1",
  "data": {
    "channels": ["log", "pipeline_status", "task_status", "core_status", "device_status"],
    "log_filter": {
      "sources": ["task", "service"],
      "min_level": "INFO",
      "pipeline_id": null
    },
    "last_seen_id": 10199
  }
}
```

`last_seen_id` 只在首次 `subscribe` 时生效，服务端据此从环形缓冲补发 `id > last_seen_id` 的记录。

服务端应答：

```json
{
  "type": "subscribed",
  "req_id": "r-1",
  "ts": 1758000000.0,
  "data": { "channels": ["log", "pipeline_status"], "backfilled": 32, "truncated": false }
}
```

**`unsubscribe`** — 退订部分频道。移动端切到后台或用户离开日志页时用它降低流量。

```json
{
  "type": "unsubscribe",
  "req_id": "r-2",
  "data": { "channels": ["log"] }
}
```

**`confirm_response`** — 人工确认的应答。

```json
{
  "type": "confirm_response",
  "req_id": "r-3",
  "data": {
    "confirmation_id": "c8a1...",
    "approved": true,
    "comment": "今天最后一把"
  }
}
```

同样的语义也通过 `POST /api/confirmations/{id}` 提供（见 [05-API规范与路由清单](./05-API规范与路由清单.md)），两条路径殊途同归。WebSocket 路径延迟更低且不需要额外鉴权往返，HTTP 路径则便于非浏览器客户端使用。服务端对同一 `confirmation_id` 的重复应答返回 `409`，只认第一次。

**`ping`** — 客户端心跳。

```json
{ "type": "ping", "req_id": "r-4", "data": { "t": 1758000009.000 } }
```

服务端立即回 `pong`，原样回带 `t` 供客户端计算 RTT：

```json
{ "type": "pong", "req_id": "r-4", "ts": 1758000009.010, "data": { "t": 1758000009.000 } }
```

**错误应答。** 客户端消息格式错误或鉴权范围不足时：

```json
{
  "type": "error",
  "req_id": "r-5",
  "ts": 1758000010.0,
  "data": { "code": "WS_BAD_MESSAGE", "message": "未知的消息类型: subscibe" }
}
```

错误码沿用 [05-API规范与路由清单](./05-API规范与路由清单.md)的字符串枚举体系。协议错误不关闭连接，除非连续 10 条非法消息。

### 7.5 连接管理

`ConnectionManager` 维护活跃连接集合，每个连接是一个 `ClientSession`：

```python
@dataclass
class ClientSession:
    ws: WebSocket
    id: str
    send_queue: asyncio.Queue      # maxsize=500
    channels: set[str]
    log_filter: LogFilter
    last_pong: float
    sender_task: asyncio.Task
```

每个连接跑两个协程：`_receiver` 循环 `ws.receive_json()` 处理客户端消息，`_sender` 循环从 `send_queue` 取消息 `ws.send_json()`。广播只往队列里塞，真正的 IO 由 `_sender` 承担——这是慢客户端不拖累广播的关键。

**多客户端广播。** 手机、平板、桌面浏览器同时连接是常态。广播时遍历连接集合，逐个 `put_nowait`。遍历期间可能有连接进出，所以对集合的快照操作要在锁内完成，或直接遍历 `list(self._sessions.values())` 的副本。

**心跳与超时。** 双向都有心跳，职责不同：

- **服务端 → 客户端**：每 30 秒发一帧 WebSocket 协议级 ping（`websockets` 库自动处理），或应用层 `server_ping` 消息。连续 2 次未收到 pong（60 秒）判定连接失效，主动关闭。
- **客户端 → 服务端**：客户端每 30 秒发应用层 `ping`。服务端记录 `last_pong`，超过 90 秒无任何客户端消息则关闭连接。

之所以两边都要：经过 nginx 反代或移动网络 NAT 时，中间设备可能在无流量一段时间后静默丢弃连接，两端都不会立刻感知。双向心跳让任一侧都能及时发现死连接。若部署在 nginx 后，`proxy_read_timeout` 需大于心跳间隔（建议 120s），否则 nginx 会先于应用超时断开。

**优雅关闭。** 服务停止时给所有连接发 `{"type": "server_shutdown"}` 后以 close code `1001`（going away）关闭，客户端据此区分"服务重启"与"网络故障"，前者用更短的重连间隔。

### 7.6 订阅过滤放在服务端

过滤在服务端做，理由是移动端优先。

前端过滤意味着所有日志都要下发到每个客户端，手机在 4G 下打开日志页就要接收 `core` 源的全量 native 日志——即便界面上只显示 `task` 标签页。实测 MaaCore 每分钟 1600 行，这是纯粹的流量与电量浪费。服务端过滤后，只订阅 `task` 源的客户端流量能降一到两个数量级。

代价是服务端要为每个连接维护过滤状态并逐条判定。这个开销很小：过滤判定是几次集合查找与整数比较，比序列化 JSON 本身便宜得多。而且广播时可以先按过滤条件分组，同一组共用一次 JSON 序列化结果：

```python
def broadcast_log(self, record: LogRecord) -> None:
    payload_cache: dict[tuple, str] = {}
    for s in list(self._sessions.values()):
        if "log" not in s.channels or not s.log_filter.accept(record):
            continue
        key = s.log_filter.cache_key()
        if key not in payload_cache:
            payload_cache[key] = json.dumps(self._envelope(record), ensure_ascii=False)
        try:
            s.send_queue.put_nowait(payload_cache[key])
        except asyncio.QueueFull:
            self._mark_slow(s)
```

前端仍保留一层轻量的客户端过滤，用于标签页切换这类不值得往返服务端的即时交互（例如已订阅 `task` 与 `service` 两源，切标签时本地隐藏另一源）。两层过滤不冲突：服务端控流量，客户端控展示。

### 7.7 断线重连与日志补发

客户端在本地持久化 `last_seen_id`（最后收到的日志 `id`），重连时通过 query 参数或首个 `subscribe` 消息带上。服务端的补发逻辑：

```
收到 last_seen_id = L
  → 环形缓冲中最小 id 记为 M
  → L >= 缓冲最大 id：无缺口，不补发
  → L >= M - 1：缺口完全在缓冲内，直接发 log_batch（应用订阅过滤）
  → L <  M - 1：缺口超出缓冲，发缓冲全量 + truncated=true
                 客户端据此调用 GET /api/system/logs?after_id=L&before_id=M 补齐
```

补发同样受订阅过滤约束，`truncated=true` 时前端应在日志列表里插入一条"存在未加载的历史日志，点击加载"的分隔提示，而不是静默跳过。

重连策略由前端实现：指数退避 1s → 2s → 4s → 8s → 最大 30s，带 ±20% 抖动避免多客户端同时重连。收到 `server_shutdown` 时首次重连间隔固定为 3s。

## 8. 截图在日志流里的处理

### 8.1 现状的问题

`executor.py` 在任务失败时调用 `adb_service.adb_screenshot_base64()`，把整张截图的 base64 字符串当作日志内容塞进 `TaskPipeline.logs`（`pipeline.py` 的 `append_img_log`）。

这个做法在移动端会很痛苦。2560×1440 的 JPEG 即使按 `screenshot_quality: 25` 压缩也有数百 KB，base64 编码后再涨 33%。这些字节要经 WebSocket 全量推给每个连接的客户端，要写进日志表的 `content` 列，要在前端渲染日志列表时一并解析——而用户可能根本不想看这张图。日志列表滚动时浏览器要反复处理这些超长字符串，卡顿明显。

### 8.2 新方案：存文件 + 引用

截图落盘，日志只带引用。`LogRecord.attachment` 字段承载：

```json
{
  "id": 10245,
  "source": "task",
  "level": "ERROR",
  "content": "任务失败 [刷理智]，已保存现场截图",
  "pipeline_id": "3f2a...",
  "task_id": "9c1b...",
  "attachment": {
    "kind": "screenshot",
    "sha256": "d4f1a2...",
    "width": 2560,
    "height": 1440,
    "bytes": 412883,
    "thumb_url": "/api/images/d4f1a2.../thumb",
    "full_url": "/api/images/d4f1a2.../full",
    "captured_at": 1758000011.0
  }
}
```

日志内容本身变回一行纯文本，前端在该条日志下渲染一个小缩略图占位，点击才加载原图。

存储位置为 `resource/image/screenshot/{sha256前2位}/{sha256}.jpg`，两级目录避免单目录文件数过多。用内容哈希做文件名带来天然去重：连续失败重试时截到的往往是同一个画面，多条日志会指向同一个文件。

截图的产生有两条路径，都汇到同一套存储：MaaCore 的 `GET_IMAGE` 命令（子进程落盘后返回路径，见 [02-系统架构设计 §3.3](./02-系统架构设计.md)）与 adbutils 的 `device.screenshot()`（见 [08-ADB连接与账号渠道](./08-ADB连接与账号渠道.md)）。两者拿到图像后统一交给 `util/image.py` 的 `store_screenshot()` 计算哈希、生成缩略图、写盘、返回 `attachment` 结构。

### 8.3 缩略图策略

写盘时**同步生成**一张缩略图，而不是等前端请求时再生成。原因是按需生成会在日志列表首次滚动时集中触发几十次图像解码，反而更卡；而生成一张缩略图只需几毫秒。

| 规格 | 尺寸 | 质量 | 用途 |
|---|---|---|---|
| `thumb` | 长边 320 px | JPEG 60 | 日志列表内联，约 15~30 KB |
| `full` | 原始尺寸 | 沿用 `screenshot_quality` | 点击放大查看 |

缩略图按长边等比缩放，不裁剪——游戏画面裁剪后会丢失关键信息。用 `Image.thumbnail()` 配合 `Image.Resampling.LANCZOS`。

`full` 规格对移动端可能仍然偏大。前端在检测到窄视口时请求 `?w=1280` 参数，服务端返回按宽度缩放的版本并缓存到 `resource/temp/`。这是可选优化，首版可以先只提供两档。

两个接口都设置 `Cache-Control: public, max-age=31536000, immutable`——URL 里含内容哈希，图片内容永不变化，浏览器可以无限期缓存。这对移动端体验提升显著。

### 8.4 清理

截图会持续累积，需要与日志保留期联动清理。清理任务每天执行，删除**没有任何日志记录引用**且创建时间超过保留期的图片文件。

不能只按时间删：一条被用户收藏或关联到未关闭故障单的日志，其截图应当保留。实现上在日志清理完成后，扫描图片目录，对每个文件查询 `attachment_sha256` 索引，无引用者删除。这个反向查询需要在日志表上建 `attachment_sha256` 的索引，见 [04-数据模型与持久化](./04-数据模型与持久化.md)。

## 9. 历史日志查询接口

WebSocket 只负责实时流，历史检索走 REST。完整路由清单见 [05-API规范与路由清单](./05-API规范与路由清单.md)，这里定义查询语义。

### 9.1 列表查询

```
GET /api/system/logs
```

| 参数 | 类型 | 说明 |
|---|---|---|
| `source` | string[] | 按来源过滤，可重复，缺省为全部 |
| `level` | string | 最低级别，如 `INFO` 表示 INFO 及以上 |
| `pipeline_id` | string | 关联的流水线 |
| `task_id` | string | 关联的任务 |
| `logger` | string | service 源的 logger 名前缀匹配 |
| `since` / `until` | float | 时间范围，Unix 时间戳 |
| `q` | string | 内容子串匹配 |
| `after_id` / `before_id` | int | 游标分页边界 |
| `size` | int | 每页条数，默认 100，上限 1000 |
| `order` | string | `asc` / `desc`，默认 `desc` |

参数名沿用 [05](./05-API规范与路由清单.md) 的全库约定（时间范围一律 `since` / `until`，每页条数一律 `size`），不另起 `start_ts` / `limit` 这类别名。唯一的例外是 `size` 的上限：全局约定是 200，日志端点放宽到 1000，因为导出前的预览与断线补发都可能一次性拉取大段记录。

**分页应当用游标而非 offset。** 05 的全局分页约定是 `page` 加 `size`，日志端点在此之外额外支持 `after_id` / `before_id` 游标，两种模式互斥（同时传返回 `400 INVALID_PAGINATION`）。前端与补发逻辑应一律走游标：日志表会长到百万行量级，`OFFSET 500000` 需要 SQLite 扫过前 50 万行，越翻越慢；而 `id` 单调自增且有索引，`WHERE id < ? ORDER BY id DESC LIMIT ?` 是稳定的索引范围扫描，任意深度都是同样的开销。另一个好处是分页期间有新日志写入时不会导致条目重复或遗漏——offset 分页在数据持续增长时必然错位。`page` 模式仅为与其他列表接口保持形式一致而保留，不建议对日志使用。

响应：

```json
{
  "items": [ { "id": 10231, "ts": 1758000000.123, "source": "task", "level": "INFO",
               "content": "当前理智：120/135", "pipeline_id": "3f2a...", "attachment": null } ],
  "page": { "next_cursor": 10132, "has_more": true, "limit": 100 }
}
```

不返回总条数。`COUNT(*)` 在百万行表上带过滤条件时开销不小，而前端用无限滚动并不需要总数。确有需要时用单独的 `GET /api/system/logs/count` 接口，并接受它可能较慢。

### 9.2 按流水线聚合

```
GET /api/pipelines/{pipeline_id}/logs
```

等价于 `GET /api/system/logs?pipeline_id=...&order=asc`，但默认升序（回放一条流水线的执行过程自然是从头看）且不分页上限更宽松（单条流水线的日志量可控）。

这个接口是"回放某次执行"的核心。前端的流水线详情页用它渲染完整时间线，把 `task_status` 变更与日志按时间戳交织展示。

`pipeline_id` 的关联靠 `LogRecord.pipeline_id`。三路日志的关联方式不同：

- `task` 源：从回调的 `taskid` 反查任务映射得到，最可靠
- `service` 源：靠 `contextvars` 传递当前流水线上下文。`PipelineRunner` 在执行每条流水线时设置 `ContextVar`，`LogHubHandler` 从中读取。跨线程与跨 `asyncio.Task` 时 `contextvars` 会自动继承，但 `to_thread` 之外的裸线程不会——这部分日志的 `pipeline_id` 为空是可接受的
- `core` 源：**无法关联**。`asst.log` 的行里没有任何我们的标识。折中方案是按时间窗口近似归属：查询某流水线日志时，`core` 源按该流水线的 `started_at` ~ `finished_at` 时间范围匹配。这只在单流水线串行执行时成立——恰好符合首版单子进程单消费者的模型。多子进程扩展后这个近似会失效，届时需要依赖 `asst.log` 行里的 `Px` 进程号做区分

### 9.3 导出

```
GET /api/system/logs/export?format=txt&pipeline_id=...
```

返回纯文本或 JSON Lines 的附件下载，用于提 issue 时附上完整日志。导出不受 `limit` 限制但有总量上限（默认 10 万行），超出时截断并在文件末尾标注。

导出的文本格式与文件日志保持一致，便于与 `resource/log/{date}.log` 交叉比对：

```
2026-09-16 11:13:40.919 [INFO ] [task   ] 开始任务 [刷理智]
2026-09-16 11:13:41.102 [INFO ] [core   ] The fastest way is RawByNc , cost: 490 ms
```

## 10. 前端消费要点

前端实现细节见 [09-前端重构方案](./09-前端重构方案.md)，这里只约定与协议相关的部分。

**按来源分标签页**对应三个 `source` 值，外加一个"全部"标签。标签页切换时不重新订阅（避免频繁往返），而是一次性订阅当前需要的全部来源，本地按标签过滤展示。只有当用户在设置里明确关闭某个来源时，才发 `subscribe` 更新服务端过滤。

**虚拟滚动是必须的。** 日志条目会到数千条量级，全量渲染 DOM 会让移动端卡死。配合 `id` 作为稳定 key。

**自动滚动到底部**默认开启，用户手动向上滚动时自动关闭并显示"回到最新"按钮——这是日志界面的基本预期，缺了会很难用。

**级别与来源的视觉编码**用颜色而非图标，移动端屏幕小，图标占地方且难点击。`ERROR` 红、`WARNING` 黄、`INFO` 默认、`DEBUG` 灰。

**PWA 的离线场景**下 WebSocket 必然断开。Service Worker 缓存的是静态资源，日志数据不做离线缓存——展示过期日志比展示"未连接"更容易误导。重新联网后走 §7.7 的补发流程。注意局域网 HTTP 下 Service Worker 不可用，见 [README 关键技术约束](./README.md#关键技术约束)。

## 11. 待确认的开放问题

**MaaCore debug 日志的默认采集级别。** 本文档建议默认 `INF` 及以上，依据是实测样本中 `TRC` 占 62%、`WRN` 占 30%。但这份样本只覆盖了连接与截图阶段（48 分钟内 68 次连接失败），任务实际执行时的级别分布可能不同。建议上线后按真实负载重新统计，再决定默认值。

**`asst.log` 是否需要按子进程重启分段。** 内核重启后 MaaCore 会向同一文件追加新的 `MaaCore Process Start` 分隔块。当前设计不做分段，前端看到的是连续流。若排查崩溃时发现难以区分重启前后，可考虑在 tailer 里识别该标记并注入一条分隔记录。

**日志保留期的默认值。** 暂定 14 天 / 200 万条。实际磁盘占用取决于 `core` 源的采集级别，需要运行一段时间后校准。

**`core` 源日志与流水线的关联。** §9.2 的时间窗口近似只在单子进程下成立。多子进程扩展时的方案（按 `Px` 进程号关联）尚未验证——需要确认同一子进程内 MaaCore 是否会用多个进程号，以及子进程 pid 与 `Px` 值是否一致。实测样本里出现了 `Px26316` 与 `Px3213` 两个值，对应两次不同的进程启动，初步支持这个思路，但样本不足以定论。
