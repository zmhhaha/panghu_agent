"""Run P5 contracts and persist actual failures/skips; no expected-failure mask."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import sys
import time
import unittest


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed_tests = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed_tests.append(test.id())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.discover(
        str(Path(__file__).parent), pattern='test_*.py',
        top_level_dir=str(Path(__file__).resolve().parents[1]))
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2, resultclass=RecordedResult).run(suite)
    successful = result.wasSuccessful() and not result.expectedFailures
    receipt = {
        'suite': 'P5 isolated model-free integration',
        'platform': platform.platform(),
        'database': 'postgresql' if os.environ.get('PPT_E2E_DATABASE_URL') else 'sqlite',
        'database_scope': 'HTTP workflow/security/recovery use selected database; SQLiteBackupRecoveryTests always use SQLite; engine boundary tests are filesystem/runtime tests',
        'tests_run': result.testsRun,
        'passed': len(result.passed_tests),
        'passed_tests': result.passed_tests,
        'failures': [{'test': test.id(), 'traceback': trace} for test, trace in result.failures],
        'errors': [{'test': test.id(), 'traceback': trace} for test, trace in result.errors],
        'skipped': [{'test': test.id(), 'reason': reason} for test, reason in result.skipped],
        'unexpected_successes': [test.id() for test in result.unexpectedSuccesses],
        'expected_failures': [test.id() for test, _ in result.expectedFailures],
        'seconds': round(time.monotonic() - started, 3),
        'successful': successful,
        'fixture_boundary': 'Synthetic Worker, local test RSA key/JWKS, dedicated temporary database and files; no real model or production auth/deployment acceptance',
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return 0 if successful else 1


if __name__ == '__main__':
    sys.exit(main())
