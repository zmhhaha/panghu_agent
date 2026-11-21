> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`archives/video-service/20261116/c-rendered/feedback-20261106/animation-lab/agent-creation-phase1/creation-product-r1/method-runtime-operations-plan.md`；原文件 SHA-256：`d46a49399351e2cf971318abb3c3f9a0d7f9c9413b5aa04452a26d4e434dc12d`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../README.md)

# 最新执行进展：r4 方法运行时已激活（2026-11-11）

本节覆盖下方旧检查点的当前状态。后端版本 `f7f98d17a81e8ddf0b5b901fc7172a7eff2a9339` 已提交、推送并同步服务器，228 项联合回归通过。`OP/full-source-method-release-r4` 已按普通 Dockerfile 从完整 Git 源码构建并推送，author/reviewer 新组合分别完成技术准入，activation apply 及独立 verify 均成功。前端交互修复版本 `9e9357474e100bcdbeabdd2e2124def337200c57` 随后已提交、推送并单独发布；分支未合主线。

- r4 源码清单 SHA256 `f941ba76e43907520f415d6c7434229959ee5705972f879d02e5fdf5a657c503`；三镜像均使用 `latest` 并固定 digest：control `6b36382d30fa4beedfea6e78b98a9e9b381afff4812b84554c95f3d64de66fa6`、Pi worker `1190bba430a238025adc22e82570f519e3269c1f2b39bbdc31909587d431c8ab`、executor `b73e319fc1debc42f67038edb060b10ecb8485c6906dc7bb7f4eddeba64e1bd8`。
- `candidate-method-author-r3` 使用新身份 UID303200040/count3，在 r4 精确 worker 镜像上成功，实际4次模型请求；原始源码仅渲染一次，3秒、72帧，媒体 SHA `9db8a9985bcab630cd1d6abfb543312321150086d761098697951a09c0b1c29b`。83文件及6目录严格封存，回执 `OP/method-render-capture-seal-r3-before.json` 和 `OP/method-render-capture-seal-r3.json`，桥后续仅 lookup 原记录。独立技术准入 `OP/method-author-admission-r3`，combination SHA `9a94d0a29dfcbfe8cc21791cb5d284c5bd6e15372257b3752f5b29b8dc20395c`。不借用旧 `candidate-method-author-r2` 的组合或准入，原始生成源码、失败历史与渲染证据保留。
- `candidate-method-review-r2` 使用 UID303200050/count3，读取 `OP/method-review-flat-inputs-r2` 的6张真实原帧，单次成功，实际4次模型请求（含连通检查），worker/proxy 均 exit0、零重启，过期 lease 实测拒绝。独立准入回执 `OP/method-review-admission-r2/summary.json`：`accepted_host=true`、生成时 `enabled=false`，combination SHA `3bce9d54b6c9ac1a970f18f52e59db7fea09ca6339ac603f61b1ad4e1fd12617`，report SHA `bec8281e8973590e2e7c1b3527be34bffe1e1e17a85d651430f2a398f3f092c3`。
- reviewer 传输证据绑定 c0～c5 的实际图片 SHA、字节数、MIME 及已完成上游请求；scope 为 `completed_upstream_transport`。`motion_observed=false`、`audio_observed=false`、`whole_video_watched=false`、`model_understanding_verified=false`，不能据此认定完整动作、音效或主观画质通过。
- 原 method 协议的 `frames/` 路径与固定 reader 根目录协议冲突已修复：methods 业务、profile、probe 均使用 `frame-candidate-N.jpg` / `frame-baseline-N.jpg`；共享 reader SHA 保持 `5f2bde15e1f477b2f6c01bca39785aec0d6ebaa2910627ccfc0a4d44e25dd601`。真实 Node reader 跨层回归覆盖12图首次评审及24图比较。旧 reviewer-r1 失败与已消耗 UID 保持原记录；新六帧备料只改名称，原图片字节及 SHA 未变。
- `OP/method-activation-live-r1` 已完成冻结计划复核、一次 apply 及独立 verify；回执为 `apply-complete.json` 与 `verify-*.json`。r4 control/executor 相关 Deployment 实际 Ready，author/review profile 均 `configured/ready/supports_inputs=true`，Service 已恢复。仅增加两方法角色、两个 pool 和受限 `video-animation-methods` caller；旧 catalog 行和凭据投影保留。正式 UID 范围为 author304000000、review304065536，各65536、互不重叠；executor 使用单独只读方法 token。渲染器仍固定原 digest `744b62419edefee38c243bce8fbb581797487990b0dc02909f7a7a6eb89433da`。
- 唯一真实 UI 委托 `fdf40e8b-c741-4c8d-b510-100cef36f446` 已完成，状态 `completed`、停止原因 `budget_exhausted`。冻结预算为 creator2（plan+create）、render1、review1、900秒；实际严格使用这两次 author、一次渲染、一次 review，3个 Agent job 各3次模型请求，共9次。原上传 JSON 与实际结果 artifact SHA 精确绑定，12张真实评审帧确认完成上游传输。独立只读回执为 `OP/method-real-commission-local-review-r1.json`、`OP/method-real-commission-relations-review-r1.json`、`OP/method-real-commission-control-review-r1.json`。
- 原始成片16秒、384帧，MP4 SHA `46bb01229e6ce194b69703a018953c621b9a924c5682ae8768ba849629c2f993`，原渲染容器已消失。3个方法版本均关联同一样片 `e1c853af-cb2f-4e7a-886a-8ef7510aa5c3` 及各自 reference validation，方法内容、标准和媒体摘要一致。验证均为 `incomplete`，静帧没有证明 motion；没有自动发布方法、迁移认可或主观效果通过。
- 私有 HTTP 交付只读核验通过：MP4和源码两次 GET 均200；MP4为198706字节且 SHA 与渲染一致，源码JSON为7877字节、SHA `7fa934e57da7f49058e2067fe068ae910ab7103ffc33726677f26fb91bc2da5a`，与原 artifact 中该候选源码一致。回执 `OP/method-real-commission-delivery-review-r2.json`。浏览器播放与保存意见、刷新恢复已验证；不将私有 HTTP 核验写成浏览器原生下载字节已独立验证。
- 前端修复已从完整 Git 源码按普通流程构建并 Ready，digest `206fcc8b2cc39096229ebd2b52418ca26bedd54fe5dee638ff5a6b737be48ee9`，回执 `OP/method-frontend-live-r2/release-receipt.json`。49项前端测试通过；真实浏览器验证保存意见触发重绘后，`feedback-range` 与 `execution` 均保持 `open=true`，“继续优化”继承16秒，新的预算仍默认3轮15分钟。两条流程验收意见均已撤回，原记录保留；没有发起第二委托。
- OpenSpec 已由主 Agent 完成同步并验证，revision `b37e36064211d0f2e086bd85f45e6a7ae71e3cbd`，新增完成1.5、2.2、2.6，现为9/34完成、25待办。后续重点仍为真实参考观察与学习点选择、预算内多轮及前后比较、异结构迁移与开发者认可、方法在正式制作中的消费；完整运动／音效证据和主观效果不能由静帧或技术成功替代。当前没有待核对的运行中委托，不另开重复生成、不自动发布方法、不合主线。

