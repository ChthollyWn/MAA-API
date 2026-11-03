# M78 Skills 体系（规格）

> 阶段：P2　依赖：M77　分支：`skills`　迁移：`0031_skills`
> 本文是 M78 的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在新分支上，工作区干净 | `git status -sb` | `## skills`，没有改动 |
| 2 | M77 已合并 | `git tag -l 'v3-m77'` | 输出 `v3-m77` |
| 3 | 迁移 head 是 0030 | `ls maa_api/db/migrations/versions/ \| rg '^00' \| sort \| tail -n 1` | `0030_knowledge_index.py`；不是就取下一个可用号，台账记差异 |
| 4 | 内置知识 34 条、其中 9 条 playbook | `ls maa_api/agent/knowledge/builtin/*.md \| wc -l; rg -l '^kind: playbook$' maa_api/agent/knowledge/builtin \| wc -l` | `34` 与 `9` |
| 5 | 8 个总控与 Worker 定义 | `ls maa_api/agent/specs/builtin/{orchestrator,general,daily_ops,copilot,planner,diagnose,analyst,ops}.md \| wc -l` | `8` |
| 6 | 代理定义有 `skills` 字段 | `rg -n 'skills' maa_api/agent/specs/schema.py` | 至少一行 |
| 7 | 代理定义注册表的列举方法 | `rg -n 'class AgentSpecRegistry\|def (all\|list\|get)\b' maa_api/agent/specs/registry.py` | 记下「列出全部定义」的方法名，T3 用 |
| 8 | 中间件链怎么排序 | `rg -n 'DurableContextMiddleware\|MemoryInjectionMiddleware\|def build_chain\|MIDDLEWARE_ORDER' maa_api/agent` | 记下链的组装位置；`SkillsMiddleware` 还不存在 |
| 9 | 系统提示第 4 段的写入接口 | `rg -n 'def (add_section\|set_section)\|sections' maa_api/agent/context/layout.py` | 记下中间件往系统提示加段的方法名，T4 用 |
| 10 | 压缩前确定性保存 | `rg -n 'skills' maa_api/agent/context/durable.py` | 记下 M74 是否已留 `skills` 键 |
| 11 | 运行的 `meta` 怎么落库 | `rg -n 'meta' maa_api/agent/runs/service.py` | 记下中间件私有状态写进 `agent_run.meta` 的方式，T4 用 |
| 12 | 按需工具组的开启函数 | `rg -n 'def enable_tool_groups\|enabled_tool_groups' maa_api/agent` | 记下 v3 里开启工具组的函数，T4 用 |
| 13 | 引用 9 个 id 的位置 | `rg -n -l 'account-resources\|playbook-daily-weekly\|playbook-drop-lookup\|playbook-infrast-shift\|playbook-promotion-planning\|playbook-recruit\|playbook-sanity-planning\|playbook-stage-clear\|task-preconditions' maa_api tests web/src scripts --glob '!maa_api/agent/knowledge/builtin/**'` | 记下文件清单，T6 逐个处理 |
| 14 | 工具与错误码计数 | `rg -n 'assert len\(actual\) ==\|model_visible\) ==' tests/agent/test_tool_catalog.py; rg -n '^ALL_CODE_COUNT\|^HTTP_BOUND_CODE_COUNT' tests/domain/test_errors.py` | 记下当前值；工具 +2，错误码 +3 |
| 15 | 前端导航 | `rg -n 'secondaryNav' web/src/components/layout/nav.ts; rg -n "more/settings/knowledge" web/src/routes/router.tsx` | 各至少一行 |
| 16 | 基线测试 | `.venv/bin/python -m pytest -q > /tmp/m78-base.log 2>&1; echo $?; tail -n 3 /tmp/m78-base.log` | 记下失败数 |

## 1. 背景与目标

v2 把「怎么做」写成 `kind=playbook` 的知识条目，索引每轮都进上下文、正文按需读。v3 把这类内容改成技能（V3-D44）：常驻只放名字与说明，用到才加载正文，并能顺带开启工具组、在压缩后保留。口径见 [07 §4](../07-知识RAG与记忆.md)。

完成后：

1. `maa_api/agent/skills/builtin/` 下有 9 个技能，内容来自 9 条 `playbook` 知识条目；知识库剩 25 条内置条目。
2. 用户对这 9 条的覆盖由迁移 0031 搬进 `agent_skill`，迁移前后用户看到的内容一致。
3. 总控与 7 个 Worker 的系统提示第 4 段有技能索引；`load_skill` 返回正文与工具可用情况，激活状态写进 `agent_run.meta.skills` 并跨轮继承；压缩后正文补回第 4 段。
4. `daily_ops` 预加载 `task-preconditions`；`copilot` 预加载 `task-preconditions` 与 `playbook-stage-clear`。
5. `read_knowledge("playbook-recruit")` 返回 `KNOWLEDGE_NOT_FOUND`，消息指向 `load_skill`。
6. 四个技能端点与前端技能页可用；M30 的无数值检查与 PRTS 页面指引检查覆盖技能正文。

## 2. 非目标

- 经验沉淀生成草稿（`status=draft`、`origin=generated`）与 `propose_skill`：M95。表结构本里程碑就支持。
- 技能不进检索索引，`search_knowledge` 查不到技能。
- 用户自建的 `kind=playbook` 知识条目不迁移，`playbook` 仍是合法的知识种类。
- 不改 `legacy_chat.md` 与 `runtime.py` 的 `SYSTEM_PROMPT`（v2 原文，只用于回放）。
- 工具集固定的代理不因加载技能而增加工具。
- 技能的版本历史（M98）。

## 3. 相关文档

- 契约：[07 §4](../07-知识RAG与记忆.md)；[03 §10.1](../03-数据模型.md)、§13 `SkillOrigin`；[04 §2.3、§5、§6](../04-接口与事件协议.md) 的 M78 行；[05 §1.2](../05-运行时与代理定义.md)（`skills` 字段）、§5.2 第 5、6 位、§6.1、§7 第 4 段；06 的压缩与确定性保存一节；[13](../13-决策记录.md) V3-D44。
- v2：`rg -n '^### 6.1\|^### 6.2' docs/v2/11-Agent模块设计.md`（条目与覆盖语义）；`rg -n 'M30 的迁移语义' docs/v2/11-Agent模块设计.md`（删除内置条目时覆盖行的处理）。
- 代码入口：
  - `maa_api/agent/knowledge/__init__.py`：`_merge`（约 664）、`_override_row`（约 794）、`read_for_agent`（约 869）、`put_entry`（约 985）、`delete_entry`（约 1045）、`load_builtins`（约 556）。技能的覆盖合并照它们写。
  - `maa_api/agent/knowledge/convergence.py`：`migrate_deleted_overrides` 与空的 `FROZEN_BUILTIN_ENTRIES`。本里程碑用迁移 0031 搬覆盖行，这个机制保持空操作。
  - `maa_api/db/repositories/agent_knowledge.py`：仓储写法。
  - `maa_api/api/routers/knowledge.py`：路由与响应模型写法。
  - `web/src/features/settings/knowledge/KnowledgeSection.tsx`、`KnowledgeEditor.tsx`、`api.ts`：列表与编辑页的交互照它们做。

## 4. 设计

### 4.1 数据模型

枚举：

