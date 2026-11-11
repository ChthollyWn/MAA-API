# 模型峰谷计价 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让模型档案按 DeepSeek 官方峰谷规则计费：工作日北京时间 9–12、14–18 用高峰价，其余用空闲价（空闲价可留空回退高峰价），并把峰谷价配置入口（`default` 档案用 `llm.*`，自定义档案用三列）接到设置页、配置模板与成本账本。

**Architecture:** 先加 `model_profile` 三列可空空闲价与七个 `llm.*` 配置键；再把 `ResolvedProfile`/档案 API/默认档案合成打通；成本引擎新增“北京时间 + 工作日窗口 + 节假日列表”的取价判定，`CostLedger` 在记账时解析一次；最后前端表单与文档/验收。历史账本不回填。

**Tech Stack:** FastAPI、Pydantic 2、SQLModel/SQLite、Alembic、pytest、`zoneinfo`；React 19、TanStack Query、vitest；不新增依赖。

**Spec:** [docs/v3/specs/offpeak-pricing.md](../specs/offpeak-pricing.md)（§0、§4、§5 T1–T6、§6、§7、§9、§11）

## Global Constraints

- 非里程碑维护性补丁：分支 `offpeak-pricing`，从 `dev` 新建；完成后 `git merge --no-ff` 回 `dev`、删分支；**不 push、不打 M tag**。
- 不使用 worktree；直接在现有检出上切分支。
- 迁移：`0044_offpeak_prices`，`down_revision` = `0043_capability_insights`；只加三列可空 float，不回填。
- 计数：配置键 144 → **151**（config-only 仍 13）；业务表 61、HTTP 端点 200（agent 97）、错误码 174、枚举、工具、评测场景 31 均不变；迁移 head 0043 → 0044。
- 峰谷规则写死为 DeepSeek 官方规则：`Asia/Shanghai`；周一至周五 `[09:00,12:00)` 与 `[14:00,18:00)` 高峰；节假日列表 `llm.peak_holidays` 全天空闲；空闲价缺省回退高峰价；`at=None` 时按高峰价（保持既有调用方语义）。
- 历史 `cost_ledger`/`agent_run` 不回填；`GET /api/agent/costs` 与 M99 看板只汇总 micros，不改口径。
- 不新增依赖、端点、错误码、表、枚举、SSE/WS；`config.template.yaml` 仅加 7 键与注释。
- 文档计数随任务增量更新：改 OpenAPI 的任务必须重生成 `web/openapi.json`/`web/src/types/api.d.ts`，`docs/v3/14` 计数不变（端点 200/错误码 174）。
- 一律 `.venv/bin/python`；本机没有 `rg`，用 `grep -rn`；长输出重定向 `/tmp` 只看尾部与退出码。
- 提交格式 `feat/offpeak: …`、`test/offpeak: …`、`docs/offpeak: …`；文档简体中文。
- 遇到 spec §10 的情形停下来报告；spec 没写到的实现细节按最小改动自行决定，写进台账「Rulings I made」。

## 开工基线（Task 1 实测后回填）

- `dev` 必须 = M99 合并与台账之后（写作时 `31c1fa7`）；`git rev-parse --short dev` 记录，计划与台账写实测值。
- 最近 tag `v3-m99`；迁移 head `0043_capability_insights`；配置键 `144 13`。
- 全量 pytest 基线：exit 1，仅 3 条真实活动资源包环境失败（`test_activity_index` 2 + `test_stage_layout` 1）；`test_incidents` 顺序 flake 若出现记台账，不算失败。
- OpenAPI `--check` 0、`pnpm gen:api:check` 0；`tests/domain/test_route_inventory.py`、`tests/domain/test_errors.py` 绿。
- 开发库 `resource/maa_api.db` 当前迁移 head 0043；（迁移验证前先备份）。

## §0 前置检查

照 spec §0 的 15 行原样执行，不重写；第 4–7 条以 Task 1 实测为准。

## 接缝裁决

实现时按这张表，不再另找入口。

