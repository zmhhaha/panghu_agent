"""Engine catalog/resource/history contracts, with explicitly synthetic assets.

These HTTP and freezing checks do not execute a browser or prove WebGL support.
The pinned Three digests are patched only for these small test resource bytes;
real dependency integrity and real-engine rendering have separate acceptance.
"""
from copy import deepcopy
import http.client
import json
import threading

import pytest

from video_workbench.animation_lab import AnimationLab, LabHTTPServer, load_assets
from video_workbench.animation_lab_evaluate import load_run, prepare
from video_workbench.animation_lab_capture import capture_plan
from video_workbench.animation_lab_review import build_review_request
import video_workbench.animation_lab_samples as samples
import video_workbench.animation_lab_three as three
from video_workbench.motion import sha256


BASE_ASSETS = {
    'gsap.min.js': b'/*! GSAP 3.14.2 synthetic integration resource, not executable */',
    'NotoSansCJK-Regular.ttc': b'ttcf-synthetic-integration-font',
}
OLD_CASES = {'diagram-connections', 'diagram-long-label', 'process-retry',
             'continuity-retain', 'continuity-copy'}
ENGINE_CASES = {'canvas-queue': 'canvas', 'three-layers': 'three'}
RUNTIME = dict(schema='video.motion-runtime.v1', hyperframes='0.8.149',
    hyperframes_source='synthetic_contract_fixture', node='synthetic', browser='synthetic',
    ffmpeg='synthetic', gsap='3.14.2', gsap_sha256=sha256(BASE_ASSETS['gsap.min.js']),
    font_sha256=sha256(BASE_ASSETS['NotoSansCJK-Regular.ttc']),
    npm_lock_sha256='a'*64, dpkg_sha256='b'*64, image_digest='sha256:'+'c'*64)


@pytest.fixture
def engine_assets(monkeypatch):
    names = tuple(three.THREE_ASSET_SHA256)
    assert names, 'Three compiler must declare actual local resources'
    synthetic = {name: ('// synthetic unit resource '+name).encode() for name in names}
    samples._compiled_case.cache_clear()
    monkeypatch.setattr(three, 'THREE_ASSET_SHA256',
                        {name: sha256(raw) for name, raw in synthetic.items()})
    yield {**BASE_ASSETS, **synthetic}
    samples._compiled_case.cache_clear()


class Client:
    def __init__(self, lab):
        self.lab = lab
        self.server = LabHTTPServer(lab, 0)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()
        self.host = '127.0.0.1:'+str(self.server.server_port)

    def request(self, method, path, value=None):
        headers = {}
        body = None
        if value is not None:
            headers = {'Origin': 'http://'+self.host, 'Content-Type': 'application/json',
                       'X-Animation-Lab-Token': self.lab.csrf_token}
            body = json.dumps(value).encode()
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            connection.request(method, path, body, headers)
            response = connection.getresponse()
            raw, status, metadata = response.read(), response.status, dict(response.getheaders())
        finally:
            connection.close()
        data = json.loads(raw) if metadata.get('Content-Type', '').startswith('application/json') else raw
        return status, data, metadata

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def clients(tmp_path):
    created = []
    def create(assets, name='data'):
        client = Client(AnimationLab(tmp_path/name, assets))
        created.append(client)
        return client
    yield create
    for client in reversed(created):
        client.close()


def _issue(case, *, comment='Synthetic integration annotation, not aesthetic approval'):
    return dict(case_id=case['id'], case_identity=case['identity'], variant_id='candidate',
                kind='issue', start_seconds=1, end_seconds=2,
                region=dict(x=.1, y=.2, width=.3, height=.25), tags=['motion'],
                severity='minor', comment=comment, preference=None)


def test_catalog_adds_two_genuine_engine_pairs_with_same_semantics_and_clock():
    catalog = samples.catalog()
    assert {row['id'] for row in catalog} == OLD_CASES | set(ENGINE_CASES)
    assert {row['module'] for row in catalog} == {'diagram', 'process', 'continuity', 'canvas', 'three'}
    assert all(row['status'] == 'available' for row in samples.MODULES)
    for case_id, engine in ENGINE_CASES.items():
        baseline, candidate = [samples.compile_sample(case_id, variant) for variant in ('baseline', 'candidate')]
        assert baseline['inputs'] == candidate['inputs']
        assert baseline['duration_seconds'] == candidate['duration_seconds']
        assert baseline['artifact_sha256'] != candidate['artifact_sha256']
        assert baseline['clips'][0]['files']['audio.wav'] == candidate['clips'][0]['files']['audio.wav']
        for bundle in (baseline, candidate):
            assert bundle['agent_calls'] == bundle['tts_calls'] == 0
            assert bundle['production_write'] is False
            assert bundle['audio_kind'] == 'synthetic_silence_not_narration'
            manifest = json.loads(bundle['clips'][0]['files']['manifest.json'])
            assert engine in manifest['compiler']
            assert manifest['compiler'] == bundle['compiler']
            assert all(sha256(bundle['clips'][0]['files'][name]) == digest
                       for name, digest in manifest['files'].items())


