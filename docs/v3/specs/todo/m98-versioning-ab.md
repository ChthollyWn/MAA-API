> 状态：TODO（先搁置，不要照此实施）
> 阶段：P5。领域亮点及之后的规格先搁置，等 P0–P3（M63–M85）落地后再写成可执行规格。
> 本文件只有范围，没有任务、签名和测试清单。

# M98 提示词与代理配置版本化、A/B（TODO）

> 依赖：M97　分支：`versioning-ab`　迁移：`0045_versions`

## 目标

代理定义的每次改动留下版本。新版本先过评测再启用。A/B 按工单分组，比较完成率、成本、耗时和人工干预次数。

## 已定契约

- 指标与激活闸门：[10 §7](../../10-可观测与评测.md)。
- 表：`agent_spec_version`、`ab_experiment`，[03 §12](../../03-数据模型.md)。
- 端点与函数名：[04 §2.5](../../04-接口与事件协议.md) 的 M98 行。
- 错误码：`SPEC_VERSION_NOT_FOUND`、`SPEC_VERSION_INVALID`、`EXPERIMENT_NOT_FOUND`、`EXPERIMENT_CONFLICT`。
- 配置：`versions.ab_enabled` 默认 false。
- span 属性 `maa.experiment.id`、`maa.experiment.variant` 已在 [10 §2](../../10-可观测与评测.md) 预留，底座在 M68。
- 包：`agent/versions/`。

## 升格前要补

1. 内置文件在启动时如何同步成 `source=builtin` 的行，与 M65 的加载顺序谁先谁后。
2. capability 评测结果从 `resource/evals/index.jsonl` 读取的字段，以及「不低于当前版本」的比较口径。
3. 实验分组的随机源、同一工单是否固定变体、停止实验时的两个按钮如何调用已有的激活端点。
4. 覆盖版本校验失败时自动退回内置版本的具体条件。
