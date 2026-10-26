# ARM64 execution image build report

Status: R8/V2 is available as a manual-review deck. Its original actor manifest
remains NEEDS_INPUT with incomplete artifact references; it did not pass formal
candidate handoff. Separate controller read-only review and collection retain
needs_input and do not modify the original. Only P3/P5 changed; the other four
slide XML files are exact matches. No runtime or accepted base was promoted and
nothing was published. PowerPoint version, actual open/edit/save/reopen evidence
and real page feedback remain outstanding.

The R8 Worker image was `ppt-service-worker:arm64-c350bc31-preview4`. The later
preview5 repair is locally built and passed all 169 restricted ARM64 tests
(9.145 seconds, no skips), plus the real V2 nested-marker preview lifecycle.
No push, deployment or model rerun occurred. Historical builds follow; the final
section records preview5 evidence and the unchanged manual-acceptance boundary.

## Inputs and host

- Builder: `root@192.168.137.101`, native `aarch64`, Docker `29.6.1`.
- Isolated context: `/root/ppt-service-build`.
- Output tag: `ppt-service-worker:arm64-c350bc31-verify` (local Docker store only).
- Base: `arm-cluster-master:5000/hermes-intelligence@sha256:1398c1eddde05516af6e7991b809c3f1592ec503fe9ce901212ed6a95fb96d91`.
- Base runtime checked without network and with its entrypoint overridden:
  Debian GNU/Linux 13 (trixie), Python `3.13.5`.
- Base `/opt/data` contained only `.bash_logout`, `.bashrc`, `.profile`;
  `/root/.hermes` and `/root/.ssh` did not exist; `/home` was empty.
  This is a runtime-directory inspection, not an audit of every historical layer.
- PPT Master archive commit: `c350bc31e6d43b7bcf5234183d650ba8f9f4c9d7`.
- Archive SHA-256: `8dc15aa98b9906ee0aba38c2b8b1496b1c3a4db05694c22de023131e81864f98`.

## Build command

```bash
bash /root/ppt-service-build/build.sh \
  --base-image arm-cluster-master:5000/hermes-intelligence@sha256:1398c1eddde05516af6e7991b809c3f1592ec503fe9ce901212ed6a95fb96d91 \
  --image ppt-service-worker:arm64-c350bc31-verify
```

The first host probe showed no buildx plugin. The script uses the standard
Docker builder on native ARM64 and streams an explicit allowlisted tar context.
The builder reports its legacy-builder deprecation; installing buildx is optional
for this verification and was not performed.

## Results

Pending completion of the corrected build. The first attempt passed font
installation, archive hash/commit validation and the Skill guard/version check,
then failed because the inherited uv-managed venv contained no pip. The base's
`ensurepip` module was available (bundled pip 25.1.1); the Dockerfile now bootstraps
it and installs the pinned pip 25.3.

The second attempt resolved binary ARM64 wheels for python-pptx, lxml 6.0.2,
Pillow 12.0.0, mammoth 1.9.1, PyMuPDF 1.26.7 and skia-pathops 0.9.2, but the
Python 3.13 / ARM64 index had no uharfbuzz 0.50.0 wheel. Its resolver listed
0.50.2 as available; the lock was corrected to 0.50.2 without relaxing the
binary-wheel requirement. The third attempt installed every candidate wheel,
including the Python 3.13 ARM64 uharfbuzz 0.50.2 wheel, then correctly failed
`pip check`: Hermes 0.21.5 requires Pillow exactly 12.3.0, and inherited MSAL
1.36.0 requires cryptography below 49 while the base contained 50.0.0. The lock
now preserves Pillow 12.3.0. An attempted cryptography 48.0.0 correction was
rejected by the next `pip check`, which exposed Hermes' own exact
cryptography 50.0.0 requirement. That intermediate pin is not retained.
Read-only package metadata inspection showed that Azure Identity permits MSAL
>=1.35.1 and MSAL Extensions permits MSAL <2; current MSAL 1.39.0 permits
cryptography <51. The final candidate keeps cryptography 50.0.0 and upgrades
MSAL to exactly 1.39.0 instead. The compatibility metadata was obtained from
`https://pypi.org/pypi/msal/json`. No dependency-check bypass is used.

