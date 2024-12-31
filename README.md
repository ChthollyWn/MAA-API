## 构建 Docker 镜像

```bash
sudo docker build -t maa-api .
```

## 运行 Docker 容器

```bash
sudo docker run -d -p 8000:8000 --name maa-api \
  -v $(pwd)/config.yaml:/app/config.yaml \
  -v $(pwd)/resource:/app/resource \
  maa-api
```


