# M90 产物与生成式 UI 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `general`、`analyst`、`planner`、`copilot` 四个 Worker 用 `create_view` 生成白名单组件组成的视图产物（`form`/`dashboard`），在工单会话与产物页安全渲染，并把表单提交作为【表单提交】用户消息写进工单 Worker 收件箱。

**Architecture:** 后端先做纯校验层 `artifacts/views.py`（T1），再做 `create_view` 工具与注册（T2），同步四个 Worker 定义与提示词（T3），最后加提交端点与详情 `meta`（T4）。前端先做解析与渲染器（T5），再做卡片、产物页与会话接入（T6）。T7 跑 12 §4 门禁、同步文档并落台账。

**Tech Stack:** FastAPI、Pydantic 2、SQLModel/SQLite、pytest；React 19、TanStack Query、vitest、Testing Library、Tailwind；图表复用 M75 的 `ChartCard`/`parseChartCard`。

**Spec:** [docs/v3/specs/m90-artifacts-genui.md](../specs/m90-artifacts-genui.md)（1462 行，§0、§4、§5 T1–T7、§6、§7、§9、§11、附录 A）

## Global Constraints

- 分支 `artifacts-genui`，从 `dev` 新建；完成后 `git merge --no-ff` 回 `dev`、打 tag `v3-m90`、删分支；**不 push**。
- 不使用 worktree；直接在现有检出上切分支。
- 迁移：无。迁移 head 保持 `0042_demonstration`，不改 SQLModel，不改 `docs/v3/03` 的表结构。
- 工具计数：以 Task 0 实测 `N`/模型可见 `M` 为基线，M90 后 `N+1`/`M+1`（spec §9 的 M89 后预算 `110/109 → 111/110`）；`EXPECTED_TOOLS` 与四处计数断言同步。
- 端点计数：以 Task 0 实测为基线，HTTP +1、`14 §6.15` agent 小节 +1、OpenAPI 里 tag 含 `agent` 的 operation +1（spec 预算 `190/87/91 → 191/88/92`）。
- 错误码、业务表、`SETTING_KEYS`/`_FIELDS` 以 Task 0 实测为准，M90 不新增（spec 预算 `173/172`、`58`、`144/133`）。
- 只给 `ArtifactDetailOut` 加 `meta: dict[str, Any]`；`ArtifactOut` 列表项不加，M75 端点其它语义不动。
- 视图卡只从 `tool_result` 挂：路径写死 `tool.result.result.artifact_id`；`artifact_created` 继续只走 WS `agent_event`，只用于失效产物列表；不加 SSE 事件、不给 `LiveSegment` 加段。
- `create_view` 只给 `general`、`analyst`、`planner`、`copilot` 四个 Worker；总控、子代理、后台代理不加。
- capability 基线取 M89 台账记录的场景数与通过率；不新增评测场景。
- 前端渲染器只产出白名单元素，不执行脚本、不加载外链、不渲染 HTML/Markdown；安全扫描范围是 `web/src/features/artifacts/view/`，交付前退出码必须是 `1`；扫描排除 `*.test.ts`/`*.test.tsx`（测试里故意放 `<img>` 夹具）；卡片里的应用内路由链接不在渲染器内。
- 不新增依赖（不引入 `jsonschema`）；不新增配置项、错误码、表，以及 `submit` 之外的端点。
- 一律 `.venv/bin/python`；本机没有 `rg`，用 `grep -rn`；pytest 看计数用 `-o addopts= -q`；长输出重定向到 `/tmp/m90-*`。
- 提交信息 `feat(m90): <中文摘要>` / `docs(m90): <中文摘要>`；文档用简体中文。

## 开工基线（Task 0 实测后回填）

- `dev` 必须 = M89 合并后的提交；Task 0 用 `git rev-parse --short dev` 与 `git log -1 --format=%H` 记录，计划与台账都写这个值。
- `git tag -l 'v3-m89'` 必须有 `v3-m89`。
- `docs/v3/ledgers/m89-*.md` 必须恰好 1 个文件，且 `grep -n "capability" docs/v3/ledgers/m89-*.md` 至少 1 行；抄下通过率与场景数，T3/T7 与它比较。
- 迁移 head：`ls maa_api/db/migrations/versions | grep -E '^[0-9]{4}_' | sort | tail -1` 输出 `0042_demonstration.py`。
- 计数以 Task 0 实测回填：spec §9 的 M89 后预算为工具 `110 109`、端点 `190`、agent 小节 `87`、OpenAPI agent tag `91`、错误码 `173 172`、表 `58`、配置 `144 133`；实测不同只记录并采用实测值，不当作停止条件。M86–M89 未全部合入时实测会低于预算，这是预期的。
- `ArtifactService.put`、`get`、`update` 与 `WorkerService.post_user_message` 的实际签名符合下文「接缝裁决」；不符合就停。
- §0 #17 的预期：M87–M89 后 `general` 8、`analyst` 4、`planner` 4、`copilot` 4；若不同，按实测值 +1 并在台账记偏离，不照抄本行。
- `create_view` 尚不存在（`grep -rn "create_view" maa_api tests web/src` 无输出、退出码 `1`）。
- 在 `dev` 上跑一次全量 pytest 记录已知失败；M90 合并后的全量结果不得新增失败。

M89 未合入、`v3-m89` tag 或台账缺失、签名与「接缝裁决」不一致时，先向主 agent 汇报；计数/版本与 spec 预算不同不属于停止条件，按实测执行并记台账。

## §0 前置检查

spec §0 共 17 行：前 15 行是环境与签面检查，`#16/#17` 是计数与版本基线。全部在切出的 `artifacts-genui` 上跑。

| # | 检查 | 命令 | 期望 |
|---|---|---|---|
| 1 | 分支与工作区 | `git status -sb` | 第一行 `## artifacts-genui`，无改动 |
| 2 | M89 已合并 | `git tag -l 'v3-m89'` | `v3-m89` |
| 3 | M89 台账与 capability 基线 | `ls docs/v3/ledgers/m89-*.md`；`grep -n "capability" docs/v3/ledgers/m89-*.md` | 1 个文件；至少 1 行，抄下通过率与场景数 |
| 4 | `ArtifactService` 签名 | `grep -nE "async def (put|get|update)\(" maa_api/agent/artifacts/service.py` | 3 行，签名与「接缝裁决」一致 |
| 5 | 详情 wire 现状 | `grep -nE "class Artifact(Out|DetailOut)" -A 14 maa_api/api/schemas/agent_v3.py` | `ArtifactDetailOut` 只有 `content`、`truncated`，没有 `meta` |
| 6 | `ArtifactKind` 有 `FORM`/`DASHBOARD` | `grep -nE "FORM = |DASHBOARD = " maa_api/domain/enums.py` | 2 行 |
| 7 | M75 四个产物端点在快照里 | 附录 A1 | 末行 `MISSING 0` |
| 8 | M72 会话文件都在 | `ls web/src/features/console/conversation/{ConversationView,LiveTurnView}.tsx web/src/features/console/conversation/{transcript-v3,live-turn,sse}.ts` | 5 个文件 |
| 9 | 工具计数 | 附录 A2 | 记录 Task 0 实测 `N M`（spec 预算 `110 109`）；与预算不同只记偏离，不停 |
| 10 | 前端产物 key 现状 | `grep -nE "artifacts:|artifactsAll:|artifact: \(" web/src/api/keys.ts` | 三行，都是函数；没有 `artifactsList` |
| 11 | `parseChartCard` 未导出 | `grep -n "^function parseChartCard" web/src/features/agent/media.ts` | 1 行，行首没有 `export` |
| 12 | `create_view` 尚未出现 | `grep -rn "create_view" maa_api tests web/src; echo $?` | 无输出，退出码 `1` |
| 13 | 四个 Worker 定义存在 | `ls maa_api/agent/specs/builtin/{general,analyst,planner,copilot}.md` | 4 个文件 |
| 14 | 消息写入函数签名 | `grep -n "async def post_user_message" -A 6 maa_api/agent/workers/service.py` | `(ticket_id, content, mentions, *, notify_content=None)` |
| 15 | OpenAPI 与生成类型是当前值 | `.venv/bin/python scripts/dump_openapi.py --check; echo $?`；`cd web && pnpm gen:api:check; echo $?; cd ..` | 两个 `0` |
| 16 | 计数基线 | 附录 A5 | 记录实测 `OPERATIONS/AGENT/ERRORS/TABLES/SETTINGS`（spec 预算 `190 91`、`173 172`、`58`、`144 133`）；M90 只加 HTTP 与 agent 各 1，其余不变 |
| 17 | 四个 Worker 当前版本 | `grep -n "^version:" maa_api/agent/specs/builtin/{general,analyst,planner,copilot}.md` | 4 行；抄下实际值，T3 各 +1 |

`#7`、`#9`、`#16` 的命令在 spec 附录 A1/A2/A5；从 spec 原样复制执行，不重写 A5 的 import 顺序。计数与版本以 Task 0 实测为准：M86–M89 未全部合入时，实测会低于 spec §9 的 M89 后预算，按实测 +1 执行并在台账记偏离，不触发本计划的停止条件。

## 接缝裁决

实现时按这张表，不再另找入口。

