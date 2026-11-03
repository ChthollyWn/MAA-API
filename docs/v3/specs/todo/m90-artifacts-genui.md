> 状态：TODO（先搁置，不要照此实施）
> 阶段：P4。领域亮点及之后的规格先搁置，等 P0–P3（M63–M85）落地后再决定是否升为可执行规格。
> 本文件正文是提前写好的草稿，执行者不要从这里开工。

# M90 产物与生成式 UI（规格）

> 阶段：P4　依赖：M89（顺序依赖；功能上用到 M75 的产物服务与 M72 的会话组件）　分支：`artifacts-genui`　迁移：无
> 本文是 M90 的唯一规格；实施偏离记在 [13 §8](../../13-决策记录.md)。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在 dev 上新建的分支，工作区干净 | `git status -sb` | `## artifacts-genui`，没有改动 |
| 2 | M89 已合并 | `git tag -l 'v3-m89'` | `v3-m89` |
| 3 | 产物服务存在 | `rg -n "class ArtifactService\|async def (create\|get)\b" maa_api/agent/artifacts/service.py` | 类与 `create`、`get` 各一行；把两个方法的完整签名抄进台账，T2、T4 按它调用 |
| 4 | 产物端点在 OpenAPI 快照里 | 附录 A 的脚本 B1 | 末行 `MISSING 0` |
| 5 | `ArtifactKind` 有 `form`、`dashboard` | `rg -n "FORM = \|DASHBOARD = " maa_api/domain/enums.py` | 两行 |
| 6 | M72 的会话文件都在 | `ls web/src/features/console/conversation/ConversationView.tsx web/src/features/console/conversation/LiveTurnView.tsx web/src/features/console/conversation/transcript-v3.ts web/src/features/console/conversation/live-turn.ts web/src/features/console/conversation/sse.ts` | 5 个文件 |
| 7 | M75 是否已在前端处理 `artifact_created` | `rg -n "artifact_created\|kind: 'artifact'" web/src/features/console/conversation` | 有匹配：T5 在其上扩展；没有：T5 一并实现 §4.6.4 的产物段 |
| 8 | `parseChartCard` 还没导出 | `rg -n "^function parseChartCard" web/src/features/agent/media.ts` | 一行 |
| 9 | 工具目录当前的计数 | `rg -n "assert len\(actual\) ==\|model_visible\) ==" tests/agent/test_tool_catalog.py` | 记下总数与模型可见数，T2 各加 1 |
| 10 | 三个 Worker 定义存在 | `ls maa_api/agent/specs/builtin/general.md maa_api/agent/specs/builtin/analyst.md maa_api/agent/specs/builtin/planner.md` | 3 个文件 |
| 11 | 工单消息的服务端写入函数 | `rg -n "async def .*message" maa_api/agent/tickets maa_api/agent/workers` | 找到 `send_ticket_message` 路由背后写 Worker 收件箱、唤醒 Worker、给总控写摘要的那个函数（M72 在它前面接了提及展开）；名字与签名写进台账，T4 调用它 |
| 12 | `create_view` 还没出现 | `rg -n "create_view" maa_api tests web/src` | 没有输出 |
| 13 | 产物路由文件 | `rg -n "^async def (list_artifacts\|get_artifact\|download_artifact\|pin_artifact)\(" maa_api/api/routers/artifacts.py` | 四行 |
| 14 | 前端产物 key | `rg -n "artifacts" web/src/api/keys.ts` | 有就复用（T6 跳过加 key），没有就按 §4.6.1 加 |
| 15 | 基线 | `.venv/bin/python -m pytest -q > /tmp/m90-base.log 2>&1; echo $?` 与 `cd web && pnpm test --run > /tmp/m90-vitest-base.log 2>&1; echo $?; cd ..` | 记录退出码与已有失败 |

## 1. 背景与目标

M75 让 Worker 能写笔记、外置大输出、存报告，但只能产出文本与 v2 的四种媒体卡。很多场景需要一张能看能填的视图：资源规划要对比几个关卡的期望理智，作业研究要让用户在几份作业里挑一份，养成规划要用户确认几个参数。本里程碑加生成式 UI，做完后：

1. Worker 调用 `create_view` 生成一张视图，存成产物（`kind=dashboard` 或 `form`）。
2. 视图只能用白名单组件：标题、文本、关键数字、表格、图表（复用 v2 的 ECharts 媒体卡）、表单字段（select、number、text、toggle）、提交按钮。服务端按 §4.3 的 JSON Schema 与附加规则校验，不合法时工具返回 `TOOL_ARGS_INVALID` 与出错路径，模型据此改正。
3. 前端在工单会话里就地渲染视图，产物页也能打开；渲染器不执行任何脚本、不加载任何外链资源、不渲染 HTML 与 Markdown。
4. 表单经 `POST /api/agent/artifacts/{artifact_id}/submit` 提交：服务端按视图定义校验，作为一条用户消息写进工单 Worker 的收件箱并唤醒它。
5. 「更多 › 产物」列出全部产物，可筛选、置顶、下载、查看。
6. 12 §4 的门禁全部通过，capability 评测不低于 M89 基线。

## 2. 非目标

- 视图编辑器、拖拽、主题、自定义组件；图片、链接、HTML、Markdown、iframe、文件上传字段。
- 视图数据实时刷新：视图是生成时的快照，要新数据就再生成一张。
- 总控、子代理、后台代理使用 `create_view`；只给 `general`、`analyst`、`planner` 三个 Worker。
- 表单答案代替确认：答案只是用户的意见，消耗类与破坏类操作照旧逐次确认。
- 新表、新错误码、新配置项、新依赖（不引入 `jsonschema`，校验用 Pydantic）。
- 改 M75 已有产物端点的语义。

## 3. 相关文档

- [11](../../11-前端指挥台.md) §2.3、§4.2、§6.1、§9；[04](../../04-接口与事件协议.md) §2.3（M75 产物端点）、§2.5（M90 行）、§3（`artifact_created`）；[05](../../05-运行时与代理定义.md) §2.4、§6.1（`create_view` 行）、§7；[03](../../03-数据模型.md) §8.2；[02](../../02-系统架构.md) §6.3。
- [文案规范](../../../copy-audit/规范-文案规范.md)。
- 既有代码入口：

