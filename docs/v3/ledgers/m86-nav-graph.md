# M86 界面导航图（台账）

- 分支：`nav-graph`（已合并并删除）；基点：`dev` = `dc4a19a`（含维护性补丁 `evals-ticket-driver` 的合并 `0f16dc4`）；合并：`79a807e`（`merge: 界面导航图（M86，见 docs/v3/13 §8.180）`）；tag：`v3-m86`。
- spec：[m86-nav-graph](../specs/m86-nav-graph.md)（1245 行，2026-11-09 升格版）
- 计划：[m86-nav-graph](../plans/m86-nav-graph.md)（857 行）
- 决策记录：[docs/v3/13 §8.180](../13-决策记录.md)
- 迁移：`0041_nav_graph`（可逆；反序删 `nav_edge`、`nav_node`；迁移只建表与索引，不写种子）
- 执行方式：按计划的「每批 2–4 个任务由同一实现者顺序实施、每批跑验证命令并提交」：W1 = T1+T2，W2 = T3+T4+T7，W3 = T5+T6+T8，W4 = T9+T10+T11，W5 = T12（文档、全量验收与本台账）。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `cbe312b` | 计划入库；spec §0 的 16 条前置检查；12 条接缝裁决；基线：全量 pytest 3 failed / 5261 passed / 5 skipped / 2 xfailed（3 条既有真资源失败）、`tests/evals` 69 passed、迁移 head 0040、工具 104/103 |
| T1 | `703a67d` | `NavNodeKind`/`NavOrigin` 两枚举；`nav_node`/`nav_edge` 两表（唯一索引、两条 CASCADE、三个 JSON 列）；迁移 `0041_nav_graph`；`test_metadata_has_exactly_57_business_tables` 改名；迁移四用例（升级列/索引/外键、唯一冲突、降级重升、离线 `--sql`） |
| T2 | `71899f8` | `maa_api/services/screenshot_archive.py`（内容寻址落文件 + 建行、不 commit）；dHash 感知哈希（两 ROI，16 位十六进制分段）；`nav_classification`/`nav_locate` 结构化 schema 与注册；`Recognizer`（哈希→歧义→视觉分类，只读不写图）；`ModelVisionClient`（`vision` 角色、结构化失败返回 None） |
| T3 | `3a66d5c` | `NavGraphService`（图读写、`ensure_seed`、Dijkstra、成功耗时滑动平均、三次失败停用、样本去重与上限 8、`LastSeenStore`）；`seed.py` 15 节点 / 36 边、21 个 roi 逐条对 MAA 资源；`device_factory` 注入点 |
| T4 | `a9bd201` | `Navigator`（识别、最短路径、逐步执行、截图校验、首达信任、走错学边重规划、未知回主界面、`max_steps`、trail 七键、`remember` 五步）；`NavDevice` 协议与 `AppNavDevice`；`raw._run_atomic` 改名 `run_atomic`；三个 `NAV_*` 错误码先落 `errors.py`（T6 补文档与计数） |
| T7 | `df2706c` | `NavSettings` 三键（`nav.enabled`/`nav.learn`/`nav.max_steps`）；`SETTING_KEYS` 与设置页 `_FIELDS`；`config.template.yaml` 的 `nav:` 段；`MAA_NAV_*` 环境变量测试 |
| T5 | `c16bb28` | `maa_api/agent/tools/nav.py` 三工具（`where_am_i` 纯读、`remember_screen` 写图、`navigate_to` DANGEROUS + 原子会话授权）；只注册给 `navigator`；工具目录 107/106（`refetchable` 51、`exclusive` 9、`read` 5） |
| T6 | `c92d020` | 四个 REST 端点（GET 图、PUT 节点、PUT 边、DELETE 边）与七个请求/响应模型；`docs/v3/14` §4.22 三错误码与 §6.15 四行（171/170/22、186/83）；`app.state.nav_service` 装配与 `ensure_seed()`；重生成 `web/openapi.json`、前端类型与 15 接入指南 |
| T8 | `0379037` | `navigator.md` 加三个导航工具、`version: 2`、正文「## 界面导航图」六条；`EXPECTED_VERSIONS["navigator"]=2` |
| T9 | `4c9a795` | 前端 `web/src/features/nav/` 七个文件（页面、节点列表/编辑器、跳转表/编辑器、api、测试）；`/more/nav` 路由、`secondaryNav` 入口、`keys.agent.navGraph()`；五个页面流程用例 |
| T10 | `d63bb43` | `scripts/nav_smoke.py` 六段断言（种子 15/36、未知识别、两步导航与成功计数、哈希识别、三次失败停用、`remember_screen`）；清理 W2 留下的重复 `FakeNavDevice` |
| T8 接缝修复 | `bc306fb` | `navigator.md` 的 `risk_ceiling` 从 `none` 改为 `consume`，否则首次 `navigate_to` 被 `MandateMiddleware` 拒绝（见 13 §8.180） |
| T11 | `5d20027` | `NavWorld`（`world.nav.start/transitions`）与 `_open_world` 假设备绑定；两个 capability 场景 `nav-infrast-navigate-to`、`nav-unknown-stage-remember`；两条手写 cassette（9/10 条应答）；场景数 11→13、`test_nav_world_parses` |
| T12 | 本台账所在提交 | 核对 02/03/04/14/15（02 的 `nav/` 行补 `__init__.py`）；同步 05 §6.1（107/106 与 `risk_ceiling`）、16 §22、README 状态行；回写 spec §4.7 / §5 T11 与 plan Task 11 的偏离；13 §8.180；全量验收（**未合并、未 tag**，留给主 agent） |

