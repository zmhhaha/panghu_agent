"""Real Chromium corner-raster regression and adversarial content checks.

The two retained JPEGs came from identical 0.375s seeks with different seek
histories. Their DOM is exact; only tiny pill-corner raster differences remain.
Modified negatives are independently encoded from one common decoded image,
so untouched JPEG blocks stay exact rather than failing due to recompression.
"""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageStat
import pytest

from agent_service.contracts import ServiceError
from video_workbench.process_continuity_review import _repeat_frame_equivalence


FIXTURES = Path(__file__).parent / 'fixtures' / 'process-repeat-frame'
EXPECTED = (
    '0ced8ba07dc78fef7a2967fa777d22bc3781bd3e2beb308c878dcdadbaa92439',
    '9f347ad5e1e895e03bc2ef358577716ebe3c0eb2e624b1b5d8a268f1dfd7a90c',
)


@pytest.fixture
def real_pair():
    samples = json.loads((FIXTURES / 'samples.json').read_bytes())
    raw = [(FIXTURES / f'sequence-{index}.jpg').read_bytes() for index in (13, 15)]
    assert tuple(hashlib.sha256(value).hexdigest() for value in raw) == EXPECTED
    assert tuple(sample['frame_sha256'] for sample in samples) == EXPECTED
    return samples, dict(zip(EXPECTED, raw))


def encoded(image, *, format='JPEG'):
    destination = io.BytesIO()
    image.save(destination, format=format, quality=100, subsampling=0)
    return destination.getvalue()


def bind(samples, first, second):
    samples = deepcopy(samples)
    values = (first, second)
    identities = tuple(hashlib.sha256(value).hexdigest() for value in values)
    for sample, identity in zip(samples, identities):
        sample['frame_sha256'] = identity
    return samples, dict(zip(identities, values))


def altered(real_pair, edit):
    samples, frames = real_pair
    original = Image.open(io.BytesIO(frames[EXPECTED[0]])).convert('RGB')
    damaged = original.copy()
    edit(damaged)
    return bind(samples, encoded(original), encoded(damaged))


def reject(pair):
    samples, frames = pair
    with pytest.raises(ServiceError):
        _repeat_frame_equivalence(*samples, frames)


def pixel_metrics(pair):
    samples, frames = pair
    images = [Image.open(io.BytesIO(frames[sample['frame_sha256']])).convert('RGB') for sample in samples]
    difference = ImageChops.difference(*images)
    red, green, blue = difference.split()
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    return (sum(ImageStat.Stat(difference).mean) / 3, maximum.getextrema()[1],
            1280 * 720 - maximum.histogram()[0])


def test_real_same_dom_seek_pair_retains_both_original_shas_and_only_bounded_corner_noise(real_pair):
    samples, frames = real_pair
    report = _repeat_frame_equivalence(*samples, frames)
    assert report['mode'] == 'bounded_corner_raster'
    assert report['frame_sha256'] == list(EXPECTED)
    assert report['changed_pixels'] == 1600 and report['max_channel_error'] == 44
    assert report['global_mae'] == pytest.approx(.0052126736111111115)
    assert report['other_pixels_exact'] and report['protected_text_exact']
    assert _repeat_frame_equivalence(samples[1], samples[0], frames)['changed_pixels'] == 1600


def test_identical_hash_keeps_the_existing_exact_evidence_path(real_pair):
    samples, _ = real_pair
    assert _repeat_frame_equivalence(samples[0], deepcopy(samples[0]), None)['mode'] == 'exact'


@pytest.mark.parametrize('field', ['elements', 'relations', 'layout', 'continuity', 'time', 'actual_time'])
def test_changed_measured_state_never_uses_pixel_similarity(real_pair, field):
    samples, frames = real_pair
    if field in ('time', 'actual_time'):
        samples[1][field] += .000001
    else:
        samples[1][field]['unexpected'] = True
    reject((samples, frames))


@pytest.mark.parametrize('damage', ['erase_glyph', 'swap_glyphs', 'single_stroke', 'extra_line',
                                    'move_token', 'missing_token', 'background', 'text_margin'])
