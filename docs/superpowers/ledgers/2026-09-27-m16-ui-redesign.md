# SDD ledger — plan: docs/superpowers/plans/2026-09-27-m16-ui-redesign.md

## Setup

- Base: `b61afac`（`docs: record M14 and M15 completion with final verification numbers`），分支 `m16-ui-redesign`，直接在当前检出上工作（AGENTS.md 禁止 worktree）。
- Spec：`docs/superpowers/specs/2026-09-27-m16-ui-redesign.md`；计划：`docs/superpowers/plans/2026-09-27-m16-ui-redesign.md`。
- 开工前工作区干净。
- 基线前端检查（`/opt/homebrew/bin/node` v23.11.0、pnpm 11.1.3）：`gen:api:check`、`typecheck`、`test --run`（19 文件 / 148 项）、`build`（含 sw_harness）全部退出码 0。
- 网络：`registry.npmjs.org` 8 秒超时不可达，`registry.npmmirror.com` 200 可达；新增依赖用 npmmirror。
- 现状问题（390px 实测截图，存于 `~/.playwright-mcp/maa-ui-current/`）：任务表单一个 Fight 任务约 4500px 高；日志筛选器占满首屏；顶栏被主题切换占去一半；桌面端是窄列 + 底部 Tab。

## User decisions

- 配色：石墨中性 + 靛蓝，深浅两套同等精调，默认跟随系统。
- 导航：首页 · 任务 · 日志 · Agent · 更多；定时任务并入「任务」，与新建 / 队列 / 历史并列。
- 计划已获批：纯前端、三项复用现有接口的增强、修复 Agent 消息分页遗留、不做停止生成。

## Rulings I made

- Ruling: iOS `apple-mobile-web-app-status-bar-style` 从 `black-translucent` 改为 `default` —— 默认改为跟随系统后，浅色主题下 translucent 的白色状态栏文字会看不见；代价：若某 iOS 版本不按 theme-color 着色状态栏，深色主题顶部会是一条浅色状态栏（可读但不协调），属真机补充实测项。
- Ruling: manifest 的 `theme_color` / `background_color` 用石墨 `#0e1014` —— 与石墨底图标一致，安装后启动画面无缝；页面内的 `<meta name="theme-color">` 仍按当前主题实时切换。
- Ruling: 图标由 `scripts/generate_pwa_icons.py`（Pillow 8 倍超采样）生成并支持 `--check` —— 本机无 rsvg/ImageMagick，脚本可复现；「any」图标带透明圆角，apple-touch 与 maskable 为满幅方块（系统自行裁切）。
- Ruling: 不单独封装 Dialog 组件，`ResponsiveSheet` 在桌面即对话框 —— 避免两套弹层；`ConfirmDialog` 用 Radix AlertDialog 保证 `role="alertdialog"` 与默认聚焦「取消」。
- Ruling: Toast 放在顶部居中 —— 手机底部被 Tab 栏与操作栏占用；sonner 显式传入解析后的主题，避免它在 system 模式下直接调用 `matchMedia`（jsdom 无此 API）。
- Ruling: `index.css` 里的 `@theme inline` 字体变量改为引用 `:root` 上的 `--app-font-sans` / `--app-font-mono` —— inline 主题变量不保证输出到 `:root`，toaster 等内联样式需要可解析的 CSS 变量。
- Ruling: 新增 `--nav-offset` / `--nav-safe` / `--keyboard-inset` 三个布局变量，由外壳维护 —— 底部操作栏、胶囊入口都按它们避让 Tab 栏与虚拟键盘，页面无需各自计算。
- Ruling: 底部 Tab 与侧边栏按 `useMediaQuery('(min-width: 1024px)')` 二选一渲染，而不是 CSS 同时渲染再隐藏 —— DOM 里始终只有一个「主导航」地标，读屏与测试都不会遇到重复链接；代价是依赖 JS 判断断点（jsdom 无 matchMedia 时按手机处理）。
- Ruling: 主题切换与登出从全局顶栏移到「更多」页，登出前弹确认框；非安全上下文说明不再每页常驻，只在登录页、「更多 › 关于」与推送设置里出现 —— 顶栏只留标题与页面操作。
- Ruling: 实时连接提示只在 OFFLINE / RECONNECTING 时出现在标题栏下方，首次连接中（CONNECTING）不提示 —— 避免每次打开页面闪一条黄条。
- Ruling: 待确认数量由外壳里的 ConfirmationDock 查询后写入 zustand（`stores/confirmations-ui.ts`），标题栏入口与首页横幅只读 store —— 页面单独渲染（测试、嵌入）时不会多发请求；未登录时 Dock 不发请求，避免 401 触发全局登出流程。
- Ruling: 标题栏返回按钮在应用内有上一页时执行浏览器后退，否则（例如从通知深链直接打开）以 replace 跳到上级页面 —— 不在历史里制造来回循环。
- Ruling: 触屏设备上文本输入获得焦点，或 visualViewport 缺口 ≥120px 时隐藏底部 Tab —— 让输入区与操作栏贴近键盘；鼠标键盘设备不隐藏。

