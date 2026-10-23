# P1 isolated Kubernetes candidate contract

**NOT DEPLOY READY.** `base` describes a future isolated `ppt-service-test`
environment. It starts no workloads: site and PostgreSQL have zero replicas,
and the separate migration Job is suspended. Rendering or server dry-run does
not prove deployment, network enforcement, storage ownership, SSO, Pi execution,
PowerPoint acceptance or release readiness. Applying the base would still create
PVCs and allocate storage; it is not a read-only validation command.

## Server build and coordinated deployment

The operator-facing `../../build.sh` builds the frontend and locked Python
dependencies on the build server, verifies the resulting site in an isolated
container, and pushes to `arm-cluster-master:5000` by default. Only a successful
verified push writes the matching repository manifest digest to
`../../rendered/image.txt`. `../../deploy.sh` normally builds first;
`--skip-build` consumes that receipt or an explicit `PPT_SITE_IMAGE` digest.
The original Hermes worker build is `../../build_worker.sh`.

`--prepare` applies infrastructure, synchronizes bootstrap credentials and starts
the dedicated PostgreSQL instance without changing the existing website.
`--init-db` explicitly initializes only an entirely new database and roles from
the dedicated Vault-backed URLs. Redeployment preserves an existing owner list
unless `PPT_OWNER_CONFIG` explicitly supplies its replacement. Migration, runtime
privileges and prepared storage are checked before the website rollout. The
storage check is read-only and does not establish write I/O or initialize the
CephFS directory. A deployment lock prevents overlapping helper runs.

These scripts do not configure Casdoor or Cloudflare, enable Pi/model execution,
or establish live SSO, CSI or CNI acceptance. Original base and migration
templates retain their zero-replica/suspended defaults; do not reapply them over
the coordinated deployment as a routine upgrade. Follow [README.md](README.md)
for first installation and recovery.

## Targets and topology

| Target | Includes | Deliberately separate |
| --- | --- | --- |
| `base` | Namespace, three tokenless SAs, configuration, two PVCs, independent PostgreSQL StatefulSet, same-Pod site/OAuth proxy, ClusterIP services and policies | No Job, RBAC, worker, model proxy, parser, ingress, NodePort, hostPath or Docker socket |
| `migrate-job` | Suspended schema-2 Job using the same site image | Does not include base, initialize roles, mount data files or run on site startup |

Only `ppt-site:4180` is a website Service port. Uvicorn serves the real
`ppt_service.server.site:create_site` factory on **127.0.0.1:8000**, with
`--no-proxy-headers`; oauth2-proxy is its same-Pod caller. The API's trusted peer
list is exactly loopback. There is no routable site 8000 port. The site requires
built assets at `/opt/ppt-service/web/dist` and uses Python at
`/usr/local/bin/python`, with `/opt/ppt-service` as its working directory.
It must use the packaged site image contract, never `control.dev_site`.

The site liveness/startup probes check only loopback `/healthz`. Readiness also
requires the runtime connection to read schema revision 2. A healthy HTTP process
alone does not establish database readiness. oauth2-proxy uses `/ping` and
`/ready`; actual v7.8.0 parsing/forwarding remains P2 acceptance. The site and proxy
share a network namespace, and cannot be isolated from one another by policy.

The site has `PPT_EXECUTION_ENABLED=false`, no `PPT_LOCAL_TEST_ONLY`, and an empty
`ppt-owner.data.emails` whitelist in `oauth/k8s/ppt-owner-configmap.yaml`.
Both site and proxy mount this ConfigMap directory read-only at `/owner`, without
`subPath`; `INVITATION_EMAILS_FILE` and `authenticated_emails_file` point to the
same `/owner/emails` file. Membership uses exact verified email addresses, one
per line; no administrator-maintained subject list is needed. The API reloads
the file on each authenticated request and continues to identify resource
ownership by verified issuer and subject. Kubernetes projection updates take
time and do not terminate in-progress requests or streams. Empty denies all;
file read or format failures reject access with 503. The whitelist code requires
a newly built site image; the older P1 image does not support this configuration.
The domain is confirmed as `ppt.panghuer.top`; its dedicated client
ID remains a placeholder. Existing `deployment_mode=test` / `manual_acceptance=pending`
guards are preserved in application code. No Pi worker contract is invented;
the Pi execution chain is a separate prerequisite.
The Pi owner's [deployment handoff](../../K8S_DEPLOYMENT_HANDOFF.md) records
the existing Docker/UID/queue/model-authority evidence and the remaining K8s
backend work. Docker bridge/IP checks, host boot/monotonic leases and CID stop
proofs cannot be reused as cross-node Kubernetes guarantees. A later worker
integration must verify K8s network boundaries, durable UID allocation, clock
and private control storage, full writer termination and the confirmed
checkpoint/base-version stage routes. Its test-derived local image also needs
current sources and an actual business entrypoint before it can be deployed.

