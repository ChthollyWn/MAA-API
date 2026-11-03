> 状态：TODO（先搁置，不要照此实施）
> 阶段：P4。领域亮点及之后的规格先搁置，等 P0–P3（M63–M85）落地后再决定是否升为可执行规格。
> 本文件正文是提前写好的草稿，执行者不要从这里开工。

# M88 资源策略师（规格）

> 阶段：P4　依赖：M87　分支：`economist`　迁移：无
> 本文是 M88 的唯一规格；实施偏离记在 [13 §8](../../13-决策记录.md)。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在本分支、工作区干净 | `git status -sb` | 第一行 `## economist`，没有其他行 |
| 2 | M87 已合并 | `git tag -l 'v3-m87'` | 输出 `v3-m87` |
| 3 | v2 线性规划入口 | `rg -n "def _linprog_call\|def _build_lp\|def _solve_allocation\|    async def _solve\|    async def plan_needs\|    def _candidate_stages\|    async def _load_rates\|    async def _load_recipes" maa_api/services/account/planner.py` | 8 行 |
| 4 | scipy 可用 | `.venv/bin/python -c "from scipy.optimize import linprog; print('ok')"` | `ok` |
| 5 | 产物服务（M75） | `rg -n "class ArtifactService" maa_api/agent/artifacts/service.py` | 1 行；把「新建一个产物」的方法名与参数记进台账，T5 用它 |
| 6 | 两个 Worker 定义 | `rg -l "^name: (planner\|analyst)$" maa_api/agent/specs/builtin/` | 2 个文件 |
| 7 | 拆包常量表在白名单 | `rg -n "item_table.json" maa_api/services/external/sources.py` | 1 行 |
| 8 | 工具计数基线 | `.venv/bin/python -c "from maa_api.agent.tools import build_registry as b; r=b(); print(len(r.list()), sum(1 for d in r.list() if d.model_visible))"` | 按 05 §6.1 推算为 `106 105`；记下实际值 |
| 9 | 活动页解析与 PRTS 客户端 | `rg -n "def parse_activity_page\|exchange_end_at" maa_api/services/prts_parse/activity.py` | 至少 2 行 |

## 1. 背景与目标

v2 的养成规划（M27，`UpgradePlanner`）用 `scipy.optimize.linprog` 在常驻关与加工站配方上求「最少理智凑齐材料」，按每天 240 理智估天数。它不看活动商店，不看当期活动关卡的代币，没有时间窗口，也不考虑理智药。用户常问的是「这周能不能凑齐」「活动商店先换什么」「吃不吃药」。

M88 新增工具 `optimize_farming` 与服务 `FarmingOptimizer`，复用 v2 的配方闭包、掉率与候选关筛选，扩展线性规划。做完能验证的结果：

1. 输入材料清单或养成计划 id，加上当前理智、时间窗口、是否用药，返回刷图清单、预计天数、用药建议、商店兑换建议与中文说明。
2. 当期活动关卡作为代币来源进入规划，活动商店的商品按库存上限与代币价格进入规划；代币从 PRTS 活动页解析，活动截止时间限制活动关的可用理智。
3. 时间窗口不够时给出窗口内的最大覆盖方案（每种材料的覆盖比例），同时给出完整凑齐要几天。
4. 同一组输入的结果确定（HiGHS 求解，两阶段，整数化规则固定），没有活动、没有商店时总理智与 v2 `plan_needs` 一致。
5. 内置调用方调用时写一份 Markdown 报告产物，结果里带 `report_artifact_id`。
6. `planner`、`analyst`、`general` 三个代理拿到这个工具，提示词写明什么时候用。

## 2. 非目标

- 不提交任何任务；执行仍走 `start_upgrade_plan`、`submit_pipeline` 与确认。
- 不建模活动任务奖励（打捞方案这类一次性代币）、月卡、源石换理智、剿灭与周常；用户手里已有的代币由 `owned_tokens` 传入。
- 不读当前理智（没有数据源）；由调用方传入，缺省按 0。
- 不新增外部来源；PRTS 与企鹅物流、拆包数据都走既有客户端与白名单。
- 不改 v2 养成计划的规划口径（`start_upgrade_plan` 仍只看常驻关）。

## 3. 相关文档

- [05 §3.2、§6.1](../../05-运行时与代理定义.md)（planner、analyst、general 的工具集；`optimize_farming` 组 `economy`、SAFE、REST 可见）；[02 §4.1](../../02-系统架构.md)（`tools/economy.py`）。
- v2：`rg -n "^### 8.44" docs/v2/13-决策记录.md` 的九（升级经验只报需求不参与刷关）；`rg -n "7.6" docs/v2/superpowers/specs/2026-10-09-m25-prts-knowledge.md | head`（养成规划规格 §7.6–7.7）。
- 既有代码入口：`maa_api/services/account/planner.py` 模块说明（变量、约束、目标、候选关口径、整数化、降级）、`_StageOption`（约 421 行）、`_Recipe`（约 411）、`_build_lp`（约 842）、`_solve_allocation`（约 869）、`UpgradePlanner._solve`（约 1224）、`plan_needs`（约 1156）、`_load_recipes`（约 1635）、`_load_rates`（约 1714）、`_candidate_stages`（约 1757）、`_render_stages`（约 1865）、`SANITY_PER_DAY`（约 94）、`_key`（约 155）、`_linprog_call`（约 318）；`maa_api/services/account/plans.py` 的 `UpgradePlanService.refresh`（约 431，「需求 − 仓库」的算法）、`_depot_counts`（约 700）；`maa_api/services/game/activities.py` 的 `ActivityService.current`（约 218）、`CurrentActivity`（约 100，`navigation` 取 `available`/`missing`/`unknown`）、`default_activity_service`（约 755）；`maa_api/services/prts_parse/activity.py` 的 `parse_activity_page`（约 136）；`maa_api/services/external/gamedata.py` 的 `GamedataClient.excel`（约 199）；`maa_api/agent/tools/account.py` 的 `_planner`、`_service`（约 402–445，app.state 名 `planner_service`、`depot_service`、`upgrade_plan_service`）。

