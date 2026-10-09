# M24 · 当期活动与关卡代号（动态资源知识）

> 关联：[11-Agent模块设计](../../11-Agent模块设计.md) §2.2、§3.2、§6，[13-决策记录](../../13-决策记录.md) §8.23–§8.25、§8.38，[M19 规格](./2026-09-29-m19-agent-knowledge-expansion.md) §3.2、§4.3。实施分支 `m24-activity-knowledge`，从 `refactor/v2` 新建。

## 1. 目标与用户决策

目标：内置 Agent 现在答不出「现在是什么活动」「这一期的关卡代号是什么」。M19 把知识库扩成 31 条时，**有意**把「会随版本失效的当期数据」排除在范围外（M19 规格 §3.2、§4.3：会随版本变化的内容一律不写死），于是这块能力一直缺着。本里程碑补上它，但**不**用把一个两周一换的清单写进 Markdown 的做法补——那种条目在下一个活动就会骗人。

用户裁决（2026-10-09）：

1. **动态工具 + 系统消息注入 + 知识库条目三者都做**。只加知识库条目答不出当期数据；只加工具则模型想不起来调用时仍然答不上来。
2. **按里程碑规范做**：新建 `m24-activity-knowledge` 分支，写规格与计划，完成后 `git merge --no-ff` 回 `refactor/v2` 并打 tag `v2-m24`。
3. **顺带修 `resolve_stage` 的「代号重名」行为**：现状在代号对应多个活动时静默取 `stageId` 字典序第一条，会给出**别的活动**的关卡；改为返回全部候选。

这一条对应用户原话：「我希望继续扩充 agent 的知识库，现在的 agent 甚至不知道游戏的最新版本活动是什么，也不知道对应的关卡代号。」

## 2. 事实基础

全部事实由主 agent 在本机实测（`AsstGetMapLevelKey` 直接调内核，资源层 = 基础层 + `repo` 增量层，内核 v6.18.0）：

| 事实 | 证据 |
|---|---|
| 活动资源包里有当期活动的**名字与开始时间** | `resource/maa-layers/repo/resource/version.json` → `{"activity": {"name": "矢量突破#3 拟生态", "time": 1790668800}, "gacha": {"pool": "石白深蓝之夜", ...}, "last_updated": "2026-10-08 21:02:19.000"}` |
| 该文件同时存在于内核自带层与增量层，是资源更新的产物 | §8.34 的实况验收：`POST /api/updates/resource` 后 `last_updated` 变为 `2026-10-08 21:02:19.000`，`version.json` 随之更新 |
| 关卡全表在 `Arknights-Tile-Pos/overview.json`，**含活动** | 4255 条，键 `f"{stageId}-{levelId.replace('/', '-')}"`，值含 `code` / `name` / `stageId` / `levelId` / `filename` / `width` / `height` |
| 常驻关卡表 `stages.json` **不含活动** | 608 条，全部为常驻关；`act3break_*`、`act54side_*` 一条都没有 |
| 当期活动关卡确实在 overview 里 | `act3break_01…_12` → `VEC-01…VEC-12`（`VEC-08` = 磷脂层破坏） |
| **代号会重名，重名时代号不足以定位** | `VEC-` 出现在 `act1break`（#1）、`act1vecb`、`act2break`（#2）、`act3break`（#3）四期；实测 `AsstGetMapLevelKey("VEC-08")` 返回 `act1break_08`（#1 期「结构化思维」），**不是**当期的 `act3break_08`（「磷脂层破坏」）；whole 表里 2156 个不同 `code` 有 958 个对应多个 stageId |
| 用 `stageId` 查则唯一且正确 | `AsstGetMapLevelKey("act3break_08")` → `code=VEC-08`、`name=磷脂层破坏`、`level_id=activities/act3break/level_act3break_08` |
| 内核 API 能接受 `code` / `stageId` / `levelId` 三种写法 | `main_01-07`、`main_01-07#f#`、`1-7`、`activities/act3break/level_act3break_08` 都能查到同一条 |
| 不是所有活动都配了关卡导航 | 内核的 `tasks/Stages/*.json`（46 个）里有 `SR`、`TO`、`TA`，**没有 `VEC`**；`tasks.json`（1090 条）里也没有任何 `VEC-*`。即 MD 文档所说的「当期 SS 活动后三关需额外加载活动关卡导航」对**这一期不成立** |
| 活动名与关卡代号之间**没有**现成的映射表 | `version.json` 只有名字；`overview.json` 只有代号与 stageId；包内没有任何文件同时含二者 |
| 关卡代号是**纯数字期数**，不是中文行号 | `act1break`/`act2break`/`act3break` 分别对应「矢量突破」#1/#2/#3；包内无中文「矢量突破」字样（`grep` 全零） |

