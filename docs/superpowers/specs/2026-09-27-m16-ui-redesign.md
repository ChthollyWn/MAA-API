# M16 前端 UI/UX 全面重构 · 交付规格

## 目标

把前端表现层整体重写一遍：外壳、导航、组件、全部页面、配色与图标。重点是手机浏览器（也在 PC 浏览器用），交互要明确、好按，界面简洁高雅。旧的视觉设计不作任何约束。

功能一个不少：本文「行为基线」一节列出的每一条都必须在新界面中可用，并由新测试覆盖。

## 范围

- **纯前端**。不改后端、不改 OpenAPI 与生成物；`dump_openapi.py --check`、`pnpm gen:api:check` 保持通过。技术栈维持决策表：Vite + React + TypeScript + Tailwind CSS + shadcn/ui（Radix 原语）。
- **数据层、实时层、鉴权、PWA 原样复用**：`web/src/api/*`、`web/src/realtime/*`、`web/src/stores/auth.ts`、`web/src/stores/log-buffer.ts`、`web/src/lib/idb.ts`、`web/src/pwa/push.ts`、`web/src/sw.ts`、`web/src/types/api.d.ts` 不改行为。
- **三项复用现有接口的易用性增强**：
  1. 首页「运行定时任务」快捷入口（`POST /api/schedules/{id}/run`）。
  2. 任务编排器「存为定时」：把当前编排好的任务带进定时任务编辑器。
  3. 通知通道按类型填写的表单（邮件、Webhook、Bark、钉钉、企业微信），保留「以 JSON 编辑」高级模式。
- **一项遗留修复**：Agent 长会话只显示最早 200 条消息（docs/13 §8.7）。改为按 `total` 取最后一页，上滑加载更早。
- **明确不做**：Agent「停止生成」（SSE 断开后后端回合仍在后台继续，见 `maa_api/agent/runtime.py` 的 detached producer，纯前端按钮会误导用户）；日志导出等新功能。

## 必须保留的契约

- `/?confirm=<id>` 深链：后端 `maa_api/agent/confirmation.py` 与 `web/src/sw.ts` 写死了这个格式。打开后必须直接定位到那条确认，处置后清掉参数；目标已不在待处理列表时提示一次。
- token 三处同步：localStorage `maa-api-token`、HttpOnly cookie（`POST/DELETE /api/system/auth/cookie`）、IndexedDB `maa-api/auth/token`。SPA 请求用 `X-Token`。401 跳登录并带 `returnTo`；403 就地提示、不跳转、保留 token。
- Service Worker：`injectManifest` + 自写 `src/sw.ts`；导航 denylist；非安全上下文跳过注册并隐藏推送入口、给出说明；`PwaUpdatePrompt` 的「提示后更新」语义。
- 构建输出到仓库根 `static/`，不引外链（字体用系统字体栈）；`index.html` 无外部资源；图标文件名不变（`frontend_smoke.py` 检查 manifest 的 `start_url` 为 `/` 且图标存在）。
- 实时层：`query-bridge` 依赖的 query key（`web/src/api/keys.ts`）不改；日志 WS 订阅由日志页在挂载期间开启、卸载时关闭。
- 任务表单：schema 驱动（`GET /api/tasks/types`）；`anyOf`/`oneOf` 剥 null；只提交用户改动过的字段；`Idempotency-Key`；`x-risk` 先确认；422 字段错误映射回控件（两种 details 形态）；504 后查询 `current` 并复用同一 key；`x-depends-on` 禁用而不隐藏；`x-widget`（select、tags、ordered-list、item-count-map、duration-map）与 Copilot 作业选择语义不变。
- 定时任务：星期 + 时间 ↔ POSIX cron 的双向转换规则（`schedule-time.ts`）；无法可视化的 cron 原样保存；模板白名单序列化（Copilot 只提交 `asset_id`）；API 调试台通过 `location.state.apiConsolePrefill.template` 预填。

## 设计系统

### 色彩令牌

在 `web/src/index.css` 以 CSS 变量定义两套主题，经 `@theme inline` 映射为 Tailwind 颜色。浅色写在 `:root`，深色写在 `:root.dark`。

