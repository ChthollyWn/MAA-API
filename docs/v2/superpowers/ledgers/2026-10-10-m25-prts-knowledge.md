# M25 进度台账

计划：`docs/superpowers/plans/2026-10-10-m25-prts-knowledge.md`
规格：`docs/superpowers/specs/2026-10-09-m25-prts-knowledge.md`
分支：`m25-prts-knowledge`（自 `refactor/v2`），完成后 tag `v2-m25`

## 任务状态

| 任务 | 状态 | 备注 |
|---|---|---|
| T1 策略漏洞 D1 | 完成 | `policy.py` 按内核实际收到的参数判断 |
| T2 夹具 | 完成 | `tests/fixtures/prts/`，抓取脚本 `scripts/fetch_prts_fixtures.py` |
| T3 访问层 + 配置 | 完成 | `services/external/{sources,http,cache,status,envelope}.py`、`sources` 配置段、5 个新错误码 |
| T4 wikitext 解析 | 完成 | `prts_parse/wikitext.py`，59 例 |
| T5 PRTS 客户端与解析 | 完成 | `external/prts.py`、`prts_parse/{activity,stage}.py` |
| T6 当期活动领域层 | 完成 | `services/game/activities.py`；PRTS 优先 + 本机兜底 |
| T7 三个新工具 | 完成 | `services/game/{stages,pages}.py`、`tools/game.py` |
| T8 注入 + 提示词 + 条目 | 完成 | 多行注入、3 s 预算、三种标题；「外部资料」段 |
| T9 REST + 生成物 | 完成 | `api/routers/sources.py`；OpenAPI/API 指南/前端类型已重新生成 |
| T10 前端 | 完成 | 中文名、来源标签、设置页状态面板 |
| T11 文档 + 冒烟 + 验收 | 进行中 | docs 同步、`scripts/prts_smoke.py` 已 SMOKE OK |

## 实测结论（T2，2026-10-10）

见计划「实测确认」一节。要点：

- 活动页用 `{{关卡链接|代号|stageId?}}`，第二参数是 stageId（空格代下划线）或页面名。
- 只写代号时代号是重定向（`YW-1` → `YW-1 互助会`），可用 `redirects=1` 批量解析。
- SMW `OR` 条件上限在 8（12 报查询过大），分块取 6；`[[关卡id::act3break_*]]` 通配符不可用。
- `[[关卡id::<stageId>]]` 可直接反查关卡页与 `关卡名称`。
- `[[!分类:集成战略]]` 拦不住集成战略主题页（「沉沦者的黑流树海」的分类来自模板转写）；
  另按 `{{活动信息}}|类型=集成战略` 过滤。
- 同一批 SP 代号在往期活动里也有页面（PRTS 上 `VEC-SP01` 指向 `act1break_sp01`），
  所以解析结果要按本期前缀收敛。

## Rulings I made

1. 访问层新增 `envelope.py` 放来源信封（规格 §3.1 只列了 `http/sources/cache/status`），因为它同时被领域层与工具层用。
2. `PrtsClient.ask` 的多条件默认 **AND**（空格连接），需要 OR 时显式传 `combine="or"`：规格 §5.6 的第 1 步是 `[[分类:有活动信息的页面]][[!分类:集成战略]]`，两条件之间是 AND；我最初按 OR 拼，结果「不在集成战略分类」被解释成「任意一个条件成立」，集成战略主题页混进了当期活动。
3. 集成战略主题页在代码里再过滤一次（按 `{{活动信息}}|类型=集成战略`）：SMW 的 `[[!分类:集成战略]]` 实测拦不住它。
4. 活动关卡按「页面上的 stageId 提示 → PRTS 反查到的 stageId」定期数前缀，再按前缀收敛 PRTS 结果并与本机整组关卡合并：同名代号跨期撞车（`VEC-01`、`VEC-SP01` 在 `act1break` 与 `act3break` 里都有），不收敛会把往期的关卡算进本期（实测 32 → 45）。
5. `get_stage` 的候选按 `levelId` 去重（实测 `7-18` 同时有 `main_07-16` 与 `main_07-16#f#`），保留不带 `#` 的正式 stageId。
6. `get_stage` 多候选时只返回候选列表（规格 §5.7「不替用户选」）；单个 `levelId` 的多个 stageId 不算多候选。
7. 注入段与降级段共用一个渲染入口 `render_activity_section`；本机快照路径的标题改为规格 §5.9 的降级口径，因此 M24 的两条注入用例改成断言 `ACTIVITY_HEADER_FALLBACK`。
8. 活动页「同代号多次出现」改成合并提示（保留首次顺序与 stageId，补上缺失的页面名）；正文链接的代号判据比模板参数严一档（挡掉 `[[2026-09-29 活动公告]]` 这类日期）。
9. `KnowledgeService` 新增 `activity_service` 注入点：没装时走 M24 的本机快照口径（测试与干净检出），装了才查 PRTS。生产装配两个都传。
10. `render_activity_section` 的降级行复用 M24 的 `render_activity_line` 口径（活动名、开始日期、代号区间、消歧提醒），PRTS 行是「名称｜至 MM-DD HH:MM｜代号区间（N 关）」。
11. `PageService.search` 的来源链接用人类可读的搜索页而不是 `api.php?...` JSON 地址；`results_total` 是本次返回条数。

## 已知问题（与本里程碑无关，但会影响 `pytest` 退出码）

`tests/services/test_activity_index.py` 的两条**真资源包**用例失败：

- 现象：`ActivityIndex().snapshot()` 的 `confidence="none"`、`stage_id_prefix=None`、`stages=()`。
- 原因：本机资源层 `resource/maa-layers/repo/resource`（`version.json` 的 `last_updated` 是 2026-10-09 23:31，早于本次会话）当期活动是「昨日海」——名字里没有期数，M24 的规则**有意**给 `none`（M24 台账 Ruling：「活动名不带期数时给不出代号，这条如实回报而不是猜」）。
- 证据：`git diff --stat maa_api/services/activity_index.py tests/services/test_activity_index.py` 为空，本轮没有任何改动落在这一对文件上；M24 台账记录的真资源实测当时是「矢量突破 #3」（名字带 `#3`）。
- 处置：不改 M24 的推断规则、不弱化断言；作为环境性失败记录，验收时单独说明。
