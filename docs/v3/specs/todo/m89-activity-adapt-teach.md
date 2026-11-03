> 状态：TODO（先搁置，不要照此实施）
> 阶段：P4。领域亮点及之后的规格先搁置，等 P0–P3（M63–M85）落地后再写成可执行规格。
> 本文件只有范围，没有任务、签名和测试清单。

# M89 新活动自适应与示教录制（TODO）

> 依赖：M88　分支：`activity-adapt-teach`　迁移：`0040_demonstration`

## 目标

新活动关卡还没有稳定作业时，让 Worker 起草并校验自定义任务；用户也可以录一段操作，再转成可复用的任务或作业草稿。

## 已定契约

- 表 `demonstration`：[03 §12](../../03-数据模型.md)。状态 `recording`、`recorded`、`converted`、`discarded`。
- 端点：[04 §2.5](../../04-接口与事件协议.md) 的 M89 行。`start_demonstration`、`list_demonstrations`、`stop_demonstration`、`convert_demonstration`。
- 错误码：`DEMONSTRATION_NOT_FOUND`、`DEMONSTRATION_STATE_INVALID`、`CUSTOM_TASK_INVALID`。
- 工具：[05 §6.1](../../05-运行时与代理定义.md)。`draft_custom_task`、`validate_custom_task`，组 `custom_task`。注册仍走 v2 的 `register_custom_task`。
- 范围：[01](../../01-总览与范围.md) 的「新活动自适应、示教录制」。

## 升格前要补

1. 自定义任务相对 v2 注册接口的输入、校验步骤、失败时的返回。
2. 录制时记哪些步骤、谁可以开始录制、录制中设备租约怎么占。
3. `convert` 的产物是自定义任务、作业草稿，还是两者都要，以及用户确认点放在哪。
4. 前端入口放在哪一页。
