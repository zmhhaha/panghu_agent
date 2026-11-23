"""Image-owned library facts for an explicitly requested WikiSkill snapshot.

These are compiler source records, never an injected published Skill. Reading
them neither publishes knowledge nor establishes an artwork's visual quality.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct

from agent_service.animation_wikiskill_contract import RECORD_MAX_BYTES
from agent_service.contracts import ServiceError, canonical_bytes, digest, safe_relative_path


LIBRARY_ID = 'ink-landscape'
SOURCE_SCHEMA = 'video.drawing-library-publication-source.v1'
SCOPE = 'image_owned_documentation_and_reading_not_artistic_validation'
FREE_COMPOSITION_ROOT = Path(__file__).parent / 'vendor/ink-landscape/experience'
FREE_COMPOSITION_MANIFEST_SHA256 = '4effc40f7f19a2ed8a7dc92e4824ac666b4ad6517e9edc5cf95d57bd335723b6'


def _require(value):
    if not value:
        raise ServiceError('ink_landscape_publication_source_invalid', 422)


def _source_records(library, files):
    _require(type(library) is dict and bool(library) and type(files) is dict and bool(files))
    library = deepcopy(library)
    fingerprint = digest(library)
    prefix = 'drawing-library:' + LIBRARY_ID + ':' + fingerprint
    records = []

    def add(identity, kind, content):
        row = dict(record_id=identity, kind=kind, content=content, sha256=digest(content))
        _require(len(canonical_bytes(row)) <= RECORD_MAX_BYTES)
        records.append(row)

    for path, text in files.items():
        try:
            safe_relative_path(path)
        except (TypeError, ValueError):
            raise ServiceError('ink_landscape_publication_source_invalid', 422) from None
        _require(type(path) is str and path.endswith('.md') and type(text) is str and bool(text))
    for path, text in sorted(files.items()):
        # The exact path stays in the record, while a bounded identity admits
        # every portable document path supported by the snapshot contract.
        file_id = hashlib.sha256(path.encode('utf-8')).hexdigest()
        add(prefix + ':file:' + file_id, 'drawing_library_documentation', dict(
            schema=SOURCE_SCHEMA, library_id=LIBRARY_ID, library_descriptor_sha256=fingerprint,
            path=path, text=text, text_sha256=hashlib.sha256(text.encode('utf-8')).hexdigest(),
            documentation_role=('technique_reading' if path.startswith('docs/learning/')
                else 'api_and_implementation' if path.startswith('docs/') else 'portable_skill'),
            evidence_scope=SCOPE, artistic_acceptance=False,
            static_visual_acceptance=False, continuous_motion_verified=False, audio_verified=False))
    add(prefix + ':manifest', 'drawing_library', dict(
        schema=SOURCE_SCHEMA, library_id=LIBRARY_ID, descriptor=library,
        descriptor_sha256=fingerprint,
        documentation=[dict(path=path, sha256=hashlib.sha256(text.encode('utf-8')).hexdigest())
                       for path, text in sorted(files.items())],
        runtime_requirements=dict(engines=['canvas'], drawing_libraries={LIBRARY_ID: library}),
        evidence_scope=SCOPE, artistic_acceptance=False,
        static_visual_acceptance=False, continuous_motion_verified=False, audio_verified=False,
        limitations=[
            'These exact image-owned documents and reading notes are publication evidence, not an already published Skill.',
            'A library name, documented technique or successful code build does not establish visual similarity, '
            'static artistic acceptance, continuous motion, sound quality or developer adoption.',
            'Compiled methods must preserve the exact library dependency and its verification limits; '
            'the consumer must separately admit that dependency before executing it.',
            'Keep generated knowledge self-contained. Do not make reference websites, mutable repository '
            'branches, private screenshots or local development paths into runtime dependencies.',
        ]))
    return sorted(records, key=lambda row: row['record_id'])


def capture_ink_landscape_records():
    """Read only the package shipped in this image, when publication is started."""
    from .ink_landscape_library import descriptor, read_publication_files
    return _source_records(descriptor(), read_publication_files())


def with_ink_landscape_records(capture):
    """Append trusted library facts without reading or modifying live releases."""
    def capture_library(owner):
        library = capture_ink_landscape_records()
        try:
            records = capture(owner)
        except ServiceError as error:
            if error.code != 'animation_wikiskill_library_empty':
                raise
            records = []
        _require(type(records) is list)
        return sorted(records + library, key=lambda row: row['record_id'])
    return capture_library


def capture_free_composition_records(directory=None):
    """Optional fixed local-case evidence; never a platform project or release.

    Source, PNG and original render receipt are shipped and hashed together.
    The compiler receives self-contained facts, not local runtime paths or a
    reference-image dependency. The PNG is verified but not injected as an
    input image, and static execution does not establish artistic acceptance.
    """
    from .ink_landscape_library import _read, descriptor
    root = Path(directory) if directory is not None else FREE_COMPOSITION_ROOT
    raw = _read(root, 'manifest.json', FREE_COMPOSITION_MANIFEST_SHA256, 16 * 1024)
    provenance = json.loads(raw)
    library = descriptor()
    _require(provenance.get('schema') == 'video.free-composition-publication-source.v1'
        and provenance.get('case_id') == 'rain-cleared-river-20261120'
        and provenance.get('source_revision') == library['source_revision']
        and provenance.get('library_sha256') == library['assets']['ink-landscape.mjs']
        and provenance.get('input_images') == provenance.get('network_inputs') == 0
        and all(provenance.get(key) is False for key in (
            'artistic_acceptance', 'continuous_motion_verified', 'audio_verified')))
    expected_files = {'scene.js', 'rain-cleared-river.png',
        'rain-cleared-river-receipt.json', 'rain-cleared-river.md'}
    _require(type(provenance.get('files')) is dict and set(provenance['files']) == expected_files)
    artifacts = {name: _read(root, name, sha, 8 * 1024 * 1024 if name.endswith('.png') else 128 * 1024)
                 for name, sha in provenance['files'].items()}
    receipt = json.loads(artifacts['rain-cleared-river-receipt.json'])
    picture = artifacts['rain-cleared-river.png']
    _require(picture[:8] == b'\x89PNG\r\n\x1a\n' and len(picture) >= 24)
    width, height = struct.unpack('>II', picture[16:24])
    _require((width, height) == (provenance['width'], provenance['height'])
        == (receipt['width'], receipt['height'])
        and receipt['source_revision'] == library['source_revision']
        and receipt['library_sha256'] == provenance['library_sha256']
        and receipt['source_sha256'] == provenance['files']['scene.js']
        and receipt['png_sha256'] == receipt['repeat_sha256'] == provenance['files']['rain-cleared-river.png']
        and receipt['bytes'] == len(picture)
        and receipt['image_inputs'] == receipt['network_inputs'] == 0
        and receipt['actual_library_api_calls'] == provenance['api_execution'])
    # Retain the exact receipt bytes above, while omitting machine-local paths
    # from the model-visible evidence and all future runtime requirements.
    observed = {key: deepcopy(receipt[key]) for key in (
        'source_sha256', 'source_revision', 'library_sha256', 'width', 'height',
        'bytes', 'png_sha256', 'repeat_sha256', 'renderer', 'image_inputs',
        'network_inputs', 'actual_library_api_calls', 'offscreen_surfaces',
        'render_seconds', 'scope')}
    common = dict(schema=provenance['schema'], case_id=provenance['case_id'],
        title=provenance['title'], provenance_manifest_sha256=FREE_COMPOSITION_MANIFEST_SHA256,
        artifact_sha256=deepcopy(provenance['files']),
        runtime_requirements=dict(engines=['canvas'], drawing_libraries={LIBRARY_ID: library}),
        evidence_scope=provenance['evidence_scope'], input_images=0, network_inputs=0,
        artistic_acceptance=False, static_visual_acceptance=False,
        continuous_motion_verified=False, audio_verified=False,
        limitations=['Independent local static case, not a platform-learning project or published knowledge.',
            'Exact artifact hashes and API counts do not establish artistic acceptance or general technique mastery.',
            'No continuous motion, absolute-clock adapter, five-minute sequence or sound has been verified.',
            'This source is fixed at b6c96a5; later structured-rock revisions are outside this case.'])
    prefix = 'free-composition:' + provenance['case_id'] + ':' + FREE_COMPOSITION_MANIFEST_SHA256
    rows = []
    for suffix, kind, document in (
            ('method', 'free_composition_documentation',
                dict(text=artifacts['rain-cleared-river.md'].decode('utf-8'))),
            ('source', 'free_composition_source',
                dict(source_text=artifacts['scene.js'].decode('utf-8'),
                    source_sha256=provenance['files']['scene.js'])),
            ('render', 'free_composition_render_evidence', dict(observation=observed))):
        content = dict(common, **document)
        row = dict(record_id=prefix + ':' + suffix, kind=kind, content=content, sha256=digest(content))
        _require(len(canonical_bytes(row)) <= RECORD_MAX_BYTES)
        rows.append(row)
    return sorted(rows, key=lambda row: row['record_id'])


def with_free_composition_records(capture, *, directory=None):
    """Explicit optional wiring; merely importing the module captures nothing."""
    def capture_case(owner):
        case = capture_free_composition_records(directory)
        try:
            records = capture(owner)
        except ServiceError as error:
            if error.code != 'animation_wikiskill_library_empty':
                raise
            records = []
        _require(type(records) is list)
        return sorted(records + case, key=lambda row: row['record_id'])
    return capture_case
