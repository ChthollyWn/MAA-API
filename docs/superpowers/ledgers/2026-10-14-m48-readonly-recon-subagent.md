# M48 只读探索工具与侦察 subagent（台账）

- 规格：[docs/superpowers/specs/2026-10-14-m48-readonly-recon-subagent.md](../specs/2026-10-14-m48-readonly-recon-subagent.md)
- 计划：[docs/superpowers/plans/2026-10-14-m48-readonly-recon-subagent.md](../plans/2026-10-14-m48-readonly-recon-subagent.md)
- 分支：`m48-subagent`（基点 `refactor/v2` = `f9d6d7a`），tag `v2-m48`
- 决策记录：[docs/13 §8.71](../../13-决策记录.md)
- 来源：用户 2026-10-14「还有一个问题，你看我最新的 agent 会话，你发现了什么问题」→「有大量的重复响应，而且上下文很快就占满了，相比于 dsh，非常不耐用，最后没有完成我的任务」→ 在三条方案里选「交给 subagent」

## 用户裁决（开工前两步确认）

1. **对象是 MAA-API 内置 agent**，不是 DSH 代码会话。开工前实测确认：内置 agent 的 76 个工具全是游戏域工具，整个 `maa_api/agent/` 没有读文件、列目录、grep、shell 的能力——第 3 条不是「调整用法」，而是要新造「只读探索工具 + subagent 委派」整套能力。先把这条冲突摆给用户，用户确认按内置 agent 做。
2. **能力边界 = 只读文件系统 + git 只读**。明确否决了「只读 + 白名单 shell（能跑 pytest）」：那等于开放命令执行，风险与实现量都上一个台阶。
3. **开 M48 里程碑**（新分支 + 规格 + 计划 + 台账 + 全量门禁），不在 `refactor/v2` 上小步直改。

## 我做的裁决（实现细节，记录在案）

| # | 裁决 | 理由 |
|---|---|---|
| R1 | `workspace.root` 默认 **空串**，否决规格初稿的「默认取进程 CWD」 | launchd 拉起服务时 CWD 可能是 `/` 或应用包目录；把未知目录当只读窗口不可接受。空串＝能力关闭，`WORKSPACE_NOT_CONFIGURED` 明确报错 |
| R2 | 四个新键**登记 `SETTING_KEYS`**（否则 `config.yaml` 与环境变量两层被整个忽略——实施中实测到的真实缺口），同时进 `CONFIG_ONLY_SETTING_KEYS` | `workspace.root` 是宿主机绝对路径，放开给设置 API 等于让持令牌者把只读窗口指向任意目录；三个预算是运维阈值，前端无产品语义（与 M42 森空岛三项同款） |
| R3 | 错误码只新增三个（`WORKSPACE_NOT_CONFIGURED` / `WORKSPACE_PATH_DENIED` / `WORKSPACE_FILE_TOO_LARGE`），其余复用既有码 | 目录不存在用 `NOT_FOUND`、「要列举的其实是文件」用 `INVALID_PARAMETER`、非法正则用 `TOOL_ARGS_INVALID`、非 git 仓库用 `EXTERNAL_SOURCE_UNAVAILABLE`；为四种既有语义各造新码只会让 docs/05 §4 的表膨胀 |
| R4 | `search_code mode=git` 的路径范围取 **`path`**，`pattern` 在该模式被忽略（规格初稿写的是 `pattern` 表示路径） | 「查哪块」与「匹配什么」分属两个参数更直白；测试里保留一条 `xfail` 钉规格初稿的读法，docs/13 §8.71 五、3 记偏离 |
| R5 | 子 agent 的工具调用**不单独落审计** | 对模型可见的动作只有父 agent 那一次 `delegate_recon`（本身在派发层被审计）；子 agent 只读工作区、没有设备与写路径，再开一套审计是过度设计 |
| R6 | `findings` 无 `evidence` 时**保留**（不丢结论），靠父侧提示词「没有证据的结论不要当成事实」兜底 | 丢结论会让父 agent 误以为没查到；保留 + 提示词比隐式过滤更诚实 |
| R7 | 深度固定为 1（子 agent 不能再派子 agent） | 预算与回归面都可控；真需要两层时是另一个里程碑的事 |

## 交付

