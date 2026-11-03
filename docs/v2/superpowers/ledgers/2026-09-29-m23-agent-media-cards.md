# SDD ledger — Agent 会话内的图片与图表（M23）

## Setup

- Base：`19f0e3c`（M22 文档收尾合并回 `refactor/v2` 的提交），分支 `m23-agent-media-cards`（从 `refactor/v2` 新建）。
- 规格：`docs/superpowers/specs/2026-09-29-m23-agent-media-cards.md`（提交 `6c06955`）；计划：`docs/superpowers/plans/2026-09-29-m23-agent-media-cards.md`。
- 用户裁决（2026-09-29）：「会话中，agent 应该有发送图片和 canvas 图表的能力。」逐项确认：①对象是 MAA-API 内置 Agent 对话页；②图片四类全要（agent 截图直接可见、MAA 资源图、任务历史/日志既有截图、服务端渲染生成图）；③图表要结构化卡片、前端渲染、可交互、跟随主题；④图表用 ECharts（canvas）；⑤服务端渲染只做截图加工（裁剪/放大/标注框），不做图表 PNG；⑥不做干员头像（本地无图源），关卡地图改由本地 tile 数据渲染示意图；⑦补齐「任务历史截图」的写入点（流水线任务留档）；⑧任务失败自动截图挂日志。

## 事实基础（动手前实际核对）

- `agent_message` 无媒体列；`get_screenshot` 的归档结果里**早就有** `screenshot_id`/`sha256`/`thumb_url`/`full_url`，只是前端当 JSON 打印——「agent 发图」缺的几乎全是渲染。
- 图片通路已存在：`store_screenshot` 内容寻址 + `/api/images/{sha256}/thumb|full`、`/api/screenshots/{id}`、`/api/resources/items/icon?item_id=`；`logImage()` 已有「只认同源 `/api/images/<sha>/…`」的白名单先例。
- 截图今天只有两个写入点（`agent`／`manual`）：`crash` 只被读、`task_snapshot` 只有枚举、`LogRecord.attachment` 只有读侧，本机库 61 行截图 `pipeline_id` 全空。
- `store_screenshot()` 默认质量取 `adb.screenshot_quality`＝25（`get_screenshot` 内存里的 85 会被它覆盖）。
- tile 数据：`overview.json` 是 4186 键的 dict，`filename` 100% 规则化；`code` 不唯一（944/2133 对多关）；单格是 `{tileKey, buildableType, heightType, isStart, isEnd}`，`tileKey` 有 **107 个**取值，起点/终点来自布尔而非 tileKey。
- 任务终态在 `_execute_pipeline` 的 `_execute_task` 调用点一处汇聚（`outcome ∈ {completed, cancelled, crashed, failed, skipped}`）；失败日志的 offer 早于任务终态落库，且 `LogHubHandler` 不搬运 `attachment`。
- 设备访问没有锁（`operation_lock` 不保护设备）；`store_screenshot` 是同步阻塞函数。

## Rulings I made

- Ruling: **资源索引逻辑另起一份**（`services/resource_index.py`），不把 `api/routers/resources.py` 的私有 helper 搬走。理由：那份路由的测试直接 monkeypatch 私有名 `_resource_root` / `_INDEX_CACHE`，搬走会破夹具；代价是约 40 行重复的路径收敛逻辑，两边用注释互相指向并各自有测试。
- Ruling: **`crashed` 终态也留档**（规格只写了 `completed`/`failed`/`skipped`）。理由：内核崩在任务里正是最需要现场的情况；失败即降级照旧，不影响终态。代价：比规格多一类写入，已在 docs/13 §8.29 记明。
- Ruling: 失败日志附件**新写一条 `source="task"` 记录**，不给既有失败日志补字段。理由：既有记录在 `_set_task_status(FAILED)` 之前就已 offer，且 `LogRecord` 一旦 offer 不可改。
- Ruling: 留档注入走 `bind_log_hub` **后置绑定**（照 `bind_notify_service` 的范式），不给 `PipelineRunner.__init__` 加参数。理由：`app.state.pipeline_runner_factory` 是可替换钩子，改构造签名会波及测试与冒烟脚本。
- Ruling: 前端媒体卡取图统一走 `api` 客户端（`X-Token`）取 blob，不依赖 cookie（日志页的 `<img src>` 做法不动）。
- Ruling: 图表上限按规格 §3.5 的两级都实现（逐项 + 总量 ≤ 600 点 / 序列化 ≤ 24 KB），因为 docs/04 的 32 KB 截断在代码里没有兜底。

