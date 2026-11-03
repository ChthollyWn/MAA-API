# M77 语料扩充（规格）

> 阶段：P2　依赖：M76　分支：`corpora`　迁移：无
> 本文是 M77 的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在新分支上，工作区干净 | `git status -sb` | `## corpora`，没有改动 |
| 2 | M76 已合并 | `git tag -l 'v3-m76'` | 输出 `v3-m76` |
| 3 | 来源协议与索引服务 | `rg -n 'class CorpusSource\|class KnowledgeIndexService\|def register\|def push_documents\|def refresh_all' maa_api/agent/knowledge/index/indexer.py` | 前三项各一行；后两项没有（本里程碑加） |
| 4 | 同步是「取一批、写一批」 | `rg -n 'await source.fetch' maa_api/agent/knowledge/index/indexer.py` | 一行，位于按 20 个一组的循环里（M76 §4.5）；不是的话先停下报告 |
| 5 | `setting_key` 已参与检索过滤 | `rg -n 'setting_key' maa_api/agent/knowledge/index/retriever.py maa_api/agent/knowledge/index/indexer.py` | 至少一行 |
| 6 | 检索设置段 | `rg -n 'class RagSettings' maa_api/settings.py` | 一行 |
| 7 | 四个外部客户端 | `rg -n '^class (ExternalClient\|PrtsClient\|GamedataClient\|CopilotSiteClient)\b' maa_api/services/external` | 四行 |
| 8 | 全应用唯一的调度器 | `rg -n 'update_scheduler = AsyncIOScheduler\(\)\|app.state.update_scheduler = update_scheduler' maa_api/main.py` | 两行 |
| 9 | 页面服务与清洗函数 | `rg -n 'def default_page_service\|^def _clean_text\|^def make_page_url' maa_api/services/game/pages.py` | 三行 |
| 10 | 职业与位置的中文名 | `rg -n '^_PROFESSION_CN\|^_POSITION_CN' maa_api/services/game/operators.py` | 两行 |
| 11 | 当期活动服务 | `rg -n 'def default_activity_service\|async def current' maa_api/services/game/activities.py` | 两行 |
| 12 | 工具描述位置 | `rg -n 'name="search_knowledge"' maa_api/agent/tools/knowledge.py` | 一行 |
| 13 | 基线测试 | `.venv/bin/python -m pytest -q > /tmp/m77-base.log 2>&1; echo $?; tail -n 3 /tmp/m77-base.log` | 记下失败数 |

## 1. 背景与目标

M76 只索引了内置与用户条目。本里程碑把 07 §3 的四个外部语料接进检索层，内容只存在本机库里，不进仓库（V3-D43）。

完成后：

1. `maa-docs`、`prts`、`gamedata`、`copilot-site` 四个来源出现在 `GET /api/agent/retrieval/sources`，各自带许可说明与开关设置状态。
2. 全应用唯一的 `AsyncIOScheduler` 上有一个 `maa-rag-refresh` 定时任务，按 `rag.refresh_cron`（默认 `0 4 * * *`）依次刷新外部语料并补向量；从没建过索引的语料在启动 120 秒后先同步一次。
3. 增量：来源侧版本没变的文档不下载；正文 `content_hash` 没变的不重建分块；上游删掉的文档随同步删除。
4. `get_prts_page` 成功读到的页面整页进 `prts` 来源，最多 500 页。
5. 四个语料各有开关设置项；关闭后不抓取、不参与检索。
6. PRTS 的许可与抓取约束经过核对并记进台账（13 Q9）。

## 2. 非目标

- 不加新端点、新错误码、新表；不做来源配置的前端编辑器（`config` 只能经 `PUT /api/agent/retrieval/sources/{id}` 改）。
- 拆包数据不取 `stage_table.json`（约 19 MB）与 `skill_table.json`（约 11 MB），卡片不写属性数值。
- 作业站不索引 `actions`，不按浏览量或好评率排序。
- 不支持 GitHub token；限额用完就等下次。
- `history`、`memory` 来源是 M79。

## 3. 相关文档

- 契约：[07 §3](../07-知识RAG与记忆.md)；[04 §6](../04-接口与事件协议.md) 的 M77 两行；[13](../13-决策记录.md) 的 V3-D43 与 Q9；[02 §4.1](../02-系统架构.md) 的 `knowledge/sources/`。
- v2：`rg -n '^### 3.6' docs/v2/superpowers/specs/2026-10-09-m25-prts-knowledge.md`（第 5 条「外部内容不落盘」被 V3-D43 推翻，日志里仍不记外部正文）；`rg -n '^### 6.6' docs/v2/11-Agent模块设计.md`（实时检索层，不变）。
- 代码入口：
  - `maa_api/services/external/sources.py`：`SourceSpec`（约 59）、`SOURCES`（约 263）、`resolve_source`（约 418）、`gamedata_mirror_origins`（约 498）、`_PATH_SEGMENT`（约 55）。
  - `maa_api/services/external/http.py`：`ExternalClient.get_json`（约 166）、`_fetch`（约 240）。
  - `maa_api/services/external/gamedata.py`：`GamedataClient._fetch`（约 205）、`validate_excel_name`（约 159）、`GamedataData`。
  - `maa_api/services/external/prts.py`：`PrtsClient.wikitext`（约 365，一次最多 20 个标题）、`WikiPage`（约 138）、`validate_title`（约 181）。
  - `maa_api/services/external/copilot_site.py`：`CopilotSiteClient.query`（约 173）、`CopilotRow`、`copilot_link`、`parse_content`。
  - `maa_api/services/game/pages.py`：`PageService.page`（约 192）、`_load`（约 224）、`_clean_text`（约 375）、`make_page_url`（约 385）、`default_page_service`（约 516）。
  - `maa_api/services/game/activities.py`：`ActivityService.current(detail="full")`、`StageEntry.stage_id`。
  - `maa_api/services/game/operators.py`：`_PROFESSION_CN`、`_POSITION_CN`（约 425）。
  - `maa_api/main.py`：调度器创建与任务注册（约 1266–1332）。

