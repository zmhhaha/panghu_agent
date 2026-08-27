FROM python:3.13-slim
WORKDIR /app
COPY content_agents ./content_agents
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "content_agents.programmer_jobs_agent.main"]
