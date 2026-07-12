# ============================================================
#  Text-to-Image Proxy — ARM64 Docker 镜像
#  基于自建 python3_base 镜像
#  纯 Python API 网关，转发到云平台文生图 API
#  通过 PROVIDER 环境变量切换平台，无需本地显卡
# ============================================================
ARG REGISTRY=arm-cluster-master:5000
FROM ${REGISTRY}/python3_base:latest

WORKDIR /app

# 时区
ENV TZ=Asia/Shanghai

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com

COPY server.py .

ENV PROVIDER=ark \
    ARK_API_KEY="" \
    REPLICATE_API_KEY="" \
    TOGETHER_API_KEY="" \
    STABILITY_API_KEY="" \
    OPENAI_API_KEY="" \
    MODEL="" \
    ARK_BASE_URL="https://ark.cn-beijing.volces.com/api/v3"

EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
