# M78 Skills 体系（台账）

- 分支：`skills`；基点：`dev` = `428f53d`；合并：`dev` 上的 `merge: Skills 体系（M78，见 docs/v3/13 §8.94–8.101）`；tag：`v3-m78`
- spec：[m78-skills](../specs/m78-skills.md)（680 行）
- 计划：[m78-skills](../plans/m78-skills.md)
- 决策记录：[docs/v3/13 §8.94–8.101](../13-决策记录.md)
- 迁移：`0031_skills`（可逆；用户技能降级不可还原，spec 已声明）。

## 做了什么

| 任务 | 提交 | 内容 |
|---|---|---|
| T0 | `a8f0a53` | 计划入库；§0 十六条；6 项接缝裁决；基线 `3 failed / 4682 passed` |
| G1（T1） | `1269dfa` | `SkillOrigin`；`AgentSkill` 表；迁移 `0031`（9 个 id 搬覆盖、`when_to_use→description`、downgrade `kind NULL` 回填）；业务表 45；迁移三条用例 |
| G2（T2） | `d24332c` | 9 个 `skills/builtin/<id>/SKILL.md`（frontmatter 6 字段、正文照抄+引用改写）；删 9 条知识（34→25）；3 条知识引用改写；`MIGRATED_TO_SKILLS` 与知识读写重定向 |
| G3（T3） | `51421c0` | `SkillRegistry`（加载/合并/校验/`index_for`/`load`/`read_file`/`render_index`/`validate_preloads`）；`AgentSkillRepository`；11 用例；预置三错误码 |
| G4（T4） | `3e52f9d` | `SkillsMiddleware`（`before_model` 写第 4 段、预加载/压缩补回、激活与工具组、meta 写回）；两个工具；`durable["skills"]`；链位；工具 99/98（refetchable 50）；11 用例 |
| G5（T6） | `fc5aa15` | 8 个定义 tools+2/version+1/提示词 + daily_ops/copilot 预加载；`account.py` 与知识测试引用；`m30_disposition.json`、page pointers、AI 无数字检查覆盖技能正文；`tests/agent tests/api tests/services` 回到仅 3 条基线失败 |
| G6（T5） | `9ac89c3` | 4 端点；错误码 153/152；14 号文档（agent 57 条、总数 160）；`SkillsSettings` + 模板；装配 `skill_registry`/`validate_preloads`/运行时注入/关闭清理；OpenAPI+类型 |
| G7（T7） | `f10ed1d` | 技能列表/编辑页、API/format、路由与导航（`group:'ops'`）、知识页 hint；前端 101 文件 **903** 用例 |
| G8 | 本台账所在提交 | 05/07/13/README 同步；13 §8.94–8.101；本台账；全量验收与合并 |

## 核心实现

- **数据**：`agent_skill`（覆盖行 NULL 列继承内置；user/generated 独立）；迁移 0031 把 9 条 playbook 的用户覆盖搬进技能表并删原知识行（downgrade 回填 `kind NULL`）。
- **内置技能**：9 个 `SKILL.md`（id/title/description/tools/agents/version），目录名=id，加载不合法即中止启动；附属文件由 `read_skill_file` 读（路径/大小守卫，≤64 KiB）。
- **渐进披露**：系统提示第 4 段只列可用技能（`- id｜title：description`）；`load_skill` 返回正文、附属清单、`tools_available`/`tools_unavailable`/`enabled_groups`；`daily_ops` 预加载 `task-preconditions`、`copilot` 预加载 `task-preconditions` + `playbook-stage-clear`；激活写 `agent_run.meta.skills` 并跨轮继承；压缩后正文补回第 4 段（`MAX_INJECTED_BODY_CHARS` 截断提示）。
- **工具组**：`general` 等有按需组的代理加载技能时自动开组；工具集固定的代理只在结果里报告缺的工具，不增声明。
- **知识重定向**：`read_knowledge` 读 9 个 id → `KNOWLEDGE_NOT_FOUND` + `details.skill_id` 指向 `load_skill`；`put_entry` → `KNOWLEDGE_BUILTIN_READONLY`。
- **前端**：技能页按来源分组（内置/自定义/草稿）、徽标、可用代理、开关；编辑页保存/恢复默认/删除与 400 字段标记；导航「技能」。

## 验证证据

### 逐任务

