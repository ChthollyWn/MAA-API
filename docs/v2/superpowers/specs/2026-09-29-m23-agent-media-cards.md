# M23 · Agent 会话内的图片与图表

> 关联：[11-Agent模块设计](../../11-Agent模块设计.md) §3.2、§4、§5、§8，[09-前端重构方案](../../09-前端重构方案.md) §3.5、§6，[04-数据模型与持久化](../../04-数据模型与持久化.md) §5.4，[13-决策记录](../../13-决策记录.md) §8.29。实施分支 `m23-agent-media-cards`，从 `refactor/v2` 新建。

## 1. 目标与用户裁决

用户裁决（2026-09-29）：「会话中，agent 应该有发送图片和 canvas 图表的能力。」逐项确认：

1. **对象是 MAA-API 内置 Agent 的对话页**，走既有的 spec → plan → 实施 → 台账流程。
2. **图片四类全要**：agent 截的图在对话里直接可见（缩略图 + 点开看大图）、MAA 资源图引用、任务历史/日志里的既有截图、服务端渲染生成图。
3. **图表要结构化卡片**：agent 输出图表规格，前端用图表库渲染，可交互并跟随主题。
4. **图表用 ECharts（canvas）**：交互（tooltip / 图例 / 缩放）最强，接受包体代价，按需引入 + 独立 chunk。
5. **服务端渲染只做截图加工**：裁剪、放大、画标注框；不做图表 PNG。
6. **不做干员头像**（本地无图源），但**关卡地图改由本地 tile 数据渲染示意图**。
7. **补齐「任务历史里的截图」的写入点**：在 M23 内实现流水线任务留档截图（`trigger=task_snapshot`），否则这一类图没有数据源。
8. **任务失败的日志附件截图一并做**：任务失败时自动截图并挂到日志上。

### 1.1 现状（实施前核实的事实）