| 接缝 | 裁决 |
|---|---|
| `ArtifactService.put` | `put(*, kind, title, body: bytes, mime='text/markdown', session_id=None, ticket_id=None, run_id=None, meta=None, pinned=False) -> Artifact`。参数名是 `body`，不是 `content`。 |
| `ArtifactService.get` | `get(artifact_id, *, max_chars=None) -> ArtifactDetail`；读 `detail.artifact`、`detail.content`、`detail.truncated`，不是行对象。提交端点不传 `max_chars`。 |
| `ArtifactService.update` | `update(artifact_id, *, content: str, meta: dict) -> Artifact`；整份替换正文与 `meta`，提交记录只能走它。 |
| 404 文案 | 服务层 `_not_found()` 的 message 是 `产物不存在`；spec §4.3.3 与测试都按这句断言，不另写「没有这个产物」。 |
| 详情 `meta` | 只给 `ArtifactDetailOut` 加 `meta: dict[str, Any]`；`artifact_detail_out` 现有签名不变，构造时传 `meta=dict(artifact.meta or {})`；`ArtifactOut` 列表项不加。 |
| 前端 key | `keys.agent.artifacts(ticketId)`、`artifactsAll()`、`artifact(id)` 已存在且是函数，不能覆盖成对象；只新增 `artifactsList(filters)`，值为 `[...root, 'agent', 'artifacts', 'list', filters ?? {}]`。注意 `artifact(id)` 是单数 `artifact`，不在 `artifactsAll()` 前缀下，置顶/提交要显式失效它。 |
| Pydantic 错误路径 | 嵌套判别联合的 `loc` 会带判别值段。`_loc_path` 丢掉形如 `"[key]"` 的段；`loc` 以 `("components", <int>)` 开头时，第三段是 `field` 就丢掉，第四段属于 `select`/`number`/`text`/`toggle` 再丢掉；否则第三段属于七种组件名就丢掉。`('components', 3, 'field', 'select', 'options', 0, 'label')` → `components.3.options.0.label`。 |
| 提交写消息 | 用 `request.app.state.worker_service.post_user_message(ticket_id, content, [], notify_content=f"【表单提交】{spec.title}")`；第三个位置参数 `mentions` 必填。不直接调 `InboxService.put_user_message`，不自己查 `Ticket` 或 `AgentSession`。 |
| 服务装配 | 产物服务沿用 `_artifact_service(request)`（未装配 `SERVICE_UNAVAILABLE`）；新增 `_worker_service(request)`，照 `tickets.py` 的写法取 `app.state.worker_service`（未装配 `AGENT_DISABLED`）。 |
| 枚举与 MIME | `ArtifactKind.FORM="form"`、`DASHBOARD="dashboard"` 已存在；只改它的 docstring。`VIEW_MIME="application/vnd.maa.view+json"` 在 `views.py` 新建，不改数据库、不加迁移。 |
| `artifact_created` 通道 | 实现只在 `ArtifactService._broadcast` 里发 WS `agent_event`，不在 `RunEventBus` 里。M90 不补 SSE 转发；`sse.ts`、`live-turn.ts` 不改。 |
| 卡片取值 | `const body = tool.result; const value = isRecord(body) && isRecord(body.result) ? body.result.artifact_id : null`；只收非空字符串。不要写成 `tool.result.artifact_id`。 |
| 测试钉死文件 | 加 `create_view` 会同时红 `test_builtin_workers.py`、`tests/agent/specs/test_m70_specs.py`、`tests/agent/specs/test_agent_specs.py`、`tests/api/test_agent_specs_router.py`；这四个文件都在 T3 写范围，验证命令逐个列路径，不用命令替换。 |
| 前端 M75 复用 | 查询与下载复用 `features/tickets/workspace-api.ts`；`ARTIFACT_KIND_LABELS`/`formatArtifactSize` 只保留一份，移到 `features/artifacts/format.ts`；`dashboard` 统一「看板」，`已置顶`、截断提示、下载失败文案统一。 |

## Review Focus

1. **视图 schema 校验与 loc 路径**：未知组件、嵌套字段、附加规则 1–12 都要报出稳定的 `components.N...` 路径，不能被判别值段污染。T1 的 `test_error_paths_skip_discriminator_segments`、`test_view_rejects_unknown_component_and_extra_keys`、`test_chart_series_must_match_categories` 钉住。
2. **表单提交与新会话消息一致**：一次成功提交只写一条【表单提交】用户消息，正文、收件箱 `thread_key` 与 passive 摘要三者一致；被拒绝的提交不能留下消息或 `meta` 记录。T4 的 `test_submit_writes_user_message_into_worker_inbox`、`test_submit_rejects_invalid_values_with_field_errors`、`test_submit_rejects_oversized_values` 与 T6 的 `submits a form and shows the submitted state` 钉住。
3. **前端只渲染白名单组件、不执行脚本**：文本里的 `<img onerror=…>`、`**粗**`、URL 都按纯文本显示；渲染期不发请求、不产生 `img/iframe/a/object/embed`。T5 的 `renders text as plain text without markup`、`never creates img, iframe or anchor elements from view data`、`does not request anything while rendering` 与安全扫描钉住。
4. **`meta` 提交记录并发写覆盖**：提交记录是读改写，后写覆盖先写，可能丢一条；单次提交限 4 KiB、只留 5 条，把损失限制在记录内。T4 的 `test_submit_keeps_last_five_submissions`、`test_submission_meta_last_write_wins`、`test_submit_rejects_oversized_values` 与 T7 台账的并发窗口说明钉住。
5. **`artifact_created` 漂移不再依赖 SSE**：卡片只从 `tool_result` 挂；即使 `artifact_created` 从不进 SSE，历史与直播都能出卡。T6 的 `attaches the artifact id to create_view calls`、`renders a view card from a live create_view result` 与 `git diff` 里 `sse.ts`/`live-turn.ts` 无改动钉住。

---

### Task 0: 分支、§0、基线与计划入库

**Files:**
- Create: `docs/v3/plans/m90-artifacts-genui.md`（把本文件从 `debug/plans/` 复制过去；`debug/` 已被 gitignore）
- 不改代码

**Interfaces:**
- Consumes: `dev` 的 M89 合并提交、`v3-m89` tag、`docs/v3/ledgers/m89-*.md`。
- Produces: `artifacts-genui` 分支、基线记录（commit、计数、签名、版本、已知失败）、T1–T7 的开工记录。

- [ ] **Step 1: 在 `dev` 上核对 M89 基线并记录**

```bash
git checkout dev
git rev-parse --short dev
git log -1 --format='%H %s'
git tag -l 'v3-m89'
ls docs/v3/ledgers/m89-*.md
grep -n "capability" docs/v3/ledgers/m89-*.md
ls maa_api/db/migrations/versions | grep -E '^[0-9]{4}_' | sort | tail -1
.venv/bin/python -m pytest -o addopts= -q > /tmp/m90-pytest-base.log 2>&1; echo $?; tail -n 30 /tmp/m90-pytest-base.log
```

Expected：`dev` 是 M89 合并提交；tag 与台账存在；迁移 head `0042_demonstration.py`；pytest 退出码为 `0` 或只有既有失败。把 commit 哈希、capability 通过率与场景数、既有失败逐条写进 `/tmp/m90-baseline.txt`；Step 3 的 §0 实测计数与四个 Worker 版本也追加进去，T7 抄进台账。M90 合并后的全量结果不得新增失败。

- [ ] **Step 2: 从 `dev` 开 `artifacts-genui`**

```bash
git checkout -b artifacts-genui
git status -sb
```

Expected：第一行 `## artifacts-genui`，没有其它改动。

- [ ] **Step 3: 在 `artifacts-genui` 上跑 §0 的 17 行检查**

按上面的 §0 表逐条执行；签名、tag/台账、capability 与 spec §10 不一致时停下来问；计数/版本与 spec 预算不同只记实测值并在台账记偏离。特别核对：`ArtifactService` 三个签名、`post_user_message` 的 `mentions`、`ArtifactKind` 的 `FORM`/`DASHBOARD`、`keys` 三个函数、`parseChartCard` 未导出、`create_view` 不存在、四个 Worker 版本、附录 A2/A5 的计数。

- [ ] **Step 4: 把计划放到 `docs/v3/plans/` 并提交**

```bash
mkdir -p docs/v3/plans
cp debug/plans/m90-artifacts-genui.md docs/v3/plans/m90-artifacts-genui.md
git add docs/v3/plans/m90-artifacts-genui.md
git commit -m "docs(m90): 产物与生成式 UI 实施计划"
```

Expected：一次只含计划文件的提交；`git status -sb` 干净。

---

### Task 1: 视图模型、校验与提交渲染（spec T1）

**Files:**
- Create: `maa_api/agent/artifacts/views.py`
- Test: `tests/agent/test_views.py`
- 不改 `maa_api/agent/artifacts/__init__.py`、不改 `service.py`

**Interfaces:**
- Produces: `VIEW_MIME`、`VIEW_VERSION`、`MAX_COMPONENTS`、`MAX_FIELDS`、`MAX_VIEW_BYTES`、`MAX_SUBMISSION_BYTES`、`MAX_SUBMISSIONS_KEPT`、`MAX_CHART_POINTS`、`MAX_CHART_BYTES`、`FIELD_NAME_PATTERN`、`ViewInvalid`、`ViewSpec`、七个组件类、四个字段类、`validate_view(raw: Any) -> ViewSpec`、`view_kind(spec: ViewSpec) -> Literal["form", "dashboard"]`、`view_fields(spec: ViewSpec) -> list[SelectField | NumberField | TextField | ToggleField]`、`view_json_schema() -> dict[str, Any]`、`validate_submission(spec: ViewSpec, values: Mapping[str, Any]) -> dict[str, Any]`、`render_submission(spec: ViewSpec, values: Mapping[str, Any], *, artifact_id: str) -> str`。
- Consumes: `pydantic.BaseModel`、`ConfigDict`、`Field`、`TypeAdapter`、`ValidationError`；不 import `maa_api.agent.tools.media`，图表上限在 `views.py` 内自带一份。

