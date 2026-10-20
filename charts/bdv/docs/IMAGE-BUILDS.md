# Image build sources and original image lineage

This directory captures our derivative builds. It does not claim to contain the
original vendor product source or the original custom Trino build. Existing
Helm values remain pinned to the images accepted in the live cluster. Building
candidates does not update Helm or push tags.

## Original layers

### Ranger

1. Original vendor base:
   `rstevens198238/mdaca-policy-manager@sha256:ebedcba7cab291e96135db5e062e9b9d992e63653028bf768142b0064c993030`.
2. JDBC-only parent:
   `rstevens198238/mdaca-policy-manager@sha256:777c25b7736c26d62dc322b88c85f2453067bc3fd5e4b0acb9514db8222920ad`.
   Captured in `images/ranger/Dockerfile.jdbc`. It installs the verified JAR
   under `/opt` and creates the original WEB-INF symlink.
3. Accepted runtime derivative:
   `rstevens198238/mdaca-policy-manager@sha256:1112b6976a88393d863754d37b69258789cd34a608fdc1719fd4fdbfeec5024e`.
   Captured in `images/ranger/Dockerfile` with `ranger-init.py`. It removes the
   JDBC WEB-INF symlink so the vendor installer owns that copy, uses the vendor
   SeparateDBA setup flow, and prepares runtime XML/JCEKS in a shared EmptyDir.

The original tag aliases for these Ranger layers were not retained in the
provided output. Their exact digests were retained. Do not invent historical
tag names or overwrite an accepted tag with rebuilt bytes. Current local tag
aliases can be recorded using the capture script below.

The final Dockerfile defaults to the accepted JDBC parent digest. To rebuild
only our runtime layer on the original JDBC layer:

```bash
cd bdv-helm/charts/bdv
export RANGER_IMAGE='rstevens198238/mdaca-policy-manager:runtime-capture-0.1.34'
bash images/ranger/build.sh
```

To rebuild both derivative layers, first build the JDBC parent with a new tag,
then use that new tag as the next build's base:

```bash
cd bdv-helm/charts/bdv
export MYSQL_JDBC_JAR='/root/bdv-ranger-mysql/mysql-connector-j-9.7.0.jar'
export RANGER_JDBC_IMAGE='rstevens198238/mdaca-policy-manager:jdbc-9.7.0-capture-0.1.34'
bash images/ranger/build-jdbc.sh

export RANGER_BASE_IMAGE="$RANGER_JDBC_IMAGE"
export RANGER_IMAGE='rstevens198238/mdaca-policy-manager:runtime-capture-0.1.34'
bash images/ranger/build.sh
```

The runtime script verifies Java, the JDBC file location/vendor-copy contract,
the separate service-password Secret contract and rejection of an unconfigured
initializer. `ranger-admin-services.sh` is also retained as an inspected launcher
reference. The Dockerfile inherits that launcher from the pinned parent;
it does not silently overwrite the vendor launcher with an untested copy.

### Hive

Original vendor base:
`rstevens198238/mdaca-metastore@sha256:fa0af47ce86878d907906e397441cb587b49cce4bfdfd73f9995ca44dfd469e9`.

The first accepted derivative tag was `hive-3.1.3-mysql-9.7.0-0.1.0`, digest
`sha256:3effb84e27f91c2f87707bee8c03d36b53f378d81d3106e3c903377a31c3e42e`.
The later tag `hive-3.1.3-mysql-9.7.0-0.1.1`, digest
`sha256:41b722eb180b952537cc0fb3d40eb5db7a52b7b57a4af86d2e3a430ed849eeb5`,
is the accepted runtime image in the chart. The retained wrapper is the later
implementation. The exact earlier wrapper bytes were not supplied separately.

`images/hive/Dockerfile` adds Connector/J, the `bdv-hive` wrapper, S3 classpath
links, writable runtime paths and UID/GID 10001. The Hadoop 3.1.0 AWS connector
and SDK bundle already came from the pinned vendor base; we did not replace
those with arbitrary new versions. Hive runs Java 8 and reports Hive 3.1.3.

```bash
cd bdv-helm/charts/bdv
export MYSQL_JDBC_JAR='/root/bdv-ranger-mysql/mysql-connector-j-9.7.0.jar'
export HIVE_DERIVATIVE_IMAGE='rstevens198238/mdaca-metastore:hive-3.1.3-mysql-9.7.0-capture-0.1.34'
bash images/hive/build.sh
```

