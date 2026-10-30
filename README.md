<!-- markdownlint-disable -->

<div align="center">

<img alt="LOGO" src="docs/assets/logo-512.png" width="180" />

# MAA-API

<br>
<div>
    <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-%233776AB?logo=python&logoColor=white">
    <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.138%2B-%23009688?logo=fastapi&logoColor=white">
</div>
<div>
    <img alt="platform" src="https://img.shields.io/badge/platform-Docker%20%7C%20Linux%20%7C%20macOS-blueviolet">
    <img alt="MaaCore" src="https://img.shields.io/badge/MaaCore-MaaAssistantArknights-%230066CC">
    <img alt="license" src="https://img.shields.io/github/license/ChthollyWn/MAA-API">
</div>
<div>
    <img alt="commit activity" src="https://img.shields.io/github/commit-activity/m/ChthollyWn/MAA-API?color=%23ff69b4">
    <img alt="last commit" src="https://img.shields.io/github/last-commit/ChthollyWn/MAA-API?color=%2374b9ff">
    <img alt="stars" src="https://img.shields.io/github/stars/ChthollyWn/MAA-API?style=social">
</div>
<br>

[文档](docs/README.md) | [开放 API](docs/14-开放API接入指南.md) | [部署指南](docs/15-部署指南.md) | [决策记录](docs/13-决策记录.md)

MAA-API 是 MaaAssistantArknights 的非官方 Web 服务封装

以 MaaCore 为内核，自带 Web 控制台、开放 API 与内置 Agent

把明日方舟的日常任务，变成可编排、可调用、可远程管理的服务 ✿✿ヽ(°▽°)ノ✿

</div>

<!-- markdownlint-restore -->

MaaCore 负责截图识别与操作游戏，本项目负责任务调度、数据持久化、Web 界面与开放 API。它不是 MAA 客户端的替代品，而是给你的脚本、面板和自动化流程提供一个稳定的 HTTP / WebSocket 入口。

## 亮点功能

- 🖥️ **Web 控制台**：移动端优先的 PWA 界面，任务队列、流水线与定时任务编排，设备画面、实时日志与任务结果一站查看
- 🔌 **开放 API**：REST + WebSocket，`/openapi.json` 契约与内置调试台，token 鉴权与来源限流，接入见 [开放 API 指南](docs/14-开放API接入指南.md)
- 🤖 **内置 Agent**：自然语言对话与任务编排、日志分析、失败自动诊断与安全恢复；工具调用带确认与审计
- 🧩 **MaaCore 内核**：复用 MAA 的识别与操作能力，支持 Copilot 作业快照、内核 / 资源 / 游戏更新
- 📊 **账号数据**：森空岛练度同步，干员与仓库只读视图（练度、潜能、模组与资源统计）
- 🌐 **多端与推送**：PWA 安装、Web Push 通知、Tailscale Serve HTTPS 接入
- 🚀 **部署友好**：单进程、单端口（默认 8002）、SQLite 持久化，Docker / 裸机两种部署形态
- 🧠 **网络自适应**：更新下载自动探测系统代理与本机代理、可配下载前缀；Agent 也能按自然语言选择代理与加速

## 快速开始

### 准备工作

