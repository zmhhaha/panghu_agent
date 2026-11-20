"""Image-owned library facts for an explicitly requested WikiSkill snapshot.

These are compiler source records, never an injected published Skill. Reading
them neither publishes knowledge nor establishes an artwork's visual quality.
"""
from copy import deepcopy
import hashlib

from agent_service.animation_wikiskill_contract import RECORD_MAX_BYTES
from agent_service.contracts import ServiceError, canonical_bytes, digest, safe_relative_path


LIBRARY_ID = 'ink-landscape'
SOURCE_SCHEMA = 'video.drawing-library-publication-source.v1'
SCOPE = 'image_owned_api_documentation_not_artistic_validation'


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
            'These exact image-owned API documents are publication evidence, not an already published Skill.',
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
    from .ink_landscape_library import descriptor, read_skill_files
    return _source_records(descriptor(), read_skill_files())


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
