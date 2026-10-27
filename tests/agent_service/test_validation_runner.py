"""Offline Linux harness planning and result handling, without Docker calls."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

import run_isolated_postgres as runner


def test_connector_suites_use_separate_pytest_processes_and_keep_real_postgres():
    groups = runner.test_targets('connectors')
    assert groups[0] == ['tests/agent_service']
    assert groups[1] == ['tests/scientific_pi/api/test_agent_service.py',
                         'tests/scientific_pi/api/test_academic_observations.py']
    assert groups[2] == ['ppt_service/tests/control/test_agent_connector.py']
    assert groups[3] == ['ppt_service/tests/server/test_acceptance_migration.py']
    assert runner.test_targets('postgres') == [['tests/agent_service/test_postgres.py']]


def test_inner_runner_keeps_failure_and_runs_remaining_groups(monkeypatch, capsys):
    import importlib.metadata
    import subprocess
    import xml.etree.ElementTree as ET
    calls = []
    groups = [['synthetic-one.py'], ['synthetic-two.py']]
    monkeypatch.setenv('AGENT_TEST_TARGETS', json.dumps(groups))
    def run(arguments, **options):
        calls.append(arguments)
        return SimpleNamespace(returncode=1 if len(calls) == 1 else 0)
    monkeypatch.setattr(subprocess, 'run', run)
    def parse(path):
        first = path.endswith('-0.xml')
        return ET.ElementTree(ET.Element('testsuite', tests='2', errors='0',
            failures='1' if first else '0', skipped='0'))
    monkeypatch.setattr(ET, 'parse', parse)
    monkeypatch.setattr(importlib.metadata, 'version', lambda name: 'fixture-version')
    with pytest.raises(SystemExit) as exit_info:
        exec(runner.INNER_RUNNER, {})
    assert exit_info.value.code == 1 and len(calls) == 2
    assert all(call[:3] == [sys.executable, '-m', 'pytest'] for call in calls)
    lines = capsys.readouterr().out.splitlines()
    report = json.loads(next(line[len(runner.RESULT_MARKER):]
        for line in lines if line.startswith(runner.RESULT_MARKER)))
    assert report['tests'] == 4 and report['failures'] == 1 and report['returncode'] == 1
    assert report['dependency_versions']['pytest-subtests'] == 'fixture-version'


def test_source_plan_excludes_repository_credentials_and_rejects_private_source(tmp_path):
    relative = ['agent_service/store.py', 'tests/agent_service/test_postgres.py',
        'scientific_agent/__init__.py', 'pi_worker/__init__.py',
        'pi_worker/research_tools/__init__.py', 'tools/__init__.py',
        'tools/academic/__init__.py', 'scientific_skills/manifest.json',
        'tests/scientific_pi/revisions/test_revision_validation.py']
    for name in relative:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('source', encoding='utf-8')
    (tmp_path / '.env').write_text('NEVER_MOUNT_REPOSITORY_SECRET', encoding='utf-8')
    roots = runner.validate_source(tmp_path, 'all')
    assert tmp_path not in roots and tmp_path / '.env' not in roots
    assert tmp_path / 'tests/scientific_pi/revisions/test_revision_validation.py' in roots
    assert tmp_path / 'tests/scientific_pi/revisions' not in roots
    (tmp_path / 'scientific_agent' / '.env').write_text('unsafe-source', encoding='utf-8')
    with pytest.raises(runner.HarnessFailure, match='private_file_in_source_tree'):
        runner.validate_source(tmp_path, 'all')
