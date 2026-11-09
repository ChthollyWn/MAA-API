# M89 新活动自适应与示教录制（规格）

> 阶段：P4　依赖：M88　分支：`activity-adapt-teach`　迁移：`0042_demonstration`
> 本文是 M89 的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在 M89 分支、基点干净 | `git status -sb` | 第一行 `## activity-adapt-teach`；工作区干净。若带 `docs/v3/05`、`docs/v3/12` 的 P4 预置修改，先确认它们属于升格准备并已提交 |
| 2 | M88 已合并 | `git tag -l 'v3-m88'` | `v3-m88` |
| 3 | M86 的截图归档函数存在 | `grep -n "async def archive_screenshot" maa_api/services/screenshot_archive.py` | 1 行，签名含 `trigger` 与 `backend` 参数 |
| 4 | 迁移 head | `ls maa_api/db/migrations/versions/ \| grep -E '^[0-9]{4}_' \| sort \| tail -n 1` | `0041_nav_graph.py` |
| 5 | 工具计数 | `.venv/bin/python -c "from maa_api.agent.tools import build_registry as b; r=b(); print(len(r.list()), sum(1 for d in r.list() if d.model_visible))"` | `108 107` |
| 6 | 端点、错误码与节数 | `.venv/bin/python -c "from maa_api.main import create_app; a=create_app(); print(sum(len([m for m in v if m in ('get','post','put','delete','patch')]) for v in a.openapi()['paths'].values()))"`；`grep -n "ALL_CODE_COUNT\|HTTP_BOUND_CODE_COUNT\|SECTION_COUNT" tests/domain/test_errors.py` | 端点 `186`；错误码 `171 170 22` |
| 7 | 业务表与配置键 | `.venv/bin/python -c "from tests.db.test_models import BUSINESS_TABLES; print(len(BUSINESS_TABLES))"`；`.venv/bin/python -c "from maa_api.settings import SETTING_KEYS; from maa_api.services.settings_schema import _FIELDS; print(len(SETTING_KEYS), len(_FIELDS))"` | 业务表 `57`（M86 之后）；配置 `143 132` |
| 8 | v2 自定义任务链路 | `grep -n "async def register_custom_task\|def _validate_custom_task\|def _write_enabled_custom_tasks" maa_api/services/resource_service.py` | 3 行，约 502、794、648 |
| 9 | 设备原子端点 | `grep -n "async def click_device\|async def swipe_device\|async def long_press_device" maa_api/api/routers/atomic.py maa_api/api/routers/device.py` | 3 行，约 89、371、406 |
| 10 | 租约、闸门与抢占 | `grep -n "class ClaimGate\|async def acquire\|async def preempt\|async def claim_gate" maa_api/agent/lease.py`；`grep -n "async def preempt_device" maa_api/api/routers/device_lease.py` | 5 行 |
| 11 | 示教代码与前端路由尚不存在 | `grep -rn "demonstration" maa_api web/src --include=*.py --include=*.ts --include=*.tsx`；`grep -n "more/demonstrations" web/src/routes/router.tsx web/src/components/layout/nav.ts` | 都无输出 |
| 12 | 迁移与枚举测试基线 | `.venv/bin/python -m pytest -o addopts= -q tests/domain/test_enums.py tests/db/test_models.py tests/db/test_migrate.py > /tmp/m89-base.log 2>&1; echo $?; tail -n 3 /tmp/m89-base.log` | 退出码 `0` |

表格单元里的 `\|` 是 Markdown 转义，复制到 shell 时去掉反斜杠。

第 3 条不成立时按 §10 停下来问，不要自己造截图归档函数。第 5 至 7 条与实测不一致时先核对 M86、M87、M88 的 spec 登记，把差值记进台账再继续。

## 1. 背景与目标

新活动刚开时 MAA 资源不认识关卡，作业站也没有作业，`resolve_stage` 与 `search_copilots` 都给不出可执行方案。M89 让用户先在网页上示教一段点击与滑动，把这段操作编译成内核认识的自定义任务链；同时给 `general` Worker 两个工具，让它在界面里试走一遍后起草并校验同一条链。做完后能验证：

1. `/more/demonstrations` 显示设备实时画面。用户点一下、滑一下、长按一下，服务端在现有的 `POST /api/device/click`、`/api/device/swipe`、`/api/device/long_press` 成功路径上采集步骤，并归档操作前后两张截图。刷新页面或关掉标签，步骤仍在服务端。
2. 同一时刻只有一个 `recording`。停止时有步骤置 `recorded`；零步、用户丢弃、进程重启、设备断开都置 `discarded`。
3. 录制期间设备由进程内闸门占用：Agent 取租约得到 `DEVICE_LEASE_BUSY`；手动提交的流水线可以入队，但不会出队执行；开始录制时已有运行中流水线则 409。
4. `convert` 把步骤编译成 `Custom_*` 链（N 个动作节点加一个 `Stop` 出口），按设备像素换算到 1280×720，做结构校验与链级校验。默认只返回编译结果；`register=true` 才一次性注册整条链。
5. `general` Worker 拿到 `draft_custom_task`、`validate_custom_task`，能对新活动关卡执行「走一遍 → 起草 → 校验 → 一次注册 → 试跑」。
6. 计数：工具 `108/107` → `110/109`；端点 `186` → `190`（§6.15 agent `83` → `87`）；错误码 `171/170` → `173/172`（节数 `22` → `23`）；业务表 `57` → `58`；配置键 `143/132` → `144/133`；迁移 head `0041` → `0042`。

## 2. 非目标

- 不做物理设备输入抓取（`adb shell getevent`）。M89 只录网页画布上发生的操作。
- 不做 Copilot 作业草稿。`converted_to` 只记自定义任务链，不产出 `artifact` 或 M90 的视图。
- 不新增 Worker，不改 M87 的 copilot 循环，不给 `copilot` 加 DANGEROUS 工具。
- 不做 SSE 或 WS 推送。录制中的步骤由前端轮询列表获得。
- 不做多设备。表里没有 `device_id`，默认 `default`。
- 不自动删除示教。截图按现有 7 天保留策略清理，示教行永久保留。
- 不做步骤编辑器，不提供修改坐标、增删步骤、调整顺序。
- 不录 `input_text`、`key_event`、`back_to_home`。内核 `tasks.json` 没有对应动作，页面也不提供这些按钮。
- 不从导航图路径编译自定义任务，`draft_custom_task` 只接受点击、滑动、长按三类步骤；不新增批量注册工具，不新增第五个端点，多节点注册通过扩展 v2 `register_custom_task` 的 `tasks` 参数完成。

## 3. 相关文档

- 表与枚举：[03 §2](../03-数据模型.md)、[03 §12](../03-数据模型.md)、[03 §13](../03-数据模型.md)、[03 §14](../03-数据模型.md)。
- 端点、错误码与配置：[04 §2.5](../04-接口与事件协议.md)、[04 §5](../04-接口与事件协议.md)、[04 §6](../04-接口与事件协议.md)。
- Worker 与工具：[05 §3.2](../05-运行时与代理定义.md)、[05 §6.1](../05-运行时与代理定义.md)、[05 §6](../05-运行时与代理定义.md)。
- 范围与前端：[01](../01-总览与范围.md) 的「新活动自适应、示教录制」；[11 §2、§3](../11-前端指挥台.md) 的 `/more/demonstrations`。
- 路线与完成标准：[12 §2、§4](../12-实施路线与里程碑.md)。
- 决策记录：[13 §8.39–§8.44](../13-决策记录.md)（M71 租约）、[13 §8.173–§8.177](../13-决策记录.md)（确认与提问进指挥台、提问不过期）。v2 先例：`docs/v2/11-Agent模块设计.md` §7（535–571，注册确认卡）；`docs/v2/13-决策记录.md` 的 ADR-08（约 59 行）、约 1700、1705、1838 行（`Custom` 链、`Done`、`ClickRect`、`Swipe`）；`docs/v2/14-开放API接入指南.md` 约 319 行（`Custom` 只跑第一个匹配任务）。
- 代码入口：`maa_api/services/resource_service.py`（`register_custom_task` 约 502 行、`_validate_custom_task` 约 794 行、`_write_enabled_custom_tasks` 约 648 行）、`maa_api/agent/tools/resource.py`（`register_custom_task` 工具约 85 行）、`maa_api/agent/tools/raw.py`（`_run_atomic` 约 108 行、`CLICK_SETTLE_SECONDS` 约 19 行）、`maa_api/services/device_service.py`（`click` 约 765 行、`swipe` 约 740 行、`long_press` 约 794 行、`screenshot` 约 696 行）、`maa_api/agent/lease.py`（`ClaimGate` 约 111 行、`acquire` 约 214 行、`preempt` 约 338 行、`claim_gate` 约 396 行）、`maa_api/db/repositories/pipeline.py`（`_next_pending_id` 约 134 行、`claim_next` 约 168 行）、`maa_api/api/routers/device_lease.py`（`preempt_device` 约 72 行）、`maa_api/api/routers/device.py`（`_run_atomic` 约 343 行）、`maa_api/util/image.py`（`store_screenshot` 约 243 行）、`maa_api/db/repositories/log.py`（`ScreenshotRepository.create` 约 317 行）。

