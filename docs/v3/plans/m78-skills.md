# M78 Skills 体系 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 v2 的 9 条 `playbook` 知识改造成技能（V3-D44）：`agent_skill` 表 + 迁移 0031 搬覆盖；9 个内置 `SKILL.md`（知识库 34→25）；总控与 7 个 Worker 系统提示第 4 段技能索引（预加载 daily_ops/copilot、加载态跨轮继承、压缩后补回）；`load_skill`/`read_skill_file` 工具与按需工具组；`read_knowledge` 对 9 个 id 指向技能；4 个技能端点 + 前端技能页；M30 检查覆盖技能正文。

**Architecture:** G1 表/迁移（T1）与 G2 技能文件+知识瘦身（T2）并行 → G3 注册表/仓储（T3）→ G4 中间件/工具/确定性保存（T4）→ G5 受影响的测试与 8 个定义（T6）→ G6 REST/错误码/设置/装配（T5）→ G7 前端（T7）→ G8（我：T8 文档与验收）。

**Spec:** [m78-skills](../specs/m78-skills.md)（680 行）

## 开工基线（2026-11-05）

- 分支 `skills`，基点 `dev` = `428f53d`（M77 合并）；tag `v3-m77`；迁移 head `0030_knowledge_index`，下一号 `0031`。
- §0：内置知识 34 条（9 条 playbook）、8 个定义、`skills` 字段已存在、`AgentSpecRegistry.list()` 可列举、链组装在 `runtime.build_agent_chain`、`SYSTEM_SEGMENT_ORDER` 已有 `skills` 槽位（第 4 段）、`DurableState.skills` 已预留、`RunService.start(meta=)` 可写、`runtime.enable_tool_groups` 可开组、前端 nav/router 可挂载。基线 `3 failed / 4682 passed`。
- **接缝裁决（记 13 §8.94–8.99）**：
  1. **第 4 段写入必须用 `before_model`**：`ContextLayoutMiddleware` 每轮 `before_model` 重建 system 段，`on_run_start` 无法写；`SkillsMiddleware` 按 spec 的 `on_run_start/wrap_tool/on_run_end` 之外**加 `before_model`**，照 `PlanMiddleware` 的 `req.meta["layout_parts"]` → `put_segment("skills", text)` → `layout.apply()` 写法（spec 方法表未列，属实现细节）。
  2. **`ctx.state["skills.active"]` 与 `DurableState.skills` 形状**：状态键照 spec 用 `skills.active`（每项 `{"id","version","loaded_seq"}`）；`durable["skills"]` 沿用既有 `list[str]`（06 文档与既有测试的契约），填激活技能 id，不改成 `{id,title,version}`。
  3. **`agent_run.meta` 落库**：`RunService.finish()` 无 `meta` 参数、无 `update_meta`；`on_run_end` 直接读运行行、合并 `meta["skills"]` 后写回（走仓储/会话，不改 RunService 签名）。
  4. **前端 nav 项**：`secondaryNav` 元素类型要求 `group`，`BookMarked` 未 import；加 `group: 'ops'` 并补 import。
  5. **计数联动**：工具 97→**99**（模型可见 96→**98**，`load_skill`/`read_skill_file` 都 `rest_visible=False`，需同步 `test_extension_fields_keep_v2_defaults` 的 v3_only 集合；`read_skill_file` 可重取，`EXPECTED_REFETCHABLE` 49→**50**）；错误码 150/149→**153/152**；端点 156→**160**（14 号文档 §6.15 agent 53→**57**，总数句同步）；业务表 44→**45**；配置键 +1；内置知识 34→**25**、内置技能 0→**9**。
  6. **T6 范围补充**：除 spec 表列文件外，`tests/fixtures/knowledge/m30_disposition.json`（9 条反向用例夹具）与 `tests/services/test_knowledge_page_pointers.py`（34→25+9）也在受影响清单；`tests/agent/test_knowledge_no_numbers.py` 的扫描要同时覆盖 9 个技能正文。逐个文件结论进台账。

## Global Constraints

- 迁移 0031 可逆：upgrade 建表 + 搬 9 行覆盖 + 删原知识行；downgrade 覆盖行回知识库（`kind NULL`）后删表（用户技能不可还原，spec 已声明）。
- 不改 `legacy_chat.md` 与 `runtime.py` 的 `SYSTEM_PROMPT`；技能不进检索索引；用户自建 `playbook` 知识不迁移；工具集固定的代理不因加载技能增加工具声明。
- 技能 frontmatter 恰好 6 字段（id/title/description/tools/agents/version），正文照抄原知识并改引用为「见技能 <id>」。
- 所有技能均 `trusted` 无关；设置关时无索引小节、`load_skill` 报关闭；预加载不开放时启动失败并写明代理与技能。
- 一律 `.venv/bin/python`；没有 `rg` 用 `grep -rn`；pytest.ini 已有 `-q`，不加 `-q`；长输出 `/tmp/m78/`。
- 不 push；提交 `<类型>(m78): <中文摘要>`；最后合并回 `dev` 打 `v3-m78`。

