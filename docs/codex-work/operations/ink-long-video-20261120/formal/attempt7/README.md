> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-long-video-20261120/formal/attempt7/README.md`；原文件 SHA-256：`fffa115d076d4169d837f2a67ef8d981b258c6056f6b7d09de0192027a4e899a`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../../../README.md)

# Attempt 7: five-minute silent landscape film

This folder is an external operational workspace. The committed application is
`D:/codex-work/operations/ink-long-video-20261120/source` at
`ac7f7028003fdccb7f8cb90503a74f99c6499824`. No file here belongs in the Git checkout
or an application image. Attempt 6 and all original captures remain unchanged.

The prepared request reuses all 12 completed scene modules from run
`63961e9b-a7e1-4ad4-86b9-0c8abfa4af50`, retaining its original package/version,
target, namespace and source identities. Its 96 author requests are historical;
this request allows zero new authors, zero audio recordings and one new whole-film
render. The film is silent, 300 seconds at 24 fps: 7200 expected frames. This
preparation has not submitted a run or started any observer, render or model.

## Evidence and entry points

- `reuse-baseline.json` is the full 12-module A custody baseline, SHA-256
  `af98fd74254f90fbbd99b1471792ec17f330f7b55ade2a2053e1af320fd4ab88`.
  It pins the actual A source, ancestors, all 13 stopped children and original
  native receipt/count files. `prepare_baseline.py` has already run; do not rerun it.
- `actual-runtime-inputs.json` is the explicit interface for real B/P/C evidence.
  All runtime values remain null until root supplies actual reviewed evidence.
- `attempt7_contract.py` checks custody, the exact evidence hashes, installed
  configuration expectations, original module receipts and the real server receipt.
- `seal_preflight.py` validates and seals concrete local evidence, without network.
- `attempt7_driver.py` provides the offline check, read-only reconciliation and
  separately dispatched single POST. `state.json` records its durable intent.
- `actual_facade_projection_remote.py` and `attempt7_transport.py` provide
  read-only SQL/facade checks and exact workbench Pod binding. Programs stream
  through stdin, rather than shell arguments. Current policy/configuration come
  from the installed server settings; request metadata cannot grant authority.
- `launch_observers.py` starts `watch_run.py` only after an actual run exists.
  The watcher invokes `observe_run.py`, starts `follow_exact_render.py` before the
  single original-source capture, and reads stage metadata with
  `observe_render_progress.py`. Auxiliary capture failures preserve evidence and
  do not stop journal observation. No old native artifact is downloaded.
- `collect_originals.py` / `collect_film_sources.py` copy original packages,
  source bytes and the actual new snapshot. Scene and transition inventories are
  checked exactly. `adapted-tools-manifest.json` records the unchanged old inputs.
- `download_actual_film.py` / `remote_download_actual.py` download only this run's
  completed actual MP4, preserving HTTP/SQL/snapshot/media custody and partial
  evidence on failure. They do not render or recreate a snapshot.
- `verify_delivery.py` runs the two unchanged, SHA-pinned independent local media
  tools. It saves tool logs and failure status even on timeout.
- `offline-preparation-validation.json` and `helper-file-manifest.json` record
  local validation and final preparation hashes. They are not production evidence.

## Fill and seal the actual interface

Root supplies absolute regular JSON file paths and exact file SHA-256 values for
`policy`, `configuration`, `renderer_release`, `rollout_postflight`,
`source_policy_dry_run`, `physical_quiet` and `root_review` in `proofs`. Fill every
`expected` slot with actual values: renderer image and namespace; P, C and
execution-admission SHA; workbench pinned image, runtime imageID and exact Pod UID.
Set status to `actual_evidence_ready` only after those concrete files exist.

The policy validators bind real original source A to actual renderer B and target
configuration C. The root review must use status
`root_actual_attempt7_preflight_review_passed` and contain:

```text
source_run_sha256, baseline_sha256, source_revision,
policy_sha256, configuration_sha256, renderer_image,
execution_admission_sha256, renderer_namespace,
workbench_image, workbench_image_id, workbench_pod_uid,
proof_file_sha256 = { each non-root-review proof name: exact file SHA-256 }
```

These values must match the concrete supplied proofs and expected fields. A ready
workbench Pod must still match the sealed UID, image and runtime imageID at each
remote read. Rollout, quiet and dry-run assertions require root review of their
actual contents; the helper's file-hash check alone is not a semantic review.

## Root dispatch sequence

Run from this directory with the existing Python runtime. Offline commands can be
run while actual inputs remain null:

```powershell
python -B attempt7_driver.py offline-check
python -B seal_preflight.py --plan
python -B launch_observers.py --plan
```

After actual evidence has been reviewed and filled, root separately dispatches:

```powershell
python -B seal_preflight.py
python -B attempt7_driver.py run --preflight-evidence D:/codex-work/operations/ink-long-video-20261120/formal/attempt7/continuation-preflight-verified.json
python -B launch_observers.py --start
```

The driver does a fresh read-only project/source/configuration/readiness check,
then persists intent before its one standard production POST using key
`ink-quiet-film-20261120-run-v7`. The HTTP body contains only expected revision,
adopted plan, silent mode and the reused source run. It supplies no policy or
admission receipt. If the POST outcome is uncertain, never replay it or delete
the intent. Use `python -B attempt7_driver.py status` to reconcile the same key.
An accepted or reconciled actual run is required before observers can launch.
Inspect existing launch/lock/intent files before any recovery; no automatic
duplicate observer or source capture is permitted.

The native follower binds the deterministic Job name, first observed Job UID,
Pod ownerReference/UID, snapshot/binding annotations and exact immutable B image.
It preserves Kubernetes merged container logs with timestamps, resources, status,
termination details and read failures before normal service cleanup. Each log
attempt is a separate file; repeated bytes across attempts are possible. Logs
have byte/time budgets. Missing resources, disconnects and incomplete coverage
are labelled absent or partial, with an inventory even on failure. Complete native
log coverage is never claimed. It creates, edits and deletes no cluster resources.

Stage labels, `1/1`, Pod termination and resource absence do not establish
captured frames. Every observer keeps `captured_frames=null`; even a completed
service journal still requires verification of the actual media.

## Verify the real delivered media

Only after the actual run/render is cleanly completed and stopped:

```powershell
python -B download_actual_film.py --state D:/codex-work/operations/ink-long-video-20261120/formal/attempt7/state.json --output D:/codex-work/operations/ink-long-video-20261120/formal/attempt7/actual-media --download
python -B verify_delivery.py --verify
```

The output directory must be new. An uncertain or failed download preserves
partial files and a failure receipt; inspect them before deciding any recovery.
The wrapper verifies the exact original snapshot and MP4 SHA, performs full media
decode, checks 300 seconds/24 fps/7200 frames and silence, and produces motion
review materials. It does not grant artistic acceptance. Root still reviews the
actual continuous film and contact sheets before delivering the MP4 to the user.
