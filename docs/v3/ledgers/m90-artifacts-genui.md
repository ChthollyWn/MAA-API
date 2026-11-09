# M90 产物与生成式 UI（台账）

- 分支：`artifacts-genui`（已合并并删除，未 push）；基点：`dev` = `09d8d6c`（M89 合并 + 台账提交）；合并：`c07272b`（`merge: 产物与生成式 UI（M90，见 docs/v3/13 §8.184）`）；tag：`v3-m90`
- spec：[m90-artifacts-genui](../specs/m90-artifacts-genui.md)；计划：[m90-artifacts-genui](../plans/m90-artifacts-genui.md)

## §0 结果

| # | 实测 |
|---|---|
| 1 | `## artifacts-genui` |
| 2 | `v3-m89` |
| 3 | 1 个 M89 台账，含 capability 行（18/18） |
| 4 | 3 行（put/get/update） |
| 5 | `ArtifactDetailOut` 无 `meta` |
| 6 | FORM/DASHBOARD 两行 |
| 7 | 附录 A1 末行 `MISSING 0` |
| 8 | 5 个会话文件 |
| 9 | `110 109` |
| 10 | 三个既有 key（函数），无 `artifactsList` |
| 11 | `parseChartCard` 未导出 |
| 12 | `create_view` 0 |
| 13 | 4 个 Worker 定义 |
| 14 | `post_user_message` 1 行 |
| 15 | openapi --check 0、gen:api:check 0 |
| 16 | OPERATIONS 190 AGENT 91；ERRORS 173 172；TABLES 58；SETTINGS 144 133 |
| 17 | general 8 / analyst 4 / planner 4 / copilot 4 |
| A5 | OPERATIONS 190 AGENT 91；ERRORS 173 172；TABLES 58；SETTINGS 144 133 |

- pydantic `2.11.10`。
- capability 基线：M89 台账 **18/18（100%）**；M90 实测 **18/18（100%）**，持平。
- M90 收口计数：工具 **111/110**、HTTP 端点 **191（14 §6.15 agent 小节 88、OpenAPI agent tag 92）**、错误码 **173/172**、业务表 **58**、配置键 **144/133**、迁移 head `0042_demonstration`。
- 分支起点全量 pytest 的已知失败是 3 条真资源用例（与 M89 台账同一组）；本分支 T1–T6 完成后的基线为 3 failed / 5558 passed（另有既有 e2e flake 单跑通过）。

## 做了什么

### 任务与提交

| 任务 | 提交 | 结果 |
|---|---|---|
| T0 分支、§0、基线与计划入库 | `d3a49cd` | `artifacts-genui` 从 `dev`=`09d8d6c` 新建；§0 十七条全通过；A5 `190/91、173/172、58、144/133`；四 Worker 版本 8/4/4/4；计划、台账骨架与 7 份 brief 入库 |
| T1 视图模型、校验与提交渲染 | `22caa80` | 新增 `maa_api/agent/artifacts/views.py`：`VIEW_MIME`/`VIEW_VERSION`、`ViewSpec` 七组件四字段判别联合、附加规则 1–12、`_loc_path`、`view_kind`/`view_fields`/`view_json_schema`/`validate_submission`/`render_submission`、`ViewInvalid`；新增 `tests/agent/test_views.py` 12 例 |
| T2 `create_view` 工具与注册 | `4f4e525` | 新增 `maa_api/agent/tools/artifacts.py` 与 `tests/agent/test_create_view_tool.py`；注册组 `artifacts`、`SAFE`、模型可见、REST 不可见；工具计数 111/110、`EXPECTED_TOOLS` 与四处计数断言同步 |
| T3 四个 Worker 定义与提示词 | `a3b7113` | `general`/`analyst`/`planner`/`copilot` 工具列加 `create_view`、版本升到 9/5/5/5、正文追加视图提示；`common.md` 补【表单提交】；三份钉死测试同步 |
| T4 提交端点与详情 `meta` | `e558556` | `POST /api/agent/artifacts/{artifact_id}/submit`、`ArtifactFormSubmit`/`ArtifactFormSubmitResult`、`ArtifactDetailOut.meta`、`ArtifactKind` docstring 八种、14 §6.15；OpenAPI 191/92、`api.d.ts` 再生成 |
| T5 前端视图解析与渲染 | `ca971bb` | `features/artifacts/view/` 五源文件 + 两测试：七组件四字段解析、`initialValues`/`validateValues`/`toSubmitPayload`、白名单渲染（纯文本、`ChartCard`、表单控件）、只读与 `dropped` 计数；`parseChartCard` 导出、`create_view: 生成视图` |
| T6 视图卡、产物页与会话接入 | `2b4248a` | `features/artifacts/` 其余文件：`ViewArtifactCard`/`ViewArtifactBody`/`ArtifactChip`、`ArtifactsPage`/`ArtifactDetailPage`、`artifactsListInfiniteQuery`/`usePinArtifact`/`useSubmitArtifactForm`/`downloadName`；会话三件套挂卡（`LiveRunView` 逐层透传）、路由与侧栏、`ArtifactList` 抽屉；`format.ts` 统一标签/大小/文件名 |
| T7 全量验收、文档与台账 | 本提交 | 十项门禁；02 §4.1、04 §2.5、11 §9、13 §8.184、README 状态行补缺口；台账 |

