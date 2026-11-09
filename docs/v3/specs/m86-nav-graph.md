# M86 界面导航图（规格）

> 阶段：P4　依赖：M85（直接）；先合并维护性补丁 `evals-ticket-driver`；用到 M67 的视觉角色、M70 的导航员、M71 的设备租约　分支：`nav-graph`　迁移：`0041_nav_graph`
> 本文是 M86 的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在本分支、工作区干净 | `git status -sb` | 第一行 `## nav-graph`，没有其他行 |
| 2 | M85 已合并 | `git tag -l 'v3-m85'` | 输出 `v3-m85` |
| 3 | 迁移 head 是 0040 | `ls maa_api/db/migrations/versions/ \| grep -E '^[0-9]{4}_' \| sort \| tail -n 1` | `0040_question_expiry_historical.py`；不是就停下来问（0041 已被 03 §2 登记给本里程碑，不取下一个空号） |
| 4 | 导航员定义存在 | `grep -n "^name: navigator$" maa_api/agent/specs/builtin/navigator.md` | 1 行 |
| 5 | 工具扩展字段（M65） | `grep -nE "^\s+(device\|refetchable\|untrusted_output\|rest_visible):" maa_api/agent/registry.py` | 4 行 |
| 6 | 按角色解析模型（M67） | `grep -n "async def resolve" maa_api/agent/llm/roles.py` | 2 行（`resolve`、`resolve_for_spec`，都是 async） |
| 7 | 结构化输出入口（M67） | `grep -n "^def register_schema\|^def get_schema\|^async def complete_structured" maa_api/agent/llm/structured.py` | 3 行 |
| 8 | 租约中间件（M71） | `grep -n "class LeaseMiddleware" maa_api/agent/lease.py` | 1 行，约 644 行 |
| 9 | 子代理沿用父 Worker 的原子操作授权 | `grep -n "_policy_session_id\|POLICY_SESSION_KEY" maa_api/agent/harness/builtin_middleware.py` | 7 行；没有就按 §10 停下来问 |
| 10 | REST 调用写设备工具时抢占租约 | `grep -n "preempt" maa_api/api/routers/agent.py` | 6 行 |
| 11 | 工具计数基线 | `.venv/bin/python -c "from maa_api.agent.tools import build_registry as b; r=b(); print(len(r.list()), sum(1 for d in r.list() if d.model_visible))"` | `104 103`；T5 在它上面加 3 |
| 12 | 图像库可用 | `.venv/bin/python -c "import PIL, numpy; print('ok')"` | `ok` |
| 13 | 现行接口清单 | `test -f docs/v3/14-现行接口清单.md && echo ok` | `ok` |
| 14 | 截图归档的既有实现 | `grep -n "def archive_screenshot_result" maa_api/agent/harness/builtin_middleware.py` | 1 行，约 1408 行 |
| 15 | 视觉客户端要用的装配 | `grep -n "app.state.model_router = ModelRouter\|app.state.cost_ledger = CostLedger" maa_api/main.py` | 2 行 |
| 16 | 评测 ticket 驱动补丁已合并 | `grep -n "def drive_ticket" maa_api/agent/evals/runner.py`；`grep -n "class HumanPolicy" maa_api/agent/evals/scenario.py` | 各 1 行；没有就按 §10 停下来问（先落地维护性补丁 `evals-ticket-driver`） |

表格单元里的 `\|` 是 Markdown 转义，复制到 shell 时去掉反斜杠。

第 3、6、7、8、9、10、15、16 条不符合时不要自己补，按 §10 停下来问。

## 1. 背景与目标

v2 的界面导航完全靠模型逐次「截图、读图、点一下、再截图」。2026-10-17 的实机会话里两段点击环花了 2.79 M prompt token，占全场 48%，12 次点击 11 次打空（[v2/13 §8.80](../../v2/13-决策记录.md)）。M86 把常用界面与它们之间的跳转存成一张图，让一次 `navigate_to` 调用在服务端完成识别、最短路径、逐步执行与校验。

做完能验证的结果：

1. `nav_node`、`nav_edge` 两张表落库；首次启动写入 15 个种子界面与 36 条种子跳转，坐标全部是归一化坐标，每个坐标都能在 MAA 资源里找到出处（§4.5.6）。
2. `where_am_i` 在已有样本的界面上 200 ms 内给出识别结果（只算感知哈希，不调模型）；没有把握时，有视觉模型就让它在候选里选，没有就如实返回 unknown。
3. `navigate_to(target)` 一次调用走完全程：每一步执行后截图校验，走错了就从实际所在界面重新规划，认不出画面就回主界面重来；全程不超过 `nav.max_steps` 步。
4. 成功的跳转累计成功次数与耗时，失败累计失败次数，失败达到阈值自动停用；走错到别的已知界面时记一条学到的跳转；导航员手动找到新界面后用 `remember_screen` 记下，下次一步到。
5. `navigate_to` 走原子操作的会话授权；内置调用由 `LeaseMiddleware` 持有设备租约，REST 调用沿用 M71 口径只抢占、不持有租约。
6. 前端「界面导航图」页能看界面与跳转、标记当前画面、启停与删除跳转、新建跳转。
7. 评测：同一段导航，用 `navigate_to` 的模型调用次数不超过 3 次；真机补充测量记录 prompt token 对比（§7.3）。

## 2. 非目标

- 不用 MaaCore 的模板匹配做逐步识别（可行性结论见 §4.5.1）。
- 不做模板图裁剪，不往 custom 层写图片。
- 不做多分辨率适配：只支持 16:9 设备（与 `DeviceManager.click` 的换算前提一致）。
- 不自动发现全部关卡：关卡页节点只在导航员手动找到后学习，或由用户在导航页新建。
- 不删除界面节点（没有端点）；错误的学习结果通过清空样本、停用或删除跳转修正。
- 导航不点任何会消耗资源的按钮：种子图里没有「开始行动」之后的界面，按键动作只允许 `BACK`。
- 不给 `general` 或其它 Worker 直接发导航工具；导航只能经 `delegate(navigator)`。
- 不改 M71 的租约口径，不改 09 的「人工操作不占租约」。
- 不改事件协议：本里程碑不新增事件 kind，也不新增 SSE/WS 事件。

## 3. 相关文档

- [02 §2.2、§4.1、§4.3、§5](../02-系统架构.md)（服务表、`nav/` 与 `services/` 布局、脚本清单、装配顺序）。
- [03 §12、§13](../03-数据模型.md)（两张表的列与索引、枚举表）。
- [04 §2.5、§5、§6](../04-接口与事件协议.md)（四个端点、三个 `NAV_*` 错误码、`nav.*` 配置）。
- [05 §3.3、§6、§8](../05-运行时与代理定义.md)（导航员工具、`device` 字段、结构化输出 schema）。
- [11 §2、§5](../11-前端指挥台.md)（`/more/nav` 入口与功能表）。
- [13 §8.20–§8.25](../13-决策记录.md)（M67 结构化输出与角色路由）、[§8.30–§8.44](../13-决策记录.md)（M70/M71 工单与租约）、[§8.65–§8.72](../13-决策记录.md)（M75 布局与配置）、[§8.163–§8.177](../13-决策记录.md)（M85 与两次维护性补丁）。
- v2 实测：`grep -n "^### 8.80" docs/v2/13-决策记录.md`（逐次点击的 token 账）；`grep -n "坐标\|原始像素" docs/v2/13-决策记录.md | head`（M18 起点击坐标是设备像素）。
- v2 知识条目：`maa_api/agent/knowledge/builtin/screen-home.md`、`screen-terminal.md`（「没有出处的按钮一律写需截图确认」）。
- 既有代码入口：`maa_api/agent/tools/raw.py` 的 `_run_atomic`（108 行，`operation_lock` + 流水线空闲检查）、`_click`（140 行）、`CLICK_SETTLE_SECONDS`（19 行）；`maa_api/services/device_service.py` 的 `DeviceManager.screenshot`（696 行）、`swipe`（740 行）、`click`（765 行）、`back_to_home`（790 行，返回内核 `AsstBackToHome` 的布尔值）、`key_event`（825 行）、`snapshot()` 的 `resolution` 键（432 行）；`maa_api/agent/harness/builtin_middleware.py` 的 `archive_screenshot_result`（1408 行）、`_policy_session_id`（1487 行）；`maa_api/agent/lease.py` 的 `LeaseMiddleware`（644 行）；`maa_api/agent/policy.py` 的 `ATOMIC_TOOL_NAMES`（35 行）、`_evaluate_atomic`（425 行）；`maa_api/api/routers/agent.py` 的 `_rest_preempt_hook`（145 行）与 `invoke_tool`（约 310 行）；`maa_api/agent/tools/status.py` 的 `get_screenshot`（195 行）；`maa_api/util/image.py` 的 `store_screenshot`（243 行）；`maa_api/db/repositories/log.py` 的 `ScreenshotRepository.create`（308 行附近）；`maa_api/agent/llm/roles.py` 的 `ModelRouter.resolve`（80 行）；`maa_api/agent/llm/structured.py` 的 `complete_structured`（173 行）；`maa_api/agent/llm/profiles.py` 的 `ResolvedProfile.supports_vision`（140 行）。
- MAA 资源（种子坐标出处，不进仓库）：`resource/lib/maa/Darwin/resource/tasks/tasks.json`、`tasks/UiTheme/*.json`。

## 4. 设计

### 4.1 数据模型

枚举加进 `maa_api/domain/enums.py`，并在 `tests/domain/test_enums.py` 整表钉死：

```python
class NavNodeKind(StrEnum):
    SCREEN = "screen"
    STAGE = "stage"
    MENU = "menu"
    DIALOG = "dialog"


class NavOrigin(StrEnum):
    SEED = "seed"
    LEARNED = "learned"
    USER = "user"
```

`maa_api/db/models.py` 末尾追加（03 §12 的列，一列不多）：

```python
# ---------------------------------------------------------------------------
# M86 nav_node / nav_edge：界面导航图（v3 03 §12）
# ---------------------------------------------------------------------------
class NavNode(SQLModel, table=True):
    """一个可识别的游戏界面。

    ``exemplars`` 每项 ``{phash, screenshot_id, source, added_at}``：``phash`` 是按
    ``recognizer["rois"]`` 顺序拼接的 dHash 十六进制串，改了 rois 就要用截图重算。
    ``source`` 取 ``agent``、``edge``、``user``、``anchor`` 之一。
    """

    __tablename__ = "nav_node"

    id: str = Field(primary_key=True, max_length=48)
    title: str = Field(max_length=64)
    kind: str = Field(default=NavNodeKind.SCREEN, max_length=24)
    exemplars: list[dict[str, Any]] = Field(default_factory=list, sa_column=json_column(
        "exemplars", nullable=False, server_default="'[]'"))
    recognizer: dict[str, Any] = Field(default_factory=dict, sa_column=json_column(
        "recognizer", nullable=False, server_default="'{}'"))
    origin: str = Field(max_length=16)
    created_at: datetime = Field(default_factory=utcnow, sa_column=datetime_column("created_at"))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=datetime_column("updated_at"))


class NavEdge(SQLModel, table=True):
    """两个界面之间的一种跳转；同一对界面可以有多条动作不同的跳转。"""

    __tablename__ = "nav_edge"

    id: str = Field(default_factory=new_uuid, primary_key=True, max_length=36)
    from_node: str = Field(foreign_key="nav_node.id", ondelete="CASCADE", max_length=48)
    to_node: str = Field(foreign_key="nav_node.id", ondelete="CASCADE", max_length=48)
    action: dict[str, Any] = Field(sa_column=json_column("action", nullable=False))
    action_hash: str = Field(max_length=64)
    cost_ms: int | None = Field(default=None)
    success_count: int = Field(default=0)
    fail_count: int = Field(default=0)
    last_used_at: datetime | None = Field(default=None, sa_column=datetime_column(
        "last_used_at", nullable=True))
    origin: str = Field(max_length=16)
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow, sa_column=datetime_column("created_at"))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=datetime_column("updated_at"))

    __table_args__ = (
        Index("uq_nav_edge_from_node_to_node_action_hash",
              "from_node", "to_node", "action_hash", unique=True),
    )
```

迁移文件 `maa_api/db/migrations/versions/0041_nav_graph.py`：`revision = "0041_nav_graph"`，`down_revision = "0040_question_expiry_historical"`。`upgrade()` 建两表与唯一索引，`downgrade()` 反序删索引、`nav_edge`、`nav_node`：

```python
def upgrade() -> None:
    op.create_table(
        "nav_node",
        sa.Column("id", sa.String(length=48), nullable=False),
        sa.Column("title", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("exemplars", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("recognizer", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_nav_node"),
    )
    op.create_table(
        "nav_edge",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("from_node", sa.String(length=48), nullable=False),
        sa.Column("to_node", sa.String(length=48), nullable=False),
        sa.Column("action", sa.JSON(), nullable=False),
        sa.Column("action_hash", sa.String(length=64), nullable=False),
        sa.Column("cost_ms", sa.Integer(), nullable=True),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("fail_count", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["from_node"], ["nav_node.id"],
                                name="fk_nav_edge_from_node_nav_node", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_node"], ["nav_node.id"],
                                name="fk_nav_edge_to_node_nav_node", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_nav_edge"),
    )
    op.create_index("uq_nav_edge_from_node_to_node_action_hash", "nav_edge",
                    ["from_node", "to_node", "action_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_nav_edge_from_node_to_node_action_hash", table_name="nav_edge")
    op.drop_table("nav_edge")
    op.drop_table("nav_node")
```

迁移不写种子数据：种子由 `NavGraphService.ensure_seed()` 在启动时写入（§4.5.6），只在 `nav_node` 为空时写。降级丢掉学到的图，属于运行时数据，可接受。

### 4.2 模块与文件

