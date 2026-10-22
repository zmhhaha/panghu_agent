"""Worker-scoped public academic HTTPS with real DNS and pinned connections.

The cluster resolver returns OpenClash fake IPs. This transport resolves only
the supported academic API hosts through AliDNS over authenticated HTTPS, then
connects to a checked public IPv4 address with the original hostname as TLS SNI
and HTTP Host. No system DNS, proxy environment, redirects, or private IPs are
used. This is a tool boundary; Kubernetes remains the process network boundary.
"""
from __future__ import annotations

from functools import wraps
import http.client
import ipaddress
import json
import math
from pathlib import Path
import socket
import ssl
import time
from urllib.parse import urlencode, urlsplit


ACADEMIC_HOSTS = frozenset({
    "api.crossref.org", "eutils.ncbi.nlm.nih.gov", "export.arxiv.org",
    "api.openalex.org", "api.semanticscholar.org",
})
RESOLVER_HOST = "dns.alidns.com"
RESOLVER_IPV4 = ("223.5.5.5", "223.6.6.6")
DNS_READ_LIMIT = 64 * 1024
ACADEMIC_READ_LIMIT = 4 * 1024 * 1024
MAX_CACHE_TTL = 300


def public_ipv4(value):
    address = ipaddress.ip_address(value)
    if address.version != 4 or not address.is_global or address.is_multicast:
        raise ValueError("academic transport requires a global unicast IPv4 address")
    return str(address)


def _hostname(value):
    if not isinstance(value, str) or not value:
        raise ValueError("invalid DNS name")
    return value.rstrip(".").lower()


def parse_dns_answer(host, payload):
    """Validate the question and CNAME ownership before accepting A records."""
    if not isinstance(payload, dict) or payload.get("Status") != 0 or payload.get("TC") is True:
        raise ValueError("DNS response is unsuccessful or truncated")
    question = payload.get("Question")
    if isinstance(question, dict):
        question = [question]
    if not isinstance(question, list) or len(question) != 1:
        raise ValueError("DNS response must contain the requested question")
    if question[0].get("type") != 1 or _hostname(question[0].get("name")) != host:
        raise ValueError("DNS response question mismatch")
    answers = payload.get("Answer")
    if not isinstance(answers, list) or not 1 <= len(answers) <= 64:
        raise ValueError("DNS response has no bounded answer set")
    cnames, addresses = {}, {}
    for row in answers:
        if not isinstance(row, dict):
            raise ValueError("malformed DNS answer")
        owner = _hostname(row.get("name"))
        ttl = row.get("TTL")
        if type(ttl) is not int or not 0 <= ttl <= 2**31 - 1:
            raise ValueError("invalid DNS TTL")
        if row.get("type") == 5:
            target = _hostname(row.get("data"))
            if owner in cnames and cnames[owner][0] != target:
                raise ValueError("conflicting DNS CNAME records")
            cnames[owner] = (target, ttl)
        elif row.get("type") == 1:
            addresses.setdefault(owner, []).append((public_ipv4(row.get("data")), ttl))
    current, seen, chain_ttls = host, set(), []
    while current in cnames:
        if current in seen or len(seen) >= 8 or current in addresses:
            raise ValueError("cyclic or ambiguous DNS CNAME chain")
        seen.add(current)
        current, ttl = cnames[current]
        chain_ttls.append(ttl)
    selected = addresses.get(current, [])
    if not selected:
        raise ValueError("DNS response lacks an A record for the requested name")
    ips = tuple(dict.fromkeys(address for address, _ in selected))
    return ips, min(MAX_CACHE_TTL, *chain_ttls, *(ttl for _, ttl in selected))


def _tls_context():
    # Explicit CA files avoid SSL_CERT_FILE/SSL_CERT_DIR environment overrides.
    for candidate in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"):
        if Path(candidate).is_file():
            return ssl.create_default_context(cafile=candidate)
    import certifi
    return ssl.create_default_context(cafile=certifi.where())


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("academic HTTPS request deadline exceeded")
    return remaining


