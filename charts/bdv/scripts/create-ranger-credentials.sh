#!/usr/bin/env bash
# Creates the retained Ranger service-password Secret once. Never prints values.
set -Eeuo pipefail
umask 077

KUBECONFIG="${KUBECONFIG:-/root/.kube/config}"
NAMESPACE="${NAMESPACE:-bdv}"
SECRET_NAME="${RANGER_CREDENTIALS_SECRET:-bdv-ranger-credentials}"
SECRET_DIR="${BDV_SECRET_DIR:-/root/bdv-secrets}/ranger-credentials"
KEYS=(rangerAdmin_password rangerTagsync_password rangerUsersync_password keyadmin_password)

if kubectl --kubeconfig "$KUBECONFIG" -n "$NAMESPACE" get secret "$SECRET_NAME" >/dev/null 2>&1; then
  kubectl --kubeconfig "$KUBECONFIG" -n "$NAMESPACE" get secret "$SECRET_NAME" -o json |
    python3 -c 'import json,sys
required = {"rangerAdmin_password", "rangerTagsync_password", "rangerUsersync_password", "keyadmin_password"}
data = json.load(sys.stdin).get("data", {})
missing = sorted(key for key in required if not data.get(key))
if missing:
    raise SystemExit("Existing Ranger credential Secret is missing required keys: " + ", ".join(missing))'
  echo "PASS: existing $SECRET_NAME has all required keys; it was not changed."
  exit 0
fi

mkdir -p "$SECRET_DIR"
chmod 0700 "$SECRET_DIR"
for key in "${KEYS[@]}"; do
  if [[ -e "$SECRET_DIR/$key" ]]; then
    echo "Stopped: $SECRET_DIR/$key already exists while $SECRET_NAME does not." >&2
    echo 'Review the retained files before creating any new credentials.' >&2
    exit 1
  fi
done

SECRET_DIR="$SECRET_DIR" python3 - <<'PY'
import os
import secrets
import string
from pathlib import Path

directory = Path(os.environ['SECRET_DIR'])
keys = ('rangerAdmin_password', 'rangerTagsync_password',
        'rangerUsersync_password', 'keyadmin_password')
alphabet = string.ascii_letters + string.digits + '@%_+=-'
for key in keys:
    # Guarantee each class rather than relying on probability.
    value = [secrets.choice(string.ascii_uppercase),
             secrets.choice(string.ascii_lowercase),
             secrets.choice(string.digits),
             secrets.choice('@%_+=-')]
    value.extend(secrets.choice(alphabet) for _ in range(28))
    secrets.SystemRandom().shuffle(value)
    path = directory / key
    path.write_text(''.join(value))
    path.chmod(0o600)
PY

CREATE_ARGS=()
for key in "${KEYS[@]}"; do
  CREATE_ARGS+=("--from-file=$key=$SECRET_DIR/$key")
done
kubectl --kubeconfig "$KUBECONFIG" -n "$NAMESPACE" create secret generic "$SECRET_NAME" \
  "${CREATE_ARGS[@]}" >/dev/null

# The array substitution above is shell-fragile on older Bash. Verify immediately.
kubectl --kubeconfig "$KUBECONFIG" -n "$NAMESPACE" get secret "$SECRET_NAME" -o json |
  python3 -c 'import json,sys
required = {"rangerAdmin_password", "rangerTagsync_password", "rangerUsersync_password", "keyadmin_password"}
data = json.load(sys.stdin).get("data", {})
missing = sorted(key for key in required if not data.get(key))
if missing:
    raise SystemExit("Created Secret is missing required keys: " + ", ".join(missing))'

echo "Created: $SECRET_NAME"
echo "Protected recovery files: $SECRET_DIR"
echo 'No credentials were printed.'
