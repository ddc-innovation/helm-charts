"""Exercise the real shell helper against a local server with independent JWT verification."""
import base64
import hashlib
import hmac
import http.server
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import unittest
import tempfile
import yaml

SCRIPT = Path(__file__).resolve().parents[1] / 'files/worker-drain.sh'
SECRET = 'fixture-only-not-an-application-credential'


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, data):
        self.send_response(code)
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        self.send(200, {'state': self.server.state, 'starting': False})

    def do_PUT(self):
        try:
            header, payload, signature = self.headers['X-Trino-Internal-Bearer'].split('.')
            # RFC 5869 extract with omitted salt, then expand one block.
            prk = hmac.digest(bytes(32), SECRET.encode(), 'sha256')
            key = hmac.digest(prk, b'internal-communication\x01', 'sha256')
            expected = base64.urlsafe_b64encode(hmac.digest(key, (header + '.' + payload).encode(), 'sha256')).decode().rstrip('=')
            claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
            valid = hmac.compare_digest(signature, expected) and claims['exp'] > time.time()
        except Exception:
            valid = False
        if not valid:
            self.send(401, {})
            return
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if body != 'SHUTTING_DOWN':
            self.send(400, {'code': 'JSON_PARSING_ERROR'})
            return
        self.server.state = 'SHUTTING_DOWN'
        self.send(200, {})

        def stop():
            time.sleep(0.3)
            self.server.shutdown()
            self.server.server_close()
        threading.Thread(target=stop, daemon=True).start()


class WorkerDrainTests(unittest.TestCase):
    def setUp(self):
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.state = 'ACTIVE'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        if self.thread.is_alive():
            self.server.shutdown()
        self.server.server_close()

    def run_helper(self, mode, secret=SECRET):
        env = dict(os.environ, BDV_INTERNAL_SHARED_SECRET=secret,
                   BDV_DRAIN_PORT=str(self.server.server_port))
        return subprocess.run(['bash', str(SCRIPT), mode, '12'], env=env,
                              capture_output=True, text=True, timeout=18)

    def test_probe_signature_matches_independent_hkdf_and_does_not_change_state(self):
        result = self.run_helper('probe')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.server.state, 'ACTIVE')
        self.assertNotIn(SECRET, result.stdout + result.stderr)

    def test_wrong_secret_fails_without_state_change(self):
        result = self.run_helper('probe', 'wrong-fixture')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.server.state, 'ACTIVE')

    def test_readiness_rejects_draining(self):
        self.assertEqual(self.run_helper('ready').returncode, 0)
        self.server.state = 'SHUTTING_DOWN'
        self.assertNotEqual(self.run_helper('ready').returncode, 0)

    def test_hook_waits_for_listener_exit(self):
        started = time.monotonic()
        result = self.run_helper('shutdown-wait')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertGreater(time.monotonic() - started, 1)
        self.assertIn('listener closed', result.stdout)


class PreparationTests(unittest.TestCase):
    def test_site_inherits_files_from_chart_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'values-site.yaml'
            original = {'configuration': {'managed': True},
                        'bdv': {'autoscaling': {'enabled': True}},
                        'ranger': {'installPropertiesTemplate': 'fixture-retain'}}
            path.write_text(yaml.safe_dump(original))
            result = subprocess.run(['python3', str(SCRIPT.parents[0].parent / 'scripts/prepare-worker-drain.py'), str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            values = yaml.safe_load(path.read_text())
            self.assertIn('shutdown.grace-period=120s', values['configuration']['files']['worker']['config.properties'])
            self.assertIn('coordinator=false', values['configuration']['files']['worker']['config.properties'])
            self.assertEqual(values['ranger'], original['ranger'])
            self.assertFalse(values['bdv']['autoscaling']['scaleDownEnabled'])
            backups = list(path.parent.glob('values-site.yaml.bak-*'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(yaml.safe_load(backups[0].read_text()), original)


if __name__ == '__main__':
    unittest.main()