```python
class SkillOrigin(StrEnum):
    """agent_skill 行的来历。"""

    OVERRIDE = "override"     # 覆盖内置技能，只存与内置不同的列
    USER = "user"             # 用户自建，字段必填
    GENERATED = "generated"   # M95 经验沉淀生成
```

表（`maa_api/db/models.py`，放在 `KnowledgeChunk` 之后）：

```python
class AgentSkill(SQLModel, table=True):
    """技能的覆盖层：内置技能在代码里，表里只存覆盖、用户技能与生成的草稿（03 §10.1）。

    覆盖行的 NULL 列继承内置值；与内置完全一致且启用时不留行。表只有几十行、每轮全量读，不建索引。
    """

    __tablename__ = "agent_skill"

    id: str = Field(primary_key=True, max_length=48)
    title: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, sa_column=text_column("body"))
    tools: list[str] | None = Field(default=None, sa_column=json_column("tools"))
    agents: list[str] | None = Field(default=None, sa_column=json_column("agents"))
    origin: str = Field(max_length=16)
    status: str = Field(default="active", max_length=16)
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow, sa_column=datetime_column("created_at"))
    updated_at: datetime = Field(default_factory=utcnow, sa_column=datetime_column("updated_at"))
```

迁移 `0031_skills.py`（`down_revision` 为前置检查第 3 条的上一条）：

```python
MIGRATED_IDS = (
    "account-resources", "playbook-daily-weekly", "playbook-drop-lookup",
    "playbook-infrast-shift", "playbook-promotion-planning", "playbook-recruit",
    "playbook-sanity-planning", "playbook-stage-clear", "task-preconditions",
)
_IN = ", ".join(f"'{i}'" for i in MIGRATED_IDS)

def upgrade() -> None:
    op.create_table(
        "agent_skill",
        sa.Column("id", sa.String(length=48), nullable=False),
        sa.Column("title", sa.String(length=32), nullable=True),
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("tools", sa.JSON(), nullable=True),
        sa.Column("agents", sa.JSON(), nullable=True),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_agent_skill"),
    )
    # 9 条 playbook 的覆盖行搬成技能覆盖行：when_to_use → description，kind 丢弃。
    op.execute(
        "INSERT INTO agent_skill (id, title, description, body, tools, agents, origin, status,"
        " enabled, created_at, updated_at)"
        " SELECT id, title, when_to_use, body, NULL, NULL, 'override', 'active', enabled,"
        f" created_at, updated_at FROM agent_knowledge WHERE id IN ({_IN})"
    )
    op.execute(f"DELETE FROM agent_knowledge WHERE id IN ({_IN})")

def downgrade() -> None:
    # 只能还原 9 条的覆盖行；用户技能与生成的草稿随表删除，不可还原。
    op.execute(
        "INSERT OR REPLACE INTO agent_knowledge (id, kind, title, when_to_use, body, enabled,"
        " created_at, updated_at)"
        " SELECT id, NULL, title, substr(description, 1, 120), body, enabled, created_at, updated_at"
        f" FROM agent_skill WHERE origin = 'override' AND id IN ({_IN})"
    )
    op.drop_table("agent_skill")
```

为什么不用 M30 的 `migrate_deleted_overrides`：它会把覆盖行补全成同 id 的自定义知识条目，用户的改动就留在知识库、和新技能重复。迁移在 `ensure_schema()` 里先于 lifespan 执行，搬走以后 `agent_knowledge` 里没有这 9 个 id 的行，`_merge` 不会遇到「内置已删但覆盖行残缺」的情况。

`tests/db/test_models.py`：`BUSINESS_TABLES` 加 `agent_skill`；`EXPECTED_TEXT_COLUMNS` 加 `("agent_skill", "body")`；`EXPECTED_JSON_COLUMNS` 加 `("agent_skill", "tools")`、`("agent_skill", "agents")`。没有索引、外键、唯一约束要加。`tests/domain/test_enums.py` 加 `SkillOrigin` 整表。

### 4.2 模块与文件

内置技能目录 `maa_api/agent/skills/builtin/<id>/SKILL.md`，9 个，映射如下（`title`、`description` 照抄原条目的 `title`、`when_to_use`；正文照抄原条目正文，再按 §4.5 的规则改引用）：

| 原知识条目 id | 技能 id | `title` | `agents` | `tools` |
|---|---|---|---|---|
| `account-resources` | 同左 | 查询账号资源 | `[general, daily_ops]` | `[get_screenshot, back_to_home, submit_pipeline, wait_pipeline]` |
| `playbook-daily-weekly` | 同左 | 日常与周常清单 | `[orchestrator, general, daily_ops]` | `[submit_pipeline, get_prts_page]` |
| `playbook-drop-lookup` | 同左 | 查掉落与选图 | `[general, daily_ops, planner, analyst]` | `[resolve_stage, get_drop_rates, get_drop_stats, search_prts, get_prts_page]` |
| `playbook-infrast-shift` | 同左 | 基建换班实操 | `[general, daily_ops, planner]` | `[list_infrast_plans, set_infrast_plan, submit_pipeline, wait_pipeline, get_screenshot, ask_user]` |
| `playbook-promotion-planning` | 同左 | 养成规划（精二与专精） | `[general, planner]` | `[get_operator, get_depot, plan_upgrade, get_drop_rates, get_skland_status, sync_skland_training, submit_pipeline, ask_user]` |
| `playbook-recruit` | 同左 | 公开招募实操 | `[general, daily_ops]` | `[list_task_types, submit_pipeline, wait_pipeline, get_pipeline, get_screenshot, ask_user]` |
| `playbook-sanity-planning` | 同左 | 理智规划与刷图 | `[general, daily_ops, planner]` | `[get_activity_stages, get_stage, resolve_stage, get_drop_stats, submit_pipeline, wait_pipeline, ask_user]` |
| `playbook-stage-clear` | 同左 | 攻克指定关卡 | `[general, copilot]` | `[search_copilots, get_stage_level_data, save_copilot_draft, run_copilot_attempt, record_copilot_result, search_prts, get_stage, resolve_stage, update_resource, wait_update, ask_user]` |
| `task-preconditions` | 同左 | 任务执行前提与游戏状态 | `[general, daily_ops, copilot, diagnose]` | `[get_screenshot, back_to_home, submit_pipeline]` |

`tools` 来自原条目正文里出现的工具名（2026-11-02 用注册表逐个匹配后挑出与步骤直接相关的）。每个 `SKILL.md` 的 frontmatter 恰好 6 个字段，`version: 1`。例：

```markdown
---
id: playbook-recruit
title: 公开招募实操
description: 用户要你自动公开招募、指定想招的星数或职业、或准备消耗招聘许可与加急许可时
tools: [list_task_types, submit_pipeline, wait_pipeline, get_pipeline, get_screenshot, ask_user]
agents: [general, daily_ops]
version: 1
---
（原条目正文）
```

新包 `maa_api/agent/skills/`：

| 文件 | 内容 |
|---|---|
| `__init__.py` | 导出 `SkillRegistry`、`SkillsMiddleware`、`builtin_skills`、`load_builtin_skills` |
| `registry.py` | 加载、合并、校验、读写、索引与加载 |
| `middleware.py` | `SkillsMiddleware` |
| `builtin/<id>/SKILL.md` | 9 个内置技能 |

`registry.py`：

