#!/usr/bin/env python3
"""Offline Linux data-root inventory/copy/verify. No database or process control.

All paths are opened using directory FDs and O_NOFOLLOW. A stable scan does not
prove that writers stopped; the operator must establish that separately.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import uuid

SCHEMA = "ppt-service.p1.files.v1"
FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


class Refused(RuntimeError):
    pass


def require_linux():
    if not sys.platform.startswith("linux") or not hasattr(os, "O_NOFOLLOW"):
        raise Refused("Linux O_NOFOLLOW/dir_fd and renameat2 are required")


def path_parts(path):
    value = os.fspath(path)
    if not os.path.isabs(value) or "\x00" in value:
        raise Refused("an absolute path is required")
    parts = value.split("/")[1:]
    if not parts or any(p in ("", ".", "..") for p in parts):
        raise Refused("empty/dot/parent path components are forbidden")
    return parts


def open_dir(path):
    require_linux()
    parts = path_parts(path)
    fd = os.open("/", FLAGS | os.O_DIRECTORY)
    try:
        for part in parts:
            child = os.open(part, FLAGS | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def open_parent(path):
    parts = path_parts(path)
    parent = "/" + "/".join(parts[:-1]) if len(parts) > 1 else "/"
    return (os.open("/", FLAGS | os.O_DIRECTORY) if parent == "/" else open_dir(parent)), parts[-1]


def signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def checked(info, device, kind):
    if info.st_dev != device:
        raise Refused("nested mounts/devices are forbidden; inventory them separately")
    if info.st_mode & 0o7000:
        raise Refused("special permission bits require a separate reviewed migration")
    if kind == "file" and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise Refused("only ordinary files with exactly one hard link are allowed")
    if kind == "directory" and not stat.S_ISDIR(info.st_mode):
        raise Refused("only ordinary directories are allowed")


def no_xattrs(fd):
    # ACLs, capabilities and security labels are not silently dropped.
    if os.listxattr(fd):
        raise Refused("extended attributes/ACLs require a filesystem-native migration")


def row(info, kind):
    return {"kind": kind, "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid,
            "gid": info.st_gid, "mtime_ns": info.st_mtime_ns}


def file_scan(parent, name, device, consume=None):
    before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    checked(before, device, "file")
    fd = os.open(name, FLAGS | os.O_NONBLOCK, dir_fd=parent)
    try:
        info = os.fstat(fd)
        checked(info, device, "file")
        if signature(before) != signature(info):
            raise Refused("file changed before open")
        no_xattrs(fd)
        digest = hashlib.sha256()
        total = 0
        while chunk := os.read(fd, 1024 * 1024):
            digest.update(chunk)
            total += len(chunk)
            if total > info.st_size:
                raise Refused("source file grew during read")
            if consume is not None:
                consume(chunk)
        after = os.fstat(fd)
        final = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if signature(before) != signature(after) or signature(after) != signature(final) or total != info.st_size:
            raise Refused("source file drift detected")
        return row(info, "file") | {"bytes": total, "sha256": digest.hexdigest()}
    finally:
        os.close(fd)


def inventory(root):
    rootfd = open_dir(root)
    entries = {}
    try:
        device = os.fstat(rootfd).st_dev

        def walk(fd, key):
            before = os.fstat(fd)
            checked(before, device, "directory")
            no_xattrs(fd)
            entries[key] = row(before, "directory")
            names = sorted(os.listdir(fd))
            for name in names:
                if name in ("", ".", "..") or "/" in name or "\\" in name or ":" in name:
                    raise Refused("unsafe portable filename")
                relative = name if key == "." else key + "/" + name
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, FLAGS | os.O_DIRECTORY, dir_fd=fd)
                    try:
                        if signature(info) != signature(os.fstat(child)):
                            raise Refused("directory changed before open")
                        walk(child, relative)
                        if signature(info) != signature(os.stat(name, dir_fd=fd, follow_symlinks=False)):
                            raise Refused("source directory replacement detected")
                    finally:
                        os.close(child)
                else:
                    entries[relative] = file_scan(fd, name, device)
            if names != sorted(os.listdir(fd)) or signature(before) != signature(os.fstat(fd)):
                raise Refused("source directory drift detected")

        walk(rootfd, ".")
    finally:
        os.close(rootfd)
    return dict(sorted(entries.items()))


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def atomic_json(path, value):
    """Publish a fresh regular file atomically and never replace an existing name."""
    parent, name = open_parent(path)
    temp = ".ppt-manifest-" + uuid.uuid4().hex
    raw = encoded(value)
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
        os.link(temp, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        os.unlink(temp, dir_fd=parent)
        os.fsync(parent)
    except BaseException:
        try:
            os.unlink(temp, dir_fd=parent)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(parent)
    return hashlib.sha256(raw).hexdigest()


def read_json(path, expected_sha256):
    if (not isinstance(expected_sha256, str) or len(expected_sha256) != 64
            or any(c not in "0123456789abcdefABCDEF" for c in expected_sha256)):
        raise Refused("supply the independently retained 64-character manifest SHA-256")
    parent, name = open_parent(path)
    try:
        chunks = []
        file_scan(parent, name, os.fstat(parent).st_dev, chunks.append)
        raw = b"".join(chunks)
    finally:
        os.close(parent)
    if hashlib.sha256(raw).hexdigest() != expected_sha256.lower():
        raise Refused("manifest checksum differs from the retained digest")
    return json.loads(raw)


def validate_manifest(manifest):
    if manifest.get("schema") != SCHEMA or set(manifest) != {"schema", "entries"}:
        raise Refused("unsupported file manifest")
    entries = manifest["entries"]
    if type(entries) is not dict or "." not in entries:
        raise Refused("missing data-root directory")
    for key, value in entries.items():
        if key != "." and (not key or any(p in ("", ".", "..") for p in key.split("/"))
                           or "\\" in key or ":" in key or "\x00" in key):
            raise Refused("unsafe manifest path")
        if type(value) is not dict or value.get("kind") not in ("directory", "file"):
            raise Refused("invalid manifest entry")
        kind = value["kind"]
        keys = {"kind", "mode", "uid", "gid", "mtime_ns"} | ({"bytes", "sha256"} if kind == "file" else set())
        if set(value) != keys or any(type(value[k]) is not int or value[k] < 0 for k in keys - {"kind", "sha256"}):
            raise Refused("invalid file metadata")
        if value["mode"] > 0o777:
            raise Refused("special permission bits are forbidden")
        if kind == "file" and (type(value["sha256"]) is not str or len(value["sha256"]) != 64
                               or any(c not in "0123456789abcdef" for c in value["sha256"])):
            raise Refused("invalid file digest")
        if key != ".":
            parent = key.rsplit("/", 1)[0] if "/" in key else "."
            if entries.get(parent, {}).get("kind") != "directory":
                raise Refused("manifest omits a parent directory")
    if entries["."].get("kind") != "directory":
        raise Refused("data root is not a directory")
    return entries


def expected_entries(entries, ownership, uid=None, gid=None):
    if ownership not in ("preserve", "map"):
        raise Refused("an explicit ownership policy is required")
    if ownership == "map":
        if type(uid) is not int or type(gid) is not int or min(uid, gid) < 0:
            raise Refused("map ownership needs explicit nonnegative UID and GID")
        return {k: v | {"uid": uid, "gid": gid} for k, v in entries.items()}
    if uid is not None or gid is not None:
        raise Refused("UID/GID are only valid with ownership=map")
    return entries


def verify(root, manifest, ownership, uid=None, gid=None):
    entries = validate_manifest(manifest)
    actual = inventory(root)
    if actual != expected_entries(entries, ownership, uid, gid):
        raise Refused("tree differs: content, path set, type, mode, nanosecond mtime or ownership")
    return {"verified": True, "entries": len(actual), "ownership": ownership,
            "files": sum(v["kind"] == "file" for v in actual.values())}


def set_metadata(fd, entry):
    info = os.fstat(fd)
    if (info.st_uid, info.st_gid) != (entry["uid"], entry["gid"]):
        os.fchown(fd, entry["uid"], entry["gid"])
    os.fchmod(fd, entry["mode"])
    os.utime(fd, ns=(entry["mtime_ns"], entry["mtime_ns"]))
    os.fsync(fd)


def rename_fresh(parent, before, after):
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise Refused("atomic no-replace renameat2 is unavailable")
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    if renameat2(parent, os.fsencode(before), parent, os.fsencode(after), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    os.fsync(parent)


def copy_tree(source, destination, manifest, ownership, uid=None, gid=None):
    require_linux()
    entries = validate_manifest(manifest)
    expected = expected_entries(entries, ownership, uid, gid)
    source, destination = os.fspath(source), os.fspath(destination)
    path_parts(source)
    path_parts(destination)
    if source == destination or destination.startswith(source.rstrip("/") + "/"):
        raise Refused("destination must be outside the source tree")
    # Nothing is written until the complete source manifest has matched.
    verify(source, manifest, "preserve")
    parent, name = open_parent(destination)
    stage = ".ppt-migration-incomplete-" + uuid.uuid4().hex
    try:
        try:
            os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise Refused("destination must not exist, including an empty directory")
        os.mkdir(stage, mode=0o700, dir_fd=parent)
        staging_path = str(Path(destination).parent / stage)
        source_fd = open_dir(source)
        try:
            target_fd = os.open(stage, FLAGS | os.O_DIRECTORY, dir_fd=parent)
        except BaseException:
            os.close(source_fd)
            raise
        try:
            device = os.fstat(source_fd).st_dev

            def copy_directory(src, dst, key):
                for name_ in sorted(os.listdir(src)):
                    key_ = name_ if key == "." else key + "/" + name_
                    item = entries.get(key_)
                    if item is None:
                        raise Refused("source gained an entry during copy")
                    if item["kind"] == "directory":
                        os.mkdir(name_, mode=0o700, dir_fd=dst)
                        child_src = os.open(name_, FLAGS | os.O_DIRECTORY, dir_fd=src)
                        child_dst = os.open(name_, FLAGS | os.O_DIRECTORY, dir_fd=dst)
                        try:
                            copy_directory(child_src, child_dst, key_)
                        finally:
                            os.close(child_src)
                            os.close(child_dst)
                    else:
                        output = os.open(name_, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                         0o600, dir_fd=dst)
                        try:
                            def write_all(chunk):
                                view = memoryview(chunk)
                                while view:
                                    written = os.write(output, view)
                                    if written <= 0:
                                        raise OSError("short file write")
                                    view = view[written:]
                            if file_scan(src, name_, device, write_all) != item:
                                raise Refused("source changed during copy")
                            set_metadata(output, expected[key_])
                        finally:
                            os.close(output)
                set_metadata(dst, expected[key])

            copy_directory(source_fd, target_fd, ".")
        finally:
            os.close(source_fd)
            os.close(target_fd)
        verify(source, manifest, "preserve")
        verify(staging_path, manifest, ownership, uid, gid)
        rename_fresh(parent, stage, name)
        return verify(destination, manifest, ownership, uid, gid) | {"destination": destination}
    except BaseException:
        # Preserve the fresh, unpublished staging tree for review. Never recursively
        # delete user data or retry over this directory.
        raise
    finally:
        os.close(parent)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    create = actions.add_parser("inventory")
    create.add_argument("--root", required=True)
    create.add_argument("--manifest", required=True)
    for action in ("copy", "verify"):
        child = actions.add_parser(action)
        child.add_argument("--manifest", required=True)
        child.add_argument("--manifest-sha256", required=True)
        child.add_argument("--ownership", required=True, choices=("preserve", "map"))
        child.add_argument("--uid", type=int)
        child.add_argument("--gid", type=int)
        if action == "copy":
            child.add_argument("--source", required=True)
            child.add_argument("--destination", required=True)
        else:
            child.add_argument("--root", required=True)
    args = parser.parse_args()
    try:
        if args.action == "inventory":
            root, output = args.root, args.manifest
            path_parts(root)
            path_parts(output)
            if output == root or output.startswith(root.rstrip("/") + "/"):
                raise Refused("manifest must be outside the source data tree")
            manifest = {"schema": SCHEMA, "entries": inventory(root)}
            result = {"manifest_sha256": atomic_json(output, manifest),
                      "entries": len(manifest["entries"]), "writers_stopped_proven": False}
        else:
            manifest = read_json(args.manifest, args.manifest_sha256)
            if args.action == "copy":
                result = copy_tree(args.source, args.destination, manifest, args.ownership, args.uid, args.gid)
            else:
                result = verify(args.root, manifest, args.ownership, args.uid, args.gid)
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, Refused) as error:
        print("migration refused: " + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
