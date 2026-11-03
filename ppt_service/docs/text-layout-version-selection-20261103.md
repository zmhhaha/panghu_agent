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

The final release must retain separate evidence for the complete final source
regression, the actual exporter/Office positive and negative samples, the known
bad ten-page deck, exact image builds and runtime activation. Those results are
recorded below as they complete; this document does not imply deployment.

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
