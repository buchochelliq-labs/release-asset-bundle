# npm

`registry: npm` publishes packages from source with `npm publish`. npm
packages take no artifacts.

```yaml
  - name: "@onto/client"
    registry: npm
    manifest: packages/client/package.json
    version: "0.2.0"
```

The `name` must equal the `package.json` name. Quote scoped names: YAML
treats a leading `@` as reserved. Packages must be listed after the listed npm
packages they depend on (`dependencies` and `peerDependencies`). The default
record tag of `@onto/client` 0.2.0 is `@onto/client-v0.2.0`; its slug is
`onto-client`.

## Credentials and setup

npm supports [Trusted Publishing](https://docs.npmjs.com/trusted-publishers)
from GitHub Actions with npm 11.5 or later.

1. Create an `npm` environment in the repository's settings.
2. In each package's settings on npmjs.com, add a trusted publisher: your
   repository, workflow `release.yml`, environment `npm`.

A trusted publisher is set on an existing package. For a package that is not
on npm yet, or if you do not use Trusted Publishing, pass an npm automation
token to the action:

```yaml
      - uses: ./.github/release-kit/publish-npm
        with:
          selection: ${{ needs.gate.outputs.selection }}
          token: ${{ secrets.NPM_TOKEN }}
          verify-only: ${{ needs.gate.outputs.verify-only }}
```

## Publish

The `publish-npm` job installs Node 24 with the npm registry, Python and
PyYAML, and runs `npm ci` at the repository root (not in a verify-only run;
remove or change that step if your root has no `package-lock.json`). The
action then:

1. runs `preflight --registry npm`;
2. if a token was given, writes it to a temporary `.npmrc`;
3. for each selected npm package, in order, runs
   `npm publish --provenance --access public` in its manifest's directory;
4. runs `verify --registry npm`.

### Action inputs

| Input | Default | Meaning |
|---|---|---|
| `selection` | required | the gate's selection |
| `token` | `""` | an npm automation token, when Trusted Publishing is not set up |
| `verify-only` | `"false"` | `"true"` skips the preflight and the upload |

## Verify

`verify --registry npm` waits (up to 15 minutes) until
`https://registry.npmjs.org/<name>/<version>` answers for every selected
package.

## Consume

`consume --registry npm` runs `npm view <name>@<version> version` in a fresh
directory with no token, retried up to 10 times, 30 seconds apart.

