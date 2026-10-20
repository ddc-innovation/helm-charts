#!/usr/bin/env bash
set -Eeuo pipefail
CHART="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
mode="${1:---plan}"
namespace="${IMAGE_NAMESPACE:-rstevens198238}"
tag="${IMAGE_TAG:-capture-0.1.34}"
[[ "$tag" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]*$ && "$tag" != latest ]] || exit 2
export RANGER_JDBC_IMAGE="$namespace/mdaca-policy-manager:jdbc-9.7.0-$tag"
export RANGER_IMAGE="$namespace/mdaca-policy-manager:runtime-$tag"
export HIVE_DERIVATIVE_IMAGE="$namespace/mdaca-metastore:hive-3.1.3-mysql-9.7.0-$tag"
export MINIO_INIT_IMAGE="$namespace/bdv-minio-init:$tag"
printf 'Build order:\n  %s\n  %s\n  %s\n  %s\n' \
  "$RANGER_JDBC_IMAGE" "$RANGER_IMAGE" "$HIVE_DERIVATIVE_IMAGE" "$MINIO_INIT_IMAGE"
[[ "$mode" != --plan ]] || { echo 'PLAN ONLY: no Docker, registry or cluster changes'; exit 0; }
[[ "$mode" == --build ]] || { echo 'Usage: build-images.sh --plan|--build'; exit 2; }
: "${MYSQL_JDBC_JAR:?Set the path to the inspected Connector/J JAR}"
python3 "$CHART/scripts/verify-jdbc.py" "$MYSQL_JDBC_JAR"
bash "$CHART/images/ranger/build-jdbc.sh"
export RANGER_BASE_IMAGE="$RANGER_JDBC_IMAGE"
bash "$CHART/images/ranger/build.sh"
bash "$CHART/images/hive/build.sh"
bash "$CHART/images/minio-init/build.sh"
echo 'All builds checked. No push or Helm values update was performed.'
