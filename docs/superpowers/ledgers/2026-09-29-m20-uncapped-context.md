# SDD ledger — 用户裁决：去掉自设的会话与上下文上限（M20）

## Setup

- Base：`e0f9cf2`（M19 合并回 `refactor/v2` 的合并提交），分支 `m20-uncapped-context`（从 `refactor/v2` 新建）。
- 规格：`docs/superpowers/specs/2026-09-29-m20-uncapped-context.md`。
- 用户裁决（2026-09-29）：「会话限制上下文限制以模型本身上上下文限制为准，不要自己设置上限。」

## 事实基础（动手前实际核对）

`grep` 全仓后确认，真正属于「自设的会话/上下文上限」的只有两处：

1. `maa_api/agent/runtime.py` 的 `DEFAULT_MAX_CONTEXT_CHARS = 1_000_000`：每轮拼 system 消息时按字符数丢掉最早的完整轮次，裁不动就在本地抛 `LLM_CONTEXT_OVERFLOW`。
2. `maa_api/agent/knowledge/__init__.py` 的 `MAX_ENABLED_ENTRIES = 50` 与 `MAX_FULLTEXT_CHARS = 3_000`：启用条目数与术语表/档案正文合计的硬上限，超出 `409 KNOWLEDGE_LIMIT_EXCEEDED`；前端 `settings/knowledge/format.ts` 有一份同样的常量与提示。

**不属于**本次范围的（保留不动）：字段长度上限（`title` 32 / `when_to_use` 120 / `body` 20,000，那是列宽与表单约束）、原地打转检测 `STUCK_REPEAT_LIMIT`、标题长度 `TITLE_MAX_CHARS`、分页 `size` 上限。`max_iterations` 自 M13 起默认已经是「不设步数上限」。

## Rulings I made

- Ruling: 保留「显式传 `max_context_chars` 就按它裁剪」的能力，只把**默认**改成 `None`。理由：裁剪逻辑本身（保护最近一个完整工具轮次）是有价值的工具，测试与将来的特殊调用方还要用；去掉默认值就满足「不要自己设置上限」。代价：多一个参数分支。
- Ruling: **删掉 `KNOWLEDGE_LIMIT_EXCEEDED` 这个错误码本身**，而不是留着一个再也抛不出来的码。理由：本仓已有明文标准——M18 §8.23 记过「PUT 对未知 id 是新建、永不 404，声明它属于假文档」。留一个不可能的码会在 docs/05、OpenAPI 与前端文案里形成三处假文档。代价：docs/05 错误码表少一行、条数 101 → 100，OpenAPI 与 TS 类型重新生成。
- Ruling: 本地不再预拦截上下文长度，但**必须把上游的「上下文过长」映射成 `LLM_CONTEXT_OVERFLOW`**，否则用户拿到的是笼统的 `LLM_REQUEST_FAILED`，看不出是窗口装不下。这是本次改动唯一的「补偿性」改动。
- Ruling: `MAX_BODY_CHARS` 等字段上限**保留**。用户说的是「会话/上下文上限」，不是「任何上限」；把 title 的 32 字上限也去掉会牵动列宽与列表布局。

## Tasks

| Task | 产物 | 结果 |
|---|---|---|
| 1 · 后端去上限 | `runtime.py`、`llm.py`、`knowledge/`、`errors.py`、`routers/knowledge.py` + 生成物 | 进行中 |
| 2 · 前端去上限 | `web/src/features/settings/knowledge/*` | 进行中 |
| 3 · 文档同步 | docs/05、09、11、12、13、README | 待办 |
| 4 · 验收（主 agent） | 见下 | 待办 |

## Task progress

- Task 1（后端）实现者完成（`backend-report.md`）。主 agent 复核：读过 `runtime.py`（`DEFAULT_MAX_CONTEXT_CHARS: int | None = None`、`None` 时直接返回全部历史、显式正整数路径未动）、`llm.py`（`_CONTEXT_OVERFLOW_CODES` + 措辞兜底、判定顺序 429 → 上下文 → 其余）、`knowledge/__init__.py`（删两个常量与 `_check_limits`）、`errors.py`（删成员与绑定，条数 100/99）；亲自复跑 `tests/agent tests/api tests/domain` **961 passed 退出码 0**、全量 **1895 passed / 4 skipped 退出码 0**、`dump_openapi.py --check` 与 `generate_api_guide.py --check` 退出码 0。
- Task 2（前端）实现者完成（`frontend-report.md`）。主 agent 复核：读过 `format.ts` / `KnowledgeSection.tsx` / `KnowledgeEditor.tsx` 的 diff，确认三个字段级上限与 `FULLTEXT_KINDS` 保留、只有上限专属的校验与文案被删；亲自复跑 `cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` **退出码 0**（53 文件 / 540 项，末行 `[sw_harness] SERVICE WORKER OK`）。
- Task 3（文档）主 agent 亲自完成：docs/11 §6.3 改写为「校验（不设条目数与字数的上限）」+ §8.2 与「成本失控」两处口径、docs/09 §6.5、docs/12 新增 M20 条目、docs/13 §8.25（本裁决十条）、docs/README 两行（知识库行 + 新增「上下文与上限」行）。docs/05 与 docs/14 由后端实现者同步。
- Task 4（验收）主 agent 亲自跑：
  - `.venv/bin/python -m pytest -o addopts= -q` → **1895 passed, 4 skipped**，退出码 0；
  - `.venv/bin/python scripts/dump_openapi.py --check`、`.venv/bin/python scripts/generate_api_guide.py --check`、`cd web && pnpm gen:api:check` → 全部退出码 0（生成物只少一行 `KNOWLEDGE_LIMIT_EXCEEDED`）；
  - 前端 `pnpm typecheck && pnpm test --run && pnpm build` → 退出码 0；
  - 行为实测：`DEFAULT_MAX_CONTEXT_CHARS is None`；构造 40 轮 ×10 万字符（400 万字符）历史，默认参数下 81 条消息**全部保留**，显式传 `max_context_chars=200_000` 时裁到 3 条（老语义仍在）；`hasattr(ErrorCode, "KNOWLEDGE_LIMIT_EXCEEDED") is False`；
  - 全仓 `grep` 确认 `KNOWLEDGE_LIMIT_EXCEEDED` 只剩文档里描述「已删除」的段落，代码与生成物里已无残留。

## 最终交付

- 分支 `m20-uncapped-context`，从 `refactor/v2`（`e0f9cf2`）新建。提交：规格、后端、前端、文档、台账（见 `git log`）。
- 交付内容：会话上下文默认**不按字符数裁剪**（`DEFAULT_MAX_CONTEXT_CHARS = None`，显式上限能力保留）；上游「上下文过长」映射成 `400 LLM_CONTEXT_OVERFLOW`；知识库删掉 50 条 / 3,000 字两条上限与 `KNOWLEDGE_LIMIT_EXCEEDED` 错误码（含 docs/05、OpenAPI、TS 类型、docs/14、前端文案与计数器）；docs 六篇同步。
- 保留的约束（有意）：字段长度 `title` 32 / `when_to_use` 120 / `body` 20,000；原地打转检测；标题长度；分页上限。
- 遗留：超长判定完全外包上游（表外措辞仍是通用 502）；超大知识库可让每轮都 400；真上游未实测。
