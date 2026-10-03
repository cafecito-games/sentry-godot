#!/usr/bin/env python3
"""Check a gpm cut before it is published.

Consumers verify every slice byte against their lockfile, so a broken cut fails
on their machines rather than on the machine that produced it. These checks run
on the producing machine instead.
"""
from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping, Sequence
import hashlib
import importlib.util
from pathlib import Path
import sys
import tomllib
from typing import Any
import zipfile


def load_stager():
    """The staged .gdextension is what decides the slice set, so share its reader."""
    path = Path(__file__).resolve().parent / "stage_addon_tree.py"
    spec = importlib.util.spec_from_file_location("stage_addon_tree", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stager = load_stager()


INDEX_NAME = "gpm-index.toml"

# Format 2 adds the shared artifact this addon needs for the Android plugin.
# gpm emits the lowest format a cut can be expressed in, so accept either: a
# future upstream dropping to one Android ABI would legitimately cut format 1.
SUPPORTED_FORMATS = (1, 2)
EXPECTED_PACKAGE_NAME = "sentry"
ADDON_RESOURCE_PREFIX = "res://addons/sentry/"
CORE_SLICE = "core"

# Every consumer downloads core on every platform, so nothing platform-specific
# belongs in it. Both of these are claimed by [package.slices] rules that gpm
# would fail the cut over if they ever stopped matching.
CORE_FORBIDDEN_DIRECTORIES = ("bin/", "web/")


class VerifyError(RuntimeError):
    """Raised when a packaged cut is not safe to publish."""


def load_index(distribution_dir: Path) -> Mapping[str, Any]:
    index_path = distribution_dir / INDEX_NAME
    if not index_path.is_file():
        raise VerifyError(f"Missing index: {index_path}")
    with index_path.open("rb") as index_file:
        index = tomllib.load(index_file)
    if not isinstance(index, Mapping):
        raise VerifyError(f"Index is not a table: {index_path}")
    return index


def verify_index_header(index: Mapping[str, Any], version: str) -> None:
    if index.get("format") not in SUPPORTED_FORMATS:
        supported = ", ".join(str(format_version) for format_version in SUPPORTED_FORMATS)
        raise VerifyError(f"Index format must be one of {supported}, found {index.get('format')!r}")
    if index.get("name") != EXPECTED_PACKAGE_NAME:
        raise VerifyError(f"Index name must be {EXPECTED_PACKAGE_NAME!r}, found {index.get('name')!r}")
    if index.get("version") != version:
        raise VerifyError(f"Index version must be {version!r}, found {index.get('version')!r}")


def index_slices(index: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    slices = index.get("slices")
    if not isinstance(slices, Mapping):
        raise VerifyError("Index is missing a [slices] table")
    for name, slice_table in slices.items():
        if not isinstance(slice_table, Mapping):
            raise VerifyError(f"Slice {name} is not a table")
    return slices


def verify_slice_set(slices: Mapping[str, Mapping[str, Any]], platforms: Iterable[str]) -> None:
    expected = {CORE_SLICE} | set(platforms)
    published = set(slices)

    missing = sorted(expected - published)
    if missing:
        raise VerifyError(f"Cut is missing expected slices: {', '.join(missing)}")

    unexpected = sorted(published - expected)
    if unexpected:
        raise VerifyError(f"Cut publishes unexpected slices: {', '.join(unexpected)}")


def index_artifacts(index: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    artifacts = index.get("artifacts", {})
    if not isinstance(artifacts, Mapping):
        raise VerifyError("Index [artifacts] is not a table")
    for name, artifact_table in artifacts.items():
        if not isinstance(artifact_table, Mapping):
            raise VerifyError(f"Artifact {name} is not a table")

    if index.get("format") == 2 and not artifacts:
        raise VerifyError("Index declares format 2 but no [artifacts], which gpm rejects on load")
    if index.get("format") == 1 and artifacts:
        raise VerifyError("Index declares format 1 with [artifacts], which gpm rejects on load")
    return artifacts


def verify_artifact_references(
    artifacts: Mapping[str, Mapping[str, Any]],
    slices: Mapping[str, Mapping[str, Any]],
) -> None:
    """Every architecture slice of an artifact's platform must claim it.

    A slice left out would install without the shared payload, which for the
    Android plugin means that ABI silently ships without it.
    """
    for artifact_name in sorted(artifacts):
        prefix = f"{artifact_name}."
        architecture_slices = sorted(name for name in slices if name.startswith(prefix))
        if len(architecture_slices) < 2:
            raise VerifyError(
                f"Artifact {artifact_name} is shared by fewer than two architecture slices, "
                "so its payload belongs in the slice itself"
            )

        for name in architecture_slices:
            declared = slices[name].get("artifacts", [])
            if not isinstance(declared, list):
                raise VerifyError(f"Slice {name} declares malformed artifacts")
            if artifact_name not in declared:
                raise VerifyError(
                    f"Slice {name} does not reference artifact {artifact_name}, so that "
                    "architecture would install without its shared payload"
                )

    published = set(artifacts)
    for name, slice_table in sorted(slices.items()):
        for declared in slice_table.get("artifacts", []) or []:
            if declared not in published:
                raise VerifyError(f"Slice {name} references unpublished artifact {declared!r}")


def archive_path_for(distribution_dir: Path, name: str, table: Mapping[str, Any]) -> Path:
    file_name = table.get("file")
    if not isinstance(file_name, str) or not file_name:
        raise VerifyError(f"Archive {name} declares no file")
    if "/" in file_name or "\\" in file_name:
        raise VerifyError(f"Archive {name} declares a path rather than an asset name: {file_name}")

    archive_path = distribution_dir / file_name
    if not archive_path.is_file():
        raise VerifyError(f"Archive {name} names a missing archive: {archive_path}")
    return archive_path


def verify_archive_checksum(name: str, table: Mapping[str, Any], archive_path: Path) -> None:
    contents = archive_path.read_bytes()

    expected_size = table.get("size")
    if expected_size != len(contents):
        raise VerifyError(f"Archive {name} declares size {expected_size!r} but the archive is {len(contents)} bytes")

    expected_digest = table.get("sha256")
    actual_digest = hashlib.sha256(contents).hexdigest()
    if expected_digest != actual_digest:
        raise VerifyError(f"Archive {name} declares sha256 {expected_digest!r} but the archive hashes to {actual_digest}")


def archive_entries(archive_path: Path) -> set[str]:
    with zipfile.ZipFile(archive_path) as archive:
        return {member.filename for member in archive.infolist() if not member.is_dir()}


def verify_core_carries_no_payload(entries: Iterable[str]) -> None:
    unexpected = sorted(
        entry for entry in entries if entry.startswith(CORE_FORBIDDEN_DIRECTORIES)
    )
    if unexpected:
        raise VerifyError(
            "Core slice carries platform payload, so every consumer would download it: "
            f"{', '.join(unexpected)}"
        )


def resource_relative_path(name: str, resource: str) -> str:
    if not resource.startswith(ADDON_RESOURCE_PREFIX):
        raise VerifyError(f"Slice {name} declares a resource outside the addon: {resource}")
    return resource[len(ADDON_RESOURCE_PREFIX) :]


def declared_resources(name: str, slice_table: Mapping[str, Any]) -> set[str]:
    resources: set[str] = set()

    libraries = slice_table.get("libraries", {})
    for gdextension, entries in libraries.items():
        if not isinstance(entries, Mapping):
            raise VerifyError(f"Slice {name} declares malformed libraries for {gdextension}")
        for key, resource in entries.items():
            if not isinstance(resource, str):
                raise VerifyError(f"Slice {name} library {key} is not a resource path")
            resources.add(resource_relative_path(name, resource))

    dependencies = slice_table.get("dependencies", {})
    for gdextension, entries in dependencies.items():
        if not isinstance(entries, Mapping):
            raise VerifyError(f"Slice {name} declares malformed dependencies for {gdextension}")
        for key, dependency in entries.items():
            if not isinstance(dependency, Mapping):
                raise VerifyError(f"Slice {name} dependency {key} is not a dictionary")
            for resource in dependency:
                resources.add(resource_relative_path(name, resource))

    return resources


def verify_resources_are_shipped(name: str, resources: Iterable[str], entries: Iterable[str]) -> None:
    # An entry value may name a bundle directory, such as an .xcframework, so a
    # resource is satisfied either by that exact file or by anything beneath it.
    for resource in sorted(resources):
        prefix = resource.rstrip("/") + "/"
        if resource in entries:
            continue
        if any(entry.startswith(prefix) for entry in entries):
            continue
        raise VerifyError(f"Slice {name} declares res://addons/sentry/{resource} but ships no such file")


def verify_no_shared_paths(entries_by_archive: Mapping[str, set[str]]) -> None:
    """One path in the merged tree may come from one archive only.

    A payload shared by several architecture slices is published once as a
    shared artifact rather than copied into each, so nothing a cut publishes
    ships a path twice, and gpm refuses a tree where anything does.
    """
    # Keyed case-insensitively because a consumer may extract onto a
    # case-insensitive filesystem, where two spellings are one file.
    owners_by_path: dict[str, list[str]] = {}
    for name, entries in entries_by_archive.items():
        for entry in entries:
            owners_by_path.setdefault(entry.casefold(), []).append(name)

    for path, owners in sorted(owners_by_path.items()):
        if len(owners) > 1:
            raise VerifyError(f"Archives {', '.join(sorted(owners))} all ship {path}")


def verify_cut(distribution_dir: Path, version: str, platforms: Iterable[str]) -> None:
    index = load_index(distribution_dir)
    verify_index_header(index, version)
    slices = index_slices(index)
    verify_slice_set(slices, platforms)
    artifacts = index_artifacts(index)
    verify_artifact_references(artifacts, slices)

    entries_by_archive: dict[str, set[str]] = {}
    for name, slice_table in sorted(slices.items()):
        archive_path = archive_path_for(distribution_dir, name, slice_table)
        verify_archive_checksum(name, slice_table, archive_path)
        entries = archive_entries(archive_path)
        if not entries:
            raise VerifyError(f"Slice {name} ships no files")
        entries_by_archive[name] = entries
        verify_resources_are_shipped(name, declared_resources(name, slice_table), entries)

    for name, artifact_table in sorted(artifacts.items()):
        archive_path = archive_path_for(distribution_dir, name, artifact_table)
        verify_archive_checksum(name, artifact_table, archive_path)
        entries = archive_entries(archive_path)
        if not entries:
            raise VerifyError(f"Artifact {name} ships no files")
        entries_by_archive[f"shared:{name}"] = entries

    verify_core_carries_no_payload(entries_by_archive[CORE_SLICE])
    verify_no_shared_paths(entries_by_archive)


def expected_platforms(staged_gdextension: Path) -> set[str]:
    """The slices the staged .gdextension implies gpm should have published.

    Deriving this rather than reading a hand-kept list is the point: upstream
    adding an architecture widens the cut on its own, and upstream dropping one
    narrows it, with neither needing a decision here.
    """
    if not staged_gdextension.is_file():
        raise VerifyError(f"Missing staged .gdextension: {staged_gdextension}")

    try:
        platforms = stager.published_slices(staged_gdextension.read_text(encoding="utf-8"))
    except stager.StageError as error:
        raise VerifyError(str(error)) from error

    if not platforms:
        raise VerifyError(f"The staged .gdextension implies no platform slice: {staged_gdextension}")
    return platforms


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify a packaged gpm cut before publishing it.",
    )
    parser.add_argument("--dist", required=True, type=Path, help="Directory holding the cut gpm-index.toml.")
    parser.add_argument("--version", required=True, help="Version the cut was packaged with.")
    parser.add_argument(
        "--staged-gdextension",
        required=True,
        type=Path,
        help="Path to the staged .gdextension the cut was packaged from.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        verify_cut(args.dist, args.version, expected_platforms(args.staged_gdextension))
    except (OSError, tomllib.TOMLDecodeError, zipfile.BadZipFile, VerifyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"Verified cut in {args.dist}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
