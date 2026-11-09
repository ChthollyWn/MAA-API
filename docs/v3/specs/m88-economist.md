# M88 资源策略师（规格）

> 阶段：P4　依赖：M87　分支：`economist`　迁移：无
> 本文是 M88 的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)。

> 依赖说明：M87 只是编辑顺序依赖（先占住 `general.md` 的版本与正文基线，避免 M87/M88 同时改同一份定义），M88 不调用 `verifier`、不调用 `copilot`、不读写 M87 的产物。M87 的 tag 门禁按 [12 §2](../12-实施路线与里程碑.md) 保留。

## 0. 前置检查

本机没有 ripgrep，命令一律用 `grep`。逐条跑，任何一条与期望不符就停下来报告。

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在本分支、工作区干净 | `git status -sb` | 第一行 `## economist`，没有其他行 |
| 2 | M87 已合并（仅编辑顺序依赖） | `git tag -l 'v3-m87'` | 输出 `v3-m87`；M87 只提供 `general.md` 的版本与正文基线，M88 不调用 verifier/copilot |
| 3 | v2 线性规划入口 | `grep -nE "def _linprog_call|def _build_lp|def _solve_allocation|async def _solve|async def plan_needs|def _candidate_stages|async def _load_rates|async def _load_recipes" maa_api/services/account/planner.py` | 8 行 |
| 4 | scipy 可用 | `.venv/bin/python -c "from scipy.optimize import linprog; print('ok')"` | `ok` |
| 5 | 产物服务的真实方法 | `grep -n "async def put\|body: bytes\|async def update" maa_api/agent/artifacts/service.py` | 至少 3 行；`put` 的正文参数是 `body: bytes`，没有 `create` |
| 6 | 两个 Worker 定义 | `grep -rlE "^name: (planner|analyst)$" maa_api/agent/specs/builtin/` | 2 个文件 |
| 7 | 拆包表在白名单 | `grep -n "GAMEDATA_FILES.*item_table.json" maa_api/services/external/sources.py` | 1 行 |
| 8 | 工具计数基线 | `.venv/bin/python -c "from maa_api.agent.tools import build_registry as b; r=b(); print(len(r.list()), sum(1 for d in r.list() if d.model_visible))"` | `107 106`（M86 加 3 个工具、M87 不加；05 §6.1 在 M86 的 T5 应已同步为 107/106）。实测值记台账 |
| 9 | 活动页解析与 PRTS 客户端 | `grep -n "def parse_activity_page\|exchange_end_at" maa_api/services/prts_parse/activity.py` | 3 行 |
| 10 | M87 的 ticket 评测驱动 | `grep -n "drive_ticket" maa_api/agent/evals/runner.py` | 至少 1 行；没有就停下来报告，T7 依赖它 |
| 11 | 评测场景数 | `ls tests/evals/scenarios/*.yaml \| wc -l` | `19`（M86 后 13，M87 加 6）。实测值记台账 |
| 12 | 运行上下文能拿到工单 | `grep -n "ticket_id: str \| ticket_id: str" maa_api/agent/harness/context.py` | 至少 1 行（`RunContext.ticket_id`），T5 用它 |
| 13 | 本工具尚未存在 | `grep -rn "optimize_farming" maa_api \| head` | 无输出 |

表格单元里的 `\|` 是 Markdown 转义，复制到 shell 时去掉反斜杠。

## 1. 背景与目标

v2 的养成规划（M27，`UpgradePlanner`）用 `scipy.optimize.linprog` 在常驻关与加工站配方上求「最少理智凑齐材料」，按每天 240 理智估天数。它不看活动商店，不看当期活动关卡的代币，没有时间窗口，也不考虑理智药。用户常问的是「这周能不能凑齐」「活动商店先换什么」「吃不吃药」。

M88 新增工具 `optimize_farming` 与服务 `FarmingOptimizer`，复用 v2 的配方闭包、掉率与候选关筛选，扩展线性规划。做完后能验证的结果：

1. 输入材料清单或养成计划 id，加上当前理智、时间窗口、是否用药，返回刷图清单、预计天数、用药建议、商店兑换建议与中文说明。
2. 当期活动关卡作为代币来源进入规划；活动商店在页面存在 `{{#invoke:EventShopList|list}}` 时按库存上限与代币价格进入规划。代币从 PRTS 活动页解析，活动截止时间限制活动关的可用理智。活动页没有商店小节时，活动关仍可作为材料来源，商店与代币只做已有代币的说明，不阻断方案。
3. 时间窗口不够时给出窗口内的最大覆盖方案（每种材料的覆盖比例），同时给出完整凑齐要几天。
4. 同一组输入的结果确定（HiGHS 求解，两阶段，整数化规则固定），没有活动、没有商店时总理智与 v2 `plan_needs` 一致。
5. 内置 Worker 调用且 `write_report=true` 时写一份 Markdown 报告产物，结果里带 `report_artifact_id`；报告产物的 `meta` 为 `{"farming": true}`。
6. `planner`、`analyst`、`general` 三个代理拿到这个工具，三份定义正文都写明什么时候用；`general` 的版本加 1。

## 2. 非目标

- 不提交任何任务；执行仍走 `start_upgrade_plan`、`submit_pipeline` 与确认。
- 不建模活动任务奖励（打捞方案这类一次性代币）、月卡、源石换理智、剿灭与周常；用户手里已有的代币由 `owned_tokens` 传入。
- 不读当前理智（没有数据源）；由调用方传入，缺省按 0。
- 不新增外部来源；PRTS 与企鹅物流、拆包数据都走既有客户端与白名单。
- 不改 v2 养成计划的规划口径（`start_upgrade_plan` 仍只看常驻关）。
- 不从仓库推断理智药的到期时间。仓库与拆包表都没有每瓶药的到期时间，本工具不把「仓库里的全部理智药」当成可用药；只按 §4.5.4 的降级口径处理。

## 3. 相关文档

- [05 §3.2、§6.1](../05-运行时与代理定义.md)（planner、analyst、general 的工具集；`optimize_farming` 组 `economy`、SAFE、REST 可见、`untrusted_output=true`）；[02 §2.2、§4.1](../02-系统架构.md)（服务表与模块布局）。
- [03 §8.2](../03-数据模型.md)（`artifact` 的 `meta`）；[14 §4.11](../14-现行接口清单.md)（`TOOL_ARGS_INVALID` 422）；[12 §4](../12-实施路线与里程碑.md)（里程碑完成标准）。
- [13 §8.153](../13-决策记录.md)（M84 的 R2 用药口径：`expiring_medicine` 不计入 `medicine` 消耗；本节只用来解释为什么不能把全部药算进天数）、[13 §8.155](../13-决策记录.md)（`ArtifactService.put` 的 `body: bytes` 与 `update`）。
- v2 的用药口径：`grep -n "Q46" docs/v2/13-决策记录.md`（每日刷关的用药口径：自然恢复加 48 小时内过期的理智药，不碎源石）；`grep -n "### 7.6\|### 7.7" docs/v2/superpowers/specs/2026-10-09-m25-prts-knowledge.md`（养成规划与计划；7.6 写明龙门币与作战记录只报需求）。
- 既有代码入口：`maa_api/services/account/planner.py` 的 `_Recipe`（约 411 行）、`_StageOption`（约 421）、`_build_lp`（约 842）、`_solve_allocation`（约 869）、`plan_needs`（约 1156）、`UpgradePlanner._solve`（约 1224）、`_load_recipes`（约 1635）、`_load_rates`（约 1714）、`_candidate_stages`（约 1757）、`_render_stages`（约 1865）、`SANITY_PER_DAY`（约 94）、`_key`（约 155）、`_linprog_call`（约 318）、`REPORT_ONLY_NAMES`（约 126）、`_is_report_only`（约 210）；`maa_api/services/account/plans.py` 的 `UpgradePlanService.refresh`（约 431）、`_depot_counts`（约 700）、`_REPORT_ONLY_NAMES`（约 64）；`maa_api/services/game/activities.py` 的 `CurrentActivity`（约 100）、`ActivityReport`（约 156）、`ActivityService.current`（约 218）、`default_activity_service`（约 755）；`maa_api/services/prts_parse/activity.py` 的 `parse_activity_page(content, *, page_title)`（约 136）；`maa_api/services/external/prts.py` 的 `PrtsClient.wikitext`（约 365）、`WikiTextBatch`（约 151）、`WikiPage`（约 137）；`maa_api/services/external/gamedata.py` 的 `GamedataClient.excel`（约 217）、`GamedataData`（约 109）；`maa_api/agent/tools/account.py` 的 `_planner`（约 407）、`_service`（约 431）；`maa_api/agent/artifacts/service.py` 的 `ArtifactService.put`（约 97）；`maa_api/agent/harness/context.py` 的 `RunContext.ticket_id`（约 186）、`CURRENT_RUN`（约 55）；`maa_api/agent/tools/_services.py` 的 `app_state`。
- PRTS 解析设施：`maa_api/services/prts_parse/wikitext.py` 的 `find_templates`、`parse_templates`、`template_param`（模板按内容切分，不靠正则数花括号）。

## 4. 设计

### 4.1 数据模型

无新表、无迁移、无新枚举。报告存成 M75 的 `artifact` 行（`kind='report'`、`mime='text/markdown'`、`meta={"farming": true}`）。

### 4.2 模块与文件

| 文件 | 新建/修改 | 职责 |
|---|---|---|
| `maa_api/services/economy/__init__.py` | 新建 | 导出 `FarmingOptimizer`、`FarmingRequest`、`FarmingModel`、`ShopGood` |
| `maa_api/services/economy/model.py` | 新建 | 规划输入、商店商品、事件、用药池、结果的数据类 |
| `maa_api/services/economy/solver.py` | 新建 | 纯函数：组矩阵、两阶段求解、整数化与取整后预算修复 |
| `maa_api/services/economy/data.py` | 新建 | 取数：活动、商店、代币、仓库、理智药；合并成 `FarmingModel` |
| `maa_api/services/economy/report.py` | 新建 | Markdown 报告与产物标题 |
| `maa_api/services/economy/service.py` | 新建 | `FarmingOptimizer.optimize` 与默认装配 |
| `maa_api/services/prts_parse/event_shop.py` | 新建 | 解析 `EventShopList` 与关卡表的代币产出；`TokenDrop` 定义在这里 |
| `maa_api/services/account/planner.py` | 改 | 抽出 `_normalize_needs`、`_prepare`、`FarmingInputs`、`farming_inputs`；`StageOption`/`Recipe` 公开别名 |
| `maa_api/services/account/plans.py` | 改 | 抽出 `missing_needs(plan_id)` 与私有 `_missing_from_plan`；`refresh` 改为调用后者 |
| `maa_api/agent/tools/economy.py`、`maa_api/agent/tools/__init__.py` | 新建/改 | `optimize_farming` 工具与注册（放在 `register_account_tools` 之后） |
| `maa_api/agent/specs/builtin/{planner,analyst,general}.md` | 改 | 工具与提示词（§4.7） |
| `tests/fixtures/prts/activity_zuori_shop.wikitext`、`tests/fixtures/prts/README.md` | 新建/改 | 商店与关卡表夹具（附录 A） |
| `tests/fixtures/gamedata/item_table_ap_supplies.json`、`tests/fixtures/gamedata/README.md` | 新建/改 | 理智药的 `apSupplies` 裁剪夹具（§4.5.1） |
| `maa_api/agent/evals/scenario.py`、`tests/evals/environment.py` | 改 | 评测的服务注入 seam（§4.5.9） |

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

