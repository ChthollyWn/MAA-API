# M1-02 实测记录：子进程隔离 + Queue IPC 最小验证

> 丢弃式探针，对应 docs/12 §5「M1 的方案验证前置」、docs/13 ADR-02 / ADR-03。
> 脚本：`scripts/spike_subprocess_isolation.py`；机器可读结果：`tests/fixtures/spike_subprocess_result.json`。
> 实测日期 2026-09-16，内核 `v6.17.5`（`resource/lib/maa/Darwin`），真机 `127.0.0.1:5555`，
> Python 3.13.3（`.venv`），macOS，`multiprocessing` start method = **spawn**。

## 0. 结论

**子进程隔离方案成立，可以按 ADR-02 / ADR-03 投入 M1-07 / M1-08 的完整实现。**
6 个检查点全部实测通过，且「回调一条不丢」有量化证据（子进程入队 13 条 = 主进程实收 13 条）：

| 检查点 | 结果 | 实测证据 |
|---|---|---|
| `spawn_child` | true | `Process.start()` 后 0.053s 收到子进程首个 `LOG` 事件（pid 62471） |
| `kernel_loaded_in_child` | true | 子进程内 `Asst.load()` 返回 True，`AsstGetVersion()` = `v6.17.5` |
| `callback_crossed_queue` | true | 13 条 `CALLBACK` 跨 `event_queue` 到达，含 `TaskChainStart`/`SubTaskExtraInfo` |
| `task_accepted` | true | `APPEND_TASK(StartUp, {"client_type":"Official"})` → `task_id=1` |
| `kill_detected` | true | `os.kill(pid, SIGKILL)` → exitcode **-9**（0.053s 内观测到） |
| `restart_observed` | true | 第二个子进程 0.222s 后 `READY`，可再次完整加载内核 |

单次全链路（含 8s 回调观察窗口）总耗时 9.35s；不等待观察窗口时 3.55s。

## 1. 复现方式

```bash
cd /Users/chtholly/Developer/WorkSpace/MAA-API
DYLD_LIBRARY_PATH=$PWD/resource/lib/maa/Darwin .venv/bin/python scripts/spike_subprocess_isolation.py
# 退出码 0 = 6 个检查点全通过；失败退出 1，且结果 JSON 里是诚实的 false
.venv/bin/python scripts/spike_subprocess_isolation.py --help   # 不加载内核
```

脚本连续跑 4 次，6 个检查点均全绿，耗时量级完全一致（见下表区间）。

## 2. ① spawn 是否可用

**可用，且成本可以忽略。** `multiprocessing.get_context("spawn")` + `ctx.Process(target=<模块级函数>)`，
子进程按限定名重新 import 本脚本（`__mp_main__`）后执行，未出现任何 pickle 失败。

| 指标 | 实测（4 次） |
|---|---|
| `Process.start()` 本身 | 0.005 – 0.030 s |
| spawn → 子进程第一个事件 | 0.043 – 0.084 s |
| spawn → `READY`（内核加载完） | 0.205 – 0.292 s |

要点：

- **入口函数必须是模块级函数**（spawn 只传 `(模块名, 限定名)`），且入口模块在子进程里会被重新
  import，所以模块顶层不能有副作用（本脚本靠 `if __name__ == "__main__":` 隔离 CLI）。
- **两个 `ctx.Queue()` 可以直接作为 `Process(args=...)` 的参数**，无需额外包装。
- **回调桥接靠「子进程模块级全局」拿 `event_queue`**，不用旧实现的 `AsstCreateEx(cb, ctypes.c_void_p(id(arg)))`
  指针法：`id()` 只在同一进程内有效，跨 spawn 必须重建对象。这是 M1-07 必须注意的一条。