| 变量 | 浅色 | 深色 | 用途 |
|---|---|---|---|
| `--background` | `#F6F7F9` | `#0E1014` | 页面底色 |
| `--surface` | `#FFFFFF` | `#15181D` | 卡片、列表组、面板（Tailwind `card`） |
| `--surface-2` | `#EEF0F3` | `#1C2027` | 次级填充：输入框底、分段控件底、悬停（`muted`、`secondary`、`accent`） |
| `--surface-raised` | `#FFFFFF` | `#1A1D23` | 浮层（`popover`） |
| `--border` | `#E2E5EA` | `#272C35` | 分隔线、描边 |
| `--border-strong` | `#CDD1D8` | `#363C47` | 输入框描边 |
| `--foreground` | `#14161B` | `#E7E9EE` | 正文 |
| `--muted-foreground` | `#646A76` | `#959BA7` | 次要文字 |
| `--primary` | `#4F5BD5` | `#5A66E8` | 主按钮底、选中态底 |
| `--primary-hover` | `#4450C8` | `#6672EE` | 主按钮悬停 |
| `--primary-foreground` | `#FFFFFF` | `#FFFFFF` | 主色上的文字 |
| `--primary-soft` | `#ECEEFD` | `#1F2340` | 主色浅底（选中行、信息提示） |
| `--primary-text` | `#4350C8` | `#9AA3F7` | 链接、选中图标与文字 |
| `--success` / `--success-soft` | `#1F8A5B` / `#E6F4EC` | `#3FB67F` / `#14281F` | 成功 |
| `--warning` / `--warning-soft` | `#A86A12` / `#FBF1DF` | `#D9A441` / `#2B2314` | 警告、待确认 |
| `--danger` / `--danger-soft` | `#C8413B` / `#FBEAE9` | `#EB6A64` / `#2E1818` | 危险、失败 |
| `--ring` | `#4F5BD5` | `#9AA3F7` | 焦点环 |

- 正文与次要文字对背景的对比度都不低于 4.5:1；主色按钮白字对比度不低于 4.5:1。
- 状态色只用于状态本身（芯片、图标、进度），不做大面积底色。
- 旧页面使用的 Tailwind 颜色名（`card`、`muted`、`primary`、`positive`、`warning`、`negative`、`destructive` 等）在过渡期继续映射到新令牌，保证每个提交都能构建。

### 字体与字号

- 系统字体栈：`-apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", "HarmonyOS Sans SC", "MiSans", "Noto Sans SC", "Microsoft YaHei", system-ui, sans-serif`。等宽：`"SF Mono", "JetBrains Mono", ui-monospace, Menlo, Consolas, monospace`。不打包字体文件。
- 字号阶梯：12（标注）/ 13（次要）/ 15（正文）/ 17（区块标题、子页标题）/ 20（一级页面标题）/ 24、28（数字与大标题）。
- 输入控件字号 16px，避免 iOS 聚焦时自动缩放。
- 时间、计数、版本号等数字用 `tabular-nums`。

### 间距、圆角、层次

- 4px 栅格。页面左右内边距：手机 16px，桌面 24–32px。
- 圆角：卡片与列表组 12px，输入框与按钮 10px，芯片与胶囊全圆角，底部面板顶部 16px。
- 浅色主题用极轻阴影（`0 1px 2px rgb(16 18 24 / 0.04)`）区分卡片；深色主题只用描边，不用阴影。
- 不做卡片套卡片：一个页面区块最多一层容器，区块内部用分隔线或留白。

### 动效

- 150–250ms，`ease-out`。底部面板上滑、对话框淡入缩放、芯片与开关状态过渡。
- `prefers-reduced-motion: reduce` 时去掉动效。

### 图标与品牌

- 图标继续使用 lucide，统一 1.75 线宽，尺寸 16/18/20。
- 品牌标：石墨底圆角方块 + 靛蓝单线「M」。`web/public/` 下的 favicon 与 PWA 图标由 `scripts/generate_pwa_icons.py` 生成，文件名不变。
- manifest 与 `<meta name="theme-color">`：浅色 `#F6F7F9`，深色 `#0E1014`。

### 主题

- 默认跟随系统（原来默认深色），可在「更多」页和「设置 › 外观」切换浅色、深色、跟随系统。选择存 localStorage `maa-api-theme`。
- `index.html` 内联脚本在首帧前根据存储值与系统偏好设置 `dark` class 和 `theme-color`，避免闪烁。

## 交互规范

### 布局与导航

- 宽度 < 1024px：顶部为页面标题栏，底部为 Tab 栏（首页、任务、日志、Agent、更多）。
- 宽度 ≥ 1024px：左侧固定侧边栏，包含同样的主入口，以及「更多」里的次级入口（热更新、Agent 审计、API 调试台、设置）。不显示底部 Tab。
- 页面标题栏吸顶，适配 `safe-area-inset-top`；一级页面显示 20px 标题，子页面显示返回按钮和 17px 标题。标题栏右侧放页面操作和实时连接指示点。
- 虚拟键盘弹出时（触屏设备上文本输入框获得焦点）隐藏底部 Tab，让输入区和操作栏贴近键盘。
- 实时连接断开或重连中时，标题栏下方出现一行细提示；连接正常时不占空间。