| 文件 | 新建/修改 | 职责 |
|---|---|---|
| `maa_api/agent/nav/__init__.py` | 新建 | 导出 `NavGraphService`、`Recognizer`、`Navigator` |
| `maa_api/agent/nav/graph.py` | 新建 | 动作模型、`action_hash`、图的读写、最短路径、成败记账、`LastSeenStore` |
| `maa_api/agent/nav/seed.py` | 新建 | `SEED_NODES`、`SEED_EDGES` 两个常量 |
| `maa_api/agent/nav/recognizer.py` | 新建 | dHash、样本近邻、`ModelVisionClient`、视觉分类与定位 |
| `maa_api/agent/nav/navigator.py` | 新建 | `NavDevice` 协议、`AppNavDevice`、导航执行、`remember_screen` 的实现 |
| `maa_api/services/screenshot_archive.py` | 新建 | `archive_screenshot`：截图落文件与建行（M89 复用） |
| `maa_api/agent/llm/schemas/nav_classification.py`、`nav_locate.py` | 新建 | 两个 pydantic 结构化输出 schema，模块末尾 `register_schema` |
| `maa_api/agent/llm/schemas/__init__.py` | 改 | 导入两个新 schema |
| `maa_api/agent/tools/nav.py` | 新建 | `where_am_i`、`remember_screen`、`navigate_to` |
| `maa_api/agent/tools/__init__.py` | 改 | 在 `register_raw_tools` 之后注册 nav 工具 |
| `maa_api/agent/tools/raw.py` | 改 | `_run_atomic` 改名 `run_atomic`（同文件调用一并改） |
| `maa_api/agent/policy.py` | 改 | `ATOMIC_TOOL_NAMES` 加 `navigate_to` |
| `maa_api/api/routers/nav.py` | 新建 | 四个端点 |
| `maa_api/api/schemas/agent_v3.py` | 改 | 导航图的请求与响应模型 |
| `maa_api/main.py` | 改 | 挂 `app.state.nav_service`、`ensure_seed()`、`include_router(nav.router)` |
| `maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml` | 改 | `nav.*` 三个键 |
| `maa_api/agent/specs/builtin/navigator.md` | 改 | 工具与提示词（§4.7）；`general.md` 不动 |
| `maa_api/domain/errors.py` | 改 | 三个 `NAV_*` 码 |
| `web/src/features/nav/*`、`web/src/routes/router.tsx`、`web/src/components/layout/nav.ts`、`web/src/api/keys.ts` | 新建/改 | 导航图页与入口 |

`graph.py` 的签名与常量：

```python
NODE_ID_PATTERN = re.compile(r"^(?:[a-z][a-z0-9_]{1,47}|stage:[A-Za-z0-9][A-Za-z0-9_-]{0,41})$")
HOME_NODE_ID = "home"
ALLOWED_KEYS = frozenset({"BACK"})
MAX_SEQUENCE_STEPS = 8
DISABLE_AFTER_FAILS = 3          # fail_count >= 3 且 fail_count >= success_count 时停用
FAIL_PENALTY_MS = 2000
DEFAULT_COST_MS = {"tap": 1500, "swipe": 1500, "key": 1500, "locate": 6500}
HOME_EDGE_COST_MS = 6000         # 隐式「回主界面」边，不落库
EXEMPLAR_SOURCES = frozenset({"agent", "edge", "user", "anchor"})
MAX_EXEMPLARS = 8
DUPLICATE_DISTANCE = 3

Ratio = Annotated[float, Field(ge=0, le=1)]
WaitMs = Annotated[int, Field(ge=0, le=10000)]

class TapAction(BaseModel):      # 五个动作模型都 model_config = ConfigDict(extra="forbid")
    type: Literal["tap"]
    x: Ratio
    y: Ratio
    wait_ms: WaitMs = 1200

class SwipeAction(BaseModel):
    type: Literal["swipe"]
    x1: Ratio
    y1: Ratio
    x2: Ratio
    y2: Ratio
    duration_ms: int = Field(default=400, ge=100, le=3000)
    wait_ms: WaitMs = 1000

class KeyAction(BaseModel):
    type: Literal["key"]
    key: Literal["BACK"]
    wait_ms: WaitMs = 1200

class LocateAction(BaseModel):   # query 是给视觉模型的中文描述
    type: Literal["locate"]
    query: str = Field(min_length=2, max_length=120)
    wait_ms: WaitMs = 1200

class SequenceAction(BaseModel):
    type: Literal["sequence"]
    steps: list[TapAction | SwipeAction | KeyAction] = Field(min_length=1, max_length=8)

NavAction = Annotated[TapAction | SwipeAction | KeyAction | LocateAction | SequenceAction,
                      Field(discriminator="type")]

def action_hash(action: NavAction) -> str:
    """规范 JSON 的 sha256：键排序、坐标保留 3 位小数、递归去掉 wait_ms。"""

def validate_action(payload: Mapping[str, Any]) -> NavAction:
    """按 NavAction 校验并返回模型；不合法抛 INVALID_PARAMETER。"""

@dataclass(frozen=True, slots=True)
class PathStep:
    edge_id: str | None; from_node: str; to_node: str
    action: dict[str, Any]; origin: str

@dataclass(frozen=True, slots=True)
class PathPlan:
    steps: tuple[PathStep, ...]
    blocked_by_vision: bool

DeviceFactory = Callable[[Any], Any]   # 入参 ToolContext；返回 NavDevice，graph.py 不 import navigator

class LastSeenStore:
    """进程内：session_id -> (node_id, audit_cursor)。重启后丢失，只影响一次学习。"""
    def put(self, session_id: str, node_id: str, audit_cursor: int) -> None: ...
    def get(self, session_id: str) -> tuple[str, int] | None: ...

class NavGraphService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], *,
                 device_factory: DeviceFactory | None = None) -> None:
        """``self.last_seen = LastSeenStore()``；``device_factory`` 为空时懒加载 AppNavDevice。"""
    def make_device(self, context: Any) -> NavDevice: ...
    async def ensure_seed(self) -> int: """nav_node 为空时写入种子，返回写入的节点数。"""
    async def graph(self) -> tuple[list[NavNode], list[NavEdge]]: ...
    async def get_node(self, node_id: str) -> NavNode | None: ...
    async def get_edge(self, edge_id: str) -> NavEdge | None: ...
    async def upsert_node(self, node_id: str, *, title: str, kind: str, origin: str,
                          recognizer: Mapping[str, Any] | None = None) -> NavNode: ...
    async def add_exemplar(self, node_id: str, *, phash: str, screenshot_id: str | None,
                           source: str) -> bool: """去重与上限见 §4.5.2；加了返回 True。"""
    async def set_exemplars(self, node_id: str, exemplars: list[dict[str, Any]]) -> NavNode: ...
    async def upsert_edge(self, *, from_node: str, to_node: str, action: Mapping[str, Any],
                          origin: str, edge_id: str | None = None) -> tuple[NavEdge, bool]:
        """同 (from_node, to_node, action_hash) 已存在时返回 (旧边, False)。"""
    async def update_edge(self, edge_id: str, *, enabled: bool | None = None,
                          action: Mapping[str, Any] | None = None,
                          cost_ms: int | None = None) -> NavEdge: ...
    async def delete_edge(self, edge_id: str) -> None: ...
    async def record_success(self, edge_id: str, *, elapsed_ms: int) -> None: ...
    async def record_failure(self, edge_id: str) -> bool: """返回这次是否触发了停用。"""
    async def shortest_path(self, source: str, target: str, *, vision: bool,
                            exclude: set[str] = frozenset()) -> PathPlan | None: ...
    async def note_seen(self, session_id: str | None, node_id: str | None) -> None:
        """认出界面后把 (node_id, 当前会话审计最大 id) 写进 ``self.last_seen``。"""
```

`recognizer.py` 的签名与常量：

```python
HASH_SIZE = 8
DEFAULT_ROIS = ((0.0, 0.0, 1.0, 1.0),)
DEFAULT_MAX_DISTANCE = 10          # 每个 ROI 的汉明距离上限
AMBIGUITY_MARGIN = 2.0             # 第一名与另一个节点的平均距离差小于它就算拿不准
VISION_MIN_CONFIDENCE = 0.7
LOCATE_MIN_CONFIDENCE = 0.6
VISION_CANDIDATES = 8
UNKNOWN_RETRY_MS = 1500

def dhash(image: Image.Image, roi: Sequence[float]) -> int: ...
def signature(image: Image.Image, rois: Sequence[Sequence[float]]) -> str: ...
def distances(sig_a: str, sig_b: str) -> list[int] | None: """ROI 数不同返回 None。"""

@dataclass(frozen=True, slots=True)
class Recognition:
    node_id: str | None
    method: Literal["hash", "vision", "none"]
    distance: float | None
    confidence: float
    candidates: tuple[tuple[str, float], ...]

class VisionClient(Protocol):
    async def available(self) -> bool: ...
    async def classify(self, image: Image.Image, candidates: Sequence[NavNode]
                       ) -> tuple[str | None, float, str] | None: ...
    async def locate(self, image: Image.Image, query: str
                     ) -> tuple[float, float, float] | None: ...

class ModelVisionClient:
    """用 M67 的 vision 角色；档案 supports_vision 不为 True 时 available() 为 False。"""
    def __init__(self, app_state: Any, *, run_id: str | None = None,
                 session_id: str | None = None) -> None: ...
    async def available(self) -> bool: ...
    async def classify(self, image: Image.Image, candidates: Sequence[NavNode]
                       ) -> tuple[str | None, float, str] | None: ...
    async def locate(self, image: Image.Image, query: str
                     ) -> tuple[float, float, float] | None: ...

class Recognizer:
    def __init__(self, graph: NavGraphService, vision: VisionClient) -> None: ...
    async def recognize(self, image: Image.Image, *, allow_vision: bool = True) -> Recognition: ...
```

`ModelVisionClient` 的依赖取法写死：`router = getattr(app_state, "model_router", None)`、`ledger = getattr(app_state, "cost_ledger", None)`；任一为空时 `available()` 返回 False，`classify`/`locate` 返回 None。`classify`/`locate` 调 `complete_structured(router=router, ledger=ledger, role=ModelRole.VISION.value, schema_name="nav_classification" 或 "nav_locate", messages=[...], agent_name="navigator", run_id=self._run_id, session_id=self._session_id)`。`run_id`/`session_id` 由 `tools/nav.py` 从 `ToolContext.origin.run_id` 与 `ToolContext.session_id` 传入。

`navigator.py` 的签名与常量：

```python
MAX_LEARNED_SEQUENCE = 8
LEARNABLE_TOOLS = frozenset({"click", "swipe", "key_event"})

class NavDevice(Protocol):          # 坐标都是设备像素
    async def screenshot(self) -> Image.Image: ...
    async def resolution(self) -> tuple[int, int]: ...
    async def tap(self, x: int, y: int) -> None: ...
    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None: ...
    async def key(self, key: str) -> None: ...
    async def home(self) -> bool: ...

class AppNavDevice:
    """每个写设备动作都包在 raw.run_atomic(context, "navigate_to", ...) 里。"""
    def __init__(self, context: ToolContext) -> None: ...

@dataclass(slots=True)
class Performed:
    ok: bool
    tap_action: dict[str, Any] | None   # locate 成功后的归一化点击动作；其余动作为 None
    screenshot: Image.Image | None      # locate 定位用的图、home 成功后的图；其余为 None
    error: str | None

@dataclass(slots=True)
class NavResult:
    arrived: bool; node_id: str; steps: int; elapsed_ms: int
    trail: list[dict[str, Any]]; learned_edges: int; learned_exemplars: int

class Navigator:
    def __init__(self, graph: NavGraphService, recognizer: Recognizer, device: NavDevice,
                 *, learn: bool, max_steps: int, db_session: AsyncSession | None = None,
                 session_id: str | None = None) -> None: ...
    async def where_am_i(self) -> tuple[Recognition, Image.Image]: ...
    async def navigate(self, target: str) -> NavResult: ...
    async def perform(self, step: PathStep) -> Performed: ...
    async def remember(self, *, node_id: str, title: str,
                       kind: str | None) -> dict[str, Any]: ...
```

`services/screenshot_archive.py`：

```python
async def archive_screenshot(session: AsyncSession, image: Image.Image, *,
                             trigger: ScreenshotTrigger,
                             backend: ScreenshotBackend
                             ) -> tuple[Screenshot, dict[str, Any]]:
    """store_screenshot 落文件 + ScreenshotRepository.create 建行；不 commit，调用方提交。

    返回 (Screenshot 行, {"screenshot_id", "sha256", "full_url", "thumb_url"})。
    路径 ``image/screenshot/<sha 前两位>/<sha>.jpg``，格式 jpg；行里写 width/height/size_bytes。
    """
```

`tools/nav.py` 的签名：

```python
async def where_am_i(params: WhereAmIParams, context: ToolContext) -> dict[str, Any]: ...
async def remember_screen(params: RememberScreenParams, context: ToolContext) -> dict[str, Any]: ...
async def navigate_to(params: NavigateToParams, context: ToolContext) -> dict[str, Any]: ...

def register_tools(registry: ToolRegistry) -> None: ...
```

### 4.3 接口

#### 4.3.1 工具

| 工具 | 组 | 风险 | device | read_only | refetchable | rest_visible |
|---|---|---|---|---|---|---|
| `where_am_i` | `nav` | SAFE | `read` | True | True | True |
| `remember_screen` | `nav` | SAFE | `none` | False | False | False |
| `navigate_to` | `nav` | DANGEROUS（策略按原子操作处理，§4.5.5） | `exclusive` | False | False | True |

参数（`extra="forbid"`）：

```python
class WhereAmIParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

class RememberScreenParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=2, max_length=48)   # 满足 NODE_ID_PATTERN
    title: str = Field(min_length=1, max_length=64)
    kind: Literal["screen", "stage", "menu", "dialog"] | None = None

class NavigateToParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=2, max_length=48)
    max_steps: int | None = Field(default=None, ge=1, le=50)    # 缺省取 nav.max_steps
```

`where_am_i` 返回（纯读：不写库、不写文件、不动设备）：

```json
{"node": {"id": "home", "title": "主界面", "kind": "screen"}, "method": "hash",
 "distance": 3.5, "confidence": 0.825,
 "candidates": [{"id": "home", "title": "主界面", "distance": 3.5},
  {"id": "terminal", "title": "终端", "distance": 21.0}]}
```

`confidence` 的算法：hash 判定为 `1 - distance / (2 × 该节点 max_distance)`，夹到 0–1；视觉判定取模型给的值，`distance` 为 null；none 为 0。`candidates` 按平均距离升序，至多 3 个，与判定是否过阈值无关。`node` 为 null 时 `method` 为 `none`、`distance` 为 null、`confidence` 为 0。

`remember_screen` 返回：

```json
{"node": {"id": "stage:1-7", "title": "1-7", "kind": "stage", "created": true},
 "exemplar_added": true, "screenshot_id": "b1…", "edge": null,
 "skipped_reason": "no_last_seen"}
```