| 接缝 | 裁决 |
|---|---|
| 峰谷判定 | 放 `maa_api/agent/llm/cost.py`：`PEAK_TIMEZONE = ZoneInfo("Asia/Shanghai")`、`PEAK_WINDOWS`、`parse_peak_holidays(raw) -> frozenset[date]`、`is_peak_time(at, *, holidays=()) -> bool`；naive `at` 按 UTC 解释后再转北京时间；`at=None` 时按高峰价。 |
| 取价回退 | 逐项回退：空闲价 `None` → 对应高峰价；高峰价 `None` → 0（既有行为）。空闲价 0 在自定义档案里是“免费”，在 `default` 档案的 settings 里 0 表示“未配置 → None”。 |
| `compute_cost_micros` | 签名改为 `(usage, profile, *, at: datetime \| None = None, holidays: Collection[date] = ())`；既有位置参数调用不变。 |
| `CostLedger` | `__init__` 加 `settings_provider: Callable[[], Settings] = get_settings`；`record` 加 `at: datetime \| None = None`；记账前 `parse_peak_holidays(self._settings().llm.peak_holidays)`；先算成本再开事务（与现状一致）。`main.py` 不改。 |
| 默认档案价格 | `_synthesize_default`：peak 三价取 `settings.llm.price_*`（float）；offpeak 三价取 settings，**值为 0 → None**；`_default_view` 同样。`default` 仍只读、不入表。 |
| 自定义档案 | `model_profile` 加三列 nullable；`ModelProfileCreate/Update/View` 与 `_view_from_row`/`_resolved_from_row`/`create`/`update` 同步；`Update` 用 `model_fields_set` 的既有规则，显式 `null` 表示清空。 |
| 配置键 | 7 个键在 `LLMSettings` + `SETTING_KEYS` + `settings_schema._FIELDS` + `config.template.yaml` + `tests/test_settings.py` 的 `ENV_OVERRIDES` 同步；全部热设置（`hot_action="none"`），不新增 config-only。 |
| 迁移 | `op.add_column` 三个 `sa.Float(), nullable=True`；`downgrade` 反序 `drop_column`；SQLite 3.35+ 原生支持，不 batch。 |
| 前端 | `ProfileEditorSheet` 的 `ProfileForm`/`initialForm`/`commonFields` 加三个字符串字段；提交走 `optionalNumber`；不新增设置页区块（通用设置表单会渲染 `llm.*` 新键）。 |
| 文档计数 | 本补丁不改端点/错误码/表数；只改配置键 151/13、迁移号与列；`docs/v3/14` 只跑门禁不改表。T3 后 OpenAPI 与类型快照必须同步。 |

## Review Focus

1. **峰谷边界与回退**：北京时间 08:59:59/09:00/12:00/13:59:59/14:00/18:00、周末、节假日、naive/aware、空谷价回退、`at=None` 回归。`tests/agent/llm/test_cost.py` 的边界用例钉住。
2. **默认档案取价**：`default` 的 6 个价格字段来自 `llm.*`，offpeak 0 → None；自定义档案 `null` 清空。`tests/agent/llm/test_profiles.py` 与模型档案路由测试钉住。
3. **账本一致性**：`CostLedger.record(at=...)` 写入的 `cost_micros` 等于当次峰谷成本，且累计进 `agent_run`；历史行不变。`test_cost.py` 的账本用例钉住。
4. **配置与类型同步**：7 个键计数 151/13、`_FIELDS` 渲染、OpenAPI/`api.d.ts` 同步、前端表单 round-trip。`tests/test_settings.py`、`pnpm gen:api:check`、`models.test.tsx` 钉住。
5. **迁移可逆**：三列加/删后既有列与数据不变；`alembic downgrade -1` 后再 upgrade 为空列。迁移测试钉住。

---

### Task 1: 分支、§0 基线、spec 与计划入库

**Files:**
- Create: `docs/v3/specs/offpeak-pricing.md`（本 spec 已写好；复制进分支）
- Create: `docs/v3/plans/offpeak-pricing.md`（本计划）
- 不改代码

**Interfaces:**
- Consumes: `dev` 的 `31c1fa7`、tag `v3-m99`、迁移 head `0043_capability_insights`。
- Produces: `offpeak-pricing` 分支、基线记录（commit、head、配置键、测试日志）、后续任务可用的已提交 spec 与计划。

- [ ] **Step 1: 切分支并记录基线**

```bash
git checkout dev
git rev-parse --short dev
git log -1 --format='%H %s'
git checkout -b offpeak-pricing
git status -sb
git tag -l 'v3-*' | sort | tail -1
ls maa_api/db/migrations/versions | grep -E '^[0-9]{4}_' | sort | tail -1
```

