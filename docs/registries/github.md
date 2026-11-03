# GitHub Releases

`registry: github` publishes built files as the assets of a GitHub release on
the package's record tag. Use it for binaries, archives, or a copy of a
package's wheels or `.vsix`.

```yaml
  - name: onto-cli-binaries
    registry: github
    manifest: crates/onto-cli/Cargo.toml
    version: "0.2.0"
    tag: "onto-cli-bin-v{version}"
    artifacts:
      - kind: rust-binary
        bin: onto
        crate: onto-cli
```

A `github` package's `name` is free: `check` does not compare it with the
manifest. Its version still comes from the manifest. It needs at least one
artifact (`rust-binary`, `python`, `maturin`, `vsix` or `command`).

## Credentials and setup

None. The `publish-github` job uses the job's `github.token` with
`contents: write`. It has no environment.

## Publish

The `publish-github` action, for each selected `github` package in order:

1. runs `preflight`;
2. takes the package's files from `dist/release--<slug>--*/` (every build
   job's output, including the `.sha256` files), and fails if there are none;
3. creates the release on the tag, if it does not exist:
   `gh release create <tag> --verify-tag --title "<name> <version>" --generate-notes`,
   with `--prerelease` when the version contains `-`;
4. uploads the files with `gh release upload --clobber`;
5. downloads every asset the release now serves into a new directory and
   checks each `.sha256` against its file with `sha256sum -c`.

Then it runs `verify --registry github --timeout 120`.

### Inputs

| Input | Default | Meaning |
|---|---|---|
| `selection` | required | the gate's selection |
| `dir` | `dist` | where the build artifacts were downloaded (not merged) |
| `files` | `""` | a glob of files to attach instead (relative to the workspace); only for a single GitHub package |
| `token` | `${{ github.token }}` | a token with `contents: write` on this repository |
| `verify-only` | `"false"` | `"true"` skips the preflight and upload |

## Verify

A `github` package counts as published when the release on its tag exists
and has at least one asset. `verify` reads the GitHub API with `GH_TOKEN` or
`GITHUB_TOKEN` when set.

## Consume

Nothing beyond step 5: the action has already downloaded every file as a
stranger would and checked its checksum.
