# M64 工程整备：包管理、锁文件、活文档迁移、断链检查（规格）

> 阶段：P0　依赖：M63　分支：`engineering-baseline`　迁移：无
> 本文是 M64 的唯一规格；实施偏离记在 [13 §8](../13-决策记录.md)。

## 0. 前置检查

先 `mkdir -p /tmp/maa_m64`，再在仓库根目录逐条执行下面的命令块（`# 0.N` 对应表格行）。任何一条不符合期望，停下来报告。

| # | 检查什么 | 期望 |
|---|---|---|
| 0.1 | 在 `engineering-baseline` 分支，基点是 dev | 首行 `## engineering-baseline`；除未跟踪的 `docs/ideas/` 外没有改动；末行 `ok` |
| 0.2 | M63 已合并 | 输出 `v3-m63` |
| 0.3 | v2 的三份活文档已在 `docs/v2/` | 输出 `3` |
| 0.4 | v3 副本还不存在 | 输出 `0` |
| 0.5 | 测试与脚本读的是 v2 路径 | 恰好 4 行，路径都含 `"docs" / "v2"` |
| 0.6 | 前端导入的是 v2 指南 | 恰好 1 行 |
| 0.7 | 锁文件缺 scipy、numpy 且与 pyproject 不一致 | 第一行 `0`；第二行以 `Error: pyproject.toml changed significantly` 开头 |
| 0.8 | 本机工具版本 | `uv 0.7.6 …`；`Poetry (version 2.1.3)` |
| 0.9 | 断链脚本与基线还不存在 | 输出 `0` |
| 0.10 | 全量测试基线 | 记下退出码与失败用例，写进台账「验证证据」；之后只比较新增失败 |

```bash
# 0.1
git status -sb && git merge-base --is-ancestor dev HEAD && echo ok
# 0.2
git tag -l 'v3-m63'
# 0.3
ls docs/v2/05-API规范与路由清单.md docs/v2/14-开放API接入指南.md docs/v2/15-部署指南.md | wc -l
# 0.4
ls docs/v3/14-*.md docs/v3/15-*.md docs/v3/16-*.md 2>/dev/null | wc -l
# 0.5
rg -n 'API_SPEC_PATH =|GUIDE_PATH =|ERROR_SPEC_PATH =' tests/domain/test_errors.py tests/domain/test_route_inventory.py scripts/generate_api_guide.py
# 0.6
rg -n "docs/v2/14-开放API接入指南.md" web/src/features/api-console/GuideMarkdown.tsx
# 0.7
grep -c -E '^name = "(scipy|numpy)"' poetry.lock; POETRY_CACHE_DIR=$(mktemp -d) poetry check --lock 2>&1 | grep '^Error'
# 0.8
uv --version; poetry --version
# 0.9
ls scripts/check_doc_links.py scripts/doc_links_baseline.txt 2>/dev/null | wc -l
# 0.10
.venv/bin/python -m pytest -q > /tmp/maa_m64/pytest-base.log 2>&1; echo $?; tail -n 5 /tmp/maa_m64/pytest-base.log
```

## 1. 背景与目标

- `pyproject.toml` 早在 M27 就声明了 `scipy = ">=1.11"`，但 `poetry.lock` 一直没重锁：锁文件里没有 scipy 与 numpy，`poetry check --lock` 报 pyproject 已变更（2026-11-02 实测退出码 1）。`Dockerfile` 按锁文件 `poetry install`，Poetry 2.x 对过期锁文件直接报错，镜像实际装不出来（本机没有可用的 Docker Engine，未实测构建）。
- 本机 `.venv` 里的 scipy 1.18.1 与 numpy 2.5.3 是 M27 用 pip 手装的，要求 Python ≥ 3.12；项目声明 Python `^3.11`，Docker 基底是 3.11.9。
- v2 冻结后，受测试约束的三份活文档（错误码表与路由清单、开放 API 指南、部署指南）需要有 v3 的现行副本（[04 §7](../04-接口与事件协议.md)、V3-D62）。
- [12 §4](../12-实施路线与里程碑.md) 第 7 条要求 M64 起每个里程碑跑 `scripts/check_doc_links.py` 并输出 `BROKEN 0`，这个脚本还不存在。

做完以后可以验证：

1. 四个 PyPI 源的可达性有当天的实测记录；Q8 有结论，记在 13 §8 与 §9。
2. 锁文件包含 scipy、numpy，`poetry check --lock`（或 `uv lock --check`）退出码 0；锁文件里其余 58 个包版本不变。
3. `.venv` 与锁文件一致，养成规划的测试与 `scripts/account_smoke.py` 通过。
4. `Dockerfile`、`docs/ENVIRONMENT.md`、`docs/v3/16-部署指南.md` 写的安装方式与锁文件一致；v3 可选 extra 的命名约定写进 `docs/v3/16-部署指南.md`。
5. `docs/v3/14-现行接口清单.md`、`docs/v3/15-开放API接入指南.md`、`docs/v3/16-部署指南.md` 建立；错误码表、路由清单、指南生成器、前端调试台指南、根 README 都改读 v3 副本；三份 v2 原件开头各有一行冻结说明。
6. `scripts/check_doc_links.py` 入库，末行 `BROKEN 0`；已知的 25 处断链列在 `scripts/doc_links_baseline.txt`，`tests/test_doc_links.py` 保证不新增、基线只减不增。
7. `setting_service._validate` 对列表类型设置项的行为有实测结论，写进台账、13 §8，并更新 04 §6 的说法。

## 2. 非目标

- 不升级任何已锁定的依赖版本；不新增运行时依赖（scipy 早已声明，numpy 是它的传递依赖）。
- 不改 Python 版本下限（仍是 `^3.11`），不改 Docker 基底镜像 `python:3.11.9-slim`（`tests/api/test_frontend.py` 钉着这一行）。
- 路径 P（继续用 Poetry）下不把 `pyproject.toml` 改成 PEP 621 的 `[project]` 写法；`poetry check` 的弃用警告保留。
- 不修 `deployment.cors_origins` 保存时报 500 的问题（见 §4.2.7），只记录。修它属于改对外行为。
- 不检查链接锚点（`#…` 部分），不检查外部 URL。
- 除三份 v2 原件开头的冻结行外，不改 `docs/v2/` 下任何文件。这是 [specs/README](./README.md) 第 6 条之外的第二个例外，由本 spec 授权。
- 不建任何 extra，只定约定；具体依赖由后续里程碑加。

## 3. 相关文档

- [README 决策速查表](../README.md)「工程与流程」的「依赖」「文档」两行；[13 §7](../13-决策记录.md) 的 V3-D60、V3-D62；[13 §9](../13-决策记录.md) 的 Q8。
- [04 §6](../04-接口与事件协议.md) 开头「不要用列表类型的设置项」一段；[04 §7](../04-接口与事件协议.md) 现行接口清单。
- [12 §4](../12-实施路线与里程碑.md) 第 7 条（断链门禁）。
- [M63 规格](./m63-docs-restructure.md) §0 第 4 条（25 处断链的来源）与附录 A（`check` 模式的原始逻辑）。
- v2 部署指南的依赖说明：`rg -n '^## 8\. |本机的安装偏离|^### Docker' docs/v2/15-部署指南.md`（[v2/15](../../v2/15-部署指南.md) §1 Docker、§8）。
- 环境事实：`rg -n 'poetry|scipy|POETRY_CACHE_DIR' docs/ENVIRONMENT.md`（[ENVIRONMENT](../../ENVIRONMENT.md)「基础工具链」第 16、18 行附近，「常驻服务与 .venv」第 26、28 行附近）。
- 既有代码入口（行号是 2026-11-02 的近似值）：
  - [`pyproject.toml`](../../../pyproject.toml)：`[tool.poetry.dependencies]`（第 9–37 行），`scipy = ">=1.11"`（第 32 行），`[[tool.poetry.source]]` 名为 `aliyun`（第 42–45 行）。
  - [`Dockerfile`](../../../Dockerfile)：运行阶段 `COPY pyproject.toml poetry.lock ./`（第 22 行）、`pip install --no-cache-dir poetry -i https://mirrors.aliyun.com/pypi/simple/` 与 `poetry install --no-root --only main --no-interaction`（第 23–24 行）、`CMD ["poetry", "run", "uvicorn", …]`（第 38 行）。
  - `tests/api/test_frontend.py` 的 `test_dockerfile_builds_frontend_then_copies_only_output_to_python_runtime`（约 139 行）：钉住 `FROM python:3.11.9-slim AS runtime`、`COPY alembic.ini ./alembic.ini`、`COPY config.template.yaml ./config.yaml`、`COPY --from=web /static ./static`。
  - `tests/domain/test_errors.py`：`API_SPEC_PATH`（约 39 行）；`parse_docs_error_tables`（约 74 行）只看 `## 4. 错误码表` 到下一个 `## ` 之间的 `### 4.N` 小节，表格行第一列是反引号包住的码；计数常量 `ALL_CODE_COUNT = 124`、`HTTP_BOUND_CODE_COUNT = 123`、`SECTION_COUNT = 20`（约 56–58 行）。
  - `tests/domain/test_route_inventory.py`：`API_SPEC_PATH`（约 36 行）、`SECTION_COUNT = 22`（约 40 行）；`parse_route_inventory`（约 155 行）从 `## 6. ` 开始，到 `## 7. ` 或文件末尾结束；总数句正则 `共 (\d+) 条 HTTP 端点`、`另有 (\d+) 条 WebSocket`、`(\d+) 条静态与文档入口`。
  - `scripts/generate_api_guide.py`：`GUIDE_PATH`、`ERROR_SPEC_PATH`（约 15–16 行）；`parse_error_descriptions`（约 22 行）扫整份文件里形如 `` | `CODE` | 404 | 说明 | `` 的行；`--check` 失败时打印 `API guide generated sections are stale`。
  - `web/src/features/api-console/GuideMarkdown.tsx` 第 2 行的 `?raw` 导入；本地 `.md` 链接一律渲染成指向 `/docs` 的链接（约 20 行）。
  - `web/src/features/api-console/api-console.page.test.tsx` 约 197 行：`screen.getAllByRole('link', { name: /05-API规范与路由清单/ })`，断言指南里有一条名字含 `05-API规范与路由清单` 的链接。
  - `maa_api/services/setting_service.py` 的 `SettingService._validate`（约 265 行），类型表在约 269–276 行；`maa_api/services/settings_schema.py` 的 `_FIELDS` 里 `deployment.cors_origins` 是 `type="string[]"`（约 142 行），`adb.common_ports` 是 `type="integer[]"`（约 56 行）；`maa_api/api/errors.py` 的 `_unhandled_exception_handler`（约 297 行）把未捕获异常映射成 500 `INTERNAL_ERROR`。

