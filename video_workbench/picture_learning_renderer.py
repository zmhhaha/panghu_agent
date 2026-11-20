"""One durable isolated PNG render using the real shared HyperFrames runtime.

The still host and ledger are separate from immutable animation admissions.
An uncertain claim is read back; neither recovery nor GET starts a container.
"""
from contextlib import closing
from copy import deepcopy
import hashlib
import io
import json
import math
from pathlib import Path
import time

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_picture_learning as protocol
from .animation_creation_renderer import _read, _write
from .animation_method_renderer import (MethodAnimationRenderer, execute_method_render,
    ASSET_SHA256, ENGINE_RUNTIME, PROJECT_RUNTIME, HYPERFRAMES_CORE_SHA256, _json, _require, _TABLE)

HOST = Path(__file__).with_name('picture_learning_host.mjs')
MAX_PNG_BYTES = 8 * 1024 * 1024


class PictureLearningRenderer(MethodAnimationRenderer):
    def __init__(self, root, *, runtime_renderer):
        super().__init__(root, runtime_renderer.image, assets_dir=runtime_renderer.assets_dir,
            enabled=runtime_renderer.enabled, timeout=runtime_renderer.timeout,
            hyperframes=True, legacy_bridge=True,
            generated_resource_resolver=getattr(runtime_renderer, 'generated_resource_resolver', None))
        self.runtime_renderer = runtime_renderer

    def capabilities(self):
        result = deepcopy(self.runtime_renderer.capabilities())
        result.pop('authoring_libraries', None)
        result['assets'] = {name: sha for name, sha in result.get('assets', {}).items() if name in ASSET_SHA256}
        from .ink_landscape_library import descriptor, read_assets
        library = descriptor()
        read_assets()
        result['drawing_libraries'] = {'ink-landscape': library}
        result['assets'].update(library['assets'])
        result['schema'] = 'video.picture-learning-runtime-capabilities.v1'
        result['runtime_identity'].update(host_sha256=digest_bytes(_read(HOST, 1024 * 1024)),
            project_runtime_sha256=digest_bytes(_read(PROJECT_RUNTIME, 1024 * 1024)),
            hyperframes_core_sha256=HYPERFRAMES_CORE_SHA256)
        result['evidence_scope'] = 'static_only'
        return result

    def _snapshot(self, operation_id, source, document, deadline_at=None):
        self._directory(operation_id)
        _require(deadline_at is None or type(deadline_at) in (int, float)
            and math.isfinite(deadline_at), 'picture_render_deadline_invalid', 422)
        context = protocol.validate_input(document)
        payload = protocol.validate_source(source, constraints=context['constraints'])
        caps = self.capabilities()
        _require(payload['engine'] in caps['allowed_engines'], 'picture_render_engine_unavailable', 422)
        width, height = (context['constraints'][key] for key in ('width', 'height'))
        assets = context.get('assets', context['constraints'].get('assets', {}))
        _require(type(assets) is dict, 'picture_render_assets_changed', 409)
        from .learning_drawing_libraries import drawing_libraries
        libraries = drawing_libraries(context['constraints'].get('drawing_libraries'), assets)
        allowed_assets = {name: sha for name, sha in caps['assets'].items() if name in ASSET_SHA256}
        if libraries:
            _require(libraries == caps['drawing_libraries'], 'picture_render_drawing_library_changed', 409)
            for library in libraries.values():
                allowed_assets.update(library['assets'])
        generated = context['constraints'].get('generated_resources')
        if generated is not None:
            from .manim_method_resources import validate_descriptor
            _require(type(generated) is list and len(generated) == 1
                and callable(self.generated_resource_resolver), 'picture_render_resource_not_admitted', 422)
            descriptor = generated[0]
            exact_assets = self.generated_resource_resolver(descriptor)
            validate_descriptor(descriptor, assets=exact_assets, duration_seconds=1)
            _require(descriptor['manifest']['frame_count'] == 1
                and descriptor['manifest']['global_start_frame'] == 0,
                'picture_render_resource_single_frame_required', 422)
            _require(not (set(allowed_assets) & set(exact_assets)), 'picture_render_resource_collision', 409)
            generated_checksums = {name: digest_bytes(raw) for name, raw in exact_assets.items()}
            _require(all(assets.get(name) == checksum for name, checksum in generated_checksums.items()),
                'picture_render_resource_assets_missing', 409)
            allowed_assets.update(generated_checksums)
        _require(all(allowed_assets.get(name) == sha for name, sha in assets.items()),
            'picture_render_assets_changed', 409)
        if payload['engine'] == 'three':
            _require('three.module.min.js' in assets, 'picture_render_three_asset_required', 422)
        frozen = context['constraints'].get('runtime_identity')
        if frozen is not None:
            _require(frozen == caps['runtime_identity'], 'picture_render_runtime_changed', 409)
        files = {name: digest_bytes(text.encode()) for name, text in payload['files'].items()}
        # The one-frame export has no external duration. This internal one-second
        # composition exists solely to drive real HyperFrames renderSeek(0).
        project = dict(schema='video.hyperframes-project.v1',
            project_id='picture-' + digest(dict(operation_id=operation_id, files=files))[:32],
            width=width, height=height, fps=24, duration_seconds=1, entry='scene.html',
            scenes=[dict(id='still', start_seconds=0, duration_seconds=1, entry='scene.html',
                source_sha256=files['scene.html'], engine=payload['engine'])])
        request = dict(schema='video.picture-learning-render-request.v1',
            constraints=dict(width=width, height=height), engine=payload['engine'],
            files_sha256=files, assets_sha256=assets, project=project, project_sha256=digest(project),
            host_sha256=digest_bytes(_read(HOST, 1024 * 1024)),
            engine_runtime_sha256=digest_bytes(_read(ENGINE_RUNTIME, 1024 * 1024)),
            project_runtime_sha256=digest_bytes(_read(PROJECT_RUNTIME, 1024 * 1024)),
            image_digest=self.image_digest, hyperframes_core_sha256=HYPERFRAMES_CORE_SHA256)
        if generated is not None:
            request.update(schema='video.picture-learning-render-request.v2', generated_resources=deepcopy(generated))
        if libraries:
            request['drawing_libraries'] = libraries
        request['identity'] = digest(request)
        return dict(schema='video.picture-learning-render-binding.v1', operation_id=operation_id,
            source=payload, source_sha256=payload['source_sha256'], document=context,
            input_sha256=digest(context), request=request, request_sha256=digest(request),
            image=self.image, image_digest=self.image_digest, timeout_seconds=self.timeout,
            configured_timeout_seconds=self.timeout, deadline_at=deadline_at)

    def render(self, operation_id, source, document, *, deadline_at=None):
        _require(self.enabled, 'picture_renderer_disabled', 503)
        binding = self._snapshot(operation_id, source, document, deadline_at)
        with closing(self._connect(create=True)) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT binding_sha256,binding FROM ' + _TABLE + ' WHERE operation_id=?',
                (operation_id,)).fetchone()
            if row is not None:
                stored = json.loads(row['binding'])
                binding['timeout_seconds'] = stored['timeout_seconds']
                _require(row['binding_sha256'] == digest(binding), 'picture_render_operation_conflict', 409)
                db.commit()
                return self.lookup(operation_id)
            if deadline_at is not None:
                remaining = deadline_at - time.time()
                _require(remaining >= 30, 'picture_render_deadline_insufficient', 409)
                binding['timeout_seconds'] = min(self.timeout, remaining)
            db.execute('INSERT INTO ' + _TABLE + '(operation_id,binding_sha256,binding) VALUES (?,?,?)',
                (operation_id, digest(binding), canonical_bytes(binding)))
            db.commit()
        root = self._directory(operation_id)
        preparing = True
        try:
            root.mkdir(parents=True, exist_ok=False)
            (root / 'input/scene/assets').mkdir(parents=True)
            for name, text in source['files'].items():
                _write(root / 'input/scene' / name, text.encode())
            generated_assets = {}
            for descriptor in binding['request'].get('generated_resources', []):
                generated_assets.update(self.generated_resource_resolver(descriptor))
            if binding['request'].get('drawing_libraries'):
                from .ink_landscape_library import read_assets
                generated_assets.update(read_assets())
            for name, sha in binding['request']['assets_sha256'].items():
                raw = (generated_assets[name] if name in generated_assets
                       else _read(self.assets_dir / name, 32 * 1024 * 1024))
                _require(digest_bytes(raw) == sha, 'picture_render_assets_changed', 409)
                target = root / 'input/scene/assets' / name
                target.parent.mkdir(parents=True, exist_ok=True)
                _write(target, raw)
            for path, content in {
                'host.mjs': _read(HOST, 1024 * 1024),
                'engine_runtime.mjs': _read(ENGINE_RUNTIME, 1024 * 1024),
                'hyperframes_project.mjs': _read(PROJECT_RUNTIME, 1024 * 1024),
                'input/request.json': canonical_bytes(binding['request']),
                'binding.json': canonical_bytes(binding),
                'source.json': canonical_bytes(source),
                'render-plan.json': canonical_bytes(dict(directory=str(root), image=self.image,
                    repository=self.repository, image_digest=self.image_digest,
                    request_sha256=binding['request_sha256'], timeout_seconds=binding['timeout_seconds'],
                    deadline_at=deadline_at)),
            }.items():
                _write(root / path, content)
            preparing = False
            execute_method_render(root)
        except Exception as error:
            if preparing and root.is_dir() and not (root / 'render-outcome.json').exists():
                _write(root / 'render-outcome.json', canonical_bytes(dict(
                    schema='video.animation-method-render-outcome.v1',
                    request_identity=binding['request']['identity'], request_sha256=binding['request_sha256'],
                    image=self.image, status='failed', error=getattr(error, 'code', type(error).__name__),
                    actual_image_id=None, container_id=None,
                    cleanup=dict(status='not_created', container_id=None), automatic_retry=False)))
        return self.lookup(operation_id)

    def lookup(self, operation_id):
        try:
            row, binding = self._row(operation_id, historical=True)
        except ServiceError as error:
            if error.status == 404:
                return None
            raise
        root, request = self._directory(operation_id), binding['request']
        if not (root / 'render-outcome.json').exists():
            return dict(status='unknown', technical_passed=False, stop_proven=False)
        outcome, raw_outcome = _json(root / 'render-outcome.json', 65536)
        _require(outcome.get('schema') == 'video.animation-method-render-outcome.v1'
            and outcome.get('request_identity') == request['identity']
            and outcome.get('request_sha256') == binding['request_sha256']
            and outcome.get('image') == binding['image'] and outcome.get('automatic_retry') is False
            and outcome.get('status') in {'rendered', 'failed'}, 'picture_render_outcome_changed', 409)
        cleanup = outcome.get('cleanup', {})
        cid = outcome.get('container_id')
        proven = (cid is not None and type(cid) is str and len(cid) == 64
            and cleanup.get('container_id') == cid and cleanup.get('status') == 'removed_and_confirmed_absent'
            or cid is None and outcome['status'] == 'failed' and cleanup.get('status') == 'not_created')
        if not proven:
            return dict(status='unknown', technical_passed=False, stop_proven=False)
        result = dict(status=outcome['status'], technical_passed=False, stop_proven=True,
            evidence_scope='static_only')
        receipt = None
        if outcome['status'] == 'rendered':
            receipt, _ = _json(root / 'work/render-receipt.json')
            _require(receipt.get('schema') == 'video.picture-learning-render-receipt.v1'
                and receipt.get('status') == 'rendered' and receipt.get('evidence_scope') == 'static_only'
                and all(receipt.get(key) == request[key] for key in ('image_digest','host_sha256',
                    'engine_runtime_sha256','project_runtime_sha256','engine'))
                and receipt.get('request_identity') == request['identity']
                and receipt.get('request_sha256') == binding['request_sha256'],
                'picture_render_receipt_changed', 409)
            for name, expected in request['files_sha256'].items():
                _require(digest_bytes(_read(root / 'input/scene' / name, 512 * 1024)) == expected,
                    'picture_render_source_changed', 409)
            _require(_json(root / 'source.json')[0] == binding['source'], 'picture_render_source_changed', 409)
            for name, expected in request['assets_sha256'].items():
                _require(digest_bytes(_read(root / 'input/scene/assets' / name, 32 * 1024 * 1024)) == expected,
                    'picture_render_assets_changed', 409)
            for name, key in [('host.mjs', 'host_sha256'), ('engine_runtime.mjs','engine_runtime_sha256'),
                              ('hyperframes_project.mjs','project_runtime_sha256')]:
                _require(digest_bytes(_read(root / name, 1024 * 1024)) == request[key],
                    'picture_render_runtime_changed', 409)
            if 'generated_resources' in request:
                observation = receipt.get('generated_resource_observation', {})
                expected_png = sorted('/assets/' + name for descriptor in request['generated_resources']
                    for name in descriptor['assets_sha256'] if name.endswith('.png'))
                _require(observation.get('schema') == 'video.manim-method-resource-observation.v1'
                    and observation.get('scope') == 'loaded_resources_not_visual_acceptance'
                    and observation.get('resources_sha256') == [row['sha256'] for row in request['generated_resources']]
                    and observation.get('loaded_png_paths') == expected_png
                    and observation.get('helpers_loaded') is True,
                    'picture_render_resource_evidence_invalid', 409)
            if 'drawing_libraries' in request:
                from .learning_drawing_libraries import validate_observation
                validate_observation(receipt.get('drawing_library_observation'), request['drawing_libraries'])
            else:
                _require('drawing_library_observation' not in receipt,
                    'learning_drawing_library_evidence_invalid', 409)
            png = _read(root / 'work/output.png', MAX_PNG_BYTES)
            checksum = digest_bytes(png)
            _require(checksum == receipt.get('output_sha256') and len(png) == receipt.get('output_bytes')
                and receipt.get('checks') == dict(repeat_sha256=checksum,reload_sha256=checksum,seek_seconds=0),
                'picture_render_output_changed', 409)
            from PIL import Image
            with Image.open(io.BytesIO(png)) as image:
                _require(image.format == 'PNG' and image.size == (request['constraints']['width'],
                    request['constraints']['height']), 'picture_render_dimensions_changed', 409)
                image.verify()
            project = receipt.get('project_runtime', {})
            _require(project.get('core_sha256') == HYPERFRAMES_CORE_SHA256
                and project.get('project_sha256') == request['project_sha256']
                and project.get('core_seek_count', 0) >= 1 and project.get('actual_seconds') == 0,
                'picture_render_hyperframes_unproven', 409)
            _require({p.name for p in (root / 'work').iterdir()} == {'output.png','render-receipt.json'},
                'picture_render_output_extra', 409)
            result.update(technical_passed=True, picture_path=str(root / 'work/output.png'),
                source_path=str(root / 'source.json'), receipt_path=str(root / 'work/render-receipt.json'),
                sha256=checksum, mime='image/png', **request['constraints'],
                runtime=dict(engine=request['engine'], image=binding['image'],
                    project_runtime='hyperframes-core-project-1',
                    hyperframes_core_sha256=HYPERFRAMES_CORE_SHA256,
                    engine_observation=receipt.get('engine_observation'),evidence_scope='static_only'))
            if 'generated_resources' in request:
                result['runtime'].update(generated_resources=deepcopy(request['generated_resources']),
                    generated_resource_observation=deepcopy(receipt['generated_resource_observation']))
            if 'drawing_libraries' in request:
                result['runtime'].update(drawing_libraries=deepcopy(request['drawing_libraries']),
                    drawing_library_observation=deepcopy(receipt['drawing_library_observation']))
        else:
            result['error'] = 'picture_render_failed'
        proof = digest(dict(binding_sha256=digest(binding),outcome_sha256=digest_bytes(raw_outcome),
            receipt_sha256=digest(receipt) if receipt else None))
        result['evidence_sha256'] = proof
        with closing(self._connect(write=True)) as db:
            db.execute('BEGIN IMMEDIATE')
            stored = db.execute('SELECT proof_sha256,result FROM '+_TABLE+' WHERE operation_id=?', (operation_id,)).fetchone()
            if stored['proof_sha256'] is not None:
                _require(stored['proof_sha256'] == proof and bytes(stored['result']) == canonical_bytes(result),
                    'picture_render_proof_changed', 409)
            else:
                db.execute('UPDATE '+_TABLE+' SET proof_sha256=?,result=? WHERE operation_id=?',
                    (proof,canonical_bytes(result),operation_id))
            db.commit()
        return result

    recover = lookup

    def stopping_proof(self, operation_id):
        result = self.lookup(operation_id)
        return dict(stop_proven=bool(result and result.get('stop_proven')),
            evidence_sha256=result.get('evidence_sha256') if result else None)


def digest_bytes(raw):
    return hashlib.sha256(raw).hexdigest()
