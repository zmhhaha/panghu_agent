# Remaining production acceptance — 2026-11-04

The parent OpenSpec change remains **145/150**. R1, R3, R4, R5 and R6 are
open. This record adds evidence to those gates; it does not replace the parent
requirements or authorize publication of another Version.

## Current production backup and independent restore

The committed operator at `1529f1a487b1e7f57f47394c4bb8edb9bb9b63a9`
was pushed, fetched with Git on the server and executed there. No local
application tests or source archive deployment were used.

Before capture, the operator proved schema 4 idle: no active jobs/projects,
parsers, slots, unacknowledged outboxes or attached execution without stop
proof. It acquired its own deployment lock and used UID/spec/resourceVersion
checks to pause only the PPT site and controller. Shared PostgreSQL, other
services and source business rows were not changed. A network-denied,
non-root, read-only backup Pod read the existing PVC.

The successful receipts are private on the manager:

- `/root/ppt-production-backup-20261104-r3/receipt.json`
- `/root/ppt-production-restore-20261104-r3/receipt.json`
- `/root/ppt-backup-safety-1529f1a-20261104-r4/receipt.json`

Capture and restore both report `passed=true`:

- 15 current tables, including schema, data, roles/ACLs and append-only review
  history. Independent restore used PostgreSQL 16.15; the unchanged source is
  16.14. Runtime privilege checks passed.
- 495,406,352 file bytes and 26,054 restorable entries. File hashes, modes,
  UID/GID and recorded link metadata matched the independent copy. Two stopped
  Unix socket nodes are recorded as metadata only, not recreated.
- Archive: 539,627,520 bytes, SHA-256
  `7641174bb678a66c0b603580a7ee260ce7d2c3d4b7e6e8302a298ab39b602b21`.
- PVC: 20 GiB, 20,979,908,608 bytes available at capture. CephFS reports free
  inodes as `-1`; the receipt preserves this as unknown rather than claiming
  measured inode headroom.
- The exact root-only `user.ceph.subvolume.earmark` storage provisioner marker
  was recorded and checked unchanged before/after capture. It is not transplanted
  into the restored filesystem. Other xattrs, including this name on children,
  still fail closed rather than silently losing ACL/capability/application data.
- The original site/controller specs were restored, both replicas returned to
  1/1 ready and their image digests stayed unchanged. The owned lock, Pod and
  NetworkPolicy were removed.
- Restore used a fresh private directory and an independent `network=none`
  PostgreSQL running as UID/GID 70. No production files or database were replaced.
  Cleanup verified the exact container ID, successful stop/wait, PID 0 and
  absence by both ID and reserved name. No database volume was left behind.

The backup safety suite passed **61/61**, no skips, in a non-root,
read-only, no-network server container. The xattr cases used a new private
host-filesystem test directory: this server's tmpfs does not support user xattrs.
The earlier 3 failing tmpfs cases remain in the r3 safety receipt, not rewritten.

Earlier capture failures are also retained: r1 rejected Ceph's unknown inode
accounting; r2 rejected its root storage marker. Both restored the original
writer specs and released their lock. The r1 safety wrapper's cleanup-format
failure is retained separately from its 56 passing assertions; Docker 29's
exact lowercase `error: no such object: <identity>` format is now recognized
with a healthy-daemon check. Permission and connection errors still fail closed.

These are a current production consistency backup and same-host independent
restore rehearsal. Scheduled/off-site disaster recovery, actual capacity
exhaustion admission and the full storage lifecycle matrix remain separate R6
work.

## Complex public-data sample

`NASA公开资料复杂稿验收-20261104` is a disposable, separate owner project
created through the normal website. It uses frozen public GISTEMP values for
2019–2024, Chinese explanatory text and an explicitly self-drawn PNG. Its
six-page brief requires a native editable table, a column chart with an embedded
workbook, faithful units/reference period, and one ordinary website revision.

The initial generation job `21ee2edb-17ca-49ce-b5a4-d2e878200d3f` failed its
independent quality gate and produced no Version. Its materials and selected
plan are retained. R1 is open; the earlier successful ten-page DSH presentation
does not prove image/chart acceptance for this sample.

During that one authorized sample execution, an identity-bound network probe
observed model-proxy reachability while Agent API, database, Vault, Kubernetes
API, direct upstream model and node SSH connections were refused with healthy
positive controls. Metadata refusal had no healthy positive control and is only
an observation. This adds a real capsule measurement to R4; it does not prove
the remaining admission and isolation matrix.

## Authorization boundaries

The two public malformed-callback cases still fail the original HTTP400/403
expectation with HTTP500. Upstream oauth2-proxy v7.8.0 `OAuthCallback` explicitly
maps `decodeState` errors to HTTP500 (oauthproxy.go lines 875–879); `decodeState`
requires a `nonce:redirect` pair (lines 1254–1265). This explains the two measured
inputs; it does not turn their receipts into passes. The missing/invalid CSRF
cookie branch for a syntactically valid pair returns HTTP403 (lines 884–892).
The public operator now measures those two branches separately without using
any cookies, following redirects or redeeming a real authorization code. The
upstream source/hash is retained in the private server directory
`/root/ppt-public-callback-code-review-20261104-r1`.

The DSH Version `6f7c220f-01a6-4ab0-8703-180f091658d9` was published only
after the owner's separate explicit visual and publication confirmations.
No other version inherits those declarations. Usage/fees/eligibility work
remains deferred. Raw backup, source files and private database fingerprints
stay on the server; this document records aggregate evidence only.
