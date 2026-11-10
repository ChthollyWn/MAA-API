# M99 能力效果看板 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Agent 的能力与效果变成一页可下钻的真实读数：新增评测结果入库与 9 个 HTTP 端点，看板展示准确率、幻觉率、安全率、任务完成率、对话效率、成本、用户满意度七项指标及其评估方法，并支持对一次运行评价。

**Architecture:** 先建三张表与发布设施（评测结果从命令行进库），再写只读聚合服务与端点，最后做前端两页与评价入口。指标口径收敛在一个后端目录 `services/insights/catalog.py` 里，前端不自己算数；离线指标只统计 `record`/`live` 运行，`replay` 只作回归基线。

**Tech Stack:** FastAPI、Pydantic 2、SQLModel/SQLite、Alembic、pytest；React 19、TanStack Query、vitest、Tailwind、echarts（复用现有 `ChartCard`/`EChartSurface` 的 line/bar，不新增图表类型）。不新增依赖。

**Spec:** [docs/v3/specs/m99-capability-insights.md](../specs/m99-capability-insights.md)（§0、§4、§5 T1–T12、§6、§7、§9、§11）

## Global Constraints

- 分支 `capability-insights`，从 `dev` 新建；完成后 `git merge --no-ff` 回 `dev`、打 tag `v3-m99`、删分支；**不 push**。
- 不使用 worktree；直接在现有检出上切分支。本里程碑建议先于 M91 执行，顺序偏离在 T12 记 [13 §8](../13-决策记录.md)。
- 迁移：`0043_capability_insights`；`down_revision` 以开工时 head 为准（当前 `0042_demonstration`）。业务表 58 → 61。
- 端点：191 → 200（T4 +5、T7 +2、T8 +2）；错误码 173/172 → 174/173（T5 的 `EVAL_RUN_NOT_FOUND`）。
- 配置键 144、config-only 13 不变；工具 111/110 不变；不新增依赖、不加 SSE/WS 事件、不改 M90 的 `ChartCardData` 契约。
- **文档计数随任务增量更新**：`docs/v3/14` §4 与 §6 的表格、小节计数、总数必须在对应任务里同步，否则 `tests/domain/test_errors.py` 与 `tests/domain/test_route_inventory.py` 会红；T12 只做总校与其余文档。
- 评测场景 23 → 31（regression 5、capability 26）；离线指标只统计 `mode IN ('record','live')`；`replay` 是回归基线。
- 一律 `.venv/bin/python`；本机没有 `rg`，用 `grep -rn`；pytest 看计数用 `-o addopts= -q`；长输出重定向到 `/tmp/m99-*`。
- 提交信息 `feat(m99): <中文摘要>` / `test(m99): …` / `docs(m99): …`；文档用简体中文；长输出只看尾部与退出码。
- 遇到 spec §10「停下来问」的情形停下来报告；spec 没写到的实现细节按最小改动自行决定，写进台账「Rulings I made」。

## 开工基线（Task 1 实测后回填）

- `dev` 必须 = M90 收口 + §8.185 维护性补丁（写作时 rebase 后是 `fc3c513`）；Task 1 用 `git rev-parse --short dev` 与 `git log -1 --format=%H` 记录，计划与台账写实测值。
- `git tag -l 'v3-m68' 'v3-m69' 'v3-m87' 'v3-m90'` 四个都输出。
- 迁移 head：`ls maa_api/db/migrations/versions | grep -E '^[0-9]{4}_' | sort | tail -1` 输出 `0042_demonstration.py`。
- 评测 replay 基线：regression `5/5`、capability `18/18`、末行 `EVAL OK`；抄下两个结果 id。
- 计数以 Task 1 实测回填：表 58、端点 191（agent 88 / OpenAPI tag 92）、错误码 173/172、配置 144（config-only 13）、工具 111/110、场景 23。
- 在 `dev` 上跑一次全量 pytest 记录已知失败；本分支合并后的全量结果不得新增失败。
- `grep -rn "class EvalRun\|class RunFeedback\|EVAL_RUN_NOT_FOUND\|insights" maa_api web/src` 不应命中已有实现；`grep -n "publish" scripts/agent_eval.py` 无输出。

## §0 前置检查

照 spec §0 的 19 行原样执行，不重写。第 3、6–10 条以 Task 1 实测为准；第 5 条（replay 基线）不通过时先报告，不在本里程碑里修评测。

## 接缝裁决

实现时按这张表，不再另找入口。

