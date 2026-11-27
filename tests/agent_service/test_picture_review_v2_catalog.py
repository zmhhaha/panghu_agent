"""A new classified-review candidate is not old profile acceptance or readiness."""
from copy import deepcopy
import hashlib

import pytest

from agent_service.catalog import CATALOG_KEYS
from agent_service.deploy.acceptance_probe import candidate_config
from agent_service.deploy.animation_worker_host import project_animation_worker_host
from agent_service.deploy.picture_review_v2_catalog import (CALLER, measure_picture_review_v2_bundle,
    prepare_picture_review_v2_catalog)
from agent_service.deploy.video_catalog import VideoCatalogError, registry
from agent_service.picture_learning_tools import environment_revision, verified_extensions
from agent_service.profiles import video_picture_review_v2 as protocol
from agent_service.registry import ToolEnvironment
from test_animation_review_catalog import IMAGE, model, public_config
from test_picture_learning_catalog import prepare as prepare_legacy
from test_candidate_scenario import write_scenario, synthetic_source_root_owner


def prepare(current=None,**changes):
    manifests,_=measure_picture_review_v2_bundle()
    values=dict(worker_image=IMAGE,resource_manifests=manifests,source_pool_id='existing-pool',review_model_policy=model())
    values.update(changes)
    return prepare_picture_review_v2_catalog(public_config() if current is None else current,**values)


def test_new_candidate_preserves_all_old_roles_and_separately_pins_original_image_tools():
    current,_,_=prepare_legacy();before=deepcopy(current)
    merged,hosts,receipt=prepare(current)
    assert current==before and set(hosts)=={protocol.PROFILE}
    for key in {*CATALOG_KEYS,'callers','kubernetes_workers'}:
        assert merged[key][:len(before[key])]==before[key]
    assert merged['callers'][-1]['caller']==CALLER and merged['callers'][-1]['profiles']==[protocol.PROFILE]
    assert receipt['model_requests']==0 and receipt['acceptance_created'] is False
    host=hosts[protocol.PROFILE];parsed,policy,binding=candidate_config(host)
    assert not parsed.profile.enabled and not policy.enabled and binding.acceptance is None
    resolved=registry(merged);contract=resolved.contract(protocol.PROFILE,'1')
    assert contract.max_timeout_seconds==3600
    environment=resolved.environment(binding.tool_environment.name,binding.tool_environment.revision)
    assert environment.name==protocol.ENVIRONMENT_NAME
    assert environment.revision==environment_revision(environment.model_dump(mode='json'))
    assert environment.capabilities==('video-picture-learning-review-output.v2',)
    assert hashlib.sha256(verified_extensions()[0].read_bytes()).hexdigest()=='b8097375c1921611e922dcda3ef3e905170947d13f79e1c2ea1445f4421be46e'
    full=dict(deepcopy(host),execution_catalog={k:deepcopy(merged[k]) for k in CATALOG_KEYS})
    projected=project_animation_worker_host(full)
    assert all(len(rows)==1 for rows in projected['execution_catalog'].values())


@pytest.mark.parametrize('change',['vision','image','manifest','old_environment','old_output'])
def test_unpinned_or_old_identity_cannot_claim_new_contract(change):
    if change=='vision':
        with pytest.raises(VideoCatalogError,match='vision_required'):prepare(review_model_policy=model(vision=False))
    elif change=='image':
        with pytest.raises(VideoCatalogError,match='latest_digest_required'):prepare(worker_image='registry.internal/worker:latest')
    elif change=='manifest':
        manifests,_=measure_picture_review_v2_bundle();manifests[protocol.BUNDLE_REVISION]['local_files'][0]['sha256']='a'*64
        with pytest.raises(VideoCatalogError,match='bundle_changed'):prepare(resource_manifests=manifests)
    else:
        _,hosts,_=prepare();env=hosts[protocol.PROFILE]['execution_catalog']['tool_environments'][0]
        if change=='old_environment':env['name']='tools-pi-picture-learning'
        else:env['capabilities']=['video-picture-learning-review-output.v1']
        env['revision']=environment_revision(env)
        with pytest.raises(ValueError):ToolEnvironment.model_validate(env)


