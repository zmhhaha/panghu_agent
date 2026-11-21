> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-long-video-20261120/render-canvas2d-recovery/READONLY-POSTFLIGHT.md`；原文件 SHA-256：`a178319312bb5773ac6842bf16ae741360f54853a59621e59ca431f40d75dd3b`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../../README.md)

# Read-only postflight and live source dry-run

Both operators are prepared externally and require root dispatch. Neither has
been run. They create no cluster resource, production run, author, recording or
render. Private evidence and payloads stay outside Git and image build contexts.
The only cluster commands in either operator are `get` and `exec`; remote service
requests in the semantic postflight use GET. The live source dry-run makes no
service request.

## Full rollout postflight

`postflight_operator.py` supports the actual Posix rollout directory:

```text
/root/video-releases/ink-film-20261120/render-canvas2d-recovery/rollout-ac7f7028003f
```

It accepts the root-reviewed raw SHA of `rollout-plan.json` and
`protected-before.json`. It rejects backslash paths. The protected capture shape
is `{deployments: [{namespace, deployment: FULL_OBJECT}], configmaps:
{"namespace/name": FULL_OBJECT}, observed_at: NUMBER}`. The current capture must
contain all four protected Deployments and its one ConfigMap. The earlier inert
backslash-named directory is never used.

Provide an unchanged copy of `formal/attempt7/reuse-baseline.json` on the host;
the operator pins its known raw SHA
`af98fd74254f90fbbd99b1471792ec17f330f7b55ade2a2053e1af320fd4ab88`.
It derives original raw run/package/version/child bindings from that baseline;
embedded original Windows path strings are custody labels, not Pod file paths.
No original host operation directory is mounted into a Pod.

The operator checks all three target Deployment UIDs and complete desired specs,
Ready/available/updated replicas and observed generation, all four protected
Deployment UIDs/specs/images, unchanged old ConfigMap data/binaryData/identity,
and the exact new immutable ConfigMaps. Normal server top-level metadata/status
changes are allowed. It binds each inspected Pod through its ReplicaSet to the
original Deployment, records its UID/image/imageID, and rechecks the workbench
identity after inspection.

Inside the current workbench it reconstructs C using the real
`FilmProductionSettings` and consumer context. It verifies exact installed B/P/C,
the regular single P file, consumer admission, producer capabilities, admitted
assets/drawing descriptor and renderer readiness with max_seconds=300. SQLite is
opened with `mode=ro`, `query_only` and a read transaction. Original source run,
all 13 stopped children, all 12 package/version bytes, original snapshot bytes
and frozen WikiSkill release are checked; public facade calls use `__new__`
objects with start/nested-transaction boundaries disabled. No Store/Creation
initializer runs. The current API's mounted public config is compared byte for
byte to the protected ConfigMap, then its real registry validates every fixed
execution binding and worker pool image pin. Control images remain fixed by
the protected Deployment checks. No Secret file is read by that control check.

Root invocation, after actual rollout and readiness:

```text
python3 -B postflight_operator.py --verify --rollout ACTUAL_POSIX_ROLLOUT --plan-sha256 ACTUAL_PLAN_RAW_SHA --protected-sha256 ACTUAL_PROTECTED_CAPTURE_RAW_SHA --reuse-baseline HOST_COPY_OF_AF98_BASELINE --output ACTUAL_POSIX_ROLLOUT/postflight-readonly-verified.json
```

`--plan` is an offline description. Actual mode requires a new output and creates
a durable intent. Failure preserves a partial report and private stderr files;
full remote tracebacks stay in those private logs. No log contents are returned
to the user. An existing intent is not automatically replayed.

## Public facade and pure assembly dry-run

`readonly_live_source_dryrun.py` streams the reviewed
`formal/attempt7/actual_facade_projection_remote.py` definitions and sealed
baseline/P/C through stdin into an exact workbench Pod. It needs no operation
tree mount, extra volume or modified reference path in the Pod.

Copy `readonly-live-source-dryrun-inputs.prepared.json` to a new actual input file.
Its exact proof keys are `baseline`, `policy`, `configuration`, `facade`; each
value is `{reference: ABSOLUTE_HOST_FILE, sha256: RAW_FILE_SHA}`. Fill the actual
workbench namespace/name/UID/container/image/imageID and renderer namespace.
All currently unknown fields remain null in the prepared template. P/C use the
raw file hash in these references and their separate semantic hash internally.

The live program uses the existing read-only connection helpers, checks original
A and its 13 SQL child pins, reconstructs actual installed settings, and calls
`FilmCreationWorkflow.authorize_source_runtime_reuse` and
`module_reuse_material` through the real public facade. Its normal pure assembler
uses the returned original packages/assets and exactly the six installed host
files. It verifies unchanged original source/asset bytes, complete module map and
silent 7200-frame clock, then discards the potential snapshot after recording its
hash. The virtual target ID is checked absent and never inserted. There is no
browser, encoder, network service call, workflow start or database write. The
transaction is rolled back and `total_changes` must remain zero.

Root invocation after concrete actual inputs are filled:

```text
python3 -B readonly_live_source_dryrun.py --verify --inputs ACTUAL_DRYRUN_INPUTS.json --output NEW_EXTERNAL_REPORT.json
```

This produces the independent `source_policy_dry_run` evidence for attempt7.
`--plan --inputs PREPARED_INPUTS.json` only describes missing values. Neither
report establishes that the user's actual five-minute film has rendered or
passed artistic review.
