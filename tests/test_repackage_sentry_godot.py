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

android.debug.arm64 = "res://addons/sentry/bin/android/libsentry.android.debug.arm64.so"
android.release.arm64 = "res://addons/sentry/bin/android/libsentry.android.release.arm64.so"

ios.debug = "res://addons/sentry/bin/ios/libsentry.ios.debug.xcframework"
ios.release = "res://addons/sentry/bin/ios/libsentry.ios.release.xcframework"

web.debug.wasm32 = "res://addons/sentry/bin/web/libsentry.web.debug.wasm32.wasm"

; noop libs for unsupported platforms
linux.debug.rv64 = "res://addons/sentry/bin/noop/libsentry.linux.debug.rv64.so"

[dependencies]

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
        "addons/sentry/bin/android/libsentry.android.release.arm64.so": "android release",
        "addons/sentry/bin/macos/libsentry.macos.debug.dylib": "macos debug",
        "addons/sentry/bin/macos/libsentry.macos.release.dylib": "macos release",
        "addons/sentry/bin/macos/libSentry.dylib": "macos dependency",
        "addons/sentry/bin/ios/libsentry.ios.debug.xcframework": "ios debug",
        "addons/sentry/bin/ios/libsentry.ios.release.xcframework": "ios release",
        "addons/sentry/bin/ios/Sentry.xcframework": "ios dependency",
        "addons/sentry/bin/windows/x86_64/libsentry.windows.debug.x86_64.dll": "windows",
        "addons/sentry/bin/windows/x86_64/crashpad_handler.exe": "windows dependency",
        "addons/sentry/bin/windows/x86_64/crashpad_wer.dll": "windows dependency",
        "addons/sentry/bin/linux/x86_64/libsentry.linux.debug.x86_64.so": "linux",
        "addons/sentry/bin/web/libsentry.web.debug.wasm32.wasm": "web",
        "addons/sentry/bin/noop/libsentry.linux.debug.rv64.so": "noop",
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
        self.assertIn("addons/sentry/bin/ios/libsentry.ios.debug.xcframework", names)
        self.assertFalse(any(name.startswith("addons/sentry/web/") for name in names))
        self.assertFalse(any(name.startswith("addons/sentry/bin/windows/") for name in names))
        self.assertFalse(any(name.startswith("addons/sentry/bin/linux/") for name in names))
        self.assertFalse(any(name.startswith("addons/sentry/bin/web/") for name in names))
        self.assertFalse(any(name.startswith("addons/sentry/bin/noop/") for name in names))
        self.assertNotIn("windows", gdextension.lower())
        self.assertNotIn("linux", gdextension.lower())
        self.assertNotIn("web", gdextension.lower())
        self.assertNotIn("noop", gdextension.lower())

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


class CliTests(unittest.TestCase):
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