## 4. 设计

### 4.1 数据模型

无。

### 4.2 模块与文件

#### 4.2.1 包管理：实测与决策树

可达性实测用附录 A 的 `/tmp/maa_m64/probe.sh`。它对每个源做两件事：

1. 取 `<源>/scipy/` 的 simple 页面（`--connect-timeout 10 --max-time 30`），记 HTTP 状态码与耗时。
2. 从页面里找 `scipy-1.17.1-cp311-cp311-manylinux…x86_64.whl` 的链接，用 `-r 0-65535` 只取前 64 KiB，记状态码与耗时。固定用这个历史版本的文件，结果不随新版本发布变化。

输出形如 `aliyun index=[200 0.150412] file=[206 0.080100]`。判定标准：

- 一个源可用，当且仅当 `index` 的状态码是 `200`，并且 `file` 的状态码是 `206` 或 `200`。
- 出现 `000`（超时或连不上）、其他状态码或 `no-wheel`（页面里找不到那个文件），这个源本次不可用。
- 跑两遍；两遍都可用才算可用。

2026-11-02 的参考结果：四个源全部可用（aliyun 0.15 s / 0.08 s，tuna 0.81 s / 0.45 s，ustc 1.29 s / 0.38 s，pypi 1.31 s / 0.51 s）。同日在仓库副本上执行 `POETRY_CACHE_DIR=$(mktemp -d) poetry lock` 用 68 秒完成，只新增 numpy 2.4.6 与 scipy 1.17.1。M27 记录的「镜像不可达」当天没有复现；ENVIRONMENT.md 第 28 行附近记过的误诊模式（受限沙箱里缓存目录不可写，报错却是连不上镜像）是一个可能原因，未验证。

决策树（按顺序走，命中即停）：

1. 可用源列表 S 按 aliyun、tuna、ustc、pypi 的顺序排列。S 为空，停下来问（§10）。
2. 取 S 的第一个源 s，走路径 P（§4.2.2）。s 不是 aliyun 时，先按 §4.2.2 第 1 步换源。
3. 路径 P 的 `poetry lock` 失败：
   - 日志里有 `Operation not permitted`：换一个新的 `POETRY_CACHE_DIR=$(mktemp -d)` 重试一次。
   - 其他失败（连接、解析、超过 900 秒被 `alarm` 结束、退出码 142）：换 S 里的下一个源重试。
   - S 里的源都试过仍失败：`git checkout -- pyproject.toml poetry.lock`，改走路径 U（§4.2.3），源仍按 S 的顺序取。
4. 路径 P 成功但锁文件差异（附录 B）出现 `REMOVED` 或 `CHANGED` 非空：`git checkout -- poetry.lock`，停下来问。
5. 路径 U 也失败，或差异出现 `REMOVED`、`CHANGED` 非空：恢复改动，停下来问。

优先路径 P 的理由：改动最小（`pyproject.toml` 格式、Dockerfile 安装方式、文档与习惯都不变）；Poetry 2.x 的 `poetry lock` 默认保留已锁版本，只锁新依赖。路径 U 只在 Poetry 本身在可达源上锁不出来时启用。

#### 4.2.2 路径 P：继续用 Poetry

1. 换源（只在 s 不是 aliyun 时做）：把 `pyproject.toml` 末尾的源改成 s，名字取探测脚本里的源名。例如 tuna：

   ```toml
   [[tool.poetry.source]]
   name = "tuna"
   url = "https://pypi.tuna.tsinghua.edu.cn/simple/"
   priority = "primary"
   ```

2. 重锁：

   ```bash
   git show HEAD:poetry.lock > /tmp/maa_m64/poetry.lock.before
   export POETRY_CACHE_DIR=$(mktemp -d)
   perl -e 'alarm shift; exec @ARGV' 900 poetry lock > /tmp/maa_m64/poetry-lock.log 2>&1; echo $?
   tail -n 3 /tmp/maa_m64/poetry-lock.log
   poetry check --lock > /tmp/maa_m64/poetry-check.log 2>&1; echo $?
   grep -n '^Error' /tmp/maa_m64/poetry-check.log
   .venv/bin/python /tmp/maa_m64/lockdiff.py /tmp/maa_m64/poetry.lock.before poetry.lock
   grep -c -E '^name = "(scipy|numpy)"' poetry.lock
   ```

   期望：`poetry lock` 退出码 0，日志末尾有 `Writing lock file`；`poetry check --lock` 退出码 0 且没有 `Error` 行（弃用警告可以有）；差异输出 `OLD 58 NEW 60`、`ADDED {'numpy': ['2.4.x'], 'scipy': ['1.17.x']}`、`REMOVED {}`、`CHANGED {}`（小版本以执行时为准）；最后一行 `2`。macOS 没有 `timeout`，这里用 `perl` 的 `alarm` 限时 900 秒。

3. 同步 `.venv`：

   ```bash
   POETRY_CACHE_DIR=$(mktemp -d) poetry install --no-root --dry-run > /tmp/maa_m64/install-dry.log 2>&1; echo $?
   grep -E 'Installing|Updating|Downgrading|Removing' /tmp/maa_m64/install-dry.log
   ```

   期望只出现 numpy 与 scipy 两行，形如 `Downgrading numpy (2.5.3 -> 2.4.6)`、`Downgrading scipy (1.18.1 -> 1.17.1)`。出现其他包，停下来问。确认后去掉 `--dry-run` 真正执行一次，再验证：

   ```bash
   .venv/bin/python -c "import scipy, numpy; print(scipy.__version__, numpy.__version__)"
   launchctl print gui/$(id -u)/local.maa-api > /dev/null 2>&1 && echo service-loaded || echo service-absent
   ```

   第一行与锁文件里的两个版本一致。第二行是 `service-loaded` 时不要重启服务，台账「遗留」写一条「常驻服务仍加载着旧的 scipy，请择机执行 `./scripts/maa-api-service.sh reload`」，并在最终汇报里提醒用户（ENVIRONMENT.md「常驻服务与 .venv」一节记过换包后在跑进程的问题）。

4. Dockerfile（运行阶段第 22–24 行）改成下面这样。只固定 Poetry 大版本与源，其余不动：

   ```dockerfile
   COPY pyproject.toml poetry.lock ./
   RUN pip install --no-cache-dir "poetry>=2.1,<3" -i https://mirrors.aliyun.com/pypi/simple/ \
       && poetry install --no-root --only main --no-interaction
   ```

   s 不是 aliyun 时，`-i` 换成 s 的 simple 地址。

#### 4.2.3 路径 U：改用 uv（只在决策树第 3 步走到这里时做）