The fifth build completed with exit 0 and image short ID `77605d4511b0`.
`pip check` reported `No broken requirements found.` The UID 10000 smoke receipt
reported `passed`, `architecture=aarch64`, `chromium=141.0.7390.37`, and
`cjk_font=Noto Sans CJK SC`; every pinned dependency version matched. Playwright
installed Chromium build 1194, Chromium Headless Shell build 1194, and its
bundled FFmpeg build 1011, all for ARM64. The final source rebuild is tracked
separately below.

The first build's context preceded the separately authored `worker/execute.py`;
subsequent contexts include it and `prompts.py`. The final context also includes
`policy_plugin.py` and `model_proxy.py`. Constructing an image does not execute
that entrypoint or prove its API behavior.

## Final image and restricted verification

The final build, including the completed `worker/` source, passed with exit 0.

- Local image ID: `sha256:e31083d8e59fb9c9e08b6b724303866592cc11ef2f33ea2755d0544d6cc43849`.
- Local tag: `ppt-service-worker:arm64-c350bc31-verify`.
- Created: `2026-10-20T17:00:09.442147421Z`.
- OS/architecture: `linux/arm64`; image size: `4164638373` bytes.
- User: `10000:10000`, with a named account available for uid/pwd lookups.
- Entrypoint: `["/opt/hermes/.venv/bin/python", "-m", "ppt_service.worker.execute"]`.
- This local image ID is not a registry manifest digest; no push was performed.

The additional restricted smoke also exited 0, reporting `passed`,
`architecture=aarch64`, `uid=10000`, `chromium=141.0.7390.37`,
`cjk_font=Noto Sans CJK SC` and exact agreement with every dependency pin:

```bash
docker run --rm --network none --read-only \
  --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 128 --memory 1g --cpus 1 \
  --tmpfs /tmp:rw,nosuid,nodev,size=256m \
  --tmpfs /work/home:rw,nosuid,nodev,uid=10000,gid=10000,size=64m \
  --entrypoint /opt/hermes/.venv/bin/python \
  ppt-service-worker:arm64-c350bc31-verify /opt/ppt-service/check_runtime.py
```

The worker entrypoint was not run in this task. The parent task owns the
separate Gateway `--probe-only` check and later model/sample acceptance.

## Acceptance boundaries

The image smoke check covers imports, exact Python versions, attribution guard,
Noto CJK font selection and Playwright Chromium PNG generation in memory.
It does not create a presentation, run Office, invoke a model, prove native chart
editability in PowerPoint, validate a Gateway contract or establish production
network isolation. The resulting image must be referenced by its build result
and later registry digest when used for a deployment. No deployment is performed
by this build task.

## Continuation: durable proxy usage and target regression

The continuation built `ppt-service-worker:arm64-c350bc31-usage`, initially
`sha256:2f1f899751b3eeb3c4bc2396f00a94a33d7a19ec8e63a5ab254e8b840d813fc0`,
including `proxy_usage.py`. The UID 10000 dependency/browser smoke passed.
The current source passed all 85 unit tests in a network-disabled ARM64
container (7.141 seconds); after requiring a trusted caller-supplied chart
oracle, the same 85 tests passed again (6.671 seconds). The later source rebuild
must be identified by the actual attempt's recorded image ID, not this initial ID.

The earlier isolated Gateway probe is recorded as `probe_passed`, zero model
requests, session created and Gateway stopped. This establishes the no-model
HTTP lifecycle, not generation or checkpoint acceptance.

Public sample preparation now verifies the pinned archive SHA-256 before
extracting any sources. Five public/sample assets were prepared with snapshot
`f66d2e18eb73ea458f4e9bd6761be760e1c6d6741e4f9c4fa928b73274ef587b`.
The model hostname resolves to Fake-IP on the host; the sample launcher pins
its operator-documented public VPS address `62.234.50.20` while retaining HTTPS
hostname/certificate verification. An unauthenticated `/v1/models` check returned
401 with TLS validation enabled and remote IP `62.234.50.20`.