### 弹层

- 同一个 `ResponsiveSheet`：手机上从底部升起（最高 90dvh，顶部抓手，内容可滚动，底部可放固定操作区），桌面上是居中对话框或右侧面板。
- 行内「更多操作」用 `ActionSheet`：一组带图标的动作按钮，危险动作用危险色。
- 所有消耗类、破坏类操作使用 `ConfirmDialog`（`role="alertdialog"`），标题说清动作，按钮文案写明后果（例如「清空 3 条待执行」），不再使用 `window.confirm`。

### 反馈

- 操作成功用 toast（sonner），2–3 秒自动消失。
- 需要用户处理的错误就地显示在触发位置附近（`InlineAlert`，`role="alert"`），并给出重试或下一步入口。
- 加载用骨架屏或行内加载提示（`role="status"`）；空状态说明原因并给出下一步操作；出错状态给「重试」。

### 表单

- 一个字段一个控件：标签在上，控件在下，说明文字默认一行、可展开，错误信息紧贴控件。
- 布尔值用开关；选项不超过 3 个的枚举用分段控件，其余用原生 `<select>`（手机上调出系统滚轮）。数字输入带步进按钮与范围提示，整数数组用芯片。
- 页面级表单的主操作放在底部吸附的操作栏（`StickyActionBar`），位于 Tab 栏之上，适配安全区与虚拟键盘。
- 有未保存改动时，离开页面前用应用内确认框拦截。

### 列表

- 列表行最小高度 48px；整行可点时右侧显示箭头；行内次要操作收进「更多操作」。
- 长列表用虚拟滚动（日志）或「加载更多」（历史、审计）。

### 可访问性

- 触控目标不小于 44×44px；所有图标按钮有 `aria-label`。
- 焦点环清晰可见；弹层打开时焦点进入弹层，关闭后回到触发元素（Radix 行为）。
- 颜色之外用文字或图标表达状态。

## 信息架构与路由

| 路由 | 页面 | 说明 |
|---|---|---|
| `/` | 首页 | `?confirm=<id>` 打开全局待确认面板并定位该条 |
| `/tasks` | 任务 › 新建 | 编排器 |
| `/tasks/queue` | 任务 › 队列 | |
| `/tasks/history` | 任务 › 历史 | |
| `/tasks/history/:pipelineId` | 流水线详情 | 子页面，返回历史列表 |
| `/tasks/schedules` | 任务 › 定时 | 从「更多」迁入 |
| `/tasks/schedules/new` | 新建定时任务 | 接受 `location.state.apiConsolePrefill` 与 `location.state.composerPrefill` |
| `/tasks/schedules/:scheduleId` | 编辑定时任务 | |
| `/logs`、`/logs/:source` | 日志 | `source` 为 `task`、`service`、`core` |
| `/agent` | Agent | `?session_id=` 选择会话 |
| `/more` | 更多 | 次级入口、外观、登出、关于 |
| `/more/updates` | 热更新 | |
| `/more/audit` | Agent 审计 | 筛选与页码写入 URL |
| `/more/api-console`、`/more/api-console/guide` | API 调试台、接入指南 | |
| `/more/settings` | 设置索引 | |
| `/more/settings/:section` | 设置分区 | `appearance`、`access`、`device`、`agent`、`notifications`、`maintenance`、`system`、`other` |
| `/more/schedules` | 重定向 | 到 `/tasks/schedules`，保留 `location.state` |
| `/login` | 登录 | `?returnTo=` |

任务区的四个子视图（新建、队列、历史、定时）共用一个分段导航，切换写入浏览器历史。详情页与编辑器是子页面，不显示分段导航。

设置分区与 schema `group` 的映射：`设备`、`渠道` → `device`；`LLM`、`Agent` → `agent`；`更新`、`日志` → `maintenance`；`应用`、`部署` → `system`；未知分组 → `other`。`appearance`（主题）与 `access`（本机 token）是纯前端分区；`notifications` 包含 Web 推送与通知通道。

## 逐页规格

### 首页

只呈现「此刻」：

