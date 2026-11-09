# M88 资源策略师（台账）

- 分支：`economist`（未合并、未 push；合并与 tag 由主 agent 复核后执行）；基点：`dev` = `e19071a`（M87 合并 + 台账提交）；合并：待补；tag：`v3-m88`（待主 agent）
- spec：[m88-economist](../specs/m88-economist.md)；计划：[m88-economist](../plans/m88-economist.md)
- 决策记录：[docs/v3/13 §8.182](../13-决策记录.md)
- 迁移：无（head 保持 `0041_nav_graph`）

## §0 结果

| # | 实测 |
|---|---|
| 1 | `## economist` |
| 2 | `v3-m87` |
| 3 | 8 行 |
| 4 | ok |
| 5 | 5 行（`put` 的 `body: bytes` 在） |
| 6 | 2 个文件 |
| 7 | 1 行 |
| 8 | `107 106` |
| 9 | 3 行 |
| 10 | 3 行（补丁驱动在） |
| 11 | 19 |
| 12 | 1 行 |
| 13 | 0（本工具尚未存在） |

- 基线（M87 合并后 dev 实测/M87 台账）：全量 pytest **3 failed / 5425 passed / 5 skipped / 2 xfailed**（3 条既有真资源失败）；`tests/evals` **72 passed**；capability replay **14/14**、`EVAL OK`；工具 107/106；配置 143/132；场景 19；错误码 171/170；端点 186/83；表 57；迁移 head 0041；定义版本 planner 3 / analyst 3 / general 6 / daily_ops 4 / copilot 4 / verifier 2 / navigator 2。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `2a7abe2` | 计划与开工台账入库；spec §0 的 13 条前置检查全过；记录 M87 合并后基线 |
| T1 | `e367b98` | v2 规划器纯重构：`_server_name`、`_normalize_needs`、`_prepare`、`StageOption`/`Recipe` 公开别名、`FarmingInputs`/`farming_inputs`；`UpgradePlanService.missing_needs`；golden 字段不变 |
| T2 | `101c0d3` | `maa_api/services/prts_parse/event_shop.py`：`parse_event_shop`（31 项）、`parse_token_drops`（8 关）、`regular_token`；附录 A 夹具与来源行 |
| T3 | `bd891fa` | `economy/{__init__,model,solver}.py`：`FarmingModel` 等数据类、矩阵、两阶段 HiGHS、整数化与取整后修复 |
| T4 | `5ff0d7f` | `economy/{data,service,report}.py`：活动/仓库/用药取数、合并、`FarmingOptimizer.optimize`、`meta={"farming": true}` 报告；理智药夹具 |
| T5 | `8996048` | `optimize_farming` 工具与装配；计数 108/107、untrusted 23、refetchable 52；工具/REST/seam 用例 |
| T6 | `76f79e5` | `planner`/`analyst`/`general` 追加 `optimize_farming` 与正文段落，版本 4/4/7 |
| T7 | `14914d7` | `World.farming_optimizer` + `FakeFarmingOptimizer` 注入；`m88-planner-week`、`m88-analyst-shop` 两个 ticket 场景与手写 cassette、两个 JSON 夹具；场景数 19→21 |
| T8 | 本台账所在提交 | 核对 02 §2.2/§4.1、03 §8.2、05 §3.2/§6.1 的已预填项；改 05 §6.1 的计数句（当前 108/107）与 M80 的 23 个、补 v2 纯重构句；16 §23 部署说明；13 §8.182；本台账；跑 §7.1 全量门禁与 §7.2 联网补充验证（**未合并、未 tag**，留给主 agent） |

## 验证证据

### Task 0–T6（各波报告为准）

| 命令 | 结果 |
|---|---|
| T1 `pytest -o addopts= -q tests/services/test_planner_farming_inputs.py` | 0；7 passed；v2 三文件 67 passed、额外 4 文件 49 passed |
| T2 `pytest -o addopts= -q tests/services/test_event_shop_parse.py` | 0；6 passed；实现不 import httpx/PrtsClient |
| T3 `pytest -o addopts= -q tests/services/economy/test_solver.py` | 0；13 passed |
| T4 `pytest -o addopts= -q tests/services/economy` | 0；41 passed；v2/W1 回归 80 passed |
| T5 `pytest -o addopts= -q tests/agent/test_economy_tool.py tests/agent/test_tool_catalog.py tests/agent/specs/test_agent_specs.py` | 0；70 passed；`build_registry()` 打印 `108 107` |
| T5 REST 补充（一次性脚本） | `POST /api/agent/tools/optimize_farming/invoke` 200 且 `report_artifact_id=null`；两个都给/都不给 → 422 `TOOL_ARGS_INVALID`；日志 `/tmp/m88-rest.log`，**未提交为测试** |
| T6 `pytest -o addopts= -q tests/agent/test_builtin_workers.py tests/agent/specs/test_m70_specs.py tests/api/test_agent_specs_router.py` | 0；12 passed；三份版本 4/4/7 |

