"""Bounded parser failure transport; no model, container or database required."""
import json
from pathlib import Path
import sys

import pytest

from ppt_service.control import parsing
from ppt_service.control.execution import (
    ControlError, RuntimeConfig, parse_failure_reason, parse_failure_report,
)
from ppt_service.control.pods import DockerSandbox
from ppt_service.control.staging import atomic_json


@pytest.mark.parametrize('error,expected', [
    (ControlError('pdf_page_limit'), 'pdf_page_limit'),
    (ControlError('/private/source.pdf?token=SECRET'), 'parse_failed'),
    (ValueError('source text and private stack SECRET'), 'parse_failed'),
])
def test_parser_entrypoint_emits_only_bound_safe_failure(tmp_path, monkeypatch, error, expected):
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    atomic_json(inputs / 'inventory.json', {'input_snapshot_sha256': 'a' * 64, 'sources': []})
    output = tmp_path / 'parsed.json'
    monkeypatch.setattr(sys, 'argv', ['parser', '--inputs', str(inputs), '--output', str(output)])
    def reject(*args):
        raise error
    monkeypatch.setattr(parsing, 'parse_inputs', reject)
    assert parsing.main() == 2
    report = json.loads(output.read_text())
    assert report == {'schema': 'ppt-service.parse-failure.v1', 'status': 'failed',
                      'input_snapshot_sha256': 'a' * 64, 'error_code': expected}
    assert parse_failure_reason(report, 'a' * 64) == expected
    assert 'SECRET' not in output.read_text() and str(tmp_path) not in output.read_text()


@pytest.mark.parametrize('changes', [
    {'schema': 'untrusted.v1'},
    {'status': 'succeeded'},
    {'input_snapshot_sha256': 'b' * 64},
    {'error_code': 'unregistered_reason'},
    {'error_code': ['pdf_page_limit']},
    {'message': '/private/source.pdf SECRET'},
    {'sources': []},
])
def test_failure_reason_rejects_unknown_unbound_or_extra_diagnostics(changes):
    report = dict(parse_failure_report('pdf_page_limit', 'a' * 64), **changes)
    assert parse_failure_reason(report, 'a' * 64) is None


class FailedParserDocker:
    def __init__(self, report, exit_code, wait_error=None):
        self.report, self.exit_code, self.wait_error = report, exit_code, wait_error
        self.calls = []

    def call(self, *args, **kwargs):
        self.calls.append(args)
        if args[0] == 'create':
            self.name = args[args.index('--name') + 1]
            mount = next(value for value in args if str(value).startswith('type=bind,') and 'dst=/output' in value)
            self.output = Path(mount.split('src=', 1)[1].split(',', 1)[0])
        if args[0] == 'wait':
            atomic_json(self.output / 'parsed.json', self.report)
            if self.wait_error:
                raise self.wait_error
            return 0, str(self.exit_code).encode()
        return 0, b''


@pytest.mark.parametrize('changes,exit_code,expected', [
    ({}, 2, 'pdf_page_limit'),
    ({'error_code': '/private/source.pdf SECRET'}, 2, 'parse_failed'),
    ({'input_snapshot_sha256': 'b' * 64}, 2, 'parse_failed'),
    ({}, 0, 'parse_failed'),
])
def test_docker_failure_reason_is_filtered_bound_and_never_promoted(tmp_path, changes, exit_code, expected):
    report = dict(parse_failure_report('pdf_page_limit', 'a' * 64), **changes)
    docker = FailedParserDocker(report, exit_code)
    sandbox = DockerSandbox(RuntimeConfig(tmp_path), docker)
    with pytest.raises(ControlError) as raised:
        sandbox.parse(tmp_path, {'input_snapshot_sha256': 'a' * 64})
    assert raised.value.code == expected
    assert [call for call in docker.calls if call[0] == 'rm'] == [
        ('rm', '--force', '--volumes', docker.name)]


def test_docker_diagnostic_cannot_override_wait_failure(tmp_path):
    report = parse_failure_report('pdf_page_limit', 'a' * 64)
    docker = FailedParserDocker(report, 2, ControlError('control_command_unavailable'))
    with pytest.raises(ControlError) as raised:
        DockerSandbox(RuntimeConfig(tmp_path), docker).parse(tmp_path, {'input_snapshot_sha256': 'a' * 64})
    assert raised.value.code == 'control_command_unavailable'