### submit 的写消息顺序与并发覆盖窗口

- 端点顺序固定：`ArtifactService.get(artifact_id)`（不传 `max_chars`）→ 非 `form`/非视图 MIME → `validate_view` → `ticket_id is None` → 4 KiB 判断 → `validate_submission` → `render_submission` → `worker_service.post_user_message(ticket_id, content, [], notify_content=f"【表单提交】{title}")` → 合并 `meta` 并 `ArtifactService.update`。
- `meta` 只改 `submissions`（追加后 `[-5:]`）与 `last_submitted_at`，其余键（含 M84 的 `brief`/`facts_markdown`）原样保留；响应与 `meta` 共用同一个 `YYYY-MM-DDTHH:MM:SSZ` 时间串。
- 已知窗口 1：消息写入成功而 `meta.update` 失败时响应 500、用户消息已发出，重试会再发一条【表单提交】消息。
- 已知窗口 2：`meta` 是读改写，并发提交后写覆盖先写，`submissions` 可能丢一条；单次限 4 KiB、只留 5 条把损失限制在提交记录内。`test_submission_meta_last_write_wins` 钉住该行为。
- 拒绝路径（404/400/409/503）都不写消息、不改 `meta`；删工单后外键 `SET NULL`，端点按「这个表单不属于任何工单」400 返回。

## 验证证据

