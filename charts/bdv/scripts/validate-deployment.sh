#!/usr/bin/env bash
# Read-only validation. No software installation, Docker, Helm install, or kubectl apply.
set -Eeuo pipefail
umask 077
CHART_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
MODE=offline
CHECK=false
VALUES="$CHART_DIR/values.yaml"
NAMESPACE=bdv
RELEASE=bdv
while (($#)); do
  case "$1" in
    --offline) MODE=offline; shift ;;
    --bootstrap-only) MODE=bootstrap; shift ;;
    --runtime) MODE=runtime; shift ;;
    --check) CHECK=true; shift ;;
    -f|--values) VALUES="${2:?Values file required}"; shift 2 ;;
    -n|--namespace) NAMESPACE="${2:?Namespace required}"; shift 2 ;;
    -r|--release) RELEASE="${2:?Release required}"; shift 2 ;;
    *) echo 'Usage: validate-deployment.sh [--offline|--bootstrap-only|--runtime] [--check] [-f values.yaml] [-n bdv] [-r bdv]' >&2; exit 2 ;;
  esac
done
for TOOL in python3 helm kubectl kubeconform; do
  command -v "$TOOL" >/dev/null || { echo "Missing required tool: $TOOL. Install separately; validation installs nothing." >&2; exit 2; }
done
python3 -c 'import yaml' || { echo 'Python 3 with PyYAML is required.' >&2; exit 2; }
HELM_VERSION="$(helm version --short)"
[[ "$HELM_VERSION" =~ ^v4\.2\.0([+-]|$) ]] || { echo 'Helm 4.2.0 is required.' >&2; exit 2; }
if $CHECK && [[ "$MODE" == offline ]]; then
  echo '--check needs --bootstrap-only or --runtime.' >&2
  exit 2
fi
python3 "$CHART_DIR/scripts/values_check.py" --defaults "$CHART_DIR/values.yaml" --values "$VALUES" --mode "$MODE"
TARGET="$(python3 "$CHART_DIR/scripts/values_check.py" --defaults "$CHART_DIR/values.yaml" --values "$VALUES" --mode "$MODE" --version-only)"
OVERRIDES=(--set deployment.enabled=false --set bootstrap.only=false)
if [[ "$MODE" == bootstrap ]]; then OVERRIDES=(--set deployment.enabled=false --set bootstrap.only=true); fi
if [[ "$MODE" == runtime ]]; then OVERRIDES=(--set deployment.enabled=true --set bootstrap.only=false); fi
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT
helm lint "$CHART_DIR" -f "$VALUES" "${OVERRIDES[@]}"
helm template "$RELEASE" "$CHART_DIR" -n "$NAMESPACE" -f "$VALUES" \
  --kube-version "$TARGET" "${OVERRIDES[@]}" > "$WORK_DIR/rendered.yaml"
python3 "$CHART_DIR/scripts/render_check.py" "$WORK_DIR/rendered.yaml" "$MODE"
kubeconform -strict -summary -exit-on-error -kubernetes-version "$TARGET" "$WORK_DIR/rendered.yaml"
if $CHECK; then
  python3 "$CHART_DIR/scripts/cluster_check.py" "$CHART_DIR/values.yaml" "$VALUES" "$NAMESPACE" "$MODE"
fi
printf 'PASS: %s rendering/schema validation for Kubernetes %s. No workloads installed.\n' "$MODE" "$TARGET"
if [[ "$MODE" == offline ]]; then echo 'Only the contract ConfigMap was validated; application workloads remain blocked.'; fi
