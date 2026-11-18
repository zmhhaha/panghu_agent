"""Video-learning documents on the admitted complete HyperFrames MP4 renderer.

The immutable binding retains the original learning input, not only an adapter
description. Rendering and reverse-seek checks confer technical evidence only;
they never assert that an Agent watched continuous motion or heard the audio.
"""
from copy import deepcopy

from agent_service.contracts import digest
from agent_service.profiles import video_reference_learning as protocol
from agent_service.profiles import video_animation_method as method
from .animation_method_renderer import MethodAnimationRenderer, ASSET_SHA256, _require


class VideoLearningRenderer(MethodAnimationRenderer):
    def __init__(self, root, *, runtime_renderer):
        super().__init__(root, runtime_renderer.image, assets_dir=runtime_renderer.assets_dir,
            enabled=runtime_renderer.enabled, timeout=runtime_renderer.timeout,
            hyperframes=True, legacy_bridge=runtime_renderer.legacy_bridge,
            generated_resource_resolver=runtime_renderer.generated_resource_resolver)
        _require(runtime_renderer.hyperframes is True, 'video_learning_hyperframes_required', 422)
        self.runtime_renderer = runtime_renderer

    def capabilities(self):
        result = deepcopy(self.runtime_renderer.capabilities())
        # This first reference-learning contract admits three drawing engines
        # and their common local assets, without claiming unbound extra tools.
        result.pop('authoring_libraries', None)
        result['assets'] = {name: checksum for name, checksum in result.get('assets', {}).items()
                            if name in ASSET_SHA256}
        result['schema'] = 'video.reference-learning-runtime-capabilities.v1'
        result['evidence_scope'] = 'sampled_frames_only'
        result['limits'] = dict(width=1280, height=720, fps=24, duration_seconds=60)
        return result

    @staticmethod
    def _adapter(context):
        bounds = deepcopy(context['constraints'])
        bounds.setdefault('allowed_engines', ['html-svg', 'canvas', 'three'])
        return dict(schema=method.INPUT_SCHEMA, mode='create', goal=context['goal'],
            criteria=[dict(id='learning-target', description='Reproduce the frozen visual learning target')],
            methods=[], constraints=bounds, feedback=context['feedback'],
            base_source=None, reference=dict(learning_input_sha256=digest(context)),
            observation=dict(frames=[], audio_observed=False, motion_observed=False,
                limitations=['Rendering is not Agent observation.']), comparison=None,
            assets=deepcopy(context.get('assets', bounds.get('assets', {}))))

    def _snapshot(self, operation_id, source, document, sound_events, deadline_at=None):
        context = protocol.validate_input(document)
        checked = protocol.validate_source(source, constraints=context['constraints'])
        events = document.get('sound_events', sound_events or [])
        protocol.validate_sound_events(events, context['constraints']['duration_seconds'])
        payload = {key: checked[key] for key in ('engine', 'strategy', 'files')}
        binding = super()._snapshot(operation_id, payload, self._adapter(context), events,
            deadline_at=deadline_at)
        binding['document'] = context
        binding['input_sha256'] = digest(context)
        binding['learning_source_sha256'] = checked['source_sha256']
        return binding

    def _public(self, operation_id, result):
        if result is None:
            return dict(status='unknown', technical_passed=False, stop_proven=False)
        proof = super().stopping_proof(operation_id)
        rendered = result.get('technical_passed') is True
        row, binding = self._row(operation_id, historical=True)
        root = self._directory(operation_id)
        value = dict(result, status='rendered' if rendered else 'failed',
            stop_proven=proof['stop_proven'], evidence_scope='sampled_frames_only',
            mime='video/mp4', evidence_sha256=result['technical']['evidence_sha256'],
            input_sha256=binding['input_sha256'])
        if rendered:
            value.update(media_path=str(root/'work/output.mp4'), artifact_path=str(root/'work/output.mp4'),
                receipt_path=str(root/'work/render-receipt.json'), sha256=result['media']['sha256'],
                width=binding['request']['constraints']['width'], height=binding['request']['constraints']['height'],
                duration_seconds=binding['request']['constraints']['duration_seconds'], fps=24)
        return value

    def render(self, operation_id, source, document, sound_events=None, *, deadline_at=None):
        # Parent render calls self.lookup after its single durable claim.
        return super().render(operation_id, source, document, sound_events, deadline_at=deadline_at)

    submit = render

    def lookup(self, operation_id):
        try:
            result = super().lookup(operation_id)
        except Exception as error:
            from agent_service.contracts import ServiceError
            if isinstance(error, ServiceError) and error.status == 404:
                return dict(status='unknown', technical_passed=False, stop_proven=False)
            raise
        return self._public(operation_id, result)

    recover = lookup

    def stopping_proof(self, operation_id):
        return super().stopping_proof(operation_id)
