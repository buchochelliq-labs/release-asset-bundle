# release-kit

The buchochelliq-labs release standard, generalised from rs-rich-cli's release
process. One YAML file in the repository, `release-kit.yml`, says what the
repository publishes and which artifacts each package is built into. One
command tags a release. One workflow run then checks it, reruns CI on the
tagged commit, builds the declared artifacts, and publishes what is not yet
published to GitHub Releases, crates.io, PyPI, npm and the VS Code
Marketplace.

## The rules

1. **A published version is permanent.** Nothing already on its registry is
   tagged, selected or uploaded again. Every publish step checks once more just
   before uploading, then waits until the registry serves the new version.
2. **Releases come from the default branch.** Every tag points at a commit on
   `main` (after the release PR merges), and the release run refuses any other.
3. **Tags are records.** Every tag is annotated and records its release and
   commit (`Release:` and `Commit:` lines). Each package gets its own tag
   (`<name>-v<version>`, which starts nothing); the release tag
   (`release-<version>`) starts the one run that publishes the whole release.
4. **A release is one commit.** A package already tagged on another commit
   that ships different files under the same version is refused: release from
   that commit, or bump the package.
5. **Re-run, don't repair.** If a release run fails, fix the cause and run
   `release.py tag` again. A run that published nothing has its tags moved to
   the fixed commit. A run that published part of the release is started
   again, and publishes only what is still missing.
6. **Least privilege.** Only the publish jobs can write. Registries use
   Trusted Publishing (short-lived OIDC credentials) where they offer it, in
   protected environments.

## `release-kit.yml`

At the repository root. It lists every package the repository publishes, in
publication order, each after the listed packages it depends on, with the
artifacts it is built into:

```yaml
# Optional: by default the release is named by the last package's version,
# tagged release-<version>, from main.
release:
  name_from: onto           # the package whose version names the release
  tag: "release-{release}"
  branch: main

packages:
  - name: onto                          # the registry's name for it
    registry: github                    # github | crates.io | pypi | npm | vscode
    manifest: crates/onto-cli/Cargo.toml
    version: "0.1.0"                    # quoted; must match the manifest (release.py check)
    tag: "v{version}"                   # optional; default "{name}-v{version}"
    paths: ["crates/**", "Cargo.lock"]  # optional: files that make up the package
                                        # (default: the manifest's directory)
    artifacts:
      - kind: rust-binary
        bin: onto
        crate: onto-cli                 # default: the manifest's package
        include: [README.md]            # added to each archive
        targets:                        # default: the five below
          - x86_64-unknown-linux-gnu
          - aarch64-unknown-linux-gnu
          - aarch64-apple-darwin
          - x86_64-apple-darwin
          - x86_64-pc-windows-msvc
```

Manifests are `Cargo.toml` (including `version.workspace = true`),
`pyproject.toml` (PEP 621 or Poetry) and `package.json` (for `vscode`, the
name is `<publisher>.<name>`).

### Artifacts

Each package declares what to build; the release run builds only the packages
that are due, each artifact (and target) in its own job on the runner it
needs.