@dataclass(slots=True)
class _Prepared:
    recipes: dict[str, _Recipe]
    meta: dict[str, dict[str, Any]]
    options: list[_StageOption]
    status: dict[str, str]

class UpgradePlanner:
    def _normalize_needs(
        self, needs: Mapping[str, int], *, display: dict[str, str]
    ) -> tuple[dict[str, int], list[str]]:
        """入口归一：去重、去非正数、_key、去龙门币与作战记录；空需求写 notes。"""

    async def _prepare(
        self,
        farm_needs: Mapping[str, int],
        *,
        display: dict[str, str],
        server_name: str,
        notes: list[str],
    ) -> _Prepared:
        """配方闭包 → universe → 取掉率 → 候选关 → 补 status；``_solve`` 与 ``farming_inputs`` 共用。"""

    async def farming_inputs(
        self,
        needs: Mapping[str, int],
        *,
        server: str | None = None,
        display: Mapping[str, str] | None = None,
    ) -> FarmingInputs:
        """与 plan_needs 同一套入口归一，再做配方闭包、取掉率、筛候选关；不求解。"""
```

`plan_needs` 的归一/去项段（当前约 1190–1222 行）抽成 `_normalize_needs`；`_solve` 的「配方闭包 → universe → 取掉率 → 候选关 → 补 status」段（当前约 1244–1278 行）抽成 `_prepare`；`_solve` 与 `farming_inputs` 都调 `_prepare`。`plan` 对 report-only 的处理保持原样（`requirements` 仍含龙门币与作战记录），只把 `_solve` 内部改成调用 `_prepare`。

`plans.py`：

```python
class UpgradePlanService:
    async def _missing_from_plan(self, plan: Mapping[str, Any]) -> dict[str, int]:
        """计划需求 − 当前仓库，只留大于 0 的；按 refresh 既有口径只排除龙门币。"""

    async def missing_needs(self, plan_id: str) -> dict[str, int]:
        """计划不存在抛 UPGRADE_PLAN_NOT_FOUND；返回显示名 → 缺口。"""
```

`refresh` 把现在的 `missing = {...}` 段替换为 `missing = await self._missing_from_plan(plan)`；其余不变。**`missing_needs` 不排除作战记录**（v2 refresh 的既有口径），作战记录由 `farming_inputs` 在规划入口去掉；这一点写进 T1 的测试，避免悄悄改 v2 计划行为。

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
    section: str                   # 「金色阶段」「无限库存区」；没有分区标题时写 ""
    unit_sanity: float | None      # 该材料的最低单件理智；merge 时从候选关明细取最小值，取不到为 None

@dataclass(slots=True)
class EventInfo:
    activity: str
    token: str | None
    end_at: datetime | None
    exchange_end_at: datetime | None
    navigable: bool
    drops: dict[str, TokenDrop]                 # 关卡代号 → 代币掉落
    shop: tuple[ParsedShopGood, ...]             # 解析出的原始商品；merge 时再映射成 ShopGood
    stage_ids: dict[str, str]                   # 关卡代号 → stage_id

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
    needs: dict[str, int]
    depot: dict[str, int]
    display: dict[str, str]
    options: list[StageOption]                 # 含活动关；活动关的 rates 可以为空
    event_of: dict[str, str]                   # stage_id → 活动页标题（常驻关不在里面）
    deadline_of: dict[str, datetime]           # stage_id → 活动结束时间
    navigable_of: dict[str, bool]              # stage_id → MAA 能否导航
    tokens_of: dict[str, tuple[str, int]]      # stage_id → (代币名, 每次代币)
    recipes: list[Recipe]
    shop: list[ShopGood]
    owned_tokens: dict[str, int]
    deadline_budgets: list[tuple[datetime, float]]   # 按截止时间升序的累计预算
    now: datetime

@dataclass(frozen=True, slots=True)
class MedicineItem:
    name: str
    ap: int
    count: int

@dataclass(slots=True)
class MedicinePool:
    items: list[MedicineItem]
    owned_sanity: int
    counted_sanity: int            # 本工具计入天数的药理智；仓库没有到期时间，恒为 0
    scope: str                     # "no_expiry_data"
    expiring_medicine: int         # 建议写进 Fight 的值：use_medicine 时 999，否则 0
    note: str
```

`TokenDrop` 与 `ParsedShopGood` 定义在 `prts_parse/event_shop.py`（T2 不依赖 economy 包），`model.py` 直接导入并再导出。`event_shop.py` 的公开签名：

```python
@dataclass(frozen=True, slots=True)
class TokenDrop:
    stage_code: str
    ap_cost: int
    tokens: int          # 每次三星通关的常规代币掉落

@dataclass(frozen=True, slots=True)
class ParsedShopGood:
    item: str
    bundle: int
    stock: int | None
    price: int
    section: str

@dataclass(frozen=True, slots=True)
class ParsedShop:
    token: str | None
    goods: tuple[ParsedShopGood, ...]

def parse_event_shop(content: str) -> ParsedShop: ...
def parse_token_drops(content: str, token: str) -> dict[str, TokenDrop]: ...
```

`solver.py`：

```python
COVERAGE_TOL = 1e-6
EPSILON = 1e-3          # 合成次数与兑换份数的打破平局系数
SHORTFALL_TOL = 0.5     # 短缺量超过半件才判为 unobtainable

@dataclass(slots=True)
class FarmingSolution:
    runs: tuple[int, ...]
    synth: tuple[int, ...]
    buys: tuple[int, ...]
    shortfall: dict[str, float]        # _key 归一后的材料键 → 缺口（单位：件）
    total_sanity: int
    status: str | None                 # 非空表示求解失败的原因
    notes: tuple[str, ...]

@dataclass(slots=True)
class LpMatrix:
    c: list[float]
    A_ub: list[list[float]]
    b_ub: list[float]
    bounds: list[tuple[float, float]]
    x_slice: slice
    y_slice: slice
    z_slice: slice
    u_slice: slice
    x_index: dict[str, int]            # stage_id → 变量下标
    y_index: dict[str, int]            # 配方产物键 → 变量下标
    u_index: dict[str, int]            # 材料键 → 变量下标
    z_order: list[ShopGood]            # z 段按 model.shop 顺序
    row_names: list[str]               # "material:<key>" / "token:<name>" / "deadline:<iso>" / "budget"

def build_matrix(model: FarmingModel, *, budget: float | None) -> LpMatrix: ...
def solve_farming(model: FarmingModel, *, budget: float | None = None) -> FarmingSolution: ...
```

`data.py`：

```python
async def load_events(
    *, activities: Any, prts: Any, now: datetime
) -> tuple[list[EventInfo], list[dict[str, Any]], list[str]]:
    """当期活动 → EventInfo。返回 (活动, 来源信封, notes)；单个活动失败只写 notes 并跳过。"""

async def read_depot(depot: Any) -> tuple[dict[str, int], list[str]]:
    """depot.list_items(limit=1000, offset=0) → {item_id 与 name: 数量}；读不到返回空并写 notes。"""

async def medicine_pool(
    *, gamedata: Any, depot_counts: Mapping[str, int], use_medicine: bool
) -> MedicinePool:
    """item_table.json 的 apSupplies + items 与仓库数量 → 用药池；counted_sanity 恒为 0。"""

def merge(
    inputs: FarmingInputs,
    *,
    depot: Mapping[str, int],
    events: Sequence[EventInfo],
    request: FarmingRequest,
    medicine: MedicinePool,
    now: datetime,
) -> FarmingModel:
    """纯函数：常驻关 + 活动关 + 商店 + 累计截止预算（含 medicine.counted_sanity）。"""
```

`service.py`：

```python
class FarmingOptimizer:
    def __init__(
        self,
        *,
        planner: Any,
        depot: Any,
        plans: Any,
        activities: Any,
        prts: Any,
        gamedata: Any,
        artifacts: Any | None = None,
        now: Callable[[], datetime] = _now,
    ) -> None: ...

    async def optimize(
        self,
        request: FarmingRequest,
        *,
        write_report: bool,
        session_id: str | None,
        ticket_id: str | None,
    ) -> dict[str, Any]: ...

def default_farming_optimizer(app_state: Any | None = None) -> FarmingOptimizer:
    """按 T5 的装配口径现建；app_state 为 None 时全部用 default_* 与现建客户端，没有产物服务。"""
```

模块级 `_now()` 返回 `datetime.now(timezone.utc).astimezone()`；测试注入固定时间。

`report.py`：

```python
def report_title(result: Mapping[str, Any]) -> str:
    """需求为空时「刷图方案：没有剩余需求」；一种材料「刷图方案：<名>」；多种「刷图方案：<首个> 与另外 <n-1> 种材料」；截到 120 字。"""

def render_report(result: Mapping[str, Any], *, generated_at: datetime) -> str:
    """§4.5.6 的 Markdown；没有数据的小节整节省略。"""
```

`tools/economy.py`：

```python
class OptimizeFarmingParams(BaseModel):
    ...

OPTIMIZE_FARMING_DESCRIPTION = "算刷图方案：..."

async def optimize_farming(params: OptimizeFarmingParams, ctx: ToolContext) -> dict[str, Any]: ...
def register_tools(registry: ToolRegistry) -> None: ...
```

### 4.3 接口

#### 4.3.1 工具 `optimize_farming`

| 组 | 风险 | device | read_only | refetchable | untrusted_output | rest_visible |
|---|---|---|---|---|---|---|
| `economy` | SAFE | `none` | True | True | True | True |

描述（工具 `description` 原文）：

```
算刷图方案：给定要凑的材料或养成计划，按当前理智、时间窗口与是否用药，结合企鹅物流掉率、当期活动关卡和活动商店，算出去哪几关刷几次、商店先换什么、要几天、要不要用药。只计算，不提交任务。比 plan_upgrade 多算活动商店与时间窗口；要建每日计划仍用 start_upgrade_plan。
```

参数（`extra="forbid"`；`materials` 与 `plan_id` 恰好一个，空字典算没给）：

```python
class OptimizeFarmingParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    materials: dict[str, Annotated[int, Field(ge=1, le=99999)]] | None = Field(default=None, max_length=60)
    plan_id: str | None = Field(default=None, min_length=1, max_length=36)
    use_depot: bool = True
    current_sanity: int = Field(default=0, ge=0, le=999)
    window_days: float | None = Field(default=None, ge=0.5, le=90)
    use_medicine: bool = False
    include_events: bool = True
    include_unnavigable: bool = False
    owned_tokens: dict[str, Annotated[int, Field(ge=0, le=999999)]] = Field(default_factory=dict, max_length=5)
    server: str | None = Field(default=None, min_length=1, max_length=16)
    write_report: bool = True

    @model_validator(mode="after")
    def _exactly_one(self) -> "OptimizeFarmingParams":
        if bool(self.materials) + bool(self.plan_id) != 1:
            raise ValueError("materials 与 plan_id 必须恰好给一个")
        return self
```

错误：