`edge` 非空时为 `{"id": "3f…", "from": "main_episode", "action": "sequence", "created": true}`。`skipped_reason` 取 `null`、`screen_is_<node_id>`、`duplicate_exemplar`、`no_session`、`no_last_seen`、`no_audit`、`same_node`、`too_many_steps`、`unsupported_action`；样本照加，只有学边被跳过。

`navigate_to` 返回：

```json
{"arrived": true, "node": {"id": "infrast", "title": "基建", "kind": "screen"},
 "steps": 2, "elapsed_ms": 3400,
 "trail": [{"edge_id": null, "action": "home", "from": null, "expected": "home",
            "actual": "home", "ok": true, "ms": 900},
           {"edge_id": "3f…", "action": "tap", "from": "home", "expected": "infrast",
            "actual": "infrast", "ok": true, "ms": 1800}],
 "learned": {"edges": 0, "exemplars": 2}}
```

失败用 AppError：

| 情形 | 码 | details |
|---|---|---|
| 目标不在图里 | `NAV_NODE_NOT_FOUND` | `{target, nearest}`；`nearest` 见 §4.5.4 |
| 当前到目标没有可走的路径 | `NAV_PATH_NOT_FOUND` | `{from, to, reason}`，`reason` 取 `no_edges`、`needs_vision` |
| 走到一半画面不符且无法恢复 | `NAV_STEP_FAILED` | `{reason, current, trail, screenshot_id}`，`reason` 取 `max_steps`、`unknown_screen`、`home_failed`；`screenshot_id` 只在 `unknown_screen` 时非空 |
| 导航图关闭 | `SERVICE_UNAVAILABLE`（v2） | `{setting: "nav.enabled"}` |
| `remember_screen` 的 node_id 不合法、title 缺失、kind 与 `stage:` 前缀矛盾 | `INVALID_PARAMETER`（v2） | `{field}` |

#### 4.3.2 REST（tag `agent`，路由文件 `maa_api/api/routers/nav.py`，前缀 `/api/agent/nav`）

| 方法与路径 | 函数名 | 请求 | 响应与状态码 |
|---|---|---|---|
| GET `/api/agent/nav/graph` | `get_nav_graph` | — | 200 `NavGraphOut` |
| PUT `/api/agent/nav/nodes/{node_id}` | `put_nav_node` | `NavNodePut` | 200 `NavNodeOut`（新建与修改都是 200）；400 `INVALID_PARAMETER`；404 `SCREENSHOT_NOT_FOUND`；410 `SCREENSHOT_EXPIRED`；`add_exemplar='current'` 而设备未连接时返回 503 `DEVICE_NOT_CONNECTED` |
| PUT `/api/agent/nav/edges/{edge_id}` | `update_nav_edge` | `NavEdgePut` | 200 `NavEdgeOut`；不存在且没给全 `from_node`/`to_node`/`action` 时 404 `NOT_FOUND`；端点不存在 404 `NAV_NODE_NOT_FOUND`；400 `INVALID_PARAMETER` |
| DELETE `/api/agent/nav/edges/{edge_id}` | `delete_nav_edge` | — | 204；404 `NOT_FOUND` |

响应与请求模型放进 `maa_api/api/schemas/agent_v3.py`：

```python
class NavExemplarOut(BaseModel):
    phash: str; screenshot_id: str | None; source: str; added_at: str | None
    thumb_url: str | None                    # /api/images/{sha256}/thumb，查不到或已清理时为 None

class NavRecognizerModel(BaseModel):        # 请求里 extra="forbid"
    rois: list[list[float]] = Field(default_factory=lambda: [[0, 0, 1, 1]], min_length=1, max_length=4)
    max_distance: int = Field(default=10, ge=1, le=32)
    description: str = Field(default="", max_length=200)
    generic: bool = False

class NavNodeOut(BaseModel):
    id: str; title: str; kind: str; origin: str
    recognizer: NavRecognizerModel; exemplars: list[NavExemplarOut]
    created_at: str; updated_at: str

class NavEdgeOut(BaseModel):
    id: str; from_node: str; to_node: str; action: dict[str, Any]; action_hash: str
    cost_ms: int | None; success_count: int; fail_count: int; last_used_at: str | None
    origin: str; enabled: bool; created_at: str; updated_at: str

class NavGraphOut(BaseModel):
    nodes: list[NavNodeOut]; edges: list[NavEdgeOut]
    enabled: bool; learn: bool; vision_available: bool

class NavNodePut(BaseModel):                 # extra="forbid"
    title: str = Field(min_length=1, max_length=64)
    kind: Literal["screen", "stage", "menu", "dialog"]
    recognizer: NavRecognizerModel | None = None
    add_exemplar: Literal["current"] | None = None
    add_exemplar_screenshot_id: str | None = Field(default=None, max_length=36)
    remove_exemplars: list[str] = Field(default_factory=list, max_length=8)

class NavEdgePut(BaseModel):                 # extra="forbid"
    from_node: str | None = None
    to_node: str | None = None
    action: dict[str, Any] | None = None     # 按 NavAction 校验，失败 400
    enabled: bool | None = None
    cost_ms: int | None = Field(default=None, ge=0, le=60000)
```

规则：

- `put_nav_node`：节点 id 不满足 `NODE_ID_PATTERN` 时 400 `INVALID_PARAMETER`（`details.field="node_id"`）。新建时 `origin='user'`；改已有节点不改 `origin`。`recognizer.rois` 变了，按每个样本的 `screenshot_id` 重算 `phash`，截图文件丢失的样本删掉。`add_exemplar='current'` 现截一张图（只读设备，不取租约），`source='user'`；`add_exemplar_screenshot_id` 用已归档截图；两者都给时 400。`remove_exemplars` 按 `phash` 删除。样本上限 8：超出时先删最早的非 `user` 样本，全是 `user` 时删最早的那个。
- `update_nav_edge`：不存在时按 `from_node`、`to_node`、`action` 新建（`origin='user'`，id 用路径里的值，必须是 UUID 形状，否则 400）。存在时只允许改 `enabled`、`action`、`cost_ms`：请求带 `from_node` 或 `to_node` 且与现值不同时 400 `INVALID_PARAMETER`（`details.field` 为 `from_node` 或 `to_node`）；与现值相同则忽略。`enabled` 从 false 改成 true 时 `fail_count` 清零。改了 `action` 重算 `action_hash`，与同一对界面的另一条撞了时 400（`details.field="action"`）。改端点要删旧建新。
- `delete_nav_edge`：物理删除。种子跳转删掉后不会自动写回，因为种子只在空表时写入。
- 时间字段输出 ISO 8601 带 `Z`。

### 4.4 配置项

| 键 | 类型 | 默认 | 范围 | 分组 | config-only | `_FIELDS` |
|---|---|---|---|---|---|---|
| `nav.enabled` | bool | true | | Agent | 否 | `dict(label="界面导航图", group="Agent", type="boolean", hot_action="none")` |
| `nav.learn` | bool | true | | Agent | 否 | `dict(label="导航时学习新跳转", group="Agent", type="boolean", hot_action="none")` |
| `nav.max_steps` | int | 12 | 1–50 | Agent | 否 | `dict(label="一次导航最多步数", group="Agent", type="integer", minimum=1, maximum=50, hot_action="none")` |

`settings.py`：新增 `class NavSettings(BaseModel)`（`model_config = ConfigDict(extra="ignore")`；`enabled: bool = True`、`learn: bool = True`、`max_steps: int = Field(default=12, ge=1, le=50)`），`Settings` 加 `nav: NavSettings = Field(default_factory=NavSettings)`。`SETTING_KEYS` 加三行，第二项必须写全：

```python
"nav.enabled": ("nav", "enabled"),
"nav.learn": ("nav", "learn"),
"nav.max_steps": ("nav", "max_steps"),
```

环境变量 `MAA_NAV_ENABLED`、`MAA_NAV_LEARN`、`MAA_NAV_MAX_STEPS` 由 `ENV_OVERRIDES` 自动派生，测试写进 `tests/test_settings.py`。

`config.template.yaml` 追加：

```yaml
# 界面导航图（M86）：常用界面与跳转存成图，navigate_to 一次走完。
nav:
  enabled: true
  learn: true      # 导航时把新走通的跳转、新确认的画面样本记进图里
  max_steps: 12    # 一次 navigate_to 最多执行多少个动作
```

### 4.5 行为与算法

#### 4.5.1 识别方案与 MaaCore 模板匹配的可行性

结论：用 MaaCore 自定义 task 做模板匹配识别在技术上可行，本里程碑不用它做逐步识别。

可行的证据：M29 已把只读识别任务写进 custom 层的独立文件（`resource/maa-layers/custom/resource/tasks/MaaApi@CopilotResult.json`），`AsstLoadResource` 之后用 `Custom` 任务运行，模板与 roi 直接引用内核自带的结算模板；M57 修好了命中标记的解析（`details.pre_task`）。同样的写法能判断「是不是主界面」（内核 `Home` 任务的模板 `SwitchTheme@ToggleSettingsMenu.png`、roi `[227,327,159,152]`）。

不用它的理由：① 每次识别都是一条流水线，入队、领取、内核跑链，两条链之间至少隔 1.5 s（v2/13 §8.53 W1 实测），单次 2–4 s，感知哈希在本进程算只要几十毫秒；② 读数不进 `log_entry`，只能靠进程内回调或 `@Hit` 命中标记绕出来（v2/13 §8.51 二、4）；③ 每次识别都在流水线历史里留一条记录，历史页会被导航刷屏；④ 识别期间设备被流水线占着，原子操作要等它结束（`run_atomic` 的空闲检查）；⑤ 只对内核已有模板的界面有效，新活动界面没有模板。

本里程碑只在一处借用内核的识别：隐式「回主界面」边调 `DeviceManager.back_to_home()`，内核返回 True 就认定到了主界面，并把这张图记成 `home` 的样本（`source='anchor'`）。`anchor` 只由 `recover_home`/`perform(home)` 成功产生，不是一种识别方法。

#### 4.5.2 感知哈希识别

- 实现：dHash，不新增依赖。Pillow 已在用（`maa_api/util/image.py`），imagehash 会多带 PyWavelets，没有必要。
- `dhash(image, roi)`：`roi` 是归一化 `[x, y, w, h]`；裁剪框 `(round(x·W), round(y·H), round((x+w)·W), round((y+h)·H))`，左闭右开、越界裁剪、退化矩形至少 1px；`convert("L").resize((9, 8), Image.Resampling.BILINEAR)`；逐行比较相邻两列，左大于右记 1，得 64 位整数。
- `signature`：各 ROI 的 `f"{bits:016x}"` 用 `:` 连接。`distances`：逐段异或后 `bit_count()`。
- 节点匹配：节点的每个样本与本图按该节点 `rois` 的签名比距离，每个 ROI 都不超过 `max_distance` 才算这个样本匹配；节点得分取匹配样本里平均距离最小的那个。
- 判定顺序：
  1. 没有任何节点匹配 → 进视觉分类（第 4 步）。
  2. 取得分最小的节点 A。若另有节点 B 也匹配且 `B.score - A.score < AMBIGUITY_MARGIN` → 拿不准，进视觉分类；例外：A 与 B 一个 `recognizer.generic=true`、另一个不是，取非 generic 的那个，不算拿不准。
  3. 否则返回 A，`method='hash'`。
  4. 视觉分类：`allow_vision=True` 且 `await vision.available()` 时，候选取得分最小的 `VISION_CANDIDATES` 个节点（不足就全取），再保证含 `home`；模型返回的 `node_id` 在候选里且置信度 ≥ 0.7 才采纳，`method='vision'`；否则 `method='none'`、`node_id=None`。
- 识别器只读：`recognize` 不写库、不写文件、不加样本。学习由 `Navigator` 在 `navigate` 与 `remember` 里显式调用 `add_exemplar`。
- 自动学样本（`nav.learn=true` 时，只发生在 `navigate` 内）：
  1. 视觉判定采纳、且该节点还没有这个签名 → 加样本（`source='agent'`）。
  2. `navigate_to` 某一步校验通过 → 把目标界面的签名加进目标节点（`source='edge'`）。
  3. 首次到达的种子节点没有样本时按 §4.5.4 的「首达信任」加第一个样本（`source='edge'`）。
  4. `recover_home` 的 `back_to_home` 返回 True → 把图加进 `home`（`source='anchor'`）。
- 去重：与已有样本每个 ROI 距离都 ≤ `DUPLICATE_DISTANCE` 就不加。上限 8：超出时先删最早的非 `user` 样本，全是 `user` 时删最早的那个。加样本时才归档截图（`trigger=agent`，经 `archive_screenshot`）；`Navigator` 归档后立即 `await db_session.commit()`（工具处理器的会话由确认服务创建、不替它提交），再加样本；归档失败只记 warning 并继续，`screenshot_id` 记 null。

视觉分类与定位用 M67 的 `complete_structured`，角色 `vision`，消息是一张截图（缩到 1280×720 的 JPEG data URL，质量 80）加文字。两个 schema 是 pydantic 模型文件：

```python
# maa_api/agent/llm/schemas/nav_classification.py
class NavClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str | None = Field(default=None, max_length=48)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(default="", max_length=200)

register_schema("nav_classification", NavClassification)

# maa_api/agent/llm/schemas/nav_locate.py
class NavLocate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    found: bool
    x: float = Field(default=0.0, ge=0, le=1)
    y: float = Field(default=0.0, ge=0, le=1)
    confidence: float = Field(ge=0, le=1)

register_schema("nav_locate", NavLocate)
```

分类提示词（`recognizer.py` 的常量 `CLASSIFY_PROMPT`）：

```
下面是明日方舟游戏的一张截图。从候选界面里选出它属于哪一个；都不像就给 null。
只看界面结构与固定的按钮和标题，不看角色立绘与背景。
候选：
{每行一个：id｜中文名｜特征描述}
```

定位提示词 `LOCATE_PROMPT`：

```
下面是明日方舟游戏的一张截图。找到「{query}」的位置，给出它中心点的归一化坐标（左上角是 0,0，右下角是 1,1）。找不到就 found=false。
```

结构化调用失败（`STRUCTURED_OUTPUT_INVALID` 或模型请求错误）一律当作「没有结论」，不抛给调用方。

#### 4.5.3 最短路径

