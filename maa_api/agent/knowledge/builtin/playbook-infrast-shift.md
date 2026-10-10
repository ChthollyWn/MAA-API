---
id: playbook-infrast-shift
kind: playbook
title: 基建换班实操
when_to_use: 用户要你换基建班次、问该不该换班、或要保存与使用自定义基建排班方案时
---
基建机制细节见 infrast-mechanics，界面位置见 screen-infrast。

## 什么时候该换班
- **有干员心情为 0（注意力涣散）：必须处理**。这类干员留在岗位上技能与基础效率加成全部失效，等于白占位。
- 制造站/贸易站的干员信息里有「进入注意力涣散状态的理论所需时间」倒计时，倒计时已经不长（按用户作息估个时长）时就该安排换班。
- 办公室的联络次数达到上限后，进驻干员会暂停工作、不再消耗心情（恢复周期与上限查 `get_prts_page(title="罗德岛基建/办公室")`）。会客室不是这样：线索没满时仍能继续搜集，到上限才停止工作并滞留线索，上限见 `get_prts_page(title="罗德岛基建/会客室")`。
- 干员心情满了还占着宿舍位，可以换出来把位置让给心情低的干员。
- 用户如果只是问「该不该换」，先给结论和依据（谁心情见底、哪些设施效率掉了），不要直接提交任务。

## 用哪个模式
Infrast 的 `mode`：
- `0` 默认换班（单设施最优解），日常首选。内核会按自己的算法重排设施内的干员，**可能和用户手动排的班不一样**，提交前说清会动哪些设施。
- `10000` 自定义换班（读取用户配置），需要配 `filename` + `plan_index`。
- `20000` 一键轮换：会跳过控制中枢、发电站、宿舍以及办公室，其余设施不进行换班但保留基本操作（如使用无人机、会客室逻辑）。

## 关键参数
- `facility` 要换班的设施（有序，顺序就是换班顺序），默认七个全换：Mfg 制造站、Trade 贸易站、Power 发电站、Control 控制中枢、Reception 会客室、Office 办公室、Dorm 宿舍。该字段不支持运行中修改。
- `threshold` 工作心情阈值，取值 0~1，内核默认 0.3；越接近 0 越晚换班、越接近 1 越早换班。
- `drones` 无人机用途，默认不使用；**mode=10000 时该字段会被忽略**；用了会消耗基建无人机，别替用户决定。
- `replenish` 贸易站「源石碎片」是否自动补货；`dorm_notstationed_enabled` 宿舍「未进驻」选项；`dorm_trust_enabled` 把宿舍剩余位置填给信赖未满干员。都默认 False。
- `filename` / `plan_index` 只在 mode=10000 时生效且必填，且不支持运行中修改。

## 自定义排班方案
1. 先 `list_infrast_plans` 查询本机已有的自定义方案列表（**有这个工具，别再说没法列出方案**），要新建或覆盖时先看一遍避免撞名。
2. `set_infrast_plan` 保存或覆盖同名方案，它是高风险工具，**一定会弹人工确认**，先讲清「写入什么方案、会覆盖哪个同名方案」。
3. `content` 必须含非空 `plans` 数组；顶层只允许 author、description、id、title、planTimes、plans、scheduleType、buildingType；每个 plans 项只允许 name（必填、非空）、description、description_post、Fiammetta、drones、rooms、period。不符合校验会直接被拒。
4. 成功后记下返回的 `filename`，原样填进 Infrast 的 `filename`，用 `plan_index` 选具体方案；再用 `list_infrast_plans` 确认它已出现。
5. 用户从别处拿到的方案 JSON（MAA 社区方案等），先 set_infrast_plan 存一遍再引用；不要手改文件名或路径。

## 提交与核对
1. 提交前检查：有流水线在跑就先用 ask_user 发卡问用户是等它跑完还是稍后再来；设备未连接就说明情况并征得同意后 reconnect_device；不支持视觉输入就说看不到心情。
2. submit_pipeline 提交 `[{"name": "Infrast", "mode": 0, "facility": [...], ...}]`；Infrast 没有消耗类参数，不会因消耗品触发确认，但 set_infrast_plan 会。游戏没启动时前面加一条 `{"name": "StartUp", "start_game_enabled": true}`（见 task-preconditions）。
3. wait_pipeline 等终态；失败或被取消就说明原因并停下，不要改用 click 手点设施；用户坚持时才用原子操作，第一次点击会弹会话授权。
4. 核对：get_screenshot 看基建总览与设施详情，确认还进驻着谁、有没有干员仍是注意力涣散；把「动了哪些设施、谁换下来了、还剩哪些涣散干员」写进回复。
6. **不要承诺换完立刻回满心情**：宿舍的心情回复速度随等级与氛围值变化，当前数值用 `get_prts_page(title="罗德岛基建/宿舍")` 现查。
6. 结束时调用 back_to_home（安全操作，不需要确认）。
