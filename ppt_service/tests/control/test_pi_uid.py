"""Offline ledger tests. No workers, suppliers or live services."""
import concurrent.futures
import json
import multiprocessing
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ppt_service.control.pi_uid import PiUIDAllocator, UIDError, UIDExhausted, UIDScope

SCOPE = UIDScope('ppt-test', 'offline', 'dedicated-hosts', 120000, 121000)
BINDING = dict(project_id='11111111-1111-4111-8111-111111111111',
               job_id='22222222-2222-4222-8222-222222222222', attempt=1, token=7, role='pi')


def allocate_many(directory, count):
    allocator = PiUIDAllocator(directory, SCOPE)
    return [allocator.allocate(**BINDING) for _ in range(count)]


def crash_after_commit(directory):
    PiUIDAllocator(directory, SCOPE).allocate(**BINDING)
    os._exit(17)


def crash_before_commit(directory):
    allocator = PiUIDAllocator(directory, SCOPE)
    with allocator._transaction() as db:
        uid = allocator._validate(db)
        db.execute('INSERT INTO allocations VALUES(?,?)', (uid, '{}'))
        db.execute('UPDATE metadata SET next_uid=?', (uid + 1,))
        os._exit(18)


class PiUIDTests(unittest.TestCase):
    def setUp(self):
        if os.name == 'posix' and os.geteuid() != 0:
            self.skipTest('production filesystem policy requires Linux root')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'ledger'
        self.a = PiUIDAllocator.initialize(self.directory, SCOPE)

    def test_restart_and_duplicate_attempt_burn_fresh_uid(self):
        self.assertEqual(self.a.allocate(**BINDING), SCOPE.uid_min)
        reopened = PiUIDAllocator(self.directory, SCOPE)
        self.assertEqual(reopened.allocate(**BINDING), SCOPE.uid_min + 1)
        with closing(sqlite3.connect(self.a.path)) as db, db:
            rows = db.execute('SELECT identity FROM allocations ORDER BY uid').fetchall()
            scope = db.execute('SELECT scope FROM metadata').fetchone()[0]
        self.assertEqual(rows[0], rows[1])
        self.assertEqual(json.loads(rows[0][0]), BINDING)
        self.assertEqual(scope, SCOPE.canonical())

    def test_preview_role_uses_separate_uid(self):
        pi = self.a.allocate(**BINDING)
        preview = self.a.allocate(**(BINDING | {'role': 'preview'}))
        self.assertNotEqual(pi, preview)
        with self.assertRaises(UIDError):
            self.a.allocate(**(BINDING | {'role': 'worker'}))
        with closing(sqlite3.connect(self.a.path)) as db:
            rows = db.execute('SELECT identity FROM allocations ORDER BY uid').fetchall()
        self.assertEqual([json.loads(row[0])['role'] for row in rows], ['pi', 'preview'])

    def test_duplicate_binding_is_not_silently_resolved_to_old_uid(self):
        first = self.a.allocate(**BINDING)
        second = self.a.allocate(**BINDING)
        with self.assertRaisesRegex(UIDError, 'exactly one'):
            self.a.verify(**BINDING)
        binding = {k:v for k,v in BINDING.items() if k != 'role'}
        self.assertEqual(self.a.writers(**binding), (first, second))

    def test_writer_recovery_includes_both_roles_but_no_other_attempt(self):
        pi = self.a.allocate(**BINDING)
        preview = self.a.allocate(**(BINDING | {'role':'preview'}))
        self.a.allocate(**(BINDING | {'attempt':2}))
        binding = {k:v for k,v in BINDING.items() if k != 'role'}
        self.assertEqual(self.a.writers(**binding), (pi, preview))

    def test_verify_returns_durable_uid_without_burning_another(self):
        pi = self.a.allocate(**BINDING)
        self.assertEqual(self.a.verify(**BINDING), pi)
        self.assertEqual(PiUIDAllocator(self.directory, SCOPE).verify(**BINDING), pi)
        self.assertEqual(self.a.allocate(**BINDING), SCOPE.uid_min + 1)

    def test_verify_rejects_unallocated_or_foreign_bindings(self):
        pi = self.a.allocate(**BINDING)
        preview = self.a.allocate(**(BINDING | {'role': 'preview'}))
        self.assertNotEqual(pi, preview)
        for binding in (BINDING | {'attempt': 2}, BINDING | {'token': 8},
                        BINDING | {'role': 'worker'},
                        BINDING | {'job_id': '33333333-3333-4333-8333-333333333333'}):
            with self.subTest(binding=binding), self.assertRaises(UIDError):
                self.a.verify(**binding)
        self.assertEqual(self.a.verify(**BINDING), pi)
        self.assertEqual(self.a.verify(**(BINDING | {'role': 'preview'})), preview)

    def test_verify_fails_closed_on_corrupt_history(self):
        self.a.allocate(**BINDING)
        with closing(sqlite3.connect(self.a.path)) as db, db:
            db.execute('DELETE FROM allocations')
        with self.assertRaises(UIDError):
            self.a.verify(**BINDING)

    def test_threads_multiple_instances(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: allocate_many(self.directory, 8), range(8)))
        self.assertEqual(sorted(uid for group in results for uid in group),
                         list(range(SCOPE.uid_min, SCOPE.uid_min + 64)))

    def test_threads_shared_instance(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(lambda _: self.a.allocate(**BINDING), range(24)))
        self.assertEqual(sorted(values), list(range(SCOPE.uid_min, SCOPE.uid_min + 24)))

    def test_sidecar_hardlink_rejected(self):
        other = Path(self.temp.name) / 'private'
        other.write_bytes(b'private')
        other.chmod(0o600)
        os.link(other, str(self.a.path) + '-journal')
        with self.assertRaises(UIDError):
            self.a.allocate(**BINDING)
        self.assertEqual(other.read_bytes(), b'private')

    def test_spawn_processes(self):
        with concurrent.futures.ProcessPoolExecutor(max_workers=4,
                mp_context=multiprocessing.get_context('spawn')) as pool:
            futures = [pool.submit(allocate_many, self.directory, 6) for _ in range(4)]
            values = [uid for future in futures for uid in future.result(timeout=45)]
        self.assertEqual(sorted(values), list(range(SCOPE.uid_min, SCOPE.uid_min + 24)))

    def test_crash_after_commit_burns_uid_without_receipt(self):
        process = multiprocessing.get_context('spawn').Process(target=crash_after_commit, args=(self.directory,))
        process.start(); process.join(30)
        self.assertEqual(process.exitcode, 17)
        self.assertEqual(self.a.allocate(**BINDING), SCOPE.uid_min + 1)

    def test_crash_before_commit_recovers_unissued_reservation(self):
        process = multiprocessing.get_context('spawn').Process(target=crash_before_commit, args=(self.directory,))
        process.start(); process.join(30)
        self.assertEqual(process.exitcode, 18)
        self.assertEqual(self.a.allocate(**BINDING), SCOPE.uid_min)

    def test_exhaustion_does_not_recycle(self):
        scope = replace(SCOPE, uid_min=2147483646, uid_max=2147483646)
        allocator = PiUIDAllocator.initialize(Path(self.temp.name) / 'last', scope)
        self.assertEqual(allocator.allocate(**BINDING), 2147483646)
        with self.assertRaises(UIDExhausted):
            PiUIDAllocator(allocator.directory, scope).allocate(**BINDING)

    def test_missing_ledger_and_reinitialize_fail_closed(self):
        self.a.allocate(**BINDING)
        self.a.path.unlink()
        with self.assertRaises(UIDError):
            self.a.allocate(**BINDING)
        with self.assertRaises(UIDError):
            PiUIDAllocator.initialize(self.directory, SCOPE)
        self.assertFalse(self.a.path.exists())

    def test_corrupt_ledger(self):
        self.a.path.write_bytes(b'not a database')
        with self.assertRaises(UIDError):
            PiUIDAllocator(self.directory, SCOPE)

    def test_unknown_schema_version_and_scope(self):
        with self.assertRaises(UIDError):
            PiUIDAllocator(self.directory, replace(SCOPE, host_pool='other'))
        with closing(sqlite3.connect(self.a.path)) as db, db:
            db.execute('PRAGMA user_version=2')
        with self.assertRaises(UIDError):
            self.a.allocate(**BINDING)
        with closing(sqlite3.connect(self.a.path)) as db, db:
            db.execute('PRAGMA user_version=1')
            db.execute('CREATE TABLE surprise (x)')
        with self.assertRaises(UIDError):
            self.a.allocate(**BINDING)

    def test_deleted_allocation_is_not_reused(self):
        self.a.allocate(**BINDING)
        with closing(sqlite3.connect(self.a.path)) as db, db:
            db.execute('DELETE FROM allocations')
        with self.assertRaises(UIDError):
            self.a.allocate(**BINDING)

    def test_invalid_config_and_binding(self):
        for lo, hi in ((0, 10), (10000, 11000), (True, 12000), (65533, 65535),
                       (120000, 119999), (120000, 2147483647)):
            with self.subTest(lo=lo, hi=hi), self.assertRaises(UIDError):
                replace(SCOPE, uid_min=lo, uid_max=hi)
        for field, value in (('attempt', True), ('attempt', 0), ('token', -1),
                             ('token', '7'), ('project_id', 'not-uuid'),
                             ('job_id', BINDING['job_id'].replace('-', ''))):
            with self.subTest(field=field, value=value), self.assertRaises(UIDError):
                self.a.allocate(**(BINDING | {field: value}))
        self.assertEqual(self.a.allocate(**BINDING), SCOPE.uid_min)

    def test_failed_initialization_leaves_exclusive_tombstone(self):
        directory = Path(self.temp.name) / 'interrupted'
        with patch('ppt_service.control.pi_uid.sqlite3.connect', side_effect=sqlite3.OperationalError('disk')):
            with self.assertRaises(UIDError):
                PiUIDAllocator.initialize(directory, SCOPE)
        with self.assertRaises(UIDError):
            PiUIDAllocator.initialize(directory, SCOPE)
        with self.assertRaises(UIDError):
            PiUIDAllocator(directory, SCOPE)

    def test_commit_failure_and_lost_commit_receipt(self):
        connect = sqlite3.connect
        for committed in (False, True):
            class FailingCommit(sqlite3.Connection):
                def commit(self):
                    if committed:
                        super().commit()
                    raise sqlite3.OperationalError('injected commit failure')

            def failing_connect(*args, **kwargs):
                return connect(*args, **kwargs, factory=FailingCommit)

            with patch('ppt_service.control.pi_uid.sqlite3.connect', side_effect=failing_connect):
                with self.assertRaises(UIDError):
                    self.a.allocate(**BINDING)
            expected = SCOPE.uid_min + (2 if committed else 0)
            self.assertEqual(self.a.allocate(**BINDING), expected)

    def test_write_failure_never_returns_uid(self):
        with closing(sqlite3.connect(self.a.path)) as db, db:
            db.execute("CREATE TRIGGER reject_insert BEFORE INSERT ON allocations BEGIN SELECT RAISE(ABORT,'disk failure'); END")
        # Unexpected triggers fail before allocation; no successful receipt exists.
        with self.assertRaises(UIDError):
            self.a.allocate(**BINDING)
        with closing(sqlite3.connect(self.a.path)) as db, db:
            self.assertEqual(db.execute('SELECT count(*) FROM allocations').fetchone()[0], 0)

    def test_hardlink_rejected(self):
        alias = Path(self.temp.name) / 'alias'
        os.link(self.a.path, alias)
        with self.assertRaises(UIDError):
            self.a.allocate(**BINDING)

    @unittest.skipIf(os.name == 'nt', 'Windows symlink privilege unavailable; Linux follow-up required')
    def test_symlink_and_permissions_rejected(self):
        real = self.a.path.with_suffix('.real')
        self.a.path.rename(real)
        self.a.path.symlink_to(real)
        with self.assertRaises(UIDError):
            self.a.allocate(**BINDING)
        self.a.path.unlink(); real.rename(self.a.path)
        self.directory.chmod(0o755)
        with self.assertRaises(UIDError):
            self.a.allocate(**BINDING)
        self.directory.chmod(0o700)
        self.a.path.chmod(0o644)
        with self.assertRaises(UIDError):
            self.a.allocate(**BINDING)
        self.a.path.chmod(0o600)
        os.chown(self.a.path, 10000, 10000)
        try:
            with self.assertRaises(UIDError):
                self.a.allocate(**BINDING)
        finally:
            os.chown(self.a.path, 0, 0)
        alias = Path(self.temp.name) / 'linked-parent'
        alias.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(UIDError):
            PiUIDAllocator(alias, SCOPE)


if __name__ == '__main__':
    unittest.main()
