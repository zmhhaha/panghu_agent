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

Final tested application commit: `7d361e82eab12c5ff7915f2693550413cba10c86`.
All 32 server-only stages and 1,269 tests passed with zero skips, failures or
errors. The selectors include the actual native exporter/Office tests, the
rendered-text policy, asynchronous failure transport, owner API, strict browser
response contract, preview interactions, version selection and feedback list.
Receipt: `/root/ppt-prepare-task-7d361e82eab1-1a2bb5c4d2-checks/summary.private.json`.

The server fetched the pushed source through Git and built all images as
`latest`, retaining revision/context labels and exact registry digests. Website,
controller and validator build receipt:
`/root/ppt-diagram-7d361e82eab1-build-20261103T063851Z-97e5ca6b/receipt.json`.
Worker-only build receipt:
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
| Pi Worker, built; activation pending | `agent-service-pi:latest@sha256:33cb5a55dbd90553d7026abeb453569ae6b5b6f95c09ffb9aef69cc58bd6989e` |

The Worker candidate is undergoing five isolated runtime prerequisite probes.
Its shared-catalog activation is recorded separately after completion. These
prerequisites are not a normal ten-page business revision or human acceptance.

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
and existing video routes are unchanged. A new Worker must pass server regression,
full live-catalog/host parsing and five probes for its own exact image before
publication. The earlier five probes remain evidence for the earlier image only.

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
