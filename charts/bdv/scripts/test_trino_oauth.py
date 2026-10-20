#!/usr/bin/env python3
"""Offline config and HTTP-probe tests; no Kubernetes/IdP writes."""
import base64
import contextlib
import http.server
import io
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('prepare_oauth', ROOT / 'scripts/prepare-trino-oauth.py')
oauth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oauth)


class ConfigTests(unittest.TestCase):
    source = 'coordinator=true\nhttp-server.http.port=8080\ndiscovery.uri=http://bdv-coordinator:8080\n'

    def test_config_preserved_and_only_references_added(self):
        actual = oauth.build_config(self.source)
        self.assertTrue(actual.startswith(self.source))
        for key, value in oauth.ADDITIONS.items():
            self.assertIn(key + '=' + value + '\n', actual)
        self.assertNotIn('allow-insecure-over-http=true', actual)

    def test_existing_security_refused(self):
        for line in ('http-server.authentication.type=oauth2',
                     'internal-communication.shared-secret=existing',
                     'http-server.process-forwarded=true'):
            with self.assertRaises(ValueError):
                oauth.build_config(self.source + line + '\n')

    def test_duplicate_or_different_port_refused(self):
        with self.assertRaises(ValueError):
            oauth.build_config(self.source + 'coordinator=false\n')
        with self.assertRaises(ValueError):
            oauth.build_config(self.source.replace('8080', '9090'))

    def test_candidate_preserves_source_and_does_not_enable_runtime(self):
        original = yaml.safe_load((ROOT / 'values-dev.yaml').read_text())
        candidate = oauth.candidate_values(original, 'bdv-trino-coordinator', 'bdv-trino-coordinator-oidc', 'bdv-keycloak', 'bdv-internal')
        self.assertEqual(original['bdv']['coordinatorConfigSecret'], 'bdv-trino-coordinator')
        self.assertFalse(candidate['ingress']['enabled'])
        self.assertFalse(any(candidate['deployment'].values()))
        self.assertEqual(candidate['bdv']['workers'], 0)
        self.assertTrue(candidate['bdv']['oauth2']['enabled'])

    def test_config_newlines_allowed_but_credential_whitespace_refused(self):
        secret = {'data': {'key': base64.b64encode(b'value\n').decode()}}
        self.assertEqual(oauth.decode(secret, 'key', credential=False), 'value\n')
        with self.assertRaises(ValueError):
            oauth.decode(secret, 'key')


class ProbeTests(unittest.TestCase):
    def probe(self, status, starting):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = ('{"starting":' + str(starting).lower() + '}').encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_args):
                pass
        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        script = yaml.safe_load((ROOT / 'values.yaml').read_text())['bdv']['readinessCommand'][-1]
        script = script.replace('/8080', '/' + str(server.server_port))
        try:
            return subprocess.run(['/usr/bin/bash', '-ec', script], capture_output=True, timeout=8).returncode
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_ready_accepted(self):
        self.assertEqual(self.probe(200, False), 0)

    def test_starting_rejected(self):
        self.assertNotEqual(self.probe(200, True), 0)

    def test_unauthorized_rejected(self):
        self.assertNotEqual(self.probe(401, False), 0)


class PreparationTests(unittest.TestCase):
    def run_preparation(self, create):
        def encoded(value):
            return base64.b64encode(value.encode()).decode()
        secrets = {
            'bdv-trino-coordinator': {'data': {
                'config.properties': encoded(ConfigTests.source),
                'node.properties': encoded('node.environment=bdv\n'),
                'jvm.config': encoded('-Xms1G\n-Xmx3G\n'),
                'log.properties': encoded('io.trino=INFO\n'),
            }},
            'bdv-keycloak': {'data': {
                'issuer': encoded('https://sso.dinecodetalkers.com/realms/innonet'),
                'clientId': encoded('bdv'),
                'clientSecret': encoded('unit-test-only-client-secret'),
            }},
            'bdv-internal': {'data': {'sharedSecret': encoded(encoded('a' * 64))}},
        }
        original = dict(secrets['bdv-trino-coordinator']['data'])
        def fake_kubectl(namespace, args, payload=None):
            self.assertEqual(args, ['create', '-f', '-', '-o', 'json'])
            secrets[payload['metadata']['name']] = payload
            return payload
        with tempfile.TemporaryDirectory() as directory:
            values_path = Path(directory) / 'values.yaml'
            output = Path(directory) / 'candidate.yaml'
            values_path.write_text((ROOT / 'values-dev.yaml').read_text())
            argv = ['prepare', '--values', str(values_path), '--output-values', str(output)]
            if create:
                argv.append('--create')
            with patch.object(sys, 'argv', argv), \
                 patch.object(oauth, 'get_secret', side_effect=lambda namespace, name: secrets.get(name)), \
                 patch.object(oauth, 'kubectl', side_effect=fake_kubectl) as write:
                with contextlib.redirect_stdout(io.StringIO()):
                    oauth.main()
            self.assertEqual(secrets['bdv-trino-coordinator']['data'], original)
            if create:
                write.assert_called_once()
                self.assertIn('bdv-trino-coordinator-oidc', secrets)
                self.assertTrue(output.exists())
                self.assertNotIn('unit-test-only-client-secret', output.read_text())
            else:
                write.assert_not_called()
                self.assertNotIn('bdv-trino-coordinator-oidc', secrets)
                self.assertFalse(output.exists())

    def test_plan_does_not_write(self):
        self.run_preparation(False)

    def test_create_preserves_original_secret(self):
        self.run_preparation(True)


if __name__ == '__main__':
    unittest.main()
