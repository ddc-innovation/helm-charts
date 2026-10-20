# BDV implementation and acceptance record

Recorded October 19, 2026 from the operator's supplied command output and the
retained source files. This is an engineering record, not a raw chat transcript.
Passwords, client secrets, shared secrets and temporary OAuth initiation URLs
are intentionally omitted. Source code is under charts/bdv in the kit.

## Accepted live deployment

- Repository: ddc-innovation/mdaca-deploy, master. The operator's successful push
  ended at 867e4fd and included chart 0.1.33. Later capture-kit edits have not been
  committed or pushed by this packaging environment.
- Namespace/release: bdv/bdv. Latest reported live release: revision 14,
  chart 0.1.32, status deployed.
- One coordinator and two ready workers after idle scale-down, zero restarts in
  the final snapshot. HPA minimum two, maximum six, target CPU 70% of requests.
- Six simultaneous workers and an empty bdv-test installation were requested
  afterward and are pending live execution. Local renders are not live evidence.
- Chart appVersion 372 is inherited metadata. Running Trino reported
  f7ef2bd-dirty and inspected plugin libraries were version 480; do not treat the
  appVersion string as verification of the actual Trino binary version.

## Prerequisites and routing

The bastion uses /root/.kube/config, Helm 4.2.0, Python/PyYAML, Docker and
kubeconform. RKE2 target is Kubernetes 1.36.4; Longhorn supplies persistent storage.
External MySQL is 192.168.250.15:3306, Percona 9.7, with a restricted application
account host 192.168.250.0/255.255.255.0. The tested MySQL transport mode is
REQUIRED. Certificate identity verification is a separate configuration choice;
REQUIRED alone does not establish that the server identity was verified.

pfSense HAProxy terminates public HTTPS and forwards HTTP to worker nodes and
Traefik. Live hostnames are bdv.dinecodetalkers.com and
bdv-policy-manager.dinecodetalkers.com. Internal access through the public address
initially timed out. Requests resolving the same names to 192.168.250.1 passed
TLS verification and reached HTTP routing; ingress was enabled afterward.

HAProxy sets X-Forwarded-Proto=https and X-Forwarded-Port=443 at the TLS boundary.
Traefik trusts the reviewed pfSense source IPs 192.168.250.1/32 and
192.168.200.1/32. Kubernetes ingress displaying HTTP/port 80 is consistent with
TLS termination on pfSense. The HTTP leg is part of the trusted proxy boundary.
A reachability probe does not by itself verify header sanitization or firewall
restrictions; keep those as explicit site configuration responsibilities.

## Database bootstrap

The first split Hive bootstrap attempted to invoke an image contract expecting
both Hive and Ranger configurations and failed with "Exactly Hive and Ranger
database configurations required". The chart was changed to the actual combined
bootstrap contract and bundled inspected provision.py for review.

The accepted combined Job persisted bdv-hive-db and bdv-ranger-db, provisioned
schemas and users, checked application logins, verified character set and exact
restricted grants, and confirmed Ranger's database-scoped installer privileges.
Application credentials are retained outside Helm. DBA credentials are used for
bootstrap, not mounted in application runtime pods.

The user originally ran MySQL operations manually on the remote server. The
packaged bootstrap and DBA contract code capture schema/user/password/grant
provisioning and verification. Existing live databases were reused during the
accepted migration; this is not proof of an empty-database installation.

## Hive and JDBC

The base image was Ubuntu 22.04.5, Java 8 and Hive 3.1.3. It lacked jar/javap,
so initial shell-based driver scanning was inconclusive; the later discovery
that jar was missing means the first "no driver" scan was not sufficient proof
of absence. Host ZIP/class parsing and actual runtime checks replaced that scan.

Connector/J 9.7.0 was inspected using its exact SHA-256, driver class and major
version 52. Java loaded the driver class. The derivative added it to Hive,
linked the existing Hadoop AWS libraries onto the Hive classpath, and added the
non-root wrapper with writable temporary/configuration locations.

Hive 3.1.3 legitimately used schema version 3.1.0. Initialization completed and
schema version, sequences, tables, DFS locations and non-NULL column checks all
passed. Reruns validated the existing schema instead of reinitializing it.
The wrapper check later reported that the Metastore was accepting connections.

