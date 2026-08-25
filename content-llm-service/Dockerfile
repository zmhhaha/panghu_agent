ARG REGISTRY=arm-cluster-master:5000
FROM ${REGISTRY}/agent-api-base:latest
WORKDIR /app
COPY tools ./tools
COPY content-llm-service ./content_llm_service
RUN pip install --break-system-packages --no-cache-dir -r content_llm_service/requirements.txt
ENV PYTHONPATH=/app
EXPOSE 8000
CMD ["uvicorn", "content_llm_service.app:app", "--host", "0.0.0.0", "--port", "8000"]
