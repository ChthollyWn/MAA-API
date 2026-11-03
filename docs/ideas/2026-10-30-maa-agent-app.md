# MAA / MaaFramework Agent App 立项讨论

> 状态：**讨论稿**（2026-10-30 第一次讨论后已拍板 v1 方向，见 §1.1），不是 spec，不进入 M0–M62 的交付范围。
> 时间：2026-10-30。证据来自对四个指定仓库 + 生态仓库的只读代码分析（克隆在 `/tmp/maa-research/`，浅克隆）与官方文档，版本以文中标注为准。
> 结论会随讨论修订；拍板后另起 spec。

---

## 1. 一页结论

**这个方向成立，但「基于 MAA」和「不局限于明日方舟」是互斥的两句话——拍板结果是把它们拆开：v1 内容用 MAA（明日方舟），泛化做在任务抽象层，MaaFramework 作为 v2 的第二执行后端（§1.1）。**

- **MAA（MaaCore）是明日方舟专用内核**：它的价值在十几种带业务语义的任务（Fight / Recruit / Mall / Copilot…，MAA-Meow 列出 17 种）与整包资源，不在通用自动化。任何"MAA 跑别的游戏"的设想都要经过 MaaFramework，而 MaaFramework 没有官方明日方舟资源。
- **MaaFramework 才是泛游戏的那条线**：v5.14.3（2026-10-10）已有 Android aarch64/x86_64 官方产物、ProjectInterface V2（`interface.json`）任务声明、运行期 `pipeline_override`、Custom/Agent 扩展、以及从 Adb 到 AndroidNative、Custom、Dbg/Record/Replay 的十余种控制单元。M9A、MaaYuan、MaaStarResonance 等 PI 项目证明"任意游戏"不是空话。
- **生态的缺口不在执行层，在意图层**。通用 UI（MFAAvalonia / MXU / MaaFwApp / MWU）已经能把 PI 任务渲染成表单；MaaMCP 已经把设备与 Pipeline 原语暴露给桌面 AI 助手。**没有人把「项目定义的任务」变成 LLM 能安全调用的语义工具，并做成手机上的产品级 App。**这就是我们要占的位置。
- **Android 单机已经可行，执行栈有现成实现**：MaaFramework 路线的最佳基座是 `Aliothmoon/MaaFwApp`（AGPL-3.0，Kotlin + Compose，52★，2026-08 起活跃）——它把 MaaFramework 跑进 Shizuku/root 拉起的特权进程、用本机 native controller 完成截屏与注入、完整支持 PI V2、后台虚拟屏、定时、通知、日志；MaaCore 路线的最佳基座是 `Aliothmoon/MAA-Meow`（同作者，AGPL，1734★）——证明 MAA 内核能在 Android 特权进程里跑。**两条路线都不该从零重写执行层。**
- **Agent 的正确接法不是"让 LLM 写 Pipeline"**。PI V2 的 `task` + `option`（select/switch/checkbox/input/hotkey，含 `default_case`、`min_count`/`max_count`、嵌套与 `pipeline_override`）本身就是一份参数化任务 schema，可以直接翻译成 function calling；pipeline 覆盖值的合并由客户端按协议计算，LLM 只出语义参数。把"写 Pipeline"降级为兜底工具（MaaMCP 走的是相反的路，所以它更像 computer-use，而不是产品）。
- **最稀缺的是产品层能力**：确认/授权/审计（消费类、破坏类、任意点击）、多项目资源管理、失败诊断、中文输入、成本与上下文预算。这些恰好是本仓库 `docs/11-Agent模块设计.md` 已经趟过一遍的东西——**MAA-API 的 Agent 设计可以直接移植，但要换一套执行后端。**
- **推荐路线（经 §1.1 讨论拍板后修订）**：Android 单机优先；**v1 用 MaaCore（MAA）+ MAA-Meow 的特权执行栈 + 引擎无关的泛化任务层**，MaaFwApp 作为 v2 接 MaaFramework 的参考实现；桌面/PC 形态作为 v2 远程后端，不做同版本双引擎。

---

### 1.1 讨论后的拍板（2026-10-30）

| # | 问题 | 拍板 |
|---|---|---|
| D1 | 主形态 | **A. Android 单机 Agent App** |
| D2 | 引擎范围 | **v1 只做 MaaCore（MAA / 明日方舟），但任务层做成引擎无关的泛化层**；MaaFramework 作为第二个 EngineAdapter 在 v2 接入 |
| D3 | 资源分发 | **v1 一项目一 APK**（MAA 本身即单一项目，沿用 MAA-Meow 的内置资源模式） |
| D4 | 开源与许可 | **接受 AGPL，全开源** |

§2–§4 是拍板前的事实与技术判断（仍然有效）；§5、§6、§9 已按拍板方向修订，主要变化如下：

1. **v1 的基座不是 MaaFwApp，而是 MAA-Meow 的内核/设备层**。MaaFwApp 是 MaaFramework 的 Android GUI，跑不了 MaaCore 的任务语义；能跑 MAA 的是 MAA-Meow 那套（MaaCore + AndroidNative 控制单元 + `libbridge.so` + 特权进程 + 虚拟屏/注入）。MaaFwApp 降级为 **v2 接 MaaFramework 时的参考实现**（PI 解析、RunPlan、agent 子进程、AIDL 设备面）。
2. **「任务层泛化」落在抽象层，不落在内容层**。v1 的可用内容只有明日方舟（MAA 内置任务，MAA-Meow 列出 17 种 + Copilot + 自定义 `tasks.json`），但工具层不绑定 MAA：统一成 `TaskSpec`（id/名称/描述/参数 JSON Schema/默认值/风险与前置条件）+ `EngineAdapter`（listTasks / runTasks / stop / status / screenshot / primitives）。v2 新增 MaaFramework 适配器时，工具层、提示词与 UI 不重写。
3. **这个选择牺牲了"一条代码路径同时支持两套内核"**：MaaCore 的 `tasks.json` 与 MaaFramework 的 Pipeline 不兼容（无官方 converter），v2 的 MaaFramework 适配器是**第二套执行后端**，不是开关切换。好处是 v1 立刻有真实可用的内容（明日方舟），坏处是 v2 的工作量不会因 v1 而减少太多。
4. **v1 的 Agent 能力边界与 MAA-API 一致**（三层模型）：L1 MAA 任务编排为主，L2 Copilot / 自定义任务 / 自定义基建方案，L3 原子操作兜底（`AsstAsyncClick` / `AsstAsyncScreencap` / `AsstBackHome` + 特权进程的滑动/长按/文本/按键）。因为 MaaCore 没有独立 OCR/识别 API，**v1 的"看画面"必须依赖多模态模型**——这是 MaaFwApp/MaaFramework 路线没有的劣势。

---

## 2. 四个指定仓库各自给什么

### 2.1 MaaXYZ/MaaFramework — 引擎与协议