- 子进程继承父进程的 `DYLD_LIBRARY_PATH`（子进程自报环境变量与父进程一致），但**实测按绝对路径
  `ctypes.CDLL(<dir>/libMaaCore.dylib)` 并不依赖它**：`env -u DYLD_LIBRARY_PATH` 也能 dlopen 成功
  （依赖库走 `@loader_path`/`@rpath`）。这条环境变量是冗余保险，不是必需。

## 3. ② 内核在子进程内的加载耗时

| 阶段 | 实测（3 次） |
|---|---|
| `Asst.load()`（dlopen + `AsstSetUserDir` + `AsstLoadResource`） | **0.157 – 0.212 s** |
| `AsstCreateEx`（建实例 + 绑回调） | 0.0001 – 0.0002 s |
| 子进程从入口到 `READY` | 0.157 – 0.212 s（几乎全是 `Asst.load`） |
| `CONNECT` 命令返回 `async_call_id` | 0.0003 s（不阻塞命令循环） |
| 首个 `ConnectionInfo` 回调 | 0.034 – 0.046 s |
| 内核自报连接成本（`AsyncCallInfo.details.cost`） | 837 – 1798 ms |

**子进程启动序列里确实没有网络 IO**：`Asst.load` 只读本地文件，设备连接是 `READY` 之后由主进程
显式下发的 `CONNECT`（docs/03 §3.1）。整条启动链路 0.2 秒量级，与旧 `scheduler.py` import 期连
ADB（失败则服务起不来）形成直接对比。

`APPEND_TASK` 的 `CMD_RESULT` 耗时 **0.78 – 0.95 s**（内核在 append 时解析并校验任务链）。
命令循环是严格串行的，这条数据支持 docs/02 §3.4 的「差异化超时」，也是 M1-08 心跳阈值在慢命令
期间必须放宽的实测依据。

## 4. ③ 回调跨进程是否完整

**完整，一条不丢。** 子进程在自己的回调线程里把每条原始回调转成 `CALLBACK` 事件入队；主进程在
SIGKILL 之前用 `PING` 命令让子进程自报入队条数，两者精确相等：

```json
{"child_enqueued": 13, "child_put_failed": 0, "parent_received_after_ping": 13, "match": true}
```

主进程实收的 msg 序列（13 条，含连接、任务链、子任务、完成四类）：

```
2  ConnectionInfo      UuidGot
2  ConnectionInfo      ResolutionGot
2  ConnectionInfo      Connected
2  ConnectionInfo      FastestWayToScreencap
2  ConnectionInfo      ResolutionInfo
4  AsyncCallInfo       {"async_call_id":1,"details":{"cost":837,"ret":true},"what":"Connect"}
10001 TaskChainStart   {"taskchain":"StartUp","taskid":1}
20001 SubTaskStart     {"class":"asst::ProcessTask","details":{"task":"StartUpBegin",...}}
20002 SubTaskCompleted ...
2  ConnectionInfo      EmulatorFPS
20003 SubTaskExtraInfo {"details":{"task":"ReturnButton","what":"ExceededLimit"},"first":[...]}
10002 TaskChainCompleted {"taskchain":"StartUp","taskid":1}
3  AllTasksCompleted   {"finished_tasks":[1],"taskchain":"StartUp","taskid":1}
```

- `payload.details` 里有多层嵌套（`details.result`、`first`、`finished_tasks` 列表），
  pickle 往返后类型无损——**「纯 dict + 基础类型」的消息约定在真实回调上成立**。
- 回调里**没有出现任何异常**：桥接只做 `details.decode` + `json.loads` + `put`，
  `put` 失败单独计数（实测 0）。异常穿 C 边界的风险被隔离在 try/except 内。
- 回调在子进程内是**异步线程**产生的：`TaskChainStart` 在主进程 `START` 返回后 0.0002s 就已在
  队列里，说明「主进程发命令」与「内核回调」两条流互不阻塞。

## 5. ④ 杀进程后的 exitcode 与重启耗时

