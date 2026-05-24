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


def select_addon_asset(release: Mapping[str, Any], version: str) -> Mapping[str, Any]:
    pattern = addon_asset_pattern(version)
    assets = release_assets(release)
    matches = [asset for asset in assets if pattern.fullmatch(str(asset.get("name", "")))]

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
        "UPSTREAM_TAG": version,
        "UPSTREAM_RELEASE_URL": release_url,
        "ASSET_NAME": asset_name,
        "DOWNLOAD_URL": download_url,
        "OUTPUT_NAME": f"{asset_name[:-4]}-mobile.zip",
    }


def write_github_env(values: Mapping[str, str], github_env: Path) -> None:
    github_env.parent.mkdir(parents=True, exist_ok=True)
    with github_env.open("a", encoding="utf-8") as env_file:
        for key in ENV_KEYS:
            value = values[key]
            if "\n" in value or "\r" in value:
                raise ResolveError(f"Refusing to write multiline GitHub env value for {key}")
            env_file.write(f"{key}={value}\n")


def load_release_json(path: Path) -> Mapping[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ResolveError("Release metadata JSON must be an object")
    return data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve a Sentry Godot upstream release asset from GitHub release JSON.",
    )
    parser.add_argument("--release-json", required=True, type=Path, help="Path to upstream release JSON.")
    parser.add_argument("--version", required=True, help="Upstream release tag, for example 1.6.0.")
    parser.add_argument("--github-env", required=True, type=Path, help="Path to the GitHub Actions env file.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        values = resolve_release(load_release_json(args.release_json), args.version)
        write_github_env(values, args.github_env)
    except (OSError, json.JSONDecodeError, ResolveError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"Resolved {values['ASSET_NAME']} -> {values['OUTPUT_NAME']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
