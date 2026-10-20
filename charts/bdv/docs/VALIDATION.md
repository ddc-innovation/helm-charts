# Verification of this revision

Revision 0.1.7 retains the API projection fix from 0.1.2 and corrects the Ranger
boundary: bootstrap provisions identity and privileges; Ranger's own setup.sh owns
schema/config installation in a deployment init container. It also separates the
password-free install.properties ConfigMap from the database and Ranger service
credential Secrets. Revision 0.1.2 addressed the
live failure in which the diagnostic kubectl attempted
localhost:8080 despite successful administrator-side RBAC impersonation checks.
This established missing/unusable automatic API configuration, not which particular
injection or client-discovery mechanism failed. The revised Pod explicitly projects
its ServiceAccount token and namespace CA; the wrapper selects the internal HTTPS
API through a private temporary kubeconfig with tokenFile. No MySQL credentials
or application Secret values are changed by this API-configuration fix.

Additional checks passed for kubeconfig namespace/CA/token-file references, mode
0600, absent-token rejection, rendered projection/mounts and projected token
expiration. The rendered wrapper compiles. These checks do not establish that the
revised pod can authenticate in the live cluster; rerun database-only preflight and
then the Helm hook there. No image rebuild is required for this mounted-code fix.

Tools prepared separately in scratch, verified against their published checksums:
Helm 4.2.0, kubeconform 0.7.0, kubectl 1.36.4. Bash and Python 3/PyYAML were already
available. The delivered validation scripts never install or fetch executables.
Kubeconform downloads schemas for the explicit target 1.36.4 during validation.

| Check | Result | What it establishes |
| --- | --- | --- |
| Python compilation and Bash syntax | Passed | Included scripts parse |
| Offline Helm lint/render + strict kubeconform | Passed, 1 resource | Contract ConfigMap syntax/schema only |
| Database-only Helm lint/render + strict kubeconform | Previous combined hook passed, 6 resources | Split Hive/Ranger hooks require rerender on the bastion |
| Runtime template fixture, REQUIRED | Passed, 20 resources | Templates and Ranger ConfigMap render without CA dependencies |
| Runtime template fixture, VERIFY_CA | Passed, 20 resources | CA environment/mount/volume wiring is present |
| Runtime template fixture, VERIFY_IDENTITY | Passed, 20 resources | CA environment/mount/volume wiring is present |
| Unsafe values checks | Passed | Wildcard source, shared application Secret names and malformed digest rejected |
| Default full-runtime validation | Correctly rejected | Unverified application fields and confirmation flags remain unresolved |
| Foreground launcher stub test | Passed | Java output and exit status propagate; RUN does not daemonize |
| Ranger init adapter compilation | Passed | Python adapter parses and enforces SeparateDBA/MySQL/JDBC inputs |
| Ranger init ordering render check | Passed | Vendor init precedes prerequisites and main mounts prepared home |
| Ranger init adapter fixture | Passed | Both Secret sets are consumed; vendor setup runs; output is redacted; install.properties is removed; XML/JCEKS are required |
| Ranger credential mount render check | Passed | Service-password Secret is mounted only by vendor init, not the main Ranger container |
| Bootstrap password wrapper | Passed | Random token retained and required character classes guaranteed |
| Pinned bootstrap reference in rendered Job | Passed | Tested registry digest is selected, not mutable tag content |

Runtime fixture files were temporary and use dummy commands. They are not shipped,
were never installed, and are not evidence for real image startup, probes, JDBC,
schema initialization, S3 access or Keycloak. They exercised template branches and
Kubernetes schema structure only.

The 0.2.2 vendor-init/foreground image passed its remote image contract, including
separate service-password token consumption, and was pushed at digest
`sha256:69a3e2657017b624dde9321761b39ca339f6922198eb314feef965973d796b36`.
Its first live init reached vendor setup but stopped before schema SQL when the
installer attempted to copy Connector/J over the existing destination link. The
0.2.3 build source removes that destination link and verifies that the source JAR
remains while the vendor destination is absent. Version 0.2.3 passed that contract
and was pushed at digest
`sha256:1112b6976a88393d863754d37b69258789cd34a608fdc1719fd4fdbfeec5024e`.
The next live attempt connected successfully using encrypted JDBC, created the
version table, and reached the core schema import. It stopped at `CREATE VIEW`
because that privilege was absent. Static review of the shipped MySQL core and
patch SQL found `CREATE VIEW` plus stored procedures and no triggers, functions, or
events. Revision 0.1.7 added database-scoped `CREATE VIEW` and `SHOW VIEW` to the
bootstrap wrapper.

