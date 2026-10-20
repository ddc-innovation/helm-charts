# Staged development deployment

This chart candidate is not cleared for live installation. The live `*-check`
Deployments proved Ranger, Hive, MinIO and one Trino coordinator, but the
Helm-managed final names and Secret handoff still need validation on the bastion.
Do not set `deployment.evidenceConfirmed=true` merely to bypass the gate.

## Confirmed contract

- Release `bdv` now names Services `bdv-ranger`, `bdv-hive`, `bdv-minio`, and
  `bdv-coordinator` (not `bdv-bdv-*`).
- Development starts with zero workers and no ingress. The confirmed external
  BDV hostname is `bdv.dinecodetalkers.com`, but exposure is deferred until
  Trino authentication and the pfSense/Traefik boundary are verified.
- Trino's coordinator Service publishes not-ready addresses for self-announcement.
  Its startup probe checks the local port. Version 0.1.29 readiness checks the
  tested HTTP `/v1/info` response and `starting:false`, so it does not bypass
  OAuth or depend on a SQL identity. SQL remains a separate functional test.
- The runtime prerequisite container checks mounted files only; it does not
  call the not-yet-running local Trino service. Workers remain scaled to zero.

## Prepare final Trino Secrets on the bastion

Run from the host with `/root/.kube/config` and the protected, already tested
`/root/bdv-trino-config` files. Review the three replacement strings before
running. This step copies files locally and does not print credentials.

```bash
umask 077
install -d -m 0700 /root/bdv-trino-final/coordinator
install -d -m 0700 /root/bdv-trino-final/catalog
install -d -m 0700 /root/bdv-trino-final/plugin
python3 - <<'PY'
from pathlib import Path

source = Path('/root/bdv-trino-config')
target = Path('/root/bdv-trino-final')
replacements = {
    'bdv-trino-coordinator-check': 'bdv-coordinator',
    'bdv-hive-runtime-check': 'bdv-hive',
    'bdv-minio-check': 'bdv-minio',
    'bdv-ranger-runtime-check': 'bdv-ranger',
}
files = {
    'coordinator': ['config.properties', 'node.properties', 'jvm.config', 'log.properties'],
    'catalog': ['hive.properties'],
    'plugin': ['access-control.properties', 'ranger-trino-security.xml',
               'ranger-trino-audit.xml', 'core-site.xml'],
}
prepared = {}
for folder, names in files.items():
    for name in names:
        origin = source / folder / name if folder != 'plugin' else source / name
        destination = target / folder / name
        if destination.exists():
            raise SystemExit(f'Refusing to overwrite {destination}')
        content = origin.read_text()
        for before, after in replacements.items():
            content = content.replace(before, after)
        if any(before in content for before in replacements):
            raise SystemExit(f'Temporary Service reference remains in {origin}')
        prepared[destination] = content
if '-Xms1G' not in prepared[target / 'coordinator/jvm.config']:
    raise SystemExit('Expected tested -Xms1G JVM setting')
if '-Xmx3G' not in prepared[target / 'coordinator/jvm.config']:
    raise SystemExit('Expected tested -Xmx3G JVM setting')
for destination, content in prepared.items():
    destination.write_text(content)
    destination.chmod(0o600)
print('Prepared final Trino configuration; no credentials printed')
PY
```

Before creating anything, ensure `bdv-trino-coordinator`,
`bdv-trino-catalog`, and `bdv-ranger-plugin` are not already used by another
deployment. Then create the Secrets once; `kubectl create` refuses an existing
name rather than overwriting it.

```bash
kubectl -n bdv create secret generic bdv-trino-coordinator \
  --from-file=/root/bdv-trino-final/coordinator
kubectl -n bdv create secret generic bdv-trino-catalog \
  --from-file=/root/bdv-trino-final/catalog
kubectl -n bdv create secret generic bdv-ranger-plugin \
  --from-file=/root/bdv-trino-final/plugin
```

Do not display the catalog Secret or `hive.properties` in logs. Verify key
names only with `kubectl get secret NAME -o go-template=...`. The Hive wrapper
generates its protected XML from the database and warehouse Secrets, so the
optional Hive `/config` overlay is intentionally unset. Generate
`files/ranger-install.properties` using `scripts/prepare-ranger-config.sh`.

## Gates before a Helm runtime upgrade

The 0.1.28 staged runtime has now passed live at revision 7, including the
warehouse table data round-trip. The next opt-in OAuth candidate is documented
in `docs/OAUTH-STAGED.md`; no external login or ingress is yet verified.

Version 0.1.28 makes the database provisioner source visible in the chart and
verifies the actual server collation and exact account grants after the DBA
operations. See `docs/MYSQL-DBA.md` for the DBA review and Secret handoff.
Version 0.1.27 fixed the bootstrap contract: the database provisioner
requires both Hive and Ranger entries together. Bootstrap-only mode now runs
one combined Job and grants Ranger its additional database-scoped vendor
installer privileges. Runtime mode does not rerun this database provisioner;
it relies on the retained, preflight-verified application Secrets. The failed
0.1.26 `bdv-bootstrap-hive` Job may remain for diagnosis and must not be
mistaken for a successful runtime rollout. Do not drop either database, rotate
the retained passwords, or delete the existing `*-check` stack as part of this
retry. Hive and Ranger still own their respective schema initialization.

Run the bundled validator with Helm 4.2.0 and `values-dev.yaml`. An expected
`deployment.evidenceConfirmed` block must remain until final Secret content,
resource rendering, Ranger service URLs, MinIO PVC behavior, image pulls and
the full staged runtime are reviewed. The current local environment cannot
reach the bastion or run Helm; no live Helm upgrade is claimed here.

Keep the `*-check` workloads running while staging. Do not delete a test PVC,
database, Secret, or policy as part of the naming change. Enable workers and
ingress only as separate, verified follow-up steps.