## 4. 设计

### 4.1 数据模型

`maa_api/domain/enums.py` 新增：

```python
class DemonstrationStatus(StrEnum):
    RECORDING = "recording"      # 正在录制，进程内闸门占用设备
    RECORDED = "recorded"        # 已停止，至少一步，可转换
    CONVERTED = "converted"      # 已注册成自定义任务，终态
    DISCARDED = "discarded"      # 零步、用户丢弃、重启或设备断开，终态
```

`maa_api/db/models.py` 新增，列与 [03 §12](../03-数据模型.md) 的 `demonstration` 行一致：

```python
class Demonstration(SQLModel, table=True):
    """一次示教：steps 是 JSON 数组，元素形状见 §4.1；不建索引（03 §12 不列索引）。"""
    __tablename__ = "demonstration"
    id: str = Field(default_factory=new_uuid, primary_key=True, max_length=36)
    title: str = Field(max_length=64)
    status: str = Field(default=DemonstrationStatus.RECORDING, max_length=16)
    steps: list[dict[str, Any]] = Field(default_factory=list, sa_column=json_column("steps", nullable=False))
    converted_to: dict[str, Any] | None = Field(default=None, sa_column=json_column("converted_to"))
    created_at: datetime = Field(default_factory=utcnow, sa_column=datetime_column("created_at"))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=datetime_column("updated_at"))
```

`steps` 的元素是扁平对象，不按 action 拆联合类型：

```json
{"seq": 1, "action": "click", "x": 640, "y": 360,
 "x1": null, "y1": null, "x2": null, "y2": null,
 "duration_ms": null, "delay_ms": 0, "screen": [1280, 720],
 "screenshot_id": "8d0d8f...", "result_screenshot_id": "8c9d1a...",
 "at": "2026-11-09T08:30:00.123Z"}
```

- `click` 用 `x`、`y`；`long_press` 用 `x`、`y`、`duration_ms`；`swipe` 用 `x1`、`y1`、`x2`、`y2`、`duration_ms`。不适用的整数字段写 `null`。
- `screen` 是设备像素分辨率 `[width, height]`，来自动作发生时的设备快照。`seq` 从 1 连续递增；`delay_ms` 是本步开始与上一步开始的间隔，夹 0–10000，第一步为 0；`at` 是结尾 `Z` 的 UTC 时间。两张截图都缺失时两个 id 都是 `null`，步骤仍保留。
- 上限 `MAX_DEMONSTRATION_STEPS = 100`。达到上限时追加第 100 步后自动停止并置 `recorded`。

`converted_to` 在 `convert(register=true)` 成功后写入：

```json
{"entry": "Custom_Demo_ab12cd34_1",
 "task_names": ["Custom_Demo_ab12cd34_1", "Custom_Demo_ab12cd34_2", "Custom_Demo_ab12cd34_Done"],
 "asset_ids": ["...", "...", "..."], "registered_at": "2026-11-09T08:31:00Z"}
```

迁移 `maa_api/db/migrations/versions/0042_demonstration.py`：`revision="0042_demonstration"`、`down_revision="0041_nav_graph"`；`upgrade()` 建一张 `demonstration` 表（`id` `VARCHAR(36)` 主键、`title` `VARCHAR(64)` 非空、`status` `VARCHAR(16)` 非空、`steps` `JSON` 非空、`converted_to` `JSON` 可空、`created_at` 与 `updated_at` `DATETIME` 非空，主键约束名 `pk_demonstration`）；`downgrade()` 删表。没有数据迁移，不建索引。截图复用 `ScreenshotTrigger.MANUAL`，步骤只存 `screenshot_id`，不新增枚举取值。

### 4.2 模块与文件

新建：

```python
# maa_api/agent/demonstrations/service.py
class DemonstrationService:
    def __init__(self, session_factory, *, device_manager, lease: DeviceLeaseService,
                 resource_service: ResourceService) -> None: ...
    async def recover(self) -> int: ...
    def is_recording(self) -> bool: ...
    async def start(self, *, title: str | None) -> dict[str, Any]: ...
    async def stop(self, demonstration_id: str, *, discard: bool = False) -> dict[str, Any]: ...
    async def list(self, *, status: str | None, page: int, size: int) -> dict[str, Any]: ...
    async def note_step(self, demonstration_id: str, *, action: str,
                        points: Mapping[str, int | None], duration_ms: int | None,
                        started_at: datetime, before: Image.Image | None,
                        after: Image.Image | None, backend: str) -> dict[str, Any] | None: ...
    async def abort(self, reason: str) -> None: ...
    async def convert(self, demonstration_id: str, *, register: bool) -> dict[str, Any]: ...

# maa_api/agent/demonstrations/compiler.py
@dataclass(frozen=True, slots=True)
class Step:
    action: str
    x: int | None = None; y: int | None = None; x1: int | None = None; y1: int | None = None
    x2: int | None = None; y2: int | None = None
    duration_ms: int | None = None; delay_ms: int = 0; screen: tuple[int, int] = (0, 0)
@dataclass(frozen=True, slots=True)
class Compiled:
    entry: str; tasks: dict[str, dict[str, Any]]; chain: tuple[str, ...]

class CompileError(ValueError):
    def __init__(self, errors: list[dict[str, str]]) -> None: ...
def parse_step(raw: Mapping[str, Any]) -> Step: ...
def compile_steps(*, prefix: str, steps: Sequence[Step]) -> Compiled: ...
def scale_point(value: int, height: int, maximum: int) -> int: ...

# maa_api/agent/custom_task/rules.py
@dataclass(frozen=True, slots=True)
class TaskIssue:
    task: str; field: str; reason: str
    def as_dict(self) -> dict[str, str]: ...
NAME_PATTERN = re.compile(r"^Custom_[A-Za-z0-9][A-Za-z0-9_.-]{0,54}$")
ALLOWED_ACTIONS = frozenset({"ClickSelf", "ClickRect", "DoNothing", "Swipe", "Stop"})
MAX_BUNDLE_TASKS = 128
def check_bundle(tasks: Mapping[str, Mapping[str, Any]], entry: str
                 ) -> tuple[list[TaskIssue], list[TaskIssue], tuple[str, ...]]: ...
def check_entry_name(name: str) -> list[TaskIssue]: ...

# maa_api/agent/demonstrations/capture.py
@dataclass(slots=True)
class PendingStep:
    demonstration_id: str; screen: tuple[int, int]; backend: str
    started_at: datetime; before: Image.Image | None
async def begin(request: Request, manager: Any) -> PendingStep | None: ...
async def finish(request: Request, pending: PendingStep | None, manager: Any, *, action: str,
                 points: Mapping[str, int | None], duration_ms: int | None) -> None: ...
async def cancel(pending: PendingStep | None) -> None: ...
```

`maa_api/agent/tools/custom_task.py` 定义 `DraftStep`（`action`、`x`/`y`、`x1`/`y1`/`x2`/`y2`、`duration_ms`、`delay_ms`、`screen`）、`DraftCustomTaskParams`（`stage_code`、`steps` 1–31 步且加 `Done` 不超过 32 个节点、`name`）、`ValidateCustomTaskParams`（`entry`、`tasks` ≤128）三个模型，`draft_custom_task`、`validate_custom_task` 两个 handler 与 `register_tools(registry)`；`RegisterCustomTaskParams` 改为 `content` 与 `tasks` 二选一。`maa_api/api/routers/demonstrations.py` 定义 `router = APIRouter(prefix="/api/agent", tags=["agent"])` 与四个端点函数，DTO 写在同文件。

修改：`domain/enums.py`、`domain/errors.py`、`db/models.py`、`services/resource_service.py`（公开校验包装与 `tasks` 批量注册）、`agent/lease.py`（录制闸门、`ClaimGate.allow_manual`）、`db/repositories/pipeline.py`（`allow_manual`）、`services/pipeline_runner.py`（传 `allow_manual`）、`api/routers/atomic.py` 与 `api/routers/device.py`（采集接线）、`agent/tools/resource.py`（`tasks` 参数）、`agent/tools/__init__.py`（注册新工具）、`agent/specs/builtin/general.md`、`main.py`（装配与启动清理）。

### 4.3 接口

只实现 [04 §2.5](../04-接口与事件协议.md) 的 M89 四行，函数名即该表第三列。四个端点都挂 `Depends(require_auth)`；`demonstration.enabled=false` 或服务未装配时返回 503 `SERVICE_UNAVAILABLE`。没有 `GET /{id}`，列表返回完整 `steps`。没有 SSE 或 WS 事件。

| 方法与路径 | 函数名 | 行为 |
|---|---|---|
| POST `/api/agent/demonstrations` | `start_demonstration` | 201。body `{title?}`。开始录制，返回 `screen` |
| GET `/api/agent/demonstrations` | `list_demonstrations` | 200。query `status`、`page`、`size`，返回 `steps` |
| POST `/api/agent/demonstrations/{id}/stop` | `stop_demonstration` | 200。body `{discard?}`。按状态机置 `recorded` 或 `discarded` |
| POST `/api/agent/demonstrations/{id}/convert` | `convert_demonstration` | 200。body `{register?}`。编译、校验，按需注册 |

