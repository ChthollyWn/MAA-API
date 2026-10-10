# M35 · 练度未知不冒充事实（未识别 ≠ 0/1）

## 1. 目标与用户决策

**需求来源**：User said (m04185)「提交推送，然后处理当前干员识别任务，无法识别到干员具体练度的问题」。
截图（m04184）是 Agent 对「查我的干员」的回答：克莱门莎「精二 90 级，潜能 1，技能等级 1，无专精、无模组」，
并自述「技能等级与专精是本次识别读不到的部分（OperBox 不识别），所以技能那一栏只能按识别的 1 级看」。
**识别端拿不到的东西，被表达成了具体值**——这就是本里程碑要修的问题。

**用户裁决（ask_user_question，2026-10-11）**：

- ① 范围＝**全部练度字段一起**：`elite` / `level` / `potential` / `skill_level` / `masteries` / `modules`
  六列都可为「未识别」；顺带修掉 M33 台账遗留的「3 名已拥有但没有详情的干员落 0/0/0」。
- ② 养成计划遇到未知＝**标为前置未知，不虚报增量**：不把未知当 1 级 / 无专精去算材料。

## 2. 事实基础

### 2.1 内核识别边界（不可协商）

`resource/maa-api/debug/asst.log` 里最后一条 `OperBoxInfo`：

- `details` 键＝`['all_opers','done','own_opers']`；`all_opers` 463 条字段只有
  `id / name / name_en / name_jp / name_kr / name_tw / own / rarity`；
- `own_opers` 381 条＝`elite / id / level / name / own / potential / rarity`
  （`{"elite":0,"id":"char_4093_frston","level":1,"name":"Friston-3","own":true,"potential":1,"rarity":1}`）。

→ 识别只能给出 `own`（`all_opers`）与 `elite / level / potential`（`own_opers`）；
**技能等级、专精、模组不在内核识别结果里**，MAA 也没有对应任务（已核查内核 `resource/tasks/tasks.json`
1090 条任务，没有任何「读干员详情页练度」的条目）。补齐它们要走自建 `Custom` 任务（M36，另行立项）。

### 2.2 「假值」的四处来源

| # | 位置 | 现状 |
|---|---|---|
| 1 | `maa_api/db/models.py:1019-1029` | 六列非空 + 默认值：`elite=0`、`level=0`、`potential=0`、`skill_level=1`、`masteries={"1":0,"2":0,"3":0}`、`modules={}` |
| 2 | `maa_api/services/account/rules.py:259-280`（`box_row_wire`） | 读侧再次兜底：`int(getattr(row,"elite",0) or 0)` … `int(getattr(row,"skill_level",1) or 1)`，`mastery_wire` / `module_wire` 总补全 |
| 3 | `maa_api/services/account/planner.py:124-131`（`FRESH_OPERATOR`）、`:500-537`（`_normalize_current` / `_fresh_current`） | 规划层把「没识别过」当成「精零 1 级、无专精、无模组」算材料增量 |
| 4 | `maa_api/services/account/box.py:214-222`（`patch_operator` 的 `before` 投影） | 编辑路径也按默认值取现状，再参与跨字段约束 |

`field_sources` 侧是诚实的：`RECOGNIZED_FIELDS: tuple[str, ...] = ("own","elite","level","potential")`
（`rules.py:68`）说明识别只写这四个，技能/专精/模组从来没被标成 `recognition`——**错的只是值**。

### 2.3 已知的连带问题

- M33 台账遗留：`own_opers` 里没有详情的干员（实测 3 名）走 `capture.py:566-584` 的
  「只写 `own`」分支，其余三列落成默认 0/0/0，与「精零 1 级」不可区分。
- 页面与 Agent 都会照 `box_row_wire` 输出渲染，所以假值同时污染 UI 与对话。

## 3. 范围

### 3.1 本里程碑

1. 六列可空化 + Alembic 迁移（含把历史默认值回填成 NULL）。
2. wire / API / Agent / 前端 / planner 的「未识别」表达。
3. 编辑与写入路径在未知现状下的约束语义。
4. 文档同步与全套验收（含负向对照）。

### 3.2 不做

