# 未修复缺陷台账

发现**前序已完成卡**的产物存在真实缺陷、而本卡范围外修不了时，由 worker 追加到这里。

格式（每行一条，`- [ ]` 表示未修复）：

```
- [ ] <里程碑>-<归属卡号> | <一句话缺陷> | 复现：<命令或用例名> | 发现于 <你的卡号>
```

编排器在里程碑打 tag 前检查本文件：**本里程碑还有未勾选条目就拒绝打 tag**，避免缺陷随里程碑出厂。
修复完成后把 `- [ ]` 改成 `- [x]`，并补一句修法；确认可接受（不修）的也要改成 `- [x]` 并写明豁免理由。

只记录**已复现**的缺陷；推测性的怀疑写在回执里，不要写进来。

---

- [x] M1-08 | `CoreSupervisor.restart()` 在旧进程仍存活时不停止上一代存活监控线程，旧 liveness 线程把优雅停止误判为 `process_exit/exitcode=0` 并调度自动重启；即使包在 `acquire_maintenance()` 里，窗口退出后仍会补记伪崩溃，并可能 `terminate()` 掉刚启动的新进程 | 复现：`tests/core/test_crash_recovery.py::test_restart_from_ready_records_no_spurious_crash`（当前以 xfail 标记） | 发现于 M1-10 | 修复卡：M1-15 | 已修（M1-15）：`restart()`/`stop()`/`_spawn_process()` 在动旧进程之前先置位监控 stop_event，并给每次 spawn 自增代际、监控线程绑定启动代际（换代即退出，`_handle_crash` 作废旧代际迟到判定）；xfail 已移除，另加窗口外 restart 回归用例
