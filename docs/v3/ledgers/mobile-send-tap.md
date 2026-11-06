# 手机端发送按钮首次轻点被吞（维护性补丁台账）

- 分支：`mobile-send-tap`；基点：`dev` = `5209442`；合并：待合并后回填（`dev` 上的 `merge: 手机端发送一次轻点即发出（维护性补丁，见 docs/v3/13 §8.174）`）；tag：无（维护性补丁，不新增里程碑）
- 决策记录：[docs/v3/13 §8.174](../13-决策记录.md)
- 本任务无 spec/plan（bugfix）、无迁移、无后端改动、无接口/错误码/配置变化。

## 根因

- 手机输入栏 `docked` 时是 `fixed` 贴底、`bottom = calc(var(--nav-offset) + var(--keyboard-inset))`。轻点「发送」会让 textarea 先失焦、键盘收起；浏览器随后才合成 `click`，而这次合成 click 可能因为贴底输入栏随键盘/视口变化位移而不再派发到按钮（iOS Safari 也有「第一下轻点只用来收键盘」的已知行为）。无论 click 是被重定向还是根本没派发，`form` 的 `onSubmit` 都没执行。
- 修法能成立的关键是 touch 事件的目标锁定：一次触摸序列里所有 touch 事件都派发给 `touchstart` 的那个元素，所以 `touchend` 一定到达按钮，不受输入栏位移影响。这也是「第一下轻点没 click」在 jsdom 里的可测代理：只派发 touch 事件、不发 click。
- 桌面 `docked={false}`（输入栏在正常流里）、没有虚拟键盘位移，`click` 稳定命中，所以只有手机端出现。
- 排除另外两类：发送路径没有防抖/去重（`streamSse` 每次调用都发 POST，接口无幂等键；后端 orchestrator messages 路由也无去重）；也不是 `pending`/`disabled` 状态机（复现用例里草稿非空、`canSend` 为真、一次请求都没有）。
- 遗留不确定：具体真机时序（位移重定向 vs. iOS 直接吞 click）没有真机日志，本文按「两类机制都指向 touchend 发送」处理，真机复测待用户。

## 做了什么

| # | 提交 | 内容 |
|---|---|---|
| T1 | 本台账所在提交 | `features/agent/Composer.tsx`：发送按钮加 `onTouchEnd`，触屏轻点结束时直接 `onSend()` 并 `preventDefault()` 拦掉随后的合成 `click`；`ConsolePage.test.tsx` 新增回归用例；13 §8.174 与台账 |

## Rulings I made

| # | 裁决 | 理由 | 代价 |
|---|---|---|---|
| R1 | 用 `touchend` 发送，不用 `touchstart` | 轻点语义按抬起算，更贴近聊天应用的发送习惯；`touchstart` 变成按下即发，误触更糟。两者都靠 touch 目标锁定，不受输入栏位移影响 | 没有位移阈值：从发送按钮上起手拖动页面、又在按钮上抬手时会发出一条（按钮只有 44px、滚动一般起手在消息区，风险低）；加阈值反而可能吞掉手指轻微移动的正常轻点，故不加 |
| R2 | 只修发送按钮，不改「停止本轮」与 API 调试台的「发送请求」栏，也不动键盘/失焦逻辑 | 用户要求只修发消息路径、不顺手改无关交互；同一根因也影响其他贴底栏，但属于另外的交互，需要单独验收 | 手机端「停止本轮」「发送请求」在键盘收起时仍可能需要点两次；记为后续项 |
| R3 | 回归用例放 `ConsolePage.test.tsx` 集成层，只派发 touch 事件、不发 click | 与仓库现有触屏用例（同文件 Enter 换行、`api-console.page.test.tsx` 的手势 helper）习惯一致；在 jsdom 里用「touch 事件到达、合成 click 缺失」复现被吞的那一次轻点 | jsdom 没有真实虚拟键盘与 click 抑制，模拟不了真机时序；真机复测仍待用户 |
| R4 | 不加 `pointerdown`/`pointerup` 兜底 | 指针事件会同时与 touch 事件触发，需要额外做一次手势去重；且 jsdom 没有 `PointerEvent`，现有测试设施覆盖不到。touch 路径已在 iOS/Android 浏览器成立 | 触控笔等只发 pointer 不发 touch 的场景没有覆盖（现有代码本来就没覆盖） |

## 验证证据

| 命令 | 结果 |
|---|---|
| RED 对照（还原 `Composer.tsx` 到 `HEAD`） | 新用例失败：`Expected 1` / `Received 0`，退出码 1 |
| 独立复审（fresh context reviewer） | RED/GREEN 与「补发 click 仍只有 1 条」均独立复跑成立，结论 Approved；据此补了聚焦断言与 `defaultPrevented` 断言 |
| `cd web && pnpm test --run src/features/console/ConsolePage.test.tsx` | 33 条通过，退出码 0 |
| `cd web && pnpm gen:api:check` | `OpenAPI types are up to date.`，退出码 0 |
| `cd web && pnpm typecheck` | 通过，退出码 0 |
| `cd web && pnpm test --run` | **114 files / 994 tests passed**（新增 1 条），退出码 0 |
| `cd web && pnpm build` | `[sw_harness] SERVICE WORKER OK`，退出码 0 |
| `.venv/bin/python scripts/frontend_smoke.py` | 末行 `SMOKE OK` |
| `.venv/bin/python scripts/check_doc_links.py` | 末行 `BROKEN 0`（合并前在分支上跑） |
| `git diff --check` | 干净 |

## 未覆盖

- 真机复测（iPhone Safari / Android Chrome 键盘弹出时轻点发送）需要用户执行；jsdom 只能模拟「touch 事件到达、合成 click 缺失」的时序。建议验收清单：键盘弹出时点发送、拼音未上屏时点发送（IME 组合态）、键盘弹出时点「停止本轮」、从按钮上起手滑动页面。
- 「停止本轮」与 API 调试台发送栏的同类风险未验证、未修，见 R2。
- 只发 pointer 不发 touch 的触控笔没覆盖（现有代码本来就没有 pointer 路径）。
- `touchcancel` 路径不发送，退回旧的两次点击行为，不会比修复前更差。
