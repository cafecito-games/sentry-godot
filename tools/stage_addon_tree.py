#!/usr/bin/env python3
"""Stage an upstream Sentry Godot addon tree so gpm can package it.

Nothing here curates platforms. Everything upstream ships is published as a gpm
slice, and a project downloads only the slices it declares, so there is no
reason to pick winners. Staging removes two kinds of thing:

- the noop placeholder libraries, which exist upstream to keep Godot quiet on
  platforms Sentry does not support and would otherwise be published as slices
  claiming support that is not there
- any platform gpm cannot package at all, named in
  packaging/unsupported-platforms.txt. That list is empty: it is the escape
  hatch for a future upstream platform gpm refuses, not a curated set.

Anything else under bin/ that no surviving .gdextension entry references has to
be classified explicitly, as a kept extra or a dropped one. A new upstream
artifact therefore fails the cut with its own name in the message rather than
vanishing into a release.
"""
from __future__ import annotations

import argparse
from collections.abc import Iterable
import fnmatch
import os
from pathlib import Path
import re
import shutil
import sys
import zipfile


ADDON_ROOT = Path("addons/sentry")
GDEXTENSION_PATH = ADDON_ROOT / "sentry.gdextension"
BIN_ROOT = ADDON_ROOT / "bin"
RESOURCE_RE = re.compile(r"res://[^\s\"'{}:,]+")

# Directories outside bin/ that belong to one platform and go when it does.
SUPPORT_DIRS_BY_PLATFORM = {"web": [ADDON_ROOT / "web"]}

NOOP_PLATFORM = "noop"

# gpm's .gdextension key vocabulary. A key whose components do not all fall into
# one of these axes is rejected by gpm package rather than ignored, so reject it
# here instead, where the message can say what to do about it.
PLATFORMS = frozenset({"android", "ios", "linux", "macos", "web", "windows"})
ARCHITECTURES = frozenset({"arm32", "arm64", "rv64", "universal", "wasm32", "x86_32", "x86_64"})
BUILD_TARGETS = frozenset({"debug", "editor", "release", "template_debug", "template_release"})
FLOAT_PRECISIONS = frozenset({"double", "single"})
PLATFORM_VARIANTS = frozenset({"simulator", "threads"})

# Files under bin/ that no .gdextension entry references. Such a file is placed
# by a [package.slices] rule in packaging/gpm-package.toml, or it falls into
# core, which every consumer downloads on every platform.
#
# Godot needs the kept ones at export time. The dropped ones are upstream build
# by-products this repository does not republish. Anything matching neither is
# an error rather than a guess: silently dropping a file Godot needs breaks one
# platform in consumers' projects, which is how the Android .aar would have been
# lost.
KEPT_UNREFERENCED_BINARIES = ("*.aar",)
DROPPED_UNREFERENCED_BINARIES = ("*.so.debug", "*.pdb", "*.dSYM/**")


class StageError(RuntimeError):
    """Raised when an addon archive cannot be safely staged."""


def normalize_dropped_platforms(platforms: Iterable[str]) -> frozenset[str]:
    dropped: set[str] = set()
    for raw_platform in platforms:
        platform = raw_platform.strip().lower()
        if not platform:
            continue
        if platform not in PLATFORMS:
            known = ", ".join(sorted(PLATFORMS))
            raise StageError(f"Not a Godot platform: {raw_platform} (known platforms: {known})")
        dropped.add(platform)
    return frozenset(dropped)