### Task 7（评测）

| 命令 | 结果 |
|---|---|
| 先红：`pytest -o addopts= -q tests/evals/test_regression_replay.py -k loads` | 退出码 2；`world.farming_optimizer：Extra inputs are not permitted`（red 证据） |
| `pytest -o addopts= -q tests/evals/test_regression_replay.py` | 0；**7 passed**；场景 21 个都有 cassette |
| `pytest -o addopts= -q tests/evals` | 0；**72 passed**（与 Task 0 基线同为 72；两个能力场景不进 pytest 参数化） |
| `scripts/agent_eval.py --suite capability --mode replay` | 0；**16/16 通过（100.0%）**、`skipped=0`、末行 `EVAL OK`；`m88-analyst-shop`、`m88-planner-week` 都 `passed`、各 **5 次模型调用**（探查 ×2 → `optimize_farming` → `report` → 记忆抽取） |
| 未联网证据 | 评测 `_open_world` 的 HTTP transport 对所有外部请求抛 `ConnectError("评测环境不访问外部网络")`；两个场景只读 `tests/evals/fixtures/` 的 JSON，`FakeFarmingOptimizer` 返回 `copy.deepcopy(payload)`、不写产物 |

### Task 8（最终验收，2026-11-09）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest -o addopts= -q` | 最终干净复跑（文档改完后）：退出码 1；**3 failed / 5487 passed / 5 skipped / 2 xfailed**（572.72 s）。3 条恰好是基线既有真资源失败：`tests/services/test_activity_index.py::test_real_resource_pack_reports_the_current_activity_and_its_stages`、`::test_real_resource_pack_disambiguates_a_repeated_code`（真资源活动数据漂移）与 `tests/services/test_stage_layout.py::test_ambiguous_code_picks_the_first_stage_id_and_counts_candidates`（`574 != 554`，同一资源）；**新增失败 0**。passed 从基线 5425 增至 5487（M88 新增 62 条用例）。文档改完后的第一次复跑另命中既有时间敏感用例 `tests/api/test_orchestrator_e2e.py::test_daily_flow_from_message_to_ready_report`（`reports["ready"]["forwarded"]` 在转发完成前取样，4 failed / 5486 passed），单跑该用例 8.05 s 通过；第三次全量即上面的 3 failed，见「遗留」 |
| `scripts/dump_openapi.py --check` | 0；`OpenAPI snapshot is current: web/openapi.json`（工具走既有 invoke 端点） |
| `scripts/generate_api_guide.py --check` | 0；`API guide generated sections are current: docs/v3/15-开放API接入指南.md` |
| 前端五步：`pnpm install --frozen-lockfile` → `gen:api:check` → `typecheck` → `test --run` → `build` | 0；vitest **113 文件 / 994 用例通过**；`vite build` 正常；`[sw_harness] SERVICE WORKER OK` |
| `scripts/frontend_smoke.py` | 0；末行 `SMOKE OK` |
| `scripts/agent_smoke.py` | 0；末行 `SMOKE OK` |
| `scripts/orchestrator_smoke.py` | 0；末行 `SMOKE OK` |
| `scripts/nav_smoke.py`（补跑） | 0；末行 `SMOKE OK` |
| `scripts/copilot_research_smoke.py`（补跑） | 0；末行 `SMOKE OK` |
| `scripts/agent_eval.py --suite regression --mode replay` | 0；`regression replay：5/5 通过（100.0%）`、末行 `EVAL OK` |
| `scripts/agent_eval.py --suite capability --mode replay` | 0；`capability replay：16/16 通过（100.0%）`、`skipped=0`、末行 `EVAL OK`；高于 M87 台账基线 14/14 |
| `scripts/check_doc_links.py` | 0；末行 `BROKEN 0` |
| `git diff --check` | 0 |
| `.venv/bin/python -c "…build_registry…"` | `108 107` |
| `grep -n "len(definitions) == 108" tests/agent/specs/test_agent_specs.py` | 1 行 |
| `grep -n "len(UNTRUSTED_OUTPUT_TOOLS) == 23\|len(EXPECTED_REFETCHABLE) == 52" tests/agent/test_tool_catalog.py` | 2 行 |
| `grep -n "len(scenarios) == 21\|len(ids) == 21" tests/evals/test_regression_replay.py` | 2 行 |
| `grep -n "^version:" maa_api/agent/specs/builtin/{planner,analyst,general}.md` | `4`、`4`、`7` |
| `grep -n "当前 108 个、模型可见 107 个" docs/v3/05-运行时与代理定义.md` | 1 行 |
| `grep -nE "T[O]DO\|待[定]" docs/v3/plans/m88-economist.md` | 无输出（退出码 1）。brief 写的 `debug/plans/m88-economist.md` 不存在（`debug/plans/` 下只有 M89/M90），按实际计划路径核对 |
| `grep -n "共 186 条" docs/v3/14-现行接口清单.md` | 1 行（186） |
| `grep -n "ALL_CODE_COUNT = 171\|HTTP_BOUND_CODE_COUNT = 170" tests/domain/test_errors.py` | 2 行 |
| `grep -n "exactly_57_business_tables" tests/db/test_models.py` | 1 行 |
| 配置键 `.venv/bin/python -c "…SETTING_KEYS…_FIELDS…"` | `143 132` |
| 迁移 head `ls maa_api/db/migrations/versions/*.py \| sort \| tail -1` | `0041_nav_graph.py` |