- `materials` 与 `plan_id` 不是恰好一个 → `TOOL_ARGS_INVALID` 422。Pydantic 的 `ValidationError` 由 `ToolRegistry.validate` 统一转成该码与 422，与工具参数校验契约一致；handler 内不再抛 `INVALID_PARAMETER`。
- `plan_id` 不存在 → `UPGRADE_PLAN_NOT_FOUND`（v2 既有码）。
- 外部来源失败不抛错，写进 `notes` 与 `unobtainable`。
- REST 调用（`CallerType.REST`）或 `write_report=false`：`report_artifact_id` 为 null。

返回（示例数值只是形状示例）：

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
  "sanity": {"needed": 2310, "current": 120, "medicine": 0, "natural_per_day": 240, "window_days": 10},
  "days_estimated": 9.2,
  "days_estimated_full": 9.2,
  "medicine": {"use": false, "scope": "no_expiry_data",
               "items": [{"name": "应急理智合剂", "ap": 60, "count": 3}],
               "owned_sanity": 180, "counted_sanity": 0, "expiring_medicine": 0,
               "max_days_saved": 0.8,
               "note": "没有用药：用户没有同意用理智药"},
  "coverage": {"异铁块": 1.0, "聚酸酯组": 1.0},
  "unobtainable": [],
  "notes": ["按每天自然恢复 240 理智估算，不含月卡与活动赠送"],
  "explanation": "凑齐需要 2310 理智，按当前理智 120 与每天 240 的自然恢复约 9.2 天。",
  "sources": [{"kind": "penguin", "label": "企鹅物流"}, {"kind": "prts", "label": "PRTS", "title": "昨日海"}, {"kind": "gamedata", "label": "拆包数据", "title": "item_table.json"}],
  "report_artifact_id": "9b1c2d3e-..."
}
```

字段口径：

- `status`：`complete`（完整方案全部覆盖，且没给窗口或装得进窗口）、`partial`（给了窗口且完整方案装不下，返回窗口内的方案）、`unobtainable`（完整方案有材料的缺口超过 `SHORTFALL_TOL`，见 `unobtainable`）。
- `fits_window`：没给窗口为 null；否则取 `days_estimated_full <= window_days` 的布尔值。
- `days_estimated` 是本方案的天数；`partial` 时 `days_estimated_full` 是完整凑齐的天数，其余情况两者相同。
- `requirements` 的键是显示名：`{inputs.display.get(key, key): amount for key, amount in inputs.needs.items()}`。
- `stages` 按 `sanity` 降序、`stage_id` 升序；`items` 只列需求闭包里的材料，形状同 v2 `_render_stages`（`item_name`、`rate` 与 v2 的明细字段）。
- `shop` 只列份数大于 0 的商品，按 `value_sanity` 降序（null 排最后）；`quantity = 份数 × bundle`，`tokens = 份数 × price`；`value_sanity = quantity × unit_sanity`，`unit_sanity` 是 `merge` 时从候选关明细的 `sanity_per_item` 取的最小值（取不到为 null，`value_sanity` 也为 null）。
- `tokens`：每个活动代币一行；`earned` 是选中方案的关卡产出，`spent` 是选中方案的兑换花费，`left = owned + earned - spent`（不小于 0）。
- `synthesis`、`unobtainable` 的形状同 v2 `UpgradePlan.as_dict()`；`unobtainable` 的原因优先取 `inputs.rate_status`，否则写「当前可用的关卡、配方与商店凑不齐」。`coverage` 的键与 `unobtainable[*].name` 用显示名。
- `medicine`：`scope` 固定 `no_expiry_data`，`counted_sanity` 固定 0，`expiring_medicine` 为 999 或 0，`max_days_saved` 是「全部仓库药都在 48 小时内过期」时的天数上限，只作参考。
- `report_artifact_id` 是 `Artifact.id`（字符串）；只有内置 Worker 且 `write_report=true` 时非 null。

### 4.4 配置项

无。

### 4.5 行为与算法

#### 4.5.1 取数

```
optimize(request, write_report, session_id, ticket_id):
    now = self._now()
    depot_counts, depot_notes = await read_depot(self._depot)
    if request.plan_id:
        needs = await plans.missing_needs(request.plan_id); depot = {}   # 计划口径已扣过仓库
    else:
        needs = request.materials or {}; depot = depot_counts if request.use_depot else {}
    inputs = await planner.farming_inputs(needs, server=request.server)
    events, sources, event_notes = ([], [], [])
    if request.include_events:
        events, sources, event_notes = await load_events(activities=self._activities,
                                                         prts=self._prts, now=now)
    medicine = await medicine_pool(gamedata=self._gamedata, depot_counts=depot_counts,
                                   use_medicine=request.use_medicine)
    model = merge(inputs, depot=depot, events=events, request=request, medicine=medicine, now=now)
    full = solve_farming(model)
    ...
```

- 仓库：`depot.list_items(limit=1000, offset=0)`，照 `UpgradePlanService._depot_counts` 的形状按 `item_id` 与 `name` 建索引，键用 `_key`；读不到按 0 计并写 notes「仓库数据读取失败，库存按 0 计」。`read_depot` 只读一次，仓库与用药池共用同一份计数。
- 活动：`report = await activities.current(detail="full")`；`report` 是 `ActivityReport`，活动在 `report.activities`。`report.fallback_reason` 非空时写 notes「当期活动没有取到（{reason}），只算常驻关与已有代币」并返回空活动。每个 `CurrentActivity`：`end_at` 为空或晚于 `now` 的才要；`batch = await prts.wikitext([activity.page])`；页面在 `batch.pages[0]`，正文在 `.content`；`parse_activity_page(content, page_title=page.title)` 取 `end_at`/`exchange_end_at`（`end_at` 取 `activity.end_at or parsed.end_at`，`exchange_end_at` 取 `parsed.exchange_end_at`）；`parse_event_shop(content)` 取代币与商品；`parse_token_drops(content, token)` 取关卡的理智与常规代币；`stage_ids` 取 `activity.stages` 的 `code → stage_id`；`navigable = activity.navigation == "available"`。页面缺失、正文为空或解析失败时跳过该活动，写 notes「活动「{name}」的页面读不到，没有计入活动关与商店」。来源信封取 `batch.source_refs()`。
- 商店是可选的：`parse_event_shop` 返回 `ParsedShop(token=None, goods=())` 时，商店为空、代币没有兑换目标；活动关仍按 §4.5.2 合并（有企鹅物流选项的带掉率，没有的只产代币）。notes 写「活动「{name}」的页面没有商店小节，商店没有计入」。没有代币掉落时，活动关不新建 token-only 选项。
- 理智药：`gamedata.excel("item_table.json")` 返回 `GamedataData`，取 `.data`。真实形状是 `apSupplies: {id: {"id": id, "ap": N, "hasTs": true}}` 与 `items: {id: {"name": ..., "itemType": "AP_SUPPLY"}}`；`apSupplies` 的键与 `items` 的键一致。只取 `apSupplies` 里存在且 `items[id].itemType == "AP_SUPPLY"` 的项，按 `items[id].name` 建「名字 → 理智」，再乘仓库同名数量。
  - **到期时间不可得**：`apSupplies` 的 `hasTs=true` 只表示这类物品有有效期，不提供每瓶的到期时间；仓库行（`account_depot_item`）只有 `item_id`/`name`/`count`/`category`/`recognized_at`，没有到期时间。因此本工具无法筛出「48 小时内过期」的药，**不计入任何药的理智**（`counted_sanity=0`、`sanity.medicine=0`），不把仓库里的全部药折成可用理智。这是与 v2 Q46 一致的保守降级：v2 的每日计划本身也不把药算进 `days_estimated`，而是把 `Fight.expiring_medicine` 设为 999 交给内核按到期先后吃。
  - 仓库有药时 `medicine.items` 照实列出来供参考，`owned_sanity` 是全部药的折合理智；`max_days_saved = round(min(owned_sanity, max(needed - current, 0)) / 240, 1)` 是「全部药都在 48 小时内过期」时的天数上限。取不到常量表、`apSupplies` 或 `items` 形状不符时按没有药处理：`items=[]`、`owned_sanity=0`、`counted_sanity=0`、`expiring_medicine=0`，写 notes「没有读到理智药的恢复量，方案里不计用药」。
  - 读表成功时把 `GamedataData.source_ref()` 加进结果的 `sources`。常量表可用且 `use_medicine=true` 时 `expiring_medicine=999`，`note` 写「仓库不提供每瓶理智药的到期时间，天数按不用药估算；要让内核吃 48 小时内过期的药，把 Fight 的 expiring_medicine 设为 999」；`use_medicine=false` 时 `expiring_medicine=0`，`note` 写「没有用药：用户没有同意用理智药」（表不可用时仍用上一条的 note）。
- 理智药夹具：T4 新增 `tests/fixtures/gamedata/item_table_ap_supplies.json`，内容是 2026-11-09 从 `https://fastly.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master/zh_CN/gamedata/excel/item_table.json` 抓取的真实片段，只保留 `ap_supply_lt_60`、`ap_supply_lt_010`、`ap_supply_lt_100` 三条 `apSupplies` 与对应的 `items` 三条，裁掉其余键；来源与抓取日期写进 `tests/fixtures/gamedata/README.md`。

夹具内容：

```json
{
  "items": {
    "ap_supply_lt_60": {"itemId": "ap_supply_lt_60", "name": "应急理智合剂", "itemType": "AP_SUPPLY"},
    "ap_supply_lt_100": {"itemId": "ap_supply_lt_100", "name": "应急理智顶液", "itemType": "AP_SUPPLY"},
    "ap_supply_lt_010": {"itemId": "ap_supply_lt_010", "name": "应急理智小样", "itemType": "AP_SUPPLY"}
  },
  "apSupplies": {
    "ap_supply_lt_60": {"id": "ap_supply_lt_60", "ap": 60, "hasTs": true},
    "ap_supply_lt_100": {"id": "ap_supply_lt_100", "ap": 100, "hasTs": true},
    "ap_supply_lt_010": {"id": "ap_supply_lt_010", "ap": 10, "hasTs": true}
  }
}
```

#### 4.5.2 合并成规划模型

- 常驻候选关取 `inputs.options`。
- 活动关：对每个活动的每个 `TokenDrop`，用代号找 `inputs.options` 里 `stage_code` 相同的选项；找到就复制该选项并记代币；找不到就新建 `StageOption(stage_id=stage_ids.get(code) or f"{activity}:{code}", stage_code=code, ap_cost=drop.ap_cost)`（`rates` 为空，只产代币）。`event_of`、`deadline_of`、`navigable_of`、`tokens_of` 按 `stage_id` 记录。
- `navigable=false` 且 `include_unnavigable=false` 时，这个活动的关卡都不进模型（商店仍可用已有代币），notes 写「活动「{name}」MAA 还不能自动导航，活动关没有计入」。`include_unnavigable=true` 时进模型，`stages` 行的 `navigable=false`。
- 商店：对每个活动的 `ParsedShopGood` 映射成 `ShopGood`（`activity` 取活动页标题、`token` 取代币名、`key=_key(item)`、`unit_sanity` 取 `inputs.options` 的 `details` 里 `_key(detail["item_name"]) == key` 的 `sanity_per_item` 最小值，取不到为 None），只收 `_key(item)` 出现在需求或配方涉及的材料里的商品；这个集合是 `set(inputs.needs) | {recipe.product} | {材料键 for recipe in inputs.recipes for 材料键, _ in recipe.materials}`。龙门币、作战记录不收（v2 口径只报需求）。
- 代币：`owned_tokens` 按代币名原样比较；同一代币的多条掉落合并到一行。
- 截止预算（B5 口径）：对每个有 `end_at` 的活动，`horizon = min(end_at, now + window_days)`（没给窗口时不截断），`days = max((horizon - now).total_seconds() / 86400, 0)`，`budget = current_sanity + days × 240 + medicine.counted_sanity`（本工具 `counted_sanity` 恒为 0）。把 `(horizon, budget)` 按 `horizon` 分组（同一时刻取同一预算），升序放进 `deadline_budgets`；`deadline_of[stage]` 仍记活动的真实 `end_at`。**多个活动重叠时不各给一份当前理智**：求解器按截止时间做累计行（§4.5.3），每个时刻的预算只加一次当前理智与自然恢复。
- `end_at` 为空的活动没有截止行，它的关卡可以在任意时间刷；`exchange_end_at` 只用于结果展示，不进入求解。

