# M17 · 任务参数默认值（schema 声明 + 前端预填 + 显式下发）

> 关联：[05-API规范与路由清单](./../05-API规范与路由清单.md) §7/§8/§9、[09-前端重构方案](./../09-前端重构方案.md) §7.3、[04-数据模型与持久化](./../04-数据模型与持久化.md) §5.2、[13-决策记录](./../13-决策记录.md) §8。

## 1. 目标与用户决策

起因：任务编辑器里 `启用本任务` 显示为「默认 + 滑块居中」，用户无法判断这是「空」还是「关」。用户裁决（2026-09-28）：

1. **不要**这种三态显示：**所有任务参数，有默认值的都按默认值显示并预填**；
2. **预填即下发**：预填进表单的值随提交体显式下发（所见即所提交），接受「MAA 将来改默认值我们不再自动跟随」这一代价；
3. 默认值来源：**后端逐字段补真实值，走标准 JSON Schema `default`**；
4. 范围：**只做任务参数**（9 种任务类型 + Copilot），不做「日常预设模板」；
5. 渠道字段（`client_type` / `server`）**也预填当前全局默认并下发**（牺牲「新建流水线自动跟随后续渠道设置变更」）；
6. 内核必填但无默认值的参数（`Recruit.select` / `confirm`、`Infrast.facility`）**由本服务给默认值并预填**；
7. `facility` 等枚举在界面上**显示中文**，不显示英文原名。

## 2. 事实基础

默认值不是猜的，三条独立证据链：

| 证据 | 内容 |
|---|---|
| 内核源码 | MAA tag `v6.17.5`（本仓捆绑 `MAA-v6.17.5-macos-runtime-universal.zip`）的 `src/MaaCore/Task/Interface/*.cpp`、`src/MaaCore/Assistant.cpp` |
| 官方文档 | 同 tag 的 `docs/zh-cn/protocol/integration.md`（`::: field` + `@default` 结构化注解），并用同 tag 英文版交叉验证 |
| 真内核实测 | 本仓 `scripts/probe_task_defaults.py`：`AsstAppendTask` 不连设备即可调用，返回 0 即 `set_params` 失败（docs/ENVIRONMENT.md「MaaCore 内核」节） |

真内核实测结果（v6.17.5，2026-09-28）：

```
拒绝 StartUp   {}                 → 0     OK  StartUp   {"client_type":"Official"} → 1
OK  CloseDown  {}                 → 3     OK  Fight     {}                          → 5
拒绝 Recruit   {}                 → 0     拒绝 Recruit  {"times":4}                → 0
拒绝 Recruit   {"select":[1,4,5]}  → 0     OK  Recruit   {"select":…,"confirm":…}    → 8
拒绝 Infrast   {}                 → 0     OK  Infrast   {"facility":["Mfg"]}        → 10
OK  Mall/Award/Roguelike/Reclamation {} → 非 0
拒绝 Copilot   {} / 不存在的 filename → 0
```

**推论（本次要修的既有缺陷）**：

- `Recruit` 必须同时给 `select` 与 `confirm`、`Infrast` 必须给 `facility`，缺任何一个内核都返回 0 → 今天用户「什么都不改」提交公招/基建任务会拿到一个看不懂的 `502 CORE_COMMAND_FAILED`。本里程碑给这三个参数补默认值（见 §3.3）。
- `StartUp` 必须给 `client_type`（源码 `Config.get_package_name("")` 失败；本仓 `scripts/core_smoke.py` 早有同样记录）——服务端已注入全局渠道默认值，本次由前端预填下发。
- `StartUp.start_game_enabled` 的内核默认值是 **false**（`StartUpTask.cpp:94` `params.get("start_game_enabled", false)`），我们原有描述里的「默认 True」是错的，必须改。

## 3. 默认值规则

### 3.1 写入 schema `default` 的条件（三条全满足）

1. **内核缺省值存在**（键缺省时内核实际采用的值），来源见 §2；
2. **能用 JSON 字面量表达**（`2147483647` 这种「无限」哨兵不算）；
3. **任何上下文下都可以显式下发**：不被本服务的 `model_validator` 按主题/模式拒绝，也不会让内核 `set_params` 失败。

