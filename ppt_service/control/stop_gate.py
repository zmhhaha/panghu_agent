"""Root controller stop gate for dedicated attempt UIDs.

This module only stops and proves. It never launches, allocates or releases a
UID, and it is not a production readiness switch for the Pi profile.
"""
from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

from .execution import Attempt, ControlError, StopProof

UID_MIN = 10000
UID_MAX = 2**31 - 1
RESERVED_UID = 65534
SCAN_INTERVAL = 0.02
MAX_RESOURCE_ID = 200


def _bounded_grace(grace):
    if type(grace) is bool or not isinstance(grace, (int, float)) or not 0 < grace <= 60:
        raise ControlError('invalid_stop_grace')
    return float(grace)


def _dedicated_uids(uids):
    if type(uids) is not tuple:
        try:
            uids = tuple(uids)
        except TypeError:
            raise ControlError('invalid_attempt_uid') from None
    if not uids:
        raise ControlError('invalid_attempt_uid')
    for uid in uids:
        if type(uid) is not int or not UID_MIN <= uid < UID_MAX or uid == RESERVED_UID:
            raise ControlError('invalid_attempt_uid')
    if len(set(uids)) != len(uids):
        raise ControlError('duplicate_attempt_uid')
    return uids


def uid_processes(uid):
    """Live (non-zombie) process ids whose real, effective or saved UID matches."""
    if type(uid) is not int or not UID_MIN <= uid < UID_MAX or uid == RESERVED_UID:
        raise ControlError('invalid_attempt_uid')
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ControlError('process_safety_unverified')
    matches = []
    for path in Path('/proc').iterdir():
        if not path.name.isdecimal():
            continue
        try:
            fields = dict(line.split(':', 1) for line in (path / 'status').read_text().splitlines() if ':' in line)
        except FileNotFoundError:
            continue  # Process reaped during the scan.
        except OSError as exc:
            raise ControlError('process_safety_unverified') from exc
        try:
            ids = [int(value) for value in fields['Uid'].split()]
            state = fields['State'].strip().split()[0]
        except (KeyError, ValueError, IndexError) as exc:
            raise ControlError('process_safety_unverified') from exc
        if uid in ids and state not in ('Z', 'X'):
            matches.append(int(path.name))
    return matches


def _kill(pid, uid):
    if not callable(getattr(os, 'pidfd_open', None)) or not callable(getattr(signal, 'pidfd_send_signal', None)):
        raise ControlError('process_safety_unverified')
    try:
        descriptor = os.pidfd_open(pid)
    except ProcessLookupError:
        return
    try:
        # Re-check ownership after opening: the pid may already have been reused.
        if pid in uid_processes(uid):
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            except ProcessLookupError:
                pass
    finally:
        os.close(descriptor)


def stop_uids(uids, *, grace=5.0):
    """SIGKILL every process of each dedicated UID until two scans are empty.

    Returns bounded resource identifiers for the caller's stop proof. Raises
    without a proof if any UID still has a live process before the deadline.
    """
    uids = _dedicated_uids(uids)
    deadline = time.monotonic() + _bounded_grace(grace)
    pending = list(uids)
    empty = {uid: 0 for uid in uids}
    while pending and time.monotonic() < deadline:
        for uid in tuple(pending):
            pids = uid_processes(uid)
            if pids:
                empty[uid] = 0
                for pid in pids:
                    _kill(pid, uid)
            else:
                empty[uid] += 1
                if empty[uid] >= 2:
                    pending.remove(uid)
        if pending:
            time.sleep(SCAN_INTERVAL)
    if pending:
        raise ControlError('writers_not_stopped')
    return tuple(f'uid:{uid}' for uid in uids)


def combined_stop_proof(attempt, uids, *, grace=5.0, resource_ids=()):
    """Stop every writer UID of one attempt and return its combined proof.

    `uids` must list every dedicated UID reserved for the attempt, including a
    separate preview/helper identity. The caller is responsible for having
    verified each UID against the durable ledger and for having stopped any
    container/cgroup resources it owns; this function only proves the UIDs have
    no live process, so it must not be treated as a publication certificate.
    """
    if type(attempt) is not Attempt or type(attempt.uid) is not str or not attempt.uid:
        raise ControlError('invalid_attempt')
    if type(resource_ids) is not tuple:
        try:
            resource_ids = tuple(resource_ids)
        except TypeError:
            raise ControlError('invalid_resource_ids') from None
    for value in resource_ids:
        if type(value) is not str or not value or len(value) > MAX_RESOURCE_ID:
            raise ControlError('invalid_resource_ids')
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ControlError('invalid_resource_ids')
    stopped = stop_uids(uids, grace=grace)
    return StopProof(attempt.uid, True, resource_ids + stopped)
