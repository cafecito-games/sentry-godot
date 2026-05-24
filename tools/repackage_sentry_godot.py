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
ARCHITECTURE_FILE_RE_BY_PLATFORM = {
    "android": re.compile(r"^libsentry\.android\.(?:debug|release)\.([^.]+)\.so(?:\.debug)?$"),
}
SUPPORTED_PLATFORMS = {"android", "ios", "linux", "macos", "web", "windows"}
NOOP_PLATFORM = "noop"


class PlatformSelection:
    def __init__(
        self,
        all_platforms: frozenset[str],
        architectures_by_platform: dict[str, frozenset[str]],
        requested_specs: frozenset[str],
    ) -> None:
        self.all_platforms = all_platforms
        self.architectures_by_platform = architectures_by_platform
        self.requested_specs = requested_specs

    @property
    def platforms(self) -> set[str]:
        return set(self.all_platforms) | set(self.architectures_by_platform)

    def matching_specs(self, key: str) -> set[str]:
        platform = platform_prefix(key)
        if platform in self.all_platforms:
            return {platform}

        requested_architectures = self.architectures_by_platform.get(platform)
        if requested_architectures is None:
            return set()

        architecture = entry_architecture(key)
        if architecture in requested_architectures:
            return {f"{platform}.{architecture}"}
        return set()

    def matches_entry(self, key: str) -> bool:
        return bool(self.matching_specs(key))


class RepackageError(RuntimeError):
    """Raised when an addon archive cannot be safely repackaged."""


def normalize_platforms(platforms: Iterable[str]) -> PlatformSelection:
    all_platforms: set[str] = set()
    architectures_by_platform: dict[str, set[str]] = {}
    requested_specs: set[str] = set()

    for raw_platform in platforms:
        spec = raw_platform.strip().lower()
        if not spec:
            continue

        parts = spec.split(".")
        if len(parts) > 2 or not all(parts):
            raise RepackageError(f"Invalid platform spec: {raw_platform}")

        platform = parts[0]
        requested_specs.add(spec)
        if len(parts) == 1:
            all_platforms.add(platform)
            architectures_by_platform.pop(platform, None)
            continue

        if platform not in all_platforms:
            architectures_by_platform.setdefault(platform, set()).add(parts[1])

    if not requested_specs:
        raise RepackageError("At least one platform must be requested")

    frozen_architectures = {
        platform: frozenset(architectures)
        for platform, architectures in architectures_by_platform.items()
    }
    return PlatformSelection(frozenset(all_platforms), frozen_architectures, frozenset(requested_specs))


def platform_prefix(key: str) -> str:
    return key.strip().split(".", 1)[0]


def entry_architecture(key: str) -> str | None:
    parts = key.strip().split(".")
    if len(parts) == 2:
        return parts[1]
    if len(parts) >= 3:
        return parts[2]
    return None


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


def filter_section_entries(lines: list[str], keep_selection: PlatformSelection) -> tuple[list[str], set[str]]:
    output: list[str] = []
    kept_specs: set[str] = set()
    index = 0

    while index < len(lines):
        key = entry_key(lines[index])
        if key is None:
            if not mentions_removed_platform(lines[index], keep_selection.platforms):
                output.append(lines[index])
            index += 1
            continue

        entry, next_index = read_entry(lines, index)
        matching_specs = keep_selection.matching_specs(key)
        if matching_specs and not entry_mentions_noop(entry):
            output.extend(entry)
            kept_specs.update(matching_specs)
        index = next_index

    return output, kept_specs


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
    keep_selection = platforms if isinstance(platforms, PlatformSelection) else normalize_platforms(platforms)
    output: list[str] = []
    kept_library_specs: set[str] = set()

    for name, section_lines in split_sections(text):
        if name in {"libraries", "dependencies"}:
            header = section_lines[:1]
            body = section_lines[1:]
            filtered_body, kept_specs = filter_section_entries(body, keep_selection)
            if name == "libraries":
                kept_library_specs.update(kept_specs)
            output.extend(header)
            output.extend(filtered_body)
        else:
            output.extend(section_lines)

    missing_specs = keep_selection.requested_specs - kept_library_specs
    if missing_specs:
        missing = ", ".join(sorted(missing_specs))
        raise RepackageError(f"Requested platform(s) have no library entries: {missing}")

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


def referenced_resource_paths(text: str) -> set[Path]:
    return {Path(match.group()[len("res://") :]) for match in RESOURCE_RE.finditer(text)}


def file_architecture(platform: str, path: Path) -> str | None:
    pattern = ARCHITECTURE_FILE_RE_BY_PLATFORM.get(platform)
    if pattern is None:
        return None
    match = pattern.match(path.name)
    if match is None:
        return None
    return match.group(1)


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


