# release-kit

release-kit is the buchochelliq-labs release standard. It was generalised from
the release scripts of [rs-rich-cli](https://github.com/buchochelliq-labs/rs-rich-cli),
which publishes about twenty crates and a Python package from one repository.

It has three parts:

- **`release-kit.yml`**, a file at the root of your repository. It lists every
  *package* the repository publishes (a crate, a Python package, an npm
  package, a VS Code extension, or a set of files on a GitHub release), the
  *registry* each one goes to, and the *artifacts* each one is built into.
- **`release.py`**, one Python script. People run it to check the file and to
  tag a release. The release workflow runs it to gate, plan, build, publish,
  verify and consume.
- **A release workflow**: composite actions (`gate`, `plan`, `build`,
  `publish-github`, `publish-crates`, `publish-pypi`, `publish-npm`,
  `publish-vscode`), a reusable build workflow, and a starter `release.yml`.

A repository does not reference release-kit remotely. It keeps an
*installed copy* of the script, the actions and the build workflow under
`.github/`, made by `release.py install`. See [Getting started](getting-started.md).

## Terms

Release
:   One version of the repository, cut from one commit of the default branch.
    It is named by one package's version (by default the last package listed),
    and has one *release tag*, by default `release-<version>`.

Package
:   One thing published to one registry under one name and version, such as
    the crate `rs-rich` 0.0.12 on crates.io.

Record tag
:   An annotated tag per package, by default `<name>-v<version>`. It records
    that this version of the package was released from this commit. Pushing
    it starts nothing.

Due
:   A package is due when its exact version is not yet on its registry.

Selection
:   The list of packages a release run publishes, worked out by the gate.

## The six rules

1. **A published version is permanent.** Nothing already on its registry is
   tagged, selected or uploaded again. Every publish step checks once more just
   before uploading, then waits until the registry serves the new version.
2. **Releases come from the default branch.** Every tag points at a commit on
   the default branch (`main`, after the release PR merges), and the release
   run refuses any other.
3. **Tags are records.** Every tag is annotated and records its release and
   commit (`Release:` and `Commit:` lines). Each package gets its own record
   tag, which starts nothing; the release tag starts the one run that
   publishes the whole release. A package tag that already exists on another
   commit must be that package's own record, or release-kit refuses it.
4. **A release is one commit.** A package already tagged on another commit
   that ships different files under the same version is refused: release from
   that commit, or bump the package.
5. **Re-run, don't repair.** If a release run fails, fix the cause and run
   `release.py tag` again. A run that published nothing has its tags moved to
   the fixed commit. A run that published part of the release is started
   again, and publishes only what is still missing.
6. **Least privilege.** Only the publish jobs can write. Registries use
   Trusted Publishing (short-lived OIDC credentials) where they offer it, in
   protected environments. Every third-party action is pinned to a commit.

## The flow

```text
release.py tag ──▶ gate ──▶ ci ──▶ build ──▶ publish ──▶ verify ──▶ consume
   (a person)      └──────────── one run of release.yml ────────────────┘
```

1. **tag.** On the default branch, a person runs
   `python3 .github/release-kit/scripts/release.py tag`. It pushes a record tag
   for each due package, then the release tag, and waits for the run.
2. **gate.** The run checks the release tag: annotated, on the default
   branch, recording its commit, `release-kit.yml` valid at that commit, no
   split release, every due package's record tag on the commit. It outputs the
   selection.
3. **ci.** Your own CI workflow runs once more, on the tagged commit.
4. **build.** One job per artifact and target builds the selected packages
   and runs each artifact's `test`.
5. **publish.** One job per registry uploads, in `release-kit.yml` order.
   Each first refuses any version that is already out (the *preflight*).
6. **verify.** Each publish job waits until the registry serves every
   version it uploaded.
7. **consume.** For crates.io and PyPI (and npm in a verify-only run; see
   [npm](registries/npm.md)), the job then uses each package as a stranger
   would: it installs or resolves it from the registry with no credentials.

crates.io and npm publish from source, so their jobs do not wait for the
build. See [The workflow](workflow.md) for every job and condition, and
[Commands](commands.md) for every flag.
