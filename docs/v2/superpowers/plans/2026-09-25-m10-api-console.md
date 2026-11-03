# M10 开放 API 调试台实施计划

> **实施者：** 使用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans` 逐项执行；每项按 checkbox 跟踪，遵循 TDD。

**目标：** 在 React 前端交付移动优先的 OpenAPI 调试台，支持安全的历史/收藏、API 与日志联动、接入指南及收藏转定时任务。

**架构：** FastAPI 新增收藏持久化 API 与请求追踪；实时日志协议增加可选 `request_id`，调试台使用独立 WebSocket 客户端按 Base URL 连接；前端依 OpenAPI 动态构造接口树，并从同一份 `docs/14-开放API接入指南.md` 展示指南。

**技术栈：** FastAPI、SQLModel、Alembic、pytest；Vite、React、TypeScript、openapi-fetch、CodeMirror 6、Vitest。

**规格：** `docs/10-开放API与调试台.md`、`docs/12-实施计划与里程碑.md`、`docs/README.md` 与 `docs/13-决策记录.md`；本计划记录经用户确认的 M10 边界。

## 全局约束

- 工作分支为 `m10-api-console`，基于 `refactor/v2`；不创建 worktree、不 push、不合并至 `dev`。
- 收藏 API：`GET/POST /api/snippets`、`GET/PUT/DELETE /api/snippets/{snippet_id}`；create=201、delete=204、缺失=404、重名=409，错误体遵守统一格式。
- 收藏名必填、trim 后长度 1–64、区分大小写唯一；收藏及历史不得持久化 `Authorization`、`X-Token`、`Cookie`、API key、password、secret 类凭据头或 query `token`。任意 JSON body 为保证重放保持原样，不做通用递归脱敏。
- `/openapi.json` 动态驱动接口树；请求体为合法 JSON；请求校验警告不得阻止发送；WebSocket 远程联动使用隔离客户端。
- 先生成并确认 `request_id` 的 HTTP → 日志 → WebSocket 并发隔离探针，再实现生产逻辑。
- 后端接口改动后生成 `web/openapi.json` 和前端类型；主 agent 复跑任务验证与后端测试。

## 任务

### Task 1：请求追踪与实时日志联动

**文件范围：** `maa_api/main.py`、`maa_api/services/log_hub.py`、`maa_api/api/ws.py`、相关 `tests/api/` 与 `tests/services/`、`scripts/api_smoke.py`。

- [x] 先写并运行并发请求探针/测试，确认当前缺少回显或日志关联的失败。
- [x] 实现 request-id 上下文、响应 `X-Request-Id` / `X-Response-Time-Ms`、日志事件字段与 CORS 允许/暴露头。
- [x] 测试 HTTP 与 WebSocket 关联、并发隔离、缺失 ID 生成、远程 CORS 预检；额外覆盖未捕获 500 与服务重启后的日志 stream-id 续传。

### Task 2：收藏持久化与 REST API

**文件范围：** `maa_api/db/models.py`、新 Alembic migration、repository/service/router、相关 `tests/db/` 与 `tests/api/`、OpenAPI 产物。

- [x] 先写 CRUD、鉴权、名字规范化/409、404 与凭据清理的失败测试。
- [x] 新建 `api_snippet` 表、仓储、服务和鉴权 CRUD；后端写入与读取前清除凭据头和 query token；按查询模式为列表排序加索引。
- [x] 更新 OpenAPI 快照及生成的 TypeScript 类型；扩充 `scripts/api_smoke.py` 覆盖 M10 合约。

### Task 3：前端调试台

**文件范围：** 新建 `web/src/features/api-console/`，路由/“更多”入口、定时任务预填、依赖与前端测试。

- [x] 先写失败测试：schema 驱动接口树、历史/收藏凭据头脱敏、cURL 认证渠道、响应分类、日志筛选与远程连接隔离。
- [x] 实现请求构造器（CodeMirror/JSON schema 提示校验）、JSON 与图片响应、错误码卡片、历史 100 条/单条 16KB、收藏 CRUD、cURL、Base URL 与会话临时 token。
- [x] 独立 WebSocket 只更新调试台状态；请求日志按 request-id 筛选，流水线日志按 pipeline-id 接续；收藏可把任务模板预填进定时任务页。按 review 修复 server ping、有限重连、服务端日志过滤、服务重启续传、指南路由全宽布局和宽凭据头脱敏。
- [x] 用 375px、800px 平板和 1280px jsdom viewport 测试移动分步/抽屉、避免平板三栏溢出、桌面三栏；未做真实浏览器截图巡检，真机检查另记为未执行补充项。

### Task 4：文档、验收记录与集成

**文件范围：** `docs/04`、`05`、`06`、`10`、`12`、`13 §8`、`docs/README.md`、新增 `docs/14-开放API接入指南.md`。

- [x] 更新收藏模型/API、request-id / stream-id 日志字段、CORS 头、JSON 说明呈现与 M10 范围/验收；接入指南错误码与 tag 表由 `scripts/generate_api_guide.py` 生成，使用 `--check` 校验。
- [x] 指南仅描述已交付 REST/WebSocket；确认工作流等待 M11、MCP 等待 M12，并由前端 Markdown 页复用同一文件。
- [x] 补记 M10 实施偏离、验证结果与未完成的真机补充检查。

### Task 5：硬验收与发布

- [x] 主 agent 运行 `.venv/bin/python -m pytest -q`、`.venv/bin/python scripts/dump_openapi.py --check`、`.venv/bin/python scripts/generate_api_guide.py --check`、完整前端安装/生成检查/typecheck/test/build、`.venv/bin/python scripts/api_smoke.py`、`.venv/bin/python scripts/frontend_smoke.py`，均通过。
- [x] 真机补充检查记为未执行：Mac GUI 当前锁定、`simctl` 不可用；Android 可由 ADB 枚举但无法做可见页面检查。Tailscale 验收留到 M15。
- [x] 通过审查后提交实现与 ledger，使用 `git merge --no-ff` 合回 `refactor/v2` 并打 `v2-m10`；M10 合并提交为 `434470750ad3422361c5fa802f3a8c0d362ea5df`，合并后硬验收通过。
