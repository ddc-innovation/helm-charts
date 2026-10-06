# Digital Innovation Helm Charts

This repository is the public Helm chart repository for Digital Innovation product and solution accelerator deployments.

The purpose of this project is to provide one standard location where Kubernetes platform teams can find, review, package, and install Helm charts for Digital Innovation products such as BDV, Data Flow, Test Ops, and App Factory.

Each product has its own chart under the `charts/` folder. Shared publishing, linting, packaging, and release automation should live at the repository root.

## Repository Layout

```text
helm-charts/
  charts/
    bdv/
    data-flow/
    test-ops/
    app-factory/
  scripts/
  .github/
    workflows/
  README.md
```

## Chart Purpose

| Chart | Purpose |
| --- | --- |
| `bdv` | BDV data platform chart. Intended for Trino coordinator, Trino workers, Apache Ranger, Hive Metastore, and optional dependencies such as MinIO or Percona. |
| `data-flow` | Data Flow accelerator chart. Intended for Apache NiFi based ingestion and movement patterns. |
| `test-ops` | Test operations chart. Intended for test automation and validation workloads. |
| `app-factory` | Application Factory chart. Intended for application build, deployment, and developer enablement workloads. |

## Prerequisites

Before using this repository, install and configure:

- Kubernetes cluster access with a valid `kubeconfig`
- Helm 3
- kubectl
- Git
- Access to the container registry used by each chart
- Access to any required secrets, databases, object storage, or IAM roles used by the target chart

Optional tools:

- `yq` for YAML review and scripting
- `ct` from chart-testing for stronger Helm chart validation
- GitHub Actions or another CI/CD system for automated linting and packaging

## Local Chart Usage

From the repository root, lint a chart:

```bash
helm lint charts/bdv
```

Render a chart locally without installing it:

```bash
helm template bdv charts/bdv
```

Install a chart from the local repository:

```bash
helm upgrade --install bdv charts/bdv \
  --namespace bdv \
  --create-namespace
```

Uninstall a release:

```bash
helm uninstall bdv --namespace bdv
```

## Public Helm Repository Usage

After this repository is published through GitHub Pages, users should be able to add it as a Helm repository:

```bash
helm repo add digital-innovation https://digital-innovation.github.io/helm-charts
helm repo update
```

Then install a chart:

```bash
helm upgrade --install bdv digital-innovation/bdv \
  --namespace bdv \
  --create-namespace
```

## Packaging Charts

Each chart should be packaged from the repository root:

```bash
helm dependency update charts/bdv
helm package charts/bdv --destination .repo
helm repo index .repo --url https://digital-innovation.github.io/helm-charts
```

The generated `.tgz` chart packages and `index.yaml` can be published to GitHub Pages.

## Dependency Guidance

Do not copy or maintain third-party Helm templates unless there is a strong reason to do so.

For products such as MinIO, Percona, NiFi, or other platform components, prefer referencing their public upstream Helm charts as dependencies in the product chart `Chart.yaml`.

This keeps Digital Innovation responsible for the accelerator configuration while allowing the upstream projects to maintain their own Kubernetes resources.

## Contribution Guidance

When adding a new product chart:

1. Create a folder under `charts/<product-name>`.
2. Add `Chart.yaml`, `values.yaml`, and a `templates/` folder.
3. Keep environment-specific settings in separate values files.
4. Run `helm lint`.
5. Run `helm template` and review the rendered Kubernetes manifests.
6. Package the chart and update the Helm repo index before publishing.

## Naming Standard

Use lowercase chart names with hyphens:

```text
bdv
data-flow
test-ops
app-factory
```

Avoid spaces, underscores, or organization-specific prefixes in chart names.

