# Source-bound native chart release, 2026-11-05

The owner confirmed that the service has not formally launched and does not need
old-task or configuration compatibility. This release directly replaces the five
current PPT v2 routes, removes 45 obsolete PPT binding references and reuses the
existing execution UID ranges. Other services' routes and business records are
unchanged. No business rows were manually rewritten.

Application source was initially fetched on the server from Git commit
`fefd714c7a9face373d08e592f6ceb37a845f1f8`; subsequent controller and review
projection fixes also arrived through Git. Application source archives were not
uploaded. All application builds and checks ran on the server. Each published
image uses the `latest` tag and source labels. Current deployed digests are:

| Image repository at arm-cluster-master:5000 | Manifest SHA256 |
| --- | --- |
| agent-service-control | cf2f61033d5cbd9eb4036452d954afc15fb5d16ff284166163a340db0add5b21 |
| agent-service-pi | 6ad6e332fcd37c7c05337e8208cd4c9ea3cb242d363f76af54299e73ac0c1926 |
| ppt-service-site | 727d8c9a400ce113638de2cd9d506a3d4a6422e0f6ef3ecffffe3cd9682886f4 |
| ppt-agent-controller | 67d69b0267c80b689e44a7a95861387f60048e8fa30b250606eccae664149987 |
| ppt-agent-validator | 86e1a59265c67ef97e2b8f15491c63f5e9ba47cda3079a2992f6530fcdeecab8 |

The shared control and Pi images retain their immutable production donor layers,
runtime configuration and dependencies. Their only source overlay is
`agent_service/profiles/ppt.py`, so newer platform and video code stays intact.
The validator had reached 125 source layers and hit Docker's build depth limit.
Its existing filesystem was cold-exported/imported without starting a container,
preserving its renderer dependencies and runtime configuration before the new
PPT source overlay was built.

The public Agent configuration is now
`agent-service-config-263b27a275f074e5c71a`. The three shared components and the
PPT site/controller all completed their readiness rollouts. The new installed
site module `server/source_charts.py` matches the checked Git source SHA256
`41fbd85a6eedbfd7dc290edf4f6dededbb057ae5bfb663d6a738d04eb06a7ee0`.

Server evidence:

- Focused 165/165 checks, zero failures/errors/skips:
  `/root/ppt-source-chart-regressions-fefd714c-d6836b9e/receipt.json`.
- Shared one-file image overlays:
  `/root/ppt-chart-shared-overlay-fefd714c-d8f357d7/receipt.json`.
- Direct catalog replacement and readiness:
  `/root/ppt-chart-direct-catalog-20261105-r2/rollout-receipt.json`.
- Site/controller image records:
  `/root/ppt-source-chart-images-fefd714c-9ef36e5c/receipt.json`.
  That aggregate receipt also records the first validator build's depth failure;
  its site and controller entries are successful published image records.
- Successful replacement validator build:
  `/root/ppt-source-chart-validator-flat-86668171/receipt.json`.
- Successful PPT site/controller deployment:
  `/root/ppt-source-chart-rollout-20261105T054908Z/receipt.json`.

## Planning failure and controller follow-up

The existing NASA public-data project remains the ordinary website workflow
sample. Planning task `6646108b-a9a8-4cce-b28b-26bd0602ae9c`, submitted through
the authenticated website after deployment, failed because the independent
validator's cold image pull took about 190 seconds and exceeded its 180-second
deadline. The validator later returned `accepted=true`, but that late result was
not adopted. The failed task and its original business records were not rewritten.

An attempted image pre-pull exhausted disk space on three NanoPC nodes. The
pre-pull DaemonSet and its Pods have been removed. The three nodes no longer
retain that validator image and now have approximately 4.4–6.1 GiB free each.
All nodes report `DiskPressure=False`; the master has approximately 62 GiB free.

Commit `01b73b3` adds a specific timeout diagnostic. Commit
`446129233e6be54cf83d22e755ef5b74b692bb3c` explicitly sets
`PPT_WORKER_HOSTNAME=arm-cluster-master` so PPT validation uses the master rather
than placing this renderer image on the space-constrained NanoPC nodes. The
focused server check passed 16/16 cases with no failures:
`/root/ppt-source-chart-regressions-44612923-f06d350b/receipt.json`.

A minimal two-file controller overlay completed from server Git. Its image is
`arm-cluster-master:5000/ppt-agent-controller:latest@sha256:b342cab35697e94877046f92972c12c2cc424fa0425b6cfe55c6d007ffd3203a`;
readiness passed with `PPT_WORKER_HOSTNAME=arm-cluster-master`. Receipt:
`/root/ppt-chart-controller-worker-53206d2e/receipt.json`. No validator rebuild
was required for this scheduling setting. This controller digest was subsequently
replaced by the review projection fix recorded below.

## First website-generated chart draft

The normal website workflow subsequently completed initial planning
`bb6c1238`, design planning `ae8528f9`, preparation `82382e4f`, and generation
`f3eb2aa5`. It produced version `e2fe3c13-9a8f-4e0c-b79a-052246ca5a45`: six
pages and an 88,411-byte PPTX. The website's download completed to
`Downloads/presentation-e2fe…pptx`, and all six page previews were available.

