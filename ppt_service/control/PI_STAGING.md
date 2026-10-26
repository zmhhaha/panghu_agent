# Pi artifact staging U2: isolated review contract

This module is **not wired** to Dispatcher, PptProfile, execution, a Pi image, or publication. PptProfile.production_ready remains false; its existing asset/continuation gates remain intact. RuntimeConfig is reused only for data_root and storage limits: it still pins the Hermes worker image, and its paid-admission method is not evidence of Pi image admission. No shared runtime or legacy staging behavior changes here.

## Controller entry points

`PiStager(config, trustedruntime_revision, trustedskill_bundle_revision).stage(attempt, checkpoint_manifest=None)` returns the existing `Layout` and source inventory only after validation and independent copies complete. Both pins come from controller configuration, never worker output. Inputs are the real `Attempt` schema (integer fencing token) and repository snapshot core `{project_id, settings, title, assets}`. The core hash is recomputed using repository canonical JSON (`sort_keys=True`, separators comma/colon, UTF-8, ensure_ascii=False); feedback, pages, checkpoint references and confirmation context are not part of that material hash. Settings and exact asset fields are bounded. Changes to materials/settings/title require a new valid snapshot and invalidate an older checkpoint.

Source bytes are read from exactly `projects/{project_uuid}/assets/{asset_uuid}/original`, with the API's limits of 10 assets, nonempty files at most 25 MiB each and 100 MiB material total, with declared size and SHA256 verification. MIME extensions reuse the existing source schema (including real Markdown text bytes). No parser, database, native session or model runs. An initial attempt has an empty writable project and empty home. The existing Layout home field is merely an empty directory; no home/.pi/token/session is restored.

Paths remain `projects/{project_uuid}/attempts/{job_uuid}/{attempt}-{token}` with inputs, task, home, project, result, audit and base subdirectories. This mirrors the existing stable `/work/inputs`, `/work/task`, `/work/project` convention; this module does not mount or start a worker. A target collision, including a dangling link, fails closed. Failure after creating an attempt renames only that newly owned directory to a sibling `.quarantine-*` name and raises; it does not return a layout, issue readiness, or recursively delete anything. Validation failures before creation leave no attempt.

`finish_inputs(attempt, layout, parsed)` is optional and one-shot: it verifies staging identity, the exact input roster and hashes, and empty task directory, then delegates to the existing `Stager.finish_inputs` payload builder. It temporarily unlocks controller-owned inputs to write parsed.json and refreezes inputs/task. The existing parsed-snapshot-hash guard is retained. This is **not** parser authenticity or acceptance evidence; the parent must supply genuine isolated parser results. A forged parsed object with the correct hash is not proven authentic by this method. Source staging alone grants no permission to run the current PptProfile with assets.

## Strict checkpoint read schema

No checkpoint creation/publisher API is provided. The version 1 manifest has exactly these keys:

```json
{
  "schema_version": 1,
  "engine": "pi",
  "profile": "ppt-master",
  "checkpoint_format": "artifact-replay-v1",
  "runtime_revision": "controller-exact-runtime-pin",
  "skill_bundle_revision": "controller-exact-skill-pin",
  "project_id": "canonical-project-uuid",
  "input_snapshot_sha256": "64-lowercase-hex",
  "source_job_id": "canonical-source-job-uuid",
  "source_attempt": 1,
  "source_token": 1,
  "phase": "prepare",
  "project_root": "deck",
  "control_context": {},
  "project": {
    "files": [{"path": "deck/notes.md", "bytes": 10, "sha256": "64-lowercase-hex"}],
    "bytes": 10
  },
  "version_id": null
}
```

The example illustrates field shape, not a complete accepted checkpoint (its confirmation context and example digest must be replaced). Source attempt and source_token are strictly positive integers, not API keys. `version_id` is null for plan_initial/plan_options/prepare source phases and a canonical UUID for generate/revise source phases. It identifies the version **owning this checkpoint**, not the source job's parent version. Revise must explicitly set Attempt.base_version_id equal to this value. Manifest version_id alone is not proof of publication: the parent dispatcher must independently verify authoritative Version/checkpoint/project/issuer/subject and publication eligibility before supplying the expected manifest. Worker accepted/candidate flags confer no authority and are rejected as extra fields.

Storage key is exactly `projects/{attempt.project_id}/checkpoints/{checkpoint_uuid}`. Disk checkpoint.json must match the independently supplied controller DB expected manifest exactly as typed JSON (including all values and types; key order/whitespace do not matter). The implementation rejects duplicate disk JSON keys and NaN. Supplying the worker's file as both arguments is not an independent binding and is outside this API's trusted-caller contract.

