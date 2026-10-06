# BDV Helm Chart

This chart deploys the BDV solution accelerator.

BDV-owned workloads:

- Trino coordinator
- Trino workers
- Apache Ranger
- Hive Metastore
- Bootstrap job

Upstream chart dependencies:

- MinIO from `https://charts.min.io/`
- Percona Server for MySQL operator and database from
  `https://percona.github.io/percona-helm-charts/`

## Dependency Update

```bash
helm dependency update charts/bdv
```

## Local Lab Install With MinIO And Percona

```bash
helm upgrade --install bdv charts/bdv \
  --namespace bdv \
  --create-namespace \
  -f charts/bdv/examples/values-local-minio-percona.yaml
```

## Production Install With External S3 And External Database

Create the database and object-store secrets first, then install:

```bash
helm upgrade --install bdv charts/bdv \
  --namespace bdv \
  --create-namespace \
  -f charts/bdv/examples/values-remote-s3-remote-db.yaml
```

## EKS With IRSA

```bash
helm upgrade --install bdv charts/bdv \
  --namespace bdv \
  --create-namespace \
  -f charts/bdv/examples/values-eks-irsa.yaml
```

## Required Secrets For External Database

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: bdv-db
type: Opaque
stringData:
  username: bdv
  password: change-me
```

## Required Secrets For External S3 Without IRSA

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: bdv-s3
type: Opaque
stringData:
  accessKeyId: change-me
  secretAccessKey: change-me
```