- Python 3.11+（开发环境用 3.13）与 [Poetry](https://python-poetry.org/)
- Node.js 22+ 与 pnpm（Docker 构建已内置；本机构建前端需要）
- 一台已连接 ADB 的安卓设备或模拟器
- 首次启动会自动检查并下载 MaaCore 到 `resource/`，也可以在 `config.yaml` 的 `app.maa_core_path` 指定本地内核目录
- 复制配置模板并按需填写：`cp config.template.yaml config.yaml`（`config.yaml` 已在 `.gitignore` 中，不会被提交）

### Docker 部署

镜像构建会自动执行 Vite 前端构建，再把产物复制到 Python 运行镜像：

```bash
sudo docker build -t maa-api .
```

启动容器时仅挂载持久化运行数据目录。不要把整个仓库挂载到 `/app`，否则会遮住镜像内的 `static/` 前端产物与应用代码：

```bash
mkdir -p resource
sudo docker run -d -p 8002:8002 --name maa-api \
  -v "$(pwd)/resource:/app/resource" \
  --network host \
  --restart unless-stopped \
  -e TZ=Asia/Shanghai \
  -e MAA_APP_ACCESS_TOKEN="替换为自己的访问令牌" \
  -e MAA_LLM_ENCRYPTION_KEY="设置页加密 API Key 用的稳定主密钥" \
  maa-api
```

镜像从 `config.template.yaml` 创建默认 `/app/config.yaml`。可用 `MAA_APP_ACCESS_TOKEN` 设置 API 访问令牌；配置 `MAA_LLM_ENCRYPTION_KEY` 后可在设置页加密保存 DeepSeek API Key。更换该主密钥会导致旧数据库密钥无法解密；运行数据与 MaaCore 下载内容保存在挂载的 `resource/` 中。

### 本机启动

第一次启动后端前先生成静态前端产物：

```bash
cd web && pnpm install --frozen-lockfile && pnpm build
cd ..
poetry run uvicorn maa_api.main:app --host 0.0.0.0 --port 8002
```

## 配置

配置文件是 `config.yaml`（模板 `config.template.yaml`），也支持用环境变量覆盖同名配置项：`MAA_` + 点分 key 的大写、点换下划线（如 `app.access_token` → `MAA_APP_ACCESS_TOKEN`）。常用项：

| 环境变量 | 作用 |
|---|---|
| `MAA_APP_ACCESS_TOKEN` | API 访问令牌；留空则免鉴权 |
| `MAA_LLM_ENCRYPTION_KEY` | 设置页加密保存 LLM API Key 用的 Fernet 主密钥；必须持久保存，更换后旧密钥无法解密 |
| `MAA_LLM_API_KEY` | LLM API Key（也可在设置页保存） |
| `MAA_APP_MAA_CORE_PATH` | MaaCore 目录，等价于 `config.yaml` 的 `app.maa_core_path` |
| `MAA_HOST` / `MAA_PORT` | 启动脚本的监听地址 / 端口（默认 `0.0.0.0:8002`） |

数据库、日志、MaaCore 下载内容与截图都落在 `resource/`；备份与迁移时带上这个目录即可。完整配置项与默认值见 [config.template.yaml](config.template.yaml)。

## 文档

| 文档 | 内容 |
|---|---|
| [文档索引](docs/README.md) | 重构方案、文档目录与决策速查表 |
| [01-重构总览与现状评估](docs/01-重构总览与现状评估.md) | 重构目标、范围边界与验收标准 |
| [02-系统架构设计](docs/02-系统架构设计.md) | 进程模型、IPC、任务队列与模块划分 |
| [11-Agent 模块设计](docs/11-Agent模块设计.md) | 内置 Agent、工具清单、安全确认与审计 |
| [13-决策记录](docs/13-决策记录.md) | 每项决策的选择、理由与实施偏离 |
| [14-开放 API 接入指南](docs/14-开放API接入指南.md) | REST / WebSocket 的调用、鉴权与错误处理 |
| [15-部署指南](docs/15-部署指南.md) | Tailscale Serve、HTTPS、Web Push 与故障排查 |

## 开发

```bash
# 后端测试
.venv/bin/python -m pytest -q

# 前端检查（在 web/ 下）
pnpm gen:api:check && pnpm typecheck && pnpm test --run && pnpm build

# OpenAPI 契约快照
.venv/bin/python scripts/dump_openapi.py --check
```

`scripts/*_smoke.py` 是各子系统的端到端冒烟，成功时末行是 `SMOKE OK`；环境陷阱（如 macOS 没有 `timeout`）记录在 [docs/ENVIRONMENT.md](docs/ENVIRONMENT.md)。

目录结构：`maa_api/` 是 FastAPI 服务（api、services、agent、domain、model），`web/` 是 React 前端（构建产物输出到 `static/`），`scripts/` 放启动、门禁与冒烟脚本，`docs/` 是重构方案与决策记录。

分支模型：`master` 是对外稳定分支，`dev` 是开发集成分支；新工作从 `dev` 拉分支，完成后 `git merge --no-ff` 回 `dev`，发布时合入 `master`。约定详见 [AGENTS.md](AGENTS.md)。

## 参与贡献

欢迎提 [Issue](https://github.com/ChthollyWn/MAA-API/issues) 和 Pull Request。提交前请先跑上面「开发」一节的门禁；会改变对外行为或与 [决策记录](docs/13-决策记录.md) 冲突的改动，先开 Issue 讨论。

## 许可

本项目自身代码与文档采用 [GNU Affero General Public License v3.0 only](LICENSE)（AGPL-3.0-only）授权，版权归 ChthollyWn 所有。品牌插画、游戏素材与第三方数据不在该授权范围内，见下方「声明」。

- 网络部署：按 AGPL §13，需要向通过网络使用本服务的用户提供对应源码；修改后再对外部署，同样需要公开修改后的源码。
- 内核与资源：MaaCore、MaaResource 等来自 [MaaAssistantArknights](https://github.com/MaaAssistantArknights/MaaAssistantArknights)，版权归 MAA Team，使用与再分发还须遵守 AGPL-3.0-only 与 [MAA 用户协议](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/dev-v2/terms-of-service.md)。本仓库不随附 MaaCore 二进制，首次启动时从 MAA 官方发布下载。
- 第三方数据：企鹅物流数据按 CC BY-NC 4.0 提供；PRTS 作业站、森空岛等按各自站点条款使用。

## 声明

- 本项目是非官方粉丝作品，与鹰角网络、MaaAssistantArknights 官方均无隶属关系；仅供学习交流使用
- 《明日方舟》的角色、游戏素材与相关商标版权归 Hypergryph / Studio Montagne 所有
- 仓库中的品牌插画与 logo 为项目自用标识，版权归画稿作者所有，未经许可不得用于商业用途

如果觉得这个项目对你有帮助，帮忙点个 Star 吧！这就是对我们最大的支持了~（网页右上角的小星星）
