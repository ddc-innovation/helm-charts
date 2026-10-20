# Fresh installation and six-worker acceptance in bdv-test

The existing bdv namespace remains deployed. This workflow uses namespace
bdv-test with release name bdv. Keeping the same release name is intentional:
all short Service names resolve inside bdv-test. The helper fixes the namespace
and checks database/user, bucket, Keycloak client and Ranger service identities
before each action. Do not run the generic setup.sh without NAMESPACE=bdv-test
for this test; use scripts/fresh-test.sh.

## Isolation and prerequisites

| Item | Fresh-test identity |
| --- | --- |
| Kubernetes namespace | bdv-test |
| Helm release | bdv within bdv-test |
| Hive database and MySQL user | bdv_test_hive |
| Ranger database and MySQL user | bdv_test_ranger |
| Ranger service | bdv-test-trino |
| MinIO bucket | bdv-test-warehouse |
| MinIO PVC | new bdv-minio in bdv-test, 20Gi Longhorn |
| Application URL | https://bdv-test.dinecodetalkers.com |
| Ranger URL, local admin login | https://bdv-test-policy-manager.dinecodetalkers.com |
| Keycloak realm/client | existing innonet realm, new bdv-test client |
| HPA capacity acceptance | min 6, max 6; automatic scale-down disabled |

Application passwords, MinIO identities, Ranger credentials and the internal
shared secret are newly generated in bdv-test. No Secret is copied from bdv.
Use the existing MySQL DBA credential when prompted, as an external prerequisite.
The read-only database preflight requires visibility of mysql.user and refuses
existing test schemas or users; it never deletes them. If bdv-test already exists,
namespace creation stops. Do not delete namespaces or databases to bypass a stop.

Six test workers request a total 3 CPU and 12Gi memory and have aggregate limits
of 12 CPU and 24Gi memory, in addition to the test coordinator, Hive, Ranger and
MinIO. They share the physical Kubernetes nodes with the live deployment.
Check kubectl top nodes and node allocated resources before starting runtime.

## 1. Extract and prepare

Download bdv-helm-0.1.34.tar.gz to /root, then:

```bash
(
  set -Eeuo pipefail
  umask 077
  test ! -e /root/bdv-test-install/bdv-helm
  install -d -m 0700 /root/bdv-test-install
  tar -xzf /root/bdv-helm-0.1.34.tar.gz -C /root/bdv-test-install
  cd /root/bdv-test-install/bdv-helm/charts/bdv
  export PATH="/opt/helm/4.2.0/bin:$PATH"
  export KUBECONFIG=/root/.kube/config
  bash scripts/fresh-test.sh prepare
  bash scripts/test-all.sh
  bash scripts/validate-deployment.sh --offline -n bdv-test -f values-test.yaml
  bash scripts/fresh-test.sh namespace
)
```

## 2. Register the separate Keycloak client with existing Ansible

The generated file contains only the bdv-test client entry and no client secret.
Extra vars select that entry for this invocation, leaving the existing client
inventory file unchanged. It uses the existing realm and administrator setup.

```bash
cd /root/digin-automation
/opt/ansible-venv/bin/ansible-playbook \
  -i ansible/applications/keycloak/inventory.yml \
  ansible/applications/keycloak/playbooks/realm-management.yml \
  --tags client \
  --extra-vars @/root/bdv-test-install/bdv-helm/charts/bdv/examples/keycloak-bdv-test-client.yml
```

Obtain the generated bdv-test client secret using your protected Keycloak admin
workflow. Do not paste it into chat or commands. Enter it at the hidden prompt
in the next step. The client has an exact callback
https://bdv-test.dinecodetalkers.com/oauth2/callback and audience bdv-test.
This file remains the repeatable test-client definition; later merge the entry
into your managed project if the environment is retained.

## 3. Create fresh credentials, check empty databases, bootstrap

```bash
(
  set -Eeuo pipefail
  cd /root/bdv-test-install/bdv-helm/charts/bdv
  export PATH="/opt/helm/4.2.0/bin:$PATH"
  export KUBECONFIG=/root/.kube/config
  bash scripts/fresh-test.sh secrets
  bash scripts/fresh-test.sh prepare-ranger
  bash scripts/fresh-test.sh preflight
  bash scripts/validate-deployment.sh --bootstrap-only --check \
    -n bdv-test -r bdv -f values-test.yaml
  bash scripts/fresh-test.sh bootstrap
)
```

Credentials are created once and retained. The bootstrap creates the new schemas,
users and grants. Expected logs name bdv_test_hive and bdv_test_ranger; stop if
any bootstrap output names a live database. Freshness is checked before creation;
a bootstrap rerun is idempotent and is not another fresh-install test.

## 4. DNS, pfSense proxy and runtime

Create internal DNS overrides for both test hostnames pointing to 192.168.250.1.
Ensure HAProxy routes their Host headers to the existing worker HTTP backend,
uses the wildcard certificate, and supplies the same sanitized forwarded HTTPS
headers as the tested live application. Verify both hostnames resolve privately
from the bastion and cluster clients. Do not reuse the live ingress hostnames.

