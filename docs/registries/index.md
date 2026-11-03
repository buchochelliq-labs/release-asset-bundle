# Registries

A package's `registry` field says where it is published. release-kit supports
five:

| `registry` | Where | Publishes | Credential | Environment |
|---|---|---|---|---|
| `github` | [GitHub Releases](github.md) | built artifacts | the job's `github.token` | none |
| `crates.io` | [crates.io](crates-io.md) | from source | Trusted Publishing; a token for first versions | `crates-io` |
| `pypi` | [PyPI](pypi.md) | built artifacts | Trusted Publishing | `pypi` |
| `npm` | [npm](npm.md) | from source | Trusted Publishing, or a token | `npm` |
| `vscode` | [VS Code Marketplace](vscode.md), and Open VSX | built artifacts | personal access tokens | `vscode-marketplace` |

Every publish action follows the same steps:

1. **preflight**: refuse any selected version already on the registry
   (rule 1), in case another run published it since the gate;
2. **upload**, in `release-kit.yml` order;
3. **verify**: wait until the registry serves every selected version;
4. **consume**, for crates.io and PyPI: use each version as a stranger
   would. (npm's action currently consumes only in verify-only runs; see
   [npm](npm.md).)

Each action takes a `verify-only` input (default `"false"`). With `"true"`
it skips the preflight and the upload, and only verifies (and consumes). The
template passes the gate's `verify-only` output.

*Trusted Publishing* means the registry trusts a GitHub Actions OIDC token
that names your repository, workflow file and environment, instead of a
long-lived secret. The trusted publisher you configure on the registry must
name `release.yml` and the environment in the table above.
