from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import socket
import ssl
import time
import unittest
from unittest.mock import MagicMock, patch

import requests

from pi_worker.research_tools import transport
from tools.academic import http_client


HOST = "api.crossref.org"


def dns_payload(ip="34.226.73.113", ttl=40):
    return {"Status": 0, "TC": False, "Question": {"name": HOST + ".", "type": 1},
            "Answer": [{"name": HOST + ".", "type": 1, "TTL": ttl, "data": ip}]}


class DnsValidationTests(unittest.TestCase):
    def test_public_a_and_ttl(self):
        self.assertEqual(transport.parse_dns_answer(HOST, dns_payload()), (("34.226.73.113",), 40))
        self.assertEqual(transport.parse_dns_answer(HOST, dns_payload(ttl=1000))[1], 300)

    def test_non_public_multicast_and_all_ipv6_are_rejected(self):
        for ip in ("198.18.5.132", "10.1.2.3", "100.64.0.1", "127.0.0.1", "169.254.169.254",
                   "192.168.137.101", "203.0.113.1", "224.0.0.1", "255.255.255.255",
                   "::1", "::ffff:127.0.0.1", "2606:4700:4700::1111"):
            with self.subTest(ip=ip), self.assertRaises(ValueError):
                transport.public_ipv4(ip)

    def test_question_mismatch_and_unrelated_a_record_are_rejected(self):
        payload = dns_payload()
        payload["Question"]["name"] = "example.com"
        with self.assertRaises(ValueError):
            transport.parse_dns_answer(HOST, payload)
        payload = dns_payload()
        payload["Answer"][0]["name"] = "example.com"
        with self.assertRaises(ValueError):
            transport.parse_dns_answer(HOST, payload)

    def test_cname_chain_preserves_smallest_ttl_and_rejects_cycle(self):
        payload = dns_payload(ttl=100)
        payload["Answer"][0]["name"] = "edge.example.com."
        payload["Answer"].append({"name": HOST, "type": 5, "TTL": 12, "data": "edge.example.com."})
        self.assertEqual(transport.parse_dns_answer(HOST, payload)[1], 12)
        payload["Answer"][0] = {"name": "edge.example.com", "type": 5, "TTL": 12, "data": HOST}
        with self.assertRaises(ValueError):
            transport.parse_dns_answer(HOST, payload)

    def test_mixed_public_private_answers_fail_closed(self):
        payload = dns_payload()
        payload["Answer"].append({"name": HOST, "type": 1, "TTL": 40, "data": "10.2.3.4"})
        with self.assertRaises(ValueError):
            transport.parse_dns_answer(HOST, payload)

    def test_negative_truncated_or_invalid_ttl_answers_are_rejected(self):
        for changes in ({"Status": 3}, {"TC": True}, {"Answer": []}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                transport.parse_dns_answer(HOST, {**dns_payload(), **changes})
        for ttl in (-1, 3.5, "60", True):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                transport.parse_dns_answer(HOST, dns_payload(ttl=ttl))

    def test_cache_obeys_zero_and_short_ttl(self):
        client = transport.AcademicTransport()
        clock = [100.0]
        def response(*args):
            return 200, {}, json.dumps(dns_payload(ttl=2)).encode()
        with patch.object(transport, "_https", side_effect=response) as https, \
                patch.object(transport.time, "monotonic", side_effect=lambda: clock[0]):
            client.resolve(HOST, 200)
            clock[0] = 101
            client.resolve(HOST, 200)
            self.assertEqual(https.call_count, 1)
            clock[0] = 102
            client.resolve(HOST, 200)
            self.assertEqual(https.call_count, 2)
        client = transport.AcademicTransport()
        with patch.object(transport, "_https", return_value=(200, {}, json.dumps(dns_payload(ttl=0)).encode())) as https:
            client.resolve(HOST, time.monotonic() + 20)
            client.resolve(HOST, time.monotonic() + 20)
            self.assertEqual(https.call_count, 2)


class TransportBoundaryTests(unittest.TestCase):
    def test_urls_are_rejected_before_dns_or_socket(self):
        urls = ["http://api.crossref.org/works", "https://localhost/", "https://10.0.0.1/",
                "https://api.crossref.org:444/", "https://user:pass@api.crossref.org/",
                "https://api.crossref.org/#fragment", "https://api.crossref.org.evil.example/"]
        client = transport.AcademicTransport()
        with patch.object(transport, "_https") as https:
            for url in urls:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    client.get(url)
            https.assert_not_called()

    def test_pinned_ip_preserves_tls_sni_and_http_host_without_system_dns(self):
        raw, secure, context, connection, response = [MagicMock() for _ in range(5)]
        context.wrap_socket.return_value = secure
        connection.getresponse.return_value = response
        response.status = 200
        response.getheaders.return_value = [("Content-Type", "application/json")]
        response.getheader.side_effect = lambda name, default=None: {"Content-Length": "2"}.get(name, default)
        response.read1.side_effect = [b"{}", b""]
        response.isclosed.return_value = False
        with patch.object(socket, "socket", return_value=raw) as make_socket, \
                patch.object(socket, "getaddrinfo", side_effect=AssertionError("must not resolve")), \
                patch.object(transport, "_tls_context", return_value=context), \
                patch.object(transport.http.client, "HTTPConnection", return_value=connection):
            status, _, body = transport._https(HOST, "34.226.73.113", "/works", {"Host": "localhost"}, time.monotonic() + 10, 100)
        make_socket.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)
        raw.connect.assert_called_once_with(("34.226.73.113", 443))
        context.wrap_socket.assert_called_once_with(raw, server_hostname=HOST)
        self.assertEqual(connection.request.call_args.kwargs["headers"]["Host"], HOST)
        self.assertEqual((status, body), (200, b"{}"))
        connection.close.assert_called_once()

    def test_redirect_and_response_limit_are_not_followed_or_relaxed(self):
        for status, length, body in ((302, "0", b""), (200, "101", b""), (200, None, b"x" * 101)):
            with self.subTest(status=status, length=length):
                raw, context, connection, response = [MagicMock() for _ in range(4)]
                context.wrap_socket.return_value = raw
                connection.getresponse.return_value = response
                response.status = status
                response.getheaders.return_value = [("Location", "http://169.254.169.254/")]
                response.getheader.side_effect = lambda name, default=None: {"Content-Length": length}.get(name, default)
                response.read1.return_value = body
                response.isclosed.return_value = False
                with patch.object(socket, "socket", return_value=raw), \
                        patch.object(transport, "_tls_context", return_value=context), \
                        patch.object(transport.http.client, "HTTPConnection", return_value=connection), self.assertRaises(ValueError):
                    transport._https(HOST, "34.226.73.113", "/works", {}, time.monotonic() + 10, 100)
                self.assertEqual(connection.request.call_count, 1)

    def test_final_body_read_may_close_socket_before_another_read(self):
        raw, context, connection, response = [MagicMock() for _ in range(4)]
        closed = [False]
        context.wrap_socket.return_value = raw
        connection.getresponse.return_value = response
        response.status = 200
        response.getheaders.return_value = []
        response.getheader.side_effect = lambda name, default=None: {"Content-Length": "2"}.get(name, default)
        response.isclosed.side_effect = lambda: closed[0]
        def settimeout(value):
            if closed[0]:
                raise OSError("Bad file descriptor")
        def read1(size):
            closed[0] = True
            return b"{}"
        raw.settimeout.side_effect = settimeout
        response.read1.side_effect = read1
        with patch.object(socket, "socket", return_value=raw), \
                patch.object(transport, "_tls_context", return_value=context), \
                patch.object(transport.http.client, "HTTPConnection", return_value=connection):
            self.assertEqual(transport._https(HOST, "34.226.73.113", "/works", {}, time.monotonic() + 10, 100)[2], b"{}")

    def test_tls_context_requires_trust_and_hostname_checks(self):
        context = transport._tls_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_resolver_failure_never_falls_back_to_system_dns(self):
        with patch.object(transport, "_https", side_effect=ssl.SSLError("failure")) as https, \
                patch.object(socket, "getaddrinfo", side_effect=AssertionError("must not resolve")), self.assertRaises(ValueError):
            transport.AcademicTransport().get("https://" + HOST + "/works")
        self.assertEqual([call.args[1] for call in https.call_args_list], list(transport.RESOLVER_IPV4))

    def test_https_response_retains_requests_interface_and_parameters(self):
        client = transport.AcademicTransport()
        with patch.object(client, "resolve", return_value=("34.226.73.113",)), \
                patch.object(transport, "_https", return_value=(429, {"Retry-After": "1"}, b'{"message":"limit"}')) as https:
            response = client.get("https://" + HOST + "/works?rows=1", params={"query": "a & b", "api_key": None})
        self.assertEqual(https.call_args.args[2], "/works?rows=1&query=a+%26+b")
        self.assertEqual(response.json(), {"message": "limit"})
        self.assertEqual(response.headers["retry-after"], "1")
        with self.assertRaises(requests.HTTPError):
            response.raise_for_status()


