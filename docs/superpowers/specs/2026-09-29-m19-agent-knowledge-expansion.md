# M19 · 内置知识库扩充（资深玩家知识）

> 关联：[11-Agent模块设计](../../11-Agent模块设计.md) §6，[13-决策记录](../../13-决策记录.md) §8.23、§8.24，[M18 规格](./2026-09-29-m18-agent-knowledge.md) §4.1、§4.6。实施分支 `m19-agent-knowledge-expansion`，从 `refactor/v2` 新建。

## 1. 目标与用户决策

目标：M18 交付的内置知识库只有 5 条骨架（账号资源剧本、首页、仓库、抽卡换算、术语表），回答稍有深度的问题就只能靠模型自己回忆。本里程碑用 **PRTS wiki** 的内容把内置知识扩成体系化的「资深明日方舟玩家」知识：明确的机制数值、界面常识、可执行的剧本，以及玩家口语与正式名称的对照。

用户裁决（2026-09-29）：

1. **通过 PRTS 的 wiki 内容补充知识库**，让内置 Agent 变成一个资深专业的明日方舟玩家。事实以 PRTS 为准，不靠模型记忆。
2. 这是对 M18 的**增量扩充**：M18 已交付的 5 条内置条目一字不改（它们与 M18 规格 §4.6 逐字绑定的测试继续有效）。

## 2. 事实基础

| 事实 | 证据 |
|---|---|
| 内置条目是随代码发布的只读 Markdown，数据库只存用户改动 | M18 规格 §4.1；`maa_api/agent/knowledge/__init__.py` |
| 只有条目数与全文段有上限，正文单条可到 20,000 字 | `MAX_ENABLED_ENTRIES = 50`、`MAX_FULLTEXT_CHARS = 3_000`、`MAX_BODY_CHARS = 20_000` |
| 索引类条目的**每轮成本**只有一行 `- <id>｜<title>：<when_to_use>`；全文只在模型主动 `read_knowledge` 时才进上下文 | M18 规格 §4.1「注入」；`KnowledgeService.render_context()` |
| `glossary` 与 `profile` 是**全文每轮注入** | 同上 |
| M18 的 5 条内置条目的索引合计 218 字，术语表正文 157 字 | 本机实测（`builtin_entries()`） |
| 内置文件与规格逐字一致由测试钉死 | `tests/agent/test_knowledge_service.py::test_builtin_files_are_copied_verbatim_from_the_spec` |

## 3. 范围

### 3.1 本里程碑

1. 新增 **26 条**内置知识条目（4 条经济与抽卡、5 条理智与作战、5 条养成、6 条公开招募与基建、6 条界面/活动/术语），内容全部按 PRTS 撰写并逐条留出处。
2. 内置条目从 5 条增加到 31 条；扩充电解术语（新增的 `glossary-terms` 与既有 `glossary` 合计不超过 3,000 字）。
3. 与本里程碑配套的规格、测试、docs 更新。

### 3.2 不做

- **运行时不联网**：Agent 不会在对话里去抓 PRTS。知识是随代码发布的离线文本。
- 不改 M18 已交付的 5 条条目。
- 不做知识检索（M18 已把「条目超过索引上限时再用 FTS5」列为以后的事；本里程碑 31 条远低于 50 条上限）。
- 不写会随版本失效的内容（当期卡池、活动时间、当期商店清单）。
- 不碰 `web/`（设置页天然支持任意多条内置条目，无需改动）。

## 4. 设计

### 4.1 条目清单

见 §4.6 的全文块；每条同时登记在 `tests/agent/test_knowledge_service.py` 的 `BUILTIN_IDS`。

| kind | 条目 | 作用 |
|---|---|---|
| rule | `headhunt-rules` | 寻访概率、保底、卡池类型 |
| rule | `currency-sources` | 合成玉/至纯源石/龙门币的来源与周期上限 |
| rule | `certificate-shops` | 高级/资质/采购/通用凭证与兑换优先级 |
| rule | `annihilation-weekly` | 剿灭作战与每周合成玉上限 |
| rule | `sanity-system` | 理智上限、恢复、药剂、碎石 |
| rule | `stage-rules` | 三星、代理指挥、突袭、首通、失败返还 |
| rule | `drop-efficiency` | 掉落与理智效率的判断方法 |
| playbook | `playbook-sanity-planning` | 理智规划与刷图剧本 |
| playbook | `playbook-drop-lookup` | 查掉落与选图剧本 |
| rule | `operator-progression` | 等级上限、精英化、专精、模组 |
| rule | `trust-and-potential` | 信赖与潜能 |
| rule | `operator-classes` | 职业、分支与稀有度 |
| playbook | `playbook-promotion-planning` | 养成规划剧本 |
| screen | `screen-operator` | 干员列表与详情界面 |
| rule | `recruit-tags` | 公开招募标签与必出组合 |
| playbook | `playbook-recruit` | 公开招募实操 |
| screen | `screen-recruit` | 公开招募界面 |
| rule | `infrast-mechanics` | 基建设施、心情、无人机 |
| playbook | `playbook-infrast-shift` | 基建换班剧本 |
| screen | `screen-infrast` | 基建界面 |
| screen | `screen-terminal` | 终端与作战选关界面 |
| screen | `screen-headhunt` | 寻访界面 |
| screen | `screen-store` | 商店与采购中心 |
| rule | `event-and-rerun` | 活动结构与复刻规则 |
| playbook | `playbook-daily-weekly` | 日常/周常清单 |
| glossary | `glossary-terms` | 扩充的玩家俗称与黑话 |

