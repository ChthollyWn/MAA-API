# M89 新活动自适应与示教录制（台账）

- 分支：`activity-adapt-teach`；基点：`dev` = `e0ce1c6`（M88 合并 + 台账提交）；合并：待补；tag：`v3-m89`
- spec：[m89-activity-adapt-teach](../specs/m89-activity-adapt-teach.md)；计划：[m89-activity-adapt-teach](../plans/m89-activity-adapt-teach.md)

## §0 结果

| # | 实测 |
|---|---|
| 1 | `## activity-adapt-teach` |
| 2 | `v3-m88` |
| 3 | 1 行（archive_screenshot） |
| 4 | `0041_nav_graph.py` |
| 5 | `108 107` |
| 6 | 端点 186；错误码 `171 170 22` |
| 7 | 业务表 57；配置 `143 132` |
| 8 | 3 行（register_custom_task/_validate_custom_task/_write_enabled_custom_tasks） |
| 9 | atomic.py 1 / device.py 2 |
| 10 | lease.py 5 / device_lease.py 1 |
| 11 | demonstration 代码 0、前端路由 0 |
| 12 | 360 passed（enums/models/migrate） |
| 13 | 场景 21 |

- 基线（M88 合并后 dev、M88 台账）：全量 pytest **3 failed / 5487 passed / 5 skipped / 2 xfailed**（3 条既有真资源失败）；`tests/evals` **72 passed**；capability **16/16**、`EVAL OK`；工具 108/107；端点 186；错误码 171/170/22；表 57；配置 143/132；场景 21；head 0041。

## 做了什么

（Task 8 补全）

## 验证证据

| 命令 | 结果 |
|---|---|
| （Task 8 补全） | |

## Rulings I made

（Task 8 补全）

## 偏离（已记 13 §8.x）

（Task 8 补全）

## 遗留

（Task 8 补全）
