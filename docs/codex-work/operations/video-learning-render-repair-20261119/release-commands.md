> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/video-learning-render-repair-20261119/release-commands.md`；原文件 SHA-256：`1c4ad269194b683a9e313add231c2cef118f11d63f87754404b7f96b2485986e`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../README.md)

# Commands after the real repair revision is committed and pushed

All prepared helpers live under `D:/codex-work/operations/video-learning-render-repair-20261119/`. Remote dedicated root is `/root/video-releases/video-learning-render-repair-20261119`.

The trusted render host is retained, not modified: both build proof and operator require `animation_method_host.mjs` SHA-256 `df7253899839aef3df112e13ccc3a2c2ca09ceee96b037c84800021e1dd26d81`. Existing engine/resource admission bindings and guards remain unchanged.

1. Launch the clean Git build from Windows (replace `<FULL_SHA>` with the reviewed, pushed 40-character revision):

```powershell
python D:/codex-work/operations/video-learning-render-repair-20261119/launch_executor_fix.py --revision <FULL_SHA> --base /root/video-releases/video-learning-render-repair-20261119
```

2. Upload only operator helpers after the launcher creates that remote directory:

```powershell
python D:/codex-work/operations/video-learning-render-repair-20261119/upload_operator_helpers.py
```

3. Once `build/build-completed.json` confirms success, run the additional offline source-byte proof on the server:

```sh
python3 -B /root/video-releases/video-learning-render-repair-20261119/verify_extra_image_bytes.py --revision <FULL_SHA>
```

4. Prepare the exact deployment plan on the server:

```sh
python3 -B /root/video-releases/video-learning-render-repair-20261119/bridge_executor_rollout.py prepare --revision <FULL_SHA> --output /root/video-releases/video-learning-render-repair-20261119/deployment-r1
```

5. Inspect its output/delta, then use the returned exact `plan_sha256`:

```sh
python3 -B /root/video-releases/video-learning-render-repair-20261119/bridge_executor_rollout.py apply --revision <FULL_SHA> --output /root/video-releases/video-learning-render-repair-20261119/deployment-r1 --plan-sha256 <PLAN_SHA256> --execute
```

6. Read-only post-deployment audit, safe while a later learning task runs:

```sh
python3 -B /root/video-releases/video-learning-render-repair-20261119/audit_deployed.py --revision <FULL_SHA> --output /root/video-releases/video-learning-render-repair-20261119/deployment-r1 --plan-sha256 <PLAN_SHA256>
```

Do not run the old fixed-root operator. Do not start a second build after ambiguous transport; inspect the persisted launch/build receipt first. The launcher uses existing ordinary dependency layers while rebuilding the complete committed application source, without application overlays. No command in this document has been executed as part of script preparation.
