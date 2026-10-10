# M30 · 知识库收敛 · 实施计划

规格见 [specs/2026-10-09-m25-prts-knowledge.md](../specs/2026-10-09-m25-prts-knowledge.md) §10（下文「§」都指该文件）。行为以规格为准；规格没覆盖的实现细节由实现者决定并写进报告，主 agent 抄进台账。

## 全局约束

- 工作分支 `m30-knowledge-convergence`（自 `refactor/v2`）。直接在当前检出上工作，不建 worktree，不 push，不合 `dev`。
- 后端测试 `.venv/bin/python -m pytest -q`；前端 `cd web && pnpm typecheck && pnpm test --run && pnpm build`。
- 先写失败测试确认 RED，再实现 GREEN；长输出重定向到文件只读尾部与退出码。
- **实现者不要 `git commit/add/stash/checkout`**；`maa_api/agent/runtime.py`（提示词）、`docs/**` 由主 agent 统一改。
- 本里程碑**不新增工具、不新增依赖**，也不改操作类条目的正文（只修其中引用的数值）。

## 分工与写范围

| 工作流 | 写范围 |
|---|---|
| T1 复核（子 agent） | `.superpowers/sdd/2026-10-10-m30-knowledge-convergence/{t1-review.md,disposition.json}`（只读分析，不改代码） |
| T2a 改写（前半） | `maa_api/agent/knowledge/builtin/` 里分给它的条目文件（见下） |
| T2b 改写（后半） | 同上，另一半条目 |
| T3 删除与覆盖行迁移 | `maa_api/agent/knowledge/{__init__.py,convergence.py}`、`tests/agent/test_knowledge_convergence.py` |
| T4/T5 无数值检查与提示词 | `tests/agent/test_knowledge_no_numbers.py`、`maa_api/agent/runtime.py`（提示词那一段由主 agent 改） |
| 主 agent | 提示词、docs/11 §6、docs/13 新增一节与 §8.24 旁注、docs/12 M30、台账、验收与合并 |

T2a/T2b 的条目按 §10.2 的处置表对半划分，**两个工作流不碰同一个文件**。索引/术语表条目的措辞若需要动，由主 agent 统一改。

## 冻结的口径

- **保留**：操作类条目（MAA 操作、界面识别、本服务规则）与术语表，正文不动（只修其中引用的数值）。
- **改写**：游戏机制类条目改写成「去哪查、查什么、怎么判断」——写明要调用的工具或要读的 PRTS 页面、判断标准与常见坑；**具体数值全部删掉**。每个被删的数值都要能指向一个存在的工具或 PRTS 页面。
- **拆分**：混合条目的操作步骤保留、数值换成工具调用（`plan_upgrade`、`get_drop_rates`、`get_infrast_candidates`、`get_gacha_pools` 等）。
- **删除**：改写后已无操作性内容的条目；用户覆盖过的被删条目迁移成同 id 的自定义条目保留（覆盖行里为 NULL 的列用迁移代码里冻结的最后一版内置内容补齐）；迁移在启动时幂等执行。
- **无数值检查（T4）**：改写后的条目里不得出现「数字 + 游戏单位」（理智、合成玉、源石、龙门币、%、小时、天等），操作类数字按白名单豁免（白名单由 T1 提案、主 agent 定稿）。
- **提示词（T5）**：「知识和画面」段补一句「游戏数据类条目是检索策略，数值以工具现查为准」。
- **docs/11 §6（T6）**：改写为三层——稳定规则 + 用户档案与账号数据 + 实时检索。

## 验收（规格 §10.3）

1. §5.15 的 1–3 条（`.venv/bin/python -m pytest -q`、生成物三处 `--check`、前端 `typecheck/test/build`）；
2. `render_context()` 快照：索引行数与条目数一致、没有数值；
3. 覆盖行迁移测试（用户改过的被删条目迁移成自定义条目、内容与来源正确、幂等）；
4. T1 的复核报告与处置表归档到台账；
5. `scripts/copy_inventory.py --check` 与 `agent_smoke.py`、`frontend_smoke.py` 末行 `SMOKE OK`；
6. `git merge --no-ff` 回 `refactor/v2` + tag `v2-m30`。

## 不做

不新增工具；不改操作类条目的正文（只修其中引用的数值）；不引入新依赖。
