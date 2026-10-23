# Additional MinIO buckets, Hive schemas and Ranger grants

Keep the default warehouse bucket, credentials, PVC and endpoint unchanged. Add
these supported fields to your site values (inside `bdv_values` for Ansible):

```yaml
objectStorage:
  additionalBuckets: [pnt-lake, pnt-artifacts]
hive:
  schemas:
    - {name: pnt_lake, bucket: pnt-lake}
    - {name: pnt_artifacts, bucket: pnt-artifacts}
```

The complete site-specific overlay is `examples/pnt-storage.yaml`. It also adds
Ranger grants for Richard and Michael. Those grant schema/table management and
column SELECT in the two declared schemas. They do not grant delegation, Ranger
administration, or new rights to the healthcheck user. Change the named users for
another site. They must already appear in ranger.authorization.users.

`ranger.authorization.additionalGrants` is appended to inherited `grants`, so you
can add policies without replacing the healthcheck list. Both lists are validated
and reconciled additively. Existing catalog-use and query-execute policies remain
necessary. Removing a grant from either list does not revoke it in Ranger.

## Runtime reconciliation

The normal runtime Helm upgrade runs the existing bucket initializer first, then
a chart-mounted supplemental script using the same pinned init image and Secret
mounts. It ensures additional buckets exist without replacing their objects,
creates or updates `bdv-additional-buckets`, attaches it to the existing workload
identity, and verifies workload bucket access. No new image build or credential
rotation is required. The original `bdv-warehouse` policy remains unchanged, so
its strict single-bucket validation still passes on subsequent upgrades. The new
script requires the same mc CLI and shell contract as the existing init image.

The additional policy allows list/read/write/delete/multipart only on declared
additional buckets. Its name is reserved for this chart and its contents are
reconciled on each upgrade with a nonempty additionalBuckets list. Removing one
name from a nonempty list removes that name from this policy; other attached
policies may still grant access. Emptying the list disables this job extension
and does not remove the prior policy. Removing buckets never deletes objects or
buckets. Explicit deny policies still apply. This is the existing regular-user
initializer contract, not a new service-account or OIDC identity flow.

Use your complete current site values with the overlay last:

```bash
helm lint charts/bdv -f /path/to/current-site-values.yaml -f charts/bdv/examples/pnt-storage.yaml
helm template bdv charts/bdv -n bdv -f /path/to/current-site-values.yaml -f charts/bdv/examples/pnt-storage.yaml > /tmp/bdv-storage-review.yaml
```

Review the runtime diff and existing deployment flags, then perform your normal
runtime upgrade using the newly published chart version and those same values.
Do not use bootstrap-only on the existing release. A catalog-only upgrade does
not run bucket or Ranger reconciliation. Check completion of the newest
`bdv-buckets-<revision>` and `bdv-authorization-<revision>` Jobs and their logs.
Schema grants need the plugin's normal 30-second refresh before effective tests.

## Create Hive schemas explicitly

Hive already derives its MinIO endpoint/credentials and S3A configuration from
the existing storage settings and Secrets. A new bucket does not require a new
Hive catalog or a replacement default warehouse. The new script prints SQL only
unless --apply is supplied:

```bash
python3 charts/bdv/scripts/manage-storage.py -f /path/to/current-site-values.yaml -f charts/bdv/examples/pnt-storage.yaml
python3 charts/bdv/scripts/manage-storage.py -f /path/to/current-site-values.yaml -f charts/bdv/examples/pnt-storage.yaml --apply --user richard.stevens@ddc-dine.com
```

Apply follows the existing authenticated Trino CLI flow and prompts for browser
OAuth authentication. It creates schemas and prints their actual locations;
it does not bypass Ranger or expose credentials. You can instead run the printed
SQL in your existing authenticated SQL client. `IF NOT EXISTS` does not change
an existing schema's location. Inspect SHOW CREATE SCHEMA before using it.

The locations use `s3a://` to match the metastore's Hadoop configuration. Trino
retains native `s3.*` catalog properties. If the metastore reports a missing S3A
filesystem, inspect its actual image/configuration rather than changing MinIO
permissions. Table definitions are still required to query existing files.
`pnt-artifacts` may hold non-tabular files; creating a Hive schema does not make
arbitrary artifacts queryable.

Run a purposeful test-table INSERT/SELECT under each permitted identity to verify
Ranger, Hive and worker object access. A successful bucket stat or schema create
alone is not proof of the complete query path. No live cluster test is implied
by the offline checks in this change.
