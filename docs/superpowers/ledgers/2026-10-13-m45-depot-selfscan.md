# M45 台账：不再依赖内核白名单，自己识别仓库全部道具（`m45-depot-selfscan`）

- 规格：`docs/superpowers/specs/2026-10-13-m45-depot-selfscan.md`
- 计划：`docs/superpowers/plans/2026-10-13-m45-depot-selfscan.md`
- 分支基点：`refactor/v2` @ `0670331`（M44 合并）
- 设备：模拟器 `127.0.0.1:5555`，服务 `resource/log/uvicorn.err` + 内核 `resource/maa-api/debug/asst.log`

## 波次进度

| 波次 | 内容 | 状态 |
| --- | --- | --- |
| W0 | 覆盖层生效性（A）+ Python masked NCC 整页识别（B）+ 规格计划 | **完成**（本文件） |
| W1 | 识别器实现 + 单测 | **完成**（见下；死代码删除与 M44 改注也在本波） |
| W2 | 任务链/取帧接线 + 数量 | **取帧接线/任务链/设置项完成 + 多屏取证完成**；数量整块留 W3（见 W2 节 D） |
| W3a | 逐屏扫描 + 合并入库 + 页面语义（不占设备） | **完成**（见下 W3a 节） |
| W3b | 数量读取（探针实测后裁决不做）+ 真机端到端 + M44 重验收 | **完成**（见下 W3b 节；占设备四轮） |
| W4 | 文档、门禁、真机验收、`--no-ff` 合并、tag `v2-m45` | 待做（含 D 节留给主 agent 的覆盖层裁决） |

## W0-a：custom 层覆盖同名内核任务到底生效不生效

### 结论

**生效。** custom 层能覆盖内核同名任务，粒度是**逐 key**（同名任务里我们给的 key 覆盖内核的，没给的 key 保留内核值），前提是这层**在基线之后加载** —— 本项目满足（`incremental_paths = [cache, custom]`，custom 最后）。

### 机制证据（MAA v6.18.0 源码，`/tmp/m45/src/` 是本轮拉的快照）

| # | 位置 | 事实 |
| --- | --- | --- |
| 1 | `src/MaaCore/AsstCaller.cpp:55-68` | `AsstLoadResource(path)` → `res_path = path / "resource"` → `ResourceLoader::load(res_path)` |
| 2 | `src/MaaCore/Config/ResourceLoader.cpp:167-177` | 任务配置走 `load_with_templ<TaskData>("tasks"_p, "template"_p)`，只在 `path/tasks` 是目录时走 |
| 3 | `src/MaaCore/Config/TaskData.cpp:226-280` | `load(dir)` 递归收本层 `tasks/**/*.json` 合并进**局部** map；**同一层重名 ⇒ `load = false`**（:264-271）；跨层不受此限 |
| 4 | `src/MaaCore/Config/TaskData.cpp:283` | `parse` → `lazy_parse` |
| 5 | `src/MaaCore/Config/TaskData.cpp:78-105` | 合并进**成员** `m_json_all_tasks_info`：带 `baseTask` ⇒ **整条替换**（:81-83）；名字不存在 ⇒ `emplace`（:95-97）；**名字已存在 ⇒ 逐 key 赋值，后来者胜**（:98-101） |
| 6 | `src/MaaCore/Config/TaskData.cpp:315-323` | `clear_tasks()` 只清 `m_all_tasks_info` / `m_raw_all_tasks_info` 两个**生成缓存**；`m_json_all_tasks_info` 全文件只被读和逐 key 写，**从不清空** ⇒ 多层加载是累加式合并 |
| 7 | `maa_api/core/worker.py:277-286` | 先 `factory.load(path=maa_path)`（基线），再按 `incremental_paths` 顺序 `factory.load(path=layer, incremental=True)` |
| 8 | `maa_api/main.py:547-550` | `incremental_paths = [resource/maa-layers/cache, resource/maa-layers/custom]` ⇒ **custom 最后 ⇒ 同名 key 最终胜出** |

### 真机实验（本轮亲做，2026-10-13 09:42–09:44）

探针文件（临时写入 custom 层，实验后**已删除**，服务已重启回原状）：

```json
{
  "MaaApi@W0Probe@OverrideInEffect": {"algorithm": "JustReturn", "action": "DoNothing"},
  "DepotBegin": {"next": ["MaaApi@W0Probe@OverrideInEffect"]}
}
```

`DepotBegin` 是内核任务（`{"algorithm":"JustReturn","next":[Depot, DepotMaterialTab, DepotAllTab, DepotMaterialTabClicked, …9 个]}`），探针**只给了 `next` 一个 key**。`./scripts/maa-api-service.sh reload`（23 s 到 200）后提交 `POST /api/pipelines {"tasks":[{"name":"Custom","task_names":["DepotBegin"]}]}`（pipeline `0d1a4874-ab04-46b7-9889-17ca8ba5e1c1`）。内核日志：

```
asst.log:223855 SubTaskCompleted {"action":"DoNothing","algorithm":"JustReturn","exec_times":1,"max_times":2147483647,"result":{},"task":"DepotBegin"}
asst.log:223858 {"class":"asst::ProcessTask","cur_task":"DepotBegin","details":{"cur_retry":0,"retry_times":20,"to_be_recognized":["MaaApi@W0Probe@OverrideInEffect"]},"first":["DepotBegin"],"pre_task":"","subtask":"ProcessTask","taskchain":"Custom","taskid":1}
asst.log:223859 SubTaskStart {"action":"DoNothing","algorithm":"JustReturn",…,"task":"OverrideInEffect"}
asst.log:223860 TaskChainCompleted {"taskchain":"Custom","taskid":1}
```

一次实验同时证明两件事：
- **同名覆盖生效** — `next` 变成我们那一项，内核那 9 个候选一个不剩；
- **按 key 合并** — 我们没给 `algorithm`/`action`，内核的 `JustReturn`/`DoNothing` 原样继承（否则 `DepotBegin` 会退回默认 `MatchTemplate` 找 `DepotBegin.png`，那文件不存在，不会出现 `cur_task` 行）。

第一次提交（09:42）失败在 `DEVICE_NOT_CONNECTED`（服务刚重启，设备还在 1/60 重连，`uvicorn.err` 09:42:18 有记录），与覆盖无关。

### M44 修正 1：`DepotAllTabClicked` 是死代码

```
asst.log:221804 [2026-10-13 09:22:43.948][ERR][Px98188][Tx10747] templ size is too large
                 Depot/DepotAllTabClicked.png image size: 300 138 templ size: 600 276
```

09:22:43–09:23:35 每条重试一行，**全日志 0 次命中**。模板是 600×276（2560 截图裁的 2 倍图），roi 是 300×138（1280 坐标），内核直接拒绝 ⇒ `Depot → DepotAllTab → DepotAllTabClicked` 最后一步永不成立。

顺带记一条**写错但没造成后果**的注释：`maa_api/core/depot_overlay.py:47-48` 写「内核按任务名替换整个定义，只给 next 会把 template/roi/sub 丢掉」——**按 §机制证据 #5 这是错的**（没有 `baseTask` 就是逐 key 合并）。M44 每个节点都写了全量 key，所以行为没问题，只是注释与事实相反，W4 一并改。

### M44 修正 2：26 → 77 **不是**页签覆盖带来的，是内核自身不稳定

内核日志里 4 次 `done=true` 的 `DepotInfo`：

| 日志行 | 时间 | 进程 | 条目数 | 大小 |
| --- | --- | --- | --- | --- |
| `150254` | 2026-10-12 15:44:19 | `Px57333` | **77** | — |
| `150950` | 2026-10-12 15:52:56 | `Px57333` | **26** | — |
| `152844` | 2026-10-12 16:01:07 | `Px60613` | **77** | — |
| `219988` | 2026-10-13 08:45:00 | `Px66769` | **77** | `4001=4722000 4002=475 4003=244000 3003=3092 4006=55` |

- 15:44 那一轮走的是**内核** `Depot.next`：`asst.log:148845 2026-10-12 15:44:05.270 {"cur_task":"Depot","details":{"cur_retry":0,…,"to_be_recognized":["DepotMaterialTab"]},"first":["DepotBegin"],"pre_task":"DepotBegin","taskchain":"Depot","taskid":1}` —— 是 `DepotMaterialTab` 而不是 M44 写的 `DepotAllTab`。
- 那一轮进程 `Px57333` 起于 `2026-10-12 15:40:13`（`asst.log:148116 Version v6.18.0`），M44 合并提交 `0670331` 在 `16:58:10`，覆盖层文件 mtime 是 `2026-10-13 09:20:17`（`ensure_depot_all_tabs` 下次启动才写）⇒ **15:44 时覆盖层根本不存在**，77 条全是内核行为。
- 同一进程、同一设备、同一页面：15:44 = 77、15:52 = 26 ⇒ 差别来自 `swipe_and_analyze()` 提前 `break`（`DepotRecognitionTask.cpp:75-81`：`cur_pos == pre_pos` 或 `analyze()` 失败都退）。

⇒ M44 台账「W1 候选 A 已验证通过（26 → 77 件）」的**证据链不成立**，W1 需重做验收；但「覆盖能生效」成立（上面实验），M44 的实现本身不用推翻。

### M44 覆盖的实际情况

