# 作业站夹具

真实响应的**裁剪快照**（2026-10-10 抓取，主机 `prts.maa.plus`），用于 M29 的作业站
客户端、筛选与草稿单测。单测一律读这里，不打网（规格 §3.6 第 7 条、§4.9）。

抓取用的请求头是 `User-Agent: MAA-API/0.1.0 (+https://github.com/ChthollyWn/MAA-API)`，
与 `services/external/http.py` 的 `USER_AGENT` 一致。

| 文件 | 来源 | 裁剪口径 |
|---|---|---|
| `query_main_01-07.json` | `GET /copilot/query?level_keyword=main_01-07&order_by=hot&page=1&limit=50` | 只留 5 条（id `53355` 分组作业、`22311` 单人、`101217` 带 `potentiality`、`102455` 十二人低练、`87576` 双人高练）；`data.total` 保持站点给的 298 |
| `get_22311.json` | `GET /copilot/get/22311` | 原样（一份单人作业的完整内容，含 `actions`） |
| `get_53355.json` | `GET /copilot/get/53355` | 原样（一份**用 `groups` 声明干员**的作业） |
| `get_404.json` | `GET /copilot/get/99999999` | 原样：HTTP 200 + body `{"status_code":404,"message":"作业不存在"}` |

## 实测结论（写进 docs/13 §8.44）

- HTTP 200 但 body 里 `status_code=404` 表示作业不存在（`/copilot/get/<id>` 实测）。
  不存在的 id 不会给 HTTP 404，所以不能只看状态码。
- `query` 的行字段：`id`、`type`、`upload_time`、`uploader_id`、`uploader`、`views`、
  `hot_score`、`available`、`rating_level`、`not_enough_rating`、`rating_ratio`、
  `rating_type`、`comments_count`、`content`、`like`、`dislike`、`comment_status`、
  `status`；分页字段在 `data` 上是 `has_next` / `page` / `total` / `limit`。
- 每行的 `content` 是**作业 JSON 的字符串**（不是对象），列表里就已经**没有 `actions`**
  （站点自己省的）；`get` 的 `content` 才带 `actions`。
- 作业内容里的要求键是 `requirements`：`elite`（0–2）、`level`、`skill_level`（1–10，
  8/9/10 是专精一/二/三）、`module`（-1 不作要求、0 不要模组、1–5 是模组编号）、
  `potentiality`（站点自己的键，官方协议里叫 `potential` 且标注「暂不支持」）。
- 排序参数 `order_by` 只认 `hot` / `views` / `id`；未知值站点静默按 `id` 排，所以客户端
  先做白名单。
- 部署坐标（`actions[].location`）是 MAA 坐标：`[x, y]`，原点左上、y 向下。判定地块
  要用关卡文件的 `buildableType`（1 地面 / 2 高台 / 3 都可），**不能**按 `tileKey` 判
  「是不是墙」——1-7 的高台在文件里就叫 `tile_wall`，实测 `12F`、`克洛丝` 等远程干员
  正常部署在那些格子上（`buildableType=2`）。