1. `cp pyproject.toml /tmp/maa_m64/pyproject.toml.before`，把 `pyproject.toml` 整个改写成下面的内容（`^` 约束按 Poetry 语义展开成上下界；原有注释逐字保留；`[[tool.uv.index]]` 的地址用 s）：

   ```toml
   [project]
   name = "maa-api"
   version = "0.1.0"
   description = "以 MaaAssistantArknights 为内核的 FastAPI 服务"
   authors = [{ name = "ChthollyWn", email = "85938618+ChthollyWn@users.noreply.github.com" }]
   readme = "README.md"
   license = "AGPL-3.0-only"
   requires-python = ">=3.11,<4.0"
   dependencies = [
     "fastapi>=0.138,<1.0",
     "uvicorn>=0.30",
     # uvicorn 自动选择 WebSocket 协议实现时需要服务端实现；M4 起的部署必须支持真实的
     # /api/ws 升级。
     "websockets>=13.0",
     "pydantic>=2.7,<3.0",
     "sqlmodel>=0.0.22",
     "alembic>=1.13,<2.0",
     "aiosqlite>=0.20,<0.21",
     # greenlet 必须显式声明：SQLAlchemy 只把它放进 sqlalchemy[asyncio] extra，且其平台
     # 标记不覆盖 macOS arm64（标记里是 aarch64/x86_64/...，本机 platform.machine() 为
     # "arm64"），导致异步引擎在 .venv 里抛 ValueError: the greenlet library is required。
     # 实测见 docs/ENVIRONMENT.md 与 tests/fixtures/db_probe_findings.md §5。
     "greenlet>=3.1,<4.0",
     "httpx>=0.27,<0.28",
     "cryptography>=39.0.1",
     # jinja2 仍在使用（maa_api/model/core/executor.py 的邮件模板），
     # 是否移除待 07 定稿，见 docs/02 §10 依赖变更
     "jinja2>=3.0.0,<4.0.0",
     # 养成规划的线性规划求解器（M27，规格 §7.6）：`scipy.optimize.linprog` 走 HiGHS。
     # 只在规划时 import，服务启动不加载；镜像体积代价见 docs/15。
     "scipy>=1.11",
     "adbutils>=2.8.0,<3.0.0",
     "ruamel-yaml>=0.18.7,<0.19.0",
     "apscheduler>=3.11.0,<4.0.0",
     "requests[socks]>=2.31.0,<3.0.0",
     "tqdm>=4.67.1,<5.0.0",
   ]

   [dependency-groups]
   dev = ["pytest>=8,<9"]

   [[tool.uv.index]]
   name = "aliyun"
   url = "https://mirrors.aliyun.com/pypi/simple/"
   default = true

   [build-system]
   requires = ["poetry-core>=2.0"]
   build-backend = "poetry.core.masonry.api"
   ```

2. 先钉住旧锁文件的版本再锁，避免 uv 把 58 个包都换成最新版：用附录 B 的 `pins.py` 往 `pyproject.toml` 末尾追加一段临时的 `[tool.uv] constraint-dependencies`，`uv lock`；然后删掉这一段，再 `uv lock` 一次（uv 会沿用 `uv.lock` 里已有的版本）：

   ```bash
   git show HEAD:poetry.lock > /tmp/maa_m64/poetry.lock.before
   .venv/bin/python /tmp/maa_m64/pins.py /tmp/maa_m64/poetry.lock.before >> pyproject.toml
   uv lock > /tmp/maa_m64/uv-lock-1.log 2>&1; echo $?
   # 手工删掉 pyproject.toml 末尾以 [tool.uv] 开头的整段
   uv lock > /tmp/maa_m64/uv-lock-2.log 2>&1; echo $?
   uv lock --check; echo $?
   .venv/bin/python /tmp/maa_m64/lockdiff.py /tmp/maa_m64/poetry.lock.before uv.lock
   ```

   期望：三个退出码都是 0；差异 `REMOVED {}`、`CHANGED {}`，`ADDED` 里 numpy 与 scipy 各有两个版本（Python 3.11 一个、3.12 及以上一个，uv 按 Python 版本分叉解析），2026-11-02 的预演结果是 `{'numpy': ['2.4.6', '2.5.4'], 'scipy': ['1.17.1', '1.18.1']}`。
3. `git rm poetry.lock`；`uv sync --frozen --inexact > /tmp/maa_m64/uv-sync.log 2>&1; echo $?` 退出码 0。`--inexact` 保留 `.venv` 里锁文件之外的包（例如 pip），ENVIRONMENT.md 里的 pip 兜底用法仍然可用。之后同路径 P 第 3 步验证版本并检查常驻服务。
4. Dockerfile 运行阶段改成：

   ```dockerfile
   COPY pyproject.toml uv.lock ./
   RUN pip install --no-cache-dir "uv>=0.7,<0.8" -i https://mirrors.aliyun.com/pypi/simple/ \
       && uv sync --frozen --no-dev --no-install-project
   ENV PATH="/app/.venv/bin:$PATH"
   ```

   `CMD` 改成 `["uvicorn", "maa_api.main:app", "--host", "0.0.0.0", "--port", "8002", "--forwarded-allow-ips", "127.0.0.1"]`，上面那段注释保留。`FROM`、`COPY alembic.ini`、`COPY config.template.yaml`、`COPY --from=web` 四行不动。
5. 根 `README.md`「准备工作」的 Poetry 改成 uv，「本机启动」最后一行改成 `.venv/bin/python -m uvicorn maa_api.main:app --host 0.0.0.0 --port 8002`，并在它前面加一行 `uv sync --frozen --inexact`。`cd web && pnpm install --frozen-lockfile && pnpm build` 这一行保持原样（`tests/api/test_frontend.py` 钉着）。

#### 4.2.4 可选 extra 的命名约定

写进 `docs/v3/16-部署指南.md` 新增的「## 12. 可选依赖（extra）」一节，正文照抄下面的内容（按实际路径删掉不适用的那一半）：

```markdown
## 12. 可选依赖（extra）

v3 的新依赖按功能做成可选 extra，基础安装不带它们。

- 名字按功能取，只用小写字母、数字与连字符（`^[a-z][a-z0-9-]*$`），例如 `rag-local`；不用库名当 extra 名。一个功能一个 extra。
- 声明位置：Poetry 写在 `[tool.poetry.dependencies]`（`optional = true`）并在 `[tool.poetry.extras]` 里列出；uv 写在 `[project.optional-dependencies]`。锁文件包含全部 extra。
- 安装：Poetry 用 `poetry install --no-root --extras "rag-local"`，全部装用 `--all-extras`；uv 用 `uv sync --frozen --inexact --extra rag-local`，全部装用 `--all-extras`。
- 代码：只在用到的函数里延迟导入；导入失败时返回明确的不可用错误或降级结果，服务启动与其他功能不受影响（与养成规划延迟导入 scipy 的做法一致）。
- 测试：不装任何 extra 时 `.venv/bin/python -m pytest -q` 必须全部通过；必须用到真实库的用例用 `pytest.importorskip`，并在引入它的 spec 里列出。
- Docker：默认镜像不装 extra。第一个引入 extra 的里程碑给 Dockerfile 加构建参数 `MAA_EXTRAS`，并在本节补上用法。
- 每个新依赖在引入它的 spec 里写明理由（决策速查表「依赖」一行）。

预留的名字（要不要真的建，由对应里程碑的 spec 决定）：

| extra | 功能 | 里程碑 |
|---|---|---|
| `otlp` | Trace 导出到 OTLP；手写 HTTP 导出时不需要 | M68 |
| `rag-local` | 本地 ONNX embedding | M76 |
| `im` | IM 通道的平台 SDK；全部用 httpx 直连时不需要 | M85 |
| `mcp` | MCP Client | M91 |
```

#### 4.2.5 活文档迁移

用附录 C 的一次性脚本 `/tmp/maa_m64/copy_live_docs.py`（不进仓库，在仓库根目录执行）。它做四件事：

1. 从 `docs/v2/05-API规范与路由清单.md` 抽出 `## 4. 错误码表` 与 `## 6. 全量路由清单` 两节：每节从标题行开始，到下一个以 `## ` 开头的行之前结束，去掉尾部空行。两节按原顺序拼接，前面加固定的文件头，写成 `docs/v3/14-现行接口清单.md`。§6 是文件的最后一节，后面没有任何内容（路由清单解析会读到文件末尾）。
2. 把 `docs/v2/14-开放API接入指南.md`、`docs/v2/15-部署指南.md` 整份复制成 `docs/v3/15-开放API接入指南.md`、`docs/v3/16-部署指南.md`。
3. 改写三份新文件里的相对链接（围栏代码块里的不动）：按原文件所在的 `docs/v2/` 解析出目标，再从 `docs/v3/` 重算相对路径。效果是 `./13-决策记录.md` 变成 `../v2/13-决策记录.md`，`../ENVIRONMENT.md`、`../../README.md` 不变。从 v2/05 抽出的两节里，指向本文件其他小节的锚点（不以 `#4`、`#6` 开头的，实际只有 `#12-废弃端点与迁移` 一处）改成 `../v2/05-API规范与路由清单.md#…`；`#615-agent` 这类指向 §4、§6 内部的锚点不动。
4. 四处显式替换，改指 v3 的现行副本（脚本按次数校验，次数不对就打印期望与实际次数并以退出码 1 结束）：

   ```text
   docs/v3/15-开放API接入指南.md  2 处
     [05-API规范与路由清单](../v2/05-API规范与路由清单.md)      →  [14-现行接口清单](./14-现行接口清单.md)
   docs/v3/15-开放API接入指南.md  1 处
     [05-API规范与路由清单 §6](../v2/05-API规范与路由清单.md)   →  [14-现行接口清单 §6](./14-现行接口清单.md)
   docs/v3/14-现行接口清单.md     1 处
     [15 §7](../v2/15-部署指南.md)                              →  [16 §7](./16-部署指南.md)
   ```

