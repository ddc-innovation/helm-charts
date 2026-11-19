#!/usr/bin/env python3
"""Publish an immutable Postfix relay chart release into a Helm repository."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml


CHART_NAME = "postfix-relay"


def semver(value):
    match = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", str(value))
    if not match:
        raise ValueError(f"Expected a stable major.minor.patch chart version: {value!r}")
    return tuple(map(int, match.groups()))


def release_version(source_version, entries):
    source = semver(source_version)
    published = [semver(item["version"]) for item in entries]
    if not published or source > max(published):
        return ".".join(map(str, source))
    major, minor, patch = max([source, *published])
    return f"{major}.{minor}.{patch + 1}"


def chart_fingerprint(chart):
    root = Path(
        subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], cwd=chart, text=True
        ).strip()
    )
    relative_chart = chart.resolve().relative_to(root)
    paths = subprocess.check_output(
        ["git", "ls-files", "-z", "--", str(relative_chart)], cwd=root
    ).split(b"\0")
    digest = hashlib.sha256()
    for relative in sorted(path for path in paths if path):
        source = root / relative.decode()
        if source.is_symlink():
            raise ValueError("Chart source must not contain symlinks")
        digest.update(relative + b"\0" + source.read_bytes() + b"\0")
    return digest.hexdigest()


def run(*args, quiet=False):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL if quiet else None)


def release(chart, site, url, helm, source_sha):
    chart = Path(chart).resolve()
    site = Path(site).resolve()
    if chart == site or chart in site.parents or site in chart.parents:
        raise ValueError("Source chart and publishing directory must be separate")
    site.mkdir(parents=True, exist_ok=True)

    metadata = yaml.safe_load((chart / "Chart.yaml").read_text(encoding="utf-8"))
    if metadata.get("name") != CHART_NAME:
        raise ValueError(f"Expected chart name {CHART_NAME!r}")

    index_path = site / "index.yaml"
    index = yaml.safe_load(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    entries = (index or {}).get("entries", {}).get(CHART_NAME, [])
    marker_path = site / f".{CHART_NAME}-release.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8")) if marker_path.exists() else {}
    fingerprint = chart_fingerprint(chart)

    if marker.get("fingerprint") == fingerprint:
        version = marker["version"]
        package = site / f"{CHART_NAME}-{version}.tgz"
        if not package.is_file() or not any(item["version"] == version for item in entries):
            raise ValueError("Release marker does not match the retained package/index")
        print(f"{CHART_NAME} {version} already published; reusing it.")
        return version

    version = release_version(metadata["version"], entries)
    package = site / f"{CHART_NAME}-{version}.tgz"
    if package.exists():
        raise ValueError(f"Refusing to replace existing package {package.name}")

    digest = "sha256:" + "a" * 64
    with tempfile.TemporaryDirectory(prefix=f"{CHART_NAME}-release-") as temporary:
        work = Path(temporary) / CHART_NAME
        shutil.copytree(chart, work)
        chart_file = work / "Chart.yaml"
        updated, count = re.subn(
            r"^version:[^\n]*$",
            f"version: {version}",
            chart_file.read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
        if count != 1:
            raise ValueError("Chart.yaml must contain exactly one top-level version")
        chart_file.write_text(updated, encoding="utf-8")

        run(helm, "lint", str(work), "--set-string", f"image.digest={digest}")
        run(
            helm,
            "template",
            CHART_NAME,
            str(work),
            "--namespace",
            "app-mail",
            "--set-string",
            f"image.digest={digest}",
            quiet=True,
        )
        output = Path(temporary) / "packages"
        output.mkdir()
        run(helm, "package", str(work), "--destination", str(output))
        shutil.copy2(output / package.name, package)

        args = [helm, "repo", "index", str(site), "--url", url.rstrip("/")]
        if index_path.exists():
            previous = Path(temporary) / "previous-index.yaml"
            shutil.copy2(index_path, previous)
            args.extend(["--merge", str(previous)])
        run(*args)

    (site / ".nojekyll").touch()
    marker_path.write_text(
        json.dumps(
            {"version": version, "fingerprint": fingerprint, "source_sha": source_sha},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Prepared {CHART_NAME} {version}; retained previous packages and index entries.")
    return version


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chart", default="charts/postfix-relay")
    parser.add_argument("--site", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--helm", default="helm")
    parser.add_argument("--source-sha", default="local")
    args = parser.parse_args()
    if not args.url.startswith("https://"):
        parser.error("Repository URL must use HTTPS")
    release(args.chart, args.site, args.url, args.helm, args.source_sha)


if __name__ == "__main__":
    main()
