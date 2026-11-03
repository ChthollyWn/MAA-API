# M19 · 内置知识库扩充实施计划

规格见 [specs/2026-09-29-m19-agent-knowledge-expansion.md](../specs/2026-09-29-m19-agent-knowledge-expansion.md)。行为以规格为准；规格没覆盖到的实现细节由实现者决定并写进报告，主 agent 抄进台账。

## 全局约束

- 工作分支 `m19-agent-knowledge-expansion`（已从 `refactor/v2` 建立）。直接在当前检出上工作，不建 worktree，不 push，不合入 `dev`。
- 事实来源**只有 PRTS**。每条写进条目的断言都要有「结论 + URL + 原文引用」，落在 `.superpowers/sdd/2026-09-29-m19-agent-knowledge/research/`。
- **不改** M18 已交付的 5 条内置条目（`account-resources`、`screen-home`、`screen-depot`、`gacha-conversion`、`glossary`）与 M18 规格 §4.6。
- 条目硬约束：`id` 匹配 `^[a-z0-9][a-z0-9-]{1,47}$`；`title` ≤32 字；`when_to_use` ≤120 字；`body` ≤20,000 字；frontmatter 只允许 `id`/`kind`/`title`/`when_to_use`；正文首尾无空行。
- 内置条目总数 31 ≤ 50；`glossary` + `glossary-terms` 正文合计 ≤ 3,000 字。
- 后端测试：`.venv/bin/python -m pytest -q`（`python` 不在 PATH）。
- 长输出重定向到文件，只读尾部和退出码。
- **实现者不要 `git commit` / `git add` / `git stash`**；提交由主 agent 统一做。
- 不引入新依赖。

任务依赖：Task 1 的五个域互相独立，可并行。Task 2 依赖 1，Task 3 依赖 1、2，Task 4 依赖 3，Task 5 依赖全部。

## Task 1 · 撰写 26 条内置条目（5 个域并行）

**产物**：`maa_api/agent/knowledge/builtin/` 下 26 个新 `.md` + `.superpowers/sdd/2026-09-29-m19-agent-knowledge/research/{gacha-economy,sanity-combat,progression,recruit-infrast,ui-events-glossary}.md`。

域划分见规格 §4.1 的表。编写规范在 `.superpowers/sdd/2026-09-29-m19-agent-knowledge/authoring-guide.md`。

**完成判据**：每个文件可被 `builtin_entries()` 解析；每条断言有出处；界面类条目不写死坐标。

## Task 2 · 独立事实核查

**产物**：`.superpowers/sdd/2026-09-29-m19-agent-knowledge/review-1-facts.md`。

由**没有参与撰写**的 agent 做对抗式复核：

- 逐份读 research 笔记，**重新抓取**被引用的 PRTS 页面，核对「原文引用」是否真实存在、结论是否被原文支持；
- 重点核对五类关键数值：寻访基础概率与保底递增、理智上限与恢复速度、公开招募必出组合、剿灭每周合成玉上限、精英化/专精的条件与量级；
- 核对条目正文里的每个数字都能在自己的来源笔记里找到对应断言；找不到的列为「无出处」；
- 核对界面类条目没有编造坐标；
- 输出：PASS 的断言数、**逐条列出有问题的断言**（条目 id + 断言 + 问题 + 建议改法）。

## Task 3 · 规格与测试同步

**产物**：`docs/superpowers/specs/2026-09-29-m19-agent-knowledge-expansion.md` 的 §4.6、`tests/agent/test_knowledge_service.py`。

- 用脚本把 26 个新条目文件**逐字**生成进 M19 规格的 §4.6（`<!-- M19-BUILTIN-BLOCKS -->` 占位处），保证「规格 = 文件」；
- 测试：`BUILTIN_IDS` 加 26 个 id；`BUILTIN_LIST_ORDER` 按 kind（playbook/screen/rule/glossary/profile）再 id 重排；`_spec_blocks()` 改成读**两份规格**（M18 §4.6 + M19 §4.6）并断言并集恰好覆盖全部内置文件；
- 运行 `.venv/bin/python -m pytest -q tests/agent/test_knowledge_service.py tests/agent` 退出码 0。

## Task 4 · 文档同步

**产物**：`docs/11-Agent模块设计.md`、`docs/12-实施计划与里程碑.md`、`docs/13-决策记录.md`、`docs/README.md`。

按规格 §7 逐条落实。docs/13 §8.24 记本里程碑的用户裁决、事实来源纪律、Token 成本口径与实现裁决。

## Task 5 · 验收（主 agent 亲自跑）

- `.venv/bin/python -m pytest -q`；
- `.venv/bin/python scripts/dump_openapi.py --check`、`.venv/bin/python scripts/generate_api_guide.py --check`、`cd web && pnpm gen:api:check`（应无变化）；
- 用脚本核对：内置条目 31 条、`render_context()` 索引含全部 31 条、`read_knowledge` 能读到新条目、全文段合计 ≤ 3,000 字；
- 亲自抽查至少 5 条关键数值（与 PRTS 页面比对）；
- 完成后 `git merge --no-ff` 回 `refactor/v2`，打 tag `v2-m19`，台账归档到 `docs/superpowers/ledgers/2026-09-29-m19-agent-knowledge-expansion.md`。