请求与响应模型（DTO 写在路由文件里）：

```python
class DemonstrationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid"); title: str | None = Field(default=None, max_length=64)
class DemonstrationStop(BaseModel):
    model_config = ConfigDict(extra="forbid"); discard: bool = False
class DemonstrationConvert(BaseModel):
    model_config = ConfigDict(extra="forbid"); register: bool = False

class DemonstrationStepOut(BaseModel):
    seq: int; action: Literal["click", "swipe", "long_press"]
    x: int | None = None; y: int | None = None; x1: int | None = None; y1: int | None = None
    x2: int | None = None; y2: int | None = None; duration_ms: int | None = None
    delay_ms: int; screen: list[int]
    screenshot_id: str | None = None; result_screenshot_id: str | None = None
    at: str

class DemonstrationOut(BaseModel):
    id: str; title: str; status: DemonstrationStatus
    steps: list[DemonstrationStepOut]; converted_to: dict[str, Any] | None = None
    created_at: datetime; updated_at: datetime

class DemonstrationStartOut(DemonstrationOut):
    screen: list[int]

class DemonstrationPage(BaseModel):
    items: list[DemonstrationOut]; total: int; page: int; size: int

class CompiledTaskOut(BaseModel):
    entry: str; tasks: dict[str, dict[str, Any]]; chain: list[str]
    valid: bool; errors: list[dict[str, str]]; chain_errors: list[dict[str, str]]
    register: bool; registered: list[dict[str, str]]

class DemonstrationConvertOut(DemonstrationOut):
    compile: CompiledTaskOut
```

- `start`：body 可省略（按 `{}` 处理）。`title` 为空或未给时用 `示教 MM-DD HH:MM`（服务端本地时间）。201 返回 `DemonstrationStartOut`，`screen` 是当前设备像素分辨率。
- `list`：`status` 取 `DemonstrationStatus` 的四个取值，非法值 400 `INVALID_PARAMETER`；`page` 默认 1 且不小于 1；`size` 默认 20，范围 1–50；`page`/`size` 越界 → 422 `VALIDATION_ERROR`（Pydantic query 校验）。按 `created_at DESC, id DESC` 排序。
- `stop`：body 可省略。404 `DEMONSTRATION_NOT_FOUND`；状态不是 `recording` 时 409 `DEMONSTRATION_STATE_INVALID`。
- `convert`：`register=false` 只编译并校验，不改状态、不写资源；`register=true` 注册整条链并置 `converted`。404、409 同上；校验失败 422 `CUSTOM_TASK_INVALID`，`details` 带 `{"errors": [...], "chain_errors": [...]}`；名字预检冲突 422 `CUSTOM_TASK_INVALID`（`errors` 带冲突名），写入竞态冲突 409 `RESOURCE_ASSET_CONFLICT`；重载失败 500 `RESOURCE_LOAD_FAILED`。
- 四个端点都可能有 503 `SERVICE_UNAVAILABLE`、401 `UNAUTHORIZED`；`start` 另有 409 `PIPELINE_ALREADY_RUNNING`、503 `DEVICE_NOT_CONNECTED`、409 `DEMONSTRATION_STATE_INVALID`。

`CUSTOM_TASK_INVALID` 保持 v2 的 422，不新增第三个码。M89 只新增 `DEMONSTRATION_NOT_FOUND`（404）与 `DEMONSTRATION_STATE_INVALID`（409），`details` 分别是 `{"id": ...}` 与 `{"id": ..., "status": ..., "action": ...}`。

权限按 D23：任何持有访问令牌的 REST 客户端都能开始、停止、转换；同一时刻只允许一个 `recording`；没有所有者与会话绑定，第二个客户端可以停掉别人开的录制。Agent 不能调用这四个端点，它们不是工具。

### 4.4 配置项

只加一个键，分组「Agent」，默认开启：

| 键 | 类型 | 默认 | 分组 | hot_action |
|---|---|---|---|---|
| `demonstration.enabled` | bool | true | Agent | none |

五处同步：

1. `maa_api/settings.py`：`SETTING_KEYS` 加 `"demonstration.enabled": ("demonstration", "enabled")`；新增 `class DemonstrationSettings(BaseModel)`（`model_config = ConfigDict(extra="ignore")`，字段 `enabled: bool = True`）；`Settings` 加 `demonstration: DemonstrationSettings = Field(default_factory=DemonstrationSettings)`；`ENV_OVERRIDES` 加 `"demonstration.enabled": "MAA_DEMONSTRATION_ENABLED"`。
2. `maa_api/services/settings_schema.py` 的 `_FIELDS` 加 `"demonstration.enabled": dict(label="示教录制", group="Agent", type="boolean", description="关闭后示教页与四个端点都不可用", hot_action="none")`。
3. `config.template.yaml` 在 `nav:` 段之后加：

```yaml
demonstration:
  # 示教录制；关闭后 /more/demonstrations 与四个端点返回 503
  enabled: true
```

4. `tests/test_settings.py` 的 `ENV_OVERRIDES` 断言集合加 `MAA_DEMONSTRATION_ENABLED`。
5. `web/src/features/settings/sections.ts` 不需要新分区，`Agent` 分组已映射到 `agent` 分区。

步数上限 100、截图后的停顿 `CLICK_SETTLE_SECONDS`、列表每页上限 50 都写成服务常量，不进设置。

### 4.5 行为与算法

**状态机**

| 当前 | 操作 | 结果 |
|---|---|---|
| 无 | `start` | 插入 `recording` |
| `recording` | `stop(discard=false)` 且步骤数 ≥ 1 | `recorded` |
| `recording` | `stop(discard=false)` 且步骤数 = 0 | `discarded` |
| `recording` | `stop(discard=true)` | `discarded` |
| `recording` | 进程启动清理、设备断开 | `discarded` |
| `recorded` | `convert(register=false)` | 不变 |
| `recorded` | `convert(register=true)` 成功 | `converted`，写 `converted_to` |
| `recorded` | `convert` 校验失败或注册失败 | 不变，无部分资产 |
| `converted`、`discarded` | 任何操作 | 409 `DEMONSTRATION_STATE_INVALID` |

`discarded` 有四条可达路径：零步停止、显式丢弃、启动清理、设备断开。`converted` 与 `discarded` 是终态，没有删除端点。

**录制闸门（D12）**

`DeviceLeaseService` 增加进程内属性 `_recording: bool`，并提供 `set_recording(active: bool) -> None` 与 `recording() -> bool`。不写 `device_lease` 表，不改表结构。

- `set_recording(True)` 在 `DemonstrationService.start` 里、插入行之前同步调用。`acquire()` 在检查抢占记号之后、排队之前检查 `_recording`，为真抛 409 `DEVICE_LEASE_BUSY`（消息「用户正在网页上示教，设备暂不可用」）；在 `_try_insert` 成功后再检查一次，若已开始录制就删掉刚插入的行并抛同一个码。
- `claim_gate()` 在 `_recording` 为真时返回 `ClaimGate(lease_id="demonstration", allowed_session_ids=frozenset(), allow_manual=False)`。
- `ClaimGate` 增加字段 `allow_manual: bool = True`；`PipelineRepository._next_pending_id` 与 `claim_next` 增加 `allow_manual: bool = True` 参数；`PipelineRunner._claim_next` 把 `gate.allow_manual` 透传下去。`allow_manual=False` 时候选条件只有 `agent_session_id IN (...)`，不再并上 `source='manual'`。
- 停止、丢弃、启动清理都调用 `set_recording(False)`。

`start` 的顺序：在 `_start_lock` 内依次检查无其它 `recording` 行、设备已连接且分辨率已知；`lease.set_recording(True)`；查 `PipelineRepository.current()`，有运行中流水线就回滚闸门并抛 409 `PIPELINE_ALREADY_RUNNING`；插入 `recording` 行（失败时回滚闸门）；`await lease.preempt("rest_atomic", detail="用户开始示教录制")`（失败只记日志）；返回。`preempt` 会给被赶走的工单收件箱写「设备被用户接管」，Agent 下一次取租约收到 `DEVICE_LEASE_PREEMPTED`。

**采集**

三个设备端点 `click_device`、`swipe_device`、`long_press_device` 的流程改成：

```python
pending = await capture.begin(request, manager)
try:
    result = await _run_atomic(...)  # 原有实现不动
except BaseException:
    await capture.cancel(pending)
    raise
await capture.finish(request, pending, manager, action=..., points=..., duration_ms=...)
return result
```