## 核心实现

- **两表**：`nav_node`（id 短 slug 主键、`title`、`kind`、`exemplars` JSON、`recognizer` JSON、`origin`、时间戳）与 `nav_edge`（UUID 主键、`from_node`/`to_node` 两条 `ON DELETE CASCADE`、`action` JSON、`action_hash`、`cost_ms`、成功/失败计数、`last_used_at`、`enabled`、`origin`、时间戳、唯一索引 `uq_nav_edge_from_node_to_node_action_hash`）。迁移 `0041_nav_graph` 的 `down_revision` 是 `0040_question_expiry_historical`。
- **种子**：15 个界面、36 条跳转，坐标全部归一化；21 个 roi 的中心坐标逐条对 MAA 资源核对（§7.2，结果 `BAD []`）；没有 `stage:` 预置节点；只在 `nav_node` 为空时由启动 `ensure_seed()` 单事务写入。
- **感知与识别**：dHash 按 ROI 归一化取 9×8 灰度、逐段 16 位十六进制；两段距离按 ROI 分段异或；平均距离最近者命中，得分差 < 2 退视觉；视觉候选是「有样本、能算出平均距离的前 8 + `home`」，答案须在候选内且置信度 ≥ 0.7；视觉不可用、请求失败或结构化输出非法都如实返回 `unknown`，识别全程不写图。
- **视觉客户端**：按 `vision` 角色解析模型档案，`supports_vision is not True` 即不可用；把截图缩到 1280×720、JPEG 质量 80 的 data URL 交给 `complete_structured(agent_name="navigator", role="vision", run_id/session_id=…)`；任何异常只记 warning。
- **图服务**：Dijkstra 代价 = `cost_ms` 或动作默认值 + `FAIL_PENALTY_MS × fail_count`，跳过禁用/exclude/locate 边，没有显式边时可走隐式 `home` 边（6000 ms）；成功按 `round(0.7×旧 + 0.3×新)` 更新耗时；失败三次自动停用；样本按平均距离去重、上限 8、超限先挤掉最早的非 `user` 样本；`last_seen` 记录「会话→（节点, 审计游标）」。
- **导航器**：先识别当前界面，再做路径规划；每一步执行后截图校验，走错到另一个已知界面时记原边失败并学一条实际跳转，然后从当前界面重新规划；认不出时等待重试、失败再回主界面；首达信任只在「`nav.learn=true`、种子边、目标零样本、实际画面未匹配任何节点、动作不是 `home`」五条同时成立时收样本；`max_steps` 由请求与配置取小；`locate` 边走通后学成 `tap` 边；`remember_screen` 按 id/title/kind 校验、整体跳过另一已知界面、建节点/加样本、从审计学 `sequence` 边。
- **设备适配**：`AppNavDevice` 的 `tap`/`swipe`/`key`/`home` 全部包在 `raw.run_atomic(context, "navigate_to", …)` 里（`operation_lock` + 流水线空闲检查），截图不走；租约被抢占、流水线已运行等 `AppError` 原样上抛，不当作步骤失败。
- **三个工具与授权**：`where_am_i`（`device="read"`、SAFE、纯读、`refetchable`、`rest_visible`）、`remember_screen`（`none`、SAFE、不可重取、`rest_visible=false`）、`navigate_to`（`exclusive`、DANGEROUS、原子会话授权、`rest_visible`）；只写进 `navigator.md`，`general` 与其它 Worker 不直接拿。
- **REST 与装配**：四个端点都在 `agent` tag 下；`GET` 返回图与 `enabled`/`learn`/`vision_available`；`PUT node` 支持标题/类型/rois/标记当前画面/按截图加样本/按 phash 删样本；`PUT edge` 新建要求端点与动作齐全、已存在只改 `enabled`/`action`/`cost_ms`；`DELETE` 物理删除。`app.state.nav_service` 在 `update_scheduler` 之前装配并 `ensure_seed()`，失败只记日志。
- **前端**：`/more/nav`，顶部「界面导航图」、界面卡片与编辑器、跳转表与新建弹层、四个 Toast 与两个错误前缀；「识别当前画面」调 `where_am_i` 且结果只放本地 state。
- **冒烟与评测**：`scripts/nav_smoke.py` 用真实 lifespan + 临时目录 + 假设备跑六段；评测新增 `world.nav` 假导航世界与两个 `ticket` 入口的能力场景，手写 cassette 以 `--strict` 回放。