- **待确认横幅**：有待确认项时显示「N 项操作等待你确认」，点开全局待确认面板。
- **当前任务**：标题、状态、进度条（`role="progressbar"`，按已完成数 / 总数）、正在执行的步骤；「全部步骤」可展开（名称、耗时、状态）；运行中可「停止任务」（确认后取消流水线）；「查看详情」进入流水线详情；无流水线时显示空状态。
- **快捷操作**：两块磁贴。「新建任务」（`/tasks`）；「运行定时任务」（面板列出定时任务与时间摘要，确认后 `POST /api/schedules/{id}/run`，成功 toast）。
- **运行状态**：一个列表组，设备、内核、服务、队列各一行，行内直接显示状态（圆点 + 文字）与关键信息（设备地址、重试进度与最近错误；PID 与世代；版本与运行时长；待处理与运行中计数）。队列行点击进入 `/tasks/queue`。设备断开或不可用时列表下方出现提示与「重新连接设备」；health 读取失败时显示错误与「重试连接」。实施时发现行内展示比「芯片 + 详情面板」少一次点击且信息不缺，改用列表。
- **设备画面**：16:9 卡片，右上角刷新按钮；点图片全屏查看；流水线运行中每 5 秒自动刷新，空闲时只手动刷新；请求带 `backend=adb&format=jpeg&size=mobile`。
- 桌面端两列：左列任务、快捷操作与运行状态，右列设备画面。

### 全局待确认

- 挂在外壳上，任何页面都能收到。首页以外的页面，有待确认项时在 Tab 栏上方（桌面在右下角）显示胶囊「N 项待确认」。
- 点胶囊或首页横幅打开待确认面板。每条确认显示标题（工具名或「授权会话操作游戏界面」）、风险类别与原因、倒计时（到期显示「已过期」并禁用按钮）、调用方、可折叠的「查看操作参数」JSON、「批准」与「拒绝」。会话授权类显示目标会话与授权时长。
- `?confirm=<id>`（任何路由）自动打开面板，高亮并滚动到该条；处置后或关闭面板时清掉参数；目标已处理或已过期时提示一次「这条待确认操作已经处理或已过期」。
- 409 时提示已处理并刷新列表；WS 的 `confirm_request`、`confirm_resolved`、`subscribed` 触发重新读取（沿用 query-bridge）。

### 任务 › 新建（编排器）

- 顶部可选的流水线名称输入。
- 任务列表：每个任务一行，显示类型图标、中文名、关键参数摘要（从用户改动过的字段生成，例如「1-7 · 理智药 2」；未改动显示「使用默认参数」）、错误角标。点整行打开任务编辑器；行右侧的「更多操作」提供上移、下移、复制、删除。
- 「添加任务」打开类型面板：10 种任务类型各带图标与一句说明，点选即加入并直接打开编辑器。上限 32 个任务。
- 底部吸附操作栏：主按钮「开始执行（N 项）」，次按钮「存为定时」。无任务时主按钮禁用。
- 提交流程保持原语义：先 `POST /api/tasks/validate`，通过后 `POST /api/pipelines`（带 `Idempotency-Key`）；有 `x-risk` 字段被改动时先弹确认；422 错误映射到对应任务行的角标与字段；504 时查询 `/api/pipelines/current` 并复用同一 key 重试。成功后 toast 并提供「查看队列」。
- 「存为定时」：跳到 `/tasks/schedules/new`，通过 `location.state.composerPrefill` 带上任务模板与名称。

### 任务编辑器

- 手机全屏面板，桌面右侧面板。标题为任务类型，右上角「完成」。
- 按 schema `groups` 分组：第一组展开，其余折叠，折叠头显示「已修改 N 项」。
- 每个字段只有一个控件：
  - 未改动时显示内核默认值（有 `default` 时显示为占位或开关状态，并标注「默认」）；改动后标签旁出现圆点，并出现「恢复默认」按钮（清除该字段的改动，回到不提交状态）。
  - 可空字段的「明确留空」收进字段的「更多」菜单，只在 schema 允许 null 时出现；选中后控件显示「已设为空」。
  - 布尔值用开关；选项不超过 3 个的枚举用分段控件，其余原生下拉；数字带步进与范围；字符串单行或多行输入。
  - 说明文字默认一行截断，点「展开说明」看全文。
  - `x-depends-on` 未满足时控件禁用，并说明依赖哪个字段；`runtime_immutable` 字段标注「运行中不可修改」。
- 专用控件：掉落物（搜索中文名，选中后以芯片显示并可填数量）、公招时长（每行一个星级，时:分两个数字框）、有序列表（上下移动与删除）、标签（芯片 + 输入回车添加）、Copilot 作业（列出作业，只存 `asset_id`）、键值映射、复杂结构回退为 JSON 文本框并校验。
- 字段错误显示在控件下方；打开编辑器时滚动到第一个错误字段。

### 任务 › 队列

