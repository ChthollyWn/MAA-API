FROM python:3.11.9-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc-12 g++-12 cmake zlib1g-dev \
    curl tar adb \
    && rm -rf /var/lib/apt/lists/*

RUN update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-12 100 \
    && update-alternatives --install /usr/bin/g++ g++ /usr/bin/g++-12 100

COPY . .

RUN python3 maadeps-download.py

RUN CC=gcc-12 CXX=g++-12 cmake -B build \
    -DINSTALL_THIRD_LIBS=ON \
    -DINSTALL_RESOURCE=ON \
    -DINSTALL_PYTHON=ON \
    && cmake --build build

# 可选：将编译结果安装到指定目录
RUN cmake --install build --prefix /app/maa

RUN pip install --no-cache-dir poetry -i https://mirrors.aliyun.com/pypi/simple/
RUN poetry install --no-root --no-dev

EXPOSE 8002

CMD ["poetry", "run", "uvicorn", "maa_api.main:app", "--host", "0.0.0.0", "--port", "8002"]
