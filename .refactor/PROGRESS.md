# 重构进度

> 本文件由 `@dsh-external/dsh-refactor-orchestrator` 自动生成，请勿手工编辑。
> 卡片协议见 [PROTOCOL.md](./PROTOCOL.md)。

- 分支：`refactor/v2`
- 进度：**14 / 21** 张卡完成
- 阻塞：0 张
- 进程内统计：cycle=1 workerRuns=0（启动于 2026-09-16T09:40:48.186Z）
- 更新时间：2026-09-16T09:43:48.225Z

| 卡 | 标题 | 状态 | 尝试 | commit | 阻塞原因 |
|---|---|---|---|---|---|
| M0-00 | 流水线自检：验证 worker 能落盘并提交 | done | 1 | 9623717 |  |
| M0-01 | 升级依赖到 pydantic v2（FastAPI/uvicorn 同步升级 + SQLModel/Alembic/aiosqlite/httpx）并建好本仓 .venv | done | 0 | f47e3a5 |  |
| M0-02 | 按 02 §6 建立 maa_api 新目录骨架（core/domain/db/services/agent/api/util 空模块占位） | done | 1 | 95a6fb4 |  |
| M0-03 | 修复 asst.py 的 get_image 缓冲区缺陷与两处漏掉的 @staticmethod | done | 1 | c6397a6 |  |
| M0-04 | 修复模型层既有缺陷：TaskRequest 尾随逗号与重复 times、Award 默认值、InfrastTask failename、ReclamationTask 参数键 | done | 1 | 6a54f08 |  |
| M0-05 | 仓库卫生：config.yaml 入 .gitignore（保留本地不再跟踪）、补 config.template.yaml、取消忽略 tests/ | done | 1 | 98f45c7 |  |
| M0-06 | 定义 AsstProtocol 与 FakeAsst 替身，搭好 pytest 骨架 | done | 1 | e2ea70c |  |
| M1-01 | 方案前置实测：AsyncCallInfo 回调载荷结构 + 本地内核构建来源与导出符号能力 | done | 1 | 301c028 |  |
| M1-02 | 方案前置实测：子进程隔离最小验证（spawn 子进程→真实内核加载→跨进程回调→杀进程→观察重启） | done | 1 | d71100b |  |
| M1-03 | 内核基础契约：core/enums.py（Message/InstanceOptionKey/StaticOptionKey）+ domain/errors.py（内核层最小错误码） | done | 2 | 58c7dce |  |
| M1-04 | 重写 core/asst.py：补齐 8 个缺失 C API、迁移 AsstAsyncConnect、实验性 API 能力探测与优雅降级 | done | 1 | e837006 |  |
| M1-05 | 对齐 AsstProtocol 与 FakeAsst：新方法签名、可注入替身、崩溃注入剧本与结构一致性契约测试 | done | 1 | a0b51b5 |  |
| M1-06 | IPC 协议定义：命令与事件的类型、payload 结构、超时表、序列化 + 真实 Queue 契约测试 | done | 1 | 6afc6d8 |  |
| M1-07 | core/worker.py 子进程：启动序列、回调桥接、命令循环与 GET_IMAGE 落盘 | done | 1 | c5a0ff7 |  |
| M1-08 | CoreSupervisor：状态机、心跳、崩溃检测、退避重启与维护窗口 | running | 0 | — |  |
| M1-09 | CoreClient：事件消费线程、cmd_id→Future 映射、差异化超时、两级 Future 与事件分派 | pending | 0 | — |  |
| M1-10 | 崩溃恢复故障注入测试：FakeAsst 段错误退出 → 检测、崩溃现场、退避重启、上限转 FAILED、维护窗口互斥 | pending | 0 | — |  |
| M1-11 | 无内核端到端联调：FakeAsst 子进程 + CoreSupervisor + CoreClient 全链路（READY/命令/回调/落盘/优雅关闭） | pending | 0 | — |  |
| M1-12 | CoreRegistry：单实例实现与多实例扩展口（core_id 恒为 default） | pending | 0 | — |  |
| M1-13 | 命令行冒烟脚本 scripts/core_smoke.py：启动子进程→加载资源→连接→提交最短任务→收回调→杀进程观察重启 | pending | 0 | — |  |
| M1-14 | 真机冒烟测试：真实内核 + 真实设备的加载/连接/截图/原子操作/最短任务（标记 hardware，不进常规 CI） | pending | 0 | — |  |
