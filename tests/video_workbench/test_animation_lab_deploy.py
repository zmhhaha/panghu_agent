"""Offline packaging and isolation checks; synthetic assets are not render proof."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from video_workbench.deploy import build_animation_lab as build
from video_workbench.deploy.animation_lab import Settings, main, render


@pytest.fixture
def settings():
    return Settings(image='arm-cluster-master:5000/video-animation-lab:latest',
                    image_digest='sha256:'+'a'*64, release_id='reviewed-source-123',
                    developer_users=['developer-subject'], storage_class='ceph-rbd')


def by_kind(document, kind):
    return next(item for item in document['items'] if item['kind'] == kind)


def test_lab_is_an_independent_single_writer_without_business_credentials(settings):
    document = render(settings)
    assert {item['kind'] for item in document['items']} == {
        'Deployment', 'Service', 'PersistentVolumeClaim', 'NetworkPolicy'}
    assert all(item['metadata']['name'].startswith('video-animation-lab') for item in document['items'])
    deployment = by_kind(document, 'Deployment')
    assert deployment['spec']['replicas'] == 1 and deployment['spec']['strategy'] == {'type': 'Recreate'}
    spec = deployment['spec']['template']['spec']
    assert not spec['automountServiceAccountToken']
    assert spec['securityContext']['runAsUser'] == spec['securityContext']['fsGroup'] == 10001
    container, = spec['containers']
    assert container['securityContext'] == {'readOnlyRootFilesystem': True,
        'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}}
    assert container['image'] == settings.image+'@'+settings.image_digest
    assert container['imagePullPolicy'] == 'Always'
    assert container['command'] == ['python', '-m', 'video_workbench.animation_lab']
    args = container['args']
    assert args[args.index('--developer-user')+1] == 'developer-subject'
    assert args[args.index('--public-origin')+1] == 'https://video-service.panghuer.top'
    assert args[args.index('--base-path')+1] == '/dev/animations'
    assert '--proxy-mode' in args and args[args.index('--listen')+1] == '0.0.0.0'
    assert not container.get('env') and not container.get('envFrom')
    assert all(not {'secret', 'projected', 'hostPath'} & set(volume) for volume in spec['volumes'])
    labels = deployment['spec']['template']['metadata']['annotations']
    assert labels['org.opencontainers.image.digest'] == settings.image_digest
    assert labels['org.opencontainers.image.revision'] == settings.release_id
    assert by_kind(document, 'Service')['spec']['type'] == 'ClusterIP'


def test_data_and_frozen_evaluations_have_separate_persistence_and_write_boundaries(settings):
    document = render(settings)
    claims = {item['metadata']['name']: item['spec'] for item in document['items']
              if item['kind'] == 'PersistentVolumeClaim'}
    assert set(claims) == {'video-animation-lab-data', 'video-animation-lab-evaluations'}
    for claim in claims.values():
        assert claim['accessModes'] == ['ReadWriteOnce'] and claim['storageClassName'] == 'ceph-rbd'
        assert claim['resources']['requests']['storage'] == '2Gi'
    container = by_kind(document, 'Deployment')['spec']['template']['spec']['containers'][0]
    mounts = {item['mountPath']: item for item in container['volumeMounts']}
    assert not mounts['/data'].get('readOnly', False)
    assert mounts['/evaluations']['readOnly'] is True
    assert all(probe['httpGet']['path'] == '/healthz'
               for key, probe in container.items() if key.endswith('Probe'))
    assert container['startupProbe']['failureThreshold'] * container['startupProbe']['periodSeconds'] >= 120


def test_network_policy_only_admits_selected_proxy_and_denies_outbound(settings):
    policy = by_kind(render(settings), 'NetworkPolicy')['spec']
    assert set(policy['policyTypes']) == {'Ingress', 'Egress'} and policy['egress'] == []
    rule, = policy['ingress']
    assert rule == {'from': [{
        'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'oauth'}},
        'podSelector': {'matchLabels': {'app': 'oauth2-proxy-video-workbench'}},
    }], 'ports': [{'protocol': 'TCP', 'port': 8766}]}


def test_developer_identity_length_matches_the_runtime_boundary(settings):
    accepted = 'a' * 200
    document = render(replace(settings, developer_users=[accepted]))
    args = by_kind(document, 'Deployment')['spec']['template']['spec']['containers'][0]['args']
    assert args[args.index('--developer-user') + 1] == accepted
    with pytest.raises(ValueError, match='allowlist required'):
        render(replace(settings, developer_users=['a' * 201]))


@pytest.mark.parametrize('changes', [
    dict(developer_users=[]), dict(developer_users='developer'), dict(developer_users=['']),
    dict(developer_users=['a', 'a']), dict(developer_users=['a,b']), dict(developer_users=['a\nb']),
    dict(developer_users=['*']), dict(developer_users=[' leading']),
    dict(image='registry.example/lab:v1'), dict(image_digest='latest'),
    dict(release_id='bad release'), dict(storage_class='../../bad'),
    dict(public_origin='http://video-service.panghuer.top'),
    dict(public_origin='https://user:pass@video-service.panghuer.top'),
    dict(public_origin='https://video-service.panghuer.top/path'),
    dict(base_path='/dev/../app'), dict(base_path='/'), dict(data_size='0Gi'),
    dict(evaluations_size='all'), dict(proxy_labels={}),
])
def test_invalid_deployment_settings_fail_before_output(settings, changes):
    with pytest.raises(ValueError):
        render(replace(settings, **changes))


def test_cli_requires_explicit_developer_identity_and_preserves_existing_output(tmp_path, settings):
    output = tmp_path/'resources.json'
    arguments = ['--image', settings.image, '--image-digest', settings.image_digest,
                 '--release-id', settings.release_id, '--storage-class', settings.storage_class,
                 '--output', str(output)]
    with pytest.raises(SystemExit):
        main(arguments)
    assert not output.exists()
    main([*arguments, '--developer-user', 'developer-subject', '--proxy-label', 'app=router'])
    assert len(json.loads(output.read_text('utf8'))['items']) == 5
    old = output.read_bytes()
    with pytest.raises(FileExistsError):
        main([*arguments, '--developer-user', 'developer-subject'])
    assert output.read_bytes() == old


@pytest.fixture
def source_and_assets(tmp_path, monkeypatch):
    source, assets = tmp_path/'source', tmp_path/'assets'
    assets.mkdir()
    for name in build.SOURCE_FILES:
        path = source/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((build.ROOT/name).read_bytes())
    for private in ('video_workbench/rendered/private.py', 'video_workbench/.env',
                    'video_workbench/state.db', 'video_workbench/private.py',
                    'agent_service/config.production.json', 'agent_service/private.py'):
        path = source/private
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'PRIVATE_RUNTIME_STATE')
    # Deliberately tiny sentinels verify packaging contracts, not engine assets.
    values = {name: ('SYNTHETIC_ASSET_'+name).encode() for name in build.ASSET_SHA256}
    for name, raw in values.items():
        (assets/name).write_bytes(raw)
    (assets/'private.json').write_bytes(b'PRIVATE_RUNTIME_STATE')
    monkeypatch.setattr(build, 'ASSET_SHA256', {name: hashlib.sha256(raw).hexdigest() for name, raw in values.items()})
    return source, assets


def test_context_is_deterministic_and_exact_even_beside_runtime_secrets(source_and_assets, tmp_path):
    source, assets = source_and_assets
    one, two = tmp_path/'one.tar', tmp_path/'two.tar'
    first = build.context_archive(source, assets, one)
    for name in build.SOURCE_FILES:
        os.utime(source/name, (100, 100))
    second = build.context_archive(source, assets, two)
    assert first == second and one.read_bytes() == two.read_bytes()
    with tarfile.open(one) as archive:
        assert set(archive.getnames()) == build.SOURCE_FILES | {'build-assets/'+name for name in build.ASSET_SHA256}
        assert all(item.isfile() and item.mode == 0o644 and item.mtime == 0 for item in archive)
        assert b'PRIVATE_RUNTIME_STATE' not in b''.join(archive.extractfile(item).read() for item in archive.getmembers())
    assert first['context_sha256'] == hashlib.sha256(one.read_bytes()).hexdigest()
    assert first['network_calls'] == first['builds'] == first['deployments'] == 0


@pytest.mark.parametrize('name', list(build.ASSET_SHA256))
def test_tampered_pinned_asset_cannot_enter_context(source_and_assets, tmp_path, name):
    source, assets = source_and_assets
    (assets/name).write_bytes(b'not the reviewed bytes')
    output = tmp_path/'bad.tar'
    with pytest.raises(ValueError, match='asset_digest_mismatch'):
        build.context_archive(source, assets, output)
    assert not output.exists()


def test_context_never_overwrites_existing_release_material(source_and_assets, tmp_path):
    source, assets = source_and_assets
    output = tmp_path/'existing.tar'
    output.write_bytes(b'previous evidence')
    with pytest.raises(ValueError, match='context_exists'):
        build.context_archive(source, assets, output)
    assert output.read_bytes() == b'previous evidence'


def test_exact_context_contains_all_runtime_imports_without_business_application(source_and_assets, tmp_path):
    source, assets = source_and_assets
    archive = tmp_path/'import.tar'
    build.context_archive(source, assets, archive)
    extracted = tmp_path/'isolated-imports'
    with tarfile.open(archive) as stream:
        stream.extractall(extracted, filter='data')
    code = ('import sys; sys.path.insert(0,sys.argv[1]); '
            'import video_workbench.animation_lab, video_workbench.animation_lab_evaluation_view, '
            'video_workbench.animation_lab_engine_review, video_workbench.animation_lab_effects_review, '
            'video_workbench.process_continuity_review; '
            'from video_workbench.animation_lab_samples import catalog; '
            'assert len(catalog())==39; '
            "assert 'video_workbench.app' not in sys.modules")
    result = subprocess.run([sys.executable, '-I', '-c', code, str(extracted)],
                            capture_output=True, check=False, timeout=30)
    assert result.returncode == 0, result.stderr.decode('utf8', 'replace')


def test_docker_context_ignore_admits_exact_files_and_no_rendered_tree():
    raw = (build.ROOT/(build.DOCKERFILE+'.dockerignore')).read_text('utf8').splitlines()
    assert '**' in raw
    exact = {line[1:] for line in raw if line.startswith('!') and not line.endswith('/')}
    assert exact == build.SOURCE_FILES | {'build-assets/'+name for name in build.ASSET_SHA256}
    assert not any('rendered' in name or name.endswith('.db') or '.env' in name for name in exact)
    docker = (build.ROOT/build.DOCKERFILE).read_text('utf8')
    assert 'USER 10001:10001' in docker
    assert 'apt-get' not in docker and 'npm' not in docker and 'COPY . ' not in docker
    assert (build.ROOT/'video_workbench/requirements-animation-lab.txt').read_text('utf8').splitlines() == [
        'pydantic==2.12.5', 'httpx==0.28.1']
