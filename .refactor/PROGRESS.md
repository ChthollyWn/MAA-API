# 重构进度

> 本文件由 `@dsh-external/dsh-refactor-orchestrator` 自动生成，请勿手工编辑。
> 卡片协议见 [PROTOCOL.md](./PROTOCOL.md)。

- 分支：`refactor/v2`
- 进度：**3 / 7** 张卡完成
- 阻塞：0 张
- 进程内统计：cycle=1 workerRuns=1（启动于 2026-09-16T08:51:38.998Z）
- 更新时间：2026-09-16T08:52:51.539Z

| 卡 | 标题 | 状态 | 尝试 | commit | 阻塞原因 |
|---|---|---|---|---|---|
| M0-00 | 流水线自检：验证 worker 能落盘并提交 | done | 1 | 9623717 |  |
| M0-01 | 升级依赖到 pydantic v2（FastAPI/uvicorn 同步升级 + SQLModel/Alembic/aiosqlite/httpx）并建好本仓 .venv | done | 0 | f47e3a5 |  |
| M0-02 | 按 02 §6 建立 maa_api 新目录骨架（core/domain/db/services/agent/api/util 空模块占位） | done | 1 | 95a6fb4 |  |
| M0-03 | 修复 asst.py 的 get_image 缓冲区缺陷与两处漏掉的 @staticmethod | running | 0 | — |  |
| M0-04 | 修复模型层既有缺陷：TaskRequest 尾随逗号与重复 times、Award 默认值、InfrastTask failename、ReclamationTask 参数键 | running | 0 | — |  |
| M0-05 | 仓库卫生：config.yaml 入 .gitignore（保留本地不再跟踪）、补 config.template.yaml、取消忽略 tests/ | pending | 0 | — | 编排器重启前遗留的 running，已自动重置 |
| M0-06 | 定义 AsstProtocol 与 FakeAsst 替身，搭好 pytest 骨架 | pending | 0 | — |  |