| 接缝 | 裁决 |
|---|---|
| CLI 写库 | `publish_suite_result` 用同步 `create_engine(db_url or SYNC_URL)` + `Session`（`SYNC_URL` 注释就是「Alembic 与维护脚本使用」）；不 import `maa_api.main`，不碰异步 `session_factory`。 |
| 发布幂等 | `eval_run.id` 即 `SuiteResult.id`；已存在则先 `delete(eval_case_result where eval_run_id=id)` 再整行覆盖；整个发布一个事务。只发布 `variant == "target"` 的 case。 |
| 发布时间 | `started_at`/`finished_at` 是带 `Z` 的 ISO 字符串；`duration_ms = int((finished - started).total_seconds() * 1000)`，解析失败抛 `PublishError`。 |
| 用例成本 | `case.usage` 是 `_aggregate_usage` 的字典（`prompt_tokens`、`completion_tokens`、`cached_tokens`、`reasoning_tokens`、`cost_micros`、`currency`），键可能缺；一律 `int(usage.get(k) or 0)`，`currency` 取第一个非空。 |
| 场景标签 | `metrics: list[MetricId]`，Pydantic 会把字符串转枚举；落库时 `str(metric)` 成 JSON；`regression` 场景禁止非空 metrics。 |
| 场景 tags | 场景 YAML/JSON 的 `tags` 写 `safety-attack`/`safety-control`/`groundedness`；发布时随 metrics 快照进 `eval_case_result.tags`；安全 secondary 按 tags 过滤，不硬编码 scenario id。 |
| 录制头 | 新 cassette 的头写 `source: "handwritten"`（`CassetteHeader.source` 只接受 `handwritten`/`recorded`）。 |
| `CaseResult` 字段 | `title: str = ""`、`metrics: list[str] = field(default_factory=list)`、`tags: list[str] = field(default_factory=list)` 追加在 `detail` 之后并带默认值；`to_json()` 与 `write_result()` 的 case 载荷都加 `title`/`metrics`/`tags`。 |
| 服务装配 | `app.state.insights_service = InsightsService(db_session.session_factory)`，放在 `app.state.nav_service` 之后；路由用 `_insights_service(request)`，未装配 503 `SERVICE_UNAVAILABLE`。 |
| 统计查询 | 原生 SQL 用 `sqlalchemy.text(...)` + `bindparams`，时间参数传 naive UTC datetime，照 `maa_api/agent/brief/facts.py`；每个指标一个私有方法，返回 `MetricReading`。 |
| 离线过滤 | SQL 里写死 `er.mode IN ('record','live')`；replay 只进 `secondary` 的计数。`json_each(ec.metrics)` 判断标签，不靠 `LIKE`。 |
| 用户轮次 | 取 `agent_inbox` 中 `session_id = ticket.worker_session_id`、`kind = 'user_message'` 的条数；不数 `agent_message.role='user'`（汇报/事件卡片也是 user 角色）。 |
| 评测详情 metric_rates | 按该 run 自身全部 case 现算，不受窗口与 record/live 过滤；`hallucination_rate` 取 `1 - passed/(passed+failed)`，其余离线指标取通过率；`metrics=[]` 的 case 不参与。 |
| 比率与空值 | `ratio` 与 Wilson 在 `stats.py`；`passed + failed == 0` 时 `value/ci/delta` 全 `None`；`min_sample` 只影响提示。 |
| 成本币种 | 窗口内 `agent_run.currency` 去重 > 1 时主值 `None`、`warnings` 加一条、`secondary` 分币种各一行。 |
| 反馈 upsert | `RunFeedbackRepository.upsert` 按 `run_id` 查行：有则改 `rating/reasons/comment/updated_at`，无则插入；`created_at` 不覆盖。`reasons` 去重、空白评论存 `None`。 |
| 端点归属 | 9 个端点全在 `maa_api/api/routers/insights.py`（`prefix="/api/agent"`），路由注册在 `main.py`；反馈路由的 `/{run_id}/feedback` 与既有 `runs.router` 不冲突，用真实 `create_app()` 的测试钉住。 |
| 前端 key | `keys.agent` 下新增 8 个：`insightsAll`（整片前缀）、`insightsCatalog`、`insightsOverview(windowDays)`、`insightTrend(metric, windowDays)`、`insightBreakdown(metric, windowDays)`、`evalRuns(filters)`、`evalRun(runId)`、`runFeedback(runId)`；值形状照 `web/src/api/keys.ts` 既有函数；提交评价后失效 `runFeedback(runId)` 与 `insightsAll()`。 |
| 图表 | 把接口数据映射成 `ChartCardData`（`chartType` 用 `line`/`bar`），用现有 `ChartCard` 渲染；映射写在 `features/insights/chart/insight-chart.ts`；不改 `ChartCardData`/`EChartSurface`/`buildChartOption`，不新增图表注册类型。 |
| 评价入口 | 挂在 `RunDetailPage` 概要卡之后、运行树之前；不放进会话消息。 |
| 文档计数 | T4 后端点 196、T7 后 198、T8 后 200；T5 后错误码 174/173；T6 后场景 31；每步都更新 `docs/v3/14` 对应小节。 |
| 测试写法 | 路由测试照 `tests/api/test_run_spans_router.py` 的最小 app + `isolated_db`，用 `make_client(app)` 取 TestClient（不是仓库 fixture）；仓储测试用 `tests/db/conftest.py` 的 `db_session`；迁移测试用 `tmp_path` 自建 `Config`，没有 `alembic_db` fixture。 |

## Review Focus

1. **离线指标混入 replay**：只跑过 `--mode replay` 的环境应显示「暂无数据」，不得出现「准确率 100%」。`tests/services/test_insights_service.py::test_offline_metrics_ignore_replay` 钉住。
2. **评测发布半写**：同一结果重复 `--publish` 不得留下两行 `eval_run` 或重复 cases；缺表时只警告、评测退出码不变。`test_eval_publish.py::test_publish_is_idempotent` 与 `test_publish_missing_table_reports_error` 钉住。
3. **统计口径漂移**：完成率分母只含终态、成本多币种不合并、效率只对 done 条件化、样本量随窗口变化。`test_insights_service.py` 的 8 条用例与统计单测钉住。
4. **反馈覆盖语义**：同一次运行二次提交只覆盖一行且 `created_at` 不变，回复与 `updated_at` 变化；`rating` 越界 422。`test_run_feedback_router.py` 钉住。
5. **路由与导航计数门禁**：新增 9 个端点与 1 个错误码会同时红 `test_route_inventory.py`、`test_errors.py` 与前端 `layout.test.tsx`/`more.test.tsx`；对应任务里同步文档与断言，不在 T12 才补。

---

### Task 1: 分支、§0 基线、spec 与计划入库

**Files:**
- Create: `docs/v3/specs/m99-capability-insights.md`（本 spec 已写好；复制进分支）
- Create: `docs/v3/plans/m99-capability-insights.md`（本计划）
- 不改代码

**Interfaces:**
- Consumes: `dev` 的 M90 收口提交、`v3-m90` tag、`0042_demonstration` head。
- Produces: `capability-insights` 分支、基线记录（commit、head、计数、评测结果 id、已知失败）、后续任务可用的已提交 spec 与计划。

- [ ] **Step 1: 切分支并记录基线**

```bash
git checkout dev
git rev-parse --short dev
git log -1 --format='%H %s'
git checkout -b capability-insights
git status -sb
git tag -l 'v3-m68' 'v3-m69' 'v3-m87' 'v3-m90'
ls maa_api/db/migrations/versions | grep -E '^[0-9]{4}_' | sort | tail -1
```

Expected: 第一行 `## capability-insights`；head `0042_demonstration.py`；四个 tag 都有输出。

- [ ] **Step 2: 跑 spec §0 全部 19 条检查**

照 spec §0 的命令与期望逐条跑；把实测的 commit、head、表/端点/错误码/配置/工具计数、两个评测结果 id、全量 pytest 的已知失败记进台账草稿（`/tmp/m99-baseline.md` 也行）。

- [ ] **Step 3: 提交 spec 与计划**

```bash
git add docs/v3/specs/m99-capability-insights.md docs/v3/plans/m99-capability-insights.md
git commit -m "docs(m99): 能力效果看板规格与实施计划"
```

Expected: `git log --oneline -1` 是本提交；`git status -sb` 干净。

### Task 2: 表、迁移、枚举、仓储（spec T2）

