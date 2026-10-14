# M48 只读探索工具与侦察 subagent（规格）

> 分支 `m48-subagent`，基点 `refactor/v2` = `f9d6d7a`（2026-10-14）。
> 本文是 M48 的唯一规格；实施后的偏离记在 [13 §8.71](../../13-决策记录.md)。

## 1. 背景

MAA-API 内置 Agent 的 76 个工具全是**游戏域**工具（关卡、作业、基建、干员、更新、流水线、媒体、日志查询）。整个 `maa_api/agent/` 里没有任何读文件、列目录、grep、shell 的能力：唯一沾边的是 `get_logs`，它查的是服务自己的日志表（数据库），不是文件。

后果有两个，都在真实会话里出现过：

1. **它没法自己查代码/资源**。用户问「这个报错为什么出现」「这条关卡数据从哪来」时，Agent 只能凭知识库条目与外部工具回答；涉及仓库里的事实（`maa_api/` 里的实现、`resource/` 里的资源、迁移脚本、测试）它看不见。
2. **一旦有了探索能力，长探索会直接烧父上下文**。对照证据是本机 DSH 会话 `session-d8cf968c`（M47，2026-10-14）：turn 3 一步跑 299 个步骤、85 分钟，单步重读上下文从 125k 涨到 447k token，全会话 130.1M token 里 93.2M 花在这一个 turn 上，其中 99.8% 是把前文反复重喂（模型真正写出的只有 0.25M）。同一批文件被反复改写 14 次。**探索的动作与它的原始输出留在主上下文里，是这类会话烧穿上下文的直接原因。**

M48 同时解决这两件事：给内置 Agent 一套**只读探索工具**，再用一个**侦察 subagent** 把长探索的过程挡在父上下文之外——父会话只收一份结构化报告。

## 2. 目标与非目标

**目标**

1. 新增只读探索工具组 `recon`（对模型可见）：`list_files`、`read_file`、`search_code`，范围内可查 `maa_api/`、`web/src/`、`tests/`、`docs/`、`scripts/`、`resource/` 等仓库内容。
2. 新增 `delegate_recon` 工具：把「查清楚某个问题」整段交给一个**只读侦察 subagent** 跑多步，返回结构化报告（结论 + 证据路径:行 + 不确定项）；**子 agent 的中间过程与原始输出不进入父会话历史**。
3. 新增配置 `workspace`（工作区根目录）与 `agent.recon_*` 三个预算项；给出越界、密钥文件、超大文件、二进制文件的统一拦截口径。
4. 门禁与文档同步：工具目录断言 76 → 80（其中对模型可见 75 → 79）、`config.template.yaml`、docs/11、docs/13 §8.71。

**非目标（本轮不动）**

- **不给子 agent 任何写能力**：不写文件、不改仓库、不跑测试、不碰 shell、不碰游戏设备（`device` / `pipeline` / `raw` 域工具一律不暴露）。
- 不引入新的外部依赖；不引入向量检索或 embedding。
- 不改父 agent 的回合循环（`_run_turn_body`）：M48 的子 agent 循环是独立实现，只复用 `_make_client` 与流式收块的读数口径。
- 不做「子 agent 再派子 agent」（深度固定为 1）。
- 不改前端：工具目录不落 OpenAPI 快照（`recon` 组不进 REST 目录的生成物，见 §5）。

## 3. 决策

