# M27 · 账号数据与养成计划 · 实施计划

规格见 [specs/2026-10-09-m25-prts-knowledge.md](../specs/2026-10-09-m25-prts-knowledge.md) §7（下文「§」都指该文件）。行为以规格为准；规格没覆盖到的实现细节由实现者决定并写进报告，主 agent 抄进台账。

## 全局约束

- 工作分支 `m27-account-data`（已从 `refactor/v2` 建立）。直接在当前检出上工作，不建 worktree，不 push，不合入 `dev`。
- 后端测试：`.venv/bin/python -m pytest -q`；前端：`cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build`。
- 改了后端接口后：`.venv/bin/python scripts/dump_openapi.py`、`cd web && pnpm gen:api`、`.venv/bin/python scripts/generate_api_guide.py`。
- 先写失败测试确认 RED，再实现 GREEN；长输出重定向到文件，只读尾部与退出码。
- **实现者不要 `git commit` / `git add` / `git stash`**；`maa_api/main.py` 由主 agent 统一改（并行任务都要动它）。
- 不引入新依赖（scipy 已加进 `pyproject.toml` 并装进 `.venv`）。

## 环境说明（scipy 与 poetry.lock）

本机 poetry 配置的镜像 `mirrors.aliyun.com` 不可达，`poetry add` / `poetry lock` 都失败；`scipy` 用 `pip install "scipy>=1.11"` 装进 `.venv`（1.18.1），`pyproject.toml` 已加 `scipy = ">=1.11"`，**`poetry.lock` 未更新**。这条作为环境偏离记录（docs/ENVIRONMENT.md 与 docs/13 §8.42），镜像可达后需要跑一次 `poetry lock`。

## 任务与写范围（并行分工）

| 任务 | 写范围 |
|---|---|
| T1 任务类型 `OperBox`/`Depot` + T2 回调采集 + T3 表/迁移/仓储/合并规则 | `domain/task.py`、`db/models.py`、`db/migrations/versions/0016_account_data.py`、`db/repositories/account.py`、`services/account/{__init__,capture}.py`、`services/callback_translator.py`、`scripts/probe_task_defaults.py` + 测试（子任务 A） |
| T3′ 账号服务（box/depot）+ T4 REST | `services/account/{box,depot}.py`、`api/routers/account.py`、`domain/errors.py`（两个新码）、`docs/05`（§4.17 与 §6.20）、`settings.py`/`settings_schema.py`/`config.template.yaml`（`agent.box_stale_days`、`agent.depot_stale_days`）+ 测试（子任务 B） |
| T8 养成规划（线性规划） | `services/account/planner.py` + 测试（子任务 D） |
| T5 前端账号数据页 | `web/src/features/account/*`、`web/src/routes/*`、`ConfirmationCard`（差异/摘要视图）、`more` 入口 + 测试（子任务 C） |
| T6 工具 + T7 注入与提示词 + T9 养成计划 + 主 agent 收尾 | `agent/tools/account.py`、`agent/tools/__init__.py`、`agent/policy.py`、`agent/confirmation.py`、`agent/knowledge/__init__.py`、`agent/runtime.py`、`services/account/plans.py`、`main.py`、通知事件、docs/13 §8.42、冒烟 |

## 冻结的接口

- 仓储（A 定义）：`db/repositories/account.py` 四个仓储；服务层只按方法语义调用。
- 服务（B 实现，我按它写工具层）：`OperatorBoxService.{summary,list_operators,get_operator,patch_operator,list_diffs,dismiss_diffs}`、`DepotService.{summary,list_items}`；构造 `(session_factory, *, settings_provider=get_settings)`。
- 规划（D 实现）：`UpgradePlanner.plan(targets, *, use_depot=True, server=None, box=None) -> UpgradePlan`，`UpgradePlan.as_dict()`。
- REST（B 实现，前端按它写）：`GET /api/account/summary`、`GET /api/account/operators`、`PATCH /api/account/operators/{char_id}`、`GET /api/account/operator-diffs`、`DELETE /api/account/operator-diffs`、`GET /api/account/depot`。
- 确认 payload（我实现）：`update_operator_box` 的 payload 带 `diff: [{"operator","field","from","to"}]` 与 `summary`；`start_upgrade_plan` 带 `summary`/`targets`/`run_times`。

## 验收（规格 §7.9，主 agent 在最终提交上亲自跑）

1. `.venv/bin/python -m pytest -q`；
2. `scripts/dump_openapi.py --check`、`scripts/generate_api_guide.py --check`、`cd web && pnpm gen:api:check`；
3. `cd web && pnpm typecheck && pnpm test --run && pnpm build`；`scripts/frontend_smoke.py`、`scripts/agent_smoke.py` 末行 `SMOKE OK`；`scripts/copy_inventory.py --check`；
4. `scripts/account_smoke.py`（假内核回调跑通「识别 → 页面数据 → 规划 → 计划 → 模拟完成」，末行 `SMOKE OK`）；
5. 真机识别（OperBox / Depot）作为补充证据，不是硬门槛；
6. `git merge --no-ff` 回 `refactor/v2`，打 tag `v2-m27`；台账抄到 `docs/superpowers/ledgers/`。

## 不做

导入导出（Q37）；前端「刷新识别」按钮（Q8）；升级经验与龙门币的计算（M29 补）；替用户执行升级。
