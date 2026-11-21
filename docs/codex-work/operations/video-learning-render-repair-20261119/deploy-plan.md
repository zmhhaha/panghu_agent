> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/video-learning-render-repair-20261119/deploy-plan.md`；原文件 SHA-256：`e21dbadca6f81238bd47e41311a13cf10a8693a9f5a49c20c4af3faa65518779`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../README.md)

# Video learning render-repair deployment preparation

Read-only preparation, 2026-11-19. No application source edits, production writes, build, rollout, model call, or retry was performed by this preparation task. Only this plan and the read-only provenance receipt were saved locally.

## Current production

`live-deploy-provenance.json` is the current inspection receipt.

- Executor: namespace/deployment `video-animation-creation/video-animation-creation`, container `creation-executor`, init container `prepare-private-root`.
- Ready 1/1, Recreate strategy, scheduled on `arm-cluster-master`.
- Current revision: `d119cd12a9d3311cd56d940c57e53d6ddbdd1915`.
- Main/init image: `arm-cluster-master:5000/video-animation-creation:latest@sha256:f200a2fd6fc506e4bc9a463f0dd329f2bd0ab5a570714340c5898637e33afe07`.
- Shared reference-learning/Agent public config remains `agent-service-resource-plan-46982c3a9cbdd00c0af2`.
- Latest saved WikiSkill release expected by the previous operator is `8b1de934-ba84-430f-96a5-c08725a75021`, SHA `69093b9b54466343ea6db3f346750023857d3416e5d3d3b05d83b65a73f80ef9`. Read this live again before freezing the new rollout plan; do not assume it cannot change.
- Agent-control, lab and workbench have old historical annotation fields alongside newer ordinary OCI revision fields. Preserve those deployments exactly; their historical annotations are not an instruction to rebuild them.
- Server `/root/panghu_agent` is clean on `codex/video-capability-experiment-redesign`, with old working-directory HEAD `13399a5fe9723c930dc923965e54a24129edf8a2`. The deployment is built from a separate clean detached worktree, so this old primary HEAD is expected and need not be moved.

Local application repository is `panghu_agent/`, branch `codex/video-capability-experiment-redesign`, HEAD `b2de600005aefcaca4303aff8e0c988c81a992cb` before this repair. Existing unrelated dirty tracked files must not be staged: `agent_service/deploy/animation_review_batch_overlay.py`, its test, `video_workbench/animation_knowledge.py`, and the pre-existing documentation edits. Commit only the repair files and associated tests/docs after review. Root armbianbegin branch is `codex/video-animation-lab-server-vps`; its portal/AGENTS/doc changes are outside this repair.

## Smallest release scope

Rebuild only the complete executor image if the final changes stay in `video_workbench` Python modules, the trusted render host, and source-context guidance. The isolated renderer mounts the host/runtime files from the executor at runtime; `animation_method_renderer.py` changes therefore do not require rebuilding `video-motion-renderer`. Do not rebuild Agent control/worker or lab unless the final implementation actually changes their running assets/contracts. The executor Dockerfile copies all of `video_workbench/`, so a new guidance module is automatically included.

Keep `latest` as the only application tag, use the pushed digest in the deployment and record full revision/source hashes. This follows root AGENTS.md. No nested AGENTS.md was found by the repository file search.

## Existing full-source build path

The reusable helpers are in `D:/codex-work/operations/video-learning-completion-20261118/`:

- `launch_executor_fix.py`: launches an executor-only build remotely.
- `launch_picture_release.py`: supplies its `REMOTE_LAUNCH` implementation and subprocess helper.
- `build_executor_fix.py`: ordinary `Dockerfile.animation-creation-executor` build/push, exact committed full source, then offline smoke.
- `build_picture_release.py`: supplies the offline smoke.

After the reviewed repair commit has been pushed to `origin/codex/video-capability-experiment-redesign`, copy the four helpers into this new local operations directory (do not overwrite old evidence). Extend the copied smoke list with every changed/new repair source file, including `learning_execution_guidance.py`, `animation_method_renderer.py`, `video_learning_service.py` and any other final integration files. Keep the actual source tree untouched and clean in the server build checkout.

Launch form, once the real full commit SHA is known:

```powershell
python D:/codex-work/operations/video-learning-render-repair-20261119/launch_executor_fix.py --revision <FULL_COMMITTED_SHA> --base /root/video-releases/video-learning-render-repair-20261119
```

The launcher performs these operations in order, rather than uploading patched application files:

1. `git -C /root/panghu_agent fetch origin codex/video-capability-experiment-redesign`.
2. Require `<FULL_COMMITTED_SHA>` to be an ancestor of `FETCH_HEAD`.
3. `git worktree add --detach /root/video-releases/video-learning-render-repair-20261119/source <FULL_COMMITTED_SHA>`.
4. Require exact HEAD and entire checkout clean.
5. Run the ordinary Dockerfile build with `SOURCE_REVISION`, push `video-animation-creation:latest`, verify revision label and immutable digest.
6. Run the image offline with no network and compare source bytes/imports against the clean checkout.
7. Save `build/full-source-manifest.json`, `executor-build.json`, `executor-smoke.json`, `build-completed.json`.

The helper default timeouts are long, but the launcher starts the build detached and returns a PID/receipt. Poll receipts/log tails with short read-only calls so user commentary remains possible. Do not rerun launch on an ambiguous result; inspect `launch.json`, `launch.lock`, the exact process and existing `build/` first.

## Rollout path

Use the latest successful operator as the basis: `learning-final-r2/bridge_executor_rollout.py`, with its `deploy_executor_only.py`, backup helper and measured-source verifier. Do not execute that old operator as-is: its ROOT, additional-byte smoke revision/image, dependencies, release receipt name and prior proof paths are deliberately fixed to the previous release.

Adapt a copy under the new operations directory and `/root/video-releases/video-learning-render-repair-20261119/`:

- Preserve the existing Agent tokens, public config, mounts, non-image arguments, admitted Manim/film-Manim bindings and pinned motion-renderer image exactly.
- Change only main/init executor image and source/provenance annotations.
- Require the new build receipt, revision, image digest and every changed/new source file in offline and runtime byte verification.
- Capture current deployment before/expected/patch, public config identity, current WikiSkill release identity, unrelated deployments and local business task counts.
- Make SQLite online backups before apply; add `video-learning-renders/method-render-ledger.sqlite3` and `picture-learning-renders/method-render-ledger.sqlite3` if present to the old operator inventory, since the original inventory predates their recent usage.
- `prepare` creates a plan, then `apply --plan-sha256 <EXACT_PLAN_SHA> --execute` uses compare-and-set tests on the latest deployment before patching. Existing user authorization covers this rollout; these are operator integrity checks, not a new human approval request.
- The previous quiet gate checks only this executor's local tasks, not all Agent tasks or the unrelated workbench globally. Recheck just before patch so an in-progress creator/render is not interrupted. If newly active work exists, allow it to finish while independent work continues.
- After rollout, verify ready replica, actual main/init digest, source bytes in `/app`, healthz, and unchanged public config/WikiSkill/unrelated deployments. Save the complete receipt.

## One real regression validation

Target existing project `68240ff5aecd482289612cde58124dad`, owner `27714443`.

- Failed initial experiment `f4f33bd8cfeb4b978cddba0cf681ccf1`.
- Latest failed technical retry `0136450b26e7486ea8ff728f46ec987d`.
- Both are terminal and proven stopped. Source SHA is `330a60ff88a97581ff61b9db2d35d3a09d8141ec2706b02cc41d7d9c95f0b3b3`.
- Reference selected 29–35 seconds; output 6 seconds, 1280×720, 24fps; original budget 3 rounds/900 seconds. No explicit drawing/motion selection, so the Agent may select another admitted implementation to preserve the visual goal.

Public endpoint: `POST https://video-service.panghuer.top/dev/animations/api/video-lab/projects/68240ff5aecd482289612cde58124dad/experiments`. Private endpoint inside executor is the same path after removing `/dev/animations`, port 8767.

