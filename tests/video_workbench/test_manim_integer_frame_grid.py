"""Integer frame sampling fixes upstream roundoff, not authored scene length."""
import math
from types import SimpleNamespace

import numpy as np
import pytest

from video_workbench.manim_resource_worker import integer_frame_count, install_integer_frame_grid


class Progress:
    def __init__(self, times, **options):
        self.times, self.options, self.closed = list(times), options, False

    def __iter__(self):
        return iter(self.times)

    def close(self):
        self.closed = True


class Scene:
    def __init__(self):
        self.renderer = SimpleNamespace(skip_animations=False)
        self.calls = []

    def get_time_progression(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return 'original-progression'


def install(scene, bar=lambda: 'none'):
    return install_integer_frame_grid(scene, fps=24, progress_bar=bar,
        progress_factory=Progress, upstream_method=Scene.get_time_progression)


def test_integer_plays_use_exact_indices_instead_of_float_stop_ceil():
    # This is the actual pinned-runtime failure: the authored fourteen-frame
    # middle play emits fifteen upstream samples before a seven-frame hold.
    assert len(np.arange(0, 14 / 24, 1 / 24)) == 15
    scene = Scene()
    assert install(scene)
    samples = []
    for count in (11, 14, 11):
        progress = scene.get_time_progression(count / 24, f'{count} frames')
        assert len(progress.times) == progress.options['total'] == count
        assert progress.times == [index / 24 for index in range(count)]
        assert progress.times[-1] < count / 24
        progress.close()
        assert progress.closed
        samples.extend(progress.times)
    # Cairo's exact frozen repeats remain unchanged: five plus seven frames.
    assert 5 + len(samples) + 7 == 48
    assert not scene.calls


def test_all_admitted_canonical_integer_durations_keep_the_frame_count():
    for count in range(1, 1441):
        duration = count / 24
        assert integer_frame_count(duration, 24) == count
        # Neighboring floats may express deliberate fractional timing. The
        # host does not absorb them into a tolerance around the frame boundary.
        assert integer_frame_count(math.nextafter(duration, 0), 24) is None
        assert integer_frame_count(math.nextafter(duration, math.inf), 24) is None


@pytest.mark.parametrize('duration', [0, -1, math.inf, -math.inf, math.nan,
    True, '0.5', 14.5 / 24, (14 + 1e-8) / 24, (14 - 1e-8) / 24])
def test_non_integer_or_invalid_duration_delegates_without_retimming(duration):
    scene = Scene()
    assert install(scene)
    assert integer_frame_count(duration, 24) is None
    assert scene.get_time_progression(duration, 'unchanged', n_iterations=-1,
        override_skip_animations=True) == 'original-progression'
    args, kwargs = scene.calls[0]
    assert args[0] is duration and args[1] == 'unchanged'
    assert kwargs == dict(n_iterations=-1, override_skip_animations=True)


def test_skipped_play_delegates_but_override_uses_grid_and_live_progress_configuration():
    scene = Scene()
    bar = ['none']
    assert install(scene, lambda: bar[0])
    scene.renderer.skip_animations = True
    assert scene.get_time_progression(14 / 24, 'skipped') == 'original-progression'
    first = scene.get_time_progression(14 / 24, 'sampled', override_skip_animations=True)
    assert first.options['disable'] and first.options['total'] == 14
    bar[0] = 'leave'
    second = scene.get_time_progression(14 / 24, 'wait condition', n_iterations=-1,
        override_skip_animations=True)
    assert second.options['leave'] and not second.options['disable']
    assert second.options['total'] == -1 and second.options['desc'] == 'wait condition'


def test_explicit_author_sampler_is_not_overridden():
    class CustomScene(Scene):
        def get_time_progression(self, *args, **kwargs):
            return 'author-sampler'

    scene = CustomScene()
    original = scene.get_time_progression
    assert not install(scene)
    assert scene.get_time_progression == original
    assert scene.get_time_progression(14 / 24, 'custom') == 'author-sampler'