## 验证证据

### 逐任务

| 任务 | 结果 |
|---|---|
| T0 | 16 条前置检查全部符合；基线全量 pytest 3 failed / 5261 passed；`tests/evals` 69 passed；迁移 head 0040；工具 104/103 |
| T1 | `tests/db` + `tests/domain/test_enums.py` **618 passed**；`alembic heads` 只有 `0041_nav_graph (head)` |
| T2 | `tests/agent/nav` **23 passed**；`tests/agent/llm/test_structured.py` **3 passed** |
| T3 | `test_graph_service.py` + `test_seed.py` **17 passed**；§7.2 坐标脚本 `BAD []`、退出码 0 |
| T4 | `tests/agent/nav` + `test_device_raw_tools.py` **93 passed**；`run_atomic` 标识符级检查无旧 `_run_atomic` 残留 |
| T7 | `tests/test_settings.py` + `test_vapid_settings.py` **55 passed**；`SETTING_KEYS`/`_FIELDS`/config-only = **142/131/13** |
| T5 | 目录/工具/策略/定义 **139 passed**；`build_registry()` 输出 **107 106** |
| T6 | 路由/错误码/route inventory/app skeleton **105 passed**；`dump_openapi.py --check` 与 `pnpm gen:api:check` 均 0；端点 **186/83**、错误码 **171/170/22** |
| T8 | `tests/agent/specs` + `test_agent_specs_router.py` **53 passed**；`navigator` 版本 2、`general` 版本 6 |
| T9 | 前端相关 **13 passed**；全量 `pnpm test --run` **113 文件 / 991 用例**；`pnpm build` 0（SW harness OK） |
| T10 | `nav_smoke.py` 末行 `SMOKE OK`；导航后端（nav/tools/router） **89 passed**；运行后仓库 `config.yaml` 与 `resource/maa_api.db` 指纹未变 |
| bc306fb | 定义与卡片相关 **54 passed**（含 `risk_ceiling=consume` 断言） |
| T11 | `tests/evals` **70 passed**；两场景 replay **2/2**（模型调用 9/10）；`--strict` **2/2** 无 mismatch/exhausted/unused，各单跑 3 次稳定 |
| T12 | 见下「最终验收」 |