| 命令 | 结果 |
|---|---|
| T1 指定 pytest（W1 报告） | `tests/agent/test_views.py` **12 passed**，退出码 0；`view_json_schema()` 的组件判别 `mapping` 与 `required` 断言通过 |
| T2 指定 pytest（W1 报告） | `test_create_view_tool.py` + `test_tool_catalog.py` **24 passed**；与 T1、`test_agent_specs.py` 联跑 **79 passed** |
| T3 指定 pytest（W2 报告） | 红跑 `6 failed, 52 passed` → 绿跑 **58 passed**；版本 9/5/5/5，`daily_ops`/`diagnose`/`ops` 不含 `create_view` 且版本不变 |
| T3 capability replay（W2 报告，`resource/evals/20261109-183907-capability-replay-d92d/result.json`） | **18/18 通过（100.0%）**，`EVAL OK`，与 M89 基线持平 |
| T4 指定 pytest（W2 报告） | 红跑 `11 failed, 1 passed` → 绿跑 **27 passed**；指定七文件合计 **85 passed** |
| T4 `dump_openapi.py --check` / `pnpm gen:api:check`（W2 报告） | 均退出码 0；`operations=191`、tag 含 `agent` 的 operation `92` |
| T5 前端（W3 报告） | 红跑退出码 1（模块不存在）→ 绿跑 `web/src/features/artifacts/view` 相关用例 **12 passed**、退出码 0；`pnpm typecheck` 0；安全扫描无输出、退出码 1 |
| T6 前端（W3 报告） | 三组红跑退出码均 1 → 绿跑全量 **121 files / 1042 tests passed**；`pnpm build` 0（`SERVICE WORKER OK`）；相关后端 pytest **28 passed** |
| 全量后端 pytest（`/tmp/m90-pytest.log`） | **4 failed / 5589 passed / 5 skipped / 2 xfailed**，552.18 秒；4 条均为已知失败（3 条真资源基线 + 1 条既有 e2e flake），**无 M90 新增失败**（详见「最终全量」） |
| 既有 e2e flake 单跑（`/tmp/m90-flake-single.log`） | `tests/api/test_orchestrator_e2e.py::test_daily_flow_from_message_to_ready_report` **1 passed**（8.04 秒），退出码 0 |
| `dump_openapi.py --check` | 退出码 0，`OpenAPI snapshot is current: web/openapi.json` |
| 前端五步（`pnpm install --frozen-lockfile`、`gen:api:check`、`typecheck`、`test --run`、`build`） | 全部退出码 0；`gen:api:check` 输出 `OpenAPI types are up to date.`；`typecheck` 0；全量 **121 files / 1042 tests passed**（17.91 秒）；`build` 0（`SERVICE WORKER OK`） |
| `scripts/frontend_smoke.py`（`/tmp/m90-fsmoke.log`） | 退出码 0，末行 `SMOKE OK` |
| `scripts/agent_smoke.py`（`/tmp/m90-asmoke.log`） | 退出码 0，末行 `SMOKE OK` |
| `scripts/orchestrator_smoke.py`（`/tmp/m90-osmoke.log`） | 退出码 0，末行 `SMOKE OK` |
| `scripts/nav_smoke.py`（`/tmp/m90-nsmoke.log`） | 退出码 0，末行 `SMOKE OK` |
| `scripts/demonstration_smoke.py`（`/tmp/m90-dsmoke.log`） | 退出码 0，末行 `SMOKE OK` |
| `agent_eval.py --suite regression --mode replay`（`/tmp/m90-eval-reg.log`） | 退出码 0，**5/5 通过（100.0%）**，`EVAL OK`（`resource/evals/20261109-191352-regression-replay-a935/result.json`） |
| `agent_eval.py --suite capability --mode replay`（`/tmp/m90-eval-cap.log`） | 退出码 0，**18/18 通过（100.0%）**，`EVAL OK`，与 M89 基线持平（`resource/evals/20261109-191413-capability-replay-f866/result.json`） |
| `check_doc_links.py` | 末行 `BROKEN 0`，退出码 0 |
| `generate_api_guide.py --check` | 退出码 0，`API guide generated sections are current` |
| 安全扫描（`web/src/features/artifacts/view`，排除 `*.test.ts`/`*.test.tsx`） | 无输出，退出码 1（没有 `dangerouslySetInnerHTML`/`img`/`iframe`/`a`/`href`/`object`/`embed`） |
| `git diff --check` | 退出码 0，无输出 |
| 计数复核（附录 A2/A5） | 工具 `111 110`；`OPERATIONS 191 AGENT 92`；`ERRORS 173 172`；`TABLES 58`；`SETTINGS 144 133`；pydantic `2.11.10`；四 Worker 版本 9/5/5/5；迁移 head `0042_demonstration` |

### 最终全量

- 后端全量（`/tmp/m90-pytest.log`）：**4 failed / 5589 passed / 5 skipped / 2 xfailed**，耗时 552.18 秒。4 条失败：
  1. `tests/api/test_orchestrator_e2e.py::test_daily_flow_from_message_to_ready_report`（既有 e2e flake，单跑 **1 passed**，8.04 秒；与 M89 台账记录的同类时间敏感 flake 一致）。
  2. `tests/services/test_activity_index.py::test_real_resource_pack_reports_the_current_activity_and_its_stages`（基线既有真资源失败）。
  3. `tests/services/test_activity_index.py::test_real_resource_pack_disambiguates_a_repeated_code`（基线既有真资源失败）。
  4. `tests/services/test_stage_layout.py::test_ambiguous_code_picks_the_first_stage_id_and_counts_candidates`（基线既有真资源失败，本机实测 `574 == 554`）。
- 结论：与开工基线（3 条真资源失败）相比 **没有新增失败**；第 4 条是既有 flake，单跑通过。通过数 5589 包含 M90 新增的 T1–T6 测试。

## Rulings I made

