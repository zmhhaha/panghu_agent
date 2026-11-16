"""Explicitly start the redesigned developer corpus without legacy opinions.

Only tables in this dedicated lab SQLite file are reset. Business projects,
media and unrelated files are outside this boundary. Counts, not old opinions
or frozen artifacts, document the one-time baseline change.
"""
from pathlib import Path
import sqlite3

CORPUS_VERSION = 'cross-domain-requirements-v2'


def initialize_baseline(database):
    path=Path(database)
    path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path,timeout=30) as db:
        db.execute('PRAGMA foreign_keys=OFF')
        db.execute('BEGIN IMMEDIATE')
        exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lab_corpus_baseline'").fetchone()
        current=db.execute('SELECT version FROM lab_corpus_baseline LIMIT 1').fetchone() if exists else None
        if current==(CORPUS_VERSION,):
            return dict(version=CORPUS_VERSION,reset=False)
        tables=[row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'lab_*'")]
        removed={name:db.execute('SELECT COUNT(*) FROM "'+name.replace('"','""')+'"').fetchone()[0] for name in tables}
        for name in tables:db.execute('DROP TABLE "'+name.replace('"','""')+'"')
        db.execute('CREATE TABLE lab_corpus_baseline(version TEXT PRIMARY KEY)')
        db.execute('INSERT INTO lab_corpus_baseline VALUES(?)',(CORPUS_VERSION,))
        db.commit()
        if removed:db.execute('VACUUM')
        return dict(version=CORPUS_VERSION,reset=bool(removed),removed_counts=removed)