## 环境事项

- 后端 pytest 中 `tests/api/test_push_subscriptions_router.py` 的 10 项失败（401「缺少 access token」）：本机 `config.yaml` 于 2026-09-27 11:18 设置了 `app.access_token`，而该测试只 monkeypatch 了 `maa_api.settings.get_settings`，`maa_api/api/deps.py` 在导入时已绑定原函数，读到真实 token。与 M16 无关（未改后端），在基线提交上同样失败；不在本里程碑修改后端测试，最终汇报时告知用户。

## Task progress

- Task 0（spec / 计划 / 台账）：完成，提交 `358bff7`。
- Task 1（设计令牌与主题）：完成，提交 `a762a81`。先写 `stores/ui.test.ts` 确认 RED（默认仍为 dark、theme-color 为旧值），实现后 GREEN。主 agent 验证：typecheck 0；vitest 20 文件 / 152 项；build + sw_harness 0；`frontend_smoke.py` SMOKE OK；`generate_pwa_icons.py --check` 0。
- Task 2（组件层）：完成，提交 `3d2b1c3`。新增依赖经 npmmirror 安装（lockfile 无镜像地址）。`components/ui/ui.test.tsx` 先于组件写成（RED 为模块不存在），14 项 GREEN。主 agent 验证：typecheck 0；vitest 21 文件 / 166 项；build + sw_harness 0。
- Task 3（外壳与路由）：完成，提交 `9c1c97a`。先写新的 `layout.test.tsx`、`more.test.tsx`、`dock.test.tsx`（RED 为模块与入口不存在），实现后 GREEN；登出用例首次失败是测试桩未覆盖 `/api/system/auth/cookie`，补桩后通过。主 agent 验证：typecheck 0；vitest 22 文件 / 174 项；build + sw_harness 0；`frontend_smoke.py` SMOKE OK；后端 pytest 1606 passed / 4 skipped / 10 failed（均为上述推送订阅环境问题）。视觉检查（Playwright，5173 开发服务器与 8002 构建）：登录页、更多页手机浅/深色与桌面侧边栏正常；8002 同源下实时连接为「已连接」。开发服务器上的「实时连接已断开」是 5173 不在后端 WS 来源白名单里的开发期现象。
- Task 4（首页）：完成，提交 `972ff18`。新写 `dashboard.test.tsx`（11 项，覆盖 D1–D6 与停止任务、全屏看图、待确认横幅）与 `schedule-time.test.ts`（描述函数 4 项）。删除被替换的 `RuntimeStatus.tsx`、`PipelineProgress.tsx`；首页路由改为 `page(...)`。主 agent 验证：typecheck 0；vitest 23 文件 / 183 项；build 0；后端 pytest 1605 passed / 4 skipped / 11 failed——其中 10 项为推送订阅环境问题，另 1 项 `tests/core/test_crash_recovery.py::test_backoff_sequence_grows_then_failed_after_budget` 在并发构建负载下超时，单独重跑 3 次均通过（时序敏感，与 M16 无关）。视觉检查：8002 构建下手机浅/深色与桌面两栏正常，无横向溢出。
- Ruling（Task 4）：首页运行状态从「芯片 + 详情面板」改为行内列表（设备、内核、服务、队列各一行，状态与关键数字直接可见）——少一次点击且信息不缺；spec 同步修改。
- Ruling（Task 4）：首页新增「停止任务」（`DELETE /api/pipelines/{id}`，先确认）与「查看详情」入口，复用现有接口。
- Task 5（表单引擎与编辑器）、Task 8（日志）、Task 9（Agent）：并行派发给子 agent，简报 `task-5-brief.md`、`task-8-brief.md`、`task-9-brief.md`，共用约定 `design-brief.md`。
- Task 5：完成，提交 `6c55962`（form/，47 项单测）与 `b688249`（editor/，23 项）。报告 `task-5-report.md`（导出签名、22 条实现裁决、Task 6 接入注意点）。RED 为模块不存在，GREEN 70/70。
- Task 8：完成，提交 `ecfb33f`。报告 `task-8-report.md`。RED：新测试对旧实现 10 失败 / 4 通过（通过的是旧实现本就满足的行为）；GREEN 28/28。`use-log-history.ts` 未改。
- Task 9：完成，提交 `c1d0244`。报告 `task-9-report.md`。RED：13 条中 11 条失败；GREEN 26/26；另做 5 处变异验证（合流、刷新前丢弃、确认挂起例外、错误存放、滚动位置），均被测试抓到。重复渲染防护保留在 `AgentPage.tsx` 的 `send()` 中（流结束后等四个查询刷新完成再丢弃实时副本）。
- 主 agent 审核与修复（提交 `20a1599`）：
  - 三个子 agent 共同发现 `cn()`（tailwind-merge）把 `text-footnote` 等自定义字号当成文字颜色，与 `text-muted-foreground` 合并时吞掉字号——在 `lib/utils.ts` 用 `extendTailwindMerge` 注册自定义字号、阴影、动画，并补 `utils.test.ts`。
  - `NativeSelect` 因 `<select>` 恒为 `:read-only` 而总是灰底——只读外观改为只给 Input / Textarea。
  - 日志、Agent 路由从 `legacy(...)` 切到 `page(...)`。
  - 全量并行下 API 调试台页面测试偶发超时（冷加载 CodeMirror 分块、JSON 校验防抖；该文件已有注释说明基线上即 3 次失败 2 次）：vitest `testTimeout` 设为 15 秒，两处等待放宽。之后全量连续 3 次 301/301 通过。
  - 静态检查：三块新代码无 `window.confirm`、无调色板颜色类、无硬编码色值。视觉检查：子 agent 截图（编辑器、日志、Agent）与 8002 真实数据下的日志页（950 条、实时已连接）正常。清理了 Task 9 子 agent 留在共享 Playwright 标签页里的接口拦截。
  - 主 agent 验证：typecheck 0；vitest 34 文件 / 301 项；build + sw_harness 0。