| 指标 | 实测（4 次） |
|---|---|
| `os.kill(pid, SIGKILL)` → `join()` 返回并拿到 exitcode | **0.044 – 0.055 s** |
| exitcode | **-9**（`signal.Signals(9).name == "SIGKILL"`） |
| 第一条子进程清理后是否残留 | 否（`alive: false`，脚本 finally 里兜底 kill） |
| 重启（spawn 第二条）→ `READY` | **0.219 – 0.241 s** |
| 重启子进程的内核加载耗时 | 0.157 – 0.171 s（与首启一致，无明显退化） |
| 第二条的优雅退出 | 发 `SHUTDOWN` → 收到 `CMD_RESULT` → 进程自然退出（未走到 kill 兜底） |

杀进程后没有任何清理残留：`event_queue`/`cmd_queue` 在父进程侧 `cancel_join_thread()` + `close()`，
不会因为对端已死而卡在 feeder 线程上——M1-08 重启路径要照抄这一点。

## 6. ⑤ 结论与对 M1-07 / M1-08 的启示

**方案成立**：spawn 隔离 + 双 Queue IPC 这条路线在真实内核、真实设备、真实任务链上跑通了
「启动 → 连接 → 提交 → 回调 → 被杀 → 重启」全环，且每一步都有量化耗时。没有发现需要在
M1 之前改方案的理由。

### 哪些参数必须可 pickle（M1-07）

实测通过的子进程入参只有三类，建议就按这个白名单收口：

| 可以传 | 不能传（会运行时报错） |
|---|---|
| `str` / `Path` 转成的 `str`（`maa_path`、`user_dir`、`incremental_paths[]`） | `Asst` 实例（含 ctypes 指针） |
| 纯 dict / list / int / float / bool / None（`instance_options`、`boot_config`） | `CFUNCTYPE` 回调对象、`id(arg)` 形式的指针 |
| `multiprocessing.Queue`（仅作为 `Process(args=...)` 的参数） | logger / handler、asyncio loop / Future、线程锁、打开的文件句柄、Config 单例 |

具体建议：

1. `boot_config` 只放纯数据（docs/03 §3.1 的形态是对的）；`InstanceOptionType` 枚举在子进程内
   重建，不要在父子之间传枚举实例之外的任何对象。
2. 回调桥接必须是**模块级函数**，`event_queue` 用**模块级全局**持有；`Asst.__init__` 里那个
   `ctypes.c_void_p(id(arg))` 的老写法只在同进程内成立，worker 化之后不能再沿用。
3. 子进程入口模块顶层保持无副作用；`Asst(callback=...)` 的 CFUNCTYPE 引用要挂在实例上防 GC。

### 崩溃检测与重启（M1-08）

1. **不要用「exitcode 为负」判 native 崩溃。** 实测本内核把 native 崩溃转成了 **exit 1 +
   `<user_dir>/crash.log`**：调用 `AsstGetTasksList(handle)` 触发 SIGSEGV 后，进程写入 crash.log
   （`Reason: Fatal Signal / Detail: SIGSEGV`）并以退出码 1 结束（不是 139 / -11）。
   判据应是「非预期退出 + 非 0 退出码」，并把 crash.log 作为落库/告警的补充证据。
   docs/03 §8 里 `FakeAsst` 主动 `os._exit(-11)` 的注入无法覆盖这条真实路径，建议再加一条
   「exit 1 + crash.log」的注入用例。
2. 外部信号（`SIGKILL`）给出的仍是正常的负 exitcode（-9），两种退出形态都要能识别。
3. 重启预算可以很小：spawn → `READY` 只有 0.22s。退避策略的作用是防崩溃循环，不是掩盖慢启动。
4. 心跳/维护窗口：`APPEND_TASK` 已经会阻塞命令循环约 0.8s，`LOAD_RESOURCE` 只会更久（docs/02 §3.4
   给了 300s 超时）；慢命令期间不能用心跳超时误判死亡——本探针的 `PING`（`CMD_RESULT` 往返，
   1ms 量级）可以直接作为心跳实现的起点。

