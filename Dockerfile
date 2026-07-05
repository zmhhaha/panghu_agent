# ============================================================
#  Panghu Agent — ARM64 K8s 部署
#  基于自建 base 镜像（Debian + 国内源）
# ============================================================
#  构建:  docker build -t panghu-agent:latest .
#  运行:  docker run -d -p 8000:8000 --env-file .env panghu-agent:latest
# ============================================================

ARG REGISTRY=arm-cluster-master:5000

FROM ${REGISTRY}/base:latest

LABEL maintainer="zmhhaha"
LABEL description="CrewAI multi-agent research assistant with billing API (ARM64)"

# ---- 安装 Python & 系统依赖（使用 base 镜像已配好的国内源） ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3 1 \
    && python -m pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/

# ---- 工作目录 ----
WORKDIR /app

# ---- 分层安装 Python 依赖（pip 走国内镜像） ----
COPY crewai/requirements.txt /app/crewai/requirements.txt
COPY api/requirements.txt     /app/api/requirements.txt

RUN pip install --no-cache-dir \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    -r /app/crewai/requirements.txt \
    && pip install --no-cache-dir \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    -r /app/api/requirements.txt

# ---- 复制源码 ----
COPY crewai/  /app/crewai/
COPY api/     /app/api/

# ---- 环境变量默认值 ----
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    API_PORT=8000 \
    PROVIDER=openai

# ---- 暴露端口 ----
EXPOSE 8000

# ---- 健康检查 ----
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:${API_PORT}/health || exit 1

# ---- 启动 ----
WORKDIR /app
CMD ["sh", "-c", "python -m uvicorn api.server:app --host 0.0.0.0 --port ${API_PORT:-8000}"]
