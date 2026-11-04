# The release record

Every release run uploads one artifact, `release-record`, holding one file,
`release-record.json`: what the run set out to publish and what came of it.
It is for a **coordinator**: a workflow or script that releases a project
split across repositories, starting each repository's release in dependency
order and checking that the results agree.

The template's last job, `record`, writes it with
[`release.py record`](commands.md#record). The job runs whatever happened
before it (`if: always()`), so a run that failed halfway, or was cancelled,
still says what it published. It needs no permission beyond the workflow's
`contents: read`.

## Getting it

```sh
# Start a release in another repository and find its run.
gh workflow run release.yml -R org/core -f tag=release-1.4.0
run=$(gh run list -R org/core -w release.yml -b release-1.4.0 -L 1 --json databaseId -q '.[0].databaseId')
gh run watch -R org/core "$run" || true        # a failed run still has a record

# Download and read it.
gh run download -R org/core "$run" -n release-record
jq -r .result release-record.json               # complete | partial | nothing-published | unknown
```

`gh run download <run> -n release-record` writes `release-record.json` into
the current directory. A token that can read the repository's Actions
artifacts is enough (`actions: read` on a private repository).

!!! note "Repositories that installed an older release-kit"
    `install` writes `release.yml` only when a repository has none, so an
    existing `release.yml` has no `record` job. Copy it from
    [`templates/release.yml`](https://github.com/buchochelliq-labs/release-kit/blob/main/templates/release.yml)
    to the end of yours, and list in its `needs` every job of yours that
    publishes (`record` refuses a selected package whose publish job it does
    not need).

There is no record when the `record` job itself did not run: the tag could
not be checked out, or the run was stopped before any job started.

## An example

A run whose crates.io job published `core`, then failed on `tool`, while the
binaries' build failed, so nothing reached GitHub Releases or PyPI:

```json
{
  "schema_version": 1,
  "repository": "org/project",
  "release": "0.2.0",
  "tag": "release-0.2.0",
  "commit": "3e9a0b1c2d4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b",
  "verify_only": false,
  "run": {"id": 1234567890, "attempt": 1,
          "url": "https://github.com/org/project/actions/runs/1234567890"},
  "result": "partial",
  "jobs": {"gate": "success", "ci": "success", "build": "failure",
           "publish-github": "skipped", "publish-homebrew": "skipped",
           "publish-chocolatey": "skipped", "publish-crates": "failure",
           "publish-pypi": "skipped", "publish-npm": "skipped", "publish-vscode": "skipped"},
  "packages": [
    {"name": "core", "registry": "crates.io", "version": "0.1.0", "tag": "core-v0.1.0",
     "selected": true, "outcome": "published", "job": "failure",
     "url": "https://crates.io/crates/core/0.1.0", "files": []},
    {"name": "tool", "registry": "crates.io", "version": "0.2.0", "tag": "tool-v0.2.0",
     "selected": true, "outcome": "failed", "job": "failure", "url": null, "files": []},
    {"name": "tool-py", "registry": "pypi", "version": "0.2.0", "tag": "tool-py-v0.2.0",
     "selected": true, "outcome": "skipped", "job": "skipped", "url": null,
     "files": [{"name": "tool_py-0.2.0-py3-none-any.whl",
                "sha256": "4c1b…", "size": 18342}]}
  ]
}
```

## The schema

The JSON Schema is [`release-record.schema.json`](release-record.schema.json)
(in the repository at `docs/release-record.schema.json`; `release.py record
--schema` prints it). `release.py record` checks every record against it
before writing.

### Versioning

`schema_version` is `1`. It changes only when a field is removed or renamed,
or changes meaning. New fields, and new values of `outcome`, `result` and job
results, may appear without it: ignore fields you do not know, and treat an
unknown `outcome` as not published.

### The run

| Field | Value |
|---|---|
| `schema_version` | `1` |
| `repository` | `owner/name` |
| `release` | the release's name (`release-kit.yml`'s `name_from` package's version), or `null` if `release-kit.yml` could not be read at the tag |
| `tag` | the release tag the run was for |
| `commit` | the tagged commit (40 hex digits), or `null` if the tag could not be resolved |
| `verify_only` | whether this was a [verify-only run](recovery.md#verify-only-runs) |
| `run` | `id`, `attempt` and `url` of the workflow run |
| `result` | the run as a whole: see below |
| `jobs` | the result of each job the `record` job needs: `success`, `failure`, `cancelled` or `skipped` |
| `packages` | every package of `release-kit.yml` at the tagged commit, in its order |

`result`:

| Value | Meaning |
|---|---|
| `complete` | every selected package is on its registry, and its publish job succeeded (in a verify-only run: was verified) |
| `partial` | this run published some selected packages, but not all, or a published package's job failed afterwards (in `verify` or `consume`) |
| `nothing-published` | this run published none of them: it failed before publishing, its uploads all failed, the gate refused, or a verify-only run failed |
| `unknown` | nothing is known to be published, and a registry did not answer for at least one package |

### Each package

| Field | Value |
|---|---|
| `name`, `registry`, `version` | as in `release-kit.yml` |
| `tag` | its record tag |
| `selected` | whether the gate selected it for this run |
| `outcome` | see below |
| `job` | its publish job's result (`publish-crates` for a crate, and so on), or `null` if not selected |
| `url` | where its registry serves this version, when `outcome` is `published` or `already-published`; else `null` |
| `files` | the files this run built for it: `name`, `sha256` and `size` |
| `channels` | a selected `github` package with `homebrew` or `chocolatey`: those jobs' results |

`outcome`:

| Value | Meaning |
|---|---|
| `published` | this run put it on its registry |
| `already-published` | it was on its registry before this run: the gate did not select it because it was published, or a verify-only run verified it |
| `failed` | selected, its publish job failed or was cancelled, and its registry does not have it |
| `skipped` | this run did not publish it or check it: its publish job was skipped (the gate refused, CI or the build failed), or a verify-only run did not select it |
| `unknown` | its publish job did not succeed, and its registry did not answer whether it has it |

When a selected package's job succeeded, the job's `verify` step saw the
version on its registry (and `consume` used it). When the job did not
succeed, the `record` job asks the registry itself, the way `verify` does: a
job can fail after uploading, and then the package is `published` but the
`result` is not `complete`.

`url`, per registry:

| Registry | `url` |
|---|---|
| `github` | `https://github.com/<repo>/releases/tag/<tag>` |
| `crates.io` | `https://crates.io/crates/<name>/<version>` |
| `pypi` | `https://pypi.org/project/<name>/<version>/` |
| `npm` | `https://www.npmjs.com/package/<name>/v/<version>` |
| `vscode` | `https://marketplace.visualstudio.com/items?itemName=<publisher.name>` |

`files` comes from the build artifacts (`release--<slug>--*`): every file
but the `.sha256` ones, with its SHA-256 computed from the file and checked
against its `.sha256` where the build wrote one (a `github` package's). A
[`ci-artifact`](configuration.md#ci-artifact) package's files are the
promoted members, unchanged. `files` is empty for crates.io and npm, which
publish from source, and in a verify-only run, which builds nothing. For a
`github` package it does not list the `checksums` file, which the publish job
writes from these same files.

## Checking a release across repositories

A coordinator might require, before starting the next repository's release:

```sh
jq -e '.result == "complete" and .commit == $sha' --arg sha "$expected" release-record.json
jq -r '.packages[] | select(.selected) | "\(.name) \(.version) \(.outcome) \(.url)"' release-record.json
```

and, after a `partial` or `nothing-published` record, fix the cause and
re-run that repository's release with `release.py tag`
([Recovering a failed release](recovery.md)): the next run selects only
what is still due, and its record lists the rest as `already-published`.
