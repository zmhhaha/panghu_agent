"""Accounting projections: observed amounts, owner scope and honest coverage."""
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ppt_service.server.app import create_app
from ppt_service.server.auth import Identity, Settings, authenticate
from ppt_service.server.migrate import migrate
from ppt_service.server.models import Job, Project, UserQuota
from ppt_service.server.repository import Repository, job_dto
from ppt_service.server.usage import money, normalize_agent_usage, quota_summary, usage_summary


@pytest.fixture
def accounting(tmp_path):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    migrate(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    owner = Identity('https://usage.fixture', 'owner')
    with sessions.begin() as session:
        project = Project(issuer=owner.issuer, subject=owner.sub, title='Usage fixture', settings={})
        session.add(project)
        session.flush()
        job = Job(project_id=project.id, issuer=owner.issuer, subject=owner.sub,
                  kind='generate', stage='generate', status='failed', attempt=1, input_snapshot={})
        session.add(job)
        session.flush()
        job_id = job.id
    yield SimpleNamespace(engine=engine, sessions=sessions, owner=owner, job_id=job_id, root=tmp_path)
    engine.dispose()


def record(session, jid, source, *, cost=None, currency=None, incoming=None, outgoing=None):
    return Repository(session).record_usage(jid, 1, phase='generate', outcome='failed',
        provider='service-model-proxy', provider_request_id=source,
        input_tokens=incoming, output_tokens=outgoing, reported_cost=cost, currency=currency)


def test_no_measurement_is_unknown_and_never_zero(accounting):
    with accounting.sessions() as session:
        job = session.get(Job, accounting.job_id)
        value = job_dto(job, session)['usage']
        assert value['scope'] == 'task'
        assert value['input_tokens'] is value['output_tokens'] is value['reported_cost'] is None
        assert value['tokens_status'] == value['cost_status'] == 'unknown'
        assert value['record_count'] == 0 and value['unknown_record_count'] == 1


def test_known_zero_is_reported_without_inventing_missing_cost(accounting):
    with accounting.sessions.begin() as session:
        record(session, accounting.job_id, 'known-zero', incoming=0, outgoing=0, cost='0', currency='USD')
    with accounting.sessions() as session:
        value = usage_summary(session, owner=accounting.owner)
        assert value['scope'] == 'account' and value['tokens_status'] == 'known'
        assert value['input_tokens'] == value['output_tokens'] == 0
        assert value['reported_cost'] == '0' and value['currency'] == 'USD'
        assert value['cost_status'] == 'reported'


def test_partial_counter_and_unknown_cost_do_not_become_complete(accounting):
    with accounting.sessions.begin() as session:
        record(session, accounting.job_id, 'measured', incoming=7, outgoing=5, cost='0.10', currency='USD')
        record(session, accounting.job_id, 'missing', incoming=3)
    with accounting.sessions() as session:
        value = usage_summary(session, owner=accounting.owner)
        assert value['input_tokens'] == 10 and value['output_tokens'] == 5
        assert value['tokens_status'] == value['cost_status'] == 'partial'
        assert value['reported_cost'] == '0.10' and value['unknown_record_count'] == 1


def test_missing_historical_attempt_keeps_measured_totals_partial(accounting):
    with accounting.sessions.begin() as session:
        session.get(Job, accounting.job_id).attempt = 2
        record(session, accounting.job_id, 'historical', incoming=7, outgoing=5,
               cost='0.10', currency='USD')
    with accounting.sessions() as session:
        value = usage_summary(session, owner=accounting.owner)
        assert value['input_tokens'] == 7 and value['output_tokens'] == 5
        assert value['reported_cost'] == '0.10' and value['currency'] == 'USD'
        assert value['tokens_status'] == value['cost_status'] == 'partial'
        assert value['record_count'] == 1 and value['unknown_record_count'] == 1


def test_unsafe_aggregate_token_total_stays_unknown(accounting):
    with accounting.sessions.begin() as session:
        record(session, accounting.job_id, 'large', incoming=2**53 - 1, outgoing=0)
        record(session, accounting.job_id, 'extra', incoming=1, outgoing=0)
    with accounting.sessions() as session:
        value = usage_summary(session, owner=accounting.owner)
        assert value['input_tokens'] is None and value['output_tokens'] == 0
        assert value['tokens_status'] == 'partial'


def test_currency_totals_never_add_incompatible_units_or_leak_other_owners(accounting):
    with accounting.sessions.begin() as session:
        record(session, accounting.job_id, 'usd1', incoming=1, outgoing=1, cost='0.1', currency='USD')
        record(session, accounting.job_id, 'usd2', incoming=2, outgoing=2, cost='0.2', currency='USD')
        record(session, accounting.job_id, 'eur', incoming=3, outgoing=3, cost='0.4', currency='EUR')
        other = Project(issuer=accounting.owner.issuer, subject='other', title='Other', settings={})
        session.add(other)
        session.flush()
        job = Job(project_id=other.id, issuer=other.issuer, subject=other.subject,
                  kind='generate', stage='review', status='succeeded', attempt=1, input_snapshot={})
        session.add(job)
        session.flush()
        record(session, job.id, 'private', incoming=9999, outgoing=9999, cost='9999', currency='USD')
    with accounting.sessions() as session:
        value = usage_summary(session, owner=accounting.owner)
        assert value['input_tokens'] == value['output_tokens'] == 6
        assert value['cost_status'] == 'mixed_currency'
        assert value['reported_cost'] is value['currency'] is None
        assert value['cost_breakdown'] == [{'currency': 'EUR', 'reported_cost': '0.4'},
                                           {'currency': 'USD', 'reported_cost': '0.3'}]
        assert usage_summary(session, owner=Identity('https://another.fixture', 'owner'))['record_count'] == 0


def test_complete_mixed_currency_costs_do_not_claim_unknown_for_partial_tokens(accounting):
    with accounting.sessions.begin() as session:
        first = record(session, accounting.job_id, 'usd', incoming=1, outgoing=1,
                       cost='0.25', currency='USD')
        second = record(session, accounting.job_id, 'eur', incoming=2, outgoing=2,
                        cost='0.40', currency='EUR')
        # An Agent's measured counters alone do not establish complete token
        # coverage, independently of these explicit reported supplier costs.
        first.provider = second.provider = 'agent-service'
    with accounting.sessions() as session:
        value = usage_summary(session, owner=accounting.owner)
        assert value['input_tokens'] == value['output_tokens'] == 3
        assert value['tokens_status'] == 'partial'
        assert value['cost_status'] == 'mixed_currency'
        assert value['unknown_record_count'] == 0
        assert value['cost_breakdown'] == [{'currency': 'EUR', 'reported_cost': '0.40'},
                                           {'currency': 'USD', 'reported_cost': '0.25'}]


@pytest.mark.parametrize('value', [True, -1, float('nan'), float('inf'), 'NaN', '1e100', '1e-99999999', 'private text'])
def test_invalid_cost_is_unknown(value):
    assert money(value) is None


def test_agent_api_normalization_requires_reported_currency_status_and_safe_counts():
    value = normalize_agent_usage({'input_tokens': True, 'output_tokens': 2**63 - 1,
        'reported_cost': 0, 'cost_status': 'unknown', 'currency': 'USD', 'private_logs': 'secret'})
    assert value['input_tokens'] is value['output_tokens'] is value['reported_cost'] is None
    assert value['cost_status'] == 'unknown' and 'private_logs' not in value
    good = normalize_agent_usage({'input_tokens': 4, 'output_tokens': 2, 'reported_cost': 0.125,
        'cost_status': 'known', 'currency': 'EUR', 'completeness': 'complete'})
    assert good['reported_cost'] == '0.125' and good['tokens_complete'] is True
    assert money(Decimal('0.0000001')) == '0.0000001'


def test_profile_and_job_expose_independent_usage_and_typed_quota_counts(accounting):
    app = create_app(Settings('sqlite://', str(accounting.root)), accounting.engine)
    who = [accounting.owner]
    app.dependency_overrides[authenticate] = lambda: who[0]
    with accounting.sessions.begin() as session:
        session.add(UserQuota(issuer=who[0].issuer, subject=who[0].sub, enabled=True,
            period='operator-defined', generation_limit=8, generation_count=3,
            revision_limit=5, revision_count=2))
        record(session, accounting.job_id, 'profile', incoming=7)
    client = TestClient(app)
    profile = client.get('/api/v1/me').json()
    assert profile['usage']['scope'] == 'account' and profile['usage']['input_tokens'] == 7
    assert profile['quota']['generation_count'] == 3 and profile['quota']['generation_remaining'] == 5
    assert profile['quota']['revision_count'] == 2 and profile['quota']['revision_remaining'] == 3
    assert client.get('/api/v1/jobs/' + accounting.job_id).json()['usage']['scope'] == 'task'
    who[0] = Identity(who[0].issuer, 'other')
    assert client.get('/api/v1/jobs/' + accounting.job_id).status_code == 404
    assert client.get('/api/v1/me').json()['usage']['input_tokens'] is None


def test_disabled_or_incomplete_quota_never_guesses_remaining():
    assert quota_summary(None)['generation_remaining'] is None
    value = SimpleNamespace(enabled=False, period='current', generation_limit=10,
                            generation_count=2, revision_limit=5, revision_count=1)
    assert quota_summary(value)['generation_remaining'] is None
    value.enabled = True
    value.period = None
    assert quota_summary(value)['generation_remaining'] is None
