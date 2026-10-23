# PPT Service execution image

This directory builds the dedicated ARM64 worker. It does not deploy a service,
start Hermes, or send a model request. The image entrypoint is
`/opt/hermes/.venv/bin/python -m ppt_service.worker.execute`; it replaces the
inherited s6/private-agent startup. The worker module is maintained separately.

## Fixed inputs

- PPT Master commit: `c350bc31e6d43b7bcf5234183d650ba8f9f4c9d7`, Skill `6.7.2`.
- The entire local Git archive is unpacked at `/opt/ppt-master`. Attribution,
  workflow, references, scripts, templates and licenses are retained. Both the
  archive SHA-256 and Git archive commit header are checked during the build;
  the unmodified upstream attribution guard must pass.
- The base is an explicit digest input. The initially authorized base is
  `arm-cluster-master:5000/hermes-intelligence@sha256:1398c1eddde05516af6e7991b809c3f1592ec503fe9ce901212ed6a95fb96d91`.
  Its runtime is Debian 13 / Python 3.13.5, although the ARM64 host may use another
  Debian release. Reusing a base does not remove its historical layers. Audit a
  replacement base before use; never put home/session/key material in the context.
- Python additions are exact pins in `requirements-worker.lock`; the inherited
  Hermes environment and system packages also appear in build-time inventories.
  These pins are not a hash-locked offline wheelhouse. APT repositories remain
  external build inputs, so the build is not claimed to be bit reproducible.

## Prepare the local vendor archive

From Windows, using the already available local PPT Master checkout:

```powershell
pwsh -File panghu_agent/ppt_service/scripts/prepare_vendor.ps1 -PptMasterRepository D:\github\research\.cache\ppt-master
```

The script refuses to overwrite an existing archive. It uses `git archive`
directly into `.vendors/ppt-master.tar`, never a network clone and never the
checkout's uncommitted files. An existing validated archive can be reused.
The build context is an explicit tar allowlist containing only the Python
package, image build inputs and that archive; uploads, sessions and repository
metadata are excluded even on legacy Docker builders.

## Build on native ARM64

From the service directory on a host with Bash, Git, tar, sha256sum and Docker:

```bash
bash build_worker.sh \
  --base-image arm-cluster-master:5000/hermes-intelligence@sha256:1398c1eddde05516af6e7991b809c3f1592ec503fe9ce901212ed6a95fb96d91 \
  --image ppt-service-worker:arm64-c350bc31-verify
```

A missing base digest, floating base, missing archive, wrong source commit or
`latest` output tag fails before building. The default builds into the local
Docker image store. `--push` explicitly pushes the requested tag after a
successful build; build verification does not require it. There is no runtime
source clone or package installation. Non-ARM64 builders require an independently
configured ARM64 execution environment; this script does not install emulation.

The Dockerfile bootstraps pip with the interpreter's bundled `ensurepip` because
this base's uv-managed venv has no pip. It installs binary Python wheels, runs
`pip check`, installs the Playwright-managed Chromium build and its system
dependencies, and installs Noto CJK fonts. The tested dependency set preserves
Hermes' exact Pillow 12.3.0 and cryptography 50.0.0 requirements and upgrades the
base's incompatible MSAL 1.36.0 to exactly 1.39.0. Browser binaries are readable
by UID 10000. A missing ARM64/Python
wheel fails the build instead of silently compiling an unverified extension.
PyMuPDF is an AGPL dependency with a commercial licensing option; retain its
license and resolve distribution obligations for the intended service packaging.

## Verification and provenance

During build, `check_runtime.py` runs as UID/GID 10000 and checks:

- ARM64 architecture and the upstream attribution guard;
- dependency imports and installed versions against the exact pin file;
- Noto CJK availability;
- Playwright Chromium startup and an in-memory Chinese text PNG.

It does not launch the default worker entrypoint, a Gateway, a model, Office,
or presentation generation. Re-run the same check without network or credentials:

```bash
docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=256m \
  --tmpfs /work/home:rw,nosuid,nodev,uid=10000,gid=10000,size=64m \
  --entrypoint /opt/hermes/.venv/bin/python \
  ppt-service-worker:arm64-c350bc31-verify /opt/ppt-service/check_runtime.py
```

Build inventories live at `/opt/ppt-service/build-info/`: `pip-freeze.txt`,
`dpkg-packages.txt`, `requirements-worker.lock`, `ppt-master.commit`, and
`ppt-master.archive.sha256`. Successful image construction establishes dependency
and browser availability, not PPT quality, PowerPoint rendering, native chart
editability, Gateway API compatibility, or a production network sandbox. Those
remain separate executor/sample acceptance checks.

Runtime defaults are `PYTHONPATH=/opt/ppt-service`, `HOME=/work/home`,
`HERMES_HOME=/work/home`, `HERMES_WRITE_SAFE_ROOT=/work`, and
`PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright`. The deployment must mount only the
authorized task input/base and writable attempt directories, provide a writable
bounded `/tmp`, and stop the complete process tree before collecting artifacts.
No credentials are built into this image. Build results and remaining limitations
are recorded separately in `build-report.md`.