上表最后两条决定了设计：**活动名 → 当期关卡代号必须靠「名字里的期数」+「候选期次字典序最大」这类可解释的推断，并如实标注推断依据**，绝不能假装包里有精确映射。

## 3. 范围

### 3.1 本里程碑

1. **新服务模块** `maa_api/services/activity_index.py`：只读活动资源，给出「当期活动 + 它的关卡代号/中文名/stageId」。
2. **新 Agent 工具** `get_activity_stages`（组 `activity`，SAFE、只读、模型可见）：查当期活动，或按代号/关卡名查某一关属于哪一期活动。
3. **系统消息注入**：知识库索引之下新增「当期活动」段（每轮成本 ≤ 120 字，见 §4.3），让模型不用调用工具就知道现在是什么活动。
4. **新内置条目** `activity-stage-codes`（kind `rule`）：讲活动关卡的代号体系、重名消歧、以及「先查再提交」的操作顺序；不含任何当期数据。
5. **修 `resolve_stage`**：本地回退索引从 608 条的 `stages.json` 换成含活动的 `overview.json`；重名时返回全部候选（新的 `candidates` 字段），不再静默取字典序第一条。
6. 规格、测试、docs 同步。

### 3.2 不做

- **不改 `builtin/` 里已有的 31 条正文**；`event-and-rerun` 只加一行指向新工具（M24 §4.5），其余一字不动。
- **不写死任何当期数据**到内置条目或代码里：没有「矢量突破#3」「VEC-08」这类常量出现在源码与知识条目中（测试会断言）。
- **不联网**：不查 PRTS、不查 `StageActivityV2.json`、不查 MaaResource 仓库。只用本机资源包。
- 不做活动**阶段**（第一段/第二段开了没有）的判断：包内没有可依据的时间表，继续按 `event-and-rerun` 的口径「以游戏内公告为准，没开就截图确认」。
- 不改 `stage_layout.py` 的 `overview.json` 解析（两处都要读同一个文件，但各自独立缓存；抽公共层属于重构，不在本里程碑）。
- 不动前端以外的接口：新增工具会进 `GET /api/agent/tools` 目录与 OpenAPI 快照，按惯例重新生成，不新增端点与错误码。

## 4. 设计

### 4.1 数据来源与解析规则

新增 `maa_api/services/activity_index.py`，只读三处数据，**层序沿用** [`iter_resource_roots()`](../../../maa_api/services/resource_index.py)（基础层 → `cache` → `custom`，读的时候反向遍历、后加载的覆盖前面的；`repo/` 不是运行时层，资源更新时已经被合并进基础层，所以 `source` 只会出现这三层之一），与 `stage_layout.py` 同一口径：

1. `<层>/version.json` 的 `activity.name` / `activity.time` —— 当期活动名与开始时间（epoch 秒）。
2. `<层>/Arknights-Tile-Pos/overview.json` —— 关卡全表。
3. `<层>/tasks/Stages/*.json` 的**文件名** —— 该代号前缀有没有关卡导航（只判断存在性，不读内容）。

推断规则（对应用户最关心的「活动名 → 关卡代号」）。**先说结论：包内没有名字到代号的映射表，中文关卡名与活动名之间也没有任何字符关系**（实测「磷脂层破坏」与「矢量突破#3 拟生态」既不同名也不是子序列）。因此只在**能解释得通**的前提下给答案，其余情况如实说「不知道」：