| 维度 | 事实 | 对我们的意义 |
|---|---|---|
| 版本 | v5.14.3（2026-10-10），LGPL-3.0，5106★ | 活跃、可依赖；LGPL 允许商业闭源集成（动态链接） |
| 产物 | `MAA-android-aarch64` / `x86_64` / linux / macos / win，各约 60–84 MB | Android 端有官方 `.so`，不必自编译 |
| 分层 | `MaaTasker` / `MaaController` / `MaaResource` / `MaaContext`，C ABI，绑定有 C++/Python/NodeJS/C#/Go（Java 仅 v3，已过时） | Kotlin 侧只能 JNA 手写子集（MaaFwApp 已这么做）；Python 侧适合做桌面/服务端 |
| 流水线 | Pipeline v1/v2 双协议；节点级字段覆盖（同名节点顶级键替换） | `MaaTaskerPostTask(entry, override)` 是"自由运行"的关键 |
| 运行期控制 | `MaaTaskerOverridePipeline(task, override)`；`MaaContext` 提供 RunTask / RunRecognition / RunAction / RunRecognitionDirect / RunActionDirect / WaitFreezes / OverridePipeline / OverrideNext / OverrideImage / GetNodeData / Anchor / HitCount / Clone | **本质是一套 agent 原语**：能直接调 OCR/模板匹配拿结构化结果，也能中途改图改链 |
| 扩展 | `MaaAgentServer`（Python/NodeJS 等独立进程注册 CustomRecognition/CustomAction，IPC 走 AF_UNIX/TCP）+ `MaaAgentClient`（宿主侧） | 项目自带的 custom 逻辑能被宿主拉起；也给了"LLM 作为 custom action"的想象空间 |
| 控制 | Adb / Win32 / MacOS / PlayCover / Gamepad / Linux / **AndroidNative** / **Custom** / Dbg / Record / Replay | 能力最全的是 CustomController；MaaFwApp 走的是 AndroidNative（已跑通）；Dbg/Replay 可做无设备测试 |
| 项目声明 | ProjectInterface V2（`interface.json`，当前 PI 语义版本 v2.11.0）：controller/resource/task/option/group/preset/global_option/setting/agent/pretask/i18n/telemetry | **泛化能力全部在这份 schema 里**；也是 LLM 工具 schema 的来源 |
| 生态 | 通用 UI：MFAAvalonia（C#/Avalonia）、MXU（Tauri+React，MistEO）、MWU（Vue+FastAPI）、MaaFwApp（Kotlin/Compose）；工具：MaaDebugger、MaaPipelineEditor、MaaInspector、MaaLogAnalyzer… | 通用 UI 有 4 个，说明"跑任务"这块是红海 |

关键仓库路径：`docs/zh_cn/1.3-Custom&Agent.md`、`2.4-控制方式说明.md`、`3.1-任务流水线协议.md`、`3.3-ProjectInterfaceV2协议.md`、`include/MaaFramework/Instance/MaaContext.h`、`include/MaaFramework/Instance/MaaController.h`。

**代码级细节（决定了架构与性能上限）：**

- **`override` 是字段级增量**，不是整节点替换：解析顺序 `override → 资源同名节点 → 全局默认`，逐字段回退（`source/Task/Context.cpp:415-431`）。因此可以注入全新节点、把任意新名字当 entry、甚至 `next: []` 只跑单节点。但 `next`/`on_error` 里引用的节点必须存在（`source/Resource/PipelineChecker.cpp:56-70`），否则整个 `PostTask` 直接失败——LLM 生成 pipeline 时必须先做引用校验。
- **运行中改参数**：`MaaTaskerOverridePipeline(task_id, override)`（`include/MaaFramework/Instance/MaaTasker.h:91`），对应本仓库 `set_task_params` 的能力，是"自由运行"的关键。
- **串行与节奏**：每个 Tasker / Controller 各一个 worker，任务串行；一轮"截图 + 识别 + `rate_limit`（默认 1000 ms）"约 1 Hz（`source/Task/PipelineTask.cpp:154-191`）。**Agent 的 observe-act 循环不要用完整 pipeline 轮次**，应直接用 `post_recognition` / `post_action` / `MaaContext` 原语，否则每一步都慢 1 秒。
- **版本与 ABI**：没有 ABI 版本宏，只有 `MaaVersion()`；Agent IPC 协议是 `kProtocolVersion=8`（`source/include/MaaAgent/Message.hpp:17`），**整套（core/control unit/binding/agent）必须同版本发**。
- **Custom/Agent 的边界**：AgentServer 走 ZeroMQ + JSON IPC，注册全局单例、单消息线程串行、默认无超时；**Agent 进程不能创建 Controller**；agent 侧可以在回调里反向同步调 `run_task` / `override`（`source/MaaAgentServer/RemoteInstance/RemoteContext.cpp:15-120`）。官方建议"复杂业务逻辑放 Agent"，但 **LLM 决策应放 in-process custom / 宿主进程**，不要塞进 Agent（否则超时、断线与单线程都会成为故障源）。
- **CustomController** 约 20 个回调（connect/screencap/click/swipe/touch_*/key_*/input_text/start_app/stop_app/shell/get_info，`include/MaaFramework/Instance/MaaCustomController.h`），在 ControllerAgent 单线程同步执行、无单次超时；坐标默认按短边 720 缩放，下发前由 `preproc_touch_point` 换回原始像素（`source/MaaFramework/Controller/ControllerAgent.cpp:1220-1240`）。
- **AndroidNativeControlUnit** 只有 Android 编译（`source/MaaFramework/API/MaaFramework.cpp:100-108`），宿主 `.so` 只需导出三个符号（`source/MaaAndroidNativeControlUnit/General/AndroidExternalLib.h:96-100`）；`click/swipe` 被禁用、改走 `touch_down/move/up`，且 `screen_resolution` 必须严格一致。
- **Android 产物**：只有 `.so` zip（NDK r29、android-23、arm64-v8a/x86_64，`.github/workflows/build.yml:435-529`），**没有 aar**；MaaFramework 本体是 SHARED，control unit 是独立 `.so`，按名 dlopen 并校验版本（`source/LibraryHolder/ControlUnit/ControlUnit.cpp:113-121`）。宿主必须自己打包 `.so`、写 JNI/JNA、实现控制单元——MaaFwApp 已经做完这些。

### 2.2 Aliothmoon/MAA-Meow — Android 上跑 MaaCore 的完整样板

- 定位：**MaaCore 的 Android 外壳**（Compose + Material3 + Koin，minSdk 28，AGPL-3.0，1734★），仅明日方舟，覆盖 6 个渠道包，共 17 种 MAA 任务（含 Copilot/SSS/Paradox/Custom），有定时、Copilot、Webhook、无 LLM。
- 内核：`libMaaCore.so` 取自 MAA 上游 Release 的 `MAAComponent-<tag>-android-*.tar.gz`（实测 v6.20.1 存在），JNA 直接调；MaaCore 运行在 **Shizuku/root 拉起的 shell uid 特权进程**里。
- 控制链路（不用 adb / MediaProjection / 无障碍）：`liblauncher.so`（可执行体伪装 .so）→ `setresuid(2000)` → `exec app_process`，CLASSPATH=APK；AIDL `RemoteService` 暴露控制面。截屏 = 原生拿 Surface → `createNewVirtualDisplay` + `AImageReader`/`AHardwareBuffer` 取帧（前台模式镜像 display 0）；注入 = hidden `InputManager.injectInputEvent` + `setDisplayId`（DOWN 同步、MOVE/UP 异步）；起 App = 绕过 BAL 的 `ActivityUtils`。
- 与 MaaFramework 的关系：**只用它的 `libMaaAndroidNativeControlUnit.so`**，通过 `libbridge.so` 实现 `AndroidExternalLib` 的 `GetLockedPixels` / `UnlockPixels` / `DispatchInputMessage` 三符号（多指需 ≥ v5.13.0-beta.3）；无 fw core、无 `interface.json`。
- **完整报告补充的定位**（dsh-go 深挖后修正）：它**不是 fork**，而是"客户端 + 官方引擎"——在手机里跑官方 `@deepseek-ai/dsh@0.2.1-rc.4`（Ubuntu 24.04 + Node 24），按 DSH 版本哈希打补丁（`runtime/patches/pins.json`），再注入 `dsh-go-bridge` 与 MCP overlay；UI 按官方 `dsh-client-ui-*` 逐项对齐。工具边界用 MCP：手机能力是固定 schema 的 `mcp__phone__*`（明确为了保模型 KV cache），shell/文件工具跑在 rootfs 或远程主机。审批是两层：引擎权限预设（只读/工作区内/自动审查/完全权限）+ App 三档风险确认 + 应用级 deny 名单。远程两条路：SSH（TOFU 指纹 + 本地转发）与第三方中继（Noise IK E2EE、固定端点 allowlist）。
- **四个可直接借的模式**：① **引擎外置**（`DshCarrier` 抽象让本地/远程共用同一套客户端 API）；② **MCP 固定工具目录**（`mcp__maa__*` 的 schema 稳定以保 KV cache）；③ **审批金字塔**（引擎预设 + 三档风险 + 应用级名单/按住确认）；④ 结果 meta 驱动原生工具卡 + transcript reducer/排队插话/断线重订阅/安全模式 + LKG 回滚。
- **对 v1 的直接结论**：MAA 有 Android 原生 `.so`，**不需要 dsh-go 那套 1 GB rootfs/proot**；但"引擎外置 + App 只做 UI/审批/工具桥"的架构可以直接借用——MaaCore 就是引擎（跑在特权进程），App 是客户端。
- 对我们的价值：**"Android 上把内核跑在特权进程 + 自绘虚拟屏 + 注入"的工程细节全在这里**，包括 Shizuku 杀进程发 SIGTERM 必须多一层 launcher、厂商 ROM 坑、FGS/闹钟/唤醒解锁、资源 stamp 重解。不能用的部分：MaaCore 任务语义、Resource 模板、渠道包名、720p 识别假设。

