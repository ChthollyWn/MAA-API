# M87 验证闸门与作业研究闭环（台账）

- 分支：`verifier-copilot-loop`；基点：`dev` = `11ebbe2`（M86 合并后的提交）；合并：待主 agent 复核后执行（本任务不做合并、tag、切分支）；tag：`v3-m87`（待主 agent）
- spec：[m87-verifier-copilot-loop](../specs/m87-verifier-copilot-loop.md)（929 行，2026-11-09 升格版）
- 计划：[m87-verifier-copilot-loop](../plans/m87-verifier-copilot-loop.md)（755 行）
- 决策记录：[docs/v3/13 §8.181](../13-决策记录.md)
- 迁移：无（head 保持 `0041_nav_graph`）

## §0 结果

- #1 `## verifier-copilot-loop`；#2 `v3-m86`；#3 4；#4 ok/1；#5 2/2；#6 1；#7 1/2（record_report 与重启恢复）；#8 1；#9 True ×7；#10 1/1/2（续租存在）；#11 2/2/2；#12 1/1/1；#13 1/1/8/4/2/1（补丁符号齐全）；#14 13、107 106、142 131；#15 186/83、171/170/22、表 57；#16 `0041_nav_graph.py`。命令输出见 `/tmp/m87/precheck.log`。
- **基线**：全量 pytest 以 M86 合并后 `dev` 实测为准 = **3 failed / 5369 passed / 5 skipped / 2 xfailed**（3 条均为 M86 前既有真资源失败）；`tests/evals` **70 passed**；`agent_eval --suite capability --mode replay` **8/8（100%）**、末行 `EVAL OK`。开工时并行跑的 `/tmp/m87/pytest-base.log` 因与 W1 实施并发（52 failed/7 errors）作废，不作为基线；`/tmp/m87/eval-base.log`、`/tmp/m87/cap-base.log` 与上述一致。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `af7cc51`、`70c12dd` | 计划与开工台账入库；spec §0 的 16 条前置检查全过；记录 M86 合并后基线（3 failed / 5369 passed / 5 skipped / 2 xfailed、`tests/evals` 70 passed、capability 8/8） |
| T1 | `d3423b0` | `verify_before_ready` 进 `AgentSpecFrontmatter` 与 `_check_field_rules`（只允许 worker）；`daily_ops`、`copilot` 开为 true、`version` 4，`verifier` `version` 2；`general` 与其余 Worker 不动 |
| T2 | `48b8d5d` | 新建 `maa_api/agent/workers/verification.py`（`VerificationGate`、`GateDecision`、常量、`merge_verification`）；`CopilotAttemptRepository.list_for_session`；闸门判定、`suggest()`、`record_verdict` 与两组单测 |
| T3 | `26ecd24` | `submit_report` 在 `record_report` 前过闸门并合并 `details.verification`；`ReportMiddleware` 见 `accepted=false` 不结束运行；`DelegationService` 写验证员 `meta["verdict"]` |
| T4 | `79d7de3` | `copilot_loop.max_iterations`（配置 143/132）；工单会话预填与 `max_attempts`；旧授权 `exhausted` 后下一次弹新卡；v2 聊天会话仍抛 `COPILOT_GRANT_EXHAUSTED`；`approve` 夹住次数 |
| T5 | `83cc5f8` | 前端授权卡按 `max_attempts` 截断下拉与预填，说明文字按 §4.6 |
| T6 | `d2a5b1c` | `copilot.md` 研究循环五步、`verifier.md` 核对完成证据、`daily_ops.md` 汇报完成；copilot 第 2 步按 M86 工具归属改为委派导航员 |
| T7 | `7200a1e` | 6 个 `m87-*` 场景与 6 份手写 cassette；`test_regression_replay.py` 计数 13→19、`test_every_scenario_has_a_driver`；`cassette.py` 占位符解析兼容工具结果后追加的系统观察（新增 `test_placeholder_ignores_appended_observation`） |
| T8 | 本台账所在提交 | 核对 05 §1.1/§1.2/§3.2/§4.1/§5.2/§6.1、02 §2.2/§4.1、03 §3.2/§6.3、04 §6 的已预填项；补 14 §6.16 的 `max_attempts`、16 的 `copilot_loop.max_iterations`、13 §8.181；全量验收（**未合并、未 tag**，留给主 agent） |

## 验证证据

### Task 7（评测场景）

| 命令 | 结果 |
|---|---|
| 先红：`pytest -o addopts= -q tests/evals/test_regression_replay.py` | 退出码 1，`assert 13 == 19`（`test_every_scenario_loads_and_has_a_cassette`）；其余 6 条通过 |
| 6 个场景逐个 `agent_eval --suite capability --mode replay --scenario <id>` | 全部退出码 0、全部 `passed`：`m87-daily-machine-pass` 模型调用 6、`m87-daily-failed-pipeline` 9、`m87-copilot-two-rounds` 7、`m87-copilot-round-exhausted` 5、`m87-copilot-model-judged` 9、`m87-waiver` 14；没有 `skipped`、没有 `mismatch`/`exhausted`/`unused` |
| `agent_eval --suite capability --mode replay --min-pass-rate 1.0` | 退出码 0；`14/14 通过（100.0%）`、末行 `EVAL OK`；日志里 `跳过` 4 条，与开工前基线相同（都是既有 v2 场景的评审跳过），6 个新场景 0 跳过 |
| `pytest -o addopts= -q tests/evals` | 72 passed（基线 70 + 驱动用例 1 + 占位符用例 1） |
| `pytest -o addopts= -q tests/evals/test_eval_cassette.py tests/evals/test_regression_replay.py` | 18 passed |

