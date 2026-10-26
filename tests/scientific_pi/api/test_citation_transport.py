"""Bounded artifact reads without opening any network connection."""
import io
import json
import unittest
import urllib.error
from unittest.mock import Mock, patch

from scientific_agent.pi_client import PiClient, WorkerError


class CitationTransportTests(unittest.TestCase):
    ID = '00000000-0000-4000-8000-000000000001'

    def connection(self, body):
        stream = Mock(status=200, headers={'Content-Type': 'application/json'})
        stream.read.return_value = body
        stream.__enter__ = Mock(return_value=stream)
        stream.__exit__ = Mock(return_value=False)
        return stream

    def test_default_transport_reads_at_most_declared_size_plus_one(self):
        client = PiClient('http://loopback-fixture.invalid', 'synthetic-control')
        stream = self.connection(b'abc')
        with patch('scientific_agent.pi_client.urllib.request.urlopen', return_value=stream):
            content, _ = client.artifact(self.ID, 'papers', max_bytes=3)
        self.assertEqual(content, b'abc')
        stream.read.assert_called_once_with(4)

    def test_one_extra_byte_is_rejected_without_unbounded_read(self):
        client = PiClient('http://loopback-fixture.invalid', 'synthetic-control')
        stream = self.connection(b'abcd')
        with patch('scientific_agent.pi_client.urllib.request.urlopen', return_value=stream), \
             self.assertRaises(WorkerError) as raised:
            client.artifact(self.ID, 'papers', max_bytes=3)
        self.assertEqual(raised.exception.code, 'artifact_size_mismatch')
        stream.read.assert_called_once_with(4)

    def test_empty_file_uses_one_byte_probe(self):
        client = PiClient('http://loopback-fixture.invalid', 'synthetic-control')
        stream = self.connection(b'')
        with patch('scientific_agent.pi_client.urllib.request.urlopen', return_value=stream):
            self.assertEqual(client.artifact(self.ID, 'evidence', max_bytes=0)[0], b'')
        stream.read.assert_called_once_with(1)

    def test_empty_artifact_expiry_retains_410_error_response(self):
        client = PiClient('http://loopback-fixture.invalid', 'synthetic-control')
        body = json.dumps({'error': {'code': 'artifact_expired'}}).encode()
        error = urllib.error.HTTPError('http://loopback-fixture.invalid', 410, 'Gone', {}, io.BytesIO(body))
        with patch('scientific_agent.pi_client.urllib.request.urlopen', side_effect=error), \
             self.assertRaises(WorkerError) as raised:
            client.artifact(self.ID, 'evidence', max_bytes=0)
        self.assertEqual(raised.exception.status, 410)
        self.assertEqual(raised.exception.code, 'artifact_expired')

    def test_injected_transport_retains_five_arguments_and_is_size_checked(self):
        transport = Mock(return_value=(200, {}, b'abcd'))
        client = PiClient('http://loopback-fixture.invalid', 'synthetic-control', transport=transport)
        with self.assertRaises(WorkerError) as raised:
            client.artifact(self.ID, 'papers', max_bytes=3)
        self.assertEqual(raised.exception.code, 'artifact_size_mismatch')
        self.assertEqual(len(transport.call_args.args), 5)

    def test_invalid_limits_never_reach_transport(self):
        transport = Mock()
        client = PiClient('http://loopback-fixture.invalid', 'synthetic-control', transport=transport)
        for limit in (-1, True, 1.5, '2', 51 * 1024 * 1024):
            with self.subTest(limit=limit), self.assertRaises(WorkerError) as raised:
                client.artifact(self.ID, 'papers', max_bytes=limit)
            self.assertEqual(raised.exception.code, 'invalid_artifact_limit')
        transport.assert_not_called()


if __name__ == '__main__':
    unittest.main()
