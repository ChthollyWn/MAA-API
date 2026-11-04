# M80 提示注入防护与会话回溯（台账）

- 分支：`guard-rewind`；基点：`dev` = `1c18880`；合并：`dev` 上的 `merge: 提示注入防护与会话回溯（M80，见 docs/v3/13 §8.112–8.120）`；tag：`v3-m80`
- spec：[m80-guard-rewind](../specs/m80-guard-rewind.md)（900 行）
- 计划：[m80-guard-rewind](../plans/m80-guard-rewind.md)
- 决策记录：[docs/v3/13 §8.112–8.120](../13-决策记录.md)
- 迁移：`0033_rewind`（可逆）。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `3e3a973` | 计划入库；§0 1–13；10 项接缝裁决；基线 `3 failed / 4775 passed` |
| G1（T1+T2） | `1e8d608` | 三列（`forked_from_*`/`superseded_at`）与迁移 0033；`list_by_session(include_superseded=False)`；两错误码（157/156）；`GuardSettings` 两键 + 模板/测试；14 号文档错误码两行 |
| G2（T3） | `2b2c875` | `guard/rules.py`（附录 B 逐字：20 规则、归一化、`collect_text`、`scan_text`、`is_suspicious`）+ `guard/render.py`（外部数据包装六条规则/flagged 安全提示）；82 用例 |
| G3（T4+T5） | `dd87450` | 附录 A 22 个工具 `untrusted_output=True` 整集钉死；`read_artifact`/`search_knowledge` 动态 `untrusted`；`confirmation.py` 加 `require_confirmation`/`_force_confirmation`/`record_rejection`；69 用例 |
| G4（T6） | `378a475` | `guard/injection.py`（扫描/包装/降权/拒绝/强制确认/复核/span/meta）；`injection_verdict` schema；`ToolOutcome.guard` 与落库/SSE guard；`spill` 保留；`common.md` 两段；链第 11 位；98 用例 |
| G5（T7） | `4924663` | `RewindService`（12 步、目标校验、检查点/计划、副作用与提示、事件、dry_run）；`fork_ticket`（复制委托/历史/检查点、forked_from、notice）；两端点；14 号文档（agent 63、总数 166）；冒烟两步 |
| G6（T8） | `3d0cadf` | `features/rewind/`（四动作、dry_run→正式、折叠、GuardBadge/InfoHint、ForkDialog）；总控/工单消息列表接入；transcript guard；确认卡 InlineAlert；`session_rewound` 失效；前端 106 文件 **943** 用例 |
| G7 | 本台账所在提交 | 09 §7 改写；04/05/16/13/README 同步；本台账；全量验收与合并 |

## 核心实现

- **规则检测**：20 条规则（override/role/fake_system/fake_tool_call/fake_confirmation/skip_confirmation/exfiltration/steer_url/obfuscation），低/中/高危；高/中危或 ≥2 条不同低危判可疑；零宽字符归一化；写工具诱导只匹配注册表里 `read_only=False` 的工具名；`collect_text` 跳过 base64/image/nonce、≤200,000 字。
- **包装**：不可信结果进上下文前包成 `【外部数据 #nonce】…【外部数据结束 #nonce】`，内部前缀替换「【→〔」；flagged 加【安全提示】；blocked 无原始结果；旧消息/非 JSON 原样。
- **打标**：22 个工具 `untrusted_output=True`（附录 A 整集），`read_artifact`/`search_knowledge` 动态 `untrusted`，外置产物 `meta.untrusted`。
- **降权**：同运行从下一步起 `GUARD_TAINT_EXEMPT` 照常、`GUARD_TAINT_DENY`（`remember`）拒绝、`create_ticket`/`send_to_ticket`/`cancel_ticket` 与其它非只读工具逐次强制确认（`payload.guard`），不能确认的直接拒绝并记 `rejected` 审计；子代理继承、历史 flagged 到最近人类消息；`off` 只打标不扫描；`block_on_detect` 整段拦截 `PROMPT_INJECTION_BLOCKED`（审计仍 success）。
- **复核**：`rules_llm` 用 `trigger_eval` 角色 + `injection_verdict` schema（每轮 ≤20 次、输入 ≤6000 字、20s 超时）；`benign≥0.7` 只能撤中低危、高危不可撤；span `guard.scan`/`guard.classify` 与 `agent_run.meta.guard`。
- **回溯**：事务第一条语句标 `superseded_at`；目标校验 7 种 reason；检查点作废/重建、计划放弃；dry_run 不写库；活动运行/待处理 409；副作用逐条列出并写提示消息；WS `session_rewound`。
- **分叉**：同事务复制工单/委托/会话/`seq≤at_seq` 未取代消息/最新有效检查点；`forked_from_*`、notice、`write_brief=False`；不复制订阅/租约/计划/产物。
- **前端**：四动作（回到这里/重新生成/编辑后重发/分叉）含 dry_run 预览、连续被取代消息折叠、GuardBadge 与确认卡警告、运行中禁用、事件失效。

