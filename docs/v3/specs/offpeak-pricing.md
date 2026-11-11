# 模型峰谷计价（维护性补丁规格）

> 非里程碑维护性补丁：按维护性改造处理，不占用新里程碑编号，不调整 [12-实施路线与里程碑](../12-实施路线与里程碑.md) 的里程碑顺序。
> 本文是本次改造的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)，台账写 `docs/v3/ledgers/offpeak-pricing.md`。
> 用户口径（2026-11-11）：DeepSeek 官方对 `deepseek-flash` 实行峰谷定价（北京时间工作日 9:00–12:00、14:00–18:00 为高峰，其余时间为空闲时段，空闲价 = 高峰价的一半）；当前 `default` 档案价格为空，成本恒 0，且自定义档案只有一组固定价，无法表达峰谷。

## 0. 前置检查

本机没有 ripgrep，下表全部用 `grep`；长输出重定向到 `/tmp`，只看尾部与退出码。

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在分支上、工作区干净 | `git status -sb` | 第一行 `## offpeak-pricing`，没有其他行 |
| 2 | 基点 | `git rev-parse --short dev HEAD` | 两行相同；2026-11-11 写作时 `dev` = `31c1fa7`（M99 合并与台账之后） |
| 3 | 最近 tag | `git tag -l 'v3-*' \| sort \| tail -1` | `v3-m99` |
| 4 | 迁移 head | `ls maa_api/db/migrations/versions \| grep -E '^[0-9]{4}_' \| sort \| tail -1` | `0043_capability_insights.py`；本补丁取 `0044_offpeak_prices` |
| 5 | 配置键基线 | `.venv/bin/python -c "from maa_api.settings import SETTING_KEYS, CONFIG_ONLY_SETTING_KEYS; print(len(SETTING_KEYS), len(CONFIG_ONLY_SETTING_KEYS))"` | `144 13` |
| 6 | 端点/错误码基线 | `grep -n "共 200 条 HTTP 端点" docs/v3/14-现行接口清单.md`；`grep -n "ALL_CODE_COUNT = " tests/domain/test_errors.py` | 各 1 行；`174` |
| 7 | 业务表基线 | `grep -n "def test_metadata_has_exactly" tests/db/test_models.py` | 行内数字 `61` |
| 8 | 成本公式现状 | `grep -n "def compute_cost_micros" -A 20 maa_api/agent/llm/cost.py` | 1 处，签名 `(usage, profile)`，无时间参数 |
| 9 | 档案价格字段现状 | `grep -n "price_input" maa_api/agent/llm/profiles.py maa_api/db/models.py` | 各 1 处（`ResolvedProfile`/`ModelProfileCreate`/`ModelProfileUpdate`/`_view_from_row` 等） |
| 10 | 默认档案价格现状 | `grep -n "price_input=None" -A 3 maa_api/agent/llm/profiles.py` | `_synthesize_default` 与 `_default_view` 两处 |
| 11 | 设置里没有价格键 | `grep -n "llm.price" maa_api/settings.py maa_api/services/settings_schema.py config.template.yaml; echo $?` | 无输出，退出码 `1` |
| 12 | 前端档案表单 | `grep -n "price_input" web/src/features/models/ProfileEditorSheet.tsx` | 1 行（表单字段） |
| 13 | OpenAPI 与类型是当前值 | `.venv/bin/python scripts/dump_openapi.py --check; echo $?`；`cd web && pnpm gen:api:check; echo $?; cd ..` | 两个 `0` |
| 14 | 全量测试基线 | `.venv/bin/python -m pytest -q > /tmp/offpeak-pytest-base.log 2>&1; echo $?; tail -n 5 /tmp/offpeak-pytest-base.log` | `1`；只有 3 条真实活动资源包环境失败（`test_activity_index` 2 条、`test_stage_layout` 1 条）。`test_incidents` 的既有顺序 flake 若出现记台账、不算失败 |
| 15 | 决策记录尾部 | `grep -n "^### 8.18[5-9]" docs/v3/13-决策记录.md` | 最后一条是 §8.186；本补丁接 §8.187 |

第 4–7 条以 Task 0 实测为准；与期望不同只更新本规格与台账，不视为停止条件。

## 1. 背景与目标

DeepSeek 官方价格页（2026-11-11 抓取，[中文](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)）对 `deepseek-flash` 的定价（元/百万 tokens）：

| 项目 | 空闲时段 | 高峰时段 |
|---|---|---|
| 输入（缓存命中） | 0.02 | 0.04 |
| 输入（缓存未命中） | 1 | 2 |
| 输出 | 4 | 8 |

- 高峰：北京时间周一至周五（不含中国法定节假日）9:00–12:00、14:00–18:00；其余时段（含周末、节假日全天）为空闲时段；空闲价是高峰价的一半。
- 当前实现（M67）无法表达：`default` 档案价格固定为 `None`（由 `llm.*` 合成、只读），自定义档案只有 `price_input`/`price_cached_input`/`price_output` 一组价；`compute_cost_micros` 把 `None` 按 0 计，所以现有 86 次运行、219 行账本的成本全为 0。

做完能验证的结果：

