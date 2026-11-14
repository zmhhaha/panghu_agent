"""Explicit preparation status; source code is not live runtime admission.

No effect-library engine is silently expanded by importing this module.
"""
from .manim_resource import MANIM_VERSION, FPS, MAX_FRAMES, MAX_RESOURCE_BYTES, MAX_DECODED_BYTES


def capability_status():
    return dict(capability='manim-community', version=MANIM_VERSION,
        category='offline-frame-resource-authoring', browser_engine=None,
        status='prepared_not_admitted', enabled=False,
        output=dict(format='png-rgba-sequence', fps=FPS,
            max_frames=MAX_FRAMES, max_bytes=MAX_RESOURCE_BYTES, max_decoded_bytes=MAX_DECODED_BYTES),
        requires=['separate_recipe_author_contract', 'isolated_cairo_runtime_identity',
            'stop_proof_before_resource_collection', 'generated_asset_transport',
            'whole_project_seek_and_render_proof', 'developer_skill_publication'],
        latex_available=False, independent_video_concatenation=False)
