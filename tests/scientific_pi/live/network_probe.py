"""Bounded fixed-target Pod network observations; no credentials or policy verdicts.

Run separately in parent-managed baseline and restricted Pods:
  python network_probe.py --profile baseline
  python network_probe.py --profile restricted
Mount only the public Kubernetes root CA at /validation-ca/ca.crt for that TLS
probe. No service account token, model key, proxy, or arbitrary target is used.
--self-test exercises only pure parsing/classification and performs no I/O.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import errno
import http.client
import ipaddress
import json
import multiprocessing
from pathlib import Path
import socket
import ssl
import time
import xml.etree.ElementTree as ET

VERSION = "scientific-pi-fixed-network-probe-v1"
MAX_READ = 64 * 1024
MAX_DNS_ADDRESSES = 64
TOTAL_SECONDS = 85.0
KUBE_CA = "/validation-ca/ca.crt"


@dataclass(frozen=True)
class Target:
    name: str
    host: str
    port: int
    protocol: str
    path: str = "/"
    expected: str = "http"
    role: str = "private_negative"
    budget: float = 3.0
    public_dns: bool = False


# This is the complete destination allowlist. No command-line destination,
# redirect, URL from a response, proxy environment, or credential is consulted.
TARGETS = (
    Target("llm_public_health", "llm-service.llm.svc.cluster.local", 80, "http", "/health/live",
           "health", "positive", 10.0),
    Target("crossref", "api.crossref.org", 443, "https", "/works?rows=0",
           "crossref", "positive", 10.0, True),
    Target("pubmed", "eutils.ncbi.nlm.nih.gov", 443, "https",
           "/entrez/eutils/einfo.fcgi?db=pubmed&retmode=json", "pubmed", "positive", 10.0, True),
    Target("arxiv", "export.arxiv.org", 443, "https",
           "/api/query?search_query=all:transformer&start=0&max_results=1", "atom", "positive", 10.0, True),
    Target("kubernetes", "kubernetes.default.svc.cluster.local", 443, "https", "/version", "json"),
    Target("vault", "vault.vault.svc.cluster.local", 8200, "http", "/v1/sys/health", "json"),
    Target("sqlite", "sqlite.data.svc.cluster.local", 8000, "http", "/health", "json"),
    Target("redis", "redis.data.svc.cluster.local", 6379, "redis"),
    Target("pi_web", "pi-web.pi.svc.cluster.local", 4180, "http", "/", "html"),
    Target("node_ssh", "192.168.137.101", 22, "ssh"),
    Target("metadata_root", "169.254.169.254", 80, "http", "/"),
)


def classify_ip(value):
    address = ipaddress.ip_address(value)
    return {"ip": str(address), "global": address.is_global,
            "private": address.is_private, "loopback": address.is_loopback,
            "link_local": address.is_link_local, "reserved": address.is_reserved,
            "benchmark_fake_ip_range": address.version == 4 and address in ipaddress.ip_network("198.18.0.0/15")}


def expected_format(kind, body):
    """Return a shape boolean only. No parsed content leaves the worker."""
    try:
        if kind == "http":
            return True
        if kind == "html":
            return b"<html" in body[:4096].lower() or b"<!doctype html" in body[:4096].lower()
        if kind == "atom":
            if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
                return False
            return ET.fromstring(body).tag == "{http://www.w3.org/2005/Atom}feed"
        if kind == "health" and body.strip().lower() in (b"ok", b"healthy", b"live", b"true"):
            return True
        value = json.loads(body)
        if not isinstance(value, dict):
            return False
        if kind == "crossref":
            return value.get("status") == "ok" and value.get("message-type") == "work-list" and isinstance(value.get("message"), dict)
        if kind == "pubmed":
            return isinstance(value.get("einforesult"), dict)
        return kind in ("json", "health")
    except (ValueError, UnicodeError, ET.ParseError):
        return False


def parse_redis(body):
    line = body.split(b"\r\n", 1)[0]
    if line == b"+PONG":
        return "pong"
    if line.startswith(b"-NOAUTH"):
        return "authentication_required"
    return None


def error_category(exc):
    # No raw exception strings: these can embed addresses, URLs, or environment.
    if isinstance(exc, ssl.SSLCertVerificationError):
        return "tls_verification"
    if isinstance(exc, ssl.SSLError):
        return "tls"
    if isinstance(exc, socket.gaierror):
        return "dns"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "refused"
    if isinstance(exc, FileNotFoundError):
        return "ca_unavailable"
    if isinstance(exc, OSError):
        if exc.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH):
            return "unreachable"
        if exc.errno in (errno.EACCES, errno.EPERM):
            return "permission"
        if exc.errno in (errno.ECONNRESET, errno.EPIPE):
            return "connection_reset"
        return "socket_error"
    if isinstance(exc, http.client.HTTPException):
        return "http_protocol"
    return "internal_error"


def tls_context(target):
    if target.name == "kubernetes":
        return ssl.create_default_context(cafile=KUBE_CA)
    # Pin system trust-store locations instead of inheriting SSL_CERT_FILE or
    # SSL_CERT_DIR overrides. Hostname checks and CERT_REQUIRED remain enabled.
    for candidate in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"):
        if Path(candidate).is_file():
            return ssl.create_default_context(cafile=candidate)
    raise FileNotFoundError("system CA store unavailable")


def worker(target, sender):
    started = time.monotonic()
    result = {"target": target.name, "host": target.host, "port": target.port,
              "protocol": target.protocol, "role": target.role, "budget_seconds": target.budget,
              "availability": "unknown", "stage": "starting", "dns_ips": [],
              "tls_verified": False, "http_status": None, "expected_format": None,
              "body_bytes_read": 0, "read_cap_bytes": MAX_READ, "error": None}
    connection = raw = None
    def publish():
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        sender.send(dict(result))
    try:
        addresses = socket.getaddrinfo(target.host, target.port, type=socket.SOCK_STREAM)
        unique = []
        for family, socktype, protocol, _, sockaddr in addresses:
            if sockaddr[0] not in [entry[3][0] for entry in unique]:
                unique.append((family, socktype, protocol, sockaddr))
            if len(unique) >= MAX_DNS_ADDRESSES:
                break
        result["dns_ips"] = [classify_ip(entry[3][0]) for entry in unique]
        result["stage"] = "dns_resolved"
        publish()
        if not unique:
            result["error"] = "dns_empty"
            return
        # Fake-IP/benchmark and other non-global answers remain visible evidence;
        # public DNS never silently expands permission to reserved/private IPs.
        candidates = [entry for entry in unique if not target.public_dns or ipaddress.ip_address(entry[3][0]).is_global]
        if not candidates:
            result["error"] = "public_dns_non_global_not_probed"
            return
        family, socktype, protocol, sockaddr = candidates[0]
        result["connected_ip"] = sockaddr[0]
        context = tls_context(target) if target.protocol == "https" else None
        raw = socket.socket(family, socktype, protocol)
        raw.settimeout(target.budget)
        raw.connect(sockaddr)
        result["stage"] = "tcp_connected"
        result["availability"] = "tcp_connected"
        publish()
        if context:
            raw = context.wrap_socket(raw, server_hostname=target.host)
            result["tls_verified"] = True
            result["stage"] = "tls_verified"
            publish()
        if target.protocol in ("http", "https"):
            # An explicit preconnected socket pins the actual DNS observation.
            # HTTPConnection never follows redirects or reads proxy environment.
            connection = http.client.HTTPConnection(target.host, target.port, timeout=target.budget)
            connection.sock = raw
            connection.request("GET", target.path, headers={"User-Agent": VERSION,
                               "Accept": "*/*", "Connection": "close"})
            response = connection.getresponse()
            result["http_status"] = response.status
            result["availability"] = "http_response"
            result["stage"] = "http_headers"
            publish()
            body = response.read(MAX_READ)
            result["body_bytes_read"] = len(body)
            result["body_may_be_truncated"] = len(body) == MAX_READ
            result["expected_format"] = expected_format(target.expected, body)
            result["redirect_followed"] = False
        elif target.protocol == "redis":
            raw.sendall(b"*1\r\n$4\r\nPING\r\n")
            body = raw.recv(min(MAX_READ, 4096))
            result["body_bytes_read"] = len(body)
            parsed = parse_redis(body)
            result["expected_format"] = parsed is not None
            result["redis_response"] = parsed
            if parsed:
                result["availability"] = "redis_response"
        elif target.protocol == "ssh":
            body = raw.recv(min(MAX_READ, 512))
            result["body_bytes_read"] = len(body)
            result["expected_format"] = body.startswith(b"SSH-")
            if result["expected_format"]:
                result["availability"] = "ssh_banner"
        result["stage"] = "complete"
    except Exception as exc:
        result["error"] = error_category(exc)
    finally:
        if connection:
            connection.close()
        elif raw:
            raw.close()
        publish()
        sender.close()


def bounded_probe(target, total_deadline):
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=worker, args=(target, sender), daemon=True)
    result = {"target": target.name, "host": target.host, "port": target.port,
              "protocol": target.protocol, "role": target.role, "budget_seconds": target.budget,
              "availability": "unknown", "stage": "not_started", "dns_ips": [], "error": None}
    deadline = min(time.monotonic() + target.budget, total_deadline)
    if deadline <= time.monotonic():
        receiver.close()
        sender.close()
        result["error"] = "total_budget_exhausted"
        return result
    process.start()
    sender.close()
    try:
        while time.monotonic() < deadline:
            if receiver.poll(min(0.05, max(0.0, deadline - time.monotonic()))):
                try:
                    result = receiver.recv()
                except EOFError:
                    break
            elif not process.is_alive():
                break
        process.join(timeout=max(0.0, deadline - time.monotonic()))
        if process.is_alive():
            result["error"] = "timeout"
            result["hard_timeout"] = True
            process.kill()
        process.join(timeout=0.25)
        # A finished worker may have sent its final result immediately before exit.
        while receiver.poll():
            try:
                update = receiver.recv()
            except EOFError:
                break
            if not result.get("hard_timeout"):
                result = update
        if process.exitcode not in (0, None) and not result.get("error"):
            result["error"] = "worker_exit"
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=0.25)
        receiver.close()
        process.close()
    return result


def self_test():
    assert classify_ip("198.18.0.1")["benchmark_fake_ip_range"]
    assert not classify_ip("198.18.0.1")["global"]
    assert classify_ip("169.254.169.254")["link_local"]
    assert expected_format("crossref", b'{"status":"ok","message-type":"work-list","message":{}}')
    assert not expected_format("crossref", b'{"status":"ok"}')
    assert expected_format("pubmed", b'{"einforesult":{}}')
    assert expected_format("atom", b'<feed xmlns="http://www.w3.org/2005/Atom"/>')
    assert not expected_format("atom", b'<!DOCTYPE a [<!ENTITY x "x">]><a/>')
    assert not expected_format("json", b'not json')
    assert parse_redis(b"+PONG\r\n") == "pong"
    assert parse_redis(b"-NOAUTH Authentication required.\r\n") == "authentication_required"
    assert parse_redis(b"sensitive unexpected response") is None
    assert error_category(TimeoutError("secret URL")) == "timeout"
    assert error_category(ConnectionRefusedError("secret URL")) == "refused"
    assert sum(target.budget for target in TARGETS) == 61.0
    assert len({target.name for target in TARGETS}) == len(TARGETS)
    print(json.dumps({"probe_version": VERSION, "self_test": "passed", "network_calls": 0}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("baseline", "restricted"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        if args.profile:
            parser.error("--self-test must run without --profile")
        self_test()
        return
    if not args.profile:
        parser.error("--profile is required unless --self-test is used")
    started = time.monotonic()
    results = [bounded_probe(target, started + TOTAL_SECONDS) for target in TARGETS]
    print(json.dumps({"probe_version": VERSION, "profile": args.profile,
        "scope": "fixed-target-network-observations-only", "policy_verdict": "not_evaluated",
        "baseline_unavailable_means": "unknown_not_policy_pass", "tls_verification_required": True,
        "proxy_environment_used": False, "credentials_used": False, "redirects_followed": False,
        "total_budget_seconds": TOTAL_SECONDS, "elapsed_seconds": round(time.monotonic() - started, 3),
        "results": results}, sort_keys=True))


if __name__ == "__main__":
    main()
