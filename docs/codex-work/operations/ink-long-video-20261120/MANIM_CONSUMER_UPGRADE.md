> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-long-video-20261120/MANIM_CONSUMER_UPGRADE.md`；原文件 SHA-256：`9f02b29974794487412a0a86b57defa05290f29c838990ddb2faa10e9da3ff9c`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../README.md)

The original admitted Manim producer and original author package are reused.
No new model, recipe, Cairo, speech, or creative request is made. The new
renderer runs the same module sources, asset bytes, recording bytes and film
clock with new image-owned host files. This is an infrastructure replay and
does not establish artistic acceptance.

Actual ARM replay evidence:

- Renderer: `arm-cluster-master:5000/video-motion-renderer:latest@sha256:af8a17ba948d2c17bdaf0a9e176508c2a5bc601285684088892f71af5f649475`
- Original producer operation: `5b6f314d-fc4b-49b4-b752-464e3c1d47f3`
- Original admission: `b030d9065b4369e15198aea4d24dd88d30e9c5f6f4d498c7559173a0c223f506`
- New snapshot: `1891d27496f764b39a0386479e2b10404cd38124b45c227bb5e0d04d3429945e`
- Actual output: 384 frames / 16 seconds, H.264/AAC, full video/audio decode passed.
- Media SHA: `da943b833e13809a1e2f58662517850bf3a2ef479727533f567b92fbd9aa7b7f`; exactly matches original admitted output.
- Docker container was observed stopped with exit code 0 and no OOM.

All ARM evidence lives in:
`/root/video-releases/ink-film-20261120/manim-consumer-replay`

Completed on this release: the new consumer admission is
`daa717fe029459bca902f87b0207cc0fbeee15f430b0659ea8143054d02a57a8`.
Root applied the reviewed three-service plan once, and all services became
Ready. Live postflight verified the exact planned specs, actual 300-second
execution capabilities, same new Manim producer capability binding, fixed ink
asset bytes, unchanged readable old ConfigMaps, three original user films,
six original module packages and fifty original recordings. Report:
`/root/video-releases/ink-film-20261120/film-rollout-cas-v1/postflight-live-release-and-history.json`.

For a future replay, after the independent runtime agent supplies the real new
execution admission, run on ARM (never reuse the old admission):

```sh
python3 -B /root/video-releases/ink-film-20261120/manim_consumer_upgrade_ops.py collect \
  --root /root/video-releases/ink-film-20261120/manim-consumer-replay \
  --image arm-cluster-master:5000/video-motion-renderer:latest@sha256:af8a17ba948d2c17bdaf0a9e176508c2a5bc601285684088892f71af5f649475 \
  --execution-admission ACTUAL_NEW_EXECUTION_ADMISSION_JSON
python3 -B /root/video-releases/ink-film-20261120/manim_consumer_upgrade_ops.py admit \
  --root /root/video-releases/ink-film-20261120/manim-consumer-replay \
  --image arm-cluster-master:5000/video-motion-renderer:latest@sha256:af8a17ba948d2c17bdaf0a9e176508c2a5bc601285684088892f71af5f649475
```

`admit` reopens the real original producer and validates the original admission
again. It writes the new admission's exact hash-addressed evidence directory,
preserving every old admission and producer record. It also emits
`admission-report.json`, `film-manim-admission.json`, and
`film-manim-consumer-context.json` in the replay directory. It does not change
the running executor admission pointer or Kubernetes configuration.

Prepare the combined rollout plan using the already captured baseline:

```sh
python3 -B /root/video-releases/ink-film-20261120/prepare_film_rollout_config.py prepare \
  --output /root/video-releases/ink-film-20261120/film-rollout-cas-v1 \
  --build /root/video-releases/ink-film-20261120/build/build-completed.json \
  --execution-admission ACTUAL_NEW_EXECUTION_ADMISSION_JSON \
  --manim-report /root/video-releases/ink-film-20261120/manim-consumer-replay/admission-report.json
```

The resulting plan has two immutable ConfigMaps and three Deployment JSON
patches with UID, resourceVersion, and complete-spec compare-and-swap tests.
Only root applies them. If the baseline changes, capture a new named directory
and regenerate; do not weaken the comparisons. Rollout order is motion
executor, creation executor (same new Manim admission), then workbench.

The film asset init container uses the exact new renderer and verifies/copies
the fixed ink drawing bytes. Old motion runtime configuration, old method
renderer image, original Manim producer image and resource admission, caller
Secret references, volume mounts, services, selectors and replicas are
preserved. No application source edit is part of these operator scripts.
