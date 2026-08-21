# 使用轻量级的 Python 基础镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装 scihub-cli
# 使用 --no-cache-dir 减小镜像体积
RUN pip install --no-cache-dir scihub-cli

# 创建用于存放下载文件的目录
RUN mkdir -p /app/papers

# 设置容器启动时的默认命令
# 当容器启动时，直接运行 scihub-cli
ENTRYPOINT ["scihub-cli"]
# 默认显示帮助信息，实际使用时通过 K8s 的 "args" 覆盖
CMD ["--help"]