# M30 进度台账

计划：`docs/superpowers/plans/2026-10-10-m30-knowledge-convergence.md`
规格：`docs/superpowers/specs/2026-10-09-m25-prts-knowledge.md` §10（下文「§」都指该文件）
分支：`m30-knowledge-convergence`（自 `refactor/v2`），完成后 tag `v2-m30`

## 任务状态

| 任务 | 状态 | 产物 |
|---|---|---|
| T1 逐条复核（两个子 agent，A–L 15 条 / M–Z 17 条） | 完成 | `.superpowers/sdd/2026-10-10-m30-knowledge-convergence/` 的 `t1-review-a.md`、`t1-review-b.md`、`disposition-a.json`、`disposition-b.json`；两列快照归档为 `tests/fixtures/knowledge/m30_disposition.json`（32 条 / 369 个待删片段，见同目录 README） |
| T2a/T2b/T2c 条目改写（三个子 agent，写范围互不相交） | 完成 | `maa_api/agent/knowledge/builtin/` 28 个文件有 diff（32 条中 4 条未动正文） |
| T3 删除与覆盖行迁移 | 完成 | `maa_api/agent/knowledge/convergence.py`、`tests/agent/test_knowledge_convergence.py`（10 用例）、`maa_api/main.py` lifespan 装配 |
| T4「无数值」检查与页面指引 | 完成 | `tests/agent/test_knowledge_no_numbers.py`（9 用例）、`tests/services/test_knowledge_page_pointers.py`（4 用例）、`tests/fixtures/prts/page_titles.json`、`tests/fixtures/prts/README.md` |
| T5 提示词 | 完成 | `maa_api/agent/runtime.py`「知识和画面」段末尾一句 |
| T6 docs/11 §6 改写为三层 | 完成 | `docs/11-Agent模块设计.md` §6（§6.1 稳定规则 / §6.5 用户档案与账号数据 / §6.6 实时检索 / §6.7 注入顺序与信任边界）、`docs/04-数据模型与持久化.md` §5.16 的 M30 迁移语义、`docs/README.md` M30 一行、`docs/05-API规范与路由清单.md` §4.17 的两处交叉引用 |
| 文档：决策记录与验收 | 完成 | `docs/13-决策记录.md` §8.45（七小节）与 §8.24 旁注 |
| 台账 + 验收 + 合并 | 完成 | 本文件；验收结果见末节 |

## 实测结论

- **条目数与处置定稿**：`builtin/*.md` 实测 **32 条 = 保留 11 + 改写 15 + 拆分 6 + 删除 0**（任务书写「`screen-*` 九个」「共 18 个」，实际 8 个 / 17 条；15 + 17 = 32 与 `load_builtins()` 一致）。
- **工具数必须用 `build_registry()` 数**：实测 **75 条**；`grep 'name="' maa_api/agent/tools/*.py` 只数出 68，因为 `back_to_home` / `click` / `input_text` / `key_event` / `long_press` / `swipe` / `trigger_screencap` 在 `raw.py` 用变量拼名。
- **PRTS 实测存在**（`action=query&redirects=1`）：`理智`、`信赖值`、`公开招募`、`采购中心`、`采购中心/凭证交易所`（8 个 section）、`罗德岛基建` 及八个子页、`罗德岛基建/会客室`、`无人机`、`寻访规则`、`寻访凭证`、`关卡一览` / `关卡一览/常态事务` / `关卡一览/曲谱` / `关卡一览/资源收集`（sections = 物资筹备 / 芯片搜索）、`乌萨斯 切尔诺伯格`、`炎国 龙门外环`、`炎国 龙门市区`、`游戏数据基础`、`干员一览`、`分支一览`、`干员模组一览`、`集成战略`、`事相结晶`、`任务列表`、`至纯源石`、`道具一览`、`技能`、`声望`。
- **实测 404（改写稿不得指向）**：`潜能`（无独立页，只能 `get_operator(sections=["potentials"])`；实测「能天使」返回 `潜能2`…`潜能6`）、`寻访`、`精英化`、`心情`、`卡池`、`悖论模拟`、`模组系统`、`分支`、`稀有度`、`作战消耗`、`推荐等级`、`代理指挥`、`演习`、`突袭`、`倍战`、`材料一览`（应为 `道具一览`）、`货物运送` / `芯片搜索`（是 `关卡一览/资源收集` 的 section，不是独立页）、`资质凭证区` 等四个（是 `采购中心/凭证交易所` 的 section 名）。
- **`.venv/bin/python` 直连 prts.wiki 报 `SSL: CERTIFICATE_VERIFY_FAILED`**，本机只有 `web_fetch` 通；T1 的页面可达性因此用 web_fetch 实测。
- **工具字段口径**：`get_stage` 的 basic 字段是 `sanity` / `challenge_practice_sanity` / `has_challenge` / `challenge_condition` / `recommend_level`（不是 `consume`/`ap_cost`）；`get_drop_rates` 行含 `rate` / `ap_cost` / `sanity_per_item`（`include_closed` 默认 False）；`get_item` 含 `recipe` + `drop_rates`。
- **T4 扫描结果：0 命中**（32 条 953 行正文）。索引行快照（`render_context()`）30 行也无命中。