### Connector/J artifact

- File: `mysql-connector-j-9.7.0.jar`.
- SHA-256: `0353648eaa1c91e0f4020c959abf756bc866ffd583df22ae6b6f6e0cbd43eb44`.
- Class: `com/mysql/cj/jdbc/Driver.class`.
- Observed class-file major version: 52, compatible with Java 8 bytecode.
- The actual Java 8 runtime loaded the class and reported no main method.
  That was a class-loading check, not a database connection test.
- Later schema initialization and validation verified actual JDBC connectivity.

The JAR is an external build input, excluded from Git. Obtain the exact artifact
through your approved distribution source, or retain the inspected copy from
the bastion. This kit verifies the recorded hash, class and bytecode; it does
not claim a newly verified vendor signature. Historical inspection noted an
expired Oracle Maven signing key, so preserve that distinction in provenance.

```bash
python3 bdv-helm/charts/bdv/scripts/verify-jdbc.py \
  /root/bdv-ranger-mysql/mysql-connector-j-9.7.0.jar
```

### MinIO initializer

`images/minio-init/Dockerfile`, `minio-init.sh` and `build.sh` retain the bucket,
warehouse identity and scoped policy setup on the pinned vendor MinIO base.
The runtime MinIO image itself was not rebuilt from source in this session.

## Build all available derivatives

From the repository root:

```bash
bash bdv-helm/charts/bdv/scripts/build-images.sh --plan

export MYSQL_JDBC_JAR='/root/bdv-ranger-mysql/mysql-connector-j-9.7.0.jar'
export IMAGE_NAMESPACE='rstevens198238'
export IMAGE_TAG='capture-0.1.34'
bash bdv-helm/charts/bdv/scripts/build-images.sh --build
```

Build order: new Ranger JDBC tag, new Ranger runtime tag based on it, new Hive
derivative tag, new MinIO initializer tag. Builds target linux/amd64 and run
image contract checks. Docker and registry access are required. These new build
scripts have local syntax/command-flow checks, but candidate image builds have
not been run in the packaging environment because Docker is unavailable there.

Push each reviewed candidate separately with docker push, inspect its RepoDigest,
and record the new immutable digest in site values only after testing it.
The chart's accepted image digests are not changed by these scripts. The new
candidate tags need not reproduce the old image digest byte for byte.

## Preserve the actual accepted images

`images/image-lock.json` records the accepted runtime digests and source coverage.
On the bastion, capture metadata privately outside the checkout:

```bash
python3 bdv-helm/charts/bdv/scripts/capture-images.py \
  --output /root/bdv-image-capture-20261019
```

Use a different new output directory if it already exists. Add `--pull` to
explicitly pull pinned images. Add `--save` to archive the actual image binaries
with per-file checksums; this can require many GB. A binary archive can preserve
an exact accepted image even when its original Dockerfile is unavailable.
Do not commit image tarballs, JARs or private operational captures to Git.

The capture excludes Config.Env, raw build history, container logs and Kubernetes
Secrets. It records image IDs, RepoDigests, current RepoTags, platform, layers
and selected OCI source/revision/version labels. This is provenance evidence,
not a substitute for missing original product source.

## Source gaps retained explicitly

| Image | What is available | What is still needed for a complete source rebuild |
| --- | --- | --- |
| Ranger | Both derivative Dockerfiles, initializer, inspected launcher, pinned vendor base | Original vendor base build context/source |
| Hive | Derivative Dockerfile, wrapper, JDBC checksum, pinned vendor base | Original vendor base build context/source |
| MinIO initializer | Dockerfile and initialization script | Original vendor MinIO base source/build context |
| Bootstrap | The actual provision.py and chart bootstrap entrypoint | Original bootstrap Dockerfile and package/version build inputs |
| BDV/Trino | Pinned custom image, plugin inspection, complete runtime configuration | Original custom Trino Dockerfile, source revision and packaging context |
| MinIO runtime | Pinned vendor image | Original vendor Dockerfile and build context |

The base-image gaps do not prevent use of the pinned images. They do prevent a
claim that every product can already be rebuilt completely from source. Preserve
those vendor build inputs when they become available rather than creating a
replacement recipe and labeling it as the historical original.
