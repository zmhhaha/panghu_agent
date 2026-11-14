"""Exact stopped Manim resources for the opt-in method v5 boundary."""
from copy import deepcopy
import json
from pathlib import Path

from agent_service.contracts import canonical_bytes, digest
from .manim_generated_assets import decode_generated_assets
from .manim_resource import manifest_path, require, sha

SCHEMA = 'video.manim-method-resource.v1'
HELPERS = ('manim_frame_layer.mjs', 'manim_method_frames.mjs')
RUNTIME = Path(__file__).with_name('motion_renderer')


def helper_assets():
    result = {}
    for name in HELPERS:
        path = RUNTIME / name
        require(path.is_file() and not path.is_symlink(), 'manim_method_helper_missing', 503)
        raw = path.read_bytes()
        require(0 < len(raw) <= 64 * 1024, 'manim_method_helper_invalid')
        result[name] = raw
    return result


def method_resource(envelope, *, expected_sha256, expected_scope, image_digest):
    files = decode_generated_assets(envelope, expected_sha256=expected_sha256,
        **expected_scope, runtime_image_digest=image_digest)
    request = envelope['request']
    require(request['frame_count'] <= 60, 'manim_method_frame_budget_exceeded', 413)
    manifest = json.loads(files[manifest_path(request['resource_id'])])
    require(all(name.startswith('assets/') for name in files), 'manim_method_asset_path_invalid')
    assets = {name.removeprefix('assets/'): raw for name, raw in files.items()}
    assets.update(helper_assets())
    descriptor = dict(schema=SCHEMA, scope=deepcopy(expected_scope),
        envelope_sha256=expected_sha256, image_digest=image_digest,
        manifest_path=manifest_path(request['resource_id']),
        manifest_sha256=envelope['manifest_sha256'], manifest=manifest,
        helpers={name: sha(assets[name]) for name in HELPERS},
        assets_sha256={name: sha(raw) for name, raw in sorted(assets.items())})
    descriptor['sha256'] = digest(descriptor)
    return descriptor, assets


def validate_descriptor(value, *, assets, duration_seconds):
    fields = {'schema', 'scope', 'envelope_sha256', 'image_digest', 'manifest_path',
        'manifest_sha256', 'manifest', 'helpers', 'assets_sha256', 'sha256'}
    require(type(value) is dict and set(value) == fields and value['schema'] == SCHEMA
        and value['sha256'] == digest({key: item for key, item in value.items() if key != 'sha256'}),
        'manim_method_resource_binding_invalid', 409)
    manifest = value['manifest']
    require(type(manifest) is dict and manifest['frame_count'] <= 60
        and manifest['global_start_frame'] + manifest['frame_count'] <= duration_seconds * 24,
        'manim_method_resource_clock_outside_project', 409)
    require(type(assets) is dict and set(assets) == set(value['assets_sha256'])
        and all(type(raw) is bytes and sha(raw) == value['assets_sha256'][name] for name, raw in assets.items()),
        'manim_method_resource_assets_changed', 409)
    require(value['helpers'] == {name: sha(raw) for name, raw in helper_assets().items()},
        'manim_method_resource_helper_changed', 409)
    require(assets[value['manifest_path'].removeprefix('assets/')] == canonical_bytes(manifest),
        'manim_method_resource_manifest_changed', 409)
    return deepcopy(value)
