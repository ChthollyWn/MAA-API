# M45：不再依赖内核白名单，自己识别仓库全部道具

- 里程碑：M45
- 分支：`m45-depot-selfscan`（从 `refactor/v2` 新建，基点 `0670331`）
- 状态：W0 已完成（实验 + 规格 + 计划），W1 起待实现
- 上游：M44 仓库识别覆盖全部页签 + 道具图（`0670331`，见 `docs/13` §8.64）、M27 仓库识别、M43/M44 资源代理
- 台账：`docs/superpowers/ledgers/2026-10-13-m45-depot-selfscan.md`

## 1. 来源与目标

用户 2026-10-13 原话：

> 完全将这个任务复制一份出来作为独立任务，然后在这个任务上增加几个道具扫描项

拆开是两件事：

1. **复制一份**：现在的仓库识别走内核 `Depot` 任务（接口任务 `DepotRecognitionTask`），我们要有一条**自己的**链路，不再受内核任务定义、页签白名单、道具白名单的约束。
2. **增加扫描项**：把内核永远拿不到的那几件道具（寻访凭证 `7003`、十连寻访凭证 `7004`、高级凭证 `4004`、资质凭证 `4005`、家具零件 `3401`、源石碎片 `3141`）扫出来。

**M45 的判定标准**：仓库识别结果**不再由内核的 `classifyType == "MATERIAL"` 名单和那 5 个硬编码 id 决定**，而是由我们自己对本机 `item_index.json` 全量（479 条，含 485 张模板里的 479 张）的识别决定。

## 2. 事实基础

### 2.1 内核的仓库识别是两趟，且第二趟 id 写死 5 个

`src/MaaCore/Task/Miscellaneous/DepotRecognitionTask.cpp`（内核 **v6.18.0**，本机 `resource/lib/maa/Darwin/`）：

- `_run()`（:15-27）= `swipe_and_analyze()` 一趟 + `analyze_basic_items()` 一趟。
- `analyze_basic_items()`（:29-59）：自己匹配 `DepotAllTab` 并点「全部」，然后
  `analyzer.set_item_ids({ "4002", "4003", "4001", "3003", "4006" })`（**:45**，注释「源石 合成玉 龙门币 赤金 采购凭证（红票）」）+ `set_is_basic(true)`（:46）。
- `set_is_basic(true)` 的后果在 `DepotImageAnalyzer.cpp:193-203`：认不出的格子 `continue`，**静默跳过**。
- `swipe_and_analyze()`（:61-92）用 `get_ordered_item_ids()`，即 `ItemData.get_ordered_material_item_id()` —— **`item_index.json` 里 `classifyType == "MATERIAL"` 的 95 个**。

本机 `resource/maa-layers/repo/resource/item_index.json` 实测分类计数：`NONE 212 / MATERIAL 95 / NORMAL 95 / CONSUME 71 / MEMENTO 6`，共 479 条。

**结论**：`7003 寻访凭证 / 7004 十连寻访凭证 / 4004 高级凭证 / 4005 资质凭证 / 3401 家具零件 / 3141 源石碎片` 全是 `NORMAL`，既不在 95 个 MATERIAL 里，也不在那 5 个硬编码 id 里 ⇒ **内核永远拿不到**。上游 issue #2236（2022 开、2026-09 关）+ PR #17287 只补了那 5 个；v6.19.0 与 dev-v2 仍是同一行白名单。

这一条同时回答 M44 留在 `docs/13` §8.64（`docs/13-决策记录.md:1991`）的未定论：「77 件是否等于该账号全部道具类型仍未验证（`7003/7004/3401/3141` 缺席，需逐屏比对判定『确实没有』还是『只读首屏』）」——**缺席的原因就是本节的白名单，不是只读首屏**。

### 2.2 一次真机识别（2026-10-13 08:44，内核日志）实际拿到什么

`resource/maa-api/debug/asst.log:219988`（`DepotInfo`，`done=true`）**77 条**，其中 `4001` 龙门币 `4722000`、`4002` 至纯源石 `475`、`4003` 合成玉 `244000`、`3003` 赤金 `3092`、`4006` 采购凭证 `55`。