2026-11-02 在模拟搬迁后的副本上预演（把 `docs/` 按 M63 T2 搬好、跑过 M63 的 `rewrite`）：脚本输出 `wrote docs/v3/14-现行接口清单.md (22 links rewritten)`、`wrote docs/v3/15-开放API接入指南.md (9 links rewritten)`、`wrote docs/v3/16-部署指南.md (11 links rewritten)` 与三行替换记录；三份文件分别 678、478、292 行；改路径后 `tests/domain/test_errors.py`、`tests/domain/test_route_inventory.py`、`tests/test_api_guide_generator.py` 全部通过，`scripts/generate_api_guide.py --check` 输出 `API guide generated sections are current: docs/v3/15-开放API接入指南.md`。§4 的 123 条说明行全部在 `## 4.` 内，只抽两节不改变生成器读到的说明。

`docs/v3/14-现行接口清单.md` 的文件头（脚本写入，原文如下）：

```markdown
# 14 现行接口清单

> 现行的错误码表（§4）与全量路由清单（§6）。`tests/domain/test_errors.py`、`tests/domain/test_route_inventory.py` 逐行比对本文，`scripts/generate_api_guide.py` 从 §4 取错误说明生成接入指南的错误码表。
> M64 从 [v2/05](../v2/05-API规范与路由清单.md) 原样复制了这两节，此后加端点或错误码的里程碑只改本文。正文提到的 §1–§3、§5、§7–§12 指 v2/05 的同名小节。
> 测试按 `## 4. 错误码表` 与 `## 6. 全量路由清单` 两个标题定位：不改这两个标题，§6 之后不追加任何内容。
```

三份 v2 原件各在第 1 行前插入一行冻结说明，后面空一行。三个文件分别写：

```markdown
docs/v2/05-API规范与路由清单.md：
> 已冻结，现行版本见 [v3/14-现行接口清单](../v3/14-现行接口清单.md)。

docs/v2/14-开放API接入指南.md：
> 已冻结，现行版本见 [v3/15-开放API接入指南](../v3/15-开放API接入指南.md)。

docs/v2/15-部署指南.md：
> 已冻结，现行版本见 [v3/16-部署指南](../v3/16-部署指南.md)。
```

每个文件只插入它自己那一行（不带上面的文件名标注）。

改读 v3 副本的代码与文档：

| 文件 | 位置 | 改后 |
|---|---|---|
| `tests/domain/test_errors.py` | `API_SPEC_PATH`（约 39 行） | `REPO_ROOT / "docs" / "v3" / "14-现行接口清单.md"` |
| `tests/domain/test_route_inventory.py` | `API_SPEC_PATH`（约 36 行） | 同上 |
| `scripts/generate_api_guide.py` | `GUIDE_PATH`、`ERROR_SPEC_PATH`（约 15–16 行） | `… / "v3" / "15-开放API接入指南.md"`、`… / "v3" / "14-现行接口清单.md"`；模块 docstring、`--check` 的 help 与 `parse_error_descriptions`、`render_error_table` 的 docstring 里的 `docs/14`、`docs/05` 同步改成 `docs/v3/15`、`docs/v3/14 §4` |
| `web/src/features/api-console/GuideMarkdown.tsx` | 第 2 行 | `import guideMarkdown from '../../../../docs/v3/15-开放API接入指南.md?raw'` |
| `web/src/features/api-console/GuidePage.tsx` | 第 4 行注释 | `docs/14` 改成 `docs/v3/15` |
| `web/src/features/api-console/api-console.page.test.tsx` | 约 197 行 | 正则 `/05-API规范与路由清单/` 改成 `/14-现行接口清单/` |
| 根 `README.md` | 所有链接 | `docs/v2/14-开放API接入指南.md` 改成 `docs/v3/15-开放API接入指南.md`，`docs/v2/15-部署指南.md` 改成 `docs/v3/16-部署指南.md`；文档表里这两行的显示文字改成「15-开放 API 接入指南」「16-部署指南」 |
| `docs/README.md`（M63 新建的总索引） | v3 一节 | 加一行：现行接口清单、开放 API 接入指南、部署指南在 `docs/v3/14`–`16`，M64 起随实现更新 |
| `docs/v3/README.md` | 「文档索引」表 | 在 `13-决策记录` 一行之后加 14、15、16 三行（内容、谁要读按第 31 行那段话写） |

两个测试文件里 `API_SPEC_PATH` 上面加一行注释：`#: 现行清单在 docs/v3/14；docs/v2/05 已冻结。` 测试 docstring 里其他的 `docs/05` 字样不改（[v3 README](../README.md)「给执行者的约定」说明代码注释里的 `docs/NN` 指 v2）。

#### 4.2.6 `scripts/check_doc_links.py`

新文件，职责：扫仓库里所有 Markdown 的相对链接，报告指向不存在文件或目录的链接；与基线比较，只把基线之外的算作新增断链。逻辑来自 [M63 附录 A](./m63-docs-restructure.md) 的 `check` 模式，有两处差异：跳过行内代码（反引号包住的部分不算链接），`BROKEN` 计的是基线之外的条数。

常量与签名（常量照抄，函数体按要点实现）：

```python
#!/usr/bin/env python3
"""检查仓库内 Markdown 的相对链接是否指向存在的文件或目录。

基线 scripts/doc_links_baseline.txt 记录冻结文档里已知的断链，每处一行，
同一处出现几次就写几行。默认模式只报告基线之外的断链，末行 ``BROKEN N``，
N 为 0 时退出码 0，否则 1。锚点不检查。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import unquote

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = REPO_ROOT / "scripts" / "doc_links_baseline.txt"
EXCLUDE_DIRS = frozenset({
    ".git", ".venv", "node_modules", ".pnpm-store", "static", "resource", ".pytest_cache",
    ".idea", ".vscode", ".superpowers", ".archify", "debug", "__pycache__",
})
INLINE_LINK = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
REF_LINK = re.compile(r"^\s*\[[^\]]+\]:\s*(\S+)")
HTML_ATTR = re.compile(r"(?:src|href)=\"([^\"]+)\"")
CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)+?\1")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "#", "/", "data:", "tel:", "javascript:")
BASELINE_HEADER = (
    "# 已知断链基线，scripts/check_doc_links.py 不把这里的条目算作新增断链。\n"
    "# 只允许 docs/v2/、docs/copy-audit/、tests/fixtures/ 下的文件；条目只减不增。\n"
    "# 每行「<文件>: <链接目标>」，同一处断链出现几次就写几行。\n"
)


def iter_markdown(root: Path) -> list[Path]:
    """os.walk 遍历，跳过 EXCLUDE_DIRS，返回排好序的 .md 路径。"""


def is_relative(target: str) -> bool:
    """非空、不以 SKIP_PREFIXES 开头、不含 ``://``。"""


def extract_links(text: str) -> list[str]:
    """按行扫：``` 或 ~~~ 开头的行切换围栏状态，围栏内跳过；
    围栏外先用 CODE_SPAN 去掉行内代码，再收集 INLINE_LINK、HTML_ATTR 与行首的 REF_LINK；
    只返回 is_relative 的目标，保持出现顺序。"""


def find_broken(root: Path) -> list[str]:
    """对每个文件的每个目标：去掉 # 或 ? 之后的部分，空则跳过；
    相对文件所在目录 unquote 后 normpath，root 下不存在就记 "<相对 root 的文件路径>: <原始目标>"。"""


def load_baseline(path: Path) -> Counter[str]:
    """文件不存在返回空 Counter；忽略空行与 # 开头的行。"""


def compare(broken: Iterable[str], baseline: Counter[str]) -> tuple[list[str], list[str]]:
    """返回 (新增, 过期)：新增 = Counter(broken) - baseline，过期 = baseline - Counter(broken)，都展开成排好序的列表。"""


def main(argv: list[str] | None = None) -> int:
    """--all 与 --write-baseline 互斥。
    --write-baseline：把当前全部断链排序后写成基线（先写 BASELINE_HEADER），打印 "BASELINE N"，返回 0。
    --all：打印全部断链（含基线里的），末行 "BROKEN <总数>"，返回 0。
    默认：先逐行打印 "stale: <条目>"，再逐行打印新增断链，末行 "BROKEN <新增数>"；新增为 0 返回 0，否则 1。"""


if __name__ == "__main__":
    sys.exit(main())