## Required configuration and credentials

No Secret values are included. These names are local to `ppt-service-test`:

| Consumer | Secret | Key | Meaning |
| --- | --- | --- | --- |
| Site only | `ppt-database-runtime` | `DATABASE_URL` | Dedicated least-privilege runtime PostgreSQL login |
| Migration only | `ppt-database-migrator` | `DATABASE_URL` | Separate schema-owning migration login |
| PostgreSQL only | `ppt-postgres-bootstrap` | `POSTGRES_PASSWORD` | Initial `ppt_bootstrap` administrator; never mounted in site or migrator |
| OAuth proxy only | `ppt-oidc` | `client-secret`, `cookie-secret` | Dedicated PPT Casdoor client and valid proxy cookie secret |

Both application URLs must use explicit `postgresql+psycopg://` SQLAlchemy
driver URLs and target the new database
`ppt_service` on `ppt-postgres.ppt-service-test.svc.cluster.local:5432`, use the
installed psycopg driver, and authenticate **different roles**.
Sharing a DB host does not authorize use of the existing `data/postgres` service
or its credentials. Do not log URLs, environment dumps or Secret contents.

`POSTGRES_DB=ppt_test` is only the independent bootstrap/maintenance database;
`bootstrap-database.sql` creates the separately owned `ppt_service` application
database. Website/migrator URLs must target the latter. Database initialization
creates only the administrator/maintenance database. Before running
the migration, provision the independent migration and runtime login roles using
the reviewed provisioning procedure. Migrator must own the application schema
and be able to create tables, functions and triggers; runtime must have required
DML/sequence access and read access to `api_schema_revision`, with no ownership,
DDL, superuser, role creation or database creation privileges. Apply grants and
default privileges for future migration-created objects. Verify them against the
real database; creating two Kubernetes Secrets does not establish DB isolation.

The base pins the read-only verified PostgreSQL **16.15 alpine** manifest
`postgres@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea`
and oauth2-proxy **v7.8.0** manifest
`quay.io/oauth2-proxy/oauth2-proxy@sha256:d73ce980a96ec1143901a4676eaf0d148ae1dc965a59d3a3e5cf626cc84350aa`.
The packaged site remains an unresolved placeholder pending its verified ARM64
registry manifest digest. A Docker image config ID or an unpushed local tag is
not a registry manifest digest. PostgreSQL metadata confirms uid/gid 70; site
and proxy run at uid/gid 10000. No image dependency lock is changed here.

P2 public authentication configuration is owned by
[oauth/k8s/PPT.md](../../../../oauth/k8s/PPT.md), with Vault mappings in
[vault/inventory](../../../../vault/inventory/PPT-SERVICE.md).
The base consumes external `ppt-auth`, `ppt-oidc` and `ppt-owner` ConfigMaps rather
than duplicating them. Use the OAuth renderer to set matching client IDs, and
maintain the independent `ppt-owner` email whitelist after configuring the
dedicated application. P2 must validate the actual
RS256 token, claims, JWKS rotation, proxy token forwarding/header replacement,
cookie size, logout, expiration, Origin and CSRF checks. Do not reuse another
agent's client, users, database role or live tokens.

## Storage and security

The repository defines `ceph-cephfs` in
`ceph/cephfs-storageclass.yaml` and `ceph-rbd` in
`ceph/ceph-rbd-storageclass.yaml`. `ppt-data` requests its own 20 GiB RWX CephFS
claim; `ppt-postgres-data` requests its own 10 GiB RWO RBD claim. No existing PVC
is referenced. A read-only 2026-10-23 cluster inspection confirmed both class
names, `Delete` reclaim policy and volume expansion support on Kubernetes
v1.31.14 with five Ready ARM64 nodes. Actual CSI mount ownership remains untested.
Only site mounts the CephFS data, only PostgreSQL mounts the RBD database; the
proxy cannot read data or database credentials. No parser or execution container
is launched.