## Tasks

| Task | 产物 | 结果 |
|---|---|---|
| 1 · 后端媒体工具链 | 五个工具、资源索引、关卡布局、截图加工、runtime 四处、测试 | 完成（`task-1-report.md`，含 §5 追加节） |
| 2 · 后端留档与日志附件 | 任务终态留档、失败日志附件、测试 | 完成（`task-2-report.md`） |
| 3 · 前端媒体卡与 ECharts | media.ts、四张卡、ECharts、取图 hook、ToolCallBar、测试 | 完成（`task-3-report.md`） |
| 4 · 文档 | docs/04·06·09·11·12·README | 完成（A 有报告；B 未留报告，diff 由主 agent 复核） |
| 5 · 验收（主 agent） | 全量 pytest、生成物三查、前端五步、两个冒烟、独立探针 | 完成（见下） |
| 6 · 台账与合并 | ledger + `git merge --no-ff` + `v2-m23` | 完成 |

## Task progress

- **Task 1（后端媒体工具链）** 实现者完成（`task-1-report.md`，19 条实现裁决）。主 agent 复核：`agent/media.py` 是契约唯一定义处（四型卡 + 三类同源 URL + 失败保留 error 的占位）、`services/resource_index.py` 与 `api/routers/resources.py` 有意各存一份（后者测试 monkeypatch 私有名）、`services/stage_layout.py` 的 107 键覆盖表与平局规则、`runtime.py` 四处改动（归档补 `media` 且既有键不变、媒体占位保留 `tool_call_id`、`SCREEN_TOOL_NAMES` 与按工具名的拦截文案、SYSTEM_PROMPT 段落位置不破顺序断言）。中途裁决三条：legend 保持「片段码」不改后端；`annotate_screenshot` 补 `pipeline_id`/`trigger`（已落地并加用例）；参数校验码维持「字段级 422 / 语义级 400」。另核实 `annotate_screenshot` 在处理器内 `commit` 是安全的（确认服务给处理器的是独立会话，`confirmation.py:984`）。
- **Task 2（留档与日志附件）** 实现者完成（`task-2-report.md`）。主 agent 复核 diff：钩子只在 `_execute_pipeline` 的 `_execute_task` 汇聚点加一行、`_capture_task_snapshot` 整段失败即降级（缺 `device_manager`/缺 `screenshot()` 走 debug，异常与超时只 WARNING、`CancelledError` 照常传播）、日志附件走 `LogHub.offer` 的 `source=task` 新记录、`main.py` 只加 `bind_log_hub` 后置绑定。实现者另证实「每个终态只抓一次图」（no-op 猴补 + 打点 + 四种终态断言 `calls == ["adb"]`）。
- **Task 3（前端）** 实现者完成（`task-3-report.md`）。主 agent 亲自复跑五步全绿（60 文件 / 596 项 / build 末行 SW OK）。复核 `media.ts` 白名单（三种同源地址 + 长度还原 + 上限二次裁剪）与 `StageMapCard` 对 legend 两种长度的分派渲染。
- **主 agent 收口的两处**：① `tests/agent/test_account_resources_flow.py` 的截图计数按新语义改成「2 次抓图 + 一条 `task_snapshot` 留档行」（并加 `_Flow.task_snapshots` 的加载与断言）；② 独立验收探针 `probe.py`。
- **Task 5（验收，主 agent 亲自跑）**：
  - 后端 `.venv/bin/python -m pytest -o addopts="" -q` → **2040 passed, 4 skipped**，退出码 0（M22 是 1946，+86 条新增）；
  - `.venv/bin/python scripts/dump_openapi.py --check` 退出码 0（无端点变化、错误码仍 105）；
  - 前端 `pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` 五步退出码 0（60 文件 / 596 项）；
  - `scripts/agent_smoke.py`、`scripts/frontend_smoke.py` 末行均 `SMOKE OK`；
  - 独立探针（假 LLM 驱动一轮跑完五个媒体工具）末行 `PROBE OK`：SSE `tool_result` 带 media 信封、五条 tool 消息落库可重建、加工图落盘且 60×45、第二轮请求里只剩 `media_sent` 占位且 `tool_call_id` 完整、无 `data:image` 回灌。
  - 未做（如实记录）：真机端到端（模型真的圈出理智条、真实流水线留档在 UI 上的观感）与「tile 行序」目视确认——手头 8 张调试截图都是 UI 画面，没有战斗界面可比对。