- **D1（工作区根目录）**：新增顶层配置 `workspace.root`，字符串，**默认空串**。空串表示「关闭探索能力」——`list_files` / `read_file` / `search_code` / `delegate_recon` 四个工具此时一律返回明确的 `WORKSPACE_NOT_CONFIGURED` 错误，而不是静默降级。**实施裁决（2026-10-14，取代本决策初稿的「默认取进程 CWD」）**：初始默认值改成空串。理由：launchd 拉起服务时 CWD 可能是 `/` 或应用包目录，「默认取 CWD」等于把一个未知目录当只读窗口；关掉默认、让部署者显式配置，语义更硬也更好排查。
- **D1b（四项都不进设置 API）**：`workspace.root` 与三个 `agent.recon_*` 都登记进 `SETTING_KEYS`（否则 `config.yaml` 与环境变量两层会被整个忽略——这是实施中实测到的真实缺口），同时列进 `CONFIG_ONLY_SETTING_KEYS`：`workspace.root` 是宿主机绝对路径，放开给设置 API 等于让任何持有访问令牌的人把 agent 的只读窗口指向任意目录；三个预算是运维阈值，前端没有对应产品语义（与 M42 的森空岛三项同款）。
- **D2（工具形态）**：字面文件名与常见约定术语（`list_files`、`read_file`、`search_code`、`delegate_recon`）对模型更直白，采用它们；组名统一 `recon`，四个工具 `risk=SAFE`、`read_only=True`。
- **D3（一个搜索工具承担内容与 git 两类检索）**：`search_code` 用 `mode` 参数二选一（`content` / `git`），而不是加第五个工具——工具目录已经 76 个，能不加就不加。`mode=git` 时只允许 `status_local` / `diff_local` / `log_path` / `blame_path` 四个动作，全部对本地仓库，**绝不允许** `push` / `fetch` / `checkout` / `clean` / `reset` / 远端操作。
- **D4（委派的边界）**：子 agent 只拿 `list_files` / `read_file` / `search_code` 三个工具，默认预算 `max_steps=8`（工作区可配 1–20），总字符预算按 `agent.recon_max_chars`（默认 200_000）截断，逐次工具输出按 `agent.recon_output_chars`（默认 20_000 字符）截断并给 `truncated.next_offset` 续读。
- **D5（父会话只收报告）**：`delegate_recon` 的返回值就是父上下文能看到的全部内容：`question`、`summary`（≤ 1500 字符）、`findings[]`（每条 `title` / `detail` / `evidence[]`，`evidence` 是 `path:line` 形式）、`uncertainties[]`、`steps_used`、`budget`。子 agent 的对话、完整文件内容、被截断的原始输出都不进父历史。

## 4. 设计

### 4.1 配置

```yaml
# 工作区：只读探索工具的根目录；留空表示关闭探索能力
workspace:
  root: /Users/you/Developer/WorkSpace/MAA-API

agent:
  # 侦察 subagent 的步数上限（1–20）
  recon_max_steps: 8
  # 侦察过程中工具输出的逐次截断（字符）
  recon_output_chars: 20000
  # 一次侦察的累计字符预算，超出即收尾（字符）
  recon_max_chars: 200000
```

`Settings` 新增 `workspace: WorkspaceSettings`（字段 `root: str = ""`）；`AgentSettings` 追加三个字段，范围校验 `1 ≤ recon_max_steps ≤ 20`、`1000 ≤ recon_output_chars ≤ 200_000`、`10_000 ≤ recon_max_chars ≤ 2_000_000`。

### 4.2 只读探索工具组 `maa_api/agent/recon/explore.py`

四个工具的公共边界（所有校验在**读之前**完成，错误一律 `AppError`）：

| 边界 | 口径 |
|---|---|
| 根目录 | `settings.workspace.root`；未配置（空串）→ `WORKSPACE_NOT_CONFIGURED`（503） |
| 路径解析 | 相对根目录解析；`resolve()` 后必须仍在根目录内，否则 `WORKSPACE_PATH_DENIED`（403，软链接同样按解析后的真实路径判定） |
| 大小 | 单文件 > `2 MiB` 拒绝读（`WORKSPACE_FILE_TOO_LARGE`，400） |
| 二进制 | 前 8 KiB 含 `\x00` 即判二进制，拒绝读（`WORKSPACE_PATH_DENIED`） |
| 密钥 | 拒绝读（不是省略）：`.env`、`config.yaml`、`*.pem`、`*.key`、`id_rsa*`、`*credential*`、`*secret*`、`.dsh/`；正文里出现私钥头或 `api_key = <16+ 字符>` 也拒读。目录列举时这些条目标 `blocked: true` 但仍出现（让模型知道存在、不要试） |
| 忽略目录 | `.git/`、`node_modules/`、`.venv/`、`__pycache__/`、`.pytest_cache/`、`.superpowers/`、`dist/`、`build/`、`resource/lib/` 默认不列举、不搜索（用 `os.walk` 原地剪枝，不是事后过滤） |
| 工具输出 | 单次结果 ≤ `agent.recon_output_chars`；超出给 `truncated: {next_offset, reason}` |

