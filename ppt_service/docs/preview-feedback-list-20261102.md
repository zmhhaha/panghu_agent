# One preview and a revision feedback list

The preview workspace now shows one slide viewer with one page navigator. It
prefers the protected, receipt-bound final PPTX render. If that render is not yet
available, it labels the source PNG as a reference preview. It never silently
substitutes a source image for a final-output image with an invalid URL.

Revision requests use an ordered, editable feedback list. Each row has one
opinion and an optional page target; adding a row focuses its text field. Rows
can be removed without losing adjacent opinions. Empty rows are ignored. A
single submission preserves every populated opinion, row order and explicit
page labels. Page-only feedback uses the union of those pages; mixed global and
page feedback uses whole-presentation scope while keeping the individual page
labels in the instruction text. Existing consent and exact selected-version,
source binding and record revision guards remain enforced.

Technical checks, history, desktop checks and publication are collapsed under
`验收与发布`. Per-page verdict and issue forms have been replaced by one explicit
whole-presentation visual declaration and a separate save action. This records
the existing bound source page roster. Viewing previews and editing feedback
do not save human acceptance. Actual desktop open/edit/save/reopen and owner
publication remain explicit, separate actions.

## Linux verification and website publication

- Implementation: `e7c37bfbed2ecb4555ed31095e22ae8e81e4c243`.
- Tested and image source: `cba57ad85df0f793ccb76d731a332b19d980ac22`.
- Linux-only checks: 4 site build contracts and 99 frontend regressions passed,
  with no failures, errors or skips. Production allowlist TypeScript/Vite build
  passed before adding test-only inputs.
- Check receipt:
  `/root/ppt-feedback-cba57ad85df0-7eccee053d-checks/receipt.json`.
- Git checkout:
  `/root/ppt-model-failure-20261031/panghu_agent`.
- Website image:
  `arm-cluster-master:5000/ppt-service-site:latest@sha256:9bd43893248bc18ee60e5dee693a9576d17e45beaa353a7787f1fecf3d473849`.
- Publication receipt:
  `/root/ppt-feedback-site-cba57ad85df0-9cae3ff3d5-release/release.json`.

The build used Git-fetched source and the standard allowlisted site builder.
Publication acquired the deployment lock and changed only the website image
and source annotation, using resource-version and UID guards. The exact website
generation became Ready. PPT controller spec, configuration, secret references
and PVC identities were preserved; the owned lock was released. No shared
agent-service, Worker, production database or user-task changes were made.

Earlier operator attempts are retained: two stopped while copying read-only
temporary test inputs, before running frontend tests. A third ran 99 frontend
tests with one incorrect hidden-element assertion; it was corrected to test
visibility, retaining the closed-details assertion. All temporary resources
were cleaned up. No local tests, build, rendering or PowerPoint execution ran.

## Authenticated website check

The existing DSH project `c9d480b5-be18-46ed-839b-e65a9bd57eda` and review Version
`7ee06cd9-ea01-40a1-8048-532f6ef71434` were inspected after publication. The
single final-PPTX page 3 image loaded at 1280 by 720. The formal section was
closed. Two temporary feedback rows exercised whole-presentation and page-3
targets; submit became enabled. Deleting the second row and clearing the first
restored one empty row and disabled submission. The temporary text was never
submitted; no model task, human review or publication decision was created.

Screenshot:
`.research-temp/ppt-feedback-list-live-20261102.jpg` in the outer workspace.
Parallel five-slot shared-control work was coordinated through
`.research-temp/platform-slots-coordination-ppt.json`; this release never reads
`agent_slot` or assumes a one-row slot table. Future runtime operators must
check all slots and allocations while preserving the global fence.
