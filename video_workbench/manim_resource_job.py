"""Fixed Cairo Job inputs on the existing journal/PVC execution boundary.

Resource generation is a Job, not a public renderer service. Generating this
plan neither submits it nor proves runtime admission or stopped writers.
"""
from copy import deepcopy
import re

from agent_service.contracts import digest
from .manim_generated_assets import scope
from .manim_resource import require, validate_request

SCHEMA = 'video.manim-resource-job.v1'
INPUT_PREFIX = 'manim-resources/inputs'
OUTPUT_PREFIX = 'manim-resources/results'
LABEL = 'video-workbench.panghuer.top/'
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_KUBE = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z')


def freeze_resource_job(request, *, owner, effect_id, experiment_id, operation_id,
        image, recipe_input_sha256, recipe_result_sha256, recipe_stop_proof_sha256,
        timeout_seconds=180):
    require(type(image) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}', image),
        'manim_resource_image_digest_required')
    require(all(type(item) is str and _SHA.fullmatch(item) for item in
        (recipe_input_sha256, recipe_result_sha256, recipe_stop_proof_sha256)), 'manim_resource_recipe_binding_invalid')
    require(type(timeout_seconds) is int and 30 <= timeout_seconds <= 600, 'manim_resource_timeout_invalid')
    plan = dict(schema=SCHEMA, scope=scope(owner, effect_id, experiment_id, operation_id),
        request=validate_request(request), image=image,
        recipe_input_sha256=recipe_input_sha256, recipe_result_sha256=recipe_result_sha256,
        recipe_stop_proof_sha256=recipe_stop_proof_sha256, timeout_seconds=timeout_seconds)
    plan['sha256'] = digest(plan)
    return plan


def validate_resource_job(plan, *, expected_sha256, image):
    require(type(plan) is dict and set(plan) == {'schema', 'scope', 'request', 'image',
        'recipe_input_sha256', 'recipe_result_sha256', 'recipe_stop_proof_sha256', 'timeout_seconds', 'sha256'}
        and plan['schema'] == SCHEMA and plan['image'] == image and plan['sha256'] == expected_sha256,
        'manim_resource_job_binding_mismatch', 409)
    expected = freeze_resource_job(plan['request'], **plan['scope'], image=image,
        recipe_input_sha256=plan['recipe_input_sha256'], recipe_result_sha256=plan['recipe_result_sha256'],
        recipe_stop_proof_sha256=plan['recipe_stop_proof_sha256'], timeout_seconds=plan['timeout_seconds'])
    require(plan == expected, 'manim_resource_job_binding_mismatch', 409)
    return deepcopy(plan)


def resource_job_resources(plan, *, expected_sha256, image, namespace, pvc_name, operation_key):
    plan = validate_resource_job(plan, expected_sha256=expected_sha256, image=image)
    require(all(type(item) is str and _KUBE.fullmatch(item) for item in (namespace, pvc_name))
        and type(operation_key) is str and _SHA.fullmatch(operation_key), 'manim_resource_job_identity_invalid')
    name = 'manim-' + operation_key[:24] + '-' + expected_sha256[:12]
    input_path = INPUT_PREFIX + '/' + expected_sha256
    output_path = OUTPUT_PREFIX + '/' + operation_key
    labels = {'app': 'video-manim-resource', LABEL + 'execution': operation_key[:32]}
    identity = dict(schema='video.manim-resource-job-binding.v1', namespace=namespace,
        pvc_name=pvc_name, name=name, plan_sha256=expected_sha256, operation_key=operation_key,
        image=image, scope=plan['scope'])
    binding = digest(identity)
    annotations = {LABEL + 'binding': binding, LABEL + 'snapshot-sha256': expected_sha256}
    security = dict(allowPrivilegeEscalation=False, readOnlyRootFilesystem=True,
        runAsNonRoot=True, runAsUser=10001, runAsGroup=10001,
        capabilities={'drop': ['ALL']}, seccompProfile={'type': 'RuntimeDefault'})
    container = dict(name='renderer', image=image, imagePullPolicy='IfNotPresent',
        command=['python', '-m', 'video_workbench.manim_resource_worker'],
        args=['--request', '/input/request.json', '--output', '/output/attempt'],
        env=[dict(name='MANIM_RESOURCE_ISOLATED_WORKER', value='1'),
            dict(name='MANIM_RESOURCE_IMAGE_DIGEST', value=image.rsplit('@', 1)[1]),
            dict(name='TMPDIR', value='/work'), dict(name='XDG_CACHE_HOME', value='/work/cache'),
            dict(name='XDG_CONFIG_HOME', value='/work/config')], securityContext=security,
        resources=dict(requests=dict(cpu='1', memory='512Mi', **{'ephemeral-storage': '256Mi'}),
            limits=dict(cpu='2', memory='2Gi', **{'ephemeral-storage': '512Mi'})),
        volumeMounts=[dict(name='data', mountPath='/input', subPath=input_path, readOnly=True),
            dict(name='data', mountPath='/output', subPath=output_path),
            dict(name='scratch', mountPath='/work'), dict(name='scratch', mountPath='/tmp')])
    job = dict(apiVersion='batch/v1', kind='Job',
        metadata=dict(name=name, namespace=namespace, labels=labels, annotations=annotations),
        spec=dict(backoffLimit=0, activeDeadlineSeconds=plan['timeout_seconds'] + 30,
            ttlSecondsAfterFinished=86400, template=dict(
                metadata=dict(labels=deepcopy(labels), annotations=deepcopy(annotations)),
                spec=dict(restartPolicy='Never', automountServiceAccountToken=False, enableServiceLinks=False,
                    securityContext=dict(runAsNonRoot=True, runAsUser=10001, runAsGroup=10001,
                        fsGroup=10001, seccompProfile={'type': 'RuntimeDefault'}),
                    nodeSelector={'kubernetes.io/arch': 'arm64'}, containers=[container],
                    volumes=[dict(name='data', persistentVolumeClaim=dict(claimName=pvc_name)),
                        dict(name='scratch', emptyDir=dict(sizeLimit='512Mi'))], terminationGracePeriodSeconds=20))))
    policy = dict(apiVersion='networking.k8s.io/v1', kind='NetworkPolicy',
        metadata=dict(name='manim-resources-deny-all', namespace=namespace),
        spec=dict(podSelector=dict(matchLabels={'app': 'video-manim-resource'}),
            policyTypes=['Ingress', 'Egress'], ingress=[], egress=[]))
    return dict(job=job, network_policy=policy, identity=identity, binding=binding,
        input_sub_path=input_path, result_sub_path=output_path,
        evidence_scope='resources_not_submitted_or_stopped')
