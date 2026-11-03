# Migrating from rs-rich-cli's scripts

release-kit was generalised from rs-rich-cli's release process
(`RELEASES.toml`, `scripts/release_cohort.py`, `release.yml` and
`pypi-release.yml`). The rules are the same. This page maps one onto the other,
using rs-rich-cli itself as the example.

## What changes

| rs-rich-cli | release-kit |
|---|---|
| `RELEASES.toml` (TOML, `[[package]]`) | `release-kit.yml` (YAML, `packages:`) |
| `scripts/release_cohort.py` | `.github/release-kit/scripts/release.py` (installed copy) |
| tags derived in code: `<name>-v<version>`, and `python-v<version>` for PyPI | `tag:` per package; default `{name}-v{version}` |
| release named by the last crates.io package | `release: name_from:` (default: the last package listed) |
| crates.io in `release.yml`, PyPI in `pypi-release.yml` from its own `python-v…` tag | every registry in one `release.yml` run, started by the release tag |
| split detection from each crate's packaged file list | split detection from each package's `paths` globs |
| build jobs written by hand | `artifacts:` declared per package |
| `tag --registry` | gone: one run publishes every registry |

## 1. `RELEASES.toml` → `release-kit.yml`

Each `[[package]]` becomes a list entry with the same four fields. Keep the
order. Versions must be quoted.

```yaml
# release-kit.yml for rs-rich-cli
release:
  name_from: rs-rich-cli    # required: the last package listed is the PyPI rs-rich

packages:
  - name: rs-rich
    registry: crates.io
    manifest: crates/rich/Cargo.toml
    version: "0.0.12"

  - name: rs-rich-macros
    registry: crates.io
    manifest: crates/rich-macros/Cargo.toml
    version: "0.0.6"

  # ... the other crates, in RELEASES.toml's order ...

  - name: rs-rich-cli
    registry: crates.io
    manifest: crates/rich-cli/Cargo.toml
    version: "0.0.22"
    paths: ["crates/rich-cli/**", "Cargo.lock"]

  # The Python bindings: PyPI `rs-rich`, outside the Cargo workspace.
  - name: rs-rich
    registry: pypi
    manifest: crates/rich-py/pyproject.toml
    version: "0.0.10"
    tag: "python-v{version}"
    artifacts:
      - kind: maturin
        targets:
          - x86_64-unknown-linux-gnu
          - aarch64-unknown-linux-gnu
          - aarch64-apple-darwin
          - x86_64-apple-darwin
          - x86_64-pc-windows-msvc
```

Three details matter:

- **`name_from: rs-rich-cli`.** rs-rich-cli names a release by its last
  crate. release-kit's default is the last package listed, which here is the
  PyPI package. Name it explicitly. (`name_from` matches by name; if two
  packages share it, the first listed wins.)
- **`tag: "python-v{version}"` on the PyPI `rs-rich`.** The crate and the
  Python package are both called `rs-rich`. Under the default format both
  would be tagged `rs-rich-v<version>`, and the crate already has tags such as
  `rs-rich-v0.0.10`. release-kit would refuse the Python package's tag as
  [not its own record](configuration.md#record-tags-and-collisions).
  `python-v{version}` is also the format rs-rich-cli already uses, so existing
  `python-v…` tags keep their meaning.
- **`paths`.** rs-rich-cli compared each crate's packaged file list. The
  default here is the manifest's directory. Widen it where a package ships
  files from elsewhere, such as `Cargo.lock` for the CLI, or the crates that
  the Python bindings build from.

Run `release.py check` until it passes.

## 2. Fold `pypi-release.yml` into the one run

Delete `pypi-release.yml`. The PyPI package now publishes from `release.yml`'s
`publish-pypi` job, in the same run as the crates, started by the release tag.
Record tags, including `python-v…`, start nothing.

PyPI's trusted publisher names the workflow file. **Change the `rs-rich`
project's trusted publisher on PyPI** to workflow `release.yml`, environment
`pypi` (it currently names `pypi-release.yml`). Until you do, the upload is
refused.

crates.io's Trusted Publishers already name `release.yml` and the `crates-io`
environment, so they need no change. Keep the `CARGO_REGISTRY_TOKEN` secret in
`crates-io` for first versions of new crates.

## 3. Call CI with `checkout_ref`

rs-rich-cli's `ci.yml` takes its commit as `checkout_ref`, not `ref`. In
`release.yml`, change the `ci` job's input name:

```yaml
  ci:
    needs: gate
    uses: ./.github/workflows/ci.yml
    with:
      checkout_ref: ${{ needs.gate.outputs.sha }}
```

## 4. Keep repository-specific checks as your own jobs

release-kit does not carry these over. Add them to `release.yml` yourself:

- **API compatibility** (`cargo-semver-checks`): a job after `gate` that
  checks out `needs.gate.outputs.sha`; make `publish-crates` need it.
- **The docs playground** module, built and attached to the GitHub release as
  `playground-X.Y.Z.tar.gz`: a job after `ci`. Because it uploads, give it a
  job name starting with `publish` (for example `publish-playground`), so
  that `release.py tag` sees that it may have published and never moves its
  tags. See [Recovering a failed release](recovery.md#what-tag-can-prove-dead).

Pin every action you add to a commit SHA, as the rest of the workflow is.

## 5. Switch over

1. Finish any release in flight with the old scripts first. release-kit does
   not resume a release whose commit has no `release-kit.yml`, and it refuses
   an existing tag of an unpublished package unless the tag's message starts
   with `<name> <version> (<registry>)`. `release_cohort.py` wrote
   `<name> <version>`.
2. Install the copy, add `release-kit.yml`, update `release.yml` (or move its
   extra jobs into the template), delete `pypi-release.yml` and
   `RELEASES.toml`, and add `release.py check` and `release.py vendored` to CI.
3. Update the PyPI trusted publisher.
4. Merge, then cut the next release with
   `python3 .github/release-kit/scripts/release.py tag`.

Already published versions are untouched: they stay published, and their old
tags are never selected again.
