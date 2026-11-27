"""Select exact image-owned hosts without changing an admitted film release."""
import hashlib
from pathlib import Path

from .hyperframes_film_browser import HOST_NAMES
from .hyperframes_film_execution import _ordinary

ROOT = Path(__file__).parent / 'motion_renderer'
LEGACY_REALM = 'legacy/hyperframes_film_realm_v1.mjs'
# The complete previously admitted v3 host set, before Three appearance support.
LEGACY_V3_HOSTS = {
    'hyperframes_film.mjs': 'd16ac2dfda228249240b07300694b804a93aeb087633dd6c3f66d6af1689eebb',
    'hyperframes_film_browser.mjs': '354a1577e20c8a1c64ee755d167d132290b369d58ce5e86e445161f14d0f845a',
    'hyperframes_film_channel.mjs': '629c6fc7a5bcdcd70a06207cda60ac35d51e264ac0cc048cefbce1d1c787ea33',
    'hyperframes_film_realm.mjs': '0b58f10bac9b21301e788422e19f445e547cef5e6fe1d8de024838b84f1bb3ba',
    'hyperframes_film_realm_guard.mjs': 'fcc860ee4e375c716024b67c8b388dc376f32b0d8518c5e2eeddb12a7bd2a08e',
    'hyperframes_film_root.mjs': '3bc525b91ca884beab5f6fde94bfd6a556f79677f85ea0403dc7ddfb805be837',
}


def _hashes(files):
    return {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}


def read_image_host_files(hosts, capabilities):
    """The operator's release selects hashes, never executable paths or code."""
    current = {name: _ordinary(ROOT / name, 2 * 1024 * 1024) for name in HOST_NAMES}
    if _hashes(current) == hosts:
        return current
    if (capabilities.get('schema') != 'video.animation-runtime-capabilities.v3'
            or capabilities.get('spatial_libraries') or hosts != LEGACY_V3_HOSTS):
        raise ValueError('Film production hosts do not match the image')
    selected = dict(current)
    selected['hyperframes_film_realm.mjs'] = _ordinary(ROOT / LEGACY_REALM, 2 * 1024 * 1024)
    if _hashes(selected) != hosts:
        raise ValueError('Film production hosts do not match the image')
    return selected
