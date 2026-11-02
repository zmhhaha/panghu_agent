# Diagram routing checks and the Profile/Bundle repair

The reported third page draws two top-row lines through the lower peer cards.
The source SVG and delivered-PPTX render share that defect. The approved facts
are four peer profiles (`web`, `headless`, `sdk`, `acp`) loading `dsh-base`, and
the independent `sdk-minimal` exception. A containing shared group communicates
this without suggesting dependencies between the profiles.

## Independent geometry contract

`control/diagram_geometry.py` reads actual SVG XML without changing it. Mark
rectangular nodes with a stable `id` and `data-pptx-diagram="node"`, and line or
polyline edges with `data-pptx-diagram="connector"`. Optional `data-pptx-source`
and `data-pptx-target` refer to those node IDs. Supported marked drawings are
checked for unrelated-card crossings and declared endpoint attachment. Grid
lines, backgrounds and decorative rules must not be marked as diagram edges.

Simple unmarked legacy diagrams receive advisory findings only. The original
third-page routing is a regression fixture. Curves, unsupported effects and
transformations, and exceeded inspection budgets remain explicitly unchecked.
A clean report is not a proof of graph semantics, visual quality, editable
PowerPoint connectors or human acceptance.

The independent source-binding path performs these checks on frozen sources.
Programmatic validation retains `diagram-geometry.json`, including ordinal,
source hash, coverage and fixed diagnostic codes. Strict errors return the safe
`diagram_geometry_failed` explanation. Existing immutable receipts and Versions
are never rewritten. Other quality and publication gates remain required.

## Authoring and rollout boundaries

The PPT profile source adds grouping, routing and metadata guidance. A new
shared Agent runtime/catalog must be separately verified before that prompt
change is called active in production. Updating the PPT independent validator
alone does not update the already pinned Pi worker. Targeted page-3 feedback
can supply the grouping instructions to the existing worker without replacing
shared runtime bindings.

A repaired artifact must be a separate review draft. Other nine source SVGs
and all original files must remain unchanged. Both its source PNG and actual
final-PPTX rendered third page must be inspected, including the full labels
`headless`, `SDK JSON-RPC` and `sdk-minimal`. No prior human pass or publication
authorization carries over. Platform usage work is deferred.

## Verified implementation and release

Git source `5e8474adba49224530119ebf6819f53f20a1cf34` passed all 1,134
Linux-only tests across 29 stages, including 57 diagram fixtures, with no
failures, errors or skips. Receipt:
`/root/ppt-prepare-task-5e8474adba49-b2b15f7c9f-checks/summary.private.json`.
The separate image-proof, revision and launch operators passed 18, 19 and 15
Linux tests respectively. No local tests, builds, renderers or Office ran.

The dedicated `data-pptx-diagram` attribute avoids the official exporter's
closed structural-role vocabulary. An early successful source scan is not
retained as acceptance evidence until the trusted PPTX/source bindings pass;
strict geometry failures retain only their private incomplete diagnostic.

Guarded deployment completed at `2026-11-02T08:32:59Z`, with both Deployments
Ready and admission/deployment locks released. All images use `latest@sha256`:

- Site: `bdfa4fc783fc824920b294a77dc1ace9c343ccd11fb7c3b68555a94098bc85c3`.
- Controller: `d0bdaa83af7bd465218e9a0404b33c590fd7ec60c2422d6a61c4fb8404b058d3`.
- Validator: `f061cd9ca3539269071fa6c2df4c5de7a5001c28253fcc627566a64b42a293bb`.

Release receipt:
`/root/ppt-diagram-site-controller-validator-5e8474adba49-20261102T083108Z-c5a42342/release-receipt.json`.
Acceptance stays enabled. OAuth, shared Agent/LLM, PostgreSQL, PVC, RBAC and
other protected settings remain unchanged; no migration, scaling or model
submission occurred in the release.