`MaaApi@DepotAllTabs.json` 里 `Depot` / `DepotAllTab` 都被逐 key 覆盖（两者都与内核同 key 集，等于整条替换），`DepotAllTabClicked` 是新增。`DepotAllTabClicked` 永不命中 ⇒ 走到 `DepotAllTab` 点完「全部」之后链就断在那里。**注意这不是「没生效」，是「生效了但链尾是死的」。**

另外：内核 `DepotRecognitionTask::analyze_basic_items()` 自己就会 `set_task_info("DepotAllTab")` 并点「全部」（`DepotRecognitionTask.cpp:34-41`），所以「全部」页签**本来就会被点到一次**；M44 改 `Depot.next` 影响的是**第一趟 `swipe_and_analyze()` 扫的是哪一页**。

## W0-b：Python 侧 masked NCC 整页识别（24 格）

### 几何（照抄内核，保证可比）

| 量 | 值 | 来源 |
| --- | --- | --- |
| 内核工作分辨率 | `1066×599` | `tasks.json:5004-5010` `DepotMatchData.roi = [0,0,1066,599]`；`DepotImageAnalyzer::resize()` 缩到该尺寸（0.8328×） |
| 列间距 | `129` | `DepotBaseRect.roi.width = 156` → `156*1066//1280` |
| 首行 y / 行间距 | `158 / 158` | `DepotBaseRect.roi.y = roi.height = 190` → `190*599//720` |
| 单元格边长 | `122` | `DepotImageAnalyzer.cpp:134` `tm_size = 183*720/1080` |
| 匹配窗口 | `162×132` | 单元格 122 外扩 `(20,5,20,5)`（`match_item` 的 `with_enlarge`） |
| mask | 模板灰度 `>= 1` + 抹右下 `80×50` | 等价内核 `DepotMatchData.maskRange=[1,255]` + `prepare_cached_templates` |
| 模板尺度 | 原生（1066 空间 1.0 ≡ 1280 空间 1.20×） | 实测 1.0 在 24/24 格胜出 |

**mask 是决定性的**：同一格同一位置，带黑角 mask 的 NCC `0.8978`，去掉 mask 只有 `0.1126`。

### 24 格结果（`/tmp/d1.jpg`，top-1，单尺度 1.0）

| 格 | id | 名字 | top1 | margin | 判定 |
| --- | --- | --- | --- | --- | --- |
| c1r1 | 4002 | 至纯源石 | 0.9306 | 0.3815 | 接受 |
| c1r2 | 4003 | 合成玉 | 0.8882 | 0.2582 | 接受 |
| c1r3 | 3141 | 源石碎片 | 0.8728 | 0.2269 | 接受 |
| c2r1 | classic_normal_ticket | 通用凭证 | 0.8953 | 0.2145 | 接受 |
| c2r2 | 4001 | 龙门币 | 0.7876 | 0.1680 | 接受 |
| c2r3 | 3003 | 赤金 | 0.8751 | 0.2044 | 接受 |
| c3r1 | 4004 | 高级凭证 | 0.9062 | 0.2502 | 接受 |
| c3r2 | 4005 | 资质凭证 | 0.9253 | 0.3164 | 接受 |
| c3r3 | 4006 | 采购凭证 | 0.8939 | 0.3118 | 接受 |
| c4r1 | EPGS_COIN | 寻访参数模型 | 0.9023 | 0.2567 | 接受 |
| c4r2 | REP_COIN | 情报凭证 | 0.9037 | 0.3964 | 接受 |
| c4r3 | CRISIS_SHOP_COIN_V2 | 晶体合约赏金 | 0.8535 | 0.2170 | 接受 |
| c5r1 | ap_supply_lt_120 | 应急理智浓缩液 | 0.9372 | **0.0102** | 拒绝（歧义） |
| c5r2 | ap_supply_lt_120 | 应急理智浓缩液 | 0.9319 | **0.0004** | 拒绝（歧义） |
| c5r3 | ap_supply_lt_80 | 应急理智加强剂 | 0.8709 | **0.0033** | 拒绝（歧义） |
| c6r1 | ap_supply_lt_60 | 应急理智合剂 | 0.8437 | **0.0123** | 拒绝（歧义） |
| c6r2 | EXTERMINATION_AGENT | 常态事务代理卡 | 0.8538 | 0.2249 | 接受 |
| c6r3 | EXTERMINATION_AGENT | 常态事务代理卡 | 0.8434 | 0.2179 | 接受 |
| c7r1 | **7003** | **寻访凭证** | **0.8978** | 0.2957 | 接受 |
| c7r2 | classic_gacha | 中坚寻访凭证 | 0.8513 | 0.1664 | 接受 |
| c7r3 | 7001 | 招聘许可 | 0.8692 | 0.2079 | 接受 |
| c8r1 | 7002 | 加急许可 | 0.9159 | 0.3342 | 接受 |
| c8r2 | — | （画面美术在 479 张模板里没有） | 0.5665 | 0.0269 | 拒绝（未知） |
| c8r3 | — | （同上） | 0.5846 | 0.0582 | 拒绝（未知） |

**18 接受 / 6 拒绝，接受的 18 格逐个肉眼核对全对。**

### 误命中与阈值

| 对照组 | top1 | margin | 按 `top1≥0.75 且 margin≥0.15` 判定 |
| --- | --- | --- | --- |
| 真格（接受的那 18 格） | 0.7876 – 0.9372 | 0.1664 – 0.3964 | 18/18 接受 ✅ |
| 未知道具格（c8r2/c8r3） | 0.5665 / 0.5846 | 0.0269 / 0.0582 | 0/2 接受 ✅ |
| **半格错位（落行间空白带）24 个位置** | 最高 **0.7907** | 最高 **0.1540** | **误接受 0/24** ✅ |

单看 `top1` **分不开**（真格最低 `0.7876` < 半格伪峰最高 `0.7907`）；加上 `margin` 才干净分开（真格最低 `0.1664` > 半格最高 `0.1540`）。

**阈值建议：`接受 ⇔ top1 ≥ 0.75 且 (top1 − top2) ≥ 0.15`，否则记「未知」。** 两个值都可配。

### 目标 id

- `7003 寻访凭证`：**命中 0.8978**（c7r1，42 张，黄色 HEADHUNTING）。
- `3141 源石碎片`：**命中 0.8728**。
- `7002 加急许可`：**命中 0.9159**（内核同样拿不到，它也不在白名单）。
- `7004 十连寻访凭证` / `3401 家具零件`：**这两张截图上没有这两件道具**。用同一套匹配做**注入召回**正对照（模板按 1066 空间原始尺寸贴进格子，与真实匹配同条件）：

| 注入 id | top1 | margin | 结果 |
| --- | --- | --- | --- |
| 7004 | `7004 : 0.9999` | 0.3324 | 命中/接受 |
| 3401 | `3401 : 0.9999` | 0.3770 | 命中/接受 |
| 3141 | `3141 : 0.9999` | 0.3811 | 命中/接受 |
| 7003 | `7003 : 0.9999` | 0.2994 | 命中/接受 |
| 7002 | `7002 : 1.0000` | 0.3970 | 命中/接受 |

⇒ **链路认得出 7004/3401，只是本页没有；这一条是「未观测」不是「失败」。**

### 其它数字

- **尺度自适配**：`0.95 / 1.0 / 1.05` 三档，**24/24 格都是 1.0 胜**；1.0 比 ±5% 里的最好者高 `0.0212–0.2998`（中位 `0.2265`）⇒ ±5% 是安全网，本几何下不触发。
- **稳定性**：`/tmp/d1.jpg` vs `/tmp/d2.jpg`（两次独立截图、同一页）top-1 **一致 22/24**；两处差异都在 `ap_supply_lt_60 ↔ ap_supply_lt_80` 这一对上（也正是被 margin 拦下的那 4 格里的 2 格）。
- **单页耗时**（精确窗口 `162×132`，numpy 滑窗矩阵 + 批量 matmul，单尺度）：全 **479** 模板 → **整页 733 ms / 单格 30.5 ms**；**60** 候选 → 314 ms；**20** 候选 → 278 ms。（早期版本给了 ±26 px 余量、窗口 214×184，单格 336 ms、整页 8.1 s —— 慢在滑窗矩阵物化 306 MB，不是 NCC 本身。）
- **与 `account_depot_item`（77 条）对照**：屏幕上的 `4002=475 / 4003=244000 / 4001=4722000 / 3003=3092 / 4006=55` 与库里**逐个相等**；本页另有 **12 个 id 库里没有**：`3141 / 4004 / 4005 / 7001 / 7002 / 7003 / EPGS_COIN / REP_COIN / CRISIS_SHOP_COIN_V2 / EXTERMINATION_AGENT / classic_gacha / classic_normal_ticket`。这就是 M45 要补的那一块。

### 网格：内核自己会漏掉第 8 列