All containers use nonroot uid/gid, seccomp RuntimeDefault, dropped ALL
capabilities, no privilege escalation and a read-only root. Writable temporary
directories are bounded emptyDir volumes. All three workload templates explicitly
select `kubernetes.io/arch=arm64`. **The site has no `fsGroup` or
`fsGroupChangePolicy`**: CSI/kubelet ownership rewriting could turn immutable
0400/0500 candidate files or a private ledger into group-writable data. File
restore must preserve metadata; the application's data root must retain its
original uid 10000 ownership and writable root directory. Do not recursively
map/chown a restored tree. A future Pi UID ledger needs its own trusted control
domain and must not become writable to this website.

The CephFS PVC mounts at `/var/lib/ppt-service`; `DATA_ROOT` is the child
`/var/lib/ppt-service/state`. Restore tooling requires this destination child
not to exist, and must preserve the source directory's owner/modes/mtime. An
empty first-use PVC still needs its newly created `state` directory initialized
with the intended uid 10000 owner using an explicit reviewed preparation step;
the nonroot website must not be expected to create it under a root-owned mount.
Verify actual CSI mount permissions without a recursive root init container or
permission broadening. The **new empty** PostgreSQL RBD claim retains
`fsGroup=70`; never apply that ownership policy blindly to an existing database
volume. The nonroot postgres entrypoint must pass an actual isolated first-start
test. Every process has resource
requests/limits and startup/readiness/liveness checks where long-running.

Three SAs and all Pod templates explicitly disable API token automount. No
Role/RoleBinding/ClusterRole or token Secret is generated. The database and
migration have no reason to access the Kubernetes API.

**Ceph classes currently specify `reclaimPolicy: Delete`.** The retention
annotation is an operator note, not an enforced data-retention policy. Scaling
workloads down retains these standalone claims; deleting their namespace or
PVCs can delete the underlying data. Keep both claims/PVs, take a verified
DB+file backup, and follow the explicit rollback procedure. Never run
`kubectl delete -k base` as a rollback or cleanup shortcut.

## Network contract

| Source | Destination | Allowed port |
| --- | --- | --- |
| All test Pods | `kube-system` Pods labeled `k8s-app=kube-dns` | UDP/TCP 53 |
| `frpc` namespace Pods labeled `app=frpc` | `ppt-site` | TCP 4180 |
| `ppt-site` and `ppt-migrator` | Same-namespace `ppt-postgres` | TCP 5432 |
| Site/OAuth proxy, with P2 overlay | Exact `ingress-nginx` controller Pods serving `auth.panghuer.top` | TCP 443 |
| Any other traffic | Any destination | Denied by default |

Namespace and Pod selectors are in the same peer entry where both must match.
The selected entry infrastructure's egress to 4180 must also be verified.
Cloudflare Tunnel public-hostname and two-ended network policy preparation is
owned by [cloudflare-tunnel](../../../../cloudflare-tunnel/PPT.md); optional VPS
Nginx/frpc preparation is owned by [vps](../../../../vps/PPT.md).
These are separate deployer-applied resources, not automatically activated by
the base. Only the chosen test route should expose the service. No public Ingress,
LoadBalancer or NodePort is supplied.

Kubernetes NetworkPolicy does not match a hostname. P2 must establish actual
DNS answers and routing for every OIDC HTTPS destination and add exact `/32`
IP blocks (or a dedicated internal namespace+Pod selector with the precise
actual port) through a reviewed isolated overlay. No broad Internet CIDR, fake
DNS result, insecure TLS skip or `hostAliases` workaround belongs in the base.
The baseline remains closed; separately apply `oauth/k8s/ppt-oidc-egress.yaml`
after checking the existing split-horizon/internal-TLS prerequisites.
Read-only cluster inspection found `calico-node` and `kube-proxy` DaemonSets;
this replaces the old flannel assumption in earlier examples. Validate both
allow and deny traffic on the actual enforcing CNI; the existence
of NetworkPolicy YAML is not isolation evidence. DNS/NAT/node-local resolver
behavior must be checked against the actual cluster path.

## Validation boundaries

Run declarative configuration tests from the repository root with existing
Python and PyYAML (no installation is needed in the current workspace):

```powershell
python -m unittest discover -s panghu_agent/ppt_service/deploy/k8s/tests -p test_manifests.py -v
```

Render both targets with the actual Kubernetes/kustomize tool. Server dry-run
needs a separately prepared validation namespace and resolvable schema; it must
not be described as successful storage allocation, DB boot, proxy parsing or
deployment acceptance. The zero-replica/suspended defaults, missing secrets,
placeholder images, separately applied OIDC HTTPS egress, unresolved client/invites and
unverified storage/UID bootstrap are deliberate remaining activation gates.
No node memory taint toleration is added: observed node metrics were inconsistent,
and real available capacity/scheduling remains to be accepted before activation.