## 4. 设计

### 4.1 数据模型

不加表也不加迁移。四个来源的 `knowledge_source` 行由 M76 的 `ensure_source` 在启动时补齐。各来源的文档约定：

| 来源 | `external_id` | `title` | `url` | `version` | `meta` |
|---|---|---|---|---|---|
| `maa-docs` | `docs/zh-cn` 下的相对路径，如 `manual/faq.md` | frontmatter 的 `title`，没有就取第一个 `# ` 标题，再没有取文件名 | `https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/<ref>/docs/zh-cn/<路径>` | blob sha | `{"path", "ref"}` |
| `prts` | 页面标题（重定向后的） | 同左 | `make_page_url(title)` | 修订号字符串 | `{"origin": "seed" \| "read", "revision", "redirect_from", "read_at"?}` |
| `gamedata` | `char:<char_id>`、`item:<item_id>`、`enemy:<enemy_id>` | `干员 <名>`、`道具 <名>`、`敌人 <名>` | `make_page_url(<名>)` | 卡片正文 sha256 前 16 位 | `{"table", "entity_id"}` |
| `copilot-site` | `copilot:<site_id>` | 作业标题，没有就写 `作业 #<site_id>` | `copilot_link(site_id)` | 卡片正文 sha256 前 16 位 | `{"stage_id", "site_id", "views", "rating_ratio", "upload_time"}` |

各来源的 `config`（经 `validate_config` 校验，缺的键用默认值补齐后整体保存）：

| 来源 | 键与默认值 | 校验 |
|---|---|---|
| `maa-docs` | `ref="dev-v2"`、`path="docs/zh-cn"` | `ref` 匹配 `^[A-Za-z0-9._/-]{1,64}$`；`path` 匹配 `^docs/[a-z]{2}-[a-z]{2}$` |
| `prts` | `seed_titles=DEFAULT_SEED_TITLES`、`max_pages=500`、`refresh_read_pages=40` | 种子不超过 50 个、每个过 `validate_title`；`max_pages` 50–2000；`refresh_read_pages` 0–200 |
| `gamedata` | `tables=["character", "item", "enemy"]` | 非空子集 |
| `copilot-site` | `extra_stage_ids=[]`、`per_stage_limit=10` | 关卡 id 不超过 30 个、每个匹配 `^[A-Za-z0-9_#\-/]{1,64}$`；`per_stage_limit` 1–20 |

不合法时抛 `VALIDATION_ERROR`，`details` 写 `{"field", "reason"}`。

### 4.2 模块与文件

外部访问层（v2 代码，只加不改语义）：

`maa_api/services/external/sources.py` 加两个来源登记与一个常量：

```python
"maa_docs": SourceSpec(
    kind="maa_docs",
    label="MAA 文档",
    hosts=("api.github.com",),
    paths={
        "docs_dir": "/repos/MaaAssistantArknights/MaaAssistantArknights/contents/docs",
        "tree": "/repos/MaaAssistantArknights/MaaAssistantArknights/git/trees/{tree_sha}",
        "blob": "/repos/MaaAssistantArknights/MaaAssistantArknights/git/blobs/{blob_sha}",
    },
    params=frozenset({"ref", "recursive", "tree_sha", "blob_sha"}),
    # 2026-11-02 实测 docs/zh-cn 的递归目录树约 15 KB、最大文件 56 KB（base64 后约 76 KB）
    max_bytes=2 * 1024 * 1024,
    connect_timeout=5.0,
    read_timeout=20.0,
    rate_per_minute=20,
    concurrency=2,
    cache_level=CACHE_STATIC,
    license="MAA 文档按 AGPL-3.0 发布（MaaAssistantArknights）",
),
"gamedata_corpus": SourceSpec(
    kind="gamedata_corpus",
    label="拆包数据（语料）",
    hosts=("fastly.jsdelivr.net", "cdn.jsdelivr.net"),
    paths={"excel": "/zh_CN/gamedata/excel"},
    params=frozenset(),
    # 2026-11-02 实测 character_table.json 为 15.5 MB
    max_bytes=24 * 1024 * 1024,
    connect_timeout=5.0,
    read_timeout=60.0,
    rate_per_minute=4,
    concurrency=1,
    cache_level=CACHE_LEVEL,
    license="拆包数据来自 Kengxxiao/ArknightsGameData，版权属于鹰角网络",
),

#: 语料用的三张 excel 表（M77）。只给语料刷新用，工具仍走 GAMEDATA_FILES。
GAMEDATA_CORPUS_FILES: frozenset[str] = frozenset(
    {"character_table.json", "item_table.json", "enemy_handbook_table.json"}
)
```

`resolve_source`：`maa_docs` 只受 `sources.enabled` 管，限流取登记表默认值；`gamedata_corpus` 并进 `kind in {"gamedata", "gamedata_github", "gamedata_corpus"}` 两处分支（受 `sources.gamedata_enabled` 管，限流取 `sources.gamedata_rate_per_minute`）。`__all__` 补 `GAMEDATA_CORPUS_FILES`。

`maa_api/services/external/http.py`：`get_json` 加关键字参数 `cache: bool = True`。为 `False` 时不读缓存、不写缓存、不回退旧值；同键并发合并照旧。大表与目录树不能占用进程内缓存。

`maa_api/services/external/gamedata.py`：

```python
GAMEDATA_CORPUS_KIND = "gamedata_corpus"

def validate_corpus_table(name: str) -> str:
    """只允许 GAMEDATA_CORPUS_FILES 里的文件名，否则 INVALID_PARAMETER。"""

class GamedataClient:
    async def corpus_table(self, name: str) -> GamedataData:
        """语料用的大表：只走镜像（不走 GitHub 兜底），cache=False。"""
    async def _fetch(self, endpoint, relative_path, *, title, github_path="",
                     kind: str = GAMEDATA_KIND, cache: bool = True,
                     allow_github: bool = True) -> GamedataData: ...
```