- 内核相位法（`DepotImageAnalyzer::analyze_base_rect`，`DepotImageAnalyzer.cpp:130-176`）在本页给出 `x_first = 105` ⇒ 第 8 列格心 `105+7*129 = 1008`，cell roi `(947, …, 122, 122)`，右边界 `1069 > m_resized_rect.width = 1066`，被 `include()` 丢掉 ⇒ **只扫 21 格**。
- 独立佐证：内核日志 15:44 那一趟所有 `match_templ | … roi: [x, y, 162, 132]` 的 `x` 只到 `537/539`（第 7 列），**没有第 8 列的 roi**；`y` 只有 `92 / 250 / 408`（= 格心 158/316/474）。
- 本轮另用「行/列能量谱 + 栅格搜索」（`grid_fit2.py`：列 `pitch 128.2 / first 107.5`，行 `pitch 162.1 / first 162`）与「金色稀有度圆环连通域投影」（`grid_ring.py`：行带 `128–229 / 317–417 / 507–609` ⇒ 行心 `≈178.5 / 367 / 558` @1280，即 `148.5 / 305 / 464` @1066）两路交叉验证过；两路与内核常量的偏差都在个位数像素内，最终按内核常量跑（保证与内核可比），但**W1 生产实现要用拟合而不是写死**。

### 数量怎么读（评估结论，未实现）

- **不推荐数字模板匹配**：位数 1–6 可变、有千分位、有「万/亿/K/M」四种缩写（内核 `match_quantity` 就是在处理这四种）、字号与描边随位数变 ⇒ 模板爆炸且脆弱。
- **推荐复用内核 OCR 节点**：内核就是这么读的 —— `DepotImageAnalyzer::match_quantity` 用 `RegionOCRer` + `set_task_info("NumberOcrReplace")` + `bin_threshold = DepotQuantity.specialParams = [140,255]` + `use_char_model(false)`（`DepotImageAnalyzer.cpp:330-420`）。我们可以在 custom 层加 24 个 `OcrDetect` 节点（每格一个 roi，`baseTask: NumberOcrReplace`）串一条 `Custom` 链，代价约「24 个 JSON 节点 + 一次取帧」。
- **已知代价**：内核在 OCR 之前会 `cv::subtract(item_templ * 0.41)` 把匹配到的模板从图标区减掉（`:362-366`），任务 JSON 做不到 ⇒ 我们的 roi 直接 OCR 会被图标美术干扰，**准确率低于内核**。所以 W2 的策略是**先身份、数量分批补**：与内核 `DepotInfo` 重叠的 id 直接用内核数量；只有内核给不出的 12 个 id 才走我们自己的 OCR；OCR 失败就**不写数量**（不写 0）。

## W1：识别器 + 单测（2026-10-13，不占设备）

**交付**（4 个提交）：`e6ffaf3` 删 `DepotAllTabClicked` 死代码 + 覆盖层改成逐 key 只写 `next`；
`5cf4f62` M44 验收结论改注；`919ad2e` `maa_api/services/account/depot_scan.py`；
`753c4ad` `tests/services/test_depot_scan.py`（30 例）+ 入库真机帧。

**接口**：`scan_panel(frame, bank, config)` → `PanelScan`（24 格逐格 `status` + top-k 候选 +
格线 + 置信度）；`flatten_scan(scan)` → 纯 dict
`{items: {item_id: 分数}, accepted, ambiguous, unknown, below_min_accepted, warnings}`。
纯函数、可注入（模板库 / 几何 / 资源根都能从参数进来），不碰设备、网络与数据库。

### 实测（本机，`resource/maa-api/debug/interface/2026.10.13-09.22.57.703_raw.png`）

> **注意**：W0 的 `/tmp/d1.jpg`、`/tmp/d2.jpg` 与 `/tmp/m45/` 脚本在本轮开工前被系统重启
> 清掉了（机器 10:54 重启），所以 W1 的实测改用内核 debug 目录里 09:22 那张 1280×720
> 仓库「全部」页截图（已裁小入库为 `tests/services/fixtures/depot/depot_all_tab_1280x720.jpg`，
> 162 KB）。它和 d1 不是同一屏（道具不同），但同样是「全部」页、同样 8×3 格。

| 量 | 值 |
| --- | --- |
| 全库 479 模板整页打分 | **0.82 s**（单尺度，窗口 162×132） |
| 网格拟合 | **0.35 s**（相位偏出窗口余量时先多扫一遍找 landmark，≈ 2.6 s） |
| 24 格结论 | **15 accepted / 6 ambiguous / 3 unknown** |
| 真值核对 | 15 个 accepted 逐格肉眼核对全对（见单测里的 `REAL_FRAME_TRUTH`） |
| 未知的 3 格 | 画面上的美术不在 479 张模板里（top1 0.47~0.53） |
| 歧义的 6 格 | 全部 `margin ≤ 0.03` 的近重复（`ap_supply_lt_*`、`advanced_material_*`、`randomMaterial_12/13`） |
| 目标 id | `7003 寻访凭证` 0.9025、`7002 加急许可` 0.9012、`7001 招聘许可` 0.8741、`4004 高级凭证` 0.8999、`4005 资质凭证` 0.9201、`4006 采购凭证` 0.8986 |

### 两处必须记下来的实现修正（都是「按台账跑会错」的地方）

1. **`x_first` 的默认值原本在错误的坐标空间**。模块里写的是 `104.0`，而那是 1280×720 空间
   的数（内核相位 105）被当成 1066 空间用了 —— 真机帧上量出来的第 1 列格心是 **87**
   （`104 × 1066/1280 = 86.6`）。相位偏 18 px 时靠 ±20 px 的窗口余量勉强不漏格，但第 8 列
   只剩 3 px 余量。改成 86.6 后用 21 件道具的**局部 NCC 全局峰**回归复核：列
   `first 86.95 / pitch 129.57`（残差 ≤ 1.92 px）、行 `first 155.05 / pitch 157.79`
   （残差 ≤ 2.67 px）⇒ 与内核换算值（129.92 / 158.07）只差 0.3 px 级，格距不必逐帧重估。
2. **拟合必须是「分数面 + 查表」**。上一位实现逐条候选格线重跑 NCC：`in_panel` 判的是
   162×132 的窗口 ⇒ 第 8 列永远判死 ⇒ **每条候选都被淘汰，x 轴恒等于先验（拟合等于没有）**；
   即便不淘汰也要 41×32 个候选 × 4 个验证格 ≈ 5000 次全格打分（本机几十秒）。现在先用
   `score_surface` 把每个验证格的分数面压成「格心 → 最高分」剖面，再在 (first, pitch) 网格上
   查表；验证格就是 landmark 自己所在的那几格（每列/每行分数最高的），目标值是
   「这些道具在候选格线上的最高分之和」。真机帧上相位偏 12/20 px 时拟合能把页面拉回来
   （识别结果与先验格线一致），相位本来就对时**不动**格线（分数平台内并列 ⇒ 先验胜出）。

### 与台账常量的一致性

全部一致：面板 `1066×599`、单元格 `122`、匹配窗口 `162×132`（外扩 `20/5/20/5`）、
mask = 模板灰度 `≥ 1` 再抹右下 `80×50`、尺度固定 `1.0`、判定 `top1 ≥ 0.75 且 margin ≥ 0.15`、
格距 `129.92 / 158.07`（2560 空间的 156×190 换算）。两处**有意的偏离**：

- `DEFAULT_SCALES` 只留 `1.0`（台账三档实测 24/24 都是 1.0 胜，三档是 3 倍代价）；
  `DepotScanConfig.scales` 仍可传三档。
- 阈值与候选收窄参数暂时只放在 `DepotScanConfig`，**没进设置 API**（W2 接线时再决定要不要
  暴露成 `account.depot_scan_*`，现在加只会多一批没人读的配置项）。
- 另外补了两条台账没有的口径：`CELL_MAX_CLIP = 12`（单元格被面板边缘裁掉 ≤ 12 px 仍照常
  识别；内核按窗口判边界所以会丢第 8 列）、`min_accepted` 默认 8 只在 `PanelScan` 里记
  `below_min_accepted` + warning，**写不写库留给 W3**。

### 门禁（W1 自跑，2026-10-13）

| 命令 | 尾行 |
| --- | --- |
| `.venv/bin/python -m pytest -o addopts= -q tests/services/test_depot_scan.py tests/core/test_depot_overlay.py` | `33 passed in 8.89s` |
| `.venv/bin/python -m pytest -o addopts= -q tests/services/ tests/core/` | `2 failed, 1887 passed, 5 skipped in 56.00s`（两条都是既有真资源用例 `tests/services/test_activity_index.py`，M24 起既有） |
| `.venv/bin/python scripts/copy_inventory.py --write-baseline && .venv/bin/python scripts/copy_inventory.py --check` | `通过：无回潮，0 项较基线下降` |

全量后端没跑（W4 的活）；本轮只动 `maa_api/services/account/depot_scan.py`、
`maa_api/core/depot_overlay.py`、`maa_api/main.py` 的一行注释、`tests/**` 与 `docs/13`，
没有 `web/`、没有接口形状变化，所以 `dump_openapi.py --check` / `pnpm gen:api:check` 不受影响。

### 遗留 W1 没做的（顺延给 W2/W3）

1. 阈值/下限没进设置 API（见上）。
2. 多屏扫描（滑动后重新拟合）没做 —— W2；本轮只保证单屏 8×3 格。
3. 第 8 列在**真机截图**上是 `993`（右边界 1054 < 1066），拟合之后它本来就在面板内；
   「裁 3 px 仍评估」这条只有合成图用例（`test_clipped_cell_is_still_evaluated`）。
4. 真机帧的真值是肉眼核对的 15 格，不是逐屏比对的**全量**真值 —— 全仓验证仍按规格 §7.2
   在 W3/W4 做。

## W2：取帧接线 + 任务链 + 多屏取证（2026-10-13 下午，占设备一轮）

