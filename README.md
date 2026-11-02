# release-kit

The buchochelliq-labs release standard, generalised from rs-rich-cli's release
process: one file says what a repository publishes, one command tags a
release, and one workflow run checks it, reruns CI on the tagged commit, and
publishes what is not yet published to GitHub Releases, crates.io, PyPI, npm
and the VS Code Marketplace.

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

## `RELEASES.toml`

At the repository root, listing every package the repository publishes in
publication order, each after the listed packages it depends on:

```toml
# Optional: by default the release is named by the last package's version,
# tagged release-<version>, from main.
[release]
name_from = "onto"        # the package whose version names the release
tag = "release-{release}"
branch = "main"

[[package]]
name = "onto"                         # the registry's name for it
registry = "github"                   # github | crates.io | pypi | npm | vscode
manifest = "crates/onto-cli/Cargo.toml"
version = "0.1.0"                     # must match the manifest (release.py check)
tag = "v{version}"                    # optional; default "{name}-v{version}"
paths = ["crates/**", "Cargo.lock"]   # optional: files that make up the package
                                      # (default: the manifest's directory)
```

Manifests are `Cargo.toml` (including `version.workspace = true`),
`pyproject.toml` (PEP 621 or Poetry) and `package.json` (for `vscode`, the
name is `<publisher>.<name>`).

## The driver: `scripts/release.py`

Run it from the repository's root with a checkout of release-kit next to your
repositories (`git clone https://github.com/buchochelliq-labs/release-kit`):

| Command | What it does |
|---|---|
| `release.py check` | `RELEASES.toml` matches every manifest, names no package or tag twice, and orders each package after the listed packages it depends on. Run it in CI. |
| `release.py sync` | Rewrites `RELEASES.toml`'s versions from the manifests after a bump. |
| `release.py status` | Each package's version, tag, and whether it is published. |
| `release.py tag` | On `main`: tags what is due and waits for the run. Options: `--dry-run`, `--commit SHA`, `--release X.Y.Z`. |

The workflow uses `gate`, `preflight` and `verify`.

## The workflow

Copy [`templates/release.yml`](templates/release.yml) to
`.github/workflows/release.yml`, keep the publish jobs for your registries and
fill in `build`. It runs **gate → ci → build → publish-…**:

- **`gate`** (`buchochelliq-labs/release-kit/gate@v1`) checks rules 1–4 on the
  checked-out tag, and outputs the selection and one flag per registry.
- **`ci`** is your own CI on the tagged commit. Your `ci.yml` needs
  `on: workflow_call` with a `ref` input that it checks out.
- **`publish-<registry>`** (`buchochelliq-labs/release-kit/publish-…@v1`)
  publishes in `RELEASES.toml` order, then waits until the registry serves
  every version.

The publish steps are **composite actions**, not reusable workflows, so that
they run inside your repository's own job. Trusted Publishing on crates.io,
PyPI and npm identifies the publisher by the calling workflow, so the
publisher you configure on each registry names your repository's
`release.yml` (and its environment), not release-kit.

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

release-kit is tagged `v1.x.y`, and `v1` moves to the latest `v1.x.y`.
Repositories use `@v1`. A breaking change becomes `v2`, and repositories move
when they choose.

## How it differs from rs-rich-cli

The rules are the same; these are the differences:

- **Registries:** the registry is a field of each package, and five
  registries are supported.
- **Single run:** a release tag publishes to every registry in one run. rs-rich
  starts PyPI from its own `python-v…` tag.
- **Split detection:** compares each package's `paths` between commits,
  instead of rs-rich's per-crate package file lists.
- **Not carried over:** the SemVer API checks (`cargo-semver-checks`) and the
  docs playground. Those stay repository-specific jobs that you add to your
  own `release.yml`.
