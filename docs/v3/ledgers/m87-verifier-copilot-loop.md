# M87 验证闸门与作业研究闭环（台账）

- 分支：`verifier-copilot-loop`；基点：`dev` = `11ebbe2`；合并：待补；tag：`v3-m87`
- spec：[m87-verifier-copilot-loop](../specs/m87-verifier-copilot-loop.md)
- 计划：[m87-verifier-copilot-loop](../plans/m87-verifier-copilot-loop.md)

## §0 结果

- #1 `## verifier-copilot-loop`；#2 `v3-m86`；#3 4；#4 ok/1；#5 2/2；#6 1；#7 1/2（record_report 与重启恢复）；#8 1；#9 True ×7；#10 1/1/2（续租存在）；#11 2/2/2；#12 1/1/1；#13 1/1/8/4/2/1（补丁符号齐全）；#14 13、107 106、142 131；#15 186/83、171/170/22、表 57；#16 `0041_nav_graph.py`。命令输出见 `/tmp/m87/precheck.log`。
- **基线**：全量 pytest 以 M86 合并后 `dev` 实测为准 = **3 failed / 5369 passed / 5 skipped / 2 xfailed**（3 条均为 M86 前既有真资源失败）；`tests/evals` **70 passed**；`agent_eval --suite capability --mode replay` **8/8（100%）**、末行 `EVAL OK`。开工时并行跑的 `/tmp/m87/pytest-base.log` 因与 W1 实施并发（52 failed/7 errors）作废，不作为基线；`/tmp/m87/eval-base.log`、`/tmp/m87/cap-base.log` 与上述一致。

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
