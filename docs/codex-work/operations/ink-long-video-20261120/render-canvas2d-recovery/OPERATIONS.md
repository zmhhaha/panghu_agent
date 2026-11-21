> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-long-video-20261120/render-canvas2d-recovery/OPERATIONS.md`；原文件 SHA-256：`cbc856accee187b1eeb427bb2036e669f85a23c241b2f7948e4249e79479abd2`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../../README.md)

Prepared operations only. No build, image measurement, new runtime probe,
admission, rollout or production request has been executed by this preparation.
The three completed af8 launch-override diagnostics remain explicitly separate
from a real B runtime admission.

The local code checkout is `../source`. The private operation directory is
outside that checkout. Copy these operators and the sealed A baseline to the
matching `/root/video-releases/ink-film-20261120/render-canvas2d-recovery`
directory only when root dispatches the next phase. Keep private A evidence,
P and generated configuration outside Git and Docker build contexts.

1. Root finishes and tests the formal browser flag, exact full12 reuse and the
   server-owned A-to-B policy implementation, then supplies a clean full Git
   revision and an ordinary clean ARM checkout of that revision. Do not build
   the current uncommitted checkout. The reviewed host delta is the one
   `--disable-accelerated-2d-canvas` argument and its two explanatory comments;
   all five other film hosts must remain identical to A.
2. Run `build_renderer_workbench.py --source CLEAN_SOURCE --revision COMMIT
   --output REMOTE_OPERATION/build`. It uses the existing full Git-file
   measurement and readable `_workbench_context`, then the ordinary renderer
   and workbench Dockerfiles. It builds/pushes only their `latest` tags and
   records digests, clean revision, labels and context/source hashes. It does
   not call the old five-image builder or rebuild control, worker or creation.
3. Run `measure_renderer_delta.py --baseline-release baseline-a-release.json
   --source-a-baseline source-A-terminal-baseline.json --build
   build/build-completed.json --output REMOTE_OPERATION/actual-image-delta`.
   This creates only two bounded read-only measurement containers. It compares
   the actual old A and actual new B image: fingerprint fields must match
   except `image_digest`; exactly one of six host hashes may change; the exact
   reviewed browser source delta must match; all `/opt/assets` files, licences,
   capability fields except identity and 28 published library files must match.
   Any drift stops the recovery rather than broadening P.
4. Use the unchanged original `arm_long_film_probe.py` against the real B image
   and clean revision, new output `REMOTE_OPERATION/probe61`, `--seconds 61`.
   This is a complete 1464-frame long-path technical export, not the user's
   7200-frame film. The existing operator records a real main realm, three
   fresh reverse/end/middle checks, one continuous encoder, no PNG spool,
   original clock, independent full audio/video decode, silence, cgroup v2
   memory/OOM/PIDs, sampled writable bytes and exact process/container cleanup.
   A 300-second technical fixture is supported when root selects that stronger
   duration; name its new output `probe300` and keep the truthful duration.
5. Use unchanged `arm_film_k8s_probe.py --image B --revision COMMIT --output
   REMOTE_OPERATION/k8s-probe`. It runs one real one-second completed Job plus
   one running 61-second Job cancelled by exact UID/foreground deletion. It
   preserves the current executor Deployment and existing deny-all policy.
   Keep original Job/Pod UIDs, image IDs, native receipts, independent media,
   active Chromium/encoder observations, disappearance and helper cleanup.
   This cancellation is not the HTTP/durable-journal cancellation test.
6. Run `prepare_execution_admission.py --build build/build-completed.json
   --delta-directory actual-image-delta --long-probe probe61 --k8s-probe
   k8s-probe --output REMOTE_OPERATION/admission-B`. This only reads and verifies
   the actual B artifacts and inventories. It creates new B job/media/cancel
   evidence and technical admission, preserving A. It emits the complete
   `target-B-runtime-identity.json` and `B-identity-for-policy.json`. The four
   fields are image digest, engine runtime SHA, digest of the six-host map, and
   `hyperframes-film-execution-v1@REAL_NEW_B_ADMISSION_SHA`.
7. Replay the current original admitted Manim consumer with unchanged
   `manim_consumer_upgrade_ops.py`, a new `REMOTE_OPERATION/manim-consumer-B`
   root and B. Use its export, prepare, render, collect with real B execution
   admission, and admit phases. No producer/model/Cairo regeneration is needed.
   The old consumer package, assets, recordings, producer image and old evidence
   remain immutable. B requires a new consumer admission because the existing
   one binds A's complete formal release. Keep its actual stopped render and
   independently decoded 384-frame/16-second output. Clean only its exact owned
   container after collecting the original stop/identity records.
8. Root constructs P using the final committed policy contract and the sealed
   `source-A-terminal-baseline.json` (raw SHA
   `0e06357a09464387492381ef801786937a4bfdd6115ecaac479fd4802b06e42c`).
   Source-run document digest is
   `e1780cc74507fdc539e9cb96db8acc7d1c3604f50e9edb3892f953cc1b98d865`.
   Bind the full A configuration/identity, owner/project/run/film, stopped
   render, original c23b snapshot and ordered12 authentic package scope. Bind
   actual B identity/admission/fingerprint/hosts/capabilities, exact browser
   delta and the real B Manim consumer context when A resource authoring is
   nonempty. Create B admission first, P second, C containing P third; P cannot
   contain target C's hash. Keep evidence references truthful: current af8
   override stills demonstrate the candidate/composition only and do not prove
   B's admission or a completed 300-second movie.
9. Once P/B/consumer evidence is reviewed, run `prepare_rollout.py capture
   --output REMOTE_OPERATION/rollout`. Then run its prepare mode with `--build`,
   `--execution-admission`, `--manim-report`, `--policy`, `--source-a-baseline`
   and `--delta-directory`. It produces three immutable public ConfigMaps and
   three full-spec/UID/resourceVersion CAS patches. It does not apply anything.
   Workbench gets B release, B consumer admission and a separate readonly P
   ConfigMap mounted as the single readonly file
   `/etc/video-film-source-runtime/source-runtime-admission.json` with
   `subPath=source-runtime-admission.json`; its only new env is
   `VIDEO_FILM_SOURCE_RUNTIME_ADMISSION_FILE`, pointing to that same file. This
   avoids ConfigMap projection symlinks at the loader path and preserves the
   loader's strict ordinary-file requirement. The motion service uses the new
   workbench image and real B runtime/admission, preserving the old motion
   engine. Creation keeps its existing image and only changes its original
   consumer-admission file pointer. Caller Secret references, old ConfigMaps,
   original producer, native author catalog/bundle, assets, engine/library and
   legacy identities remain exact. The final policy contract's RA branch must
   be complete in B before this prepare can pass.
10. Root reviews the concrete new plan and a fresh no-active-job audit, then
    separately dispatches `apply_rollout.py --prepared REMOTE_OPERATION/rollout
    --plan-sha256 ACTUAL_PLAN_RAW_SHA`. It never retries an uncertain plan;
    reconcile the existing apply marker before any new action. Order: motion,
    creation consumer pointer, workbench. Each waits Ready and compares exact
    planned specs. Finish semantic postflight: current C hash, server-owned P,
    real B admission/target4, B consumer capabilities, original library/native
    catalog and unchanged old source/admission data. Readiness alone is not
    final validation.
11. Use the existing actual service/client journal cancellation/recovery probe
    against the newly admitted B snapshots after rollout, preserving credentials
    inside existing Pods. Root then submits exactly one fresh ordinary
    full12-reuse production run. Budget is zero new authors/recordings and one
    new whole-film render. Preserve all old module packages and the original A
    snapshot. Only its actual 7200-frame H.264/AAC MP4 with full decode/silent
    PCM/clock/motion review fulfills the selected five-minute video task.

The current contact sheets show weak/faceted mountain material, late straight
plane edges and a confirmed detached reed leaf in scenes11/12. These are visual
review findings, separate from renderer technical admission. Source changes for
art must receive their own legitimate version/boundary review; do not silently
rewrite the sealed twelve packages during this infrastructure recovery.