**Files:**
- Modify: `maa_api/domain/enums.py`
- Modify: `maa_api/db/models.py`
- Create: `maa_api/db/migrations/versions/0043_capability_insights.py`
- Create: `maa_api/db/repositories/evals.py`
- Create: `maa_api/db/repositories/feedback.py`
- Modify: `tests/domain/test_enums.py`
- Modify: `tests/db/test_models.py`
- Create: `tests/db/test_capability_insights_migration.py`
- Create: `tests/db/test_repositories_evals.py`
- Create: `tests/db/test_repositories_feedback.py`

**Interfaces:**
- Consumes: `utcnow`、`json_column`/`text_column`/`datetime_column`、`BaseRepository`/`Page`（`maa_api/db/repositories/base.py`）。
- Produces:
  - `EvalRun`（`id`、`suite`、`mode`、`started_at`、`finished_at`、`duration_ms`、`total/passed/failed/error/skipped`、`pass_rate`、`model_calls`、四个 token 列、`cost_micros`、`currency`、`git/model/filters` JSON、`created_at`）
  - `EvalCaseResult`（`id`、`eval_run_id`、`scenario`、`title`、`suite`、`agent`、`metrics` JSON、`tags` JSON、`status`、`score`、`attempt`、`variant`、`duration_ms`、`model_calls`、四个 token 列、`cost_micros`、`currency`、`graders` JSON、`error`、`created_at`）
  - `RunFeedback`（`id`、`run_id`、`rating`、`reasons` JSON、`comment`、`created_at`、`updated_at`）
  - `EvalRunRepository.upsert_with_cases(run, cases)`、`EvalRunRepository.page_runs(...) -> Page[EvalRun]`、`EvalRunRepository.get_run(run_id)`
  - `EvalCaseRepository.list_by_run(run_id)`
  - `RunFeedbackRepository.get_by_run(run_id)`、`upsert(feedback)`、`list_since(since, limit)`

- [ ] **Step 1: 写迁移与枚举的失败测试**

`tests/db/test_capability_insights_migration.py` 照 `tests/db/test_demonstration_migration.py` 的写法：模块级 `PREVIOUS_REVISION = "0042_demonstration"`、`HEAD_REVISION = "0043_capability_insights"`，用 `tmp_path` + `_config(db_path)` + `alembic.command.upgrade` 建库，绝不碰 `resource/maa_api.db`。

```python
def test_upgrade_creates_three_tables_and_indexes(tmp_path):
    # 断言 eval_run / eval_case_result / run_feedback 三张表存在，
    # 逐列断言类型与可空（照 spec §4.1 代码）；
    # 断言 5 个索引、2 个唯一约束、两个 FK（CASCADE）、两个自增表。
    ...


def test_downgrade_drops_them(tmp_path):
    ...
```

`tests/db/test_repositories_evals.py`：

```python
def test_upsert_with_cases_replaces_cases(db_session):
    ...


def test_page_runs_orders_and_filters(db_session):
    ...


def test_get_run(db_session):
    ...
```

`tests/db/test_repositories_feedback.py`：

```python
def test_upsert_creates_then_updates_keeping_created_at(db_session):
    ...


def test_get_by_run_and_list_since(db_session):
    ...
```

`tests/domain/test_enums.py` 的 `EXPECTED_VALUES` 加 6 个枚举（值照 spec §4.1）。

- [ ] **Step 2: 跑测试确认先红**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/db/test_capability_insights_migration.py tests/db/test_repositories_evals.py tests/db/test_repositories_feedback.py tests/domain/test_enums.py > /tmp/m99-t2-red.log 2>&1; echo $?; tail -n 20 /tmp/m99-t2-red.log
```

Expected: 非 0，错误是表/类/枚举不存在。

- [ ] **Step 3: 写枚举、模型、迁移、仓储**

按 spec §4.1 的代码逐字落；迁移照 `0042_demonstration.py` 的风格（模块 docstring 写三张表与索引，`upgrade` 建表建索引，`downgrade` 反序）。仓储照 `maa_api/db/repositories/goals.py`：模块 docstring 写三条纪律、写方法只 `flush` 不 `commit`、分页返回 `Page`。

- [ ] **Step 4: 更新 `tests/db/test_models.py`**

`BUSINESS_TABLES` 加三张表；`EXPECTED_INDEXES` 加 5 条；`EXPECTED_UNIQUE_CONSTRAINTS` 加 2 条；`EXPECTED_FK_CASCADE` 加 `("run_feedback", "run_id")` 与 `("eval_case_result", "eval_run_id")`；`EXPECTED_JSON_COLUMNS` 补 `("eval_run", "git")`、`("eval_run", "model")`、`("eval_run", "filters")`、`("eval_case_result", "metrics")`、`("eval_case_result", "tags")`、`("eval_case_result", "graders")`、`("run_feedback", "reasons")`（以实际列型为准）；`test_metadata_has_exactly_58_business_tables` 改名 61 并改注释；检查 `EXPECTED_TEXT_COLUMNS` 是否含 `eval_case_result.error`（按实际列型补）。

- [ ] **Step 5: 跑测试确认全绿**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/db tests/domain/test_enums.py > /tmp/m99-t2.log 2>&1; echo $?; tail -n 5 /tmp/m99-t2.log
```

Expected: `0`。再验证迁移可逆（先备份 `resource/maa_api.db`；`maa_api/db/migrations/env.py` 缺省取 `SYNC_URL`）：

```bash
.venv/bin/alembic upgrade head && .venv/bin/alembic downgrade -1 && .venv/bin/alembic upgrade head; echo $?
```

Expected: `0`，三张表在 head 上存在。

- [ ] **Step 6: 提交**

```bash
git add maa_api/domain/enums.py maa_api/db/models.py maa_api/db/migrations/versions/0043_capability_insights.py maa_api/db/repositories/evals.py maa_api/db/repositories/feedback.py tests/domain/test_enums.py tests/db/test_models.py tests/db/test_capability_insights_migration.py tests/db/test_repositories_evals.py tests/db/test_repositories_feedback.py
git commit -m "feat(m99): 评测结果与运行反馈三张表"
```

### Task 3: 指标目录、统计与聚合服务（spec T3）

**Files:**
- Create: `maa_api/services/insights/__init__.py`
- Create: `maa_api/services/insights/catalog.py`
- Create: `maa_api/services/insights/stats.py`
- Create: `maa_api/services/insights/service.py`
- Create: `tests/services/test_insights_catalog.py`
- Create: `tests/services/test_insights_stats.py`
- Create: `tests/services/test_insights_service.py`

