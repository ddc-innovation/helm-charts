#!/usr/bin/env bash
# Run load in another session first. This removes one busy worker Pod, not its data.
set -Eeuo pipefail
umask 077
action="${1:-probe}"
ns="${BDV_NAMESPACE:-bdv}"
release="${BDV_RELEASE:-bdv}"
name="${BDV_WORKER_DEPLOYMENT:-bdv-worker}"
mapfile -t workers < <(kubectl -n "$ns" get pods \
  -l "app.kubernetes.io/instance=$release,app.kubernetes.io/component=worker" -o json |
  python3 -c '
import json,sys
for p in sorted(json.load(sys.stdin)["items"], key=lambda p:p["metadata"]["name"]):
    if not p["metadata"].get("deletionTimestamp") and any(c["type"]=="Ready" and c["status"]=="True" for c in p.get("status",{}).get("conditions",[])):
        print(p["metadata"]["name"])
')
((${#workers[@]})) || { echo 'FAIL: no ready workers'; exit 1; }

if [[ "$action" == probe ]]; then
  for worker in "${workers[@]}"; do
    echo "Probing: $worker"
    kubectl -n "$ns" exec "$worker" -c worker -- \
      /usr/bin/bash /etc/trino/worker-drain.sh probe
  done
  exit 0
fi
[[ "$action" == delete-busy ]] || { echo 'Usage: test-worker-drain.sh probe|delete-busy'; exit 2; }
((${#workers[@]} >= 3)) || { echo 'FAIL: keep at least three ready workers before this test'; exit 1; }
kubectl -n "$ns" get deployment "$name" -o json |
  python3 -c '
import json,sys
p=json.load(sys.stdin)["spec"]["template"]["spec"]
c=next(c for c in p["containers"] if c["name"]=="worker")
assert c.get("lifecycle",{}).get("preStop",{}).get("exec",{}).get("command")==["/usr/bin/bash","/etc/trino/worker-drain.sh","shutdown-wait","840"], "Unexpected drain hook; review before removing a worker"
assert p["terminationGracePeriodSeconds"]>=900, "Insufficient termination budget"
'
policy="$(kubectl -n "$ns" get hpa "$name" -o jsonpath='{.spec.behavior.scaleDown.selectPolicy}')"
[[ "$policy" == Disabled ]] || { echo 'FAIL: disable automatic scale-down during the controlled test'; exit 1; }

for worker in "${workers[@]}"; do
  if kubectl -n "$ns" exec "$worker" -c worker -- \
      /usr/bin/bash /etc/trino/worker-drain.sh busy; then
    log="$(mktemp /root/bdv-worker-drain.XXXXXX.log)"
    kubectl -n "$ns" logs -f "$worker" -c worker --since=30s >"$log" 2>&1 &
    log_pid=$!
    trap 'kill "$log_pid" 2>/dev/null || true' EXIT
    echo "Removing busy worker through its preStop hook: $worker"
    echo "Protected shutdown log: $log"
    kubectl -n "$ns" delete pod "$worker" --wait=false
    kubectl -n "$ns" wait --for=delete "pod/$worker" --timeout=16m
    kubectl -n "$ns" rollout status "deployment/$name" --timeout=5m
    echo 'Worker replacement is ready. Check the load query completed without failure.'
    echo 'Review the shutdown log for graceful completion and termination errors.'
    exit 0
  fi
done
echo 'No busy worker found. Start the authenticated load query, then retry.' >&2
exit 1