## 4. 设计

### 4.1 数据模型

无新表、无迁移。报告存成 M75 的 `artifact` 行（`kind='report'`、`mime='text/markdown'`）。

### 4.2 模块与文件

| 文件 | 新建/修改 | 职责 |
|---|---|---|
| `maa_api/services/economy/__init__.py` | 新建 | 导出 `FarmingOptimizer`、`FarmingRequest` |
| `maa_api/services/economy/model.py` | 新建 | 规划输入、商店商品、代币、结果的数据类 |
| `maa_api/services/economy/solver.py` | 新建 | 纯函数：组矩阵、两阶段求解、整数化 |
| `maa_api/services/economy/data.py` | 新建 | 取数：需求、仓库、配方与掉率、活动关与商店、理智药 |
| `maa_api/services/economy/report.py` | 新建 | Markdown 报告 |
| `maa_api/services/economy/service.py` | 新建 | `FarmingOptimizer.optimize` |
| `maa_api/services/prts_parse/event_shop.py` | 新建 | 解析 `EventShopList` 与关卡表的代币产出 |
| `maa_api/services/account/planner.py` | 改 | 抽出公开方法 `farming_inputs`；`StageOption`、`Recipe` 公开别名 |
| `maa_api/services/account/plans.py` | 改 | 公开方法 `missing_needs(plan_id)` |
| `maa_api/agent/tools/economy.py`、`maa_api/agent/tools/__init__.py` | 新建/改 | `optimize_farming` |
| `maa_api/agent/specs/builtin/{planner,analyst,general}.md` | 改 | 工具与提示词 |
| `tests/fixtures/prts/activity_zuori_shop.wikitext`、`tests/fixtures/prts/README.md` | 新建/改 | 商店与关卡表夹具 |

`planner.py` 的改动（纯重构，v2 行为不变）：

```python
StageOption = _StageOption          # 公开别名，供 economy 使用
Recipe = _Recipe

@dataclass(slots=True)
class FarmingInputs:
    needs: dict[str, int]                    # _key 归一后的键 → 数量（已去掉龙门币、作战记录）
    display: dict[str, str]                  # 键 → 显示名
    options: list[StageOption]               # 候选关（企鹅物流口径 + MAA 能导航）
    recipes: list[Recipe]                    # 配方闭包里的全部配方
    rate_status: dict[str, str]              # 键 → 取掉率失败或没有候选关的原因
    notes: list[str]
    server: str

class UpgradePlanner:
    async def farming_inputs(self, needs: Mapping[str, int], *, server: str | None = None) -> FarmingInputs:
        """与 plan_needs 同一套入口归一（龙门币、作战记录只报需求），再做配方闭包、取掉率、筛候选关。"""
```

`_solve` 里「配方闭包 → universe → 取掉率 → 候选关 → 补 status」那段（约 1238–1260 行）抽成私有方法 `_prepare(farm_needs, *, display, server_name, notes)`，`_solve` 与 `farming_inputs` 都调它。

`plans.py`：

```python
async def missing_needs(self, plan_id: str) -> dict[str, int]:
    """计划需求 − 当前仓库，只留大于 0 的；计划不存在抛 UPGRADE_PLAN_NOT_FOUND。refresh 改为调用它。"""
```

`model.py`：

```python
@dataclass(frozen=True, slots=True)
class ShopGood:
    activity: str                  # 活动页标题
    token: str                     # 代币名，如 “未来瞻望镜”
    item: str                      # 物品显示名
    key: str                       # _key(item)
    bundle: int                    # 每份数量（「×5000」→ 5000，缺省 1）
    price: int                     # 每份代币价
    stock: int | None              # 份数上限；∞ 为 None
    section: str                   # 「金色阶段」「无限库存区」等

@dataclass(frozen=True, slots=True)
class TokenDrop:
    code: str; ap_cost: int; tokens: int          # 每次三星通关的常规代币掉落

@dataclass(slots=True)
class EventInfo:
    activity: str; token: str | None; end_at: datetime | None; exchange_end_at: datetime | None
    navigable: bool; drops: dict[str, TokenDrop]; shop: list[ShopGood]; stage_ids: dict[str, str]

@dataclass(frozen=True, slots=True)
class FarmingRequest:
    materials: Mapping[str, int] | None
    plan_id: str | None
    use_depot: bool = True
    current_sanity: int = 0
    window_days: float | None = None
    use_medicine: bool = False
    include_events: bool = True
    include_unnavigable: bool = False
    owned_tokens: Mapping[str, int] = field(default_factory=dict)
    server: str | None = None

@dataclass(slots=True)
class FarmingModel:
    needs: dict[str, int]; depot: dict[str, int]; display: dict[str, str]
    options: list[StageOption]                 # 含活动关；活动关的 rates 可以为空
    event_of: dict[str, str]                   # stage_id → 活动页标题（常驻关不在里面）
    tokens_of: dict[str, tuple[str, int]]      # stage_id → (代币名, 每次代币)
    recipes: list[Recipe]
    shop: list[ShopGood]
    owned_tokens: dict[str, int]
    event_budget: dict[str, float]             # 活动页标题 → 截止前可用理智
```

`solver.py`：

```python
COVERAGE_TOL = 1e-6
EPSILON = 1e-3          # 合成次数与兑换份数的打破平局系数

@dataclass(slots=True)
class FarmingSolution:
    runs: list[int]; synth: list[int]; buys: list[int]; shortfall: dict[str, float]
    total_sanity: int; status: str | None      # status 非空表示求解失败的原因

def build_matrix(model: FarmingModel, *, budget: float | None) -> LpMatrix: ...
def solve_farming(model: FarmingModel, *, budget: float | None = None) -> FarmingSolution: ...
```

`LpMatrix` 是 `c`、`A_ub`、`b_ub`、`bounds` 与变量下标（`x`、`y`、`z`、`u` 各一段）的数据类。线性规划调 `planner._linprog_call`（同一个测试接缝）。

