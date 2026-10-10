# PRTS 夹具

真实响应的**裁剪快照**，用于 M25 的解析层与访问层单测。单测一律读这里，不打网；重新抓取用
`scripts/fetch_prts_fixtures.py`（`--out` 默认本目录）。

| 文件 | 来源与抓取方式 | 抓取日期 |
|---|---|---|
| `ask_activities.json` | `GET https://prts.wiki/api.php?action=ask&query=[[分类:有活动信息的页面]][[!分类:集成战略]]\|?名称缩短\|?活动开始时间\|?活动结束时间\|sort=活动开始时间\|order=desc`，结果裁到前 4 条 | 2026-10-10 |
| `ask_stage_by_id.json` | `action=ask&query=[[关卡id::act3break_01]] OR [[关卡id::act3break_08]]\|?关卡id\|?关卡名称\|?敌人数量` | 2026-10-10 |
| `ask_stage_info.json` | `action=ask&query=[[7-18 爱国者之死]]\|?关卡id\|…\|?作战消耗` | 2026-10-10 |
| `ask_error_too_large.json` | 12 个 OR 条件触发的真实错误响应（SMW 查询大小上限） | 2026-10-10 |
| `page_vec03.json` | `action=query&prop=revisions&rvslots=main&redirects=1&titles=矢量突破/03`；`{{#widget:style}}` 的 CSS 换成占位 | 2026-10-10 |
| `page_zuori.json` | 同上，`titles=昨日海`（只写代号、不带 stageId 的活动页） | 2026-10-10 |
| `page_7-18.json` | 同上，`titles=7-18`（重定向到 `7-18 爱国者之死`） | 2026-10-10 |
| `page_vec08.json` | 同上，`titles=VEC-08 磷脂层破坏`（活动关） | 2026-10-10 |
| `resolve_codes.json` | `action=query&titles=YW-1\|YW-2\|YW-ST-1\|YW-EX-1&redirects=1&prop=info` | 2026-10-10 |
| `search_ajz.json` | `action=query&list=search&srsearch=爱国者&srnamespace=0&srlimit=5&srprop=snippet\|titlesnippet\|redirecttitle` | 2026-10-10 |
| `categories.json` | `action=query&prop=categories&clshow=!hidden&titles=7-18 爱国者之死\|矢量突破/03\|爱国者` | 2026-10-10 |
| `sections.json` | `action=parse&prop=sections&page=矢量突破/03` | 2026-10-10 |

`activity_vec03.wikitext` / `activity_zuori.wikitext` 是上面两份活动页正文的裸文本快照，给
`prts_parse.wikitext` 的解析测试用，来源与日期同上。

请求头的 User-Agent 是 `MAA-API/0.1 (+https://github.com/ChthollyWn/MAA-API)`（规格 §3.2、S1）。

## M26 追加（2026-10-10）

| 文件 | 页面 |
|---|---|
| `page_silverash.json` | `银灰`（6★、三技能、两个模组） |
| `page_surtr.json` | `史尔特尔`（6★、专精与模组材料） |
| `page_kroos.json` | `克洛丝`（3★，只有两个技能） |
| `page_originium_slug.json` | `源石虫`（敌人，级别 0/1） |
| `page_patriot.json` | `爱国者`（敌人，级别 0/1，带技能与天赋） |
| `page_item_30013.json` | `固源岩组`（材料，加工站配方） |
| `page_item_30033.json` | `聚酸酯组`（材料，加工站配方） |

抓取方式同 M25：`scripts/fetch_m26_fixtures.py`（`action=query&prop=revisions&redirects=1`）。
实测：干员页的 `稀有度` 从 0 开始（`5` = 6★）；材料消耗写作 `{{材料消耗|龙门币|3w}}`（`w` = 万）；
敌人页只有级别 0 与级别 1 两级，级别 1 只写与级别 0 不同的字段。

## M28 追加（2026-10-10）

| 文件 | 来源与抓取方式 | 抓取日期 |
|---|---|---|
| `ask_gacha_pools.json` | `GET https://prts.wiki/api.php?action=ask&query=[[寻访开启时间cn::+]]\|?寻访名cn\|?寻访开启时间cn\|?寻访关闭时间cn\|?出率提升干员\|sort=寻访开启时间cn\|order=desc\|limit=20`，结果裁到最近 6 条（卡池数据实测挂在 `文件:` 页上） | 2026-10-10 |
| `story_1-1_beg.wikitext` | `action=query&prop=revisions&rvprop=content\|ids\|timestamp&rvslots=main&redirects=1&titles=1-1 孤岛/BEG` 的裸 wikitext（revid 426748） | 2026-10-10 |

抓取方式同 M25/M26 的 httpx 脚本（`action=ask` 与 `action=query&prop=revisions`）。
