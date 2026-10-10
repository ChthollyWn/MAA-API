# 企鹅物流夹具

真实响应的**裁剪快照**（2026-10-10 抓取），用于 M26 的掉率领域层与客户端单测。单测一律读这里，不打网；重新抓取用 `scripts/fetch_m26_fixtures.py --only penguin`。

主机 `penguin-stats.io`（`.cn` 内容一致），请求头 `MAA-API/0.1.0 (+https://github.com/ChthollyWn/MAA-API)`。数据许可 CC BY-NC 4.0（`developer.penguin-stats.io`）。

| 文件 | 来源 | 裁剪口径 |
|---|---|---|
| `matrix_subset.json` | `GET /PenguinStats/api/v2/result/matrix?server=CN` | 只留材料 `30012` / `30013` / `30033`；每个材料留样本最多的 40 行（共 60 行；其中最小样本 1798） |
| `matrix_closed_subset.json` | 同上 `&show_closed_zones=true` | 只留 `_perm` / `_rep` 变体与已关闭活动的行（11 行），含唯一一条 `times<100` 的小样本行 `act13d0_05`(92) |
| `stages_subset.json` | `GET /stages?server=CN` | 只留被上面两份 matrix 引用到的关卡（63 条），保留 `stageId`、`code`、`apCost`、`zoneId`、`stageType`、`existence` |
| `items_subset.json` | `GET /items` | 只留材料 `30012` / `30013` / `30033` 与几条其它类型（`2001` 等），保留 `itemId`、`name`、`itemType`、`rarity` |
| `period_subset.json` | `GET /period` | 前 3 条 |

实测结论（写进 docs/13 §8.41）：

- 默认 matrix 只含**当前开放**关卡（8,087 行），`show_closed_zones=true` 含已关闭（12,522 行）→ 正好是 `include_closed` 开关的两档。
- `_perm`（常驻化）与 `_rep`（复刻）在 `/stages` 里与基础 id 同时存在且 `code` 相同（`a001_01` / `a001_01_perm` / `a001_01_rep` 都是 `GT-1`）；对回游戏 id 时优先用基础 id。
- 当前数据里**没有 `times=0` 的行**（全量 12,522 行里 0 条），除零保护只能靠构造行做单测。