### 3.2 不写默认值的四类（保持 `None`）

| 类别 | 字段 | 原因 |
|---|---|---|
| 语义是「无限」 | `Fight.times`、`Roguelike.starts_count`、`Roguelike.investments_count` | 内核默认 `INT_MAX`，写进表单是 `2147483647`；`Fight.times=0` 另有「跳过整个任务」的含义，也不能用 |
| 语义是「空 = 不启用」 | `Fight.stage`、`StartUp.account_name`、`Fight.penguin_id`、`Recruit.penguin_id`、`Recruit.yituliu_id`、`Roguelike.squad`、`Roguelike.roles`、`Roguelike.core_char`、`Roguelike.first_floor_foldartal`、`Infrast.filename` | 内核默认空串，预填与不填在界面/语义上无差别，显式下发只是噪声；说明文案里写清「留空 = 内核自动」 |
| **条件默认值**（随 mode/theme 变） | `Roguelike.use_foldartal`（mode 5 → false，其他 → true）、`Roguelike.check_collapsal_paradigms`、`Roguelike.double_check_collapsal_paradigms`（mode 5 → true，其他 → false）、`Roguelike.difficulty`（Phantom 主题不允许非空）、`Roguelike.expected_collapsal_paradigms`（仅 Sami + mode 5 允许非空） | 静态 `default` 会在别的模式/主题下**改掉内核行为**；`expected_collapsal_paradigms` 更会直接触发本服务的 422 |
| 本服务校验器禁止「设了但不生效」 | `Recruit.expedite_times`（仅 `expedite=true` 时允许非空，且内核解析后丢弃） | 静态默认必然违反 `RecruitInput._check_expedite_times` |

另：`Roguelike.start_foldartal_list` 内核默认「键缺省 = 插件禁用」，但**显式传 `[]` 会让 Sami 主题的 `set_params` 返回 false**（`RoguelikeFoldartalStartTaskPlugin.cpp`），所以既不设默认也不能下发空数组。

`Roguelike.squad` / `roles` 官方文档写 `@default 指挥分队` / `取长补短`，**源码是空串**（`RoguelikeCustomStartTaskPlugin.cpp:74/80`，空 = 内核自动选）。以源码为准，不设默认值，并把描述改成「留空 = 内核自动选择」。

### 3.3 本服务自定的默认值（内核无默认值，标注来源）

| 字段 | 默认值 | 依据 |
|---|---|---|
| `Recruit.select` | `[1, 4, 5]` | 内核必填；取值沿用旧前端 `createTasks` 的日常预设（[09 §15.3](./../09-前端重构方案.md)） |
| `Recruit.confirm` | `[2, 3, 4, 5]` | 同上 |
| `Infrast.facility` | `["Mfg","Trade","Power","Control","Reception","Office","Dorm"]` | 内核必填；「全部设施按标准顺序换班」是中性默认（旧前端 `createTasks` 与 `resource/daily_task_template.json` 同值） |

这三个字段的 `default` 是**本服务的约定**，不是内核默认值，必须在 `description` 里写明。

### 3.4 渠道字段：动态默认值 overlay

`client_type`（StartUp / CloseDown / Fight）与 `server`（Fight / Recruit）在模型里**保持 `default=None`**：服务端按 `model_fields_set` 注入设置表里的全局渠道默认值（[05 §9](./../05-API规范与路由清单.md)），这条行为不变。前端要预填的是**当前生效值**，因此：

- `GET /api/tasks/types` 与 `GET /api/tasks/types/{type_name}` 在返回的 schema 里，给这两个字段注入**当前生效**的 `default`（`load_channel_defaults` → `ChannelDefaults`，默认 `Bilibili` / `CN`）；
- OpenAPI 快照不受影响（overlay 只作用于这两个响应），pydantic 模型与 `POST /api/pipelines` / `/validate` 的默认语义不变；
- 前端提交时把它当普通默认值预填并显式下发，于是**新建的流水线/定时任务会冻结当时的渠道值**，不再跟随后续设置变更（用户已确认；已有流水线不受影响）；
- 设置保存后前端要失效任务类型目录查询（`taskTypesKey`），否则 5 分钟 `staleTime` 内会预填旧值。