- 顶部状态行：运行中与待执行计数；「暂停队列 / 恢复队列」开关按钮。
- 当前运行项单独置顶显示（标题、来源、开始时间），可「停止」。
- 待执行列表：标题、来源（手动/Agent/定时）、优先级、状态。行内「更多操作」：「取消」、「停止当前并执行此条」（先 `PATCH /api/queue/{id}` 把优先级设为 0，再取消当前流水线；确认文案说明不保证紧接着执行）。
- 页面菜单：「刷新」、「清空待执行」（确认框写明条数）。保持 5 秒轮询。

### 任务 › 历史与详情

- 列表：状态图标、标题（无标题时显示任务类型组合）、开始时间、耗时、任务数。底部「加载更多」。点行进入 `/tasks/history/:pipelineId`。
- 详情页：概要（状态、优先级、来源、创建/开始/结束时间、错误），每个任务的结果，关联日志（最近 100 条，可跳转日志页），截图网格（点开全屏）。底部操作栏：运行中或等待中可「取消」，失败或已取消可「重试」。
- 深链直接打开详情，不要求该流水线在列表当前页。

### 任务 › 定时

- 列表：名称、启用开关（行内直接 `PATCH`）、时间摘要（例如「周一至周五 04:00」；无法可视化时显示原始 cron）、下次执行、最近结果芯片（含 skipped 与错误）、任务数。点行进入编辑器。
- 编辑器（整页）：名称；星期芯片（一至日，另有「每天」「工作日」「周末」快捷）；时间（原生 `type="time"`）；「高级」折叠区含时区；无法可视化的 cron 显示原始表达式输入并说明；任务列表复用编排器组件；底部操作栏「保存」。页面菜单：「立即运行」「删除」（都需确认）。
- 去掉写死为 2 的优先级控件，改为一行说明「定时任务以最低优先级排队」。
- 409 名称冲突时就地提示，编辑器保持打开。
- 旧 `daily_task.json` 迁移说明收进列表页底部的说明文字。

### 日志

- 列表占满剩余高度，虚拟滚动。标题栏下方吸顶工具条：来源分段（全部/任务/服务/核心，对应路由）、搜索框、筛选按钮（有生效条件时显示数量）。
- 筛选面板：级别多选芯片（全部、DEBUG、INFO、WARNING、ERROR、CRITICAL）、时间范围（快捷「最近 15 分钟」「最近 1 小时」「今天」+ 自定义起止）。生效的条件以芯片显示在工具条下方，每个可单独移除，另有「清除全部」。
- 行：等宽时间、级别色条与级别文字、来源、内容（两行截断，点行展开全文并显示 logger，可「复制」）、截图缩略图（点开全屏原图）。
- 滚到顶部或点「加载更早」读取更早历史；不在底部时有新日志显示「N 条新日志」胶囊，点击回到底部；在底部时自动跟随。
- 实时离线、历史截断时的提示与「加载缺失历史日志」补齐按钮保持现有语义。

### Agent

- 手机全高对话；桌面左侧会话栏 + 右侧对话区。
- 标题栏显示当前会话标题（点开会话面板：会话列表、新建、删除（确认）、自动诊断徽章）；右侧菜单含「检查模型能力」，结果与降级原因显示在对话顶部的提示条中。
- 会话授权条固定在标题栏下方：「已授权屏幕操作 · 剩余 12 分钟」与「撤销」。
- 消息：用户消息右侧气泡；助手消息左侧正文排版；工具调用显示为可折叠的紧凑条（工具名、状态、参数与结果）；等待确认时对话内出现确认卡片（批准并继续 / 拒绝）。
- 输入栏固定在底部，随内容增高（最多约 6 行），通过 `visualViewport` 避让虚拟键盘。桌面 Enter 发送、Shift+Enter 换行；触屏设备 Enter 换行，用发送按钮发送。流式进行中发送按钮禁用并显示「正在处理」。
- 空会话显示三条快捷提示（点选填入输入框，不直接发送）。无会话时显示「新建会话」空状态。
- LLM 配置相关错误给出「前往设置」链接（`/more/settings/agent`）。
- 消息读取：先读首页拿到 `total`，再读最后一页；向上滚动到顶部或点「加载更早」时读取前一页。

### 设置