The first inspection used a read-only container with a small tmpfs and an
unresolvable hostname. A larger temporary filesystem and explicit local hostname
mapping allowed hive --version to complete. Multiple SLF4J bindings and missing
log4j.properties warnings remained separate from schema validation failures.

## MinIO and warehouse

The schema Job initially blocked in hadoop fs -ls because bdv-minio-check did
not exist. Later a 403 InvalidAccessKeyId identified a credential/identity problem.
The MinIO initializer created/verified the bucket, warehouse identity and scoped
policy. Hive then verified warehouse access successfully.

The final runtime created a new 100Gi Longhorn PVC in bdv. The separate older
check PVC and workloads were not automatically deleted. Test objects were written
and read with the warehouse identity. Actual Parquet smoke-table objects were
observed in bdv-warehouse under warehouse/bdv_smoke_20261019/.

## Ranger service definition and policies

The existing mdaca-ranger-plugin definition exposed an application resource and
was not the Trino system-access-control contract. Inspection of the installed
Trino Ranger plugin identified catalog/schema/table/column and the additional
trinouser, procedure, systemproperty, sessionproperty, schemafunction, role,
queryid and sysinfo resources.

An existing trino definition had an older resource set. It was backed up and
merged additively, preserving function and update as legacy additions. Missing
schemafunction, role, queryid, sysinfo, read_sysinfo and write_sysinfo contracts
were added and verified. The live Trino Ranger service was bdv-trino.

Several API behaviors were captured in the reconciler:

- Missing users may return HTTP 400 DATA_NOT_FOUND instead of 404.
- User creation required a nonblank generated password.
- Creating another policy for the same resource signature returned HTTP 400.
  Reconciliation therefore adopts and updates the matching existing policy.
- Root resource parent can be absent, null or an empty string. Helm can omit
  null keys, so code uses contract.get('parent') and normalizes root parents.
- Existing users and unrelated policy entries are preserved. Removing entries
  from values does not automatically revoke an existing Ranger permission.

Healthcheck readiness initially failed because self-impersonation was denied.
Per-user self policies fixed that. Query execution, catalog access, schema SHOW,
information_schema column reads, and exact smoke-table reads required separate
permissions. Policy changes sometimes needed the 30-second refresh interval
before a retry succeeded.

Richard and Michael were configured for query execution, self-impersonation,
metadata and the exact smoke table. Both received verified column SELECT and
INSERT/DELETE/DROP grants. Richard's authenticated SELECT and INSERT were run
successfully. Michael's policy configuration was verified, but his independent
interactive login/query was not supplied as live acceptance evidence.

DELETE permission does not prove that row DELETE is supported on the tested
nontransactional Parquet table. DROP was not executed against retained smoke
data. Access was scoped to the smoke table, not unrestricted warehouse access.

The Trino MySQL plugin already bundled Connector/J 9.6.0. That separate plugin
was inspected and left unchanged; adding 9.7.0 to Hive/Ranger was not a blanket
replacement of every JDBC library inside Trino. The Hive connector uses native
S3 settings in Trino and the metastore URI; Hive itself retains Hadoop S3A.

## Keycloak and Trino authentication

Existing issuer: https://sso.dinecodetalkers.com/realms/innonet. Live client: bdv.
Discovery endpoints were verified. The client uses a confidential standard flow,
exact /oauth2/callback, profile/email scopes and preferred_username identity.
Client setup is managed through digin-automation realm-management.yml --tags client.
create-realm.yml was the wrong workflow for the client-only change and failed
on an absent password_policy variable.

The coordinator receives issuer/client ID/client secret through Secret-backed
references. Workers receive the internal shared secret, not OAuth client secrets.
The CLI environment sets TRINO_CONFIG=/dev/null because the image's original
TRINO_CONFIG directory value collided with the CLI config-file setting.

Raw local HTTP readiness checks require /v1/info HTTP 200 and starting=false.
The accepted negative test rejected unauthenticated local SQL with
"Authentication over HTTP is not enabled". Public browser/CLI authentication
through Keycloak worked. Richard's SELECT current_user returned his email identity.

Ranger SSO was deferred by the user. Ranger uses its local administrator login.
A browser navigation observation is not evidence that native Ranger OIDC was set up.

## Configuration consolidation and defects fixed

The chart now manages nonsecret coordinator, worker, catalog and plugin files
from configuration.files in values. Sensitive values are environment/Secret
references. Ranger vendor install.properties is extracted, tokenized and imported
into a protected site values file. The optional Hive overlay is left unset because
its wrapper generates protected XML from database and warehouse Secrets.

