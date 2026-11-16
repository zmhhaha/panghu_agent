import json

from video_workbench.production_refinement_workflow import ProductionRefinementWorkflow
from video_workbench.production_sequence_store import ProductionSequenceStore
from video_workbench.store import Store


class Service:
    def __init__(self, sequences):
        self.sequences, self.calls, self.ticks = sequences, [], 0

    def start(self, owner, project, source, *, kind, request_id):
        self.calls.append(dict(owner=owner, project=project, source=source, kind=kind, request_id=request_id))
        known = self.sequences.source_for_run(owner, project, source['run_id'], source['version_id'])
        sequence = known['sequence']
        self.sequences.reserve_round(owner, project, sequence['id'], kind, source['version_id'], 'b' * 64,
            request_id, expected_revision=sequence['revision'])

    def tick(self):
        self.ticks += 1


def seed(tmp_path):
    store = Store(tmp_path / 'workbench.db')
    sequences = ProductionSequenceStore(tmp_path / 'sequences.db')
    with store.transaction() as db:
        db.execute('CREATE TABLE production_runs (document TEXT NOT NULL)')
    project = store.create_project('owner', dict(title='作品', script='原稿'))
    return store, sequences, project


def add_run(store, sequences, project, name, created_at):
    source = dict(run_id=name, version_id=name + '-draft', sha256='a' * 64)
    sequence = sequences.create('owner', project['id'], source, name)
    run = dict(owner='owner', project_id=project['id'], renderer_mode='hyperframes-film', status='completed',
        created_at=created_at, sequence_registration=dict(status='registered', sequence_id=sequence['id'], source=source))
    with store.transaction() as db:
        db.execute('INSERT INTO production_runs VALUES (?)', (json.dumps(run),))
    return run


def test_only_new_registered_films_admit_one_default_round_and_restart_does_not_reset(tmp_path):
    store, sequences, project = seed(tmp_path)
    old = add_run(store, sequences, project, 'old', 99)
    new = add_run(store, sequences, project, 'new', 101)
    service = Service(sequences)
    activation = tmp_path / 'activation.json'
    workflow = ProductionRefinementWorkflow(store, sequences, service, enabled=True,
        activation_path=activation, clock=lambda: 100)
    workflow.tick(); workflow.tick()
    assert len(service.calls) == 1 and service.calls[0]['source'] == new['sequence_registration']['source']
    assert service.calls[0]['kind'] == 'automatic'
    assert sequences.operations('owner', project['id'], old['sequence_registration']['sequence_id']) == []
    restarted = ProductionRefinementWorkflow(store, sequences, service, enabled=True,
        activation_path=activation, clock=lambda: 200)
    restarted.tick()
    assert len(service.calls) == 1 and restarted.activated_at == 100
    assert service.ticks == 3


def test_disabled_workflow_never_creates_activation_or_starts_paid_work(tmp_path):
    store, sequences, project = seed(tmp_path)
    add_run(store, sequences, project, 'new', 101)
    service = Service(sequences)
    activation = tmp_path / 'activation.json'
    workflow = ProductionRefinementWorkflow(store, sequences, service, enabled=False,
        activation_path=activation, clock=lambda: 100)
    workflow.tick(); workflow.start(); workflow.stop()
    assert service.calls == [] and service.ticks == 0 and not activation.exists()