def slice_id(key: str) -> str:
    """Reduce a .gdextension entry key to the gpm slice that owns it.

    Only the architecture survives into a slice ID. The build target, the float
    precision and the platform variant are all dropped, so macos.debug and
    macos.template_release both land in "macos".
    """
    components = [component for component in key.strip().split(".") if component]
    if not components:
        raise StageError("Empty .gdextension entry key")

    platform = components[0]
    if platform not in PLATFORMS:
        raise StageError(f"Not a Godot platform in .gdextension key {key!r}: {platform!r}")

    architecture: str | None = None
    seen_axes: set[str] = set()
    for component in components[1:]:
        if component in ARCHITECTURES:
            axis = "architecture"
        elif component in BUILD_TARGETS:
            axis = "build target"
        elif component in FLOAT_PRECISIONS:
            axis = "float precision"
        elif component in PLATFORM_VARIANTS:
            axis = "platform variant"
        else:
            raise StageError(
                f"gpm cannot classify .gdextension key {key!r}: component {component!r} is no known "
                "architecture, build target, float precision or platform variant. Add "
                f"{platform!r} to packaging/unsupported-platforms.txt if this platform cannot be published."
            )

        if axis in seen_axes:
            raise StageError(f"gpm rejects .gdextension key {key!r}: it names a {axis} twice")
        seen_axes.add(axis)

        if axis == "architecture":
            architecture = component

    return platform if architecture is None else f"{platform}.{architecture}"


def entry_platform(key: str) -> str:
    return key.strip().split(".", 1)[0].lower()


def section_name(line: str) -> str | None:
    stripped = line.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        return stripped[1:-1].strip()
    return None


def entry_key(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith(";") or stripped.startswith("#"):
        return None
    if "=" not in stripped:
        return None
    return stripped.split("=", 1)[0].strip()


def read_entry(lines: list[str], index: int) -> tuple[list[str], int]:
    entry = [lines[index]]
    depth = lines[index].count("{") - lines[index].count("}")
    index += 1

    while depth > 0 and index < len(lines):
        entry.append(lines[index])
        depth += lines[index].count("{") - lines[index].count("}")
        index += 1

    return entry, index


def entry_mentions_noop(entry: list[str]) -> bool:
    return any(NOOP_PLATFORM in line.lower() for line in entry)


def mentions_dropped_platform(line: str, dropped_platforms: frozenset[str]) -> bool:
    normalized_line = line.lower()
    return any(token in normalized_line for token in dropped_platforms | {NOOP_PLATFORM})


def filter_section_entries(lines: list[str], dropped_platforms: frozenset[str]) -> tuple[list[str], set[str]]:
    output: list[str] = []
    kept_slices: set[str] = set()
    index = 0

    while index < len(lines):
        key = entry_key(lines[index])
        if key is None:
            # A comment explaining an entry that is on its way out goes with it.
            if not mentions_dropped_platform(lines[index], dropped_platforms):
                output.append(lines[index])
            index += 1
            continue

        entry, next_index = read_entry(lines, index)
        index = next_index

        if entry_platform(key) in dropped_platforms or entry_mentions_noop(entry):
            continue

        kept_slices.add(slice_id(key))
        output.extend(entry)

    return output, kept_slices


def split_sections(text: str) -> list[tuple[str | None, list[str]]]:
    sections: list[tuple[str | None, list[str]]] = []
    current_name: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines(keepends=True):
        name = section_name(line)
        if name is not None:
            if current_lines:
                sections.append((current_name, current_lines))
            current_name = name
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_name, current_lines))

    return sections


def filter_gdextension_text(text: str, dropped_platforms: Iterable[str]) -> str:
    dropped = dropped_platforms if isinstance(dropped_platforms, frozenset) else normalize_dropped_platforms(dropped_platforms)
    output: list[str] = []
    kept_library_slices: set[str] = set()

    for name, section_lines in split_sections(text):
        if name in {"libraries", "dependencies"}:
            header = section_lines[:1]
            body = section_lines[1:]
            filtered_body, kept_slices = filter_section_entries(body, dropped)
            if name == "libraries":
                kept_library_slices.update(kept_slices)
            output.extend(header)
            output.extend(filtered_body)
        else:
            output.extend(section_lines)

    if not kept_library_slices:
        raise StageError("No library entries survive, so the addon would publish no platform slice")

    result = "".join(output)
    if text.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result


