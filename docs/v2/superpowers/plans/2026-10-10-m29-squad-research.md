# M29 · 阵容研究（作业站、自行设计、授权内自动重试）· 实施计划

规格见 [specs/2026-10-09-m25-prts-knowledge.md](../specs/2026-10-09-m25-prts-knowledge.md) §9（下文「§」都指该文件）。行为以规格为准；规格没覆盖的实现细节由实现者决定并写进报告，主 agent 抄进台账。

## 全局约束

- 工作分支 `m29-squad-research`（自 `refactor/v2`）。直接在当前检出上工作，不建 worktree，不 push，不合 `dev`。
- 后端测试 `.venv/bin/python -m pytest -q`；前端 `cd web && pnpm typecheck && pnpm test --run && pnpm build`。
- 改后端接口后：`.venv/bin/python scripts/dump_openapi.py`、`cd web && pnpm gen:api`、`.venv/bin/python scripts/generate_api_guide.py`。
- 先写失败测试确认 RED，再实现 GREEN；长输出重定向到文件只读尾部与退出码。
- **实现者不要 `git commit/add/stash/checkout`**；`maa_api/main.py`、`maa_api/agent/policy.py`、`maa_api/agent/runtime.py`、`maa_api/agent/tools/__init__.py`、`maa_api/agent/knowledge/__init__.py`、`web/src/features/agent/format.ts`、`web/src/components/layout/nav.ts`、`web/src/routes/router.tsx` 由主 agent 改。
- 不引入新依赖。单测不打网（真网络只在 `scripts/copilot_research_smoke.py` 与 `--m29` 冒烟里用）。

## 分工与写范围

| 工作流 | 写范围 |
|---|---|
| A 关卡数据（T3、T10 后半） | `services/external/gamedata.py`、`services/prts_parse/gamedata.py`、`services/game/level_data.py`、`services/game/levelup.py`（升级经验/龙门币常量）、`services/settings_schema.py` 与 `config.template.yaml` 的 `sources.gamedata_*`、`agent/tools/stage_level.py`（新文件，一条工具）、对应测试与夹具 |
| B 作业站与草稿（T4、T5） | `services/external/copilot_site.py`、`services/game/copilots.py`、`services/copilot/__init__.py`、`services/copilot/draft.py`（静态校验 + 存资源）、`agent/tools/copilot_site.py`（新文件，两条工具）、对应测试与夹具 |
| C 授权、尝试与结果（T2 的 D2/D7、T6、T7） | `db/models.py` 的 `copilot_grant` 表、`db/migrations/versions/0017_copilot_grant.py`、`db/repositories/copilot.py`、`services/copilot/grant.py`、`services/copilot/result.py`、`services/copilot/attempt.py`、`domain/task.py`（`CopilotInput` 的 D2 修复 + `support_unit_usage` + `Custom` 任务类型 D7）、`agent/tools/copilot_attempt.py`（新文件，两条工具）、对应测试 |
| D 清理与通知（T8、T10 前半） | `services/retention_service.py`（作业资源的保留与清理）、`services/copilot/notify.py`（`copilot_research_finished` 的 payload 构造）、`domain/enums.py` 加 `NotifyEvent.COPILOT_RESEARCH_FINISHED`、`services/notify_service.py` 的中文名、`config.template.yaml` 的保留天数、对应测试 |
| E 前端（T9） | `web/src/features/copilots/**`（作业页、授权卡片、授权条、工具卡片）、`web/src/routes/copilots/index.tsx`、`web/src/features/agent/ConfirmationCard.tsx`（`grant_copilot_attempts` 卡片）、对应测试 |
| 主 agent | `agent/policy.py`（授权策略 + 确认动作）、`main.py` 装配、`agent/tools/__init__.py` 注册、提示词（§9.8）、工具中文名两边、测试门禁（工具目录/任务类型/错误码/路由小节/通知事件）、`scripts/copilot_research_smoke.py`、docs/13 §8.44 等文档、验收与合并 |

## 冻结的接口

- `StageLevelService.level_data(stage, *, include=None, wave_detail="summary") -> dict`；坐标一律换算成 MAA 作业的 `location`。
- `CopilotSiteClient.query(*, stage, order="hot", page=1, limit)` / `.get(site_id)`。
- `CopilotService.search(*, stage, limit=5, order="hot", only_feasible=True) -> dict`；三档 `ok`/`missing_one`/`infeasible`，排序缺口数 → 好评率 → 热度。
- `CopilotDraftService.save(*, content=None, site_id=None, parent_id=None, note="", session_id=None) -> dict`（kind=COPILOT 的 `ResourceAsset`，`meta` 见 §9.5）。
- `CopilotGrantService(session_factory, *, settings_provider)`：`request(...)`、`get_active(session_id)`、`consume(session_id, stage_id)`、`approve(...)`、`revoke(session_id)`、`mark_stale_active()`（重启标 `interrupted`）。
- `CopilotAttemptService.run(...)`、`CopilotResultService.judge(callbacks) -> outcome`、`record(attempt_id, outcome, evidence)`。
- 通知：`build_copilot_research_notification(...) -> (event, payload)`。
- 清理：`RetentionService.cleanup_copilots(now=None) -> dict`（`{"deleted": [...], "skipped": [...]}`）。

## 关键口径（规格逐条）

- 坐标：关卡文件的行列 → MAA `location`（T1 探针结论写在 `services/prts_parse/gamedata.py` 的模块说明里，必须有一份可用 `1-7` 作业的全部署点落在可部署格子上的测试）。
- 作业校验 D2：放行 `skill=0`、`skill_usage=3`、`costs`；`CopilotInput.support_unit_usage`（0/1）；新任务类型 `Custom`（D7）。
- 静态校验：部署坐标在地图内且落在可部署格；近战/远程与地块类型匹配；干员在账号里或是唯一允许的助战补位；部署数 ≤ 上限；技能序号 ≤ 技能数；`Click`/`Swipe` 不限制。
- 授权：`grant_copilot_attempts` 确认动作，payload 见 §9.6；有效期只在**开始下一次尝试**时检查；同时最多一个 `active`；重启把 `active` 标 `interrupted` 并发通知。
- 策略（只对 INTERNAL）：`run_copilot_attempt` 与含 Copilot 任务的 `submit_pipeline` 必须有授权；无授权返回 `confirmation_action="grant_copilot_attempts"`；关卡不符 `COPILOT_GRANT_STAGE_MISMATCH`；次数用完 `COPILOT_GRANT_EXHAUSTED`；REST 保持现状。
- 结果判定按 §9.7 的表；`record_copilot_result` 只允许在 `unknown` 上调用。
- 清理按 §9.11 的五个条件；成功的作业与用户上传的永不自动删除。

## 验收（规格 §9.12，主 agent 在最终提交上亲自跑）

1. `.venv/bin/python -m pytest -q`；2. 三处 `--check`（OpenAPI / docs14 / 前端生成类型）；3. 前端 `typecheck / test / build` 与 `frontend_smoke.py`、`agent_smoke.py` 末行 `SMOKE OK`；4. `scripts/copilot_research_smoke.py` 用假内核回调与假外部来源跑通「搜索 → 导入 → 授权 → 两次尝试（失败、三星）→ 通知」，末行 `SMOKE OK`；5. `copy_inventory.py --check`；6. 真机（简单关卡、授权 1 次、三星标准）作为补充证据；7. `git merge --no-ff` 回 `refactor/v2` + tag `v2-m29`，台账归档。

## 不做

上传作业到作业站与评分；`copilot_list` 列表模式；保全派驻与悖论模拟作业；吃药或碎石。