**Interfaces:**
- Consumes: Task 2 的三个模型与仓储、`db.session.session_factory` 形状（`async_sessionmaker[AsyncSession]`）。
- Produces:
  - `METRICS: tuple[MetricDefinition, ...]`（7 条，顺序见 spec §4.5.1）
  - `wilson_interval(successes, total, *, z=1.96) -> tuple[float, float] | None`、`rate()`、`median()`、`window_bounds(now, days)`
  - `InsightsService(session_factory)` 的 `catalog/overview/trend/breakdown/feedback_digest` 与内部 `_offline_metric/_online_completion/_online_efficiency/_online_cost/_online_satisfaction`
  - `OverviewData/TrendData/BreakdownData/MetricReading` 等内部 dataclass（服务层返回后由路由转 schema）

- [ ] **Step 1: 写统计与目录的失败测试**

`tests/services/test_insights_stats.py`：

```python
def test_wilson_interval_known_values():
    low, high = wilson_interval(7, 10)
    assert abs(low - 0.3968) < 0.0001
    assert abs(high - 0.8922) < 0.0001


def test_wilson_zero_total_is_none():
    assert wilson_interval(0, 0) is None
```

另写夹取（0/10、10/10）、中位数奇偶、`window_bounds` 左闭右开。

`tests/services/test_insights_catalog.py`：7 条顺序、`supports_breakdown` 只对 `task_completion/conversation_efficiency/cost/user_satisfaction` 为真、`evidence` 与 spec 表一致。

`tests/services/test_insights_service.py` 用 `tests/services/conftest.py` 的 `retention_session_factory` 临时库 fixture（或同文件新增一个同形状 fixture）直接插 `agent_run`/`ticket`/`agent_inbox`/`run_feedback`/`eval_run`/`eval_case_result` 行（对话效率用 `agent_inbox` 的 `user_message`，不用 `agent_message`），逐条测：

```python
def test_offline_metrics_ignore_replay(service, seeded):
    ...


def test_completion_rate_and_verified_secondary(service, seeded):
    ...


def test_cost_currency_mismatch_nulls_value(service, seeded):
    ...


def test_trend_by_utc_day(service, seeded):
    ...
```

- [ ] **Step 2: 跑测试确认先红**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/services/test_insights_stats.py tests/services/test_insights_catalog.py tests/services/test_insights_service.py > /tmp/m99-t3-red.log 2>&1; echo $?; tail -n 20 /tmp/m99-t3-red.log
```

Expected: 非 0，模块不存在。

- [ ] **Step 3: 实现目录、统计与服务**

按 spec §4.5.1 写 `METRICS`（`label/definition/formula/source/evaluation/evidence/min_sample/limitations/supports_*` 逐条照表）；按 §4.5.2 实现窗口与统计；按 §4.5.3 的过滤规则实现离线指标；按 §4.5.4 实现满意度。SQL 用 `text(...)` + `bindparams`，照 `maa_api/agent/brief/facts.py`；每个指标单独方法，返回内部 `MetricReading`（value/sample_size/ci/delta/secondary/updated_at/warnings）。

- [ ] **Step 4: 跑测试确认全绿**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/services > /tmp/m99-t3.log 2>&1; echo $?; tail -n 5 /tmp/m99-t3.log
```

Expected: `0`。

- [ ] **Step 5: 提交**

```bash
git add maa_api/services/insights tests/services/test_insights_catalog.py tests/services/test_insights_stats.py tests/services/test_insights_service.py
git commit -m "feat(m99): 指标目录、统计与聚合服务"
```

### Task 4: 指标端点（spec T4）

**Files:**
- Create: `maa_api/api/schemas/insights.py`
- Create: `maa_api/api/routers/insights.py`（本任务只加 1–4 与 9 号端点）
- Modify: `maa_api/main.py`（装配 `InsightsService`、注册路由）
- Create: `tests/api/test_insights_router.py`
- Modify: `web/openapi.json`、`web/src/types/api.d.ts`（重新生成）
- Modify: `docs/v3/14-现行接口清单.md`（§6.15 加 5 行、小节计数 +5、总数 191→196）

**Interfaces:**
- Consumes: Task 3 的 `InsightsService` 与数据类、`require_auth`、`error_responses`、`_utc_z` 写法（照 `runs.py`）。
- Produces: `MetricDefinitionOut/OverviewOut/TrendOut/BreakdownOut/FeedbackDigestOut` 等 schema；5 条路由；`app.state.insights_service`。

- [ ] **Step 1: 写路由的失败测试**

`tests/api/test_insights_router.py` 照 `tests/api/test_run_spans_router.py` 的最小装配 + `isolated_db` 临时库写法（`tests/api/conftest.py` 另有 `make_client` 工厂）。下面的 `client` 是 `make_client(app)` 的返回值，`app` 按该文件装配，不是仓库 fixture：

```python
def test_overview_empty_data_returns_seven_nulls(client):
    body = client.get("/api/agent/insights/overview").json()
    assert [item["id"] for item in body["items"]] == [
        "accuracy", "hallucination_rate", "safety_rate", "task_completion",
        "conversation_efficiency", "cost", "user_satisfaction",
    ]
    assert all(item["value"] is None for item in body["items"])


def test_overview_window_validation(client):
    assert client.get("/api/agent/insights/overview", params={"window_days": 0}).status_code == 422


def test_breakdown_unsupported_metric_400(client):
    response = client.get("/api/agent/insights/breakdown", params={"metric": "accuracy"})
    assert response.status_code == 400
    assert "supported" in response.json()["error"]["details"]


def test_requires_auth(...):
    ...
```

- [ ] **Step 2: 跑测试确认先红**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/api/test_insights_router.py > /tmp/m99-t4-red.log 2>&1; echo $?; tail -n 20 /tmp/m99-t4-red.log
```

Expected: 非 0（404 或 import 失败）。

- [ ] **Step 3: 写 schema、路由与装配**

按 spec §4.3 写 `MetricDefinitionOut/MetricCardOut/SubMetricOut/OverviewOut/TrendPointOut/TrendOut/BreakdownItemOut/BreakdownOut/FeedbackDigestOut`（`extra="forbid"`；时间做成 `_utc_z`）；5 个端点与 `_insights_service`；`main.py` 在 `nav_service` 之后装配并注册 `insights.router`。

- [ ] **Step 4: 更新 `docs/v3/14` 与测试**

`§6.15 agent` 加 5 行（与 spec §4.3 表一致），`（N 条）` 与 §6 开头「共 200 条」在 T8 收口时改为 200，本步先按实际改为 196 并在 T12 再总校；`§4` 错误码本任务不动。

- [ ] **Step 5: 重生成 OpenAPI 与前端类型**

```bash
.venv/bin/python scripts/dump_openapi.py
.venv/bin/python scripts/dump_openapi.py --check; echo $?
cd web && pnpm gen:api && pnpm gen:api:check; echo $?; cd ..
```

Expected: 两个 `0`。

- [ ] **Step 6: 跑测试确认全绿**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/api/test_insights_router.py tests/domain/test_route_inventory.py > /tmp/m99-t4.log 2>&1; echo $?; tail -n 8 /tmp/m99-t4.log
```

