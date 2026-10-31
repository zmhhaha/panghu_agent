"""Read-only PPT handoff diagnostics, never a collector or acceptance gate.

The fixed Worker prompt invokes this image-owned entry point with isolated
Python. It reads the host-exported task and actual output files, then prints
only bounded diagnostic metadata. The host later proves physical writer stop
and collects again using its original trusted execution envelope.
"""
from __future__ import annotations

import sys

# The direct image entry point is used with -I -B, so neither cwd nor a caller's
# PYTHONPATH can shadow service modules, and imports do not write bytecode.
if __package__ in (None, ''):
    sys.dont_write_bytecode = True
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import json
from pathlib import Path

from agent_service.artifacts import read_output
from agent_service.contracts import ServiceError
from agent_service.profiles.ppt import PPTProfile, _object


_HINTS = {
    'invalid_ppt_output': 'Check the phase, status and typed manifest fields. project_root is the child path under project/, without a project/ prefix; preserve the checkpoint root.',
    'ppt_candidate_incomplete': 'Supply all ordered SVG/PNG slide references, declared quality reports and exactly one PPTX artifact with role="pptx"; candidate is the manifest status.',
    'ppt_artifact_roster_mismatch': 'Declare exactly every actual project file, with its measured SHA256 and byte count; artifact paths are relative to project_root.',
    'invalid_ppt_quality_refs': 'quality contains only declared report paths or path references with matching SHA256, without prose, booleans or verdicts.',
    'ppt_approved_spec_changed': 'Generation must retain the approved design_spec.md and spec_lock.md bytes from the prepared checkpoint.',
    'ppt_checkpoint_binding_mismatch': 'The restored checkpoint must match the task checkpoint descriptor.',
    'ppt_formal_spec_required': 'The real project must contain design_spec.md and spec_lock.md.',
    'ppt_quality_reports_required': 'Declare the real quality report files and reference them from quality.',
    'ppt_artifact_type_forbidden': 'Remove temporary programs and unsupported runtime files from the project; do not remove required deliverables.',
    'ppt_linked_artifact': 'Project files and directories must be ordinary, without symbolic links, reparse points or hardlinked files.',
    'ppt_artifact_unreadable': 'Every project directory must be readable and traversable so the complete actual file tree can be measured; a failed scan cannot be omitted.',
    'invalid_ppt_task': 'Use the exact host-exported task file from the supplied command.',
    'ppt_handoff_unreadable': 'Ensure the manifest, exported task, restored checkpoint and declared project files exist and are readable.',
}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Advisory read-only PPT handoff contract check.')
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--task-file', required=True)
    args = parser.parse_args(argv)
    summary = dict(schema='ppt-handoff-preflight.v1', status='invalid',
        scope='handoff_contract_only', advisory_only=True,
        host_collection_required=True, publication_authorized=False)
    try:
        task_file = Path(args.task_file).absolute()
        task = _object(read_output(task_file.parent, task_file.name, limit=4 * 1024**2))
        try:
            profile = PPTProfile(task.get('phase'))
        except ValueError:
            raise ServiceError('invalid_ppt_task', 422) from None
        summary = profile.preflight_output(Path(args.workspace), task)
        summary['code'] = 'ppt_handoff_valid'
        result = 0
    except ServiceError as exc:
        summary['code'] = exc.code
        result = 1
    except OSError:
        summary['code'] = 'ppt_handoff_unreadable'
        result = 1
    except (TypeError, ValueError, RecursionError):
        summary['code'] = 'invalid_ppt_task'
        result = 1
    if result and summary['code'] in _HINTS:
        summary['hint'] = _HINTS[summary['code']]
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True, separators=(',', ':')))
    return result


if __name__ == '__main__':
    raise SystemExit(main())
