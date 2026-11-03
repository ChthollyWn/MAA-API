# M16 前端 UI/UX 全面重构 · 实施计划

规格见 [specs/2026-09-27-m16-ui-redesign.md](../specs/2026-09-27-m16-ui-redesign.md)。本计划只改前端表现层与相关文档，不改后端接口。

## 全局约束

- 分支 `m16-ui-redesign`，从 `refactor/v2` 的 `b61afac` 切出；完成后 `git merge --no-ff` 回 `refactor/v2` 并打 `v2-m16`。不 push，不合并到 dev，不用 worktree。
- 工具链：`/opt/homebrew/bin/node`（v23.11.0）与 `/opt/homebrew/bin/pnpm`（11.1.3）。本机连不上 `registry.npmjs.org`，新增依赖用 `pnpm add … --registry=https://registry.npmmirror.com`；lockfile 只记录 integrity，不记录 registry。
- 每个任务先写失败测试并确认 RED，再实现到 GREEN。提交信息用 Conventional Commits（`feat(web): …`、`test(web): …`、`docs: …`）。
- 任务门禁：`cd web && pnpm typecheck && pnpm test --run && pnpm build`。标记任务完成前，主 agent 亲自重跑任务门禁与后端 `.venv/bin/python -m pytest -q`。
- 长输出重定向到文件，只读尾部和退出码。
- 每个提交都必须能构建、测试全绿：过渡期旧页面继续使用兼容的组件与颜色名；被重写的页面在同一提交内替换其测试。
- 不改：`web/src/api/*`、`web/src/realtime/*`、`web/src/stores/auth.ts`、`web/src/stores/log-buffer.ts`、`web/src/lib/idb.ts`、`web/src/pwa/push.ts`、`web/src/sw.ts`、`web/src/types/api.d.ts`、`web/openapi.json`、后端全部代码。
- `pnpm build` 会覆盖 `static/`，正在运行的 8002 服务会立即提供新界面；开发期视觉检查优先用 `pnpm dev`（5173，代理到 8002）。

## Task 1 · 设计令牌与主题

**产物**：`web/src/index.css`、`web/src/stores/ui.ts`、`web/index.html`、`web/vite.config.ts`（manifest 颜色）、`scripts/generate_pwa_icons.py`、`web/public/` 下的 favicon 与 PWA 图标。

- 先写失败测试：主题默认跟随系统；`applyTheme` 写入新 theme-color（浅 `#F6F7F9`、深 `#0E1014`）。
- 按规格重写色彩令牌、字体栈、字号、圆角、焦点环、`prefers-reduced-motion`；保留旧 Tailwind 颜色名的映射。
- `index.html`：去掉写死的 `class="dark"`，加首帧前的内联主题脚本；viewport 加 `interactive-widget=resizes-content`；theme-color 用新值。
- `scripts/generate_pwa_icons.py` 用 Pillow 超采样绘制品牌标，输出 `favicon.svg`、`apple-touch-icon.png`（180）、`pwa-192x192.png`、`pwa-512x512.png`、`pwa-maskable-512x512.png`（标志在 80% 安全区内）。
- 提交 `feat(web): introduce graphite and indigo design tokens`。

## Task 2 · 组件层

**产物**：`web/src/components/ui/` 下重写与新增的原语、`web/src/lib/toast.ts`、对应单测。

- 新依赖：`@radix-ui/react-dialog`、`@radix-ui/react-alert-dialog`、`@radix-ui/react-switch`、`@radix-ui/react-toggle-group`。
- 重写：Button（`default`/`primary`、`secondary`、`outline`、`ghost`、`destructive`、`link`；`sm`、`default`、`lg`、`icon`、`icon-sm`）、Input、Label、Badge（新增 `success`、`danger`、`neutral`、`primary` 变体，保留旧名）、Card、Skeleton、Tabs。
- 新增：Textarea、NativeSelect、Switch、SegmentedControl、Chip、Field、ListGroup / ListItem、Section、EmptyState / ErrorState / LoadingState、InlineAlert、Progress、Dialog、ResponsiveSheet、ConfirmDialog 与 `useConfirm`、ActionSheet、StickyActionBar、ImageViewer、Toaster。
- 单测覆盖：`useConfirm` 的确认与取消分支、ResponsiveSheet 打开关闭与标题、SegmentedControl 选择、Switch 的 `role="switch"` 与 `aria-checked`、ActionSheet 执行动作后关闭。
- 提交 `feat(web): rebuild UI primitives for touch-first layouts`。

## Task 3 · 应用外壳与路由

**产物**：`web/src/components/layout/`（AppShell、BottomTabBar、SideNav、TopBar、Page、nav 配置、键盘与 visualViewport hooks）、`web/src/routes/router.tsx`、`web/src/features/confirmations/`（面板 + 全局入口 + 深链）、`web/src/routes/login.tsx`、`web/src/routes/more/`、`web/src/routes/not-found.tsx`，及 `layout.test.tsx`、`more.test.tsx`、`confirmations.test.tsx`、`deeplink.test.tsx`。