- [ ] **Step 1: 写 `tests/agent/test_views.py`（先红）**

文件头 import `pytest`、`validate_view`、`view_fields`、`view_json_schema`、`view_kind`、`validate_submission`、`render_submission`、`ViewInvalid`、各组件类。按 spec §6 写以下用例，断言值逐字照写：

- `test_view_accepts_every_component`：spec §4.3.1 的例子通过；七种组件各一个的视图通过；例子的 `view_kind` 是 `form`，无字段视图是 `dashboard`；`view_fields` 的 `name` 顺序与组件顺序一致。
- `test_view_rejects_unknown_component_and_extra_keys`：`{"type":"html"}` 抛 `ViewInvalid`，`errors[0] == {"path": "components.0", "message": "取值不在允许范围"}`；组件多一个 `bogus` 时路径 `components.0.bogus`、原因 `不认识的字段`。
- `test_fields_require_exactly_one_trailing_submit`：有字段没 submit、两个 submit、submit 不在最后、没字段却有 submit 四种都报错，`path == "components"`。
- `test_field_names_must_be_unique`：两个 `name` 相同报错；21 个字段报错，路径 `components`。
- `test_table_rows_only_use_declared_columns`：行里多未声明键报错，路径 `components.N.rows.M`；缺的键不报错。
- `test_chart_series_must_match_categories`：`values` 与 `categories` 长度不等报错；`pie` 两条系列报错；总点数 600 通过、604（151 个分类 × 4 条系列）报错（601 是质数，且每条系列长度必须等于分类数、最多 6 条系列，构造不出 601；spec §6 写的 601 按此修正）；24 KiB 超限报错；路径都含 `components.N.chart`。
- `test_number_and_select_defaults_are_checked`：`min > max`、默认值越界、选择默认值不在选项里、`multiple` 与默认值类型不符都报错。
- `test_view_rejects_nan_control_characters_and_oversize`：`NaN`、`Infinity`、`\x01`、标题里的 `\n` 都报错；`text` 组件正文与文本字段 `default` 里的 `\n` 允许；`model_dump_json(exclude_none=True)` 超过 64 KiB 时报错，路径 `""`。
- `test_error_paths_skip_discriminator_segments`：第 4 个组件是 `select`、第 1 个选项缺 `label`，`errors[0]["path"] == "components.3.options.0.label"`。
- `test_json_schema_lists_only_whitelisted_types`：`schema["properties"]["components"]["items"]["discriminator"]["mapping"]` 的键正好是 `heading`、`text`、`stats`、`table`、`chart`、`field`、`submit`；`mapping["field"]["discriminator"]["mapping"]` 的键正好是 `select`、`number`、`text`、`toggle`；`schema["required"]` 是 `version`、`title`、`components`。Pydantic 2.11 把判别器放在这个路径，不要假设在 schema 字典的顶层。
- `test_validate_submission_checks_each_field_type`：覆盖 §4.5.2 表的每种合法与不合法值；未知键原因是 `表单里没有这一项`；`toggle` 缺失按 `false`；数字整数返回 `int`、文本去首尾空白。
- `test_render_submission_uses_labels_and_values`：输出与 §4.5.3 的例子逐字相同，含【表单提交】、前 8 位 id、单选 `label（value）`、多选「、」、数字不带小数点、开关 `是`/`否`、选填 `（未填）`。

- [ ] **Step 2: 跑红**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/test_views.py > /tmp/m90-t1-red.log 2>&1; echo $?; tail -n 8 /tmp/m90-t1-red.log
```

Expected：非 0；`ModuleNotFoundError`（`maa_api.agent.artifacts.views`）。

- [ ] **Step 3: 写模型与 `validate_view`**

按 spec §4.2 的代码清单逐字写常量、`ViewInvalid`、`_Base`（`extra="forbid"`、`str_strip_whitespace=True`）、七个组件类、四个字段类、`FieldBlock`、`Component`、`ViewSpec`、`_VIEW_ADAPTER`。`validate_view` 捕获 `ValidationError`，用 `_loc_path` 清洗 `loc`、`_reason` 映射 `type`（表外保留 Pydantic 的 `msg`），最多 20 条；Pydantic 通过后再按 §4.3.1 的附加规则 1–12 顺序检查，每条错误也截到 20 条。

- [ ] **Step 4: 写其余公开函数**

`view_kind`、`view_fields`、`view_json_schema` 用 `_VIEW_ADAPTER.json_schema()`；`validate_submission` 按 §4.5.2 的字段表逐项校验并规整，失败抛 `ViewInvalid`（path 是字段名，原因用 §4.3.3 的原文）；`render_submission` 按 §4.5.3 的格式拼接。`view_json_schema()` 不进工具声明。

- [ ] **Step 5: 跑绿并核对 schema 形状**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/test_views.py > /tmp/m90-t1.log 2>&1; echo $?; tail -n 5 /tmp/m90-t1.log
```

Expected：`0`，12 passed。把本机 `pydantic.__version__` 记进 `/tmp/m90-baseline.txt`，T7 写台账。

- [ ] **Step 6: 提交**

```bash
git add maa_api/agent/artifacts/views.py tests/agent/test_views.py
git commit -m "feat(m90): 视图模型、校验与提交渲染"
```

---

### Task 2: `create_view` 工具与注册（spec T2）

**Files:**
- Create: `maa_api/agent/tools/artifacts.py`
- Modify: `maa_api/agent/tools/__init__.py`
- Modify: `tests/agent/test_tool_catalog.py`
- Test: `tests/agent/test_create_view_tool.py`

**Interfaces:**
- Produces: `CREATE_VIEW_DESCRIPTION`、`CreateViewParams`、`async create_view(params, ctx) -> dict[str, Any]`、`register_tools(registry: ToolRegistry) -> None`；注册组 `artifacts`、工具名 `create_view`、`risk=ToolRisk.SAFE`、`read_only=False`、`refetchable=False`、`device="none"`、`untrusted_output=False`、`rest_visible=False`、`model_visible=True`。
- Consumes: T1 的 `VIEW_MIME`、`validate_view`、`view_kind`、`view_fields`；`require_run(kinds={"worker"})`；`v3_service(run, ctx, "artifact_service", "产物服务")`。

- [ ] **Step 1: 写 `tests/agent/test_create_view_tool.py`（先红）**

照 `tests/agent/test_artifacts.py` 的 `_current_run`、`RunContext`、`make_session_factory`、`seed_session` 写法搭真实 `ArtifactService`，先 seed 一个 `Ticket`、Worker 会话与 `AgentRun`，`app.state.artifact_service` 挂服务。写四条：

- `test_create_view_stores_form_artifact_for_ticket`：产物 `kind == "form"`、`mime == VIEW_MIME`、`ticket_id`/`session_id`/`run_id` 正确、`meta["submissions"] == []`、`meta["last_submitted_at"] is None`；返回值 `field_count` 等于字段数；`detail.content` 能被 `validate_view(json.loads(...))` 读回。
- `test_create_view_returns_errors_with_paths`：未知组件时抛 `AppError`，`code == ErrorCode.TOOL_ARGS_INVALID`，`details["errors"][0]["path"] == "components.0"`，`Artifact` 表没有新行。
- `test_create_view_requires_a_ticket_session`：`run.ticket_id=None` 时 `INVALID_PARAMETER`，message 逐字 `只能在工单里生成视图`。
- `test_create_view_is_hidden_from_rest_catalog`：`registry.get("create_view").rest_visible is False`；`GET /api/agent/tools` 不含它；用 `SimpleNamespace(registry=registry)` 调 `_ensure_rest_visible` 抛 `TOOL_NOT_FOUND`。

- [ ] **Step 2: 跑红**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/test_create_view_tool.py > /tmp/m90-t2-red.log 2>&1; echo $?; tail -n 8 /tmp/m90-t2-red.log
```

Expected：非 0；`ImportError: cannot import name 'create_view'` 或 `maa_api.agent.tools.artifacts` 不存在。

- [ ] **Step 3: 实现工具并注册**

按 spec §4.2 写 `CREATE_VIEW_DESCRIPTION`、`CreateViewParams`、`create_view`、`register_tools`。`create_view` 先 `require_run(kinds={"worker"}, message="create_view 只能在 Worker 里调用")`，再检查 `run.ticket_id`，再 `validate_view`；`put` 时 `body=spec.model_dump_json(exclude_none=True).encode("utf-8")`、`meta={"view_version": 1, "submissions": [], "last_submitted_at": None}`；返回 `artifact_id`、`kind`、`title`、`field_count`。`__init__.py` 在 import 区加 `register_artifacts_tools`，在 `register_goal_tools(registry)` 之后加注释与调用。

- [ ] **Step 4: 同步工具全集与计数**

`tests/agent/test_tool_catalog.py`：`EXPECTED_TOOLS` 加 `create_view`；`test_untrusted_output_tools_are_pinned` 与 `test_registry_exposes_all_delivered_m11_tools_and_no_deferred_copilot_runner` 两个测试里的四处计数断言（`len(actual)` 与 `model_visible` 各两条）改成 Task 0 实测值 +1（spec 预算 `110/109 → 111/110`）；检出低于 `110/109` 只说明 M86–M89 尚未全部合入，记实测并 +1，不按 §0 停；计数历史注释末尾补一句 M90 的 `+1`；断言 `build_registry()` 有 `artifacts` 组。

- [ ] **Step 5: 跑绿**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/test_create_view_tool.py tests/agent/test_tool_catalog.py > /tmp/m90-t2.log 2>&1; echo $?; tail -n 5 /tmp/m90-t2.log
```