**错误码口径（实施对齐）**：本节初稿给六个边界各留了一个码，实施时收敛成**三个新码**（`WORKSPACE_NOT_CONFIGURED` / `WORKSPACE_PATH_DENIED` / `WORKSPACE_FILE_TOO_LARGE`），其余复用既有码——目录不存在用 `NOT_FOUND`、「要列举的路径其实是文件」用 `INVALID_PARAMETER`、非法正则用 `TOOL_ARGS_INVALID`、非 git 仓库与 git 不可用用 `EXTERNAL_SOURCE_UNAVAILABLE`。理由：这四类都是既有语义的直接复用，新造码只会让 docs/05 §4 的错误码表膨胀而信息量不变。表见 `docs/05` §4.20。

**工具 1 `list_files`**：参数 `path`（默认 `.`）、`depth`（1–3，默认 1，语义是**相对根目录的层数**：`depth=1` 只列根目录的直接子项）、`pattern`（可选 glob，如 `*.py`）。返回 `root` 与 `entries[]`：`path`（相对根目录）、`kind`（`file`/`dir`）、`bytes`、`blocked`。列举与搜索都按解析后的真实路径判定，指向根目录之外的软链接条目直接跳过（与 `read_file` 的越界拦截同一口径）。

**工具 2 `read_file`**：参数 `path`、`offset`（字符偏移，默认 0）、`max_chars`（默认取 `recon_output_chars`，**上限也是它**——调用方只能往下要，不能把整个文件拉进父上下文）。返回 `path`、`text`、`bytes`、`chars`、`start_line`、`truncated`。文本按 UTF-8 解码，解码失败按 `WORKSPACE_PATH_DENIED` 拒绝。

**工具 3 `search_code`**：参数 `mode`（`content` | `git`）、`pattern`、`path`（默认 `.`）、`glob`（可选）、`max_results`（默认 40，上限 100）、`context_lines`（0–3，默认 0）。

- `mode=content`：对根目录内可读文本文件做正则搜索（大小、忽略目录、软链接越界、**正文密钥探测**都与 `read_file` 同一口径——含私钥或 `api_key = <16+ 字符>` 的文件整个跳过，否则搜索会成为 `read_file` 拒读的旁路），返回 `matches[]`：`path`、`line`、`text`（含上下文的行）、`column`。正则非法 → `TOOL_ARGS_INVALID`。
- `mode=git`：`action` 取 `status_local` / `diff_local` / `log_path` / `blame_path`，用 `git -C <root>` 执行；路径范围由 **`path`** 决定（`git diff --stat -- <path>`），`pattern` 在 git 模式下只作占位、被忽略（schema 里写明）。白名单外的动作返回 `WORKSPACE_PATH_DENIED`；非 git 仓库或本机没有 git → `EXTERNAL_SOURCE_UNAVAILABLE`。

**工具 4 `delegate_recon`**（`maa_api/agent/recon/delegate.py` + `runner.py`）

参数：`question`（必填，1–500 字）、`hints`（可选，最多 5 条「去哪找」的提示）、`max_steps`（可选，1–20，默认取配置）。

流程：

1. 取 `settings.workspace.root`，未配置直接报错；取 LLM 设置与 `AgentRuntime._make_client` 同一套构造口径。
2. 组装**子 agent 系统提示**：只读、不许猜、每条结论必须带 `path:line` 证据、没有证据就写进 `uncertainties`、`question` 与 `hints` 之外不得扩大范围。
3. 循环至多 `max_steps` 步：每步一次流式补全，只带 `list_files` / `read_file` / `search_code` 的 schema；工具按 §4.2 口径执行并把结果压成「下一次请求可见的文本」；累计字符超过 `recon_max_chars` 立即收尾（报告里 `budget.exhausted=true`）。
4. 收尾时要求模型输出 JSON 报告（`summary` / `findings` / `uncertainties`）；解析失败时退化为「把最后一段文本当 summary、`findings=[]`」，不抛错给父 agent。
5. 返回 §3 D5 的结构；`steps_used` 与 `budget` 一并返回，让父 agent 知道报告是否完整。

