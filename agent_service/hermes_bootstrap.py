"""Fixed Hermes entry point with provider SDK retries disabled before imports."""
from __future__ import annotations

from importlib import metadata
from pathlib import Path
import sys


def main():
    # The immutable donor's Hermes `tools` package must precede the neutral
    # control image's unrelated tools.academic package on module resolution.
    sys.path.insert(0, '/opt/hermes')
    import openai
    for name in ('OpenAI', 'AsyncOpenAI'):
        cls = getattr(openai, name)
        original = cls.__init__
        def initialize(self, *args, _initialize=original, **kwargs):
            kwargs['max_retries'] = 0
            _initialize(self, *args, **kwargs)
        cls.__init__ = initialize
    # No request/environment module name is loaded. The distribution's single
    # fixed `hermes` console entry belongs to the pinned donor image.
    entries = list(metadata.entry_points(group='console_scripts').select(name='hermes'))
    if len(entries) != 1:
        raise RuntimeError('hermes_entrypoint_unavailable')
    sys.argv[0] = '/opt/hermes/.venv/bin/hermes'
    result = entries[0].load()()
    return result if isinstance(result, int) else 0


if __name__ == '__main__':
    raise SystemExit(main())
