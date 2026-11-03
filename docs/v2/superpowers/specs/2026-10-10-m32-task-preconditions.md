# M32 · 任务执行前提（游戏状态）与流水线护栏

- 里程碑：M32（收尾轮 M31 之后的新需求）
- 分支：`m32-task-preconditions`，自 `refactor/v2`；完成后 `git merge --no-ff` 回 `refactor/v2` 并打 `v2-m32`
- 台账：`docs/superpowers/ledgers/2026-10-10-m32-task-preconditions.md`

## 1. 目标与用户决策

用户报告（原话）：「看我最新的会话，我问"我的克莱门莎练度怎么样"，agent 正常向我申请权限执行更新数据的任务队列，但是这个工具提示词需要优化，更新数据的任务，需要保证游戏启动状态才能执行，不能直接执行，否则会一直报错，maa 的所有小任务都有执行前提的，游戏启动或停留在某个页面，这些需要对知识库进行优化。」

用户裁决（两问都取推荐项）：

1. **范围**：知识库 + 工具提示词 + 代码护栏，三者都做；仍走里程碑流程（分支、spec、计划、台账、merge + tag）。
2. **护栏强度**：**提醒，不拦**。不改变用户已确认的提交，不阻止提交，只在确认卡片的原因与工具结果里给警告与失败提示，由模型决定是否停掉重提。

## 2. 事实基础

### 2.1 用户会话的实测证据

会话 `cbbc4bd1-88ff-4aa4-9d44-7628547a823c`（MaaCore v6.18.0，设备 127.0.0.1:5555）：

| 时序 | 事实 |
| --- | --- |
| 用户问「我的克莱门莎练度怎么样」 | `get_operator_box {"query": "克莱门莎"}` 返回 `{"items": [], "total": 0}` |
| `ask_user` 征得同意后 | 提交 `submit_pipeline {"tasks": [{"name": "OperBox"}], "title": "干员识别"}`，**没有 `StartUp`** |
| `wait_pipeline` | 等 201.27 秒后 `status="failed"`，`error={"code": "CORE_COMMAND_FAILED", "message": "内核报告任务失败：干员识别；请到日志页查看原因"}`；子任务 `retry_count=3 / max_retries=3 / retry_delay=30`、`status="skipped"` |
| 日志 | `任务失败 [OperBox]` 四次（08:26:54 / 08:27:49 / 08:28:47 / 08:29:42），随后「任务多次失败已跳过 [干员识别]，已保存现场截图」 |
| 现场截图 | 设备停在模拟器桌面，游戏没有启动 |
| 模型自己补救 | 改提 `[{"name": "StartUp", "start_game_enabled": true}, {"name": "OperBox"}]` |
| 自动诊断会话 `94a1aa1c-42d4-425d-a57d-f3ffd201eb97` | 三分钟内定性正确（游戏不在前台），但这是**事后**诊断 |

结论：失败后能定性，**事前提交没有任何前置条件约束**；失败代价是一次 200 秒量级的空跑加重试。

### 2.2 内核侧的任务前提（权威依据）

`resource/lib/maa/Darwin/resource/tasks/tasks.json`（MaaCore v6.18.0）里对外暴露的任务，第一步模板**完全相同**——主界面标志 `SwitchTheme@ToggleSettingsMenu.png`，`roi=[227,327,159,152]`，`action=DoNothing`：

| 任务 | 首步模板 | sub |
| --- | --- | --- |
| `StartUp` | 主界面标志 | next 落到 `StartUpBegin`（`StartUpThemes` → `GameStart` 点桌面图标 / `StartToWakeUp`） |
| `Fight` | 主界面标志 | `Terminal-Entry` |
| `Recruit` | 主界面标志 | `Recruit-Entry` |
| `Infrast` | 主界面标志 | `Infrast-Entry` |
| `Mall` | 主界面标志 | `Mall-Entry` |
| `Award` | 主界面标志 | `Task-Entry` |
| `OperBox` | 主界面标志 | `OperBox-Entry` |
| `Depot` | 主界面标志 | `Depot-Entry` |
| `Roguelike` | 主界面标志（`tasks/Roguelike/base.json`） | `Terminal-Entry` |

只有 `StartUp` 会拉起客户端；任务链里的 `@ReturnButtons` / `@QuickSwitch` / `*ReturnHome` 只处理「游戏内其它页面 → 主界面」。`CloseDown` 是接口级任务（`maa_api/model/core/task.py:79` 的 `CloseDownTask`，内核直接关客户端进程），不需要停在某一页。`Reclamation` 的入口任务在 `tasks/RA/Reclamation2.json` / `Reclamation3.json`（OCR 识别「开始／继续」），导航任务 `RA/plugin/base.json` 的 `RA@Navigation` 含从主界面进入的分支。

