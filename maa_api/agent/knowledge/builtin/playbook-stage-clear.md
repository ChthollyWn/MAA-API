---
id: playbook-stage-clear
kind: playbook
title: 攻克指定关卡
when_to_use: 用户要过某一关、卡关、或问「怎么打这关」时
---
## 原则
让内核去打，agent 只负责找方案或写方案。MaaCore 没有滑动与独立 OCR 能力，原子操作打不了关卡；历史截图不保留图像，逐格点击意味着每一步都要重新截图、重新认，又慢又容易断。顺序固定：先搜现成作业，搜不到再自己写一份，最后才考虑原子操作。

## 1. 动手前先看现状
- get_system_status / get_queue：有流水线在跑就不要抢，先说清在跑什么，再用 ask_user 发卡问是等它结束还是稍后再来。设备没连接就说明情况，征得同意后 reconnect_device。
- resolve_stage 确认关卡代号能对上；用户给的名字有歧义（例如只说「1-7 突袭」）就问清难度，不要猜。

## 2. 先搜现成作业
- search_copilots(stage=<关卡>, limit=5, only_feasible=true)：按关卡搜作业站并用账号现有干员筛。
- 看每条的可行性档：
  - ok：直接用。
  - missing_one：缺一名、可借助战。先问用户同不同意借助战（借了就不用自己有这名干员），再继续。
  - infeasible：缺口名单列了缺谁、缺多少练度，如实告诉用户，别硬跑。
- 选中后用 save_copilot_draft(site_id=<作业站编号>, note=<出处与思路>) 存成本地资源。保存只做校验、不执行，也不占设备。
- 保存会跑静态检查：坐标在地图内且落在可部署格、近战远程与地块匹配、干员在账号里或是唯一允许的助战补位、部署数不超过关卡上限、技能序号不超过技能数。失败抛 COPILOT_JSON_INVALID 并逐条给 violations。照 violations 改，不要原样重试。

## 3. 搜不到就自己写一份
- 先用 get_stage_level_data 拿这关的地图、出怪与部署上限，别凭记忆画格子。
- 用 save_copilot_draft(content=<作业 JSON>, note=<思路>) 保存。作业 JSON 是 MAA 的自动战斗协议：部署序列 + 技能时机，字段与候选取值以 list_task_types 里 Copilot 的 schema 为准。
- 静态检查只管部署项。Click / Swipe 这两类动作不校验，风险留给运行时；写这类动作前先用 get_screenshot 确认当前画面与坐标系。
- 写不出可行方案（缺干员、缺练度、卡在某个节点）就直说卡在哪，并给出还差什么，不要为了交差塞一份跑不通的作业。

## 4. 执行
- 先 stop_pipeline 停掉在跑的流水线，再把游戏停到「开始行动」界面并截图确认：run_copilot_attempt 从那里开始打。
- run_copilot_attempt(copilot_id=<草稿 id>, support=<是否借助战>) 需要授权，没授权先申请 grant_copilot_attempts；被拒绝就说明情况并问用户下一步，不要重试同一张卡。
- 返回 not_started 表示战斗没开始（不在开始行动界面、编队失败或干员缺失），这一次不计次：把游戏停对位置、截图确认后再试。
- 结果里 outcome=unknown 表示内核没认出来，用 record_copilot_result 看截图补判，并如实标明是模型判定。

## 5. 失败之后
- 读动作日志摘要判断是「练度不够」还是「时机不对」，再决定改哪一步；不要放弃作业改去点屏幕，也不要原样重复同一份。
- 同一关的尝试次数由授权决定；COPILOT_GRANT_EXHAUSTED 就是用完了，停下并告诉用户。换了关卡要重新申请授权（COPILOT_GRANT_STAGE_MISMATCH）。
- 收尾按用户要求汇总：每份作业试了几次、结果是什么、判定方是谁、最后用的哪一份。

## 原子操作的位置
它只用于单步、幂等、能截图核对的界面动作：确认编队、关掉弹窗、回到主界面。做之前先停掉流水线（避免和内核抢控制权）并截图确认，做完再截图核对。
