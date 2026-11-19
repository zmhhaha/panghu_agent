"""Creative runtime facts and method selection, without prescribing a scene.

The numbers describe the shared isolated browser renderer's configured policy,
not measured speed or spare cluster capacity. Keep this in the immutable input
document so a creator can reason about cost before writing its own source.
"""
from __future__ import annotations


def learning_execution_guidance():
    """Return fresh input data for the picture/video learning creator.

    Call while constructing a new operation document. Do not retrofit it into
    a historical operation that is being replayed by its original input hash.
    This guidance never adds a capability or changes a developer's selection.
    """
    return dict(
        schema='video.learning-execution-guidance.v1',
        objective=(
            'Achieve the supplied visual target and criteria. Technology is a means, '
            'not an acceptance criterion unless the developer explicitly requires it. '
            'Among implementations with comparable observed visual quality, prefer '
            'lower measured render cost and more reliable complete export.'),
        environment=dict(
            evidence_scope='configured_renderer_policy_not_a_performance_measurement',
            browser_renderer=dict(cpu_limit=2, memory_limit_bytes=1073741824,
                gpu_acceleration=False, three_webgl_backend='ANGLE/SwiftShader on CPU'),
            notes=[
                'These limits apply to the isolated browser render, not to all producers or the whole cluster.',
                'Do not assume a hardware GPU, GPU-like throughput, spare CPU capacity, or a measured frame time.',
                'Offline export renders individual frames; real-time playback speed is not required. '
                'Each frame and the entire export must still fit the host operation deadline.',
            ]),
        capability_boundaries=[
            'Use only constraints.allowed_engines, the supplied local assets, and resources actually offered by the host.',
            'Respect constraints.capability_selection and its guidance. Multiple selected capabilities '
            'permit selection or combination according to that contract; they do not require decorative use of every tool.',
            'Preserve an explicitly required drawing capability, motion tool, key effect, output size, '
            'duration, absolute clock, and learning target. If no compliant implementation fits the budget, '
            'report the limitation instead of silently substituting a forbidden technique or weakening the goal.',
            'Custom shaders are optional. Their availability does not require their use, and '
            'Three.js can use ordinary geometry and built-in materials without a custom ray-marching shader.',
        ],
        technique_roles=[
            dict(role='drawing', capabilities=['HTML/SVG', 'Canvas 2D', 'Three.js', 'Manim'],
                note='These are alternatives or composable capabilities only when admitted. Manim '
                     'requires an offered production plan and an actual prepared resource; do not invent one.'),
            dict(role='motion_authoring', capabilities=['native absolute-time state', 'GSAP', 'Anime.js'],
                note='Use only the admitted and selected motion tools through their supplied local interfaces. '
                     'They organize motion rather than replace drawing capabilities.'),
            dict(role='composition_and_export', capabilities=['HyperFrames'],
                note='Keep the host seek/readiness interface and deterministic absolute-time composition '
                     'regardless of the chosen drawing implementation.'),
        ],
        cost_reasoning=[
            'Choose from the visual requirements, not from a preference for a more elaborate engine. '
            'Use HTML/SVG for suitable explicit shapes and paths, Canvas for suitable painted/composited '
            'surfaces, or Three.js where informative geometry, perspective and occlusion help. '
            'These examples are choices, not a mandatory visual recipe or a ranking of quality.',
            'On CPU WebGL, consider output pixel count, per-pixel shader loops, noise octaves, '
            'secondary shadow/density samples, overdraw, texture copies, geometry and initialization cost together. '
            'A visually rich result does not automatically require full-screen volumetric ray marching.',
            'Where the target permits, consider reusable geometry, finite caches, prepared textures, '
            'bounded shader samples, layered compositing or lower internal sampling resolution. '
            'Preserve the required final output size and judge the visible result; do not trade away the goal merely to pass.',
            'Use host-supplied probe timings and actual failure evidence when available. '
            'Do not claim to have benchmarked, rendered, watched, or improved a result from source inspection alone.',
        ],
        failure_recovery=[
            'A technical rerun of unchanged source is useful for a transient failure. '
            'A repeated source, initialization or performance failure needs implementation repair, not unlimited reruns.',
            'For an authorized repair, preserve the frozen goal, criteria, reference observations, '
            'knowledge binding and explicit capability constraints. Revise your own implementation using '
            'the host-classified facts, and change the technique only within those constraints.',
            'Remain within the host-provided attempt/time budget. Do not start additional experiments, '
            'request unoffered resources, publish knowledge or claim visual acceptance as part of a repair.',
            'A repaired source is a new candidate, not proof that the failure or visual quality has improved. '
            'Keep the last successful candidate and unresolved limitations available for comparison.',
        ])
