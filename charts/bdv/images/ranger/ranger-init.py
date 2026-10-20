#!/usr/bin/env python3
"""Prepare a shared Ranger home and run the image's vendor setup.sh.

Database administrator credentials are never mounted. Database application
credentials are read from Secret files and injected only into a protected,
ephemeral install.properties used by the vendor installer.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys

SOURCE = Path(os.environ.get('RANGER_IMAGE_HOME', '/opt/bdv-policy-manager'))
TARGET = Path(os.environ.get('RANGER_SHARED_HOME', '/work/ranger-home'))
TEMPLATE = Path(os.environ.get('RANGER_INSTALL_TEMPLATE', '/config/install.properties'))
DB_DIR = Path(os.environ.get('RANGER_DB_SECRET_DIR', '/db'))
RANGER_CREDENTIALS_DIR = Path(os.environ.get(
    'RANGER_CREDENTIALS_SECRET_DIR', '/ranger-credentials'))


def fail(message):
    raise RuntimeError(message)


def read_secret(directory, name, description):
    path = directory / name
    if not path.is_file():
        fail('missing ' + description + ' Secret key: ' + name)
    value = path.read_text()
    if value.endswith(('\n', '\r')):
        fail(description + ' Secret key has a trailing newline: ' + name)
    if not value:
        fail('empty ' + description + ' Secret key: ' + name)
    return value


def render(template, database, service_passwords):
    text = template.read_text()
    tokens = {
        '@@BDV_DB_HOST@@': database['host'],
        '@@BDV_DB_PORT@@': database['port'],
        '@@BDV_DB_NAME@@': database['database'],
        '@@BDV_DB_USER@@': database['username'],
        '@@BDV_DB_PASSWORD@@': database['password'],
        '@@RANGER_ADMIN_PASSWORD@@': service_passwords['rangerAdmin_password'],
        '@@RANGER_TAGSYNC_PASSWORD@@': service_passwords['rangerTagsync_password'],
        '@@RANGER_USERSYNC_PASSWORD@@': service_passwords['rangerUsersync_password'],
        '@@RANGER_KEYADMIN_PASSWORD@@': service_passwords['keyadmin_password'],
    }
    for token, value in tokens.items():
        if text.count(token) != 1:
            fail('install.properties must contain exactly one ' + token)
        if '\x00' in value or '\n' in value or '\r' in value:
            fail('invalid control character in Ranger database Secret')
        text = text.replace(token, value)
    required = {
        'setup_mode': 'SeparateDBA',
        'DB_FLAVOR': 'MYSQL',
        'SQL_CONNECTOR_JAR': '/opt/mysql-connector-j-9.7.0.jar',
    }
    properties = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            properties[key.strip()] = value.strip().strip("'\"")
    for key, expected in required.items():
        if properties.get(key) != expected:
            fail('install.properties requires ' + key + '=' + expected)
    if properties.get('db_root_password'):
        fail('Ranger init must not receive a database administrator password')
    return text


def main():
    if not SOURCE.is_dir() or not TEMPLATE.is_file():
        fail('Ranger image or configuration template is missing')
    credentials = {key: read_secret(DB_DIR, key, 'Ranger database') for key in
                   ('host', 'port', 'database', 'username', 'password')}
    service_passwords = {
        key: read_secret(RANGER_CREDENTIALS_DIR, key, 'Ranger service credential')
        for key in ('rangerAdmin_password', 'rangerTagsync_password',
                    'rangerUsersync_password', 'keyadmin_password')
    }

    if TARGET.exists():
        # Safe retry cleanup is restricted to this fixed EmptyDir path.
        shutil.rmtree(TARGET)
    shutil.copytree(SOURCE, TARGET, symlinks=True)
    install_properties = TARGET / 'install.properties'
    install_properties.write_text(render(TEMPLATE, credentials, service_passwords))
    install_properties.chmod(0o600)

    environment = os.environ.copy()
    environment.update({
        'RANGER_HOME': str(TARGET),
        'RANGER_ADMIN_HOME': str(TARGET),
        'RANGER_ADMIN_CONF': str(TARGET),
    })
    process = subprocess.Popen(
        [str(TARGET / 'setup.sh')], cwd=TARGET, env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    secrets = [credentials['password'], *service_passwords.values()]
    for line in process.stdout:
        for secret in secrets:
            line = line.replace(secret, '[REDACTED]')
        sys.stdout.write(line)
    status = process.wait()
    if status:
        fail('vendor Ranger setup.sh failed')

    install_properties.unlink(missing_ok=True)
    expected = TARGET / 'ews/webapp/WEB-INF/classes/conf/ranger-admin-site.xml'
    if not expected.is_file():
        fail('vendor setup completed without Ranger runtime configuration')
    if not any(TARGET.rglob('*.jceks')):
        fail('vendor setup completed without a Ranger credential store')
    secret_bytes = [value.encode() for value in secrets]
    inspect_suffixes = {'.conf', '.json', '.jceks', '.log', '.properties', '.sh', '.txt', '.xml'}
    for path in TARGET.rglob('*'):
        if path.is_file() and path.suffix.lower() in inspect_suffixes and path.stat().st_size <= 16 * 1024 * 1024:
            content = path.read_bytes()
            if any(secret in content for secret in secret_bytes):
                fail('vendor setup retained a cleartext password in a runtime file')
    print('PASS: vendor Ranger setup completed; shared runtime home is ready', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print('FAIL: Ranger vendor initialization: ' + str(exc), flush=True)
        raise SystemExit(1)
