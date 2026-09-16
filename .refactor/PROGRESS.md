# 重构进度

> 本文件由 `@dsh-external/dsh-refactor-orchestrator` 自动生成，请勿手工编辑。
> 卡片协议见 [PROTOCOL.md](./PROTOCOL.md)。

- 分支：`refactor/v2`
- 进度：**38 / 46** 张卡完成
- 阻塞：0 张
- 进程内统计：cycle=2 workerRuns=5（启动于 2026-09-16T12:24:18.733Z）
- 更新时间：2026-09-16T12:55:41.121Z

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
| M1-08 | CoreSupervisor：状态机、心跳、崩溃检测、退避重启与维护窗口 | done | 1 | 9b26525 |  |
| M1-09 | CoreClient：事件消费线程、cmd_id→Future 映射、差异化超时、两级 Future 与事件分派 | done | 1 | d5072c8 |  |
| M1-10 | 崩溃恢复故障注入测试：FakeAsst 段错误退出 → 检测、崩溃现场、退避重启、上限转 FAILED、维护窗口互斥 | done | 1 | 921b2fe |  |
| M1-11 | 无内核端到端联调：FakeAsst 子进程 + CoreSupervisor + CoreClient 全链路（READY/命令/回调/落盘/优雅关闭） | done | 1 | c7b8746 |  |
| M1-12 | CoreRegistry：单实例实现与多实例扩展口（core_id 恒为 default） | done | 1 | d6a9541 |  |
| M1-13 | 命令行冒烟脚本 scripts/core_smoke.py：启动子进程→加载资源→连接→提交最短任务→收回调→杀进程观察重启 | done | 1 | b69a819 |  |
| M1-14 | 真机冒烟测试：真实内核 + 真实设备的加载/连接/截图/原子操作/最短任务（标记 hardware，不进常规 CI） | done | 1 | 220a72f |  |
| M1-15 | 修复 CoreSupervisor.restart() 的伪崩溃缺陷，并把 M1-10 的 xfail 转正 | done | 1 | 96cddac |  |
| M2-01 | 方案前置实测：SQLModel+Alembic 在本机 SQLite 上的 DDL 能力边界（AUTOINCREMENT/部分唯一索引/命名约定/auto_vacuum/batch downgrade/greenlet） | done | 2 | 4927c1e |  |
| M2-02 | 数据层底座：domain/enums.py 全量持久化枚举 + db/session.py 异步引擎与六个 PRAGMA（并补 greenlet 依赖） | done | 1 | db50760 |  |
| M2-03 | SQLModel 表定义：13 张业务表 + 命名约定 + §6 索引（db/models.py） | done | 1 | ed1c585 |  |
| M2-04 | Alembic 装配与 0001 初始迁移：env.py（render_as_batch）+ 13 张表的建表与索引 + 可用 downgrade | done | 1 | 2f74ee9 |  |
| M2-05 | 0002 数据迁移：resource/daily_task.json 逐日展开为 schedule 记录（文件不存在则跳过） | done | 1 | cbf2ad5 |  |
| M2-06 | db/migrate.py：ensure_schema 自动迁移到 head（线程池 + 迁移前备份 + 失败中止启动） | done | 1 | 4a7abcd |  |
| M2-07 | 仓储层（一）：BaseRepository + Page 分页 + PipelineRepository/TaskRepository（含 tests/db/conftest.py） | done | 1 | b6f5660 |  |
| M2-08 | 仓储层（二）：LogRepository（攒批插入/游标查询/两维清理）+ ScreenshotRepository | done | 1 | d68849c |  |
| M2-09 | 仓储层（三）：ScheduleRepository + SettingRepository（JSON 值保型与覆盖层语义） | done | 1 | 0052d9d |  |
| M2-10 | 仓储层（四）：AgentSession/AgentMessage/Audit + Confirmation（审计裁剪与终态不可变） | done | 1 | b2f5926 |  |
| M2-11 | 仓储层（五）：UpdateRepository + NotifyChannelRepository + ResourceAssetRepository（部分唯一索引与 CHECK 生效） | done | 1 | 52593ad |  |
| M2-12 | 保留策略与后台清理：services/retention_service.py（分级日志/流水线/截图/临时图 + 增量 vacuum） | done | 1 | ec1d368 |  |
| M2-13 | 命令行冒烟脚本 scripts/db_smoke.py：空库建库→迁移到 head→各仓储族读写往返→保留策略清理→退出码即结论 | done | 1 | 1dcbd6e |  |
| M2-14 | 修正 M2-01 实测文档里不完整的 auto_vacuum 放置说明（并补上探针漏掉的版本行断言） | done | 1 | 7789634 |  |
| M3-01 | 方案前置实测：pydantic v2 判别联合与 x-* schema 导出、FastAPI 400/422 分界、最小装配行为 | done | 1 | 456222f |  |
| M3-02 | domain/errors.py：全量 92 条错误码（docs/05 §4 十四张表）+ 固定 HTTP 状态绑定 + AppError | done | 1 | 76bbf28 |  |
| M3-03 | maa_api/settings.py：config.yaml 最小配置加载（pydantic 模型 + 进程内缓存），替代有 import 副作用的旧 config/config.py | running | 0 | — |  |
| M3-04 | api/errors.py：统一错误体、AppError/校验错误/HTTP 异常/兜底异常处理器与 error_responses() 辅助，并建 tests/api 公共夹具 | pending | 0 | — |  |
| M3-05 | domain/task.py：9 种任务类型的 pydantic v2 模型、判别联合、x-* 参数元信息与 normalize（参数说明从 task.py 文档字符串逐字段迁移） | pending | 0 | — |  |
| M3-06 | api/deps.py：四渠道 token 鉴权（优先级、cookie 写限制、豁免清单、失败限流）与会话依赖 | pending | 0 | — |  |
| M3-07 | api/routers/system.py：GET /api/system/health（免鉴权、带 auth_enabled）与 /api/system/auth/cookie 的换取与清除 | pending | 0 | — |  |
| M3-08 | api/routers/tasks.py：GET /api/tasks/types（9 类 schema 导出）、/types/{type_name} 与 POST /api/tasks/validate（渠道默认值注入） | pending | 0 | — |  |
| M3-09 | main.py 应用装配：lifespan（docs/02 §7 顺序）、CORS 白名单修正、OpenAPI 元数据/tag 分组/operation_id/redirect_slashes | pending | 0 | — |  |
| M3-10 | 端到端冒烟脚本 scripts/api_smoke.py：真实 app 装配 + lifespan 迁移 + 鉴权/校验/文档全链路（含 --serve 真实 uvicorn 模式） | pending | 0 | — |  |
