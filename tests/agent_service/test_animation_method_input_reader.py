"""Method review inputs must work with the actual shared reader, without a model."""
import base64
import hashlib
import json
import os
import shutil
import struct
import subprocess
import zlib

import pytest

from agent_service import input_tools
from agent_service.contracts import canonical_bytes, digest
from agent_service.profiles import video_animation_method as protocol


PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC')
READ = '''
const {readInputs} = await import(process.argv[1]);
const result = await readInputs({}, new AbortController().signal);
process.stdout.write(JSON.stringify(result));
'''


@pytest.mark.parametrize('comparison', [False, True], ids=['first-candidate', 'baseline-and-candidate'])
def test_admitted_review_inputs_deliver_json_and_all_original_images_to_shared_reader(tmp_path, comparison):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node unavailable for real input-reader integration')
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    frames, originals = [], {}
    for candidate in (['baseline', 'candidate'] if comparison else ['candidate']):
        for index in range(12):
            name = f'frame-{candidate}-{index}.png'
            payload = b'tEXt' + f'fixture\0{candidate}-{index}'.encode()
            raw = (PNG[:-12] + struct.pack('>I', len(payload) - 4) + payload
                   + struct.pack('>I', zlib.crc32(payload)) + PNG[-12:])
            originals[name] = raw
            frames.append(dict(frame_id=candidate + '-' + str(index), file_name=name,
                sha256=hashlib.sha256(raw).hexdigest(), timestamp_seconds=index / 24,
                candidate_id=candidate, media_sha256=('a' if candidate == 'baseline' else 'b') * 64))
            (inputs / name).write_bytes(raw)
    context = dict(schema=protocol.INPUT_SCHEMA, mode='review', goal='Check an observed change',
        criteria=[dict(id='legibility', description='The supplied objects are legible')], methods=[],
        constraints=dict(width=640, height=360, fps=24, duration_seconds=3, allowed_engines=['html-svg']),
        feedback='', base_source=None, reference=None, assets={},
        observation=dict(frames=frames, audio_observed=False, motion_observed=False,
                         limitations=['Only supplied still images are observed.']),
        comparison=dict(candidate_id='candidate', baseline_candidate_id='baseline' if comparison else None))
    original_json = canonical_bytes(context)
    (inputs / protocol.INPUT_NAME).write_bytes(original_json)
    profile = protocol.VideoAnimationMethodProfile(True)
    profile.validate_inputs(tmp_path, {'input_sha256': digest(context)})
    result = subprocess.run([node, '--input-type=module', '-e', READ,
        input_tools.READ_INPUTS_EXTENSION.resolve().as_uri()], cwd=tmp_path,
        capture_output=True, text=True, encoding='utf-8', timeout=10,
        env={key: value for key, value in os.environ.items()
             if key.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}})
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output['content'][0] == dict(type='text',
        text='Input file: ' + protocol.INPUT_NAME + '\n' + original_json.decode('utf-8'))
    images = output['content'][1:]
    assert len(images) == 2 * len(frames)
    returned = {}
    for label, image in zip(images[::2], images[1::2]):
        assert label['type'] == 'text' and label['text'].startswith('Input file: ')
        assert image['type'] == 'image' and image['mimeType'] == 'image/png'
        returned[label['text'].removeprefix('Input file: ')] = base64.b64decode(image['data'], validate=True)
    assert returned == originals
    assert output['details']['totalBytes'] == len(original_json) + sum(map(len, originals.values()))
