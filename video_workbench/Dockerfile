ARG PYTHON_BASE_IMAGE=python@sha256:cf50a4f6ac4f026458b08d5222023d700ac806410dff5e9ca8728fa44f2b850f
FROM ${PYTHON_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIDEO_WORKBENCH_DB=/data/workbench.db

WORKDIR /app
COPY video_workbench/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && python -m pip check \
    && groupadd --gid 10001 workbench \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin workbench \
    && mkdir /data \
    && chown 10001:10001 /data

# Build context is the repository root; no Agent runtime is started here.
COPY agent_service /app/agent_service
COPY video_workbench /app/video_workbench

USER 10001:10001
VOLUME ["/data"]
EXPOSE 8089
CMD ["python", "-m", "video_workbench", "--host", "0.0.0.0", "--port", "8089"]