`_fetch` 里的 `resolve_source`、`get_json` 都改用参数 `kind`，`get_json` 传 `cache=cache`；`allow_github=False` 时跳过 GitHub 兜底那一段。原有调用不传新参数，行为不变。

新文件 `maa_api/services/external/maa_docs.py`：

```python
MAA_DOCS_KIND = "maa_docs"
MAA_DOCS_REPO_URL = "https://github.com/MaaAssistantArknights/MaaAssistantArknights"

class MaaDocsClient:
    def __init__(self, client: ExternalClient | None = None) -> None: ...
    async def docs_dir(self, ref: str) -> list[dict[str, Any]]:
        """GET contents/docs?ref=<ref>，cache_level=live，cache=False；返回目录项列表，不是列表就抛 EXTERNAL_RESPONSE_INVALID。"""
    async def tree(self, tree_sha: str) -> dict[str, Any]:
        """GET git/trees/<sha>?recursive=1，cache=False；truncated 为真时抛 EXTERNAL_RESPONSE_INVALID（目录树被截断）。"""
    async def blob(self, blob_sha: str) -> str:
        """GET git/blobs/<sha>，cache=False；encoding 必须是 base64，解码成 UTF-8 文本。"""
```

`ExternalClient` 把 HTTP 403 映射成 `EXTERNAL_SOURCE_UNAVAILABLE`（`details.status=403`）。`MaaDocsClient` 遇到它时改抛同码、消息「GitHub API 本小时的匿名额度已用完，下次刷新时继续」，`details` 保留 `status`。

页面服务与干员服务（v2 代码，只加）：

- `maa_api/services/game/pages.py`：加公开函数 `clean_page_text(content: str) -> str`（直接调 `_clean_text`）；`PageService` 加 `add_listener(listener: Callable[[WikiPage, datetime], Awaitable[None]]) -> None`，`page()` 在 `_load` 成功后逐个 `await listener(raw.page, raw.batch.result.fetched_at)`，监听器抛异常只记 debug，不影响返回。
- `maa_api/services/game/operators.py`：加公开别名 `PROFESSION_CN = _PROFESSION_CN`、`POSITION_CN = _POSITION_CN`。

新包 `maa_api/agent/knowledge/sources/`：

| 文件 | 类 | 来源 id | 标题 | `license` | `setting_key` | `full_listing` |
|---|---|---|---|---|---|---|
| `maa_docs.py` | `MaaDocsSource` | `maa-docs` | MAA 官方文档 | AGPL-3.0（MaaAssistantArknights） | `rag.maa_docs_enabled` | 是 |
| `prts.py` | `PrtsCorpusSource` | `prts` | PRTS 页面 | CC BY-NC-SA 4.0，版权属于鹰角网络 | `rag.prts_enabled` | 否（按 `prune` 规则删） |
| `gamedata.py` | `GamedataCorpusSource` | `gamedata` | 拆包实体卡 | 版权属于鹰角网络；镜像自 Kengxxiao/ArknightsGameData | `rag.gamedata_enabled` | 是 |
| `copilot_site.py` | `CopilotSiteCorpusSource` | `copilot-site` | 作业站作业 | 作业版权属于各自的作业作者 | `rag.copilot_site_enabled` | 是 |
| `__init__.py` | `build_external_sources(...) -> list[CorpusSource]` | | | | | |

四个类都 `trusted=False`、`searchable_by_default=True`、`scheduled_refresh=True`。

M76 来源协议的扩展（`indexer.py`，都是可选成员，用 `getattr` 取，默认值写在括号里）：

```python
class CorpusSource(Protocol):
    ...
    scheduled_refresh: bool                       # 参与定时刷新（False）
    async def start(self, index: "KnowledgeIndexService") -> None: ...   # 服务启动时调（不调）
    async def close(self) -> None: ...                                  # 服务关闭时调（不调）
    async def prune(self, config: Mapping[str, Any]) -> int: ...        # 同步成功后调，返回删掉的文档数（不调）

class KnowledgeIndexService:
    async def push_documents(self, source_id: str, docs: Sequence[SourceDocument]) -> SyncSummary:
        """推送式写入：等该来源的锁，按同步的同一套规则写文档（不删任何文档），写完排一次 ensure_embeddings。"""
    async def refresh_all(self) -> dict[str, str]:
        """定时刷新：rag.enabled 为假直接返回；依次同步 scheduled_refresh 为真、已启用、开关设置为真的来源，
        一个来源失败不影响下一个；最后 ensure_embeddings。返回 {source_id: "ok" | "skipped" | "error"}。
        已有一次 refresh_all 在跑时直接返回 {}。"""
    def never_indexed(self) -> list[str]: ...   # last_indexed_at 为空且开关设置为真的来源 id（启动时读一次缓存）
```

`build_external_sources` 的签名：

```python
def build_external_sources(
    *, session_factory, settings_provider,
    maa_docs: MaaDocsClient | None = None,
    prts: PrtsClient | None = None,
    gamedata: GamedataClient | None = None,
    copilot_site: CopilotSiteClient | None = None,
    activity_service: Any = None,
) -> list[CorpusSource]: ...
```

### 4.3 接口

不加端点。`GET /api/agent/retrieval/sources` 多出四个来源，`setting_enabled` 为对应设置值。`PUT /api/agent/retrieval/sources/{source_id}` 的 `config` 按 §4.1 校验。

```json
{"id":"maa-docs","kind":"maa_docs","title":"MAA 官方文档","enabled":true,"setting_enabled":true,
 "searchable_by_default":true,"trusted":false,"license":"AGPL-3.0（MaaAssistantArknights）",
 "status":"idle","error":null,"doc_count":43,"chunk_count":612,"vector_count":0,
 "last_indexed_at":"2026-11-03T04:00:41Z","config":{"ref":"dev-v2","path":"docs/zh-cn"}}
```