- Dijkstra。边的代价 = `cost_ms`（有实测值时）或 `DEFAULT_COST_MS[action.type]`（序列取各步之和）+ `FAIL_PENALTY_MS × fail_count`。
- 跳过：`enabled=false` 的边；`exclude` 里的边（本次导航已失败过的）；`vision=false` 时的 `locate` 边，以及步骤里含 `locate` 的序列（序列不允许含 `locate`，这一条只是兜底）。
- 隐式边：除 `home` 外的每个节点都有一条到 `home` 的边，`edge_id=None`、`action={"type":"home"}`、`origin="implicit"`、代价 6000。
- 目标就是当前节点时返回 `PathPlan((), False)`。
- 找不到时的判定：先用 `vision=True` 算一次；没有路径返回 `None`（`reason=no_edges`）。`vision=False` 时再算一次；仍没有路径但 `vision=True` 有，返回 `PathPlan((), True)`（调用方报 `reason=needs_vision`）。

#### 4.5.4 导航执行

```
navigate(target):
    nodes = graph.graph()
    if target 不在 nodes: raise NAV_NODE_NOT_FOUND(target, nearest=nearest_container(target))
    steps = 0; failed_edges = set(); trail = []; learned_edges = 0; learned_exemplars = 0
    rec, image = where_am_i()
    if learn and rec.method == "vision" and rec.node_id:
        learned_exemplars += learn_exemplar(rec.node_id, image, source="agent")
    current = rec.node_id
    if current is None:
        current = recover_home()                 # steps += 1；失败 → NAV_STEP_FAILED(home_failed)
    while current != target:
        if steps >= max_steps: raise NAV_STEP_FAILED(max_steps, current, trail)
        vision_ok = await vision.available()
        plan = graph.shortest_path(current, target, vision=vision_ok, exclude=failed_edges)
        if plan is None: raise NAV_PATH_NOT_FOUND(current, target, reason="no_edges")
        if plan.blocked_by_vision: raise NAV_PATH_NOT_FOUND(current, target, reason="needs_vision")
        step = plan.steps[0]
        t0 = now(); performed = perform(step); steps += 1
        if not performed.ok:                     # locate 找不到、动作抛错
            if step.edge_id: graph.record_failure(step.edge_id); failed_edges.add(step.edge_id)
            trail.append(trail_item(step, actual=None, ok=False, ms=now()-t0))
            continue                             # 重新规划；无路可走时下一轮报 NAV_PATH_NOT_FOUND
        rec, image = where_am_i()
        if rec.node_id is None:
            sleep(UNKNOWN_RETRY_MS); rec, image = where_am_i()
        if learn and rec.method == "vision" and rec.node_id:
            learned_exemplars += learn_exemplar(rec.node_id, image, source="agent")
        elapsed = now() - t0
        if rec.node_id == step.to_node:
            if step.edge_id: graph.record_success(step.edge_id, elapsed_ms=elapsed)
            if learn: learned_exemplars += learn_exemplar(step.to_node, image, source="edge")
            if step.action.type == "locate" and learn:
                edge, created = graph.upsert_edge(
                    from_node=step.from_node, to_node=step.to_node,
                    action={"type":"tap", **performed.tap_action}, origin="learned")
                learned_edges += int(created)
            trail.append(trail_item(step, actual=step.to_node, ok=True, ms=elapsed))
            current = step.to_node; continue
        if 首达信任(step, rec):                   # 条件见下
            if step.edge_id: graph.record_success(step.edge_id, elapsed_ms=elapsed)
            learned_exemplars += learn_exemplar(step.to_node, image, source="edge")
            trail.append(trail_item(step, actual=step.to_node, ok=True, ms=elapsed))
            current = step.to_node; continue
        if step.edge_id:
            graph.record_failure(step.edge_id); failed_edges.add(step.edge_id)
        trail.append(trail_item(step, actual=rec.node_id, ok=False, ms=elapsed))
        if rec.node_id is not None:
            if learn and rec.node_id != current and step.action.type in {"tap","swipe","key","sequence"}:
                edge, created = graph.upsert_edge(
                    from_node=current, to_node=rec.node_id,
                    action=step.action, origin="learned")
                learned_edges += int(created)
            current = rec.node_id; continue
        if steps >= max_steps:
            raise NAV_STEP_FAILED(unknown_screen, current, trail, screenshot_id=archive(image))
        current = recover_home()                 # steps += 1；失败 → home_failed
    return NavResult(arrived=True, node_id=target, steps=steps,
                     elapsed_ms=..., trail=trail,
                     learned_edges=learned_edges, learned_exemplars=learned_exemplars)
```

- `trail` 每项固定六个键：`{"edge_id", "action", "from", "expected", "actual", "ok", "ms"}`；`action` 是动作类型字符串（`tap`、`swipe`、`key`、`locate`、`sequence`、`home`），`from` 在初始位置未知时为 null，`actual` 在没认出时为 null。
- `perform(step)`：`tap` 按设备分辨率换算 `px = round(x·W)`、`py = round(y·H)` 后调 `NavDevice.tap`，再等 `wait_ms`；`swipe` 同理；`key` 只发 `BACK`；`sequence` 依次执行各步，中间不识别；`locate` 先截图、调 `vision.locate`，置信度 < 0.6 或 `found=false` 返回 `Performed(ok=False, error="locate")`，否则点那个坐标并返回 `tap_action={"x": x, "y": y}`；`home` 调 `NavDevice.home()`，成功时返回 `screenshot` 供 `home` 样本使用。`perform` 自己不改图、不记失败，失败计数与 exclude 由 `navigate` 处理。
- 首达信任：同时满足五个条件才把「没认出」当成到达——① `nav.learn=true`；② 这一步是种子边（`step.origin == "seed"` 且 `step.edge_id` 非空）；③ 目标节点当前样本数为 0；④ 实际画面没有匹配到任何已知节点（`rec.node_id is None`）；⑤ 本步动作不是 `home`。满足时把这张图作为目标节点的第一个样本，并按成功记账。之后这个节点一律走正常校验。首个到达 `home` 走 `recover_home` 的 anchor，不适用本条。
- `recover_home`：该步计入步数；`steps` 到上限时抛 `max_steps`；`NavDevice.home()` 返回 False 时抛 `NAV_STEP_FAILED(reason='home_failed', current, trail)`；成功时 `steps += 1`、追加一条 `edge_id=None, action="home"` 的 trail、返回 `"home"`；`learn=true` 时截图并给 `home` 加 anchor 样本（`learned_exemplars` 加 1）。
- `nearest_container(target)`：`stage:` 后面的代号匹配 `^\d{1,2}-\d{1,2}$`（主线，如 `1-7`）→ `main_episode`；其他 `stage:*` → `activity_hub`；不是 `stage:` 开头 → `home`。容器节点不在图里时取 `home`。
- 识别失败的截图才归档（`NAV_STEP_FAILED` 的 `screenshot_id`）；成功只在加样本时归档。
- 每个写设备动作都在 `raw.run_atomic(context, "navigate_to", …)` 里执行：拿 `operation_lock`、检查没有流水线在跑；有流水线在跑时原样抛 `PIPELINE_ALREADY_RUNNING`，不算跳转失败。
- `max_steps` 取 `min(params.max_steps 或 nav.max_steps, nav.max_steps)`：参数只能把上限调小。

记账规则（`record_failure`）：`fail_count += 1`；`fail_count ≥ 3 且 fail_count ≥ success_count` 时 `enabled=false`。`record_success`：`success_count += 1`、`last_used_at=now`、`cost_ms` 为空时取本次耗时，否则 `round(0.7·旧值 + 0.3·本次)`。学到的跳转已存在（同 `action_hash`）时只记一次成功，不新建。

#### 4.5.5 授权、租约与 `remember_screen`

- 策略：`policy.py` 的 `ATOMIC_TOOL_NAMES` 加 `"navigate_to"`。内置调用方在会话（子代理按 §0 第 9 条的口径沿用父 Worker 会话）有有效原子操作授权时直接放行，没有就弹 `grant_atomic_ops`；REST 调用逐次确认（`_atomic_confirmation`）。`where_am_i` 与 `remember_screen` 是 SAFE，不经确认。
- 租约：`navigate_to` 的 `device='exclusive'`，内置 Worker/子代理调用由 `LeaseMiddleware` 在确认通过后、执行前取租约，执行结束释放。REST 调用沿用 M71 对写设备工具的抢占规则（`_rest_preempt_hook` 调 `preempt_device`），**不获取、不持有租约**；REST 导航期间不保证与代理互斥，另一个 Worker 可以取租约并操作设备。`where_am_i` 的 `device='read'` 不取租约；`remember_screen` 的 `device='none'` 不取租约。
- 一次 `navigate_to` 只需要一次授权判定；内部的多个点击不再逐个经过策略层，审计只有 `navigate_to` 这一行，`trail` 写进它的结果。
- `LastSeenStore` 挂在 `NavGraphService.last_seen`。`where_am_i`、`navigate_to` 每次认出节点后调 `nav_service.note_seen(session_id, node_id)`，记 `(node_id, 当时该会话 agent_audit 的最大 id)`。
- `remember_screen`（`nav.enabled=true` 时必须可用；`nav.learn` 只控制自动学习，不拦这个显式工具）：
  1. 校验 `node_id` 满足 `NODE_ID_PATTERN`、`title` 非空；`kind` 缺省时 `stage:` 开头为 `stage`，其余为 `screen`；显式 `kind` 与 `stage:` 前缀矛盾（`stage:` 却给了非 `stage`）时 400 `INVALID_PARAMETER`。
  2. 现截一张图（只读设备），`recognize(image, allow_vision=False)`。若结果是另一个节点 Y（`method='hash'` 且 Y ≠ `node_id`）→ 不写，返回 `skipped_reason="screen_is_<Y>"`。
  3. `node_id` 不存在：新建节点（`origin='learned'`，`kind`、`title` 按上面规则），把本图签名作为第一个样本（`source='agent'`，截图归档）。已存在：只加样本。
  4. 连边：从 `LastSeenStore` 取本会话上一次认出的节点 P 与审计游标 c；查 `agent_audit` 里 `session_id=本会话 AND id > c AND tool_name IN ('click','swipe','key_event') AND status='success'`，按 id 排序。0 条、超过 8 条、含 `long_press` 或 `input_text`、或 P 就是 `node_id` 时不连边，分别返回 `no_audit`、`too_many_steps`、`unsupported_action`、`same_node`；没有 session 或游标时返回 `no_session`、`no_last_seen`。否则把它们换成归一化动作（像素除以当前分辨率，`key_event` 只收 `BACK`），1 条时存成单个动作，多条存成 `sequence`，`upsert_edge(P → node_id, origin='learned')`。
  5. 记 `note_seen(session_id, node_id)`。

#### 4.5.6 种子图

坐标出处都在 MAA 资源（1280×720 基准，取 roi 中心再除以 1280、720，保留 4 位小数）：`UiTheme/*.json` 的 `*Default` 首页入口、`UiTheme/QuickSwitch.json` 的快捷菜单、`tasks.json` 的 `ReturnButton`、`EnterEpisodeNew-Click`、`Mail`。没有出处的按钮写成 `locate` 跳转，第一次走时由视觉模型定位，走通后学成 `tap` 跳转。

界面（`origin='seed'`，`recognizer.max_distance=10`）：

| id | title | kind | rois（除整屏 `[0,0,1,1]` 外的那个） | 出处 | 特征描述 |
|---|---|---|---|---|---|
| `home` | 主界面 | screen | `[0.1773,0.4542,0.1242,0.2111]` | `Home` 模板 roi | 游戏首页，右侧是终端、采购中心、公开招募、干员寻访、基建、仓库这些入口 |
| `terminal` | 终端 | screen | `[0.3609,0.8333,0.1094,0.1667]` | `GoTerminalStopflag` roi | 作战终端，底部是关卡分类栏 |
| `main_theme` | 主题曲 | screen | — | | 主题曲页，右上角有「乐章收录」，下方有「前往章节」 |
| `main_episode` | 主线章节 | screen | — | | 某一章主线的关卡地图，关卡节点横向排开 |
| `activity_hub` | 当期活动 | screen | — | | 当期活动的主页或关卡地图 |
| `stage_start` | 关卡开始页 | stage | 只用 `[0.7891,0.8681,0.2031,0.0847]`，`generic=true` | `StartButton1` roi | 某个关卡的出战确认页，右下角有「开始行动」 |
| `infrast` | 基建 | screen | `[0.0000,0.1389,0.2344,0.2083]` | `InfrastEnteredFlag` roi | 基建总览 |
| `recruit` | 公开招募 | screen | `[0.0391,0.1389,0.1797,0.1389]` | `RecruitFlag` roi | 公开招募的四个招募位 |
| `store` | 采购中心 | screen | `[0.7812,0.0000,0.2188,0.4167]` | `CreditStoreOcr` roi | 采购中心，右上有信用交易所这些分区 |
| `depot` | 仓库 | screen | — | | 仓库物品列表 |
| `operator_list` | 干员 | screen | — | | 干员列表 |
| `headhunt` | 干员寻访 | screen | — | | 卡池页 |
| `friends` | 好友 | screen | — | | 好友列表 |
| `mail` | 邮件 | dialog | — | | 邮件面板 |
| `quick_menu` | 快捷菜单 | menu | — | | 左上角菜单展开后的快捷跳转面板 |

「—」表示 `rois=[[0,0,1,1]]`；其余行是 `[[0,0,1,1], <表中值>]`，`stage_start` 例外，只有表中那一个。

跳转（`origin='seed'`；`tap` 的 `wait_ms` 用默认 1200）：