```

基线 `scripts/doc_links_baseline.txt`：M64 执行时用 `--write-baseline` 生成一次，然后核对。2026-11-02 在模拟搬迁后的副本上，v3 之外正好 25 条：

- `docs/copy-audit/ai-tells-docs.md` 5 条、`docs/copy-audit/诊断-4-文档与注释.md` 6 条；
- `docs/v2/superpowers/plans/2026-10-10-m34-account-list-contract.md` 3 条、`docs/v2/superpowers/specs/2026-09-28-m14-pwa-push.md` 4 条、`docs/v2/superpowers/specs/2026-09-28-m17-task-param-defaults.md` 6 条；
- `tests/fixtures/core_probe_findings.md` 1 条（`../docs/03-MaaCore内核层设计.md`）。

v3 之内应为 0 条。M63 附录 A 的旧脚本不跳过行内代码，会把 M63 规格 §4.3 反引号里那段示意的链接写法当成一处断链；新脚本不计它。

`tests/test_doc_links.py`（新文件，`tests/` 是包，导入写 `import scripts.check_doc_links as doc_links`，与 `tests/test_api_guide_generator.py` 同样的做法）。用例见 §6。

#### 4.2.7 设置项列表类型的核实

接缝速查与 04 §6 写的是推断。2026-11-02 实测（附录 D 两段命令）：

- `_validate` 的类型表（`setting_service.py` 约 269–276 行）只有 `string`、`integer`、`number`、`integer[]`、`boolean`，用字典下标取，`string[]` 不在表里会抛 `KeyError('string[]')`。
- 走 HTTP 时，`PUT /api/settings` 带 `deployment.cors_origins`（`_FIELDS` 里唯一的 `string[]`）返回 500 `INTERNAL_ERROR`，日志里是 `未捕获异常 … KeyError: 'string[]'`。前端设置页有这个字段的列表编辑器（`web/src/features/settings/settings.test.tsx` 约 397 行会 PUT 它），所以设置页保存「附加浏览器来源白名单」目前会失败。这是 v2 既有缺陷。
- `integer[]` 能用，但它的附加校验是端口专用的：要求元数据里有 `minimum`、`maximum`，报错文案是「端口必须在 1 到 65535 之间」「端口列表不能重复」，重复值返回 422 `SETTING_VALUE_INVALID`。

结论：「不要用列表类型的设置项」这条约定成立，理由改成实测事实。执行者重跑附录 D，结果与上面一致就记录；不一致，按实测写，并在台账说明差异。

### 4.3 接口

无。OpenAPI 快照不变（`.venv/bin/python scripts/dump_openapi.py --check` 退出码 0）。

### 4.4 配置项

无。

### 4.5 行为与算法

边界情况：

- 某个源的 simple 页面能取到，但页面里没有那个固定的 wheel 文件（镜像缺包）→ `file=[no-wheel]`，按不可用处理。
- `poetry lock` 在可达源上超过 900 秒 → `perl` 的 `alarm` 结束进程，退出码 142，按决策树第 3 步换源。
- 重锁后 Poetry 选出的 scipy 要求 Python ≥ 3.12（锁文件里 scipy 条目的 `python-versions` 不覆盖 3.11）→ 停下来问。路径 P 下 Poetry 会为整个 `^3.11` 范围挑一个版本，正常结果是 1.17.x。
- `copy_live_docs.py` 发现目标文件已存在 → 打印 `target files already exist; refusing to overwrite`，退出码 1，不写任何文件。
- 显式替换的次数与期望不符 → 打印期望与实际次数，退出码 1。此时三份文件已经写出，删掉 `docs/v3/14-*`、`15-*`、`16-*` 再查原因。
- 断链检查遇到读不了的字节 → `read_text(errors="replace")`，不因编码问题中断。
- 基线里的条目后来被修好 → 默认模式打印 `stale: …`，`BROKEN` 不受影响；`tests/test_doc_links.py` 的「没有过期条目」用例会失败，要求同步删掉基线里的那一行。
- 同一文件里同一个断链目标出现两次，基线只记了一次 → 第二次算新增。

### 4.6 前端

- `GuideMarkdown.tsx` 第 2 行改导入路径；`GuidePage.tsx` 第 4 行注释改路径；`api-console.page.test.tsx` 约 197 行改正则（见 §4.2.5 的表）。
- 界面文案不变：指南 H1 仍是「开放 API 接入指南」，三处改指 v3/14 的链接在调试台里照旧渲染成指向 `/docs` 的链接，显示文字变成「14-现行接口清单」。

### 4.7 提示词与代理定义

无。

## 5. 实施任务

### T1 包管理实测与决策

- 写范围：只写 `/tmp/maa_m64/`；仓库文件不改。
- 步骤：
  1. 把附录 A 的两个文件存到 `/tmp/maa_m64/`，执行 `sh /tmp/maa_m64/probe.sh | tee /tmp/maa_m64/probe-1.txt`，隔一分钟再跑一遍存成 `probe-2.txt`。
  2. 按 §4.2.1 判定每个源，得出 S 与 s，写进台账草稿（四行原始输出贴上）。
- 验证：`cat /tmp/maa_m64/probe-1.txt /tmp/maa_m64/probe-2.txt` → 8 行，每行都有 `index=[…]` 与 `file=[…]`。
- 完成判据：台账草稿里有两遍探测的原始输出、S、s，以及准备走的路径。

### T2 重建锁文件并同步 `.venv`

- 写范围：`pyproject.toml`、`poetry.lock`（路径 U 下是删除 `poetry.lock`、新增 `uv.lock`）、`.venv/`（不进版本库）。
- 步骤：路径 P 按 §4.2.2 第 1–3 步；路径 U 按 §4.2.3 第 1–3 步。Dockerfile 留到 T5。
- 验证：
  ```bash
  .venv/bin/python /tmp/maa_m64/lockdiff.py /tmp/maa_m64/poetry.lock.before poetry.lock   # 路径 U 换成 uv.lock
  .venv/bin/python -c "import scipy, numpy; print(scipy.__version__, numpy.__version__)"
  .venv/bin/python -m pytest -q tests/services/test_upgrade_planner.py tests/services/test_upgrade_plans.py > /tmp/maa_m64/t2.log 2>&1; echo $?; tail -n 3 /tmp/maa_m64/t2.log
  .venv/bin/python scripts/account_smoke.py > /tmp/maa_m64/account-smoke.log 2>&1; echo $?; tail -n 1 /tmp/maa_m64/account-smoke.log
  ```
  差异只有 `ADDED` 非空；版本与锁文件一致；pytest 退出码 0；冒烟退出码 0、末行 `SMOKE OK`。
- 完成判据：路径 P 下 `POETRY_CACHE_DIR=$(mktemp -d) poetry check --lock` 退出码 0；路径 U 下 `uv lock --check` 退出码 0。

### T3 复制三份活文档

- 写范围：新建 `docs/v3/14-现行接口清单.md`、`docs/v3/15-开放API接入指南.md`、`docs/v3/16-部署指南.md`。
- 步骤：把附录 C 存成 `/tmp/maa_m64/copy_live_docs.py`，在仓库根目录执行 `.venv/bin/python /tmp/maa_m64/copy_live_docs.py`。
- 验证：
  ```bash
  wc -l docs/v3/14-现行接口清单.md docs/v3/15-开放API接入指南.md docs/v3/16-部署指南.md
  rg -n '^## ' docs/v3/14-现行接口清单.md
  rg -n -o '\]\(\./[0-9][0-9]-[^)]*\)' docs/v3/14-现行接口清单.md docs/v3/15-开放API接入指南.md docs/v3/16-部署指南.md
  ```
  行数是 678、478、292（v2 原件在 M63 之后没改过时）；第二条只有 `## 4. 错误码表` 与 `## 6. 全量路由清单` 两行；第三条只有 4 处：`docs/v3/15` 里 3 处 `./14-现行接口清单.md`，`docs/v3/14` 里 1 处 `./16-部署指南.md`。
- 完成判据：脚本退出码 0，打印三行 `wrote …` 与三行 `replaced …`。

### T4 测试、脚本、前端与 README 改读 v3 副本

- 写范围：`tests/domain/test_errors.py`、`tests/domain/test_route_inventory.py`、`scripts/generate_api_guide.py`、`web/src/features/api-console/GuideMarkdown.tsx`、`web/src/features/api-console/GuidePage.tsx`、`web/src/features/api-console/api-console.page.test.tsx`、`README.md`、`docs/README.md`、`docs/v3/README.md`、`docs/v2/05-API规范与路由清单.md`、`docs/v2/14-开放API接入指南.md`、`docs/v2/15-部署指南.md`（后三个只插冻结行）。
- 步骤：按 §4.2.5 的表逐项改；三份 v2 原件开头插冻结行。
- 验证：
  ```bash
  .venv/bin/python -m pytest -q tests/domain/test_errors.py tests/domain/test_route_inventory.py tests/test_api_guide_generator.py > /tmp/maa_m64/t4.log 2>&1; echo $?; tail -n 3 /tmp/maa_m64/t4.log
  .venv/bin/python scripts/generate_api_guide.py --check
  rg -n 'docs/v2/1[45]-' README.md; echo "exit $?"
  rg -n '"v2" / "05-API|"v2" / "14-开放|docs/v2/14-' tests scripts web/src --glob '!**/node_modules/**'; echo "exit $?"
  git diff --stat -- docs/v2
  ```
  pytest 退出码 0；生成器输出 `API guide generated sections are current: docs/v3/15-开放API接入指南.md`；两条 `rg` 都没有输出、`exit 1`；`docs/v2` 的 diff 只有三个文件、每个 2 行新增。
- 完成判据：上面全部符合。前端门禁在 T9 统一跑。

### T5 Dockerfile、环境文档与 extra 约定

- 写范围：`Dockerfile`、`docs/ENVIRONMENT.md`、`docs/v3/16-部署指南.md`；路径 U 另加 `README.md` 的「快速开始」。
- 步骤：
  1. Dockerfile 按 §4.2.2 第 4 步或 §4.2.3 第 4 步。
  2. `docs/ENVIRONMENT.md`「基础工具链」末尾追加一条 M64 实测：两遍探测结果、选的路径与源、重锁耗时、新增的两个包与版本、「其余 58 个包零漂移」、`.venv` 同步前后的 scipy/numpy 版本、Python 3.11 与 ≥ 3.12 能装的 scipy 版本不同这一事实。第 18 行附近 M27 那条末尾加「（M64 复测不成立，见本节末条）」。路径 U 下「常驻服务与 .venv」第 26 行附近的 `poetry install --no-root` 改成 `uv sync --frozen --inexact`。
  3. `docs/v3/16-部署指南.md`：§8 的「本机的安装偏离（重要）」整段换成锁文件现状（锁文件含 scipy、numpy 与版本，按锁文件安装的环境都带养成规划，结论见 13 §8）；§8「镜像体积」一段里的安装命令按实际路径改；路径 U 下 §1 Docker 一节补一句镜像用 uv 安装。文末加 §4.2.4 的「## 12. 可选依赖（extra）」。
