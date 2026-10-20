#!/bin/sh
set -eu
umask 077

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }
need_file() { test -s "$1" || fail "required Secret file is missing or empty"; }

: "${S3_ENDPOINT:?S3_ENDPOINT is required}"
: "${S3_BUCKET:?S3_BUCKET is required}"
: "${S3_REGION:?S3_REGION is required}"

case "$S3_BUCKET" in
  *[!a-z0-9.-]*|.*|*.) fail "invalid bucket name" ;;
esac
test "${#S3_BUCKET}" -ge 3 && test "${#S3_BUCKET}" -le 63 || fail "invalid bucket length"
case "$S3_BUCKET" in *..*) fail "invalid bucket name" ;; esac

need_file /admin/MINIO_ROOT_USER
need_file /admin/MINIO_ROOT_PASSWORD
need_file /s3/accessKey
need_file /s3/secretKey

root_user=$(cat /admin/MINIO_ROOT_USER)
root_password=$(cat /admin/MINIO_ROOT_PASSWORD)
warehouse_user=$(cat /s3/accessKey)
warehouse_password=$(cat /s3/secretKey)
test -n "$root_user" && test -n "$root_password" || fail "empty administrator credential"
test -n "$warehouse_user" && test -n "$warehouse_password" || fail "empty warehouse credential"

config_dir=$(mktemp -d)
work_dir=$(mktemp -d)
trap 'rm -rf "$config_dir" "$work_dir"' EXIT HUP INT TERM
export MC_CONFIG_DIR="$config_dir"

attempt=0
until curl --fail --silent --show-error --max-time 3 "$S3_ENDPOINT/minio/health/ready" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  test "$attempt" -lt 60 || fail "MinIO did not become ready"
  sleep 2
done

printf '%s\n%s\n' "$root_user" "$root_password" |
  mc alias set bdv "$S3_ENDPOINT" --api S3v4 --path on >/dev/null

mc mb --ignore-existing --region "$S3_REGION" "bdv/$S3_BUCKET" >/dev/null

policy_name=bdv-warehouse
policy_file="$work_dir/policy.json"
cat >"$policy_file" <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3:GetBucketLocation","s3:ListBucket","s3:ListBucketMultipartUploads"],"Resource":["arn:aws:s3:::$S3_BUCKET"]},{"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject","s3:AbortMultipartUpload","s3:ListMultipartUploadParts"],"Resource":["arn:aws:s3:::$S3_BUCKET/*"]}]}
EOF

if mc admin policy info bdv "$policy_name" >"$work_dir/current-policy" 2>/dev/null; then
  current_compact="$work_dir/current-policy.compact"
  tr -d '[:space:]' <"$work_dir/current-policy" >"$current_compact"

  actual_actions=$(grep -o '"s3:[A-Za-z]*"' "$current_compact" | tr -d '"' | sort -u)
  expected_actions=$(printf '%s\n' \
    s3:AbortMultipartUpload \
    s3:DeleteObject \
    s3:GetBucketLocation \
    s3:GetObject \
    s3:ListBucket \
    s3:ListBucketMultipartUploads \
    s3:ListMultipartUploadParts \
    s3:PutObject | sort -u)

  actual_resources=$(grep -o '"arn:aws:s3:::[^"]*"' "$current_compact" | tr -d '"' | sort -u)
  expected_resources=$(printf '%s\n' \
    "arn:aws:s3:::$S3_BUCKET" \
    "arn:aws:s3:::$S3_BUCKET/*" | sort -u)

  allow_count=$(grep -o '"Effect":"Allow"' "$current_compact" | wc -l | tr -d ' ')
  test "$actual_actions" = "$expected_actions" \
    && test "$actual_resources" = "$expected_resources" \
    && test "$allow_count" = 2 \
    && grep -q '"Version":"2012-10-17"' "$current_compact" \
    && ! grep -q '"Effect":"Deny"' "$current_compact" \
    && ! grep -q '"Condition":' "$current_compact" \
    || fail "existing policy does not match the configured bucket"
else
  mc admin policy create bdv "$policy_name" "$policy_file" >/dev/null
fi

user_exists=false
while IFS= read -r line; do
  case "$line" in
    *"\"accessKey\":\"$warehouse_user\""*) user_exists=true ;;
  esac
done <<EOF
$(mc admin user ls bdv --json)
EOF

if test "$user_exists" = false; then
  printf '%s\n%s\n' "$warehouse_user" "$warehouse_password" |
    mc admin user add bdv >/dev/null
fi

# MinIO requires the access-key identifier for policy association. The secret key
# is never placed in argv. A final authenticated bucket check detects mismatches.
mc admin policy attach bdv "$policy_name" --user "$warehouse_user" \
  >"$work_dir/attach.out" 2>"$work_dir/attach.err" || true

entities=$(mc admin policy entities bdv --json) || fail "cannot verify policy association"
case "$entities" in
  *"$policy_name"*"$warehouse_user"*|*"$warehouse_user"*"$policy_name"*) ;;
  *) fail "warehouse identity is not associated with the scoped policy" ;;
esac

printf '%s\n%s\n' "$warehouse_user" "$warehouse_password" |
  mc alias set warehouse "$S3_ENDPOINT" --api S3v4 --path on >/dev/null ||
  fail "existing warehouse identity does not match the retained Secret"

mc stat "warehouse/$S3_BUCKET" >/dev/null ||
  fail "warehouse identity cannot access the configured bucket"

printf 'PASS: MinIO bucket, warehouse identity and scoped policy verified\n'