| 从 | 到 | 动作 | 出处 |
|---|---|---|---|
| home | infrast | tap 0.8164, 0.8750 | `InfrastDefault` `[930,540,230,180]` |
| home | recruit | tap 0.8008, 0.6944 | `RecruitDefault` `[925,425,200,150]` |
| home | store | tap 0.6758, 0.6840 | `MallDefault` `[772,407,186,171]` |
| home | depot | tap 0.9332, 0.8889 | `DepotDefault` `[1109,560,171,160]`；与 screen-home 条目的 93%、89% 一致 |
| home | operator_list | tap 0.8340, 0.4931 | `OperBoxDefault` `[935,300,265,110]` |
| home | headhunt | tap 0.9109, 0.7125 | `GachaDefault` `[1052,426,228,174]` |
| home | friends | tap 0.2734, 0.8333 | `FriendsDefault` `[250,500,200,200]` |
| home | mail | tap 0.1523, 0.0938 | `Mail` `[120,0,150,135]` |
| home | terminal | locate「首页上标着「终端」的大按钮，按钮里显示当前理智」 | 没有固定坐标 |
| terminal | main_theme | locate「终端底部分类栏里的「主题曲」」 | 没有固定坐标 |
| terminal | activity_hub | locate「终端里当期活动的入口横幅」 | 没有固定坐标 |
| main_theme | main_episode | tap 0.8859, 0.8556 | `EnterEpisodeNew-Click`「前往章节」`[1078,595,112,42]` |
| terminal、infrast、recruit、store、depot、operator_list、headhunt、friends（8 条） | home | tap 0.0703, 0.0556 | `ReturnButton` `[0,0,180,80]` |
| main_theme→terminal、main_episode→main_theme、activity_hub→terminal（3 条） | 见左 | tap 0.0703, 0.0556 | 同上 |
| terminal、depot、store、recruit、operator_list、main_theme、main_episode、activity_hub、stage_start（9 条） | quick_menu | tap 0.2102, 0.0931 | `QuickSwitch@ToHome@Open` `[197,0,144,134]` |
| quick_menu | home | tap 0.0727, 0.3896 | `QuickSwitch@ToHome@Entry` `[11,206,164,149]` |
| quick_menu | terminal | tap 0.4137, 0.1028 | `QuickSwitch@ToTerminal@Entry` `[450,0,159,148]` |
| quick_menu | operator_list | tap 0.2402, 0.3903 | `QuickSwitch@ToOperBox@Entry` `[226,204,163,154]` |
| quick_menu | infrast | tap 0.5352, 0.1021 | `QuickSwitch@ToInfrast@Entry` `[610,0,150,147]` |

共 36 条。关卡页（`stage:<代号>`）不进种子：关卡在章节地图里的位置随滚动变化，MAA 自己也是靠 OCR 加滑动找关卡，固定坐标没有出处。

种子以首页默认主题为准；用户换了首页主题，首页入口的坐标可能失效，三次失败后自动停用，由视觉定位或用户在导航页修正。

`main_theme`、`main_episode`、`activity_hub`、`depot`、`operator_list`、`headhunt`、`friends`、`mail`、`quick_menu` 只有整屏 dHash：换章节、换活动、滚动或换主题会让签名漂移，认不出是预期行为。它们第一次到达靠 §4.5.4 的首达信任写入样本；之后认不出就走回主界面重试，重试仍失败时报 `NAV_STEP_FAILED`，用户可在导航页删掉漂移样本或用「标记当前画面」重新采样。T3 的种子测试不要假设这些界面能稳定命中 hash。

#### 4.5.7 边界情况

| 输入 | 期望行为 |
|---|---|
| 设备未连接时调用 `where_am_i` | `DEVICE_NOT_CONNECTED`（截图那一步抛出） |
| `nav.enabled=false` | 三个工具都抛 `SERVICE_UNAVAILABLE`；REST 端点照常读写 |
| 当前画面认不出、没有视觉模型 | 先执行回主界面，再规划；回主界面失败报 `home_failed` |
| 走某条跳转后到了另一个已知界面 | 这条跳转记失败、本次不再用；学一条「当前 → 实际界面」的跳转，从实际界面重新规划 |
| 首次去一个没有样本的种子节点 | 按首达信任收下并加第一个样本；`nav.learn=false` 时不收，走回主界面重试 |
| 导航中途有流水线开始运行 | 原样抛 `PIPELINE_ALREADY_RUNNING`，不算跳转失败 |
| 设备被用户接管 | M71 让下一次写设备的动作拿到 `DEVICE_LEASE_PREEMPTED`，原样抛出 |
| REST 导航期间另一个 Worker 取走租约 | 不保证互斥；下一次设备动作按当时的实际持有者执行或报租约错误 |
| 学样本时截图归档失败 | 只记 warning，不影响导航结果 |
| PUT 节点改了 rois，有样本的截图文件已被清理 | 那个样本删掉，其余重算 |
| `remember_screen` 时另一个 Worker 正在操作设备 | 截到过渡帧时样本不可用；用户移除该样本后重拍 |

### 4.6 前端

- 路由：`/more/nav`。`web/src/routes/router.tsx` 加 `const NavGraphPage = lazy(() => import('@/features/nav/NavGraphPage'))` 与 `{ path: 'more/nav', element: page(<NavGraphPage />) }`；`NavGraphPage` 必须默认导出（`export default function NavGraphPage`）。`secondaryNav` 加 `{ to: '/more/nav', label: '界面导航图', icon: Route, description: '常用界面与跳转，导航员按它一步走到', group: 'agent' }`（`Route` 来自 `lucide-react`）。
- 文件：`web/src/features/nav/NavGraphPage.tsx`、`NodeList.tsx`、`NodeEditor.tsx`、`EdgeTable.tsx`、`EdgeEditor.tsx`、`api.ts`、`NavGraphPage.test.tsx`。
- 请求与 query key：`web/src/api/keys.ts` 加 `navGraph: () => [...root, 'agent', 'nav', 'graph'] as const`；GET `/api/agent/nav/graph` 用这个 key，三个写操作成功后 invalidate 这个 key。「识别当前画面」调 `POST /api/agent/tools/where_am_i/invoke`（`{arguments: {}}`），结果只在页面上显示，不写缓存。
- 页面结构：
  - 顶部 `Section title="界面导航图"`，`description` 显示「{n} 个界面 · {m} 条跳转」，`hint`：「导航员按这张图从当前界面一步走到目标。跳转失败三次会自动停用，可以在这里重新启用或删掉。」；`actions` 放按钮「识别当前画面」。视觉模型不可用时在 hint 末尾补一句「当前没有可用的视觉模型，标着「按描述定位」的跳转不会被使用。」
  - 「界面」区：卡片列表，每张显示标题、id、类型、来源（种子、学到的、手动）与样本缩略图（`thumb_url`）。点开进 `NodeEditor`：改标题与类型、编辑特征描述、按钮「标记当前画面」（PUT `add_exemplar: 'current'`）、每个样本旁的「移除」；高级区（折叠）里用 JSON 文本框编辑 `rois` 与 `max_distance`。按钮「新建界面」打开同一个编辑器，id 输入框校验 `NODE_ID_PATTERN`。
  - 「跳转」区：表格列「从」「到」「动作」「成功/失败」「耗时」「状态」；动作列把 `tap` 写成「点击 (x, y)」、`locate` 写成「按描述定位」、`sequence` 写成「{n} 步操作」、`key` 写成「返回键」。每行操作：开关「启用」、按钮「删除跳转」（确认弹窗标题「删除这条跳转？」，描述「删除后导航不会再走这条路，需要时可以重新新建。」）。按钮「新建跳转」打开 `EdgeEditor`：选「从」「到」，动作类型选「点击」时填 x、y（0–1）。
- 空状态：「还没有界面节点」/「重启服务后会写入种子界面」。
- Toast：「已保存界面」「已删除跳转」「已启用跳转」「已停用跳转」。
- 错误提示：「保存界面失败：{message}」「删除跳转失败：{message}」。

### 4.7 提示词与代理定义

`navigator.md`：frontmatter 的 `tools` 末尾加 `where_am_i`、`remember_screen`、`navigate_to`，`version` 改成 `2`；`risk_ceiling` 从 `none` 改成 `consume`（否则首次 `navigate_to` 在 `MandateMiddleware` 被 `MANDATE_SCOPE_EXCEEDED` 拒绝，与 §4.5.5 的原子操作会话授权矛盾；实施偏离，见 [13 §8.180](../13-决策记录.md)）；正文末尾追加：

```markdown
## 界面导航图

- 动手前先用 where_am_i 确认当前在哪个界面，再决定怎么走。
- 目标界面在导航图里时，直接调用一次 navigate_to，中途的识别和校验由它完成；不要拆成一步一步的 click。
- navigate_to 返回 NAV_NODE_NOT_FOUND 或 NAV_PATH_NOT_FOUND 时，看 details 里的 nearest：先 navigate_to 到那里，再用 get_screenshot 和 click 手动找。找到之后用 remember_screen 记下这个界面，关卡页写成 stage:<关卡代号>，title 写中文名，下次就能一步到。
- 返回 NAV_STEP_FAILED 时，details 里有走到了哪一步、停在什么画面。先 get_screenshot 看清楚再决定，同一个 navigate_to 原样重试最多一次。
- where_am_i 认不出画面时，先截图看是不是弹窗或正在加载：弹窗就关掉，加载就等一会儿再识别。
- 不能确定是否已经到达目标时，以 where_am_i 和截图为准，不要凭记忆假定。
```

`general.md` 不加这两个工具（`version` 保持 6）；其他代理也不直接拿导航工具，导航交给 `navigator`。

## 5. 实施任务

### T1 数据模型与迁移

- 写范围：`maa_api/domain/enums.py`、`maa_api/db/models.py`、`maa_api/db/migrations/versions/0041_nav_graph.py`、`tests/db/test_models.py`、`tests/db/test_nav_graph_migration.py`、`tests/domain/test_enums.py`
- 步骤：1. 加两个枚举与两个表类（§4.1）。2. 写迁移，`down_revision` 为 `0040_question_expiry_historical`。3. `test_models.py`：`BUSINESS_TABLES` 加 `nav_node`、`nav_edge`；`UUID_PK_TABLES` 加 `nav_edge`（`nav_node` 的 id 是短 slug，不加）；`EXPECTED_INDEXES` 加 `uq_nav_edge_from_node_to_node_action_hash`；`EXPECTED_FK_CASCADE` 加 `("nav_edge", "from_node")`、`("nav_edge", "to_node")`；`EXPECTED_JSON_COLUMNS` 加 `nav_node.exemplars`、`nav_node.recognizer`、`nav_edge.action`；把 `test_metadata_has_exactly_56_business_tables` 改名为 `test_metadata_has_exactly_57_business_tables`，docstring 与 `maa_api/db/models.py` 模块 docstring 的「56 张业务表」改成「57 张业务表」；`test_index_inventory_matches_spec_exactly` 的 docstring「63 条」改成「64 条」。4. 新迁移测试：在 `0040_question_expiry_historical` 上升级到 `0041_nav_graph`，断言两表、列、唯一索引与两条 CASCADE 外键；插入两节点一条边后唯一索引冲突抛 `sqlite3.IntegrityError`；降级回 `0040_question_expiry_historical` 后两表不存在；再升级可重放且是空表；`--sql` 离线渲染两个方向。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/db tests/domain/test_enums.py > /tmp/m86-t1.log 2>&1; echo $?; tail -n 5 /tmp/m86-t1.log` → `0`
- 完成判据：`tests/db/test_initial_migration.py` 的元数据比对、可重复升级、降级到底再升级都通过；`alembic heads` 只有一个 `0041_nav_graph`。

### T2 截图归档、感知哈希、视觉客户端与识别器

- 写范围：`maa_api/services/screenshot_archive.py`、`maa_api/agent/nav/__init__.py`、`maa_api/agent/nav/recognizer.py`、`maa_api/agent/llm/schemas/nav_classification.py`、`nav_locate.py`、`schemas/__init__.py`、`tests/fakes/nav.py`、`tests/agent/nav/__init__.py`、`tests/agent/nav/test_phash.py`、`tests/agent/nav/test_recognizer.py`、`tests/agent/nav/test_vision_client.py`、`tests/agent/nav/test_screenshot_archive.py`
- 步骤：1. `archive_screenshot` 照 `maa_api/agent/harness/builtin_middleware.py` 的 `archive_screenshot_result` 的落文件与建行写法；区别是入参是 `Image`，`archive_screenshot_result` 收的是编码后的截图 mapping。2. 实现 §4.5.2 的 dHash、签名、距离、近邻与歧义判定。3. `ModelVisionClient` 按 §4.2 的依赖取法；`available()` 是 async，每次调用解析一次 vision 档案，不跨调用缓存；档案 `supports_vision is not True` 或解析抛 `AppError` 时返回 False。4. `tests/fakes/nav.py` 写 `make_screen(name: str) -> Image.Image`（按名字的哈希在 1280×720 灰底上画 6 个确定位置的色块，不同名字的图互相距离 > 20）、`FakeVision`（可预设 `classify`、`locate` 的返回与调用计数）、`FakeNavDevice`（T4 用）。5. 性能用例：1280×720 图重复 20 次取中位数，断言 ≤ 200 ms。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/agent/nav > /tmp/m86-t2.log 2>&1; echo $?; tail -n 5 /tmp/m86-t2.log` → `0`
- 完成判据：§6 中 T2 的用例全部通过；`tests/agent/llm/test_structured.py` 不因新增 schema 失败。

### T3 图服务与种子

- 写范围：`maa_api/agent/nav/graph.py`、`maa_api/agent/nav/seed.py`、`tests/agent/nav/test_graph_service.py`、`tests/agent/nav/test_seed.py`
- 步骤：1. 动作模型、`action_hash`、`validate_action`。2. `NavGraphService` 全部方法；所有写操作用 `async with session_factory() as s` + 显式 `commit`，JSON 列整列赋值。3. `seed.py` 照 §4.5.6 的两张表写常量（15 个节点、36 条边），`ensure_seed()` 在 `nav_node` 为空时一次事务写入。4. Dijkstra、隐式回主界面边、`PathPlan.blocked_by_vision`（§4.5.3）。5. `LastSeenStore` 与 `note_seen`。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/agent/nav/test_graph_service.py tests/agent/nav/test_seed.py > /tmp/m86-t3.log 2>&1; echo $?; tail -n 5 /tmp/m86-t3.log` → `0`
- 完成判据：种子写入 15 个节点、36 条边，重复调用 `ensure_seed()` 返回 0；`shortest_path` 的三种返回（路径、None、`blocked_by_vision`）都有用例。

### T4 导航执行器与设备适配

- 写范围：`maa_api/agent/nav/navigator.py`、`maa_api/agent/tools/raw.py`、`tests/fakes/nav.py`（加 `FakeNavDevice`）、`tests/agent/nav/test_navigator.py`、`tests/agent/nav/test_nav_device.py`、`tests/agent/test_device_raw_tools.py`
- 步骤：1. `raw.py` 的 `_run_atomic` 改名 `run_atomic`，同文件调用改掉；`grep -rn "_run_atomic" maa_api/agent/tools/raw.py tests/agent` 确认没有遗漏。**`maa_api/api/routers/device.py` 的同名函数 `_run_atomic` 不动**。2. `AppNavDevice` 用 `run_atomic(context, "navigate_to", …)` 包住 `DeviceManager` 的 `click`、`swipe`、`key_event`、`back_to_home`；`screenshot()` 调 `screenshot(backend="adb")`；`resolution()` 读 `snapshot()["resolution"]`，为空时用截图尺寸并缓存。3. 实现 §4.5.4 的 `navigate`、`perform`、`recover_home`、首达信任、`nearest_container`、trail 与计数。4. 实现 §4.5.5 的 `remember`。5. `FakeNavDevice(transitions: dict[str, str], start: str = "home")`：键是 `(界面, 动作键)` 的动作键，形如 `tap:1045,630`（设备像素）、`swipe:…`、`key:BACK`、`home`；没登记的动作停在原地；`screenshot()` 返回 `make_screen(当前界面)`；`home()` 把当前界面设为 `home` 并返回可配置的布尔；`unknown_once` 让下一次截图返回一张认不出的图。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/agent/nav tests/agent/test_device_raw_tools.py > /tmp/m86-t4.log 2>&1; echo $?; tail -n 5 /tmp/m86-t4.log` → `0`
- 完成判据：§6 中 T4 的用例全部通过；`grep -rn "run_atomic" maa_api/agent/tools/raw.py | wc -l` 与改动前 `_run_atomic` 的调用点数一致（改名不改调用结构）。

