# M13 内置 Agent 实施计划

> **执行方式：** 由 superpowers:subagent-driven-development 分任务实施和审查；所有测试按 TDD 编写。

**目标：** 交付使用 DeepSeek V4.1 Flash 的内置对话 Agent、流水线/Copilot/schedule 执行和 MAA 故障安全自愈。

**架构：** 复用 M11 的 ToolRegistry、ConfirmationService、AgentMessage/Audit 和统一 pipeline queue。OpenAI-compatible SSE 客户端只负责协议；AgentRuntime 控制会话、上下文和工具循环；failure incident runtime 以持久 key 去重并限制工具范围。

**技术栈：** FastAPI StreamingResponse、httpx、Fernet (`cryptography` 已在锁文件中)、SQLModel/Alembic、React/TypeScript/Vitest。

**规格：** `docs/superpowers/specs/2026-09-26-m13-agent-runtime.md`。

## 全局约束

- 工作分支 `m13-agent` 从 `refactor/v2` 建立；直接当前检出工作，不创建 worktree，不 push，不合入 `dev`。
- 所有 native MaaCore 命令只通过 CoreClient / PipelineRunner；Agent 工具只调应用服务，不开放任意 shell/ADB。
- 普通 Agent 工具调用必须经过 ToolRegistry、PolicyEngine、ConfirmationService 与 agent_audit；禁止恢复 MCP。
- Agent 自动诊断默认开启且每 incident 一次；诊断 runtime 的 allowlist 是唯一自动自愈工具来源，原子操作授权与消耗/破坏确认不得绕过。
- LLM API Key 的 SQLite 覆盖值用 `MAA_LLM_ENCRYPTION_KEY` Fernet 加密；密钥不得写入数据库或日志。
- Copilot 固定使用已校验 `resource_asset` 的 asset id；MaaCore 参数为 `Copilot` + `filename` + `formation`，不得降级成 Fight。
- 每项变更先写失败测试、确认失败原因、实现、复跑覆盖测试；每个子任务结束提交，并经独立 diff review。
- 最终由主 agent 亲自重跑对应验证命令和后端测试；长输出写文件，只读取尾部与退出码。
- 若派发子智能体，模型固定 `gpt-6-luna`、思考强度 `max`；实施者不得派发自己的子智能体。

---

### Task 1：LLM 密钥加密与 OpenAI-compatible 客户端

**文件：** `maa_api/agent/secrets.py`（新）、`maa_api/agent/llm.py`、`maa_api/settings.py`、`maa_api/services/setting_service.py`、`maa_api/services/settings_schema.py`、`maa_api/domain/errors.py`、`maa_api/api/routers/settings.py`、`pyproject.toml` / `poetry.lock`、`config.template.yaml`、`docs/05-API规范与路由清单.md`、`web/openapi.json`、`web/src/types/api.d.ts`（生成）、`tests/agent/test_llm.py`（新）、`tests/services/test_setting_service.py`、`tests/domain/test_errors.py`、`tests/api/test_settings_router.py`。

**接口：** `OpenAICompatibleClient(*, base_url: str, api_key: str, http_client: httpx.AsyncClient | None = None, timeout_seconds: float = 120.0)`；`stream_chat_completion(model, messages, tools)` 输出原始 OpenAI SSE chunk dict。`encrypt_api_key(api_key, key)` / `decrypt_api_key(value, key)` 使用字符串 Fernet key，数据库密文前缀为 `enc:v1:`。

- [ ] 先加密/解密 roundtrip、篡改/错 key、缺密钥写入拒绝、DB refresh 解密、旧明文迁移的失败测试并验证 RED。

```python
def test_api_key_ciphertext_round_trips_and_rejects_wrong_key():
    key = Fernet.generate_key().decode("ascii")
    other_key = Fernet.generate_key().decode("ascii")
    value = encrypt_api_key("sk-test-value", key)

    assert value.startswith("enc:v1:")
    assert "sk-test-value" not in value
    assert decrypt_api_key(value, key) == "sk-test-value"
    with pytest.raises(LLMEncryptionKeyInvalid):
        decrypt_api_key(value, other_key)
```

