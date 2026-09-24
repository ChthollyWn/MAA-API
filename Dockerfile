# ---------- 阶段 1：构建前端 ----------
FROM node:22-slim AS web

WORKDIR /web

# Separate dependency manifests to keep the pnpm install layer cacheable.
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
# Copy only Vite output; the runtime image is based on Python and has no Node toolchain.
COPY --from=web /static ./static

EXPOSE 8002

CMD ["poetry", "run", "uvicorn", "maa_api.main:app", "--host", "0.0.0.0", "--port", "8002"]