原始渲染器内部 `work/frames/` 保留；只有模型输入根目录平铺。不要修改老候选或render原始字节、重用已消耗UID、套用旧准入或自动无限重试。

---

# 本轮收尾与模型切换接续（2026-11-11，历史检查点）

用户要求在切换模型前收尾。所有子 Agent 已停止扩展工作；没有正在执行的生成、渲染、构建或部署操作。先读本节，再读下方原操作计划；原计划中的“未执行”只描述计划最初编写时的状态。

这里的“换模型”是用户切换当前协作模型，不构成修改线上 Agent model_policy 的指示。OpenSpec 收尾同步已完成：revision `db108aea663d6e90c97a5207c63bc02b4408c0ca`，精确回读一致、validate_change 通过，6/34 完成状态未变。

## 实际状态

- 应用仓库为外层工作区的 `panghu_agent/`，分支 `codex/video-capability-experiment-redesign`。
- 代码已提交、推送并同步服务器，版本 `384e25e864e4bf9720ff29c4c2044dbf36509cb8`；未合主线。
- 此版本修复结构化失败/取消记录的显示及比较播放器缓冲期间无法取消的问题。定向 Python 89 项、前端 47 项通过；两组不可与之前阶段测试简单累加。
- 从完整 Git 源码按普通 Dockerfile 构建三个后端镜像成功，保存在下述 `full-source-method-release-r3/`；尚未激活。
- 2026-11-11 收尾时只读核实：线上 lab 为 `e1ba4363be777595a5000f77cd84f9bbc32695ad`，digest `5d96d65467bfb69dd76febacf00fa25cde9c395bca7c00ace33308558861f2d5`，Ready 1/1，`frontend-preview=true`。本轮 384e25e 前端构建/发布尚未开始。
- 线上 executor digest `8f7730bb5d4bd710ee05599e2d6d0d0a3a916ab96443432f93e531d4370a5640`，Ready 1/1；Agent api/dispatcher digest `b8e5e536c8ddf1baffe8979d5d78f37245193fad3a27be2d6725e9a62b77b99a`，均 Ready 1/1。本轮未切换它们。

