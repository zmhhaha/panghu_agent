"""One full-film Job on the existing durable execution journal and PVC.

No renderer submission is retried after an uncertain POST. Cancellation only
finishes after the bound Job and its Pods disappear; an unobserved submission
remains pending until Kubernetes resolves the exact name.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import threading
import time

from agent_service.contracts import ServiceError, canonical_bytes, digest
from . import media
from .hyperframes_film_execution import (
    INPUT_PREFIX, LABEL, MAX_RESULT_JSON_BYTES, OUTPUT_PREFIX, _directory, _ordinary,
    film_job_resources, stage_film_execution_snapshot, verify_completed_film_output,
)
from .hyperframes_film_export import MAX_MEDIA_BYTES
from .hyperframes_film_snapshot import decode_film_execution_snapshot, require
from .motion_render_adapter import UNAVAILABLE
from .motion_render_k8s import ANNOTATION, _subset

SCHEMA = 'video.hyperframes-film-execution-snapshot.v1'
ADMISSION_SCHEMA = 'video.hyperframes-film-execution-admission.v1'
ADMISSION_SCOPE = 'technical_execution_only_not_agent_profile_or_human_approval'


def verify_execution_admission(value, *, image, runtime_fingerprint, trusted_host_sha256):
    """Operator-supplied technical evidence; never produce an admission here."""
    require(type(value) is dict and value.get('schema') == ADMISSION_SCHEMA
        and value.get('status') == 'admitted' and value.get('scope') == ADMISSION_SCOPE
        and value.get('image') == image and value.get('runtime_fingerprint') == runtime_fingerprint
        and value.get('trusted_host_sha256') == trusted_host_sha256
        and value.get('sha256') == digest({k: v for k, v in value.items() if k != 'sha256'}),
        'hyperframes_film_admission_required', 503)
    evidence = value.get('evidence')
    require(type(evidence) is dict and set(evidence) == {'job', 'media', 'cancellation'},
        'hyperframes_film_admission_required', 503)
    import re
    for item in evidence.values():
        require(type(item) is dict and type(item.get('reference')) is str
            and 0 < len(item['reference']) <= 1024
            and type(item.get('sha256')) is str and re.fullmatch('[0-9a-f]{64}', item['sha256']),
            'hyperframes_film_admission_required', 503)
    return deepcopy(value)


def verify_film_media(path, snapshot, *, cancel_event=None):
    """Independently probe and fully decode the delivered whole-film media."""
    require(path.is_file() and not path.is_symlink()
        and 0 < path.stat().st_size <= MAX_MEDIA_BYTES, 'hyperframes_film_media_invalid', 502)
    ffprobe = media._binary('ffprobe', os.environ.get('VIDEO_FFPROBE_BIN'))
    ffmpeg = media._binary('ffmpeg', os.environ.get('VIDEO_FFMPEG_BIN'))
    count, seconds = snapshot['clock']['frame_count'], snapshot['clock']['frame_count'] / 24
    try:
        probe = json.loads(media._run([ffprobe, '-v', 'error', '-show_streams', '-show_format',
            '-of', 'json', str(path)], cwd=path.parent, timeout=30, cancel_event=cancel_event))
        videos = [row for row in probe['streams'] if row['codec_type'] == 'video']
        audios = [row for row in probe['streams'] if row['codec_type'] == 'audio']
        require(len(videos) == len(audios) == 1, 'hyperframes_film_media_invalid', 502)
        video, audio = videos[0], audios[0]
        n, d = map(int, video['avg_frame_rate'].split('/'))
        require(video['codec_name'] == 'h264' and video['width'] == snapshot['width']
            and video['height'] == snapshot['height'] and d > 0 and n == d * 24
            and int(video['nb_frames']) == count and abs(float(video['duration']) - seconds) <= .002
            and abs(float(video['start_time'])) <= .002
            and abs(float(probe['format']['start_time'])) <= .002
            and abs(float(probe['format']['duration']) - seconds) <= .15
            and audio['codec_name'] == 'aac' and int(audio['sample_rate']) == 48000
            and audio['channels'] == 2 and abs(float(audio['start_time'])) <= .002
            and abs(float(audio['duration']) - seconds) <= .12,
            'hyperframes_film_media_invalid', 502)
        media._run([ffmpeg, '-v', 'error', '-nostdin', '-xerror', '-i', str(path),
            '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'], cwd=path.parent,
            timeout=180, cancel_event=cancel_event)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        raise ServiceError('hyperframes_film_media_invalid', 502) from None
    with path.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    return dict(sha256=checksum, bytes=path.stat().st_size, duration_seconds=float(video['duration']))


class KubeFilmRenderer:
    def __init__(self, journal, kube, *, image, release_id, runtime, trusted_host_sha256,
            admission, pvc_root, pvc_name, root, pvc_workload_selector=None,
            poll_seconds=2, cleanup_seconds=45):
        self.journal, self.kube, self.image = journal, kube, image
        self.release_id, self.runtime = release_id, deepcopy(runtime)
        self.hosts, self.pvc_name = deepcopy(trusted_host_sha256), pvc_name
        self.pvc_workload_selector = deepcopy(pvc_workload_selector)
        self.pvc_root, self.root = _directory(pvc_root), Path(root).resolve()
        self.admission = verify_execution_admission(admission, image=image,
            runtime_fingerprint=runtime, trusted_host_sha256=self.hosts)
        self.poll_seconds, self.cleanup_seconds = poll_seconds, cleanup_seconds
        self.validation_lock = threading.Lock()

    def verify_snapshot(self, snapshot):
        with self.validation_lock:
            return decode_film_execution_snapshot(snapshot, expected_snapshot_sha256=snapshot.get('sha256'),
                **self.release)

    @property
    def release(self):
        return dict(image=self.image, runtime_fingerprint=self.runtime, trusted_host_sha256=self.hosts)

    def check_policy(self):
        response = self.kube.request('GET', 'networkpolicies')
        require(type(response) is dict and type(response.get('items')) is list,
            'hyperframes_film_namespace_not_isolated', 503)
        # Kubernetes policy rules are additive. A matching deny alone is not
        # enough when another selector grants the renderer ingress or egress.
        labels = {'app': 'video-hyperframes-film-renderer', LABEL + 'execution': None,
            'job-name': None, 'batch.kubernetes.io/job-name': None,
            'controller-uid': None, 'batch.kubernetes.io/controller-uid': None}
        denied = set()
        for row in response['items']:
            spec = row.get('spec', {})
            selector = spec.get('podSelector', {})
            if not self._selects(selector, labels):
                continue
            require(not spec.get('ingress') and not spec.get('egress'),
                'hyperframes_film_namespace_not_isolated', 503)
            if self._selects(selector, labels, possible=False):
                denied.update(spec.get('policyTypes', ['Ingress']))
        require(denied >= {'Ingress', 'Egress'}, 'hyperframes_film_namespace_not_isolated', 503)

    @staticmethod
    def _selects(selector, labels, *, possible=True):
        if any(key not in labels or labels[key] is not None and labels[key] != value
               or labels.get(key) is None and not possible
               for key, value in selector.get('matchLabels', {}).items()):
            return False
        for row in selector.get('matchExpressions', []):
            key, operator, values = row.get('key'), row.get('operator'), row.get('values', [])
            if operator == 'In' and (key not in labels or labels[key] is not None and labels[key] not in values):
                return False
            if operator == 'NotIn' and key in labels and labels[key] is not None and labels[key] in values:
                return False
            if operator in {'In', 'NotIn'} and key in labels and labels[key] is None and not possible:
                return False
            if operator == 'Exists' and key not in labels:
                return False
            if operator == 'DoesNotExist' and key in labels:
                return False
            require(operator in {'In', 'NotIn', 'Exists', 'DoesNotExist'},
                'hyperframes_film_namespace_not_isolated', 503)
        return True

    def _context(self, snapshot, key):
        with self.validation_lock:
            resources = film_job_resources(snapshot, expected_snapshot_sha256=snapshot['sha256'],
                **self.release, namespace=self.kube.namespace, pvc_name=self.pvc_name,
                operation_key=key, release_id=self.release_id,
                pvc_workload_selector=self.pvc_workload_selector)
        return resources, key + ':film', self.pvc_root / resources['result_sub_path']

    @staticmethod
    def _bound(expected, actual):
        require(actual is not None and _subset(expected, actual),
            'hyperframes_film_k8s_binding_mismatch', 409)

    def _pods(self, name, binding):
        response = self.kube.request('GET', 'pods', params={'labelSelector': 'job-name=' + name})
        require(type(response) is dict and type(response.get('items')) is list, UNAVAILABLE, 503)
        pods = response['items']
        require(len(pods) <= 1, 'hyperframes_film_unexpected_retry', 409)
        for pod in pods:
            require(pod.get('metadata', {}).get('annotations', {}).get(ANNOTATION) == binding,
                'hyperframes_film_k8s_binding_mismatch', 409)
        return pods

    def _prepare_storage(self, snapshot, directory):
        inputs = self.pvc_root / INPUT_PREFIX
        inputs.mkdir(parents=True, exist_ok=True)
        with self.validation_lock:
            stage_film_execution_snapshot(inputs, snapshot,
                expected_snapshot_sha256=snapshot['sha256'], **self.release)
        directory.mkdir(parents=True, exist_ok=True)
        _directory(directory)
        if os.name != 'nt':
            if os.geteuid() == 0:
                os.chown(directory, 10001, 10001)
            require(directory.stat().st_uid == 10001 and directory.stat().st_gid == 10001,
                'hyperframes_film_output_permissions', 503)
            directory.chmod(0o770)

    def _pending(self, handle, state, error=None, status=502, result=None):
        record = self.journal.scene(handle)
        pending = dict(state=state, error=error, status=status, result=result,
            submission_uncertain=record['state'] == 'submitting')
        self.journal.scene_update(handle, 'cleanup_pending', result={'cleanup_pending': pending}, error=error)

    def _drain(self, resources, *, uncertain=False):
        expected, binding = resources['job'], resources['binding']
        name = expected['metadata']['name']
        actual = self.kube.request('GET', 'jobs', name)
        if actual is None and uncertain:
            # An earlier POST may still commit after this 404. Do not announce
            # cancellation, fence the name, submit again, or erase the handle.
            raise ServiceError(UNAVAILABLE, 503)
        if actual is not None:
            self._bound(expected, actual)
            require(type(actual['metadata'].get('uid')) is str, UNAVAILABLE, 503)
            self.kube.request('DELETE', 'jobs', name, body=dict(apiVersion='v1', kind='DeleteOptions',
                propagationPolicy='Foreground', preconditions={'uid': actual['metadata']['uid']}))
        until = time.monotonic() + self.cleanup_seconds
        while True:
            current = self.kube.request('GET', 'jobs', name)
            if current is not None:
                self._bound(expected, current)
            pods = self._pods(name, binding)
            if current is None and not pods:
                return
            require(time.monotonic() < until, UNAVAILABLE, 503)
            time.sleep(self.poll_seconds)

    def _finish_cleanup(self, snapshot, resources, handle, directory, cancel_event=None):
        pending = json.loads(self.journal.scene(handle)['result'])['cleanup_pending']
        try:
            if pending['submission_uncertain']:
                actual = self.kube.request('GET', 'jobs', resources['job']['metadata']['name'])
                if actual is not None:
                    self._bound(resources['job'], actual)
                    pending['submission_uncertain'] = False
                    self.journal.scene_update(handle, 'cleanup_pending',
                        result={'cleanup_pending': pending}, error=pending['error'])
            self._drain(resources, uncertain=pending['submission_uncertain'])
        except Exception:
            raise ServiceError(UNAVAILABLE, 503) from None
        self.journal.scene_update(handle, pending['state'], result=pending['result'] or {}, error=pending['error'])
        if pending['state'] != 'completed':
            raise ServiceError(pending['error'] or 'hyperframes_film_execution_failed', pending['status'])
        return self._recover(snapshot, resources, directory, cancel_event=cancel_event)

    def _recover(self, snapshot, resources, directory, *, cancel_event=None):
        with self.validation_lock:
            job_result = verify_completed_film_output(directory, snapshot=snapshot,
                expected_snapshot_sha256=snapshot['sha256'], **self.release)
        path = directory / 'video.mp4'
        metadata = verify_film_media(path, snapshot, cancel_event=cancel_event)
        require(metadata['sha256'] == job_result['artifacts']['video.mp4']['sha256'],
            'hyperframes_film_media_changed', 409)
        receipt = json.loads(_ordinary(directory / 'export-receipt.json', MAX_RESULT_JSON_BYTES))
        count = snapshot['clock']['frame_count']
        return dict(**metadata, expected_duration_seconds=count / 24, width=snapshot['width'],
            height=snapshot['height'], fps=24, video_codec='h264', audio_codec='aac',
            audio_sample_rate=48000, audio_channels=2, silent=False, subtitles=False,
            visual_mode='hyperframes-film', snapshot_sha256=snapshot['sha256'],
            runtime_fingerprint=self.runtime, quality='draft', elapsed_seconds=0,
            film_export=dict(bundle_sha256=snapshot['bundle_sha256'], film_sha256=snapshot['film_sha256'],
                project_manifest_sha256=snapshot['project_manifest_sha256'], export_receipt_sha256=receipt['sha256'],
                capture_sha256=receipt['capture_sha256'], audio_mix_sha256=receipt['audio_mix_sha256'],
                job_result_sha256=job_result['sha256'], job_binding=resources['binding'],
                admission_sha256=self.admission['sha256'], concatenated_scene_videos=False,
                clock=dict(fps=24, frame_count=count, sample_rate=48000, sample_count=count * 2000)))

    def _publish(self, source, output_dir, result):
        target = Path(output_dir)
        require(target == self.root / result.pop('_key') / 'result', 'hyperframes_film_output_path_invalid', 409)
        target.mkdir(parents=True, exist_ok=True)
        _directory(target)
        destination = target / 'video.mp4'
        temporary = target / 'video.pending'
        require(not temporary.is_symlink() and not destination.is_symlink(),
            'hyperframes_film_output_path_invalid', 409)
        if temporary.exists():
            descriptor = os.open(temporary, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            try:
                info = os.fstat(descriptor)
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= MAX_MEDIA_BYTES,
                    'hyperframes_film_output_path_invalid', 409)
            finally:
                os.close(descriptor)
            # A crash can leave this service-owned copy half written. The PVC
            # source was independently verified; rebuild only this known file.
            temporary.unlink()
        try:
            with source.open('rb') as reader, temporary.open('xb') as writer:
                shutil.copyfileobj(reader, writer, 1024 * 1024)
                writer.flush()
                os.fsync(writer.fileno())
            with temporary.open('rb') as stream:
                require(hashlib.file_digest(stream, 'sha256').hexdigest() == result['sha256'],
                    'hyperframes_film_media_changed', 409)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        (target / 'report.json').write_bytes(canonical_bytes(result))
        return {**result, 'path': str(destination)}

    def render(self, snapshot, output_dir, *, key, cancel_event=None):
        started = time.monotonic()
        resources, handle, directory = self._context(snapshot, key)
        record = self.journal.scene_intent(handle, resources['binding'], resources['job']['metadata']['name'])
        try:
            if record['state'] == 'cleanup_pending':
                result = self._finish_cleanup(snapshot, resources, handle, directory, cancel_event)
            elif record['state'] == 'completed':
                self._drain(resources)
                result = self._recover(snapshot, resources, directory, cancel_event=cancel_event)
            elif record['state'] in {'cancelled', 'failed', 'timed_out'}:
                raise ServiceError(record['error'] or 'hyperframes_film_execution_failed',
                    409 if record['state'] == 'cancelled' else 504 if record['state'] == 'timed_out' else 502)
            else:
                self.check_policy()
                result = self._execute(snapshot, resources, handle, directory, record, cancel_event)
            result.update(elapsed_seconds=time.monotonic() - started, _key=key)
            return self._publish(directory / 'video.mp4', output_dir, result)
        except Exception as failure:
            error = failure if isinstance(failure, ServiceError) else ServiceError(
                'hyperframes_film_execution_storage_error' if isinstance(failure, OSError)
                else 'hyperframes_film_execution_internal_error', 507 if isinstance(failure, OSError) else 502)
            if error.code == UNAVAILABLE:
                raise error from None
            current = self.journal.scene(handle)
            if current['state'] not in {'failed', 'cancelled', 'timed_out'}:
                self._pending(handle, 'cancelled' if error.code == 'media_render_cancelled'
                    else 'timed_out' if error.status == 504 else 'failed', error.code, error.status)
                self._finish_cleanup(snapshot, resources, handle, directory)
            raise error from None

    def _execute(self, snapshot, resources, handle, directory, record, cancel_event):
        expected, binding = resources['job'], resources['binding']
        name = expected['metadata']['name']
        cancelled = lambda: cancel_event is not None and cancel_event.is_set()
        if cancelled():
            raise ServiceError('media_render_cancelled', 409)
        actual = self.kube.request('GET', 'jobs', name)
        if actual is not None:
            self._bound(expected, actual)
            self.journal.scene_update(handle, 'running')
        elif record['state'] == 'submitting':
            raise ServiceError(UNAVAILABLE, 503)
        elif record['state'] == 'running':
            raise ServiceError('hyperframes_film_execution_disappeared', 502)
        else:
            self._prepare_storage(snapshot, directory)
            if cancelled():
                raise ServiceError('media_render_cancelled', 409)
            self.journal.scene_update(handle, 'submitting')
            try:
                self.kube.request('POST', 'jobs', body=expected)
            except ServiceError as error:
                if error.code not in {UNAVAILABLE, 'motion_k8s_conflict'}:
                    raise
            actual = self.kube.request('GET', 'jobs', name)
            require(actual is not None, UNAVAILABLE, 503)
            self._bound(expected, actual)
            self.journal.scene_update(handle, 'running')
        until = time.monotonic() + max(0, snapshot['timeout_seconds'] + 150 - (time.time() - record['updated']))
        while True:
            if cancelled():
                raise ServiceError('media_render_cancelled', 409)
            require(time.monotonic() < until, 'hyperframes_film_execution_timed_out', 504)
            actual = self.kube.request('GET', 'jobs', name)
            require(actual is not None, 'hyperframes_film_execution_disappeared', 502)
            self._bound(expected, actual)
            pods = self._pods(name, binding)
            for pod in pods:
                for status in pod.get('status', {}).get('containerStatuses', []):
                    if status.get('imageID'):
                        require(status['imageID'].endswith(self.runtime['image_digest']),
                            'hyperframes_film_running_image_mismatch', 409)
                    terminated = status.get('state', {}).get('terminated', {})
                    require(terminated.get('reason') != 'OOMKilled', 'hyperframes_film_execution_oom', 502)
                    require(terminated.get('exitCode', 0) == 0, 'hyperframes_film_execution_failed', 502)
            failure = next((row for row in actual.get('status', {}).get('conditions', [])
                if row.get('type') == 'Failed' and row.get('status') == 'True'), None)
            if failure:
                raise ServiceError('hyperframes_film_execution_timed_out'
                    if failure.get('reason') == 'DeadlineExceeded' else 'hyperframes_film_execution_failed',
                    504 if failure.get('reason') == 'DeadlineExceeded' else 502)
            if actual.get('status', {}).get('succeeded') == 1:
                require(len(pods) == 1, 'hyperframes_film_pod_missing', 502)
                statuses = pods[0].get('status', {}).get('containerStatuses', [])
                require(len(statuses) == 1 and statuses[0].get('name') == 'renderer'
                    and statuses[0].get('imageID', '').endswith(self.runtime['image_digest'])
                    and statuses[0].get('state', {}).get('terminated', {}).get('exitCode') == 0,
                    'hyperframes_film_running_image_mismatch', 409)
                result = self._recover(snapshot, resources, directory, cancel_event=cancel_event)
                self._pending(handle, 'completed', result=result)
                return self._finish_cleanup(snapshot, resources, handle, directory, cancel_event)
            time.sleep(self.poll_seconds)

    def cleanup(self, snapshot, *, key, state, error, status):
        resources, handle, directory = self._context(snapshot, key)
        record = self.journal.scene(handle)
        if record is None:
            return
        require(record['binding'] == resources['binding']
            and record['name'] == resources['job']['metadata']['name'], UNAVAILABLE, 503)
        if record['state'] != 'cleanup_pending':
            self._pending(handle, state, error, status)
        try:
            self._finish_cleanup(snapshot, resources, handle, directory)
        except ServiceError as failure:
            if failure.code == UNAVAILABLE:
                raise
