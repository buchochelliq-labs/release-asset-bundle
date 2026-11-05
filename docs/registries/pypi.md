# PyPI

`registry: pypi` uploads built wheels and sdists to PyPI.

```yaml
  - name: rs-rich
    registry: pypi
    manifest: crates/rich-py/pyproject.toml
    version: "0.0.10"
    tag: "python-v{version}"
    artifacts:
      - kind: maturin
        targets:
          - x86_64-unknown-linux-gnu
          - aarch64-unknown-linux-gnu
          - aarch64-apple-darwin
          - x86_64-apple-darwin
          - x86_64-pc-windows-msvc
```

The `name` must equal the `pyproject.toml` name (`[project]` or
`[tool.poetry]`). Versions follow PEP 440. A PyPI package needs at least one
artifact: `python`, `maturin` or `command` (whose `outputs` should be wheels
or sdists).

If a crate in the same repository has the same name, give the PyPI package a
tag format of its own, as above. See
[collisions](../configuration.md#record-tags-and-collisions).

## Credentials and setup

PyPI uses [Trusted Publishing](https://docs.pypi.org/trusted-publishers/).
No secret is stored.

1. Create a `pypi` environment in the repository's settings.
2. On PyPI, add a trusted publisher for the project: owner and repository,
   workflow `release.yml`, environment `pypi`. For a project that does not
   exist yet, add a *pending* publisher from your PyPI account's publishing
   page; the first upload creates the project.

## Publish

The `publish-pypi` job downloads the build artifacts into `dist/`. The action:

1. runs `preflight --registry pypi`;
2. copies every `.whl` and `.tar.gz` from `dist/release--<slug>--*/` of each
   selected PyPI package into one directory, and fails if there are none;
3. creates a virtualenv and installs twine and pypi-attestations into it from
   the action's `requirements.txt`, with `pip install --require-hashes`
   (every package pinned to an exact version and its hashes);
4. runs [`pypi-upload`](../commands.md#pypi-upload) with that virtualenv's
   Python, which:
    - runs `twine check` on every file;
    - exchanges the job's OIDC token for a short-lived PyPI API token, as
      [PyPI documents](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
      for other CI systems: the audience from `https://pypi.org/_/oidc/audience`,
      GitHub's OIDC token for it (this is what needs `id-token: write`), and
      `https://pypi.org/_/oidc/mint-token`. The token is masked in the log;
    - signs a [PEP 740](https://peps.python.org/pep-0740/) attestation for
      each file with `pypi-attestations sign`, using the job's identity
      through Sigstore, so PyPI shows the files' provenance;
    - prints each file's sha256, md5 and blake2_256;
    - runs `twine upload --verbose --attestations` with the token.

The trusted publisher on PyPI is the same as before: it names the repository,
`release.yml` and the `pypi` environment, which is what the job's OIDC token
says.

When PyPI refuses the exchange, the job fails with PyPI's reasons and the
token's claims, for example:

```text
error: https://pypi.org refused the Trusted Publishing exchange (422):
  invalid-publisher: valid token, but no corresponding publisher (...)
The publisher on PyPI must match this job's token (owner, repository, workflow file, environment):
  sub: repo:owner/repo:environment:pypi
  repository: owner/repo
  ...
  workflow_ref: owner/repo/.github/workflows/release.yml@refs/tags/release-1.2.0
  environment: pypi
```

Compare them with the publisher's owner, repository, workflow file name and
environment. A job without `id-token: write` fails before asking anyone:
`GitHub offers this job no OIDC token (ACTIONS_ID_TOKEN_REQUEST_URL is not
set)`.

### Why not `pypa/gh-action-pypi-publish`

rab 2.0.0 used it, and its first PyPI release failed without uploading
anything: the action runs a Docker image that it names from the calling
action's repository and ref (`github.action_repository`, `github.action_ref`).
Inside a composite action, as `publish-pypi` is, those are not set, so it fell
back to the release's own repository and tag and tried to pull
`ghcr.io/<owner>/<repo>:<tag>`, which does not exist (`denied`). pypa
documents that the action does not work from a composite action; it works
when a workflow job uses it directly. rab 2.0.1 does the same steps itself.
A test keeps every rab composite free of Docker-based actions.

### Action inputs

| Input | Default | Meaning |
|---|---|---|
| `selection` | required | the gate's selection |
| `dir` | `dist` | where the build artifacts were downloaded (not merged) |
| `verify-only` | `"false"` | `"true"` skips the preflight and upload |
| `attestations` | `"true"` | `"false"` uploads without PEP 740 attestations (for when Sigstore is down) |

## Verify

`verify --registry pypi` waits (up to 15 minutes) until
`https://pypi.org/pypi/<name>/<version>/json` answers for every selected
package.

## Consume

`consume --registry pypi` creates a new virtualenv for each package and runs
`pip install --quiet --no-cache-dir <name>==<version>` in it, with no token.
The install is retried up to 10 times, 30 seconds apart. It runs on the
`ubuntu-latest` publish job, so a package must have a wheel or sdist that
installs there.
