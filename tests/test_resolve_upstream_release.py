from pathlib import Path
import importlib.util
import json
import tempfile
import unittest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "tools" / "resolve_upstream_release.py"
spec = importlib.util.spec_from_file_location("resolve_upstream_release", SCRIPT_PATH)
resolver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resolver)


def release_fixture(*assets: dict[str, str]) -> dict[str, object]:
    return {
        "html_url": "https://github.com/getsentry/sentry-godot/releases/tag/1.6.0",
        "assets": list(assets),
    }


class ResolveUpstreamReleaseTests(unittest.TestCase):
    def test_resolve_ignores_demo_asset_and_derives_output_name(self):
        release = release_fixture(
            {
                "name": "sentry-godot-demo-project-1.6.0+4e3e3e5.zip",
                "browser_download_url": "https://example.test/demo.zip",
            },
            {
                "name": "sentry-godot-1.6.0+4e3e3e5.zip",
                "browser_download_url": "https://example.test/addon.zip",
            },
        )

        values = resolver.resolve_release(release, "1.6.0")

        self.assertEqual("1.6.0", values["UPSTREAM_TAG"])
        self.assertEqual("sentry-godot-1.6.0+4e3e3e5.zip", values["ASSET_NAME"])
        self.assertEqual("https://example.test/addon.zip", values["DOWNLOAD_URL"])
        self.assertEqual("sentry-godot-1.6.0+4e3e3e5-mobile.zip", values["OUTPUT_NAME"])
        self.assertEqual(
            "https://github.com/getsentry/sentry-godot/releases/tag/1.6.0",
            values["UPSTREAM_RELEASE_URL"],
        )

    def test_resolve_rejects_missing_addon_asset(self):
        release = release_fixture(
            {
                "name": "sentry-godot-demo-project-1.6.0+4e3e3e5.zip",
                "browser_download_url": "https://example.test/demo.zip",
            }
        )

        with self.assertRaisesRegex(resolver.ResolveError, "Expected exactly one addon asset"):
            resolver.resolve_release(release, "1.6.0")

    def test_resolve_rejects_multiple_addon_assets(self):
        release = release_fixture(
            {
                "name": "sentry-godot-1.6.0+4e3e3e5.zip",
                "browser_download_url": "https://example.test/addon-a.zip",
            },
            {
                "name": "sentry-godot-1.6.0+abcdef0.zip",
                "browser_download_url": "https://example.test/addon-b.zip",
            },
        )

        with self.assertRaisesRegex(resolver.ResolveError, "found 2"):
            resolver.resolve_release(release, "1.6.0")

    def test_write_github_env_writes_expected_assignments(self):
        values = {
            "UPSTREAM_TAG": "1.6.0",
            "UPSTREAM_RELEASE_URL": "https://example.test/release",
            "ASSET_NAME": "sentry-godot-1.6.0+4e3e3e5.zip",
            "DOWNLOAD_URL": "https://example.test/addon.zip",
            "OUTPUT_NAME": "sentry-godot-1.6.0+4e3e3e5-mobile.zip",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / "github-env"

            resolver.write_github_env(values, env_path)

            self.assertEqual(
                [
                    "UPSTREAM_TAG=1.6.0",
                    "UPSTREAM_RELEASE_URL=https://example.test/release",
                    "ASSET_NAME=sentry-godot-1.6.0+4e3e3e5.zip",
                    "DOWNLOAD_URL=https://example.test/addon.zip",
                    "OUTPUT_NAME=sentry-godot-1.6.0+4e3e3e5-mobile.zip",
                ],
                env_path.read_text(encoding="utf-8").splitlines(),
            )

    def test_main_resolves_release_json_to_github_env(self):
        release = release_fixture(
            {
                "name": "sentry-godot-1.6.0+4e3e3e5.zip",
                "browser_download_url": "https://example.test/addon.zip",
            }
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            release_json = temp_path / "release.json"
            github_env = temp_path / "github-env"
            release_json.write_text(json.dumps(release), encoding="utf-8")

            exit_code = resolver.main(
                [
                    "--release-json",
                    str(release_json),
                    "--version",
                    "1.6.0",
                    "--github-env",
                    str(github_env),
                ]
            )

            self.assertEqual(0, exit_code)
            self.assertIn(
                "OUTPUT_NAME=sentry-godot-1.6.0+4e3e3e5-mobile.zip",
                github_env.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