def _https(host, ip, path, headers, deadline, limit):
    """Dial the validated literal address; never re-resolve host after checking."""
    ip = public_ipv4(ip)
    raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    connection = None
    try:
        raw.settimeout(_remaining(deadline))
        raw.connect((ip, 443))
        raw.settimeout(_remaining(deadline))
        raw = _tls_context().wrap_socket(raw, server_hostname=host)
        raw.settimeout(_remaining(deadline))
        connection = http.client.HTTPConnection(host, 443, timeout=_remaining(deadline))
        connection.sock = raw
        # Callers cannot override routing, compression, credentials or framing.
        safe_headers = {"User-Agent": "PanghuAcademic/1.0", "Accept": "*/*"}
        safe_headers.update({key: value for key, value in (headers or {}).items()
                             if key.lower() in {"user-agent", "accept", "x-api-key"}})
        safe_headers.update({"Host": host, "Accept-Encoding": "identity", "Connection": "close"})
        connection.request("GET", path, headers=safe_headers)
        raw.settimeout(_remaining(deadline))
        response = connection.getresponse()
        response_headers = dict(response.getheaders())
        if 300 <= response.status < 400:
            raise ValueError("academic HTTPS redirects are not permitted")
        if response.getheader("Content-Encoding", "identity").lower() != "identity":
            raise ValueError("academic HTTPS compressed responses are not permitted")
        length = response.getheader("Content-Length")
        if length is not None and (not length.isdecimal() or int(length) > limit):
            raise ValueError("academic HTTPS response exceeds its byte limit")
        chunks, size = [], 0
        while not response.isclosed():
            raw.settimeout(_remaining(deadline))
            chunk = response.read1(min(65536, limit + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise ValueError("academic HTTPS response exceeds its byte limit")
            chunks.append(chunk)
        return response.status, response_headers, b"".join(chunks)
    finally:
        if connection is not None:
            connection.close()
        else:
            raw.close()


class AcademicTransport:
    def __init__(self):
        self._cache = {}

    def resolve(self, host, deadline):
        if host not in ACADEMIC_HOSTS:
            raise ValueError("host is outside the academic API allowlist")
        cached = self._cache.get(host)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        last_error = None
        for bootstrap in RESOLVER_IPV4:
            try:
                status, _, body = _https(RESOLVER_HOST, bootstrap,
                    "/resolve?" + urlencode({"name": host, "type": "A"}),
                    {"Accept": "application/dns-json"}, min(deadline, time.monotonic() + 6), DNS_READ_LIMIT)
                if status != 200:
                    raise ValueError("DNS HTTPS response was unsuccessful")
                ips, ttl = parse_dns_answer(host, json.loads(body))
                self._cache[host] = (time.monotonic() + ttl, ips)
                return ips
            except (OSError, http.client.HTTPException, ValueError) as exc:
                last_error = exc
        raise ValueError("authenticated public DNS lookup unavailable") from last_error

    def get(self, url, *, params=None, headers=None, timeout=20):
        import requests
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in ACADEMIC_HOSTS
                or parsed.port not in (None, 443) or parsed.username is not None
                or parsed.password is not None or parsed.fragment):
            raise ValueError("only supported academic HTTPS API URLs are permitted")
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("invalid academic HTTPS timeout")
        deadline = time.monotonic() + min(timeout, 30)
        path = parsed.path or "/"
        query = parsed.query
        if params:
            extra = urlencode({key: value for key, value in params.items() if value is not None}, doseq=True)
            query = query + "&" + extra if query else extra
        if query:
            path += "?" + query
        ips = self.resolve(parsed.hostname, deadline)
        last_error = None
        for ip in ips:
            try:
                status, response_headers, body = _https(parsed.hostname, ip, path, headers,
                    min(deadline, time.monotonic() + 10), ACADEMIC_READ_LIMIT)
                response = requests.Response()
                response.status_code, response.url, response._content = status, url, body
                response.headers = requests.structures.CaseInsensitiveDict(response_headers)
                response.encoding = requests.utils.get_encoding_from_headers(response.headers) or "utf-8"
                return response
            except (OSError, http.client.HTTPException) as exc:
                last_error = exc
                if time.monotonic() >= deadline:
                    break
        raise requests.ConnectionError("academic HTTPS connection unavailable") from last_error


def scoped_academic_transport(function):
    """Apply the transport only to this bridge call and reset it on all exits."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        from tools.academic.http_client import using_transport
        with using_transport(AcademicTransport().get):
            return function(*args, **kwargs)
    return wrapped