**缺的正是 §2.1 那六个 id**（以及 `EPGS_COIN`、`REP_COIN`、`7001/7002` 等全部 `NORMAL`/`CONSUME`/`NONE` 道具）。

### 2.3 M44 修正之一：`DepotAllTabClicked` 是死代码

M44 写了 `DepotAllTabClicked` 节点，模板 `resource/maa-layers/custom/resource/template/Depot/DepotAllTabClicked.png` 是 **600×276**（2560 截图裁的 2 倍图），而节点 `roi` 是 `300×138`（1280 坐标）。内核直接拒绝：

```
asst.log:221804 [2026-10-13 09:22:43.948][ERR] templ size is too large
                 Depot/DepotAllTabClicked.png image size: 300 138 templ size: 600 276
```

该错误在 09:22:43–09:23:35 之间每条重试一次，**全日志 0 次命中**（`grep -c DepotAllTabClicked` 只有这些 ERR 行）。⇒ `Depot → DepotAllTab → DepotAllTabClicked` 这条链的最后一步永远不成立。

### 2.4 M44 修正之二：26 → 77 **不是**页签覆盖带来的

M44 台账记「W1 候选 A 已验证通过（26 → 77 件）」。本轮把内核日志里全部 4 次 `done=true` 的 `DepotInfo` 都取出来对照：

| 日志行 | 时间 | 进程 | 条目数 | `Depot.next` 当时的实际值 |
| --- | --- | --- | --- | --- |
| `150254` | 2026-10-12 15:44:19 | `Px57333` | **77** | `DepotMaterialTab`（内核值，见 `148845`） |
| `150950` | 2026-10-12 15:52:56 | `Px57333` | **26** | 同上 |
| `152844` | 2026-10-12 16:01:07 | `Px60613` | **77** | 同上 |
| `219988` | 2026-10-13 08:45:00 | `Px66769` | **77** | 同上 |

- `asst.log:148845`：`2026-10-12 15:44:05.270 {"class":"asst::ProcessTask","cur_task":"Depot","details":{"cur_retry":0,...,"to_be_recognized":["DepotMaterialTab"]},"first":["DepotBegin"],"pre_task":"DepotBegin","taskchain":"Depot","taskid":1}` —— 走的是**内核** `Depot.next`。
- 那一轮进程 `Px57333` 起于 `2026-10-12 15:40:13`（`asst.log:148116 Version v6.18.0`），而 M44 的合并提交 `0670331` 在 `2026-10-12 16:58:10`；覆盖层文件 mtime 是 `2026-10-13 09:20:17`（`ensure_depot_all_tabs` 在下次启动时才写）。⇒ 15:44 那次**根本没有覆盖层**，77 条完全来自内核自身行为。
- 同一个进程、同一台设备、同一个页面，15:44 拿 77 条、15:52 拿 26 条 ⇒ **77/26 的差别是内核 `swipe_and_analyze()` 的不稳定**（`cur_pos == pre_pos` 或 `analyze()` 失败都会 `break`，见 `DepotRecognitionTask.cpp:75-81`），不是页签差异。

⇒ M44 的 W1「已验证通过（26 → 77）」**证据链不成立，要重做验收**；但「覆盖层能覆盖同名任务」这件事本身是成立的（§2.5），M44 的 W1 不需要推翻实现。

### 2.5 W0-a 结论：custom 层**能**覆盖内核同名任务，按 key 合并、后来者胜

#### 机制（MAA v6.18.0 源码）

1. `AsstLoadResource(path)` → `res_path = path / "resource"` → `ResourceLoader::load(res_path)`
   —— `src/MaaCore/AsstCaller.cpp:55-68`。
2. `ResourceLoader::load` 对任务配置走 `load_with_templ<TaskData>("tasks"_p, "template"_p)`；只有 `path/tasks` 是**目录**时走这条路
   —— `src/MaaCore/Config/ResourceLoader.cpp:167-177`。
