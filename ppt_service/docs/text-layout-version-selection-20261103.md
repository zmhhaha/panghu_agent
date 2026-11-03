# Native text layout and version selection — 2026-11-03

## Reported defect and preserved baseline

The ordinary-user review described split identifiers, card title/body collisions
and labels escaping containers on pages 2, 5, 6, 8 and 9 of the ten-page DSH deck.
The retained baseline is project `c9d480b5-be18-46ed-839b-e65a9bd57eda`, Version
`7ee06cd9-ea01-40a1-8048-532f6ef71434`, Job
`db076cff-ada0-484b-9848-4caa0704c362`. Its PPTX has 53,781 bytes and SHA256
`fc620aa89f92e85486cfbd05fd0a3751247c315b14870af24dede769c7522d79`.
The prior revision changed page 3 only; that did not repair the other pages.

The pinned native exporter uses the text object's own frame. Increasing a
background rectangle does not increase the editable text frame. Default
`preserve` output can therefore retain a narrow or undersized text frame even
when the visible card has ample room. The new authoring guidance uses explicit
native frames and independent areas for identifiers, titles and descriptions.
It retains editable text and the existing exporter, rather than rasterizing it.

## Implementation and evidence boundaries

Application commit `1fb81b6cf9cb24ccba80763fcadac3cbb3c14321` adds native text
guidance, exact final-PPTX/PDF text measurement, version-bound diagnostics and
newest-first immutable version selection. Old v1 success receipts remain readable
and immutable. New v2 rendering receipts bind their actual glyph/frame inspection.

The asynchronous acceptance controller also retains a separate measured failure
bundle after proving the validator stopped. Its owner API projects fixed rule
messages and page numbers only. It does not rewrite the completed generation job,
Version, human review or publication records, and it does not run Office twice.
The preview shows these issues outside the collapsed formal acceptance controls.

The first server-only baseline regression completed 30 stages and 1,175 tests
with zero skips, failures or errors. Receipt:
`/root/ppt-prepare-task-1fb81b6cf9cb-089edd3113-checks/summary.private.json`.
This baseline does not yet establish the two added actual-text/native-export
stages or the subsequent failure-diagnostics extension.

## Verified final source and website rollout

Website/controller/validator source: `7d361e82eab12c5ff7915f2693550413cba10c86`.
All 32 server-only stages and 1,269 tests passed with zero skips, failures or
errors. The selectors include the actual native exporter/Office tests, the
rendered-text policy, asynchronous failure transport, owner API, strict browser
response contract, preview interactions, version selection and feedback list.
Receipt: `/root/ppt-prepare-task-7d361e82eab1-1a2bb5c4d2-checks/summary.private.json`.

The server fetched the pushed source through Git and built all images as
`latest`, retaining revision/context labels and exact registry digests. Website,
controller and validator build receipt:
`/root/ppt-diagram-7d361e82eab1-build-20261103T063851Z-97e5ca6b/receipt.json`.
Initial Worker-only build receipt (superseded before activation):
`/root/ppt-authoring-worker-7d361e82eab1-build-20261103T063851Z-1041f425/receipt.json`.
The latter verifies the image-owned `ppt.py` and `ppt_text_layout.py` imports and
their exact Git-source hashes, without rebuilding shared Agent control.

Website/controller/validator deployment completed with exact image/source
checks, both Deployments Ready and the original Office/PyMuPDF dependency prefix
preserved. Public ping returns 200, root redirects to authentication, and owner
endpoints return 401 without authentication. The idle/read-only admission lock
and deployment lock were released. No migration or business-row write occurred;
auth, database/PVC, quotas, RBAC/resources and shared Agent configuration were
preserved. Release receipt:
`/root/ppt-diagram-site-controller-validator-7d361e82eab1-20261103T064029Z-8024d03c/release-receipt.json`.

Immutable image references (all prefixed `arm-cluster-master:5000/`):

| Role | Repository and retained latest digest |
| --- | --- |
| Website | `ppt-service-site:latest@sha256:3641bdf243ee2fdc103fcedd429d920f2d077fe4d2736ce68664f208de71e5a9` |
| Controller | `ppt-agent-controller:latest@sha256:345338fa565e288b06063665a01ca2979ac4a6aa8f52cd1068b37d17e5b6a492` |
| Validator | `ppt-agent-validator:latest@sha256:4492a878db0d575f20542379a61986500a1c5263b5ecc5460026a87937549bc9` |
| Pi Worker, active | `agent-service-pi:latest@sha256:8a3e77253c7f2f75c998b22adef7e0151ca7e2797ac5d341b782e8426dd75a21` |

The active Worker uses source `6bc5624b9084e25521488ebeceb28ec35789cddb`.
Its own five isolated runtime prerequisite probes passed, with physical cleanup
and dispatcher restoration. Activation is verified below. These prerequisites
are not a normal ten-page business revision or human acceptance.

### Shared-catalog compatibility checkpoint

The five probes for Worker `33cb5a55...` completed successfully, including
physical resource cleanup and restoration of the original dispatcher replicas.
Evidence is retained at
`/root/agent-service-deploy-20261026/ppt-text-layout-runtime-7d361e8-r1`.
The first publication plan stopped before creating its public snapshot: the
shared live catalog contains the already released `video.motion-plan.v1` and
`video.explanation-plan.v1` contracts, which this PPT branch's older strict
registry could not parse. `_Public` accepted the public configuration, while
Registry rejected `output_schema` with `literal_error`. No Worker binding was
published. The guarded maintenance operator restored the dispatcher and its
rollout was observed Ready; the failed plan directory remains retained.

