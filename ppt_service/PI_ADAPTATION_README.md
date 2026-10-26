# Pi execution profile adaptation — first slice and follow-ups

Latest follow-ups (2026-10-22): the independent artifact stager is now implemented and passed all 39 filesystem tests in a restricted Linux container; see [the staging contract](control/PI_STAGING.md). It is not wired into runtime or dispatch. The initial profile admission restrictions below therefore still apply.

Trusted `RuntimeSettings.model_input` can explicitly select `("text",)` or `("text", "image")` for a custom endpoint. Its default is `None`, preserving the exact scientific model definition. Wrong types, unsupported/duplicate/reordered inputs and a missing custom endpoint fail before process probing/launch. This constructor setting cannot be selected by the PPT job wire and cannot authorize production execution.

The real Pi 0.73.1 CLI was tested through the existing PPT Responses proxy using only synthetic IPv4 loopback providers and fake credentials. A real `read` tool call on a valid small PNG produces the SDK image-omitted placeholder by default; explicit image input transmits an `input_image` data URL whose decoded bytes and SHA256 exactly match the PNG. This proves transport, not visual quality or supplier-model approval. The initial fixture PNG was invalid and was replaced with a valid stdlib-generated PNG; no runtime image workaround was added.

Post-change coverage: 6 new image-input tests, 9 profile tests, 2 Responses-proxy tests and 40 scientific-runtime tests: 56 passed, 1 existing Windows symlink-permission skip. All four scientific actual-CLI loopback cases are included. Frozen adapter SHA256: `297e77e2a56fd9af50a77a2b0b6ae14fbbf2b2c87771afe18a780a9c5beca3f2`. Production readiness remains false. The original first-slice scope and evidence follow.


This is an offline, partial Pi-unification slice. `PiRuntime(settings)` still selects the scientific research profile; trusted server code may explicitly construct `PiRuntime(settings, profile=profile)`. There is no request-selected module, class path, callable, plugin loader, or profile registry. Both profiles use the existing single subprocess runtime, its Pi 0.73.1 command, empty workspace, `--no-session`, environment filtering, deadline, output limits, process-tree cleanup and fencing.

The scientific profile delegates directly to `ResearchJob.from_dict` and `build_research_prompt`. Its emitted provenance retains the exact legacy `retrieval_started_at`, `retrieval_finished_at` and `retrieval_interval_basis` fields; no domain fields are added to scientific output. Profile provenance is restricted to bounded, allowlisted strings and cannot overwrite model/runtime evidence.

`PptProfile(runtime_revision=..., skill_bundle_revision=...)` accepts a separate strict envelope: integer `schema_version=1`, `domain=ppt`, `engine=pi`, `profile=ppt-master`, phase, job ID, input snapshot SHA256, runtime/skill pins, creation/deadline/timeout, task metadata and an explicit nullable checkpoint descriptor. It does not turn PPT into a research topic or report. The nested task names come from the existing PPT staging contract, including attempt/session identity, requirements, assets, feedback and base version. The snapshot hash is controller-supplied lineage metadata, not recomputed from this reduced envelope; source staging/hash verification is still pending. Unknown fields and mismatched identities/pins are rejected. JSON is limited to 64 KiB, text and page counts are bounded, and delimiter characters in user text are escaped.

Only `plan_initial` with no assets/checkpoint/base version is admitted in explicit offline fixtures. Nonempty assets fail with `unsupported_staging`. Every later phase (`plan_options`, `prepare`, `generate`, `revise`) fails with `unsupported_recovery` before any subprocess probe or launch. The existing dispatcher requires project restoration for every continuation phase and binds confirmations to plan revision, input hash and selected source plan. This slice does not infer approval from an Agent recommendation or implement that restoration/confirmation handoff.

The Pi renderer reuses the existing PPT business stage instructions and translates tool spellings locally to the verified default Pi tools `read`, `bash`, `edit`, `write`. It retains full official PPT Master Default workflow, attribution guard, both confirmation gates, closed-source/no-invented-facts policy and candidate-versus-published distinction. It does not change the Hermes COMMON prompt. No source files are staged by this slice, so missing materials require truthful `needs_input` output.

A checkpoint descriptor is compatible only with `engine=pi`, the same runtime revision, `profile=ppt-master`, and `checkpoint_format=artifact-replay-v1`. Compatibility validation does not restore anything: even a compatible descriptor fails execution with `unsupported_recovery`. Hermes home/SQLite, native Pi sessions, unknown formats and cross-engine descriptors are not restored or converted.

PPT production readiness is unconditionally false in this implementation. Scientific capability/model/isolation flags cannot authorize PPT production execution. The old PPT UID 10000/drop-ALL container is not compatible evidence for the root-controller/unique-child-UID boundary. A separately verified Pi PPT image, reviewed full skill bundle, resource staging and profile-specific isolation evidence are required before production work can proceed. Fixture success is not production or model acceptance evidence.

Not implemented: PPT model execution, image builds/deployment, asset replay, native checkpoint restore, generic HTTP/queue service, Kubernetes integration, UI engine persistence, or controller/publisher changes. Existing Hermes workers, results and runtime configuration remain intact. U1 is an initial shared interface with compatibility coverage; U2 remains partial. This does not complete the overall Pi-unification goal.

Offline verification on Windows: 9 focused profile tests passed, including exact scientific prompt/provenance and actual fake-command PPT execution/cancellation/output limits. The no-network subset of the existing scientific runtime suite ran 36 tests: 35 passed and one host symlink test skipped. Four existing HTTP loopback CLI tests were deliberately not selected under the no-network constraint. The existing Hermes task/prompt suite also passed all 10 tests (total: 54 passed, one host skip across 55 selected tests). The existing mismatch fixture now uses deterministic unique workspace names, avoiding collisions from a coarse Windows monotonic clock. No model gateway, upstream, image build, SSH or deployment was used in that child scope.

Parent follow-up: the four excluded tests subsequently passed against IPv4 loopback-only synthetic HTTP services using the actual pinned Pi 0.73.1 CLI (4/4, 33.337 seconds, no skips). This checked two real SDK/tool rounds against the gateway request schema, bounded 429/503 retries, permanent authentication-error handling, and exactly-once research-result publication after a retry. Fake keys and controlled responses were used; no real model supplier, credentials or live deployment was involved. Combined verification: 59 tests selected, 58 passed, one Windows symlink skip. These are compatibility/mechanism checks, not actual PPT generation or production isolation acceptance.

Run focused coverage from the repository's `panghu_agent` directory with the existing Python environment:

```bash
PYTHONPATH=. python -m unittest discover -s ppt_service/tests -p test_pi_profile.py -v
```

Run the no-network scientific runtime selection from `tests/scientific_pi/runtime`, with `PYTHONPATH` set to the absolute `panghu_agent` path:

```bash
python -m unittest -v test_runtime test_jsonl_compactor test_model_retry.ModelRetryUnitTests test_gateway_compat.GatewayCompatTests.test_factory_completions_defaults_and_preserves_gates test_gateway_compat.GatewayCompatTests.test_responses_has_token_cap_without_completions_compat test_gateway_compat.GatewayCompatTests.test_direct_provider_can_clear_only_the_fixed_profile test_gateway_compat.GatewayCompatTests.test_compatibility_and_token_limit_fail_closed_before_launch
```
