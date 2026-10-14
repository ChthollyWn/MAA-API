# M49 攻克关卡的固定决策链（知识库条目）（规格）

> 分支 `m49-stage-clear-playbook`，基点 `refactor/v2` = `b8948dd`（2026-10-14）。
> 本文是 M49 的唯一规格；实施后的偏离记在 [13 §8.72](../../13-决策记录.md)。

## 1. 背景

用户 2026-10-14 提问：「让 agent 攻克一个关卡，该采用怎样的方式成功率更高？直接操控模拟器，还是让 agent 编写自定义作业再执行？」答复后用户要求把结论固化成知识库条目，让 agent 每次都按这个顺序走，而不是每次自己权衡。

现状：内置 33 条知识条目里没有一条讲「怎么攻克一关」。散落的相关信息只在 `playbook-daily-weekly`（Fight 的 stage 怎么填）、`playbook-sanity-planning`（刷图选图）与 `stage-rules`（关卡规则）里，而 M29 交付的作业研究链路（`search_copilots` / `save_copilot_draft` / `run_copilot_attempt` / `record_copilot_result`）没有对应的操作剧本。

三条硬约束决定了正确答案（全部来自仓库现状，不是估算）：

1. MaaCore 没有滑动与独立 OCR 能力（`maa_api/agent/runtime.py` 的 `SYSTEM_PROMPT` 明写）。战斗要把干员拖到格子上，原子操作给不了这一步。
2. 历史截图不保留图像（同一段提示词）。逐格点击意味着每一步都要重新截图、重新认画面，一局几十步，任何一步误判即中断且无法从中途恢复。
3. 探索本身烧上下文：M48 之前实测的一次会话（DSH `session-d8cf968c`）里，turn 3 一步 299 个步骤、85 分钟，全会话 130.1M token 里 93.2M 花在这一个 turn 上。把「识别 + 决策 + 执行」全搬到模型侧是这套系统里最贵、最慢、最易错的形态。

因此顺序固定：先搜现成作业 → 搜不到自己写一份（都用 M29 工具存草稿）→ 执行 → 按结果迭代；原子操作只用于单步可核对的界面动作。这与 `docs/11-Agent模块设计.md:23-25` 的三层能力模型一致（第一层任务编排最可靠，第三层原子操作是兜底）。

## 2. 目标与非目标

目标

1. 新增内置知识条目 `playbook-stage-clear`（`kind=playbook`），把上面那条决策链写成可执行的操作剧本：先看现状 → 搜作业（按可行性档分流）→ 自己写作业（含静态检查失败怎么改）→ 执行前提与失败处置 → 原子操作的边界。
2. 条目遵守既有的两条硬约束：不带具体游戏数值（M30 的「数字 + 游戏单位」检查）与 frontmatter 的字段/长度约束。
3. 同步所有把内置条目数写死的断言与文档：33 → 34。

非目标（本轮不动）

- 不改任何工具、不改 `SYSTEM_PROMPT`、不改知识库的加载与注入机制。
- 不给作业研究链新增能力（例如自动迭代作业参数）；本条目只固化「怎么用现有工具」。
- 不动前端。
- 不删改任何既有条目。

## 3. 决策

- D1（条目优先）：把决策链写成 `kind=playbook` 的知识条目，不塞进 `SYSTEM_PROMPT`。理由：提示词是常驻上下文（每条都花 token），知识库索引只花一行，全文由 `read_knowledge` 按需读；而且知识条目有 `when_to_use` 触发条件，天然只在「要过关」时生效。
- D2（先搜后写，且以可行性档分流）：明确 `ok` / `missing_one` / `infeasible` 三种处置，`missing_one` 要先问用户同不同意借助战再继续，`infeasible` 如实报缺口。理由：借助战改变的是账号前提，属于要用户裁决的事。
- D3（静态检查失败照 violations 改）：`save_copilot_draft` 失败时按 `details.violations` 改，并禁止原样重试。理由：这是把内核已有的校验能力用起来，而不是让模型猜。
- D4（原子操作的边界写死）：条目里写明它只用于「单步、幂等、能截图核对」的界面动作，且做之前必须 `stop_pipeline`。理由：MaaCore 与 agent 抢设备是真实故障模式。
- D5（不写数值）：条目里不出现理智、练度、次数等具体数值，全部指回工具或既有条目（`playbook-sanity-planning` / `stage-rules` / `annihilation-weekly`）。

