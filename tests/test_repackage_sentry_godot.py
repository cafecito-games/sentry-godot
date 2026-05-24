from pathlib import Path
import importlib.util
import unittest


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

    def test_filter_rejects_requested_platform_without_libraries(self):
        with self.assertRaisesRegex(repackage.RepackageError, "visionos"):
            repackage.filter_gdextension_text(
                SAMPLE_GDEXTENSION,
                ["android", "visionos"],
            )


if __name__ == "__main__":
    unittest.main()
