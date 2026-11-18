"""Fixed bounded reader identity for frozen WikiSkill compilation inputs."""
from pathlib import Path
import hashlib

from .pi_cage import _read
from .contracts import digest

COMPILATION_INPUT_EXTENSION = Path(__file__).with_name('read_compilation_input_extension.mjs')
COMPILATION_INPUT_EXTENSION_SHA256 = 'e682b716835e3fda336e1b3a1b947f1acfd49dba53de7697ea3db0da3b3cf6c7'
COMPILATION_INPUT_ENVIRONMENT_NAME = 'tools-pi-compilation-input'
COMPILATION_EVIDENCE_ENVIRONMENT_NAME = 'tools-pi-compilation-evidence'
COMPILATION_EVIDENCE_EXTENSION = Path(__file__).with_name('read_compilation_evidence_extension.mjs')
COMPILATION_EVIDENCE_EXTENSION_SHA256 = '009e7eef1a2405840c744a43631a4e9c4a3d6aac6ac5365c1eda04f2e824ad4f'


def compilation_input_environment_revision(environment):
    data = dict(environment)
    data.pop('revision', None)
    if (set(data) != {'name', 'tools', 'capabilities', 'image_digest', 'runtime_revision'}
            or data['name'] not in {COMPILATION_INPUT_ENVIRONMENT_NAME, COMPILATION_EVIDENCE_ENVIRONMENT_NAME}
            or set(data['tools']) != {'read_compilation_input', 'write'}):
        raise ValueError('compilation_input_environment_identity')
    data['tools'] = sorted(data['tools'])
    data['capabilities'] = sorted(data['capabilities'])
    extension_sha256 = (COMPILATION_EVIDENCE_EXTENSION_SHA256
        if data['name'] == COMPILATION_EVIDENCE_ENVIRONMENT_NAME else COMPILATION_INPUT_EXTENSION_SHA256)
    return 'compile-' + extension_sha256 + '-' + digest(data)[:20]


def verified_compilation_input_extension(environment_name=COMPILATION_INPUT_ENVIRONMENT_NAME):
    if environment_name not in {COMPILATION_INPUT_ENVIRONMENT_NAME, COMPILATION_EVIDENCE_ENVIRONMENT_NAME}:
        raise ValueError('compilation_input_environment_identity')
    evidence = environment_name == COMPILATION_EVIDENCE_ENVIRONMENT_NAME
    extension = COMPILATION_EVIDENCE_EXTENSION if evidence else COMPILATION_INPUT_EXTENSION
    expected = COMPILATION_EVIDENCE_EXTENSION_SHA256 if evidence else COMPILATION_INPUT_EXTENSION_SHA256
    raw = _read(extension, 128 * 1024)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('compilation_input_extension_integrity')
    return extension
