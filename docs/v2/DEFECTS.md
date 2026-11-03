# 缺陷台账（卡片协议时期，已全部关闭）

> M0–M9 卡片协议（`.refactor/`，已于 M9 后退役，原貌见 git tag `pre-superpowers`）期间的缺陷记录，
> 全部条目均已修复或豁免，保留作历史。代码注释里引用的「DEFECTS 的某条目」指这里。
> 以下为当时的记录规则，原样保留。

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
- [x] M2-01 | `tests/fixtures/db_probe_findings.md` §2 与 §6.5 记录的 auto_vacuum「唯一生效放置」不完整：只 `conn.exec_driver_sql("PRAGMA auto_vacuum=INCREMENTAL")` 而不 `commit()`，会因 autobegin 让 Alembic 判为外部事务、迁移结束不提交 —— 表建好了但 `alembic_version` 行被回滚（探针只查表存在、未查版本行故未暴露）；照抄该放置的迁移链第二次 `upgrade head` 会从头重放并报 table already exists | 复现：按该放置写 `maa_api/db/migrations/env.py` 后 `command.upgrade(cfg,"head")`，`select * from alembic_version` 返回 `[]`（M2-04 env.py 初版即此现象；pragma 后补 `conn.commit()` 即返回 `[('0001',)]`，`PRAGMA auto_vacuum` 仍为 2） | 发现于 M2-04 | 已修（M2-14）：`tests/fixtures/db_probe_findings.md` §2/§6.5 补全「pragma 后必须紧跟 `conn.commit()`」与不 commit 的具体现象（实测值：pragma 仍为 2、`alembic_version` 空 0 行、第二次 `upgrade head` 报 `table probe_min already exists`），标注由 M2-04 发现、M2-14 修正；`scripts/probe_sqlmodel_alembic.py` 补上「`alembic_version` 行 == 预期版本」断言、第二次 `upgrade head` 重放检查，以及 `env_py_before_begin_transaction_no_commit` 对照场景，新增 `auto_vacuum_version_row_ok` / `auto_vacuum_no_commit_control_reproduced` 两条 required_checks 门禁；重跑探针 `PROBE OK`（exit 0）
- [x] M3-04 | `AppError` 无法携带响应头、`api/errors.py::_app_error_handler` 也不透传 headers：docs/05 §2/§4.1 要求 429（`RATE_LIMITED` / `QUEUE_FULL` / `LLM_RATE_LIMITED`）与 503 系列带 `Retry-After`，但用 AppError 抛这些码时只能把数值塞进 `details`，响应头永远拿不到，`error_responses()` 也没有 header 的说明入口 | 复现：`.venv/bin/python -c "from fastapi import FastAPI;from fastapi.testclient import TestClient;from maa_api.api.errors import register_exception_handlers;from maa_api.domain.errors import AppError,ErrorCode;app=FastAPI();register_exception_handlers(app);app.add_api_route('/q',lambda:(_ for _ in ()).throw(AppError(ErrorCode.QUEUE_FULL,'队列已满',{'retry_after':30})),methods=['GET']);r=TestClient(app,raise_server_exceptions=False).get('/q');print(r.status_code,r.headers.get('retry-after'))"` → 实测输出 `429 None`（同一处理器对 `StarletteHTTPException(429, headers={"Retry-After": ...})` 会保留该头） | 发现于 M3-06（本卡绕行：鉴权限流 429 改用 StarletteHTTPException + headers，响应体仍是 `RATE_LIMITED` 统一体，已在 `maa_api/api/deps.py` 注释说明） | 已修（M3-11）：`AppError.__init__` 新增可选 `headers`（`Mapping[str, str]`，构造时校验 str→str 与 latin-1，空映射归一为 `None`，进 pickle 往返），`_app_error_handler` 经 `_error_response` 原样透传（401 的 `WWW-Authenticate` 仍走 setdefault）；`message` 同时改为可省略（验收命令的构造形式），缺省时处理器回落 `_STATUS_MESSAGE` 的中文说明，错误体 JSON 形状不变；`error_responses()` 按新表 `ERROR_RESPONSE_HEADERS` 在 OpenAPI 的 `headers` 里声明 429 三类与"稍后可重试"的 503（`Retry-After` 一律整数秒），未登记头的状态码条目形状不变；M3-06 鉴权 429 改走 `AppError(RATE_LIMITED, headers={"Retry-After": ...})`，`StarletteHTTPException` 绕行删除，两套口径合一
- [x] M6-01 | adbutils 高阶 `AdbDevice.install()` 在 `INSTALL_FAILED_UPDATE_INCOMPATIBLE` / `INSTALL_FAILED_VERSION_DOWNGRADE` 时会自动卸载后重试，违反破坏性卸载必须人工确认的决策 | 复现：`.venv/bin/python -c 'import inspect, adbutils; from adbutils._device import AdbDevice; source=inspect.getsource(AdbDevice._install); assert "self.uninstall(package_name)" in source; print(adbutils.__version__)'`（环境版本 2.12.0，锁定兼容范围允许 2.x） | 发现于 M7-05 | 已修（M7-10）：`DeviceManager.install_apk()` 与新增 `install_apk_safe()` 统一使用唯一远端临时 APK、sync push 和低层 `pm install`；失败直接回报，不调用 adbutils 高阶 install/uninstall；`tests/services/test_device_service.py` 证明两个入口均不调用高阶卸载，并验证所有结果清理远端临时包

- [x] M2-05 | 旧 `weekday_task` 的 Python weekday（0=周一…6=周日）被 0002 原样写入 POSIX cron（0=周日），迁移后的七个按星期任务会整体错一天 | 复现：`.venv/bin/python -m pytest -q tests/db/test_daily_task_migration.py::test_upgrade_expands_every_weekday` 并对照 `maa_api/scheduler/daily_task_scheduler.py::daily_art_task` 的 `date.today().weekday()` 与 `maa_api/db/migrations/versions/0002_migrate_daily_task_json.py::iter_rows` 的 cron 直接插值 | 发现于 M9-03；已修（M9-08）：新增 0004，只映射 0002 默认 cron 且名字精确匹配的行（0..5→1..6、6→0）；downgrade 只还原仍匹配修正后默认值的记录，测试覆盖迁移链、映射和用户编辑保留