| `kind` | Builds | Registries | Fields |
|---|---|---|---|
| `rust-binary` | `<bin>-v<version>-<target>.tar.gz` (`.zip` on Windows) holding the binary and `include` files | github | `bin` (required), `crate`, `targets`, `include` |
| `python` | sdist and wheel with `python -m build` | pypi, github | `dir` (default: the manifest's directory) |
| `maturin` | wheels per target with maturin, plus an sdist | pypi, github | `targets`, `sdist` (default true) |
| `vsix` | `<name>-<version>.vsix` with `vsce package` | vscode, github | `dir` |
| `command` | whatever `run` makes, collected by the `outputs` glob | github, pypi, vscode | `run`, `outputs` (both required), `os` (default ubuntu-latest) |

`command` runs in bash with `VERSION`, `PACKAGE` and `OUT` (the output
directory) set. crates.io and npm publish from source and take no artifacts.
For GitHub Releases every file gets a `.sha256`, and the publish step checks
each one after uploading. A target is a string, or `{target: ..., runner: ...}`
for a runner the kit doesn't know.

## Adopting it: install a copy

release-kit is private, and GitHub lets public repositories use only public
actions and workflows. So every repository, public or private, uses an
**installed copy**. From the repository's root, with a checkout of release-kit
next to it:

```sh
git clone https://github.com/buchochelliq-labs/release-kit ../release-kit   # once
python3 ../release-kit/scripts/release.py install
```

That writes:

- **`.github/release-kit/`:** the script and the actions, a `manifest.json` of
  their checksums, and the version they came from;
- **`.github/workflows/release-kit-build.yml`:** the build workflow;
- **`.github/workflows/release.yml`:** the starter workflow, only if the
  repository has none.

Commit them with a `release-kit.yml`. Add `python3
.github/release-kit/scripts/release.py vendored` to CI: it fails if anyone
edits the copy, so the copy changes only by installing again. To update a
repository, pull release-kit and run `install` again, then open a PR. That
updates the copy and the build workflow, and leaves `release.yml` alone.

The copied code is visible in a public repository. The source, tests and
history stay private in release-kit.

## The driver: `release.py`

Run it from the repository's root, either as the installed copy
(`python3 .github/release-kit/scripts/release.py`) or from the release-kit
checkout:

| Command | What it does |
|---|---|
| `check` | `release-kit.yml` matches every manifest, names no package or tag twice, orders each package after the listed packages it depends on, and declares artifacts its registry can publish. Run it in CI. |
| `sync` | Rewrites `release-kit.yml`'s versions from the manifests after a bump. |
| `status` | Each package's version, tag, and whether it is published. |
| `tag` | On `main`: tags what is due and waits for the run. Options: `--dry-run`, `--commit SHA`, `--release X.Y.Z`. |
| `install` | From a release-kit checkout: installs or updates the copy in the repository you are in (`--into DIR`). |
| `vendored` | The installed copy is unmodified. Run it in CI. |

The workflow uses `gate`, `plan`, `build`, `preflight` and `verify`. The
script needs Python 3.11+, git and PyYAML (`pip install pyyaml`).

## The workflow

`.github/workflows/release.yml` (from [`templates/release.yml`](templates/release.yml))
runs **gate → ci → build → publish-…**. Keep the publish jobs for your
registries:

- **`gate`** (`./.github/release-kit/gate`) checks rules 1–4 on the checked-out
  tag, and outputs the selection and one flag per registry.
- **`ci`** is your own CI on the tagged commit. Your `ci.yml` needs
  `on: workflow_call` with a `ref` input that it checks out.
- **`build`** (`./.github/workflows/release-kit-build.yml`) plans one job per
  declared artifact and target (`./.github/release-kit/plan`), builds each
  (`./.github/release-kit/build`), and uploads its files as
  `release--<package>--<job>`.
- **`publish-<registry>`** (`./.github/release-kit/publish-…`) publishes each
  package's built artifacts, in `release-kit.yml` order, then waits until the
  registry serves every version.

The publish steps are **composite actions** that run inside the repository's
own jobs. Trusted Publishing on crates.io, PyPI and npm identifies the
publisher by the calling workflow, so the publisher you configure on each
registry names the repository's `release.yml` (and its environment).

| Registry | Credential | Set up once |
|---|---|---|
| GitHub Releases | `github.token` with `contents: write` | nothing |
| crates.io | Trusted Publishing via `rust-lang/crates-io-auth-action`; a crate's first version needs a token | add the Trusted Publisher (repository, `release.yml`, environment `crates-io`) on each crate's settings page |
| PyPI | Trusted Publishing | add the publisher on PyPI (environment `pypi`) |
| npm | Trusted Publishing (npm 11.5+), or a token input | add the trusted publisher on npmjs.com (environment `npm`) |
| VS Code Marketplace | `VSCE_PAT` secret (and `OVSX_PAT` for Open VSX) | secrets in the `vscode-marketplace` environment |

Requirements: Python 3.11+ (the runners' default is newer), Cargo 1.90+ for
publishing several crates in one run, and `jq` and `gh` (both on GitHub's
runners).

## Versioning release-kit

release-kit is tagged `v1.x.y`. An installed copy records the version and
commit it came from in `.github/release-kit/manifest.json`. A breaking change
becomes `v2`, and each repository moves when it installs the new version.

## How it differs from rs-rich-cli

The rules are the same; these are the differences:

- **Config:** YAML (`release-kit.yml`) instead of TOML, and each repository
  declares the artifacts to build rather than writing build jobs.
- **Registries:** the registry is a field of each package, and five
  registries are supported.
- **Single run:** a release tag publishes to every registry in one run. rs-rich
  starts PyPI from its own `python-v…` tag.
- **Split detection:** compares each package's `paths` between commits,
  instead of rs-rich's per-crate package file lists.
- **Not carried over:** the SemVer API checks (`cargo-semver-checks`) and the
  docs playground. Those stay repository-specific jobs that you add to your
  own `release.yml`.