`search_knowledge` 的工具描述改为：「按内容检索知识库与已建索引的资料（MAA 文档、PRTS 页面、拆包实体卡、作业站作业元数据），返回带出处的片段。不知道该读哪条知识、或要找规则与做法的线索时用；数值仍要用对应工具现查。」

### 4.4 配置项

`RagSettings` 加 5 个字段（都不是 config-only），`SETTING_KEYS`、`_FIELDS`、模板、`tests/test_settings.py` 的 `ENV_OVERRIDES` 同步：

```python
maa_docs_enabled: bool = True
prts_enabled: bool = True
gamedata_enabled: bool = True
copilot_site_enabled: bool = False
refresh_cron: str = "0 4 * * *"

@field_validator("refresh_cron")
@classmethod
def _valid_cron(cls, value: str) -> str:
    """五段 cron，用 APScheduler 自己解析一遍；解析不了就拒绝。"""
    CronTrigger.from_crontab(value)
    return value
```

```python
"rag.maa_docs_enabled": dict(label="检索 MAA 文档", group="Agent", type="boolean", hot_action="none"),
"rag.prts_enabled": dict(label="检索 PRTS 页面", group="Agent", type="boolean", hot_action="none"),
"rag.gamedata_enabled": dict(label="检索拆包实体卡", group="Agent", type="boolean", hot_action="none"),
"rag.copilot_site_enabled": dict(label="检索作业站作业", group="Agent", type="boolean", hot_action="none"),
"rag.refresh_cron": dict(label="语料刷新时间（cron）", group="Agent", type="string", hot_action="restart_required",
    description="五段 cron，按本机时区；改完重启服务生效"),
```

```yaml
rag:
  # 外部语料开关：关闭后不抓取、不参与检索（出站请求另受 sources.* 管）
  maa_docs_enabled: true
  prts_enabled: true
  gamedata_enabled: true
  copilot_site_enabled: false
  # 语料刷新时间，五段 cron，本机时区；改完重启服务生效
  refresh_cron: "0 4 * * *"
```

### 4.5 行为与算法

MAA 文档（`MaaDocsSource`）：

```
list_refs(config):
  entries = docs_dir(config.ref)
  zh = 名字等于 path 最后一段、type 为 dir 的那一项；没有就抛 EXTERNAL_RESPONSE_INVALID
  tree = tree(zh.sha)
  refs = [DocumentRef(external_id=e.path, title=e.path, version=e.sha)
          for e in tree.tree if e.type == "blob" and e.path 以 ".md" 结尾 and e.size >= 200]
fetch(refs, config):
  每个 ref：text = blob(ref.version)；title 按 §4.1 取；fetched_at = utcnow()
```

`size < 200` 的文件基本是只有一行标题的目录页（如 `manual/README.md` 98 字节），跳过。2026-11-02 的 49 个文件里有 6 个小于 200 字节，期望索引 43 篇。

PRTS（`PrtsCorpusSource`）：

```python
DEFAULT_SEED_TITLES = ("理智", "公开招募", "罗德岛基建", "信赖值", "游戏数据基础",
                       "采购中心", "干员模组一览", "危机合约", "集成战略", "保全派驻")
READ_PAGE_TTL_DAYS = 90
```

- `list_refs`：种子页各一个 `DocumentRef(version=None)`；再从库里取 `meta.origin == "read"` 的文档按 `fetched_at` 升序取前 `refresh_read_pages` 个，也给 `version=None`（总要重新取正文，正文没变不会重建）。
- `fetch`：按 20 个标题一组调 `PrtsClient.wikitext`；`missing` 的页面跳过；正文 `clean_page_text(page.content)`；`meta.origin` 是种子就写 `seed`，否则保留库里原来的值（随读页保留 `read_at`）。
- 随读：`start()` 里 `default_page_service().add_listener(self._on_page_loaded)` 并起一个后台消费者；`_on_page_loaded` 只把 `(page, fetched_at)` 放进 `asyncio.Queue(maxsize=100)`（满了丢弃并记 debug）。消费者在 `rag.enabled`、`rag.prts_enabled`、来源 `enabled` 都为真时调 `index.push_documents("prts", [doc])`，`doc.meta = {"origin": "read", "read_at": <ISO 时间>, ...}`；页面本来是种子时 `origin` 仍写 `seed`、另加 `read_at`。
- `prune(config)`：删掉 `origin == "seed"`、标题不在当前种子里、也没有 `read_at` 的文档；删掉 `read_at` 早于 90 天前的随读页；随读页总数超过 `max_pages` 时按 `fetched_at` 从旧到新删到上限。

拆包数据（`GamedataCorpusSource`）：

- `list_refs`：按 `config.tables` 依次 `corpus_table(...)`（`character` → `character_table.json`，`item` → `item_table.json`，`enemy` → `enemy_handbook_table.json`）；每张表解析完立刻生成卡片、丢掉原始 JSON；卡片放进 `self._pending`，返回的 `DocumentRef.version` 就是卡片正文 sha256 前 16 位。任何一张表取不到就抛错，本次同步不删任何文档。
- `fetch`：从 `_pending` 取；同步结束（无论成败）清空 `_pending`。
- 收录范围：`character_table` 里 id 以 `char_` 开头且 `isNotObtainable` 为假的干员；`item_table.items` 里 `classifyType` 为 `MATERIAL` 或 `CONSUME` 的道具；`enemy_handbook_table.enemyData` 里 `hideInHandbook` 为假的敌人。2026-11-02 实测分别是 438、1017、1761 个。
- 富文本清理：去掉 `<@…>`、`<$…>`、`</>` 标记，字面量 `\n` 换成换行。
- 卡片模板（字段为空的行整行省略）：

