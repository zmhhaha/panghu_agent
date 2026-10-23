"""Immutable fixed-command probe for domain_smoke; OFFLINE TEST ONLY."""
from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, '/code')
MARKER = 'PI_DOMAIN_FIXED_PROBE_COMPLETE'


def main():
    if len(sys.argv) != 1 or sys.platform != 'linux':
        raise RuntimeError('fixed Linux test only')
    source = Path('/work/inputs/source.md').read_bytes()
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    denied = {}
    for domain, existing in (('inputs', 'source.md'), ('task', 'task.json'), ('base', 'base.md')):
        for action, name, flags in (
            ('overwrite', existing, os.O_WRONLY | os.O_TRUNC),
            ('create', 'domain-probe-must-not-exist', os.O_WRONLY | os.O_CREAT | os.O_EXCL),
        ):
            try:
                fd = os.open('/work/' + domain + '/' + name, flags, 0o600)
            except OSError as exc:
                if exc.errno not in (errno.EROFS, errno.EACCES, errno.EPERM):
                    raise
                denied[domain + '_' + action] = exc.errno
            else:
                os.close(fd)
                raise RuntimeError('read-only domain write succeeded')
    try:
        Path('/work/audit/control-private/sentinel').read_bytes()
    except PermissionError:
        denied['controller_sentinel'] = True
    else:
        raise RuntimeError('controller sentinel readable')
    allowed = {'PATH', 'HOME', 'USERPROFILE', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME',
               'TMPDIR', 'TMP', 'TEMP', 'PI_CODING_AGENT_DIR', 'PI_OFFLINE',
               'NO_COLOR', 'LANG', 'SCIENTIFIC_MODEL_API_KEY', 'PWD', 'OLDPWD', 'SHLVL', '_', 'LC_CTYPE',
               'PI_CODING_AGENT', 'PI_SKIP_VERSION_CHECK'}
    if os.environ.get('PI_CODING_AGENT') != 'true' or os.environ.get('PI_SKIP_VERSION_CHECK') != '1':
        raise RuntimeError('pinned Pi CLI environment flags mismatch')
    if set(os.environ) - allowed:
        raise RuntimeError('unexpected inherited environment names: ' + repr(sorted(set(os.environ) - allowed)))
    if os.environ.get('SCIENTIFIC_MODEL_API_KEY') != 'fake-worker-protocol-key':
        raise RuntimeError('synthetic worker key missing')
    for key in ('HOME', 'USERPROFILE', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'TMPDIR', 'TMP', 'TEMP', 'PI_CODING_AGENT_DIR'):
        if not os.environ[key].startswith('/work/runtime/domain-attempt/'):
            raise RuntimeError('runtime paths escaped scratch')
    payload = {
        'test_only': True, 'source_sha256': hashlib.sha256(source).hexdigest(),
        'source_bytes': len(source), 'resuid': list(os.getresuid()),
        'resgid': list(os.getresgid()), 'groups': os.getgroups(),
        'status': {key: status[key].strip() for key in ('CapEff', 'CapPrm', 'CapInh', 'CapAmb', 'CapBnd', 'NoNewPrivs')},
        'denied': denied, 'environment_names': sorted(os.environ),
        'synthetic_worker_key_only': True,
    }
    Path('/work/project/probe.txt').write_bytes(b'OFFLINE TEST ONLY\n' + source)
    Path('/work/result/probe.json').write_text(json.dumps(payload, sort_keys=True) + '\n', encoding='utf-8')
    print(MARKER, flush=True)


if __name__ == '__main__':
    main()
