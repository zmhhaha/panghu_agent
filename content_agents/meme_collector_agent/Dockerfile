FROM python:3.13-slim
WORKDIR /app
COPY content_agents ./content_agents
COPY tools/responses_client.py ./tools/responses_client.py
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "content_agents.meme_collector_agent.main"]