### 最终验收（T12，2026-11-09）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest -o addopts= -q`（分支 `nav-graph`，工作区含本台账与文档改动） | 退出码 1；**3 failed / 5369 passed / 5 skipped / 2 xfailed**（577.61 s）。3 条恰为基线既有失败：`tests/services/test_activity_index.py` 的 `test_real_resource_pack_reports_the_current_activity_and_its_stages`、`test_real_resource_pack_disambiguates_a_repeated_code`（真资源活动数据漂移）与 `tests/services/test_stage_layout.py::test_ambiguous_code_picks_the_first_stage_id_and_counts_candidates`（同一资源）；**新增失败 0**。passed 从基线 5261 增至 5369（M86 新增 108 条用例） |
| `scripts/dump_openapi.py --check` | 0（`web/openapi.json` 与实现一致） |
| `scripts/generate_api_guide.py --check` | 0（15 接入指南由 14 §4 生成，已同步） |
| 前端五步：`pnpm install --frozen-lockfile` → `gen:api:check` → `typecheck` → `test --run` → `build` | 0；vitest **113 文件 / 991 用例**通过；`vite build` 正常，`[sw_harness] SERVICE WORKER OK` |
| `scripts/frontend_smoke.py` | 0；末行 `SMOKE OK` |
| `scripts/agent_smoke.py` | 0；末行 `SMOKE OK`（临时 SQLite 隔离，仓库 DB/config 指纹未变） |
| `scripts/orchestrator_smoke.py` | 0；末行 `SMOKE OK`（同上） |
| `scripts/nav_smoke.py` | 0；末行 `SMOKE OK`（六段断言全部通过） |
| `scripts/agent_eval.py --suite regression --mode replay` | 0；`regression replay：5/5 通过（100.0%）`，末行 `EVAL OK` |
| `scripts/agent_eval.py --suite capability --mode replay` | 0；`capability replay：8/8 通过（100.0%）`，末行 `EVAL OK`（M85 基线 6/6 之上新增两个导航场景，全部通过） |
| `scripts/agent_eval.py --suite capability --mode replay --strict --scenario nav-infrast-navigate-to --scenario nav-unknown-stage-remember` | 0；2/2 通过（模型调用 9 / 10），无 `mismatch`/`exhausted`/`unused`，末行 `EVAL OK` |
| spec §7.2 种子坐标脚本 | `BAD []`、退出码 0（21 个 roi 对 MAA 资源） |
| `git diff --check` | 0 |
| `scripts/check_doc_links.py` | `BROKEN 0` |
| `.venv/bin/alembic heads` | `0041_nav_graph (head)` |
| 计数实测 | 工具 107/106；`SETTING_KEYS`/`_FIELDS`/config-only = 142/131/13；错误码 171/170；业务表 57 |

以上在 `nav-graph` 分支上实测；**合并到 `dev` 后由主 agent 复跑 pytest、OpenAPI check、`nav_smoke` 与两个评测套件**，并把复跑结果补进本表。

### 计数