Expected: 第一行 `## offpeak-pricing`；tag `v3-m99`；head `0043_capability_insights.py`。

- [ ] **Step 2: 跑 spec §0 全部 15 条检查**

逐条执行；把实测 commit、配置键、迁移 head、全量 pytest 日志、`test_incidents` flake 情况记进 `/tmp/offpeak-baseline.md` 与台账草稿。

- [ ] **Step 3: 提交 spec 与计划**

```bash
git add docs/v3/specs/offpeak-pricing.md docs/v3/plans/offpeak-pricing.md
git commit -m "docs(offpeak): 峰谷计价维护性补丁规格与实施计划"
```

Expected: `git log --oneline -1` 是本提交；`git status -sb` 干净。

### Task 2: 迁移与模型字段（spec T2）

**Files:**
- Modify: `maa_api/db/models.py`（`ModelProfile` 加三列）
- Create: `maa_api/db/migrations/versions/0044_offpeak_prices.py`
- Create: `tests/db/test_offpeak_prices_migration.py`

**Interfaces:**
- Consumes: 迁移模板 `tests/db/test_capability_insights_migration.py`、`0026/0033` 的可空列写法。
- Produces: `ModelProfile.price_input_offpeak/price_cached_input_offpeak/price_output_offpeak: float | None = None`；迁移 `0044_offpeak_prices`。

- [ ] **Step 1: 写迁移的失败测试**

`tests/db/test_offpeak_prices_migration.py` 照 `tests/db/test_capability_insights_migration.py`：`tmp_path` + `_config(db_path)` + `alembic.command.upgrade`，不碰 `resource/maa_api.db`。

```python
PREVIOUS_REVISION = "0043_capability_insights"
HEAD_REVISION = "0044_offpeak_prices"
EXPECTED_NEW_COLUMNS = {
    "price_input_offpeak": ("FLOAT", True),
    "price_cached_input_offpeak": ("FLOAT", True),
    "price_output_offpeak": ("FLOAT", True),
}


def test_upgrade_adds_three_nullable_price_columns(tmp_path):
    ...


def test_downgrade_drops_them_and_reupgrade_is_empty(tmp_path):
    ...
```

- [ ] **Step 2: 跑测试确认先红**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/db/test_offpeak_prices_migration.py > /tmp/offpeak-t2-red.log 2>&1; echo $?; tail -n 15 /tmp/offpeak-t2-red.log
```

Expected: 非 0（`Can't locate revision 0044` 或列不存在）。

- [ ] **Step 3: 加模型字段与迁移**

按 spec §4.1 写三列（放在 `price_output` 后）与迁移；`down_revision = "0043_capability_insights"`；`upgrade` 三个 `op.add_column`，`downgrade` 反序 `op.drop_column`；模块 docstring 写清“只加可空列、不回填、SQLite 原生 ADD/DROP COLUMN”。

- [ ] **Step 4: 跑测试确认全绿**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/db/test_offpeak_prices_migration.py tests/db/test_models.py > /tmp/offpeak-t2.log 2>&1; echo $?; tail -n 2 /tmp/offpeak-t2.log
```

Expected: `0`。迁移可逆验证（先备份 `resource/maa_api.db`）：

```bash
.venv/bin/alembic upgrade head && .venv/bin/alembic downgrade -1 && .venv/bin/alembic upgrade head; echo $?
```

Expected: `0`，head 到 `0044_offpeak_prices`。

- [ ] **Step 5: 提交**

```bash
git add maa_api/db/models.py maa_api/db/migrations/versions/0044_offpeak_prices.py tests/db/test_offpeak_prices_migration.py
git commit -m "feat(offpeak): model_profile 增加空闲时段价格列"
```

### Task 3: 设置与档案字段（后端，spec T3）

**Files:**
- Modify: `maa_api/settings.py`、`maa_api/services/settings_schema.py`、`maa_api/agent/llm/profiles.py`、`config.template.yaml`
- Modify: `tests/agent/llm/test_profiles.py`、`tests/test_settings.py`、`tests/api/` 的模型档案路由测试（若存在）
- Modify: `web/openapi.json`、`web/src/types/api.d.ts`（重新生成）

**Interfaces:**
- Consumes: Task 2 的三个列；`LLMSettings`/`SETTING_KEYS`/`_FIELDS`；`ModelProfileService` 的 `_synthesize_default`/`_view_from_row`/`_resolved_from_row`/`create`/`update`。
- Produces: `llm.price_input` 等 7 个设置键（默认 0 / 空串）；`ResolvedProfile`/`ModelProfileView`/`ModelProfileCreate`/`ModelProfileUpdate` 的三个 `*_offpeak` 字段；`default` 档案的峰谷价来自 settings。

- [ ] **Step 1: 写设置的失败测试**

`tests/test_settings.py` 加：

```python
def test_llm_price_settings_parse_from_config_and_env(tmp_path, monkeypatch):
    ...


