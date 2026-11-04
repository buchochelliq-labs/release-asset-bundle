# crates.io

`registry: crates.io` publishes crates from source with `cargo publish`.
crates.io packages take no artifacts.

```yaml
  - name: onto
    registry: crates.io
    manifest: crates/onto/Cargo.toml
    version: "0.2.0"
```

The `name` must equal the `Cargo.toml` package name, and crates must be
listed after the listed crates they depend on. `release.py check` also
refuses a crate that crates.io would refuse: see
[Publishing to crates.io](publishing-to-crates-io.md).

## Credentials

crates.io offers [Trusted Publishing](https://crates.io/docs/trusted-publishing),
but only for a crate that already exists. So rab uses two
credentials:

- **Existing crates** use Trusted Publishing.
  [`rust-lang/crates-io-auth-action`](https://github.com/rust-lang/crates-io-auth-action)
  exchanges the job's OIDC token for a short-lived crates.io token.
- **A crate's first version** uses an API token: the `CARGO_REGISTRY_TOKEN`
  secret of the `crates-io` environment.

## One-time setup

1. Create a `crates-io` environment in the repository's settings.
2. For each crate that is already on crates.io, open its settings page on
   crates.io and add a Trusted Publisher: your repository, the workflow
   `release.yml`, and the environment `crates-io`.
3. If a release will publish a crate's **first** version, add a
   `CARGO_REGISTRY_TOKEN` secret to the `crates-io` environment: a crates.io
   API token with the `publish-new` scope (and `publish-update` if new and
   existing crates share it). After the first version is out, add that
   crate's Trusted Publisher. You can then delete the secret.

## Publish

The template's `publish-crates` job:

```yaml
      - id: preflight
        if: needs.gate.outputs.verify-only != 'true'
        env:
          SELECTION: ${{ needs.gate.outputs.selection }}
        run: python3 .github/rab/scripts/release.py preflight --registry crates.io --selection "$SELECTION"
      - id: auth
        if: steps.preflight.outputs.existing_crate == 'true'
        uses: rust-lang/crates-io-auth-action@3268eb413133e2960fa4ae0e963ca76380c54cd9 # v1.0.6
      - uses: ./.github/rab/publish-crates
        with:
          selection: ${{ needs.gate.outputs.selection }}
          new-crates: ${{ steps.preflight.outputs.new_crates || '[]' }}
          token: ${{ steps.auth.outputs.token }}
          new-crate-token: ${{ steps.preflight.outputs.new_crate == 'true' && secrets.CARGO_REGISTRY_TOKEN || '' }}
          verify-only: ${{ needs.gate.outputs.verify-only }}
```

1. **`preflight`** refuses any selected crate version already on crates.io,
   and writes `new_crates` (the selected crates with no version yet),
   `new_crate` and `existing_crate`.
2. **`crates-io-auth-action`** runs only when an existing crate is selected.
3. The secret reaches the action only when a new crate is selected.
4. **`release.py publish-crates`** runs one
   `cargo publish --dry-run --locked` over all the selected crates, then one
   `cargo publish --locked` per consecutive run of existing crates, and one
   per new crate. Order is kept, so each crate is published after the crates
   it depends on. If a needed token is missing, it refuses before uploading
   anything:

    ```text
    error: cannot publish: missing the new-crate token (the environment's CARGO_REGISTRY_TOKEN secret)
    ```

Publishing several crates in one `cargo publish` needs Cargo 1.90 or later;
the job installs stable Rust.

### Many new crates at once

crates.io limits how many new crates an account publishes: a short burst,
then one every few minutes. A first release of a whole workspace hits it.
When crates.io answers a new crate with `429 Too Many Requests`,
`publish-crates` waits until the time the answer names (ten minutes if it
names none) and tries that crate again, as often as it takes. Any other
failure stops at once. Eighteen new crates take about two hours, inside a
job's six-hour limit; crates.io can raise an account's limit on request.

### Example: a first release

`onto` (a library) is new; `onto-cli`, which depends on it, already exists.
`preflight` writes `new_crates=["onto"]`, `new_crate=true`,
`existing_crate=true`. `publish-crates` dry-runs both, publishes `onto` with
`CARGO_REGISTRY_TOKEN`, then `onto-cli` with the Trusted Publishing token.

### Action inputs

| Input | Default | Meaning |
|---|---|---|
| `selection` | required | the gate's selection |
| `new-crates` | `"[]"` | `preflight`'s `new_crates` output |
| `token` | `""` | the Trusted Publishing token; needed when any selected crate exists |
| `new-crate-token` | `""` | a crates.io API token; needed only when a selected crate is new |
| `verify-only` | `"false"` | `"true"` skips the upload |

## Verify

`verify --registry crates.io` waits (up to 15 minutes) until
`https://crates.io/api/v1/crates/<name>/<version>` answers for every selected
crate.

## Consume

`consume --registry crates.io` then, for each crate, in a fresh directory with
no token:

- a crate with a binary (a `[[bin]]` table, or `src/main.rs` beside its
  `Cargo.toml`): `cargo install <name> --version =<version> --locked`;
- a library: a new crate, `cargo add <name>@=<version>`, then `cargo check`.

The install or add step is retried up to 10 times, 30 seconds apart, because
the crates.io index can lag behind its API.
