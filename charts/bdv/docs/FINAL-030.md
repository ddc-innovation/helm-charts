# Consolidation checkpoint

Use README.md for the complete runbook. Version0.1.30 is an integration of the
live-tested images and authentication/storage/policy contracts, with new managed
configuration, repeatable authorization and worker/HPA paths. No new images are
required. The custom Trino binary still identifies as f7ef2bd-dirty.

## What is new

- Values-owned coordinator/worker/catalog/plugin ConfigMaps with Secret references.
- Complete vendor install.properties import into site values and Helm rendering.
- Additive authorization Job, including service-definition hierarchy, explicit
  identities, self-only impersonation and resource-matched policy adoption.
- Fixed workers or HPA min2/max6 configurable; CPU metrics; scale-down disabled.
- Container-aware JVM and query-memory/headroom derived from per-role pod limits.
- Setup phases, retained credential setup, read-only and authenticated SQL tests.
- Existing Keycloak client-management example and current-install migration path.

## Intentional boundaries

No Ranger SSO. No identity/group synchronization or automatic revocation. No
automatic data/table deletion. No password rotation. No current-cluster changes.
No assumption that CPU HPA implements query draining or node autoscaling.
Existing check resources/configuration Secrets remain rollback/evidence assets.

## Live acceptance still required

Validate the newly rendered ConfigMaps with the actual binary, both workers
joining with internal authentication, both per-role JVM memory observations,
Ranger reconciliation against the real service, effective human read/write,
and active HPA metrics. Verify actual distributed tasks under representative
authorized load. Explicitly test graceful worker drain before scale-down.

The earlier live SELECT/INSERT evidence belongs to revision9 before this new
managed configuration. Do not present offline schema tests as functional proof.