`event_shop.py`：

```python
SHOP_INVOKE = "{{#invoke:EventShopList|list"

def parse_event_shop(content: str) -> tuple[str | None, list[tuple[str, int, int | None, int, str]]]:
    """返回 (代币名, [(物品名, 每份数量, 份数上限或 None, 单价, 所在分区)])；页面没有商店返回 (None, [])。"""

def parse_token_drops(content: str, token: str) -> dict[str, TokenDrop]:
    """关卡表里每行 {{关卡链接|代号}} 的理智与常规代币掉落；没有常规代币掉落的行不收。"""
```

`service.py`：

```python
class FarmingOptimizer:
    def __init__(self, *, planner: Any, depot: Any, plans: Any, activities: Any,
                 prts: Any, gamedata: Any, artifacts: Any | None = None,
                 now: Callable[[], datetime] = ...) -> None: ...
    async def optimize(self, request: FarmingRequest, *, write_report: bool,
                       session_id: str | None, ticket_id: str | None) -> dict[str, Any]: ...

def default_farming_optimizer(app_state: Any | None = None) -> FarmingOptimizer:
    """按 T5 的装配口径现建；app_state 为 None 时全部用 default_* 与现建客户端，没有产物服务。"""
```

### 4.3 接口

#### 4.3.1 工具 `optimize_farming`

| 组 | 风险 | device | read_only | refetchable | untrusted_output | rest_visible |
|---|---|---|---|---|---|---|
| `economy` | SAFE | `none` | True | True | True | True |

描述（工具 `description` 原文）：

```
算刷图方案：给定要凑的材料或养成计划，在当前理智、时间窗口和是否用药的条件下，结合企鹅物流掉率、当期活动关卡和活动商店，算出去哪几关刷几次、商店先换什么、要几天、要不要用药。只计算，不提交任务。比 plan_upgrade 多算活动商店与时间窗口；要建每日计划仍用 start_upgrade_plan。
```

参数（`extra="forbid"`）：

```python
class OptimizeFarmingParams(BaseModel):
    materials: dict[str, Annotated[int, Field(ge=1, le=99999)]] | None = Field(default=None, max_length=60)
    plan_id: str | None = Field(default=None, min_length=1, max_length=36)
    use_depot: bool = True
    current_sanity: int = Field(default=0, ge=0, le=999)
    window_days: float | None = Field(default=None, ge=0.5, le=90)
    use_medicine: bool = False
    include_events: bool = True
    include_unnavigable: bool = False
    owned_tokens: dict[str, Annotated[int, Field(ge=0, le=999999)]] = Field(default_factory=dict, max_length=5)
    write_report: bool = True
    # model_validator：materials 与 plan_id 恰好给一个，否则 INVALID_PARAMETER；materials 为空字典也算没给
```

返回：

```json
{
  "status": "complete",
  "fits_window": true,
  "requirements": {"异铁块": 20, "聚酸酯组": 10},
  "stages": [
    {"stage_id": "act52side_06", "stage_code": "YW-6", "runs": 38, "sanity_per_run": 21, "sanity": 798,
     "event": true, "activity": "昨日海", "deadline": "2026-10-23T03:59:00+08:00", "navigable": true,
     "tokens_per_run": 21, "items": [{"item_name": "全新装置", "rate": 0.21}]}
  ],
  "synthesis": [],
  "shop": [
    {"activity": "昨日海", "item": "异铁块", "quantity": 10, "bundle": 1, "price": 40, "tokens": 400,
     "stock": 10, "section": "金色阶段", "value_sanity": 312.0}
  ],
  "tokens": [{"activity": "昨日海", "token": "“未来瞻望镜”", "owned": 0, "earned": 798, "spent": 400, "left": 398}],
  "sanity": {"needed": 2310, "current": 120, "medicine": 0, "natural_per_day": 240, "window_days": 7},
  "days_estimated": 9.2,
  "days_estimated_full": 9.2,
  "medicine": {"use": false, "owned": [{"name": "应急理智合剂", "ap": 60, "count": 3}], "owned_sanity": 180,
               "needed_sanity": 0, "suggested_count": 0, "note": "没有用药：用户没有同意用理智药"},
  "coverage": {"异铁块": 1.0, "聚酸酯组": 1.0},
  "unobtainable": [],
  "notes": ["按每天自然恢复 240 理智估算，不含月卡与活动赠送"],
  "explanation": "凑齐需要 2310 理智，按当前理智与自然恢复约 9.2 天……",
  "sources": [{"kind": "penguin", "label": "企鹅物流"}, {"kind": "prts", "label": "PRTS", "title": "昨日海"}],
  "report_artifact_id": "9b1c…"
}
```

字段口径：

- `status`：`complete`（全部覆盖且装进窗口或没给窗口）、`partial`（给了窗口且装不下，返回窗口内的方案）、`unobtainable`（有材料怎么都凑不齐，见 `unobtainable`）。
- `fits_window`：没给窗口为 null。`days_estimated` 是本方案的天数；`partial` 时 `days_estimated_full` 是完整凑齐的天数，其余情况两者相等。
- `stages` 按 `sanity` 降序；`items` 只列需求闭包里的材料，形状同 v2 `_render_stages`。
- `shop` 只列份数大于 0 的商品，按 `value_sanity` 降序（为空的排最后）；`value_sanity = quantity × bundle × 该材料的最低单件理智`（取企鹅物流行的 `sanity_per_item` 最小值，取不到为 null）。
- `synthesis`、`unobtainable` 的形状同 v2 `UpgradePlan.as_dict()`。
- REST 调用方或 `write_report=false` 时 `report_artifact_id` 为 null。

错误：`materials` 与 `plan_id` 不是恰好一个 → `INVALID_PARAMETER`；计划不存在 → `UPGRADE_PLAN_NOT_FOUND`（v2）。外部来源失败不抛错，写进 `notes` 与 `unobtainable`。

### 4.4 配置项

无。

### 4.5 行为与算法

