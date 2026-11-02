"""Generate a separate, offline, credential-free experimental render Job.

Only trusted compiler output is admitted: it is recompiled and byte-compared
before freezing the immutable ConfigMap. This module never contacts Kubernetes.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

from video_workbench.motion import compile_project
from video_workbench.motion_snapshot import _runtime as validate_runtime_fingerprint

NAMESPACE = 'video-motion-experiment'
FILES = ('index.html', 'plan.json', 'beats.json', 'alignment.json', 'timeline.json', 'manifest.json', 'audio.wav')
INIT = '''import base64,hashlib,io,os,tarfile
from pathlib import Path
raw=Path('/bundle/input.tgz').read_bytes()
if hashlib.sha256(raw).hexdigest()!=os.environ['MOTION_BUNDLE_SHA256']: raise RuntimeError('bundle_sha')
allowed={'index.html','plan.json','beats.json','alignment.json','timeline.json','manifest.json','audio.wav'}
for name in ('input','work','tmp'): Path('/scratch/'+name).mkdir(exist_ok=True)
with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
 members=archive.getmembers()
 if len(members)!=len(allowed) or {m.name for m in members}!=allowed or any(not m.isfile() or m.size>8*1024*1024 for m in members): raise RuntimeError('bundle_members')
 for m in members: Path('/scratch/input/'+m.name).write_bytes(archive.extractfile(m).read())
'''


def _ordinary(path, limit):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError('input_not_regular_or_large')
    return path.read_bytes()


def freeze_input(directory, *, allow_unbound_experiment=False, image_digest=None):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('input_directory_invalid')
    files = {name: _ordinary(directory / name, 8 * 1024 * 1024 if name == 'audio.wav' else 1024 * 1024)
             for name in FILES}
    manifest, timeline = (json.loads(files[name]) for name in ('manifest.json', 'timeline.json'))
    fingerprint = manifest.get('runtime_fingerprint')
    if fingerprint is None:
        if allow_unbound_experiment is not True or 'runtime_fingerprint' not in manifest:
            raise ValueError('runtime_fingerprint_required')
    else:
        validate_runtime_fingerprint(fingerprint)
        if image_digest is not None and fingerprint['image_digest'] != image_digest:
            raise ValueError('runtime_image_digest_mismatch')
    expected = compile_project(json.loads(files['plan.json']), json.loads(files['beats.json']),
                               json.loads(files['alignment.json']), animated=manifest['animated'],
                               leading_seconds=timeline['leading_seconds'], trailing_seconds=timeline['trailing_seconds'],
                               runtime_fingerprint=manifest['runtime_fingerprint'])
    if any(expected[name] != files[name] for name in expected):
        raise ValueError('input_not_compiler_output')
    if hashlib.sha256(files['audio.wav']).hexdigest() != manifest['audio_sha256']:
        raise ValueError('audio_binding_mismatch')
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode='w') as archive:
        for name in FILES:
            member = tarfile.TarInfo(name)
            member.size = len(files[name])
            member.mode = 0o444
            archive.addfile(member, io.BytesIO(files[name]))
    compressed = gzip.compress(payload.getvalue(), mtime=0)
    if len(compressed) > 900 * 1024:
        raise ValueError('configmap_payload_limit')
    return dict(payload=compressed, bundle_sha256=hashlib.sha256(compressed).hexdigest(),
                manifest_sha256=hashlib.sha256(files['manifest.json']).hexdigest(),
                unbound_experiment=fingerprint is None)


def resources(directory, *, image, release_id, name='motion-technical-a', seconds=300,
              allow_unbound_experiment=False):
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,48}[a-z0-9])?', name):
        raise ValueError('job_name_invalid')
    if not re.fullmatch(r'[A-Za-z0-9./:_-]+:latest@sha256:[0-9a-f]{64}', image):
        raise ValueError('immutable_latest_image_required')
    if type(seconds) is not int or seconds not in (300, 600):
        raise ValueError('experiment_budget_invalid')
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,80}', release_id):
        raise ValueError('release_id_invalid')
    image_digest = image.rsplit('@', 1)[1]
    frozen = freeze_input(directory, allow_unbound_experiment=allow_unbound_experiment,
                          image_digest=image_digest)
    labels = {'app': 'video-motion-renderer', 'experiment': name}
    meta = dict(namespace=NAMESPACE, labels=labels)
    security = dict(allowPrivilegeEscalation=False, readOnlyRootFilesystem=True,
                    runAsNonRoot=True, runAsUser=10001, runAsGroup=10001,
                    capabilities={'drop': ['ALL']}, seccompProfile={'type': 'RuntimeDefault'})
    init = dict(name='prepare', image=image, command=['python3', '-c', INIT], securityContext=security,
                env=[dict(name='MOTION_BUNDLE_SHA256', value=frozen['bundle_sha256'])],
                resources=dict(requests=dict(cpu='50m', memory='64Mi'), limits=dict(cpu='250m', memory='256Mi')),
                volumeMounts=[dict(name='bundle', mountPath='/bundle', readOnly=True),
                              dict(name='scratch', mountPath='/scratch')])
    renderer = dict(name='renderer', image=image, command=['python3', '/opt/renderer/experiment.py'],
                    securityContext=security,
                    env=[dict(name='MOTION_MANIFEST_SHA256', value=frozen['manifest_sha256']),
                         dict(name='MOTION_IMAGE_DIGEST', value=image_digest),
                         *([dict(name='MOTION_ALLOW_UNBOUND_EXPERIMENT', value='1')]
                           if frozen['unbound_experiment'] else []),
                         dict(name='MOTION_TIMEOUT_SECONDS', value=str(seconds))],
                    resources=dict(requests=dict(cpu='1', memory='2Gi', **{'ephemeral-storage': '512Mi'}),
                                   limits=dict(cpu='2', memory='4Gi', **{'ephemeral-storage': '4Gi'})),
                    volumeMounts=[dict(name='scratch', mountPath='/input', subPath='input', readOnly=True),
                                  dict(name='scratch', mountPath='/work', subPath='work'),
                                  dict(name='scratch', mountPath='/tmp', subPath='tmp')])
    spec = dict(restartPolicy='Never', automountServiceAccountToken=False, enableServiceLinks=False,
                securityContext=dict(runAsNonRoot=True, runAsUser=10001, runAsGroup=10001, fsGroup=10001),
                nodeSelector={'kubernetes.io/arch': 'arm64'}, initContainers=[init], containers=[renderer],
                volumes=[dict(name='bundle', configMap=dict(name=name, defaultMode=0o444)),
                         dict(name='scratch', emptyDir=dict(sizeLimit='4Gi'))], terminationGracePeriodSeconds=10)
    job = dict(apiVersion='batch/v1', kind='Job', metadata=dict(name=name, **meta),
               spec=dict(backoffLimit=0, activeDeadlineSeconds=seconds + 90, ttlSecondsAfterFinished=86400,
                         template=dict(metadata=dict(labels=labels, annotations={
                             'video-workbench.panghuer.top/release-id': release_id,
                             'video-workbench.panghuer.top/input-sha256': frozen['manifest_sha256']}), spec=spec)))
    return dict(apiVersion='v1', kind='List', items=[
        dict(apiVersion='v1', kind='Namespace', metadata=dict(name=NAMESPACE)),
        dict(apiVersion='networking.k8s.io/v1', kind='NetworkPolicy', metadata=dict(name='deny-all', **meta),
             spec=dict(podSelector={}, policyTypes=['Ingress', 'Egress'], ingress=[], egress=[])),
        dict(apiVersion='v1', kind='ConfigMap', metadata=dict(name=name, **meta), immutable=True,
             binaryData={'input.tgz': base64.b64encode(frozen['payload']).decode()}), job])