## 唯一真实 author 试跑：失败并已回收

远端操作根目录（OP）：
`/root/video-process-20261105/feedback-20261106/animation-capability-redesign/agent-creation-phase1/creation-product-r1`

`OP/candidate-method-author-r1` 是一次 3 秒、640×360、24fps HTML/SVG 技术探针，最多 8 次模型请求。实际 4 次请求，产物 0；未运行 render/reviewer/技术准入/激活/产品委托。

最后的 SSE 原始错误只有 151 字节：
`{"type":"error","code":"upstream_error","message":"Responses stream reported a failure","param":null,"sequence_number":0}`

HTTP 200 不代表流成功。worker 最终记录 `model_error`，失败阶段 `execute_native`。现有证据只证明上游 Responses 流失败，不能据此认定模型不支持工具、上下文超限或动画源码错误。下一步先只读追查同一请求的上游错误，不直接再次运行。

- `candidate-output/response-ledger.sqlite3` → `agent_model_requests.response`；末条 `body_sha256=e1d329ff02bb12a78031ef9173b8067190f64570763c73c8c747b7b543eebe1f`。只查看必要错误/元数据，不输出巨大正文或凭据。
- job `356539c0-2f92-4a37-8e28-f284491fe7db`，attempt `f804f0b6-026c-44c9-849b-faf93ca723d9`。
- `candidate-output/cleanup-summary.json` 确认 `exact_candidate_resources_deleted=true`；operator summary 确认 worker exit 2、proxy exit 0，均未重启，镜像身份验证通过，过期 lease 返回 401。
- UID `303200000`、count 3 已永久消耗，不得复用。失败目录与账本必须保留，不自动重试。

## 已构建但未部署的版本

`OP/full-source-method-release-r3/` 含 source-manifest/control/worker/executor/build-completed 回执。source manifest SHA256 为 `4db284555fb33bed2e2963d7a7e52cfaaf945e94f287484bfadc0e618efc1a5b`。

| 镜像（均 latest） | SHA256 digest |
| --- | --- |
| agent-service-control | 693d2dca29601fe5f9196fdcffc83561c0b092c9b60b8bf6b1368eec7777a715 |
| agent-service-pi | 31c95872f22d1171bf4fe4cb42d41fdd50b3129fa702d60a7dee729594f2859e |
| video-animation-creation | 6c464faa6b38298b87f536756e27b25794ba7559d9a4a59ec0e224267b486c51 |

Pi Cage donor pin 为 `arm-cluster-master:5000/pi-cage-agent-runtime@sha256:8c6c175ae53f0f1f6ebface6855079d7549138ef33de0320b5942dc38f6e6ced`；PPT donor pin 为 `arm-cluster-master:5000/ppt-hermes-worker@sha256:e77a8cde745afd782d43549681acdc88ed491f0f7a69f0327233c0c401ad60df`。r2 失败是选用错误旧 donor 缺少 worker-cli，已保留，不能重用该 donor。

## 后续执行顺序

