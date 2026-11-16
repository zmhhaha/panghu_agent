"""Write a reviewable disabled recipe candidate; never apply or call a model."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from agent_service.animation_skills_contract import build_skill_package, bundle_files, freeze_skill_catalog
from agent_service.contracts import canonical_bytes, digest
from agent_service.deploy.acceptance_probe import render_probe
from agent_service.deploy.animation_manim_catalog import measure_manim_recipe_bundle, prepare_manim_recipe_catalog
from agent_service.profiles.video_animation_manim_contract import INPUT_SCHEMA, validate_core_task, validate_input
from agent_service.profiles.video_animation_manim_recipe import PROFILE, INPUT_NAME, RESULT_NAME, EVIDENCE_NAME
from agent_service.worker_entry import HostConfig


def prepare_candidate(current, *, output, worker_image, source_pool_id, source_revision,
        candidate_name, candidate_uid_start, node='arm-cluster-master', model_policy=None):
    if not re.fullmatch(r'[0-9a-f]{40}', source_revision or ''):
        raise ValueError('manim_candidate_source_revision_required')
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,50}', candidate_name or ''):
        raise ValueError('manim_candidate_name_invalid')
    if type(candidate_uid_start) is not int or not 65536 <= candidate_uid_start <= 2**31 - 4:
        raise ValueError('manim_candidate_uid_range_invalid')
    output = Path(output).absolute()
    if output.exists() or any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError('manim_candidate_new_directory_required')
    manifests, measurement = measure_manim_recipe_bundle()
    disabled, hosts, preparation = prepare_manim_recipe_catalog(current, worker_image=worker_image,
        resource_manifests=manifests, source_pool_id=source_pool_id, model_policy=model_policy, register_caller=False)
    core = validate_core_task(dict(effect_id=candidate_name,
        goal=('Create an original two-second transparent geometric transformation that makes a visible '
              'cause and its result easy to follow. Choose the geometry, staging, palette and choreography '
              'yourself; do not use a supplied scene or layout. This is a technical recipe candidate, '
              'not aesthetic approval or a published effect.'),
        guidance=('Use skill_search for offline deterministic Manim authoring and skill_read for its '
                  'returned SKILL.md. Write original source only. Do not execute Python or render. '
                  'Keep the exact frozen frame count and include an intentional wait within the timing.'),
        resource_constraints=dict(resource_id='manim_recipe_candidate', width=640, height=360, fps=24,
            frame_count=48, global_start_frame=0, seed=17, transparent=True, manim_version='0.22.1')))
    package = build_skill_package(skill_id='operator-only-manim-clock-discipline', version=1, kind='expression',
        files={'SKILL.md': '---\nname: operator-only-manim-clock-discipline\n'
            'description: Original deterministic Cairo resources on an exact frame clock; technical probe only.\n---\n'
            'Create an original composition appropriate to the requested causal explanation. Use Manim '
            'Community 0.22.1 Scene geometry and Pango Text where useful. Design all play and wait '
            'durations on the exact supplied 24 fps grid and make their sum equal frame_count/fps. '
            'Use explicit seeds for randomness. LaTeX, downloaded images, sound and external files '
            'are unavailable. The caller renders outside the source-only Agent and later consumes '
            'transparent PNG frames on its own absolute clock. Do not start a second renderer, loop '
            'or encode video. This is a technical reading fixture, not business publication.'},
        runtime_requirements={'engines': ['html-svg']})
    method = dict(schema='video.animation-method.v2', id='operator-only-manim-clock-discipline-v1',
        status='published', content_sha256=digest(dict(operator_only_fixture=True)), skill_package=package)
    method['publication'] = dict(schema='video.animation-method-publication.v2', method_version_id=method['id'],
        method_content_sha256=method['content_sha256'], skill_package_sha256=package['sha256'],
        reference_validation_ids=['operator-only-not-human-approval'],
        transfer_validation_ids=['operator-only-not-business-transfer'])
    catalog = freeze_skill_catalog([method], recipient_owner='technical-manim-recipe-probe',
        consumer_id=candidate_name, task_sha256=digest(core), allowed_engines=['html-svg'])
    document = validate_input(dict(schema=INPUT_SCHEMA, **core, skill_catalog=catalog))
    files = {item.name: item.content() for item in bundle_files(catalog, {package['sha256']: package})}
    files[INPUT_NAME] = canonical_bytes(document)
    scenario = dict(schema_version='agent-candidate-scenario.v1', candidate_scope_only=True,
        scenario_id=str(uuid4()), profile=PROFILE, timeout_seconds=600, task={'input_sha256': digest(document)},
        inputs=[dict(name=name, sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
            for name, raw in sorted(files.items())], business_export=None, parent=None)
    output.mkdir(mode=0o700, parents=True)
    sources = output / 'candidate-sources'
    sources.mkdir(mode=0o700)
    def save(path, value):
        raw = value if type(value) is bytes else canonical_bytes(value)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
    for name, raw in files.items():
        path = sources / name
        path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        save(path, raw)
        path.chmod(0o644)
    save(output/'candidate-scenario.json', scenario)
    manifest = render_probe(HostConfig.model_validate(hosts[PROFILE]), name=candidate_name,
        worker_image=worker_image, node=node, uid_start=candidate_uid_start,
        bootstrap_secret=candidate_name+'-bootstrap', upstream_secret=candidate_name+'-upstream',
        scenario=output/'candidate-scenario.json', source_dir=sources)
    plan = dict(schema='video.manim-recipe-candidate-plan.v1', profile=PROFILE, source_revision=source_revision,
        worker_image=worker_image, input_sha256=digest(document), caller=preparation['caller'],
        preparation=preparation, candidate_name=candidate_name, candidate_uid_start=candidate_uid_start,
        candidate_uid_count=3, uid_reserved=False, tokens_created=False, model_requests=0,
        activation_permitted=False, source_only=True, runtime_execution_permitted=False,
        artifact_names=[RESULT_NAME, EVIDENCE_NAME],
        next_steps=['verify_source_and_image_labels', 'check_and_reserve_three_fresh_candidate_uids',
            'review_and_execute_one_candidate', 'independently_verify_original_stop_and_two_artifacts',
            'render_exact_recipe_with_separately_admitted_cairo_image', 'verify_method_v5_and_review_sample',
            'prepare_and_review_independent_role_activation'])
    for name, value in {'public-before.json': current, 'public-disabled.json': disabled,
        'host-disabled.json': hosts[PROFILE], 'bundle-measurement.json': measurement,
        'candidate-manifest.json': manifest, 'release-plan.json': plan,
        'candidate-skill-catalog.json': catalog,
        'budget.json': dict(maximum_new_recipe_tasks=1, hard_model_requests_per_attempt=8,
            connectivity_included_in_model_limit=True, task_timeout_seconds=600,
            automatic_retry=False, failed_attempts_consume_budget=True, unknown_attempts_reconcile_original=True,
            new_cairo_executions=0, tts_requests=0, business_skill_publication_permitted=False)}.items():
        save(output/name, value)
    save(output/'frozen-files.json', {path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.rglob('*')) if path.is_file()})
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--worker-image', required=True)
    parser.add_argument('--source-pool-id', required=True)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--candidate-name', required=True)
    parser.add_argument('--candidate-uid-start', type=int, required=True)
    parser.add_argument('--node', default='arm-cluster-master')
    parser.add_argument('--model-policy', type=Path)
    args = vars(parser.parse_args(argv))
    current = json.loads(args.pop('catalog').read_bytes())
    if args['model_policy'] is not None:
        args['model_policy'] = json.loads(args['model_policy'].read_bytes())
    plan = prepare_candidate(current, **args)
    print(json.dumps(dict(status='disabled_candidate_prepared', profile=PROFILE,
        input_sha256=plan['input_sha256'], model_requests=0, uid_reserved=False, routes_changed=False)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
