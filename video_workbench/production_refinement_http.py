"""Owner-scoped formal-film refinement routes; lab budgets are not involved."""
from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from agent_service.contracts import ServiceError
from .production_sequence_store import TERMINAL


class RefinementSource(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_id: str = Field(min_length=1, max_length=200)
    version_id: str = Field(min_length=1, max_length=200)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class RefinementRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source: RefinementSource
    kind: Literal['user_requested', 'requirements']
    request_id: str = Field(min_length=1, max_length=128)
    instruction: str | None = Field(default=None, min_length=1, max_length=16000)
    items: list[dict] | None = Field(default=None, min_length=1, max_length=64)
    budget: dict | None = None


def register_refinement_routes(app, *, store, sequences, runs, service, workflow, identity, mutation):
    def authorize(owner, project_id):
        with store.transaction() as db:
            store._project(db, owner, project_id)

    def require_service():
        if service is None or not workflow.enabled:
            raise ServiceError('production_refinement_disabled', 503)
        return service

    def project_catalogue(owner, project_id):
        authorize(owner, project_id)
        operations = service.list(owner, project_id) if service is not None else []
        bindings = {}
        for run in runs.list(owner, project_id):
            registration = run.get('sequence_registration') or {}
            if registration.get('status') == 'registered' and registration.get('source'):
                sequence_id = registration['sequence_id']
                bindings.setdefault(sequence_id, []).append(registration['source'])
        for row in operations:
            for source in [row.get('source'), (row.get('result') or {}).get('source')]:
                if source is not None:
                    bindings.setdefault(row['sequence_id'], []).append(source)
        summaries = []
        for sequence_id, sources in bindings.items():
            sequence = sequences.get(owner, project_id, sequence_id)
            work = sequences.operations(owner, project_id, sequence_id)
            consumed = {kind: sum(row.get('kind') == kind for row in work)
                        for kind in ['automatic', 'user_requested']}
            unique = {source['version_id']: source for source in sources}
            summaries.append(dict(id=sequence_id, limits=sequence['limits'], consumed=consumed,
                remaining={kind: max(0, sequence['limits'][kind] - consumed[kind]) for kind in consumed},
                automatic_finished=any(row.get('kind') == 'automatic' and row['status'] in TERMINAL for row in work),
                active=any(row['status'] not in TERMINAL for row in work),
                sources=list(unique.values())))
        return dict(enabled=bool(service is not None and workflow.enabled),
                    operations=operations, sequences=summaries)

    @app.get('/api/projects/{project_id}/refinements')
    def project_refinements(project_id: str, owner=Depends(identity)):
        return project_catalogue(owner, project_id)

    @app.post('/api/projects/{project_id}/refinements', status_code=202)
    def start_refinement(project_id: str, body: RefinementRequest, owner=Depends(mutation)):
        authorize(owner, project_id)
        return require_service().start(owner, project_id, **body.model_dump(exclude_none=True))

    @app.get('/api/refinements/{operation_id}')
    def read_refinement(operation_id: str, owner=Depends(identity)):
        if service is None:
            raise ServiceError('production_refinement_not_found', 404)
        return service.get(owner, operation_id)

    @app.post('/api/refinements/{operation_id}/cancel')
    def cancel_refinement(operation_id: str, owner=Depends(mutation)):
        if service is None:
            raise ServiceError('production_refinement_not_found', 404)
        return service.cancel(owner, operation_id)