Expected：`0`；工具总数与模型可见数都是 Task 0 实测值 +1（spec 预算 `111`/`110`）。

- [ ] **Step 6: 提交**

```bash
git add maa_api/agent/tools/artifacts.py maa_api/agent/tools/__init__.py tests/agent/test_create_view_tool.py tests/agent/test_tool_catalog.py
git commit -m "feat(m90): create_view 工具与注册"
```

---

### Task 3: 四个 Worker 定义与提示词（spec T3）

**Files:**
- Modify: `maa_api/agent/specs/builtin/{general,analyst,planner,copilot}.md`
- Modify: `maa_api/agent/specs/common.md`
- Modify: `tests/agent/test_builtin_workers.py`
- Modify: `tests/agent/specs/test_m70_specs.py`
- Modify: `tests/agent/specs/test_agent_specs.py`
- Modify: `tests/api/test_agent_specs_router.py`

**Interfaces:**
- Produces: 四个定义 `tools` 末尾含 `create_view`、`version` 各 +1；`common.md` 新【表单提交】条目；四份测试的期望集合与版本映射。
- Consumes: T2 的 `create_view` 已注册；§0 #17 的四个实际版本；§0 #3 的 capability 基线。

- [ ] **Step 1: 写测试（先红）**

- `tests/agent/test_builtin_workers.py`：`WORKERS` 的 `copilot`、`planner`、`analyst` 工具元组末尾加 `create_view`；每项加/改 `version` 字段，四个加工具的 Worker 用 §0 实测值 +1，`daily_ops`、`diagnose`、`ops` 保持实测值；新增 `test_workers_with_create_view_bump_version`，断言四个定义含 `create_view` 且版本等于映射值，`daily_ops`/`diagnose`/`ops` 不含且版本不变；原有 `test_six_workers_match_contract` 改成 `spec.version == expected["version"]`（M87 已引入时直接改值）。
- M88/M89 的既有变化（列出，不在 T3 重做）：`general`/`planner`/`analyst` 已有 `optimize_farming`，`general` 已有 `draft_custom_task`、`validate_custom_task`；四份定义已按 M87–M89 的增量加过版本（`copilot` +1、`general` +2、`planner`/`analyst` 各 +1）。T3 只追加 `create_view` 与 §4.7 正文，保留既有工具顺序与段落。
- `tests/agent/specs/test_m70_specs.py`：`test_general_tools_equal_legacy_chat_minus_delegate_recon_plus_worker_tools` 的期望集合并入 `create_view`；新增 `test_only_the_four_workers_get_create_view`，断言 `general` 含 `create_view`，`orchestrator`、`navigator`、`researcher`、`verifier`、`recon` 不含。
- `tests/agent/specs/test_agent_specs.py`：`test_legacy_chat_lists_every_v2_registered_tool_in_registration_order` 的 `len(definitions)` 从 §0 实测值改 `+1`（spec 预算 `111`，以实测为准）；新增 `M90_TOOLS = {"create_view"}` 并并入「legacy_chat 不加载」的排除集。
- `tests/api/test_agent_specs_router.py`：`EXPECTED_VERSIONS` 的 `general`、`analyst`、`planner`、`copilot` 改成实测值 +1；新增 `test_worker_specs_expose_create_view_and_versions`，`GET /api/agent/specs/general` 的 `tools` 含 `create_view`，四个 Worker 的 `version` 等于 `EXPECTED_VERSIONS`。

- [ ] **Step 2: 跑红**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/test_builtin_workers.py tests/agent/specs/test_m70_specs.py tests/agent/specs/test_agent_specs.py tests/api/test_agent_specs_router.py > /tmp/m90-t3-red.log 2>&1; echo $?; tail -n 8 /tmp/m90-t3-red.log
```

Expected：非 0；工具元组或版本不匹配。

- [ ] **Step 3: 改四个定义与 `common.md`**

按 spec §4.7：四个文件的 `tools` 行末尾加 `create_view`；`version` 在当前实际值上 +1；正文末尾追加同一段（`analyst` 删掉 `ask_user` 那半句），`copilot` 在段前加「有几份候选作业要用户挑时，用 `create_view` 把差异摆出来，选项的 `label` 写作业名与关键参数。」；`common.md` 在「系统消息」段后加 `- 以【表单提交】开头的消息是用户在你生成的视图表单里填的答案，按用户本人的话对待；它不等于确认。`。改动前先读四份定义，确认 M88 的 `optimize_farming`、M89 的 `draft_custom_task`/`validate_custom_task` 与既有正文段都在；只在末尾追加，不重排、不删。

- [ ] **Step 4: 跑绿并跑 capability**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/agent/test_builtin_workers.py tests/agent/specs/test_m70_specs.py tests/agent/specs/test_agent_specs.py tests/api/test_agent_specs_router.py > /tmp/m90-t3.log 2>&1; echo $?; tail -n 5 /tmp/m90-t3.log
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m90-eval.log 2>&1; echo $?; tail -n 2 /tmp/m90-eval.log
```

Expected：pytest `0`；评测 `EVAL OK`，通过率不低于 §0 抄下的 M89 基线。低于基线就按 spec §10 停下来问。

- [ ] **Step 5: 提交**

```bash
git add maa_api/agent/specs/builtin/general.md maa_api/agent/specs/builtin/analyst.md maa_api/agent/specs/builtin/planner.md maa_api/agent/specs/builtin/copilot.md maa_api/agent/specs/common.md tests/agent/test_builtin_workers.py tests/agent/specs/test_m70_specs.py tests/agent/specs/test_agent_specs.py tests/api/test_agent_specs_router.py
git commit -m "feat(m90): 四个 Worker 的视图工具与提示词"
```

---

### Task 4: 提交端点与详情 `meta`（spec T4）

**Files:**
- Modify: `maa_api/api/routers/artifacts.py`
- Modify: `maa_api/api/schemas/agent_v3.py`
- Modify: `maa_api/domain/enums.py`（只改 docstring）
- Modify: `docs/v3/14-现行接口清单.md`
- Modify: `web/openapi.json`、`web/src/types/api.d.ts`（由脚本生成）
- Test: `tests/api/test_artifact_form_submit.py`
- Test: `tests/api/test_agent_workspace.py`

**Interfaces:**
- Produces: `ArtifactFormSubmit`（`values: dict[str, Any]`，`extra="forbid"`）、`ArtifactFormSubmitResult`（`artifact_id`、`ticket_id`、`submitted_at`）、`_worker_service(request) -> Any`、`async submit_artifact_form(request, payload, artifact_id) -> ArtifactFormSubmitResult`；`ArtifactDetailOut.meta`；`artifact_detail_out` 的 `meta` 赋值。
- Consumes: T1 的 `VIEW_MIME`、`validate_view`、`validate_submission`、`render_submission`、`ViewInvalid`；`ArtifactDetail`；`WorkerService.post_user_message`；`error_responses`。

- [ ] **Step 1: 写 `tests/api/test_artifact_form_submit.py`（先红）**

照 `tests/api/test_ticket_messages_router.py` 的临时库 + `TicketService` + `WorkerService` + `InboxService` 装配，再加 `ArtifactService` 并挂 `app.state.artifact_service`；`app.state.worker_service` 用真实 `WorkerService`。写以下用例：

- `test_submit_writes_user_message_into_worker_inbox`：200；响应有 `artifact_id`、`ticket_id`、`submitted_at`；Worker 会话多一条 `role=user` 消息，正文以 `【表单提交】` 开头；收件箱有一条 `thread_key="ticket:<id>"` 的待处理项；总控收到一条 passive 摘要 `【表单提交】{title}`。
- `test_submit_rejects_invalid_values_with_field_errors`：两个字段不合法 → 400 `INVALID_PARAMETER`，message `表单有 2 处需要修改`，`details.fields` 有两个键与 §4.3.3 的原因；没有写消息。
- `test_submit_rejects_non_form_artifact`：`dashboard` 与 `note` 产物都 400，message `这个产物不是表单`。
- `test_submit_rejects_corrupt_view`：`form` 产物的正文不是 JSON，或 `validate_view` 不过 → 400，message `表单定义已损坏，请让 Worker 重新生成`。
- `test_submit_rejects_artifact_without_ticket`：`ticket_id` 为 NULL → 400，message `这个表单不属于任何工单`。
- `test_submit_rejects_oversized_values`：20 个文本框各 500 字 → 序列化超过 4096 字节，400，message `提交内容太长`，`details.limit_bytes == 4096`；没有写消息。
- `test_submit_on_ended_ticket_returns_ticket_ended`：工单 `done` → 409 `TICKET_ENDED`，message `工单已结束`。
- `test_submit_after_ticket_deleted_reports_no_ticket`：删除产物指向的 `Ticket` 后外键把 `artifact.ticket_id` 置 NULL，提交 400 `INVALID_PARAMETER`、message `这个表单不属于任何工单`；`TICKET_NOT_FOUND` 只作 `post_user_message` 的防御分支，不另造关外键的夹具；没有写消息。
- `test_submit_unknown_artifact_returns_404`：404 `ARTIFACT_NOT_FOUND`，message `产物不存在`。
- `test_submit_keeps_last_five_submissions`：产物预置 `meta["brief"]="旧简报"`；顺序提交 6 次，`meta["submissions"]` 长度 5，`meta["last_submitted_at"]` 是第 6 次，最后一条 `values` 是第 6 次的值，`meta["brief"]` 仍是 `旧简报`。
- `test_submission_meta_last_write_wins`：取两次 `ArtifactService.get` 的 `meta` 快照，各自追加不同 `values` 后依次 `update`；最终 `meta["submissions"]` 只有第二次那条，钉住「读改写、后写覆盖先写」的已知窗口。
- `test_submit_route_is_in_agent_group`：`app.openapi()` 里该路径 `post.operationId == "agent_submit_artifact_form"`，`tags == ["agent"]`。

