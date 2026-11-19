import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts/release-charts.py"
SPEC = importlib.util.spec_from_file_location("release_charts", SCRIPT)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_first_release_uses_source_version(self):
        self.assertEqual(release.next_version("0.1.0", [], "patch"), "0.1.0")

    def test_patch_release(self):
        self.assertEqual(
            release.next_version("0.1.0", [{"version": "0.1.0"}], "patch"),
            "0.1.1",
        )

    def test_minor_release_preserves_existing_bdv_behavior(self):
        self.assertEqual(
            release.next_version("0.1.35", [{"version": "0.1.0"}], "minor"),
            "0.2.0",
        )

    def test_rejects_non_stable_versions(self):
        for value in ["0.1", "0.1.0-dev", "01.2.3", "latest"]:
            with self.assertRaises(ValueError):
                release.semver(value)


if __name__ == "__main__":
    unittest.main()
