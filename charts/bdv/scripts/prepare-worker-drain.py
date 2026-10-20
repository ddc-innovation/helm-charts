#!/usr/bin/env python3
"""Prepare worker draining in site values. Does not contact or modify the cluster."""
from datetime import datetime
from pathlib import Path
import shutil
import sys
import yaml
from values_check import load, merge

path = Path(sys.argv[1] if len(sys.argv) > 1 else 'values-site.yaml')
values = yaml.safe_load(path.read_text())
if values.get('configuration', {}).get('managed') is not True:
    raise SystemExit('FAIL: managed configuration is required')
effective = merge(load(Path(__file__).resolve().parents[1] / 'values.yaml'), values)
config = effective['configuration']['files']['worker']['config.properties']
lines = [line for line in config.splitlines() if not line.strip().startswith('shutdown.grace-period=')]
values['configuration'].setdefault('files', {}).setdefault('worker', {})['config.properties'] = '\n'.join(lines) + '\nshutdown.grace-period=120s\n'
values.setdefault('bdv', {})['workerShutdown'] = {
    'enabled': True, 'terminationGracePeriodSeconds': 900, 'waitSeconds': 840,
}
values['bdv'].setdefault('autoscaling', {})['scaleDownEnabled'] = False
backup = path.with_name(path.name + '.bak-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
shutil.copy2(path, backup)
path.write_text(yaml.safe_dump(values, sort_keys=False))
path.chmod(0o600)
print('Prepared worker drain hook; automatic scale-down remains disabled')
print('Backup:', backup)
