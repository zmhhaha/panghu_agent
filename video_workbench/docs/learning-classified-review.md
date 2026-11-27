# Explicit classified reviews in the learning and effect services

New picture-learning, video-learning and effect requests can explicitly select
`review_contract: "video.learning-classified-review.v1"`. The existing request
without this field retains its original protocol. A technical retry copies the
original frozen contract, policy, renderer capability and reviewer route. A new
`continue` or `fresh` learning request must select the new contract again.

The actual assembly is `animation_creation_executor.create_server`. Configure
`--classified-review-agent-token-file` with a dedicated caller credential that
differs from the executor and all author/legacy reviewer credentials. Optional
`--classified-review-agent-url` selects its Agent endpoint; profile revision is
`1`. The factory supplies the same independent
`ClassifiedPictureReviewGateway` to the effect, picture and video services.
Readiness is checked when selecting a request and before uploading a review;
configuration alone does not establish an admitted or usable route. No browser
form silently opts existing tasks into the new contract.

The selected path requires an admitted Canvas artwork API 3 scene capability,
an executable validated scene plan, a 1280×720 viewport and a 24 fps video clock.
It reuses existing world/layer/grid or shared-mesh planning and the existing
compositor rather than introducing another spatial format. The author and
transfer-plan contracts are unchanged. Missing plans or unsupported routes fail
before the corresponding render/review claim, without a legacy-review fallback.

Video observations come from the renderer's original PNG samples at the start,
midpoint and last frame of the half-open rendered range. They do not come from
decoding the resulting MP4 back into JPEG. Still observations retain the
original PNG too. Reference-video decoding remains honestly identified as
reference JPEG observations. Review input is bounded to three reference images,
three output images and two baseline images. Source, plan, render operation,
frame range, capture metadata and retained bytes are checked again on resume.

The capture bound remains 4 MiB per original PNG, while the separate model
transport bound remains 2 MiB per image and the existing aggregate transport
limit. An oversized PNG is retained and rejected before reviewer upload. It is
never resized, recompressed or silently relabeled. A definitive capture failure
after a proven stopped render ends that operation; it does not dispatch another
render or remain an uncertain execution forever.

The frozen reviewer is `video.picture-learning-review.v2`, revision `1`. Its
actual Agent JobRequest receives `model_limits={max_requests:8,
max_output_tokens:8192}`. Readiness must advertise job-wide request accounting
across all attempts and uncertain admissions; this is not a per-attempt reset.
Submission responses and subsequent lookup/poll responses must retain these
exact limits. A control-plane retry reconciles the original operation/job and
does not POST another job. The output-token ceiling is per forwarded provider
request; these settings do not establish a total input/output-token hard cap.
Other author and legacy operations retain their existing budget behavior.

Only completed actual upstream image transport is accepted. The existing
gateway binds its receipt to the stopped job/attempt/fence/request; the consumer
also validates MIME, encoded byte length, request identity and all selected
image hashes against retained bytes. The native v2 opinion, frozen policy,
classification decision, original PNG provenance and transport receipt identity
remain available as evidence. They are not projected into an invented v1 model
opinion.

The existing classified gate requires satisfied required criteria, score at
least 80, no blocking findings and no stop recommendation. Optional advice by
itself does not trigger another authoring round after that gate passes. A
required violation or unresolved required criterion remains blocked even with a
high score. Passed candidates take precedence over blocked candidates when
selecting the best new-contract output; scores rank candidates within that
category. A high-scoring blocked attempt cannot replace an existing passed
project output. An effect stop recommendation ends the attempt with its native
opinion retained and no further author/render claim. The decision covers supplied static images only: it establishes
neither continuous motion/audio verification nor human artistic acceptance,
adoption or WikiSkill publication.

Learning job usage keeps its original accounting semantics, including partial
or unknown measurements. A passed visual gate does not convert those records
into complete provider-usage evidence or grant an exception to another run's
budget/unknown-usage policy. Picture/video evidence retains the original usage
receipt; effect evidence points back to its original Agent job for accounting.

The effect adapter currently accepts scene-native Canvas work without Manim,
resource planning or collaborative authoring. It reviews only the current
output against the frozen goal and criteria and does not invent a baseline
preference. A nonempty effect `reference` dictionary is unavailable on this
path because the existing dictionary supplies no observed image bytes. Its
native v2 review fact can be retained by the existing Skill knowledge flow;
this does not accept or publish the method.

Validation includes the actual factory, an authenticated private HTTP create
request, real gateway preparation/upload/job binding/image receipt checking,
and service gate consumption. Model responses and rendered artwork in these
tests are synthetic. A real FFmpeg reference fixture verifies that source JPEG
decoding and original renderer PNG observations remain distinct. Negative
cases cover missing route limits, changed persisted job limits, acknowledgement
loss with a single job POST, stale route/provenance, lossy or missing captures,
oversized originals, old/new retry isolation and required/advisory behavior.
No new learning model call or production admission is implied by these tests.