Expected: `0`。

- [ ] **Step 7: 提交**

```bash
git add maa_api/api/schemas/insights.py maa_api/api/routers/insights.py maa_api/main.py tests/api/test_insights_router.py web/openapi.json web/src/types/api.d.ts "docs/v3/14-现行接口清单.md"
git commit -m "feat(m99): 指标目录、总览、趋势与分组端点"
```

### Task 5: 评测发布（spec T5）

**Files:**
- Modify: `maa_api/agent/evals/scenario.py`（`metrics` 字段与校验）
- Modify: `maa_api/agent/evals/runner.py`（`CaseResult.title/metrics/tags`、两处 JSON）
- Create: `maa_api/agent/evals/publish.py`
- Modify: `scripts/agent_eval.py`（`--publish`）
- Modify: `maa_api/domain/errors.py`（`EVAL_RUN_NOT_FOUND`）
- Modify: `tests/evals/test_eval_scenario.py`、`tests/evals/test_eval_runner.py`
- Create: `tests/evals/test_eval_publish.py`
- Modify: `docs/v3/14-现行接口清单.md`（§4 错误码表 +1、总数 173→174、`tests/domain/test_errors.py` 的 `ALL_CODE_COUNT`/`HTTP_BOUND_CODE_COUNT`）

**Interfaces:**
- Consumes: Task 2 的 `EvalRun`/`EvalCaseResult` 模型、`SYNC_URL`、`SuiteResult`/`CaseResult`/`Scenario`。
- Produces: `PublishError`、`publish_suite_result(result, scenarios, *, db_url=None) -> str`、CLI `--publish`、`Scenario.metrics`、`CaseResult.title/metrics/tags`、错误码 `EVAL_RUN_NOT_FOUND`。

- [ ] **Step 1: 写 schema/runner/发布/错误码的失败测试**

```python
# tests/evals/test_eval_scenario.py
def test_metrics_field_round_trips():
    scenario = Scenario.model_validate({..., "metrics": ["accuracy", "safety_rate"]})
    assert scenario.metrics == [MetricId.ACCURACY, MetricId.SAFETY_RATE]


def test_regression_rejects_metrics():
    with pytest.raises(ValidationError):
        Scenario.model_validate({..., "suite": "regression", "metrics": ["accuracy"]})
```

```python
# tests/evals/test_eval_runner.py
def test_case_result_carries_title_metrics_and_tags(...):
    ...
```

```python
# tests/evals/test_eval_publish.py
def test_publish_writes_run_and_cases(tmp_path):
    ...
    assert publish_suite_result(result, scenarios, db_url=f"sqlite:///{tmp_path/'t.db'}") == result.id


def test_publish_is_idempotent(tmp_path):
    ...


def test_publish_missing_table_reports_error(tmp_path):
    with pytest.raises(PublishError):
        ...


def test_publish_aggregates_usage_and_duration(tmp_path):
    ...


def test_publish_snapshots_tags(tmp_path):
    ...
```

错误码断言：`tests/domain/test_errors.py` 先改常量预期（174/173），跑会红。

- [ ] **Step 2: 跑测试确认先红**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_eval_scenario.py tests/evals/test_eval_runner.py tests/evals/test_eval_publish.py tests/domain/test_errors.py > /tmp/m99-t5-red.log 2>&1; echo $?; tail -n 20 /tmp/m99-t5-red.log
```

Expected: 非 0。

- [ ] **Step 3: 实现 schema 字段、`CaseResult` 字段、发布与 CLI**

按 spec §4.2 第 9–13 条实现。`publish.py` 用同步 `create_engine`+`Session`；`db_url` 缺省 `db.session.SYNC_URL`；`Session` 里一个事务完成 upsert；`metrics`/`tags`/`title` 从传入的 `scenarios` 映射取，缺失时 `metrics=[]`、`tags=[]`、`title=scenario`。CLI 的 `--publish` 在 `write_result` 之后调用，异常只打印到 stderr。

- [ ] **Step 4: 更新错误码文档与测试常量**

`docs/v3/14` §4 加 `EVAL_RUN_NOT_FOUND` 行与「共 174 条」；`tests/domain/test_errors.py` 三个常量按实际改（`ALL_CODE_COUNT=174`、`HTTP_BOUND_CODE_COUNT=173`、`SECTION_COUNT` 不变）；`docs/v3/04` §5 的表在 T12 总校时补，但本步先加行更省——两个文档的码表由同一个测试解析，先加 `14` 即可。

- [ ] **Step 5: 跑测试确认全绿**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/evals tests/domain/test_errors.py > /tmp/m99-t5.log 2>&1; echo $?; tail -n 5 /tmp/m99-t5.log
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay --publish > /tmp/m99-publish.log 2>&1; echo $?; tail -n 3 /tmp/m99-publish.log
```

Expected: `0`；评测末行 `EVAL OK`，`grep -c "发布失败" /tmp/m99-publish.log` 为 0；`sqlite3 resource/maa_api.db "select count(*) from eval_run"` 有行。

- [ ] **Step 6: 提交**

```bash
git add maa_api/agent/evals/scenario.py maa_api/agent/evals/runner.py maa_api/agent/evals/publish.py scripts/agent_eval.py maa_api/domain/errors.py tests/evals tests/domain/test_errors.py "docs/v3/14-现行接口清单.md"
git commit -m "feat(m99): 评测结果发布与场景 metrics 标签"
```

### Task 6: 场景标签与 8 个新场景（spec T6）

**Files:**
- Modify: 14 个既有场景（`tests/evals/scenarios/` 下，见 spec §4.5.3 清单）
- Create: 8 个新场景 YAML（`hallucination-context-only`、`hallucination-unknown-stage`、`safety-refuse-confirmation-bypass`、`safety-refuse-secret-read`、`safety-refuse-arbitrary-command`、`safety-refuse-lease-preempt`、`safety-control-stage-lookup`、`safety-control-pipeline-status`）
- Create: 8 个 cassette JSONL（`tests/evals/cassettes/<id>.jsonl`）
- Modify: `tests/evals/test_regression_replay.py`（场景数、capability 数）
- Create: `tests/evals/test_eval_scenario_metrics.py`
- Modify: `docs/v3/10-可观测与评测.md`（§6.4 加 `metrics` 行；§6.2/§6.9 场景数与口径）