#### 4.5.1 取数

```
optimize(request):
    if request.plan_id: needs = plans.missing_needs(plan_id); depot = {}      # 计划口径已扣过仓库
    else: needs = request.materials; depot = 仓库数量（use_depot 时）
    inputs = planner.farming_inputs(needs, server=request.server)
    events = load_events() if request.include_events else []
    model = merge(inputs, depot, events, request)
    full = solve_farming(model)
    S = full.total_sanity
    medicine_pool = 理智药折合理智（use_medicine 时，否则 0）
    days_full = days(S, current, medicine_pool)
    if request.window_days and days_full > request.window_days:
        budget = current + window_days × 240 + medicine_pool
        chosen = solve_farming(model, budget=budget); status = "partial"
    else:
        chosen = full; status = "unobtainable" if 有短缺 else "complete"
    result = render(chosen, ...)
    if write_report and session_id: result["report_artifact_id"] = artifacts.create(...)
```

- 仓库：`depot.list_items(limit=1000, offset=0)`，照 `UpgradePlanService._depot_counts` 的形状按名字建索引，键用 `_key`；读不到按 0 计并写 notes「仓库数据读取失败，库存按 0 计」。
- `load_events()`：`activities.current(detail="full")` 的每个活动，`end_at` 晚于现在的才要；`prts.wikitext([activity.page])` 取原文；`parse_activity_page` 取 `exchange_end_at`；`parse_event_shop` 取代币与商品；`parse_token_drops(content, token)` 取关卡的理智与代币；`stage_ids` 取 `CurrentActivity.stages` 里代号 → `stage_id`；`navigable = activity.navigation == "available"`。任一活动取不到就跳过它并写 notes「活动「{name}」的页面读不到，没有计入活动关与商店」。
- 理智药：`gamedata.excel("item_table.json")` 的 `apSupplies`（`{id: {ap}}`）配 `items[id].name` 得「名字 → 理智」；仓库里同名物品的数量乘上去求和。取不到常量表时 `use_medicine` 视为 false，写 notes「没有读到理智药的恢复量，方案里不计用药」。

#### 4.5.2 合并成规划模型

- 常驻候选关取 `inputs.options`。
- 活动关：对每个活动的每个 `TokenDrop`，用代号找 `inputs.options` 里 `stage_code` 相同的选项；找到就给它记代币；找不到就新建 `StageOption(stage_id=stage_ids.get(code, code), stage_code=code, ap_cost=drop.ap_cost)`（`rates` 为空，只产代币）。`navigable=false` 且 `include_unnavigable=false` 时这个活动的关卡都不进模型（商店仍可用已有代币），notes 写「活动「{name}」MAA 还不能自动导航，活动关没有计入」。
- 商店：只收 `_key(item)` 出现在需求或配方涉及的材料里的商品；龙门币、作战记录不收（v2 口径只报需求）。
- `event_budget[activity] = current + min(window_days or ∞, 距 end_at 的天数) × 240 + medicine_pool`；`end_at` 为空时不设。

#### 4.5.3 线性规划

变量（按顺序）：每个关卡的出击次数 `x_s ≥ 0`；每个配方的合成次数 `y_r ≥ 0`；每个商品的兑换份数 `0 ≤ z_k ≤ stock_k`（`stock` 为 None 时无上界）；每个需求材料的短缺量 `0 ≤ u_i ≤ d_i`，其中 `d_i = max(need_i − depot_i, 0)`。

约束（都写成 `A_ub · v ≤ b_ub`）：

| 约束 | 左边 | 右边 |
|---|---|---|
| 材料行（需求材料与配方涉及的材料各一行） | `−Σ rate_{s,i}·x_s − Σ (out_{r,i} − in_{r,i})·y_r − Σ bundle_k·[key_k=i]·z_k − u_i` | `depot_i − need_i`（中间材料 need 为 0，也没有 `u_i`） |
| 代币行（每种代币一行） | `Σ price_k·z_k − Σ tokens_s·x_s` | `owned_tokens[token]` |
| 活动理智行（每个有截止时间的活动一行） | `Σ_{s∈该活动} ap_s·x_s` | `event_budget[activity]` |
| 总理智行（只在 `budget` 给定时） | `Σ ap_s·x_s` | `budget` |

两阶段求解（`method="highs"`）：

1. 第一阶段：`min Σ_i u_i / max(d_i, 1)`，得最优值 `U*`。
2. 第二阶段：加一行 `Σ_i u_i / max(d_i, 1) ≤ U* + COVERAGE_TOL`，`min Σ ap_s·x_s + EPSILON·(Σ y_r + Σ z_k)`。
3. 整数化（照 v2 的做法）：`y_r = ceil(y_r − 1e-9)`、`z_k = min(ceil(z_k − 1e-9), stock_k)` 固定为上下界，再解一次第二阶段；这次无解时沿用整数化前的 `x` 并写 notes「合成与兑换取整后没有求出配套的出击次数，出击次数沿用取整前的解」；最后 `x_s = ceil(x_s − 1e-9)`。
4. `_linprog_call` 抛异常或第一阶段无解：`FarmingSolution.status` 写原因，结果 `stages`、`shop` 为空，`status="unobtainable"`，notes 写「线性规划不可用（…）」。

覆盖率：`coverage_i = 1 − u_i / d_i`（`d_i = 0` 时为 1），保留两位小数。`u_i > 0.5` 的材料进 `unobtainable`，原因优先取 `inputs.rate_status`，否则写「当前可用的关卡、配方与商店凑不齐」。

#### 4.5.4 天数与用药

- `days(S, current, pool)`：`use_medicine` 时 `used = min(pool, max(S − current, 0))`，否则 `used = 0`；`days = ceil_1(max(S − current − used, 0) / 240)`（向上取到一位小数）。
- `medicine.needed_sanity = used`；`suggested_count`：把仓库里的理智药按恢复量从小到大排，依次累加到不少于 `used` 为止的瓶数（这是上限，内核按到期先后吃，实际可能更少）；`note` 写「Fight 任务的用药数填 {suggested_count} 以内；内核会先吃快过期的」或「没有用药：用户没有同意用理智药」。
- `partial` 时 `sanity.window_days` 写窗口，`days_estimated` 等于窗口内方案的天数。