| 项 | M86 前 | M86 后 | 钉子 |
|---|---|---|---|
| 工具（总数/模型可见） | 104/103 | **107/106** | `tests/agent/test_tool_catalog.py` |
| 工具 `refetchable` / `exclusive` / `read` | 50 / 8 / 4 | **51 / 9 / 5** | 同上 |
| HTTP 端点（总数/agent 小节） | 182/79 | **186/83** | `tests/domain/test_route_inventory.py`、14 §6 |
| 错误码（全部/HTTP 绑定/节） | 168/167/21 | **171/170/22** | `tests/domain/test_errors.py`、14 §4 |
| 业务表 | 55 | **57** | `tests/db/test_models.py` |
| 配置键（`SETTING_KEYS`/`_FIELDS`/config-only） | 139/128/13 | **142/131/13** | `tests/test_settings.py` |
| 迁移 head | 0040 | **0041_nav_graph** | `alembic heads` |
| `navigator` 定义版本 | 1 | **2** | `tests/api/test_agent_specs_router.py` |
| 事件 kind | 16 | **16**（不变） | `tests/agent/events` |
| 评测场景 | 11 | **13** | `tests/evals/test_regression_replay.py` |

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | REST 调 `navigate_to` 只抢占、不持有设备租约 | 沿用 M71 人工操作口径；`_rest_preempt_hook` 在执行前 `preempt_device`，不 `acquire`（13 §8.180） |
| R2 | `NavGraphService.device_factory` 是公开注入属性；为空时懒导入 `AppNavDevice` | 让冒烟与评测直接替换假设备，且 `graph.py` 不反向 import 导航器 |
| R3 | `remember_screen` 认出另一个已知节点时整体跳过 | 避免把已有界面重复记成新节点；与 §4.5.5 和用例名一致（13 §8.180） |
| R4 | trail 固定七键 | spec 文字写「六个」但列出了七个；按七个与用例执行（13 §8.180） |
| R5 | `where_am_i` 候选：哈希前 3；视觉前 8 + `home` | 候选与是否过阈值无关，只作诊断与视觉选择域（13 §8.180） |
| R6 | 首达信任只在五个条件同时成立时生效 | 防止把误点收成样本；`home` 走 anchor |
| R7 | `archive_screenshot` 的提交归调用方 | 归档与加样本要在同一调用方事务边界内，失败不阻断导航 |
| R8 | `remember_screen` 的审计查询按 `click`/`swipe`/`key_event` 与四种跳过原因 | 从 `LastSeenStore` 游标起只收上次认出后的成功动作 |
| R9 | `max_steps` 只能调小 | 请求参数不能突破配置上限；`recover_home` 也计一步 |
| R10 | `update_nav_edge` 只允许改 `enabled`/`action`/`cost_ms` | 端点变更让用户删旧建新；重新启用清零失败计数 |
| R11 | 冒烟直接走工具注册表 | `remember_screen` 的 `rest_visible=false`，不通过 REST invoke |
| R12 | 导航图页放 `web/src/features/nav/`，入口 `/more/nav` | 「更多」页实际是 `routes/more/index.tsx`，没有 `features/more/` |
| R13 | T7 提前到 T5/T6 之前 | T5/T6 都读 `get_settings().nav`，先落配置让工具与路由只写一次（13 §8.180） |
| R14 | `navigator.risk_ceiling` 从 `none` 改为 `consume` | 否则首次 `navigate_to` 在 `MandateMiddleware` 被拒，与原子会话授权矛盾（13 §8.180，提交 `bc306fb`） |
| R15 | 评测 cassette 按 9/10 条写 | `grant_atomic_ops` 确认会触发总控一轮 system 观察模型调用，场景一必然多一条（13 §8.180） |
| R16 | 新建跳转成功不弹 Toast；启停/新建失败用「保存跳转失败：{message}」 | §4.6 的 Toast 清单封闭为四条；错误前缀按实际补（13 §8.180） |
| R17 | 页面标题栏与顶部 Section 同名「界面导航图」 | 照 §4.6 顶部 `Section title` 逐字实现；只影响观感（13 §8.180） |
| R18 | `nav_smoke.py` 把 `app.state.model_router` 置空 | 避免冒烟的首次视觉探查走真实网络；不影响生产装配（13 §8.180） |
| R19 | `PUT edge` 的重复边返回旧边、新建忽略 `enabled/cost_ms`、畸形 recognizer JSON 报 500 保持现状 | spec 未定义，不自行新增错误码或行为（13 §8.180） |
| R20 | 样本重复不拦学边；`remember_screen` 回显库里的 `title`/`kind` | 样本照加，只有学边被跳过；请求的 title 只用于新建 |
| R21 | `perform` 只吞非 `AppError`；`locate` 首达也学 `tap` 边 | 租约/流水线错误原样上抛；§4.5.6 要求首次走通即学 |
| R22 | 迁移 `--sql` 断言按 Alembic 实际输出（DDL 原文） | 迁移源码形态与渲染结果不同，测试按真实输出断言 |
| R23 | 评测覆盖缺口只记录，不补 capability 回放单测 | pytest 从 M68 起只回放 regression；两场景由验收 `agent_eval --suite capability --strict` 保证（13 §8.180） |

