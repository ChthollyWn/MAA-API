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
