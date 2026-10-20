# MySQL DBA bootstrap contract

The database-only Helm mode runs one `bdv-bootstrap` Job using the dedicated
`bdv-mysql-admin` Kubernetes Secret (`username`, `password`). The Job mounts
that Secret at `/admin`, sets up a TLS MySQL client, and executes the reviewable
`files/provision.py` plus `files/bootstrap-entrypoint.py`. It requires both Hive
and Ranger entries in `DATABASES_JSON` and runs only when `bootstrap.only=true`.
Runtime deployments do **not** recreate accounts or rotate passwords.

The DBA account needs permission to create databases and restricted accounts,
grant the listed schema privileges, read `information_schema.SCHEMATA`, and
execute `SHOW GRANTS FOR` those accounts. The actual MySQL DBA account's host
scope and privileges remain the DBA team's responsibility. No DBA credentials
are embedded in the chart or printed in Job logs.

| Component | Database and user | Secret | Schema-level privileges |
| --- | --- | --- | --- |
| Hive | From `bootstrap.hive.database` and `.username` (dev: `bdv_hive`) | `bdv-hive-db` | SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES, CREATE TEMPORARY TABLES, LOCK TABLES, EXECUTE |
| Ranger | From `bootstrap.ranger.database` and `.username` (dev: `bdv_ranger`) | `bdv-ranger-db` | Hive list plus CREATE ROUTINE, ALTER ROUTINE, CREATE VIEW, SHOW VIEW |

The accounts use the configured restricted `bootstrap.accountHost` (dev:
`192.168.250.0/255.255.255.0`), not `%`, and grants apply to each matching
database only, without `GRANT OPTION`. Example SQL shape for DBA review (the
Job constructs and runs these statements with the *actual retained Secret*
password; do not run the placeholders literally):

```sql
CREATE DATABASE IF NOT EXISTS `bdv_hive` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'bdv_hive'@'192.168.250.0/255.255.255.0' IDENTIFIED BY '<retained bdv-hive-db password>';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES, CREATE TEMPORARY TABLES, LOCK TABLES, EXECUTE ON `bdv_hive`.* TO 'bdv_hive'@'192.168.250.0/255.255.255.0';

CREATE DATABASE IF NOT EXISTS `bdv_ranger` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'bdv_ranger'@'192.168.250.0/255.255.255.0' IDENTIFIED BY '<retained bdv-ranger-db password>';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES, CREATE TEMPORARY TABLES, LOCK TABLES, EXECUTE ON `bdv_ranger`.* TO 'bdv_ranger'@'192.168.250.0/255.255.255.0';
GRANT CREATE ROUTINE, ALTER ROUTINE, CREATE VIEW, SHOW VIEW ON `bdv_ranger`.* TO 'bdv_ranger'@'192.168.250.0/255.255.255.0';
```

The Job first creates and retains each application Kubernetes Secret, then
creates the matching database and account under a MySQL advisory lock. If the
account exists, `CREATE USER IF NOT EXISTS` does not reset its password: the
script tests login with the retained password *before* granting permissions.
It tests each app's database access after granting, and finally verifies the
server's actual charset/collation and exact expected grants with the DBA
connection. Existing Secrets with a different identity cause a hard stop.
This design permits safe reruns while preserving the two already-initialized
schemas; it is not a migration/repair mechanism for changed credentials.

For a bootstrap-only dry validation, run from the chart directory:

```bash
bash scripts/validate-deployment.sh --bootstrap-only --check -f values-dev.yaml
```

That is read-only and does not test DBA login or execute SQL. A deliberate
bootstrap-only Helm upgrade runs the Job. Examine only status and redacted logs:

```bash
kubectl -n bdv get job bdv-bootstrap -o wide
kubectl -n bdv logs job/bdv-bootstrap --tail=80
```

Do not export or print password-bearing Secret contents or `SHOW GRANTS`
output in shared logs. Hive and Ranger schema initialization are separate
image-owned steps, not part of this DBA account/bootstrap script. Never drop
either already-initialized database as part of a Helm retry.
