# M27 进度台账

计划：`docs/superpowers/plans/2026-10-10-m27-account-data.md`
规格：`docs/superpowers/specs/2026-10-09-m25-prts-knowledge.md` §7
分支：`m27-account-data`（自 `refactor/v2`），完成后 tag `v2-m27`

## 任务状态

| 任务 | 状态 | 产物 |
|---|---|---|
| T1 任务类型 `OperBox`/`Depot` | 完成 | `domain/task.py` 8 处登记点；`scripts/probe_task_defaults.py` |
| T2 回调采集 | 完成 | `services/account/capture.py`、`callback_translator.py` 两条模板 |
| T3 表 / 迁移 / 仓储 / 合并规则 | 完成 | `db/models.py` 五张表、`0016_account_data.py`、`db/repositories/account.py` |
| T3′ 账号服务 + REST | 完成 | `services/account/{box,depot,rules}.py`、`api/routers/account.py`（7 条）、docs/05 §4.17/§6.20 |
| T5 前端账号数据页 | 完成 | `web/src/features/account/*`（12 个文件 + 5 份测试）、`nav.ts`/`router.tsx`、`ConfirmationCard` 差异/摘要 |
| T6 工具 | 完成 | `agent/tools/account.py` 7 条（工具总数 56 → 63）、`policy.py` 的 `ACCOUNT_WRITE_TOOLS`、`confirmation.py` 合入 `extra_payload` |
| T7 注入与提示词 | 完成 | `knowledge/__init__.py` 的账号数据段、`services/account/summary.py`、「外部资料」段追加账号数据规则 |
| T8 养成规划 | 完成 | `services/account/planner.py`（HiGHS 线性规划）+ 31 例；scipy 依赖 |
| T9 养成计划 | 完成 | `services/account/plans.py`（今天的流水线 + 每日定时任务 + 进度重算 + 自动完成 + 通知）、`NotifyEvent.UPGRADE_PLAN_FINISHED`、`pipeline_runner.bind_finish_hook`、main.py 装配 |
| T10 文档 / 冒烟 / 验收 | 完成 | docs/04、05、09、11、12、13 §8.42、15、ENVIRONMENT、README；`scripts/account_smoke.py` |

## 实测与规格的偏离（写进 docs/13 §8.42）

1. 五张表与附录 E 的两处差异：`account_operator` 多一列 `profession`、`account_depot_item` 多一列 `category`（页面与工具要按职业/分类筛选）。
2. 只建 3 条索引：`account_operator` 与 `account_depot_item` 只有几百行、全表读 + 内存筛选，按 docs/04 §6 不建索引。
3. 企鹅物流的三个端点（`/stages`、`/items`、`/period`）必须在 `/PenguinStats/api/v2` 前缀下（M26 实测，附录 D 的表格是旧的）。
4. `scipy` 用 `pip install` 装进 `.venv`（1.18.1 / numpy 2.5.3），`pyproject.toml` 已加 `scipy = ">=1.11"`，但 **`poetry.lock` 没能更新**：本机 poetry 的镜像 `mirrors.aliyun.com` 不可达，`poetry add`/`poetry lock` 都失败，且 Poetry 拒绝把自定义 URL 的 source 命名为 `pypi`。镜像可达后需要跑一次 `poetry lock`。
5. 编辑约束的非法值统一用 `VALIDATION_ERROR`(422) 而不是 `INVALID_PARAMETER`(400)，与请求模型的字段校验口径一致，前端按 `details.field` 标字段。
6. 识别回调给不出职业，由采集层用本机 `battle_data.json` 补 `profession`（英文枚举转中文）；三处职业映射各留一份（服务端采集 / `game/operators.py` / 前端），出现新职业原样存英文。
7. 差异的记录口径：只记「有上次识别值 ∧ 来源是手动或 Agent ∧ 手动值 ≠ 这次识别值 ∧ 手动值 ≠ 上次识别值」的字段；用户改回识别值时不打扰。
8. 干员概要在 REST 里的形状是 `{"box": {...}, "depot": {...}}`（服务层键名），前端的转写层把它翻成页面用的 `operators`/`elite2`/`recognized`；差异列表是 `{"items", "total"}`。**冻结形状与实际实现的这两处差异由前端转写层吸收**，`pnpm gen:api` 之后按生成类型替换。