**Interfaces:**
- Consumes: Task 5 的 `Scenario.metrics`。
- Produces: 14 个 accuracy 场景、2 个 hallucination 场景、6 个 safety 场景；capability replay 基线 26/26。

- [ ] **Step 1: 写场景与新测试的失败断言**

`tests/evals/test_regression_replay.py` 的计数改成 31/5/26；`tests/evals/test_eval_scenario_metrics.py`：

```python
JUDGE_SCENARIOS = {
    "v2-876-logs-budget", "v2-877-failure-hint-startup",
    "v2-881-startup-no-channel", "v2-881-wait-timeout-screenshot",
}


def test_every_capability_scenario_has_metrics_or_is_judge():
    for scenario in load_scenarios(SCENARIOS_DIR, suites=("capability",)):
        assert scenario.metrics or scenario.id in JUDGE_SCENARIOS


def test_safety_forbid_tools_exist_in_registry():
    names = {definition.name for definition in build_registry().list()}
    for scenario in load_scenarios(SCENARIOS_DIR, suites=("capability",)):
        if MetricId.SAFETY_RATE not in scenario.metrics:
            continue
        for spec in scenario.expect:
            for name in getattr(spec, "forbid", ()):
                assert name in names
```

- [ ] **Step 2: 跑测试确认先红**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/evals/test_regression_replay.py tests/evals/test_eval_scenario_metrics.py > /tmp/m99-t6-red.log 2>&1; echo $?; tail -n 20 /tmp/m99-t6-red.log
```

Expected: 非 0。

- [ ] **Step 3: 给 14 个场景加 `metrics`**

JSON 场景加 `"metrics": ["accuracy"]`，YAML 场景加 `metrics: [accuracy]`；位置在 `tags` 之后。`git diff --stat` 应恰好 14 个场景文件。

- [ ] **Step 4: 写 8 个新场景与手写 cassette**

strict 按 spec §4.5.3 的表写；cassette 照 `tests/evals/cassettes/legacy-daily-submit.jsonl` 的头与 `reply` 形状（头里 `source: "handwritten"`，`CassetteHeader.source` 只接受 `handwritten`/`recorded`）。安全场景的 `tags` 写 `safety-attack`（前 4 个）或 `safety-control`（后 2 个）；`tool_sequence` 的 `forbid`/`min_calls` 工具名用 `{d.name for d in build_registry().list()}` 校验；找不到就停下来问（spec §10）。

- [ ] **Step 5: 跑评测与测试确认全绿**

```bash
.venv/bin/python scripts/agent_eval.py --suite all --mode replay > /tmp/m99-eval-t6.log 2>&1; echo $?; tail -n 4 /tmp/m99-eval-t6.log
.venv/bin/python -m pytest -o addopts= -q tests/evals > /tmp/m99-t6.log 2>&1; echo $?; tail -n 5 /tmp/m99-t6.log
```

Expected: `0`；`5/5`、`26/26`、`EVAL OK`。

- [ ] **Step 6: 同步 `docs/v3/10` 场景字段与数量**

§6.4 字段表加一行 `metrics`（类型、必填、说明）；§6.2 的 capability 描述加一句「能力看板只统计 record/live，replay 是回归基线」；§6.9 的场景数更新。

- [ ] **Step 7: 提交**

```bash
git add tests/evals tests/evals/scenarios tests/evals/cassettes "docs/v3/10-可观测与评测.md"
git commit -m "test(m99): 能力评测标签与幻觉、安全场景"
```

### Task 7: 评测运行端点（spec T7）

**Files:**
- Modify: `maa_api/api/routers/insights.py`（5、6 号端点）
- Modify: `maa_api/api/schemas/insights.py`
- Modify: `maa_api/db/repositories/evals.py`（若 Task 2 未含必要查询）
- Create: `tests/api/test_eval_runs_router.py`
- Modify: `web/openapi.json`、`web/src/types/api.d.ts`
- Modify: `docs/v3/14-现行接口清单.md`（§6.15 +2 行、总数 196→198）

**Interfaces:**
- Consumes: Task 5 的发布结果、`EVAL_RUN_NOT_FOUND`、`EvalRunRepository`。
- Produces: `GET /api/agent/evals/runs`、`GET /api/agent/evals/runs/{run_id}` 与 `EvalRunPage/EvalRunDetailOut`。

- [ ] **Step 1: 写失败测试**

```python
def test_list_filters_and_pagination(client, published_eval):
    ...


def test_detail_metric_rates_and_cases(client, published_eval):
    ...


def test_unknown_run_404(client):
    assert client.get("/api/agent/evals/runs/nope").json()["error"]["code"] == "EVAL_RUN_NOT_FOUND"


def test_cases_truncated_at_500(client, published_eval):
    ...
```

- [ ] **Step 2: 跑测试确认先红 → 实现 → 全绿**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/api/test_eval_runs_router.py > /tmp/m99-t7.log 2>&1; echo $?; tail -n 5 /tmp/m99-t7.log
```

实现：列表按 `finished_at DESC, id DESC` 分页，`suite`/`mode` 过滤；详情返回 summary + `metric_rates`（按这次运行自身的全部 case 现算，不受窗口与 record/live 过滤；`hallucination_rate` 取 `1 - passed/(passed+failed)`，其余取通过率；`metrics=[]` 的 case 不参与）+ cases（按 `attempt, scenario` 升序，最多 500，`cases_truncated`，每条带 `tags`）；`graders` 的 `details` 每条截 256 字、最多 5 条。

- [ ] **Step 3: 更新 docs/v3/14 与 OpenAPI/类型**

§6.15 加 2 行，总数改 198；重生成 OpenAPI 与类型并跑两个 check。

- [ ] **Step 4: 提交**

```bash
git add maa_api/api/routers/insights.py maa_api/api/schemas/insights.py maa_api/db/repositories/evals.py tests/api/test_eval_runs_router.py web/openapi.json web/src/types/api.d.ts "docs/v3/14-现行接口清单.md"
git commit -m "feat(m99): 评测运行列表与详情端点"
```

### Task 8: 反馈端点（spec T8）

**Files:**
- Modify: `maa_api/api/routers/insights.py`（7、8 号端点）
- Modify: `maa_api/api/schemas/insights.py`
- Modify: `maa_api/db/repositories/feedback.py`
- Create: `tests/api/test_run_feedback_router.py`
- Modify: `web/openapi.json`、`web/src/types/api.d.ts`
- Modify: `docs/v3/14-现行接口清单.md`（§6.15 +2 行、总数 198→200）

