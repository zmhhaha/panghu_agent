> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-film-recovery-20261121/README.md`；原文件 SHA-256：`33a69526df36364dca1443971245f6793471aad0f1df338a40d91e478d33f8af`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../README.md)

# Film recovery: exact two-service image rollout

The authorized clean revision is `ea6ea02aa84bbf4772c4940005f1e6e6ae3ad7a7`.
`build_release.py` built ordinary control and workbench Dockerfiles only, with
`latest` tags, Git/context labels and immutable pushed digest receipts. It
checked image source bytes without network access in read-only containers.

The concrete root-reviewed artifacts are `rollout-plan.json`, the two
`*.patch.json` files, each `*-rollout-before.json` and `*-desired-spec.json`.
`root-plan-review.json` records root's independent diff/source review.
`preflight-pre-review.json` is the read-only quiet/config/protected-spec check.
No ConfigMap, native worker/admission, renderer or other deployment is changed.
Workbench init `film-static-assets` stays on the existing renderer `af8a17…`.

`rollout.py apply-one` requires an explicitly reviewed canonical plan SHA,
which `rollout.py preflight` prints. It writes a durable local intent first,
repeats the exact reviewed physical-quiet helper, checks all original target
UID/resourceVersion/specs before the first mutation, and applies one JSON CAS.
An uncertain SSH result must be reconciled from actual state; intent files
prevent automatic replay. After the first target, apply checks that exact
desired spec remains and tests the remaining original target CAS again.

After authorized apply, `rollout.py verify` checks both exact desired specs,
observed Deployment generations, complete Ready rollout, exact current Pod and
owner identities, declared and runtime image digests, running source bytes,
successful unchanged renderer init, all protected specs and mounted ConfigMap
data. It emits local raw evidence plus `rollout-verified.json` using schema
`video.film-recovery-rollout-verified.v1`. Evidence paths are relative to this
directory and independently hashable by the attempt-2 driver.

Rollback templates preserve prior exact specs/digests. If rollback becomes
necessary, `prepare_rollback.py --role ROLE` reads current state and prepares
a fresh UID/resourceVersion/full-spec CAS to the original spec without applying.

These helpers submit no publication, Agent, production-run or paid model work.
Applying either rollout still awaits root's explicit authorization.
