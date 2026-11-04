# Migrating from release-kit 1.x

rab 2.0.0 is release-kit renamed: the product is now **rab**, the Release Asset
Bundle (BAB), and the repository is
[`buchochelliq-labs/release-asset-bundle`](https://github.com/buchochelliq-labs/release-asset-bundle).
GitHub redirects the old URL, so an existing clone keeps working, but point it
at the new one.

The names a repository keeps on disk change too:

| release-kit 1.x | rab 2.x |
|---|---|
| `release-kit.yml` | `rab.yml` |
| `.github/release-kit/` (the installed copy) | `.github/rab/` |
| `.github/workflows/release-kit-build.yml` | `.github/workflows/rab-build.yml` |
| `python3 .github/release-kit/scripts/release.py …` | `python3 .github/rab/scripts/release.py …` |
| `RELEASE_KIT_OFFLINE=1` | `RAB_OFFLINE=1` |

The file's contents, the commands and the script's name (`release.py`) do not
change.

## The old names keep working until 3.0.0

For the whole 2.x series, a repository that has not moved yet keeps working,
and each old name prints a warning naming the new one:

- every command that reads the configuration (`check`, `sync`, `status`,
  `tag`, `gate`, `plan`, `preflight`, `record`, …) reads `rab.yml`, else
  `release-kit.yml`. **Both present is refused**: delete the one you do not
  mean (normally `release-kit.yml`).
- A tagged commit is read the same way, so releases tagged under release-kit
  are still resumed and checked.
- `vendored` checks a copy at `.github/rab/`, else one at
  `.github/release-kit/`. A `.github/release-kit/` left next to
  `.github/rab/` is reported.
- `RELEASE_KIT_OFFLINE` is read when `RAB_OFFLINE` is unset.

In GitHub Actions the warnings are annotations, so they show on the run's
summary. rab 3.0.0 drops the old names.

## Moving a repository

`install` does the renaming. From the repository's root, on a branch:

```sh
# The kit's checkout: clone it again, or repoint an old clone.
git clone https://github.com/buchochelliq-labs/release-asset-bundle ../release-asset-bundle
# (or: git -C ../release-kit remote set-url origin https://github.com/buchochelliq-labs/release-asset-bundle)
git -C ../release-asset-bundle checkout v2.0.0

python3 ../release-asset-bundle/scripts/release.py install
```

In a repository with the 1.x layout, `install` first migrates it and prints
each change:

```text
migrated /path/to/repo from release-kit 1.x:
  renamed release-kit.yml -> rab.yml (git mv)
  removed .github/release-kit/ (replaced by .github/rab/)
  removed .github/workflows/release-kit-build.yml (replaced by .github/workflows/rab-build.yml)
  rewrote 2 old paths in .github/workflows/ci.yml (.github/release-kit/ -> .github/rab/, release-kit-build.yml -> rab-build.yml)
  rewrote 12 old paths in .github/workflows/release.yml (...)
installed rab v2.0.0 into /path/to/repo:
  ...
```

It rewrites the old paths in every workflow under `.github/workflows/`, not
only `release.yml`: a CI workflow that runs `check` or `vendored` would
otherwise point at a directory that no longer exists. It changes nothing when
both `rab.yml` and `release-kit.yml` exist.

Then:

1. **Copy the template changes into `release.yml`.** `install` never rewrites
   your `release.yml` beyond the paths above, and these came after 1.7.0
   (compare with
   [`templates/release.yml`](https://github.com/buchochelliq-labs/release-asset-bundle/blob/main/templates/release.yml)):
    - the **`record` job**, at the end, with its *Check the tag still names
      the gated commit* step; list every publish job of yours in its `needs`
      (see [The release record](release-record.md));
    - **`publish-homebrew` and `publish-chocolatey`**: `needs: [gate, ci,
      build, publish-github]`, and the `if:` that also runs them when the
      gate selected a published release only to finish them (`publish-github`
      skipped, `ci` and `build` successful);
    - **`package-manager-cache: false`** on `actions/setup-node` in the
      `publish-npm` and `publish-vscode` jobs (release jobs write no caches).
2. **`open-vsx: true`** on each `vscode` package in `rab.yml` that is also
   published to Open VSX (see [VS Code](registries/vscode.md)); without it the
   upload still happens, unchecked, with a warning.
3. **Other references.** Search the repository for what `install` does not
   touch: `git grep -n -e release-kit -e RELEASE_KIT` (documentation, agent
   instructions, scripts, `RELEASE_KIT_OFFLINE` in an `env:`).
4. **Check and commit.**

    ```sh
    python3 .github/rab/scripts/release.py check
    python3 .github/rab/scripts/release.py vendored
    git add -A rab.yml .github
    git status        # release-kit.yml -> rab.yml shows as a rename
    ```

    Open a PR with everything together. The next release after it merges is
    made by rab; nothing about tags, versions or registries changes.

A release started under release-kit 1.x and not finished can be finished
after the move: `tag` reads the old commit's `release-kit.yml`.

## Behaviour changes in 2.0.0

Besides the names, see the [changelog](changelog.md#v200). One changes what
`tag` does: a **rejected Chocolatey version is terminal**. The community
repository never takes a rejected version again, so `tag` no longer selects
it to retry: `status` shows `rejected: publish a new version`. See
[the retry policy](recovery.md#what-each-channel-allows-again-the-retry-policy).