3. `TaskData::load(dir)`：递归收集该层 `tasks/**/*.json`，先合并进一个**局部** map；**同一层内出现重复任务名会直接 `load = false`**（跨层不会）；然后 `parse(merged)`
   —— `src/MaaCore/Config/TaskData.cpp:226-280`，重复名在 :264-271。
4. `TaskData::parse` → `lazy_parse(json)` —— `:283`。
5. `lazy_parse`（`:78-105`）合并进**成员** `m_json_all_tasks_info`：
   - 该任务带 `baseTask` ⇒ `m_json_all_tasks_info[name] = task_json`，**整条替换**（:81-83，注释原文「直接声明 baseTask 的任务不继承同名任务参数而是直接覆盖」）；
   - 名字**不存在** ⇒ `emplace`（:95-97）；
   - 名字**已存在** ⇒ **逐 key 赋值 `m_json_all_tasks_info[name][key] = value`，后来者胜**（:98-101）。
6. `m_json_all_tasks_info` **从不被清空**：`clear_tasks()`（`:315-323`）只清 `m_all_tasks_info` / `m_raw_all_tasks_info` 这两个**生成缓存**，并把任务标回 `ToBeGenerate`（全文件 grep `m_json_all_tasks_info` 只有读与逐 key 写，没有 clear/erase）。⇒ 多层加载是**累加式按 key 合并**。
7. 加载顺序（本项目）：先内核自带资源，再 `incremental_paths` 顺序叠加
   —— `maa_api/core/worker.py:277-286`；`incremental_paths = [cache, custom]`
   —— `maa_api/main.py:547-550`。**custom 最后加载 ⇒ 同名 key 最终胜出。**

**注意两点**：
- 覆盖是**逐 key** 的，不是整条替换。只写 `next` 会保留内核的 `template`/`roi`/`sub`/`algorithm`；想整条替换必须显式声明 `baseTask`。（M44 `depot_overlay.py:47-48` 的注释「内核按任务名替换整个定义，只给 next 会把 template/roi/sub 丢掉」是**错的**，实现本身因为每个节点都写了全量 key 所以没出问题。见 §3 文档同步表。）
- `clear_tasks()` 的注释提醒「已经通过 `get()` 获取的任务指针内容不会更新」⇒ **运行期 `LOAD_RESOURCE` 重载**对已经跑过的任务可能不生效；启动期的层加载没有这个问题。

#### 真机实验（2026-10-13，本轮亲做）

临时在 custom 层放一个探针文件（现已删除）：

```json
{
  "MaaApi@W0Probe@OverrideInEffect": {"algorithm": "JustReturn", "action": "DoNothing"},
  "DepotBegin": {"next": ["MaaApi@W0Probe@OverrideInEffect"]}
}
```

`DepotBegin` 是内核任务，`{"algorithm":"JustReturn","next":[9 个内核候选]}`。探针**只给了 `next` 一个 key**。重启服务后提交 `POST /api/pipelines {"tasks":[{"name":"Custom","task_names":["DepotBegin"]}]}`，内核日志（`asst.log`）：

```
223855 SubTaskCompleted {"action":"DoNothing","algorithm":"JustReturn",...,"task":"DepotBegin"}      ← algorithm/action 从内核继承（按 key 合并）
223858 {"cur_task":"DepotBegin","details":{"cur_retry":0,...,"to_be_recognized":["MaaApi@W0Probe@OverrideInEffect"]},"first":["DepotBegin"],"pre_task":"","taskchain":"Custom","taskid":1}
223859 SubTaskStart {"action":"DoNothing","algorithm":"JustReturn",...,"task":"OverrideInEffect"}     ← 链真的走到了新节点
223860 TaskChainCompleted {"taskchain":"Custom","taskid":1}
```

一件事同时证明了两个结论：**同名覆盖生效**（`next` 变成我们的列表，内核那 9 个候选一个不剩）+ **按 key 合并**（没给的 `algorithm: JustReturn` 原样继承）。

