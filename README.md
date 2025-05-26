## 构建 Docker 镜像

```bash
sudo docker build -t maa-api .
```

## 运行 Docker 容器

```bash
sudo docker run -d -p 8002:8002 --name maa-api \
  -v $(pwd):/app \
  --network host \
  -e TZ=Asia/Shanghai \
  maa-api
```