### 2.3 RikkaApps/Shizuku — 特权来源

- 本质：用 adb/root 以 `app_process` 常驻的 Java 进程 + binder 中间人，把 uid 2000/0 的系统 API 能力借给普通 App；**不是 hook 框架，不解除 app 进程的 hidden API 限制**（UserService 进程不受限）。minSdk 24、compileSdk 36，Android 11+ 用无线调试启动，重启后非 root 需手动再启一次。
- 对自动化的能力（判定 = 能做到）：
  - 截屏：`screencap` 100–400 ms/次；**虚拟屏 + ImageReader 16–33 ms/帧（最佳）**；MediaProjection 只能采集且 Android 14+ 每次要确认。
  - 触摸：`injectInputEvent` 亚毫秒、多指；`input tap/swipe` 每次 fork 进程 50–150 ms（兜底）。点击吞吐约 10–20 次/秒（UserService）vs 3–6 次/秒（input 命令）。
  - 按键 / 启动停止 App / 读写设置：可以。**文本输入只有 ASCII**（`input text` 中文无效），需要 IME / 剪贴板 / ADBKeyboard 另做通道——LLM 场景中文是常态，这是硬缺口。
  - UI 层级：`uiautomator dump` 0.5–2 s（慢）。
- 安全与可检测性：授权粒度只有"整个 shell 身份"一个开关；注入事件 `deviceId=0`、`pressure/size` 恒定、`KeyEvent` 无 `FLAG_FROM_SYSTEM`——无对抗游戏够用，有反外挂不够；Android 15 起 app 还能感知录屏。
- 替代方案对比（截图/触摸/授权成本/兼容/隐蔽/开发量综合）：Shizuku 是 Android 不 root 的最高性价比；无线 ADB 自连次优（重启要重开无线调试，`localhost:5555` 只在已 `tcpip 5555` 时成立）；AccessibilityService 保底但慢（`takeScreenshot` API 30+ 有 333 ms 限频）；MediaProjection 只能采集；root 最强但门槛高；iOS 除越狱/PlayCover 无解。
- 对 MaaFramework 的对接优先级：**`MaaCustomControllerCreate` 首选**（回调覆盖 connect/screencap/click/swipe/touch_*/key/input_text/start_app/stop_app/shell/inactive，Shizuku 除中文 `input_text` 外都能实现）；`MaaAndroidNativeControlUnit` 次选（只需 3 个符号，但分辨率必须严格一致，且 MAA-Meow 的 bridge 里 `INPUT`/`STOP_GAME` 未实现）。

### 2.4 ccch1mneyyy/dsh-go — 手机上的 Agent 宿主要怎么长

- 定位：**DeepSeek Harness 的 Android 客户端**（Flutter 3.47 + Kotlin，AGPL-3.0，667★）：手机上直接跑 DSH，也能遥控电脑上的 DSH。
- 本地运行时：App 自带 **Ubuntu 24.04 + Node + `@deepseek-ai/dsh`，经 proot 运行**（免 root、免 Termux）；proot 等可执行文件以 `lib*.so` 放 jniLibs、`useLegacyPackaging=true` 使其可执行；`targetSdk=28`（与 Termux 相同）避开 Android 10+ 私有目录 exec 限制。
- 连接方式：原生层起 `dsh web --host 127.0.0.1 --port 3080`，从 stdout 解析 token；Dart 侧 `packages/dsh_client` 用 token 换 cookie，之后 HTTP/WS 带 cookie；WebView 兜底。远程模式是另一个纯 Dart 包 `dsh_remote`。
- 产品细节直接可抄：首次引导（本机安装运行时 → 登录/填 Key，或连远程主机）、消息排队/打断/过程折叠、原生 UI 与 WebView 同一段会话、端点切换、引擎状态与日志页、前台服务与进程管理。
- 产品面比"聊天壳"大得多：`packages/dsh_client` 覆盖会话/文件/终端/语音/插件/订阅/agents 等一整套 RPC（`rpc.dart`/`mux.dart`/`connection.dart`），`runtime/bridge/dsh-go-bridge` 是注入 DSH 的 Node 插件（cordis patch + native capabilities），`packages/dsh_remote` 与本地共用同一套连接模型。它走的是"DSH 生态的移动端全家桶"路线。
- 对我们的价值：**证明"把 agent 运行时塞进手机"的工程路径是 proot + 本地 HTTP/WS + 原生 UI**；也证明"同一套会话既能本机跑也能连电脑"是可接受的 UX。代价是 APK 体积与启动复杂度（一整套 Ubuntu+Node）、`targetSdk=28` 的合规风险（为避开 Android 10+ 私有目录 exec 限制，与 Termux 同策略，不适合上架商店）。

---

## 3. 生态现状与竞品：缺口在哪

| 项目 | 是什么 | 已有能力 | 缺什么（我们的机会） |
|---|---|---|---|
| **MaaMCP**（Maa-AI/MaaMCP，MistEO，394★，AGPL） | 基于 MaaFramework 的 **MCP server**（Python + fastmcp + `maafw`） | 设备发现/连接（ADB/Windows 窗口）、OCR、截图、点击/滑动/输入/按键、Pipeline 的读写与 `run_pipeline`（含单次 `pipeline_override`、`benchmark_node`、多文件加载）、后台流水线模式、从 `interface.json` 读 agent 配置拉起 AgentServer | 只面向桌面 AI 助手（Claude/Cursor/Cherry Studio）；**没有 PI 任务语义层**（不按 `task`/`option` 暴露工具）；没有 App、没有确认/审计、没有多项目管理；移动端无解 |
| **MaaAgent**（MAA-AI/MaaAgent） | Cherry Studio 的 fork，尝试做 MAA 桌面 AI 客户端 | 继承 Cherry Studio 的 LLM 客户端与 MCP 能力 | 2025-12 创建、2026-01 后停更（0★）；说明这个方向有人试过并放弃——产品难点不在客户端 |
| **MaaFwApp**（Aliothmoon，52★，AGPL） | MaaFramework 的 Android 通用 GUI | PI V2 全量、agent 子进程、后台虚拟屏、定时/通知/日志/遥测 | **没有意图层**（只能手动填表单）；资源构建期打进 APK，**一项目一 APK**，不能装多个项目 |
| 通用 UI：MFAAvalonia / MXU / MWU | 桌面/Web 的 PI 任务表单 | 表单化执行、资源管理、更新 | 同样没有自然语言/Agent 层 |
| MAA 侧：`maa-cli` + `maa-dsh-skill`（5★，MIT） | DSH Skill 包装 MAA 官方 CLI，驱动 MaaCore 跑明日方舟日常 | 桌面端"自然语言 → maa-cli 命令"的最小可行形态 | 只覆盖 MAA/明日方舟，依赖桌面与 CLI |
| 本仓库 MAA-API | MaaCore 的 FastAPI 服务 + 内置 Agent | 84 个工具、PolicyEngine、确认/会话授权、审计、知识库、PRTS/企鹅物流、热更新、PWA | 只服务明日方舟；执行后端绑死 MaaCore/ADB |

