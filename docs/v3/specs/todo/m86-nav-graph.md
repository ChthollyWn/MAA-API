> 状态：TODO（先搁置，不要照此实施）
> 阶段：P4。领域亮点及之后的规格先搁置，等 P0–P3（M63–M85）落地后再决定是否升为可执行规格。
> 本文件正文是提前写好的草稿，执行者不要从这里开工。

# M86 界面导航图（规格）

> 阶段：P4　依赖：M85（直接）；用到 M67 的视觉角色、M70 的导航员、M71 的设备租约　分支：`nav-graph`　迁移：`0039_nav_graph`
> 本文是 M86 的唯一规格；实施偏离记在 [13 §8](../../13-决策记录.md)。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在本分支、工作区干净 | `git status -sb` | 第一行 `## nav-graph`，没有其他行 |
| 2 | M85 已合并 | `git tag -l 'v3-m85'` | 输出 `v3-m85` |
| 3 | 迁移 head | `ls maa_api/db/migrations/versions/ \| grep -E '^[0-9]{4}_' \| sort \| tail -n 1` | `0038_channels.py`；不是它就取下一个可用号，记进台账 |
| 4 | 导航员定义存在 | `rg -n "^name: navigator$" maa_api/agent/specs/builtin/navigator.md` | 1 行 |
| 5 | 工具扩展字段（M65） | `rg -n "^\s+(device\|refetchable\|untrusted_output\|rest_visible):" maa_api/agent/registry.py` | 4 行 |
| 6 | 按角色取模型（M67） | `rg -n "def client_for" maa_api/agent/llm/roles.py` | 1 行 |
| 7 | 结构化输出入口（M67） | `rg -n "^async def \|^def " maa_api/agent/llm/structured.py` | 至少 1 行；「按 schema 名调用并本地校验」的函数名记进台账，T2 用它 |
| 8 | 租约中间件（M71） | `rg -n "class LeaseMiddleware" maa_api/agent` | 1 行 |
| 9 | 子代理沿用父 Worker 的原子操作授权（M70/M71） | `rg -n "parent_session_id" maa_api/agent/policy.py` | 至少 1 行 |
| 10 | REST 调用写设备工具时抢占租约（M71） | `rg -n "preempt" maa_api/api/routers/agent.py` | 至少 1 行 |
| 11 | 工具计数基线 | `.venv/bin/python -c "from maa_api.agent.tools import build_registry as b; r=b(); print(len(r.list()), sum(1 for d in r.list() if d.model_visible))"` | 按 05 §6.1 推算为 `104 103`；记下实际值，T5 在它上面加 2 |
| 12 | 图像库可用 | `.venv/bin/python -c "import PIL, numpy; print('ok')"` | `ok` |
| 13 | 现行接口清单 | `test -f docs/v3/14-现行接口清单.md && echo ok` | `ok` |

第 9、10 条不符合时不要自己补，按 §10 停下来问。

## 1. 背景与目标

v2 的界面导航完全靠模型逐次「截图 → 读图 → 点一下 → 再截图」。2026-10-17 的实机会话里两段点击环花了 2.79 M prompt token，占全场 48%，12 次点击 11 次打空（[v2/13 §8.80](../../../v2/13-决策记录.md)）。M86 把常用界面与它们之间的跳转存成一张图，让一次 `navigate_to` 调用在服务端完成识别、最短路径、逐步执行与校验。

做完能验证的结果：

1. `nav_node`、`nav_edge` 两张表落库；首次启动写入 15 个种子界面与 36 条种子跳转，坐标全部是归一化坐标，每个坐标都能在 MAA 资源里找到出处（§4.5.6）。
2. `where_am_i` 在已有样本的界面上 200 ms 内给出识别结果（感知哈希近邻，不调模型）；没有把握时，有视觉模型就让它在候选里选，没有就如实返回 unknown。
3. `navigate_to(target)` 一次调用走完全程：每一步执行后截图校验，走错了就从实际所在界面重新规划，认不出画面就回主界面重来；全程不超过 `nav.max_steps` 步。
4. 成功的跳转累计成功次数与耗时，失败累计失败次数，失败达到阈值自动停用；走错到别的已知界面时记一条学到的跳转；导航员手动找到新界面后能用 `where_am_i(remember_as=…)` 记下，下次一步到。
5. `navigate_to` 走原子操作的会话授权，持有设备租约；REST 调用逐次确认。
6. 前端「界面导航图」页能看界面与跳转、标记当前画面、启停与删除跳转、新建跳转。
7. 评测：同一段导航，用 `navigate_to` 的模型调用次数不超过 3 次；真机补充测量记录 prompt token 对比（§7.3）。

## 2. 非目标

- 不用 MaaCore 的模板匹配做逐步识别（可行性结论见 §4.5.1）。
- 不做模板图裁剪，不往 custom 层写图片。
- 不做多分辨率适配：只支持 16:9 设备（与 `DeviceManager.click` 的换算前提一致）。
- 不自动发现全部关卡：关卡页节点只在导航员手动找到后学习，或由用户在导航页新建。
- 不删除界面节点（没有端点）；错误的学习结果通过清空样本、停用或删除跳转修正。
- 导航不点任何会消耗资源的按钮：种子图里没有「开始行动」之后的界面，按键动作只允许 `BACK`。

## 3. 相关文档

- [02 §4.1](../../02-系统架构.md)（`nav/` 模块布局）、[03 §12](../../03-数据模型.md)（两张表）、[04 §2.5、§5、§6](../../04-接口与事件协议.md)（端点、`NAV_*` 错误码、`nav.*` 配置）、[05 §3.3、§6](../../05-运行时与代理定义.md)（导航员工具、`device` 字段）。
- v2 实测：`rg -n "^### 8.80" docs/v2/13-决策记录.md`（逐次点击的 token 账）；`rg -n "坐标|原始像素" docs/v2/13-决策记录.md | head`（M18 起点击坐标是设备像素）。
- v2 知识条目：`maa_api/agent/knowledge/builtin/screen-home.md`、`screen-terminal.md`（「没有出处的按钮一律写需截图确认」）。
- 既有代码入口：`maa_api/agent/tools/raw.py` 的 `_run_atomic`（约 108 行，`operation_lock` + 流水线空闲检查）、`_click`（约 140）、`CLICK_SETTLE_SECONDS`（约 19）；`maa_api/services/device_service.py` 的 `DeviceManager.click`（约 765，设备像素换算到 720 基准）、`swipe`（约 740）、`key_event`（约 825）、`back_to_home`（约 790，返回内核 `AsstBackToHome` 的布尔值）、`screenshot`、`snapshot()["resolution"]`；`maa_api/agent/policy.py` 的 `ATOMIC_TOOL_NAMES`（约 35）、`_evaluate_atomic`（约 376）；`maa_api/agent/tools/status.py` 的 `get_screenshot`（约 188）；`maa_api/util/image.py` 的 `store_screenshot`（约 243）；`maa_api/db/repositories/log.py` 的 `ScreenshotRepository`（约 308）；`maa_api/services/copilot/result.py` 的模块说明（M29 用 custom 层识别任务判结算）。
- MAA 资源（种子坐标出处，不进仓库）：`resource/lib/maa/Darwin/resource/tasks/tasks.json`、`tasks/UiTheme/*.json`。

## 4. 设计

### 4.1 数据模型

枚举加进 `maa_api/domain/enums.py`，并在 `tests/domain/test_enums.py` 整表钉死：

```python
class NavNodeKind(StrEnum):
    SCREEN = "screen"; STAGE = "stage"; MENU = "menu"; DIALOG = "dialog"

class NavOrigin(StrEnum):
    SEED = "seed"; LEARNED = "learned"; USER = "user"
```

（代码里按仓库风格每个成员一行，这里为省篇幅写在一行。）

`maa_api/db/models.py` 末尾追加（03 §12 的列，一列不多）：