**交付（4 个提交）**：`6a45999` 四个设置项；`7a3aa4f` `DepotScanService`（取帧接线）；
`9e9ed21` 自识别任务链（启动期写进 custom 层）；本文档。

### A：取帧接线（落地形态）

- 新文件 `maa_api/services/account/depot_scan_service.py`，类 `DepotScanService`：
  - `await scan(*, config=None, force=False) -> DepotScanOutcome`：取帧 → 归一 → 识别 → 统计；
  - `await scan_frame(frame, *, config=None, backend=None)`：离线回放（W3 的「同一批帧同一结果」用它）；
  - `config_from_settings()` / `enabled`：每次都现读设置（`account.depot_scan_*`）。
- **取帧走已有通路，没有新造截图通道**：默认取帧器 `DepotFrameSource(DeviceManager)` 调
  `DeviceManager.screenshot("adb")` —— 与 `GET /api/device/screenshot` 同一条路：ADB 全分辨率
  （本机 2560×1440），ADB 失败时 `DeviceManager` 自己退回内核 `AsstAsyncScreencap` + `GET_IMAGE`。
  真正供货的通道记在 `DepotScanOutcome.backend`。解码（PIL / 内核 JPEG 字节 / RGBA）在服务层做，
  识别核心仍然只吃像素数组（规格 §3.1 的分工）。
- 错误语义（单测钉住）：取帧器抛别的异常 → `SCREENSHOT_FAILED`（带 `stage=depot_self_scan`）；
  设备层 `AppError` 原样上抛（`DEVICE_NOT_CONNECTED` 等）；帧解不出图 → `DepotScanError`；
  未启用 → `DepotScanDisabled`（`scan(force=True)` 给验收用）。**任何一条都不返回空结果**：
  空结果会被下游当成「仓库是空的」，与 M44 下限护栏防的是同一类事故。接受格数不足只如实报告
  `below_min_accepted`，闸门留给 W3。
- 识别在 `asyncio.to_thread` 里跑（单页 24 格 × 479 模板 ≈ 1.2 s 纯 CPU）。

单测 `tests/services/test_depot_scan_service.py` **14 例**：假取帧器给 W1 那张真机帧、真识别器出
15 接受 / 6 歧义 / 3 未知；`ScreenshotResult` 形状的通道名、JPEG 字节、async 取帧器、
`DepotFrameSource` 只转发 `DeviceManager`；取帧失败 / 设备错误透传 / 帧解不出 / 未启用 / force；
阈值与下限改设置立即生效（同一帧 `top1_min=0.95 + min_accepted=24` ⇒ 接受数下降且 `below_min_accepted=True`）。

### C：设置项

`account.depot_scan_enabled` / `depot_scan_top1_min` / `depot_scan_margin_min` /
`depot_scan_min_accepted`，默认 `false / 0.75 / 0.15 / 8`（W0-b 实测值；`enabled` 默认关的理由：
自识别链路要到 W4 真机验收才成立，默认关可以先合进主线而不改变内核那一趟的行为）。
`SETTING_KEYS` + 新的 `AccountSettings` 段 + `settings_schema.py`（group 账号、hot_action none：
服务每次调用现读）+ `config.template.yaml` 一段。账号数据的旧旋钮继续留在 `agent` 段
（那是数据新鲜度与写库闸门），新的四个是识别器参数，所以按规格 §3.1-1 预留的名字新开一段。

### B：多屏滑动取证（真机，模拟器 127.0.0.1:5555）

**过程**：设备本来就在仓库「全部」页。三轮内核会话（脚本 `/tmp/m45w2/run_chain.py`、
`run_prod.py`，不进版本库），每轮：启动内核（基线资源 + `cache` + `custom` 层）→ 连接 →
提交 `Custom` 链（探针节点或生产节点）→ 等链跑完 → `client.screencap()` 取帧（**与 W2 服务
同一条取帧通路**，落 `/tmp/m45w2/frames/*.jpg`，1280×720）。分析脚本 `/tmp/m45w2/analyze.py`
对每帧跑两遍识别：默认（含拟合）与 `fit_grid=False`（复用先验格线），并用「局部 NCC 全局峰」
独立量一次行列格心（行窗口 ±80 px、列窗口 ±40 px，避免串到相邻行/列）。

**第一轮：竖向滑动是死路。** `y 1320 → 180`（ADB `input swipe`）、`y 950 → 300`（同上）、
`input draganddrop`、内核 `Swipe y 570 → 0`（3 行 = 570 px）、`y 620 → 0` 五种姿势，
4 帧的接受格数 17/17/17/17 且接受 id **完全相同**、实测格线与先验逐位相同 ⇒ **仓库「全部」页
不响应竖向滑动**。这一轮还排除了「ADB 注入坏了」：同一支 `input` 的 `tap` 能切页签
（点到「消耗物品」页，17 件道具），只有 swipe 不动。

**第二轮：横向一划一页**（照内核自带 `DepotSlowlySwipeToTheRight` 的几何 `x 1150 → 110`、
`y 340`、200 ms）。9 帧的结论：

| 帧 | 接受 | 歧义 | 未知 | 拟合 | 实测行线 `first/pitch/残差` | 实测列距 `pitch/残差` | 实测 `x_first` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| q00 基准页 | 17 | 4 | 3 | prior | 157.52 / 158.62 / 2.14 | 129.56 / 1.87 | 105.31 |
| q01 下一页 | 14 | 7 | 3 | prior | 159.14 / 157.50 / 2.14 | 129.59 / 1.61 | 86.05 |
| q02 下二页 | **11** | 4 | **9** | **fitted（x_first 108.6）** | 158.22 / 157.87 / 1.22 | 129.66 / **0.36** | **114.33** |
| q03 下三页 | 21 | 0 | 3 | prior | 159.05 / 157.71 / 2.48 | 129.64 / 1.71 | 107.07 |
| q04 下四页 | **24** | 0 | 0 | prior | 159.06 / 157.69 / 2.44 | 129.62 / 1.44 | 84.08 |
| q05 半页横滑 | 23 | 1 | 0 | prior | 158.78 / 157.91 / 1.78 | 129.56 / 1.26 | 85.18 |
| q06 再下一页 | 9 | 13 | 2 | prior | 158.58 / 158.16 / 1.58 | 129.99 / 0.89 | 77.91 |
| q07 反向一页 | 23 | 1 | 0 | prior | 158.78 / 157.91 / 1.78 | 129.57 / 1.20 | 88.23 |
| q08 长距横滑 | 7 | 12 | 5 | prior | 159.00 / 158.00 / 0.00 | 130.01 / 0.72 | 83.26 |

三页相邻的接受 id 集合互不重叠（q00→q01→q02→q03→q04 五页全不重叠）⇒ 一划确实走一页
（一页宽 = 8 列 × 129.92 ≈ 1039 px，与 1040 px 的位移一致）。半页横滑（510 px）落在
「上一页与下一页之间」：q05 与 q04 的接受 id 有重叠，q07 反向一页又精确回到 q05
（23 个 id 逐个相同）。

**结论（回答 W2 要回答的两个问题）**：

1. **格距可以复用，相位必须逐屏重拟合，而且现在这个拟合的搜索半径刚好不够。**
   列距实测 `129.56–130.01`（残差 ≤ 1.87 px）、行线实测 `157.50–159.14`（残差 ≤ 2.48 px），
   与内核换算的先验（`129.92 / 158.07`）都在 2 px 级 ⇒ **格距不用逐屏重估**。
   但**横向相位逐屏在漂**：实测 `x_first` 从 `77.91` 到 `114.33`（先验 `86.6`），极差 36 px。
   q02 就是漂出窗口余量的那一帧：**复用先验格线（`fit_grid=False`）得到 0 接受 / 0 歧义 /
   24 未知**，逐屏重拟合后回到 11 接受 / 4 歧义 / 9 未知（那 9 格是页面右侧的空格）。
   `fit_lattice` 的 `FIRST_SEARCH_MAX = 24` 只能兜住 ±24 px，而这一帧真实偏移 +27.7 px
   （它修到 +22 px 就顶到搜索边界了）⇒ **W3 要把 x 轴的搜索半径放宽到半列（≈65 px）
   或至少 40 px**，否则「滑到一半」的页面会整屏判失败。
2. **逐屏接受格数差异极大（7–24），整轮闸门不能按屏判。** q08 只有 7 格接受、
   q06 只有 9 格、13 格歧义 ⇒ 若把 W1 的 `min_accepted=8` 按屏判，最后几页会被整屏丢掉。
   闸门必须在**整轮聚合**之后判（W3 与 M44 的 `agent.depot_min_items` 一起做）。
3. **「全部」页不是只有道具，横向滑到底会进干员信物区**：第三轮用生产节点连划 5 次
   （起点是上一轮留下的最后一页），跨过材料区后进入 **干员信物/凭证** 区，那里的格子
   **不是 8 列 × 129.92 的格线**（画面每行 8 个但更密、首列被裁），24 格全是未知、
   拟合直接 `insufficient`（分数 ≥ 0.80 的道具不足 3 个）。这正是「该收手了」的信号：
   生产侧要在「本屏没有新增 id」时停（计划 W2 的「连续两屏新增为空就停」），
   不能靠固定翻页次数。

**这一轮顺带钉住的两件事**：`@AllTab` 节点真机命中并停在「全部」页（7.3 s 跑完，含一次重试）；
`@SwipeNextPage` 一划一页（q01–q04 四页互不重叠）。

