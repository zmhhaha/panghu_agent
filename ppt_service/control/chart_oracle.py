"""Deployment-owned chart expectations, bound to exact authorized input.

Without a reviewed entry only chart-free decks can pass verification. Editing
materials or requirements invalidates approval; Agent output is never an oracle.
"""
from __future__ import annotations
import copy
import os
import re
from pathlib import Path
from .execution import ControlError
from .staging import read_json

SHA = re.compile(r'[0-9a-f]{64}\Z')

class ChartOracle:
    def __init__(self, path: Path | None = None):
        self.entries = {}
        if path is None:return
        path = Path(path).absolute()
        value = read_json(path.parent, path.name)
        if not isinstance(value, dict) or value.get('schema_version') != 1:
            raise ControlError('invalid_chart_oracle')
        entries = value.get('snapshots')
        if not isinstance(entries, dict) or len(entries) > 1000:
            raise ControlError('invalid_chart_oracle')
        for digest, entry in entries.items():
            if not isinstance(digest, str) or not SHA.fullmatch(digest) or not isinstance(entry, dict):
                raise ControlError('invalid_chart_oracle')
            charts, sources = entry.get('expected_charts'), entry.get('source_sha256')
            if (not isinstance(charts, list) or len(charts) > 100
                    or not isinstance(sources, list) or len(sources) > 10
                    or any(not isinstance(s, str) or not SHA.fullmatch(s) for s in sources)
                    or len(sources) != len(set(sources)) or (charts and not sources)):
                raise ControlError('invalid_chart_oracle')
            self.entries[digest] = copy.deepcopy(entry)

    @classmethod
    def env(cls):
        return cls(Path(os.environ['PPT_CHART_ORACLE_FILE']) if os.getenv('PPT_CHART_ORACLE_FILE') else None)

    def __call__(self, attempt, checkpoint_manifest=None):
        entry = copy.deepcopy(self.entries.get(attempt.snapshot['hash'],
            {'expected_charts': [], 'source_sha256': []}))
        authorized = {row['sha256'] for row in attempt.snapshot.get('assets', [])}
        if not set(entry['source_sha256']).issubset(authorized):raise ControlError('oracle_source_mismatch')
        return entry
