"""Run only on the isolated Linux server test container; no production paths."""
import json
import os
import sys

import pytest

from ppt_service.deploy.ops.storage_readiness import inspect_storage

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux no-follow probe")


def probe(root, **bounds):
    return inspect_storage(root, minimum_free_bytes=0, **bounds)


def codes(receipt):
    return {finding["code"] for finding in receipt["findings"]}


def test_readonly_probe_reports_capacity_without_contents_names_or_snapshot_claim(tmp_path):
    root = tmp_path / "private-store"
    root.mkdir(mode=0o700)
    private = root / "private-project-title-never-print"
    private.write_text("private-source-bytes-never-print", encoding="utf-8")
    private.chmod(0o600)
    before = private.stat()
    receipt = probe(root, expected_root_uid=os.getuid())
    assert receipt["status"] == "ready"
    assert receipt["inspection_complete"] is True
    assert receipt["counts"] == {"directories": 1, "entries": 1, "files": 1, "logical_bytes": before.st_size}
    assert receipt["capacity"]["available_bytes"] >= 0
    assert receipt["consistent_snapshot"] is receipt["backup_verified"] is False
    assert "private-project-title" not in json.dumps(receipt)
    assert "private-source-bytes" not in json.dumps(receipt)
    assert private.stat().st_mtime_ns == before.st_mtime_ns
    assert private.stat().st_atime_ns == before.st_atime_ns


def test_links_special_files_and_budgets_never_become_healthy_inventory(tmp_path):
    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    regular = root / "blob"
    regular.write_bytes(b"synthetic")
    regular.chmod(0o600)
    os.link(regular, root / "hard-link")
    (root / "outside-link").symlink_to(tmp_path)
    os.mkfifo(root / "fifo", mode=0o600)
    receipt = probe(root, maximum_tree_bytes=1)
    assert receipt["status"] == "needs_attention"
    assert receipt["inspection_complete"] is False
    assert {"symbolic_link", "multiple_hard_links", "special_file", "configured_storage_budget_exceeded"} <= codes(receipt)


def test_root_ancestors_are_never_followed_or_created(tmp_path):
    root = tmp_path / "original"
    root.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    assert probe(alias)["status"] == "needs_attention"
    assert "storage_inventory_unavailable" in codes(probe(alias))
    absent = tmp_path / "not-created"
    assert probe(absent)["inspection_complete"] is False
    assert not absent.exists()


def test_private_root_owner_entry_and_space_bounds_are_explicit(tmp_path):
    root = tmp_path / "store"
    root.mkdir(mode=0o755)
    for index in range(3):
        (root / str(index)).write_bytes(b"x")
    receipt = inspect_storage(root, minimum_free_bytes=2**63,
                              maximum_entries=1, expected_root_uid=os.getuid() + 1)
    assert receipt["status"] == "needs_attention"
    assert receipt["inspection_complete"] is False
    assert {"root_not_private", "root_owner_mismatch", "insufficient_free_space", "inventory_entry_limit"} <= codes(receipt)


@pytest.mark.skipif(getattr(os, "geteuid", lambda: 0)() == 0, reason="Must use the actual non-root inspection identity")
def test_unreadable_subtree_is_not_silently_omitted(tmp_path):
    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    blocked = root / "not-readable"
    blocked.mkdir(mode=0o700)
    (blocked / "retained").write_bytes(b"synthetic")
    blocked.chmod(0o000)
    try:
        receipt = probe(root)
        assert receipt["status"] == "needs_attention"
        assert receipt["inspection_complete"] is False
        assert "storage_inventory_unavailable" in codes(receipt)
    finally:
        blocked.chmod(0o700)


@pytest.mark.parametrize("bounds", [{"minimum_free_bytes": -1}, {"minimum_free_bytes": True},
                                   {"minimum_free_bytes": 0, "maximum_entries": 0}])
def test_invalid_bounds_never_open_data(tmp_path, bounds):
    with pytest.raises(ValueError):
        inspect_storage(tmp_path / "absent", **bounds)