- 验证：
  ```bash
  .venv/bin/python -m pytest -q tests/api/test_frontend.py > /tmp/maa_m64/t5.log 2>&1; echo $?; tail -n 3 /tmp/maa_m64/t5.log
  rg -n 'poetry.lock|uv.lock' Dockerfile
  rg -n '^## 12\. 可选依赖' docs/v3/16-部署指南.md
  ```
  pytest 退出码 0；Dockerfile 的 `COPY` 行写的是实际使用的锁文件；`docs/v3/16` 有第 12 节。
- 补充检查（不作硬性验证）：`test -x /Applications/Docker.app/Contents/Resources/bin/docker && /Applications/Docker.app/Contents/Resources/bin/docker info > /dev/null 2>&1 && echo docker-ok`。输出 `docker-ok` 时执行 `/Applications/Docker.app/Contents/Resources/bin/docker build -t maa-api:m64 . > /tmp/maa_m64/docker-build.log 2>&1; echo $?`，把结果与 `tail -n 20` 记进台账；没有 Docker 就在台账写「未验证」。
- 完成判据：上面全部符合。

### T6 断链检查入库

- 写范围：新建 `scripts/check_doc_links.py`、`scripts/doc_links_baseline.txt`、`tests/test_doc_links.py`。
- 步骤：
  1. 按 §4.2.6 实现脚本。
  2. `.venv/bin/python scripts/check_doc_links.py --all > /tmp/maa_m64/links-all.txt; tail -n 1 /tmp/maa_m64/links-all.txt`，核对：断链只出现在 `docs/v2/`、`docs/copy-audit/`、`tests/fixtures/` 下，共 25 条（分布见 §4.2.6）。有 v3 或其他位置的断链，先修好它们再继续；v2、copy-audit、tests 三处多于 25 条，停下来问。
  3. `.venv/bin/python scripts/check_doc_links.py --write-baseline` → `BASELINE 25`（以第 2 步核对后的数为准）。
  4. 写 `tests/test_doc_links.py`（§6）；`BASELINE_LIMIT` 取第 3 步的数。
- 验证：
  ```bash
  .venv/bin/python scripts/check_doc_links.py; echo "exit $?"
  .venv/bin/python -m pytest -q tests/test_doc_links.py > /tmp/maa_m64/t6.log 2>&1; echo $?; tail -n 3 /tmp/maa_m64/t6.log
  ```
  第一条末两行是 `BROKEN 0` 与 `exit 0`；pytest 退出码 0。再做一次反向验证：临时在 `docs/v3/16-部署指南.md` 末尾加一行指向 `./不存在.md` 的链接，脚本输出该条与 `BROKEN 1`、退出码 1；删掉这一行。
- 完成判据：上面全部符合，临时改动已撤回（`git diff docs/v3/16-部署指南.md` 里没有那一行）。

### T7 核实列表类型设置项

- 写范围：只写台账草稿；仓库代码不改。
- 步骤：把附录 D 存成 `/tmp/maa_m64/probe_string_list.py`，执行 §4.2.7 列的两段命令，把输出贴进台账。
- 验证：输出与 §4.2.7 一致（`KeyError` 一行、`500 INTERNAL_ERROR`、`422 SETTING_VALUE_INVALID`、`200 OK`）。
- 完成判据：台账「验证证据」有原始输出；结论在 T8 写进 13 §8 与 04 §6。

### T8 文档同步与 AGENTS.md

- 写范围：`docs/v3/13-决策记录.md`、`docs/v3/04-接口与事件协议.md`、`AGENTS.md`。
- 步骤：
  1. 13 §8 按 §9 追加三条，13 §9 的 Q8 一行最后一列改成「已定，见 §8.A」。
  2. 04 §6 那句「`setting_service._validate` 的类型表里没有 `string[]`，PUT 会报错（推断，见 M64 核实）」改成：「`setting_service._validate` 的类型表只有 string、integer、number、boolean 与端口专用的 integer[]；`string[]` 查表时抛 `KeyError`，PUT 返回 500 `INTERNAL_ERROR`（M64 实测，13 §8.B；既有的 `deployment.cors_origins` 也受影响）」。
  3. `AGENTS.md`「## 命令」一节，在 `scripts/*_smoke.py` 那一行之后加一行：`- 文档断链检查：.venv/bin/python scripts/check_doc_links.py，末行 BROKEN 0 表示没有基线之外的断链；--all 列出全部断链（含 scripts/doc_links_baseline.txt 里的已知项）。`（命令与文件名用反引号包住）
- 验证：`rg -n 'check_doc_links' AGENTS.md` 一行；`rg -n '推断，见 M64' docs/v3/04-接口与事件协议.md` 没有输出；`rg -n '^### 8\.' docs/v3/13-决策记录.md` 能看到新加的三条。
- 完成判据：上面全部符合。

### T9 全量验证、台账与合并

- 写范围：`docs/v3/ledgers/m64-engineering-baseline.md`（新建）。
- 步骤：跑 §7 的全部命令；按 [specs/README](./README.md) §4 写台账（必须有：两遍探测原始输出、选的路径与理由、锁文件差异输出、`.venv` 前后版本、常驻服务是否加载、断链基线的 25 条分布、附录 D 输出、Docker 补充检查结果、Rulings I made）。然后：
  ```bash
  git add -A pyproject.toml Dockerfile README.md AGENTS.md docs/README.md docs/ENVIRONMENT.md docs/v2 docs/v3 scripts tests web/src/features/api-console
  git add -A poetry.lock 2>/dev/null || true
  git add -A uv.lock 2>/dev/null || true
  git status --porcelain | grep -v '^?? docs/ideas/'   # 只剩已暂存的改动，docs/ideas/ 保持未跟踪
  git commit -m "chore(m64): 工程整备：锁文件补齐 scipy 与 numpy、活文档迁入 docs/v3、断链检查入库"
  git checkout dev && git merge --no-ff engineering-baseline -m "merge: 工程整备（M64，见 docs/v3/13 §8.A–§8.C）"
  git tag v3-m64
  git branch -d engineering-baseline
  ```
- 完成判据：[12 §4](../12-实施路线与里程碑.md) 的完成标准全部满足。

## 6. 测试清单

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/test_doc_links.py` | `test_repository_has_no_new_broken_doc_links` | `compare(find_broken(REPO_ROOT), load_baseline(BASELINE_PATH))` 的新增列表为空；失败信息逐条列出 `文件: 目标` |
| `tests/test_doc_links.py` | `test_baseline_has_no_stale_entries` | 同一次比较的过期列表为空（修好的断链必须从基线删掉） |
| `tests/test_doc_links.py` | `test_baseline_stays_in_frozen_areas_and_never_grows` | 基线条目数 ≤ `BASELINE_LIMIT`（T6 核对后的数，预期 25）；每条都以 `docs/v2/`、`docs/copy-audit/`、`tests/fixtures/` 之一开头 |
| `tests/test_doc_links.py` | `test_extract_links_skips_code_and_external_targets` | 输入一段含行内链接、图片、引用式定义、HTML 的 `src` 属性、行内代码里的链接、围栏代码块里的链接、`https://`、`#锚点`、`mailto:` 的文本；只返回行内、图片、引用式、`src` 四个相对目标，顺序与出现顺序一致 |
| `tests/test_doc_links.py` | `test_compare_counts_duplicate_occurrences` | 基线 `Counter({"a.md: x.md": 1})`，当前 `["a.md: x.md", "a.md: x.md", "b.md: y.md"]` → 新增 `["a.md: x.md", "b.md: y.md"]`，过期 `[]` |
| `tests/test_doc_links.py` | `test_main_prints_broken_count_as_last_line` | `main([])` 返回 0；`capsys` 捕获的最后一行是 `BROKEN 0` |
| `tests/domain/test_errors.py` | 既有全部用例 | 只改 `API_SPEC_PATH`，用例与计数常量不变 |
| `tests/domain/test_route_inventory.py` | 既有全部用例 | 只改 `API_SPEC_PATH` |
| `tests/test_api_guide_generator.py` | 既有全部用例 | 不改；`ERROR_SPEC_PATH` 随生成器指向 v3/14 |
| `web/src/features/api-console/api-console.page.test.tsx` | `K1 loads operations from /openapi.json, …` | 只改链接名正则，其余断言不变 |
| `tests/api/test_frontend.py` | `test_dockerfile_builds_frontend_then_copies_only_output_to_python_runtime` | 不改；Dockerfile 改完仍通过 |

## 7. 验收

