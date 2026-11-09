# M90 产物与生成式 UI（规格）

> 阶段：P4　依赖：M89　分支：`artifacts-genui`　迁移：无
> 本文是 M90 的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)。

## 0. 前置检查

本机没有 ripgrep，下表全部用 `grep`；命令返回 1 表示没有匹配，按「期望」列判断。

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在 dev 最新提交上新建的分支，工作区干净 | `git status -sb` | 第一行 `## artifacts-genui`，没有改动 |
| 2 | M89 已合并 | `git tag -l 'v3-m89'` | `v3-m89` |
| 3 | M89 台账与 capability 基线 | `ls docs/v3/ledgers/m89-*.md`；`grep -n "capability" docs/v3/ledgers/m89-*.md` | 1 个台账文件；至少 1 行 capability 记录，抄下通过率与场景数 |
| 4 | 产物服务实际签名 | `grep -n "async def put\|async def get\|async def update(" maa_api/agent/artifacts/service.py` | 3 行；抄下签名：`put(*, ..., body: bytes, ...) -> Artifact`、`get(artifact_id, *, max_chars=None) -> ArtifactDetail`、`update(artifact_id, *, content: str, meta: dict) -> Artifact` |
| 5 | 详情 wire 现状 | `grep -n "class ArtifactOut\|class ArtifactDetailOut" -A 14 maa_api/api/schemas/agent_v3.py` | 两个类；`ArtifactDetailOut` 只有 `content`、`truncated`，没有 `meta`（T4 加） |
| 6 | `ArtifactKind` 有 `form`、`dashboard` | `grep -n "FORM = \|DASHBOARD = " maa_api/domain/enums.py` | 2 行 |
| 7 | M75 四个产物端点在 OpenAPI 快照里 | 附录 A 的 A1 | 末行 `MISSING 0` |
| 8 | M72 会话文件都在 | `ls web/src/features/console/conversation/ConversationView.tsx web/src/features/console/conversation/LiveTurnView.tsx web/src/features/console/conversation/transcript-v3.ts web/src/features/console/conversation/live-turn.ts web/src/features/console/conversation/sse.ts` | 5 个文件 |
| 9 | 工具目录当前计数 | `.venv/bin/python -c "from maa_api.agent.tools import build_registry as b; r=b(); print(len(r.list()), sum(1 for d in r.list() if d.model_visible))"` | `110 109`（M89 台账口径）；不是这个值时停下来问（§10） |
| 10 | 前端产物 key 的现状 | `grep -n "artifacts" web/src/api/keys.ts` | `artifacts(ticketId)`、`artifactsAll()`、`artifact(id)` 三行，都是函数；没有 `artifactsList` |
| 11 | `parseChartCard` 还没导出 | `grep -n "^function parseChartCard" web/src/features/agent/media.ts` | 1 行，行首没有 `export` |
| 12 | `create_view` 还没出现 | `grep -rn "create_view" maa_api tests web/src; echo $?` | 没有输出，退出码 1 |
| 13 | 四个 Worker 定义存在 | `ls maa_api/agent/specs/builtin/general.md maa_api/agent/specs/builtin/analyst.md maa_api/agent/specs/builtin/planner.md maa_api/agent/specs/builtin/copilot.md` | 4 个文件 |
| 14 | 工单消息写入函数的实际签名 | `grep -n "async def post_user_message" -A 6 maa_api/agent/workers/service.py` | `(ticket_id, content, mentions, *, notify_content=None)`；第三个位置参数 `mentions` 必填。用 `WorkerService.post_user_message`，不要直接调 `InboxService.put_user_message` |
| 15 | OpenAPI 快照与前端生成类型是当前值 | `.venv/bin/python scripts/dump_openapi.py --check; echo $?`；`cd web && pnpm gen:api:check; echo $?; cd ..` | 两个 0 |
| 16 | 计数基线（附录 A5） | 附录 A5 的脚本 | `OPERATIONS 190 AGENT 91`、`ERRORS 173 172`、`TABLES 58`、`SETTINGS 144 133`；差一个就按 §10 停下来问 |
| 17 | 四个 Worker 当前版本 | `grep -n "^version:" maa_api/agent/specs/builtin/{general,analyst,planner,copilot}.md` | 4 行；抄下四个值，T3 各 +1 |

表格单元里的 `\|` 是 Markdown 转义，复制到 shell 时去掉反斜杠。

## 1. 背景与目标

M75 让 Worker 能写笔记、外置大输出、存报告，但只能产出文本与 v2 的四种媒体卡。很多场景需要一张能看能填的视图：资源规划要对比几个关卡的期望理智，作业研究要让用户在两三份作业里挑一份，养成规划要用户确认几个参数。本里程碑加生成式 UI，做完后：

1. Worker 调用 `create_view` 生成一张视图，存成产物（`kind=form` 或 `dashboard`）。
2. 视图只能用白名单组件：标题、文本、关键数字、表格、图表（复用 v2 的 ECharts 媒体卡）、表单字段（select、number、text、toggle）、提交按钮。服务端按 §4.3.1 的模型与附加规则校验，不合法时工具返回 `TOOL_ARGS_INVALID` 与出错路径，模型据此改正。
3. 前端在工单会话里就地渲染视图，产物页也能打开；渲染器不执行任何脚本、不加载任何外链资源、不渲染 HTML 与 Markdown。
4. 表单经 `POST /api/agent/artifacts/{artifact_id}/submit` 提交：服务端按视图定义校验，作为一条用户消息写进工单 Worker 的收件箱并唤醒它。
5. 「更多 › 产物」列出全部产物，可筛选、置顶、下载、查看。
6. 12 §4 的门禁全部通过，capability 评测不低于 M89 台账记录的基线。

## 2. 非目标

- 视图编辑器、拖拽、主题、自定义组件；图片、链接、HTML、Markdown、iframe、文件上传字段。
- 视图数据实时刷新：视图是生成时的快照，要新数据就再生成一张。
- 总控、子代理、后台代理不使用 `create_view`；只给 `general`、`analyst`、`planner`、`copilot` 四个 Worker。
- 表单答案代替确认：答案只是用户的意见，消耗类与破坏类操作照旧逐次确认。
- 新表、新错误码、新配置项、新依赖（不引入 `jsonschema`，校验用 Pydantic）；不产生迁移。
- 改 M75 已有产物端点的语义。只允许给 `ArtifactDetailOut` 增加 `meta: dict[str, Any]` 这一处 additive 扩展；`ArtifactOut` 列表项不加。
- 加 SSE 或 WebSocket 事件。视图卡只从 `tool_result` 挂；`artifact_created` 继续只走 WS `agent_event`，只用于失效产物列表。
- 提交记录单独建表、单独写审计或单独发通知；提交记录只写进产物 `meta`。

## 3. 相关文档

- 前端信息架构与实时层：[11](../11-前端指挥台.md) §2.3（`/more/artifacts` 路由）、§4.2（产物页）、§6.1（SSE 事件表）、§6.3（WS 到 query 失效）、§9（页面与函数名）。
- 端点与事件：[04](../04-接口与事件协议.md) §2.3（M75 产物端点）、§2.5（M90 行）、§3（SSE v3）、§4.2（`agent_event` 已有频道）。
- 工具与代理定义：[05](../05-运行时与代理定义.md) §3.2（四个 Worker 的工具列）、§6.1（`create_view` 行与工具计数）、§7（提示词分层）。
- 数据模型：[03](../03-数据模型.md) §8.2（`artifact`）、§13（`ArtifactKind`）。
- 架构与对话路径：[02](../02-系统架构.md) §4.1（`artifacts/` 文件）、§6.3（工单内直接对话）。
- 决策记录：[13](../13-决策记录.md) §8.155（`ArtifactService.put(body: bytes)`/`update`）、§8.173（计划待批准上指挥台）、§8.175（删除消耗护栏与旧会话）、§8.176（待你处理进主会话）。
- 文案：[文案规范](../../copy-audit/规范-文案规范.md)。

既有代码入口（路径 + 符号名 + 约行号；行号会漂移，用符号名定位）：

| 位置 | 符号 | 用途 |
|---|---|---|
| `maa_api/agent/artifacts/service.py` | `ArtifactService.put`（约 97）、`get`（约 195）、`update`（约 269）、`_broadcast_created`（约 151）、`_not_found`（约 412） | 写与读视图产物、提交记录 |
| `maa_api/api/schemas/agent_v3.py` | `ArtifactOut`、`ArtifactDetailOut`、`artifact_detail_out`（约 347） | 详情加 `meta` |
| `maa_api/api/routers/artifacts.py` | `_artifact_service`（约 40）、四个端点（约 61、96、116、132） | 加提交端点与服务访问器 |
| `maa_api/agent/tools/__init__.py` | `build_registry()`（约 13） | 注册 `artifacts` 组 |
| `maa_api/agent/tools/_services.py` | `v3_service(run, ctx, name, label)`（约 33）、`app_state` | 取 `app.state.artifact_service` |
| `maa_api/agent/harness/context.py` | `RunContext`（约 181，含 `ticket_id`/`run_id`）、`require_run`（约 45） | 取当前 Worker 运行的工单 |
| `maa_api/agent/tools/workspace.py` | `update_plan`（约 253）的 `require_run` + `v3_service` 写法 | 新工具照此取服务 |
| `maa_api/agent/tools/media.py` | `SendChartParams`（约 122）、`_chart_spec`（约 335）、`MAX_CHART_POINTS`（约 56）、`MAX_CHART_BYTES`（约 57） | 图表组件的形状与上限 |
| `maa_api/agent/workers/service.py` | `post_user_message`（约 431）、`require_open_ticket`（约 389）、`ensure_ready`（约 402） | 提交写入 Worker 收件箱 |
| `web/src/features/agent/media.ts` | `parseChartCard`（约 348，未导出）、`ChartCardData`（约 97）、`MEDIA_LIMITS`（约 16） | 图表解析与上限 |
| `web/src/features/agent/MediaCards.tsx` | `ChartCard`（约 362） | 图表渲染 |
| `web/src/features/tickets/ArtifactList.tsx` | `ARTIFACT_KIND_LABELS`（约 23）、`formatArtifactSize`（约 35）、抽屉（约 90） | M75 产物列表，复用并统一文案 |
| `web/src/features/tickets/workspace-api.ts` | `ArtifactView`（约 15）、`artifactsQueryOptions`（约 62）、`artifactDetailQueryOptions`（约 77）、`saveArtifactFile`（约 108） | 复用查询与下载 |
| `web/src/api/keys.ts` | `artifacts(ticketId)`（约 51）、`artifactsAll()`（约 54）、`artifact(id)`（约 56） | 复用三个 key，只加 `artifactsList` |
| `web/src/realtime/query-bridge.ts` | `artifact_created` → `artifactsAll()`（约 110 到 119） | 不改 |
| `web/src/features/agent/transcript.ts` | `ToolView`（约 7）、`toolBody`（约 100）、`liveToolView`（约 110） | `tool.result` 是 `{result}`/`{error}` 信封 |
| `web/src/features/console/conversation/transcript-v3.ts` | `ConversationEntry` 的 `tool` 分支（约 15）、`toEntry`（约 61） | 取 `artifact_id` |
| `web/src/features/console/conversation/ConversationView.tsx` | `ConversationViewProps`（约 40）、`ConversationEntryView`（约 76） | 挂视图卡、传 `ticketEnded` |
| `web/src/features/console/conversation/LiveTurnView.tsx` | 工具段渲染（约 57） | 实时工具结果挂视图卡 |
| `web/src/features/tickets/TicketDetailPage.tsx` | `ENDED_STATUSES`（约 50）、`ConversationView`（约 262）、`ArtifactList`（约 382） | 传终态、保留右栏列表 |
| `tests/agent/test_tool_catalog.py` | `EXPECTED_TOOLS`（约 11）、四处计数（约 135、136、155、156） | 工具全集 |
| `tests/agent/test_builtin_workers.py` | `WORKERS` 工具元组（约 47、77、135）、版本断言 | T3 同步 |
| `tests/agent/specs/test_m70_specs.py` | `general` 工具集合等式（约 94） | T3 同步 |
| `tests/api/test_agent_specs_router.py` | `EXPECTED_VERSIONS`（约 68） | T3 同步 |

## 4. 设计

### 4.1 数据模型

不加表、不加列、不改 SQLModel，不产生迁移；迁移 head 仍是 `0042_demonstration`（M86 的 0041、M89 的 0042 之后），[03 §2](../03-数据模型.md) 不变。视图存进 `artifact`（03 §8.2）：

| 列 | 值 |
|---|---|
| `kind` | 有至少一个表单字段时 `form`，否则 `dashboard` |
| `title` | 视图的 `title`（≤ 80 字，`artifact.title` 上限 120） |
| `mime` | `application/vnd.maa.view+json` |
| `storage` | `db`（视图序列化后不超过 64 KiB，低于 256 KiB 的库内阈值） |
| `content` | 校验后的视图 JSON，`spec.model_dump_json(exclude_none=True)` 的 UTF-8 字节 |
| `session_id` | 调用者运行所属会话：`RunContext.session_id` |
| `ticket_id` | 调用者运行所属工单：`RunContext.ticket_id` |
| `run_id` | 调用者运行：`RunContext.run_id` |
| `meta` | `{"view_version": 1, "submissions": [], "last_submitted_at": null}`；每次提交在 `submissions` 末尾追加 `{"at": "<ISO Z>", "values": {...}}`，只留最近 5 次，并更新 `last_submitted_at` |

`meta` 里已有的简报键（`brief`、`brief_date`、`status`、`origin`、`ticket_id`、`facts_markdown`）原样保留，写入提交记录时不能覆盖它们。`ArtifactDetailOut.meta` 对外返回整个对象，含这些内部键（§4.3.4）。

### 4.2 模块与文件

后端：

