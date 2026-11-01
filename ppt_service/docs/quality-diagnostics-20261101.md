# PPT quality failure diagnosis — 2026-11-01

## Confirmed incident

The second DSH production job, `43133abb-2e94-4560-860a-2c1504f8ed42`,
failed with `quality_gate_failed` before independent rendering. The UI's
“检查与渲染” label covers validation generally; it does not establish that a
renderer ran. The retained independent report identifies
`external_svg_resource`, with `accepted=false` and
`publication_authorized=false`.

The offending references are two ordinary HTTPS navigation links in SVG
namespace `<a>` elements on `svg_output/10_references.svg`, pointing to the
official DeepSeek Harness repository and its architecture document. The old
source-binding check treated every `href` containing a colon as an external
loaded resource. The links therefore failed before the later rendering and
delivery checks could run. This evidence does not implicate user requirements,
the approved plan, model availability or a rendering timeout.

The exact frozen result manifest is
`deacbab09cb074379ea21d507fd622ee4b27f2af1b2df5f3ff56c7d03769d2f7`.
All 80 declared artifacts match their measured sizes and hashes. The retained
PPTX is 53,722 bytes, has a clean ZIP CRC, ten slides and ten notes. The frozen
candidate also has ten preview PNGs. The PPTX SHA-256 is
`fc87f384ad0b2270523a539b63f7157b65f48ba11020b5915c71403f3ad5aa5f`.
These structural observations alone do not constitute independent acceptance
or an owner-reviewed deliverable. The historical project has no Version from
this failed job.

The controller-owned request's approved-spec hashes match the actual candidate:

- `design_spec.md`: `820a2084c40fc58903ebe404f8967deb0746bdbaceb2a487d5ef91710129ad4f`.
- `spec_lock.md`: `6e7b04ffd49ddd742ddfee5e09acbf6e51ddc968d3713892ce5329005871059c`.

## Implementation

Source binding now distinguishes ordinary HTTPS navigation on exact SVG `<a>`
elements from externally loaded resources. SVG2 and exact XLink hrefs work;
userinfo, malformed encodings, invalid ports, whitespace, control characters,
namespace confusion and other active or external resource mechanisms remain
rejected. No candidate SVG or manifest is rewritten by this fix.

After its own sandbox's physical stop, the controller retains rejected
independent output separately from accepted output. Measured files, request,
nonce, source manifest and execution image bind the report. Invalid reports
supply only a generic stopped failure; altered transport or candidate evidence
fails closed, including after a controller restart.

The business projection checks the terminal job, project, attempt, fence,
business reference and input snapshot before exposing an allowlisted code and
fixed explanation. Owners can download a safe JSON diagnostic from
`GET /api/v1/jobs/{id}/diagnostics`. Other owners and deleted projects receive
404. Private logs, filesystem paths, source text and arbitrary model-authored
messages are never returned. Historical failures without bound diagnostics
explicitly direct the owner to administrator investigation instead of telling
them to rewrite their requirements.

## Verification and release evidence

Application implementation commit:
`4dd9c28ecb24013d8dc229f1ff354a509178dd29`.
Linux server checks ran 645 tests across 15 required stages, with zero skips,
failures or errors. No local tests, compilation, builds or preview were run.
The API schema-tampering fixture explicitly marks its JSON field modified:
Python considers `True == 1`, which otherwise prevented SQLAlchemy from
persisting that malformed fixture; the production type check remains strict.

Private server check receipt:
`/root/ppt-prepare-task-4dd9c28ecb24-12972a7537-checks/summary.private.json`.

Three images were built from the Git-fetched source with `latest` tags and
verified immutable registry/config/context identities. The validator uses a
single COPY layer over its measured old immutable base; this avoids the old
image's filesystem layer limit without reinstalling dependencies or changing
its inherited runtime configuration. Build imports run in a separate isolated
read-only container.

Private passing build receipt:
`/root/ppt-quality-4dd9c28ecb24-build-20261101T061629Z-896738e0/receipt.json`.

The unchanged original candidate passed the full image-owned programmatic
validator in an isolated, network-denied diagnostic Pod (exit 0). The report
binding, exact Pod termination, actual image digest, original request and full
candidate inventory were checked. Ten independent PNGs, official gate reports,
delivery and native inspection outputs are retained privately. The diagnostic
Pod and its policy were removed after physical stop. No model call or business
database access occurred.

Private replay receipt:
`/root/ppt-quality-revalidation-20261101T061845Z-cf846b4a/receipt.private.json`.
PVC diagnostic directory:
`state/quality-diagnostic-cf846b4a-b0f1-4879-839c-5bf7da3672cc`.

The successful programmatic result remains `accepted=false`, with
`publication_authorized=false`, and still requires `independent_visual_review`
and `independent_postflight_and_delivery`. It is diagnostic evidence, not a new
business Version or acceptance credential. The failed historical task remains
unchanged. Owner PowerPoint open/edit/save acceptance and OpenSpec apply/archive
remain pending.

## Observed production rollout

The guarded release completed at `2026-11-01T06:21:59Z`. Both deployments are
Ready, actual runtime source copies match the committed build, and the
controller's effective validator image matches the new digest. Deployment and
PostgreSQL admission locks were released. Shared Agent/LLM configuration,
OAuth settings, PVC and RBAC were preserved. The release performed no business
database writes.

