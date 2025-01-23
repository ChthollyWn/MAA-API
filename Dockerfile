FROM python:3.11.9-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    adb \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml poetry.lock ./

RUN pip install --no-cache-dir poetry -i https://mirrors.aliyun.com/pypi/simple/
RUN poetry install --no-root

EXPOSE 8002

CMD ["poetry", "run", "uvicorn", "maa_api.main:app", "--host", "0.0.0.0", "--port", "8002"]
