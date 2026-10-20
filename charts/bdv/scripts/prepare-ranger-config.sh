#!/usr/bin/env bash
# Extracts the complete vendor install.properties into the chart for Helm's ConfigMap.
set -Eeuo pipefail
umask 077

IMAGE='rstevens198238/mdaca-policy-manager@sha256:1112b6976a88393d863754d37b69258789cd34a608fdc1719fd4fdbfeec5024e'
CHART_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="$CHART_DIR/files"
VALUES_FILE=''
if [[ $# -eq 2 && "$1" == --values ]]; then VALUES_FILE="$2"; shift 2; fi
[[ $# -eq 0 ]] || { echo 'Usage: prepare-ranger-config.sh [--values values-site.yaml]' >&2; exit 2; }

for tool in docker python3; do
  command -v "$tool" >/dev/null || { echo "Missing required tool: $tool" >&2; exit 2; }
done

mkdir -p "$OUTPUT_DIR"
chmod 0700 "$OUTPUT_DIR"
CID="$(docker create "$IMAGE")"
VENDOR_FILE="$(mktemp)"
trap 'docker rm "$CID" >/dev/null 2>&1 || true; rm -f "$VENDOR_FILE"' EXIT
docker cp "$CID":/opt/bdv-policy-manager/install.properties "$VENDOR_FILE" >/dev/null

SOURCE="$VENDOR_FILE" TARGET="$OUTPUT_DIR/ranger-install.properties" python3 - <<'PY'
import os
import re
from pathlib import Path

source = Path(os.environ['SOURCE'])
target = Path(os.environ['TARGET'])
text = source.read_text()
settings = {
    'setup_mode': 'SeparateDBA',
    'PYTHON_COMMAND_INVOKER': 'python3',
    'DB_FLAVOR': 'MYSQL',
    'SQL_CONNECTOR_JAR': '/opt/mysql-connector-j-9.7.0.jar',
    'db_root_password': '',
    'db_host': '@@BDV_DB_HOST@@:@@BDV_DB_PORT@@',
    'db_ssl_enabled': 'true',
    'db_ssl_required': 'true',
    'db_ssl_verifyServerCertificate': 'false',
    'db_name': '@@BDV_DB_NAME@@',
    'db_user': '@@BDV_DB_USER@@',
    'db_password': '@@BDV_DB_PASSWORD@@',
    'rangerAdmin_password': '@@RANGER_ADMIN_PASSWORD@@',
    'rangerTagsync_password': '@@RANGER_TAGSYNC_PASSWORD@@',
    'rangerUsersync_password': '@@RANGER_USERSYNC_PASSWORD@@',
    'keyadmin_password': '@@RANGER_KEYADMIN_PASSWORD@@',
    'policymgr_http_enabled': 'true',
    'policymgr_external_url': 'https://bdv-policy-manager.dinecodetalkers.com',
}
lines = text.splitlines()
for key, value in settings.items():
    pattern = re.compile(r'^\s*' + re.escape(key) + r'\s*=')
    positions = [index for index, line in enumerate(lines) if pattern.match(line)]
    replacement = key + '=' + value
    if positions:
        lines[positions[0]] = replacement
        for index in reversed(positions[1:]):
            del lines[index]
    else:
        lines.append(replacement)
rendered = '\n'.join(lines) + '\n'
for key, value in settings.items():
    active = [line for line in rendered.splitlines()
              if re.match(r'^\s*' + re.escape(key) + r'\s*=', line)]
    if active != [key + '=' + value]:
        raise SystemExit('Failed to set exactly one active property: ' + key)
tokens = (
    '@@BDV_DB_HOST@@', '@@BDV_DB_PORT@@', '@@BDV_DB_NAME@@',
    '@@BDV_DB_USER@@', '@@BDV_DB_PASSWORD@@',
    '@@RANGER_ADMIN_PASSWORD@@', '@@RANGER_TAGSYNC_PASSWORD@@',
    '@@RANGER_USERSYNC_PASSWORD@@', '@@RANGER_KEYADMIN_PASSWORD@@')
for token in tokens:
    if rendered.count(token) != 1:
        raise SystemExit('Template token count is not one: ' + token)
target.write_text(rendered)
target.chmod(0o600)
PY

echo "Prepared: $OUTPUT_DIR/ranger-install.properties"
echo 'PASS: file contains tokens and no application passwords.'
echo 'Helm will create the Ranger ConfigMap when runtime deployment is enabled.'
if [[ -n "$VALUES_FILE" ]]; then
  python3 - "$OUTPUT_DIR/ranger-install.properties" "$VALUES_FILE" <<'PY'
import sys
from pathlib import Path
import yaml
source, target = map(Path, sys.argv[1:])
values = yaml.safe_load(target.read_text())
values.setdefault('ranger', {})['installPropertiesTemplate'] = source.read_text().replace(
    'https://bdv-policy-manager.dinecodetalkers.com',
    'https://{{ .Values.ingress.rangerHost }}')
class Dumper(yaml.SafeDumper):
    pass
Dumper.add_representer(str, lambda d, v: d.represent_scalar(
    'tag:yaml.org,2002:str', v, style='|' if '\n' in v else None))
target.write_text(yaml.dump(values, Dumper=Dumper, sort_keys=False))
target.chmod(0o600)
print('PASS: tokenized Ranger configuration imported into', target)
PY
fi