| 路径 | 新建或修改 | 职责 |
|---|---|---|
| `maa_api/agent/artifacts/views.py` | 新 | 视图模型、`validate_view`、`validate_submission`、`render_submission`、`ViewInvalid`、`VIEW_MIME` 与上限常量 |
| `maa_api/agent/tools/artifacts.py` | 新 | `create_view` 工具与 `register_tools` |
| `maa_api/agent/tools/__init__.py` | 改 | import `register_artifacts_tools`，在 `register_goal_tools(registry)` 之后调用 |
| `maa_api/api/routers/artifacts.py` | 改 | `_worker_service`、`ArtifactFormSubmit`、`ArtifactFormSubmitResult`、`submit_artifact_form` |
| `maa_api/api/schemas/agent_v3.py` | 改 | `ArtifactDetailOut.meta` 与 `artifact_detail_out` 传入 `meta` |
| `maa_api/domain/enums.py` | 改 | `ArtifactKind` 的 docstring 改成「note、spill、report、chart、table、file、form、dashboard」 |
| `maa_api/agent/specs/builtin/general.md`、`analyst.md`、`planner.md`、`copilot.md` | 改 | `tools` 末尾加 `create_view`，`version` 加 1，正文追加一段（§4.7） |
| `maa_api/agent/specs/common.md` | 改 | 加【表单提交】前缀的说明（§4.7） |
| `tests/agent/test_views.py`、`tests/agent/test_create_view_tool.py`、`tests/api/test_artifact_form_submit.py` | 新 | §6 |
| `tests/agent/test_tool_catalog.py`、`tests/agent/test_builtin_workers.py`、`tests/agent/specs/test_m70_specs.py`、`tests/api/test_agent_specs_router.py`、`tests/api/test_agent_workspace.py` | 改 | 工具全集、版本、详情 `meta` |
| `docs/v3/14-现行接口清单.md` | 改 | agent 小节加一行与计数（T4） |

`views.py` 的模型与函数签名如下；类名与字段照抄，附加规则见 §4.3.1。

```python
# maa_api/agent/artifacts/views.py
"""生成式视图 v1：白名单组件的模型、附加规则、表单提交的校验与渲染。"""

from __future__ import annotations

import json
import math
from typing import Annotated, Any, Literal, Mapping, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

VIEW_MIME = "application/vnd.maa.view+json"
VIEW_VERSION = 1
MAX_COMPONENTS = 40
MAX_FIELDS = 20
MAX_VIEW_BYTES = 64 * 1024
MAX_SUBMISSION_BYTES = 4 * 1024
MAX_SUBMISSIONS_KEPT = 5
MAX_CHART_POINTS = 600
MAX_CHART_BYTES = 24 * 1024
FIELD_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"

ShortText = Annotated[str, Field(min_length=1, max_length=80)]
Label = Annotated[str, Field(min_length=1, max_length=40)]
Name = Annotated[str, Field(pattern=FIELD_NAME_PATTERN)]
Description = Annotated[str, Field(max_length=120)]
Finite = Annotated[float, Field(allow_inf_nan=False)]


class ViewInvalid(ValueError):
    """视图或提交不合法；``errors`` 每项是 ``{"path": "...", "message": "..."}``，最多 20 项。"""

    def __init__(self, errors: list[dict[str, str]]) -> None:
        self.errors = errors[:20]
        super().__init__("；".join(f"{item['path']}：{item['message']}" for item in self.errors))


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class HeadingComponent(_Base):
    type: Literal["heading"] = "heading"
    text: ShortText
    level: Literal[2, 3] = 2


class TextComponent(_Base):
    type: Literal["text"] = "text"
    text: Annotated[str, Field(min_length=1, max_length=2000)]
    tone: Literal["default", "muted", "warning"] = "default"


class StatItem(_Base):
    label: Label
    value: Union[Finite, Annotated[str, Field(min_length=1, max_length=24)]]
    unit: Annotated[str | None, Field(max_length=16)] = None
    delta: Finite | None = None
    hint: Annotated[str | None, Field(max_length=80)] = None


class StatsComponent(_Base):
    type: Literal["stats"] = "stats"
    items: Annotated[list[StatItem], Field(min_length=1, max_length=6)]


class TableColumn(_Base):
    key: Name
    label: Label
    align: Literal["left", "right"] = "left"
    format: Literal["text", "number", "percent"] = "text"


CellValue = Union[Annotated[str, Field(max_length=200)], Finite, bool, None]


class TableComponent(_Base):
    type: Literal["table"] = "table"
    title: ShortText | None = None
    columns: Annotated[list[TableColumn], Field(min_length=1, max_length=12)]
    rows: Annotated[list[dict[Name, CellValue]], Field(max_length=200)]
    caption: Annotated[str | None, Field(max_length=300)] = None


class ChartSeries(_Base):
    name: Label
    values: Annotated[list[Finite | None], Field(min_length=1, max_length=200)]


class ChartSpec(_Base):
    chart_type: Literal["line", "bar", "pie"]
    title: ShortText
    categories: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=40)]],
        Field(min_length=1, max_length=200),
    ]
    series: Annotated[list[ChartSeries], Field(min_length=1, max_length=6)]
    stacked: bool = False
    x_label: Annotated[str | None, Field(max_length=40)] = None
    y_label: Annotated[str | None, Field(max_length=40)] = None
    unit: Annotated[str | None, Field(max_length=16)] = None
    source: Annotated[str | None, Field(max_length=200)] = None


class ChartComponent(_Base):
    type: Literal["chart"] = "chart"
    chart: ChartSpec
    caption: Annotated[str | None, Field(max_length=300)] = None


class SelectOption(_Base):
    value: Annotated[str, Field(min_length=1, max_length=64)]
    label: Label
    description: Description | None = None


class SelectField(_Base):
    type: Literal["field"] = "field"
    field: Literal["select"] = "select"
    name: Name
    label: Label
    description: Description | None = None
    required: bool = True
    options: Annotated[list[SelectOption], Field(min_length=1, max_length=20)]
    multiple: bool = False
    default: str | list[str] | None = None


class NumberField(_Base):
    type: Literal["field"] = "field"
    field: Literal["number"] = "number"
    name: Name
    label: Label
    description: Description | None = None
    required: bool = True
    min: Finite | None = None
    max: Finite | None = None
    step: Annotated[Finite | None, Field(gt=0)] = None
    unit: Annotated[str | None, Field(max_length=16)] = None
    default: Finite | None = None


class TextField(_Base):
    type: Literal["field"] = "field"
    field: Literal["text"] = "text"
    name: Name
    label: Label
    description: Description | None = None
    required: bool = True
    max_length: Annotated[int, Field(ge=1, le=500)] = 200
    multiline: bool = False
    placeholder: Annotated[str | None, Field(max_length=60)] = None
    default: Annotated[str | None, Field(max_length=500)] = None


class ToggleField(_Base):
    type: Literal["field"] = "field"
    field: Literal["toggle"] = "toggle"
    name: Name
    label: Label
    description: Description | None = None
    required: bool = False
    default: bool = False


FieldBlock = Annotated[
    Union[SelectField, NumberField, TextField, ToggleField], Field(discriminator="field")
]


class SubmitComponent(_Base):
    type: Literal["submit"] = "submit"
    label: Annotated[str, Field(min_length=1, max_length=12)] = "提交"


Component = Annotated[
    Union[
        HeadingComponent,
        TextComponent,
        StatsComponent,
        TableComponent,
        ChartComponent,
        FieldBlock,
        SubmitComponent,
    ],
    Field(discriminator="type"),
]


class ViewSpec(_Base):
    version: Literal[1]
    title: ShortText
    description: Annotated[str | None, Field(max_length=300)] = None
    components: Annotated[list[Component], Field(min_length=1, max_length=MAX_COMPONENTS)]


_VIEW_ADAPTER = TypeAdapter(ViewSpec)


def validate_view(raw: Any) -> ViewSpec:
    """先 Pydantic 校验，再查 §4.3.1 的附加规则；失败抛 ``ViewInvalid``。"""
    ...


def view_kind(spec: ViewSpec) -> Literal["form", "dashboard"]:
    """有至少一个表单字段返回 ``form``，否则 ``dashboard``。"""
    ...


def view_fields(spec: ViewSpec) -> list[SelectField | NumberField | TextField | ToggleField]:
    """按组件顺序返回全部表单字段。"""
    ...


def view_json_schema() -> dict[str, Any]:
    """``TypeAdapter(ViewSpec).json_schema()``；只给测试与文档核对，不进工具声明。"""
    ...


def validate_submission(
    spec: ViewSpec, values: Mapping[str, Any]
) -> dict[str, Any]:
    """按字段定义校验并规整提交值；失败抛 ``ViewInvalid``，path 是字段名。"""
    ...


def render_submission(spec: ViewSpec, values: Mapping[str, Any], *, artifact_id: str) -> str:
    """写给 Worker 的那条用户消息正文，格式见 §4.5.3。"""
    ...
```

工具与注册：

```python
# maa_api/agent/tools/artifacts.py
"""产物工具：把白名单组件组成的视图存成 artifact（M90 规格 §4.3.2、§4.5.1）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from maa_api.agent.artifacts.views import (
    VIEW_MIME,
    ViewSpec,
    view_fields,
    view_kind,
    validate_view,
)
from maa_api.agent.harness.context import require_run
from maa_api.agent.registry import ToolContext, ToolDefinition, ToolRegistry, ToolRisk
from maa_api.agent.tools._services import v3_service
from maa_api.domain.errors import AppError, ErrorCode

CREATE_VIEW_DESCRIPTION = """要用户一眼看懂一组数字或几项对比，或者要用户在几个选项里做决定、填几个参数时，生成一张视图。组件只能用 heading（标题）、text（文本）、stats（关键数字）、table（表格）、chart（图表）、field（表单字段：select、number、text、toggle）与 submit（提交按钮）；最多 40 个组件、20 个字段、64 KiB。有表单字段时最后一个组件必须是 submit，没有字段时不能有 submit。chart 组件形如 {"type":"chart","caption":"…","chart":{"chart_type":"line|bar|pie","title":"…","categories":[…],"series":[{"name":"…","values":[…]}],"stacked":false,"x_label":"…","y_label":"…","unit":"…","source":"…"}}，chart 内的字段与 send_chart 同名但平铺在 chart 下，caption 在组件层；总点数不超过 600，序列化不超过 24 KiB。用户提交后，答案会作为一条【表单提交】消息发给你；表单答案不等于确认。视图不代替汇报，本轮照常以 report 收尾。"""


class CreateViewParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    view: dict[str, Any] = Field(
        description="视图定义，组件只能是 heading、text、stats、table、chart、field、submit"
    )


async def create_view(params: CreateViewParams, ctx: ToolContext) -> dict[str, Any]:
    """校验视图，按当前运行的工单与会话写一份 artifact，返回 id 与种类。"""
    run = require_run(kinds={"worker"}, message="create_view 只能在 Worker 里调用")
    if not run.ticket_id:
        raise AppError(ErrorCode.INVALID_PARAMETER, "只能在工单里生成视图")
    try:
        spec: ViewSpec = validate_view(params.view)
    except ValueError as exc:
        errors = getattr(exc, "errors", [])
        head = "；".join(
            f"{item['path']}：{item['message']}" if item["path"] else item["message"]
            for item in errors[:3]
        )
        raise AppError(
            ErrorCode.TOOL_ARGS_INVALID,
            f"视图不合法：{head}",
            {"errors": errors[:20]},
        ) from None
    service = v3_service(run, ctx, "artifact_service", "产物服务")
    body = spec.model_dump_json(exclude_none=True).encode("utf-8")
    artifact = await service.put(
        kind=view_kind(spec),
        title=spec.title,
        body=body,
        mime=VIEW_MIME,
        session_id=run.session_id,
        ticket_id=run.ticket_id,
        run_id=run.run_id,
        meta={"view_version": 1, "submissions": [], "last_submitted_at": None},
    )
    return {
        "artifact_id": str(artifact.id),
        "kind": view_kind(spec),
        "title": spec.title,
        "field_count": len(view_fields(spec)),
    }


def register_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolDefinition(
            name="create_view",
            group="artifacts",
            risk=ToolRisk.SAFE,
            description=CREATE_VIEW_DESCRIPTION,
            params_model=CreateViewParams,
            handler=create_view,
            read_only=False,
            refetchable=False,
            device="none",
            untrusted_output=False,
            rest_visible=False,
        )
    )
```

`maa_api/agent/tools/__init__.py` 的改动：import 区加
`from maa_api.agent.tools.artifacts import register_tools as register_artifacts_tools`；在
`register_goal_tools(registry)` 之后加注释与调用：

```python
# M90：产物视图工具（规格 §4.2），接在目标工具之后，不进 REST 目录。
register_artifacts_tools(registry)
```

路由新增（文件顶部补 `from pydantic import BaseModel, ConfigDict, Field`；其余 import 已存在）：

```python
# maa_api/api/routers/artifacts.py（新增部分）
class ArtifactFormSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any] = Field(default_factory=dict)


class ArtifactFormSubmitResult(BaseModel):
    artifact_id: str
    ticket_id: str
    submitted_at: str


def _worker_service(request: Request) -> Any:
    """取 Worker 服务；未装配时与 tickets 路由同一口径。"""
    service = getattr(request.app.state, "worker_service", None)
    if service is None:
        raise AppError(ErrorCode.AGENT_DISABLED, "工单服务还在启动中，请稍后重试")
    return service


@router.post(
    "/{artifact_id}/submit",
    response_model=ArtifactFormSubmitResult,
    summary="提交生成式表单",
    responses=error_responses(
        "ARTIFACT_NOT_FOUND",
        "INVALID_PARAMETER",
        "TICKET_NOT_FOUND",
        "TICKET_ENDED",
        "SERVICE_UNAVAILABLE",
        "AGENT_DISABLED",
        "LLM_NOT_CONFIGURED",
        "LLM_ENCRYPTION_KEY_INVALID",
        "VALIDATION_ERROR",
        "UNAUTHORIZED",
    ),
    dependencies=[Depends(require_auth)],
)
async def submit_artifact_form(
    request: Request,
    payload: ArtifactFormSubmit,
    artifact_id: str = Path(min_length=1, max_length=36, description="产物 id"),
) -> ArtifactFormSubmitResult:
    ...
```

