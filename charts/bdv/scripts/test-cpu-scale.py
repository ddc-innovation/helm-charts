#!/usr/bin/env python3
"""Scoped bdv-test CPU acceptance: configure, seed, load, or evidence."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import yaml
from values_check import load, merge

TABLE = 'bdv_cpu_load_20261019'
USER = 'richard.stevens@ddc-dine.com'
QUERY = """
SELECT sum(CAST(from_big_endian_64(substr(
 sha256(to_utf8(t.note || ':' || CAST(n AS VARCHAR))), 1, 8)) AS DOUBLE))
FROM hive.default.bdv_cpu_load_20261019 t
CROSS JOIN UNNEST(sequence(1, 100)) AS u(n)
""".strip()
SEED = """
CREATE TABLE hive.default.bdv_cpu_load_20261019 (
 probe_id BIGINT, note VARCHAR, batch_id INTEGER
) WITH (format='PARQUET', partitioned_by=ARRAY['batch_id']);
INSERT INTO hive.default.bdv_cpu_load_20261019 (probe_id,note,batch_id)
SELECT probe_id,concat(digest,digest,digest,digest),batch_id FROM (
 SELECT CAST((a.n-1)*1000+b.n AS BIGINT) AS probe_id,
 to_hex(sha256(to_utf8(CAST((a.n-1)*1000+b.n AS VARCHAR)))) AS digest,
 CAST(mod(a.n,32) AS INTEGER) AS batch_id
 FROM UNNEST(sequence(1,1000)) a(n)
 CROSS JOIN UNNEST(sequence(1,1000)) b(n)
);
SELECT batch_id,count(*) AS rows_per_partition
FROM hive.default.bdv_cpu_load_20261019 GROUP BY batch_id ORDER BY batch_id;
"""

def profile(path):
    chart=Path(__file__).resolve().parents[1]
    v=merge(load(chart/'values.yaml'),load(path))
    if (v['ranger']['authorization']['serviceName']!='bdv-test-trino'
        or v['ingress']['bdvHost']!='bdv-test.dinecodetalkers.com'
        or v['objectStorage']['bucket']!='bdv-test-warehouse'):
        raise ValueError('Expected isolated bdv-test profile')
    a=v['bdv']['autoscaling']
    if not a['enabled'] or (a['minReplicas'],a['maxReplicas'],a['targetCPUUtilizationPercentage'])!=(2,6,70):
        raise ValueError('Expected enabled HPA min=2 max=6 CPU target=70')
    return v

def grants(user):
    resources={'catalog':['hive'],'schema':['default'],'table':[TABLE]}
    return [dict(name='bdv-test-cpu-load-table',resources=resources,
                 users=[user],accesses=['create','insert','show','drop']),
            dict(name='bdv-test-cpu-load-columns',
                 resources={**resources,'column':['probe_id','note','batch_id']},
                 users=[user],accesses=['select'])]

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['configure','seed','load','evidence'])
    p.add_argument('-f','--values',default=str(Path(__file__).resolve().parents[1]/'values-test.yaml'))
    p.add_argument('--user',default=USER)
    p.add_argument('--output',help='New private evidence directory outside a Git repository')
    args=p.parse_args()
    os.umask(0o077)
    path=Path(args.values).resolve(); v=profile(path)
    if args.user not in v['ranger']['authorization']['users']:
        raise ValueError('User must already exist in managed authorization')
    if args.action=='configure':
        site=load(path); items=site['ranger']['authorization']['grants']
        for grant in grants(args.user):
            matches=[x for x in items if x['name']==grant['name']]
            if matches and matches!=[grant]:
                raise ValueError('Existing load grant differs; review instead of overwriting')
        additions=[g for g in grants(args.user) if not any(x['name']==g['name'] for x in items)]
        if not additions:
            print('PASS: load grants already configured; no file changed'); return
        backup=path.with_name(path.name+'.bak-load-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        shutil.copy2(path,backup); items.extend(additions)
        path.write_text(yaml.safe_dump(site,sort_keys=False)); path.chmod(0o600)
        print('Prepared scoped table grants. Backup:',backup)
        print('Apply with bash scripts/fresh-test.sh runtime before seeding'); return
    kube=['kubectl','-n','bdv-test']
    label=subprocess.check_output(['kubectl','get','namespace','bdv-test','-o',
             'jsonpath={.metadata.labels.bdv-test-isolated}'],text=True)
    if label!='true': raise ValueError('Expected bdv-test-isolated namespace label')
    if args.action=='evidence':
        if not args.output: raise ValueError('--output is required')
        out=Path(args.output).resolve()
        for parent in [out,*out.parents]:
            if (parent/'.git').exists(): raise ValueError('Evidence must stay outside Git repositories')
        out.mkdir(parents=True,mode=0o700,exist_ok=False)
        commands={
          'hpa.json':kube+['get','hpa','bdv-worker','-o','json'],
          'hpa-describe.txt':kube+['describe','hpa','bdv-worker'],
          'workers.json':kube+['get','pods','-l','app.kubernetes.io/instance=bdv,app.kubernetes.io/component=worker','-o','json'],
          'deployments.txt':kube+['get','deployments','-l','app.kubernetes.io/instance=bdv','-o','wide'],
          'storage.txt':kube+['get','pvc','-o','wide'],
          'release-history.json':['helm','-n','bdv-test','history','bdv','-o','json'],
        }
        # Do not persist Pod specs: environment values could contain credentials.
        for name,cmd in commands.items():
            data=subprocess.check_output(cmd,text=True)
            if name=='workers.json':
                pods=json.loads(data)
                data=json.dumps([{'name':x['metadata']['name'],
                   'deletionTimestamp':x['metadata'].get('deletionTimestamp'),
                   'phase':x.get('status',{}).get('phase'),
                   'containers':[{k:c.get(k) for k in ('name','ready','restartCount')}
                       for c in x.get('status',{}).get('containerStatuses',[])]}
                   for x in pods['items']],indent=2)
            (out/name).write_text(data)
        (out/'capture-time.txt').write_text(datetime.now(timezone.utc).isoformat()+'\n')
        print('Captured non-Secret operational evidence:',out)
        print('Authentication URLs, Secret bodies, Helm values and raw pod specs are excluded'); return
    sql=SEED if args.action=='seed' else ';\n'.join([QUERY]*20)+';'
    if args.action=='seed':
        print('Seed once only. CREATE deliberately refuses an existing table.',flush=True)
        print('If the table already has one million rows, skip seed.',flush=True)
    cmd=kube+['exec','-it','deployment/bdv-coordinator','-c','coordinator','--',
       'timeout','900s','/usr/bin/trino','--server','https://'+v['ingress']['bdvHost'],
       '--external-authentication','--external-authentication-redirect-handler=PRINT',
       '--user',args.user,'--no-progress','--output-format','TSV_HEADER','--execute',sql]
    subprocess.run(cmd,check=True)

if __name__=='__main__':
    try: main()
    except (ValueError,KeyError,subprocess.CalledProcessError) as exc:
        sys.exit('FAIL: '+str(exc))
