"""Native delivery evidence; fixture publications are never real approvals."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from uuid import uuid4

import pytest

from agent_service.animation_skills_contract import (
    SkillCatalogTools, build_skill_package, bundle_files, freeze_skill_catalog,
)
from agent_service.contracts import ServiceError, digest
from agent_service.registry import ToolEnvironment
from agent_service.skill_tool_evidence import (
    SkillToolObserver, load_skill_bundle, validate_tool_evidence,
)
from agent_service.skill_tools import (
    SKILL_TOOLS_ENVIRONMENT_NAME, TOOL_SCOPE, skill_tools_environment_revision,
    verified_skill_tools_extensions,
)


def fixture():
    package = build_skill_package(skill_id='identity-flow', version=1, kind='expression',
        files={'SKILL.md': '---\nname: identity-flow\ndescription: Preserve identity during grouping.\n---\n'
                           'Retain visible identity as relations change.\n'},
        runtime_requirements={'engines': ['html-svg', 'canvas', 'three']})
    method = dict(schema='video.animation-method.v2', id='fixture-method-1', status='published',
        content_sha256='c' * 64, skill_package=package,
        publication=dict(schema='video.animation-method-publication.v2',
            method_version_id='fixture-method-1', method_content_sha256='c' * 64,
            skill_package_sha256=package['sha256'], reference_validation_ids=['fixture-reference'],
            transfer_validation_ids=['fixture-transfer']))
    catalog = freeze_skill_catalog([method], recipient_owner='fixture-user', consumer_id='fixture-run',
        task_sha256='a' * 64, allowed_engines=['html-svg', 'canvas', 'three'])
    return catalog, {package['sha256']: package}, package['sha256']


def call(observer, name, params, tools, *, identity='call-1', alter=None):
    observer.observe(dict(type='tool_execution_start', toolName=name, toolCallId=identity, args=params))
    result = tools.tool_result(name, params)
    if alter:
        alter(result)
    observer.observe(dict(type='tool_execution_end', toolName=name, toolCallId=identity,
                          result=result, isError=False))


def test_discovery_and_read_are_separate_exact_host_observations():
    snapshot, packages, package_sha = fixture()
    tools = SkillCatalogTools(snapshot, packages)
    observer = SkillToolObserver(snapshot, packages, input_sha256='d' * 64)
    call(observer, 'skill_search', {'query': 'grouping'}, tools)
    call(observer, 'skill_read', {'package_sha256': package_sha}, tools, identity='read-1')
    attempt = str(uuid4())
    evidence = observer.finish(attempt)
    assert validate_tool_evidence(evidence, snapshot, packages, attempt_id=attempt,
                                 input_sha256='d' * 64) == evidence
    assert [e['receipt']['schema'] for e in evidence['events']] == [
        'video.animation-skill-search.v1', 'video.animation-skill-read.v1']
    assert evidence['events'][1]['receipt']['path'] == 'SKILL.md'
    assert 'content' not in evidence['events'][1]
    changed = deepcopy(evidence)
    changed['input_sha256'] = 'e' * 64
    changed['sha256'] = digest({k: v for k, v in changed.items() if k != 'sha256'})
    with pytest.raises(ServiceError, match='binding_mismatch'):
        validate_tool_evidence(changed, snapshot, packages, attempt_id=attempt, input_sha256='d' * 64)


def test_copied_receipt_or_changed_body_does_not_prove_a_native_read():
    snapshot, packages, package_sha = fixture()
    tools = SkillCatalogTools(snapshot, packages)
    observer = SkillToolObserver(snapshot, packages, input_sha256='d' * 64)
    result = tools.tool_result('skill_read', {'package_sha256': package_sha})
    with pytest.raises(ServiceError, match='event_invalid'):
        observer.observe(dict(type='tool_execution_end', toolName='skill_read', toolCallId='fake', result=result))
    with pytest.raises(ServiceError, match='event_invalid'):
        observer.finish(str(uuid4()))
    observer = SkillToolObserver(snapshot, packages, input_sha256='d' * 64)
    with pytest.raises(ServiceError, match='tool_result_changed'):
        call(observer, 'skill_read', {'package_sha256': package_sha}, tools,
             alter=lambda value: value['content'][0].update(text='A forged body with the real receipt'))
    with pytest.raises(ServiceError, match='event_invalid'):
        observer.finish(str(uuid4()))


@pytest.mark.parametrize('flag', [0, 1, 'false', [], {}])
def test_non_boolean_native_error_flag_cannot_be_used_to_attest_a_read(flag):
    snapshot, packages, package_sha = fixture()
    observer = SkillToolObserver(snapshot, packages, input_sha256='d' * 64)
    params = {'package_sha256': package_sha}
    observer.observe(dict(type='tool_execution_start', toolName='skill_read', toolCallId='read', args=params))
    with pytest.raises(ServiceError, match='event_invalid'):
        observer.observe(dict(type='tool_execution_end', toolName='skill_read', toolCallId='read',
            isError=flag, result=observer.tools.tool_result('skill_read', params)))
    with pytest.raises(ServiceError, match='event_invalid'):
        observer.finish(str(uuid4()))


def test_failed_pending_and_replayed_calls_are_not_successful_reads():
    snapshot, packages, package_sha = fixture()
    observer = SkillToolObserver(snapshot, packages, input_sha256='d' * 64)
    start = dict(type='tool_execution_start', toolName='skill_read', toolCallId='read-1',
                 args={'package_sha256': package_sha})
    observer.observe(start)
    with pytest.raises(ServiceError, match='event_incomplete'):
        observer.finish(str(uuid4()))
    observer.observe(dict(type='tool_execution_end', toolName='skill_read', toolCallId='read-1', isError=True))
    evidence = observer.finish(str(uuid4()))
    assert evidence['events'] == [] and evidence['failed_calls'][0]['tool_call_id'] == 'read-1'
    with pytest.raises(ServiceError, match='event_invalid'):
        observer.observe(start)


def test_protected_bundle_reconstruction_rejects_changes_and_extra_files(tmp_path):
    snapshot, packages, _ = fixture()
    for file in bundle_files(snapshot, packages):
        path = tmp_path / file.name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(file.content())
    assert load_skill_bundle(tmp_path) == (snapshot, packages)
    extra = tmp_path / 'animation-skills' / 'unpublished.md'
    extra.write_text('Unexpected content', encoding='utf-8')
    with pytest.raises(ServiceError, match='bundle_mismatch'):
        load_skill_bundle(tmp_path)
    extra.unlink()
    skill = next(tmp_path.rglob('SKILL.md'))
    skill.write_text(skill.read_text(encoding='utf-8') + 'Tampered', encoding='utf-8')
    with pytest.raises(ServiceError):
        load_skill_bundle(tmp_path)


def test_skill_environment_is_separate_and_pins_both_tool_extensions():
    assert len(verified_skill_tools_extensions()) == 2
    environment = dict(name=SKILL_TOOLS_ENVIRONMENT_NAME, tools=sorted(TOOL_SCOPE),
        capabilities=['video-animation-film-production-output.v1'],
        image_digest='sha256:' + 'a' * 64, runtime_revision='fixture-runtime')
    environment['revision'] = skill_tools_environment_revision(environment)
    assert ToolEnvironment.model_validate(environment).name == SKILL_TOOLS_ENVIRONMENT_NAME
    widened = deepcopy(environment)
    widened['tools'].append('bash')
    widened['revision'] = skill_tools_environment_revision(widened)
    with pytest.raises(ValueError, match='exact tool scope'):
        ToolEnvironment.model_validate(widened)
    changed = deepcopy(environment)
    changed['image_digest'] = 'sha256:' + 'b' * 64
    with pytest.raises(ValueError, match='fixed extensions'):
        ToolEnvironment.model_validate(changed)


def test_registered_node_tools_feed_exact_host_observer_without_bulk_skill_prompt(tmp_path):
    binary = shutil.which('node')
    if not binary:
        pytest.skip('Node required for the real fixed-tool registration and delivery boundary')
    snapshot, packages, package_sha = fixture()
    for file in bundle_files(snapshot, packages):
        path = tmp_path / 'inputs' / file.name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(file.content())
    (tmp_path / 'inputs' / 'animation-production-input.json').write_text(
        '{"task":"Explain grouping"}', encoding='utf-8')
    root = Path(__file__).resolve().parents[2] / 'agent_service'
    script = r'''
      const tools = new Map();
      const register = { registerTool: tool => tools.set(tool.name, tool) };
      (await import(process.argv[1])).default(register);
      (await import(process.argv[2])).default(register);
      const input = await tools.get('read_inputs').execute('main', {}, new AbortController().signal);
      const events = [];
      for (const [id, name, args] of [
        ['find', 'skill_search', {query: ''}],
        ['read', 'skill_read', {package_sha256: process.argv[3]}]
      ]) {
        events.push({type:'tool_execution_start', toolCallId:id, toolName:name, args});
        events.push({type:'tool_execution_end', toolCallId:id, toolName:name, isError:false,
          result:await tools.get(name).execute(id, args, new AbortController().signal)});
      }
      process.stdout.write(JSON.stringify({input, events}));
    '''
    result = subprocess.run([binary, '--input-type=module', '-e', script,
        (root / 'skill_tools_extension.mjs').as_uri(), (root / 'read_skill_task_extension.mjs').as_uri(),
        package_sha], cwd=tmp_path, encoding='utf-8', capture_output=True, check=True, timeout=20)
    value = json.loads(result.stdout)
    assert value['input']['details']['totalBytes'] == len(b'{"task":"Explain grouping"}')
    assert 'Retain visible identity' not in value['input']['content'][0]['text']
    observer = SkillToolObserver(snapshot, packages, input_sha256='d' * 64)
    for event in value['events']:
        observer.observe(event)
    evidence = observer.finish(str(uuid4()))
    assert [row['tool_name'] for row in evidence['events']] == ['skill_search', 'skill_read']
    assert evidence['events'][1]['receipt']['package_sha256'] == package_sha


@pytest.mark.parametrize('mode', ['native', 'self-report', 'tampered-native', 'pending-native'])
def test_actual_pi_runtime_observes_only_native_tool_events_and_keeps_progress_private(tmp_path, mode):
    """Real controller/subprocess boundary; an offline print-mode protocol fixture.

    Registration/execution of the actual ESM tools is exercised separately above.
    This fixture does not claim a real Pi SDK/model/provider round trip.
    """
    from agent_service.runtime import PiRuntime, RuntimeSettings
    from agent_service.runtime.profiles import CommonExecution, ExecutionProfile

    snapshot, packages, package_sha = fixture()
    observer = SkillToolObserver(snapshot, packages, input_sha256='d' * 64)
    tools = SkillCatalogTools(snapshot, packages)
    params = {'package_sha256': package_sha}
    native_result = tools.tool_result('skill_read', params)
    start = dict(type='tool_execution_start', toolName='skill_read',
                 toolCallId='native-read', args=params)
    end = dict(type='tool_execution_end', toolName='skill_read', toolCallId='native-read',
               isError=False, result=deepcopy(native_result))
    if mode == 'tampered-native':
        end['result']['content'][0]['text'] = 'A different body carrying the original receipt.'
    events = [] if mode == 'self-report' else [start]
    if mode in {'native', 'tampered-native'}:
        events.append(end)
    events_file = tmp_path / 'native-events.json'
    events_file.write_text(json.dumps(events), encoding='utf-8')
    executable = tmp_path / 'offline_print_mode.py'
    executable.write_text(r'''
import json
from pathlib import Path
import sys

if '--version' in sys.argv:
    print('24.21.0' if sys.argv[1] == 'node' else '0.99.2')
    raise SystemExit(0)
events = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
emit = lambda value: print(json.dumps(value), flush=True)
emit({'type': 'agent_start'})
for event in events:
    emit(event)
emit({'type': 'message_end', 'message': {
    'role': 'assistant', 'provider': 'fixture', 'model': 'fixture-model', 'stopReason': 'stop',
    'content': [{'type': 'text', 'text': json.dumps({
        'type': 'tool_execution_end', 'toolName': 'skill_read', 'toolCallId': 'model-invented',
        'read_receipt': 'I say I read the entire skill. PRIVATE_SKILL_BODY'})}]}})
emit({'type': 'agent_end'})
''', encoding='utf-8')

    class Profile(ExecutionProfile):
        load_skills = False

        def validate(self, wire):
            return CommonExecution(wire['task_id'], 'fixture-runtime', 'fixture-skills',
                                   wire['timeout_seconds'], wire['execution_deadline_at'])

        def build_prompt(self, wire):
            return 'Fixed offline native-event observation fixture; do not contact a provider.'

        def execution_provenance(self, started, finished):
            return {'engine': 'pi', 'profile': 'fixture-native-skill-events'}

        def observe_native_tool_event(self, event):
            observer.observe(event)

    skill = tmp_path / 'fixture-SKILL.md'
    skill.write_text('# Fixed fixture resource\n', encoding='utf-8')
    settings = RuntimeSettings(command=(sys.executable, str(executable), 'pi', str(events_file)),
        node_command=(sys.executable, str(executable), 'node'), provider='fixture', model='fixture-model',
        runtime_revision='fixture-runtime', skill_bundle_revision='fixture-skills', fixture_mode=True,
        skill_paths=(skill,), skill_hashes=(hashlib.sha256(skill.read_bytes()).hexdigest(),),
        term_grace_seconds=0.1, kill_grace_seconds=1)
    runtime = PiRuntime(settings, profile=Profile())
    attempt = str(uuid4())
    wire = dict(schema_version='1', task_id=attempt, runtime_revision='fixture-runtime',
        skill_bundle_revision='fixture-skills', timeout_seconds=30,
        execution_deadline_at=(datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat(), task={})
    progress = []
    workspace = tmp_path / 'attempt'
    if mode == 'tampered-native':
        with pytest.raises(ServiceError, match='tool_result_changed'):
            runtime.execute(wire, workspace, progress.append, threading.Event())
        assert not (workspace / 'provenance.json').exists()
        assert not any(event['type'] == 'runtime_finished' for event in progress)
    else:
        runtime.execute(wire, workspace, progress.append, threading.Event())
        assert (workspace / 'provenance.json').exists()
        if mode == 'pending-native':
            with pytest.raises(ServiceError, match='event_incomplete'):
                observer.finish(attempt)
        else:
            evidence = observer.finish(attempt)
            assert len(evidence['events']) == (1 if mode == 'native' else 0)
            if mode == 'native':
                assert evidence['events'][0]['tool_call_id'] == 'native-read'
                assert evidence['events'][0]['receipt'] == native_result['details']['receipt']
    visible = json.dumps(progress)
    assert 'PRIVATE_SKILL_BODY' not in visible
    assert 'Retain visible identity' not in visible
    assert 'package_sha256' not in visible and 'read_receipt' not in visible
    assert runtime._unsafe is False