- `agent_message`（[models.py:501](../../../maa_api/db/models.py#L501)）只有 15 列，**没有任何附件/媒体列**；结构化信息只能靠 `tool_calls` 列与 `role="tool"` 的 JSON 文本表达。
- 一张图都不显示：`get_screenshot` 的归档结果里**已经有** `screenshot_id` / `sha256` / `thumb_url` / `full_url`（[runtime.py:1898-1901](../../../maa_api/agent/runtime.py#L1898)），前端 `ToolCallBar` 只把它当 JSON 打印，且整条工具条在默认折叠的 `<details>` 里（[ToolCallBar.tsx:28](../../../web/src/features/agent/ToolCallBar.tsx#L28)）。
- 图片通路已存在：`store_screenshot()` 内容寻址落盘（[util/image.py:24](../../../maa_api/util/image.py#L24)），`GET /api/images/{sha256}/thumb|full`（[screenshots.py:176](../../../maa_api/api/routers/screenshots.py#L176)）与 `GET /api/screenshots/{id}`（同文件 :115）都要鉴权；材料图标端点 `GET /api/resources/items/icon?item_id=`（[resources.py:244](../../../maa_api/api/routers/resources.py#L244)）也已存在。
- **截图只有两个写入点**：agent 工具（`trigger=agent`，[runtime.py:1882](../../../maa_api/agent/runtime.py#L1882)）与前端手动存档（`trigger=manual`，[device.py:304](../../../maa_api/api/routers/device.py#L304)）。`crash` 只被读（[incidents.py:561](../../../maa_api/agent/incidents.py#L561)），`task_snapshot` **只有枚举没有写入点**，`LogRecord.attachment` 也**只有读侧**（[log_hub.py:425](../../../maa_api/services/log_hub.py#L425)）。本机库实测：截图 61 行全是 `agent`，`pipeline_id` 全为空，所以 `GET /api/pipelines/{id}/screenshots` 永远是空的。
- `store_screenshot()` 不传 `quality` 时用 `adb.screenshot_quality`（默认 **25**，[settings.py:187](../../../maa_api/settings.py#L187)）重新编码；`get_screenshot` 先在内存里按 85 编码，落盘时仍被 25 覆盖。
- 前端 `markdown.tsx` 明确不支持图片（刻意的），`transcript.ts` 只认 user / assistant / tool 三种条目；M21 的提问卡不是消息，是「独立表 + 独立事件 + 独立 REST」。
- `web/package.json` 里没有任何图表库；`index.css` 没有图表色板 token。

## 2. 范围

### 2.1 本里程碑

1. **媒体卡契约**：tool 结果里新增 `result.media[]` 信封（image / item_icons / stage_map / chart 四种卡），一处定义、后端产出、前端解析。
2. **五个新工具**（组 `media`，全部 `ToolRisk.SAFE`、`model_visible=True`）：`send_screenshot`、`annotate_screenshot`、`send_item_icons`、`send_stage_map`、`send_chart`。
3. **服务端图片加工**：`annotate_screenshot` 用 Pillow 裁剪 / 放大 / 画框，产物走既有内容寻址存储并落一条 `Screenshot(trigger=AGENT)`。
4. **关卡示意图数据链**：从本地 `Arknights-Tile-Pos`（`overview.json` + 每关 JSON，按内核真实加载链查找）解析并归一化成网格码，交给前端画。
5. **`get_screenshot` 结果补上 media 信封**，让 agent 每次截图都自动在对话里可见。
6. **模型上下文压缩**：媒体工具结果在回灌模型时替换成一行占位，避免图表规格与 tile 网格每轮重复占 token。
7. **前端渲染**：`media.ts` 解析 + 四张卡组件 + ECharts 图表面（懒加载、按需注册、跟随主题、数据表兜底）+ 复用既有 `ImageViewer` 看大图。
8. **提示词**：把「给用户看东西」的工具用法与图表数据来源要求写进系统提示词。
9. **流水线任务留档截图**（§3.9）与**任务失败日志附件截图**（§3.10）。
10. 生成物与文档同步。

### 2.2 不做

- **不新增数据表、不新增 REST 端点、不新增错误码**：卡片的持久化载体就是既有的 tool 消息；截图复用既有的截图表与存储；日志附件复用既有的 `meta.attachment`。
- **不做干员头像 / 立绘**：本地没有图源（MaaCore 的 `AvatarCacheManager` 只从画面识别头像，不附带图库；三个 `cache/avatars/` 目录都是空的）。
- **不引入远端图源**（PRTS / akhr / penguin-stats 之类）与配套代理缓存：离线可用是这条链路的硬约束。
- **不做服务端图表 PNG**（用户裁决 §1.5）；**不做 Web Push 带图**。
- **不改 markdown 渲染器**：`![]()` 仍然不渲染，模型在提示词里被要求用工具发图。
- **不改 `get_screenshot` 的视觉回灌语义**：仍然只把最新一张截图喂给多模态模型，历史截图仍是文本占位。
- **不给消息表加附件列**、不做「消息级媒体」：卡片挂在工具调用上，位置天然正确、刷新可重建。
- **不为留档截图加设置开关**：M23 固定开启，是否需要开关记 [13 §8.29](../../13-决策记录.md) 待决；清理仍由既有保留策略负责。
- **不改 `crash` / `update` 两类触发者的语义**（它们仍只有读侧或既有写入点）。

## 3. 设计

### 3.1 媒体卡契约

产生卡片的工具结果统一长这样（`result` 里多一个 `media` 数组）：

```json
{"result": {"media": [ /* 卡片 */ ], "caption": "整批的说明（可选）"}}
```

四种卡片：

```json
{"type": "image", "screenshot_id": "…", "sha256": "…",
 "url": "/api/images/<sha256>/full", "thumb_url": "/api/images/<sha256>/thumb",
 "alt": "…", "caption": "…", "width": 1920, "height": 1080}

{"type": "item_icons", "caption": "…",
 "items": [{"item_id": "2001", "name": "基础作战记录",
            "icon_url": "/api/resources/items/icon?item_id=2001"}]}

{"type": "stage_map", "caption": "…",
 "stage": {"code": "1-7", "name": "暴君", "stage_id": "main_01-07",
           "width": 11, "height": 7, "candidates": 2,
           "tiles": [["#00", "#00", ".10"]],
           "start": [[10, 1], [10, 3]], "end": [[0, 3]],
           "legend": [{"code": "!00", "label": "特殊地形"}]}}

{"type": "chart", "caption": "…",
 "chart": {"chart_type": "bar", "title": "1-7 掉落", "categories": ["固源岩"],
           "series": [{"name": "数量", "values": [12]}], "stacked": false,
           "x_label": "物品", "y_label": "个数", "unit": "个",
           "source": "get_drop_stats(stage=1-7)"}}
```

**前端解析规则（安全边界）**：

- 只认三种同源图片地址：`/api/images/<sha256>/(thumb|full)`、`/api/screenshots/<id>`、`/api/resources/items/icon?item_id=<id>`；其余（外域、`data:`、相对路径）整卡丢弃。这是 `features/logs/format.ts:77-92` 白名单做法的延续。
- 未识别的 `type`、缺字段、类型不对、超上限的卡一律忽略，**不抛异常、不空白页**。
- 上限（前端再裁一次，后端已拒）：一次 ≤ 8 张图、≤ 6 个系列、≤ 200 个类目、文本长度同 §3.5。
- **旧会话兼容**：`tool_name === "get_screenshot"` 且结果里带 `screenshot_id`/`sha256`/`thumb_url`/`full_url`（M23 之前落库的老消息没有 `media` 键）时，前端按 `image` 卡渲染。

### 3.2 五个工具（`maa_api/agent/tools/media.py`，组 `media`）

| 工具 | read_only | 参数 | 说明 |
|---|---|---|---|
| `send_screenshot` | ✔ | `screenshot_id?`、`pipeline_id?`、`trigger?`、`caption?` | 把一张已有截图发给用户：给 `screenshot_id` 就用它；只给 `pipeline_id` 就取该流水线最新一张（可用 `trigger` 过滤）；都不给＝本会话最近一张 agent 截图 |
| `annotate_screenshot` | ✘ | `screenshot_id?`、`crop?`、`zoom?`、`boxes?`、`caption?` | 服务端裁剪/放大/画框，生成**新图**并展示 |
| `send_item_icons` | ✔ | `items[]`（1–8）、`caption?` | 材料图标；`item_id` 或中文名，按 `item_index.json` 解析 |
| `send_stage_map` | ✔ | `stage`、`caption?` | 关卡布局示意图（本地 tile 数据） |
| `send_chart` | ✔ | 见 §3.5 | 结构化图表卡 |

- 全部 `ToolRisk.SAFE` → 策略引擎直接放行（[policy.py:112](../../../maa_api/agent/policy.py#L112)），**不弹确认**，与 `get_screenshot` 同级；调用照常进审计。
- `annotate_screenshot` 会写文件与数据库行，故 `read_only=False`（决定批内是否并发，[runtime.py:1261](../../../maa_api/agent/runtime.py#L1261)），其余四个为 `read_only=True`。
- **`annotate_screenshot` 进 `SCREEN_TOOL_NAMES`**：视觉降级时从模型 schema 隐藏（[runtime.py:1738](../../../maa_api/agent/runtime.py#L1738)）并在派发处拦截（:1302）——模型看不见画面就不可能圈对位置。其余四个不进：它们服务的是用户的眼睛，视觉降级时仍应可用。拦截文案要按工具名生成（现在写死「截图和原子屏幕操作已禁用」，不该把圈选说成原子操作）。
- `trigger` 只接受 `agent` / `manual` / `task_snapshot` / `crash` / `update` 五个枚举值。
- REST 调用与内置会话走同一条注册表路径，五个工具的返回值都不依赖 runtime 的 `on_audit_prepare` 钩子，所以 REST 与内置会话拿到同样的 JSON（**例外**：既有 `get_screenshot` 的归档只发生在 runtime 钩子里，REST 仍返回 base64——保持既有行为，不改）。

**「最近一张截图」怎么定位**：`screenshot` 表没有 session 列，所以不能按 `trigger=AGENT` 取全局最近一张。做法是从**本会话的消息**里倒着找第一条带 `screenshot_id` 的工具消息（`get_screenshot` / `annotate_screenshot` / `send_screenshot`），再按 id 读 `screenshot` 行。因此：

- 缺省用法只在有内置会话时可用；REST 调用（`context.session_id` 为空）**必须**显式给 `screenshot_id` 或 `pipeline_id`，否则 `INVALID_PARAMETER`——`send_screenshot` 与 `annotate_screenshot` 都是这条规则。
- 本会话还没有任何截图时 → `SCREENSHOT_NOT_FOUND`，message 提示模型先调 `get_screenshot`。
- 显式给的 id 或 `pipeline_id` 查不到、或该筛选条件下没有截图 → `SCREENSHOT_NOT_FOUND`；行存在但已被清理（`deleted_at` 非空或文件不在）→ `SCREENSHOT_EXPIRED`。
- §3.9 落地后 `pipeline_id` 过滤才有真实数据（留档截图带 `pipeline_id`/`task_id`）。

### 3.3 `annotate_screenshot` 的几何与降级

- 坐标系：原图**归一化坐标**（0–1，原点左上）。`crop` 缺省＝整图；`boxes` 画在原图坐标上。
- 固定顺序：**先画框 → 再裁剪 → 再放大**（用户看到的放大图里框仍在原位）。
- 参数边界：`boxes` ≤ 16、`label` ≤ 32 字符；`zoom ∈ [1, 8]`；`crop` 宽高 ≥ 0.01；放大后长边超 2048 时自动下调有效 zoom，并在结果 `caption` 里注明实际倍数。
- 画框：红色 `#e5484d`，线宽 `max(2, round(min(w, h) / 400))`（按裁剪前原图尺寸算）。
- 文字：用 `ImageFont.truetype` 按候选列表探测 CJK 字体（macOS `PingFang.ttc` / `Hiragino Sans GB.ttc`，Linux Noto Sans CJK，Windows `msyh.ttc`）；**一个都找不到时不画文字**，label 只进卡片图例与返回结果，不报错。
- 输出：Pillow 图 → `store_screenshot(..., quality=90)`（**显式给质量**：默认会取 `adb.screenshot_quality`＝25，放大图会被二次压缩毁掉；这是有意的偏离，代价是这类图比普通截图大）→ 新 `Screenshot(trigger=AGENT)` 行 → image 卡。
- 失败：源图不存在/已清理 → `SCREENSHOT_NOT_FOUND` / `SCREENSHOT_EXPIRED`；参数越界 → `INVALID_PARAMETER`（都是既有码）。

### 3.4 `send_stage_map` 的数据链

1. **索引**：`<资源根>/Arknights-Tile-Pos/overview.json` 是 **dict**（4186 键，键＝`f"{stageId}-{levelId.replace('/','-')}"`），值含 `code` / `stageId` / `levelId` / `filename` / `width` / `height` / `name`；实测 `filename` 100% 等于 `<键>.json`。
2. **资源根按内核的真实加载链查找**：基础层 `<maa_path>/resource`，其上叠加 `resource/maa-layers/cache/resource` 与 `resource/maa-layers/custom/resource`（后者覆盖前者）。注意 `maa-layers/repo/resource` **不是运行时增量层**——它在资源更新时就被合并进 `<maa_path>/resource`（[main.py:545-559](../../../maa_api/main.py#L545)），且本机上 `repo/`、`custom/` 未必存在，查找要容忍目录缺失。`overview.json` 与关卡文件都在同一个胜出层里取。
3. **关卡定位与平局规则**：`code` 并不唯一（4186 关里 2133 个不同 code，其中 944 个对应多关：`1-7` 对应 2 条、`ISW-NO` 对应 554 条）。规则是：query 精确等于某条 `stageId` 或 `levelId`（大小写不敏感）→ 直接命中；否则按 `code` 命中；多个命中时按 `stageId` 字典序取第一条，并把 `stage_id` 与候选数 `candidates` 一起返回（模型可以用 `stageId` 精确重指）。都没命中 → 交给既有 `StageResolver` 试别名 → 仍无 → `RESOURCE_ASSET_NOT_FOUND`。
4. **文件读取**：先用 overview 的 `filename`；磁盘上没有时按目录列表建一次索引，用前缀 `f"{stageId}-"` 匹配（**不要**用含 `/` 的 `levelId` 拼 glob，那样永远匹配不到）。
5. **归一化（单格 3 个字符）**：`<地形><可部署><高度>`。
   - 地形：由 `tileKey` 映射到 5 类 —— `#` 阻挡（forbidden / wall / *wall* / fence* 等）、`~` 坑洞（hole / empty / rabbithole*）、`w` 水域（*sea / water / puddle 等）、`!` 特殊机制（毒雾 / 火山 / 传送 / 草丛 / 冰面 / 治疗格 / 目标点等）、`.` 普通地面（road / floor / 其余未特别标记者）。映射表是代码里的单一真相源，**必须覆盖本地 107 个 `tileKey`**（清单作为测试夹具提交，出现表外的新键时测试变红，提醒补表）。
   - 可部署：直接取 `buildableType`（0 / 1 / 2 / 3，本地实测四个值都有）。
   - 高度：直接取 `heightType`（0 地面 / 1 高台）。
   - 起点与终点：**不来自 `tileKey`**，来自格子的 `isStart` / `isEnd` 布尔（本地实测 `tile_start` 只占 `isStart` 的 41%、`tile_end` 只占 `isEnd` 的 65%），因此单独出 `start` / `end` 坐标数组。
6. 结果里带 `code` / `name` / `stage_id` / `width` / `height` / `candidates` / `tiles` / `start` / `end` / `legend`；**这是快照**——资源更新后卡片不会变，重发一次即可。最大关卡 40×40＝1600 格，约 5 KB。

### 3.5 `send_chart` 参数与校验

```
chart_type: "line" | "bar" | "pie"
title: 1–80 字
categories: 1–200 项，每项 ≤ 40 字
series: 1–6 项，每项 {name: 1–40 字, values: 1–200 项}
stacked: bool = false（仅 bar/line 有意义）
x_label / y_label: ≤ 40 字；unit: ≤ 16 字
caption: ≤ 300 字；source: ≤ 200 字（数据来源，例如 get_drop_stats(stage=1-7)）
```

- `line` / `bar`：`len(values) == len(categories)`；`pie` 只用第一条 series。
- 数值必须是有限数（`NaN` / `Infinity` 拒绝），允许 `null` 表示断点。
- **上限是共同约束，优先按逐项检查、再按总量检查**：总点数 ≤ 600、序列化后 ≤ 24 KB。两级都要满足——按最大允许输入（6×200 点 5 位整数 ≈ 8 KB）估算，合法输入不会撞上限，但规格把两条都写成硬约束，超限一律 `INVALID_PARAMETER`，message 指明是哪一项、实际多大（让模型自己改一版重发）。
- 为什么自己守大小：docs/04 §5.8 要求 tool content 落库前截断到 32 KB，但**代码里没有强制**（[repositories/agent.py:226](../../../maa_api/db/repositories/agent.py#L226) 明确写成「调用方纪律」），所以 M23 的三个产出方（图表 ≤ 24 KB、tile 网格 ≤ 8 KB、图片卡只是 URL）必须自己保证单条结果远低于 32 KB。
- 服务端只**校验与转发**，不做聚合、不补数据：数据真实性靠提示词约束（§3.7），来源写进 `source` 展示在卡片脚注。

### 3.6 模型上下文压缩

`build_model_context()` 对 `tool_name ∈ {send_screenshot, annotate_screenshot, send_item_icons, send_stage_map, send_chart}` 的 tool 消息，把 content 换成一行占位（保留卡片类型与标题，例如 `{"result":{"media_sent":[{"type":"chart","title":"1-7 掉落"}],"note":"媒体卡已发给用户，历史内容不再重复"}}`）。落库内容不变，只有回灌模型时压缩；形态与既有的历史截图纯文本占位（`[历史截图已省略…]`）不同，但互不影响。`get_screenshot` 保持既有语义（最新一张仍回灌图像、更早的仍是文本占位）。

### 3.7 提示词

在系统提示词的「能力边界与操作顺序」与「安全数据边界」之间插入一节（**位置受 [test_runtime.py:2580](../../../tests/agent/test_runtime.py#L2580) 的段落顺序断言约束，不能插到那两段之后**）：

- 要给用户看画面用 `send_screenshot`；只靠文字说不清时用 `annotate_screenshot` 把关键区域放大并画框，坐标用原图归一化值。
- 数据分布与趋势用 `send_chart`，**数据必须来自工具真实返回**（`get_drop_stats` / `list_pipelines` / `get_logs` / `list_schedules` 等），`source` 写明出处；没数据就说没有，不许编。
- 材料图标用 `send_item_icons`，关卡布局用 `send_stage_map`；**不要用 markdown 图片语法**（前端不渲染），也不要在正文里贴 URL。
- 一次最多 8 张图、一张图最多 6 个系列；同一张图不要重复发。

### 3.8 前端

- `features/agent/media.ts`：`parseMediaCards(result: unknown): MediaCard[]`（纯函数，白名单 + 上限 + 未知忽略 + `get_screenshot` 旧形态兼容）。
- `features/agent/MediaCards.tsx`：
  - `ImageCard`：懒加载缩略图（IntersectionObserver）、点击进既有 `ImageViewer` 看原图、失败显示「图片不可用」+ 重试、下方 caption。
  - `ItemIconsCard`：图标 + 名称网格，**与图片卡走同一条 token 取 blob 的路**（`/api/resources/items/icon` 同样有 router 级 `require_auth`，不靠 cookie；既有 `MapControls.tsx` 的 `<img src>` 做法不动）。
  - `StageMapCard`：**SVG** 网格（关卡示意图不占 canvas 名额，DOM 可测）+ 起点/终点标记 + 图例 + 关卡名与编号 + 候选数提示。
  - `ChartCard`：标题 + 图表面 + 脚注（`source`）+ 「数据」折叠表格（兜底与无障碍，永远可用）。
- `features/agent/chart/`：
  - `EChartSurface.tsx`：动态 `import('echarts/core')` 并按需注册 `LineChart`/`BarChart`/`PieChart` + `GridComponent`/`TooltipComponent`/`LegendComponent`/`TitleComponent` + `CanvasRenderer`；`ResizeObserver` 跟随容器；主题或 token 变化时重建 option；加载失败回落到数据表。
  - `buildChartOption.ts`：纯函数，`spec + 调色板 + 主题色` → ECharts option（可单测）。
- `components/ui/use-authed-image.ts`：把 `PipelineScreenshots` 里的「token 取 blob + objectURL + 懒加载 + 清理」抽成共用 hook，`PipelineScreenshots` 改用它（行为不变）。
- `ToolCallBar.tsx`：卡片渲染在** `<details>` 之外、它的上方**（工具条整体默认折叠，放里面就等于看不到）；JSON 折叠块保留在下方（调试与审计用）。`format.ts` 的 `TOOL_LABELS` 补五个中文标签。
- `index.css`：新增 `--chart-1…--chart-6` 两套（浅/深），图表配色只从这里取（系列数 ≤ 6 与色板一一对应，系列更多时循环取色，但后端上限就是 6）。
- ECharts 只经动态 import 进独立 chunk（不引 React 包装库，`vite.config.ts` 不加手动分包）。依赖锁到 `echarts@^6.1`（当前最新 6.1.0），按需注册后该 chunk 增加约 150–200 KB gzip（估算，实施时以 `build` 产物为准），只有会话里真的出现图表时才加载。

### 3.9 流水线任务留档截图

- **时机**：每条任务进入终态时留一张。`completed`、`failed`、`skipped`（重试耗尽后跳过）三种都留——用户要看的正是「跑成什么样 / 卡在哪」；**`cancelled` 不留**（用户主动取消或服务重启回收，语义上不需要现场）。
- **落库**：`Screenshot(trigger=task_snapshot, pipeline_id, task_id, backend, format="jpg", width, height, size_bytes, path)`，文件走 `store_screenshot()` 内容寻址（与手动存档共用质量 25 的默认值，避免磁盘膨胀）。
- **容错（硬要求）**：截图失败（设备忙 / adb 断开 / 内核崩）只记一条 WARNING 日志，**不改变任务终态、不重试、不影响流水线结果**；带超时，不能拖慢任务收尾。
- **并发**：在任务终态收敛后、下一个任务开始前执行；设备访问走既有 `DeviceManager.screenshot`（adb 优先、失败回落 MaaCore），不新建设备通道，也不与 MaaCore 的 screencap 并行抢设备。
- **可见性**：读取侧不用改——`GET /api/pipelines/{id}/screenshots`（[pipelines.py:403](../../../maa_api/api/routers/pipelines.py#L403)）与前端 `PipelineScreenshots` 组件早已就位，M23 之后它们不再永远是空的。
- **保留**：沿用既有保留策略清理，不新增配额。

### 3.10 任务失败的日志附件截图

- **时机**：任务进入 `failed` 或 `skipped` 终态时，把该任务的终态截图挂到它的失败日志上（`completed` 不挂）。
- **复用同一张图**：与 §3.9 的留档是同一次截图（内容寻址天然去重），`screenshot` 行只有一条（`trigger=task_snapshot`），日志附件引用它的 `sha256` / `screenshot_id`。
- **附件形状**：`{"kind": "screenshot", "sha256": …, "thumb_url": "/api/images/<sha>/thumb", "full_url": "/api/images/<sha>/full", "screenshot_id": …}`，与前端 `logs/format.ts` 的 `logImage()` 认的字段一致（它只认本服务的这三种地址，外域忽略）。
- **日志落点**：按既有日志写入路径落一条 `source=task` 的 ERROR/INFO 记录（具体级别与文案由实施任务定），`meta.attachment` 走 [log_hub.py:425](../../../maa_api/services/log_hub.py#L425) 已有的读侧拼装，不新增字段。
- **失败即降级**：截图失败就不带附件写普通失败日志，不报错、不重试。
- 前端日志页的附件显示与全屏查看已经实现，本里程碑不改前端日志页。

## 4. 影响与兼容

- **无数据库迁移**：无新表、无新列（`screenshot.trigger` 与 `log.meta.attachment` 都是既有结构）。
- **无新 REST 端点、无新错误码**（错误码总数仍是 105）：`/api/agent/tools` 的响应是运行时计算的目录，工具变多不改变 OpenAPI schema；`web/openapi.json` 里没有 agent 工具名——`dump_openapi.py --check` 预期不变，实施时实测确认。
- **消息体变大**：图表 ≤ 24 KB、tile 网格 ≤ 8 KB，靠 §3.5 的上限与 §3.6 的占位控制；历史里仍然完整可读。
- **旧会话**：M23 之前落库的 `get_screenshot` 结果靠 §3.1 的兼容规则也能显示图片；其他工具结果照旧只显示 JSON。
- **流水线执行器被改动**：M23 是第一次在任务终态插入截图动作，必须保证「截图失败不影响任务终态」这条有测试兜住。
- **磁盘**：留档截图按 90 天保留策略清理（质量 25，单张百 KB 量级）；`annotate_screenshot` 产物是质量 90 的大图，同样受保留策略约束。
- **新依赖**：前端 `echarts`（后端无新依赖）；`pnpm-lock.yaml` 变更。
- **既有测试要改**：`tests/agent/test_tool_catalog.py` 的 `EXPECTED_TOOLS` 与计数（43 → 48）加上五个新工具并断言新组 `media`；`tests/agent/test_runtime.py` 的 SYSTEM_PROMPT 段落顺序断言受 §3.7 插入位置约束；前端 `AgentPage.test.tsx` 会因媒体卡断言而扩展。
- **`docs/09` 的图表章节要一并纠正**：§3.5 的 recharts 倾向被推翻（用户裁决 §1.4），§4/§9 里从未落地的 `vendor-charts` 手动分包段也要改写成「按需动态 import，不加手动分包」。
- **审计**：媒体工具调用照常落库，可回溯「agent 给用户看过什么」。

## 5. 验收

- `.venv/bin/python -m pytest -q` 退出码 0；新增测试覆盖：
  - 五个工具的参数校验（超限、非有限数值、长度、未知 item、未知关卡、REST 无会话时缺 id）、返回信封形状、REST 直接调用也能拿到 JSON；
  - 免确认（`PolicyEngine` 对五个工具都给 `requires_confirmation=False`）；视觉降级时 `annotate_screenshot` 从 schema 消失且派发被拦、拦截文案按工具名生成，其余四个仍在；
  - `annotate_screenshot` 的像素级几何断言（构造已知色块图 → 断言输出尺寸、框位置与颜色、越界拒绝、字体缺失不抛异常、输出质量 90）；
  - `send_stage_map` 用本地 `1-7` 的真实 tile 文件断言网格尺寸与 tile 码（**注意 1-7 上「起点= tile_start」两种推导等价，起点/终点断言要另选一个 `isStart` 与 `tile_start` 不一致的关卡**）；覆盖表对 107 个 `tileKey` 夹具零遗漏；`code` 多义时的平局规则（如 `ISW-NO`）；未知关卡报 `RESOURCE_ASSET_NOT_FOUND`；`overview.json` 缺失/损坏时优雅失败；层目录缺失时回落到基础层；
  - §3.9：`completed` / `failed` / `skipped` 各留一张且 `pipeline_id`/`task_id` 正确、`cancelled` 不留、截图抛错时任务终态不变、超时不拖慢收尾；
  - §3.10：失败时日志附件字段齐备且与留档同 `sha256`、截图失败时仍写普通失败日志；
  - `get_screenshot` 结果新增 `media` 且既有断言不变；§3.6 的上下文占位。
- 前端：`cd web && pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` 退出码 0；新增 `media.test.ts`（白名单/上限/未知类型/旧形态兼容）、`ChartCard.test.tsx`（option 由 spec 生成、主题切换重建、加载失败回落数据表）、`StageMapCard.test.tsx`、`ImageCard.test.tsx`、`ToolCallBar` 卡片在折叠区之外、`AgentPage.test.tsx`（一轮内出现图片卡与图表卡，刷新后历史里一致）。
- `.venv/bin/python scripts/dump_openapi.py --check`、`scripts/generate_api_guide.py --check`、`scripts/agent_smoke.py`、`scripts/frontend_smoke.py` 退出码 0。
- **行为实测（主 agent）**：假 LLM 驱动一轮，依次调用五个媒体工具 → 断言 SSE `tool_result` 携带 media 信封、消息落库、历史重建出的卡片与实时一致；跑一次真实流水线（或假内核探针）确认任务终态后有留档截图、失败任务有日志附件；真机可用时补一次「给我看现在的画面并圈出理智条」的端到端（不可用则记为补充证据，不作为硬性门槛）。

## 6. 文档同步

- docs/11：§3.2 工具清单加五个工具、§4.4 说明「声明 `SAFE` 即免确认」为何让媒体工具天然不进白名单、§5 新增「媒体卡」小节写契约与安全边界、§8.2 事件与消息、§8.3 上下文占位、§8.4 提示词、§8.5 对话界面的四种卡。
- docs/09 §3.5：图表选型改写为 ECharts（并记明服务端不出图表 PNG）；§4/§9 的手动分包段改写为按需动态 import；§6 对话页加媒体卡四型与数据表兜底、日志与流水线截图的呈现不变。
- docs/04 §5.4：`task_snapshot` 从「只有枚举」变为真实写入点，补一句写入时机与用途；§5.8 的 32 KB 纪律与 M23 的自我约束。
- docs/06：日志附件（`meta.attachment`）新增写入侧说明与失败截图的关系。
- docs/12：M23 条目（裁决、可交付状态、验证方式）。
- docs/13 §8.29：八条用户裁决、实现裁决（无新表/无新端点/无新错误码、`annotate_screenshot` 进 `SCREEN_TOOL_NAMES`、服务端只做截图加工、关卡图由本地 tile 渲染、留档固定开启、失败日志复用同一张图）、干员头像待决、ECharts 推翻 recharts 倾向。
- docs/README 决策速查表「需求 7 · Agent 模块」加一行「会话内媒体与任务留档（M23）」。
- docs/14 不需要重生成（脚本只渲染错误码表与路由标签，本里程碑两者都没变）。

## 7. 实施期必须验证的假设

1. **tile 行序**：`Arknights-Tile-Pos` 的 `tiles[0]` 对应画面上方还是下方，未在文档或本地工具里找到权威说明。按 `view` 矩阵推算「行号增大→画面向上」，因此**按 `tiles[0]` 画在最下方**实现，并在实施期用一张真实关卡（1-7）的画面确认一次；若相反，翻转渲染常量并更新 golden。
2. **`buildableType` 的语义**（0 不可部署 / 1 近战位 / 2 远程位 / 3 二者皆可）是社区口径转述，本地只有数值没有名字。渲染只按数值上色并给出中性图例（「可部署类型 1」等）；若实施期能从内核源码或实测确认语义，再把图例写成「近战位 / 远程位」。
3. **ECharts 在 jsdom 下不能真渲染 canvas**：组件测试对 `echarts/core` 做模块 mock，断言「option 由 spec 正确生成」；数据表兜底与加载失败路径不依赖 canvas。
4. **取图鉴权**走 `api` 客户端（`X-Token`）取 blob，不依赖 cookie——与 `PipelineScreenshots` 一致；若 object URL 在 `ImageViewer` 或 PWA 离线场景下有坑，回落到 `<img src>` + cookie（日志页的既有做法）。
5. **任务终态截图与 MaaCore 抢设备**：任务刚结束时内核可能仍在收尾，`DeviceManager.screenshot(backend="adb")` 与内核 screencap 是否互斥需要实测；失败即降级这条兜底必须在真实环境验证一次。
