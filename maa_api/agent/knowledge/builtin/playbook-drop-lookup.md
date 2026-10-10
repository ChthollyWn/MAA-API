---
id: playbook-drop-lookup
kind: playbook
title: 查掉落与选图
when_to_use: 用户问某个材料去哪刷、某关卡掉什么，或要比较两张图的掉落收益时
---
## 回答「这关掉什么」
1. resolve_stage 确认关卡代号（code / stageId），不要直接拿用户的口语名去查。
2. get_drop_stats 传 `stage="<关卡代号>"`，看返回的 `runs`（本服务实际打过的次数）与 `items`。
3. `items` 里 `quantity` 是主掉落累计数量，`add_quantity` 是「额外物资」那一份的累计数量（日志里显示为 `数量(+额外)`）。
4. 只报「样本量 runs + 观测到的材料与数量 + 时间范围」，不要说成掉率。

## 回答「某个材料去哪刷」
1. 先确认材料名；用户用俗称时对照 glossary，或直接问清楚是哪个材料。
2. 想拿现成掉率：`get_drop_rates(item="<材料名>")` 给按 `rate` 排序的关卡列表（行里有 `ap_cost`、`sanity_per_item`，够你算性价比）。**它默认只统计当前开放的关卡**，查已关闭活动图要传 `include_closed=True`；命中「样本少」的关卡要说明样本不足。要跟「买材料」比成本就折算到每点理智（见 drop-efficiency）。
3. 只想看本服务记录：get_drop_stats 可以不传 `stage`，会聚合刷过的所有关卡（用 `since` / `until` 收窄，时间按 UTC）。
4. 要引用第三方统计就写明出处：`search_prts` / `get_prts_page(title="道具一览")` 指向的 PRTS 材料页与关卡页，掉落表由企鹅物流数据统计提供，页面会标注统计区间并提示可能过期。**两边口径不同，数字对不上正常，要分别标明出处。**

## 没有数据时怎么如实说
- `runs` 为 0、`items` 为空，只能说明本服务还没记录过这些关卡，绝不等于掉率为 0。此时先 `get_drop_rates` 兜底，并给用户一条路：看游戏内关卡详情，或先提交一次小样本 Fight 再回来看记录。
- 不要把「固定掉落」「小概率」「大概率」这些官方档位当百分比，也不要用单次作战结果推断掉率；查关卡用 resolve_stage，查掉落用 get_drop_stats 或 get_drop_rates，查 PRTS 用 search_prts。
