# M88 资源策略师 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增工具 `optimize_farming` 与服务 `FarmingOptimizer`：复用 v2 养成规划的配方闭包、掉率与候选关，用两阶段 HiGHS 线性规划把当期活动关卡（代币来源）与活动商店（库存、单价）纳入方案，按当前理智、时间窗口与是否用药给出刷图清单、天数、商店兑换建议与中文说明；内置 Worker 调用且 `write_report=true` 时写一份 `meta={"farming": true}` 的 Markdown 报告产物。

**Architecture:** T1 先把 v2 规划器的「入口归一」与「取数」抽成可复用接缝（纯重构，`as_dict()` 逐字段不变），并给计划服务加 `missing_needs`；T2 在 `prts_parse/event_shop.py` 用附录 A 夹具做纯解析（不联网、不依赖 economy 包）；T3 写纯求解器（矩阵、两阶段、整数化、取整后修复）；T4 写取数（活动/仓库/理智药）、合并成 `FarmingModel`、服务编排与报告；T5 写工具、装配与工具计数；T6 改三份代理定义；T7 给评测加 `app.state.farming_optimizer` seam 与两个 ticket 场景；T8 同步文档、写台账、跑 §7 验收并合并。

**Tech Stack:** FastAPI + SQLModel + pytest；`scipy.optimize.linprog(method="highs")`（经 `planner._linprog_call` 调用）；PRTS wikitext 解析设施（`find_templates`/`parse_templates`/`template_param`）；PyYAML/JSON-in-YAML 评测场景与手写 cassette；`.venv/bin/python`，本机没有 `rg`。

**Spec:** [m88-economist](../specs/m88-economist.md)（1083 行；§0 前置检查、§4 设计、§5 T1–T8、§6 测试清单、§7 验收、§9 文档同步、§11 契约、附录 A 真实 PRTS 夹具）

## Global Constraints

- 分支 `economist`，从 `dev` 新建；**不 push**；完成后 `git merge --no-ff economist` 回 `dev`、删分支、打 tag `v3-m88`；**不使用 worktree**，直接在当前检出上工作（`.venv`、`config.yaml`、`resource/` 不进版本库）。
- 工具计数：开工 107/106 → 交付 **108/107**（总数 108、模型可见 107）；`untrusted_output` 23；`refetchable` 52。
- 端点 **186（agent 83）不变**；错误码 **171/170 不变**；业务表 **57 不变**；配置键 **143/132 不变**；**无迁移**（迁移 head 保持 M86 的 0041 或之后）。
- 参数校验错误统一 `TOOL_ARGS_INVALID` **422**（Pydantic `ValidationError` 经 `ToolRegistry.validate` 转换）；`plan_id` 不存在 → `UPGRADE_PLAN_NOT_FOUND`（v2 既有码）；handler 内不再抛 `INVALID_PARAMETER`。
- 用药口径：仓库没有每瓶药的到期时间，**只按 48 小时内过期口径的降级实现**——`medicine.counted_sanity` 恒为 0、`sanity["medicine"]` 恒为 0、天数不计任何药；`use_medicine` 只决定 `expiring_medicine` 为 999 或 0；`owned_sanity` 与 `max_days_saved` 只作参考。不得把仓库里的全部药折成天数。
- 报告产物：`kind="report"`、`mime="text/markdown"`、`body` 是 UTF-8 字节、`meta` **恰为 `{"farming": true}`**；`ArtifactService.put` 的正文参数是 `body: bytes`，没有 `create`。
- PRTS/企鹅物流/拆包**联网只用于 §7.2 的补充验证**，结果记台账、不作硬门禁；硬门禁全部用固定夹具：`tests/fixtures/prts/activity_zuori_shop.wikitext`（附录 A）+ 固定 `now=2026-10-20T12:00:00+08:00` + `tests/fixtures/gamedata/item_table_ap_supplies.json`。
- 不改 v2 养成计划口径：`start_upgrade_plan` 仍只看常驻关；`plan`/`plan_needs` 的对外字段、notes、候选口径不变；`refresh` 的 missing 仍只排除龙门币。
- 命令一律 `.venv/bin/python`；pytest 用 `-o addopts= -q`（`pytest.ini` 已带 `-q`，不再加 `-q`）；没有 `rg`，用 `grep -rn`；长输出重定向 `/tmp/m88-*.log`，只看退出码与尾部；提交信息 `<类型>(m88): <中文摘要>`，每个任务一个提交，不动 `docs/v3/12` 的 M88 行、`docs/v3/specs/README.md` 与 `specs/todo/README.md`（由主 agent 升格时统一改）。
- 单个任务里任何对外行为、范围或与 `docs/v3/13` 冲突的裁决，停下来问用户；纯实现细节的裁决记进台账「Rulings I made」。

## 开工基线（Task 0 实测）

- `dev` = M87 合并后的提交（`git rev-parse --short dev HEAD` 两行相同）；tag `v3-m87` 存在；迁移 head 为 M86 的 `0041` 或之后，M88 不新增迁移文件。
- 三份定义 `version` 实测应为 `planner 3`、`analyst 3`、`general 6`（M86/M87 没有改这三份）；`daily_ops`/`copilot` 为 4（M87），`diagnose` 4，`ops` 3。实测不同先停下来报告；T6 的 planner/analyst 用 3→4，general 用**实测值 +1**（M87 后为 6→7），不硬抄 7。
- 工具计数实测 `107 106`、`untrusted_output` 22、`refetchable` 51、评测场景 `19`；不符按 spec §0 停工报告。这些是 M87 合并后的预期；T5/T6/T7 的数字按 Task 0 实测 + 本任务增量写，不硬写 M87 前值。后端全量、`regression`/`capability` replay、`BROKEN 0` 的基线都记进台账，起点已有失败逐条列出。

## §0 前置检查与接缝裁决

spec §0 的表有 13 条（第 1–13 行），逐条跑，任何一条与期望不符就停下来报告；实测值抄进 Task 0 的台账提交。接缝裁决表随其后，实现按它取入口。

| # | 检查 | 命令 | 期望 |
|---|---|---|---|
| 1 | 分支与工作区 | `git status -sb` | 第一行 `## economist`，没有其他行 |
| 2 | M87 已合并 | `git tag -l 'v3-m87'` | `v3-m87`（仅编辑顺序依赖，M88 不调用 verifier/copilot） |
| 3 | v2 线性规划入口 | `grep -nE "def _linprog_call\|def _build_lp\|def _solve_allocation\|async def _solve\|async def plan_needs\|def _candidate_stages\|async def _load_rates\|async def _load_recipes" maa_api/services/account/planner.py` | 8 行 |
| 4 | scipy | `.venv/bin/python -c "from scipy.optimize import linprog; print('ok')"` | `ok` |
| 5 | 产物服务 | `grep -n "async def put\|async def update\|body: bytes" maa_api/agent/artifacts/service.py` | 至少 3 行；`put` 的第 4 个关键字参数是 `body: bytes` |
| 6 | 两个 Worker 定义 | `grep -rlE "^name: (planner\|analyst)$" maa_api/agent/specs/builtin/` | 2 个文件 |
| 7 | 拆包白名单 | `grep -n "GAMEDATA_FILES.*item_table.json" maa_api/services/external/sources.py` | 1 行 |
| 8 | 工具计数基线 | `.venv/bin/python -c "from maa_api.agent.tools import build_registry as b; r=b(); print(len(r.list()), sum(1 for d in r.list() if d.model_visible))"` | `107 106` |
| 9 | 活动页解析 | `grep -n "def parse_activity_page\|exchange_end_at" maa_api/services/prts_parse/activity.py` | 3 行 |
| 10 | M87 ticket 驱动 | `grep -n "drive_ticket" maa_api/agent/evals/runner.py` | 至少 1 行 |
| 11 | 评测场景数 | `ls tests/evals/scenarios/*.yaml \| wc -l` | `19` |
| 12 | 运行上下文带工单 | `grep -n "ticket_id: str" maa_api/agent/harness/context.py` | 至少 1 行（`RunContext.ticket_id`） |
| 13 | 本工具尚未存在 | `grep -rn "optimize_farming" maa_api \| head` | 无输出 |

**接缝裁决（实现按这张表取入口，不再另找）：**