- [ ] 先对 MockTransport 增加测试：增量文本/tool call SSE、最终 usage、非 2xx、无效 JSON、超时及携带 data URL 图片的请求形状；验证 RED。

用 `httpx.MockTransport` 返回两段文本 chunk、一个 finish/usage chunk 与 `[DONE]`；断言迭代器按序返回 chunk、使用量精确保留、非 2xx 映射 `LLM_REQUEST_FAILED`、错误的 JSON/SSE 映射稳定 LLM 错误。

- [ ] 用 Fernet 实现版本化密文存储；Settings API 对敏感值继续只返回 `***`；缺 key 时拒绝新增 DB 密钥；遇到旧明文在 key 已配置时改写密文；错 key 保持服务启动但清空可用 key 并记录状态错误。
- [ ] 为错 key 定义 `LLM_ENCRYPTION_KEY_INVALID`，HTTP 映射为 503；实现 httpx SSE 行解析，识别 `data: [DONE]`，不把请求认证头/响应原始密文写日志；设置默认 endpoint/model，保留三个 OpenAI-compatible 设置项。
- [ ] 将 `cryptography` 明确列为项目直接依赖并更新 lockfile，不升级无关包；在 docs/05 的规范错误码表加入密钥错误及可操作说明。
- [ ] 设置 `PUT /api/settings` 的声明错误列表加入 `LLM_ENCRYPTION_KEY_INVALID`；按 AGENTS.md 先运行 `.venv/bin/python scripts/dump_openapi.py`，再运行 `cd web && pnpm gen:api`，提交生成的 OpenAPI/TS 契约。
- [ ] 运行 `.venv/bin/python -m pytest -q tests/agent/test_llm.py tests/services/test_setting_service.py tests/api/test_settings_router.py`，再提交 `feat(agent): add encrypted DeepSeek-compatible client`。

### Task 2：AgentRuntime、会话 SSE 与能力自检

**文件：** `maa_api/agent/runtime.py`、`maa_api/agent/confirmation.py`（事务回调扩展）、`maa_api/agent/registry.py`、`maa_api/agent/tools/*.py`（只补只读元数据）、`maa_api/api/routers/agent.py`、`maa_api/db/repositories/agent.py`（如需会话 seq 辅助）、`tests/agent/test_runtime.py`、`tests/agent/test_confirmation_service.py`、`tests/api/test_agent_router.py`。

**接口：** `AgentRuntime.stream_message(session_id, content, request)` 产生具名 SSE 事件；`acquire_session(session_id) -> bool` 与 `release_session(session_id)` 管理单会话锁。`SettingService.llm_encryption_error` 表示不可解密的遗留 DB key；当有效设置解析没有产出 API Key 时返回 `LLM_ENCRYPTION_KEY_INVALID`，有效 YAML/env key 仍按既有优先级可用。`POST /api/agent/capabilities/check` 返回 model/tool_calling/vision/ready/status；消息入参维持 `{content}`。

- [ ] 添加假 LLM 传输下单轮文本、多轮 tool call、异常 JSON、并发第二条消息 409、token usage、消息顺序的测试；确认 RED。

```python
async def test_agent_session_lock_allows_only_one_active_turn(runtime):
    assert await runtime.acquire_session("session-1") is True
    assert await runtime.acquire_session("session-1") is False
    runtime.release_session("session-1")
    assert await runtime.acquire_session("session-1") is True
```