#### 4.5.3 线性规划

变量（按顺序）：每个关卡的出击次数 `x_s ≥ 0`；每个配方的合成次数 `y_r ≥ 0`；每个商品的兑换份数 `0 ≤ z_k ≤ stock_k`（`stock` 为 None 时无上界）；每个需求材料的短缺量 `0 ≤ u_i ≤ d_i`，其中 `d_i = max(need_i − depot_i, 0)`，只为 `d_i > 0` 的键建 `u`。

约束（都写成 `A_ub · v ≤ b_ub`，行顺序固定为：材料行、代币行、截止行、总预算行）：

| 约束 | 左边 | 右边 |
|---|---|---|
| 材料行（需求材料与配方涉及的材料各一行，键按 `_key` 排序） | `−Σ rate_{s,i}·x_s − Σ (out_{r,i} − in_{r,i})·y_r − Σ bundle_k·[key_k=i]·z_k − u_i` | `depot_i − need_i`（中间材料 `need` 为 0，也没有 `u_i`） |
| 代币行（每种代币一行，按名字排序） | `Σ price_k·z_k − Σ tokens_s·x_s` | `owned_tokens[token]` |
| 截止行（每个 `deadline_budgets` 时刻一行，升序） | `Σ_{s: deadline_of[s] ≤ 该时刻} ap_s·x_s` | `budget` |
| 总预算行（只在 `budget` 给定时，最后一行） | `Σ ap_s·x_s` | `budget` |

两阶段求解（`method="highs"`，调用 `maa_api.services.account.planner._linprog_call`；用 `import maa_api.services.account.planner as planner` 后按 `planner._linprog_call(...)` 调用，测试 monkeypatch 这个接缝）。`build_matrix` 返回的 `c` 是第一阶段的 `u` 系数；第二阶段复制 `A_ub`/`b_ub` 后追加覆盖率行，再换成第二阶段的目标系数。

1. 第一阶段：`min Σ_i u_i / max(d_i, 1)`，得最优值 `U*`。
2. 第二阶段：加一行 `Σ_i u_i / max(d_i, 1) ≤ U* + COVERAGE_TOL`，`min Σ ap_s·x_s + EPSILON·(Σ y_r + Σ z_k)`。
3. 整数化：`y_r = ceil(y_r − 1e-9)`、`z_k = min(ceil(z_k − 1e-9), stock_k)` 固定为上下界，再解一次第二阶段；无解时沿用整数化前的 `x` 并写 note「合成与兑换取整后没有求出配套的出击次数，出击次数沿用取整前的解」；最后 `x_s = ceil(x_s − 1e-9)`。
4. **取整后修复**（B6）：按整数 `x/y/z` 复查截止行、总预算行与代币行。
   - 预算或截止行超出：在违反该行的关卡里选 `ap_cost` 最大的一个，`x_s -= 1`；同 `ap_cost` 时选 `stage_id` 字典序大的。重复到不再超出或没有可减的关卡。
   - 代币行超出：在 `z_k > 0` 的商品里选 `unit_sanity` 最低的一个（null 视为最低；同值时选 `_key(item)` 字典序大的），`z_k -= 1`；重复到 `spent ≤ owned + earned` 或没有可减的份数。
   - 材料行不做硬修复：取整后的合成可能让某种材料出现缺口，缺口如实进 `shortfall` 与 `coverage`，不把结果标成求解失败。
5. 修复后重新计算 `shortfall_i = max(0, need_i − depot_i − supply_i)`，`supply_i = Σ rate·x + Σ(out−in)·y + Σ bundle·z`，`total_sanity = Σ ap_s·x_s`。有修复时 note 写「取整后按预算削减了 {n} 次出击、按代币削减了 {m} 份兑换」。
6. `_linprog_call` 抛异常或第一阶段无解：`FarmingSolution.status` 写原因，结果 `stages`、`shop` 为空，`status="unobtainable"`，notes 写「线性规划不可用（…）」。

覆盖率：`coverage_i = max(0, 1 − shortfall_i / d_i)`（`d_i = 0` 时为 1），保留两位小数。完整方案里 `shortfall_i > SHORTFALL_TOL` 的材料进 `unobtainable`，原因优先取 `inputs.rate_status`，否则写「当前可用的关卡、配方与商店凑不齐」。

#### 4.5.4 天数与用药

- `days(s, current) = ceil_1(max(s − current, 0) / 240)`；`ceil_1` 是向上取到一位小数（`math.ceil(x * 10) / 10`）。**天数不计任何药**，与 v2 计划的 `total_sanity / 240` 口径一致。
- `medicine.counted_sanity = 0`、`sanity.medicine = 0`；常量表可用时 `medicine.expiring_medicine` 按 `use_medicine` 取 999 或 0，常量表不可用时为 0；`medicine.note` 按 §4.5.1；`medicine.max_days_saved` 按 §4.5.1。
- 完整方案的天数超过窗口时，`budget = current_sanity + window_days × 240`（不计药），用 `solve_farming(model, budget=budget)` 再解一次，`status=partial`；否则用完整方案。
- `partial` 时 `sanity.window_days` 写窗口，`days_estimated` 是窗口内方案的天数，`days_estimated_full` 是完整方案的天数。
- `current_sanity` 缺省 0；`window_days` 缺省 null。

#### 4.5.5 说明文字

`explanation` 由代码按模板拼，不调模型，三到四句：

1. 总量：「凑齐需要 {S} 理智，按当前理智 {current} 与每天 240 的自然恢复约 {days} 天。」
2. 主力关卡：「主要刷 {前两关代号与次数}{；{活动名} 的 {代号} 每次还给 {tokens} 个代币}。」
3. 商店（有兑换时）：「代币先换 {前两项物品与数量}，按掉率折合约 {value} 理智。」
4. 窗口或缺口（有时）：「{window} 天内能做到：{各材料覆盖百分比，最多 3 项}。」或「{材料} 目前凑不齐：{原因}。」

需求为空时 `explanation` 固定为「没有剩余需求：不需要刷关」。

#### 4.5.6 报告产物

