# Production Agent capsule admission audit

Run `production_isolation_admission.py` from committed Git on the Linux cluster
operator host. It requires `kubectl` operator access and reads only the public
Dispatcher deployment/configuration. It never reads a real Secret, creates a
Pod, starts an Agent task, changes an active worker or installs admission policy.

```sh
python3 ppt_service/tests/acceptance/production_isolation_admission.py \
  --source "$PWD" --output /root/ppt-r4-admission-UNIQUE
```

The output directory must not already exist. Every provisionable retained pool
uses its exact image, host ConfigMap, UID reservation, runtime/profile and node
pins. The Pod shape comes from the committed provider's actual pure `_manifest`
renderer, with synthetic admission-only envelope/signing material. Historical
disabled pools without reserved UIDs are recorded separately: the real provider
already refuses to construct their Pods. Do not invent a UID or count them as
successfully schedulable. No API-returned Secret or real credential is saved.

The API server receives `create --dry-run=server` requests as the configured
Dispatcher, read-only observer and default worker ServiceAccounts. The identity
is explicit API impersonation by the operator, testing actual Kubernetes RBAC;
it is not evidence that a real Casdoor user authenticated. Production-shaped
positive controls and well-formed unsafe mutations are recorded individually.
Some invalid combinations can be rejected by built-in validation before custom
admission, so do not claim that every rejection proves the custom policy ran.

Exit 1 is expected when a dangerous mutation is accepted. Preserve that failed
report, do not overwrite it or call it a passing isolation result. The script
does not attempt to make an accepted dangerous Pod real.

The generated ConfigMap, ValidatingAdmissionPolicy and Binding are review
inputs. They cover only the `agent-workers` namespace, retaining the trusted
root WorkerHost and its fixed minimal mount-helper capabilities/AppArmor
boundary. They do not globally change Pod Security Admission to restricted or
alter unrelated candidate namespaces, current Agent routes, UIDs or images.

Before installation, check the configuration UID/resourceVersion/hash remains
unchanged and inspect current live allocations. Obtain the normal guarded
publication lock, preserve existing objects and install only a previously absent
policy *without its Binding* first. A policy without a Binding is inert. Wait
for the API server's asynchronous `.status.typeChecking.expressionWarnings` and
conditions; `create --dry-run=server` alone returns an empty status and proves
API/schema acceptance, not successful CEL type checking. Any warning or compile
failure must be repaired before adding enforcement. Do not claim this operator
already performed installation or rollback.

After a reviewed policy is active with its exact parameters, rerun the entire
audit into a fresh evidence directory. All provisionable historical baselines
must still be admitted, both unauthorized ServiceAccounts denied and unsafe
mutations denied. Then prove a normal real execution still works and retain
physical-stop/cleanup evidence. API dry-run does not by itself prove worker
network/mount enforcement, actual create/observe/cancel, parser deadlines,
business recovery, owner authorization or publication acceptance.

Any later catalog publication introducing a new image/host or UID reservation
must update the admission parameters through the same guarded review path before
activating that pool. An unregistered pool must fail closed. Historical
parameters must remain while their immutable recovery references are retained.
