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
| W2 | 任务链/取帧接线 + 数量 | 待做 |
| W3 | 合并入库与页面展示 | 待做 |
| W4 | 文档、门禁、真机验收、`--no-ff` 合并、tag `v2-m45` | 待做 |

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

## W0 遗留 / 未取证

1. **M44 W1 需重做验收**（§修正 2）：覆盖机制成立，但「26 → 77」的证据链错了；M44 台账与 `docs/13` §8.64 的对应说法要在 W4 一并更正。
2. **`DepotAllTabClicked` 死代码**：W1 裁决 = **删掉**（节点 + 自带模板 + 资产目录），`DepotAllTab.next` 覆盖成空链（点完「全部」就地收尾）。见提交 `e6ffaf3`。
3. **第 8 列的对齐**：W1 已做 —— 拟合出真实相位（真机帧 `x_first ≈ 87`，第 8 列格心 993），边界只作兜底钳制（`CELL_MAX_CLIP = 12`），单测 `test_eighth_column_is_clipped_not_dropped` / `test_clipped_cell_is_still_evaluated` 钉住。
4. **多屏扫描**：M45 只验证了单屏 24 格。仓库「全部」页需要滑动才能看全，滑动后的网格是否仍可用（尤其首列是否被裁）**未取证**，W2 要处理。
5. **`7003` 数量 42 没进 DB**：本页 `7003` 数量肉眼是 42，但库里没有这一行；我们的 OCR 路径能不能稳定读出 42 未验证（§数量评估是方案，不是结果）。
6. **服务重启后的首次提交**：`DEVICE_NOT_CONNECTED`（09:42:18）说明重启后设备重连窗口约 20–25 s，真机实验要等 `uvicorn.err` 出现 `connected（原因 ADB 与内核均已确认连接）` 再提交。

## 门禁

本轮**只改文档**（`docs/superpowers/*`），不动 `maa_api/`、`web/`，`resource/` 是 gitignore 的。因此没有跑后端/前端门禁；W1 起按计划跑。

实验脚本与中间产物全部在 `/tmp/m45/`（`ncc_v3.py`、`controls.py`、`controls2.py`、`grid_fit*.py`、`grid_ring.py`、`probe7003.py`、`probe7002*.py`、`r_main.json`、`r_gap.json`、`r_scales.json`、`r_d2.json`、`overlay_grid.png`、`cmp7002.png`），**不进版本库**。
