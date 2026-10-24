"""Storage admission must reject incomplete filesystem inventories."""
from dataclasses import replace
import errno
from pathlib import Path
import uuid

import pytest

from ppt_service.control.execution import Attempt,ControlError,RuntimeConfig
from ppt_service.control import staging


def test_regular_inventory_counts_nested_and_hidden_files_for_admission(tmp_path):
    (tmp_path/'source').write_bytes(b'abc')
    nested=tmp_path/'nested'
    nested.mkdir()
    (nested/'output').write_bytes(b'1234567')
    (nested/'.hidden').write_bytes(b'12345')
    assert staging.storage_bytes(tmp_path)==15
    config=RuntimeConfig(data_root=tmp_path,max_storage_bytes=18)
    staging.require_storage(config,3)
    with pytest.raises(ControlError,match='^storage_full$'):
        staging.require_storage(config,4)
    with pytest.raises(ControlError,match='^storage_full$'):
        staging.require_storage(replace(config,max_storage_bytes=14),0)


@pytest.mark.parametrize('failure',[PermissionError(errno.EACCES,'unreadable'),OSError(errno.EIO,'unavailable')],ids=['permission_denied','filesystem_io_error'])
def test_unreadable_subtree_refuses_admission_before_creating_attempt(tmp_path,monkeypatch,failure):
    (tmp_path/'visible').write_bytes(b'visible')
    hidden=tmp_path/'unreadable'
    hidden.mkdir()
    (hidden/'retained-output').write_bytes(b'previous attempt data')
    real_scandir=staging.os.scandir
    def scandir(path):
        if Path(path)==hidden:
            raise failure
        return real_scandir(path)
    # Exercise os.walk's real error callback, including after a partial count;
    # injecting scandir keeps the check meaningful under root and on Windows.
    monkeypatch.setattr(staging.os,'scandir',scandir)
    with pytest.raises(ControlError,match='^storage_inventory_unavailable$'):
        staging.storage_bytes(tmp_path)
    config=RuntimeConfig(data_root=tmp_path)
    stager=staging.Stager(config)
    attempt=Attempt(str(uuid.uuid4()),str(uuid.uuid4()),1,1,'plan_initial',{'hash':'a'*64},'synthetic-session')
    with pytest.raises(ControlError,match='^storage_inventory_unavailable$'):
        stager.stage(attempt)
    assert not stager.layout(attempt).root.exists()