- **期数提取**：活动名里匹配 `#(\d+)` 与紧跟其后的中文序号（`#3`、`第三期`），如「矢量突破#3 拟生态」→ 3。提取不到期数 → 直接 `confidence="none"`，不猜。
- **候选定位**：把 overview 里的 stageId 前缀按 `^([a-z]+)(\d+)([a-z]+)$` 拆成「词干 + 期数 + 模式后缀」（`act3break` → `act` / 3 / `break`；`ro3`、`a003`、`camp_r_02` 这类拆不出来的直接出局）。保留期数**等于当期期数**、且满足两条硬门槛的前缀：① 该前缀下全部关卡的 `levelId` 都在 `activities/` 命名空间（排除肉鸽、剿灭、主线等）；② 有字母模式后缀。实测这一刀把当期 9 个候选收敛到 3 个完整活动。
- **排序**：留下的候选按「主关（`XX-<数字>`）数降序 → 关卡总数降序 → 前缀字典序升序」排列，第一名作为推断结果。实测 `act3break`（12 主关 / 32 关）> `act3bossrush`（8 / 8）> `act3enemyduel`（2 / 2），第一名正是当期活动。
- **关联期次**：`related` 列**同一类玩法**（词干 + 模式后缀都相同）的其它期次，例如 `act3break` 的 `related` = `act1break` / `act2break`。**它只是提示，不参与 `is_current` 判定**。
- **信心等级**：唯一候选 → `high`；有多个候选 → `low`（取排第一的那个，其余进 `alternatives`）；提不到期数、或该期数没有任何合格前缀 → `none`（`stages` 为空数组）。返回 `confidence`、`matched_by`（`series-number` / `none`）、`stage_id_prefix`、`code_range`、`related` 与 `alternatives`，让模型知道结论是怎么来的。

> **这是排序，不是映射**：包内没有名字到代号的映射表，`related` 与 `alternatives` 就是把不确定性摊开给模型看的那一部分。模型可以用 `stage` 参数查一条具体关卡来核对。
>
> 这套规则的正确性由**回归夹具**钉住：夹具里放 `version.json` 与一份从真实 `overview.json` 裁出来的最小关卡表（含 `act1break`/`act1vecb`/`act2break`/`act3break` 四期同名代号 + 肉鸽/剿灭的同名期数干扰项），断言「矢量突破#3 拟生态」→ `act3break`、`related` = `act1break`/`act2break`、`VEC-08` → 磷脂层破坏且 `is_current` 为真。
>
> 已知边界（写进 docs/13 §8.38，不假装解决）：活动名不带期数时（如只有副标题的写法）本工具给不出代号。上游 `version.json` 的 `name` 长什么样由 MaaResource 决定，换一种命名就会退化到 `none`——这是「宁可说不知道」的代价，也是本里程碑选择不把它藏起来的原因。

### 4.2 工具 `get_activity_stages`

| 项 | 值 |
|---|---|
| 名称 | `get_activity_stages` |
| 组 | `activity`（新组） |
| 风险 | `SAFE`、`read_only=True`、`model_visible=True` |
| 参数 | `stage: str \| None`（1–64 字）：查某一关；`prefix: str \| None`（2–32 字）：取某一组关卡。两个都不给就列当期活动 |

返回值（两个参数都省略时）：

```json
{
  "activity": {"name": "矢量突破#3 拟生态", "started_at": "2026-09-29", "series": 3},
  "stages": [{"code": "VEC-01", "name": "寂静螺旋", "stage_id": "act3break_01"}, "…"],
  "stages_total": 32,
  "stage_id_prefix": "act3break",
  "confidence": "low",
  "matched_by": "series-number",
  "code_range": ["VEC-01", "VEC-12"],
  "related": ["act1break", "act2break"],
  "alternatives": [{"stage_id_prefix": "act3bossrush", "mode": "bossrush", "stages_total": 8, "main_stages": 8, "code_range": ["TN-1", "TN-4"]}],
  "navigation": "missing",
  "resource_updated_at": "2026-10-08 21:02:19.000",
  "source": "<真实生效的那一层，例如内核自带资源目录>"
}
```

