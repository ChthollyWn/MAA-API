# 移除消耗护栏与旧会话查看器（维护性补丁台账）

- 分支：`remove-spend-legacy`；基点：`dev` = `02b5ae7`；合并：待回填（`dev` 上的 `merge: 移除消耗护栏与旧会话（维护性补丁，见 docs/v3/13 §8.175）`）；tag：无（维护性补丁，不新增里程碑）
- spec：[remove-spend-legacy-sessions](../specs/remove-spend-legacy-sessions.md)
- 计划：[remove-spend-legacy-sessions](../plans/remove-spend-legacy-sessions.md)
- 决策记录：[docs/v3/13 §8.175](../13-决策记录.md)
- 迁移：`0039_drop_spend_ledger`（删 `spend_ledger` 表与索引；降级按 0037 DDL 重建结构，不恢复数据）
- 用户决定（2026-11-09）：两块都**彻底删除**——前端入口 + 后端服务/端点/配置/账本表 + 旧会话的 4 个 v2 只读端点；不是只隐藏界面。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `6909598` | 规格与实施计划入库（非里程碑补丁，不改 12 的路线与顺序） |
| T1 | `9eca405` | 后端消耗护栏拆除：删 `agent/spend.py`、Worker `SpendMiddleware`、`spend_ledger` 模型、迁移 0039、三个 `spend.daily_*` 配置、`SpendKind`、`SPEND_CAP_EXCEEDED`；`QueueService`/`PipelineRunner`/工具层/`invoke_tool` 删预留与预检；`api/routers/spend.py` → `brief.py`；简报删消耗小节、日界改 `brief_day_window`；测试整删/改写 15 个文件；`brief_spend_smoke.py` → `brief_smoke.py` |
| T2 | `9eca405`（同上提交） | 后端旧会话只读端点拆除：删 4 个 v2 只读路由与 `session_wire`、`message_wire`、`_v2_session_or_404`、`copilot_grant_wire`、`active_copilot_grant_wire`、`_V2_SESSION_*`；仓储删 `legacy_only`；`agent_smoke.py` 会话夹具改仓储直连；`lease.py` docstring 位次同步 |
| T3 | `63c4786` | 前端两块删除：删 `features/spend/`、`LegacySessionsPage` 等 4 个查看器专用文件；`useRunBrief` 迁到 `features/brief/api.ts`；router/nav/keys/实时层/指挥台 attention 全链路同步；`/agent` 深链重定向 `/`；`frontend_smoke.py` 同步 |
| T4 | `f1607d5` | 现行文档同步 01–06、08–12、16、README；09 §6、03 §11.6、11 §8 保留两行历史节不重编号；03/12 迁移编号顺延 0040–0046 |
| T5 | `fe94aa0` | OpenAPI 快照、前端生成类型、`generate_api_guide.py` 重生成；14 清单 168 错误码 / 182 端点 / agent 79 条 |
| T6 | 本台账所在提交 | 13 §8.175、台账、全量验证与合并 |

## Rulings I made

