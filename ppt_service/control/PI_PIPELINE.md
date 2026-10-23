# Offline attempt pipeline

`ppt_service/control/pi_pipeline.py` composes already-verified components into one ordered attempt:

```
stage + finish_inputs  →  PreparedPptContext  →  fixture Pi execution
        →  combined stop proof (Pi UID + every extra writer)  →  frozen candidate
```

It is deliberately **not** a dispatch route. It exists so the order and the boundaries between the components are fixed and testable before anything reaches the website scheduler.

```python
outcome = run_offline_attempt(config, stager, attempt, runtime_settings, scratch,
                              allocator=ledger, parsed=trusted_parser_result,
                              checkpoint_manifest=db_manifest,      # None for plan_initial
                              extra_writers=(preview_uid,), candidate_id=...)
outcome.frozen   # candidate files + measured indexes
outcome.proof    # StopProof covering every writer
```

## Preview phase

`preview=<PreviewSpec>` adds a preview step that runs **after** the Pi writer is proven stopped, so the two identities never write the same tree concurrently. Order:

1. Pi executes in its fresh scratch directory.
2. The Pi UID is stopped and its processes confirmed gone.
3. Traversal is opened from the data root down to the attempt, project modes are set while still root-owned (dirs `0755`, files `0640`), and only then ownership is handed to the preview UID.
4. The official preview starts under that UID and is health-verified as that identity.
5. The preview is stopped and reaped, ownership is reclaimed to root, and the transient `live_preview/` lock is removed so it neither enters the candidate nor blocks a later preview.
6. A single combined proof covers the Pi UID and the preview UID; only then may the candidate be frozen.

The deliberate mode choice (`0755` directories, non-readable files) exists because a root controller without `CAP_FOWNER`/`CAP_DAC_OVERRIDE` must still be able to walk the tree back after handing it over. Concurrent preview during generation is **not** claimed and remains future work.



1. **Linux root controller.** Any other host is refused before staging, so a Windows developer machine cannot silently produce a "successful" attempt.
2. **Writer list validated first.** Every extra UID must be a distinct plausible dedicated UID, and must not repeat the Pi UID; duplicates and malformed values are rejected before staging.
3. **Pi UID from the ledger.** The Pi identity comes from `PiUIDAllocator.verify`, never from a caller-supplied integer.
4. **Preparation is real.** `stage` builds the immutable inputs/base/project layout and `finish_inputs` writes the authoritative task from trusted data; the context constructor then re-derives and re-checks everything it is about to hand to the runtime.
5. **Execution re-checks disk.** The fixture runner rebuilds the context and rejects any drift before spawning, and only runs with `fixture_mode=True`.
6. **Stop before freeze.** `combined_stop_proof` must prove every listed UID has no live process; if it cannot, the pipeline raises and **no candidate directory is produced**.
7. **Completion is not acceptance.** The frozen candidate is an execution artifact. It carries measured hashes and sizes, no `accepted` field, no `checkpoint.json`, no version id, and it does not authorize publication or unlock `production_ready`.

## What a frozen candidate contains

Only `project/` and `result/`, plus a controller-authored `snapshot.json` index. Inputs, task, base, audit and any HOME/native state stay out. A restored phase therefore shows the checkpoint's project files in the candidate, which is exactly the evidence the downstream business validator consumes.

## Limits

- The pipeline runs the **fixture** runtime; no model is called and no real PPT output is produced.
- The stop proof is an in-process trusted contract, not an authenticated cross-service certificate.
- Container/cgroup/network resources are not covered by the proof; a caller that owns them must add their identifiers.
- Restored-phase candidates are not resumable checkpoints: a generate/revise checkpoint still needs an authoritative version binding from the business transaction.
- Nothing here opens or bypasses the PowerPoint manual acceptance or formal publication gates.

## Verification

Windows ran 8 cases with 1 passing and 7 platform skips (Linux root controller and the real stop proof are required).

The parent independently ran all 8 on Linux as root: **8/8 passing, zero skips, 3.154 s**, in the disposable API-image runner (network disabled, readonly root/source, no-new-privileges, only CHOWN/SETUID/SETGID/KILL, 512 MiB/1 CPU/pids 256, private tmpfs, `--rm` container confirmed absent by exact name).

Covered by those cases: a `plan_initial` fixture attempt that freezes only after the proof and contains no accepted/checkpoint fields; a second run whose proof also names the preview UID; a restored `generate` attempt whose checkpoint project file reappears byte-identically in the candidate; a stop-gate failure that leaves no candidate behind; a runtime failure that never reaches collection; refusals for a non-Linux host and malformed writer UIDs; and a restored phase with a real official preview started under its own UID, stopped and reaped, whose transient lock never reaches the candidate and whose marker file appears byte-identically alongside the checkpoint baseline.

Not covered: real models, real PPT generation, preview rendering during generation, network/cgroup isolation, power loss, multi-node storage, deployment, or any acceptance/publication step.