**一句话：执行层（MaaFramework/UI/MCP）已经拥挤，意图层（PI 任务语义 + 产品级 App + 安全治理）还是空的。**

相邻生态还有 `maafw-cli`、`MaaCtl`、`create-maa-project`（MCP）、`MaaEvidenceKit` 等项目（MaaFramework README 的生态列表），说明上游在往「CLI / AI 工具链」方向走——**我们不要重复做工具链，要做产品与语义层**；必要时直接消费它们的输出（例如用 CLI 做资源校验、用 MCP 做桌面自动化）。

---

## 4. 技术基线：决定了上限的几条事实

### 4.1 PI V2 是一份现成的 LLM 工具 schema

`interface.json` 的 `task` 与 `option` 恰好是我们需要的结构：

| PI 字段 | LLM 工具映射 |
|---|---|
| `task[].name` / `label` / `description`（支持 i18n、Markdown） | 工具名（需稳定的 sanitize 映射）与工具描述 |
| `task[].entry` | 执行时传给 `post_task` 的入口节点 |
| `task[].group[] / icon` | 工具分组（按需暴露，控制上下文） |
| `task[].resource[] / controller[]` | 工具可用性过滤（当前项目/控制器不支持就不暴露） |
| `task[].option[]`（有序） | 参数列表与顺序 |
| `option.type = select/switch/checkbox` | `enum` / `boolean` / `array<enum>` |
| `option.cases[].label/description/pipeline_override` | enum 值 + 值说明；选中后产生的节点补丁由客户端计算 |
| `option.default_case` | 参数默认值（`select`/`switch` 单值，`checkbox` 数组） |
| `option.type = input` + `inputs[]`（`name/label/description/default/pipeline_type/verify/pattern_msg/password/input_type`） | `object` 子字段；`pipeline_type` 决定替换时的类型；`password` 要加密存储且不进日志 |
| `option.type = hotkey` + `hotkeys[]` | 字符串（`Ctrl+A` 形式），由客户端按控制器类型映射键码 |
| `option.option[]`（嵌套，子选项只在父项选中时生效） | 依赖参数（展平为 `if parent == X` 的说明或分组工具）；**不能简单全暴露** |
| `option.pipeline_override`（含 `{名称}` 占位符替换） | 客户端合并，不交给 LLM |
| `option.min_count` / `max_count` | `minItems` / `maxItems` |
| `global_option` / `resource.option` / `controller.option` | 全局/资源级/控制器级参数，参与所有任务 override |
| `preset`（预设：勾选任务 + option 取值 + pretask） | "一键方案"工具：`apply_preset(name)` |
| `agent`（`child_exec/child_args/identifier`） | 项目自带 CustomRecognition/Action 子进程；客户端须注入 `PI_*` 环境变量（v2.5+） |
| `pretask`（控制器启动前执行的程序，v2.7+） | 启动前置步骤，可能涉及设备/环境准备 |

这条映射是"agent 90% 的工作停在上层"的基础（与 MAA-API 的三层能力模型同构：任务编排 > 自定义 > 原子操作）。

**但 PI 不够直接做 function calling，有五个缺口必须在工具层补：**

1. **没有输出契约**：`task` 只声明入口，不说明成功长什么样、会消耗什么、有什么前置条件（游戏是否在主界面、是否已登录、体力够不够）。工具描述必须由适配层补"何时用 / 何时别用 / 失败怎么办"，否则模型会把所有任务当黑盒乱试。
2. **没有互斥与依赖**：`option` 之间、`task` 之间没有声明关系；"先刷材料再合成""这个选项和那个选项不能同时开"只能靠人工适配或运行时约束（MAA-API 的 `preconditions.py` 是同类补丁）。
3. **类型表达不全**：没有数组/浮点参数，`pipeline_override` 的值没有强类型 schema；`input` 子字段靠 `pipeline_type` 做字符串替换，校验只有正则。
4. **`option` 的嵌套与作用域复杂**：子选项只在父项选中时生效、`global_option`/`resource.option`/`controller.option` 参与所有任务 override、`preset` 还能覆盖取值——合并逻辑必须由客户端实现（MaaFwApp 的 `PiParser`/resolver 已实现，可复用），**LLM 只出语义参数**。
5. **PI 只覆盖"预定义任务"**：真正的"自由运行"要靠 `MaaResourceGetNodeList` / `MaaResourceGetNodeData`（`include/MaaFramework/Instance/MaaResource.h:60,74`）枚举 Pipeline 节点，再把任意节点当 entry 跑（`next: []`）。这条是 L3 原语层与 PI 层之间的桥。

### 4.2 MaaFramework 的原语比 MaaCore 丰富

MaaCore 只有 `AsstAsyncClick` / `AsstAsyncScreencap` / `AsstBackToHome`，滑动/长按/文本都要走 ADB，OCR 只能经 task 间接调用（本仓库 docs/11 §2.1 已经踩过）。

MaaFramework 的 `MaaContext` 直接给：`RunTask`、`RunRecognition`（含 `RunRecognitionDirect` 指定算法与参数）、`RunAction`（含 Direct）、`WaitFreezes`、`OverridePipeline`、`OverrideNext`、`OverrideImage`、`GetNodeData`、anchor/hit count、`Clone`。

**这意味着第二层 agent 不必全靠多模态 VLM 看截图**：模板匹配 / OCR / 颜色匹配都能以结构化结果直接返回，token 与延迟都低一个量级；VLM 只用于"识别不了的未知界面"和"给用户看"。这是比 MAA-API 更有利的条件。

**但要正视它的节奏**：Tasker / Controller 各一个 worker，任务串行；一轮完整流水线（截图 + 识别 + 默认 `rate_limit` 1000 ms）约 1 Hz。Agent 的"看一步、想一步、动一步"必须走 `post_recognition` / `post_action` / `MaaContext` 原语而不是反复 post 完整 task，否则每一步都要等 1 秒。

**自由运行的桥**：`MaaResourceGetNodeList` / `MaaResourceGetNodeData` 能枚举资源里全部 Pipeline 节点并读出节点定义（`include/MaaFramework/Instance/MaaResource.h:60,74`）。"用户说'跑一下 XXX 节点'"就靠它——这是 PI（预定义任务）之外真正意义上的"自由运行 MAA 中配置的东西"。

### 4.3 Android 执行链路（已被两个项目验证）

```
App 进程（Compose UI / agent / 资源管理）
   │ AIDL（控制面 + 回调）
   ▼
特权进程（Shizuku bindUserService 或 root 拉起，uid shell/root）
   ├─ MaaFramework 本体（JNA / C API）
   ├─ Controller：AndroidNative（3 符号外部库）或 CustomController
   ├─ MaaAgentClient → 项目 agent 子进程（Python 等）
   └─ 设备能力：VirtualDisplay + ImageReader 取帧 / injectInputEvent 注入 /
                解锁、亮屏、改分辨率、停目标 App、授权、看门狗
   ▼
MaaResource（PI 项目资源 Bundle：pipeline/image/model）
```

已验证的组合：
- MAA-Meow：MaaCore + MaaFramework 的 AndroidNative 控制单元 + 自研 `libbridge.so`（特权重、零弹窗、需 Shizuku/root）。
- MaaFwApp：MaaFramework 本体 + AndroidNative controller（JNA 手写 C API）+ Shizuku/root 特权进程 + 虚拟屏（后台模式）。
- 两者共同结论：**Android 上不需要 adb，也不需要 MediaProjection**；代价是必须引导用户装 Shizuku（或 root），且非 root 的 Shizuku 每次重启要手动启一次。

**控制层选型的取舍（重要）**：

