> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-long-video-20261120/RECOVERY_OPTOUT.md`；原文件 SHA-256：`10bc94c981dbddc12beab2618edd17cd2088fdd15f8c202dac0bcc0efe2d8a4e`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../README.md)

The operator is `cleanup_recovery_optin.py`. It has been syntax reviewed only;
no mode has been executed. Upload it to the ARM host after review.

It targets only creation Deployment `video-animation-creation` and the fifth
publication request `119ab57d-3f70-490f-9468-adddc62d4eb5`, publication
`61fb7522-a64d-4d3f-9319-8ac7eb00a0da`, owner `27714443`. It removes exactly
the three recovery flag/value pairs for private manifest `1d5930bd…`, preserving
every other spec value, including application/init images and annotations.

After actual publication and source audit pass, prepare with the reviewed
release ID and full release SHA:

```sh
python3 -B cleanup_recovery_optin.py prepare --release-id ACTUAL_RELEASE_UUID --release-sha256 ACTUAL_RELEASE_SHA256
```

Preparation requires the selected publication to be published, its native
Agent job to be completed with matching positive stop proof, zero active or
quarantined Agent jobs, no pending publication, and no current creation business
or uncollected render work. All checks are read-only. It writes the captured
Deployment, exact desired spec, JSON patch, capabilities, 32 source hashes,
selected active-release identity and prior specs of unrelated deployments under:

`/root/video-releases/ink-publication-draft-20261121/recovery-optout-r1`

Review those files before the explicit apply mode:

```sh
python3 -B cleanup_recovery_optin.py apply
kubectl -n video-animation-creation rollout status deployment/video-animation-creation --timeout=120s
python3 -B cleanup_recovery_optin.py verify
```

Apply repeats the publication/stop/idle/capability checks, verifies unrelated
deployments stayed unchanged, and tests the creation UID, resourceVersion and
entire spec. The only replacement is its container args, removing six entries.
An intent is saved before the single Kubernetes patch call. On a lost response,
use `status` and then `verify`; never repeat apply.

Verify requires Ready updated replicas, no old Pods, the exact desired spec and
actual image digest, absent recovery flags, unchanged live capabilities including
formal Manim, identical 32 fixed source hashes, the same new active release and
unchanged unrelated deployments. Private failed drafts and historical snapshots
are retained. No cancellation, image build, model request, publication POST,
source change, or cleanup of private data occurs. Hold formal plan submission
until this verification completes.
