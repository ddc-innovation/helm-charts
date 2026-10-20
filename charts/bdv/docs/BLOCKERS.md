# Outstanding runtime requirements

Current checkpoint: the 0.1.28 internal dev runtime passed at Helm revision 7,
including narrow Ranger enforcement and a Hive/MinIO data write/read. The
sections below include historical failures and are not authorization to drop
or reset databases. OAuth and ingress remain unverified. See
`docs/OAUTH-STAGED.md` for the opt-in 0.1.29 candidate and its remaining gates.

## 0.1.26 bootstrap hook failure and 0.1.28 correction

The first runtime upgrade from database-only revision 3 failed before creating
runtime workloads. Its single-component `bdv-bootstrap-hive` pre-upgrade Job
passed Kubernetes API access but the pinned provisioner rejected the input:
`Exactly Hive and Ranger database configurations required`. The release was
observed at failed revision 5. Version 0.1.27 limits database bootstrap hooks
to bootstrap-only mode and passes both database configurations in one Job in
that mode. Version 0.1.28 bundles its reviewed source and verifies the DBA
contract after provisioning. Runtime upgrades use the existing retained MySQL Secrets instead.
No schema or database reset is part of this correction.

Defaults intentionally keep `deployment.enabled=false` and `bootstrap.only=false`.
Database-only mode is separately available after read-only validation.

## Established in the session

- Kubernetes server: 1.36.4+rke2r1; schema target: 1.36.4.
- Percona server: 9.7.1-1 at 192.168.250.15:3306.
- Bootstrap 0.1.1 connected from Kubernetes using TLS and the existing admin Secret.
- Database-only Helm revision 3 completed: both retained application Secrets were
  reused/provisioned and both application database logins were verified.
- `bdv_ranger` was granted database-scoped `CREATE ROUTINE` and `ALTER ROUTINE`;
  SHOW GRANTS confirmed the expected DDL/DML/routine set without GRANT OPTION.
- MySQL observed source 192.168.250.18 and matched the subnet account
  `root@192.168.250.0/255.255.255.0`. This verifies that observed worker path,
  not every possible network path.
- Bootstrap image digest:
  `sha256:a0285ef33f12a2888adccfeb0a611329d977798b6cee6a1bf068e1ceedd11975`.
- Ranger driver-only candidate digest:
  `sha256:de06f2862f99267fb8512a26521878fc2e7dc56ae6f76d8233976a746efbcc76`.
- Ranger foreground candidate digest:
  `sha256:777c25b7736c26d62dc322b88c85f2453067bc3fd5e4b0acb9514db8222920ad`.
- Ranger vendor-init 0.2.1 digest and passed image contract:
  `sha256:77f5bba2b674a44df45e31fb0b9907e0d3d2982b79a4c88a0799132e0bd1bf56`.
- Ranger vendor-init 0.2.2 with separate service-password Secret consumption passed
  its image contract and was pushed at:
  `sha256:69a3e2657017b624dde9321761b39ca339f6922198eb314feef965973d796b36`.
- The first 0.2.2 live init stopped before schema SQL because Connector/J already
  occupied the vendor copy destination. Version 0.2.3 source removes only that
  destination link and adds an image-contract assertion. Version 0.2.3 passed and
  was pushed at:
  `sha256:1112b6976a88393d863754d37b69258789cd34a608fdc1719fd4fdbfeec5024e`.
- BDV digest:
  `sha256:5e2bfadf0ae1e159f2105c84fc97ff124a7ad9de1571ac22e070377e0a18e5cc`.

## Ranger

The vendor version file is `3.0.0-SNAPSHOT`. Driver candidate includes Connector/J
9.7.0, but a JDBC connection, schema compatibility with Percona 9.7.1, and a
running Ranger HTTP listener have not been tested. Exposed port 6080 is metadata,
not a runtime test. The Maven JAR signature matched Oracle Maven Release Key 3,
but GPG reported that key expired; retain the verification limitation in release
evidence. JAR SHA256:
`0353648eaa1c91e0f4020c959abf756bc866ffd583df22ae6b6f6e0cbd43eb44`.

The image's install.properties defaults to PostgreSQL. A complete verified input
configuration must select MYSQL, point SQL_CONNECTOR_JAR to the added MySQL JAR,
use db_ssl_enabled=true, db_ssl_required=true, and
db_ssl_verifyServerCertificate=false for REQUIRED-equivalent behavior.
These legacy JDBC properties still need a driver-level test.

Use setup_mode=SeparateDBA to skip vendor account creation: the inspected DBA
script creates wildcard-host accounts and grants WITH GRANT OPTION, contradicting
the intended application account restrictions. SeparateDBA does NOT disable SQL
or Java schema patches in setup.sh.

Ranger owns its schema lifecycle. The chart does not ship or execute a replacement
schema loader. The supplied optimized schema was reviewed only to establish the
permissions needed by the image's `setup.sh`. The live core-schema attempt further
established that the shipped MySQL schema needs `CREATE VIEW`. Review found no
trigger, function or event creation. The restricted Ranger account therefore needs
`CREATE ROUTINE`, `ALTER ROUTINE`, `CREATE VIEW`, and `SHOW VIEW` on `bdv_ranger`;
the bootstrap wrapper performs this grant on future runs without changing its
password or host identity.

