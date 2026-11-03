# M18 · Agent 知识库与账号资源查询实施计划

规格见 [specs/2026-09-29-m18-agent-knowledge.md](../specs/2026-09-29-m18-agent-knowledge.md)。行为以规格为准：§4 是设计，§4.6 是内置知识原文，§8 是用户已确认的行为变更。规格没覆盖到的实现细节，由实现者自行决定并写进报告，主 agent 抄进台账。

## 全局约束

- 工作分支 `m18-agent-knowledge`（已从 `refactor/v2` 建立，含规格与本计划）。直接在当前检出上工作，不建 worktree，不 push，不合入 `dev`。
- **开工前置条件**：建分支时，`refactor/v2` 的工作区里有一批未提交的通知相关改动，共 15 个文件，包括 `maa_api/main.py`、`docs/README.md`、`docs/04`、`web/openapi.json`、`web/src/types/api.d.ts`、`NotificationChannels.tsx`。本里程碑也要改其中几个文件。开工前 `git status` 必须干净，这批改动由用户自行提交或处理；主 agent 不得替用户提交、暂存或丢弃它们。
- 后端测试：`.venv/bin/python -m pytest -q`（`python` 不在 PATH 里）。
- 前端：`cd web && pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build`。`node` / `pnpm` 在 `/opt/homebrew/bin`；子环境 PATH 里没有时用绝对路径。
- 改了后端接口后：先运行 `.venv/bin/python scripts/dump_openapi.py`，再 `cd web && pnpm gen:api`，然后 `.venv/bin/python scripts/generate_api_guide.py`。
- 每项变更先写失败测试并确认失败原因（RED），再实现（GREEN）。
- **实现者不要 `git commit`**：并行任务会让索引互相干扰。提交由主 agent 在审核通过、亲自复跑验证之后统一做，提交信息用 Conventional Commits。
- 长输出重定向到文件，只读尾部和退出码。
- 不引入新依赖，frontmatter 用已有的 ruamel-yaml 解析。
- 新错误码必须同时登记到 `ErrorCode` 和 `ERROR_HTTP_STATUS`，并更新条数注释以及断言条数的测试（按代码事实）。
- 本里程碑不给 Agent 任何写知识库的工具。
- 依赖真机、真实内核或真实模型的检查只作补充，结果记入台账，不作为硬性验证。

任务依赖：Task 1 与 Task 4 互相独立，可以并行。Task 2、3、5 都改 `maa_api/agent/runtime.py`，必须按 2 → 3 → 5 串行。Task 6 依赖 1–5，Task 7 依赖 1–5，Task 8 依赖 7。

## Task 1 · 后端：点击坐标统一为设备像素（规格 §4.5）

**产物**：`maa_api/services/device_service.py`、`maa_api/api/routers/atomic.py`、`maa_api/api/routers/device.py`、`maa_api/agent/tools/raw.py`、`maa_api/core/asst.py`、`maa_api/core/asst_protocol.py`，以及对应测试（`tests/agent/test_device_raw_tools.py`、`tests/api/test_device_router.py`；REST click 的既有测试位置按代码事实找）。

- `DeviceManager.click(x, y)` 接收设备像素：先取快照里的 `resolution`，未知时抛 `DEVICE_NOT_CONNECTED`；越界抛 `INVALID_PARAMETER`；然后按 `round(px × 720 / 高)` 换算，再调用内核客户端。MaaCore 只接受 16:9，换算规则与 `ControlScaleProxy` 保持一致。
- `swipe` / `long_press` 在分辨率已知时做越界校验，未知时照常执行。
- REST `/api/device/click` 改为经 `DeviceManager.click`，参照 `device.py` 的 `_run_atomic`，保留 `operation_lock` 与 `force` 语义。错误声明补上 `DEVICE_NOT_CONNECTED`、`INVALID_PARAMETER`、`SERVICE_UNAVAILABLE`；`/api/device/swipe`、`/api/device/long_press` 补 `INVALID_PARAMETER`。响应照旧回显调用方传入的坐标。
- Agent 的 `click` / `swipe` / `long_press` 工具描述写明「使用 get_screenshot 返回图像的像素坐标」。
- `asst.py`、`asst_protocol.py` 的 click 注释改为：接收 MaaCore 基准坐标，由内核按「高 / 720」缩放。
- 测试（Agent 工具与 REST 各一套）：
  - 2560×1440 下 `click(2388, 1280)` 以 `(1194, 640)` 调用内核；
  - 1280×720 下坐标原样传入；
  - 分辨率未知时返回 503 `DEVICE_NOT_CONNECTED`；
  - click、swipe、long_press 越界时返回 400 `INVALID_PARAMETER`；
  - 分辨率未知时 swipe 照常执行；
  - REST click 不再直接调用 `registry.get().click`。
