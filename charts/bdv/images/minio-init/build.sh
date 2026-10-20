#!/usr/bin/env bash
set -Eeuo pipefail
: "${MINIO_INIT_IMAGE:?Set repository and explicit non-latest tag}"
[[ "$MINIO_INIT_IMAGE" == *:* && "$MINIO_INIT_IMAGE" != *:latest ]] || { echo 'Explicit non-latest image tag required' >&2; exit 2; }
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
docker build --platform linux/amd64 -t "$MINIO_INIT_IMAGE" .
docker run --rm --network none --entrypoint /bin/sh "$MINIO_INIT_IMAGE" -ec \
  'minio --version; mc --version; test -x /usr/local/bin/bdv-minio-init'
printf 'Built and checked %s. Push separately after review.\n' "$MINIO_INIT_IMAGE"