**正确的覆盖姿势**（写进 W1 的实现约定）：
- 覆盖内核同名节点：文件放 `resource/maa-layers/custom/resource/tasks/`，**后加载胜**，可以只写要改的 key；
- 想整条替换（含清掉内核的 `sub`/`exceededNext`）：显式写 `"baseTask": "#none"` 或写全量 key；
- 用不同任务名 + `Custom` 任务调用**也能**跑通（M36 `MaaApi@OperatorScan` 就是这么做的），且**更安全**：不会改动内核链；M45 采用这条路（§4.2）；
- 同一层里**不能有重名任务**（会让整层 `TaskData::load` 失败）。
- **模板**：任务没写 `template` 时，内核按任务名推 `"<任务名>.png"`（`src/MaaCore/Config/TaskData.cpp:580-583` 与 `:866-867`）；模板文件放在**同层** `resource/template/` 下的目录树里，内核按名字找（M44 的模板就落在 `custom/resource/template/Depot/DepotAllTabClicked.png`，日志里解析成 `Depot/DepotAllTabClicked.png`）。**模板尺寸必须 ≤ roi 尺寸**，否则内核报 `templ size is too large` 并永远不命中（§2.3）。

### 2.6 W0-b 结论：Python 侧 masked NCC 整页识别闭环（24 格）

实验脚本在 `/tmp/m45/`（`ncc_v3.py` 主程序、`controls.py` / `controls2.py` 对照，**不进版本库**），截图 `/tmp/d1.jpg`、`/tmp/d2.jpg`（2560×1440，仓库「全部」页）。

**几何**（照抄内核，保证可比）：`2560×1440 → 1280×720 → 1066×599`（`DepotMatchData.roi = [0,0,1066,599]`，即 0.8328×）；`x_period = 156*1066//1280 = 129`；`y_first = y_period = 190*599//720 = 158`；`tm_size = 183*720//1080 = 122`；单元格中心 `(x_first+129i, 158+158j)`，`i=0..7, j=0..2`；匹配窗口 = 单元格 122 外扩 `(20,5,20,5)` = `162×132`。模板 485 张（479 张在 `item_index.json` 里），**原生尺度**（在 1066 空间不用缩放）。mask = 模板灰度 `>= 1`（等价内核 `DepotMatchData.maskRange = [1,255]`，即去掉纯黑角）+ 抹掉右下 `80×50`（等价内核 `prepare_cached_templates`）。匹配度量 = 带 mask 的零均值归一化互相关（masked NCC），在窗口内取全位置最大值。

**「mask」是决定性的**：同一格同一位置，带黑角 mask 的 NCC `0.8978`，不带 mask 只有 `0.1126`。

**识别结果（d1，24 格，top-1）**

| 结论 | 数字 |
| --- | --- |
| 24 格里 top-1 得分 | 22 格 `≥ 0.7876`，2 格 `0.5665 / 0.5846` |
| 接受规则 `top1 ≥ 0.75` 且 `top1 − top2 ≥ 0.15` | **18 格接受，全部正确**；6 格拒绝 |
| 被拒绝的 6 格 | 4 格是 `ap_supply_lt_60/80/100/120`（应急理智系列）互相之间 `margin ≤ 0.0123`，规则把它判成「歧义」而不是猜；2 格（c8r2/c8r3）画面上的美术在 479 张模板里**没有对应条目** |
| 与 `maa_api.db` 的 `account_depot_item`（77 条）对照 | 屏幕上的 `4002=475 / 4003=244000 / 4001=4722000 / 3003=3092 / 4006=55` 与库里**逐个相等**；本页另有 **12 个 id 库里没有**：`3141 4004 4005 7001 7002 7003 EPGS_COIN REP_COIN CRISIS_SHOP_COIN_V2 EXTERMINATION_AGENT classic_gacha classic_normal_ticket` |
| 目标 id 命中 | `7003 寻访凭证` **命中 `0.8978`**（第 7 列第 1 行，「全部」页上那张黄色 HEADHUNTING，42 张）；`3141 源石碎片` **命中 `0.8728`**；`7002 加急许可` **命中 `0.9159`**（内核也拿不到这个，它同样不在白名单里） |
| `7004 / 3401` | **本页（d1/d2）画面上就没有**这两件道具。用同一套匹配做**注入召回**正对照：把模板按 1066 空间原始尺寸贴进格子，`7004` → `top1 = 7004 : 0.9999`、`margin 0.3324`；`3401` → `top1 = 3401 : 0.9999`、`margin 0.3770`；另测 `3141/7003/7002` 同理 5/5 命中 ⇒ **链路认得出它们，只是这两张截图里没有** |
| 尺度自适配 | 先固定 1.0（= 1280 空间 1.20×），再试 `0.95/1.05`：**24/24 格都是 1.0 胜**，且 1.0 比 ±5% 里的最好者高 `0.0212–0.2998`（中位 `0.2265`）⇒ 在这套几何下 ±5% 是安全网、不会触发 |
| 稳定性（d1 vs d2，两次独立截图同一页） | top-1 **一致 22/24**，两处差异都落在 `ap_supply_lt_60 ↔ ap_supply_lt_80` 这一对上 |
| 误命中（半格错位，落在行间空白带） | 24 个位置按同一规则判定：**误接受 0/24**；半格位置的 `top1` 最高 `0.7907`、`margin` 最高 `0.1540`（对比：真格 `margin ≥ 0.1664`） |
| 单页耗时 | 精确窗口 `162×132` + 全 479 模板 + 单尺度：**整页 733 ms，单格 30.5 ms**；候选 60 个 → 314 ms；候选 20 个 → 278 ms（老版本用 ±26 px 余量的大窗口是 8.1 s/页，慢在滑窗矩阵物化，不是 NCC 本身） |

