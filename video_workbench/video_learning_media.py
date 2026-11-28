"""Decode bounded local reference media and retain measured presentation times.

This adapter prepares visual evidence. It does not watch continuous motion or
listen to audio, and never treats a source URL as observed media.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import time
from uuid import uuid4

from agent_service.contracts import ServiceError
from .picture_learning_store import require

MAX_UPLOAD = 24 * 1024 * 1024
MAX_DURATION = 600
SAMPLES_PER_MEDIA = 8
MAX_IMAGE_BYTES = 96 * 1024
LIMITATIONS = [
    'Ordered sampled still frames only; continuous playback and unsampled motion are unverified.',
    'Times are measured FFmpeg presentation timestamps, not only requested seek labels.',
    'Audio stream presence is metadata; no audio was supplied to or heard by either model role.',
]


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


class VideoLearningMedia:
    def __init__(self, root, *, ffmpeg='ffmpeg', ffprobe='ffprobe'):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ffmpeg, self.ffprobe = ffmpeg, ffprobe

    def probe(self, path):
        path = Path(path)
        require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 32 * 1024 * 1024,
                'video_learning_media_size', 413)
        with path.open('rb') as stream:
            header = stream.read(32)
        require((len(header) >= 12 and header[4:8] == b'ftyp') or header[:4] == b'\x1aE\xdf\xa3',
                'video_learning_media_format')
        try:
            result = subprocess.run([self.ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe',
                '-format_whitelist', 'mov,matroska,webm', '-show_entries',
                'stream=codec_type,width,height,avg_frame_rate:format=duration,format_name',
                '-of', 'json', str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise ServiceError('video_learning_decoder_unavailable', 503) from None
        require(result.returncode == 0 and len(result.stdout) <= 65536,
                'video_learning_media_decode_failed')
        try:
            metadata = json.loads(result.stdout)
            streams = metadata['streams']
            video = next(row for row in streams if row.get('codec_type') == 'video')
            width, height = int(video['width']), int(video['height'])
            duration = float(metadata['format']['duration'])
            formats = metadata['format']['format_name'].split(',')
        except (ValueError, KeyError, TypeError, StopIteration):
            raise ServiceError('video_learning_media_metadata_invalid', 422) from None
        require(1 <= width <= 7680 and 1 <= height <= 4320 and width * height <= 32_000_000
                and finite(duration) and 1 <= duration <= MAX_DURATION,
                'video_learning_media_dimensions_or_duration')
        return dict(width=width, height=height, duration_seconds=duration,
                    has_audio=any(row.get('codec_type') == 'audio' for row in streams),
                    mime_type='video/webm' if 'webm' in formats else 'video/mp4')

    @staticmethod
    def clip(metadata, start, end):
        require(finite(start) and finite(end) and 0 <= start < end <= metadata['duration_seconds'],
                'video_learning_clip_invalid')
        duration = end - start
        require(1 <= duration <= 60 and abs(round(duration * 24) - duration * 24) < 1e-6,
                'video_learning_clip_requires_24fps_duration')
        duration = round(duration * 24) / 24
        return dict(start_seconds=start, end_seconds=end, duration_seconds=duration, fps=24)

    def sample(self, path, *, media_sha256, start_seconds, end_seconds, store,
               sample_count=SAMPLES_PER_MEDIA, deadline_at=None, sampling_service=None,
               sampling_owner=None, sampling_operation_id=None):
        if sampling_service is None:
            return self._sample(path, media_sha256=media_sha256, start_seconds=start_seconds,
                end_seconds=end_seconds, store=store, sample_count=sample_count, deadline_at=deadline_at)
        require(type(sample_count) is int and 2 <= sample_count <= 24,
                'video_learning_sampling_budget_invalid')
        require(hashlib.sha256(Path(path).read_bytes()).hexdigest() == media_sha256,
                'video_learning_media_changed', 409)
        from .native_sampling import sampling_manifest
        require(sampling_owner is not None and sampling_operation_id is not None,
                'video_learning_sampling_identity_required')
        source = dict(provider='video-learning-decoder', media_sha256=media_sha256,
            start_seconds=start_seconds, end_seconds=end_seconds)
        step=(end_seconds-start_seconds)/sample_count
        policy = dict(strategy='learning_half_open', max_frames=sample_count,
            requested_samples=sample_count, width=640, height=480, mime='image/jpeg',
            decoder_quality=5, bounded_jpeg_qualities=[85,75,60,45], max_frame_bytes=MAX_IMAGE_BYTES,
            targets=[round(start_seconds + index * step,9)
                     for index in range(sample_count)], alias_policy='identical_measured_pts_only')

        def provider(owner, selected_source, selected_policy, check):
            require(owner == sampling_owner and selected_source == source and selected_policy == policy,
                    'video_learning_sampling_binding_changed')
            observed=self._sample(path,media_sha256=media_sha256,start_seconds=start_seconds,
                end_seconds=end_seconds,store=store,sample_count=sample_count,deadline_at=deadline_at,
                check=check)
            frames=[]
            for frame in observed['frames']:
                check()
                frames.append(dict(frame,raw=store.read_blob(frame['sha256']),mime='image/jpeg',
                    timestamp_seconds=frame['actual_pts'],clock_basis='measured_ffmpeg_pts',
                    evidence_kind='decoded_media_observation'))
            return dict(source=dict(source,observation=observed),frames=frames)

        result=sampling_service.prepare(sampling_owner,sampling_operation_id,source,policy,
            provider=provider,deadline_at=deadline_at)
        require(result.get('status')=='completed','video_learning_sampling_unavailable',409)
        manifest=sampling_manifest(sampling_service,sampling_owner,sampling_operation_id)
        observed=deepcopy(manifest['source']['observation'])
        rows=sampling_service.read_frames(sampling_owner,sampling_operation_id)
        require(len(rows)==len(observed['frames']),'video_learning_sampling_binding_changed')
        for row,frame in zip(rows,observed['frames']):
            require(row['frame_id']==frame['frame_id'] and row['actual_pts']==frame['actual_pts']
                and row['mime']=='image/jpeg' and store.blob(row['raw'])==frame['sha256'],
                'video_learning_sampling_binding_changed')
        return observed

    def _sample(self, path, *, media_sha256, start_seconds, end_seconds, store,
               sample_count=SAMPLES_PER_MEDIA, deadline_at=None, check=None):
        require(type(sample_count) is int and 2 <= sample_count <= 24,
                'video_learning_sampling_budget_invalid')
        require(hashlib.sha256(Path(path).read_bytes()).hexdigest() == media_sha256,
                'video_learning_media_changed', 409)
        directory = self.root / uuid4().hex
        directory.mkdir()
        try:
            frames, requests = [], []
            # Sample both establishment and ending without requesting the
            # half-open endpoint, which might select a frame outside the clip.
            step = (end_seconds - start_seconds) / sample_count
            targets = [round(start_seconds + index * step, 9) for index in range(sample_count)]
            previous = None
            for index, requested in enumerate(targets):
                if check is not None:check()
                target = directory / ('frame-%02d.jpg' % index)
                remaining = 60 if deadline_at is None else deadline_at - time.time()
                require(remaining > 0, 'video_learning_sampling_budget_exhausted', 409)
                measured = self._decode(path, target, requested, timeout=min(60, remaining))
                actual = measured['time_seconds']
                require(start_seconds <= actual < end_seconds, 'video_learning_frame_outside_clip')
                alias = previous is not None and actual == previous['actual_pts']
                frame_id = previous['frame_id'] if alias else 'frame-%02d' % index
                requests.append(dict(requested_time=requested, actual_pts=actual,
                    frame_id=frame_id, alias=alias, pts_ticks=measured['pts'],
                    time_base=measured['time_base']))
                if alias:
                    continue
                require(previous is None or actual > previous['actual_pts'],
                        'video_learning_frame_order')
                raw = self._bounded_image(target.read_bytes())
                frame = dict(frame_id=frame_id, file_name=frame_id + '.jpg', sha256=store.blob(raw),
                    media_sha256=media_sha256, requested_time=requested, actual_pts=actual,
                    clip_time=actual - start_seconds)
                frames.append(frame)
                previous = frame
            require(len(frames) >= 2, 'video_learning_reference_insufficient_frames')
            require(hashlib.sha256(Path(path).read_bytes()).hexdigest() == media_sha256,
                    'video_learning_media_changed', 409)
            return dict(frames=frames, coverage=dict(scope='sampled_frames_only',
                media_sha256=media_sha256, start_seconds=start_seconds, end_seconds=end_seconds,
                requested_samples=len(targets), unique_presentation_frames=len(frames),
                aliased_samples=len(targets) - len(frames), requests=requests,
                continuous_motion_verified=False, audio_verified=False,
                limitations=deepcopy(LIMITATIONS)))
        finally:
            shutil.rmtree(directory)

    def _decode(self, media_path, target, requested, *, timeout=60):
        selector = 'select=gte(t\\,%s),scale=\'min(640,iw)\':-2,showinfo' % requested
        try:
            result = subprocess.run([self.ffmpeg, '-v', 'info', '-nostdin', '-protocol_whitelist',
                'file,pipe', '-format_whitelist', 'mov,matroska,webm', '-i', str(media_path),
                '-map', '0:v:0', '-vf', selector, '-frames:v', '1', '-an', '-fps_mode',
                'passthrough', '-q:v', '5', '-threads', '1', str(target)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise ServiceError('video_learning_decoder_unavailable', 503) from None
        require(result.returncode == 0 and len(result.stderr) <= 1024 * 1024,
                'video_learning_frame_decode_failed')
        log = result.stderr.decode('utf-8', errors='replace')
        bases = re.findall(r'^\[Parsed_showinfo_(\d+) @ ([^\]]+)\]\s+config in time_base:\s*(\d+)/(\d+)', log, re.M)
        firsts = re.findall(r'^\[Parsed_showinfo_(\d+) @ ([^\]]+)\]\s+n:\s*0\s+pts:\s*(-?\d+)\s+pts_time:', log, re.M)
        require(len(bases) == len(firsts) == 1 and bases[0][:2] == firsts[0][:2],
                'video_learning_frame_timestamp_unverified')
        numerator, denominator, pts = int(bases[0][2]), int(bases[0][3]), int(firsts[0][2])
        require(numerator > 0 and denominator > 0 and pts >= 0,
                'video_learning_frame_timestamp_unverified')
        actual = pts * numerator / denominator
        require(finite(actual) and actual + 1e-9 >= requested,
                'video_learning_frame_timestamp_unverified')
        return dict(pts=pts, time_base=[numerator, denominator], time_seconds=actual)

    @staticmethod
    def _bounded_image(raw):
        from PIL import Image
        with Image.open(io.BytesIO(raw)) as original:
            image = original.convert('RGB')
            image.thumbnail((640, 480))
            for quality in (85, 75, 60, 45):
                output = io.BytesIO()
                image.save(output, format='JPEG', quality=quality)
                value = output.getvalue()
                if len(value) <= MAX_IMAGE_BYTES:
                    return value
        raise ServiceError('video_learning_frame_too_large', 413)
