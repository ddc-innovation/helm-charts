#!/usr/bin/env python3
"""Offline tests for the chart's DBA verification; no Kubernetes or MySQL calls."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'files'))
import provision

spec = importlib.util.spec_from_file_location(
    'bootstrap_entrypoint', ROOT / 'files' / 'bootstrap-entrypoint.py'
)
entrypoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entrypoint)

BASE = ('SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, '
        'REFERENCES, CREATE TEMPORARY TABLES, LOCK TABLES, EXECUTE')
EXTRA = 'CREATE ROUTINE, ALTER ROUTINE, CREATE VIEW, SHOW VIEW'


class FakeMySQL:
    grants = {}
    collation = ['utf8mb4\tutf8mb4_unicode_ci']

    def __init__(self, *_args):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def query(self, sql):
        if sql.startswith('SELECT DEFAULT_CHARACTER_SET_NAME'):
            return self.collation
        if sql.startswith('SHOW GRANTS FOR '):
            return self.grants['ranger' if "'bdv_ranger'" in sql else 'hive']
        raise AssertionError('Unexpected SQL: ' + sql)


class DBAContractTests(unittest.TestCase):
    def setUp(self):
        FakeMySQL.collation = ['utf8mb4\tutf8mb4_unicode_ci']
        FakeMySQL.grants = {
            'hive': [
                "GRANT USAGE ON *.* TO 'bdv_hive'@'192.168.250.0/255.255.255.0'",
                "GRANT " + BASE + " ON `bdv_hive`.* TO 'bdv_hive'@'192.168.250.0/255.255.255.0'",
            ],
            'ranger': [
                "GRANT USAGE ON *.* TO 'bdv_ranger'@'192.168.250.0/255.255.255.0'",
                "GRANT " + BASE + " ON `bdv_ranger`.* TO 'bdv_ranger'@'192.168.250.0/255.255.255.0'",
                "GRANT " + EXTRA + " ON `bdv_ranger`.* TO 'bdv_ranger'@'192.168.250.0/255.255.255.0'",
            ],
        }
        self.env = patch.dict(os.environ, {
            'DATABASES_JSON': json.dumps({
                'hive': {'database': 'bdv_hive', 'username': 'bdv_hive'},
                'ranger': {'database': 'bdv_ranger', 'username': 'bdv_ranger'},
            }),
            'MYSQL_HOST': '192.168.250.15',
            'MYSQL_ACCOUNT_HOST': '192.168.250.0/255.255.255.0',
            'MYSQL_SSL_MODE': 'REQUIRED',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.mysql = patch.object(provision, 'MySQL', FakeMySQL)
        self.mysql.start()
        self.addCleanup(self.mysql.stop)
        original_read_text = Path.read_text
        def read_secret(path, *args, **kwargs):
            if str(path) == '/admin/username':
                return 'dbadmin'
            if str(path) == '/admin/password':
                return 'not-a-real-password'
            return original_read_text(path, *args, **kwargs)
        self.read = patch.object(Path, 'read_text', read_secret)
        self.read.start()
        self.addCleanup(self.read.stop)

    def test_exact_grants(self):
        entrypoint.verify_dba_contracts()

    def test_global_grant_rejected(self):
        FakeMySQL.grants['hive'].append(
            "GRANT SELECT ON *.* TO 'bdv_hive'@'192.168.250.0/255.255.255.0'"
        )
        with self.assertRaises(provision.ProvisionError):
            entrypoint.verify_dba_contracts()

    def test_grant_option_rejected(self):
        FakeMySQL.grants['hive'][1] += ' WITH GRANT OPTION'
        with self.assertRaises(provision.ProvisionError):
            entrypoint.verify_dba_contracts()

    def test_missing_grant_rejected(self):
        FakeMySQL.grants['ranger'].pop()
        with self.assertRaises(provision.ProvisionError):
            entrypoint.verify_dba_contracts()

    def test_collation_drift_rejected(self):
        FakeMySQL.collation = ['latin1\tlatin1_swedish_ci']
        with self.assertRaises(provision.ProvisionError):
            entrypoint.verify_dba_contracts()


if __name__ == '__main__':
    unittest.main()
