"""Isolated real oauth2-proxy protocol validation; all identities are synthetic.

Runs inside the pinned packaged site image with networking disabled. A temporary
trusted CA, issuer, authorization-code endpoint, JWKS and SQLite database exist
only in tmpfs. This is an HTTP-client protocol test, not browser or Casdoor SSO
acceptance. It never prints bearer tokens, cookies, credentials or callback URLs.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
from http import HTTPStatus
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import threading
import time
import tomllib
from urllib import error, parse, request

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import jwt
from sqlalchemy import create_engine
import uvicorn

from ppt_service.server.auth import Settings
from ppt_service.server import auth as auth_module
from ppt_service.server.migrate import migrate
from ppt_service.server.site import create_site


ISSUER = "https://127.0.0.1:44443"
PROXY = "https://127.0.0.1:44180"
CLIENT = "synthetic-ppt-client"
SUBJECT = "synthetic-first-subject"
EMAIL = "synthetic-first@example.invalid"
NEW_SUBJECT = "synthetic-new-subject"
NEW_EMAIL = "synthetic-unlisted@example.invalid"
PROXY_IMAGE_ID = "sha256:c410cd2f0b69eadd73498ede45579e847177e1fd45947fe3500091409c532867"
PROXY_BINARY_SHA256 = "01f5b2b357b8f79dfcaf6c156deb02e2001f0c9162e9af9bbb0ad46b34af5a67"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_private(path, data):
    path.write_bytes(data)
    path.chmod(0o600)


def certificates(work):
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic P2 test CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .sign(ca_key, hashes.SHA256()))
    leaf = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic P2 loopback")]))
            .issuer_name(ca_name).public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(ca_key, hashes.SHA256()))
    (work / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (work / "leaf.pem").write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    write_private(work / "leaf.key", leaf_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return ssl.create_default_context(cafile=str(work / "ca.pem"))


def configure(source, work):
    # Keep all non-fixture security settings from the actual candidate.
    canonical = tomllib.loads(source.read_text(encoding="utf-8"))
    if canonical.get("email_domains") != ["*"] or "authenticated_emails_file" in canonical:
        raise RuntimeError("Canonical proxy must admit all authenticated email domains without membership files")
    changes = {
        "http_address": "127.0.0.1:4180", "https_address": "127.0.0.1:44180",
        "oidc_issuer_url": ISSUER, "redirect_url": PROXY + "/oauth2/callback",
        "client_id": CLIENT, "upstreams": ["http://127.0.0.1:8000/"],
        "tls_cert_file": str(work / "leaf.pem"), "tls_key_file": str(work / "leaf.key"),
    }
    lines = []
    for line in source.read_text(encoding="utf-8").splitlines():
        active = line.strip()
        name = active.split("=", 1)[0].strip() if "=" in active and not active.startswith("#") else None
        if name in changes:
            lines.append(name + " = " + json.dumps(changes.pop(name)))
        else:
            lines.append(line)
    lines.extend(name + " = " + json.dumps(value) for name, value in changes.items())
    (work / "oauth2-proxy.cfg").write_text("\n".join(lines) + "\n", encoding="utf-8")


class Fixture:
    def __init__(self):
        self.keys = {"synthetic-key-1": rsa.generate_private_key(public_exponent=65537, key_size=2048)}
        self.codes = {}
        self.identity = {"sub": SUBJECT, "email": EMAIL}
        self.client_secret = secrets.token_urlsafe(32)
        self.issued_token = None
        self.jwks_available = True
        self.issuer_calls = {"discovery": 0, "authorize": 0, "token": 0, "jwks": 0}

    def token(self, key_id="synthetic-key-1", omit=(), **overrides):
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": CLIENT, "sub": SUBJECT, "iat": now,
                  "exp": now + 300, "email": EMAIL, "email_verified": True}
        claims.update(overrides)
        for claim in omit:
            claims.pop(claim, None)
        return jwt.encode(claims, self.keys[key_id], algorithm="RS256", headers={"kid": key_id})


def issuer_handler(fixture):
    class Issuer(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Neither query strings nor protocol credentials enter logs.

        def output(self, status, data, location=None):
            content = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            if location:
                self.send_header("Location", location)
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            url = parse.urlsplit(self.path)
            if url.path == "/.well-known/openid-configuration":
                fixture.issuer_calls["discovery"] += 1
                return self.output(200, {"issuer": ISSUER, "authorization_endpoint": ISSUER + "/authorize",
                    "token_endpoint": ISSUER + "/token", "jwks_uri": ISSUER + "/jwks",
                    "response_types_supported": ["code"], "subject_types_supported": ["public"],
                    "id_token_signing_alg_values_supported": ["RS256"],
                    "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"]})
            if url.path == "/jwks":
                fixture.issuer_calls["jwks"] += 1
                if not fixture.jwks_available:
                    return self.output(503, {"error": "synthetic_jwks_unavailable"})
                keys = []
                for kid, key in fixture.keys.items():
                    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
                    keys.append(public | {"kid": kid, "alg": "RS256", "use": "sig"})
                return self.output(200, {"keys": keys})
            if url.path == "/authorize":
                fixture.issuer_calls["authorize"] += 1
                q = parse.parse_qs(url.query)
                if (q.get("client_id") != [CLIENT] or q.get("redirect_uri") != [PROXY + "/oauth2/callback"]
                        or q.get("response_type") != ["code"] or not q.get("state")):
                    return self.output(400, {"error": "synthetic_bad_authorization_request"})
                code = secrets.token_urlsafe(32)
                fixture.codes[code] = (q.get("nonce", [None])[0], dict(fixture.identity))
                callback = PROXY + "/oauth2/callback?" + parse.urlencode({"code": code, "state": q["state"][0]})
                return self.output(302, {}, callback)
            return self.output(404, {"error": "not_found"})

        def do_POST(self):
            if self.path != "/token":
                return self.output(404, {"error": "not_found"})
            fixture.issuer_calls["token"] += 1
            count = int(self.headers.get("Content-Length", "0"))
            if count > 16384:
                return self.output(413, {"error": "too_large"})
            q = parse.parse_qs(self.rfile.read(count).decode())
            expected = "Basic " + base64.b64encode((CLIENT + ":" + fixture.client_secret).encode()).decode()
            auth_ok = (self.headers.get("Authorization") == expected or
                       (q.get("client_id") == [CLIENT] and q.get("client_secret") == [fixture.client_secret]))
            code = q.get("code", [None])[0]
            if not auth_ok or q.get("grant_type") != ["authorization_code"] or code not in fixture.codes:
                return self.output(400, {"error": "invalid_grant"})
            nonce, identity = fixture.codes.pop(code)
            fixture.issued_token = fixture.token(**(identity | ({"nonce": nonce} if nonce else {})))
            return self.output(200, {"access_token": "synthetic-opaque-access-token", "token_type": "Bearer",
                                    "expires_in": 300, "id_token": fixture.issued_token})
    return Issuer


def http(opener, path, headers=None, data=None):
    req = request.Request(path, data=data, headers=headers or {})
    try:
        response = opener.open(req, timeout=5)
    except error.HTTPError as exc:
        response = exc
    with response:
        raw = response.read(1024 * 1024)
        return response.status, response.headers, raw


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    checks = {}
    fixture = Fixture()
    work = Path("/tmp/p2-oidc")
    work.mkdir(mode=0o700)
    proxy = None
    proxy_log = None
    server = None
    issuer = None
    result = {"synthetic": True, "browser_acceptance": False, "casdoor_acceptance": False,
              "external_network": False, "model_requests": 0, "production_ready": False,
              "proxy_binary_sha256": sha(args.binary), "template_sha256": sha(args.template), "checks": checks}

    def check(name, condition):
        checks[name] = bool(condition)
        print(json.dumps({"check": name, "passed": bool(condition)}), flush=True)
        if not condition:
            raise AssertionError(name)

    try:
        receipt=json.loads((args.template.parent / "source.json").read_text(encoding="utf-8"))
        check("proxy_binary_matches_pinned_deployed_image",
              result["proxy_binary_sha256"] == PROXY_BINARY_SHA256 and
              receipt.get("proxy_image_id") == PROXY_IMAGE_ID and
              receipt.get("proxy_binary_sha256") == PROXY_BINARY_SHA256)
        actual_auth=Path(auth_module.__file__)
        result["runtime_auth_sha256"]=sha(actual_auth)
        result["runtime_auth_matches_package"]=(
            str(actual_auth)=="/opt/ppt-service/ppt_service/server/auth.py" and
            result["runtime_auth_sha256"]==receipt["runtime_source_sha256"]["ppt_service/server/auth.py"] and
            "invitation_emails_file" not in Settings.__dataclass_fields__)
        if not result["runtime_auth_matches_package"]:
            raise RuntimeError("The packaged site must match current authenticated-access authentication")
        context = certificates(work)
        os.environ["SSL_CERT_FILE"] = str(work / "ca.pem")
        configure(args.template, work)
        issuer = ThreadingHTTPServer(("127.0.0.1", 44443), issuer_handler(fixture))
        issuer_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        issuer_tls.load_cert_chain(work / "leaf.pem", work / "leaf.key")
        issuer.socket = issuer_tls.wrap_socket(issuer.socket, server_side=True)
        threading.Thread(target=issuer.serve_forever, daemon=True).start()

        engine = create_engine("sqlite:////tmp/p2-oidc/database.sqlite", connect_args={"check_same_thread": False})
        migrate(engine)
        settings = Settings("sqlite:////tmp/p2-oidc/database.sqlite", str(work / "state"),
            oidc_issuer=ISSUER, oidc_audience=CLIENT, jwks_url=ISSUER + "/jwks",
            trusted_proxy_cidrs=("127.0.0.1/32", "::1/128"),
            allowed_origins=(PROXY,))
        site = create_site(settings, engine, "/opt/ppt-service/web/dist")
        seen = []

        async def observed_site(scope, receive, send):
            if scope["type"] == "http" and scope["path"] == "/api/v1/me":
                seen.append(dict(scope.get("headers", [] )).get(b"authorization", b"").decode())
            return await site(scope, receive, send)

        server = uvicorn.Server(uvicorn.Config(observed_site, host="127.0.0.1", port=8000,
                                              log_level="critical", access_log=False, proxy_headers=False))
        threading.Thread(target=server.run, daemon=True).start()
        opener = request.build_opener(request.HTTPSHandler(context=context), request.HTTPCookieProcessor(CookieJar()))
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        check("packaged_real_site_started", server.started)
        version = subprocess.run([str(args.binary), "--version"], capture_output=True, text=True, check=True).stdout.strip()
        result["proxy_version"] = version
        # This deployed image has a blank linker version. Pin the measured image
        # and binary instead; retain raw output without inventing a version.
        proxy_env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "SSL_CERT_FILE": str(work / "ca.pem"), "OAUTH2_PROXY_CLIENT_SECRET": fixture.client_secret,
            "OAUTH2_PROXY_COOKIE_SECRET": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()}
        proxy_log = args.output.with_name("proxy-private.log").open("x", encoding="utf-8")
        os.chmod(proxy_log.name, 0o600)
        proxy = subprocess.Popen([str(args.binary), "--config", str(work / "oauth2-proxy.cfg")],
                                 env=proxy_env, stdout=proxy_log, stderr=proxy_log)
        proxy_ready = False
        for _ in range(100):
            if proxy.poll() is not None:
                break
            try:
                if http(opener, PROXY + "/ping")[0] == 200:
                    proxy_ready = http(opener, PROXY + "/ready")[0] == 200
                    break
            except (OSError, error.URLError):
                pass
            time.sleep(0.05)
        result["proxy_startup_exit_code"] = proxy.poll()
        check("real_proxy_config_parsed_and_started", proxy.poll() is None and proxy_ready)
        status, headers, _ = http(opener, PROXY + "/api/v1/me")
        check("anonymous_api_401_no_redirect", status == 401 and "Location" not in headers)
        check("inbound_bearer_alone_not_authenticated",
              http(opener, PROXY + "/api/v1/me", {"Authorization": "Bearer " + fixture.token()})[0] == 401)

        jar = CookieJar()
        session = request.build_opener(request.HTTPSHandler(context=context), request.HTTPCookieProcessor(jar))
        status, _, _ = http(session, PROXY + "/oauth2/start?rd=/")
        check("synthetic_authorization_code_flow_completed", status == 200 and fixture.issued_token is not None)
        session_cookies = [cookie for cookie in jar if cookie.name == "__Host-ppt"]
        check("host_only_secure_httponly_cookie", len(session_cookies) == 1 and
              all(c.secure and c.path == "/" and not c.domain_specified and c.has_nonstandard_attr("HttpOnly") for c in session_cookies))
        check("session_cookie_samesite_lax_and_eight_hours", all(
            str(c.get_nonstandard_attr("SameSite")).lower() == "lax" and
            c.expires is not None and 28740 <= c.expires - int(time.time()) <= 28860
            for c in session_cookies))
        check("synthetic_session_cookie_under_4096_bytes", all(len(c.name) + len(c.value) + 1 < 4096 for c in session_cookies))
        status, _, raw = http(session, PROXY + "/api/v1/me", {"Authorization": "Bearer synthetic-injected-token",
                                                               "X-Auth-Request-User": "synthetic-attacker"})
        identity = json.loads(raw) if status == 200 else {}
        check("session_id_token_replaces_inbound_authorization", status == 200 and seen[-1] == "Bearer " + fixture.issued_token)
        check("real_api_checks_issuer_audience_subject", identity.get("issuer") == ISSUER and identity.get("sub") == SUBJECT)
        check("authenticated_identity_contract", bool(identity.get("csrf_token")) and isinstance(identity.get("quota"), dict))
        payload = json.dumps({"title": "Synthetic OIDC project"}).encode()
        base = {"Content-Type": "application/json"}
        check("mutation_missing_origin_and_csrf_rejected", http(session, PROXY + "/api/v1/projects", base, payload)[0] == 403)
        headers = base | {"Origin": PROXY, "X-CSRF-Token": identity["csrf_token"]}
        check("mutation_wrong_origin_rejected", http(session, PROXY + "/api/v1/projects", headers | {"Origin": "https://wrong.example.invalid"}, payload)[0] == 403)
        check("mutation_wrong_csrf_rejected", http(session, PROXY + "/api/v1/projects", headers | {"X-CSRF-Token": "synthetic-wrong"}, payload)[0] == 403)
        status, _, raw = http(session, PROXY + "/api/v1/projects", headers, payload)
        check("mutation_valid_origin_csrf_accepted", status == 201)
        project_id = json.loads(raw)["id"]

        # An unrelated signed-in identity must pass the actual proxy, even though
        # its address was never recorded in an operator-maintained membership list.
        fixture.identity = {"sub": NEW_SUBJECT, "email": NEW_EMAIL}
        newcomer = request.build_opener(request.HTTPSHandler(context=context), request.HTTPCookieProcessor(CookieJar()))
        status, _, _ = http(newcomer, PROXY + "/oauth2/start?rd=/")
        check("new_email_real_oauth_flow_completed", status == 200)
        status, _, raw = http(newcomer, PROXY + "/api/v1/me")
        new_identity = json.loads(raw) if status == 200 else {}
        check("new_email_proxy_and_api_admitted", status == 200 and
              new_identity.get("issuer") == ISSUER and new_identity.get("sub") == NEW_SUBJECT)
        check("different_subject_project_is_hidden", http(newcomer, PROXY + "/api/v1/projects/" + project_id)[0] == 404)
        check("different_subject_project_list_is_empty", json.loads(http(newcomer, PROXY + "/api/v1/projects")[2]) == [])
        new_headers = base | {"Origin": PROXY, "X-CSRF-Token": new_identity["csrf_token"]}
        status, _, raw = http(newcomer, PROXY + "/api/v1/projects", new_headers, payload)
        check("new_email_can_create_own_project", status == 201)
        check("first_subject_cannot_read_new_project", http(session, PROXY + "/api/v1/projects/" + json.loads(raw)["id"])[0] == 404)

        direct = request.build_opener(request.HTTPSHandler(context=context))
        check("real_api_admits_without_email_verified", http(direct, "http://127.0.0.1:8000/api/v1/me",
              {"Authorization": "Bearer " + fixture.token(omit=("email_verified",))})[0] == 200)
        check("real_api_admits_new_valid_email", http(direct, "http://127.0.0.1:8000/api/v1/me",
              {"Authorization": "Bearer " + fixture.token(sub=NEW_SUBJECT, email=NEW_EMAIL)})[0] == 200)
        valid_claims = {"iss": ISSUER, "aud": CLIENT, "sub": SUBJECT, "iat": int(time.time()),
                        "exp": int(time.time()) + 300, "email": EMAIL}
        variants = {"wrong_issuer": fixture.token(iss="https://wrong.example.invalid"),
            "wrong_audience": fixture.token(aud="wrong-client"),
            "missing_email": fixture.token(omit=("email",)), "invalid_email": fixture.token(email="invalid"),
            "expired": fixture.token(exp=int(time.time()) - 60),
            "wrong_signature": jwt.encode(valid_claims,
                                          rsa.generate_private_key(public_exponent=65537, key_size=2048), algorithm="RS256", headers={"kid": "synthetic-key-1"}),
            "unsupported_hs256": jwt.encode(valid_claims,
                                            "synthetic-hs256-key-that-is-not-an-rsa-key", algorithm="HS256", headers={"kid": "synthetic-key-1"})}
        for name, token in variants.items():
            check("real_api_rejects_" + name, http(direct, "http://127.0.0.1:8000/api/v1/me", {"Authorization": "Bearer " + token})[0] == 401)
        fixture.keys["synthetic-key-2"] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        check("new_jwks_kid_rotation_accepted", http(direct, "http://127.0.0.1:8000/api/v1/me", {"Authorization": "Bearer " + fixture.token("synthetic-key-2")})[0] == 200)
        fixture.keys["synthetic-key-3"] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        fixture.jwks_available = False
        check("unavailable_jwks_unknown_kid_rejected", http(direct, "http://127.0.0.1:8000/api/v1/me", {"Authorization": "Bearer " + fixture.token("synthetic-key-3")})[0] == 401)
        fixture.jwks_available = True
        signout = request.build_opener(request.HTTPSHandler(context=context),
            request.HTTPCookieProcessor(jar), NoRedirect())
        http(signout, PROXY + "/oauth2/sign_out")
        check("logout_clears_client_session", http(session, PROXY + "/api/v1/me")[0] == 401 and not any(c.name == "__Host-ppt" for c in jar))
        result["issuer_calls"] = fixture.issuer_calls
        result["passed"] = True
        return 0
    except Exception as exc:
        result["passed"] = False
        result["failure_type"] = type(exc).__name__
        # Safe named checks explain assertions. Other exception text can contain credentials.
        print(json.dumps({"failed": True, "type": type(exc).__name__}), flush=True)
        return 1
    finally:
        if proxy is not None and proxy.poll() is None:
            proxy.terminate()
            try:
                proxy.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proxy.kill()
                proxy.wait(timeout=5)
        if server:
            server.should_exit = True
        if issuer:
            issuer.shutdown()
            issuer.server_close()
        result["proxy_process_stopped"] = proxy is None or proxy.poll() is not None
        if proxy_log:
            proxy_log.close()
        result["checks_passed"] = sum(checks.values())
        result["checks_total"] = len(checks)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
