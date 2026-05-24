# Sentry Godot Mobile Releases

This repository republishes selected release archives from
[`getsentry/sentry-godot`](https://github.com/getsentry/sentry-godot) with only
the platform bindings needed by Cafecito Games projects.

Upstream Sentry Godot release assets include bindings for several platforms.
This repository keeps:

- Android
- macOS
- iOS

It removes unsupported platform binaries and updates
`addons/sentry/sentry.gdextension` so the addon no longer references removed
files.

## Releases

Release tags match upstream tags exactly. For example, upstream
`getsentry/sentry-godot@1.6.0` is republished here as `1.6.0`.

The repackaged asset name adds a `-mobile` suffix:

```text
sentry-godot-1.6.0+4e3e3e5-mobile.zip
```

The repository does not vendor upstream addon contents in git. Generated zips
are published as GitHub release assets only.

## Workflow

Run the `Repackage Sentry Godot Release` workflow manually and provide the
upstream version tag, such as:

```text
1.6.0
```

The workflow:

1. Runs the local unit tests.
2. Fetches upstream release metadata with `gh api`.
3. Resolves the addon zip asset with `tools/resolve_upstream_release.py`.
4. Downloads the upstream addon zip.
5. Repackages it with `tools/repackage_sentry_godot.py`.
6. Creates or updates this repository's release using the same tag.

`gh` is used for the GitHub API request because GitHub-hosted runners already
provide the CLI, authentication, and useful API error handling. Python is still
used for the repository-specific asset selection rule because that logic is
important and unit tested: it must select exactly the addon asset named like
`sentry-godot-<version>+<sha>.zip` and reject demo project assets.

## Local Usage

Resolve an upstream release JSON file into GitHub Actions environment values:

```bash
gh api repos/getsentry/sentry-godot/releases/tags/1.6.0 > upstream-release.json
python3 tools/resolve_upstream_release.py \
  --release-json upstream-release.json \
  --version 1.6.0 \
  --github-env /tmp/github-env
```

Repackage an addon archive directly:

```bash
python3 tools/repackage_sentry_godot.py \
  --input sentry-godot-1.6.0+4e3e3e5.zip \
  --output sentry-godot-1.6.0+4e3e3e5-mobile.zip \
  --platform android \
  --platform macos \
  --platform ios
```

## Tests

Run all tests with:

```bash
python3 -m unittest discover -s tests -v
```

The tests use small synthetic fixtures and do not download upstream release
assets.
