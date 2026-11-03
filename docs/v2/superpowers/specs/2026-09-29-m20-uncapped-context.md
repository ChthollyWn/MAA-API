# M20 · 去掉自设的会话与上下文上限

> 关联：[11-Agent模块设计](../../11-Agent模块设计.md) §6.3、§8，[13-决策记录](../../13-决策记录.md) §8.23、§8.24、§8.25。实施分支 `m20-uncapped-context`，从 `refactor/v2` 新建。

## 1. 目标与用户裁决

用户裁决（2026-09-29）：「会话限制上下文限制以模型本身上上下文限制为准，不要自己设置上限。」

当前有两处**自设**的上下文上限：

| 位置 | 上限 | 现状 |
|---|---|---|
| `maa_api/agent/runtime.py` | `DEFAULT_MAX_CONTEXT_CHARS = 1_000_000` | 每轮拼 system 消息时按字符数裁掉最早的完整轮次；裁不动就本地报 `LLM_CONTEXT_OVERFLOW` |
| `maa_api/agent/knowledge/__init__.py` | `MAX_ENABLED_ENTRIES = 50`、`MAX_FULLTEXT_CHARS = 3_000` | 启用条目数与术语表/档案正文合计的硬上限，超出 `409 KNOWLEDGE_LIMIT_EXCEEDED`；前端也有一份同样的常量与提示 |

本里程碑把这两处**全部去掉**：注入多少会话历史、多少知识，以模型自身的上下文窗口为准。

## 2. 范围

### 2.1 本里程碑

1. `build_model_context` 与 `AgentRuntime` 的 `max_context_chars` 默认改为 `None`＝**不裁剪**；保留显式传入数值时的裁剪能力（测试与特殊调用方仍可用）。
2. 把「上游因为上下文过长而拒绝」映射成 `LLM_CONTEXT_OVERFLOW`：本地不再预先拦截，但模型窗口真的放不下时要给出可读的 400，而不是笼统的 `LLM_REQUEST_FAILED`。
3. 知识库去掉条目数与全文段上限，一并去掉 `KNOWLEDGE_LIMIT_EXCEEDED`（没有任何路径能再抛出它，留着就是假文档）。
4. 前端设置页去掉两个上限的计数与提示、去掉对应的 409 文案。
5. 生成物（OpenAPI / TS 类型 / API 指南）与 docs 同步。

### 2.2 不做

- **不动字段长度上限**（`title` 32、`when_to_use` 120、`body` 20,000）：那是列宽与表单的约束，不是上下文预算。
- **不动其它守卫**：原地打转检测（`STUCK_REPEAT_LIMIT`）、确认时限、标题长度（`TITLE_MAX_CHARS`）、分页 `size` 上限都不属于「会话/上下文上限」。
- 不动 `max_iterations`（M13 起默认已经是「不设步数上限」）。
- 不引入「按模型查上下文窗口」的配置项：本里程碑的语义是「不预设数字」，不是「换一个数字」。

## 3. 设计

### 3.1 会话上下文

- `DEFAULT_MAX_CONTEXT_CHARS: int | None = None`，`build_model_context(max_context_chars=None)` 直接跳过裁剪循环，历史整段发给模型。
- 显式传正整数时行为与现在完全一致（含「最近一个完整工具轮次受保护」的语义与 `LLM_CONTEXT_OVERFLOW`）。
- `AgentRuntime(max_context_chars=None)`：默认不裁；传正整数仍生效。校验改成「要么 None，要么 ≥ 1」。

### 3.2 上游上下文过长

`OpenAICompatibleClient` 收到非 2xx 时，若上游错误码/消息表明是上下文过长（`context_length_exceeded`、`context_window_exceeded`、`string_above_max_length`，或消息里出现 context length / maximum context 之类的措辞），映射成 `LLM_CONTEXT_OVERFLOW`（400），消息说明「超出模型上下文长度」，`details` 保留 `upstream_status` / `upstream_code` / `upstream_message`。其余非 2xx 行为不变。

### 3.3 知识库

- 删除 `MAX_ENABLED_ENTRIES`、`MAX_FULLTEXT_CHARS`、`_check_limits()`；`put_entry` 只做字段校验（422）与 `kind` 覆盖保护（409 `KNOWLEDGE_BUILTIN_READONLY`）。
- 删除 `ErrorCode.KNOWLEDGE_LIMIT_EXCEEDED` 与 `ERROR_HTTP_STATUS` 里的绑定，`docs/05` 的错误码表同步删一行、条数 101 → 100。
- PUT/DELETE 的 `error_responses` 去掉该码；OpenAPI 快照、`api.d.ts`、`docs/14` 重新生成。
- 前端 `settings/knowledge/format.ts` 去掉两个常量、两条校验与 `KNOWLEDGE_LIMIT_EXCEEDED` 文案；`KnowledgeSection.tsx` 的页脚不再显示 `x / 50`、`y / 3000`，改为显示条目数与全文段字数（不带分母）。

## 4. 影响与兼容

- **变长的每轮请求**：知识库索引与术语表全文不再有上限，用户加多少就注入多少；超出模型窗口时由上游拒绝，前端/SSE 收到 400 `LLM_CONTEXT_OVERFLOW`。
- **超长会话的行为变化**：以前会被本地裁掉最早的轮次并继续，现在会一直带着完整历史，直到模型窗口放不下。这是用户要的口径（宁可让模型决定，也不要悄悄丢历史）。
- 无 migration、无新表、无新依赖。

## 5. 验收

- `.venv/bin/python -m pytest -q` 退出码 0；
- 默认不裁剪：`build_model_context` 在默认参数下不丢任何轮次；显式传小上限时仍按老语义裁剪并抛 `LLM_CONTEXT_OVERFLOW`；
- 上游上下文错误映射：用 MockTransport 造 `context_length_exceeded`，断言得到 `LLM_CONTEXT_OVERFLOW`；
- 知识库：写第 51 条不再 409、术语表写超 3,000 字不再 409；`KNOWLEDGE_LIMIT_EXCEEDED` 已从 `ErrorCode`、OpenAPI、前端文案里消失；
- `scripts/dump_openapi.py --check`、`scripts/generate_api_guide.py --check`、`cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` 全部退出码 0。

## 6. 文档同步

- docs/05：错误码表删掉知识库上限码、条数改 100；§6.15 的知识库说明去掉两条上限；
- docs/11 §6.3：从「上限与校验」改写为「字段校验与不再设上限」；
- docs/09 §6.5：设置页去掉两个计数器与 409 文案；
- docs/13 §8.25：本次用户裁决、去掉的两处上限、保留的守卫、以及「超长时改由上游拒绝」的取舍；
- docs/12：M20 条目；docs/README：决策速查表补一行。