- `click_device` 没有 `_run_atomic`：`begin` 放在现有 `preempt_device` 之前，`finish` 放在 `operation_lock` 块之后，其余照上面的 `try/except`。
- `begin` 先从 `app.state.demonstration_service` 取服务；没有服务、未启用或 `is_recording()` 为假时返回 `None`，不截图、不加锁，行为与现状一致。
- 设备状态不是 `connected` 时 `begin` 调 `service.abort("device_lost")` 并返回 `None`，端点随后按原有错误返回，录制置 `discarded`。设备分辨率未知时不记录该步（日志 warning），录制继续。
- 操作前截一张图放在内存（不归档）；`finish` 先 `await asyncio.sleep(CLICK_SETTLE_SECONDS)`，再截操作后的图，把两张图交给 `service.note_step(...)`。`note_step` 在同一个会话里对每张图调用 `archive_screenshot(session, image, trigger=ScreenshotTrigger.MANUAL, backend=ScreenshotBackend(backend))`，追加步骤后一次提交。单张截图失败时对应 id 写 `null`，动作照常成功。
- `note_step` 用服务内 `asyncio.Lock` 串行追加；`seq=len(steps)+1`；`delay_ms` 取本步 `started_at` 与上一步 `started_at` 的毫秒差，夹 0–10000，第一步 0。达到 100 步时提交后立刻置 `recorded`、清闸门。页面在 mutation pending 时忽略新的 pointerdown，步骤顺序与用户操作顺序一致。

**编译（D14、D16）**

`compile_steps` 对每一步：

1. 校验 `screen` 两个分量都大于 0；`x`、`y` 在 `[0, width)`、`[0, height)` 内；`swipe` 的起点与终点都在界内。不满足抛 `CompileError`。
2. 坐标换算与 `DeviceManager.click` 同一规则：`round(px * 720 / height)`。x 结果夹 0–1279，y 结果夹 0–719。
3. `click` 节点：`{"algorithm": "JustReturn", "action": "ClickRect", "specificRect": [x, y, 10, 10], "postDelay": 800, "next": ["<下一节点>"]}`。`x` 夹 0–1270、`y` 夹 0–710，保证 10×10 矩形落在 1280×720 内；落点是矩形中心，与录制点最多差 5 px。
4. `swipe` 节点：`{"algorithm": "JustReturn", "action": "Swipe", "specificRect": [x1, y1, 100, 100], "rectMove": [x2, y2, 100, 100], "specialParams": [duration_ms, 1, 37, 1], "postDelay": 800, "next": ["<下一节点>"]}`。起点与终点分别夹到 0–1180、0–620；`duration_ms` 取录制值，夹 100–5000。
5. `long_press` 没有独立 action，编译成起终点相同的 `Swipe`：`specificRect` 与 `rectMove` 都是 `[x, y, 100, 100]`（夹到 0–1180、0–620），`specialParams` 是 `[duration_ms, 1, 37, 1]`，`duration_ms` 夹 600–5000。`draft_custom_task` 的返回里给一条 `warnings`：「长按编译成起终点相同的滑动」。
6. 节点 `postDelay` 取下一步 `delay_ms`，最后一步固定 800。
7. 链尾追加 `{"algorithm": "JustReturn", "action": "Stop"}`，命名为 `<prefix>_Done`。每个节点 `next` 指向下一个节点，最后一个动作节点指向 `Done`。
8. 节点名：`prefix` 以 `Custom_` 开头，去掉前缀后的部分把非 `[A-Za-z0-9_.-]` 字符换成 `_`，截到 40 字符，空则用 `Demo`；动作节点是 `Custom_<slug>_<序号>`，出口是 `Custom_<slug>_Done`。每个名字必须匹配 `^Custom_[A-Za-z0-9][A-Za-z0-9_.-]{0,54}$` 且总长不超过 62。

示教转换的 `prefix` 是 `Custom_Demo_<id 前 8 位>`；`draft_custom_task` 用 `name` 参数，未给时用 `Custom_<清理后的 stage_code>`。

**校验（D15、D24）**

`ResourceService.validate_custom_task_content(content)` 是 `_validate_custom_task` 的公开包装：调用私有方法，捕获 `AppError` 并返回 `[{"field": "content", "reason": exc.message}]`，成功返回 `[]`。不改变 v2 抛错口径。

`rules.check_bundle(tasks, entry)` 做链级检查，返回 `(errors, chain_errors, chain)`：

1. `tasks` 1–128 个节点；`entry` 是其中一个键；每个键匹配名字正则。
2. 每个节点如果有 `action`，必须属于 `{"ClickSelf", "ClickRect", "DoNothing", "Swipe", "Stop"}`；`next`、`onErrorNext`、`exceededNext` 的每个值必须出现在 `tasks` 里，`#self` 视为环并拒绝。
3. 从 `entry` 做深度优先遍历，同一路径上重复访问报「任务链有环」；遍历不到任何 `Stop` 节点时报「任务链没有 Stop 出口」；`chain` 记录第一次到达 `Stop` 的路径。
4. `ClickRect` 缺 `specificRect`、`Swipe` 缺 `specificRect` 或 `rectMove` 时报错；`specificRect`、`rectMove`、`roi` 的矩形必须落在 1280×720 内且宽高大于 0。
5. 每个错误是 `{"task": 节点名, "field": 字段, "reason": 中文原因}`；链级错误放在 `chain_errors`，结构错误放在 `errors`。

`validate_custom_task` 工具与 `convert` 都先逐节点调 `validate_custom_task_content`，再调 `check_bundle`。工具不抛错，返回 `{"valid": false, "errors": [...], "chain_errors": [...], "chain": [...]}`；`convert` 在 `valid=false` 时抛 422 `CUSTOM_TASK_INVALID`。

**批量注册（D5）**

`ResourceService.register_custom_task` 签名改为：

```python
async def register_custom_task(self, *, name: str, description: str | None,
                               content: Mapping[str, Any] | None = None,
                               tasks: Mapping[str, Mapping[str, Any]] | None = None
                               ) -> dict[str, Any]:
```

- `content` 与 `tasks` 必须恰好给一个，两个都给或都不给抛 422 `CUSTOM_TASK_INVALID`。`tasks` 路径要求 `name` 在 `tasks` 里、节点数 1–128、每个名字过 `_custom_task_name`、每个节点过 `_validate_custom_task`、`check_bundle` 通过；已存在同名 `custom_task` 资产时抛 409 `RESOURCE_ASSET_CONFLICT`，不写任何行。
- 在一个数据库事务里创建全部 `resource_asset` 行；提交后按需写每个节点的大文件，再调一次 `_write_enabled_custom_tasks()` 与一次 `_reload()`。任一步失败时删除全部新行、删除新文件、恢复 `tasks.json` 快照、再重载一次，抛 `RESOURCE_LOAD_FAILED`。
- 返回 `{"entry": name, "task_names": [...], "items": [...]}`，`items` 是 `_asset_wire` 的列表。旧调用（只给 `content`）走原来的 `_register_custom_task`，返回形状不变。工具层把 `tasks` 上限收紧到 32 个节点，保证确认卡可读；REST `convert` 直接用服务方法，不受这个上限约束。

**转换**

`convert` 加服务内 `_convert_lock`，在锁内重新读行：

1. 状态必须是 `recorded`，否则 409。
2. `parse_step` 逐条解析 `steps`，`compile_steps(prefix=f"Custom_Demo_{id[:8]}", ...)`；`CompileError` 转 422 `CUSTOM_TASK_INVALID`。
3. 逐节点结构校验加 `check_bundle`；失败 422，`details` 带两个错误数组。
4. 计算注册后的名字集合，与现有 `custom_task` 资产比对；有冲突时 422，`errors` 里给 `{"task": 名字, "field": "name", "reason": "已存在同名自定义任务"}`。只预览也报这个错，因为注册一定失败。
5. `register=false`：返回 `DemonstrationConvertOut`，`compile.registered=[]`，状态不变。
6. `register=true`：调 `resource_service.register_custom_task(name=entry, description=f"示教 {title[:48]}", tasks=compiled.tasks)`；成功后把 `status` 改 `converted`、写 `converted_to`、提交；返回 `compile.registered`。服务抛错时行保持 `recorded`，不写 `converted_to`。

**启动清理**

`main.py` 在 `device_lease_service` 装配之后创建 `DemonstrationService`（02 §5 装配表追加为第 12 行，`nav_service` 保持第 11 行），调用 `await demonstration_service.recover()`：把所有 `recording` 行改成 `discarded`（`updated_at` 同步），清内存活动标记，`lease.set_recording(False)`。随后把服务挂到 `app.state.demonstration_service`，并 `include_router(demonstrations.router)`。

**边界**

| 输入 | 期望 |
|---|---|
| 第二个 `start` | 409 `DEMONSTRATION_STATE_INVALID`，原录制不受影响 |
| `start` 时已有运行中流水线 | 409 `PIPELINE_ALREADY_RUNNING`，不插入行，闸门回滚 |
| `start` 时设备未连接或分辨率未知 | 503 `DEVICE_NOT_CONNECTED`，不插入行 |
| 录制中 Agent 取租约 | 409 `DEVICE_LEASE_BUSY` |
| 录制中手动提交流水线 | 入队成功，`claim_next` 不领取；停止录制后按队列顺序执行 |
| 零步 `stop` 或 `stop(discard=true)` | `discarded` |
| `stop` 已停止的示教 | 409 `DEMONSTRATION_STATE_INVALID` |
| 第 100 步 | 步骤写入成功，随后自动 `recorded`、闸门释放 |
| 动作失败或截图失败 | 动作错误原样返回；失败的动作不追加步骤，截图失败时 id 为 `null` |
| 设备断开或进程重启 | 录制置 `discarded`，闸门释放 |
| `convert` 预览、注册失败、重复注册 | 预览不改状态；注册失败无部分资产；重复注册 409 |

