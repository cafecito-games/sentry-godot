from pathlib import Path
import hashlib
import importlib.util
import tempfile
import unittest
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPOSITORY_ROOT / "tools" / "verify_gpm_cut.py"
spec = importlib.util.spec_from_file_location("verify_gpm_cut", SCRIPT_PATH)
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


VERSION = "2.3.0"
PLATFORMS = ("linux.x86_64", "macos")

CORE_FILES = {
    "sentry.gdextension": "[configuration]\n",
    "shared.gd": "shared",
}
ANDROID_PLUGIN = {"bin/android/sentry_android_godot_plugin.release.aar": "android plugin"}
LINUX_FILES = {
    "bin/linux/x86_64/libsentry.linux.release.x86_64.so": "linux",
    "bin/linux/x86_64/crashpad_handler": "linux dependency",
}
MACOS_FILES = {
    "bin/macos/libsentry.macos.release.dylib": "macos",
    "bin/macos/libSentry.dylib": "macos dependency",
}


def write_slice(distribution_dir: Path, name: str, files: dict[str, str]) -> dict[str, object]:
    archive_path = distribution_dir / f"sentry-{VERSION}-{name}.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for path, contents in sorted(files.items()):
            archive.writestr(path, contents)

    contents = archive_path.read_bytes()
    return {
        "file": archive_path.name,
        "sha256": hashlib.sha256(contents).hexdigest(),
        "size": len(contents),
    }


def render_index(slices: dict[str, dict[str, object]], extra_by_slice: dict[str, str] | None = None) -> str:
    extra_by_slice = extra_by_slice or {}
    lines = ["format = 1", 'name = "sentry"', f'version = "{VERSION}"', "", "[slices]"]
    for name, slice_table in sorted(slices.items()):
        lines.append(f'  [slices."{name}"]')
        lines.append(f'    file = "{slice_table["file"]}"')
        lines.append(f'    sha256 = "{slice_table["sha256"]}"')
        lines.append(f'    size = {slice_table["size"]}')
        if name in extra_by_slice:
            lines.append(extra_by_slice[name])
    return "\n".join(lines) + "\n"


def write_cut(
    distribution_dir: Path,
    core_files: dict[str, str] | None = None,
    linux_files: dict[str, str] | None = None,
    macos_files: dict[str, str] | None = None,
    extra_by_slice: dict[str, str] | None = None,
) -> None:
    slices = {
        "core": write_slice(distribution_dir, "core", CORE_FILES if core_files is None else core_files),
        "linux.x86_64": write_slice(
            distribution_dir, "linux.x86_64", LINUX_FILES if linux_files is None else linux_files
        ),
        "macos": write_slice(distribution_dir, "macos", MACOS_FILES if macos_files is None else macos_files),
    }
    linux_libraries = "\n".join(
        [
            '    [slices."linux.x86_64".libraries]',
            '      [slices."linux.x86_64".libraries."sentry.gdextension"]',
            '        "linux.release.x86_64" = '
            '"res://addons/sentry/bin/linux/x86_64/libsentry.linux.release.x86_64.so"',
            '    [slices."linux.x86_64".dependencies]',
            '      [slices."linux.x86_64".dependencies."sentry.gdextension"]',
            '        [slices."linux.x86_64".dependencies."sentry.gdextension"."linux.x86_64"]',
            '          "res://addons/sentry/bin/linux/x86_64/crashpad_handler" = ""',
        ]
    )
    merged_extra = {"linux.x86_64": linux_libraries}
    merged_extra.update(extra_by_slice or {})
    (distribution_dir / "gpm-index.toml").write_text(render_index(slices, merged_extra), encoding="utf-8")


