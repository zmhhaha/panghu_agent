"""Resource custody tests; these do not claim a real Cairo/HyperFrames render."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench import manim_resource as M
from video_workbench import manim_resource_worker as W

IMAGE = 'sha256:' + 'a' * 64


def request(**changes):
    value = dict(schema=M.REQUEST_SCHEMA, resource_id='geometry',
        source='from manim import Scene\nclass Geometry(Scene):\n    pass\n',
        scene_class='Geometry', width=320, height=180, fps=24, frame_count=3,
        global_start_frame=48, seed=7, transparent=True, manim_version='0.22.1')
    value.update(changes)
    return value


def png(*, mode='RGBA', size=(320, 180), color=(31, 42, 53, 120)):
    output = io.BytesIO()
    Image.new(mode, size, color).save(output, format='PNG')
    return output.getvalue()


def compiled(value=None):
    value = request() if value is None else value
    frames = {M.frame_path(value['resource_id'], i): png(color=(i * 71, 12, 92, 120))
        for i in range(value['frame_count'])}
    files = M.compile_resource(value, frames, runtime_image_digest=IMAGE)
    return value, files, M.sha(files[M.manifest_path(value['resource_id'])])


def test_exact_original_source_clock_frame_bytes_and_runtime_are_bound():
    value, files, checksum = compiled()
    result = M.verify_resource(files, value, expected_manifest_sha256=checksum, runtime_image_digest=IMAGE)
    assert result['source_sha256'] == M.sha(value['source'].encode())
    assert result['request_sha256'] == digest(value)
    assert result['runtime']['image_digest'] == IMAGE
    assert result['fps'] == 24
    assert [row['global_frame'] for row in result['frames']] == [48, 49, 50]
    for row in result['frames']:
        assert row['sha256'] == M.sha(files[row['path']])
        assert row['bytes'] == len(files[row['path']])
    assert all(name.endswith(('.png', '/manifest.json')) for name in files)
    assert result['audio'] is None
    assert 'not_visual_acceptance' in result['evidence_scope']


@pytest.mark.parametrize('changes', [
    {'resource_id': '../escape'}, {'resource_id': 'CON'}, {'resource_id': 'con'},
    {'resource_id': 'aux'}, {'scene_class': 'Thing;system()'},
    {'source': '\x00'}, {'source': 'x' * (M.MAX_SOURCE_BYTES + 1)}, {'fps': 30},
    {'fps': True}, {'frame_count': 0}, {'frame_count': 241}, {'frame_count': True},
    {'global_start_frame': -1}, {'global_start_frame': .5}, {'global_start_frame': True},
    {'width': 1281}, {'height': 721}, {'height': 1}, {'transparent': 1},
    {'manim_version': 'latest'}, {'seed': -1}, {'seed': 2**32}, {'unknown': 'ignored'},
    {'width': 1280, 'height': 720, 'frame_count': 240},
])
def test_request_limits_reject_ambiguous_paths_clocks_and_runtime(changes):
    with pytest.raises(ServiceError):
        M.validate_request(request(**changes))


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'traversal', 'wrong_dimensions',
    'rgb', 'corrupt_png', 'movie', 'binary_type'])
def test_only_complete_decodable_rgba_sequence_is_deliverable(mutation):
    value = request()
    frames = {M.frame_path('geometry', i): png() for i in range(3)}
    first = M.frame_path('geometry', 0)
    if mutation == 'missing':
        del frames[first]
    elif mutation == 'extra':
        frames[M.frame_path('geometry', 3)] = png()
    elif mutation == 'traversal':
        frames['assets/manim/geometry/../other.png'] = frames.pop(first)
    elif mutation == 'wrong_dimensions':
        frames[first] = png(size=(321, 180))
    elif mutation == 'rgb':
        frames[first] = png(mode='RGB', color=(1, 2, 3))
    elif mutation == 'corrupt_png':
        raw = bytearray(frames[first])
        raw[len(raw) // 2] ^= 128
        frames[first] = bytes(raw)
    elif mutation == 'movie':
        frames[first] = b'\x00\x00\x00\x20ftypisom'
    elif mutation == 'binary_type':
        frames[first] = bytearray(frames[first])
    with pytest.raises(ServiceError):
        M.compile_resource(value, frames, runtime_image_digest=IMAGE)


def test_per_frame_and_total_budgets_are_checked_before_decoding(monkeypatch):
    value = request()
    frames = {M.frame_path('geometry', i): png() for i in range(3)}
    monkeypatch.setattr(M, 'MAX_FRAME_BYTES', 16)
    with pytest.raises(ServiceError, match='frame_budget'):
        M.compile_resource(value, frames, runtime_image_digest=IMAGE)
    monkeypatch.setattr(M, 'MAX_FRAME_BYTES', 4 * 1024 * 1024)
    monkeypatch.setattr(M, 'MAX_RESOURCE_BYTES', 16)
    with pytest.raises(ServiceError, match='budget_exceeded'):
        M.compile_resource(value, frames, runtime_image_digest=IMAGE)


def test_compressed_png_size_does_not_bypass_decoded_pixel_budget():
    # A transparent full-size PNG compresses to only a few KiB. 240 decoded
    # 720p RGBA frames would nevertheless require about 844 MiB.
    small_compressed = png(size=(1280, 720), color=(0, 0, 0, 0))
    assert len(small_compressed) < 4096
    with pytest.raises(ServiceError, match='decoded_budget_exceeded'):
        M.validate_request(request(width=1280, height=720, frame_count=240))
    assert M.validate_request(request(width=1280, height=720, frame_count=36))['frame_count'] == 36


@pytest.mark.parametrize('mutation', ['source', 'start', 'seed', 'image', 'manifest', 'frame'])
def test_collector_rejects_changes_to_any_frozen_binding(mutation):
    value, files, checksum = compiled()
    image = IMAGE
    if mutation == 'source':
        value['source'] += '# changed\n'
    elif mutation == 'start':
        value['global_start_frame'] += 1
    elif mutation == 'seed':
        value['seed'] += 1
    elif mutation == 'image':
        image = 'sha256:' + 'b' * 64
    elif mutation == 'manifest':
        path = M.manifest_path('geometry')
        metadata = json.loads(files[path])
        metadata['runtime']['renderer'] = 'opengl'
        files[path] = canonical_bytes(metadata)
        checksum = M.sha(files[path])
    elif mutation == 'frame':
        files[M.frame_path('geometry', 1)] = png(color=(2, 3, 4, 5))
    with pytest.raises(ServiceError):
        M.verify_resource(files, value, expected_manifest_sha256=checksum, runtime_image_digest=image)


def save_tree(root, files):
    for name, raw in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)


def test_stopped_tree_collector_allows_only_the_exact_resource(tmp_path):
    value, files, checksum = compiled()
    root = tmp_path / 'deliverable'
    save_tree(root, files)
    assert M.collect_resource_files(root, value,
        expected_manifest_sha256=checksum, runtime_image_digest=IMAGE) == files
    extra = root / 'assets/manim/geometry/extra.mp4'
    extra.write_bytes(b'not a deliverable')
    with pytest.raises(ServiceError, match='inventory_invalid'):
        M.collect_resource_files(root, value,
            expected_manifest_sha256=checksum, runtime_image_digest=IMAGE)


def test_stopped_tree_collector_rejects_linked_frames(tmp_path):
    value, files, checksum = compiled()
    root = tmp_path / 'deliverable'
    save_tree(root, files)
    first, second = [root / M.frame_path('geometry', i) for i in range(2)]
    second.unlink()
    os.link(first, second)
    with pytest.raises(ServiceError, match='inventory_invalid'):
        M.collect_resource_files(root, value,
            expected_manifest_sha256=checksum, runtime_image_digest=IMAGE)


def test_worker_does_not_import_authored_python_outside_explicit_isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.delenv('MANIM_RESOURCE_ISOLATED_WORKER', raising=False)
    marker = tmp_path / 'must-not-exist'
    value = request(source=f'open({str(marker)!r}, "w").write("executed")')
    with pytest.raises(ServiceError, match='isolated_worker_required'):
        W.render_resource(value, tmp_path / 'result', runtime_image_digest=IMAGE)
    assert not marker.exists()
    assert not (tmp_path / 'result').exists()


def fake_runtime(monkeypatch, *, count=3):
    """File-writer stand-in; not evidence that Manim or Cairo actually ran."""
    from contextlib import contextmanager
    settings = {}
    @contextmanager
    def tempconfig(value):
        settings.update(value)
        yield
    class SceneFileWriter:
        def __init__(self):
            self.frame_count = 0
            self.output_spec = SimpleNamespace(is_image_sequence=True, is_video=False)
            self.image_sequence_directory = Path(settings['media_dir']) / 'images'
            self.image_sequence_directory.mkdir(parents=True)
        def write_frame(self, pixels, *, repeat=1):
            # Match the pinned writer's PNG behavior, including ignoring repeat.
            (self.image_sequence_directory / f'{self.frame_count:06d}.png').write_bytes(png())
            self.frame_count += 1
    class CairoRenderer:
        def __init__(self, *, file_writer_class):
            self.file_writer = file_writer_class()
    class Scene:
        def __init__(self, *, renderer):
            self.renderer = renderer
        def render(self):
            # These names are from the pinned 0.22.1 output API. png is a still
            # image, png-sequence emits every frame; no legacy movie flag exists.
            assert settings['format'] == 'png-sequence'
            assert 'write_to_movie' not in settings
            assert settings['renderer'] == 'cairo' and settings['frame_rate'] == 24
            for index in range(count):
                self.renderer.file_writer.write_frame(None)
    runtime = SimpleNamespace(__version__='0.22.1', Scene=Scene, tempconfig=tempconfig)
    monkeypatch.setitem(W.sys.modules, 'manim', runtime)
    monkeypatch.setitem(W.sys.modules, 'manim.renderer.cairo_renderer', SimpleNamespace(CairoRenderer=CairoRenderer))
    monkeypatch.setitem(W.sys.modules, 'manim.scene.scene_file_writer', SimpleNamespace(SceneFileWriter=SceneFileWriter))
    monkeypatch.setitem(W.sys.modules, 'numpy', SimpleNamespace(random=SimpleNamespace(seed=lambda value: None)))
    monkeypatch.setattr(W.sys, 'platform', 'linux')
    monkeypatch.setenv('MANIM_RESOURCE_ISOLATED_WORKER', '1')
    monkeypatch.setenv('MANIM_RESOURCE_IMAGE_DIGEST', IMAGE)
    return settings


def test_worker_preserves_source_and_delivers_only_compilable_resource(tmp_path, monkeypatch):
    fake_runtime(monkeypatch)
    value = request()
    output = tmp_path / 'render'
    files = W.render_resource(value, output, runtime_image_digest=IMAGE)
    assert (output / 'source.py').read_bytes() == value['source'].encode()
    checksum = M.sha(files[M.manifest_path('geometry')])
    assert M.collect_resource_files(output / 'resource', value,
        expected_manifest_sha256=checksum, runtime_image_digest=IMAGE) == files


@pytest.mark.parametrize('count', [2, 4])
def test_worker_never_silently_pads_or_trims_wrong_frame_count(tmp_path, monkeypatch, count):
    fake_runtime(monkeypatch, count=count)
    with pytest.raises(ServiceError, match='frame_count_mismatch'):
        W.render_resource(request(), tmp_path / 'render', runtime_image_digest=IMAGE)
    assert not (tmp_path / 'render/resource').exists()


def test_cairo_frozen_wait_repeats_become_exact_frames_with_early_bound(tmp_path):
    class UpstreamPNGWriter:
        frame_count = 0
        output_spec = SimpleNamespace(is_image_sequence=True, is_video=False)
        image_sequence_directory = tmp_path
        def write_frame(self, pixels, *, repeat=1):
            # Upstream 0.22.1 emits one PNG regardless of repeat.
            (tmp_path / f'{self.frame_count:06d}.png').write_bytes(png())
            self.frame_count += 1
    writer = W.exact_png_writer(UpstreamPNGWriter, 4)()
    writer.write_frame(None, repeat=3)
    assert writer.frame_count == 3
    assert sorted(path.name for path in tmp_path.iterdir()) == ['000000.png', '000001.png', '000002.png']
    with pytest.raises(ServiceError, match='frame_count_mismatch'):
        writer.write_frame(None, repeat=2)
    # Reject before writing a partial oversized repeat.
    assert writer.frame_count == 3
    writer.write_frame(None, repeat=1)
    assert writer.frame_count == 4
    with pytest.raises(ServiceError, match='frame_count_mismatch'):
        writer.write_frame(None)