def test_old_admission_helper_refuses_new_profile_and_bundle_tampering_fails(monkeypatch,tmp_path):
    from agent_service.deploy import picture_learning_acceptance as old_admission
    from agent_service.contracts import ServiceError
    with pytest.raises(ServiceError,match='profile_not_allowed'):old_admission._protocol(protocol.PROFILE)
    import shutil
    root=tmp_path/'bundle';shutil.copytree(protocol.BUNDLE_ROOT,root)
    (root/'CONTRACT.md').write_bytes(b'changed')
    monkeypatch.setattr(protocol,'BUNDLE_ROOT',root)
    with pytest.raises(ServiceError,match='bundle_integrity'):protocol.verified_bundle()


def test_candidate_scenario_validates_original_new_profile_inputs_without_business_creation(tmp_path):
    from agent_service.contracts import canonical_bytes, digest
    from agent_service.deploy import candidate_scenario
    from agent_service.worker_entry import HostConfig, host_policy
    from test_picture_review_v2_profile import document, PNG
    _,hosts,_=prepare();value=document()
    sources={protocol.INPUT_NAME:canonical_bytes(value),
        **{row['file_name']:PNG for row in value['observation']['frames']}}
    path,root,_=write_scenario(tmp_path/'classified-candidate',protocol.PROFILE,
        dict(input_sha256=digest(value)),sources)
    loaded=candidate_scenario.load_scenario(path,root,None,
        host_policy(HostConfig.model_validate(hosts[protocol.PROFILE])))
    assert loaded.files==sources and loaded.metadata['production_acceptance_created'] is False
    assert {p.name:p.read_bytes() for p in root.iterdir()}==sources


def test_worker_installs_exact_picture_extension_for_new_profile_without_running_a_model(monkeypatch,tmp_path):
    from agent_service import worker_entry as worker
    from agent_service import resources
    from agent_service.registry import binding_fingerprint
    from agent_service.worker_entry import CapsuleScope, HostConfig, HostSecrets, host_policy
    _,hosts,_=prepare();host=hosts[protocol.PROFILE];catalog=host['execution_catalog']
    c,b,e,x=(catalog[key][0] for key in ('task_contracts','capability_bundles','tool_environments','execution_bindings'))
    # Synthetic test-only grant: it does not create an operator admission report.
    x['acceptance']=dict(combination_sha256=binding_fingerprint(c,b,e,x),
        suite='video.picture-learning-review-contract.v2',report_sha256='a'*64)
    catalog['routes'][0]['enabled']=True;host['profile']['enabled']=True
    config=HostConfig.model_validate(host);policy=host_policy(config)
    attempt,pod='00000000-0000-0000-0000-000000000001','00000000-0000-0000-0000-000000000002'
    scope=CapsuleScope(attempt_id=attempt,pod_uid=pod,request_sha256='b'*64,
        agent_uid=1000001,preview_uid=1000002,execution_binding_sha256=policy.execution_binding.sha256)
    secrets=HostSecrets(worker_token='synthetic-worker-token-0000000000000',receipt_key_hex='c'*64,
        model_proxy_token='synthetic-proxy-token-00000000000000',model_proxy_attempt_id=attempt,
        model_proxy_request_sha256='b'*64,model_proxy_expires_at=200.)
    assert worker.build_pi_driver(config,secrets,scope,object(),acceptance=False,now=lambda:100).ready is False
    monkeypatch.setattr(resources,'measured_package',lambda *args:(protocol.BUNDLE_ROOT,
        protocol.BUNDLE_ROOT/'manifest.json',protocol.BUNDLE_MANIFEST_SHA256,(),()))
    captured=[]
    class Runtime:
        def __init__(self,settings,*,profile):captured.append((settings,profile))
        def health(self):return dict(ready=False,fixture_mode=False)
    monkeypatch.setattr(worker,'PiRuntime',Runtime)
    driver=worker.build_pi_driver(config,secrets,scope,object(),acceptance=True,now=lambda:100)
    assert driver.ready is False and len(captured)==1
    settings,implementation=captured[0]
    assert implementation.extension_paths==verified_extensions()
    assert implementation.allowed_tools==('read_inputs','skill_search','skill_read','write')
    assert settings.skill_bundle_revision==protocol.BUNDLE_REVISION and settings.model_max_tokens==8192