**含义**：每个任务的第一步匹配就隐含了「必须停在哪个界面」这一前提，这是「任务前提」的权威来源，可从 `tasks.json` 逐任务推导。

### 2.3 现有覆盖与缺口

- 32 条内置条目里**没有一条**讲任务前提（M30 后仍是 32 条）。
- 只有零散线索：`maa-api/agent/knowledge/builtin/account-resources.md:11` 先提 `StartUp`；`playbook-promotion-planning.md:32` 用 `[StartUp, Fight]`；`playbook-daily-weekly.md:10/26` 推荐 `StartUp → …`。
- **反向线索**：`maa_api/agent/knowledge/builtin/playbook-recruit.md:29` 写「tasks 里只放需要的那一个任务，不要塞 StartUp、Fight 等无关任务」——与「游戏没开就先 StartUp」直接冲突，是这次单独提交 OperBox 的同源诱因之一，必须就地消歧。
- 工具侧：`maa_api/agent/tools/pipeline.py:126` 的 `submit_pipeline` 描述只说「提交前先确认设备已连接、参数都来自用户」，没有游戏状态要求。
- 提示词侧：`maa_api/agent/runtime.py:212` 的「外部资料」段在讲 OperBox/Depot 识别时也没提前提；`runtime.py:200` 的「工具怎么用」段只有「开始操作游戏前先截图确认画面」。

## 3. 范围

### 3.1 本里程碑

1. **知识库**：新增内置条目 `task-preconditions`（§4.2、§4.6），并就地消歧与补齐既有条目（§4.3）。
2. **工具提示词**：`submit_pipeline`、`wait_pipeline` 的工具描述补前提与失败特征；`get_operator_box` / `get_depot` 描述补「数据来自游戏内识别，需要游戏已启动」。
3. **系统提示词**：`maa_api/agent/runtime.py` 的「工具怎么用」补通用规则，「外部资料」的 OperBox/Depot 句补就地要求（§4.4）。
4. **代码护栏（提醒不拦）**：`submit_pipeline` 工具结果在「含游戏内任务但没有 `StartUp`」时带 `warnings`；确认卡片在需要确认时附同一条原因；`wait_pipeline` 在 `CORE_COMMAND_FAILED` 失败时附 `hint`（§4.5）。
5. **文档**：spec、计划、`docs/11`、`docs/12`、`docs/13`、`docs/README.md` 同步（§6）。

### 3.2 不做

- 不拦截提交、不自动补 `StartUp`、不改变用户已确认的提交内容（用户明确选「提醒，不拦」）。
- 不改内核重试策略（`max_retries` / `retry_delay` 保持默认），不改 `resource/` 下的 MaaCore 资源。
- 不为 `CloseDown`、`Custom` 增减业务语义；不新增工具、不改 REST 契约。
- 不做真机验证（本轮无设备动作），§2.1 的失败证据已经足够。

## 4. 设计

### 4.1 判定规则（唯一口径）

放在新模块 `maa_api/agent/preconditions.py`，纯函数、无 I/O，供工具层与策略层共用：

- `GAME_RUNNING_TASK_TYPES`：需要「游戏已启动」的任务类型集合 = `TASK_MODELS` 的全部键减去 `{"StartUp", "CloseDown"}`。`StartUp` 是解法本身，`CloseDown` 直接关客户端、不需要停在某一页，都不参与判定。
- `needs_running_game(tasks)`：任一条任务的 `name` 命中上述集合即为真；`name` 大小写不敏感；无法识别的条目（缺 `name`、不是映射/模型）按**不参与**处理，不要凭猜测报警。
- `missing_start_up_warning(tasks) -> str | None`：`needs_running_game` 为真且整条流水线没有 `StartUp` 时返回固定文案，否则 `None`。文案（单一来源，测试逐字断言）：

  > 流水线里没有 StartUp：内核的任务都从主界面起手，游戏没启动或停在桌面时只会反复重试到失败。游戏状态不确定时，把 StartUp（start_game_enabled: true）放在第一条，或先让用户确认游戏已启动。

- `failure_hint(error_code) -> str | None`：`CORE_COMMAND_FAILED` 时返回：

  > 这个任务需要游戏已启动并停在主界面：先看现场截图，如果画面是设备桌面或登录页，先补一条 StartUp（start_game_enabled: true）再重跑，不要原样重试。

