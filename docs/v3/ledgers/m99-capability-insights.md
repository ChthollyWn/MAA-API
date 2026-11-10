# M99 能力效果看板（台账）

- 分支：`capability-insights`；基点：`dev` = `fc3c513`；合并提交：`<由控制器填写>`；tag：`v3-m99`（打 tag 由控制器执行）
- spec：[m99-capability-insights](../specs/m99-capability-insights.md)
- 计划：[m99-capability-insights](../plans/m99-capability-insights.md)

## 做了什么

- 三张表与迁移 `0043_capability_insights`：`eval_run`、`eval_case_result`、`run_feedback`；六个枚举；`EvalRunRepository`、`EvalCaseRepository`、`RunFeedbackRepository`。
- `services/insights/`：7 条指标目录（唯一口径）、Wilson 95% 区间与窗口统计、4 个在线指标与 3 个离线指标的聚合、反馈摘要；9 个 HTTP 端点（目录、总览、趋势、分组、评测列表/详情、评价 PUT/GET、反馈摘要）与 1 个错误码 `EVAL_RUN_NOT_FOUND`。
- 评测发布：`Scenario.metrics`、`CaseResult.title/metrics/tags`、`publish.py`、`scripts/agent_eval.py --publish`（幂等，失败只警告）。14 个既有 capability 场景加 `accuracy`，新增 2 个幻觉与 6 个安全场景（含 2 个对照），场景 23 → 31、capability 18 → 26。
- 前端：`/more/insights` 能力效果页（7 张指标卡、窗口切换、趋势、按工单类型分组、离线评测、最近评价、评估方法）、`/more/evals/:runId` 评测详情页、运行详情页「这次运行怎么样」评价入口；8 个 query key、2 条路由与 agent 组导航项。
- 契约文档：01/02/03/04/10/11/12/13/15、README 与 specs/README 同步，14 总校（174 个错误码、200 条端点、agent 小节 97 条），15 重新生成；`config.template.yaml` 无变化。
- 整支复审 fix wave（提交 `<由控制器填写>`）：满意度与反馈摘要的回收率分子只数「评价与运行都在窗口内结束」的条数（≤100%）；`MetricCardOut` 加 `currency`，成本卡单币种时由前端显示 ¥/$；分组图用 `/api/agent/specs` 的 title 显示工单类型、不再摆 `ticket.kind` 原值；accuracy secondary 展示标签已在 `f855f24` 修正为「可判定用例数」。spec §4.3/§4.5.1/§4.5.2/§4.5.4 与 docs/10 §8 同步。

## 验证证据

