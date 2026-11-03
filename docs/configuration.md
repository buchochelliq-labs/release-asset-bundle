# `release-kit.yml` reference

`release-kit.yml` sits at the repository root. It has an optional `release:`
mapping and a required `packages:` list. `release.py check` validates all of
it against the repository's manifests; the gate runs the same check on the
tagged commit and refuses a release that fails it.

```yaml
release:                    # optional
  name_from: onto-cli
  tag: "release-{release}"
  branch: main

packages:
  - name: onto-cli
    registry: crates.io
    manifest: crates/onto-cli/Cargo.toml
    version: "0.1.0"
    tag: "{name}-v{version}"
    paths: ["crates/onto-cli/**", "Cargo.lock"]
```

## `release:`

| Field | Default | Meaning |
|---|---|---|
| `name_from` | the last package's name | The package whose version names the release. It must be a listed package's name. If two packages share that name (a crate and a PyPI package), the first one listed is used. |
| `tag` | `"release-{release}"` | The release tag's format. `{release}` is the release's name. The template's trigger matches `release-*`; change it if you change this. |
| `branch` | `main` | The default branch. Every tag must point at a commit on it. |

## `packages:`

A list, in publication order. Each package comes after the listed packages
**of the same registry** that it depends on. `check` reads the dependencies
from the manifests:

- `Cargo.toml`: `[dependencies]`, `[build-dependencies]` and
  `[target.*.dependencies]` (a renamed dependency counts by its `package =`
  name). `[dev-dependencies]` do not count.