| 位置 | 符号 | 用途 |
|---|---|---|
| `web/src/features/agent/media.ts` | `parseChartCard`（约 348）、`ChartCardData`（约 97）、`MEDIA_LIMITS`（约 16） | 图表组件复用媒体卡的解析与上限 |
| `web/src/features/agent/MediaCards.tsx` | `ChartCard`（约 362） | 图表组件直接渲染它（含「数据」折叠表与加载失败兜底） |
| `web/src/components/ui/` | `Field`、`NativeSelect`、`Input`、`Textarea`、`SwitchRow`、`Chip`、`Button`、`InlineAlert`、`Badge` | 表单控件 |
| `web/src/features/console/conversation/` | `transcript-v3.ts` 的 `ConversationEntry`、`ConversationView.tsx`、`LiveTurnView.tsx`、`live-turn.ts` 的 `LiveSegment`、`sse.ts` 的 `SseV3Event` | 在会话里挂视图卡 |
| `web/src/features/tickets/api.ts` | `ticketDetailQuery` | 产物详情页显示所属工单 |
| `maa_api/agent/tools/__init__.py` | `build_registry()` | 注册新工具 |
| `maa_api/agent/registry.py` | `ToolDefinition`（含 M65 加的 `device`、`refetchable`、`untrusted_output`、`rest_visible`） | 工具定义 |
| `tests/agent/test_tool_catalog.py` | `EXPECTED_TOOLS` 与两个计数 | 工具全集 |
| `maa_api/api/routers/artifacts.py` | M75 的四个端点 | 加提交端点 |

## 4. 设计

### 4.1 数据模型

不加表。视图存进 `artifact`（03 §8.2）：

| 列 | 值 |
|---|---|
| `kind` | 有至少一个表单字段时 `form`，否则 `dashboard` |
| `title` | 视图的 `title` |
| `mime` | `application/vnd.maa.view+json` |
| `storage` | `db`（视图不超过 64 KiB） |
| `content` | 校验后的视图 JSON（`model_dump_json(exclude_none=True)`） |
| `session_id` | 调用者的会话 |
| `ticket_id` | 调用者会话的 `agent_session.ticket_id` |
| `run_id` | 该会话唯一的活动运行（`uq_agent_run_active_session` 保证至多一个），找不到为空 |
| `meta` | `{"view_version": 1, "submissions": [], "last_submitted_at": null}`；每次提交在 `submissions` 末尾追加 `{"at": "<ISO Z>", "values": {...}}`，只留最近 5 次，并更新 `last_submitted_at` |

### 4.2 模块与文件

后端：

| 路径 | 新建或修改 | 职责 |
|---|---|---|
| `maa_api/agent/artifacts/views.py` | 新 | 视图模型、校验、提交校验与渲染 |
| `maa_api/agent/tools/artifacts.py` | 新 | `create_view` 工具 |
| `maa_api/agent/tools/__init__.py` | 改 | `build_registry()` 末尾注册 `artifacts` 组 |
| `maa_api/api/routers/artifacts.py` | 改 | `submit_artifact_form` 端点与两个模型 |
| `maa_api/agent/specs/builtin/general.md`、`analyst.md`、`planner.md` | 改 | `tools` 加 `create_view`，`version` 加 1，正文加一句（§4.7） |
| `maa_api/agent/specs/common.md` | 改 | 加【表单提交】前缀的说明（§4.7） |
| `tests/agent/test_views.py`、`tests/agent/test_create_view_tool.py`、`tests/api/test_artifact_form_submit.py` | 新 | §6 |
| `tests/agent/test_tool_catalog.py` | 改 | 工具全集与计数 |
| `docs/v3/14-现行接口清单.md` | 改 | agent 小节加一行 |

```python
# maa_api/agent/artifacts/views.py
"""生成式视图：白名单组件的模型、附加规则、表单提交的校验与渲染。"""
VIEW_MIME = "application/vnd.maa.view+json"
VIEW_VERSION = 1
FIELD_NAME = r"^[a-z][a-z0-9_]{0,31}$"
MAX_COMPONENTS = 40
MAX_FIELDS = 20
MAX_VIEW_BYTES = 64 * 1024
MAX_SUBMISSIONS_KEPT = 5

class ViewInvalid(ValueError):
    """errors 的每项是 {"path": "components.3.options", "message": "…"}，最多 20 项。"""
    errors: list[dict[str, str]]

class ViewSpec(BaseModel): ...          # 各组件模型见 §4.3 的 JSON Schema，一一对应

def validate_view(raw: Any) -> ViewSpec:
    """先用 Pydantic 校验（Pydantic 的错误转成 path/message），再查 §4.3 的附加规则。"""
def view_kind(spec: ViewSpec) -> Literal["form", "dashboard"]: ...
def view_fields(spec: ViewSpec) -> list[FieldBlock]: ...
def view_json_schema() -> dict[str, Any]:
    """TypeAdapter(ViewSpec).json_schema()；只给测试与文档核对用，不进工具声明。"""
def validate_submission(spec: ViewSpec, values: Mapping[str, Any]) -> dict[str, Any]:
    """按字段定义校验并规整；失败抛 ViewInvalid，path 是字段名。"""
def render_submission(spec: ViewSpec, values: Mapping[str, Any], *, artifact_id: str) -> str:
    """写给 Worker 的那条用户消息正文，格式见 §4.5.3。"""
```

Pydantic 写法要点：所有模型 `ConfigDict(extra="forbid", str_strip_whitespace=True)`；数值用 `Annotated[float, Field(allow_inf_nan=False)]`；组件是按 `type` 区分的判别联合，`type="field"` 的那一支再按 `field` 区分（嵌套判别联合）；字符串长度、数组长度、正则照 §4.3。

```python
# maa_api/agent/tools/artifacts.py
class CreateViewParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    view: dict[str, Any] = Field(description="视图定义，组件只能是 heading、text、stats、table、chart、field、submit")

async def create_view(params: CreateViewParams, ctx: ToolContext) -> dict[str, Any]:
    """校验视图 → 找会话的工单与活动运行 → ArtifactService.create → 返回 {artifact_id, kind, title, field_count}。"""

def register(registry: ToolRegistry) -> None:
    registry.register(ToolDefinition(
        name="create_view", group="artifacts", risk=ToolRisk.SAFE,
        description=CREATE_VIEW_DESCRIPTION,         # §4.7
        params_model=CreateViewParams, handler=create_view,
        read_only=False, model_visible=True,
        device="none", refetchable=False, untrusted_output=False, rest_visible=False,
    ))
```

工具声明里 `view` 只声明为对象，不展开整份 schema：组件的完整形状靠描述里的组件名与校验错误的路径引导，工具声明保持短，前缀缓存也稳定。

```python
# maa_api/api/routers/artifacts.py（新增）
class ArtifactFormSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, Any] = Field(default_factory=dict)

class ArtifactFormSubmitResult(BaseModel):
    artifact_id: str
    ticket_id: str
    submitted_at: str          # ISO 8601 带 Z

@router.post(
    "/artifacts/{artifact_id}/submit",
    response_model=ArtifactFormSubmitResult,
    summary="提交生成式表单",
    responses=error_responses("ARTIFACT_NOT_FOUND", "INVALID_PARAMETER", "TICKET_ENDED", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def submit_artifact_form(artifact_id: str, payload: ArtifactFormSubmit, request: Request) -> ArtifactFormSubmitResult: ...
```

