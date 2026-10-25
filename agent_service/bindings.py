"""Worker-private durable Hermes admission CAS, separate from Agent HOME."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .engines import NativeBinding


class SQLiteNativeBindings:
    def __init__(self, path: Path):
        self.path = Path(path)

    def initialize(self):
        if self.path.is_symlink():
            raise RuntimeError('Unsafe native binding store')
        with self._connection() as con:
            con.execute('CREATE TABLE IF NOT EXISTS bindings (execution_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, admission_key TEXT NOT NULL, admission_started INTEGER NOT NULL DEFAULT 0, run_id TEXT)')
        self.path.chmod(0o600)

    @contextmanager
    def _connection(self):
        con = sqlite3.connect(str(self.path), timeout=10)
        con.row_factory = sqlite3.Row
        try:
            con.execute('BEGIN IMMEDIATE')
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    @staticmethod
    def _binding(row):
        return None if row is None else NativeBinding(row['execution_id'], row['session_id'], row['admission_key'],
                                                     bool(row['admission_started']), row['run_id'])

    def load(self, execution_id):
        with self._connection() as con:
            return self._binding(con.execute('SELECT * FROM bindings WHERE execution_id=?', (execution_id,)).fetchone())

    def prepare(self, execution_id, session_id, admission_key):
        with self._connection() as con:
            row = con.execute('SELECT * FROM bindings WHERE execution_id=?', (execution_id,)).fetchone()
            if row is not None:
                if row['session_id'] != session_id or row['admission_key'] != admission_key:
                    raise RuntimeError('Native binding conflict')
                return self._binding(row)
            con.execute('INSERT INTO bindings(execution_id,session_id,admission_key) VALUES (?,?,?)',
                        (execution_id, session_id, admission_key))
            return NativeBinding(execution_id, session_id, admission_key)

    def mark_admission_started(self, execution_id):
        with self._connection() as con:
            return con.execute('UPDATE bindings SET admission_started=1 WHERE execution_id=? AND admission_started=0',
                               (execution_id,)).rowcount == 1

    def bind_run(self, execution_id, run_id):
        with self._connection() as con:
            row = con.execute('SELECT * FROM bindings WHERE execution_id=?', (execution_id,)).fetchone()
            if row is None or not row['admission_started'] or (row['run_id'] is not None and row['run_id'] != run_id):
                raise RuntimeError('Native binding conflict')
            con.execute('UPDATE bindings SET run_id=? WHERE execution_id=?', (run_id, execution_id))
            return NativeBinding(execution_id, row['session_id'], row['admission_key'], True, run_id)