- Site: `arm-cluster-master:5000/ppt-service-site:latest@sha256:93ec773a8b7e6711c8a631bfcd9ff65111bde1e8ae3492c0bf6b763acface391`.
- Controller: `arm-cluster-master:5000/ppt-agent-controller:latest@sha256:2dbce878c73a4d2383b93e18ac5ac7ae2f6f96a2ff801e0f56dd464a5aff995c`.
- Validator: `arm-cluster-master:5000/ppt-agent-validator:latest@sha256:e25951c70a07879cc52edc9e371bc756ff2314732cb4785e3e202fc4cb7486cc`.

Release receipt:
`/root/ppt-quality-site-controller-validator-4dd9c28ecb24-20261101T062018Z-44d5cf50/release-receipt.json`.
The unauthenticated public boundary remains `/ping` 200, `/` 302,
`/api/me` and `/api/projects` 401.

The signed-in browser loaded the new diagnostic component. The historical
job still has no bound public diagnostic or Version, as expected: it displays
the explicit administrator-investigation fallback, preserving the old record.

## Normal website production follow-up

After the unchanged frozen candidate passed programmatic validation and the
release completed, the existing signed-in owner browser submitted one normal
“生成演示稿” action. It uses the unchanged source material and already confirmed
A direction (plan revision 4), without editing input or confirming a new plan.
Preparation job: `c7e33bbf-83a3-4302-9699-0c92952db7ef`; remote:
`d3e959d8-6e75-4611-b971-7d9ac4394f0b`.
It was submitted at `2026-11-01T06:25:53.925983Z`, with the normal controller
deadline `2026-11-01T06:55:54.669750Z`. This is a targeted normal production
check after the deterministic repair, not a reconstruction or mutation of the
historical failed task.

Preparation completed successfully through independent validation and the
controller automatically started generation
`f5d0d14e-df23-48ea-b116-4737281abd75`, remote
`cd02aeee-1334-474d-91a2-9cc37873f441`, at
`2026-11-01T06:33:13.387459Z`, with the normal immutable deadline
`2026-11-01T07:03:15.534370Z`.

Generation succeeded and the normal controller created review Version
`b6b02806-506a-414e-a314-8d3ec230f9f2` at
`2026-11-01T06:48:31Z`. Its outbox is mapped and acknowledged (cursor 378),
and the project is `awaiting_review`, revision 26, with no active job. The
formal `latest_version_id` remains null because this is a review draft.

The signed-in production UI now shows “预览与修改 1”, ten pages, the selected
review draft and “下载审核草稿”. The real browser download completed to
`C:\Users\4770_2070super\Downloads\presentation-b6b02806-506a-414e-a314-8d3ec230f9f2.pptx`.
Its measured SHA-256 matches both the business manifest and the actual stored
artifact:
`19d9e26451ebb621f4cfa9452edaa7b5381c2a075d0c26bbf587933937e3207d`.
The server measured 53,959 bytes, clean ZIP CRC, ten slides and ten notes.
This resolves the zero-preview/no-download incident through the normal product
workflow. At that checkpoint visual and desktop PowerPoint checks were
unsubmitted, and the version remained `review/gate_pending`. The owner's later
review below updates the human visual status; no formal publication is claimed.

Private production evidence:
`/root/ppt-quality-version-b6b02806-20261101.private.json` and
`/root/ppt-quality-version-b6b02806-artifact-check-20261101.json`.

## Owner post-delivery review

The owner supplied `PPT服务-修复后产物核查-2026-11-01.md` and the renamed
`DSH架构解析（网站审核稿）.pptx` from the existing output directory. A read-only
SHA-256 measurement of that file matches the delivered artifact above. No new
generation, revision or desktop test was initiated for this follow-up.

The supplied audit records ten native slides in 16:9, 220 native shapes,
147 shapes with nonempty native text, no picture nodes or whole-slide images,
and ten Chinese speaker notes. All ten 1280×720 SVG previews were inspected;
text, arrows, diagram relationships and page numbers were readable, with no
obvious clipping or overlap. Key source facts and the two separate reference
links were checked. These are owner-reported structural/content/visual checks,
not evidence of desktop PowerPoint editing.

The report records visual revision 1 saved at `2026-11-01T15:10:04+08:00`,
with all ten pages passed, and read back after refresh and version reselection.
Desktop revision 2, saved at `2026-11-01T15:11:06+08:00`, leaves actual opening,
content editing, saving and reopening unchecked. The explicit zero-native-chart
“not applicable” selection only applies to chart editing.

The supplied desktop attempt failed during PowerPoint COM activation with
`0x80070520` (specified logon session does not exist). No desktop rendering or
open/edit/save/reopen evidence was produced. This identifies a local validation
environment blocker; it does not establish a defect in the downloaded PPTX.

The current acceptance service records human observations, but complete
programmatic acceptance and formal publication are still pending. Even a
completed desktop record alone cannot promote this draft: the independent full
postflight evidence and authorized publication path remain to be implemented.
The immutable earlier validator receipt still lists its original pending gates;
the later saved human visual record is separate evidence. The draft remains
`review/gate_pending`, with no claimed formal acceptance or OpenSpec archive.

Follow-up priorities from the supplied audit:

1. Complete independent final-PPTX/export-build/postflight evidence and connect
   version-bound acceptance to an authorized formal-publication operation.
2. Add final-PPTX rendering with the renderer/environment identified, alongside
   the existing source-SVG previews.
3. Distinguish editable arrow shapes from semantic connectors that follow moved
   modules; the current deck has editable shapes, not `p:cxnSp` connectors.
4. In a future content revision, clarify “plugin id” as plugin configuration-row
   id where patch targeting is described.
5. Verify failed-diagnostic download and targeted recovery with dedicated fault
   cases. This successful deck alone does not verify every failure path.
