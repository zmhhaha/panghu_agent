"""Business adapters for the executor-owned sampling service.

The adapters only register verified retained bytes during an explicit advance.
They preserve original evaluator inputs; the sampling reference lives alongside
the operation, never inside its prompt or result contract.
"""
from copy import deepcopy
import hashlib
import math
import time

from agent_service.contracts import ServiceError, digest


def require(ok, code='visual_sampling_business_input_changed'):
    if not ok:
        raise ServiceError(code, 409)


def sampling_service(value):
    service = getattr(value, 'sampling_service', None)
    if service is None:
        service = getattr(getattr(value, 'renderer', None), 'sampling_service', None)
    return service


def image_mime(raw):
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if raw.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    raise ServiceError('visual_sampling_image_format_invalid', 409)


def _time(frame):
    for name in ('timestamp_seconds', 'clip_time', 'time_seconds', 'actual_pts'):
        value = frame.get(name)
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            return value
    return 0


def sampling_manifest(service, owner, operation_id):
    read = getattr(service, 'read_manifest', None) or service.get
    return read(owner, operation_id)


def retain_frames(service, owner, operation_id, source_ref, policy, values, *, deadline_at=None):
    """Prepare only from a business write/advance; reads never advance work."""
    remote = getattr(service, 'prepare_frames', None)
    if callable(remote):
        result = remote(owner, operation_id, source_ref, policy, values, deadline_at=deadline_at)
        until = min(deadline_at or time.time() + 60, time.time() + 60)
        while result.get('state', result.get('status')) in {'queued', 'running', 'uploading'}:
            if time.time() >= until:
                raise ServiceError('visual_sampling_business_input_pending', 503)
            time.sleep(min(.1, max(0, until - time.time())))
            result = service.get(owner, operation_id)
        require(result.get('state', result.get('status')) == 'completed',
                'visual_sampling_business_input_unavailable')
        return sampling_manifest(service, owner, operation_id)

    def provider(actual_owner, selected_source, selected_policy, check):
        require(actual_owner == owner and selected_source == source_ref and selected_policy == policy)
        for frame in values:
            check()
        return dict(source=deepcopy(source_ref), frames=values)

    return service.prepare(owner, operation_id, source_ref, policy, provider=provider,
                           deadline_at=deadline_at)


def prepare_input_frames(service, owner, operation_id, document, files, *, source=None,
                         deadline_at=None):
    """Freeze every supplied role, retaining duplicate pixels at distinct times."""
    frames = document.get('observation', {}).get('frames', [])
    if service is None or not frames:
        return None, files
    metadata = deepcopy(frames)
    identities = [frame.get('frame_id', frame['file_name']) for frame in frames]
    require(len(identities) == len(set(identities)))
    source_ref = dict(provider='retained-business-input',owner=owner,business_operation_id=operation_id,
                      input_sha256=digest(document),
                      observation=metadata, context=deepcopy(source or {}))
    policy = dict(strategy='provided', frame_count=len(frames), max_frames=len(frames),
                  frame_ids=identities, scope='actual_supplied_images_only')

    def provider(actual_owner, ref, selected_policy, check):
        require(actual_owner == owner and ref == source_ref and selected_policy == policy)
        values = []
        for index, frame in enumerate(frames):
            check()
            raw = files.get(frame['file_name'])
            require(type(raw) is bytes and hashlib.sha256(raw).hexdigest() == frame['sha256'])
            clock = next((name for name in ('timestamp_seconds', 'clip_time', 'time_seconds', 'actual_pts')
                          if type(frame.get(name)) in (int,float) and math.isfinite(frame[name])
                          and frame[name]>=0), None)
            if clock is None:
                declared_frame=next((name for name in ('absolute_frame','frame_number','frame')
                    if type(frame.get(name)) is int),None)
                scope='declared_frame_without_timebase' if declared_frame else 'unknown'
                basis=declared_frame or 'not_provided_zero_placeholder'
            else:
                scope='source_reported_presentation_time' if clock=='actual_pts' else 'declared_observation_time'
                basis=clock
            values.append(dict(raw=raw, mime=image_mime(raw), timestamp_seconds=_time(frame),
                frame_id=identities[index], file_name=frame['file_name'],
                observation=deepcopy(frame), evidence_kind='retained_model_input',
                clock_basis=basis,clock_scope=scope,
                role=frame.get('purpose', 'output')))
        return dict(source=deepcopy(source_ref), frames=values)

    sampling_id = 'business-' + digest(dict(owner=owner, operation=operation_id))
    values = provider(owner, source_ref, policy, lambda: None)['frames']
    result = retain_frames(service, owner, sampling_id, source_ref, policy, values, deadline_at=deadline_at)
    require(result.get('status') == 'completed', 'visual_sampling_business_input_unavailable')
    reference = dict(operation_id=sampling_id, manifest_sha256=result['manifest_sha256'])
    return reference, read_input_frames(service, owner, reference, document, files,
                                       expected_operation_id=operation_id)


def read_input_frames(service, owner, reference, document, files, *, expected_operation_id=None):
    """Read only; validate the exact file, role and logical observation identity."""
    result = sampling_manifest(service, owner, reference['operation_id'])
    require(result.get('status') == 'completed'
            and result.get('manifest_sha256') == reference['manifest_sha256'])
    source=result.get('source_ref',{})
    if source.get('provider')=='private-retained-source':source=source.get('source',{})
    require(source.get('provider')=='retained-business-input' and source.get('owner')==owner
        and source.get('input_sha256')==digest(document)
        and source.get('observation')==document.get('observation',{}).get('frames',[]))
    business_id=source.get('business_operation_id')
    require(type(business_id) is str and reference['operation_id']==
        'business-'+digest(dict(owner=owner,operation=business_id))
        and (expected_operation_id is None or business_id==expected_operation_id))
    rows = service.read_frames(owner, reference['operation_id'])
    expected = document.get('observation', {}).get('frames', [])
    require(len(rows) == len(expected))
    copied = dict(files)
    for row, frame in zip(rows, expected):
        identity = frame.get('frame_id', frame['file_name'])
        raw = row.get('raw')
        require(row.get('frame_id') == identity and row.get('file_name') == frame['file_name']
                and row.get('observation') == frame and type(raw) is bytes
                and hashlib.sha256(raw).hexdigest() == frame['sha256']
                and row.get('mime') == image_mime(raw))
        require(files.get(frame['file_name']) == raw)
        copied[frame['file_name']] = raw
    return copied


def verify_input_delivery(service, document, files, receipt):
    if service is None or not document.get('observation', {}).get('frames'):
        return None
    from .visual_sampling import verify_delivery
    return verify_delivery(document['observation']['frames'], files, receipt)
