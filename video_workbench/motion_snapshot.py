"""Version-two immutable inputs for mixed static and motion production.

This is a pure freezing boundary, not an authorization or adoption endpoint.
Callers resolve versions through owner-scoped storage first. Delivery additionally
requires an exact current-adoption map, checked in the caller's transaction.
The existing static snapshot v1 implementation is deliberately untouched.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import math
import re

from agent_service.contracts import ServiceError, canonical_bytes, digest
from . import media
from .motion import validate_alignment, semantic_digest
from .motion_dispatch import scene_compiler
from .speech import inspect_wav
from .audio_store import narration_document, narration_binding

SCHEMA = 'video.render-snapshot.v2'
RUNTIME_KEYS = {'schema', 'hyperframes', 'hyperframes_source', 'node', 'browser', 'ffmpeg',
                'gsap', 'gsap_sha256', 'font_sha256', 'dpkg_sha256', 'npm_lock_sha256', 'image_digest'}
ENGINE_RUNTIME_KEYS = RUNTIME_KEYS | {'three', 'three_sha256', 'three_license_sha256',
                                    'engine_runtime_sha256', 'creative_host_sha256'}
HYPERFRAMES_RUNTIME_KEYS = ENGINE_RUNTIME_KEYS | {
    'hyperframes_core_sha256', 'hyperframes_project_host_sha256'}


def _require(value, code='invalid_motion_snapshot', status=422):
    if not value:
        raise ServiceError(code, status)


def _runtime(value):
    _require(type(value) is dict and set(value) ==
        (HYPERFRAMES_RUNTIME_KEYS if value.get('schema') == 'video.motion-runtime.v3' else
         ENGINE_RUNTIME_KEYS if value.get('schema') == 'video.motion-runtime.v2' else RUNTIME_KEYS),
        'motion_runtime_fingerprint_missing')
    try:
        raw = canonical_bytes(value)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ServiceError('motion_runtime_fingerprint_invalid', 422) from None
    _require(len(raw) <= 16384, 'motion_runtime_fingerprint_invalid')
    _require(all(type(v) is str and 0 < len(v) <= 2000
                 and not any(ord(c) < 32 or ord(c) == 127 for c in v) for v in value.values()),
             'motion_runtime_fingerprint_invalid')
    _require(value['schema'] in {'video.motion-runtime.v1', 'video.motion-runtime.v2', 'video.motion-runtime.v3'} and value['hyperframes'] == '0.8.149'
             and value['hyperframes_source'] == '2905162d925fc87fb8242e24ed0bbf025d307ea1'
             and value['gsap'] == '3.14.2', 'motion_runtime_version_mismatch')
    _require(re.fullmatch(r'sha256:[0-9a-f]{64}', value['image_digest'])
             and all(re.fullmatch(r'[0-9a-f]{64}', value[key])
                     for key in ('gsap_sha256', 'font_sha256', 'dpkg_sha256', 'npm_lock_sha256')),
             'motion_runtime_fingerprint_invalid')
    if value['schema'] in {'video.motion-runtime.v2', 'video.motion-runtime.v3'}:
        _require(value['three'] == '0.170.0'
            and value['three_sha256'] == '08fd7545d13d2c7fb65ab691530a802dafefd638596501854f267d0fb13c39e7'
            and value['three_license_sha256'] == '4c40a1ef62450b857c3b2aaf294936304cd552d965fbcd9d32d4c5bcf4ba4454'
            and re.fullmatch(r'[0-9a-f]{64}', value['engine_runtime_sha256']),
            'motion_runtime_version_mismatch')
        _require(re.fullmatch(r'[0-9a-f]{64}', value['creative_host_sha256']), 'motion_runtime_fingerprint_invalid')
    if value['schema'] == 'video.motion-runtime.v3':
        _require(value['hyperframes_core_sha256'] ==
            '675e02ee21f31540e3a6abaec061f86cea92a0178ae67a390700acb4f801dbbc'
            and re.fullmatch(r'[0-9a-f]{64}', value['hyperframes_project_host_sha256']),
            'motion_runtime_version_mismatch')
    # The renderer independently checks installed binaries against this complete
    # manifest; matching field names alone is not proof of the running image.
    return json.loads(raw)


def _audio(candidate):
    _require(type(candidate) is dict and not candidate.get('stale'),
             'missing_or_stale_audio', 409)
    data = candidate.get('data')
    _require(type(data) is bytes and 0 < len(data) <= media.MAX_AUDIO_BYTES)
    info = inspect_wav(data)
    checksum = hashlib.sha256(data).hexdigest()
    seconds = candidate.get('duration_seconds')
    _require(type(seconds) in (float, int) and 0 < seconds <= 300 and math.isfinite(seconds)
             and abs(seconds - info.duration_seconds) <= 1 / info.sample_rate
             and candidate.get('sha256') == checksum)
    identity = media._text(candidate.get('id'), 100)
    spoken = candidate.get('spoken_text')
    if spoken is None:
        _require(type(candidate.get('settings')) is dict and type(candidate.get('narration')) is str,
                 'motion_audio_text_missing', 409)
        settings = candidate['settings']
        _require(type(settings.get('model')) is str and type(settings.get('voice')) is str,
                 'motion_audio_text_missing', 409)
        document = narration_document(dict(narration=candidate['narration'], speech_settings=settings),
                                      dict(model=settings['model'], voice=settings['voice']))
        _require(candidate.get('input_sha256') == narration_binding(document),
                 'motion_audio_text_binding_mismatch', 409)
        spoken = document['spoken_text']
    media._text(spoken, 10000)
    return dict(id=identity, sha256=checksum, duration_seconds=info.duration_seconds,
                sample_rate=info.sample_rate, channels=info.channels, sample_width=info.sample_width,
                spoken_text=spoken, data_base64=base64.b64encode(data).decode('ascii'))


def freeze_snapshot_v2(project_id, revision, title, scenes, *, runtime_fingerprint,
                       purpose='draft', quality='draft', subtitles=False, current_adoptions=None):
    """Freeze exact selected candidates; playback never changes adopted heads.

    Each input has id/title/narration/audio, visual_mode=static|motion. Static
    inputs use the original visual dict. Motion inputs supply motion={id,plan,
    beats,alignment}. Drafts may select any current candidate; deliveries must
    match current_adoptions[scene_id]={visual_mode,visual_version_id,
    audio_version_id} and have reviewed motion timing. No fake approval booleans
    or latest-result selection are accepted.
    """
    media._text(project_id, 100)
    media._text(title, 300)
    _require(type(revision) is int and revision >= 1 and type(purpose) is str and purpose in {'draft', 'delivery'}
             and type(quality) is str and quality in {'draft', 'final'} and type(subtitles) is bool)
    _require(type(scenes) is list and 1 <= len(scenes) <= media.MAX_SCENES)
    fingerprint = _runtime(runtime_fingerprint)
    frozen, seen, voices, elapsed, audio_bytes = [], set(), set(), 0.0, 0
    _require(purpose != 'delivery' or type(current_adoptions) is dict,
             'motion_adoption_evidence_missing', 409)
    for source in scenes:
        _require(type(source) is dict and type(source.get('visual_mode')) is str
                 and source['visual_mode'] in {'static', 'motion'})
        identity = media._text(source.get('id'), 100)
        _require(identity not in seen)
        seen.add(identity)
        mode = source['visual_mode']
        if mode == 'static':
            legacy = media.freeze_snapshot(project_id, revision, title, [source],
                                           silent=False, subtitles=subtitles)
            scene = legacy['scenes'][0]
            scene['visual_mode'] = 'static'
            selected_renderer = source.get('static_renderer', legacy['static_renderer'])
            _require(type(selected_renderer) is str and selected_renderer in {'chromium', 'weasyprint'})
            scene['static_renderer'] = selected_renderer
            visual_id = scene['visual']['id']
            duration = scene['timing']['duration_seconds']
        else:
            audio = _audio(source.get('audio'))
            motion = source.get('motion')
            _require(type(motion) is dict and not motion.get('stale'),
                     'motion_plan_stale', 409)
            visual_id = media._text(motion.get('id'), 100)
            beats, plan = motion.get('beats'), motion.get('plan')
            alignment = validate_alignment(motion.get('alignment'), beats)
            _require(beats['source_text'] == source.get('narration'), 'motion_semantics_stale', 409)
            _require(alignment['audio_sha256'] == audio['sha256']
                     and beats['spoken_text'] == audio['spoken_text']
                     and abs(alignment['audio_seconds'] - audio['duration_seconds']) <= 1 / audio['sample_rate'],
                     'motion_audio_binding_mismatch', 409)
            _require(purpose != 'delivery' or alignment['review_status'] == 'verified',
                     'motion_timing_needs_review', 409)
            options = dict(leading_seconds=source.get('leading_seconds', 0),
                           trailing_seconds=source.get('trailing_seconds', 0))
            # New candidates have no frozen timeline and use the current
            # compiler. Rebuilding immutable inputs must retain their version;
            # replacing it with the new default would silently alter old bytes.
            if 'timeline' in motion:
                _require(type(motion['timeline']) is dict, 'motion_snapshot_corrupt', 409)
                resolver, compiler, version = scene_compiler(plan,
                    compiler_version=motion['timeline'].get('compiler'))
            elif 'compiler_version' in motion:
                resolver, compiler, version = scene_compiler(plan,
                    compiler_version=motion['compiler_version'])
            else:
                resolver, compiler, version = scene_compiler(plan)
            options['compiler_version'] = version
            timeline = resolver(plan, beats, alignment, **options)
            project = compiler(plan, beats, alignment, runtime_fingerprint=fingerprint, **options)
            duration = timeline['duration_seconds']
            captions = dict(audio_sha256=audio['sha256'], alignment='none', requires_review=False, cues=[])
            if subtitles:
                supplied = source.get('captions')
                _require(type(supplied) is dict and supplied.get('audio_sha256') == audio['sha256']
                         and not supplied.get('stale') and not supplied.get('timing_stale'), 'stale_captions', 409)
                captions = media.validate_captions(supplied.get('cues'), duration, audio['sha256'],
                    alignment=supplied.get('alignment', 'manual'), leading_seconds=options['leading_seconds'],
                    audio_seconds=audio['duration_seconds'])
            scene = dict(id=identity, title=media._text(source.get('title', ''), 300, empty=True),
                narration=source['narration'], visual_mode='motion', audio=audio, captions=captions,
                motion=dict(id=visual_id, plan=deepcopy(plan), beats=deepcopy(beats), alignment=alignment,
                    timeline=timeline, semantic_sha256=semantic_digest(beats),
                    project_files_sha256={name: hashlib.sha256(data).hexdigest() for name, data in sorted(project.items())}),
                timing=dict(duration_seconds=duration, leading_seconds=options['leading_seconds'],
                            trailing_seconds=options['trailing_seconds']))
        # Provider-generated narration must retain one project voice. Uploaded
        # recordings remain explicitly uploaded; their audible voice is unknown.
        candidate = source['audio']
        for key in ('origin', 'model', 'voice'):
            if key in candidate:
                scene['audio'][key] = media._text(candidate[key], 200)
        if candidate.get('origin') == 'tts':
            voices.add((candidate.get('model'), candidate.get('voice')))
            _require(None not in next(iter(voices)) and len(voices) <= 1, 'motion_project_voice_conflict', 409)
        audio_bytes += len(base64.b64decode(scene['audio']['data_base64'], validate=True))
        _require(audio_bytes <= media.MAX_SNAPSHOT_AUDIO_BYTES, 'media_project_too_large', 413)
        if purpose == 'delivery':
            _require(current_adoptions.get(identity) == dict(visual_mode=mode, visual_version_id=visual_id,
                         audio_version_id=scene['audio']['id']), 'motion_adoption_conflict', 409)
        scene.update(start_seconds=elapsed, end_seconds=elapsed + duration)
        elapsed += duration
        _require(elapsed <= media.MAX_PROJECT_SECONDS, 'media_project_too_long')
        frozen.append(scene)
    snapshot = dict(schema=SCHEMA, project_id=project_id, project_revision=revision, title=title,
        purpose=purpose, quality=quality, silent=False, subtitles=subtitles, visual_mode='mixed',
        width=media.WIDTH, height=media.HEIGHT, fps=media.FPS, runtime_fingerprint=fingerprint,
        duration_seconds=elapsed, scenes=frozen)
    snapshot['sha256'] = digest(snapshot)
    return snapshot


def verify_snapshot_v2(snapshot):
    """Rebuild a frozen package to reject both hash and semantic corruption.

    This validates the original immutable delivery, not freshness against later
    project edits. That is checked only when creating a new delivery snapshot.
    """
    _require(type(snapshot) is dict and snapshot.get('schema') == SCHEMA)
    try:
        _require(snapshot.get('sha256') == digest({k: v for k, v in snapshot.items() if k != 'sha256'}),
                 'motion_snapshot_corrupt', 409)
        inputs, adopted = [], {}
        for scene in snapshot['scenes']:
            audio = dict(scene['audio'])
            audio['data'] = base64.b64decode(audio.pop('data_base64'), validate=True)
            item = dict(id=scene['id'], title=scene['title'], narration=scene['narration'],
                        visual_mode=scene['visual_mode'], audio=audio, captions=scene['captions'])
            for key in ('leading_seconds', 'trailing_seconds'):
                item[key] = scene['timing'][key]
            if scene['visual_mode'] == 'static':
                item['visual'] = scene['visual']
                item['duration_seconds'] = scene['timing']['configured_seconds']
                visual_id = scene['visual']['id']
                item['static_renderer'] = scene['static_renderer']
            else:
                item['motion'] = scene['motion']
                visual_id = scene['motion']['id']
            adopted[scene['id']] = dict(visual_mode=scene['visual_mode'], visual_version_id=visual_id,
                                       audio_version_id=audio['id'])
            inputs.append(item)
        rebuilt = freeze_snapshot_v2(snapshot['project_id'], snapshot['project_revision'], snapshot['title'], inputs,
            runtime_fingerprint=snapshot['runtime_fingerprint'], purpose=snapshot['purpose'],
            quality=snapshot['quality'], subtitles=snapshot['subtitles'], current_adoptions=adopted)
        _require(rebuilt == snapshot, 'motion_snapshot_corrupt', 409)
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ServiceError('motion_snapshot_corrupt', 409) from None
    return snapshot
