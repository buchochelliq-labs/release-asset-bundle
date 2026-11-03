# release-kit

The buchochelliq-labs release standard, generalised from rs-rich-cli's release
scripts. One file, `release-kit.yml`, declares the packages a repository
publishes. One command, `release.py tag`, tags a release. One workflow run
then checks it, reruns CI on the tagged commit, builds the declared
artifacts, publishes what is not yet published to GitHub Releases, crates.io,
PyPI, npm and the VS Code Marketplace, waits until each registry serves it,
and uses each package as a stranger would.

**Documentation: <https://buchochelliq-labs.github.io/release-kit/>**
(source in [`docs/`](docs/)).

## The rules

1. **A published version is permanent:** nothing on its registry is tagged, selected or uploaded again.
2. **Releases come from the default branch:** every tag points at a commit on `main`.
3. **Tags are records:** annotated, recording release and commit; one per package, plus the release tag that starts the run.
4. **A release is one commit:** the same version may not ship different files from two commits.
5. **Re-run, don't repair:** after a failure, fix the cause and run `release.py tag` again.
6. **Least privilege:** only publish jobs write; Trusted Publishing in protected environments; actions pinned to commits.

## A minimal `release-kit.yml`

```yaml
packages:
  - name: onto                       # the registry's name for it
    registry: crates.io              # github | crates.io | pypi | npm | vscode
    manifest: crates/onto/Cargo.toml
    version: "0.1.0"                 # quoted; must match the manifest
```

See the [reference](https://buchochelliq-labs.github.io/release-kit/configuration/)
for tags, paths, artifacts and their tests.

## Install

Each repository uses an installed copy under `.github/`: pinned and
reviewable, and changed only when someone installs again. From your
repository's root:

```sh
git clone https://github.com/buchochelliq-labs/release-kit ../release-kit
python3 ../release-kit/scripts/release.py install
python3 .github/release-kit/scripts/release.py check
```

Then follow [Getting started](https://buchochelliq-labs.github.io/release-kit/getting-started/)
to wire `release.yml` and set up each registry.

## Developing release-kit

```sh
pip install pyyaml
python3 -m unittest discover -s tests
pip install zensical && zensical build --strict   # the docs site
```