## Rulings I made

本计划「接缝裁决」里实际用到的条目，以及各波新增的实现细节：

1. `FarmingModel` 没有 notes 字段：活动级 note（如「MAA 还不能自动导航」）由 `merge` 追加进 `FarmingInputs.notes`，服务统一收口；模型字段与签名不动。
2. `generated_at` 作企鹅取数时间：报告「数据来源」里企鹅来源只有 `kind`/`label`，`render_report` 用 `generated_at`（`%Y-%m-%d %H:%M`）补它的取数时间；PRTS/拆包有 `fetched_at` 时用真实时间。
3. `status` 优先级：完整方案有缺口（或求解失败）→ `unobtainable`；否则窗口装不下 → `partial`；否则 `complete`。`fits_window` 无窗口时为 null；`coverage` 用最终方案、`unobtainable` 用完整方案。
4. 无商店活动的 token-only 口径：只有活动页有关卡表代币掉落才新建 token-only 关卡；商店非空但无掉落时保留活动（商店可用已有代币），不新建关卡。
5. 畸形商店行跳过：`_parse_good` 对字段不足 4 段、家具/皮肤、整数解析失败的行返回 None；分区标题行只更新 section，不产生商品。
6. 用药降级口径：仓库没有到期时间，`counted_sanity` 恒 0、`sanity["medicine"]` 恒 0、天数不计药；`use_medicine` 只决定 `expiring_medicine` 999/0；表读不到为 0 并写 notes。
7. `regular_token` / `_server_name` / `_prepare` / `medicine_pool(sources=...)` 的接缝按计划 §0 落地，见 13 §8.182 偏离 1–4；`MedicinePool` 字段不变。
8. 测试文件名用 `tests/agent/test_economy_tool.py`（派单上下文里的 `tests/agent/tools/test_economy.py` 与 spec/plan/brief 不一致，以 brief 为准）。
9. `test_refetchable_tools_are_pinned` 除两处总数外还单独断言 `EXPECTED_REFETCHABLE == 51`（brief 未提）；已改为 52 并同步 docstring。`test_tool_catalog.py` 里 M80「附录 A 的 22 个工具」是历史说明，保留不改。
10. 05 §6.1 改成单点「当前 108 个、模型可见 107 个」（更新 §8.181 偏离 2 的累计口径），保留 M89/M90 前瞻值；M80 行的 untrusted 数 22→23；补 v2 纯重构句。
11. 16 §23「刷图方案（M88）」是 spec §9 清单之外的部署说明补充：本里程碑无新增配置键、出站域名、迁移、端点或前端页面，只说明既有依赖与用药/报告口径。
12. 评测 seam：`World.farming_optimizer` 为空时不进入新代码；两个新场景沿用 M87 的 `settings.llm.role_orchestrator = missing-profile`（避免总控观察轮抢 cassette 应答），`limits` 取 120 秒/10 次模型调用。cassette 沿用 §8.179 R7 的「探查 ×2 → 实质调用 → 记忆抽取」。
13. T8 自检 grep 的 `debug/plans/m88-economist.md` 是 brief 的笔误（计划实际在 `docs/v3/plans/`）；用实际路径核对，计划里没有 TODO/待定。
14. REST 完成判据只用一次性脚本验证、未提交端点用例（沿用 M87 的端点测试口径；工具层已覆盖 seam/转发/报告门控）。

