"""Cairo resource CLI for the existing isolated Pi authoring process.

This executes Agent Python. Run only inside an admitted isolated author worker,
whose existing StopProof must stop all writers before artifact collection.
It is deliberately not an HTTP server or a host-side Python execution API.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import random
import re
import sys

from agent_service.contracts import canonical_bytes
from .manim_resource import (MAX_FRAME_BYTES, MAX_RESOURCE_BYTES, MANIM_VERSION,
    compile_resource, frame_path, require, validate_request)


def plain(path):
    path = Path(path).absolute()
    require(not any(item.is_symlink() or getattr(item, 'is_junction', lambda: False)()
        for item in (path, *path.parents)), 'manim_resource_path_invalid')
    return path


def exact_png_writer(base, frame_count):
    """Preserve Cairo's explicit repeat duration in the pinned PNG output API.

    In 0.22.1 upstream PNG output writes one image even when Cairo passes
    repeat=N for a frozen wait. Expanding that renderer request preserves its
    original timing; it is not post-hoc padding of an undersized scene.
    """
    class ExactPNGWriter(base):
        def write_frame(self, pixels, *, repeat=1):
            require(type(repeat) is int and 1 <= repeat <= frame_count
                and self.frame_count + repeat <= frame_count, 'manim_resource_frame_count_mismatch')
            require(self.output_spec.is_image_sequence and not self.output_spec.is_video,
                'manim_resource_output_mode_changed')
            for _ in range(repeat):
                super().write_frame(pixels, repeat=1)
                path = plain(self.image_sequence_directory / f'{self.frame_count - 1:06d}.png')
                size = path.stat().st_size
                require(0 < size <= MAX_FRAME_BYTES, 'manim_resource_frame_budget', 413)
                self.resource_bytes = getattr(self, 'resource_bytes', 0) + size
                require(self.resource_bytes <= MAX_RESOURCE_BYTES, 'manim_resource_budget_exceeded', 413)
    return ExactPNGWriter


def render_resource(request, output, *, runtime_image_digest):
    """No retries, padding, resampling, MP4 encoding, or unbounded frame import."""
    request = validate_request(request)
    require(sys.platform == 'linux' and os.environ.get('MANIM_RESOURCE_ISOLATED_WORKER') == '1',
        'manim_resource_isolated_worker_required')
    require(type(runtime_image_digest) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', runtime_image_digest)
        and os.environ.get('MANIM_RESOURCE_IMAGE_DIGEST') == runtime_image_digest,
        'manim_resource_image_binding_mismatch')
    output = plain(output)
    require(not output.exists(), 'manim_resource_output_exists', 409)
    # Fresh job-local output only. A failed attempt is left for normal job cleanup.
    output.mkdir(parents=True)
    import manim
    import numpy as np
    from manim.renderer.cairo_renderer import CairoRenderer
    from manim.scene.scene_file_writer import SceneFileWriter
    require(manim.__version__ == MANIM_VERSION, 'manim_resource_version_mismatch')
    random.seed(request['seed'])
    np.random.seed(request['seed'])
    source = output / 'source.py'
    source.write_text(request['source'], encoding='utf-8', newline='')
    settings = dict(renderer='cairo', format='png-sequence', frame_rate=24,
        pixel_width=request['width'], pixel_height=request['height'],
        media_dir=str(output / 'intermediate'), output_file='resource',
        transparent=request['transparent'], disable_caching=True,
        preview=False, save_sections=False, zero_pad=6,
        progress_bar='none', verbosity='ERROR')
    with manim.tempconfig(settings):
        specification = importlib.util.spec_from_file_location('authored_manim_resource', source)
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        scene_class = getattr(module, request['scene_class'], None)
        require(isinstance(scene_class, type) and issubclass(scene_class, manim.Scene),
            'manim_resource_scene_invalid')
        writer = exact_png_writer(SceneFileWriter, request['frame_count'])
        scene = scene_class(renderer=CairoRenderer(file_writer_class=writer))
        scene.render()
        require(scene.renderer.__class__.__name__ == 'CairoRenderer', 'manim_resource_renderer_changed')
        images = plain(scene.renderer.file_writer.image_sequence_directory)
        require(images.is_relative_to(output), 'manim_resource_output_path_changed')
        names = {f'{index:06d}.png' for index in range(request['frame_count'])}
        entries = list(images.iterdir())
        require({path.name for path in entries} == names, 'manim_resource_frame_count_mismatch')
        frames, total = {}, 0
        for index in range(request['frame_count']):
            path = plain(images / f'{index:06d}.png')
            require(path.is_file() and 0 < path.stat().st_size <= MAX_FRAME_BYTES,
                'manim_resource_frame_budget', 413)
            total += path.stat().st_size
            require(total <= MAX_RESOURCE_BYTES, 'manim_resource_budget_exceeded', 413)
            frames[frame_path(request['resource_id'], index)] = path.read_bytes()
    files = compile_resource(request, frames, runtime_image_digest=runtime_image_digest)
    # Keep diagnostics outside the deliverable tree. Only compiled PNG resources
    # and exact manifest may be included in the eventual film asset envelope.
    delivered = output / 'resource'
    for name, raw in files.items():
        target = delivered / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    (output / 'request.json').write_bytes(canonical_bytes(request))
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    path = plain(args.request)
    require(path.is_file() and 0 < path.stat().st_size <= 1024 * 1024, 'manim_resource_request_invalid')
    request = json.loads(path.read_bytes())
    render_resource(request, args.output, runtime_image_digest=os.environ.get('MANIM_RESOURCE_IMAGE_DIGEST'))


if __name__ == '__main__':
    main()
