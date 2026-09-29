# SDD ledger — plan: docs/superpowers/plans/2026-09-29-m19-agent-knowledge-expansion.md

## Setup

- Base：`1d81caa`（M18 合并回 `refactor/v2` 的合并提交），分支 `m19-agent-knowledge-expansion`（从 `refactor/v2` 新建，直接在当前检出上工作）。
- Spec：`docs/superpowers/specs/2026-09-29-m19-agent-knowledge-expansion.md`；计划：`docs/superpowers/plans/2026-09-29-m19-agent-knowledge-expansion.md`。提交 `b668ceb`。
- 用户指令（2026-09-29）：「通过 prts 的 wiki 内容，来对知识库进行补充，现在的知识库内容太少了，我希望这个内置 agent 要变成一个资深专业的明日方舟玩家。」
- 基线：M18 交付 5 条内置条目（索引 218 字、术语表 157 字）。后端全量 pytest 在 M18 收尾时 1886 passed / 4 skipped。

## Preflight plan scan

| 依赖 | 结论 |
|---|---|
| 5 个撰稿域互相独立 | 可并行：条目文件互不相交，研究笔记各自一份 |
| 撰稿 → 核查 | 必须串行：核查要读成品与来源笔记 |
| 核查 → 规格块生成 | 必须串行：规格 §4.6 是从**最终**条目文件逐字生成的 |
| 规格块 → 测试 | 串行：`_spec_blocks()` 要读两份规格 |
| 测试 → 文档 | 串行：docs/11 要写最终条目数与成本口径 |

## Rulings I made

- Ruling: 本里程碑**不改 M18 已交付的 5 条内置条目，也不改 M18 规格 §4.6**。逐字一致性测试改成读「M18 §4.6 + M19 §4.6」两份规格的块并集。代价：内置知识的「原文」分布在两份规格里，读的人要知道去看 M19。
- Ruling: 新增术语表用**新 id `glossary-terms`**，而不是改写 M18 的 `glossary`。多条启用的 glossary 会按 id 顺序全部注入，是 M18 已有的语义。代价：术语表在设置页分成两行。
- Ruling: 规格 §4.6 的 26 个逐字块**由脚本从条目文件生成**（而不是先写规格再抄进文件）。理由：内容来自 PRTS 研究，先有成品更自然，且生成保证「规格 = 文件」。代价：对新增条目而言，这条测试退化为「防漂移」而不是「照规格实现」。
- Ruling: **`MAX_FULLTEXT_CHARS` 保持 3,000 不动**。扩充后的 glossary（`glossary` 157 + `glossary-terms` 2,281）占 2,438，用户档案剩约 560 字。判断：档案是「默认账号、偏好」级别的短文本，560 字够用；上调上限是改变 M18 已文档化的对外限制，应由用户决定（已在收尾报告里提出）。
- Ruling: `screen-*` 条目**不写具体坐标**。PRTS 没有界面布局资料，M18 的 `screen-home` 那组比例来自用户自己的归档截图；新条目按「相对位置 + 必须截图确认」写，宁可少写也不编。代价：Agent 进这些界面时要先截图，多一步。
- Ruling: 事实核查发现的问题分两类处理——「PRTS 明确与条目矛盾」或「数字无出处」的**直接改**；「信息不全」但结论无误的列为待改，由主 agent 逐条决定。核查者只被授权改第一类。

## Tasks

| Task | 产物 | 结果 |
|---|---|---|
| 1 · 撰写 26 条内置条目 | `builtin/*.md` ×26 + `research/*.md` ×5 | 完成（5 域并行） |
| 2 · 独立事实核查 | `review-1-facts.md` | 完成：277 断言，PASS 267 / 问题 9 / 无法核对 1 |
| 3 · 规格块与测试同步 | M19 规格 §4.6、两个测试文件 | 完成，13 个失败用例全部按「改期望值 / 改构造」处理，无一处削弱断言 |
| 4 · 文档同步 | docs/11、12、13、README | 完成 |
| 5 · 验收（主 agent） | 见下 | 完成 |

## Task progress

