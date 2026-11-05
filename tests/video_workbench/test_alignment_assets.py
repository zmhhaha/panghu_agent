"""Complete asset identity cannot pass on a partial, changed or unsafe closure."""
from copy import deepcopy
import hashlib
import io
import json
from urllib.parse import parse_qs, urlparse

import pytest

from agent_service.contracts import ServiceError, canonical_bytes
from video_workbench import alignment_assets as assets


@pytest.fixture
def closure(monkeypatch):
    names = ['.gitattributes', 'README.md', 'config.json', 'generation_config.json',
             'merges.txt', 'model.safetensors', 'preprocessor_config.json',
             'special_tokens_map.json', 'tokenizer_config.json', 'vocab.json',
             'chat_template.json']
    bodies = {name: ('fixture-only:' + name).encode() for name in names}
    revision = 'a' * 40
    files = [dict(Name=name, Path=name, Type='blob', Size=len(bodies[name]),
                  Sha256=hashlib.sha256(bodies[name]).hexdigest(), Revision=revision)
             for name in names]
    monkeypatch.setattr(assets, 'WEIGHT_BYTES', len(bodies['model.safetensors']))
    monkeypatch.setattr(assets, 'WEIGHT_SHA256', hashlib.sha256(bodies['model.safetensors']).hexdigest())
    monkeypatch.setattr(assets, 'WEIGHT_REVISION', revision)
    monkeypatch.setattr(assets, 'EXPECTED_TOTAL', sum(map(len, bodies.values())))
    payload = dict(Code=200, Success=True, Data=dict(Files=files))
    return payload, bodies


def opener_for(payload, bodies, calls):
    def opener(url, timeout):
        assert timeout == 30
        calls.append(url)
        if url == assets.METADATA_URL:
            return io.BytesIO(json.dumps(payload).encode())
        parsed = urlparse(url)
        assert parsed.scheme == 'https' and parsed.netloc == 'modelscope.cn'
        query = parse_qs(parsed.query)
        assert query['Revision'] == ['a' * 40]
        return io.BytesIO(bodies[query['FilePath'][0]])
    return opener


def test_freezes_complete_metadata_and_rejects_weight_or_total_change(closure):
    payload, _ = closure
    manifest = assets.freeze_metadata(payload)
    assert len(manifest['files']) == 11
    assert manifest['files'] == sorted(manifest['files'], key=lambda r: r['name'])
    assert all(set(row) == {'name', 'size', 'sha256', 'revision'} for row in manifest['files'])
    changed = deepcopy(payload)
    changed['Data']['Files'][5]['Sha256'] = 'b' * 64
    with pytest.raises(ServiceError, match='weight_identity_changed'):
        assets.freeze_metadata(changed)
    changed = deepcopy(payload)
    changed['Data']['Files'][0]['Size'] += 1
    with pytest.raises(ServiceError, match='closure_changed'):
        assets.freeze_metadata(changed)


def test_download_and_reuse_rehash_the_entire_closure(tmp_path, closure):
    payload, bodies = closure
    calls = []
    root = tmp_path / 'model'
    receipt = assets.download_assets(root, opener=opener_for(payload, bodies, calls))
    assert len(calls) == 12
    assert receipt['file_count'] == 11 and len(receipt['downloaded']) == 11
    manifest = json.loads((root / assets.MANIFEST_NAME).read_bytes())
    assert receipt['manifest_sha256'] == hashlib.sha256(canonical_bytes(manifest)).hexdigest()
    assert assets.verify_assets(root)['manifest_sha256'] == receipt['manifest_sha256']
    calls.clear()
    again = assets.download_assets(root, opener=opener_for(payload, bodies, calls))
    assert not calls and not again['downloaded'] and len(again['reused']) == 11
    assert again['manifest_sha256'] == receipt['manifest_sha256']


