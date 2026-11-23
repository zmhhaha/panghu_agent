"""Opt-in private application data for an unpublished model-draft source.

No file is a Skill, a successful check, or a runtime dependency. Only a new
manual publication copies this exact text into its ordinary frozen snapshot.
"""
import hashlib
import json
from pathlib import Path
import re

from agent_service.animation_wikiskill_contract import RECORD_MAX_BYTES, identity, validate_input
from agent_service.contracts import ServiceError, canonical_bytes, digest
from .animation_wikiskill_gateway import stopped_job
from .agent import _uuid

SCHEMA = 'video.private-failed-compilation-draft-source.v1'
FILES = {'draft.json', 'provenance.json', 'original-input.json', 'failed-publication.json', 'model-write-call.json'}


def _require(value):
    if not value:
        raise ServiceError('wikiskill_recovery_source_invalid', 422)


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result)
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ServiceError('wikiskill_recovery_source_invalid', 422) from None


def _path(path, root):
    path, root = Path(path).absolute(), Path(root).absolute()
    _require(root in path.parents and path.resolve() == path and root.resolve() == root
        and not any(p.is_symlink()
        or getattr(p, 'is_junction', lambda: False)() for p in (path, *path.parents)))
    return path


def _read(path, root, expected, maximum=4 * 1024 * 1024):
    path = _path(path, root)
    _require(path.is_file() and path.stat().st_nlink == 1
        and 0 < path.stat().st_size <= maximum)
    raw = path.read_bytes()
    _require(hashlib.sha256(raw).hexdigest() == expected)
    return raw