- 索引页：iOS 式分组列表，每个分区一行（图标、名称、一句摘要），点进分区子页。
- 分区子页：schema 驱动字段。布尔 → 开关；枚举 → 下拉；数字 → 数字框（显示范围）；整数数组 → 芯片输入；字符串 → 输入框；敏感字段显示「已配置 · 已脱敏」，输入框为 password 且初始为空，留空不提交。字段下方小字标注生效方式（立即生效 / 保存后重连设备 / 需重启服务）与来源（默认值 / 配置文件 / 数据库覆盖）；只读字段显示为只读文本。不显示原始 key。
- 每个子页独立保存：只提交改动过且非只读的字段；有改动时底部出现「保存」操作栏；保存成功 toast；`SETTING_APPLY_FAILED` 且已写入时提示「已保存但生效失败」。「恢复默认」清除本分区的数据库覆盖（确认后 `POST /api/settings/reset`）。有未保存改动时离开被拦截。
- `agent` 分区顶部是「检查模型能力」（显示工具调用、视觉输入是否可用与降级原因）。
- `access` 分区：替换或清除本机访问 token。
- `appearance` 分区：主题三选一。
- `notifications` 分区：Web 推送（权限状态、启用 / 注销本机、已登记设备列表与删除；非安全上下文显示说明且不提供订阅控件；权限被拒时说明如何在浏览器里重新开启）与通知通道（列表：名称、类型、启用开关、测试、编辑、删除；编辑器：类型下拉、名称、按类型的字段、订阅事件多选芯片、启用；「以 JSON 编辑」切换到原始配置文本框）。
- 通知通道类型字段对齐 `maa_api/services/notify_service.py` 的配置模型：
  - `email`：`server`、`port`、`use_tls`、`username`、`password`、`to[]`
  - `webhook`：`url`、`method`（POST/PUT）、`headers`（键值）、`body_template`
  - `bark`：`server`（默认 `https://api.day.app`）、`device_key`、`sound`、`group`
  - `dingtalk`：`webhook`、`secret`、`at_mobiles[]`
  - `wecom`：`webhook`、`mentioned_list[]`
  - 事件：`pipeline_completed`、`pipeline_failed`、`core_crashed`、`device_disconnected`、`update_available`、`update_finished`、`confirmation_required`

### 热更新

- 顶部：上次检查时间（是否为缓存结果）、「立即检查」。有目标在更新时说明三个目标共用一条更新队列。
- 三个目标（内核、活动资源、游戏）各一张卡：名称、当前版本 → 最新版本、状态芯片（可更新 / 已最新 / 尚未检查 / 检查失败）、一个主操作（更新至 x、安装 stable 内核、立即生效、下载并安装、重新安装）。资源卡显示仓库与 OTA 两个通道的可用性。
- 点主操作打开选项面板：资源范围（全部 / 仅 OTA / 仅仓库）、生效方式（等待空闲 / 延后 / 立即）、「允许强制中断正在运行的流水线」（打开时说明后果），确认后提交。
- 进行中：阶段、百分比进度条，第二行显示已下载 / 总量、速度、剩余时间；下载阶段可「取消更新」，应用阶段说明不可取消。
- 最近更新记录：状态、目标、时间；点开详情（编号、阶段、错误码与消息、结束时间、日志）；失败可「重试」；「显示最近 N / 共 M」。

### Agent 审计

- 工具条：「筛选」按钮（打开筛选面板：调用方、工具名、状态、风险、开始日期）+ 生效条件芯片 + 刷新。筛选与页码写入 URL，可分享与还原。
- 列表行：工具名、状态与风险芯片、调用方 · 时间 · 耗时、错误码。点行打开详情（手机底部面板、桌面右侧）：审计编号、request / session / confirmation / 授权信息、参数 JSON、结果摘要、「查看流水线与日志」（`/tasks/history/:pipelineId`）。
- 分页：上一页、下一页与页码。

### API 调试台

- 纯逻辑模块（`utils.ts`、`json-validation.ts`、`use-console-realtime.ts`）与其测试不动，只换外观。
- 宽度 < 1280px：三步工作流「接口列表 → 编辑请求 → 响应」，发送按钮在底部操作栏，响应与服务端日志在可展开的底部面板中。宽度 ≥ 1280px：三栏（接口树、请求编辑、响应与日志）。
- 接入指南整页显示（`/more/api-console/guide`），调试台头部只放「接入指南」链接，不再用浮层。
- 手机端在 Body 编辑器上方提供「格式化」按钮。
- 「转为定时任务」跳到 `/tasks/schedules/new` 并带 `apiConsolePrefill`。

### 登录、更多、404

- 登录：居中窄卡片，品牌标、说明、token 输入（默认掩码，可显示）、登录按钮、错误就地提示；非安全上下文说明显示在卡片内。
- 更多：分组列表（运维：热更新、Agent 审计、API 调试台；系统：设置）、外观分段控件、「退出登录」（确认后执行）、关于（版本、连接状态、非安全上下文说明）。
- 404：说明与「返回首页」。

## 行为基线

