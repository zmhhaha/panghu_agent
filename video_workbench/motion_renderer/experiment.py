"""Bounded experiment harness with diagnostics even when media exceed budget.

No archive or receipt from this module publishes a video or claims user approval.
The outer Job owns resource/time/egress limits and kills its cgroup on deletion.
"""
import base64
import io
import json
from pathlib import Path
import subprocess
import tarfile
import time

COMPRESSED_LIMIT = 6 * 1024 * 1024
UNCOMPRESSED_LIMIT = 32 * 1024 * 1024
ALLOWED = ['experiment.json', 'render-receipt.json', 'observe-receipt.json', 'render.log', 'observe.log',
           'output.mp4', 'observations.json', 'timeline.json', 'manifest.json', 'plan.json', 'beats.json', 'alignment.json']


class ArtifactBudgetExceeded(Exception):
    def __init__(self, reason, size):
        self.reason, self.size = reason, size
        super().__init__(reason)


def _archive(entries, *, compressed_limit, uncompressed_limit):
    total = sum(len(raw) for _, raw in entries)
    if total > uncompressed_limit:
        raise ArtifactBudgetExceeded('uncompressed_limit', total)
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        for name, raw in entries:
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(raw), 0o444
            archive.addfile(member, io.BytesIO(raw))
    raw = output.getvalue()
    if len(raw) > compressed_limit:
        raise ArtifactBudgetExceeded('compressed_limit', len(raw))
    return raw


def _entries(root, members, uncompressed_limit):
    total = 0
    for path in members:
        if path.is_symlink() or not path.is_file():
            raise ValueError('artifact_not_regular')
        total += path.stat().st_size
    if total > uncompressed_limit:
        raise ArtifactBudgetExceeded('uncompressed_limit', total)
    return [(path.relative_to(root).as_posix(), path.read_bytes()) for path in members]


def collect_artifacts(root, summary, *, compressed_limit=COMPRESSED_LIMIT, uncompressed_limit=UNCOMPRESSED_LIMIT):
    """Return (bounded archive, honest summary); failed fallback omits all media."""
    if compressed_limit < 1024 or uncompressed_limit < 2048:
        raise ValueError('diagnostic_budget_too_small')
    root = Path(root)
    summary = dict(summary)
    (root / 'experiment.json').write_text(json.dumps(summary, indent=2))
    members = [root / name for name in ALLOWED if (root / name).is_file()]
    members += sorted((root / 'observations').glob('*.jpg'))
    try:
        entries = _entries(root, members, uncompressed_limit)
        return _archive(entries, compressed_limit=compressed_limit, uncompressed_limit=uncompressed_limit), summary
    except ArtifactBudgetExceeded as failure:
        summary.update(status='artifact_budget_exceeded', prior_stage_status=summary['status'],
                       artifact_failure=dict(reason=failure.reason, observed_bytes=failure.size,
                                             compressed_limit=compressed_limit, uncompressed_limit=uncompressed_limit),
                       partial_media_included=False)
        diagnostics = []
        for name in ('render-receipt.json', 'observe-receipt.json'):
            path = root / name
            if path.is_file() and not path.is_symlink() and path.stat().st_size <= 64 * 1024:
                diagnostics.append((name, path.read_bytes()))
        for name in ('render.log', 'observe.log'):
            path = root / name
            if path.is_file() and not path.is_symlink():
                with path.open('rb') as log:
                    log.seek(max(0, path.stat().st_size - 32 * 1024))
                    diagnostics.append((name + '.tail.txt', log.read(32 * 1024)))
        def packet():
            raw = json.dumps(summary, indent=2).encode()
            (root / 'experiment.json').write_bytes(raw)
            return _archive([('experiment.json', raw), *diagnostics],
                            compressed_limit=compressed_limit, uncompressed_limit=uncompressed_limit)
        try:
            return packet(), summary
        except ArtifactBudgetExceeded:
            # Still emit a small failure summary if even diagnostic tails exceed
            # a caller's test/small budget. Never fall back to a partial MP4.
            summary['diagnostic_payload_reduced'] = True
            diagnostics = []
            return packet(), summary


def main():
    root = Path('/work')
    started, codes = time.monotonic(), {}
    for mode in ('render', 'observe'):
        with (root / (mode + '.log')).open('wb') as log:
            process = subprocess.run(['node', '/opt/renderer/run.mjs', mode], stdout=log, stderr=log)
        codes[mode] = process.returncode
        receipt = root / 'receipt.json'
        if receipt.exists():
            receipt.rename(root / (mode + '-receipt.json'))
        if process.returncode:
            break
    summary = dict(schema='video.motion-experiment.v1', wall_seconds=time.monotonic() - started,
                   stages=codes, status='pending_validation' if all(v == 0 for v in codes.values()) else 'failed')
    raw, summary = collect_artifacts(root, summary)
    print('VIDEO_MOTION_ARTIFACT_BASE64=' + base64.b64encode(raw).decode(), flush=True)
    return 0 if summary['status'] == 'pending_validation' else 1


if __name__ == '__main__':
    raise SystemExit(main())
