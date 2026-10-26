"""Opt-in router. Importing this module never mounts routes or modifies state."""
from fastapi import APIRouter, Depends, Header, HTTPException, Response

from ..auth import authenticate
from .contracts import AcceptanceSnapshot, ReviewCommand
from .domain import AcceptanceError
from .store import AcceptanceService


def create_acceptance_router(service: AcceptanceService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/projects/{project_id}/versions/{version_id}/acceptance", tags=["human acceptance"])

    def respond(response, operation):
        response.headers["Cache-Control"] = "no-store"
        try:
            return operation()
        except AcceptanceError as exc:
            raise HTTPException(exc.status, detail={"code": exc.code}) from None

    @router.get("", response_model=AcceptanceSnapshot)
    def get(project_id: str, version_id: str, response: Response, owner=Depends(authenticate)):
        return respond(response, lambda: service.get(owner, project_id, version_id))

    @router.post("/records", response_model=AcceptanceSnapshot)
    def append(project_id: str, version_id: str, body: ReviewCommand, response: Response,
               idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), owner=Depends(authenticate)):
        return respond(response, lambda: service.append(owner, project_id, version_id, body, idempotency_key))

    return router
