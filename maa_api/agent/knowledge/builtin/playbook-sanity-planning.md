---
id: playbook-sanity-planning
kind: playbook
title: 理智规划与刷图
when_to_use: 用户要刷材料、推主线、打活动或剿灭，需要安排理智怎么花，或要提交 Fight 任务时
---
## 1. 先看现状，不要直接开刷
- get_system_status / get_queue：有流水线在跑就不要打断，先说清在跑什么、用 ask_user 发卡问是等它结束还是稍后再来；设备未连接就说明情况，征得同意后 reconnect_device。
- 读理智：back_to_home → get_screenshot，按 screen-home 读「终端 TERMINAL」里的「当前理智/上限」。把这两个数先写在回复里，再决定下一步。

## 2. 按用户目标选图
- 刷材料：走 playbook-drop-lookup 的流程选图；不要在没查数据的情况下点名「最优图」。
- 推主线：问清推到哪一关、用 Normal 还是 Hard，不要替用户决定打哪关。
- 打活动：先用 `get_activity_stages` 看当前开放的关卡列表，只推荐表里真有的；拿不准就直说拿不准。
- 剿灭：Fight 的 `stage` 必须填 `Annihilation`。选哪张图、理智怎么算、每周合成玉上限怎么涨，见 annihilation-weekly；本服务只负责把 `stage` 传对。
- 顺手做任务：部分活动关卡不消耗理智且可代理，适合刷通关次数和歼灭数；哪张图零消耗要现查 `resolve_stage` 的返回，不要凭记忆点名。

## 3. 提交前校验关卡
- 用 resolve_stage 查一次关卡代号，确认 code / stageId 对得上；`source=resource` 表示走本地关卡索引，`source=core` 表示内核解析。
- 用户给的关卡名有歧义（例如只说「1-7 突袭」）时问清楚要哪个难度，不要猜。
- 想确认「理智够不够打 N 次」先自己算：⌊当前理智 ÷ `get_stage` 返回的 `sanity`⌋，再和用户要的次数比。

## 4. submit_pipeline 提交 Fight
- 形如 `submit_pipeline(tasks=[{"name": "Fight", "stage": "<关卡>", "times": N, "series": 0}])`；一次流水线 1~32 个任务；游戏没启动时第一条放 `{"name": "StartUp", "start_game_enabled": true}`（见 task-preconditions）。
- 关键参数怎么填：
  - `stage`：关卡名；剿灭必须 `Annihilation`；当期 SideStory 的后三关必须写完整关卡编号；结尾可加 `Normal` / `Hard` 切换标准与磨难难度。
  - `times`：战斗次数。不传等于交给内核的默认值（无穷大），会一直刷到理智耗尽——要么按用户说的次数传，要么明确告诉用户这是无限刷。
  - `series`：连战次数。`0` = 让内核自动选当前可用的最大次数（推荐），`-1` = 禁用切换，`1`~`6` = 指定。本服务目前只到 6，游戏内上限已更高。
  - `medicine` / `stone`：最多允许吃几个理智药 / 碎几颗源石；默认都是 `0`，即不吃不碎。
  - `expiring_medicine` 自内核 v6.8.0 起已弃用（内核改用 `medicine_expire_days`），只为兼容旧提交保留，不要填。
  - `DrGrandet`：节省理智碎石模式，只在可能碎石时生效——它会等到当前这 1 点理智恢复好再碎石。
- `medicine`、`stone` 是消耗类参数，提交会触发人工确认。提交前必须先在回复里写明「用什么、最多几个、最多花几颗源石」，得到同意再提交；用户没同意就一律传 0。
- 不要在用户没指定的情况下加次数、加药或碎石。

## 5. 等结果与打完核对
- 提交后用 wait_pipeline 等终态，超时就再等一次；中途要停用 stop_pipeline，不要反复 get_pipeline 或改用原子操作硬闯。
- 核对三件事：
  1. get_logs（`source=["task"]`）里能看到每次「已战斗 x/y 次，消耗理智 N」，以及每条「{星级}⭐通关{关卡代号} 掉落统计」。
  2. get_drop_stats 看这关累计样本（runs）与各项材料数量。
  3. 需要看剩余理智就 back_to_home 后再 get_screenshot 读一次。
- 回复里写清「打了几次、消耗多少理智、拿到什么、还剩多少理智」；没读到的数字说没读到，不要推算后当成观测值。
- 理智不够导致任务停在补充理智界面（`medicine`/`stone` 为 0 时）要如实说明，并用 ask_user 发卡让用户选吃药、碎石还是等自然恢复。
