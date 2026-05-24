from pathlib import Path
import importlib.util
import tempfile
import unittest
import zipfile


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "tools" / "repackage_sentry_godot.py"
spec = importlib.util.spec_from_file_location("repackage_sentry_godot", SCRIPT_PATH)
repackage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repackage)


SAMPLE_GDEXTENSION = """[configuration]
entry_symbol = "sentry_gdextension_init"
compatibility_minimum = "4.5"

[libraries]

macos.debug = "res://addons/sentry/bin/macos/libsentry.macos.debug.dylib"
macos.release = "res://addons/sentry/bin/macos/libsentry.macos.release.dylib"

windows.debug.x86_64 = "res://addons/sentry/bin/windows/x86_64/libsentry.windows.debug.x86_64.dll"
linux.debug.x86_64 = "res://addons/sentry/bin/linux/x86_64/libsentry.linux.debug.x86_64.so"
linux.release.x86_64 = "res://addons/sentry/bin/linux/x86_64/libsentry.linux.release.x86_64.so"
linux.debug.x86_32 = "res://addons/sentry/bin/linux/x86_32/libsentry.linux.debug.x86_32.so"
linux.release.x86_32 = "res://addons/sentry/bin/linux/x86_32/libsentry.linux.release.x86_32.so"
linux.debug.arm64 = "res://addons/sentry/bin/linux/arm64/libsentry.linux.debug.arm64.so"
linux.release.arm64 = "res://addons/sentry/bin/linux/arm64/libsentry.linux.release.arm64.so"

android.debug.arm64 = "res://addons/sentry/bin/android/libsentry.android.debug.arm64.so"
android.release.arm64 = "res://addons/sentry/bin/android/libsentry.android.release.arm64.so"
android.debug.arm32 = "res://addons/sentry/bin/android/libsentry.android.debug.arm32.so"
android.release.arm32 = "res://addons/sentry/bin/android/libsentry.android.release.arm32.so"
android.debug.x86_64 = "res://addons/sentry/bin/android/libsentry.android.debug.x86_64.so"
android.release.x86_64 = "res://addons/sentry/bin/android/libsentry.android.release.x86_64.so"
android.debug.x86_32 = "res://addons/sentry/bin/android/libsentry.android.debug.x86_32.so"
android.release.x86_32 = "res://addons/sentry/bin/android/libsentry.android.release.x86_32.so"

ios.debug = "res://addons/sentry/bin/ios/libsentry.ios.debug.xcframework"
ios.release = "res://addons/sentry/bin/ios/libsentry.ios.release.xcframework"

web.debug.wasm32 = "res://addons/sentry/bin/web/libsentry.web.debug.wasm32.wasm"

; noop libs for unsupported platforms
linux.debug.rv64 = "res://addons/sentry/bin/noop/libsentry.linux.debug.rv64.so"

[dependencies]

linux.x86_64 = {
    "res://addons/sentry/bin/linux/x86_64/crashpad_handler" : ""
}

linux.x86_32 = {
    "res://addons/sentry/bin/linux/x86_32/crashpad_handler" : ""
}

linux.arm64 = {
    "res://addons/sentry/bin/linux/arm64/crashpad_handler" : ""
}

windows.x86_64 = {
    "res://addons/sentry/bin/windows/x86_64/crashpad_handler.exe" : "",
    "res://addons/sentry/bin/windows/x86_64/crashpad_wer.dll": ""
}

macos.debug = {
    "res://addons/sentry/bin/macos/libSentry.dylib" : ""
}

macos.release = {
    "res://addons/sentry/bin/macos/libSentry.dylib" : ""
}

ios.debug = {
  "res://addons/sentry/bin/ios/Sentry.xcframework": "",
}

ios.release = {
  "res://addons/sentry/bin/ios/Sentry.xcframework": "",
}
"""


