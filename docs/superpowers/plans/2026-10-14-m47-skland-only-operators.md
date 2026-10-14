# M47 干员数据只来自森空岛（计划）

- 规格：[2026-10-14-m47-skland-only-operators.md](../specs/2026-10-14-m47-skland-only-operators.md)
- 分支：`m47-skland-only-operators`（基点 `refactor/v2` = `716deb8`）
- 完成后：`git merge --no-ff` 回 `refactor/v2`、打 tag `v2-m47`，台账复制到 `docs/superpowers/ledgers/2026-10-14-m47-skland-only-operators.md`

每个波次都能独立跑门禁；W1–W3 是后端核心（顺序依赖），W4（Agent 文案）与 W5（前端）互不重叠，W6 收文档与生成物，W7 全量验收。

## W1 删任务类型与采集链路

**写范围**：`maa_api/domain/task.py`、`maa_api/main.py`、`maa_api/services/account/capture.py`、`maa_api/services/account/callback_wiring.py`、`maa_api/services/callback_translator.py`

- `OperBoxInput` 与其 `__all__` / `_TASK_MODEL_TYPES` / `TaskInput` 联合 / `TASK_LABELS` / `RUNTIME_IMMUTABLE` / `RISK_FIELDS` 登记删干净（`OperBox` 不再出现在任务目录）。
- 采集层只留 depot：删 `parse_operbox` / `OperBoxSnapshot` / `OPERBOX_WHAT` / `_capture_operbox` 与分发分支；`DepotInfo` 路径（整表替换、绝对下限、下降护栏）逐行不动。
- `install_account_capture` 只装 depot；`callback_translator` 里 `OperBoxInfo` 的中文提示删掉。
- **验收**：`grep -rn "OperBox" maa_api/ | grep -v "内核\|MaaCore"` 无业务命中；`.venv/bin/python -m pytest -q tests/domain tests/api -x -q`。

## W2 写库层与路由只读化

**写范围**：`maa_api/services/account/box.py`、`maa_api/services/account/summary.py`、`maa_api/api/routers/account.py`、`maa_api/db/repositories/account.py`、`maa_api/services/account/__init__.py`、`maa_api/services/account/rules.py`

- 删 `apply_recognition`、`patch_operator`、`list_diffs`、`dismiss_diffs`、`_operbox_count`、`WRITE_SOURCES` 与 `account_recognition` 的 operbox 写入；`apply_skland_sync` / `record_skland_sync` / `list_operators` / `get_operator` / `summary` 保留。
- 删 PATCH `/api/account/operators/{char_id}`、`GET|DELETE /api/account/operator-diffs` 与它们的模型；`summary` 去掉 `pending_diffs`。
- 差异仓储方法（create/list/dismiss）随写路径删除还是保留？——**删**（无写者），`account_operator_diff` 表与模型留给历史数据。
- **验收**：`tests/services/test_account_box*.py`、`tests/api/test_account_router_contract.py` 按新面改绿。

## W3 迁移 0022 与迁移用例

**写范围**：`maa_api/db/migrations/versions/0022_skland_only_operators.py`、`maa_api/db/models.py`（类文档口径）、`tests/db/test_m47_clears_account_operators.py`

- `DELETE FROM account_operator`；`downgrade` 显式 no-op 并写明「数据不可恢复，重建靠森空岛同步」。
- 用例：空库升级不炸、有行时升级清空、`account_operator_diff` / `account_recognition` 行不受影响、离线 `--sql` 含 `DELETE FROM account_operator`。

## W4 Agent 层口径

**写范围**：`maa_api/agent/tools/account.py`、`maa_api/agent/policy.py`、`maa_api/agent/runtime.py`、`maa_api/agent/knowledge/__init__.py`、`maa_api/agent/knowledge/builtin/*.md`、`maa_api/agent/tools/estate.py`、`maa_api/services/game/building.py`、`maa_api/services/account/planner.py`

- 删 `update_operator_box` 工具与其确认卡片/写入白名单分支；工具总数与「对模型可见」按实施时的钉子校正（现 77 / 76）。
- 所有「跑一次干员识别（OperBox）」→「绑定森空岛账号并同步练度（`sync_skland_training`）」；知识库条目、注入段、系统提示词同步。
- **验收**：`tests/agent/` 全绿，工具目录用例的计数与新目录一致。

## W5 前端只读化

**写范围**：`web/src/features/account/{OperatorsTab,OperatorCard,RecognitionStatus,format,api,nav}.tsx|ts`、删 `OperatorEditorSheet.*` 与 `OperatorDiffBanner.tsx`、`web/src/components/layout/nav.ts`、对应 `*.test.tsx`

- 状态卡改「森空岛练度同步」口径；删编辑入口、抽屉、差异条与 `editing` 状态；空状态文案改成先绑定并同步。
- 卡片逐字段来源徽章收敛成单枚「森空岛」。
- `api.ts` 删 `patchOperator` / `fetchOperatorDiffs` / `dismissOperatorDiffs` / `OperatorPatch` / `accountKeys.diffs`。
- **验收**：`pnpm typecheck && pnpm test --run`。

## W6 生成物与文档

**写范围**：`web/openapi.json`、`web/src/types/api.d.ts`、`docs/04`、`docs/05`、`docs/09`、`docs/12`、`docs/13`、`docs/README.md`、`docs/15`、`config.template.yaml`、`docs/copy-audit/*`

- `scripts/dump_openapi.py`（重新生成）→ `cd web && pnpm gen:api`。
- docs/12 补 M47 小节；docs/13 补 §8.70（含破坏性变更：PATCH 与 `operator-diffs` 下线、`OperBox` 任务类型消失、迁移不可逆）；`config.template.yaml` 与 docs/15 的 `box_stale_days` 说明改口径。
- `scripts/copy_inventory.py --write-baseline` 后 `--check`。

## W7 门禁与验收

主 agent 亲自跑（详见规格 §6）：后端全量 `pytest -q`、`dump_openapi.py --check`、`account_smoke.py`、前端 `gen:api:check + typecheck + test --run + build`、`copy_inventory.py --check`；另跑负向对照（提交 `OperBox` 任务被拒、PATCH 端点 404、干员页无编辑入口）与 `OperBox` 残留 grep。

## W8 合并与归档

- `git merge --no-ff m47-skland-only-operators` 回 `refactor/v2`、`git tag v2-m47`、推送。
- 台账（本文的进度与裁决）复制到 `docs/superpowers/ledgers/2026-10-14-m47-skland-only-operators.md` 并提交。