Allowed source-to-target phases are plan_initial → plan_options → prepare → generate, and generate/revise → revise. Every continuation requires a checkpoint. Initial planning rejects checkpoints. Native Hermes/legacy home/SQLite/session metadata is rejected; no conversion is attempted.

For plan_options and prepare, website confirmation must already exist in trusted Attempt.snapshot.control_context, assembled by Dispatcher from confirmed Plan records. Stage 1 accepts the actual API selection shape with design_mode=free_design and optional template_id=null. Plan-options snapshot.plan_revision is optional (the current API does not set it); a supplied value must match the positive confirmed revision. Prepare requires snapshot.plan_revision. Both compare snapshot.confirmation_selection exactly. Stage 2 requires selected_direction.id equal selection.direction_id and exactly one equal object in design.directions (or design.options). Optional page_plan/production_parameters selection values must equal selected-direction values, falling back to design values as the API does. Outline and the entire selected object are preserved. Generate/revise must carry exactly the checkpoint's hash-verified control_context, including stage-two confirmation. These structural checks do not invent database proof or mint confirmations from raw wire data.

## Filesystem and resource boundaries

Only indexed project bytes are copied. Each entry has exactly path/bytes/sha256; count is 1–8192, per-file size ≤64 MiB, declared aggregate equals the actual sum, and sources plus project fit max_snapshot_bytes. The default RuntimeConfig limit is 512 MiB. Serialized manifests/snapshots/parsed data are bounded to 4 MiB. A no-follow global inventory is capped at 200,000 entries, counts unrelated links/special entries by their own lstat size without opening or following them (for retained Hermes home compatibility), and reserves source bytes plus two project copies plus 12 MiB metadata within max_storage_bytes. Controller admission must serialize quota checks; this is not a concurrency quota coordinator.

The checkpoint's exact file and directory roster consists solely of checkpoint.json plus project and indexed file ancestors. Extra unindexed files **and empty directories** fail. Paths reject absolute/traversal/backslash/colon/control characters, Windows device/trailing-dot/trailing-space/short-name aliases, case-insensitive component collisions, file-directory aliases, and known native state paths/extensions. Indexed files must be beneath project_root. Root and ancestor directories are inspected for symlinks/reparse points; files reject hardlinks, symlinks and special types. POSIX reads reuse descriptor-relative O_NOFOLLOW open_regular; copies reuse copy_file and atomic_json. The Windows roster uses full lstat (DirEntry's cached stat can report st_nlink=0).

Base/project and writable project are separately copied from the same verified index, never hardlinked. Sources and checkpoint roster/hashes are verified again after copying. Inputs and base are read-only (0444 files, 0555 directories on POSIX), project files are 0600, and files retain controller ownership. No make_writable/chown(10000) is used. **These permissions are not production isolation proof.** A future reviewed Pi image boundary must implement explicit privileged per-attempt UID handoff and read-only mounts. Storage and sources must be controller-exclusive and immutable/stopped during this transaction; preflight checks do not protect against a privileged concurrent process replacing directories. No StopProof is fabricated, no engine persistence is enabled, and no UI/runtime/image integration is claimed.

## Offline test invocation

From repository root, with installed Python and no extra packages:

```bash
PYTHONPATH=./panghu_agent PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s panghu_agent/ppt_service/tests/control -p test_pi_staging.py -v
```

Tests use temporary real files and unittest. Symlink tests explicitly skip only when the host cannot create links; FIFO and true POSIX ownership/mode checks skip on Windows. Such fixture evidence is not Pi production-container admission.

Recorded verification:

- Local Windows Python 3.13.5: 39 tests, 30 passed and 9 skipped (seven symlink privilege cases, FIFO, POSIX ownership/modes), with no failures.
- Additional independent verification reported by the parent: Linux **39/39 passed, zero skips, 0.244 seconds**, including all seven link cases, FIFO rejection and POSIX permission checks. The parent used the existing API image identified by prefix `8148cd`, network disabled, UID 10000, read-only root filesystem and source mount, all capabilities dropped, no-new-privileges, 512 MiB memory and one CPU. No environment file, database or model was used. The disposable runner used `--rm`; the parent checked its exact container name afterward and found no remaining container.
- The parent's remote `/root/ppt-service-pi-staging-r18-a1` directory retains test code only. This was not a live deployment, and PPT production readiness remains false. These Linux results are parent-reported independent evidence, not a run performed by this module's author.