`tests/api/test_agent_workspace.py` 新增 `test_detail_returns_meta_and_list_does_not`：写一条 `meta={"facts_markdown": "x"}` 的产物；详情响应有 `meta` 且值相同；列表项的键里没有 `meta`。

- [ ] **Step 2: 跑红**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/api/test_artifact_form_submit.py > /tmp/m90-t4-red.log 2>&1; echo $?; tail -n 8 /tmp/m90-t4-red.log
```

Expected：非 0；`404`/`AttributeError`（端点不存在或 `ArtifactDetailOut` 没有 `meta`）。

- [ ] **Step 3: 实现端点**

按 spec §4.2/§4.3.3/§4.5.2 写两个模型、`_worker_service`、`submit_artifact_form`，`responses=error_responses("ARTIFACT_NOT_FOUND", "INVALID_PARAMETER", "TICKET_NOT_FOUND", "TICKET_ENDED", "SERVICE_UNAVAILABLE", "AGENT_DISABLED", "LLM_NOT_CONFIGURED", "LLM_ENCRYPTION_KEY_INVALID", "VALIDATION_ERROR", "UNAUTHORIZED")`。顺序固定为：`get`（不传 `max_chars`）→ 类型判断 → 解析并 `validate_view` → `ticket_id` 判断 → 4 KiB 判断 → `validate_submission` → `render_submission` → `post_user_message(artifact.ticket_id, content, [], notify_content=f"【表单提交】{spec.title}")` → 合并 `meta` 并 `update`。`meta` 从 `dict(artifact.meta or {})` 出发，只改 `submissions` 与 `last_submitted_at`，`brief`、`brief_date`、`status`、`origin`、`ticket_id`、`facts_markdown` 原样保留。`submitted_at` 用 `utcnow()`，用 `datetime` 的 UTC naive 值格式化成 `YYYY-MM-DDTHH:MM:SSZ`；`meta` 的 `submissions` 只保留 `[-5:]`，`last_submitted_at` 与响应共用同一个字符串。第二步失败时消息已发出、响应 500，台账记这个已知窗口。删工单时外键把 `artifact.ticket_id` 置 NULL，先命中「这个表单不属于任何工单」；`TICKET_NOT_FOUND` 只作 `require_open_ticket` 的防御分支。

- [ ] **Step 4: 加详情 `meta` 与 docstring**

`ArtifactDetailOut` 加 `meta: dict[str, Any]`；`artifact_detail_out` 构造时传 `meta=dict(artifact.meta or {})`，签名不变；`ArtifactOut` 不动。`ArtifactKind` 的 docstring 改成 `note`、`spill`、`report`、`chart`、`table`、`file`、`form`、`dashboard`。

- [ ] **Step 5: 同步 14 号文档并重新生成快照**

`docs/v3/14-现行接口清单.md` §6.15 的 agent 小节加 spec §9 给出的 `POST /api/agent/artifacts/{artifact_id}/submit` 行；把 agent 小节当前的 `（N 条）` 改成 `N+1`、§6 开头的 `共 M 条` 改成 `M+1`。N/M 用 Task 0 实测值（spec 预算 `87→88`、`190→191`）；实测不同只记偏离，不按 §10 停。然后：

```bash
.venv/bin/python scripts/dump_openapi.py
cd web && pnpm gen:api && pnpm gen:api:check; cd ..
.venv/bin/python scripts/dump_openapi.py --check; echo $?
```

Expected：两个 `--check`/`gen:api:check` 退出码 `0`。

- [ ] **Step 6: 跑绿**

```bash
.venv/bin/python -m pytest -o addopts= -q tests/api/test_artifact_form_submit.py tests/api/test_agent_workspace.py tests/domain/test_route_inventory.py > /tmp/m90-t4.log 2>&1; echo $?; tail -n 5 /tmp/m90-t4.log
```

Expected：`0`；路由清单总数与 agent 小节计数通过。

- [ ] **Step 7: 提交**

```bash
git add maa_api/api/routers/artifacts.py maa_api/api/schemas/agent_v3.py maa_api/domain/enums.py docs/v3/14-现行接口清单.md web/openapi.json web/src/types/api.d.ts tests/api/test_artifact_form_submit.py tests/api/test_agent_workspace.py
git commit -m "feat(m90): 表单提交端点与产物详情 meta"
```

---

### Task 5: 前端视图解析与渲染（spec T5）

**Files:**
- Modify: `web/src/features/agent/media.ts`（只给 `function parseChartCard` 加 `export`）
- Modify: `web/src/features/agent/format.ts`（`TOOL_LABELS` 加 `create_view: '生成视图'`）
- Create: `web/src/features/artifacts/view/schema.ts`
- Create: `web/src/features/artifacts/view/ViewRenderer.tsx`
- Create: `web/src/features/artifacts/view/ViewField.tsx`
- Create: `web/src/features/artifacts/view/ViewTable.tsx`
- Create: `web/src/features/artifacts/view/ViewStats.tsx`
- Test: `web/src/features/artifacts/view/schema.test.ts`
- Test: `web/src/features/artifacts/view/ViewRenderer.test.tsx`

**Interfaces:**
- Produces: `VIEW_MIME`、`VIEW_LIMITS`、`ViewStat`、`ViewColumn`、`ViewCell`、`ViewField`、`ViewComponent`、`ParsedView`、`FormValue`、`FormValues`、`parseView(raw: unknown): ParsedView | null`、`initialValues(fields, submitted?): FormValues`、`validateValues(fields, values): Record<string, string>`、`toSubmitPayload(fields, values): Record<string, unknown>`。
- 组件 props 写死：`ViewRendererProps { view: ParsedView; mode: 'interactive' | 'readonly'; values: FormValues; errors: Record<string, string>; onChange: (name: string, value: FormValue) => void; onSubmit?: () => void; submitting?: boolean }`；`ViewFieldProps { spec: ViewField; value: FormValue; error: string | undefined; disabled: boolean; onChange: (value: FormValue) => void }`；`ViewTableProps { component: Extract<ViewComponent, { type: 'table' }> }`；`ViewStatsProps { items: readonly ViewStat[] }`。
- Consumes: `parseChartCard`、`ChartCardData`、`ChartCard`、`Field`、`NativeSelect`、`Input`、`Textarea`、`SwitchRow`、`Chip`、`Button`、`InlineAlert`。

- [ ] **Step 1: 写前端测试（先红）**

`schema.test.ts` 六条：`parses every whitelisted component`（七种组件、字段顺序）、`drops unknown or malformed components and counts them`（未知类型/缺字段/类型不对被丢弃并计入 `dropped`；另用 41 个合法组件断言只留前 40、`dropped` 为 1）、`returns null for an unsupported version or shape`（`version: 2`、缺 `title`、`components` 不是数组）、`builds initial values from defaults or the last submission`（两种来源、选填缺失为空）、`validates values like the server`（原因文案与后端一致：`必填`、`不在可选项里`、`选项不合法`、`不能重复选择`、`需要填数字`、`不能小于 {min}`、`不能大于 {max}`、`需要是文本`、`不能超过 {max_length} 字`、`需要是开或关`、`表单里没有这一项`）、`serializes numbers and omits empty optional fields`（数字框字符串转数字，空选填省略）。

`ViewRenderer.test.tsx` 六条：`renders text as plain text without markup`（`<b>粗</b>`、`**粗**` 原样显示）、`never creates img, iframe or anchor elements from view data`（文本里放 `<img src=x onerror=alert(1)>` 与 `https://evil.example/x.png`，`container.querySelector('img, iframe, a, object, embed')` 为 `null`）、`does not request anything while rendering`（`fetch` 一次都没被调用）、`renders stats, tables and charts with the data table fallback`（数字格式、百分比、布尔、`—`；`delta` 正数 `+3`、负数 `−2`、零不显示；`hint` 小字；图表卡「数据」折叠表存在）、`disables every field in readonly mode`（全部控件 `disabled`，没有提交按钮）、`renders heading levels`（level 2 是 `h3`，level 3 是 `h4`）。

- [ ] **Step 2: 跑红**

```bash
cd web && pnpm test --run src/features/artifacts/view > /tmp/m90-t5-red.log 2>&1; echo $?; cd ..; tail -n 8 /tmp/m90-t5-red.log
```

Expected：非 0；`schema.ts`/`ViewRenderer.tsx` 不存在。

- [ ] **Step 3: 实现解析**

`media.ts` 的 `parseChartCard` 前加 `export`，其余不动。`schema.ts` 的 `parseView` 永不抛异常：顶层不是对象、`version !== 1`、`title` 缺失、`components` 不是数组返回 `null`；单个组件不合法就丢弃并计入 `dropped`；超过 40 个只留前 40；图表组件用 `parseChartCard({ chart: component.chart, caption: component.caption ?? '' })`，返回 `null` 也丢弃；上限与 `VIEW_LIMITS` 和服务端一致。`initialValues`、`validateValues`、`toSubmitPayload` 按 §4.5.4 与 §4.5.2 的规则实现。

