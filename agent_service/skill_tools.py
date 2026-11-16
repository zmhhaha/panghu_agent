"""Identity of the separately admitted progressive animation Skill tools."""
import hashlib
from pathlib import Path

from .contracts import digest
from .pi_cage import _read


SKILL_TOOLS_ENVIRONMENT_NAME = 'tools-pi-animation-skills'
SKILL_TOOLS_EXTENSION_SHA256 = '481e6d78e9601b0291d529a879d8d620eeecfc5f63508cf7c0cce1a4050610f6'
SKILL_TASK_EXTENSION_SHA256 = '12cb0a209d678ebf9ae829eae51f4aa5b500f2ffbaf105107d20135c595d395b'
TOOL_SCOPE = frozenset({'read_inputs', 'skill_search', 'skill_read', 'write'})
_FILES = {'read_skill_task_extension.mjs': SKILL_TASK_EXTENSION_SHA256,
          'skill_tools_extension.mjs': SKILL_TOOLS_EXTENSION_SHA256}


def skill_tools_environment_revision(environment):
    data = dict(environment)
    data.pop('revision', None)
    if set(data) != {'name', 'tools', 'capabilities', 'image_digest', 'runtime_revision'}:
        raise ValueError('skill_tools_environment_identity')
    data['tools'], data['capabilities'] = sorted(data['tools']), sorted(data['capabilities'])
    return 'skills-' + digest(dict(environment=data, extensions=_FILES))


def verified_skill_tools_extensions():
    paths = []
    for name, checksum in _FILES.items():
        path = Path(__file__).with_name(name)
        if hashlib.sha256(_read(path, 128 * 1024)).hexdigest() != checksum:
            raise ValueError('skill_tools_extension_integrity')
        paths.append(path)
    return tuple(paths)