```python
SKILL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")
MAX_TITLE_CHARS, MAX_DESCRIPTION_CHARS, MAX_BODY_CHARS = 32, 200, 20_000
MAX_SKILL_FILE_BYTES = 64 * 1024
SKILL_FILE_PATTERN = re.compile(r"^(?:[a-z0-9_-]+/)?[a-z0-9][a-z0-9._-]{0,63}\.(?:md|json|yaml|yml|txt)$")
FRONTMATTER_FIELDS = frozenset({"id", "title", "description", "tools", "agents", "version"})
MAX_INJECTED_BODY_CHARS = 12_000   # 预加载与压缩后补回的正文合计上限

class SkillBuiltinError(RuntimeError): ...

@dataclass(frozen=True, slots=True)
class BuiltinSkill:
    id: str
    title: str
    description: str
    tools: tuple[str, ...]
    agents: tuple[str, ...]
    version: int
    body: str
    files: tuple[str, ...]     # 同目录除 SKILL.md 外、匹配 SKILL_FILE_PATTERN 的相对路径

def load_builtin_skills(root: Traversable | None = None) -> dict[str, BuiltinSkill]:
    """读 builtin/*/SKILL.md；目录名不等于 id、字段缺失或多余、长度不合法、id 重复都抛 SkillBuiltinError。"""

@lru_cache(maxsize=1)
def builtin_skills() -> dict[str, BuiltinSkill]: ...

class SkillSummary(BaseModel):
    id: str
    title: str
    description: str
    tools: list[str]
    agents: list[str]
    source: Literal["builtin", "user", "generated"]
    modified: bool
    status: Literal["draft", "active"]
    enabled: bool
    body_chars: int
    files: list[str]
    version: int | None          # 内置技能的 frontmatter 版本；其余为 None
    updated_at: datetime | None

class SkillBuiltinView(BaseModel):
    id: str; title: str; description: str; tools: list[str]; agents: list[str]; body: str; version: int

class SkillDetail(SkillSummary):
    body: str
    builtin: SkillBuiltinView | None

class SkillWrite(BaseModel):
    """PUT 的完整生效内容；不在模型层限长，校验失败统一报 SKILL_INVALID。"""
    title: str
    description: str
    body: str
    tools: list[str] = []
    agents: list[str] = []
    enabled: bool = True

@dataclass(frozen=True, slots=True)
class SkillIndexEntry:
    id: str
    title: str
    description: str

class LoadedSkill(BaseModel):
    id: str
    title: str
    description: str
    body: str
    tools: list[str]
    files: list[str]
    version: str              # sha256(body + "\n" + ",".join(tools)) 前 12 位

class SkillRegistry:
    def __init__(self, session_factory, *, tool_names: Collection[str],
                 agent_kinds: Mapping[str, str],
                 builtins: Mapping[str, BuiltinSkill] | None = None,
                 settings_provider: Callable[[], Settings] = get_settings) -> None:
        """构造时校验内置技能的 tools 都存在、agents 都是 orchestrator 或 worker 种类，不合法抛 SkillBuiltinError。"""
    async def list_skills(self) -> list[SkillSummary]: ...
    async def get_skill(self, skill_id: str) -> SkillDetail: ...                 # SKILL_NOT_FOUND
    async def put_skill(self, skill_id: str, payload: SkillWrite) -> SkillDetail: ...   # SKILL_INVALID
    async def delete_skill(self, skill_id: str) -> None: ...   # SKILL_BUILTIN_READONLY / SKILL_NOT_FOUND
    async def index_for(self, agent_name: str) -> list[SkillIndexEntry]: ...
    async def load(self, skill_id: str, *, agent_name: str | None) -> LoadedSkill: ...  # SKILL_NOT_FOUND
    def read_file(self, skill_id: str, path: str) -> str: ...                     # SKILL_NOT_FOUND
    def validate_preloads(self, specs: Iterable[AgentSpec]) -> None: ...          # SkillBuiltinError
    @staticmethod
    def render_index(entries: Sequence[SkillIndexEntry]) -> str: ...
```

合并规则（照知识库的 `_merge`）：

- 内置技能先放进结果；`origin='override'` 的行按列覆盖，NULL 继承内置值（`tools`、`agents` 为 NULL 也继承），`enabled` 取行里的值，`modified` 为「任一列与内置不同或已停用」。
- `origin` 为 `user` 或 `generated` 的行是独立技能；`tools` 为 NULL 当空列表，`agents` 为 NULL 当空列表（表示所有总控与 Worker 可用）。
- `origin='override'` 但内置里没有这个 id（以后的版本删了某个内置技能）的行：跳过并记一条 warning，不让列表读不出来。
- `put_skill` 写内置 id：算出与内置不同的列写成覆盖行；没有任何不同且 `enabled` 为真时删行。写新 id：`origin='user'`、各列必填。
- 校验（不合法都报 `SKILL_INVALID`，`details={"field", "reason"}`，未知工具与代理另带 `unknown`）：`id` 匹配模式；`title` 1–32 字；`description` 1–200 字；`body` 1–20000 字；`tools` 每个都在注册表里；`agents` 每个都是已注册的 `orchestrator` 或 `worker` 定义。
- `index_for(agent)`：`skills.enabled` 为真时，取 `enabled`、`status='active'`、`agents` 为空或包含该代理的技能，按 `id` 排序。
- `load`：技能不存在、停用、草稿、不对该代理开放、`skills.enabled` 为假，都报 `SKILL_NOT_FOUND`，消息分别是「技能不存在或已停用」「这个技能不对当前代理开放」「技能已在设置里关闭」，`details` 带 `skill_id` 与 `agent`。
- `read_file`：只有内置技能有附属文件；`path` 必须匹配 `SKILL_FILE_PATTERN`、不能含 `..`、解析后仍在技能目录里、大小不超过 64 KiB；否则报 `SKILL_NOT_FOUND`（`details.reason` 写原因）。
- `validate_preloads`：每个代理定义的 `skills` 里的 id 都要是内置技能，且该技能的 `agents` 为空或包含这个代理；不满足就抛 `SkillBuiltinError` 中止启动。

`render_index` 的输出：

```
技能索引（需要时用 load_skill 加载全文；预加载与压缩前加载过的技能正文附在后面）：
- playbook-recruit｜公开招募实操：用户要你自动公开招募、指定想招的星数或职业、或准备消耗招聘许可与加急许可时
```

没有可用技能时整段不出现。

`middleware.py`：

```python
class SkillsMiddleware(Middleware):
    """05 §5.2 第 5 位。注入技能索引与需要补回的正文；记录 load_skill 的激活状态。"""

    name = "skills"
    applies_to = {"orchestrator", "worker"}
    STATE_KEY = "skills.active"      # list[{"id": str, "version": str, "loaded_seq": int}]

    def __init__(self, registry: SkillRegistry, *, session_factory) -> None: ...
    async def on_run_start(self, ctx: RunContext) -> None: ...
    async def wrap_tool(self, ctx: RunContext, call: ToolCall, call_next) -> ToolOutcome: ...
    async def on_run_end(self, ctx: RunContext, outcome: RunOutcome) -> None: ...
```

工具 `maa_api/agent/tools/skills.py`：

```python
class LoadSkillParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_id: str = Field(pattern=SKILL_ID_PATTERN.pattern)

class ReadSkillFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_id: str = Field(pattern=SKILL_ID_PATTERN.pattern)
    path: str = Field(min_length=1, max_length=128)
```

