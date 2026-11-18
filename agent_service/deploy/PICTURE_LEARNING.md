# Direct-reference picture roles

`video.picture-learning-author.v1` and `video.picture-learning-review.v1` are new general Agent/Pi identities. Both require text, tool calls and vision. They do not reuse acceptance from animation-method v2. The existing historical contracts and tool extensions are unchanged.

Clean Agent control and Pi builds include the new profile, pinned `picture-learning-v1` resources and `picture_learning_extension.mjs`. The extension imports the independently pinned common image reader. New image tags remain `latest`; deploy exact digests and record source identity in labels/annotations.

## Preparation

Use `measure_picture_learning_bundle()` and `prepare_picture_learning_catalog(...)` from `agent_service.deploy.picture_learning_catalog` with the current catalog, a clean Pi `latest@sha256:...` image, the measured resource manifests, a confinement donor pool and vision-capable author/review model policies. The helper returns an additive catalog, two exact candidate hosts and a receipt. Routes and pools are disabled, UIDs unreserved, model requests zero, and no credentials are created. A distinct `video-picture-learning` caller scopes these two profiles; do not silently widen the existing method caller or borrow an accepted host.

The tool environment is `tools-pi-picture-learning`; its content identity binds both the image reader and the progressive reader. Tools are exactly `read_inputs`, `skill_search`, `skill_read`, `write`. The first tool returns original reference/output image bytes, task data and a knowledge catalogue. Page bodies only appear through bounded search/read against the same frozen snapshot. `knowledge_binding.use_wikiskill=false` requires no knowledge snapshot; the tools cannot fetch a mutable working Wiki. Read receipts describe delivery, not understanding or successful visual application.

Input schema is `video.picture-learning-input.v1`, filename `picture-learning-input.json`. Result schema is `video.picture-learning-result.v1`, filename `picture-learning-result.json`. Refer to the pinned resource CONTRACT.md for complete author output. Source files use the shared scene.html/js/css map; the stopped collector computes source_sha256 from real source bytes. Still constraints are width/height without a fake external video duration. The author output allowance is 32768 model tokens; review remains 8192. Existing roles retain their original allowances.

## Actual candidate admission

Use the existing candidate-scenario/probe operator with a frozen picture scenario, original PNG/JPEG inputs and the exact candidate host. Initial author admission must actually deliver a reference image, produce checked source and render a PNG through `PictureLearningRenderer`. Reviewer admission must deliver the reference and that actual rendered output. Keep original root-owned candidate exports and renderer capture directories. Do not edit candidate-only flags, synthesize delivery records or equate subjective scores with technical admission.

The new independent capture verifier is executable on the server:

```text
python -m agent_service.deploy.picture_learning_acceptance \
  --candidate-dir <original-candidate-capture> \
  --source <clean-source-root> \
  --renderer-dir <original-still-render-directory> \
  --renderer-image <renderer:latest@sha256:digest> \
  --output <new-admitted-host-directory>
```

For the reviewer omit renderer-dir and renderer-image. The verifier checks exact protocol/bundle/tools/model pins, signed handoff custody, completed model-proxy image-byte delivery for **both** roles, isolation, fresh UID reservation, actual stopped worker/proxy and lease expiry rejection. For the author it independently rechecks source and runtime bytes, actual decoded PNG dimensions, real HyperFrames seek/project receipt, repeat/reload hashes and stopped renderer ownership. It performs no Agent/model calls, rendering or deployment, and creates a technically admitted host that remains disabled. Artistic quality, broad transfer, movement/audio and developer approval remain outside this admission.

After actual checks pass, apply the usual Agent catalog/operator workflow to install the produced host configs/acceptance documents, allocate permanent fresh UIDs, enable only these two accepted routes/pools and mount the dedicated caller credential into the private creation executor. Preserve old routes, acceptances and historical records. Point its `PictureAuthorGateway` and `PictureReviewGateway` at the admitted general Agent endpoint; web sessions never receive that credential or Docker access. Verify capabilities and a bounded real picture-learning job through the public API. Manual WikiSkill publication is a separate developer action.