### 任务链（`MaaApi@DepotSelfScan@*`）

`maa_api/core/depot_selfscan_overlay.py` 在启动期把
`resource/maa-layers/custom/resource/tasks/MaaApi@DepotSelfScan.json` 写进 custom 层
（照 M44 `depot_overlay` 的做法，写不动只记日志）。节点：`@Start`（已在仓库页就点「全部」，
否则 `@EnterDepot` 调内核 `DepotBegin` 进仓库）→ `@AllTab`（`baseTask: DepotAllTab` 继承
模板/roi/动作，只换走向）→ `@SwipeNextPage` / `@SwipePrevPage` → `@Done`。内核节点一个不覆盖
（规格 §4.2），识别全在 Python 侧。

**两处真机踩出来的坑（都写进了注释与单测）**：

1. **用 `baseTask` 的节点也必须显式写 `template`**。内核在加载这一层时给每个需要模板的节点
   找模板，没写就按任务名推 `<任务名>.png`；推出来的文件不存在会**让整层加载失败、内核起不来**
   （12:36 实测：`Templ load failed, file not exists: MaaApi@DepotSelfScan@AllTab.png` →
   `TaskData load failed, path: .../custom/resource/tasks` → `CORE_START_FAILED`）。
   M36 的 `@Back` 也是 `baseTask` + 显式 `template`。
2. **不加「已选中」确认节点**：M44 的 `DepotAllTabClicked` 死在模板 600×276 > roi 300×138，
   W1 已删（`e6ffaf3`），不重新引入二进制资产；页面对不对由识别器的 `min_accepted` 回答。

### D：数量读取 —— **W2 未做，整块留 W3**

不半做的理由（规格 §2.6 的数量评估 + 本轮的新证据）：24 个 `NumberOcrReplace` 节点的 roi
必须落在**这一屏的格心**上，而 B 已经证明横向相位逐屏会漂 ±30 px（q02 那帧偏 27.7 px）——
roi 只能按「当屏拟合出来的格线」现算，也就是每次翻页都要**动态改任务 JSON 或另写一批节点**，
再让内核用新 roi 跑 OCR；加上内核 OCR 前会 `subtract(item_templ * 0.41)` 减掉图标美术
（任务 JSON 做不到，准确率必然低于内核）。这是一件独立的、有风险的活，和「合并入库 + 页面」
一起放 W3 做，比在 W2 尾巴上塞半个版本更安全。**W2 的边界：只做身份，不写任何数量。**

### 门禁（W2 自跑，2026-10-13）

| 命令 | 尾行 |
| --- | --- |
| `.venv/bin/python -m pytest -o addopts= -q tests/services/test_depot_scan.py tests/services/test_depot_scan_service.py` | `44 passed in 18.28s` |
| `.venv/bin/python -m pytest -o addopts= -q tests/services/ tests/core/ tests/api/test_settings_router.py` | `2 failed, 1907 passed, 5 skipped`（两条都是既有真资源用例 `tests/services/test_activity_index.py`） |
| `.venv/bin/python scripts/copy_inventory.py --write-baseline && .venv/bin/python scripts/copy_inventory.py --check` | 见提交说明（新设置项标签进基线） |

### W2 改动面

