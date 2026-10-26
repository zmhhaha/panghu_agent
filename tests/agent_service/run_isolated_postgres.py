#!/usr/bin/env python3
"""Run Agent Service tests against disposable, offline PostgreSQL images.

Linux root Docker host only. Images must already exist; every run uses
--pull=never. PostgreSQL has no TCP listener, persistent database or production
credentials. A private /dev/shm directory shares only its temporary Unix socket.
This script never deletes source/log directories or manipulates Kubernetes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid


SITE_IMAGE = "arm-cluster-master:5000/ppt-service-site@sha256:96070732086f057bb9b653a889e134f481ecda90801352f8e143042f76a88f1e"
PG_IMAGE = "sha256:ef738a34a8651d11b2bace81c55c7e2187f786b484add6c83070884340074368"
OWNER_LABEL = "agent.execution.test-owner"
DATABASE = "agent_service_agent_fixture"
DATABASE_URL = "postgresql+psycopg://postgres@/" + DATABASE + "?host=/pgsocket"
RESULT_MARKER = "AGENT_ISOLATED_PG_RESULT="
INNER_RUNNER = r'''
import importlib.metadata, json, os, subprocess, sys, time, xml.etree.ElementTree as ET
started = time.monotonic()
phases, returncode = [], 0
for index, targets in enumerate(json.loads(os.environ['AGENT_TEST_TARGETS'])):
    print('AGENT_TEST_PHASE=' + json.dumps(targets), flush=True)
    xml = '/tmp/agent-results-' + str(index) + '.xml'
    command = [sys.executable, '-m', 'pytest', *targets, '-q', '-p', 'no:cacheprovider',
        '--tb=short', '-rs', '--basetemp=/tmp/agent-tests-' + str(index), '--junitxml=' + xml]
    result = subprocess.run(command, check=False)
    counts = dict.fromkeys(('tests', 'failures', 'errors', 'skipped'))
    try:
        root = ET.parse(xml).getroot()
        suites = [root] if root.tag == 'testsuite' else list(root.findall('testsuite'))
        for key in counts:
            counts[key] = sum(int(s.get(key, '0')) for s in suites)
    except (OSError, ValueError, ET.ParseError):
        pass
    phases.append(dict(targets=targets, returncode=result.returncode, **counts))
    returncode = returncode or result.returncode
counts = {key: sum(phase[key] for phase in phases) if all(type(phase[key]) is int
    for phase in phases) else None for key in ('tests', 'failures', 'errors', 'skipped')}
versions = {}
for name in ('pytest', 'pytest-subtests', 'fastapi', 'starlette', 'pydantic',
             'httpx', 'SQLAlchemy', 'psycopg', 'requests', 'PyJWT', 'Pillow'):
    try:
        versions[name] = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        versions[name] = None
print('AGENT_ISOLATED_PG_RESULT=' + json.dumps(dict(returncode=returncode,
    duration_seconds=round(time.monotonic()-started, 3), phases=phases,
    dependency_versions=versions, **counts)), flush=True)
sys.exit(returncode)
'''


def test_targets(suite):
    if suite == 'postgres':
        return [['tests/agent_service/test_postgres.py']]
    groups = [['tests/agent_service']]
    if suite == 'connectors':
        # Distinct processes prevent test_api.py imports in the two suites
        # from colliding. Business tests remain offline synthetic peers.
        groups += [['tests/scientific_pi/api/test_agent_service.py',
                    'tests/scientific_pi/api/test_academic_observations.py'],
                   ['ppt_service/tests/control/test_agent_connector.py']]
    return groups


class HarnessFailure(RuntimeError):
    pass


def checked_path(value, *, must_exist=True):
    path = Path(value).absolute()
    for component in (*reversed(path.parents), path):
        try:
            info = component.lstat()
        except FileNotFoundError:
            if component != path or must_exist:
                raise HarnessFailure("path_missing") from None
            continue
        if stat.S_ISLNK(info.st_mode):
            raise HarnessFailure("path_link_rejected")
    resolved = path.resolve(strict=must_exist)
    if any(character in str(resolved) for character in (",", "\n", "\r", "\x00")):
        raise HarnessFailure("unsafe_docker_mount_path")
    return resolved


def execute(arguments, *, timeout=15, log=None):
    try:
        result = subprocess.run(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=timeout, check=False,
                                text=True, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as exc:
        if log is not None:
            output = exc.stdout or b""
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")
            log.write_text(output[-2 * 1024 * 1024:], encoding="utf-8")
        raise HarnessFailure("command_timeout") from None
    if log is not None:
        log.write_text(result.stdout[-2 * 1024 * 1024:], encoding="utf-8")
    return result


def require(result, code):
    if result.returncode != 0:
        raise HarnessFailure(code)
    return result.stdout


def docker_base(name, run_id, *, user, memory, pids=128, cpus="1"):
    return ["docker", "run", "--pull=never", "--name", name,
            "--label", OWNER_LABEL + "=" + run_id,
            "--network", "none", "--read-only", "--user", user,
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", memory, "--pids-limit", str(pids), "--cpus", cpus]


def cleanup_containers(names, run_id):
    success = True
    for name in reversed(names):
        try:
            value = execute(["docker", "container", "inspect", "--format",
                             '{{index .Config.Labels "' + OWNER_LABEL + '"}}', name])
            if value.returncode == 0:
                if value.stdout.strip() != run_id:
                    success = False  # Never remove an unrelated namesake.
                    continue
                if execute(["docker", "rm", "-f", name]).returncode != 0:
                    success = False
            else:
                # A daemon error is not evidence of nonexistence.
                remaining = execute(["docker", "ps", "-aq", "--filter", "name=^" + name + "$"])
                if remaining.returncode != 0 or remaining.stdout.strip():
                    success = False
        except (HarnessFailure, OSError):
            success = False
    try:
        remaining = execute(["docker", "ps", "-aq", "--filter", "label=" + OWNER_LABEL + "=" + run_id])
        success = success and remaining.returncode == 0 and not remaining.stdout.strip()
    except (HarnessFailure, OSError):
        success = False
    return success


def validate_source(code_root, suite='postgres'):
    relative = ['agent_service', 'tests/agent_service']
    if suite != 'postgres':
        relative += ['scientific_agent', 'pi_worker/__init__.py', 'pi_worker/research_tools',
                     'tools/__init__.py', 'tools/academic', 'scientific_skills',
                     'tests/scientific_pi/revisions/test_revision_validation.py']
    if suite == 'connectors':
        relative += ['app/api/scientific_agent.py', 'tools/sqlite_client.py',
            'ppt_service/__init__.py', 'ppt_service/control', 'ppt_service/server',
            'ppt_service/worker', 'tests/scientific_pi/api',
            'ppt_service/tests/control/test_agent_connector.py']
    roots = [checked_path(code_root / path) for path in relative]
    if not (roots[0] / "store.py").is_file() or not (roots[1] / "test_postgres.py").is_file():
        raise HarnessFailure("reviewed_source_missing")
    # Mount only explicit source trees/files, never .env/HOME/keys or the whole
    # repository. Related business source is included only for connector runs.
    for root in roots:
        for path in (root, *(root.rglob("*") if root.is_dir() else ())):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise HarnessFailure("source_link_or_special_file")
            if path.name in {".env", ".ssh", "credentials", "credentials.json"} or path.suffix in {".pem", ".key"}:
                raise HarnessFailure("private_file_in_source_tree")
            read = stat.S_IRUSR if info.st_uid == 10000 else stat.S_IRGRP if info.st_gid == 10000 else stat.S_IROTH
            search = stat.S_IXUSR if info.st_uid == 10000 else stat.S_IXGRP if info.st_gid == 10000 else stat.S_IXOTH
            if not info.st_mode & read or (stat.S_ISDIR(info.st_mode) and not info.st_mode & search):
                raise HarnessFailure("source_not_readable_by_test_uid")
    return roots


def local_linux_docker():
    override = os.environ.get("DOCKER_HOST", "")
    if override and not override.startswith("unix://"):
        raise HarnessFailure("local_docker_socket_required")
    endpoint = require(execute(["docker", "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"]),
                       "docker_context_unavailable")
    try:
        value = json.loads(endpoint)
    except ValueError:
        raise HarnessFailure("docker_context_invalid") from None
    if not isinstance(value, str) or not value.startswith("unix://"):
        raise HarnessFailure("local_docker_socket_required")
    kind = require(execute(["docker", "info", "--format", "{{.OSType}}"]), "docker_unavailable")
    if kind.strip() != "linux":
        raise HarnessFailure("linux_docker_required")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--report-dir", type=Path, required=True,
                        help="New report directory; it is preserved, never recursively deleted")
    parser.add_argument("--suite", choices=("postgres", "all", "connectors"), default="postgres",
                        help="Fixed target; connectors adds offline business tests, all modes use disposable PostgreSQL")
    parser.add_argument('--test-image', default=SITE_IMAGE,
                        help='Already cached dependency image, fixed sha256 identity required')
    parser.add_argument('--postgres-image', default=PG_IMAGE,
                        help='Already cached PostgreSQL image, fixed sha256 identity required')
    parser.add_argument('--timeout', type=int, default=900,
                        help='Total test-container timeout in seconds (60..3600)')
    args = parser.parse_args(argv)
    for value in (args.test_image, args.postgres_image):
        if not re.fullmatch(r'(?:[A-Za-z0-9._:/-]+@)?sha256:[a-f0-9]{64}', value):
            parser.error('Use an immutable repository@sha256 digest or cached sha256 image ID')
    if not 60 <= args.timeout <= 3600:
        parser.error('Timeout must be between 60 and 3600 seconds')
    if sys.platform != "linux" or os.geteuid() != 0:
        parser.error("Run on the explicitly selected Linux root Docker host")
    code_root = checked_path(args.code_root)
    report_dir = checked_path(args.report_dir, must_exist=False)
    if report_dir.exists():
        raise HarnessFailure("new_report_directory_required")
    if report_dir == code_root or report_dir in code_root.parents or code_root in report_dir.parents:
        raise HarnessFailure("source_report_overlap")
    report_dir.mkdir(mode=0o700)
    run_id = "agent-pg-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:12]
    names = [run_id + "-uid", run_id + "-postgres", run_id + "-tests"]
    started = time.monotonic()
    report = {"schema_version": 1, "run_id": run_id, "model_requests": 0,
              "suite": args.suite,
              "no_model_calls": True, "test_returncode": None, "tests": None,
              "passed": False, "error_code": None,
              "images": {"tests": args.test_image, "postgres": args.postgres_image},
              "cleanup": {"containers_removed": False, "socket_removed": False}}
    socket_root = None
    socket_identity = None
    old_handlers = {}

    def interrupted(signum, frame):
        raise HarnessFailure("harness_interrupted")

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            old_handlers[signum] = signal.signal(signum, interrupted)
        roots = validate_source(code_root, args.suite)
        local_linux_docker()
        for image in (args.test_image, args.postgres_image):
            require(execute(["docker", "image", "inspect", "--format", "{{.Id}}", image]), "cached_image_missing")
        probe = docker_base(names[0], run_id, user="0:0", memory="64m", pids=32, cpus="0.25")
        probe += ["--rm", "--entrypoint", "id", args.postgres_image, "postgres"]
        identity = require(execute(probe), "postgres_uid_probe_failed")
        found = re.fullmatch(r"uid=(\d+)\(postgres\) gid=(\d+)\(postgres\)(?: groups=.*)?\s*", identity)
        if found is None or not 1 <= int(found[1]) < 65536 or not 1 <= int(found[2]) < 65536:
            raise HarnessFailure("postgres_uid_invalid")
        pg_uid, pg_gid = int(found[1]), int(found[2])
        report["postgres_uid"] = pg_uid
        shm = checked_path("/dev/shm")
        mountinfo = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
        if not any(" /dev/shm " in line and " - tmpfs " in line for line in mountinfo.splitlines()):
            raise HarnessFailure("tmpfs_socket_root_required")
        socket_root = Path(tempfile.mkdtemp(prefix=run_id + "-", dir=shm))
        socket_identity = socket_root.stat()
        shared = socket_root / "socket"
        shared.mkdir(mode=0o1777)
        os.chown(shared, pg_uid, pg_gid)
        shared.chmod(0o1777)
        pg = docker_base(names[1], run_id, user=f"{pg_uid}:{pg_gid}", memory="512m")
        pg += ["-d", "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m,mode=1777",
               "--tmpfs", f"/var/lib/postgresql/data:rw,nosuid,nodev,size=384m,uid={pg_uid},gid={pg_gid},mode=700",
               "--mount", f"type=bind,source={shared},target=/pgsocket",
               "--mount", f"type=bind,source={shared},target=/var/run/postgresql",
               "-e", "POSTGRES_DB=" + DATABASE, "-e", "POSTGRES_USER=postgres",
               "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "-e", "PGHOST=/pgsocket",
               args.postgres_image, "-c", "listen_addresses=", "-c", "unix_socket_directories=/pgsocket",
               "-c", "unix_socket_permissions=0777"]
        require(execute(pg), "postgres_start_failed")
        until = time.monotonic() + 45
        ready = False
        while time.monotonic() < until:
            process = execute(["docker", "exec", names[1], "cat", "/proc/1/comm"], timeout=5)
            if process.returncode == 0 and process.stdout.strip() == "postgres":
                check = execute(["docker", "exec", names[1], "pg_isready", "-h", "/pgsocket",
                                 "-U", "postgres", "-d", DATABASE], timeout=5)
                if check.returncode == 0:
                    ready = True
                    break
            time.sleep(0.25)
        if not ready:
            raise HarnessFailure("postgres_readiness_failed")
        test = docker_base(names[2], run_id, user="10000:10000", memory="768m", pids=128)
        test += ["--tmpfs", "/tmp:rw,nosuid,nodev,size=384m,mode=1777"]
        for root in roots:
            test += ['--mount', f'type=bind,source={root},target=/src/{root.relative_to(code_root).as_posix()},readonly']
        test += [
                 "--mount", f"type=bind,source={shared},target=/pgsocket",
                 "-w", "/src", "-e", "PYTHONPATH=/src", "-e", "PYTHONDONTWRITEBYTECODE=1",
                 '-e', 'PYTHONUNBUFFERED=1', '-e', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1',
                 '-e', 'PYTEST_PLUGINS=pytest_subtests',
                 '-e', 'AGENT_TEST_TARGETS=' + json.dumps(test_targets(args.suite)),
                 "-e", "AGENT_TEST_DATABASE_URL=" + DATABASE_URL,
                 "--entrypoint", "python", args.test_image, "-c", INNER_RUNNER]
        result = execute(test, timeout=args.timeout, log=report_dir / "postgres-tests.log")
        report["test_returncode"] = result.returncode
        summaries = [line[len(RESULT_MARKER):] for line in result.stdout.splitlines() if line.startswith(RESULT_MARKER)]
        if len(summaries) != 1:
            raise HarnessFailure("test_summary_missing")
        try:
            summary = json.loads(summaries[0])
        except ValueError:
            raise HarnessFailure("test_summary_invalid") from None
        report["tests"] = summary
        report["passed"] = (result.returncode == 0 and summary.get("returncode") == 0
                            and type(summary.get("tests")) is int and summary["tests"] > 0
                            and summary.get("failures") == 0 and summary.get("errors") == 0
                            and summary.get("skipped") == 0)
        if not report["passed"]:
            report["error_code"] = "postgres_tests_failed"
    except HarnessFailure as exc:
        report["error_code"] = str(exc)
    except Exception as exc:
        report["error_code"] = "harness_" + type(exc).__name__.lower()
    finally:
        # Avoid SIGINT/SIGTERM interrupting removal half-way; the parent can still
        # impose its external timeout. Only this random run's labeled names go.
        for signum in old_handlers:
            signal.signal(signum, signal.SIG_IGN)
        try:
            execute(["docker", "logs", "--tail", "200", names[1]], timeout=10,
                    log=report_dir / "postgres-container.log")
        except (HarnessFailure, OSError):
            pass
        containers_removed = cleanup_containers(names, run_id)
        report["cleanup"]["containers_removed"] = containers_removed
        if socket_root is None:
            report["cleanup"]["socket_removed"] = True
        elif containers_removed:
            try:
                current = socket_root.lstat()
                if (socket_root.parent.resolve() != Path("/dev/shm").resolve()
                        or not socket_root.name.startswith(run_id + "-")
                        or stat.S_ISLNK(current.st_mode) or not stat.S_ISDIR(current.st_mode)
                        or not os.path.samestat(current, socket_identity)):
                    raise HarnessFailure("socket_cleanup_identity_changed")
                # POSIX shutil.rmtree uses FD-based symlink-safe traversal.
                if not shutil.rmtree.avoids_symlink_attacks:
                    raise HarnessFailure("safe_socket_cleanup_unavailable")
                shutil.rmtree(socket_root)
                report["cleanup"]["socket_removed"] = not socket_root.exists()
            except Exception:
                report["cleanup"]["socket_removed"] = False
        report["duration_seconds"] = round(time.monotonic() - started, 3)
        report["cleanup_complete"] = all(report["cleanup"].values())
        if not report["cleanup_complete"]:
            report["passed"] = False
            report["error_code"] = report["error_code"] or "owned_cleanup_failed"
        (report_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] and report["cleanup_complete"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except HarnessFailure as exc:
        print(json.dumps({"passed": False, "error_code": str(exc)}))
        raise SystemExit(2)
