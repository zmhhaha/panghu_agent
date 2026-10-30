# Authenticated feedback access — 2026-10-30

The owner requested removing the PPT whitelist so other users can try the service
and provide feedback. PPT now accepts accounts authenticated by its dedicated
Casdoor application with a valid signed email. No operator-maintained mailbox or
subject membership list is required. Anonymous access remains disabled.

## Released behavior

- OAuth proxy uses `email_domains = ["*"]` without `authenticated_emails_file`.
- API no longer reads invitation files or checks mailbox membership.
- Canonical configuration and deployment no longer require or mount `ppt-owner`.
  The old live ConfigMap is retained unused. `PPT_OWNER_CONFIG` is obsolete and
  causes the normal deployer to stop before cluster mutations.
- JWT signature/issuer/audience/expiry/subject/signed-email validation, trusted
  proxy peers, Origin/CSRF, issuer/sub ownership and authenticated assets remain.
- The workbench displays a trial feedback notice instead of saying invitations
  are closed. This does not mark full PPT quality or PowerPoint acceptance passed.

## Git source and server validation

Application release: `a60a04c446d709b61876b86b832241604c40f05c`.
Outer release: `76f99e663b5ab1c5c8b34bf5358f27e26b7ba7d5`.
Both were committed, pushed and fetched through Git. No application archive was
uploaded. All tests and builds ran on the existing Linux server.

Final source checks passed 263 tests, with zero failures/errors/skips, plus the
production TypeScript/Vite compile. Coverage includes authenticated new emails,
two-way project isolation, invalid credentials, Origin/CSRF, deployment manifests,
obsolete configuration rejection, the OAuth renderer and workbench interactions.
Evidence: `/root/ppt-open-access-a60a04c446d7-b4ef5e3c95-checks/receipt.json`.

The actual packaged site image and the exact deployed proxy binary passed all
34 isolated OAuth protocol checks. Two unrelated synthetic identities completed
authorization-code flows and created projects; each received 404 for the other's
project. Anonymous API requests, invalid JWTs and invalid mutations were rejected.
The fixture used no external network, real client, user data or model requests.
Its owned containers were removed.

The final protocol fixture was fetched/exported from Git application
`b342ecea080e8d222c0d0f01e831812d8b2554d8`, independently of the unchanged
release checkout. These later commits change test code/documentation only;
the tested installed API auth hash matches the release source. Evidence:
`/root/ppt-service-p2-oidc-20261023-r-open-access-a60a04c-20261030-8d0848f0/output/summary.json`.

Earlier fixture runs stopped before successful OAuth: the proxy's linker version
field was blank and the generated certificates lacked strict X.509 extensions.
The final fixture pins the measured image/config and binary hashes, records the
raw version string, and generates standard CA/leaf certificates. It uses system
TLS defaults with strict CA/hostname verification; no production TLS setting or
proxy image was changed. This is synthetic HTTP protocol evidence, not an
observed new human account logging into real Casdoor in a browser.

## Production rollout

Completed `2026-10-30T12:00:57Z`, operation
`bb0a5c64-fdcc-4686-9a99-2be8ace41982`.

Image built/pushed as `arm-cluster-master:5000/ppt-service-site:latest`, deployed as
`arm-cluster-master:5000/ppt-service-site:latest@sha256:e61c220f007f08ff858ab81804cc0d3323a06f7a51e6e905fc45f8aa75160062`.
OCI source/revision/context labels and deployment provenance bind the release.
Build evidence:
`/root/ppt-authenticated-access-a60a04c-build-20261030T113334Z/source.json`.

The release used UID/resourceVersion-preconditioned patches to remove only the
API invitation environment key, replace the proxy membership setting, remove
owner mounts/volume, and update the site image/provenance. It did not replay the
broad deployment/migrator. Site generation44 is Ready with the verified running
image digest. Public GET results: `/ping`200, `/`302, `/api/v1/me`401 and
`/api/v1/projects`401.

The controller, Agent backend/enabled state, shared PostgreSQL references,
database schema, PVC, existing projects, OAuth image, client/cookie settings and
Secret references were preserved. No Casdoor/account/database changes, production
business row reads/writes or model jobs were performed. Private before/after
snapshots and release receipt:
`/root/ppt-authenticated-access-a60a04c-release-r1/release-receipt.json`.

This completes authenticated feedback access. The previously recorded upstream
model failures, full generation/revision delivery acceptance and PowerPoint owner
acceptance remain separate unfinished work. No acceptance/publication gate was
weakened or marked passed by this access change.