- Ruling（Task 5，接受）：连战次数（Fight.series）从 `x-enum-labels` 推导下拉选项（旧页面是空下拉，无法设置）；公招 Tag 等级改为 1–6 芯片（规格「整数数组用芯片」）。两处都是可见行为改进，记入 docs/13 §8.12。
- Ruling（Task 9，接受）：SSE `error` 事件写入可见的本轮错误（旧代码写进从不渲染的字段）；自检成功也给出「模型能力正常」提示——两者补上 docs/13 §8.7 的遗留，记入 §8.12。
- 遗留（Task 9 报告，未改）：本轮确认若在全局面板或推送里被处置，流结束时这一轮会因「确认仍挂着」被保留而与历史重复；属受保护的合流逻辑，记为后续事项。待确认列表仍按全局前 100 条过滤（docs/13 §8.7 原遗留）。
- Task 6（任务页）、Task 10（设置）、Task 11a（热更新与审计）：并行派发，简报 `task-6-brief.md`、`task-10-brief.md`、`task-11a-brief.md`。
- Task 6：完成，提交 `20e8d06`。报告 `task-6-report.md`（21 条实现裁决、Task 7 接入方式）。RED 为模块不存在；GREEN 14 文件 / 124 项；删除旧 `features/tasks/index.tsx`（1333 行）与 `tasks.test.tsx`，迁出 `more.test.tsx` 的 3 条任务路由用例。
- Task 10：完成，提交 `37f7327`。报告 `task-10-report.md`。RED：页面 30 条中 28 条失败；GREEN 4 文件 / 55 项；4 处变异验证。
- Task 11a：完成，提交 `18dcd57`。报告 `task-11a-report.md`。RED 15/15 失败；GREEN 15/15 连续 3 次；5 处变异验证。
- 主 agent 审核与修复（提交 `bcf3451`、`0a50c16`）：
  - Task 6 为无标题流水线逐条读详情取任务名（一页历史最多多 20 个请求）——改为无额外请求的兜底：定时触发的用定时任务名（共享的 `['schedules','list']` 查询，一次请求），其余为「来源 + 任务数」（如「手动任务 · 3 项」）；详情页仍用任务名组合。对应测试改为断言不读详情。
  - 发现 `['schedules']` 这个 key 在旧定时任务页里存数组、首页快捷面板存分页对象——同 key 两种形状会互相污染。新建 `features/schedules/queries.ts` 的 `schedulesQuery`（key `['schedules','list']`，旧页面对 `['schedules']` 的失效仍能覆盖），首页与历史改用它。
  - `StickyActionBar` 桌面无 `summary` 时按钮靠左——改为 `lg:ml-auto`。
  - 真实服务上发现 PWA「新版本可用」提示与页面主操作栏同位、挡住「开始执行」——提示移到顶部安全区下方，并加「稍后」。
  - 设置、热更新、审计路由切到 `page(...)`；未保存拦截 hook 移到 `lib/use-unsaved-changes.tsx` 供定时任务编辑器复用。
  - 主 agent 验证：typecheck 0；vitest 41 文件 / 377 项；build + sw_harness 0；`frontend_smoke.py` SMOKE OK。真实服务（8002）视觉检查：任务新建 / 类型面板 / 全屏编辑器 / 历史 / 设置设备分区 / 热更新均正常（只在前端操作，未提交任何任务）。
