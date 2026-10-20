# Fresh installation and CPU scaling acceptance, October 19, 2026

## Scope and provenance

Operator-reported live results from the isolated `bdv-test` namespace, release
`bdv`, chart 0.1.34 on the existing RKE2 cluster. These are the observations
provided in the working session, not independently collected cluster telemetry.
The packaging environment did not access the cluster. The existing `bdv`
namespace was not a target of the fresh-test or load commands.

## Results

| Check | Observed result |
|---|---|
| Empty database preflight | Test schemas/users absent before bootstrap |
| Remote MySQL bootstrap | New bdv_test_hive and bdv_test_ranger schemas, accounts, generated passwords and restricted grants verified |
| New application state | Separate Ranger/Hive databases, MinIO storage and Keycloak test client |
| Initial capacity | Six ready workers; authenticated runtime.nodes returned one coordinator plus six active workers |
| Warehouse SQL | CREATE and INSERT passed; authenticated read returned the inserted timestamp row |
| Idle capacity reduction | Six workers returned to two; terminating workers subsequently disappeared |
| CPU scale-up | Earlier tiny-table query drove 2 to 3 to 4; partitioned workload subsequently reached six, with HPA readings 326%/70% and 93%/70% |
| UI observation | Six active workers displayed after the partitioned load |
| Final scale-down | Two Running workers, both 1/1 ready, zero restarts; no terminating workers in final output |
| Runtime acceptance | All component rollouts, latest authorization, active HPA metrics and unauthenticated local SQL rejection passed |
| Post-scale read | Existing smoke-table row returned through browser-authenticated Trino CLI |

The six-worker Kubernetes readiness snapshot and final HPA event listing were
requested but were not supplied in the session. Do not invent intermediate
4-to-5-to-6 event timestamps. The HPA snapshots and UI establish that the maximum
was reached under load; retain event telemetry on future runs with the evidence
action before Kubernetes event expiration.

## Workload and lessons

The original one-row smoke query concentrated CPU on one or two workers. At
four workers HPA averages stayed around 50 to 60 percent, below the 70 percent
target. This was insufficient evidence for a six-worker CPU test.

A dedicated `hive.default.bdv_cpu_load_20261019` Parquet table was created with
`probe_id BIGINT`, `note VARCHAR`, and final partition column `batch_id INTEGER`.
One million synthetic rows were distributed among 32 partitions, each with
31,000 or 32,000 rows. Richard alone received table-specific create/insert/show/
drop and column-specific select permissions through values-driven reconciliation.
The original smoke-table rows were not changed by this setup.

`repeat(string, 4)` produced an array in this Trino build and failed INSERT type
validation before writing. The corrected seed uses `concat` and inserted exactly
one million rows. SHOW CREATE on the smoke table was denied; that permission was
not broadened because the known table schema made inspection unnecessary.

The accepted load scans the partitioned table and computes SHA-256 using each
row's note and an UNNEST sequence of 100 values. Two concurrent authenticated CLI
sessions each submit up to 20 queries, bounded by a 900-second client timeout.
A timed-out client is not proof of immediate server cancellation: inspect the
Trino UI for outstanding queries before declaring the load stopped.

HPA configuration remained min=2, max=6, CPU target=70% of requests. Scale-up is
limited to one Pod per 60 seconds. Scale-down uses a 600-second recommendation
window and at most one Pod per 120 seconds, with worker graceful draining.
Observed worker shutdown in an earlier busy-worker test waited 120 seconds for
discovery, completed active tasks, then waited 120 seconds for task observation.
These waits overlap across removed workers and are not a ten-minute per-worker
sleep. Timing is workload- and recommendation-dependent.

## Reproduce

From `/root/mdaca-deploy/bdv-helm/charts/bdv` with the protected test values:

```bash
python3 scripts/test-cpu-scale.py configure
bash scripts/fresh-test.sh runtime
# Seed only on a new table. Skip when the one-million-row table already exists.
python3 scripts/test-cpu-scale.py seed
# Run load concurrently in two terminals, authenticating each in a browser.
python3 scripts/test-cpu-scale.py load
```

Before load, record two ready workers with no terminating Pods. Observe HPA and
per-worker CPU during load. Capture CPU-driven rescale events and six ready
workers before allowing the load to finish. Wait for two ready workers and all
terminating workers to disappear, then run:

```bash
python3 scripts/test-managed.py -n bdv-test -r bdv -f values-test.yaml
python3 scripts/test-managed.py -n bdv-test -r bdv -f values-test.yaml \
  --user richard.stevens@ddc-dine.com --sql read
python3 scripts/test-cpu-scale.py evidence --output /root/bdv-test-evidence-$(date +%Y%m%d-%H%M%S)
```

The evidence action does not export Secret contents, raw Pod specs, Helm values,
or authentication links. Its directory stays outside the repository. Review
its operational metadata before sharing. Test values and local backups remain
private and must not be added to Git.

## Undeploy after recording results

```bash
bash scripts/undeploy-test.sh --plan
bash scripts/undeploy-test.sh --execute
```

This removes only Helm release `bdv` in `bdv-test`, allows graceful termination,
and retains Helm history. Namespace, credential Secrets, the kept MinIO PVC,
remote MySQL databases/accounts, Keycloak client and external DNS/proxy settings
remain for recovery. Retained Jobs and preflight objects can remain too. The
uninstall does not mean every namespace object is deleted or that a second fresh
install will be possible without a separately reviewed data cleanup. Public test
routes stop serving applications when their Ingress resources are removed.
No force deletion, SQL DROP, PVC deletion, namespace deletion, or live namespace
modification is part of this workflow.

The acceptance result concerns this installation and workload. It does not prove
HA, node-loss recovery, unlimited query drain time, all-user authorization,
maximum production throughput or compliance certification.