## Rulings I made

1. **处置定稿与规格 §10.2 的三处修订**（以实现为准，规格与计划同步）：① `activity-stage-codes` 拆分→**保留**（M25 已把数值换成工具调用，拆分对象不存在）；② `glossary-terms` 保留→**改写**（`kind: glossary` 走全文注入，不改则 T4 必挂）；③ 两个删除候选 `operator-classes`、`trust-and-potential` → **改写**（口语→检索词映射与信赖/潜能的操作性内容不可替代，且每个数值都有实测落点；删除还要额外承担 T3 覆盖行迁移的风险，代价不对称）。
2. **「拆分」的定稿含义 = 把数值段落换成工具调用，不是拆文件**：六个 `playbook-*` 保持单文件、单 id、`kind: playbook` 与自己的流程骨架。
3. **`★` 算白名单**：否（豁免），否则三条稀有度规则删掉 `★` 后不可读。保留桶里的 8 条 `screen-*` 按 §10.4 仍需删掉其中引用的数值，不是零改动条目。
4. **白名单口径（14 条，主 agent 定稿）**：只豁免「操作类数字」，判据是「命中片段整个落在某条白名单区间内」，取最短包含区间；三条关键收窄——UI 比例必须带「横向/纵向/宽/高」或「的比例/坐标」（裸 `%` 不豁免）、行首 Markdown 标记只豁免标记不整行、单位表不含「次/笔/名/级」这类机制计数词。
5. **T4 判据宁漏不误**：要求数字与单位相邻（只允许空白、顿号、斜杠等分隔），「135 点理智」这类中间夹汉字的会漏报，已写进测试注释。
6. **T3 的纪律**：冻结表（`FROZEN_BUILTIN_ENTRIES`）与删除条目必须**同一次发布**——先删条目、冻结表留到下版，覆盖过该条目的用户会读到残缺覆盖行，`_merge` 抛 `RuntimeError("自定义知识条目 ... 的字段不完整")`，**整个知识库读不出来**。本版删除 0 条，冻结表是空 dict、启动时迁移为空操作。
7. **反例来源改为仓库内快照**：T4 的反向用例原读 `.superpowers/sdd/**/disposition-*.json`，而 `.superpowers/` 是 gitignore 的临时工作区（清理即挂）。改为优先读 `tests/fixtures/knowledge/m30_disposition.json`（只含 `source`/`id`/`numbers_to_remove` 三列），SDD 路径保留为回退。
8. **三层口径与优先级**：`安全规则 > 画面 > 稳定规则 > 档案`；数值冲突时 `工具现查 > 截图 > 条目`。第一、二层条目只写「去哪查、查什么、怎么判断」，第三层（PRTS、企鹅物流、作业站、拆包数据）只在工具调用时付费、不进 system 消息。
9. **不新增任何上限旋钮**：`MAX_FULLTEXT_CHARS` 与 `MAX_ENABLED_ENTRIES` 已在 M20 删除，本次不复活；全文段成本由用户自己停用或改短条目控制。

## 实施中修掉的问题