`maa_api/services/account/depot_scan_service.py`（新）、`maa_api/core/depot_selfscan_overlay.py`（新）、
`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`maa_api/main.py`（一行调用）、
`config.template.yaml`、`tests/services/test_depot_scan_service.py`（新）、
`tests/core/test_depot_selfscan_overlay.py`（新）、`tests/test_settings.py`。
没有 `web/`、没有接口形状变化（`DepotScanService` 还没接 HTTP），所以
`dump_openapi.py --check` / `pnpm gen:api:check` 不受影响。

## W3a：逐屏扫描 + 合并入库 + 页面语义（2026-10-13，不占设备）

**交付（3 个提交）**：`e890b27` 逐屏扫描循环 + 拟合只量相位；`bc97699` 与内核那趟合并入库
（迁移 `0021`）；`f26303d` 仓库页把「未识别」与「不支持」分开。文档与门禁在本节之后收口。

### A：逐屏扫描循环（`DepotScanService.scan_all_pages`）

- **入口**：`scan_all_pages(pager=None, config=None, sweep=None, force=False)`；`pager` 缺省用
  装配时注入的那个（`main.py` 注入 `DepotChainPager(core_client)`），两个都没有就只扫当前一屏。
- **每屏独立**：独立取帧 + 独立 `scan_frame`（**重新拟合格线**）。翻页器返回帧就直接用它
  （离线回放），返回 `None` 就自己取帧（真机）。翻页**不用 adb 硬滑**：真机走链上的
  `MaaApi@DepotSelfScan@SwipeNextPage`（`DepotChainPager` 提交 `Custom` + 等落定，
  `ready` 回调留给 W3b 接内核 `TaskChainCompleted`）。
- **停止条件**（三者之一，`stop_reason` 如实记）：`page_limit`（默认 10）、
  `no_new`（**连续 2 屏没有新增 id**）、`unusable`（连续 2 屏接受格数低于
  `min_page_accepted`＝3）、`page_turn_failed`（翻页器连试两次都抛异常 —— **不作废已扫到的屏**）、
  `no_pager`（没翻页器，扫一屏就停）。
- **整轮聚合**：`items` 是并集（同一 id 取分数最高那次），计数是各屏之和，`below_min_accepted`
  按整轮判 —— W2 实测末页接受格数低到 7，按屏判会把最后几页整屏丢掉。
  计数器 `DEFAULT_MIN_PAGE_ACCEPTED = 3` 与整轮闸门 `min_accepted`（8）**分开**：后者是
  「整轮够不够写库」的口径，拿它当「这一屏不是道具格线区」的判据会误伤真实末页。
- **设置项**：`account.depot_scan_max_pages`（默认 10）/ `account.depot_scan_no_new_pages`
  （默认 2），服务每次调用现读。
- **单测** `tests/services/test_depot_scan_pages.py` **26 例**：相位半径与实测漂移、半格错位
  仍 0 接受、同模板多行不能被顶掉、两屏合并、连续两屏无新增即停、翻页失败保留已扫结果、
  页数上限、整轮聚合闸门、每屏各自重新拟合、翻页器的提交形状与拒绝/超时、取帧只在需要时发生。

### B：拟合口径的三处修正（都是「按台账跑会错」的地方）

1. **`_fit_axis` 不许跳过算不出的 landmark**：旧实现 `continue`，于是总分只统计能算的那几个
   —— 把外侧列推出剖面范围的假格距反而总分更高。合成满屏实测：列距被拉到 122.7（真值 130），
   第 8 列整列错位。现在一个 landmark 算不出就整条格线作废。
2. **格距不参与搜索**（只量相位）：W2 实测列距 `129.56–130.01`、行线 `157.50–159.14`（残差
   ≤ 2.5 px），该量的是相位。同一张满屏画面对照：锁定格距 **21/24** 接受，放开格距 **15/24**。
3. **并列解取平台中心**（不是「离先验最近」）：分数面是平台，取最近的会把格线永远钉在先验上。
   相位偏移 30 px 的满屏画面：半径 20 px 时量出 105（误差 15）、半径 40 px 时量出 115（误差 5）
   —— **平台的跨度由搜索半径决定**，这正是 `FIRST_SEARCH_MAX` 24 → 40 的实测依据。

外加一条：`_diverse` 只按 `(col, row)` 去重会把「同一模板铺在多行」压成 1 个 landmark
（24 格同模板实测只剩 2 个 ⇒ `insufficient`），去重键改成 `(模板, col, row)`。

### C：与内核那趟合并入库（迁移 `0021_depot_sources`）

- **合并语义**（`merge_depot_sources`，纯函数）：并集；两边都有 ⇒ 数量取**内核**（它真读数量，
  自识别 W3a 只保证身份）；只有自识别有 ⇒ `count=None`（＝未识别，写 `NULL` 不写 0）；
  两边都没有 ⇒ 不进表，进 `unrecognized` 清单。
- **来源**：`account_depot_item.sources` ＝ `{item_id: "kernel" | "self_scan"}`（整份快照存每行，
  读侧按行取自己那一项）；识别记录的 `detail` 记来源表 + 未识别清单 + 逐屏计数。
- **未识别的候选范围**：`item_index.json` 里 `MATERIAL` / `NORMAL` / `CONSUME` 三类
  （`MEMENTO` / `NONE` 按机制不进仓库，算进去只会制造噪音）。
- **未启用时行为不变**：`account.depot_scan_enabled` 默认关；关着时采集层拿到 `DepotScanDisabled`
  走「没有自识别」那条路，`DepotService.replace_all` 的 `item_sources` 只会有 `kernel`
  —— 写库路径与 M27 逐字一致。**自识别失败也不影响内核那一趟**（单测钉住）。
- **迁移**：`account_depot_item.count` 放宽到可空、加 `sources`；`account_recognition` 加 `detail`。
  三列都可空、不回填。batch 重建显式传 `copy_from`（含索引、且与 batch 开始时的列集合一致），
  否则 `alembic upgrade --sql` 离线渲染会报 `cannot proceed in --sql mode`。
- **单测**：`tests/db/test_depot_sources_migration.py` 4 例、
  `tests/services/test_account_depot_merge.py` 12 例、`tests/services/test_account_capture.py`
  新增 3 例（合并入库 / 未装配时行为不变 / 自识别失败仍写内核结果）。

### D：仓库页语义（接口 + 页面）

- `GET /api/account/depot` 多四项：每行 `source`；容器 `sources`、`unrecognized` +
  `unrecognized_total`、`coverage`（都是**整份快照**的事实，与筛选、分页无关）。
- 页面：每行来源标签（旧数据没有 `sources` 就**不贴标签**，不猜成内核）、数量 `null` 显示
  「—」并写明「不是 0」、列表上方给覆盖说明（`共 N 种：内核 a、自识别 b（扫了 k 屏）`，
  有漏项时补「m 种这一轮没识别到（不等于 0）」）与未识别清单（最多 8 个名字）。
- 已重新生成 `web/openapi.json` + `web/src/types/api.d.ts`（`pnpm gen:api`）；前端新增 5 条用例，
  `api.test.ts` 的仓库用例改成同时断言四项新字段并加一条「后端少给时给安全默认值」。

### W3a 门禁（自跑，2026-10-13）

| 命令 | 尾行 |
| --- | --- |
| `.venv/bin/python -m pytest -o addopts= -q tests/services/test_depot_scan.py tests/services/test_depot_scan_service.py tests/api/test_account_router.py` | `64 passed, 1 warning in 20.81s`（任务书那条还要求 `-q tests/services/test_depot_scan_pages.py`，本波新加，见下） |
| 同上 + `tests/services/test_depot_scan_pages.py` | 见 W4 收口（W3a 期间每次：`26 passed`） |
| `.venv/bin/python -m pytest -o addopts= -q tests/services/ tests/core/` | `2 failed, 1948 passed, 5 skipped in 103.61s`（两条都是既有真资源用例 `tests/services/test_activity_index.py`，M24 起既有） |
| `.venv/bin/python -m pytest -o addopts= -q tests/api/ tests/db/` | `488 passed`（api）+ `282 passed`（db） |
| `cd web && pnpm typecheck && pnpm test --run` | `Test Files 76 passed (76)` / `Tests 803 passed (803)` |
| `.venv/bin/python scripts/dump_openapi.py --check` + `pnpm gen:api:check` | `OpenAPI snapshot is current` / `OpenAPI types are up to date` |
| `.venv/bin/python scripts/copy_inventory.py --write-baseline && --check` | `通过：无回潮，0 项较基线下降` |

**没有跑**（W4 的活）：全量 `pytest -q`、`pnpm build`、`frontend_smoke` / `account_smoke`、
真机（W3b）。

### W3a 改动面

`maa_api/services/account/depot_scan.py`（拟合口径）、`depot_scan_service.py`（逐屏扫描、翻页器、
合并结果）、`depot.py`（合并 + 来源 + `depot_state`）、`capture.py`（两路合并接线）、
`db/models.py` + `db/repositories/account.py` + 迁移 `0021`、`api/routers/account.py`、
`main.py`（装配 `DepotScanService` + `DepotChainPager`）、`settings.py` / `settings_schema.py` /
`config.template.yaml`（两个新设置项）、`web/src/features/account/{api.ts,DepotTab.tsx}` 与
三个测试文件、`web/openapi.json`、`web/src/types/api.d.ts`、docs 04/05/09/12/13、规格/计划。
**没接真机**（W3b 的活）。

### W3a 顺带修掉的两条假红

1. `tests/test_settings.py::test_env_mapping_names_are_pinned` 的钉死字典里没有 W2 加的
   `account.depot_scan_*` 四项 —— 从 `6a45999` 起那条用例一直红。
2. `tests/api/test_account_router.py::test_depot_list_is_read_only_and_filterable` 把识别时刻
   写死成 `2026-10-10`，跨过 `agent.depot_stale_days`（3 天）之后 `stale` 判 true 就红 ——
   改成「两小时前的整点」。

### 留给 W3b（占设备一轮）

> **W3b 已收口**（见下节），四条逐条回答：1 数量 ⇒ **实测后裁决不做**（A）；
> 2 完成判据 ⇒ 已接 `TaskChainCompleted`（B）；3 真机端到端 + M44 重验收 ⇒ 已做（C/D）；
> 4 设备争抢 ⇒ **会发生**，已修提交路径（C2）。

1. **数量**：24 个 `NumberOcrReplace` 节点的 roi 按当屏拟合出的格线现算（翻页后要换一批），
   与内核重叠的 id 直接用内核数量，我们独有的才走 OCR，读不到**不写数量**。
2. **`DepotChainPager` 的完成判据**：现在默认「等 1.5 s 落定」（链上的 `@SwipeNextPage`
   自带 `postDelay: 1500`）；真机上应改成接内核的 `TaskChainCompleted` 回调。
3. **M44 的真机重验收** + 本轮 W3a 的真机端到端（来源标签、数量「—」、未识别清单肉眼确认）。
4. **自识别与流水线的并发**：`DepotInfo(done=true)` 的回调里直接跑逐屏扫描（每屏 ≈ 1.2 s
   识别 + 1.5 s 落定，10 屏 ≈ 27 s）。回调是独立 `asyncio.Task`（`callback_wiring` 用
   `loop.create_task`），但它占着设备 —— 真机上要确认同一条流水线的下一个任务不会在这个
   窗口里抢设备（必要时改成「先写内核那一趟的结果，自识别跑完再补一次合并写」）。

## W3b：真机端到端 + 数量读取 + M44 重验收（2026-10-13，占设备一轮）

**交付（4 个提交）**：`014738c` 翻页完成判据接内核 `TaskChainCompleted`（B）；
`ee90ff8` 数量 OCR 实测裁决「不做」（A）；`f8259f5` 修逐屏扫描收尾读 PIL 图 `.shape`
的真机崩溃（C1 第一轮踩到）；`c70660c` 翻页提交遇内核忙时退避重试（C2）。

设备：模拟器 `127.0.0.1:5555`；设置 `account.depot_scan_enabled=true`（`config.yaml`，
本轮打开）；内核日志 `resource/maa-api/debug/asst.log`、服务日志 `resource/log/`。

### A：数量读取 —— 探针两轮实测后裁决**不做**

探针：临时把 `MaaApi@W3bQty.json` 写进 custom 层（**跑完已删**，不入库；服务重启加载），
节点形如 `{"baseTask": "NumberOcrReplace", "text": [], "roi": [...], "action": "DoNothing",
"maxTimes": 1}`，roi 一律**按当屏拟合出来的格线现算**（1066 空间格心 × 1280/1066 + 偏移）。

**探针一**（6 格 × 3 种 roi 变体 ＝ 18 节点；格心用生产识别器在 `/tmp/w3b/page1.jpg` 上
拟合出来的格线）：

| 真值（肉眼 + 内核 `DepotInfo`） | 紧带 roi（x −25..+72 / y +24..+54 @1280） | 宽带 | 右下象限 |
| --- | --- | --- | --- |
| 4002 = 475 | **475**（0.999951） | 475（0.998752） | 475（0.999801） |
| 7003 = 42 | **42**（0.986288） | 42（0.960114） | 42（0.999760） |
| 7002 = 1302 | **1302**（0.999990） | 1302（0.999975） | 1N/1ED/**1302**（多候选） |
| 4001 = 472.2万 | 472.25（0.923594） ✗ | 472.27（0.957317） ✗ | 2.2F（0.830189） ✗ |
| 3141 = 330 | **330**（0.999993） | 330（0.999887） | 330（0.999882） |
| 3003 = 3092 | **3092**（0.999991） | 3092（0.999926） | 3092（0.997149） |

⇒ 纯数字 15/15 与画面逐个一致；**万/亿缩写 3/3 全错**（`472.2万` → `472.25` / `472.27` / `2.2F`）。

**探针二**（**生产形状**：格心 + 固定偏移 `110×40` @1280，整页 24 格，一次链跑完；
结果在 `asst.log:232525-232693`）：**13 对 / 6 错 / 5 读不出**。最要命的一条：

```
asst.log:232588 [2026-10-13 13:55:16.414][TRC] analyze | OcrDetect MaaApi@W3bQty@r1c8
                 [{ text: 302, rect: [ 1216, 219, 42, 19 ], score: 0.999491 }]