- 先写失败测试：五个主入口在手机底部 Tab 与桌面侧边栏都存在；「更多」页入口与 `aria-current`；主题分段控件写 localStorage 与 theme-color；登出确认后调用 `DELETE /api/system/auth/cookie`；`/more/schedules` 重定向保留 state；非首页的 `?confirm=` 打开待确认面板；登录页行为（returnTo、掩码、403）。
- 路由按规格定型：任务区（`/tasks`、`/tasks/queue`、`/tasks/history(/:id)`、`/tasks/schedules(/new|/:id)`）、设置分区（`/more/settings/:section`）、重定向。尚未重写的页面暂时用 `LegacyPage` 包一层内边距挂上去。
- 全局待确认：`ConfirmationsPanel`（列表，保留深链高亮与清参数语义）+ `ConfirmationDock`（胶囊、面板、`?confirm=` 自动打开）挂在外壳。
- 提交 `feat(web): add adaptive app shell and route map`。

## Task 4 · 首页

**产物**：`web/src/routes/dashboard/`、`web/src/features/dashboard/`、`dashboard.test.tsx`。

- 按规格重写状态条与详情面板、待确认横幅、当前流水线（含停止）、队列摘要、快捷操作（运行定时任务）、截图卡片与全屏查看。
- 行为基线 D1–D6。
- 提交 `feat(web): redesign home around live status`。

## Task 5 · 任务表单引擎与任务编辑器

**产物**：`web/src/features/tasks/form/`（schema 归一、改动追踪、序列化、422 映射、摘要生成等纯逻辑 + 单测）、`web/src/features/tasks/editor/`（TaskEditor 与字段控件）。

- 先把现有 `web/src/features/tasks/index.tsx` 中的纯逻辑原样抽出并补单测锁住语义（RED 为新增的边界用例），再在其上实现新的字段控件。
- 行为基线 T1–T6 中与编辑器相关的部分；新增「恢复默认」「明确留空」「已修改 N 项」的单测。
- 提交 `refactor(web): extract task form engine` 与 `feat(web): rebuild task editor fields`。

## Task 6 · 任务页（新建、队列、历史）

**产物**：`web/src/features/tasks/`（编排器、类型面板、队列、历史列表、历史详情）、`web/src/routes/tasks/`、`tasks.test.tsx`（及从 `more.test.tsx` 迁来的任务路由用例）。

- 行为基线 T1–T13。旧的 `web/src/features/tasks/index.tsx` 页面代码在本任务删除。
- 提交 `feat(web): redesign task composer, queue and history`。

## Task 7 · 定时任务

**产物**：`web/src/features/schedules/`、`schedules.test.tsx`。

- 列表、整页编辑器、复用编排器组件；接受 `apiConsolePrefill` 与 `composerPrefill`。行为基线 S1–S8、T13。
- 提交 `feat(web): move schedules under tasks with a full-page editor`。

## Task 8 · 日志

**产物**：`web/src/routes/logs/`、`web/src/features/logs/`、`logs.test.tsx`。

- 全高虚拟列表、吸顶工具条、筛选面板与条件芯片、行展开与复制、新日志胶囊、截图查看。行为基线 G1–G8。`use-log-history.ts` 的数据逻辑不改。
- 提交 `feat(web): redesign logs for small screens`。

## Task 9 · Agent

**产物**：`web/src/features/agent/`、`AgentPage.test.tsx`。

- 会话面板与桌面会话栏、消息与工具调用条、授权条、确认卡片、避让键盘的输入栏、消息分页（最后一页 + 加载更早）。行为基线 AG1–AG5。
- 提交 `feat(web): redesign agent chat for mobile`。

## Task 10 · 设置

**产物**：`web/src/features/settings/`、`web/src/routes/settings/`、`settings.test.tsx`。

- 分区索引与子页、schema 控件映射、分区保存与恢复默认、未保存拦截、按类型的通知通道表单、Web 推送、本机 token、外观、模型能力自检。`parseFieldValue` 抽出为纯函数并补单测。行为基线 P1–P8。
- 提交 `feat(web): split settings into focused sections`。

## Task 11 · 热更新、审计、API 调试台

**产物**：`web/src/routes/updates/`、`web/src/features/updates/`、`web/src/features/audit/`、`web/src/features/api-console/`（只改表现层）及对应页面测试。

- 行为基线 U1–U5、A1–A4、K1–K11。调试台三栏断点改为 1280px；接入指南整页；「转为定时任务」跳 `/tasks/schedules/new`。
- 提交 `feat(web): redesign updates, audit and API console`。

## Task 12 · 文档、验证与合并

- 同步 `docs/09`（§2、§6、§7.3、§8、§11）、`docs/10` §4、`docs/11` 的界面描述、`docs/15` 的页面路径、`docs/README.md`（需求 1 导航行与首行完成状态）、`docs/12`（M16 章节）、`docs/13` §8.12（偏离记录）。
- 删除不再使用的旧组件与样式；`rg` 确认没有 `window.confirm`。
- 全量验证：后端 pytest、前端五项、`dump_openapi.py --check`、全部 smoke、`git diff --check`。
- 视觉巡检：四种视口 × 浅深两套主题截图，记入台账。
- 独立复审（不带开发上下文的子 agent），按行为基线逐条核对并修复发现的问题。
- 台账复制到 `docs/superpowers/ledgers/2026-09-27-m16-ui-redesign.md` 并提交；`git merge --no-ff m16-ui-redesign` 进 `refactor/v2`，打 `v2-m16`。