### Task 8（最终验收，2026-11-09）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest -o addopts= -q` | 退出码 1；**3 failed / 5425 passed / 5 skipped / 2 xfailed**（590.04 s）。3 条恰好是基线既有失败：`tests/services/test_activity_index.py::test_real_resource_pack_reports_the_current_activity_and_its_stages`、`::test_real_resource_pack_disambiguates_a_repeated_code`（真资源活动数据漂移）与 `tests/services/test_stage_layout.py::test_ambiguous_code_picks_the_first_stage_id_and_counts_candidates`（`554 != 574`，同一资源）；**新增失败 0**。passed 从基线 5369 增至 5425（M87 新增 56 条用例） |
| `scripts/dump_openapi.py --check` | 退出码 0；`OpenAPI snapshot is current: web/openapi.json` |
| `scripts/generate_api_guide.py --check` | 退出码 0；`API guide generated sections are current: docs/v3/15-开放API接入指南.md` |
| 前端五步：`pnpm install --frozen-lockfile` → `gen:api:check` → `typecheck` → `test --run` → `build` | 退出码 0；vitest 113 文件 / 994 用例通过；`vite build` 正常；`[sw_harness] SERVICE WORKER OK` |
| `scripts/frontend_smoke.py` | 退出码 0；末行 `SMOKE OK` |
| `scripts/agent_smoke.py` | 退出码 0；末行 `SMOKE OK` |
| `scripts/orchestrator_smoke.py` | 退出码 0；末行 `SMOKE OK`、`仓库 DB/config 指纹未改变（1421312 bytes）`。第一次与全量 pytest 并发跑时因仓库 `resource/maa_api.db` 被 pytest 进程改动而报「冒烟写入了仓库 DB/config 产物」；pytest 结束后单独重跑通过，不是 M87 引入 |
| `scripts/copilot_research_smoke.py` | 退出码 0；末行 `SMOKE OK`（v2 聊天会话的作业授权口径没有回退） |
| `scripts/agent_eval.py --suite regression --mode replay` | 退出码 0；`regression replay：5/5 通过（100.0%）`、末行 `EVAL OK` |
| `scripts/agent_eval.py --suite capability --mode replay --min-pass-rate 1.0` | 退出码 0；`capability replay：14/14 通过（100.0%）`、末行 `EVAL OK`；`跳过` 4 条，与开工前基线相同（既有 v2 场景的评审跳过），6 个 M87 场景 0 跳过 |
| `git diff --check` | 退出码 0 |
| `grep -n "max_attempts" docs/v3/14-现行接口清单.md` | 1 行 |
| `grep -n "copilot_loop.max_iterations" docs/v3/16-部署指南.md` | 1 行 |
| `scripts/check_doc_links.py`（默认） | 退出码 0；末行 `BROKEN 0`（没有基线之外的断链） |
| `scripts/check_doc_links.py --all` | 退出码 0；`BROKEN 26`，逐条与 `scripts/doc_links_baseline.txt` 完全一致（diff 为空），没有新增断链；spec §9 记的「今天 4 条」是旧口径，实测 26 条全在基线里 |
| `scripts/copy_inventory.py --check` | 退出码 1；`lint-baseline.json` 的 sha256 运行前后一致（`7f21935d…`），`--check` 只读也会重写 9 份清单/回潮文件（已 `git restore` 复原，工作区没有它的改动）；回潮条目远多于 spec 记的 4 条，属既有漂移、非 M87 引入，见「Rulings」 |

## Rulings I made

本计划「接缝裁决」里实际用到的条目：