`render_report(result, *, generated_at)` 输出：

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
- 拆包数据 item_table.json（{取数时间}）
```

没有数据的小节整节省略。产物标题用 `report_title(result)`；`session_id` 取 `ToolContext.session_id`，`ticket_id` 取 `RunContext.ticket_id`（T5 的 `CURRENT_RUN`）。写入调用：

```python
artifact = await artifacts.put(
    kind="report",
    title=report_title(result),
    body=render_report(result, generated_at=now).encode("utf-8"),
    mime="text/markdown",
    session_id=session_id,
    ticket_id=ticket_id,
    meta={"farming": True},
)
result["report_artifact_id"] = artifact.id
```

产物服务未装配、`put` 抛错或没有 `session_id` 时不写产物，`report_artifact_id` 为 null；抛错时 notes 写「报告没有保存成功」。报告写失败不影响工具结果。

#### 4.5.7 商店与关卡表的解析口径

PRTS 活动页的商店在 `{{#invoke:EventShopList|list … }}` 里（2026-11-09 抓取的《昨日海》原文，附录 A）：

- 用 `find_templates(content, "#invoke:EventShopList", include_nested=False)` 取外层模板，不手写花括号配对；`token_alias` 去掉 `带框_` 前缀就是代币名，与关卡表 `{{关卡报酬|“未来瞻望镜”||60px|n=21}}` 里的名字一致。
- `data` 按行切；每行按 `;;` 切字段，字段两侧去空白：
  - `X;;<分区名>;;…` 是分区标题行；分区名取 `{{` 之前的文本（例如 `往期复刻`、`金色阶段`）。
  - 其余行是 `<标记>;;<物品>;;<库存>;;<单价>;;<颜色>`。
- 物品取第一个 `{{道具图标|<名字>|…}}` 或 `{{家具|<名字>|…}}` 的位置参数 0；`{{家具|` 与 `{{皮肤头像|` 开头的行不是材料，跳过。`<small>'''×N'''</small>` 是每份数量，缺省 1；库存是整数或 `∞`；单价是整数。
- 商店行的 `标记`（0/1）只表示游戏内的兑换状态，解析结果不使用。
- 关卡表：按 `|-` 切行；同一行里同时有 `{{关卡链接|<代号>}}` 与 `{{道具图标|理智|<数字>|` 的才算一关；常规代币掉落是第二个位置参数为空的那个 `{{关卡报酬|<代币>||…|n=<数字>}}`（第二个参数写「三星获得」的是首次掉落，不算）。没有常规代币掉落的关（如 EX 关）不收。
- `parse_event_shop` 与 `parse_token_drops` 都返回纯数据，不联网、不读盘。

#### 4.5.8 边界情况

| 输入 | 期望行为 |
|---|---|
| 没有在开活动 | 只算常驻关与配方；结果与 v2 `plan_needs` 的总理智一致 |
| 活动页没有商店小节 | 活动关仍按代币来源进入模型；`shop` 为空，notes 记一句；不把活动判成失败 |
| 活动页没有 `EventShopList` 且关卡表也没有代币 | 该活动跳过，notes 记一句 |
| 需求只有商店能给（如模组数据块） | 用商店与已有代币覆盖；不够的进 `unobtainable` |
| 代币不够换满库存 | 代币行约束生效，按折合理智高的优先换（由目标函数与取整后修复共同决定） |
| 活动今天结束 | 截止行的 `days` 按 0 算，只剩当前理智；活动关仍可在这点理智内刷 |
| 两个活动同一天结束 | 只加一条累计截止行，当前理智只算一次（B5） |
| 窗口 0.5 天 | 合法；多数情况 `partial` |
| 材料名企鹅物流不认识 | 进 `unobtainable`，原因写「企鹅物流没有这个材料的掉率记录」 |
| `plan_id` 的计划已完成 | `missing_needs` 为空，`status=complete`、`stages` 为空、notes「没有剩余需求」 |
| `plan_id` 的计划含作战记录 | `missing_needs` 保留作战记录（v2 refresh 口径）；`farming_inputs` 去掉它，结果里没有作战记录 |
| `use_depot=false` | 库存不参与抵扣，`depot` 按空；用药池仍读仓库数量；notes 写「use_depot=False：没有用仓库库存抵扣」 |
| 仓库读取失败 | 按 0 计，notes 写原因；方案照常出 |
| `use_medicine=true` 但仓库没有药 | `counted_sanity=0`，`items=[]`，`expiring_medicine=999`，note 说明仓库没有药 |
| 理智药常量表读取失败 | `counted_sanity=0`，notes 写「没有读到理智药的恢复量，方案里不计用药」 |
| `include_events=false` | 不取 PRTS 活动，只算常驻关与配方 |
| `include_unnavigable=false` 且活动 `navigation=missing` | 活动关不进模型，notes 写原因；商店仍可用已有代币 |
| `include_unnavigable=true` | 活动关进模型，`stages` 行的 `navigable=false` |
| scipy 不可用或 `_linprog_call` 抛异常 | `status="unobtainable"`，notes 写原因，不抛错 |
| 产物服务抛错 | `report_artifact_id=null`，notes 有一句；工具结果不失败 |

#### 4.5.9 评测的服务注入 seam

- `World` 增加一个可选字段 `farming_optimizer: str | None = None`（`tests/evals` 用的夹具文件名，相对 `tests/evals/fixtures/`）。`tests/evals/environment.py` 在 `app = main_module.create_app()` 之后、`client.__enter__()` 之前，如果该字段非空，读取 JSON 并 `app.state.farming_optimizer = FakeFarmingOptimizer(payload)`。
- `FakeFarmingOptimizer` 定义在 `tests/evals/environment.py`：`async def optimize(self, request, *, write_report, session_id, ticket_id)` 返回 `copy.deepcopy(payload)`；不写产物，`report_artifact_id` 保持 null。
- `tools/economy.py` 取服务时先看 `app.state.farming_optimizer`；有就用，没有就 `default_farming_optimizer(app.state)` 并写回 `app.state.farming_optimizer`。这样评测不碰真实 PRTS、企鹅物流与拆包客户端，T7 不依赖网络。
- 该 seam 只用于评测与单测替换，生产启动不设 `app.state.farming_optimizer`。

### 4.6 前端

无。报告产物在 M75 的产物视图里看。

### 4.7 提示词与代理定义

三份定义只改 frontmatter 的 `tools` 与 `version`，其余字段与正文其余段落逐字保留现文件，不在本 spec 重抄；本节列出唯一改动与新增段落。

| 文件 | `tools` 改动 | 版本 |
|---|---|---|
| `planner.md` | 在 `tools` 列表末尾（`read_skill_file` 之后）追加 `optimize_farming` | 3 → 4 |
| `analyst.md` | 在 `tools` 列表末尾（`read_skill_file` 之后）追加 `optimize_farming` | 3 → 4 |
| `general.md` | 在 `tools` 列表末尾（`remember` 之后）追加 `optimize_farming` | 6 → 7 |

`planner.md` 正文末尾追加：

```markdown
## 刷图方案

- 用户问「这些材料怎么刷最快」「这周能不能凑齐」「活动商店先换什么」「要不要吃药」时，用 optimize_farming。它在 plan_upgrade 的基础上多算当期活动关卡、活动商店、时间窗口与理智药。
- 用户给的是干员目标时，先 plan_upgrade 算出材料，再把还缺的材料作为 materials 传给 optimize_farming；已经有养成计划就直接传 plan_id。
- 用户没说现在有多少理智就留 current_sanity=0，回答里说明按 0 算；用户给了期限才传 window_days。
- 只有用户同意用药才传 use_medicine=true。仓库数据看不出哪些药 48 小时内过期，天数按不用药估算；方案里的用药建议是让内核用 expiring_medicine=999 自动吃快过期的药，真正吃药仍要在任务确认里批准。
- 汇报先说结论：能不能按期凑齐、要几天、总理智；再列前三个关卡与前三项商店兑换。完整表格在 report_artifact_id 指向的报告里，不要把整张表贴进摘要。
- 要执行，常驻关仍用 start_upgrade_plan 建每日计划；活动关的场次写进汇报，由用户决定要不要另开工单去刷。
```

`analyst.md` 正文末尾追加：

```markdown
## 资源账

- 分析「这期活动值不值得刷」「商店先换什么」「缺的材料还要多久」时，用 optimize_farming。返回里的 shop 已按折合理智排序，stages 写了每关的产出与代币。
- 只给结论与依据：哪几项最划算、为什么、数据来自哪里（企鹅物流、PRTS 活动页）。不替用户决定吃药或碎石。
- 要画图时用 send_chart，数据直接取 optimize_farming 的返回，不要另行估算。
```

`general.md` 正文末尾追加：

```markdown
## 刷图方案

- 用户问「这些材料怎么刷最快」「这周能不能凑齐」「活动商店先换什么」「要不要吃药」时，用 optimize_farming。它按企鹅物流掉率、当期活动关卡与活动商店算方案，只计算，不提交任务。
- 已经有养成计划就传 plan_id；用户直接给材料就传 materials。用户没说当前理智就留 current_sanity=0，回答里说明按 0 算；用户给了期限才传 window_days。
- 只有用户同意用药才传 use_medicine=true。仓库数据看不出哪些药 48 小时内过期，天数按不用药估算；要让内核吃快过期的药，用 Fight 的 expiring_medicine=999。
- 要执行仍用 start_upgrade_plan 建计划，或提交 Fight 流水线；optimize_farming 只给方案。
```

## 5. 实施任务

### T1 规划器重构

- 写范围：`maa_api/services/account/planner.py`、`maa_api/services/account/plans.py`、`tests/services/test_planner_farming_inputs.py`。
- 步骤：
  1. 按 §4.2 把 `plan_needs` 的归一段抽成 `_normalize_needs`，把 `_solve` 的取数段抽成 `_prepare`；加 `FarmingInputs`、`farming_inputs`、`StageOption`/`Recipe` 两个公开别名。`plan` 与 `plan_needs` 的对外字段、notes 与候选口径不变。`display` 由 `_normalize_needs` 与 `_prepare` 就地补全；notes 由 `_normalize_needs`、`_prepare`、`_data_notes` 按原顺序追加。
  2. `plans.py` 抽 `_missing_from_plan` 与 `missing_needs`，`refresh` 改为调用 `_missing_from_plan`；`_REPORT_ONLY_NAMES` 仍只含龙门币。
  3. 新测试：先用重构前的代码对一组固定假数据跑 `plan_needs`，把 `as_dict()` 存成测试常量；重构后逐字段相同。另测 `farming_inputs` 去掉龙门币与作战记录、`missing_needs` 保留作战记录。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/services/test_planner_farming_inputs.py tests/services/test_upgrade_planner.py tests/services/test_upgrade_plans.py tests/services/test_upgrade_plan_lifecycle.py > /tmp/m88-t1.log 2>&1; echo $?; tail -n 5 /tmp/m88-t1.log` → `0`。
- 完成判据：v2 既有的养成规划与计划测试一条不改全部通过；`plan_needs` 的 `as_dict()` 与重构前逐字段相同；`FarmingInputs` 的 `needs` 不含龙门币与作战记录。

### T2 商店与关卡表解析

- 写范围：`maa_api/services/prts_parse/event_shop.py`、`tests/fixtures/prts/activity_zuori_shop.wikitext`、`tests/fixtures/prts/README.md`、`tests/services/test_event_shop_parse.py`。
- 步骤：
  1. 把附录 A 的原文写进夹具；README 补一行来源（URL、抓取日期、裁剪范围）。
  2. 实现 `parse_event_shop`、`parse_token_drops` 与 `TokenDrop`；用 `find_templates`/`parse_templates`，不手写花括号配对。
  3. 测试断言附录 A 的期望值：代币、31 项商品、分区、库存、单价、每份数量、皮肤与家具跳过、YW-EX-1 不收。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/services/test_event_shop_parse.py > /tmp/m88-t2.log 2>&1; echo $?; tail -n 5 /tmp/m88-t2.log` → `0`。
- 完成判据：附录 A 的期望值全部断言通过；测试不读网络。

### T3 求解器

- 写范围：`maa_api/services/economy/__init__.py`、`maa_api/services/economy/model.py`、`maa_api/services/economy/solver.py`、`tests/services/economy/__init__.py`、`tests/services/economy/test_solver.py`。
- 步骤：
  1. 按 §4.2 写 `ShopGood`、`EventInfo`、`MedicineItem`、`MedicinePool`、`FarmingRequest`、`FarmingModel` 数据类；`TokenDrop` 从 `prts_parse.event_shop` 导入。
  2. 按 §4.5.3 写 `build_matrix`、两阶段求解、整数化与取整后修复；`_linprog_call` 从 planner 模块按属性调用。
  3. 测试用手造的 `FarmingModel`，覆盖 §6 的求解器用例；不联网。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/services/economy/test_solver.py > /tmp/m88-t3.log 2>&1; echo $?; tail -n 5 /tmp/m88-t3.log` → `0`。
- 完成判据：§6 的求解器用例全部通过；同一模型重复求解结果相同；取整后的方案不超预算、不超代币、不超库存。

### T4 取数、合并与结果

- 写范围：`maa_api/services/economy/data.py`、`maa_api/services/economy/service.py`、`maa_api/services/economy/report.py`、`tests/fixtures/gamedata/item_table_ap_supplies.json`、`tests/fixtures/gamedata/README.md`、`tests/services/economy/test_data.py`、`tests/services/economy/test_service.py`、`tests/services/economy/test_report.py`。
- 步骤：
  1. 按 §4.5.1 写 `load_events`、`read_depot`、`medicine_pool`；按 §4.5.2 写 `merge`；按 §4.5.4–§4.5.6 写 `optimize` 与报告。
  2. 所有外部依赖从构造参数注入，测试里用假对象：假 `planner.farming_inputs`、假 `activities.current`、假 `prts.wikitext` 返回附录 A 夹具、假 `gamedata.excel` 返回 T4 的 `apSupplies` 夹具、假仓库、假产物服务。
  3. 用药测试锁定 `counted_sanity=0`、`scope="no_expiry_data"`、`expiring_medicine` 999/0；报告测试锁定 `meta={"farming": True}` 与空需求标题。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/services/economy > /tmp/m88-t4.log 2>&1; echo $?; tail -n 5 /tmp/m88-t4.log` → `0`。
- 完成判据：§6 的取数与服务用例全部通过；同一输入跑两次结果完全相同；测试不读网络。

### T5 工具

- 写范围：`maa_api/agent/tools/economy.py`、`maa_api/agent/tools/__init__.py`、`tests/agent/test_economy_tool.py`、`tests/agent/test_tool_catalog.py`、`tests/agent/specs/test_agent_specs.py`。
- 步骤：
  1. 参数、描述与返回按 §4.3.1；`register_tools` 注册 `economy`/SAFE/`device="none"`/`read_only=True`/`refetchable=True`/`untrusted_output=True`/`rest_visible=True`；在 `build_registry()` 里放在 `register_account_tools` 之后。
  2. 装配：`planner` 取 `app.state.planner_service` 或 `default_upgrade_planner()`；`depot` 取 `app.state.depot_service` 或 `DepotService(db.session.session_factory)`；`plans` 取 `app.state.upgrade_plan_service` 或 `default_upgrade_plan_service()`；`activities` 用 `default_activity_service()`；`prts` 用 `activities.prts`；`gamedata` 用 `GamedataClient()`；`artifacts` 取 `app.state.artifact_service`。先看 `app.state.farming_optimizer`，没有就 `default_farming_optimizer(app.state)` 并写回。
  3. 只有 `ctx.caller is CallerType.INTERNAL`、`CURRENT_RUN` 的 `spec.kind == "worker"` 且 `params.write_report=true` 时把 `write_report=True` 传给 optimizer；`session_id` 取 `run.session_id or ctx.session_id`，`ticket_id` 取 `run.ticket_id`（`CURRENT_RUN` 与 `require_run(kinds={"worker"})` 取的是同一个 `RunContext`）。其余调用传 `write_report=False`、`session_id` 与 `ticket_id` 可以传 null。产物由 §4.5.6 的 `ArtifactService.put` 在 optimizer 内写。
  4. `EXPECTED_TOOLS` 加 `optimize_farming`；`UNTRUSTED_OUTPUT_TOOLS` 加它并断言 23；`EXPECTED_REFETCHABLE` 加它（M86 的 `where_am_i` 已使基线从 50 变为 51，这里断言 52）；`test_agent_specs.py` 的 `len(definitions)` 改为 108，`M88_TOOLS` 加 `optimize_farming` 并加入“legacy_chat 不加载”的排除集；两处工具总数断言改 108/107。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/agent/test_economy_tool.py tests/agent/test_tool_catalog.py tests/agent/specs/test_agent_specs.py > /tmp/m88-t5.log 2>&1; echo $?; tail -n 5 /tmp/m88-t5.log` → `0`。
- 完成判据：REST 调用 `POST /api/agent/tools/optimize_farming/invoke` 返回 200 且 `report_artifact_id` 为 null；两个都给或都不给 `materials`/`plan_id` 返回 422 `TOOL_ARGS_INVALID`；评测 seam 能替换服务。

### T6 代理定义

- 写范围：`maa_api/agent/specs/builtin/planner.md`、`analyst.md`、`general.md`、`tests/agent/test_builtin_workers.py`、`tests/agent/specs/test_m70_specs.py`、`tests/api/test_agent_specs_router.py`。
- 步骤：
  1. 按 §4.7 改三份定义：`tools` 末尾加 `optimize_farming`，`version` 各加 1，正文追加对应段落。
  2. `test_builtin_workers.py` 的 planner/analyst 工具元组末尾加 `optimize_farming`，版本断言改为全六项显式映射 `{"daily_ops": 4, "copilot": 4, "planner": 4, "analyst": 4, "diagnose": 4, "ops": 3}[name]`（M87 已把 `daily_ops`/`copilot` 升到 4，不能退回 3）。
  3. `test_m70_specs.py` 加 `ECONOMY_TOOLS = {"optimize_farming"}` 并并入 general 的期望集合；`test_agent_specs_router.py` 的 `EXPECTED_VERSIONS` 改为 `analyst: 4`、`general: 7`、`planner: 4`。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/agent/test_builtin_workers.py tests/agent/specs/test_m70_specs.py tests/api/test_agent_specs_router.py > /tmp/m88-t6.log 2>&1; echo $?; tail -n 5 /tmp/m88-t6.log` → `0`。
- 完成判据：三份定义的 `version` 分别是 4/4/7；三份正文都有「刷图方案」或「资源账」段；工具集断言全部通过。

### T7 评测

- 写范围：`tests/evals/scenarios/m88-planner-week.yaml`、`tests/evals/scenarios/m88-analyst-shop.yaml`、`tests/evals/cassettes/m88-planner-week.jsonl`、`tests/evals/cassettes/m88-analyst-shop.jsonl`、`tests/evals/fixtures/m88-farming-week.json`、`tests/evals/fixtures/m88-farming-shop.json`、`maa_api/agent/evals/scenario.py`、`tests/evals/environment.py`、`tests/evals/test_regression_replay.py`。
- 步骤：
  1. 按 §4.5.9 给 `World` 加 `farming_optimizer` 字段，`tests/evals/environment.py` 加 `FakeFarmingOptimizer` 与注入逻辑。
  2. 两个场景用 `entry.kind: ticket`（M87 的 `drive_ticket`），文件名与 `id` 都用连字符；`world.farming_optimizer` 指向各自的 JSON 夹具。
  3. `m88-planner-week`：`entry: {kind: ticket, worker: planner, title: 本周材料, brief: 这周想凑 20 个异铁块和 10 个聚酸酯组，来得及吗, scope: 用 optimize_farming 算这周来不来得及}`。期望 `optimize_farming(materials={"异铁块":20,"聚酸酯组":10}, window_days=7)` 一次、`report(status="ready")` 的 `summary` 含「天」与「异铁块」；不调用 `start_upgrade_plan`。
  4. `m88-analyst-shop`：`entry: {kind: ticket, worker: analyst, title: 活动商店兑换, brief: 这期活动商店先换什么？我想补 10 个异铁块和 15 个聚酸酯组, scope: 用 optimize_farming 算商店先换什么}`。期望 `optimize_farming(materials={"异铁块":10,"聚酸酯组":15})` 一次、`report(status="ready")` 的 `summary` 含「商店」与「异铁块」；不调用 `submit_pipeline`。
  5. cassette 手写：header 写 `source: handwritten`、`recorded_at`、`model: eval-model`、`agent`；每个 call 的 `reply` 与场景期望的调用一一对应；最后一条给 `text`。
  6. `tests/evals/test_regression_replay.py` 的场景总数改为 21。
- 验证：`.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m88-cap.log 2>&1; echo $?; tail -n 10 /tmp/m88-cap.log` → `0`；输出里 `skipped` 为 0，通过率不低于 M87 台账基线。`tests/evals/test_regression_replay.py` 的全量场景数是 21。
- 完成判据：两个新场景通过；capability 套件通过率不低于 M87 台账基线且没有 skipped；测试不读网络。

### T8 文档同步与台账

- 写范围：`docs/v3/05-运行时与代理定义.md`、`docs/v3/02-系统架构.md`、`docs/v3/03-数据模型.md`、`docs/v3/13-决策记录.md`、`docs/v3/ledgers/m88-economist.md`。
- 步骤：
  1. 按 §9 改 05 §3.2/§6.1、02 §2.2/§4.1、03 §8.2。
  2. 在 13 §8 追加一条 `### 8.<下一个号> 资源策略师落地（M88，<日期>）`，写实现偏离、用药降级口径、工具计数。
  3. 写 `docs/v3/ledgers/m88-economist.md`：分支/基点/合并/tag、做了什么、验证证据、Rulings、偏离、遗留。
  4. 12 §2 的 M88 链接、`specs/README.md` 与 `specs/todo/README.md` 的 M88 行由主 agent 在升格时统一改；本任务不改这三处。
- 验证：`.venv/bin/python scripts/check_doc_links.py | tail -n 1` → `BROKEN 0`；`git diff --check; echo $?` → `0`。
- 完成判据：05/02/03 的契约行与 §11 一致；13 §8 有本里程碑条目；台账存在且验证证据完整。

## 6. 测试清单

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/services/test_planner_farming_inputs.py` | `test_plan_needs_unchanged_after_refactor` | 重构前后 `plan_needs` 的 `as_dict()` 逐字段相同 |
| 同上 | `test_farming_inputs_drops_report_only_items` | 龙门币与作战记录都不进 `needs` |
| 同上 | `test_farming_inputs_empty_after_report_only` | 只有龙门币与作战记录时 `needs` 为空，notes 有「没有剩余需求」 |
| 同上 | `test_farming_inputs_rate_status_matches_plan_needs` | 没有掉率的材料在两边的 `rate_status` 原因相同 |
| 同上 | `test_missing_needs_subtracts_depot` | 需求 30、仓库 12 → 18；仓库够的不出现 |
| 同上 | `test_missing_needs_keeps_battle_records` | 计划含作战记录时 `missing_needs` 仍返回它（v2 refresh 口径） |
| 同上 | `test_missing_needs_plan_not_found` | `UPGRADE_PLAN_NOT_FOUND` |
| `tests/services/test_event_shop_parse.py` | `test_shop_token_and_sections` | 代币 `“未来瞻望镜”`；有「金色阶段」「紫色阶段」「灰色阶段」「无限库存区」；没有「往期复刻」商品 |
| 同上 | `test_shop_items_stock_price_bundle` | 31 项；异铁块 10/40；聚酸酯组 15/8；龙门币 5000/100/7；高级作战记录 2/25/5；家具零件 10/200/2；RMA70-12 库存 None |
| 同上 | `test_shop_skips_furniture_and_skins` | 没有「全域广播扬声器」「流星专属时装」；有「家具零件」 |
| 同上 | `test_token_drops_regular_only` | YW-1 9/9、YW-6 21/21、YW-8 21/21；首次掉落 n=80 不算；YW-EX-1（附录 A）与 YW-ST-1（既有 `activity_zuori.wikitext`）不收 |
| 同上 | `test_page_without_shop` | `activity_vec03.wikitext` → `ParsedShop(token=None, goods=())` |
| 同上 | `test_activity_page_deadline` | `exchange_end_at` 为 `2026-10-30T03:59:00+08:00`，`end_at` 为 `2026-10-23T03:59:00+08:00` |
| `tests/services/economy/test_solver.py` | `test_matrix_row_order_matches_spec` | `row_names` 顺序为材料、代币、截止、预算 |
| 同上 | `test_permanent_only_matches_v2_total` | 同一组关卡与配方，总理智与 v2 `_solve_allocation` 相同 |
| 同上 | `test_shop_beats_farming_when_cheaper` | 商店 1 份 = 1 件、40 代币，活动关 21 理智给 42 代币，常驻关 30 理智 1 件 → 买满 10 份、活动关 10 次 |
| 同上 | `test_stock_cap_is_respected` | 需求 15、库存 10 → 兑换 10，其余靠刷 |
| 同上 | `test_token_row_binds` | 没有活动关、已有代币 80、单价 40 → 只换 2 份 |
| 同上 | `test_event_deadline_caps_event_runs` | 活动截止行 210 → 活动关最多 10 次 |
| 同上 | `test_cumulative_event_budget_caps_overlapping_events` | 两个活动同日结束、当前 0、每天 240、每关 30 理智 → 两活动总次数不超过 8 |
| 同上 | `test_budget_mode_spreads_coverage` | 两种材料、预算只够一半 → 两者覆盖率都在 0.4–0.6 |
| 同上 | `test_unobtainable_shows_as_shortfall` | 没有任何来源 → `shortfall` 与需求相同，不报无解异常 |
| 同上 | `test_integerization_respects_stock` | 兑换取整后不超过库存，出击次数为整数 |
| 同上 | `test_integerization_respects_token_budget` | 取整后 `spent ≤ owned + earned`；有削减 note |
| 同上 | `test_integerization_respects_window_budget` | 向上取整会超预算时，修复后 `Σ ap·x ≤ budget` |
| 同上 | `test_linprog_failure_is_reported` | monkeypatch `planner._linprog_call` 抛异常 → `status` 有原因 |
| `tests/services/economy/test_data.py` | `test_event_stage_merges_with_penguin_option` | 代号相同的企鹅物流选项拿到代币；没有的新建 token-only 选项 |
| 同上 | `test_unnavigable_event_is_excluded_by_default` | `navigation=missing` 时活动关不进模型，notes 有原因 |
| 同上 | `test_include_unnavigable_adds_event_stages` | 同一活动在 `include_unnavigable=true` 时进模型，`navigable_of` 为 false |
| 同上 | `test_cumulative_deadline_budget_does_not_duplicate_current_sanity` | 两个活动同日结束时 `deadline_budgets` 只有一条，预算只加一次当前理智 |
| 同上 | `test_medicine_pool_from_real_ap_supplies` | 合剂 3 瓶（60）+ 小样 2 瓶（10）→ `owned_sanity=200`、`counted_sanity=0` |
| 同上 | `test_medicine_counts_zero_without_expiry_data` | 全部药都在仓库时 `counted_sanity=0`、`scope="no_expiry_data"` |
| 同上 | `test_missing_item_table_keeps_result` | 常量表抛错 → 不计用药，notes 有原因 |
| 同上 | `test_fallback_reason_is_noted` | `fallback_reason` 非空 → 活动为空，notes 有原因 |
| `tests/services/economy/test_service.py` | `test_complete_within_window`、`test_partial_when_window_too_short` | `fits_window` 分别为 true、false；`partial` 时 `days_estimated_full` 大于窗口 |
| 同上 | `test_plan_id_uses_missing_needs_without_depot` | 计划路径不再读仓库 |
| 同上 | `test_medicine_note_and_expiring_value` | `use_medicine=true` → 999 与 note；false → 0 |
| 同上 | `test_result_is_deterministic` | 两次调用结果相同 |
| 同上 | `test_include_events_false_skips_events` | `activities.current` 不被调用；结果没有活动来源 |
| 同上 | `test_result_shape_covers_sources_and_medicine` | `coverage` 的键是需求材料；`sources` 含 `penguin` 与 `prts`；`sanity["medicine"]` 与 `medicine["counted_sanity"]` 都是 0 |
| 同上 | `test_report_failure_keeps_result` | 产物服务抛错 → `report_artifact_id=None`，notes 有一句 |
| 同上 | `test_report_meta_is_farming` | `put` 的 `meta` 恰为 `{"farming": True}`，`kind="report"`、`body` 是 UTF-8 字节 |
| `tests/services/economy/test_report.py` | `test_report_sections_and_no_raw_json` | 有「刷图清单」「商店兑换」；不含 `{"` |
| 同上 | `test_empty_sections_are_omitted` | 没有商店时没有「商店兑换」 |
| 同上 | `test_empty_needs_title` | `report_title` 为「刷图方案：没有剩余需求」 |
| `tests/agent/test_economy_tool.py` | `test_tool_registered_as_safe_economy_tool` | 组 `economy`、SAFE、`device=none`、`rest_visible=True`、`untrusted_output=True`、`refetchable=True` |
| 同上 | `test_exactly_one_of_materials_or_plan` | 两个都给、都不给、`materials={}` → `TOOL_ARGS_INVALID` 422 |
| 同上 | `test_server_is_forwarded` | `server="JP"` 进 `FarmingRequest.server` |
| 同上 | `test_rest_call_does_not_request_report`、`test_internal_call_passes_ticket_to_optimizer` | REST 的假 optimizer 收到 `write_report=False`；内置 Worker 收到 `write_report=True`、`ticket_id` 来自 `RunContext`、`session_id` 来自运行 |
| 同上 | `test_service_seam_is_used` | `app.state.farming_optimizer` 被调用，默认装配不触发 |
| 同上 | `test_include_flags_forwarded` | `include_events`、`include_unnavigable`、`use_depot`、`owned_tokens` 原样进 `FarmingRequest` |
| `tests/agent/test_builtin_workers.py` | `test_six_workers_match_contract` | planner/analyst 工具元组末尾有 `optimize_farming`；版本 4 |
| `tests/agent/specs/test_m70_specs.py` | `test_general_tools_equal_legacy_chat_minus_delegate_recon_plus_worker_tools` | general 的工具集合多 `optimize_farming` |
| `tests/api/test_agent_specs_router.py` | 版本断言 | planner 4、analyst 4、general 7 |
| `tests/agent/test_tool_catalog.py` | `test_untrusted_output_tools_are_pinned`、`test_refetchable_tools_are_pinned`、工具全集断言 | untrusted 23；refetchable 52；工具总数 108、模型可见 107 |
| `tests/agent/specs/test_agent_specs.py` | `test_legacy_chat_lists_every_v2_registered_tool_in_registration_order` | `len(definitions)==108`；`M88_TOOLS` 被排除出 legacy_chat |
| `tests/evals/test_regression_replay.py` | 场景总数 | 21 个场景都有 cassette |
| `tests/evals/scenarios/m88-planner-week.yaml`、`m88-analyst-shop.yaml` | 两个场景 | §5 T7 的期望调用与文本断言 |

## 7. 验收

### 7.1 命令（照 12 §4）

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
```

期望：pytest 不新增失败；OpenAPI 不变（工具走既有的 invoke 端点）；两个冒烟末行 `SMOKE OK`；评测通过且 capability 无 skipped；`BROKEN 0`；`git diff --check` 为 0；最后一条打印 `108 107`。

### 7.2 联网补充验证（不作为硬性门禁，结果记台账）

```bash
.venv/bin/python - <<'EOF'
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
```

期望打印状态、天数与关卡代号；台账记下当期活动是否进入方案、商店解析出几项。今天（2026-11-09）的当期活动是《卫戍协议·规程》，它的关卡表是 `#lsth` 转写、单页不含 `{{关卡链接}}`，所以联网结果可能没有活动关；这不影响 T2/T4 的夹具测试。

## 8. 风险与回滚

| 风险 | 对策 |
|---|---|
| T1 的重构改变 v2 行为 | T1 只做纯重构；回归 `tests/services/test_upgrade_planner.py`、`tests/services/test_upgrade_plans.py`、`tests/services/test_upgrade_plan_lifecycle.py`；任一字段变化就回滚该提交，不改 v2 口径 |
| PRTS 改了 `EventShopList` 写法 | 解析不到时不计商店并写 notes；附录 A 夹具钉住 2026-11-09 的真实写法；`find_templates` 不依赖正则花括号 |
| 企鹅物流的活动关代号与 PRTS 对不上 | 对不上的活动关按 PRTS 新建 token-only 选项，只产代币；notes 记录 |
| 两个活动重叠导致预算重复 | 用累计截止行，当前理智与自然恢复只加一次；`test_cumulative_event_budget_caps_overlapping_events` 锁定 |
| 整数化突破预算或代币 | 取整后修复；两个修复用例锁定；材料缺口如实进 `shortfall` |
| 用药被悄悄放宽成全部药 | 只报 `owned_sanity` 作参考，`counted_sanity` 恒为 0；`use_medicine` 只改 `expiring_medicine` 与 note |
| 评测依赖 M87 的 ticket 驱动 | §0 #10 检查；没有就停工；seam 只替换 optimizer，不依赖网络 |
| 报告产物写失败 | 工具结果不失败，`report_artifact_id` 为 null，notes 记一句 |
| 数值求解器差异 | 两阶段代替大惩罚系数；与 v2 同一 HiGHS 与 `_linprog_call` 接缝 |

回滚：`git revert -m 1 <merge>`；没有迁移。T1 的单独提交可先 `git revert <T1 commit>`，不影响 T2–T8。

## 9. 文档同步

- [05 §3.2](../05-运行时与代理定义.md)：`planner`、`analyst`、`general` 的工具列末尾追加 `optimize_farming`(M88)；三份定义的 `version` 分别改为 4、4、7。
- [05 §6.1](../05-运行时与代理定义.md)：M80 行的「22 个」改为「23 个」；计数句改为「当前 108 个、模型可见 107 个」（M86 后为 107/106，本里程碑加 1）；补一段工具契约，写 §4.3.1 的参数、返回字段、`TOOL_ARGS_INVALID` 422 与 `UPGRADE_PLAN_NOT_FOUND`。
- [02 §2.2](../02-系统架构.md)：服务表补 `FarmingOptimizer`（`maa_api/services/economy/service.py`，M88）；类型 `FarmingRequest`/`FarmingInputs`/`FarmingModel` 的说明放 05 §6.1 的契约段，不写成服务。
- [02 §4.1](../02-系统架构.md)：在 `maa_api/agent/` 树后补 `maa_api/services/` 子树：`economy/`（`__init__.py`、`model.py`、`solver.py`、`data.py`、`report.py`、`service.py`，M88）与 `prts_parse/event_shop.py`（M88）。
- [03 §8.2](../03-数据模型.md)：`artifact.meta` 行补 `farming`（bool，M88 的刷图报告写 `{"farming": true}`）。
- [14](../14-现行接口清单.md)：不变（没有新端点、错误码、表、配置键；`TOOL_ARGS_INVALID` 422 与 `UPGRADE_PLAN_NOT_FOUND` 都是既有码）。
- 计数：工具 108/107（M86 后 107/106，M88 +1）；端点 186（agent 83）不变；错误码 171/170 不变；业务表 57 不变；配置键 143/132 不变；评测场景 21；迁移无。
- `12 §2` 的 M88 行、`docs/v3/specs/README.md` 与 `docs/v3/specs/todo/README.md` 的 M88 行由主 agent 在升格时统一改；本任务只保证文件名与内容已按升格后格式写。
- [13 §8](../13-决策记录.md)：追加一条实施记录（用药降级口径、M87 仅编辑顺序依赖、工具计数、`meta.farming`）。
- 台账：`docs/v3/ledgers/m88-economist.md`。

## 10. 停下来问

- §0 第 2 条没有 `v3-m87`，或第 10 条没有 `drive_ticket`。
- §0 第 5 条的 `ArtifactService.put` 不再是 `body: bytes`，或没有 `put`。
- T1 重构后 v2 养成规划的任一既有测试结果变了。
- 附录 A 的夹具在本仓库解析不出 31 项商品、8 个代币掉落关或 `exchange_end_at`。
- 需要新增外部来源、白名单主机或新的错误码。
- 需要把商店功能从「页面有就解析」改成「必须联网抓当期商店」。
- 需要把用药口径改成「全部仓库药」或新增按瓶数传药的参数。

## 11. 需要补进契约的名字

| 名字 | 种类 | 用途 | 落点 |
|---|---|---|---|
| `FarmingOptimizer` | 服务 | 刷图方案 | 02 §2.2 |
| `FarmingRequest`、`FarmingInputs`、`FarmingModel`、`ShopGood`、`MedicinePool` | 类型 | 工具与服务之间的数据形状 | 05 §6.1 工具契约段；02 §2.2 只登记 `FarmingOptimizer` |
| `maa_api/services/economy/` 包 | 模块 | 求解、取数、报告 | 02 §4.1 的 services 子树 |
| `maa_api/services/prts_parse/event_shop.py` | 模块 | 活动商店与代币掉落解析 | 02 §4.1 |
| `UpgradePlanner.farming_inputs`、`FarmingInputs` | v2 服务公开方法 | 复用配方闭包、掉率与候选关 | 02 §2.2 的 `UpgradePlanner` 行或 05 §6.1；注明纯重构、v2 行为不变 |
| `UpgradePlanService.missing_needs` | v2 服务公开方法 | 计划缺口 | 02 §2.2 的 `UpgradePlanService` 行；注明 refresh 行为不变 |
| `optimize_farming` 的参数、返回字段与错误码 | 工具契约 | planner/analyst/general 据此汇报 | 05 §6.1 新增契约段 |
| `optimize_farming` 加入 `planner`、`analyst`、`general` | 代理名单 | 三份定义的工具集 | 05 §3.2 三行；general 正文加一段 |
| `untrusted_output` 23、工具总数 108/107、refetchable 52 | 计数 | 工具目录 | 05 §6.1 与 `tests/agent/test_tool_catalog.py` |
| `artifact.meta.farming` | JSON 字段 | 刷图报告识别 | 03 §8.2 |
| 不新增 | 端点/错误码/表/配置键 | — | 14、03、config.template.yaml 不变 |

## 附录 A：夹具 `tests/fixtures/prts/activity_zuori_shop.wikitext`

来源：`https://prts.wiki/w/昨日海?action=raw`（HTTP 200，15 684 字节），抓取日期 **2026-11-09**。这是《昨日海》活动页的真实片段，**裁剪**范围：

- `{{活动信息}}` 只保留 `类型`、`名称`、`名称缩短`、`活动开始时间`、`活动结束时间`、`兑换结束时间`、`理智本消耗` 七个字段。
- `==关卡信息==` 只保留主关 `YW-1`–`YW-8` 与 `YW-EX-1` 一行；`YW-ST-*`、其余 EX 关与 `突袭模式报酬` 表的其余行剪掉。
- `==劳工配给所==` 的 `{{#invoke:EventShopList|list}}` 整段原样保留。

同日也抓了当期《卫戍协议·规程》的原文：`EventShopList` 存在，但关卡表是 `{{#lsth:关卡一览/活动关卡|卫戍协议·规程}}` 转写，单页没有 `{{关卡链接}}`，不能同时提供商店与关卡表。因此夹具仍用《昨日海》；测试注入固定 `now=2026-10-20`，不依赖抓取当天的活动。

```
{{活动信息
|类型=支线故事
|名称=SideStory「昨日海」
|名称缩短=昨日海
|活动开始时间=2026-10-09 12:00
|活动结束时间=2026-10-23 03:59
|兑换结束时间=2026-10-30 03:59
|理智本消耗=21
}}
==关卡信息==
{| class="wikitable" style="white-space:normal;display:table;text-align:center;"
!关卡!!推荐等级!!理智消耗!!首次掉落!!常规掉落
|-
|{{关卡链接|YW-1}}
| LV.20 || {{道具图标|理智|9|60px}}
| {{关卡报酬|至纯源石|三星获得|60px}}{{关卡报酬|“未来瞻望镜”|三星获得|60px|n=40}}
| {{关卡报酬|“未来瞻望镜”||60px|n=9}}{{关卡报酬|源岩|概率掉落|60px}}{{关卡报酬|破损装置|小概率|60px}}{{关卡报酬|酯原料|小概率|60px}}<br>{{关卡报酬|代糖|小概率|60px}}{{关卡报酬|异铁碎片|小概率|60px}}{{关卡报酬|双酮|小概率|60px}}
|-
|{{关卡链接|YW-2}}
| LV.35 || {{道具图标|理智|9|60px}}
| {{关卡报酬|至纯源石|三星获得|60px}}{{关卡报酬|“未来瞻望镜”|三星获得|60px|n=40}}
| {{关卡报酬|“未来瞻望镜”||60px|n=9}}{{关卡报酬|源岩|概率掉落|60px}}{{关卡报酬|破损装置|小概率|60px}}{{关卡报酬|酯原料|小概率|60px}}<br>{{关卡报酬|代糖|小概率|60px}}{{关卡报酬|异铁碎片|小概率|60px}}{{关卡报酬|双酮|小概率|60px}}
|-
|{{关卡链接|YW-3}}
| 精英1 LV.1 || {{道具图标|理智|12|60px}}
| {{关卡报酬|至纯源石|三星获得|60px}}{{关卡报酬|“未来瞻望镜”|三星获得|60px|n=60}}
| {{关卡报酬|“未来瞻望镜”||60px|n=12}}{{关卡报酬|装置|小概率|60px}}{{关卡报酬|糖|固定掉落|60px}}
|-
|{{关卡链接|YW-4}}
| 精英1 LV.20 || {{道具图标|理智|12|60px}}
| {{关卡报酬|至纯源石|三星获得|60px}}{{关卡报酬|“未来瞻望镜”|三星获得|60px|n=60}}
| {{关卡报酬|“未来瞻望镜”||60px|n=12}}{{关卡报酬|固源岩|固定掉落|60px}}{{关卡报酬|酮凝集|小概率|60px}}
|-
|{{关卡链接|YW-5}}
| 精英1 LV.30 || {{道具图标|理智|12|60px}}
| {{关卡报酬|至纯源石|三星获得|60px}}{{关卡报酬|“未来瞻望镜”|三星获得|60px|n=60}}
| {{关卡报酬|“未来瞻望镜”||60px|n=12}}{{关卡报酬|糖|概率掉落|60px}}{{关卡报酬|酮凝集|大概率|60px}}
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
|}
{| class="wikitable" style="white-space:normal;display:table;text-align:center;"
!关卡!!推荐等级!!理智消耗!!首次掉落!!突袭模式报酬
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
X;;往期复刻 {{popup|内容=本部分物品为往期实装的时装。<br>{{mdi|circle-small}}在未获得的情况下可消耗对应活动代币兑换。<br>{{mdi|circle-small}}活动结束后<b>本部分物品将不再在后续活动（含复刻）中上架</b>，而是进入采购中心【高级凭证区】与【通用凭证区】。<br>{{mdi|circle-small}}本部分物品的兑换状态与其它获取渠道互通，已获得时无法再兑换。|颜色=white}};;#b7864c;;white
0;;{{皮肤头像|流星|30px|1}} '''流星专属时装''' 诗人的假日;;1;;500;;灰
X;;金色阶段;;linear-gradient(45deg, #F1E944aa, #F4CF36aa, #ddbdbdaa, #EAEBEE40 40%)
1;;{{道具图标|旅骨的信物||30px}} '''阶段商品1：'''旅骨的信物;;1;;200;;金
1;;{{道具图标|旅骨的信物||30px}} '''阶段商品2：'''旅骨的信物;;1;;240;;金
1;;{{道具图标|旅骨的信物||30px}} '''阶段商品3：'''旅骨的信物;;1;;280;;金
1;;{{道具图标|旅骨的信物||30px}} '''阶段商品4：'''旅骨的信物;;1;;320;;金
1;;{{道具图标|旅骨的信物||30px}} '''阶段商品5：'''旅骨的信物;;1;;360;;金
1;;{{道具图标|寻访凭证||30px}} 寻访凭证;;3;;150;;金
1;;{{道具图标|模组数据块||30px}} 模组数据块;;2;;75;;金
1;;{{道具图标|聚合剂||30px}} 聚合剂;;5;;100;;金
1;;{{道具图标|异铁块||30px}} 异铁块;;10;;40;;紫
1;;{{道具图标|精炼溶剂||30px}} 精炼溶剂;;10;;35;;紫
1;;{{道具图标|手性屈光体||30px}} 手性屈光体;;10;;35;;紫
1;;{{家具|全域广播扬声器|px=30}} 全域广播扬声器;;1;;40;;灰
1;;{{家具|阿戈尔风格演讲台|px=30}} 阿戈尔风格演讲台;;1;;45;;灰
1;;{{家具|藻类培养池|px=30}} 藻类培养池;;1;;50;;灰
1;;{{家具|一体式鞋架|px=30}} 一体式鞋架;;1;;55;;灰
1;;{{家具|立式经书台|px=30}} 立式经书台;;1;;60;;灰
1;;{{家具|经文抄写台|px=30}} 经文抄写台;;1;;60;;灰
X;;紫色阶段;;linear-gradient(45deg, #C0C0D5aa, #9191b2aa, #EAEBEE40 40%)
1;;{{道具图标|数据增补仪||30px}} 数据增补仪;;10;;15;;金
1;;{{道具图标|数据增补条||30px}} 数据增补条;;60;;5;;紫
1;;{{道具图标|聚酸酯组||30px}} 聚酸酯组;;15;;8;;蓝
1;;{{道具图标|化合切削液||30px}} 化合切削液;;10;;12;;蓝
0;;{{道具图标|龙门币||30px}} 龙门币<small>'''×5000'''</small>;;100;;7;;
0;;{{道具图标|高级作战记录||30px}} 高级作战记录<small>'''×2'''</small>;;25;;5;;
0;;{{道具图标|中级作战记录||30px}} 中级作战记录<small>'''×2'''</small>;;50;;3;;
0;;{{道具图标|初级作战记录||30px}} 初级作战记录<small>'''×2'''</small>;;120;;1;;
0;;{{道具图标|技巧概要·卷3||30px}} 技巧概要·卷3;;25;;4;;
0;;{{道具图标|技巧概要·卷2||30px}} 技巧概要·卷2;;50;;2;;
X;;灰色阶段
0;;{{道具图标|糖||30px}} 糖;;25;;3;;
0;;{{道具图标|聚酸酯||30px}} 聚酸酯;;25;;3;;
0;;{{道具图标|异铁||30px}} 异铁;;25;;3;;
1;;{{道具图标|特种芯片||30px}} 特种芯片;;5;;6;;蓝
0;;{{道具图标|家具零件||30px}} 家具零件<small>'''×10'''</small>;;200;;2;;
X;;无限库存区
0;;{{道具图标|RMA70-12||30px}} RMA70-12;;∞;;40;;
0;;{{道具图标|研磨石||30px}} 研磨石;;∞;;35;;
0;;{{道具图标|轻锰矿||30px}} 轻锰矿;;∞;;30;;
0;;{{道具图标|扭转醇||30px}} 扭转醇;;∞;;25;;
0;;{{道具图标|龙门币||30px}} 龙门币<small>'''×20'''</small>;;∞;;1;;
}}
```

期望解析结果：

- 代币 `“未来瞻望镜”`。
- 商品 31 项；分区为「金色阶段」「紫色阶段」「灰色阶段」「无限库存区」，「往期复刻」只有时装、没有商品。
- 关键行：`模组数据块`（每份 1、库存 2、单价 75、金色阶段）、`异铁块`（1、10、40、金色阶段）、`聚酸酯组`（1、15、8、紫色阶段）、`龙门币`（5000、100、7、紫色阶段）、`高级作战记录`（2、25、5、紫色阶段）、`家具零件`（10、200、2、灰色阶段）、`RMA70-12`（1、None、40、无限库存区）、`龙门币`（20、None、1、无限库存区）。
- 家具与皮肤不出现在结果里。
- 代币掉落：`YW-1`、`YW-2` 各 `ap_cost=9, tokens=9`；`YW-3`、`YW-4`、`YW-5` 各 `12/12`；`YW-6`、`YW-7`、`YW-8` 各 `21/21`；`YW-EX-1` 与 `YW-ST-*` 不收；`n=80` 是首次掉落，不算。
- `parse_activity_page`：`end_at=2026-10-23T03:59:00+08:00`、`exchange_end_at=2026-10-30T03:59:00+08:00`。