| | AndroidNativeControlUnit | CustomController |
|---|---|---|
| 实现量 | 宿主 `.so` 只导出 3 个符号（GetLockedPixels/UnlockPixels/DispatchInputMessage） | 宿主实现约 20 个回调（含 input_text / start_app / stop_app / shell / get_info） |
| 输入能力 | `click/swipe` 被禁用，只能 touch_down/move/up；文本与 stop 若宿主不实现就没有 | 回调齐全，`input_text` 可走 IME/剪贴板；`start_app/stop_app` 可控 |
| 坐标 | `screen_resolution` 必须与原始截图严格一致，错误直接失败 | 缩放坐标由框架换算回原始像素，更宽松 |
| 现成实现 | MaaFwApp / MAA-Meow 已跑通（推荐 v1 沿用） | MaaFwApp 未用；要自己写，但更完整 |
| 结论 | **v1 沿用 MaaFwApp 的 AndroidNative 路径**；需要文本/停止能力时切换或补 CustomController | v2 视需求迁移 |

**中文输入**：Shizuku 的 `input text` 只有 ASCII，这是 LLM 场景的硬缺口。MaaFwApp 已经给出做法——特权进程提供 `ITextInputSink`（AIDL），把"按键打不出来的文本"交给无障碍服务写入（`RemoteService.aidl` 的 `setTextInputSink`），资源侧 `InputText` 动作走这条通道。MaaCore 侧则要自己在工具层接 IME/剪贴板。


### 4.4 MAA 与 MaaFramework：共控制面，不共任务语义

- MAA 官方 Release 已提供 Android 产物（`MAAComponent-<tag>-android-*.tar.gz`），并捆绑 MaaFramework 的 `libMaaAndroidNativeControlUnit.so`；MAA-Meow 用它 + 自己的 `libbridge.so` 跑在特权进程里。
- 所以**设备控制层可以共用**（一个特权进程同时服务两套内核在技术上是通的），但**任务语义层不能共用**：
  - MaaCore 的任务是 C++ 里实现的业务任务（Fight/Recruit/Mall/Copilot…），参数是 MAA 自定义的 JSON，`tasks.json` 是 MAA 的资源格式；
  - MaaFramework 的任务是 Pipeline 节点图 + PI 声明，没有 Fight/Recruit 这些语义。
- 想"一个 App 同时支持明日方舟和别的游戏"，要么双引擎（两套工具、两套资源、两套日志/错误模型），要么只做 MaaFramework 并接受明日方舟只有社区 PI 项目（如 `LuoMu0OTU/arknights-auto`、`SimonQvQ/MAAi`，成熟度远不如 MAA）。
- **建议 v1 只做 MaaFramework**；明日方舟用户继续走 MAA 官方客户端 / 本仓库 MAA-API。双引擎是 v2 之后的选项。

---

## 5. 产品形态：三个方案

| | A. Android 单机 Agent App | B. PC 宿主 + 手机客户端 | C. MCP / Skill 形态 |
|---|---|---|---|
| 执行后端 | v1：MaaCore（MAA）+ 特权进程（Shizuku/root）；v2：加 MaaFramework 适配器 | MaaFramework（Python binding）或 MaaCore（本仓库） | 桌面 MaaFramework（MaaMCP 已有） |
| Agent 跑在哪 | 手机上（Kotlin 或内嵌 Python/Node） | 电脑上的服务 | 用户的 AI 客户端里 |
| 基座 | v1：MAA-Meow 的内核/设备层；v2：MaaFwApp 提供 PI 参考 | 改本仓库 MAA-API 或新写 | fork/复用 MaaMCP |
| 优点 | 不依赖电脑；后台虚拟屏可挂机；与 MaaFwApp 生态一致 | 复用 MAA-API 的 84 工具/确认/审计/知识库；算力与网络好；可跑 MaaCore 深度内容 | 成本最低；马上能用（DSH/Claude/Cursor 都能接）；不碰分发合规 |
| 缺点 | Shizuku 门槛；APK 体积；LLM 成本；项目资源打包/分发难 | 必须有电脑；手机只是遥控器；两套代码 | 不是 App；无移动端；MaaMCP 已占位，差异化难 |
| 建议 | **主线（v1）**：MaaCore + 泛化任务层 | 第二后端（v2） | 低成本试验/开发者入口 |

**推荐组合（按 §1.1 拍板修订）**：v1 做 A 的单项目 MVP——**取 MAA-Meow 的内核/设备层 + 新写对话式外壳与意图层**；v2 把 B 作为远程模式、并把 MaaFramework 适配器接进同一个 `EngineAdapter`（参考 MaaFwApp 的 PI 实现）；C 不自己造，优先与 MaaMCP 互补（MaaMCP 做低层设备/桌面自动化，我们做任务语义与 App）。

---

## 6. 架构：v1（MaaCore）与 v2（MaaFramework）

### 6.1 v1 架构（MaaCore + 泛化任务层）

```
┌─────────────────────────────────────────────────────────────┐
│ UI 层（Compose）：对话页 / 任务卡 / 确认卡 / 运行日志 / 设置      │
├─────────────────────────────────────────────────────────────┤
│ 意图层（新）：AgentRuntime                                    │
│  ├─ LLM 客户端（OpenAI 兼容，流式，tool calling）              │
│  ├─ ToolRegistry：TaskSpec 工具 + 原语工具 + 交互工具           │
│  ├─ PolicyEngine：风险分级 → 直执行 / 需要确认 / 会话授权        │
│  └─ 会话持久化（Room）、审计、token 统计、提示词装配              │
├─────────────────────────────────────────────────────────────┤
│ 任务层（泛化，新）：TaskSpec + EngineAdapter                   │
│  ├─ MaaCoreAdapter（v1）：MAA 任务目录 + 参数 schema +          │
│  │    Profile→TaskChain→AsstAppendTask/AsstSetTaskParams       │
│  └─ MaaFrameworkAdapter（v2）：PI task/option → TaskSpec        │
├─────────────────────────────────────────────────────────────┤
│ 执行层（取 MAA-Meow 的内核/设备层）：AIDL → 特权进程             │
│  ├─ MaaCore（JNA）+ libMaaAndroidNativeControlUnit + libbridge │
│  ├─ VirtualDisplay + AImageReader / injectInputEvent           │
│  └─ 解锁、亮屏、FGS、闹钟、看门狗、日志回捞                      │
└─────────────────────────────────────────────────────────────┘
```

**新写的部分**：UI、意图层、任务层（`TaskSpec` / `EngineAdapter` / `MaaCoreAdapter`）。
**复用/移植的部分**：
- MAA-Meow 的 `privileged/`、`remote/`、`manager/`、`maa/`、`native/`（AGPL，保留版权声明）——这是 Android 上唯一被验证过的 MAA 特权执行栈。
- 参数 schema 与默认值：本仓库 `maa_api/agent/tools/task_schema.py`、`maa_api/services/task_defaults.py`（MAA v6.17.5 实测默认值），移植到 Kotlin 或作为数据文件。
- 确认/授权/审计/提示词：`docs/11-Agent模块设计.md` 的设计，Kotlin 重写（或内嵌 Python，见 §11）。
**与 MaaCore 的边界**：任务只走 `AsstAppendTask` / `AsstStart` / `AsstStop` / `AsstSetTaskParams`；`Custom` 任务与注入的 `tasks.json` 走 L2；原子操作走 `AsstAsyncClick` / `AsstAsyncScreencap` / `AsstBackHome` + 特权进程输入。
**最大能力差**：MaaCore 没有独立 OCR/识别 API，L3 的画面判断只能"截图 + VLM"；结构化识别要等 v2 的 MaaFramework 适配器。

### 6.2 参考形态：MaaFramework / PI（v2）

下图是拍板前按 MaaFwApp 画的草图，保留作为 v2 接 MaaFramework 时的参考：