- **Task 4（文档）**：第一轮派出的文档子 agent **空跑**（未改文件、未写报告），改为拆两个更小的任务重派（`task-4a-brief.md` / `task-4b-brief.md`，事实直接写进简报）。A 完成 docs/11（§3.2 媒体组表 43→48、关卡数据段、两级校验段；§4.4；新增 §5.5 媒体卡；§8.2–§8.5）、docs/12（M23 条目）、docs/README（需求 7 新行）并留了报告；B 完成 docs/04（§5.4 trigger 写入侧现状与留档口径、§5.8 的 32 KB 纪律纠正）、docs/06（附件写入侧 + §8.4 清理口径纠正，标注为**既有漂移**）、docs/09（§3.5 改 ECharts 与体积、§4/§9 去掉从未落地的手动分包、§6 媒体卡、§8.2 色板、§10.4 取图鉴权、§11.2 预缓存事实），但**未留报告**——其 diff 由主 agent 逐段复核，并实测核对了两个关键数字（ECharts 相关 8 个 chunk = 1071 KB raw / 356 KB gzip；保留策略确为 04:00 cron + 启动后 30 秒补跑）。
- 文档 A 提出的三处小事，主 agent 裁决：① §3.2 里「43 → 48」的总数句保留；② 媒体卡作为 §5.5 放在「人工确认机制」下**保留**（该节从 M21/M22 起已经演变成「交互卡片/人工介入」的集合）；③ tile 上下方向按「待验证」口径写，不断言。它另纠正了简报里的一处错误——系统提示词的段落顺序断言来自 **M18**（`tests/agent/test_runtime.py:2596-2602`），M21 只是最近一次插段的人——文档按纠正后的口径写。

## 最终交付与遗留

见 `docs/13-决策记录.md` §8.29（含验证段与遗留清单）。台账原件：`docs/superpowers/ledgers/2026-09-29-m23-agent-media-cards.md`。

## 收尾修复（tag 之后，各占一个短分支，见 docs/13 §8.29 第十、十一节）

| 分支 | 起因（用户裁决） | 改动 | 验证（主 agent） |
|---|---|---|---|
| `m23-media-thumb-followups` | 会话里的图片发糊：「缩略图在会话中的尺寸可以缩小，质量不变，点击放大也是原图，其余的不改」「直接去掉这个文本」 | 只改前端 `ImageCard`：预览按缩略图自然尺寸显示（`w-fit` + `max-w-[20rem]`，长边 320 px），去掉卡片下方的原图尺寸文字 | `typecheck` / `test --run`（60 文件 596 项）/ `build` 退出码 0，构建产物含新样式 |
| `m23-media-attachment-followups` | 「工具调用响应的图片，能不能以附件的形式，放到这个工具调用卡片内？只有用户明确的命令…图片才会发送显示到会话中」 | 后端撤掉 `get_screenshot` 归档结果里的 `media` 信封（契约收紧为「只有五个 `send_*` 产生媒体卡」）+ 工具描述与系统提示词加「只在用户明确要求时发送」门槛；前端删掉旧形态→会话卡的分支，新增 `toolScreenshotAttachment()` 与 `ToolAttachment.tsx`，附件渲染在该次工具调用 `<details>` 展开区「参数」之前 | 后端全量 **2042 passed / 4 skipped**；前端 `gen:api:check` / `typecheck` / `test --run`（60 文件 **602** 项）/ `build` 全部退出码 0；`dump_openapi.py --check`、`generate_api_guide.py --check` 退出码 0 |

附带裁决：自动诊断会话开头 `incidents.py` 插的崩溃现场种子截图**同样按工具卡内附件处理**（用户裁决保持现状，不为它开 `snapshot_source` 例外）。规格里「`get_screenshot` 结果补上 media 信封」的表述被 §8.29 第十一节取代（规格是方案快照，按本仓惯例以 docs/13 §8 为准）。