| 命令 | 结果 |
|---|---|
| 后端全量 pytest（`/tmp/m99-t12-pytest.log`） | `1`；最终验收全量失败 **3 条**，全部为 Task 1 登记的环境基线（真实活动资源包缺失：`test_activity_index` 2 条、`test_stage_layout` 1 条）；无其他失败。T12 曾多 1 条 incident 顺序 flake，复核后最终验收未复现（见「遗留」）。`pytest.ini` 的 addopts 已含 `-q`，命令行再带 `-q` 变 `-qq`，日志没有 passed 计数 |
| OpenAPI 重生成 + `--check` | 两个 `0`；T12 时 `web/openapi.json` 无变化，fix wave 因新增 `currency` 重生成并再 `--check` 0（见下） |
| `pnpm gen:api:check` | `0`；T12 时 `web/src/types/api.d.ts` 无变化，fix wave 重生成后 check 0 |
| 前端 `pnpm install --frozen-lockfile` / `pnpm typecheck` | `0` / `0` |
| 前端 `pnpm test --run`（`/tmp/m99-t12-web-test.log`） | `0`；125 files / 1066 tests passed |
| 前端 `pnpm build`（`/tmp/m99-t12-web-build.log`） | `0`；SW harness `SERVICE WORKER OK` |
| `scripts/frontend_smoke.py` | `0`，末行 `SMOKE OK` |
| `scripts/agent_smoke.py` | `0`，末行 `SMOKE OK`；仓库 DB/config 指纹未变 |
| `agent_eval --suite regression --mode replay` | `0`，regression `5/5`，`EVAL OK`，结果 id `20261110-181136-regression-replay-0375` |
| `agent_eval --suite capability --mode replay` | `0`，capability `26/26`，`EVAL OK`，结果 id `20261110-181528-capability-replay-1a97`（首次与前端 `pnpm install` 并发时 `m89-new-activity-custom-task` 90 秒超时，25/26；静默重跑 26/26，见「遗留」） |
| `agent_eval --suite capability --mode replay --publish` | `0`，`26/26`，`EVAL OK`，`已发布 20261110-181757-capability-replay-14e6`，日志 0 条「发布失败」 |
| `generate_api_guide.py` + `--check` | 两个 `0`；15 补进 `EVAL_RUN_NOT_FOUND` 一行 |
| `scripts/check_doc_links.py` | `0`，末行 `BROKEN 0` |
| `git diff --check` | `0` |
| 最终整支复审（HEAD `f855f24`） | Verdict **With fixes**，无 Critical；4 条 in-scope 修复项：① 满意度/反馈摘要的回收率分子可超过分母（>100%）；② 单币种成本卡不带币种符号；③「按工单类型」分组图显示原始 `ticket.kind`；④ docs/13 §8.186 与台账未随 `f855f24` 更新 |
| 本次 fix wave（提交 `<由控制器填写>`） | 四条修复：① 回收率分子 join `agent_run`，只数「评价与运行都在窗口内结束」的条数，保证 ≤1.0；② `MetricCardOut.currency` + 前端 `formatMicros(value, currency)`，单币种显示 ¥/$、多币种保持现状，`web/openapi.json` 与 `web/src/types/api.d.ts` 已重生成；③ 分组图用 spec title 显示工单类型（找不到回退 label）；④ docs/13 §8.186 与台账校正（label 已修、incident 方向、pytest 3 条）。验证见 task-12 报告「Final-review fix wave」 |
| 手工 API 验收（真实 `create_app()` TestClient + 开发库 `resource/maa_api.db`） | 见下 |

手工验收（2026-11-11，命令输出在 `/tmp/m99-t12-manual.log`、`/tmp/m99-t12-manual-overview.log`）：

- `GET /api/agent/evals/runs?page=1&size=5` → 200，`total=3`：`20261110-181757-capability-replay-14e6`（capability replay，26 例）、两条 regression replay smoke 行（各 5 例）。
- `GET /api/agent/insights/overview?window_days=30` → 200，7 张卡，`warnings` 含「准确率、幻觉率、安全率还没有 record/live 模式的评测结果」；离线三项 `value=null`、`sample_size=0`，accuracy 的 `replay 用例数=14`，与 capability replay 发布行一致。在线四项按开发库数据给出完成率/成本等读数。
- `GET /api/agent/runs/865c766f-503a-4629-ae10-dc08102c672e/feedback` → 200 `{"item":null}`。
- `PUT .../feedback` `rating=4` → 200，`id=1`、`created_at=2026-11-10T18:19:56.302034Z`；再次 GET 回读一致；总览满意度卡 `value=1.0`、`sample_size=1`、负向率 0。
- `PUT .../feedback` `rating=2` → 200，同一 `id=1`、`created_at` 不变、`updated_at` 前进；总览满意度卡 `value=0.0`、`sample_size=1`、负向率 1；`GET /api/agent/insights/feedback?window_days=30&limit=5` → 200，`total=1`、`negative=1`，最近评价包含该 `run_id`、`ticket_id`、`agent_name=analyst`、`rating=2` 与原因 `slow`。
- 测试后用 `DELETE FROM run_feedback WHERE run_id='865c766f…'` 清理，开发库 `run_feedback` 回到 0 行；开发库本身 gitignored。

## Rulings I made