- Ruling（Task 6，接受）：详情页日志改为取最近 100 条（`order=desc&size=100`，再按时间正序显示）——规格原文是「最近 100 条」，旧实现取的是最早 100 条。
- Ruling（Task 10，接受）：敏感项「来源为默认值」显示「未配置」，其余「已配置，已脱敏」；「清除本机 token」沿用旧行为不注销 cookie（「更多 › 退出登录」负责全清）；`string[]` 字段按数组提交。
- 发现的后端缺陷（未修，超出 M16 纯前端范围，最终汇报告知用户）：`SettingService._validate` 的类型表没有 `string[]`，`deployment.cors_origins` 无论提交数组还是字符串 `PUT /api/settings` 都返回 500；旧前端同样无法保存。建议修法见 `task-10-report.md`。
- Ruling（Task 11a，接受）：三个更新目标都走选项面板（保留对所有目标生效的「强制中断」）；有可用更新时状态芯片优先显示「可更新」；能显示 `{error:{message}}` 格式的错误消息本身。
- Task 7（定时任务）、Task 11b（API 调试台）：并行派发，简报 `task-7-brief.md`、`task-11b-brief.md`。
- Task 7：完成，提交 `7db3be6`。报告 `task-7-report.md`（18 条实现裁决）。RED 为模块不存在 + 2 条新描述用例失败；GREEN 5 文件 / 55 项；3 组变异验证。删除旧 `features/schedules/index.tsx`；`@/features/tasks` 的 `DynamicTaskForm` 兼容导出已移除。
- Task 11b：完成，提交 `a220e3d`。报告 `task-11b-report.md`。RED 12/12 失败；GREEN 12/12；7 处变异验证；纯逻辑模块与测试零改动（K11）。顺带修复 5 个页面层缺陷（历史恢复误绑收藏并 PUT 历史 id、带路径参数的历史无法恢复、删除任意收藏清掉当前绑定、改 header 名丢焦点、status 0 显示为绿色）。
- 主 agent 收尾（提交 `7c70ebd`）：
  - API 调试台两条路由切到 `page(...)`；删除过渡用的 `LegacyPage` 与 `legacy()`，以及不再使用的 `PageHeader`、`card.tsx`、`tabs.tsx`（连同依赖 `@radix-ui/react-tabs`）、Badge 旧变体名、`index.css` 旧颜色别名。
  - 编排器提示区补底部滚动边距（Task 7 报告指出同类遮挡问题）。
  - 测试 `setup.ts` 统一 `configure({ asyncUtilTimeout: 5_000 })`：页面均为懒加载分块，全量并行时冷加载超过默认 1 秒（子 agent 报告 5 处偶发超时）。
  - 主 agent 验证：typecheck 0；vitest 连续两次 44 文件 / 418 项；build + sw_harness 0；后端 pytest 1605 passed / 4 skipped / 11 failed（10 项推送订阅环境问题 + 崩溃恢复时序用例在并发负载下超时，单独运行通过）。
