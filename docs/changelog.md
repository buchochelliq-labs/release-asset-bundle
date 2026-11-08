# Versions and changelog

## How rab is versioned

rab (release-kit until 1.7.0) is tagged `vX.Y.Z`:

- a **patch** (`v2.0.0` → `v2.0.1`) fixes a bug;
- a **minor** (`v2.0.x` → `v2.1.0`) adds something that existing
  `rab.yml` files and `release.yml` workflows keep working with;
- a **major** (`v3.0.0`) needs repositories to change their configuration or
  workflow. Each repository moves when it installs the new version.

`release.py --version` prints the version of the script (`rab 2.0.0`). An
installed copy records where it came from in `.github/rab/manifest.json`:

```json
{
  "source": "buchochelliq-labs/release-asset-bundle",
  "version": "v2.0.0",
  "commit": "874e21cbe8bdf07c9f1a613e116fbd7542950b41",
  "files": {
    ".github/rab/scripts/release.py": "<sha256>",
    "...": "..."
  }
}
```

`version` is `git describe --tags --always --dirty` of the checkout it was
installed from: `v2.0.0` from a tag, `v2.0.0-1-g874e21c` from a commit after
it, and one from a checkout with local edits ends in `-dirty`. `files` maps
every installed file to its SHA-256; `release.py vendored` compares against
it, and prints the version (`rab v2.0.0: the installed copy is unmodified.`).

To move a repository to a new version: pull the kit, check out the tag you
want, run `install`, and open a PR. From release-kit 1.x, see
[Migrating from release-kit 1.x](migrating-to-rab.md).

## Changelog

### v2.0.3

**Fixed: `check` missed a crate listed before one it dev-depends on.** Cargo
keeps a dev-dependency that has a `version` (directly or through
`workspace = true`) in the published manifest, so crates.io must already have
it. `check` counted only normal and build dependencies, and the combined
`cargo publish --dry-run` uses a local registry holding every crate, so
open-data-suite's v0.1.3 release passed both, then stopped after four crates:
`ods-health` dev-depends on `ods-provider-fake`, which `rab.yml` listed later.
`check` now counts `[dev-dependencies]` (and `[target.*.dev-dependencies]`)
that carry a version; a path-only one is stripped by cargo and still does not
count.

### v2.0.2

**Fixed: `publish-chocolatey` failed on Windows before packing.** Its bash
step read `jq`'s output, and on Windows `jq` ends each line with CRLF; bash
kept the `\r`, so open-data-suite's v0.1.3 release read the package slug as
`ods\r` and `gh release download` could not create the directory
(`The filename, directory name, or volume label syntax is incorrect`).
The step now strips carriage returns from everything `jq` prints, which also
keeps them out of the package's `test` command. A test runs the step under bash
with a `jq` that writes CRLF.

### v2.0.1

**Fixed: `publish-pypi` uploaded nothing.** It ran
`pypa/gh-action-pypi-publish`, which runs a Docker image it names from the
calling action's repository and ref. Nested in rab's composite action those
are not set, so it fell back to the release's own repository and tag:
rs-rich-cli's first rab release ran `docker run …
ghcr.io/buchochelliq-labs/rs-rich-cli:release-0.0.25` and was `denied`. pypa
documents that the action does not work from a composite action (called
directly from a job, as rs-rich-cli's old workflow did, it works).

`publish-pypi` now does the upload itself, with no Docker action: it
installs twine 7.0.0 and pypi-attestations 0.0.30 into a virtualenv from its
new `requirements.txt` (`pip install --require-hashes`, every package pinned
with its hashes), and runs the new `release.py pypi-upload`. That exchanges
the job's OIDC token for a short-lived PyPI token as
[PyPI documents](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
(`/_/oidc/audience`, GitHub's OIDC token, `/_/oidc/mint-token`), masks it,
signs PEP 740 attestations with `pypi-attestations sign`, prints each file's
digests, and runs `twine upload --attestations`. A refused exchange shows
PyPI's reasons and the token's claims. Attestations are kept, as the pypa
action made them; the new `attestations: "false"` input turns them off. The
trusted publisher on PyPI does not change. See [PyPI](registries/pypi.md#publish).

A test now refuses a Docker-based action (`docker://`,
`pypa/gh-action-pypi-publish`) in any rab composite, and any third-party
action there that has not been read at its pinned commit. The others were
read: `actions/setup-python`, `actions/setup-node` and `PyO3/maturin-action`
are Node actions (maturin-action's manylinux images are named from its
inputs), `dtolnay/rust-toolchain` is a composite of shell steps, and
`actions/attest-build-provenance` is a composite around the Node action
`actions/attest`; none reads `github.action_repository` or `action_ref`.

