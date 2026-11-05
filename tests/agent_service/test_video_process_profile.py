"""Explicit stopped process candidates and opt-in catalogs; no model requests."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from agent_service import catalog
from agent_service.contracts import InlineFile, ServiceError, canonical_bytes
from agent_service.engines import StopEvidence
from agent_service.profiles import implementation
from agent_service.profiles import video_process as process
from agent_service.profiles.video_motion import validate_motion_input, validate_motion_result
from agent_service.registry import TaskContract
from test_catalog import image_packages


BEAT = 'beat_0123456789abcdef'


def document():
    text = '请求从入口进入队列，随后执行。'
    beats = dict(schema='video.beats.v1', source_text=text, spoken_text=text, segments=[
        dict(id=BEAT, source_start=0, source_end=len(text), spoken_start=0, spoken_end=len(text),
             source_text=text, spoken_text=text)])
    return dict(schema=process.INPUT_SCHEMA, title='排队', style='', visual_description=text,
                beats=beats, instruction='', base_plan=None)


def plan():
    def action(kind, target, placement, **fields):
        return dict(kind=kind, target=target, placement=placement, beat_id=BEAT, pace='quick', **fields)

    return dict(schema='video.process-plan.v1', template='flow', title='同一请求进入队列', takeaway='对象保留，状态改变',
        elements=[dict(id=slot, kind='card', slot=slot, label=slot, detail='')
                  for slot in ('source', 'queue', 'worker')] + [
            dict(id='request', kind='token', slot='token_source', label='请求', detail='')],
        relations=[dict(id='enter', source='source', target='queue', label='排队')],
        actions=[action('reveal', 'request', 'start'), action('draw', 'enter', 'start'),
                 action('transfer', 'request', 'middle', relation_id='enter'),
                 action('set_state', 'queue', 'end', state='waiting')])


def prepare(tmp_path):
    handler = implementation(process.PROFILE)
    (tmp_path / 'inputs').mkdir()
    raw = canonical_bytes(document())
    (tmp_path / 'inputs' / process.INPUT_NAME).write_bytes(raw)
    (tmp_path / 'output.json').write_bytes(canonical_bytes(plan()))
    envelope = SimpleNamespace(profile=handler.name, profile_revision='1', attempt_id='process-attempt',
        task=dict(input_sha256=hashlib.sha256(raw).hexdigest()),
        inputs=[InlineFile.from_bytes(process.INPUT_NAME, raw)], checkpoint_inputs=[], checkpoint_ref=None)
    return handler, envelope


def test_process_contract_is_separate_read_write_and_original_pinned_resources():
    handler = implementation(process.PROFILE)
    contract = TaskContract(name=handler.name, revision='1', task_schema='video-process-plan.v1',
        output_schema='video-process-plan-output.v1', required_tools=['read', 'write'],
        required_model_capabilities=['text', 'tool_calls'])
    assert handler.required_tools == ('read', 'write') and contract.max_input_files is None
    assert contract.limits()['max_input_files'] == 1
    assert set(process.verified_bundle()) == process.BUNDLE_FILES
    prompt = handler.build_prompt({'input_sha256': 'a' * 64})
    assert 'inputs/process-input.json' in prompt
    assert 'does not replace general flow' in prompt
    assert 'same token identity' in prompt and 'actual audio alignment' in prompt
    assert 'Do not render' in prompt and 'Read/write only' in prompt
    with pytest.raises(ServiceError):
        validate_motion_input(document())
    with pytest.raises(ServiceError):
        validate_motion_result(dict(schema=process.RESULT_SCHEMA, input_sha256='a' * 64, plan=plan()),
                               'a' * 64, document()['beats'])


def test_stopped_collection_binds_exact_profile_input_and_plan(tmp_path):
    handler, envelope = prepare(tmp_path)
    handler.validate_inputs(tmp_path, envelope.task)
    result = handler.collect_output(tmp_path, envelope, StopEvidence('process-attempt', True, ('worker-1',)))
    assert len(result) == 1 and result[0].name == process.RESULT_NAME
    parsed = json.loads(result[0].content())
    assert parsed['schema'] == process.RESULT_SCHEMA and parsed['plan'] == plan()
    assert parsed['input_sha256'] == envelope.task['input_sha256']


@pytest.mark.parametrize('change', ['profile', 'revision', 'attempt', 'not_stopped', 'ref_name',
    'digest', 'input_bytes', 'noncanonical', 'extra_input', 'checkpoint', 'wrong_schema', 'free_code'])
def test_collector_rejects_cross_contract_or_unproven_output(tmp_path, change):
    handler, envelope = prepare(tmp_path)
    proof = StopEvidence('process-attempt', True, ('worker-1',))
    if change == 'profile':
        envelope.profile = 'video.motion-plan.v1'
    elif change == 'revision':
        envelope.profile_revision = 'v1'
    elif change == 'attempt':
        proof = StopEvidence('other-attempt', True, ('worker-1',))
    elif change == 'not_stopped':
        proof = StopEvidence('process-attempt', False, ('worker-1',))
    elif change == 'ref_name':
        envelope.inputs = [InlineFile.from_bytes('motion-input.json', canonical_bytes(document()))]
    elif change == 'digest':
        envelope.task['input_sha256'] = 'b' * 64
    elif change in {'input_bytes', 'noncanonical'}:
        source = document()
        if change == 'input_bytes':
            source['title'] = 'changed'
        (tmp_path / 'inputs' / process.INPUT_NAME).write_bytes(
            json.dumps(source).encode() if change == 'noncanonical' else canonical_bytes(source))
    elif change == 'extra_input':
        (tmp_path / 'inputs' / 'extra.txt').write_text('extra')
    elif change == 'checkpoint':
        envelope.checkpoint_ref = object()
    else:
        revised = plan()
        revised.update(schema='video.motion-plan.v1') if change == 'wrong_schema' else revised.update(js='alert(1)')
        (tmp_path / 'output.json').write_bytes(canonical_bytes(revised))
    with pytest.raises(ServiceError):
        handler.collect_output(tmp_path, envelope, proof)


def test_bundle_tamper_is_detected_before_prompt_or_collection(tmp_path, monkeypatch):
    for name in ('manifest.json', *process.BUNDLE_FILES):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((process.BUNDLE_ROOT / name).read_bytes())
    monkeypatch.setattr(process, 'BUNDLE_ROOT', tmp_path)
    assert process.verified_bundle()
    (tmp_path / 'PLAN.schema.json').write_text('{}')
    with pytest.raises(ServiceError, match='process_bundle_integrity'):
        process.verified_bundle()


def test_catalog_process_is_disabled_opt_in_without_changing_old_bindings(image_packages):
    kwargs = dict(pi_image='registry.invalid/pi@sha256:' + 'a' * 64, runtime_revision='test', include_motion=True)
    old, old_hosts, _ = catalog.generate(**kwargs)
    new, hosts, summary = catalog.generate(**kwargs, include_process=True)
    assert not any(row['name'] == process.PROFILE for row in old['task_contracts'])
    assert all(row in new['task_contracts'] for row in old['task_contracts'])
    assert all(row in new['tool_environments'] for row in old['tool_environments'])
    assert all(row in new['execution_bindings'] for row in old['execution_bindings'])
    for name, host in old_hosts.items():
        assert {key: value for key, value in hosts[name].items() if key != 'execution_catalog'} == {
            key: value for key, value in host.items() if key != 'execution_catalog'}
        assert all(row in hosts[name]['execution_catalog'][section]
                   for section, rows in host['execution_catalog'].items() for row in rows)
    binding = next(row for row in new['execution_bindings'] if row['profile'] == process.PROFILE)
    assert binding['bundle'] == dict(name='video-process', revision='video-process-v1')
    assert next(row for row in new['routes'] if row['profile'] == process.PROFILE)['enabled'] is False
    assert all(not row['enabled'] for row in new['kubernetes_workers'])
    assert process.PROFILE in next(row for row in new['callers'] if row['caller'] == 'video-workbench')['profiles']
    assert summary['network_requests'] == 0 and summary['acceptance_created'] is False


def test_process_result_does_not_mutate_the_plan():
    value = dict(schema=process.RESULT_SCHEMA, input_sha256='a' * 64, plan=plan())
    original = deepcopy(value)
    assert process.validate_process_result(value, 'a' * 64, document()['beats']) == original
    assert value == original
