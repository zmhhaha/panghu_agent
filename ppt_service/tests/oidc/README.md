# P2 isolated OAuth protocol validation

`synthetic_oidc.py` runs the **real pinned oauth2-proxy v7.8.0 binary** against
the rebuilt website with its real email-admission authentication dependency. It uses
an HTTPS loopback issuer, an authorization-code/token endpoint, RSA JWKS,
an explicitly trusted temporary CA and an isolated SQLite fixture. All keys,
credentials, subjects, email addresses, codes and sessions are synthetic.

This is an HTTP-client protocol check. It does not replace browser, live
Casdoor, actual claim/cookie size, public TLS/DNS, FRP, Vault or enforcing CNI
acceptance. CookieJar does not implement a browser's SameSite rules; the runner
checks the emitted attributes and uses the real API's Origin/CSRF validation.

The input is the canonical `ConfigMap/ppt-oidc.data['oauth2-proxy.cfg']` from
[`oauth/k8s/ppt-proxy-configmap.yaml`](../../../../oauth/k8s/ppt-proxy-configmap.yaml).
Keep its security settings intact. The runner explicitly replaces only
`http_address`, `https_address`, `oidc_issuer_url`, `redirect_url`, `client_id`,
`upstreams`, `authenticated_emails_file`, `tls_cert_file` and `tls_key_file`
with synthetic loopback/fixture values. It supplies synthetic client/cookie
secrets through the child process environment. It does not depend on uncertain
environment/config client ID precedence. No real client is registered or borrowed.

From the repository root, use the dependency-free packager to extract that exact
ConfigMap block and preserve its source and input hashes. Choose a fresh output
directory; existing directories are refused:

```sh
python panghu_agent/ppt_service/tests/oidc/package_validation.py \
  --output .research-temp/ppt-p2-oidc-20261023/package-rN
```

The deployer copies the generated `input.tar` to a fresh dedicated remote
`/root/ppt-service-p2-oidc-20261023-rN/` directory, extracts it there, then runs:

```sh
cd /root/ppt-service-p2-oidc-20261023-rN
tar -xf input.tar
PPT_SITE_IMAGE_ID=sha256:REPLACE_WITH_REBUILT_SITE_IMAGE_CONFIG_ID \
  bash /root/ppt-service-p2-oidc-20261023-rN/input/run_proxy_validation.sh \
  /root/ppt-service-p2-oidc-20261023-rN
```

`PPT_SITE_IMAGE_ID` is required and must be the complete immutable config ID of
an already built ARM64 site image. The old P1 image is incompatible with the
email-admission Settings and cannot be used. Rebuild the site using the current
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
the real API's issuer/audience/subject and email-allowlist checks, Origin/CSRF mutations and
test/pending guard; invalid JWTs; accepting a new JWKS kid; unknown-kid failure
when JWKS is unavailable; and client-cookie removal on logout.

Rotation acceptance only proves a newly introduced key can be fetched. The
current `PyJWKClient(cache_keys=True)` caches known keys; this validation does
not prove immediate revocation of removed keys. Cookie sign-out clears that
client's session and does not prove global revocation of copied stateless
cookies. No claim is made about real Casdoor token lifetimes or refresh.

The API and proxy use the same owner-maintained email file. In deployment this
is `INVITATION_EMAILS_FILE=/owner/emails`; the synthetic fixture sets
`Settings.invitation_emails_file` to its own temporary file containing only
`synthetic-invited@example.invalid`. No email/sub pairing is needed. The API
matches the signed JWT's email to the file with `casefold`. Casdoor owns email
verification; the API's admission decision does not depend on `email_verified`
being present or on its value. A direct request to the real API exercises the
missing claim while retaining a valid signature and allowlisted email; API unit
tests also cover boolean-false and string-valued claims. The proxy fixture retains
its existing protocol settings.
Caller-supplied identity/email headers
cannot grant access. Ownership remains keyed by issuer and stable subject,
so two subjects using the same invited email cannot see each other's projects.

The file is re-read after JWT/peer validation on every request, including via
Kubernetes ConfigMap projection symlinks. Empty or unconfigured lists deny with
401; missing/unreadable, invalid UTF-8 or invalid-format configured files return
503. The limit is 64 KiB. Blank lines and whole-line `#` comments are supported;
use one complete ASCII mailbox per line with no display names, inline comments
or `*`/`?` wildcards. Updating a projected file takes effect when Kubernetes
publishes the new projection; the API retains no old allowlist cache.
