"""Small revision jobs and bounded recorded-evidence input, independent of v1 research.

The internal controller supplies fixed records. No input contains a filesystem
location, callback URL, model credential or runtime option selected by a client.
"""
from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import hashlib
import re
from typing import Mapping

from .pi_contracts import (ContractError, canonical_json, parse_timestamp,
                           validate_digest, validate_task_id)

MAX_REVISION_JOB_BYTES = 16 * 1024
MAX_REVISION_INPUT_BYTES = 16 * 1024 * 1024
REVISION_SCOPE = 'recorded_evidence_only'
REVISION_OUTPUT_FILE = 'revision-output.json'


def _require(value, code='invalid_revision_job', status=422):
    if not value:
        raise ContractError(code, 'Invalid or inconsistent fixed revision input', status)


def revision_input_digest(payload: Mapping) -> str:
    """Fingerprint immutable intent, evidence and pins; time is fixed separately."""
    keys = set(RevisionJob.__dataclass_fields__) - {
        'input_digest', 'task_id', 'created_at', 'execution_deadline_at'}
    _require(isinstance(payload, dict) and keys.issubset(payload))
    return hashlib.sha256(canonical_json({key: payload[key] for key in keys}).encode('utf-8')).hexdigest()


def revision_material_digest(value: dict) -> str:
    raw = canonical_json(value).encode('utf-8')
    _require(len(raw) <= MAX_REVISION_INPUT_BYTES, 'revision_input_too_large', 413)
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class RevisionJob:
    schema_version: str
    task_kind: str
    task_id: str
    input_digest: str
    topic: str
    user_id: str
    engine: str
    mode: str
    timeout_seconds: int
    created_at: str
    execution_deadline_at: str
    skill_bundle_revision: str
    runtime_revision: str
    intent_id: str
    parent_version_id: str
    root_report_id: str
    base_sha256: str
    section_id: str
    section_sha256: str
    instruction: str
    input_sha256: str
    material_policy: str

    @classmethod
    def from_dict(cls, payload: Mapping) -> 'RevisionJob':
        _require(isinstance(payload, dict) and set(payload) == set(cls.__dataclass_fields__))
        _require(len(canonical_json(payload).encode('utf-8')) <= MAX_REVISION_JOB_BYTES,
                 'revision_job_too_large', 413)
        _require(payload['schema_version'] == '1' and payload['task_kind'] == 'revision'
                 and payload['engine'] == 'pi' and payload['mode'] == 'review'
                 and payload['material_policy'] == REVISION_SCOPE)
        for key in ('task_id', 'intent_id', 'parent_version_id', 'root_report_id'):
            validate_task_id(payload[key])
        for key in ('input_digest', 'base_sha256', 'section_sha256', 'input_sha256'):
            validate_digest(payload[key], key)
        for key, maximum in (('topic', 500), ('user_id', 200)):
            text = payload[key]
            _require(isinstance(text, str) and 1 <= len(text) <= maximum
                     and text == text.strip() and not any(ord(c) < 32 or ord(c) == 127 for c in text))
        _require(isinstance(payload['section_id'], str) and
                 re.fullmatch('[A-Za-z0-9_-]{1,200}', payload['section_id']) is not None)
        instruction = payload['instruction']
        _require(isinstance(instruction, str) and instruction == instruction.strip()
                 and 1 <= len(instruction) <= 2000 and len(instruction.encode('utf-8')) <= 8000
                 and not any((ord(c) < 32 and c not in '\t\n\r') or ord(c) == 127 for c in instruction))
        for key in ('skill_bundle_revision', 'runtime_revision'):
            value = payload[key]
            _require(isinstance(value, str) and 1 <= len(value) <= 200
                     and not any(ord(c) < 32 or ord(c) == 127 for c in value))
        timeout = payload['timeout_seconds']
        _require(type(timeout) is int and 60 <= timeout <= 3600)
        created = parse_timestamp(payload['created_at'])
        deadline = parse_timestamp(payload['execution_deadline_at'])
        _require(0 < (deadline - created).total_seconds() <= timeout, 'invalid_deadline')
        _require(payload['input_digest'] == revision_input_digest(payload), 'input_digest_mismatch')
        return cls(**payload)

    def to_dict(self):
        return asdict(self)


def validate_revision_materials(job, value: dict) -> dict:
    """Decode and validate fixed evidence, never following full_text_path.

    Returns internal artifact bytes for the composer. The wire object itself
    remains suitable for persistent fingerprinting and bounded queue storage.
    """
    checked = job if isinstance(job, RevisionJob) else RevisionJob.from_dict(job)
    _require(isinstance(value, dict) and set(value) == {'report', 'items', 'artifacts'},
             'invalid_revision_input')
    _require(revision_material_digest(value) == checked.input_sha256, 'revision_input_mismatch', 409)
    _require(isinstance(value['report'], str) and isinstance(value['items'], list)
             and isinstance(value['artifacts'], dict)
             and set(value['artifacts']) == {'papers', 'evidence', 'claims', 'references'},
             'invalid_revision_input')
    artifacts = {}
    for name, encoded in value['artifacts'].items():
        _require(isinstance(encoded, str), 'invalid_revision_input')
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise ContractError('invalid_revision_input', 'Invalid fixed evidence encoding') from None
        _require(base64.b64encode(raw).decode('ascii') == encoded, 'invalid_revision_input')
        artifacts[name] = raw
    from .pi_revision_validation import validate_revision_input
    try:
        fixed = validate_revision_input(value['report'], value['items'], artifacts,
                                        checked.section_id, checked.base_sha256)
        _require(fixed['section']['sha256'] == checked.section_sha256, 'revision_section_mismatch', 409)
    except ContractError:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise ContractError(getattr(exc, 'code', 'invalid_revision_input'),
                            'Fixed evidence or selected section is invalid', 422) from None
    return {'report': value['report'], 'items': value['items'], 'artifacts': artifacts}


def revision_contract_schema():
    properties = {key: {'type': 'string'} for key in RevisionJob.__dataclass_fields__}
    properties.update(schema_version={'const': '1'}, task_kind={'const': 'revision'},
                      engine={'const': 'pi'}, mode={'const': 'review'},
                      material_policy={'const': REVISION_SCOPE},
                      timeout_seconds={'type': 'integer', 'minimum': 60, 'maximum': 3600})
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', 'title': 'RevisionJob v1',
            'type': 'object', 'additionalProperties': False,
            'required': list(RevisionJob.__dataclass_fields__), 'properties': properties}
