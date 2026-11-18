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
COMPILATION_CHECKED_ENVIRONMENT_NAME = 'tools-pi-compilation-checked'
COMPILATION_CHECKED_EXTENSION = Path(__file__).with_name('checked_compilation_extension.mjs')
COMPILATION_CHECKED_FILES = {'checked_compilation_extension.mjs': '64424d6291699cc1a69d2f945005cb924dce5be77b3a78e6d42cb249b11d7405', 'compilation_output_preflight.mjs': '6017b379dd78ebaccbb9ad1814f83acc47d2a25d509006a5cceaa271a8eca663', 'read_compilation_evidence_extension.mjs': '009e7eef1a2405840c744a43631a4e9c4a3d6aac6ac5365c1eda04f2e824ad4f'}
COMPILATION_CHECKED_TOOLS_SHA256 = '5c25caf23dcf353641b44fb61ef06ced2869acd0abe10111bb1171e772a40c82'


def compilation_tools(environment_name):
    if environment_name == COMPILATION_CHECKED_ENVIRONMENT_NAME:
        return frozenset({'read_compilation_input', 'write', 'check_compilation_output'})
    if environment_name in {COMPILATION_INPUT_ENVIRONMENT_NAME, COMPILATION_EVIDENCE_ENVIRONMENT_NAME}:
        return frozenset({'read_compilation_input', 'write'})
    raise ValueError('compilation_input_environment_identity')


def compilation_input_environment_revision(environment):
    data = dict(environment)
    data.pop('revision', None)
    if (set(data) != {'name', 'tools', 'capabilities', 'image_digest', 'runtime_revision'}
            or set(data['tools']) != compilation_tools(data['name'])):
        raise ValueError('compilation_input_environment_identity')
    data['tools'] = sorted(data['tools'])
    data['capabilities'] = sorted(data['capabilities'])
    extension_sha256 = (COMPILATION_CHECKED_TOOLS_SHA256
        if data['name'] == COMPILATION_CHECKED_ENVIRONMENT_NAME else COMPILATION_EVIDENCE_EXTENSION_SHA256
        if data['name'] == COMPILATION_EVIDENCE_ENVIRONMENT_NAME else COMPILATION_INPUT_EXTENSION_SHA256)
    return 'compile-' + extension_sha256 + '-' + digest(data)[:20]


def verified_compilation_input_extension(environment_name=COMPILATION_INPUT_ENVIRONMENT_NAME):
    if environment_name == COMPILATION_CHECKED_ENVIRONMENT_NAME:
        if not COMPILATION_CHECKED_FILES or digest(COMPILATION_CHECKED_FILES) != COMPILATION_CHECKED_TOOLS_SHA256:
            raise ValueError('compilation_input_extension_integrity')
        for name, checksum in COMPILATION_CHECKED_FILES.items():
            raw = _read(Path(__file__).with_name(name), 128 * 1024)
            if hashlib.sha256(raw).hexdigest() != checksum:
                raise ValueError('compilation_input_extension_integrity')
        return COMPILATION_CHECKED_EXTENSION
    if environment_name not in {COMPILATION_INPUT_ENVIRONMENT_NAME, COMPILATION_EVIDENCE_ENVIRONMENT_NAME}:
        raise ValueError('compilation_input_environment_identity')
    evidence = environment_name == COMPILATION_EVIDENCE_ENVIRONMENT_NAME
    extension = COMPILATION_EVIDENCE_EXTENSION if evidence else COMPILATION_INPUT_EXTENSION
    expected = COMPILATION_EVIDENCE_EXTENSION_SHA256 if evidence else COMPILATION_INPUT_EXTENSION_SHA256
    raw = _read(extension, 128 * 1024)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('compilation_input_extension_integrity')
    return extension