The frozen source chart binding is
`802b8059ef591d5e59ba703a2c4299662e26c9f765b66591e4a1b62524938ea0`.
Artifact readback checked the six source values, native chart and embedded XLSX,
actual rendered PNGs and preview hashes, slide text, and speaker notes. Evidence:
`/root/ppt-chart-artifact-read-5181ae3ef346/receipt.json`. These are artifact and
data checks; they do not substitute for owner visual approval or actual chart
editing in PowerPoint.

## Review revision projection fix

The first website review-revision request returned HTTP 409
`REVIEW_CHECKPOINT_UNAVAILABLE` and created no revision job. The connector had
correctly kept the complete `source_chart_binding` only in `selected_direction`
to fit the existing request depth limit, but the site's confirmation projection
still duplicated it in `design.directions`. Strict comparison therefore rejected
the otherwise unchanged frozen confirmation.

Commit `9a9f0a79f2539fc859985c5ae4d703bd02545234` synchronizes that pure
projection in the site and controller. It retains the full selected binding and
strict equality checks. Focused server checks passed 14/14:
`/root/ppt-source-chart-regressions-9a9f0a79-d31085af/receipt.json`.
The site and controller rollout succeeded:
`/root/ppt-chart-review-projection-fb079bbc/receipt.json`.
Their current digests appear in the table above; shared Agent and validator
images did not change in this follow-up.

Normal website revision `ae3f4d24-2d37-442c-b80f-89a29a84a2a3` succeeded and
produced version `24960dcf-55cf-46bf-a631-0ecc6590f302`, whose parent is the
first draft. The 88,231-byte PPTX downloaded through the website to
`Downloads/presentation-24960dcf-55cf-46bf-a631-0ecc6590f302.pptx`.
The original remained selected when the second version arrived; the new version
was explicitly selected for preview and download.

Readback receipt `/root/ppt-chart-artifact-read-b305ffc9b187/receipt.json`
passed for both versions. The first five preview hashes are identical; only
page six changed, including the exact requested title “读懂距平，也看清资料边界”
and paragraph “本资料未提供置信区间、地区分解和未来预测；若需这些分析，应先补充资料。”
Both PPTX files retain the exact six source values in their native chart cache
and embedded XLSX, the uploaded PNG bytes, native text and all speaker notes.
The original PPTX is unchanged from its first readback:
`344499a3356650c23305512eb22ece69f794d0c960a39f85bbde59e0d1b061ac`.
The revised PPTX SHA256 is
`9593b42a0bedaaf91f9d8c7002972138abcae1ff90dcb14513c29941dcff6669`.

The ordinary source-bound planning, generation, download and review-revision
workflow is now demonstrated. All application checks/builds/rendering ran on
the server; the browser was used for the actual UI, downloads and visual spot checks.
Owner visual approval, actual native-chart editing in PowerPoint, and publication
authorization have not been recorded for this chart draft.

## Later public-trial closeout and acceptance correction

The later signed-in acceptance view exposed 18 source-structure warnings on
the six-page chart draft. The readback receipts above verify artifact content;
they do not assert completion of all seven strict acceptance gates. Actual
PowerPoint native-chart editing, source-range refresh, save and reopen have
since passed on an independent copy. The original download is unchanged.
The source-warning repair, public-trial site release and platform responsibility
transfer are recorded in [the current closeout](public-trial-closeout-20261105.md).
Owner visual approval and publication remain version-specific.

The subsequent website revision `4605df6a-fdb7-43a7-addd-8e3ad5f73d46`
succeeded and produced corrected review Version
`e81f58f1-f589-4236-b0d2-3ea04e0daedb`, parented by `24960dcf`.
Its exact downloaded PPTX SHA256 is
`bf6521cec2c063e14b93315323208f13416f04d57ff73aee3151b8cf711c5b24`.
All seven strict programmatic gates passed. Actual PowerPoint 16.0 then
edited the native chart data in a separate copy, rebound the original range,
saved and reopened it; the workbook, native series and OOXML cache agreed,
the three source formulas and original download hash remained unchanged,
and the Office processes exited. Receipt:
outer `.research-temp/ppt-nasa-office-e81-20261105-r1/receipt.json`,
`passed=true`. The owner's visual confirmation is still pending and this
Version remains unpublished. The PPT parent remains 145/146 with R1 open.

### Owner confirmation follow-up — 2026-11-05

The owner subsequently replied “我已经视觉确认，还是不错的” for the six-page
NASA review Version `e81f58f1-f589-4236-b0d2-3ea04e0daedb`. Together with its
seven strict programmatic gates and actual PowerPoint result, this supplies
the remaining R1 acceptance evidence and permits a 146/146 PPT closeout.
The normal website saved the e81 visual check at
`2026-11-05 20:02:47 Asia/Shanghai`, checker `27714443`, review revision 2;
the original desktop-operation record at revision 1 remains retained.
Independent programmatic, per-page visual, PowerPoint desktop and complete
acceptance now display passed. The PPT parent passed validation at 146/146 and
was archived as `2026-11-05-add-hermes-ppt-workbench`, with main specs updated
at OpenSpec revision `92088c7c6cbcadceb76d17e5f65e3fc569fbbe96`. The shared
platform P1–P4 remain open. The Version remains unpublished; visual confirmation
is not formal publication authorization.