## 4. 设计

### 4.1 条目内容骨架

```
frontmatter: id=playbook-stage-clear, kind=playbook, title=攻克指定关卡, when_to_use=用户要过某一关、卡关、或问「怎么打这关」时
原则        ， 让内核去打；三条硬约束一句话；顺序固定
1 动手前    ， get_system_status / get_queue 看有没有在跑的；resolve_stage 校验关卡；歧义就问
2 先搜作业  ， search_copilots + 三档可行性分流 + save_copilot_draft(site_id=…) + violations 怎么用
3 自己写    ， get_stage_level_data 拿地图与出怪 + save_copilot_draft(content=…) + Click/Swipe 不校验的风险
4 执行      ， stop_pipeline → 停到「开始行动」→ run_copilot_attempt（含 not_started 不计次）→ unknown 用 record_copilot_result
5 失败之后  ， 按动作日志判断改哪里；授权用完/跨关要重新申请；按用户要求汇总
原子操作的位置 ， 只做单步幂等动作，先停流水线、截图确认
```

### 4.2 受约束的登记点（不改就会红）

| 位置 | 现状 | 改动 |
|---|---|---|
| `tests/services/test_knowledge_page_pointers.py:6`、`:43` | 33 | 34 |
| `tests/agent/test_knowledge_no_numbers.py` 的 `EXPECTED_ENTRY_COUNT` | 33 | 34（顺带改索引行注释里的「33 个条目」） |
| `tests/agent/test_knowledge_service.py` 的 `BUILTIN_IDS`（M19 规格表格顺序） | 33 条 | 末尾加一条并标 `# M49` |
| 同文件的 `BUILTIN_LIST_ORDER`（先 kind 再 id）与注释 | 33 条 | 在 `playbook-sanity-planning` 之后插入（`stage-clear` > `sanity-planning`） |
| `tests/api/test_agent_knowledge_router.py` 的 `BUILTIN_IDS` 与 `BUILTIN_LIST_ORDER` | 33 条 | 同上两处顺序各加一条 |
| `tests/agent/test_knowledge_service.py::test_builtin_files_match_the_spec_registry_and_frontmatter` | 要求每个条目都有规格登记 | 本文件即登记 |
| `docs/11-Agent模块设计.md` 的条目数口径（§6.1、§6.4 两处） | 33 条 | 34 条，并写明 M49 加的一条 |

### 4.3 不新增测试的理由

条目是数据，不是逻辑：`test_builtin_files_match_the_spec_registry_and_frontmatter`（登记与 frontmatter）、`test_knowledge_no_numbers`（无数值）、`test_knowledge_page_pointers`（工具名与页面指引必须存在）三组既有测试已经逐条扫全部内置条目，新条目只要进目录就自动被这三组扫到，比新写一条针对它的用例更严。

### 4.6 内置条目原文（M49）

以下代码块是 `maa_api/agent/knowledge/builtin/playbook-stage-clear.md` 的唯一原文来源，由
`tests/agent/test_knowledge_service.py::test_builtin_files_match_the_spec_registry_and_frontmatter`
逐字比对（frontmatter 必须一致）。