| 工具 | 组 | 风险 | `read_only` | `refetchable` | `rest_visible` | 描述 |
|---|---|---|---|---|---|---|
| `load_skill` | `skills` | SAFE | True | False | False | 「加载一个技能的全文。系统提示的技能索引里有和当前请求对得上的技能时，先加载再动手，按技能的步骤做。」 |
| `read_skill_file` | `skills` | SAFE | True | True | False | 「读技能目录里的附属文件。技能正文提到附属文件、需要更细的清单或示例时用。」 |

handler：`load_skill` 调 `app.state.skill_registry.load(skill_id, agent_name=getattr(ctx, "agent_name", None))`，返回 `LoadedSkill` 的字典；`read_skill_file` 返回 `{"skill_id", "path", "content", "chars"}`。`load_skill` 不可重取（`refetchable=False`），旧结果不会被 M74 的结果清理换成占位。

其他文件：

- `maa_api/db/repositories/agent_skill.py`：`AgentSkillRepository(BaseRepository)`，方法 `get`、`all`、`upsert`、`delete`，写法照 `AgentKnowledgeRepository`。
- `maa_api/api/routers/skills.py`：§4.3。
- `maa_api/settings.py`：`SkillsSettings(enabled: bool = True)`，`Settings.skills`。
- `maa_api/agent/knowledge/__init__.py`：§4.5 的知识库改动。
- `maa_api/agent/context/durable.py`：`durable["skills"]` 取 `ctx.state["skills.active"]`，每项 `{"id", "title", "version"}`（前置检查第 10 条显示 M74 已留键就填它，没留就加这个键）。
- `maa_api/main.py`：02 §5 第 9 步挂 `app.state.skill_registry`（在 `retriever` 之后）；构造时 `tool_names={d.name for d in build_registry().list()}`、`agent_kinds` 由前置检查第 7 条的方法列出全部定义得到；随后 `validate_preloads(...)`；把 `SkillsMiddleware` 按 05 §5.2 第 5 位接进链（前置检查第 8 条的位置）。

### 4.3 接口

路由 `maa_api/api/routers/skills.py`，`APIRouter(prefix="/api/agent/skills", tags=["agent"])`，挂 `require_auth`，`response_model=` 用 §4.2 的模型。

| 方法与路径 | 函数名 | 请求 | 响应 | 错误 |
|---|---|---|---|---|
| GET `/api/agent/skills` | `list_skills` | — | 200 `SkillList {items: [SkillSummary]}` | — |
| GET `/api/agent/skills/{skill_id}` | `get_skill` | — | 200 `SkillDetail` | 404 `SKILL_NOT_FOUND` |
| PUT `/api/agent/skills/{skill_id}` | `put_skill` | `SkillWriteRequest`（继承 `SkillWrite`，`extra="forbid"`） | 200 `SkillDetail` | 400 `SKILL_INVALID` |
| DELETE `/api/agent/skills/{skill_id}` | `delete_skill` | — | 204 | 404 `SKILL_NOT_FOUND`、409 `SKILL_BUILTIN_READONLY` |

- 路径参数 `skill_id` 不在路由层加正则（否则不合法的 id 会变成 422）；交给服务层，GET/DELETE 不合法时报 404，PUT 报 400 `SKILL_INVALID`。
- 写操作各记一条服务日志（技能 id 与动作），不进 Agent 审计，与知识库同口径。
- 时间字段输出补 `Z`。

示例：

```json
GET /api/agent/skills/playbook-recruit
{"id":"playbook-recruit","title":"公开招募实操","description":"用户要你自动公开招募、……",
 "tools":["list_task_types","submit_pipeline","wait_pipeline","get_pipeline","get_screenshot","ask_user"],
 "agents":["general","daily_ops"],"source":"builtin","modified":false,"status":"active","enabled":true,
 "body_chars":1910,"files":[],"version":1,"updated_at":null,"body":"## ……",
 "builtin":{"id":"playbook-recruit","title":"公开招募实操","description":"……","tools":["…"],
            "agents":["general","daily_ops"],"body":"## ……","version":1}}
```

### 4.4 配置项

| 键 | 类型 | 默认 | 分组 | `_FIELDS` |
|---|---|---|---|---|
| `skills.enabled` | bool | true | Agent | `dict(label="启用技能", group="Agent", type="boolean", hot_action="none")` |

`config.template.yaml` 加：

```yaml
skills:
  # 关闭后 Agent 看不到技能索引，也不能加载技能
  enabled: true
```

`tests/test_settings.py` 的 `ENV_OVERRIDES` 加 `MAA_SKILLS_ENABLED`。

### 4.5 行为与算法

`SkillsMiddleware.on_run_start`：

```
if not settings.skills.enabled: ctx.state[STATE_KEY] = []; 第 4 段的 skills 小节置空; return
carried = 同一会话里上一个运行（started_at 最近、id 不等于本运行）的 meta.get("skills", [])
ctx.state[STATE_KEY] = [s for s in carried if 技能仍可用于本代理]
upto = 本会话最新 status='active' 的 compaction_checkpoint.upto_seq（没有为 -1）
sections = [render_index(index_for(spec.name))]
preloaded = [registry.load(i, agent_name=spec.name) for i in spec.skills，停用的跳过并记 debug]
sections += 「已预加载的技能」+ 每个技能「### 标题（id）」+ 正文
restore = [s for s in ctx.state[STATE_KEY] if s.loaded_seq <= upto and s.id 不在 preloaded 里]
sections += 「压缩前加载过的技能」+ 同样格式的正文
正文合计超过 MAX_INJECTED_BODY_CHARS 时，超出的技能只写「### 标题（id）：正文较长，需要时重新 load_skill」
用前置检查第 9 条的方法把 sections 写成第 4 段的 skills 小节
```

`SkillsMiddleware.wrap_tool`：

```
outcome = await call_next(call)            # 总是先执行，审计照常落库
if call.name != "load_skill" or outcome.status != "ok": return outcome
loaded = outcome.result
seq = 用 outcome.message_id 查 agent_message.seq（查不到用 2**31）
active = ctx.state[STATE_KEY]
if loaded.id 在 active 里且对应 loaded_seq > upto，或 loaded.id 在预加载里:
    outcome.result = {"id": loaded.id, "already_loaded": True,
                      "note": "这个技能已经加载过，正文在前面的结果或系统提示里"}
else:
    active 里有同 id 就更新 version 与 loaded_seq，否则追加
available = [t for t in loaded.tools if t 在 spec.tools 里]
groups = 这些工具里属于 spec.deferred_groups、当前没开的组
if groups: 用前置检查第 12 条的函数开启
outcome.result 加 "tools_available"、"tools_unavailable"、"enabled_groups"
return outcome
```

`SkillsMiddleware.on_run_end`：把 `ctx.state[STATE_KEY]` 写进本运行的 `agent_run.meta["skills"]`（按前置检查第 11 条的方式；M66 若没有「中间件状态随 finish 落库」的约定，就在 `on_run_end` 里读出本运行的 `meta`、合并 `skills` 键、整体赋值写回）。

知识库改动（`maa_api/agent/knowledge/__init__.py`）：