class VerifyCutTests(unittest.TestCase):
    def test_accepts_a_consistent_cut(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            distribution_dir = Path(temp_dir)
            write_cut(distribution_dir)

            verify.verify_cut(distribution_dir, VERSION, PLATFORMS)

    def test_rejects_a_missing_slice(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            distribution_dir = Path(temp_dir)
            write_cut(distribution_dir)

            with self.assertRaisesRegex(verify.VerifyError, "missing expected slices: windows.x86_64"):
                verify.verify_cut(distribution_dir, VERSION, (*PLATFORMS, "windows.x86_64"))

    def test_rejects_an_unexpected_slice(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            distribution_dir = Path(temp_dir)
            write_cut(distribution_dir)

            with self.assertRaisesRegex(verify.VerifyError, "unexpected slices: macos"):
                verify.verify_cut(distribution_dir, VERSION, ("linux.x86_64",))

    def test_rejects_a_version_the_cut_was_not_packaged_with(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            distribution_dir = Path(temp_dir)
            write_cut(distribution_dir)

            with self.assertRaisesRegex(verify.VerifyError, "Index version"):
                verify.verify_cut(distribution_dir, "2.4.0", PLATFORMS)

    def test_rejects_a_checksum_that_does_not_match_the_archive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            distribution_dir = Path(temp_dir)
            write_cut(distribution_dir)
            archive_path = distribution_dir / f"sentry-{VERSION}-macos.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("bin/macos/libsentry.macos.release.dylib", "retagged")

            with self.assertRaisesRegex(verify.VerifyError, "sha256|size"):
                verify.verify_cut(distribution_dir, VERSION, PLATFORMS)

    def test_rejects_platform_binaries_in_core(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            distribution_dir = Path(temp_dir)
            write_cut(
                distribution_dir,
                core_files={**CORE_FILES, "bin/macos/libsentry.macos.release.dylib": "leaked"},
            )

            with self.assertRaisesRegex(verify.VerifyError, "Core slice carries platform payload"):
                verify.verify_cut(distribution_dir, VERSION, PLATFORMS)

    def test_rejects_the_android_plugin_in_core(self):
        # The .aar files are claimed generically for android, so gpm fans them
        # into every ABI slice. Finding them in core means that rule stopped
        # matching and every consumer is paying for them.
        with tempfile.TemporaryDirectory() as temp_dir:
            distribution_dir = Path(temp_dir)
            write_cut(distribution_dir, core_files={**CORE_FILES, **ANDROID_PLUGIN})

            with self.assertRaisesRegex(verify.VerifyError, "Core slice carries platform payload"):
                verify.verify_cut(distribution_dir, VERSION, PLATFORMS)

    def test_rejects_web_support_files_in_core(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            distribution_dir = Path(temp_dir)
            write_cut(distribution_dir, core_files={**CORE_FILES, "web/sentry-bundle.js": "leaked"})

            with self.assertRaisesRegex(verify.VerifyError, "Core slice carries platform payload"):
                verify.verify_cut(distribution_dir, VERSION, PLATFORMS)

    def test_rejects_two_slices_shipping_the_same_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            distribution_dir = Path(temp_dir)
            write_cut(
                distribution_dir,
                macos_files={**MACOS_FILES, "bin/linux/x86_64/crashpad_handler": "duplicate"},
            )

            with self.assertRaisesRegex(verify.VerifyError, "all ship bin/linux/x86_64/crashpad_handler"):
                verify.verify_cut(distribution_dir, VERSION, PLATFORMS)

    def test_rejects_a_slice_declaring_a_resource_it_does_not_ship(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            distribution_dir = Path(temp_dir)
            write_cut(
                distribution_dir,
                linux_files={"bin/linux/x86_64/libsentry.linux.release.x86_64.so": "linux"},
            )

            with self.assertRaisesRegex(verify.VerifyError, "crashpad_handler but ships no such file"):
                verify.verify_cut(distribution_dir, VERSION, PLATFORMS)

    def test_resolves_a_bundle_directory_resource_through_its_contents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            distribution_dir = Path(temp_dir)
            ios_libraries = "\n".join(
                [
                    "    [slices.macos.libraries]",
                    '      [slices.macos.libraries."sentry.gdextension"]',
                    '        "macos.release" = "res://addons/sentry/bin/macos/Sentry.framework"',
                ]
            )
            write_cut(
                distribution_dir,
                macos_files={**MACOS_FILES, "bin/macos/Sentry.framework/Info.plist": "bundle"},
                extra_by_slice={"macos": ios_libraries},
            )

            verify.verify_cut(distribution_dir, VERSION, PLATFORMS)

    def test_rejects_a_missing_index(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(verify.VerifyError, "Missing index"):
                verify.verify_cut(Path(temp_dir), VERSION, PLATFORMS)


STAGED_GDEXTENSION = "\n".join(
    [
        "[libraries]",
        'linux.release.x86_64 = "res://addons/sentry/bin/linux/x86_64/libsentry.linux.release.x86_64.so"',
        'macos.release = "res://addons/sentry/bin/macos/libsentry.macos.release.dylib"',
        "",
        "[dependencies]",
        'linux.x86_64 = {"res://addons/sentry/bin/linux/x86_64/crashpad_handler": ""}',
        "",
    ]
)


def write_staged_gdextension(temp_dir: Path, text: str = STAGED_GDEXTENSION) -> Path:
    path = temp_dir / "sentry.gdextension"
    path.write_text(text, encoding="utf-8")
    return path


class ArtifactTests(unittest.TestCase):
    """Format 2 publishes a payload several slices need exactly once."""

    ANDROID_PLATFORMS = ("android.arm32", "android.arm64")
    AAR = "bin/android/sentry_android_godot_plugin.release.aar"

    def write_format_two_cut(
        self,
        distribution_dir: Path,
        referencing: tuple[str, ...] = ANDROID_PLATFORMS,
        artifact_files: dict[str, str] | None = None,
    ) -> None:
        tables = {
            "core": write_slice(distribution_dir, "core", CORE_FILES),
            "android.arm32": write_slice(
                distribution_dir, "android.arm32", {"bin/android/libsentry.android.release.arm32.so": "arm32"}
            ),
            "android.arm64": write_slice(
                distribution_dir, "android.arm64", {"bin/android/libsentry.android.release.arm64.so": "arm64"}
            ),
        }
        artifact = write_slice(
            distribution_dir, "shared-android", {self.AAR: "plugin"} if artifact_files is None else artifact_files
        )

        lines = ["format = 2", 'name = "sentry"', f'version = "{VERSION}"', "", "[artifacts]", "  [artifacts.android]"]
        lines.append(f'    file = "{artifact["file"]}"')
        lines.append(f'    sha256 = "{artifact["sha256"]}"')
        lines.append(f'    size = {artifact["size"]}')
        lines.append("")
        lines.append("[slices]")
        for name, table in sorted(tables.items()):
            lines.append(f'  [slices."{name}"]')
            lines.append(f'    file = "{table["file"]}"')
            lines.append(f'    sha256 = "{table["sha256"]}"')
            lines.append(f'    size = {table["size"]}')
            if name in referencing:
                lines.append('    artifacts = ["android"]')
        (distribution_dir / "gpm-index.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_accepts_a_consistent_format_two_cut(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            distribution_dir = Path(temp_dir)
            self.write_format_two_cut(distribution_dir)

            verify.verify_cut(distribution_dir, VERSION, self.ANDROID_PLATFORMS)

    def test_rejects_an_architecture_slice_that_does_not_reference_the_artifact(self):
        # That ABI would install without the Android plugin, which is the
        # failure the shared artifact exists to prevent.
        with tempfile.TemporaryDirectory() as temp_dir:
            distribution_dir = Path(temp_dir)
            self.write_format_two_cut(distribution_dir, referencing=("android.arm64",))

            with self.assertRaisesRegex(verify.VerifyError, "android.arm32 does not reference artifact android"):
                verify.verify_cut(distribution_dir, VERSION, self.ANDROID_PLATFORMS)

    def test_rejects_an_artifact_that_ships_nothing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            distribution_dir = Path(temp_dir)
            self.write_format_two_cut(distribution_dir, artifact_files={})

            with self.assertRaisesRegex(verify.VerifyError, "Artifact android ships no files"):
                verify.verify_cut(distribution_dir, VERSION, self.ANDROID_PLATFORMS)

    def test_rejects_an_artifact_checksum_that_does_not_match(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            distribution_dir = Path(temp_dir)
            self.write_format_two_cut(distribution_dir)
            archive_path = distribution_dir / f"sentry-{VERSION}-shared-android.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(self.AAR, "retagged")

            with self.assertRaisesRegex(verify.VerifyError, "Archive android declares (sha256|size)"):
                verify.verify_cut(distribution_dir, VERSION, self.ANDROID_PLATFORMS)

    def test_rejects_format_one_declaring_artifacts(self):
        with self.assertRaisesRegex(verify.VerifyError, "format 1 with .artifacts."):
            verify.index_artifacts({"format": 1, "artifacts": {"android": {}}})

    def test_rejects_format_two_declaring_no_artifacts(self):
        with self.assertRaisesRegex(verify.VerifyError, "format 2 but no .artifacts."):
            verify.index_artifacts({"format": 2})

    def test_rejects_an_artifact_only_one_slice_shares(self):
        # One slice means the payload belongs in that slice, and gpm cuts
        # format 1 for it.
        with self.assertRaisesRegex(verify.VerifyError, "fewer than two architecture slices"):
            verify.verify_artifact_references(
                {"android": {}}, {"android.arm64": {"artifacts": ["android"]}}
            )

    def test_rejects_a_slice_referencing_an_unpublished_artifact(self):
        with self.assertRaisesRegex(verify.VerifyError, "unpublished artifact 'ios'"):
            verify.verify_artifact_references(
                {},
                {"android.arm32": {"artifacts": ["ios"]}, "android.arm64": {"artifacts": ["ios"]}},
            )


class SharedPathTests(unittest.TestCase):
    """One path in the merged tree may come from one archive only.

    A payload several architecture slices need is published once as a shared
    artifact, so a cut never ships the same path twice and gpm refuses a tree
    where anything does.
    """

    AAR = "bin/android/sentry_android_godot_plugin.release.aar"

    def test_accepts_archives_that_share_no_path(self):
        verify.verify_no_shared_paths(
            {
                "android.arm64": {"bin/android/libsentry.android.release.arm64.so"},
                "shared:android": {self.AAR},
            }
        )

    def test_rejects_an_artifact_and_a_slice_sharing_a_path(self):
        # gpm reports this as "one path in the merged tree cannot come from two
        # archives", so the cut must not reach a consumer.
        with self.assertRaisesRegex(verify.VerifyError, "all ship"):
            verify.verify_no_shared_paths(
                {
                    "android.arm64": {self.AAR},
                    "shared:android": {self.AAR},
                }
            )

    def test_rejects_two_architecture_slices_sharing_a_path(self):
        with self.assertRaisesRegex(verify.VerifyError, "all ship"):
            verify.verify_no_shared_paths(
                {"android.arm64": {self.AAR}, "android.x86_64": {self.AAR}}
            )

    def test_rejects_two_spellings_of_one_path_on_a_case_insensitive_host(self):
        with self.assertRaisesRegex(verify.VerifyError, "all ship"):
            verify.verify_no_shared_paths(
                {
                    "macos": {"bin/macos/libSentry.dylib"},
                    "core": {"bin/macos/libsentry.dylib"},
                }
            )


class ExpectedPlatformsTests(unittest.TestCase):
    def test_derives_the_slice_set_from_the_staged_gdextension(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = write_staged_gdextension(Path(temp_dir))

            self.assertEqual({"linux.x86_64", "macos"}, verify.expected_platforms(path))

    def test_rejects_a_staged_gdextension_that_implies_no_slice(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = write_staged_gdextension(Path(temp_dir), "[configuration]\n")

            with self.assertRaisesRegex(verify.VerifyError, "implies no platform slice"):
                verify.expected_platforms(path)

    def test_reports_a_key_gpm_could_not_classify(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = write_staged_gdextension(
                Path(temp_dir),
                '[libraries]\nlinux.debug.hardfloat.arm32 = "res://addons/sentry/bin/linux/x.so"\n',
            )

            with self.assertRaisesRegex(verify.VerifyError, "hardfloat"):
                verify.expected_platforms(path)

    def test_rejects_a_missing_staged_gdextension(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(verify.VerifyError, "Missing staged"):
                verify.expected_platforms(Path(temp_dir) / "absent.gdextension")


class CliTests(unittest.TestCase):
    def test_main_verifies_a_cut(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            distribution_dir = Path(temp_dir) / "dist"
            distribution_dir.mkdir()
            write_cut(distribution_dir)
            staged_gdextension = write_staged_gdextension(Path(temp_dir))

            exit_code = verify.main(
                [
                    "--dist",
                    str(distribution_dir),
                    "--version",
                    VERSION,
                    "--staged-gdextension",
                    str(staged_gdextension),
                ]
            )

        self.assertEqual(0, exit_code)

    def test_main_reports_a_broken_cut(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            staged_gdextension = write_staged_gdextension(Path(temp_dir))

            exit_code = verify.main(
                [
                    "--dist",
                    temp_dir,
                    "--version",
                    VERSION,
                    "--staged-gdextension",
                    str(staged_gdextension),
                ]
            )

        self.assertEqual(1, exit_code)


if __name__ == "__main__":
    unittest.main()