```markdown
# {name}（{appellation}）
- 稀有度：{n}★
- 职业：{PROFESSION_CN[profession]}；分支：{subProfessionId}
- 位置：{POSITION_CN[position]}
- 标签：{tagList 用「、」连接}
- 特性：{description}
- 获取方式：{itemObtainApproach}
- 简介：{itemUsage}
```

```markdown
# {name}
- 稀有度：{rarity 的数字}★
- 分类：{材料 | 消耗品}
- 用途：{usage}
- 说明：{description}
- 获取方式：{obtainApproach}
```

```markdown
# {name}（{enemyIndex}）
- 级别：{普通 | 精英 | 领袖}
- 伤害类型：{物理 | 法术 | 治疗 | 不造成伤害，多个用「、」连接}
- 攻击方式：{attackType}
- 能力：{abilityList[].text 逐条，用「；」连接}
- 描述：{description}
```

`rarity` 是 `TIER_5` 这样的字符串，取数字；`enemyLevel` 的 `NORMAL`、`ELITE`、`BOSS` 分别写普通、精英、领袖；`damageType` 的 `PHYSIC`、`MAGIC`、`HEAL`、`NO_DAMAGE` 分别写物理、法术、治疗、不造成伤害；没登记的取值原样写。

作业站（`CopilotSiteCorpusSource`）：

- `list_refs`：关卡 id = `activity_service.current(detail="full")` 里所有活动的 `stages[].stage_id`（去掉空值）加 `config.extra_stage_ids`，去重保序。取当期活动抛异常就整体抛错（不能把「取不到」当成「没有关卡」去删文档）。每个关卡调 `CopilotSiteClient.query(stage=..., order="hot", page=1, limit=per_stage_limit)`，一行一张卡片，放进 `_pending`。
- 卡片只用元数据：

```markdown
# {doc.title 或 作业 #{site_id}}
- 关卡：{stage_name}（{stage_id}）
- 作业编号：{site_id}；上传者：{uploader}；上传时间：{upload_time}
- 干员：{opers: 名字（技能 n）用「、」连接}
- 干员组：{groups: 组名：成员}
- 说明：{doc.details}
```

`actions` 不写；浏览量、好评率只放 `meta`，不写进正文，避免每天变化导致重建分块与重算向量。

定时刷新（`maa_api/main.py`，在注册 `maa-retention-startup` 之后、`update_scheduler.start()` 之前）：

```python
async def refresh_corpora() -> None:
    service = getattr(app.state, "knowledge_index_service", None)
    if service is not None:
        await service.refresh_all()

try:
    rag_trigger = CronTrigger.from_crontab(get_settings().rag.refresh_cron)
except ValueError:
    logger.warning("rag.refresh_cron 无法解析，改用默认的 0 4 * * *")
    rag_trigger = CronTrigger.from_crontab("0 4 * * *")
update_scheduler.add_job(refresh_corpora, rag_trigger, id="maa-rag-refresh",
                         replace_existing=True, max_instances=1, coalesce=True)
index_service = getattr(app.state, "knowledge_index_service", None)
for source_id in (index_service.never_indexed() if index_service is not None else []):
    update_scheduler.add_job(index_service.sync_source, "date",
                             run_date=datetime.now(timezone.utc) + timedelta(seconds=120),
                             args=[source_id], id=f"maa-rag-first-sync-{source_id}", replace_existing=True)
```

装配（02 §5 第 9 步，M76 注册两个条目来源之后、`start()` 之前）：`for source in build_external_sources(session_factory=db_session.session_factory, settings_provider=get_settings, activity_service=getattr(app.state, "activity_service", None)): index_service.register(source)`。`KnowledgeIndexService.start()` 对每个来源调可选的 `start(index)`，`close()` 对每个来源调可选的 `close()`；`sync_source` 成功后调可选的 `prune(config)`，删掉的文档数并进 `SyncSummary.deleted`。

边界情况：

- `rag.maa_docs_enabled=false` → `refresh_all` 跳过它，返回 `skipped`；检索不查它（M76 已按 `setting_key` 过滤）。
- `sources.enabled=false` 或 `sources.gamedata_enabled=false` → 对应语料同步时 `EXTERNAL_SOURCE_DISABLED`，来源 `status='error'`，错误写「外部数据源已在设置里关闭」，已有文档保留。
- GitHub 匿名额度用完 → 本次 `maa-docs` 记错误；已写入的批次保留；下次只下载缺的与变了的文件。
- 上游删了一个 MAA 文档 → 下次完整同步时删除；同步中途失败时不删。
- `get_prts_page` 读同一页两次、正文没变 → `push_documents` 只刷新抓取时间。
- 随读页超过 500 → `prune` 删最早抓取的随读页，种子页不受影响。
- `character_table.json` 下载超时 → 拆包语料本次失败，438 张干员卡与其余卡片都保留旧版。
- 没有当期活动、也没配额外关卡 → 作业站语料同步成功、清单为空，原有作业卡全部删除。
- 刷新进行中用户点「重建索引」→ 该来源忙时返回 409（M76 行为）；`refresh_all` 遇到忙的来源记 `skipped`。
- 服务刚启动、四个语料都没建过索引 → 120 秒后各有一个首次同步任务；同一来源的首次同步与定时刷新撞上时，后到的一方看到忙就跳过。

### 4.6 前端

`web/src/features/settings/knowledge/RetrievalSources.tsx` 的来源行加一行小字「许可：{license}」（`license` 为空不显示）。`setting_enabled===false` 的禁用与提示 M76 已做，本里程碑补测试。其余不改。

### 4.7 提示词与代理定义

代理定义不改。只改 `search_knowledge` 的工具描述（§4.3）。

## 5. 实施任务

### T1 许可与抓取约束核对

