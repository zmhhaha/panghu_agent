> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-quality-perf-20261117/release-diagnosis/AUTHOR-IDLE-RELEASE.md`；原文件 SHA-256：`cc43888b137f0ba261c98081e7fd1d8d850f5bdfc2590e2dda790b08cb27a169`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../../README.md)

# Author HTTP idle runtime release

Completed on 2026-11-17. This operation changes only the animation-method author worker route.

- Full clean pushed source: `31f3eed7c24a23668f2b29219a9654c922dd7bb3`.
- Worker: `arm-cluster-master:5000/agent-service-pi:latest@sha256:67664e0f96fca102cd6df82d3e6f53b77255b1547f87a048b4f86f8c75e7746f`.
- Runtime: `author-http-idle-31f3eed7-r1`.
- Live binding: `video-animation-skill-method-author-pi` / `binding-734556c4a2c1622f7982`.
- New immutable public ConfigMap: see `worker-release-evidence/activation-author-only/plan.json`.
- Production UID range: `312541184` + `65536`; permanent ledger append, no old range reuse.
- Current author and review profiles are configured and ready. Review remains `binding-090c44c3c31ff531e3fd` with its old exact image and admission.

## Validation scope

The image smoke independently inspected the actual built adapter and verified that `httpIdleTimeoutMs` uses the task remaining budget. One new author candidate completed with **4 real model requests**, including connectivity. Its worker and proxy terminated with exit 0, no restart, exact image identity, and expired lease HTTP 401. Candidate resources were removed after evidence collection.

One original 4-second native Canvas technical clip rendered 96 frames using the existing pinned renderer `45504cc1...`. Renderer capture metadata was secured only after original cleanup and an independent container absence check. All **122 paths** retained identical file SHA256 values; no source, input, artifact, binding, or result content was changed. Offline author contract assessment and admission then passed. This fixture does **not** claim HyperFrames validation, artistic quality, or a greater-than-300-second idle stream test. The real water-ink experiment remains a separate normal HyperFrames business validation owned by the parent task.

The author-only catalog adds exactly one environment, binding, and pool; it adds no contract or bundle, changes one of 37 routes, and preserves every caller and historical row. Static checks used the actual worker and the unchanged current control image.

## Activation

Three control deployments kept their exact existing image `474a01be...`; only their public config volume changed. Shared nonterminal jobs and dispatcher allocation slots were checked before entry closure and again after API termination. Terminal/quarantined slot references were retained. The API was stopped during the small window to prevent keepalive submissions, then restored with its original replica count and the original Service selector. The measured closure was **34.578 seconds**, with no errors and no rollback. Authenticated capabilities validated the live author binding and unchanged review pins. Secret hashes, executor, model proxy Service, other deployments, policies, and historical hosts remained unchanged. No DB migration or credential write occurred.

The database claim is limited to read-only active jobs, slots, scheduler, and new UID range checks. It does not claim a full database hash freeze.

## Receipts

`worker-release-evidence/` holds build labels/digest, candidate execution and cleanup summaries, original renderer proof, metadata freeze hashes, real disabled admission, before/after catalog, deployment specs, 120-second bounded window receipts, final live capabilities, and an independent downloaded JSON closure comparison. Bulky media and original candidate sources remain only in this private server release:

`/root/video-releases/ink-worker-http-idle-20261117-r1`

No new model task was started after the technical candidate. The parent was notified immediately after live verification, before receipt collection.
