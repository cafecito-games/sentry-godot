from pathlib import Path
import importlib.util
import json
import tempfile
import unittest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "tools" / "resolve_upstream_release.py"
spec = importlib.util.spec_from_file_location("resolve_upstream_release", SCRIPT_PATH)
resolver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resolver)


def release_fixture(
    *assets: dict[str, str],
    tag_name: str = "1.6.0",
    published_at: str = "2026-04-21T07:48:54Z",
    draft: bool = False,
    prerelease: bool = False,
) -> dict[str, object]:
    return {
        "tag_name": tag_name,
        "html_url": f"https://github.com/getsentry/sentry-godot/releases/tag/{tag_name}",
        "published_at": published_at,
        "draft": draft,
        "prerelease": prerelease,
        "assets": list(assets),
    }


def addon_asset(version: str, short_sha: str = "4e3e3e5") -> dict[str, str]:
    return {
        "name": f"sentry-godot-{version}+{short_sha}.zip",
        "browser_download_url": f"https://example.test/sentry-godot-{version}+{short_sha}.zip",
    }


def demo_asset(version: str, short_sha: str = "4e3e3e5") -> dict[str, str]:
    return {
        "name": f"sentry-godot-demo-project-{version}+{short_sha}.zip",
        "browser_download_url": f"https://example.test/sentry-godot-demo-project-{version}+{short_sha}.zip",
    }


class ResolveUpstreamReleaseTests(unittest.TestCase):
    def test_resolve_ignores_demo_asset_and_derives_output_name(self):
        release = release_fixture(
            demo_asset("1.6.0"),
            addon_asset("1.6.0"),
        )

        values = resolver.resolve_release(release, "1.6.0")

        self.assertEqual("true", values["SHOULD_RELEASE"])
        self.assertEqual("1.6.0", values["UPSTREAM_TAG"])
        self.assertEqual("sentry-godot-1.6.0+4e3e3e5.zip", values["ASSET_NAME"])
        self.assertEqual("https://example.test/sentry-godot-1.6.0+4e3e3e5.zip", values["DOWNLOAD_URL"])
        self.assertNotIn("OUTPUT_NAME", values)
        self.assertEqual(
            "https://github.com/getsentry/sentry-godot/releases/tag/1.6.0",
            values["UPSTREAM_RELEASE_URL"],
        )

    def test_resolve_rejects_missing_addon_asset(self):
        release = release_fixture(
            demo_asset("1.6.0")
        )

        with self.assertRaisesRegex(resolver.ResolveError, "Expected exactly one addon asset"):
            resolver.resolve_release(release, "1.6.0")

    def test_resolve_rejects_multiple_addon_assets(self):
        release = release_fixture(
            addon_asset("1.6.0", "4e3e3e5"),
            addon_asset("1.6.0", "abcdef0"),
        )

        with self.assertRaisesRegex(resolver.ResolveError, "found 2"):
            resolver.resolve_release(release, "1.6.0")

    def test_write_github_env_writes_expected_assignments(self):
        values = {
            "SHOULD_RELEASE": "true",
            "UPSTREAM_TAG": "1.6.0",
            "UPSTREAM_RELEASE_URL": "https://example.test/release",
            "ASSET_NAME": "sentry-godot-1.6.0+4e3e3e5.zip",
            "DOWNLOAD_URL": "https://example.test/addon.zip",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / "github-env"

            resolver.write_github_env(values, env_path)

            self.assertEqual(
                [
                    "SHOULD_RELEASE=true",
                    "UPSTREAM_TAG=1.6.0",
                    "UPSTREAM_RELEASE_URL=https://example.test/release",
                    "ASSET_NAME=sentry-godot-1.6.0+4e3e3e5.zip",
                    "DOWNLOAD_URL=https://example.test/addon.zip",
                ],
                env_path.read_text(encoding="utf-8").splitlines(),
            )

    def test_main_resolves_release_json_to_github_env(self):
        release = release_fixture(
            addon_asset("1.6.0")
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
                "ASSET_NAME=sentry-godot-1.6.0+4e3e3e5.zip",
                github_env.read_text(encoding="utf-8"),
            )

    def test_resolve_next_release_picks_newest_missing_supported_release(self):
        upstream_releases = [
            release_fixture(
                addon_asset("1.7.0"),
                tag_name="1.7.0",
                published_at="2026-05-01T00:00:00Z",
                prerelease=True,
            ),
            release_fixture(
                addon_asset("1.6.0"),
                tag_name="1.6.0",
                published_at="2026-04-21T07:48:54Z",
            ),
            release_fixture(
                addon_asset("1.5.0"),
                tag_name="1.5.0",
                published_at="2026-02-01T00:00:00Z",
            ),
        ]
        local_releases = [{"tagName": "1.5.0"}]

        values = resolver.resolve_next_release(upstream_releases, local_releases)

        self.assertEqual("true", values["SHOULD_RELEASE"])
        self.assertEqual("1.6.0", values["UPSTREAM_TAG"])
        self.assertEqual("sentry-godot-1.6.0+4e3e3e5.zip", values["ASSET_NAME"])

    def test_resolve_next_release_skips_releases_without_addon_asset(self):
        upstream_releases = [
            release_fixture(
                demo_asset("1.7.0"),
                tag_name="1.7.0",
                published_at="2026-05-01T00:00:00Z",
            ),
            release_fixture(
                addon_asset("1.6.0"),
                tag_name="1.6.0",
                published_at="2026-04-21T07:48:54Z",
            ),
        ]

        values = resolver.resolve_next_release(upstream_releases, [])

        self.assertEqual("1.6.0", values["UPSTREAM_TAG"])

    def test_resolve_next_release_returns_false_when_nothing_new(self):
        upstream_releases = [
            release_fixture(
                addon_asset("1.6.0"),
                tag_name="1.6.0",
                published_at="2026-04-21T07:48:54Z",
            )
        ]
        local_releases = [{"tagName": "1.6.0"}]

        values = resolver.resolve_next_release(upstream_releases, local_releases)

        self.assertEqual({"SHOULD_RELEASE": "false"}, values)

    def test_main_auto_mode_writes_false_when_already_synced(self):
        upstream_releases = [
            release_fixture(
                addon_asset("1.6.0"),
                tag_name="1.6.0",
                published_at="2026-04-21T07:48:54Z",
            )
        ]
        local_releases = [{"tagName": "1.6.0"}]

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            upstream_json = temp_path / "upstream-releases.json"
            local_json = temp_path / "local-releases.json"
            github_env = temp_path / "github-env"
            upstream_json.write_text(json.dumps([upstream_releases]), encoding="utf-8")
            local_json.write_text(json.dumps(local_releases), encoding="utf-8")

            exit_code = resolver.main(
                [
                    "--upstream-releases-json",
                    str(upstream_json),
                    "--local-releases-json",
                    str(local_json),
                    "--github-env",
                    str(github_env),
                ]
            )

            self.assertEqual(0, exit_code)
            self.assertEqual("SHOULD_RELEASE=false\n", github_env.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