- 写范围：台账草稿（`docs/v3/ledgers/m77-corpora.md`，最后一起提交）
- 步骤：
  1. `curl -sS --max-time 20 -G https://prts.wiki/api.php --data-urlencode action=query --data-urlencode meta=siteinfo --data-urlencode siprop=rightsinfo --data-urlencode format=json -A "MAA-API/<版本> (+https://github.com/ChthollyWn/MAA-API)"`，记下 `rightsinfo`。2026-11-02 的结果是 CC BY-NC-SA 4.0。
  2. `curl -sS --max-time 20 https://prts.wiki/robots.txt | awk '/^User-agent: \*/{f=1} f'`，确认没有 `Disallow: /api.php`。2026-11-02 的结果：只禁 `/index.php`、`/images/` 与特殊页面。
  3. `curl -sS --max-time 20 https://api.github.com/repos/MaaAssistantArknights/MaaAssistantArknights`，记下 `default_branch`（应为 `dev-v2`）与 `license.spdx_id`（应为 `AGPL-3.0`）。
- 验证：三条结果写进台账「验证证据」。
- 完成判据：结果与上面的期望一致；不一致按 §10 停下来问。

### T2 外部访问层

- 写范围：`maa_api/services/external/sources.py`、`http.py`、`gamedata.py`、`maa_docs.py`（新）、`tests/services/test_maa_docs_client.py`（新）、`tests/services/test_gamedata_corpus.py`（新）、`tests/services/test_external_client_cache_flag.py`（新）
- 步骤：按 §4.2 实现；测试用 `httpx.MockTransport` 注入 `ExternalClient(transport=...)`。
- 验证：`.venv/bin/python -m pytest -q tests/services > /tmp/m77-t2.log 2>&1; echo $?; tail -n 3 /tmp/m77-t2.log` → 0。
- 完成判据：v2 既有的外部来源测试全部仍通过；新用例见 §6。

### T3 页面服务与干员服务的小改动

- 写范围：`maa_api/services/game/pages.py`、`maa_api/services/game/operators.py`、`tests/services/test_pages_listener.py`（新）
- 步骤：按 §4.2 加 `clean_page_text`、`PageService.add_listener`、两个公开别名。
- 验证：`.venv/bin/python -m pytest -q tests/services/test_pages_listener.py tests/services/test_game_pages.py tests/agent/test_game_tools.py > /tmp/m77-t3.log 2>&1; echo $?` → 0。
- 完成判据：`get_prts_page` 的既有测试不变；监听器用例通过。

### T4 来源协议扩展与刷新

- 写范围：`maa_api/agent/knowledge/index/indexer.py`、`tests/agent/knowledge_index/test_rag_refresh.py`（新）
- 步骤：按 §4.2 加 `push_documents`、`refresh_all`、`never_indexed`，以及可选成员 `scheduled_refresh`、`start`、`close`、`prune` 的调用点。
- 验证：`.venv/bin/python -m pytest -q tests/agent/knowledge_index > /tmp/m77-t4.log 2>&1; echo $?` → 0。
- 完成判据：M76 的用例不变且通过。

### T5 四个语料来源

- 写范围：`maa_api/agent/knowledge/sources/`（新包，5 个文件）、`tests/agent/knowledge_sources/`（新目录，4 个测试文件）、`tests/fixtures/corpora/`（新目录，夹具）
- 步骤：按 §4.5 实现。夹具：`tests/fixtures/corpora/` 下放手写的小 JSON（MAA 目录与目录树各一份、3 个干员 3 个道具 3 个敌人的精简表、一份作业站查询结果），不放真实大表。
- 验证：`.venv/bin/python -m pytest -q tests/agent/knowledge_sources > /tmp/m77-t5.log 2>&1; echo $?; tail -n 3 /tmp/m77-t5.log` → 0。
- 完成判据：§6 里四个来源的用例全部通过；`rg -n 'pytest.mark.skip\|importorskip' tests/agent/knowledge_sources` 没有输出。

### T6 设置与装配

- 写范围：`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml`、`tests/test_settings.py`、`maa_api/main.py`、`maa_api/agent/tools/knowledge.py`（只改描述）、`tests/api/test_rag_refresh_wiring.py`（新）
- 步骤：按 §4.4 加设置；按 §4.5 装配来源与两类定时任务；改工具描述。
- 验证：`.venv/bin/python -m pytest -q tests/test_settings.py tests/test_vapid_settings.py tests/api/test_rag_refresh_wiring.py tests/agent/test_search_knowledge_tool.py > /tmp/m77-t6.log 2>&1; echo $?` → 0；`.venv/bin/python scripts/agent_smoke.py > /tmp/m77-as.log 2>&1; tail -n 1 /tmp/m77-as.log` → `SMOKE OK`。
- 完成判据：真实 lifespan 起来后 `app.state.update_scheduler.get_job("maa-rag-refresh")` 存在，触发器字符串含 `hour='4'`。

### T7 前端

- 写范围：`web/src/features/settings/knowledge/RetrievalSources.tsx`、`retrieval.test.tsx`
- 步骤：按 §4.6。
- 验证：`cd web && pnpm typecheck > /tmp/m77-tc.log 2>&1 && pnpm test --run > /tmp/m77-vitest.log 2>&1; echo $?; cd ..` → 0。

### T8 文档与验收

- 写范围：§9 列出的文档、台账
- 步骤：跑 §7。
- 验证：见 §7。

