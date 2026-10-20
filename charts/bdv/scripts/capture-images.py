#!/usr/bin/env python3
"""Archive pinned image metadata, optionally binaries, outside the Git checkout."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True)
    p.add_argument('--pull',action='store_true',help='Explicitly pull every pinned image first')
    p.add_argument('--save',action='store_true',help='Also docker save each image; potentially many GB')
    args=p.parse_args()
    os.umask(0o077)
    chart=Path(__file__).resolve().parents[1]
    output=Path(args.output).resolve()
    repo=chart.parents[1]
    if output==repo or repo in output.parents:
        raise SystemExit('Keep image archives outside the bdv-helm checkout')
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    lock=json.loads((chart/'images/image-lock.json').read_text())
    manifest={'capturedAt':datetime.now(timezone.utc).isoformat(),'images':[]}
    for name,item in lock['runtime'].items():
        ref=item['image']
        if args.pull:subprocess.run(['docker','pull',ref],check=True)
        raw=subprocess.check_output(['docker','image','inspect',ref])
        image=json.loads(raw)[0]
        config=image.get('Config',{})
        labels=config.get('Labels') or {}
        record={'component':name,'requestedImage':ref,'imageId':image['Id'],
                'repoDigests':image.get('RepoDigests',[]),'repoTags':image.get('RepoTags',[]),'os':image.get('Os'),
                'architecture':image.get('Architecture'),'rootFS':image.get('RootFS'),
                'oci':{k:v for k,v in labels.items() if k in (
                    'org.opencontainers.image.source','org.opencontainers.image.revision',
                    'org.opencontainers.image.version')}}
        (output/(name+'.json')).write_text(json.dumps(record,indent=2)+'\n')
        if args.save:
            archive=output/(name+'.tar')
            subprocess.run(['docker','image','save','--output',str(archive),ref],check=True)
            with archive.open('rb') as f:
                record['archiveSha256']=hashlib.file_digest(f,'sha256').hexdigest()
            record['archiveFile']=archive.name
        manifest['images'].append(record)
        print('Captured:',name)
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('Private capture directory:',output)
    print('No Kubernetes objects or credentials were read; no images pushed')


if __name__=='__main__':main()