def with_failed_compilation_draft(capture, *, trusted_root, manifest_file, manifest_sha256, owner):
    """Freeze owner/path/hash in private process configuration, without reads."""
    identity(owner)
    _require(type(manifest_sha256) is str and re.fullmatch('[0-9a-f]{64}', manifest_sha256))
    root = Path(trusted_root).absolute() / 'knowledge-recovery'
    manifest_file = _path(manifest_file, root)

    def capture_with_draft(request_owner):
        records = capture(request_owner)
        if request_owner != owner:
            return records
        manifest = _json(_read(manifest_file, root, manifest_sha256, 64 * 1024))
        _require(type(manifest) is dict and manifest.get('schema') == SCHEMA and manifest.get('owner') == owner
            and manifest.get('scope') == 'unpublished_invalid_model_draft_only'
            and type(manifest.get('files')) is dict
            and FILES <= set(manifest['files']) <= FILES | {'diagnostics.json'})
        artifacts = {}
        for name, ref in manifest['files'].items():
            _require(type(ref) is dict and set(ref) == {'sha256', 'bytes'})
            raw = _read(manifest_file.parent / name, root, ref['sha256'])
            _require(type(ref['bytes']) is int and len(raw) == ref['bytes'])
            artifacts[name] = raw
        provenance = _json(artifacts['provenance.json'])
        publication = _json(artifacts['failed-publication.json'])
        call = _json(artifacts['model-write-call.json'])
        original = validate_input(_json(artifacts['original-input.json']))
        draft = _json(artifacts['draft.json'])  # Inspect only; never normalize or coerce.
        _require(all(type(value) is dict for value in (provenance, publication, draft, call)))
        draft_text = artifacts['draft.json'].decode('utf-8')
        remote = publication.get('remote_receipt', {})
        _require(type(remote) is dict)
        _require(publication.get('owner') == provenance.get('owner') == original['snapshot']['owner'] == owner
            and publication.get('state') == 'failed' and publication.get('release_id') is None
            and publication.get('remote_state') in {'failed', 'timed_out', 'cancelled'}
            and publication.get('remote_stop_proven') is True
            and remote.get('state') == publication['remote_state']
            and stopped_job(remote, profile='video.animation-wikiskill-compile.v4')
            and remote.get('publication_authorized') is False
            and publication.get('job_id') == remote.get('job_id') == provenance.get('agent_job_id')
            and publication.get('id') == provenance.get('publication_id')
            and publication.get('request_id') == provenance.get('request_id')
            and remote.get('business_ref') == 'animation-wikiskill:' + publication['id']
            and remote['attempt']['attempt_id'] == provenance.get('attempt_id')
            and remote['attempt']['fence'] == provenance.get('fence'))
        original_sha = digest(original)
        _require(original_sha == provenance.get('input_sha256') == publication.get('input_sha256')
            == draft.get('input_sha256')
            and original['snapshot']['id'] == provenance.get('snapshot_id') == publication.get('snapshot_id')
            and original['snapshot']['sha256'] == provenance.get('snapshot_sha256')
            == publication.get('snapshot_sha256') == draft.get('snapshot_sha256'))
        ref, failed, scope, write = (provenance.get(key, {}) for key in
            ('original_draft', 'failed_job', 'scope', 'model_write'))
        input_ref = provenance.get('original_input_manifest')
        arguments = call.get('arguments')
        _require(all(type(value) is dict for value in (ref, failed, scope, write, input_ref, arguments)))
        _require(provenance.get('schema') == 'video.wikiskill-private-failed-draft-provenance.v1'
            and ref.get('sha256') == manifest['files']['draft.json']['sha256']
            and ref.get('bytes') == len(artifacts['draft.json']) and ref.get('modified') is False
            and input_ref.get('record_count') == len(original['records'])
            and failed.get('state') == remote['state'] and failed.get('stop_proven') is True
            and failed.get('deadline_at') == remote.get('deadline_at')
            and failed.get('stop_observed_at') == remote['stop_proof']['observed_at']
            and _uuid(failed.get('outer_pod_uid'))
            and any(value.endswith(':' + failed.get('outer_pod_uid', ''))
                for value in remote['stop_proof']['resource_ids'])
            and all(scope.get(key) is True for key in ('unpublished', 'structurally_invalid',
                'first_and_only_completed_model_write_proposal', 'no_new_visual_audio_or_artistic_acceptance'))
            and scope.get('original_check_returns_retained') is False
            and scope.get('partial_rewrite_available') is False
            and write.get('http_status') == 200
            and all(type(write.get(key)) is str and re.fullmatch('[0-9a-f]{64}', write[key])
                for key in ('model_request_sha256', 'raw_model_response_sha256'))
            and type(write.get('function_call_id')) is str and bool(write['function_call_id']))
        _require(call.get('schema') == 'video.preserved-model-write-call.v1'
            and call.get('name') == 'write' and set(arguments) == {'path', 'content'}
            and arguments.get('path') == 'output.json'
            and type(arguments.get('content')) is str
            and arguments['content'].encode('utf-8') == artifacts['draft.json']
            and all(call.get(key) == write.get(key) for key in ('model_request_sha256',
                'model_response_id', 'function_call_id', 'raw_model_response_sha256')))
        diagnostics = None
        if 'diagnostics.json' in artifacts:
            observed = _json(artifacts['diagnostics.json'])
            _require(type(observed) is dict and observed.get('original_output_sha256') == ref['sha256']
                and observed.get('original_output_modified') is False
                and observed.get('projection_saved_or_supplied_as_source') is False
                and type(observed.get('findings')) is list)
            diagnostics = dict(scope='operator_schema_findings_not_native_check_or_repaired_output',
                findings=observed['findings'])
        _require(type(records) is list and len({row['record_id'] for row in records}) == len(records))
        records = [dict(row) for row in records]
        for row in records:
            if 'sha256' not in row:
                row['sha256'] = digest(row['content'])
        ordered = sorted(records, key=lambda row: row['record_id'])
        current = {row['record_id']: (index, row) for index, row in enumerate(ordered)}
        mapping = []
        for index, prior in enumerate(original['records']):
            position, row = current.get(prior['record_id'], (None, None))
            unchanged = row is not None and row['sha256'] == prior['sha256'] and row['kind'] == prior['kind']
            if unchanged:
                unchanged = hashlib.sha256(canonical_bytes(row)).hexdigest() == prior['file_sha256']
            mapping.append(dict(record_id=prior['record_id'], original_index=index,
                original_sha256=prior['sha256'], current_index=position,
                current_sha256=row['sha256'] if row else None, unchanged=unchanged))
        protected = manifest.get('protected_record_ids')
        _require(type(protected) is list and bool(protected)
            and all(type(value) is str for value in protected) and len(set(protected)) == len(protected))
        indexed = {row['record_id']: row for row in mapping}
        _require(all(record_id in indexed and indexed[record_id]['unchanged'] for record_id in protected))
        record_id = 'zz-unpublished-compilation-draft:' + publication['id'] + ':' + ref['sha256']
        _require(all(row['record_id'] < record_id for row in ordered))
        content = dict(schema=SCHEMA, status='unpublished_invalid_model_draft_only',
            owner=owner, provenance_manifest_sha256=manifest_sha256,
            original_draft_text=draft_text, original_draft_sha256=ref['sha256'], original_draft_bytes=ref['bytes'],
            sanitized_model_write_call_sha256=manifest['files']['model-write-call.json']['sha256'],
            operator_schema_diagnostics=diagnostics,
            original_input_sha256=original_sha, original_snapshot_sha256=original['snapshot']['sha256'],
            origin=dict(publication_id=publication['id'], agent_job_id=remote['job_id'],
                attempt=remote['attempt'], remote_state=remote['state'], stop_proof=remote['stop_proof'], model_write=write),
            original_index_to_current=mapping, protected_record_ids=protected,
            all_original_addresses_unchanged=all(row['unchanged'] and row['original_index'] == row['current_index']
                for row in mapping), current_record_count_before_draft=len(ordered), draft_index=len(ordered),
            published=False, structurally_valid=False, native_write_execution_proven=False, check_passed=False,
            human_approval=False, artistic_acceptance=False, continuous_motion_verified=False, audio_verified=False,
            limitations=[
                'Original retained model write-call text only. It is invalid and unpublished; it is not ground truth or a Skill.',
                'The original worker write/check returns were not retained. No successful check, completed reading, mastery or acceptance is claimed.',
                'Re-read relevant original material and decide independently. Use current manifest addresses; changed or missing sources are explicitly mapped.',
                'The new Agent must bind the current input_sha256, author any corrections itself, write/check its complete output and stop normally.',
                'The host has not rewritten the draft, coerced package versions, supplied a repaired package, or authorized publication.'])
        row = dict(record_id=record_id, kind='unpublished_failed_compilation_model_draft',
            content=content, sha256=digest(content))
        _require(len(canonical_bytes(row)) <= RECORD_MAX_BYTES)
        return ordered + [row]
    return capture_with_draft