def test_peak_holidays_defaults_empty():
    ...
```

以及 `ENV_OVERRIDES` 的 7 项（`MAA_LLM_PRICE_INPUT` 等）。

`tests/agent/llm/test_profiles.py` 加：

```python
def test_default_profile_prices_come_from_settings(...): ...
def test_default_profile_offpeak_zero_falls_back_to_none(...): ...
def test_create_profile_roundtrips_offpeak_prices(...): ...
def test_update_profile_can_clear_offpeak_prices(...): ...
```

- [ ] **Step 2: 跑测试确认先红**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/test_settings.py tests/agent/llm/test_profiles.py > /tmp/offpeak-t3-red.log 2>&1; echo $?; tail -n 15 /tmp/offpeak-t3-red.log
```

Expected: 非 0（字段/键不存在）。

- [ ] **Step 3: 实现设置、档案字段与服务**

按 spec §4.2 第 1–4 条、§4.4：`LLMSettings` 加 7 字段与 `SETTING_KEYS`；`_FIELDS` 加 7 条；`profiles.py` 加 `ResolvedProfile`/`ModelProfileView`/`Create`/`Update` 三字段，`_synthesize_default` 用 settings（offpeak 0→None），`_default_view` 同，`_view_from_row`/`_resolved_from_row`/`create`/`update` 同步；`config.template.yaml` 加 7 键与注释。不要写峰谷判定逻辑（属 Task 4）。

- [ ] **Step 4: 更新路由测试与快照**

模型档案路由测试若存在，补 wire 三字段断言；然后：

```bash
.venv/bin/python scripts/dump_openapi.py
.venv/bin/python scripts/dump_openapi.py --check; echo $?
cd web && pnpm gen:api && pnpm gen:api:check; echo $?; cd ..
```

Expected: 全 `0`。

- [ ] **Step 5: 跑后端测试确认全绿**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/llm tests/test_settings.py tests/api tests/domain/test_route_inventory.py tests/domain/test_errors.py > /tmp/offpeak-t3.log 2>&1; echo $?; tail -n 2 /tmp/offpeak-t3.log
```

Expected: `0`（`tests/api` 全量，模型档案 wire 与计数都覆盖）。

- [ ] **Step 6: 提交**

```bash
git add maa_api/settings.py maa_api/services/settings_schema.py maa_api/agent/llm/profiles.py config.template.yaml tests/agent/llm/test_profiles.py tests/test_settings.py tests/api web/openapi.json web/src/types/api.d.ts
git commit -m "feat(offpeak): 档案与 default 支持峰谷价字段与设置键"
```

### Task 4: 成本引擎（spec T4）

**Files:**
- Modify: `maa_api/agent/llm/cost.py`
- Modify: `maa_api/agent/harness/builtin_middleware.py`
- Modify: `tests/agent/llm/test_cost.py`、`tests/agent/trace/test_trace_middleware.py`

**Interfaces:**
- Consumes: Task 3 的 `ResolvedProfile` 三字段、7 个 `llm.*` 配置键。
- Produces: `PEAK_TIMEZONE`、`PEAK_WINDOWS`、`parse_peak_holidays`、`is_peak_time`、扩展后的 `compute_cost_micros`、`compute_cost_micros_at`（settings 入口）、`CostLedger(settings_provider=...)` 与 `record(at=...)`；`TraceMiddleware` 的 `llm.chat` span 改走 `compute_cost_micros_at`。

- [ ] **Step 1: 写成本引擎的失败测试**

`tests/agent/llm/test_cost.py` 加（固定 UTC 时刻，避免依赖运行日）：

```python
def test_peak_windows_boundaries():
    # 北京时间 = UTC+8：01:00 UTC = 09:00 +08，03:00 UTC = 11:00，04:00 = 12:00，06:00 = 14:00，10:00 = 18:00
    ...


