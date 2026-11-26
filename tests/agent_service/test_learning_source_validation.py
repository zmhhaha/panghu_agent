"""Learning author syntax checks use the browser script goal and fixed receipts."""
from copy import deepcopy
import json
import shutil
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.contracts import InlineFile, ServiceError, canonical_bytes, digest
from agent_service.diagnostics import SAFE_OUTPUT_CODES
from agent_service.engines import StopEvidence
from agent_service.learning_source_tools import (
    SOURCE_ENVIRONMENTS, checked_environment_revision, verified_source_check_extension,
)
from agent_service.learning_source_validation import SYNTAX_ERROR, check_workspace, validate_source_syntax
from agent_service.profiles.video_learning_source_handoff import SOURCE_HANDOFF
from agent_service.worker_api import _output_failure_diagnostic
from agent_service.worker_events import valid_worker_payload
from agent_service.deploy.acceptance_probe import _record_learning_source_check, candidate_config
import test_picture_learning_catalog as picture_catalog
import test_picture_learning_profile as picture
import test_reference_learning_catalog as reference_catalog
import test_reference_learning_profile as reference


pytestmark = pytest.mark.skipif(not shutil.which('node'), reason='Node is required for browser syntax checking')
_INVALID_SHADER = "const finish = '#include <tonemapping_fragment>\n#include <colorspace_fragment>';"
_VALID_SHADER = _INVALID_SHADER.replace("'#", "`#").replace(">';", ">`;")


@pytest.mark.parametrize('fixture', [picture, reference])
def test_checked_author_reports_syntax_and_rechecks_exact_stopped_source(tmp_path, fixture):
    protocol = fixture.protocol
    context = fixture.document()
    context['source_handoff'] = SOURCE_HANDOFF
    fixture.stage(tmp_path, context)
    result = fixture.result(context)
    source = result['source']
    source_dir = tmp_path / 'output-source'
    source_dir.mkdir()
    (source_dir / 'scene.html').write_bytes(b'<script type="module" src="./scene.js"></script>')
    scene_path = source_dir / 'scene.js'
    line_prefix = '\n' * 28
    scene_path.write_bytes((line_prefix + _INVALID_SHADER).encode())
    result['source'] = dict(engine=source['engine'], strategy=source['strategy'],
                            file_names=['scene.html', 'scene.js'])
    (tmp_path / 'output.json').write_bytes(canonical_bytes(result))
    attempt = str(uuid4())
    author_profile = protocol.AUTHOR_PROFILE
    profile = fixture.implementation(author_profile)
    environment = next(name for name, output in SOURCE_ENVIRONMENTS.items()
                       if output == profile.output_schema)
    envelope = SimpleNamespace(profile=author_profile, profile_revision='1', attempt_id=attempt,
        execution_binding=SimpleNamespace(tool_environment_name=environment),
        task=dict(input_sha256=digest(context), source_handoff=SOURCE_HANDOFF),
        checkpoint_ref=None, checkpoint_inputs=[], inputs=[
            InlineFile.from_bytes(protocol.INPUT_NAME, canonical_bytes(context)),
            InlineFile.from_bytes('reference.png', fixture.PNG)])
    stopped = StopEvidence(execution_id=attempt, all_writers_stopped=True, resource_ids=('fixture-writer',))
    for check in (lambda: check_workspace(tmp_path),
                  lambda: profile.collect_output(tmp_path, envelope, stopped)):
        with pytest.raises(ServiceError, match=SYNTAX_ERROR) as caught:
            check()
        diagnostic = _output_failure_diagnostic('collect_output', caught.value)
        assert diagnostic == dict(stage='collect_output', classification='invalid_javascript',
                                 file='scene_js', line=29, column=16)
        assert SYNTAX_ERROR in SAFE_OUTPUT_CODES
        assert '#include' not in json.dumps(diagnostic)

    legacy_envelope = deepcopy(envelope)
    legacy_envelope.execution_binding.tool_environment_name = environment.removesuffix('-checked-source')
    assert profile.collect_output(tmp_path, legacy_envelope, stopped)
    scene_path.write_bytes((line_prefix + _VALID_SHADER).encode())
    check_workspace(tmp_path)
    output = profile.collect_output(tmp_path, envelope, stopped)
    assert json.loads(output[0].content())['source']['files']['scene.js'] == line_prefix + _VALID_SHADER


def test_syntax_uses_the_html_script_goal_without_evaluating_code():
    validate_source_syntax({'files': {'scene.html': '''
        <script type="application/json">{ opaque inline data }</script>
        <textarea><script>return;</script></textarea>
        <template><script>return;</script></template>
        <script nomodule>return;</script>
        <script>var await; throw new Error("The checker never evaluates this")</script>
        <script type="module">import "./missing.js"; await missingFunction();</script>
    '''}})
    with pytest.raises(ServiceError, match=SYNTAX_ERROR) as caught:
        validate_source_syntax({'files': {'scene.html': '\n<script>return;</script>'}})
    assert caught.value.details['path'] == '/scene.html'
    assert caught.value.details['line'] == 2 and caught.value.details['column'] == 9


def test_source_extension_and_public_syntax_diagnostics_have_fixed_identities():
    assert verified_source_check_extension().name == 'learning_source_write_extension.mjs'
    for kind, code in (('output_validation_failed', 'invalid_javascript'),
                       ('output_validation_file', 'scene_js'),
                       ('output_validation_location', 'javascript_line')):
        payload = dict(event=kind, phase='collect_output', code=code)
        if kind.endswith('location'):
            payload['count'] = 29
        assert valid_worker_payload(payload)
        assert not valid_worker_payload(dict(payload, code='private-source-snippet'))

    observations = []
    for check in (dict(source_syntax='check_failed', code=SYNTAX_ERROR, path='/scene.js',
                       line=29, column=16, source='private-source-snippet'), dict(source_syntax='checked')):
        _record_learning_source_check(dict(type='tool_execution_end', toolName='write',
            isError=check['source_syntax'] != 'checked',
            result=dict(details=dict(learning_source_check=check))), observations)
    assert observations == [
        dict(source_syntax='check_failed', code=SYNTAX_ERROR, path='/scene.js', line=29, column=16),
        dict(source_syntax='checked'),
    ]


@pytest.mark.parametrize('catalog', [picture_catalog, reference_catalog])
def test_checked_catalog_rotates_author_tool_environment_only(catalog):
    _, old_hosts, _ = catalog.prepare()
    _, new_hosts, _ = catalog.prepare(checked_source=True)
    author = next(name for name in old_hosts if '-author.' in name)
    review = next(name for name in old_hosts if '-review.' in name)
    assert new_hosts[review] == old_hosts[review]
    _, old_policy, old_binding = candidate_config(old_hosts[author])
    _, new_policy, new_binding = candidate_config(new_hosts[author])
    assert new_policy.revision == old_policy.revision == '1'
    assert new_binding.bundle == old_binding.bundle
    assert new_binding.tool_environment.name == old_binding.tool_environment.name + '-checked-source'
    environment, = new_hosts[author]['execution_catalog']['tool_environments']
    assert new_binding.tool_environment.revision == checked_environment_revision(environment)