The failed core import left a partial foreign-key graph. A second vendor setup run
failed with MySQL error 3730 while dropping `x_role`, and setup.sh instructed the
operator to drop the database. Before further validation, recreate only the new
`bdv_ranger` database with its existing character set and collation. Preserve the
MySQL account and both retained Ranger Secrets. This cleanup is valid only while
the database contains no application data. The clean vendor init subsequently
completed and recorded 108 active schema-history rows. Repeat initialization
against that completed schema also passed, confirming upgrade-time database
idempotency. Foreground startup then passed without a restart and the local root
endpoint returned HTTP 302 to the login page. The bounded HTTP readiness behavior
is now confirmed. Authenticated metrics and plugin-definition endpoints returned
HTTP 200, and guarded registration of `mdaca-ranger-plugin` succeeded and was
verified. The chart now carries the same name-checking registration logic in a
short-lived post-install/post-upgrade Job. Its upgrade-time skip path still needs
one live chart or equivalent repeat execution.

The vendor-init adapter copies the immutable image installation to a shared
EmptyDir, renders protected install.properties from the Helm-created
`bdv-ranger-config` ConfigMap, `bdv-ranger-db`, and `bdv-ranger-credentials`, runs
the image's own setup.sh, and exposes that prepared home to the main container only
after successful init. Administrator credentials are not mounted in the Ranger
pod, and SeparateDBA prevents the vendor DBA script.

Required work before setting ranger.schemaCommand:

1. Repeat guarded registration and confirm the existing definition is skipped.

The image, ConfigMap path, init argv and foreground startup argv are pinned.
The bounded readiness command remains unresolved. Do not mount the whole Ranger
installation directory from a ConfigMap: doing so hides image libraries, scripts
and SQL assets.

## Hive

The source metastore image is pinned at
`sha256:fa0af47ce86878d907906e397441cb587b49cce4bfdfd73f9995ca44dfd469e9`.
Inspection confirmed Hive 3.1.3, Java 8, MySQL schema scripts, and Hadoop 3.1.0
S3 libraries. It contains no MySQL JDBC driver and needs writable configuration
and temporary directories. The derivative adds the previously verified
Connector/J 9.7.0 JAR and a Secret-file wrapper and is pinned at
`sha256:41b722eb180b952537cc0fb3d40eb5db7a52b7b57a4af86d2e3a430ed849eeb5`.
Its schema init and runtime commands are wired into the chart with UID/GID 10001,
a read-only root filesystem, and bounded writable work and temporary volumes.
Live tests passed schema initialization, repeat validation, metastore startup,
the local port 9083 probe, and MinIO warehouse access. The missing-MinIO test
exposed an unbounded Hadoop S3 command. The pinned 0.1.1 derivative wraps that
operation in an explicit timeout and prints a positive local readiness result.
The repeat rollout used that exact digest for both schema init and runtime. The
pod became Ready with zero restarts and `/usr/local/bin/bdv-hive check` reported
that the metastore accepted connections. Hive image, schema, MinIO, and readiness
evidence is complete. Full deployment evidence remains blocked by the unfinished
BDV/Trino runtime configuration and validation.

## MinIO

MinIO is limited to the dev/demo/prototype profile and uses the `longhorn`
StorageClass. The pinned server and initializer passed a live isolated test. The
initializer created or verified the `bdv-warehouse` bucket, warehouse identity,
and bucket-scoped policy without printing credentials. A second run reused and
verified all three resources without changing retained credentials. Never use
root keys for warehouse catalog access.

The dev server candidate is pinned at
`sha256:eacd3dc98e269e30b88ed10df48560b8b9e7484465912724bedaecf8d5c9936a`.
It contains both `minio` and `mc`, runs as UID 100/GID 101, and exposes 9000/9001. Both
binaries report `DEVELOPMENT.GOGET` and the image has no provenance labels, so the
source Dockerfile or an identifiable release replacement is still required.
The corrected initializer is pinned at
`sha256:043c62faf5a22d57bdd521e8ed8ef5c78812a89e5faa066749e14f59a9ced63c`.

Production selects `aws-s3`, keeps `minio.enabled=false`, and must supply the
approved bucket and region in its protected values file. Production rendering must
contain no MinIO PVC, Deployment, Service or initializer Job. The existing
`bdv-warehouse-s3` Secret remains the current access-key contract; verify the AWS
identity and bucket policy before deployment.

## Trino/BDV

Startup argv is grounded in the inspected entrypoint and run-trino script. Full
configuration is still required: config.properties, node.properties, jvm.config,
logging, catalogs, JDBC connector account, Keycloak client/trust material, custom
Ranger plugin/JCEKS/trust material and internal shared-secret material. Exact keys
must come from the pinned image startup/plugin implementation.

Leave node.id out of the shared worker node.properties. The inspected run-trino
sets a distinct node ID from HOSTNAME when node.id is absent. Its set -x means
credentials must not be placed in startup argv.

## Dependency/probe contracts

The same readinessCommand is invoked as a prerequisite init container and as
startup/readiness probes. It must explicitly distinguish BDV_CHECK_MODE=prerequisites
from a bounded local probe. It must not wait on its own not-yet-started service or
introduce a circular dependency. The prerequisite timeout is 600 seconds; probe
timeout is five seconds. /ca is now mounted wherever MYSQL_CA is set.

## DNS, ingress and persistence

The live default IngressClass is `traefik`. Confirm pfSense's TLS boundary. Supplied BDV domain
BDV's confirmed hostname is `bdv.dinecodetalkers.com`. Ingress remains disabled
until DNS, authentication and the proxy boundary are verified. Ingress terminates neither TLS nor redirects
by default; the upstream proxy contract must establish those behaviors.

Confirm Longhorn capacity, the MinIO PVC lifecycle and backups. Existing imagePull
Secrets must be configured if images are private. Recheck actual runtime source
addresses before granting any additional MySQL source ranges.