- **配置**：`WorkspaceSettings.root`（空串＝关闭）与 `AgentSettings.recon_max_steps=8` / `recon_output_chars=20000` / `recon_max_chars=200000`；`config.template.yaml` 增补 `workspace:` 段与三个 `recon_*` 项。
- **只读探索组 `recon`（四个工具，全 `SAFE`/`read_only`）**：`list_files`、`read_file`、`search_code`（`content` 正则 / `git` 四只读动作）、`delegate_recon`。边界：工作区根目录、越界含软链接按真实路径判定、密钥名单与正文探测、二进制、2 MiB、忽略目录用 `os.walk` 原地剪枝、截断续读。
- **侦察子循环** `maa_api/agent/recon/runner.py`：步数 + 单次输出 + 累计字符三层预算，任一层到顶即收尾；报告 JSON 非法时退化；只有三个只读工具的 schema。
- **父会话只收报告**：返回 `question` / `summary` / `findings`（带 `path:line` 证据）/ `uncertainties` / `steps_used` / `stopped` / `budget`，子 agent 的中间步骤与文件正文不进父历史。
- **接线**：`AgentRuntime.recon(...)`（复用 `_make_client`，不占会话锁、不写 `agent_message`、不碰设备）；`SYSTEM_PROMPT` 新增「查仓库」段；docs/05 §4.20 三个错误码；docs/11 新小节；docs/13 §8.71；docs/12 M48 小节。工具总数 76 → **80**（对模型可见 75 → **79**）。

## 主 agent 亲自复跑的验收（最终提交上）

| 门禁 | 结果 |
|---|---|
| `.venv/bin/python -m pytest -o addopts="" -q` | **2 failed, 3783 passed, 5 skipped, 2 xfailed**（211s）：两条失败是既有的 `tests/services/test_activity_index.py` 真资源用例（本期未动该模块，开工前同一状态，见 docs/ENVIRONMENT.md 口径） |
| `.venv/bin/python -m pytest -o addopts="" -q tests/agent` | **649 passed, 1 skipped, 2 xfailed** |
| `.venv/bin/python scripts/dump_openapi.py --check` | `OpenAPI snapshot is current`（`recon` 组不落 REST 目录，快照未变） |
| `cd web && pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build` | 退出码 0（未改前端，作回归证据） |
| 负向对照 | `workspace.root` 留空 → 四个工具都报 `WORKSPACE_NOT_CONFIGURED`；配好根目录 → 能读到文件、读不到 `.env`（`WORKSPACE_PATH_DENIED`）、`read_file(max_chars=10_000_000)` 被夹到 20000 |
| 端到端（默认步进器 + 真实 `registry.execute`） | `/tmp/m48_smoke/test_e2e.py` → 子 agent 第一步 `search_code` 读到工作区真实内容、第二步出报告，`E2E OK` |

## 实施期修掉的缺陷（由 W3 用例抓出，全部已修并有绿用例钉住）

1. **P0 子 agent 读不到工作区**：`dispatch_tool` 造 `ToolContext` 时 `request=None`，工具取不到 `settings_provider`，每次调用只回 `AGENT_DISABLED`（`settings` 形参完全没用上）。改成造一个**只含 `settings_provider`** 的最小 request（刻意不透传父 agent 的真实 state，否则子 agent 会顺着拿到设备与确认服务）。
2. **P0 回灌模型的 `tool_calls` 形态错**：发回模型的 assistant 消息用的是内部扁平结构 `{id,name,arguments}`，不是 OpenAI 的 `{id,type,function:{...}}`，真实兼容端点会 400。改成在 `run_recon` 里显式折成 OpenAI 形态，内部读取走 `_call_parts` 兼容两种。
3. **P1 `read_file.max_chars` 无上限**：调用方传超大值就能把整个文件拉进父上下文，与本期动机相反。改成夹在 `recon_output_chars` 之下。
4. **P1 `search_code` 软链接越界**：搜索逐文件读字节时只查名单与大小，不校验真实路径，根内一个指向根外的软链接就能绕过 `read_file` 的拦截读到外部文件。改成 `_walk` 与搜索共用 `_inside_root`（解析后判定）。
5. **P1 `search_code` 绕过正文密钥探测**：`read_file` 拒读含私钥/`api_key=...` 的文件，搜索却把同一行原样回给模型。改成同一道 `_SECRET_BODY_PATTERN` 闸。
6. **P2 `list_files depth` 多列一层**：`depth=1` 会把子目录里的文件也列出来。改成「相对根目录的层数」语义。
7. **P2 系统提示词缺「查仓库」段**：规格 §4.3 要求的那段没写。已补，位置在「数据边界」段之前。

## 未覆盖 / 待验证

- 真实 LLM 端点下的整轮行为（尤其缺陷 2 修复后的形态）没有真机验证：本机自测与用例都用假客户端 + 真实临时 git 仓库。
- `search_git` 的「本机没有 git」与「git 超时」两个分支要打桩 `subprocess` 才能造，未覆盖。
- `AgentRuntime.recon` 的 `owned=True` 分支（真造 `OpenAICompatibleClient` 并在 finally 里 `aclose`）会真打网，未覆盖；其余接线（settings_provider、model、三层预算、`max_steps` 夹到 20、schema 只含三个工具）已用注入假客户端覆盖。
- 子 agent 的单步超时 `RECON_TIMEOUT_SECONDS = 60.0` 未在真机上验证过（上游慢时的表现）。
- 结论：**M48 的交付面（配置、只读边界、委派、父只收报告、门禁、文档）已闭环；上述四项是有意留作后续真机验证的补充证据，不阻塞本里程碑。**
