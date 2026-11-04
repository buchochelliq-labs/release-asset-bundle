# The workflow

A release is one run of `.github/workflows/release.yml`. `install` writes it
from [`templates/release.yml`](https://github.com/buchochelliq-labs/release-kit/blob/main/templates/release.yml)
when the repository has none. After that it is the repository's own file.

```text
gate ──▶ ci ──▶ build ──┬──▶ publish-github
                        ├──▶ publish-pypi
                        ├──▶ publish-vscode
                        ├──▶ publish-crates
                        └──▶ publish-npm
```

## Triggers

```yaml
on:
  push:
    tags: ["release-*"]
  workflow_dispatch:
    inputs:
      tag:          # an existing release tag to run again (required)
      verify_only:  # boolean, default false
```

- **A pushed release tag** starts a normal run. `release.py tag` pushes it.
- **A manual dispatch** runs an existing release tag again. With
  `verify_only` set, it only verifies (see [Verify-only runs](recovery.md#verify-only-runs)).

```sh
gh workflow run release.yml -f tag=release-0.2.0
gh workflow run release.yml -f tag=release-0.2.0 -f verify_only=true
```

## Permissions, environments, concurrency

The workflow's default token permission is `contents: read`. Only the publish
jobs get more:

| Job | Permissions | Environment |
|---|---|---|
| `gate`, `ci` | `contents: read` | none |
| `build` | `contents: read`, `actions: read` (only a [`ci-artifact`](configuration.md#ci-artifact) job uses its token) | none |
| `publish-github` | `contents: write` | none |
| `publish-crates` | `contents: read`, `id-token: write` | `crates-io` |
| `publish-pypi` | `contents: read`, `id-token: write` | `pypi` |
| `publish-npm` | `contents: read`, `id-token: write` | `npm` |
| `publish-vscode` | `contents: read` (default) | `vscode-marketplace` |

`id-token: write` lets a job request an OIDC token for Trusted Publishing.
Environments hold the secrets and are what the registries' trusted publishers
name. In each environment's settings, you can limit deployments to the
`main` branch and `release-*` tags (a dispatched run's ref is the branch it was
dispatched from), and add required reviewers if you want a person to approve
each upload.

```yaml
concurrency:
  group: release
  cancel-in-progress: false
```

One release runs at a time. A second run waits; a run that may be half
published is never cancelled by another.

Every checkout uses `persist-credentials: false`, so later steps cannot push
with the job's token.

## Jobs

### `gate`

Checks out `refs/tags/<tag>` with full history (`fetch-depth: 0`) and runs
the [`gate`](commands.md#gate) action. The tag is `inputs.tag` on a dispatch,
else the pushed tag. Its outputs are the job's outputs: `sha`, `release`,
`selection`, `verify-only`, and one flag per registry (`github`, `crates-io`,
`pypi`, `npm`, `vscode`).

If the gate refuses, nothing else runs.

### `ci`

```yaml
  ci:
    needs: gate
    uses: ./.github/workflows/ci.yml
    with:
      ref: ${{ needs.gate.outputs.sha }}
```

Your own CI, called on the tagged commit. `ci.yml` needs `on: workflow_call`
with a `ref` input that it checks out. If your file or input has another name,
change this job. Every later job needs `ci` to succeed.

### `build`

```yaml
  build:
    needs: [gate, ci]
    if: needs.gate.outputs.verify-only != 'true'
    uses: ./.github/workflows/release-kit-build.yml
    with:
      sha: ${{ needs.gate.outputs.sha }}
      selection: ${{ needs.gate.outputs.selection }}
```

Calls the build workflow (below). Skipped in a verify-only run.

### `publish-github`, `publish-pypi`, `publish-vscode`

These publish built files, so they wait for the build:

```yaml
    needs: [gate, ci, build]
    if: >-
      !cancelled() && needs.gate.outputs.github == 'true' && needs.ci.result == 'success'
      && (needs.build.result == 'success'
          || (needs.build.result == 'skipped' && needs.gate.outputs.verify-only == 'true'))
```

That is: the gate selected a package of this registry, CI passed, and the
build succeeded, or was skipped because this is a verify-only run. Each job
checks out the tagged commit, installs Python 3.12 and PyYAML, downloads every
`release--*` artifact into `dist/` (not in a verify-only run), and runs its
publish action.

`publish-vscode` passes the `VSCE_PAT` and `OVSX_PAT` secrets.

### `publish-crates`, `publish-npm`

These publish from source, but still wait for the build, with the same
condition as the other publish jobs: a release whose artifacts fail to build
or fail their tests publishes nothing anywhere.

```yaml
    needs: [gate, ci, build]
    if: >-
      !cancelled() && needs.gate.outputs.crates-io == 'true' && needs.ci.result == 'success'
      && (needs.build.result == 'success'
          || (needs.build.result == 'skipped' && needs.gate.outputs.verify-only == 'true'))
```
 `publish-crates` installs stable Rust, runs
`preflight`, requests a Trusted Publishing token only when an existing crate
is selected, and passes `CARGO_REGISTRY_TOKEN` only when a new one is; see
[crates.io](registries/crates-io.md). `publish-npm` installs Node 24 with the
npm registry, runs `npm ci` at the repository root (not in a verify-only run),
and runs its action.

### Adding your own jobs

Keep repository-specific checks (for example `cargo-semver-checks`) as jobs
in `release.yml` that the publish jobs `need`.

!!! warning "Name publishing jobs `publish…`"
    `release.py tag` decides whether a failed run published anything by
    looking for jobs whose names start with `publish` that started. A job that
    uploads anywhere must have a name starting with `publish`, or `tag` may
    move its tags after it uploaded.

## The build workflow

`.github/workflows/release-kit-build.yml` is part of the installed copy: do
not edit it. It is a reusable workflow with two inputs, `sha` and `selection`,
and `contents: read`. It holds no credentials.

1. **`plan`** checks out `sha` and runs the [`plan`](commands.md#plan) action:
   outputs `jobs` and `count`.
2. **`build`** runs only when `count` is not `0`. It is a matrix over `jobs`
   (`fail-fast: false`), named `build <id>`, on each job's `os`. Each job
   checks out `sha`, runs the [`build`](commands.md#build) action, and
   uploads `dist/*` as the artifact `release--<slug>--<id>`
   (`if-no-files-found: error`).

The build action sets up what the kind needs: Python 3.12 always; stable Rust
with the target for `rust-binary`; Node 24 for `vsix`; `PyO3/maturin-action`
for `maturin` (wheels, `--release`, `manylinux: auto`) and for the sdist.

### Artifact naming

Artifacts are named `release--<slug>--<job id>`, for example:

```text
release--onto-cli-binaries--onto-cli-binaries-rust-binary-x86_64-unknown-linux-gnu
release--rs-rich--rs-rich-maturin-aarch64-apple-darwin
release--rs-rich--rs-rich-sdist
```

The publish jobs download them with `pattern: release--*` into `dist/`, one
directory per artifact (not merged), and each publish action takes a
package's files from `dist/release--<slug>--*/`.

## Pinned actions

Every third-party action in the kit and the template is pinned to a full
commit SHA, with its version in a comment:

```yaml
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
```

A test in release-kit refuses any `uses:` that is not local (`./…`) or pinned
this way, and Dependabot opens PRs that move a SHA and its comment together.
Repositories get new pins by installing a newer release-kit. Pin any actions
you add to `release.yml` the same way.
