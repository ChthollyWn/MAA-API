# M29 进度台账

计划：`docs/superpowers/plans/2026-10-10-m29-squad-research.md`
规格：`docs/superpowers/specs/2026-10-09-m25-prts-knowledge.md` §9
分支：`m29-squad-research`（自 `refactor/v2`），完成后 tag `v2-m29`

## 任务状态

| 任务 | 状态 | 产物 |
|---|---|---|
| T1 探针（坐标换算等） | 完成 | `services/prts_parse/gamedata.py` 的模块 docstring + 三条可执行断言 |
| T2 D2 修复 / `support_unit_usage` / `Custom`（D7） | 完成 | `services/copilot/rules.py`（校验口径）、`resource_service._validate_copilot` 委托它、`domain/task.py` 的 `CopilotInput.support_unit_usage` 与 `CustomInput` |
| T3 拆包关卡数据 | 完成 | `services/external/gamedata.py`、`services/game/level_data.py`、`get_stage_level_data` |
| T4 作业站与筛选 | 完成 | `services/external/copilot_site.py`、`services/game/copilots.py`、`search_copilots` |
| T5 草稿与资源 | 完成 | `services/copilot/draft.py`、`save_copilot_draft`、三条资源 REST 端点 |
| T6 结果识别与判定 | 完成 | `services/copilot/result.py`（`MaaApi@CopilotResult…` 系列 + `judge_callbacks`） |
| T7 授权与一次尝试 | 完成 | `copilot_grant` 表 + 迁移 0017 + `services/copilot/{grant,attempt}.py`、`run_copilot_attempt`/`record_copilot_result`、策略分支 |
| T8 通知与推送 | 完成 | `services/copilot/notify.py`、`NotifyEvent.COPILOT_RESEARCH_FINISHED`、前端事件选项与实时刷新 |
| T9 前端作业页 | 完成 | `web/src/features/copilots/**`、`grant_copilot_attempts` 确认卡片、授权条、工具中文名 |
| T10 保留与清理 + 养成规划补经验/龙门币 | 完成 | `retention_service.cleanup_copilots`、`services/game/levelup.py` 接进 `UpgradePlanner` |
| T11 文档、冒烟、验收 | 完成 | docs/13 §8.44 等九份文档、`scripts/copilot_research_smoke.py` |

## 实测结论

- **坐标**：MAA 作业的 `location` 就是 `[col, row]`、行自上而下，与关卡文件的 Tile-Pos 同一套（不翻转不换轴）。证据：1-7 的 `mapData.map` 是自上而下的下标矩阵；作业 22311 的 5 个部署点 5/5 落在可部署格且地面/高台一致；26 条出怪路线起点落 `tile_start`、终点落 `tile_end`。
- **作业站**：`GET /copilot/query` 与 `/copilot/get/<id>`；「作业不存在」是 **HTTP 200 + body `status_code=404`**；列表里的 `content` 不含 `actions`；作业链接实测为 `https://prts.maa.plus/copilot/<id>`。
- **静态检查判据是 `buildableType`**（不是 `tileKey`）：1-7 的高台在文件里叫 `tile_wall`，实测远程干员正常部署在 `buildableType=2` 的格子上。
- **拆包访问**：jsDelivr 对 `?path=` 形式回 HTTP 400，路径只能走路径（`…@master/zh_CN/gamedata/levels/…json`）；本机 `cdn.jsdelivr.net` 直连超时，`fastly.jsdelivr.net` 可用。升级经验与龙门币取自 `gamedata_const.json` 的三张表，作战记录折算取自 `item_table.json` 的 `expItems`。
- **关卡数据体积**：1-7 打包 6,615 B（工具结果 7,542 B）；压力用例（400 出怪 / 30 路线 / 120 途经点）`summary` 10,953 B、`full` 15,440 B，都 ≤ 24 KB。

## Rulings I made

1. 策略层：`run_copilot_attempt` 与「含 Copilot 任务的 `submit_pipeline`」只对 INTERNAL 调用方要求授权；无授权返回 `confirmation_action="grant_copilot_attempts"`（预填 attempts/success/support + `session_id`/`stage_id`/`stage_code`，关卡从作业资源 `meta` 读）。关卡比对仍由 `CopilotGrantService.consume` 兜（它比服务层更早拿到作业的关卡）——两处都不省，policy 少写一条也不会漏判。
2. `agent.copilot_grant_minutes` 的取值域取 **1–1440**（规格附录 B 写 10–120）：附录 B 是「设置项参考值」，实现放宽不会破坏语义，但要在 docs/13 §8.44 记一条偏离并同步附录 B。
3. `update_user_profile` / `grant_copilot_attempts` 等确认动作的 payload 由策略层的 `extra_payload` 提供，前端卡片只读它，不再各自解析 `arguments`。
4. `prts.maa.plus` 同时加进后端 `envelope.LINK_HOSTS` 与前端 `SOURCE_LINK_HOSTS`：否则作业站链接不会渲染成可点外链。
5. 设施/参数的机械门禁（任务类型 12 → 13、工具 70 → 75、错误码 112 → 114、路由 110 → 114、业务表 25 → 26、索引 28 → 31）随本次接口变化同步。
6. `resource_service._validate_copilot` 改为委托 `services/copilot/rules.py::normalize_content`（D2 的单一落点），HTTP 上传路径与草稿路径因此同口径。
7. 冒烟脚本的静态检查用 `layout_provider`/`operators_provider` 返回 `None` 跳过（那两样要网络或资源包，单测已用真夹具覆盖），协议校验照常跑。

## 主 agent 亲自复跑的验收

- 后端 `.venv/bin/python -m pytest -o addopts= -q` → **2 failed, 3453 passed, 5 skipped**；两条失败是 M24 起就存在的真资源用例（`tests/services/test_activity_index.py`）。
- 生成物 `dump_openapi.py --check`、`generate_api_guide.py --check`、`pnpm gen:api:check`、`generate_task_types_fixture.py --check`、`copy_inventory.py --check` 全部退出码 0（先重新生成）。
- 前端 `pnpm typecheck && pnpm test --run && pnpm build` 退出码 0（70 文件 / 711 项）。
- 冒烟：`scripts/copilot_research_smoke.py`（假作业站 + 假内核回调跑通「搜索 → 导入 → 授权 → 两次尝试（失败、三星）→ 通知 → 清理」）、`agent_smoke.py`、`frontend_smoke.py`、`account_smoke.py`、`prts_smoke.py --m28` 末行均 `SMOKE OK`。
- 真机补充证据（未跑，本机不连设备）：1-7 用作业站作业、授权 1 次、三星标准。

## 实施中修掉的问题

1. `main.py` 装配 `RoguelikeService(settings_provider=…)` 时构造不接受该参数（M28 遗留）；本次装配 `CopilotResultService` 时同样先漏了 `resource_service`，已补。
2. `pipeline_runner` 重建 Copilot 的 `core_params` 时丢掉 `support_unit_usage`（「缺一名时借助战」会静默失效），已补并更新既有断言。
3. `settings_schema._FIELDS` 与 `SETTING_KEYS` 在并行编辑中出现过缺键与重复（`KeyError: 'sources.cache_level_seconds'`、同一键写两遍），已补齐与去重。
4. 错误码门禁我一开始用整文件正则替换，误伤了 `test_errors.py` 的其它数字，已回退成定点修改。
5. 前端 `schedules/payload.ts` 的 Copilot 模板在新 schema 下缺 `support_unit_usage`（类型报错），已补默认 0。
