> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`tmp/video-service/fresh-implementation-1117/release/README.md`；原文件 SHA-256：`abedd06d77ce1b3987a80eb005959fa7b4ce4b27395dd2f03f03428e8a03ffe0`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../README.md)

# 2026-11-17 从零重做 / WikiSkill 选择 发布准备

此目录是临时发布工具，不是待提交的业务源码。已准备，尚未构建、推送或部署。

三个目标：`executor`、`workbench`、`lab`；只使用 `latest@sha256:...`，以 Git SHA、源文件清单、context SHA、image labels 和 deployment annotations 识别版本。完整 Git 源码构建，可复用依赖层，不从旧应用镜像拼装业务源码。

`animation_creation_policy.py`、`animation_effect_adoption.py`、`animation_laboratory_library.py` 等后端文件，在 executor/workbench 的完整源码 context、离线镜像 smoke、运行中 Pod 均逐文件核对。lab 只作为 HTTP/UI 代理，使用原有精确 allowlist；其入口、代理及 UI/OpenAPI 文件单独核对，不扩大职责。

未改动 control、worker、renderer；workbench 的 `film-static-assets` renderer init image 保持原值。executor 的 `prepare-private-root` 则跟随其自身镜像。发布仅修改目标 app/init image 与 release annotations，通过 resourceVersion 和旧镜像测试防止覆盖并发变更，检查完整 spec 保持一致。

## 使用

1. 根 Agent 完成集成、校验、提交并推送后，将 `release_video_services.py`、`launch_release.py`、`deploy_release.py`、`backup_live.py` 上传到一个新的 ARM 主机目录，比如 `/root/video-releases/fresh-implementation-20261117-r1/`。
2. 必要时先通过 `backup_live.py --output NEW_DIRECTORY --database-root /var/lib/video-animation-creation` 在线备份 executor 元数据。若备份其他 host-mounted SQLite，追加明确的 `--database-root`。该脚本不自动推测 Ceph PVC 在宿主机的路径，不读取 Secret 或音视频文件。
3. 启动构建：

```sh
python3 -B /root/video-releases/fresh-implementation-20261117-r1/launch_release.py \
  --revision FULL_40_CHARACTER_COMMITTED_SHA \
  --base /root/video-releases/fresh-implementation-20261117-r1 \
  --assets-dir /root/video-process-20261105/feedback-20261106/animation-capability-redesign/agent-creation-phase1/creation-product-r1/authoring-libraries-release-r1/assets
```

默认只构建、推送并完成离线 smoke，不部署。追加 `--deploy` 会在三个镜像全部 smoke 通过后依次部署。保留默认模式时，可随后执行：

```sh
python3 -B /root/video-releases/fresh-implementation-20261117-r1/deploy_release.py \
  --base /root/video-releases/fresh-implementation-20261117-r1
```

`--branch` 默认 `codex/video-capability-experiment-redesign`；若根 Agent 最终推送其他分支，启动时显式传入。只从指定分支可达的完整 commit 建立新的 detached worktree，不复制未提交代码。每次 launch 使用全新 base；没有自动构建或模型重试。

查看 `launch.log`、`build/build-completed.json`、`build/*-image-smoke.json`、`build/*-deployment-{before,patch,after,completed}.json`、`build/*-runtime-verification.json`。成功部署要求实际运行 Pod 的 UID/containerID/image digest 在源文件核验前后保持一致。

`deploy_release.py` 可重新核对已完成的角色后跳过它；若某个角色已 patch 但尚未形成 completed receipt，需要根 Agent 先读取 before/patch/当前 deployment，手动核清状态后继续，不自动覆盖同 digest。

## 验证范围

本地 `check_scripts.py` 检查 Python AST、各角色逐文件与 UI 静态 smoke、metadata/spec patch 保留、错误 digest 拒绝。无 Docker/集群写入，无 build/push/deploy，无模型请求。服务器仅做部署、挂载、源码和 assets 的只读核对。
