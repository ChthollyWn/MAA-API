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