```markdown
---
id: playbook-stage-clear
kind: playbook
title: 攻克指定关卡
when_to_use: 用户要过某一关、卡关、或问「怎么打这关」时
---
## 原则
让内核去打，agent 只负责找方案或写方案。MaaCore 没有滑动与独立 OCR 能力，原子操作打不了关卡；历史截图不保留图像，逐格点击意味着每一步都要重新截图、重新认，又慢又容易断。顺序固定：先搜现成作业，搜不到再自己写一份，最后才考虑原子操作。

## 1. 动手前先看现状
- get_system_status / get_queue：有流水线在跑就不要抢，先说清在跑什么，再用 ask_user 发卡问是等它结束还是稍后再来。设备没连接就说明情况，征得同意后 reconnect_device。
- resolve_stage 确认关卡代号能对上；用户给的名字有歧义（例如只说「1-7 突袭」）就问清难度，不要猜。

## 2. 先搜现成作业
- search_copilots(stage=<关卡>, limit=5, only_feasible=true)：按关卡搜作业站并用账号现有干员筛。
- 看每条的可行性档：
  - ok：直接用。
  - missing_one：缺一名、可借助战。先问用户同不同意借助战（借了就不用自己有这名干员），再继续。
  - infeasible：缺口名单列了缺谁、缺多少练度，如实告诉用户，别硬跑。
- 选中后用 save_copilot_draft(site_id=<作业站编号>, note=<出处与思路>) 存成本地资源。保存只做校验、不执行，也不占设备。
- 保存会跑静态检查：坐标在地图内且落在可部署格、近战远程与地块匹配、干员在账号里或是唯一允许的助战补位、部署数不超过关卡上限、技能序号不超过技能数。失败抛 COPILOT_JSON_INVALID 并逐条给 violations。照 violations 改，不要原样重试。

## 3. 搜不到就自己写一份
- 先用 get_stage_level_data 拿这关的地图、出怪与部署上限，别凭记忆画格子。
- 用 save_copilot_draft(content=<作业 JSON>, note=<思路>) 保存。作业 JSON 是 MAA 的自动战斗协议：部署序列 + 技能时机，字段与候选取值以 list_task_types 里 Copilot 的 schema 为准。
- 静态检查只管部署项。Click / Swipe 这两类动作不校验，风险留给运行时；写这类动作前先用 get_screenshot 确认当前画面与坐标系。
- 写不出可行方案（缺干员、缺练度、卡在某个节点）就直说卡在哪，并给出还差什么，不要为了交差塞一份跑不通的作业。

## 4. 执行
- 先 stop_pipeline 停掉在跑的流水线，再把游戏停到「开始行动」界面并截图确认：run_copilot_attempt 从那里开始打。
- run_copilot_attempt(copilot_id=<草稿 id>, support=<是否借助战>) 需要授权，没授权先申请 grant_copilot_attempts；被拒绝就说明情况并问用户下一步，不要重试同一张卡。
- 返回 not_started 表示战斗没开始（不在开始行动界面、编队失败或干员缺失），这一次不计次：把游戏停对位置、截图确认后再试。
- 结果里 outcome=unknown 表示内核没认出来，用 record_copilot_result 看截图补判，并如实标明是模型判定。

## 5. 失败之后
- 读动作日志摘要判断是「练度不够」还是「时机不对」，再决定改哪一步；不要放弃作业改去点屏幕，也不要原样重复同一份。
- 同一关的尝试次数由授权决定；COPILOT_GRANT_EXHAUSTED 就是用完了，停下并告诉用户。换了关卡要重新申请授权（COPILOT_GRANT_STAGE_MISMATCH）。
- 收尾按用户要求汇总：每份作业试了几次、结果是什么、判定方是谁、最后用的哪一份。

## 原子操作的位置
它只用于单步、幂等、能截图核对的界面动作：确认编队、关掉弹窗、回到主界面。做之前先停掉流水线（避免和内核抢控制权）并截图确认，做完再截图核对。
```

## 5. 错误与兼容（本里程碑的风险与对策）

| 风险 | 对策 |
|---|---|
| 条目写成「数值清单」，触发 M30 的无数值检查 | D5：正文只写流程与判断标准；数值指回工具与既有条目。落地时直接跑 `test_knowledge_no_numbers.py` |
| 条目提到不存在的工具名 | `test_knowledge_page_pointers.py` 的「工具名必须在 `build_registry()` 里」会红；文中只引用真实工具 |
| 条目很长，污染索引 | `title` ≤ 32 字、`when_to_use` ≤ 120 字由 loader 与测试双重把关；索引只注入这两行 |
| 把「先搜作业」写成「一定用作业站」 | 条目里写明搜不到就自己写，且 `search_copilots` 不可用时按降级口径返回空列表，不是失败 |

## 6. 验收

1. `.venv/bin/python -m pytest -o addopts="" -q tests/services/test_knowledge_page_pointers.py tests/agent/test_knowledge_no_numbers.py tests/agent/test_knowledge_service.py tests/agent/test_knowledge_convergence.py tests/api/test_agent_knowledge_router.py` 全绿。
2. `read_knowledge("playbook-stage-clear")` 能读到全文，索引里出现 `playbook-stage-clear｜攻克指定关卡`。
3. 后端全量 `pytest` 只剩既有的两条真资源失败。
4. `scripts/dump_openapi.py --check` 不变（条目不进 OpenAPI）。
5. docs/11 的条目数口径为 34 且与新条目一致。
