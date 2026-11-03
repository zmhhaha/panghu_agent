# R2 / R5 / R6 evidence map — 2026-11-03

This inventory distinguishes committed test cases from actual server runs. It
does not mark new cases passed, invent production evidence, or substitute
synthetic process proofs for observed termination.

| Gate | Existing code/evidence | Remaining current deployment evidence |
| --- | --- | --- |
| R2 file/transport bounds | `tests/server/test_api.py::test_transport_bounds_and_consent`; `tests/server/test_asset_removal.py` covers the 10-file cap, replacement after removal, retained original bytes and global storage accounting. `tests/control/test_agent_connector.py::test_connector_uploads_supported_25_mib_source` covers the connector's admitted source size. New `tests/ops/test_upload_boundaries.py` tests exact 25 MiB/+1, malformed/empty content, interrupted/cancelled streams and chunked/underdeclared transport. | Execute the new cases in the isolated server runner. Exercise the actual ingress/browser at the 25 MiB boundary and loss of upload connection, verify specific UI error/progress and absence of residual live asset rows. Parser limits, timeout/OOM and malformed Office/PDF are separate sandbox tests. |
| R5 durable HTTP/Agent recovery | `tests/control/test_agent_connector.py` contains lost reply/unreached POST, immutable deadline, cancel while submission uncertain, remote stop mismatch, artifact write interruption and safe model failure tests. `tests/control/test_materials.py` covers parser restart with stop proof and stale token rejection. `tests/e2e/test_recovery.py` has 15 synthetic worker/lease/checkpoint cases; `tests/e2e/results.md` records the historical Linux PG run. | Re-run current code with the Agent connector cases. A disposable runtime rehearsal must stop/restart a real controller, interrupt only its own synthetic service connection, and prove one remote execution, current fencing/stop identity, no extra model request and preserved user versions. Actual OOM, abrupt host loss, network partition and incomplete OS stop are not covered by the old FakeWorker evidence. |
| R6 capacity and offline backup | New `deploy/ops/storage_readiness.py` reports no-follow traversal/capacity findings without reading names/content into the receipt. `deploy/k8s/migration/{snapshot.py,db_inventory.py}` provides offline hash/metadata capture/copy/verification and DB fingerprints. New `tests/ops/current_backup_probe.py` adds non-passing synthetic review history and a mapped Agent outbox to the exact disposable rehearsal, then verifies restoration, triggers and runtime grants. Historical `migration-validation/final-05/status.json` and `tests/e2e/evidence/20261023-fixes/pg-backup/verification.json` record real isolated `pg_dump`/`pg_restore`. | Execute the new probe with a fresh current-schema dump/restore: 15 tables and retained review/Agent records, with source unchanged; do not reuse the old 12-table receipt. Production offline consistency, private off-cluster backup, retention and recoverability of keys remain explicit platform deployment gates. A live capacity scan is not a backup. |

## Isolation entry points

- New R2/probe cases: `python -m pytest -q ppt_service/tests/ops` inside the
  server's frozen-source, non-root, network-none test container. No DB or model.
- Current R5 cases: select the non-usage tests in
  `tests/control/test_agent_connector.py` named `uncertain_submission`,
  `unreached_post`, `cancel_during_uncertain`, `cancel_after_lost_reply`,
  `terminal_remote_without_matching_stop`, `interrupted_artifact_write`,
  `remote_failure`, and `model_failure_retains`. Their service object is a
  substitute; the runner must label it as contract evidence.
- Legacy combined integration: `tests/e2e/run_isolated_linux.sh`. It needs a
  newly frozen `source.tar`, a new case directory and the reviewed already-local
  test image digests. It creates dedicated PostgreSQL databases over a private
  Unix socket with TCP disabled, no network, no production credentials and no
  application Docker socket. It records each phase and owned-resource cleanup.
  It is a historical fixture harness, not the current Agent's live runtime.
- Offline backup rehearsal: `deploy/k8s/migration/tests/run_isolated_linux.sh`.
  It accepts only its fixed disposable source/restore database names and fresh
  paths, uses real dump/restore and hashes, and preserves the old source. The
  existing historical image selections must be reviewed for current schema and
  dependencies before rerun; do not silently call the historical result current.

Keep results grouped by source commit/hash and image digest. Failed, skipped and
not-run gates retain their actual states. Existing usage/accounting fields in
the source schema are preserved by backups; this work adds no usage collection,
eligibility rule or business quota behavior.
