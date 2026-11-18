"""Identity of the new image/knowledge tool transport; old tools stay exact."""
import hashlib
from pathlib import Path

from .contracts import digest
from .input_tools import READ_INPUTS_EXTENSION_SHA256, verified_read_inputs_extension
from .pi_cage import _read


ENVIRONMENT_NAME = 'tools-pi-reference-learning'
EXTENSION_SHA256 = '1be14e22d3908782e70245af0513345c5e69d04cffd1a41eb2d97c52b9ed2703'


def environment_revision(environment):
    data = dict(environment)
    data.pop('revision', None)
    if set(data) != {'name', 'tools', 'capabilities', 'image_digest', 'runtime_revision'}:
        raise ValueError('reference_learning_environment_identity')
    data['tools'], data['capabilities'] = sorted(data['tools']), sorted(data['capabilities'])
    return 'reference-' + digest(dict(environment=data, extension_sha256=EXTENSION_SHA256,
        image_reader_sha256=READ_INPUTS_EXTENSION_SHA256))


def verified_extensions():
    verified_read_inputs_extension()  # imported dependency is independently pinned
    path = Path(__file__).with_name('reference_learning_extension.mjs')
    if hashlib.sha256(_read(path, 128 * 1024)).hexdigest() != EXTENSION_SHA256:
        raise ValueError('reference_learning_extension_integrity')
    return (path,)
