"""Identity of the new image/knowledge tool transport; old tools stay exact."""
import hashlib
from pathlib import Path

from .contracts import digest
from .input_tools import READ_INPUTS_EXTENSION_SHA256, verified_read_inputs_extension
from .pi_cage import _read


ENVIRONMENT_NAME = 'tools-pi-picture-learning'
EXTENSION_SHA256 = 'b8097375c1921611e922dcda3ef3e905170947d13f79e1c2ea1445f4421be46e'


def environment_revision(environment):
    data = dict(environment)
    data.pop('revision', None)
    if set(data) != {'name', 'tools', 'capabilities', 'image_digest', 'runtime_revision'}:
        raise ValueError('picture_learning_environment_identity')
    data['tools'], data['capabilities'] = sorted(data['tools']), sorted(data['capabilities'])
    return 'picture-' + digest(dict(environment=data, extension_sha256=EXTENSION_SHA256,
        image_reader_sha256=READ_INPUTS_EXTENSION_SHA256))


def verified_extensions():
    verified_read_inputs_extension()  # imported dependency is independently pinned
    path = Path(__file__).with_name('picture_learning_extension.mjs')
    if hashlib.sha256(_read(path, 128 * 1024)).hexdigest() != EXTENSION_SHA256:
        raise ValueError('picture_learning_extension_integrity')
    return (path,)