| 接缝 | 做法 | 不做什么 |
|---|---|---|
| 产物写入 | `artifacts.put(kind="report", title=report_title(result), body=render_report(...).encode("utf-8"), mime="text/markdown", session_id=..., ticket_id=..., meta={"farming": True})`，`result["report_artifact_id"] = artifact.id`；没有装配、抛错或 `session_id` 为 None 时不写，抛错时 notes 加「报告没有保存成功」 | 不调 `create`（不存在），不新增端点 |
| 工单 | `require_run(kinds={"worker"}, message="optimize_farming 的报告只能由工单里的 Worker 写")` 取 `RunContext.ticket_id`；只在内置 Worker 且 `params.write_report` 为真时调用 | 不给 `ToolContext` 加 `ticket_id`，不从会话表反查 |
| 服务取用 | 先看 `app.state.farming_optimizer`；没有就 `default_farming_optimizer(state)` 并写回。state 取法：`CURRENT_RUN.get()` 非空时 `app_state(run, ctx)`，否则 `ctx.request.app.state` | 不在启动期装配，不在 `main.py` 赋值 |
| 默认装配 | `default_farming_optimizer(app_state)`：`planner = getattr(state, "planner_service", None) or default_upgrade_planner()`（仓库没有 `app.state.planner_service`）；`depot = getattr(state, "depot_service", None) or DepotService(state.session_factory)`；`plans = getattr(state, "upgrade_plan_service", None) or default_upgrade_plan_service()`；`activities = default_activity_service()`；`prts = activities.prts`；`gamedata = GamedataClient()`；`artifacts = getattr(state, "artifact_service", None)`；`app_state=None` 时 depot 用 `db_session.session_factory` | 不建 `app.state.activity_service`（M77 裁决） |
| LP 调用 | `import maa_api.services.account.planner as planner` 后 `planner._linprog_call(...)`；测试 monkeypatch 该属性 | 不直接 `from scipy...` |
| 取数形状 | `activities.current(detail="full")` 返回 `ActivityReport`（活动在 `.activities`，降级原因在 `.fallback_reason`）；`prts.wikitext([page])` 返回 `WikiTextBatch`（正文在 `.pages[0].content`，来源用 `.source_refs()`）；`gamedata.excel("item_table.json")` 返回 `GamedataData`（数据在 `.data`，来源用 `.source_ref()`） | 不假设 `activities.current()` 直接给列表 |
| 拆包来源 | `medicine_pool` 额外接可选 `sources: list[dict] \| None`，读表成功时 append `GamedataData.source_ref(title="item_table.json").as_dict()`；`MedicinePool` 字段不变 | 不给 `MedicinePool` 加 source 字段，不为了取来源重复调 `gamedata.excel` |
| 无商店时的代币名 | `parse_event_shop` 没有 `EventShopList` 时返回 `ParsedShop(token=None, goods=())`（spec §6 的 `test_page_without_shop` 钉住）；`event_shop.py` 另给 `regular_token(content) -> str \| None`，从关卡表里第一个「第二个位置参数为空」的 `{{关卡报酬\|<名>\|\|…\|n=N}}` 取代币名；`load_events` 用 `token = parsed.token or regular_token(content)`，取不到才丢弃该活动的关卡 | 不把代币名写进 `TokenDrop`，不改 `ParsedShop` 形状 |
| 无商店的商店小节 | 商店为空但关卡表有常规代币掉落时，活动关仍按 spec §4.5.2 进模型（有企鹅物流选项的带掉率，没有的新建 token-only），notes 写「活动「{name}」的页面没有商店小节，商店没有计入」；只有商店与代币掉落都为空时才不新建 token-only 关卡，记一句并跳过该活动（spec §4.5.8） | 不把活动判成失败 |
| 评测 | 场景 id 与文件名都用连字符（`SCENARIO_ID_RE` 不接受下划线）；`World` 加 `farming_optimizer: str \| None`，`environment.py` 在 `create_app()` 之后、`client.__enter__()` 之前读 `tests/evals/fixtures/<名>` 并设 `app.state.farming_optimizer = FakeFarmingOptimizer(payload)`；cassette 照 M87 口径：前两条是 dynamic-context 落点探查（应答 `{"text": "OK"}`），ready 结尾再加一条记忆抽取 `{"text": "{\"items\": []}"}` | 不联网、不写产物、不改 `main.py` |
| 时间 | `service.py` 模块级 `_now()` 返回 `datetime.now(timezone.utc).astimezone()`；测试注入固定 `datetime(2026, 10, 20, 12, 0, tzinfo=timezone(timedelta(hours=8)))` | 不在测试里读当天时间 |
| v2 服务名 | `planner.py` 加 `StageOption = _StageOption` 与 `Recipe = _Recipe` 两个公开别名；`_prepare` 与 `farming_inputs` 共用；server 归一抽成私有 `_server_name(server)`（`plan_needs` 与 `farming_inputs` 都调它） | 不改 `_Recipe`/`_StageOption` 字段 |
| 文档 | 02 §2.2/§4.1、03 §8.2、05 §3.2/§6.1 的 M88 行在升格时已预写；T8 只核对与 §11 一致、补 05 §6.1 的 M80 计数与 v2 纯重构句 | 不重复加行、不改 12/14 |

## Review Focus

1. **LP 数值确定性**：同一 `FarmingModel` 两次求解的 `runs/synth/buys/total_sanity/shortfall` 完全相同；矩阵行顺序固定为材料→代币→截止→预算；整数化后不超预算、不超代币、不超库存。T3 的 `test_matrix_row_order_matches_spec`、`test_integerization_respects_stock`、`test_integerization_respects_token_budget`、`test_integerization_respects_window_budget`；T4 的 `test_result_is_deterministic`。
2. **活动窗口与商店库存**：两个活动同日截止只加一次当前理智与自然恢复；`stock=None` 无上界；`z` 取整不超过库存；`exchange_end_at` 只展示不参与求解。T3 的 `test_cumulative_event_budget_caps_overlapping_events`、`test_stock_cap_is_respected`；T4 的 `test_cumulative_deadline_budget_does_not_duplicate_current_sanity`、`test_event_stage_merges_with_penguin_option`。
3. **用药口径不被放宽**：没有任何输入能把仓库药折成天数；`counted_sanity` 恒 0、`sanity["medicine"]` 恒 0、`expiring_medicine` 只由 `use_medicine` 决定 999/0，表读不到时为 0。T4 的 `test_medicine_pool_from_real_ap_supplies`、`test_medicine_counts_zero_without_expiry_data`、`test_missing_item_table_keeps_result`、`test_medicine_note_and_expiring_value`。
4. **v2 养成规划不回归**：`plan`/`plan_needs` 的 `as_dict()` 逐字段不变；`refresh` 仍只排除龙门币、`missing_needs` 保留作战记录；`_prepare` 的 notes 顺序不变。T1 的 `test_plan_needs_unchanged_after_refactor`、`test_missing_needs_keeps_battle_records`，加跑三个既有测试文件。
5. **评测假世界 seam**：`app.state.farming_optimizer` 优先、默认装配不触发；REST 调用 `report_artifact_id` 为 null；两个场景 replay 不联网。T5 的 `test_service_seam_is_used`、`test_rest_call_does_not_request_report`；T7 的两个场景与 `test_regression_replay.py` 的 21 个场景。

---

### Task 0: 分支、§0 前置检查、基线、接缝

**Files:**
- Create: `docs/v3/ledgers/m88-economist.md`（只写「开工基线」与「Rulings I made」骨架）

**Interfaces:**
- Consumes: `dev`（M87 合并后的提交）。
- Produces: 分支 `economist`、§0 实测表、基线数字、接缝确认结果；T1–T8 的起点。

- [ ] **Step 1: 从 dev 开分支并确认干净**

```bash
git checkout dev
git checkout -b economist
git status -sb
git rev-parse --short dev HEAD
git tag -l 'v3-m87'
```

Expected：第一行 `## economist`，没有其他行；两行 commit 相同；`v3-m87`。均记进台账。

- [ ] **Step 2: 记录迁移 head、版本与计数**

```bash
ls maa_api/db/migrations/versions/*.py | sort | tail -1
grep -n "^version:" maa_api/agent/specs/builtin/{planner,analyst,general,daily_ops,copilot,diagnose,ops}.md
.venv/bin/python -c "from maa_api.agent.tools import build_registry as b; r=b(); print(len(r.list()), sum(1 for d in r.list() if d.model_visible))"
.venv/bin/python -c "from tests.agent.test_tool_catalog import UNTRUSTED_OUTPUT_TOOLS as u, EXPECTED_REFETCHABLE as r; print(len(u), len(r))"
ls tests/evals/scenarios/*.yaml | wc -l
```

Expected：迁移文件 `0041_*` 或更晚；版本 `planner 3`、`analyst 3`、`general 6`、`daily_ops 4`、`copilot 4`、`diagnose 4`、`ops 3`；`107 106`；`22 51`；`19`。与期望不符就停下来报告；T5/T7 的数字按这里实测 + 本任务的增量（工具 +1、untrusted +1、refetchable +1、场景 +2）写，不要沿用 M87 前的值。

- [ ] **Step 3: 记录后端与评测基线**

```bash
mkdir -p /tmp/m88
.venv/bin/python -m pytest -o addopts= -q > /tmp/m88/base-pytest.log 2>&1; echo $?; tail -n 30 /tmp/m88/base-pytest.log
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m88/base-reg.log 2>&1; echo $?; tail -n 6 /tmp/m88/base-reg.log
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m88/base-cap.log 2>&1; echo $?; tail -n 6 /tmp/m88/base-cap.log
.venv/bin/python scripts/check_doc_links.py | tail -n 1
```

Expected：全量不新于 M87 台账基线（起点失败逐条列进台账）；两个 replay 末行给出 `x/y 通过` 与 `EVAL OK`（capability 有阈值时同时打印基线）；末行 `BROKEN 0`。

- [ ] **Step 4: 逐条跑 spec §0 的 13 条检查**

按本文「§0 前置检查与接缝裁决」的表逐条执行，把每条的实际输出与是否通过写进台账；第 5、8、10、11、12 条的实测值必须抄原文。

- [ ] **Step 5: 确认接缝签名**

```bash
sed -n '97,110p' maa_api/agent/artifacts/service.py
sed -n '181,200p' maa_api/agent/harness/context.py
grep -n "class ToolContext" -A 10 maa_api/agent/registry.py
```

Expected：`put` 的关键字参数含 `body: bytes`、`session_id`、`ticket_id`、`meta`；`RunContext` 有 `ticket_id: str | None`；`ToolContext` 只有 `session_id`（`_state` 从 `context.request.app.state` 取）。

- [ ] **Step 6: 建台账骨架并提交**

台账写「分支/基点/spec 链接/开工基线/§0 实测/接缝裁决」，其余小节留标题。

```bash
git add docs/v3/ledgers/m88-economist.md
git commit -m "docs(m88): 开工基线与接缝裁决"
```

---

### Task 1: 规划器重构与计划缺口（spec T1）

**Files:**
- Modify: `maa_api/services/account/planner.py`（`plan_needs` 约 1156–1222、`_solve` 约 1224–1300、`_Recipe` 405–415、`_StageOption` 416–424）
- Modify: `maa_api/services/account/plans.py`（`refresh` 431–455、`_REPORT_ONLY_NAMES` 64）
- Test: `tests/services/test_planner_farming_inputs.py`（新建）

**Interfaces:**
- Consumes: `_key`、`_is_report_only`、`REPORT_ONLY_NAMES`、`_load_recipes`、`_load_rates`、`_candidate_stages`、`settings_provider`、`UpgradePlanService._get_plan`。
- Produces: `StageOption = _StageOption`、`Recipe = _Recipe`、`FarmingInputs`（`needs/display/options/recipes/rate_status/notes/server`）、`_Prepared`（`recipes/meta/options/status`）、`UpgradePlanner._normalize_needs(needs, *, display) -> tuple[dict[str,int], list[str]]`、`UpgradePlanner._prepare(farm_needs, *, display, server_name, notes) -> _Prepared`、`UpgradePlanner.farming_inputs(needs, *, server=None, display=None) -> FarmingInputs`、私有 `_server_name(server)`、`UpgradePlanService._missing_from_plan(plan) -> dict[str,int]`、`UpgradePlanService.missing_needs(plan_id) -> dict[str,int]`。

- [ ] **Step 1: 用重构前的代码捕获金标准**

