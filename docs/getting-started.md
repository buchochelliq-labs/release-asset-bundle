# Getting started

This page takes a repository from nothing to its first release. It uses a
small Rust workspace as the example: one library crate, `onto`, and one CLI
crate, `onto-cli`, both on crates.io, plus the CLI's binaries on a GitHub
release.

You need Python 3.11 or newer, git and PyYAML (`pip install pyyaml`) on your
machine. `release.py tag` also uses the [GitHub CLI](https://cli.github.com/)
(`gh`) to follow the release run; install it and run `gh auth login` first.

## 1. Install the copy

release-kit is a public repository. Clone it next to your repository, then run
`install` from your repository's root:

```sh
git clone https://github.com/buchochelliq-labs/release-kit ../release-kit
cd your-repository
python3 ../release-kit/scripts/release.py install
```

`install` writes:

| Path | What it is |
|---|---|
| `.github/release-kit/scripts/release.py` | the driver script |
| `.github/release-kit/<action>/action.yml` | the composite actions: `build`, `gate`, `plan`, `publish-crates`, `publish-github`, `publish-npm`, `publish-pypi`, `publish-vscode` |
| `.github/release-kit/README.md` | a notice: do not edit, reinstall instead |
| `.github/release-kit/manifest.json` | the kit's version and commit, and a SHA-256 checksum of every installed file |
| `.github/workflows/release-kit-build.yml` | the reusable build workflow |
| `.github/workflows/release.yml` | the starter release workflow, **only if the repository has none** |

Why a copy, and not `uses: buchochelliq-labs/release-kit/gate@v1`? A copy is
pinned and reviewable. Your repository runs exactly the code in its own
history, a reviewer sees every line of it, and it changes only when someone
installs again and opens a PR. `install --into DIR` installs into another
directory instead of the repository you are in.

To update later: `git -C ../release-kit pull`, run `install` again, and open a
PR. That replaces `.github/release-kit/` (files the new version dropped are
removed) and the build workflow, and leaves your `release.yml` alone.

## 2. Write `release-kit.yml`

At the repository root. List every package, in publication order: each after
the listed packages of the same registry it depends on.

```yaml
release:
  name_from: onto-cli        # the release is named by onto-cli's version

packages:
  - name: onto
    registry: crates.io
    manifest: crates/onto/Cargo.toml
    version: "0.1.0"

  - name: onto-cli
    registry: crates.io
    manifest: crates/onto-cli/Cargo.toml
    version: "0.1.0"

  - name: onto-cli-binaries     # a GitHub package's name is free
    registry: github
    manifest: crates/onto-cli/Cargo.toml
    version: "0.1.0"
    tag: "onto-cli-bin-v{version}"
    artifacts:
      - kind: rust-binary
        bin: onto
        crate: onto-cli
        include: [README.md, LICENSE]
```

Then check it:

```sh
python3 .github/release-kit/scripts/release.py check
```

```text
release-kit.yml matches the repository (release 0.1.0).
```

Quote every version. YAML reads `0.10` as the number `0.1`, so release-kit
refuses an unquoted version. See the [`release-kit.yml` reference](configuration.md)
for every field.

## 3. Wire `release.yml`

`install` wrote `.github/workflows/release.yml` from the template. It is your
file: edit it. Two things usually need attention.

**Delete the publish jobs you do not use.** The template has
`publish-github`, `publish-crates`, `publish-pypi`, `publish-npm` and
`publish-vscode`. Each runs only when the gate selects a package of its
registry, so unused jobs are harmless, but they name environments and secrets
you would otherwise have to explain.

**Make your CI callable.** The release run calls your `.github/workflows/ci.yml`
on the tagged commit:

```yaml
  ci:
    needs: gate
    uses: ./.github/workflows/ci.yml
    with:
      ref: ${{ needs.gate.outputs.sha }}
```

So `ci.yml` needs a `workflow_call` trigger with a `ref` input, and must check
that ref out:

```yaml
on:
  push:
    branches: [main]
  pull_request:
  workflow_call:
    inputs:
      ref:
        type: string
        required: false

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          ref: ${{ inputs.ref }}    # empty on push and pull_request: the default
          persist-credentials: false
      # ...
```

If your CI file has another name, change `uses:` in the `ci` job. If its input
has another name (rs-rich-cli's is `checkout_ref`), change `ref:` under `with:`
to that name.

**Edit the trigger if you change the release tag.** The template starts on
tags matching `release-*`. If you set `release: tag:` to another format, change
`on: push: tags:` to match it.

## 4. Check the copy in CI

Add these steps to a job in `ci.yml`:

```yaml
      - run: python3 -m pip install --quiet pyyaml
      - run: python3 .github/release-kit/scripts/release.py check
      - run: python3 .github/release-kit/scripts/release.py vendored
```

`check` fails when `release-kit.yml` disagrees with a manifest. `vendored`
fails when anyone edits, deletes or adds a file in the installed copy, so the
copy changes only by installing again.

!!! note "Dependabot"
    If your repository's Dependabot updates GitHub Actions, it may open PRs
    that change `.github/workflows/release-kit-build.yml`. That file is part of
    the installed copy, so `vendored` fails on them. Close those PRs and
    reinstall a newer release-kit instead; release-kit's own Dependabot keeps
    its pins current.

## 5. Set up each registry once

| Registry | Credential | Set up once |
|---|---|---|
| [GitHub Releases](registries/github.md) | the job's `github.token`, with `contents: write` | nothing |
| [crates.io](registries/crates-io.md) | Trusted Publishing; a crate's first version uses a token | a `crates-io` environment; a `CARGO_REGISTRY_TOKEN` secret in it for new crates; a Trusted Publisher on each crate |
| [PyPI](registries/pypi.md) | Trusted Publishing | a `pypi` environment; a (pending) trusted publisher on PyPI |
| [npm](registries/npm.md) | Trusted Publishing (npm 11.5+), or a token | an `npm` environment; a trusted publisher on npmjs.com |
| [VS Code Marketplace](registries/vscode.md) | `VSCE_PAT` (and `OVSX_PAT` for a package with `open-vsx: true`) | both secrets in a `vscode-marketplace` environment |

Trusted publishers name the workflow file `release.yml` and the environment.
Keep both names, or change the publisher settings to match.

## 6. Commit, merge, release

Commit the copy, `release-kit.yml`, `release.yml` and the CI changes in a PR,
and merge it. Then, on the default branch:

```sh
git switch main && git pull
python3 .github/release-kit/scripts/release.py status
python3 .github/release-kit/scripts/release.py tag --dry-run
python3 .github/release-kit/scripts/release.py tag
```

`tag` pushes the tags, then waits until the run has published everything
(up to three hours by default). See [Commands](commands.md#tag).

For each later release: bump the versions in the manifests, run `sync` to copy
them into `release-kit.yml`, merge the PR, and run `tag` again.
