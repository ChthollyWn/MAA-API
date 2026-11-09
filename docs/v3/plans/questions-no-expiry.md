# 提问不再过期与工单卡选项优先 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除提问的时间失效（含配置、扫描、倒计时与 wire 字段），跨重启保留 pending 提问并在作答时把答案投递给来源会话；把指挥台工单卡改成选项优先。

**Architecture:** 后端改 `QuestionService`（无超时、作答投递、工单结束/总控重置收尾）、删配置与迁移 `expires_at` 为历史列；前端提问卡去倒计时、指挥台排序改 `created_at`、工单卡优先渲染关联失效提问的选项或 `human_action.type` 固定按钮。

**Tech Stack:** FastAPI + SQLModel/Alembic + pytest；React 19 + TypeScript + TanStack Query + Vitest。

**Spec:** `docs/v3/specs/questions-no-expiry.md`

## Global Constraints

- 分支 `questions-no-expiry`（已从 `dev` 创建）；完成后 `git merge --no-ff` 回 `dev` 并删除分支；**不 push**。
- 确认链路与 `agent.confirmation_timeout_seconds` 不动；`ask_user` 工具参数不动。
- 不复活历史 expired 提问（只在工单卡回填选项）。
- 提问 wire 去掉 `expires_at`；`QUESTION_EXPIRED` 只保留给历史行。
- 文档用简体中文；提交信息风格 `feat(agent): …（维护性补丁，见 docs/v3/13 §8.177）`。
- 后端命令：`.venv/bin/python -m pytest -o addopts= -q <文件>`；前端在 `web/` 下 `pnpm typecheck` / `pnpm test --run <文件>`。
- 进度写 `.superpowers/sdd/2026-11-09-questions-no-expiry/progress.md`，完成后复制为台账。

## Review Focus

- **跨重启作答不能丢**：重启后作答要把答案投递给来源会话（工单 → Worker / 总控 → 总控），且等待轮还活着时不能重复投递。T1 用例。
- **工单结束/总控重置不能留下永远答不了的卡**：收尾要撤回 pending 提问。T1 用例。
- **提问卡没有倒计时、不会本地过期**：没有 `expires_at` 也能一直答。T2 用例。
- **工单卡选项优先级**：关联历史提问选项 > 类型固定按钮 > 自由文本；答题一律走工单消息。T3 用例。
- **历史 expired 行仍安全**：作答返回 409 `QUESTION_EXPIRED`，不产生新的 expired。T1 用例。

---

### Task 1: 后端提问不再过期 + 跨重启投递