- 运行：`.venv/bin/python -m pytest -q tests/agent/test_device_raw_tools.py tests/api/test_device_router.py`，再加上 REST click 所在的测试文件。
- 提交：`fix(device): take device-pixel click coordinates and scale them for MaaCore`。

## Task 2 · 后端：`wait_pipeline` 与原地打转豁免（规格 §4.2）

**产物**：`maa_api/services/pipeline_runner.py`、`maa_api/agent/tools/pipeline.py`、`maa_api/agent/runtime.py`、`tests/agent/test_pipeline_tools.py`、`tests/agent/test_runtime.py`、`tests/agent/test_tool_catalog.py`。

- 在 `PipelineRunner` 上加公开方法（如 `wait_terminal(pipeline_id, timeout_seconds)`）：用自己的 `_session_factory` 开短会话，约每秒轮询一次；如果有现成的状态事件可以挂，也可以改用事件。到终态或超时就返回流水线（含任务），未知 id 抛 `PIPELINE_NOT_FOUND`。工具只调用这个方法，**不使用 `ctx.db_session` 等待**。
- 工具 `wait_pipeline`：组 `pipeline`，风险 `SAFE`，`read_only=True`。参数 `pipeline_id`，`timeout_seconds` 默认 300、范围 5–600。返回 `{finished, waited_seconds, pipeline}`，其中 `pipeline` 复用 `_pipeline_wire`。描述用规格 §4.2 的原文。
- runtime：一批调用如果只含 `wait_pipeline`，就不更新原地打转计数（`last_signature` / `repeats` 保持不变，或者重置，由实现者决定并写进报告）。
- 测试：
  - 已是终态时立即返回；
  - 等到完成（用 fake runner 或 SQLite fixture 在后台把状态改成 completed）；
  - 超时返回 `finished: false`；
  - 未知 id 报错；
  - handler 执行期间没有访问 `ctx.db_session`；
  - 任务被取消时正常退出；
  - 连续三批只含 `wait_pipeline` 的调用不会触发 `stuck_loop`，而三批相同的 `get_pipeline` 仍会触发；
  - 工具目录多出 `wait_pipeline`，且只读、SAFE。
- 运行：`.venv/bin/python -m pytest -q tests/agent`。
- 提交：`feat(agent): add wait_pipeline and exempt it from stuck-loop detection`。

## Task 3 · 后端：`trigger_screencap` 对内置 Agent 隐藏，状态摘要加当前时间（规格 §4.3、§4.4）

**产物**：`maa_api/agent/registry.py`、`maa_api/agent/tools/raw.py`、`maa_api/agent/runtime.py`、`tests/agent/test_runtime.py`、`tests/agent/test_registry_policy.py` 或 `tests/agent/test_tool_catalog.py`、`tests/agent/test_incidents.py`。

- `ToolDefinition` 增加 `model_visible: bool = True`，并做类型校验（参照 `read_only`）；`trigger_screencap` 设为 `False`。
- `_tool_schemas` 过滤掉 `model_visible=False` 的工具；`_execute_tool_batch` 遇到这类工具，返回 `TOOL_NOT_FOUND`，消息为「内置 Agent 不提供该工具，请用 get_screenshot」，不进 ConfirmationService。
- `INCIDENT_ALLOWED_TOOLS` 移除 `trigger_screencap`。`scripts/agent_smoke.py` 按常量断言，不需要改。
- `_status_summary` 的结果在最前面加 `now`：`datetime.now().astimezone().isoformat()`。自定义的 `context_provider` 分支也要补上 `now`，写法由实现者决定。
- 测试：
  - 模型 schema 里没有 `trigger_screencap`；
  - 伪造调用会被拒绝，且不产生审计；
  - 诊断白名单里没有它；
  - REST `GET /api/agent/tools` 仍列出它，`POST /api/agent/tools/trigger_screencap/invoke` 仍可调用；
  - 状态摘要第一项是带时区偏移的 `now`。
- 运行：`.venv/bin/python -m pytest -q tests/agent tests/api/test_agent_router.py`。
- 提交：`fix(agent): hide trigger_screencap from the model and stamp the status summary`。

## Task 4 · 后端：知识库数据层、服务与内置条目（规格 §4.1、§4.6）

