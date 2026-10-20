#!/usr/bin/bash
# Trino 480 internal JWT: HKDF-SHA256(shared secret, info=internal-communication).
# Only loopback is allowed. Never enable shell tracing or print credentials.
set +x
set -Eeuo pipefail
mode="${1:-probe}"
budget="${2:-840}"
port="${BDV_DRAIN_PORT:-8080}"
[[ "$budget" =~ ^[0-9]+$ && "$port" =~ ^[0-9]+$ ]] || exit 2
deadline=$((SECONDS + budget))

b64url() { base64 | tr -d '\n=' | tr '+/' '-_'; }
token() {
  test -n "${BDV_INTERNAL_SHARED_SECRET:-}"
  local key header payload signature expires
  key="$(openssl kdf -binary -keylen 32 -kdfopt digest:SHA256 \
    -kdfopt "key:$BDV_INTERNAL_SHARED_SECRET" \
    -kdfopt info:internal-communication HKDF | od -An -v -tx1 | tr -d ' \n')"
  header="$(printf '%s' '{"alg":"HS256"}' | b64url)"
  expires=$(( $(date +%s) + 60 ))
  payload="$(printf '{"sub":"bdv-worker-drain","exp":%s}' "$expires" | b64url)"
  signature="$(printf '%s.%s' "$header" "$payload" |
    openssl dgst -sha256 -mac HMAC -macopt "hexkey:$key" -binary | b64url)"
  export BDV_DRAIN_TOKEN="$header.$payload.$signature"
}

request() {
  # The token stays in the environment, never in a command argument or output.
  export BDV_DRAIN_PORT="$port" BDV_DRAIN_METHOD="$1" BDV_DRAIN_BODY="$2" BDV_DRAIN_PATH="$3"
  timeout 5s /usr/bin/bash -ec '
    exec 3<>/dev/tcp/127.0.0.1/$BDV_DRAIN_PORT
    printf "%s %s HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n" "$BDV_DRAIN_METHOD" "$BDV_DRAIN_PATH" >&3
    if [[ -n "${BDV_DRAIN_TOKEN:-}" ]]; then
      printf "X-Trino-Internal-Bearer: %s\r\n" "$BDV_DRAIN_TOKEN" >&3
    fi
    if [[ "$BDV_DRAIN_METHOD" == PUT ]]; then
      printf "Content-Type: application/json\r\nContent-Length: %s\r\n" "${#BDV_DRAIN_BODY}" >&3
    fi
    printf "\r\n%s" "$BDV_DRAIN_BODY" >&3
    cat <&3
  '
}

if [[ "$mode" == ready ]]; then
  response="$(request GET '' /v1/info 2>/dev/null)" || exit 1
  [[ "$response" =~ ^HTTP/1\.[01][[:space:]]200[[:space:]] ]] || exit 1
  [[ "$response" =~ \"starting\"[[:space:]]*:[[:space:]]*false ]] || exit 1
  [[ "$response" =~ \"state\"[[:space:]]*:[[:space:]]*\"ACTIVE\" ]] || exit 1
  exit 0
fi

if [[ "$mode" == busy ]]; then
  token
  response="$(request GET '' /v1/task 2>/dev/null)" || exit 1
  [[ "$response" =~ ^HTTP/1\.[01][[:space:]]200[[:space:]] ]] || exit 1
  if [[ "$response" =~ \"state\"[[:space:]]*:[[:space:]]*\"RUNNING\" ]]; then
    echo 'PASS: worker currently has RUNNING task(s)'
    exit 0
  fi
  echo 'No RUNNING task found on this worker' >&2
  exit 1
fi

case "$mode" in
  probe) body='"BDV_INVALID_DRAIN_PROBE"'; expected=400 ;;
  shutdown|shutdown-wait) body='"SHUTTING_DOWN"'; expected=200 ;;
  *) echo 'Usage: worker-drain.sh probe|ready|busy|shutdown|shutdown-wait [wait-seconds]' >&2; exit 2 ;;
esac

if token && response="$(request PUT "$body" /v1/info/state 2>/dev/null)" &&
   [[ "$response" =~ ^HTTP/1\.[01][[:space:]]${expected}[[:space:]] ]]; then
  if [[ "$mode" == probe ]]; then
    [[ "$response" == *JSON_PARSING_ERROR* ]] || exit 1
    echo 'PASS: internal-authentication probe reached Trino JSON validation; no state changed'
    exit 0
  fi
  echo 'PASS: Trino accepted SHUTTING_DOWN'
else
  echo 'FAIL: worker shutdown request was not accepted; credentials withheld' >&2
  if [[ "$mode" == shutdown-wait ]]; then
    echo 'Holding the remaining hook budget for operator intervention' >&2
    while (( SECONDS < deadline )); do sleep 2; done
  fi
  exit 1
fi

[[ "$mode" == shutdown-wait ]] || exit 0
while (( SECONDS < deadline )); do
  if response="$(request GET '' /v1/info/state 2>/dev/null)"; then
    sleep 2
  else
    # Confirm refusal twice. A single request timeout is not proof of shutdown.
    if ! timeout 2s /usr/bin/bash -ec 'exec 3<>/dev/tcp/127.0.0.1/$BDV_DRAIN_PORT' 2>/dev/null; then
      sleep 2
      if ! timeout 2s /usr/bin/bash -ec 'exec 3<>/dev/tcp/127.0.0.1/$BDV_DRAIN_PORT' 2>/dev/null; then
        echo 'PASS: worker HTTP listener closed after shutdown request'
        exit 0
      fi
    fi
    sleep 2
  fi
done
echo 'FAIL: drain wait budget exhausted; Kubernetes termination deadline still applies' >&2
exit 1