- Task 1：5 个域并行完成，共 26 个条目 + 5 份来源笔记。实测：31 条全部可解析、无长度违规；全文段 2,438 / 3,000；索引 1,892 字（M18 是 218 字）。
- Task 2：独立核查（未参与撰写）重新抓取 PRTS 逐条核对，277 个断言里 267 PASS。**点名的载荷数值全部逐字命中**：寻访 2/8/50/40% 与 50 抽后 +2%、跨池继承；理智 Lv.117 上限 210、6 分钟 1 点、源石 135、药剂 10/60/80/100/120；公招【资深干员】/【高级资深干员】9:00:00 不被划掉；剿灭 1200+400+100+25×4=1800；精英化 3 万+18 万、专精 8/16/24 小时；心情 24、无人机 235、贸易站订单表。核查者只改了 1 处（`operator-progression` 的「芯片助剂只能从采购中心换」与 PRTS 的见习任务/组合包矛盾）。
- 主 agent 据核查报告另做了 7 处内容修正（详见「Rulings」与各条目 diff）：`sanity-system`、`playbook-sanity-planning` 去掉已弃用的 `Fight.expiring_medicine`；`headhunt-rules` 把「跨年欢庆寻访」计数清零收缩到 PRTS 明文的【跨年欢庆·中坚寻访】；`certificate-shops` 红票干员合同名单改为举例 + 「每年 11 月 1 日追加」，并补上漏列的「中级作战记录×15」；`trust-and-potential` 补 4★「传承信物」；`operator-progression` 的精英阶段经验口径点明是 6★；`stage-rules` 的「固定随机数序列」标为机制推断；`operator-classes` 顶部加「分支数字不是通用规则」的限定；`glossary-terms` 去掉与 M18 `glossary` 重复的基础词条。

- Task 3：实现者完成（`task-3-report.md`）。主 agent 复核：读过两个测试文件的全部 diff——`_spec_blocks()` 改成读两份规格的块并集，并新增「并集恰好等于 `builtin/` 目录」「等于 `builtin_entries()`」两条更强的断言；预算类用例把基数从「只扣 `glossary`(157)」改成「全部启用内置全文合计 2,438」，可用 562 字，四步边界语义（恰好通过 / 超一字 409 / 停用不计 / 共享预算）全部保留；顺序与注入类的期望改成从 `builtin_entries()` 现算。亲自复跑：两个测试文件 **79 passed**（M18 时也是 79），全量 **1886 passed / 4 skipped**，退出码 0。
- Task 4：主 agent 亲自改 docs/11 §6（补 M19 条目清单与域分组表、每轮成本、内容纪律）、docs/12（新增 M19 条目）、docs/13 §8.24（本里程碑裁决十条）、docs/README（决策速查表一行补 31 条与每轮成本）。
- Task 5（主 agent 亲自跑，全部在最终提交上）：
  - `.venv/bin/python -m pytest -o addopts= -q` → **1886 passed, 4 skipped**，退出码 0；
  - `.venv/bin/python scripts/dump_openapi.py --check` → current；`scripts/generate_api_guide.py --check` → current；`cd web && pnpm gen:api:check` → up to date；三者退出码 0（本里程碑不动接口与生成物）；
  - 运行时实测：`list_entries()` 31 条全部启用；`render_context()` 索引 29 行（glossary 类走全文段）、知识库段总长 **4,325 字**；`read_knowledge("headhunt-rules")` 与 `read_knowledge("glossary-terms")` 都能读到全文；
  - **主 agent 自己的事实抽查**（不看核查报告，直接抓 PRTS 原文）：`寻访规则` 逐字确认基础概率 2/8/50/40%、「连续 50 次没有获得 6★…提升至 4%…每次提高 2%…直至 100%」、跨池累计与出六星回 2%、新寻访特惠、标准选调 150/300、限定 300 次必得赠送与 1:6 转参数模型、联动 120 抽、新年特别十连、新人特惠 380 玉 21 次；`公开招募` 逐字确认 1:00/4:00/7:40 三档稀有度下限、【资深干员】/【高级资深干员】配 9:00:00 不被划去、2024-05-15 起不再消耗龙门币、6★ 必须靠【高级资深干员】。

## 最终交付

- 分支 `m19-agent-knowledge-expansion`，从 `refactor/v2`（`1d81caa`）新建。提交：`b668ceb` 规格与计划、`97f83ce` 26 条新条目 + M19 规格 §4.6 + 测试同步、`1b7ee84` 文档、本台账。
- 交付内容：内置知识 5 → **31 条**（抽卡与经济 4、理智与作战 5、养成 5、公开招募与基建 6、界面/活动/术语 6），全部按 PRTS 撰写并逐条留出处；新增 `glossary-terms` 扩充俗称；M18 的 5 条与 M18 规格一字未改；规格/测试/文档同步。
- 未做（有意）：运行时不联网查 PRTS；知识检索（31 条远低于 50 条上限）；会随版本失效的当期数据；`screen-*` 的具体坐标（等真机截图）。
- 遗留：用户 `profile` 的全文预算只剩约 560 字（`MAX_FULLTEXT_CHARS` 未动，属已文档化的对外限制，上调需用户拍板）；`operator-classes` 的分支数值、`stage-rules` 的代理随机序列属「已加限定但无逐字出处」；真机与真模型的实测仍未做。
