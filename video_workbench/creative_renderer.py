"""Deterministic input adapter; generated code is executed only in isolated Jobs.

This creates the existing bounded seven-file Job envelope. It does not turn
untrusted scene code into trusted compiler code or certify visual quality.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math

from agent_service.contracts import ServiceError, canonical_bytes, digest
from .creative_contract import validate_scene_package
from .motion import semantic_digest, validate_alignment

COMPILER = 'creative-scene-adapter-1'
TEMPLATE = 'generated-html-svg-1'


def _require(condition, code='creative_render_binding_mismatch'):
    if not condition:
        raise ServiceError(code, 409)


def resolve_creative_timeline(package, beats, alignment, *, leading_seconds=0,
                              trailing_seconds=0, compiler_version=COMPILER):
    package = validate_scene_package(package)
    _require(compiler_version == COMPILER, 'creative_renderer_version_unsupported')
    context = package['input_document']['constraints']['production']
    alignment = validate_alignment(alignment, beats)
    _require(beats == context['beats'])
    # A review may change status; editing the actual authored clock requires a
    # new creative candidate rather than stretching already generated code.
    review_fields = {'review_status', 'reviewed_by', 'reviewed_at'}
    _require({k: v for k, v in alignment.items() if k not in review_fields} ==
             {k: v for k, v in context['alignment'].items() if k not in review_fields})
    _require(all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 10
                 for v in (leading_seconds, trailing_seconds)))
    seconds = math.ceil((leading_seconds + alignment['audio_seconds'] + trailing_seconds) * 24) / 24
    _require(0 < seconds <= 60, 'motion_scene_duration_exceeded')
    return dict(schema='video.motion-timeline.v1', compiler=COMPILER,
        plan_sha256=digest(package), semantic_sha256=semantic_digest(beats),
        alignment_sha256=digest(alignment), audio_sha256=alignment['audio_sha256'],
        spoken_sha256=alignment['spoken_sha256'], audio_seconds=alignment['audio_seconds'],
        leading_seconds=leading_seconds, trailing_seconds=trailing_seconds,
        duration_seconds=seconds, alignment_method=alignment['method'],
        review_status=alignment['review_status'],
        beats=[dict(row, start=leading_seconds + row['start'], end=leading_seconds + row['end'])
               for row in alignment['segments']], actions=[],
        creative=dict(engine=package['engine'], input_sha256=package['input_sha256'],
            result_sha256=package['result_sha256'],
            capability_release_sha256=package['capability_release_sha256'],
            seek_protocol='window.__animationExperiment.seek(seconds)',
            renderer='isolated-chromium-absolute-seek-ffmpeg-1'))


def compile_creative_project(package, beats, alignment, *, runtime_fingerprint,
                             leading_seconds=0, trailing_seconds=0,
                             compiler_version=COMPILER, animated=True):
    _require(animated is True, 'creative_static_render_not_supported')
    value = validate_scene_package(package)
    timeline = resolve_creative_timeline(value, beats, alignment,
        leading_seconds=leading_seconds, trailing_seconds=trailing_seconds,
        compiler_version=compiler_version)
    if runtime_fingerprint is not None:
        from .motion_snapshot import _runtime
        runtime_fingerprint = _runtime(runtime_fingerprint)
        declared = value['input_document']['assets']
        _require(all(declared.get(name, runtime_fingerprint[key]) == runtime_fingerprint[key]
            for name, key in (('gsap.min.js', 'gsap_sha256'),
                              ('NotoSansCJK-Regular.ttc', 'font_sha256'))),
            'creative_render_asset_mismatch')
    files = {'index.html': value['result']['files']['scene.html'].encode('utf-8'),
        'plan.json': canonical_bytes(value), 'beats.json': canonical_bytes(beats),
        'alignment.json': canonical_bytes(alignment), 'timeline.json': canonical_bytes(timeline)}
    manifest = dict(schema='video.motion-project.v1', compiler=COMPILER, template=TEMPLATE,
        hyperframes='0.8.149', gsap='3.14.2', width=1280, height=720, fps=24, animated=True,
        plan_sha256=digest(value), semantic_sha256=semantic_digest(beats),
        timeline_sha256=digest(timeline), audio_sha256=alignment['audio_sha256'],
        runtime_fingerprint=deepcopy(runtime_fingerprint),
        files={name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(files.items())},
        required_assets=['assets/gsap.min.js', 'assets/NotoSansCJK-Regular.ttc'])
    files['manifest.json'] = canonical_bytes(manifest)
    return files
