# M47 干员数据只来自森空岛（规格）

> 分支 `m47-skland-only-operators`，基点 `refactor/v2` = `716deb8`（2026-10-14）。
> 本文是 M47 的唯一规格；实施后的偏离记在 [13 §8.70](../../13-决策记录.md)。

## 1. 背景

账号干员数据（`account_operator`）从 M27 起有三个写入者：

| 写入者 | 写什么 | 来源标记 |
|---|---|---|
| 内核 `OperBox` 识别（任务「干员识别」） | `own` / `elite` / `level` / `potential` | `recognition` |
| 森空岛同步（M42，`SyncSklandTraining`） | `own` + 六列练度 + `favor_percent` + `skill_ids` / `module_details` | `skland` |
| 手动修正 / Agent 补录（M27/M43） | 任意练度列 | `manual` / `agent` |

M35 把练度六列改成可空（`NULL` ＝ 未识别），M42 把练度来源整体换成森空岛，M43 换成卡片网格——但 **`own` 与练度的默认路径仍要跑一次「干员识别」**，页面顶部也还挂着「干员识别」状态条与「哪些识别覆盖了手改」的差异条。用户 2026-10-14 的三条裁决把这条路径整体去掉：

- **D1**：后端（任务类型、采集回调、写库）也一起删掉 `OperBox`，不是只从页面拿掉入口。
- **D2**：手动修正与 Agent 补录也去掉，干员页变成纯只读。
- **D3**：已有库里由 `OperBox` 写入的行清掉，等森空岛重新同步。

## 2. 目标与非目标

**目标**

1. 账号数据页的干员标签页只剩一个数据来源：**森空岛练度同步**；状态卡口径改成「上次同步时间 / 是否过期 / 已同步 N 名（精二 M）」，未绑定时直接引导绑定。
2. 删除内核 `OperBox` 任务类型与其整条链路（任务注册、采集回调、写库与两道护栏、提示词与文档口径）。
3. 删除手动修正与 Agent 补录：编辑抽屉、`PATCH /api/account/operators/{char_id}`、`update_operator_box` 工具、差异条。
4. 迁移清空历史 `account_operator` 行（`0022_skland_only_operators`），未同步前页面为空并引导绑定。

**非目标（本轮不动）**

- 仓库识别（`Depot`）与仓库页签一行不动。
- 养成计划 / 养成规划（planner / plans）的算法与数据链不动——只是输入数据的来源变成森空岛。
- **不删表**：`account_operator_diff` 的历史行保留（不再产生新行），`account_recognition` 的 `operbox` 记录保留为审计历史。
- **不删只读端点** `GET /api/account/operators` 与 `GET /api/account/operators/{char_id}`（外部 API 读取方仍需）。
- 不动 MaaCore 侧：内核自己还有 `OperBox` 任务，只是服务端不再暴露它。

## 3. 决策

| # | 决策 | 依据 |
|---|---|---|
| D1 | `OperBox` 任务类型从 `domain/task.py` 的注册表与联合类型里整块删除，`install_account_capture` 只留 depot | 用户裁决；「干员数据只能来源于森空岛同步」 |
| D2 | 手动 / Agent 写入通道删除：`patch_operator`、PATCH 路由、`update_operator_box`、编辑抽屉 | 用户裁决 |
| D3 | 迁移 `0022` 清空 `account_operator`（`DELETE`），**不可逆**：`downgrade` 只写说明、不重建数据 | 用户裁决；权威来源是森空岛，重同步即可重建 |
| D4 | `agent.box_stale_days` **键保留**（不动设置契约与 config 兼容），标签与说明改成「干员数据过期天数（距上次森空岛同步）」 | 少动一层；`summary()` 的 `recognized_at` 继续取 `max(account_operator.recognized_at)`，森空岛同步会写它 |
| D5 | 差异条（`operator-diffs` 读写端点 + 页面通知条 + Agent 注入的 `pending_diffs`）整块下线：手改来源没了，差异不可能再产生 | D2 的直接后果 |
| D6 | `field_sources` 仍照写（审计与排障），但卡片上的逐字段来源徽章收敛成单枚「森空岛」 | 只剩一个来源，逐字段徽章是噪声 |
| D7 | Agent 侧：删 `update_operator_box`（CONDITIONAL），`get_operator_box` / `plan_upgrade` 保留只读；提示词与知识库一律改成「先看森空岛绑定状态 → `sync_skland_training`」 | D1/D2 |
| D8 | 定时任务或外部客户端里已保存的 `OperBox` 载荷会校验失败（未知任务类型），**不写兼容层**，只在 §8 记破坏性变更 | 用户要求「去除」，留兼容等于没去 |

## 4. 设计

### 4.1 后端