路由沿用该文件已有的 `tags=["agent"]`，operationId 因此是 `agent_submit_artifact_form`（04 §2.5）。

前端（路径都在 `web/src/` 下）：

| 路径 | 新建或修改 | 职责 |
|---|---|---|
| `features/agent/media.ts` | 改 | `function parseChartCard` 前加 `export`，别的不动 |
| `features/artifacts/view/schema.ts` | 新 | 视图类型、`parseView`、初值与就地校验 |
| `features/artifacts/view/ViewRenderer.tsx` | 新 | 按组件渲染 |
| `features/artifacts/view/ViewField.tsx` | 新 | 四种表单字段 |
| `features/artifacts/view/ViewTable.tsx`、`ViewStats.tsx` | 新 | 表格、关键数字 |
| `features/artifacts/ViewArtifactBody.tsx` | 新 | 读产物、解析、表单状态与提交（卡片与详情页共用） |
| `features/artifacts/ViewArtifactCard.tsx` | 新 | 会话里的视图卡 |
| `features/artifacts/ArtifactChip.tsx` | 新 | 非视图产物的胶囊（M75 已有同等组件时复用它，不新建） |
| `features/artifacts/api.ts`、`format.ts` | 新 | 查询、提交、置顶、下载；文案 |
| `features/artifacts/ArtifactsPage.tsx`、`ArtifactDetailPage.tsx` | 新 | 产物页 |
| `features/console/conversation/transcript-v3.ts`、`ConversationView.tsx`、`LiveTurnView.tsx`、`live-turn.ts`、`sse.ts` | 改 | 挂视图卡（§4.6.4） |
| `api/keys.ts`、`components/layout/nav.ts`、`routes/router.tsx` | 改 | key、导航、路由 |

### 4.3 接口

#### 4.3.1 视图 JSON Schema（v1）

服务端的 Pydantic 模型与下面的 schema 一一对应；前端 `parseView` 用同样的上限。

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

附加规则（schema 表达不了，`validate_view` 逐条检查，路径照写）：

1. 表单字段的 `name` 互不相同；字段总数不超过 20。
2. 有字段时恰好一个 `submit`，而且必须是最后一个组件；没有字段时不能有 `submit`。
3. 表格每一行的键都必须是已声明的 `columns[].key`；缺的键按 `null`。
4. 图表每条 `series.values` 的长度等于 `categories` 的长度；`chart_type="pie"` 时只能有一条系列。
5. 数字字段：`min`、`max` 都有时 `min <= max`；`default` 落在范围内。
6. 选择字段：`multiple=false` 时 `default` 是字符串，`true` 时是字符串数组；`default` 里的值都要在 `options[].value` 里；`options[].value` 互不相同。
7. 文本字段：`default` 的长度不超过 `max_length`。
8. 任何字符串里不能有控制字符（`\x00`–`\x08`、`\x0b`–`\x1f`、`\x7f`）；`text` 组件与文本字段的 `default` 允许 `\n`。
9. 数值一律有限（`NaN`、`Infinity` 拒绝）。
10. `model_dump_json(exclude_none=True)` 的 UTF-8 字节数不超过 64 KiB。

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

- 参数：`{"view": <视图对象>}`。
- 返回：`{"artifact_id": "...", "kind": "form", "title": "这周刷哪一关", "field_count": 3}`。
- 视图不合法：`AppError(TOOL_ARGS_INVALID, "视图不合法：<前 3 条错误，每条「路径：原因」，用；连接>", {"errors": [前 20 条]})`。
- 调用者会话不属于任何工单：`AppError(INVALID_PARAMETER, "只能在工单里生成视图")`。

#### 4.3.3 端点 `POST /api/agent/artifacts/{artifact_id}/submit`

请求 `{"values": {"stage": "1-7", "times": 10, "use_medicine": false}}`。

| 情形 | 状态码 | 错误码 | message | details |
|---|---|---|---|---|
| 成功 | 200 | | | 响应 `{"artifact_id": "...", "ticket_id": "...", "submitted_at": "2026-12-01T08:00:00Z"}` |
| 产物不存在 | 404 | `ARTIFACT_NOT_FOUND` | `没有这个产物` | |
| 产物不是表单（`kind != "form"` 或 `mime` 不是视图） | 400 | `INVALID_PARAMETER` | `这个产物不是表单` | |
| 产物里存的视图读不出或校验不过 | 400 | `INVALID_PARAMETER` | `表单定义已损坏，请让 Worker 重新生成` | |
| 产物不属于工单 | 400 | `INVALID_PARAMETER` | `这个表单不属于任何工单` | |
| 值不合法 | 400 | `INVALID_PARAMETER` | `表单有 {n} 处需要修改` | `{"fields": {"<name>": "<原因>"}}` |
| 工单已结束 | 409 | `TICKET_ENDED` | `工单已结束，不能再提交` | |

字段错误原因的原文：`必填`、`不在可选项里`、`选项不合法`、`需要填数字`、`不能小于 {min}`、`不能大于 {max}`、`不能超过 {max_length} 字`、`需要是开或关`、`表单里没有这一项`。

SSE、WebSocket：不加事件。提交之后 Worker 被唤醒，前端经 M72 已有的 `agent_run`、`ticket_status` 失效看到后续。

### 4.4 配置项

无。

### 4.5 行为与算法

#### 4.5.1 `create_view`

```
validate_view(params.view)            → 失败抛 TOOL_ARGS_INVALID（§4.3.2）
session = AgentSession(ctx.session_id) → ticket_id = session.ticket_id；为空抛 INVALID_PARAMETER
run_id = 该会话 status in ('running','waiting_human') 的那条 agent_run 的 id，没有为 None
artifact = await app.state.artifact_service.create(
    kind=view_kind(spec), title=spec.title, mime=VIEW_MIME,
    content=spec.model_dump_json(exclude_none=True),
    session_id=ctx.session_id, ticket_id=ticket_id, run_id=run_id,
    meta={"view_version": 1, "submissions": [], "last_submitted_at": None})
    # 参数名以前置检查 #3 抄下的签名为准
return {"artifact_id": artifact.id, "kind": artifact.kind, "title": artifact.title, "field_count": len(view_fields(spec))}
```

M75 的服务在建产物时若会发 `artifact_created`，照常发；前端主要靠 `create_view` 的工具结果挂视图卡，`artifact_created` 只作兜底（§4.6.4）。

#### 4.5.2 提交