1. `ctx.spec` 替身：`submit_report` 读 `verify_before_ready` 用 `bool(getattr(ctx.spec, "verify_before_ready", False))`；`tests/agent/workers/test_worker_service.py` 的 `_SPEC` 是 `SimpleNamespace`，没有这个字段，不改既有用例。
2. 拒绝形状用工具结果 `accepted=false`（`status="ok"`、`stop_run=False`），不抛 `AppError`；`ReportMiddleware` 不写 `SUBMITTED_KEY`。
3. 两批用例允许轮末 `ensure_report` 补 `progress`（`details.auto=true`）：被拒的 `report` 不落 `ticket_report` 行；断言只约束被拒的那次调用，不断言「整个用例没有 `ticket_report` 行」。
4. `_ticket_worker_session` 依赖 `context.db_session`：PolicyMiddleware 求值时有，REST 调用会提前返回；沿用 v2 口径，不额外开关。
5. 验证员结论查询按 `ticket_id` + `agent_name == "verifier"`，在 Python 里判 `meta["verdict"]`，不写 JSON SQL、不按 `run.status` 过滤。
6. `suggest()` 只列最近 3 条流水线、3 条尝试，`task`+`context` 合计 ≤4000 字；验证员 `max_steps` 保持 8，不加工具。
7. 语义：`merge_verification` 在 T2 没有单测（spec §6 清单没有它），由 T3 的汇报接入用例覆盖。
8. copilot 提示词第 2 步按 M86 的工具归属改为「委派 navigator 到开始行动页」，见 [13 §8.181](../13-决策记录.md) 偏离 1。
9. `m87-copilot-round-exhausted` 降级为模型行为回放：假尝试不消耗授权、`remaining` 恒为 `None`（维护性补丁裁决），场景验不到「旧授权落 `exhausted` 并弹新卡」；授权状态机由 `tests/agent/test_copilot_loop_limit.py::test_ticket_session_exhausted_grant_opens_new_card` 与 `::test_chat_session_exhausted_grant_still_raises` 覆盖。
10. 05 §6.1 保持累计口径、不改成单点 107/106，见 [13 §8.181](../13-决策记录.md) 偏离 2。
11. 评测 cassette 占位符：`_tool_results` 先整段 `json.loads`，失败再取开头 JSON 值，兼容动态尾部并进 tool 消息与截图提示；否则 `{{result:…}}` 解析不到 `pipeline_id`、`attempt_id`、`question_id`。
12. 两条失败流水线场景加 `world.device_connected=false`：让流水线在设备预检立即置 `failed`，避免失败剧本每任务 3×30 秒重试超过 60 秒场景上限；`world.core_script="failure"` 仍保留。
13. 六个 M87 场景的 `settings` 把 `llm.role_orchestrator` 指向不存在的档案：确认/提问冒泡会唤醒总控并起一轮模型调用（M86 §8.180 记录过一次），会抢走手写 cassette 的应答；场景不断言总控，故在评测里关掉这一轮。
14. 文案回潮：`docs/copy-audit/lint-baseline.json` 是旧基线，`copy_inventory.py --check` 的回潮数多于 spec 记的 4 条，属既有漂移、非 M87 引入；`--check` 只读也会重写清单，所以不以它的 git diff 作证据，用 `lint-baseline.json` 的 sha256 前后一致与「`git restore` 后工作区干净」作证据。

## 偏离（已记 13 §8.181）

1. `copilot.md` 第 2 步导航工具归属：spec 写让 copilot 自己 `navigate_to`，实现按 M86 改为委派 `navigator`（三个导航工具只给导航员）。
2. 05 §6.1 保持累计口径（M86 后 107/106，M87 不变，M88–M90 后依次 108/107、110/109、111/110），不按 spec T8 字面改成单点 107/106。
3. `m87-copilot-round-exhausted` 只回放模型行为，授权状态机由 Task 4 单测覆盖。
4. Task 7 的评测设施补充：占位符解析兼容追加观察、失败流水线条目用 `device_connected=false`、M87 场景在评测里关闭总控模型调用；前两条是 spec §5 T7 手写 cassette 口径没有覆盖到的边界。

## 遗留

- `tests/evals/scenarios/m87-copilot-round-exhausted.yaml` 的 `copilot_loop.max_iterations=2` 在假尝试不消耗授权的前提下不生效，该场景只验模型行为；授权换卡状态机由单元测试覆盖（见「Rulings」与 13 §8.181 偏离 3）。


- 真机补充验证（spec §7.2）**未验证**，见下节。
- `m87-copilot-round-exhausted` 的授权换卡只由单测覆盖，评测场景不覆盖；如果未来维护性补丁让假尝试消耗授权，可改回端到端验证。
- 文案 AI 腔回潮是旧基线下的既有漂移（`lint-baseline.json` 未重设）；本里程碑没有改 `docs/copy-audit`，后续如需收敛另起任务。
- 合并、tag `v3-m87`、删除分支由主 agent 复核后执行；本任务不 push、不 merge、不 tag、不切分支。

## 真机补充验证结果（spec §7.2）

| # | 项 | 结果 |
|---|---|---|
| 1 | 指挥台说「用作业三星 1-7」，建 `copilot` 工单，授权卡上限 3 | 未验证 |
| 2 | 委派导航员、`navigate_to` 到开始行动页、ready 汇报 `method="machine"`、`ticket.result.verification` 可见 | 未验证 |
| 3 | 日常工单故意失败，ready 被拒、验证员被委派、最终 `failed`/`blocked` | 未验证 |
| 4 | 一轮授权用完，汇报 `waiting` + `decision`，回复后弹第二张卡；v2 聊天会话仍报 `COPILOT_GRANT_EXHAUSTED` | 未验证 |
| 5 | 两次验证不通过后 `ask_user` 放行，汇报 `method="waiver"`；重启后 `{"selection": "仍按完成结束"}` 路径 | 未验证 |
| 6 | 台账记录每张工单的模型调用次数、验证员次数、误放行、第二张卡路径 | 未验证 |
