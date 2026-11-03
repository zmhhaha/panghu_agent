#!/usr/bin/env python3
"""Read-only Linux capacity/metadata check; never a backup or stop proof.

No names, file contents, database URLs, or exception text are printed. A live
tree may change while it is inspected, so this is operational headroom and
permission evidence only. The offline migration tools own backup verification.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
import stat

from ppt_service.deploy.k8s.migration.snapshot import Refused, open_dir, signature

SCHEMA = "ppt-service.storage-readiness.v1"


def _integer(value, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("invalid probe bound")
    return value


def inspect_storage(data_root, *, minimum_free_bytes, maximum_tree_bytes=None,
                    maximum_entries=200000, expected_root_uid=None):
    """Inspect one existing tree through no-follow directory FDs, without writes.

    Do not run as a more privileged UID to hide an application's inability to
    traverse its data. Foreign devices, links and uncertain enumeration remain
    explicit findings rather than an underestimated successful inventory.
    """
    _integer(minimum_free_bytes)
    _integer(maximum_entries, minimum=1)
    if maximum_tree_bytes is not None:
        _integer(maximum_tree_bytes, minimum=1)
    if expected_root_uid is not None:
        _integer(expected_root_uid)
    findings = Counter()
    counts = {"entries": 0, "directories": 0, "files": 0, "logical_bytes": 0}
    capacity = None
    complete = False

    def scan(fd, device, depth):
        if depth > 64:
            findings["directory_depth_limit"] += 1
            return False
        before = os.fstat(fd)
        if before.st_mode & 0o022:
            findings["shared_writable_directory"] += 1
        if before.st_mode & 0o6000:
            findings["special_permission_bits"] += 1
        finished = True
        with os.scandir(fd) as entries:
            for entry in entries:
                counts["entries"] += 1
                if counts["entries"] > maximum_entries:
                    findings["inventory_entry_limit"] += 1
                    return False
                info = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                if info.st_dev != device:
                    findings["foreign_device"] += 1
                    finished = False
                elif stat.S_ISLNK(info.st_mode):
                    findings["symbolic_link"] += 1
                    finished = False
                elif stat.S_ISDIR(info.st_mode):
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY |
                                    os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                    try:
                        if signature(os.fstat(child)) != signature(info):
                            findings["inventory_changed"] += 1
                            finished = False
                        else:
                            counts["directories"] += 1
                            finished = scan(child, device, depth + 1) and finished
                    finally:
                        os.close(child)
                    if counts["entries"] > maximum_entries:
                        return False
                elif stat.S_ISREG(info.st_mode):
                    counts["files"] += 1
                    counts["logical_bytes"] += info.st_size
                    if info.st_nlink != 1:
                        findings["multiple_hard_links"] += 1
                    if info.st_mode & 0o022:
                        findings["shared_writable_file"] += 1
                    if info.st_mode & 0o6000:
                        findings["special_permission_bits"] += 1
                else:
                    findings["special_file"] += 1
                    finished = False
        if signature(os.fstat(fd)) != signature(before):
            findings["inventory_changed"] += 1
            finished = False
        return finished

    try:
        root = open_dir(os.fspath(data_root))
        try:
            info = os.fstat(root)
            if stat.S_IMODE(info.st_mode) != 0o700:
                findings["root_not_private"] += 1
            if expected_root_uid is not None and info.st_uid != expected_root_uid:
                findings["root_owner_mismatch"] += 1
            space = os.fstatvfs(root)
            capacity = {"total_bytes": space.f_blocks * space.f_frsize,
                        "available_bytes": max(0, space.f_bavail) * space.f_frsize,
                        "available_inodes": max(0, space.f_favail),
                        "minimum_free_bytes": minimum_free_bytes}
            if capacity["available_bytes"] < minimum_free_bytes:
                findings["insufficient_free_space"] += 1
            if space.f_files and space.f_favail == 0:
                findings["no_available_inodes"] += 1
            counts["directories"] = 1
            complete = scan(root, info.st_dev, 0)
        finally:
            os.close(root)
    except (OSError, Refused, NotImplementedError):
        findings["storage_inventory_unavailable"] += 1
    if maximum_tree_bytes is not None and counts["logical_bytes"] > maximum_tree_bytes:
        findings["configured_storage_budget_exceeded"] += 1
    return {"schema": SCHEMA, "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "read_only": True, "consistent_snapshot": False, "backup_verified": False,
            "status": "ready" if complete and not findings else "needs_attention",
            "inspection_complete": complete, "counts": counts, "capacity": capacity,
            "maximum_tree_bytes": maximum_tree_bytes,
            "findings": [{"code": code, "count": findings[code]} for code in sorted(findings)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--minimum-free-bytes", required=True, type=int)
    parser.add_argument("--maximum-tree-bytes", type=int)
    parser.add_argument("--maximum-entries", type=int, default=200000)
    parser.add_argument("--expected-root-uid", type=int)
    args = parser.parse_args()
    try:
        receipt = inspect_storage(args.data_root, minimum_free_bytes=args.minimum_free_bytes,
                                  maximum_tree_bytes=args.maximum_tree_bytes,
                                  maximum_entries=args.maximum_entries,
                                  expected_root_uid=args.expected_root_uid)
    except ValueError:
        parser.error("probe bounds must be nonnegative; entry/tree limits must be positive")
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