**To pick it up**, install v2.0.1 (`release.yml` does not change: `install`
adds `.github/rab/publish-pypi/requirements.txt`). A release whose
`publish-pypi` job failed this way published nothing on PyPI: after
installing, run `release.py tag` again and the new run publishes only PyPI.

### v2.0.0

**release-kit is now rab, the Release Asset Bundle** (BAB, after its
[mascot](mascot.md)), at
[`buchochelliq-labs/release-asset-bundle`](https://github.com/buchochelliq-labs/release-asset-bundle).
This major version renames what repositories keep on disk, and gathers
everything since 1.7.0: Open VSX checked like the Marketplace, a
machine-readable release record, a retry policy per channel with `tag`
finishing Homebrew and Chocolatey, crates.io pre-checks in `check`, release
builds from a record tag with no caches, and a rejected Chocolatey version as
a terminal state. **Repositories must copy template changes into their
`release.yml`**: see [Migrating from release-kit 1.x](migrating-to-rab.md).

**The rename.** The configuration is `rab.yml` (was `rab.yml`), the
installed copy is `.github/rab/` (was `.github/release-kit/`), the build
workflow is `.github/workflows/rab-build.yml` (was `release-kit-build.yml`),
and `RAB_OFFLINE` replaces `RELEASE_KIT_OFFLINE`. The script keeps its name:
`python3 .github/rab/scripts/release.py`. `install` writes the new names and
migrates a 1.x layout: it moves `rab.yml` to `rab.yml` (`git mv` when
tracked), removes the old copy and build workflow, rewrites the old paths in
the repository's workflows, and prints each change. Until rab 3.0.0 the old
names still work with a warning: every command reads `rab.yml`, else
`rab.yml` (and refuses when both exist), `vendored` accepts a copy at
`.github/release-kit/`, and `RELEASE_KIT_OFFLINE` is read when `RAB_OFFLINE`
is unset. The actions are named `rab: …`, the build workflow `rab build`, and
`release.py --version` prints the version. The release record's schema keeps
`schema_version: 1`; its `$id` moves to the new site.

**A rejected Chocolatey version is terminal.** The community repository keeps
a rejected version and refuses every push of it, while a version still in
moderation can be pushed again. So `rejected` is now neither done nor due:
the gate does not select it, `tag` does not retry or wait for it and notes
`chocolatey of <package> is rejected: publish a new version; not retried`,
and `status` shows `rejected: publish a new version`. Fix the package and
release a new version. See [the retry policy](recovery.md#what-each-channel-allows-again-the-retry-policy).

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
gets `open_vsx_url`. A `github` package's due Homebrew and Chocolatey jobs
count toward `result`; one selected only to finish them is
`already-published` with those jobs' results in `channels`. A coordinator
releasing a project across repositories downloads it with `gh run download
<run> -n release-record`. A new last job in the template, `record`, writes it
with the new `release.py record` command, even when publishing fails, with no
new permissions. The record is versioned (`schema_version: 1`), and its JSON
Schema is published as
[`release-record.schema.json`](release-record.schema.json). **Installed
repositories must add the `record` job to their `release.yml`**: `install`
does not change an existing one. Copy it from `templates/release.yml`. See
[The release record](release-record.md).

**Retry policy per channel; `tag` finishes Homebrew and Chocolatey.** Each
channel has a retry policy defined by rab: `once` for crates.io, PyPI,
npm and the VS Code Marketplace, `finish` for GitHub Releases, `idempotent`
for a Homebrew tap and `until-accepted` for Chocolatey. The derived channels
now have a state, read live: Homebrew is done when the tap's default branch
has the formula at the version or a pull request from `<name>-<version>` is
open; Chocolatey when the community API lists the version or it is in
moderation. When a `github` package is published but one of its channels is
not done, `tag` goes back to its commit (`finish <tag>`) and pushes the
release tag again, and the gate selects the package for that channel only:
each selection entry gains `channels`, the gate's `github` flag is false for
it, and `homebrew`/`chocolatey` are true only when that channel is due. `tag`
also waits briefly for a release's channels and names any not done.
`status` prints each channel's policy and state. **Update an installed
`release.yml`:** give `publish-homebrew` and `publish-chocolatey` `needs:
[gate, ci, build, publish-github]` and let them run when `publish-github` was
skipped because nothing was due on GitHub (copy the two `if:` conditions from
`templates/release.yml`); without that, a run that only finishes a channel
publishes nothing. A `choco push` refused because the community repository
already has the version ("already exists and cannot be modified") counts as
pushed, unless the version was rejected. See [the retry policy](recovery.md#what-each-channel-allows-again-the-retry-policy).

**Catch a crate crates.io would refuse at `check`.** For every crates.io
package, `release.py check` reads the manifests with `cargo metadata` and
refuses `publish = false` (or other registries only), `git` or `path`
dependencies without a `version`, dependencies from other registries, a
dependency that publishing would take from crates.io when crates.io has no
matching version (unless this release publishes it first), a name someone else
owns (from the public owners API), a failing `cargo package --list`, and
`include_str!`-style embeds the package does not carry. Each message names the
fix. It warns about library crates in the workspace that `rab.yml`
publishes nowhere. `check --offline` (or `RAB_OFFLINE=1`) skips the
questions to crates.io; `check --publish-dry-run` runs
`cargo publish --dry-run` over the crates, for CI. `preflight --registry
crates.io` runs the same checks on the selected crates. New page:
[Publishing to crates.io](registries/publishing-to-crates-io.md). In a
Rust repository, `check` now runs cargo and asks crates.io (about one
request a second); without cargo it warns and skips these checks.

**Release builds check out a record tag and write no caches.** The build
workflow's `plan` and `build` jobs check out a record tag the gate checked
(`refs/tags/<name>-v<version>`) instead of the bare `sha`, and stop before
anything from the checkout runs unless the tag still names `sha`, so a tag
moved during the run builds nothing; the `record` job checks its release
tag against `sha` the same way. Every `actions/setup-node` in the kit
and the template sets `package-manager-cache: false`, which setup-node
otherwise turns on by itself when `package.json` names npm as its
`packageManager`, and `PyO3/maturin-action` sets `sccache: "false"`: a run
dispatched from `main` shares the default branch's cache, and a release run
adds nothing to it. GitHub CodeQL's
`actions/cache-poisoning/poisonable-step` alerts on an installed copy (a
checkout of `inputs.sha` followed by the `plan` and `build` actions) go
away. Install the new version; `release.yml` needs no change, except to add
`package-manager-cache: false` to the `setup-node` steps of an installed
`publish-npm` or `publish-vscode` job. See
[No caches](workflow.md#no-caches).

### v1.2.0 to v1.7.0

These shipped across v1.2.0 to v1.7.0 and are recorded together, with
release-kit's names (`release-kit.yml`, `.github/release-kit/`).

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