```bash
.venv/bin/python -m pytest -q > /tmp/maa_m64/pytest.log 2>&1; echo $?; tail -n 30 /tmp/maa_m64/pytest.log
.venv/bin/python scripts/dump_openapi.py --check; echo $?
.venv/bin/python scripts/generate_api_guide.py --check; echo $?
cd web && pnpm install --frozen-lockfile > /tmp/maa_m64/pnpm.log 2>&1 && pnpm gen:api:check > /tmp/maa_m64/genapi.log 2>&1 && pnpm typecheck > /tmp/maa_m64/tc.log 2>&1 && pnpm test --run > /tmp/maa_m64/vitest.log 2>&1 && pnpm build > /tmp/maa_m64/build.log 2>&1; echo $?; cd ..
.venv/bin/python scripts/frontend_smoke.py > /tmp/maa_m64/fe-smoke.log 2>&1; echo $?; tail -n 1 /tmp/maa_m64/fe-smoke.log
.venv/bin/python scripts/account_smoke.py > /tmp/maa_m64/account-smoke.log 2>&1; echo $?; tail -n 1 /tmp/maa_m64/account-smoke.log
.venv/bin/python scripts/check_doc_links.py | tail -n 1
POETRY_CACHE_DIR=$(mktemp -d) poetry check --lock; echo $?      # 路径 U 换成：uv lock --check; echo $?
git diff --check; echo $?
```

期望：pytest 与前置检查 0.10 的基线相比不新增失败；`dump_openapi --check` 退出码 0（本里程碑不改接口）；指南生成器 `current`；前端五步退出码 0；两个冒烟末行 `SMOKE OK`；断链末行 `BROKEN 0`；锁文件检查退出码 0；`git diff --check` 退出码 0。

本里程碑特有：

- `grep -c -E '^name = "(scipy|numpy)"' poetry.lock` 输出 `2`（路径 U 用 `uv.lock`，输出 `4`）。
- `rg -n '^## ' docs/v3/14-现行接口清单.md` 只有两行，且 §6 是最后一节。
- 浏览器里打开 `/more/api-console/guide`：标题「开放 API 接入指南」，错误码表与 M64 之前一致（本里程碑不加码），原「05-API规范与路由清单」的三处链接显示为「14-现行接口清单」。手动检查，结果记进台账；无法打开浏览器时写「未验证」。

## 8. 风险与回滚

- 重锁带出版本漂移：决策树第 4、5 步拦住；回滚 `git checkout -- pyproject.toml poetry.lock`。
- `.venv` 同步后养成规划行为变化（scipy 1.18 → 1.17）：T2 的规划测试与 `account_smoke.py` 覆盖；回滚 `.venv/bin/python -m pip install "scipy==1.18.1" "numpy==2.5.3"`，同时撤回锁文件改动并停下来问。
- 常驻服务在 `.venv` 换包后出现导入错误：只影响养成规划的延迟导入；`./scripts/maa-api-service.sh reload` 恢复。不在本里程碑里自动重启。
- 复制脚本把链接改错：T4 的测试、生成器 `--check` 与 T6 的断链检查会暴露；删掉三份 v3 文件重跑脚本。
- 前端构建找不到新路径：`vite.config.ts` 的 `server.fs.allow` 已放开到仓库根，`?raw` 导入 `docs/v3/` 与原来的 `docs/` 同级；`pnpm build` 失败时检查路径层数（`../../../../docs/v3/…`）。
- 整体回滚：`git revert -m 1 <merge>`；`.venv` 需要另按上面的命令恢复。

## 9. 文档同步

- 13 §8 追加三条（编号接在现有条目之后，M63 没有偏离时从 8.1 开始；下面用 8.A、8.B、8.C 指代）：
  - 8.A 包管理结论。原定：Q8 待定，v2/15 §8 记本机镜像不可达、锁文件缺 scipy。实际：两遍探测结果一句话；走路径 P 或 U、源 s；锁文件新增的两个包与版本，其余 58 个包不变；`.venv` 已同步；extra 命名约定见 `docs/v3/16` §12。理由：§4.2.1 的决策树与实际命中的分支。影响：列出改过的文件；Q8 关闭。
  - 8.B 列表类型设置项实测。原定：04 §6 推断 PUT 会报错。实际：`string[]` → `KeyError` → 500 `INTERNAL_ERROR`，既有的 `deployment.cors_origins` 受影响；`integer[]` 只适合端口。理由：附录 D 实测。影响：04 §6 改写；缺陷未修，写进台账「遗留」，等用户决定由哪个里程碑修。
  - 8.C 活文档迁移与断链门禁的两处细则：v3 副本里的相对链接按 §4.2.5 改写，三处改指 v3/14、一处改指 v3/16，前端测试的链接名正则随之改；三份 v2 原件各加一行冻结说明（v2 只读规则的例外）。`check_doc_links.py` 跳过行内代码，`BROKEN` 计基线之外的条数，基线 25 条只减不增。
- `docs/v3/README.md` 文档索引加 14、15、16 三行；顶部状态行补「M64 已完成」。
- `docs/v3/04-接口与事件协议.md` §6 一句（T8 第 2 步）。§7 不改，它描述的就是 M64 之后的状态。
- 计数：本里程碑不改工具（84 / 模型可见 83）、错误码（124 / 123 / 20 节）、HTTP 路由（121 条）、业务表（28 张）、OpenAPI tag（19 个）；新增测试文件 1 个（`tests/test_doc_links.py`，6 个用例）。以执行时实际值为准。

## 10. 停下来问

- 四个源两遍探测都不可用。
- 锁文件差异出现 `REMOVED` 或 `CHANGED`（任何路径）。
- 路径 P 与路径 U 都锁不出来。
- 重锁结果要求提高 Python 下限或换 Docker 基底镜像。
- `poetry install --dry-run`（或 `uv sync`）要动 numpy、scipy 之外的包。
- 断链核对时，v2、copy-audit、tests 三处的断链多于 25 条，或出现在这三处之外且不是本里程碑造成的。
- 复制后 `generate_api_guide.py --check` 报 `stale`，或错误码、路由测试失败，且原因不是路径没改对。
- 附录 D 的结果与 §4.2.7 不一致，且差异意味着 04 §6 的约定要改。
- 用户要求顺手修 `deployment.cors_origins` 的 500（属于改对外行为）。
- 全量测试出现与本里程碑无关的新失败。

## 需要补进契约的名字

| 名字 | 种类 | 用途 |
|---|---|---|
| `scripts/check_doc_links.py` 的 `--all`、`--write-baseline` | 脚本参数 | 列出全部断链；生成基线 |
| `scripts/doc_links_baseline.txt` | 文件 | 已知断链基线，只减不增 |
| `tests/test_doc_links.py` 的 `BASELINE_LIMIT` | 测试常量 | 基线条数上限 |
| `otlp`、`rag-local`、`im`、`mcp` | 预留的 extra 名 | 见 §4.2.4；由 M68、M76、M85、M91 的 spec 决定是否启用 |
| `docs/v3/16-部署指南.md` §12「可选依赖（extra）」 | 文档小节 | extra 约定的落点 |

## 附录 A：`/tmp/maa_m64/probe.sh` 与 `/tmp/maa_m64/wheel_url.py`

```python
# /tmp/maa_m64/wheel_url.py
import re
import sys
import urllib.parse

index, page = sys.argv[1], sys.argv[2]
html = open(page, encoding="utf-8", errors="replace").read()
match = re.search(r'href="([^"#]*scipy-1\.17\.1-cp311-cp311-manylinux[^"#]*x86_64\.whl)', html)
print(urllib.parse.urljoin(index.rstrip("/") + "/scipy/", match.group(1)) if match else "")
```

```sh
#!/bin/sh
# /tmp/maa_m64/probe.sh：在仓库里任意位置执行
cd "$(git rev-parse --show-toplevel)" || exit 1
for pair in aliyun=https://mirrors.aliyun.com/pypi/simple tuna=https://pypi.tuna.tsinghua.edu.cn/simple ustc=https://mirrors.ustc.edu.cn/pypi/simple pypi=https://pypi.org/simple; do
  name=${pair%%=*}; url=${pair#*=}
  index=$(curl -sS -o "/tmp/maa_m64/$name.html" --connect-timeout 10 --max-time 30 -w '%{http_code} %{time_total}' "$url/scipy/" 2>&1 | tail -n 1)
  wheel=$(.venv/bin/python /tmp/maa_m64/wheel_url.py "$url" "/tmp/maa_m64/$name.html" 2>/dev/null)
  if [ -n "$wheel" ]; then
    file=$(curl -sS -o /dev/null -r 0-65535 --connect-timeout 10 --max-time 30 -w '%{http_code} %{time_total}' "$wheel" 2>&1 | tail -n 1)
  else
    file="no-wheel"
  fi
  echo "$name index=[$index] file=[$file]"
done
```

## 附录 B：`/tmp/maa_m64/lockdiff.py` 与 `/tmp/maa_m64/pins.py`

