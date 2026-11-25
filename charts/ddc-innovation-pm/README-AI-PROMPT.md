# DDC Innovation PM: Rancher Deployment Prompt Context

Use this document as reusable context inside a larger AI prompt when planning,
reviewing, troubleshooting, or automating deployment of `ddc-innovation-pm` to a
Rancher-managed Kubernetes cluster.

It deliberately contains no credentials. Replace placeholders such as
`<IMAGE_TAG>` and `<CONNECTION_STRING>` only at execution time, and never commit
their resolved values.

## How to use this document

Copy the section named **Reusable AI prompt block** into the context portion of
your larger prompt. Add the specific task you want the AI to perform after the
block, for example:

> Using the deployment context below, generate a change plan and the exact
> commands needed to upgrade production from image tag `949f353` to `abc1234`.
> Do not execute anything.

The AI should treat this file and the chart's main [README](README.md) as the
deployment contract. Application source, cluster state, DNS, Keycloak, Harbor,
and database state must be verified rather than inferred.

## Reusable AI prompt block

```text
You are helping deploy or operate DDC Innovation Portfolio Management (DCIPM)
on a Rancher-managed Kubernetes cluster.

Repository and chart:
- Helm repository name: digital-innovation
- Helm repository URL: https://ddc-innovation.github.io/helm-charts/
- Chart: digital-innovation/ddc-innovation-pm
- Chart source: https://github.com/ddc-innovation/helm-charts/tree/main/charts/ddc-innovation-pm
- Release name: ddc-innovation-pm
- Production namespace: ddc-pm
- Production hostname: ddc-innovation-pm.dinecodetalkers.com

Application images:
- API: harbor.dinecodetalkers.com/ddc-innovation-pm/dcipm-api:<IMAGE_TAG>
- Web: harbor.dinecodetalkers.com/ddc-innovation-pm/dcipm-web:<IMAGE_TAG>
- The same immutable image tag must exist for both repositories.
- image.tag is required and has no default.
- Do not use latest, a branch name, or an unverified tag.

Required namespace-scoped objects:
- Namespace: ddc-pm
- Registry pull Secret: dcipm-registry-pull
- Database Secret: dcipm-db
  - ConnectionStrings__Dcipm
  - ConnectionStrings__DatabaseExplorerReadOnly
- Optional bootstrap administrator Secret: dcipm-auth
  - Auth__BootstrapAdmins__0
- Deployment identity permissions must be verified before installation. The
  chart does not grant a deployer permission to the namespace.

Security and configuration rules:
- Never place credentials in a values file, command transcript, AI response,
  pull request, or source repository.
- Prefer pre-created Kubernetes Secrets. Do not enable secrets.create for
  production unless the user explicitly accepts that Helm will retain secret
  values in release history.
- TLS terminates outside the cluster at pfSense HAProxy. Traefik and the pods
  receive HTTP, with the original scheme preserved through forwarded headers.
- Production must keep features.useHttpsRedirection=false.
- Production ingress uses the one approved hostname only. Do not invent a
  development hostname or allow a second release to claim the production host.
- Production authentication mode is Keycloak.
- The production Keycloak metadata address is the public HTTPS endpoint; do
  not use an in-cluster HTTP metadata address in Production.
- The private Harbor project requires dcipm-registry-pull on the API, web, and
  migration workloads.
- A manifest unknown/not found response means the requested image tag was not
  pushed. An unauthorized/denied response indicates a credential problem.
- The migration Job is a pre-install/pre-upgrade Helm hook. Database migrations
  must remain additive because helm rollback does not reverse them.

Production Helm values override:

namespace: ddc-pm
aspnetcoreEnvironment: Production
authMode: Keycloak

image:
  tag: "<IMAGE_TAG>"

ingress:
  enabled: true
  host: ddc-innovation-pm.dinecodetalkers.com

cors:
  allowedOrigins: []

keycloak:
  metadataAddress: ""
  jwksUrl: ""

imagePullSecrets:
  - name: dcipm-registry-pull

features:
  useHttpsRedirection: false

Preferred Helm workflow:
1. Use the kubeconfig for the intended Rancher-managed downstream cluster.
2. Confirm the active kubectl context and namespace before mutating anything.
3. Add/update the published Helm repository.
4. Confirm the desired chart version and image tag exist.
5. Confirm all required Secrets exist in ddc-pm without printing their values.
6. Render or dry-run first.
7. Install or upgrade with --wait and --timeout 10m.
8. Verify the Helm release, hook Job, pods, Services, and Ingress.
9. Report exact failures and evidence. Do not guess whether an ImagePullBackOff
   is caused by a missing image or bad credentials.

Baseline commands:

helm repo add digital-innovation https://ddc-innovation.github.io/helm-charts/
helm repo update
helm search repo digital-innovation/ddc-innovation-pm --versions

helm pull digital-innovation/ddc-innovation-pm \
  --version <CHART_VERSION> \
  --untar

helm upgrade --install ddc-innovation-pm ./ddc-innovation-pm \
  --namespace ddc-pm \
  --values ./ddc-innovation-pm/values-prod.yaml \
  --set-string image.tag=<IMAGE_TAG> \
  --wait \
  --timeout 10m

Verification commands:

helm status ddc-innovation-pm --namespace ddc-pm
helm get values ddc-innovation-pm --namespace ddc-pm
kubectl get pods,services,ingress,jobs --namespace ddc-pm
kubectl get events --namespace ddc-pm --sort-by=.lastTimestamp

Before suggesting or executing a change, ask for any missing value that would
materially affect the result, including the target cluster/context, chart
version, image tag, deployment environment, and whether execution is authorized.
Never fabricate credentials or production state.
```

