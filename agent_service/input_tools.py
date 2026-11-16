"""Fixed Pi input-reader identity, independent of any business profile."""
from pathlib import Path
import hashlib

from .pi_cage import _read
from .contracts import digest


READ_INPUTS_EXTENSION = Path(__file__).with_name('read_inputs_extension.mjs')
READ_INPUTS_EXTENSION_SHA256 = '5f2bde15e1f477b2f6c01bca39785aec0d6ebaa2910627ccfc0a4d44e25dd601'
READ_INPUTS_ENVIRONMENT_NAME = 'tools-pi-read-inputs'


def read_inputs_environment_revision(environment):
    """Bind the extension bytes and the full environment, including its image."""
    data = dict(environment)
    data.pop('revision', None)
    if set(data) != {'name', 'tools', 'capabilities', 'image_digest', 'runtime_revision'}:
        raise ValueError('read_inputs_environment_identity')
    data['tools'] = sorted(data['tools'])
    data['capabilities'] = sorted(data['capabilities'])
    return 'inputs-' + READ_INPUTS_EXTENSION_SHA256 + '-' + digest(data)[:20]


def verified_read_inputs_extension():
    """Load only the image-owned bytes whose hash is part of tool identity."""
    raw = _read(READ_INPUTS_EXTENSION, 128 * 1024)
    if hashlib.sha256(raw).hexdigest() != READ_INPUTS_EXTENSION_SHA256:
        raise ValueError('read_inputs_extension_integrity')
    return READ_INPUTS_EXTENSION
