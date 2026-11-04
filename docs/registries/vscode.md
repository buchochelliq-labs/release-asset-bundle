# VS Code Marketplace and Open VSX

`registry: vscode` publishes a packaged `.vsix` to the Visual Studio
Marketplace, and to [Open VSX](https://open-vsx.org/) when the package says
`open-vsx: true`.

```yaml
  - name: onto.onto-vscode           # <publisher>.<name> from package.json
    registry: vscode
    manifest: editors/vscode/package.json
    version: "0.2.0"
    open-vsx: true                     # also publish to Open VSX (needs OVSX_PAT)
    artifacts:
      - kind: vsix                     # writes onto-vscode-0.2.0.vsix
```

The `name` is `<publisher>.<name>` from `package.json`. A `vscode` package
needs a `vsix` or `command` artifact. A `command` artifact must produce a file
named `<name>-<version>.vsix` (the part after `<publisher>.`), because that
is the file the action publishes.

With `open-vsx: true`, Open VSX is part of the package's publication: the
version is due until **both** registries have it, so `status`, `tag` and the
gate select it while either lacks it, and `verify` waits for both. Open VSX
is declared in `release-kit.yml`, not inferred from the `OVSX_PAT` secret,
because the gate and `status` run without the publish job's secrets and must
still know what "published" means.

## Credentials and setup

The Marketplace and Open VSX use personal access tokens.

1. Create the publisher on the Visual Studio Marketplace, and on Open VSX if
   you publish there.
2. Create a `vscode-marketplace` environment in the repository's settings.
3. Add the secret `VSCE_PAT`: an Azure DevOps personal access token with the
   Marketplace *Manage* scope.
4. For a package with `open-vsx: true`, add `OVSX_PAT`: an Open VSX access
   token. The publish step refuses, before any upload, an extension that
   Open VSX still lacks when no token is given.

## Publish

The `publish-vscode` job downloads the build artifacts into `dist/` and
passes both secrets. The action:

1. runs `preflight --registry vscode`: it refuses an extension both registries
   already have, and says which registry each extension still lacks (a
   registry that has the version is skipped, so a re-run uploads only the
   missing one);
2. for each selected extension, finds exactly one
   `dist/release--<slug>--*/<name>-<version>.vsix`, and fails otherwise;
3. if the Marketplace lacks it, runs `npx @vscode/vsce publish --packagePath
   <file>` (with `VSCE_PAT`);
4. with `open-vsx: true`, if Open VSX lacks it, runs `npx ovsx publish <file>`
   (with `OVSX_PAT`);
5. runs `verify --registry vscode`.

A package without `open-vsx: true` is still uploaded to Open VSX when
`OVSX_PAT` is set, as before, with a warning: that upload is not checked, and
a re-run does not finish it. Add `open-vsx: true` to have it checked.

### Action inputs

| Input | Default | Meaning |
|---|---|---|
| `selection` | required | the gate's selection |
| `dir` | `dist` | where the build artifacts were downloaded (not merged) |
| `vsce-token` | required | a Visual Studio Marketplace personal access token |
| `ovsx-token` | `""` | an Open VSX token; needed when an `open-vsx: true` extension is missing from Open VSX |
| `verify-only` | `"false"` | `"true"` skips the preflight and upload |

## Verify

`verify --registry vscode` waits (up to 15 minutes) until the Marketplace's
extension query lists the version and, with `open-vsx: true`, until
`https://open-vsx.org/api/<publisher>/<name>/<version>` answers 200. If an
Open VSX upload fails after the Marketplace's succeeded, the extension stays
due: run `release.py tag` again and the re-run uploads it to Open VSX only.

## Consume

Nothing: `consume` skips `vscode` packages.
