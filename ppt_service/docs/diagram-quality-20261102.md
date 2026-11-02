# Diagram routing checks and the Profile/Bundle repair

The reported third page draws two top-row lines through the lower peer cards.
The source SVG and delivered-PPTX render share that defect. The approved facts
are four peer profiles (`web`, `headless`, `sdk`, `acp`) loading `dsh-base`, and
the independent `sdk-minimal` exception. A containing shared group communicates
this without suggesting dependencies between the profiles.

## Independent geometry contract

`control/diagram_geometry.py` reads actual SVG XML without changing it. Mark
rectangular nodes with a stable `id` and `data-pptx-role="node"`, and line or
polyline edges with `data-pptx-role="connector"`. Optional `data-pptx-source`
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
