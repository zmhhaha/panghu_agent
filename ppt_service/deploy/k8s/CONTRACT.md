# PPT Kubernetes deployment contract

The website is deployed in `ppt-service`, with public origin
`https://ppt-service.panghuer.top`. [README.md](README.md) is the operator entry point.
The deployment provides the website, OAuth proxy and PPT file storage. Database
`ppt_service` is isolated by its own roles inside the existing `data/postgres`
instance. Routine website releases preserve the operator's existing execution
settings and do not submit model work. The trusted execution deployment is
documented under [Hermes](../hermes/README.md).

## Server build and deployment

Run `../../deploy.sh` from the server checkout. It normally builds and verifies
the ARM64 site image, pushes it to `arm-cluster-master:5000`, and then deploys the
verified registry digest. `../../build.sh` can run separately, followed by
`../../deploy.sh --skip-build`. Only a successful verified push writes the matching
repository manifest digest to `../../rendered/image.txt`. A local Docker image ID
or mutable tag is not that digest. `PPT_SITE_IMAGE` can provide an explicit
verified digest. The original Hermes worker build is `../../build_worker.sh`.

The ordinary deployment command synchronizes three Vault-backed Secrets,
uses the operator-side administrative connection to `data/postgres-0` to
initialize a new dedicated logical database or migrate a complete existing one,
checks runtime privileges and storage, and rolls out the website. It does not
create, scale, upgrade or reconfigure the shared PostgreSQL StatefulSet/PVC, nor
restart the retired `ppt-postgres` instance.
Role passwords are set atomically with each CREATE ROLE. Only after all bootstrap
database/schema grants succeed does initialization write database comment
`ppt-service-bootstrap-v1`. Reuse requires that completion marker as well as the
expected roles and ownership; it does not unconditionally reset existing
passwords. A missing marker or other partial initialization stops deployment and
preserves the database for operator investigation. Manually adding a comment is
not a substitute for completed initialization. A deployment lock prevents
overlapping helper runs.

`PPT_CLIENT_ID` or `PPT_AUTH_CONFIG` supplies the public authentication settings;
a later deployment may reuse valid settings already in the cluster. Upgrading
from email-list admission requires regenerated settings: legacy membership fields
are rejected. `PPT_OWNER_CONFIG` is obsolete and stops deployment before cluster
changes. Casdoor and Cloudflare are configured by the operator in their respective
dashboards.

The `base` and `migrate-job` directories are helper inputs, not a separate
environment. The helper coordinates resource ordering and the one-off migration;
reapplying raw templates is not the normal upgrade command. Rendering and server
dry-run alone do not verify SSO, CSI mounts, CNI traffic or model execution.

## Targets and topology

| Target | Resources |
| --- | --- |
| `base` | Namespace, tokenless service accounts, configuration, PPT file PVC, same-Pod website/OAuth proxy, ClusterIP website Service and network policies |
| `migrate-job` | One-off schema migration using the same site image and its own database role |
| Existing `data/postgres` | Shared PostgreSQL instance with dedicated `ppt_service` database and `ppt_migrator`/`ppt_runtime` roles; administered outside PPT workloads |

Only `ppt-site:4180` is exposed as a website Service port. Uvicorn serves
`ppt_service.server.site:create_site` on `127.0.0.1:8000`, with
`--no-proxy-headers`; the same-Pod oauth2-proxy is its caller. The trusted peer
list is exactly loopback. The site uses `/usr/local/bin/python`, working directory
`/opt/ppt-service` and built assets at `/opt/ppt-service/web/dist`.

Startup and liveness probes check loopback `/healthz`; readiness also requires
the runtime connection to read the current schema revision. oauth2-proxy uses
`/ping` and `/ready`. Website and proxy share a network namespace, so network
policy cannot isolate those two containers from each other.

Both containers admit all valid Casdoor-authenticated users. The proxy uses
`email_domains = ["*"]` and the API has no email-list membership check. Neither
container mounts or reads `ppt-owner`; old cluster ConfigMaps are left unused.
Resource ownership remains bound to the verified issuer and stable subject,
so opening admission does not expose one user's projects to another.

