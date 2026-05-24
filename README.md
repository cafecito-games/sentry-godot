# Sentry Godot Repackaged Releases

This repository republishes selected release archives from
[`getsentry/sentry-godot`](https://github.com/getsentry/sentry-godot) with only
the platform bindings needed by Cafecito Games projects.

Upstream Sentry Godot release assets include bindings for several platforms.
This repository keeps:

- Android
- macOS
- iOS
- Linux x86_64

It removes unsupported platform binaries and updates
`addons/sentry/sentry.gdextension` so the addon no longer references removed
files.

## Releases

Release tags match upstream tags exactly. For example, upstream
`getsentry/sentry-godot@1.6.0` is republished here as `1.6.0`.

The repackaged asset name keeps the historical `-mobile` suffix:

```text
sentry-godot-1.6.0+4e3e3e5-mobile.zip
```

The repository does not vendor upstream addon contents in git. Generated zips
are published as GitHub release assets only.

## Workflow

The `Repackage Sentry Godot Release` workflow runs once a day and checks for the
newest upstream `getsentry/sentry-godot` release that is not already published
in this repository. If there is nothing new, the workflow exits without
downloading or publishing an asset.

You can also run the workflow manually. Provide an upstream version tag to force
that specific release, such as:

```text
1.6.0
```

Leave the manual version input empty to run the same "sync the newest missing
release" behavior used by the daily schedule.

The workflow:

1. Runs the local unit tests.
2. Fetches upstream and local release metadata with `gh`.
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

Resolve the newest upstream release that is missing locally:

```bash
gh api --paginate --slurp \
  "repos/getsentry/sentry-godot/releases?per_page=100" > upstream-releases.json
gh release list --limit 1000 --json tagName > local-releases.json
python3 tools/resolve_upstream_release.py \
  --upstream-releases-json upstream-releases.json \
  --local-releases-json local-releases.json \
  --github-env /tmp/github-env
```

Repackage an addon archive directly:

```bash
python3 tools/repackage_sentry_godot.py \
  --input sentry-godot-1.6.0+4e3e3e5.zip \
  --output sentry-godot-1.6.0+4e3e3e5-mobile.zip \
  --platform android \
  --platform macos \
  --platform ios \
  --platform linux.x86_64
```

## Tests

Run all tests with:

```bash
python3 -m unittest discover -s tests -v
```

The tests use small synthetic fixtures and do not download upstream release
assets.
