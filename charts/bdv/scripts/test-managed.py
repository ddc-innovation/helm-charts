#!/usr/bin/env python3
"""Read-only runtime checks plus opt-in authenticated functional SQL tests."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from values_check import load, merge


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('-f', '--values', required=True)
    p.add_argument('-n', '--namespace', default='bdv')
    p.add_argument('-r', '--release', default='bdv')
    p.add_argument('--user')
    p.add_argument('--sql', choices=['identity', 'read', 'write', 'create-smoke'])
    p.add_argument('--probe-id', type=int, help='Write-test BIGINT; default is a fresh timestamp ID')
    args = p.parse_args()
    v = merge(load(Path(__file__).resolve().parents[1] / 'values.yaml'), load(args.values))
    # Chart fullname follows helper behavior for the supported default bdv release.
    chart_name = v.get('nameOverride') or 'bdv'
    name = args.release if chart_name in args.release else args.release + '-' + chart_name
    name = name[:45].rstrip('-')
    kube = ['kubectl', '-n', args.namespace]
    if args.sql:
        if not args.user or args.user not in v['ranger']['authorization']['users']:
            raise ValueError('Choose an explicitly configured Ranger user')
        probe = args.probe_id if args.probe_id is not None else time.time_ns()
        if not 0 < probe < 9223372036854775807:
            raise ValueError('Probe ID must be a positive BIGINT')
        sql = {
            'identity': 'SELECT current_user',
            'read': 'SELECT probe_id, note FROM hive.default.bdv_smoke_20261019 ORDER BY probe_id',
            'write': f"INSERT INTO hive.default.bdv_smoke_20261019 (probe_id, note) VALUES ({probe}, 'authenticated write test'); SELECT probe_id,note FROM hive.default.bdv_smoke_20261019 WHERE probe_id={probe}",
            'create-smoke': 'CREATE TABLE IF NOT EXISTS hive.default.bdv_smoke_20261019 (probe_id BIGINT, note VARCHAR) WITH (format=\'PARQUET\')',
        }[args.sql]
        if args.sql == 'create-smoke' and args.user != v['ranger']['authorization'].get('smokeCreator'):
            raise ValueError('Set ranger.authorization.smokeCreator to this explicit user first')
        cmd = kube + ['exec', '-it', 'deployment/' + name + '-coordinator', '-c', 'coordinator', '--',
                      '/usr/bin/trino', '--server', 'https://' + v['ingress']['bdvHost'],
                      '--external-authentication', '--external-authentication-redirect-handler=PRINT',
                      '--user', args.user, '--no-progress', '--output-format', 'TSV_HEADER', '--execute', sql]
        subprocess.run(cmd, check=True)
        return
    for role in ('minio', 'hive', 'ranger', 'coordinator', 'worker'):
        if role == 'minio' and v['objectStorage']['provider'] != 'minio':
            continue
        subprocess.run(kube + ['rollout', 'status', 'deployment/' + name + '-' + role, '--timeout=5m'], check=True)
    selector = 'app.kubernetes.io/instance=' + args.release
    jobs = json.loads(subprocess.check_output(kube + ['get', 'jobs', '-l', selector, '-o', 'json']))
    if v['ranger']['authorization']['enabled']:
        matching = [j for j in jobs['items'] if j['metadata']['name'].startswith(name + '-authorization-')]
        if not matching:
            raise ValueError('Authorization Job not found')
        latest = max(matching, key=lambda j: j['metadata']['creationTimestamp'])
        if not any(c['type'] == 'Complete' and c['status'] == 'True' for c in latest.get('status', {}).get('conditions', [])):
            raise ValueError('Latest authorization Job has not completed')
        print('PASS: latest authorization reconciliation completed')
    if v['bdv']['autoscaling']['enabled']:
        hpa = json.loads(subprocess.check_output(kube + ['get', 'hpa', name + '-worker', '-o', 'json']))
        if hpa['spec']['minReplicas'] != v['bdv']['autoscaling']['minReplicas']:
            raise ValueError('HPA minimum differs')
        if hpa.get('status', {}).get('currentReplicas', 0) < hpa['spec']['minReplicas']:
            raise ValueError('HPA has not reached the configured worker minimum')
        if not any(c['type'] == 'ScalingActive' and c['status'] == 'True' for c in hpa.get('status', {}).get('conditions', [])):
            raise ValueError('HPA metrics are not active; inspect kubectl describe hpa')
        print('PASS: worker HPA has active metrics')
    if v['bdv']['oauth2']['enabled']:
        command = ['timeout', '20s', '/usr/bin/trino', '--server', 'http://127.0.0.1:8080',
                   '--user', 'trino-healthcheck', '--no-progress', '--execute', 'SELECT 1']
        result = subprocess.run(kube + ['exec', 'deployment/' + name + '-coordinator', '-c', 'coordinator', '--'] + command,
                                capture_output=True, text=True)
        if result.returncode == 0 or 'Authentication over HTTP is not enabled' not in result.stdout + result.stderr:
            raise ValueError('Unauthenticated query rejection was not confirmed')
        print('PASS: unauthenticated local SQL rejected')
    print('PASS: runtime checks; run authenticated identity/read/write tests separately')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as exc:
        sys.exit('FAIL: ' + str(exc))