The new `scripts/run_sample.py` starts one phase only, keeps the upstream key
only in the proxy, persists proxy audit separately, and installs narrowly scoped
Worker firewall rules before its preflight. Actual phase, cleanup and network
acceptance are recorded per attempt; source review alone does not establish them.
No generated PPTX or PowerPoint manual acceptance has been claimed here.

### First real phase result

`plan_initial` completed with image
`sha256:d6f73e6d621a96f5f2559c4e92c27e05d537fb1b84c34d40897ef78cf3fed1e8`
and attempt `ec9dc1f0ed774d998f841e5825c95419`. Worker and proxy both stopped;
the source-scoped firewall rules and internal network were removed. The actual
network preflight passed before the model ran. Independent validation checked
all five authorized inputs and 8 declared artifacts (106138 total bytes), with
`published=false` and `quality_reports_verified=false`. The owner then explicitly
confirmed the six-page Chinese, 16:9, free-design technical acceptance sample;
`plan_options` is a separate attempt restored from that stopped checkpoint.

The live planning manifest exposed an adapter contract mismatch: planning prose
was being treated as final report references. Planning claims are now ignored
as evidence, while explicit report paths/hashes are still checked. Generated
phases retain strict report reference handling. An additional regression covers
this boundary. A target rerun exposed an upstream-error accounting race; error
responses now finalize the durable ledger event before returning. All 86 tests
passed after the fixes (6.965 seconds).

The first phase ledger has 16 starts and 14 finishes, all 14 finished outcomes
completed; observed finished usage totals 722014 input and 5421 output tokens.
Two missing terminal records remain unknown/potentially billable. No provider
cost values were reported; no zero-cost or complete-cost claim is made.

## Source-repair build and restricted target verification (2026-10-21)

A source-repair build completed successfully through `build.sh` in the separate
context `/root/ppt-service-repair-20261021`. The pinned base image and PPT Master
vendor archive/commit remain unchanged.

- New local tag: `ppt-service-worker:arm64-c350bc31-shutdown2`.
- Local image ID: `sha256:fd8ceaf194f4ec26def150ceb658494c3efe0631ef928749d368987b132f5d02`.
- UID 10000 / native aarch64 smoke passed, including Chromium `141.0.7390.37`
  and Noto CJK font availability.
- All 107 tests passed in 7.536 seconds in this new image, including the real
  Linux SIGTERM test. The verification container had networking disabled, a
  read-only root, all capabilities dropped, no-new-privileges, and resource
  limits. Only tests/scripts were mounted read-only; no credentials or Docker
  socket were mounted.
- Eight local checkpoint/deadline tests also passed. No tests were rerun merely
  to synchronize this report.

The repaired source records proxy shutdown as `proxy_shutdown` / unknown when
SIGTERM interrupts it; it does not fill historical missing terminal records.
Candidate validation accepts standard printerSettings and precise root metadata
while continuing to reject macro/OLE content. The sample runner now accepts an
explicit `--deadline-seconds`: defaults remain 600 seconds for planning and 1800
for other phases, with maximums of 600 and 3600 seconds respectively.

This was a local image build only: no registry push, deployment, or new model
request occurred. Old images and attempts remain available. The failed generate
attempt and its unaccepted draft remain failed/unaccepted; these build and test
results do not establish visual review, PowerPoint rendering, or manual
acceptance, and do not change `quality_reports_verified=false`.

No retry was started as part of that build/report update. A later authorized
attempt 5 stopped without the required handoff; its delivery failed, as recorded
below. Any future retry still requires the user's choice and must start a new
attempt from the independently verified successful prepare checkpoint, never
from a failed runtime.

## Handoff repair build and restricted target verification (2026-10-21)

Both local repairs were built from fixed source in
`/root/ppt-service-handoff-20261021`; the pinned base and vendor remain unchanged.

- Latest local tag: `ppt-service-worker:arm64-c350bc31-handoff3`.
- Local image ID: `sha256:a11c936ddeb94c8d1e2a217c410778f659ea277dcf22b946d029fe06f4b3247b`.
- UID 10000 / native aarch64 smoke passed, including Chromium `141.0.7390.37`
  and Noto CJK.
- All 125 tests passed in 7.566 seconds in this image. The container had networking
  disabled, a read-only root, all capabilities dropped, no-new-privileges and
  resource limits. Only tests/scripts were mounted read-only; no credentials or
  Docker socket were mounted. This documentation update did not rerun tests.

