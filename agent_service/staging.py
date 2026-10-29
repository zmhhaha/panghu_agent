"""Host-owned protected input sources; runtime must supply verified read-only mounts."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import stat

from .artifacts import relative_path, unique_paths
from .contracts import InlineFile, ServiceError, digest


@dataclass(frozen=True)
class StagedInputs:
    root: Path
    files: tuple[tuple[str, str, int], ...]
    owner_uid: int

    @property
    def manifest(self):
        return tuple(dict(path=path, sha256=checksum, size=size) for path, checksum, size in self.files)

    @property
    def sha256(self):
        return digest(list(self.manifest))

    def cleanup(self):
        """Only the controller owning this exact source tree can remove it."""
        info = self.root.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or os.name == 'posix' and info.st_uid != self.owner_uid):
            raise ServiceError('unsafe_input_staging', 503)
        for directory, dirs, files in os.walk(self.root, topdown=True, followlinks=False):
            current = Path(directory)
            for name in (*dirs, *files):
                candidate = current / name
                item = candidate.lstat()
                if (stat.S_ISLNK(item.st_mode) or getattr(item, 'st_file_attributes', 0) & 0x400
                        or os.name == 'posix' and item.st_uid != self.owner_uid):
                    raise ServiceError('unsafe_input_staging', 503)
                # Windows maps chmod(0444) to the DOS read-only attribute;
                # unlike POSIX directory ownership, it also blocks unlink.
                if os.name == 'nt' and stat.S_ISREG(item.st_mode):
                    candidate.chmod(0o600)
            current.chmod(0o700)
        shutil.rmtree(self.root)


@dataclass(frozen=True)
class ExecutionContext:
    inputs: StagedInputs | None = None
    checkpoint: StagedInputs | None = None
    prompt: str | None = None

    @property
    def input_root(self):
        return self.inputs.root if self.inputs else None

    @property
    def checkpoint_root(self):
        return self.checkpoint.root if self.checkpoint else None


def stage_inputs(parent, attempt_id, files, *, max_bytes, max_files,
                 max_file_bytes=None, require_root=True, subtree=None):
    """Create fresh protected sources; chmod is not a read-only mount claim."""
    import uuid
    if str(uuid.UUID(attempt_id)) != attempt_id:
        raise ServiceError('invalid_execution_identity', 422)
    if require_root and (os.name != 'posix' or os.geteuid() != 0):
        raise ServiceError('input_staging_requires_root', 503)
    if type(files) not in (tuple, list) or len(files) > max_files:
        raise ServiceError('input_size_limit', 413)
    max_file_bytes = max_bytes if max_file_bytes is None else max_file_bytes
    unique_paths(file.name for file in files if isinstance(file, InlineFile))
    if subtree not in (None, 'inputs', 'base'):
        raise ServiceError('invalid_input_staging', 422)
    decoded, total = [], 0
    for file in files:
        if not isinstance(file, InlineFile):
            raise ServiceError('invalid_inputs', 422)
        content = file.content(limit=max_file_bytes)
        total += len(content)
        if total > max_bytes:
            raise ServiceError('input_size_limit', 413)
        name = file.name
        # The protected root exposes exactly one reviewed mount subtree. Input
        # references may already carry the business-facing inputs/ prefix;
        # checkpoint names always retain their original artifact-relative path.
        if subtree == 'inputs' and name.startswith('inputs/'):
            name = name[len('inputs/'):]
        if subtree:
            name = subtree + '/' + name
        decoded.append((name, file.sha256, content))
    unique_paths(name for name, _, _ in decoded)
    parent = Path(parent).absolute()
    for part in (*reversed(parent.parents), parent):
        if part.exists():
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ServiceError('unsafe_input_staging', 503)
    parent.mkdir(parents=True, exist_ok=True, mode=0o711)
    owner = os.geteuid() if hasattr(os, 'geteuid') else 0
    info = parent.stat()
    if os.name == 'posix' and (info.st_uid != owner or info.st_mode & 0o022):
        raise ServiceError('unsafe_input_staging', 503)
    root = parent / attempt_id
    root.mkdir(mode=0o700)
    staged = StagedInputs(root, tuple((name, checksum, len(data)) for name, checksum, data in decoded), owner)
    try:
        for name, checksum, data in decoded:
            relative_path(name)
            target = root.joinpath(*name.split('/'))
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o400)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            target.chmod(0o444)
        for directory, dirs, _ in os.walk(root, topdown=False):
            Path(directory).chmod(0o555)
        return staged
    except BaseException:
        staged.cleanup()
        raise