- [ ] **Step 4: 实现渲染器**

`ViewRenderer` 只产出标题、段落、`dl`、`table`、表单控件、按钮与 `ChartCard`；有字段时整张视图包在 `<form noValidate onSubmit>` 里，没有字段时不包；文本用 `whitespace-pre-wrap` 纯文本，不解析 Markdown，不生成 `img/iframe/a/object/embed`，不把视图字符串当 URL/类名/样式/事件处理。数字用 `Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 })`；`percent` 列按 0–100 显示并加 `%`；布尔 `是`/`否`；`null` `—`。只读模式全部 `disabled` 且不渲染 submit；`heading` 2→`h3`、3→`h4`；`stats` 用 `dl`，`delta` 正数 `+3`、负数 `−2`、零不显示，`hint` 小字；`table` 外层 `max-h-96 overflow-auto rounded-lg border`，第一列 `th scope="row"`，右对齐列加 `tabular-nums`，`caption` 写在表下；`select` 单选用 `NativeSelect`（首项禁用 `选择一项`）、多选用 `Chip`；`number` 用 `Input type="number"`；`text` 用 `Input`/`Textarea`；`toggle` 用 `SwitchRow`；提交按钮取 `label`，提交中 `loading`。`TOOL_LABELS` 加 `create_view: '生成视图'`。

- [ ] **Step 5: 跑绿与安全扫描**

```bash
cd web && pnpm test --run src/features/artifacts/view > /tmp/m90-t5.log 2>&1; echo $?; cd ..; tail -n 5 /tmp/m90-t5.log
grep -rnE "dangerouslySetInnerHTML|<img|<iframe|<a |href=|<object|<embed" web/src/features/artifacts/view --include='*.ts' --include='*.tsx' --exclude='*.test.ts' --exclude='*.test.tsx'; echo $?
```

Expected：pytest 等价的 vitest `0`；扫描排除 `*.test.ts`/`*.test.tsx` 后无输出、退出码 `1`（不排除会命中 `ViewRenderer.test.tsx` 里的 `<img>` 夹具）。

- [ ] **Step 6: 提交**

```bash
git add web/src/features/agent/media.ts web/src/features/agent/format.ts web/src/features/artifacts/view
git commit -m "feat(m90): 前端视图解析与渲染"
```

---

### Task 6: 视图卡、产物页与会话接入（spec T6）

**Files:**
- Create: `web/src/features/artifacts/api.ts`
- Create: `web/src/features/artifacts/format.ts`
- Create: `web/src/features/artifacts/ArtifactChip.tsx`
- Create: `web/src/features/artifacts/ViewArtifactBody.tsx`
- Create: `web/src/features/artifacts/ViewArtifactCard.tsx`
- Create: `web/src/features/artifacts/ArtifactsPage.tsx`
- Create: `web/src/features/artifacts/ArtifactDetailPage.tsx`
- Modify: `web/src/features/console/conversation/transcript-v3.ts`
- Modify: `web/src/features/console/conversation/ConversationView.tsx`
- Modify: `web/src/features/console/conversation/LiveTurnView.tsx`
- Modify: `web/src/features/tickets/TicketDetailPage.tsx`
- Modify: `web/src/features/tickets/ArtifactList.tsx`
- Modify: `web/src/features/tickets/workspace-api.ts`
- Modify: `web/src/features/tickets/format.ts`（把 `ENDED_STATUSES` 从 `TicketDetailPage.tsx` 提到这里导出，两个页面共用）
- Modify: `web/src/api/keys.ts`
- Modify: `web/src/components/layout/nav.ts`
- Modify: `web/src/routes/router.tsx`
- Modify: `web/src/features/console/test-utils.tsx`
- Test: `web/src/features/artifacts/ViewArtifactCard.test.tsx`
- Test: `web/src/features/artifacts/ArtifactsPage.test.tsx`
- Test: `web/src/features/artifacts/ArtifactDetailPage.test.tsx`
- Test: `web/src/features/artifacts/format.test.ts`
- Test: `web/src/features/tickets/ArtifactList.test.tsx`
- Test: `web/src/features/console/conversation/transcript-v3.test.ts`
- Test: `web/src/features/tickets/TicketDetailPage.test.tsx`
- Test: `web/src/routes/more/more.test.tsx`
- Test: `web/src/components/layout/layout.test.tsx`

**Interfaces:**
- Produces: `keys.agent.artifactsList(filters?)`；`artifactsListInfiniteQuery({ kind, ticketId })`；`usePinArtifact()`；`useSubmitArtifactForm(artifactId, ticketId)`；`downloadName(title, mime)`；`ARTIFACT_KIND_LABELS`、`formatArtifactSize`；`ArtifactChip`、`ViewArtifactBody`、`ViewArtifactCard`；`artifactIdOf(tool: ToolView): string | null`；`ConversationEntry` 的 `tool` 分支 `artifactId`；`ConversationView`/`LiveTurnView` 的 `ticketEnded?: boolean`，以及 `LiveTurnView` 内部 `LiveRunView` 的逐层透传。
- 组件 props 写死：`ViewArtifactBodyProps { artifact: ArtifactDetailView; ticketEnded: boolean }`；`ViewArtifactCardProps { artifactId: string; ticketEnded: boolean }`；`ArtifactChipProps { artifactId: string; title: string; kind: string }`。
- Consumes: T4 的 `ArtifactDetailOut.meta`、`ArtifactFormSubmit`、`ArtifactFormSubmitResult`；T5 的 `parseView`、`initialValues`、`validateValues`、`toSubmitPayload`、`ViewRenderer`；M75 的 `ArtifactView`、`ArtifactDetailView`、`artifactDetailQueryOptions`、`saveArtifactFile`、`artifactsQueryOptions`。

- [ ] **Step 1: 写 key、format 与 api 测试（先红）**

`format.test.ts` 三条：`maps artifact kinds to labels`（`dashboard` 是 `看板`，八种齐全）、`formats sizes`（`812 B`、`3.4 KB`、`1.2 MB`）、`builds download names`（markdown→`md`、视图与 json→`json`、`text/csv`→`csv`、`text/plain`→`txt`、其他→`bin`；非法字符删除；空标题回 `artifact`）。

写完先跑红：

```bash
cd web && pnpm test --run src/features/artifacts/format.test.ts > /tmp/m90-t6-red1.log 2>&1; echo $?; cd ..; tail -n 5 /tmp/m90-t6-red1.log
```

Expected：非 0（`features/artifacts/format.ts` 尚未创建）。

- [ ] **Step 2: 实现 key、format 与 api**

`keys.agent.artifactsList: (filters?) => [...root, 'agent', 'artifacts', 'list', filters ?? {}] as const`。`format.ts` 放 `ARTIFACT_KIND_LABELS`（`note`、`spill`、`report`、`chart`、`table`、`file`、`form`、`dashboard`，`dashboard: '看板'`）、`formatArtifactSize`、`downloadName`。`api.ts` 的查询与 mutation 严格按 spec §4.6.1 表：列表 `keys.agent.artifactsList({ kind, ticket_id: ticketId })`、GET `/api/agent/artifacts`、`page`/`size=20`、`getNextPageParam` 用 `page * size < total`；置顶 PUT `/api/agent/artifacts/{artifact_id}/pin`，成功失效 `artifactsAll()` 与 `artifact(id)`；提交 POST `/api/agent/artifacts/{artifact_id}/submit`，成功失效 `artifact(id)`、`artifactsAll()`、`tickets.messagesAll(ticketId)`、`tickets.detail(ticketId)`。`workspace-api.ts` 的 `saveArtifactFile` 改用 `downloadName` 并删掉 `filenameOf`/`extensionOf`，`ArtifactList.tsx` 改成 import 共享的 label/size。

- [ ] **Step 3: 写卡片与页面测试（先红）**

`ViewArtifactCard.test.tsx` 七条：`submits a form and shows the submitted state`（POST 体是规整后的值；Toast `已提交表单`；切只读并显示 `已提交 · …`）、`maps server field errors onto the fields`（400 的 `details.fields` 落到字段，表单上方显示服务端 message）、`reopens a submitted form for editing`（`meta.submissions` 有值时初始只读；点 `修改后重新提交` 切回可填，初值是上次提交值）、`locks the form when the ticket has ended`（`ticketEnded` 与 409 两条路径都只读并提示 `工单已结束，不能再提交`）、`falls back to a chip for non-view artifacts`（笔记产物显示胶囊链接）、`shows the dropped component notice`（未知组件或 41 个组件时显示 `有 1 个组件无法显示`）、`shows an alert when the view cannot be parsed`（正文不是 JSON 或 `meta.view_version` 不是 1 时 `视图无法显示`）。

`ArtifactsPage.test.tsx` 四条：`lists artifacts with a kind filter and the pinned badge`（`?kind=form` 写进请求参数；行描述含种类、大小、时间；`已置顶` 徽章）、`loads more pages`（25 条时点 `加载更多` 发第二页并追加 5 条）、`shows and clears the ticket filter chip`（`?ticket_id=t1` 出现 `只看这个工单`，点击清参数）、`shows the empty state`（`还没有产物` 与 `前往指挥台`）。