- 加常量 `MIGRATED_TO_SKILLS: frozenset[str]`（9 个 id），`__all__` 补上；`skills/registry.py` 从知识模块导入它，不另写一份。
- `read_for_agent` 与 `get_entry`：id 在 `MIGRATED_TO_SKILLS` 里、合并结果里又没有它时，报 `KNOWLEDGE_NOT_FOUND`，消息「「{id}」已改成技能，用 load_skill 加载」，`details={"entry_id": id, "skill_id": id}`。
- `put_entry`：id 在 `MIGRATED_TO_SKILLS` 里时报 `KNOWLEDGE_BUILTIN_READONLY`，消息「这个 id 已改成技能，到技能页修改」。
- 模块 docstring 里「索引（playbook / screen / rule）」改成「索引（screen / rule，以及用户自建的 playbook）」。

引用改写（12 处，2026-11-02 实测都以空格开头）：在 9 个技能正文与 3 条知识条目（`drop-efficiency`、`recruit-tags`、`screen-terminal`）正文里，把「空格 + 9 个 id 之一」改成「技能 + 空格 + id」，例如「见 playbook-recruit」→「见技能 playbook-recruit」、「走 playbook-drop-lookup 的流程」→「走技能 playbook-drop-lookup 的流程」。改完用 §5 T2 的命令核对剩余数为 0。

代码与提示词里的其他引用（前置检查第 13 条的清单）：

- `maa_api/agent/tools/account.py` 约 170 行的「（见 task-preconditions）」改成「（见技能 task-preconditions）」。
- `maa_api/agent/specs/builtin/*.md`（`legacy_chat.md` 除外）里出现的同样改写。
- `maa_api/agent/runtime.py` 的 `SYSTEM_PROMPT` 与 `legacy_chat.md` 不改（v2 原文）。
- 测试按 T6 处理。

代理定义（§4.7）：8 个文件的 `tools` 末尾加 `load_skill`、`read_skill_file`，`version` 加 1；`daily_ops`、`copilot` 改 `skills` 字段。

边界情况：

- `skills.enabled=false` → 第 4 段没有技能小节；`load_skill` 报「技能已在设置里关闭」；预加载也不注入。
- 同一轮里两次 `load_skill` 同一个技能 → 第二次结果是 `already_loaded`，正文不重复进历史。
- 技能被停用后，下一轮继承时把它从激活列表里去掉，第 4 段不再补它的正文。
- 用户覆盖改了预加载技能的正文 → 下一轮第 4 段用新正文（第 4 段在本轮内保持不变）。
- `general` 加载 `playbook-promotion-planning`，`get_depot` 所在的 `account` 组还没开 → 自动开启，结果里 `enabled_groups=["account"]`。
- `planner` 加载 `playbook-infrast-shift`，`submit_pipeline` 不在它的工具集里 → 列进 `tools_unavailable`，不加工具。
- `read_skill_file("playbook-recruit", "../SKILL.md")` → `SKILL_NOT_FOUND`，`details.reason="路径不合法"`。
- `PUT /api/agent/skills/playbook-recruit` 的 `tools` 含 `nope` → 400 `SKILL_INVALID`，`details.unknown=["nope"]`。
- `DELETE /api/agent/skills/playbook-recruit` 没有覆盖 → 409 `SKILL_BUILTIN_READONLY`；有覆盖 → 204，恢复默认。
- `PUT /api/agent/knowledge/playbook-recruit` → 409 `KNOWLEDGE_BUILTIN_READONLY`。
- 代理定义预加载了一个不对它开放的技能 → 启动失败，报错写明代理名与技能 id。

### 4.6 前端

文件：

| 文件 | 内容 |
|---|---|
| `web/src/features/skills/api.ts` | 列表、详情、保存、删除、启用开关；query key `[...keys.agent.all(), 'skills', 'list']`、`[...keys.agent.all(), 'skills', 'detail', id]` |
| `web/src/features/skills/format.ts` | 来源徽标、表单与请求体互转、代理名显示（用 `GET /api/agent/specs` 的 `title`） |
| `web/src/features/skills/SkillsPage.tsx` | 列表页 |
| `web/src/features/skills/SkillEditorPage.tsx` | 新建与编辑页 |
| `web/src/features/skills/skills.test.tsx` | 测试 |
| `web/src/routes/skills/index.tsx`、`web/src/routes/skills/editor.tsx` | 路由入口（照 `routes/settings/knowledge-editor.tsx` 的写法） |
| `web/src/routes/router.tsx` | 加 `more/skills`、`more/skills/new`、`more/skills/:skillId` |
| `web/src/components/layout/nav.ts` | `secondaryNav` 在「作业」之后加 `{ to: '/more/skills', label: '技能', icon: BookMarked, description: 'Agent 按需加载的做事方法' }` |

列表页：

- 分区「技能」，hint「Agent 平时只看到技能的名字和说明，用到时才读全文。」，右上角按钮「新建」。
- 按来源分组：「内置」「自定义」「草稿」（`status='draft'`，M95 起才会有）。每行：标题、`id`、徽标（内置 / 已修改 / 自定义 / 草稿）、可用代理（代理定义的 `title` 用「、」连接，空列表写「所有代理」）、启用开关（`aria-label`「启用「{title}」」）。
- 开关成功的 Toast：「已启用「{title}」」「已停用「{title}」」；失败：「启用「{title}」失败：{原因}」「停用「{title}」失败：{原因}」。
- 空状态：「还没有技能」/「内置技能随服务发布，也可以自己新建」；读取失败：ErrorState「技能读取失败」；加载中：「正在读取技能…」。

编辑页：

- 字段：「id」（只在新建时可填，说明「小写字母、数字与连字符，2–48 个字符」）、「标题」（≤32）、「什么时候用」（≤200，对应 `description`）、「可用代理」（复选框，选项来自 `GET /api/agent/specs` 里 `kind` 为 `orchestrator` 或 `worker` 的定义；全不选表示所有代理）、「依赖的工具」（多行输入，每行一个工具名）、「正文」（Markdown，显示「{n}/20000」）、「启用」开关。
- 按钮：「保存」（Toast「已保存技能」）；内置且已修改时「恢复默认」（确认弹窗标题「恢复默认？」，描述「你的修改会被丢弃，换回内置内容。」，Toast「已恢复默认」）；自定义技能「删除技能」（确认弹窗标题「删除技能？」，描述「删除后 Agent 不能再加载它，无法撤销。」，Toast「已删除技能」）。
- 400 `SKILL_INVALID` 时把 `details.field` 对应字段标错，`details.unknown` 拼进提示：「这些工具不存在：nope」「这些代理不能用技能：researcher」。
- 内置技能的编辑页在正文下方折叠显示「内置原文」，对比用。

`web/src/features/settings/knowledge/KnowledgeSection.tsx` 的分区 hint 改为「Agent 每轮都会带上这份索引，正文在需要时读取。做事步骤已移到技能页。」

### 4.7 提示词与代理定义

8 个文件（`orchestrator`、`general`、`daily_ops`、`copilot`、`planner`、`diagnose`、`analyst`、`ops`）：`tools` 末尾加 `load_skill`、`read_skill_file`；`version` 加 1；正文末尾加一行：「系统提示里有技能索引。请求对得上某个技能时，先用 load_skill 加载，再按技能的步骤做；技能里提到你没有的工具时，把那一步交给能做的执行者或告诉用户。」

`daily_ops.md` 的 frontmatter 改 `skills: [task-preconditions]`；`copilot.md` 改 `skills: [task-preconditions, playbook-stage-clear]`。