| # | 裁决 | 理由 | 代价 |
|---|---|---|---|
| R1 | 迁移用「新增 0039 删表」而不是回改 0037；downgrade 重建表结构、不恢复数据 | 已发布迁移不回写；账本属可丢弃运行数据 | 降级不恢复历史用量统计 |
| R2 | 历史 `agent_audit` 里 `SPEND_CAP_EXCEEDED` 行保留、不清理 | `error_code` 是字符串，删枚举不影响读；清理属数据丢失风险 | 审计页仍可能有历史码 |
| R3 | 每日简报保留，只删「消耗（按任务参数上限计）」小节；路由文件改名 `brief.py`、operationId 不变 | 用户只要求去消耗护栏；简报属相邻能力 | 文档与路由清单需同步 |
| R4 | 中间件链重编号 14 Mandate / 15 Lease / 16 Clarification / 17 Report / 18 Policy | 删除第 15 位后必须保持相对顺序；05 §5.2 与测试断言同步 | 05/09 文档位置引用同步审 |
| R5 | `tests/db/test_migrate.py` 的 `tables_created_by_head()` 只扫 `upgrade()` | 0039 downgrade 的 `op.create_table` 会被原实现误算为「head 新增表」，与 0038 备份集合相交 | 测试设施小改，不改变断言语义 |
| R6 | `/agent`、`/agent/*` 深链 `Navigate replace` 到 `/`（丢弃 `?session_id=`） | 旧书签不 404；旧会话已无查看页 | 旧深链不再指到查看器 |
| R7 | `originLabel` 的「来自旧会话」兜底改「来自后台运行」 | v2 会话已无写入口，该分支不可达；文案不能指向已删功能 | 指挥台来源行文案变化，测试同步 |
| R8 | `AgentSessionRepository.delete` 与 `AgentRuntime.turn_running` 保留 | 生产无调用但历史仓储/运行时测试仍在用；删方法会连带改历史测试 | 保留少量无生产调用方法 |
| R9 | 前端 `keys.agent.messages` 保留，只删 `sessions`/`session`/`spend` | rewind 的 `MessageActions` 与 `session_rewound` 仍有消费者 | 无 |
| R10 | `scripts/agent_smoke.py` 会话夹具改成仓储直连创建/回读 | 不能再保留 v2 会话 HTTP 入口 | 冒烟脚本局部重构 |
| R11 | 文案基线快照（`docs/copy-audit/`）在本补丁**不重设** | 基点 `dev`（`02b5ae7`）上 `scripts/copy_inventory.py --check` 已经是 1（基线停在 2026-11-03，M85 以来的文档积压造成同组回潮；实测 dev 与本分支的回归项同组，非本补丁引入）。全量重新生成会把积压一次刷出 ~1.8 万行无关差异，并给 `scripts/doc_links_baseline.txt` 引入 4 条新断链（超出「条目只减不增」上限） | `copy_inventory.py --check` 的既有基线差异留待后续单独刷新，不作为本补丁门禁 |

## 验收

写者全部停止后，主 agent 独占复跑（长输出在 `/tmp/lead-*.log`）：

| 检查 | 结果 |
|---|---|
| 后端全量 `pytest -o addopts= -q` | **3 failed / 5243 passed / 5 skipped / 2 xfailed**；3 条失败是既有真资源用例（`test_activity_index` 2 + `test_stage_layout` 1，见 m63/m64/m65 台账），无新增失败 |
| 契约/清单定向 `tests/domain/test_route_inventory.py test_errors.py test_enums.py tests/db/test_migrate.py tests/api/test_app_skeleton.py` | 222 passed |
| `scripts/dump_openapi.py --check` | 0；`web/openapi.json` 重生成（182 operations） |
| `scripts/generate_api_guide.py --check` | 0 |
| `cd web && pnpm gen:api:check` | 0 |
| 前端 `pnpm typecheck && pnpm test --run && pnpm build` | typecheck 0；112 文件 / 976 用例；build 0（`[sw_harness] SERVICE WORKER OK`） |
| `scripts/frontend_smoke.py`（先 `pnpm build`） | 0，末行 `SMOKE OK` |
| `scripts/agent_smoke.py` | 0，末行 `SMOKE OK` |
| `scripts/brief_smoke.py` | 0，末行 `SMOKE OK` |
| `scripts/orchestrator_smoke.py` | 0，末行 `SMOKE OK` |
| `scripts/check_doc_links.py` + `tests/test_doc_links.py` | 末行 `BROKEN 0`；基线无新增/过期条目 |
| 残留 grep | `SpendGuard|spend_guard|SpendLedger|SpendKind|SPEND_CAP_EXCEEDED|spend.daily_|/more/spend|/api/agent/spend` 在代码/配置/脚本/测试零命中；旧会话残留仅历史注释与 `services/copilot/grant.py` 的同名不同符号 |

已知失败（非本补丁引入）：`tests/services/test_activity_index.py` 2 条 + `tests/services/test_stage_layout.py` 1 条，依赖 `resource/` 真资源数据，m63 起的既有基线。

## 备注

- 冒烟脚本并发复跑会因仓库 `resource/maa_api.db-wal` mtime 变化误报「冒烟写入了仓库 DB/config 产物」；串行、无 pytest 并发时重跑即通过（本台账记录的是串行复跑结果）。
- 历史 spec/计划/台账（`m84-brief-spend`、`m72-console-frontend`）一行未改，作为记录保留。
