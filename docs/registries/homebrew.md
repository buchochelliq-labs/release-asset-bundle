# Homebrew

A `github` package with `homebrew` gets a formula with each release: rendered
from the package's template and the published release's checksums, attached
to the release, and proposed to a tap as a pull request. Merging the pull
request is what publishes it to `brew upgrade`.

```yaml
  - name: onto
    registry: github
    manifest: crates/onto-cli/Cargo.toml
    version: "0.2.0"
    tag: "v{version}"
    attest: true                  # also attests the formula
    homebrew:
      tap: org/homebrew-tap       # required: the tap repository
      formula: packaging/onto.rb.tmpl   # required: the template
      name: onto                  # default: the template's name without .tmpl and .rb
    artifacts:
      - kind: rust-binary
        bin: onto
        targets: [aarch64-apple-darwin, x86_64-apple-darwin, x86_64-unknown-linux-musl, aarch64-unknown-linux-musl]
```

## The template

`${name}` placeholders are filled; Ruby's own `#{...}` is left alone. A
placeholder without a value fails the job rather than being left in.

| Placeholder | Value |
|---|---|
| `${version}` | the package's version |
| `${tag}` | its record tag, which names the release |
| `${repository}` | `owner/name` of this repository |
| `${base_url}` | `https://github.com/<repository>/releases/download/<tag>` |
| `${url_<target>}`, `${sha256_<target>}` | for each `rust-binary` archive on the release (`<bin>-v<version>-<target>.tar.gz` or `.zip`), its download URL and SHA-256, with the target's `-` written `_`: `${sha256_aarch64_apple_darwin}` |

```ruby
class Onto < Formula
  desc "..."
  version "${version}"
  on_macos do
    on_arm do
      url "${url_aarch64_apple_darwin}"
      sha256 "${sha256_aarch64_apple_darwin}"
    end
  end
  def install
    bin.install "onto"
  end
end
```

`release.py render --name onto --channel homebrew --sums SHA256SUMS --out dir`
renders it locally from any `sha256sum` output, to check a template.

## Credentials and setup

- A fine-grained personal access token with access to the tap only, and
  **Contents** and **Pull requests** set to read and write, stored as the
  `HOMEBREW_TAP_TOKEN` secret of a `homebrew` environment that allows the
  release tags.
- Without the secret, the formula is still attached to the release, and a
  notice says the tap was not updated: copy it to the tap's `Formula/` by hand.
- The job uses the job's `github.token` to attach the formula
  (`contents: write`) and, with `attest: true`, `id-token: write` and
  `attestations: write`. The template grants them.

## Publish

The template's `publish-homebrew` job runs after `publish-github` succeeds,
and not in a verify-only run. For each selected `github` package with
`homebrew`, the `publish-homebrew` action:

1. leaves out a prerelease (a version with `-`);
2. downloads the release's `.sha256` assets and renders the formula
   (`ruby -c` checks it when Ruby is installed);
3. with `attest: true`, attests it;
4. attaches it to the release (`--clobber`);
5. with a tap token, clones the tap, puts the formula in `Formula/` on the
   branch `<name>-<version>`, pushes it, and opens a pull request
   (`release.py homebrew-tap`). A tap that already has the formula is left
   alone; running again replaces the branch and reuses the open pull request.

A failed job is re-run on its own (**Re-run failed jobs**): `release.py tag`
does not select a package whose GitHub release is already published.