两条文案里不写具体重试次数与等待时长（那些口径以 `wait_pipeline` 的结果与日志为准）。

### 4.2 内置条目 `task-preconditions`

kind 取 `playbook`（操作剧本），title「任务执行前提与游戏状态」，when_to_use 指向「提交流水线前 / 任务反复失败时」。正文分五节：先看这一条（通用规则）、怎么保证前提成立、每条任务的前提（表）、失败长什么样、两处容易读错的地方。正文遵守 M30 的数值口径（不写具体数值），并把工具指引写成真实工具名。

### 4.3 既有条目的就地消歧

- `playbook-recruit.md`：把「不要塞 StartUp」改成「不要塞用户没要求的业务任务（`Fight`、`Infrast` 等）；游戏没启动时前面要加 `StartUp`（见 `task-preconditions`）」。
- `playbook-infrast-shift.md`、`playbook-sanity-planning.md`：提交示例补「游戏没启动时第一条放 `StartUp`（见 `task-preconditions`）」。
- `account-resources.md`：在已有的 `StartUp` 步骤后补一句「这一条也是所有游戏内任务的前提」。

### 4.4 系统提示词与工具描述

- `maa_api/agent/runtime.py:200`（工具怎么用）补：内核任务都从主界面起手，只有 `StartUp` 会拉起客户端；游戏没启动就提交只会重试到失败，不确定先把 `StartUp` 放第一条。
- `maa_api/agent/runtime.py:212`（外部资料）的 OperBox/Depot 句补：提交识别前先确认游戏已启动并停在主界面。
- `maa_api/agent/tools/pipeline.py` 的 `submit_pipeline` / `wait_pipeline` 描述补同样的前提与失败特征；`maa_api/agent/tools/account.py` 的 `get_operator_box` / `get_depot` 描述补「数据来自游戏内识别结果，缺失时要先跑识别（需要游戏已启动）」。

### 4.5 护栏（提醒，不拦）

1. `submit_pipeline` 工具结果：`missing_start_up_warning(tasks)` 非空时加 `"warnings": [<文案>]`；为空时**不出现该键**（保持既有结果的兼容性）。
2. 确认卡片：`maa_api/agent/policy.py` 的 `submit_pipeline` 分支在需要确认时把同一条文案追加进 `PolicyDecision.reasons`；不需要确认时策略层不加（工具结果里那一条仍然可见）。
3. `wait_pipeline` 工具结果：流水线终态失败且 `error.code == "CORE_COMMAND_FAILED"` 时加 `"hint": <failure_hint 文案>`；其它情况不出现该键。

### 4.6 全文块（内置条目原文）

```markdown
---
id: task-preconditions
kind: playbook
title: 任务执行前提与游戏状态
when_to_use: 提交流水线前、跑识别或任务反复失败时：确认游戏已启动并停在主界面
---

## 先看这一条

MaaCore 的每个任务都从**主界面**起手：它们的第一步都是同一个主界面标志的模板匹配，匹配不到就等一会儿再试。**只有 `StartUp` 会启动或唤醒客户端**，其它任务都不会自己把游戏拉起来。所以游戏没启动、或者设备还停在桌面与登录页时，提交任何游戏内任务都只会反复重试到失败。

结论：不管跑什么，先让游戏启动并停在主界面。拿不准当前画面就先 `get_screenshot` 看一眼，或者让用户确认。

## 怎么保证前提成立

- 流水线的第一条放 `{"name": "StartUp", "start_game_enabled": true}`。游戏已经在主界面时它什么都不做就结束，停在桌面时它会点图标启动，两种情况都不会有害。
- 游戏还在启动动画、登录、更新、公告里时不用管，`StartUp` 会等它进到主界面。
- 只在用户明确要求关游戏时加 `CloseDown`，它直接关掉客户端，不需要停在某一页。
- 设备没连接或没授权时任何任务都跑不了：先看设备状态，把要用户处理的事说清楚。

## 每条任务的前提

| 任务 | 提交时的前提 |
| --- | --- |
| `StartUp` | 桌面或任意界面都可以；只有它会启动／唤醒客户端 |
| `Fight`、`Recruit`、`Infrast`、`Mall`、`Award` | 游戏已启动并停在主界面 |
| `Depot`、`OperBox` | 同上；识别结果来自这一步，识别完成账号数据才会更新 |
| `Roguelike`、`Reclamation` | 游戏已启动；入口会从主界面导航进去，停在桌面一样会失败 |
| `Copilot`、自定义任务 | 游戏已启动并停在主界面，作业与自定义 JSON 也是从主界面开始操作 |
| `CloseDown` | 不需要停在某一页；用户没要求关游戏就不要加 |

## 失败长什么样

- 日志里同一个任务反复「任务失败」，最后一条是「任务多次失败已跳过」，工具返回 `CORE_COMMAND_FAILED`。
- 现场截图是设备桌面、模拟器首页或登录页——这是前提没满足最直接的证据。
- 处置：不要原样重试。先看现场截图确认画面，再补一条 `StartUp` 重新提交。重试由内核负责，重复提交同一条流水线没有意义。

## 两处容易读错的地方

- 有的条目写「tasks 里只放需要的那一个任务，不要塞无关任务」，那是指别把用户没要求的业务任务（`Fight`、`Infrast` 之类）塞进去；`StartUp` 是**前提**不是业务任务，游戏没开时必须加。
- 流水线中途失败或被中止后，画面可能停在中途页面。下一条任务前先 `back_to_home` 回到主界面：`StartUp` 的第一步同样要匹配主界面标志，在游戏内页面上补它也一样匹配不到（它只负责从桌面把客户端拉起来）。
```

