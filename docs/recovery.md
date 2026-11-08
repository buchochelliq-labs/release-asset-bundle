# Recovering a failed release

The rule is: **re-run, don't repair.** Fix the cause, then run
`release.py tag` again. Do not delete or move tags by hand, and do not use
GitHub's "Re-run jobs" button on a publish job: `tag` works out what happened
and what is still due, including the Homebrew and Chocolatey channels made
from a published GitHub release ([retry policy](#what-each-channel-allows-again-the-retry-policy)).

```sh
git switch main && git pull
python3 .github/rab/scripts/release.py tag --dry-run   # see what it would do
python3 .github/rab/scripts/release.py tag
```

Every run's [release record](release-record.md) says what it published:
`gh run download <run> -n release-record`, then look at each package's
`outcome`.

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

If the fix is in the release tooling itself (the installed kit, `rab.yml`,
the workflow), a re-run on the tagged commit fails the same way, because it
runs that commit's files. When the fix changes no file a package ships, carry
the release to the commit with the fix: once the run on the tagged commit has
ended, run `tag --commit <sha>` (for example `--commit origin/main` after
`git fetch`):

```text
Release 0.2.0 from 7c1d... (--commit)
skip   onto-v0.2.0: onto 0.2.0 (crates.io) is already published
carry  onto-cli-v0.2.0: the release run at 3e9a0b1c2d4f stopped before publishing it; 7c1d2e3f4a5b ships the same files
tag    onto-cli-v0.2.0 at 7c1d2e3f4a5b (a record)
carry  release-0.2.0: its run at 3e9a0b1c2d4f ended failure; the release is finished from 7c1d2e3f4a5b
```

Published packages keep their record tags where they were published, and the
channels made from them (Homebrew, Chocolatey) are finished from the new
commit. Packages not yet published are tagged there. The release tag records
`Carried-from: <old commit>`, and the gate checks it again: that commit must be
an ancestor, and no package tagged there may ship different files. `tag`
carries only when you ask with `--commit`, or when the tagged commit's
`rab.yml` fails `check` and `HEAD` fixes it, and never while that run is live.
If the carried run fails too (a channel, say), run `tag` again: the
`Carried-from:` trailer keeps the release on the new commit.

If the fix must change files that the tagged commit ships, the release cannot
move there: that would split it (rule 4). Bump the versions of the packages
that are still due, merge, and release the new versions.

### Partial uploads per registry

- **crates.io, npm:** each crate or package is one upload. A re-run selects
  the ones not yet on the registry, in order.
- **PyPI:** the package counts as published as soon as the version exists on
  PyPI, that is, once any one of its files is up. If an upload stopped after
  some wheels, a re-run does not upload the rest. Upload the missing files
  from the run's build artifacts by hand (for example with `twine upload`);
  files uploaded by hand have no attestations (only the release job's
  identity can sign them). A `publish-pypi` job that failed before
  uploading anything, as every PyPI release with rab 2.0.0 did (see the
  [changelog](changelog.md#v201)), leaves nothing on PyPI: install rab 2.0.1
  or later and run `tag` again, and the new run publishes only PyPI.
- **GitHub Releases:** a package counts as published once its release has at
  least one asset. If an upload stopped partway, a re-run does not upload the
  rest. Check the release's files against the build artifacts, and upload any
  missing ones (with their `.sha256`) with `gh release upload`.
- **VS Code Marketplace and Open VSX:** the package counts as published once
  the Marketplace lists the version and, with `open-vsx: true`, Open VSX
  serves it. A re-run uploads only to the registry still missing it.

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

## What each channel allows again: the retry policy

Channels differ in whether publishing again is safe. rab defines a
retry policy per channel; it is not configured.

| Channel | Policy | Publish again? | Done when | What `tag` again does | Needs a manual step |
|---|---|---|---|---|---|
| crates.io, PyPI, npm | `once` | never: a version is permanent (rule 1) | the registry serves the version | skips it | a partial PyPI upload (above) |
| VS Code Marketplace, Open VSX | `once` | never for the same version, on each registry | the Marketplace lists it, and with [`open-vsx: true`](registries/vscode.md) Open VSX serves it too | skips it; with `open-vsx: true`, a version only one registry has is due, and the new run uploads only to the other | an Open VSX upload that failed for a package without `open-vsx: true` |
| GitHub Releases | `finish` | only to finish a draft; a published release's files never change | the release is out of draft with its files | finishes a draft; skips a published one | missing assets on a published release (above) |
| Homebrew tap | `idempotent` | yes: rendering the formula again and updating its branch and pull request is harmless | the tap's default branch has `Formula/<name>.rb` at the version, or a pull request from `<name>-<version>` is open on the tap; a prerelease is never put in the tap | selects the package again for `homebrew` only | merging the pull request (the tap's maintainers) |
| Chocolatey | `until-accepted` | until the community repository has accepted or rejected the version | the API lists the version, or it is submitted (in its checks or moderation); a **rejected** version is terminal: never done, never due | selects the package again for `chocolatey` only; skips a rejected version with a note | a rejected version: fix the package and release a new version |

`release.py status` prints each channel's policy and live state under its
package.

### How the state is read, and why

- **Live, not recorded in tags.** The Homebrew and Chocolatey states are read
  from the tap (GitHub's API) and from the community repository's API every
  time `tag`, the gate or `status` needs them; nothing is written into the
  record tags. A record tag records what was released and is never rewritten
  for a run that published; a channel's state changes outside rab (a
  maintainer merges or closes the pull request, moderators approve or reject
  a package, someone copies a formula by hand), so a stored `homebrew: done`
  could only go stale. The live state is the truth, and costs one or two
  requests per channel.
- **Submitted counts as done.** Chocolatey's moderation can take days, so
  `tag` does not wait for it: a version in moderation is reported as
  `submitted` and counts as done: pushing it again would only replace the
  version under review. Likewise an open tap pull request counts as done:
  merging it is the tap's decision.
- **Rejected is terminal.** A version in moderation can be pushed again (the
  new push replaces the one under review), but a rejected one never can:
  `choco push` refuses it ("Package versions that are approved, rejected, or
  exempted cannot be modified"). So a rejected version is neither done nor
  due. The gate does not select it, `tag` does not retry or wait for it and
  prints `note   chocolatey of <package> is rejected: publish a new version;
  not retried`, and `status` shows `rejected: publish a new version`. Fix
  what moderation objected to and release a new version of the package. The
  package's other channels (Homebrew) are still finished as usual.
- **A newer release supersedes an older one's channels.** `tag` goes back to
  finish an older release's channels only while `HEAD` names that release (or
  with `--release`); once `HEAD` names a newer one it prints a `note` and
  releases `HEAD`, since a tap or package moving back to an older version
  would be a downgrade.
- **Other channels made from a release** fit the same model. Scoop (a bucket
  repository) and AUR (a `PKGBUILD` pushed to the AUR's git) are `idempotent`
  like Homebrew: done when the bucket's default branch or the AUR's RPC has
  the version (or a bucket pull request is open). winget proposes a manifest
  as a pull request on `microsoft/winget-pkgs`: `idempotent` while proposing,
  done when the pull request is open or the manifest for the version is
  merged. Each needs only a state function and a policy.

## Case 5: the release is out, but Homebrew or Chocolatey is not

For example, `publish-chocolatey` failed on a Chocolatey outage after
`publish-github` published the release. Fix the cause outside the tagged
commit (an expired API key, a tap token without pull-request access, an
outage), then run `tag`. It goes back to the release's commit, sees the
GitHub release is published but a channel made from it is not done, and
pushes the release tag again with only that channel due:

```text
Release 0.2.0 from 3e9a... (release 0.2.0 is already tagged there)
skip   onto-v0.2.0: onto 0.2.0 (crates.io) is already published
finish onto-cli-v0.2.0: onto-cli 0.2.0 (github) is published; chocolatey is due (until-accepted)
retag  release-0.2.0: its run ended failure; the new run publishes only what is still due
tag    release-0.2.0 at 3e9a0b1c2d4f (starts the release run: onto-cli (chocolatey))
```

The new run's gate selects `onto-cli` for `chocolatey` only (its selection
entry has `"channels": ["chocolatey"]`): the build plans nothing for it,
`publish-github` is skipped, and `publish-chocolatey` runs. A run that
*succeeded* without finishing a channel (for example, `publish-homebrew` without
`HOMEBREW_TAP_TOKEN` only attaches the formula) is pushed again the same way.
This needs the template's `publish-homebrew` and `publish-chocolatey` jobs to
run when `publish-github` was skipped; see the [changelog](changelog.md).

When `tag` has waited for a run, it polls a channel that is not done yet a
few more times (five minutes), then names it in a `note` and returns rather
than waiting for hours.

## Errors that need a decision

`tag` and the gate refuse, rather than guess, in these cases:

| Message starts | Meaning and fix |
|---|---|
| `these tags already exist for something else` | A package tag on the remote is not that package's record. Give the package its own `tag:` format, or bump it. See [collisions](configuration.md#record-tags-and-collisions). |
| `... has no rab.yml (nor release-kit.yml): the tag predates the kit` | A record tag is on a commit from before adoption. Bump the package, or delete the stale tag if nothing was published from it. |
| `... changes packages already tagged under the same version on other commits` | A split release. Release from the named commit (`--commit`), or bump those packages. |
| `release-X is on <sha>, not <sha>, and its run there may have published` | Release from that commit (`--commit`) or wait for its run. |
| `... is not on origin/main as last fetched` | Run `git fetch`, and release only merged commits. |
| `... every package of release X is already published` | (gate) Nothing to do. |
| `<tag> is at <sha>, not <sha>, the commit the gate checked` | (build) A record tag moved after the gate checked it, and nothing was built. Find out who moved it and why; put it back on the gated commit to run again, or tag a new release. |