class ScopedIntegrationTests(unittest.TestCase):
    def test_context_override_restores_and_does_not_leak_into_threads(self):
        response = requests.Response()
        response.status_code, response._content = 200, b"{}"
        seen = []
        def custom(*args, **kwargs):
            seen.append("custom")
            return response
        with patch.object(requests, "get", return_value=response) as default, patch.object(http_client, "_throttle"):
            with self.assertRaises(RuntimeError):
                with http_client.using_transport(custom):
                    http_client.request_json("https://" + HOST)
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        pool.submit(http_client.request_json, "https://" + HOST).result()
                    raise RuntimeError("fixture")
            http_client.request_json("https://" + HOST)
        self.assertEqual(seen, ["custom"])
        self.assertEqual(default.call_count, 2)

    def test_bridge_search_and_identifier_use_scoped_transport(self):
        from pi_worker.research_tools.bridge import search, verify_identifier
        response = requests.Response()
        response.status_code, response._content = 200, b'{"message":{"items":[],"DOI":"10.1234/test"}}'
        with patch.object(transport.AcademicTransport, "get", return_value=response) as scoped, \
                patch.object(requests, "get", side_effect=AssertionError("default HTTP forbidden")), \
                patch.object(http_client, "_throttle"):
            result = search("test", providers=["crossref"], limit=1)
            self.assertEqual(result["searches"][0]["status"], "success")
            result = verify_identifier("doi", "10.1234/test")
            self.assertEqual(result["verification"]["status"], "verified")
        self.assertEqual(scoped.call_count, 2)
        self.assertIsNone(http_client._SCOPED_TRANSPORT.get())


if __name__ == "__main__":
    unittest.main()
