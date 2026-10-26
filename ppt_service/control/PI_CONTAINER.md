# Pi attempt container boundary

Updated 2026-10-23. Production model admission and the website Pi route remain disabled.

Follow-up: the same actual-Pi container self-test now also passes with a full project/job/attempt/token/phase-bound ModelGrant. Two requests consume its budget, a third receives429 before backend contact, usage includes job/attempt/phase, and proxy close revokes the grant with inflight0. Report87290e6e7c9ab4401f4e92258ab9da95dbe7f58e50ed1b955288d04a6c5db8a5 and71-test PostgreSQL/proxy evidence are documented in PI_MODEL_PROXY.md. The transport-only measurements below are the preserved earlier snapshot.

## Fixed plans

`plan_attempt_container` renders a trusted Docker argument vector, not a shell string. It requires the exact canonical layout `data_root/projects/{project}/attempts/{job}/{attempt}-{token}` and exact input/task/base/project/result/audit children. Links/reparse points in every path ancestor are rejected before resolution; aliasing an input to project is rejected even inside the same root. Only reviewed full lowercase SHA256 image IDs are accepted. Container names use the full attempt UUID rather than an eight-character job prefix.

The controller runs as root with exactly CHOWN, DAC_OVERRIDE, KILL, SETGID, SETUID (mask0xe3), no FOWNER/SYS_ADMIN. Readonly root, network=none, ipc=none, NNP, CPU/memory/pid limits, fresh tmpfs `/tmp` and `/work/runtime`, RO inputs/task/base and RW project/result/audit are explicit. No host environment, envfile, HOME, socket or device is forwarded by the plan. The image still has an inherited unused `/opt/data` anonymous volume; this is not a claim that every container mount is readonly. Docker --rm removes that owned anonymous volume along with the container.

`plan_domain_smoke` adds a readonly code mount and a fixed interpreter/script entry. It accepts no arbitrary command, model endpoint or credentials:

```python
plan = plan_domain_smoke(config, attempt, layout,
                        pi_uid=31800, code_root=reviewed_code)
subprocess.run(["docker", *plan.argv], check=True, timeout=120)
```

Code at the fixed path is trusted operator-reviewed code. A production adapter must additionally bind source/image/profile/tool revisions to an authorized job; this test plan does not implement that production admission.

## Corrected offline image

Image: `ppt-service-pi:domain-offline-20261023-a1`

Local immutable ID: `sha256:2a09398d26a387ac79b3b7b635202062ee7856f151b47eba0cb773b7e020e1a1`.

Built with network disabled and no downloads from previously verified base `sha256:250b5cbd45beac1965d0d4023f0dd9842a93b6680498af38721130fb103e12b4`, using `images/pi_candidate/Dockerfile.domain`. The derivative only makes `/work` and domain mountpoint directories root-owned0755, sets USER0:0 for the trusted supervisor, and adds offline-only labels. It retains the test-only inherited entrypoint and unused Hermes layers. The host verified the local base tag resolved to that exact ID before build. This ID is a local image configuration/content ID, not a pushed registry manifest digest.

The old image's `/work` was owned by the inherited Hermes UID. The smoke correctly rejected that replaceable parent before starting Pi. Fixing image ownership preserves the required boundary rather than relaxing the check.

## Actual Pi verification

`images/pi_candidate/domain_smoke.py` is a fixed OFFLINE TEST ONLY supervisor. It validates actual `/proc/self/mountinfo` modes, tmpfs types, protected domains without nested override mounts, root-owned immutable code/inputs/parents, reviewed runtime hashes and exact five-cap controller status before handoff. An exclusive root-private ledger reserves Pi31800 and preview31801; preview is reserved but not executed in this test.

It runs real Node24.21.0 and Pi0.73.1 through PiRuntime with `fixture_mode=False`, using a separate explicit TestOnlyProfile and synthetic readiness flags. PptProfile remains production_ready=False. The fixed synthetic Responses backend and existing model proxy are both IPv4 loopback-only inside a network-none container; no external model is called. The upstream protocol uses synthetic keys and fixed tool arguments, not a model-selected shell command.

The real Pi bash tool invokes immutable `domain_probe.py`, which reads `/work/inputs/source.md`, attempts overwrite/create in every RO domain, checks controller-sentinel denial, and writes `/work/project/probe.txt` and `/work/result/probe.json`. Actual child credentials measured: resuid/resgid=[31800,31800,31800], groups=[], CapEff/CapPrm/CapInh/CapAmb=0, NoNewPrivs=1. Bounding capabilities retain0xe3; this is not a claim all bounding capabilities are zero. Every RO write failed with EROFS(30). Child environment contains only the reviewed runtime names and Pi's own `PI_CODING_AGENT=true`/`PI_SKIP_VERSION_CHECK=1` (verified against the pinned CLI source).

Two real Pi->proxy->synthetic-backend SSE requests completed, call/output IDs matched, provider synthetic key replaced the worker key upstream, and synthetic usage was known. Those counters are fixture values, not measured supplier billing; job attribution in this transport-only ledger is not populated. Source hashes/bytes were unchanged. Combined UID stop evidence was obtained and no runtime UID process remained; the container exited0 and exact-name absence was verified.

Authoritative run artifacts retained on server: `/root/ppt-pi-domain-20261023-a5/`, including source archive, redacted plan, execution log and host data. Source archive SHA256 `093c68f0d3b0c81767eba01b0794695b5609550467d1a67307ffbb82bc84ba75`; runtime adapter SHA256 `297e77e2a56fd9af50a77a2b0b6ae14fbbf2b2c87771afe18a780a9c5beca3f2`; report SHA256 `9dcac1a55b6ece785bd267e4bf0c04b6d03171902e4e3c0b2595891cf20cb819`.

The first runs were safely refused for package resolution, inherited /work ownership, and missing allowlisting of Pi's two internally generated flags. Their logs are retained in separate a1-a4 directories; failure is not reported as success.

## Verification and remaining boundaries

Container plan tests: Windows13 total,12pass1symlink-privilege skip; independent Linux13/13pass0skip. Actual isolated Pi round: completed exit0 with hashes and a controller-authored report. This is neither PPT generation, model reasoning/vision validation, preview rendering, a production image rollout, nor publication authorization. The fixed entry is a reviewed transport/isolation self-test, not a user-job executor. Real task-bound model proxy credentials, business profile admission, container lifecycle recovery and live website dispatch remain to be connected and verified.