## Rancher UI mapping

The same deployment can be performed through Rancher:

1. Open the intended downstream cluster.
2. Go to **Apps > Repositories** and add an HTTP(S) Helm repository named
   `digital-innovation` with URL
   `https://ddc-innovation.github.io/helm-charts/`.
3. Create or select the `ddc-pm` namespace.
4. Create the required Secrets in `ddc-pm` without exposing their values in the
   chart configuration.
5. Go to **Apps > Charts**, select `ddc-innovation-pm`, and choose the intended
   chart version.
6. Install with release name `ddc-innovation-pm` and paste the production values
   override from the prompt block into the values editor.
7. Follow the Helm operation logs, then verify the release under
   **Apps > Installed Apps** and the workloads under **Workloads**.

Repositories added in Rancher are cluster-scoped catalog configuration for the
selected downstream cluster; adding the repository to one cluster does not
automatically add it to every managed cluster.

## Kubernetes Secret command templates

These are templates only. Substitute credentials through a secure local process
that does not persist them in shell history or logs.

```bash
kubectl create namespace ddc-pm

kubectl --namespace ddc-pm create secret docker-registry dcipm-registry-pull \
  --docker-server=harbor.dinecodetalkers.com \
  --docker-username='<HARBOR_ROBOT_USERNAME>' \
  --docker-password='<HARBOR_ROBOT_PASSWORD>'

kubectl --namespace ddc-pm create secret generic dcipm-db \
  --from-literal=ConnectionStrings__Dcipm='<PRIMARY_CONNECTION_STRING>' \
  --from-literal=ConnectionStrings__DatabaseExplorerReadOnly='<READONLY_CONNECTION_STRING>'

kubectl --namespace ddc-pm create secret generic dcipm-auth \
  --from-literal=Auth__BootstrapAdmins__0='<ADMIN_EMAIL>'
```

For production automation, prefer the existing secret-management or Ansible
workflow over interactive commands.

## Acceptance criteria for an AI-generated deployment plan

An acceptable plan or automation must:

- identify the exact Rancher-managed cluster and Kubernetes context;
- use namespace `ddc-pm` and release name `ddc-innovation-pm`;
- select an explicit chart version and immutable image tag;
- confirm both Harbor images exist before deployment;
- verify required Secrets by name without printing their contents;
- render or dry-run the chart before installation;
- preserve the external TLS termination and forwarded-header design;
- wait for the migration hook and workloads to complete;
- verify the resulting Ingress, Services, pods, Jobs, and Helm release;
- provide rollback or recovery guidance appropriate to the observed failure;
- avoid claiming that `helm rollback` reverses database migrations.

## Useful references

- [Full chart documentation](README.md)
- [Default values](values.yaml)
- [Production values](values-prod.yaml)
- [Development values](values-dev.yaml)
- [Published Helm repository](https://ddc-innovation.github.io/helm-charts/)
- [Rancher Helm Charts and Apps documentation](https://ranchermanager.docs.rancher.com/v2.14/how-to-guides/new-user-guides/helm-charts-in-rancher)