**产物**：
- `maa_api/db/models.py`（`AgentKnowledge`）；
- `maa_api/db/migrations/versions/0014_agent_knowledge.py`；
- `maa_api/db/repositories/agent_knowledge.py`；
- `maa_api/agent/knowledge/__init__.py`（`KnowledgeService`）；
- `maa_api/agent/knowledge/builtin/{account-resources,screen-home,screen-depot,gacha-conversion,glossary}.md`；
- `maa_api/domain/errors.py`（`KNOWLEDGE_NOT_FOUND` 404、`KNOWLEDGE_BUILTIN_READONLY` 409、`KNOWLEDGE_LIMIT_EXCEEDED` 409）；
- 测试：`tests/agent/test_knowledge_service.py`、`tests/db/test_agent_knowledge_migration.py`（参照既有的 migration 测试）。

- 表结构、覆盖语义、上限按规格 §4.1。覆盖行只存与内置不同的字段；若没有任何不同且处于启用状态，就删除该行。
- 内置条目用 `importlib.resources` 读取，ruamel-yaml 解析 frontmatter；启动时加载一次，id 重复或字段不合法时 fail loud。正文逐字照搬规格 §4.6。
- `KnowledgeService` 对外提供：
  - `list_entries()`、`get_entry(id)`、`put_entry(id, payload)`、`delete_entry(id)`；
  - `read_for_agent(id)`：未启用或未知时抛 `KNOWLEDGE_NOT_FOUND`；
  - `render_context()`：返回注入 system 消息的文本，格式按规格 §4.1，索引按 kind、id 排序。
- 测试：
  - 内置、覆盖（NULL 字段继承）、自定义、停用、恢复默认；
  - 与内置同 id 的自定义行被当作覆盖行；
  - 删除未修改的内置条目，或修改内置条目的 kind，返回 409；
  - 第 51 个启用条目，以及 glossary + profile 合计超过 3,000 字，返回 409 `KNOWLEDGE_LIMIT_EXCEEDED`；
  - `render_context` 的段落顺序与索引格式；
  - 内置文件全部可解析，且各自不超过上限；
  - migration 升级与降级。
- 运行：`.venv/bin/python -m pytest -q tests/agent/test_knowledge_service.py tests/db tests/domain`。
- 提交：`feat(agent): add the knowledge base store with built-in entries`。

## Task 5 · 后端：知识库 REST、`read_knowledge`、上下文注入与提示词（规格 §4.1、§4.3）

**产物**：
- 知识库路由：放在 `maa_api/api/routers/agent.py` 或新文件，实现者自定；
- `maa_api/agent/tools/knowledge.py`、`maa_api/agent/tools/__init__.py`；
- `maa_api/main.py`（装配 `app.state.knowledge_service`）；
- `maa_api/agent/runtime.py`；
- 测试：`tests/api/test_agent_knowledge_router.py`（新增）、`tests/agent/test_runtime.py`、`tests/agent/test_tool_catalog.py`、`tests/agent/test_incidents.py`。

- 四个端点按规格 §4.1 的表格实现，全部 `require_auth`，`error_responses` 声明全部新错误码。写操作记一条服务日志。
- 工具 `read_knowledge`：组 `knowledge`，风险 `SAFE`，`read_only=True`，参数 `entry_id`。加入 `INCIDENT_ALLOWED_TOOLS`。
- `build_model_context` 增加 `knowledge_context` 参数，system 消息按「提示词 → 知识库段 → 状态摘要」拼接。`_run_turn` 与生成状态摘要同时生成知识库段；生成失败时写「知识库暂不可用」并记 warning，不中断本轮。
- `SYSTEM_PROMPT` 按规格 §4.3 加入「工作方法」一段，并在「安全数据边界」一段补一句。
- 测试：
  - 四个端点的成功与错误路径、鉴权、OpenAPI 里声明了错误码；
  - system 消息的段落顺序；
  - 知识库读取失败时降级；
  - 诊断会话同样注入知识库段，且可以调用 `read_knowledge`；
  - 提示词包含「工作方法」与 `read_knowledge`、`wait_pipeline` 这两个名字；
  - 工具目录共 42 个，并含 `knowledge` 组。
- 运行：`.venv/bin/python -m pytest -q tests/agent tests/api`。
- 提交：`feat(agent): expose the knowledge base to the runtime and REST`。

## Task 6 · 场景测试：问「我有多少合成玉」（规格 §6）

**产物**：`tests/agent/test_account_resources_flow.py`（新增）。优先复用 `tests/agent/test_runtime.py` 与 `scripts/agent_smoke.py` 里现成的 Fake LLM 和 FakeAsst。

- Fake LLM 按以下顺序发工具调用：`read_knowledge("account-resources")` → `submit_pipeline([StartUp])` → `wait_pipeline` → `back_to_home` → `get_screenshot`，最后给出文字回答。
- FakeAsst 让 StartUp 正常完成，fake 设备返回一张 16:9 图片。
- 断言：
  - 全程没有 `confirmation_required` 事件；
  - `wait_pipeline` 返回 `finished: true`；
  - 最后一次模型请求带 `image_url`；
  - 每次请求的 system 消息里都有 `account-resources` 的索引行和 `now`；
  - 流水线来源是 `agent`，并关联到该会话。