Casdoor enforces email verification through its registration and login policies.
The API does not additionally require or evaluate an `email_verified` claim.
It still requires a valid signed email, alongside
signature, issuer, audience and token-time checks. The proxy's OIDC security
settings remain in effect.

## Required configuration and credentials

Secrets are local to `ppt-service`; values are managed in Vault and mapped by
[vault/inventory](../../../../vault/inventory/PPT-SERVICE.md).

| Consumer | Secret | Key |
| --- | --- | --- |
| Website and trusted controller | `ppt-database-runtime` | `DATABASE_URL` |
| Schema migration | `ppt-database-migrator` | `DATABASE_URL` |
| OAuth proxy | `ppt-oidc` | `client-secret`, `cookie-secret` |

Vault CLI records are `secret/ppt-service/oidc`,
`secret/ppt-service/database-runtime` and `secret/ppt-service/database-migrator`.
ExternalSecret references use the
corresponding KV v2 `secret/data/ppt-service/...` paths and explicit properties.
The application and migration Job do not receive a Vault token or Kubernetes
API token.

Both application URLs use `postgresql+psycopg://` and target database
`ppt_service` at `postgres.data.svc.cluster.local:5432`, with different
roles. They never use the shared `appdb` database or `appuser` administrator as
an application login. Shared administrator access stays in the server-side
operator connection and is not projected into the PPT namespace. The migration role owns the application
schema; runtime has required table DML/sequence privileges and read-only access
to `api_schema_revision`. Schema revision 3 adds append-only human review entries:
runtime has SELECT/INSERT only on entries and SELECT/INSERT/UPDATE on streams,
with no DELETE/TRUNCATE/REFERENCES/TRIGGER on either review table. A PostgreSQL
trigger also rejects entry UPDATE/DELETE for the migration owner. Runtime retains
no schema ownership, DDL, superuser, role creation or database creation privileges.
Schema revision 4 adds `ppt_agent_outbox` with runtime CRUD privileges while
preserving the revision 3 review tables, entries and append-only constraints.
The deployment applies migration grants before rolling
out the website. Secret synchronization is not a substitute for these database
checks.

Migration credentials go only to the migration workload, and runtime credentials
only to the website and trusted controller. Proxy credentials do not enter the
website. The legacy `ppt-postgres-bootstrap` Secret/Vault record is retained only
for rollback and is not part of the default credential manifest. Workers receive no database credential
or long-lived provider/gateway token. Do not log URLs, Secret contents or
complete environment dumps.

Public authentication settings are owned by
[oauth/k8s](../../../../oauth/k8s/PPT.md). The proxy client ID and API audience
must match the dedicated Casdoor application. Actual RS256 tokens, issuer,
audience, expiry, valid signed email and allowlist membership, JWKS, forwarded identity, Cookie behavior,
Origin and CSRF are verified during deployment acceptance. The base does not
duplicate the shared authentication ConfigMaps.

## Storage and security

`ppt-data` requests a dedicated 20 GiB RWX `ceph-cephfs` claim, mounted only by the
website and trusted execution workloads at `/var/lib/ppt-service`; `DATA_ROOT` is
its `state` child. Database storage belongs to the existing shared `data/postgres`
instance. PPT does not create, mount or change the shared database claim.

The website runs as UID/GID 10000 without `fsGroup` or `fsGroupChangePolicy`.
Before website rollout, a nonroot initialization Job creates `state` with owner
10000:10000 and mode 0700 on an empty writable volume, or verifies an existing
directory's type, owner, mode and access. It rejects symbolic links and a missing
`state` on a nonempty root. It does not rewrite existing files or directory
permissions.

Only a dynamically provisioned PVC newly created by the deployment helper is
marked for first initialization. Its pending state is bound to the observed PVC
UID; the helper refuses to apply that path to an existing website or a restored
claim. The initialization Job may use `fsGroup=10000` with
`fsGroupChangePolicy=OnRootMismatch` for that pending new volume. A failed first
attempt can be retried by the normal deployment command; successful initialization
marks the same claim ready before website creation. Existing unmarked claims
never acquire `fsGroup`; their valid `state` is reused, or a new directory can be
created only if the empty root is already writable by UID 10000. Other ownership
or permission states require operator investigation.

