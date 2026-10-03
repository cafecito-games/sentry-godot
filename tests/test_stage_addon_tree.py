from pathlib import Path
import importlib.util
import tempfile
import unittest
import zipfile


TESTS_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = TESTS_DIR.parent


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stage = load_module("stage_addon_tree", REPOSITORY_ROOT / "tools" / "stage_addon_tree.py")
fixture = load_module("upstream_fixture", TESTS_DIR / "upstream_fixture.py")

SAMPLE_GDEXTENSION = fixture.SAMPLE_GDEXTENSION
create_addon_zip = fixture.create_addon_zip
NOTHING_DROPPED: list[str] = []


def staged_names(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


def stage_fixture(
    temp_dir: Path,
    dropped_platforms: list[str] = NOTHING_DROPPED,
    **fixture_arguments,
) -> tuple[set[str], str, set[str]]:
    input_zip = temp_dir / "input.zip"
    output_dir = temp_dir / "staged"
    create_addon_zip(input_zip, **fixture_arguments)

    slices = stage.stage_addon_tree(input_zip, output_dir, dropped_platforms)

    gdextension = (output_dir / "addons" / "sentry" / "sentry.gdextension").read_text(encoding="utf-8")
    return staged_names(output_dir), gdextension, slices


class SliceIdentifierTests(unittest.TestCase):
    def test_only_the_architecture_survives_into_a_slice_id(self):
        self.assertEqual("macos", stage.slice_id("macos.debug"))
        self.assertEqual("macos", stage.slice_id("macos.template_release"))
        self.assertEqual("windows.x86_64", stage.slice_id("windows.x86_64.single.debug"))
        self.assertEqual("windows.x86_64", stage.slice_id("windows.x86_64.double.release"))
        self.assertEqual("ios", stage.slice_id("ios.simulator.release"))
        self.assertEqual("linux.arm64", stage.slice_id("linux.debug.arm64"))

    def test_threaded_web_keys_land_in_the_architecture_slice(self):
        # Threaded and non-threaded web libraries are one slice: "threads" is a
        # platform variant, so it no more splits web than "simulator" splits iOS.
        self.assertEqual("web.wasm32", stage.slice_id("web.debug.threads.wasm32"))
        self.assertEqual("web.wasm32", stage.slice_id("web.release.wasm32"))

    def test_rejects_a_component_gpm_cannot_classify(self):
        with self.assertRaisesRegex(stage.StageError, "hardfloat"):
            stage.slice_id("linux.debug.hardfloat.arm32")

    def test_rejects_a_key_naming_one_axis_twice(self):
        with self.assertRaisesRegex(stage.StageError, "twice"):
            stage.slice_id("linux.x86_64.arm64")

    def test_rejects_a_key_that_does_not_start_with_a_platform(self):
        with self.assertRaisesRegex(stage.StageError, "Not a Godot platform"):
            stage.slice_id("visionos.debug.arm64")


class PublishedSlicesTests(unittest.TestCase):
    def test_a_platform_with_architectures_publishes_no_generic_slice(self):
        # gpm folds a platform's generic entries into each of its architecture
        # slices, because a host installs the first slice of its platform it
        # finds and would otherwise lose whichever half it did not match.
        text = "\n".join(
            [
                "[libraries]",
                'macos.editor = "res://addons/sentry/bin/macos/editor.dylib"',
                'macos.template_release.universal = "res://addons/sentry/bin/macos/release.dylib"',
            ]
        )

        self.assertEqual({"macos.universal"}, stage.published_slices(text))

    def test_a_platform_without_architectures_publishes_a_generic_slice(self):
        text = "\n".join(
            [
                "[libraries]",
                'macos.debug = "res://addons/sentry/bin/macos/debug.dylib"',
                'macos.release = "res://addons/sentry/bin/macos/release.dylib"',
            ]
        )

        self.assertEqual({"macos"}, stage.published_slices(text))


class GDExtensionFilteringTests(unittest.TestCase):
    def test_keeps_every_platform_that_is_not_dropped(self):
        filtered = stage.filter_gdextension_text(SAMPLE_GDEXTENSION, NOTHING_DROPPED)

        self.assertIn("[configuration]", filtered)
        self.assertIn('entry_symbol = "sentry_gdextension_init"', filtered)
        self.assertIn("macos.debug", filtered)
        self.assertIn("android.release.arm32", filtered)
        self.assertIn("android.release.arm64", filtered)
        self.assertIn("ios.release", filtered)
        self.assertIn("linux.debug.x86_32", filtered)
        self.assertIn("windows.release.arm64", filtered)
        self.assertIn("[dependencies]", filtered)
        self.assertIn("res://addons/sentry/bin/ios/Sentry.xcframework", filtered)
        self.assertIn("res://addons/sentry/bin/windows/arm64/crashpad_wer.dll", filtered)

    def test_keeps_both_halves_of_a_threaded_web_platform(self):
        filtered = stage.filter_gdextension_text(SAMPLE_GDEXTENSION, NOTHING_DROPPED)

        self.assertIn("web.debug.threads.wasm32", filtered)
        self.assertIn("web.debug.wasm32", filtered)

    def test_removes_the_entries_of_a_dropped_platform(self):
        filtered = stage.filter_gdextension_text(SAMPLE_GDEXTENSION, ["web"])

        self.assertNotIn("threads", filtered)
        self.assertNotIn("wasm32", filtered)
        self.assertNotIn("web", filtered.lower())
        self.assertIn("macos", filtered)

    def test_removes_noop_entries_whichever_platform_they_are_keyed_under(self):
        # The noop libraries exist upstream to keep Godot quiet on platforms
        # Sentry does not support. Publishing bin/noop as a linux.rv64 slice
        # would claim support that is not there.
        filtered = stage.filter_gdextension_text(SAMPLE_GDEXTENSION, NOTHING_DROPPED)

        self.assertIn("linux.debug.x86_64", filtered)
        self.assertNotIn("linux.debug.rv64", filtered)
        self.assertNotIn("noop", filtered.lower())

    def test_rejects_an_unknown_platform_in_the_drop_list(self):
        with self.assertRaisesRegex(stage.StageError, "Not a Godot platform: visionos"):
            stage.filter_gdextension_text(SAMPLE_GDEXTENSION, ["visionos"])

    def test_rejects_dropping_every_platform(self):
        with self.assertRaisesRegex(stage.StageError, "no platform slice"):
            stage.filter_gdextension_text(
                SAMPLE_GDEXTENSION,
                ["android", "ios", "linux", "macos", "web", "windows"],
            )


class StagingTests(unittest.TestCase):
    def test_publishes_every_upstream_platform_except_the_dropped_ones(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            names, gdextension, slices = stage_fixture(Path(temp_dir))

        self.assertEqual(
            {
                "android.arm32",
                "android.arm64",
                "android.x86_32",
                "android.x86_64",
                "ios",
                "linux.arm64",
                "linux.x86_32",
                "linux.x86_64",
                "macos",
                "web.wasm32",
                "windows.arm64",
                "windows.x86_32",
                "windows.x86_64",
            },
            slices,
        )
        self.assertIn("addons/sentry/shared.gd", names)
        self.assertIn("addons/sentry/feedback/user_feedback.gd", names)
        self.assertIn("addons/sentry/dotnet/lib/Sentry.Godot.dll", names)
        self.assertIn("addons/sentry/bin/android/libsentry.android.debug.arm32.so", names)
        self.assertIn("addons/sentry/bin/android/sentry_android_godot_plugin.release.aar", names)
        self.assertIn("addons/sentry/bin/windows/arm64/libsentry.windows.debug.arm64.dll", names)
        self.assertIn("addons/sentry/bin/linux/x86_32/crashpad_handler", names)
        self.assertIn("addons/sentry/bin/ios/Sentry.xcframework/Info.plist", names)
        self.assertIn("addons/sentry/web/sentry_web.js", names)
        self.assertIn("web.debug.threads.wasm32", gdextension)

    def test_removes_a_dropped_platform_tree_and_its_support_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            names, _, _ = stage_fixture(Path(temp_dir), dropped_platforms=["web"])

        self.assertFalse(any(name.startswith("addons/sentry/bin/web/") for name in names))
        self.assertFalse(any(name.startswith("addons/sentry/web/") for name in names))

    def test_removes_the_noop_tree(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            names, _, _ = stage_fixture(Path(temp_dir))

        self.assertFalse(any(name.startswith("addons/sentry/bin/noop/") for name in names))

    def test_drops_debug_symbol_sidecars_no_entry_references(self):
        # Upstream publishes debug symbols as a separate release asset. Were
        # they in the addon archive, nothing would reference them and they would
        # land in core, where every consumer would pay for them.
        with tempfile.TemporaryDirectory() as temp_dir:
            names, _, _ = stage_fixture(Path(temp_dir), extra_files=fixture.ANDROID_DEBUG_SYMBOLS)

        self.assertIn("addons/sentry/bin/android/libsentry.android.debug.arm64.so", names)
        self.assertFalse(any(name.endswith(".so.debug") for name in names))

    def test_refuses_an_unreferenced_binary_it_cannot_classify(self):
        # A new upstream artifact under bin/ has to be decided on, not guessed
        # at: silently dropping it breaks one platform in consumers' projects,
        # and silently keeping it puts it in core for everyone.
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(stage.StageError, "no slice can own them: bin/android/mystery.jar"):
                stage_fixture(
                    Path(temp_dir),
                    extra_files={"addons/sentry/bin/android/mystery.jar": "new upstream artifact"},
                )

    def test_rejects_a_platform_key_gpm_cannot_classify(self):
        # A key gpm cannot partition has to fail the cut here, where the message
        # can name the key and say what to do, rather than inside gpm package.
        unclassifiable = SAMPLE_GDEXTENSION.replace(
            "macos.debug", "macos.debug.hardfloat", 1
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(stage.StageError, "hardfloat"):
                stage_fixture(Path(temp_dir), gdextension_text=unclassifiable)

    def test_rejects_missing_gdextension(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip = Path(temp_dir) / "input.zip"
            create_addon_zip(input_zip, omit={"addons/sentry/sentry.gdextension"})

            with self.assertRaisesRegex(stage.StageError, "sentry.gdextension"):
                stage.stage_addon_tree(input_zip, Path(temp_dir) / "staged", NOTHING_DROPPED)

    def test_rejects_missing_referenced_resource(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip = Path(temp_dir) / "input.zip"
            create_addon_zip(
                input_zip,
                omit={"addons/sentry/bin/android/libsentry.android.debug.arm64.so"},
            )

            with self.assertRaisesRegex(stage.StageError, "Missing referenced resource"):
                stage.stage_addon_tree(input_zip, Path(temp_dir) / "staged", NOTHING_DROPPED)

    def test_rejects_unsafe_zip_member_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip = Path(temp_dir) / "input.zip"
            with zipfile.ZipFile(input_zip, "w") as archive:
                archive.writestr("../escape.txt", "unsafe")
                archive.writestr("addons/sentry/sentry.gdextension", SAMPLE_GDEXTENSION)

            with self.assertRaisesRegex(stage.StageError, "Unsafe archive member path"):
                stage.stage_addon_tree(input_zip, Path(temp_dir) / "staged", NOTHING_DROPPED)

    def test_refuses_a_non_empty_output_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip = Path(temp_dir) / "input.zip"
            create_addon_zip(input_zip)
            output_dir = Path(temp_dir) / "staged"
            output_dir.mkdir()
            (output_dir / "leftover.txt").write_text("stale")

            with self.assertRaisesRegex(stage.StageError, "not empty"):
                stage.stage_addon_tree(input_zip, output_dir, NOTHING_DROPPED)


class UnsupportedPlatformsFileTests(unittest.TestCase):
    def test_the_repository_drops_only_what_gpm_cannot_package(self):
        platforms = stage.read_platforms_file(REPOSITORY_ROOT / "packaging" / "unsupported-platforms.txt")

        self.assertEqual([], platforms)


class CliTests(unittest.TestCase):
    def test_build_parser_has_expected_description(self):
        parser = stage.build_parser()

        self.assertEqual(
            "Stage a Sentry Godot addon zip into a tree that gpm can package.",
            parser.description,
        )

    def test_main_stages_addon_tree(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip = Path(temp_dir) / "input.zip"
            output_dir = Path(temp_dir) / "staged"
            create_addon_zip(input_zip)

            exit_code = stage.main(
                [
                    "--input",
                    str(input_zip),
                    "--output-dir",
                    str(output_dir),
                    "--drop-platform",
                    "web",
                ]
            )

            self.assertEqual(0, exit_code)
            self.assertTrue((output_dir / "addons" / "sentry" / "sentry.gdextension").is_file())

    def test_main_reports_a_tree_it_cannot_stage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip = Path(temp_dir) / "input.zip"
            create_addon_zip(input_zip)

            exit_code = stage.main(
                [
                    "--input",
                    str(input_zip),
                    "--output-dir",
                    str(Path(temp_dir) / "staged"),
                    "--drop-platform",
                    "visionos",
                ]
            )

            self.assertEqual(1, exit_code)


if __name__ == "__main__":
    unittest.main()
