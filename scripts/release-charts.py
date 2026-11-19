#!/usr/bin/env python3
"""Validate and publish every configured chart into one retained Helm index."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml


def semver(value):
    match = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", str(value))
    if not match:
        raise ValueError(f"Expected a stable major.minor.patch chart version: {value!r}")
    return tuple(map(int, match.groups()))


def next_version(source_version, entries, bump):
    source = semver(source_version)
    published = [semver(item["version"]) for item in entries]
    if not published:
        return ".".join(map(str, source))
    major, minor, patch = max([source, *published])
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"Unsupported release bump: {bump!r}")


def run(*args, quiet=False, cwd=None):
    subprocess.run(
        args,
        check=True,
        cwd=cwd,
        stdout=subprocess.DEVNULL if quiet else None,
    )


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


def helm_set_args(settings):
    result = []
    for name, value in sorted((settings or {}).items()):
        result.extend(["--set-string", f"{name}={value}"])
    return result


def load_charts(config_path):
    config_path = Path(config_path).resolve()
    root = config_path.parents[1]
    document = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    charts = document.get("charts") or {}
    if not charts:
        raise ValueError("Release configuration must define at least one chart")

    resolved = []
    for name, settings in sorted(charts.items()):
        chart = (root / settings["path"]).resolve()
        if root not in chart.parents:
            raise ValueError(f"Chart {name!r} resolves outside the repository")
        metadata = yaml.safe_load((chart / "Chart.yaml").read_text(encoding="utf-8"))
        if metadata.get("name") != name:
            raise ValueError(f"Configured name {name!r} does not match Chart.yaml")
        minimum = settings.get("minimum_source_version")
        if minimum and semver(metadata["version"]) < semver(minimum):
            raise ValueError(f"{name} source version must be at least {minimum}")
        resolved.append((name, chart, metadata, settings))
    return root, resolved


def resolve_dependencies(chart, helm):
    metadata = yaml.safe_load((chart / "Chart.yaml").read_text(encoding="utf-8"))
    if metadata.get("dependencies"):
        command = "build" if (chart / "Chart.lock").exists() else "update"
        run(helm, "dependency", command, str(chart))


def validate_chart(root, name, chart, settings, helm):
    resolve_dependencies(chart, helm)
    extra = helm_set_args(settings.get("set"))
    run(helm, "lint", str(chart), *extra)
    run(
        helm,
        "template",
        name,
        str(chart),
        "--namespace",
        settings["namespace"],
        *extra,
        quiet=True,
    )
    for command in settings.get("tests", []):
        if not isinstance(command, list) or not command:
            raise ValueError(f"Invalid test command for {name}")
        run(*map(str, command), cwd=root)


def publish_chart(root, name, chart, metadata, settings, site, url, helm, source_sha):
    index_path = site / "index.yaml"
    index = yaml.safe_load(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    entries = (index or {}).get("entries", {}).get(name, [])
    marker_path = site / f".{name}-release.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8")) if marker_path.exists() else {}
    fingerprint = chart_fingerprint(chart)

    if marker.get("fingerprint") == fingerprint:
        version = marker["version"]
        if not (site / f"{name}-{version}.tgz").is_file() or not any(
            item["version"] == version for item in entries
        ):
            raise ValueError(f"{name} release marker does not match package/index")
        print(f"{name} {version} already published; reusing it.")
        return version

    version = next_version(metadata["version"], entries, settings["bump"])
    package = site / f"{name}-{version}.tgz"
    if package.exists():
        raise ValueError(f"Refusing to replace existing package {package.name}")

    with tempfile.TemporaryDirectory(prefix=f"{name}-release-") as temporary:
        work = Path(temporary) / name
        shutil.copytree(chart, work)
        chart_file = work / "Chart.yaml"
        updated, count = re.subn(
            r"^version:[^\n]*$",
            f"version: {version}",
            chart_file.read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
        if count != 1:
            raise ValueError(f"{name} Chart.yaml must contain one top-level version")
        chart_file.write_text(updated, encoding="utf-8")

        resolve_dependencies(work, helm)
        extra = helm_set_args(settings.get("set"))
        run(helm, "lint", str(work), *extra)
        run(
            helm,
            "template",
            name,
            str(work),
            "--namespace",
            settings["namespace"],
            *extra,
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

    marker_path.write_text(
        json.dumps(
            {"version": version, "fingerprint": fingerprint, "source_sha": source_sha},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Prepared {name} {version}; retained prior packages and index entries.")
    return version


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="charts/release-config.yml")
    parser.add_argument("--helm", default="helm")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--site")
    parser.add_argument("--url")
    parser.add_argument("--source-sha", default="local")
    args = parser.parse_args()

    root, charts = load_charts(args.config)
    if args.validate_only:
        for name, chart, _metadata, settings in charts:
            validate_chart(root, name, chart, settings, args.helm)
        return

    if not args.site or not args.url or not args.url.startswith("https://"):
        parser.error("Publishing requires --site and an HTTPS --url")
    site = Path(args.site).resolve()
    site.mkdir(parents=True, exist_ok=True)
    for name, chart, metadata, settings in charts:
        publish_chart(
            root,
            name,
            chart,
            metadata,
            settings,
            site,
            args.url,
            args.helm,
            args.source_sha,
        )
    (site / ".nojekyll").touch()


if __name__ == "__main__":
    main()
