"""Internal owner-scoped scientific workbench HTTP API.

Identity comes exclusively from the existing authenticated BFF's forwarded user
header. Browser CSRF/Origin enforcement belongs to that same BFF.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response

from scientific_agent.pi_client import WorkerError
from scientific_agent.pi_contracts import ContractError
from scientific_agent.pi_sections import SectionError
from scientific_agent.workflow_contracts import WorkflowContractError
from scientific_agent.workspace import WorkspaceError, encode, owner_id, require
from scientific_agent.workspace_materials import MaterialError, MAX_UPLOAD_BYTES


def create_router(service):
    router = APIRouter(prefix='/api/v1/research', tags=['research-workspace'])
    store = service.store

    def call(operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except HTTPException:
            raise
        except (WorkspaceError, MaterialError, SectionError, ContractError,
                WorkflowContractError, WorkerError) as exc:
            status = getattr(exc, 'status', getattr(exc, 'status_code', 422))
            raise HTTPException(status, detail=dict(code=getattr(exc, 'code', 'invalid_workspace_request'),
                message=str(exc), retryable=getattr(exc, 'retryable', False))) from None
        except Exception:
            # Never return raw SQL, material contents, URLs or authentication details.
            raise HTTPException(503, detail=dict(code='workspace_storage_unavailable',
                message='Research workspace is temporarily unavailable', retryable=True)) from None

    def user(value):
        return call(owner_id, value)

    @router.get('/projects')
    def projects(x_forwarded_user: str | None = Header(default=None)):
        return {'projects': call(store.list, user(x_forwarded_user))}

    @router.post('/projects', status_code=201)
    def create(payload: dict = Body(...), idempotency_key: str | None = Header(default=None),
               x_forwarded_user: str | None = Header(default=None)):
        return call(store.create, user(x_forwarded_user), payload, idempotency_key)

    @router.get('/projects/{project_id}')
    def project(project_id: str, x_forwarded_user: str | None = Header(default=None)):
        owner = user(x_forwarded_user)
        call(store.get, owner, project_id)
        try:
            service.reconcile(owner, project_id)
        except (WorkerError, WorkspaceError):
            pass
        return call(store.get, owner, project_id)

    @router.patch('/projects/{project_id}')
    def patch(project_id: str, payload: dict = Body(...),
              idempotency_key: str | None = Header(default=None),
              x_forwarded_user: str | None = Header(default=None)):
        return call(store.patch, user(x_forwarded_user), project_id, payload, idempotency_key)

    @router.post('/projects/{project_id}/actions/{action}')
    def action(project_id: str, action: str, payload: dict = Body(...),
               idempotency_key: str | None = Header(default=None),
               x_forwarded_user: str | None = Header(default=None)):
        return call(service.action, user(x_forwarded_user), project_id, action, payload, idempotency_key)

    @router.post('/projects/{project_id}/materials')
    async def upload(project_id: str, file: UploadFile = File(...), expected_revision: int = Form(...),
                     idempotency_key: str | None = Header(default=None),
                     x_forwarded_user: str | None = Header(default=None)):
        owner = user(x_forwarded_user)
        call(store.get, owner, project_id)
        try:
            raw = await file.read(MAX_UPLOAD_BYTES + 1)
            if len(raw) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, detail={'code': 'material_upload_limit', 'retryable': False})
            return call(store.add_material, owner, project_id, expected_revision,
                        idempotency_key, filename=file.filename, raw=raw)
        finally:
            await file.close()

    @router.post('/projects/{project_id}/materials/doi')
    def doi(project_id: str, payload: dict = Body(...), idempotency_key: str | None = Header(default=None),
            x_forwarded_user: str | None = Header(default=None)):
        def register(owner):
            require(set(payload) == {'expected_revision', 'doi'})
            return store.add_material(owner, project_id, payload['expected_revision'],
                idempotency_key, doi=payload['doi'])
        return call(register, user(x_forwarded_user))

    @router.delete('/projects/{project_id}/materials/{material_id}')
    def remove(project_id: str, material_id: str, expected_revision: int = Query(ge=0),
               idempotency_key: str | None = Header(default=None),
               x_forwarded_user: str | None = Header(default=None)):
        return call(store.remove_material, user(x_forwarded_user), project_id, material_id,
                    expected_revision, idempotency_key)

    @router.get('/projects/{project_id}/versions/{version_id}')
    def version(project_id: str, version_id: str, x_forwarded_user: str | None = Header(default=None)):
        return call(store.version, user(x_forwarded_user), project_id, version_id)

    @router.get('/projects/{project_id}/jobs/{task_id}/diagnostic')
    def job_diagnostic(project_id: str, task_id: str,
                       x_forwarded_user: str | None = Header(default=None)):
        value = call(service.job_diagnostic, user(x_forwarded_user), project_id, task_id)
        return Response(encode(value), media_type='application/json', headers={
            'Content-Disposition': 'attachment; filename="research-task-diagnostic.json"',
            'Cache-Control': 'private, no-store'})

    @router.get('/projects/{project_id}/evidence/{evidence_id}')
    def evidence(project_id: str, evidence_id: str,
                 x_forwarded_user: str | None = Header(default=None)):
        return call(store.evidence, user(x_forwarded_user), project_id, evidence_id)

    @router.get('/projects/{project_id}/evidence/{evidence_id}/download')
    def evidence_download(project_id: str, evidence_id: str, format: str = Query(default='json'),
                          x_forwarded_user: str | None = Header(default=None)):
        value = call(store.evidence, user(x_forwarded_user), project_id, evidence_id)
        if format == 'json':
            raw, media, filename = encode(value), 'application/json', 'evidence.json'
        elif format == 'markdown':
            output = value['content']
            lines = ['# 证据与大纲', '证据版本：' + value['id'],
                '输入版本：' + str(value.get('input_revision')),
                '状态：' + ('基于上一版范围，待更新（只读）' if value['stale'] else '已保存（只读）')]
            for item in output['evidence']:
                lines.extend(['## ' + item['evidence_id'], item['finding'],
                    '来源：' + item['paper_id'], '访问范围：' + item['access_level'],
                    '位置：' + item['locator'], '方法：' + item['method'], '限制：' + item['limitations']])
            lines.append('## 报告大纲')
            for section in output['sections']:
                lines.extend(['### ' + section['title'], *section['questions'],
                    '证据：' + '、'.join(section['evidence_ids'])])
            lines.extend(['## 证据缺口', *output['gaps']])
            raw, media, filename = '\n\n'.join(lines) + '\n', 'text/markdown; charset=utf-8', 'evidence.md'
        else:
            raise HTTPException(422, detail={'code': 'unsupported_export_format', 'retryable': False})
        return Response(raw, media_type=media, headers={
            'Content-Disposition': 'attachment; filename="' + filename + '"',
            'Cache-Control': 'private, no-store'})

    @router.get('/projects/{project_id}/versions/{version_id}/download')
    def download(project_id: str, version_id: str, format: str = Query(default='markdown'),
                 x_forwarded_user: str | None = Header(default=None)):
        value = call(store.version, user(x_forwarded_user), project_id, version_id)
        if format == 'markdown':
            raw, media, filename = value['report_markdown'], 'text/markdown; charset=utf-8', 'report.md'
        elif format == 'sources':
            raw, media, filename = encode(dict(schema_version='1', version_id=version_id,
                report_sha256=value['report_sha256'], evidence_snapshot_id=value['evidence_snapshot_id'],
                confirmation_id=value['confirmation_id'], sources=value['sources'],
                evidence=value['evidence'], claims=value['claims'], references=value['references'],
                origin=value.get('origin', 'website_generated'),
                model_trace_available=value.get('model_trace_available', True),
                source_review=value.get('source_review'), import_review=value.get('import_review'),
                status='final' if value['finalized'] else 'draft', publication_authorized=False)), \
                'application/json', 'sources.json'
        else:
            raise HTTPException(422, detail={'code': 'unsupported_export_format', 'retryable': False})
        return Response(raw, media_type=media, headers={
            'Content-Disposition': 'attachment; filename="' + filename + '"',
            'X-Research-Document-Status': 'final' if value['finalized'] else 'draft',
            'X-Research-Document-Origin': value.get('origin', 'website_generated'),
            'Cache-Control': 'private, no-store'})

    @router.get('/projects/{project_id}/compare')
    def compare(project_id: str, base_version_id: str, candidate_version_id: str,
                x_forwarded_user: str | None = Header(default=None)):
        return call(store.compare, user(x_forwarded_user), project_id, base_version_id, candidate_version_id)

    @router.post('/projects/{project_id}/annotations')
    def annotate(project_id: str, payload: dict = Body(...),
                 idempotency_key: str | None = Header(default=None),
                 x_forwarded_user: str | None = Header(default=None)):
        return call(store.annotate, user(x_forwarded_user), project_id, payload, idempotency_key)

    return router