## 4. 逐字段默认值表（附录 · 实现时逐行照做）

`overlay` = 见 §3.4；`skip` = 见 §3.2；`★` = §3.3 的本服务约定值。

| 任务类型 | 字段 | schema `default` | 内核默认值（证据） |
|---|---|---|---|
| 全部（`TaskInputBase`） | `enable` | `true` | `true`（`Assistant.cpp:352`） |
| StartUp | `client_type` | overlay | `""`（append 返回 0，必填） |
| StartUp | `start_game_enabled` | `false` | `false`（`StartUpTask.cpp:94`）**原文案写 True，要改** |
| StartUp | `account_name` | skip | `""` |
| CloseDown | `client_type` | overlay | `""`（运行期不关游戏） |
| Fight | `stage` | skip | `""`（打当前/上次关卡） |
| Fight | `times` | skip | `INT_MAX` |
| Fight | `series` | `1` | `1`（`FightTask.cpp:91`） |
| Fight | `medicine` / `expiring_medicine` / `stone` | `0` | `0` |
| Fight | `dr_grandet` | `false` | `false`（`FightTask.cpp:111`，键名 `DrGrandet`） |
| Fight | `server` / `client_type` | overlay | `"CN"` / `""` |
| Fight | `report_to_penguin` | `false` | `false` |
| Fight | `penguin_id` | skip | `""` |
| Fight | `drops` | `{}` | `{}`（不指定掉落目标） |
| Recruit | `refresh` | `false` | `false` |
| Recruit | `select` / `confirm` | `[1,4,5]` / `[2,3,4,5]` ★ | 无默认值，内核必填 |
| Recruit | `first_tags` | `[]` | `[]` |
| Recruit | `extra_tags_mode` | `0` | `0` |
| Recruit | `times` | `0` | `0`（0 = 主循环不执行，即只进公招页） |
| Recruit | `set_time` | `true` | `true`（原文案「仅 times=0 时生效」与源码不符） |
| Recruit | `expedite` | `false` | `false` |
| Recruit | `expedite_times` | skip | `0`（解析后丢弃；本服务校验器禁止单独设置） |
| Recruit | `skip_robot` | `false` | 键缺省等效 `false`（源码是 `find`；文档 `@default true` 与源码冲突，以源码为准） |
| Recruit | `recruitment_time` | `{"3":540,"4":540,"5":540,"6":540}` | 同（`RecruitTask.cpp:70-73`） |
| Recruit | `report_to_penguin` / `report_to_yituliu` | `false` | `false` |
| Recruit | `penguin_id` / `yituliu_id` | skip | `""` |
| Recruit | `server` | overlay | `"CN"` |
| Infrast | `mode` | `0` | `0` |
| Infrast | `facility` | 七个设施 ★ | 无默认值，内核必填 |
| Infrast | `drones` | `"_NotUse"` | `"_NotUse"`（mode=10000 时内核忽略，下发无害） |
| Infrast | `threshold` | `0.3` | `0.3`（原文案已写 0.3） |
| Infrast | `replenish` / `dorm_notstationed_enabled` / `dorm_trust_enabled` | `false` | `false` |
| Infrast | `filename` | skip | 仅 `mode=10000` 必填 |
| Infrast | `plan_index` | `0` | `0`（仅 `mode=10000` 生效） |
| Mall | `shopping` | `true` | `true` |
| Mall | `buy_first` / `blacklist` | `[]` | `[]`（等价） |
| Mall | `force_shopping_if_credit_full` / `only_buy_discount` / `reserve_max_credit` | `false` | `false` |
| Award | `award` | `true` | `true` |
| Award | `mail` / `recruit` / `orundum` / `mining` / `specialaccess` | `false` | `false` |
| Roguelike | `theme` | `"Phantom"` | `"Phantom"` |
| Roguelike | `mode` | `0` | `0` |
| Roguelike | `squad` / `roles` / `core_char` | skip | `""`（文档写「指挥分队/取长补短」，以源码为准并改文案） |
| Roguelike | `use_support` / `use_nonfriend_support` | `false` | `false` |
| Roguelike | `starts_count` | skip | `INT_MAX` |
| Roguelike | `difficulty` | skip | `-1`（Phantom 主题下不允许非空） |
| Roguelike | `start_with_elite_two` / `only_start_with_elite_two` | `false` | `false` |
| Roguelike | `first_floor_foldartal` | skip | `""`（Sami 专用，非 Sami 传非 null 会被本服务拒绝） |
| Roguelike | `start_foldartal_list` | skip | 键缺省 = 插件禁用；显式 `[]` 会让 Sami 任务 append 失败 |
| Roguelike | `use_foldartal` | skip | 条件默认（mode 5 → false，其他 → true） |
| Roguelike | `stop_at_final_boss` | `false` | `false` |
| Roguelike | `investment_enabled` | `true` | `true` |
| Roguelike | `investments_count` | skip | `INT_MAX` |
| Roguelike | `stop_when_investment_full` | `false` | `false` |
| Roguelike | `refresh_trader_with_dice` | `false` | `false` |
| Roguelike | `check_collapsal_paradigms` / `double_check_collapsal_paradigms` | skip | 条件默认（mode 5 → true） |
| Roguelike | `expected_collapsal_paradigms` | skip | 内核默认 4 项，但仅在 Sami + mode 5 允许非空 |
| Reclamation | `theme` | `"Tales"` | `"Tales"`（Fire 已下线） |
| Reclamation | `mode` | `0` | `0` |
| Reclamation | `tools_to_craft` | `[]` | `[]`；内核源码对该键未判空，显式下发比缺省安全 |
| Reclamation | `increment_mode` | `0` | `0` |
| Reclamation | `num_craft_batches` | `16` | `16` |
| Copilot | `asset_id` | 不设（必填字段） | 非内核参数，由服务层映射为 `filename` |
| Copilot | `formation` | `false` | `false`（已有） |

