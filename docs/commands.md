# Commands

`release.py` is one script with subcommands. Run it from anywhere inside the
repository (it works from the repository root, found with
`git rev-parse --show-toplevel`):

```sh
python3 .github/release-kit/scripts/release.py <command> [options]
```

It needs Python 3.11 or newer, git and PyYAML. It exits 0 on success and 1 on
any refusal, printing `error: <reason>` to standard error.

People run the [person commands](#person-commands). The release workflow runs
the [CI commands](#ci-commands); you can run them by hand to see what a run
would do.

## Person commands

### `check`

```sh
release.py check [--offline] [--publish-dry-run]
```

Checks `release-kit.yml` against the repository's manifests: versions,
names, tag formats, dependency order and artifacts. The full list is in the
[reference](configuration.md#what-check-refuses). On success it prints
`release-kit.yml matches the repository (release 0.1.0).`; otherwise it prints
one problem per line and exits 1. Run it in CI.

For crates.io packages, it then checks what `cargo publish` would refuse:
manifests, dependencies, who owns the name, and what each package carries
(see [Publishing to crates.io](registries/publishing-to-crates-io.md)). It
also warns, on lines starting `warning:`, about library crates in the
workspace that `release-kit.yml` does not publish. Warnings do not fail it.

| Option | Meaning |
|---|---|
| `--offline` | Skip the checks that ask crates.io. `RELEASE_KIT_OFFLINE=1` does the same. |
| `--publish-dry-run` | Also run `cargo publish --dry-run --locked` over every crates.io package. This builds them, so use it in CI. |

### `sync`

```sh
release.py sync
```

After you bump versions in the manifests, rewrites each package's `version:`
line in `release-kit.yml` to match, quoted, keeping comments and layout. It
prints one line per change (`onto: 0.1.0 -> 0.2.0`) or `Nothing to change.`

### `status`

```sh
release.py status
```

Prints the release, its tag and where the tag is on the remote, then one line
per package: the package, its record tag, the commit that tag is on (or
`untagged`), and `published` or `due`.

```text
Release 0.2.0 (tag release-0.2.0: not tagged)
  onto 0.2.0 (crates.io)                           onto-v0.2.0                  untagged             due
  onto-cli 0.2.0 (crates.io)                       onto-cli-v0.2.0              untagged             due
```

It asks each registry over HTTPS, so it needs network access.

### `tag`

```sh
release.py tag [--commit SHA] [--release X.Y.Z] [--remote NAME] [--timeout SECONDS] [--dry-run]
```

Tags the release and waits for its run to publish it.

| Option | Default | Meaning |
|---|---|---|
| `--commit SHA` | | Release this commit. |
| `--release X.Y.Z` | | Release (or finish) this release name. |
| `--remote NAME` | `origin` | The remote to read tags from and push them to. |
| `--timeout SECONDS` | `10800` (3 hours) | How long to wait for the run. Run `tag` again to keep waiting. |
| `--dry-run` | off | Print what would be tagged, moved or waited for; push nothing and wait for nothing. |

**Which commit.** In order:

1. `--commit`, if given.
2. Otherwise, a release that is already tagged and not finished: the one named
   by `--release`, or else the newest tagged release (by version) if it
   still has a due package. `tag` goes back to that commit. A tag whose run
   provably published nothing does not hold its commit.
3. Otherwise, with `--release`, the last commit on `<remote>/<branch>`
   (first-parent history) whose `release-kit.yml` names that release.
4. Otherwise `HEAD`.

The commit must be on `<remote>/<branch>` **as last fetched** (`tag` does not
fetch the branch): run `git pull` or `git fetch` first.

**What it checks.** It refuses when a package tag on the remote is not that
package's own record ([collisions](configuration.md#record-tags-and-collisions)),
when `release-kit.yml` at the commit fails `check`, and when the release would
be split (rule 4).

**What it does**, one line per package:

| Line | Meaning |
|---|---|
| `skip   <tag>` | the package is already published |
| `keep   <tag>` | the record tag is already on this commit |
| `tag    <tag>` | a new record tag on this commit |
| `move   <tag>` | the record tag is on another commit whose run published nothing; it is moved here |
| `wait   <tag>` | the record tag is on another commit that ships the same files, and that commit's run may still publish it; `tag` waits for that run |

Then the release tag:

| Line | Meaning |
|---|---|
| `tag    release-X` | a new release tag, which starts the run |
| `move   release-X` | it was on another commit whose run ended before publishing; it is moved here |
| `retag  release-X` | it is on this commit but its run failed; it is pushed again, starting a new run that publishes only what is still due |
| `wait   release-X` | it is on this commit and its run is live (or succeeded); `tag` only waits |

Tags are annotated. A record tag's message is:

```text
onto-cli 0.2.0 (crates.io)

Release: 0.2.0
Commit: 9f1c2e7d4b5a6c3e8f0a1b2c3d4e5f6a7b8c9d0e
```

The release tag's message starts `Release 0.2.0`, lists the packages it
publishes, and ends with the same `Release:` and `Commit:` lines.

`tag` deletes moved tags in one atomic push, pushes all record tags in a second
atomic push, then pushes the release tag on its own (GitHub starts no workflow
for a push of more than three tags).

**Waiting.** `tag` then polls every 30 seconds until the run has succeeded and
every due package is on its registry. If the run fails, it stops and says how
many packages were published. If `--timeout` passes while the run is still
going, it says so and names the run; run `tag` again to keep waiting. Running
`tag` while a run is live changes nothing: it prints `wait` and waits.

`tag` follows runs with the GitHub CLI (`gh run list`, `gh api`). Without `gh`
it cannot see runs, so it never proves that a run published nothing: it does
not move or retag tags, and it waits on the registries alone. See
[Recovering a failed release](recovery.md).

### `install`

```sh
python3 ../release-kit/scripts/release.py install [--into DIR]
```

Run from a release-kit checkout's script, in the repository to install into
(or name it with `--into`). It refuses to run from an installed copy, and to
install into release-kit itself. It replaces `.github/release-kit/`, writes
`.github/workflows/release-kit-build.yml`, writes a starter
`.github/workflows/release.yml` only if there is none, and prints every path
written. The version recorded is `git describe --tags --always --dirty` of
the checkout. See [Getting started](getting-started.md#1-install-the-copy).

### `vendored`

```sh
release.py vendored
```

Checks the installed copy against `.github/release-kit/manifest.json`. It
fails if the manifest is missing, if a listed file is missing or edited, or if
a file not in the manifest was added under `.github/release-kit/`
(`__pycache__` is ignored). On success it prints
`release-kit v1.2.0: the installed copy is unmodified.` Run it in CI.

## CI commands

These take the gate's *selection*: a JSON list with one object per selected
package, in `release-kit.yml` order:

```json
[{"name": "onto", "registry": "crates.io", "version": "0.2.0",
  "manifest": "crates/onto/Cargo.toml", "tag": "onto-v0.2.0", "slug": "onto"}]
```

Commands that write outputs append `key=value` lines to the file named by
`GITHUB_OUTPUT`.

### `gate`

```sh
release.py gate TAG [--remote NAME] [--verify-only]
```

| Option | Default | Meaning |
|---|---|---|
| `TAG` | | the release tag, which must be checked out with full history |
| `--remote NAME` | `origin` | |
| `--verify-only` | off | select the release's published packages, to verify them again |

It refuses unless:

1. `TAG` is an annotated tag;
2. `release-kit.yml` at the tagged commit passes `check`;
3. `TAG` is that commit's release tag;
4. the commit is on `<remote>/<branch>` (fetched first);
5. the tag's message records `Release: <name>` and `Commit: <sha>`;
6. no unpublished package's tag on the remote belongs to something else;
7. the release is not split;
8. at least one package is due;
9. every due package's record tag is on the commit, and annotated.

It prints `selected` or `not selected` for each package.

With `--verify-only`, steps 7–9 are replaced: it selects every package whose
record tag is on the commit, and refuses if none is, or if any of them is not
yet published (`Run release.py tag again to publish them`).

Outputs:

| Output | Value |
|---|---|
| `sha` | the tagged commit |
| `release` | the release's name |
| `selection` | the selection (JSON) |
| `verify-only` | `true` or `false` |
| `github`, `crates-io`, `pypi`, `npm`, `vscode` | `true` when the selection has a package of that registry |

### `plan`

```sh
release.py plan --selection JSON
```

The build matrix: one job per artifact of each selected package, and per
target for `rust-binary` and `maturin`. It prints `build  <id> on <runner>`
per job and writes two outputs: `jobs` (a JSON list for a matrix's `include`)
and `count`.

Each job carries `id`, `kind`, `os`, `target`, `package`, `slug`, `version`,
`registry`, `manifest` and `test`, plus what its kind needs (`bin`, `crate`,
`include`; `cargo`; `dir`; `run`, `outputs`). Job ids:

| Kind | Job id |
|---|---|
| `rust-binary` | `<slug>-rust-binary-<target>` |
| `maturin` | `<slug>-maturin-<target>`, plus `<slug>-sdist` |
| `python`, `vsix`, `command` | `<slug>-<kind>` for a package's first artifact; `<slug>-<kind>-<n>` for the artifact at 0-based position `n` after that |

Two artifacts that plan the same job id are refused.

### `build`

```sh
release.py build --job JSON [--out DIR]
```

Builds one planned job into `--out` (default `dist`), as described per kind
in the [reference](configuration.md#artifacts). Then it runs the artifact's
`test`, if any, and for a `github` package writes a `.sha256` beside every
file. It fails if the job produced no files. For `maturin` jobs the build
action has already run maturin; this step adds the test and checksums.

### `preflight`

```sh
release.py preflight --registry REGISTRY --selection JSON
```

Just before uploading: refuses if any selected package of `REGISTRY` is
already published (`already on crates.io: ...; a published version is
permanent`). Another run may have published it since the gate.

For `crates.io` it first runs `check`'s
[crates.io checks](registries/publishing-to-crates-io.md) on the selected
crates, and refuses (`not ready for crates.io: ...`) before writing any
output. Then it asks crates.io which selected crates do not exist yet,
and writes three outputs:

| Output | Value |
|---|---|
| `new_crates` | JSON list of the selected crates with no version on crates.io |
| `new_crate` | `true` if that list is not empty |
| `existing_crate` | `true` if at least one selected crate already exists |

### `publish-crates`

```sh
RELEASE_TRUSTED_TOKEN=... RELEASE_NEW_CRATE_TOKEN=... \
  release.py publish-crates --selection JSON [--new-crates JSON]
```

| Option | Default | Meaning |
|---|---|---|
| `--selection` | | the gate's selection |
| `--new-crates` | `[]` | `preflight`'s `new_crates` output |

Tokens come from the environment, never the command line:
`RELEASE_TRUSTED_TOKEN` (the Trusted Publishing token) and
`RELEASE_NEW_CRATE_TOKEN` (the token for first versions).

It splits the selected crates, in order, into runs that share a credential:
existing crates use the trusted token, new crates the new-crate token. If a
run's token is missing it refuses before uploading anything. Then it runs one
`cargo publish --dry-run --locked -p A -p B ...` for all of them, and one
`cargo publish --locked -p ...` per run. For example, with `onto` new and
`onto-cli` existing, it publishes `onto` with the new-crate token, then
`onto-cli` with the trusted one. Publishing several crates in one `cargo
publish` needs Cargo 1.90 or later.

### `verify`

```sh
release.py verify --registry REGISTRY --selection JSON [--timeout SECONDS]
```

Waits until every selected package of `REGISTRY` is served, checking every 20
seconds, for up to `--timeout` seconds (default `900`). Prints
`on <registry>: ...`, or fails with `not on <registry> after N minutes`.

What "served" means per registry:

| Registry | Check |
|---|---|
| `github` | the release on the package's tag exists and has at least one asset |
| `crates.io` | `crates.io/api/v1/crates/<name>/<version>` answers 200 |
| `pypi` | `pypi.org/pypi/<name>/<version>/json` answers 200 |
| `npm` | `registry.npmjs.org/<name>/<version>` answers 200 |
| `vscode` | the Marketplace's extension query lists the version |

### `consume`

```sh
release.py consume --registry REGISTRY --selection JSON
```

Uses each selected package as a stranger would: in a fresh temporary
directory, with `CARGO_REGISTRY_TOKEN`, `NODE_AUTH_TOKEN` and
`PYPI_API_TOKEN` emptied.

| Registry | What it does |
|---|---|
| `crates.io`, a crate with a binary | `cargo install <name> --version =<version> --locked` into a temporary root |
| `crates.io`, a library | `cargo new --lib consumer`, `cargo add <name>@=<version>`, `cargo check` |
| `pypi` | a new virtualenv, then `pip install --no-cache-dir <name>==<version>` |
| `npm` | `npm view <name>@<version> version` |
| `github`, `vscode` | nothing (`verify` has already seen the files) |

A crate "has a binary" when its `Cargo.toml` has a `[[bin]]` table or there
is a `src/main.rs` beside it. A registry's index can lag behind its API, so
the install, add or view step is tried up to 10 times, 30 seconds apart.
Prints `used   <package> as a consumer would` per package.