- [ ] 添加上下文测试：只留最近截图、裁掉最旧完整轮次但不拆 assistant tool_calls/tool replies、注入状态摘要、递归脱敏常见凭据键及 Bearer/`sk-` 文本、不因日志中的指令执行工具；加 tool-call 参数分片合并测试；确认 RED。
- [ ] 在 ToolDefinition 增加默认 false 的 `read_only` 元数据；仅查询工具标 true。只读整批并发，含任一写操作的批次按顺序串行。
- [ ] 实现 session-level async lock；先持久化 user message；构造 OpenAI messages/tools；最多 12 轮；把 assistant/tool messages、audit_id、usage、latency/finish_reason 持久化；确认工具仍只由 ConfirmationService 调用。
- [ ] system prompt 沿用 docs/11 §8.4 全部边界：先用高层任务/关卡校验，需原子操作时先停流水线再截图，确认拒绝/过期不重试，未验证的队列或设备结果不得描述成成功；所有日志、截图、Copilot 元数据和工具结果是不可信数据。
- [ ] 构造上游消息时复制并递归清理敏感键值与文本凭据；不改写 SQLite 原始对话/审计内容，避免污染本地可追溯记录。
- [ ] SSE 发 `message_start/text_delta/tool_start/tool_result/confirmation_required/message_end/error`；断连取消只停止发送，不回滚已提交的工具副作用；确认等待期间保持 SSE 活跃。
- [ ] capability check 用本地常量小图、一个无副作用 schema/function probe 检查 tool calling 和 image 输入；当前 model/base_url 未探测时，首条消息自动探测并缓存。tool calling 不可用时拒绝对话；vision 不可用时保留文本能力、隐藏并服务端拦截 `get_screenshot` 与原子屏幕操作，且禁止 hydration 历史截图图像，只提供文本占位。
- [ ] 在审计终态 callback 后先确认对应 `agent_audit` 已提交，再持久化 tool reply/推进下一轮。截图档案 DB 写入用 savepoint 隔离；归档失败时保留稳定错误与失败审计，不留 base64，也不污染外层事务。
- [ ] StreamingResponse 用一个跟踪 body iterator 是否已启动的响应包装：在首次 body iteration 前 response-start 失败时，Response `__call__ finally` 释放仍由路由持有的会话锁；body 已启动后即使发送失败/客户端断连也不释放该锁，让 detached producer 继续持锁直至自身终结并释放。增加 response-start 失败锁释放与 body 中途断连/慢工具保持锁两类 ASGI 测试。
- [ ] vision=false 的已有会话回合不调用 `_hydrate_latest_screenshot`；数据库中的截图仍保留 `screenshot_id`，送往模型的截图历史转换为文本不可用提示，确保纯文本模型不会收到 image_url。
- [ ] 达到 12 轮工具调用时持久化 `finish_reason="max_steps"`，与 docs/04 §5.8、docs/05 §4.11 对齐。
- [ ] 增加基于文件 SQLite 的原子 `click` / 会话授权测试，覆盖 runtime 带 policy db session 调用 ConfirmationService 后的真实确认与审计，不用共享 `StaticPool` 单连接制造并发。
- [ ] 扩展 `ConfirmationService.invoke` 可选事务 callbacks：`on_audit_prepare(session, audit_id, result)` 在同一终态事务中把大结果转为安全持久化结果；它可抛 `AppError` 将审计改记为 `FAILED`。既有 `on_audit_terminal` 仍在 commit 前写幂等快照；新增 `on_audit_committed(audit_id, status, response_body)` 只在 commit 成功后通知 runtime。为这三种顺序/失败语义增加 ConfirmationService contract tests。
- [ ] 运行 Agent runtime/API 覆盖测试并提交 `feat(agent): stream built-in agent conversations`。

### Task 3：Copilot 任务类型与 MaaCore 投递

**文件：** `maa_api/domain/task.py`、`maa_api/services/resource_service.py`、`maa_api/services/pipeline_runner.py`、`maa_api/main.py`（注入资源解析器）、`maa_api/api/routers/tasks.py`、`web/openapi.json` / `web/src/types/api.d.ts`（生成）、`README.md`、`docs/README.md`、`docs/11-Agent模块设计.md`、`docs/13-决策记录.md` §8、`tests/services/test_agent_resource_service.py`、`tests/services/test_pipeline_runner.py`、`tests/api/test_tasks_router.py`、`tests/domain/test_task_schema.py`。