```
artifact = await artifact_service.get(artifact_id)           → 没有：404 ARTIFACT_NOT_FOUND
校验 kind == "form" 且 mime == VIEW_MIME                      → 否则 400「这个产物不是表单」
spec = validate_view(json.loads(artifact.content))            → 异常：400「表单定义已损坏…」
ticket = await session.get(Ticket, artifact.ticket_id)        → 为空：400「这个表单不属于任何工单」
ticket.status in (done, failed, cancelled)                    → 409 TICKET_ENDED
values = validate_submission(spec, payload.values)            → ViewInvalid：400，details.fields
content = render_submission(spec, values, artifact_id=artifact.id)
await <前置检查 #11 的工单消息写入函数>(ticket.id, content)     # 与 send_ticket_message 同路：写用户消息与 Worker 收件箱（thread_key ticket:<id>）、唤醒 Worker、给总控写 system 摘要
meta = 原 meta；submissions 追加 {at, values} 后只留最近 5 次；last_submitted_at = at
整体赋值写回 artifact.meta（JSON 列不原地修改，03 §1），updated_at = utcnow()
return {artifact_id, ticket_id, submitted_at: at}
```

同一张表单可以多次提交（改主意、修正填错），每次都是一条新消息；前端提交成功后默认收起表单，避免误触重复提交。

`validate_submission` 的规则：

| 字段 | 合法值 | 缺省 |
|---|---|---|
| `select`，`multiple=false` | 字符串，且在 `options[].value` 里 | 必填缺失报 `必填`；选填缺失不出现在结果里 |
| `select`，`multiple=true` | 字符串数组，元素都在选项里、互不相同；必填时至少一个 | 同上 |
| `number` | 数字（布尔不算），有限，落在 `min`–`max` | 同上 |
| `text` | 字符串，去首尾空白后不超过 `max_length`；必填时不能为空 | 同上 |
| `toggle` | 布尔 | 缺失按 `false` |
| 不在视图里的键 | 报 `表单里没有这一项` | |

#### 4.5.3 写给 Worker 的消息

```
【表单提交】{视图标题}（产物 {artifact_id 前 8 位}）
- {label}：{值}
- {label}：{值}
```

值的写法：单选 `{选项 label}（{value}）`；多选每项同上、用「、」连接；数字 `{数字}{unit}`；文本原样（多行时每行前加两个空格续写）；开关 `是` / `否`；选填没填 `（未填）`。例子：

```
【表单提交】这周刷哪一关（产物 1a2b3c4d）
- 关卡：1-7（1-7）
- 次数：10
- 可以用理智药：否
```

#### 4.5.4 前端解析与安全

`parseView(raw)` 永不抛异常：顶层不是对象、`version !== 1`、`title` 缺失、`components` 不是数组时返回 `null`；单个组件不合法（未知 `type`、缺必填字段、超上限、值类型不对）就丢弃并计入 `dropped`；图表组件用 `parseChartCard({ chart: component.chart, caption: component.caption ?? '' })`，返回 `null` 也丢弃。前端不信任服务端已校验过，上限与服务端相同。

渲染器的硬约束（`ViewRenderer.test.tsx` 有对应用例）：

1. 只产出固定的 React 元素：标题、段落、`dl`、`table`、表单控件、按钮与 v2 的 `ChartCard`。不用 `dangerouslySetInnerHTML`，不渲染 `img`、`iframe`、`a`、`object`、`embed`，不把视图里的任何字符串当 URL、类名、样式或事件处理。
2. 文本一律按纯文本渲染（`whitespace-pre-wrap`），不解析 Markdown；形如 `<img onerror=…>`、`https://…` 的文字原样显示。
3. 不发任何视图触发的网络请求；只有提交时调 `POST /api/agent/artifacts/{id}/submit`。
4. ECharts 的配置只由 `buildChartOption` 从解析后的 `ChartCardData` 生成（v2 媒体卡的同一条路），视图数据不能带格式化函数。
5. 数字用 `Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 })`；`percent` 列的值按 0–100 显示并加 `%`；布尔显示 `是` / `否`；`null` 显示 `—`。

交付前跑：`rg -n "dangerouslySetInnerHTML|<img|<iframe|<a |href=|<object|<embed" web/src/features/artifacts/view` → 没有输出。

#### 4.5.5 边界情况

| 输入 | 期望 |
|---|---|
| 模型给的视图有未知组件 `{"type": "html"}` | 工具返回 `TOOL_ARGS_INVALID`，路径 `components.N.type`；不建产物 |
| 有字段却没有 `submit` | `TOOL_ARGS_INVALID`，路径 `components` |
| 表格行里多了未声明的键 | `TOOL_ARGS_INVALID`，路径 `components.N.rows.M` |
| 用户提交时工单刚结束 | 409；卡片切到只读并提示 `工单已结束，不能再提交` |
| 用户改了表单再提交一次 | 200，Worker 收到第二条【表单提交】，`submissions` 两条 |
| 旧前端遇到新版本视图（`version` 不是 1） | `parseView` 返回 `null`，卡片显示 `视图无法显示` 与「在产物页打开」 |
| 产物正文超过 64 KiB（非视图产物） | 详情页显示截断提示与「下载」 |
| 表单答案里「可以用理智药：是」 | 只是用户意见；Worker 真要用药时照旧发起确认 |

### 4.6 前端

#### 4.6.1 key、查询与路由

```ts
// api/keys.ts 的 agent 下（前置检查 #14 显示已有就复用）
artifacts: {
  all: () => [...root, 'agent', 'artifacts'] as const,
  list: (filters: Readonly<Record<string, unknown>>) => [...root, 'agent', 'artifacts', 'list', filters] as const,
  detail: (artifactId: string) => [...root, 'agent', 'artifacts', 'detail', artifactId] as const,
},
```

| 查询或动作（`features/artifacts/api.ts`） | key | 端点 |
|---|---|---|
| `artifactListInfiniteQuery({ kind, ticketId })` | `artifacts.list({ kind, ticket_id, size: 20 })` | GET `/api/agent/artifacts?kind&ticket_id&page&size=20` |
| `artifactDetailQuery(id)` | `artifacts.detail(id)` | GET `/api/agent/artifacts/{artifact_id}` |
| `usePinArtifact()` | 成功失效 `artifacts.all()` | PUT `/api/agent/artifacts/{artifact_id}/pin`，body `{pinned}` |
| `useSubmitArtifactForm(id)` | 成功失效 `artifacts.detail(id)` | POST `/api/agent/artifacts/{artifact_id}/submit` |
| `downloadArtifact(id, title, mime)` | | `fetch('/api/agent/artifacts/{id}/content')`，带 `X-Token` 与 `X-Request-Id`（同 `streamSse` 的写法）→ blob → `URL.createObjectURL` → 程序生成的临时下载链接点击后立即移除并 `revokeObjectURL` |