def test_content_changes_are_rejected_including_one_small_stroke_below_global_limits(real_pair, damage):
    def edit(image):
        draw = ImageDraw.Draw(image)
        if damage == 'erase_glyph':
            draw.rectangle((604, 479, 621, 504), fill=(22, 68, 83))
        elif damage == 'swap_glyphs':
            first, second = image.crop((604, 479, 622, 505)), image.crop((622, 479, 640, 505))
            image.paste(second, (604, 479)); image.paste(first, (622, 479))
        elif damage == 'single_stroke':
            point = next((x, y) for y in range(480, 504) for x in range(608, 674)
                         if min(image.getpixel((x, y))) > 180)
            image.putpixel(point, tuple(value - 32 for value in image.getpixel(point)))
        elif damage == 'extra_line':
            draw.line((100, 600, 115, 600), fill=(24, 33, 47), width=1)
        elif damage == 'text_margin':
            # Outside the glyph box but inside its mandatory two-pixel margin.
            image.putpixel((603, 478), tuple(min(255, value + 16) for value in image.getpixel((603, 478))))
        elif damage == 'move_token':
            token = image.crop((572, 470, 708, 514))
            draw.rectangle((572, 470, 708, 513), fill=(22, 36, 58))
            image.paste(token, (573, 470))
        elif damage == 'missing_token':
            draw.rectangle((572, 470, 707, 513), fill=(22, 36, 58))
        else:
            draw.rectangle((0, 0, 31, 31), fill=(13, 22, 36))
    pair = altered(real_pair, edit)
    if damage in {'single_stroke', 'extra_line', 'text_margin'}:
        mae, maximum, changed = pixel_metrics(pair)
        assert mae <= .01 and maximum <= 48 and changed <= 2048
    reject(pair)


@pytest.mark.parametrize('failure', ['missing_bytes', 'missing_second', 'bad_sha', 'corrupt',
                                    'wrong_format', 'wrong_size', 'oversized'])
def test_missing_unbound_malformed_or_oversized_image_evidence_fails_closed(real_pair, failure):
    samples, frames = real_pair
    if failure == 'missing_bytes':
        frames = None
    elif failure == 'missing_second':
        frames.pop(EXPECTED[1])
    elif failure == 'bad_sha':
        frames[EXPECTED[1]] = frames[EXPECTED[0]]
    else:
        raw = (frames[EXPECTED[1]][:100] if failure == 'corrupt' else
               encoded(Image.new('RGB', (1280, 720)), format='PNG') if failure == 'wrong_format' else
               encoded(Image.new('RGB', (16, 16))) if failure == 'wrong_size' else
               frames[EXPECTED[1]] + bytes(2 * 1024 * 1024))
        samples, frames = bind(samples, frames[EXPECTED[0]], raw)
    reject((samples, frames))


@pytest.mark.parametrize('failure', ['outside_token', 'outside_text', 'nan', 'enlarged_token',
                                    'missing_label_box', 'no_visible_token'])
def test_mutated_metadata_cannot_enlarge_or_remove_protected_regions(real_pair, failure):
    samples, frames = real_pair
    token = next(identity for identity, state in samples[0]['elements'].items() if state['state'] is None)
    for sample in samples:
        state = sample['elements'][token]
        if failure == 'outside_token':
            state['x'] = -1
        elif failure == 'outside_text':
            state['text_boxes']['element-label']['x'] = -1
        elif failure == 'nan':
            state['x'] = float('nan')
        elif failure == 'enlarged_token':
            state['width'] = 200
        elif failure == 'missing_label_box':
            state['text_boxes'].pop('element-label')
        else:
            for token_state in sample['elements'].values():
                if token_state['state'] is None:
                    token_state['opacity'] = 0
    reject((samples, frames))


@pytest.mark.parametrize('failure', ['maximum', 'pixel_count', 'global_mae'])
def test_each_raster_budget_independently_blocks_changes_inside_allowed_corner_blocks(real_pair, failure):
    def edit(image):
        rectangle, amount = (((560, 464, 568, 472), 100) if failure == 'maximum' else
                             ((560, 464, 600, 528), 1) if failure == 'pixel_count' else
                             ((560, 464, 592, 520), 6))
        for y in range(rectangle[1], rectangle[3]):
            for x in range(rectangle[0], rectangle[2]):
                image.putpixel((x, y), tuple(value + amount for value in image.getpixel((x, y))))
    pair = altered(real_pair, edit)
    mae, maximum, changed = pixel_metrics(pair)
    if failure == 'maximum':
        assert maximum > 48 and mae <= .01 and changed <= 2048
    elif failure == 'pixel_count':
        assert changed > 2048 and mae <= .01 and maximum <= 48
    else:
        assert mae > .01 and maximum <= 48 and changed <= 2048
    reject(pair)