def test_weekend_is_offpeak():
    ...


def test_holiday_is_offpeak():
    ...


def test_offpeak_prices_fallback_per_component():
    ...


def test_compute_without_at_keeps_peak_semantics():
    ...


def test_parse_peak_holidays_skips_invalid():
    ...


def test_ledger_records_offpeak_cost():
    ...
```

账本用例用 `CostLedger(factory)` 的 `record(..., at=datetime(2026, 11, 11, 1, 0))`（北京时间 09:00 高峰）与 `at=datetime(2026, 11, 11, 4, 0)`（12:00 空闲）分别断言成本；`test_ledger_records_offpeak_cost` 断言账本行与 `agent_run.cost_micros` 等于当次成本。

- [ ] **Step 2: 跑测试确认先红**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/llm/test_cost.py > /tmp/offpeak-t4-red.log 2>&1; echo $?; tail -n 15 /tmp/offpeak-t4-red.log
```

Expected: 非 0。

- [ ] **Step 3: 实现峰谷取价、账本与 trace 接线**

按 spec §4.2 第 3 条、第 5 条与 §4.5：加常量与三个 helper；`compute_cost_micros` 加 `at`/`holidays` 关键字参数；新增 `compute_cost_micros_at`（`at` 缺省 utcnow、节假日取 settings）；`CostLedger.__init__` 加 `settings_provider`，`record` 用 helper；`TraceMiddleware` 的 `llm.chat` span 成本改用 `compute_cost_micros_at(usage, profile, at=end)`。`main.py` 不改。