### 4.6 前端

路由：`web/src/routes/router.tsx` 加 `{ path: 'more/demonstrations', element: page(<DemonstrationsPage />) }`，放在 `more/nav` 之前。导航：`web/src/components/layout/nav.ts` 的 `secondaryNav` 加 `{ to: '/more/demonstrations', label: '示教录制', icon: MousePointerClick, description: '在画面里点几下，转成自定义任务', group: 'agent' }`，位置在 `more/watchdog` 之后。`11 §2` 已把示教放在 `agent` 组，这里按契约落地；技能、记忆仍留在 `ops`，不在本里程碑改动。

新建 `web/src/features/demonstrations/`：

- `DemonstrationsPage.tsx`：左右两栏，窄屏单列。左栏是「示教」卡片，右栏是「示教列表」。
- `useDemonstrations.ts`：`useQuery` 调 `GET /api/agent/demonstrations`，query key 是 `keys.agent.demonstrations(filters)`；有 `recording` 时 `refetchInterval: 2000`，否则 `false`。`useStartDemonstration`、`useStopDemonstration`、`useConvertDemonstration` 三个 mutation，成功后失效 `keys.agent.demonstrationsAll()`。
- `LiveCanvas.tsx`：录制中每 1000ms 用 `fetch('/api/device/screenshot?backend=adb&format=jpeg&size=mobile&quality=70', { credentials: 'same-origin' })` 取一张 JPEG，转成 object URL 铺在画布上，每帧替换时 `URL.revokeObjectURL` 旧地址。没有录制时只显示一张静态画面与「开始示教」。`<img>` 用 `w-full h-auto select-none`，`draggable={false}`，外层 `touch-action: none`。
- `StepList.tsx`、`ConvertPanel.tsx`：步骤行显示序号、动作、坐标、延迟、状态与两张缩略图（`GET /api/screenshots/{id}`，失败或 410 显示「截图已过期」占位）；预览面板按节点展开 JSON，显示 `chain` 与错误，有错误时禁用「注册任务」。
- `DemonstrationsPage.test.tsx`、`canvas.test.ts`：组件与手势测试。

坐标映射：从 `GET /api/device/status` 的 `resolution` 取 `[width, height]`（未知时禁用画布并提示重连设备）。`x = round((clientX - rect.left) / rect.width * width)`，`y` 同理，最后夹到 `[0, width-1]`、`[0, height-1]`。不按 `naturalWidth` 映射，因为 `size=mobile` 会把图缩到长边 1280。

手势：

| 手势 | 判定 | 请求 |
|---|---|---|
| 点击 | 按下到抬起 < 600ms 且位移 ≤ 8px | `POST /api/device/click` `{x, y, force: false}` |
| 滑动 | 抬起时位移 > 8px | `POST /api/device/swipe` `{x1, y1, x2, y2, duration_ms: 夹 100–2000 的实际时长, force: false}` |
| 长按 | 按住 600ms 仍未移动 | 到点发 `POST /api/device/long_press` `{x, y, duration_ms: 夹 600–3000 的实际时长, force: false}`，抬起不再发点击 |

请求进行中忽略新的 `pointerdown`；失败时 Toast「示教失败：{原因}」，录制继续。

页面文案（遵守[文案规范](../../copy-audit/规范-文案规范.md)）：

| 位置 | 文案 |
|---|---|
| 导航与标题 | 示教录制 |
| 空态 | 还没有示教；在画面里点一下、滑一下，停止后就能生成自定义任务 |
| 按钮 | 开始示教、停止示教、丢弃示教、预览任务、注册任务、刷新 |
| 录制中提示 | 正在示教。这里的点击与滑动会直接操作设备，手机上同时操作会互相抢。 |
| 停止结果 | 示教已保存；示教没有步骤，已丢弃 |
| 注册确认 | 注册为自定义任务？；会写入 N 个任务节点并重载资源 |
| 注册成功 | 已注册 N 个任务 |
| 截图过期 | 截图已过期；只保留 7 天，不影响已注册的任务 |
| 状态标签 | 录制中、已录制、已转换、已丢弃 |
| 长按说明 | 按住 0.6 秒是长按 |
| 滑动说明 | 滑动是否生效取决于设备与界面，点击最可靠 |

进入页面时不自动开始录制；开始前要求设备已连接，否则按钮禁用并显示「设备未连接」。

### 4.7 提示词与代理定义

`maa_api/agent/specs/builtin/general.md`：

- frontmatter 的 `tools` 末尾加 `draft_custom_task`、`validate_custom_task`；`version: 7` 改 `version: 8`。
- 正文在「外部资料」段之后加一段：

```
新活动没有作业时：先用 get_activity_stages、resolve_stage、search_copilots 确认确实没有现成作业。确认后先 get_screenshot 看界面，用 click、swipe 走到目标关卡的「开始行动」页，每一步截图确认。把这段步骤交给 draft_custom_task(stage_code, steps)，每个 step 写 action、坐标与截图分辨率 screen；返回的 tasks 先交给 validate_custom_task。valid=false 就按 errors 与 chain_errors 改步骤重编，不要直接注册。注册用 register_custom_task，一次传 tasks 整条链，确认卡会展示所有节点。注册后 submit_pipeline 用 Custom 任务试跑，停在开始行动页才算成功。用户自己在 /more/demonstrations 录的示教是另一条输入；不要替用户开始、停止或转换示教。
```

不新增 Worker，不改 `copilot.md`。`05 §3.2` 的 `general` 工具列补两个名字，`05 §6.1` 的计数改成 `110/109`，并在 `register_custom_task` 行补「可选 `tasks` 映射，1–32 个节点，additive」。

## 5. 实施任务

### T1 枚举、错误码、表与迁移（约 1.5 小时）

- 写范围：`maa_api/domain/enums.py`、`maa_api/domain/errors.py`、`maa_api/db/models.py`、`maa_api/db/migrations/versions/0042_demonstration.py`、`tests/domain/test_enums.py`、`tests/domain/test_errors.py`、`tests/db/test_models.py`、`tests/db/test_demonstration_migration.py`。
- 步骤：1. 加 `DemonstrationStatus` 与 `Demonstration`，照 §4.1 逐列。2. 错误码加两条，各带中文注释，`ERROR_HTTP_STATUS` 绑 404 与 409。3. 迁移照 0038 的写法，`down_revision="0041_nav_graph"`。4. `test_enums.py` 的 `EXPECTED_VALUES` 加 `DemonstrationStatus`。5. `test_errors.py` 改成 `173/172/23`。6. `test_models.py` 的 `BUSINESS_TABLES` 与 `UUID_PK_TABLES` 加 `demonstration`，JSON 列清单加 `steps`、`converted_to`，测试名与 docstring 的数字改成 `58`。7. 迁移测试覆盖列、可空性、默认值、升级、降级、离线 SQL。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/domain/test_enums.py tests/domain/test_errors.py tests/db/test_models.py tests/db/test_demonstration_migration.py > /tmp/m89-t1.log 2>&1; echo $?; tail -n 5 /tmp/m89-t1.log` → `0`。
- 完成判据：升级后 `demonstration` 七列与 03 §12 一致；降级后表消失；再升级为空表；枚举与错误码计数测试通过。

### T2 服务状态机与录制闸门（约 2 小时）

- 写范围：`maa_api/agent/demonstrations/__init__.py`、`maa_api/agent/demonstrations/service.py`、`maa_api/agent/lease.py`、`maa_api/db/repositories/pipeline.py`、`maa_api/services/pipeline_runner.py`、`tests/agent/demonstrations/__init__.py`、`tests/agent/demonstrations/test_service.py`、`tests/agent/lease/test_device_lease_service.py`、`tests/services/test_pipeline_runner.py`。
- 步骤：1. 按 §4.2 写服务骨架与 `start`、`stop`、`abort`、`recover`、`list`、`note_step`。2. 加 `_recording` 闸门与 `ClaimGate.allow_manual`。3. 仓储与 runner 透传 `allow_manual`。4. 测试覆盖状态机全部转移、并发 `start`、100 步上限、启动清理、`acquire` 与手动领取被挡。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/agent/demonstrations tests/agent/lease/test_device_lease_service.py tests/services/test_pipeline_runner.py > /tmp/m89-t2.log 2>&1; echo $?; tail -n 5 /tmp/m89-t2.log` → `0`。
- 完成判据：状态机表每一行都有用例；录制中 `acquire` 抛 `DEVICE_LEASE_BUSY`；手动流水线在闸门期间不被领取；重启清理只改 `recording` 行。

### T3 采集接线（约 1.5 小时）

