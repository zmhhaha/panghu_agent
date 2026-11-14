# Manim Community resource integration

Status: **prepared, not admitted**. The existing source-only film author v1,
three browser engine allowlists, released Skills and deployed service are
unchanged. The capability status is available through
`manim_resource_admission.capability_status()`; importing these helpers does
not advertise a new live drawing engine.

## Implemented boundary

`manim_resource.py` defines a bounded Python recipe request, an exact RGBA PNG
sequence manifest, independent PNG decoding/hash validation and a collector for
the stopped job's exact deliverable tree. It binds the original Python source,
request, seed, Manim version, Cairo renderer, admitted runtime image digest,
dimensions, frame interval and every output byte. Missing/extra/wrong-size
frames, path links, changed manifests and movie outputs are rejected.

`manim_resource_worker.py` is a CLI to execute **inside** a separately admitted
isolated author/render runtime. It does not run at service import or startup.
It uses the pinned Manim 0.22.1 `format='png-sequence'` API; `png` means one last
frame in this version. It imports the unchanged Agent Python, instantiates its
declared Scene, invokes Cairo rendering and freezes exact frames. It never pads
or resamples a recipe that produces the wrong number of frames. A bounded PNG
writer expands Cairo's explicit `repeat=N` for a frozen wait into N frame files:
upstream 0.22.1's PNG branch otherwise emits only one image for that renderer
request. This preserves authored timing; oversized repeat counts fail before
any partial repeat is written. Compressed byte bounds are checked as frames
are written as well as during independent collection.

`motion_renderer/manim_frame_layer.mjs` consumes an already verified manifest.
The original Agent JS module calls `createManimFrameLayer()` during mount,
awaits `layer.renderAt(context)` and disposes it normally. Each integer global
frame maps directly to one PNG; reverse, repeated and arbitrary seeks do not
advance an independent clock. Decode completes before `renderAt` returns.
Outside its half-open interval the image is hidden, with no implicit last-frame
hold. There is one image element and no full-sequence preloading. Source files
continue to describe any surrounding vector objects and boundary transition.

The skill in `skills/manim-community-resource/SKILL.md` teaches reusable
expression decisions and the actual authoring/consumption constraints. It
contains no copied video, external reference dependency or fixed drawing
recipe, and has not been automatically published into the effect library.

## Resource limits

The resource is 24 fps, at most 240 frames (10 seconds), 1280×720, 24 MiB total
compressed bytes, 4 MiB per PNG and 128 MiB decoded RGBA bytes. The last limit
is `width * height * frame_count * 4`; compression does not relax it. At 720p
this admits 36 frames, while smaller resources can use longer intervals. Whole
film limits apply in addition and must also bound **aggregate decoded bytes**
across all declared resources. Do not blindly raise film file/asset limits to
accept the maximum sequence size.

Only geometry and Pango Text are promised by the baseline dependency image;
TeX Live is not installed and LaTeX must not be advertised. There is no Manim
audio output. Native objects remain preferable when vector editability,
long-duration storage cost or cross-module object continuity matters.

## Runtime and collector handoff

Build the complete repository normally with
`motion_renderer/Dockerfile.manim-resource`, a reviewed Python >=3.11 base
digest and `SOURCE_REVISION`; publish only the `latest` tag. This candidate
Dockerfile adds CPU Cairo/Pango/Noto and pinned Manim 0.22.1, not a new public
service. ARM execution and actual output have not been proved by unit tests.
The current prepared code has not built or pushed this image.

In the existing isolated process/Job tool system, a future admitted resource
author can call:

```text
python -m video_workbench.manim_resource_worker --request /work/recipe.json --output /work/attempt-unique
```

The trusted runtime supplies `MANIM_RESOURCE_ISOLATED_WORKER=1` and the measured
`MANIM_RESOURCE_IMAGE_DIGEST=sha256:...`. These flags are guardrails, **not**
independent proof of isolation. The host must provide existing process/Pod
identity and StopProof, no-network/read-only runtime policy, CPU/memory/PID,
wall-time and workspace storage bounds. Never run Agent Python in the API,
controller or a developer host merely because these variables can be set.

