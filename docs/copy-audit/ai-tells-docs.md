# AI 腔规则命中：docs 与 README 正文（逐行统计）

总计 13071 条文本。命中按规则汇总，「用户可见」只统计界面/对外文案，
「开发向」是注释、docstring、内部常量（维护者看得到，终端用户看不到）。

| 规则 | 严重度 | 用户可见 | 开发向 | 合计 |
|---|---|---|---|---|
| 粗体强调 | medium | 0 | 3192 | 3192 |
| 破折号当冒号用 | high | 0 | 628 | 628 |
| 「A 而不是 B」对照腔 | high | 0 | 283 | 283 |
| 绝对化规定腔 | medium | 0 | 219 | 219 |
| 「…即可」机器腔 | medium | 0 | 75 | 75 |
| 「不是…而是…」对照句 | high | 0 | 22 | 22 |
| 自指式讲解腔 | medium | 0 | 20 | 20 |
| 省略号 | medium | 0 | 5 | 5 |
| 提示腔 | medium | 0 | 3 | 3 |
| emoji/符号装饰 | high | 0 | 2 | 2 |
| 「值得注意的是」式过渡 | high | 0 | 2 | 2 |
| 口播腔 | high | 0 | 1 | 1 |
| 英文 AI 惯用语 | medium | 0 | 1 | 1 |
| 宣传词 | medium | 0 | 1 | 1 |

## 粗体强调（medium｜用户可见 0 / 开发向 3192）

**开发向**

