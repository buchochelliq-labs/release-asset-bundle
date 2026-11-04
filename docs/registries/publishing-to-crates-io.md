# Publishing to crates.io

A crate that crates.io will refuse should fail the pull request, not the
release run. `release.py check` asks the questions `cargo publish` will ask
later, for every package with `registry: crates.io`, and the release run's
`preflight --registry crates.io` asks them again just before uploading.

Setting up the release workflow, credentials and Trusted Publishing is covered
in [crates.io](crates-io.md). This page covers what has to be true of the
crates themselves.

## Setup, in order

1. **Declare the crates.** List each crate in `release-kit.yml` with
   `registry: crates.io`, after the crates it depends on. `check` warns about
   library crates in the workspace that `release-kit.yml` publishes nowhere.
   That is only a warning, because some crates are meant to stay private.
2. **Create the `crates-io` environment** in the repository's settings.
3. **First versions use a token.** Trusted Publishing works only for a crate
   that already exists. Before the first release of a new crate, add a
   `CARGO_REGISTRY_TOKEN` secret (a crates.io API token with the
   `publish-new` scope) to the `crates-io` environment.
4. **Then add Trusted Publishers.** After a crate's first version is out, open
   its settings on crates.io and add a Trusted Publisher: this repository,
   the workflow `release.yml` and the environment `crates-io`. Once every
   crate has one, you can delete the token.