- 不做 M36 的练度扫描任务（自建 `Custom` 任务读技能/专精/模组）；本轮只保证「扫到之前不撒谎」。
- 不新增「把字段显式写回未识别」的 API 语义：`PATCH` 里 `null` 与缺省同义（＝不改），清空语义留到有真实需求时再定。
- 不改 `own` 的非空语义（`all_opers` 每次都给 `own`，它没有「未知」态）。
- 不动 `field_sources` / `recognized_values` / Diff 的既有语义。

## 4. 设计

### 4.1 语义

**NULL ＝ 未识别**：既没有识别值，也没有 manual / agent 写入。`field_sources` 仍是唯一来源台账
（`recognition` / `manual` / `agent` 有记录 ⇔ 有值）。读到 `null` 的任何一层都不许再折算成 0 / 1 / 空映射。

### 4.2 数据模型与迁移（`0018_unknown_training`）

- `account_operator.elite / level / potential / skill_level` → `INTEGER NULL`；
  `masteries / modules` → `JSON NULL`；`empty_masteries()` 不再作为列默认（`default=None`）。
- **回填**（逐行、按 `field_sources` 与 `recognized_values` 判定）：

  | 字段 | 保留值 | 置 NULL |
  |---|---|---|
  | `elite` / `level` / `potential` | `field_sources[field] ∈ {manual, agent}`，或 `recognized_values` 含该键 | 其余 |
  | `skill_level` / `masteries` / `modules` | `field_sources[field] ∈ {manual, agent}` | 其余（识别永不写这三列） |

  真机库（384 行、recognition 来源）预期：`elite/level/potential` 保留 381 行、3 行转 NULL；
  技能/专精/模组三列全部转 NULL（除非用户手动改过）。
- SQLite 不支持 `ALTER COLUMN`，用 `op.batch_alter_table` 重建表（保持主键、索引与既有列顺序）。
- downgrade：六列恢复非空并把 NULL 填回默认值（`0/0/0/1/全 0/{}`）——信息丢失是迁移方向固有的，写进迁移注释。

### 4.3 wire（`rules.py`）

- `box_row_wire`：六字段原样输出 `None`，不再兜底；`own` 仍是 `bool`。
- `mastery_wire(None) -> None`、`module_wire(None) -> None`；有值时仍是三槽 / 类型码形状。
- 新增常量 `UNKNOWN_TRAINING_FIELDS: tuple[str, ...] = ("elite","level","potential","skill_level","masteries","modules")`，
  给 API 文档、前端与 planner 共用，避免各处手抄字段名。
- `json_mapping(None)` 仍回 `{}`（它是读 JSON 列的唯一入口，写路径要用）；但**wire 层不再调它兜底**。

### 4.4 读路径

- `list_operators` 的 `elite_min` 等筛选：`NULL` 行不命中（SQL 三值逻辑天然如此），与「未知不是满足条件」一致，文档写明。
- `get_operator` / 列表项：返回 `null`，页面显示「未识别」。

### 4.5 写路径（`box.py::patch_operator`）

- `before` / `final` 用 `None` 表示未知，不再取默认值；`None → 值` 算一次变化（写库、标来源）。
- 跨字段约束**只在已知时判**（「不知道」不是「违反」）：
  - 专精：`final["elite"] is not None and < 2` → 拒；`final["elite"] is None` → 放行；
    `final["skill_level"] is not None and != 7` → 拒；`None` → 放行。
  - 模组：`final["elite"] is not None and < 2` → 拒；未知 → 放行。
  - 显式降精英化（本次给了 `elite < 2`）仍然清空 `masteries` / `modules`（保持 M27 行为）。
- 单字段取值域不变（`require_int` 的上下限照旧）；`masteries` / `modules` 的归一化照旧（写入值总是具体值）。

### 4.6 识别路径（`box.py::apply_recognition` / `capture.py`）

- 只有 `all_opers` 条目、没有详情的行：`elite/level/potential` 保持 `NULL`（不再落 0/0/0）。
- 有详情：写具体三值；`skill_level/masteries/modules` 一律不写（未知保持未知）。
- `recognized_values` 仍只记 `RECOGNIZED_FIELDS`；收缩保护（M33 裁决 A）、Diff、`recognized_at` 不变。

### 4.7 养成计划（`planner.py` / `plans.py`）

- `_normalize_current` / `_fresh_current`：未知字段回 `None`（`elite`/`level`/`skill_level`/`masteries`/`modules`），
  不再 clamp 成 1 / 0 / 空映射。
