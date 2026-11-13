"""Deterministic input adapter; generated code is executed only in isolated Jobs.

This creates the existing bounded seven-file Job envelope. It does not turn
untrusted scene code into trusted compiler code or certify visual quality.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math

from agent_service.contracts import ServiceError, canonical_bytes, digest
from .creative_contract import validate_scene_package, scene_context, scene_source
from .motion import semantic_digest, validate_alignment

COMPILER = 'creative-scene-adapter-1'
TEMPLATE = 'generated-html-svg-1'
METHOD_COMPILER = 'creative-method-scene-adapter-1'
METHOD_TEMPLATE = 'generated-method-html-svg-1'
ENGINE_METHOD_COMPILER = 'creative-method-engine-scene-adapter-1'
ENGINE_METHOD_TEMPLATE = 'generated-method-engine-scene-1'
FILM_COMPILER = 'creative-film-scene-adapter-1'
FILM_TEMPLATE = 'generated-film-engine-scene-1'
SKILL_COMPILER = 'creative-skill-film-scene-adapter-1'
SKILL_TEMPLATE = 'generated-skill-film-engine-scene-1'
COMPILER_TEMPLATES = {COMPILER: TEMPLATE, METHOD_COMPILER: METHOD_TEMPLATE,
                      ENGINE_METHOD_COMPILER: ENGINE_METHOD_TEMPLATE,
                      FILM_COMPILER: FILM_TEMPLATE, SKILL_COMPILER: SKILL_TEMPLATE}


def _compiler(package, requested):
    expected = {'video.creative-scene.v1': COMPILER, 'video.creative-scene.v2': METHOD_COMPILER,
                'video.creative-scene.v3': ENGINE_METHOD_COMPILER,
                'video.creative-scene.v4': FILM_COMPILER,
                'video.creative-scene.v5': SKILL_COMPILER}[package['schema']]
    _require(requested is None or requested == expected, 'creative_renderer_version_unsupported')
    return expected


def _require(condition, code='creative_render_binding_mismatch'):
    if not condition:
        raise ServiceError(code, 409)


def resolve_creative_timeline(package, beats, alignment, *, leading_seconds=0,
                              trailing_seconds=0, compiler_version=None):
    package = validate_scene_package(package)
    compiler_version = _compiler(package, compiler_version)
    context = scene_context(package)
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
    creative = dict(engine=package['engine'], input_sha256=package['input_sha256'],
        result_sha256=package['result_sha256'],
        seek_protocol='window.__animationExperiment.seek(seconds)',
        renderer='isolated-chromium-engine-seek-ffmpeg-1' if package['schema'] in {'video.creative-scene.v3', 'video.creative-scene.v4', 'video.creative-scene.v5'}
            else 'isolated-chromium-absolute-seek-ffmpeg-1')
    if package['schema'] == 'video.creative-scene.v5':
        creative.update(skill_catalog_sha256=package['skill_catalog_sha256'],
            skill_delivery_sha256=package['skill_delivery_sha256'],
            skill_decisions_sha256=digest(package['skill_decisions']),
            skill_tool_evidence_sha256=package['skill_tool_evidence_sha256'],
            skill_evidence_scope=package['evidence_scope'])
    elif package['schema'] in {'video.creative-scene.v2', 'video.creative-scene.v3', 'video.creative-scene.v4'}:
        creative.update(method_delivery_sha256=package['method_delivery_sha256'],
            method_decision_sha256=digest(package['method_decision']),
            method_evidence_scope=package['evidence_scope'])
    else:
        creative['capability_release_sha256'] = package['capability_release_sha256']
    if package['schema'] in {'video.creative-scene.v4', 'video.creative-scene.v5'}:
        film = package['input_document']['film_context']
        row = film['film']['scenes'][film['current_scene_index']]
        _require(abs(row['end_seconds'] - row['start_seconds'] - seconds) <= 1e-6
            and abs(row['leading_silence_seconds'] - leading_seconds) <= 1e-9
            and abs(row['trailing_silence_seconds'] - (seconds - leading_seconds - alignment['audio_seconds'])) <= 1e-6,
            'creative_film_clock_mismatch')
        predecessor = film['predecessor']
        creative.update(film_context_sha256=digest(film), film_sha256=film['film_sha256'],
            handoff_sha256=digest(package['result']['handoff']),
            predecessor_motion_version_id=predecessor['motion_version_id'] if predecessor else None)
    return dict(schema='video.motion-timeline.v1', compiler=compiler_version,
        plan_sha256=digest(package), semantic_sha256=semantic_digest(beats),
        alignment_sha256=digest(alignment), audio_sha256=alignment['audio_sha256'],
        spoken_sha256=alignment['spoken_sha256'], audio_seconds=alignment['audio_seconds'],
        leading_seconds=leading_seconds, trailing_seconds=trailing_seconds,
        duration_seconds=seconds, alignment_method=alignment['method'],
        review_status=alignment['review_status'],
        beats=[dict(row, start=leading_seconds + row['start'], end=leading_seconds + row['end'])
               for row in alignment['segments']], actions=[],
        creative=creative)


def compile_creative_project(package, beats, alignment, *, runtime_fingerprint,
                             leading_seconds=0, trailing_seconds=0,
                             compiler_version=None, animated=True):
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
    engine_aware = value['schema'] in {'video.creative-scene.v3', 'video.creative-scene.v4', 'video.creative-scene.v5'}
    if engine_aware:
        # v3 identifies the measured image/core/host bytes. This route still
        # uses native seek; accepting those identities is no HF-use claim.
        runtime_schemas = ({'video.motion-runtime.v2', 'video.motion-runtime.v3'}
                           if value['schema'] == 'video.creative-scene.v5' else {'video.motion-runtime.v2'})
        _require(runtime_fingerprint is not None and runtime_fingerprint['schema'] in runtime_schemas,
                 'creative_engine_runtime_required')
        identity = value['input_document']['constraints'].get('runtime_identity')
        if identity is not None:
            _require(identity['image_digest'] == runtime_fingerprint['image_digest']
                and identity['engine_runtime_sha256'] == runtime_fingerprint['engine_runtime_sha256']
                and identity['host_sha256'] == runtime_fingerprint['creative_host_sha256'],
                'creative_engine_runtime_identity_mismatch')
        if value['engine'] == 'three':
            _require(value['input_document']['assets'].get('three.module.min.js') == runtime_fingerprint['three_sha256'],
                     'creative_render_asset_mismatch')
    files = {'index.html': scene_source(value)['files']['scene.html'].encode('utf-8'),
        'plan.json': canonical_bytes(value), 'beats.json': canonical_bytes(beats),
        'alignment.json': canonical_bytes(alignment), 'timeline.json': canonical_bytes(timeline)}
    compiler = timeline['compiler']
    manifest = dict(schema='video.motion-project.v1', compiler=compiler, template=COMPILER_TEMPLATES[compiler],
        hyperframes='0.8.149', gsap='3.14.2', width=1280, height=720, fps=24, animated=True,
        plan_sha256=digest(value), semantic_sha256=semantic_digest(beats),
        timeline_sha256=digest(timeline), audio_sha256=alignment['audio_sha256'],
        runtime_fingerprint=deepcopy(runtime_fingerprint),
        files={name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(files.items())},
        required_assets=['assets/gsap.min.js', 'assets/NotoSansCJK-Regular.ttc'])
    if engine_aware:
        manifest.update(engine=value['engine'], engine_assets={
            'three.module.min.js': runtime_fingerprint['three_sha256']} if value['engine'] == 'three' else {})
        manifest['required_assets'] += ['assets/' + name for name in manifest['engine_assets']]
    files['manifest.json'] = canonical_bytes(manifest)
    return files
