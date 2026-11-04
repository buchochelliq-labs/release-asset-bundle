# Versions and changelog

## How release-kit is versioned

release-kit is tagged `v1.x.y`:

- a **patch** (`v1.2.3` → `v1.2.4`) fixes a bug;
- a **minor** (`v1.2.x` → `v1.3.0`) adds something that existing
  `release-kit.yml` files and `release.yml` workflows keep working with;
- a **major** (`v2.0.0`) needs repositories to change their configuration or
  workflow. Each repository moves when it installs the new version.

An installed copy records where it came from in
`.github/release-kit/manifest.json`:

```json
{
  "source": "buchochelliq-labs/release-kit",
  "version": "v1.1.0-1-g874e21c",
  "commit": "874e21cbe8bdf07c9f1a613e116fbd7542950b41",
  "files": {
    ".github/release-kit/scripts/release.py": "<sha256>",
    "...": "..."
  }
}
```

`version` is `git describe --tags --always --dirty` of the release-kit
checkout it was installed from: `v1.1.0` from a tag, `v1.1.0-1-g874e21c` from
a commit after it, and one from a checkout with local edits ends in
`-dirty`. `files` maps every installed file to its SHA-256; `release.py
vendored` compares against it.

To move a repository to a new version: pull release-kit, check out the tag you
want, run `install`, and open a PR.

## Changelog

### Unreleased

**Open VSX is checked like the Marketplace.** A `vscode` package takes
`open-vsx: true`: Open VSX is then part of its publication. The version is
due until both registries have it, `preflight` skips the registry that
already has it (so a re-run uploads only the missing one) and refuses an
Open VSX upload without a token, and `verify` waits for
`open-vsx.org/api/<publisher>/<name>/<version>` too. Add `open-vsx: true` to
a package published to Open VSX: without it, the upload still happens when
`OVSX_PAT` is set, with a warning, but is not checked.

**A machine-readable release record.** Every release run uploads the artifact
`release-record`, holding `release-record.json`: the release, its tag and
commit, the run, each job's result, and every package's outcome
(`published`, `already-published`, `failed`, `skipped` or `unknown`), its
registry URL and the SHA-256 of the files built for it, with an overall
`result` (`complete`, `partial`, `nothing-published`). A `vscode` package
with `open-vsx: true` counts as published only on both registries, and also
gets `open_vsx_url`. A coordinator
releasing a project across repositories downloads it with `gh run download
<run> -n release-record`. A new last job in the template, `record`, writes it
with the new `release.py record` command, even when publishing fails, with no
new permissions. The record is versioned (`schema_version: 1`), and its JSON
Schema is published as
[`release-record.schema.json`](release-record.schema.json). **Installed
repositories must add the `record` job to their `release.yml`**: `install`
does not change an existing one. Copy it from `templates/release.yml`. See
[The release record](release-record.md).

**Promote reviewed CI artifacts.** A new artifact kind, `ci-artifact`,
publishes the files of a pinned GitHub Actions artifact unchanged instead of
building them again: for releases that must ship exactly the bytes CI built
and tested. The archive's SHA-256 is pinned, an expired artifact is refused,
and the tagged commit must be the commit that built it or a descendant that
changes only `release-kit.yml`, the pin itself (rule 4). Promoted files must
carry exactly the package's version. The build workflow and the template's
`build` job gain `actions: read`; add it to the `build` job of an installed
`release.yml` to use this kind.

**GitHub releases are drafts until complete.** `publish-github` creates the
release as a draft, uploads and checks every file, then publishes it. A
draft does not count as published, so a run that stopped halfway is
finished by the next one instead of leaving an incomplete public release.

**Commands run in bash on Windows.** `prepare`, `test`, `command` and
`notes` run as `bash -c <command>`. They used `shell=True` with bash as the
executable, which on Windows still builds `<shell> /c "..."`, so they
failed on Windows runners.

**GitHub release options.** A `github` package takes `notes` (a command
whose output is the release's notes, for example a CHANGELOG section;
otherwise GitHub generates them), `checksums` (one `SHA256SUMS`-style file
over every asset, uploaded and checked with them) and `attest: true` (build
provenance attestations for every asset). The template's `publish-github`
job gains `id-token: write` and `attestations: write`; add them to an
installed `release.yml` to use `attest`.

