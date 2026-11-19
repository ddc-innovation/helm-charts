import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts/release-postfix-relay.py"
SPEC = importlib.util.spec_from_file_location("release_postfix_relay", SCRIPT)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_initial_release_uses_source_version(self):
        self.assertEqual(release.release_version("0.1.0", []), "0.1.0")

    def test_changed_source_increments_published_patch(self):
        self.assertEqual(
            release.release_version("0.1.0", [{"version": "0.1.0"}]),
            "0.1.1",
        )

    def test_higher_source_version_is_preserved(self):
        self.assertEqual(
            release.release_version("0.2.0", [{"version": "0.1.9"}]),
            "0.2.0",
        )

    def test_rejects_non_stable_versions(self):
        for value in ["0.1", "0.1.0-dev", "01.2.3", "latest"]:
            with self.assertRaises(ValueError):
                release.semver(value)


if __name__ == "__main__":
    unittest.main()