## 5. 后端改动

1. `maa_api/domain/task.py`：按 §4 给字段补 `default=`；`enable` 在 `TaskInputBase` 上改一次；`facility` 补 `x-enum-labels`（中文：制造站 / 贸易站 / 发电站 / 控制中枢 / 会客室 / 办公室 / 宿舍）。
2. **文案修正**（调研发现的错误，同一 PR 内改）：`start_game_enabled` 的 True→False；`Recruit.skip_robot` 的 True→源码的假；`Recruit.set_time` 的生效条件；`Recruit.confirm` 的「仅公招计算」= `confirm` 含 `-1`；`Recruit.expedite_times` 标注 v6.17.5 已无效；`Fight.expiring_medicine` 标注已弃用（内核会用 `medicine_expire_days`）；`Roguelike.squad/roles/core_char` 改「留空 = 内核自动选择」。凡「不传则由内核使用默认值（X）」在已写 `default` 的字段上一律改成「内核默认值 X」。
3. `to_core_params()` 保持 `exclude_none=True`：因为默认值不再是 `None`，**默认值现在会随下发快照显式下发**（这正是「预填即下发」的服务端对应物）；`raw_params` 仍用 `exclude_unset=True`，只记客户端显式提交的字段。docs/04 §5.2 与 docs/05 §9 同步说明。
4. 渠道默认值 overlay：`GET /api/tasks/types`、`/api/tasks/types/{type_name}` 的响应里给 `client_type` / `server` 注入当前生效 `default`（复用 `load_channel_defaults`）。`POST /validate` 与提交流程的注入语义不变。
5. 新增测试：
   - **全默认可实例化**：9 种任务类型各自「只给 name + 全默认」构造 + `normalize` 必须通过（能挡住「默认值违反 model_validator」这类错误）；
   - 逐字段断言 §4 的表（防止以后被无意改掉），skip 的字段断言 `default is None`；
   - `/api/tasks/types` 的 overlay：改设置后两个字段的 `default` 跟着变（默认 settings 下是 `Bilibili` / `CN`）；
   - `facility` 的中文标签存在。

