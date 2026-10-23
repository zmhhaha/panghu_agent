# Offline Pi PPT candidate evidence — 2026-10-22

This is an independently tagged TEST-ONLY candidate. Its fixed entrypoint runs the smoke harness; it is not a deployed PPT executor. Existing services, image tags, database, and publication gates were not changed. No model supplier was contacted.

## Verified candidate

- Tag: `ppt-service-pi:candidate-offline-r19-20261022-7f92-v2`
- Local image ID: `sha256:250b5cbd45beac1965d0d4023f0dd9842a93b6680498af38721130fb103e12b4` (not a registry manifest digest).
- Existing local PPT base: `sha256:6e9bdb213f75120171d712e9bd037759cedfce6aced2caff0dae01d5fa08583a`.
- Existing local scientific Pi donor: `sha256:fe9129357e91ee371123663476ae41e3308c01013953fe227e20e5a2b7300a45`.
- Context SHA256: `56cafdbdf04ccb62e8f3cb02d0c83d2f4e6c5c9daf44a8e0e054b3dfe919efd4`.
- Imported adapter SHA256: `297e77e2a56fd9af50a77a2b0b6ae14fbbf2b2c87771afe18a780a9c5beca3f2`.

The legacy Docker builder used only verified locally loaded source tags, `--pull=false`, `--network none`, explicit source files, and no installation or remote ADD. Node and the complete installed `/opt/pi` tree came from the donor. PPT tools, fonts, browser and Python packages came from the PPT base. The inherited Hermes executor ENTRYPOINT was explicitly replaced. The image still retains unused Hermes layers and an editable-package import finder; no size or token saving is established.

## Actual checks

Phase A passed: Node 24.21.0, Pi 0.73.1, ARM64 Linux, Node ABI137, no unresolved Node shared library; imports of pptx, PIL.Image, lxml.etree and playwright.sync_api; official PPT Master attribution guard; Noto Sans CJK SC resolution; Chromium executable metadata. Chromium was not rendered by this harness. The fixed global Python sitecustomize is the system apport-hook installer, and the global editable Hermes finder remains present. `-I` excludes environment/user-site injection, not global `.pth` hooks. The inspected module set contained no loaded Hermes executor modules; this is not a whole-package audit.

Phase B passed four production-Linux-runtime-path cases using a controlled fake Pi executable and a TEST-ONLY profile: normal completion, cancellation, deadline, and safe injected cleanup error followed by recovery. UIDs/GIDs20000–20003 each had matching real/effective/saved IDs, no supplementary groups, zero effective/permitted/inheritable/ambient capabilities, no-new-privileges, seccomp mode2 and RLIMIT_NPROC32. Root controller data, `/proc/1/environ` and a different-UID sentinel were unreadable. No live UID process or detached writer remained; the heartbeat stopped. The error was injected only after real cleanup succeeded, so the fence/recovery test does not claim a naturally unkillable process was exercised.

The controller had exactly SETUID, SETGID, CHOWN, DAC_OVERRIDE and KILL (mask0xe3). Docker used readonly root, no host binds/devices/socket, default private PID, private IPC, no network, disabled DNS/IPv6, 1GiB memory, 2CPUs and pids128, with bounded work/tmp tmpfs. The base's fresh anonymous `/opt/data` volume was writable but unused; readonly root does not make that mount readonly.

## Cleanup and limitations

Final container `e4419c6b0e919d150281a28837ec39c658daf6a3eb2d47057672d44f81f75ff5` was removed with `--volumes`; owned anonymous volume `847911d5e8cd39226f86a2209d8a76413129b6aed914987ba922ee29dbae7511` was verified absent. Temporary source tags were removed. Container and script exits were both0. The candidate image and its isolated evidence remain available; no unidentified volume was pruned.

Initial tooling attempts are retained: a Docker optional Volumes metadata field needed an index lookup; the first candidate then stopped because the harness expected Pi's version on stdout whereas Pi writes it to stderr. Correcting the harness produced the separately tagged v2 candidate. No shared runtime workaround was made.

Raw evidence is retained in the ignored local `.runtime/pi-candidate-r19/20261022-7f92/v2/remote-evidence` subtree and remote `/root/ppt-pi-candidate-r19-20261022-7f92-v2`. The runtime and skill acceptance flags and image digest used by the fake process harness were explicitly synthetic test inputs. Actual Pi model/tool/image transport inside this image, durable never-reused UID allocation, asset/replay handoff, dropped-UID official preview, dispatch/UI integration, real model generation, K8s, and PowerPoint manual acceptance remain pending. PPT production readiness remains false.