## 偏离（已记 13 §8.180）

- `navigator.risk_ceiling`：`none` → `consume`。
- 评测 cassette：场景一 9 条、场景二 10 条（原计划 8/10）。
- T7 提前到 T5/T6 之前执行。
- `remember_screen` 认出其它已知节点时整体跳过；trail 七键；`where_am_i` 候选口径；REST 只抢占不持租约；种子核对 `BAD []`；M89 复用 `screenshot_archive`。
- 新建跳转不弹 Toast、错误前缀「保存跳转失败」、标题栏与 Section 同名、冒烟置空 `model_router`、`PUT edge` 三处未定义边界保持现状、T11 覆盖缺口。

## 遗留

- **真机补充验证未做**（spec §7.3）：本机没有可用设备与真实视觉模型档案，首页 anchor 采样、真机 `navigate_to` 步数与 prompt token 对比、关闭导航图的对照组、`terminal` 的 locate 跳转都没有实测；导航执行只经 `FakeNavDevice`、冒烟与评测回放验证。视觉客户端只经 `FakeVision`、结构化失败与「不可用」用例验证。
- 场景二的 10 条 cassette 依赖「总控这一轮赶不上」的时序（`orchestrator.batch_window_ms` 默认 800 ms）；当前 `--strict` 3/3 稳定，若未来调度变化导致总控抢到一条应答，症状是 `exhausted`，需要补第 11 条总控应答。
- `PUT /api/agent/nav/edges/{edge_id}` 的三处边界（重复边返回旧边、新建忽略 `enabled/cost_ms`、畸形 recognizer JSON 500）保持现状；要改需先补 spec 与错误码。
- 两个 M86 能力场景只由 `agent_eval --suite capability` 回放，pytest 不跑 capability 套件；cassette 的 9/10 条口径没有单测钉住（见 13 §8.180 其余裁决 10）。
- `nav.enabled=false` 时三个工具报 503，REST 端点仍可读写图（照 spec §4.3.2 口径）。
- 回滚口径（spec §8，**未执行**）：在 `dev` 上用合并提交 hash 执行 `git revert -m 1 <merge>`，再 `.venv/bin/alembic downgrade 0040_question_expiry_historical`；降级会丢掉学到的导航图。
- **整支审查与修复**：整支审查（`dc4a19a..bf7d595`）REQUEST CHANGES（learn=false 成功分支仍写样本；upsert 已存在边未记成功；PUT node 移除样本/rois 重算竞态与 404/503 部分写入；05 device 清单缺两工具）→ 修复 `4bb1364`（navigator 学习门控、created=False 记成功、PUT node 改先删后重算且写库前校验、05 §6 补 navigate_to/where_am_i）→ scoped re-review 通过（114 passed、新用例对旧代码 6/7 失败）→ spec §4.5.4 伪代码补学习门控 `9c2b24b`。
- **主 agent 独立复核（nav-graph @bf7d595，合并前）**：全量 pytest 3 failed / 5369 passed / 5 skipped / 2 xfailed（3 条=基线既有真资源失败，无新增）；`dump_openapi.py --check` 0；前端五步 0；frontend/agent/orchestrator/nav 四冒烟 SMOKE OK；regression 与 capability `EVAL OK`；`check_doc_links.py` BROKEN 0；`git diff --check` 0；`alembic heads` = 0041_nav_graph。合并后 dev 与分支树一致，未重复跑全量。
- **合并后**：`git merge --no-ff nav-graph` = `79a807e`；`git tag v3-m86`；删除 `nav-graph` 分支；未 push。
