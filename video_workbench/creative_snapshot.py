"""Immutable mixed production snapshots admitting generated creative source.

Historical v2/v3 freeze and verification remain unchanged. Every generated
scene is bound to its exact narration recording, source package and adapter.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json

from agent_service.contracts import ServiceError, digest
from . import media
from .creative_contract import validate_scene_package, scene_context, scene_source
from .motion import validate_alignment
from .motion_snapshot import _audio, _runtime, freeze_snapshot_v2


SCHEMA = 'video.render-snapshot.v4'


def _require(condition, code='invalid_creative_snapshot', status=422):
    if not condition:
        raise ServiceError(code, status)


def _film_chain(project_id, scenes):
    """A new full-film contract cannot borrow an adjacent but unrelated cut."""
    packages = [(source['creative'].get('package') if type(source) is dict
                 and type(source.get('creative')) is dict else None) for source in scenes]
    if not any(type(value) is dict and value.get('schema') == 'video.creative-scene.v4' for value in packages):
        return None
    checked = []
    for source, package in zip(scenes, packages):
        _require(type(source) is dict and source.get('visual_mode') == 'creative' and type(package) is dict
            and package.get('schema') == 'video.creative-scene.v4', 'creative_film_chain_mismatch', 409)
        checked.append(validate_scene_package(package))
    film = checked[0]['input_document']['film_context']['film']
    checksum = digest(film)
    _require(film['project_id'] == project_id and [row['scene_id'] for row in film['scenes']]
        == [source.get('id') for source in scenes], 'creative_film_chain_mismatch', 409)
    for index, (source, package, row) in enumerate(zip(scenes, checked, film['scenes'])):
        context = package['input_document']['film_context']
        _require(context['film'] == film and context['film_sha256'] == checksum
            and context['current_scene_index'] == index
            and type(source.get('audio')) is dict
            and source['audio'].get('id') == row['audio_version_id']
            and source['audio'].get('sha256') == row['audio_sha256'], 'creative_film_chain_mismatch', 409)
        if index:
            previous = checked[index - 1]
            previous_source = scene_source(previous)
            expected = dict(scene_id=scenes[index - 1]['id'],
                motion_version_id=scenes[index - 1]['creative']['id'],
                input_sha256=previous['input_sha256'], result_sha256=previous['result_sha256'],
                source_sha256=digest(previous_source),
                source_summary={key: previous_source[key] for key in ('engine', 'strategy', 'files_sha256')},
                handoff=previous['result']['handoff'])
            _require(context['predecessor'] == expected, 'creative_film_predecessor_mismatch', 409)
    return film


def freeze_snapshot_v4(project_id, revision, title, scenes, *, runtime_fingerprint,
                       purpose='draft', quality='draft', subtitles=False, current_adoptions=None):
    media._text(project_id, 100)
    media._text(title, 300)
    _require(type(revision) is int and revision >= 1 and type(purpose) is str and purpose in {'draft', 'delivery'}
             and type(quality) is str and quality in {'draft', 'final'} and type(subtitles) is bool)
    _require(type(scenes) is list and 1 <= len(scenes) <= media.MAX_SCENES)
    _require(purpose != 'delivery' or type(current_adoptions) is dict,
             'motion_adoption_evidence_missing', 409)
    fingerprint = _runtime(runtime_fingerprint)
    film = _film_chain(project_id, scenes)
    result, seen, voices, cursor, audio_bytes = [], set(), set(), 0.0, 0
    for source in scenes:
        _require(type(source) is dict and type(source.get('visual_mode')) is str
                 and source['visual_mode'] in {'static', 'motion', 'creative'})
        identity = media._text(source.get('id'), 100)
        _require(identity not in seen)
        seen.add(identity)
        if source['visual_mode'] != 'creative':
            original = freeze_snapshot_v2(project_id, revision, title, [source],
                runtime_fingerprint=fingerprint, purpose=purpose, quality=quality,
                subtitles=subtitles, current_adoptions=current_adoptions)
            scene = original['scenes'][0]
            visual_id = scene['motion']['id'] if scene['visual_mode'] == 'motion' else scene['visual']['id']
        else:
            from .creative_renderer import compile_creative_project
            audio = _audio(source.get('audio'))
            creative = source.get('creative')
            _require(type(creative) is dict and not creative.get('stale'), 'motion_plan_stale', 409)
            visual_id = media._text(creative.get('id'), 100)
            package = validate_scene_package(creative.get('package'))
            context = scene_context(package)
            beats = deepcopy(creative.get('beats'))
            alignment = validate_alignment(creative.get('alignment'), beats)
            _require(context['scene_id'] == identity and context['narration'] == source.get('narration')
                     and context['title'] == source.get('title') and context['beats'] == beats,
                     'creative_scene_semantics_stale', 409)
            _require(context['audio_sha256'] == audio['sha256']
                     and beats['spoken_text'] == audio['spoken_text']
                     and abs(context['alignment']['audio_seconds'] - audio['duration_seconds']) <= 1 / audio['sample_rate']
                     and alignment['audio_sha256'] == audio['sha256'],
                     'creative_audio_binding_mismatch', 409)
            # Review metadata can be added after preview; source anchor changes
            # require generation again, because source already owns those times.
            frozen_alignment = context['alignment']
            review_fields = {'review_status', 'reviewed_by', 'reviewed_at'}
            _require({k: v for k, v in alignment.items() if k not in review_fields}
                     == {k: v for k, v in frozen_alignment.items() if k not in review_fields},
                     'creative_timing_requires_regeneration', 409)
            _require(purpose != 'delivery' or alignment['review_status'] == 'verified',
                     'motion_timing_needs_review', 409)
            options = dict(leading_seconds=source.get('leading_seconds', 0),
                           trailing_seconds=source.get('trailing_seconds', 0))
            if 'timeline' in creative:
                options['compiler_version'] = creative['timeline']['compiler']
            elif 'compiler_version' in creative:
                options['compiler_version'] = creative['compiler_version']
            files = compile_creative_project(package, beats, alignment,
                runtime_fingerprint=fingerprint, **options)
            timeline = json.loads(files['timeline.json'])
            duration = timeline['duration_seconds']
            _require(0 < duration <= 60, 'motion_scene_duration_exceeded', 422)
            captions = dict(audio_sha256=audio['sha256'], alignment='none', requires_review=False, cues=[])
            if subtitles:
                supplied = source.get('captions')
                _require(type(supplied) is dict and supplied.get('audio_sha256') == audio['sha256']
                         and not supplied.get('stale') and not supplied.get('timing_stale'), 'stale_captions', 409)
                captions = media.validate_captions(supplied.get('cues'), duration, audio['sha256'],
                    alignment=supplied.get('alignment', 'manual'), leading_seconds=options['leading_seconds'],
                    audio_seconds=audio['duration_seconds'])
            scene = dict(id=identity, title=source['title'], narration=source['narration'], visual_mode='creative',
                audio=audio, captions=captions,
                creative=dict(id=visual_id, package=package, beats=beats, alignment=alignment, timeline=timeline,
                    project_files_sha256={name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(files.items())}),
                timing=dict(duration_seconds=duration, leading_seconds=options['leading_seconds'],
                            trailing_seconds=options['trailing_seconds']))
        for key in ('origin', 'model', 'voice'):
            if key in source['audio']:
                scene['audio'][key] = media._text(source['audio'][key], 200)
        if source['audio'].get('origin') == 'tts':
            voices.add((source['audio'].get('model'), source['audio'].get('voice')))
            _require(None not in next(iter(voices)) and len(voices) <= 1, 'motion_project_voice_conflict', 409)
        audio_bytes += len(base64.b64decode(scene['audio']['data_base64'], validate=True))
        _require(audio_bytes <= media.MAX_SNAPSHOT_AUDIO_BYTES, 'media_project_too_large', 413)
        if purpose == 'delivery':
            _require(current_adoptions.get(identity) == dict(visual_mode=source['visual_mode'],
                visual_version_id=visual_id, audio_version_id=scene['audio']['id']), 'motion_adoption_conflict', 409)
        duration = scene['timing']['duration_seconds']
        if film is not None:
            row = film['scenes'][len(result)]
            _require(abs(duration - (row['end_seconds'] - row['start_seconds'])) <= 1e-6
                and abs(cursor - row['start_seconds']) <= 1e-6,
                'creative_film_clock_mismatch', 409)
        scene.update(start_seconds=cursor, end_seconds=cursor + duration)
        cursor = scene['end_seconds']
        _require(cursor <= media.MAX_PROJECT_SECONDS, 'media_project_too_long')
        result.append(scene)
    snapshot = dict(schema=SCHEMA, project_id=project_id, project_revision=revision, title=title,
        purpose=purpose, quality=quality, silent=False, subtitles=subtitles, visual_mode='mixed',
        width=media.WIDTH, height=media.HEIGHT, fps=media.FPS, runtime_fingerprint=fingerprint,
        duration_seconds=cursor, scenes=result)
    snapshot['sha256'] = digest(snapshot)
    return snapshot


def verify_snapshot_v4(snapshot):
    _require(type(snapshot) is dict and snapshot.get('schema') == SCHEMA)
    try:
        _require(snapshot.get('sha256') == digest({k: v for k, v in snapshot.items() if k != 'sha256'}),
                 'creative_snapshot_corrupt', 409)
        sources, adopted = [], {}
        for scene in snapshot['scenes']:
            audio = dict(scene['audio'])
            audio['data'] = base64.b64decode(audio.pop('data_base64'), validate=True)
            item = dict(id=scene['id'], title=scene['title'], narration=scene['narration'],
                visual_mode=scene['visual_mode'], audio=audio, captions=scene['captions'],
                leading_seconds=scene['timing']['leading_seconds'], trailing_seconds=scene['timing']['trailing_seconds'])
            mode = scene['visual_mode']
            if mode == 'static':
                item.update(visual=scene['visual'], duration_seconds=scene['timing']['configured_seconds'],
                            static_renderer=scene['static_renderer'])
                visual_id = scene['visual']['id']
            else:
                key = 'creative' if mode == 'creative' else 'motion'
                item[key] = scene[key]
                visual_id = scene[key]['id']
            adopted[scene['id']] = dict(visual_mode=mode, visual_version_id=visual_id, audio_version_id=audio['id'])
            sources.append(item)
        rebuilt = freeze_snapshot_v4(snapshot['project_id'], snapshot['project_revision'], snapshot['title'], sources,
            runtime_fingerprint=snapshot['runtime_fingerprint'], purpose=snapshot['purpose'],
            quality=snapshot['quality'], subtitles=snapshot['subtitles'], current_adoptions=adopted)
        _require(rebuilt == snapshot, 'creative_snapshot_corrupt', 409)
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ServiceError('creative_snapshot_corrupt', 409) from None
    return snapshot