产物详情的字段名以 M75 生成类型为准，预期正文是 `content`、截断标记是 `truncated`；不一致时只改 `format.ts` 的 `toArtifactView(raw)` 适配函数并记台账。

```ts
// features/artifacts/format.ts
export interface ArtifactView { id: string; kind: string; title: string; mime: string; sizeBytes: number; ticketId: string | null; pinned: boolean; content: string | null; truncated: boolean; meta: Record<string, unknown>; createdAt: string | null }
export function toArtifactView(raw: unknown): ArtifactView
export const ARTIFACT_KIND_LABELS: Record<string, string>   // note 笔记、spill 外置输出、report 报告、chart 图表、table 表格、file 文件、form 表单、dashboard 看板
export function formatBytes(size: number): string           // 812 B、3.4 KB、1.2 MB
export function downloadName(title: string, mime: string): string   // 标题去掉 \/:*?"<>| 后加扩展名：markdown→md、视图与 json→json、text/plain→txt、text/csv→csv、其他→bin
```

路由（`routes/router.tsx`）：`{ path: 'more/artifacts', element: page(<ArtifactsPage />) }`、`{ path: 'more/artifacts/:artifactId', element: page(<ArtifactDetailPage />) }`。导航（`nav.ts` 的 `secondaryNav`）加 `{ to: '/more/artifacts', label: '产物', icon: FileBox, description: '报告、表单、看板与外置的大输出', group: 'agent' }`。

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
export function validateValues(fields: readonly ViewField[], values: FormValues): Record<string, string>   // 与 §4.5.2 同口径，返回 name → 原因
export function toSubmitPayload(fields: readonly ViewField[], values: FormValues): Record<string, unknown> // 数字框的字符串转数字，空的选填项省略
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
- `table`：外层 `max-h-96 overflow-auto rounded-lg border`；`th scope="col"`，第一列单元格用 `th scope="row"`；`align='right'` 的列右对齐并 `tabular-nums`；`caption` 写在表下方小字。
- `chart`：直接 `<ChartCard card={card} />`。
- `field`：外层 v2 `Field`（`label`、`description`、`error`）。`select` 单选用 `NativeSelect`，首项是禁用的 `选择一项`；多选用一排 `Chip`（`selected` 表示选中）。`number` 用 `Input type="number" inputMode="decimal"`，带 `min`、`max`、`step`，单位写在输入框右侧。`text` 用 `Input`，`multiline` 时用 `Textarea`，都带 `maxLength`。`toggle` 用 `SwitchRow`。只读模式下全部 `disabled`。
- `submit`：`Button type="submit"`，文字取 `label`，提交中 `loading`；只读模式不渲染。

#### 4.6.3 视图卡与产物页

```ts
// features/artifacts/ViewArtifactBody.tsx
export interface ViewArtifactBodyProps { artifact: ArtifactView; ticketEnded: boolean }
// features/artifacts/ViewArtifactCard.tsx
export interface ViewArtifactCardProps { artifactId: string; ticketEnded: boolean }
// features/artifacts/ArtifactChip.tsx
export interface ArtifactChipProps { artifactId: string; title: string; kind: string }
```

- `ViewArtifactBody`：`parseView(JSON.parse(content))`，JSON 坏了或返回 `null` 时显示 `InlineAlert`「视图无法显示」；`dropped > 0` 时底部小字 `有 {n} 个组件无法显示`。表单状态：初值取 `meta.submissions` 最后一次的 `values`（有的话）否则取默认值；有过提交时初始为只读，显示 `已提交 · {时间}` 与按钮 `修改后重新提交`（切回可填）。提交：先 `validateValues`，有错就地显示不发请求；成功 Toast `已提交表单`、切只读；400 且 `details.fields` 存在时把原因落到对应字段，并在表单上方显示服务端的 message；409 `TICKET_ENDED` 切只读并显示 `工单已结束，不能再提交`；其他错误 `提交表单失败：{原因}`。`ticketEnded` 为真时一开始就是只读，提示同上。
- `ViewArtifactCard`：读 `artifactDetailQuery`；加载中 `Skeleton`；读失败 `读取产物失败：{原因}`；`mime` 不是视图时退化成 `ArtifactChip`。卡片头：图标（看板 `LayoutDashboard`、表单 `ClipboardList`）、标题、徽章 `看板` / `表单`、右侧链接 `在产物页打开`（去 `/more/artifacts/{id}`）；卡片体是 `ViewArtifactBody`。
- `ArtifactsPage`：`Page title="产物" back="/more"`；工具条一个 `NativeSelect`（标签 `种类`，选项 `全部` 与 `ARTIFACT_KIND_LABELS` 的八项），选择写 `?kind=`；`?ticket_id=` 存在时工具条多一个可移除的芯片 `只看这个工单`。列表行 `ListItem`：图标按种类，标题，描述 `{种类} · {formatBytes} · {时间}`，置顶的行 `trailing` 是徽章 `已置顶`；底部 `加载更多`。空状态 `还没有产物` / `Worker 生成报告、表单或看板后会列出来` / 按钮 `前往指挥台`。
- `ArtifactDetailPage`：`Page title={title} back="/more/artifacts"`；操作 `置顶` 或 `取消置顶`（Toast `已置顶` / `已取消置顶`）、`下载`（失败 `下载产物失败：{原因}`）。元信息一行：种类徽章、时间、大小、`所属工单「{标题}」`链接（`ticketDetailQuery`）。正文按 `mime`：视图 → `ViewArtifactBody`（工单已结束则只读）；`text/markdown` → v2 `Markdown`；`application/json`、`text/plain`、`text/csv` → 等宽 `pre`（`max-h-[70dvh] overflow-auto`）；其他 → `这种产物不能在页面里预览` 与 `下载`。`truncated` 为真时正文上方 `InlineAlert`「内容较长，只显示前 64 KiB」带 `下载`。404 时 `EmptyState`「没有这个产物」与按钮 `返回产物列表`。

#### 4.6.4 挂进会话（改 M72 的文件）

1. `sse.ts`：`SseV3Event` 加 `{ type: 'artifact_created'; runId: string; artifactId: string; kind: string; title: string }`（前置检查 #7 显示 M75 已加就跳过）。
2. `live-turn.ts`：`LiveSegment` 加 `{ kind: 'artifact'; artifactId: string; artifactKind: string; title: string }`，`artifact_created` 把它追加到对应运行（已有就跳过）。
3. `transcript-v3.ts`：`ConversationEntry` 的 `tool` 分支加 `artifactId: string | null`；`create_view` 调用取结果里 `result.artifact_id`（字符串才算）。
4. `ConversationView.tsx`：props 加 `ticketEnded?: boolean`；`tool` 条目带 `artifactId` 时，在工具条下方渲染 `<ViewArtifactCard artifactId ticketEnded />`。工单详情页把 `detail.ticket.status` 是否为终态传进来。
5. `LiveTurnView.tsx`：实时工具 `create_view` 有结果时同样挂视图卡；`artifact` 段里 `artifactKind` 是 `form`、`dashboard` 且同一运行里已经因 `create_view` 挂过同一个 id 的，不再重复；其余画 `ArtifactChip`。