```python
# ---------------------------------------------------------------------------
# M86 nav_node / nav_edge：界面导航图（v3 03 §12）
# ---------------------------------------------------------------------------
class NavNode(SQLModel, table=True):
    """一个可识别的游戏界面。

    ``exemplars`` 每项 ``{phash, screenshot_id, source, added_at}``：``phash`` 是按
    ``recognizer["rois"]`` 顺序拼接的 dHash 十六进制串，改了 rois 就要用截图重算。
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

迁移 `maa_api/db/migrations/versions/0039_nav_graph.py`（`revision = "0039_nav_graph"`，`down_revision = "0038_channels"`，按 §0 第 3 条调整）：

```python
def upgrade() -> None:
    S, J, I, D = sa.String, sa.JSON, sa.Integer, sa.DateTime
    op.create_table(
        "nav_node",
        sa.Column("id", S(48), nullable=False), sa.Column("title", S(64), nullable=False),
        sa.Column("kind", S(24), nullable=False),
        sa.Column("exemplars", J(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("recognizer", J(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("origin", S(16), nullable=False),
        sa.Column("created_at", D(), nullable=False), sa.Column("updated_at", D(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_nav_node"),
    )
    op.create_table(
        "nav_edge",
        sa.Column("id", S(36), nullable=False),
        sa.Column("from_node", S(48), nullable=False), sa.Column("to_node", S(48), nullable=False),
        sa.Column("action", J(), nullable=False), sa.Column("action_hash", S(64), nullable=False),
        sa.Column("cost_ms", I(), nullable=True),
        sa.Column("success_count", I(), nullable=False), sa.Column("fail_count", I(), nullable=False),
        sa.Column("last_used_at", D(), nullable=True), sa.Column("origin", S(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", D(), nullable=False), sa.Column("updated_at", D(), nullable=False),
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

（`S(48)` 即 `sa.String(length=48)`；实际文件照既有迁移写全称。）

迁移不写种子数据：种子由 `NavGraphService.ensure_seed()` 在启动时写入（§4.5.6），只在 `nav_node` 为空时写。降级丢掉学到的图，属于运行时数据，可接受。

### 4.2 模块与文件

| 文件 | 新建/修改 | 职责 |
|---|---|---|
| `maa_api/agent/nav/__init__.py` | 新建 | 导出 `NavGraphService`、`Recognizer`、`Navigator` |
| `maa_api/agent/nav/graph.py` | 新建 | 动作模型、`action_hash`、图的读写、最短路径、成败记账 |
| `maa_api/agent/nav/seed.py` | 新建 | `SEED_NODES`、`SEED_EDGES` 两个常量 |
| `maa_api/agent/nav/recognizer.py` | 新建 | dHash、样本近邻、视觉分类与定位 |
| `maa_api/agent/nav/navigator.py` | 新建 | `NavDevice` 协议与真实设备适配、导航执行、从审计学习 |
| `maa_api/services/screenshot_archive.py` | 新建 | 截图归档的公共函数（M89 复用） |
| `maa_api/agent/llm/schemas/nav_classification.json`、`nav_locate.json` | 新建 | 两个结构化输出 schema |
| `maa_api/agent/tools/nav.py` | 新建 | `where_am_i`、`navigate_to` |
| `maa_api/agent/tools/__init__.py` | 改 | 注册 nav 工具（放在 `register_raw_tools` 之后） |
| `maa_api/agent/tools/raw.py` | 改 | `_run_atomic` 改名 `run_atomic`（同文件调用一并改），供导航复用 |
| `maa_api/agent/policy.py` | 改 | `ATOMIC_TOOL_NAMES` 加 `navigate_to` |
| `maa_api/api/routers/nav.py`、`maa_api/api/schemas/agent_v3.py` | 新建/改 | 四个端点与响应模型 |
| `maa_api/main.py` | 改 | 挂 `app.state.nav_service`、`ensure_seed()`、`include_router` |
| `maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml` | 改 | `nav.*` 三个键 |
| `maa_api/agent/specs/builtin/navigator.md`、`general.md` | 改 | 工具与提示词（§4.7） |
| `web/src/features/nav/*`、`web/src/routes/router.tsx`、`web/src/components/layout/nav.ts` | 新建/改 | 导航图页 |

`graph.py` 的签名：

```python
NODE_ID_PATTERN = re.compile(r"^(?:[a-z][a-z0-9_]{1,47}|stage:[A-Za-z0-9][A-Za-z0-9-]{0,41})$")
ALLOWED_KEYS = frozenset({"BACK"})
MAX_SEQUENCE_STEPS = 8
DISABLE_AFTER_FAILS = 3          # fail_count ≥ 3 且 fail_count ≥ success_count 时停用
FAIL_PENALTY_MS = 2000
DEFAULT_COST_MS = {"tap": 1500, "swipe": 1500, "key": 1500, "locate": 6500}
HOME_EDGE_COST_MS = 6000         # 隐式「回主界面」边，不落库

Ratio = Annotated[float, Field(ge=0, le=1)]
WaitMs = Annotated[int, Field(ge=0, le=10000)]

class TapAction(BaseModel):      # 五个动作模型都 extra="forbid"
    type: Literal["tap"]; x: Ratio; y: Ratio; wait_ms: WaitMs = 1200

class SwipeAction(BaseModel):
    type: Literal["swipe"]; x1: Ratio; y1: Ratio; x2: Ratio; y2: Ratio
    duration_ms: int = Field(default=400, ge=100, le=3000); wait_ms: WaitMs = 1000

class KeyAction(BaseModel):
    type: Literal["key"]; key: Literal["BACK"]; wait_ms: WaitMs = 1200

class LocateAction(BaseModel):   # query 是给视觉模型的中文描述
    type: Literal["locate"]; query: str = Field(min_length=2, max_length=120); wait_ms: WaitMs = 1200

class SequenceAction(BaseModel):
    type: Literal["sequence"]
    steps: list[TapAction | SwipeAction | KeyAction] = Field(min_length=1, max_length=8)

NavAction = Annotated[TapAction | SwipeAction | KeyAction | LocateAction | SequenceAction,
                      Field(discriminator="type")]

def action_hash(action: NavAction) -> str:
    """规范 JSON（键排序、坐标保留 3 位小数、去掉 wait_ms）的 sha256 十六进制。"""

@dataclass(frozen=True, slots=True)
class PathStep:                    # edge_id 为 None 表示隐式回主界面边，action 是 {"type": "home"}
    edge_id: str | None; from_node: str; to_node: str; action: dict[str, Any]

class NavGraphService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None: ...
    async def ensure_seed(self) -> int: """nav_node 为空时写入种子，返回写入的节点数。"""
    async def graph(self) -> tuple[list[NavNode], list[NavEdge]]: ...
    async def get_node(self, node_id: str) -> NavNode | None: ...
    async def upsert_node(self, node_id: str, *, title: str, kind: str, origin: str,
                          recognizer: Mapping[str, Any] | None = None) -> NavNode: ...
    async def add_exemplar(self, node_id: str, *, phash: str, screenshot_id: str | None,
                           source: str) -> bool: """去重与上限见 §4.5.2；加了返回 True。"""
    async def set_exemplars(self, node_id: str, exemplars: list[dict[str, Any]]) -> NavNode: ...
    async def upsert_edge(self, *, from_node: str, to_node: str, action: Mapping[str, Any],
                          origin: str, edge_id: str | None = None) -> NavEdge: ...
    async def update_edge(self, edge_id: str, *, enabled: bool | None = None,
                          action: Mapping[str, Any] | None = None,
                          cost_ms: int | None = None) -> NavEdge: ...
    async def delete_edge(self, edge_id: str) -> None: ...
    async def record_success(self, edge_id: str, *, elapsed_ms: int) -> None: ...
    async def record_failure(self, edge_id: str) -> bool: """返回这次是否触发了停用。"""
    async def shortest_path(self, source: str, target: str, *, vision: bool,
                            exclude: set[str] = frozenset()) -> list[PathStep] | None: ...
```

`recognizer.py` 的签名：

```python
HASH_SIZE = 8
DEFAULT_ROIS = ((0.0, 0.0, 1.0, 1.0),)
DEFAULT_MAX_DISTANCE = 10          # 每个 ROI 的汉明距离上限
AMBIGUITY_MARGIN = 2.0             # 第一名与另一个节点的平均距离差小于它就算拿不准
DUPLICATE_DISTANCE = 3             # 每个 ROI 都不超过它就算重复样本
MAX_EXEMPLARS = 8
VISION_MIN_CONFIDENCE = 0.7
LOCATE_MIN_CONFIDENCE = 0.6
VISION_CANDIDATES = 8

def dhash(image: Image.Image, roi: Sequence[float]) -> int: ...
def signature(image: Image.Image, rois: Sequence[Sequence[float]]) -> str: ...
def distances(sig_a: str, sig_b: str) -> list[int] | None: """ROI 数不同返回 None。"""

@dataclass(frozen=True, slots=True)
class Recognition:   # distance 是平均汉明距离；candidates 是 (node_id, 平均距离)，至多 3 个；
                     # signatures 是 node_id → 本图按该节点 rois 算的签名，学样本时用
    node_id: str | None; method: Literal["hash", "vision", "anchor", "none"]
    distance: float | None; confidence: float
    candidates: tuple[tuple[str, float], ...]; signatures: Mapping[str, str]

class VisionClient(Protocol):
    def available(self) -> bool: ...
    async def classify(self, image: Image.Image, candidates: Sequence[NavNode]) -> tuple[str | None, float, str] | None: ...
    async def locate(self, image: Image.Image, query: str) -> tuple[float, float, float] | None: ...

class ModelVisionClient:
    """用 M67 的 vision 角色档案；档案 supports_vision 不为 True 时 available() 为 False。"""
    def __init__(self, app_state: Any) -> None: ...

class Recognizer:
    def __init__(self, graph: NavGraphService, vision: VisionClient) -> None: ...
    async def recognize(self, image: Image.Image, *, allow_vision: bool = True) -> Recognition: ...
```

`navigator.py` 的签名：

```python
UNKNOWN_RETRY_MS = 1500
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
    """每个动作都包在 raw.run_atomic(context, "navigate_to", …) 里。"""
    def __init__(self, context: ToolContext) -> None: ...

@dataclass(slots=True)
class NavResult:
    arrived: bool; node_id: str; steps: int; elapsed_ms: int
    trail: list[dict[str, Any]]; learned_edges: int; learned_exemplars: int

class Navigator:
    def __init__(self, graph: NavGraphService, recognizer: Recognizer, device: NavDevice,
                 *, learn: bool, max_steps: int) -> None: ...
    async def where_am_i(self) -> tuple[Recognition, Image.Image]: ...
    async def navigate(self, target: str) -> NavResult: ...
    async def remember(self, node_id: str, *, title: str | None, kind: str | None,
                       image: Image.Image, recognition: Recognition,
                       session_id: str | None) -> dict[str, Any]: ...

class LastSeenStore:
    """进程内：session_id → (node_id, audit_cursor)。重启后丢失，只影响一次学习。"""
    def put(self, session_id: str, node_id: str, audit_cursor: int) -> None: ...
    def get(self, session_id: str) -> tuple[str, int] | None: ...
```

`maa_api/services/screenshot_archive.py`：

```python
async def archive_screenshot(session: AsyncSession, image: Image.Image, *,
                             trigger: ScreenshotTrigger, backend: ScreenshotBackend) -> tuple[Screenshot, dict[str, Any]]:
    """store_screenshot 落文件 + ScreenshotRepository.create 建行；不 commit，调用方提交。"""
```

### 4.3 接口

#### 4.3.1 工具

| 工具 | 组 | 风险 | device | read_only | refetchable | rest_visible |
|---|---|---|---|---|---|---|
| `where_am_i` | `nav` | SAFE | `read` | True | True | True |
| `navigate_to` | `nav` | DANGEROUS（策略按原子操作处理，§4.5.5） | `exclusive` | False | False | True |

参数（`extra="forbid"`）：

```python
class WhereAmIParams(BaseModel):
    remember_as: str | None = Field(default=None, max_length=48)   # 满足 NODE_ID_PATTERN
    title: str | None = Field(default=None, min_length=1, max_length=64)
    kind: Literal["screen", "stage", "menu", "dialog"] | None = None

class NavigateToParams(BaseModel):
    target: str = Field(min_length=2, max_length=48)
    max_steps: int | None = Field(default=None, ge=1, le=50)    # 缺省取 nav.max_steps
```

`where_am_i` 返回：

```json
{"node": {"id": "home", "title": "主界面", "kind": "screen"}, "method": "hash", "distance": 3.5,
 "confidence": 0.78, "candidates": [{"id": "home", "title": "主界面", "distance": 3.5},
 {"id": "terminal", "title": "终端", "distance": 21.0}], "screenshot_id": null, "remembered": null}
```

`confidence` 的算法：hash 判定为 `1 - distance / (2 × max_distance)`，夹到 0–1；视觉判定取模型给的值；none 为 0。`node` 为 null 时 `screenshot_id` 必有值（归档了这张认不出的图）。`remembered` 在用了 `remember_as` 时为 `{"node_id", "created": bool, "exemplar_added": bool, "edge_id": str | null, "skipped_reason": str | null}`。

`navigate_to` 返回：

```json
{"arrived": true, "node": {"id": "infrast", "title": "基建", "kind": "screen"}, "steps": 1, "elapsed_ms": 2140,
 "trail": [{"edge_id": "3f…", "action": "tap", "from": "home", "expected": "infrast", "actual": "infrast",
            "ok": true, "ms": 2140}], "learned": {"edges": 0, "exemplars": 0}}
```

失败用 AppError：

| 情形 | 码 | details |
|---|---|---|
| 目标不在图里 | `NAV_NODE_NOT_FOUND` | `{target, nearest}`；`nearest` 见 §4.5.4 |
| 当前到目标没有可走的路径 | `NAV_PATH_NOT_FOUND` | `{from, to, reason}`，`reason` 取 `no_edges`、`needs_vision` |
| 走到一半画面不符且无法恢复 | `NAV_STEP_FAILED` | `{reason, current, trail, screenshot_id}`，`reason` 取 `max_steps`、`unknown_screen`、`home_failed` |
| 导航图关闭 | `SERVICE_UNAVAILABLE`（v2） | `{setting: "nav.enabled"}` |
| `remember_as` 不合法、学习关闭时用了 `remember_as` | `INVALID_PARAMETER`（v2） | `{field}` |

#### 4.3.2 REST（tag `agent`，路由文件 `maa_api/api/routers/nav.py`，前缀 `/api/agent/nav`）

| 方法与路径 | 函数名 | 请求 | 响应与状态码 |
|---|---|---|---|
| GET `/api/agent/nav/graph` | `get_nav_graph` | — | 200 `NavGraphOut` |
| PUT `/api/agent/nav/nodes/{node_id}` | `put_nav_node` | `NavNodePut` | 200 `NavNodeOut`（新建与修改都是 200）；400 `INVALID_PARAMETER`；404 `SCREENSHOT_NOT_FOUND`；`add_exemplar='current'` 而设备未连接时返回 v2 的 `DEVICE_NOT_CONNECTED`（状态码沿用 v2 绑定） |
| PUT `/api/agent/nav/edges/{edge_id}` | `update_nav_edge` | `NavEdgePut` | 200 `NavEdgeOut`；不存在且没给 `from_node`/`to_node`/`action` 时 404 `NOT_FOUND`；端点不存在 404 `NAV_NODE_NOT_FOUND`；400 `INVALID_PARAMETER` |
| DELETE `/api/agent/nav/edges/{edge_id}` | `delete_nav_edge` | — | 204；404 `NOT_FOUND` |

响应与请求模型放进 `maa_api/api/schemas/agent_v3.py`：

```python
class NavExemplarOut(BaseModel):
    phash: str; screenshot_id: str | None; source: str; added_at: str | None
    thumb_url: str | None                    # /api/images/{sha256}/thumb，查不到截图时为 None

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

- `put_nav_node`：新建时 `origin='user'`；改已有节点不改 `origin`。`recognizer.rois` 变了，按每个样本的 `screenshot_id` 重算 `phash`，截图文件丢失的样本删掉。`add_exemplar='current'` 现截一张图（只读设备，不取租约），`source='user'`；`add_exemplar_screenshot_id` 用已归档截图；两者都给时 400。`remove_exemplars` 按 `phash` 删除。节点 id 不满足 `NODE_ID_PATTERN` 时 400。
- `update_nav_edge`：不存在时按 `from_node`、`to_node`、`action` 新建（`origin='user'`，id 用路径里的值，必须是 UUID 形状）；存在时只改给了的字段。`enabled` 从 false 改成 true 时 `fail_count` 清零。改了 `action` 重算 `action_hash`，与同一对界面的另一条撞了时 400。
- `delete_nav_edge`：物理删除。种子跳转删掉后不会自动写回，因为种子只在空表时写入。
- 时间字段输出 ISO 8601 带 `Z`。

### 4.4 配置项

| 键 | 类型 | 默认 | 范围 | 分组 | config-only | `_FIELDS` |
|---|---|---|---|---|---|---|
| `nav.enabled` | bool | true | | Agent | 否 | `dict(label="界面导航图", group="Agent", type="boolean", hot_action="none")` |
| `nav.learn` | bool | true | | Agent | 否 | `dict(label="导航时学习新跳转", group="Agent", type="boolean", hot_action="none")` |
| `nav.max_steps` | int | 12 | 1–50 | Agent | 否 | `dict(label="一次导航最多步数", group="Agent", type="integer", minimum=1, maximum=50, hot_action="none")` |

`settings.py`：新增 `class NavSettings(BaseModel)`（`model_config = ConfigDict(extra="ignore")`；`enabled: bool = True`、`learn: bool = True`、`max_steps: int = Field(default=12, ge=1, le=50)`），`Settings` 加 `nav: NavSettings = Field(default_factory=NavSettings)`；`SETTING_KEYS` 加三行 `"nav.enabled": ("nav", "enabled")` 等。环境变量 `MAA_NAV_ENABLED`、`MAA_NAV_LEARN`、`MAA_NAV_MAX_STEPS` 进 `tests/test_settings.py` 的 ENV_OVERRIDES。

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

不用它的理由：① 每次识别都是一条流水线，入队、领取、内核跑链，两条链之间至少隔 1.5 s（v2/13 §8.53 W1 实测），单次 2–4 s，感知哈希在本进程算只要几十毫秒；② 读数不进 `log_entry`，只能靠进程内回调或 `@Hit` 命中标记绕出来（v2/13 §8.51 二、4）；③ 每次识别都在流水线历史里留一条记录，历史页会被导航刷屏；④ 识别期间设备被流水线占着，原子操作要等它结束（`_run_atomic` 的空闲检查）；⑤ 只对内核已有模板的界面有效，新活动界面没有模板。

本里程碑只在一处借用内核的识别：隐式「回主界面」边调 `DeviceManager.back_to_home()`，内核返回 True 就认定到了主界面，并把这张图记成 `home` 的样本（`source='anchor'`）。

#### 4.5.2 感知哈希识别

- 实现：dHash，不新增依赖。Pillow 已在用（`maa_api/util/image.py`），imagehash 会多带 PyWavelets，没有必要。
- `dhash(image, roi)`：`roi` 是归一化 `[x, y, w, h]`；裁剪框 `(round(x·W), round(y·H), round((x+w)·W), round((y+h)·H))`；`convert("L").resize((9, 8), Image.Resampling.BILINEAR)`；逐行比较相邻两列，左大于右记 1，得 64 位整数。
- `signature`：各 ROI 的 `f"{bits:016x}"` 用 `:` 连接。`distances`：逐段异或后 `bit_count()`。
- 节点匹配：节点的每个样本与本图按该节点 `rois` 的签名比距离，每个 ROI 都不超过 `max_distance` 才算这个样本匹配；节点得分取匹配样本里平均距离最小的那个。
- 判定顺序：
  1. 没有任何节点匹配 → 进视觉分类（第 4 步）。
  2. 取得分最小的节点 A。若另有节点 B 也匹配且 `B.score - A.score < AMBIGUITY_MARGIN` → 拿不准，进视觉分类；例外：A 与 B 一个 `recognizer.generic=true`、另一个不是，取非 generic 的那个，不算拿不准。
  3. 否则返回 A，`method='hash'`。
  4. 视觉分类：`allow_vision` 且 `vision.available()` 时，候选取得分最小的 `VISION_CANDIDATES` 个节点（不足就全取），再保证含 `home`；模型返回的 `node_id` 在候选里且置信度 ≥ 0.7 才采纳，`method='vision'`；否则 `method='none'`、`node_id=None`。
- 学样本（`nav.learn=true` 时）：视觉判定采纳、`back_to_home` 返回 True、`navigate_to` 校验通过三种情况，把本图在该节点 `rois` 下的签名加进样本。去重：与已有样本每个 ROI 距离都 ≤ 3 就不加。上限 8 个：超出时先删最早的非 `user` 样本，全是 `user` 时删最早的那个。加样本时才归档截图（`trigger=agent`）。

视觉分类与定位用 M67 的结构化输出入口（§0 第 7 条记下的函数），角色 `vision`，消息是一张截图（缩到 1280×720 的 JPEG data URL）加文字。两个 schema：

```json
// maa_api/agent/llm/schemas/nav_classification.json
{"type": "object", "additionalProperties": false, "required": ["node_id", "confidence", "reason"],
 "properties": {"node_id": {"type": ["string", "null"], "maxLength": 48},
   "confidence": {"type": "number", "minimum": 0, "maximum": 1}, "reason": {"type": "string", "maxLength": 200}}}
// maa_api/agent/llm/schemas/nav_locate.json
{"type": "object", "additionalProperties": false, "required": ["found", "x", "y", "confidence"],
 "properties": {"found": {"type": "boolean"}, "x": {"type": "number", "minimum": 0, "maximum": 1},
   "y": {"type": "number", "minimum": 0, "maximum": 1}, "confidence": {"type": "number", "minimum": 0, "maximum": 1}}}
```

分类提示词（写成 `recognizer.py` 里的常量 `CLASSIFY_PROMPT`）：

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
- 隐式边：除 `home` 外的每个节点都有一条到 `home` 的边，`edge_id=None`、`action={"type":"home"}`、代价 6000。
- 目标就是当前节点时返回空列表。找不到返回 None；调用方据此报 `NAV_PATH_NOT_FOUND`，`reason`：因为 `vision=false` 跳过了 locate 边才不通时为 `needs_vision`，否则 `no_edges`。

#### 4.5.4 导航执行

```
navigate(target):
    if target not in graph: raise NAV_NODE_NOT_FOUND(target, nearest=nearest_container(target))
    steps = 0; failed_edges = set(); trail = []
    rec, image = where_am_i()
    current = rec.node_id
    if current is None:
        current = recover_home()                 # steps += 1；失败 → NAV_STEP_FAILED(home_failed)
    while current != target:
        path = graph.shortest_path(current, target, vision=vision.available(), exclude=failed_edges)
        if path is None: raise NAV_PATH_NOT_FOUND(current, target, reason)
        step = path[0]
        if steps >= max_steps: raise NAV_STEP_FAILED(max_steps)
        t0 = now(); perform(step); steps += 1
        rec, image = where_am_i()
        if rec.node_id is None:                  # 可能在加载
            sleep(UNKNOWN_RETRY_MS); rec, image = where_am_i()
        trail.append(...)
        if rec.node_id == step.to_node:
            if step.edge_id: graph.record_success(step.edge_id, elapsed_ms=now()-t0)
            learn_exemplar(step.to_node, image, source="edge")
            if step.action.type == "locate" and learn: graph.upsert_edge(tap 到同一对界面, origin="learned")
            current = step.to_node; continue
        if step.edge_id:
            graph.record_failure(step.edge_id); failed_edges.add(step.edge_id)
        if rec.node_id is not None:
            if learn and rec.node_id != current and step.action.type in {"tap","swipe","key","sequence"}:
                graph.upsert_edge(current → rec.node_id, 同一动作, origin="learned")
            current = rec.node_id; continue
        if steps >= max_steps: raise NAV_STEP_FAILED(unknown_screen, screenshot_id=归档这张图)
        current = recover_home(); steps += 1
    return NavResult(arrived=True, ...)
```

- `perform`：`tap` 按设备分辨率换算 `px = round(x·W)`、`py = round(y·H)` 后调 `NavDevice.tap`，再等 `wait_ms`；`swipe` 同理；`key` 只发 `BACK`；`sequence` 依次执行各步，中间不识别；`locate` 先截图、调 `vision.locate`，置信度 < 0.6 或 `found=false` 记一次失败并 `exclude`，否则点那个坐标；`home` 调 `NavDevice.home()`，返回 True 时把截图记成 `home` 的样本（`source='anchor'`）。
- `recover_home`：执行隐式回主界面边；`home()` 返回 False 时抛 `NAV_STEP_FAILED(reason='home_failed')`。
- `nearest_container(target)`：`stage:` 后面的代号匹配 `^\d{1,2}-\d{1,2}$`（主线，如 `1-7`）→ `main_episode`；其他 `stage:*` → `activity_hub`；不是 `stage:` 开头 → `home`。
- 识别失败的截图才归档；成功只在加样本时归档。
- 每个设备动作都在 `raw.run_atomic` 里执行：拿 `operation_lock`、检查没有流水线在跑；有流水线在跑时原样抛 `PIPELINE_ALREADY_RUNNING`。

记账规则（`record_failure`）：`fail_count += 1`；`fail_count ≥ 3 且 fail_count ≥ success_count` 时 `enabled=false`。`record_success`：`success_count += 1`、`last_used_at=now`、`cost_ms` 为空时取本次耗时，否则 `round(0.7·旧值 + 0.3·本次)`。学到的跳转已存在（同 `action_hash`）时只记一次成功，不新建。

#### 4.5.5 授权、租约与 `remember_as`

- 策略：`policy.py` 的 `ATOMIC_TOOL_NAMES` 加 `"navigate_to"`。内置调用方在会话（子代理按 §0 第 9 条的口径沿用父 Worker 会话）有有效原子操作授权时直接放行，没有就弹 `grant_atomic_ops`；REST 调用逐次确认（`_atomic_confirmation`）。`where_am_i` 是 SAFE，不经确认。
- 租约：`navigate_to` 的 `device='exclusive'`，由 `LeaseMiddleware` 在确认通过后、执行前取租约；REST 调用沿用 M71 对写设备工具的抢占规则。`where_am_i` 的 `device='read'` 不取租约。
- 一次 `navigate_to` 只需要一次授权判定；内部的多个点击不再逐个经过策略层，审计只有 `navigate_to` 这一行，`trail` 写进它的结果。
- `LastSeenStore`：`where_am_i` 与 `navigate_to` 认出界面后，记 `(session_id → node_id, 当时该会话 agent_audit 的最大 id)`。
- `where_am_i(remember_as=X)`（需要 `nav.learn=true`）：
  1. X 不满足 `NODE_ID_PATTERN` → 400。
  2. 本图识别结果是另一个节点 Y（`method='hash'` 且 Y ≠ X）→ 不写，`remembered.skipped_reason="screen_is_<Y>"`。
  3. X 不存在：新建节点（`origin='learned'`，`title` 必填，否则 400；`kind` 缺省时 `stage:` 开头为 `stage`，其余为 `screen`），本图签名作为第一个样本（`source='agent'`）。X 已存在：只加样本。
  4. 连边：从 `LastSeenStore` 取本会话上一次认出的节点 P 与审计游标 c；查 `agent_audit` 里 `session_id=本会话 AND id > c AND tool_name IN ('click','swipe','key_event') AND status='success'`，按 id 排序。0 条、超过 8 条、含 `long_press` 或 `input_text`、或 P 就是 X 时不连边；否则把它们换成归一化动作（像素除以当前分辨率，`key_event` 只收 `BACK`，其余按键不连边），1 条时存成单个动作，多条存成 `sequence`，`upsert_edge(P → X, origin='learned')`。
  5. 记 `LastSeenStore(session → X)`。

#### 4.5.6 种子图

坐标出处都在 MAA 资源（1280×720 基准，取 roi 中心再除以 1280、720，保留 4 位小数）：`UiTheme/*.json` 的 `*Default` 首页入口、`UiTheme/QuickSwitch.json` 的快捷菜单、`tasks.json` 的 `ReturnButton`、`EnterEpisodeNew-Click`、`Mail`。没有出处的按钮写成 `locate` 跳转，第一次走时由视觉模型定位，走通后学成 `tap` 跳转。

界面（`origin='seed'`，`recognizer.max_distance=10`）：

| id | title | kind | rois（除整屏 `[0,0,1,1]` 外的那个） | 出处 | 特征描述 |
|---|---|---|---|---|---|
| `home` | 主界面 | screen | `[0.1773,0.4542,0.1242,0.2111]` | `Home` 模板 roi | 游戏首页，右侧是终端、采购中心、公开招募、干员寻访、基建、仓库等入口 |
| `terminal` | 终端 | screen | `[0.3609,0.8333,0.1094,0.1667]` | `GoTerminalStopflag` roi | 作战终端，底部是关卡分类栏 |
| `main_theme` | 主题曲 | screen | — | | 主题曲页，右上角有「乐章收录」，下方有「前往章节」 |
| `main_episode` | 主线章节 | screen | — | | 某一章主线的关卡地图，关卡节点横向排开 |
| `activity_hub` | 当期活动 | screen | — | | 当期活动的主页或关卡地图 |
| `stage_start` | 关卡开始页 | stage | 只用 `[0.7891,0.8681,0.2031,0.0847]`，`generic=true` | `StartButton1` roi | 某个关卡的出战确认页，右下角有「开始行动」 |
| `infrast` | 基建 | screen | `[0.0000,0.1389,0.2344,0.2083]` | `InfrastEnteredFlag` roi | 基建总览 |
| `recruit` | 公开招募 | screen | `[0.0391,0.1389,0.1797,0.1389]` | `RecruitFlag` roi | 公开招募的四个招募位 |
| `store` | 采购中心 | screen | `[0.7813,0.0000,0.2188,0.4167]` | `CreditStoreOcr` roi | 采购中心，右上有信用交易所等分区 |
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
| terminal、depot、store、recruit、operator_list、main_theme、main_episode、activity_hub、stage_start（9 条） | quick_menu | tap 0.2102, 0.0931 | `QuickSwitch@*@Open` `[197,0,144,134]` |
| quick_menu | home | tap 0.0727, 0.3896 | `QuickSwitch@ToHome@Entry` `[11,206,164,149]` |
| quick_menu | terminal | tap 0.4137, 0.1028 | `QuickSwitch@ToTerminal@Entry` `[450,0,159,148]` |
| quick_menu | operator_list | tap 0.2402, 0.3903 | `QuickSwitch@ToOperBox@Entry` `[226,204,163,154]` |
| quick_menu | infrast | tap 0.5352, 0.1021 | `QuickSwitch@ToInfrast@Entry` `[610,0,150,147]` |

共 36 条。关卡页（`stage:<代号>`）不进种子：关卡在章节地图里的位置随滚动变化，MAA 自己也是靠 OCR 加滑动找关卡，固定坐标没有出处。

种子以首页默认主题为准；用户换了首页主题，首页入口的坐标可能失效，三次失败后自动停用，由视觉定位或用户在导航页修正。

#### 4.5.7 边界情况

| 输入 | 期望行为 |
|---|---|
| 设备未连接时调用 `where_am_i` | `DEVICE_NOT_CONNECTED`（截图那一步抛出） |
| `nav.enabled=false` | 两个工具都抛 `SERVICE_UNAVAILABLE`；REST 端点照常读写 |
| 当前画面认不出、没有视觉模型 | 先执行回主界面，再规划；回主界面失败报 `home_failed` |
| 走某条跳转后到了另一个已知界面 | 这条跳转记失败、本次不再用；学一条「当前 → 实际界面」的跳转，从实际界面重新规划 |
| 导航中途有流水线开始运行 | 原样抛 `PIPELINE_ALREADY_RUNNING`，不算跳转失败 |
| 设备被用户接管 | M71 让下一次写设备的动作拿到 `DEVICE_LEASE_PREEMPTED`，原样抛出 |
| 学样本时截图归档失败 | 只记 warning，不影响导航结果 |
| PUT 节点改了 rois，有样本的截图文件已被清理 | 那个样本删掉，其余重算 |

### 4.6 前端

- 路由：`/more/nav`（`web/src/routes/router.tsx` 加 `{ path: 'more/nav', element: page(<NavGraphPage />) }`，`lazy(() => import('@/features/nav/NavGraphPage'))`）；`secondaryNav` 加 `{ to: '/more/nav', label: '界面导航图', icon: Route, description: '常用界面与跳转，导航员按它一步走到' }`（`Route` 来自 `lucide-react`）。
- 文件：`web/src/features/nav/NavGraphPage.tsx`、`NodeList.tsx`、`NodeEditor.tsx`、`EdgeTable.tsx`、`EdgeEditor.tsx`、`api.ts`、`NavGraphPage.test.tsx`。
- 请求与 query key：`['api', 'agent', 'nav', 'graph']` 取 `GET /api/agent/nav/graph`；三个写操作成功后 invalidate 这个 key。「识别当前画面」调 `POST /api/agent/tools/where_am_i/invoke`（`{arguments: {}}`），结果只在页面上显示，不写缓存。
- 页面结构：
  - 顶部 `Section title="界面导航图"`，`description` 显示「{n} 个界面 · {m} 条跳转」，`hint`：「导航员按这张图从当前界面一步走到目标。跳转失败三次会自动停用，可以在这里重新启用或删掉。」；`actions` 放按钮「识别当前画面」。视觉模型不可用时在 hint 末尾补一句「当前没有可用的视觉模型，标着「按描述定位」的跳转不会被使用。」
  - 「界面」区：卡片列表，每张显示标题、id、类型、来源（种子、学到的、手动）与样本缩略图（`thumb_url`）。点开进 `NodeEditor`：改标题与类型、编辑特征描述、按钮「标记当前画面」（PUT `add_exemplar: 'current'`）、每个样本旁的「移除」；高级区（折叠）里用 JSON 文本框编辑 `rois` 与 `max_distance`。按钮「新建界面」打开同一个编辑器，id 输入框校验 `NODE_ID_PATTERN`。
  - 「跳转」区：表格列「从」「到」「动作」「成功/失败」「耗时」「状态」；动作列把 `tap` 写成「点击 (x, y)」、`locate` 写成「按描述定位」、`sequence` 写成「{n} 步操作」、`key` 写成「返回键」。每行操作：开关「启用」、按钮「删除跳转」（确认弹窗标题「删除这条跳转？」，描述「删除后导航不会再走这条路，需要时可以重新新建。」）。按钮「新建跳转」打开 `EdgeEditor`：选「从」「到」，动作类型选「点击」时填 x、y（0–1）。
- 空状态：「还没有界面节点」/「重启服务后会写入种子界面」。
- Toast：「已保存界面」「已删除跳转」「已启用跳转」「已停用跳转」。
- 错误提示：「保存界面失败：{message}」「删除跳转失败：{message}」。

### 4.7 提示词与代理定义

`navigator.md`：frontmatter 的 `tools` 末尾加 `where_am_i`、`navigate_to`，`version` 加 1；正文末尾追加：

```markdown
## 界面导航图

- 动手前先用 where_am_i 确认当前在哪个界面，再决定怎么走。
- 目标界面在导航图里时，直接调用一次 navigate_to，中途的识别和校验由它完成；不要拆成一步一步的 click。
- navigate_to 返回 NAV_NODE_NOT_FOUND 或 NAV_PATH_NOT_FOUND 时，看 details 里的 nearest：先 navigate_to 到那里，再用 get_screenshot 和 click 手动找。找到之后用 where_am_i 的 remember_as 记下这个界面，关卡页写成 stage:<关卡代号>，title 写中文名，下次就能一步到。
- 返回 NAV_STEP_FAILED 时，details 里有走到了哪一步、停在什么画面。先 get_screenshot 看清楚再决定，同一个 navigate_to 原样重试最多一次。
- where_am_i 认不出画面时，先截图看是不是弹窗或正在加载：弹窗就关掉，加载就等一会儿再识别。
- 不能确定是否已经到达目标时，以 where_am_i 和截图为准，不要凭记忆假定。
```

`general.md`：`tools` 加 `where_am_i`、`navigate_to`，`version` 加 1，正文不改。其他代理不直接拿这两个工具，导航交给 `navigator`。

## 5. 实施任务

### T1 数据模型与迁移

- 写范围：`maa_api/domain/enums.py`、`maa_api/db/models.py`、`maa_api/db/migrations/versions/0039_nav_graph.py`、`tests/db/test_models.py`、`tests/db/test_0039_nav_graph_migration.py`、`tests/domain/test_enums.py`
- 步骤：1. 加两个枚举与两个表类（§4.1）。2. 写迁移。3. `test_models.py` 的 `BUSINESS_TABLES` 加 `nav_node`、`nav_edge`，`EXPECTED_INDEXES` 加 `uq_nav_edge_from_node_to_node_action_hash`，外键清单加两条，JSON 列清单加 `nav_node.exemplars`、`nav_node.recognizer`、`nav_edge.action`。4. 新迁移测试：在 `0038` 上升级，断言两表与索引存在、`exemplars` 默认 `[]`；降级后两表不存在。
- 验证：`.venv/bin/python -m pytest -q tests/db tests/domain/test_enums.py > /tmp/m86-t1.log 2>&1; echo $?; tail -n 5 /tmp/m86-t1.log` → `0`
- 完成判据：`tests/db/test_initial_migration.py` 的元数据比对、可重复升级、降级到底再升级都通过。

### T2 截图归档、感知哈希与识别器

- 写范围：`maa_api/services/screenshot_archive.py`、`maa_api/agent/nav/__init__.py`、`maa_api/agent/nav/recognizer.py`、`maa_api/agent/llm/schemas/nav_classification.json`、`nav_locate.json`、`tests/agent/nav/__init__.py`、`tests/agent/nav/fakes.py`、`tests/agent/nav/test_phash.py`、`tests/agent/nav/test_recognizer.py`
- 步骤：1. `archive_screenshot` 照 `AgentRuntime._archive_screenshot_result` 的写法（路径 `image/screenshot/<sha 前两位>/<sha>.jpg`）。2. 实现 §4.5.2。3. `ModelVisionClient` 用 §0 第 6、7 条的函数；`available()` 读 vision 角色解析出的档案 `supports_vision is True`。4. `tests/agent/nav/fakes.py` 写 `make_screen(name: str) -> Image.Image`（按名字的哈希在 1280×720 灰底上画 6 个确定位置的色块，不同名字的图互相距离 > 20）、`FakeVision`（可预设 `classify`、`locate` 的返回）。
- 验证：`.venv/bin/python -m pytest -q tests/agent/nav > /tmp/m86-t2.log 2>&1; echo $?; tail -n 5 /tmp/m86-t2.log` → `0`
- 完成判据：§6 中 T2 的用例全部通过。

### T3 图服务与种子

- 写范围：`maa_api/agent/nav/graph.py`、`maa_api/agent/nav/seed.py`、`tests/agent/nav/test_graph_service.py`、`tests/agent/nav/test_seed.py`
- 步骤：1. 动作模型与 `action_hash`。2. `NavGraphService` 全部方法；所有写操作用 `async with session_factory() as s` + 显式 `commit`，JSON 列整列赋值。3. `seed.py` 照 §4.5.6 的两张表写常量，`ensure_seed()` 在 `nav_node` 为空时一次事务写入。4. Dijkstra 与隐式回主界面边（§4.5.3）。
- 验证：`.venv/bin/python -m pytest -q tests/agent/nav/test_graph_service.py tests/agent/nav/test_seed.py > /tmp/m86-t3.log 2>&1; echo $?; tail -n 5 /tmp/m86-t3.log` → `0`
- 完成判据：种子写入 15 个节点、36 条边，重复调用 `ensure_seed()` 返回 0。

### T4 导航执行器

- 写范围：`maa_api/agent/nav/navigator.py`、`maa_api/agent/tools/raw.py`（只改名）、`tests/agent/nav/fakes.py`（加 `FakeNavDevice`）、`tests/agent/nav/test_navigator.py`
- 步骤：1. `raw.py` 的 `_run_atomic` 改名 `run_atomic`，同文件调用改掉；`rg -n "_run_atomic" maa_api tests` 确认没有遗漏。2. `AppNavDevice` 用 `run_atomic(context, "navigate_to", …)` 包住 `DeviceManager` 的 `click`、`swipe`、`key_event`、`back_to_home`；`screenshot()` 调 `screenshot(backend="adb")`；`resolution()` 读 `snapshot()["resolution"]`，为空时用截图尺寸。3. 实现 §4.5.4 与 §4.5.5 的 `remember`。4. `FakeNavDevice(transitions: dict[tuple[str, str], str], start: str)`：键是 `(界面, 动作键)`，动作键形如 `tap:0.8164,0.8750`、`key:BACK`、`home`；没登记的动作停在原地；`screenshot()` 返回 `make_screen(当前界面)`，可以用 `unknown_once` 让下一次截图返回一张认不出的图。
- 验证：`.venv/bin/python -m pytest -q tests/agent/nav tests/agent/test_device_raw_tools.py > /tmp/m86-t4.log 2>&1; echo $?; tail -n 5 /tmp/m86-t4.log` → `0`
- 完成判据：§6 中 T4 的用例全部通过。

### T5 工具、策略与目录

- 写范围：`maa_api/agent/tools/nav.py`、`maa_api/agent/tools/__init__.py`、`maa_api/agent/policy.py`、`tests/agent/test_tool_catalog.py`、`tests/agent/test_nav_tools.py`
- 步骤：1. 两个工具按 §4.3.1 注册，`nav.enabled=false` 时抛 `SERVICE_UNAVAILABLE`。服务取 `app.state.nav_service`；`Recognizer` 与 `ModelVisionClient` 每次调用现建；`LastSeenStore` 是 `nav_service` 上的属性。2. `ATOMIC_TOOL_NAMES` 加 `navigate_to`。3. `EXPECTED_TOOLS` 加两个名字，两个计数在 §0 第 11 条的值上各加 2。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_tool_catalog.py tests/agent/test_nav_tools.py tests/agent/test_registry_policy.py > /tmp/m86-t5.log 2>&1; echo $?; tail -n 5 /tmp/m86-t5.log` → `0`
- 完成判据：工具目录测试与新测试通过。

### T6 REST 端点与装配

- 写范围：`maa_api/api/routers/nav.py`、`maa_api/api/schemas/agent_v3.py`、`maa_api/main.py`、`maa_api/domain/errors.py`、`tests/api/test_nav_router.py`、`tests/domain/test_errors.py`、`docs/v3/14-现行接口清单.md`、`web/openapi.json`、`web/src/types/api.d.ts`
- 步骤：1. 错误码 `NAV_NODE_NOT_FOUND`（404）、`NAV_PATH_NOT_FOUND`（409）、`NAV_STEP_FAILED`（409），每个上一行写中文注释（04 §5 的含义）。2. 路由按 §4.3.2，每条挂 `require_auth`，`responses=error_responses(...)` 列出可能的码。3. `main.py`：在 v3 服务装配处 `app.state.nav_service = NavGraphService(session_factory)`，随后 `await app.state.nav_service.ensure_seed()`（失败只记 `logger.exception`，不拖垮启动）；`create_app()` 里 `include_router(nav.router)`。4. 现行接口清单：§4 末尾新建 `### 4.N 界面导航图` 放三个码；§6 agent 小节追加四行，改「（N 条）」与总数句。`test_errors.py` 的 `ALL_CODE_COUNT`、`HTTP_BOUND_CODE_COUNT` 各加 3，`SECTION_COUNT` 加 1。5. `.venv/bin/python scripts/dump_openapi.py && (cd web && pnpm gen:api)`。
- 验证：
  ```bash
  .venv/bin/python -m pytest -q tests/api/test_nav_router.py tests/domain/test_errors.py tests/domain/test_route_inventory.py tests/api/test_app_skeleton.py > /tmp/m86-t6.log 2>&1; echo $?; tail -n 5 /tmp/m86-t6.log
  .venv/bin/python scripts/dump_openapi.py --check; echo $?
  ```
  → 两个 `0`
- 完成判据：19 个 tag 的断言不变；operationId 为 `agent_get_nav_graph` 等四个。

### T7 配置项

- 写范围：`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml`、`tests/test_settings.py`
- 步骤：按 §4.4。
- 验证：`.venv/bin/python -m pytest -q tests/test_settings.py tests/test_vapid_settings.py > /tmp/m86-t7.log 2>&1; echo $?; tail -n 3 /tmp/m86-t7.log` → `0`
- 完成判据：设置页 schema 里出现三个键，分组 Agent。

### T8 代理定义

- 写范围：`maa_api/agent/specs/builtin/navigator.md`、`maa_api/agent/specs/builtin/general.md`、`tests/agent/specs/`（只改断言工具集或版本号的用例）
- 步骤：按 §4.7。
- 验证：`.venv/bin/python -m pytest -q tests/agent/specs > /tmp/m86-t8.log 2>&1; echo $?; tail -n 3 /tmp/m86-t8.log` → `0`
- 完成判据：`AgentSpecRegistry.load()` 不报错；`navigator` 的工具集含两个新工具。

### T9 前端导航图页

- 写范围：`web/src/features/nav/*`、`web/src/routes/router.tsx`、`web/src/components/layout/nav.ts`、更多页的测试里断言入口数量的用例（`rg -n "secondaryNav" web/src --glob '*.test.tsx'` 找）
- 步骤：按 §4.6。
- 验证：`cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run src/features/nav > /tmp/m86-t9.log 2>&1; echo $?; tail -n 5 /tmp/m86-t9.log` → `0`
- 完成判据：`NavGraphPage.test.tsx` 覆盖列表渲染、启停、删除、新建跳转、识别当前画面五个流程。

### T10 冒烟与评测场景

- 写范围：`scripts/nav_smoke.py`、`tests/evals/scenarios/nav_infrast_navigate_to.yaml`、`tests/evals/scenarios/nav_unknown_stage_remember.yaml`
- 步骤：1. `nav_smoke.py`：真实 lifespan + 临时目录（照 `scripts/agent_smoke.py` 的隔离写法），把 `app.state.nav_service` 用到的设备换成 `FakeNavDevice`（`tests/agent/nav/fakes.py` 里的类，脚本 `sys.path` 加仓库根后 import）；依次断言：种子 15/36 → `where_am_i` 在 home 上 `method=anchor` 后 `method=hash` → `navigate_to("infrast")` 一步到达、`success_count=1` → 让 home→recruit 的设备转移失效，导航三次后这条跳转停用 → 末行 `SMOKE OK`。2. 两个评测场景按 M68 的场景格式（`maa_api/agent/evals/scenario.py`）写：第一个是导航员接到「导航到基建」，期望工具序列 `where_am_i`、`navigate_to`，模型调用不超过 3 次；第二个是目标 `stage:1-7` 不存在，期望 `navigate_to` 拿到 `NAV_NODE_NOT_FOUND` 后转去 `main_episode`、手动点击、最后 `where_am_i(remember_as="stage:1-7")`。
- 验证：
  ```bash
  .venv/bin/python scripts/nav_smoke.py > /tmp/m86-smoke.log 2>&1; echo $?; tail -n 1 /tmp/m86-smoke.log
  .venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m86-eval.log 2>&1; echo $?; tail -n 5 /tmp/m86-eval.log
  ```
  → `0`、`SMOKE OK`；评测通过率不低于 M85 台账记录的基线
- 完成判据：两个新场景通过。

### T11 文档同步

- 写范围：§9 列出的文档
- 步骤：按 §9。
- 验证：`.venv/bin/python scripts/check_doc_links.py | tail -n 1` → `BROKEN 0`
- 完成判据：§9 的每一项都改完。

## 6. 测试清单

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/agent/nav/test_phash.py` | `test_identical_images_have_zero_distance`、`test_signature_has_one_segment_per_roi` | 同图距离全 0；两个 ROI 得到两段 16 位十六进制，`:` 分隔 |
| 同上 | `test_small_noise_stays_within_threshold`、`test_different_layouts_exceed_threshold` | ±3 灰度噪声后每段 ≤ 10；`make_screen("a")` 与 `("b")` 距离 > 20 |
| 同上 | `test_roi_count_mismatch_returns_none` | `distances` 返回 None |
| `tests/agent/nav/test_recognizer.py` | `test_nearest_exemplar_wins` | 返回距离更近的节点，`method=hash` |
| 同上 | `test_ambiguous_match_falls_back_to_vision`、`test_generic_node_yields_to_specific_stage` | 得分差 < 2 时调了 `FakeVision.classify`；`stage_start` 与 `stage:1-7` 都匹配时取后者且不调视觉 |
| 同上 | `test_vision_below_confidence_is_unknown`、`test_vision_unavailable_returns_unknown`、`test_vision_answer_outside_candidates_is_rejected` | 三种情况都是 `node_id=None`、`method=none`；不可用时不调 classify |
| `tests/agent/nav/test_graph_service.py` | `test_action_hash_ignores_wait_and_rounds_coordinates` | `wait_ms` 不同、坐标差 0.0001 的两个 tap 哈希相同 |
| 同上 | `test_shortest_path_prefers_cheaper_edges`、`test_disabled_and_excluded_edges_are_skipped` | 选代价小的路径；停用与 `exclude` 的边不出现 |
| 同上 | `test_implicit_home_edge_connects_everything`、`test_locate_edges_need_vision` | 没有显式边时经隐式边到 home；`vision=False` 绕开 locate 边，只剩它时返回 None |
| 同上 | `test_three_failures_disable_edge`、`test_failures_do_not_disable_a_proven_edge` | 0 成功 3 失败后停用且返回 True；5 成功 3 失败仍启用 |
| 同上 | `test_success_updates_cost_moving_average`、`test_reenable_resets_fail_count` | 1000 → 2000 后 `cost_ms=1300`；重新启用后 `fail_count=0` |
| 同上 | `test_exemplars_dedupe_and_cap` | 近重复不加；第 9 个挤掉最早的非 user 样本 |
| `tests/agent/nav/test_seed.py` | `test_seed_counts_and_idempotence`、`test_seed_coordinates_are_normalized`、`test_seed_has_no_stage_specific_nodes` | 15 节点、36 边，第二次返回 0；坐标都在 0–1，返回键那几条是 0.0703、0.0556；没有 `stage:` 开头的 id |
| `tests/agent/nav/test_navigator.py` | `test_navigate_one_hop_records_success`、`test_already_there_does_nothing` | home→infrast 一步、成功计数 +1；目标是当前界面时设备没收到动作 |
| 同上 | `test_wrong_landing_learns_edge_and_replans` | tap 到了 depot：原边失败 +1、新增 learned 边 home→depot、从 depot 继续 |
| 同上 | `test_unknown_screen_recovers_via_home`、`test_home_failure_raises_step_failed` | 认不出 → 等待再认 → 回主界面后到达；`home()` 为 False → `NAV_STEP_FAILED(home_failed)` |
| 同上 | `test_max_steps_raises_step_failed`、`test_missing_target_suggests_container`、`test_no_path_raises_path_not_found` | `max_steps=1` 需两步 → `max_steps` 且带 trail；`stage:1-7` → `NAV_NODE_NOT_FOUND`、`nearest=main_episode`；孤立节点 → `NAV_PATH_NOT_FOUND(no_edges)` |
| 同上 | `test_locate_edge_learns_tap_edge`、`test_home_anchor_adds_home_exemplar` | 视觉给 (0.5, 0.5)，到达后多一条 learned tap 边；`home()` 为 True 后 home 多一个 `anchor` 样本 |
| 同上 | `test_remember_creates_node_and_sequence_edge`、`test_remember_skips_when_screen_is_another_node` | 审计里两次 click → 新节点 + `sequence` 边，坐标按分辨率归一化；识别为 home 时 `skipped_reason=screen_is_home` |
| 同上 | `test_learning_off_changes_nothing` | `learn=False`：没有新边与新样本，失败计数照记 |
| `tests/agent/test_nav_tools.py` | `test_nav_tools_registered_with_extension_fields` | 组 `nav`；`device` 为 `read`、`exclusive`；`rest_visible=True` |
| 同上 | `test_navigate_to_uses_atomic_grant`、`test_rest_navigate_to_needs_confirmation_each_time`、`test_where_am_i_is_safe` | 内置无授权 → `grant_atomic_ops`，有授权 → 放行；REST 每次确认；`where_am_i` 不确认 |
| 同上 | `test_nav_disabled_raises_service_unavailable` | `nav.enabled=false` 时两个工具都报 `SERVICE_UNAVAILABLE` |
| `tests/api/test_nav_router.py` | `test_get_graph_returns_seed`、`test_put_node_creates_user_node`、`test_put_node_rejects_bad_id` | 200 且 15 节点、`vision_available` 是布尔；新 id → `origin=user`；`Bad Id` → 400 |
| 同上 | `test_put_node_add_current_exemplar`、`test_put_node_recomputes_exemplars_when_rois_change` | 假设备截图后样本 +1、`source=user`；rois 变成两个后 `phash` 有两段 |
| 同上 | `test_put_edge_creates_and_updates`、`test_put_edge_unknown_without_fields_is_404`、`test_put_edge_unknown_node_is_404`、`test_delete_edge` | 新建后再 PUT `enabled=false` 停用；缺字段 `NOT_FOUND`；端点不存在 `NAV_NODE_NOT_FOUND`；删除 204、再删 404 |
| `tests/db/test_0039_nav_graph_migration.py` | `test_upgrade_creates_tables_and_index`、`test_downgrade_drops_tables` | 见 T1 |
| `web/src/features/nav/NavGraphPage.test.tsx` | `renders nodes and edges`、`toggles an edge`、`deletes an edge after confirm`、`creates a tap edge`、`shows where_am_i result` | 用 `vi.stubGlobal('fetch')` 做假后端，断言请求体与 toast 文案 |

## 7. 验收

### 7.1 命令（照 12 §4）

```bash
.venv/bin/python -m pytest -q > /tmp/m86-pytest.log 2>&1; echo $?; tail -n 30 /tmp/m86-pytest.log
.venv/bin/python scripts/dump_openapi.py --check; echo $?
cd web && pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build > /tmp/m86-web.log 2>&1; echo $?; cd ..
.venv/bin/python scripts/frontend_smoke.py | tail -n 1
.venv/bin/python scripts/agent_smoke.py | tail -n 1
.venv/bin/python scripts/orchestrator_smoke.py | tail -n 1
.venv/bin/python scripts/nav_smoke.py | tail -n 1
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m86-reg.log 2>&1; echo $?
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m86-cap.log 2>&1; echo $?
git diff --check; echo $?
.venv/bin/python scripts/check_doc_links.py | tail -n 1
```

期望：pytest 不新增失败；各退出码 0；四个冒烟末行 `SMOKE OK`；能力评测通过率不低于 M85 基线；`BROKEN 0`。

### 7.2 种子坐标出处核对（本机有 MAA 资源时做，结果记台账）

```bash
.venv/bin/python - <<'EOF'
import glob, json
d = {}
for f in glob.glob('resource/lib/maa/Darwin/resource/tasks/**/*.json', recursive=True):
    try: d.update(json.load(open(f)))
    except Exception: pass
keys = ('InfrastDefault RecruitDefault MallDefault DepotDefault OperBoxDefault GachaDefault FriendsDefault Mail '
        'ReturnButton EnterEpisodeNew-Click QuickSwitch@ToHome@Open QuickSwitch@ToHome@Entry '
        'QuickSwitch@ToTerminal@Entry QuickSwitch@ToOperBox@Entry QuickSwitch@ToInfrast@Entry Home '
        'GoTerminalStopflag StartButton1 InfrastEnteredFlag RecruitFlag CreditStoreOcr').split()
for k in keys:
    x, y, w, h = d[k]['roi']
    print(k, round((x + w/2)/1280, 4), round((y + h/2)/720, 4), [round(v, 4) for v in (x/1280, y/720, w/1280, h/720)])
EOF
```

逐项与 §4.5.6 两张表对照，一致才算通过；MAA 资源更新导致的差异记进台账，并按新值改 `seed.py`。

### 7.3 真机补充验证（不作为硬性门禁，结果记台账）

1. 设备停在首页，导航页点「识别当前画面」：第一次通常是 unknown 或 vision；调用一次 `POST /api/core/back_to_home` 后再点，应为 `home`、`method=hash`。
2. 用工单让导航员「导航到基建再回到主界面」，记录 `agent_run` 里该子运行的 `steps` 与 `prompt_tokens`。
3. 把 `nav.enabled` 关掉，重复第 2 步（导航员退回逐次点击），记录同样两个数。
4. 两组各跑 3 次取平均，台账里写出模型调用次数与 prompt token 的对比。期望开启导航图后模型调用不超过 3 次、prompt token 不超过关闭时的 40%；达不到时记录实际值与原因，不阻塞合并。
5. 用 `navigate_to("terminal")` 走一次 locate 跳转，确认导航页出现一条 `origin=learned` 的点击跳转。

## 8. 风险与回滚

| 风险 | 对策 |
|---|---|
| 首页主题或游戏改版让种子坐标失效 | 三次失败自动停用；视觉定位与学习补位；导航页可修正 |
| 感知哈希把两个相似界面认混 | 歧义判定交给视觉模型；样本上限 8 并去重；导航页可移除错误样本 |
| 学到错误跳转污染图 | 学到的跳转同样受失败停用规则约束；`nav.learn` 可关闭 |
| 导航误点到消耗类按钮 | 种子图不含开始行动之后的界面；按键只允许返回；视觉定位只用于种子里写明的入口 |
| 视觉模型调用花钱 | 只在哈希拿不准时调用；成功后学成样本与点击跳转，之后不再调用 |

回滚：`git revert -m 1 <merge>`，再 `alembic downgrade 0038_channels`（丢失学到的图）。

## 9. 文档同步

- [02 §4.1](../../02-系统架构.md)：`nav/` 一行补 `seed.py`；§2.2 服务表补 `NavGraphService`（`maa_api/agent/nav/graph.py`，M86）；§5 装配表补 `nav_service`。
- [05 §6.1](../../05-运行时与代理定义.md)：`navigate_to` 的风险写成「DANGEROUS（策略按原子操作的会话授权）」；§8 schema 表补 `nav_classification`、`nav_locate`。
- [03 §13](../../03-数据模型.md)：枚举表补 `NavNodeKind`、`NavOrigin`。
- `docs/v3/14-现行接口清单.md`：错误码 +3（新增一节），agent 路由 +4。
- `docs/v3/16-部署指南.md`：补「界面导航图」一节，说明 `nav.*` 三个键与视觉模型的关系。
- 计数：工具 +2（模型可见 +2）、端点 +4、错误码 +3、表 +2、配置键 +3。

## 10. 停下来问

- §0 第 9 条没有匹配：导航员在子代理会话里拿不到父 Worker 的原子操作授权，`navigate_to` 会每次弹授权卡。
- §0 第 10 条没有匹配：REST 调用写设备工具时不抢占租约，需要先确认 M71 的抢占口径。
- M67 没有能用的 vision 角色调用方式（§0 第 6、7 条有一条不满足）。
- 本机 MAA 资源里任一出处 roi 与 §4.5.6 不一致且差值超过 0.01。
- 需要新增本文没列出的错误码、端点或配置键。

## 11. 需要补进契约的名字

| 名字 | 种类 | 用途 |
|---|---|---|
| `NavNodeKind`、`NavOrigin` | 枚举（03 §13） | `nav_node.kind`、`nav_*.origin` 的取值 |
| `NavGraphService` | 服务（02 §2.2） | 图的读写、种子、最短路径 |
| `app.state.nav_service` | 装配（02 §5） | 工具与路由共用 |
| `maa_api/agent/nav/seed.py` | 模块（02 §4.1） | 种子常量 |
| `maa_api/services/screenshot_archive.py` | 模块 | 截图归档公共函数，M89 复用 |
| `nav_classification`、`nav_locate` | 结构化输出 schema（05 §8） | 视觉分类与定位 |
| `web/src/features/nav/`、路由 `/more/nav` | 前端（11） | 导航图页 |
| `scripts/nav_smoke.py` | 冒烟脚本（02 §4.3） | 导航图端到端冒烟 |
| `where_am_i` 的参数 `remember_as`、`title`、`kind` | 工具参数（05 §6.1） | 导航员记下新界面 |
| `nav_node.exemplars[*].source`、`added_at` | JSON 字段（03 §12） | 样本来源与时间 |
