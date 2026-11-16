"""Synthetic Docker boundary, real PNG/envelope and durable recovery checks.

These tests do not execute authored Python or assert real Cairo rendering.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import io
import json
from pathlib import Path
import sqlite3
import threading
import time

from PIL import Image
import pytest

from agent_service.contracts import ServiceError, canonical_bytes
from video_workbench import manim_resource as resource
from video_workbench import manim_resource_executor as module
from video_workbench.manim_resource_job import freeze_resource_job


IMAGE = 'registry.example/manim:latest@sha256:' + 'a' * 64
CONFIG = 'sha256:' + 'b' * 64
CONTAINER = 'c' * 64
BINDING = dict(owner='organization/person@example.com', effect_id='effect',
    experiment_id='experiment', operation_id='operation')


def plan(**changes):
    request = dict(schema=resource.REQUEST_SCHEMA, resource_id='geometry',
        source='from manim import Scene\nclass Geometry(Scene):\n    pass\n',
        scene_class='Geometry', width=320, height=180, fps=24, frame_count=3,
        global_start_frame=48, seed=7, transparent=True, manim_version='0.22.1')
    return freeze_resource_job(request, **dict(BINDING, **changes), image=IMAGE,
        recipe_input_sha256='d' * 64, recipe_result_sha256='e' * 64,
        recipe_stop_proof_sha256='f' * 64, timeout_seconds=30)


class AbruptCrash(BaseException):
    pass


class DockerBoundary:
    """Keep the actual ownership/cleanup primitives; replace only Docker I/O."""
    def __init__(self, monkeypatch, *, failure=None):
        self.failure = failure
        self.containers = {}
        self.calls = []
        self.creates = self.starts = self.removes = 0
        self.create_arguments = None
        self.entered = self.release = None
        monkeypatch.setattr(module.docker, '_command', self.command)
        monkeypatch.setattr(module.docker, '_ownership', lambda *args, **kwargs: None)
        monkeypatch.setattr(module.ManimResourceExecutor, '_attach', self.attach)

    def command(self, arguments, timeout, log_path=None):
        self.calls.append(list(arguments))
        if arguments[:2] == ['image', 'inspect']:
            inspected = dict(image_id=CONFIG, repo_digests=[IMAGE.split(':latest@')[0] + '@sha256:' + 'a' * 64])
            if self.failure == 'wrong_image':
                inspected['repo_digests'] = []
            return dict(code=0, out=json.dumps(inspected), err='')
        if arguments[:2] == ['container', 'create']:
            self.creates += 1
            self.create_arguments = arguments
            name = arguments[arguments.index('--name') + 1]
            labels = dict(arguments[i + 1].split('=', 1) for i, word in enumerate(arguments) if word == '--label')
            value = dict(id=CONTAINER, name='/' + name, image=CONFIG, running=False,
                exit_code=0, oom_killed=False, owner=labels[module.docker._OWNER],
                capture=labels[module.docker._CAPTURE])
            self.containers[name] = value
            if self.failure == 'foreign_owner':
                value['owner'] = 'foreign'
            if self.failure == 'crash_create_response':
                raise AbruptCrash()
            return dict(code=0, out=CONTAINER, err='')
        if arguments[:2] == ['container', 'inspect']:
            value = next((value for name, value in self.containers.items()
                if arguments[-1] in (name, value['id'])), None)
            return dict(code=0, out=json.dumps(value), err='') if value is not None else dict(
                code=1, out='', err='No such container')
        if arguments[:2] == ['container', 'rm']:
            if self.failure in ('cleanup_unknown', 'crash_cleanup'):
                if self.failure == 'crash_cleanup':
                    raise AbruptCrash()
                return dict(code=1, out='', err='synthetic unreachable daemon')
            self.removes += 1
            self.containers = {name: value for name, value in self.containers.items() if value['id'] != arguments[-1]}
            return dict(code=0, out='', err='')
        raise AssertionError(arguments)

    def attach(self, container_id, directory, binding, deadline):
        self.starts += 1
        value = next(iter(self.containers.values()))
        value['running'] = True
        if self.entered is not None:
            self.entered.set()
            assert self.release.wait(10)
        if self.failure == 'crash_running':
            raise AbruptCrash()
        if self.failure == 'execution_failure':
            value.update(running=False, exit_code=1)
            raise ServiceError('synthetic_worker_failure', 409)
        request = json.loads((directory / 'input/request.json').read_bytes())
        frames = {}
        for index in range(request['frame_count']):
            output = io.BytesIO()
            Image.new('RGBA', (320, 180), color=(index * 70, 3, 5, 120)).save(output, format='PNG')
            frames[resource.frame_path('geometry', index)] = output.getvalue()
        files = resource.compile_resource(request, frames, runtime_image_digest='sha256:' + 'a' * 64)
        for name, raw in files.items():
            path = directory / 'output/attempt/resource' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        (directory / 'output/attempt/source.py').write_text('synthetic diagnostics stay outside deliverable')
        if self.failure == 'extra_file':
            (directory / 'output/attempt/resource/extra.py').write_text('not deliverable')
        value.update(running=False, exit_code=0, oom_killed=self.failure == 'oom')


def executor(tmp_path, **changes):
    return module.ManimResourceExecutor(tmp_path / 'private', image=IMAGE, enabled=True, **changes)


def test_disabled_construction_is_inert_and_latest_pin_required(tmp_path):
    root = tmp_path / 'private'
    instance = module.ManimResourceExecutor(root, image=IMAGE)
    assert not root.exists()
    with pytest.raises(ServiceError, match='disabled'):
        instance.execute(plan())
    assert not root.exists()
    with pytest.raises(ServiceError, match='pin_required'):
        module.ManimResourceExecutor(root, image=IMAGE.replace(':latest@', ':version@'))
    with pytest.raises(ServiceError, match='not_found'):
        instance.lookup(**BINDING)
    assert not root.exists()


def test_completed_assets_are_collected_only_after_owned_cleanup_and_replay_is_readonly(tmp_path, monkeypatch):
    backend = DockerBoundary(monkeypatch)
    instance = executor(tmp_path)
    original_collect = instance._collect
    def collect(row, frozen):
        assert not backend.containers and backend.removes == 1
        return original_collect(row, frozen)
    monkeypatch.setattr(instance, '_collect', collect)
    result = instance.execute(plan())
    assert result['status'] == 'completed' and result['stop_proven']
    assert result['generated_assets']['request'] == plan()['request']
    assert len(result['generated_assets']['files']) == 4
    assert not any(name.endswith('.py') for name in result['generated_assets']['files'])
    before = len(backend.calls)
    assert instance.lookup(**BINDING) == result
    assert instance.execute(plan()) == result
    assert executor(tmp_path).recover(**BINDING) == result
    assert len(backend.calls) == before and backend.creates == backend.starts == 1
    arguments = backend.create_arguments
    for flag in ('--pull=never', '--network=none', '--read-only', '--user=10001:10001',
            '--cap-drop=ALL', '--security-opt=no-new-privileges', '--cpus=2', '--memory=2g',
            '--pids-limit=256', '--restart=no', '--log-driver=none', '--init'):
        assert flag in arguments
    assert arguments[arguments.index('--entrypoint=python') + 1] == CONFIG
    mounts = [arguments[i + 1] for i, word in enumerate(arguments) if word == '--mount']
    assert len(mounts) == 2
    assert mounts[0].endswith(',target=/input,readonly')
    assert mounts[1].endswith(',target=/output')
    assert not any('docker.sock' in value for value in mounts)
    assert arguments[-6:] == ['-m', 'video_workbench.manim_resource_worker',
        '--request', '/input/request.json', '--output', '/output/attempt']


@pytest.mark.parametrize('change', ['owner', 'recipe', 'source', 'image'])
def test_existing_operation_cannot_change_scope_source_recipe_or_image(tmp_path, monkeypatch, change):
    backend = DockerBoundary(monkeypatch)
    instance = executor(tmp_path)
    instance.execute(plan())
    altered = plan(owner='someone-else') if change == 'owner' else deepcopy(plan())
    if change == 'recipe':
        altered['recipe_result_sha256'] = '1' * 64
    elif change == 'source':
        altered['request']['source'] += '# changed\n'
    elif change == 'image':
        altered['image'] = altered['image'].replace('a' * 64, '1' * 64)
    if change != 'owner':
        from agent_service.contracts import digest
        altered['sha256'] = digest({key: value for key, value in altered.items() if key != 'sha256'})
    with pytest.raises(ServiceError):
        instance.execute(altered)
    with pytest.raises(ServiceError, match='not_found'):
        instance.lookup(**dict(BINDING, owner='someone-else'))
    assert backend.creates == 1


@pytest.mark.parametrize('failure', ['wrong_image', 'execution_failure', 'extra_file', 'oom'])
def test_known_failure_never_returns_assets_or_reexecutes(tmp_path, monkeypatch, failure):
    backend = DockerBoundary(monkeypatch, failure=failure)
    instance = executor(tmp_path)
    result = instance.execute(plan())
    assert result['status'] == 'failed' and result['stop_proven']
    assert result['generated_assets'] is None and result['error']
    assert result['error'] == {'wrong_image': 'manim_resource_executor_image_changed',
        'execution_failure': 'synthetic_worker_failure',
        'extra_file': 'manim_resource_file_inventory_invalid',
        'oom': 'manim_resource_executor_exit_invalid'}[failure]
    before = len(backend.calls)
    assert instance.execute(plan()) == result
    assert instance.recover(**BINDING) == result
    assert len(backend.calls) == before and not backend.containers


def test_foreign_container_is_never_removed_or_adopted(tmp_path, monkeypatch):
    backend = DockerBoundary(monkeypatch, failure='foreign_owner')
    instance = executor(tmp_path)
    result = instance.execute(plan())
    assert result['status'] == 'needs_recovery' and not result['stop_proven']
    assert backend.removes == 0 and result['generated_assets'] is None
    assert instance.recover(**BINDING)['status'] == 'needs_recovery'
    assert backend.removes == 0 and backend.creates == 1


def test_cleanup_uncertainty_blocks_collection_until_explicit_recovery(tmp_path, monkeypatch):
    backend = DockerBoundary(monkeypatch, failure='cleanup_unknown')
    instance = executor(tmp_path)
    result = instance.execute(plan())
    assert result['status'] == 'needs_recovery' and not result['stop_proven']
    assert result['generated_assets'] is None
    assert instance.execute(plan()) == result and backend.creates == 1
    backend.failure = None
    recovered = executor(tmp_path).recover(**BINDING)
    assert recovered['status'] == 'completed' and recovered['stop_proven']
    assert backend.creates == backend.starts == backend.removes == 1


@pytest.mark.parametrize('failure', ['crash_create_response', 'crash_running'])
def test_interrupted_unknown_execution_is_stopped_never_restarted_or_guessed_success(tmp_path, monkeypatch, failure):
    backend = DockerBoundary(monkeypatch, failure=failure)
    instance = executor(tmp_path)
    with pytest.raises(AbruptCrash):
        instance.execute(plan())
    assert backend.creates == 1 and backend.removes == 0
    before = len(backend.calls)
    assert instance.lookup(**BINDING)['status'] == 'running'
    assert len(backend.calls) == before
    assert instance.execute(plan())['status'] == 'running'
    backend.failure = None
    result = executor(tmp_path).recover(**BINDING)
    assert result['status'] == 'failed' and result['stop_proven']
    assert result['generated_assets'] is None and backend.creates == backend.removes == 1
    assert backend.starts == (0 if failure == 'crash_create_response' else 1)


def test_crash_after_durable_success_can_recover_only_original_stopped_output(tmp_path, monkeypatch):
    backend = DockerBoundary(monkeypatch, failure='crash_cleanup')
    instance = executor(tmp_path)
    with pytest.raises(AbruptCrash):
        instance.execute(plan())
    backend.failure = None
    assert executor(tmp_path).recover(**BINDING)['status'] == 'completed'
    assert backend.creates == backend.starts == backend.removes == 1


def test_concurrent_replay_cancel_and_recover_do_not_stop_active_execution_or_repeat(tmp_path, monkeypatch):
    backend = DockerBoundary(monkeypatch)
    backend.entered, backend.release = threading.Event(), threading.Event()
    instance = executor(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(instance.execute, plan())
        assert backend.entered.wait(10)
        assert executor(tmp_path).execute(plan())['status'] == 'running'
        cancelled = executor(tmp_path).cancel(**BINDING)
        assert cancelled['cancel_requested'] and not cancelled['stop_proven']
        assert executor(tmp_path).recover(**BINDING)['status'] == 'running'
        assert backend.removes == 0 and backend.creates == 1
        backend.release.set()
        result = future.result(10)
    assert result['status'] == 'cancelled' and result['stop_proven']
    assert result['generated_assets'] is None and backend.removes == 1
    assert executor(tmp_path).lookup(**BINDING) == result


def test_completed_result_is_revalidated_and_tampering_is_not_silently_accepted(tmp_path, monkeypatch):
    DockerBoundary(monkeypatch)
    instance = executor(tmp_path)
    instance.execute(plan())
    with sqlite3.connect(instance.ledger) as db:
        raw = db.execute('SELECT result FROM manim_resource_operations').fetchone()[0]
        altered = json.loads(raw)
        altered['generated_assets']['request']['global_start_frame'] += 1
        changed = canonical_bytes(altered)
        import hashlib
        db.execute('UPDATE manim_resource_operations SET result=?,result_sha256=?',
            (changed, hashlib.sha256(changed).hexdigest()))
    with pytest.raises(ServiceError, match='completion_unproven'):
        executor(tmp_path).lookup(**BINDING)


def test_lost_ledger_cannot_authorize_reexecution_of_existing_operation_tree(tmp_path, monkeypatch):
    backend = DockerBoundary(monkeypatch)
    instance = executor(tmp_path)
    instance.execute(plan())
    instance.ledger.unlink()
    with pytest.raises(ServiceError, match='ledger_missing'):
        executor(tmp_path).execute(plan())
    assert backend.creates == 1


def test_real_attach_loop_observes_cancellation_and_reaps_only_cli_before_owner_cleanup(tmp_path, monkeypatch):
    instance = executor(tmp_path)
    instance._claim(plan())
    directory = tmp_path / 'attach'
    directory.mkdir()
    class Process:
        returncode = None
        killed = False
        waited = False
        def poll(self):
            return self.returncode
        def kill(self):
            self.killed = True
            self.returncode = -9
        def wait(self, timeout):
            self.waited = True
    process = Process()
    def spawn(arguments, **kwargs):
        assert arguments == ['docker', 'container', 'start', '--attach', CONTAINER]
        instance.cancel(**BINDING)
        return process
    monkeypatch.setattr(module.subprocess, 'Popen', spawn)
    with pytest.raises(ServiceError, match='cancel_requested'):
        instance._attach(CONTAINER, directory, BINDING, time.monotonic() + 10)
    assert process.killed and process.waited
    assert not instance.lookup(**BINDING)['stop_proven']


def test_attach_does_not_start_a_cancelled_or_expired_attempt(tmp_path, monkeypatch):
    instance = executor(tmp_path)
    instance._claim(plan())
    monkeypatch.setattr(module.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('must not start'))
    with pytest.raises(TimeoutError, match='timeout'):
        instance._attach(CONTAINER, tmp_path, BINDING, time.monotonic() - 1)
    instance.cancel(**BINDING)
    with pytest.raises(ServiceError, match='cancel_requested'):
        instance._attach(CONTAINER, tmp_path, BINDING, time.monotonic() + 10)