1. 只读追查上述流错误，确认具体原因。必要修复重新提交/推送完整源码并普通构建；变更代码后不可沿用旧版本准入证据。
2. 确定原因后，另建有界 author 探针及新的 UID，保留第一次失败记录；成功后才能用原始未修改源码真实渲染。
3. 用真实抽帧输入运行独立 reviewer，再分别形成禁用状态的技术准入。静帧输入不能声称观察了运动或听过音效。
4. 前端发布与后端准入可独立推进，但前端未接通前保留 preview。前端发布脚本/接续记录在本目录 `deploy_frontend_live_r1.py`、`frontend-live-r1-handoff.json`，本轮没有执行。
5. 准入完成且前端变更结束后，新建 activation prepare 计划，核对后 apply/verify；不要运行旧 rollout 的 main，也不要套用旧快照。
6. 从真实 UI 创建一次新的完整样片委托，建议 16 秒“近路不一定更快”，一轮、900 秒，验证提交/状态/视频/意见/继续表单。不自动发布方法、不合主线。

原始渲染桥 `method_probe_evidence_v1.py` 已上传 OP 但未执行；仅可接收成功的原始 author 目录。activation helper 已修复 ESO 默认值冻结问题，20 个离线自检通过。拟用 reviewer UID 303200010、正式 author 304000000、reviewer 304065536 都只是计划，使用前重新核实，不视为已分配。

OpenSpec 项目 `ec663f5a-53d3-4a0e-9037-933eb788d183`，change `evolve-video-agent-creation-and-effect-library`，仍 6/34 完成。不能因代码/镜像完成勾选真实闭环、参考视频学习、迁移认可或正式应用任务。同步结果以该 change 最新检查点为准。

保留既有无关修改：`agent_service/deploy/animation_review_batch_overlay.py` 及相应测试；未跟踪的旧 effect_library 文件和 audit/test 目录不是本轮待提交内容。不要在接续中批量 add、清理或迁移它们。

---

# Method role runtime: shortest actual validation path

This document is an unexecuted operator plan. No live cluster, image, credential,
UID reservation, paid model task or actual render was created by preparing it.
Old create/review acceptance does not admit the two new method roles.

## 1. Complete-source release

Commit and push all reviewed application source first. Sync `/root/panghu_agent`
to that exact full commit and require clean application roots. Build with the
ordinary builder; use existing immutable Pi Cage and PPT donor pins from the
server, never infer or print secrets:

```bash
python3 -B video_workbench/deploy/build_creation_release.py \
  --revision <full-40-hex-commit> --output <new-release-directory>
```

It builds/pushes the control, Pi worker and creation executor from ordinary full
Git source. Build the laboratory frontend separately with its normal Dockerfile
and updated allowlist. All tags are `latest`; record the resulting immutable
digests and image labels. Old running image identities in historical notes are
baseline hints, not evidence that this release has been built.

Keep `source-manifest.json`, `control-build.json`, `worker-build.json` and
`executor-build.json`. New `animation-method-v1` resources must appear in the
worker context and match the manifest pin in this commit. The ordinary builder
now verifies and includes this bundle.

## 2. Freeze author probe without invoking the model

The server operation directory is:

`/root/video-process-20261105/feedback-20261106/animation-capability-redesign/agent-creation-phase1/creation-product-r1`

Review and copy these local ignored operator files there:

- `method_prepare_server_v1.py`
- `method_candidate_runner_v1.py`

They use existing read-only helpers `read_inputs_prepare_server_v1.py` and
`activation_server_v3.py`. Do not replace those helpers. Do not run the old
candidate runner against new method input: its role/filenames are incompatible.

Create a root-owned JSON input with explicit values, for example:

```json
{
  "schema": "animation-method-candidate-inputs.v1",
  "role": "author",
  "name": "candidate-method-author-r1",
  "source": "/root/panghu_agent",
  "revision": "<full-40-hex-commit>",
  "source_files_manifest_sha256": "<source-manifest value>",
  "worker_image_file": "<release-directory>/worker-build.json",
  "source_pool_id": "<explicit existing Pi pool>",
  "candidate_uid_start": 219200000
}
```

The UID is illustrative: inspect permanent ledgers, live Pods, host accounts and
processes, and choose an explicit unused range. Preparation checks but never
reserves. With no input document, author uses a new three-second, 640x360,
24-fps HTML/SVG filtering fragment. Default model policies come from the actual
existing create-v2/review-v4 bindings, not guessed model names.

```bash
python3 -B method_prepare_server_v1.py --inputs <author-inputs.json>
```

Inspect the resulting new candidate directory, `budget.json`, frozen hashes,
disabled host, source provenance and candidate manifest. Preparation copies the
exact reviewed runner into that directory. A changed live public catalog or
source/image/operator byte invalidates the launch.