**阈值建议（写进 W1 的默认值）**：`接受 ⇔ top1 ≥ 0.75 且 (top1 − top2) ≥ 0.15`，否则该格记为「未知」。理由：单看 `top1` 分不开（真格最低 `0.7876` vs 半格伪峰最高 `0.7907`，重叠），但加 `margin` 后真格/半格干净分开（`0.1664` vs `0.1540`）；`ap_supply` 家族这类**近重复模板**正好被 `margin` 拦下，变成「歧义」而不是错误答案。两个阈值都应可配并记进设置。

**数量（格子角标数字）怎么读 —— 评估，不实现**

- **不推荐数字模板匹配**：位数 1–6 可变、有千分位、还有「万/亿/K/M」缩写（内核 `DepotImageAnalyzer::match_quantity` 就是在处理这四种），字号与描边随位数变化 ⇒ 模板数量爆炸且脆弱。
- **推荐复用内核 OCR 节点**：内核自己就是这么读的 —— `match_quantity` 用 `RegionOCRer` + `set_task_info("NumberOcrReplace")` + `bin_threshold = DepotQuantity.specialParams = [140, 255]` + `use_char_model(false)`（`DepotImageAnalyzer.cpp:330-420`）。我们可以在 custom 层加 24 个 `OcrDetect` 节点（每格一个 roi，`baseTask: NumberOcrReplace`）串成一条 `Custom` 链，走内核现成的 OCR 模型，成本约「24 个 JSON 节点 + 一次取帧」。**已知代价**：内核在 OCR 前会把匹配到的模板按 `0.41` 系数从图标区**减掉**（`cv::subtract`，:362-366）再送 OCR，任务 JSON 做不到这一步 ⇒ 我们的 roi 直接 OCR 会被图标美术干扰，准确率低于内核。因此 W2 的顺序是：**先拿身份、数量分批补**，重叠部分直接用内核 `DepotInfo` 已有的数量，只有内核给不出的那 12 个 id 才走我们自己的 OCR，并且 OCR 失败就**不写数量**（不写 0）。

## 3. 范围

### 3.1 本里程碑做