1. **接缝裁决照计划执行**：`ArtifactService.put(*, kind, title, body: bytes, ...)`、`get(...) -> ArtifactDetail`、`update(artifact_id, *, content: str, meta: dict)`；提交消息走 `worker_service.post_user_message(ticket_id, content, [], notify_content=...)`（第三个位置参数 `mentions` 必填），不直接写收件箱；产物服务用 `_artifact_service`，新 `_worker_service` 未装配时 `AGENT_DISABLED`。404 文案固定「产物不存在」。
2. **详情 wire 只加 `ArtifactDetailOut.meta`**：`ArtifactOut` 列表项不加，`artifact_detail_out` 签名不变，`meta` 原样返回整列（含 `brief`/`facts_markdown` 等内部键）；M75 端点其它语义不动。03 §8.2、04 §2.3、13 §8.184 已同步。
3. **`artifact_created` 只走 WS**：实现只在 `ArtifactService._broadcast` 发 `agent_event`；视图卡只从 `tool_result` 的 `tool.result.result.artifact_id` 挂，历史与直播共用 `artifactIdOf`；不补 SSE、不改 `sse.ts`/`live-turn.ts`、不给 `LiveSegment` 加段。
4. **submit 错误码按 spec，不按任务摘要**：非法值/超限/非视图 400 `INVALID_PARAMETER`（超限 `details.limit_bytes=4096`）、结束工单 409 `TICKET_ENDED`、未知产物 404、删工单后外键 SET NULL → 400「这个表单不属于任何工单」；`TICKET_NOT_FOUND` 只作 `post_user_message` 的防御分支。不是 422/413/409。
5. **提交记录窗口如实保留**：写消息成功后再 `meta.update`，后者失败会 500 且消息已发；并发读改写后写覆盖先写、`submissions` 可能丢一条（限 4 KiB、留 5 条）。端点 docstring 与 `test_submission_meta_last_write_wins` 钉住。
6. **文案统一**：`dashboard` 统一「看板」（`ARTIFACT_KIND_LABELS` 与 `ArtifactList` 测试）；`已置顶`、`内容较长，只显示前 64 KiB`、`下载产物失败：{原因}`、`提交表单失败：{原因}`、`表单有 N 处需要修改` 统一；`ARTIFACT_KIND_LABELS`/`formatArtifactSize` 移到 `features/artifacts/format.ts` 只留一份。
7. **安全扫描排除测试文件**：范围 `web/src/features/artifacts/view/`，排除 `*.test.ts`/`*.test.tsx`（测试夹具有意放 `<img>` 等负例）；交付前无输出、退出码 `1`。
8. **604 图表点边界**：总点数上限 600；测试用 600 点通过、604 点（151 分类 × 4 系列）报错。601 是质数，且每条 `series.values` 长度必须等于 `categories`，构造不出 601，故 spec §6 的「601 报错」按 604 落地（见 T1 brief 的同条修正）。
9. **T6 红跑→绿跑退出码**：三组红跑退出码均为 1，绿跑全量 121 files/1042 tests、`build` 0；T5 红跑退出码 1 → 绿跑 12 passed、退出码 0。
10. **计数以 Task 0 实测为准**：开工 `110/109`、`190/91`、`173/172`、`58`、`144/133`；M90 只加工具与端点各 1，收口 `111/110`、`191/92`（14 §6.15 为 88，与 OpenAPI tag 的 92 口径不同源）；错误码/表/配置不变。
11. **`LiveRunView` 逐层透传**：`artifactIdOf` 在 `transcript-v3.ts`，`ConversationView`、`LiveTurnView`、`LiveRunView` 逐层把 `artifactId` 传给 `ViewArtifactCard`，没有新增全局 store 或 SSE 事件。
12. **删工单路径**：`artifact.ticket_id` 外键 SET NULL 后端点按 400「这个表单不属于任何工单」返回；不另造关外键的夹具。
13. **T7 文档只补缺口**：逐条核对升格预写项后只补了 02 §4.1 的 `views.py` 契约名、04 §2.5 的视图 MIME、11 §9 的产物页组件/`看板` 说明、13 §8.184 与 README 状态行；M91–M98 的预写值未覆盖；05 §6.1 保留「104/103 + 各里程碑增量（…M90 后 111/110）」的累计口径；16 无需改（无迁移、无配置、无部署形态变化）。
14. **T1 测试路径**：按 spec/plan/brief 放 `tests/agent/test_views.py`，任务消息里的 `tests/agent/artifacts/test_views.py` 未采用。
15. **`create_view` 返回四键** `{artifact_id, kind, title, field_count}`；只给 `general`/`analyst`/`planner`/`copilot`，总控、子代理、后台代理不加。
16. **`test_agent_specs.py` 提前到 T2**：W1 已改 `len(definitions) == 111` 与 `M90_TOOLS`，T3 不再 +1；`test_tool_catalog.py` 的模型侧 schema 总长上限由 70 000 调到 72 000，`v3_only` 集合加 `create_view`（REST 不可见）。
17. **T4 测试文件名** `tests/api/test_artifact_form_submit.py`；`test_submit_route_is_in_agent_group` 需要生产 `custom_operation_id`，测试 app 用同一装配，生产代码未为此改动。
18. **前端展现裁决**：详情页复用 `ViewArtifactBody` 的空态；提交记录初值按最后一次整份取值、缺失字段留空；`ArtifactList` 抽屉对视图产物只给「在产物页打开」入口。不影响 wire。