The legacy Docker layer-depth limit required replacing the old validator's
source-only final layer over its verified actual 124-layer dependency parent,
not appending another layer. Parent identity, exact layer prefix, runtime
configuration and ten fixed Office/Python/tool provenance hashes were checked
before build and independently again before release. The new validator has
125 layers. No dependencies were reinstalled or flattened. Build receipt:
`/root/ppt-diagram-5e8474adba49-build-20261102T083009Z-d491dbe9/receipt.json`.

This deployment activates the independent geometry checks, not the new prompt
inside the existing shared Pi worker. That worker/catalog remains pinned;
the page-three canary supplies explicit feedback through its ordinary revise
contract. The in-app browser had no active page during this verification,
so a signed-in production-browser walkthrough is not claimed.

## Repaired page-three review draft

One actual Agent revision completed in an isolated read-only-source clone:
business task `37d81eed-f9ba-4ec7-aa3e-d7665cb0d72e`, remote task
`7b3cc890-66f3-4ceb-bc0a-d85e4545d42c`, new review Version
`677d9931-11e0-47a5-9adc-9c07379d1407`. This is a clone canary result, not a
Version inserted into the production user's project. The original Version
`b6b02806-506a-414e-a314-8d3ec230f9f2`, confirmations, timezone, human records
and original PPTX hash remain unchanged.

The revised downloadable PPTX is 53,804 bytes, SHA256
`3b932a6891010ca0a104a46087fd3dba8d0874814f28f580de1c24504ca8592c`.
Only page three's source SVG changed; the other nine are byte-identical. All
ten source previews and ten independent final-PPTX previews passed their
download/hash checks. All seven programmatic gates passed, receipt SHA256
`b8ecad8bf90884a0683e1261cd5d68aca20a0979d6389f9a2797f3782a7c5897`.
There were exactly one model submission and one candidate ACK, with exact
attempt/fence/physical-stop proof. No new human acceptance or publication
record was created.

Both server-produced page-three PNGs were visually inspected: four peer cards
are inside the shared `dsh-base` region, `sdk-minimal` is in the separate amber
exception region, and the four misleading lines are gone. `headless`,
`SDK JSON-RPC` and `sdk-minimal` are complete in both renders, with no observed
card/text overlap. Direct-text `data-pptx-frame` plus
`data-pptx-text-baseline="metrics-v1"` prevents the narrow exported text-box
issue; enlarging only background cards or parent bounds would not do so.
Source-preview PNG SHA256:
`e0b1d3f9d4ecade9cdb258a92d1552eeaf1fdfbfe0d555876e97c6635b18d4ca`;
final-PPTX PNG SHA256:
`697514b6664daf49eb54eec5d6a4a07e148bacc845ef98977a403ca75dd91f77`.
Final-PPTX rendering uses the identified Linux LibreOffice environment and does
not establish actual Microsoft PowerPoint open/edit/save/reopen acceptance.

Private evidence is retained under `/root/ppt-diagram-revision-20261102-r1`.
Owned namespace/PV/PVC, private PostgreSQL container/network and temporary
credentials were removed after physical-stop proof and a verified database
dump. Dump SHA256:
`290d2168ebd1ff568c485ab54dddb0c32803cdbaa001dcab0900966c75ac5068`.
The final `launch-completed.json` supersedes the pre-cleanup revision receipt's
`cleanup_required=true`. There were zero production business-database writes.
Retained PNG/PPTX copies are in the workspace's ignored
`.research-temp/ppt-diagram-delivery-20261102/` directory.

The independent new check replay on the unmodified original SVG had reported
exactly two advisory `svg_diagram_connector_crosses_node` findings. The repair
uses grouping instead of connectors, so a line-free diagram does not claim
that a strict marked-connector case or relationship semantics was proven.
Actual desktop PowerPoint acceptance, explicit owner publication and the wider
unfinished tasks remain pending; no OpenSpec apply/archive was performed.
