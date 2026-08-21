# 构建镜像
REGISTRY=arm-cluster-master:5000
docker build -t $REGISTRY/scihub-cli:latest .

# 推送到你的镜像仓库（例如 Docker Hub 或私有仓库）
docker push $REGISTRY/scihub-cli:latest