def prune_platform_directories(root: Path, keep_selection: PlatformSelection) -> None:
    bin_root = root / BIN_ROOT
    if bin_root.is_dir():
        for child in bin_root.iterdir():
            if not child.is_dir():
                continue
            if child.name not in keep_selection.platforms:
                shutil.rmtree(child)
                continue
            keep_architectures = keep_selection.architectures_by_platform.get(child.name)
            if keep_architectures is not None and child.name not in keep_selection.all_platforms:
                for architecture_dir in child.iterdir():
                    if architecture_dir.is_dir() and architecture_dir.name not in keep_architectures:
                        shutil.rmtree(architecture_dir)
                        continue

                    architecture = file_architecture(child.name, architecture_dir)
                    if architecture_dir.is_file() and architecture is not None and architecture not in keep_architectures:
                        architecture_dir.unlink()

    for platform, directories in SUPPORT_DIRS_BY_PLATFORM.items():
        if platform in keep_selection.platforms:
            continue
        for directory in directories:
            shutil.rmtree(root / directory, ignore_errors=True)


def validate_tree(root: Path, keep_selection: PlatformSelection) -> None:
    gdextension_path = root / GDEXTENSION_PATH
    if not gdextension_path.is_file():
        raise RepackageError(f"Missing required file: {GDEXTENSION_PATH}")

    gdextension_text = gdextension_path.read_text(encoding="utf-8")
    removed_platforms = (SUPPORTED_PLATFORMS | {NOOP_PLATFORM}) - keep_selection.platforms
    remaining_removed_entries = sorted(
        key
        for key in gdextension_entry_keys(gdextension_text)
        if platform_prefix(key) in (SUPPORTED_PLATFORMS | {NOOP_PLATFORM}) and not keep_selection.matches_entry(key)
    )
    if remaining_removed_entries:
        entries = ", ".join(remaining_removed_entries)
        raise RepackageError(f"gdextension still references removed platform entries: {entries}")

    lowered_text = gdextension_text.lower()
    for platform in sorted(removed_platforms):
        if platform in lowered_text:
            raise RepackageError(f"gdextension still references removed platform: {platform}")

    bin_root = root / BIN_ROOT
    if bin_root.is_dir():
        unexpected_bin_dirs = sorted(
            child.name for child in bin_root.iterdir() if child.is_dir() and child.name not in keep_selection.platforms
        )
        if unexpected_bin_dirs:
            directories = ", ".join(str(BIN_ROOT / name) for name in unexpected_bin_dirs)
            raise RepackageError(f"Removed platform directory remains: {directories}")

        for platform, keep_architectures in keep_selection.architectures_by_platform.items():
            if platform in keep_selection.all_platforms:
                continue
            platform_dir = bin_root / platform
            if not platform_dir.is_dir():
                continue
            unexpected_architecture_dirs = sorted(
                child.name for child in platform_dir.iterdir() if child.is_dir() and child.name not in keep_architectures
            )
            if unexpected_architecture_dirs:
                directories = ", ".join(str(BIN_ROOT / platform / name) for name in unexpected_architecture_dirs)
                raise RepackageError(f"Removed platform architecture directory remains: {directories}")

            unexpected_architecture_files = sorted(
                child.name
                for child in platform_dir.iterdir()
                if child.is_file()
                and (architecture := file_architecture(platform, child)) is not None
                and architecture not in keep_architectures
            )
            if unexpected_architecture_files:
                files = ", ".join(str(BIN_ROOT / platform / name) for name in unexpected_architecture_files)
                raise RepackageError(f"Removed platform architecture file remains: {files}")

    removed_directories = []
    for platform, directories in SUPPORT_DIRS_BY_PLATFORM.items():
        if platform not in keep_selection.platforms:
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


def validate_output_zip(output_path: Path, keep_selection: PlatformSelection) -> None:
    if not output_path.is_file():
        raise RepackageError(f"Output archive was not created: {output_path}")

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        with zipfile.ZipFile(output_path) as archive:
            if not archive.namelist():
                raise RepackageError("Output archive is empty")
            safe_extract(archive, root)
        validate_tree(root, keep_selection)


def repackage_archive(input_path: os.PathLike[str] | str, output_path: os.PathLike[str] | str, platforms: Iterable[str]) -> None:
    keep_selection = normalize_platforms(platforms)
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
        gdextension_path.write_text(filter_gdextension_text(gdextension_text, keep_selection), encoding="utf-8")

        prune_platform_directories(root, keep_selection)
        validate_tree(root, keep_selection)
        write_zip(root, output_path)

    validate_output_zip(output_path, keep_selection)


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
        help="Platform or platform.architecture to keep. Repeat for multiple targets.",
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
