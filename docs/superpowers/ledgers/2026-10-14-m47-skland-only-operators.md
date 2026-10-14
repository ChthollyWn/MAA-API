# M47 干员数据只来自森空岛（台账）

- 规格：[docs/superpowers/specs/2026-10-14-m47-skland-only-operators.md](../specs/2026-10-14-m47-skland-only-operators.md)
- 计划：[docs/superpowers/plans/2026-10-14-m47-skland-only-operators.md](../plans/2026-10-14-m47-skland-only-operators.md)
- 分支：`m47-skland-only-operators`（基点 `refactor/v2` = `716deb8`），tag `v2-m47`
- 决策记录：[docs/13 §8.70](../../13-决策记录.md)
- 来源：用户 2026-10-14「修改账号数据干员展示页，去除干员识别任务，干员数据只能来源于森空岛同步」

## 用户裁决（三项，开工前确认）

1. **后端也整体删除 `OperBox`**：任务类型、采集回调、写库与两道护栏、Agent 引导、任务表单全链摘掉，不是只从页面拿掉入口。
2. **手动修正与 Agent 补录也去掉**：编辑抽屉、`PATCH` 端点、`update_operator_box` 工具与差异条一并下线，干员页纯只读。
3. **清掉旧数据**：迁移清空 `account_operator`，等森空岛重新同步。

第 3 条在实测后重新确认过一次：本地库 463 行当时**已经全部是 `skland` 来源**，我更倾向「只删从未被森空岛写过的行」，用户仍选择**全清**（页面空到下次同步，重同步即恢复）。

## 我做的裁决（实现细节，记录在案）

| # | 裁决 | 理由 |
|---|---|---|
| R1 | `account_operator_diff` 表与历史行**保留**，只删仓储方法、两个端点与前端差异条 | 删表要新迁移且会丢审计；没有写入者之后它不会再长 |
| R2 | `account_recognition` 里 `kind='operbox'` 的历史记录保留 | 识别审计；概要的「上次写入」取 `max(account_operator.recognized_at)`，不受它影响 |
| R3 | `GET /api/account/operators/{char_id}` **保留**（只删 PATCH） | 外部 API 读取方仍需单条读；删的是写路径 |
| R4 | `agent.box_stale_days` **键保留**，只改标签与说明 | 少动设置契约与 `config.yaml` 兼容；`recognized_at` 语义自然落到「距上次森空岛同步」 |
| R5 | `field_sources` 仍照写，`KNOWN_SOURCES` 保留四个历史取值 | 单枚「森空岛」徽章够用，但老响应不该因为一个历史取值丢掉整行来源 |
| R6 | 旧定时任务 / 外部客户端里的 `OperBox` **不写兼容层**，校验失败即报错 | 用户要求「去除」；留兼容等于没删。真机库 6 条定时任务模板实测都不含 `OperBox` |
| R7 | `RecognitionStatus` 的过期提示改成调用方传 `staleHint`，不直接改通用文案 | 该组件被仓库页共用，仓库页的「去任务里重新识别」不能跟着变成「去同步」 |
| R8 | 删除随编辑通道失效的死代码：`policy._current_operator`、`_evaluate_account_write` 的 `payload` 形参、`rules.py` 的编辑校验函数、`repositories.upsert_many`、`constraints.ts` | 「整条链路删除」的自然结果，留着是误导 |

## 波次与落地

| 波次 | 内容 | 结果 |
|---|---|---|
| W0 | 两路只读侦察（后端 9 节 + Agent/前端/文档 7 节），补出 4 个计划外漏点：`scripts/account_smoke.py` 重度依赖 OperBox、`constraints.ts` 变死代码、`SklandBindingCard` 的 `accountKeys.diffs()`、`RecognitionStatus` 与仓库页共用 | 全部纳入 |
| W1 | `domain/task.py` 的 7 处登记、`capture.py` 的 OperBox 采集、`box.py` 的 `apply_recognition` / `patch_operator` / 差异读写、`api/routers/account.py` 的 PATCH 与 diffs、`rules.py` 编辑约束 | 任务类型 13 → 12 种；`OperBoxInfo` 现在 `skipped="unsupported"` |
| W2 | 迁移 `0022_skland_only_operators`：`DELETE FROM account_operator`，`downgrade` 显式 no-op | 真机库副本实测 463 → 0，仓库 76 行 / 识别记录 26 行不受影响 |
| W3 | Agent 层 12 个文件（工具、策略、提示词、知识库、hint） | 工具 77/76 → 76/75 |
| W4 | 前端只读化：删编辑抽屉 / 差异条 / `constraints.ts`，卡片与状态卡改森空岛口径 | 74 文件 763 例全绿 |
| W5 | 后端 22 个测试文件 + 新增迁移用例；`account_smoke.py` 重写成「OperBox 负向 + 森空岛写入」 | 见验收 |
| W6 | 生成物（openapi / api.d.ts / 任务类型 fixture）重出、docs 04/05/09/11/12/13/15/README/`config.template.yaml` 口径同步、文案基线重设 | OpenAPI operation 124 → 121 |

## 验收（主 agent 在最终提交上亲自复跑）

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m pytest -q` | 见提交信息（全量） |
| `cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` | 全绿：typecheck 0、74 文件 763 例、build 含 SW harness |
| `.venv/bin/python scripts/dump_openapi.py --check` | `OpenAPI snapshot is current` |
| `.venv/bin/python scripts/generate_task_types_fixture.py --check` | `is current` |
| `.venv/bin/python scripts/account_smoke.py` | `SMOKE OK`（含 `[1a] OperBoxInfo 已被忽略`、`[8] 干员写路径已下线`） |
| `.venv/bin/python scripts/frontend_smoke.py` | `SMOKE OK` |
| `.venv/bin/python scripts/copy_inventory.py --check` | 无回潮 |

负向对照：`OperBox` 提交被任务校验拒绝（类型不在 `TASK_MODELS`）；`PATCH /api/account/operators/{char_id}` 与 `operator-diffs` 不在 OpenAPI paths；干员页 DOM 无「编辑」入口与差异条（前端用例）。

## 遗留

- `confirmation` 卡片的 `update_operator_box` 分支删除后，账号组只剩 `start_upgrade_plan` / `update_user_profile` / `sync_skland_training` 三处确认；`ConfirmationCard` 的 `isAccountTool` 判定同步收窄。
- 真机上「迁移删行 → 重新绑定并同步 → 页面恢复」由用户复验（自动化只覆盖临时库与真库副本）。
- `docs/superpowers/` 下的旧规格、计划与台账按既有约定保留原貌，不回填。