Empty local configuration directories initially produced empty Kubernetes Secrets.
The missing files were generated, checked, and applied. Secret keys and contents
were compared privately; values were not printed. Last-applied annotations were
removed after the repair. New managed configuration avoids those legacy config
Secret copies while retaining old Secrets for operator recovery.

An extra blank before XML declarations from nindent caused the Trino XML parser
to fail at line 2. Rendering now trims before indenting. Backups created inside
templates were also rendered by Helm, creating duplicate stale ConfigMaps.
Backups were moved outside templates and XML parsing/duplicate-identity checks
were added. The live ConfigMap then passed XML validation and the coordinator
was restarted successfully.

The drain-preparation script initially assumed site values contained the full
configuration.files map. The fix merges chart defaults first; a regression test
covers inherited site configuration and preserves a backup.

## JVM and worker scaling

The early configuration used RAM percentages, while a staged script incorrectly
expected literal -Xms1G/-Xmx3G. This mismatch was resolved during staging. The final
managed configuration derives JVM initial/max heap percentages from each pod's
memory limit: initial 25%, max 65%. Query per-node memory is 30% of the limit and
heap headroom 20%. Worker and coordinator resource limits are separately configured.
Pod requests are scheduling reservations, not JVM minimum heap settings.

The accepted HPA uses CPU utilization as a percentage of CPU requests, minimum
2 and maximum 6. It scaled from 2 to 3 during three bounded synthetic SQL queries.
All three queries completed. Six-worker scaling was not yet exercised live.

## Graceful shutdown acceptance

A public GET /v1/info/state returned ACTIVE and did not prove shutdown permission.
Worker inspection found OpenSSL, base64, sha256sum, date and sleep, but no compiler
module. The helper uses Trino 480 internal JWT signing with HKDF-SHA256 and a
loopback-only request. An invalid-state probe reached JSON validation without
changing state. Every replacement worker passed the probe.

The worker-only preStop hook requests SHUTTING_DOWN and waits for listener closure.
It has an 840-second wait within Kubernetes' 900-second termination budget. The
configured Trino shutdown grace is 120 seconds before task completion waiting,
then another 120 seconds for task states to reach the coordinator. Readiness
rejects a non-ACTIVE state. Long tasks can still exceed the finite termination
budget; this is not an unlimited guarantee against termination.

During the manual busy-worker test, a running task was found before deletion:

- 17:40:44 UTC: ACTIVE -> SHUTTING_DOWN, first 120-second grace began.
- 17:42:44 UTC: all active tasks finished; second 120-second grace began.
- 17:44:44 UTC: HTTP server shutdown completed.

The replacement was ready, the old Pod disappeared, and all three authenticated
load queries completed successfully. Automatic scale-down was then enabled.
The HPA reduced desired replicas from 3 to 2 because metrics were below target.
The selected worker logged SHUTTING_DOWN at 17:52:27 UTC, then disappeared after
the drain window. The final snapshot showed only two ready workers.

Final checks passed for all deployments, latest authorization Job, active HPA
metrics and unauthenticated-query rejection. A final authenticated warehouse read
returned the retained original rows and the later unique write-test row.

## What remains to verify or obtain

- Execute the fresh bdv-test bootstrap against empty test databases.
- Register the new bdv-test Keycloak client and test-host proxy/DNS prerequisites.
- Bring up six workers, verify seven Trino nodes including the coordinator, and
  test fresh storage and query execution before returning to a 2-to-6 HPA range.
- Test six-way query task utilization separately if required; six registered
  workers alone do not prove all six executed tasks in one query.
- Obtain original vendor image build contexts and the bootstrap/custom Trino
  Dockerfiles where unavailable. See IMAGE-BUILDS.md for precise source coverage.
- Rebuild and validate new candidate image tags before changing accepted digests.
  New packaging scripts were not Docker-built in this environment.
- Capture current site-specific values privately and retain external database,
  object-storage and Keycloak backups through the operator's existing process.

No automatic cleanup, database drops, PVC deletion, production rollback,
credential rotation or GitHub push is performed by this handoff package.

## October 19 acceptance update

See [fresh-install and CPU scaling acceptance](ACCEPTANCE-20261019.md) for
operator evidence superseding earlier pending fresh-install/six-worker notes.
