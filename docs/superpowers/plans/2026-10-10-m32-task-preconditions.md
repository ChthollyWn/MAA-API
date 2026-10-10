# M32 · 任务执行前提（游戏状态）与流水线护栏

- 分支：`m32-task-preconditions`（自 `refactor/v2`），完成后 tag `v2-m32`
- 依据：用户报告（见规格 §1）；规格 `docs/superpowers/specs/2026-10-10-m32-task-preconditions.md`
- 口径：不是新功能，而是把「任务前提」这一知识补进知识库、工具提示词与系统提示词，并在提交与失败两处加**提醒**（用户明确选「提醒，不拦」）

## 交付项

### W1 内置条目 `task-preconditions`（规格 §4.2、§4.6）

1. 新增 `maa_api/agent/knowledge/builtin/task-preconditions.md`（kind=playbook）：通用规则（内核任务都从主界面起手、只有 `StartUp` 拉客户端）、保证前提的做法、逐任务前提表、失败特征与处置、「不要塞无关任务」的消歧。
2. 登记表接线：`tests/agent/test_knowledge_service.py` 加 `M32_SPEC_PATH`（`_spec_blocks()` 读四份规格）、`BUILTIN_IDS` 与 `BUILTIN_LIST_ORDER` 各加一条（playbook 组内按 id 升序排在 `playbook-sanity-planning` 之后）。
3. 计数同步：`tests/agent/test_knowledge_no_numbers.py` 的 `EXPECTED_ENTRY_COUNT` 32 → 33、`tests/services/test_knowledge_page_pointers.py` 的条目数断言 32 → 33。

### W2 既有条目的就地消歧（规格 §4.3）

`playbook-recruit.md`（把「不要塞 StartUp」改成「不要塞用户没要求的业务任务；游戏没启动时第一条要加 StartUp」）、`playbook-infrast-shift.md`、`playbook-sanity-planning.md`、`account-resources.md` 各补一句指向 `task-preconditions`。

### W3 工具与系统提示词（规格 §4.4）

`maa_api/agent/tools/pipeline.py` 的 `submit_pipeline` / `wait_pipeline` 描述、`maa_api/agent/tools/account.py` 的 `get_operator_box` / `get_depot` 描述、`maa_api/agent/runtime.py` 的「工具怎么用」与「外部资料」两处。

### W4 代码护栏：提醒不拦（规格 §4.5）

1. 新模块 `maa_api/agent/preconditions.py`：`GAME_RUNNING_TASK_TYPES`（`TASK_MODELS` 减去 `StartUp`/`CloseDown`）、`task_type_names`、`needs_running_game`、`missing_start_up_warning`、`failure_hint`，两条文案是单一来源。
2. `submit_pipeline` 结果在缺 `StartUp` 且含游戏内任务时加 `warnings`（为空时不出现该键）；`wait_pipeline` 在 `CORE_COMMAND_FAILED` 失败时加 `hint`。
3. `PolicyEngine` 的 `submit_pipeline` 分支在**确实要确认**时把同一条警告追加进 `reasons`；不改变是否需要确认。
4. 新建 `tests/agent/test_preconditions.py`：纯函数、工具层、策略层用例 + 负向对照（改前必红）。

### W5 文档

`docs/11-Agent模块设计.md`（条目数 32 → 33 与 M32 条目说明）、`docs/12-实施计划与里程碑.md`（M32 小节）、`docs/13-决策记录.md`（§8.47）、`docs/README.md`（里程碑表加 M32）、台账 `docs/superpowers/ledgers/2026-10-10-m32-task-preconditions.md`。

## 验收

1. 后端全量 `pytest -o addopts= -q`：只允许既有的两条真资源用例失败。
2. 四处生成物 `--check` 全 0；`scripts/copy_inventory.py --write-baseline` 后 `--check` 通过（工具描述与系统提示词改动会抬基线）。
3. 知识门禁四文件全绿（新条目无数值命中、页面与工具指引可达、登记表集合相等）。
4. 护栏用例全绿 + 负向对照证明不是永真。
5. 五个冒烟脚本末行 `SMOKE OK`；前端 `pnpm typecheck`（本轮不动前端）。
6. `git merge --no-ff` 回 `refactor/v2` + tag `v2-m32`，不 push、不碰 dev。