- `EvalCaseResult`/`RunFeedback` 的四个 JSON 列显式 `nullable=False`，与迁移一致；`json_column()` 缺省可空。spec 已按复审补注。
- `EvalRunRepository.page_runs`/`get_run` 归在 `EvalRunRepository`（不是 `EvalCaseRepository`），按实现改正 spec §4.2。
- `metric_rates` 在路由层按该次运行自身全部 case 现算（T7 的写范围不含 `service.py`），不复用看板窗口口径；`hallucination_rate` 取 `1 - passed/(passed+failed)`，Wilson 用失败数。
- 发布只写 `variant=target`；`model_calls` 取 target 用例之和；`score` 不臆造聚合口径，发布为 `NULL`；`graders[].details` 最多 5 条、每条 256 字。
- 发布用同步 `create_engine` + 单事务 `merge`：同 id 先删旧 cases 再整行覆盖；缺表/写库失败转 `PublishError`，CLI 只警告，不改评测退出码。
- 评价 `reasons` 先按首次出现去重再限 5 个（spec 写「去重后最多 5 个」）；`comment` 去空白后为空存 `NULL`，非空保存 trim 后的值；9 个端点都保留 `SERVICE_UNAVAILABLE` 503 门禁。
- 离线聚合 SQL 先取 `replay` 再在 Python 里拆成 secondary，主值只算 `record`/`live`；趋势只返回有数据的天，不补零；分组 `truncated` 恒为 `false`（`ticket.kind` 是有限集合）。
- 前端指标卡的 `n=` 与获取方式提示以 `sample_size` 判定：`value=null` 且 `sample_size>0`（多币种成本）不显示获取提示；评价表单只在读取成功后挂载，读取失败时不显示可提交表单，避免空表单覆盖已有评价。
- `AUTOINCREMENT` 命名的 `pk_eval_case_result`/`pk_run_feedback` 是 SQLAlchemy/SQLite 的固有行为，`sqlite_autoincrement` 仍按 spec 打开。

## 偏离（已记 13 §8.186）

- M99 计划上追加在 P5 之后，本轮先于 M91–M98 执行，占用迁移 `0043`；M91–M98 的建议号顺延为 `0044`–`0048`。
- 评测场景 23 → 31；capability 基线 18/18 → 26/26；replay 结果不进入能力主值，只在准确率 secondary 中显示。
- 没有模型 key：`capability --mode live`/`record` 未跑，离线三项显示「暂无数据」；以 replay 5/5、26/26 与既有台账基线为准。
- `/api/agent/runs` 的 `ticket_id` 过滤恒空的缺陷不在本里程碑修复范围；看板服务端聚合直接读库。
- 新增 9 个端点、1 个错误码、3 张表、6 个枚举；无新增配置键、依赖、工具、SSE/WS 事件。

## 遗留

- 在线 LLM 判分、judge 录制与开放式幻觉检测（当前幻觉率/安全率是确定性场景口径）。
- ticket 级反馈（当前只有运行级评价；工单满意度由名下运行汇总）。
- A/B 实验指标与 M98 的版本化（本里程碑不建相关表、不读指标）。
- 历史评测结果导入（`resource/evals/` 里的旧结果不迁移）。
- `eval_case_result.score` 暂为 `NULL`；若后续要展示分数，需先定义聚合口径。
- `RunFeedbackRepository.upsert` 的并发首提交竞态（T2 复审延后，单次提交无问题）。
- `tests/agent/test_incidents.py::test_incident_allowed_action_still_uses_confirmation_service` 在 T12 全量出现过超时；控制器复核为既有顺序/装配 flake：单测单跑通过（0.91s），仅跑 `tests/agent/test_incidents.py` 整个文件时该测超时（16 例中 1 失败，62s），最终验收全量未复现。测试源码自带 M68/M71「已知 flake」注释（确认广播与 waiter 注册的竞态），分支未改 `runtime.py`/`harness/`/该测试，与 M99 代码无关。
- `/api/agent/runs` 的 `ticket_id` 过滤缺陷（spec §2 明确不在本里程碑修复）。
- 未做真机/真实浏览器手工验收；前端行为由 jsdom 测试、build 与 `frontend_smoke` 覆盖。
