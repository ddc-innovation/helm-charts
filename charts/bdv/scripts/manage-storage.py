#!/usr/bin/env python3
"""Print declared Hive schema SQL, or explicitly apply with authenticated Trino."""
import argparse
from pathlib import Path
import re
import subprocess
import sys
from values_check import load, merge, check

ROOT = Path(__file__).resolve().parents[1]


def schema_sql(values):
    errors = check(values, 'offline')
    if errors:
        raise ValueError('; '.join(errors))
    statements = []
    for schema in values.get('hive', {}).get('schemas', []):
        # SQL identifiers and URIs must be safe even without Helm JSON schema checks.
        name, bucket = schema['name'], schema['bucket']
        if not re.fullmatch(r'[a-z][a-z0-9_]*', name) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', bucket):
            raise ValueError('Unsupported schema or bucket identifier')
        statements.append(f"CREATE SCHEMA IF NOT EXISTS hive.{name} WITH (location = 's3a://{bucket}/');")
    for schema in values.get('hive', {}).get('schemas', []):
        statements.append(f"SHOW CREATE SCHEMA hive.{schema['name']};")
    return '\n'.join(statements)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('-f', '--values', action='append', required=True, help='Merged in order after chart defaults')
    p.add_argument('-n', '--namespace', default='bdv')
    p.add_argument('-r', '--release', default='bdv')
    p.add_argument('--apply', action='store_true', help='Create schemas through authenticated SQL; default prints SQL only')
    p.add_argument('--user')
    args = p.parse_args()
    values = load(ROOT / 'values.yaml')
    for path in args.values:
        values = merge(values, load(path))
    sql = schema_sql(values)
    if not sql:
        raise ValueError('No hive.schemas declared')
    if not args.apply:
        print(sql)
        return
    if not values['ranger']['authorization']['enabled'] or args.user not in values['ranger']['authorization']['users']:
        raise ValueError('Apply requires enabled Ranger authorization and an explicitly declared --user')
    if not values['bdv']['oauth2']['enabled'] or not values['ingress']['bdvHost']:
        raise ValueError('Apply requires the configured OAuth HTTPS endpoint')
    chart_name = (values.get('nameOverride') or 'bdv')[:28].rstrip('-')
    name = (args.release if chart_name in args.release else args.release + '-' + chart_name)[:45].rstrip('-')
    subprocess.run(['kubectl', '-n', args.namespace, 'exec', '-it', 'deployment/' + name + '-coordinator',
                    '-c', 'coordinator', '--', '/usr/bin/trino', '--server', 'https://' + values['ingress']['bdvHost'],
                    '--external-authentication', '--external-authentication-redirect-handler=PRINT',
                    '--user', args.user, '--no-progress', '--output-format', 'TSV_HEADER', '--execute', sql], check=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit('FAIL: ' + str(exc))
