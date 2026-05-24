#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import zipfile
from collections.abc import Iterable


ADDON_ROOT = Path("addons/sentry")
GDEXTENSION_PATH = ADDON_ROOT / "sentry.gdextension"
BIN_ROOT = ADDON_ROOT / "bin"
SUPPORT_DIRS_BY_PLATFORM = {"web": [ADDON_ROOT / "web"]}
RESOURCE_RE = re.compile(r"res://[^\s\"'{}:,]+")
SUPPORTED_PLATFORMS = {"android", "ios", "linux", "macos", "web", "windows"}
NOOP_PLATFORM = "noop"


class RepackageError(RuntimeError):
    """Raised when an addon archive cannot be safely repackaged."""


def normalize_platforms(platforms: Iterable[str]) -> set[str]:
    normalized = {platform.strip().lower() for platform in platforms if platform.strip()}
    if not normalized:
        raise RepackageError("At least one platform must be requested")
    return normalized


def platform_prefix(key: str) -> str:
    return key.strip().split(".", 1)[0]


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


def mentions_removed_platform(line: str, keep_platforms: set[str]) -> bool:
    removed_tokens = {"android", "ios", "linux", "macos", "noop", "web", "windows"} - keep_platforms
    normalized_line = line.lower()
    return any(token in normalized_line for token in removed_tokens)


def filter_section_entries(lines: list[str], keep_platforms: set[str]) -> tuple[list[str], set[str]]:
    output: list[str] = []
    kept_platforms: set[str] = set()
    index = 0

    while index < len(lines):
        key = entry_key(lines[index])
        if key is None:
            if not mentions_removed_platform(lines[index], keep_platforms):
                output.append(lines[index])
            index += 1
            continue

        entry, next_index = read_entry(lines, index)
        platform = platform_prefix(key)
        if platform in keep_platforms and not entry_mentions_noop(entry):
            output.extend(entry)
            kept_platforms.add(platform)
        index = next_index

    return output, kept_platforms


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


def filter_gdextension_text(text: str, platforms: Iterable[str]) -> str:
    keep_platforms = normalize_platforms(platforms)
    output: list[str] = []
    kept_library_platforms: set[str] = set()

    for name, section_lines in split_sections(text):
        if name in {"libraries", "dependencies"}:
            header = section_lines[:1]
            body = section_lines[1:]
            filtered_body, kept_platforms = filter_section_entries(body, keep_platforms)
            if name == "libraries":
                kept_library_platforms.update(kept_platforms)
            output.extend(header)
            output.extend(filtered_body)
        else:
            output.extend(section_lines)

    missing_platforms = keep_platforms - kept_library_platforms
    if missing_platforms:
        missing = ", ".join(sorted(missing_platforms))
        raise RepackageError(f"Requested platform(s) have no library entries: {missing}")

    result = "".join(output)
    if text.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result


def gdextension_entry_platforms(text: str) -> set[str]:
    platforms: set[str] = set()
    for name, section_lines in split_sections(text):
        if name not in {"libraries", "dependencies"}:
            continue
        for line in section_lines[1:]:
            key = entry_key(line)
            if key is not None:
                platforms.add(platform_prefix(key))
    return platforms


def referenced_resource_paths(text: str) -> set[Path]:
    return {Path(match.group()[len("res://") :]) for match in RESOURCE_RE.finditer(text)}


def safe_extract(archive: zipfile.ZipFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in archive.infolist():
        member_path = Path(member.filename)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise RepackageError(f"Unsafe archive member path: {member.filename}")

        target = (destination / member.filename).resolve()
        try:
            target.relative_to(destination)
        except ValueError as error:
            raise RepackageError(f"Unsafe archive member path: {member.filename}") from error

        if member.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)


def prune_platform_directories(root: Path, keep_platforms: set[str]) -> None:
    for platform in SUPPORTED_PLATFORMS | {NOOP_PLATFORM}:
        if platform not in keep_platforms:
            shutil.rmtree(root / BIN_ROOT / platform, ignore_errors=True)

    for platform, directories in SUPPORT_DIRS_BY_PLATFORM.items():
        if platform in keep_platforms:
            continue
        for directory in directories:
            shutil.rmtree(root / directory, ignore_errors=True)


def validate_tree(root: Path, keep_platforms: set[str]) -> None:
    gdextension_path = root / GDEXTENSION_PATH
    if not gdextension_path.is_file():
        raise RepackageError(f"Missing required file: {GDEXTENSION_PATH}")

    gdextension_text = gdextension_path.read_text(encoding="utf-8")
    removed_platforms = (SUPPORTED_PLATFORMS | {NOOP_PLATFORM}) - keep_platforms
    remaining_entry_platforms = gdextension_entry_platforms(gdextension_text) & removed_platforms
    if remaining_entry_platforms:
        platforms = ", ".join(sorted(remaining_entry_platforms))
        raise RepackageError(f"gdextension still references removed platform entries: {platforms}")

    lowered_text = gdextension_text.lower()
    for platform in sorted(removed_platforms):
        if platform in lowered_text:
            raise RepackageError(f"gdextension still references removed platform: {platform}")

    removed_directories = [BIN_ROOT / platform for platform in removed_platforms]
    for platform, directories in SUPPORT_DIRS_BY_PLATFORM.items():
        if platform not in keep_platforms:
            removed_directories.extend(directories)

    for directory in removed_directories:
        if (root / directory).exists():
            raise RepackageError(f"Removed platform directory remains: {directory}")

    for resource_path in sorted(referenced_resource_paths(gdextension_text)):
        if not (root / resource_path).exists():
            raise RepackageError(f"Missing referenced resource: res://{resource_path.as_posix()}")


def write_zip(root: Path, output_path: Path) -> None:
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not files:
        raise RepackageError("Refusing to write empty archive")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(root).as_posix())


def validate_output_zip(output_path: Path, keep_platforms: set[str]) -> None:
    if not output_path.is_file():
        raise RepackageError(f"Output archive was not created: {output_path}")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        with zipfile.ZipFile(output_path) as archive:
            if not archive.namelist():
                raise RepackageError("Output archive is empty")
            safe_extract(archive, root)
        validate_tree(root, keep_platforms)


def repackage_archive(input_path: os.PathLike[str] | str, output_path: os.PathLike[str] | str, platforms: Iterable[str]) -> None:
    keep_platforms = normalize_platforms(platforms)
    input_path = Path(input_path)
    output_path = Path(output_path)

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir) / "addon"
        root.mkdir()

        with zipfile.ZipFile(input_path) as archive:
            safe_extract(archive, root)

        gdextension_path = root / GDEXTENSION_PATH
        if not gdextension_path.is_file():
            raise RepackageError(f"Missing required file: {GDEXTENSION_PATH}")

        gdextension_text = gdextension_path.read_text(encoding="utf-8")
        gdextension_path.write_text(filter_gdextension_text(gdextension_text, keep_platforms), encoding="utf-8")

        prune_platform_directories(root, keep_platforms)
        validate_tree(root, keep_platforms)
        write_zip(root, output_path)

    validate_output_zip(output_path, keep_platforms)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Repackage a Sentry Godot addon zip for selected platforms.",
    )
    parser.add_argument("--input", required=True, type=Path, help="Path to the upstream addon zip.")
    parser.add_argument("--output", required=True, type=Path, help="Path to write the repackaged zip.")
    parser.add_argument(
        "--platform",
        action="append",
        required=True,
        help="Platform to keep. Repeat for multiple platforms.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        repackage_archive(args.input, args.output, args.platform)
    except (OSError, zipfile.BadZipFile, RepackageError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