- `domain/task.py`：删 `OperBoxInput`、任务联合成员、`TASK_NAMES` / 分类 / 默认参数登记；OpenAPI 里 `OperBoxInput` 随之消失（生成物重出）。
- `services/account/capture.py`：删 `parse_operbox` / `OperBoxSnapshot` / `OPERBOX_WHAT` 与分发表里的 operbox 分支；`DepotInfo` 路径原样保留（含整表替换、绝对下限与下降护栏）。
- `services/account/callback_wiring.py` / `main.py`：只装 depot 采集。
- `services/account/box.py`：删 `apply_recognition` / `patch_operator` / `list_diffs` / `dismiss_diffs` / `_operbox_count` 与 `WRITE_SOURCES`；留 `list_operators` / `get_operator` / `summary` / `apply_skland_sync` / `record_skland_sync`。
- `api/routers/account.py`：删 PATCH 与 `operator-diffs` 两个端点及其模型；`GET` 系列与 `assets` 不动。
- 设置：`agent.box_stale_days` 仅改标签与说明（`settings_schema.py`、`config.template.yaml` 注释）。
- 只读门面 `services/account/summary.py` 去掉 `pending_diffs`。

### 4.2 迁移

`0022_skland_only_operators`（`down_revision = "0021_depot_sources"`）：`DELETE FROM account_operator`。`downgrade` 为显式 no-op（数据已不可恢复，重建靠森空岛同步）。

### 4.3 Agent

- 删 `update_operator_box` 与其 `ACCOUNT_WRITE_TOOLS` / 确认卡片分支；工具总数与「对模型可见」数各减一（按实施时的钉子测试校正）。
- 系统提示词、`knowledge/__init__.py` 的账号注入段、`builtin/*.md`（`playbook-promotion-planning.md`、`task-preconditions.md` 等）、`tools/estate.py`、`services/game/building.py`、`services/account/planner.py` 的文案：把「跑一次干员识别（OperBox）」换成「绑定森空岛账号并同步练度」。

### 4.4 前端

- `OperatorsTab`：状态卡 label/description 改森空岛口径；删编辑抽屉、差异条与 `editing` 状态；空状态文案改成「先绑定森空岛并同步练度」。
- `OperatorCard`：删编辑按钮与逐字段来源徽章，保留「森空岛」单枚徽章；其余（头像、练度胶囊、技能/模组）不动。
- 删 `OperatorEditorSheet.tsx` / `OperatorEditorSheet.test.tsx` / `OperatorDiffBanner.tsx`；`api.ts` 删 `patchOperator` / `fetchOperatorDiffs` / `dismissOperatorDiffs` / `OperatorPatch` 与 `accountKeys.diffs`。
- `nav.ts` 的账号数据描述去掉「干员识别」。

### 4.5 文档与生成物

`docs/04`（任务类型与表语义）、`docs/05`（端点表）、`docs/09`（账号页段）、`docs/12`（M47 小节）、`docs/13`（§8.70）、`docs/README`（决策速查表）、`docs/15`（设置项说明）；`web/openapi.json` 与 `web/src/types/api.d.ts` 重出；`docs/copy-audit` 清单与基线重设。

## 5. 风险与对策

| 风险 | 对策 |
|---|---|
| 清库后未绑定森空岛的用户看到空列表 | 空状态直接给绑定引导，绑定卡就在同一屏；文档写明「重同步即可重建」 |
| 外部客户端仍在 PATCH 干员数据 | 破坏性变更记入 §8.70；README 决策表标注 |
| 已保存的定时任务含 `OperBox` | 校验失败即报错（不静默），§8.70 记录处置建议（改任务或删掉） |
| 养成计划引用了被清掉的干员 | 计划层把缺失当「未识别」（M35 口径），实施时用定向用例确认不炸 |
| 拆一半（例如只删任务类型、留写库路径） | 计划里按 W1–W6 分波，每波都能跑；删除面上「登记点」逐项对照侦察清单 |

## 6. 验收

主 agent 在最终提交上亲自复跑：

- 后端 `.venv/bin/python -m pytest -q`（全量）。
- 前端 `cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build`。
- `.venv/bin/python scripts/dump_openapi.py --check`。
- `.venv/bin/python scripts/account_smoke.py`（末行 `SMOKE OK`）。
- `.venv/bin/python scripts/copy_inventory.py --check`。
- 定向钉子：`OperBox` 在 `maa_api/ tests/ web/src/` 零命中（`docs/` 的历史条目除外）；`PATCH /api/account/operators/{char_id}` 与 `operator-diffs` 端点不存在；迁移 `0022` 清空且 `downgrade` 不炸；森空岛同步后 `GET /api/account/summary` 的 `operators.recognized_at` 与列表口径正确。
- 负向对照：向 `/api/tasks` 提交 `OperBox` 得到未知任务类型的 4xx；干员页 DOM 里没有「编辑」入口与差异条。
