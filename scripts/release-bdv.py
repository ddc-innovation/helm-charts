#!/usr/bin/env python3
"""Build the next BDV minor release into an existing Helm repository directory."""
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
    match = re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', str(value))
    if not match:
        raise ValueError(f'Expected a stable major.minor.patch chart version: {value!r}')
    return tuple(map(int, match.groups()))


def next_version(source_version, entries, bump="minor"):
    versions = [semver(source_version)] + [semver(item['version']) for item in entries]
    major, minor, patch = max(versions)
    return f'{major}.{minor + 1}.0' if bump == 'minor' else f'{major}.{minor}.{patch + 1}'


def chart_fingerprint(chart):
    # Called before dependency resolution creates files in charts/.
    # Only source tracked by Git participates, not untracked build output.
    root = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], cwd=chart, text=True).strip())
    paths = subprocess.check_output(['git', 'ls-files', '-z', '--', str(chart.resolve().relative_to(root))], cwd=root).split(b'\0')
    digest = hashlib.sha256()
    for relative in sorted(p for p in paths if p):
        path = root / relative.decode()
        if path.is_symlink():
            raise ValueError('Chart source must not contain symlinks')
        digest.update(relative + b'\0' + path.read_bytes() + b'\0')
    return digest.hexdigest()


def run(*args, quiet=False):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL if quiet else None)


def release(chart, site, url, helm, source_sha, bump="minor"):
    chart = Path(chart).resolve()
    site = Path(site).resolve()
    if chart == site or chart in site.parents or site in chart.parents:
        raise ValueError('Source chart and publishing directory must be separate')
    site.mkdir(parents=True, exist_ok=True)
    metadata = yaml.safe_load((chart / 'Chart.yaml').read_text())
    if metadata.get('name') != 'bdv':
        raise ValueError('Only the bdv chart is supported by this publisher')
    if semver(metadata['version']) < (0, 1, 34):
        raise ValueError('Import the current local BDV chart 0.1.34 or newer into charts/bdv before publishing; GitHub still contains an older chart')
    index_path = site / 'index.yaml'
    index = yaml.safe_load(index_path.read_text()) if index_path.exists() else {}
    entries = (index or {}).get('entries', {}).get('bdv', [])
    marker_path = site / '.bdv-release.json'
    marker = json.loads(marker_path.read_text()) if marker_path.exists() else {}
    fingerprint = chart_fingerprint(chart)
    if marker.get('fingerprint') == fingerprint:
        version = marker['version']
        if not (site / f'bdv-{version}.tgz').is_file() or not any(e['version'] == version for e in entries):
            raise ValueError('Release marker does not match the retained package/index')
        print(f'BDV {version} already published for this chart source; reusing it.')
        return version
    version = next_version(metadata['version'], entries, bump)
    package = site / f'bdv-{version}.tgz'
    if package.exists():
        raise ValueError(f'Refusing to replace existing package {package.name}')
    # Work on an isolated copy; main/Chart.yaml and appVersion are not modified.
    with tempfile.TemporaryDirectory(prefix='bdv-release-') as temporary:
        work = Path(temporary) / 'bdv'
        shutil.copytree(chart, work)
        chart_file = work / 'Chart.yaml'
        updated, count = re.subn(r'^version:[^\n]*$', f'version: {version}', chart_file.read_text(), flags=re.MULTILINE)
        if count != 1:
            raise ValueError('Chart.yaml must contain exactly one top-level version')
        chart_file.write_text(updated)
        if metadata.get('dependencies'):
            run(helm, 'dependency', 'build' if (work / 'Chart.lock').exists() else 'update', str(work))
        run(helm, 'lint', str(work))
        run(helm, 'template', 'bdv', str(work), '--namespace', 'bdv', quiet=True)
        output = Path(temporary) / 'packages'
        output.mkdir()
        run(helm, 'package', str(work), '--destination', str(output))
        shutil.copy2(output / package.name, package)
        args = [helm, 'repo', 'index', str(site), '--url', url.rstrip('/')]
        if index_path.exists():
            previous = Path(temporary) / 'previous-index.yaml'
            shutil.copy2(index_path, previous)
            args.extend(['--merge', str(previous)])
        run(*args)
    (site / '.nojekyll').touch()
    marker_path.write_text(json.dumps({'version': version, 'fingerprint': fingerprint, 'source_sha': source_sha}, indent=2) + '\n')
    print(f'Prepared BDV {version}; previous chart packages and index entries retained.')
    return version


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--chart', default='charts/bdv')
    parser.add_argument('--site', required=True)
    parser.add_argument('--url', required=True)
    parser.add_argument('--helm', default='helm')
    parser.add_argument('--source-sha', default='local')
    parser.add_argument('--bump', choices=['minor', 'patch'], default='minor')
    args = parser.parse_args()
    if not args.url.startswith('https://'):
        parser.error('Repository URL must use HTTPS')
    release(args.chart, args.site, args.url, args.helm, args.source_sha, args.bump)


if __name__ == '__main__':
    main()