1. `default` 档案的价格可由 `config.yaml` 或设置页的 `llm.price_*` 与 `llm.price_*_offpeak` 配置，当前单档案部署不必再建自定义档案即可得到非零成本。
2. 自定义模型档案（`model_profile` 表）增加三列空闲时段价格；新建/编辑/查看档案的 API 与设置页表单同步支持，缺省为 `null`（回退高峰价）。
3. 成本计算按**北京时间**判断时段：工作日 9:00–12:00、14:00–18:00 用高峰价，其余时段用空闲价（空闲价为空则回退高峰价）；`llm.peak_holidays` 可列出中国法定节假日，列表中日期全天按空闲时段。
4. `cost_ledger` 每行的 `cost_micros` 仍是**调用当时**的实际价格成本；`agent_run.cost_micros`、`GET /api/agent/costs` 与 M99 看板成本卡继续只做求和，不改口径；历史行不回填。
5. 前端模型档案编辑器可录入空闲时段价格；`default` 档案只读展示来自设置的峰谷价。
6. 不新增端点、错误码、表、枚举与依赖；业务表仍 61、端点仍 200、错误码仍 174；配置键 144 → 151（其中 config-only 仍 13）。

## 2. 非目标

- 不自动维护中国法定节假日日历：`llm.peak_holidays` 是手工维护的日期列表，默认空；未填的节假日按高峰价计（只会高估，不会低估）。
- 不做按 provider 可配置的时段规则：高峰时段固定为 DeepSeek 规则（北京时间工作日 9–12、14–18）；其他 provider 若没有峰谷价，把 `*_offpeak` 留空即退化为全天高峰价。
- 不导入/重算历史成本：`cost_ledger` 是 append-only，既有 219 行保持 0；不做一次性补算脚本。
- 不改币种与预算口径：`currency`、`BUDGET_CURRENCY`、`GET /api/agent/costs` 的行为不变。
- 不做 DeepSeek Search/Fetch API 的按次计费；不在本补丁里接入它们的用量。
- 不做提示缓存命中率的估算修正；只按上游返回的 `cached_tokens` 计费。
- 不改 `default` 档案的合成机制（仍不在表里、仍只读）；只把价格从“固定 None”改为“从 `llm.*` 读取”。
- 不新增依赖（时区用标准库 `zoneinfo`，不引入 `pytz`/节假日库）。

## 3. 相关文档与代码

必读：

- [10 §4、§8](../10-可观测与评测.md)：成本账本一致性、M99 看板成本口径（只读）。
- [03 §2、§4.2、§14](../03-数据模型.md)：迁移登记、`model_profile`/`cost_ledger` 列、保留策略。
- [04 §2.1、§6](../04-接口与事件协议.md)：模型档案端点、配置项总表与新增配置项规则。
- [11 §9](../11-前端指挥台.md)：设置页「模型与角色」分区。
- [13 §8.186](../13-决策记录.md)：当前最后一条决策；本补丁接 §8.187。
- [16-部署指南](../16-部署指南.md) 与 `config.template.yaml`：`llm` 段的注释写法。

代码入口（行号会漂移，用符号名定位）：

- `maa_api/agent/llm/cost.py`：模块 docstring 的计费公式、`_decimal`（约 46）、`compute_cost_micros`（约 64）、`CostLedger.__init__`（约 140）、`CostLedger.record`（约 155）、`CostLedger._write`（约 180）。
- `maa_api/agent/llm/profiles.py`：`ResolvedProfile`（约 131）、`ModelProfileView`（约 175）、`ModelProfileCreate`（约 211）、`ModelProfileUpdate`（约 250）、`_view_from_row`（约 300）、`_synthesize_default`（约 610）、`_resolved_from_row`（约 650）、`_default_view`（约 700）、`create`（约 755）、`update`（约 800）。
- `maa_api/settings.py`：`LLMSettings`（约 374）、`SETTING_KEYS`（约 79）、`CONFIG_ONLY_SETTING_KEYS`（约 302）。
- `maa_api/services/settings_schema.py`：`_FIELDS` 的 `llm.*` 行（约 117–129）。
- `maa_api/db/models.py`：`ModelProfile`（约 1130–1200）。
- `maa_api/db/migrations/versions/0043_capability_insights.py`：最新迁移；加列写法参考 `0026_tickets.py`/`0033_rewind.py` 的可空列。
- `config.template.yaml`：`llm:` 段（约 167）。
- `web/src/features/models/ProfileEditorSheet.tsx`（表单 `ProfileForm`/`initialForm`/`commonFields`）、`web/src/features/models/api.ts`、`web/src/features/models/models.test.tsx`。
- 测试：`tests/agent/llm/test_cost.py`、`tests/agent/llm/test_profiles.py`、`tests/api/`（模型档案路由测试，若存在）、`tests/test_settings.py`、`tests/db/test_models.py`、迁移测试模板 `tests/db/test_capability_insights_migration.py`。

## 4. 设计

### 4.1 数据模型

只给 `model_profile` 加三列，全部可空、无默认、不回填（既有行 `NULL` 表示“回退高峰价”）：

```python
    price_input_offpeak: float | None = Field(default=None)
    price_cached_input_offpeak: float | None = Field(default=None)
    price_output_offpeak: float | None = Field(default=None)
```

