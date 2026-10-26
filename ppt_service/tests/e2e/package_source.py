"""Freeze P5 source only; runtime config, credentials and evidence are excluded."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tarfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    target = args.output.resolve()
    target.mkdir(parents=True, exist_ok=True)
    archive_path = target / 'source.tar'
    hashes_path = target / 'source-hashes.json'
    if archive_path.exists() or hashes_path.exists():
        parser.error('Use a new output directory to preserve previous evidence')
    paths = []
    for directory in ('ppt_service/server', 'ppt_service/control', 'ppt_service/worker', 'pi_worker/runtime'):
        paths.extend((root / directory).rglob('*.py'))
    paths += [root / path for path in (
        'ppt_service/__init__.py', 'pi_worker/__init__.py',
        'scientific_agent/__init__.py', 'scientific_agent/pi_contracts.py')]
    paths += [path for path in Path(__file__).parent.rglob('*')
              if path.is_file() and path.suffix in ('.py', '.sh')]
    paths += [root / path for path in (
        'ppt_service/tests/server/test_api.py', 'ppt_service/tests/server/test_auth.py',
        'ppt_service/tests/server/test_postgres.py', 'ppt_service/tests/control/test_control.py')]
    paths = sorted(set(path for path in paths
                       if not any(part.startswith('.') or part in ('__pycache__', 'evidence')
                                  for part in path.relative_to(root).parts)))
    records = []
    with tarfile.open(archive_path, 'x') as archive:
        for path in paths:
            if path.is_symlink():
                raise RuntimeError('Source package may contain only regular source files')
            relative = path.relative_to(root).as_posix()
            data = path.read_bytes()
            info = tarfile.TarInfo(relative)
            info.size = len(data)
            info.mode = 0o644
            # Hash and archive exactly the same bytes despite concurrent edits.
            from io import BytesIO
            archive.addfile(info, BytesIO(data))
            records.append({'path': relative, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
    hashes_path.write_text(json.dumps(records, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'files': len(records), 'tar_sha256': hashlib.sha256(archive_path.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
