# Automatic BDV chart publishing

This workflow belongs to helm-charts. It is separate from the BDV Ansible
application and does not deploy any workload into Kubernetes.

## Existing state and source migration

The gh-pages branch currently contains bdv-0.1.0.tgz and index.yaml. The operator
reports that the chart used locally is 0.1.34. Before publishing a new release,
copy the actual current chart source from mdaca-deploy into charts/bdv on main.
The publisher rejects source versions below 0.1.34. It does not fabricate the
missing updated source or treat the existing GitHub 0.1.0 chart as current.

The handoff docs mention other historical kit versions; those do not override
the operator's current local baseline. Verify Chart.yaml when copying the chart.

## One-time repository setting

In Settings > Pages > Build and deployment, select **GitHub Actions** as Source.
The workflow still keeps the published chart packages and index on gh-pages,
and deploys the same files explicitly using the official Pages actions.

An Actions push made with GITHUB_TOKEN does not itself trigger the traditional
branch-based Pages build. The explicit deploy-pages step avoids relying on it.
No personal access token is needed. Repository/organization policies must allow
the workflow contents:write, pages:write and id-token:write permissions.

The github-pages environment must allow deployment from main. If it was
restricted to gh-pages by an older setup, update its deployment branch rule.
Existing protection/reviewer rules may delay the deployment job.

## What happens after a change

A push to main changing charts/bdv triggers validation, packaging and publishing.
Pull requests validate only. Publisher/workflow/test changes also trigger runs;
an unchanged chart is reused once its release marker exists. Runs are serialized
and never force-push gh-pages.

The released package gets the next minor version by default. The calculation
uses the greatest of the source Chart.yaml version and published BDV versions:

| Source | Latest published | Next minor package |
| --- | --- | --- |
| 0.1.34 | 0.1.0 | 0.2.0 |
| 0.1.34 | 0.2.0 | 0.3.0 |

If you mean 0.1.34 to 0.1.35, select patch for a manual workflow run, or set the
repository Actions variable BDV_CHART_BUMP to patch for subsequent pushes.
Minor means increment the middle number and reset the last number to zero.

The chart version is updated in an isolated build copy and in the .tgz/index.
The source Chart.yaml on main is not changed by a bot commit. appVersion is
unchanged. The published index is the release version history. Re-running the
same tracked chart source reuses its package/version rather than releasing again.
Changing only the bump setting does not republish unchanged chart source.

The workflow preserves previous packages and merges existing index entries,
including entries for other charts. A release marker on gh-pages records the
source fingerprint and commit. It prevents repeat releases after deployment
retries. If the package/index were committed but Pages failed, rerun the workflow
so it deploys the same content without another version bump.

A force-pushed or manually changed gh-pages history is outside this workflow's
concurrency control. A non-fast-forward push fails rather than overwriting it.

Dependencies use Chart.lock when present, otherwise Helm resolves declared
versions. Commit a reviewed Chart.lock for repeatable dependency resolution.
Source versions and existing BDV release versions must be stable X.Y.Z values;
pre-release/build-suffix version policies are intentionally not guessed.

The workflow lints and renders default values. Environment-specific values and
live acceptance tests remain separate. No Ranger setup, database bootstrap, or
Ansible deployment is invoked by chart publication.

## Helm clients

```bash
helm repo add digital-innovation https://ddc-innovation.github.io/helm-charts/
helm repo update
helm search repo digital-innovation/bdv --versions
```

Deploy the version you intend, using your own protected values file. A new
published version does not automatically upgrade an installed release.
