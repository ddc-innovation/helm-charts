#!/usr/bin/env python3
"""Flag private/generated filenames already tracked under bdv-helm. Not a secret scanner."""
from pathlib import PurePosixPath
import subprocess
import sys

paths=subprocess.check_output(['git','ls-files','-z','--','bdv-helm']).decode().split('\0')
blocked=[]
for name in filter(None,paths):
    p=PurePosixPath(name)
    if (p.name in {'values-site.yaml','values-test.yaml','values-dev-oidc.yaml','ranger-install.properties','.env'}
        or p.name.startswith('.env.') or '.bak' in p.name or p.name.endswith(('.jar','.pyc','.tar','.tar.gz'))
        or any(x in p.parts for x in ('backups','__pycache__','evidence','image-archives'))):
        blocked.append(name)
if blocked:
    print('STOP: review these tracked private/generated paths before committing:')
    print('\n'.join(blocked))
    sys.exit(1)
print('PASS: no known private/generated filenames tracked under bdv-helm')
print('Review staged contents too; this filename check is not a secret scanner')
