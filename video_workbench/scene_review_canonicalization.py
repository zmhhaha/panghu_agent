"""Select supplied observations without changing retained capture bytes.

This is opt-in for an explicitly versioned new review. The caller first verifies every
retained render against the one frozen capability. Equal bytes at different
absolute frames remain distinct observations. A same-clock clip capture may
refer to an identical static capture, but is never represented as separately
supplied or seen by the model.
"""
from copy import deepcopy
import hashlib
import re

from agent_service.contracts import ServiceError, digest
from .scene_space_plan import FROZEN_SCHEMA, compile_evaluator

CANONICALIZATION_REVISION = 'same-clock-original-png-v1'
SCHEMA = 'video.scene-review-clock-canonicalization.v1'
_SHA = re.compile('[a-f0-9]{64}\\Z')
_PROVENANCE_FIELDS = {'frame_id', 'absolute_frame', 'kind', 'render_id', 'source_sha256',
    'plan_sha256', 'capture', 'retained_sha256', 'model_input_sha256', 'model_input_bytes', 'conversion'}


def _require(ok):
    if not ok:
        raise ServiceError('scene_review_canonicalization_identity_unproven', 409)


def _sha(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


def canonicalize_clock_observations(frozen, source_sha256, runtime_identity, frames, files, provenance):
    """Return supplied frames/files and a complete original-capture mapping.

    ``runtime_identity`` is the already verified, frozen render capability;
    rendering job IDs and relative capture clocks are retained as provenance,
    rather than mistaken for differences in the logical scene at one clock.
    No input is mutated, no bytes are converted, and no filesystem is touched.
    """
    _require(type(frozen) is dict and frozen.get('schema') == FROZEN_SCHEMA and _sha(source_sha256))
    evaluator = compile_evaluator(frozen)  # Validates the entire plan and frozen hash.
    _require(type(runtime_identity) is dict and runtime_identity.get('admitted') is True
        and type(runtime_identity.get('engines')) is list and 'canvas' in runtime_identity['engines']
        and _sha(runtime_identity.get('runtime_sha256')) and _sha(runtime_identity.get('host_sha256')))
    _require(type(frames) is list and 1 <= len(frames) <= 8 and type(files) is dict
        and type(provenance) is list and len(frames) == len(provenance))
    _require(all(type(frame) is dict and set(frame) == {'file_name', 'sha256', 'purpose', 'frame_id'}
        and type(frame['frame_id']) is str and frame['frame_id']
        and type(frame['file_name']) is str and frame['file_name']
        and frame['purpose'] == 'output' and _sha(frame['sha256']) for frame in frames))
    _require(len({frame['frame_id'] for frame in frames}) == len(frames)
        and len({frame['file_name'] for frame in frames}) == len(frames)
        and set(files) == {frame['file_name'] for frame in frames})
    _require(all(type(item) is dict and set(item) == _PROVENANCE_FIELDS for item in provenance))
    by_id = {item['frame_id']: item for item in provenance}
    _require(len(by_id) == len(provenance) and set(by_id) == {frame['frame_id'] for frame in frames})
    identities = {}
    runtime_sha = digest(runtime_identity)
    for frame in frames:
        identity = frame['frame_id']; item = by_id[identity]; raw = files[frame['file_name']]
        capture = item['capture']
        _require(type(item['absolute_frame']) is int and item['absolute_frame'] >= 0
            and item['kind'] in {'static_preview', 'clip_keyframe'}
            and type(item['render_id']) is str and bool(item['render_id'])
            and item['source_sha256'] == source_sha256 and item['plan_sha256'] == frozen['plan_sha256']
            and item['conversion'] == 'identity_original_png'
            and type(raw) is bytes and raw.startswith(b'\x89PNG\r\n\x1a\n')
            and 0 < len(raw) <= 2*1024*1024
            and hashlib.sha256(raw).hexdigest() == frame['sha256']
            and item['retained_sha256'] == item['model_input_sha256'] == frame['sha256']
            and type(item['model_input_bytes']) is int and item['model_input_bytes'] == len(raw))
        _require(type(capture) is dict and set(capture) == {'schema', 'absolute_frame', 'relative_frame',
            'format', 'quality', 'sha256', 'bytes'}
            and capture['schema'] == 'video.scene-space-frame-capture.v1'
            and type(capture['absolute_frame']) is int and capture['absolute_frame'] == item['absolute_frame']
            and type(capture['relative_frame']) is int and 0 <= capture['relative_frame'] <= item['absolute_frame']
            and capture['format'] == 'png' and capture['quality'] is None
            and capture['sha256'] == frame['sha256'] and type(capture['bytes']) is int
            and capture['bytes'] == len(raw)
            and (item['kind'] != 'static_preview' or capture['relative_frame'] == 0))
        evaluated = evaluator.frame(item['absolute_frame'])
        logical = dict(source_sha256=source_sha256, plan_sha256=frozen['plan_sha256'],
            frozen_plan_sha256=frozen['sha256'], absolute_frame=item['absolute_frame'],
            world_id=evaluated['world_id'], world_revision=evaluated['world_revision'],
            world_sha256=evaluated['world_sha256'], shot_id=evaluated['shot_id'],
            camera_sha256=digest(evaluated['camera']), viewport_sha256=digest(evaluated['viewport']),
            runtime_identity_sha256=runtime_sha, original_png_sha256=frame['sha256'], original_png_bytes=len(raw))
        identities[identity] = dict(logical, sha256=digest(logical))
    static = {}
    for frame in frames:
        if by_id[frame['frame_id']]['kind'] == 'static_preview':
            static.setdefault(identities[frame['frame_id']]['sha256'], frame)
    mapping = {}; supplied = []
    for frame in frames:
        identity = frame['frame_id']; representative = frame
        if by_id[identity]['kind'] == 'clip_keyframe':
            representative = static.get(identities[identity]['sha256'], frame)
            # Hash equality is supplemented by exact bytes, never used alone.
            if files[representative['file_name']] != files[frame['file_name']]:
                representative = frame
        mapping[identity] = representative['frame_id']
        if representative is frame:
            supplied.append(deepcopy(frame))
    supplied_files = {frame['file_name']: files[frame['file_name']] for frame in supplied}
    evidence = dict(schema=SCHEMA, revision=CANONICALIZATION_REVISION,
        original_frames=deepcopy(frames), original_provenance=deepcopy(provenance),
        supplied_frame_ids=[frame['frame_id'] for frame in supplied],
        capture_to_supplied_frame=mapping, identities=identities,
        scope='same_clock_identical_original_png_not_independent_observation')
    evidence['sha256'] = digest(evidence)
    return supplied, supplied_files, evidence