- 写范围：`maa_api/agent/demonstrations/capture.py`、`maa_api/api/routers/atomic.py`、`maa_api/api/routers/device.py`、`tests/api/test_demonstration_capture.py`。
- 步骤：1. 写 `begin`、`finish`、`cancel`。2. 在三个端点按 §4.5 的 `try/except` 接线，`_run_atomic` 与既有错误行为不动。3. 归档用 M86 的 `archive_screenshot`，trigger 固定 `MANUAL`。4. 测试覆盖三种动作、未录制不截图、动作失败不追加、截图失败留 `null`、设备断开置 `discarded`、100 步自动停止。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/api/test_demonstration_capture.py tests/api/test_device_router.py > /tmp/m89-t3.log 2>&1; echo $?; tail -n 5 /tmp/m89-t3.log` → `0`。
- 完成判据：假设备下点三次得到三条步骤与六张截图记录；未录制时端点行为与现状逐字一致。

### T4 编译器、校验器与批量注册（约 2 小时）

- 写范围：`maa_api/agent/demonstrations/compiler.py`、`maa_api/agent/custom_task/__init__.py`、`maa_api/agent/custom_task/rules.py`、`maa_api/services/resource_service.py`、`tests/agent/demonstrations/test_compiler.py`、`tests/services/test_custom_task_rules.py`、`tests/services/test_agent_resource_service.py`。
- 步骤：1. 按 §4.5 写节点模板、720 换算、名字生成、链尾。2. 写 `parse_step`、`scale_point`、`CompileError`。3. 写 `check_bundle` 与 `validate_custom_task_content`。4. 扩展 `register_custom_task` 的 `tasks` 路径，单事务、一次重载、失败全回滚。5. 测试覆盖点击、滑动、长按节点形状，换算边界，未知 `next`、环、缺 `Stop`、非法 action、越界矩形，批量成功、批量回滚、旧调用不变。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/agent/demonstrations/test_compiler.py tests/services/test_custom_task_rules.py tests/services/test_agent_resource_service.py > /tmp/m89-t4.log 2>&1; echo $?; tail -n 5 /tmp/m89-t4.log` → `0`。
- 完成判据：编译出的 `tasks` 能被 v2 `_validate_custom_task` 逐节点接受；批量注册只调用一次 `reload_resources`；重载失败后数据库与 `tasks.json` 都回到调用前。

### T5 工具、代理定义与目录计数（约 1.5 小时）

- 写范围：`maa_api/agent/tools/custom_task.py`、`maa_api/agent/tools/__init__.py`、`maa_api/agent/tools/resource.py`、`maa_api/agent/specs/builtin/general.md`、`tests/agent/test_tool_catalog.py`、`tests/agent/test_custom_task_tools.py`、`tests/agent/test_resource_ops_schedule_tools.py`、`tests/api/test_agent_specs_router.py`、`tests/agent/specs/test_agent_specs.py`、`tests/agent/specs/test_m70_specs.py`。
- 步骤：1. 按 §4.2 与 §4.3 注册两个工具，组 `custom_task`，SAFE，`read_only=True`，`refetchable=True`，`rest_visible=True`。2. `RegisterCustomTaskParams` 加可选 `tasks`，`model_validator` 保证 `content`/`tasks` 二选一，`tasks` 1–32 个且 `name` 是入口。3. `general.md` 加工具与段落，版本改 8。4. `EXPECTED_TOOLS` 与 `EXPECTED_REFETCHABLE` 各加两个名字，工具计数改 `110/109`，refetchable 计数改 `54`；`EXPECTED_VERSIONS["general"]` 改 8。5. `tests/agent/specs/test_agent_specs.py` 的 `test_legacy_chat_lists_every_v2_registered_tool_in_registration_order`：`len(definitions)` 从 108 改成 110，新增 `M89_TOOLS = {"draft_custom_task", "validate_custom_task"}` 并并入「legacy_chat 不加载」排除集。6. `tests/agent/specs/test_m70_specs.py` 的 `general` 期望集合加 `draft_custom_task`、`validate_custom_task`。7. 测试覆盖起草、校验不抛、注册批量、注册旧调用不变、SAFE 与 REST 可见。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/agent/test_tool_catalog.py tests/agent/test_custom_task_tools.py tests/agent/test_resource_ops_schedule_tools.py tests/api/test_agent_specs_router.py tests/agent/specs/test_agent_specs.py tests/agent/specs/test_m70_specs.py > /tmp/m89-t5.log 2>&1; echo $?; tail -n 5 /tmp/m89-t5.log` → `0`；`.venv/bin/python -c "from maa_api.agent.tools import build_registry as b; r=b(); print(len(r.list()), sum(1 for d in r.list() if d.model_visible))"` → `110 109`。
- 完成判据：两个工具在 `GET /api/agent/tools` 可见；`draft_custom_task` 不写库、不写文件；`general` 版本为 8。

### T6 REST、装配、OpenAPI 与清单（约 1.5 小时）

- 写范围：`maa_api/api/routers/demonstrations.py`、`maa_api/main.py`、`tests/api/test_demonstrations.py`、`docs/v3/14-现行接口清单.md`、`tests/domain/test_route_inventory.py`、`tests/domain/test_errors.py`。
- 步骤：1. 四个端点照 §4.3，DTO 写在路由文件里，`responses=error_responses(...)` 列出全部可能的码。2. `main.py` 装配服务、调用 `recover()`、`include_router`。3. 14 §4 末尾新建 `### 4.23 示教与自定义任务`，放两个新码；§6.15 追加四行，小节计数 `83` 改 `87`，总数句 `186` 改 `190`；错误码总数句 `171` 改 `173`，HTTP 绑定 `170` 改 `172`，节数说明 `22` 改 `23`。4. 跑 `scripts/dump_openapi.py` 后 `cd web && pnpm gen:api`。5. 测试覆盖四端点成功与错误路径、503 开关、启动清理、OpenAPI operationId。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/api/test_demonstrations.py tests/domain/test_route_inventory.py tests/domain/test_errors.py > /tmp/m89-t6.log 2>&1; echo $?; tail -n 5 /tmp/m89-t6.log` → `0`；`.venv/bin/python scripts/dump_openapi.py --check > /tmp/m89-openapi.log 2>&1; echo $?` → `0`。
- 完成判据：OpenAPI 有四个新 operation，总数 190；14 的行集合与 `web/openapi.json` 一致。

### T7 前端（约 2 小时）

- 写范围：`web/src/routes/router.tsx`、`web/src/components/layout/nav.ts`、`web/src/features/demonstrations/`、`web/src/api/keys.ts`、`web/src/types/api.d.ts`（生成）。
- 步骤：1. 加路由与 `agent` 组导航项。2. 写页面、查询、三个 mutation、画布、步骤列表、预览面板。3. 手势与坐标映射照 §4.6，请求进行中忽略新手势。4. 文案照 §4.6 的表，空态、错误态、截图过期占位齐全。5. 测试覆盖画布映射与三种手势、列表渲染、开始/停止/丢弃、预览、注册确认。
- 验证：`cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run src/features/demonstrations > /tmp/m89-t7.log 2>&1; echo $?; tail -n 8 /tmp/m89-t7.log` → `0`。
- 完成判据：页面在 1280×720 的假截图上点 (640,360) 发出的请求坐标是 (640,360)；滑动与长按不重复发点击；窄屏单列可操作。

### T8 冒烟、文档、计数与评测（约 1.5 小时）

- 写范围：`scripts/demonstration_smoke.py`、`docs/v3/02-系统架构.md`、`docs/v3/03-数据模型.md`、`docs/v3/04-接口与事件协议.md`、`docs/v3/05-运行时与代理定义.md`、`docs/v3/11-前端指挥台.md`、`docs/v3/13-决策记录.md`、`config.template.yaml`、`tests/evals/scenarios/m89-new-activity-custom-task.yaml`、`tests/evals/scenarios/m89-demonstration-is-user-operation.yaml`、`tests/evals/cassettes/m89-new-activity-custom-task.jsonl`、`tests/evals/cassettes/m89-demonstration-is-user-operation.jsonl`、`docs/v3/ledgers/m89-activity-adapt-teach.md`。
- 步骤：1. 冒烟脚本照 `scripts/agent_smoke.py` 的临时目录与 lifespan 隔离写法，在脚本内定义 `SmokeDemoDevice`（`snapshot()` 返回 `state="connected"`、`resolution={"width":1280,"height":720}`，实现 `screenshot`、`click`、`swipe`、`long_press`、`close`），在 `install_fake_lifespan(app)` 之后把 `app.state.device_manager_factory` 换成它，覆盖从开始到注册的完整链路。2. 按 §9 改文档与计数，14 与 03、04、05 的措辞照 §11 的表。3. 两个评测场景：`m89-new-activity-custom-task` 的 `tool_sequence` 依次 `draft_custom_task`、`validate_custom_task`、`register_custom_task`（`args.tasks` 长度 ≥ 3）、`submit_pipeline`，`max_calls` 把注册限制为 1 次，`outcome_state` 要求流水线 `completed`，`text_contains` 含 `Custom_` 与「开始行动」；`m89-demonstration-is-user-operation` 的 `forbid` 含 `draft_custom_task`、`validate_custom_task`、`register_custom_task`，`text_contains` 含 `/more/demonstrations` 与「示教」。4. 写台账。
- 验证：`.venv/bin/python scripts/demonstration_smoke.py > /tmp/m89-smoke.log 2>&1; echo $?; tail -n 1 /tmp/m89-smoke.log` → `0`、`SMOKE OK`；`.venv/bin/python scripts/check_doc_links.py | tail -n 1` → `BROKEN 0`；`.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m89-eval.log 2>&1; echo $?; tail -n 8 /tmp/m89-eval.log` → `0`，2 个新场景通过。
- 完成判据：冒烟跑通开始、点三下、划一下、长按一下、停止、预览、注册、假内核收到 `Custom` 任务；文档计数与 §9 的表一致；台账含验证证据与 Rulings。