## 验证证据

### 逐任务

| 任务 | 结果 |
|---|---|
| G1 | db/errors/settings 513 passed；迁移 SQL 含两列；`app ok` |
| G2 | guard 82 passed；样例逐字核对 |
| G3 | 7 文件 69 passed；`tests/agent` 1332 passed；22 工具整集 |
| G4 | guard 98 passed；`tests/agent` 1348 passed；cross 27 passed |
| G5 | rewind/fork/api/route 25 passed；`tests/agent/context tests/api` 619 passed；`orchestrator_smoke` `SMOKE OK` + 两新 `[ok]` |
| G6 | gen/typecheck/vitest（106 文件 943 用例）/build/frontend_smoke 全 0 |

### 最终验收（G7，主 agent 亲自跑）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest`（全量） | 首轮 5 failed（3 条基线 + 2 条满载 flake：`test_incidents` 已知、`test_agent_router` 单独/整文件均过）；复验见「合并后复验」 |
| `scripts/dump_openapi.py --check` | 0（166 operations） |
| `scripts/generate_api_guide.py --check` | 0（新端点后重新生成） |
| 前端五步 | 0；vitest 106 文件 **943** 用例；build 正常 |
| `frontend_smoke` / `agent_smoke` / `orchestrator_smoke` | 均 `SMOKE OK`；`agent_smoke` 在与其他全量测试并发时因仓库 DB WAL mtime 变化误报，顺序单独重跑通过（已核对）；`orchestrator_smoke` 含两新步骤 |
| `agent_eval regression` / `capability` replay | 均 `EVAL OK`（6/6，capability 100%） |
| `check_doc_links` / `git diff --check` | `BROKEN 0` / 0 |
| 手动验收（设置页两键、block 模式工具卡「已拦截」） | **未验证**（无常驻前台服务）；自动用例与冒烟覆盖等价逻辑 |

### 计数

- 错误码 155/154→**157/156**；端点 164→**166**（agent 63）；工具 100/99 不变，22 个加 `untrusted_output`；业务表 46 不变；配置键 +2；迁移 head **`0033_rewind`**。

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | 09 §7 按 M80 spec 改写 | spec 为唯一规格；两稿冲突，内部口径统一（13 §8.112） |
| R2 | `injection_verdict` 用 pydantic schema 模块 | 仓库惯例（13 §8.113） |
| R3 | 中间件在 runtime 链第 11 位；包装在 `prepare_history_messages` | 实际装配点（13 §8.114） |
| R4 | 分叉用仓库直写，不走 `TicketService.create` 重算 | 推断委托需逐字段照抄（13 §8.115） |
| R5 | `list_after_seq` 也排除被取代消息 | 否则回溯后下一轮仍读到（13 §8.116） |
| R6 | 错误码/端点/配置计数 | 真值变化（13 §8.117） |
| R7 | 包装后能力评测不降（replay 6/6） | 13 §8.118 |
| R8 | 冒烟新增两步；前端按实际组件接入 | 13 §8.119 |
| R9 | 回溯列与迁移 | 13 §8.120 |

## 偏离（已记 13 §8.112–8.120）

- §8.112 09 口径冲突；§8.113 schema 落法；§8.114 链位/包装点；§8.115 分叉写入口；§8.116 被取代消息过滤；§8.117 计数；§8.118 评测；§8.119 冒烟/前端；§8.120 迁移。

## 遗留

- 真实环境手动验收未做（设置页两键、`block_on_detect=true` 的工具卡「已拦截」）；自动用例与 `orchestrator_smoke` 两步覆盖等价逻辑。
- 降权期间写操作依赖确认服务；真实模型下「逐次确认」的交互体验需人工评估。
- 规则误报/漏报以附录 B 样例为准，编码/加密指令不在范围（M94 可扩展）。
- 全量测试偶发 `test_incidents`（60s 等待）与 `test_agent_router`（满载 e2e）超时，单跑均过，属已知满载 flake。
