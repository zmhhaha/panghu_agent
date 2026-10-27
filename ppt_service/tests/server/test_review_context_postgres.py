"""PostgreSQL review transactions in a disposable schema; no production rows."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from ppt_service.server.app import create_app
from ppt_service.server.auth import Identity, Settings, authenticate
from ppt_service.server.migrate import migrate
from ppt_service.server.acceptance.contracts import VisualReviewCommand
from ppt_service.server.acceptance.domain import AcceptanceError
from review_fixture import seed_review


@pytest.fixture
def pg_review(tmp_path):
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('TEST_DATABASE_URL must point to a disposable test PostgreSQL')
    schema = 'ppt_review_' + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as db:
        db.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_engine(url, connect_args={'options': f'-csearch_path={schema}'})
    try:
        migrate(engine)
        app = create_app(Settings(url, str(tmp_path)), engine)
        owner = Identity('https://issuer', 'alice')
        app.dependency_overrides[authenticate] = lambda: owner
        ids = seed_review(app, owner)
        with TestClient(app) as client:
            yield app, client, owner, ids
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        admin.dispose()


def test_pg_actual_review_context_and_concurrent_source_locked_appends(pg_review):
    app, client, owner, ids = pg_review
    route = f"/api/v1/projects/{ids['pid']}/versions/{ids['vid']}/acceptance"
    first = client.get(route)
    assert first.status_code == 200
    data = first.json()
    command = VisualReviewCommand.model_validate({
        'kind': 'visual_review', 'expected_revision': 0, 'binding': data['binding'],
        'note': 'Synthetic PostgreSQL concurrency assertion',
        'pages': [{'number': page['number'], 'preview_sha256': page['preview_sha256'],
                   'verdict': 'pending', 'issues': []} for page in data['pages']]})

    def append(key):
        try:
            return app.state.acceptance.append(owner, ids['pid'], ids['vid'], command, key).revision
        except AcceptanceError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(append, ['concurrent-one', 'concurrent-two']))
    assert sorted(map(str, results)) == ['1', 'acceptance_revision_changed']
    saved = client.get(route).json()
    assert saved['revision'] == 1 and len(saved['records']) == 1
    key = 'concurrent-one' if results[0] == 1 else 'concurrent-two'
    assert append(key) == 1
    assert client.get(route).json()['publication_authorized'] is False
