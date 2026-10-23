#!/bin/sh
# Runs after the existing initializer. Never replace its default bucket policy.
set -eu
umask 077
fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }
: "${S3_ENDPOINT:?S3_ENDPOINT is required}"
: "${S3_REGION:?S3_REGION is required}"
: "${S3_ADDITIONAL_BUCKETS:?Additional bucket list is required}"
# Validate the entire list before any remote operation or JSON interpolation.
set -f
for bucket in $S3_ADDITIONAL_BUCKETS; do
  case "$bucket" in *[!a-z0-9-]*|-*|*-) fail 'invalid additional bucket name' ;; esac
  test "${#bucket}" -ge 3 && test "${#bucket}" -le 63 || fail 'invalid additional bucket length'
  test "$bucket" != "${S3_BUCKET:-}" || fail 'default bucket cannot be repeated'
done
admin_dir=${MINIO_ADMIN_DIR:-/admin}
app_dir=${MINIO_APP_DIR:-/s3}
for file in "$admin_dir/MINIO_ROOT_USER" "$admin_dir/MINIO_ROOT_PASSWORD" "$app_dir/accessKey" "$app_dir/secretKey"; do
  test -s "$file" || fail 'required Secret file is missing or empty'
done
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM
export MC_CONFIG_DIR="$work/config"
mkdir -p "$MC_CONFIG_DIR"
root_user=$(cat "$admin_dir/MINIO_ROOT_USER")
root_password=$(cat "$admin_dir/MINIO_ROOT_PASSWORD")
app_user=$(cat "$app_dir/accessKey")
app_password=$(cat "$app_dir/secretKey")
printf '%s\n%s\n' "$root_user" "$root_password" |
  mc alias set bdv "$S3_ENDPOINT" --api S3v4 --path on >/dev/null
# Existing initializer already ensures the retained regular workload user exists.
mc admin user info bdv "$app_user" >/dev/null
bucket_resources=''
object_resources=''
separator=''
for bucket in $S3_ADDITIONAL_BUCKETS; do
  mc mb --ignore-existing --region "$S3_REGION" "bdv/$bucket" >/dev/null
  bucket_resources="${bucket_resources}${separator}\"arn:aws:s3:::${bucket}\""
  object_resources="${object_resources}${separator}\"arn:aws:s3:::${bucket}/*\""
  separator=','
done
cat >"$work/policy.json" <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3:GetBucketLocation","s3:ListBucket","s3:ListBucketMultipartUploads"],"Resource":[$bucket_resources]},{"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject","s3:AbortMultipartUpload","s3:ListMultipartUploadParts"],"Resource":[$object_resources]}]}
EOF
policy=bdv-additional-buckets
# This dedicated policy is chart-owned. Updating its list changes this policy only.
mc admin policy create bdv "$policy" "$work/policy.json" >/dev/null
# Attach can return nonzero when already attached. Verify association on readback.
mc admin policy attach bdv "$policy" --user "$app_user" >"$work/attach.log" 2>&1 || :
mc admin user info bdv "$app_user" --json >"$work/user.json"
sed -n 's/.*"policyName"[[:space:]]*:[[:space:]]*"\([^" ]*\)".*/\1/p' "$work/user.json" | tr ',' '\n' | grep -Fx "$policy" >/dev/null || fail 'additional bucket policy association was not verified'
printf '%s\n%s\n' "$app_user" "$app_password" |
  mc alias set workload "$S3_ENDPOINT" --api S3v4 --path on >/dev/null
for bucket in $S3_ADDITIONAL_BUCKETS; do
  mc stat "workload/$bucket" >/dev/null || fail 'workload cannot access an additional bucket'
done
printf 'PASS: additional MinIO buckets and workload-scoped policy verified\n'
