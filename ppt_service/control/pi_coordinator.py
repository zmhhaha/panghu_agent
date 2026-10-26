"""Explicit fixture-only coordinator assembly. No live queue or model admission.

The CLI refuses any database whose name does not end in _fixture. It exercises
real queue transactions and existing sandbox parsing with a fixed synthetic CLI.
It never turns fixture output into accepted plans or published versions.
"""
import argparse
import hashlib
import os
from pathlib import Path
import sys
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from pi_worker.runtime import RuntimeSettings
from .execution import ControlError, RuntimeConfig
from .pi_dispatch import PiDispatch
from .pi_queue import RepositoryQueue
from .pi_staging import PiStager
from .pi_uid import PiUIDAllocator, UIDScope


def build_fixture_coordinator(sessions, config, allocator, settings, sandbox):
    if settings.fixture_mode is not True:
        raise ControlError('production_admission_not_configured')
    queue = RepositoryQueue(sessions, lease_seconds=config.lease_seconds)
    stager = PiStager(config, settings.runtime_revision, settings.skill_bundle_revision)
    scratch_root = config.data_root / 'runtime-scratch'
    scratch_root.mkdir(parents=True, exist_ok=True)

    def prepare_claim(claim):
        built = queue.build(claim)
        a = built.attempt
        binding = dict(project_id=a.project_id, job_id=a.job_id, attempt=a.attempt, token=a.token)
        if allocator.writers(**binding):
            # Existing identity requires reconciliation, never allocate a second
            # UID and accidentally stop an older one instead.
            raise ControlError('writer_reconciliation_required')
        allocator.allocate(**binding, role='pi')
        return built
    return PiDispatch(queue, config, allocator, settings, stager,
        config.data_root / 'runtime-scratch', sandbox=sandbox, build=prepare_claim)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture-once', action='store_true', required=True)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--ledger-dir', type=Path, required=True)
    parser.add_argument('--uid-min', type=int, required=True)
    parser.add_argument('--uid-max', type=int, required=True)
    args = parser.parse_args(argv)
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ControlError('dispatch_requires_linux_root')
    url = os.environ.get('PPT_PI_FIXTURE_DATABASE_URL', '')
    if not url or not (make_url(url).database or '').endswith('_fixture'):
        raise ControlError('isolated_fixture_database_required')
    root = args.data_root.absolute()
    root.mkdir(parents=True, exist_ok=True)
    scratch = root / 'runtime-scratch'; scratch.mkdir(exist_ok=True)
    skill = root / 'fixture-SKILL.md'
    if not skill.exists():
        skill.write_text('Synthetic coordinator fixture. Not a production skill bundle.\n', encoding='utf-8')
    fake = Path(__file__).resolve().parents[2] / 'tests/scientific_pi/runtime/fake_pi.py'
    if not fake.is_file():
        raise ControlError('fixture_binary_missing')
    settings = RuntimeSettings(command=(sys.executable,str(fake),'success'),
        node_command=(sys.executable,str(fake),'node'), provider='fixture',model='fixture-model',
        fixture_mode=True, runtime_revision='coordinator-fixture-v1',skill_bundle_revision='ppt-fixture-v1',
        skill_paths=(skill,),skill_hashes=(hashlib.sha256(skill.read_bytes()).hexdigest(),))
    scope = UIDScope('ppt-fixture','coordinator','isolated-container',args.uid_min,args.uid_max)
    allocator = (PiUIDAllocator(args.ledger_dir,scope) if args.ledger_dir.exists()
                 else PiUIDAllocator.initialize(args.ledger_dir,scope))
    from .pods import DockerSandbox
    config = RuntimeConfig(root)
    engine = create_engine(url)
    try:
        driver = build_fixture_coordinator(sessionmaker(engine,expire_on_commit=False),config,
            allocator,settings,DockerSandbox(config))
        outcome = driver.run_next()
        print('idle' if outcome is None else 'fixture_candidate_ready; business_validation_pending')
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