## 5. 实施任务

### T1 枚举、表与迁移

- 写范围：`maa_api/domain/enums.py`、`tests/domain/test_enums.py`、`maa_api/db/models.py`、`maa_api/db/migrations/versions/0031_skills.py`、`tests/db/test_models.py`、`tests/db/test_skills_migration.py`（新）
- 步骤：按 §4.1。
- 验证：`.venv/bin/python -m pytest -q tests/db tests/domain/test_enums.py > /tmp/m78-t1.log 2>&1; echo $?; tail -n 3 /tmp/m78-t1.log` → 0。
- 完成判据：§6 的迁移用例通过。

### T2 内置技能文件与知识库瘦身

- 写范围：`maa_api/agent/skills/builtin/*/SKILL.md`（9 个新文件）、`maa_api/agent/knowledge/builtin/` 里 9 个文件（删除）与 3 个文件（改引用）、`maa_api/agent/knowledge/__init__.py`
- 步骤：
  1. 对每个映射行：新建 `builtin/<id>/SKILL.md`，frontmatter 按 §4.2，正文用 `git show HEAD:maa_api/agent/knowledge/builtin/<id>.md` 的 `---` 之后全文。
  2. `git rm` 9 个旧文件。
  3. 按 §4.5 改 12 处引用。
  4. 按 §4.5 改 `knowledge/__init__.py`。
- 验证：
  ```bash
  ls maa_api/agent/knowledge/builtin/*.md | wc -l
  ls maa_api/agent/skills/builtin/*/SKILL.md | wc -l
  rg -P -o '(?<!id: )(?<!技能 )(?<![a-z0-9-])(account-resources|playbook-daily-weekly|playbook-drop-lookup|playbook-infrast-shift|playbook-promotion-planning|playbook-recruit|playbook-sanity-planning|playbook-stage-clear|task-preconditions)(?![a-z0-9-])' maa_api/agent/knowledge/builtin maa_api/agent/skills/builtin | rg -v '/SKILL.md:id: ' | wc -l
  ```
  依次输出 `25`、`9`、`0`。
- 完成判据：三条输出符合。

### T3 技能注册表与仓储

- 写范围：`maa_api/agent/skills/__init__.py`、`registry.py`、`maa_api/db/repositories/agent_skill.py`、`tests/agent/test_skill_registry.py`（新）
- 步骤：按 §4.2 实现。测试用 `tmp_path` 下的假技能目录注入 `builtins`（含一个带附属文件 `notes/checklist.md` 的技能），会话工厂用 `create_all` 的临时库。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_skill_registry.py > /tmp/m78-t3.log 2>&1; echo $?` → 0。
- 完成判据：真实内置目录 `load_builtin_skills()` 返回 9 个，`SkillRegistry(..., tool_names=注册表, agent_kinds=全部定义)` 构造不报错。

### T4 中间件、工具与确定性保存

- 写范围：`maa_api/agent/skills/middleware.py`、`maa_api/agent/tools/skills.py`、`maa_api/agent/tools/__init__.py`（注册顺序：放在知识工具之后）、`maa_api/agent/context/durable.py`、链的组装文件（前置检查第 8 条）、`tests/agent/test_tool_catalog.py`、`tests/agent/test_skills_middleware.py`（新）、`tests/agent/test_skills_tools.py`（新）
- 步骤：按 §4.2、§4.5 实现；`EXPECTED_TOOLS` 加两个工具，计数各加 2（两个都对模型可见）。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_skills_middleware.py tests/agent/test_skills_tools.py tests/agent/test_tool_catalog.py > /tmp/m78-t4.log 2>&1; echo $?` → 0。
- 完成判据：§6 里中间件与工具的用例通过；测试用 `FakeLLM` 驱动 `AgentLoop`，不连真实模型。

### T5 REST、错误码、设置与装配

- 写范围：`maa_api/domain/errors.py`、`maa_api/api/routers/skills.py`、`maa_api/main.py`、`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml`、`tests/test_settings.py`、`docs/v3/14-现行接口清单.md`、`tests/domain/test_errors.py`、`tests/api/test_skills_router.py`（新）、`web/openapi.json`、`web/src/types/api.d.ts`
- 步骤：
  1. `ErrorCode` 加 `SKILL_NOT_FOUND`（没有这个技能，404）、`SKILL_BUILTIN_READONLY`（内置技能没有覆盖可删，409）、`SKILL_INVALID`（技能内容不合法，400），各带中文注释；`ALL_CODE_COUNT`、`HTTP_BOUND_CODE_COUNT` 各加 3；14 号文档 §4 加三行、§6 agent 小节加四条路由，改计数句。
  2. 按 §4.3 写路由，`include_router`；按 §4.4 加设置；按 §4.2 装配 `skill_registry`、`validate_preloads`、中间件。
  3. `.venv/bin/python scripts/dump_openapi.py`，`cd web && pnpm gen:api`。
- 验证：`.venv/bin/python -m pytest -q tests/api/test_skills_router.py tests/domain tests/test_settings.py tests/test_vapid_settings.py > /tmp/m78-t5.log 2>&1; echo $?` → 0；`.venv/bin/python scripts/dump_openapi.py --check; echo $?` → 0。
- 完成判据：真实 lifespan 下 `GET /api/agent/skills` 返回 9 项。

### T6 改受影响的测试与引用

- 写范围：前置检查第 13 条列出的测试文件与 `maa_api` 文件（只改与 9 个 id 有关的部分）、`maa_api/agent/specs/builtin/` 的 8 个文件
- 步骤：逐个按下表改；表里没有、但在第 13 条清单里的文件，按同一原则改（断言内置知识条目的，去掉这 9 个 id；断言这 9 条内容的，改成读技能）并在台账列出。

| 文件 | 改法 |
|---|---|
| `tests/agent/test_knowledge_service.py` | `BUILTIN_IDS`、`BUILTIN_LIST_ORDER` 去掉 9 个 id（剩 25）；加 `MIGRATED_TO_SKILLS` 常量（9 个 id）；`test_builtin_files_match_the_spec_registry_and_frontmatter` 里先算 `all_blocks = _spec_blocks()`，断言 `set(all_blocks) - set(MIGRATED_TO_SKILLS)` 等于目录里的文件集合、`set(MIGRATED_TO_SKILLS) <= set(all_blocks)`，其余比对只对剩下的块做；新增 §6 列出的两条用例 |
| `tests/api/test_agent_knowledge_router.py` | `BUILTIN_IDS`、`BUILTIN_LIST_ORDER` 去掉 9 个 id；`test_knowledge_detail_includes_the_body_and_the_builtin_snapshot` 改读 `screen-home`：`kind=="screen"`、`title=="首页布局"`、`body.startswith("位置用占截图宽、高的比例表示")`、`builtin.id=="screen-home"` |
| `tests/agent/test_knowledge_no_numbers.py` | `EXPECTED_ENTRY_COUNT = 25`，注释写「34 条去掉 M78 迁成技能的 9 条」；`scan_entries()` 同时扫 `builtin_skills()` 的正文，键写 `skill:<id>`；`test_builtin_entries_are_scannable` 另断言技能 9 个且正文非空；`test_whitelist_rules_actually_exempt_something_in_the_builtins` 的样本加上技能正文与 `description` |
| `tests/services/test_knowledge_page_pointers.py` | `_bodies()` 返回知识条目 25 篇加技能 9 篇（技能键写 `skill:<id>`），断言合计 34；其余不变 |
| `tests/agent/test_runtime.py`（若 `test_system_prompt_says_copilot_execution_needs_the_stage_in_the_kernel_resource` 还在） | `builtin_entries()["playbook-stage-clear"].body` 改成 `builtin_skills()["playbook-stage-clear"].body`；三条断言不变 |
| `tests/agent/test_account_resources_flow.py`（若还在） | 剧本里的 `read_knowledge(entry_id="account-resources")` 改成 `load_skill(skill_id="account-resources")`；系统消息断言改查技能索引行「- account-resources｜查询账号资源：」；被测代理改用有这个技能的 `general` 或 `daily_ops` |
| `tests/agent/test_incidents.py`（若相关用例还在） | `_read_knowledge_response("account-resources")` 改成 `_read_knowledge_response("screen-home")`，索引断言改成「- screen-home｜首页布局」 |
| `web/src/features/settings/knowledge/*.test.*` | 用的是桩数据，不改 |