### 4.3 父 agent 侧接线

- `maa_api/agent/tools/recon.py`：`register_tools(registry)` 注册 `delegate_recon`，`risk=SAFE`、`read_only=True`、`group="recon"`，handler 从 `context.request.app.state.settings_provider` 取设置、从 `app.state.agent_runtime` 取 runtime；两者缺一 → `AGENT_DISABLED`。
- `maa_api/agent/tools/__init__.py`：`build_registry()` 里加一行 `recon` 注册（探索三件套直接注册，不走 LLM）。
- `maa_api/agent/runtime.py`：`AgentRuntime.recon(question, hints, max_steps)` 公开入口，内部调 `recon.runner.run_recon(...)`；不参与 `_run_turn_body` 的会话状态（子 agent 不占设备、不写 `agent_message`）。
- 系统提示词追加一段「查仓库」（实施已落地，`SYSTEM_PROMPT` 里在「数据边界」段之前）：涉及仓库事实（实现位置、报错来源、资源文件、迁移脚本、用例）先用 `delegate_recon` 查清，不要在父会话里逐个文件读；位置已确定、只看一两个文件时直接 `read_file`；报告里没有证据的结论不当事实；报告写「没查清」就如实说卡在哪。

### 4.4 文档与生成物

- `config.template.yaml` 增补 `workspace.root` 与三个 `agent.recon_*` 项（含注释口径）。
- docs/11（Agent 模块设计）新增「只读探索与侦察 subagent」小节：工具清单、边界口径、预算、为什么只收报告。
- docs/13 §8.71：把「为什么给内置 agent 加探索能力」「为什么深度固定为 1」「为什么 git 只给四个只读动作」写进决策记录。
- `tests/agent/test_tool_catalog.py` 的 `EXPECTED_TOOLS` 与计数断言：76 → 80、75 → 79。

## 5. 风险与对策

| 风险 | 对策 |
|---|---|
| 探索工具把密钥/配置读进模型上下文 | §4.2 的密钥名单在读之前拦截；`config.yaml`、`.env`、`.dsh/` 一律拒读；用例逐条覆盖 |
| 越界读（`../`、符号链接） | `resolve()` 后校验前缀；软链接按真实路径判定；用例覆盖 `..`、绝对路径、软链接三种 |
| 子 agent 烧 token（正是本里程碑要治的病） | 步数上限 + 逐次输出截断 + 累计字符预算 + 深度固定 1；超预算收尾而不是继续 |
| 子 agent 报告不可信/无证据 | 提示词强制 `path:line`；`findings` 无 `evidence` 时保留但标记；父 agent 侧提示词要求「报告里没有证据的结论不要当事实」 |
| `resource/lib/` 下的二进制库被当文本读 | 二进制探测 + 忽略目录双保险 |
| 工具目录计数断言被忘记 | `test_tool_catalog.py` 的集合断言是精确集合，漏改必然红 |

## 6. 验收

1. `tests/agent/test_recon_explore.py`：路径越界/软链接/密钥/二进制/超大文件/忽略目录/截断续读/正则非法/git 白名单，逐条用例。
2. `tests/agent/test_recon_delegate.py`：用假 LLM 客户端（按步返回现造的 tool_calls）覆盖「正常出报告」「预算耗尽收尾」「步数用尽」「报告 JSON 非法退化」「未配置工作区报错」，并断言**父上下文只拿到报告**（返回值里不含完整文件内容）。
3. `.venv/bin/python -m pytest -q` 全绿（既有两条真资源失败照旧）。
4. `.venv/bin/python scripts/dump_openapi.py --check` 不变（`recon` 组不落 REST 目录）。
5. 主 agent 亲自复跑：后端全量、`git diff --check`，并做一次负向对照（`workspace.root` 留空时四个工具都报 `WORKSPACE_NOT_CONFIGURED`）。
