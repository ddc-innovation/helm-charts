# Automatic multi-chart publishing

The repository publishes every chart configured in
`charts/release-config.yml` through one workflow. Pull requests validate all
configured charts. A change on `main` packages changed chart sources, merges
them into the retained `gh-pages/index.yaml`, and deploys that directory with
GitHub Pages.

## Adding a chart

1. Add the chart under `charts/<chart-name>`.
2. Add one entry to `charts/release-config.yml` with its namespace and release
   bump policy.
3. Add required synthetic `--set-string` values under `set` when defaults are
   intentionally incomplete, such as an immutable image digest.
4. Add chart-specific regression commands under `tests` when needed.

No chart-specific workflow or publishing script is required.

## Release behavior

The first package uses the version from `Chart.yaml`. Later source changes
increment from the greatest source or published version using the configured
`major`, `minor`, or `patch` policy. Packaging changes only an isolated copy;
the workflow does not bot-edit `Chart.yaml` on `main`.

Each chart has a fingerprint marker on `gh-pages`. Re-running unchanged source
reuses the existing package and index entry. The workflow refuses to overwrite
an existing package. Publishing is serialized across the entire repository so
two chart changes cannot race while updating the shared index.

The current BDV entry preserves its historical minimum source version and
minor-bump behavior. Postfix relay starts at `0.1.0` and uses patch bumps.

## Repository settings

Under **Settings > Pages > Build and deployment**, select **GitHub Actions**.
The `github-pages` environment must allow deployment from `main`. The workflow
requires `contents: write`, `pages: write`, and `id-token: write` only in its
publish job.

## Helm clients

```bash
helm repo add digital-innovation https://ddc-innovation.github.io/helm-charts/
helm repo update
helm search repo digital-innovation --versions
```

Deploy a specific version with `--version`. Publishing a new package never
automatically upgrades a running release; application deployment automation
must pin and intentionally roll out the desired chart version.