The `execute.py` candidate handoff now requires a regular non-link file, bounded
reading at 4 MiB, strict JSON, and phase/snapshot/status checks. A missing or
invalid manifest fails with exit 4 and `candidate_output_missing` or
`candidate_output_rejected`. The original `hermes_status` is retained and
`candidate_validated=false`; the final independent validator is unchanged.

The draft prompts prefer simple arithmetic and file tools without calling the
official calculator early. A blocked phase must hand off a real `needs_input`
status without inventing a candidate or passed result. CLI confirmation wording
now distinguishes actual chat confirmation from the unimplemented website flow.

The accurate attempt 5 diagnosis is a terminal pure-arithmetic `python3 -c`
command, with no file/network/process operations, triggering the generic
`script execution via -e/-c flag` approval rule. It waited 333.10 seconds before
timing out; the blocked command did not run. Smart mode defaults to a 300-second
timeout, and the API retains `ask`; `NON_INTERACTIVE=1` does not imply no-wait.
The specific reason the smart guardian did not approve remains unknown, so this
is not asserted to be a guardian timeout. Approval policy remains enabled and
the allowlist was not broadened. The blocked command was not executed.

Attempt 5's runner reported `candidate_execution_finished` and Hermes reported
`completed`, but independent validation rejected the absent `result/manifest.json`.
Only the first three SVG pages existed, with no PPTX. The phase remains a failed
delivery; the partial runtime is not a recovery or accepted base. This was an
approval/handoff contract failure, not source-material distortion.

This repair build is complete, with no push, deployment or subsequent paid
attempt. The prompt changes have not been verified through a new paid generation.
Any future retry requires the user's choice and a new attempt from successful
prepare. All old originals and images remain retained. Real page feedback and
final PowerPoint manual acceptance remain outstanding; no phase acceptance or
production-readiness claim follows from these tests.

## Preview4 build and official foreground lifecycle verification (2026-10-21)

The new local image was built in `/root/ppt-service-preview-20261021`; pinned
base and vendor remain unchanged. Historical images, attempts and all originals
remain retained.

- Latest local tag: `ppt-service-worker:arm64-c350bc31-preview4`.
- Local image ID: `sha256:aadf6d7a9d162969e188030c7e1c2d9ee95f7554363d8ae1f1702692d3eed8fc`.
- UID 10000 / native aarch64, Chromium 141 and Noto smoke passed.
- All 142 tests passed in 9.503 seconds, with no skips, in this image itself.
  The container was network-disabled, read-only-root, drop ALL,
  no-new-privileges and resource-limited, with tests/scripts mounted read-only,
  tmpfs working storage, no credentials and no Docker socket.
- Tests included the real official foreground server starting with an empty
  project, health/project/PID binding, shutdown and process-group disappearance.
  They did not render PNGs or send model requests. This documentation update did
  not rerun tests.

The new `preview.py` controller fixes the current project's endpoint to
`127.0.0.1:8765` and launches the real official `server.py` in foreground mode,
without `--daemon`. Health checks bind pid/project/service/live/status/svg_output.
The minimal server environment contains no model/API keys. Readiness must pass
before admission; failure does not submit a paid run. Finally, real HTTP JSON
shutdown is followed by wait/TERM/KILL of the process group; cleanup failure
exits 1. An inherited preview lock fails closed without killing old processes
or modifying originals.

COMMON and input_message now identify the controller-managed URL and actual
channel. Agent background/PTY restrictions and approval policy remain unchanged.
SVG is not a substitute for PPTX, and PNG output does not establish passage of
the visual gate.

This update built and verified the local preview lifecycle only: no push,
deployment or subsequent model retry occurred. Attempt 6 remains `needs_input`,
with no PNG/PPTX, publication or PowerPoint manual acceptance. The subsequent
private working continuation has now completed strict validation, restricted
regression tests and a free preflight, as recorded below. It is limited to a
private working copy and does not establish an accepted base.

## Controller-only working continuation and free preflight (2026-10-21)

