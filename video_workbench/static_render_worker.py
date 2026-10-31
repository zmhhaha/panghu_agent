"""Isolated no-JS WeasyPrint worker. Input/output paths are operator supplied.

The application validates source before creating this private input file. This
worker never imports application code, reads credentials, or accepts URL inputs.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import sys


class NoResources:
    def __init__(self):
        self.blocked = False

    def __call__(self, *_args, **_kwargs):
        self.blocked = True
        # Reject data:, file:, http:, https: and every other URI scheme. System
        # fonts are resolved by Pango/Fontconfig rather than this URL fetcher.
        raise ValueError('External resources are disabled')


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) != 2:
            raise ValueError()
        source, destination = map(Path, args)
        if (not source.is_absolute() or not destination.is_absolute()
                or source.is_symlink() or destination.is_symlink()
                or source.parent.resolve() != destination.parent.resolve()
                or not source.is_file() or source.stat().st_size > 132 * 1024
                or destination.exists()):
            raise ValueError()
        content = source.read_text(encoding='utf-8')
        logging.disable(logging.CRITICAL)
        from weasyprint import CSS, HTML

        fetcher = NoResources()
        css = CSS(string='''
            @page { size: 1280px 720px; margin: 0; }
            html, body { margin: 0; padding: 0; }
            body { font-family: "Noto Sans CJK SC", sans-serif; }
            .video-workbench-static-svg > svg { width: 1280px; height: 720px; display: block; }
        ''', url_fetcher=fetcher)
        document = HTML(string=content, base_url=None, url_fetcher=fetcher,
                        media_type='screen').render(stylesheets=[css])
        if len(document.pages) != 1 or fetcher.blocked:
            raise ValueError()
        document.write_pdf(target=str(destination))
        if (fetcher.blocked or not destination.is_file()
                or not 0 < destination.stat().st_size <= 32 * 1024 * 1024):
            raise ValueError()
        print(json.dumps({'status': 'passed', 'renderer': 'weasyprint',
                          'pages': 1, 'width': 1280, 'height': 720,
                          'external_resources': 'denied'}), flush=True)
        return 0
    except Exception:
        print(json.dumps({'status': 'failed', 'code': 'static_render_failed'}), flush=True)
        return 78


if __name__ == '__main__':
    raise SystemExit(main())