- 验证：`.venv/bin/python -m pytest -q tests/agent tests/api tests/services > /tmp/m78-t6.log 2>&1; echo $?; tail -n 5 /tmp/m78-t6.log` → 0。
- 完成判据：第 13 条清单里每个文件都有处理结论（改了什么或为什么不改），写进台账。

### T7 前端

- 写范围：§4.6 表里的文件、`web/src/features/settings/knowledge/KnowledgeSection.tsx`、`web/src/components/layout/layout.test.tsx`（只在它枚举了全部次级入口时补「技能」）
- 步骤：按 §4.6。
- 验证：`cd web && pnpm install --frozen-lockfile > /tmp/m78-pnpm.log 2>&1 && pnpm gen:api:check && pnpm typecheck > /tmp/m78-tc.log 2>&1 && pnpm test --run > /tmp/m78-vitest.log 2>&1 && pnpm build > /tmp/m78-build.log 2>&1; echo $?; cd ..` → 0；`.venv/bin/python scripts/frontend_smoke.py > /tmp/m78-fs.log 2>&1; tail -n 1 /tmp/m78-fs.log` → `SMOKE OK`。

### T8 评测与文档

- 写范围：`tests/evals/scenarios/*.yaml`（只改引用 9 个 id 的 `read_knowledge` 步骤）、§9 的文档
- 步骤：`rg -n -l 'account-resources\|playbook-\|task-preconditions' tests/evals`，把剧本里 `read_knowledge` 这 9 个 id 的步骤改成 `load_skill`；录制的 cassette（真实模型输出）里有这类调用时按 §10 停下来问。跑 §7。

