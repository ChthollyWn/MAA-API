# M44 台账：仓库识别覆盖全部页签 + 道具图（`m44-depot-all-items`）

- 规格：`docs/superpowers/specs/2026-10-12-m44-depot-all-items.md`
- 计划：`docs/superpowers/plans/2026-10-12-m44-depot-all-items.md`
- 分支基点：`refactor/v2`（tag `v2-m43`）

## 波次进度

| 波次 | 内容 | 状态 |
| --- | --- | --- |
| W0 | 事实取证 | 完成（规格 §2） |
| W1 | 页签链真机实验 + 实现 | 实现完成；**「26 → 77」的验收结论已作废**（M45 W0 推翻归因，见 W1 小节末的更正） |
| W2 | 下限护栏 | 完成（提交 `9a3e7b7`） |
| W3 | 道具图代理 `kind=item` + 仓库页展示 | 完成（见下） |
| W4 | 文档、门禁、真机验收、合并打 tag | 待做 |

## W3 道具图代理与仓库页展示（2026-10-12，子 agent 实现 + 门禁自跑）

**后端**：`ASSET_KINDS` 加 `"item": "item"`（上游 `item/{icon}`）；`GET /api/account/assets/item/<item_id>` 收的是 **item_id**，图标文件名由服务端用本机 `item_index.json` 的 `icon` 字段解析（复用 `capture.py` 的物品索引读取链，新增 `load_item_icons()`，没有第二套解析）——前端不必知道图标叫什么，解析不到直接 404、连上游都不打。`capture.ItemMeta` 因此多了 `icon` 字段（其余两条老字段与既有调用方一字未改）。
拼 URL 与缓存文件都过一个 `_png_filename()`：icon 自带 `.png` 时**不重复补后缀**（不是 `3003.png.png`），缓存落在 `resource/assets-cache/item/3003.png`。白名单正则没动，`3003.png`、`mod_unlock_token.png` 本来就落在 `[A-Za-z0-9_\[\]().\-]` 内。

**实测（本机，2026-10-12）**：`curl -o /dev/null -w '%{http_code}'` 打 `https://web.hycdn.cn/arknights/game/assets/item/{3003.png,mod_unlock_token.png,4001.png}` 三张都是 **200**；走 `MockTransport` 断言服务拼出的 URL 正好是 `.../item/3003.png`（无重复后缀）。本机索引 479 条 icon **全部**带且只带一处 `.png` 后缀。

**前端**：`itemIconPath(itemId)` → `/api/account/assets/item/<item_id>`；仓库每行改成「40px 图标 + 名字 + 分类 + id + `× 数量`」，图标走 `fetch` + blob + object URL（`AssetImage` 从 `OperatorCard.tsx` 抽成共享组件，OperatorCard 的行为与 testid 未动）；取不到图或索引里没有这个 id 时是中性占位（`Package` 图标），不留破图。`format.ts` 加两个纯函数 `isApproximateCountItem` / `approximateCountHint`：`4001` 龙门币 / `4002` 至纯源石 / `4003` 合成玉 的数量前标「约」，`title` 写明「上游识别只给到千位，后三位不准确」。

**顺手改掉的一处过期文案**：仓库页识别状态条与空状态原写「识别只覆盖养成材料页签」、`/api/account/depot` 的路由 description 同样写法——W1 的覆盖层把识别改成走「全部」页签，这句话在 W3 时已经是假事实，一并改成「走仓库的『全部』页签」。（**M45 W0 更正**：改文案这件事本身没错，「26 → 77 件」这个理由错了，见 W1 小节的更正。）这动了 `web/openapi.json` 与 `web/src/types/api.d.ts`（只是 description 文案，响应形状未变），已按项目约定重新生成两份产物并 `--check` 通过。

**新增用例 10 条**：后端服务层 3 条（item_id → icon 映射并验上游 URL 无重复后缀、索引里没有的 id 404 且不打上游、第二次取走磁盘缓存）、路由层 1 条（带 token 200 + 未知 item_id 404 + 无 token 401）、前端 6 条（仓库页 3 条：代理路径与 `blob:` src、取不到图退占位、龙门币「约」与 `title`；`format.ts` 2 条：三件近似值与边界、说明文案只给近似项；原有「只覆盖养成材料页签」那 1 条断言随文案改正而删除）。

**一处踩到的门禁（已修）**：把 `load_item_icons` 放在 `assets.py` 的模块顶层导入时，`import maa_api.main` 会连带拉进 `maa_api.core.enums`，`tests/api/test_app_skeleton.py::test_import_maa_api_main_is_isolated_and_fast` 立刻红（`['maa_api.core', 'maa_api.core.enums']`）。改成 `_resolve_asset_name()` 里的**函数内导入**：只有真的取道具图时才拉 `capture`，那条隔离断言恢复通过。

**门禁（W3 自跑）**：

| 命令 | 结果 |
| --- | --- |
| `pytest -o addopts= -q tests/services/test_account_assets.py tests/api/test_account_assets_router.py tests/services/test_account_depot.py` | 21 passed |
| `pnpm typecheck` | 通过 |
| `pnpm test --run` | 76 文件 / 797 例全部通过（基线 76 / 792，另删掉 1 条过期断言） |
| `pnpm build` | 通过（`SERVICE WORKER OK`） |
| `dump_openapi.py --check` / `gen:api:check` | 重新生成后两份产物均为 current |
| `copy_inventory.py --check` | 先报 13 项上升（W3 的新文档/注释），`--write-baseline` 后「无回潮，0 项较基线下降」 |

