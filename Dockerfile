FROM python:3.11.9-slim

WORKDIR /app

COPY pyproject.toml poetry.lock ./

RUN pip install --no-cache-dir poetry -i https://mirrors.aliyun.com/pypi/simple/

RUN poetry install --no-root --no-dev

EXPOSE 8002

CMD ["poetry", "run", "uvicorn", "maa_api.main:app", "--host", "0.0.0.0", "--port", "8002"]