5. **Run `check` in CI**, and also the dry run if you can spare the build
   time. Cargo must be on `PATH` (GitHub's hosted runners have it):

    ```yaml
          - run: python3 -m pip install --quiet pyyaml
          - run: python3 .github/release-kit/scripts/release.py check --publish-dry-run
    ```

## What `check` refuses

For each crates.io package, `check` reads the manifests with
`cargo metadata --no-deps`, so workspace inheritance and renamed dependencies
are read the way cargo reads them. Each message names the fix.

| Problem | Message (abridged) | Fix |
|---|---|---|
| `publish = false` | `onto: crates/onto/Cargo.toml says publish = false` | Remove the line, or take the package out of `release-kit.yml`. |
| `publish = ["other"]` | `onto: ... publishes only to other` | Add `"crates-io"` to the list, or remove `publish`. |
| A `git` dependency with no `version` | `onto-ods: depends on ods-erd from git (...) with no version` | Publish the dependency to crates.io first, then add a `version`. |
| A `path` dependency with no `version` | `onto-cli: depends on onto by path with no version; crates.io needs one: onto = { path = "../onto", version = "0.2.0" }` | Add the version shown. |
| A dependency from another registry | `onto: depends on x from the registry ...` | crates.io accepts only crates.io dependencies. |
| A dependency that is not on crates.io at a matching version | `onto-ods: depends on ods-erd ^0.3, which is not on crates.io` | Publish it first, or list it in `release-kit.yml` before the crate that needs it. |
| The name belongs to someone else | `onto: the name is taken on crates.io (owners: ...)` | Rename the package. See [taken names](#a-taken-name). |
| `cargo package --list` fails | `` onto: `cargo package --list` fails: ... `` | What cargo says. |
| An embed from outside the crate | `onto-cli: src/main.rs embeds ../../../skills/onto.md, outside the crate` | Copy the file into the crate. See [embeds](#an-embed-from-outside-the-crate). |
| An embed the package leaves out | `onto-cli: src/main.rs embeds data.json, which the package does not carry` | Add it to `include`, or take it out of `exclude`. |

Only normal and build dependencies count. `cargo publish` drops a
dev-dependency that has a path and no version.

A dependency is checked against crates.io when publishing would turn it into
a crates.io dependency, that is, a `git` or `path` dependency that has a
`version`. If it is a crates.io package of this release listed before the
crate, it passes, because the release publishes it first. If it is listed
after the crate, `check` already refuses the order. Plain crates.io
dependencies are not asked about: `Cargo.lock` already resolved them from
crates.io.

### Who may publish a name

`check` reads the public API, `GET /api/v1/crates/<name>` and
`GET /api/v1/crates/<name>/owners`. The repository's owner is the GitHub user
or organisation in `GITHUB_REPOSITORY`, or in the `origin` remote when run
locally.

| crates.io says | `check` |
|---|---|
| no such crate | passes: it will be a first version, published with the token |
| a crate whose name differs only in case, `-` or `_` | refuses: crates.io treats them as one name |
| the crate's `repository` is on GitHub under the repository's owner | passes (for example, a crate an organisation member owns personally) |
| an owner is the repository's owner, or a team of it (`github:<org>:<team>`) | passes |
| the crate's `repository` is on GitHub under someone else, and no owner matches | refuses: the name is taken |
| anything else, such as no `repository` | warns: release-kit cannot tell |

Whether the credential can publish the crate is not checked. The token is not
available to `check`, and crates.io settles a Trusted Publisher when the run
exchanges its OIDC token. A missing Trusted Publisher still fails at upload.

### Packaging

For every crates.io package, `check` runs `cargo package --list` and scans the
listed Rust sources for `include_str!`, `include_bytes!` and `include!` with a
literal path, either relative to the file or
`concat!(env!("CARGO_MANIFEST_DIR"), "...")`. It skips `tests/`, `benches/`,
`examples/` and code under `#[cfg(test)]` or `#[test]`, because
`cargo publish` does not build them.

The scan is a quick approximation. `check --publish-dry-run` runs the real
thing, as the release does:
`cargo publish --dry-run --locked -p A -p B ...` for every crates.io
package, one cargo call per workspace. It builds every crate, so it belongs
in CI rather than in a quick local check. It needs Cargo 1.90 or later.

### Offline, and without cargo

The checks that ask crates.io (dependencies, owners) are skipped with
`check --offline`, or `RELEASE_KIT_OFFLINE=1` (which also covers
`preflight`). Cargo is then run with `--offline` too. Without cargo on
`PATH`, `check` prints a warning and skips every crates.io check: it still
checks `release-kit.yml` against the manifests.

## Common blockers

### A git dependency

crates.io refuses a crate with a git dependency, because a crate on crates.io
must build from crates.io alone. The dependency has to be published first,
often from another repository. Give it a version beside `git`: cargo builds
from git locally, and the published crate depends on the crates.io version.

```toml
[dependencies]
ods-erd = { git = "https://github.com/opendatasuite/open-data-suite", version = "0.3" }
```

`check` then asks crates.io for a matching `ods-erd` and refuses until there is
one, which makes "publish ODS first" visible in the pull request.

### A taken name

If someone else owns the name, rename the package. A `[[bin]]` table keeps the
command's name:

```toml
[package]
name = "onto-cli"        # the crate, on crates.io

[[bin]]
name = "onto"            # the command people type
path = "src/main.rs"
```

Then update `name:` in `release-kit.yml`. If the crate is yours but owned by
a person, have that person add the organisation's team
(`cargo owner --add github:<org>:<team>`).

### An embed from outside the crate

A package carries only its crate directory. `include_str!("../../../skills/onto.md")`
builds in the workspace but not from the package. Copy the file into the
crate, embed the copy, and add a test that keeps the copy in step with the
original:

```rust
const SKILL: &str = include_str!("../skills/onto.md");

#[cfg(test)]
mod tests {
    #[test]
    fn the_skill_matches_the_original() {
        assert_eq!(super::SKILL, include_str!("../../../skills/onto.md"));
    }
}
```

The test embeds the original from outside the crate, which is fine:
`cargo publish` does not build tests, and `check` does not scan them.

### Path dependencies without a version

Within a workspace, `onto = { path = "../onto" }` builds. crates.io needs a
version on it as well. With `version.workspace = true`, declare internal
crates once in the root manifest:

```toml
[workspace.dependencies]
onto = { path = "crates/onto", version = "0.2.0" }
```

and use `onto.workspace = true` in each crate.
