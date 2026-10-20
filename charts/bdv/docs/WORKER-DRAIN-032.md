> Live acceptance update, October 19, 2026: the user verified busy-worker graceful shutdown with all load queries completing, followed by HPA scale-down from three to two and a successful authenticated warehouse read. The original candidate instructions below are retained as procedure history.

# Worker draining, version 0.1.32

Version 0.1.31 has live acceptance for Keycloak authentication, Ranger policy
reconciliation, warehouse read/write, and HPA scale-up from two to three workers.
Its worker pods have no preStop drain hook. Keep automatic scale-down disabled.

This version adds an opt-in worker-only preStop hook, an authenticated loopback
helper, readiness that rejects a draining worker, and a controlled test. The
hook is new and still needs live acceptance. A local test is not evidence that
a live query survived worker removal.

## Install the candidate while the cluster is idle

The first upgrade cannot protect old pods which do not yet have the hook. Finish
running queries first. This upgrade can restart the coordinator. Preserve the
existing installation and its databases, Secrets and PVCs.

Copy the downloaded archive to `/root/bdv-helm-0.1.32.tar.gz`, then run:

```bash
(
  set -Eeuo pipefail
  umask 077
  install -d -m 0700 /root/bdv-final-032
  tar -xzf /root/bdv-helm-0.1.32.tar.gz -C /root/bdv-final-032
  cd /root/bdv-final-032/bdv-helm/charts/bdv
  test ! -e values-site.yaml
  cp /root/bdv-final-030/bdv-helm/charts/bdv/values-site.yaml values-site.yaml
  chmod 600 values-site.yaml
  export PATH="/opt/helm/4.2.0/bin:$PATH"
  export KUBECONFIG=/root/.kube/config
  python3 scripts/prepare-worker-drain.py values-site.yaml
  bash scripts/test-all.sh
  bash scripts/validate-deployment.sh --runtime --check -f values-site.yaml
  bash scripts/setup.sh runtime values-site.yaml
  bash scripts/test-worker-drain.sh probe
)
```

Expect the probe to pass on every ready worker without changing its state.
If authentication fails, leave scale-down disabled. Do not grant anonymous
system-management access or relax OAuth authentication to make the hook work.

## Controlled busy-worker removal

Use three ready workers and keep HPA scale-down disabled. Run the previously
accepted authenticated CPU load query in one terminal. While it is executing,
run from the chart directory in another terminal:

```bash
export KUBECONFIG=/root/.kube/config
bash scripts/test-worker-drain.sh delete-busy
```

This explicitly deletes one worker Pod through its preStop hook, using normal
Kubernetes termination. It does not delete a Deployment, database, Secret or
PVC. The Deployment creates a replacement. Never add `--force` or
`--grace-period=0`. If no running task is found, the script deletes nothing.

The script saves a protected native worker log under `/root/bdv-worker-drain.*.log`.
Inspect it for the shutdown request and completion, and any termination errors.
Watch worker readiness and Trino's active-worker count during the test. The
selected worker should drain, finish its active tasks, and close its HTTP
listener before Kubernetes' termination deadline. The authenticated load query
must complete successfully. A replacement Pod alone is not a drain-test pass.

## Budgets and security

- The migration script sets Trino `shutdown.grace-period=120s`. Trino waits this
  grace period twice, with active-task completion between those waits.
- The hook waits at most 840 seconds. Kubernetes allows 900 seconds including
  the preStop hook. Long tasks may exceed this budget and still be killed.
  Adjust task/query limits and these budgets together before production use.
- A rejected shutdown request holds the hook budget for operator intervention,
  then fails. Kubernetes can still terminate the container at the deadline.
- The helper makes requests only to `127.0.0.1:8080`, uses the mounted internal
  shared-secret environment variable, and does not print tokens or credentials.
  OpenSSL key material is passed to local OpenSSL processes; processes running
  as the same container user already have access to the shared-secret environment.
- Its JWT key derivation follows Trino 480 HKDF-SHA256 with the
  `internal-communication` context. A different Trino image must be reverified.
  Keep the internal Secret restricted and do not expose worker HTTP ports by
  ingress or NodePort.
- Workers reject readiness while state is not ACTIVE. No hook is added to the
  coordinator. HPA scale-down remains disabled until the live test passes.

After a successful live test, enable `bdv.autoscaling.scaleDownEnabled` in site
values, validate and upgrade with `scripts/setup.sh runtime values-site.yaml`.
Then watch the pool return toward its minimum using the configured 600-second
stabilization window and one-worker-per-120-second scale-down policy. Repeat the
authenticated warehouse read afterward. Do not infer safe scale-down solely
from a successful scale-up test.

Sources:

- https://trino.io/docs/current/admin/graceful-shutdown.html
- https://github.com/trinodb/trino/blob/480/core/trino-main/src/main/java/io/trino/server/InternalAuthenticationManager.java
- https://github.com/trinodb/trino/blob/480/core/trino-main/src/main/java/io/trino/server/ServerInfoResource.java

Local validation exercises real shell JWT signing against independent Python
HKDF/HMAC verification, invalid-secret rejection, draining readiness, and the
shutdown wait. Helm tests check worker-only hook wiring, helper content and
termination budget. Live drain acceptance remains pending.
