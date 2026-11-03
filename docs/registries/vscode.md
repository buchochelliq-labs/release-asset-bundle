# VS Code Marketplace and Open VSX

`registry: vscode` publishes a packaged `.vsix` to the Visual Studio
Marketplace, and to [Open VSX](https://open-vsx.org/) when a token is given.

```yaml
  - name: onto.onto-vscode           # <publisher>.<name> from package.json
    registry: vscode
    manifest: editors/vscode/package.json
    version: "0.2.0"
    artifacts:
      - kind: vsix                     # writes onto-vscode-0.2.0.vsix
```

The `name` is `<publisher>.<name>` from `package.json`. A `vscode` package
needs a `vsix` or `command` artifact. A `command` artifact must produce a file
named `<name>-<version>.vsix` (the part after `<publisher>.`), because that
is the file the action publishes.

## Credentials and setup

The Marketplace and Open VSX use personal access tokens.

1. Create the publisher on the Visual Studio Marketplace, and on Open VSX if
   you publish there.
2. Create a `vscode-marketplace` environment in the repository's settings.
3. Add the secret `VSCE_PAT`: an Azure DevOps personal access token with the
   Marketplace *Manage* scope.
4. Optionally add `OVSX_PAT`: an Open VSX access token. Without it, Open VSX
   is skipped.

## Publish

The `publish-vscode` job downloads the build artifacts into `dist/` and
passes both secrets. The action:

1. runs `preflight --registry vscode`;
2. for each selected extension, finds exactly one
   `dist/release--<slug>--*/<name>-<version>.vsix`, and fails otherwise;
3. runs `npx @vscode/vsce publish --packagePath <file>` (with `VSCE_PAT`);
4. if `OVSX_PAT` is set, runs `npx ovsx publish <file>`;
5. runs `verify --registry vscode`.

### Action inputs

| Input | Default | Meaning |
|---|---|---|
| `selection` | required | the gate's selection |
| `dir` | `dist` | where the build artifacts were downloaded (not merged) |
| `vsce-token` | required | a Visual Studio Marketplace personal access token |
| `ovsx-token` | `""` | an Open VSX token; empty skips Open VSX |
| `verify-only` | `"false"` | `"true"` skips the preflight and upload |

## Verify

`verify --registry vscode` waits (up to 15 minutes) until the Marketplace's
extension query lists the version. Open VSX is not checked: if a release
reaches the Marketplace but the Open VSX upload fails, release-kit counts the
extension as published, and you publish the `.vsix` to Open VSX by hand.

## Consume

Nothing: `consume` skips `vscode` packages.
