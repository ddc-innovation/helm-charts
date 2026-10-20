#!/usr/bin/env python3
"""Register the bundled Ranger service definition exactly once."""
import base64
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request


def fail(message):
    raise RuntimeError(message)


def request(endpoint, authorization, method, payload=None):
    headers = {'Authorization': authorization, 'Accept': 'application/json'}
    data = None
    if payload is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(payload).encode()
    call = urllib.request.Request(endpoint, data=data, headers=headers, method=method)
    with urllib.request.urlopen(call, timeout=10) as response:
        body = response.read()
        return response.status, json.loads(body) if body else None


def contains_name(value, expected):
    if isinstance(value, dict):
        return value.get('name') == expected or any(
            contains_name(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(contains_name(item, expected) for item in value)
    return False


def main():
    base_url = os.environ['RANGER_URL'].rstrip('/')
    timeout = int(os.environ.get('RANGER_REGISTRATION_TIMEOUT', '600'))
    password_path = Path('/ranger-credentials/rangerAdmin_password')
    plugin_path = Path('/opt/bdv-policy-manager/mdaca-ranger-plugin.json')
    if not password_path.is_file() or not plugin_path.is_file():
        fail('Ranger credential or plugin definition is missing')
    password = password_path.read_bytes()
    if not password or password.endswith((b'\n', b'\r')):
        fail('Ranger administrator password is empty or has a trailing newline')
    plugin = json.loads(plugin_path.read_text())
    plugin_name = plugin.get('name')
    if plugin_name != 'mdaca-ranger-plugin':
        fail('Unexpected Ranger plugin definition name')
    authorization = 'Basic ' + base64.b64encode(b'admin:' + password).decode()
    definitions_url = base_url + '/service/plugins/definitions'
    status_url = base_url + '/service/metrics/status'
    deadline = time.monotonic() + timeout
    while True:
        try:
            status, _ = request(status_url, authorization, 'GET')
            if status == 200:
                break
        except urllib.error.HTTPError as exc:
            if exc.code not in (502, 503, 504):
                fail('Ranger readiness authentication or endpoint check failed')
        except (OSError, urllib.error.URLError):
            pass
        if time.monotonic() >= deadline:
            fail('Ranger did not become ready before plugin registration timeout')
        time.sleep(5)
    status, definitions = request(definitions_url, authorization, 'GET')
    if status != 200:
        fail('Ranger plugin definition lookup failed')
    if contains_name(definitions, plugin_name):
        print('PASS: Ranger plugin definition already registered', flush=True)
        return
    status, _ = request(definitions_url, authorization, 'POST', plugin)
    if status not in (200, 201):
        fail('Ranger plugin definition registration failed')
    status, definitions = request(definitions_url, authorization, 'GET')
    if status != 200 or not contains_name(definitions, plugin_name):
        fail('Ranger plugin definition registration could not be verified')
    print('PASS: Ranger plugin definition registered and verified', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (KeyError, ValueError, OSError, RuntimeError, urllib.error.URLError) as exc:
        print('FAIL: Ranger plugin registration: ' + str(exc), flush=True)
        raise SystemExit(1)