1. **自己的整页识别器**（不依赖内核任务定义）：
   - 取帧 → 缩放到 1066×599；
   - **网格定位**：不照抄内核那个不稳定的相位法（本页它给出 `x_first = 105`，导致第 8 列 `item1066=1008` 的单元格 `roi.x + 122 = 1069 > 1066` 被 `m_resized_rect.include()` 丢掉，**只扫到 21 格**——内核自己就漏掉最右一列）。改用「行/列能量谱 + 栅格搜索」拟合 8×3（本轮实测：列 `pitch 128.2 / first 107.5`、行 `pitch 162.1 / first 162` 的能量最优解，以及黄圈连通域的独立交叉验证），并用 `DepotBaseRect`（`[0,190,156,190]`，1280 坐标）做兜底先验；
   - 每格 masked NCC 全库打分（原生尺度，`0.95/1.0/1.05` 三点取最优）；
   - 按 §2.6 的 `top1 ≥ 0.75 且 margin ≥ 0.15` 判定接受/未知，输出 top-3。
2. **任务链与取帧接线**：一条只做「进仓库页 → 取帧（可多屏）→ 离开」的 `Custom` 链，用**我们自己的任务名**（不覆盖内核 `Depot*`）；识别在 Python 侧做，不占内核的 `Depot` 任务。
3. **数量**：先只保证身份；数量按 §2.6 的分批策略（重叠 id 用内核 `DepotInfo`，新 id 走 24 个 `NumberOcrReplace` OCR 节点），失败就不写。
4. **合并入库**：识别结果与内核那一趟结果**按 id 合并**（同一 id 取「有数量的那一边」，两边都有数量取我们自己的——因为我们的识别不依赖白名单），继续走 M44 的**下限护栏**（低于阈值整轮不写）。
5. **页面展示**：M44 已经做了道具图与「约」标注，M45 只增加新出现的分类（凭证/寻访/家具/货币）在仓库页的正常展示，不新做页面。

### 3.2 本里程碑不做

1. **不改内核、不发 PR**：上游白名单是上游的事（issue #2236 已关），我们只在自己的层与 Python 侧绕开它。
2. **不做数量 OCR 的模板相减**：那要改内核 C++。
3. **不扫非仓库页**：背包/采购中心/信物兑换不碰。
4. **不做实时/高频识别**：仓库扫描是低频操作，不追求 60 fps，也不放进任何逐帧路径。
5. **不删除 M44 的覆盖层**：`MaaApi@DepotAllTabs.json` 与 `DepotAllTabClicked.png` 暂时留着（M45 的链路不依赖它），删不删留给 M45 收官时一并裁决。

## 4. 设计

### 4.1 识别链路

```
POST /api/pipelines (Custom: MaaApi@DepotSelfScan@*)      ← 我们自己的任务名
  → 进仓库页（复用内核 DepotBegin/Depot 仅用于导航，或自己的 ClickRect）
  → 点「全部」页签（自己的节点，roi [450,0,300,138]）
  → 取帧（Controller.get_image，1280×720）
  → 【Python】缩放到 1066×599
  → 【Python】网格定位（能量谱栅格拟合 + 黄圈连通域交叉验证 + DepotBaseRect 先验）
  → 【Python】每格 masked NCC 全库（479 模板 × {0.95,1.0,1.05}）
  → 【Python】阈值判定：top1 ≥ 0.75 且 margin ≥ 0.15 ⇒ 接受，否则未知
  → 【Python】数量：重叠 id 取内核 DepotInfo；新 id 走 24 个 NumberOcrReplace OCR 节点
  → 与内核那一趟结果按 id 合并 → DepotService（含 M44 下限护栏）→ account_depot_item
```

**W2 实施补充（2026-10-13 真机取证，详见台账 W2 节）**：

- 仓库「全部」页是**横向翻页**：竖向滑动五种姿势（ADB `input swipe` / `draganddrop` /
  内核 `Swipe`）全都不动，横向一划一页，几何照内核自带的 `DepotSlowlySwipeToTheRight`
  （`x 1150 → 110`、`y 340`、200 ms；一页宽 = 8 列 × 129.92 ≈ 1039 px）。链里的翻页节点
  因此叫 `@SwipeNextPage` / `@SwipePrevPage`，**没有** `@SwipeUp`。
