# M35 · 练度未知不冒充事实（实施计划）

规格：`docs/superpowers/specs/2026-10-11-m35-unknown-training.md`（本计划的所有口径以规格为准）
分支：`m35-unknown-training`（基点 `refactor/v2` = tag `v2-m34`）

合并 `--no-ff` 回 `refactor/v2` + tag `v2-m35`；不 push、不碰 `dev`；主 agent 亲自复跑验收。

## 交付项

| # | 任务 | 落地物 |
|---|---|---|
| W1 | 数据模型可空化 + 迁移 `0018_unknown_training`（含按 `field_sources`/`recognized_values` 回填） | `maa_api/db/models.py`、`maa_api/db/migrations/versions/0018_unknown_training.py`、迁移用例 |
| W2 | wire 与读/写语义：`box_row_wire` 不再兜底、`mastery_wire/module_wire(None)->None`、`UNKNOWN_TRAINING_FIELDS`、`patch_operator` 约束只在已知时判 | `maa_api/services/account/rules.py`、`maa_api/services/account/box.py`、用例 |
| W3 | 识别路径：只有 `all_opers` 条目的行保持三列 NULL；技能/专精/模组永不写 | `maa_api/services/account/capture.py`、`maa_api/services/account/box.py:apply_recognition`、用例 |
| W4 | 规划层：未知不虚报，输出 `unknowns` + note；`plans.py` 进度同口径 | `maa_api/services/account/planner.py`、`maa_api/services/account/plans.py`、用例 |
| W5 | API 契约：`AccountOperatorRow` 六字段可空、PATCH 语义文案、重生成 openapi/types/API 指南 | `maa_api/api/routers/account.py`、`web/openapi.json`、`web/src/types/api.d.ts`、`docs/api-guide.md`（若受影响） |
| W6 | 前端「未识别」：列表/详情/编辑抽屉/摘要/筛选文案 | `web/src/features/account/*.tsx`、`format.ts`、`constraints.ts`、`api.ts` 与用例 |
| W7 | Agent 口径：工具描述 + 知识库补「OperBox 不给技能/专精/模组，null＝未识别」 | `maa_api/agent/tools/account.py`、`maa_api/agent/knowledge/builtin/` |
| W8 | 文档与台账：docs/04、docs/05、docs/13 §8.50、docs/12 M35、progress.md + ledger | 五处文档 |

写范围互斥：W1–W3、W5 由主 agent（后端同一条链，避免并发改同一批文件）；W4 与 W6 可并行
（W4 只动 `services/account/planner.py`、`plans.py` 与对应测试；W6 只动 `web/`）；W7、W8 在主链冻结后写。

## 顺序与依赖

1. W1 → W2（同一条读/写链，必须先后落库再谈测试期望）→ W3。
2. W1 的模型就绪后即可并行 W4（规划层读的是 wire 形状）与 W5（API 模型）。
3. W5 生成物冻结后开 W6（前端类型依赖生成结果）。
4. W2/W3/W4/W5/W6 全绿后写 W7、W8，最后跑门禁。

## 验收

AGENTS.md 门禁全跑：

- `.venv/bin/python -m pytest -o addopts= -q`（已知红：`tests/services/test_activity_index.py` 两条真资源用例）
- `scripts/dump_openapi.py --check`、`scripts/generate_api_guide.py --check`、`cd web && pnpm gen:api:check`
- `scripts/copy_inventory.py --check`（基线若因本轮变化需重设，用 `--write-baseline` 并说明）
- `scripts/agent_smoke.py`、`scripts/frontend_smoke.py`（末行 `SMOKE OK`）
- `cd web && pnpm test`、`pnpm typecheck`、`pnpm build`

本里程碑专项（规格 §7）：真机库迁移结果核对、`GET /api/account/operators` 返回 null、
规划 `unknowns`、负向对照（wire 兜底改回 1/{} 必须让护栏失败）、写路径未知放行 / 已知违法仍拒。

## 风险与回退

- SQLite 重建表用 `batch_alter_table`，先在真机库副本上演练再上正式库（服务重启前备份 `resource/maa_api.db`）。
- 前端若漏改会显示 `null`，用负向对照兜住。
- 规划层期望值变化会让既有用例失败：按规格 §5 更新期望值并记进台账。