def test_missing_optional_three_resources_only_disables_three_preview(tmp_path):
    lab = AnimationLab(tmp_path/'missing', BASE_ASSETS)
    catalog = lab.get_catalog()
    for case in catalog['cases']:
        if case['id'] == 'three-layers':
            assert all(row['available'] is False and row['preview_url'] is None
                       and row['reason'] for row in case['variants'])
        else:
            assert all(row['available'] is True and row['preview_url'] for row in case['variants'])
    assert set(lab.runtime['assets']) == set(BASE_ASSETS)


def test_optional_resource_installation_preserves_old_identities_and_opinions(clients, engine_assets):
    original = clients(BASE_ASSETS)
    before = original.request('GET', '/api/catalog')[1]
    old = {row['id']: row for row in before['cases']}
    issue = _issue(old['diagram-connections'])
    assert original.request('POST', '/api/annotations', issue)[0] == 201

    installed = clients(engine_assets)
    after = installed.request('GET', '/api/catalog')[1]
    current = {row['id']: row for row in after['cases']}
    for case_id in OLD_CASES | {'canvas-queue'}:
        assert old[case_id]['identity'] == current[case_id]['identity']
    assert current['diagram-connections']['annotation_count'] == 1
    assert installed.request('GET', '/api/annotations?case_id=diagram-connections')[1]['annotations'][0]['case_identity'] == issue['case_identity']
    assert all(row['available'] for row in current['three-layers']['variants'])
    assert after['runtime'] == before['runtime']
    records = [json.loads(line) for line in installed.request('GET', '/api/export')[1].decode().splitlines()]
    snapshots = {row['snapshot']['case_id']: row['snapshot'] for row in records if row['record_type'] == 'case_snapshot'}
    assert 'engine_assets' not in snapshots['diagram-connections']['runtime']
    assert snapshots['three-layers']['runtime']['engine_assets'] == three.THREE_ASSET_SHA256


def test_corrupt_or_missing_optional_resource_cannot_claim_three_ready(tmp_path, engine_assets):
    root = tmp_path/'assets'
    root.mkdir()
    for name, raw in engine_assets.items():
        (root/name).write_bytes(raw)
    assert load_assets(root) == engine_assets
    victim = next(iter(three.THREE_ASSET_SHA256))
    (root/victim).write_bytes(b'corrupted synthetic resource')
    loaded = load_assets(root)
    assert victim not in loaded
    lab = AnimationLab(tmp_path/'corrupt-data', loaded)
    assert all(not row['available'] for row in lab.cases['three-layers']['variants'])
    assert all(row['available'] for row in lab.cases['canvas-queue']['variants'])
    (root/victim).unlink()
    assert victim not in load_assets(root)


def test_three_module_routes_use_local_verified_bytes_and_javascript_mime(clients, engine_assets):
    client = clients(engine_assets)
    case = next(row for row in client.request('GET', '/api/catalog')[1]['cases'] if row['id'] == 'three-layers')
    for variant in case['variants']:
        status, page, metadata = client.request('GET', f"/composition/three-layers/{variant['id']}/scene-1/index.html")
        assert status == 200 and metadata['Content-Type'].startswith('text/html')
        assert b'https://' not in page and b'http://' not in page
        for name in three.THREE_ASSET_SHA256:
            path = f"/composition/three-layers/{variant['id']}/scene-1/assets/{name}"
            status, raw, headers = client.request('GET', path)
            assert status == 200 and raw == engine_assets[name]
            assert headers['Content-Type'].startswith('text/javascript')
    assert client.request('GET', '/composition/three-layers/candidate/scene-1/assets/unregistered.mjs')[0] == 404


@pytest.mark.parametrize('case_id', sorted(ENGINE_CASES))
def test_engine_annotations_keep_their_exact_identity_and_export_after_restart(clients, engine_assets, case_id):
    original = clients(engine_assets)
    case = next(row for row in original.request('GET', '/api/catalog')[1]['cases'] if row['id'] == case_id)
    issue = _issue(case)
    status, result, _ = original.request('POST', '/api/annotations', issue)
    assert status == 201
    saved = result['annotation']
    restarted = clients(engine_assets)
    rows = restarted.request('GET', '/api/annotations?case_id='+case_id)[1]['annotations']
    assert [row['id'] for row in rows] == [saved['id']]
    assert rows[0]['case_identity'] == case['identity']
    records = [json.loads(line) for line in restarted.request('GET', '/api/export')[1].decode().splitlines()]
    snapshots = [row['snapshot'] for row in records if row['record_type'] == 'case_snapshot']
    assert any(row['case_id'] == case_id and row['identity'] == case['identity'] for row in snapshots)
    stale = dict(issue, case_identity='f'*64)
    assert restarted.request('POST', '/api/annotations', stale)[0] == 409


