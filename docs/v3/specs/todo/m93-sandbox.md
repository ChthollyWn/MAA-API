> 状态：TODO（先搁置，不要照此实施）
> 阶段：P5。领域亮点及之后的规格先搁置，等 P0–P3（M63–M85）落地后再写成可执行规格。
> 本文件只有范围，没有任务、签名和测试清单。

# M93 代码沙箱（TODO）

> 依赖：M92　分支：`sandbox`　迁移：无

## 目标

给分析师等代理一个只能跑 Python 的沙箱。Hook 的 `script` 动作走同一套限制。

## 已定契约

- 安全模型全文：[09 §9](../../09-安全与治理.md)。不开 shell，不开 `adb shell`（V3-D27）。
- 后端：`sandbox.backend` 为 `auto`、`seatbelt`、`docker`、`off`。`auto` 在 macOS 上用 Seatbelt，其他系统为 `off`。
- 配置：[04 §6](../../04-接口与事件协议.md) 的 M93 行。`sandbox.timeout_seconds` 默认 60，`sandbox.memory_mb` 默认 512，`sandbox.docker_image` 为 config-only。
- 端点：`GET /api/agent/sandbox`，函数名 `get_sandbox_status`。
- 错误码：`SANDBOX_UNAVAILABLE`、`SANDBOX_TIMEOUT`、`SANDBOX_EXECUTION_FAILED`。
- 工具：`run_python`，组 `sandbox`，[05 §3.2](../../05-运行时与代理定义.md) 的 `analyst` 行、[05 §6.1](../../05-运行时与代理定义.md)。输出按不可信处理。
- 标准输出与错误合计截到 64 KiB。Docker 参数见 09 §9：只读根、`tmpfs /tmp`、丢掉全部 capability、非 root、1 CPU。

## 升格前要补

1. Seatbelt profile 的具体策略，以及 `docker` 不在 PATH 时如何配置可执行文件。
2. `run_python` 的参数、允许的标准库、禁止的网络与文件路径。
3. Hook `script` 的输入输出形状，超时如何映射到 `HOOK_BLOCKED` 或沙箱错误码。
4. 不可用时拒绝执行，不降级成进程内 `exec`。