- `stages` 按代号排序，主关（`XX-<数字>`）在前、特殊关（EX / TR / S）在后，最多 `MAX_STAGES = 60` 条；`stages_total` 是**未截断**的总数。同一条上限也适用于 `prefix` 形态（实测某组 70 关时只回 60 条、`stages_total` 为 70）。
- `started_at` 是 `date`（`YYYY-MM-DD`，按服务器本地时区），不写时刻。
- `navigation`：`"available"`（`tasks/Stages/<代号前缀>.json` 存在）、`"missing"`（不存在）、`"unknown"`（连前缀都没推出来）。**只描述资源现状，不预测 Fight 能不能过**：导航文件存在说明 MAA 为这组关卡定义了识别与跳转，缺失时能否直接提交属未知，工具照实报，让模型提示用户先截图确认。
- 查单关（`stage` 参数）：按 `stageId` → `code` → 中文名 → `levelId` **分级**精确匹配（大小写不敏感），某一级有命中就不再往下一级找——一个词可能同时是某关的代号与另一关的名字（实测「切尔诺伯格」两义），取并集会把两关混在一起返回。**全部候选都返回**（`matches` 数组，含每条的 `code`/`name`/`stage_id`/`level_id`/`activity`/`period`/`mode`/`is_current`），并回报命中的是哪一级（`matched_field`）。`is_current` 标出哪条属于当期活动、`period` 直接给出期数——这正是「同名代号到底是哪一期」的答案。

  **已知边界**：分级意味着「一个词既是 A 关的代号、又是 B 关的中文名」时，只返回代号那一级（实测「切尔诺伯格」：代号级命中一条，以它为中文名的另一关不会同时出现，要查那一关得用它的代号或 stageId）。取舍理由是本工具的常客是代号与 stageId（活动关的主要问法），并集会把两关混在一起返回。
- 查某一组（`prefix="act3break"`）：只接受**当期或 alternatives 里出现过**的前缀，返回这一组关卡与 `is_current`；未知前缀抛 `INVALID_PARAMETER` 并在 details 里带上已知前缀（防模型瞎编前缀）。
- 查不到：返回 `matches: []` 与 `hint`（提示可换 stageId 或中文名），**不抛异常**（查数据不是错误）。
- 资源缺失/损坏：抛 `RESOURCE_LOAD_FAILED`（与 `item_index`、`stage_layout` 同一错误码口径）。

### 4.3 系统消息注入

`KnowledgeService.render_context()` 在索引之后、术语表之前插入一段：

```
当期活动（来自本机活动资源，随资源更新自动变化）：矢量突破#3 拟生态，2026-09-29 开始；关卡代号 VEC-01…VEC-12（共 12 关）；提到具体关卡时用 get_activity_stages 查 stageId，同名代号可能属于别的期次。
```

- 新增常量 `ACTIVITY_HEADER = "当期活动（来自本机活动资源，随资源更新自动变化）："`，与 `INDEX_HEADER` / `GLOSSARY_HEADER` 并列，供测试引用。
- 只放**一行**：活动名、开始日期、代号区间与关数、以及「同名代号可能属于别的期次」这句消歧提醒。整行 ≤ 120 字（`MAX_ACTIVITY_LINE_CHARS`）：正常活动名不会触发裁剪（真数据 101 字），名字长到超限时先退掉代号区间、再裁活动名，保证每轮的固定成本有上界。
- 活动数据读不出来时**整段不插**（不是写「暂无」）：与「知识库读失败降级为『知识库暂不可用』」不同，这里没有内容可展示，插一行「暂无」只花 token 不提供信息。
- 取数据的服务通过构造参数注入（`KnowledgeService(factory, activity_index=…)`），缺省用模块级默认实现；测试可注入假件。读盘按文件签名缓存，`update_resource` 之后无需重启即可反映新活动。
- 这一段是**每轮成本**，因此不列全部关卡；列全表的路径是工具。

### 4.4 `resolve_stage` 的重名修正