迁移 `maa_api/db/migrations/versions/0044_offpeak_prices.py`（`revision="0044_offpeak_prices"`，`down_revision="0043_capability_insights"`）：

```python
def upgrade() -> None:
    op.add_column("model_profile", sa.Column("price_input_offpeak", sa.Float(), nullable=True))
    op.add_column("model_profile", sa.Column("price_cached_input_offpeak", sa.Float(), nullable=True))
    op.add_column("model_profile", sa.Column("price_output_offpeak", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("model_profile", "price_output_offpeak")
    op.drop_column("model_profile", "price_cached_input_offpeak")
    op.drop_column("model_profile", "price_input_offpeak")
```

SQLite 3.35+ 支持原生 `ADD COLUMN`/`DROP COLUMN`，这三列没有约束、索引或触发器，不需要 batch 重建。03 §2 加迁移行；03 §4.2 的 `model_profile` 表加一行：

| `price_input_offpeak`、`price_cached_input_offpeak`、`price_output_offpeak` | float? | 空闲时段价格，语义同 `price_*`；`NULL` 表示逐项回退对应高峰价 |
| `price_*`（peak 三列） | 说明补一句：**高峰时段价格**（原来只写“每百万 token 的价格”） |

M91–M98 在 03 §2 与 12 §2 里的建议迁移号从 `0044`–`0048` 顺延为 `0045`–`0049`（本补丁先于它们执行），并在 03 §2 表格下注一句。

### 4.2 模块与文件

1. `maa_api/settings.py`

```python
class LLMSettings(BaseModel):
    ...
    #: 峰谷价（元/百万 token，docs/10 §4、M67 的成本口径）。
    price_input: float = Field(default=0.0, ge=0, le=100_000)
    price_cached_input: float = Field(default=0.0, ge=0, le=100_000)
    price_output: float = Field(default=0.0, ge=0, le=100_000)
    price_input_offpeak: float = Field(default=0.0, ge=0, le=100_000)
    price_cached_input_offpeak: float = Field(default=0.0, ge=0, le=100_000)
    price_output_offpeak: float = Field(default=0.0, ge=0, le=100_000)
    #: 中国法定节假日（逗号/空白分隔的 YYYY-MM-DD）；这些日期全天按空闲时段计价。
    peak_holidays: str = ""
```

`SETTING_KEYS` 追加这 7 个键（`llm.price_input` … `llm.peak_holidays`）；`CONFIG_ONLY_SETTING_KEYS` 不变。

2. `maa_api/services/settings_schema.py` 的 `_FIELDS`：`llm.price_*` 与 `llm.price_*_offpeak` 用 `dict(label=..., group="LLM", type="number", minimum=0, maximum=100000, hot_action="none")`，`llm.peak_holidays` 用 `dict(label="峰谷计价的法定节假日", group="LLM", type="string", description="逗号/空白分隔的 YYYY-MM-DD；这些日期全天按空闲时段计价", hot_action="none")`。

3. `maa_api/agent/llm/cost.py`

```python
from zoneinfo import ZoneInfo

#: DeepSeek 官方峰谷规则（docs/10 §4）：北京时间工作日两个高峰窗口。
PEAK_TIMEZONE = ZoneInfo("Asia/Shanghai")
PEAK_WINDOWS: tuple[tuple[time, time], ...] = (
    (time(9, 0), time(12, 0)),
    (time(14, 0), time(18, 0)),
)


def parse_peak_holidays(raw: str) -> frozenset[date]:
    """解析 ``llm.peak_holidays``；非法片段记 warning 后跳过。"""


def is_peak_time(at: datetime, *, holidays: Collection[date] = ()) -> bool:
    """``at`` 是 naive UTC（项目口径）或带时区时间；转成北京时间判断。"""


def compute_cost_micros(
    usage: Usage,
    profile: ResolvedProfile,
    *,
    at: datetime | None = None,
    holidays: Collection[date] = (),
) -> int:
    """``at`` 为空时按高峰价（保持既有调用方语义）；否则按峰谷取价。"""


def compute_cost_micros_at(
    usage: Usage,
    profile: ResolvedProfile,
    *,
    at: datetime | None = None,
    settings_provider: Callable[[], Settings] | None = None,
) -> int:
    """``compute_cost_micros`` 的“当前配置”入口：``at`` 缺省 utcnow，节假日取 settings。

    ``CostLedger.record`` 与 ``TraceMiddleware`` 的 ``llm.chat`` span 都走这里，
    保证 ``docs/v3/10 §4.1`` 的「``Σ cost_ledger`` = ``agent_run.cost_micros`` =
    ``Σ llm.chat maa.cost.micros``」在峰谷价下仍然成立。"""
```

选价规则（逐项回退）：`at` 为 `None` 或 `is_peak_time(at, holidays=...)` 为真 → 用 `price_input`/`price_cached_input`/`price_output`；否则用 `price_input_offpeak` 等，某项为 `None` 时回退对应高峰价。`price_input`/`price_output` 为 `None` 时按 0 计（既有行为），`price_cached_input` 为 `None` 时回退同一时段的输入价、不按 0 计。

