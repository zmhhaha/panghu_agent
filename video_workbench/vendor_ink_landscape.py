"""Rebuild the image-owned ink module from an exact clean public checkout.

Operator/build utility only. Learning tasks never run a package manager or
resolve mutable Git references. The output includes the upstream MIT license.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


ESBUILD_VERSION = '0.25.12'
# Explicit publication inputs, separately hashed from the portable Skill. This
# list is read from the same clean source revision as the executable module.
PUBLICATION_DOCUMENTS = (
    'docs/learning/README.md',
    'docs/learning/brush-calligraphy.md',
    'docs/learning/ink-water.md',
    'docs/learning/texture-vegetation.md',
    'docs/learning/linework.md',
    'docs/learning/jieziyuan.md',
    'docs/brushwork.md',
    'docs/foundation-brush.md',
    'docs/stroke-group-document.md',
    'docs/cun-document.md',
    'docs/ink-process.md',
    'docs/water-process.md',
    'docs/vegetation-document.md',
    'docs/subject-macros.md',
    'docs/bark-document.md',
    'docs/rock-planes.md',
    'docs/natural-authoring.md',
    'docs/perturbation.md',
    'docs/existing-technique-revision.md',
    'docs/point-texture-revision.md',
    'docs/line-texture-revision.md',
    'docs/split-texture-revision.md',
    'docs/remaining-technique-batches.md',
)


def vendor(source, revision, esbuild, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise ValueError('An exact Git revision is required')
    actual = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    dirty = subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain'], text=True).strip()
    if actual != revision or dirty:
        raise ValueError('The source must be a clean checkout at the requested revision')
    command = [str(esbuild)]
    if str(esbuild).endswith('.js') or Path(esbuild).read_bytes().startswith(b'#!/usr/bin/env node'):
        command.insert(0, 'node')
    version = subprocess.check_output(command + ['--version'], text=True).strip()
    if version != ESBUILD_VERSION:
        raise ValueError('Unexpected bundler version')
    package = json.loads((source / 'package.json').read_text(encoding='utf-8'))
    if package['name'] != 'ink-landscape' or package['license'] != 'MIT':
        raise ValueError('Unexpected package identity or license')
    output.mkdir(parents=True, exist_ok=True)
    module = output / 'ink-landscape.mjs'
    subprocess.run(command + ['src/index.ts', '--bundle', '--format=esm',
        '--platform=browser', '--target=es2022', '--charset=utf8', '--legal-comments=inline',
        '--outfile=' + str(module)], check=True, cwd=source)
    (output / 'ink-landscape.LICENSE.txt').write_bytes((source / 'LICENSE').read_text(encoding='utf-8').encode('utf-8'))
    skills = {}
    for path in sorted((source / 'skills/ink-landscape').rglob('*.md')):
        name = path.relative_to(source / 'skills/ink-landscape').as_posix()
        target = output / 'skill' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_text(encoding='utf-8').encode('utf-8'))
        skills[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    publications = {}
    for name in PUBLICATION_DOCUMENTS:
        target = output / 'publication' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = (source / name).read_text(encoding='utf-8').encode('utf-8')
        if not raw or len(raw) > 128 * 1024:
            raise ValueError('Publication documentation must be nonempty and bounded')
        target.write_bytes(raw)
        publications[name] = hashlib.sha256(raw).hexdigest()
    assets = {name: hashlib.sha256((output / name).read_bytes()).hexdigest()
              for name in ('ink-landscape.mjs', 'ink-landscape.LICENSE.txt')}
    manifest = dict(schema='video.ink-landscape-vendor.v1',
        source_repository='https://github.com/zmhhaha/ink-landscape',
        source_revision=revision, version=package['version'], license='MIT',
        build=dict(tool='esbuild', version=version, target='es2022', format='esm', platform='browser'),
        assets=assets, skill_files=skills, publication_files=publications,
        publication_scope=dict(
            source_revision=revision,
            reading='100-item reading notes and their source/confidence boundaries; not mastery or artistic approval',
            jieziyuan='Selected public pages, especially images 1-8; not a complete reading of the book',
            runtime='Exact API and implementation documentation from this revision; no mutable repository lookup',
            release='Source preparation only; publication and dependency admission are separate explicit operations'))
    (output / 'manifest.json').write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--esbuild', required=True)
    parser.add_argument('--output', default=str(Path(__file__).parent / 'vendor/ink-landscape'))
    args = parser.parse_args()
    print(json.dumps(vendor(args.source, args.revision, args.esbuild, args.output), ensure_ascii=False))


if __name__ == '__main__':
    main()