## 6. 测试清单

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/db/test_demonstration_migration.py` | `test_upgrade_creates_demonstration_table` | 七列的类型、可空性、主键名与 03 §12 一致，没有索引 |
| 同上 | `test_defaults_and_downgrade` | 插入后 `steps == []`、`converted_to is None`；降级后表不存在，再升级为空表 |
| 同上 | `test_offline_sql_renders_both_directions` | `--sql` 输出含建表与删表 |
| `tests/agent/demonstrations/test_service.py` | `test_start_conflicts_with_existing_recording` | 第二个 start 抛 409，第一行不变 |
| 同上 | `test_start_rejects_running_pipeline` | 抛 `PIPELINE_ALREADY_RUNNING`，闸门已回滚 |
| 同上 | `test_stop_transitions` | 有步骤 `recorded`；零步与 `discard=true` 都是 `discarded` |
| 同上 | `test_stop_on_recorded_is_state_invalid` | 409 `DEMONSTRATION_STATE_INVALID` |
| 同上 | `test_recover_and_abort_discard` | 启动清理只改 `recording` 行；设备断开置 `discarded` 并释放闸门 |
| 同上 | `test_note_step_stops_at_100` | 第 100 步后状态 `recorded`、闸门释放 |
| `tests/agent/lease/test_device_lease_service.py` | `test_acquire_busy_while_recording` | 409 `DEVICE_LEASE_BUSY` |
| 同上 | `test_claim_gate_blocks_manual_while_recording` | `allow_manual=False`，空允许集 |
| `tests/services/test_pipeline_runner.py` | `test_recording_gate_does_not_claim_manual` | 手动行保持 `pending` |
| `tests/api/test_demonstration_capture.py` | `test_click_appends_step_with_two_screenshots` | 一条步骤、两个非空截图 id |
| 同上 | `test_no_capture_without_recording` | 截图函数未被调用，响应不变 |
| 同上 | `test_failed_action_appends_no_step` | 抛错的点击不追加步骤 |
| 同上 | `test_swipe_long_press_and_null_screenshots` | 坐标与时长正确；截图失败时 id 为 `null` 且步骤仍在 |
| 同上 | `test_device_lost_discards_recording` | 状态 `discarded` |
| `tests/agent/demonstrations/test_compiler.py` | `test_compile_node_shapes` | `ClickRect` 是 `[x,y,10,10]`；`Swipe` 有 `rectMove` 与 `specialParams`；长按起终点相同且有 warning |
| 同上 | `test_scale_and_chain_tail` | 1080 高设备换算与 1280×720 边界；最后一个动作指向 `_Done`，`_Done` 是 `Stop` |
| 同上 | `test_compile_rejects_zero_height` | `CompileError` 含字段名 |
| `tests/services/test_custom_task_rules.py` | `test_chain_errors` | 未知 `next`、环、缺 `Stop` 三种都进 `chain_errors` |
| 同上 | `test_action_and_bounds` | `InputText` 被拒；1281 或 721 被拒；包装返回 `field=content` 不抛 |
| `tests/services/test_agent_resource_service.py` | `test_register_bundle_writes_all_nodes_once` | 所有节点进 `tasks.json`，`_reload` 一次 |
| 同上 | `test_register_bundle_rolls_back_on_reload_failure` | 行、文件、`tasks.json` 全部回滚 |
| 同上 | `test_register_bundle_rejects_conflict_before_write` | 409，无新行 |
| 同上 | `test_register_single_call_shape_unchanged` | 旧调用返回单个资产 |
| `tests/api/test_demonstrations.py` | `test_start_returns_recording_and_screen` | 201、`status=recording`、`screen` 两个分量 |
| 同上 | `test_start_error_paths` | 录制中 409；开关关闭 503 |
| 同上 | `test_list_steps_and_pagination` | `steps` 完整；过滤生效；越界 size 422 `VALIDATION_ERROR` |
| 同上 | `test_stop_error_paths` | 不存在 404 `DEMONSTRATION_NOT_FOUND`；状态错误 409 |
| 同上 | `test_convert_preview_does_not_write` | 状态仍 `recorded`，资产数不变 |
| 同上 | `test_convert_register_marks_converted` | `converted`、`converted_to.entry` 正确 |
| 同上 | `test_convert_failure_paths` | 校验失败 422 且状态不变；名字预检冲突 422；写入竞态冲突 409；都没有部分资产 |
| `tests/agent/test_custom_task_tools.py` | `test_draft_returns_bundle_without_writing` | 返回 `entry/tasks/chain/warnings`，无资产 |
| 同上 | `test_validate_and_register` | 校验不抛且错误数组完整；注册一次且名称是入口；`content` 与 `tasks` 同给时参数校验失败 |
| `tests/agent/test_tool_catalog.py` | `test_custom_task_tools_are_safe_and_rest_visible` | 组、风险、`rest_visible` |
| `tests/agent/specs/test_agent_specs.py` | `test_legacy_chat_lists_every_v2_registered_tool_in_registration_order` | `len(definitions)==110`；`M89_TOOLS`（两个工具）被排除出 legacy_chat |
| `tests/agent/specs/test_m70_specs.py` | `test_general_tools_equal_legacy_chat_minus_delegate_recon_plus_worker_tools` | general 的工具集合多 `draft_custom_task`、`validate_custom_task` |
| `web/src/features/demonstrations/canvas.test.ts` | `maps_tap_to_device_pixels` | 640×360 设备坐标 |
| 同上 | `classifies_swipe_and_long_press` | 三种手势不重复发 |
| `web/src/features/demonstrations/DemonstrationsPage.test.tsx` | `starts_and_stops_recording` | 按钮、状态文案、失效查询 |
| 同上 | `preview_then_register_requires_confirmation` | 预览不出确认，注册出确认 |

## 7. 验收

自动验收：

```bash
# 1. 目标测试
.venv/bin/python -m pytest -o addopts= -q tests/domain/test_enums.py tests/domain/test_errors.py \
  tests/db/test_demonstration_migration.py tests/db/test_models.py \
  tests/agent/demonstrations tests/api/test_demonstration_capture.py \
  tests/api/test_demonstrations.py tests/services/test_custom_task_rules.py \
  tests/agent/test_custom_task_tools.py > /tmp/m89-target.log 2>&1; echo $?; tail -n 8 /tmp/m89-target.log