## Review Focus

1. **迁移**：只搬 9 个 id 的覆盖行（含停用）；`when_to_use→description`、`kind` 丢弃；downgrade 恢复 `kind NULL`；空库可升级。
2. **内置加载**：9 个目录/SKILL.md、frontmatter 6 字段与 tools/agents 一致、目录名=id、重复/多余/超长/缺失都响亮失败；文件大小与附属文件 64 KiB 上限。
3. **注册表**：内置+override 合并（NULL 继承、modified、enabled）、user/generated 独立、孤儿 override 跳过、`put_skill` 差异列与删行规则、校验错误 `SKILL_INVALID` details、`index_for` 过滤（设置/启用/active/agents）、`load` 三种失败消息、`read_file` 路径与大小守卫、`validate_preloads`。
4. **中间件**：索引第 4 段逐字、预加载正文、压缩后补回（`loaded_seq <= upto`）、跨轮继承、二次加载 `already_loaded`、deferred 工具组开启与 `tools_available/unavailable`、设置关无注入、durable 含激活 id。
5. **工具**：`load_skill` 返回正文/工具/附属/12 位版本、`read_skill_file` 返回内容；REST 不可见、非可重取/可重取属性正确。
6. **REST/设置/装配**：4 端点与错误码（404/409/400/422）、写操作日志、时间 `Z`；`skills.enabled` 设置与模板；真实 lifespan `GET /api/agent/skills` 9 项、`validate_preloads(daily_ops/copilot)` 不报错。
7. **知识库重定向**：`read_knowledge("playbook-recruit")` → `KNOWLEDGE_NOT_FOUND` 指向 `load_skill`；`put_entry` 9 id → `KNOWLEDGE_BUILTIN_READONLY`；其余 25 条行为不变。
8. **前端**：技能列表分组/徽标/代理显示/开关、编辑保存/恢复默认/删除、错误字段标记、路由与导航、知识页 hint。

---

### Task 0: 分支、§0、计划入库（已完成）

- [x] 建 `skills`；§0 十六条；6 处接缝裁决写入本计划；提交计划。

### Task 1（G1）: 枚举、表与迁移（spec T1）

**Files:** `maa_api/domain/enums.py`、`tests/domain/test_enums.py`、`maa_api/db/models.py`、新建 `maa_api/db/migrations/versions/0031_skills.py`、`tests/db/test_models.py`、新建 `tests/db/test_skills_migration.py`

- [ ] `SkillOrigin`；`AgentSkill` 表（列/类型/无索引，放 `KnowledgeChunk` 之后）；迁移 0031（可逆、空库可升、down 恢复 `kind NULL`）；`test_models` 登记（业务表 45、Text/JSON 列）；枚举期望。
- [ ] 验证：`pytest tests/db tests/domain/test_enums.py` → 0；`alembic upgrade head --sql | grep -c agent_skill` ≥1。
- [ ] 提交：`feat(m78): 技能枚举、表与 0031 迁移`

### Task 2（G2）: 9 个技能文件与知识库瘦身（spec T2）

**Files:** 新建 `maa_api/agent/skills/builtin/<9 id>/SKILL.md`、删除 `maa_api/agent/knowledge/builtin/<9 id>.md`、`maa_api/agent/knowledge/__init__.py`、3 条知识正文引用（drop-efficiency/recruit-tags/screen-terminal）

- [ ] 9 个 SKILL.md（frontmatter 6 字段与映射表、正文照抄原知识并改「见技能 <id>」引用）；`git rm` 9 个知识文件；知识模块加 `MIGRATED_TO_SKILLS`/重定向/写保护/docstring。
- [ ] 验证：`ls knowledge/builtin/*.md | wc -l` = **25**；`ls skills/builtin/*/SKILL.md | wc -l` = **9**；旧 id 引用（排除 frontmatter id 行）为 **0**。
- [ ] 提交：`feat(m78): 内置技能文件与知识库瘦身`

### Task 3（G3）: 技能注册表与仓储（spec T3）

**Files:** 新建 `maa_api/agent/skills/{__init__,registry}.py`、新建 `maa_api/db/repositories/agent_skill.py`、新建 `tests/agent/test_skill_registry.py`

- [ ] 常量/类型/`load_builtin_skills`/`builtin_skills`/`SkillRegistry` 全套（合并/校验/index/load/read_file/render_index/validate_preloads）；`AgentSkillRepository`。
- [ ] 验证：`pytest tests/agent/test_skill_registry.py` → 0；真实目录加载 9 个、构造 registry 不报错。
- [ ] 提交：`feat(m78): 技能注册表与仓储`