## 5. 错误与兼容

- 不新增错误码、不改 REST 契约、不新增工具：条目数与工具数只增一个知识条目。
- 工具结果新增的 `warnings` / `hint` 都是**可选键**，未命中时不出现，既有消费者（前端会话渲染、冒烟脚本、测试断言）不受影响。
- 内置条目登记表新增 M32 规格：`tests/agent/test_knowledge_service.py` 的 `_spec_blocks()` 读的规格清单要加 `M32_SPEC_PATH`，`BUILTIN_IDS` 与 `BUILTIN_LIST_ORDER` 各加一条（`task-preconditions` 属 playbook，同 kind 按 id 升序排在 `playbook-sanity-planning` 之后）。
- 知识条目计数：`tests/agent/test_knowledge_no_numbers.py` 的 `EXPECTED_ENTRY_COUNT` 与 `tests/services/test_knowledge_page_pointers.py` 的条目数断言都要从 32 改到 33；`docs/11-Agent模块设计.md` 的条目数说明同步。
- 工具描述改动会被 `scripts/copy_inventory.py` 记账：改完用 `--write-baseline` 重设基线再 `--check`。

## 6. 文档同步

| 文件 | 改动 |
| --- | --- |
| `docs/superpowers/specs/2026-10-10-m32-task-preconditions.md` | 本文件 |
| `docs/superpowers/plans/2026-10-10-m32-task-preconditions.md` | 计划 |
| `docs/superpowers/ledgers/2026-10-10-m32-task-preconditions.md` | 台账（含 Rulings I made） |
| `docs/11-Agent模块设计.md` | §6 条目数与 M32 新增条目说明 |
| `docs/12-实施计划与里程碑.md` | M32 小节（依赖、可交付、验证方式、验收结论） |
| `docs/13-决策记录.md` | §8.47 M32 |
| `docs/README.md` | 里程碑表加 M32 一行 |

## 7. 验收

1. 后端全量 `pytest -o addopts= -q`：只允许既有的两条真资源用例失败（`tests/services/test_activity_index.py`）。
2. 四处生成物 `--check` 全 0：`scripts/dump_openapi.py --check`、`scripts/generate_api_guide.py --check`、`scripts/generate_task_types_fixture.py --check`、`cd web && pnpm gen:api:check`；`scripts/copy_inventory.py --write-baseline` 后 `--check` 通过。
3. 知识门禁：`tests/agent/test_knowledge_service.py`、`tests/agent/test_knowledge_no_numbers.py`、`tests/services/test_knowledge_page_pointers.py`、`tests/agent/test_knowledge_convergence.py` 全绿（新条目无「数字 + 单位」命中、页面与工具指引可达）。
4. 护栏用例：新模块单测 + 工具层与策略层用例，且**负向对照**证明改前会红（去掉 `warnings` / `hint` 后对应用例失败）。
5. 冒烟：`scripts/agent_smoke.py`、`scripts/frontend_smoke.py`、`scripts/account_smoke.py`、`scripts/prts_smoke.py --m28`、`scripts/copilot_research_smoke.py` 末行 `SMOKE OK`。
6. 前端不新增文件时跑 `pnpm typecheck` 与 `pnpm test --run`（工具描述与后端改动不影响前端，若无改动可只跑 typecheck）。