### T5 工具、策略与目录

- 写范围：`maa_api/agent/tools/nav.py`、`maa_api/agent/tools/__init__.py`、`maa_api/agent/policy.py`、`tests/agent/test_tool_catalog.py`、`tests/agent/test_nav_tools.py`、`tests/agent/specs/test_agent_specs.py`
- 步骤：1. 三个工具按 §4.3.1 注册，`nav.enabled=false` 时抛 `SERVICE_UNAVAILABLE`。服务取 `app.state.nav_service`；`Recognizer` 与 `ModelVisionClient` 每次调用现建；`LastSeenStore` 用 `nav_service.last_seen`。2. `ATOMIC_TOOL_NAMES` 加 `navigate_to`。3. `EXPECTED_TOOLS` 加三个名字；总数 104→107、模型可见 103→106（`test_tool_catalog.py` 的四处数字）；`test_device_levels_follow_the_contract` 的 exclusive 集合加 `navigate_to`（8→9）、read 集合加 `where_am_i`（4→5）、none 计数改 `len(registry.list()) - 14`；`EXPECTED_REFETCHABLE` 加 `where_am_i`（50→51）；`test_extension_fields_keep_v2_defaults` 的 `v3_only` 集合加 `remember_screen`。4. `tests/agent/specs/test_agent_specs.py`：`test_legacy_chat_lists_every_v2_registered_tool_in_registration_order` 的 `len(definitions)` 从 104 改成 107；新增 `M86_TOOLS = {"where_am_i", "remember_screen", "navigate_to"}` 并把它并入该用例「legacy_chat 不加载」的排除集（与 `M70_TOOLS` 等并列）。5. `UNTRUSTED_OUTPUT_TOOLS` 不变（22 个）。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/agent/test_tool_catalog.py tests/agent/test_nav_tools.py tests/agent/test_registry_policy.py tests/agent/specs/test_agent_specs.py > /tmp/m86-t5.log 2>&1; echo $?; tail -n 5 /tmp/m86-t5.log` → `0`
- 完成判据：工具目录测试与新测试通过；`.venv/bin/python -c "from maa_api.agent.tools import build_registry as b; r=b(); print(len(r.list()), sum(1 for d in r.list() if d.model_visible))"` 输出 `107 106`。

### T6 REST 端点、错误码与装配

- 写范围：`maa_api/api/routers/nav.py`、`maa_api/api/schemas/agent_v3.py`、`maa_api/domain/errors.py`、`maa_api/main.py`、`maa_api/api/routers/__init__.py`（如需导入）、`tests/api/test_nav_router.py`、`tests/domain/test_errors.py`、`docs/v3/14-现行接口清单.md`、`docs/v3/15-开放API接入指南.md`、`web/openapi.json`、`web/src/types/api.d.ts`
- 步骤：1. 错误码 `NAV_NODE_NOT_FOUND`（404）、`NAV_PATH_NOT_FOUND`（409）、`NAV_STEP_FAILED`（409），每个上一行写中文注释；`ERROR_HTTP_STATUS` 加三条；模块注释与 `tests/domain/test_errors.py` 的 `ALL_CODE_COUNT` 168→171、`HTTP_BOUND_CODE_COUNT` 167→170、`SECTION_COUNT` 21→22。2. 路由按 §4.3.2，四个端点都挂 `require_auth`，`responses=error_responses(...)` 列出可能的码。3. `main.py`：在 `app.state.channel_service` 赋值之后、`update_scheduler` 之前 `app.state.nav_service = NavGraphService(db_session.session_factory)`，随后 `await app.state.nav_service.ensure_seed()`（失败只记 `logger.exception`，不拖垮启动）；`create_app()` 里 `include_router(nav.router)`。4. 现行接口清单：§4 末尾新建 `### 4.22 界面导航图` 放三个码，§4 开头的「共 168 条」改 171；§6 开头的「共 182 条」改 186，§6.15 的「（79 条）」改「（83 条）」并在表末追加四行。5. `.venv/bin/python scripts/generate_api_guide.py`；`.venv/bin/python scripts/dump_openapi.py && (cd web && pnpm gen:api)`。
- 验证：
  ```bash
  .venv/bin/python -m pytest -o addopts= -q tests/api/test_nav_router.py tests/domain/test_errors.py tests/domain/test_route_inventory.py tests/api/test_app_skeleton.py > /tmp/m86-t6.log 2>&1; echo $?; tail -n 5 /tmp/m86-t6.log
  .venv/bin/python scripts/dump_openapi.py --check; echo $?
  ```
  → 两个 `0`
- 完成判据：19 个 tag 的断言不变；operationId 为 `agent_get_nav_graph`、`agent_put_nav_node`、`agent_update_nav_edge`、`agent_delete_nav_edge`；`tests/domain/test_route_inventory.py::test_declared_total_matches_api_rows_and_openapi` 通过。

### T7 配置项

- 写范围：`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml`、`tests/test_settings.py`
- 步骤：按 §4.4。`_FIELDS` 加三行（128→131），`SETTING_KEYS` 加三行（139→142），`CONFIG_ONLY_SETTING_KEYS` 不变（13）。`tests/test_settings.py` 加三条：默认值、`MAA_NAV_*` 环境变量覆盖、`schema_for` 的 label/group/type/min/max。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/test_settings.py tests/test_vapid_settings.py > /tmp/m86-t7.log 2>&1; echo $?; tail -n 3 /tmp/m86-t7.log` → `0`
- 完成判据：设置页 schema 里出现三个键，分组 Agent；`config.template.yaml` 与 `NavSettings` 默认值一致。

### T8 代理定义

- 写范围：`maa_api/agent/specs/builtin/navigator.md`、`tests/api/test_agent_specs_router.py`
- 步骤：按 §4.7 改 `navigator.md`（三个工具、`version: 2`、正文追加一段）；`general.md` 不动；`tests/api/test_agent_specs_router.py` 的 `EXPECTED_VERSIONS["navigator"]` 改成 2，`general` 保持 6。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/agent/specs tests/api/test_agent_specs_router.py > /tmp/m86-t8.log 2>&1; echo $?; tail -n 3 /tmp/m86-t8.log` → `0`
- 完成判据：`AgentSpecRegistry.load()` 不报错；`navigator` 的工具集含三个新工具且 `general` 不含；版本断言通过。

### T9 前端导航图页

- 写范围：`web/src/features/nav/*`、`web/src/routes/router.tsx`、`web/src/components/layout/nav.ts`、`web/src/api/keys.ts`、`web/src/routes/more/more.test.tsx`
- 步骤：按 §4.6。`NavGraphPage` 默认导出；`more.test.tsx` 加两条断言：`screen.getByRole('link', { name: /界面导航图/ })` 指向 `/more/nav`，`routeConfig` 的 `children` 含 `more/nav`。
- 验证：`(cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run src/features/nav src/routes/more) > /tmp/m86-t9.log 2>&1; echo $?; tail -n 5 /tmp/m86-t9.log` → `0`
- 完成判据：`NavGraphPage.test.tsx` 覆盖列表渲染、启停、删除、新建跳转、识别当前画面五个流程；「更多」页显示入口。

### T10 冒烟脚本

- 写范围：`scripts/nav_smoke.py`
- 步骤：1. 真实 lifespan + 临时目录（照 `scripts/agent_smoke.py` 的隔离写法），`install_fake_lifespan` 装假设备边界；lifespan 起来后把 `app.state.nav_service.device_factory` 换成 `lambda context: FakeNavDevice(transitions=..., start="home")`（`tests/fakes/nav.py` 的类）。2. 依次断言：种子 15/36 → `where_am_i` 在 home 上返回 unknown（`node=None`、`method="none"`）→ `navigate_to("infrast")` 两步到达、`success_count=1`、home 与 infrast 各有样本 → 再 `where_am_i` 得到 `infrast`、`method="hash"` → 让 home→recruit 的动作不改变界面，连续三次 `navigate_to("recruit")` 都报 `NAV_PATH_NOT_FOUND`，第三次后该边 `enabled=false` → `remember_screen(node_id="stage:1-7", title="1-7")` 在当前画面（把设备停在 stage 图）建节点与样本。3. 断言失败打印 `[FAIL] 原因` 并返回 1；成功末行严格 `SMOKE OK`。
- 验证：`.venv/bin/python scripts/nav_smoke.py > /tmp/m86-smoke.log 2>&1; echo $?; tail -n 1 /tmp/m86-smoke.log` → `0`、`SMOKE OK`
- 完成判据：脚本在临时目录运行，不读也不写仓库的 `resource/maa_api.db` 与 `config.yaml`。

### T11 评测场景与假导航世界

`ticket` 入口的评测驱动由**维护性补丁 `evals-ticket-driver`** 提供（见 §0 #16 与 §3），本任务只做 M86 特有的假导航世界、两个能力场景与 cassette。

- 写范围：`maa_api/agent/evals/scenario.py`（只加 `World.nav`/`NavWorld`）、`tests/evals/environment.py`（world.nav 绑定假设备）、`tests/evals/scenarios/nav-infrast-navigate-to.yaml`、`tests/evals/scenarios/nav-unknown-stage-remember.yaml`、`tests/evals/cassettes/nav-infrast-navigate-to.jsonl`、`tests/evals/cassettes/nav-unknown-stage-remember.jsonl`、`tests/evals/test_regression_replay.py`、`tests/evals/test_eval_scenario.py`
- 步骤：
  1. `scenario.py`：`World` 加 `nav: NavWorld | None = None`；`NavWorld` 字段 `start: str = "home"`、`transitions: dict[str, str] = {}`，`extra="forbid"`。`Scenario.human` 与 `HumanPolicy` 由补丁提供，本任务只读用。
  2. `tests/evals/environment.py`：`_open_world` 在 TestClient 进入后，若 `scenario.world.nav` 非空，用 T4 的 `FakeNavDevice(transitions=..., start=...)` 建假设备，并设 `app.state.nav_service.device_factory = lambda _context: device`。
  3. 两个场景的 YAML 按下面写死；cassette 是手写的 JSONL，第一行 header（`"source": "handwritten"`，`"agent": "general"`），之后是 9/10 条 agent 通道应答（含 `ready` 结束后的记忆抽取 1 条，按维护性补丁 `evals-ticket-driver` 的口径；7/9 是补丁前口径）。前两条是档案首次使用时的动态上下文落点探查（system 开头、没有 tools，按 M68 归到 agent 通道），应答 `{"text":"OK"}`；后面依次是 Worker 的 `delegate`、导航员的 `where_am_i`/`navigate_to`、场景一的 1 条总控 system 观察应答（`navigate_to` 的 `grant_atomic_ops` 确认会经 `bubble_human_request` 给总控投一条观察消息并起一轮模型调用）、场景二的 `remember_screen` 与第二次 `where_am_i`、Worker 的 `report`。工具调用 id 用 `call-delegate`、`call-where-1`、`call-navigate`、`call-remember`、`call-where-2`、`call-report`；`report` 成功后 `ReportMiddleware` 结束 Worker 运行，不再有下一条 Worker 工具调用应答；`ready` 结束的场景末尾还有一条记忆抽取应答（见上）。场景一实际消费顺序是「探查 ×2 → `delegate` → `where_am_i` → `navigate_to` → 总控文本 → 导航员最终正文 → `report` → 记忆抽取」共 9 条，场景二 10 条（见 [13 §8.180](../13-决策记录.md)）。
  4. `test_regression_replay.py` 的场景数 11→13；`test_eval_scenario.py` 加一条解析用例，确认 `world.nav.start/transitions` 能读入，未知字段报错。
- 场景一 `tests/evals/scenarios/nav-infrast-navigate-to.yaml`：

```json
{
  "id": "nav-infrast-navigate-to",
  "title": "导航到基建",
  "suite": "capability",
  "agent": "general",
  "source": "M86 §1 目标 2/3",
  "tags": ["nav"],
  "entry": {
    "kind": "ticket",
    "worker": "general",
    "title": "导航到基建",
    "brief": "导航到基建，到了告诉我。",
    "scope": "在游戏界面里导航到基建"
  },
  "human": {"confirmations": "approve"},
  "settings": {"nav.enabled": true, "nav.learn": true},
  "world": {
    "screen": "home",
    "nav": {
      "start": "home",
      "transitions": {"tap:1045,630": "infrast", "tap:90,40": "home"}
    }
  },
  "expect": [
    {"grader": "tool_sequence", "match": "subsequence",
     "steps": [{"tool": "delegate"}, {"tool": "where_am_i"}, {"tool": "navigate_to"}]},
    {"grader": "outcome_state",
     "runs": {"count": {"gte": 2}, "all": [{"agent_name": "navigator", "status": "succeeded"}]},
     "model_calls": {"lte": 12}},
    {"grader": "text_contains", "any_of": ["基建", "到了"]}
  ],
  "limits": {"timeout_seconds": 90, "max_model_calls": 15}
}
```