```
┌─────────────────────────────────────────────────────────────┐
│ UI 层（Compose）：对话页 / 任务卡 / 确认卡 / 运行日志 / 设置      │
├─────────────────────────────────────────────────────────────┤
│ 意图层（新）：AgentRuntime                                    │
│  ├─ LLM 客户端（OpenAI 兼容，流式，tool calling）              │
│  ├─ ToolRegistry：PI 任务工具 + 原语工具 + 交互工具             │
│  ├─ PolicyEngine：风险分级 → 直执行 / 需要确认 / 会话授权        │
│  ├─ 会话与消息持久化（SQLite/Room）、审计、token 统计            │
│  └─ 提示词装配：项目说明(PI description/welcome) + Pipeline 协议 │
│     + 当前状态摘要 + 工具目录（按需暴露）                        │
├─────────────────────────────────────────────────────────────┤
│ 任务语义层（复用 MaaFwApp）：PiParser / ProjectLoader / Resolver │
│  → RunPlan{tasks[], options→有序 pipelineOverrides, agents, preset} │
├─────────────────────────────────────────────────────────────┤
│ 执行层：RunnerPort（MaaFwApp 已有）→ AIDL → 特权进程            │
│  ├─ MaaFramework：Resource / Controller / Tasker / AgentClient │
│  └─ 设备：虚拟屏采集、注入、解锁、看门狗、分辨率                  │
└─────────────────────────────────────────────────────────────┘
```

**这个形态相对 MaaFwApp 新增的部分**：
1. **意图层**：Kotlin 实现的 agent 循环（LLM 流式 + tool 调用 + 确认 + 会话）。
2. **工具层**：把 PI 与 MaaFramework 原语包成工具，并把执行请求翻译回 `RunPlan` / 原语调用（v2 的任务层直接复用这张图里的 PiParser/RunPlan）。

**需要给特权进程补的控制面**（MaaFwApp 现有 AIDL 只有 `startRun(RunPlanJson)` / `stopRun` / 少量设备方法）：
- `postTask(entry, overrideJson)` → taskId、`overridePipeline(taskId, override)`、`stopTask(taskId)`
- `screencap()` → 文件路径或共享内存（binder 1 MB 限制，MaaFwApp 已用 `saveCachedImage`/`saveDisplayFrame` 落文件）
- `runRecognition(entry | directType+param, image?)` → 结构化结果（box/score/text）
- `runAction(entry | directType+param)`、`click/swipe/key/text`（文本走 IME/剪贴板通道）
- `waitTask(taskId, timeout)` 或继续用回调事件流（MaaFwApp 已有 `IMaaRunnerCallback`）
- 事件流：复用 MaaFramework 的 `Node.*` / `Tasker.Task.*` 回调，转成 UI 与 agent 都能消费的有序事件

---

## 7. Agent 工具设计

### 7.1 四层工具（与 MAA-API 的三层能力模型对齐）

| 层 | 工具（示例） | 风险 | 说明 |
|---|---|---|---|
| L0 会话/项目 | `list_projects`、`get_project_info`、`list_tasks`、`get_task_detail`、`get_status`、`get_screenshot`、`list_presets` | SAFE | 只读；`get_screenshot` 的图给模型看时叠坐标网格（MAA-API M57 经验） |
| L1 任务执行（主路径） | `run_tasks([{task, options}...])`、`stop_run`、`wait_run`、`get_run_log`、`apply_preset` | CONDITIONAL | 90% 的请求应停在这里；参数用 PI 生成的 schema；含消耗/破坏语义时确认 |
| L2 参数编辑 | `set_task_options`（运行前）、`override_pipeline(taskId, patch)`（运行中，对应 `MaaTaskerOverridePipeline`） | CONDITIONAL | 运行中改参数是 MaaFramework 特有优势（对应 MAA-API 的 `set_task_params`） |
| L3 原语（兜底） | `list_nodes` / `get_node_data`（枚举资源全部 Pipeline 节点）、`run_node(name)`、`run_pipeline(entry, nodes)`、`run_recognition(entry/type+param)`、`run_action`、`click/swipe/key/input_text`、`wait_freezes`、`start_app/stop_app` | DANGEROUS / 会话授权 | "自由运行 MAA 中配置的东西"就在这里：节点枚举 + 任意 entry + `next: []` 单节点执行；LLM 写 pipeline 只在 `run_pipeline` 一个工具里出现 |
| L4 知识/交互 | `get_pipeline_protocol`、`read_project_doc`、`ask_user`、`confirm`、`send_screenshot` | SAFE | 协议文档按需注入（MaaMCP 的做法），不要常驻上下文 |

**v1（MaaCore）与上表的差异**（工具层按 `TaskSpec`/`EngineAdapter` 抽象，下面的差异只影响 v1 适配器）：

- **L1 任务**：来自 MAA 的内置任务目录（Fight / Recruit / Mall / Infrast / Award / Roguelike / Copilot / SSS / Paradox / StartUp / CloseDown / Custom…），参数用适配层内置 schema（不是 PI）；`run_tasks` 一次 append 多个任务再 `AsstStart`，语义与 MAA 一致。
- **L2 参数编辑**：`set_task_params` 对应 `AsstSetTaskParams`；Copilot 作业、自定义基建方案、注入 `tasks.json` 走资源类工具（与 MAA-API docs/11 §2 的第二层一致）。
- **L3 原语**：`click` / `screencap` / `back_to_home` 走 MaaCore；`swipe` / `long_press` / `input_text` / `key_event` 走特权进程（MaaCore 没有这些 API）；**没有 `run_recognition` / `list_nodes`**（MaaCore 无独立识别 API、无节点枚举），替代是截图 + VLM、`Custom` 任务、`AsstGetMapLevelKey` 这类专用 API。
- **L4 知识**：直接搬 MAA-API 的 33 条内置知识（任务前提、失败特征、渠道、活动），另可接 PRTS/企鹅物流（需联网白名单）；协议文档从 Pipeline 协议换成 MAA 的 task-schema 与资源说明。

### 7.2 从任务模型生成工具 schema 的规则

v1 的 task 目录来自 MAA 内置任务（参数 schema 由适配层内置，可搬 MAA-API 的 `task_schema.py`/`task_defaults.py`）；v2 换成 PI 的 `task`/`option`。下面的规则对两者通用。

- **工具名**：`<project>.<task.name>` 的稳定映射（sanitize 非法字符 + 保留原名对照表）；一个 App 只装一个项目时可以直接用 `task.name`，多项目必须加前缀。
- **描述**：`task.label` + `task.description`（Markdown 降级为纯文本/要点）+ 一句"何时用/何时不该用"（可自动生成，也可由项目作者补充）。
- **参数**：只暴露 `task.option[]` 里列出的选项（PI 明确"Client 可以使用 option 中的顺序展示"）；未列出的 `option` 不进 schema。嵌套 option 用 `if/then` 或"父参数选中后子参数才有效"的说明表达，**不做隐式默认**。
- **默认值**：`default_case` / `inputs[].default` → schema `default`；没有默认的必填参数要在确认卡上显式展示。
- **校验**：`min_count`/`max_count` → `minItems`/`maxItems`；`inputs[].verify` → pattern；`password` → 不落日志、加密存储。
- **执行**：LLM 只产出结构化参数；`pipeline_override` 的合并（option 选中→case 的 override、嵌套、全局/资源/控制器级 option、preset、`{占位符}` 替换）全部在客户端按 PI 规则计算——复用 MaaFwApp 的 `PiParser`/resolver，不要让 LLM 碰。
- **规模控制**：项目任务多时按 `task.group` 做工具分组、按需 `enable_tool_group`（MAA-API M57 的做法）；实测一次全量 schema 可能上万 token。

### 7.3 确认、授权与审计（照抄 MAA-API 的结论）

- 风险分级：`SAFE`（只读）/ `CONDITIONAL`（按参数判定）/ `DANGEROUS`（总是确认）。
- 消耗类判定要落在**内核实际会收到的参数**上（对应 MAA-API `_consumption_reasons` 的口径修正）：例如任务含"碎石/用药/购物/投资"语义时，按合并默认值后的 pipeline override 判断，取不到就 fail closed。
- 原子操作走**会话级授权**（首次弹一次、默认 15 分钟、可撤销、审计不降级）；消耗/破坏类仍逐次确认。
- 确认渠道：App 内卡片 + 系统通知（Chromium/Android 支持按钮；提醒打开 App 深链回确认卡）。
- 审计全量落库：调用方、时间、参数、结果、耗时、授权来源。
- **新增 Android 特有的风险**：改虚拟屏分辨率/主屏分辨率（MaaFwApp `setForcedDisplaySize`）、解锁与亮屏、停目标 App、给 App 授权（`grantPermissions`）、断网（`setPackageNetworkingEnabled`）——这些在 MaaFwApp 里是可用的，必须列入 DANGEROUS 或至少显式确认。