现状（`maa_api/services/stage_resolver.py`）：内核可用时优先内核；内核不可用或查不到时回退读 `stages.json`，重名时按 `stageId` 字典序取第一条。问题有两个：**回退索引比文档写的弱**（`docs/11 §2.2` 说回退用 `tasks/Stages/`，实现读的是 608 条的 `stages.json`），以及**重名静默取错**（`VEC-08` 会得到 `act1break_08`）。

本里程碑的改法：

1. 本地回退索引换成 `Arknights-Tile-Pos/overview.json`（含活动，4255 条）。选择顺序：`stageId` 精确 → `levelId` 精确 → `code` 精确 → 中文名精确；每级都大小写不敏感。
2. **全部候选取 `stageId` 字典序**返回，`resolved` 仍是第一条（兼容既有调用方与前端），新增 `candidates: [{code, stage_id, name, level_id}]`（无候选时为空数组）与 `candidates_total: int`。
3. 内核路径不变（仍优先、仍返回 `source="core"`）；内核返回的结果补 `candidates`（单条）。
4. `stages_path` 参数保留（测试与旧装配用），但语义变成「显式指定 overview.json 的路径」；新增 `overview_path` 别名并让两者指向同一个内部属性，避免两份逻辑。

> 这一条是**对外行为变更**（`resolve_stage` 返回值多两个字段、重名时不再只给一条），按用户裁决 3 执行，记入 §8 与 docs/11 §2.2。

### 4.5 内置条目 `activity-stage-codes`

- 文件 `maa_api/agent/knowledge/builtin/activity-stage-codes.md`，`kind: rule`，`title: 活动关卡代号`，`when_to_use` ≤ 120 字。
- 正文讲**稳定规律**：代号形如 `<前缀>-<数字>`（`VEC-01`）或 `<前缀>-EX<数字>`；中文名是关卡名、代号是阶段名；代号在活动期次之间会重名（`VEC-` 有 4 期），所以「代号」不足以定位，**stageId**（`act3break_08`）才唯一；查的方法只有一个：`get_activity_stages`；`resolve_stage` 能校验内核认不认这一关。
- 如实写两条限制：① 不是所有活动都配了关卡导航（`tasks/Stages/` 里没有对应前缀时，`Fight.stage` 能否直接跑通属未知，先截图确认，别对用户打包票）；② 活动分阶段开放，包内没有时间表，按公告与截图判断。
- **不写**任何当期活动名、期数、具体代号（有测试断言）。
- 全文块进 M24 规格 §4.6，与 M19 同一套逐字一致性机制。

### 4.6 全文块（内置条目原文）

```markdown
---
id: activity-stage-codes
kind: rule
title: 活动关卡代号
when_to_use: 用户提活动、当期关卡、某关代号或问「这关现在能不能打」时
---
## 代号不是关卡名
活动关在游戏里同时有两个标识，别混用：
- **代号**是选关界面上写的那个，形如 `XX-01`、`XX-EX-3`。同一期活动里的代号连编号都不重复，但**跨期次会重名**——同一个代号在不同期活动里指向不同的关。
- **stageId** 是内核与资源用的内部名，形如 `act<期数><模式>_08`：前半段是这一期活动的标识，后半段是关卡号。它全局唯一。
- 提交 `Fight` 用的是**代号**（`stage` 参数），要先拿到代号再提交；但要确认「这个代号属于哪一期」，只能靠 stageId。

## 同名代号怎么消歧
用户说一个代号（例如 `XX-08`）时，先假设它可能属于多期：当期的 `XX-08` 和前几期的 `XX-08` 是不同的关。判断办法只有一个——用 `get_activity_stages` 查这个代号，看返回的候选里哪条的 `is_current` 为真，那条才是当期。**不要凭期数或记忆推断**，也不要拿别的期的关卡信息回答当期问题。

## 怎么查
1. 想知道「现在是什么活动、这一期有哪些关」：`get_activity_stages` 不带参数。
2. 想知道「某个代号/关卡名是哪一期、当期是不是它」：`get_activity_stages` 带 `stage`。
3. 想校验内核认不认这一关、拿到 `Fight` 要的写法：`resolve_stage`。
4. 想知道某一关的地形与出怪位置：`send_stage_map`（本地关卡图）。

提交 `Fight` 前先确认这一段已经开放。工具返回的 `navigation` 只说明资源里有没有这一期关卡的导航定义，不代表一定跑得通：`missing` 时先截图确认选关界面，别对用户打包票。

## 三条不确定的事
- **活动分阶段开放**：第一段、第二段、末段先后开，资源里没有时间表。用户问「后三关能不能打」时，答「以游戏内公告与当前界面为准」，必要时让他截个图。
- **活动名推代号是推断，不是查表**：本机资源里只有活动名和关卡表，没有名字到代号的映射。工具靠「活动名里的期数 + 当期候选的量级」排序，返回 `confidence`：`high` 表示同期只剩一个候选，`low` 表示有多个候选、取的是排第一的那个（其余在 `alternatives` 里）。`none` 就是推不出来——这时如实说不知道，让用户给个关卡代号或名字，再用 `stage` 查。
- **活动之外的模式**（肉鸽、危机合约、剿灭、生息演算）各有自己的代号体系，不在「当期活动」里，按各自的说法回答。
```