- **格距可复用、相位必须逐屏重拟合**：实测列距 `129.56–130.01`、行线 `157.50–159.14`
  （残差 ≤ 2.5 px），但横向相位在 `78–114` 之间漂（先验 `86.6`）。复用先验格线时出现过
  **整屏 0 接受**（相位偏 +27.7 px 的那一帧），逐屏重拟合后恢复到 11 接受。W3 要把 x 轴
  搜索半径从 ±24 px 放宽到 ≥40 px（或半列 ≈65 px）。
- **没有 `@AllTabSelected` 节点**：选中态模板这条路 M44 已经证伪（600×276 > roi 300×138），
  页面对不对改由识别器的 `min_accepted` 回答，不再引二进制资产。
- **数量（§3.1-3）整块挪到 W3**：OCR 的 roi 必须按当屏拟合出的格线现算（相位逐屏漂），
  加上内核 OCR 前的模板相减任务 JSON 做不到，不在 W2 半做（台账 W2 节 D）。
- 「全部」页横滑到底会进**干员信物区**，那里的格线不是 8 列 × 129.92（拟合直接
  `insufficient`）⇒ 逐屏扫描要在「连续两屏没有新增 id」时停，不能按固定页数翻。

### 4.2 为什么用「不同任务名 + Custom」而不是继续覆盖内核节点

- §2.5 已证明同名覆盖**能**生效，但覆盖会**改掉内核链的行为**（M44 就是这么把 `Depot` 的 `next` 改掉的），而 M45 想要的是**并存**：内核那一趟（带 95 个材料的稳定识别 + 数量 OCR）仍然是最好的材料来源，我们只是补它拿不到的。
- 因此 M45 的节点一律用 `MaaApi@DepotSelfScan@…` 前缀，用 `Custom` 任务调用；`Depot`/`DepotAllTab`/`DepotBegin` 一概不动。
- 代价：多一条链、多一次取帧；收益：内核升级不会踩到我们、我们出问题也不会让内核那趟变差。

### 4.3 网格定位（M45 与内核最大的分歧点）

内核那一趟**自己就会漏掉第 8 列**（§3.1-1：`x_first = 105` ⇒ 第 8 列 cell roi 右边界 1069 > 1066，被 `include()` 丢掉）。这条在本轮实验里有两条独立证据：

- 用内核相位法跑本页，只得到 **21 格**（7 列 × 3 行）；
- 内核日志里 15:44 那一趟的所有 `match_templ | ... roi: [x, y, 162, 132]`，`x` 取值只覆盖到 `537/539`（对应第 7 列），**没有第 8 列的 roi**。

所以 M45 的网格**必须自己定**，且要能被验证：能量谱拟合 + 黄圈（稀有度圆环）连通域 + `DepotBaseRect` 先验三路互相印证；三者不一致就**整轮判失败**，不用一个可疑的格子去猜。

### 4.4 阈值与自检

- 接受规则见 §2.6；`margin` 是防近重复模板（`ap_supply` 家族、`emoticon_*` 系列）的关键。
- **自检**：整页接受格数低于下限（默认 8，可配）⇒ 判定整轮失败、不写库 —— 与 M44 的条目数下限护栏同一思路，但更早一层（识别层就拦住）。
- 未知道具**如实记录**：`item_id` 留空、记 `top1` 分数与 rect，写进识别记录，页面上显示「未知道具 ×N」，不猜 id。

### 4.5 与内核那趟结果的合并

- 内核 `DepotInfo` 的 `data` 是 `{item_id: quantity}`，我们的是 `{item_id: {score, margin, rect}}`。
- 合并规则：**并集**；同一 id 以「有数量的一方」为准；两边都有数量 ⇒ 以我们自己的为准（我们的识别不依赖白名单，也不受 `swipe_and_analyze` 提前 `break` 的影响）；两边都没有数量 ⇒ 入库但数量留空。
- M44 的下限护栏复用：合并后的条目数低于阈值仍然整轮不写。

## 5. 兼容与风险