### Task 4（G4）: 中间件、工具与确定性保存（spec T4）

**Files:** 新建 `maa_api/agent/skills/middleware.py`、新建 `maa_api/agent/tools/skills.py`、`tools/__init__.py`、`context/durable.py`、`runtime.py`（链位）、`tests/agent/test_tool_catalog.py`、新建 `tests/agent/test_skills_middleware.py`、`tests/agent/test_skills_tools.py`

- [ ] `SkillsMiddleware`（`before_model` 写第 4 段、索引/预加载/压缩后补回、wrap_tool 激活与工具组、on_run_end 写 meta）；两个工具（REST 不可见、可重取属性）；`DurableContextMiddleware` 填 `durable["skills"]`（id 列表）；链位在 ContextLayout 之后、Durable 之前。
- [ ] `test_tool_catalog` 计数 99/98、v3_only 与 refetchable 50。
- [ ] 验证：`pytest tests/agent/test_skills_middleware.py tests/agent/test_skills_tools.py tests/agent/test_tool_catalog.py` → 0。
- [ ] 提交：`feat(m78): 技能中间件、加载工具与压缩保留`

### Task 5（G5）: 受影响的测试与 8 个定义（spec T6）

**Files:** 8 个 `maa_api/agent/specs/builtin/*.md`、`maa_api/agent/tools/account.py`（引用）、`tests/agent/{test_knowledge_service,test_knowledge_no_numbers,test_runtime,test_account_resources_flow,test_incidents}.py`、`tests/api/test_agent_knowledge_router.py`、`tests/services/test_knowledge_page_pointers.py`、`tests/fixtures/knowledge/m30_disposition.json`

- [ ] 8 个定义 tools +2/version+1/提示词逐字；daily_ops/copilot 的 `skills`；9 id 引用改写；按 spec 表 + 裁决 6 逐个改测试与夹具。
- [ ] 验证：`pytest tests/agent tests/api tests/services` → 0（允许基线 3 条真资源失败）。
- [ ] 提交：`feat(m78): 代理定义与受影响引用`

### Task 6（G6）: REST、错误码、设置与装配（spec T5）

**Files:** `domain/errors.py`、新建 `api/routers/skills.py`、`main.py`、`settings.py`、`services/settings_schema.py`、`config.template.yaml`、`tests/test_settings.py`、`docs/v3/14-现行接口清单.md`、`tests/domain/test_errors.py`、新建 `tests/api/test_skills_router.py`、`web/openapi.json`、`web/src/types/api.d.ts`

- [ ] 3 错误码（404/409/400，计数 153/152）；4 端点；14 号文档 §4 +3、§6 agent +4（53→57）与总数 156→160；`SkillsSettings(enabled)` + `_FIELDS`/模板/测试；装配 `skill_registry`、`validate_preloads`、`SkillsMiddleware`；快照/类型。
- [ ] 验证：`pytest tests/api/test_skills_router.py tests/domain tests/test_settings.py tests/test_vapid_settings.py` → 0；`dump_openapi --check` 0；`pnpm gen:api:check` 0。
- [ ] 提交：`feat(m78): 技能端点、错误码、设置与装配`

### Task 7（G7）: 前端（spec T7）

**Files:** 新建 `web/src/features/skills/{api,format,SkillsPage,SkillEditorPage,skills.test}.*`、`routes/skills/{index,editor}.tsx`、`routes/router.tsx`、`components/layout/nav.ts`、`features/settings/knowledge/KnowledgeSection.tsx`

- [ ] 技能列表/编辑页（分组/徽标/代理/开关、保存/恢复默认/删除、错误字段、内置原文折叠）；路由 + 导航（`group:'ops'`、`BookMarked`）；知识页 hint；前端用例 §6 四条。
- [ ] 验证：`pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` → 0；`frontend_smoke` `SMOKE OK`。
- [ ] 提交：`feat(m78): 技能页与导航`

### Task 8（G8，我）: 评测、文档、全量验收与合并（spec T8/§9）

**Files:** `tests/evals/scenarios/*.yaml`（如有 9 id 引用）、`docs/v3/{02,05,07,13,14,README}`、新建 `docs/v3/ledgers/m78-skills.md`

- [ ] §7 全量：pytest 不新增失败；openapi/前端五步；三冒烟；regression/capability；断链；diff；T8 的评测场景引用检查。
- [ ] 07 §4 与实现同步；05 §3.2 工具与预加载；13 §8.94–8.99；README；台账（基线、裁决、T6 逐文件结论、手动项未验证说明）。
- [ ] 提交 `docs(m78): 台账、契约同步与状态行`；`git merge --no-ff skills` → tag `v3-m78` → 删分支 → 合并后复验 → 汇报。