The controller package is `/root/ppt-service-continuation-20261021/ppt_service`.
Controller scripts are not built into the Worker image: preview4 and its ID
`sha256:aadf6d7a9d162969e188030c7e1c2d9ee95f7554363d8ae1f1702692d3eed8fc`
remain unchanged.

The CLI flag `--continue-needs-input` defaults to false and is required together
with a task.continuation object containing exactly five keys: kind (`needs_input`),
source_manifest_sha256, approved_spec_sha256, approved_lock_sha256, and resolution
(`worker_supervised_official_preview`). Only generate-to-generate continuation
from a legitimate needs_input source is accepted. Checks cover both containers
truly exited 0, mounts and identity, a new incremented attempt, unchanged Stage1/2
confirmations and assets, actual input hashes, all manifest artifact/quality-ref
hashes, approved spec/lock hashes, absence of all PPTX files, and a no-link scan.
Failed/timeout/candidate/revise sources and base_version_id are rejected; default
phase transitions are unchanged.

The new controller plus preview4 passed all 166 tests in 9.121 seconds, without
skips, on real ARM64 in a network-disabled, read-only-root, drop ALL,
no-new-privileges, tmpfs environment without credentials or Docker socket.
CLI help confirmed the new flag. This documentation update did not rerun tests.

The real free probe at
`/root/ppt-service-sample/acceptance-v3/controller-continuation-probe-6`
called only continuation_inputs and checkpoint clone, with no run or admission.
The actual attempt 6 source had nonempty Stage1/2 confirmations. Scope was
`private_needs_input_continuation`; integrity checks passed for 18 files / 184910
bytes, and every one of the six SVG hashes matched its source. The source manifest
SHA-256 `e2da44ca17feea10232d7c27e60ba0fd23f763d3ee18af04134a986dc4b26b88`
was unchanged.

The clone's project then ran the real official foreground preview service in a
network-disabled preview4 temporary container with no key. It verified health ok,
live_preview, the actual same project, six slides and PID equal to the owned
child; the process group truly stopped afterward. Reports are saved as
`probe-report.json` in the probe directory and locally as
`.runtime/recovered-options/continuation6-probe-report.json`.

No PNG was rendered, no PPTX exported, no model request sent and no business
attempt 7 created. Neither the source runtime nor an accepted base was promoted;
there was no push or deployment. All previous images, attempts and originals
remain retained. A proposed next run would keep the six pages and continue only
pending Step 7 visual review/export, correcting pages only if actual visual
inspection finds a problem. That paid continuation has not been authorized or
started at this snapshot. Attempt 6 remains needs_input; real page feedback and
final PowerPoint manual acceptance are still outstanding.

## Attempt 7 candidate V1 completion and independent checks (2026-10-21)

The user selected `复用六页继续（推荐）` in the real question
`resume_verified_six_svg`. Task `sample-task.generate.continue-7.json` has SHA-256
`d1d38cab5f4e48af8ef5e93794c46e8c130a3bf3cae42f14c2cbff78f859fb22`.
Scope was `private_needs_input_continuation`, with attempt 6 source manifest
`e2da44ca17feea10232d7c27e60ba0fd23f763d3ee18af04134a986dc4b26b88`.
The Worker image remained preview4, ID
`sha256:aadf6d7a9d162969e188030c7e1c2d9ee95f7554363d8ae1f1702692d3eed8fc`.

Attempt `ed00fb6abfe2489b8f7834ce43f66d9b` completed normally in 750.2 seconds.
Worker/proxy exited 0; Gateway and preview stopped, and firewall/internal network
cleanup were true. `delivery_published=false`. The original manifest passed
strict validate_sample directly, with no normalization or modification: status
candidate, 33 artifacts / 965257 bytes, inputs verified true, six slides and one
chart (slide 4, chart401). Trusted categories Q1/Q2/Q3 and Cloud [12,15,19] /
Services [8,9,11] matched the cache and embedded workbook. Both
quality_reports_verified and visual_acceptance remain false; it is unpublished.

The PPTX `exports/acceptance-public-sample_20261021_112554_native_charts_tables.pptx`
is 43360 bytes, SHA-256
`dcff0a90aa089ef4e46746d516f95fa148da0223cf67ff2daecec28108006068`.
It was copied locally to `.runtime/recovered-options/acceptance-sample-candidate-v1.pptx`
and presented to the user for manual review. Six PNGs and an Agent visual report
exist, but do not establish parent-independent visual or PowerPoint acceptance.