每条都要在新测试中有对应断言，复审时逐条核对。「新增」为本里程碑新增的行为。

### 外壳、登录与路由

- L1 主导航有五个带文字的入口：首页、任务、日志、Agent、更多；手机底部导航适配 safe-area。
- L2 API 调试台与接入指南路由使用全宽布局。
- L3 主题选择写入 localStorage `maa-api-theme`，同步 `<html>` 的 `dark` class 与 `theme-color`；默认跟随系统（新增）。
- L4 受保护页未登录时跳 `/login?returnTo=…`；token 输入默认掩码、可切换显示；登录 `POST /api/system/auth/cookie` 带 `X-Token`，成功后跳回 `returnTo` 并保存 token。
- L5 退出登录 `DELETE /api/system/auth/cookie` 带 `X-Token`，清除 token 并跳 `/login`。
- L6 登录 403 就地提示「服务拒绝了此认证请求」，保留已保存的 token。
- L7 非安全上下文时登录页显示 Service Worker 不可用说明。
- M1 「更多」页列出热更新 `/more/updates`、API 调试台 `/more/api-console`、Agent 审计 `/more/audit`、设置 `/more/settings`；定时任务不再在「更多」里。
- M2 路由表包含受保护的 `more/api-console`、`more/audit`。
- M3 直接打开子页面并支持浏览器前进后退；在子页面时「更多」入口 `aria-current="page"`。
- M4 `/more/schedules` 重定向到 `/tasks/schedules` 并保留 `location.state`（新增）。

### 待确认

- C1 读取待确认列表，显示操作影响（原因）；「查看操作参数」展开参数 JSON；「批准」提交 `{approved: true}`，成功后该条消失。
- C2 读取失败显示错误与「重试」。
- C3 会话授权类确认显示目标会话 ID 与授权分钟数。
- C4 409 竞态后刷新列表，该条消失。
- C5 WS `confirm_resolved`、`subscribed`、`confirm_request` 触发重新读取。
- C6 `?confirm=<id>` 高亮并滚动到该条；处置后清除参数。
- C7 深链目标已不在待处理列表时提示「这条待确认操作已经处理或已过期」。
- C8 没有待确认且没有深链时不显示入口。
- C9 没有深链时不高亮。
- C10 非首页有待确认时显示入口胶囊，点开面板；非首页的 `?confirm=` 深链也能打开面板（新增）。

### 首页

- D1 显示服务版本、内核 PID、设备地址、队列计数；空闲时可手动刷新截图，请求带 `backend=adb&format=jpeg&size=mobile`。
- D2 有流水线时显示步骤与 `progressbar`；无流水线显示空状态。
- D3 设备未连接显示占位；「重新连接设备」`POST /api/device/reconnect` 带 token，成功提示已发送。
- D4 health 503 显示错误，「重试」后恢复。
- D5 只有流水线运行中才每 5 秒刷新截图；空闲不轮询。
- D6 「运行定时任务」列出定时任务，确认后 `POST /api/schedules/{id}/run`（新增）。

### 日志

- G1 最新优先分页；来源在本地过滤；日志订阅只在日志页挂载期间开启。
- G2 按 lastSeen 用 `after_id` 升序补漏。
- G3 多级别 OR 过滤，级别不进 REST 查询。
- G4 截断时用 after/before 边界补齐历史。
- G5 空状态、错误与重试。
- G6 不在底部时新日志不抢滚动，显示「N 条新日志」。
- G7 长列表虚拟化；截图缩略图懒加载，点开看原图。
- G8 生效的筛选条件以芯片显示，可单独移除（新增）。

### 任务

- T1 10 种任务类型 fixture 的每个属性标签（`x-label`）都能在编辑器中渲染。
- T2 Copilot 读取作业列表，只保存 `asset_id`。
- T3 Fight 掉落物按中文名搜索，写入物品 id。
- T4 Recruit 公招时长按时:分换算为分钟。
- T5 `x-risk` 字段改动后提交前确认；只提交用户改过的字段；带 `Idempotency-Key`。
- T6 validate 的字段错误映射到对应控件（两种 details 形态）。
- T7 504 后查询 current，重试复用同一 key。
- T8 队列「停止当前并执行此条」先 `PATCH` 优先级，再 `DELETE` 当前流水线。
- T9 历史分页使用 page/size/total；详情包含日志与截图。
- T10 历史深链直接打开详情，不要求在当前页；从详情返回列表。
- T11 任务子视图切换写入浏览器历史，可前进后退。
- T12 队列暂停与恢复、清空待执行（确认）、取消单条；历史取消与重试。
- T13 「存为定时」把编排的任务带进新建定时任务编辑器（新增）。