### 4.2 每轮上下文成本

- 索引行：31 行合计约 1.4–1.6 千字（M18 的 5 条是 218 字）。这是每轮都要付的成本，因此 `when_to_use` 必须短而准。
- 全文段：`glossary` + `glossary-terms` 合计 ≤ 3,000 字，同样是每轮成本。
- 其余 29 条的正文只在模型调用 `read_knowledge` 时才进上下文——**这正是能把知识写深的原因**：单条 20,000 字的上限内可以写得很细，不读不花钱。

### 4.3 准确性纪律

- 事实**只有 PRTS 一个来源**，每条断言在 `.superpowers/sdd/2026-09-29-m19-agent-knowledge/research/*.md` 里留「结论 + URL + 原文引用」。
- 界面位置类知识（`screen-*`）PRTS 没有可靠依据：**不写具体坐标**，只写相对位置并要求「截图确认后再点」。
- 会随版本变化的内容不写死；不确定的数值不猜，写明「以游戏内为准，不要凭记忆回答」。
- 独立复核：由第二个 agent 重新抓取引用页面核对，主 agent 再抽查关键数值。

### 4.4 与 M18 的边界

- M18 规格 §4.6 的 5 个块**不动**；本规格新增 §4.6 的 26 个块。
- 逐字一致性测试改成读**两份规格**的块并集，要求并集恰好等于内置目录里的文件集合。

### 4.5 术语表拆分

M18 的 `glossary`（157 字）保持原样，新增 `glossary-terms`。多条启用的 `glossary` 会按 id 顺序全部注入（M18 Task 4 的既有语义），因此两者合计受同一个 3,000 字上限约束。

### 4.6 内置知识全文（M19 新增 26 条）

<!-- M19-BUILTIN-BLOCKS -->

## 5. 错误与兼容

- 无新接口、无新错误码、无 migration；`web/openapi.json`、`web/src/types/api.d.ts`、`docs/14` 都不变。
- 已有会话下一轮就用上新知识；用户对某条内置条目的覆盖仍以用户为准，没覆盖的字段跟随新版本。
- 内置条目数 5 → 31，仍低于启用上限 50；全文段合计仍在 3,000 字以内。
- 设置页会多出 26 行条目，无需改前端代码。
- 每轮 system 消息变长（索引约 +1.2 千字、术语表约 +2.5 千字），是本次扩充的**已知代价**；代价换来的是模型不必凭记忆猜数值。

## 6. 验收

- 内置条目全部可解析、在各项上限内，且与两份规格的块逐字一致；
- 研究的每条断言都有 PRTS 出处；独立复核未发现与 PRTS 矛盾的数值；
- 关键数值由主 agent 亲自抽查（寻访概率与保底、理智恢复、公开招募必出组合、剿灭周上限、精英化/专精条件）；
- `.venv/bin/python -m pytest -q` 退出码 0；
- `scripts/dump_openapi.py --check`、`scripts/generate_api_guide.py --check`、`cd web && pnpm gen:api:check` 均无变化；
- `read_knowledge` 能读到新条目；`render_context()` 的索引包含全部 31 条；
- 环境性检查：本里程碑不改代码路径，因此不重跑真机项。

## 7. 文档同步

- docs/11 §6：条目清单与「资深玩家知识」的定位、每轮成本口径；
- docs/12：新增 M19 条目；
- docs/13 §8.24：本里程碑的裁决与偏离；
- docs/README：决策速查表的 Agent 知识库一行补「内置 31 条」；
- `docs/superpowers/ledgers/2026-09-29-m19-agent-knowledge-expansion.md`：台账归档。

## 8. 已确认的行为变更（2026-09-29）

1. **每轮 system 消息显著变长**（索引 +1.2 千字、术语表 +2.5 千字量级）。用户已确认要「资深玩家」的知识密度，接受这部分 token 成本。
2. **内置知识的事实来源是 PRTS**，随代码发布、离线可用；不是运行时联网查询。