**LLM 决策放哪一层**（来自 MaaFramework 的约束）：放**宿主进程的 in-process custom / Kotlin agent loop**，不要塞进 AgentServer。AgentServer 是单线程、全局注册、默认无超时、不能建 Controller 的隔离进程；把 LLM 循环塞进去会把"模型慢"变成"框架卡死"。正确分工：AgentServer 只跑项目自带的识别/动作，宿主负责决策与工具调度。

### 7.4 上下文与成本

- 截图是最大成本项：优先用 `run_recognition` 的结构化结果（OCR 文本、box、score）；VLM 只在必要时看压缩后的截图（Maa-API 的视觉成本折算经验）。
- 项目知识：PI 的 `description`/`welcome`/任务说明 + Pipeline 协议摘要 + 常用术语表；按需 `get_pipeline_protocol` 全文注入。
- 工具结果预算与裁剪：MAA-API 的 M56/M57 经验（模型侧 schema 覆盖、输出投影、工具分组）。
- 多步任务的状态机：`run_tasks` 是异步的，agent 应拿到事件流（`Node.*`、任务完成/失败）而不是阻塞轮询；失败时给"看截图 → 诊断 → 重试/换方案"的标准流程（MAA-API 的自动诊断设计）。

---

## 8. 决策清单（D1–D4 已拍板，其余待定）

| # | 决策 | 选项 | 推荐 | 影响 |
|---|---|---|---|---|
| D1 | 主形态 | A 手机单机 / B PC 宿主 / C MCP | ✅ **已拍板 A**（B 作 v2 远程后端） | 决定基座与工程量 |
| D2 | 引擎范围 | 只 MaaFramework / 只 MaaCore / 双引擎 | ✅ **已拍板：v1 只做 MaaCore，但任务层抽象为引擎无关**；MaaFramework 作 v2 第二适配器 | 好处是 v1 立刻有真实内容（明日方舟）；代价是 v2 的 MaaFramework 适配器是第二套执行后端、工作量不会少太多。MAA task-schema 与 MaaFramework 协议不兼容、无官方 converter（`source/Resource/PipelineParser.cpp:257-279` 拒绝 `is_sub`/`interrupt`），无法共用一套资源 |
| D3 | 资源分发 | 构建期一项目一 APK / App 内导入多项目 / 在线下载 | ✅ **已拍板：v1 一项目一 APK** | MAA 本身即单一项目，沿用 MAA-Meow 的内置资源模式；代价是 v1 只能服务明日方舟 |
| D4 | 开源与许可 | AGPL 全开源 / 闭源 / 混合 | ✅ **已拍板：AGPL 全开源** | 可复用 MAA-Meow 的 AGPL 代码；代价是将来难闭源、上架商店与商业化路径受限 |
| D5 | Agent 运行时位置 | Kotlin 原生 / 内嵌 Python（MaaFwApp 式 bundled python3 / dsh-go 式 proot）/ 远程服务 | **待定**：先 Kotlin 壳，Python sidecar 作为候选（能直接复用 MAA-API 15.6k 行 agent 与知识库） | 决定复用 MAA-API Python 代码的程度、APK 体积与启动复杂度 |
| D6 | LLM 供给 | BYOK（用户自备 OpenAI 兼容 key）/ 官方托管 / 两者 | **BYOK + 可选托管 key** | 成本、合规、隐私（截图外传） |
| D7 | 自主程度 | 只 L1 任务执行 / 加 L3 原语与 pipeline 生成 | **v1 L1+L2，L3 作为高风险开关** | 决定产品风险与模型要求（L3 需要视觉模型） |
| D8 | 多项目策略 | 单项目深做 / 多项目通用 | 先单项目深做，再抽象 | 泛化是卖点，但知识/工具质量决定体验 |
| D9 | 桌面形态 | 不做好 / MaaFramework Python 服务 / 复用 MaaMCP | **v2 用 Python binding 或 MaaMCP 桥接** | 与 MaaMCP 的关系（互补而非重复） |
| D10 | 分发渠道 | GitHub Releases / 酷安等国内商店 / 自建更新 | **GitHub + 自建更新**（Shizuku 类工具上架风险高） | 合规与更新通道（MaaFwApp 用 Mirror 酱） |

---

## 9. MVP 路线（建议 8–12 周）

**M0 基座（1–2 周）**：新建 Compose 对话式外壳；从 MAA-Meow 取特权执行栈（launcher + `libbridge` + VirtualDisplay + `injectInputEvent` + `RemoteService`）；真机 Shizuku 下跑通：MaaCore 连接 → 内置资源 → `AsstAppendTask(StartUp / Fight)` → `AsstStart` → 回调。资源与内核版本固定，暂不做热更。

**M1 任务层泛化 + Agent 闭环（2 周）**：`TaskSpec` + `EngineAdapter` + `MaaCoreAdapter`（先做 5 个任务：Fight / Recruit / Infrast / Mall / Award）；参数 schema 与默认值移植 MAA-API 的 `task_schema.py` / `task_defaults.py`；Kotlin LLM 客户端 + ToolRegistry + 六个工具（`list_tasks` / `run_tasks` / `set_task_params` / `stop_run` / `get_status` / `get_screenshot`）；对话页 + 确认卡 + 审计 + 会话持久化。

**M2 兜底与原语（2 周）**：MaaCore 原生原语（`AsstAsyncClick` / `AsstAsyncScreencap` / `AsstBackHome`）+ 特权进程输入（swipe / long_press / input_text / key_event，文本先走剪贴板/无障碍）；截图坐标网格；L2 自定义任务（Copilot 作业 + 注入 `tasks.json`），对齐 MAA-API docs/11 的三层模型。

**M3 知识、诊断与体验（2 周）**：移植 MAA-API 的知识库（任务前提 / 失败特征 / 渠道 / 活动）与提示词；失败自动诊断（截图 + 日志 + 重试建议）；定时任务与通知（可直接用 MAA-Meow 的 schedule/notification）；中文输入；成本与上下文预算。

**M4 引擎抽象验证 + v2 预研（2 周）**：写一个最小 `MaaFrameworkAdapter` spike（不承诺 v1 交付），验证"新增 engine 不改工具层/提示词/UI"；同时定 v2 的资源分发与许可。

**验收标准（建议）**：10 个 NL 场景（日常 / 刷关 / 公招 / 基建 / 活动 / 卡死救援）成功率 ≥ 80%；单轮 LLM 成本有上限；消耗类与任意点击 100% 有确认或会话授权；崩溃/断线可恢复；日志可导出；**抽象验收：新增一个 `EngineAdapter` 不需要改 `ToolRegistry`、提示词与对话 UI**。

---

## 10. 风险与待实测

**产品/合规**
- Shizuku 门槛（装 App + 每次重启手动启）会筛掉大部分普通用户；不装 Shizuku 的降级链（无障碍/MediaProjection）体验差很多。
- 游戏 ToS 与反作弊：注入事件可被检测（`deviceId=0`、无 `FLAG_FROM_SYSTEM`）；Android 15 起可感知录屏。无对抗游戏可行，PVP/反外挂游戏不要碰。
- 商店政策：Google Play 对无障碍/自动化类应用限制严格；国内商店相对宽松但需要资质。GitHub Releases 是最稳的。
- **截图外传 LLM 的隐私**：游戏画面可能含账号信息；需要明示、可关闭、可脱敏（密码字段/输入框）。
- **许可证**：MaaFwApp / MaaMCP 是 AGPL-3.0（fork 必须 AGPL，网络服务也要提供源码）；MaaFramework 是 LGPL-3.0（动态链接可闭源）；各 PI 项目许可不同。想闭源就必须 clean-room 重写执行层——成本很高，建议接受 AGPL。