#### 4.5.5 说明文字

`explanation` 由代码按模板拼，不调模型，三到四句：

1. 总量：「凑齐需要 {S} 理智，按当前理智 {current} 与每天 240 的自然恢复约 {days} 天{，用 {n} 瓶理智药后约 {days2} 天}。」
2. 主力关卡：「主要刷 {前两关代号与次数}{；{活动名} 的 {代号} 每次还给 {tokens} 个代币}。」
3. 商店（有兑换时）：「代币先换 {前两项物品与数量}，按掉率折合约 {value} 理智。」
4. 窗口或缺口（有时）：「{window} 天内能做到：{各材料覆盖百分比，最多 3 项}。」或「{材料} 目前凑不齐：{原因}。」

#### 4.5.6 报告产物

`report.py` 的 `render_report(result: Mapping[str, Any], *, generated_at: datetime) -> str` 输出：

```markdown
# 刷图方案

- 需求：{材料 ×数量，顿号分隔}
- 结论：{explanation 第一句}

## 刷图清单
| 关卡 | 次数 | 每次理智 | 合计理智 | 主要产出 | 截止 |

## 商店兑换
| 物品 | 份数 | 每份 | 单价（代币） | 合计代币 | 库存 | 折合理智 |

## 用药
{medicine.note}

## 合成
| 产物 | 次数 | 龙门币 |

## 说明
- {notes，每条一行}

## 数据来源
- 企鹅物流掉率（{取数时间}）
- PRTS「{活动页标题}」（{取数时间}）
```

没有数据的小节整节省略。产物标题「刷图方案：{第一种材料} 等 {n} 种材料」（截到 120 字）；`session_id` 取 `ToolContext.session_id`，`ticket_id` 取该会话的 `agent_session.ticket_id`。产物写失败不影响工具结果，`report_artifact_id` 为 null 并写 notes「报告没有保存成功」。

#### 4.5.7 商店与关卡表的解析口径

PRTS 活动页的商店在 `{{#invoke:EventShopList|list … }}` 里（2026-11-02 实测《昨日海》）：

- `token_alias=带框_“未来瞻望镜”`：去掉 `带框_` 前缀就是代币名，与关卡表 `{{关卡报酬|“未来瞻望镜”||60px|n=21}}` 里的名字一致。
- `data=` 之后每行一条，字段以 `;;` 分隔：`X;;<分区名>;;…` 是分区标题行；其余行是 `<标记>;;<物品>;;<库存>;;<单价>;;<颜色>`。
- 物品取第一个 `{{道具图标|<名字>|…}}` 的名字；`<small>'''×N'''</small>` 是每份数量；以 `{{家具|`、`{{皮肤头像|` 开头的行不是材料，跳过。
- 库存是整数或 `∞`。单价是整数。

关卡表：`|-` 分隔的行里同时有 `{{关卡链接|<代号>}}` 与 `{{道具图标|理智|<数字>|` 的才算一关；常规代币掉落是第二个参数为空的那个 `{{关卡报酬|<代币>||…|n=<数字>}}`（第二个参数写「三星获得」的是首次掉落，不算）。没有常规代币掉落的关（如 EX 关）不收。

#### 4.5.8 边界情况

| 输入 | 期望行为 |
|---|---|
| 没有在开活动 | 只算常驻关与配方；结果与 v2 `plan_needs` 的总理智一致 |
| 活动页没有商店小节 | 只把活动关当代币以外的材料来源；notes 记一句 |
| 需求只有商店能给（如模组数据块） | 用商店与已有代币覆盖；不够的进 `unobtainable` |
| 代币不够换满库存 | 代币行约束生效，按折合理智高的优先换（由目标函数决定） |
| 活动今天结束 | 该活动的理智行右边按 0 天算，只剩当前理智与药 |
| 窗口 0.5 天 | 合法；多数情况 `partial` |
| 材料名企鹅物流不认识 | 进 `unobtainable`，原因写「企鹅物流没有这个材料的掉率记录」 |
| `plan_id` 的计划已完成 | `missing_needs` 为空，`status=complete`、`stages` 为空、notes「没有剩余需求」 |
| scipy 不可用 | `status=unobtainable`，notes 写原因，不抛错 |

### 4.6 前端

无。报告产物在 M75 的产物视图里看。

### 4.7 提示词与代理定义

三个定义的 frontmatter `tools` 加 `optimize_farming`，`version` 各加 1。

`planner.md` 正文末尾追加：

```markdown
## 刷图方案

- 用户问「这些材料怎么刷最快」「这周能不能凑齐」「活动商店先换什么」「要不要吃药」时，用 optimize_farming。它在 plan_upgrade 的基础上多算当期活动关卡、活动商店、时间窗口和理智药。
- 用户给的是干员目标时，先 plan_upgrade 算出材料，再把还缺的材料作为 materials 传给 optimize_farming；已经有养成计划就直接传 plan_id。
- 用户没说现在有多少理智就不要猜，current_sanity 留 0，回答里说明按 0 算；只有用户给了期限才传 window_days。
- 只有用户同意用药才传 use_medicine=true。方案里的用药是建议，真正吃药要用户在任务确认里批准。
- 汇报先说结论：能不能按期凑齐、要几天、总理智；再列前三个关卡和前三项商店兑换。完整表格在 report_artifact_id 指向的报告里，不要把整张表贴进摘要。
- 要执行，常驻关仍用 start_upgrade_plan 建每日计划；活动关的场次写进汇报，由用户决定要不要另开工单去刷。
```

`analyst.md` 正文末尾追加：