`ArtifactDetailOut` 与构造函数：

```python
class ArtifactDetailOut(ArtifactOut):
    content: str
    truncated: bool
    meta: dict[str, Any]


def artifact_detail_out(artifact: Artifact, *, content: str, truncated: bool) -> ArtifactDetailOut:
    return ArtifactDetailOut(
        **artifact_out(artifact).model_dump(),
        content=content,
        truncated=truncated,
        meta=dict(artifact.meta or {}),
    )
```

前端（路径都在 `web/src/` 下）：

| 路径 | 新建或修改 | 职责 |
|---|---|---|
| `features/agent/media.ts` | 改 | `function parseChartCard` 前加 `export`，别的不动 |
| `features/artifacts/view/schema.ts` | 新 | 视图类型、`parseView`、初值与就地校验 |
| `features/artifacts/view/ViewRenderer.tsx` | 新 | 按组件渲染 |
| `features/artifacts/view/ViewField.tsx` | 新 | 四种表单字段 |
| `features/artifacts/view/ViewTable.tsx`、`ViewStats.tsx` | 新 | 表格、关键数字 |
| `features/artifacts/ViewArtifactBody.tsx` | 新 | 读视图、表单状态与提交（卡片与详情页共用） |
| `features/artifacts/ViewArtifactCard.tsx` | 新 | 会话里的视图卡 |
| `features/artifacts/ArtifactChip.tsx` | 新 | 非视图产物的胶囊；M75 没有同等组件，不复用 |
| `features/artifacts/api.ts`、`format.ts` | 新 | 列表查询、置顶、提交、下载名与种类文案 |
| `features/artifacts/ArtifactsPage.tsx`、`ArtifactDetailPage.tsx` | 新 | 产物页 |
| `features/console/conversation/transcript-v3.ts`、`ConversationView.tsx`、`LiveTurnView.tsx` | 改 | 挂视图卡（§4.6.4） |
| `features/tickets/ArtifactList.tsx`、`workspace-api.ts` | 改 | 统一 `看板`、`已置顶`、截断提示、下载失败文案与下载扩展名；视图产物显示「在产物页打开」 |
| `api/keys.ts`、`components/layout/nav.ts`、`routes/router.tsx` | 改 | key、导航、路由 |
| `features/console/test-utils.tsx` | 改 | mock 加 `POST /api/agent/artifacts/{id}/submit` |

前端与 M75 的边界（只保留一份实现）：

| 既有符号 | M90 的处理 |
|---|---|
| `ArtifactView`、`ArtifactDetailView`（`features/tickets/workspace-api.ts`） | 直接复用生成类型，不另定义同名类型，也不写 `toArtifactView`；列表读 `id`、`kind`、`title`、`mime`、`size_bytes`、`ticket_id`、`pinned`、`created_at`，详情另读 `content`、`truncated`、`meta` |
| `artifactsQueryOptions(ticketId)` | 保留给工单右栏；产物页用新的 `artifactsListInfiniteQuery(filters)`，不复制工单筛选逻辑 |
| `artifactDetailQueryOptions(id)` | 卡片与详情页直接复用 |
| `saveArtifactFile(artifact)` | 保留在 `workspace-api.ts`，文件名改调 `downloadName`（§4.6.1） |
| `ARTIFACT_KIND_LABELS`、`formatArtifactSize` | 从 `ArtifactList.tsx` 移到 `features/artifacts/format.ts`，`ArtifactList.tsx` 改为 import；`dashboard` 统一为「看板」 |
| `ArtifactChip` | 新建；M75 只有列表与抽屉，没有胶囊 |

### 4.3 接口

#### 4.3.1 视图 JSON Schema（v1）

服务端的 Pydantic 模型（§4.2）与下面的 schema 一一对应；前端 `parseView` 用同样的上限。

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "MAA-API 生成式视图 v1",
  "type": "object",
  "additionalProperties": false,
  "required": ["version", "title", "components"],
  "properties": {
    "version": { "const": 1 },
    "title": { "$ref": "#/$defs/shortText" },
    "description": { "type": "string", "maxLength": 300 },
    "components": { "type": "array", "minItems": 1, "maxItems": 40, "items": { "$ref": "#/$defs/component" } }
  },
  "$defs": {
    "shortText": { "type": "string", "minLength": 1, "maxLength": 80 },
    "label": { "type": "string", "minLength": 1, "maxLength": 40 },
    "name": { "type": "string", "pattern": "^[a-z][a-z0-9_]{0,31}$" },
    "component": {
      "oneOf": [
        { "$ref": "#/$defs/heading" }, { "$ref": "#/$defs/text" }, { "$ref": "#/$defs/stats" },
        { "$ref": "#/$defs/table" }, { "$ref": "#/$defs/chart" }, { "$ref": "#/$defs/field" },
        { "$ref": "#/$defs/submit" }
      ]
    },
    "heading": {
      "type": "object", "additionalProperties": false, "required": ["type", "text"],
      "properties": {
        "type": { "const": "heading" },
        "text": { "$ref": "#/$defs/shortText" },
        "level": { "enum": [2, 3], "default": 2 }
      }
    },
    "text": {
      "type": "object", "additionalProperties": false, "required": ["type", "text"],
      "properties": {
        "type": { "const": "text" },
        "text": { "type": "string", "minLength": 1, "maxLength": 2000 },
        "tone": { "enum": ["default", "muted", "warning"], "default": "default" }
      }
    },
    "stats": {
      "type": "object", "additionalProperties": false, "required": ["type", "items"],
      "properties": {
        "type": { "const": "stats" },
        "items": { "type": "array", "minItems": 1, "maxItems": 6, "items": { "$ref": "#/$defs/stat" } }
      }
    },
    "stat": {
      "type": "object", "additionalProperties": false, "required": ["label", "value"],
      "properties": {
        "label": { "$ref": "#/$defs/label" },
        "value": { "oneOf": [{ "type": "number" }, { "type": "string", "minLength": 1, "maxLength": 24 }] },
        "unit": { "type": "string", "maxLength": 16 },
        "delta": { "type": "number" },
        "hint": { "type": "string", "maxLength": 80 }
      }
    },
    "table": {
      "type": "object", "additionalProperties": false, "required": ["type", "columns", "rows"],
      "properties": {
        "type": { "const": "table" },
        "title": { "$ref": "#/$defs/shortText" },
        "columns": { "type": "array", "minItems": 1, "maxItems": 12, "items": { "$ref": "#/$defs/column" } },
        "rows": {
          "type": "array", "maxItems": 200,
          "items": { "type": "object", "propertyNames": { "$ref": "#/$defs/name" }, "additionalProperties": { "$ref": "#/$defs/cell" } }
        },
        "caption": { "type": "string", "maxLength": 300 }
      }
    },
    "column": {
      "type": "object", "additionalProperties": false, "required": ["key", "label"],
      "properties": {
        "key": { "$ref": "#/$defs/name" },
        "label": { "$ref": "#/$defs/label" },
        "align": { "enum": ["left", "right"], "default": "left" },
        "format": { "enum": ["text", "number", "percent"], "default": "text" }
      }
    },
    "cell": { "oneOf": [{ "type": "string", "maxLength": 200 }, { "type": "number" }, { "type": "boolean" }, { "type": "null" }] },
    "chart": {
      "type": "object", "additionalProperties": false, "required": ["type", "chart"],
      "properties": {
        "type": { "const": "chart" },
        "chart": { "$ref": "#/$defs/chartSpec" },
        "caption": { "type": "string", "maxLength": 300 }
      }
    },
    "chartSpec": {
      "type": "object", "additionalProperties": false, "required": ["chart_type", "title", "categories", "series"],
      "properties": {
        "chart_type": { "enum": ["line", "bar", "pie"] },
        "title": { "$ref": "#/$defs/shortText" },
        "categories": { "type": "array", "minItems": 1, "maxItems": 200, "items": { "type": "string", "maxLength": 40 } },
        "series": { "type": "array", "minItems": 1, "maxItems": 6, "items": { "$ref": "#/$defs/series" } },
        "stacked": { "type": "boolean", "default": false },
        "x_label": { "type": "string", "maxLength": 40 },
        "y_label": { "type": "string", "maxLength": 40 },
        "unit": { "type": "string", "maxLength": 16 },
        "source": { "type": "string", "maxLength": 200 }
      }
    },
    "series": {
      "type": "object", "additionalProperties": false, "required": ["name", "values"],
      "properties": {
        "name": { "$ref": "#/$defs/label" },
        "values": { "type": "array", "minItems": 1, "maxItems": 200, "items": { "type": ["number", "null"] } }
      }
    },
    "field": {
      "oneOf": [
        { "$ref": "#/$defs/selectField" }, { "$ref": "#/$defs/numberField" },
        { "$ref": "#/$defs/textField" }, { "$ref": "#/$defs/toggleField" }
      ]
    },
    "selectField": {
      "type": "object", "additionalProperties": false, "required": ["type", "field", "name", "label", "options"],
      "properties": {
        "type": { "const": "field" }, "field": { "const": "select" },
        "name": { "$ref": "#/$defs/name" }, "label": { "$ref": "#/$defs/label" },
        "description": { "type": "string", "maxLength": 120 },
        "required": { "type": "boolean", "default": true },
        "options": { "type": "array", "minItems": 1, "maxItems": 20, "items": { "$ref": "#/$defs/option" } },
        "multiple": { "type": "boolean", "default": false },
        "default": { "oneOf": [{ "type": "string" }, { "type": "array", "items": { "type": "string" } }] }
      }
    },
    "option": {
      "type": "object", "additionalProperties": false, "required": ["value", "label"],
      "properties": {
        "value": { "type": "string", "minLength": 1, "maxLength": 64 },
        "label": { "$ref": "#/$defs/label" },
        "description": { "type": "string", "maxLength": 120 }
      }
    },
    "numberField": {
      "type": "object", "additionalProperties": false, "required": ["type", "field", "name", "label"],
      "properties": {
        "type": { "const": "field" }, "field": { "const": "number" },
        "name": { "$ref": "#/$defs/name" }, "label": { "$ref": "#/$defs/label" },
        "description": { "type": "string", "maxLength": 120 },
        "required": { "type": "boolean", "default": true },
        "min": { "type": "number" }, "max": { "type": "number" },
        "step": { "type": "number", "exclusiveMinimum": 0 },
        "unit": { "type": "string", "maxLength": 16 },
        "default": { "type": "number" }
      }
    },
    "textField": {
      "type": "object", "additionalProperties": false, "required": ["type", "field", "name", "label"],
      "properties": {
        "type": { "const": "field" }, "field": { "const": "text" },
        "name": { "$ref": "#/$defs/name" }, "label": { "$ref": "#/$defs/label" },
        "description": { "type": "string", "maxLength": 120 },
        "required": { "type": "boolean", "default": true },
        "max_length": { "type": "integer", "minimum": 1, "maximum": 500, "default": 200 },
        "multiline": { "type": "boolean", "default": false },
        "placeholder": { "type": "string", "maxLength": 60 },
        "default": { "type": "string", "maxLength": 500 }
      }
    },
    "toggleField": {
      "type": "object", "additionalProperties": false, "required": ["type", "field", "name", "label"],
      "properties": {
        "type": { "const": "field" }, "field": { "const": "toggle" },
        "name": { "$ref": "#/$defs/name" }, "label": { "$ref": "#/$defs/label" },
        "description": { "type": "string", "maxLength": 120 },
        "required": { "type": "boolean", "default": false },
        "default": { "type": "boolean", "default": false }
      }
    },
    "submit": {
      "type": "object", "additionalProperties": false, "required": ["type"],
      "properties": {
        "type": { "const": "submit" },
        "label": { "type": "string", "minLength": 1, "maxLength": 12, "default": "提交" }
      }
    }
  }
}
```

附加规则（schema 表达不了，`validate_view` 按顺序检查，路径照写；一次最多报 20 条）：

1. 表单字段的 `name` 互不相同；字段总数不超过 20。
2. 有字段时恰好一个 `submit`，而且必须是最后一个组件；没有字段时不能有 `submit`。路径都写 `components`。
3. 表格每一行的键都必须是已声明的 `columns[].key`，未声明的键报 `components.N.rows.M`；缺的键不补，前端渲染时按 `null` 处理。
4. 图表每条 `series.values` 的长度等于 `categories` 的长度；`chart_type="pie"` 时只能有一条系列。
5. 数字字段：`min`、`max` 都有时 `min <= max`；`default` 落在范围内。
6. 选择字段：`multiple=false` 时 `default` 是字符串，`true` 时是字符串数组；`default` 里的值都要在 `options[].value` 里；`options[].value` 互不相同。
7. 文本字段：`default` 的长度不超过 `max_length`。
8. 禁止字符：`\x00`–`\x08`、`\x0b`–`\x1f`、`\x7f`；`\n` 只允许出现在 `text` 组件的 `text` 与文本字段的 `default`，其余字符串含 `\n` 也报错。路径写到具体键。
9. 数值一律有限（`NaN`、`Infinity` 拒绝）；Pydantic 层已用 `allow_inf_nan=False`，附加规则再扫一遍 `model_dump()` 兜底。
10. `model_dump_json(exclude_none=True)` 的 UTF-8 字节数不超过 64 KiB，路径写空串。
11. 图表总点数（所有 `series[].values` 长度之和）不超过 600，路径 `components.N.chart`。
12. 图表按 `{**chart, "caption": caption}` 紧凑序列化（`ensure_ascii=False`、`separators=(",", ":")`）后不超过 24 KiB，路径 `components.N.chart`。

错误路径的清洗规则（Pydantic 的 `loc` 会带判别值段）：把 `loc` 转成列表，丢掉 `"[key]"`；当 `loc` 形如 `("components", <int>, ...)` 时，若第三段是 `"field"`，丢掉它，并在第四段属于 `select`、`number`、`text`、`toggle` 时再丢掉第四段；否则若第三段属于 `heading`、`text`、`stats`、`table`、`chart`、`submit` 之一，丢掉第三段；其余段按下标用 `.` 连接。例：`('components', 3, 'field', 'select', 'options', 0, 'label')` 得到 `components.3.options.0.label`；`('components', 0, 'text', 'bogus')` 得到 `components.0.bogus`；`('components', 0)` 得到 `components.0`。

Pydantic 错误类型到中文原因的映射（映射表外的类型原样用 Pydantic 的 `msg`）：

| Pydantic `type` | 中文原因 |
|---|---|
| `missing` | `必填` |
| `extra_forbidden` | `不认识的字段` |
| `union_tag_invalid`、`union_tag_not_found`、`literal_error` | `取值不在允许范围` |
| `string_type` | `需要字符串` |
| `string_pattern_mismatch` | `格式不对` |
| `string_too_short`、`too_short` | `太短` |
| `string_too_long`、`too_long` | `太长` |
| `float_parsing`、`int_parsing`、`float_type`、`int_type` | `需要数字` |
| `bool_type`、`bool_parsing` | `需要布尔值` |
| `greater_than`、`greater_than_equal`、`less_than`、`less_than_equal` | `超出范围` |

例子（规划师让用户挑关卡）：

```json
{
  "version": 1,
  "title": "这周刷哪一关",
  "components": [
    { "type": "text", "text": "按企鹅物流近 30 天的掉落率算出每件固源岩的期望理智，越低越划算。" },
    { "type": "table", "columns": [
        { "key": "stage", "label": "关卡" },
        { "key": "cost", "label": "每件理智", "align": "right", "format": "number" }
      ],
      "rows": [{ "stage": "1-7", "cost": 13.4 }, { "stage": "S4-1", "cost": 15.2 }] },
    { "type": "field", "field": "select", "name": "stage", "label": "关卡",
      "options": [{ "value": "1-7", "label": "1-7" }, { "value": "S4-1", "label": "S4-1" }] },
    { "type": "field", "field": "number", "name": "times", "label": "次数", "min": 1, "max": 99, "default": 10 },
    { "type": "field", "field": "toggle", "name": "use_medicine", "label": "可以用理智药" },
    { "type": "submit", "label": "就这么刷" }
  ]
}
```

#### 4.3.2 工具 `create_view`

- 参数：`{"view": <视图对象>}`；`view` 在工具声明里只声明为对象，不展开整份 schema。
- 返回：`{"artifact_id": "...", "kind": "form", "title": "这周刷哪一关", "field_count": 3}`。
- 视图不合法：`AppError(TOOL_ARGS_INVALID, "视图不合法：<前 3 条「路径：原因」，用「；」连接；路径为空时只写原因>", {"errors": [前 20 条]})`。
- 调用者运行不属于任何工单：`AppError(INVALID_PARAMETER, "只能在工单里生成视图")`。
- 产物服务未装配：`AppError(SERVICE_UNAVAILABLE, "产物服务尚未启动")`（由 `v3_service` 给出）。

工具描述原文（`CREATE_VIEW_DESCRIPTION`，祈使句）：

```
要用户一眼看懂一组数字或几项对比，或者要用户在几个选项里做决定、填几个参数时，生成一张视图。组件只能用 heading（标题）、text（文本）、stats（关键数字）、table（表格）、chart（图表）、field（表单字段：select、number、text、toggle）与 submit（提交按钮）；最多 40 个组件、20 个字段、64 KiB。有表单字段时最后一个组件必须是 submit，没有字段时不能有 submit。chart 组件形如 {"type":"chart","caption":"…","chart":{"chart_type":"line|bar|pie","title":"…","categories":[…],"series":[{"name":"…","values":[…]}],"stacked":false,"x_label":"…","y_label":"…","unit":"…","source":"…"}}，chart 内的字段与 send_chart 同名但平铺在 chart 下，caption 在组件层；总点数不超过 600，序列化不超过 24 KiB。用户提交后，答案会作为一条【表单提交】消息发给你；表单答案不等于确认。视图不代替汇报，本轮照常以 report 收尾。
```

#### 4.3.3 端点 `POST /api/agent/artifacts/{artifact_id}/submit`

请求模型 `ArtifactFormSubmit`：`{"values": {"stage": "1-7", "times": 10, "use_medicine": false}}`；`values` 是对象，未知顶层字段 422。

响应模型 `ArtifactFormSubmitResult`：`{"artifact_id": "...", "ticket_id": "...", "submitted_at": "2026-12-01T08:00:00Z"}`。

| 情形 | 状态码 | 错误码 | message | details |
|---|---|---|---|---|
| 成功 | 200 | | | 响应 `{artifact_id, ticket_id, submitted_at}` |
| 产物不存在 | 404 | `ARTIFACT_NOT_FOUND` | `产物不存在` | |
| 产物不是表单（`kind != "form"` 或 `mime` 不是视图） | 400 | `INVALID_PARAMETER` | `这个产物不是表单` | |
| 产物里存的视图读不出或校验不过 | 400 | `INVALID_PARAMETER` | `表单定义已损坏，请让 Worker 重新生成` | |
| 产物不属于工单（`ticket_id` 为 NULL） | 400 | `INVALID_PARAMETER` | `这个表单不属于任何工单` | |
| 提交值序列化后超过 4 KiB | 400 | `INVALID_PARAMETER` | `提交内容太长` | `{"limit_bytes": 4096}` |
| 值不合法 | 400 | `INVALID_PARAMETER` | `表单有 {n} 处需要修改` | `{"fields": {"<name>": "<原因>"}}` |
| 工单不存在（产物指向的工单行已删除） | 404 | `TICKET_NOT_FOUND` | `没有这个工单` | |
| 工单已结束 | 409 | `TICKET_ENDED` | `工单已结束` | |
| Worker 服务未装配 | 503 | `AGENT_DISABLED` | `工单服务还在启动中，请稍后重试` | |
| 产物服务未装配 | 503 | `SERVICE_UNAVAILABLE` | `产物服务尚未启动` | |
| LLM 未配置 | 503 | `LLM_NOT_CONFIGURED` | `还没有配置 LLM，请先在设置页填写 base_url、api_key 与 model` | |
| LLM 密钥无法解密 | 503 | `LLM_ENCRYPTION_KEY_INVALID` | `已保存的 LLM API Key 无法解密，请到设置页重新填写 API Key` | |
| 请求体不是对象 | 422 | `VALIDATION_ERROR` | Pydantic 的中文校验体 | 原生字段列表 |
| 未登录 | 401 | `UNAUTHORIZED` | | |

字段错误原因的原文：`必填`、`不在可选项里`、`选项不合法`、`不能重复选择`、`需要填数字`、`不能小于 {min}`、`不能大于 {max}`、`需要是文本`、`不能超过 {max_length} 字`、`需要是开或关`、`表单里没有这一项`。

SSE、WebSocket：不加事件。提交不开 SSE 流，`post_user_message` 也不发 `message_persisted`；前端提交成功后失效 `tickets.messagesAll(ticketId)` 与 `tickets.detail(ticketId)`，能看到刚写入的这条用户消息；Worker 的回复在它下一轮运行结束（`run_finished` 已会失效消息窗口）时整段出现。

#### 4.3.4 详情 `meta` 的 wire

`GET /api/agent/artifacts/{artifact_id}` 的响应 `ArtifactDetailOut` 增加 `meta: dict[str, Any]`；`GET /api/agent/artifacts` 的 `ArtifactOut` 列表项不加。`meta` 原样返回整列，含 M84 的内部键 `facts_markdown`；控制台登录用户本来就能读产物正文，不额外过滤。这个扩展写进 [04 §2.3](../04-接口与事件协议.md) 的 M75 详情行与 [03 §8.2](../03-数据模型.md) 的 `meta` 行，并在 [13 §8](../13-决策记录.md) 记一条「M90 为提交记录扩展详情 wire」。M75 端点的其它语义不动。

### 4.4 配置项

无。不加配置键，不改 `config.template.yaml`。

### 4.5 行为与算法

#### 4.5.1 `create_view`

```
run = require_run(kinds={"worker"}, message="create_view 只能在 Worker 里调用")
ticket_id = run.ticket_id                                   # 为空抛 INVALID_PARAMETER
spec = validate_view(params.view)                           # 失败抛 TOOL_ARGS_INVALID（§4.3.2）
service = v3_service(run, ctx, "artifact_service", "产物服务")
body = spec.model_dump_json(exclude_none=True).encode("utf-8")
artifact = await service.put(
    kind=view_kind(spec),                                   # "form" 或 "dashboard"
    title=spec.title,
    body=body,                                              # 参数名是 body: bytes，不是 content
    mime=VIEW_MIME,
    session_id=run.session_id,
    ticket_id=ticket_id,
    run_id=run.run_id,
    meta={"view_version": 1, "submissions": [], "last_submitted_at": None},
)
return {"artifact_id": str(artifact.id), "kind": view_kind(spec),
        "title": spec.title, "field_count": len(view_fields(spec))}
