# M52 历史思考内容只回灌最后一条（规格）

> 分支 `m52-reasoning-context`，基点 `refactor/v2` = `9420fd9`（2026-10-15）。
> 本文是 M52 的唯一规格；实施后的偏离记在 [13 §8.75](../../13-决策记录.md)。

## 1. 背景

用户 2026-10-15 问：「为什么 DSH 会话很难用到上限，但是这个 agent 很快就上下文上限了？」现场是 MAA-API 内置 Agent 的会话「运行yw-1不用代理」（`bcbc79e1…`，08:50→09:45）。把它的 143 条消息按字段量了一遍：

| 项 | 实测 |
|---|---|
| assistant `content` 合计 | **7,003 字**（真正写给用户看的） |
| assistant `reasoning_content` 合计 | **3,441,718 字** |
| 单条最大思考 | **297,730 字** |
| 最后一次请求的 `prompt_tokens` | **1,030,502** |
| 单步延迟 | 5 秒 → **114 秒** |
| 工具结果合计 | 280,975 字（其中 `get_logs` 17 次占 203,661 字） |

原因在装配层：`_message_dict()` 把**每一条**历史 assistant 的 `reasoning_content` 一并转出（注释写着「思考模式下缺它的 assistant 消息会被上游 400 拒绝」），`build_model_context()` 又逐字透传。于是每一轮请求都把前面所有轮次的思考重发一遍，按轮次重复计费。53 条消息、每轮思考几十万字，prompt 涨到百万 token 是必然结果。

另外 DSH 侧对照：DSH 的会话日志里每步只有 `stream`（增量）与最终 `message`，回灌给模型的是 prompt 与工具结果，**思考内容不回灌**；MAA-API 因为上游协议约束必须保留它，但只该保留**刚生成的那一条**。

## 2. 目标与非目标

**目标**

1. 回灌给上游的历史里，**只有最后一条 assistant 保留 `reasoning_content`**，其余全部去掉。
2. 保留的那条也设预算：超过 `HISTORICAL_REASONING_CHARS`（默认 20 000 字）时**截尾**（保留最后一段）。
3. 回归用例复现该会话形态（多条各带 10 万字思考），断言回灌量从 40 万字降到 1 个预算、轮次一条不丢。

**非目标**

- 不动落库：审计要留着完整思考，`agent_message.reasoning_content` 原样保存。
- 不动 `max_context_chars` 的默认值（`None`＝不裁剪）——那是另一个决策（docs/13 §8.73 讨论过），本轮不混进来。
- 不压缩工具结果（`get_logs` 17 次 20 万字是第二个可优化点，另开）。

## 3. 决策

- **D1（只留最后一条）**：上游约束的是「刚生成的 assistant 消息不能缺思考内容」，历史条目没有这个约束；去掉历史思考既不改协议行为，又消掉重复计费。
- **D2（截尾而不是截头）**：思考的结论在开头，但「我下一步打算怎么做」在末尾；上限约束下截尾保留的是最有用的部分。
- **D3（预算做成模块常量）**：`HISTORICAL_REASONING_CHARS = 20_000`，先按常量走；要可配置时再进 `agent.*`（避免为一处阈值扩设置面）。

## 4. 设计

`maa_api/agent/runtime.py`：

- 新增常量 `HISTORICAL_REASONING_CHARS = 20_000`。
- 新增 `_trim_historical_reasoning(messages, *, budget=...)`：找到最后一条带思考的 assistant，其余 `pop("reasoning_content")`；保留的那条超预算则取尾 `budget` 个字符。
- `build_model_context()` 在组完 `model_history` 之后、拼 system 段之前调用它。

## 5. 风险与对策

| 风险 | 对策 |
|---|---|
| 去掉历史思考后上游 400 | 约束只针对刚生成那条；`tests/agent/test_runtime.py` 既有用例（含多轮带思考的历史）全部跑通 |
| 截尾丢掉关键结论 | 预算 2 万字远大于单步决策所需；真要复现时库里还有全文与审计 |
| 归因错误（可能是工具结果吃掉的） | 实测已排除：工具结果合计 28 万字，思考 344 万字，差一个数量级 |

## 6. 验收

1. `.venv/bin/python -m pytest -o addopts="" -q tests/agent/test_runtime.py` 全绿（含新用例 `test_historical_reasoning_is_kept_only_for_the_newest_assistant_message`）。
2. 新用例的反向验证：不调用 `_trim_historical_reasoning` 时回灌量应为 40 万字。
3. 后端全量 `pytest` 只剩既有的两条真资源失败。
4. `dump_openapi.py --check` 不变。