## 3. One author task, then one actual render

Run the frozen runner once from its candidate directory with `--execute` and
matching absolute paths:

```bash
python3 -B <author-directory>/method_candidate_runner_v1.py --execute \
  --profile video.animation-method-author.v1 \
  --name candidate-method-author-r1 --uid-start <frozen-value> \
  --catalog <author-directory>/public-disabled.json \
  --scenario <author-directory>/candidate-scenario.json \
  --source-dir <author-directory>/candidate-sources
```

The exact new profile enforces at most eight model requests per attempt in the
durable ModelProxy transaction, including connectivity, failed/reserved/unknown
requests. Completed-request replay is free. The global proxy setting can remain
40 for old roles. The one-shot runner reserves its UID permanently and exports
both stopped Pods, ledger and original source/result. Failure stops; unknown
execution is reconciled against these original resources, never a new name.

Use the committed `MethodAnimationRenderer` with `enabled=True`, a dedicated new
root, explicit existing renderer `latest@sha256` image and timeout 360. Load the
original `candidate-output/animation-method-input.json` and validated result.
Call exactly one `render(operation_id, result['source'], input,
result['sound_events'])`. Do not edit Agent animation source or substitute a
manually drawn sample. Save its response and operation directory, including
`binding.json`, original host/scene/request, plan/start/outcome, all frames,
reverse-seek frame, MP4, optional sound WAV and renderer receipt.

Any failed or unknown render consumes this allocation. `lookup` the same
operation if needed; do not allocate another render automatically.

## 4. Freeze and run one reviewer task using actual image bytes

From that successful render, take at most six original JPEG frames using
`renderer.frames(operation_id, max_frames=6)`. Stage their exact bytes as flat
`frame-candidate-N.jpg` files in the review input root (not a `frames/` subdirectory).
Build a new review input from the frozen author context:

- Set `mode='review'`, source's actual `base_source`, and `comparison` with the
  current candidate ID and `baseline_candidate_id=null`.
- Each observed frame needs `frame_id`, `file_name`, actual byte `sha256`,
  `timestamp_seconds`, current `candidate_id`, and actual MP4 `media_sha256`.
- Preserve goal/criteria/constraints/assets; supply the author's proposed method
  content. Keep `audio_observed=False` and `motion_observed=False`; limitations
  must explain that only sampled still images are observed.

Prepare a second input with role `review`, a distinct unused UID range,
`name='candidate-method-review-r1'`, `document_file` and `frames_dir`. Execute
the same prepare and frozen one-shot runner with the review profile. One actual
review task is allocated. The independent admission verifies completed upstream
transport of every image byte; merely naming frames is insufficient.

## 5. Independently admit both exact combinations, still disabled

Run from the committed full source, after successful originals are exported:

```bash
python3 -B -m agent_service.deploy.animation_method_acceptance \
  --candidate-dir <author-directory> --source /root/panghu_agent \
  --renderer-dir <author-render-root>/operations/<operation-hash> \
  --renderer-image <actual-renderer-latest-at-digest> \
  --output <new-author-admission-directory>

python3 -B -m agent_service.deploy.animation_method_acceptance \
  --candidate-dir <review-directory> --source /root/panghu_agent \
  --output <new-review-admission-directory>
```

These commands invoke no model or render and never enable a route. They verify
the exact new protocol, bundle/tool/model/image pins, captured harness, stopped
Pods, isolation, actual proxy calls and lease rejection. Author additionally
requires actual stopped render bytes. Their result is disabled technical
admission, not human aesthetic or migration approval.

## 6. Additive activation, scoped caller, then one product commission

The old rollout scripts hardcode older roles/caller/revisions. Use the new
`method_activation_server_v1.py`; do not run those old scripts unchanged.

Copy and fill `method-activation-inputs.example.json` in this operation directory.
Its UID values are examples only and must pass the permanent-ledger, historical,
database, active-Pod, process and account checks. Required input is one exact full
Git revision/source measurement, control/worker/executor full-source build
receipts, explicit confinement donor pool, actual renderer digest, two distinct
production UID ranges of 65536, and both original new-role candidate/admission
directories. Author also requires the exact original renderer capture.