`CostLedger.__init__` 加 `settings_provider: Callable[[], Settings] = get_settings`；`record(..., at: datetime | None = None)` 内：

```python
cost_micros = compute_cost_micros_at(
    usage,
    profile,
    at=at,
    settings_provider=self._settings_provider,
)
```

`compute_cost_micros_at` 内部在 `at` 缺省时取 `utcnow()`、用 `settings_provider()` 解析节假日；`settings_provider` 缺省为 `None`，调用时才解析成 `get_settings`（不在导入时绑定函数对象，测试可 monkeypatch 注入节假日）。`CostLedger` 构造处不传 `settings_provider` 时用默认 `get_settings`，`main.py` 无需改动。

4. `maa_api/agent/llm/profiles.py`

- `ResolvedProfile` 加三个 `float | None` 字段（放在 `price_output` 后）。
- `ModelProfileView` 加三个 `float | None` 字段；`ModelProfileCreate` 与 `ModelProfileUpdate` 各加三个 `float | None = Field(default=None, ge=0, le=100_000)`。
- `_view_from_row`、`_resolved_from_row`、`create`、`update` 的字段清单与赋值同步加三个字段。
- `_synthesize_default`：六个价格都取 `settings.llm.price_*`，**为 0 时置 `None`**（0 表示“未配置”：高峰输入/输出价 `None` 在引擎里按 0 计，高峰缓存价 `None` 回退输入价，空闲价 `None` 逐项回退高峰价）；不再把高峰价的 0 原样传下去，避免缓存命中按 0 计费。
- `_default_view`：同样从 settings 取 6 个价格字段（六个键的 0 都折成 `None`），`readonly=True` 不变。

5. `maa_api/agent/harness/builtin_middleware.py`：`TraceMiddleware` 的 `llm.chat` span 成本从 `compute_cost_micros(usage, profile)` 改为 `compute_cost_micros_at(usage, profile, at=end)`（`end` 即该 span 的结束时刻），节假日取默认 settings；这样 span 的 `maa.cost.micros`/`trace.cost_micros` 与账本在峰谷价下同口径。

6. 前端：`web/src/features/models/ProfileEditorSheet.tsx` 的 `ProfileForm` 加 `price_input_offpeak`/`price_cached_input_offpeak`/`price_output_offpeak`（字符串），`initialForm`/`commonFields` 同步；表单在现有三个价格输入后加一行「空闲时段价格」的三个输入，label 写「空闲时段输入价（缓存未命中）」等，占位/说明写「留空表示回退高峰价」。`models.test.tsx` 补一条新建/编辑时带回三个字段的断言。

7. `config.template.yaml` 的 `llm:` 段追加：

```yaml
  # 峰谷价（元/百万 token）。高峰：北京时间工作日 9:00-12:00、14:00-18:00；其余为空闲。
  # 0 表示未配置：输入/输出价按 0 计；缓存命中价未配置时回退同一时段的输入价；
  # 空闲价未配置时回退高峰价。
  # DeepSeek deepseek-flash 官方价示例见 docs/v3/10-可观测与评测.md §4.5。
  price_input: 0
  price_cached_input: 0
  price_output: 0
  price_input_offpeak: 0
  price_cached_input_offpeak: 0
  price_output_offpeak: 0
  # 中国法定节假日（逗号/空白分隔的 YYYY-MM-DD），这些日期全天按空闲时段计价
  peak_holidays:
```

### 4.3 接口

没有新端点、错误码与事件。既有模型档案端点的请求/响应增加三个可选字段：

| 方法 | 路径 | 变化 |
|---|---|---|
| GET `/api/agent/model-profiles` | `list_model_profiles` | 每项（含 `default`）多 `price_input_offpeak`、`price_cached_input_offpeak`、`price_output_offpeak`；`default` 的值来自 `llm.*`，只读 |
| POST `/api/agent/model-profiles` | `create_model_profile` | 请求体可选三字段（float，0–100000，可为 null） |
| GET `/api/agent/model-profiles/{name}` | `get_model_profile` | 同上 |
| PUT `/api/agent/model-profiles/{name}` | `update_model_profile` | 可部分更新三字段；`null` 表示清空（回退高峰价） |

`OpenAPI` 快照与 `web/src/types/api.d.ts` 重新生成；`docs/v3/14` 路由表与计数不变（200 条），错误码表不变。

### 4.4 配置项

7 个新键，全部为热设置（`hot_action="none"`，改完下一次模型调用生效；`peak_holidays` 下一次记账时解析）：

| 键 | 类型 | 默认 | 范围 | 说明 |
|---|---|---|---|---|
| `llm.price_input` | number | 0 | 0–100000 | 高峰时段输入价（缓存未命中），元/百万 token；0 表示未配置 |
| `llm.price_cached_input` | number | 0 | 0–100000 | 高峰时段输入价（缓存命中）；0 表示未配置，回退同一时段的输入价 |
| `llm.price_output` | number | 0 | 0–100000 | 高峰时段输出价；0 表示未配置 |
| `llm.price_input_offpeak` | number | 0 | 0–100000 | 空闲时段输入价（缓存未命中）；0 表示未配置，回退高峰价 |
| `llm.price_cached_input_offpeak` | number | 0 | 0–100000 | 空闲时段输入价（缓存命中）；0 表示未配置，逐项回退（缓存价缺失时再回退同时段输入价） |
| `llm.price_output_offpeak` | number | 0 | 0–100000 | 空闲时段输出价；0 表示未配置，回退高峰价 |
| `llm.peak_holidays` | string | 空 | — | 中国法定节假日日期，逗号/空白分隔的 `YYYY-MM-DD`；这些日期全天按空闲时段计价 |