The output root contains preserved source, request and intermediate diagnostics.
Only its `resource/` subtree is deliverable. After the existing runtime confirms
all writers stopped, the trusted collector calls:

```python
files = collect_resource_files(
    stopped_output / 'resource', frozen_request,
    expected_manifest_sha256=frozen_manifest_sha256,
    runtime_image_digest=admitted_image_digest,
)
```

The expected request and runtime identity must come from the separately frozen
job plan, not from authored output. A manifest hash must be collected only
after StopProof. Subsequent reads bind it as an immutable artifact. Frame and
manifest bytes can then enter an explicitly versioned generated-asset envelope.

## Admission requirements

1. A separate Python recipe author/resource contract and admitted isolated
   runtime; film-module-v1 remains source-only with no bash/render permission.
2. Bounded transport and custody of the exact stopped generated resource from
   Agent artifacts to creative module packages and whole-film assembly. Keep
   generated resources separate from the fixed trusted runtime-library list.
3. Bind the adapter and every used PNG/manifest to the module's exact asset
   declarations. Serve only compiled project bytes through the existing
   resource server. Recheck total file count, compressed and decoded budgets.
4. Actual CPU/ARM Cairo generation and complete HyperFrames capture, including
   repeated, reverse and boundary seeks and awaited decode. A frame resource is
   not a substitute for runtime admission or developer visual acceptance.
5. Publish a developer-accepted reusable Skill version and then expose Manim as
   an **authoring capability** in the effect library, not a fourth browser engine.

Each requirement is independently evidenced; unit fixtures alone do not grant
runtime admission, subjective quality approval or publication.

## Source-only recipe and bounded handoff now implemented

`video.animation-manim-recipe.v1` is a separately registered implementation
with its own fixed resource bundle. It preserves the source-only tools
read_inputs/skill_search/skill_read/write and host-observed Skill receipts.
`ManimRecipeAgentGateway` reuses the existing authenticated general Agent
transport, exact input upload, uncertain-admission lookup and stopped artifact
collection. Registering the implementation does not enable a route or pool.

`manim_resource_job.py` freezes recipe input/result/StopProof digests and
owner/effect/experiment/operation scope into a fixed no-network Cairo Job plan.
It uses one precise readonly PVC input subPath and one output subPath, no shell
or caller command, no automatic retry and the existing isolated process budget.
The execution controller must still persist intent, reconcile the same named
Job, observe native image identity and delete Job/Pods before collecting a
terminal stopped resource; the pure manifest generator performs none of that.

`manim_generated_assets.py` freezes and decodes exact RGBA resources after typed
StopEvidence. Its transport binds owner, effect, experiment, attempt, original
recipe, resource manifest and runtime image. This is a separate generated
resource envelope, not an extension to the fixed runtime-library allowlist.
The aggregate merge rechecks 128 MiB decoded and 48 MiB film resource limits.

The initial consumer recipe profile limits resources to 60 frames: unchanged
film-module-v1 permits at most 64 used assets, leaving manifest/adapter/runtime
slots. Extra fonts/libraries reduce the remaining slots and are checked again
at merge. The broader resource format's 240-frame limit does not imply a
240-frame resource fits every consumer contract.

For the method lab's current synchronous seek contract,
`manim_method_frames.mjs` provides separate bounded initialization: two parallel
decodes, at most 60 frames and 128 MiB RGBA, a ready promise, synchronous
seekFrame after readiness, and explicit disposal even during loading.

## Effect-library integration

The private method executor now supports an opt-in resource prelude. A
developer can request `resource_authoring` with `kind: "manim"`, width/height,
frame_count, global_start_frame and seed. The server fills the fixed library
version, transparent output and immutable resource ID. A browser never chooses
an image, Python source, Docker command, envelope or storage path.

`ManimMethodCoordinator` durably freezes one recipe input/Skill catalog, saves
its complete upload receipt, claims one source-only Agent submission, then
reconciles uncertain responses through its original operation. After stopped
artifact collection it freezes one Cairo plan. `ManimResourceExecutor` uses
the private executor's existing Docker isolation boundary and its own SQLite
claim; the separate Kubernetes Job generator is available preparation code,
not the initial production execution route. No public web process gets Docker.