Independent python-pptx/OOXML checks verified 16:9, six visible pages, editable
text on every page (including a 234-character continuous text block on P2), the
P4 native column-clustered source-data chart, and only one picture on P3. That
picture matched its source SHA, retained its full 4:1 image without cropping,
and stayed within slide bounds. There are no notes, animations or transitions;
chat-confirmation wording does not claim submission through a website.
Local reports are `retry7-independent-pptx-check.json` and `retry7-validation.json`.

The no-link private snapshot
`controller-check-retry7-ed00fb6abfe2489b8f7834ce43f66d9b` passed revalidation of
the 33-artifact graph and native oracle. Approved spec/lock SHAs were unchanged
and the old preview lock was absent. In an independent restricted, network-disabled
scratch container using the pinned image, attribution_guard exited 0 and the
static final checker passed 6/6 with zero warnings/errors. The snapshot records
this in `controller-check.json`; it is not an accepted base or publication.

Usage closed at 26 starts / 26 finishes, without unpaired/duplicate records:
24 completed / 2 proxy_shutdown. Observed input/output tokens were 3344823/18169;
there were zero provider-reported cost entries, so cost remains unknown, not zero.
Attempts 4/5/6, all originals and all images remain retained.

The sample is not finally accepted, and website/API/DB are not deployed. The user
must supply the PowerPoint version, open the deck, edit chart data, save and
reopen, and provide real page feedback followed by at least one revision. The
parent has no usable visual capability; no independent visual acceptance or
final PPTX rendering check is claimed. The candidate's future role/base still
requires explicit user selection. This was documentation-only synchronization,
without MCP updates, source changes, test execution or model calls.

## R8/V2 manual review, actor needs_input and controller checks (2026-10-21)

At 20:48 the user said `可以，继续`, authorizing the proposed P3/P5 status-wording
revision. The revise8 task SHA-256 is
`987cf8b31ab0c8d0dbc08133c38c1a266462eacea3dc973f6555a2e56c805453`, with
base_version_id `sample-review-v1-dcff0a90aa08` and review-version-maps bound to
the original V1 SHA. This was not actual PowerPoint acceptance.
Attempt `d6ddeff1bd5f4a8a8bb6e7e498894017` used preview4, image ID
`sha256:aadf6d7a9d162969e188030c7e1c2d9ee95f7554363d8ae1f1702692d3eed8fc`.
Execution completed in 1286.08 seconds, Worker/proxy exited 0, Gateway/preview
stopped, and network/firewall cleanup were true.

The raw actor manifest status is NEEDS_INPUT. It declares only five artifacts
and has incomplete artifact-graph references. A final extra read-only package
comparison `python3 -c` waited for tool authorization and timed out without
running; the known rule is unknown. The original `result/manifest.json` remains
unchanged. This must not be described as merely an omitted directory, a passed
original manifest or a successful formal candidate handoff.

After the user's further `继续` at 21:50, the parent performed independent
read-only checks without rerunning the blocked command or starting a model.
An actual-hash inventory covered 33 files / 970608 bytes; the separate
`controller-review8-collection/manifest.controller-collected.json` still records
needs_input. The original manifest was separately retained with its hash intact.
No runtime/accepted base was promoted, and no publication occurred.

The V2 PPTX `exports/acceptance-public-sample_20261021_131243_native_charts_tables.pptx`
is 43274 bytes, SHA-256
`1819eb54fe25b1d760f4318da656425fb39a733432bba538b991bb1d58d770ed`.
The local `.runtime/recovered-options/acceptance-sample-review-v2.pptx` was
presented as a manual-review version. Only SVG03/05 and PNG03/05 changed; the
P1/2/4/6 SVG bytes and corresponding PPTX slide XML were exact matches.

