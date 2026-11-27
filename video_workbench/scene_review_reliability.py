"""Server-owned visual requirements and byte provenance for new scene reviews."""
from copy import deepcopy
import hashlib
import io

from agent_service.contracts import ServiceError

GATE_SCHEMA = 'video.scene-classified-review-gate.v1'
EVIDENCE_SCHEMA = 'video.review-image-provenance.v1'
POLICY_SCHEMA = 'video.picture-review-policy.v1'
MAX_REVIEW_IMAGE_BYTES = 2 * 1024 * 1024


def default_review_policy():
    return dict(schema=POLICY_SCHEMA, evidence_scope='static_only', criteria=[
        dict(id='goal-and-style', requirement='required', text=
             'The actual supplied images satisfy the full frozen film goal, explicit content and requested visual style. '
             'A required stylistic failure is a violation, not an optional refinement.'),
        dict(id='visible-spatial-structure', requirement='required', text=
             'Visible objects retain the requested relative proportions, support, depth and opaque occlusion. '
             'Judge visible pixels only; do not claim source or unseen geometry was verified.'),
        dict(id='declared-light-interpretation', requirement='required', text=
             'Where light orientation is declared or visible, depicted highlights and shadows have a coherent '
             'interpretation appropriate to the requested style. Do not impose physical shading on all styles.'),
        dict(id='optional-refinement', requirement='optional', text=
             'Offer refinements that preserve already satisfied required content, structure and visual style. '
             'Suggestions are retained and do not themselves request automatic reauthoring.')])


def retain_png(raw, mime, *, expected_sha256=None):
    """Validate and preserve original lossless bytes; never relabel JPEG as PNG."""
    def require(ok, code='scene_space_lossless_review_image_invalid', status=409):
        if not ok: raise ServiceError(code, status)
    require(type(raw) is bytes and 0 < len(raw) <= 4*1024*1024 and mime == 'image/png')
    sha = hashlib.sha256(raw).hexdigest()
    require(expected_sha256 is None or sha == expected_sha256,
            'scene_space_preview_media_changed')
    from PIL import Image
    try:
        with Image.open(io.BytesIO(raw)) as image:
            require(image.format == 'PNG' and image.size == (1280,720))
            image.verify()
        with Image.open(io.BytesIO(raw)) as image: image.load()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise ServiceError('scene_space_lossless_review_image_invalid',409) from None
    return sha


def image_provenance(item, raw, *, source_sha256, plan_sha256, kind, render_id,frame_range):
    """Called only after the ordinary renderer/source/plan/clock checks."""
    sha = retain_png(raw, 'image/png', expected_sha256=item['artifact_sha256'])
    capture = deepcopy(item.get('capture'))
    if capture is None:
        if kind != 'static_preview':
            raise ServiceError('scene_space_review_capture_missing',409)
        capture = dict(schema='video.scene-space-frame-capture.v1', absolute_frame=item['frame'],
            relative_frame=0, format='png', quality=None, sha256=sha, bytes=len(raw))
    from .scene_space_capture import validate_frame_capture
    validate_frame_capture(capture,frame_range=frame_range,raw=raw)
    if (capture.get('absolute_frame') != item['frame'] or capture.get('format') != 'png'
            or capture.get('quality') is not None or capture.get('sha256') != sha
            or type(capture.get('bytes')) is not int or capture['bytes'] != len(raw)):
        raise ServiceError('scene_space_review_capture_changed',409)
    return dict(frame_id=item['identity'], absolute_frame=item['frame'], kind=kind,
        render_id=render_id, source_sha256=source_sha256, plan_sha256=plan_sha256,
        capture=capture, retained_sha256=sha, model_input_sha256=sha,
        model_input_bytes=len(raw), conversion='identity_original_png')