## 6. 测试清单

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/db/test_skills_migration.py` | `test_upgrade_moves_playbook_overrides_into_skills` | 升级前造 4 行：`playbook-recruit` 覆盖（改了标题）、`task-preconditions` 只停用、`screen-home` 覆盖、自定义 `my-playbook`；升级后 `agent_skill` 两行（`origin='override'`，`description` 来自 `when_to_use`，停用状态保留），`agent_knowledge` 只剩 `screen-home` 与 `my-playbook` |
| 同上 | `test_downgrade_restores_overrides_and_drops_table` | 降级后两行回到 `agent_knowledge`（`kind` 为 NULL），`agent_skill` 不存在 |
| 同上 | `test_upgrade_on_empty_database` | 空库升级成功，`agent_skill` 为空 |
| `tests/agent/test_skill_registry.py` | `test_builtin_loader_reads_nine_skills_with_exact_frontmatter` | 9 个 id 与映射表一致；frontmatter 恰好 6 个字段；`agents`、`tools` 与表一致 |
| 同上 | `test_builtin_loader_fails_loud` | 目录名与 id 不符、多余字段、缺字段、超长标题、重复 id 都抛 `SkillBuiltinError` |
| 同上 | `test_registry_rejects_unknown_tools_or_subagent_agents` | 内置技能引用不存在的工具或 `researcher` 时构造失败 |
| 同上 | `test_override_merge_and_restore_default` | 改标题只存标题列；改回原值且启用时删行；`modified` 正确 |
| 同上 | `test_put_user_skill_and_validation_errors` | 新 id 存成 `origin='user'`；各字段越界、未知工具、子代理名都报 `SKILL_INVALID` 且 `details` 对得上 |
| 同上 | `test_delete_rules` | 内置无覆盖 409；内置有覆盖删行；用户技能删除；未知 404 |
| 同上 | `test_index_for_filters_agents_status_and_switch` | `planner` 只看到 `agents` 含它或为空的技能；停用与草稿不出现；`skills.enabled=false` 时为空 |
| 同上 | `test_load_errors_have_reasons` | 停用、不对该代理开放、设置关闭三种消息不同 |
| 同上 | `test_read_file_guards_paths` | 合法附属文件能读；`..`、绝对路径、软链接逃逸、超过 64 KiB、用户技能都报 `SKILL_NOT_FOUND` |
| 同上 | `test_validate_preloads` | 预加载不存在或不开放的技能时抛错，消息含代理名与技能 id |
| 同上 | `test_orphan_override_row_is_skipped` | 内置里没有的覆盖行被跳过，列表照常返回 |
| `tests/agent/test_skills_middleware.py` | `test_index_section_lists_available_skills` | 第 4 段 skills 小节含 `daily_ops` 可用的技能行，不含 `planner` 专属的 |
| 同上 | `test_preloaded_body_is_injected_for_daily_ops` | 第 4 段含 `task-preconditions` 正文 |
| 同上 | `test_load_skill_records_activation_and_carries_over` | 第一轮加载后 `agent_run.meta.skills` 有它；第二轮 `ctx.state` 继承 |
| 同上 | `test_compacted_load_is_restored_into_section` | 造一个 `upto_seq` 大于加载消息 seq 的检查点后，第 4 段补回正文 |
| 同上 | `test_second_load_returns_already_loaded` | 同一轮第二次加载的结果是 `already_loaded`，审计仍有两条 |
| 同上 | `test_general_enables_deferred_group` | `general` 加载 `playbook-promotion-planning` 后 `account` 组开启，结果带 `enabled_groups` |
| 同上 | `test_fixed_toolset_reports_unavailable_tools` | `planner` 加载 `playbook-infrast-shift`，`submit_pipeline` 在 `tools_unavailable` 里，工具声明不变 |
| 同上 | `test_skills_disabled_injects_nothing` | 设置关闭时没有 skills 小节 |
| 同上 | `test_durable_context_includes_active_skills` | 压缩前收集的 `durable["skills"]` 有已激活技能 |
| `tests/agent/test_skills_tools.py` | `test_load_skill_returns_body_tools_files` | 返回正文、工具、附属文件清单与 12 位版本 |
| 同上 | `test_tools_are_hidden_from_rest` | `GET /api/agent/tools` 没有这两个工具；REST 调用返回 `TOOL_NOT_FOUND` |
| `tests/api/test_skills_router.py` | `test_list_and_detail_shapes` | 列表 9 项；详情含 `builtin` 原文；时间带 `Z` |
| 同上 | `test_put_and_delete_round_trip` | 覆盖、恢复默认、新建、删除的状态码与响应 |
| 同上 | `test_errors` | 400 `SKILL_INVALID`、404 `SKILL_NOT_FOUND`、409 `SKILL_BUILTIN_READONLY`；未知字段 422 |
| 同上 | `test_routes_require_auth` | 不带 token 时 401 |
| `tests/agent/test_knowledge_service.py` | `test_migrated_ids_redirect_to_load_skill` | `read_for_agent("playbook-recruit")` 报 `KNOWLEDGE_NOT_FOUND`，`details.skill_id=="playbook-recruit"` |
| 同上 | `test_put_entry_rejects_migrated_ids` | `put_entry("task-preconditions", …)` 报 `KNOWLEDGE_BUILTIN_READONLY` |
| `web/src/features/skills/skills.test.tsx` | `lists builtin skills with agents and switch` | 分组、徽标、代理显示「执行官、通用执行」 |
| 同上 | `toggle puts the whole skill back` | 开关先 GET 详情再 PUT 完整内容，Toast 正确 |
| 同上 | `editor saves, restores default and deletes` | 三个按钮的请求与确认弹窗文案 |
| 同上 | `editor marks invalid fields from SKILL_INVALID` | `details.field="tools"` 时工具框标错并显示「这些工具不存在：nope」 |

## 7. 验收

照 [12 §4](../12-实施路线与里程碑.md)：

```bash
.venv/bin/python -m pytest -q > /tmp/m78-pytest.log 2>&1; echo $?; tail -n 30 /tmp/m78-pytest.log
.venv/bin/python scripts/dump_openapi.py --check; echo $?
cd web && pnpm install --frozen-lockfile > /tmp/m78-pnpm.log 2>&1 && pnpm gen:api:check && pnpm typecheck > /tmp/m78-tc.log 2>&1 && pnpm test --run > /tmp/m78-vitest.log 2>&1 && pnpm build > /tmp/m78-build.log 2>&1; echo $?; cd ..
.venv/bin/python scripts/frontend_smoke.py > /tmp/m78-fs.log 2>&1; tail -n 1 /tmp/m78-fs.log
.venv/bin/python scripts/agent_smoke.py > /tmp/m78-as.log 2>&1; tail -n 1 /tmp/m78-as.log
.venv/bin/python scripts/orchestrator_smoke.py > /tmp/m78-os.log 2>&1; tail -n 1 /tmp/m78-os.log
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m78-eval-r.log 2>&1; tail -n 5 /tmp/m78-eval-r.log
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m78-eval-c.log 2>&1; tail -n 5 /tmp/m78-eval-c.log
.venv/bin/python scripts/check_doc_links.py | tail -n 1
git diff --check; echo $?
```

期望：pytest 不新增失败；openapi 与前端退出码 0；三个冒烟 `SMOKE OK`；回归评测全部通过；能力评测不低于台账基线（8 个代理定义改了）；`BROKEN 0`；`git diff --check` 退出码 0。

手动验收（补充）：

1. 在旧库上（M77 的库，`playbook-recruit` 改过标题）升级到本里程碑，技能页里 `playbook-recruit` 显示「已修改」、标题是改过的那个；知识库页里没有这 9 条。
2. 在指挥台让总控建一个「公开招募」工单，活动树里 Worker 先调 `load_skill(playbook-recruit)` 再提交流水线。
3. 在技能页停用 `playbook-recruit`，新工单的 Worker 系统提示里不再有它的索引行。

## 8. 风险与回滚

- 模型不主动加载技能：提示词与工具描述都写了「对得上就先加载」；能力评测会暴露，降了就调整 `description` 的写法。
- 第 4 段因预加载变长：`daily_ops` 多约 1200 字、`copilot` 多约 4900 字，前缀缓存仍按工单稳定；`MAX_INJECTED_BODY_CHARS` 兜底。
- 9 个 id 的旧调用（回放里的 `read_knowledge`）拿到「已改成技能」的提示，模型会改用 `load_skill`；录制回放若因此失败按 §10 处理。
- 回滚：`alembic downgrade <0031 的上一版>` 把 9 条覆盖行还给知识库（用户技能丢失，降级前先导出 `GET /api/agent/skills` 的自定义项），再 `git revert -m 1 <merge>`（9 个知识文件随之恢复）。

## 9. 文档同步

- [07 §4](../07-知识RAG与记忆.md)：与实现不一致处同步。
- [05 §3.2](../05-运行时与代理定义.md)：总控与 7 个 Worker 的工具清单加 `load_skill`、`read_skill_file`；`daily_ops`、`copilot` 的预加载技能。
- `docs/v3/14-现行接口清单.md`：错误码 +3、路由 +4。
- `config.template.yaml`：`skills` 段。
- 计数：工具 +2（都对模型可见）、端点 +4、错误码 +3、业务表 +1、配置键 +1、内置知识条目 34 → 25、内置技能 0 → 9。

## 10. 停下来问

- 前置检查第 9、11、12 条找不到对应接口（第 4 段写入、运行 `meta` 落库、开启工具组）。
- 评测里录制的 cassette（真实模型输出）依赖这 9 个 id 的 `read_knowledge`，需要重新录制。
- 想把技能放进检索索引，或想迁移用户自建的 `playbook` 条目。
- 想改 `legacy_chat.md` 或 `SYSTEM_PROMPT`。
- 迁移 0031 在真实库上搬出的行数与 `SELECT count(*) FROM agent_knowledge WHERE id IN (9 个 id)` 不一致。

## 需要补进契约的名字

| 名字 | 用途 | 建议补到 |
|---|---|---|
| `maa_api/agent/skills/middleware.py`（`SkillsMiddleware` 的位置） | 中间件文件 | 02 §4.1 的 `skills/` |
| `BuiltinSkill`、`load_builtin_skills`、`builtin_skills`、`SkillBuiltinError`、`SkillSummary`、`SkillDetail`、`SkillBuiltinView`、`SkillWrite`、`SkillWriteRequest`、`SkillList`、`SkillIndexEntry`、`LoadedSkill` | 注册表的类型与 REST 模型 | 02 §4.1、04 §2.3 |
| `SkillRegistry.validate_preloads`、`index_for`、`load`、`read_file`、`render_index` | 注册表方法 | 02 §2.2 |
| `maa_api/db/repositories/agent_skill.py`（`AgentSkillRepository`）、模型类 `AgentSkill` | 仓储与模型 | 02 §4.1、03 §10.1 |
| `SkillsSettings`（`Settings.skills`） | 设置段 | 04 §6 |
| `MIGRATED_TO_SKILLS` | 9 个迁走的知识 id | 07 §4.3 |
| `ctx.state["skills.active"]`、`agent_run.meta["skills"]` 的项形状 `{"id", "version", "loaded_seq"}`、`durable["skills"]` 的项形状 `{"id", "title", "version"}` | 激活状态的存放与压缩保存 | 03 §3.2 `meta` 列说明、06 的确定性保存一节 |
| `MAX_INJECTED_BODY_CHARS`、`SKILL_FILE_PATTERN`、`MAX_SKILL_FILE_BYTES` | 正文注入上限与附属文件规则 | 07 §4 |
| 前端路由 `/more/skills`、`/more/skills/new`、`/more/skills/:skillId`；`web/src/features/skills/` | 技能页 | 11 的页面清单 |
| 05 §3.2 工具清单的改动 | 总控与 7 个 Worker 加 `load_skill`、`read_skill_file`；`daily_ops`、`copilot` 的 `skills` | 05 §3.2 |
