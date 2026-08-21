# 使用轻量级的 Python 基础镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装 scihub-cli；与 Literature Downloader API 使用同一个阿里云 PyPI 源。
RUN pip install --break-system-packages --no-cache-dir \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    --default-timeout=300 \
    scihub-cli

# 创建用于存放下载文件的目录
RUN mkdir -p /app/papers

# 设置容器启动时的默认命令
# 当容器启动时，直接运行 scihub-cli
ENTRYPOINT ["scihub-cli"]
# 默认显示帮助信息，实际使用时通过 K8s 的 "args" 覆盖
CMD ["--help"]