#### 4.6.5 文案

| 位置 | 原文 |
|---|---|
| 徽章 | `表单`、`看板`、`已置顶` |
| 按钮 | 视图里的 `label`（缺省 `提交`）、`修改后重新提交`、`在产物页打开`、`置顶`、`取消置顶`、`下载`、`加载更多`、`前往指挥台`、`返回产物列表` |
| Toast | `已提交表单`、`已置顶`、`已取消置顶` |
| 提示 | `已提交 · {时间}`、`有 {n} 个组件无法显示`、`视图无法显示`、`工单已结束，不能再提交`、`内容较长，只显示前 64 KiB`、`这种产物不能在页面里预览`、下拉首项 `选择一项` |
| 错误 | `提交表单失败：{原因}`、`读取产物失败：{原因}`、`下载产物失败：{原因}`、服务端的 `表单有 {n} 处需要修改` 与 §4.3.3 的字段原因 |
| 空状态 | `还没有产物` / `Worker 生成报告、表单或看板后会列出来` / 按钮 `前往指挥台`；`没有这个产物` / 按钮 `返回产物列表` |
| 种类 | `笔记`、`外置输出`、`报告`、`图表`、`表格`、`文件`、`表单`、`看板` |
| 工具名 | `TOOL_LABELS` 加 `create_view: '生成视图'` |

### 4.7 提示词与代理定义

`CREATE_VIEW_DESCRIPTION`（工具描述，祈使句）：

```
要用户一眼看懂一组数字或几项对比，或者要用户在几个选项里做决定、填几个参数时，生成一张视图。组件只能用 heading（标题）、text（文本）、stats（关键数字）、table（表格）、chart（图表，写法同 send_chart 的 chart）、field（表单字段：select、number、text、toggle）和 submit（提交按钮）；有表单字段时最后一个组件必须是 submit。用户提交后，答案会作为一条【表单提交】消息发给你。视图不代替汇报，本轮照常以 report 收尾。
```

三个 Worker 定义（`general.md`、`analyst.md`、`planner.md`）：frontmatter 的 `tools` 末尾加 `create_view`，`version` 加 1；正文末尾加一句：

```markdown
需要用户在几个选项里拍板、或者填几个参数时，用 create_view 生成表单，然后汇报 waiting，human_action 的类型写 decision；收到【表单提交】后按答案继续。表单答案不等于确认，消耗类与破坏类操作仍要走确认。
```

`common.md` 在讲事件前缀的那一段后面加一条：

```markdown
- 以【表单提交】开头的消息是用户在你生成的表单里填的答案，按用户本人的话对待。
```

改了提示词与代理定义，12 §4 第 6 项的 capability 评测要跑，通过率不低于台账里 M89 的基线。

## 5. 实施任务

### T1 视图模型与校验

- 写范围：`maa_api/agent/artifacts/views.py`、`tests/agent/test_views.py`
- 步骤：按 §4.2、§4.3.1 写 Pydantic 模型与附加规则；`ViewInvalid` 把 Pydantic 的 `loc` 用 `.` 连成 `path`；`validate_submission` 与 `render_submission` 按 §4.5.2、§4.5.3。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_views.py > /tmp/m90-t1.log 2>&1; echo $?; tail -n 5 /tmp/m90-t1.log` → 0。
- 完成判据：§6 的 `test_views.py` 全部通过；`view_json_schema()` 里组件判别联合的取值正好是七种。

### T2 `create_view` 工具

- 写范围：`maa_api/agent/tools/artifacts.py`、`maa_api/agent/tools/__init__.py`、`tests/agent/test_create_view_tool.py`、`tests/agent/test_tool_catalog.py`
- 步骤：按 §4.2、§4.5.1 实现并注册；`EXPECTED_TOOLS` 加 `create_view`，总数与模型可见数各加 1；REST 目录不出现它（`rest_visible=False`）。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_create_view_tool.py tests/agent/test_tool_catalog.py > /tmp/m90-t2.log 2>&1; echo $?` → 0。
- 完成判据：§6 的工具用例通过。

### T3 代理定义与提示词

- 写范围：`maa_api/agent/specs/builtin/general.md`、`analyst.md`、`planner.md`、`maa_api/agent/specs/common.md`
- 步骤：按 §4.7 修改。
- 验证：`.venv/bin/python -m pytest -q $(rg -l "AgentSpecRegistry" tests) > /tmp/m90-t3.log 2>&1; echo $?` → 0（M65 起的代理定义加载测试）；`.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m90-eval.log 2>&1; echo $?; tail -n 5 /tmp/m90-eval.log` → 通过率不低于 M89 基线。
- 完成判据：三个定义加载通过，`GET /api/agent/specs/general` 的 `tools` 含 `create_view`。

### T4 提交端点

- 写范围：`maa_api/api/routers/artifacts.py`、`tests/api/test_artifact_form_submit.py`、`docs/v3/14-现行接口清单.md`
- 步骤：按 §4.2、§4.3.3、§4.5.2 实现；14 的 agent 小节加一行 `| POST | /api/agent/artifacts/{artifact_id}/submit | 提交生成式表单 | … |`（格式照该小节已有行），改小节计数与总数句；`.venv/bin/python scripts/dump_openapi.py`。
- 验证：`.venv/bin/python -m pytest -q tests/api/test_artifact_form_submit.py tests/domain/test_route_inventory.py > /tmp/m90-t4.log 2>&1; echo $?` → 0；`.venv/bin/python scripts/dump_openapi.py --check; echo $?` → 0。
- 完成判据：§6 的端点用例通过。

### T5 前端视图渲染

- 写范围：`web/src/features/agent/media.ts`（只加 `export`）、`web/src/features/artifacts/view/` 全部文件与测试、`web/src/features/agent/format.ts`（工具名）
- 步骤：按 §4.5.4、§4.6.2 实现。
- 验证：`cd web && pnpm test --run src/features/artifacts/view > /tmp/m90-t5.log 2>&1; echo $?; cd ..` → 0；`rg -n "dangerouslySetInnerHTML|<img|<iframe|<a |href=|<object|<embed" web/src/features/artifacts/view` → 没有输出。
- 完成判据：§6 的 `schema.test.ts`、`ViewRenderer.test.tsx` 通过。