## 5. 错误与兼容

- **不新增错误码**：复用 `RESOURCE_LOAD_FAILED`（资源缺失/损坏）与 `VALIDATION_ERROR`（参数越界）。工具查不到关卡是**正常返回**（`matches: []`），不是错误。
- `GET /api/agent/tools` 多一条 `get_activity_stages`；`POST /api/agent/tools/get_activity_stages/invoke` 自然可用。
- `resolve_stage` 返回值**向后兼容**：既有键（`query`/`source`/`resolved`）语义不变，只增不减。
- `render_context()` 的输出多一段：这是 system 消息内容，不影响接口契约；既有断言改期望值而不是放宽。
- 工具总数 48 → 49（`tests/agent/test_tool_catalog.py`）。

## 6. 文档同步

- `docs/11-Agent模块设计.md`：§2.2 改为「回退索引是 `overview.json`」并写重名候选；§3.2 工具清单加 `get_activity_stages`（新组 `activity`）；§6.1/§6.5 写当期活动段的注入位置与成本。
- `docs/12-实施计划与里程碑.md`：新增 M24 条目（目标、范围、验证方式）。
- `docs/13-决策记录.md`：§8.38 记用户裁决、事实证据、推断规则、`resolve_stage` 的对外变更与不做的部分。
- `docs/README.md`：决策速查表的 Agent 知识库行补「当期活动段」与新工具。
- `maa_api/agent/knowledge/builtin/event-and-rerun.md` 中「用本服务刷活动」一节加一行指向 `get_activity_stages`（M19 条目，仅此一处小改，正文其余不动）。

## 7. 验收

1. 后端 `.venv/bin/python -m pytest -q` 退出码 0。
2. `.venv/bin/python scripts/dump_openapi.py --check`、`scripts/generate_api_guide.py --check`、`cd web && pnpm gen:api:check` 全绿（先跑不带 `--check` 的重新生成）。
3. **运行时实测（主 agent 亲自跑）**：用真资源包调 `activity_index`，断言活动名 = `矢量突破#3 拟生态`、关卡首尾 = `VEC-01`/`VEC-12`、`act3break_08` 的中文名 = `磷脂层破坏`；调 `get_activity_stages` 两种参数形态；`resolve_stage("VEC-08")` 的 `candidates` 里同时出现 `act1break_08` 与 `act3break_08`；`render_context()` 含当期活动行、整行 ≤ 120 字（真数据实测 101 字）。
4. **反例**：把 `version.json` 换成活动名与关卡名毫无关系的假数据，断言工具返回 `confidence="none"` 且**不**瞎给代号。
5. 前端 `pnpm typecheck && pnpm test --run && pnpm build`（工具中文名表加 `get_activity_stages`）。
6. 完成后 `git merge --no-ff` 回 `refactor/v2`，打 tag `v2-m24`，台账归档到 `docs/superpowers/ledgers/2026-10-09-m24-activity-knowledge.md`。
