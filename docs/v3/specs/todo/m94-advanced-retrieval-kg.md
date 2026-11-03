> 状态：TODO（先搁置，不要照此实施）
> 阶段：P5。领域亮点及之后的规格先搁置，等 P0–P3（M63–M85）落地后再写成可执行规格。
> 本文件只有范围，没有任务、签名和测试清单。

# M94 高级检索与知识图谱（TODO）

> 依赖：M93　分支：`advanced-retrieval-kg`　迁移：`0043_kg`

## 目标

在 M76 的检索层上加上下文前缀、查询改写、重排和一层游戏实体图谱。四项都有开关，打不开就退回 M76 的行为。

## 已定契约

- 行为与开关：[07 §7](../../07-知识RAG与记忆.md)。
- 表：`kg_entity`、`kg_relation`，`knowledge_chunk.context_prefix`，[03 §12](../../03-数据模型.md)。
- 端点：[04 §2.5](../../04-接口与事件协议.md)。`search_graph_entities`、`get_graph_entity`（`depth` ≤ 2）。
- 错误码：`KG_ENTITY_NOT_FOUND`。
- 配置：`rag.contextual_retrieval`、`rag.query_rewrite` 默认 false；`rag.rerank` 默认 `off`；`kg.enabled` 默认 true。
- 工具 `graph_query` 的落点见 07 §7。数值仍以现查工具为准。
- 改写、重排、上下文前缀的模型调用记入成本账本。用哪个模型角色由升格后的 spec 指定。

## 升格前要补

1. 四个开关的模型角色、超时、以及失败时如何退回。
2. 图谱从哪份拆包数据、哪份 PRTS 字段抽取，实体 id 的 `<type>:<原始 id>` 怎么对齐现有工具 id。
3. `graph_query` 与两个 HTTP 端点的字段是否同一套。
4. 评测场景放在哪，命中率不升时如何保持默认关闭。