- `docs/01-重构总览与现状评估.md:21` 因此这是一次**重写而非改造**：后端执行层重写，`static/` 整体废弃，引入 SQLite 与子进程隔离。
- `docs/01-重构总览与现状评估.md:34` | pydantic | **1.10.21** | **v1，不是 v2** |
- `docs/01-重构总览与现状评估.md:39` pydantic 仍在 v1 是个容易被忽略但影响工作量的事实。现有代码的 `super().dict(*args, **kwargs)`（`task.py` 的 `to_dict`）、在 `__init__` 里 `super().__i…（共 269 字）
- `docs/01-重构总览与现状评估.md:45` **启动期强耦合。** `maa_api/model/core/scheduler.py` 末尾是模块级 `task_scheduler = TaskScheduler()`，构造函数里调 `load_asst()`，而 `load_ass…（共 273 字）
- `docs/01-重构总览与现状评估.md:47` **单实例且无队列。** 全局一个 MaaCore 实例，`_check_asst_running()` 在运行中直接抛异常拒绝新提交。定时任务撞上手动任务时靠 `if task_scheduler.is_running(): return…（共 149 字）
- `docs/01-重构总览与现状评估.md:49` **状态全在内存。** `TaskPipeline` 是个 pydantic 模型，`tasks`、`status`、`logs` 都是内存字段。重启丢失全部执行历史，前端刷新看到的是当前进程的瞬时状态。
- …… 另有 3186 条，见 inventory.json

## 破折号当冒号用（high｜用户可见 0 / 开发向 628）

**开发向**

- `AGENTS.md:16` 不要合并到 dev，也不要把 dev 拉进任何分支——dev 是旧线，与 v2 无关。
- `docs/01-重构总览与现状评估.md:51` **截图以 base64 内嵌日志。** `pipeline.py` 的 `append_img_log` 把整张 JPEG 的 base64 塞进日志内容，前端通过 `GET /api/maa/pipeline` 全量拉取。一条流水线跑完…（共 163 字）
- `docs/01-重构总览与现状评估.md:102` **连接失败的回调事件名拼错，该日志从未触发过。** `callback_handler.py` 的 `connection_map` 里键是 `ConnectFaild`，而 MaaCore 实际发出的是 `ConnectFailed`（…（共 226 字）
- `docs/02-系统架构设计.md:135` `LOAD_RESOURCE` 的 300 秒是从原定的 120 秒上调的。原值是按"基础资源 + 一个 15 KB 的 OTA `tasks.json`"估的，而现在这条命令一次要跑完整条加载链——基础资源加上三个增量层，其中通道 B 的…（共 272 字）
- `docs/02-系统架构设计.md:197` **不抢占**意味着：正在执行的流水线不会被更高优先级的新提交打断。用户若想立刻插队，需显式调用停止接口终止当前流水线，再提交新的 —— 这个动作在前端是一个明确的按钮，不是隐式行为。
- `docs/02-系统架构设计.md:311` 启动 `CoreSupervisor`，**不阻塞等待** —— 子进程在后台走"校验版本 → 加载资源 → 连接设备"
- …… 另有 622 条，见 inventory.json

## 「A 而不是 B」对照腔（high｜用户可见 0 / 开发向 283）

**开发向**

- `docs/01-重构总览与现状评估.md:21` 因此这是一次**重写而非改造**：后端执行层重写，`static/` 整体废弃，引入 SQLite 与子进程隔离。
- `docs/01-重构总览与现状评估.md:53` **轮询式任务等待。** `executor.py` 用 `while self._running and self.asst.running(): self._condition.wait(timeout=5)` 等任务完成。`runni…（共 192 字）
- `docs/01-重构总览与现状评估.md:72` 键位置用的是变量本身而非字符串字面量。当多个参数都是 `None` 时，字典会塌缩成 `{None: <最后一个值>}`。生息演算任务的参数从未正确传给内核。
- `docs/01-重构总览与现状评估.md:82` 类体里的尾随逗号会让默认值变成元组 `(None,)` 而非 `None`，与声明的类型不符。
- `docs/01-重构总览与现状评估.md:94` **枚举值硬编码在 Python 里会随内核版本过时。** 最典型的是 `Roguelike.theme`（当前硬编码 `Phantom` / `Mizuki` / `Sami` / `Sarkaz`），MAA 每次新增肉鸽主题都需要改代码…（共 214 字）
- `docs/01-重构总览与现状评估.md:156` 配置文件 `config.yaml` 的结构会扩展（新增渠道默认值、LLM 配置、通知通道、重试参数等），但可视化设置页的配置项落库而非写回 YAML，两者的优先级关系见 [08-ADB连接与账号渠道](./08-ADB连接与账号渠道.md…（共 122 字）
- …… 另有 277 条，见 inventory.json

## 绝对化规定腔（medium｜用户可见 0 / 开发向 219）

**开发向**

- `AGENTS.md:29` `python` 不在 PATH，一律用 `.venv/bin/python`。
- `docs/01-重构总览与现状评估.md:17` 开放 API 受阻于响应格式。所有接口一律返回 HTTP 200，业务码塞在 body 的 `code` 字段里，这个格式在 OpenAPI 与 agent 场景下无法表达错误语义。
- `docs/02-系统架构设计.md:48` 主进程**绝不加载** `libMaaCore`。所有涉及内核的操作都编码为命令投递给子进程。这条纪律是子进程隔离能成立的前提，一旦主进程里出现 `ctypes.CDLL(libMaaCore)`，内核热更新与崩溃隔离就同时失效。
- `docs/03-MaaCore内核层设计.md:220` pass # 回调线程内绝不抛异常，否则可能直接崩内核
- `docs/03-MaaCore内核层设计.md:315` **内核未就绪时的行为。** 子进程处于 `STARTING` / `RESTARTING` / `CRASHED` / `FAILED` 时，所有业务命令直接抛领域异常（对应 `CORE_NOT_READY` / `CORE_RESTAR…（共 247 字）
- `docs/04-数据模型与持久化.md:82` 会话管理遵循两条纪律。HTTP 请求通过依赖注入拿到 `AsyncSession`，请求结束即关闭；后台任务（`PipelineRunner`、`LogHub` 刷盘、清理任务）**各自开新会话**，绝不跨任务共享——`AsyncSessi…（共 238 字）
- …… 另有 213 条，见 inventory.json

## 「…即可」机器腔（medium｜用户可见 0 / 开发向 75）

**开发向**

- `AGENTS.md:15` 默认不 push：提交到本地即可。用户明确要求推送时，push 当前分支到 origin 的同名分支（refactor/v2），不必再确认一次；没有明确要求就不要推。
- `docs/04-数据模型与持久化.md:9` 数据库文件位于 `resource/maa_api.db`。选这个位置的原因有三：`resource/` 已在 `.gitignore` 中被整体忽略，数据库及其 WAL 副产品不会误入版本库；`resource/` 已是运行期数据的既有落…（共 178 字）
- `docs/05-API规范与路由清单.md:380` | `SKLAND_LOGIN_EXPIRED` | 409 | 扫码会话到期（TTL 180 秒）、已被消费，或上游判定失效（`scan_status` 回 `status=102`）；用户重新生成一张二维码即可 |
- `docs/05-API规范与路由清单.md:725` **中断本轮（M22）。** `POST /api/agent/sessions/{id}/interrupt` 幂等：返回 `{"session_id", "interrupted", "turn_running"}`，其中 `inter…（共 660 字）
- `docs/06-实时日志与WebSocket.md:420` 内核热更新导致子进程重启时，MaaCore 会重新初始化日志系统并追加到同一个 `asst.log`（`LoadFileStream` 后接 `log_init_info`，可从实测日志开头的 `MaaCore Process Start`…（共 148 字）
- `docs/07-热更新方案.md:53` **当前版本号**从内核自身取。现有做法是 `Asst.load()` 后调 `Asst().get_version()`——这在重构后**不可行**，因为主进程绝不加载 `libMaaCore`（[02-系统架构设计 §1](./02-系…（共 247 字）
- …… 另有 69 条，见 inventory.json

## 「不是…而是…」对照句（high｜用户可见 0 / 开发向 22）

**开发向**

- `docs/05-API规范与路由清单.md:373` 两个码只对 INTERNAL（Agent 会话）调用方出现。没有授权不是错误而是确认：策略层返回 `confirmation_action="grant_copilot_attempts"`，由用户在本轮批准（可改尝试次数、成功标准与是否允…（共 182 字）
- `docs/05-API规范与路由清单.md:1102` 这些不是设计偏好，是现有代码里的实际缺陷，新模型必须修正。
- `docs/07-热更新方案.md:363` 顺带修正一个直觉误差：体积大头不是模板图而是 `Arknights-Tile-Pos/` 的关卡格子坐标（93 MB 对 12 MB）。它们是高度可压缩的 JSON，所以压缩包只有 13 MB 却能解压出 107 MB——**磁盘预检要按解…（共 139 字）
- `docs/08-ADB连接与账号渠道.md:472` **并发命令的交错。** 两个客户端同时对同一设备发命令，adb server 会串行化，但顺序不保证。实际影响很小（我们的操作本身就是异步的），唯一需要小心的是"截图 + 点击"这类要求时序的组合——agent 先截图看到某个按钮、再点击…（共 250 字）
- `docs/08-ADB连接与账号渠道.md:792` **账号数据不互通**。官服与 B 服是不同的服务器，账号、进度完全独立。切换渠道不是"换个皮肤"而是"换个游戏"。界面上的渠道切换入口要有足够的视觉分量，不能像切换主题那样轻描淡写。
- `docs/09-前端重构方案.md:487` `get_screenshot` 的图不是卡片，是工具卡内的附件（M23 收尾）。只有五个 `send_*` 工具产生会话级媒体卡；agent 自己观察画面用的 `get_screenshot` 结果由 `media.ts` 的 `tool…（共 390 字）
- …… 另有 16 条，见 inventory.json

## 自指式讲解腔（medium｜用户可见 0 / 开发向 20）

**开发向**

- `docs/01-重构总览与现状评估.md:1` 本文档说明重构的动因、现状的具体问题、范围边界与验收标准。决策依据见 [README 决策速查表](./README.md#决策速查表)，目标架构见 [02-系统架构设计](./02-系统架构设计.md)。
- `docs/02-系统架构设计.md:1` 本文档是整个重构方案的架构基准。其余文档中的模块边界、数据流、命名均以本文档为准。决策依据见 [README 决策速查表](./README.md#决策速查表)。
- `docs/03-MaaCore内核层设计.md:1` 本文档定义 MaaCore 内核的 FFI 封装、子进程 worker 与生命周期管理。决策依据见 [README 决策速查表](./README.md#决策速查表)，架构位置见 [02-系统架构设计](./02-系统架构设计.md)。
- `docs/04-数据模型与持久化.md:1` 本文档定义 MAA-API 重构后的全部持久化结构：SQLite 库文件布局、20 张业务表的字段与索引、状态枚举、Alembic 迁移组织、仓储层职责与数据保留策略。技术选型依据见 [README 决策速查表](./README.md#决…（共 164 字）
- `docs/04-数据模型与持久化.md:92` `AUTOINCREMENT` 关键字在这里不能省。不带它时 SQLite 用「当前最大 rowid + 1」分配 id，删掉末尾若干行之后新插入的行会**复用已被删除的 id**；而日志表恰好有一个按天清理的保留策略，一旦某次清理删到了尾…（共 202 字）
- `docs/04-数据模型与持久化.md:1364` 截图文件保留 7 天，同时受总大小上限约束（默认 512 MB）。两个维度依次执行：先按 7 天删，删完若总量仍超上限，再按 `created_at` 升序继续删最旧的；文件被删时给记录打 `deleted_at`，记录本身按 90 天随流…（共 422 字）
- …… 另有 14 条，见 inventory.json

## 省略号（medium｜用户可见 0 / 开发向 5）

**开发向**

- `docs/13-决策记录.md:782` 三、时序与收尾口径。模型调用新工具 `ask_user`（group `interaction`、SAFE、`read_only=False`、`model_visible=True`，参数 `question` + `options[2.…（共 713 字）
- `docs/13-决策记录.md:1581` **护栏靠关键词判定，不判断真实画面**：只检查任务列表里有没有 `StartUp`，不看当前是不是已经在主界面。已经在游戏里跑日常的流水线不会带 `StartUp`（也不需要），所以 `warnings` 的措辞是「游戏状态不确定时……」…（共 130 字）
- `docs/ENVIRONMENT.md:430` ``| `CODE` | 404 | 含义…… |`` 的行共 92 行 / 14 张表，HTTP 列为 `—` 的只有 `UPDATE_INTERRUPTED`。
- `docs/superpowers/plans/2026-09-28-m17-task-param-defaults.md:64` docs/09 §7.3/§603/§656：把「未改动的字段不进草稿……控件显示内核默认值并标「默认」」改写为新的物化 + 预填 + 显式下发模型，并保留「服务端不预设内核默认值」这条历史理由的说明（现在由 §8 的偏离条目承接）。
- `docs/superpowers/specs/2026-10-09-m25-prts-knowledge.md:134` | 当期活动用 SMW 查询取，时间同时给 `timestamp`（Unix 秒）与 `raw`（UTC） | `action=ask&query=[[分类:有活动信息的页面]][[!分类:集成战略]]\|?名称缩短\|?活动开始时间\|?…（共 239 字）

## 提示腔（medium｜用户可见 0 / 开发向 3）

**开发向**

- `docs/09-前端重构方案.md:865` `4401` / `4403` 这两个必须硬编码成「不重连」。鉴权失败是确定性错误，重连只会在服务端日志里刷出一串失败握手，且掩盖真正的问题。close `1011` 的语义值得特别注意：按 06 §6，它表示「本连接的服务端发送队列满了，…（共 186 字）
- `docs/13-决策记录.md:1464` **实测 404（改写稿不得指向这些标题）**：`潜能`、`材料一览`、`寻访`、`精英化`、`卡池`、`分支`、`稀有度`、`作战`、`干员养成`、`概率`、`悖论模拟`、`模组系统`、`货物运送`、`芯片搜索`、`公开招募/标签`、`作…（共 460 字）
- `docs/ENVIRONMENT.md:736` 复跑注意：`.venv/bin/python scripts/probe_log_ws.py` 每次都会覆盖 `tests/fixtures/log_ws_probe_result.json` 与 `log_ws_probe_finding…（共 233 字）

## emoji/符号装饰（high｜用户可见 0 / 开发向 2）

**开发向**

- `docs/06-实时日志与WebSocket.md:189` | `StageDrops` | `{details.stars}`⭐通关`{details.stage.stageCode}` <br>掉落统计: <br>`{掉落明细}` |
- `docs/superpowers/specs/2026-09-29-m19-agent-knowledge-expansion.md:927` get_logs（`source=["task"]`）里能看到每次「已战斗 x/y 次，消耗理智 N」，以及每条「{星级}⭐通关{关卡代号} 掉落统计」。

## 「值得注意的是」式过渡（high｜用户可见 0 / 开发向 2）

**开发向**

- `docs/07-热更新方案.md:49` 需要注意的是**旧域名 `ota.maa.plus` 已于 2025-11-29 停止解析**，官方要求迁移到 `api.maa.plus`。现有代码的 `Mirrors = ["https://api.maa.plus"]` 已经是正确的…（共 171 字）
- `docs/13-决策记录.md:169` cookie 只允许 `GET` / `HEAD` 与 WebSocket 升级，写操作必须走 header 或 query。需要说明的是 CSRF 风险本身已被 `SameSite=Lax` 挡住大半（跨站 POST 不携带 cookie…（共 190 字）

## 口播腔（high｜用户可见 0 / 开发向 1）

**开发向**

- `docs/08-ADB连接与账号渠道.md:436` **关于滑动需要澄清一个容易混淆的点。** MaaCore 内部**是能滑动的**——`resource/config.json` 的 `General` 配置里明确定义了 `"swipe": "[Adb] -s [AdbSerial] s…（共 420 字）

## 英文 AI 惯用语（medium｜用户可见 0 / 开发向 1）

**开发向**

- `docs/09-前端重构方案.md:1183` 反向代理终结 TLS。前面放一个 Caddy 或 Nginx，用自有域名 + Let's Encrypt。如果域名解析到公网 IP 则用 HTTP-01 挑战，但那意味着服务要暴露到公网，与私有部署的前提冲突。更好的做法是 DNS-01 挑…（共 310 字）

## 宣传词（medium｜用户可见 0 / 开发向 1）

**开发向**

- `docs/09-前端重构方案.md:1189` Chrome DevTools 的端口转发。Android 手机用 USB 连电脑，在 `chrome://inspect` 里配端口转发，手机上的 `localhost:8002` 映射到电脑能访问的地址，从而落进 `localhost`…（共 176 字）