The retry proved that the vendor core SQL is not a safe cleanup path for this
partially installed schema on Percona 9.7.1. It failed with error 3730 while
dropping `x_role`, which was still referenced by `x_security_zone_ref_role`. The
vendor installer removed its failed marker and instructed the operator to drop the
database. Revision 0.1.8 records the required recovery: for this new, empty Ranger
installation, recreate only `bdv_ranger` while preserving its character set,
collation, account, grants, passwords, and Kubernetes Secrets. The clean live retry
then completed in 30 seconds. The resulting `x_db_version_h` contained 108 active
rows, including `CORE_DB_SCHEMA`, `DB_PATCHES`, `JAVA_PATCHES`, and
`DEFAULT_ALL_ADMIN_UPDATE`. Repeat initialization against the completed schema
also passed. The vendor installer detected the active Java-patch and administrator
password-update markers, skipped the completed work, regenerated JCEKS/XML runtime
configuration in a new EmptyDir, and reported the shared runtime home ready.

The prepared foreground runtime then rolled out successfully with no restart. Its
first test exposed a missing `/work` mount in the standalone fixture because vendor
configuration retained absolute `/work/ranger-home` log paths. Adding the same
dual mount already modeled by the chart resolved startup. Ranger returned HTTP 302
from `127.0.0.1:6080/`, redirecting to its login page. Revision 0.1.11 uses a
bounded `curl --fail --max-time 3` probe against that path, skips the impossible
pre-start HTTP probe for Ranger, and limits DB/service-password Secret mounts to
the vendor init container.

Authenticated runtime checks returned HTTP 200 from the login page, metrics status,
and plugin-definition endpoints. The bundled `mdaca-ranger-plugin` definition had
no assigned ID before registration, then a guarded POST registered it and a second
GET verified it. Revision 0.1.12 implements this as a post-install/post-upgrade Job
that checks by name and skips an existing definition. Its password comes from a
read-only Secret file, never argv, and the main Ranger container does not mount it.

Release notes were also checked to expose the external Ranger URL and `admin`
username without rendering a credential. They provide an on-demand `kubectl`
Secret retrieval command. This is intentionally separate from Ranger's internal
JCEKS use: JCEKS supplies runtime aliases, while the retained Secret supplies the
operator's recoverable login value.
The full chart was not installed. No cluster credentials were supplied here, and
no live database changes or Secret operations were performed during editing.
Read-only cluster preflight must be run on the user's bastion.

The database-only mode and projected API wrapper subsequently passed in the live
cluster. The hook reused/provisioned `bdv-hive-db` and `bdv-ranger-db`, then verified
both application database logins. That result does not prove the additional Ranger
routine privilege, schema initialization or application startup.

Revision 0.1.14's split Job approach was invalid for the pinned provisioner,
which requires both Hive and Ranger entries together. Revision 0.1.27 changed
bootstrap-only mode to one combined Job; revision 0.1.28 bundles the inspected
provisioner script and adds DBA-state verification. Five offline tests cover
exact grants and rejection of missing, global, grant-option and collation-drift
cases. Live Job verification remains necessary on the bastion. The bootstrap
image build sources remain unavailable; this is not a reproducible image build.

Revision 0.1.15 adds dev and production object-storage profiles. Dev must render
the MinIO PVC, Deployment, Service and initializer Job using Longhorn. Production
must render none of those resources and requires an explicit AWS S3 bucket and
region. Both profile renders require validation on the bastion.

## MinIO development profile

The pinned development MinIO server image runs as UID 100 and GID 101. A live
Longhorn-backed deployment and the pinned initializer completed successfully.
The initializer created or verified the `bdv-warehouse` bucket, warehouse
identity, and bucket-scoped policy without printing credentials. The pod
contract uses `fsGroup: 101`, a read-only root filesystem, and writable
`/data`, `/home/minio`, and `/tmp` mounts.

The first repeat exposed an order-dependent policy comparison. Revision 0.1.20
changed validation to compare exact action and resource sets. The corrected
initializer at digest
`sha256:043c62faf5a22d57bdd521e8ed8ef5c78812a89e5faa066749e14f59a9ced63c`
then completed successfully against the existing bucket, identity, and policy.

## Hive metastore image and init contract

Revision 0.1.23 pins the pushed Hive derivative at
`sha256:3effb84e27f91c2f87707bee8c03d36b53f378d81d3106e3c903377a31c3e42e`.
The image build verified Connector/J 9.7.0 and Java 8 compatibility. Static chart
checks require the Hive prerequisite init to precede schema init, schema init to
receive both database and object-storage Secrets, and all Hive containers to run
as UID/GID 10001 with read-only root filesystems. Live schema initialization and
metastore startup subsequently passed on the bastion. The first initialization
created and validated the Hive 3.1.0 metastore schema used by Hive 3.1.3. A repeat
run validated the existing schema and verified access to the MinIO warehouse
bucket. The foreground metastore pod became Ready with zero restarts and accepted
connections on port 9083. A missing MinIO Service caused the original S3 command
to wait, so the included next-build wrapper adds an explicit S3 timeout and a
positive readiness-probe message. The replacement is pinned at
`sha256:41b722eb180b952537cc0fb3d40eb5db7a52b7b57a4af86d2e3a430ed849eeb5`
and passed its repeat live check. Kubernetes reported this exact digest for both
the schema init and runtime containers. The Deployment became Ready with zero
restarts, and the corrected readiness command printed
`PASS: Hive Metastore is accepting connections`.