- 场景二 `tests/evals/scenarios/nav-unknown-stage-remember.yaml`：

```json
{
  "id": "nav-unknown-stage-remember",
  "title": "记下未知关卡界面",
  "suite": "capability",
  "agent": "general",
  "source": "M86 §1 目标 4",
  "tags": ["nav"],
  "entry": {
    "kind": "ticket",
    "worker": "general",
    "title": "记下 1-7 界面",
    "brief": "导航到 1-7；导航图里没有就把当前界面记成 stage:1-7，标题写 1-7。",
    "scope": "记下一个关卡界面"
  },
  "human": {"confirmations": "approve"},
  "settings": {"nav.enabled": true, "nav.learn": true},
  "world": {
    "screen": "stage",
    "nav": {"start": "stage:1-7", "transitions": {}}
  },
  "expect": [
    {"grader": "tool_sequence", "match": "subsequence",
     "steps": [
       {"tool": "where_am_i"},
       {"tool": "navigate_to", "status": "error", "error_code": "NAV_NODE_NOT_FOUND"},
       {"tool": "remember_screen"},
       {"tool": "where_am_i"}
     ]},
    {"grader": "outcome_state",
     "runs": {"count": {"gte": 2}, "all": [{"agent_name": "navigator", "status": "succeeded"}]},
     "model_calls": {"lte": 14}},
    {"grader": "text_contains", "any_of": ["1-7", "记下"]}
  ],
  "limits": {"timeout_seconds": 90, "max_model_calls": 18}
}
```

- 验证：
  ```bash
  .venv/bin/python -m pytest -o addopts= -q tests/evals > /tmp/m86-eval-unit.log 2>&1; echo $?; tail -n 5 /tmp/m86-eval-unit.log
  .venv/bin/python scripts/agent_eval.py --suite capability --mode replay --scenario nav-infrast-navigate-to --scenario nav-unknown-stage-remember > /tmp/m86-eval.log 2>&1; echo $?; tail -n 8 /tmp/m86-eval.log
  ```
  → 两个 `0`，两个场景 `通过`、`模型调用` 分别 ≤ 12 与 ≤ 14
- 完成判据：13 个场景都有 cassette 且 replay 通过；手写 cassette 与 `--strict` 回放都不报 `mismatch`/`exhausted`。

### T12 文档同步

- 写范围：§9 列出的文档
- 步骤：按 §9 逐项改；计数按 §9 的表写；`13 §8` 在实施有偏离时补一条，没有偏离就只写台账。
- 验证：`.venv/bin/python scripts/check_doc_links.py | tail -n 1` → `BROKEN 0`
- 完成判据：§9 的每一项都改完；`docs/v3/14-现行接口清单.md` 的解析测试通过。

## 6. 测试清单

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/db/test_models.py` | `test_metadata_has_exactly_57_business_tables`（改名）、`test_foreign_key_ondelete_map`、`test_index_inventory_matches_spec_exactly`、`test_json_columns_use_json_type` | 业务表集合含 `nav_node`/`nav_edge`；两条外键 CASCADE；唯一索引在清单；三个 JSON 列在清单 |
| `tests/db/test_nav_graph_migration.py` | `test_upgrade_creates_nav_tables_and_index` | 两表列/可空性、唯一索引、外键与 03 §12 一致 |
| `tests/db/test_nav_graph_migration.py` | `test_nav_edge_unique_conflict` | 同 from/to/action_hash 第二条插入抛 `IntegrityError` |
| `tests/db/test_nav_graph_migration.py` | `test_downgrade_drops_tables_and_reupgrades_empty` | 降级到 0040 后两表消失；再升级为空表 |
| `tests/db/test_nav_graph_migration.py` | `test_offline_sql_renders_both_directions` | `--sql` 两个方向都能渲染，升级不建库文件 |
| `tests/domain/test_enums.py` | 现有整表用例 | `NavNodeKind`、`NavOrigin` 的成员与取值逐项一致 |
| `tests/agent/nav/test_phash.py` | `test_identical_images_have_zero_distance`、`test_signature_has_one_segment_per_roi` | 同图距离全 0；两个 ROI 得到两段 16 位十六进制，`:` 分隔 |
| `tests/agent/nav/test_phash.py` | `test_small_noise_stays_within_threshold`、`test_different_layouts_exceed_threshold` | ±3 灰度噪声后每段 ≤ 10；`make_screen("a")` 与 `("b")` 距离 > 20 |
| `tests/agent/nav/test_phash.py` | `test_roi_count_mismatch_returns_none` | `distances` 返回 None |
| `tests/agent/nav/test_phash.py` | `test_hash_recognition_stays_under_200ms` | 1280×720 图重复 20 次，中位数 ≤ 200 ms |
| `tests/agent/nav/test_recognizer.py` | `test_nearest_exemplar_wins` | 返回距离更近的节点，`method="hash"` |
| `tests/agent/nav/test_recognizer.py` | `test_ambiguous_match_falls_back_to_vision`、`test_generic_node_yields_to_specific_stage` | 得分差 < 2 时调了 `FakeVision.classify`；`stage_start` 与 `stage:1-7` 都匹配时取后者且不调视觉 |
| `tests/agent/nav/test_recognizer.py` | `test_vision_below_confidence_is_unknown`、`test_vision_unavailable_returns_unknown`、`test_vision_answer_outside_candidates_is_rejected` | 三种情况都是 `node_id=None`、`method="none"`；不可用时不调 `classify` |
| `tests/agent/nav/test_recognizer.py` | `test_recognize_does_not_write_graph` | 识别前后 `graph()` 的节点与样本完全一致 |
| `tests/agent/nav/test_vision_client.py` | `test_available_requires_supports_vision` | 档案 `supports_vision=True` 为真，False/None 为假 |
| `tests/agent/nav/test_vision_client.py` | `test_available_is_false_without_router_or_ledger` | `app_state` 缺依赖时返回 False 且不抛 |
| `tests/agent/nav/test_vision_client.py` | `test_classify_and_locate_pass_agent_name_and_ids` | monkeypatch `complete_structured`，断言 `agent_name="navigator"`、`role="vision"`、`schema_name`、`run_id`/`session_id` |
| `tests/agent/nav/test_vision_client.py` | `test_structured_failure_returns_none` | `complete_structured` 抛 `STRUCTURED_OUTPUT_INVALID` 时为 None，不向上抛 |
| `tests/agent/nav/test_screenshot_archive.py` | `test_archive_screenshot_stores_file_and_row` | 文件落在 `image/screenshot/<sha 前两位>/<sha>.jpg`，行写 trigger/backend/尺寸/字节数 |
| `tests/agent/nav/test_screenshot_archive.py` | `test_archive_screenshot_returns_thumb_url` | 返回 `screenshot_id`、`sha256`、`thumb_url` |
| `tests/agent/nav/test_graph_service.py` | `test_action_hash_ignores_wait_and_rounds_coordinates` | `wait_ms` 不同、坐标差 0.0001 的两个 tap 哈希相同；`x` 差 0.002 的两个 tap 哈希不同 |
| `tests/agent/nav/test_graph_service.py` | `test_shortest_path_prefers_cheaper_edges`、`test_disabled_and_excluded_edges_are_skipped` | 选代价小的路径；停用与 `exclude` 的边不出现 |
| `tests/agent/nav/test_graph_service.py` | `test_implicit_home_edge_connects_everything`、`test_locate_edges_need_vision` | 没有显式边时经隐式边到 home；`vision=False` 绕开 locate 边，只剩它时 `PathPlan((), True)` |
| `tests/agent/nav/test_graph_service.py` | `test_shortest_path_returns_none_without_any_path`、`test_same_node_returns_empty_plan` | 孤立节点返回 None；源等于目标返回空 plan、`blocked_by_vision=False` |
| `tests/agent/nav/test_graph_service.py` | `test_three_failures_disable_edge`、`test_failures_do_not_disable_a_proven_edge` | 0 成功 3 失败后停用且返回 True；5 成功 3 失败仍启用 |
| `tests/agent/nav/test_graph_service.py` | `test_success_updates_cost_moving_average`、`test_reenable_resets_fail_count` | 1000 → 2000 后 `cost_ms=1300`；重新启用后 `fail_count=0` |
| `tests/agent/nav/test_graph_service.py` | `test_exemplars_dedupe_and_cap` | 近重复不加；第 9 个挤掉最早的非 user 样本；`source` 与 `added_at` 落进 JSON |
| `tests/agent/nav/test_graph_service.py` | `test_last_seen_cursor_advances` | 认出节点后 `last_seen.get` 返回节点与当前审计最大 id |
| `tests/agent/nav/test_seed.py` | `test_seed_counts_and_idempotence` | 15 节点、36 边；第二次返回 0 |
| `tests/agent/nav/test_seed.py` | `test_seed_coordinates_are_normalized` | 坐标都在 0–1；返回键那几条是 0.0703、0.0556；采购中心 x 是 0.7812 |
| `tests/agent/nav/test_seed.py` | `test_seed_has_no_stage_specific_nodes` | 没有 `stage:` 开头的 id |
| `tests/agent/nav/test_navigator.py` | `test_navigate_one_hop_records_success`、`test_already_there_does_nothing` | home→infrast 一步、成功计数 +1；目标是当前界面时设备没收到动作 |
| `tests/agent/nav/test_navigator.py` | `test_first_arrival_bootstraps_seed_exemplar` | 首次到 infrast：目标没有样本也收下，`learned_exemplars` 加 1，trail 末项 `ok=true` |
| `tests/agent/nav/test_navigator.py` | `test_bootstrap_skipped_when_learning_off` | `learn=False` 时没有新样本，最终 `NAV_STEP_FAILED` |
| `tests/agent/nav/test_navigator.py` | `test_wrong_landing_learns_edge_and_replans` | tap 到了 depot：原边失败 +1、新增 learned 边 home→depot、从 depot 继续 |
| `tests/agent/nav/test_navigator.py` | `test_unknown_screen_recovers_via_home`、`test_home_failure_raises_step_failed` | 认不出 → 等待再认 → 回主界面后到达；`home()` 为 False → `NAV_STEP_FAILED(home_failed)` |
| `tests/agent/nav/test_navigator.py` | `test_max_steps_raises_step_failed`、`test_missing_target_suggests_container`、`test_no_path_raises_path_not_found`、`test_locate_only_path_raises_needs_vision` | `max_steps=1` 需两步 → `max_steps` 且带 trail；`stage:1-7` → `NAV_NODE_NOT_FOUND`、`nearest=main_episode`；孤立节点 → `NAV_PATH_NOT_FOUND(no_edges)`；只靠 locate 边 → `needs_vision` |
| `tests/agent/nav/test_navigator.py` | `test_locate_edge_learns_tap_edge`、`test_home_anchor_adds_home_exemplar` | 视觉给 (0.5, 0.5)，到达后多一条 learned tap 边；`home()` 为 True 后 home 多一个 `anchor` 样本 |
| `tests/agent/nav/test_navigator.py` | `test_trail_has_fixed_keys_and_types` | 每项恰好 `edge_id/action/from/expected/actual/ok/ms` 七键，`ms` 为非负整数 |
| `tests/agent/nav/test_navigator.py` | `test_remember_creates_node_and_sequence_edge`、`test_remember_skips_when_screen_is_another_node` | 审计里两次 click → 新节点 + `sequence` 边，坐标按分辨率归一化；识别为 home 时 `skipped_reason="screen_is_home"` |
| `tests/agent/nav/test_navigator.py` | `test_learning_off_changes_nothing` | `learn=False`：没有新边与新样本，失败计数照记 |
| `tests/agent/nav/test_nav_device.py` | `test_each_write_action_runs_inside_run_atomic` | `click`/`swipe`/`key_event`/`back_to_home` 都经过 `run_atomic`，截图不走 |
| `tests/agent/nav/test_nav_device.py` | `test_lease_preempted_propagates` | `DEVICE_LEASE_PREEMPTED` 原样抛出，不被吞 |
| `tests/agent/test_nav_tools.py` | `test_nav_tools_registered_with_extension_fields` | 组 `nav`；`device` 为 `read`/`none`/`exclusive`；`read_only`/`refetchable`/`rest_visible` 与 §4.3.1 一致 |
| `tests/agent/test_nav_tools.py` | `test_where_am_i_is_pure_read` | 调用前后节点、样本、截图行都不变 |
| `tests/agent/test_nav_tools.py` | `test_navigate_to_uses_atomic_grant`、`test_rest_navigate_to_needs_confirmation_each_time`、`test_where_am_i_is_safe` | 内置无授权 → `grant_atomic_ops`，有授权 → 放行；REST 每次确认；`where_am_i` 不确认 |
| `tests/agent/test_nav_tools.py` | `test_remember_screen_creates_node_and_exemplar` | 节点 `origin=learned`、样本 `source=agent`、返回 `created=true` |
| `tests/agent/test_nav_tools.py` | `test_nav_disabled_raises_service_unavailable` | `nav.enabled=false` 时三个工具都报 `SERVICE_UNAVAILABLE` |
| `tests/agent/test_tool_catalog.py` | 现有整表与集合用例 | 总数 107/106；exclusive 9、read 5、none 为总数−14；refetchable 51；`remember_screen` 在 `v3_only` |
| `tests/agent/specs/test_agent_specs.py` | `test_legacy_chat_lists_every_v2_registered_tool_in_registration_order` | `len(definitions)==107`；`M86_TOOLS`（三个导航工具）被排除出 legacy_chat |
| `tests/api/test_nav_router.py` | `test_get_graph_returns_seed`、`test_put_node_creates_user_node`、`test_put_node_rejects_bad_id` | 200 且 15 节点、`vision_available` 是布尔；新 id → `origin=user`；`Bad Id` → 400 |
| `tests/api/test_nav_router.py` | `test_put_node_add_current_exemplar`、`test_put_node_recomputes_exemplars_when_rois_change`、`test_put_node_removes_exemplar_by_phash` | 假设备截图后样本 +1、`source=user`；rois 变成两个后 `phash` 有两段；删除后样本少一条 |
| `tests/api/test_nav_router.py` | `test_put_edge_creates_and_updates`、`test_put_edge_rejects_endpoint_change`、`test_put_edge_unknown_without_fields_is_404`、`test_put_edge_unknown_node_is_404`、`test_delete_edge` | 新建后再 PUT `enabled=false` 停用；改 `from_node` → 400；缺字段 `NOT_FOUND`；端点不存在 `NAV_NODE_NOT_FOUND`；删除 204、再删 404 |
| `tests/domain/test_errors.py` | 现有计数与文档解析用例 | 171/170/22；14 §4.22 三行逐字一致 |
| `tests/domain/test_route_inventory.py` | `test_declared_total_matches_api_rows_and_openapi` | 186 条、agent 83 条；`web/openapi.json` 同步 |
| `tests/test_settings.py` | `test_nav_settings_defaults`、`test_nav_env_overrides`、`test_nav_fields_schema` | 默认 true/true/12；`MAA_NAV_*` 覆盖；label/group/type/范围正确 |
| `tests/api/test_agent_specs_router.py` | `test_list_specs_returns_the_nineteen_builtin_definitions` | `navigator` 版本 2、`general` 版本 6 |
| `web/src/features/nav/NavGraphPage.test.tsx` | `renders nodes and edges`、`toggles an edge`、`deletes an edge after confirm`、`creates a tap edge`、`shows where_am_i result` | 用 `vi.stubGlobal('fetch')` 做假后端，断言请求体与 toast 文案 |
| `web/src/routes/more/more.test.tsx` | `groups the secondary destinations and no longer lists schedules` | 页面含 `/more/nav` 链接；`routeConfig` 含 `more/nav` |
| `tests/evals/test_regression_replay.py` | `test_every_scenario_loads_and_has_a_cassette` | 13 个场景、13 个 id；每个都有 cassette |
| `tests/evals/test_eval_scenario.py` | `test_nav_world_parses` | `world.nav.start/transitions` 能读入，未知字段报错（`human` 由维护性补丁的测试覆盖） |

## 7. 验收

### 7.1 命令（照 12 §4）

```bash
.venv/bin/python -m pytest -o addopts= -q > /tmp/m86-pytest.log 2>&1; echo $?; tail -n 30 /tmp/m86-pytest.log
.venv/bin/python scripts/dump_openapi.py --check; echo $?
(cd web && pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build) > /tmp/m86-web.log 2>&1; echo $?
.venv/bin/python scripts/frontend_smoke.py | tail -n 1
.venv/bin/python scripts/agent_smoke.py | tail -n 1
.venv/bin/python scripts/orchestrator_smoke.py | tail -n 1
.venv/bin/python scripts/nav_smoke.py | tail -n 1
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m86-reg.log 2>&1; echo $?
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m86-cap.log 2>&1; echo $?; tail -n 5 /tmp/m86-cap.log
git diff --check; echo $?
.venv/bin/python scripts/check_doc_links.py | tail -n 1
```

期望：pytest 不新增失败；各退出码 0；四个冒烟末行 `SMOKE OK`；回归评测全部通过，能力评测新增两个场景通过、总通过率不低于 M85 台账记录的基线；`BROKEN 0`。

### 7.2 种子坐标出处核对（本机有 MAA 资源时做，结果记台账）

```bash
.venv/bin/python - <<'EOF'
import glob, json, sys
d = {}
for f in glob.glob('resource/lib/maa/Darwin/resource/tasks/**/*.json', recursive=True):
    try: d.update(json.load(open(f)))
    except Exception: pass
