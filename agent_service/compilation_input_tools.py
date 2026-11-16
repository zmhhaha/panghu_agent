"""Fixed bounded reader identity for frozen WikiSkill compilation inputs."""
from pathlib import Path
import hashlib

from .pi_cage import _read
from .contracts import digest

COMPILATION_INPUT_EXTENSION = Path(__file__).with_name('read_compilation_input_extension.mjs')
COMPILATION_INPUT_EXTENSION_SHA256 = 'e682b716835e3fda336e1b3a1b947f1acfd49dba53de7697ea3db0da3b3cf6c7'
COMPILATION_INPUT_ENVIRONMENT_NAME = 'tools-pi-compilation-input'


def compilation_input_environment_revision(environment):
    data = dict(environment)
    data.pop('revision', None)
    if (set(data) != {'name', 'tools', 'capabilities', 'image_digest', 'runtime_revision'}
            or data['name'] != COMPILATION_INPUT_ENVIRONMENT_NAME
            or set(data['tools']) != {'read_compilation_input', 'write'}):
        raise ValueError('compilation_input_environment_identity')
    data['tools'] = sorted(data['tools'])
    data['capabilities'] = sorted(data['capabilities'])
    return 'compile-' + COMPILATION_INPUT_EXTENSION_SHA256 + '-' + digest(data)[:20]


def verified_compilation_input_extension():
    raw = _read(COMPILATION_INPUT_EXTENSION, 128 * 1024)
    if hashlib.sha256(raw).hexdigest() != COMPILATION_INPUT_EXTENSION_SHA256:
        raise ValueError('compilation_input_extension_integrity')
    return COMPILATION_INPUT_EXTENSION