### 定时任务

- S1 星期 + 时间生成 POSIX cron，并提交任务模板。
- S2 Copilot 模板只提交 `asset_id`。
- S3 列表显示 skipped 与错误；「立即运行」「删除」需要确认。
- S4 409 名称冲突时提示，编辑器保持打开。
- S5 `location.state.apiConsolePrefill.template` 预填并打开新建编辑器。
- S6 无法识别的 cron 原样保存。
- S7 `schedule-time` 单测：周日为 0、每天为 `*`、多个星期排序、7 归一为 0、非法值拒绝。
- S8 列表行内启停 `PATCH {enabled}`；编辑后 `PUT` 保存。

### 热更新

- U1 显示三个目标与版本；手动检查失败时保留缓存数据并提示「立即检查失败」。
- U2 选择资源通道并勾选强制中断，确认后才 `POST`；出现进度与「取消更新」；WS 后进度更新；可 `DELETE` 取消。
- U3 失败记录可查看详情（错误码与消息）并 `POST` 重试，随后出现进度。
- U4 内核版本未知时仍可「安装 stable 内核」并 `POST`。
- U5 字节数显示为「1.0 KB / 4.0 KB」这样的格式。

### Agent 审计

- A1 筛选（含 since 的 ISO 时间），查看详情可见 request 与参数，「查看流水线与日志」跳 `/tasks/history/pipeline-1`。
- A2 列表读取失败显示错误与「重试」。
- A3 筛选后翻页，URL 保留筛选并带 `page=2`。
- A4 URL 中 ISO 格式的 since 能还原到日期控件。

### 设置

- P1 显示「设备地址」当前值与来源「数据库覆盖」。
- P2 「LLM API 密钥」为 password 输入、初始为空、显示已配置且已脱敏，页面不出现明文。
- P3 只改设备地址并保存时，`PUT` 只包含该字段。
- P4 自动诊断开关从启用改为停用，单独保存 `false`。
- P5 「检查模型能力」后显示工具调用可用、视觉输入不可用与降级原因。
- P6 有未保存改动时离开分区被拦截（新增）。
- P7 通知通道按类型表单序列化为正确的 `config`；JSON 模式仍可编辑（新增）。
- P8 恢复默认（确认后 `POST /api/settings/reset`）；本机 token 替换与清除；Web 推送启用、注销与设备删除；通知通道新建、编辑、删除（确认）与测试。

### Agent

- AG1 显示对话历史，工具结果可折叠；撤销会话授权；「检查模型能力」显示降级与视觉不可用。
- AG2 发送后进入确认等待；批准后看到助手回复、确认卡消失；流结束后临时副本消失，不出现重复消息。
- AG3 长会话读取最后一页，可加载更早的消息（新增）。
- AG4 触屏设备 Enter 换行、桌面 Enter 发送（新增）。
- AG5 会话新建、切换（URL `?session_id=`）、删除（确认）；快捷提示填入输入框。

### API 调试台

- K1 从 `/openapi.json` 加载接口列表；`/docs`、`/redoc` 链接；接入指南页。
- K2 指南路由可返回调试台。
- K3 窄屏：分步工作流、返回列表、校验警告仍可发送、临时 token、响应面板，token 不写 localStorage。
- K4 宽屏（≥1280px）三栏；更窄时走分步工作流。
- K5 收藏保存、改名、重放（带会话 token）、删除，payload 不含凭据。
- K6 远程 Base URL + 临时 token 读写收藏。
- K7 远程收藏 CORS 失败可见。
- K8 远程 WS 独立，不影响共享实时连接与日志缓冲；截断提示。
- K9 网络失败写入本机历史且不含 token。
- K10 流水线收藏「转为定时任务」预填新建定时任务，且不写 `schedule-template` 存储。
- K11 `api-console.test.ts` 与 `use-console-realtime.test.tsx` 原样通过。

## 验收

- 自动化：后端 `.venv/bin/python -m pytest -q`；前端 `pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build`；`.venv/bin/python scripts/dump_openapi.py --check`；全部 `scripts/*_smoke.py`（`frontend_smoke`、`api_smoke`、`agent_smoke`、`push_smoke` 等）末行 `SMOKE OK`；`git diff --check`。Vitest 用例总数不低于基线（19 文件 / 148 项）。
- 视觉巡检（补充证据）：Playwright 在 375×812、390×844、768×1024、1440×900 下，每页浅色、深色各一张截图，记入台账。
- 补充实测（不阻塞）：iOS Safari 虚拟键盘、真机已安装 PWA 的安全区与触控，按 docs/13 §8.3 记录。