- `_target_increments`：目标需要的维度只要现状未知，就**不产生该维度的材料增量**，并收集一条
  `{"operator": <名字>, "field": <字段>, "reason": "未识别，需先确认"}`。
- 结果输出新增 `unknowns`（数组）并在 `notes` 里加一句人话（例如
  「克莱门莎的技能等级未识别，未计入材料增量；跑一次干员识别或用 Agent/页面补录后再算」）。
  已知维度的增量照算（精英化/等级这类有识别值的部分不受影响）。
- `start_upgrade_plan` 保存的目标与进度展示沿用同一份 `unknowns`（计划详情里能看到「待确认」项）。

### 4.8 API（`maa_api/api/routers/account.py`）

- `AccountOperatorRow` 六字段改 `| None`；`OperatorPatchRequest` 语义写清「`null` ＝ 不改」。
- 重生成 `web/openapi.json`、`web/src/types/api.d.ts`、`docs/05` 的接口清单（`dump_openapi.py --write` 等）。
- `docs/06`/`docs/05` 里凡写「默认 0/1」的段落同步改口径。

### 4.9 前端（`web/src/features/account/`）

- `OperatorsTab.tsx`：单字段显示「未识别」（弱化样式），不再显示 0 级 / 技能 1；
- `format.ts`：概要与列表摘要里未知字段写「技能未识别」这类词，不写具体值；
- `constraints.ts` / 编辑抽屉：留空 = 未识别（提交时不带该字段），校验与后端同口径（未知不拦）；
- 筛选：`最低精英化` 不命中未识别行（与后端一致），筛选说明文案补一句。

### 4.10 Agent

- `get_operator_box` 工具描述与知识库补口径：OperBox 不提供技能等级/专精/模组；`null` ＝ 未识别，
  不要当成 1 级 / 无专精；需要这些数据时建议用户跑识别（拿到精英化/等级/潜能）或由 Agent 写入。
- 回答「练度够不够」时不把未知当事实，也不再把「未识别」表述成「1 级」。

## 5. 兼容与风险

- 旧数据迁移后出现大量 `NULL`，是目标状态；Diff 待处理列表与 `recognized_at` 不受影响。
- 前端若仍按数字渲染 `null`，会出现 `null` 字样——必须由 4.9 一起改完，验收里做负向对照。
- 规划层的「未知不虚报」会让部分计划的材料数变小（原先把未知当 1 级/无专精，增量被高估）；
  验收用例要覆盖「既有测试期望值是否需要更新」，并在台账里说明。
- 回滚：`alembic downgrade` 会把 NULL 写回默认值（值语义不可逆），仅用于本地回退。

## 6. 文档同步

- `docs/04-数据模型与持久化.md`：`account_operator` 列定义与「NULL ＝ 未识别」口径；
- `docs/05-API规范与路由清单.md`：账号单条/列表字段可空、PATCH 语义；
- `docs/13-决策记录.md` §8.50：本轮决策（六列可空、未知不虚报、写路径约束只在已知时判）；
- `docs/12-实施计划与里程碑.md`：M35 段与验收结论；
- 本规格、`docs/superpowers/plans/2026-10-11-m35-unknown-training.md`、`.superpowers/sdd/2026-10-11-m35-unknown-training/progress.md`（完成后抄进 `docs/superpowers/ledgers/`）。

## 7. 验收

AGENTS.md 全套门禁（见计划「验收」节）＋ 本里程碑专项：

1. 迁移在真机库（384 行）上跑通：`elite/level/potential` 保留 381 行、NULL 3 行，
   技能/专精/模组全 NULL（若用户手动改过则保留）。
2. `GET /api/account/operators` 返回 `skill_level: null`、`masteries: null`、`modules: null`，
   页面与 Agent 都不再显示「技能 1 / 无专精 / 无模组」。
3. 规划接口对「技能等级未识别」的目标返回 `unknowns` 与非空 note，材料增量不包含未知维度的猜测值。
4. 负向对照：把 `box_row_wire` 的 `None` 兜底改回 `1` / `{}`，对应护栏用例必须失败。
5. 写路径：未知现状下写入专精不再因「未知精英化」被拒；已知 `elite=0` 时仍拒。
