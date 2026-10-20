#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

: "${HIVE_DERIVATIVE_IMAGE:?Set repository and explicit non-latest tag}"
: "${MYSQL_JDBC_JAR:?Set path to the verified Connector/J 9.7.0 JAR}"

[[ "$HIVE_DERIVATIVE_IMAGE" == *:* && "$HIVE_DERIVATIVE_IMAGE" != *:latest ]] || {
  echo 'Explicit non-latest image tag required' >&2
  exit 2
}

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
test -f "$MYSQL_JDBC_JAR"
printf '%s  %s\n' \
  '0353648eaa1c91e0f4020c959abf756bc866ffd583df22ae6b6f6e0cbd43eb44' \
  "$MYSQL_JDBC_JAR" | sha256sum -c -

install -m 0644 "$MYSQL_JDBC_JAR" mysql-connector-j-9.7.0.jar
trap 'rm -f mysql-connector-j-9.7.0.jar' EXIT

docker build --platform linux/amd64 -t "$HIVE_DERIVATIVE_IMAGE" .

docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,nosuid,nodev,size=256m \
  --entrypoint /bin/sh "$HIVE_DERIVATIVE_IMAGE" -ec '
    test "$(id -u)" = 10001
    test "$(id -g)" = 10001
    sha256sum -c <<EOF
0353648eaa1c91e0f4020c959abf756bc866ffd583df22ae6b6f6e0cbd43eb44  /opt/hive/lib/mysql-connector-j-9.7.0.jar
EOF
    test -L /opt/hive/lib/hadoop-aws-3.1.0.jar
    test -L /opt/hive/lib/aws-java-sdk-bundle-1.11.271.jar
    test -x /usr/local/bin/bdv-hive
    command -v timeout >/dev/null
  '

printf 'Built and checked %s. Push separately after review.\n' "$HIVE_DERIVATIVE_IMAGE"