| 风险 | 说明 | 对策 |
| --- | --- | --- |
| 内核升级改模板/改几何 | 模板美术、`DepotMatchData.roi`、`DepotBaseRect` 都随内核版本变 | 几何参数全部从**本机** `tasks.json` 读（`DepotMatchData` / `DepotBaseRect`），不硬编码；内核读不到就整轮失败并记原因 |
| 游戏改版改图标/改布局 | 新道具、图标重绘、页签增减 | 网格定位是**拟合**出来的（不写死坐标）；模板库来自本机 `item_index.json`，新道具跟着资源更新走；识别不到就是「未知」，不写假 id |
| 近重复模板互相误判 | `ap_supply_lt_60/80/100/120`、`emoticon_*` 系列实测 `margin ≤ 0.0123` | `margin ≥ 0.15` 硬门槛；这类格子记「歧义」而不是给答案；W1 单测专门锁这条 |
| 模板与坐标的版本相关性 | 模板按 1280×720 的 1.20× 画，任何分辨率/DPI 变化都会整体失效 | 靠 1066/1280 的比例换算而不是写死像素；`--scales 0.95/1.05` 保留为安全网；分辨率变化时整轮自检会先失败 |
| 误命中（伪峰） | 半格错位实测最高 `top1 0.7907`，与真格最低 `0.7876` 重叠 | 双阈值（分数 + margin）+ 整页接受数下限；不用单格结果直接写库 |
| 取帧时机与动画 | 页签切换有动画，取帧太早会拿到过渡态 | 页签点击节点带 `postDelay`，取帧前先确认页签已是选中态（用 M44 已有的选中态思路，但模板尺寸按 roi 裁对） |
| 运行时资源重载 | `TaskData::clear_tasks()` 不更新已 `get()` 的任务指针（源码注释明说） | 只在**启动期**加载层；运行期 `LOAD_RESOURCE` 之后不复用旧指针（沿用现有 M38/M44 做法） |
| 数量 OCR 被图标美术干扰 | 内核会 `subtract(item_templ * 0.41)`，任务 JSON 做不到 | 数量读不到就**不写**，不写 0；页面对缺失数量显示「—」而不是「0」 |

## 6. 文档同步表

| 文档 | 要改什么 |
| --- | --- |
| `docs/13-决策记录.md` | §8 新增 M45 条目：内核白名单两条（MATERIAL 95 + 5 个硬编码 id）；custom 层**按 key 合并、后来者胜**的机制结论（含源码位置）与 M44 注释纠错；`DepotAllTabClicked` 死代码；26/77 是内核不稳定而非页签覆盖；本页 12 个 id 库里没有；阈值默认值 |
| `docs/04` | 仓库识别的链路从「内核 `Depot` 一趟」改成「内核 `Depot` + M45 自识别两路按 id 合并」；数量来源的分批规则 |
| `docs/05` | 仓库页展示「未知道具」与缺失数量的表现 |
| `docs/12` | M45 里程碑小节 |
| `docs/README.md` | 决策速查表加一行 M45 |
| `docs/ENVIRONMENT.md` | 若 W1 新增了依赖（希望没有），记在这里；内核几何参数的读取位置也记一条 |
| `maa_api/core/depot_overlay.py` | 修正 :47-48 的错误注释（逐 key 合并 vs 整条替换），行为不动 |
| `docs/copy-audit/*` | 仓库页若有新文案，按既有流程更新基线 |

## 7. 验收

1. **W1 单测**：用本轮两张真截图（`/tmp/d1.jpg`、`/tmp/d2.jpg` 转成 fixture）跑识别器，断言：
   - 24 格全部产出结果；
   - 接受格 ≥ 18，且 `4002/4003/4001/3003/4006/3141/4004/4005/7001/7002/7003` 全部在接受的 top-1 里；
   - `ap_supply` 那 4 格判为「歧义/未知」而不是给一个 id；
   - 半格错位输入的误接受数为 **0**。
2. **真机验收**：跑完整链路，`account_depot_item` 里出现 `7003/7004/4004/4005/3401/3141`（至少出现本页实际有的那几件），且 5 个基础道具的数量与内核 `DepotInfo` 一致。
3. **回归**：M44 的下限护栏仍然生效（构造小结果 → 整轮不写）。
4. **门禁**：`pytest -q`、`dump_openapi.py --check`、`pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build`、`scripts/copy_inventory.py --check` 全过。