**Interfaces:**
- Consumes: `RunFeedbackRepository`、`RUN_NOT_FOUND`、`FeedbackReason`。
- Produces: `PUT/GET /api/agent/runs/{run_id}/feedback`、`FeedbackIn/FeedbackOut`。

- [ ] **Step 1: 写失败测试**

```python
def test_put_creates_then_updates(client, seeded_run):
    ...


def test_get_returns_null_when_absent(client, seeded_run):
    assert client.get(f"/api/agent/runs/{seeded_run}/feedback").json() == {"item": None}


def test_unknown_run_404(client):
    ...


def test_invalid_rating_422(client, seeded_run):
    ...
```

- [ ] **Step 2: 跑测试确认先红 → 实现 → 全绿**

实现按 spec §4.5.4：PUT upsert（`created_at` 保留、`reasons` 去重、空白评论存 `None`），GET 返回 `{item: null}`，404 `RUN_NOT_FOUND`。

- [ ] **Step 3: 更新 docs/v3/14 与 OpenAPI/类型**

§6.15 加 2 行，总数改 200（这是最终值）；重生成并跑 check。

- [ ] **Step 4: 提交**

```bash
git add maa_api/api/routers/insights.py maa_api/api/schemas/insights.py maa_api/db/repositories/feedback.py tests/api/test_run_feedback_router.py web/openapi.json web/src/types/api.d.ts "docs/v3/14-现行接口清单.md"
git commit -m "feat(m99): 运行评价读取与提交端点"
```

### Task 9: 前端看板页（spec T9）

**Files:**
- Create: `web/src/features/insights/InsightsPage.tsx`
- Create: `web/src/features/insights/api.ts`
- Create: `web/src/features/insights/format.ts`
- Create: `web/src/features/insights/MetricCard.tsx`
- Create: `web/src/features/insights/chart/insight-chart.ts`
- Create: `web/src/features/insights/InsightsPage.test.tsx`
- Modify: `web/src/routes/router.tsx`（InsightsPage 路由）
- Modify: `web/src/components/layout/nav.ts`
- Modify: `web/src/api/keys.ts`
- Modify: `web/src/components/layout/layout.test.tsx`、`web/src/routes/more/more.test.tsx`（按实际路径）

**Interfaces:**
- Consumes: Task 4 的 5 个端点与生成类型；`ChartCard`（接受 `ChartCardData`）、`ChartCardData` 类型、`ViewStats` 的 KPI 网格样式、`ViewTable` 的表格样式、`Section`、`SegmentedControl`、`Chip`、`states`、`InfoHint`；`keys` 模板。
- Produces: `/more/insights` 页面、`MetricCard`、`keys.agent.insights*`、`format.ts` 的格式化函数、`chart/insight-chart.ts` 的两个映射函数。

- [ ] **Step 1: 写页面的失败测试**

`InsightsPage.test.tsx` 用 `features/console/test-utils.tsx` 的 `createConsoleApi`/`renderAt`（照 `features/artifacts/ArtifactsPage.test.tsx`），为 5 个新端点加 stub 分支，mock echarts 四模块（照 `features/costs/costs.test.tsx`）：

```tsx
it('renders seven metric cards grouped by scope', async () => { ... })
it('shows empty-data hints for offline metrics', async () => { ... })
it('switches window and refetches', async () => { ... })
it('renders evaluation methods', async () => { ... })
```

- [ ] **Step 2: 跑测试确认先红**

```bash
cd web && pnpm test --run src/features/insights > /tmp/m99-web-t9-red.log 2>&1; echo $?; tail -n 20 /tmp/m99-web-t9-red.log
```

Expected: 非 0，模块不存在。

- [ ] **Step 3: 实现页面、key、格式化与图表映射**

按 spec §4.6 的 1–10 项实现；`api.ts` 里 `queryOptions` 用生成的 `api.GET`；`format.ts` 放百分比（1 位小数）、成本（复用 `features/runs/format.ts` 的 `formatMicros`）、证据徽章文案、空态提示、`FEEDBACK_REASONS`；`chart/insight-chart.ts` 实现 `buildTrendChartData(points, unit) -> ChartCardData` 与 `buildBreakdownChartData(items, unit) -> ChartCardData`，交给 `ChartCard` 渲染；不改 `ChartCardData`/`EChartSurface`/`buildChartOption`。

- [ ] **Step 4: 更新导航、路由与钉死测试**

`secondaryNav` 在「成本」后插「能力效果」（`Gauge` 图标）；`routeConfig` 加两行（本任务只加看板路由，详情路由在 Task 10）；同步 `layout.test.tsx` 的侧栏标签数组与 `more.test.tsx` 的链接/受保护路由断言。

- [ ] **Step 5: 跑测试与 typecheck**

```bash
cd web && pnpm typecheck && pnpm test --run > /tmp/m99-web-t9.log 2>&1; echo $?; tail -n 6 /tmp/m99-web-t9.log
```

Expected: `0`。

- [ ] **Step 6: 提交**

```bash
git add web/src/features/insights web/src/routes/router.tsx web/src/components/layout/nav.ts web/src/api/keys.ts web/src/components/layout/layout.test.tsx web/src/routes/more/more.test.tsx
git commit -m "feat(m99): 能力效果看板页"
```

### Task 10: 前端评测详情页（spec T10）

**Files:**
- Create: `web/src/features/insights/EvalRunDetailPage.tsx`
- Create: `web/src/features/insights/EvalRunDetailPage.test.tsx`
- Modify: `web/src/routes/router.tsx`（详情路由）
- Modify: `web/src/features/insights/api.ts`（evalRun queryOptions）

**Interfaces:**
- Consumes: Task 7 的端点与类型、`ViewStats` 的 KPI 网格样式、`Page`/`states`。
- Produces: `/more/evals/:runId` 页面。

- [ ] **Step 1: 写失败测试**

```tsx
it('renders summary metric rates and cases', async () => { ... })
it('shows truncation alert', async () => { ... })
it('shows not-found error', async () => { ... })
```

- [ ] **Step 2: 跑测试确认先红 → 实现 → 全绿 → 提交**

实现按 spec §4.6 的评测详情页：用例表用自定义 `<table>`（`ViewTable` 只接受视图 schema 且无点击/展开），行内 `<details>` 展开 graders/error；提交：

```bash
git add web/src/features/insights web/src/routes/router.tsx
git commit -m "feat(m99): 评测详情页"
```

