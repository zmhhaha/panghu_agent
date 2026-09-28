"""百家争鸣统一 FastAPI。"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .registry import AgentDefinition, get_registry
from .runtime import AgentBusyError, AgentRuntime, UserTaskRunningError


class SubmitRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=20_000)
    user_id: str = Field(default="", max_length=256)


class TaskResponse(BaseModel):
    id: str
    agent: str
    text: str
    status: str
    report: str | None = None
    error: str | None = None
    cached: bool = False


class LegacyTaskResponse(BaseModel):
    id: str
    text: str
    status: str
    report: str | None = None
    error: str | None = None
    cached: bool = False


registry = get_registry()
runtime = AgentRuntime(registry)
app = FastAPI(title="百家争鸣 Agent API", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _definition(slug: str) -> AgentDefinition:
    definition = registry.get(slug)
    if not definition:
        raise HTTPException(404, "Agent not found or disabled")
    return definition


def _submit(definition: AgentDefinition, request: SubmitRequest) -> TaskResponse:
    try:
        result = runtime.submit(definition, request.text, request.user_id)
    except (AgentBusyError, UserTaskRunningError) as error:
        raise HTTPException(429, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error
    return TaskResponse(
        id=result.task_id,
        agent=definition.slug,
        text=result.text,
        status=result.status,
        report=result.report,
        cached=result.cached,
    )


def _task(definition: AgentDefinition, task_id: str) -> TaskResponse:
    task = runtime.get_task(definition, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return TaskResponse(
        id=task["id"],
        agent=definition.slug,
        text=task["topic"],
        status=task["status"],
        report=task.get("report"),
        error=task.get("error"),
    )


@app.get("/health")
def framework_health():
    agents = {}
    for definition in registry.list_enabled():
        error = runtime.configuration_error(definition)
        agents[definition.slug] = {
            "status": "degraded" if error else "ok",
            "error": error,
            # 还没轮到执行的任务数：并发满时会堆积，是判断要不要扩容的唯一信号
            "queued": runtime.queue_depth(definition),
        }
    status = "ok" if all(item["status"] == "ok" for item in agents.values()) else "degraded"
    return {"status": status, "agents": agents}


@app.get("/v1/agents")
def list_agents():
    return [
        {
            "slug": item.slug,
            "display_name": item.display_name,
            "host": item.host,
            "icon": item.icon,
            "tagline": item.tagline,
        }
        for item in registry.list_enabled()
    ]


@app.get("/v1/agents/{slug}")
def get_agent(slug: str):
    item = _definition(slug)
    return {
        "slug": item.slug,
        "display_name": item.display_name,
        "host": item.host,
        "icon": item.icon,
        "tagline": item.tagline,
        "max_input_length": item.max_input_length,
    }


@app.get("/v1/agents/{slug}/health")
def agent_health(slug: str):
    item = _definition(slug)
    error = runtime.configuration_error(item)
    return {
        "status": "degraded" if error else "ok",
        "llm_configured": error is None,
        "error": error,
    }


@app.post("/v1/agents/{slug}/tasks", response_model=TaskResponse)
def submit_task(slug: str, request: SubmitRequest):
    return _submit(_definition(slug), request)


@app.get("/v1/agents/{slug}/tasks/{task_id}", response_model=TaskResponse)
def get_task(slug: str, task_id: str):
    return _task(_definition(slug), task_id)


def _legacy_submit(definition: AgentDefinition):
    def endpoint(request: SubmitRequest):
        return _submit(definition, request)

    endpoint.__name__ = f"submit_{definition.slug}_legacy"
    return endpoint


def _legacy_get(definition: AgentDefinition):
    def endpoint(task_id: str):
        return _task(definition, task_id)

    endpoint.__name__ = f"get_{definition.slug}_legacy"
    return endpoint


def _legacy_health(definition: AgentDefinition):
    def endpoint():
        error = runtime.configuration_error(definition)
        return {"status": "degraded" if error else "ok", "llm_configured": error is None}

    endpoint.__name__ = f"health_{definition.slug}_legacy"
    return endpoint


for _item in registry.list_enabled():
    app.add_api_route(
        _item.legacy_path,
        _legacy_submit(_item),
        methods=["POST"],
        response_model=LegacyTaskResponse,
    )
    app.add_api_route(
        f"{_item.legacy_path}/{{task_id}}",
        _legacy_get(_item),
        methods=["GET"],
        response_model=LegacyTaskResponse,
    )
    app.add_api_route(
        f"{_item.legacy_path}-health",
        _legacy_health(_item),
        methods=["GET"],
    )