class GDExtensionFilteringTests(unittest.TestCase):
    def test_filter_keeps_requested_platform_entries(self):
        filtered = repackage.filter_gdextension_text(
            SAMPLE_GDEXTENSION,
            ["android", "macos", "ios"],
        )

        self.assertIn("[configuration]", filtered)
        self.assertIn('entry_symbol = "sentry_gdextension_init"', filtered)
        self.assertIn("macos.debug", filtered)
        self.assertIn("android.release.arm64", filtered)
        self.assertIn("ios.release", filtered)
        self.assertIn("[dependencies]", filtered)
        self.assertIn("res://addons/sentry/bin/ios/Sentry.xcframework", filtered)

    def test_filter_removes_unrequested_platform_entries(self):
        filtered = repackage.filter_gdextension_text(
            SAMPLE_GDEXTENSION,
            ["android", "macos", "ios"],
        )

        self.assertNotIn("windows.", filtered)
        self.assertNotIn("linux.", filtered)
        self.assertNotIn("web.", filtered)
        self.assertNotIn("bin/windows", filtered)
        self.assertNotIn("bin/linux", filtered)
        self.assertNotIn("bin/web", filtered)
        self.assertNotIn("bin/noop", filtered)
        self.assertNotIn("noop", filtered)

    def test_filter_removes_noop_entries_even_when_entry_platform_is_requested(self):
        filtered = repackage.filter_gdextension_text(
            SAMPLE_GDEXTENSION,
            ["linux"],
        )

        self.assertIn("linux.debug.x86_64", filtered)
        self.assertNotIn("linux.debug.rv64", filtered)
        self.assertNotIn("bin/noop", filtered)
        self.assertNotIn("noop", filtered)

    def test_filter_can_keep_only_linux_x86_64_entries(self):
        filtered = repackage.filter_gdextension_text(
            SAMPLE_GDEXTENSION,
            ["linux.x86_64"],
        )

        self.assertIn("linux.debug.x86_64", filtered)
        self.assertIn("linux.release.x86_64", filtered)
        self.assertIn("res://addons/sentry/bin/linux/x86_64/crashpad_handler", filtered)
        self.assertNotIn("linux.debug.x86_32", filtered)
        self.assertNotIn("linux.release.x86_32", filtered)
        self.assertNotIn("linux.debug.arm64", filtered)
        self.assertNotIn("linux.release.arm64", filtered)
        self.assertNotIn("res://addons/sentry/bin/linux/x86_32", filtered)
        self.assertNotIn("res://addons/sentry/bin/linux/arm64", filtered)
        self.assertNotIn("linux.debug.rv64", filtered)
        self.assertNotIn("bin/noop", filtered)

    def test_filter_can_keep_only_android_arm64_entries(self):
        filtered = repackage.filter_gdextension_text(
            SAMPLE_GDEXTENSION,
            ["android.arm64"],
        )

        self.assertIn("android.debug.arm64", filtered)
        self.assertIn("android.release.arm64", filtered)
        self.assertNotIn("android.debug.arm32", filtered)
        self.assertNotIn("android.release.arm32", filtered)
        self.assertNotIn("android.debug.x86_32", filtered)
        self.assertNotIn("android.release.x86_32", filtered)
        self.assertNotIn("android.debug.x86_64", filtered)
        self.assertNotIn("android.release.x86_64", filtered)
        self.assertNotIn("libsentry.android.debug.x86_32.so", filtered)
        self.assertNotIn("libsentry.android.release.x86_64.so", filtered)

    def test_filter_rejects_requested_platform_without_libraries(self):
        with self.assertRaisesRegex(repackage.RepackageError, "visionos"):
            repackage.filter_gdextension_text(
                SAMPLE_GDEXTENSION,
                ["android", "visionos"],
            )


