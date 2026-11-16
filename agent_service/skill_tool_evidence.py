"""Host observations of the fixed Skill tools, never Agent-written receipts.

The runtime feeds native start/end events directly to this object. Tool bodies
stay out of public progress. Collection happens after writer stop and freezes
this controller-owned evidence into the signed artifact handoff.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from uuid import UUID

from .animation_skills_contract import (
    PACKAGE_SCHEMA, MANIFEST_SCHEMA, PREFIX, MAX_FILE_BYTES, MAX_CATALOG_BYTES,
    MAX_PACKAGE_FILES, SkillCatalogTools, bundle_files, require,
    validate_skill_catalog, validate_skill_package,
)
from .contracts import canonical_bytes, digest, safe_relative_path
from .profiles.video import _read, decode_video_json


SCHEMA = 'video.animation-skill-tool-evidence.v1'
MAX_CALLS = 256
TOOLS = frozenset({'skill_search', 'skill_read'})


def load_skill_bundle(input_root):
    """Reconstruct the exact protected inputs, checking bytes and membership."""
    input_root = Path(input_root)
    raw = _read(input_root, PREFIX + '/catalog.json', MAX_CATALOG_BYTES)
    snapshot = validate_skill_catalog(decode_video_json(raw))
    require(raw == canonical_bytes(snapshot), 'animation_skill_bundle_mismatch')
    packages = {}
    for row in snapshot['skills']:
        package_sha = row['package_sha256']
        prefix = PREFIX + '/packages/' + package_sha + '/'
        manifest_raw = _read(input_root, prefix + 'manifest.json', MAX_FILE_BYTES)
        manifest = decode_video_json(manifest_raw)
        require(type(manifest) is dict and manifest_raw == canonical_bytes(manifest)
            and manifest.get('schema') == MANIFEST_SCHEMA
            and manifest.get('package_schema') == PACKAGE_SCHEMA
            and manifest.get('sha256') == package_sha
            and type(manifest.get('files_sha256')) is dict
            and 1 <= len(manifest['files_sha256']) <= MAX_PACKAGE_FILES,
            'animation_skill_bundle_mismatch')
        files = {}
        for name in manifest['files_sha256']:
            safe_relative_path(name)
            require(name == 'SKILL.md' or name.startswith(('references/', 'assets/', 'scripts/')),
                    'animation_skill_path_invalid')
            files[name] = _read(input_root, prefix + name, MAX_FILE_BYTES).decode('utf-8')
        package = {key: deepcopy(value) for key, value in manifest.items()
                   if key not in {'package_schema', 'files_bytes'}}
        package.update(schema=PACKAGE_SCHEMA, files=files)
        packages[package_sha] = validate_skill_package(package)
    expected = bundle_files(snapshot, packages)
    expected_paths = {item.name for item in expected}
    actual_paths = {path.relative_to(input_root).as_posix()
                    for path in (input_root / PREFIX).rglob('*') if path.is_file()}
    require(expected_paths == actual_paths, 'animation_skill_bundle_mismatch')
    for item in expected:
        require(_read(input_root, item.name, max(MAX_FILE_BYTES, MAX_CATALOG_BYTES)) == item.content(),
                'animation_skill_bundle_mismatch')
    return snapshot, packages


class SkillToolObserver:
    """Trusted runtime-local collector. There is no file/import-receipt API."""

    def __init__(self, snapshot, packages, *, input_sha256):
        require(type(input_sha256) is str and len(input_sha256) == 64
            and all(c in '0123456789abcdef' for c in input_sha256))
        self.tools = SkillCatalogTools(snapshot, packages)
        self.input_sha256 = input_sha256
        self._pending, self._seen, self._events, self._failed = {}, set(), [], []
        self._invalid = False

    def observe(self, event):
        require(not self._invalid, 'animation_skill_event_invalid')
        try:
            self._observe(event)
        except Exception:
            # A caller must never turn a rejected terminal event into a clean
            # no-read handoff by catching our error after pending was removed.
            self._invalid = True
            raise

    def _observe(self, event):
        require(type(event) is dict, 'animation_skill_event_invalid')
        if event.get('type') not in {'tool_execution_start', 'tool_execution_end'}:
            return
        name, call_id = event.get('toolName'), event.get('toolCallId')
        if name not in TOOLS:
            # A terminal event may not relabel a relevant outstanding call.
            require(call_id not in self._pending, 'animation_skill_event_invalid')
            return
        require(type(call_id) is str and 1 <= len(call_id) <= 300
            and not any(ord(c) < 32 or ord(c) == 127 for c in call_id),
            'animation_skill_event_invalid')
        if event['type'] == 'tool_execution_start':
            params = event.get('args')
            require(type(params) is dict and len(canonical_bytes(params)) <= 8192
                and call_id not in self._seen and len(self._seen) < MAX_CALLS,
                'animation_skill_event_invalid')
            self._seen.add(call_id)
            self._pending[call_id] = (name, deepcopy(params))
            return
        require(call_id in self._pending and self._pending[call_id][0] == name,
                'animation_skill_event_invalid')
        _, params = self._pending.pop(call_id)
        require(event.get('isError') is None or type(event['isError']) is bool,
                'animation_skill_event_invalid')
        if event.get('isError') is True:
            self._failed.append(dict(tool_call_id=call_id, tool_name=name))
            return
        expected = self.tools.validate_tool_result(name, params, event.get('result'))
        self._events.append(dict(seq=len(self._events) + 1, tool_call_id=call_id,
            tool_name=name, parameters=params, receipt=deepcopy(expected['details']['receipt'])))

    def finish(self, attempt_id):
        require(not self._invalid, 'animation_skill_event_invalid')
        require(type(attempt_id) is str and str(UUID(attempt_id)) == attempt_id
            and not self._pending, 'animation_skill_event_incomplete')
        snapshot = self.tools.snapshot
        result = dict(schema=SCHEMA, source='verified_host_native_tool_events',
            attempt_id=attempt_id, input_sha256=self.input_sha256,
            snapshot_sha256=snapshot['sha256'], task_sha256=snapshot['task_sha256'],
            recipient_owner=snapshot['recipient_owner'], consumer_id=snapshot['consumer_id'],
            events=deepcopy(self._events), failed_calls=deepcopy(self._failed))
        result['sha256'] = digest(result)
        return result


def validate_tool_evidence(evidence, snapshot, packages, *, attempt_id, input_sha256):
    """Validate a host-owned handoff; this alone does not prove its provenance.

    Worker collection calls this with the in-memory observer result. Consumers
    must obtain it through the authenticated signed Agent artifact handoff.
    """
    tools = SkillCatalogTools(snapshot, packages)
    require(type(evidence) is dict and set(evidence) == {'schema', 'source',
        'attempt_id', 'input_sha256', 'snapshot_sha256', 'task_sha256',
        'recipient_owner', 'consumer_id', 'events', 'failed_calls', 'sha256'}
        and evidence.get('schema') == SCHEMA
        and evidence.get('source') == 'verified_host_native_tool_events'
        and evidence['attempt_id'] == attempt_id and evidence['input_sha256'] == input_sha256
        and evidence['snapshot_sha256'] == tools.snapshot['sha256']
        and all(evidence[key] == tools.snapshot[key]
                for key in ('task_sha256', 'recipient_owner', 'consumer_id')),
        'animation_skill_evidence_binding_mismatch')
    require(type(evidence['events']) is list and type(evidence['failed_calls']) is list
        and len(evidence['events']) + len(evidence['failed_calls']) <= MAX_CALLS,
        'animation_skill_event_invalid')
    seen = set()
    for index, event in enumerate(evidence['events'], 1):
        require(type(event) is dict and set(event) == {'seq', 'tool_call_id', 'tool_name',
            'parameters', 'receipt'} and event['seq'] == index and event['tool_name'] in TOOLS
            and type(event['tool_call_id']) is str and event['tool_call_id'] not in seen,
            'animation_skill_event_invalid')
        seen.add(event['tool_call_id'])
        result = tools.tool_result(event['tool_name'], event['parameters'])
        require(event['receipt'] == result['details']['receipt'], 'animation_skill_event_invalid')
    for event in evidence['failed_calls']:
        require(type(event) is dict and set(event) == {'tool_call_id', 'tool_name'}
            and type(event['tool_call_id']) is str and event['tool_call_id'] not in seen
            and event['tool_name'] in TOOLS, 'animation_skill_event_invalid')
        seen.add(event['tool_call_id'])
    require(evidence['sha256'] == digest({k: v for k, v in evidence.items() if k != 'sha256'}),
            'animation_skill_evidence_changed')
    return deepcopy(evidence)