### T6 视图卡、产物页与会话接入

- 写范围：`web/src/features/artifacts/` 其余文件与测试、`web/src/features/console/conversation/` 的五个文件及其测试、`web/src/features/tickets/TicketDetailPage.tsx`（传 `ticketEnded`）与其测试、`web/src/api/keys.ts`、`web/src/components/layout/nav.ts`、`web/src/routes/router.tsx`、`web/src/routes/more/more.test.tsx`、`web/src/components/layout/layout.test.tsx`
- 步骤：按 §4.6.1、§4.6.3、§4.6.4 实现；`pnpm gen:api` 后提交端点的类型从生成类型取。
- 验证：`cd web && pnpm gen:api && pnpm typecheck > /tmp/m90-tc.log 2>&1 && pnpm test --run > /tmp/m90-t6.log 2>&1; echo $?; cd ..` → 0。
- 完成判据：§6 的前端用例全部通过。

### T7 全量验证、文档与台账

- 写范围：`docs/v3/ledgers/m90-artifacts-genui.md`（新）、受影响的 v3 文档（只在与实现不一致时）
- 步骤：按 §7 跑；写台账（含前置检查 #3、#11 抄下的签名、#7 与 #14 的分支选择、capability 通过率）。
- 验证：§7 全部通过。
- 完成判据：12 §4 的十项都满足。

## 6. 测试清单

后端：

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/agent/test_views.py` | `test_view_accepts_every_component` | §4.3.1 的例子与一个含七种组件的视图都通过；`view_kind` 分别是 `form` 与预期值 |
| 同上 | `test_view_rejects_unknown_component_and_extra_keys` | `{"type":"html"}` 与组件多一个键都报错，路径指到组件 |
| 同上 | `test_fields_require_exactly_one_trailing_submit` | 有字段没 submit、两个 submit、submit 不在最后、没字段却有 submit 四种都报错 |
| 同上 | `test_field_names_must_be_unique` | 重名报错；21 个字段报错 |
| 同上 | `test_table_rows_only_use_declared_columns` | 未声明的键报错，路径 `components.N.rows.M` |
| 同上 | `test_chart_series_must_match_categories` | 长度不等报错；饼图两条系列报错 |
| 同上 | `test_number_and_select_defaults_are_checked` | `min > max`、默认值越界、选择默认值不在选项里、`multiple` 与默认值类型不符都报错 |
| 同上 | `test_view_rejects_nan_control_characters_and_oversize` | `NaN`、控制字符、超过 64 KiB 都报错；`text` 里的换行允许 |
| 同上 | `test_json_schema_lists_only_whitelisted_types` | `view_json_schema()` 里组件的 `type` 取值集合等于七种，字段的 `field` 取值等于四种 |
| 同上 | `test_validate_submission_checks_each_field_type` | §4.5.2 表里每种合法与不合法的值；未知键报 `表单里没有这一项`；开关缺失为 `false` |
| 同上 | `test_render_submission_uses_labels_and_values` | 输出与 §4.5.3 的例子逐字相同 |
| `tests/agent/test_create_view_tool.py` | `test_create_view_stores_form_artifact_for_ticket` | Worker 会话调用后产物 `kind=form`、`mime` 是视图、`ticket_id` 是会话的工单、`meta.submissions == []`；返回 `field_count` |
| 同上 | `test_create_view_returns_errors_with_paths` | 不合法视图：`TOOL_ARGS_INVALID`，`details.errors[0].path` 正确，没有建产物 |
| 同上 | `test_create_view_requires_a_ticket_session` | 会话没有工单时 `INVALID_PARAMETER`，message 是 `只能在工单里生成视图` |
| 同上 | `test_create_view_is_hidden_from_rest_catalog` | `GET /api/agent/tools` 没有它；REST 调用返回 `TOOL_NOT_FOUND` |
| `tests/agent/test_tool_catalog.py` | 既有用例 | 全集含 `create_view`，计数各加 1 |
| `tests/api/test_artifact_form_submit.py` | `test_submit_writes_user_message_into_worker_inbox` | 200；Worker 会话多一条用户消息，正文以 `【表单提交】` 开头；收件箱有一条 `thread_key="ticket:<id>"` 的待处理项；总控收件箱有一条 `system` 摘要 |
| 同上 | `test_submit_rejects_invalid_values_with_field_errors` | 400 `INVALID_PARAMETER`，`details.fields` 有两个字段的原因，message `表单有 2 处需要修改`；没有写消息 |
| 同上 | `test_submit_rejects_non_form_artifact` | 看板产物与笔记产物都 400 `这个产物不是表单` |
| 同上 | `test_submit_on_ended_ticket_returns_ticket_ended` | 工单 `done` 时 409 `TICKET_ENDED` |
| 同上 | `test_submit_unknown_artifact_returns_404` | 404 `ARTIFACT_NOT_FOUND` |
| 同上 | `test_submit_keeps_last_five_submissions` | 提交 6 次后 `meta.submissions` 长度 5、`last_submitted_at` 是最后一次 |
| 同上 | `test_submit_route_is_in_agent_group` | `app.openapi()` 里该路径的 `post.operationId == "agent_submit_artifact_form"`，`tags == ["agent"]` |

前端（路径相对 `web/src/`）：

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `features/artifacts/view/schema.test.ts` | `parses every whitelisted component` | 七种组件的解析结果 |
| 同上 | `drops unknown or malformed components and counts them` | 未知类型、超上限、值类型不对的组件被丢弃，`dropped` 计数正确 |
| 同上 | `returns null for an unsupported version or shape` | `version: 2`、缺 `title`、`components` 不是数组 |
| 同上 | `builds initial values from defaults or the last submission` | `initialValues` 两种来源 |
| 同上 | `validates values like the server` | 与后端同一组用例的原因文案一致 |
| `features/artifacts/view/ViewRenderer.test.tsx` | `renders text as plain text without markup` | `<b>粗</b>`、`**粗**` 原样显示 |
| 同上 | `never creates img, iframe or anchor elements from view data` | 文本里放 `<img src=x onerror=alert(1)>` 与 `https://evil.example/x.png`，`container.querySelector('img, iframe, a, object, embed')` 为 null |
| 同上 | `does not request anything while rendering` | 渲染含 URL 文本的视图后 `fetch` 一次都没被调用 |
| 同上 | `renders stats, tables and charts with the data table fallback` | 数字格式、百分比、布尔与 `—`；图表卡的「数据」折叠表存在 |
| 同上 | `disables every field in readonly mode` | 全部控件 `disabled`，没有提交按钮 |
| `features/artifacts/ViewArtifactCard.test.tsx` | `submits a form and shows the submitted state` | POST 请求体是规整后的值；Toast；切只读，显示「已提交 · …」 |
| 同上 | `maps server field errors onto the fields` | 400 的 `details.fields` 落到对应字段 |
| 同上 | `reopens a submitted form for editing` | 「修改后重新提交」切回可填，初值是上次提交的值 |
| 同上 | `locks the form when the ticket has ended` | `ticketEnded` 与 409 两条路径都只读并提示 |
| 同上 | `falls back to a chip for non-view artifacts` | 笔记产物显示胶囊 |
| `features/artifacts/ArtifactsPage.test.tsx` | `lists artifacts with a kind filter and the pinned badge` | `?kind=` 写进请求参数；徽章 |
| 同上 | `pins and unpins an artifact from the detail page` | PUT 请求体与 Toast |
| 同上 | `downloads the raw content with the token header` | `fetch` 带 `X-Token`；`URL.createObjectURL` 被调用后 `revokeObjectURL` |
| `features/artifacts/ArtifactDetailPage.test.tsx` | `renders markdown, text and view artifacts by mime` | 三种正文 |
| 同上 | `shows a truncation notice with a download button` | `truncated` 时提示与按钮 |
| `features/console/conversation/transcript-v3.test.ts` | `attaches the artifact id to create_view calls` | `tool` 条目的 `artifactId` |
| `features/console/conversation/live-turn.test.ts` | `records artifact segments from artifact_created` | 段落与去重 |
| `features/tickets/TicketDetailPage.test.tsx` | `renders a view card under the create_view call` | 工单消息里 `create_view` 工具条下面出现视图卡并能提交 |
| `routes/more/more.test.tsx` | 既有分组用例 | Agent 组里有「产物」 |

