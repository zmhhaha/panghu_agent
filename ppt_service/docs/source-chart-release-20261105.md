# Source-bound native chart release, 2026-11-05

The owner confirmed that the service has not formally launched and does not need
old-task or configuration compatibility. This release directly replaces the five
current PPT v2 routes, removes 45 obsolete PPT binding references and reuses the
existing execution UID ranges. Other services' routes and business records are
unchanged. No business rows were manually rewritten.

Application source was fetched on the server from Git commit
`fefd714c7a9face373d08e592f6ceb37a845f1f8`; application source archives were not
uploaded. All application builds and checks ran on the server. Each published
image uses the `latest` tag, source labels and the following immutable digest:

| Image repository at arm-cluster-master:5000 | Manifest SHA256 |
| --- | --- |
| agent-service-control | cf2f61033d5cbd9eb4036452d954afc15fb5d16ff284166163a340db0add5b21 |
| agent-service-pi | 6ad6e332fcd37c7c05337e8208cd4c9ea3cb242d363f76af54299e73ac0c1926 |
| ppt-service-site | 6290f3f263a67ece37e3147093152a624f6e8886f4bd73f0aa85a06dac18d236 |
| ppt-agent-controller | 510b0fa903aeeb204f4a7d896a6d99fe7bfebbde4a9bd80b961a30837698c72e |
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
was required for this scheduling setting. The table above records the preceding
deployment.

Normal website generation and revision remain unverified. No completed chart
draft, native-chart PowerPoint editing, owner visual approval or publication is
claimed from the deployment or the late validator response.
