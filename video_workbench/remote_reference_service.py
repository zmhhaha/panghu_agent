"""Owner-scoped remote references and stopped-role observation handoffs.

Only the host contacts a source website. Durable storage contains a source
descriptor and the actual stills used as evidence, never a copied source video.
"""
from copy import deepcopy
import hashlib
import math
import re

from agent_service.contracts import ServiceError, digest
from .picture_learning_store import identifier, new_id, owner_id, require


SOURCE_TYPE = 'video-url'
MAX_SOURCE_SECONDS = 6 * 60 * 60
MAX_OBSERVATION_REQUESTS = 4
MAX_SUPPLEMENTAL_FRAMES = 32
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def remote_reference(row):
    return row.get('source_type') == SOURCE_TYPE


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def normalized_crop(value):
    if value is None:
        return None
    require(type(value) is dict and set(value) == {'x', 'y', 'width', 'height'}
        and all(finite(item) for item in value.values())
        and 0 <= value['x'] < 1 and 0 <= value['y'] < 1
        and 0 < value['width'] <= 1 and 0 < value['height'] <= 1
        and value['x'] + value['width'] <= 1
        and value['y'] + value['height'] <= 1, 'remote_reference_crop_invalid')
    return deepcopy(value)


class RemoteReferenceServiceMixin:
    def _remote_reference_public(self, row):
        source = row['source']
        result = {key: deepcopy(source[key]) for key in (
            'source_type', 'source_url', 'source_id', 'source_snapshot_sha256',
            'source_identity_scope', 'width', 'height', 'duration_seconds', 'has_audio', 'quality')}
        result.update(id=row['id'], file_name=row['file_name'], created_at=row['created_at'],
            mime_type='video/remote', url=None, media_url=None,
            observation_status='registered_not_observed',
            reference_storage='source_descriptor_only')
        if 'timestamp_seconds' in row:
            result.update(timestamp_seconds=row['timestamp_seconds'], crop=deepcopy(row.get('crop')))
        return result

    def _register_remote_reference(self, owner, body, *, picture):
        owner_id(owner)
        allowed = {'request_id', 'source_type', 'source_url'} | (
            {'timestamp_seconds', 'crop'} if picture else set())
        required = {'request_id', 'source_type', 'source_url'} | (
            {'timestamp_seconds'} if picture else set())
        require(type(body) is dict and required <= set(body) <= allowed
            and body['source_type'] == SOURCE_TYPE, 'remote_reference_request_invalid')
        identifier(body['request_id'])
        require(type(body['source_url']) is str and 1 <= len(body['source_url']) <= 2000,
            'remote_reference_url_invalid')
        crop = normalized_crop(body.get('crop')) if picture else None
        if picture:
            require(finite(body['timestamp_seconds']) and body['timestamp_seconds'] >= 0,
                'remote_reference_timestamp_invalid')
        existing = self.store.request(owner, 'reference', body['request_id'], body)
        if existing:
            return self._remote_reference_public(self._reference(owner, existing))
        source = self.remote_media.describe(body['source_url'])
        require(finite(source['duration_seconds']) and 0 < source['duration_seconds'] <= MAX_SOURCE_SECONDS,
            'remote_reference_duration_invalid')
        if picture:
            require(body['timestamp_seconds'] < source['duration_seconds'],
                'remote_reference_timestamp_invalid')
        row = dict(id=new_id(), owner=owner, created_at=self.clock(), source_type=SOURCE_TYPE,
            source=deepcopy(source), file_name=source.get('title') or source['source_id'],
            width=source['width'], height=source['height'], duration_seconds=source['duration_seconds'],
            has_audio=source['has_audio'], source_url=source['source_url'])
        if picture:
            row.update(timestamp_seconds=body['timestamp_seconds'], crop=crop)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = self.store.request(owner, 'reference', body['request_id'], body, db=db)
            if existing:
                row = self._reference(owner, existing)
            else:
                self.store.put('references', row, db=db, insert=True)
                self.store.bind_request(owner, 'reference', body['request_id'], body, row['id'], db)
        return self._remote_reference_public(row)

    def _store_remote_observation(self, observed, reference, *, start_seconds=0):
        source = reference['source']
        require(observed['source_snapshot']['source_snapshot_sha256'] == source['source_snapshot_sha256'],
            'remote_reference_source_changed', 409)
        frames = []
        for index, frame in enumerate(observed['frames']):
            content = frame['content']
            require(type(content) is bytes and hashlib.sha256(content).hexdigest() == frame['sha256'],
                'remote_reference_frame_changed', 409)
            require(frame['source_snapshot_sha256'] == source['source_snapshot_sha256'],
                'remote_reference_source_changed', 409)
            record = {key: deepcopy(value) for key, value in frame.items() if key != 'content'}
            record.update(sha256=self.store.blob(content), reference_id=reference['id'],
                source_identity_scope='provider_metadata', clip_time=frame['actual_pts'] - start_seconds)
            record.setdefault('frame_id', 'frame-' + str(index + 1))
            record.setdefault('file_name', record['frame_id'] + '.jpg')
            frames.append(record)
        require(bool(frames), 'remote_reference_frames_unavailable', 409)
        return dict(frames=frames, coverage=deepcopy(observed['coverage']) | {
            'source_snapshot_sha256': source['source_snapshot_sha256'],
            'source_identity_scope': 'provider_metadata', 'media_sha256': None,
            'reference_storage': 'source_descriptor_and_used_frame_evidence_only',
            'continuous_motion_verified': False, 'audio_verified': False})

    def _observe_remote(self, reference, *, times, crop=None, quality='overview', start_seconds=0,
                        deadline_at=None):
        observed = self.remote_media.observe(reference['source'], times=times, crop=crop,
            quality=quality, deadline_at=deadline_at)
        return self._store_remote_observation(observed, reference, start_seconds=start_seconds)

    def _remote_source_input(self, reference, start, end):
        source = reference['source']
        keys = ('source_type', 'source_url', 'source_id', 'source_snapshot_sha256',
            'source_identity_scope', 'width', 'height', 'duration_seconds', 'quality')
        result = {key: deepcopy(source[key]) for key in keys}
        result.update(reference_id=reference['id'], start_seconds=start, end_seconds=end)
        if 'timestamp_seconds' in reference:
            result.update(timestamp_seconds=reference['timestamp_seconds'], crop=deepcopy(reference.get('crop')))
        return result

    def _remote_picture_frozen(self, references, *, deadline_at=None):
        observations, sources = {}, []
        for ref in references:
            if not remote_reference(ref):
                continue
            observations[ref['id']] = self._observe_remote(ref, times=[ref['timestamp_seconds']],
                crop=ref.get('crop'), quality='detail', deadline_at=deadline_at)
            sources.append(self._remote_source_input(ref, 0, ref['duration_seconds']))
        return dict(remote_reference_observations=observations, reference_sources=sources) if sources else {}

    def _remote_sample_reference(self, reference, start, end, *, count=8, deadline_at=None):
        times = [round(start + (end - start) * index / count, 9) for index in range(count)]
        observed = self._observe_remote(reference, times=times, quality='overview', start_seconds=start,
            deadline_at=deadline_at)
        require(all(start <= frame['actual_pts'] < end for frame in observed['frames']),
            'remote_reference_frame_outside_scope', 409)
        return observed

    def _refresh_remote_frozen(self, frozen, *, deadline_at):
        refs = frozen['references']
        if 'clip' in frozen:
            ref = refs[0]
            if remote_reference(ref):
                clip = frozen['clip']
                frozen['reference_observation'] = self._remote_sample_reference(ref,
                    clip['start_seconds'], clip['end_seconds'], deadline_at=deadline_at)
                frozen['reference_sources'] = [self._remote_source_input(ref,
                    clip['start_seconds'], clip['end_seconds'])]
        else:
            frozen.update(self._remote_picture_frozen(refs, deadline_at=deadline_at))

    @staticmethod
    def _named_remote_frames(observation, *, prefix='reference', start_index=1):
        result = []
        for index, frame in enumerate(observation['frames'], start_index):
            result.append(deepcopy(frame) | dict(source_frame_id=frame['frame_id'], frame_id=f'{prefix}-{index}',
                file_name=f'{prefix}-{index}.jpg', purpose='reference'))
        return result

    def _with_remote_supplemental_frames(self, run, frames):
        if run['work_kind'] != 'reproduction' or not run['frozen'].get('reference_sources'):
            return frames
        supplemental = []
        for index, item in enumerate(run.get('remote_observations', []), 1):
            supplemental += self._named_remote_frames(item['observation'],
                prefix=f'reference-extra-{index}')
        if not supplemental:
            return frames
        fixed = [frame for frame in frames if frame['purpose'] != 'reference']
        original = [frame for frame in frames if frame['purpose'] == 'reference']
        budget = self.protocol.MAX_FRAMES - len(fixed)
        require(budget > 0, 'remote_reference_frame_budget_exhausted', 409)
        # Keep an anchor for every selected reference, then prioritize the
        # latest requested details. Four picture references must not silently
        # become a comparison against only the source requested most recently.
        anchors, anchor_ids = [], set()
        for frame in original:
            identity = frame.get('reference_id') or ('image:' + frame['sha256'])
            if identity not in anchor_ids:
                anchors.append(frame)
                anchor_ids.add(identity)
        chosen = anchors + list(reversed(supplemental)) + original
        selected, seen = [], set()
        for frame in chosen:
            identity = ((frame.get('reference_id'), frame['source_snapshot_sha256'],
                frame.get('actual_pts'), digest(frame.get('crop')), frame.get('quality'))
                if 'source_snapshot_sha256' in frame else ('image', frame['sha256']))
            if identity not in seen and len(selected) < budget:
                selected.append(frame)
                seen.add(identity)
        # Source-time order remains explicit even when detail priority selected
        # only a view of all retained evidence.
        selected.sort(key=lambda frame: (frame.get('reference_id', ''), frame.get('actual_pts', 0), frame['frame_id']))
        return selected + fixed

    def _remote_document(self, run, document):
        if run['work_kind'] != 'reproduction' or not run['frozen'].get('reference_sources'):
            return document
        document['reference_sources'] = deepcopy(run['frozen']['reference_sources'])
        document['observation_request_history'] = [{
            'request_id': item['operation_id'], 'reference_id': item['request']['reference_id'],
            'request': deepcopy(item['request']), 'frame_ids': [
                f'reference-extra-{index}-{offset}' for offset in range(1, len(item['observation']['frames']) + 1)],
            'source_snapshot_sha256': item['observation']['coverage']['source_snapshot_sha256'],
            'scope': 'sampled_frames_only',
        } for index, item in enumerate(run.get('remote_observations', []), 1)]
        document['observation']['coverage'] = dict(document['observation'].get('coverage') or {}) | {
            'scope': 'sampled_frames_only',
            'supplemental_requests': len(run.get('remote_observations', [])),
            'retained_supplemental_frames': sum(len(item['observation']['frames'])
                for item in run.get('remote_observations', [])),
            'supplied_frame_ids': [frame['frame_id'] for frame in document['observation']['frames']],
            'supplied_reference_frame_map': [{key: deepcopy(frame[key]) for key in
                ('frame_id', 'source_frame_id', 'sha256', 'actual_pts') if key in frame}
                for frame in document['observation']['frames'] if frame['purpose'] == 'reference'],
            'selection': 'One anchor per reference and latest requested detail frames within role input capacity.',
            'continuous_motion_verified': False, 'audio_verified': False}
        return document

    def _queue_remote_observation(self, run, operation, result):
        request = result.get('observation_request')
        if request is None:
            return False
        require(run['work_kind'] == 'reproduction', 'remote_reference_transfer_observation_forbidden')
        require(not run.get('remote_observation_pending'), 'remote_reference_observation_already_pending', 409)
        history = run.get('remote_observations', [])
        require(len(history) < MAX_OBSERVATION_REQUESTS
            and sum(len(item['request']['times']) for item in history) + len(request['times'])
                <= MAX_SUPPLEMENTAL_FRAMES, 'remote_reference_observation_budget_exhausted', 409)
        identity = run['id'] + '-reference-' + str(len(history) + 1)
        pending = dict(operation_id=identity, source_operation_id=operation['id'],
            request=deepcopy(request), stage=operation['stage'])
        run['remote_observation_pending'] = pending
        if not run['stop_requested'] and self.clock() < run['deadline_at']:
            self._resume_remote_observation(run)
        return True

    def _resume_remote_observation(self, run):
        pending = run.get('remote_observation_pending')
        if not pending:
            return False
        request = pending['request']
        source = next((row for row in run['frozen'].get('reference_sources', [])
            if row['reference_id'] == request['reference_id']), None)
        require(source is not None, 'remote_reference_unknown_source', 404)
        times = request['times']
        require(type(times) is list and 1 <= len(times) <= 8
            and all(finite(value) and source['start_seconds'] <= value < source['end_seconds']
                for value in times), 'remote_reference_observation_outside_scope')
        ref = next(row for row in run['frozen']['references'] if row['id'] == source['reference_id'])
        try:
            observation_op = self.store.read('operations', run['owner'], pending['operation_id'])
        except ServiceError as error:
            if error.status != 404:
                raise
            observation_op = dict(id=pending['operation_id'], owner=run['owner'], experiment_id=run['id'],
                stage='reference_observation', status='reserved', input=deepcopy(request),
                input_sha256=digest(request), job_id=None, result=None, created_at=self.clock())
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                self.store.put('operations', observation_op, db=db, insert=True)
                run['operation_ids'].append(observation_op['id'])
                self.store.put('experiments', run, db=db)
        if observation_op['status'] == 'completed':
            observed = observation_op['result']
        else:
            require(self.clock() < run['deadline_at'], 'remote_reference_observation_budget_exhausted', 409)
            # No model submission occurs here. Reconciliation repeats only an
            # uncompleted read from the same immutable reference descriptor.
            observation_op.update(status='reading')
            self.store.put('operations', observation_op)
            try:
                observed = self._observe_remote(ref, times=times, crop=normalized_crop(request.get('crop')),
                    quality=request['quality'], start_seconds=source['start_seconds'],
                    deadline_at=run['deadline_at'])
                require(all(source['start_seconds'] <= frame['actual_pts'] < source['end_seconds']
                    for frame in observed['frames']), 'remote_reference_frame_outside_scope', 409)
            except BaseException as error:
                observation_op.update(status='retryable' if isinstance(error, ServiceError)
                    and error.status >= 500 else 'failed',
                    error=error.code if isinstance(error, ServiceError) else 'remote_reference_read_failed',
                    stop_proven=True)
                self.store.put('operations', observation_op)
                raise
            observation_op.update(status='completed', result=deepcopy(observed), completed_at=self.clock(),
                stop_proven=True)
            self.store.put('operations', observation_op)
        run.setdefault('remote_observations', []).append(dict(operation_id=observation_op['id'],
            source_operation_id=pending['source_operation_id'], request=deepcopy(request),
            observation=deepcopy(observed)))
        run.pop('remote_observation_pending', None)
        return True

    def _cancel_remote_observation(self, run):
        pending = run.get('remote_observation_pending')
        if not pending:
            return
        try:
            op = self.store.read('operations', run['owner'], pending['operation_id'])
        except ServiceError as error:
            if error.status != 404:
                raise
        else:
            if op['status'] != 'completed':
                op.update(status='cancelled', stop_proven=True)
                self.store.put('operations', op)
        run.pop('remote_observation_pending', None)

    def _reference_evidence_artifact(self, owner, project_id, artifact_id):
        if not artifact_id.startswith('reference-'):
            return None
        sha = artifact_id[len('reference-'):]
        require(_SHA.fullmatch(sha) is not None, 'remote_reference_evidence_not_found', 404)
        runs = self.store.list('experiments', owner, project_id=project_id)
        for run in runs:
            observations = list(run['frozen'].get('remote_reference_observations', {}).values())
            if run['frozen'].get('reference_sources') and 'reference_observation' in run['frozen']:
                observations.append(run['frozen']['reference_observation'])
            observations += [item['observation'] for item in run.get('remote_observations', [])]
            for observed in observations:
                if any(frame['sha256'] == sha for frame in observed['frames']):
                    self.store.read_blob(sha)
                    return self.store.blob_path(sha), 'image/jpeg'
        raise ServiceError('remote_reference_evidence_not_found', 404)

    def _project_remote_references(self, result, latest):
        observations = latest['frozen'].get('remote_reference_observations', {})
        video_observation = latest['frozen'].get('reference_observation')
        for ref in result['references']:
            if not remote_reference(ref):
                continue
            observed = observations.get(ref['id']) or video_observation
            if observed and observed['frames']:
                frame = observed['frames'][0]
                ref.update(frame_url=self.prefix + '/projects/' + result['id']
                    + '/artifacts/reference-' + frame['sha256'], actual_pts=frame['actual_pts'],
                    observed_width=frame['width'], observed_height=frame['height'],
                    observed_sha256=frame['sha256'], observation_status='sampled_frames_only')
        return result