配置键 144 → **151**；config-only 仍 13。`config.template.yaml` 按 §4.2 第 6 条加注释片段；`tests/test_settings.py` 的 `ENV_OVERRIDES` 加 7 项（`MAA_LLM_PRICE_INPUT` 等）。

### 4.5 行为与算法

- **时段判定**：`at` 为 naive datetime 时按 UTC（项目统一口径）解释，先转 `Asia/Shanghai`；带时区时间直接转换。高峰 = 周一至周五（`weekday() < 5`）、本地时间落在 `[09:00,12:00)` 或 `[14:00,18:00)`、且本地日期不在 `holidays` 集合里。周六/周日整天、节假日整天为空闲。
- **节假日解析**：`parse_peak_holidays("")` → 空集合；按逗号与空白切分；每项 `date.fromisoformat`；非法项 `logger.warning` 后跳过；重复忽略。
- **取价**：高峰/空闲各三元组；空闲项为 `None` 时逐项回退到高峰项；`price_input`/`price_output` 为 `None` 时按 0 计（与 M67 一致），`price_cached_input` 为 `None` 时回退同一时段的输入价、不按 0 计；`at=None` 时全部按高峰价（既有直接调用方与测试的语义不变）。
- **默认档案合成**：六个价格都取 `settings.llm` 的对应 float（默认 0.0），值为 0 时置 `None`（0 表示未配置：高峰输入/输出价按 0 计，高峰缓存价回退输入价，空闲价逐项回退高峰价）。`ModelProfileView` 的 `default` 项展示这 6 个值。
- **自定义档案**：三列可空；API 传 `null` 清空；不传保持原值（`ModelProfileUpdate` 的 `model_fields_set` 规则不变）。
- **trace 与账本一致**：`CostLedger.record` 与 `TraceMiddleware` 的 `llm.chat` span 都调用 `compute_cost_micros_at`；前者用记账时刻、后者用 span 结束时刻，两者都在同一次模型调用结束后数毫秒内，跨峰谷窗口边界的那一瞬间可能各取一个 tier（一次调用级差异），其余情况同值。
- **账本**：`CostLedger.record` 在计算成本前解析一次节假日；`cost_micros` 仍写入 `cost_ledger` 行并累计进 `agent_run`；`GET /api/agent/costs`、M99 看板的成本口径不变，只汇总已有 micros。
- 例：`deepseek-flash` 高峰价（输入 2、缓存 0.04、输出 8）下，prompt 1,000,000（cached 100,000）+ completion 100,000 的成本 = 900,000×2 + 100,000×0.04 + 100,000×8 = 2,604,000 micros（¥2.604）；空闲价（1/0.02/4）同用量 = 900,000×1 + 100,000×0.02 + 100,000×4 = 1,302,000 micros（¥1.302）。
- **边界情况**：

| 输入 → 期望 |
|---|
| `at` 恰为北京时间 09:00:00 → 高峰；08:59:59 → 空闲；12:00:00 → 空闲；13:59:59 → 空闲；14:00:00 → 高峰；18:00:00 → 空闲 |
| 周六/周日任意时刻 → 空闲 |
| `holidays` 含当天（即使是工作日 10:00） → 空闲 |
| `at` 带 `+08:00` 时区 → 按同一本地时刻判断；naive 按 UTC 解释 |
| 空闲价三项都为 `None` → 与只配高峰价完全一致（回归） |
| 空闲价只配输出价 → 输入/缓存项回退高峰价，输出用空闲价 |
| 高峰价全 `None`、空闲价有值 → 高峰时段成本 0、空闲时段按空闲价 |
| `llm.peak_holidays` 有语法错误日期 → 跳过该项并 warning，其余照常 |
| `default` 档案任一 `llm.price_*` 设置为 0 → 视作未配置（高峰输入/输出价按 0 计、高峰缓存价回退输入价、空闲价逐项回退高峰价），不当作免费 |
| 历史 `cost_ledger` 行 → 不改写 |
| 直接调用 `compute_cost_micros(usage, profile)`（不传 `at`） → 行为与旧版完全一致 |

### 4.6 前端

- `ProfileEditorSheet`：表单三个新输入（`price_input_offpeak`/`price_cached_input_offpeak`/`price_output_offpeak`），放在现有「价格」三个输入之后、`currency` 之前；label 文案：「空闲时段输入价（缓存未命中）」「空闲时段输入价（缓存命中）」「空闲时段输出价」，辅助说明「留空表示回退高峰价」。
- `default` 档案只读视图（列表/详情）若展示价格，则同时展示三组峰谷价（来自 settings）；没有价格展示的地方不新增区块。
- 不做设置页新的分组；`llm.price_*` 与 `llm.peak_holidays` 会由通用设置表单渲染在「LLM」分组（`_FIELDS` 已加）。
- 文案遵守 `docs/v3/11` §11 与 copy 规范：不用「仪表盘」，无感叹号/emoji。