```bash
python3 -B method_activation_server_v1.py prepare \
  --inputs <actual-method-activation-inputs.json> --output <new-plan-directory>

# Only after inspecting the exact frozen plan and its before/after resources:
python3 -B method_activation_server_v1.py apply --execute \
  --inputs <same-inputs.json> --output <same-plan-directory>

python3 -B method_activation_server_v1.py verify \
  --inputs <same-inputs.json> --output <same-plan-directory>
```

The script must reside in the original root-owned operation directory, and the
new plan directory must be its immediate child. `prepare` performs only trusted
read-only checks, a server dry-run to freeze new ExternalSecret defaults, a
networkless same-image static host check, and writes the private plan. It never
reserves UID, modifies Vault, creates Kubernetes resources, submits models or
renders. Compare `config.public.json`, `configmaps.json`,
`desired-executor-spec.json`, `desired-runtime-external-spec.json`,
`executor-method-externalsecret.json` and all `before-*.json` before apply.

Preconditions include one ready Pod per existing Agent control deployment, the
existing single-writer Recreate executor with the exact original data root and
renderer pin, no new method routes/caller/token/Vault field/Secret, no active
Agent job, no nonterminal business creation/commission or unresolved renderer
ledger operation, unchanged historical quarantines, existing migrated database
schema, available nonoverlapping UIDs and trusted ordinary source/media files.
Preparation records the Vault version/hash and credential fingerprint without
writing raw secret values to the plan.

`apply` is a one-shot operation: a durable claim precedes mutations. It closes
and stops the executor first, then holds the existing shared Store admission
lock while closing Agent admission and stopping API/dispatcher. This keeps work
under the existing guard's 240-second bound; the guard-protected phase rejects
after 210 seconds. Any drift, active work, ambiguous patch, failed credential
projection or lost process stops with retained evidence and manual review. It
does not roll back, recycle UID ranges, clean historical files or retry under a
new identity.

It permanently appends only two UID ranges; CAS-adds
`ANIMATION_METHOD_AGENT_TOKEN` to the existing Vault record; appends one token
projection to the existing runtime ExternalSecret; creates a dedicated
`animation-method-agent` ExternalSecret in `video-animation-creation`; and mounts
that distinct projected token in the executor. Previous Vault fields, runtime
tokens, callers, old pools/routes/hosts and network policies remain exact. The
private data snapshot compares SQLite rows semantically and verifies all old
non-database file hashes; startup may add empty new method stores.

Concrete evidence includes `plan-files.json`, `apply-once.json`, namespaced CAS
patches/results, `submitters-stopped.json`, `reservation-proof.json`,
`credentials-once.json`/`credentials-complete.json`,
`before-reopen-verification.json` and `apply-complete.json`. Before reopening, it
checks actual image IDs, exact config/secret mounts, scoped `/v1/capabilities`,
both ESO specs and ownership, old data/Agent rows/quarantines and both closed
service selectors. Reopening confirms endpoints reference only the new ready
API/executor Pods. `verify` checks a completed activation and does not submit a
task. A partial apply deliberately cannot be rerun as a new attempt.

Prepare an additive public catalog for both new profiles with the exact admitted
hosts and two distinct unused production UID ranges. Preserve old bindings and
historical hosts. Register a dedicated `video-animation-methods` caller/token
reference restricted to only the two new profiles. Create its separate Secret;
never expand the old creation caller's permissions. Mount the new caller token
in the executor through `--method-agent-token-file`. Update control and executor
to this release's immutable digests with normal full-source deploy paths. Freeze
the public baseline and compare it immediately before any catalog mutation.

Only after admission, host verification and scoped token readiness may the new
routes become enabled. Verify actual API/dispatcher/Pi/proxy request, not only
direct candidate harnesses, using one fresh product commission:

- creator budget 2: one plan and one create;
- reviewer budget 1;
- render budget 1;
- explicit commissioned seconds allowance;
- no automatic retry; failed operations consume budget, unknown operations look
  up their original operation and retain their claim.

The per-task timeout is 600 seconds and renderer maximum is 360 seconds for
these probes. The probe preparation does **not** enforce one combined deadline
across separate operator commands and does not claim a money cap. Record actual
limits honestly. Runtime proof cannot publish a method: the developer still
approves the full sample and migration result before formal production release.
