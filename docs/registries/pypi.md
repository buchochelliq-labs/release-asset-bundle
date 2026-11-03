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
3. uploads them with
   [`pypa/gh-action-pypi-publish`](https://github.com/pypa/gh-action-pypi-publish),
   which uses the job's OIDC token (`id-token: write`).

### Action inputs

| Input | Default | Meaning |
|---|---|---|
| `selection` | required | the gate's selection |
| `dir` | `dist` | where the build artifacts were downloaded (not merged) |
| `verify-only` | `"false"` | `"true"` skips the preflight and upload |

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