- `pyproject.toml`: `[project] dependencies` (or Poetry's).
- `package.json`: `dependencies` and `peerDependencies`.

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | The registry's name for the package. For crates.io, PyPI and npm it must equal the manifest's name. For `vscode` it is `<publisher>.<name>` from `package.json`. For `github` it is free. |
| `registry` | yes | `github`, `crates.io`, `pypi`, `npm` or `vscode`. |
| `manifest` | yes | The manifest that holds the version: a `Cargo.toml`, `pyproject.toml` or `package.json`, as a path from the repository root. |
| `version` | yes | The version, **quoted**. It must equal the manifest's (`release.py sync` copies it). |
| `tag` | no | The record tag's format. Default `"{name}-v{version}"`. |
| `paths` | no | Glob patterns (fnmatch, from the repository root) for the files that make up the package. Default: everything under the manifest's directory (`**` if the manifest is at the root). |
| `artifacts` | for `github`, `pypi`, `vscode` | What to build. See [Artifacts](#artifacts). |

### Manifests

- **`Cargo.toml`**: `[package] name` and `version`. `version.workspace = true`
  reads `[workspace.package] version` from the root `Cargo.toml`.
- **`pyproject.toml`**: `[project]` (PEP 621), or `[tool.poetry]`.
- **`package.json`**: `name` and `version`; for `vscode`, the name is
  `<publisher>.<name>`.

### Versions

PyPI versions follow PEP 440 (`1.2.0`, `1.2.0rc1`, `1.2.0.post1`,
`1.2.0.dev3`). Every other registry's follow SemVer (`1.2.0`, `1.2.0-rc.1`).
A version with a `-` makes a GitHub release a pre-release.

Quote versions. YAML reads `version: 0.10` as the number `0.1`, so release-kit
refuses any version that is not a string:

```text
onto: quote the version (version: "0.1"); YAML reads an unquoted one as a number
```

`release.py sync` rewrites each package's `version:` line from its manifest,
quoted, and keeps comments and layout. It needs exactly one `version:` line
per package, so do not put a `version:` key anywhere else in the file.

### `paths`: what a package ships

`paths` decides when a release would be *split* (rule 4). If a package's
version is already tagged on another commit, and any file matching its
`paths` differs between that commit and the one being released, the release
is refused: the same version would name different code.

The default, the manifest's directory, suits a crate whose files all live
there. Widen it when the package also ships files from elsewhere, such as a
workspace `Cargo.lock` or a shared `README.md`:

```yaml
    paths: ["crates/onto-cli/**", "Cargo.lock", "README.md"]
```

### Record tags and collisions

Each package's record tag must be distinct from every other package's and
from the release tag; `check` refuses duplicates.

`check` compares the tags of the versions in the file now. A collision can
also come from history. Suppose crates.io has the crate `rs-rich` and PyPI has
the Python package `rs-rich`, both tagged with the default `{name}-v{version}`.
When the crate was at 0.0.10, release-kit pushed the tag `rs-rich-v0.0.10`.
When the Python package reaches 0.0.10, its tag would be `rs-rich-v0.0.10`
too, already on another commit.

release-kit catches this. A package tag that already exists on the remote, at
a commit other than the one being released, must be that package's own
record: an annotated tag whose message starts with
`<name> <version> (<registry>)`, which is what `release.py tag` writes. If it
is not, `tag` and the gate refuse:

```text
these tags already exist for something else:
  rs-rich-v0.0.10 at 4f2a9c1d7e3b: not rs-rich 0.0.10 (pypi)'s record
Give the package a tag format of its own in release-kit.yml (for example
`tag: "python-v{version}"` for a PyPI package named like a crate), or bump its version.
```

The fix is a tag format of its own:

```yaml
  - name: rs-rich
    registry: pypi
    manifest: crates/rich-py/pyproject.toml
    version: "0.0.10"
    tag: "python-v{version}"
```

The check covers packages that are not yet published. A lightweight tag, or
one made by other tooling with a different first line, is also "not its own".

A tag that points at a commit with no `release-kit.yml` (made before the
repository adopted release-kit) is refused with an explanation:

```text
rs-rich-v0.0.9 is on 1a2b3c4d5e6f, which has no release-kit.yml: the tag predates
release-kit. Bump the package's version, or delete the stale tag if nothing was
published from it.
```

## Artifacts

crates.io and npm publish from source and take no artifacts (`check` refuses
them). GitHub Releases, PyPI and the VS Code Marketplace need at least one.

Each artifact is a mapping with a `kind`. The release run builds only the
selected packages' artifacts, each artifact (and target) in its own job, on
the runner it needs.

| `kind` | Builds | Registries | Required | Optional |
|---|---|---|---|---|
| `rust-binary` | `<bin>-v<version>-<target>.tar.gz` (`.zip` on Windows), holding the binary and the `include` files | `github` | `bin` | `crate`, `targets`, `include`, `test` |
| `python` | an sdist and a wheel, with `python -m build` | `pypi`, `github` | | `dir`, `test` |
| `maturin` | a wheel per target with maturin, plus an sdist | `pypi`, `github` | | `targets`, `sdist`, `test` |
| `vsix` | `<name>-<version>.vsix`, with `vsce package` | `vscode`, `github` | | `dir`, `test` |
| `command` | whatever `run` makes, collected by the `outputs` glob | `github`, `pypi`, `vscode` | `run`, `outputs` | `os`, `test` |

Any other field is refused (`rust-binary does not take dir`).

### `rust-binary`

```yaml
    artifacts:
      - kind: rust-binary
        bin: onto                 # the [[bin]] to build
        crate: onto-cli           # default: the manifest's package name
        include: [README.md, LICENSE]
        targets: [x86_64-unknown-linux-gnu, aarch64-apple-darwin]
```

Each target's job runs
`cargo build --release --locked -p <crate> --bin <bin> --target <target>`
and archives the binary as `<bin>-v<version>-<target>/<bin>` (`.exe` on
Windows) with each `include` file beside it. `include` paths are from the
repository root; inside the archive each file keeps only its file name. On
Unix the binary is mode `0755`.

### `python`

```yaml
      - kind: python
        dir: python               # default: the manifest's directory
```

Runs `python -m pip install build` and `python -m build --outdir dist <dir>`:
an sdist and a pure wheel. One job on `ubuntu-latest`.

### `maturin`

```yaml
      - kind: maturin
        targets: [x86_64-unknown-linux-gnu, aarch64-apple-darwin, x86_64-pc-windows-msvc]
        sdist: true               # default true
```

Builds from the `Cargo.toml` beside the `pyproject.toml` with
[`PyO3/maturin-action`](https://github.com/PyO3/maturin-action)
(`--release`, `manylinux: auto`), one job per target. With `sdist: true` an
extra job, `<slug>-sdist`, builds the sdist on `ubuntu-latest`.

### `vsix`

```yaml
      - kind: vsix
        dir: editors/vscode       # default: the manifest's directory
```

Runs `npm ci` in `dir` (or `npm install` without a `package-lock.json`), then
`npx @vscode/vsce package`, writing `<name>-<version>.vsix`, where `<name>` is
the part of the package name after `<publisher>.`. Node 24, on `ubuntu-latest`.

### `command`

```yaml
      - kind: command
        run: ./scripts/package.sh "$VERSION" "$OUT"
        outputs: "target/package/*.tar.gz"
        os: ubuntu-latest         # default
```

`run` runs in bash (on Windows runners, Git's bash) from the repository root,
with these variables set:

| Variable | Value |
|---|---|
| `VERSION` | the package's version |
| `PACKAGE` | the package's name |
| `OUT` | the absolute path of the output directory (`dist`) |

Then every file matching `outputs` (a glob from the repository root) is copied
into `OUT`, unless it is already there. Files `run` writes straight into
`$OUT` are collected too. The build action installs nothing extra for this
kind: use what the runner has, or install it in `run`.

### Targets and runners

`targets` (for `rust-binary` and `maturin`) is a list. Each entry is a Rust
target triple, or `{target: ..., runner: ...}` for a runner the kit does not
know. The default is the first five below.

| Target | Runner |
|---|---|
| `x86_64-unknown-linux-gnu` | `ubuntu-latest` |
| `aarch64-unknown-linux-gnu` | `ubuntu-24.04-arm` |
| `aarch64-apple-darwin` | `macos-latest` |
| `x86_64-apple-darwin` | `macos-latest` (cross-compiled; GitHub retired the Intel runners) |
| `x86_64-pc-windows-msvc` | `windows-latest` |
| `aarch64-pc-windows-msvc` | `windows-11-arm` (not in the default list) |

```yaml
        targets:
          - x86_64-unknown-linux-gnu
          - {target: x86_64-unknown-linux-musl, runner: ubuntu-latest}
```

A target with no known runner and none given is refused by `check`.

### `test`: check what was built

Every kind takes an optional `test`. It runs in the build job, right after the
build, on the files just built. If it fails, the build fails, and nothing that
needs the build is published.

A `test` is a bash command, run from the repository root with these variables:

| Variable | Value |
|---|---|
| `DIST` | the absolute path of the output directory |
| `FILES` | the files this job built, as absolute paths separated by spaces (no `.sha256` files) |
| `VERSION` | the package's version |
| `PACKAGE` | the package's name |
| `TARGET` | the job's target, or empty for kinds without targets |

```yaml
      - kind: rust-binary
        bin: onto
        targets: [x86_64-unknown-linux-gnu, aarch64-unknown-linux-gnu]
        test: |
          tar -xzf "$FILES" -C "$RUNNER_TEMP"
          "$RUNNER_TEMP/onto-v$VERSION-$TARGET/onto" --version | grep -F "$VERSION"
```

For `rust-binary` and `maturin`, a mapping limits the test to some targets.
A cross-compiled build cannot run on its runner (an `x86_64-apple-darwin`
wheel built on Apple Silicon, for example):

```yaml
      - kind: maturin
        targets: [x86_64-unknown-linux-gnu, aarch64-apple-darwin, x86_64-apple-darwin]
        test:
          run: pip install "$DIST"/*.whl && python -c "import onto"
          targets: [x86_64-unknown-linux-gnu, aarch64-apple-darwin]
```

A mapping without `targets` runs after every job, like a string. Kinds that
are not built per target (`python`, `vsix`, `command`) take a string, or a
mapping without `targets`; `check` refuses `targets` there. The sdist job of a
`maturin` artifact runs no test.

### What each job produces

Every build job uploads its output directory as a workflow artifact named
`release--<slug>--<job id>`. The *slug* is the package name with every run of
characters other than letters, digits, `.`, `_` and `-` replaced by `-`
(`@org/pkg` becomes `org-pkg`). Job ids are listed under
[`plan`](commands.md#plan). For a `github` package, every file gets a
`<file>.sha256` beside it, in `sha256sum` format.

## What `check` refuses

- an unknown registry, or a package listed twice for the same registry;
- a version that is not SemVer (PEP 440 for PyPI), or that differs from the
  manifest (`run release.py sync`);
- a crates.io, PyPI, npm or vscode package whose manifest names another package;
- two packages with the same record tag, or a record tag equal to the release tag;
- a package listed before a package of the same registry it depends on;
- artifacts on crates.io or npm packages, no artifacts on the others, an
  unknown kind, a kind its registry cannot publish, a missing or unknown
  field, a malformed `test`, or a target with no runner.

Before any of that, the file must load. These stop `check` at once: an empty `packages:`, a package missing `name`,
`registry`, `manifest` or `version`, an unquoted version, `artifacts` that are
not a list of mappings, or a `name_from` that names no package.