**技术**
- **串行与 1 Hz 节奏**：Tasker/Controller 各一个 worker、任务串行、每轮默认 `rate_limit` 1000 ms。Agent 的多步操作要按"原语调用"设计，不能靠反复 post 完整任务；评估时要测"每步决策 + 原语"的端到端延迟。
- **版本锁**：core / control unit / binding / agent 必须同版本（`MaaVersion()` + Agent 协议 v8）。App 内置 MaaFramework 版本是单点，热更新内核会连带所有 `.so`；要做运行期版本门槛（MaaFwApp/MAA-Meow 都有）。
- **状态增长**：`RuntimeCache` 元数据只增不减（`source/Tasker/RuntimeCache.cpp:70-95`），长会话/长时间挂机要定期 `ClearCache`；`Destroy` 会 join 工作线程，先 `PostStop`。
- **崩溃兜底**：MaaFramework 核心没有崩溃恢复，只有 Agent 子进程隔离。宿主需要看门狗（MaaFwApp 有：app 死则特权进程自杀；目标 App 离开虚拟屏有 watchdog 状态）+ 会话级失败诊断。
- **中文文本输入**：Shizuku 注入只有 ASCII，需要 IME / 剪贴板 / 无障碍通道，L3 的 `input_text` 要单独设计。
- **空间与体积**：MaaFramework Android 产物 ~60 MB + 资源（模板图/OCR 模型）数十 MB；APK 大小与 ABI 分包要规划。
- **性能**：虚拟屏 16–33 ms/帧、注入亚毫秒；但整链路的瓶颈在识别与 LLM。需要在目标机型实测截图 p50/p95、点击往返、识别耗时。
- **稳定性**：Shizuku 服务重启、厂商后台限制、虚拟屏残影、Android 14+ 的 BAL/VDM 限制——MaaFwApp/MAA-Meow 都有现成对策，要逐条验证。
- 多项目同时运行：MaaFramework 单 Tasker 一次一个任务；多设备/多实例要另设计（MaaMCP 支持多 controller，但那是桌面场景）。
- 无设备开发/测试：v1（MaaCore）没有 `MaaDbgController` 这类离线控制器，评估集要么用真机/模拟器，要么录屏回放（自己搭）。
- **MaaCore-on-Android 特有的三个风险**：
  - **上游依赖**：MAA 的 Android 产物是上游 CI 的 `MAAComponent-<tag>-android-*.tar.gz`（MAA-Meow 也是下载它），是否长期承诺、版本门槛要持续盯；`.so` 不能热更，v1 建议 APK 更新内核/资源，资源热更另做。
  - **没有结构化识别**：MaaCore 无独立 OCR API，L3 的画面判断只能"截图 + VLM"，比 MaaFramework 路线更贵、更依赖视觉模型，也更慢。
  - **参数默认值随内核版本漂移**：MAA-API 的默认值是用 M17 在 MAA v6.17.5 上逐字段取证的；升级内核后要重测，否则确认卡片与消耗判定会错。
- **许可证细节**：MAA（MaaAssistantArknights）与 MAA-Meow 都是 AGPL-3.0，复用其代码必须保留版权声明并开源；MAA 资源（模板图/关卡数据）的再分发也要遵守上游约定。
- **定位冲突**：v1 与本仓库 MAA-API 共用 MAA 内核，功能会重叠；建议明确分工（Android 单机 Agent vs 桌面/服务端 API），否则维护两份任务 schema/知识库会漂移。

**生态**
- PI 项目质量参差：任务名/选项/描述是否足以让 LLM 理解，取决于项目作者；可能需要"项目适配层"（人工写使用说明/同义词）。
- MaaMCP 可能向上做 PI 语义层；MaaAgent 可能复活。我们要么快，要么差异化明确（App + 产品级安全/多项目）。
- 上游依赖风险：MaaFramework 的 Android 产物是否长期承诺、PI 协议版本演进（v2.11.0 已很活跃）需要跟进。

---

## 11. 剩余待拍板的问题（拍板后）

1. **基座复用方式**：fork MAA-Meow 全仓裁剪，还是抽它的 `privileged/remote/manager/maa/native` 组件到新工程？（前者快、背 MAA-Meow 的 Profile/UI 包袱；后者干净、但要把特权栈重新拼起来。）
2. **Agent 运行时**：Kotlin 原生 vs 内嵌 Python sidecar。内嵌 Python 能直接复用 MAA-API 的 15.6k 行 agent（PolicyEngine、确认、审计、知识库、PRTS/企鹅物流），MaaFwApp 也证明 Android 上能打包 python3；代价是体积、启动与调试复杂度。
3. **LLM 供给**：BYOK（用户自备 OpenAI 兼容 key）还是你托管？要不要支持本地小模型/离线？
4. **MAA 内核与资源更新**：只随 APK 更新，还是做应用内资源热更 + 内核升级？跟 stable 还是 beta？
5. **v1 范围**：只做 L1（任务编排），还是一开始就带 L2（Copilot / 自定义任务 / 基建方案）与定时任务？
6. **与 MAA-API 的关系**：知识库、任务 schema、工具描述是否共用一份源（避免两边漂移）？还是各维护各的？

---

## 附录 A：证据索引

- MaaFramework：`docs/zh_cn/1.3-Custom&Agent.md`、`2.4-控制方式说明.md`、`3.1-任务流水线协议.md`、`3.3-ProjectInterfaceV2协议.md`、`include/MaaFramework/Instance/MaaContext.h`、`include/MaaFramework/Instance/MaaController.h`、`source/binding/{Python,NodeJS}`、`source/MaaAgent{Client,Server}`、`source/MaaAndroidNativeControlUnit`
- MAA-Meow：`app/src/main/java/com/aliothmoon/maameow/{maa,remote,manager}/`、`app/src/main/native/bridge*.{h,cpp}`、`scripts/setup_maa_core.py`、`README.md`、`ROADMAP.md`、`docs/`、`BUILDING.md`
- Shizuku：`server/src/main/java/rikka/shizuku/server/ShizukuService.java`、`api/`（`ShizukuBinderWrapper`/`SystemServiceHelper`/UserService）、官方文档 shizuku.rikka.app
- dsh-go：`docs/architecture.md`、`README.md`、`packages/dsh_client/`、`runtime/`、`app/lib/` 目录与少量关键文件（**注意：dsh-go 仓库 311 MB，分析时只做了部分检出，未逐文件审查**）
- 补充：MaaFwApp（`README.md`、`INTEGRATION.md`、`pi-profile.sample.yaml`、`app/src/main/aidl/.../RemoteService.aidl`、`runner/RunPlan.kt`、`project/PiParser.kt`）、MaaMCP（`README.md`、`CLAUDE_CN.md`、`maa_mcp/{pipeline_tools,agent_supervisor}.py`、`docs/research/custom-action-support.md`）、MaaAgent（MAA-AI/MaaAgent，Cherry Studio fork，已停更）
- 本仓库：`docs/11-Agent模块设计.md`、`docs/README.md`（决策速查表）

## 附录 B：术语

- **PI / ProjectInterface**：MaaFramework 的标准项目声明（`interface.json`），当前 PI 语义版本 v2.11.0。
- **Bundle / Resource**：按标准结构存放的 `pipeline`/`image`/`model` 文件夹；Resource 是多个 Bundle 按序叠加。
- **Pipeline / Node / Entry / Task**：节点图、单节点、入口节点、一条任务链。
- **MaaFramework 的 "Agent"**：指 AgentServer（自定义识别/动作的独立进程），**不是 LLM agent**——讨论时注意区分。
- **AgentServer / AgentClient**：自定义识别/动作的子进程与宿主侧客户端。
- **MaaFwApp**：Aliothmoon 的 MaaFramework Android 通用 GUI（AGPL）。
- **MaaMCP**：MistEO/MAA-AI 的 MaaFramework MCP server（AGPL）。
- **Cut / 特权限定**：本文中"特权进程"指 Shizuku bindUserService 或 root 拉起的 shell/root uid 进程。
