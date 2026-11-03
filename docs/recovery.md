# Recovering a failed release

The rule is: **re-run, don't repair.** Fix the cause, then run
`release.py tag` again. Do not delete or move tags by hand, and do not use
GitHub's "Re-run jobs" button on a publish job: `tag` works out what happened
and what is still due.

```sh
git switch main && git pull
python3 .github/release-kit/scripts/release.py tag --dry-run   # see what it would do
python3 .github/release-kit/scripts/release.py tag
```

## What `tag` can prove: `dead()`

Moving a tag is safe only if nothing was published from it. `tag` moves a tag
only when the latest release run for it proves that. In the code this is
`dead(run, commit)`, true when all of these hold:

- the run ran on the tagged commit (its head SHA is that commit);
- the run has completed;
- it did not succeed;
- no job whose name starts with `publish` started (a job counts as started
  unless it was still queued or was skipped).

If `tag` cannot list the run's jobs, it assumes a publish job started. Without
the GitHub CLI (`gh`) it cannot see runs at all, so no run is ever proven
dead: it moves nothing and only waits.

## Case 1: the run failed before publishing

For example, CI failed on the tagged commit. The run is dead.

Fix the cause in a PR and merge it. Then run `tag` on the default branch. It
finds that the record tags and the release tag are on a commit whose run
published nothing, and moves them to `HEAD`:

```text
Release 0.2.0 from 7c1d... (the local checkout (HEAD))
move   onto-v0.2.0: the release run at 3e9a0b1c2d4f published nothing
move   onto-cli-v0.2.0: the release run at 3e9a0b1c2d4f published nothing
move   release-0.2.0: its run at 3e9a0b1c2d4f ended failure before publishing
pushed release-0.2.0; waiting for the release run
```

The moved tags are deleted from the remote in one atomic push and pushed
again at the new commit. The new tag starts a new run.

## Case 2: the run published part of the release

For example, `publish-crates` uploaded `onto` and then failed on `onto-cli`
because of a missing secret. A publish job started, so the run is not dead,
and the release stays on its commit: `onto` 0.2.0 is published from it, and
rule 1 says that is permanent.

Fix the cause outside the tagged commit (add the secret, fix the environment,
wait out a registry outage). Then run `tag`. It goes back to the commit where
the release is tagged, keeps the record tags, skips what is published, and
pushes the release tag again:

```text
Release 0.2.0 from 3e9a... (release 0.2.0 is already tagged there)
skip   onto-v0.2.0: onto 0.2.0 (crates.io) is already published
keep   onto-cli-v0.2.0: already tagged here
retag  release-0.2.0: its run ended failure; the new run publishes only what is still due
```

The new run's gate selects only `onto-cli`.

If the fix must change files that the tagged commit ships, the release cannot
move there: that would split it (rule 4). Bump the versions of the packages
that are still due, merge, and release the new versions.

### Partial uploads per registry

- **crates.io, npm:** each crate or package is one upload. A re-run selects
  the ones not yet on the registry, in order.
- **PyPI:** the package counts as published as soon as the version exists on
  PyPI, that is, once any one of its files is up. If an upload stopped after
  some wheels, a re-run does not upload the rest. Upload the missing files
  from the run's build artifacts by hand (for example with `twine upload`).
- **GitHub Releases:** a package counts as published once its release has at
  least one asset. If an upload stopped partway, a re-run does not upload the
  rest. Check the release's files against the build artifacts, and upload any
  missing ones (with their `.sha256`) with `gh release upload`.
- **VS Code Marketplace:** the package counts as published once the
  Marketplace lists the version. Open VSX is not checked; if only the Open VSX
  upload failed, publish that `.vsix` to Open VSX by hand.

## Case 3: the run is still going, or `tag` stopped waiting

`tag` waits up to `--timeout` seconds (default 10800, three hours). If it
stops while the run is live, it says so:

```text
error: not published within 180 minutes: onto-cli 0.2.0 (crates.io). The run is
still going (https://github.com/...); run `release.py tag` again to keep waiting:
it changes nothing while the run is live.
```

Run `tag` again. With the release tag on its commit and its run live, it
prints `wait` and waits; it pushes nothing. A record tag on another commit
that ships the same files, whose run may still publish it, is also waited for
and left alone.

## Case 4: everything uploaded, but verify or consume failed

For example, crates.io took longer than `verify`'s 15 minutes to serve a
version, or the consumer check hit a flaky network. Everything is published,
so there is nothing for `tag` to do. To get a green record, start a
verify-only run.

### Verify-only runs

```sh
gh workflow run release.yml -f tag=release-0.2.0 -f verify_only=true
```

In a verify-only run:

- the gate (`gate --verify-only`) selects every package whose record tag is
  on the release's commit, and refuses if any of them is not yet published
  ("Run `release.py tag` again to publish them");
- the build is skipped;
- each publish job is started with `verify-only: true`: it skips the
  preflight and the upload, and runs `verify` (and `consume` for crates.io,
  PyPI and npm).

A verify-only run never uploads, so it cannot finish a partial upload. Use
`tag` for that.

## Errors that need a decision

`tag` and the gate refuse, rather than guess, in these cases:

| Message starts | Meaning and fix |
|---|---|
| `these tags already exist for something else` | A package tag on the remote is not that package's record. Give the package its own `tag:` format, or bump it. See [collisions](configuration.md#record-tags-and-collisions). |
| `... has no release-kit.yml: the tag predates release-kit` | A record tag is on a commit from before adoption. Bump the package, or delete the stale tag if nothing was published from it. |
| `... changes packages already tagged under the same version on other commits` | A split release. Release from the named commit (`--commit`), or bump those packages. |
| `release-X is on <sha>, not <sha>, and its run there may have published` | Release from that commit (`--commit`) or wait for its run. |
| `... is not on origin/main as last fetched` | Run `git fetch`, and release only merged commits. |
| `... every package of release X is already published` | (gate) Nothing to do. |
