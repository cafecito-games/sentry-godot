#!/usr/bin/env python3
"""Build a synthetic upstream Sentry Godot addon zip.

The fixture mirrors the shape of a real getsentry/sentry-godot addon asset
closely enough to exercise staging and gpm packaging without downloading one:
every platform this repository republishes, including web's threaded and
non-threaded library keys and the files no .gdextension key references, plus
what staging drops — noop libraries and a hypothetical future platform
directory.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from pathlib import Path
import sys
import zipfile


SAMPLE_GDEXTENSION = """[configuration]
entry_symbol = "sentry_gdextension_init"
compatibility_minimum = "4.5"

[libraries]

macos.debug = "res://addons/sentry/bin/macos/libsentry.macos.debug.dylib"
macos.release = "res://addons/sentry/bin/macos/libsentry.macos.release.dylib"

windows.debug.x86_64 = "res://addons/sentry/bin/windows/x86_64/libsentry.windows.debug.x86_64.dll"
windows.release.x86_64 = "res://addons/sentry/bin/windows/x86_64/libsentry.windows.release.x86_64.dll"
windows.debug.x86_32 = "res://addons/sentry/bin/windows/x86_32/libsentry.windows.debug.x86_32.dll"
windows.release.x86_32 = "res://addons/sentry/bin/windows/x86_32/libsentry.windows.release.x86_32.dll"
windows.debug.arm64 = "res://addons/sentry/bin/windows/arm64/libsentry.windows.debug.arm64.dll"
windows.release.arm64 = "res://addons/sentry/bin/windows/arm64/libsentry.windows.release.arm64.dll"

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

web.debug.wasm32 = "res://addons/sentry/bin/web/libsentry.web.debug.wasm32.nothreads.wasm"
web.debug.threads.wasm32 = "res://addons/sentry/bin/web/libsentry.web.debug.wasm32.wasm"

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

windows.x86_32 = {
    "res://addons/sentry/bin/windows/x86_32/crashpad_handler.exe" : "",
    "res://addons/sentry/bin/windows/x86_32/crashpad_wer.dll": ""
}

windows.arm64 = {
    "res://addons/sentry/bin/windows/arm64/crashpad_handler.exe" : "",
    "res://addons/sentry/bin/windows/arm64/crashpad_wer.dll": ""
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


ANDROID_DEBUG_SYMBOLS = {
    f"addons/sentry/bin/android/libsentry.android.{target}.{architecture}.so.debug": f"android {target} symbols"
    for architecture in ("arm32", "arm64", "x86_32", "x86_64")
    for target in ("debug", "release")
}
"""Debug-symbol sidecars some upstream versions ship beside the Android libraries.

They are absent from the current upstream addon asset, so they are not part of
the default fixture: a cut that found them would have to claim them per
architecture, and a `[package.slices]` pattern matching no file is an error.
"""


def addon_files(
    gdextension_text: str = SAMPLE_GDEXTENSION,
    extra_files: Mapping[str, str] | None = None,
) -> dict[str, str]:
    files = {
        "addons/sentry/sentry.gdextension": gdextension_text,
        "addons/sentry/shared.gd": "shared",
        "addons/sentry/feedback/user_feedback.gd": "feedback",
        "addons/sentry/dotnet/lib/Sentry.Godot.dll": "managed assembly",
        "addons/sentry/web/sentry_web.js": "web support",
        "addons/sentry/bin/android/sentry_android_godot_plugin.debug.aar": "android plugin",
        "addons/sentry/bin/android/sentry_android_godot_plugin.release.aar": "android plugin",
        "addons/sentry/bin/macos/libsentry.macos.debug.dylib": "macos debug",
        "addons/sentry/bin/macos/libsentry.macos.release.dylib": "macos release",
        "addons/sentry/bin/macos/libSentry.dylib": "macos dependency",
        "addons/sentry/bin/ios/libsentry.ios.debug.xcframework/Info.plist": "ios debug",
        "addons/sentry/bin/ios/libsentry.ios.release.xcframework/Info.plist": "ios release",
        "addons/sentry/bin/ios/Sentry.xcframework/Info.plist": "ios dependency",
        "addons/sentry/bin/web/libsentry.web.debug.wasm32.wasm": "web",
        "addons/sentry/bin/web/libsentry.web.debug.wasm32.nothreads.wasm": "web",
        "addons/sentry/bin/noop/libsentry.linux.debug.rv64.so": "noop",
        "addons/sentry/bin/visionos/libsentry.visionos.debug.xcframework/Info.plist": "visionos",
    }

    for architecture in ("arm32", "arm64", "x86_32", "x86_64"):
        for target in ("debug", "release"):
            files[f"addons/sentry/bin/android/libsentry.android.{target}.{architecture}.so"] = f"android {target}"

    for architecture in ("arm64", "x86_32", "x86_64"):
        for target in ("debug", "release"):
            files[f"addons/sentry/bin/linux/{architecture}/libsentry.linux.{target}.{architecture}.so"] = "linux"
            files[f"addons/sentry/bin/windows/{architecture}/libsentry.windows.{target}.{architecture}.dll"] = "windows"
        files[f"addons/sentry/bin/linux/{architecture}/crashpad_handler"] = "linux dependency"
        files[f"addons/sentry/bin/windows/{architecture}/crashpad_handler.exe"] = "windows dependency"
        files[f"addons/sentry/bin/windows/{architecture}/crashpad_wer.dll"] = "windows dependency"

    files.update(extra_files or {})
    return files


def create_addon_zip(
    path: Path,
    gdextension_text: str = SAMPLE_GDEXTENSION,
    omit: set[str] | None = None,
    extra_files: Mapping[str, str] | None = None,
) -> None:
    omit = omit or set()
    with zipfile.ZipFile(path, "w") as archive:
        for name, contents in sorted(addon_files(gdextension_text, extra_files).items()):
            if name not in omit:
                archive.writestr(name, contents)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a synthetic upstream Sentry Godot addon zip.")
    parser.add_argument("--output", required=True, type=Path, help="Path to write the fixture zip to.")
    args = parser.parse_args(argv)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    create_addon_zip(args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
