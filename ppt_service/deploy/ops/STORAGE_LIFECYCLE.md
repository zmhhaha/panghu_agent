# Explicit storage lifecycle operator

Run `python -m ppt_service.deploy.ops.storage_lifecycle` from committed source on
the Linux server, with the existing trusted `DATABASE_URL`. It requires current
PostgreSQL schema 4. It has no scheduled execution and defaults to a dry run.
Do not run it against real user directories as part of its acceptance tests.

`staging` considers only the exact terminal job's
`projects/<project>/attempts/<job>/<attempt>-<fence>` and
`projects/<project>/agent-results/<job>/<fence>` trees. Their newest entry must
be at least seven days old (or the explicitly supplied positive retention).
Original assets, checkpoints, versions, their Agent lineage sources and review
evidence remain protected regardless of age. Referenced Agent results are
retained. Unknown paths, old attempt identities, material-parser working trees,
global authority journals and failure-diagnostic directories are not candidates.

`deleted-project` requires the owner's existing authenticated API deletion to
have set both `deletion_requested` and `cleanup_ready`. Only then can it reclaim
that exact project's files. It preserves database rows and append-only review
history. This is file reclamation after access revocation, not a complete
database/audit-history erasure procedure.

Both modes refuse active project jobs/slots, any unexpired job lease, unproven
job stops, queued/parsing/stopping assets, outstanding parse leases and unacked
or unmapped Agent outboxes. Outbox attempt/fence and job identity must match.
For every asset they also require its original Kubernetes parser authority,
bound scope/token, exact Pod UID and positive container termination receipt,
completed original cleanup and fresh, complete empty Pod lists under both
supported scope selectors. A ready status or missing Pod alone is insufficient.
Missing legacy scope identity, an unavailable API or residual scope refuses
reclamation; the operator never repairs a journal or stops a running task.

All tree access uses directory descriptors and `O_NOFOLLOW`. Links, hard links,
special files, nested devices, xattrs/ACLs and changed contents/metadata/physical
identities refuse the operation. The plan contains private paths and hashes;
store it in an existing private directory (0700) outside `DATA_ROOT`.

```bash
python -m ppt_service.deploy.ops.storage_lifecycle \
  --data-root "$DATA_ROOT" --project-id <project-uuid> --mode staging \
  --output /root/ppt-lifecycle-review/plan.json
```

Inspect that plan and retain the output's `sha256`. To execute that exact plan,
explicitly provide both its path and retained file digest, using a new output:

```bash
python -m ppt_service.deploy.ops.storage_lifecycle \
  --data-root "$DATA_ROOT" --project-id <project-uuid> --mode staging \
  --execute-plan /root/ppt-lifecycle-review/plan.json --plan-sha256 <file-sha256> \
  --output /root/ppt-lifecycle-review/result.json
```

Execution locks the PostgreSQL admission, slot, job, project and asset rows and
recomputes the reviewed plan before its first unlink. Staging additionally
verifies all protected trees after reclamation. A filesystem interruption can
leave partial cleanup; inspect the private evidence and create a new plan. Do
not broaden a failed command, edit authority data or force-delete a remainder.

The server-only acceptance runner is
`ppt_service/tests/ops/run_storage_lifecycle_linux.py`. It requires cached
immutable test/PostgreSQL images and its own frozen Git checkout. It creates
new labelled network-none containers, a private Unix socket and one fresh
database per test, installs the current schema, and checks the filesystem and
database gates. Synthetic source/old-version bytes in those tests are not a
native PPTX, real Pi model execution or a production project deletion. Actual
test outcome is recorded only by the generated private `receipt.json` and
JUnit file. No production file reclamation has been performed by adding this
operator or its tests.
# Server evidence, 2026-11-05

Git source `4acd98aaf1e8d9e2ae794fa9b4f9fac74352a8e8` passed the isolated
current-schema4 PostgreSQL suite: **25 tests, 0 failures, 0 errors, 0 skipped**,
9.85 seconds in pytest. The operator parsed the retained JUnit and returned
`passed=true`, `owned_resources_removed=true`, `cleanup_errors=[]`.

Private server evidence (not published business material):

- `/root/ppt-lifecycle-acceptance-4acd98a-20261105-r2/receipt.json`
- `/root/ppt-lifecycle-acceptance-4acd98a-20261105-r2/lifecycle.xml`
- `/root/ppt-lifecycle-acceptance-4acd98a-20261105-r2/pytest.stdout`

Each case created/dropped only its random `ppt_lifecycle_<uuid>` database in a
new PostgreSQL container and used synthetic temporary file trees. The suite
exercised terminal scratch reclamation, explicit owner-soft-deleted project
reclamation, preservation of old versions/checkpoints/Agent-source lineage,
lease/outbox/parser stop refusals, positive original parser-journal consumption,
and revision/hash/path-identity drift and link denial. The parser journal case
uses new synthetic IDs and an explicitly substituted absence observer; it is
not live Kubernetes stop proof. API-owner soft deletion is exercised through
the repository contract, not an external authenticated browser.

Both containers ran with `network=none`. The test UID was 10000 and PostgreSQL
UID 70. Only an owner-labelled temporary socket volume and private result bind
were shared; PostgreSQL data used bounded tmpfs, and declared image volumes
were explicitly covered. Cleanup checked owner identity and mount inventory,
stop/wait/PID0, exact container-ID/volume absence and a healthy daemon. No
production DB/PVC/project, real parser/Agent, model or supplier was accessed;
no production files were reclaimed.

The earlier run at Git `f4d5408`, evidence
`/root/ppt-lifecycle-acceptance-f4d5408-20261104-r1/`, truthfully remains failed:
pytest passed 25 cases, but the operator attempted to copy JUnit from an already
unmounted container tmpfs. Its resources were removed. Commit `1796c13` moved
JUnit to a private bind directory; only the fresh r2 receipt supplies complete
passing evidence. This suite does not close the separate R6 capacity-exhaustion
and admission-pause matrix, nor does it establish complete R5 fault recovery.