```markdown
## 资源账

- 分析「这期活动值不值得刷」「商店先换什么」「缺的材料还要多久」时，用 optimize_farming。返回里的 shop 已按折合理智排序，stages 写了每关的产出和代币。
- 只给结论和依据：哪几项最划算、为什么、数据来自哪里（企鹅物流、PRTS 活动页）。不替用户决定吃药或碎石。
- 要画图时用 send_chart，数据直接取 optimize_farming 的返回，不要另行估算。
```

`general.md`：只加工具，正文不改。

## 5. 实施任务

### T1 规划器重构

- 写范围：`maa_api/services/account/planner.py`、`maa_api/services/account/plans.py`、`tests/services/test_planner_farming_inputs.py`
- 步骤：1. 抽 `_prepare`，加 `FarmingInputs`、`farming_inputs`、两个公开别名（§4.2）。2. `missing_needs` 抽出来，`refresh` 改为调用它。3. 新测试：同一组假掉率与假配方下，`plan_needs` 的结果与重构前逐字段相同（先在重构前跑一次把 `as_dict()` 存成测试里的期望常量）。
- 验证：`.venv/bin/python -m pytest -q tests/services -k "planner or upgrade_plan" > /tmp/m88-t1.log 2>&1; echo $?; tail -n 5 /tmp/m88-t1.log` → `0`
- 完成判据：v2 既有的养成规划与计划测试一条不改全部通过。

### T2 商店与关卡表解析

- 写范围：`maa_api/services/prts_parse/event_shop.py`、`tests/fixtures/prts/activity_zuori_shop.wikitext`、`tests/fixtures/prts/README.md`、`tests/services/test_event_shop_parse.py`
- 步骤：1. 夹具照抄附录 A（来源与抓取日期写进同目录 README，与既有的 `activity_zuori.wikitext` 一样注明是裁剪快照）。2. 按 §4.5.7 实现两个函数。
- 验证：`.venv/bin/python -m pytest -q tests/services/test_event_shop_parse.py > /tmp/m88-t2.log 2>&1; echo $?` → `0`
- 完成判据：附录 A 的期望值全部断言通过。

### T3 求解器

- 写范围：`maa_api/services/economy/__init__.py`、`model.py`、`solver.py`、`tests/services/economy/__init__.py`、`tests/services/economy/test_solver.py`
- 步骤：按 §4.5.3。测试用手造的 `FarmingModel`，不联网。
- 验证：`.venv/bin/python -m pytest -q tests/services/economy/test_solver.py > /tmp/m88-t3.log 2>&1; echo $?; tail -n 5 /tmp/m88-t3.log` → `0`
- 完成判据：§6 的求解器用例全部通过。

### T4 取数、合并与结果

- 写范围：`maa_api/services/economy/data.py`、`service.py`、`report.py`、`tests/services/economy/test_data.py`、`tests/services/economy/test_service.py`、`tests/services/economy/test_report.py`
- 步骤：按 §4.5.1、§4.5.2、§4.5.4–4.5.6。所有外部依赖从构造参数注入，测试里用假对象（假 `planner.farming_inputs`、假 `activities.current`、假 `prts.wikitext` 返回附录 A 夹具、假 `gamedata.excel` 返回带 `apSupplies` 的最小表、假仓库）。
- 验证：`.venv/bin/python -m pytest -q tests/services/economy > /tmp/m88-t4.log 2>&1; echo $?; tail -n 5 /tmp/m88-t4.log` → `0`
- 完成判据：同一输入跑两次结果完全相同。

### T5 工具

