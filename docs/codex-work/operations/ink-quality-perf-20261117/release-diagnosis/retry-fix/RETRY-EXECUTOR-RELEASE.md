> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-quality-perf-20261117/release-diagnosis/retry-fix/RETRY-EXECUTOR-RELEASE.md`；原文件 SHA-256：`1a26f719bd751ba3980b8b086baecb3b8302afb2501338368e384bef834ac047`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../../../README.md)

# Retry materialization executor release

Completed: 2026-11-17. Built and deployed only the animation creation executor from the exact clean pushed Git revision `9c1cea5a5e517c01d91ce00930708ca4e64899a5`.

- Image: `arm-cluster-master:5000/video-animation-creation:latest@sha256:b2a938757c247bc87837d08c1b99784f157b5aa03e6f08e6934302ec34009b36`.
- Running and init containers both use that actual digest, no restart; `/healthz` returned `status=ok`.
- Source: `/root/video-releases/ink-materialize-retry-9c1cea5a5e51/source`.
- Live Pod UID: `66e47ca8-c4a1-47ba-bfe4-69bc7670d1bf`.
- Git blob and actual Linux image service.py SHA256: `50ead8495ce412ec746d8646751874f6bb2dbaa5f2a5f17e9a33d6ea9de5d2b4`.
- The corresponding Windows CRLF SHA256 is `0a664dc3e5e0581208f19a2b986817d9ae911c64a97cb553288c8af1c8a85a15`. This was confirmed as a line-ending difference by the parent before deployment. No source adjustment or rebuild was required.

Only the main/init image references and truthful pod release annotations changed. Command, args, resource limits, security, volumes, credentials, renderer `45504cc1...`, Manim image, all admission paths and contents, and the three Agent controls remain exact. The new author binding was not changed.

Before activation, the operator checked there were no active video Agent jobs, pending method commissions, or named animation Docker containers. It gated only this executor Service, scaled the old executor to zero, observed old Pod absence, repeated idle checks, and backed up both `method-lab/methods.sqlite3` and `method-lab/commissions.sqlite3` using SQLite's backup API. It verified backup logical schema/table hashes and asserted those hashes still matched after startup, before reopening. It did not migrate, reset, edit, or restore production records. The backups remain on the server; no media or databases were downloaded.

The Service selector and original replicas were restored. The operation completed in 16.84 seconds, with no rollback or errors. Source/receipt/image/manifest-label and Linux service-file hashes passed. Standard executor receipt context hash remains `null`; the extra labels accurately describe the measured Git source inventories, not an invented Docker tar hash.

The parent was notified immediately once Ready. This operator submitted **zero model requests, renders, or business recovery calls**. The parent exclusively owns the subsequent single reviewer recovery and its outcome.

`evidence/` contains 17 downloaded lightweight JSON receipts (about 340 KB), including build, smoke, clean Git provenance, newline proof, before/desired deployment, protected resources, idle checks, backup receipt, window and completed receipt. `offline-deployment-audit.json` independently verifies the stored deployment delta and source identity. The deliberately disabled older `deploy_retry_executor.py` is an unfinished handoff outline and was never used; the executed operator is `deploy_retry_executor_v1.py`.