`ArtifactDetailPage.test.tsx` 八条：`renders markdown, text and view artifacts by mime`（三种正文；视图渲染表单）、`shows a truncation notice with a download button`（`truncated` 时 `内容较长，只显示前 64 KiB` 与按钮）、`shows the not-found state`（404 `没有这个产物` 与 `返回产物列表`）、`submits a form from the detail page`（POST 与 Toast；成功后只读）、`links the owning ticket`（`ticket_id` 存在时 `所属工单「…」`）、`keeps rendering when the owning ticket is gone`（`ticket_id` 非空但 `ticketDetailQuery` 404 时仍渲染产物，链接回落 `所属工单 {id}`）、`pins and unpins from the detail page`（PUT 体与 Toast `已置顶`/`已取消置顶`；产物列表失效）、`downloads the raw content`（走 `api` 客户端；`URL.createObjectURL` 后 `revokeObjectURL`；文件名按 `downloadName`）。

写完先跑红：

```bash
cd web && pnpm test --run src/features/artifacts/ViewArtifactCard.test.tsx src/features/artifacts/ArtifactsPage.test.tsx src/features/artifacts/ArtifactDetailPage.test.tsx > /tmp/m90-t6-red2.log 2>&1; echo $?; cd ..; tail -n 5 /tmp/m90-t6-red2.log
```

Expected：非 0（卡片与两个页面文件尚未创建）。

- [ ] **Step 4: 写顺序、卡片与页面**

`ArtifactChip` 用应用内 `<Link>` 指向 `/more/artifacts/{artifactId}`，包一层胶囊样式（`inline-flex h-9 items-center rounded-full border px-3.5 text-sm font-medium`），显示标题与 `ARTIFACT_KIND_LABELS[kind] ?? kind`；`Chip` 本身是 button，不能嵌套链接，所以只复用外观类，不直接渲染 `Chip`；不渲染正文。`ViewArtifactBody` 读 `artifactDetailQueryOptions` 的 `ArtifactDetailView`，`parseView(JSON.parse(content))` 失败或 `meta.view_version !== 1` 时显示 `InlineAlert` `视图无法显示` 与 `在产物页打开`；`dropped > 0` 时底部小字；初值取 `meta.submissions` 最后一次的 `values`，否则默认值；有过提交时初始只读并显示 `已提交 · {时间}` 与 `修改后重新提交`；提交用 `useSubmitArtifactForm(artifact.id, artifact.ticket_id ?? '')`，先 `validateValues`，有错就地显示；成功 Toast `已提交表单`；400 且 `details.fields` 落到字段并在表单上方显示服务端 message；409 切只读并提示；其他错误 `提交表单失败：{原因}`；父组件用 `key={artifact.id}` 重置状态。`ViewArtifactCard` 读详情，加载中 `Skeleton h-24 w-full`，读失败 `读取产物失败：{原因}`，`mime` 非视图退化成 `ArtifactChip`；卡片头图标（`LayoutDashboard`/`ClipboardList`）、标题、徽章 `看板`/`表单`、右侧 `在产物页打开` 去 `/more/artifacts/{id}`。

`ArtifactsPage` 用 `Page title="产物" back="/more"`；`NativeSelect` 标签 `种类`，选项 `全部` 与八个 label，选择写 `?kind=`，未知 kind 按全部处理且不改 URL；`?ticket_id=` 存在时显示可移除芯片 `只看这个工单`；列表行 `ListItem` 链到 `/more/artifacts/{id}`，图标按种类写死 `note: NotebookPen`、`spill: ExternalLink`、`report: FileText`、`chart: ChartLine`、`table: Table`、`file: File`、`form: ClipboardList`、`dashboard: LayoutDashboard`，未知用 `FileText`，描述 `{种类} · {formatArtifactSize} · {时间}`，置顶行 `trailing` 是 `已置顶`；底部 `加载更多`（没有下一页不渲染）；空状态 `还没有产物` / `Worker 生成报告、表单或看板后会列出来` / `前往指挥台`。

`ArtifactDetailPage` 用 `Page title={detail.title} back="/more/artifacts"`；操作 `置顶`/`取消置顶`（Toast `已置顶`/`已取消置顶`）、`下载`（失败 `下载产物失败：{原因}`）；元信息含种类徽章、时间、大小、`所属工单「{标题}」`（`ticketDetailQuery(detail.ticket_id ?? '')` 并加 `enabled: Boolean(detail.ticket_id)`，读不到标题显示 `所属工单 {id}`，查询 404 时也保持页面可读）；正文按 `mime`：视图→`ViewArtifactBody`（用同一 `ticketDetailQuery` 的 `ticket.status` 按 `ENDED_STATUSES` 算 `ticketEnded`；无 `ticket_id` 或查询失败时为 `false`）、`text/markdown`→v2 `Markdown`、`application/json`/`text/plain`/`text/csv`→`pre`（`max-h-[70dvh] overflow-auto`）、其他→`这种产物不能在页面里预览` 与 `下载`；`truncated` 时正文上方 `InlineAlert` `内容较长，只显示前 64 KiB` 带下载；404 `EmptyState` `没有这个产物`/`返回产物列表`，其他错误 `读取产物失败：{原因}` 与重试。

- [ ] **Step 5: 写会话接入与工单页测试（先红）**

`transcript-v3.test.ts` 加 `attaches the artifact id to create_view calls`：`tool.result.result.artifact_id` 是字符串时 `entry.artifactId` 等于它；缺字段或非字符串时为 `null`。`TicketDetailPage.test.tsx` 加 `renders a view card under the create_view call`（历史里工具条下出现视图卡并能提交）与 `renders a view card from a live create_view result`（流里的 `tool_result` 挂同一张卡）；既有 `ArtifactList.test.tsx` 加 `labels dashboards as 看板 and pinned as 已置顶`、`opens view artifacts in the artifact page`（视图产物抽屉显示 `在产物页打开`，不显示 `<pre>`）。`more.test.tsx` 既有分组用例断言 Agent 组有 `产物`、`href` 是 `/more/artifacts`、受保护路由含 `more/artifacts`；`layout.test.tsx` 断言侧边栏含 `产物`。

写完先跑红：

```bash
cd web && pnpm test --run src/features/console/conversation/transcript-v3.test.ts src/features/tickets/TicketDetailPage.test.tsx src/features/tickets/ArtifactList.test.tsx src/routes/more/more.test.tsx src/components/layout/layout.test.tsx > /tmp/m90-t6-red3.log 2>&1; echo $?; cd ..; tail -n 5 /tmp/m90-t6-red3.log
```

Expected：非 0（`artifactId` 与视图卡尚未接入；`ArtifactList`/路由/导航的既有用例此时应仍通过）。

- [ ] **Step 6: 实现会话接入与工单页改动**

`transcript-v3.ts`：`ConversationEntry` 的 `tool` 分支加 `artifactId: string | null`，新增导出 `artifactIdOf(tool: ToolView): string | null`，取值表达式写死为 `const body = tool.result; const value = isRecord(body) && isRecord(body.result) ? body.result.artifact_id : null`，只收非空字符串；`toEntry` 填 `artifactId`。`ConversationView`：props 加 `ticketEnded?: boolean`；`ConversationEntryView` 也收这个 prop（普通分组与 `SupersededFold` 两个调用点都传）并在递归渲染子代理时继续传；`tool` 条目带 `artifactId` 时在工具条下方渲染 `<ViewArtifactCard artifactId={entry.artifactId} ticketEnded={ticketEnded ?? false} />`；渲染 `LiveTurnView` 时传同一个值。`LiveTurnView`：props 加 `ticketEnded?: boolean` 并传给内部的 `LiveRunView`；`LiveRunView` 也加 `ticketEnded?: boolean`，工具段用 `liveToolView` 后若 `artifactIdOf(view)` 非空，在 `ToolCallBar` 下方渲染同一张卡，递归渲染子运行的 `LiveRunView` 时继续传；不加 `artifact_created` 段，不改 `sse.ts`/`live-turn.ts`。`TicketDetailPage` 复用页面里已算出的 `ended`（`detail.data?.ticket` 的 `status` 属于 `ENDED_STATUSES`；不要写 `detail.ticket`），作为 `ticketEnded` 传给 `ConversationView`；`ENDED_STATUSES` 现在是 `TicketDetailPage.tsx` 的文件内常量，把它提到 `features/tickets/format.ts` 导出、两个页面 import，`ArtifactDetailPage` 才能用同一组三态（`done`/`failed`/`cancelled`）；右栏 `ArtifactList` 保留，视图产物抽屉改成 `在产物页打开`，徽章统一 `已置顶`，截断提示统一 `内容较长，只显示前 64 KiB`，下载失败统一 `下载产物失败：{原因}`。

- [ ] **Step 7: 加路由、导航与 mock**

`routes/router.tsx` 在 `more/watchdog` 之后加 `{ path: 'more/artifacts', element: page(<ArtifactsPage />) }` 与 `{ path: 'more/artifacts/:artifactId', element: page(<ArtifactDetailPage />) }`。`nav.ts` 的 `secondaryNav` 在「值守」之后加 `{ to: '/more/artifacts', label: '产物', icon: FileBox, description: '报告、表单、看板与外置的大输出', group: 'agent' }`。`test-utils.tsx` 的 mock 把 `artifactMatch` 的正则从 `(\/content|\/pin)?` 改成 `(\/content|\/pin|\/submit)?`，并在 `POST` + `/submit` 分支返回 `200` 与 `{ artifact_id, ticket_id, submitted_at }`，把请求体记到 `state` 供卡片测试断言；不校验视图，不影响既有产物用例。

- [ ] **Step 8: 跑绿**

