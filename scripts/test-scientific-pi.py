#!/usr/bin/env python3
"""Run independent scientific Pi unittest suites without changing global tooling.

Separate processes avoid module-name collisions across existing test directories.
Tests use local fixtures; optional actual-Pi smoke only uses a loopback model stub.
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
SUITES = ('contracts', 'research', 'worker', 'api', 'runtime', 'integration', 'deployment')


def main():
    failed = []
    for suite in SUITES:
        print(f'Running scientific Pi suite: {suite}', flush=True)
        result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', f'tests/scientific_pi/{suite}', '-p', 'test*.py', '-v'], cwd=ROOT)
        if result.returncode:
            failed.append(suite)
    if failed:
        print('Failed suites: ' + ', '.join(failed), file=sys.stderr)
        return 1
    print(f'All {len(SUITES)} scientific Pi suites passed; inspect explicit platform/runtime skips above.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
