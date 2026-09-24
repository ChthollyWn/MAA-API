## Docker 镜像

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
  maa-api
```

镜像从 `config.template.yaml` 创建默认 `/app/config.yaml`。可用 `MAA_APP_ACCESS_TOKEN` 设置 API 访问令牌；运行数据与 MaaCore 下载内容保存在挂载的 `resource/` 中。

## 裸机启动

第一次启动后端前先生成静态前端产物：

```bash
cd web && pnpm install --frozen-lockfile && pnpm build
cd ..
poetry run uvicorn maa_api.main:app --host 0.0.0.0 --port 8002
```

## 手机与 PWA

手机可通过同一网络访问主机地址。若要启用 Service Worker、离线外壳与 Chromium 安装提示，请通过 **Tailscale Serve 的 HTTPS 地址**访问；直接使用局域网 HTTP 仍可打开页面和调用 API，但浏览器会禁用这些安全上下文能力。iOS 仍可从 Safari 分享菜单手动添加到主屏幕。Web Push 将在 M14 接入。

## 用 superpowers 推进里程碑

后续里程碑（M10–M15，范围见 [docs/12](docs/12-实施计划与里程碑.md)）在 Codex 中用 superpowers 插件推进。项目约定写在 [AGENTS.md](AGENTS.md)，下面的提示词只需把 `<N>` 换成里程碑编号。

### 第一步：在计划模式里理清范围

```text
/plan 使用 superpowers 开发执行方案中的 M<N>。规格以 docs/12 的 M<N> 一节及其引用的专题文档为准，已定决策见 docs/README.md 与 docs/13（含 §8）；只问文档没覆盖、互相冲突或需要先实测的地方。最后给我一份一页的 M<N> 简报：范围与非目标、硬约束、需要先验证的假设、硬验收命令、需要真机的补充检查。
```

回答它的问题，确认简报后进入下一步。

### 第二步：设为目标，让它执行到底

```text
/goal 按计划模式里确认的 M<N> 简报完成 M<N>：先把简报写成 spec 并提交（视为已批准，不用再等我审），再用 writing-plans 写计划（不需要我审，执行方式用 subagent-driven），然后执行。收尾按 AGENTS.md 直接本地合并并打 v2-m<N> tag，不用问我合并方式；涉及前端时，前端验收至少在干净克隆上跑一次。只有 AGENTS.md 规定要问我的情况才停下。
```

它只会在 AGENTS.md 规定必须问你的地方暂停。结束后读它的报告：验收结果、替你做过的裁决、推迟处理的问题。

### 大里程碑：换新会话执行

M11、M13 这类大里程碑，确认简报后先在普通模式里说"把刚才确认的简报写成 spec，再写计划，都提交后停下"，然后新开一个会话执行，让主控从干净的上下文开始：

```text
/goal 用 subagent-driven-development 执行 docs/superpowers/plans/<计划文件>.md，完成后按 AGENTS.md 收尾，不用问我合并方式；涉及前端时，前端验收至少在干净克隆上跑一次。
```

执行中断或会话过长时，新开一个会话接着做：

```text
继续用 subagent-driven-development 执行 docs/superpowers/plans/<计划文件>.md。先读它的台账和 git log，从第一个未完成的任务接着做。
```