```

`put` 内部会发 WS `agent_event` 的 `artifact_created`；前端不依赖它挂卡片，只用它失效产物列表。不要为了挂卡片补 SSE。

#### 4.5.2 提交

```
service = _artifact_service(request)                         # 503 SERVICE_UNAVAILABLE
detail = await service.get(artifact_id)                      # 404 ARTIFACT_NOT_FOUND；不传 max_chars
artifact = detail.artifact
if artifact.kind != "form" or artifact.mime != VIEW_MIME:    # 400「这个产物不是表单」
spec = validate_view(json.loads(detail.content))             # 异常：400「表单定义已损坏…」
if artifact.ticket_id is None:                               # 400「这个表单不属于任何工单」
if len(json.dumps(payload.values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 4096:  # 400「提交内容太长」
values = validate_submission(spec, payload.values)           # ViewInvalid：400，details.fields
content = render_submission(spec, values, artifact_id=artifact_id)
worker = _worker_service(request)                            # 503 AGENT_DISABLED
await worker.post_user_message(                              # 第三个位置参数是 []
    artifact.ticket_id, content, [],
    notify_content=f"【表单提交】{spec.title}",
)                                                            # 404 TICKET_NOT_FOUND / 409 TICKET_ENDED / 503 LLM_*
submitted_at = utcnow()
meta = dict(artifact.meta or {})
submissions = [item for item in meta.get("submissions") or [] if isinstance(item, dict)]
submissions.append({"at": _iso_z(submitted_at), "values": values})
meta["submissions"] = submissions[-5:]
meta["last_submitted_at"] = _iso_z(submitted_at)
await service.update(artifact_id, content=detail.content, meta=meta)
return {artifact_id, ticket_id: artifact.ticket_id, submitted_at: _iso_z(submitted_at)}
```

顺序固定为「校验视图与值 → 写 Worker 消息 → 写回 `meta`」：先写消息保证被拒绝的提交不会留下记录；第二步失败时会返回 500 且消息已发出，重试会再发一条，属于已知窗口，写进台账。`post_user_message` 内部已经做 `require_open_ticket` 与 `ensure_ready`，不要再自己查 `Ticket` 或 `AgentSession`。并发提交同一张表单是读改写，后写覆盖先写；同一个 Worker 会话的实际提交是串行的，这个窗口记进 §8。

`validate_submission` 的规则：

| 字段 | 合法值 | 缺省 |
|---|---|---|
| `select`，`multiple=false` | 字符串，且在 `options[].value` 里 | 必填缺失报 `必填`；选填缺失不出现在结果里 |
| `select`，`multiple=true` | 字符串数组，元素都在选项里、互不相同；必填时至少一个 | 同上 |
| `number` | 数字（布尔不算），有限，落在 `min`–`max` | 同上 |
| `text` | 字符串，去首尾空白后不超过 `max_length`；必填时不能为空 | 同上 |
| `toggle` | 布尔 | 必填缺失报 `必填`，否则按 `false` |
| 不在视图里的键 | 报 `表单里没有这一项` | |

数字返回值统一成 `int`（值是整数时）或 `float`；文本去首尾空白；多选保留顺序。

#### 4.5.3 写给 Worker 的消息

```
【表单提交】{视图标题}（产物 {artifact_id 前 8 位}）
- {label}：{值}
- {label}：{值}
```

值的写法：单选 `{选项 label}（{value}）`；多选每项同上、用「、」连接；数字 `{数字}{unit}`（整数不带小数点）；文本原样（多行时续行前加两个空格）；开关 `是` / `否`；选填没填 `（未填）`。例子：

```
【表单提交】这周刷哪一关（产物 1a2b3c4d）
- 关卡：1-7（1-7）
- 次数：10
- 可以用理智药：否
```

给总控的 passive 摘要用 `notify_content=f"【表单提交】{spec.title}"`，不把整段值再抄一遍。

#### 4.5.4 前端解析与安全

`parseView(raw)` 永不抛异常：顶层不是对象、`version !== 1`、`title` 缺失、`components` 不是数组时返回 `null`；单个组件不合法（未知 `type`、缺必填字段、超上限、值类型不对）就丢弃并计入 `dropped`；超过 40 个组件时只保留前 40 个，其余计入 `dropped`；图表组件用 `parseChartCard({ chart: component.chart, caption: component.caption ?? '' })`，返回 `null` 也丢弃。前端不信任服务端已校验过，上限与服务端相同。

渲染器的硬约束（`ViewRenderer.test.tsx` 有对应用例）：

1. 只产出固定的 React 元素：标题、段落、`dl`、`table`、表单控件、按钮与 v2 的 `ChartCard`。不用 `dangerouslySetInnerHTML`，不渲染 `img`、`iframe`、`a`、`object`、`embed`，不把视图里的任何字符串当 URL、类名、样式或事件处理。
2. 文本一律按纯文本渲染（`whitespace-pre-wrap`），不解析 Markdown；形如 `<img onerror=…>`、`https://…` 的文字原样显示。
3. 不发任何视图触发的网络请求；只有提交时调 `POST /api/agent/artifacts/{id}/submit`。
4. ECharts 的配置只由 `buildChartOption` 从解析后的 `ChartCardData` 生成（v2 媒体卡的同一条路），视图数据不能带格式化函数。
5. 数字用 `Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 })`；`percent` 列的值按 0–100 显示并加 `%`；布尔显示 `是` / `否`；`null` 显示 `—`。

交付前跑：

```bash
grep -rnE "dangerouslySetInnerHTML|<img|<iframe|<a |href=|<object|<embed" web/src/features/artifacts/view; echo $?
```

退出码 1（没有匹配）是合格；有输出就不合格。

#### 4.5.5 边界情况

| 输入 | 期望 |
|---|---|
| 模型给的视图有未知组件 `{"type": "html"}` | 工具返回 `TOOL_ARGS_INVALID`，路径 `components.N`；不建产物 |
| 有字段却没有 `submit` | `TOOL_ARGS_INVALID`，路径 `components` |
| 表格行里多了未声明的键 | `TOOL_ARGS_INVALID`，路径 `components.N.rows.M` |
| 视图 `components` 超过 40 个 | Pydantic 报错，路径 `components`；不建产物 |
| 字段超过 20 个 | 附加规则报错，路径 `components`；不建产物 |
| 视图序列化后超过 64 KiB | `TOOL_ARGS_INVALID`，路径空串 |
| 用户提交时工单刚结束 | 409 `TICKET_ENDED`；卡片切到只读并提示 `工单已结束，不能再提交` |
| 用户改了表单再提交一次 | 200，Worker 收到第二条【表单提交】，`submissions` 两条 |
| 连续提交 6 次 | `submissions` 只留最近 5 条，`last_submitted_at` 是第 6 次 |
| 两个标签页同时提交 | 后写覆盖先写；`submissions` 可能少一条，响应都按本次提交成功返回 |
| 提交值超过 4 KiB | 400 `提交内容太长`；不写 Worker 消息 |
| 视图产物的 `content` 是空串或落在文件存储 | 空串按「表单定义已损坏」；视图不会落文件（64 KiB 低于 256 KiB），无需特判 |
| 视图 `version` 不是 1（旧前端） | `parseView` 返回 `null`，卡片显示 `视图无法显示` 与「在产物页打开」 |
| 视图 `meta.view_version` 不是 1 | 卡片显示 `视图无法显示`，不渲染表单 |
| 产物 `ticket_id` 为 NULL | 400 `这个表单不属于任何工单` |
| 产物指向的工单行已被删除 | 404 `TICKET_NOT_FOUND` |
| 产物正文超过 64 KiB（非视图产物） | 详情页显示截断提示与「下载」 |
| 表单答案里「可以用理智药：是」 | 只是用户意见；Worker 真要用药时照旧发起确认 |

### 4.6 前端

#### 4.6.1 key、查询与路由

复用 M75 的三个 key；只在 `keys.agent` 下新增一个 `artifactsList`，挂在 `artifactsAll()` 前缀下，自动吃到 `artifact_created` 的整片失效：

```ts
// api/keys.ts 的 agent 下
/** 全部产物的分页列表（M90 §4.6）：挂在 artifactsAll 前缀下。 */
artifactsList: (filters?: Readonly<Record<string, unknown>>) =>
  [...root, 'agent', 'artifacts', 'list', filters ?? {}] as const,
```

| 查询或动作（`features/artifacts/api.ts`） | key | 端点 |
|---|---|---|
| `artifactsListInfiniteQuery({ kind, ticketId })` | `artifactsList({ kind, ticket_id })` | GET `/api/agent/artifacts?kind&ticket_id&page&size=20`；`getNextPageParam` 用 `page * size < total` |
| `artifactDetailQueryOptions(id)`（M75 复用） | `artifact(id)` | GET `/api/agent/artifacts/{artifact_id}` |
| `usePinArtifact()` | 成功失效 `artifactsAll()` 与 `artifact(id)` | PUT `/api/agent/artifacts/{artifact_id}/pin`，body `{pinned}` |
| `useSubmitArtifactForm(artifactId, ticketId)` | 成功失效 `artifact(id)`、`artifactsAll()`、`tickets.messagesAll(ticketId)`、`tickets.detail(ticketId)` | POST `/api/agent/artifacts/{artifact_id}/submit` |
| `saveArtifactFile(artifact)`（M75 复用） | | GET `/api/agent/artifacts/{artifact_id}/content`，`parseAs: 'blob'` |

`downloadName(title, mime)` 放在 `features/artifacts/format.ts`，替换 `workspace-api.ts` 里的 `filenameOf`/`extensionOf`：

```ts
export function downloadName(title: string, mime: string): string {
  const stem = title.replace(/[\\/:*?"<>|\s]+/g, '').slice(0, 64) || 'artifact'
  const ext = /markdown/.test(mime) ? 'md'
    : /json/.test(mime) ? 'json'
    : /^text\/csv/.test(mime) ? 'csv'
    : /^text\/plain/.test(mime) ? 'txt'
    : 'bin'
  return `${stem}.${ext}`
}
```

`ARTIFACT_KIND_LABELS` 与 `formatArtifactSize` 从 `ArtifactList.tsx` 移到 `features/artifacts/format.ts`；`dashboard` 改为「看板」；`ARTIFACT_KIND_LABELS` 的键与顺序是 `note`、`spill`、`report`、`chart`、`table`、`file`、`form`、`dashboard`。

路由（`routes/router.tsx`）：`{ path: 'more/artifacts', element: page(<ArtifactsPage />) }`、`{ path: 'more/artifacts/:artifactId', element: page(<ArtifactDetailPage />) }`，放在 `more/watchdog` 之后。导航（`nav.ts` 的 `secondaryNav`）在「值守」之后加：

```ts
{ to: '/more/artifacts', label: '产物', icon: FileBox, description: '报告、表单、看板与外置的大输出', group: 'agent' },
```

#### 4.6.2 视图组件

```ts
// features/artifacts/view/schema.ts
export const VIEW_MIME = 'application/vnd.maa.view+json'
export const VIEW_LIMITS = { components: 40, fields: 20, text: 2000, shortText: 80, label: 40, stats: 6, columns: 12, rows: 200, cell: 200, options: 20, optionValue: 64, textField: 500 } as const
export interface ViewStat { label: string; value: number | string; unit: string | null; delta: number | null; hint: string | null }
export interface ViewColumn { key: string; label: string; align: 'left' | 'right'; format: 'text' | 'number' | 'percent' }
export type ViewCell = string | number | boolean | null
export interface SelectOptionSpec { value: string; label: string; description: string | null }
interface FieldBase { name: string; label: string; description: string | null; required: boolean }
export type ViewField =
  | (FieldBase & { field: 'select'; options: SelectOptionSpec[]; multiple: boolean; default: string | string[] | null })
  | (FieldBase & { field: 'number'; min: number | null; max: number | null; step: number | null; unit: string | null; default: number | null })
  | (FieldBase & { field: 'text'; maxLength: number; multiline: boolean; placeholder: string | null; default: string | null })
  | (FieldBase & { field: 'toggle'; default: boolean })
export type ViewComponent =
  | { type: 'heading'; text: string; level: 2 | 3 }
  | { type: 'text'; text: string; tone: 'default' | 'muted' | 'warning' }
  | { type: 'stats'; items: ViewStat[] }
  | { type: 'table'; title: string | null; columns: ViewColumn[]; rows: Array<Record<string, ViewCell>>; caption: string | null }
  | { type: 'chart'; card: ChartCardData }
  | { type: 'field'; spec: ViewField }
  | { type: 'submit'; label: string }
export interface ParsedView { title: string; description: string | null; components: ViewComponent[]; fields: ViewField[]; dropped: number }
export type FormValue = string | string[] | number | boolean | null
export type FormValues = Record<string, FormValue>
export function parseView(raw: unknown): ParsedView | null
export function initialValues(fields: readonly ViewField[], submitted?: Record<string, unknown>): FormValues
export function validateValues(fields: readonly ViewField[], values: FormValues): Record<string, string>
export function toSubmitPayload(fields: readonly ViewField[], values: FormValues): Record<string, unknown>
```

```ts
// features/artifacts/view/ViewRenderer.tsx
export interface ViewRendererProps {
  view: ParsedView
  mode: 'interactive' | 'readonly'
  values: FormValues
  errors: Record<string, string>
  onChange: (name: string, value: FormValue) => void
  onSubmit?: () => void
  submitting?: boolean
}
// features/artifacts/view/ViewField.tsx
export interface ViewFieldProps { spec: ViewField; value: FormValue; error: string | undefined; disabled: boolean; onChange: (value: FormValue) => void }
// features/artifacts/view/ViewTable.tsx、ViewStats.tsx
export interface ViewTableProps { component: Extract<ViewComponent, { type: 'table' }> }
export interface ViewStatsProps { items: readonly ViewStat[] }
```

- 整个视图包在一个 `<form noValidate onSubmit>` 里（没有字段时不包）；组件之间 `space-y-4`。
- `heading`：level 2 渲染 `h3`（`text-headline font-semibold`），level 3 渲染 `h4`（`text-[0.9375rem] font-semibold`）。
- `text`：`p`，`default` 用正文色，`muted` 用 `text-footnote text-muted-foreground`，`warning` 用 `text-warning`。
- `stats`：`dl`，网格 `grid-cols-2 sm:grid-cols-3`；每项 `dt` 是标签，`dd` 是值（`text-title tabular-nums`）与单位；`delta` 正数写 `+3`（`text-success`），负数写 `−2`（`text-danger`），零不显示；`hint` 小字。
- `table`：外层 `max-h-96 overflow-auto rounded-lg border`；`th scope="col"`，第一列单元格用 `th scope="row"`；`align='right'` 的列右对齐并 `tabular-nums`；`caption` 写在表下方小字；行里缺的键按 `null` 显示。
- `chart`：直接 `<ChartCard card={card} />`。
- `field`：外层 v2 `Field`（`label`、`description`、`error`）。`select` 单选用 `NativeSelect`，首项是禁用的 `选择一项`；多选用一排 `Chip`（`selected` 表示选中）。`number` 用 `Input type="number" inputMode="decimal"`，带 `min`、`max`、`step`，单位写在输入框右侧。`text` 用 `Input`，`multiline` 时用 `Textarea`，都带 `maxLength`。`toggle` 用 `SwitchRow`。只读模式下全部 `disabled`，`Field` 的 `aria-invalid` 由 `error` 决定。
- `submit`：`Button type="submit"`，文字取 `label`，提交中 `loading`；只读模式不渲染。

#### 4.6.3 视图卡与产物页

```ts
// features/artifacts/ViewArtifactBody.tsx
export interface ViewArtifactBodyProps { artifact: ArtifactDetailView; ticketEnded: boolean }
// features/artifacts/ViewArtifactCard.tsx
export interface ViewArtifactCardProps { artifactId: string; ticketEnded: boolean }
// features/artifacts/ArtifactChip.tsx
export interface ArtifactChipProps { artifactId: string; title: string; kind: string }
```

- `ViewArtifactBody`：`parseView(JSON.parse(content))`，JSON 坏了或返回 `null` 时显示 `InlineAlert`「视图无法显示」与「在产物页打开」；`meta.view_version` 不是 1 时同样处理；`dropped > 0` 时底部小字 `有 {n} 个组件无法显示`。表单状态：初值取 `meta.submissions` 最后一次的 `values`（有的话）否则取默认值；有过提交时初始为只读，显示 `已提交 · {时间}` 与按钮 `修改后重新提交`（切回可填）。提交：先 `validateValues`，有错就地显示不发请求；成功 Toast `已提交表单`、切只读；400 且 `details.fields` 存在时把原因落到对应字段，并在表单上方显示服务端的 message；409 `TICKET_ENDED` 切只读并显示 `工单已结束，不能再提交`；其他错误 `提交表单失败：{原因}`。`ticketEnded` 为真时一开始就是只读，提示同上；父组件用 `key={artifact.id}` 保证换产物时状态重置。
- `ViewArtifactCard`：读 `artifactDetailQueryOptions`；加载中 `Skeleton`（`h-24 w-full`）；读失败 `读取产物失败：{原因}`；`mime` 不是视图时退化成 `ArtifactChip`。卡片头：图标（看板 `LayoutDashboard`、表单 `ClipboardList`）、标题、徽章 `看板` / `表单`、右侧链接 `在产物页打开`（去 `/more/artifacts/{id}`）；卡片体是 `ViewArtifactBody`。
- `ArtifactsPage`：`Page title="产物" back="/more"`；工具条一个 `NativeSelect`（标签 `种类`，选项 `全部` 与 `ARTIFACT_KIND_LABELS` 的八项），选择写 `?kind=`；`?kind=` 不在八种里时按全部处理，不改写 URL。`?ticket_id=` 存在时工具条多一个可移除的芯片 `只看这个工单`，点击清掉该参数。列表行 `ListItem` 是指向 `/more/artifacts/{id}` 的链接：图标按种类（note `NotebookPen`、spill `ExternalLink`、report `FileText`、chart `ChartLine`、table `Table`、file `File`、form `ClipboardList`、dashboard `LayoutDashboard`，未知用 `FileText`），标题，描述 `{种类} · {formatArtifactSize} · {时间}`，置顶的行 `trailing` 是徽章 `已置顶`；底部 `加载更多`（没有下一页时不渲染）。空状态 `还没有产物` / `Worker 生成报告、表单或看板后会列出来` / 按钮 `前往指挥台`。
- `ArtifactDetailPage`：`Page title={detail.title} back="/more/artifacts"`；操作 `置顶` 或 `取消置顶`（Toast `已置顶` / `已取消置顶`）、`下载`（失败 `下载产物失败：{原因}`）。元信息一行：种类徽章、时间、大小、`所属工单「{标题}」`链接（`ticketDetailQuery`；读不到标题时显示 `所属工单 {id}`）。正文按 `mime`：视图 → `ViewArtifactBody`（工单终态则只读）；`text/markdown` → v2 `Markdown`；`application/json`、`text/plain`、`text/csv` → 等宽 `pre`（`max-h-[70dvh] overflow-auto`）；其他 → `这种产物不能在页面里预览` 与 `下载`。`truncated` 为真时正文上方 `InlineAlert`「内容较长，只显示前 64 KiB」带 `下载`。404 时 `EmptyState`「没有这个产物」与按钮 `返回产物列表`；其他错误显示 `读取产物失败：{原因}` 与重试。
- 工单右栏的 `ArtifactList` 保留为第二入口；视图类产物的抽屉不显示 `<pre>`，改为 `在产物页打开` 按钮；列表徽章统一 `已置顶`；截断提示统一 `内容较长，只显示前 64 KiB`；下载失败统一 `下载产物失败：{原因}`。

#### 4.6.4 挂进会话（改 M72 的文件）

1. `transcript-v3.ts`：`ConversationEntry` 的 `tool` 分支加 `artifactId: string | null`；取法写死为
   `const body = tool.result; const value = isRecord(body) && isRecord(body.result) ? body.result.artifact_id : null`，
   只有 `typeof value === 'string' && value.length > 0` 才收。新增导出 `artifactIdOf(tool: ToolView): string | null`，历史与实时共用。
2. `ConversationView.tsx`：props 加 `ticketEnded?: boolean`；`ConversationEntryView` 也收这个 prop，递归渲染子代理消息时继续传；渲染 `LiveTurnView` 时把同一个值传下去。`tool` 条目带 `artifactId` 时，在工具条下方渲染 `<ViewArtifactCard artifactId={entry.artifactId} ticketEnded={ticketEnded ?? false} />`。工单详情页把 `detail.ticket.status` 是否为终态（`ENDED_STATUSES`）传进来。
3. `LiveTurnView.tsx`：props 加 `ticketEnded?: boolean`；工具段用 `liveToolView` 拿到 `ToolView` 后，若 `artifactIdOf(view)` 非空，就在 `ToolCallBar` 下方渲染同一个 `ViewArtifactCard`；没有 `artifact_created` 段，也不给 `LiveSegment` 加新种类。
4. `sse.ts`、`live-turn.ts` 不改：`SseV3Event` 不加 `artifact_created`，`LiveSegment` 不加 `artifact` 段。
5. 不做「同一运行里去重」：`create_view` 的每个工具结果各挂一张卡，一张视图只对应一次成功调用。

#### 4.6.5 文案

| 位置 | 原文 |
|---|---|
| 徽章 | `表单`、`看板`、`已置顶` |
| 按钮 | 视图里的 `label`（缺省 `提交`）、`修改后重新提交`、`在产物页打开`、`置顶`、`取消置顶`、`下载`、`加载更多`、`前往指挥台`、`返回产物列表` |
| Toast | `已提交表单`、`已置顶`、`已取消置顶`、`已下载 {文件名}` |
| 提示 | `已提交 · {时间}`、`有 {n} 个组件无法显示`、`视图无法显示`、`工单已结束，不能再提交`、`内容较长，只显示前 64 KiB`、`这种产物不能在页面里预览`、下拉首项 `选择一项`、`只看这个工单` |
| 错误 | `提交表单失败：{原因}`、`读取产物失败：{原因}`、`下载产物失败：{原因}`、服务端的 `表单有 {n} 处需要修改` 与 §4.3.3 的字段原因 |
| 空状态 | `还没有产物` / `Worker 生成报告、表单或看板后会列出来` / 按钮 `前往指挥台`；`没有这个产物` / 按钮 `返回产物列表` |
| 种类 | `笔记`、`外置输出`、`报告`、`图表`、`表格`、`文件`、`表单`、`看板` |
| 工具名 | `TOOL_LABELS` 加 `create_view: '生成视图'` |

### 4.7 提示词与代理定义

四个 Worker 的 frontmatter 改动：`tools` 末尾加 `create_view`，`version` 在当前实际值上加 1。当前（d9e9c7d）是 general 6、analyst 3、planner 3、copilot 3；M87 只给 copilot +1（general 不动），M88 给 general/planner/analyst +1，M89 只给 general +1；开工时以 §0 读到的实际值为准，测试断言用同一个值。`tools` 行改成：

| 文件 | `tools` 行末尾 |
|---|---|
| `general.md` | `… load_skill, read_skill_file, remember, create_view` |
| `analyst.md` | `… delegate, load_skill, read_skill_file, create_view` |
| `planner.md` | `… delegate, load_skill, read_skill_file, create_view` |
| `copilot.md` | `… delegate, hold_device, release_device, load_skill, read_skill_file, create_view` |

正文末尾追加一段（四个文件同一段；analyst 没有 `ask_user`，删掉那半句）：

```markdown
要用户对着几项数据拍板、或一次填几个参数时，用 create_view 生成视图表单，然后 report 报 waiting，human_action 的类型写 decision；只问一个选择、没有数据要摆时用 ask_user。收到【表单提交】后按答案继续。表单答案不等于确认，消耗类与破坏类操作仍要逐次确认。
```

copilot 的正文在这段前面再加一句：「有几份候选作业要用户挑时，用 create_view 把差异摆出来，选项的 label 写作业名与关键参数。」

`common.md` 在「系统消息」那一段后面加一条：

```markdown
- 以【表单提交】开头的消息是用户在你生成的视图表单里填的答案，按用户本人的话对待；它不等于确认。
```

改了提示词与代理定义，12 §4 第 6 项的 capability 评测要跑，通过率不低于 §0 抄下的 M89 台账基线。

## 5. 实施任务

### T1 视图模型与校验

- 写范围：`maa_api/agent/artifacts/views.py`、`tests/agent/test_views.py`
- 步骤：1. 按 §4.2、§4.3.1 写 Pydantic 模型、`validate_view`、`_loc_path`、错误原因映射。2. 写 `view_kind`、`view_fields`、`view_json_schema`、`validate_submission`、`render_submission`。3. 附加规则 1 到 12 逐条实现，错误一次最多收 20 条。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/agent/test_views.py > /tmp/m90-t1.log 2>&1; echo $?; tail -n 5 /tmp/m90-t1.log` → 0。
- 完成判据：§6 的 `test_views.py` 全部通过；`view_json_schema()` 顶层 `discriminator.mapping` 的键正好是七种组件，`field` 的嵌套 mapping 正好是四种字段；把本机 pydantic 版本记进 M90 台账。

### T2 `create_view` 工具

- 写范围：`maa_api/agent/tools/artifacts.py`、`maa_api/agent/tools/__init__.py`、`tests/agent/test_create_view_tool.py`、`tests/agent/test_tool_catalog.py`
- 步骤：1. 按 §4.2、§4.5.1 实现并注册。2. `EXPECTED_TOOLS` 加 `create_view`，四处计数各加 1（总数 110→111，模型可见 109→110），计数历史注释补一句 M90 的 +1。3. 断言 REST 目录看不到它（`rest_visible=False`）。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/agent/test_create_view_tool.py tests/agent/test_tool_catalog.py > /tmp/m90-t2.log 2>&1; echo $?; tail -n 5 /tmp/m90-t2.log` → 0。
- 完成判据：§6 的工具用例通过；`build_registry()` 里 `artifacts` 组存在。

### T3 代理定义与提示词

- 写范围：`maa_api/agent/specs/builtin/general.md`、`analyst.md`、`planner.md`、`copilot.md`、`maa_api/agent/specs/common.md`、`tests/agent/test_builtin_workers.py`、`tests/agent/specs/test_m70_specs.py`、`tests/agent/specs/test_agent_specs.py`、`tests/api/test_agent_specs_router.py`
- 步骤：1. 按 §4.7 改四个 frontmatter 与正文、`common.md`。2. 把四个定义开工时的实际 `version` 记进台账，各 +1。3. `test_builtin_workers.py` 的 `WORKERS` 元组给 planner、analyst、copilot 末尾加 `create_view`，版本断言改成显式映射（四个加工具的 Worker 用实际值 +1，其余保持实际值）。4. `test_m70_specs.py` 的 `general` 期望集合加 `create_view`。5. `test_agent_specs_router.py` 的 `EXPECTED_VERSIONS` 同步四个值，注释补 M90 一行。6. `tests/agent/specs/test_agent_specs.py` 的 `len(definitions)` 从 110 改成 111，新增 `M90_TOOLS = {"create_view"}` 并并入「legacy_chat 不加载」排除集。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/agent/test_builtin_workers.py tests/agent/specs/test_m70_specs.py tests/agent/specs/test_agent_specs.py tests/api/test_agent_specs_router.py > /tmp/m90-t3.log 2>&1; echo $?; tail -n 5 /tmp/m90-t3.log` → 0；`.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m90-eval.log 2>&1; echo $?; tail -n 2 /tmp/m90-eval.log` → `EVAL OK`，通过率不低于 M89 基线。
- 完成判据：四个定义加载通过；`GET /api/agent/specs/general` 的 `tools` 含 `create_view`；总控与四个子代理的工具集都不含它。

### T4 提交端点与详情 `meta`

- 写范围：`maa_api/api/routers/artifacts.py`、`maa_api/api/schemas/agent_v3.py`、`maa_api/domain/enums.py`、`tests/api/test_artifact_form_submit.py`、`tests/api/test_agent_workspace.py`、`docs/v3/14-现行接口清单.md`
- 步骤：1. 按 §4.2、§4.3.3、§4.5.2 实现端点与两个模型；测试 fixture 照 `tests/api/test_ticket_messages_router.py` 的临时库 + `TicketService` + `WorkerService` + `InboxService` 装配，再加 `ArtifactService` 并挂到 `app.state.artifact_service`。2. `ArtifactDetailOut` 加 `meta`，`artifact_detail_out` 传入；列表不动。3. 改 `ArtifactKind` docstring。4. 14 的 agent 小节加一行，`（87 条）` 改成 `（88 条）`，§6 开头的总数 `190` 改成 `191`；开工时这两个数不是 87/190 就按 §10 停下来问。5. `.venv/bin/python scripts/dump_openapi.py` 重新生成快照。
- 验证：`.venv/bin/python -m pytest -o addopts= -q tests/api/test_artifact_form_submit.py tests/api/test_agent_workspace.py tests/domain/test_route_inventory.py > /tmp/m90-t4.log 2>&1; echo $?; tail -n 5 /tmp/m90-t4.log` → 0；`.venv/bin/python scripts/dump_openapi.py --check; echo $?` → 0。
- 完成判据：§6 的端点用例通过；详情有 `meta`，列表项没有；`POST .../submit` 的 operationId 是 `agent_submit_artifact_form`，tag 是 `agent`。

### T5 前端视图渲染

- 写范围：`web/src/features/agent/media.ts`（只加 `export`）、`web/src/features/artifacts/view/` 全部文件与测试、`web/src/features/agent/format.ts`（工具名）
- 步骤：1. 按 §4.5.4、§4.6.2 实现 `schema.ts`、`ViewRenderer`、`ViewField`、`ViewTable`、`ViewStats`。2. `parseChartCard` 前加 `export`。3. `TOOL_LABELS` 加 `create_view: '生成视图'`。
- 验证：`cd web && pnpm test --run src/features/artifacts/view > /tmp/m90-t5.log 2>&1; echo $?; cd ..` → 0；`grep -rnE "dangerouslySetInnerHTML|<img|<iframe|<a |href=|<object|<embed" web/src/features/artifacts/view; echo $?` → 1。
- 完成判据：§6 的 `schema.test.ts`、`ViewRenderer.test.tsx` 通过。

### T6 视图卡、产物页与会话接入

- 写范围：`web/src/features/artifacts/` 其余文件与测试、`web/src/features/console/conversation/transcript-v3.ts`、`ConversationView.tsx`、`LiveTurnView.tsx` 与它们的测试、`web/src/features/tickets/TicketDetailPage.tsx` 与测试、`web/src/features/tickets/ArtifactList.tsx`、`workspace-api.ts` 与 `ArtifactList.test.tsx`、`web/src/api/keys.ts`、`web/src/components/layout/nav.ts`、`layout.test.tsx`、`web/src/routes/router.tsx`、`web/src/routes/more/more.test.tsx`、`web/src/features/console/test-utils.tsx`
- 步骤：1. 按 §4.6.1 加 key、查询、置顶与提交 mutation、`downloadName`、路由、导航。2. 按 §4.6.3 写卡片、产物页、详情页、`ArtifactChip`。3. 按 §4.6.4 改三个会话文件与 `TicketDetailPage`。4. 把 `ARTIFACT_KIND_LABELS`、`formatArtifactSize` 移到 `features/artifacts/format.ts`，`ArtifactList` 改 import 并统一文案与视图入口。5. `test-utils.tsx` 的产物 mock 加 `POST .../submit` 分支。6. `pnpm gen:api` 后用生成类型。
- 验证：`cd web && pnpm gen:api && pnpm typecheck > /tmp/m90-tc.log 2>&1 && pnpm test --run > /tmp/m90-t6.log 2>&1; echo $?; cd ..` → 0。
- 完成判据：§6 的前端用例全部通过。

### T7 全量验证、文档与台账

- 写范围：`docs/v3/ledgers/m90-artifacts-genui.md`（新）、§9 列出的 v3 文档（只改与实现不一致的地方）
- 步骤：1. 按 §7 跑。2. 写台账：§0 抄下的服务签名与版本、工具与端点计数、capability 通过率、并发提交窗口、Rulings。3. 按 §9 改文档，13 §8 补一条。
- 验证：§7 全部通过。
- 完成判据：12 §4 的十项都满足；`docs/v3/14-现行接口清单.md` 的总数是 191、agent 小节是 88。

## 6. 测试清单

后端：

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/agent/test_views.py` | `test_view_accepts_every_component` | §4.3.1 的例子与一个含七种组件的视图都通过；`view_kind` 分别是 `form` 与 `dashboard`；字段顺序与组件顺序一致 |
| 同上 | `test_view_rejects_unknown_component_and_extra_keys` | `{"type":"html"}` 路径 `components.0`；组件多一个键路径 `components.0.bogus`，原因 `不认识的字段` |
| 同上 | `test_fields_require_exactly_one_trailing_submit` | 有字段没 submit、两个 submit、submit 不在最后、没字段却有 submit 四种都报错，路径 `components` |
| 同上 | `test_field_names_must_be_unique` | 重名报错；21 个字段报错 |
| 同上 | `test_table_rows_only_use_declared_columns` | 未声明的键报错，路径 `components.N.rows.M`；缺的键不报错 |
| 同上 | `test_chart_series_must_match_categories` | 长度不等报错；饼图两条系列报错；总点数 601 报错；24 KiB 超限报错 |
| 同上 | `test_number_and_select_defaults_are_checked` | `min > max`、默认值越界、选择默认值不在选项里、`multiple` 与默认值类型不符都报错 |
| 同上 | `test_view_rejects_nan_control_characters_and_oversize` | `NaN`、`Infinity`、`\x01`、标题里的 `\n`、超过 64 KiB 都报错；`text` 组件与文本字段默认值里的 `\n` 允许 |
| 同上 | `test_error_paths_skip_discriminator_segments` | 第 4 个组件是 select、第 1 个选项缺 `label` 时路径逐字是 `components.3.options.0.label` |
| 同上 | `test_json_schema_lists_only_whitelisted_types` | 顶层 `discriminator.mapping` 的键是七种；`field` 的嵌套 mapping 是四种；顶层 `required` 是 `version`、`title`、`components` |
| 同上 | `test_validate_submission_checks_each_field_type` | §4.5.2 表里每种合法与不合法的值；未知键报 `表单里没有这一项`；开关缺失按 `false` |
| 同上 | `test_render_submission_uses_labels_and_values` | 输出与 §4.5.3 的例子逐字相同 |
| `tests/agent/test_create_view_tool.py` | `test_create_view_stores_form_artifact_for_ticket` | 真实 `ArtifactService` + 临时库：产物 `kind=form`、`mime` 是视图、`ticket_id`/`session_id`/`run_id` 正确、`meta.submissions == []`；返回 `field_count`；`content` 可被 `validate_view` 读回 |
| 同上 | `test_create_view_returns_errors_with_paths` | 不合法视图：`TOOL_ARGS_INVALID`，`details.errors[0].path` 正确，没有建产物 |
| 同上 | `test_create_view_requires_a_ticket_session` | `run.ticket_id=None` 时 `INVALID_PARAMETER`，message 是 `只能在工单里生成视图` |
| 同上 | `test_create_view_is_hidden_from_rest_catalog` | `rest_visible is False`；`GET /api/agent/tools` 没有它；`_ensure_rest_visible` 抛 `TOOL_NOT_FOUND` |
| `tests/agent/test_tool_catalog.py` | 既有用例 | `EXPECTED_TOOLS` 含 `create_view`；四处计数是 111/110；`artifacts` 组存在 |
| `tests/api/test_artifact_form_submit.py` | `test_submit_writes_user_message_into_worker_inbox` | 200；Worker 会话多一条用户消息，正文以 `【表单提交】` 开头；收件箱有一条 `thread_key="ticket:<id>"` 的待处理项；总控收到一条 passive 摘要 |
| 同上 | `test_submit_rejects_invalid_values_with_field_errors` | 400 `INVALID_PARAMETER`，`details.fields` 有两个字段的原因，message `表单有 2 处需要修改`；没有写消息 |
| 同上 | `test_submit_rejects_non_form_artifact` | 看板产物与笔记产物都 400 `这个产物不是表单` |
| 同上 | `test_submit_rejects_corrupt_view` | 正文不是 JSON 或校验不过的 `form` 产物 400 `表单定义已损坏，请让 Worker 重新生成` |
| 同上 | `test_submit_rejects_artifact_without_ticket` | `ticket_id` 为 NULL 时 400 `这个表单不属于任何工单` |
| 同上 | `test_submit_rejects_oversized_values` | 20 个文本框各 500 字时序列化超过 4096 字节，400 `提交内容太长`，`details.limit_bytes == 4096`；没有写消息 |
| 同上 | `test_submit_on_ended_ticket_returns_ticket_ended` | 工单 `done` 时 409 `TICKET_ENDED` |
| 同上 | `test_submit_unknown_artifact_returns_404` | 404 `ARTIFACT_NOT_FOUND` |
| 同上 | `test_submit_keeps_last_five_submissions` | 提交 6 次后 `meta.submissions` 长度 5、`last_submitted_at` 是最后一次、最后一条的 `values` 是第 6 次的值 |
| 同上 | `test_submit_route_is_in_agent_group` | `app.openapi()` 里该路径的 `post.operationId == "agent_submit_artifact_form"`，`tags == ["agent"]` |
| `tests/api/test_agent_workspace.py` | `test_detail_returns_meta_and_list_does_not` | 写一条带 `meta` 的产物；详情有 `meta`，列表项的键里没有 `meta` |
| `tests/agent/test_builtin_workers.py` | `test_workers_with_create_view_bump_version` | analyst、planner、copilot 的工具元组含 `create_view`，版本是实际值 +1；daily_ops、diagnose、ops 不含且版本不变 |
| `tests/agent/specs/test_m70_specs.py` | `test_only_the_four_workers_get_create_view` | general 含 `create_view`；orchestrator、navigator、researcher、verifier、recon 不含 |
| `tests/agent/specs/test_agent_specs.py` | `test_legacy_chat_lists_every_v2_registered_tool_in_registration_order` | `len(definitions)==111`；`M90_TOOLS`（`create_view`）被排除出 legacy_chat |
| `tests/api/test_agent_specs_router.py` | `test_worker_specs_expose_create_view_and_versions` | `GET /api/agent/specs/general` 的 `tools` 含 `create_view`；四个 Worker 的 `version` 等于 `EXPECTED_VERSIONS` |

前端（路径相对 `web/src/`）：

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `features/artifacts/view/schema.test.ts` | `parses every whitelisted component` | 七种组件的解析结果；字段按顺序进 `fields` |
| 同上 | `drops unknown or malformed components and counts them` | 未知类型、缺字段、类型不对被丢弃，`dropped` 计数正确；超过 40 个只留前 40 |
| 同上 | `returns null for an unsupported version or shape` | `version: 2`、缺 `title`、`components` 不是数组 |
| 同上 | `builds initial values from defaults or the last submission` | `initialValues` 两种来源；选填缺失为空 |
| 同上 | `validates values like the server` | 与后端同一组用例的原因文案一致 |
| 同上 | `serializes numbers and omits empty optional fields` | `toSubmitPayload` 把数字框的字符串转数字，空的选填项省略 |
| `features/artifacts/view/ViewRenderer.test.tsx` | `renders text as plain text without markup` | `<b>粗</b>`、`**粗**` 原样显示 |
| 同上 | `never creates img, iframe or anchor elements from view data` | 文本里放 `<img src=x onerror=alert(1)>` 与 `https://evil.example/x.png`，`container.querySelector('img, iframe, a, object, embed')` 为 null |
| 同上 | `does not request anything while rendering` | 渲染含 URL 文本的视图后 `fetch` 一次都没被调用 |
| 同上 | `renders stats, tables and charts with the data table fallback` | 数字格式、百分比、布尔与 `—`；图表卡的「数据」折叠表存在 |
| 同上 | `disables every field in readonly mode` | 全部控件 `disabled`，没有提交按钮 |
| 同上 | `renders heading levels` | level 2 是 `h3`，level 3 是 `h4` |
| `features/artifacts/ViewArtifactCard.test.tsx` | `submits a form and shows the submitted state` | POST 请求体是规整后的值；Toast `已提交表单`；切只读，显示「已提交 · …」 |
| 同上 | `maps server field errors onto the fields` | 400 的 `details.fields` 落到对应字段，表单上方显示服务端 message |
| 同上 | `reopens a submitted form for editing` | `meta.submissions` 有值时初始只读；「修改后重新提交」切回可填，初值是上次提交的值 |
| 同上 | `locks the form when the ticket has ended` | `ticketEnded` 与 409 两条路径都只读并提示 |
| 同上 | `falls back to a chip for non-view artifacts` | 笔记产物显示胶囊链接 |
| 同上 | `shows the dropped component notice` | 一个未知组件时显示 `有 1 个组件无法显示` |
| 同上 | `shows an alert when the view cannot be parsed` | 正文不是 JSON 或 `meta.view_version` 不是 1 时 `视图无法显示` |
| `features/artifacts/ArtifactsPage.test.tsx` | `lists artifacts with a kind filter and the pinned badge` | `?kind=form` 写进请求参数；行描述含种类、大小、时间；`已置顶` 徽章 |
| 同上 | `loads more pages` | 25 条时点 `加载更多` 发第二页请求并追加 5 条 |
| 同上 | `shows and clears the ticket filter chip` | `?ticket_id=t1` 时出现 `只看这个工单`，点击后参数被清掉 |
| 同上 | `shows the empty state` | `还没有产物` 与 `前往指挥台` |
| `features/artifacts/ArtifactDetailPage.test.tsx` | `renders markdown, text and view artifacts by mime` | 三种正文；视图渲染表单 |
| 同上 | `shows a truncation notice with a download button` | `truncated` 时提示与按钮 |
| 同上 | `shows the not-found state` | 404 显示 `没有这个产物` 与 `返回产物列表` |
| 同上 | `submits a form from the detail page` | POST 与 Toast；成功后只读 |
| 同上 | `links the owning ticket` | `ticket_id` 存在时显示 `所属工单「…」` |
| 同上 | `pins and unpins from the detail page` | PUT 请求体与 Toast `已置顶` / `已取消置顶`；产物列表失效 |
| 同上 | `downloads the raw content` | 走 `api` 客户端；`URL.createObjectURL` 被调用后 `revokeObjectURL`；文件名按 `downloadName` |
| `features/artifacts/format.test.ts` | `maps artifact kinds to labels` | `dashboard` 是 `看板`，八种齐全 |
| 同上 | `formats sizes` | `812 B`、`3.4 KB`、`1.2 MB` |
| 同上 | `builds download names` | markdown→md、视图与 json→json、text/csv→csv、text/plain→txt、其他→bin；非法字符删除；空标题回 `artifact` |
| `features/tickets/ArtifactList.test.tsx` | `labels dashboards as 看板 and pinned as 已置顶` | 两个徽章 |
| 同上 | `opens view artifacts in the artifact page` | 视图产物的抽屉显示 `在产物页打开`，不显示 `<pre>` |
| `features/console/conversation/transcript-v3.test.ts` | `attaches the artifact id to create_view calls` | `tool.result.result.artifact_id` 是字符串时 `entry.artifactId` 等于它；缺字段时为 null |
| `features/tickets/TicketDetailPage.test.tsx` | `renders a view card under the create_view call` | 工单历史里 `create_view` 工具条下面出现视图卡并能提交 |
| 同上 | `renders a view card from a live create_view result` | 流里的 `tool_result` 挂出同一张卡 |
| `routes/more/more.test.tsx` | 既有分组用例 | Agent 组里有 `产物`，`href` 是 `/more/artifacts`；受保护路由含 `more/artifacts` |
| `components/layout/layout.test.tsx` | 桌面侧边栏用例 | 侧边栏标签列表含 `产物` |

## 7. 验收

```bash
.venv/bin/python -m pytest -o addopts= -q > /tmp/m90-pytest.log 2>&1; echo $?; tail -n 30 /tmp/m90-pytest.log
.venv/bin/python scripts/dump_openapi.py && (cd web && pnpm gen:api) && .venv/bin/python scripts/dump_openapi.py --check; echo $?
cd web && pnpm install --frozen-lockfile > /tmp/m90-pnpm.log 2>&1 && pnpm gen:api:check && pnpm typecheck > /tmp/m90-tc.log 2>&1 && pnpm test --run > /tmp/m90-vitest.log 2>&1 && pnpm build > /tmp/m90-build.log 2>&1; echo $?; cd ..
.venv/bin/python scripts/frontend_smoke.py > /tmp/m90-fsmoke.log 2>&1; echo $?; tail -n 1 /tmp/m90-fsmoke.log
.venv/bin/python scripts/agent_smoke.py > /tmp/m90-asmoke.log 2>&1; echo $?; tail -n 1 /tmp/m90-asmoke.log
.venv/bin/python scripts/orchestrator_smoke.py > /tmp/m90-osmoke.log 2>&1; echo $?; tail -n 1 /tmp/m90-osmoke.log
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m90-eval-reg.log 2>&1; echo $?; tail -n 2 /tmp/m90-eval-reg.log
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m90-eval-cap.log 2>&1; echo $?; tail -n 2 /tmp/m90-eval-cap.log
.venv/bin/python scripts/check_doc_links.py | tail -n 1
grep -rnE "dangerouslySetInnerHTML|<img|<iframe|<a |href=|<object|<embed" web/src/features/artifacts/view; echo $?
git diff --check; echo $?
```

期望：pytest 与 M89 台账相比不新增失败；OpenAPI 与前端五步退出码 0；三个冒烟末行 `SMOKE OK`；两个评测 `EVAL OK`，capability 通过率不低于 §0 抄下的 M89 基线；断链 `BROKEN 0`；安全扫描退出码 1（没有匹配）；`git diff --check` 退出码 0。

补充检查（不作硬性验证，记台账）：配好 LLM，在指挥台说「帮我比较一下 1-7 和 S4-1 刷固源岩哪个划算，让我选一个」，规划师的工单里出现表单，选完提交后 Worker 收到【表单提交】并继续；产物页能看到这张表单与提交记录。作业研究场景同样跑一次 copilot 的候选作业表单。

## 8. 风险与回滚

- 模型反复生成不合法的视图，浪费步数：校验错误带路径与原因，工具描述写明组件与上限，提示词要求视图不代替汇报；capability 评测覆盖。
- 生成式内容被用来做钓鱼（假按钮、假链接）：渲染器不生成链接、不生成图片，只有一个提交按钮且只提交到本服务；视图标题与文本都是纯文本。产物详情页的 Markdown 预览是 M75 既有行为，不渲染视图里的 Markdown。
- 用户把表单答案当成授权：提示词与 `common.md` 写明答案不等于确认，策略引擎照常拦截消耗与破坏类操作。
- Pydantic 嵌套判别联合在当前版本的表现：T1 用例会暴露；本机 2.11.10 已实测可表达，错误 `loc` 的清洗规则也已在 §4.3.1 写死。出现异常时停下来问。
- 并发提交同一张表单：`meta` 是读改写，后写覆盖先写；同一个 Worker 会话的实际提交是串行的，跨标签页同时提交会丢一条记录，响应都按成功返回。单次提交限 4 KiB、只留 5 条，把损失限制在提交记录。
- 详情 `meta` 返回内部键：`facts_markdown` 这类简报正文本来就能从产物正文读到，控制台登录用户可读；04/03 已注明含内部键。
- Worker 版本号漂移：M87/M89 也会改这四个定义；T3 以开工时实际值为准，不照抄本文的当前值。
- 回滚：`git revert -m 1 <merge>`；没有迁移；已建的视图产物在回滚后仍可下载，只是不能就地渲染。

## 9. 文档同步

计数（M90 后）：

| 项 | 值 |
|---|---|
| 工具 | **111** 个，模型可见 **110** 个（M89 后 110/109，+1） |
| HTTP 端点 | **191** 条，agent 小节 **88** 条（M89 后 190/87，+1） |
| 错误码 | **173** 条，绑定 HTTP **172** 条（不变） |
| 业务表 | **58** 张（不变） |
| 配置键 | `SETTING_KEYS` **144** 个，`_FIELDS` **133** 个（不变） |
| 迁移 | 无；head 是 `0042_demonstration` |
| 评测场景 | 不新增；以 M89 台账记录的场景集与通过率为基线。若后续补场景，`id` 与文件名主干一致，只用小写字母、数字与连字符（`^[a-z0-9][a-z0-9-]{2,63}$`） |

要改的文档：

- [03 §8.2](../03-数据模型.md)：`mime` 行补 `application/vnd.maa.view+json` 与「视图正文是 v1 JSON」；`meta` 行补 `view_version`、`submissions`（`[{at, values}]`，只留最近 5 条）、`last_submitted_at`，并注明详情端点对外返回整个 `meta`（含简报内部键）。顺手修 §9.2 `knowledge_document.meta` 那行从 §8.2 复制错的文字，改成「来源侧的附加元数据，与 §8.2 的产物 meta 无关」。
- [04 §2.3](../04-接口与事件协议.md)：M75 详情行补「含 `meta`」。
- [04 §2.5](../04-接口与事件协议.md)：M90 行补请求/响应模型名与主要错误码（`ARTIFACT_NOT_FOUND`、`INVALID_PARAMETER`、`TICKET_NOT_FOUND`、`TICKET_ENDED`、`SERVICE_UNAVAILABLE`、`AGENT_DISABLED`、`LLM_NOT_CONFIGURED`、`LLM_ENCRYPTION_KEY_INVALID`、`VALIDATION_ERROR`）。
- [04 §3](../04-接口与事件协议.md)：删掉 `artifact_created` 行；[04 §4.2](../04-接口与事件协议.md) 的 `agent_event` 行补 `artifact_created`（M75，载荷 `{run_id, artifact_id, kind, title}`）。这次只改通道描述，不改后端实现。
- [02 §4.1](../02-系统架构.md)：`artifacts/` 一行补 `views.py`（M90）。
- [02 §6.3](../02-系统架构.md)：补一句「表单提交写成用户消息，正文以【表单提交】开头，Worker 按用户本人的话对待」。
- [05 §3.2](../05-运行时与代理定义.md)：`general`、`analyst`、`planner`、`copilot` 的工具列补 `create_view`（M90）；[05 §6.1](../05-运行时与代理定义.md) 的计数句改成 M90 后的 111/110（开工时那里的值应是 M89 后的 110/109）；[05 §7](../05-运行时与代理定义.md) 的 `common.md` 分层说明补一句【表单提交】也是用户输入，不新增收件箱种类。
- [11](../11-前端指挥台.md) §5 的链接去掉 `todo/` 与「搁置」标记；§6.1 删掉 `artifact_created` 行；§6.3 补 `artifacts(ticketId)`、`artifactsAll()`、`artifact(id)`、`artifactsList(filters)` 四个 key 与 `agent_event.artifact_created → artifactsAll()` 的失效行。§2.3、§4.2、§9 已有 M90 行，不改。
- [12 §2](../12-实施路线与里程碑.md)：M90 行的链接去掉 `todo/` 与「搁置」标记，迁移仍写「无」。
- [14 §6.15](../14-现行接口清单.md)：agent 小节加一行，`（87 条）` 改成 `（88 条）`，§6 开头的总数从 190 改 191。新行照抄：
  `| POST | /api/agent/artifacts/{artifact_id}/submit | 提交生成式表单（M90）：按视图定义校验，作为一条用户消息写进对应工单 Worker 的收件箱并唤醒 | {values: object} | 200 {artifact_id, ticket_id, submitted_at} | ARTIFACT_NOT_FOUND、INVALID_PARAMETER、TICKET_NOT_FOUND、TICKET_ENDED、SERVICE_UNAVAILABLE、AGENT_DISABLED、LLM_NOT_CONFIGURED、LLM_ENCRYPTION_KEY_INVALID、VALIDATION_ERROR |`
- [13 §8](../13-决策记录.md)：末尾补一条「M90 为提交记录扩展详情 wire」，写明只加 `ArtifactDetailOut.meta`、列表不加、`meta` 含内部键，04/03 已同步。
- `maa_api/domain/enums.py` 的 `ArtifactKind` docstring 改成 `note`、`spill`、`report`、`chart`、`table`、`file`、`form`、`dashboard`。
- [01](../01-总览与范围.md) 的 M90 行已存在，不改。
- 升格时：删除本文件开头的搁置横幅三行（本任务已完成删除）、把文件移到 `docs/v3/specs/`、同步 [11](../11-前端指挥台.md) 第 5 行、[12](../12-实施路线与里程碑.md) 第 56 行、`docs/v3/specs/todo/README.md` 的 M90 行。本 spec 内的相对链接按 `docs/v3/specs/` 位置写。

## 10. 停下来问

- 前置检查 #2 或 #3 不通过：没有 `v3-m89` tag，或 `docs/v3/ledgers/m89-*.md` 不存在、没有 capability 记录。
- 前置检查 #9 的工具计数不是 110/109，或 14 的端点不是 190/87、错误码不是 173/172。
- 前置检查 #4 的 `put`/`get`/`update` 签名与 §4.5.1、§4.5.2 不一致。
- 前置检查 #14 的 `post_user_message` 签名变了，或它不再写收件箱与唤醒。
- 不能只给 `ArtifactDetailOut` 加 `meta`，或有人要求改 M75 端点的其它语义。
- Pydantic 嵌套判别联合在开工时的版本下不能表达 §4.3.1，或错误 `loc` 的清洗结果与 §4.3.1 的示例不一致。
- capability 评测通过率低于 M89 基线。
- 要新增端点、错误码、配置项、依赖或表才能完成某一步。

## 11. 需要补进契约的名字

| 名字 | 现状 | 应补进 |
|---|---|---|
| `VIEW_MIME = "application/vnd.maa.view+json"`、视图 v1 schema、`VIEW_VERSION = 1` | 缺失 | [03 §8.2](../03-数据模型.md) 的 `mime` 行、[04 §2.5](../04-接口与事件协议.md) |
| `maa_api/agent/artifacts/views.py`（`ViewSpec`、`validate_view`、`view_kind`、`view_fields`、`view_json_schema`、`validate_submission`、`render_submission`、`ViewInvalid`） | 缺失 | [02 §4.1](../02-系统架构.md) |
| `ArtifactFormSubmit`、`ArtifactFormSubmitResult` | 缺失 | [04 §2.5](../04-接口与事件协议.md) |
| `ArtifactDetailOut.meta: dict[str, Any]` | 缺失 | [04 §2.3](../04-接口与事件协议.md)、[03 §8.2](../03-数据模型.md)、[13 §8](../13-决策记录.md) |
| `meta` 的 `view_version`、`submissions`、`last_submitted_at` | 缺失 | [03 §8.2](../03-数据模型.md) |
| 【表单提交】前缀 | 缺失 | [02 §6.3](../02-系统架构.md)、[05 §7](../05-运行时与代理定义.md) |
| `create_view` 分配给 `general`、`analyst`、`planner`、`copilot` | 部分（[05 §6.1](../05-运行时与代理定义.md) 有工具行，[05 §3.2](../05-运行时与代理定义.md) 没有） | [05 §3.2](../05-运行时与代理定义.md)、[05 §6.1](../05-运行时与代理定义.md) |
| `keys.agent.artifactsList(filters)`；复用 `artifacts(ticketId)`、`artifactsAll()`、`artifact(id)` | `artifacts` 已是函数，`artifactsList` 缺失 | [11 §6.3](../11-前端指挥台.md) |
| `artifactsListInfiniteQuery`、`usePinArtifact`、`useSubmitArtifactForm`、`downloadName`、`ViewArtifactBody`、`ViewArtifactCard`、`ArtifactChip` | 新前端符号 | [11 §9](../11-前端指挥台.md) 的产物页函数表补一行组件与 key 说明 |
| `ARTIFACT_KIND_LABELS.dashboard = 看板` | 已有，值为「仪表盘」 | [11 §9](../11-前端指挥台.md) 的产物页说明、`ArtifactList` 测试 |
| 05 §6.1 的工具计数句 | 开工时是 M89 后的 110/109 | [05 §6.1](../05-运行时与代理定义.md)，改成 111/110 |
| 14 §6.15 的 agent 条数与 §6 总数 | 当前 79/182，M89 后应为 87/190 | [14](../14-现行接口清单.md)，M90 后 88/191 |
| [11](../11-前端指挥台.md) 第 5 行、[12](../12-实施路线与里程碑.md) 第 56 行、`specs/todo/README.md` 的 M90 链接 | 指向 `specs/todo/` 且带搁置标记 | 升格时一起改 |

## 附录 A：前置检查脚本

本机没有 ripgrep，下面全部用 `grep`；验收命令里也不要写 ripgrep 的短命令名。

### A1 产物端点

```bash
.venv/bin/python - <<'EOF'
import json
paths = json.load(open("web/openapi.json"))["paths"]
need = [
    ("get", "/api/agent/artifacts"), ("get", "/api/agent/artifacts/{artifact_id}"),
    ("get", "/api/agent/artifacts/{artifact_id}/content"), ("put", "/api/agent/artifacts/{artifact_id}/pin"),
]
missing = [f"{m.upper()} {p}" for m, p in need if m not in paths.get(p, {})]
print("\n".join(missing))
print(f"MISSING {len(missing)}")
EOF
```

期望末行 `MISSING 0`。

### A2 工具计数

```bash
.venv/bin/python -c "from maa_api.agent.tools import build_registry as b; r=b(); print(len(r.list()), sum(1 for d in r.list() if d.model_visible))"
```

§0 期望 `110 109`；M90 完成后期望 `111 110`。

### A3 后端签名与 wire

```bash
grep -n "async def put\|async def get\|async def update(" maa_api/agent/artifacts/service.py
grep -n "class ArtifactOut\|class ArtifactDetailOut" -A 14 maa_api/api/schemas/agent_v3.py
grep -n "async def post_user_message" -A 6 maa_api/agent/workers/service.py
grep -n "class ArtifactKind" -A 10 maa_api/domain/enums.py
```

期望：第一条 3 行；第二条能看到 `ArtifactDetailOut` 的 `content`、`truncated`，T4 后多一行 `meta`；第三条是 `(ticket_id, content, mentions, *, notify_content=None)`；第四条能看到 `FORM` 与 `DASHBOARD`。

### A4 前端符号

```bash
grep -n "artifacts: (ticketId\|artifactsAll:\|artifact: (artifactId\|artifactsList:" web/src/api/keys.ts
grep -n "ARTIFACT_KIND_LABELS\|dashboard:" web/src/features/tickets/ArtifactList.tsx
grep -n "^function parseChartCard\|^export function parseChartCard" web/src/features/agent/media.ts
```

期望：第一条在 T6 后 4 行；第二条在 T6 后 `ArtifactList.tsx` 不再定义 `ARTIFACT_KIND_LABELS`（改成 import），`features/artifacts/format.ts` 里 `dashboard: '看板'`；第三条在 T5 后带 `export`。

### A5 计数

```bash
.venv/bin/python - <<'EOF'
import json
from maa_api.domain.errors import ErrorCode, ERROR_HTTP_STATUS
from maa_api.services.settings_schema import _FIELDS
from maa_api.settings import SETTING_KEYS
from tests.db.test_models import BUSINESS_TABLES

paths = json.load(open("web/openapi.json"))["paths"]
ops = [(p, m, op) for p, item in paths.items() for m, op in item.items() if isinstance(op, dict)]
print("OPERATIONS", len(ops), "AGENT", sum(1 for _, _, op in ops if "agent" in op.get("tags", [])))
print("ERRORS", len(ErrorCode), len(ERROR_HTTP_STATUS))
print("TABLES", len(BUSINESS_TABLES))
print("SETTINGS", len(SETTING_KEYS), len(_FIELDS))
EOF
```

§0 期望 `OPERATIONS 190 AGENT 91`、`ERRORS 173 172`、`TABLES 58`、`SETTINGS 144 133`；M90 完成后期望 `OPERATIONS 191 AGENT 92`，其余三行不变。`AGENT` 是 OpenAPI 里 tag 含 `agent` 的 operation 数；14 §6.15 的 87/88 是小节行数，两个口径不同源，不要混用。不是这些值时按 §10 停下来问。

### A6 安全扫描与命名冲突

```bash
grep -rn "create_view" maa_api tests web/src; echo $?
grep -rnE "dangerouslySetInnerHTML|<img|<iframe|<a |href=|<object|<embed" web/src/features/artifacts/view; echo $?
grep -n "artifacts: {" web/src/api/keys.ts; echo $?
```

§0 期望第一条无输出、退出码 1；T5 完成后第二条无输出、退出码 1；第三条无输出、退出码 1（`artifacts` 是函数，不能被对象覆盖）。