@pytest.mark.parametrize('mutation', [
    lambda rows: rows[0].update(Path='../config.json', Name='../config.json'),
    lambda rows: rows[0].update(Path='/config.json', Name='/config.json'),
    lambda rows: rows[0].update(Path='sub/config.json', Name='sub/config.json'),
    lambda rows: rows[0].update(Path='config.py', Name='config.py'),
    lambda rows: rows[0].update(Path='model.bin', Name='model.bin'),
    lambda rows: rows[0].update(Size=True),
    lambda rows: rows[0].update(Size=-1),
    lambda rows: rows[0].update(Sha256='bad'),
    lambda rows: rows[0].update(Revision='master'),
    lambda rows: rows[0].update(Type='tree'),
    lambda rows: rows.append(dict(rows[0])),
])
def test_metadata_rejects_unsafe_or_unbounded_files(closure, mutation):
    payload, _ = closure
    mutation(payload['Data']['Files'])
    with pytest.raises(ServiceError):
        assets.freeze_metadata(payload)


def test_corrupt_existing_file_is_not_silently_overwritten(tmp_path, closure):
    payload, bodies = closure
    calls = []
    assets.download_assets(tmp_path, opener=opener_for(payload, bodies, calls))
    (tmp_path / 'config.json').write_bytes(b'corrupt')
    calls.clear()
    with pytest.raises(ServiceError, match='integrity_mismatch'):
        assets.download_assets(tmp_path, opener=opener_for(payload, bodies, calls))
    assert not calls and (tmp_path / 'config.json').read_bytes() == b'corrupt'
    with pytest.raises(ServiceError, match='integrity_mismatch'):
        assets.verify_assets(tmp_path)


@pytest.mark.parametrize('mode', ['truncated', 'wrong-hash', 'too-large', 'disconnect'])
def test_failed_download_keeps_partial_but_never_publishes_target(tmp_path, closure, mode):
    payload, bodies = closure
    manifest = assets.freeze_metadata(payload)
    first = manifest['files'][0]['name']
    good = opener_for(payload, bodies, [])

    class Disconnected(io.BytesIO):
        def read(self, size=-1):
            raise OSError('fixture interrupted connection')

    def opener(url, timeout):
        name = parse_qs(urlparse(url).query)['FilePath'][0]
        if name != first:
            return good(url, timeout)
        if mode == 'disconnect':
            return Disconnected(b'')
        data = bodies[name]
        return io.BytesIO(data[:-1] if mode == 'truncated' else
                          b'X' * len(data) if mode == 'wrong-hash' else data + b'X')

    with pytest.raises((ServiceError, OSError)):
        assets.download_assets(tmp_path, manifest=manifest, opener=opener)
    assert (tmp_path / (first + '.part')).exists()
    assert not (tmp_path / first).exists()
    with pytest.raises(ServiceError, match='partial_exists'):
        assets.download_assets(tmp_path, manifest=manifest, opener=good)
    with pytest.raises(ServiceError):
        assets.verify_assets(tmp_path)


def test_extra_code_file_or_changed_manifest_refuses_verification(tmp_path, closure):
    payload, bodies = closure
    assets.download_assets(tmp_path, opener=opener_for(payload, bodies, []))
    (tmp_path / 'unexpected.py').write_text('raise RuntimeError', encoding='utf-8')
    with pytest.raises(ServiceError, match='closure_mismatch'):
        assets.verify_assets(tmp_path)
    with pytest.raises(ServiceError, match='closure_mismatch'):
        assets.download_assets(tmp_path, opener=opener_for(payload, bodies, []))


def test_metadata_size_is_bounded_and_http_failure_is_not_success(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, 'MAX_METADATA_BYTES', 10)
    with pytest.raises(ServiceError, match='metadata_too_large'):
        assets.fetch_metadata(opener=lambda url, timeout: io.BytesIO(b'X' * 11))
    with pytest.raises(ServiceError):
        assets.freeze_metadata(dict(Code=500, Success=False, Data=dict(Files=[])))