The local `revision8-independent-pptx-check.json` actually read both decks and
verified six pages, 16:9, text changes only on P3/P5, the unchanged P4 native
column/clustered chart cache and embedded XLSX values Q1/Q2/Q3, Cloud [12,15,19],
Services [8,9,11], and the original P3 image SHA, full 4:1 image without crop,
and bounds inside the slide. There are no notes, animations or transitions.
Remote `final-review-check.json` independently used inspect_pptx and reported
chart data verified true. A no-link snapshot and restricted network-disabled
scratch checks returned attribution_guard exit 0 and static final checker 6/6
passed, zero warnings/errors. None of these checks changes raw actor status or
substitutes for final rendering or manual PowerPoint acceptance.

Usage closed at 29 starts / 29 finishes, with no unpaired/duplicate records:
26 completed / 1 provider_failed / 2 proxy_shutdown. Observed input/output tokens
were 2960027/24312, with no provider-cost entries; cost remains unknown, not zero.
All attempt 4/5/6/7/8 originals and all images remain retained.

The actual project-marker pair was `['.', 'revisions/round-08/base']`.
The preview.py multiple-root false positive was repaired using direct-child-only
selection while retaining whole-tree safety scanning and parent-marker rejection.
Fifteen focused Windows tests yielded 12 passed / 3 skipped. The subsequent
preview5 build and Linux verification have now completed; final evidence appears
below. COMMON now requires indexing every unchanged page and retaining
all official checks. Independent package comparison belongs to the trusted
publisher; approval rules remain unchanged.

Website/API/DB are not deployed. The user has not supplied the PowerPoint version
or actual open/edit-data/save/reopen evidence and real page feedback. V2 remains
for manual review, not an accepted base. This documentation-only update did not
run tests, modify source, call models or update MCP.

## Preview5 final build and nested-marker verification (2026-10-21)

- Latest local image: `ppt-service-worker:arm64-c350bc31-preview5`.
- Image ID: `sha256:6e9bdb213f75120171d712e9bd037759cedfce6aced2caff0dae01d5fa08583a`.
- Build root: `/root/ppt-service-preview5-20261021`; base/vendor unchanged.
- UID 10000/aarch64, Chromium 141 and Noto smoke passed.
- All 169 tests passed in 9.145 seconds with no skips in the actual image on
  ARM64: networking disabled, read-only root, drop ALL, no-new-privileges,
  tmpfs/resource limits, tests/scripts read-only, no credentials or Docker socket.
  These are supplied verification results; this documentation task ran no tests.

preview.py selects only the current project root's direct-child marker. It
rejects workspace parent markers and multiple siblings, while nested backups
remain subject to whole-tree bounds/link/hardlink/special-file safety scanning.
The actual V2 read-only copy contained `['.', 'revisions/round-08/base']`.
Pinned preview5 ran the real foreground preview, bound health to the current
project/PID/six slides, and actually stopped its process group afterward.
Model requests were zero and pngs_rendered=false. The report is
`controller-preview5-nested-health/probe-report.json` under the controller run
root. The original R8 project and actor manifest remain needs_input, with no
promotion, push, deployment or model rerun. R8's historical preview4 image record
is unchanged.

COMMON now requires complete hash declarations for all references, including
unchanged pages. Independent package comparison belongs to the trusted publisher;
official checks and approval stop boundaries remain intact. Approval was neither
bypassed nor disabled, and an additional temporary Python comparison is not a
prerequisite for producing the full index.

The controller's completed index and independent checks are recorded in
`controller-review8-collection/final-review-check.json` under the run root:
33 files / 970608 bytes, status still needs_input. A separate inspect_pptx result
verifies six pages / one native chart on slide 4, with cache and embedded workbook
data verified true. Actual attribution_guard exited 0 and static final checker
passed 6/6 with zero warnings/errors. The raw actor records are unchanged. The
V2 deck is still 43274 bytes and remains a manual-review version, not an accepted
base or publication.

The user's 21:50 `继续` authorized ongoing read-only verification and organization,
not a PowerPoint acceptance statement. Bringing website development/testing
forward before manual acceptance has not been explicitly authorized. The current
rule still requires manual acceptance before full business implementation;
PowerPoint acceptance remains a gate before formal publication or invitations.
No specification change or business-implementation permission is implied.
Website/API/DB are not deployed, and all originals/images remain retained.
This documentation update performed no MCP writes, source changes, tests or
model requests.