```

`r1c8` 是 **7002 加急许可 = 1302**：读成 `302`，**掉首位**，分数还有 **0.9995** ——
「分数门槛 + 纯数字白名单」这套安全网**拦不住**。其余错读：`24.4万 → 4`（0.969831）、
`472.2万 → 472.275`（0.897287）、`1.6万 → 1.67`（0.810791）、两格读成字母 `E`/`M`；
5 格（计数是单个数字 5/2/1/2/5）整格读不出。

**根因**（规格 §5 风险表那条）：内核 `match_quantity` 在 OCR 前会
`subtract(item_templ * 0.41)` 把匹配到的模板从图标区减掉，任务 JSON 做不到；
roi 一放大到能兜住逐屏相位漂移（实测 ±30 px），图标美术就进了 OCR 的输入。
**写错一个数量比不写更糟**（页面会把错数字当事实显示）。

**裁决（2026-10-13，不改对外接口）**：**不做数量 OCR**。自识别独有的 id 一律
`count = NULL`（页面显示「—」），数量继续以内核 `DepotInfo` 那一趟为准。
代码里只落文档口径（`depot_scan_service` / `depot_selfscan_overlay` 模块说明），
规格 §3.1-3 / §4.1 / §5 与 `docs/13` §8.66 同步。

### B：`DepotChainPager` 的完成判据改成事件驱动

- 新增 `_ChainCompletionTap`：订阅 `CoreClient` 的 `CALLBACK`，按 `append_task` 返回的
  `taskid` 收 `TaskChainCompleted`（等价信号 `AllTasksCompleted.finished_tasks`）；
  内核回调跑在消费线程 ⇒ `loop.call_soon_threadsafe` 唤醒 `asyncio.Event`。
- **超时兜底保留**：`completion_seconds`（默认 6 s，真机实测一趟翻页链 **2.8 s**：
  14:24:39.640 提交 → 14:24:43.207 完成）等不到信号就退回 `settle_seconds` 落定。
- 链自己报错（`TaskChainError` / `TaskChainStopped`）不再当「翻好了」：抛
  `DepotScanError` → 扫描循环记 `page_turn_failed` 并保留已扫到的屏。
- `ready` 注入点保留（离线回放/更精确判据）。单测 4 条：正常路径按回调立即返回
  （`settle_seconds` 给 5 s，靠耗时把两条路径分开）、`AllTasksCompleted` 等价信号、
  等不到回调时退回落定（不抛）、链报错抛错。

### C1：真机端到端 —— 条目数 77 → **96**（内核 77 + 自识别 19）

**第一轮先在真机上撞到一个崩溃**：8 屏全部扫完，收尾时 `scan_all_pages` 读
`captured.image.shape` —— 真机取帧器（`DepotFrameSource` → `DeviceManager.screenshot`）
给的是 `PIL.Image`，没有 `.shape`。异常被 `_scan_depot` 的兜底吞成「这一轮没有自识别」：

```
resource/log/2026-10-13.log:1355 2026-10-13 14:05:20 - maa_api.services.account.capture -
  WARNING - 仓库自识别未完成，本轮只写内核那一趟的结果：
  AttributeError("'Image' object has no attribute 'shape'")
```

8 屏结果全丢、库里只写内核那趟的 76 条。**已修**（`f8259f5`，改用逐屏结果里解码好的
尺寸），并补了「帧源给 PIL 图」的回归用例（改回旧写法即红，已核对）。

修好后正常路径（16:20 那一轮，从游戏主界面进仓库，`Depot.next` 用内核值，
见 D 的 A/B；详见 §D 的说明）实测：

| 量 | 值 |
| --- | --- |
| `account_depot_item` 条目数 | **96**（旧数据 77） |
| 其中 `source=kernel` | **77**（全部有数量） |
| 其中 `source=self_scan` | **19**（全部 `count = NULL`，页面「—」） |
| 基础 5 项数量 | `4001=4722000 / 4002=475 / 4003=244000 / 3003=3092 / 4006=55`，与内核 `DepotInfo` 逐个相等 |
| 逐屏扫描 | 6 屏，`stop_reason=no_new`，接受 72 / 歧义 12 / 未知 60，整轮达标（`below_min_accepted=false`） |
| 识别记录 `detail.scanned` | `{"pages": 6, "stop_reason": "no_new", "accepted": 72, "ambiguous": 12, "unknown": 60, "below_min_accepted": false}` |

**新增（自识别独有）的 19 个 id**：

```
3141 4004 4005 7001 7002 7003 CRISIS_SHOP_COIN_V2 EPGS_COIN EXTERMINATION_AGENT
REP_COIN classic_gacha classic_normal_ticket randomMaterial_act42side
randomMaterial_act53side randomMaterial_amiya randomMaterial_message1
randomMaterial_message2 randomMaterial_message3 randomMaterial_message4
```

- 任务书点名的 `7003 寻访凭证 / 4004 高级凭证 / 4005 资质凭证 / 3141 源石碎片 /
  7001 招聘许可 / 7002 加急许可` **全部进来了**（W0-b 的整页识别命中，这次真的落库）。
- `7004 十连寻访凭证` 与 `3401 家具零件` **不在库里**：本账号的仓库里就没有这两件
  （W0-b 的注入召回正对照 `top1 = 0.9999` 证明链路认得出它们，是「未观测」不是「失败」）。
- `source` 是**整份快照**（`account_depot_item.sources` ＝ `{item_id: "kernel"|"self_scan"}`，
  每行都存同一份，读侧按行取自己那一项）；接口层每行给 `source`，容器给 `sources` / `coverage`。
- 另一轮（14:29，设备已在仓库内、3 屏就 `no_new`）拿到 88 条（内核 77 + 自识别 11）——
  **条目数随「这一轮扫到几屏」浮动**，19 与 11 都是真实观测；`no_new` 收手会因起始页不同而提前。

### C2：设备争抢 —— **会**，已修提交路径

真机 `POST /api/pipelines {"tasks":[{"name":"Depot"},{"name":"Award"}]}`（14:24）实测：

```
asst.log 14:24:30.197 TaskChainCompleted {"taskchain":"Depot","taskid":7}      ← 内核那趟结束，回调里开始自识别
asst.log 14:24:31.233 TaskChainStart     {"taskchain":"Award","taskid":8}      ← 同流水线下一个任务 1 s 后就抢设备
asst.log 14:24:39.640 append_task Custom {"task_names":["MaaApi@DepotSelfScan@SwipeNextPage"]}   ← 第 1 次提交
asst.log 14:24:39.643 append_task Custom {"task_names":["MaaApi@DepotSelfScan@SwipeNextPage"]}   ← 第 2 次（重试）
asst.log 14:24:40.457 TaskChainCompleted {"taskchain":"Award","taskid":8}
asst.log 14:24:40.659 run | run subtask 1 / 1 SwipeNextPage                    ← 翻页链到这时才真正跑
```

⇒ **会抢**：`DepotInfo(done=true)` 的采集任务与流水线下一个任务没有任何串行关系（回调是
独立 `asyncio.Task`）。后果实测：两次 `start()` 都被拒（内核忙着 `Award`），旧实现当场抛错 →
`逐屏扫描：1 屏（page_turn_failed）`，自识别独有的 id 从 19 掉到 **11**（96 → 88 行）。

**改法**（`c70660c`）：`_submit()` 在 `timeout_seconds` 预算内按 `retry_delay`（默认 0.5 s）
**只重试 `start()`**，不重复 `append_task`（重复 append 会把同一趟链排两遍、多滑一页）；
预算用尽仍被拒才抛 `DepotScanError`。实测同轮 `Award` 9 s 就结束，等一等完全来得及。
单测 2 条：忙 3 轮后成功（且 append 只有 1 次）、一直忙则到预算报错。

**没有改成「先写内核结果、自识别跑完再合并补写」**：那条备选不消除设备争抢本身
（下一个任务照样抢），而内核那一趟本来就不会丢 —— `_scan_depot` 一个异常都不外抛，
合并写始终会把内核的行写进去（本轮争抢下仍写出 77 条内核 + 11 条自识别）；
真正的失效点是「提交被拒就放弃」，所以修在那里。**遗留**：自识别的翻页链仍是绕过流水线
队列直接交给内核的（`append_task` + `start`），与同流水线任务并发时只能靠退避错开；
要彻底串行化得让采集任务与流水线任务共用一把设备锁（W4 可评估）。

### C3：页面（接口 + 页面字段）

`GET /api/account/depot`（14:25 实测，那一轮是争抢后的 88 行）字段：

```
顶层: categories, coverage, items, limit, offset, sources, total, unrecognized, unrecognized_total
coverage: {"total": 88, "kernel": 77, "self_scan": 11, "unrecognized": 173,
           "scanned": {"pages": 1, "stop_reason": "page_turn_failed", ...}}
每行: {"item_id":"3141","name":"源石碎片","count":null,"category":"NORMAL",
       "recognized_at":"...","source":"self_scan"}