### 4.7 提示词与代理定义

无。

## 5. 实施任务

### T1 分支、基线、spec 与计划入库

- 写范围：`docs/v3/specs/offpeak-pricing.md`、`docs/v3/plans/offpeak-pricing.md`
- 步骤：1. `git checkout dev && git checkout -b offpeak-pricing`。2. 跑 §0 全部检查，回填 Task 0 基线（commit、head、配置键、测试基线日志）。3. 提交 spec 与计划。
- 验证：`git status -sb` 第一行 `## offpeak-pricing`；§0 全表通过；`git log --oneline -1` 是本任务提交。
- 完成判据：基线写进计划「开工基线」与台账草稿。

### T2 迁移与模型字段

- 写范围：`maa_api/db/models.py`、新建 `maa_api/db/migrations/versions/0044_offpeak_prices.py`、新建 `tests/db/test_offpeak_prices_migration.py`
- 步骤：按 §4.1 加三个模型字段与迁移；迁移测试照 `tests/db/test_capability_insights_migration.py` 的 `tmp_path` + Alembic Config 写法，断言 `model_profile` 三列类型/可空、既有列不变、降级后三列消失、再升级为空列值。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/db/test_offpeak_prices_migration.py tests/db/test_models.py > /tmp/offpeak-t2.log 2>&1; echo $?; tail -n 2 /tmp/offpeak-t2.log` → `0`；`.venv/bin/alembic upgrade head && .venv/bin/alembic downgrade -1 && .venv/bin/alembic upgrade head; echo $?` → `0`（先备份 `resource/maa_api.db`）。
- 完成判据：迁移可逆、`test_models` 的 EXPECTED 清单无需变化但全绿；业务表仍 61。

### T3 设置与档案字段（后端）

- 写范围：`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`maa_api/agent/llm/profiles.py`、`config.template.yaml`、`tests/agent/llm/test_profiles.py`、`tests/test_settings.py`（及模型档案路由测试，若存在）、`web/openapi.json`、`web/src/types/api.d.ts`
- 步骤：1. 加 7 个设置键与 `_FIELDS` 条目（§4.2 第 1–2 条、§4.4）。2. `profiles.py` 的 `ResolvedProfile`/`ModelProfileView`/`Create`/`Update`/`_view_from_row`/`_resolved_from_row`/`_synthesize_default`/`_default_view`/`create`/`update` 加三个空闲价字段（§4.2 第 4 条）。3. 更新 `config.template.yaml`。4. 测试：`default` 档案 view 的价格来自 settings、自定义档案创建/更新/读取三字段往返、`ModelProfileUpdate` 的 `null` 清空语义、配置键计数与 schema 渲染。5. 重生成 OpenAPI 与前端类型。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/agent/llm/test_profiles.py tests/test_settings.py tests/api > /tmp/offpeak-t3.log 2>&1; echo $?; tail -n 2 /tmp/offpeak-t3.log` → `0`（`tests/api` 若过慢可只跑模型档案相关文件，但不得跳过全量 `tests/api` 之外的门禁）；`.venv/bin/python scripts/dump_openapi.py && .venv/bin/python scripts/dump_openapi.py --check; echo $?` → 0；`cd web && pnpm gen:api && pnpm gen:api:check; echo $?` → 0。
- 完成判据：配置键 151/13；`GET /api/agent/model-profiles` 的 `default` 项带 6 个价格字段；OpenAPI 与类型同步。

### T4 成本引擎（峰谷计算与账本）

- 写范围：`maa_api/agent/llm/cost.py`、`tests/agent/llm/test_cost.py`
- 步骤：按 §4.2 第 3 条实现 `PEAK_TIMEZONE`、`PEAK_WINDOWS`、`parse_peak_holidays`、`is_peak_time`、扩展 `compute_cost_micros`、`CostLedger` 构造与 `record` 的 `at`/settings；测试覆盖 §4.5 的全部边界情况、逐项回退与 `at=None` 回归；账本用例固定 `at`（峰值/空闲各一条，断言写入账本与运行累计）。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/agent/llm/test_cost.py > /tmp/offpeak-t4.log 2>&1; echo $?; tail -n 2 /tmp/offpeak-t4.log` → `0`。
- 完成判据：所有边界用例通过；直接调用 `compute_cost_micros` 的既有用例不变。

### T5 前端档案表单

- 写范围：`web/src/features/models/ProfileEditorSheet.tsx`、`web/src/features/models/models.test.tsx`（如需要 `web/src/features/models/labels.ts`）
- 步骤：按 §4.6 加三个输入与文案；`models.test.tsx` 补字段往返断言；`default` 档案只读价格展示若受影响一并更新。
- 验证：`cd web && pnpm typecheck; echo $?` → 0；`cd web && pnpm test --run > /tmp/offpeak-t5.log 2>&1; echo $?; tail -n 4 /tmp/offpeak-t5.log` → `0`。
- 完成判据：表单可录入/回填三字段，提交 body 带三字段；前端全量测试通过。

