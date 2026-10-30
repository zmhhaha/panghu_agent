"""Real filesystem backup regression, run in the Linux server checks container."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys


HELPER = Path(__file__).resolve().parents[2] / 'deploy/k8s/consolidate_database.py'
spec = importlib.util.spec_from_file_location('ppt_consolidation_test', HELPER)
consolidation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(consolidation)


def test_backup_keeps_files_and_links_with_stale_unix_socket(tmp_path):
    root = tmp_path / 'state'
    root.mkdir(mode=0o700)
    (root / 'report.txt').write_text('unchanged report\n')
    os.link(root / 'report.txt', root / 'report-copy.txt')
    (root / 'report-link').symlink_to('report.txt')
    with socket.socket(socket.AF_UNIX) as runtime:
        runtime.bind(str(root / 'stale.sock'))
    code = consolidation.FILE_CODE.replace("Path('/var/lib/ppt-service')", 'Path(' + repr(str(root)) + ')')
    before = json.loads(subprocess.check_output([sys.executable, '-c', code, 'index']))
    assert before['data/stale.sock']['kind'] == 'socket'
    backup = tmp_path / 'files.tar'
    with backup.open('wb') as stream:
        subprocess.run([sys.executable, '-c', code, 'archive'], stdout=stream, check=True)
    assert consolidation.archive_index(backup) == {
        key: value for key, value in before.items() if value['kind'] != 'socket'
    }
    assert json.loads(subprocess.check_output([sys.executable, '-c', code, 'index'])) == before
