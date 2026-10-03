# Sentry Godot Repackaged Releases

This repository republishes
[`getsentry/sentry-godot`](https://github.com/getsentry/sentry-godot) releases as
[`gpm`](https://github.com/cafecito-games/godot-package-manager) slice archives, so a Godot
project downloads the addon's scripts and scenes plus only the platform binaries it
actually ships to.

Upstream publishes one 39 MB archive carrying every platform. A sliced release instead
carries a mandatory `core` archive of 80 KB and one archive per platform, listed in a
`gpm-index.toml` asset beside them. A project that exports to Linux and Android downloads
`core`, `linux.x86_64`, `android.arm64` and the shared Android plugin — not Windows, not
iOS. That is 5 MB rather than 39 MB.

## Consuming it

Needs `gpm` 0.5.0 or newer. Declare it in the project's `addons.toml`. Because the release
carries a `gpm-index.toml` asset, `gpm` discovers that the addon is sliced with no extra
configuration:

```toml
[project]
platforms = ["linux.x86_64", "windows.x86_64", "android.arm64", "ios"]

[addons.sentry]
source = "github-release"
repo = "cafecito-games/sentry-godot"
version = "2.3.0"
```

```bash
gpm install
```

`gpm install` fetches `core`, every platform declared in `platforms`, and the host's own
slice — the host slice so that opening the project in the editor loads a library whatever
the export targets are. Tags are matched exactly: a project declaring `ios.arm64` does not
match the published `ios` slice.

A project that does not use the C# integration can skip the managed assemblies in `core`:

```toml
[addons.sentry]
exclude = ["dotnet"]
```

### Published archives

| Archive | Contents |
| --- | --- |
| `core` | Scripts, scenes, the C# integration, and the `.gdextension` body |
| `android.arm32` `android.arm64` `android.x86_32` `android.x86_64` | Android libraries |
| `shared:android` | The Android plugin `.aar`s, pulled in automatically by any Android slice |
| `ios` | iOS xcframeworks, including the bundled Sentry framework |
| `linux.arm64` `linux.x86_32` `linux.x86_64` | Linux libraries and `crashpad_handler` |
| `macos` | macOS dylibs and the bundled Sentry dylib |
| `web.wasm32` | Threaded and non-threaded WebAssembly libraries and the JavaScript bundle |
| `windows.arm64` `windows.x86_32` `windows.x86_64` | Windows DLLs, `crashpad_handler.exe` and `crashpad_wer.dll` |

That is every platform upstream 2.3.0 ships.

Nothing here curates platforms. The slice set is derived from the upstream `.gdextension`
on every run, so an architecture upstream adds is published without a change here, and one
upstream drops stops being published. Projects pick their platforms in their own
`addons.toml`.

Two things are absent, and neither is a decision about platforms:

- **The noop placeholder libraries.** Upstream ships `bin/noop/` to keep Godot quiet on
  platforms Sentry does not support. Publishing it as a `linux.rv64` slice would claim
  support that is not there.
- **Debug symbols.** Upstream publishes those as a separate release asset, which this
  repository does not republish.

`packaging/unsupported-platforms.txt`, which once held `web`, is now empty. It stays as the
escape hatch for a platform `gpm` cannot package at all.

Two kinds of file are placed by `[package.slices]` rules rather than by a `.gdextension` key,
because no key references them and anything unclaimed falls into `core`:

- **The Android plugin `.aar`s.** One pair of files serves all four ABIs, so naming `android`
  generically publishes them once as a shared artifact that every `android.*` slice pulls in
  automatically. No ABI can install without the plugin, the 76 KB is stored once rather than
  four times, and a project selecting two ABIs downloads it once. Shared artifacts are what
  make the index format 2, and cannot be named in a project's `addons.toml` — they follow
  the slice that needs them.
- **The web JavaScript bundle.** 1 MB, and useless anywhere but web, so it is claimed for
  `web.wasm32`.

## Releases

Release tags match upstream tags exactly: upstream `getsentry/sentry-godot@2.3.0` is
republished here as `2.3.0`. Each release carries `gpm-index.toml` and the slice archives,
named `sentry-<version>-<slice>.zip`. The index must stay beside its archives, because a
consumer resolves each archive by joining the index's `file` name to the directory the
index came from. The shared Android artifact is published alongside them as
`sentry-<version>-shared-android.zip`.

The index is format 2, which `gpm` 0.5.0 introduced for shared artifacts. An older `gpm`
refuses it outright — `unsupported index format 2`, exit code 4 — rather than installing a
tree missing the Android plugin.

The repository does not vendor upstream addon contents in git. Slice archives exist only as
GitHub release assets.

## Workflow

The `Repackage Sentry Godot Release` workflow runs once a day and checks for the newest
upstream release that is not already published here. If there is nothing new, it exits
without downloading or publishing anything.

You can also run it manually with an upstream version tag, such as `2.3.0`, to force that
specific release. Leave the manual version input empty to run the same "sync the newest
missing release" behavior as the daily schedule.

The workflow:

1. Runs the unit tests.
2. Installs the pinned `gpm` release, verified against its published checksums. The version
   floor also lives in `scripts/package-addon`, because `gpm`'s `.gdextension` key handling
   and its slice fan-out are release contracts and a local Homebrew install drifts from CI's.
3. Runs the packaging self-test, which cuts a synthetic addon twice and checks the result.
4. Fetches upstream and local release metadata with `gh`.
5. Resolves the addon zip asset with `tools/resolve_upstream_release.py`.
6. Downloads the upstream addon zip.
7. Cuts slices with `scripts/package-addon`.
8. Creates or updates this repository's release under the same tag, removing assets left
   over from an earlier cut of that tag.

`gh` is used for the GitHub API requests because GitHub-hosted runners already provide the
CLI, authentication, and useful API error handling. Python is still used for the
repository-specific asset selection rule because that logic is important and unit tested:
it must select exactly the addon asset named like `sentry-godot-<version>+<sha>.zip` and
reject demo project and debug symbol assets.

## How the cut is produced

`gpm` has no exclusion mechanism: every file the `.gdextension` does not claim lands in
`core`, which every consumer downloads on every platform. So the cut never packages the
upstream tree directly.

1. `tools/stage_addon_tree.py` extracts the upstream zip into a staging directory, rewrites
   `addons/sentry/sentry.gdextension` to drop the noop entries and any platform in
   `packaging/unsupported-platforms.txt`, prunes the matching binaries, and validates that
   every surviving `res://` reference resolves to a file on disk. It also classifies every key
   the way `gpm` will, so a key `gpm` could not partition fails here with the key in the
   message rather than inside `gpm package`.

   Any file under `bin/` that no surviving entry references is placed by a `[package.slices]`
   rule or falls into `core`. The kept ones (`*.aar`) and the dropped ones (`*.so.debug`,
   `*.pdb`, `*.dSYM`) are listed explicitly, and anything else is an error rather than a
   guess — silently dropping a file Godot needs is how the Android `.aar`s would have been
   lost.
2. `scripts/package-addon` copies `packaging/gpm-package.toml` into the staged tree and runs
   `gpm package`. That manifest lives under `packaging/` rather than at the repository root
   on purpose, so a bare `gpm package` fails instead of publishing an unstaged tree.
3. `tools/verify_gpm_cut.py` checks the result before it is published: the index header, that
   the published slice set is exactly `core` plus the slices the staged `.gdextension` implies,
   that every slice and shared-artifact archive matches its declared size and checksum, that
   `core` carries nothing under `bin/` or `web/`, that every architecture slice of a shared
   artifact's platform references it, that no two archives ship the same path, and that every
   `res://` path the index declares is actually shipped in the slice that declares it.

## Local Usage

Cut slices from an upstream archive:

```bash
scripts/package-addon \
  --input sentry-godot-2.3.0+8ec9083.zip \
  --version 2.3.0 \
  --out dist
```

This needs `gpm` 0.5.0 or newer on `PATH`:

```bash
brew install cafecito-games/tap/gpm
```

Stage the addon tree without packaging it, to inspect what would go into a cut. It prints the
slices the staged tree will publish:

```bash
python3 tools/stage_addon_tree.py \
  --input sentry-godot-2.3.0+8ec9083.zip \
  --output-dir /tmp/staged
```

Resolve an upstream release JSON file into GitHub Actions environment values:

```bash
gh api repos/getsentry/sentry-godot/releases/tags/2.3.0 > upstream-release.json
python3 tools/resolve_upstream_release.py \
  --release-json upstream-release.json \
  --version 2.3.0 \
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

## Tests

```bash
python3 -m unittest discover -s tests -v   # no network, no gpm needed
scripts/test-package-addon                 # needs gpm; cuts a synthetic addon twice
```

The unit tests use small synthetic fixtures and do not download upstream release assets.
`scripts/test-package-addon` covers what they cannot: that `gpm` partitions this addon's
`.gdextension` into exactly the promised slice set, and that two cuts of one tree write
identical bytes.