Before ingress exists, verify the route without bypassing TLS verification:

```bash
for host in bdv-test.dinecodetalkers.com bdv-test-policy-manager.dinecodetalkers.com; do
  getent ahostsv4 "$host"
  curl --silent --show-error --connect-timeout 5 --max-time 15 \
    --resolve "$host:443:192.168.250.1" --output /dev/null \
    --write-out 'HTTP %{http_code}; TLS verification %{ssl_verify_result}\n' "https://$host/"
done
```

An HTTP 404 before ingress can confirm the route reaches an HTTP responder; it
is not an authentication or application readiness test. Confirm the correct
backend and proxy-header boundary using your existing pfSense configuration.
After those prerequisites and database bootstrap pass, explicitly enable the
reviewed runtime profile:

```bash
(
  set -Eeuo pipefail
  cd /root/bdv-test-install/bdv-helm/charts/bdv
  export PATH="/opt/helm/4.2.0/bin:$PATH"
  export KUBECONFIG=/root/.kube/config
  python3 - <<'PY'
from datetime import datetime
from pathlib import Path
import shutil,yaml
p=Path('values-test.yaml')
shutil.copy2(p,p.with_name(p.name+'.bak-'+datetime.now().strftime('%Y%m%d-%H%M%S')))
v=yaml.safe_load(p.read_text())
v['deployment'].update(enabled=True,evidenceConfirmed=True,domainsConfirmed=True,proxyBoundaryConfirmed=True)
v['ingress']['enabled']=True
p.write_text(yaml.safe_dump(v,sort_keys=False));p.chmod(0o600)
PY
  kubectl top nodes
  bash scripts/fresh-test.sh runtime
  python3 scripts/test-managed.py -n bdv-test -r bdv -f values-test.yaml
  BDV_NAMESPACE=bdv-test bash scripts/test-worker-drain.sh probe
)
```

The authorization Job configures the new Ranger database, identities, service
definition and policies. Hive initializes the new schema. MinIO initializes the
new bucket and identities. No manual SQL table creation should be required.
If any Job fails, inspect its logs in bdv-test before retrying. Do not copy live
schemas or policies into the test to make fresh installation pass.

## 5. Prove six workers joined and test fresh storage

```bash
kubectl -n bdv-test get deployment bdv-coordinator bdv-worker
kubectl -n bdv-test get hpa bdv-worker
kubectl -n bdv-test get pvc

kubectl -n bdv-test exec -it deployment/bdv-coordinator -c coordinator -- \
  /usr/bin/trino --server https://bdv-test.dinecodetalkers.com \
    --external-authentication --external-authentication-redirect-handler=PRINT \
    --user richard.stevens@ddc-dine.com --no-progress --output-format TSV_HEADER \
    --execute 'SELECT node_id, coordinator, state FROM system.runtime.nodes'
```

Authenticate using the printed link. Expect seven active nodes: six workers and
one coordinator. The fresh-test profile grants only the corresponding node
metadata read permissions, not system-management writes. Six ready Pods alone
are not proof that six Trino workers joined. If Ranger is still polling a new
policy, inspect the authorization Job and policy-refresh logs before retrying.

Create the single allowed smoke table and write one new row:

```bash
cd /root/bdv-test-install/bdv-helm/charts/bdv
python3 scripts/test-managed.py -n bdv-test -r bdv -f values-test.yaml \
  --user richard.stevens@ddc-dine.com --sql create-smoke
python3 scripts/test-managed.py -n bdv-test -r bdv -f values-test.yaml \
  --user richard.stevens@ddc-dine.com --sql write
python3 scripts/test-managed.py -n bdv-test -r bdv -f values-test.yaml \
  --user richard.stevens@ddc-dine.com --sql read
```

For the earlier CPU load query with WHERE probe_id=1, first insert a seed row
with probe_id=1 into this new table using authenticated SQL. The write helper
uses a unique timestamp ID and does not create that seed. Do not point the load
query at the live namespace. Repeat the bounded load test against the test URL,
inspect successful execution and resource metrics with all six workers present.
Membership plus a successful query does not prove all six processed tasks of
one query; use task/query statistics if that is an acceptance requirement.

## 6. Return to the normal 2-to-6 HPA range

The six-worker bring-up forces HPA min=max=6. It tests capacity and registration,
not CPU-triggered scale-up from two all the way to six. Once six-worker query
and fresh storage tests pass, back up values-test.yaml, set minReplicas=2,
maxReplicas=6 and scaleDownEnabled=true, and run fresh-test.sh runtime again.
Observe successful scale-down events and draining until two ready workers
remain. Then repeat the authenticated read. The stabilization window and drain
hook can take several minutes. Do not force-delete a draining Pod.

Check both namespaces afterward. The live bdv release should remain at its
existing revision and have no new live workloads or credential changes from
this workflow. A test namespace does not isolate shared-node CPU or memory.
No namespace, PVC, database, user or Keycloak client cleanup is automated here.