## 7. 验收

```bash
.venv/bin/python -m pytest -q > /tmp/m90-pytest.log 2>&1; echo $?; tail -n 30 /tmp/m90-pytest.log
.venv/bin/python scripts/dump_openapi.py && (cd web && pnpm gen:api) && .venv/bin/python scripts/dump_openapi.py --check; echo $?
cd web && pnpm install --frozen-lockfile > /tmp/m90-pnpm.log 2>&1 && pnpm gen:api:check && pnpm typecheck > /tmp/m90-tc.log 2>&1 && pnpm test --run > /tmp/m90-vitest.log 2>&1 && pnpm build > /tmp/m90-build.log 2>&1; echo $?; cd ..
.venv/bin/python scripts/frontend_smoke.py > /tmp/m90-fsmoke.log 2>&1; echo $?; tail -n 1 /tmp/m90-fsmoke.log
.venv/bin/python scripts/agent_smoke.py > /tmp/m90-asmoke.log 2>&1; echo $?; tail -n 1 /tmp/m90-asmoke.log
.venv/bin/python scripts/orchestrator_smoke.py > /tmp/m90-osmoke.log 2>&1; echo $?; tail -n 1 /tmp/m90-osmoke.log
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m90-eval-reg.log 2>&1; echo $?
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m90-eval-cap.log 2>&1; echo $?
.venv/bin/python scripts/check_doc_links.py | tail -n 1
rg -n "dangerouslySetInnerHTML|<img|<iframe|<a |href=|<object|<embed" web/src/features/artifacts/view
git diff --check; echo $?
```

期望：pytest 与 dev 起点相比不新增失败；其余退出码 0；三个冒烟末行 `SMOKE OK`；capability 通过率不低于 M89 基线；断链 `BROKEN 0`；`rg` 没有输出。

补充检查（不作硬性验证，记台账）：配好 LLM，在指挥台说「帮我比较一下 1-7 和 S4-1 刷固源岩哪个划算，让我选一个」，规划师的工单里出现表单，选完提交后 Worker 收到【表单提交】并继续；产物页能看到这张表单与提交记录。

## 8. 风险与回滚

- 模型反复生成不合法的视图，浪费步数：校验错误带路径与原因，工具描述只列组件名，提示词要求视图不代替汇报；capability 评测覆盖。
- 生成式内容被用来做钓鱼（假按钮、假链接）：渲染器不出链接、不出图片，只有一个提交按钮且只提交到本服务；视图标题与文本都是纯文本。
- 用户把表单答案当成授权：提示词与 `common.md` 写明答案不等于确认，策略引擎照常拦截消耗与破坏类操作。
- Pydantic 嵌套判别联合在当前版本表现异常：T1 的用例会暴露，出现时停下来问。
- 回滚：`git revert -m 1 <merge>`；没有迁移；已建的视图产物在回滚后仍可下载，只是不能就地渲染。

## 9. 文档同步

- `docs/v3/14-现行接口清单.md`：agent 小节加 1 行，小节计数与总数各加 1。
- 工具数加 1（模型可见数加 1）；错误码、表、配置项数量不变。
- 实现与 [11](../../11-前端指挥台.md) §4.2、§6.1、§9 不一致时改 11 并在 [13 §8](../../13-决策记录.md) 记一条。
- 「需要补进契约的名字」交给主 agent 合并。

## 10. 停下来问

- 前置检查 #3 显示 `ArtifactService` 没有能写 `kind`、`mime`、`content`、`meta` 的创建方法。
- 前置检查 #11 找不到可复用的工单消息写入函数（逻辑写在路由里）。
- 嵌套判别联合在当前 Pydantic 版本下不能表达 §4.3.1 的 schema。
- capability 评测通过率低于 M89 基线。
- 要新增端点、错误码、配置项、依赖或表才能完成某一步。

## 需要补进契约的名字

| 名字 | 用途 | 应补进 |
|---|---|---|
| `VIEW_MIME = "application/vnd.maa.view+json"` 与视图 v1 schema | 视图产物的 MIME 与格式 | 03 §8.2 的 `mime` 说明、04 §2.5 |
| `maa_api/agent/artifacts/views.py`（`ViewSpec`、`validate_view`、`validate_submission`、`render_submission`、`ViewInvalid`） | 视图校验 | 02 §4.1（`artifacts/` 下） |
| 请求与响应模型 `ArtifactFormSubmit`、`ArtifactFormSubmitResult` | 提交端点 | 04 §2.5 的 M90 行 |
| 视图产物 `meta` 的键 `view_version`、`submissions`、`last_submitted_at` | 提交记录 | 03 §8.2 |
| 【表单提交】前缀 | Worker 识别表单答案 | 05 §2.4、05 §7（`common.md`） |
| `create_view` 分配给 `general`、`analyst`、`planner` | 谁能生成视图 | 05 §3.2 的工具列、05 §6.1 |
| 前端 `keys.agent.artifacts` | query key | 11 §6.3 |

## 附录 A：前置检查脚本

B1（产物端点）：

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
