#!/usr/bin/env bash
# Reconstructs the JDBC parent stage that preceded the Ranger runtime derivative.
set -Eeuo pipefail
umask 077
: "${MYSQL_JDBC_JAR:?Set the verified Connector/J 9.7.0 JAR path}"
: "${RANGER_JDBC_IMAGE:?Set an explicit repository and non-latest tag}"
[[ "$RANGER_JDBC_IMAGE" == *:* && "$RANGER_JDBC_IMAGE" != *:latest && "$RANGER_JDBC_IMAGE" != *@* ]] || exit 2
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python3 "$here/../../scripts/verify-jdbc.py" "$MYSQL_JDBC_JAR"
context="$(mktemp -d)"
trap 'rm -rf "$context"' EXIT
install -m 0644 "$MYSQL_JDBC_JAR" "$context/mysql-connector-j-9.7.0.jar"
cp "$here/Dockerfile.jdbc" "$context/Dockerfile"
docker build --platform linux/amd64 -t "$RANGER_JDBC_IMAGE" "$context"
docker run --rm --network none --entrypoint /bin/sh "$RANGER_JDBC_IMAGE" -ec '
  printf "%s  %s\n" \
    0353648eaa1c91e0f4020c959abf756bc866ffd583df22ae6b6f6e0cbd43eb44 \
    /opt/mysql-connector-j-9.7.0.jar | sha256sum -c -
  test -L /opt/bdv-policy-manager/ews/webapp/WEB-INF/lib/mysql-connector-j-9.7.0.jar
'
printf 'Built JDBC parent: %s. No image pushed.\n' "$RANGER_JDBC_IMAGE"