## 6. 测试清单

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/services/test_external_client_cache_flag.py` | `test_cache_false_skips_read_and_write` | `cache=False` 时同一请求发两次网络、缓存里没有条目 |
| 同上 | `test_cache_false_does_not_fall_back_to_stale` | 失败时不回退旧值，直接抛错 |
| `tests/services/test_maa_docs_client.py` | `test_urls_and_params` | 三个端点的 URL 形状；`tree` 带 `recursive=1`；`blob` 的 sha 进路径 |
| 同上 | `test_blob_decodes_base64` | 返回 UTF-8 文本；`encoding` 不是 base64 时报 `EXTERNAL_RESPONSE_INVALID` |
| 同上 | `test_truncated_tree_is_invalid` | `truncated: true` 报错 |
| 同上 | `test_rate_limit_message` | 403 改写成「匿名额度已用完」，`details.status==403` |
| `tests/services/test_gamedata_corpus.py` | `test_corpus_table_whitelist` | 三个文件名之外报 `INVALID_PARAMETER`，不发请求 |
| 同上 | `test_corpus_table_uses_corpus_kind_without_cache_or_github` | 请求走 `gamedata_corpus`；缓存里没有条目；镜像都失败时不请求 `api.github.com` |
| 同上 | `test_corpus_spec_limits` | `SOURCES["gamedata_corpus"].max_bytes == 24 MiB`；受 `sources.gamedata_enabled` 管 |
| 同上 | `test_existing_excel_and_level_unchanged` | `excel()`、`level()` 的请求与缓存行为与改前相同 |
| `tests/services/test_pages_listener.py` | `test_listener_receives_loaded_page` | `page()` 成功后监听器拿到 `WikiPage` 与抓取时间 |
| 同上 | `test_listener_error_does_not_break_page` | 监听器抛错时 `page()` 照常返回 |
| 同上 | `test_clean_page_text_matches_private_cleaner` | 与 `_clean_text` 输出相同 |
| `tests/agent/knowledge_sources/test_maa_docs_source.py` | `test_list_refs_filters_markdown_and_tiny_files` | 只要 `.md`、跳过小于 200 字节的；`version` 是 blob sha |
| 同上 | `test_fetch_title_and_url` | frontmatter `title` 优先，其次 `# ` 标题；URL 用配置的 `ref` |
| 同上 | `test_unchanged_blob_is_not_downloaded` | 第二次同步不请求 blob |
| 同上 | `test_rate_limit_keeps_written_batches` | 第二批 403 时第一批已入库、来源 `status='error'`、没有删除 |
| 同上 | `test_config_validation` | 非法 `ref`、`path` 报 `VALIDATION_ERROR` |
| `tests/agent/knowledge_sources/test_prts_source.py` | `test_seeds_fetched_in_batches_of_twenty` | 25 个种子分两次请求；`missing` 的跳过 |
| 同上 | `test_read_through_pushes_page` | 监听器入队后消费者写入，`meta.origin=="read"`、带 `read_at` |
| 同上 | `test_read_through_respects_switches` | `rag.prts_enabled=false` 时不写入 |
| 同上 | `test_prune_rules` | 移出种子且没读过的删除；读过超过 90 天的删除；超过 `max_pages` 删最旧的随读页 |
| 同上 | `test_refresh_includes_oldest_read_pages` | `list_refs` 带上最旧的 `refresh_read_pages` 个随读页 |
| `tests/agent/knowledge_sources/test_gamedata_source.py` | `test_cards_follow_templates_and_filters` | 夹具里不可获得的干员、`NONE` 类道具、图鉴隐藏的敌人都不出卡片；卡片行与模板一致 |
| 同上 | `test_markup_cleaned_and_no_stat_numbers` | 没有 `<@`、`</>`；卡片不含属性字段（`phases`、`attributes`） |
| 同上 | `test_unchanged_cards_are_skipped` | 第二次同步 `rebuilt == 0` |
| 同上 | `test_table_failure_deletes_nothing` | 一张表失败时抛错、旧卡片都在 |
| `tests/agent/knowledge_sources/test_copilot_site_source.py` | `test_stage_ids_from_activity_and_config` | 关卡 id 合并去重，按每关 `per_stage_limit` 查询 |
| 同上 | `test_cards_exclude_actions_and_counters` | 正文没有 `actions` 内容、没有浏览量与好评率；`meta` 里有 |
| 同上 | `test_activity_failure_raises_and_keeps_docs` | 当期活动抛错时同步失败、文档不删 |
| `tests/agent/knowledge_index/test_rag_refresh.py` | `test_refresh_all_skips_disabled_and_continues_after_error` | 设置关闭的记 `skipped`；一个来源出错后下一个照常同步；最后调了 `ensure_embeddings` |
| 同上 | `test_refresh_all_is_single_flight` | 并发两次调用，第二次返回 `{}` |
| 同上 | `test_push_documents_waits_for_lock_and_never_deletes` | 同步进行时推送等待；推送不删除其他文档 |
| 同上 | `test_prune_runs_after_successful_sync_only` | 同步失败时不调 `prune` |
| `tests/api/test_rag_refresh_wiring.py` | `test_refresh_job_registered_with_cron` | `maa-rag-refresh` 存在、触发器来自 `rag.refresh_cron` |
| 同上 | `test_first_sync_jobs_for_never_indexed_sources` | 没建过索引且开关为真的来源各有一个 `maa-rag-first-sync-*` |
| 同上 | `test_sources_list_shows_external_corpora` | `GET /api/agent/retrieval/sources` 有六个来源，外部四个 `setting_enabled` 与设置一致 |
| `tests/test_settings.py` | `ENV_OVERRIDES` 整表（改）、`test_rag_refresh_cron_rejects_garbage`（新） | 新增 5 个 `MAA_RAG_*`；`refresh_cron="every day"` 解析失败 |
| `web/src/features/settings/knowledge/retrieval.test.tsx` | `external sources show license and setting state` | 「许可：」一行；`setting_enabled:false` 时开关禁用并显示「已在设置里关闭」 |

## 7. 验收

照 [12 §4](../12-实施路线与里程碑.md)：