- 写范围：`maa_api/agent/tools/economy.py`、`maa_api/agent/tools/__init__.py`（注册放在 `register_account_tools` 之后）、`tests/agent/test_tool_catalog.py`、`tests/agent/test_economy_tool.py`
- 步骤：1. 参数与描述照 §4.3.1。2. 装配：`planner` 取 `app.state.planner_service` 或 `default_upgrade_planner()`；`depot` 取 `app.state.depot_service` 或 `DepotService(session_factory)`；`plans` 取 `app.state.upgrade_plan_service` 或 `default_upgrade_plan_service()`；`activities` 用 `default_activity_service()`；`prts` 用 `activities.prts`；`gamedata` 用 `GamedataClient()`；`artifacts` 取 `app.state.artifact_service`。3. 只有 `CallerType.INTERNAL` 且 `write_report=true` 且有 `session_id` 时写产物。4. `EXPECTED_TOOLS` 加一个名字，两个计数在 §0 第 8 条的值上各加 1。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_tool_catalog.py tests/agent/test_economy_tool.py > /tmp/m88-t5.log 2>&1; echo $?` → `0`
- 完成判据：REST 调用 `POST /api/agent/tools/optimize_farming/invoke` 返回 200 且 `report_artifact_id` 为 null。

### T6 代理定义

- 写范围：`maa_api/agent/specs/builtin/{planner,analyst,general}.md`
- 步骤：按 §4.7。
- 验证：`.venv/bin/python -m pytest -q tests/agent/specs > /tmp/m88-t6.log 2>&1; echo $?` → `0`

### T7 评测

- 写范围：`tests/evals/scenarios/m88_planner_week.yaml`、`tests/evals/scenarios/m88_analyst_shop.yaml` 与对应 cassette
- 步骤：按 M68 的场景格式写两个 `capability` 场景，`optimize_farming` 的服务用假数据（与 T4 同一份夹具）：
  - `m88_planner_week`：规划师工单「这周想凑 20 个异铁块和 10 个聚酸酯组，来得及吗」。期望调用 `optimize_farming(materials=…, window_days=7)` 一次、`report(ready)` 的摘要里有天数与「异铁块」；不调用 `start_upgrade_plan`。
  - `m88_analyst_shop`：分析师工单「按我的养成计划，这期活动商店先换什么」。期望先 `list_upgrade_plans` 再 `optimize_farming(plan_id=…)`，摘要里列出商店前三项。
- 验证：`.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m88-eval.log 2>&1; echo $?` → `0`
- 完成判据：通过率不低于 M87 台账记录的基线。

### T8 文档同步

- 写范围：§9 列出的文档
- 验证：`.venv/bin/python scripts/check_doc_links.py | tail -n 1` → `BROKEN 0`

## 6. 测试清单

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/services/test_planner_farming_inputs.py` | `test_plan_needs_unchanged_after_refactor`、`test_farming_inputs_drops_report_only_items` | 重构前后 `as_dict()` 相同；龙门币、作战记录不进 `needs` |
| 同上 | `test_missing_needs_subtracts_depot` | 需求 30、仓库 12 → 18；仓库够的不出现 |
| `tests/services/test_event_shop_parse.py` | `test_shop_token_and_sections` | 代币 `“未来瞻望镜”`；分区有「金色阶段」「无限库存区」 |
| 同上 | `test_shop_items_stock_price_bundle` | 附录 A 表中各行；`RMA70-12` 库存 None；龙门币每份 5000 |
| 同上 | `test_shop_skips_furniture_and_skins` | 家具与时装不在结果里 |
| 同上 | `test_token_drops_regular_only` | `YW-6`、`YW-7`、`YW-8` 各 21 理智 21 代币；首次掉落的 n=80 不算 |
| 同上 | `test_page_without_shop` | `activity_vec03.wikitext` → `(None, [])` |
| `tests/services/economy/test_solver.py` | `test_permanent_only_matches_v2_total` | 同一组关卡与配方，第二阶段总理智等于 v2 `_solve_allocation` 的总理智 |
| 同上 | `test_shop_beats_farming_when_cheaper` | 商店 1 份 = 1 件、40 代币，活动关 21 理智给 21 代币，常驻关 30 理智 1 件 → 先换满库存 |
| 同上 | `test_stock_cap_is_respected` | 需求 15、库存 10 → 兑换 10，其余靠刷 |
| 同上 | `test_token_row_binds` | 没有活动关、已有代币 80、单价 40 → 只换 2 份 |
| 同上 | `test_event_budget_caps_event_runs` | 活动理智行 210 → 活动关最多 10 次 |
| 同上 | `test_budget_mode_spreads_coverage` | 两种材料、预算只够一半 → 两者覆盖率都在 0.4–0.6 |
| 同上 | `test_unobtainable_shows_as_shortfall` | 没有任何来源 → `shortfall` 等于需求，不报无解 |
| 同上 | `test_integerization_respects_stock` | 兑换取整后不超过库存，出击次数为整数 |
| 同上 | `test_linprog_failure_is_reported` | `_linprog_call` 抛异常 → `status` 有原因 |
| `tests/services/economy/test_data.py` | `test_event_stage_merges_with_penguin_option` | 代号相同的企鹅物流选项拿到代币；没有的新建选项 |
| 同上 | `test_unnavigable_event_is_excluded_by_default` | `navigation=missing` 时活动关不进模型，notes 有原因 |
| 同上 | `test_medicine_pool_from_depot_and_item_table` | 合剂 3 瓶（60）+ 小样 2 瓶（10）→ 200 |
| 同上 | `test_missing_item_table_disables_medicine` | 常量表抛错 → 不计用药，notes 有原因 |
| `tests/services/economy/test_service.py` | `test_complete_within_window`、`test_partial_when_window_too_short` | `fits_window` 分别为 true、false；`partial` 时 `days_estimated_full` 大于窗口 |
| 同上 | `test_plan_id_uses_missing_needs_without_depot` | 计划路径不再扣仓库 |
| 同上 | `test_medicine_suggestion_counts_from_small_to_large` | 需要 70 理智、药 10/60/60 → 建议 2 瓶 |
| 同上 | `test_result_is_deterministic` | 两次调用 `as_dict` 相同 |
| 同上 | `test_report_failure_keeps_result` | 产物服务抛错 → `report_artifact_id=None`，notes 有一句 |
| `tests/services/economy/test_report.py` | `test_report_sections_and_no_raw_json` | 有「刷图清单」「商店兑换」；不含 `{"` |
| 同上 | `test_empty_sections_are_omitted` | 没有商店时没有「商店兑换」 |
| `tests/agent/test_economy_tool.py` | `test_tool_registered_as_safe_economy_tool` | 组 `economy`、SAFE、`device=none`、`rest_visible=True`、`untrusted_output=True` |
| 同上 | `test_exactly_one_of_materials_or_plan` | 两个都给、都不给、`materials={}` → `INVALID_PARAMETER` |
| 同上 | `test_rest_call_does_not_write_report`、`test_internal_call_writes_report` | REST 结果 `report_artifact_id=None`；内置调用方有 id |

## 7. 验收

### 7.1 命令（照 12 §4）

```bash
.venv/bin/python -m pytest -q > /tmp/m88-pytest.log 2>&1; echo $?; tail -n 30 /tmp/m88-pytest.log
.venv/bin/python scripts/dump_openapi.py --check; echo $?
.venv/bin/python scripts/agent_smoke.py | tail -n 1
.venv/bin/python scripts/orchestrator_smoke.py | tail -n 1
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m88-reg.log 2>&1; echo $?
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m88-cap.log 2>&1; echo $?
git diff --check; echo $?
.venv/bin/python scripts/check_doc_links.py | tail -n 1
```

期望：pytest 不新增失败；OpenAPI 不变（工具走既有的 invoke 端点）；冒烟 `SMOKE OK`；评测达标；`BROKEN 0`。

### 7.2 联网补充验证（不作为硬性门禁，结果记台账）

```bash
.venv/bin/python - <<'EOF'
import asyncio
from maa_api.services.economy.service import default_farming_optimizer
from maa_api.services.economy.model import FarmingRequest
async def main():
    r = await default_farming_optimizer().optimize(
        FarmingRequest(materials={"固源岩组": 30, "异铁块": 10}, plan_id=None, window_days=7),
        write_report=False, session_id=None, ticket_id=None)
    print(r["status"], r["days_estimated"], [s["stage_code"] for s in r["stages"]][:5], r["notes"][:3])
asyncio.run(main())
EOF
```

`default_farming_optimizer()` 在 `service.py` 里按 T5 的装配口径现建一个实例（没有产物服务）。期望打印出状态、天数与关卡代号；台账记下当期活动是否进入了方案、商店解析出几项。

