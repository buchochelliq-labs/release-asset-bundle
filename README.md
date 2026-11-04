<p align="center">
  <img src="docs/assets/bab.svg" width="160" height="160"
       alt="BAB, the rab mascot: a baby elephant king with a crown too big for him, holding a parcel">
</p>

# rab, the Release Asset Bundle

**rab** (formerly release-kit; around the lab, the *Buchochelliq-labs Asset
Bundle*, **BAB**) is the buchochelliq-labs release standard, generalised from
rs-rich-cli's release scripts. One file, `rab.yml`, declares the packages a
repository publishes. One command, `release.py tag`, tags a release. One
workflow run then checks it, reruns CI on the tagged commit, builds the
declared artifacts, publishes what is not yet published to GitHub Releases,
crates.io, PyPI, npm and the VS Code Marketplace (and Open VSX, Homebrew and
Chocolatey), waits until each registry serves it, and uses each package as a
stranger would.

**Documentation: <https://buchochelliq-labs.github.io/release-asset-bundle/>**
(source in [`docs/`](docs/)).

## The rules

1. **A published version is permanent:** nothing on its registry is tagged, selected or uploaded again.
2. **Releases come from the default branch:** every tag points at a commit on `main`.
3. **Tags are records:** annotated, recording release and commit; one per package, plus the release tag that starts the run.
4. **A release is one commit:** the same version may not ship different files from two commits.
5. **Re-run, don't repair:** after a failure, fix the cause and run `release.py tag` again.
6. **Least privilege:** only publish jobs write; Trusted Publishing in protected environments; actions pinned to commits.

## A minimal `rab.yml`

```yaml
packages:
  - name: onto                       # the registry's name for it
    registry: crates.io              # github | crates.io | pypi | npm | vscode
    manifest: crates/onto/Cargo.toml
    version: "0.1.0"                 # quoted; must match the manifest
```

See the [reference](https://buchochelliq-labs.github.io/release-asset-bundle/configuration/)
for tags, paths, artifacts and their tests.

## Install

Each repository uses an installed copy under `.github/rab/`: pinned and
reviewable, and changed only when someone installs again. From your
repository's root:

```sh
git clone https://github.com/buchochelliq-labs/release-asset-bundle ../release-asset-bundle
python3 ../release-asset-bundle/scripts/release.py install
python3 .github/rab/scripts/release.py check
```

Then follow [Getting started](https://buchochelliq-labs.github.io/release-asset-bundle/getting-started/)
to wire `release.yml` and set up each registry.

**Coming from release-kit 1.x?** `install` migrates the old names
(`release-kit.yml`, `.github/release-kit/`, `release-kit-build.yml`), and the
old names keep working with a warning until rab 3.0.0. See
[Migrating from release-kit 1.x](https://buchochelliq-labs.github.io/release-asset-bundle/migrating-to-rab/).

## Developing rab

```sh
pip install pyyaml
python3 -m unittest discover -s tests
pip install zensical && zensical build --strict   # the docs site
```
