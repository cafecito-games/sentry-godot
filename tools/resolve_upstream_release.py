#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import re
import sys
from typing import Any


ENV_KEYS = (
    "SHOULD_RELEASE",
    "UPSTREAM_TAG",
    "UPSTREAM_RELEASE_URL",
    "ASSET_NAME",
    "DOWNLOAD_URL",
    "OUTPUT_NAME",
)


class ResolveError(RuntimeError):
    """Raised when upstream release metadata cannot be resolved."""


def normalize_version(version: str) -> str:
    normalized = version.strip()
    if not normalized:
        raise ResolveError("Version must not be empty")
    return normalized


def addon_asset_pattern(version: str) -> re.Pattern[str]:
    return re.compile(rf"^sentry-godot-{re.escape(version)}\+[0-9a-f]+\.zip$")


def release_assets(release: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        raise ResolveError("Release metadata field 'assets' must be a list")
    return [asset for asset in assets if isinstance(asset, Mapping)]


def matching_addon_assets(release: Mapping[str, Any], version: str) -> list[Mapping[str, Any]]:
    pattern = addon_asset_pattern(version)
    assets = release_assets(release)
    return [asset for asset in assets if pattern.fullmatch(str(asset.get("name", "")))]


def select_addon_asset(release: Mapping[str, Any], version: str) -> Mapping[str, Any]:
    pattern = addon_asset_pattern(version)
    assets = release_assets(release)
    matches = matching_addon_assets(release, version)

    if len(matches) != 1:
        asset_names = ", ".join(str(asset.get("name", "<unnamed>")) for asset in assets)
        raise ResolveError(
            f"Expected exactly one addon asset matching {pattern.pattern}; "
            f"found {len(matches)}. Assets: {asset_names}"
        )

    return matches[0]


def require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ResolveError(f"Release metadata is missing required field: {field}")
    return value


def resolve_release(release: Mapping[str, Any], version: str) -> dict[str, str]:
    version = normalize_version(version)
    asset = select_addon_asset(release, version)
    asset_name = require_string(asset.get("name"), "asset.name")
    download_url = require_string(asset.get("browser_download_url"), "asset.browser_download_url")

    if not asset_name.endswith(".zip"):
        raise ResolveError(f"Resolved asset is not a zip file: {asset_name}")

    release_url = release.get("html_url")
    if not isinstance(release_url, str) or not release_url:
        release_url = f"https://github.com/getsentry/sentry-godot/releases/tag/{version}"

    return {
        "SHOULD_RELEASE": "true",
        "UPSTREAM_TAG": version,
        "UPSTREAM_RELEASE_URL": release_url,
        "ASSET_NAME": asset_name,
        "DOWNLOAD_URL": download_url,
        "OUTPUT_NAME": f"{asset_name[:-4]}-mobile.zip",
    }


def flatten_json_objects(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, list):
        objects: list[Mapping[str, Any]] = []
        for item in value:
            objects.extend(flatten_json_objects(item))
        return objects
    return []


def local_release_tags(local_releases: Any) -> set[str]:
    tags: set[str] = set()
    for release in flatten_json_objects(local_releases):
        tag = release.get("tagName", release.get("tag_name"))
        if isinstance(tag, str) and tag:
            tags.add(tag)
    return tags


def is_release_candidate(release: Mapping[str, Any]) -> bool:
    return not release.get("draft", False) and not release.get("prerelease", False)


def release_tag_name(release: Mapping[str, Any]) -> str | None:
    tag = release.get("tag_name")
    if isinstance(tag, str) and tag.strip():
        return tag.strip()
    return None


def published_at(release: Mapping[str, Any]) -> str:
    value = release.get("published_at")
    if isinstance(value, str):
        return value
    return ""


def resolve_next_release(upstream_releases: Any, local_releases: Any) -> dict[str, str]:
    synced_tags = local_release_tags(local_releases)
    candidates: list[Mapping[str, Any]] = []

    for release in flatten_json_objects(upstream_releases):
        if not is_release_candidate(release):
            continue

        tag = release_tag_name(release)
        if tag is None or tag in synced_tags:
            continue

        matches = matching_addon_assets(release, tag)
        if len(matches) > 1:
            pattern = addon_asset_pattern(tag)
            raise ResolveError(
                f"Expected at most one addon asset matching {pattern.pattern}; found {len(matches)}"
            )
        if len(matches) == 1:
            candidates.append(release)

    if not candidates:
        return {"SHOULD_RELEASE": "false"}

    newest_release = max(candidates, key=published_at)
    tag = release_tag_name(newest_release)
    if tag is None:
        raise ResolveError("Selected release is missing tag_name")
    return resolve_release(newest_release, tag)


def write_github_env(values: Mapping[str, str], github_env: Path) -> None:
    github_env.parent.mkdir(parents=True, exist_ok=True)
    with github_env.open("a", encoding="utf-8") as env_file:
        for key in ENV_KEYS:
            if key not in values:
                continue
            value = values[key]
            if "\n" in value or "\r" in value:
                raise ResolveError(f"Refusing to write multiline GitHub env value for {key}")
            env_file.write(f"{key}={value}\n")


def load_release_json(path: Path) -> Mapping[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ResolveError("Release metadata JSON must be an object")
    return data


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve a Sentry Godot upstream release asset from GitHub release JSON.",
    )
    parser.add_argument("--release-json", type=Path, help="Path to one upstream release JSON object.")
    parser.add_argument("--version", help="Upstream release tag, for example 1.6.0.")
    parser.add_argument(
        "--upstream-releases-json",
        type=Path,
        help="Path to upstream releases JSON from gh api --paginate --slurp.",
    )
    parser.add_argument(
        "--local-releases-json",
        type=Path,
        help="Path to local release JSON from gh release list --json tagName.",
    )
    parser.add_argument("--github-env", required=True, type=Path, help="Path to the GitHub Actions env file.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.release_json or args.version:
            if not args.release_json or not args.version:
                raise ResolveError("--release-json and --version must be provided together")
            values = resolve_release(load_release_json(args.release_json), args.version)
        else:
            if not args.upstream_releases_json or not args.local_releases_json:
                raise ResolveError(
                    "--upstream-releases-json and --local-releases-json are required when --version is omitted"
                )
            values = resolve_next_release(
                load_json(args.upstream_releases_json),
                load_json(args.local_releases_json),
            )
        write_github_env(values, args.github_env)
    except (OSError, json.JSONDecodeError, ResolveError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if values["SHOULD_RELEASE"] == "true":
        print(f"Resolved {values['ASSET_NAME']} -> {values['OUTPUT_NAME']}")
    else:
        print("No new upstream release to sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