**后端全量（`pytest -q`）12 failed / 3811 passed**，逐条核过，没有一条由 W3 引入：

- W2 的护栏遗留（`stash` 到 HEAD 复跑同样失败）：`test_account_capture.py` 2 条 + `test_account_depot_service.py` 7 条——它们仍按「无下限、整表替换」的老语义断言，而 W2 已把默认下限设成 20；
- 真资源依赖：`test_activity_index.py` 2 条（既有，属「依赖真资源的检查」）；
- 抖动：`tests/agent/test_incidents.py` 1 条，单跑该文件 15 passed。

前两组留给 W4 / W2 收尾处理；W3 的验证命令（上面三条）不受影响。


## W1 真机实验（2026-10-12 15:59–16:05）

**页签栏实测**（模拟器 2560×1440，游戏停在仓库页）：`全部 / 消耗物品 / 基础物品 / 养成材料 / 特别纪念`。
内核坐标（1280×720）里 `DepotAllTab` 的 roi `[450,0,300,138]` 正好覆盖「全部」（换算后 x 900–1500，页签中心 ≈1152 ✓），`DepotMaterialTab` 的 `[945,0,300,138]` 覆盖「养成材料」（≈1991 ✓）。

**做法（候选 A）**：在自定义资源层
`resource/maa-layers/custom/resource/tasks/MaaApi@DepotAllTabs.json` 里覆盖三个节点：

| 节点 | 覆盖内容 |
| --- | --- |
| `Depot` | `next` 从 `DepotMaterialTab` 改成 `DepotAllTab`（其余字段照抄内置定义） |
| `DepotAllTab` | `next` 改成 `DepotAllTabClicked`（内置是切回养成材料） |
| `DepotAllTabClicked` | 新增：等「全部」页签进入选中态 |

配套模板 `resource/maa-layers/custom/resource/template/Depot/DepotAllTabClicked.png` 从真机截图裁出（roi `[450,0,300,138]` ×2 倍 → 600×276）。内核资源在启动时加载，改层后需要重启服务。

**结果**（下表**已被 M45 W0 推翻**，见本小节末「更正」）：

| 指标 | 覆盖前 | 覆盖后 |
| --- | --- | --- |
| 一次 `Depot` 识别的条目数 | 26 | **77** |
| 跑完后游戏停在哪个页签 | 养成材料 | **全部**（截图确认） |

**仍待确认**：77 件是否等于该账号的全部道具类型。`7003 寻访凭证 / 7004 十连寻访凭证 / 3401 家具零件 / 3141 源石碎片` 仍不在库里——需要对照「全部」页签实际滚动到底的卡片数，判定是「用户确实没有」还是「内核只读了首屏」。这一步要在 W1 收尾时做（截图逐屏比对），不确定前不写进结论。（**M45 W0 已答**：这四件与 `4004/4005/7001/7002` 等全是 `NORMAL` 道具，既不在内核 `classifyType == "MATERIAL"` 的 95 个里、也不在 `analyze_basic_items()` 硬编码的 5 个 id 里，**内核永远拿不到**——不是「只读首屏」。见 M45 规格 §2.1。）

## W1 更正（M45 W0，2026-10-13）：26 → 77 **不是**页签覆盖带来的

本小节上面那张「26 → 77」的表**不能作为覆盖生效的证据**，M45 W0 用内核日志把它拆开了：

- `asst.log:150254`（2026-10-12 15:44:19，进程 `Px57333`）**在没有覆盖层的情况下**就拿到 **77** 条：该进程起于 15:40:13（`asst.log:148116 Version v6.18.0`），M44 的合并提交 `0670331` 在 16:58:10，覆盖层文件 mtime 是 2026-10-13 09:20:17（`ensure_depot_all_tabs` 下次启动才写）⇒ 15:44 时自定义层根本不存在。那一轮走的也是内核自己的 `Depot.next`（`asst.log:148845` 的 `to_be_recognized` 是 `DepotMaterialTab`）。
- 同进程、同设备、同页面，15:52:56 只有 **26** 条（`asst.log:150950`）⇒ 26 / 77 的差别来自内核 `swipe_and_analyze()` 提前 `break`（`DepotRecognitionTask.cpp:75-81`：`cur_pos == pre_pos` 或 `analyze()` 失败都退），**与页签覆盖无关**。
- 覆盖机制本身**确实有效**，另有一次独立实验直接证明：临时覆盖内核 `DepotBegin` 的 `next` 一个 key，`asst.log:223858` 的 `to_be_recognized` 变成我们的列表、`asst.log:223855` 显示没给的 `algorithm: JustReturn` 被按 key 继承。⇒ M44 的**实现**不用推翻，**验收结论**作废。
- 同一批取证还发现 `DepotAllTabClicked` 是死代码（模板 600×276 > roi 300×138，`asst.log:221804` 每轮报 `templ size is too large`，全日志 0 次命中），M45 W1 已连同模板删除。

⇒ **M44 的真机验收挪到 M45 W3 重做**（判据：跑完完整链路后 `account_depot_item` 里出现内核白名单拿不到的 id）。

## 实测事故（W2 要修）

一次只读到 26 件的识别把 77 行整表替换成 26 行 —— `DepotService.replace_all` 没有下限护栏（M42 给森空岛做过 `SKLAND_MIN_CHARS`，仓库这条一直没有）。