# 2. 后端全量（按 12 §4 第 2 条）
.venv/bin/python -m pytest -o addopts= -q > /tmp/m89-pytest.log 2>&1; echo $?; tail -n 30 /tmp/m89-pytest.log
# 3. OpenAPI 与生成物
.venv/bin/python scripts/dump_openapi.py && (cd web && pnpm gen:api)
.venv/bin/python scripts/dump_openapi.py --check && (cd web && pnpm gen:api:check)
# 4. 前端
(cd web && pnpm install --frozen-lockfile && pnpm typecheck && pnpm test --run && pnpm build)
# 5. 冒烟
.venv/bin/python scripts/demonstration_smoke.py > /tmp/m89-smoke.log 2>&1; echo $?; tail -n 1 /tmp/m89-smoke.log
.venv/bin/python scripts/agent_smoke.py > /tmp/m89-agent-smoke.log 2>&1; echo $?; tail -n 1 /tmp/m89-agent-smoke.log
.venv/bin/python scripts/orchestrator_smoke.py > /tmp/m89-orch-smoke.log 2>&1; echo $?; tail -n 1 /tmp/m89-orch-smoke.log
.venv/bin/python scripts/frontend_smoke.py > /tmp/m89-front-smoke.log 2>&1; echo $?; tail -n 1 /tmp/m89-front-smoke.log
# 6. 评测
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m89-reg.log 2>&1; echo $?; tail -n 8 /tmp/m89-reg.log
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m89-cap.log 2>&1; echo $?; tail -n 8 /tmp/m89-cap.log
# 7. 文档与 diff
.venv/bin/python scripts/check_doc_links.py | tail -n 1
git diff --check
```

期望：1 至 6 的退出码都是 `0`，四个冒烟末行都是 `SMOKE OK`；`BROKEN 0`；`git diff --check` 无输出；capability 通过率不低于 M88 台账的 capability 基线（M88 后 capability 16 个场景）。

手动验收（真机，不作为硬性门禁，证据记台账）：

1. 真机连上后开始录示教，点三次、滑一次、长按一次；每一步在列表里有两张缩略图，坐标与截图一致。
2. 停止后预览，确认 `chain` 顺序与操作顺序一致，`_Done` 是 `Stop`。
3. 注册后提交 `Custom` 任务，链尾停在预期界面；对同一关卡的第二次提交仍只跑第一个匹配的入口。
4. 录制中刷新页面，步骤还在；关掉标签再打开，列表显示 `recording` 或已停止的状态。
5. 录制中拔掉设备，页面提示设备断开，列表状态变 `discarded`；重新连接后 Agent 能取到租约。录制中手动提交一条流水线，队列里看得到、不执行；停止录制后它开始执行。
6. 真机连续点击 20 次，检查每次点击前的截图与点击后的截图时间差不超过 1 秒。

## 8. 风险与回滚

- 迁移只建一张表，`downgrade` 直接删表，没有数据迁移。回滚前先 `remove_custom_task` 删除本次注册的节点，或在回滚后保留这些节点（它们仍在自定义层，`Custom` 任务仍可用）。
- 批量注册是本次唯一的 REST 写资源入口，也是 v2「资源的写入侧没有 HTTP 端点」原则的例外。写前做结构校验、链级校验、同名冲突检查；写中单事务、单次重载；失败删行、删文件、恢复 `tasks.json` 快照并再重载。
- 进程崩溃或重启：所有 `recording` 行在启动时置 `discarded`，闸门随进程消失，不会留下永久占用。设备被 Agent 抢占时，`start` 先开闸门再 `preempt`，被赶走的 Agent 收到 `DEVICE_LEASE_PREEMPTED` 与工单系统消息；录制期间 `acquire` 直接失败，不会出现两边同时点。
- 截图 7 天后过期：步骤只存 `screenshot_id`，`converted_to` 不依赖截图；前端显示占位，已注册的任务不受影响。转换后资源被删时 `converted_to` 仍是历史记录，M89 不回写；要重新注册就重新录制。
- 关掉功能：`demonstration.enabled=false`，四个端点与页面都返回 503，已有数据保留。关闭开关不会自动停止正在进行的录制；先停止再关闭，进程重启时 `recover` 会清理遗留的 `recording` 行。
- 滑动在部分设备与界面上无效（v2 真机记录里 `adb shell input swipe` 对游戏画面可能完全无效）；长按编译成起终点相同的 `Swipe`，真机是否把这段时长当作长按需要实测。点击走内核 `AsstAsyncClick`，是可靠路径；页面文案明确说明滑动不承诺。
- 网页画布只能录页面内发生的操作，用户在手机上直接操作不会被采集，这是 D1 的边界。每步两张截图会增加 0.1–0.3 秒延迟与磁盘占用；100 步上限避免一次录制无限增长。
- 没有所有者：任何持有 token 的客户端都能停止别人的录制，单用户模型下接受这个行为，页面不显示「谁在录」。开始录制与运行中流水线之间有极小的竞态；闸门在插入行之前打开，`PipelineRunner` 在领取前读闸门，因此本进程内不会有流水线在录制开始后出队，跨进程不在 M89 范围。
- 回滚：`git revert -m 1 <merge>`，再 `alembic downgrade 0041_nav_graph`。回滚后 `/more/demonstrations` 与四个端点消失，已注册的自定义任务留在资源层。

## 9. 文档同步与计数

| 文档 | 改什么 |
|---|---|
| [03 §12](../03-数据模型.md)、§13、§14 | `demonstration` 行补 `steps` 元素与 `converted_to` 形状（指向本文 §4.1）；枚举表加 `DemonstrationStatus`；「不自动删除」列表加 `demonstration` |
| [04 §5](../04-接口与事件协议.md)、§6 | `CUSTOM_TASK_INVALID` 由 400 改 422（v2 既有绑定）；加 `demonstration.enabled`，bool，true，Agent 分组 |
| [05 §3.2](../05-运行时与代理定义.md) | `general` 工具列加 `draft_custom_task`、`validate_custom_task` |
| [05 §6.1](../05-运行时与代理定义.md) | 计数改 `110/109`；`register_custom_task` 补 `tasks` 参数；两个新工具补参数与返回 |
| [02 §2.2、§4.1、§4.3、§5](../02-系统架构.md) | 服务表加 `DemonstrationService`；模块表加 `agent/demonstrations/`、`agent/custom_task/`、`api/routers/demonstrations.py`；脚本表加 `demonstration_smoke.py`；装配表追加为第 12 行 `demonstration_service`（`nav_service` 保持第 11 行） |
| [11 §3](../11-前端指挥台.md) | `/more/demonstrations` 行补实时画布、点/滑/长按、预览与注册确认 |
| [12 §2](../12-实施路线与里程碑.md) | 迁移号已是 `0042`，不改 |
| [14 §4](../14-现行接口清单.md) | 新建 `### 4.23 示教与自定义任务` 放两个新码；总数 171→173、绑定 170→172、节数 22→23 |
| [14 §6](../14-现行接口清单.md) | §6.15 追加四行，`（83 条）`→`（87 条）`，总数 186→190 |
| [13 §8](../13-决策记录.md) | 末尾加一条（按当时最后编号 +1），记 D1–D24 的裁决、REST 写资源例外、长按编译方式、遗留的真机项 |
| `config.template.yaml` | 加 `demonstration.enabled` |
| 测试计数 | `test_tool_catalog.py` 改 `110/109` 并加两个名字；`test_agent_specs_router.py` 的 `general` 改 8；`test_errors.py` 改 `173/172/23`；`test_models.py` 加 `demonstration` 并把测试名改 58；`test_settings.py` 断言加 `MAA_DEMONSTRATION_ENABLED` |

计数：

| 项 | M88 后 | M89 后 |
|---|---|---|
| 工具（总/模型可见） | 108/107 | 110/109 |
| 端点（总/§6.15 agent） | 186/83 | 190/87 |
| 错误码（全部/HTTP 绑定/节数） | 171/170/22 | 173/172/23 |
| 业务表 | 57 | 58 |
| 配置键（`SETTING_KEYS`/`_FIELDS`） | 143/132 | 144/133 |
| 迁移 head | 0041_nav_graph | 0042_demonstration |
| 评测场景 | 21 | 23 |

## 10. 停下来问

- §0 第 3 条没有匹配：`archive_screenshot` 不存在或签名不是 `(session, image, *, trigger, backend)`。
- §0 第 8 至 10 条的实际签名与本文不符，尤其是 `_run_atomic` 已被改写、`ClaimGate` 没有 `allow_manual`、`claim_next` 不接受 `allow_manual`。
- 设备端点不再走 `_run_atomic`，或点击不再经过 `DeviceManager.click` 的 720 换算。
- 需要新增本文没列的错误码、端点、配置键、表列或枚举取值。
- 需要让 `convert` 产出 Copilot 作业草稿才能满足用户，或 N 节点批量注册无法在不改 v2 `register_custom_task` 旧调用行为的前提下完成。
- 真机验证表明画布坐标映射与 `DeviceManager.click` 的换算差超过 2 像素。
- 录制期间手动流水线仍然被执行，或进程重启后仍有 `recording` 行；前端无法从 `GET /api/device/status` 拿到分辨率。

## 11. 需要补进契约的名字

| 名字 | 种类 | 用途 | 补进 |
|---|---|---|---|
| `DemonstrationStatus` | 枚举 | `demonstration.status` 的四个取值 | 03 §13 |
| `demonstration.steps` 元素、`converted_to` 形状、保留策略 | JSON 形状与保留 | 步骤、转换记录、不自动删除 | 03 §12、§14（指向本文 §4.1） |
| `DEMONSTRATION_NOT_FOUND`、`DEMONSTRATION_STATE_INVALID` | 错误码 | 404、409 | 14 §4.23（04 §5 已列） |
| `CUSTOM_TASK_INVALID` 改 422 | 错误码状态 | 保持 v2 绑定 | 04 §5 |
| `demonstration.enabled` | 配置 | 示教开关，默认 true | 04 §6、settings、schema、template |
| `DemonstrationService`、`app.state.demonstration_service` | 服务与装配 | 录制状态机、编译、转换；端点与采集共用 | 02 §2.2、§5 |
| `maa_api/agent/demonstrations/`、`agent/custom_task/`、`api/routers/demonstrations.py` | 模块 | `service.py`、`compiler.py`、`capture.py`、`rules.py`、四个端点 | 02 §4.1 |
| `web/src/features/demonstrations/`、`/more/demonstrations` | 前端 | 示教页与画布 | 11 §3 |
| `scripts/demonstration_smoke.py` | 冒烟 | 端到端验收 | 02 §4.3 |
| `register_custom_task` 的 `tasks`、两个新工具的参数与返回、`general` 的工具与版本 | 工具与代理契约 | 一次注册整条链；新活动起草与校验；版本 8 | 05 §6.1、§3.2、`general.md` |
| `convert(register=true)` 的写资源例外 | 决策 | v2 原则的唯一例外 | 13 §8 新条目 |
| `archive_screenshot` 的 M89 复用 | 模块 | 采集归档 | 02 §4.1（M86 已登记，补复用说明） |
