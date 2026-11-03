# M28 进度台账

计划：`docs/superpowers/plans/2026-10-10-m28-gacha-infrast-roguelike.md`
规格：`docs/superpowers/specs/2026-10-09-m25-prts-knowledge.md` §8
分支：`m28-gacha-infrast-roguelike`（自 `refactor/v2`），完成后 tag `v2-m28`

## 任务状态

| 任务 | 状态 | 产物 |
|---|---|---|
| §8.2 卡池 | 完成 | `services/game/gacha.py`、`get_gacha_pools`、注入段 `GACHA_HEADER`（≤3 行） |
| §8.5 剧情 | 完成 | `services/prts_parse/story.py`、`services/game/story.py`、`get_story` |
| §8.3 基建 | 完成 | `services/prts_parse/building.py`、`services/game/building.py`、`get_operator` 的 `building` 段、`get_building_skills`/`get_infrast_candidates`/`list_infrast_plans`/`update_user_profile` |
| D3（自定义基建方案路径） | 完成 | `pipeline_runner._prepare_infrast_params` + `resource_service.resolve_infrast_plan_path` |
| D4（基建方案校验） | 完成 | `resource_service._validate_infrast_plan` 放宽 5 项、补检 4 项；`InfrastInput.facility` 补 Processing/Training |
| D5（肉鸽参数对齐上游） | 完成 | `domain/task.py` 的 `RoguelikeInput`：主题 6、模式 11、13 个新参数、校验矩阵对齐 `RoguelikeConfig.cpp` |
| §8.4 集成战略数据 | 完成 | `services/game/roguelike.py`（按章节取）、`get_roguelike_info` |
| Q44 用户档案 | 完成 | 条目 `agent-profile`、`merge_profile_section`、`update_user_profile`（每次确认 + 差异） |
| Q45 按 period 选班次 | 完成 | `pipeline_runner.select_infrast_plan_index`（跨午夜覆盖） |
| 注入与提示词 | 完成 | 当期卡池段、提示词追加 M28 工具名与基建档案规则 |
| 文档与验收 | 进行中 | docs/13 §8.43 等七份文档；`scripts/prts_smoke.py --m28` 末行 `SMOKE OK` |

## 实测结论

- 卡池挂在**文件页**的 `{{寻访信息}}` 上（`page` 带 `文件:` 前缀）；UP 干员星级用本机 `battle_data.json` 的 `rarity`（就是星数，与 PRTS 页面的 0 基 `稀有度` 不同）；实测 2026-10-10 在开 4 个卡池（海渊巡游等）。
- 剧情正文在 `{{剧情模拟器|文本数据=…}}`；`[name="杜宾"] 台词` → `杜宾：台词`，其余 `[` 指令行丢弃、旁白保留；`分类:剧情` 实测 1713 个成员。
- **`后勤技能名` SMW 属性不可反查**（`[[后勤技能名::+]]` 返回 0 条、`[[银灰]]|?后勤技能名` 也是空）；Cargo `building_skill2` 全表 720 行 / 720 个技能名，单次上限 500 且无 offset → 按 10 个房间分块取，`static` 档缓存。
- 集成战略主题页 170–261 KB，一律 `parse&prop=sections` + `parse&section=N` 按章节取；BlackFlow 分队实测 15 行。

## Rulings I made

1. 当期卡池注入行（名称、UP、结束时间）由 `render_gacha_lines` 渲染，最多 3 行、每行 ≤120 字；超长先省 UP 再截名称。外服由卡池服务自己判（不带 `client_type` 时返回空 + `region_note`），注入段整段不插。
2. 用户档案分节的纯函数（`split_profile_sections` / `merge_profile_section`）放在 `agent/knowledge/__init__.py`：工具写入与确认卡片的差异计算共用同一套语义。
3. `update_user_profile` 进 `ACCOUNT_WRITE_TOOLS`（每次确认、`risk=NONE`），payload 带 `diff`（`section`/`from`/`to`/`mode`）与摘要；读不到现有值时 `from` 为 `null`，卡片照常显示将要写入的内容。
4. `get_operator` 的 `sections` 放开 `building`（M28 把 `building` 从「M28 加入」变成可用）。
5. `AssistantChange` 不加入 `InfrastInput.facility`：规格 D4 只点名 Processing 与 Training，且它不是房间、不参与换班顺序（记入 docs/13 §8.43 的偏离）。
6. 设施枚举补到 9 项但**默认值保持 7 项**：改默认会动到已存流水线的语义，取旧前端日常预设。
7. 剧情正文除 `max_chars` 外另有 22 KB 字节预算（实测 8000 汉字 + 说话人表 + 信封会到 24.6 KB，超 §4.4 的 24 KB）。

## 主 agent 亲自复跑的验收

- 后端 `.venv/bin/python -m pytest -o addopts= -q` → **3 failed, 3107 passed, 5 skipped**；三条失败是两条既有的真资源用例（`test_activity_index.py`）与一条负载抖动（`tests/core/test_crash_recovery.py`，单跑通过）。
- `scripts/dump_openapi.py --check`、`scripts/generate_api_guide.py --check`、`cd web && pnpm gen:api:check`、`scripts/copy_inventory.py --check` 退出码 0。
- 前端 `pnpm typecheck && pnpm test --run && pnpm build` 退出码 0（67 文件 / 691 项）。
- 真网络 `scripts/prts_smoke.py --m28` 末行 `SMOKE OK`：4 个在开卡池（首位海渊巡游、UP 克莱门莎）；`get_building_skills(operator="银灰")` 2 条带描述；`get_roguelike_info(topic="squads")` 主题 BlackFlow、20 个章节、15 个分队；`get_story("1-1 孤岛/BEG")` 702 字、6 个说话人。
- `scripts/agent_smoke.py`、`scripts/frontend_smoke.py` 末行 `SMOKE OK`。

## 实施中修掉的问题

1. `main.py` 装配 `RoguelikeService(settings_provider=...)` 时该构造不接受这个参数 → app 起不来（`tests/api` 28 条红），改成无参构造。
2. 我早期给 `KnowledgeService` 的 gacha 注入加了 `gacha_reader`，卡池服务构造签名随后对齐；`Update_user_profile` 的两条策略用例在工具未注册时红，注册后转绿。
3. 基建子任务的 `building` 段需要在工具层放开 `sections`，并把两处「`building` 是非法值」的旧断言改成 `unknown`。