## 偏离（已记 13 §8.184）

- 详情 wire 只加 `ArtifactDetailOut.meta`（列表不加、`meta` 含内部键）；`artifact_created` 只走 WS、不补 SSE；视图卡只靠 `tool_result`。
- submit 错误码按 spec 的 400/409，而不是任务摘要的 422/413/409；超限 `details.limit_bytes=4096`。
- 前端渲染器只产出白名单元素，安全扫描排除测试文件；`dashboard` 统一显示「看板」。
- 测试实现层面的偏离：T1 测试路径、604 点边界（spec §6 写 601）、`test_agent_specs.py` 提前到 T2、`test_tool_catalog.py` 阈值、`test_submit_route_is_in_agent_group` 的 `custom_operation_id` 装配。
- 未新增依赖（不引入 `jsonschema`）、配置、错误码、表、迁移、SSE 事件或 `submit` 之外的端点；M75 其它端点语义未动。

## 整支审查修复

- 整支审查 APPROVE（无 Critical/Important）：修复两处 Minor —— `ArtifactFormSubmit.values` 按 spec §4.2 改为 `Field(default_factory=dict)`（缺省空字典按必填项报 400 字段错误，不是 422）并补 API 用例；`render_submission` 对显式空串的可选文本渲染「（未填）」（§4.5.3）并补用例。
- README 的「M90 已合入 dev 并打 v3-m90 tag」在合并与 tag 后成立（本台账记录合并提交后核对）。

## 最终验证（主 agent）

- 合并前全量 pytest：3 failed / 5590 passed / 5 skipped / 2 xfailed；3 条均为基线既有真资源失败，新增 0（`test_orchestrator_e2e...ready_report` 为既有 flake，单跑通过）。
- `dump_openapi.py --check` 0、`gen:api:check` 0、`generate_api_guide.py --check` 0；前端五步 0（121 files/1042 tests，build + SW）；五冒烟末行 `SMOKE OK`；regression 与 capability `EVAL OK`（18/18）；`check_doc_links.py` BROKEN 0；`git diff --check` 0；视图渲染器安全扫描无输出（退出 1）。
- 整支审查 APPROVE（无 Critical/Important）→ Minor 修复 `08a3793`（values 缺省、空文本渲染）+ `16e6a65`（OpenAPI/类型重生成）→ 合并 `c07272b`、tag `v3-m90`。

## 遗留

1. spec §7 的两项补充检查**未执行**：配好 LLM 后在指挥台真实对话出表单、选完提交、Worker 收到【表单提交】并继续；作业研究场景跑一次 copilot 候选作业表单。需要真实 LLM 与浏览器，本任务只用回放、自动化测试与假世界验证，如实记「未验证」。
2. 真实浏览器手工回归未做：视图卡在直播流的实际观感、移动端布局、提交成功 Toast 与只读切换、产物页置顶/下载交互只在 vitest/RTL 与 `frontend_smoke` 层验证。
3. `tests/api/test_orchestrator_e2e.py::test_daily_flow_from_message_to_ready_report` 在本次全量中失败一次，单跑 8.04 秒通过，是既有时间敏感 flake，不是 M90 新增。
4. 3 条真资源失败为基线既有（活动索引与关卡布局依赖本机 `resource/` 版本），与 M89 台账同一组，不是 M90 新增。
5. 并发提交窗口按设计保留：真实跨标签页/多客户端同时提交未验证；单次 4 KiB、留 5 条的限制由测试覆盖。
6. 合并、`v3-m90` tag、删分支与合并后复验按要求留给主 agent；本台账的「合并」「tag」两行在合并后补。
7. `docs/v3/16-部署指南.md` 未加 M90 小节：本里程碑没有迁移、配置或部署形态变化；如果要对齐 M88/M89 的部署说明惯例，可另补。
