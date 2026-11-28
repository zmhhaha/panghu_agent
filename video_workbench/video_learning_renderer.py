"""Video-learning documents on the admitted complete HyperFrames MP4 renderer.

The immutable binding retains the original learning input, not only an adapter
description. Rendering and reverse-seek checks confer technical evidence only;
they never assert that an Agent watched continuous motion or heard the audio.
"""
from copy import deepcopy
import hashlib
from pathlib import Path

from agent_service.contracts import digest
from agent_service.profiles import video_reference_learning as protocol
from agent_service.profiles import video_animation_method as method
from .animation_method_renderer import MethodAnimationRenderer, ASSET_SHA256, MAX_CANVAS_CONTEXTS, _require, _read, _json
from .learning_drawing_libraries import drawing_libraries, validate_observation


HOST = Path(__file__).with_name('video_learning_host.mjs')
DEFAULT_RENDER_TIMEOUT_SECONDS = 3600
CANVAS_FRAME_STATE = 'reset-visible-canvas-2d-v1'


class VideoLearningRenderer(MethodAnimationRenderer):
    def __init__(self, root, *, runtime_renderer, timeout=DEFAULT_RENDER_TIMEOUT_SECONDS):
        super().__init__(root, runtime_renderer.image, assets_dir=runtime_renderer.assets_dir,
            enabled=runtime_renderer.enabled, timeout=timeout,
            hyperframes=True, legacy_bridge=runtime_renderer.legacy_bridge,
            generated_resource_resolver=runtime_renderer.generated_resource_resolver,
            sampling_service=getattr(runtime_renderer, 'sampling_service', None))
        _require(runtime_renderer.hyperframes is True, 'video_learning_hyperframes_required', 422)
        self.runtime_renderer = runtime_renderer

    def _host_path(self):
        return HOST

    def _static_asset_checksums(self, context):
        assets = super()._static_asset_checksums(context)
        libraries = drawing_libraries(context['constraints'].get('drawing_libraries'), context['assets'])
        for library in libraries.values():
            assets.update(library['assets'])
        return assets

    def _read_static_asset(self, name):
        from .ink_landscape_library import descriptor, read_assets
        if name in descriptor()['assets']:
            return read_assets()[name]
        return super()._read_static_asset(name)

    def capabilities(self):
        result = deepcopy(self.runtime_renderer.capabilities())
        # This first reference-learning contract admits three drawing engines
        # and their common local assets, without claiming unbound extra tools.
        result.pop('authoring_libraries', None)
        result['assets'] = {name: checksum for name, checksum in result.get('assets', {}).items()
                            if name in ASSET_SHA256}
        from .ink_landscape_library import descriptor, read_assets
        library = descriptor()
        read_assets()
        result['drawing_libraries'] = {'ink-landscape': library}
        result['assets'].update(library['assets'])
        result['runtime_identity']['host_sha256'] = hashlib.sha256(_read(HOST, 1024 * 1024)).hexdigest()
        result['schema'] = 'video.reference-learning-runtime-capabilities.v2'
        from .scene_space_renderer import render_capability
        result['scene_space'] = render_capability(admitted_engines=result['allowed_engines'] if self.enabled else ())
        result['evidence_scope'] = 'sampled_frames_only'
        result['limits'] = dict(width=1280, height=720, fps=24, duration_seconds=60)
        return result

    @staticmethod
    def _adapter(context, source):
        bounds = deepcopy(context['constraints'])
        bounds.setdefault('allowed_engines', ['html-svg', 'canvas', 'three'])
        assets = deepcopy(context.get('assets', bounds.get('assets', {})))
        libraries = drawing_libraries(bounds.get('drawing_libraries'), assets)
        selected = {}
        for name, library in libraries.items():
            referenced = any(asset in content for asset in library['assets']
                             for content in source['files'].values())
            compatible = source['engine'] in library['engines']
            _require(compatible or not referenced,
                'learning_drawing_library_engine_not_admitted', 422)
            if compatible and referenced:
                selected[name] = library
                continue
            for asset in library['assets']:
                assets.pop(asset, None)
                if type(bounds.get('assets')) is dict:
                    bounds['assets'].pop(asset, None)
        # Optional helpers are dependencies only when the source references them.
        # The original context remains intact in the immutable binding below.
        if selected:
            bounds['drawing_libraries'] = selected
        else:
            bounds.pop('drawing_libraries', None)
        return dict(schema=method.INPUT_SCHEMA, mode='create', goal=context['goal'],
            criteria=[dict(id='learning-target', description='Reproduce the frozen visual learning target')],
            methods=[], constraints=bounds, feedback=context['feedback'],
            base_source=None, reference=dict(learning_input_sha256=digest(context)),
            observation=dict(frames=[], audio_observed=False, motion_observed=False,
                limitations=['Rendering is not Agent observation.']), comparison=None,
            assets=assets)

    def _snapshot(self, operation_id, source, document, sound_events, deadline_at=None):
        context = protocol.validate_input(document)
        checked = protocol.validate_source(source, constraints=context['constraints'])
        events = document.get('sound_events', sound_events or [])
        protocol.validate_sound_events(events, context['constraints']['duration_seconds'])
        payload = {key: checked[key] for key in ('engine', 'strategy', 'files')}
        adapter = self._adapter(context, payload)
        binding = super()._snapshot(operation_id, payload, adapter, events,
            deadline_at=deadline_at)
        libraries = adapter['constraints'].get('drawing_libraries')
        request = binding['request']
        if libraries:
            request['drawing_libraries'] = libraries
        if payload['engine'] == 'canvas':
            request['canvas_frame_state'] = CANVAS_FRAME_STATE
        if libraries or payload['engine'] == 'canvas':
            request.pop('identity')
            request['identity'] = digest(request)
            binding['request_sha256'] = digest(request)
        binding['document'] = context
        binding['input_sha256'] = digest(context)
        binding['learning_source_sha256'] = checked['source_sha256']
        return binding

    def _diagnostic_outputs(self, work, request, receipt):
        path = work / 'render-progress.json'
        if not path.exists():
            return {}
        progress, raw = _json(path, 65536)
        diagnostics = receipt.get('diagnostics')
        _require(progress.get('schema') == 'video.method-render-diagnostics.v1'
            and type(diagnostics) is dict and digest(progress) == digest(diagnostics),
            'video_learning_render_diagnostics_changed', 409)
        return {'render-progress.json': len(raw)}

    def _outputs(self, root, binding, outcome):
        receipt, raw, audio = super()._outputs(root, binding, outcome)
        request = binding['request']
        if 'canvas_frame_state' in request:
            proof = receipt.get('canvas_frame_state')
            # The idle probe does not seek. The initial reverse check is
            # recorded separately from the named seek probes.
            seeks = receipt['frame_count'] + 1 + sum(
                row.get('kind') != 'idle' for row in receipt['seek_checks']['probes'])
            _require(type(proof) is dict and set(proof) == {'policy', 'seek_count', 'reset_contexts'}
                and proof['policy'] == request['canvas_frame_state'] == CANVAS_FRAME_STATE
                and type(proof['seek_count']) is int and proof['seek_count'] == seeks
                and type(proof['reset_contexts']) is int and 0 <= proof['reset_contexts'] <= seeks * MAX_CANVAS_CONTEXTS,
                'video_learning_canvas_frame_state_evidence_invalid', 409)
        libraries = request.get('drawing_libraries')
        if libraries is not None:
            validate_observation(receipt.get('drawing_library_observation'), libraries)
        else:
            _require('drawing_library_observation' not in receipt,
                'learning_drawing_library_evidence_invalid', 409)
        return receipt, raw, audio

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
            if 'drawing_libraries' in binding['request'] or 'canvas_frame_state' in binding['request']:
                receipt, _ = _json(root / 'work/render-receipt.json')
            if 'drawing_libraries' in binding['request']:
                value['runtime'].update(
                    drawing_libraries=deepcopy(binding['request']['drawing_libraries']),
                    drawing_library_observation=deepcopy(receipt['drawing_library_observation']))
            if 'canvas_frame_state' in binding['request']:
                value['runtime']['canvas_frame_state'] = deepcopy(receipt['canvas_frame_state'])
        return value

    def render(self, operation_id, source, document, sound_events=None, *, deadline_at=None, scene_space=None,
               expected_request_sha256=None):
        # Parent render calls self.lookup after its single durable claim.
        return super().render(operation_id, source, document, sound_events, deadline_at=deadline_at,scene_space=scene_space,
            expected_request_sha256=expected_request_sha256)

    submit = render

    def lookup(self, operation_id):
        from .scene_space_renderer import scene_renderer
        spatial=scene_renderer(self).lookup(operation_id)
        if spatial is not None:
            return spatial
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
