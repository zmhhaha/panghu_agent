"""Checksum-pinned in-session validation for visual learning authors."""
import hashlib
from pathlib import Path

from .contracts import digest
from .pi_cage import _read


SOURCE_ENVIRONMENTS = {
    'tools-pi-picture-learning-checked-source': 'video-picture-learning-author-output.v1',
    'tools-pi-reference-learning-checked-source': 'video-reference-learning-author-output.v1',
}
_EXTENSION = 'learning_source_write_extension.mjs'
SOURCE_CHECK_HASHES = {
    _EXTENSION: 'df0560421e55f73c4fa415c9fafa78ac47144350cc69a12102b66e7c352b5d24',
    'learning_source_syntax.mjs': 'e5b600137d267fdaa84f4ca2bf081ced92aaf70c1572bb8809a7c6ffefa1137d',
    'learning_source_validation.py': '1dedc25c1735447b866210a4c6ccec26db60a864edb0fdfc152c5e650c925f1b',
}


def checked_environment_revision(environment):
    name = environment['name']
    if name == 'tools-pi-picture-learning-checked-source':
        from .picture_learning_tools import environment_revision
    elif name == 'tools-pi-reference-learning-checked-source':
        from .reference_learning_tools import environment_revision
    else:
        raise ValueError('learning_source_environment_unknown')
    return 'checked-source-' + digest(dict(base=environment_revision(environment), checker=SOURCE_CHECK_HASHES))


def verified_source_check_extension():
    root = Path(__file__).parent
    for name, sha256 in SOURCE_CHECK_HASHES.items():
        if hashlib.sha256(_read(root / name, 128 * 1024)).hexdigest() != sha256:
            raise ValueError('learning_source_checker_integrity')
    return root / _EXTENSION