Restoring data preserves its owner, mode and timestamp; recursive ownership
mapping is not part of deployment. Database storage is separate from website
files; changing database hosts does not move or rewrite `ppt-data`.

Workloads select `kubernetes.io/arch=arm64`. Containers run without root or
privilege escalation, with seccomp RuntimeDefault, dropped ALL capabilities and
a read-only root. Writable temporary directories are bounded emptyDir volumes.
Requests, limits and long-running workload probes are explicit. Service accounts
and Pod templates disable API token automount; workloads receive no API RBAC,
hostPath or Docker socket.

Ceph StorageClasses use `reclaimPolicy: Delete`. Scaling down retains PVCs;
deleting namespace/PVCs can remove underlying data. Back up the database and
files before recovery work, retain the source database and file claims, and follow
[migration/README.md](migration/README.md). `kubectl delete -k base` is not a
rollback operation.

## Network contract

| Source | Destination | Allowed port |
| --- | --- | --- |
| PPT workloads | `kube-system` Pods labeled `k8s-app=kube-dns` | UDP/TCP 53 |
| Selected Cloudflare Connector, or optional `frpc/app=frpc` | `ppt-site` | TCP 4180 |
| Website, migration and trusted controller | Namespace `data`, Pod label `app=postgres` | TCP 5432 |
| Website/OAuth proxy | Exact `ingress-nginx` controller serving `auth.panghuer.top` | TCP 443 |
| Other traffic | Other destinations | Denied by default |

Namespace and Pod selectors share a peer entry so both must match. The entry
connector needs reciprocal egress to 4180. Cloudflare public-hostname, cache
bypass and both policies are managed under
[cloudflare-tunnel](../../../../cloudflare-tunnel/PPT.md); the optional VPS route
is managed under [vps](../../../../vps/PPT.md). Activate one route for the host.
No NodePort, LoadBalancer or public API port 8000 is provided.

Database egress is limited to the exact shared instance selector and port.
Workers, model proxies and sandboxes receive neither database credentials nor
database network permission. PPT deployment does not install an ingress policy
that would replace or restrict other clients of `data/postgres`.

Casdoor uses the existing CoreDNS split horizon and internal TLS ingress while
retaining `https://auth.panghuer.top` as issuer. The shared
`oauth/k8s/ppt-oidc-egress.yaml` permits only the specified controller Pods on
443. NetworkPolicy cannot select HTTPS hostnames; actual DNS, TLS and
allow/deny traffic must be checked on Calico. YAML existence is not evidence of
enforced traffic isolation.

## Execution and evidence

Website deployment does not launch a Worker or model proxy. Follow
[K8S_DEPLOYMENT_HANDOFF.md](../../K8S_DEPLOYMENT_HANDOFF.md) for the remaining
execution integration: durable UID allocation, task-bound model authority,
checkpoint/base-version routes, lease semantics and complete writer termination.
Docker bridge/IP, boot/monotonic clock and container-ID evidence do not establish
cross-node Kubernetes behavior. Generated PPTX still requires artifact and
PowerPoint acceptance.

Consolidating an existing standalone database requires quiescing all writers,
backing up the source database and data root, restoring into a new empty
`ppt_service` database and verifying rows, schema revision, sequences, triggers,
ownership and runtime privileges before changing the two Vault URLs. Preserve
the bootstrap completion marker and append-only review history. Conflicting
target databases/roles are not overwritten. Keep the stopped legacy instance,
PVC and credentials for rollback; after new writes, rollback requires a separate
reconciliation rather than reopening the stale source.

2026-10-28: this contract and [README.md](README.md) describe the shared-database
deployment. Historical [RESULTS.md](RESULTS.md), troubleshooting and evidence keep
the independent instance, namespace, helper commands and validation boundaries
from their own runs; those old commands are not the current installation procedure.