The repair inherits the two existing reviewed video implementations, their fixed
resource bundles and build allowlists from Git. It preserves the current PPT
text-layout/handoff changes and shared execution-slot support. Catalog generation
and existing video routes are unchanged. The repaired Worker passed its own
server regression, full live-catalog/host parsing and five probes before
activation. The earlier five probes remain evidence for the earlier image only.

### Worker activation and final verification

Worker source `6bc5624b9084e25521488ebeceb28ec35789cddb` was committed/pushed and
fetched through Git. All 32 server stages passed: 1,273 tests, zero skips,
failures or errors. Receipt:
`/root/ppt-prepare-task-6bc5624b9084-830f732373-checks/summary.private.json`.
A separate server-only offline run of `tests/agent_service/test_build.py` passed
all 64 tests, including resource packaging and tamper rejection.

The Worker-only build retained both fixed donor images and the new text guidance:
`/root/ppt-authoring-worker-6bc5624b9084-build-20261103T073159Z-cb8c2cd4/receipt.json`.
The current complete catalog and all 90 existing immutable host configurations
were strictly parsed by both the Git checkout and the exact candidate image.
Every policy pin matched; 71 image-owned source files and all ten new bundle
files matched their Git hashes. Read-only receipt:
`/root/ppt-current-catalog-compatibility-6bc5624-r1.json`.
The initial diagnostic probe's source-identity failure came from the legitimate
`agent_service.deploy` namespace package having no `__file__`; the corrected
probe verifies its exact Git directory, loader, search path and permissions.
Its unsuccessful result is not used as passing evidence.

Five probes for the final Worker and their complete physical cleanup are under
`/root/agent-service-deploy-20261026/ppt-text-layout-runtime-6bc5624-r1`.
The separately inspected publication plan changed only the three control
ConfigMap pointers, added five immutable PPT hosts/bindings and permanently
reserved UID range `[47120384, 47185920)`. The control images and their source
annotations, shared five slots, non-PPT catalog, and historical hosts were
preserved. All three controls are Ready and the dispatcher was restored.
Final verification receipt:
`/root/agent-service-deploy-20261026/ppt-text-layout-publication-6bc5624-r1/final-runtime.json`.
New catalog: `agent-service-config-f78661ea19a3a6be3c57`.

Public observations after activation remain ping 200, root 302 and anonymous
owner/project API 401. Receipt:
`/root/ppt-text-layout-live-closeout-6bc5624-r1.json`.
No business database write, user impersonation or owner-deck revision was used.
The live catalog now retains 95 binding/pool entries under the existing bound of
100; plan a bounded historical-catalog lifecycle before further additions would
exceed that limit. No historical entry was pruned in this release.

## Actual exporter and final rendering samples

Server evidence for application `48dc05e929252af5d4469c832e086e261342deb5`:
`/root/ppt-text-layout-20261103/native-evidence-48dc05e92925-20261103T054004Z-93e368`.
It used the retained validator digest
`sha256:f061cd9ca3539269071fa6c2df4c5de7a5001c28253fcc627566a64b42a293bb`,
its actual fixed native exporter, LibreOffice and PyMuPDF 1.26.7, without a
network, model, database or production credential. All three cases completed:

- The exact reported ten-page PPTX failed the new text-layout gate. All 148
  text objects matched actual final-PDF glyphs, with zero unmatched or limited
  objects. It found 57 rule issues, including each reported page 2/5/6/8/9.
  All ten final-page PNGs and the bound report are retained. Multiple issues may
  describe the same visible defect; this is not 57 independent user complaints.
- Three pages exported from the corrected explicit-frame fixture passed with
  33 editable text objects, zero layout issues and all five test identifiers
  intact on one actual rendered line. No text was rasterized.
- Three pages with a deliberately undersized identifier frame were rejected
  for text extending beyond that frame. They retain their own final PDF/PNGs.

The positive page and the original page 5 were also visually inspected as
server-produced PNGs. The original card labels wrap over their descriptions;
the corrected bilingual fixture gives identifiers and body text separate space.
These are actual offline exporter/renderer observations, not an owner-project
revision, full workflow equivalence evidence or desktop PowerPoint acceptance.

The subsequent 32-stage run executed 1,269 tests with zero skips and one failure:
an older App test expected a legacy Version gate label after the UI had correctly
switched to the authoritative acceptance snapshot. That assertion is updated to
wait for and verify the actual pending programmatic state. This failed run is
retained at `/root/ppt-prepare-task-48dc05e92925-00d887d952-checks`; it is not a
passing release receipt. All other stages, including actual native export,
asynchronous transport, owner API and diagnostic client/panel tests, passed.

## Remaining owner-deck verification

Prepared feedback retains the ten-page scope, theme and repaired page-3
relationships. It requests fixes on pages 2/5/6/8/9 and a clearly labelled
README-reading explanation based on the original source. Configuration examples
must remain illustrative, with whole replacement rather than invented deep-merge
semantics. No extra source facts, default values or execution results are claimed.

The normal authenticated owner revision and all ten final-page inspection remain
pending. Browser automation currently fails before initialization with
`failed to write kernel assets` / Windows error 3. No browser cookies or private
credentials were extracted and no privileged business-row impersonation was used.
A synthetic sample or runtime preflight is not this owner-project revision.

Microsoft PowerPoint edit/save/reopen, owner visual acceptance and explicit
publication remain separate pending human gates. Usage/eligibility work remains
deferred. All tests, builds, actual exporting and rendering run on the server;
source reaches it through Git. The shared Agent five-slot configuration and PPT's
separate one-business-slot constraint are preserved.