def create_addon_zip(path: Path, gdextension_text: str = SAMPLE_GDEXTENSION, omit: set[str] | None = None) -> None:
    omit = omit or set()
    files = {
        "addons/sentry/sentry.gdextension": gdextension_text,
        "addons/sentry/shared.gd": "shared",
        "addons/sentry/feedback/user_feedback.gd": "feedback",
        "addons/sentry/web/sentry_web.js": "web support",
        "addons/sentry/bin/android/libsentry.android.debug.arm64.so": "android debug",
        "addons/sentry/bin/android/libsentry.android.debug.arm64.so.debug": "android debug symbols",
        "addons/sentry/bin/android/libsentry.android.release.arm64.so": "android release",
        "addons/sentry/bin/android/libsentry.android.release.arm64.so.debug": "android release symbols",
        "addons/sentry/bin/android/libsentry.android.debug.arm32.so": "android debug",
        "addons/sentry/bin/android/libsentry.android.debug.arm32.so.debug": "android debug symbols",
        "addons/sentry/bin/android/libsentry.android.release.arm32.so": "android release",
        "addons/sentry/bin/android/libsentry.android.release.arm32.so.debug": "android release symbols",
        "addons/sentry/bin/android/libsentry.android.debug.x86_32.so": "android debug",
        "addons/sentry/bin/android/libsentry.android.debug.x86_32.so.debug": "android debug symbols",
        "addons/sentry/bin/android/libsentry.android.release.x86_32.so": "android release",
        "addons/sentry/bin/android/libsentry.android.release.x86_32.so.debug": "android release symbols",
        "addons/sentry/bin/android/libsentry.android.debug.x86_64.so": "android debug",
        "addons/sentry/bin/android/libsentry.android.debug.x86_64.so.debug": "android debug symbols",
        "addons/sentry/bin/android/libsentry.android.release.x86_64.so": "android release",
        "addons/sentry/bin/android/libsentry.android.release.x86_64.so.debug": "android release symbols",
        "addons/sentry/bin/android/sentry_android_godot_plugin.debug.aar": "android plugin",
        "addons/sentry/bin/android/sentry_android_godot_plugin.release.aar": "android plugin",
        "addons/sentry/bin/macos/libsentry.macos.debug.dylib": "macos debug",
        "addons/sentry/bin/macos/libsentry.macos.release.dylib": "macos release",
        "addons/sentry/bin/macos/libSentry.dylib": "macos dependency",
        "addons/sentry/bin/ios/libsentry.ios.debug.xcframework/Info.plist": "ios debug",
        "addons/sentry/bin/ios/libsentry.ios.release.xcframework/Info.plist": "ios release",
        "addons/sentry/bin/ios/Sentry.xcframework/Info.plist": "ios dependency",
        "addons/sentry/bin/windows/x86_64/libsentry.windows.debug.x86_64.dll": "windows",
        "addons/sentry/bin/windows/x86_64/crashpad_handler.exe": "windows dependency",
        "addons/sentry/bin/windows/x86_64/crashpad_wer.dll": "windows dependency",
        "addons/sentry/bin/linux/x86_64/libsentry.linux.debug.x86_64.so": "linux",
        "addons/sentry/bin/linux/x86_64/libsentry.linux.release.x86_64.so": "linux",
        "addons/sentry/bin/linux/x86_64/crashpad_handler": "linux dependency",
        "addons/sentry/bin/linux/x86_32/libsentry.linux.debug.x86_32.so": "linux",
        "addons/sentry/bin/linux/x86_32/libsentry.linux.release.x86_32.so": "linux",
        "addons/sentry/bin/linux/x86_32/crashpad_handler": "linux dependency",
        "addons/sentry/bin/linux/arm64/libsentry.linux.debug.arm64.so": "linux",
        "addons/sentry/bin/linux/arm64/libsentry.linux.release.arm64.so": "linux",
        "addons/sentry/bin/linux/arm64/crashpad_handler": "linux dependency",
        "addons/sentry/bin/web/libsentry.web.debug.wasm32.wasm": "web",
        "addons/sentry/bin/noop/libsentry.linux.debug.rv64.so": "noop",
        "addons/sentry/bin/visionos/libsentry.visionos.debug.xcframework/Info.plist": "visionos",
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, contents in files.items():
            if name not in omit:
                archive.writestr(name, contents)


class ArchiveRepackagingTests(unittest.TestCase):
    def test_repackage_archive_keeps_requested_files_and_removes_unsupported_platforms(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip = Path(temp_dir) / "input.zip"
            output_zip = Path(temp_dir) / "output.zip"
            create_addon_zip(input_zip)

            repackage.repackage_archive(
                input_zip,
                output_zip,
                ["android", "macos", "ios"],
            )

            with zipfile.ZipFile(output_zip) as archive:
                names = set(archive.namelist())
                gdextension = archive.read("addons/sentry/sentry.gdextension").decode("utf-8")

        self.assertIn("addons/sentry/shared.gd", names)
        self.assertIn("addons/sentry/feedback/user_feedback.gd", names)
        self.assertIn("addons/sentry/bin/android/libsentry.android.debug.arm64.so", names)
        self.assertIn("addons/sentry/bin/macos/libsentry.macos.debug.dylib", names)
        self.assertIn("addons/sentry/bin/ios/libsentry.ios.debug.xcframework/Info.plist", names)
        self.assertIn("addons/sentry/bin/ios/Sentry.xcframework/Info.plist", names)
        self.assertFalse(any(name.startswith("addons/sentry/web/") for name in names))
        self.assertFalse(any(name.startswith("addons/sentry/bin/windows/") for name in names))
        self.assertFalse(any(name.startswith("addons/sentry/bin/linux/") for name in names))
        self.assertFalse(any(name.startswith("addons/sentry/bin/web/") for name in names))
        self.assertFalse(any(name.startswith("addons/sentry/bin/noop/") for name in names))
        self.assertFalse(any(name.startswith("addons/sentry/bin/visionos/") for name in names))
        self.assertNotIn("windows", gdextension.lower())
        self.assertNotIn("linux", gdextension.lower())
        self.assertNotIn("web", gdextension.lower())
        self.assertNotIn("noop", gdextension.lower())

    def test_repackage_archive_keeps_only_requested_linux_x86_64_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip = Path(temp_dir) / "input.zip"
            output_zip = Path(temp_dir) / "output.zip"
            create_addon_zip(input_zip)

            repackage.repackage_archive(input_zip, output_zip, ["linux.x86_64"])

            with zipfile.ZipFile(output_zip) as archive:
                names = set(archive.namelist())
                gdextension = archive.read("addons/sentry/sentry.gdextension").decode("utf-8")

        self.assertIn("addons/sentry/bin/linux/x86_64/libsentry.linux.debug.x86_64.so", names)
        self.assertIn("addons/sentry/bin/linux/x86_64/libsentry.linux.release.x86_64.so", names)
        self.assertIn("addons/sentry/bin/linux/x86_64/crashpad_handler", names)
        self.assertFalse(any(name.startswith("addons/sentry/bin/linux/x86_32/") for name in names))
        self.assertFalse(any(name.startswith("addons/sentry/bin/linux/arm64/") for name in names))
        self.assertIn("linux.debug.x86_64", gdextension)
        self.assertIn("linux.release.x86_64", gdextension)
        self.assertIn("linux.x86_64", gdextension)
        self.assertNotIn("linux.debug.x86_32", gdextension)
        self.assertNotIn("linux.release.x86_32", gdextension)
        self.assertNotIn("linux.debug.arm64", gdextension)
        self.assertNotIn("linux.release.arm64", gdextension)
        self.assertNotIn("linux.x86_32", gdextension)
        self.assertNotIn("linux.arm64", gdextension)

    def test_repackage_archive_keeps_only_requested_android_arm64_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip = Path(temp_dir) / "input.zip"
            output_zip = Path(temp_dir) / "output.zip"
            create_addon_zip(input_zip)

            repackage.repackage_archive(input_zip, output_zip, ["android.arm64"])

            with zipfile.ZipFile(output_zip) as archive:
                names = set(archive.namelist())
                gdextension = archive.read("addons/sentry/sentry.gdextension").decode("utf-8")

        self.assertIn("addons/sentry/bin/android/libsentry.android.debug.arm64.so", names)
        self.assertIn("addons/sentry/bin/android/libsentry.android.debug.arm64.so.debug", names)
        self.assertIn("addons/sentry/bin/android/libsentry.android.release.arm64.so", names)
        self.assertIn("addons/sentry/bin/android/libsentry.android.release.arm64.so.debug", names)
        self.assertIn("addons/sentry/bin/android/sentry_android_godot_plugin.debug.aar", names)
        self.assertIn("addons/sentry/bin/android/sentry_android_godot_plugin.release.aar", names)
        self.assertFalse(any("libsentry.android.debug.arm32" in name for name in names))
        self.assertFalse(any("libsentry.android.release.arm32" in name for name in names))
        self.assertFalse(any("libsentry.android.debug.x86_32" in name for name in names))
        self.assertFalse(any("libsentry.android.release.x86_32" in name for name in names))
        self.assertFalse(any("libsentry.android.debug.x86_64" in name for name in names))
        self.assertFalse(any("libsentry.android.release.x86_64" in name for name in names))
        self.assertIn("android.debug.arm64", gdextension)
        self.assertIn("android.release.arm64", gdextension)
        self.assertNotIn("android.debug.arm32", gdextension)
        self.assertNotIn("android.release.arm32", gdextension)
        self.assertNotIn("android.debug.x86_32", gdextension)
        self.assertNotIn("android.release.x86_32", gdextension)
        self.assertNotIn("android.debug.x86_64", gdextension)
        self.assertNotIn("android.release.x86_64", gdextension)

    def test_repackage_archive_removes_unknown_future_platform_bin_directories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip = Path(temp_dir) / "input.zip"
            output_zip = Path(temp_dir) / "output.zip"
            create_addon_zip(input_zip)

            repackage.repackage_archive(input_zip, output_zip, ["android", "macos", "ios"])

            with zipfile.ZipFile(output_zip) as archive:
                names = set(archive.namelist())

        self.assertFalse(any(name.startswith("addons/sentry/bin/visionos/") for name in names))

    def test_repackage_archive_rejects_missing_gdextension(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip = Path(temp_dir) / "input.zip"
            output_zip = Path(temp_dir) / "output.zip"
            create_addon_zip(input_zip, omit={"addons/sentry/sentry.gdextension"})

            with self.assertRaisesRegex(repackage.RepackageError, "sentry.gdextension"):
                repackage.repackage_archive(input_zip, output_zip, ["android"])

    def test_repackage_archive_rejects_missing_referenced_resource(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip = Path(temp_dir) / "input.zip"
            output_zip = Path(temp_dir) / "output.zip"
            create_addon_zip(
                input_zip,
                omit={"addons/sentry/bin/android/libsentry.android.debug.arm64.so"},
            )

            with self.assertRaisesRegex(repackage.RepackageError, "Missing referenced resource"):
                repackage.repackage_archive(input_zip, output_zip, ["android"])

    def test_repackage_archive_rejects_unsafe_zip_member_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip = Path(temp_dir) / "input.zip"
            output_zip = Path(temp_dir) / "output.zip"
            with zipfile.ZipFile(input_zip, "w") as archive:
                archive.writestr("../escape.txt", "unsafe")
                archive.writestr("addons/sentry/sentry.gdextension", SAMPLE_GDEXTENSION)

            with self.assertRaisesRegex(repackage.RepackageError, "Unsafe archive member path"):
                repackage.repackage_archive(input_zip, output_zip, ["android"])


class CliTests(unittest.TestCase):
    def test_build_parser_has_expected_description(self):
        parser = repackage.build_parser()

        self.assertEqual(
            "Repackage a Sentry Godot addon zip for selected platforms.",
            parser.description,
        )

    def test_main_repackages_archive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_zip = Path(temp_dir) / "input.zip"
            output_zip = Path(temp_dir) / "output.zip"
            create_addon_zip(input_zip)

            exit_code = repackage.main(
                [
                    "--input",
                    str(input_zip),
                    "--output",
                    str(output_zip),
                    "--platform",
                    "android",
                    "--platform",
                    "macos",
                    "--platform",
                    "ios",
                ]
            )

            self.assertEqual(0, exit_code)
            self.assertTrue(output_zip.exists())


if __name__ == "__main__":
    unittest.main()