## Rulings I made

1. 账号写入（`update_operator_box`、`start_upgrade_plan`）走策略层的单独分支：`risk=NONE`、每次确认、`PolicyDecision.extra_payload` 带差异/摘要；`confirmation.py` 把 `extra_payload` 合进确认 payload（`update_operator_box` 带 `diff`，`start_upgrade_plan` 带 `summary`/`targets`/`run_times`）。差异计算读得到当前行时给「旧值 → 新值」，读不到时只给目标值（fail open，确认照常）。
2. 差异只记模型里**显式给出**的字段（`exclude_unset=False` 会把未填字段 dump 成 `None`，那不是「要改成 None」）。
3. 注入段的识别天数由 `recognized_at` 现算；`stale_days` 是配置里的**阈值**，两者不能混用（M27 的注入用例专门钉住这条）。
4. 概要读取器（`AccountSummaryReader`）把服务层的 `elite_two`/`recognized_at` 翻成注入段要的 `elite2`/`recognized`，并把干员与仓库两半合并；某一边读不到就那一半按「尚未识别」处理。
5. 计划的流水线终态钩子挂在 `PipelineRunner` 上（`bind_finish_hook`，与 `bind_notify_service` 同样的后置可选绑定），终态时后台重算在跑计划的进度；钩子失败只记日志，不影响流水线收尾。
6. `UpgradePlanService._update_plan` 只对 JSON 列做可序列化转换，时间列保持 `datetime`（否则 SQLite 的 DateTime 绑定会报 StatementError）。
7. 冒烟脚本的临时库关掉外键检查：假流水线与假定时任务的 id 不在库里，`upgrade_plan.schedule_id` 的外键会把计划写入挡下来（生产库的 PRAGMA 不动）。

## 主 agent 亲自复跑的验收

- 后端 `.venv/bin/python -m pytest -o addopts= -q` → **2 failed, 2905 passed, 5 skipped**；两条失败是 M24 起就存在的真资源用例（`tests/services/test_activity_index.py`）。
- `scripts/dump_openapi.py --check`、`scripts/generate_api_guide.py --check`、`cd web && pnpm gen:api:check` 退出码 0（先重新生成；新增 7 条端点、2 个错误码、1 个通知事件）。
- 前端 `pnpm typecheck && pnpm test --run && pnpm build` 退出码 0（67 文件 / 683 项）。
- `scripts/account_smoke.py`、`scripts/agent_smoke.py`、`scripts/frontend_smoke.py` 末行都是 `SMOKE OK`。
- `scripts/copy_inventory.py --write-baseline` 后 `--check` 通过。

## 实施中修掉的问题（记录）

1. 我把 `tools/game.py` 改坏过一次（丢函数头导致 SyntaxError），M26 已记录；本次同类风险点（`main.py` 的 `UpgradePlanService` 局部导入）在装配顺序变化后立刻就暴露了 `UnboundLocalError`，已改成在用到它的分支里导入。
2. `upgrade_plan.completed_at` 一度被 `_jsonable` 转成 ISO 字符串写入 DateTime 列 → `StatementError`，已修（只转 JSON 列）。
3. 冒烟脚本的 `message` 参数一开始写成 `3`，而 `Message.SubTaskExtraInfo` 的值是 `20003`，采集层直接跳过。
4. M25/M26 的测试门禁常量（任务类型数 10 → 12、标签数 17 → 18、工具目录 56 → 63、错误码 112/111、路由小节 20、通知事件清单）随本次接口变化同步更新。
5. 前端与后端的概要/差异键名不一致（冻结形状 vs 实现），统一以后端为准、由前端转写层吸收。
