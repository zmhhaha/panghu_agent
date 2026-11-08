"""Read-only provenance for frozen renders, including pre-existing history."""
from __future__ import annotations


def render_metadata(snapshot, project, alignments=None, *, end_hold_seconds=0):
    current = {scene['id']: scene for scene in project['scenes']}
    sources, changed = [], False
    for frozen in snapshot['scenes']:
        scene = current.get(frozen['id'])
        mode = frozen.get('visual_mode', 'static')
        dynamic = mode in {'motion', 'creative'}
        # Creative candidates share the scene's motion head, but their frozen
        # source and authored clock have a separate envelope and mode name.
        visual_input = frozen.get(mode if dynamic else 'visual') or {}
        visual_id = visual_input.get('id')
        audio_id = (frozen.get('audio') or {}).get('id')
        visual_versions = ((scene or {}).get('motion', {}).get('versions', [])
                           if dynamic else (scene or {}).get('versions', []))
        visual = next((item for item in visual_versions if item['id'] == visual_id), None)
        audio = next((item for item in (scene or {}).get('audio_versions', []) if item['id'] == audio_id), None)
        # Candidate availability is about this scene's actual inputs, not the
        # project counter or whether another scene has gained a new candidate.
        stale = (scene is None or frozen.get('narration') != scene.get('narration')
                 or visual is None or visual.get('stale', False)
                 or bool(audio_id and (audio is None or audio.get('stale', False))))
        if scene:
            timing = frozen.get('continuity_base_timing', frozen.get('timing')) or {}
            expected_trailing = max(scene.get('trailing_seconds', 0), end_hold_seconds) if frozen is snapshot['scenes'][-1] else scene.get('trailing_seconds', 0)
            stale = stale or float(timing.get('leading_seconds', 0)) != float(scene.get('leading_seconds', 0))
            stale = stale or float(timing.get('trailing_seconds', 0)) != float(expected_trailing)
            if mode == 'static':
                stale = stale or float(timing.get('configured_seconds', scene['duration_seconds'])) != float(scene['duration_seconds'])
            else:
                saved = (alignments or {}).get((visual_id, audio_id))
                if saved is not None:
                    # Review alone does not change pixels; changed anchors do.
                    stale = stale or saved['segments'] != visual_input.get('alignment', {}).get('segments')
            if snapshot.get('subtitles') and scene.get('captions'):
                stale = stale or (scene['captions'].get('stale', False)
                    or scene['captions'].get('timing_stale', False)
                    or scene['captions'].get('cues') != frozen.get('captions', {}).get('cues'))
            if snapshot.get('purpose') == 'delivery':
                head = scene.get('motion', {}).get('head_version_id') if dynamic else scene.get('head_version_id')
                stale = stale or head != visual_id or scene.get('head_audio_version_id') != audio_id
                if scene.get('motion'):
                    stale = stale or scene['motion']['mode'] != ('motion' if mode == 'creative' else mode)
        changed = changed or stale
        sources.append(dict(scene_id=frozen['id'], title=frozen.get('title', ''),
            visual_mode=mode, visual_version_id=visual_id, audio_version_id=audio_id,
            visual_version_number=(visual or {}).get('number') or
                (next((i + 1 for i, item in enumerate(visual_versions) if item['id'] == visual_id), None)),
            audio_version_number=(audio or {}).get('number')))
    continuity = snapshot.get('continuity')
    if continuity and snapshot.get('purpose') == 'delivery':
        changed = changed or project.get('continuity_head_version_id') != continuity['version_id']
    return dict(scenes=sources, width=snapshot.get('width'), height=snapshot.get('height'),
                fps=snapshot.get('fps'), duration_seconds=snapshot.get('duration_seconds'),
                content_state='changed' if changed else 'current', **(
                    {'continuity_current': project.get('continuity_head_version_id') == continuity['version_id']}
                    if continuity else {}))