@pytest.mark.parametrize('case_id', sorted(ENGINE_CASES))
def test_engine_run_preparation_freezes_actual_compiler_bytes_without_dispatch(tmp_path, engine_assets, case_id):
    assets_dir = tmp_path/'assets'
    assets_dir.mkdir()
    for name, raw in engine_assets.items():
        (assets_dir/name).write_bytes(raw)
    root = tmp_path/case_id
    run = prepare(root, deepcopy(RUNTIME), case_id=case_id, engine_assets_dir=assets_dir)
    loaded, bundles = load_run(root)
    assert loaded['identity'] == run['identity']
    assert run['limits']['captures'] == 2 and run['production_adopted'] is False
    assert not (root/'model-attempt.json').exists()
    assert not (root/'technical.json').exists()
    for bundle in bundles:
        expected = samples.compile_sample(case_id, bundle['variant_id'])
        assert bundle['artifact_sha256'] == expected['artifact_sha256']
        for clip in bundle['clips']:
            assert clip['files'] == expected['clips'][0]['files']
            directory = root/'inputs'/bundle['variant_id']/clip['id']
            request = json.loads((directory/'capture-request.json').read_bytes())
            assert request['compiler'] == bundle['compiler']
            assert request['engine_observer_sha256'] == sha256((root/'overlay'/'engine_observe.mjs').read_bytes())
            assert all(sha256((directory/name).read_bytes()) == digest for name, digest in request['files'].items())
    if case_id == 'three-layers':
        assert all((root/'overlay'/name).read_bytes() == engine_assets[name] for name in three.THREE_ASSET_SHA256)


@pytest.mark.parametrize('state', ['unspecified', 'missing', 'corrupt'])
def test_three_prepare_rejects_missing_or_corrupt_engine_assets_before_creating_run(tmp_path, engine_assets, state):
    root, assets_dir = tmp_path/'run', tmp_path/'assets'
    assets_dir.mkdir()
    for name, raw in engine_assets.items():
        (assets_dir/name).write_bytes(raw)
    victim = next(iter(three.THREE_ASSET_SHA256))
    if state == 'missing':
        (assets_dir/victim).unlink()
    elif state == 'corrupt':
        (assets_dir/victim).write_bytes(b'wrong bytes')
    with pytest.raises(ValueError, match='animation_evaluation_engine_assets_'):
        prepare(root, deepcopy(RUNTIME), case_id='three-layers',
                engine_assets_dir=None if state == 'unspecified' else assets_dir)
    assert not root.exists()


def test_changed_three_asset_is_rejected_during_capture_plan_without_docker(tmp_path, engine_assets):
    assets_dir = tmp_path/'assets'
    assets_dir.mkdir()
    for name, raw in engine_assets.items():
        (assets_dir/name).write_bytes(raw)
    root = tmp_path/'frozen-run'
    prepare(root, deepcopy(RUNTIME), case_id='three-layers', engine_assets_dir=assets_dir)
    directory = root/'inputs'/'candidate'/'scene-1'
    image = 'test.invalid/animation:latest@'+RUNTIME['image_digest']
    plan = capture_plan(directory, RUNTIME, image, root/'overlay')
    assert plan['network'] == 'none' and plan['pull'] == 'never'
    assert plan['automatic_retry'] is False
    victim = next(iter(three.THREE_ASSET_SHA256))
    (root/'overlay'/victim).write_bytes(b'tampered after prepare')
    with pytest.raises(ValueError, match='animation_lab_executor_engine_asset_mismatch'):
        capture_plan(directory, RUNTIME, image, root/'overlay')
    assert not (directory/'capture-start.json').exists()
    assert not (directory/'capture-work').exists()


@pytest.mark.parametrize('engine', ['canvas', 'three'])
def test_review_contract_accepts_new_engine_module_without_dispatch(engine):
    # Explicit descriptor-only fixture; no image or model opinion is invented.
    request = build_review_request(case_identity='a'*64, module=engine,
        variants=[dict(id=label, artifact_sha256=digest*64, duration_seconds=16)
                  for label, digest in [('baseline', 'b'), ('candidate', 'c')]],
        frames=[dict(id=label+':scene-1:synthetic', variant_id=label, clip_id='scene-1',
            time_seconds=1, sha256=digest*64, mime_type='image/png', width=1280, height=720)
            for label, digest in [('baseline', 'd'), ('candidate', 'e')]],
        runtime_identity='f'*64, evidence_identity='1'*64)
    assert request['public']['module'] == engine
    assert {row['id'] for row in request['private']['variants'].values()} == {'baseline', 'candidate'}