def gdextension_entry_keys(text: str) -> set[str]:
    keys: set[str] = set()
    for name, section_lines in split_sections(text):
        if name not in {"libraries", "dependencies"}:
            continue
        for line in section_lines[1:]:
            key = entry_key(line)
            if key is not None:
                keys.add(key)
    return keys


def published_slices(text: str) -> set[str]:
    """The slice IDs gpm will publish for this .gdextension.

    A platform whose entries name architectures publishes only architecture
    slices: gpm folds that platform's generic entries into each of them, because
    a host installs the first slice of its platform it finds and would otherwise
    lose whichever half it did not match.
    """
    architectures_by_platform: dict[str, set[str]] = {}
    for key in gdextension_entry_keys(text):
        identifier = slice_id(key)
        platform, _, architecture = identifier.partition(".")
        architectures_by_platform.setdefault(platform, set())
        if architecture:
            architectures_by_platform[platform].add(architecture)

    slices: set[str] = set()
    for platform, architectures in architectures_by_platform.items():
        if architectures:
            slices.update(f"{platform}.{architecture}" for architecture in architectures)
        else:
            slices.add(platform)
    return slices


def referenced_resource_paths(text: str) -> set[Path]:
    return {Path(match.group()[len("res://") :]) for match in RESOURCE_RE.finditer(text)}


def matches_any(path: Path, patterns: Iterable[str]) -> bool:
    name = path.name
    posix = path.as_posix()
    return any(fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(posix, pattern) for pattern in patterns)


def is_referenced(path: Path, referenced: set[Path]) -> bool:
    # A library entry may name a bundle directory, such as an .xcframework, so a
    # file is referenced when it is named or when one of its parents is.
    return path in referenced or any(parent in referenced for parent in path.parents)


def remove_dropped_platform_trees(root: Path, dropped_platforms: frozenset[str]) -> None:
    for platform in sorted(dropped_platforms):
        shutil.rmtree(root / BIN_ROOT / platform, ignore_errors=True)
        for directory in SUPPORT_DIRS_BY_PLATFORM.get(platform, []):
            shutil.rmtree(root / directory, ignore_errors=True)

    # bin/ is laid out one directory per platform, so a directory that is not a
    # Godot platform — noop, or a platform Godot does not have a feature tag for
    # yet — can be claimed by no slice whatever it holds.
    bin_root = root / BIN_ROOT
    if bin_root.is_dir():
        for child in sorted(bin_root.iterdir()):
            if child.is_dir() and child.name not in PLATFORMS:
                shutil.rmtree(child)


def prune_unreferenced_binaries(root: Path, gdextension_text: str) -> None:
    bin_root = root / BIN_ROOT
    if not bin_root.is_dir():
        return

    referenced = referenced_resource_paths(gdextension_text)
    unclassified: list[str] = []

    for path in sorted(bin_root.rglob("*")):
        if not path.is_file():
            continue

        relative_path = path.relative_to(root)
        if is_referenced(relative_path, referenced):
            continue

        addon_relative_path = relative_path.relative_to(ADDON_ROOT)
        if matches_any(addon_relative_path, KEPT_UNREFERENCED_BINARIES):
            continue
        if matches_any(addon_relative_path, DROPPED_UNREFERENCED_BINARIES):
            path.unlink()
            continue

        unclassified.append(addon_relative_path.as_posix())

    if unclassified:
        raise StageError(
            "No .gdextension entry references these files and they are neither a kept nor a dropped "
            f"extra, so no slice can own them: {', '.join(unclassified)}. Classify them in "
            "tools/stage_addon_tree.py, and claim the kept ones in packaging/gpm-package.toml."
        )

    remove_empty_directories(bin_root)