## 7. 顺带挖到的坑（都会影响 M1 实现，建议同步进 M1-04 / M1-07 / M1-09）

### 7.1 `AsstMsg` 取值不是 1..12 连号

实测（与旧实现 `maa_api/model/util/utils.py` 的 `Message` 枚举一致，但与「1 起连号」的直觉不同）：

```
InternalError=0  InitFailed=1  ConnectionInfo=2  AllTasksCompleted=3  AsyncCallInfo=4  Destroyed=5
TaskChainError=10000  TaskChainStart=10001  TaskChainCompleted=10002  TaskChainExtraInfo=10003  TaskChainStopped=10004
SubTaskError=20000   SubTaskStart=20001   SubTaskCompleted=20002   SubTaskExtraInfo=20003   SubTaskStopped=20004
```

写主进程翻译表时若照 1..12 连号硬编码，会把 `ConnectionInfo`(2) 认成 `TaskChainError`。
本脚本第一版就踩了这个坑：等待连接回调时写的是 `msg == 3`（自以为是 `ConnectionInfo`，实际是
`AllTasksCompleted`），于是 5 条 `ConnectionInfo` 已经跨进程到达，却仍被判成「没收到回调」。

### 7.2 `AsstAppendTask` 会校验必填参数，缺参数时静默返回 0

这是本卡最花的调试成本，也是给 M1-07 / M1-09 最值钱的一条：

| 调用 | 返回 |
|---|---|
| `APPEND_TASK("StartUp", {})` | **0**（被拒，无异常、无回调） |
| `APPEND_TASK("StartUp", {"client_type": "Official"})` | 1 |
| `APPEND_TASK("Infrast", {})` / `("Infrast", {"facility": ["Mfg"]})` | 0 / 2 |
| `APPEND_TASK("Recruit", {})` / `("Recruit", {"times": 4})` | 0 / 0 |
| `APPEND_TASK("Award" / "Fight" / "Mall" / "Roguelike" / "OperBox" / "Depot", {})` | 非 0 |

推论：`task_id == 0` **必须**被当成「命令失败」上报给前端，不能当作正常结果；`StartUp` 这类任务
需要主进程把默认参数补齐（本探针给 `--task-params` 的默认值 `{"client_type": "Official"}` 就是
为了这个）。另外 `AsstGetTasksList` 本应给出可提交任务清单，但它在本内核上不可用（见 7.4）。

### 7.3 `AsstSetUserDir` 的目录必须已存在

否则返回 False；旧封装把它 `&=` 进 `Asst.load()` 的返回值，表现为「内核加载失败」这种误导性现象。
worker 里用 `tempfile.mkdtemp()` 或先 `mkdir(parents=True, exist_ok=True)`。

### 7.4 `AsstGetTasksList(handle)` 在本内核上直接段错误

`lib.AsstGetTasksList(handle)`（`restype=c_char_p`）调用后进程立即死亡：`<user_dir>/crash.log`
记录 `SIGSEGV`，退出码 **1**。符号本身是导出的（M1-01 已核实），所以更可能是调用约定/返回缓冲
形态与旧封装不一致。M1-04 绑这个 API 之前必须先从本地头文件确认签名，不要照旧封装直接调。

### 7.5 `import maa_api.model.core.asst` 的副作用

这条 import 链会连带 import `maa_api.config.config`，后者在 **import 期** mkdir 4 个目录
（`static/`、`resource/lib`、`resource/log`、`resource/temp`）并可能拷贝 `daily_task_template.json`，
CWD 不是仓库根时直接 `RuntimeError`。本探针因此刻意**不 import 旧 `Asst`**，自己绑了 9 个 C API；
新的 `maa_api/core/asst.py`（M1-04）应保持 import 期零副作用，否则子进程启动耗时会掺进无关变量。
