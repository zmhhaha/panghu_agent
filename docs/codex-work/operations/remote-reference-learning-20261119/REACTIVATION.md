> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/remote-reference-learning-20261119/REACTIVATION.md`；原文件 SHA-256：`a70605a175ff680ed9eb99fe1d0b2f51e6fd9480031f06617ea3494020eef251`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../README.md)

# Existing caller re-admission for remote-reference learning

These operation files were prepared locally and uploaded to
`/root/video-releases/remote-reference-20261119/server-helpers`. Server read-only
UID/Agent/Pod/runtime inspection and a proxy-restart preview were performed.
After the parent explicitly authorized it and all technical candidates stopped,
one necessary same-image shared-proxy restart was completed. Its actual exit
and final quiet receipt is `server-helpers/proxy-drain-r1/receipt.json`, with a
local copy under `operator-evidence/proxy-drain-receipt.json`. Public ConfigMap,
runtime Secret, all caller identities and other five service specs were
preserved. No model task, UID reservation or route activation was performed by
this subtask.

## Why the 2026-11-18 scripts cannot be used unchanged

- `prepare_*_learning_catalog` is deliberately additive. With an existing profile
  route, a new image produces a conflicting route identity. A new disabled
  candidate view must replace only the target family's route; the actual live
  before-state remains frozen separately.
- Old `*_activate.py` requires the caller token not to exist, then creates it.
  Existing callers already have tokens. That branch must never run again.
- New Pi digest means all four role combinations must be re-admitted. Old role
  acceptance, old renderer capture or an old host cannot certify the new image.

## Files to upload together to a new server operation directory

Upload everything under `server-helpers/` without changing filenames. Both
candidate preparers and runners expect their parent directory to contain the
shared helper. The preparer and helper SHA are frozen into each candidate's
`release-inputs.json`; do not edit after candidate preparation.

`adapt_existing_operators.py` records the old and new preparer hashes in
`operator-adaptation.json`; copied runners retain the exact original bytes.
Existing caller scopes and each role's current model policy are preserved.
The family's own current author pool supplies confinement by default.

## Sequence after the four clean `latest` image builds

1. Verify clean source revision and all four image labels/digests. The Agent Pi
   and control images use the same source commit. Executor and animation-lab are
   the other two images; changing them does not excuse new Pi admissions.
2. Prepare fresh picture-author and reference-author scenarios with real frozen
   frame evidence and the current observation contract. Existing upload fixture
   inputs need the new PTS/crop/source evidence fields where applicable. Never
   label provider dimensions as observed dimensions. The source cannot be
   fetched by Agent/Pi workers; controlled executor/network policy does that.
3. Run each original one-shot candidate runner explicitly, with unique fresh
   three-UID candidate reservations. These runners issue model work only when
   deliberately executed. Capture their original root-owned output.
4. Actually render the authors' stopped source in the newly built renderer /
   executor context and retain unmodified captures. Picture author requires a
   decoded PNG capture; video author requires the continuous clip plus frame
   evidence. Prepare reviewer inputs from those real output bytes, then run
   both fresh reviewer candidates.
5. Run the current source's independent verifiers (one per role), for example:

   ```text
   python -B -m agent_service.deploy.picture_learning_acceptance \
     --candidate-dir <new-picture-author> --source <clean-source> \
     --renderer-dir <actual-new-picture-capture> \
     --renderer-image <renderer:latest@sha256:...> --output <new-picture-author-admission>

   python -B -m agent_service.deploy.reference_learning_acceptance \
     --candidate-dir <new-reference-author> --source <clean-source> \
     --renderer-dir <actual-new-video-capture> \
     --renderer-image <renderer:latest@sha256:...> --output <new-reference-author-admission>
   ```

   For review roles omit renderer-dir/image; retain the actual reference and
   output image delivery evidence. No old image acceptance is copied.
6. Two newer shared-proxy `admitted` rows were observed after the existing
   classifier's twelve historical records. Both attempts are terminal and
   retired, with absent worker Pods, but the current proxy process predates
   them. Do not classify them as safe until that process actually exits.
   `restart_uncertain_proxy.py` implements the necessary one-shot same-image
   proxy restart, preserving all ConfigMaps, Secrets and other service specs.
   Its default is a read-only preview. Wait for all candidate probes to stop
   before explicitly executing it with a fresh output directory:

   ```text
   python3 -B /root/video-releases/remote-reference-20261119/server-helpers/restart_uncertain_proxy.py \
     --plan-dir /root/video-releases/remote-reference-20261119/server-helpers/readiness-fdb2c6248b \
     --output /root/video-releases/remote-reference-20261119/server-helpers/proxy-drain-r1 \
     --execute
   ```

   The actual shared proxy is on `orangepi5-max-server1`; the script uses the
   existing node SSH route and captures its original Docker daemon/container
   identity before mutation. It independently observes container exit or removal
   from that same daemon, then checks the new proxy's actual start time. The
   two exact rows remain historical uncertain requests, without claiming model
   success or changing any business record. The original classifier and its
   allowlist are unchanged.
7. After that restart, run `prepare_reactivation_plan.py --help`, then pass the same new worker and
   control digests, all four independent admission directories and four fresh
   permanent 65536-UID starts. The script reads live public configuration and
   deployments; validates actual image labels, source and admissions; and emits
   one full public ConfigMap, four immutable accepted worker hosts, exact CAS
   deployment patches and proposed UID reservations. It performs no writes to
   Kubernetes, DB, secrets or the UID ledger.
8. `reactivation_quiet_guard.py` is the actual shared-control read-only checker.
   It reuses the SHA-pinned existing classifier for the twelve older residuals,
   adds exact classification for the two observed rows only after real proxy
   exit/start evidence, and rejects any additional uncertain work. The guard
   verifies the apply process holds the independent root maintenance flock using
   its parent FD, `/proc/locks` and a separate nonblocking lock probe. It returns:

   ```json
   {"schema":"remote-reference-reactivation-quiet.v1","quiet":true,
    "maintenance_held":true,"uid_database_conflicts":0,"database_writes":0}
   ```

   These assertions come from actual read-only database, current proxy,
   stopped old-proxy and Pod observations. No static success report is used.
   The lock excludes concurrent deployment operators while actual Agent work is
   checked; it does not stop unrelated services or change user-task records.
   The apply process holds it through final verification and releases it on exit.
9. Inspect `plan-files.json`; use its SHA with `apply_reactivation_plan.py`.
   Default invocation validates source/images and the exact live baseline only.
   `--execute` is one-shot: records intent, reserves new permanent UID ranges,
   creates immutable public/host ConfigMaps and switches control deployments
   with resourceVersion + UID CAS. It calls the actual guard before each control
   patch, then independently observes the original proxy Docker container stop
   and runs the guard again before switching the remaining controls.
   It changes no runtime Secret or executor
   caller Secret mount, creates no token, and refuses automatic retry.
10. Parent release operator deploys the new executor and lab, validates current
   NetworkPolicy against real pods, performs authenticated read-only UI/API
   checks, registration-only API verification and actual controlled frame
   extraction. Do not start new business projects, analyses, experiments or
   artistic-generation/model tasks as part of this release smoke.

## Important apply boundary

Kubernetes cannot atomically patch three Deployments. One immutable whole-catalog
ConfigMap gives an atomic route set; held maintenance, exact CAS, sequential
rollout and fresh post-rollout quiet verification govern the control transition.
On partial failure, retain operation evidence and maintenance. Do not rerun the
one-shot apply or silently consume different UIDs. Recovery is an explicit
reviewed CAS operation. Historical bindings, acceptances and UID reservations
stay available; only their old pools are disabled.

Neither technical re-admission nor a successful HTTP render means artistic
quality or source-site stability was approved. Source access failures remain
explicit; reference registration itself does not observe video content.