```bash
.venv/bin/python - <<'EOF' > /tmp/m88-t1-golden.txt 2>&1
import asyncio, json
from tests.services.test_upgrade_planner import TARGET, harness, stage_row

from maa_api.services.account import planner
planner.permanent_stage_ids = lambda: frozenset({"main_01-07", "main_04-04"})

async def main():
    h = harness(rates={TARGET: [stage_row("main_01-07", "1-7", TARGET, times=1000, quantity=1000, ap_cost=6)]})
    plan = await h.planner.plan_needs({TARGET: 10})
    print(json.dumps(plan.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))

asyncio.run(main())
EOF
cat /tmp/m88-t1-golden.txt
```

Expected：打印 `requirements`、`stages`、`total_sanity`、`days_estimated`、`notes`、`server` 等完整 `as_dict()`；把它原样粘进测试的 `GOLDEN` 常量。

- [ ] **Step 2: 写测试（先红）**

`tests/services/test_planner_farming_inputs.py` 的文件头 import `asyncio`、`functools`、`pytest`；从 `maa_api.services.account import planner` 取 `_key` 与 `permanent_stage_ids`，从 `tests.services.test_upgrade_planner` 复用 `TARGET`、`harness`、`stage_row`，从 `maa_api.domain.errors` 取 `AppError`/`ErrorCode`，从 `tests.services.test_upgrade_plan_lifecycle` 复用 `FakeDepot`、`FakePipelineService`、`FakePlanner`、`FakeScheduleService`、`plan_payload`、`service`。文件里带自己的 `async_test` 装饰器与 `@pytest.fixture(autouse=True)` 的 `permanent_stages`（`monkeypatch.setattr(planner, "permanent_stage_ids", lambda: frozenset({"main_01-07", "main_04-04"}))`）。用例：

- `test_plan_needs_unchanged_after_refactor`：`plan = await harness(...).planner.plan_needs({TARGET: 10})`，断言 `plan.as_dict() == GOLDEN`（GOLDEN 是 Step 1 的输出）。
- `test_farming_inputs_drops_report_only_items`：`h = harness(default_rates=True)`（`default_rates` 是 `harness` 的参数）；`inputs = await h.planner.farming_inputs({"龙门币": 30000, "作战记录": 5, TARGET: 10})`（真实签名是 `farming_inputs(needs, *, server=None, display=None)`，没有 `default_rates`），断言 `set(inputs.needs) == {planner._key(TARGET)}`、`inputs.needs[planner._key(TARGET)] == 10`、notes 含「只报需求」、`inputs.server == "CN"`。
- `test_farming_inputs_empty_after_report_only`：`await h.planner.farming_inputs({"龙门币": 1, "作战记录": 1})`，断言 `inputs.needs == {}`、`inputs.options == []`、`inputs.recipes == []`、notes 含「没有剩余需求」。
- `test_farming_inputs_rate_status_matches_plan_needs`：不给 TARGET 的掉率时，断言 `inputs.rate_status[planner._key(TARGET)]` 等于 `(await h.planner.plan_needs({TARGET: 10})).as_dict()["unobtainable"][0]["reason"]`，且含「企鹅物流」。
- `test_missing_needs_subtracts_depot`：`factory, handle = await service(planner=FakePlanner(plan=plan_payload(requirements={"固源岩组": 30})), depot=FakeDepot({"固源岩组": 12}), schedule=FakeScheduleService(), pipeline=FakePipelineService())`；`started = await handle.start(name="甲", targets=[{"operator": "甲"}], run_times=["08:00"])`；断言 `await handle.missing_needs(started["plan"]["id"]) == {"固源岩组": 18}`。
- `test_missing_needs_keeps_battle_records`：计划 requirements 为 `{"作战记录": 5}`、`FakeDepot({})`，断言 `await handle.missing_needs(plan_id) == {"作战记录": 5}`。
- `test_missing_needs_plan_not_found`：`pytest.raises(AppError)` 且 `exc.value.code is ErrorCode.UPGRADE_PLAN_NOT_FOUND`。

- [ ] **Step 3: 跑测试确认失败**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/services/test_planner_farming_inputs.py > /tmp/m88-t1-red.log 2>&1; echo $?; tail -n 8 /tmp/m88-t1-red.log
```

Expected：非 0；`AttributeError: 'UpgradePlanner' object has no attribute 'farming_inputs'` 或 `ImportError: cannot import name 'FarmingInputs'`。

- [ ] **Step 4: 重构 `planner.py`**

- 把 `plan_needs` 的归一段（`names` 循环、`farm_needs` 累加、report-only 剔除、空需求 note、`server_name` 计算）抽成 `_normalize_needs`：`display` 就地补全、返回 `(farm_needs, notes)`；空需求只写 note 不提前返回，提前返回留给 `plan_needs` 与 `farming_inputs` 各自做。`plan_needs` 改为调用它，字段与 notes 顺序不变。把 `_solve` 的「配方闭包 → universe → `_load_rates` → `_candidate_stages` → 补 status」段抽成 `_prepare`：返回 `_Prepared(recipes=recipes, meta=meta, options=options, status=status)`，`closure_notes`/`stage_notes` 就地追加到 `notes`、universe 键 `display.setdefault`；`_solve` 调用它后继续 `_supply_analysis`/`_solve_allocation`，其余不动。
- 加 `StageOption = _StageOption`、`Recipe = _Recipe`；`_server_name(server)` 供 `plan_needs` 与 `farming_inputs` 共用；`FarmingInputs`/`_Prepared` 按 spec §4.2 的字段与 `@dataclass(slots=True)` 写。
- 实现 `farming_inputs`：`server_name = self._server_name(server)`；`farm_needs, notes = self._normalize_needs(needs, display=names)`；空需求返回空 `FarmingInputs`；否则 `prepared = await self._prepare(...)`，返回 `FarmingInputs(needs=farm_needs, display=names, options=prepared.options, recipes=list(prepared.recipes.values()), rate_status=prepared.status, notes=notes, server=server_name)`。

- [ ] **Step 5: 改 `plans.py`**

把 `refresh` 的 `missing = {...}` 段替换为 `missing = await self._missing_from_plan(plan)`；`_missing_from_plan` 复制原推导（`requirements − depot`，只留 > 0，只排除 `_REPORT_ONLY_NAMES`，即龙门币）；`missing_needs` 用 `_get_plan` 取计划后调 `_missing_from_plan`。`_REPORT_ONLY_NAMES` 不动。

- [ ] **Step 6: 跑 T1 与 v2 回归**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/services/test_planner_farming_inputs.py > /tmp/m88-t1.log 2>&1; echo $?; tail -n 5 /tmp/m88-t1.log
.venv/bin/python -m pytest -o addopts= -q tests/services/test_upgrade_planner.py tests/services/test_upgrade_plans.py tests/services/test_upgrade_plan_lifecycle.py > /tmp/m88-t1-old.log 2>&1; echo $?; tail -n 5 /tmp/m88-t1-old.log
```

Expected：两个 `0`；新文件 7 passed；三个既有文件一条不改全部通过。

- [ ] **Step 7: 提交**

```bash
git add maa_api/services/account/planner.py maa_api/services/account/plans.py tests/services/test_planner_farming_inputs.py
git commit -m "refactor(m88): 抽出规划器公共取数与计划缺口方法"
```

---

### Task 2: 活动商店与代币掉落解析（spec T2）

**Files:**
- Create: `maa_api/services/prts_parse/event_shop.py`
- Create: `tests/fixtures/prts/activity_zuori_shop.wikitext`
- Modify: `tests/fixtures/prts/README.md`
- Test: `tests/services/test_event_shop_parse.py`（新建）

**Interfaces:**
- Consumes: `wikitext.find_templates`、`wikitext.parse_templates`、`wikitext.template_param`、`Template.arg`、`Template.positional`。
- Produces: `TokenDrop(stage_code, ap_cost, tokens)`、`ParsedShopGood(item, bundle, stock, price, section)`、`ParsedShop(token, goods)`、`parse_event_shop(content) -> ParsedShop`、`parse_token_drops(content, token) -> dict[str, TokenDrop]`、`regular_token(content) -> str \| None`；`event_shop.py` 不导入 `maa_api.services.economy`。

- [ ] **Step 1: 落夹具**

把 spec 附录 A 的整段 wikitext 原样写进 `tests/fixtures/prts/activity_zuori_shop.wikitext`（`{{活动信息}}` 七字段、`YW-1`–`YW-8` 与 `YW-EX-1` 两张关卡表、`==劳工配给所==` 的 `{{#invoke:EventShopList|list}}` 整段）。`tests/fixtures/prts/README.md` 的表格补一行：来源 `https://prts.wiki/w/昨日海?action=raw`（HTTP 200，15 684 字节）、抓取日期 `2026-11-09`、裁剪范围「活动信息七字段；主关 YW-1–YW-8 与 YW-EX-1 一行；商店整段原样；剪掉 YW-ST-*、其余 EX 关与突袭模式报酬其余行」。

- [ ] **Step 2: 写测试（先红）**

`tests/services/test_event_shop_parse.py` 读两份夹具（`activity_zuori_shop.wikitext` 与既有 `activity_vec03.wikitext`），用例与断言：

- `test_shop_token_and_sections`：`shop = parse_event_shop(content)`；`shop.token == "“未来瞻望镜”"`；分区集合含「金色阶段」「紫色阶段」「灰色阶段」「无限库存区」，不含「往期复刻」。
- `test_shop_items_stock_price_bundle`：`len(shop.goods) == 31`；逐项断言 `异铁块 (1, 10, 40, "金色阶段")`、`聚酸酯组 (1, 15, 8, "紫色阶段")`、`龙门币 (5000, 100, 7, "紫色阶段")`、`高级作战记录 (2, 25, 5, "紫色阶段")`、`家具零件 (10, 200, 2, "灰色阶段")`、`RMA70-12 (1, None, 40, "无限库存区")`、`模组数据块 (1, 2, 75, "金色阶段")`。`test_shop_skips_furniture_and_skins`：物品名集合不含「全域广播扬声器」「流星专属时装」，含「家具零件」。
- `test_token_drops_regular_only`：`drops = parse_token_drops(content, "“未来瞻望镜”")`；`drops["YW-1"] == TokenDrop("YW-1", 9, 9)`、`YW-6` 与 `YW-8` 都是 `(21, 21)`、`YW-3` 是 `(12, 12)`；`"YW-EX-1" not in drops`（附录 A）与 `"YW-ST-1" not in drops`（`activity_zuori.wikitext`）；`n=80` 的首掉不出现。
- `test_page_without_shop`：`parse_event_shop(activity_vec03 正文) == ParsedShop(token=None, goods=())`。
- `test_activity_page_deadline`：`parse_activity_page(content, page_title="昨日海")` 的 `end_at == datetime(2026,10,23,3,59, tzinfo=+08:00)`、`exchange_end_at == datetime(2026,10,30,3,59, tzinfo=+08:00)`。