```

- 自识别独有的行 `count = null` + `source = self_scan`（页面显示「—」，不是 0）✔
- 内核的行全部有数量、`source = kernel` ✔；旧数据（无 `sources`）不贴标签 ✔
- `unrecognized` / `unrecognized_total`（173 = 候选目录里这一轮没识别到的）✔
- 前端用例（W3a 加的 5 条 + `api.test.ts`）覆盖「—」、来源标签、覆盖说明与未识别清单，
  本轮 `pnpm test --run` 全绿；页面肉眼确认靠接口字段 + 前端用例两道（本轮没有浏览器截图，
  GUI 在 19387 是另一个应用，MAA 自己的 Web 端只做了接口与用例核对）。

### D：M44 重验收 —— 覆盖层**逐 key 生效**，但它在「从主界面进仓库」这条正常路径上把仓库识别打挂

**逐 key 生效的直接日志证据**（同一场景 A/B：从游戏主界面提交 `Depot`）：

| 覆盖层 | 日志原文 | 结果 |
| --- | --- | --- |
| 在（默认） | `asst.log:236732`（14:13:59.932）`{"class":"asst::ProcessTask","cur_task":"Depot","details":{"cur_retry":0,"retry_times":20,"to_be_recognized":["DepotAllTab"]},"first":["DepotBegin"],"pre_task":"DepotBegin","subtask":"ProcessTask","taskchain":"Depot","taskid":7}` | **我们的** `DepotAllTab`（内核值是 `DepotMaterialTab`）⇒ 覆盖生效 |
| 写不进去（A/B，临时把目标路径换成目录让 `ensure_depot_all_tabs` 写失败） | `asst.log:243716`（14:20:46.237）同一条日志的 `to_be_recognized` 变成 `["DepotMaterialTab"]` | 内核值 ⇒ 对照成立 |

**但这条覆盖在正常路径上是负面的**（两轮复现：14:13 与 14:17）：

1. `Depot` 命中后（它是「主界面 → 进仓库」的入口，模板 `SwitchTheme@ToggleSettingsMenu.png`），
   链按我们的 `next` 去找 `DepotAllTab`，**连试 20 次都匹配不上**（14:13:59.932 → 14:14:11.446，
   `cur_retry` 0 → 20）→ `SubTaskError`。
2. 重试窗口的截图（`/tmp/w3b/frames/f15.jpg`，14:17:56）显示那一屏**就是仓库页且「全部」页签已选中**。
   自算 masked NCC：`DepotAllTab.png`（77×34，内核自带模板）在「全部已选中」时最高只有
   **0.4710**，在「全部未选中」时才是 **0.8369**（= 14:01:40 C++ `analyze_basic_items` 能点中的那次）
   ⇒ **该模板是未选中态**，`Depot.next = ["DepotAllTab"]` 在「仓库已经停在全部页」时必然失败。
3. 后果：`analyze_basic_items` 返回 `{"data":"{}","done":true}` + `TaskChainError`
   （14:13:14.024 / 14:17:18.065），整轮仓库识别**空结果**；采集层空结果护栏拦住不写库
   ⇒ **M45 自识别也一并没跑**（`_scan_depot` 在空结果判断之后才调用）。
4. 而 14:01 / 14:09 那两轮之所以成功，是因为设备**本来就在仓库里**：`Depot` 节点没命中
   （仓库页没有那个模板），`DepotBegin` 的候选直接落到 `DepotMaterialTab`，覆盖层那条
   `next` 根本没被走到；随后 C++ 自己 `match_templ | DepotAllTab.png score: 0.836907`
   点了「全部」（14:01:40.199，`rect [516,21,77,34] roi [450,0,300,138]`）。

**条目数对照（覆盖层生效前 vs 后）**：

| 时间 | 覆盖层 | 起始画面 | `Depot.next` 实际值 | 条目数 |
| --- | --- | --- | --- | --- |
| 2026-10-12 15:44（`asst.log:148845`） | **无**（覆盖层 10-13 09:20 才写出） | 非仓库页（`Depot` 命中） | `["DepotMaterialTab"]` | **77** |
| 2026-10-13 08:45（`asst.log:219988`） | 无 | 仓库页内 | 未走到 | **77** |
| 2026-10-13 14:01 / 14:09 | 有 | 仓库页内（覆盖层被绕过） | 未走到 | 76 / 76 |
| 2026-10-13 14:13 / 14:17 | 有 | 主界面（覆盖层被走到） | `["DepotAllTab"]` | **空 `{}`** |

⇒ **M44 那条「26 → 77 是页签覆盖的功劳」彻底不成立**（W0 已证 26/77 是内核自身
`swipe_and_analyze()` 提前 `break`），而 W3b 进一步实测：**这条覆盖在正常路径上把整轮识别
打成空结果**。`docs/13` §8.64 的「验收挪到 W3 重做」据此改成**已重做，结论是「覆盖本身既
不是 26→77 的原因，又会在从主界面进仓库时把识别打挂」**。

**留给 W4 的裁决（需要主 agent 定，我没有自行改）**：删掉
`maa_api/core/depot_overlay.py` 生成的 `MaaApi@DepotAllTabs.json`（M44 的覆盖层），
或至少把 `Depot.next` 改回内核值。理由：① 它的 `DepotAllTabClicked` 早在 W1 就被判死代码；
② `Depot.next` 这条在正常路径上打挂识别；③ M45 的链路不依赖它（自己的链用
`baseTask: DepotAllTab` + 显式 `template`，只在需要点页签时用，且是幂等的可选一步）。

### W3b 门禁与改动面

见文末「## 门禁」一节。改动面：`maa_api/services/account/depot_scan_service.py`、
`maa_api/core/depot_selfscan_overlay.py`（文档口径）、
`tests/services/test_depot_scan_pages.py`；没有接口形状变化，没有 `web/` 改动。

## W0 遗留 / 未取证

1. **M44 W1 需重做验收**（§修正 2）：覆盖机制成立，但「26 → 77」的证据链错了；M44 台账与 `docs/13` §8.64 的对应说法要在 W4 一并更正。
   > **W3b 已重做**：见 §D —— 覆盖层逐 key 生效有 A/B 日志（`asst.log:236732` vs `243716`），
   > 但它在「从主界面进仓库」这条正常路径上把整轮识别打成空结果（两轮复现）；
   > 26/77 与覆盖无关的结论不变。`docs/13` §8.64 已按此改写。
2. **`DepotAllTabClicked` 死代码**：W1 裁决 = **删掉**（节点 + 自带模板 + 资产目录），`DepotAllTab.next` 覆盖成空链（点完「全部」就地收尾）。见提交 `e6ffaf3`。
3. **第 8 列的对齐**：W1 已做 —— 拟合出真实相位（真机帧 `x_first ≈ 87`，第 8 列格心 993），边界只作兜底钳制（`CELL_MAX_CLIP = 12`），单测 `test_eighth_column_is_clipped_not_dropped` / `test_clipped_cell_is_still_evaluated` 钉住。
4. **多屏扫描**：W2 已取证（见上）：竖向滑动无效、横向一划一页；格距可复用，横向相位逐屏漂
   最多 36 px，必须逐屏重拟合，且 `FIRST_SEARCH_MAX = 24` 要放宽（W3）。
5. **`7003` 数量 42 没进 DB**：本页 `7003` 数量肉眼是 42，但库里没有这一行；我们的 OCR 路径能不能稳定读出 42 未验证（§数量评估是方案，不是结果）。
   > **W3b 已收口**：`7003` 的身份**已进库**（`source=self_scan`，W3b-C1）；数量仍留 `NULL`
   > —— 探针在「生产形状 roi」下把 `7002` 的 1302 读成 302（分数 0.9995），数量 OCR 整块裁决不做（§W3b-A）。
6. **服务重启后的首次提交**：`DEVICE_NOT_CONNECTED`（09:42:18）说明重启后设备重连窗口约 20–25 s，真机实验要等 `uvicorn.err` 出现 `connected（原因 ADB 与内核均已确认连接）` 再提交。
   > **W3b 实测**：本轮四次 `reload` 的 `connected` 时间分别是 33 / 45 / 36 / 39 / 36 s，
   > 比 20–25 s 更保守，等 40 s 再提交最稳。
7. **W3b 新留的两条**：① M44 覆盖层的删/改裁决（见 §D 末尾，**要主 agent 定**）；
   ② 自识别翻页链绕过流水线队列直接 `append_task`，与同流水线任务并发时只靠退避错开，
   彻底串行化要一把设备锁（W4 可评估）。另有一条性能观察：真机上一轮 6 屏耗时
   **128–172 s**（逐屏 8.5–42 s），同一帧在空闲进程里只要 1.3–4.2 s —— 服务进程与
   模拟器/内核抢 CPU 时慢一个量级，把设备占用窗口从 W3a 估计的 27 s 拉到 2–3 分钟。

## 门禁

本轮**只改文档**（`docs/superpowers/*`），不动 `maa_api/`、`web/`，`resource/` 是 gitignore 的。因此没有跑后端/前端门禁；W1 起按计划跑。

### W3b 门禁（自跑，2026-10-13）

| 命令 | 尾行 |
| --- | --- |
| `.venv/bin/python -m pytest -o addopts= -q tests/services/test_depot_scan.py tests/services/test_depot_scan_service.py` | `44 passed in 21.79s` |
| 加 `tests/services/test_depot_scan_pages.py`（三条一起） | `77 passed in 59.70s` |
| `.venv/bin/python -m pytest -o addopts= -q tests/services/ tests/core/ tests/api/` | `2 failed, 2443 passed, 5 skipped, 23 warnings in 197.72s`（两条都是既有真资源用例 `tests/services/test_activity_index.py::test_real_resource_pack_reports_the_current_activity_and_its_stages` / `…disambiguates_a_repeated_code`，M24 起既有） |
| `cd web && pnpm typecheck && pnpm test --run` | typecheck 无输出（干净）＋ `Test Files 76 passed (76)` / `Tests 803 passed (803)` |
| `.venv/bin/python scripts/copy_inventory.py --write-baseline && .venv/bin/python scripts/copy_inventory.py --check` | 基线 45 项 + `通过：无回潮，0 项较基线下降` |

真机证据文件都在 `/tmp/w3b/`（探针生成脚本 `make_probe.py`、格线拟合 `fit.py`、
重试窗口抓帧 `frames/`、`ocr_lines.txt`），**不进版本库**；探针任务文件
`resource/maa-layers/custom/resource/tasks/MaaApi@W3bQty.json` 跑完已删。

实验脚本与中间产物全部在 `/tmp/m45/`（`ncc_v3.py`、`controls.py`、`controls2.py`、`grid_fit*.py`、`grid_ring.py`、`probe7003.py`、`probe7002*.py`、`r_main.json`、`r_gap.json`、`r_scales.json`、`r_d2.json`、`overlay_grid.png`、`cmp7002.png`），**不进版本库**。
