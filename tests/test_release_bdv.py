import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('release', Path(__file__).parents[1] / 'scripts/release-bdv.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_minor_increment_resets_patch(self):
        self.assertEqual(release.next_version('0.1.35', [{'version': '0.1.0'}]), '0.2.0')

    def test_patch_bump_from_current_local_baseline(self):
        self.assertEqual(release.next_version("0.1.34", [{"version": "0.1.0"}], "patch"), "0.1.35")

    def test_published_version_wins_over_source(self):
        self.assertEqual(release.next_version('0.1.0', [{'version': '0.2.0'}, {'version': '0.1.0'}]), '0.3.0')

    def test_source_major_is_respected(self):
        self.assertEqual(release.next_version('1.0.0', [{'version': '0.9.0'}]), '1.1.0')

    def test_rejects_ambiguous_versions(self):
        for value in ['0.1', '0.1.0-dev', '01.2.3', 'latest']:
            with self.assertRaises(ValueError):
                release.semver(value)

    def test_rejects_older_github_source_before_packaging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chart = root / 'chart'
            chart.mkdir()
            (chart / 'Chart.yaml').write_text('name: bdv\nversion: 0.1.0\n')
            with self.assertRaisesRegex(ValueError, '0.1.34'):
                release.release(chart, root / 'site', 'https://example.invalid/', 'helm', 'test')

    def test_fingerprint_ignores_untracked_files_but_tracks_source_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chart = root / 'charts/bdv'
            chart.mkdir(parents=True)
            (chart / 'Chart.yaml').write_text('name: bdv\nversion: 0.1.0\n')
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
            first = release.chart_fingerprint(chart)
            (chart / 'untracked.tmp').write_text('ignored')
            self.assertEqual(first, release.chart_fingerprint(chart))
            (chart / 'Chart.yaml').write_text('name: bdv\nversion: 0.1.1\n')
            self.assertNotEqual(first, release.chart_fingerprint(chart))


if __name__ == '__main__':
    unittest.main()