def remove_empty_directories(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda candidate: len(candidate.parts), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def validate_tree(root: Path, dropped_platforms: frozenset[str]) -> None:
    gdextension_path = root / GDEXTENSION_PATH
    if not gdextension_path.is_file():
        raise StageError(f"Missing required file: {GDEXTENSION_PATH}")

    gdextension_text = gdextension_path.read_text(encoding="utf-8")

    remaining_dropped_entries = sorted(
        key for key in gdextension_entry_keys(gdextension_text) if entry_platform(key) in dropped_platforms
    )
    if remaining_dropped_entries:
        entries = ", ".join(remaining_dropped_entries)
        raise StageError(f"gdextension still references dropped platform entries: {entries}")

    lowered_text = gdextension_text.lower()
    for platform in sorted(dropped_platforms | {NOOP_PLATFORM}):
        if platform in lowered_text:
            raise StageError(f"gdextension still references dropped platform: {platform}")

    # Raises on any key gpm could not classify, so the cut fails here rather
    # than inside gpm package.
    published_slices(gdextension_text)

    bin_root = root / BIN_ROOT
    if bin_root.is_dir():
        remaining = sorted(
            child.name
            for child in bin_root.iterdir()
            if child.is_dir() and (child.name in dropped_platforms or child.name not in PLATFORMS)
        )
        if remaining:
            directories = ", ".join((BIN_ROOT / name).as_posix() for name in remaining)
            raise StageError(f"Dropped platform directory remains: {directories}")

    for platform in sorted(dropped_platforms):
        for directory in SUPPORT_DIRS_BY_PLATFORM.get(platform, []):
            if (root / directory).exists():
                raise StageError(f"Dropped platform directory remains: {directory}")

    for resource_path in sorted(referenced_resource_paths(gdextension_text)):
        if not (root / resource_path).exists():
            raise StageError(f"Missing referenced resource: res://{resource_path.as_posix()}")


def safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in archive.infolist():
        member_path = Path(member.filename)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise StageError(f"Unsafe archive member path: {member.filename}")

        target = (destination / member.filename).resolve()
        try:
            target.relative_to(destination)
        except ValueError as error:
            raise StageError(f"Unsafe archive member path: {member.filename}") from error

        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)


def prepare_output_directory(output_dir: Path) -> None:
    if output_dir.exists():
        if not output_dir.is_dir():
            raise StageError(f"Output path is not a directory: {output_dir}")
        if any(output_dir.iterdir()):
            raise StageError(f"Output directory is not empty: {output_dir}")
        return
    output_dir.mkdir(parents=True)


def stage_addon_tree(
    input_path: os.PathLike[str] | str,
    output_dir: os.PathLike[str] | str,
    dropped_platforms: Iterable[str],
) -> set[str]:
    dropped = normalize_dropped_platforms(dropped_platforms)
    input_path = Path(input_path)
    output_dir = Path(output_dir)

    prepare_output_directory(output_dir)

    with zipfile.ZipFile(input_path) as archive:
        safe_extract(archive, output_dir)

    gdextension_path = output_dir / GDEXTENSION_PATH
    if not gdextension_path.is_file():
        raise StageError(f"Missing required file: {GDEXTENSION_PATH}")

    staged_text = filter_gdextension_text(gdextension_path.read_text(encoding="utf-8"), dropped)
    gdextension_path.write_text(staged_text, encoding="utf-8")

    remove_dropped_platform_trees(output_dir, dropped)
    prune_unreferenced_binaries(output_dir, staged_text)
    validate_tree(output_dir, dropped)

    return published_slices(staged_text)


def read_platforms_file(path: Path) -> list[str]:
    platforms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        platform = line.split("#", 1)[0].strip()
        if platform:
            platforms.append(platform)
    return platforms


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stage a Sentry Godot addon zip into a tree that gpm can package.",
    )
    parser.add_argument("--input", required=True, type=Path, help="Path to the upstream addon zip.")
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory to stage the addon tree into. Must be empty or absent.",
    )
    parser.add_argument(
        "--drop-platform",
        action="append",
        default=[],
        help="Platform to leave out entirely. Repeat for several. Everything else is published.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        slices = stage_addon_tree(args.input, args.output_dir, args.drop_platform)
    except (OSError, zipfile.BadZipFile, StageError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"Staged {args.output_dir} for slices: {', '.join(sorted(slices))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