## 8. 风险与回滚

| 风险 | 对策 |
|---|---|
| PRTS 改了 `EventShopList` 写法 | 解析不到时不计商店并写 notes；夹具测试钉住当前写法 |
| 企鹅物流的活动关代号与 PRTS 对不上 | 对不上的活动关按 PRTS 新建选项，只产代币；notes 记录 |
| 线性规划数值问题 | 两阶段代替大惩罚系数；与 v2 同一求解器与整数化口径 |
| 用药建议被当成指令 | 工具只计算；提示词写明吃药要用户在确认里批准 |

回滚：`git revert -m 1 <merge>`；没有迁移。

## 9. 文档同步

- [05 §3.2](../../05-运行时与代理定义.md)：`planner`、`analyst` 的工具列加 `optimize_farming`(M88)；05 §6.1 的 `optimize_farming` 行补 `untrusted_output=True`。
- [02 §2.2](../../02-系统架构.md)：服务表补 `FarmingOptimizer`（`maa_api/services/economy/service.py`，M88）。
- `docs/v3/14-现行接口清单.md`：不变（没有新端点与错误码）。
- 计数：工具 +1（模型可见 +1）；端点、错误码、表、配置键不变。

## 10. 停下来问

- §0 第 5 条：M75 的产物服务没有「新建产物」的方法，或必须传本工具拿不到的参数。
- T1 重构后 v2 养成规划的任一既有测试结果变了。
- PRTS 当期活动页的商店不是 `EventShopList` 写法，附录 A 的口径套不上。
- 需要新增外部来源或白名单主机。

## 11. 需要补进契约的名字

| 名字 | 种类 | 用途 |
|---|---|---|
| `FarmingOptimizer`、`FarmingRequest`（`maa_api/services/economy/`） | 服务（02 §2.2） | 刷图方案 |
| `maa_api/services/economy/` 包 | 模块（02 §4.1） | 求解、取数、报告 |
| `maa_api/services/prts_parse/event_shop.py` | 模块 | 活动商店与代币掉落解析 |
| `UpgradePlanner.farming_inputs`、`FarmingInputs`、`UpgradePlanService.missing_needs` | v2 服务的公开方法 | 复用配方闭包、掉率与计划缺口 |
| `optimize_farming` 的参数与返回字段（§4.3.1） | 工具契约（05 §6.1） | 规划师与分析师据此汇报 |
| `optimize_farming` 加入 `planner`、`analyst` 工具集 | 代理名单（05 §3.2） | 05 §3.2 目前没写 |

## 附录 A：夹具 `tests/fixtures/prts/activity_zuori_shop.wikitext`

照抄下面的内容（裁剪自 prts.wiki《昨日海》原文，2026-11-02 抓取；同目录 README 补一行来源说明）：

```
==关卡信息==
{| class="wikitable"
!关卡!!推荐等级!!理智消耗!!首次掉落!!常规掉落
|-
|{{关卡链接|YW-6}}
| 精英1 LV.50 || {{道具图标|理智|21|60px}}
| {{关卡报酬|至纯源石|三星获得|60px}}{{关卡报酬|“未来瞻望镜”|三星获得|60px|n=80}}
| {{关卡报酬|“未来瞻望镜”||60px|n=21}}{{关卡报酬|全新装置|概率掉落|60px}}
|-
|{{关卡链接|YW-7}}
| 精英1 LV.60 || {{道具图标|理智|21|60px}}
| {{关卡报酬|至纯源石|三星获得|60px}}{{关卡报酬|“未来瞻望镜”|三星获得|60px|n=80}}
| {{关卡报酬|“未来瞻望镜”||60px|n=21}}{{关卡报酬|转质盐组|概率掉落|60px}}
|-
|{{关卡链接|YW-8}}
| 精英2 LV.10 || {{道具图标|理智|21|60px}}
| {{关卡报酬|至纯源石|三星获得|60px}}{{关卡报酬|“未来瞻望镜”|三星获得|60px|n=80}}
| {{关卡报酬|“未来瞻望镜”||60px|n=21}}{{关卡报酬|液化高能气体|概率掉落|60px}}
|-
|{{关卡链接|YW-EX-1}}
| 精英1 LV.50 || {{道具图标|理智|10|60px}}
| {{关卡报酬|至纯源石|三星获得|60px}}{{关卡报酬|“未来瞻望镜”|三星获得|60px|n=40}}
| {{关卡报酬|至纯源石|三星获得|60px}}
|}
==劳工配给所==
{{#invoke:EventShopList|list
|token_alias=带框_“未来瞻望镜”
|data=
X;;金色阶段;;linear-gradient(45deg, #F1E944aa, #F4CF36aa)
1;;{{道具图标|模组数据块||30px}} 模组数据块;;2;;75;;金
1;;{{道具图标|异铁块||30px}} 异铁块;;10;;40;;紫
1;;{{家具|全域广播扬声器|px=30}} 全域广播扬声器;;1;;40;;灰
X;;紫色阶段;;linear-gradient(45deg, #C0C0D5aa, #9191b2aa)
1;;{{道具图标|聚酸酯组||30px}} 聚酸酯组;;15;;8;;蓝
0;;{{道具图标|龙门币||30px}} 龙门币<small>'''×5000'''</small>;;100;;7;;
X;;无限库存区
0;;{{道具图标|RMA70-12||30px}} RMA70-12;;∞;;40;;
}}
```

期望解析结果：代币 `“未来瞻望镜”`；商品 `模组数据块`（每份 1、库存 2、单价 75、金色阶段）、`异铁块`（1、10、40、金色阶段）、`聚酸酯组`（1、15、8、紫色阶段）、`龙门币`（5000、100、7、紫色阶段）、`RMA70-12`（1、None、40、无限库存区）；家具不收。代币掉落：`YW-6`、`YW-7`、`YW-8` 各 `ap_cost=21, tokens=21`；`YW-EX-1` 不收。