## 偏离（已记 13 §8.182）

1. 05 §6.1 计数句从 M87 的累计口径改为单点 108/107（M88 收口后的当前值）；M80 的 untrusted 数改为 23。
2. `regular_token`：无 `EventShopList` 时从关卡表取常规代币名；不改 `ParsedShop`/`TokenDrop` 形状。
3. `_server_name` 与 `_prepare` 抽成 v2 规划器的共用接缝，`plan`/`plan_needs`/`refresh` 对外行为不变（golden 钉住）。
4. `medicine_pool` 加可选 `sources` 入参，`MedicinePool` 字段不变。
5. 默认装配不写启动期：`app.state.farming_optimizer` 优先，没有才建并写回；不建 `app.state.activity_service`。
6. 16 §23 是 spec §9 清单之外的部署说明补充（无行为变化）。
7. 评测 seam 只替换 optimizer，两个场景各 5 次模型调用；cassette 口径沿用 M87 维护性补丁。
8. T8 brief 的 `debug/plans/m88-economist.md` 路径不存在，按 `docs/v3/plans/m88-economist.md` 核对。
9. REST 完成判据没有提交端点用例，只有手工脚本证据。

## 遗留

- **联网补充验证（spec §7.2）**：2026-11-09 实跑 `default_farming_optimizer().optimize(...)`（材料「固源岩组」30、「异铁块」10，窗口 7 天），退出码 0。
  - `use_depot=true`：`status=complete`、`days_estimated=0.0`、`sanity.needed=0`、`stages=[]`——本机仓库库存已覆盖这两项需求。
  - `use_depot=false`：`status=partial`、`days_estimated=7.0`、`sanity.needed=1665`；关卡 `1-7 ×121`、`14-12 ×31`、`7-4 ×16`（常驻关）。
  - **当期活动没有进方案**：`卫戍协议·规程` 与 `时间啊，向前！` 都是 `navigable=false`（`navigation=missing`），默认不进模型；`感谢庆典2026限时签到` 既没有商店小节也没有常规代币掉落，记一条 note 后跳过。
  - **商店解析项数**：`卫戍协议·规程` 15 项、`时间啊，向前！` 37 项（合计 52 项），但默认方案没有活动关、也没有已持有代币，所以 `shop` 兑换行为空（`tokens` 行仍在）。
  - 额外验证 `include_unnavigable=true`（`use_depot=false`）：活动关进模型，`TL-4 ×39`（`navigable=false`）、`14-12 ×9`、`7-4 ×4` 等，`status=complete`、`days_estimated=3.3`；商店换 6 项（`异铁组`×15、`固源岩组`×15、`装置`×20、`聚酸酯`×30 等），代币 `earned=468`、`spent=458`、`left=10`。证明活动/商店链路联网可用，默认只是被导航状态挡住。
- **真机/前端 UI 未验证**：本任务只跑回放、假世界、冒烟与上面的联网服务调用，没有在指挥台/模拟器上走真实工单、确认、报告产物查看路径；按 AGENTS 真机检查只作补充，未写成已验证。
- 既有时间敏感用例的抖动（非 M88 引入）：
  - `tests/api/test_orchestrator_e2e.py::test_daily_flow_from_message_to_ready_report` 在文档改完后的第一次全量复跑里失败（`reports["ready"]["forwarded"] is False`，转发在断言前没完成）；单跑 8.05 s 通过，随后全量复跑 3 failed 通过。该用例与 M88 改动无关，历史上也以「时间敏感」记过（M86 评测补丁验收记录）。
  - `tests/evals/test_ticket_driver.py::test_ticket_driver_approves_confirmation` 在本波两次运行（一次全量 `tests/evals`、一次单跑）中出现 `run_timeout`（日志里有总控档案探查走真实 DNS 失败重试），随后单独重跑 3 次与全量 `tests/evals` 都是 **72 passed**；HEAD（stash 掉本波改动）单跑也通过。该用例不读 `world.farming_optimizer`，判定为环境/网络抖动，不是 M88 引入；若全量验收再遇到，先复跑该用例。
- REST 端点完成判据只有 `/tmp/m88-rest.log` 的手工证据，没有提交用例；后续要钉死可另起任务补 `tests/api` 用例。
- `docs/v3/12` 的 M88 行、`docs/v3/specs/README.md` 与 `specs/todo/README.md` 的 M88 行按 spec §9 由主 agent 升格时统一改，本任务未动。
- 合并回 `dev`、打 tag `v3-m88`、删除分支由主 agent 复核后执行；本任务不 push、不 merge、不 tag、不切分支。