**接口：** `CopilotInput(name="Copilot", asset_id: str, formation: bool = False)` 加入 `TaskInput` union 和任务 schema；`ResourceService.resolve_copilot_path(asset_id) -> Path` 校验 kind/schema/checksum 与源路径后生成唯一受控 snapshot；`release_copilot_path(path)` 在执行终态清理 snapshot。snapshot 位于 `resource/agent-assets/copilot/runs/<uuid>/operation.json`，run 目录 mode `0700`；读取源资产时不得跟随任何路径组件上的 symlink。

- [ ] 添加 TaskInput union、API task schema、schedule template 接受 CopilotInput 的测试；确认 RED。

```python
payload = PipelineCreate(tasks=[CopilotInput(name="Copilot", asset_id="asset-1", formation=False)])
schedule = ScheduleWrite(name="copilot", cron="0 9 * * *", template=payload.tasks)
assert schedule.template[0].name == "Copilot"
```

- [ ] 添加 resolver 测试覆盖外置文件与数据库 inline 内容的 per-run snapshot、缺失 id、非 Copilot kind、格式错误 id、路径穿越、任一路径祖先 symlink；确认 RED。
- [ ] FakeAsst runner 测试断言 CoreClient 收到 type `Copilot` 与 `{filename: str(absolute_path), formation: bool}`，并覆盖 core 拒绝/资产删除错误进入稳定 FAILED 状态；确认 RED。
- [ ] 实现新 Pydantic task 类型；持久化 params 保留 asset_id；只在向 CoreClient append 边界通过注入 ResourceService 解析参数；使用原 Task/Pipeline/Queue/schedule 顺序与优先级。
- [ ] 所有 inline/外置作业均复制为每次运行独立 snapshot，禁止 Core 直接读取可变 asset 文件。snapshot 父目录使用 `0700`；PipelineRunner 在任务/流水线 terminal、失败、取消及 shutdown finally 中释放 snapshot。根目录或路径组件 symlink、复制失败、checksum 变更均在 MaaCore append 前失败。
- [ ] 扩充任务标签与 schema 示例，运行三组目标测试并提交 `feat(agent): queue validated Copilot tasks`。
- [ ] 按 AGENTS.md 先运行 `.venv/bin/python scripts/dump_openapi.py`，再 `cd web && pnpm gen:api`；更新 README 决策速查表、docs/11 Copilot 章节和 docs/13 §8 M13 实施记录，不得留下“Copilot runner 暂缓”的现行文案。

### Task 4：失败事件自动诊断与安全恢复

