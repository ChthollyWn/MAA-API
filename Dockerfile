FROM python:3.11.9-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    curl \
    tar \
    && rm -rf /var/lib/apt/lists/*

RUN curl -L -o /tmp/maa_cli.tar.gz "https://github.com/MaaAssistantArknights/maa-cli/releases/latest/download/maa_cli-x86_64-unknown-linux-gnu.tar.gz" \
    && tar -xzvf /tmp/maa_cli.tar.gz -C /tmp \
    && mv /tmp/maa /usr/local/bin/maa \
    && chmod +x /usr/local/bin/maa \
    && rm /tmp/maa_cli.tar.gz

RUN maa install 
RUN maa update

COPY pyproject.toml poetry.lock ./

RUN pip install --no-cache-dir poetry -i https://mirrors.aliyun.com/pypi/simple/
RUN poetry install --no-root --no-dev

EXPOSE 8002

CMD ["poetry", "run", "uvicorn", "maa_api.main:app", "--host", "0.0.0.0", "--port", "8002"]
