"""Synthetic reviewed catalog fixtures; these hashes grant no live readiness."""
from __future__ import annotations

import hashlib


def make_catalog(engine='pi', enabled=False, *, name='workspace-task.v1', revision='v1',
                 image_digest=None, runtime_revision='runtime-v1'):
    image_digest = image_digest or 'sha256:' + 'a' * 64
    contract = {'name': name, 'revision': revision, 'task_schema': 'workspace-text.v1',
                'output_schema': 'text-output.v1', 'required_tools': ['read', 'write'],
                'required_model_capabilities': ['text', 'tool_calls'], 'max_timeout_seconds': 600}
    bundle = {'name': 'workspace-instructions', 'revision': 'bundle-v1',
              'resources': [{'name': 'instructions.txt',
                             'sha256': hashlib.sha256(b'Synthetic trusted instructions.\n').hexdigest()}],
              'source': 'local-reviewed', 'license': 'MIT', 'required_tools': ['read', 'write']}
    environment = {'name': 'workspace-tools', 'revision': 'tools-v1', 'tools': ['read', 'write'],
                   'capabilities': ['text-output.v1'], 'image_digest': image_digest,
                   'runtime_revision': runtime_revision}
    binding = {'id': 'workspace-' + engine, 'revision': 'binding-v1',
               'profile': name, 'profile_revision': revision,
               'bundle': {'name': bundle['name'], 'revision': bundle['revision']},
               'tool_environment': {'name': environment['name'], 'revision': environment['revision']},
               'engine': engine, 'adapter_revision': engine + '-adapter-v1',
               'model_policy': {'revision': 'model-v1', 'provider': 'task-proxy', 'model': 'fixture-model',
                                'api_mode': 'openai-responses',
                                'capabilities': ['text', 'tool_calls']}}
    if enabled:
        from agent_service.registry import binding_fingerprint
        binding['acceptance'] = {'combination_sha256': binding_fingerprint(contract, bundle, environment, binding),
                                 'suite': 'workspace-contract.v1',
                                 'report_sha256': hashlib.sha256(b'Synthetic contract fixture approval').hexdigest()}
    route = {'profile': name, 'profile_revision': revision, 'binding_id': binding['id'],
             'binding_revision': binding['revision'], 'enabled': enabled}
    return {'schema_version': 2, 'task_contracts': [contract], 'capability_bundles': [bundle],
            'tool_environments': [environment], 'execution_bindings': [binding], 'routes': [route]}