```python
# /tmp/maa_m64/lockdiff.py：比较两份锁文件（poetry.lock 或 uv.lock）里的包版本
import sys
import tomllib


def load(path: str) -> dict[str, set[str]]:
    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    versions: dict[str, set[str]] = {}
    for package in data.get("package", []):
        source = package.get("source", {})
        if source.get("editable") or source.get("virtual"):
            continue
        versions.setdefault(package["name"], set()).add(package["version"])
    return versions


old, new = load(sys.argv[1]), load(sys.argv[2])
added = {name: sorted(new[name]) for name in sorted(set(new) - set(old))}
removed = {name: sorted(old[name]) for name in sorted(set(old) - set(new))}
changed = {
    name: (sorted(old[name]), sorted(new[name]))
    for name in sorted(set(old) & set(new))
    if old[name] != new[name]
}
print(f"OLD {len(old)} NEW {len(new)}")
print(f"ADDED {added}")
print(f"REMOVED {removed}")
print(f"CHANGED {changed}")
```

```python
# /tmp/maa_m64/pins.py：把旧 poetry.lock 的版本写成临时的 uv 约束段（只在路径 U 用）
import sys
import tomllib

with open(sys.argv[1], "rb") as handle:
    packages = tomllib.load(handle)["package"]
print("\n[tool.uv]")
print("constraint-dependencies = [")
for name, version in sorted((p["name"], p["version"]) for p in packages):
    print(f'  "{name}=={version}",')
print("]")
```

## 附录 C：`/tmp/maa_m64/copy_live_docs.py`

```python
#!/usr/bin/env python3
"""M64 one-off: copy the three v2 live docs into docs/v3 and rewrite relative links."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote

ROOT = Path.cwd()
V2 = "docs/v2"
V3 = "docs/v3"
API_SPEC = f"{V2}/05-API规范与路由清单.md"
LIVE_LIST = f"{V3}/14-现行接口清单.md"
COPIES = {
    f"{V2}/14-开放API接入指南.md": f"{V3}/15-开放API接入指南.md",
    f"{V2}/15-部署指南.md": f"{V3}/16-部署指南.md",
}
SECTIONS = ("## 4. 错误码表", "## 6. 全量路由清单")
KEEP_ANCHOR = re.compile(r"^#[46]\d*-")
INLINE_LINK = re.compile(r"(\]\()([^)\s]+)((?:\s+\"[^\"]*\")?\))")
SKIP = ("http://", "https://", "mailto:", "#", "/", "data:", "tel:", "javascript:")
LIVE_LIST_HEADER = """# 14 现行接口清单

> 现行的错误码表（§4）与全量路由清单（§6）。`tests/domain/test_errors.py`、`tests/domain/test_route_inventory.py` 逐行比对本文，`scripts/generate_api_guide.py` 从 §4 取错误说明生成接入指南的错误码表。
> M64 从 [v2/05](../v2/05-API规范与路由清单.md) 原样复制了这两节，此后加端点或错误码的里程碑只改本文。正文提到的 §1–§3、§5、§7–§12 指 v2/05 的同名小节。
> 测试按 `## 4. 错误码表` 与 `## 6. 全量路由清单` 两个标题定位：不改这两个标题，§6 之后不追加任何内容。

"""
EXPLICIT = [
    (f"{V3}/15-开放API接入指南.md",
     "[05-API规范与路由清单](../v2/05-API规范与路由清单.md)",
     "[14-现行接口清单](./14-现行接口清单.md)", 2),
    (f"{V3}/15-开放API接入指南.md",
     "[05-API规范与路由清单 §6](../v2/05-API规范与路由清单.md)",
     "[14-现行接口清单 §6](./14-现行接口清单.md)", 1),
    (LIVE_LIST, "[15 §7](../v2/15-部署指南.md)", "[16 §7](./16-部署指南.md)", 1),
]


def extract_section(lines: list[str], heading: str) -> list[str]:
    start = next(i for i, line in enumerate(lines) if line.startswith(heading))
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    body = lines[start:end]
    while body and not body[-1].strip():
        body.pop()
    return body + ["\n"]


def relink(lines: list[str], src_dir: str, dst_dir: str, anchor_doc: str | None) -> tuple[list[str], int]:
    count = 0

    def fix(target: str) -> str:
        nonlocal count
        if target.startswith("#"):
            if anchor_doc and not KEEP_ANCHOR.match(target):
                count += 1
                return os.path.relpath(anchor_doc, dst_dir) + target
            return target
        if target.startswith(SKIP) or "://" in target:
            return target
        cut = min([i for i in (target.find("#"), target.find("?")) if i >= 0], default=len(target))
        part, suffix = target[:cut], target[cut:]
        resolved = os.path.normpath(os.path.join(src_dir, unquote(part)))
        new = os.path.relpath(resolved, dst_dir).replace(os.sep, "/")
        if not new.startswith("../"):
            new = "./" + new
        if "%" in part:
            new = quote(new, safe="/._-~")
        if new + suffix != target:
            count += 1
        return new + suffix

    out, fence = [], False
    for line in lines:
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        elif not fence:
            line = INLINE_LINK.sub(lambda m: m.group(1) + fix(m.group(2)) + m.group(3), line)
        out.append(line)
    return out, count


def main() -> int:
    if (ROOT / LIVE_LIST).exists() or any((ROOT / dst).exists() for dst in COPIES.values()):
        print("target files already exist; refusing to overwrite", file=sys.stderr)
        return 1
    spec_lines = (ROOT / API_SPEC).read_text(encoding="utf-8").splitlines(keepends=True)
    body: list[str] = []
    for heading in SECTIONS:
        body += extract_section(spec_lines, heading)
    body, n = relink(body, V2, V3, API_SPEC)
    (ROOT / LIVE_LIST).write_text(LIVE_LIST_HEADER + "".join(body).rstrip("\n") + "\n", encoding="utf-8")
    print(f"wrote {LIVE_LIST} ({n} links rewritten)")
    for src, dst in COPIES.items():
        lines = (ROOT / src).read_text(encoding="utf-8").splitlines(keepends=True)
        lines, n = relink(lines, V2, V3, None)
        (ROOT / dst).write_text("".join(lines), encoding="utf-8")
        print(f"wrote {dst} ({n} links rewritten)")
    for path, old, new, expected in EXPLICIT:
        text = (ROOT / path).read_text(encoding="utf-8")
        if text.count(old) != expected:
            print(f"{path}: expected {expected} x {old!r}, found {text.count(old)}", file=sys.stderr)
            return 1
        (ROOT / path).write_text(text.replace(old, new), encoding="utf-8")
        print(f"{path}: replaced {expected} x {old}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

## 附录 D：列表类型设置项的两段实测命令

第一段直接调 `_validate`：

```bash
.venv/bin/python - <<'EOF'
from maa_api.services.setting_service import SettingService
service = SettingService.__new__(SettingService)
for key, value in [("deployment.cors_origins", ["https://a.example"]), ("adb.common_ports", [5555, 16384]), ("adb.common_ports", [5555, 5555])]:
    try:
        print(key, value, "->", repr(service._validate(key, value)))
    except Exception as exc:
        print(key, value, "-> raised", type(exc).__name__, getattr(exc, "code", ""), str(exc))
EOF
```

2026-11-02 输出：

```text
deployment.cors_origins ['https://a.example'] -> raised KeyError  'string[]'
adb.common_ports [5555, 16384] -> [5555, 16384]
adb.common_ports [5555, 5555] -> raised AppError SETTING_VALUE_INVALID SETTING_VALUE_INVALID: 端口列表不能重复
```

第二段走 HTTP（临时库与临时配置都在 `tempfile.mkdtemp()` 里，不碰 `resource/`），存成 `/tmp/maa_m64/probe_string_list.py` 后执行 `.venv/bin/python /tmp/maa_m64/probe_string_list.py 2>&1 | tail -n 4`：

```python
"""One-off probe: what PUT /api/settings does with a string[] setting."""
import asyncio
import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import SQLModel

import maa_api.db.models  # noqa: F401
import maa_api.settings as settings_module
from maa_api.api.errors import register_exception_handlers
from maa_api.api.routers import settings as settings_router
from maa_api.db import session as db_session
from maa_api.services.setting_service import SettingService

tmp = Path(tempfile.mkdtemp())
config_path = tmp / "config.yaml"
config_path.write_text('app:\n  access_token: ""\n', encoding="utf8")
settings_module.set_settings(settings_module.load_settings(config_path))
engine = db_session.make_engine(f"sqlite+aiosqlite:///{tmp / 'probe.db'}")
factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def create_schema() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)


asyncio.run(create_schema())
engine.sync_engine.dispose()
app = FastAPI()
register_exception_handlers(app)
app.include_router(settings_router.router)
app.state.setting_service = SettingService(factory, device_manager=None, settings_path=config_path, env={})
with TestClient(app, raise_server_exceptions=False) as client:
    for items in ({"deployment.cors_origins": ["https://a.example"]}, {"adb.common_ports": [5555, 5555]}, {"adb.common_ports": [5555, 16384]}):
        response = client.put("/api/settings", json={"items": items})
        body = response.json()
        print(list(items)[0], items[list(items)[0]], "->", response.status_code, body.get("error", {}).get("code", "OK"))
```

2026-11-02 输出的最后四行（前面还有 httpx 弃用警告与关闭连接时的噪音日志，与结论无关）：

```text
KeyError: 'string[]'
deployment.cors_origins ['https://a.example'] -> 500 INTERNAL_ERROR
adb.common_ports [5555, 5555] -> 422 SETTING_VALUE_INVALID
adb.common_ports [5555, 16384] -> 200 OK
```