**Files:**
- Modify: `maa_api/agent/questions.py`、`maa_api/db/repositories/question.py`、`maa_api/db/models.py`、`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`maa_api/main.py`、`maa_api/agent/harness/builtin_middleware.py`、`maa_api/agent/orchestrator/service.py`
- New: `maa_api/db/migrations/versions/0040_question_expiry_historical.py`
- Test: `tests/agent/test_question_service.py`、`tests/api/test_questions_router.py`、`tests/agent/test_runtime.py`、`tests/evals/*`、`tests/db/test_migrate.py`

**Interfaces:**
- Produces: `QuestionService(session_factory, *, broadcast=None, clock=utcnow)`（去掉 `timeout_seconds`）；`set_answer_delivery(delivery: Callable[[dict], Awaitable[None]])`；`cancel_pending_for_ticket(ticket_id) -> list[str]`；wire 无 `expires_at`；`AgentQuestionRepository.cancel_pending_for_ticket(ticket_id, now)`。
- Consumes: `WorkerService.post_user_message(ticket_id, content, mentions, *, notify_content=...)`、`OrchestratorService.post_user_message(content, mentions)`、`TicketService.add_end_listener`。

- [ ] **Step 1: 改写/新增后端测试（先红）**

`tests/agent/test_question_service.py`：删超时/过期用例，新增
- `wait` 在无回答时一直 pending（用 asyncio.wait_for 短超时断言未结束）；
- `answer` 有等待者时不调用 delivery、等待者拿到 selection；
- `answer` 无等待者且 origin 运行已中断时调用 delivery（回调记录 wire，含 selection 与 origin）；
- 工单结束取消：`cancel_pending_for_ticket` 后状态 `cancelled`；
- wire 断言没有 `expires_at`。

`tests/api/test_questions_router.py`：wire 断言改为无 `expires_at`；历史 expired 行回答仍是 409 `QUESTION_EXPIRED`；删除「到点变 expired」用例。

`tests/agent/test_runtime.py`：删 `question_timeout_seconds` 参数与依赖超时的用例（改为跳过/正常回答）。

`tests/evals/`：删除 `scenarios/legacy-question-timeout.yaml` 与 `cassettes/legacy-question-timeout.jsonl`；`environment.py` 默认设置去掉 `question_timeout_seconds`；`test_eval_scenario.py` 去掉该键断言。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest -o addopts= -q tests/agent/test_question_service.py tests/api/test_questions_router.py`
Expected: FAIL（`set_answer_delivery` / `cancel_pending_for_ticket` 不存在、wire 仍带 expires_at）。

- [ ] **Step 3: 改服务与仓储**

`maa_api/agent/questions.py`：按 spec §4.1–4.3 删除超时/扫描/重启清理；`wait` 无 deadline；`answer` 末尾 `await self._deliver_if_orphaned(wire)`；新增 `set_answer_delivery`、`cancel_pending_for_ticket`、`_deliver_if_orphaned`、`_run_active`（`AgentRunStatus` 终态集合：`succeeded/failed/interrupted/cancelled`）；wire 删 `expires_at`。

`maa_api/db/repositories/question.py`：删 `expire_overdue`、`expire_all_pending`；加 `cancel_pending_for_ticket(ticket_id, now)`（`UPDATE ... WHERE origin_ticket_id=? AND status='pending'` 返回 ids）。

`maa_api/db/models.py`：`AgentQuestion.expires_at` 改 `datetime | None = Field(default=None, sa_column=datetime_column("expires_at", nullable=True))`，docstring 标明历史列。

`maa_api/db/migrations/versions/0040_question_expiry_historical.py`：`down_revision="0039_drop_spend_ledger"`；upgrade 用 `batch_alter_table` 把 `expires_at` 改可空；downgrade 回填 `created_at` 后恢复非空。

- [ ] **Step 4: 删配置并改装配**

`settings.py`：删 `SETTING_KEYS` 行与 `AgentSettings.question_timeout_seconds`（class docstring 同步）。
`services/settings_schema.py`：删条目。
`main.py`：删 `timeout_seconds=` 参数、`reset_after_restart()`、`question_service.start()`、两处 `question_service.close()`；在 `worker_service`/`orchestrator_service` 装配之后 `question_service.set_answer_delivery(...)`（按 §4.2 路由）；`ticket_service.add_end_listener(lambda ended: question_service.cancel_pending_for_ticket(ended.ticket_id))`。
`builtin_middleware.py`：`question_required` 事件删 `expires_at`。
`orchestrator/service.py`：`reset` 归档旧会话后 `cancel_pending_for_session(old_id)`（try/except 记日志）。

- [ ] **Step 5: 跑定向测试并修到绿**

Run: `.venv/bin/python -m pytest -o addopts= -q tests/agent/test_question_service.py tests/api/test_questions_router.py tests/agent/test_runtime.py tests/db/test_migrate.py`
Expected: PASS（逐条修；超时相关断言按新语义改写）。

- [ ] **Step 6: 跑 evals 定向**

Run: `.venv/bin/python -m pytest -o addopts= -q tests/evals`
Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add -A maa_api tests
git commit -m "feat(agent): 提问不再过期并支持跨重启作答投递（维护性补丁，见 docs/v3/13 §8.177）"
```

---

### Task 2: 前端提问卡去倒计时与排序

**Files:**
- Modify: `web/src/features/agent/types.ts`、`QuestionCard.tsx`、`format.ts`；`web/src/features/console/attention.ts`；`web/src/features/console/test-utils.tsx`
- Test: `web/src/features/agent/QuestionCard.test.tsx`、`web/src/features/console/attention.test.ts`、`web/src/features/console/ConsolePage.test.tsx`（涉及提问的部分）

**Interfaces:**
- `AgentQuestionPrompt` 去掉 `expires_at`；`QuestionCard` 不再有计时器与 `expired` 本地终态（409 `QUESTION_EXPIRED` 归入 `stale`）；`buildAttentionItems` 里提问组按 `created_at` 升序。

- [ ] **Step 1: 改测试（先红）**

`QuestionCard.test.tsx`：删倒计时/到点超时用例；新增「没有 expires_at 一直在答」；`QUESTION_EXPIRED` 409 → 显示已结束。
`attention.test.ts`：提问排序用 `created_at`（早的在前，缺失排最后）。
`ConsolePage.test.tsx`：提问 fixture 去掉 `expires_at`。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && pnpm test --run src/features/agent/QuestionCard.test.tsx src/features/console/attention.test.ts`
Expected: FAIL（类型/行为还在）。

- [ ] **Step 3: 实现**

`types.ts` 删字段；`QuestionCard.tsx` 删 deadline/now/计时器/`questionRemainingLabel`/`expired` 分支与倒计时渲染，`QUESTION_EXPIRED` → `stale`；`format.ts` 删 `questionRemainingLabel`；`attention.ts` 提问 `expires` 改用 `created_at` 毫秒（缺失为 `Infinity`）；`test-utils.tsx` 的 `makeQuestion` 去 `expires_at`、加 `created_at`。

- [ ] **Step 4: 跑测试与类型检查**

Run: `cd web && pnpm test --run src/features/agent/QuestionCard.test.tsx src/features/console/attention.test.ts src/features/console/ConsolePage.test.tsx && pnpm typecheck`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add -A web/src
git commit -m "feat(web): 提问卡去掉倒计时（维护性补丁，见 docs/v3/13 §8.177）"
```

---

### Task 3: 工单卡选项优先

**Files:**
- Modify: `web/src/features/console/AttentionTicketCard.tsx`、`web/src/features/console/api.ts`、`web/src/api/keys.ts`
- Test: `web/src/features/console/ConsolePage.test.tsx`

**Interfaces:**
- 新增 `expiredQuestionsQuery`（`GET /api/agent/questions?status=expired&size=50`，key `keys.agent.expiredQuestions()`）；`AttentionTicketCard` 按 spec §4.6 渲染：历史提问选项按钮 → type 固定按钮 → 自由文本；按钮点击复用现有 `streamSse` detach 发送。

- [ ] **Step 1: 改测试（先红）**

`ConsolePage.test.tsx`：把现有工单回复用例改为点「其他回复」；新增
- `confirmation` 工单显示「批准/拒绝」，点击发消息 `{content:'批准'}`；
- `decision` 工单显示「同意/不同意」；
- 关联历史 expired 提问的工单显示其选项，点选项发消息内容为选项 label；
- 无 `human_action` 的工单不显示按钮（只有打开工单）。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && pnpm test --run src/features/console/ConsolePage.test.tsx`
Expected: FAIL。

- [ ] **Step 3: 实现**

`keys.ts` 加 `expiredQuestions()`；`api.ts` 加查询；`AttentionTicketCard.tsx` 按 §4.6 重写操作区（按钮行 + 其他回复切换 + 打开工单），发送逻辑复用现有 mutation（内容改为按钮文案/草稿）。

- [ ] **Step 4: 跑测试与类型检查**

Run: `cd web && pnpm test --run src/features/console/ConsolePage.test.tsx && pnpm typecheck`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add -A web/src
git commit -m "feat(console): 工单卡选项优先（维护性补丁，见 docs/v3/13 §8.177）"
```

---

### Task 4: 文档、全量验证与合并

**Files:**
- Modify: `docs/v3/03-数据模型.md`、`04-接口与事件协议.md`、`05-运行时与代理定义.md`、`08-主动性与通道.md`、`09-安全与治理.md`、`10-可观测与评测.md`、`11-前端指挥台.md`、`14-现行接口清单.md`、`13-决策记录.md`
- Create: `docs/v3/ledgers/questions-no-expiry.md`

- [ ] **Step 1: 文档同步**

按 spec §6 更新；13 §8 末尾新增 §8.177（用户口径、实施裁决、验证方式）。

- [ ] **Step 2: 全量验证**

```bash
cd web && pnpm typecheck && pnpm test --run && pnpm build
cd .. && .venv/bin/python scripts/frontend_smoke.py
.venv/bin/python scripts/dump_openapi.py --check
cd web && pnpm gen:api:check && cd ..
.venv/bin/python -m pytest -o addopts= -q 2>&1 | tail -5
.venv/bin/python scripts/check_doc_links.py | tail -2
grep -rn "question_timeout_seconds" maa_api docs/v3/09-安全与治理.md docs/v3/10-可观测与评测.md docs/v3/14-现行接口清单.md || echo "no setting residue"
```

Expected：全绿；后端只有既有 3 条真资源失败；`BROKEN 0`。

- [ ] **Step 3: 台账与合并**

`docs/v3/ledgers/questions-no-expiry.md` 记录任务、裁决、验收；提交后 `git checkout dev && git merge --no-ff questions-no-expiry`，删除分支；合并后补台账「合并提交」。

---

## Self-Review

- 覆盖规格：§4.1（T1 Step 3）、§4.2（T1 Step 3/4）、§4.3（T1 Step 3/4）、§4.4（T1 Step 3/4 + T2）、§4.5（T2）、§4.6（T3）、§4.7（T1 Step 4 + T4 文档）、§5 测试（各任务）、§6 文档（T4）。
- 关键风险都有用例：跨重启投递与不重复投递（T1）、收尾取消（T1）、wire 无字段（T1/T2）、历史 409（T1）、选项优先级（T3）。
