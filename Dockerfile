# ---------- 阶段 1：构建前端 ----------
FROM node:22-slim AS web

WORKDIR /web

# 依赖清单单独复制：改业务代码不会让 pnpm install 这一层缓存失效。
COPY web/package.json web/pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile

COPY web/ ./
RUN pnpm build

# ---------- 阶段 2：运行服务 ----------
FROM python:3.11.9-slim AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl adb libatomic1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml poetry.lock ./
RUN pip install --no-cache-dir poetry -i https://mirrors.aliyun.com/pypi/simple/ \
    && poetry install --no-root --only main --no-interaction

COPY maa_api/ ./maa_api/
COPY scripts/ ./scripts/
COPY alembic.ini ./alembic.ini
COPY config.template.yaml ./config.yaml
# 只复制 Vite 产物：运行镜像是 Python 基底，没有 Node 工具链。
COPY --from=web /static ./static

EXPOSE 8002

# --forwarded-allow-ips 默认只信任回环：容器通常由反向代理（tailscale serve、Caddy）
# 从本机转发进来。设为 "*" 会让任何客户端都能伪造 X-Forwarded-For / X-Forwarded-Proto，
# 既绕过按来源限流，也让 cookie 的 Secure 判定失真。多跳代理时按需追加地址。
CMD ["poetry", "run", "uvicorn", "maa_api.main:app", "--host", "0.0.0.0", "--port", "8002", "--forwarded-allow-ips", "127.0.0.1"]