### T6 文档同步、验收与合并

- 写范围：`docs/v3/03-数据模型.md`、`04-接口与事件协议.md`、`10-可观测与评测.md`、`11-前端指挥台.md`、`12-实施路线与里程碑.md`、`13-决策记录.md`、`15-开放API接入指南.md`、`docs/v3/README.md`（如需）、`web/openapi.json`、`web/src/types/api.d.ts`、新建 `docs/v3/ledgers/offpeak-pricing.md`
- 步骤：按 §9 同步；跑 §7 验收；写台账；`git merge --no-ff offpeak-pricing -m "merge: 模型峰谷计价（维护性补丁，见 docs/v3/13 §8.187）"`；删除分支；合并后复验。
- 验证：§7 全表通过；`git status -sb` 干净。
- 完成判据：12 §4 的完成标准（维护性补丁按等价口径）全部满足；台账写好。

## 6. 测试清单

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/db/test_offpeak_prices_migration.py` | `test_upgrade_adds_three_nullable_price_columns`、`test_downgrade_drops_them_and_reupgrade_is_empty` | `model_profile` 列名/类型/可空；既有列原样；降级删列；再升级三列为 NULL |
| `tests/agent/llm/test_profiles.py` | `test_default_profile_prices_come_from_settings`、`test_default_profile_offpeak_zero_falls_back_to_none`、`test_default_profile_peak_zero_falls_back_to_none`、`test_create_profile_roundtrips_offpeak_prices`、`test_update_profile_can_clear_offpeak_prices`、`test_profile_view_includes_offpeak_prices` | settings → default view；6 个价格键的 0 都→None；CRUD 三字段；`null` 清空 |
| `tests/test_settings.py` | 既有 + `test_llm_price_settings_parse_from_config_and_env`、`test_peak_holidays_defaults_empty` | 7 键可解析、范围校验、`ENV_OVERRIDES` 可覆盖 |
| `tests/api/`（模型档案路由） | `test_model_profile_wire_includes_offpeak_prices` | 创建/读取/更新的 wire 字段名与类型 |
| `tests/agent/llm/test_cost.py` | `test_compute_cost_micros_at_uses_injected_settings`、`test_peak_windows_boundaries`、`test_weekend_is_offpeak`、`test_holiday_is_offpeak`、`test_offpeak_prices_fallback_per_component`、`test_compute_without_at_keeps_peak_semantics`、`test_parse_peak_holidays_skips_invalid`、`test_ledger_records_offpeak_cost` | §4.5 的表格逐行；账本行与 `agent_run.cost_micros` 等于当次成本 |
| `web/src/features/models/models.test.tsx` | 既有 + `submits offpeak prices in create and update payloads` | 表单回填与提交 body 三字段 |
| `tests/agent/trace/test_trace_middleware.py` | `test_llm_span_cost_uses_offpeak_prices_in_offpeak_window`、`test_llm_span_cost_uses_holiday_offpeak_prices_on_peak_weekday` | 固定时钟 + 注入 settings 的节假日；span 的 `maa.cost.micros` 与同参数 `compute_cost_micros_at` 相等；工作日高峰时刻列入 `llm.peak_holidays` 后按空闲价而非高峰价 |
| 既有门禁 | `tests/db/test_models.py`、`tests/domain/test_route_inventory.py`、`tests/domain/test_errors.py`、`tests/test_settings.py` 的计数 | 表 61、端点 200、错误码 174 不变；配置键 151/13 |

## 7. 验收

按维护性补丁等价口径跑：

1. `.venv/bin/python -m pytest -q > /tmp/offpeak-pytest.log 2>&1; echo $?; tail -n 5 /tmp/offpeak-pytest.log` → `1`，仅 3 条 Task 0 登记的环境基线失败；若出现其他失败停下报告。
2. `.venv/bin/python scripts/dump_openapi.py && .venv/bin/python scripts/dump_openapi.py --check; echo $?` → 0；`cd web && pnpm gen:api && pnpm gen:api:check; echo $?` → 0。
3. `cd web && pnpm install --frozen-lockfile && pnpm typecheck && pnpm test --run && pnpm build` → 全 0。
4. `.venv/bin/python scripts/frontend_smoke.py` → 末行 `SMOKE OK`。
5. `.venv/bin/python scripts/agent_smoke.py` → 末行 `SMOKE OK`。
6. `.venv/bin/python scripts/agent_eval.py --suite regression --mode replay` → `5/5`、`EVAL OK`（本补丁不改提示词/代理定义，capability replay 不强制；跑了就记台账）。
7. `.venv/bin/python scripts/generate_api_guide.py && .venv/bin/python scripts/generate_api_guide.py --check` → 0。
8. `.venv/bin/python scripts/check_doc_links.py` → 末行 `BROKEN 0`；`git diff --check` → 0。
9. 手工验证（开发库 + 真实调用可选）：设置 `llm.price_*`（DeepSeek 官方峰价/谷价）后重启服务，`GET /api/agent/model-profiles` 的 `default` 项返回 6 个价格；用 `compute_cost_micros` 或一条真实模型调用确认新账本行非零（真实调用会消耗少量额度，做之前先问用户；只做单测口径验证则不调用）。
10. `docs/v3/14` 总校：错误码 174、端点 200、agent 小节 97 不变；配置计数在 04 §6 与 settings 里为 151/13。

## 8. 风险与回滚

- **峰谷规则写死为 DeepSeek 规则**：工作日两个窗口 + 节假日手动列表。若以后换 provider 或官方改规则，需要新补丁；spec 明确这是当前唯一支持的规则，其他档案可把谷价留空退化为全天高峰价。
- **历史成本不回填**：既有 `cost_ledger` 219 行仍为 0；看板 30 天窗口的成本卡在旧数据上仍偏低，新调用才有真实值。台账写明。
- **节假日列表手工维护**：默认空时法定节假日按高峰计（高估）；台账与 16 部署指南提示每年更新。
- **时区**：用标准库 `zoneinfo` 的 `Asia/Shanghai`；naive datetime 按 UTC 解释，与项目其它时间口径一致；测试固定 UTC 时刻断言边界。
- **回滚**：`alembic downgrade 0043_capability_insights` 删三列（不涉及既有数据）；代码回滚后旧账本与旧计算行为恢复。新配置键在旧代码里会被忽略（`extra="ignore"`），不会阻止启动。

## 9. 文档同步

| 文档 | 改什么 |
|---|---|
| [03 §2、§4.2](../03-数据模型.md) | 迁移表加 0044 维护性补丁行；M91–M98 建议号 0044–0048 → 0045–0049；`model_profile` 加三列与“高峰时段价”说明 |
| [04 §2.1、§6](../04-接口与事件协议.md) | 模型档案创建/更新/视图字段加三个 `*_offpeak`；配置项总表加 7 行；配置键计数 151/13 |
| [10 §4](../10-可观测与评测.md) | 成本公式增加峰谷取价与时段规则；DeepSeek 官方价表与节假日说明；历史不回填 |
| [11 §9](../11-前端指挥台.md) | 设置 › 模型与角色：档案编辑器增加三个空闲时段价格输入；LLM 分组新增 7 个设置项 |
| [12 §2](../12-实施路线与里程碑.md) | M91–M98 建议迁移号顺延 0045–0049（维护性补丁不占 M 号、不调顺序，加一句注） |
| [13 §8](../13-决策记录.md) | 新增 §8.187：补丁范围、硬编码 DeepSeek 规则、节假日手工列表、历史不回填、迁移号顺延 |
| [15](../15-开放API接入指南.md) | 由 `scripts/generate_api_guide.py` 重新生成（错误码表无变化，`--check` 通过） |
| `config.template.yaml` | `llm:` 段加 7 键与注释（§4.2 第 6 条） |
| `docs/v3/README.md` | 如文档索引需要，补一句维护性补丁入口（无则不改） |
| 台账 | `docs/v3/ledgers/offpeak-pricing.md`（做了什么、验证证据、Rulings、偏离、遗留） |

计数预算（以 Task 0 实测为准，不同只记台账）：配置键 144 → 151；config-only 13 不变；业务表 61、HTTP 端点 200、错误码 174、枚举与工具不变；迁移 head 0043 → 0044；评测场景 31 不变。

## 10. 停下来问

- 官方价格规则与本文 §4.5 不一致（例如高峰窗口改了、空闲价不再是半价）时，先报告再实现。
- 需要新增本文之外的配置键、表、端点、错误码或依赖时。
- 用户要求把历史成本回填/重算时（超出本补丁范围）。
- `model_profile` 已有行且三列需要非空默认值时（本文设计是 NULL 回退，不需要）。
- 时区处理方式（naive UTC → Asia/Shanghai）与既有时间口径冲突时。
- 手工验证需要发起真实模型调用（会消耗额度）时，先问用户。

## 11. 需要补进契约的名字

| 名字 | 用途 | 落点 |
|---|---|---|
| `llm.price_input`、`llm.price_cached_input`、`llm.price_output` | default 档案高峰价 | 04 §6、16？`config.template.yaml` |
| `llm.price_input_offpeak`、`llm.price_cached_input_offpeak`、`llm.price_output_offpeak` | default 档案空闲价 | 04 §6、`config.template.yaml` |
| `llm.peak_holidays` | 峰谷计价的节假日列表 | 04 §6、`config.template.yaml` |
| `price_input_offpeak`、`price_cached_input_offpeak`、`price_output_offpeak` | 自定义档案空闲价 | 03 §4.2、04 §2.1 |
| `PEAK_TIMEZONE`、`PEAK_WINDOWS`、`parse_peak_holidays`、`is_peak_time` | 峰谷判定 | 10 §4 |

## 附录 A 检查与命令速查

```bash
# 分支与基线
git checkout dev && git checkout -b offpeak-pricing
git rev-parse --short dev HEAD
git tag -l 'v3-*' | sort | tail -1
ls maa_api/db/migrations/versions | grep -E '^[0-9]{4}_' | sort | tail -1
.venv/bin/python -c "from maa_api.settings import SETTING_KEYS, CONFIG_ONLY_SETTING_KEYS; print(len(SETTING_KEYS), len(CONFIG_ONLY_SETTING_KEYS))"

# 验收
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