| 任务 | 结果 |
|---|---|
| G1 | db/enums 503 passed；`alembic --sql` 含 3 处 agent_skill；`create_app()` ok |
| G2 | 知识 25、技能 9、旧引用 0；知识模块 import ok |
| G3 | 注册表 11 passed；真实目录 9 个；registry ok |
| G4 | 中间件/工具/目录/链序 47（+parity 单独）；计数 99/98 |
| G5 | `tests/agent tests/api tests/services` **3 failed / 3394 passed**（3 条基线真资源） |
| G6 | 路由/domain/设置 519 passed；openapi/genapi 0；`agent_smoke` `SMOKE OK`；openapi operations 160 |
| G7 | typecheck/vitest 101 文件 903 用例/build/frontend_smoke 全 0 |

### 最终验收（G8，主 agent 亲自跑）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest`（全量） | **3 failed / 4720 passed**；与基线同一组真资源失败，无新增 |
| `scripts/dump_openapi.py --check` | 0（160 operations） |
| `scripts/generate_api_guide.py --check` | 0（新端点后重新生成） |
| 前端五步 | 0；vitest 101 文件 **903** 用例；build 正常 |
| `frontend_smoke` / `agent_smoke` / `orchestrator_smoke` | 均 `SMOKE OK` |
| `agent_eval regression` / `capability` replay | 均 `EVAL OK`（6/6，capability 100%） |
| `check_doc_links` / `git diff --check` | `BROKEN 0` / 0 |
| 手动验收（旧库升级、真实工单先 load_skill、停用后索引消失） | **未验证**（无常驻服务）；自动用例覆盖等价逻辑 |

### 计数

- 工具 97→**99**（模型可见 96→**98**；可重取 49→**50**）；端点 156→**160**（agent 57）；错误码 150/149→**153/152**；业务表 44→**45**；配置键 +1；内置知识 34→**25**、内置技能 0→**9**；迁移 head **`0031_skills`**。

## Rulings I made

| # | 裁决 | 理由 |
|---|---|---|
| R1 | 第 4 段用 `before_model` 注入 | `on_run_start` 会被重建覆盖（13 §8.94） |
| R2 | `durable["skills"]` 保持 id 列表 | 06 与既有测试契约（13 §8.95） |
| R3 | `on_run_end` 直接合并写回 `agent_run.meta` | `finish()` 无 meta 参数（13 §8.96） |
| R4 | nav 加 `group:'ops'` 与 import | 类型要求（13 §8.97） |
| R5 | 计数与门禁联动同步 | 真值变化（13 §8.98） |
| R6 | T6 范围补正 + 测试夹具改造 | 实际文件与 legacy 链（13 §8.99） |
| R7 | 不用 M30 `migrate_deleted_overrides` | 避免覆盖变自定义条目（13 §8.100） |
| R8 | 三错误码先预置、G6 收敛 | 任务依赖顺序（13 §8.101） |
| R9 | 真实环境手动验收记「未验证」 | 无常驻服务；自动用例覆盖 |

## T6 逐文件处理结论

| 文件 | 处理 |
|---|---|
| `test_knowledge_service.py` | 名单 25、迁移集差集、新增重定向/put 拒绝 2 用例 |
| `test_agent_knowledge_router.py` | 25 条；详情改 `screen-home` |
| `test_knowledge_no_numbers.py` | 25 条 + 9 技能正文扫描（键 `skill:<id>`） |
| `test_knowledge_page_pointers.py` | 25+9=34 |
| `test_runtime.py` | `builtin_skills()["playbook-stage-clear"].body` |
| `test_account_resources_flow.py` | `load_skill`；测试内给 legacy 链补 `SkillsMiddleware`（夹具改造） |
| `test_incidents.py` | 改 `screen-home` |
| `m30_disposition.json` | 实际 7/9 个迁移 id，移除后 25 条 |
| `test_m70_specs.py` / `test_builtin_workers.py` / `test_agent_specs_router.py` | 只同步名单与版本期望 |
| 8 个 `specs/builtin/*.md` | tools +2、version +1、提示词逐字、daily_ops/copilot 预加载、引用改写 |

## 偏离（已记 13 §8.94–8.101）

- §8.94 注入时机；§8.95 durable 形状；§8.96 meta 写回；§8.97 前端导航；§8.98 计数联动；§8.99 T6 补正；§8.100 迁移与 M30 关系；§8.101 错误码预置。

## 遗留

- 真实环境手动验收未做（旧库 0031 升级后的技能页「已修改」、真实工单先 `load_skill`、停用后索引消失）；自动用例覆盖等价逻辑。
- 模型是否主动加载技能依赖提示词与工具描述，待能力评测长期观察（spec §8 已记风险）。
- 技能不进检索索引（按 spec），`search_knowledge` 查不到技能。
- 降级会丢用户自建技能（spec §8 已声明，回滚前可导出）。
