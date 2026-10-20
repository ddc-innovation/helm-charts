#!/usr/bin/env python3
"""Run the bundled combined Hive/Ranger provisioner in bootstrap-only mode.

Existing application Secrets are still handled exclusively by provision.py.
This wrapper never edits/replaces an existing password and prints no credentials.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, '/operator')
import provision

original_token_urlsafe = provision.secrets.token_urlsafe


def policy_password(nbytes=None):
    # Preserve the complete random token's entropy and guarantee the known MEDIUM
    # policy's uppercase/lowercase/digit/special classes without quote/backslash.
    return original_token_urlsafe(nbytes) + 'Aa1!'


def configure_kubernetes(namespace, credential_dir=Path('/var/run/secrets/kubernetes.io/serviceaccount')):
    token = credential_dir / 'token'
    ca = credential_dir / 'ca.crt'
    for path in (token, ca):
        if not path.is_file() or not os.access(path, os.R_OK):
            raise provision.ProvisionError('Projected Kubernetes API credentials are missing or unreadable')
    config = {
        'apiVersion': 'v1', 'kind': 'Config',
        'clusters': [{'name': 'in-cluster', 'cluster': {
            'server': 'https://kubernetes.default.svc:443',
            'certificate-authority': str(ca),
        }}],
        'users': [{'name': 'bootstrap', 'user': {'tokenFile': str(token)}}],
        'contexts': [{'name': 'bootstrap', 'context': {
            'cluster': 'in-cluster', 'user': 'bootstrap', 'namespace': namespace,
        }}],
        'current-context': 'bootstrap',
    }
    fd, filename = tempfile.mkstemp(prefix='bdv-kubeconfig-', suffix='.json')
    with os.fdopen(fd, 'w') as stream:
        json.dump(config, stream)
    os.chmod(filename, 0o600)
    os.environ['KUBECONFIG'] = filename
    return Path(filename)


def check_api(namespace):
    # Names only: no Secret bodies, credential values or raw subprocess errors.
    configs = json.loads(os.environ['DATABASES_JSON'])
    if set(configs) != {'hive', 'ranger'}:
        raise provision.ProvisionError('Bootstrap requires Hive and Ranger database contracts')
    for component in ('hive', 'ranger'):
        result = subprocess.run(
            ['kubectl', '--request-timeout=20s', '--namespace', namespace,
             'get', 'secret', configs[component]['secret'], '--ignore-not-found', '-o', 'name'],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30,
        )
        if result.returncode:
            message = result.stderr.decode(errors='replace').lower()
            if 'forbidden' in message:
                reason = 'Kubernetes API denied Secret read; check bootstrap ServiceAccount RBAC'
            elif 'unauthorized' in message or 'provide credentials' in message:
                reason = 'Kubernetes API rejected the projected ServiceAccount credentials'
            elif 'x509' in message:
                reason = 'Kubernetes API certificate verification failed'
            elif 'no such host' in message:
                reason = 'Kubernetes API service DNS resolution failed'
            elif 'timeout' in message or 'timed out' in message or 'connection refused' in message:
                reason = 'Kubernetes API connection failed; check pod networking and policy'
            else:
                reason = 'Kubernetes API preflight failed; inspect pod configuration securely'
            raise provision.ProvisionError(reason)
    print('PASS: Hive and Ranger Kubernetes API and Secret access confirmed', flush=True)


def grant_ranger_installer_privileges():
    """Grant only the extra DB-scoped privileges required by Ranger setup.sh.

    The pinned provisioner already creates the account and grants ordinary DDL/DML.
    Credentials remain in protected files; no password is placed in argv or SQL.
    """
    configs = json.loads(os.environ['DATABASES_JSON'])
    ranger = configs['ranger']
    database = provision.identifier(ranger['database'])
    username = provision.identifier(ranger['username'])
    account_host = os.environ['MYSQL_ACCOUNT_HOST']
    provision.literal(account_host)
    admin_user = Path('/admin/username').read_text()
    admin_password = Path('/admin/password').read_text()
    if admin_user.endswith(('\n', '\r')) or admin_password.endswith(('\n', '\r')):
        raise provision.ProvisionError('MySQL administrator Secret contains a trailing newline')

    config = (
        '[client]\n'
        'host=' + provision.option(os.environ['MYSQL_HOST']) + '\n'
        'port=' + provision.option(os.environ.get('MYSQL_PORT', '3306')) + '\n'
        'protocol=TCP\n'
        'user=' + provision.option(admin_user) + '\n'
        'password=' + provision.option(admin_password) + '\n'
        'ssl-mode=' + os.environ['MYSQL_SSL_MODE'] + '\n'
        'connect-timeout=10\n'
    )
    if os.environ['MYSQL_SSL_MODE'] in ('VERIFY_CA', 'VERIFY_IDENTITY'):
        config += 'ssl-ca=' + provision.option(os.environ['MYSQL_CA']) + '\n'
    fd, filename = tempfile.mkstemp(prefix='bdv-mysql-admin-', suffix='.cnf')
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(config)
        os.chmod(filename, 0o600)
        sql = (
            'GRANT CREATE ROUTINE, ALTER ROUTINE, CREATE VIEW, SHOW VIEW ON `' + database + '`.* TO ' +
            provision.literal(username) + '@' + provision.literal(account_host) + ';\n'
        )
        result = subprocess.run(
            ['mysql', '--defaults-file=' + filename, '--batch'],
            input=sql.encode(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=40,
        )
        if result.returncode:
            raise provision.ProvisionError('Ranger installer privilege grant failed')
    finally:
        Path(filename).unlink(missing_ok=True)
    print('PASS: Ranger account has database-scoped vendor installer privileges', flush=True)


def verify_dba_contracts():
    """Check the actual server state without printing accounts or credential data."""
    configs = json.loads(os.environ['DATABASES_JSON'])
    admin = {
        'host': os.environ['MYSQL_HOST'],
        'port': os.environ.get('MYSQL_PORT', '3306'),
        'username': Path('/admin/username').read_text().strip(),
        'password': Path('/admin/password').read_text(),
    }
    if admin['password'].endswith(('\n', '\r')):
        raise provision.ProvisionError('MySQL administrator Secret contains a trailing newline')
    base = {
        'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'DROP', 'ALTER',
        'INDEX', 'REFERENCES', 'CREATE TEMPORARY TABLES', 'LOCK TABLES',
        'EXECUTE',
    }
    ranger_extra = {'CREATE ROUTINE', 'ALTER ROUTINE', 'CREATE VIEW', 'SHOW VIEW'}
    with provision.MySQL(admin, os.environ.get('MYSQL_CA', ''), os.environ['MYSQL_SSL_MODE']) as db:
        for component in ('hive', 'ranger'):
            cfg = configs[component]
            database = provision.identifier(cfg['database'])
            account = (provision.literal(provision.identifier(cfg['username'])) + '@' +
                       provision.literal(os.environ['MYSQL_ACCOUNT_HOST']))
            observed = db.query(
                'SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME '
                'FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = ' + provision.literal(database)
            )
            if observed != ['utf8mb4\tutf8mb4_unicode_ci']:
                raise provision.ProvisionError(component + ' database charset/collation differs from contract')
            grants = db.query('SHOW GRANTS FOR ' + account)
            scope = ' ON `' + database + '`.* '
            granted = set()
            for line in grants:
                if 'WITH GRANT OPTION' in line:
                    raise provision.ProvisionError(component + ' account has GRANT OPTION')
                if line.startswith('GRANT USAGE ON *.* '):
                    continue
                if scope not in line or not line.startswith('GRANT '):
                    raise provision.ProvisionError(component + ' account has an unexpected grant scope')
                privilege_list = line[len('GRANT '):].split(' ON ', 1)[0]
                granted.update(item.strip() for item in privilege_list.split(','))
            required = base | (ranger_extra if component == 'ranger' else set())
            if granted != required:
                raise provision.ProvisionError(component + ' account grants differ from database-scoped contract')
            print('PASS: ' + component + ' database charset and exact restricted grants verified', flush=True)


def main():
    kubeconfig = configure_kubernetes(os.environ['NAMESPACE'])
    try:
        check_api(os.environ['NAMESPACE'])
        provision.secrets.token_urlsafe = policy_password
        provision.main()
        grant_ranger_installer_privileges()
        verify_dba_contracts()
        print('PASS: Hive and Ranger database bootstrap completed', flush=True)
    finally:
        kubeconfig.unlink(missing_ok=True)


if __name__ == '__main__':
    os.umask(0o077)
    try:
        main()
    except provision.ProvisionError as exc:
        print('FAIL: ' + str(exc), flush=True)
        raise SystemExit(1)
    except (KeyError, ValueError, OSError, subprocess.SubprocessError):
        print('FAIL: provisioning input/tool failure; inspect prerequisites securely', flush=True)
        raise SystemExit(1)