`video_learning_http.py` forwards this to `service.experiment(owner, project_id, body)` and wakes the normal durable pump. Do not manually advance or edit the database.

Current API modes:

- `retry`: body exactly `{"request_id":"<PERSISTED_UUID>","mode":"retry"}`; frozen original request/budget, no feedback fields allowed. In the old code it resumes render using identical source. Use this only if the new implementation explicitly routes the recognized failed-render case through bounded Agent repair.
- `fresh`: permits a new feedback and budget while preserving project/reference; it is a new creation round and is not equivalent to restoring the failed render.
- `continue`: needs an actually rendered baseline and is unavailable for this peak project, which has no successful sample.

Existing private API helper, safe to import for its functions without executing CLI commands: `D:/codex-work/operations/video-learning-20261118/product/verify_video_product.py`. `product.doc(method,path,body,owner='27714443')` uses SSH and the in-pod private token without printing/exporting it. It is safer than extracting browser credentials or guessing an external bearer token.

Before the single submission, persist the exact JSON to `peak-repair-request.json` in this operations directory and capture current project/latest experiment. On uncertain transport, use GET project/catalog and match the exact request_id; do not invent a new request ID or automatically post again. Save the returned new experiment identity immediately. GET/read only during polling. Download outputs only to `D:/codex-work/archives/video-service/20261119/peak-render-repair/`; temporary output stays under `D:/codex-work/tmp/video-service/video-learning-render-repair-20261119/`.

Success evidence must include new author operation (if repair was required), actual changed source/engine, real 144-frame render success, 6-second MP4 and technical seek checks, review outcome and bounded budget usage. Sampled-frame review does not establish continuous playback/audio/human aesthetic acceptance. Preserve old failed records and any best sample; do not silently delete, replace history, publish WikiSkill or claim visual equivalence solely from a technical pass.