**Homebrew and Chocolatey.** A `github` package takes `homebrew` (a
formula rendered from a template and the release's checksums, attached to the
release and proposed to a tap as a pull request) and `chocolatey` (a package
rendered from a directory of templates, installed and tested on Windows, then
pushed). The template gains the `publish-homebrew` and `publish-chocolatey`
jobs, the gate the `homebrew` and `chocolatey` outputs, and `release.py` the
`render` and `homebrew-tap` commands.

**`prepare`.** Every artifact kind takes a `prepare` command, run in the
build job before anything is built (for `maturin`, before maturin-action):
generated files the build ships, or tools it needs.

**musl targets.** `x86_64-unknown-linux-musl` and
`aarch64-unknown-linux-musl` have default runners, and a `rust-binary` musl
build installs `musl-tools` so C dependencies link statically.

**maturin from a workspace.** A `pyproject.toml` with `dynamic =
["version"]` takes its version from the crate maturin builds (in PEP 440
form). When `[tool.maturin] manifest-path` names the crate, maturin runs in
the `pyproject.toml`'s directory and finds it. Wheels build with `--locked`.

**New crates and crates.io's rate limit.** New crates publish one at a time;
a `429` for too many new crates waits until the time crates.io names, then
tries again.

**crates.io first versions.** `preflight --registry crates.io` now says which
selected crates do not exist yet, as the outputs `new_crates`, `new_crate` and
`existing_crate`. The new `release.py publish-crates` dry-runs every selected
crate, then publishes each consecutive run of crates that share a credential,
in order: the Trusted Publishing token for crates already on crates.io, and
the `crates-io` environment's `CARGO_REGISTRY_TOKEN` secret for a crate's
first version. It refuses before uploading anything if a needed token is
missing. The template requests a Trusted Publishing token only when an
existing crate is selected, and passes the secret only when a new one is.

**Tag ownership.** A package tag already on the remote at another commit must
be that package's own record, whose message starts
`<name> <version> (<registry>)`. Otherwise `tag` and the gate refuse, and say
how to fix it: a tag format of its own (for example `tag: "python-v{version}"`
for a PyPI package named like a crate), or a version bump. A tag on a commit
with no `release-kit.yml`, from before the repository adopted release-kit, is
refused with an explanation instead of a git error.

**Pinned actions.** Every third-party action in the kit and the template is
pinned to a commit SHA with its version in a comment. A test refuses an
unpinned `uses:`, and Dependabot keeps the pins current.

**Consumer checks.** The new `release.py consume` uses each published package
as a stranger would, from the registry with no credentials: `cargo install`
or a consumer crate for crates.io, `pip install` into a new virtualenv for
PyPI, and `npm view` for npm. Each attempt is retried while a registry's index
catches up. The crates.io and PyPI publish actions run it after `verify`.

**Verify-only runs.** The release workflow's manual dispatch has a
`verify_only` input. The gate (`gate --verify-only`) then selects the
release's published packages, the build is skipped, and each publish action
(with its new `verify-only` input) only verifies.

**Artifact tests.** Every artifact kind takes a `test`: a command, or
`{run, targets}` to run it on some targets only. It runs in the build job on
the files just built, with `DIST`, `FILES`, `VERSION`, `PACKAGE` and `TARGET`
set, before anything can publish them.

**Waiting.** `tag --timeout` defaults to 10800 seconds (three hours). When it
stops while the run is still live, it names the run; running `tag` again keeps
waiting and changes nothing while the run is live.

**Every publish job waits for the build.** `publish-crates` and `publish-npm`
now need `build` too, so a release whose artifacts fail to build or fail their
`test` publishes nothing anywhere. `publish-npm` runs `consume` after a normal
upload, and once in a verify-only run. `check` refuses `test.targets` on kinds
that are not built per target, where it could never match.

**Documentation.** This site, built with Zensical from `mkdocs.yml` and
published to GitHub Pages by `.github/workflows/docs.yml`. The README is now a
short summary that links here, and no longer claims the repository is
private.

### v1.1.0

**Installed copies.** Repositories use the kit as a copy under
`.github/release-kit/`, written by `release.py install` with a
`manifest.json` of checksums, plus `.github/workflows/release-kit-build.yml`
and a starter `release.yml`. `release.py vendored` fails CI when the copy is
edited.

### v1.0.0

**The release standard.** `release-kit.yml` declares each package, its
registry, manifest, version and the artifacts it is built into; `release.py`
checks, syncs, tags, gates, plans, builds, preflights and verifies; composite
actions and a build workflow publish to GitHub Releases, crates.io, PyPI, npm
and the VS Code Marketplace.
