---
id: playbook-daily-weekly
kind: playbook
title: 日常与周常清单
when_to_use: 用户问每天/每周该做什么，或要拼一条日常周常流水线时
---
任务名一律用本服务的名字：`StartUp`（开始唤醒）、`Fight`（刷理智）、`Recruit`（自动公招）、`Infrast`（基建换班）、`Mall`（获取信用及购物）、`Award`（领取奖励）、`CloseDown`（关闭游戏）、`Roguelike`（自动肉鸽）、`Reclamation`（生息演算）、`Copilot`（作业）。没有叫「收菜」「领任务」的独立任务名，那些动作落在 `Infrast` 和 `Award` 里。

## 每天
推荐顺序（`StartUp` 之后）：
1. `Award`：领每日任务奖励。日常任务按「印章需求」逐层自动领取，做够就自动给；奖励里有合成玉、采购凭证、招聘许可、加急许可、常态事务代理卡等（明细见 currency-sources）。
2. `Infrast`：基建换班 + 收菜。收取制造站产物和贸易站订单，把心情耗尽的干员换下来——「注意力涣散」会让加成失效。队列与心情机制见 infrast-mechanics，排班看 playbook-infrast-shift。
3. `Recruit`：公开招募。它是稳定刷资质凭证/高级凭证的来源，也能完成日常与周常的招募类任务；参数怎么填见 playbook-recruit，标签怎么看见 recruit-tags。默认参数下不消耗加急许可。
4. `Mall`：信用交易所。**信用几点清算、上限多少，以 `get_prts_page(title="采购中心")` 与 screen-store 为准**，不要凭记忆报固定值。
5. `Fight`：把理智用掉，顺手完成「通关任意关卡若干次」；打哪张图见 playbook-sanity-planning 与 playbook-drop-lookup。
6. `CloseDown`：只在用户明确要关游戏时加。

## 每周
1. **剿灭作战刷满合成玉**（`Fight`，`stage` 填 `Annihilation`）。只有当期剿灭委托和长期剿灭委托给合成玉，剿灭战模拟不给；合成玉报酬有每周上限，超出部分改成返还理智。上限多少、怎么靠历练任务提满、几点刷新，**见 annihilation-weekly 与 `get_prts_page(title="关卡一览/常态事务")`，不要凭记忆报数字**。
2. `Award`：领周常任务奖励。要打多少次关卡、公招多少次、下多少笔订单、让多少名干员在宿舍恢复心情、在信用商店买几次，**看游戏内任务列表或 `get_prts_page(title="任务列表")`**；基建和公招日常做掉，周末就不用补作业。
3. **按开放日刷资源收集**：`get_prts_page(title="关卡一览/资源收集")` 列了每张图的开放星期；看当天开放表决定刷哪张，只报实际开放的关卡代号。
4. **按开放日刷芯片搜索**：常用的芯片本代号与掉落可以记，但「今天哪几张开」必须现查——先 `get_prts_page(title="关卡一览/资源收集")` 看开放表，排计划前先看今天星期几。
5. 事相结晶的补充节奏与使用方式见 `get_prts_page(title="事相结晶")`，要解锁老活动就按该页规则提醒用户。

## 拼流水线时的注意
- 典型日常：`StartUp` → `Infrast` → `Recruit` → `Mall` → `Award` → `Fight`（→ `CloseDown`）。用户没说要关游戏就不要加 `CloseDown`。
- `Fight` 的 `stage` 写不清就别填，让内核识别当前关卡，比填错强；剿灭一定写 `Annihilation`。
- `Recruit` 的 `expedite`、`Fight` 的 `stone`/`medicine`、`Mall` 的 `shopping` 是消耗类参数，会消耗加急许可、理智药或源石，第一次提交会弹授权确认，先跟用户说清。
- 游戏以凌晨 4:00 为日界（日常周常、信用商店、补充次数都按这个点刷新，剿灭是周一 4:00），跨过 4:00 再跑当天的日常更合适。
