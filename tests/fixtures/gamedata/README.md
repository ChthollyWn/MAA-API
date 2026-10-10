# 拆包数据与作业夹具（M29）

真实响应的**裁剪快照**（抓取日期 **2026-10-10**），用于 M29 的坐标探针（规格 §9.2 T1 第①条）、
拆包客户端（§9.3）与升级经验/龙门币（§7.6）。单测一律读这里，不打网；重新抓取用
`scripts/fetch_m29_gamedata_fixtures.py`。

请求头 `MAA-API/0.1.0 (+https://github.com/ChthollyWn/MAA-API)`。拆包数据来自
`Kengxxiao/ArknightsGameData`，版权属于鹰角网络。

| 文件 | 来源 | 裁剪口径 |
|---|---|---|
| `level_main_01-07.json` | `GET https://fastly.jsdelivr.net/gh/Kengxxiao/ArknightsGameData@master/zh_CN/gamedata/levels/obt/main/level_main_01-07.json`（HTTP 200，87 KB） | **原样**，不裁剪（坐标探针与网格编码都要求完整） |
| `copilot_22311.json` | `GET https://prts.maa.plus/copilot/get/22311`（HTTP 200） | 保留 `id`、`uploader`、`views`、`rating_*`、`available` 与 **`content` 原文**（作业 JSON 是坐标探针的输入）；裁掉站点的时间戳副本等长字段 |
| `gamedata_const.json` | `GET …/zh_CN/gamedata/excel/gamedata_const.json`（HTTP 200，57 KB） | 只留 `characterExpMap`、`characterUpgradeCostMap`、`evolveGoldCost`、`maxLevel` |
| `item_table.json` | `GET …/zh_CN/gamedata/excel/item_table.json`（HTTP 200，1.8 MB） | `items` 只留四张作战记录（`2001`–`2004`，各留 7 个字段）＋完整的 `expItems` |

## 坐标探针结论（2026-10-10 实测）

作业 22311 是「1-7 稀音 单人 61s 速刷 信赖 无专」，`stage_name=main_01-07`，与关卡文件的
`obt/main/level_main_01-07` 是同一关。结论写进了 `maa_api/services/prts_parse/gamedata.py`
的模块说明，可执行断言在 `tests/services/test_gamedata_client.py`：

- MAA 作业的 `location` 就是 `[col, row]`，**行自上而下数、原点左上**，与关卡文件里的
  Tile-Pos 坐标同一套，不需要翻转或换轴；
- `mapData.map` 是 `height × width` 的地块下标矩阵（行序自上而下），
  `tiles[map[row][col]]` 取地块；一维下标按**自下而上**的行号递增（1-7 是 7×11、77 格，
  `map[0] = 66..76`、`map[6] = 0..10`）；
- 作业的 5 个部署点 **5/5 落在可部署格**：稀音（远程）在 `[8,2]` 的 `tile_wall`
  （`buildableType=RANGED`、`HIGHLAND`），移动摄影器（近战）在 `[8,5] [8,3] [8,1] [7,3]`
  的 `tile_road`（`MELEE`、`LOWLAND`）；
- 1-7 的 27 条路线里 26 条是真正的出怪路线，起点落在 `tile_start`、终点落在 `tile_end`；
  剩下那条是 `preview` 用的占位（两端都是 `tile_forbidden`）。

## 抓取时的网络实测

- `fastly.jsdelivr.net` 通（1-7 的关卡文件 87 KB，约 0.8 s）；
- `cdn.jsdelivr.net` **本机直连超时**（TLS 握手 42 s 未完成）——两个镜像内容一致，客户端按
  配置顺序试，这里只用 `fastly` 抓；
- `api.github.com` 的兜底路径（`Accept: application/vnd.github.raw+json`）没有在抓夹具时用到，
  单测用 `httpx.MockTransport` 断言它的请求形状。

## 关卡文件的结构要点

- `mapData.tiles` 是**扁平数组**，`mapData.map` 是行×列的下标矩阵（见上）；
- 单格的 `tileKey` / `buildableType`（`NONE` / `MELEE` / `RANGED`）/ `heightType`
  （`LOWLAND` / `HIGHLAND`）是网格编码的三个来源；`stage_layout.TILE_KEY_TERRAIN` 提供
  地形字符（与 `send_stage_map` 同一套编码）；
- `options.characterLimit` / `maxLifePoint` / `initialCost` / `maxCost` / `costIncreaseTime`
  对应工具返回的部署上限 / 目标耐久 / 初始 COST / COST 上限 / 回费间隔；
- `waves[*].fragments[*].actions[*]` 里只有 `actionType=SPAWN` 是出怪；时刻是
  `wave.preDelay + Σ片段 preDelay + action.preDelay`（秒），1-7 的第一个出怪在 6.0 s；
- `enemyDbRefs` 只有 `id`（`enemy_xxx`）与 `level`，**不带名字**，所以工具返回的
  `enemy_name` 是 `null`，要名字得用 `get_enemy(key)` 查 PRTS。