- 运行：`.venv/bin/python -m pytest -q tests/agent/test_account_resources_flow.py`。
- 提交：`test(agent): cover the account resources flow end to end`。

## Task 7 · 生成物同步

**产物**：`web/openapi.json`、`web/src/types/api.d.ts`、`docs/14-开放API接入指南.md`。

- 依次运行 `.venv/bin/python scripts/dump_openapi.py`、`cd web && pnpm gen:api`、`.venv/bin/python scripts/generate_api_guide.py`。
- 检查 diff，只应出现以下变化：知识库四个端点、新增错误码、`/api/device/click|swipe|long_press` 的错误声明和摘要。docs/14 里 click 示例的坐标说明要与新语义一致。
- 提交：`chore(api): regenerate schema snapshots and the API guide`。

## Task 8 · 前端：知识库设置页与工具中文名（规格 §4.1「设置页」）

**产物**：
- `web/src/features/settings/sections.ts`、`web/src/features/settings/SettingsPage.tsx`；
- `web/src/features/settings/knowledge/`：列表、编辑页、API hooks、格式化工具与测试；
- `web/src/routes/router.tsx`；
- `web/src/features/agent/format.ts`。

- 新增分区 `knowledge`（「Agent 知识库」，摘要「操作剧本、游戏知识与用户档案」，图标 `BookOpen`，scope 为 server），排在 `agent` 之后。分区页用专用组件，不走设置 schema。
- 路由：`more/settings/knowledge/new` 与 `more/settings/knowledge/:entryId` 指向整页编辑器，形态参照 `ScheduleEditorPage`；删除与恢复默认的二次确认参照现有设置页的确认模式。
- 列表按类型分组，显示来源徽标与启用开关。编辑页字段、字数上限、索引行预览，以及内置条目的 kind 不可改，都按规格实现。
- `TOOL_LABELS` 补 `read_knowledge: '查阅知识库'`、`wait_pipeline: '等待流水线结束'`。
- 移动优先，遵循 docs/09 §8。
- 测试：
  - 列表分组与徽标；
  - 启用开关发出 PUT；
  - 新建时的校验与上限提示；
  - 编辑后保存；
  - 恢复默认发出 DELETE；
  - 删除自定义条目；
  - 409 错误的展示；
  - Agent 页显示新工具的中文名。
- 运行：`cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build`。
- 提交：`feat(web): edit the agent knowledge base in settings`。

## Task 9 · 文档同步（规格 §7）

**产物**：docs/11、docs/04、docs/05、docs/03、docs/09、docs/12、docs/README、docs/13、docs/ENVIRONMENT.md。

- 逐条落实规格 §7。
- docs/13 §8 新增一节 M18：规格 §8 的四条行为变更（注明用户 2026-09-29 的裁决），以及实施中的实现裁决。
- docs/ENVIRONMENT.md 的「MaaCore 内核」一节追加：`AsstAsyncClick` 接收 MaaCore 基准坐标，由内核按「高 / 720」缩放。证据写 v6.17.5 的 `ControlScaleProxy::click` 与 `asst.log` 中的 `Click with scaled coordinates` 日志。
- docs/11 §10 把「注入自定义 task 后用 `submit_pipeline` 调用」标为第 3 步才实现。
- 提交：`docs: record the M18 knowledge base and click coordinate semantics`。

## Task 10 · 验收（主 agent 亲自跑）

- `.venv/bin/python -m pytest -q`
- `.venv/bin/python scripts/dump_openapi.py --check`、`.venv/bin/python scripts/generate_api_guide.py --check`
- `cd web && pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build && cd ..`
- `.venv/bin/python scripts/api_smoke.py`、`.venv/bin/python scripts/agent_smoke.py`、`.venv/bin/python scripts/frontend_smoke.py`，末行都是 `SMOKE OK`。
- 补充检查（不阻塞，证据记入台账）：
  - 真机加真实模型问「我有多少合成玉」「我还有多少抽卡资源」，记录工具轨迹，核对读数与截图是否一致；
  - 真机点击仓库入口，确认坐标换算正确；
  - 真机核对仓库页签与凭证位置，据此更新内置的 `screen-depot`，并同步规格 §4.6。
- 完成后用 `git merge --no-ff` 合并回 `refactor/v2`，打 tag `v2-m18`。把 progress 连同「Rulings I made」归档到 `docs/superpowers/ledgers/2026-09-29-m18-agent-knowledge.md` 并提交。