- [ ] **Step 3: 跑测试确认失败**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/services/test_event_shop_parse.py > /tmp/m88-t2-red.log 2>&1; echo $?; tail -n 6 /tmp/m88-t2-red.log
```

Expected：非 0；`ModuleNotFoundError: No module named 'maa_api.services.prts_parse.event_shop'`。

- [ ] **Step 4: 实现 `event_shop.py`**

- `parse_event_shop`：`templates = find_templates(content, "#invoke:EventShopList", include_nested=False)`；没有就返回 `ParsedShop(None, ())`；`token = (template_param(t, "token_alias") or "").removeprefix("带框_")`，空串写 None；`data = template_param(t, "data") or ""` 按行切、按 `;;` 切字段、字段 `strip()`。
- 行首判定按 spec §4.5.7 的字段口径：`data` 按行切后先 `strip()`，空行跳过；第一个 `;;` 字段 `strip()` 后为 `X` → 分区标题行，分区名取第二个字段里 `{{` 之前的文本并 `strip()`（「往期复刻」「金色阶段」「紫色阶段」「灰色阶段」「无限库存区」）；其余非空行 → 商品行。`<标记>`（0/1）只表示游戏内兑换状态，解析结果不使用。
- 商品行：物品取 `{{道具图标|…}}` 或 `{{家具|…}}` 的位置参数 0（`Template.arg(0)`）；跳过判据看**物品字段**，不是整行 `startswith`（实测行首是 `0;;`/`1;;`，整行判定会漏掉家具与皮肤）：`{{家具|` 与 `{{皮肤头像|` 开头的字段跳过。`bundle` 从 `<small>'''×N'''</small>` 取（缺省 1）；`stock` 是整数或 `∞`（None）；`price` 是整数；首尾与空白都 `strip()`。
- `parse_token_drops`：按 `|-` 切行；同一行必须同时有 `{{关卡链接|<代号>}}` 与 `{{道具图标|理智|<数字>|`；`tokens` 取该行里 `{{关卡报酬|<token>||…|n=<数字>}}`（`Template.arg(1)` 为空串）的 `n`；没有常规代币掉落的行不收；不用正则数花括号。
- `regular_token`：按同样规则找第一个 `arg(1) == ""` 的 `{{关卡报酬|…}}`，返回其位置参数 0（没有就 None）。

- [ ] **Step 5: 跑测试并确认不联网**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/services/test_event_shop_parse.py > /tmp/m88-t2.log 2>&1; echo $?; tail -n 5 /tmp/m88-t2.log
```

Expected：`0`，6 passed；`grep -n "httpx\|external_http\|PrtsClient" maa_api/services/prts_parse/event_shop.py` 无输出。

- [ ] **Step 6: 提交**

```bash
git add maa_api/services/prts_parse/event_shop.py tests/fixtures/prts/activity_zuori_shop.wikitext tests/fixtures/prts/README.md tests/services/test_event_shop_parse.py
git commit -m "feat(m88): 活动商店与代币掉落解析"
```

---

### Task 3: 求解器（spec T3）

**Files:**
- Create: `maa_api/services/economy/__init__.py`、`model.py`、`solver.py`
- Create: `tests/services/economy/__init__.py`、`tests/services/economy/test_solver.py`

**Interfaces:**
- Consumes: `maa_api.services.prts_parse.event_shop` 的 `TokenDrop`、`ParsedShopGood`；`planner._linprog_call`；`planner._key`、`SANITY_PER_DAY`、`StageOption`、`Recipe`。
- Produces: `ShopGood`、`EventInfo`、`FarmingRequest`、`FarmingModel`、`MedicineItem`、`MedicinePool`（字段逐字照 spec §4.2）；`COVERAGE_TOL=1e-6`、`EPSILON=1e-3`、`SHORTFALL_TOL=0.5`；`FarmingSolution(runs/synth/buys/shortfall/total_sanity/status/notes)`、`LpMatrix(c/A_ub/b_ub/bounds/x_slice/y_slice/z_slice/u_slice/x_index/y_index/u_index/z_order/row_names)`、`build_matrix(model, *, budget)`、`solve_farming(model, *, budget=None)`；`economy/__init__.py` 导出 `FarmingOptimizer`、`FarmingRequest`、`FarmingModel`、`ShopGood`（`FarmingOptimizer` 在 T4 落地前用延迟导入，T3 只导出已有名字）。

- [ ] **Step 1: 写 `model.py` 与 `__init__.py`**

按 spec §4.2 的字段、`frozen`/`slots` 与默认值写数据类；`TokenDrop` 与 `ParsedShopGood` 从 `prts_parse.event_shop` 导入并在 `model.py` 再导出；`economy/__init__.py` 的 `__all__` 先写 `FarmingRequest`、`FarmingModel`、`ShopGood`、`MedicinePool`。

- [ ] **Step 2: 写求解器测试（先红）**

`tests/services/economy/test_solver.py` 的文件头 import `asyncio`、`functools`、`pytest`、`from datetime import datetime, timedelta, timezone`、`from maa_api.services.account import planner`、`from maa_api.services.economy.model import ...`、`from maa_api.services.economy.solver import ...`。写三个构造 helper：

```python
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone(timedelta(hours=8)))
WORD = "异铁块"

def _model(*, needs, options, recipes=(), shop=(), owned=(), deadlines=(), now=NOW):
    return FarmingModel(needs=dict(needs), depot={}, display={k: k for k in needs},
                        options=list(options), event_of={}, deadline_of={}, navigable_of={},
                        tokens_of={}, recipes=list(recipes), shop=list(shop),
                        owned_tokens=dict(owned), deadline_budgets=list(deadlines), now=now)

def _permanent(stage_id="main_01-07", key=WORD, rate=1.0, ap=30):
    return planner.StageOption(stage_id=stage_id, stage_code="1-7", ap_cost=ap, rates={key: rate},
                               details=[{"item_name": key, "sanity_per_item": ap / rate}])

def _event(stage_id="act52side_06", tokens=42, ap=21, key=WORD, rate=0.0):
    return planner.StageOption(stage_id=stage_id, stage_code="YW-6", ap_cost=ap, rates={key: rate})
```

`_model` 不含活动/代币字段，用例在构造后直接给 `model.event_of[...]`、`model.deadline_of[...]`、`model.navigable_of[...]`、`model.tokens_of[...]` 赋值（`FarmingModel` 非 frozen）。用例与断言：

- `test_matrix_row_order_matches_spec`：模型带一个商品（`shop` 一条、`owned` 有该代币）与一条 `deadlines`；`matrix = build_matrix(model, budget=240)`；`matrix.row_names[0] == "material:异铁块"`，随后是 `"token:…"`、`"deadline:…"`，最后一项 `"budget"`；`matrix.x_index == {"main_01-07": 0}`；`len(matrix.bounds) == len(matrix.c)`。
- `test_permanent_only_matches_v2_total`：一关 30 理智 1 件、需求 10、无商店；用 `planner._supply_analysis(needs=model.needs, depot={}, has_stage={WORD: True}, recipes=[])` 取 `rows` 后调 `planner._solve_allocation(...)` 记作 `v2`，断言 `solution.total_sanity == sum(r * s.ap_cost for r, s in zip(v2.runs, model.options))`。
- `test_shop_beats_farming_when_cheaper`：需求 10、商店 1 件=40 代币（10 份）、活动关 21 理智给 42 代币、常驻关 30 理智 1 件；断言 `solution.buys == (10,)`、`runs` 里活动关 10 次、常驻关 0 次、`total_sanity == 210`。
- `test_stock_cap_is_respected`：需求 15、库存 10、`owned={token: 400}`（够换 10 份）、无活动关；断言 `solution.buys == (10,)`、常驻关 5 次、`total_sanity == 150`。
- `test_token_row_binds`：没有活动关、`owned={token: 80}`、单价 40；断言 `solution.buys == (2,)`。
- `test_event_deadline_caps_event_runs`：`deadline_budgets=[(NOW + timedelta(days=1), 210.0)]`；活动关每次 21 理智；断言活动关次数 `<= 10` 且 `total_sanity <= 210`。
- `test_cumulative_event_budget_caps_overlapping_events`：两个活动关（各 30 理智）、同一天截止、`deadline_budgets=[(NOW + timedelta(days=1), 240.0)]`；断言两个活动关次数之和 `<= 8`。
- `test_budget_mode_spreads_coverage`：两种材料各需 10、预算 300（完整需 600）；断言两种材料的 `1 - shortfall / 需求` 都落在 `0.4–0.6`，且 `status is None`。
- `test_unobtainable_shows_as_shortfall`：没有任何关卡与商店；断言 `solution.shortfall == {"异铁块": 10}`、`solution.runs == ()`、`solution.status is None`。
- `test_integerization_respects_stock`：库存 2、需求 5；断言 `solution.buys[0] <= 2`、所有 `runs` 为 `int`。
- `test_integerization_respects_token_budget`：小数解会超代币；断言 `spent <= owned + earned`，`solution.notes` 里有「按代币削减」。
- `test_integerization_respects_window_budget`：预算 205、关卡 21 理智；断言 `sum(r * 21 for r in runs) <= 205`。
- `test_linprog_failure_is_reported`：`monkeypatch.setattr(planner, "_linprog_call", boom)`；断言 `solution.status` 非空且含「线性规划不可用」。

- [ ] **Step 3: 跑测试确认失败**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/services/economy/test_solver.py > /tmp/m88-t3-red.log 2>&1; echo $?; tail -n 6 /tmp/m88-t3-red.log
```

Expected：非 0；`ModuleNotFoundError: No module named 'maa_api.services.economy.solver'`。

- [ ] **Step 4: 实现 `build_matrix`**

变量顺序 `x`（`model.options` 顺序）→ `y`（`model.recipes` 顺序）→ `z`（`model.shop` 顺序）→ `u`（`d_i = max(need_i − depot_i, 0) > 0` 的键按 `_key` 排序）；`c` 是第一阶段系数（`u` 段 `1/max(d_i,1)`，其余 0）；行顺序固定材料行（`needs ∪ 配方产物 ∪ 配方原料`，键按 `_key` 排序）→ 代币行（代币名排序）→ 截止行（`deadline_budgets` 升序）→ 总预算行（`budget` 非空时最后一行）；`bounds` 的 `z` 上界取 `stock`（None → `None`）、`u` 上界取 `d_i`；行名写 spec §4.2 的四种前缀。

- [ ] **Step 5: 实现两阶段求解、整数化与修复**

按 spec §4.5.3 的 1–6 步：第一阶段 `min Σ u_i/max(d_i,1)`；第二阶段追加覆盖率行 `Σ u_i/max(d_i,1) ≤ U* + COVERAGE_TOL`、目标 `Σ ap_s·x_s + EPSILON·(Σ y + Σ z)`；`y/z` 向上取整后固定上下界再解一次，无解时沿用整数化前的 `x` 并写 note；`x` 最后向上取整；按预算/截止行（选 `ap_cost` 最大、同值 `stage_id` 字典序大）与代币行（`unit_sanity` 最低、null 最低、同值 `_key(item)` 字典序大）做削减；材料行不硬修复；重算 `shortfall`/`total_sanity`；`_linprog_call` 抛异常或第一阶段无解时写 `status` 与已削减的 notes。

- [ ] **Step 6: 跑测试并验确定性**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/services/economy/test_solver.py > /tmp/m88-t3.log 2>&1; echo $?; tail -n 5 /tmp/m88-t3.log
```

Expected：`0`，13 passed；同一模型重复求解的确定性由 `test_integerization_*` 与 T4 的 `test_result_is_deterministic` 覆盖。

- [ ] **Step 7: 提交**

```bash
git add maa_api/services/economy tests/services/economy
git commit -m "feat(m88): 刷图线性规划求解器"
```

---

### Task 4: 取数、合并、服务与报告（spec T4）

**Files:**
- Create: `maa_api/services/economy/data.py`、`report.py`、`service.py`
- Modify: `maa_api/services/economy/__init__.py`（导出 `FarmingOptimizer`）
- Create: `tests/fixtures/gamedata/item_table_ap_supplies.json`
- Modify: `tests/fixtures/gamedata/README.md`
- Test: `tests/services/economy/test_data.py`、`test_service.py`、`test_report.py`（新建）

**Interfaces:**
- Consumes: T1 的 `FarmingInputs`/`farming_inputs`/`missing_needs`；T2 的 `parse_event_shop`/`parse_token_drops`/`regular_token`/`TokenDrop`；T3 的 model 与 `solve_farming`；`ArtifactService.put`。
- Produces: `load_events(*, activities, prts, now) -> tuple[list[EventInfo], list[dict], list[str]]`、`read_depot(depot) -> tuple[dict[str,int], list[str]]`、`medicine_pool(*, gamedata, depot_counts, use_medicine, sources: list[dict] | None = None) -> MedicinePool`（`sources` 是可选出口，不改 `MedicinePool` 字段；读表成功时把拆包来源 append 进去）、`merge(inputs, *, depot, events, request, medicine, now) -> FarmingModel`、`FarmingOptimizer`（构造参数 `planner/depot/plans/activities/prts/gamedata/artifacts=None/now=_now`，方法 `optimize(request, *, write_report, session_id, ticket_id) -> dict`）、`default_farming_optimizer(app_state=None)`、`report_title(result) -> str`、`render_report(result, *, generated_at) -> str`。

- [ ] **Step 1: 落理智药夹具**

`tests/fixtures/gamedata/item_table_ap_supplies.json` 照 spec §4.5.1 的夹具内容写三个 `apSupplies` 与三个 `items`；`tests/fixtures/gamedata/README.md` 表格补一行：来源 `https://fastly.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master/zh_CN/gamedata/excel/item_table.json`、抓取日期 `2026-11-09`、裁剪「只留 `ap_supply_lt_60`/`lt_010`/`lt_100` 三条与对应 `items` 三条」。

- [ ] **Step 2: 写 `test_data.py`（先红）**

假对象：`FakePlanner`（`farming_inputs` 返回给定 `FarmingInputs`）、`FakeActivities`（`current(detail=...)` 返回 `ActivityReport`）、`FakePrts`（`wikitext(titles)` 返回带 `pages[0].content` 与 `source_refs()` 的对象）、`FakeGamedata`（`excel` 返回 `GamedataData` 或抛错）、`FakeDepot`（`list_items`）。`NOW` 与固定时间同 T3，活动页用附录 A 夹具正文。用例与断言：

- `test_event_stage_merges_with_penguin_option`：附录 A 的 YW-1 在 `inputs.options` 里已有 `stage_code="YW-1"` 的选项；断言合并后的活动关 `tokens_of[stage_id] == ("“未来瞻望镜”", 9)`、`event_of` 记活动页标题、YW-EX-1 不产生 token-only 选项。
- `test_unnavigable_event_is_excluded_by_default`：`navigation="missing"`、`include_unnavigable=False`；断言这些 `stage_id` 不在 `model.options`，notes 含「MAA 还不能自动导航」。
- `test_include_unnavigable_adds_event_stages`：同一活动 `include_unnavigable=True`；断言关卡进模型、`navigable_of[stage_id] is False`。
- `test_cumulative_deadline_budget_does_not_duplicate_current_sanity`：两个活动同一天 `end_at`、`current_sanity=120`、`window_days=None`；断言 `len(model.deadline_budgets) == 1` 且预算等于 `120 + days × 240`（当前理智只加一次，用 120 才能看出是否重复）。
- `test_medicine_pool_from_real_ap_supplies`：夹具 + 仓库 `{"应急理智合剂": 3, "应急理智小样": 2}`；断言 `owned_sanity == 200`、`counted_sanity == 0`、`scope == "no_expiry_data"`、`items` 两条；`use_medicine=True` 时 `expiring_medicine == 999`、`False` 时为 0（`test_medicine_counts_zero_without_expiry_data` 单列这条断言）。
- `test_missing_item_table_keeps_result`：`gamedata.excel` 抛 `AppError`；断言 `items == []`、`owned_sanity == 0`、`expiring_medicine == 0`、`note` 含「没有读到理智药的恢复量」，且传给 `medicine_pool` 的 `sources` 仍为空。`test_fallback_reason_is_noted`：`ActivityReport(activities=(), fallback_reason="PRTS 不可用")`，断言 `load_events` 返回空活动、notes 含「当期活动没有取到」。
- `test_medicine_pool_adds_gamedata_source`：`sources = []` 传给 `medicine_pool`；断言新增一条 `kind == "gamedata"`、`title == "item_table.json"`、`fetched_at` 等于 `FakeGamedata` 的固定时间；表抛错时 `sources == []`（与上一条共用断言）。
- `test_event_without_shop_keeps_stages_and_notes`：用既有 `activity_zuori.wikitext`（`==劳工配给所==` 为空、无 `EventShopList`，关卡表有常规代币）；`load_events` 后断言活动仍在、`shop == ()`、`drops["YW-1"] == TokenDrop("YW-1", 9, 9)`、notes 含「没有商店小节」；`merge` 后 `model.shop == []`，且 `model.options` 里有 YW-1 的活动关（有企鹅选项则带掉率，没有则 token-only）——锁住 spec §4.5.1「商店可选、活动关仍可进模型」。

- [ ] **Step 3: 写 `test_service.py`（先红）**

`FakeOptimizerDeps` 组装 `FarmingOptimizer`：假 `planner.farming_inputs`（返回固定 `FarmingInputs`）、假 `plans.missing_needs`、假 `activities`、假 `prts`、假 `gamedata`（`source_ref` 的 `fetched_at` 用 `NOW` 固定）、假仓库、假 `artifacts`（`put` 记录 kwargs 并返回 `SimpleNamespace(id="artifact-1")`）、`now=lambda: NOW`。用例与断言：

- `test_complete_within_window`：`window_days=10`；断言 `status == "complete"`、`fits_window is True`、`days_estimated == days_estimated_full`。
- `test_partial_when_window_too_short`：`window_days=2`、完整需 10 天；断言 `status == "partial"`、`fits_window is False`、`days_estimated_full > 2 >= days_estimated`。
- `test_plan_id_uses_missing_needs_without_depot`：`plan_id="p1"`、仓库塞满该材料；断言 `requirements` 仍等于 `missing_needs` 的缺口（不因仓库减少），`plans.missing_needs` 只调一次。
- `test_plan_complete_returns_empty_solution`：`plans.missing_needs` 返回 `{}`、`include_events=False`；断言 `status == "complete"`、`stages == []`、`shop == []`、`explanation == "没有剩余需求：不需要刷关"`、notes 含「没有剩余需求」（spec §4.5.8 的完成分支）。
- `test_use_depot_false_keeps_depot_empty_but_reads_medicine`：仓库有 10 个异铁块与 3 瓶合剂、`materials={"异铁块": 20}`、`use_depot=False`；断言 notes 含「use_depot=False：没有用仓库库存抵扣」、需求按 20 个算且总理智不被 10 个库存抵扣、`medicine["items"]` 仍列出合剂（用药池照读仓库，spec §4.5.1）。
- `test_medicine_note_and_expiring_value`：`use_medicine=True` → `999` 与 note 含「expiring_medicine 设为 999」；`False` → `0` 与 note 含「没有用药」。
- `test_result_is_deterministic`：同一输入连跑两次，断言 `json.dumps` 两次相同（`sources` 里的 `fetched_at` 由假对象固定）。
- `test_include_events_false_skips_events`：断言 `activities.current` 未被调用、`sources` 里没有 `prts`。
- `test_sources_include_penguin_and_gamedata`：`include_events=False`、拆包表可读；断言 `{s["kind"] for s in sources} == {"penguin", "gamedata"}`（企鹅来源始终在；拆包来源的 `title == "item_table.json"`、`fetched_at` 等于固定时间），且没有 `prts`。与 `test_result_shape_covers_sources_and_medicine` 一起覆盖 penguin/PRTS/gamedata 三种来源。
- `test_result_shape_covers_sources_and_medicine`：`set(coverage) == set(requirements)`、`{"penguin", "prts", "gamedata"} <= {s["kind"] for s in sources}`、`sanity["medicine"] == 0`、`medicine["counted_sanity"] == 0`。
- `test_report_failure_keeps_result`：`artifacts.put` 抛 `RuntimeError`；断言 `report_artifact_id is None`、notes 含「报告没有保存成功」。
- `test_report_meta_is_farming`：`write_report=True`、`session_id="s-1"`、`ticket_id="t-1"`；断言 `put` 的 `kind == "report"`、`mime == "text/markdown"`、`body` 是 `bytes`、`meta == {"farming": True}`、`result["report_artifact_id"] == "artifact-1"`。

- [ ] **Step 4: 写 `test_report.py`（先红）**

- `test_report_sections_and_no_raw_json`：结果里同时有关卡与商店；断言正文含「刷图清单」「商店兑换」，且不含 `{"`。
- `test_empty_sections_are_omitted`：没有商店与合成，断言不含「商店兑换」「合成」。`test_empty_needs_title`：`report_title({"requirements": {}}) == "刷图方案：没有剩余需求"`；另断言一种材料时是「刷图方案：<名>」、两种时是「刷图方案：<首个> 与另外 1 种材料」。

- [ ] **Step 5: 跑三个文件确认失败**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/services/economy/test_data.py tests/services/economy/test_service.py tests/services/economy/test_report.py > /tmp/m88-t4-red.log 2>&1; echo $?; tail -n 8 /tmp/m88-t4-red.log
```

Expected：非 0；`ModuleNotFoundError`（`data`/`service`/`report` 任一）。

- [ ] **Step 6: 实现 `data.py`**

`load_events`：`report = await activities.current(detail="full")`；`fallback_reason` 非空时写 note 并返回空；每个 `CurrentActivity` 只在 `end_at is None or end_at > now` 时处理；`batch = await prts.wikitext([activity.page])`，页面缺失（`.missing`）或正文为空时跳过并写 note；`parsed = parse_activity_page(content, page_title=page.title)`；`token = parsed.token or regular_token(content)`；`drops = parse_token_drops(content, token)`（token 为 None 时 `{}`）；`shop = parse_event_shop(content)`；商店为空但 `drops` 非空时写 note「活动「{name}」的页面没有商店小节，商店没有计入」并保留活动；`drops` 也为空时跳过该活动并写「活动「{name}」的页面没有商店小节，也没有常规代币掉落，活动没有计入」（spec §4.5.8）；`navigable = activity.navigation == "available"`；`stage_ids = {stage.code: stage.stage_id for stage in activity.stages if stage.stage_id}`；来源用 `batch.source_refs()` 的 `as_dict()`。`read_depot` 照 `_depot_counts` 的键口径（`item_id` 与 `name` 都建索引），失败时返回 `{}` 并写 note「仓库数据读取失败，库存按 0 计」。`medicine_pool` 按 spec §4.5.1 的 `apSupplies`+`items` 口径，`items` 按名字排序，`note`/`expiring_medicine` 按 `use_medicine` 与表可用性给；读表成功时把 `GamedataData.source_ref(title="item_table.json").as_dict()` append 到可选出参 `sources`，失败时不 append。`merge` 按 spec §4.5.2 复制/新建活动关、筛商店（需求或配方涉及的材料键集合，龙门币与作战记录不收）、取 `unit_sanity` 最小值、按 `horizon` 分组 `deadline_budgets`（含 `medicine.counted_sanity`）。

- [ ] **Step 7: 实现 `service.py`**

`optimize` 按 spec §4.5.1 的取数顺序（`depot_counts` 只读一次；`plan_id` 用 `missing_needs` 且 `depot={}`；`use_depot=False` 时 `depot={}` 并在 notes 写「use_depot=False：没有用仓库库存抵扣」）→ `merge` → `solve_farming(model)` → 天数/用药/窗口二次求解 → 结果装配（`status`、`fits_window`、`requirements` 显示名、`stages`、`synthesis`、`shop`、`tokens`、`sanity`、`days_estimated`、`days_estimated_full`、`medicine`、`coverage`、`unobtainable`、`notes`、`explanation`、`sources`、`report_artifact_id`）→ 报告写入；`sources` 按「始终一条 `{"kind": "penguin", "label": "企鹅物流"}` + `load_events` 的 PRTS 行 + `medicine_pool` 追加的拆包行」拼；`plan_id` 的 `missing_needs` 为空时照常装配 `status="complete"`、空 `stages`/`shop` 与固定 `explanation`；`medicine.max_days_saved` 在 `optimize` 里按 `min(owned_sanity, max(needed - current, 0)) / 240` 保留一位小数；`explanation` 按 spec §4.5.5 的模板拼、空需求固定「没有剩余需求：不需要刷关」；模块级 `_now()`；`default_farming_optimizer` 按「接缝裁决」表的装配表实现。

- [ ] **Step 8: 实现 `report.py`**

`report_title` 按「空需求 / 一种 / 多种」三种规则并截到 120 字；`render_report` 按 spec §4.5.6 的 Markdown 段落，没有数据的小节整节省略，数据来源三行只在对应来源存在时写；`generated_at` 用 `%Y-%m-%d %H:%M`。

- [ ] **Step 9: 跑 T4 测试与确定性复跑**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/services/economy > /tmp/m88-t4.log 2>&1; echo $?; tail -n 5 /tmp/m88-t4.log
```

Expected：`0`；`test_service.py` 的 12 条里 `test_result_is_deterministic` 连跑两次相等；其余条数按 log 统计，失败就按 log 修正、不删断言。

- [ ] **Step 10: 提交**

```bash
git add maa_api/services/economy tests/fixtures/gamedata/item_table_ap_supplies.json tests/fixtures/gamedata/README.md tests/services/economy
git commit -m "feat(m88): 刷图取数、合并、服务与报告"
```

---

### Task 5: 工具与计数（spec T5）

**Files:**
- Create: `maa_api/agent/tools/economy.py`
- Modify: `maa_api/agent/tools/__init__.py`（`build_registry` 里 `register_account_tools` 之后）
- Test: `tests/agent/test_economy_tool.py`（新建）；Modify: `tests/agent/test_tool_catalog.py`、`tests/agent/specs/test_agent_specs.py`

**Interfaces:**
- Consumes: `FarmingRequest`、`default_farming_optimizer`、`RunContext`/`CURRENT_RUN`/`require_run`、`app_state`、`ToolRegistry`/`ToolContext`/`ToolRisk`、`CallerType`。
- Produces: `OptimizeFarmingParams`（字段与约束逐字照 spec §4.3.1，含 `server: str | None = Field(default=None, min_length=1, max_length=16)` 与 `_exactly_one` 校验器）、`OPTIMIZE_FARMING_DESCRIPTION`（spec §4.3.1 的描述原文）、`optimize_farming(params, ctx)`、`register_tools(registry)`；注册为 `group="economy"`、`risk=ToolRisk.SAFE`、`device="none"`、`read_only=True`、`refetchable=True`、`untrusted_output=True`、`rest_visible=True`。

- [ ] **Step 1: 写工具测试（先红）**

`tests/agent/test_economy_tool.py` 用 `build_registry().get("optimize_farming")` 取定义；`_context(caller, *, state=None, run=None)` 造 `ToolContext`（REST 用 `SimpleNamespace(app=SimpleNamespace(state=state))`，INTERNAL 用 `test_plans.py` 的 `RunContext` + `_current_run` 模式，`run.app` 带 state）。用例与断言：

- `test_tool_registered_as_safe_economy_tool`：`group == "economy"`、`risk.value == "SAFE"`、`device == "none"`、`rest_visible is True`、`untrusted_output is True`、`refetchable is True`、`model_visible is True`；描述含「活动商店」与「不提交任务」。
- `test_exactly_one_of_materials_or_plan`：给两个、都不给、`materials={}` 三种都 `pytest.raises(AppError)`，`code is ErrorCode.TOOL_ARGS_INVALID`、`status` 由既有异常处理器映射 422（断言 details 里有 `issues`）；`materials={"异铁块": 0}` 也拒。
- `test_server_is_forwarded`：假 optimizer 记录 `request`；`server="JP"` → `request.server == "JP"`；`test_include_flags_forwarded` 断言 `include_events=False`、`include_unnavigable=True`、`use_depot=False`、`owned_tokens={"“未来瞻望镜”": 30}` 原样进 `FarmingRequest`。
- `test_rest_call_does_not_request_report`：REST + `write_report=True`；断言 `write_report is False`、`session_id is None`、`ticket_id is None`，返回的 `report_artifact_id is None`。
- `test_internal_call_passes_ticket_to_optimizer`：`CURRENT_RUN` 的 `spec=builtin_spec("planner")`、`session_id="s-1"`、`ticket_id="t-1"`；断言 `write_report is True`、`ticket_id == "t-1"`、`session_id == "s-1"`。
- `test_service_seam_is_used`：`monkeypatch.setattr("maa_api.agent.tools.economy.default_farming_optimizer", boom)`；`state.farming_optimizer = FakeOptimizer()` 时正常返回，`boom` 未被调用。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/test_economy_tool.py > /tmp/m88-t5-red.log 2>&1; echo $?; tail -n 6 /tmp/m88-t5-red.log
```

Expected：非 0；`ModuleNotFoundError: No module named 'maa_api.agent.tools.economy'` 或 `get()` 返回 None 后的 `AttributeError`。

- [ ] **Step 3: 实现 `tools/economy.py`**

参数模型与描述照抄 spec；handler 先按「接缝裁决」取 state 与 optimizer，再算报告门控（`ctx.caller is CallerType.INTERNAL`、`CURRENT_RUN` 非空、`run.spec.kind == "worker"`、`params.write_report` 四者同时为真才 `write_report=True`，并那时才 `require_run`）；`session_id = run.session_id or ctx.session_id`、`ticket_id = run.ticket_id`；构造 `FarmingRequest` 调 `optimizer.optimize` 并原样返回。

- [ ] **Step 4: 注册并更新计数**

`build_registry()` 在 `register_account_tools` 之后加 `register_economy_tools`（导入写在函数内）；`EXPECTED_TOOLS` 加 `optimize_farming`；`UNTRUSTED_OUTPUT_TOOLS` 加它、`assert len(...) == 23`；`EXPECTED_REFETCHABLE` 加它、`assert len(...) == 52`；两处总数断言改 `108` / `107`；`tests/agent/specs/test_agent_specs.py` 加 `M88_TOOLS = frozenset({"optimize_farming"})`、并进排除集、`len(definitions) == 108`。

- [ ] **Step 5: 跑工具、目录与 spec 测试**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/test_economy_tool.py tests/agent/test_tool_catalog.py tests/agent/specs/test_agent_specs.py > /tmp/m88-t5.log 2>&1; echo $?; tail -n 5 /tmp/m88-t5.log
.venv/bin/python -c "from maa_api.agent.tools import build_registry as b; r=b(); print(len(r.list()), sum(1 for d in r.list() if d.model_visible))"
```

Expected：`0`；最后一条打印 `108 107`。

- [ ] **Step 6: 提交**

```bash
git add maa_api/agent/tools/economy.py maa_api/agent/tools/__init__.py tests/agent/test_economy_tool.py tests/agent/test_tool_catalog.py tests/agent/specs/test_agent_specs.py
git commit -m "feat(m88): optimize_farming 工具与计数"
```

---

### Task 6: 三份代理定义（spec T6）

**Files:**
- Modify: `maa_api/agent/specs/builtin/planner.md`、`analyst.md`、`general.md`
- Test: `tests/agent/test_builtin_workers.py`、`tests/agent/specs/test_m70_specs.py`、`tests/api/test_agent_specs_router.py`

**Interfaces:**
- Consumes: T5 注册的工具名 `optimize_farming`。
- Produces: 三份定义 `version` 为 planner 4、analyst 4、general 7（general 以 Task 0 实测值 +1；M87 后为 6→7）；`tools` 末尾各追加一项；正文各追加 spec §4.7 的段落（逐字）。

- [ ] **Step 1: 先改测试（红）**

`test_builtin_workers.py`：`WORKERS` 的 `version` 字段与断言已由 M87 建好（`daily_ops 4`、`copilot 4`、`planner 3`、`diagnose 4`、`analyst 3`、`ops 3`），**只把** `WORKERS["planner"]["version"]` 与 `WORKERS["analyst"]["version"]` 改成 4，并在这两项的 `tools` 元组末尾各加 `"optimize_farming"`；不回退 M87 已改的 `daily_ops`/`copilot`，也不重写整张版本映射。`test_m70_specs.py`：加 `ECONOMY_TOOLS = frozenset({"optimize_farming"})`，并入 general 期望集合的并集。`test_agent_specs_router.py`：`EXPECTED_VERSIONS` 只改 `analyst: 4`、`general: 7`、`planner: 4`（general 7 以 Task 0 实测 6 为基准）。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/test_builtin_workers.py tests/agent/specs/test_m70_specs.py tests/api/test_agent_specs_router.py > /tmp/m88-t6-red.log 2>&1; echo $?; tail -n 8 /tmp/m88-t6-red.log
```

Expected：非 0；planner/analyst 的工具元组不匹配，或 `assert 3 == 4` 的版本断言失败。

- [ ] **Step 3: 改三份定义**

只改 frontmatter 的 `tools` 与 `version`，其余字段逐字保留；`planner.md` 在 `read_skill_file` 后加 `optimize_farming`、`version` 3→4、正文末尾追加「## 刷图方案」六条；`analyst.md` 同样在 `read_skill_file` 后加、3→4、追加「## 资源账」三条；`general.md` 在 `remember` 之后加、`version` 按 Task 0 实测值 +1（M87 后 6→7）、追加「## 刷图方案」四条。三段正文逐字抄 spec §4.7。

- [ ] **Step 4: 跑测试**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/test_builtin_workers.py tests/agent/specs/test_m70_specs.py tests/api/test_agent_specs_router.py > /tmp/m88-t6.log 2>&1; echo $?; tail -n 5 /tmp/m88-t6.log
grep -n "^version:" maa_api/agent/specs/builtin/{planner,analyst,general}.md
```

Expected：`0`；版本三行 `4`、`4`、`7`（general 若 Task 0 实测不是 6，则按「实测 +1」并把偏差记进台账）。

- [ ] **Step 5: 提交**

```bash
git add maa_api/agent/specs/builtin/planner.md maa_api/agent/specs/builtin/analyst.md maa_api/agent/specs/builtin/general.md tests/agent/test_builtin_workers.py tests/agent/specs/test_m70_specs.py tests/api/test_agent_specs_router.py
git commit -m "feat(m88): 三份代理定义接入 optimize_farming"
```

---

### Task 7: 评测 seam 与两个场景（spec T7）

**Files:**
- Modify: `maa_api/agent/evals/scenario.py`（`World` 加 `farming_optimizer: str | None = None`）
- Modify: `tests/evals/environment.py`（`FakeFarmingOptimizer` + 注入）
- Create: `tests/evals/scenarios/m88-planner-week.yaml`、`m88-analyst-shop.yaml`
- Create: `tests/evals/cassettes/m88-planner-week.jsonl`、`m88-analyst-shop.jsonl`
- Create: `tests/evals/fixtures/m88-farming-week.json`、`m88-farming-shop.json`
- Modify: `tests/evals/test_regression_replay.py`（`len(scenarios)`/`len(ids)` 19→21）

**Interfaces:**
- Consumes: T5 的 `app.state.farming_optimizer` seam；M87 的 `drive_ticket`；`entry.kind="ticket"`。
- Produces: `World.farming_optimizer`；`FakeFarmingOptimizer.optimize(request, *, write_report, session_id, ticket_id)` 返回 `copy.deepcopy(payload)`；两个 `suite: "capability"` 场景与 cassette。

- [ ] **Step 1: 写两个 JSON 夹具**

`m88-farming-week.json`：`status="complete"`、`fits_window=true`、`requirements={"异铁块": 20, "聚酸酯组": 10}`、`stages` 两条（`act52side_06`/`YW-6` 38 次、`act52side_03`/`YW-3` 10 次）、`shop` 一条（异铁块 10 份 = 400 代币）、`tokens` 一行（`earned=798`、`spent=400`、`left=398`）、`sanity{"needed": 918, "current": 0, "medicine": 0, "natural_per_day": 240, "window_days": 7}`、`days_estimated=3.9`、`days_estimated_full=3.9`、`coverage` 两项 1.0、`unobtainable=[]`、`notes`、`explanation` 含「天」「异铁块」、`sources` 三项、`report_artifact_id=None`。`m88-farming-shop.json`：`status="complete"`、`window_days` 不传、`shop` 两条按 `value_sanity` 降序、`explanation` 含「商店」与「异铁块」。

- [ ] **Step 2: 写两个场景与 cassette**

场景字段照既有能力场景：`id`/`title`/`suite: "capability"`/`agent`/`source: "M88 spec §5 T7"`/`tags`/`entry`/`world`/`expect`/`limits`。`m88-planner-week`：`agent: "planner"`，`entry: {"kind": "ticket", "worker": "planner", "title": "本周材料", "brief": "这周想凑 20 个异铁块和 10 个聚酸酯组，来得及吗", "scope": "用 optimize_farming 算这周来不来得及"}`，`world: {"farming_optimizer": "m88-farming-week.json"}`，期望 `tool_sequence` 的 `steps` 为 `{"tool": "optimize_farming", "args": {"materials": {"异铁块": 20, "聚酸酯组": 10}, "window_days": 7}}` 与 `{"tool": "report", "args": {"status": "ready", "summary": {"re": "(?s)(天.*异铁块|异铁块.*天)"}}}`，`forbid: ["start_upgrade_plan"]`，`max_calls: {"optimize_farming": 1}`；`limits` 取 `timeout_seconds: 120`、`max_model_calls: 10`（比 M87 ticket 场景的 60/30 留更多超时余量，本场景实际只需 5 次模型调用）。`m88-analyst-shop`：`agent: "analyst"`，`title: "活动商店兑换"`，brief「这期活动商店先换什么？我想补 10 个异铁块和 15 个聚酸酯组」，scope「用 optimize_farming 算商店先换什么」；`optimize_farming` 的 `args` 只给 `materials`；`report` 的 `summary` 用 `{"re": "(?s)(商店.*异铁块|异铁块.*商店)"}`；`forbid: ["submit_pipeline"]`。

cassette 照 M87 口径：首行 `header`（`source: "handwritten"`、`recorded_at`、`model: "eval-model"`、`agent` 与场景一致），随后依次是 2 条落点探查 `{"text": "OK"}`、`optimize_farming` 的 tool_call、`report(status="ready")` 的 tool_call，最后一条是记忆抽取 `{"text": "{\"items\": []}"}`（`ready` 结束不再加终结 text；M87 维护性补丁的 cassette 口径就是「探查 ×2 → 实质调用 → 记忆抽取」）；每个 `tool_calls[*].id` 与下一次请求里的 tool result id 一致。

- [ ] **Step 3: 跑场景加载确认失败**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_regression_replay.py -k "loads" > /tmp/m88-t7-red.log 2>&1; echo $?; tail -n 6 /tmp/m88-t7-red.log
```

Expected：非 0；`World` 的 `farming_optimizer` 未知字段（`Extra inputs are not permitted`）——这是 red 证据。

- [ ] **Step 4: 改 `scenario.py` 与 `environment.py`**

`World` 加 `farming_optimizer: str | None = None`（受 `extra="forbid"` 约束，写错键在加载期报错）；`tests/evals/environment.py` 加 `class FakeFarmingOptimizer`（`__init__(payload)` + `async def optimize(...)` 返回 `copy.deepcopy(self._payload)`）；在 `create_app()` 之后、`for_each fake_core` 之前，若 `scenario.world.farming_optimizer` 非空则 `payload = json.loads(_read_fixture(fixtures_dir, name))` 并 `app.state.farming_optimizer = FakeFarmingOptimizer(payload)`。

- [ ] **Step 5: 改场景计数并跑两个场景**

`tests/evals/test_regression_replay.py` 的两处 `len(...) == 19` 改成 `21`（Task 0 实测 M87 后为 19：M86 的 13 + M87 的 6；若 Task 0 实测不是 19，按「实测 +2」写并记台账）。

```bash
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_regression_replay.py > /tmp/m88-t7.log 2>&1; echo $?; tail -n 6 /tmp/m88-t7.log
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m88-cap.log 2>&1; echo $?; tail -n 10 /tmp/m88-cap.log
```

Expected：`0`，21 个场景都加载且有 cassette；capability `skipped` 为 0、通过率不低于 M87 台账基线；日志里两个 M88 场景 `passed`。

- [ ] **Step 6: 提交**

```bash
git add maa_api/agent/evals/scenario.py tests/evals/environment.py tests/evals/scenarios/m88-*.yaml tests/evals/cassettes/m88-*.jsonl tests/evals/fixtures/m88-farming-*.json tests/evals/test_regression_replay.py
git commit -m "test(m88): 两个刷图评测场景与假世界 seam"
```

---

### Task 8: 文档同步、验收、台账与合并（spec T8）

**Files:**
- Modify: `docs/v3/05-运行时与代理定义.md`（§6.1 的 M80 行、v2 纯重构句）、`docs/v3/02-系统架构.md`、`docs/v3/03-数据模型.md`（核对）
- Modify: `docs/v3/13-决策记录.md`（§8 追加一条）
- Modify: `docs/v3/ledgers/m88-economist.md`（T0 的骨架补全）

**Interfaces:**
- Consumes: T1–T7 的全部改动。
- Produces: 与 spec §11 一致的契约行；13 §8 的实施记录；台账；`v3-m88` tag。

- [ ] **Step 1: 核对并补齐文档**

逐行核对（已预写，缺的补、错的改）：02 §2.2 的 `FarmingOptimizer` 行（`maa_api/services/economy/service.py`、M88）与 §4.1 的 `economy/` 子树、`prts_parse/event_shop.py`；03 §8.2 的 `artifact.meta` 行含「刷图报告写 `farming=true`（M88）」；05 §3.2 的 `general`/`planner`/`analyst` 三行工具列末尾有 `optimize_farming`(M88)（版本的事实来源是文件 frontmatter，T6 已改，05 不另记版本号）；05 §6.1 的 `optimize_farming` 契约段（参数、返回字段、`TOOL_ARGS_INVALID` 422、`UPGRADE_PLAN_NOT_FOUND`）。再改三处：05 §6.1 的计数句按 Task 0 实测的 M87 后 `107/106` 写成「当前 108 个、模型可见 107 个」（不要从该节旧文里的 104/103 起算，`108/107` 与 T5 的断言一致）；M80 行的「22 个」→「23 个」（按 Task 0 实测的 `untrusted_output` 数 +1）；补一句 v2 纯重构——`UpgradePlanner.farming_inputs` 与 `UpgradePlanService.missing_needs` 是本里程碑新增的公开方法，复用配方闭包/掉率/候选关，`plan`/`plan_needs`/`refresh` 的对外行为不变。

- [ ] **Step 2: 13 §8 追加实施记录**

读 `docs/v3/13-决策记录.md` 末尾最后一个 `### 8.N`，紧接着写 `### 8.<N+1> 资源策略师落地（M88，<实施日期>）`：用药降级口径（仓库无到期时间 → `counted_sanity=0`，`use_medicine` 只改 `expiring_medicine`）、M87 仅编辑顺序依赖、工具计数 108/107、`artifact.meta.farming`、无迁移与计数不变、`regular_token`/`_server_name`/`_prepare` 的接缝裁决、`medicine_pool` 的 `sources` 可选出参（不改 `MedicinePool` 字段）。

- [ ] **Step 3: 写台账**

按 [specs/README §4](../specs/README.md) 的模板补全：分支/基点/合并 commit/tag、spec 与 plan 链接、T0–T8 表格（做了什么）、验证证据（命令与结果，含 Task 0 基线）、Rulings I made、偏离（13 §8 的条目号）、遗留（联网补充验证的结论、当期活动是否进方案、商店解析项数）。

- [ ] **Step 4: 跑 §7.1 的硬门禁**

```bash
.venv/bin/python -m pytest -o addopts= -q > /tmp/m88-pytest.log 2>&1; echo $?; tail -n 30 /tmp/m88-pytest.log
.venv/bin/python scripts/dump_openapi.py --check; echo $?
.venv/bin/python scripts/agent_smoke.py | tail -n 1
.venv/bin/python scripts/orchestrator_smoke.py | tail -n 1
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m88-reg.log 2>&1; echo $?
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m88-cap.log 2>&1; echo $?
.venv/bin/python scripts/check_doc_links.py | tail -n 1
git diff --check; echo $?
.venv/bin/python -c "from maa_api.agent.tools import build_registry as b; r=b(); print(len(r.list()), sum(1 for d in r.list() if d.model_visible))"
grep -n "len(definitions) == 108" tests/agent/specs/test_agent_specs.py
grep -n "len(UNTRUSTED_OUTPUT_TOOLS) == 23\|len(EXPECTED_REFETCHABLE) == 52" tests/agent/test_tool_catalog.py
grep -n "len(scenarios) == 21\|len(ids) == 21" tests/evals/test_regression_replay.py
grep -n "^version:" maa_api/agent/specs/builtin/{planner,analyst,general}.md
grep -n "当前 108 个、模型可见 107 个" docs/v3/05-运行时与代理定义.md
grep -nE "T[O]DO|待[定]" debug/plans/m88-economist.md
```

契约计数再核一遍（与 Task 0 实测对齐）：

```bash
grep -n "共 186 条" docs/v3/14-现行接口清单.md
grep -n "ALL_CODE_COUNT = 171\|HTTP_BOUND_CODE_COUNT = 170" tests/domain/test_errors.py
grep -n "exactly_57_business_tables" tests/db/test_models.py
.venv/bin/python -c "from maa_api.settings import SETTING_KEYS; from maa_api.services.settings_schema import _FIELDS; print(len(SETTING_KEYS), len(_FIELDS))"
ls maa_api/db/migrations/versions/*.py | sort | tail -1
```

Expected：全量 pytest 相对 Task 0 基线不新增失败；OpenAPI `0`（工具走既有 invoke 端点）；两个冒烟末行 `SMOKE OK`；两个 replay `0` 且 capability 无 skipped、通过率不低于 M87 台账基线；`BROKEN 0`；`git diff --check` `0`；最后打印 `108 107`；六条 grep 中前五条各有匹配（版本三行 `4`、`4`、`7`），最后一条自检 grep 无输出；契约计数与 Task 0 实测一致（端点 186/agent 83、错误码 171/170、业务表 57、配置键 M87 后 `143 132`、迁移 head `0041` 或之后）。

- [ ] **Step 5: 跑 §7.2 的联网补充验证（不作门禁）**

```bash
.venv/bin/python - <<'EOF' > /tmp/m88-online.log 2>&1
import asyncio
from maa_api.services.economy.model import FarmingRequest
from maa_api.services.economy.service import default_farming_optimizer

async def main():
    r = await default_farming_optimizer().optimize(
        FarmingRequest(materials={"固源岩组": 30, "异铁块": 10}, plan_id=None, window_days=7),
        write_report=False, session_id=None, ticket_id=None)
    print(r["status"], r["days_estimated"], [s["stage_code"] for s in r["stages"]][:5], r["notes"][:3])

asyncio.run(main())
EOF
tail -n 5 /tmp/m88-online.log
```

Expected：打印状态、天数与关卡代号；失败或超时按「补充验证」记台账，不影响合并。台账记下当期活动是否进入方案、商店解析出几项。

- [ ] **Step 6: 提交文档与台账**

```bash
git add docs/v3/02-系统架构.md docs/v3/03-数据模型.md docs/v3/05-运行时与代理定义.md docs/v3/13-决策记录.md docs/v3/ledgers/m88-economist.md
git commit -m "docs(m88): 契约同步、实施记录与台账"
```

- [ ] **Step 7: 合并、打 tag、删分支（主 agent）**

```bash
git checkout dev
git merge --no-ff economist
git tag v3-m88
git branch -d economist
git log --oneline -3
```

Expected：merge commit 出现；`git tag -l 'v3-m88'` 输出 `v3-m88`；`git branch` 只剩 `master` 与 `dev`；合并后重跑 Step 4 的 pytest 与 `dump_openapi --check`，结果补进台账再提交到 `dev`。

---

## Self-Review

- **规格覆盖**：§4.2 的文件表逐行落到 T1（planner/plans）、T2（`event_shop.py`）、T3（economy 三文件）、T4（data/report/service）、T5（tools）、T7（scenario/environment）；§4.3.1 参数与返回在 T5；§4.5.1–§4.5.6 在 T4；§4.5.2 在 T4 的 `merge`；§4.5.3 在 T3；§4.5.7 在 T2；§4.5.9 在 T7；§4.7 在 T6；§9 在 T8；§11 的落点在 T8 的核对清单。§5 T1–T8 与本计划的 T1–T8 一一对应，T0 覆盖 §0 与基线。
- **测试清单覆盖**：§6 的每个文件名都在对应任务的 Files 或步骤里出现；用例名逐条抄进 T1–T5 的测试步骤；§6 里没有列、但 T5 完成判据要求的 REST/422/seam 断言在 T5 的用例里补齐。
- **类型一致性**：`FarmingInputs`、`_Prepared`、`FarmingModel`、`ShopGood`、`MedicinePool`、`FarmingSolution`、`LpMatrix` 在 T1/T3 定义，T4/T5 只引用；`StageOption`/`Recipe` 别名只在 T1 定义、T3 起使用；`default_farming_optimizer` 在 T4 定义、T5 调用；`regular_token` 在 T2 定义、T4 调用。后续任务不回改前面的签名。五条 Review Focus 各在 T3/T4/T5/T7 的测试步骤里落地。
- **比例与计数**：计划不重抄实现正文，代码块只留签名、断言与命令；13 条 §0 检查与附录 A 夹具只在 T0/T2 引用，不复制夹具正文。T5 改 108/107（两处断言）与 23/52；T6 改三份版本；T7 改 21；T8 用 `108 107` 兜底；端点、错误码、表、配置键、迁移在 T8 的契约计数块核一次（迁移 head 在 T0 已记）。核查命令：`grep -n "len(definitions) == 108" tests/agent/specs/test_agent_specs.py`、`grep -n "len(UNTRUSTED_OUTPUT_TOOLS) == 23\|len(EXPECTED_REFETCHABLE) == 52" tests/agent/test_tool_catalog.py`、`grep -n "len(scenarios) == 21\|len(ids) == 21" tests/evals/test_regression_replay.py`、`grep -n "^version:" maa_api/agent/specs/builtin/{planner,analyst,general}.md`、`grep -nE "T[O]DO|待[定]" debug/plans/m88-economist.md`（应无输出）；端点/错误码/表/配置键/迁移的核查用 T8 Step 4 的第二组命令，pytest 用 T8 Step 4 的全量命令。
- **裁决留痕**：`regular_token`（无商店时代的代币名）、`_server_name`（server 归一）、`app.state` 无 `planner_service` 的默认装配、计划路径仍读一次仓库（供用药池）、`medicine_pool` 的 `sources` 可选出参（不改 `MedicinePool` 字段）、评测 cassette 的「2 条探查 + 记忆抽取」口径、05 不记版本号，全部写进 T0 台账与 T8 的 13 §8 条目。
