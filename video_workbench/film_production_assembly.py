"""Freeze stopped module handoffs into one complete HyperFrames film input.

Authorization and selection of project/run/targets/releases precede this pure
adapter. It creates no Agent task, per-scene video, execution or user approval.
"""
from copy import deepcopy
import hashlib
import re

from agent_service.contracts import ServiceError, digest
from agent_service.profiles.video_animation_production_contract import validate_runtime_identity
from .creative_film_module_contract import validate_module_package, predecessor_from_package
from .hyperframes_film_browser import HOST_NAMES, compile_film_browser_bundle
from .hyperframes_film_project import MAX_ASSET_BYTES, compile_film_project
from .hyperframes_film_snapshot import freeze_film_execution_snapshot
from .motion_snapshot import _runtime

SCHEMA = 'video.film-production-assembly.v1'


def _require(value, code='film_production_assembly_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def assemble_film_production(*, packages, film, film_sha256, recordings, assets,
        runtime_fingerprint, runtime_identity, module_targets, host_files,
        trusted_host_sha256, image, project_revision, production_run_id, timeout_seconds=600):
    """Return a snapshot and provenance manifest for the exact ordered handoffs.

    ``module_targets`` and ``runtime_identity`` are externally frozen author
    inputs; they must not be recovered from the packages to authorize themselves.
    ``assets`` are the available immutable bytes. All admitted asset hashes are
    checked, while only module-declared assets enter the compiled project.
    """
    _require(type(film) is dict and type(film.get('scenes')) is list
        and digest(film) == film_sha256, 'film_production_film_binding_mismatch', 409)
    rows = film['scenes']
    _require(type(packages) is list and 1 <= len(packages) == len(rows) <= 24
        and type(module_targets) is list and len(module_targets) == len(rows),
        'film_production_package_inventory_invalid')
    runtime = _runtime(runtime_fingerprint)
    identity = validate_runtime_identity(runtime_identity)
    _require(identity['image_digest'] == runtime['image_digest']
        and identity['engine_runtime_sha256'] == runtime['engine_runtime_sha256'],
        'film_production_runtime_binding_mismatch', 409)
    _require(type(host_files) is dict and set(host_files) == HOST_NAMES
        and type(trusted_host_sha256) is dict and set(trusted_host_sha256) == HOST_NAMES
        and all(type(raw) is bytes and _sha(raw) == trusted_host_sha256[name]
                for name, raw in host_files.items()), 'film_production_host_binding_mismatch', 409)
    _require(type(assets) is dict and len(assets) <= 128
        and all(type(name) is str and re.fullmatch(r'assets/[A-Za-z0-9_./-]+', name)
                and not any(part in {'.', '..', ''} for part in name.split('/'))
                and len(name) <= 200 and type(raw) is bytes and 0 < len(raw) <= 32 * 1024 * 1024
                for name, raw in assets.items())
        and sum(map(len, assets.values())) <= MAX_ASSET_BYTES,
        'film_production_asset_inventory_invalid')
    asset_sha = {name: _sha(raw) for name, raw in assets.items()}
    frozen, modules, boundaries, sources = [], [], [], []
    dimensions = None
    for index, package in enumerate(packages):
        package = validate_module_package(package)
        document, result = package['input_document'], package['result']
        context, bounds = document['film_context'], document['constraints']
        _require(context['film'] == film and context['film_sha256'] == film_sha256
            and context['current_scene_index'] == index
            and document['scene']['scene_id'] == rows[index]['scene_id'],
            'film_production_package_order_mismatch', 409)
        expected_previous = None if index == 0 else predecessor_from_package(frozen[-1])
        _require(context['predecessor'] == expected_previous,
            'film_production_exact_predecessor_mismatch', 409)
        _require(document['target'] == module_targets[index], 'film_production_target_mismatch', 409)
        _require(bounds['runtime_identity'] == identity, 'film_production_runtime_binding_mismatch', 409)
        current_dimensions = (bounds['width'], bounds['height'], bounds['fps'])
        if dimensions is None:
            dimensions = current_dimensions
        _require(current_dimensions == dimensions, 'film_production_dimensions_mismatch', 409)
        _require(all(asset_sha.get(name) == checksum for name, checksum in document['assets'].items()),
            'film_production_asset_binding_mismatch', 409)
        modules.append(result['module'])
        incoming = result['incoming_boundary']
        if incoming is not None:
            boundaries.append(incoming['boundary'])
            if incoming['transition_module'] is not None:
                modules.append(incoming['transition_module'])
        sources.append(dict(scene_id=rows[index]['scene_id'], package_sha256=digest(package),
            input_sha256=package['input_sha256'], result_sha256=package['result_sha256'],
            target=deepcopy(document['target']), skill_catalog_sha256=package['skill_catalog_sha256'],
            skill_delivery_sha256=package['skill_delivery_sha256'],
            skill_tool_evidence_sha256=package['skill_tool_evidence_sha256']))
        frozen.append(package)
    used = {name for module in modules for name in module['assets']}
    _require(used <= set(assets), 'film_production_asset_binding_mismatch', 409)
    project = compile_film_project(film=film, film_sha256=film_sha256, modules=modules,
        recordings=recordings, boundaries=boundaries,
        assets={name: assets[name] for name in used}, runtime_fingerprint=runtime)
    project_sha = _sha(project['manifest.json'])
    bundle = compile_film_browser_bundle(project, expected_project_manifest_sha256=project_sha,
        width=dimensions[0], height=dimensions[1], host_files=host_files)
    snapshot = freeze_film_execution_snapshot(bundle,
        expected_bundle_sha256=_sha(bundle['browser-manifest.json']), runtime_fingerprint=runtime,
        trusted_host_sha256=trusted_host_sha256, image=image, project_revision=project_revision,
        production_run_id=production_run_id, timeout_seconds=timeout_seconds)
    manifest = dict(schema=SCHEMA, project_id=film['project_id'], project_revision=project_revision,
        production_run_id=production_run_id, film_sha256=film_sha256,
        snapshot_sha256=snapshot['sha256'], bundle_sha256=snapshot['bundle_sha256'],
        project_manifest_sha256=project_sha, runtime_identity=identity,
        runtime_fingerprint=runtime, image=image, trusted_host_sha256=deepcopy(trusted_host_sha256),
        sources=sources, available_asset_sha256=dict(sorted(asset_sha.items())),
        project_asset_sha256={name: asset_sha[name] for name in sorted(used)},
        module_sha256=deepcopy(snapshot['module_sha256']),
        recording_sha256=deepcopy(snapshot['recording_sha256']), clock=deepcopy(snapshot['clock']),
        execution=dict(status='not_submitted', concatenated_scene_videos=False,
            evidence_scope='verified_stopped_modules_and_frozen_whole_project_not_render_or_adoption'))
    manifest['sha256'] = digest(manifest)
    return dict(snapshot=snapshot, manifest=manifest)
