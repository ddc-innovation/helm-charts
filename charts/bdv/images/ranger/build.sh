#!/usr/bin/env bash
set -Eeuo pipefail

: "${RANGER_IMAGE:?Set an explicit repository and non-latest tag}"
[[ "$RANGER_IMAGE" == *:* && "$RANGER_IMAGE" != *:latest ]] || {
  echo 'RANGER_IMAGE must include an explicit non-latest tag' >&2
  exit 2
}

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

build_args=()
if [[ -n "${RANGER_BASE_IMAGE:-}" ]]; then
  build_args+=(--build-arg "RANGER_BASE_IMAGE=$RANGER_BASE_IMAGE")
fi
docker build --platform linux/amd64 "${build_args[@]}" -t "$RANGER_IMAGE" .

docker run --rm --network none "$RANGER_IMAGE" -version

docker run --rm --network none --entrypoint /bin/sh "$RANGER_IMAGE" -ec '
test -f /opt/mysql-connector-j-9.7.0.jar
test ! -e /opt/bdv-policy-manager/ews/webapp/WEB-INF/lib/mysql-connector-j-9.7.0.jar
test ! -L /opt/bdv-policy-manager/ews/webapp/WEB-INF/lib/mysql-connector-j-9.7.0.jar
echo "PASS: vendor setup owns the Connector/J copy into WEB-INF/lib"
'

docker run --rm --network none --entrypoint python3 "$RANGER_IMAGE" -c '
from pathlib import Path
text = Path("/opt/bdv-ranger/bin/ranger-init").read_text()
required = (
    "RANGER_CREDENTIALS_SECRET_DIR",
    "@@RANGER_ADMIN_PASSWORD@@",
    "@@RANGER_TAGSYNC_PASSWORD@@",
    "@@RANGER_USERSYNC_PASSWORD@@",
    "@@RANGER_KEYADMIN_PASSWORD@@",
)
missing = [value for value in required if value not in text]
if missing:
    raise SystemExit("missing service-password contract: " + ", ".join(missing))
print("PASS: separate Ranger service-password Secret contract")
'

set +e
docker run --rm --network none \
  --entrypoint /opt/bdv-ranger/bin/ranger-init \
  "$RANGER_IMAGE" >/tmp/bdv-ranger-guard-negative.out 2>&1
status=$?
set -e
test "$status" -ne 0
grep -F 'Ranger image or configuration template is missing' \
  /tmp/bdv-ranger-guard-negative.out >/dev/null

printf 'Built and checked %s. Push separately after review.\n' "$RANGER_IMAGE"
