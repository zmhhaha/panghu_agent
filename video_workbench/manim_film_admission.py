"""Separate stopped-producer and whole-film-consumer technical admission.

The private executor reopens exact evidence on startup. A business consumer
also pins that admission to its current complete-film release and installed
helper bytes. This authorizes a resource path, never a Skill publication.
"""
import hashlib
import json
from pathlib import Path
import re

from agent_service.contracts import canonical_bytes, digest
from .film_authoring_resources import expected_scope
from .film_render_adapter import validate_film_render_result
from .hyperframes_film_snapshot import decode_film_execution_snapshot
from .manim_method_resources import helper_assets, method_resource, PRODUCER_REQUIREMENT
from .manim_resource import require

SCHEMA = 'video.manim-film-admission.v1'
CONTEXT_SCHEMA = 'video.manim-film-consumer-context.v1'
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_IMAGE = re.compile(r'[A-Za-z0-9./:_-]+:latest@sha256:[0-9a-f]{64}\Z')


def helpers():
    return {name: hashlib.sha256(raw).hexdigest() for name, raw in helper_assets().items()}


def build_admission(*, resource_service, owner, operation_id, snapshot, result, media, capture):
    view = resource_service.get(owner, operation_id)
    require(view['status'] == 'completed' and view['stop_proven'] is True
        and not view['cancel_requested'], 'film_manim_generation_not_proven', 409)
    envelope = resource_service.envelope(owner, operation_id)
    image = view['resource_image']
    descriptor, assets = method_resource(envelope, expected_sha256=envelope['sha256'],
        expected_scope=expected_scope(owner, operation_id), image_digest=image.rsplit('@', 1)[1])
    require(view['descriptor'] == descriptor, 'film_manim_descriptor_changed', 409)
    release = {key: snapshot[key] for key in ('image', 'runtime_fingerprint', 'trusted_host_sha256')}
    files = decode_film_execution_snapshot(snapshot, expected_snapshot_sha256=snapshot['sha256'], **release)
    checked = validate_film_render_result(result, snapshot, runtime_fingerprint=release['runtime_fingerprint'])
    require(type(media) is bytes and len(media) == checked['bytes']
        and hashlib.sha256(media).hexdigest() == checked['sha256'], 'film_manim_media_changed', 409)
    require(view['film_sha256'] == snapshot['film_sha256'], 'film_manim_consumer_film_mismatch', 409)
    required = {descriptor['manifest_path'], 'assets/manim_frame_layer.mjs',
        *(row['path'] for row in descriptor['manifest']['frames'])}
    for name in required:
        require(files.get('project/' + name) == assets[name.removeprefix('assets/')],
            'film_manim_consumer_assets_missing', 409)
    manifest = json.loads(files['project/manifest.json'])
    consumers = [module for module in manifest['modules'] if required <= set(module['assets'])]
    require(consumers, 'film_manim_consumer_declaration_missing', 409)
    require(type(capture) is dict and digest(capture) == result['film_export']['capture_sha256']
        and capture.get('bundle_sha256') == snapshot['bundle_sha256']
        and capture.get('film_sha256') == snapshot['film_sha256'], 'film_manim_capture_binding_mismatch', 409)
    loaded = capture.get('probe', {}).get('loaded_resources')
    require(type(loaded) is list and all('/project/' + name in loaded for name in required),
        'film_manim_consumer_assets_not_loaded', 409)
    record = dict(schema=SCHEMA, owner=owner, operation_id=operation_id,
        producer=PRODUCER_REQUIREMENT, resource_image=image, helpers=helpers(), formal_release=release,
        evidence=dict(resource_request_sha256=view['request_sha256'], envelope_sha256=envelope['sha256'],
            descriptor_sha256=descriptor['sha256'], snapshot_sha256=snapshot['sha256'],
            result_sha256=digest(result), media_sha256=checked['sha256'], capture_sha256=digest(capture)),
        scope='stopped_resource_and_encoded_whole_film_not_visual_acceptance_or_skill_publication')
    record['sha256'] = digest(record)
    return record


def _read(path, maximum):
    path = Path(path).absolute()
    require(path.is_file() and not any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)()
        for p in (path, *path.parents)) and path.stat().st_nlink == 1
        and 0 < path.stat().st_size <= maximum, 'film_manim_admission_file_invalid', 503)
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    require(len(raw) <= maximum, 'film_manim_admission_file_invalid', 503)
    return raw


def read_admission(path):
    try:
        value = json.loads(_read(path, 64 * 1024))
    except (ValueError, UnicodeError, RecursionError):
        require(False, 'film_manim_admission_file_invalid', 503)
    require(type(value) is dict and set(value) == {'schema', 'owner', 'operation_id', 'producer',
        'resource_image', 'helpers', 'formal_release', 'evidence', 'scope', 'sha256'}
        and value['schema'] == SCHEMA and type(value['sha256']) is str and _SHA.fullmatch(value['sha256'])
        and digest({key: item for key, item in value.items() if key != 'sha256'}) == value['sha256'],
        'film_manim_admission_binding_invalid', 503)
    return value


def validate_admission(path, *, resource_service):
    value = read_admission(path)
    from .animation_creation_service import _uuid
    require(_uuid(value['operation_id']), 'film_manim_admission_binding_invalid', 503)
    root = resource_service.root / 'admissions' / value['operation_id']
    snapshot = json.loads(_read(root / 'snapshot.json', 96 * 1024 * 1024))
    result = json.loads(_read(root / 'result.json', 512 * 1024))
    media = _read(root / 'video.mp4', 128 * 1024 * 1024)
    capture = json.loads(_read(root / 'capture.json', 32 * 1024 * 1024))
    actual = build_admission(resource_service=resource_service, owner=value['owner'],
        operation_id=value['operation_id'], snapshot=snapshot, result=result, media=media, capture=capture)
    require(actual == value, 'film_manim_admission_evidence_changed', 503)
    return actual


def consumer_context(value):
    return dict(schema=CONTEXT_SCHEMA, producer=value['producer'], resource_image=value['resource_image'],
        helpers=value['helpers'], formal_release=value['formal_release'], admission_sha256=value['sha256'])


def validate_consumer_context(value, *, runtime, hosts, image):
    require(type(value) is dict and set(value) == {'schema', 'producer', 'resource_image', 'helpers',
        'formal_release', 'admission_sha256'} and value['schema'] == CONTEXT_SCHEMA
        and value['producer'] == PRODUCER_REQUIREMENT and type(value['resource_image']) is str
        and _IMAGE.fullmatch(value['resource_image']) and value['helpers'] == helpers()
        and type(value['admission_sha256']) is str and _SHA.fullmatch(value['admission_sha256'])
        and value['formal_release'] == dict(image=image, runtime_fingerprint=runtime, trusted_host_sha256=hosts),
        'film_manim_consumer_release_mismatch', 503)
    return json.loads(canonical_bytes(value))


def validate_producer_capabilities(value, context):
    require(type(value) is dict and value.get('schema') == 'video.manim-film-resource-capabilities.v1'
        and value.get('enabled') is True and value.get('producer') == context['producer']
        and value.get('resource_image') == context['resource_image'] and value.get('helpers') == context['helpers']
        and value.get('admission_sha256') == context['admission_sha256'], 'film_manim_producer_not_admitted', 503)
    return True