expected = {
    'InfrastDefault': (0.8164, 0.8750), 'RecruitDefault': (0.8008, 0.6944),
    'MallDefault': (0.6758, 0.6840), 'DepotDefault': (0.9332, 0.8889),
    'OperBoxDefault': (0.8340, 0.4931), 'GachaDefault': (0.9109, 0.7125),
    'FriendsDefault': (0.2734, 0.8333), 'Mail': (0.1523, 0.0938),
    'ReturnButton': (0.0703, 0.0556), 'EnterEpisodeNew-Click': (0.8859, 0.8556),
    'QuickSwitch@ToHome@Open': (0.2102, 0.0931),
    'QuickSwitch@ToHome@Entry': (0.0727, 0.3896),
    'QuickSwitch@ToTerminal@Entry': (0.4137, 0.1028),
    'QuickSwitch@ToOperBox@Entry': (0.2402, 0.3903),
    'QuickSwitch@ToInfrast@Entry': (0.5352, 0.1021),
    'Home': (0.2395, 0.5597), 'GoTerminalStopflag': (0.4156, 0.9167),
    'StartButton1': (0.8906, 0.9104), 'InfrastEnteredFlag': (0.1172, 0.2431),
    'RecruitFlag': (0.1289, 0.2083), 'CreditStoreOcr': (0.8906, 0.2083),
}
bad = []
for key, (ex, ey) in expected.items():
    x, y, w, h = d[key]['roi']
    cx, cy = round((x + w/2)/1280, 4), round((y + h/2)/720, 4)
    if abs(cx - ex) > 0.0001 or abs(cy - ey) > 0.0001:
        bad.append((key, cx, cy, ex, ey))
print('BAD', bad)
sys.exit(1 if bad else 0)
EOF
```

期望：最后一个表达式为 `BAD []`、退出码 0。MAA 资源更新导致的差异记进台账，并按新值改 `seed.py`。

### 7.3 真机补充验证（不作为硬性门禁，结果记台账）

1. 设备停在首页，导航页点「识别当前画面」：第一次通常是 unknown 或 vision（种子节点还没有样本）。让导航员调用一次 `navigate_to("home")`（或先 `navigate_to("infrast")` 再回到首页），之后首页再点「识别当前画面」应为 `home`、`method=hash`。不要用 `POST /api/core/back_to_home` 期待 anchor：这个 v2 端点不写 nav 样本。
2. 用工单让导航员「导航到基建再回到主界面」，记录 `agent_run` 里该子运行的 `steps` 与 `prompt_tokens`。
3. 把 `nav.enabled` 关掉，重复第 2 步（导航员退回逐次点击），记录同样两个数。
4. 两组各跑 3 次取平均，台账里写出模型调用次数与 prompt token 的对比。期望开启导航图后模型调用不超过 3 次、prompt token 不超过关闭时的 40%；达不到时记录实际值与原因，不阻塞合并。
5. 用 `navigate_to("terminal")` 走一次 locate 跳转，确认导航页出现一条 `origin=learned` 的点击跳转。

## 8. 风险与回滚

| 风险 | 对策 |
|---|---|
| 首页主题或游戏改版让种子坐标失效 | 三次失败自动停用；视觉定位与学习补位；导航页可修正 |
| 感知哈希把两个相似界面认混 | 歧义判定交给视觉模型；样本上限 8 并去重；导航页可移除错误样本 |
| 只靠整屏哈希的页面（章节、活动、仓库）换主题或滚屏后漂移 | 认不出时走回主界面重试；重试失败报 `NAV_STEP_FAILED`；用户重新采样或加 ROI；T3/T4 不假设它们能稳定 hash 命中 |
| 首达信任把一次误点当成成功，写入错误样本 | 只对种子边、目标零样本、实际画面未匹配任何节点时生效，且只在 `nav.learn=true` 时；错误样本可在导航页移除；后续步骤校验失败会走错边学习并把原边记失败 |
| 学到错误跳转污染图 | 学到的跳转同样受失败停用规则约束；`nav.learn` 可关闭 |
| 导航误点到消耗类按钮 | 种子图不含开始行动之后的界面；按键只允许返回；视觉定位只用于种子里写明的入口 |
| 视觉模型调用花钱 | 只在哈希拿不准时调用；成功后学成样本与点击跳转，之后不再调用 |
| REST 导航期间与代理不互斥 | 按 M71 口径明确写入 §4.5.5；REST 只抢占不持有租约；需要互斥时由用户改用代理调用 |
| `remember_screen` 在别的 Worker 操作设备时截到过渡帧 | 该工具 `device='none'` 是用户口径；样本可在导航页移除后重拍 |

回滚：`git revert -m 1 <merge>`，再 `.venv/bin/alembic downgrade 0040_question_expiry_historical`（丢失学到的图）。

## 9. 文档同步

- [02 §2.2](../02-系统架构.md)：服务表加 `NavGraphService`（`maa_api/agent/nav/graph.py`，M86）。§4.1 的 `nav/` 一行补 `seed.py`；`services/` 布局补 `screenshot_archive.py`；§4.3 补 `scripts/nav_smoke.py`（末行 `SMOKE OK`）。§5 装配表加第 11 行 `nav_service`（M86）。
- [03 §12](../03-数据模型.md)：`nav_node.exemplars` 的说明从 `[{phash, screenshot_id}]` 改成 `[{phash, screenshot_id, source, added_at}]`，注明后两个是 JSON 字段、不新增列。§13 枚举表加 `NavNodeKind`、`NavOrigin`（M86）。
- [05 §3.3](../05-运行时与代理定义.md)：`navigator` 的工具加 `remember_screen`（M86）。§6.1 的 M86 行改成 `where_am_i`、`remember_screen`、`navigate_to` 三个工具，`remember_screen` 的 `rest_visible=false`；工具总数改 `107 个、模型可见 106 个`。§8 schema 表加 `nav_classification`、`nav_locate`。
- [14 现行接口清单](../14-现行接口清单.md)：§4 加 `### 4.22 界面导航图` 三行，总数 168→171；§6 总数 182→186，agent 小节 79→83，§6.15 追加四行。
- [15 开放API接入指南](../15-开放API接入指南.md)：由 `scripts/generate_api_guide.py` 重新生成。
- [16 部署指南](../16-部署指南.md)：加「界面导航图」一节，说明 `nav.enabled`、`nav.learn`、`nav.max_steps` 与视觉模型档案的关系；关闭 `nav.enabled` 后三个工具不可用，导航员退回逐次点击。
- 计数（今天实测基线；`13 §8.171` 的 56 张业务表是 0039 删 `spend_ledger` 之前的值，当前实际是 55 张）：

| 项 | M86 前 | M86 后 | 钉子 |
|---|---|---|---|
| 工具（总数/模型可见） | 104/103 | 107/106 | `tests/agent/test_tool_catalog.py` |
| 工具 `refetchable` 计数 | 50 | 51 | `tests/agent/test_tool_catalog.py`（T5） |
| 工具 `exclusive` 计数 | 8 | 9 | `tests/agent/test_tool_catalog.py`（T5） |
| 工具 `read` 计数 | 4 | 5 | `tests/agent/test_tool_catalog.py`（T5） |
| HTTP 端点（总数/agent） | 182/79 | 186/83 | `tests/domain/test_route_inventory.py`、14 §6 |
| 错误码（全部/HTTP 绑定/节） | 168/167/21 | 171/170/22 | `tests/domain/test_errors.py`、14 §4 |
| 业务表 | 55 | 57 | `tests/db/test_models.py` |
| 配置键（`SETTING_KEYS`/`_FIELDS`/config-only） | 139/128/13 | 142/131/13 | `tests/test_settings.py` |
| 迁移 head | 0040 | 0041 | `alembic heads` |
| `navigator` 定义版本 | 1 | 2 | `tests/api/test_agent_specs_router.py`（T8） |
| 事件 kind | 16 | 16 | 不变 |
| 评测场景 | 11 | 13 | `tests/evals/test_regression_replay.py` |

## 10. 停下来问

- §0 第 3 条不是 `0040_question_expiry_historical`：迁移链与 03 §2 的登记冲突，不要自己改号。
- §0 第 9 条找不到 `_policy_session_id`，或它不再把子代理会话映射到父 Worker 会话：导航员拿不到原子操作授权。
- §0 第 10 条没有 `preempt` 钩子：REST `navigate_to` 的抢占口径不成立。
- §0 第 6、7 条有一条不满足：M67 的 vision 角色或结构化输出入口不可用。
- §0 第 15 条不满足：`app.state.model_router`/`cost_ledger` 的装配位置变了，视觉客户端取不到依赖。
- 本机 MAA 资源里任一出处 roi 与 §4.5.6 不一致且中心坐标差值超过 0.01。
- 需要新增本文没列出的错误码、端点、配置键、事件或工具。
- 维护性补丁 `evals-ticket-driver` 未合并，或 `drive_ticket`/`HumanPolicy` 符号与 §0 #16 不符。
- 前端生成类型与四个端点的响应模型对不上，且不是重新生成能解决的。

## 11. 需要补进契约的名字

| 名字 | 种类 | 用途 | 落点 |
|---|---|---|---|
| `NavNodeKind`、`NavOrigin` | 枚举 | `nav_node.kind`、`nav_*.origin` 的取值 | 03 §13 |
| `NavGraphService` | 服务 | 图的读写、种子、最短路径、`last_seen` | 02 §2.2 |
| `app.state.nav_service` | 装配 | 工具与路由共用 | 02 §5 第 11 行 |
| `maa_api/agent/nav/seed.py` | 模块 | 种子常量 | 02 §4.1 `nav/` 行 |
| `maa_api/services/screenshot_archive.py` | 模块 | 截图归档公共函数，M89 复用 | 02 §4.1 `services/` |
| `nav_classification`、`nav_locate` | 结构化输出 schema | 视觉分类与定位；pydantic `.py` 文件 | 05 §8 |
| `where_am_i`、`remember_screen`、`navigate_to` | 工具 | 导航员三件套；`remember_screen` 的 `rest_visible=false` | 05 §3.3、05 §6.1 |
| `where_am_i` 的空参数、`remember_screen` 的 `node_id`/`title`/`kind`、`navigate_to` 的 `target`/`max_steps` | 工具参数 | 模型侧 schema | 05 §6.1 |
| `nav_node.exemplars[*].source`、`added_at` | JSON 字段 | 样本来源与时间 | 03 §12 |
| `scripts/nav_smoke.py` | 冒烟脚本 | 导航图端到端冒烟 | 02 §4.3 |
| `/more/nav`、`web/src/features/nav/` | 前端 | 导航图页；11 已有入口，本里程碑落地页面 | 11 §2、§5（已登记，不改） |
| `world.nav`、`NavWorld` | 评测设施（M86 自己） | 假导航世界 | M68 spec 的评测扩展节（测试设施，不进 02–05） |
| `ticket` 驱动、`Scenario.human`、`HumanPolicy` | 评测设施（维护性补丁 `evals-ticket-driver`） | v3 工单入口与人工答复/确认模拟 | 补丁的 spec 与台账；不进 02–05 |

计数以 §9 的表为准：工具 107/106、端点 186/83、错误码 171/170/22、业务表 57、配置键 142/131/13、迁移 head 0041、事件 16 不变、评测场景 13。05 §6.1 的工具计数在 T12 一并改成 107/106。
