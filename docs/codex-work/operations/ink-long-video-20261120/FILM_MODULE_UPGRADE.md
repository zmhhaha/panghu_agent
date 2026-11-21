> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-long-video-20261120/FILM_MODULE_UPGRADE.md`；原文件 SHA-256：`13221646bdaf0c83fd6fa03a05254f93faaec1ad7bb18e2b3f32a8819c99a494`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../README.md)

# Explicit-silent film-module role upgrade

The preparation tool is `film_module_upgrade_ops.py`. Run it on the existing
server with `VIDEO_SOURCE_ROOT` pointing to the clean new application checkout,
or with that checkout on `PYTHONPATH`. It never reads or exports Secret values.
It never applies Kubernetes objects or starts a model implicitly.

1. Finish the knowledge compilation using the old control deployment and
   verify its Worker has stopped. Keep its exact output and publication
   identity. Do not restart the shared proxy during an active SSE exchange.
2. Complete the ordinary five-image build from one clean Git commit, using
   `latest@sha256:...`. Keep the new Pi image, renderer runtime fingerprint and
   original current catalog. The renderer runtime JSON passed below must have
   its measured `image_digest`, as required by the normal film-release builder.
3. On the server run:

   ```sh
   python3 -B film_module_upgrade_ops.py prepare \
     --output /ABSOLUTE/NEW/film-module-v2-candidate \
     --worker-image REGISTRY/agent-pi:latest@sha256:ACTUAL_DIGEST \
     --renderer-runtime /ABSOLUTE/renderer-runtime.json \
     --release /ABSOLUTE/actual-published-wikiskill-release.json
   ```

   `--release` is optional: omission offers an immutable empty catalogue. An
   actual published release allows the real candidate to exercise Skill tools;
   no fake publication record is manufactured. `prepare` reads the live API
   deployment's current public ConfigMap. For offline rehearsal, use
   `--current-config /path/to/public.json`. The old module pool is the default
   source of model and isolated runtime settings; `--source-pool` overrides it.
4. Review `catalog-preparation.json`, `public-disabled.json`, and
   `host-disabled.json`. Only the current module route pointer changes. Its new
   binding and pool remain disabled, with no acceptance and no UID reservation.
   Former bundles, accepted bindings, callers, pools and UID ranges remain exact.
5. Permanently reserve three fresh candidate UIDs using the existing server
   operator ledger. Use already prepared, namespace-scoped candidate bootstrap
   and upstream Secret objects through their normal trust boundary. Then run:

   ```sh
   python3 -B film_module_upgrade_ops.py render-probe \
     --prepared /ABSOLUTE/NEW/film-module-v2-candidate \
     --name candidate-silent-film-v2 --worker-image REGISTRY/agent-pi:latest@sha256:ACTUAL_DIGEST \
     --node ACTUAL_NODE --uid-start FRESH_RESERVED_UID \
     --bootstrap-secret EXISTING_PROBE_BOOTSTRAP --upstream-secret EXISTING_PROBE_UPSTREAM
   ```

   This calls the existing administrator-only
   `agent_service.deploy.acceptance_probe.render_probe`. Its `proxy` and
   `worker` entrypoints run the real Pi image and exact `candidate_scenario`
   inputs. Applying the resulting `probe-objects.json` starts a paid candidate
   (including one connectivity request, with at most eight requests total).
   Root must explicitly execute that reviewed apply. This is not a business
   movie and does not adopt or publish anything.
6. After the original Worker exits successfully, run `collect --prepared ...`
   while the candidate proxy is still available. It reads original request,
   result, stop evidence and the externally observed Pod. `assess --prepared ...`
   then verifies the stopped handoff, exact v2 bundle/binding, input hash,
   original module JSON and actual host-observed Skill tool events. A model or
   protocol failure is preserved, with no automatic retry or new request key.
7. Execute the exact `original-module-package.json` in the new isolated formal
   renderer with its frozen silent film and real library bytes. Capture the
   actual 24 frames, repeat seeking, MP4, complete decode and original module
   identity. This uses the standard `assemble_film_production` and
   `hyperframes_film_execution`/export path. The separate long-film probe is
   useful renderer evidence, but cannot substitute for this candidate's source.
   Preserve an independently reviewed media report with:

   ```json
   {"status":"passed","actual_render":true,"input_module_package_sha256":"ACTUAL_PACKAGE_SHA",
    "frame_count":24,"duration_seconds":1,"complete_decode":true,"repeat_seek_verified":true}
   ```

   Those fields must describe actual captured evidence. The author Agent and
   unit tests do not create this report.
8. Once both captures are checked, run `prepare-enable --prepared ...
   --assessment-sha256 ACTUAL_ASSESSMENT_SHA --media-report ACTUAL_MEDIA_REPORT
   --production-uid-start FRESH_RESERVED_PRODUCTION_RANGE`. It verifies the live
   catalog still matches the original preparation and creates a technical
   admission bound to the exact candidate and media report. It writes
   `public-enabled.json`, `host-enabled.json`, and immutable
   `reviewed-config-objects.json`; it performs no deployment.
9. Root reviews those objects, applies them, and uses the existing full
   deployment helper to switch API/dispatcher/proxy config and image together.
   Reuse existing caller and secret references; do not rotate credentials or
   overwrite old immutable ConfigMaps. Verify readiness and old frozen pins.
   Only after the old compiler has stopped may this control rollout begin.
10. Check candidate cleanup by its exact labels/UIDs. Only then use the normal
    project → joint-plan → adopted-plan → production-run API with
    `audio_mode="silent"` for the actual five-minute film.

The old `animation_film_acceptance` and `animation_production_acceptance`
commands assess the earlier scene-based production role, not this composable
module role. Do not use their reports as acceptance for this new binding.