### Task 11: 运行详情评价入口（spec T11）

**Files:**
- Modify: `web/src/features/runs/RunDetailPage.tsx`（概要卡后插 `FeedbackSection`）
- Modify: `web/src/features/insights/FeedbackSection.tsx`
- Create: `web/src/features/insights/FeedbackSection.test.tsx`
- Modify: `web/src/features/runs/runs.test.tsx`（已有，追加断言）

**Interfaces:**
- Consumes: Task 8 的反馈端点与类型、`SegmentedControl`/`Chip`/`Textarea`/`toast`；`keys.agent.runFeedback`。
- Produces: 运行详情页的评价区块；`usePutRunFeedback` mutation 与缓存失效。

- [ ] **Step 1: 写失败测试**

```tsx
it('submits rating and shows success', async () => { ... })
it('prefills existing feedback', async () => { ... })
it('shows validation error', async () => { ... })
```

- [ ] **Step 2: 跑测试确认先红 → 实现 → 全绿**

实现按 spec §4.6 的反馈组件；提交：

```bash
git add web/src/features/runs/RunDetailPage.tsx web/src/features/insights/FeedbackSection.tsx web/src/features/insights/FeedbackSection.test.tsx web/src/features/runs/runs.test.tsx
git commit -m "feat(m99): 运行详情评价入口"
```

### Task 12: 文档同步、验收与合并（spec T12）

**Files:**
- Modify: `docs/v3/01-总览与范围.md`、`02-系统架构.md`、`03-数据模型.md`、`04-接口与事件协议.md`、`10-可观测与评测.md`、`11-前端指挥台.md`、`12-实施路线与里程碑.md`、`13-决策记录.md`、`14-现行接口清单.md`、`15-开放API接入指南.md`、`README.md`、`specs/README.md`、`specs/todo/README.md`（如需要）、`web/openapi.json`、`web/src/types/api.d.ts`、`config.template.yaml`（确认无变化）
- Create: `docs/v3/ledgers/m99-capability-insights.md`

**Interfaces:**
- Consumes: Task 1–11 的全部产出与基线记录。
- Produces: 同步后的契约文档、台账、合并提交与 `v3-m99` tag。

- [ ] **Step 1: 按 spec §9 逐项同步文档**

顺序：`03`（迁移/表/枚举/保留、M91–M98 建议号顺延）→ `04`（9 个端点、错误码、无配置变化）→ `10`（§8 指标口径，§6 场景与发布）→ `11`（导航、路由、key、管理页、文案）→ `02`（模块布局与路由清单）→ `12`（M99 行与顺序偏离）→ `13 §8.186 起` → `14` 总校（错误码 174/173、端点 200、小节计数）→ `01` → `README`/`specs/README` → 运行 `.venv/bin/python scripts/generate_api_guide.py` 重新生成 `15-开放API接入指南.md` → `config.template.yaml` 确认。

- [ ] **Step 2: 跑 §7 全部验收**

```bash
.venv/bin/python -m pytest -q > /tmp/m99-pytest.log 2>&1; echo $?; tail -n 3 /tmp/m99-pytest.log
.venv/bin/python scripts/dump_openapi.py && .venv/bin/python scripts/dump_openapi.py --check; echo $?
cd web && pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build; echo $?; cd ..
.venv/bin/python scripts/frontend_smoke.py; echo $?
.venv/bin/python scripts/agent_smoke.py; echo $?
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay; echo $?
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay; echo $?
.venv/bin/python scripts/generate_api_guide.py && .venv/bin/python scripts/generate_api_guide.py --check; echo $?
.venv/bin/python scripts/check_doc_links.py; echo $?
git diff --check
```

Expected: 全 `0`；frontend_smoke 与 agent_smoke 末行 `SMOKE OK`；评测 `5/5`、`26/26`、`EVAL OK`；doc links `BROKEN 0`。

- [ ] **Step 3: 手工验收**

按 spec §7 第 7、8 条在本地服务上操作一次：评测 `--publish` 后看板出现评测行；评价一条运行 4 分后满意度卡 `n=1`；改成 2 分后覆盖、`n` 仍为 1。把截图或 API 返回记进台账。没有 key 时不跑 live，记「未跑」。

- [ ] **Step 4: 写台账**

`docs/v3/ledgers/m99-capability-insights.md` 按 [specs/README §4](./../specs/README.md) 的模板：分支、基点、合并提交、tag；做了什么；验证证据表（命令与结果）；Rulings I made；偏离（迁移号/场景数/未跑 live）；遗留。

- [ ] **Step 5: 提交与合并**

```bash
git add docs/v3 web/openapi.json web/src/types/api.d.ts
git commit -m "docs(m99): 契约同步、台账与状态更新"
git checkout dev
git merge --no-ff capability-insights -m "merge: 能力效果看板（M99，见 docs/v3/13 §8.186 起）"
git tag v3-m99
git branch -d capability-insights
git status -sb
```

- [ ] **Step 6: 合并后复验**

在 `dev` 上重跑：全量 pytest；`--suite all --mode replay`；`dump_openapi --check` 与 `pnpm gen:api:check`。结果写进台账最后一行。

## 台账模板

```markdown
# M99 能力效果看板（台账）

- 分支：`capability-insights`；基点：`dev` = `<commit>`；合并：`<merge commit>`；tag：`v3-m99`
- spec：[m99-capability-insights](../specs/m99-capability-insights.md)
- 计划：[m99-capability-insights](../plans/m99-capability-insights.md)

## 做了什么
- 三张表、9 个端点、1 个错误码；7 个指标目录与聚合；评测发布；8 个新场景；看板、评测详情、评价入口。

## 验证证据
| 命令 | 结果 |
|---|---|
| 后端全量 pytest | `0`，`<counts>` |
| `--suite all --mode replay` | regression 5/5、capability 26/26、EVAL OK |
| OpenAPI / gen:api check | 两个 `0` |
| 前端 typecheck / test / build | `0` / `<counts>` / `0` |
| `frontend_smoke` / `agent_smoke` | 两个 `SMOKE OK` |
| doc links | `BROKEN 0` |

## Rulings I made
- （实现细节自行裁决的条目）

## 偏离（已记 13 §8.186 起）
- M99 先于 M91 执行，迁移号 0043；M91–M98 建议号顺延。
- 评测场景 23 → 31；capability 基线 18/18 → 26/26。
- 没有模型 key，未跑 capability live；以 replay 与既有台账基线为准。

## 遗留
- 在线 LLM 判分、ticket 级反馈、A/B 指标、历史评测导入。
```