- [ ] **Step 4: 跑测试确认全绿**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/llm/test_cost.py tests/agent/llm/test_budget.py tests/agent/llm/test_usage.py > /tmp/offpeak-t4.log 2>&1; echo $?; tail -n 2 /tmp/offpeak-t4.log
```

Expected: `0`（既有成本/预算用例不回归）。

- [ ] **Step 5: 提交**

```bash
git add maa_api/agent/llm/cost.py tests/agent/llm/test_cost.py
git commit -m "feat(offpeak): 成本账本按北京时间峰谷取价"
```

### Task 5: 前端档案表单（spec T5）

**Files:**
- Modify: `web/src/features/models/ProfileEditorSheet.tsx`、`web/src/features/models/models.test.tsx`（如需要 `labels.ts`）

**Interfaces:**
- Consumes: Task 3 生成类型中的三个 `*_offpeak` 字段。
- Produces: 表单三个输入与提交 body 的三字段；`default` 档案只读价格展示（若已有展示）。

- [ ] **Step 1: 写表单的失败测试**

`models.test.tsx` 加：

```tsx
it('submits offpeak prices in create and update payloads', async () => { ... })
```

用既有测试工具（`features/settings`/`models.test.tsx` 的 mock 后端）；断言创建/更新请求体含 `price_input_offpeak` 等三字段，回填时显示原值。

- [ ] **Step 2: 跑测试确认先红**

```bash
cd web && pnpm test --run src/features/models > /tmp/offpeak-t5-red.log 2>&1; echo $?; tail -n 20 /tmp/offpeak-t5-red.log
```

Expected: 非 0。

- [ ] **Step 3: 实现表单字段**

按 spec §4.6：`ProfileForm` 加三个字符串字段，`initialForm`/`commonFields` 同步；在现有价格输入后加「空闲时段价格」三个输入（label 与说明见 spec）。`default` 档案的只读价格展示若已展示 `price_*`，同步显示 `*_offpeak`。

- [ ] **Step 4: 跑 typecheck 与全量前端测试**

```bash
cd web && pnpm typecheck; echo $?
cd web && pnpm test --run > /tmp/offpeak-t5.log 2>&1; echo $?; tail -n 4 /tmp/offpeak-t5.log
```

Expected: 两个 `0`。

- [ ] **Step 5: 提交**

```bash
git add web/src/features/models
git commit -m "feat(offpeak): 档案编辑器支持空闲时段价格"
```

### Task 6: 文档同步、验收与合并（spec T6）

**Files:**
- Modify: `docs/v3/03-数据模型.md`、`04-接口与事件协议.md`、`10-可观测与评测.md`、`11-前端指挥台.md`、`12-实施路线与里程碑.md`、`13-决策记录.md`、`15-开放API接入指南.md`、`docs/v3/README.md`（如需）、`web/openapi.json`、`web/src/types/api.d.ts`
- Create: `docs/v3/ledgers/offpeak-pricing.md`

**Interfaces:**
- Consumes: Task 1–5 的全部产出与基线记录。
- Produces: 同步后的契约文档、台账、合并提交。

- [ ] **Step 1: 按 spec §9 逐项同步文档**

顺序：`03`（0044 行、M91–M98 建议号顺延 0045–0049、`model_profile` 三列与“高峰时段价”）→ `04`（模型档案三字段、§6 配置 7 行与 151/13 计数）→ `10`（§4 峰谷公式/时段/官方价表/历史不回填）→ `11`（设置页档案编辑器三输入、LLM 分组 7 键）→ `12`（M91–M98 迁移号顺延注）→ `13 §8.187` → `15` 重新生成 → `README` 如需。

- [ ] **Step 2: 跑 spec §7 全部验收**

```bash
.venv/bin/python -m pytest -q > /tmp/offpeak-pytest.log 2>&1; echo $?; tail -n 5 /tmp/offpeak-pytest.log
.venv/bin/python scripts/dump_openapi.py && .venv/bin/python scripts/dump_openapi.py --check; echo $?
cd web && pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build; echo $?; cd ..
.venv/bin/python scripts/frontend_smoke.py; echo $?
.venv/bin/python scripts/agent_smoke.py; echo $?
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay; echo $?
.venv/bin/python scripts/generate_api_guide.py && .venv/bin/python scripts/generate_api_guide.py --check; echo $?
.venv/bin/python scripts/check_doc_links.py; echo $?
git diff --check
```

Expected: 全 `0`；全量 pytest 仅 3 条 Task 0 登记的环境基线失败；`frontend_smoke`/`agent_smoke` 末行 `SMOKE OK`；regression `5/5`、`EVAL OK`；doc links `BROKEN 0`。

- [ ] **Step 3: 手工验证（不发起真实模型调用，除非用户同意）**

在临时/开发库上用 `compute_cost_micros` 与 `GET /api/agent/model-profiles` 的 `default` 项验证 6 个价格字段来自配置；不做真实付费调用（spec §7 第 9 条）。

- [ ] **Step 4: 写台账与提交**

`docs/v3/ledgers/offpeak-pricing.md` 按维护性补丁台账模板：分支、基点、合并提交占位、做了什么、验证证据、Rulings、偏离、遗留（历史不回填、节假日手工、规则写死）。提交：

```bash
git add docs/v3 web/openapi.json web/src/types/api.d.ts
git commit -m "docs(offpeak): 契约同步、台账与状态更新"
```

- [ ] **Step 5: 合并与复验**

```bash
git checkout dev
git merge --no-ff offpeak-pricing -m "merge: 模型峰谷计价（维护性补丁，见 docs/v3/13 §8.187）"
git branch -d offpeak-pricing
git status -sb
```

合并后在 `dev` 复跑：全量 pytest；`dump_openapi --check`；`pnpm gen:api:check`；`agent_eval --suite regression --mode replay`。把结果写进台账最后一行与最终汇报。

## 台账模板

```markdown
# 模型峰谷计价（台账）

- 分支：`offpeak-pricing`；基点：`dev` = `<commit>`；合并：`<merge commit>`
- spec：[offpeak-pricing](../specs/offpeak-pricing.md)
- 计划：[offpeak-pricing](../plans/offpeak-pricing.md)

## 做了什么
- `model_profile` 三列空闲价 + 迁移 0044；7 个 `llm.*` 配置键；默认档案峰谷价；成本引擎按北京时间的峰谷取价；台账 `record(at=...)`；前端档案编辑器三输入；文档同步。

## 验证证据
| 命令 | 结果 |
|---|---|
| 后端全量 pytest | `1`，仅 3 条环境基线失败 |
| OpenAPI / gen:api check | 两个 `0` |
| 前端 typecheck/test/build | `0` / `<counts>` / `0` |
| frontend_smoke / agent_smoke | 两个 `SMOKE OK` |
| regression replay | `5/5`、`EVAL OK` |
| generate_api_guide / doc links | `0` / `BROKEN 0` |
| git diff --check | `0` |

## Rulings I made
## 偏离（已记 13 §8.187）
## 遗留
- 历史成本不回填；节假日手工维护；规则写死 DeepSeek。
```