```bash
.venv/bin/python -m pytest -q > /tmp/m77-pytest.log 2>&1; echo $?; tail -n 30 /tmp/m77-pytest.log
.venv/bin/python scripts/dump_openapi.py --check; echo $?
cd web && pnpm install --frozen-lockfile > /tmp/m77-pnpm.log 2>&1 && pnpm gen:api:check && pnpm typecheck > /tmp/m77-tc.log 2>&1 && pnpm test --run > /tmp/m77-vitest.log 2>&1 && pnpm build > /tmp/m77-build.log 2>&1; echo $?; cd ..
.venv/bin/python scripts/frontend_smoke.py > /tmp/m77-fs.log 2>&1; tail -n 1 /tmp/m77-fs.log
.venv/bin/python scripts/agent_smoke.py > /tmp/m77-as.log 2>&1; tail -n 1 /tmp/m77-as.log
.venv/bin/python scripts/orchestrator_smoke.py > /tmp/m77-os.log 2>&1; tail -n 1 /tmp/m77-os.log
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m77-eval-r.log 2>&1; tail -n 5 /tmp/m77-eval-r.log
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m77-eval-c.log 2>&1; tail -n 5 /tmp/m77-eval-c.log
.venv/bin/python scripts/check_doc_links.py | tail -n 1
git diff --check; echo $?
```

期望：pytest 不新增失败；openapi 不变（本里程碑没有改接口形状，`--check` 退出码 0）；前端四步退出码 0；三个冒烟 `SMOKE OK`；回归评测全部通过；能力评测不低于台账基线（工具描述变了）；`BROKEN 0`；`git diff --check` 退出码 0。

联网的补充验收（结果记台账，不作硬性验证）：

1. 起服务，在「检索来源」里依次点四个外部语料的「重建索引」（作业站先在设置里打开）。期望：`maa-docs` 约 43 篇；`prts` 10 篇；`gamedata` 约 3200 篇；`copilot-site` 篇数等于当期活动关卡数乘以不超过 10。
2. 测试检索「MAA 连接模拟器失败」命中 MAA 文档；「源石虫」命中拆包敌人卡；「公开招募 标签」命中 PRTS 页面。
3. 在指挥台让 Worker 读一个 PRTS 页面（`get_prts_page`），几秒后 `prts` 来源的篇数加一。
4. 再点一次 `maa-docs` 的「重建索引」，日志里没有 blob 请求（版本都没变）。

## 8. 风险与回滚

- GitHub 匿名额度（每小时 60 次）与 v2 拆包兜底共用：首次全量约 51 次，可能挤掉当小时的拆包兜底；拆包兜底只在两个 jsDelivr 镜像都失败时才用，影响小。
- `character_table.json` 解析时内存瞬时上涨约百兆：只在定时刷新与手动重建时发生，解析完立刻释放。
- PRTS 页面进本地库与 v2 M25 §3.6 第 5 条相反：V3-D43 已决定；只存本机、日志不记正文、结果带许可与出处。
- jsDelivr 对超过 50 MB 的仓库会跳转到 `raw.githubusercontent.com`（MAA 仓库就是这样），目前 `Kengxxiao/ArknightsGameData` 能直接取；哪天也被跳转，`follow_redirects=False` 会让拆包语料失败、旧卡片保留，再换方案。
- 回滚：没有迁移，`git revert -m 1 <merge>` 即可；四个来源的文档留在库里不影响检索（来源没注册时不参与），需要清掉时 `DELETE FROM knowledge_source WHERE id IN ('maa-docs','prts','gamedata','copilot-site')`（级联删文档与分块）。

## 9. 文档同步

- [07 §3](../07-知识RAG与记忆.md)：实际篇数、默认种子、刷新规则与实现不一致处同步修改。
- `docs/v3/16-部署指南.md`：出站域名加 `api.github.com`（MAA 文档）、`fastly.jsdelivr.net`（拆包语料）；说明语料刷新时间与开关在哪里改。
- `config.template.yaml`：`rag` 段加 5 个键（T6）。
- [02 §4.1](../02-系统架构.md)：`knowledge/sources/` 的文件清单。
- 计数：配置键 +5；工具、端点、错误码、表都不变。

## 10. 停下来问

- T1 核对的许可或 robots 规则与 §5 T1 写的期望不一致，或 PRTS 站点出现禁止抓取、禁止本地存储的声明。
- MAA 仓库的默认分支不是 `dev-v2`，或 `docs/zh-cn` 不在原位置。
- 前置检查第 4 条显示 M76 的同步不是「取一批、写一批」。
- 想取 `stage_table.json`、`skill_table.json`，或想把属性数值写进卡片。
- 想加 GitHub token 配置（会新增配置键）。

## 需要补进契约的名字

| 名字 | 用途 | 建议补到 |
|---|---|---|
| `maa_api/agent/knowledge/sources/{maa_docs,prts,gamedata,copilot_site}.py`；`MaaDocsSource`、`PrtsCorpusSource`、`GamedataCorpusSource`、`CopilotSiteCorpusSource`、`build_external_sources` | 四个外部语料来源 | 02 §4.1 |
| `maa_api/services/external/maa_docs.py`（`MaaDocsClient`）；`SOURCES` 新增 `maa_docs`、`gamedata_corpus`；`GAMEDATA_CORPUS_FILES`；`GamedataClient.corpus_table` | 外部访问层扩展 | 02 §4.1（v2 外部访问层附注） |
| `ExternalClient.get_json(..., cache=...)` | 大响应不进进程内缓存 | 02 §4.1 |
| `PageService.add_listener`、`clean_page_text`；`PROFESSION_CN`、`POSITION_CN` | 随读与卡片生成复用 v2 代码 | 02 §4.1 |
| `KnowledgeIndexService.push_documents`、`refresh_all`、`never_indexed`；来源可选成员 `scheduled_refresh`、`start`、`close`、`prune` | 推送式写入、定时刷新、首次同步、收尾清理 | 02 §2.2 |
| 调度任务 id `maa-rag-refresh`、`maa-rag-first-sync-<source_id>` | 定时刷新与首次同步 | 02 §5 |
| `DEFAULT_SEED_TITLES`、`READ_PAGE_TTL_DAYS` | PRTS 种子页与随读页保留期 | 07 §3 |
