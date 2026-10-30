# P2 isolated OAuth protocol validation

`synthetic_oidc.py` runs the **exact deployed oauth2-proxy binary** against
the rebuilt website with its real authenticated-access dependency. It uses
an HTTPS loopback issuer, an authorization-code/token endpoint, RSA JWKS,
an explicitly trusted temporary CA and an isolated SQLite fixture. All keys,
credentials, subjects, email addresses, codes and sessions are synthetic.

Proxy identity is pinned to image config `sha256:c410cd2f0b69eadd73498ede45579e847177e1fd45947fe3500091409c532867`
and binary SHA256 `01f5b2b357b8f79dfcaf6c156deb02e2001f0c9162e9af9bbb0ad46b34af5a67`.
That cached image has the upstream repository digest
`quay.io/oauth2-proxy/oauth2-proxy@sha256:d73ce980a96ec1143901a4676eaf0d148ae1dc965a59d3a3e5cf626cc84350aa`,
OCI version `v7.8.0` and source `https://github.com/oauth2-proxy/oauth2-proxy`.
The runner verifies the config ID, OCI labels and extracted binary hash.
Its actual `--version` output is `oauth2-proxy  (built with go1.22.10)`;
the linker version field is blank. The test records that raw value and checks
exact binary identity rather than inferring a version string. This does not
replace or upgrade the production proxy.

The generated CA and leaf include subject/authority key identifiers, critical
key usage and the leaf's server-auth purpose, supporting strict X.509 validation
in the packaged Python runtime. The HTTP client uses system TLS defaults and
retains CA and hostname verification. This fixture does not change or validate
production ingress TLS negotiation or browser TLS policy.

This is an HTTP-client protocol check. It does not replace browser, live
Casdoor, actual claim/cookie size, public TLS/DNS, FRP, Vault or enforcing CNI
acceptance. CookieJar does not implement a browser's SameSite rules; the runner
checks the emitted attributes and uses the real API's Origin/CSRF validation.

The input is the canonical `ConfigMap/ppt-oidc.data['oauth2-proxy.cfg']` from
[`oauth/k8s/ppt-proxy-configmap.yaml`](../../../../oauth/k8s/ppt-proxy-configmap.yaml).
Keep its security settings intact. The runner explicitly replaces only
`http_address`, `https_address`, `oidc_issuer_url`, `redirect_url`, `client_id`,
`upstreams`, `tls_cert_file` and `tls_key_file`
with synthetic loopback/fixture values. It supplies synthetic client/cookie
secrets through the child process environment. It does not depend on uncertain
environment/config client ID precedence. No real client is registered or borrowed.

On the server, from the Git checkout of the exact release, use the dependency-free
packager to extract that ConfigMap block and preserve its source and input hashes.
Choose a fresh output directory; existing directories are refused:

```sh
python panghu_agent/ppt_service/tests/oidc/package_validation.py \
  --output /root/ppt-service-p2-oidc-20261023-rN
```

The package is generated from server-side Git sources. Run the generated input
directly on that server:

```sh
cd /root/ppt-service-p2-oidc-20261023-rN
PPT_SITE_IMAGE_ID=sha256:REPLACE_WITH_REBUILT_SITE_IMAGE_CONFIG_ID \
  bash /root/ppt-service-p2-oidc-20261023-rN/input/run_proxy_validation.sh \
  /root/ppt-service-p2-oidc-20261023-rN
```

`PPT_SITE_IMAGE_ID` is required and must be the complete immutable config ID of
an already built ARM64 site image. The old P1 image is incompatible with the
authenticated-access Settings and cannot be used. Rebuild the site using the current
source, then obtain its config ID with `docker image inspect --format '{{.Id}}'
IMAGE_TAG`. The runner also checks the installed `server/auth.py` path and hash
against `input/source.json`; it does not mount replacement application source.

`run_proxy_validation.sh` verifies that this explicit site ID and the fixed
oauth2-proxy ID exist locally; it does not install dependencies or pull images. A never-started, owned
container exports `/bin/oauth2-proxy` and is immediately removed. A single
nonroot validation container then runs with `--network none`, no published
ports, a read-only root, dropped capabilities, no new privileges, bounded
resources and writable tmpfs. It has no host credentials, business data,
Docker socket or production mounts. Output contains input hashes, named
boolean checks, safe status and exact owned-resource cleanup receipts.
Proxy protocol logs are discarded so callback queries, cookies and tokens
cannot enter retained evidence. Fixture private keys, database and secrets
exist only in tmpfs and disappear with the owned runner.

The end-to-end checks cover real parsing/startup; anonymous API 401 without a
redirect; a completed synthetic authorization-code exchange; replacement of
caller-supplied Authorization by the session's ID token; host-only Secure,
HttpOnly, Path=/ and SameSite=Lax cookie attributes; synthetic cookie size;
the real API's issuer/audience/subject and signed-email checks, Origin/CSRF mutations and
test/pending guard; invalid JWTs; accepting a new JWKS kid; unknown-kid failure
when JWKS is unavailable; and client-cookie removal on logout.

Rotation acceptance only proves a newly introduced key can be fetched. The
current `PyJWKClient(cache_keys=True)` caches known keys; this validation does
not prove immediate revocation of removed keys. Cookie sign-out clears that
client's session and does not prove global revocation of copied stateless
cookies. No claim is made about real Casdoor token lifetimes or refresh.

The canonical proxy must use `email_domains = ["*"]` without an email-membership
file. The packaged API must no longer expose `Settings.invitation_emails_file`.
The runner completes two independent real authorization-code flows for unrelated
synthetic subjects and emails, including an address never listed by an operator.
Both identities can create projects; each receives 404 for the other's project.
The new identity initially has an empty project list.

Casdoor owns email verification; API admission does not depend on `email_verified`
being present or on its value. The direct API checks cover a signed valid new
email and a missing verification claim, and reject missing or malformed emails.
Caller-supplied identity/email headers cannot grant access. Ownership remains
keyed by issuer and stable subject. The proxy retains its other protocol settings.