**文件：** `maa_api/agent/incidents.py`（新）、`maa_api/agent/runtime.py`（受限入口）、`maa_api/agent/confirmation.py`（只在需要 incident caller metadata 时）、`maa_api/services/pipeline_runner.py`、`maa_api/core/supervisor.py` / `maa_api/main.py`、`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`maa_api/services/setting_service.py`（boolean validation）、`config.template.yaml`、`maa_api/db/models.py`、`maa_api/db/repositories/agent.py`、新 Alembic migration、`tests/agent/test_incidents.py`（新）、`tests/db/test_agent_incident_migration.py`（新）、`tests/services/test_setting_service.py`。

**接口：** `AgentIncidentService.pipeline_failed(pipeline_id)` 使用持久 key `pipeline:<pipeline_id>`；`core_crashed(generation, record)` 使用 `core:<core_id>:<boot_uuid>:<generation>`，boot UUID 在每次服务启动时新建且整个 lifespan 固定；`AgentSession.incident_key` 为 nullable unique；`agent.auto_diagnose_failures` 默认 true。

**并发/关联决策：** runtime 内用 per-key asyncio lock 降低同进程重复 LLM 请求，DB 唯一键处理重启/并发竞态；唯一键冲突读取并返回既有诊断 session。Core crash 回调发现 PipelineRunner 有 active/currently-claimed pipeline 时只标记 pipeline core-crashed，不建 core incident；回调迟到时，仅对本次 boot 内由该 Runner claim 且以相同 core epoch 失败的 pipeline 归并。旧服务遗留的 RUNNING 行在启动恢复时形成 pipeline incident；不得因 generation 数字重用而吞掉新 core crash。

**Incident 对话：** 创建 `[自动诊断] ...` active session，持久化一条描述失败对象/error code/安全现场摘要的 user message，再调用 `AgentRuntime.run_incident(session_id, request, allowed_tools)`；该入口必须在 `AgentRuntime` schema 构造和实际 dispatch 两处 enforce allowlist：`get_system_status`、`get_device_status`、`get_logs`、`get_screenshot`、`get_pipeline`、`get_queue`、`list_pipelines`、`get_drop_stats`、`stop_pipeline`、`reconnect_device`、`back_to_home`、`trigger_screencap`。其余工具不传给模型，即便模型伪造调用也在 ConfirmationService 前拒绝。允许列表内仍调用正常 ConfirmationService/PolicyEngine；若未来操作需确认，照常等待用户。配置/能力失败保留 incident session，落一条带稳定错误说明的 assistant message 并广播事件，不吞为“未触发”。

若 incident 创建时没有有效模型配置，保存 `model="unconfigured"` 占位和清晰的 assistant 说明。用户修复配置后继续此 incident session 时，如果 stored model 是该占位或为空，就使用当前有效 model/base_url 并持久化更新；正常 incident 的有效模型快照保持不变。

- [ ] 增加 migration/model/repository 测试：旧 session 为 NULL，新 incident key 唯一可读，upgrade/downgrade 保留旧数据；确认 RED。
- [ ] 增加设置测试：`agent.auto_diagnose_failures` 默认 true，可经 `/api/settings` 更新 true/false；`SettingService._validate` 将 metadata type `boolean` 限定为真正 Python bool，不接受 `0/1` 或字符串。config.template.yaml 显示默认开启项。
- [ ] 增加 fake runtime 测试：pipeline 失败同 key 重放只建立一次 session；core idle generation 建立一次；有 active pipeline 时 core crash 归并到 pipeline；关闭开关不调用 client；确认 RED。

```python
first = await incidents.pipeline_failed("pipeline-1")
second = await incidents.pipeline_failed("pipeline-1")
assert first.session_id == second.session_id
assert fake_llm.calls == 1
```

- [ ] 增加 auto-recovery tool allowlist 测试：`get_logs/get_screenshot/get_system_status/stop_pipeline/reconnect_device/back_to_home/trigger_screencap` 可提供；submit/update/restart/click/swipe/resource mutation 不可执行；原子授权与高风险确认继续经过 ConfirmationService；确认 RED。
- [ ] 实现 `agent_session.incident_key` 和 forward-only Alembic migration；创建标题为 `[自动诊断] ...` 的可续聊会话并落 user/assistant/tool messages。
- [ ] 在 PipelineRunner 唯一终态失败路径通知 incident service；core generation 崩溃若无 active pipeline 且无同 generation 的已失败 CORE_CRASHED pipeline 才单独通知；唯一键冲突作为已处理事件；广播 `agent_event` 让前端刷新。
- [ ] 为自动诊断 user message 加入 pipeline/task status、error code 与 summary；注入当前三路错误日志索引摘要，剩余明细使用 get_logs 拉取；已有 crash screenshot 通过 get_screenshot snapshot 引用提供，不重复落 base64。LLM 不可用时仍保留错误会话并说明配置/能力原因。
- [ ] 运行 incident、migration、pipeline 回归测试并提交 `feat(agent): diagnose MAA failures safely`。

### Task 5：Agent 与 Copilot 前端

**文件：** `web/src/features/agent/`（新）、`web/src/routes/router.tsx`、`web/src/components/layout/BottomTabBar.tsx`（如 label/link 需调整）、`web/src/features/settings/SettingsPage.tsx`、`web/src/features/tasks/` 与 `web/src/features/schedules/` 的 Copilot 选择控件、`web/src/types/api.d.ts`（生成）、相应 Vitest 文件。

- [ ] 先补 SSE parser、agent session list/detail、streamed text、tool trace 与 busy/error 状态测试；确认 RED。

```typescript
const parser = new AgentSseParser()
expect(parser.push('event: text_delta\ndata: {"text":"早"}\n\n')).toEqual([
  { event: 'text_delta', data: { text: '早' } },
])
```

- [ ] 先补 pending confirmation/authorization banner、grant countdown/revoke、incident session navigation、capability self-test、auto-diagnose toggle 的交互测试；确认 RED。
- [ ] 先运行 `.venv/bin/python scripts/dump_openapi.py` 和 `cd web && pnpm gen:api` 更新后端 endpoint/schema 类型；生成文件仅由本任务所有。
- [ ] 实现移动优先 `/agent`：左侧/弹层历史列表，消息流、输入框、stream abort、增量 assistant 回复、工具步骤折叠、confirmation 状态与 session grant 操作。恢复页面时从 REST 加载消息和授权状态。
- [ ] 设置页增加 DeepSeek 能力自检与 `agent.auto_diagnose_failures` 布尔开关；新 key 只放输入草稿，不回填 `***` 到保存请求。
- [ ] pipeline 和 schedule task editor 对 Copilot 类型加载 `GET /api/resources/copilots`，按资产名称展示、以 asset_id 提交，并编辑 formation。
- [ ] 运行相关 Vitest、typecheck 和 build 并提交 `feat(web): add built-in agent workspace`。

### Task 6：文档、冒烟与集成契约

**文件：** `README.md`、`docs/README.md`、`docs/04-数据模型与持久化.md`、`docs/05-API规范与路由清单.md`、`docs/11-Agent模块设计.md`、`docs/12-实施计划与里程碑.md`、`docs/13-决策记录.md` §8、`docs/14-开放API接入指南.md`（经生成器同步）、`scripts/agent_smoke.py`、相关测试 fixtures。

- [ ] 为 agent smoke 添加离线 FakeLLM 和 FakeAsst 路径，断言 capability check、SSE event 顺序、pipeline Copilot 实际 payload、schedule persistence、failure incident dedupe、auto-recovery allowlist；确认 RED。
- [ ] 更新决策速查表/M13 交付边界，写明 DeepSeek 默认、Fernet env、自动诊断与 Copilot 新契约；M11 历史保持原状，docs/13 §8 新增 M13 决策和 Copilot 证据，移除当前“仍未运行 Copilot”的陈旧结论。
- [ ] 更新 settings/API/task/schedule/Agent schema、错误码和失败触发时序；运行 `.venv/bin/python scripts/dump_openapi.py`、`cd web && pnpm gen:api`、`.venv/bin/python scripts/generate_api_guide.py`。
- [ ] 检查同计划文件冲突、OpenAPI/API guide `--check`；运行 `.venv/bin/python scripts/agent_smoke.py` 与 `.venv/bin/python scripts/api_smoke.py`，写报告后提交 `docs(agent): specify M13 runtime and recovery`。

## 最终验收

主 agent 在所有 task review 通过后亲自运行：

- `.venv/bin/python -m pytest -q`
- `.venv/bin/python scripts/agent_smoke.py`
- `.venv/bin/python scripts/api_smoke.py`
- `.venv/bin/python scripts/dump_openapi.py --check`
- `.venv/bin/python scripts/generate_api_guide.py --check`
- `cd web && pnpm install --frozen-lockfile && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build`
- `.venv/bin/python scripts/frontend_smoke.py`

真实 DeepSeek API、真机/模拟器 MaaCore 执行补充记录，不替代 FakeLLM/FakeAsst 硬验收。