```bash
cd web && pnpm gen:api && pnpm typecheck > /tmp/m90-tc.log 2>&1; echo $?; cd ..
cd web && pnpm test --run > /tmp/m90-t6.log 2>&1; echo $?; cd ..; tail -n 5 /tmp/m90-t6.log
```

Expected：`typecheck` `0`；`pnpm test --run` `0`，M90 新增用例全绿，既有用例不回归。把三次红跑（`red1`–`red3`）与本次绿跑的退出码一并记进台账。

- [ ] **Step 9: 提交**

```bash
git add web/src/features/artifacts web/src/features/console/conversation/transcript-v3.ts web/src/features/console/conversation/ConversationView.tsx web/src/features/console/conversation/LiveTurnView.tsx web/src/features/tickets/TicketDetailPage.tsx web/src/features/tickets/ArtifactList.tsx web/src/features/tickets/workspace-api.ts web/src/features/tickets/format.ts web/src/api/keys.ts web/src/components/layout/nav.ts web/src/routes/router.tsx web/src/features/console/test-utils.tsx web/src/features/console/conversation/transcript-v3.test.ts web/src/features/tickets/ArtifactList.test.tsx web/src/features/tickets/TicketDetailPage.test.tsx web/src/routes/more/more.test.tsx web/src/components/layout/layout.test.tsx
git commit -m "feat(m90): 视图卡、产物页与会话接入"
```

---

### Task 7: 全量验收、文档与台账（spec T7）

**Files:**
- Create: `docs/v3/ledgers/m90-artifacts-genui.md`
- Modify: `docs/v3/02-系统架构.md`、`03-数据模型.md`、`04-接口与事件协议.md`、`05-运行时与代理定义.md`、`11-前端指挥台.md`、`12-实施路线与里程碑.md`、`13-决策记录.md`、`14-现行接口清单.md`
- 不改 `docs/v3/01-总览与范围.md`；不新增迁移、表、配置、错误码

**Interfaces:**
- Consumes: T1–T6 的全部改动、Task 0 的基线、M89 台账的 capability 基线。
- Produces: 通过的 12 §4 十项门禁、同步后的 v3 文档、台账、`v3-m90` tag。

- [ ] **Step 1: 按 spec §7 跑全量验收**

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
grep -rnE "dangerouslySetInnerHTML|<img|<iframe|<a |href=|<object|<embed" web/src/features/artifacts/view --include='*.ts' --include='*.tsx' --exclude='*.test.ts' --exclude='*.test.tsx'; echo $?
git diff --check; echo $?
```

Expected：pytest 与 Task 0 基线相比不新增失败；OpenAPI/前端五步退出码 `0`；`frontend_smoke`、`agent_smoke`、`orchestrator_smoke` 末行 `SMOKE OK`；两个评测 `EVAL OK`，capability 通过率不低于 §0 抄下的 M89 基线；断链 `BROKEN 0`；安全扫描排除 `*.test.ts`/`*.test.tsx` 后退出码 `1`；`git diff --check` 退出码 `0`。

- [ ] **Step 2: 同步 v3 文档**

按 spec §9 先核对升格时已预填的条目，只补缺口；不重写已同步段落：

- 03 §8.2 已写视图 `mime`、`view_version`、`submissions`、`last_submitted_at`，§9.2 已写「来源侧的附加元数据，与 §8.2 的产物 meta 无关」；04 §2.3 已写详情「含 `meta`」、§2.5 已有 M90 行、§3 已无 `artifact_created`、§4.2 已注明 M90 视图也走 `agent_event`；02 §4.1/§6.3、05 §3.2/§7、11 §5/§6.1/§6.3、11 §9 的产物行、12 §2 也已预写。
- 用 `grep -n` 按小节标题与关键词逐项核对；不一致才改。05 §6.1 保留「104/103 + 各里程碑增量（…M90 后 111/110）」的现成口径，不要改成裸「当前 111/110」。
- 只补缺口：14 §6.15 的 submit 行与 `88/191` 由 T4 按 Task 0 实测值 +1 维护；13 §8.178 已记 M90 的五项裁决，若缺「M90 为提交记录扩展详情 wire」才在 §8 末尾追加一条，写明只加 `ArtifactDetailOut.meta`、列表不加、`meta` 含内部键、04/03 已同步。
- §11 契约名落点：`VIEW_MIME`/`ViewSpec`/`validate_view`/`view_kind`/`view_fields`/`view_json_schema`/`validate_submission`/`render_submission`/`ViewInvalid` 由 T1 产出，文档在 02 §4.1、03 §8.2、04 §2.5；`ArtifactFormSubmit`/`ArtifactFormSubmitResult` 与 `ArtifactDetailOut.meta` 由 T4 产出，文档在 04 §2.3/§2.5、03 §8.2、13 §8；`keys.agent.artifactsList`、`artifactsListInfiniteQuery`、`usePinArtifact`、`useSubmitArtifactForm`、`downloadName`、`ViewArtifactBody`、`ViewArtifactCard`、`ArtifactChip` 由 T6 产出，文档在 11 §6.3；【表单提交】由 T3 产出，文档在 02 §6.3、05 §7。

- [ ] **Step 3: 写台账**

`docs/v3/ledgers/m90-artifacts-genui.md` 按 [specs/README §4](../specs/README.md) 模板：分支、基点 commit、合并与 tag、spec/plan 链接、T0–T7 表、§0 实测值（签名、四版本、工具/端点/错误/表/配置计数）、pydantic 版本、capability 基线与 M90 通过率、`submit` 的写消息顺序与并发覆盖窗口、已知失败、Rulings（接缝裁决、文案统一、`artifact_created` 只走 WS、安全扫描排除测试、604 图表点边界、T6 红跑→绿跑退出码、计数以 Task 0 实测为准、`LiveRunView` 逐层透传、删工单→`ticket_id` 置 NULL→400、T7 文档只补缺口）。

- [ ] **Step 4: 提交文档与台账**

```bash
git add docs/v3/02-系统架构.md docs/v3/03-数据模型.md docs/v3/04-接口与事件协议.md docs/v3/05-运行时与代理定义.md docs/v3/11-前端指挥台.md docs/v3/12-实施路线与里程碑.md docs/v3/13-决策记录.md docs/v3/14-现行接口清单.md docs/v3/ledgers/m90-artifacts-genui.md
git commit -m "docs(m90): 台账、契约同步与验收"
```

- [ ] **Step 5: 合并、打 tag、合并后复验**

```bash
git checkout dev
git merge --no-ff artifacts-genui
git tag v3-m90
git branch -d artifacts-genui
git rev-parse --short dev
git show --stat --oneline v3-m90
```

Expected：`merge --no-ff` 生成合并提交；tag `v3-m90` 指向它；分支删除；`dev` 上复跑 T7 Step 1 的关键命令（全量 pytest、OpenAPI 两个 check、前端五步、三个冒烟、两个评测、断链、安全扫描、`git diff --check`）全部通过。不 push。

---

## Self-Review

- **Spec 覆盖**：§0→Task 0；§4.1/§4.3.1/§4.5.2/§4.5.3→T1；§4.3.2/§4.5.1→T2；§4.7→T3；§4.3.3/§4.3.4→T4；§4.5.4/§4.6.2→T5；§4.6.1/§4.6.3/§4.6.4/§4.6.5→T6；§7/§9/§11→T7。§2 的非目标由全局约束与 T7 的 diff 范围守住：无迁移、无新表/错误码/配置/依赖、无 SSE 新事件、无第二个新端点。
- **类型一致性**：T1 产出的 `ViewSpec`/`validate_view`/`validate_submission`/`render_submission` 与 T2、T4 的调用签名一致；T4 的 `ArtifactDetailOut.meta` 与 T6 的 `ArtifactDetailView` 读取一致；T6 的 `FormValue`/`FormValues` 与 T5 的 `initialValues`/`validateValues`/`toSubmitPayload` 一致；key 名统一 `artifactsList`，路径统一 `/more/artifacts` 与 `/more/artifacts/:artifactId`。
- **Review Focus 对照**：五条分别在 T1（loc 路径）、T4/T6（提交消息）、T5（白名单与脚本）、T4/T7（meta 截断与并发窗口）、T6（只靠 `tool_result`）有测试或验收证据；没有一条只有说明没有落点。
- **契约覆盖**：spec §11 的 `VIEW_MIME`/`ViewSpec` 由 T1 落 `views.py`，`ArtifactFormSubmit`/`ArtifactFormSubmitResult` 与 `ArtifactDetailOut.meta` 由 T4 落 `artifacts.py`/`agent_v3.py`，`keys.agent.artifactsList` 等前端符号由 T6 落 `keys.ts`/`features/artifacts`；T7 只核对已预填文档并补缺口。
- **比例**：本计划约 640 行，spec 1462 行；只给签名、测试名、断言值与命令，不含实现正文。
- **已消除的执行歧义**：`put(body: bytes)`/`get→ArtifactDetail`/`update(content, meta)`、`post_user_message` 的 `mentions`、`keys.artifacts` 是函数、Pydantic `loc` 判别段清洗、`ArtifactDetailOut` 只加 `meta`、`artifact_created` 只走 WS、T3 的四个钉死测试文件、`test_tool_catalog` 的四处计数、`web/src/types/api.d.ts` 的再生成、`test-utils.tsx` 的 submit mock、604 图表点边界、安全扫描排除测试文件、计数与版本以 Task 0 实测为准、`LiveRunView` 逐层透传、T7 只补文档缺口。
