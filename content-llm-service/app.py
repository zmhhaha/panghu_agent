from __future__ import annotations

import json
import re
from typing import Any
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from tools.llm_config import get_llm_config_error
from .crew import (
    create_github_batch_crew,
    create_meme_batch_crew,
    create_meme_crew,
    create_programmer_jobs_summary_crew,
)

app = FastAPI(title="Panghu Content LLM Service", version="0.1.0")


class MemeJudgeRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    summary: str = Field(default="", max_length=4000)
    url: str = Field(default="", max_length=2000)


class MemeBatchRequest(BaseModel):
    candidates: list[MemeJudgeRequest] = Field(..., min_length=1, max_length=50)


class GithubProjectRequest(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    url: str = Field(default="", max_length=2000)
    stars: int = 0
    forks: int = 0
    language: str = "unknown"
    license: str = "unknown"


class GithubBatchRequest(BaseModel):
    candidates: list[GithubProjectRequest] = Field(..., min_length=1, max_length=50)


class ProgrammingJobRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    company: str = Field(default="", max_length=200)
    city: str = Field(default="", max_length=100)
    salary: str = Field(default="", max_length=100)
    experience: str = Field(default="", max_length=100)
    education: str = Field(default="", max_length=100)
    skills: list[str] = Field(default_factory=list, max_length=30)
    description: str = Field(default="", max_length=4000)
    source: str = Field(default="", max_length=100)


class ProgrammerJobsSummaryRequest(BaseModel):
    jobs: list[ProgrammingJobRequest] = Field(..., min_length=1, max_length=80)


def parse_json_result(value: str) -> Any:
    text = value.strip()
    fenced = re.search(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", text, re.S)
    try:
        data = json.loads(fenced.group(1) if fenced else text)
    except (AttributeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="CrewAI returned invalid JSON") from exc
    if not isinstance(data, (dict, list)):
        raise HTTPException(status_code=502, detail="CrewAI returned an invalid JSON result")
    return data


@app.get("/health")
def health() -> dict[str, Any]:
    error = get_llm_config_error("content_llm_service")
    return {"status": "ok" if error is None else "degraded", "llm_configured": error is None}


@app.post("/v1/meme/judge")
def judge_meme(request: MemeJudgeRequest) -> dict[str, Any]:
    error = get_llm_config_error("content_llm_service")
    if error:
        raise HTTPException(status_code=503, detail=error)
    try:
        return parse_json_result(str(create_meme_crew(request.model_dump()).kickoff()))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"content agent failed: {exc}") from exc


@app.post("/v1/meme/judge-batch")
def judge_meme_batch(request: MemeBatchRequest) -> dict[str, Any]:
    error = get_llm_config_error("content_llm_service")
    if error:
        raise HTTPException(status_code=503, detail=error)
    try:
        raw = create_meme_batch_crew([item.model_dump() for item in request.candidates]).kickoff()
        result = parse_json_result(str(raw))
        items = result if isinstance(result, list) else result.get("items")
        if not isinstance(items, list) or len(items) != len(request.candidates):
            raise HTTPException(status_code=502, detail="CrewAI returned an invalid batch result")
        return {"items": items}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"content batch agent failed: {exc}") from exc


@app.post("/v1/github/enrich-batch")
def enrich_github_batch(request: GithubBatchRequest) -> dict[str, Any]:
    error = get_llm_config_error("content_llm_service")
    if error:
        raise HTTPException(status_code=503, detail=error)
    try:
        raw = create_github_batch_crew([item.model_dump() for item in request.candidates]).kickoff()
        result = parse_json_result(str(raw))
        items = result if isinstance(result, list) else result.get("items")
        if not isinstance(items, list) or len(items) != len(request.candidates):
            raise HTTPException(status_code=502, detail="CrewAI returned an invalid GitHub batch result")
        return {"items": items}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"github batch agent failed: {exc}") from exc


@app.post("/v1/jobs/programmer-summary")
def summarize_programmer_jobs(request: ProgrammerJobsSummaryRequest) -> dict[str, Any]:
    """Summarize a daily job sample with one shared LLM request."""
    error = get_llm_config_error("content_llm_service")
    if error:
        raise HTTPException(status_code=503, detail=error)
    try:
        raw = create_programmer_jobs_summary_crew(
            [item.model_dump() for item in request.jobs]
        ).kickoff()
        result = parse_json_result(str(raw))
        if not isinstance(result, dict) or not isinstance(result.get("overview"), str):
            raise HTTPException(status_code=502, detail="CrewAI returned an invalid jobs summary")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"programmer jobs summary failed: {exc}") from exc