1. **docs 子 agent（`73c76f95`）报告失败但改动已落盘**：`docs/04` §5.16、`docs/11` §6 三层改写、`docs/README.md` M30 一行都在工作区且内容完整，主 agent 逐处复核 diff 后采用，未重做（该 agent 只是没留下收尾消息）。
2. **front matter 逐字比对**：`tests/agent/test_knowledge_service.py` 的 `test_builtin_files_match_the_spec_registry_and_frontmatter` 把每条 front matter 与三份规格 §4.6 的块逐字比对；T2a 一度改短 `headhunt-rules` 的 `when_to_use` 导致红，已改回（正文可动、契约字段不可动）。
3. **三处陈旧断言**：`playbook-drop-lookup`「没有掉率计算器」、`playbook-promotion-planning`「没有查升阶材料的工具」、`playbook-infrast-shift`「没有读基建方案的工具」在 M25–M29 之后已经是错的，一并改成正向工具入口。
4. **交叉引用随改写失效**：`docs/05` §4.17 原指「11 §6.6」（旧编号的注入与信任边界），账号数据现在是 §6.5，已改；其余 `11 §6.6` 引用（外部来源降级、来源开关）仍指向实时检索层，保持不变。
5. **`render_context()` 的两处 docstring** 仍写着 M18 口径「索引 + 术语表 + 档案」，补上 M24/M28/M27 的三段与「读不到整段不插」，并指向 docs/11 §6.7。
6. **`docs/13` §8.45 的一条事实错误**：原写「`MAX_FULLTEXT_CHARS` 与 `MAX_ENABLED_ENTRIES` 都不动」，实际这两个常量在 M20 已随「不设自设上限」删除，改为「没有旋钮可调，只有 `body` ≤ 20,000 字的列宽约束」。

## 偏离与遗留（详见 `docs/13-决策记录.md` §8.45 七）

1. 4 处 `screen-*` 受条目长度预算限制，只删掉了数字、没附上指引（写「以游戏内为准」）。
2. 多数游戏数值没有对应工具，只能靠 `get_prts_page(title=…, section=…)`，而 section 名依赖线上版本；T1-b 建议的「优先只写页面名 + 关键词」是否全量遵守未复核。
3. 离线时这些条目给不出具体数字（§10.1 的取舍，Q15：取不到就回「取不到」，不凭记忆填），`gacha-conversion` 受影响最明显。
4. 未实测 `get_prts_page` 在真实配置（`sources.use_proxy=true` / 缓存）下的取数结果；T1 建议补一个把实测标题喂给 `search_prts` / `get_prts_page` 的夹具测试（本轮只做了「正文引用的标题与工具都在册」的静态断言）。

## 主 agent 亲自复跑的验收（2026-10-10，最终提交上）

- 后端 `.venv/bin/python -m pytest -o addopts= -q` → **2 failed, 3476 passed, 5 skipped**，178 s，退出码 1；两条失败是 M24 起就存在的真资源用例（`tests/services/test_activity_index.py` 的 `test_real_resource_pack_reports_the_current_activity_and_its_stages` 与 `test_real_resource_pack_disambiguates_a_repeated_code`），与本里程碑无关（本机 `resource/` 快照）。新增用例 23 个全绿：`test_knowledge_convergence.py` 10 + `test_knowledge_no_numbers.py` 9 + `test_knowledge_page_pointers.py` 4（对照 M29 的 3453 passed）。
- 生成物：`scripts/dump_openapi.py --check`、`scripts/generate_api_guide.py --check`、`scripts/generate_task_types_fixture.py --check`、`cd web && pnpm gen:api:check` 退出码全 0（未重新生成即通过）。
- `scripts/copy_inventory.py --write-baseline` 重设为 45 项后 `--check` 通过（「无回潮，0 项较基线下降」）。
- 前端 `pnpm typecheck && pnpm test --run && pnpm build` 退出码 0：70 文件 / 711 项通过，构建 570 ms + 443 ms，含 service worker harness 的 `SERVICE WORKER OK`。
- 冒烟五个末行 `SMOKE OK`：`scripts/agent_smoke.py`、`scripts/frontend_smoke.py`、`scripts/account_smoke.py`、`scripts/copilot_research_smoke.py`、`scripts/prts_smoke.py --m28`（真网络，只作补充证据）。
- T4：32 条内置条目 953 行正文扫描 **0 命中**；`render_context()` 索引行 30 行无命中；反向用例 39 条真实片段 + 24 条手写反例全部被命中，白名单 14 条每条都真豁免过语料。
- 合并：`git merge --no-ff` 回 `refactor/v2` + tag `v2-m30`（不 push、不碰 `dev`）。