## 6. 前端改动

1. **物化默认值进草稿**（`features/tasks/composer/drafts.ts`）：`createDraft` / `duplicateDraft` / `draftsFromTemplate` 都过一遍 `materializeDefaults(taskType, params)`（按 schema 递归填「有 default 且未设置」的字段）。定时任务编辑器（`features/schedules/ScheduleEditor.tsx`）复用同一条路径。
2. **状态与标签**（`form/values.ts` + `editor/FieldEditor.tsx`）：`fieldState(values, name, schema)` 返回
   - `default`：值**深等于** schema `default` → 标签「默认」；
   - `unset`：字段无默认值且未设置 → 标签「未设置」（原来错写成「默认」）；
   - `modified`：与默认值不同 → 「已修改」+「恢复默认」；
   - `null`：显式留空 → 「已设为空」；
   - 必填且未设置 → 「必填」。
3. **「恢复默认」写回默认值**（有默认值时不再删键）；分组徽标「已修改 N 项」按 `modified`/`null` 计数。
4. **控件预填**（`editor/ScalarControls.tsx` 等）：开关按默认值显示开/关（滑块居中只剩「无默认值」的字段）；数字/文本/枚举/分段控件的值就是草稿里的默认值；清空输入 = 恢复默认值。`SelectControl` 的「使用默认」项只在字段无默认值时保留（文案改「未设置」）。
5. **任务行摘要**（`form/summary.ts`）：只列与默认值不同的字段，全默认时显示「使用默认参数」（今天会把 `medicine: 0` 这类显式默认值也列出来）。
6. **提交体**：`serializeTask` 不变（草稿本身就是提交体），但提交前再跑一次 `materializeDefaults` 兜底（例如草稿来自旧缓存）。
7. 设置保存成功后失效 `taskTypesKey`（渠道默认值 overlay 的缓存）。
8. 前端测试：物化（新建/加载/复制）、三种标签、摘要、开关/数字/枚举预填、`facility` 中文标签、提交体包含默认值。

## 7. 验证

| 检查 | 命令 |
|---|---|
| 后端测试 | `.venv/bin/python -m pytest -q` |
| OpenAPI 快照 | `.venv/bin/python scripts/dump_openapi.py --check`（先不带 `--check` 重新生成） |
| 前端类型与 schema 同步 | `cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` |
| 前端集成冒烟 | `.venv/bin/python scripts/frontend_smoke.py` |
| 真内核接受全默认参数（补充证据） | `DYLD_LIBRARY_PATH=$PWD/resource/lib/maa/Darwin .venv/bin/python scripts/probe_task_defaults.py --defaults <导出的全默认 JSON>`，要求每个任务类型 `task_id != 0` |

真内核探针是**补充检查**（需要本机内核，不进 CI 门禁），但本次必须跑：它是「预填即下发」不会把内核打回 0 的唯一直接证据。

## 8. 已知偏离与代价（实施后回填到 docs/13 §8）

1. 预填即下发 → 参数快照冻结当时的默认值，MAA 后续调整默认值不再自动跟随（用户已确认的取舍）。
2. 渠道字段预填下发 → 新建的流水线/定时任务不再跟随后续渠道设置变更；已有（未显式带 `client_type` 的）流水线不受影响。
3. `Recruit.times` 默认 0（内核默认）意味着「什么都不改」的公招任务进去就出来；要真招募得自己改次数。日常预设模板（本次不做）才是「一整套能跑的值」。
4. `Roguelike` 的条件默认值字段保持三态显示（滑块居中），因为静态默认会在别的模式/主题下改掉内核行为。
5. 三个本服务自定默认值（`select` / `confirm` / `facility`）与内核默认值不是一回事，文档与 `description` 都已注明。
