# Chocolatey

A `github` package with `chocolatey` gets a Chocolatey package with each
release: rendered from the package's templates and the published release's
checksums, installed and tested on Windows, then pushed to the community
repository. The package itself installs the release's Windows archive, so the
GitHub release must be out first.

```yaml
  - name: onto
    registry: github
    ...
    chocolatey:
      package: packaging/chocolatey   # required: the package's directory
      test: onto --version            # optional: run after installing (bash)
```

## The package directory

Every file under `package` is copied into the package, keeping its path.
Files ending in `.tmpl` are rendered, and lose the suffix. There must be
exactly one `.nuspec` (or `.nuspec.tmpl`); its `<id>` is the package's name.
The placeholders are [Homebrew's](homebrew.md#the-template). Only `${name}`
is a placeholder, so PowerShell's `$variables` and `${env:X}` are left alone.

```powershell
# packaging/chocolatey/tools/chocolateyinstall.ps1.tmpl
$packageArgs = @{
  packageName    = $env:ChocolateyPackageName
  unzipLocation  = Split-Path -Parent $MyInvocation.MyCommand.Definition
  url64bit       = '${url_x86_64_pc_windows_msvc}'
  checksum64     = '${sha256_x86_64_pc_windows_msvc}'
  checksumType64 = 'sha256'
}
Install-ChocolateyZipPackage @packageArgs
```

## Credentials and setup

A `chocolatey` environment that allows the release tags, with the
`CHOCOLATEY_API_KEY` secret: the API key of the chocolatey.org account that
owns the package. Add required reviewers if each push should wait.

## Publish

The template's `publish-chocolatey` job runs on `windows-latest` after
`publish-github` succeeds, and not in a verify-only run. For each selected
`github` package with `chocolatey`, the `publish-chocolatey` action:

1. downloads the release's `.sha256` assets and renders the package;
2. packs it with `choco pack`;
3. installs it from the packed file, as a user would: the install downloads
   the release's archive and checks its SHA-256;
4. runs `test` in bash, with `VERSION` set, then uninstalls it;
5. pushes it to `https://push.chocolatey.org/`. Input `push: "false"` packs
   and tests only.

Each version then goes through Chocolatey's automated checks, and a new
package through human moderation, before it is listed. While a version is in
moderation, Chocolatey refuses newer ones with `403 Forbidden`: run
`release.py tag` again once the earlier one is approved.

## Finishing it later

Chocolatey is `until-accepted`: a version can be pushed again until the
community repository has it. `release.py` reads the version's state from
`https://community.chocolatey.org/api/v2/Packages(Id='<id>',Version='<version>')`,
with `<id>` from the package's `.nuspec`: `listed` (approved), `submitted` (in
its checks or moderation), `rejected`, or `due` (never pushed). Listed and
submitted count as done, so `tag` does not wait days for moderation; a
rejected or unpushed version is due. Fix the cause and run `release.py tag`:
it pushes the release tag again with only `chocolatey` due for the package,
and `publish-chocolatey` runs while `publish-github` is skipped. See
[the retry policy](../recovery.md#what-each-channel-allows-again-the-retry-policy).