The resource prelude has one recipe job and one resource execution. Their
claims and status are visible beside the experiment; they do not consume or
change the main author's existing plan/create/review budget. Main creation
starts after the resource is completed. Cancellation and timeout prevent a
new recipe/resource claim, stopped cancelled results are not adopted, and
recovery never starts another container or author attempt.

The original method-author v2 profile receives the exact manifest, asset
checksums and self-contained usage instructions. Its own composition remains
Agent-authored. A separate resource-aware method request/receipt v5 resolves
immutable owner/effect/experiment/operation references through the trusted
coordinator. It never extends the ordinary fixed runtime-library allowlist.

The v5 host verifies exact resource inventory, PNG geometry, original SHA and
pinned helpers; it records actually loaded helper scripts and PNG paths.
`generated_resource_observation` means loaded resources, not visual acceptance.
The complete HyperFrames render still verifies absolute/reverse/repeated/idle
and fresh-load frame equality. PNGs are layers on that clock, not MP4 clips.

Local actual Chromium plus HyperFrames 0.8.149 validation passed with distinct
RGBA frames, exact half-open disappearance, and all seek/reload comparisons.
Separately, the ARM Cairo 0.22.1 runtime rendered a 48-frame 640×360 technical
fixture with Chinese Pango text and an exact 12-frame wait. Neither is an
Agent-created product sample or a developer aesthetic acceptance.

## Deployment and operator sequence

The independent runtime image is `video-manim-resource:latest@sha256:<digest>`.
Build normal complete Git sources; do not overwrite the browser renderer image
with Cairo packages. The recipe worker uses its own profile, caller/token,
binding, host and non-overlapping UID allocation. Its only tools remain
`read_inputs`, `skill_search`, `skill_read`, `write`. No TeX installation is
included. Preserve all existing film-module-v1 pins, caller and privileges.

Configure the private executor with the existing normal options plus:

```sh
python -m video_workbench.animation_creation_executor \
  --root /data/animation-executor \
  --agent-url http://agent-service.agent-service.svc.cluster.local:8080 \
  --agent-token-file /run/secrets/creation-token \
  --method-agent-token-file /run/secrets/method-token \
  --token-file /run/secrets/executor-token \
  --renderer-image 'registry/video-motion-renderer:latest@sha256:<measured-digest>' \
  --assets-dir /data/animation-assets \
  --method-project-runtime hyperframes-core-bridge \
  --engine-admission-file /data/engine-admission.json \
  --manim-recipe-agent-token-file /run/secrets/manim-recipe-token \
  --manim-resource-image 'registry/video-manim-resource:latest@sha256:<measured-digest>' \
  --manim-resource-admission-file /data/manim-method-admission.json \
  --enable-isolated-rendering
```

Without the admission file, the configured coordinator remains
`prepared_not_admitted` and the public form does not advertise Manim as ready.
Candidate testing is an explicit private Python harness option
`coordinator.candidate = True`, never a browser field or automatic startup job.
First run one separately bounded source-only recipe candidate and verify its
real stopped two-artifact handoff. Then run the Cairo resource and method-v5
composition with the exact pinned runtime. Review source strategy and actual
sample separately from technical proof.

`manim_method_admission.build_admission(...)` binds the exact completed stopped
resource and successful v5 method operation to current host/engine/project/core
and helper identities. Save that output as the admission file. Startup
`validate_admission(...)` recomputes the evidence; a hash-shaped JSON claim,
same-image old host, missing ledger or unverified render cannot enable it.
Published reusable Skills and formal-film resource regeneration remain separate
work. No task-specific PNG bundle is published as a reusable Skill.

Every original method/Skill returned for a Manim experiment must declare the
producer dependency in `runtime_requirements.resource_authoring` as
`{"manim":{"version":"0.22.1","renderer":"cairo","latex_available":false}}`.
The host validates both the method and its original hashed Skill package; it
never fills in missing declarations. Pure technical fixtures without methods
do not acquire invented methods or publication evidence. A continuation or
transfer through the private lab preflights the producer and creates a new
recipe/resource for that experiment. The ordinary formal-video Skill adapter
continues to exclude Manim producer dependencies until its separate resource
regeneration path is admitted.