- Ruling（Task 7，接受）：编辑已有定时任务时数据取自共享列表（沿用旧页面「不读单个定时任务」）；原始 cron 模式提供「改为按星期设置」切回入口；保存或删除后以 `replace` 离开编辑页。
- Ruling（Task 11b，接受）：窄屏（<1280px）在列表步骤也提供「请求环境」入口；响应抽屉内改为「响应 / 日志」分段；指南路由只渲染指南（不读 OpenAPI、不开专用 WS）；分步状态仍不进浏览器历史（与 docs/10 §4.1 一致）。
- Task 12（文档、验证、复审、合并）：进行中。

## 收尾（Task 12）

- 最终验证：四个 smoke（api / agent / push / frontend）均 `SMOKE OK`；`dump_openapi.py --check`、`generate_api_guide.py --check`、`generate_task_types_fixture.py --check`、`generate_pwa_icons.py --check`、`git diff --check b61afac..HEAD` 全部退出码 0。前端 `pnpm test --run` 44 文件 / 420 项（复审修复后）；`pnpm typecheck` 0；`pnpm build` + sw_harness 0。后端 pytest 1605 passed / 4 skipped / 11 failed：10 项为 `test_push_subscriptions_router.py` 的本机 token 环境问题（基线同样失败），1 项 `tests/core/test_supervisor.py::test_backoff_sequence_then_failed_after_budget` 在并发负载下超时，单独运行两次均通过。
- 文档同步：提交 `3846ea5`（docs/09、10、11、12、13 §8.12、15、README、规格）。
- 独立复审（`review-report.md`）：结论 NEEDS-FIX，无 P1，3 项 P2。修复提交 `8edaf82`：P2-1 通道脱敏范围与服务端 `_mask_config` 对齐（新增单测）；P2-2 深链目标缺失时即使列表非空也提示（新增测试），入口计数改用 `total`；P2-3 接入指南阅读宽度记入 docs/13 §8.12 并改写规格基线 L2。P3 项记录在 §8.12「复审后的修复与保留项」。
- 台账归档到 `docs/superpowers/ledgers/2026-09-27-m16-ui-redesign.md`。
