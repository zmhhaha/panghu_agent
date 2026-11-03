# PPT operational checks

Run these tools from the selected Git checkout on the Linux server. Application
builds still use `latest`; record the checkout commit, selected image digest and
source hashes alongside each result. No tool here installs a scheduled job,
connects to a database, deletes data, starts a model or changes the deployment.

## Capacity and metadata

`storage_readiness.py` is a read-only inspection of an **existing** data root.
Run under the data access identity, with the mount read-only. It checks Linux
no-follow traversal, available filesystem bytes/inodes, root privacy/owner,
enumeration failures, foreign devices, links, unusual file types and configured
logical byte/entry bounds. It prints counters and fixed finding codes; it never
prints project/file names, content, exception messages, credentials or paths.

Example command inside an operator-selected read-only server container with the
Git checkout at `/src` and the data mount at `/data`:

```bash
cd /src
python -m ppt_service.deploy.ops.storage_readiness \
  --data-root /data --expected-root-uid 10000 \
  --minimum-free-bytes 1073741824 --maximum-tree-bytes 21474836480
```

The 1 GiB headroom in this example is an explicit operator policy, not a computed
guarantee that every task will fit. Use the deployment's actual storage budget.
Filesystem free bytes can describe a shared CephFS filesystem rather than the
PPT PVC quota; the logical tree budget and Ceph/PVC health must also be checked.
Hard-linked bytes are counted per path and reported as a finding. No automatic
cleanup follows a capacity finding. Enumeration failure or a changing tree is
an incomplete inspection, never a low successful total. Exit code 0 requires a
complete traversal with no findings; exit code 1 means operator attention.

The tool does not read payload bytes or establish that all writers stopped.
`consistent_snapshot` and `backup_verified` remain false on every result.
It must not authorize a backup, restore, publication or deletion.

## Server-only regression

The new isolated regressions are in `ppt_service/tests/ops`. Run them in the
server's disposable test container, with a frozen source mount, no network or
credentials, a temporary writable `/tmp`, non-root UID and a read-only root:

```bash
python -m pytest -q ppt_service/tests/ops
```

`test_upload_boundaries.py` exercises the actual upload stream implementation at
25 MiB and 25 MiB + 1, rejected content/signatures/UTF-8, interruption/cancellation
cleanup, and the ASGI request limiter with absent or understated Content-Length.
The ASGI consumer is explicitly synthetic; this suite does not assert reverse
proxy/Ingress timeout behavior, browser progress, SSO or parser correctness.
`test_storage_readiness.py` checks probe privacy and refusal behavior on a fresh
temporary tree. The unreadable-subtree case requires a non-root test identity.

No local test result is claimed. The server runner's report, image digest and
frozen source identity are required before treating these new cases as passed.

## Backup and restore

Use the reviewed offline procedure in `../k8s/migration/README.md`; it retains
the original database/data root and restores only into a new target. Its
`snapshot.py` inventory/copy/verify commands and `db_inventory.py` capture/compare
remain the tools for full hashes and metadata. A capacity probe is insufficient.

The current schema revision is 4. A new rehearsal must verify all 15 expected
tables, including `ppt_agent_outbox`, `ppt_acceptance_streams` and
`ppt_acceptance_entries`. The historical 2026-10-23 dump/restore receipts have
12 tables and predate these records; they do not prove that current Agent jobs
and human review history restore correctly.

`tests/ops/current_backup_probe.py` adds those records to the **fixed disposable
P1 fixture only**. It accepts no URL, credential or database option. Integrate it
with the existing migration runner in this order:

1. After `pg_fixture.py seed`, before any source inventory or dump, run
   `python /src/ppt_service/tests/ops/current_backup_probe.py seed` in the same
   isolated source/socket container. It writes one mapped/acked Agent outbox and
   two pending/revoked synthetic review records. It records no human passes.
2. Execute the runner's existing full source capture, `pg_dump`, new-target
   restore, independent DB/file comparison, schema migration and runtime grants.
3. Run the helper with `verify-target` in the same isolated context. It checks
   exact retained field values, review append-only trigger rejection and runtime
   permissions on all three current tables. The forbidden writes are rolled back
   even if a trigger is missing. It must report 15 tables and schema revision 4.
4. Run `verify-source` against the original fixture and compare
   `current_records_sha256` with seed/target, then perform the runner's full
   original-source comparison and owned-resource cleanup.

These commands use only the exact isolated `ppt_p1_source_fixture` and
`ppt_p1_restore_fixture` databases via `/pgsocket`; they are not production
backup commands. Freeze the helper along with the current server/model sources.

Before an operational snapshot, stop or gate **all** upload/API, parser,
connector, acceptance/render and maintenance writers using their normal lifecycle
and record actual stop evidence. An idle job table is only a database observation.
Preserve the database dump checksum, file manifest hash, exact schema/commit and
image identity in a private backup location. After restoration compare complete
table contents/definitions and file hashes/metadata before allowing writes.
Then use only synthetic new projects to verify current API ACLs, retained
versions, Agent outbox/checkpoints, acceptance history and idempotent replay.

Automatic production restore, PVC removal and database rewriting are absent.
Remote/off-cluster copy, retention policy, encryption/key recovery and a timed
disaster-recovery rehearsal remain platform operations to configure and verify.
