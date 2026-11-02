"""The buchochelliq-labs release driver: RELEASES.toml, checked, tagged and gated.

RELEASES.toml lists every package a repository publishes, on which registry,
from which manifest, at which version, in publication order. A release is cut
from one commit of the default branch and named by one package's version (the
last, unless `[release] name_from` says otherwise). Generalised from
rs-rich-cli's release_cohort.py; the rules are the same:

  - A published version is permanent: nothing already on its registry is
    tagged, selected or uploaded again.
  - Every tag is annotated and records its release and commit, and points at a
    commit on the default branch.
  - A release is one commit: a package already tagged on another commit that
    ships other files under the same version is refused (a "split" release).
  - A failed release is finished by running `tag` again: a run that provably
    published nothing has its tags moved or pushed again; a run that published
    part of the release is pushed again and publishes only what is still due.

Commands (person):
    check       RELEASES.toml matches every manifest, names no package twice,
                and lists each package after the listed packages it depends on.
    sync        rewrite RELEASES.toml's versions from the manifests.
    status      each package: version, tag, and whether it is published.
    tag         tag the release and wait for it: an annotated record tag per
                package due (which starts nothing), then the release tag,
                which starts the one release run. --commit, --release, --dry-run.

Commands (the gate action, in CI):
    gate TAG    the tag is annotated, on the default branch, records its
                commit, does not split its release, and something is due.
                Writes sha, release, selection and per-registry flags to
                GITHUB_OUTPUT.
    preflight --registry R --selection JSON
                refuse, before uploading, any selected package of R already on R.
    verify --registry R --selection JSON
                wait until every selected package of R is on R.

Registries: github (GitHub Releases), crates.io, pypi, npm, vscode (Visual
Studio Marketplace). Requires Python 3.11+ and git; `tag` follows runs with
the GitHub CLI (`gh`) when it is installed.
"""

import sys

if sys.version_info < (3, 11):
    sys.exit(f"release.py needs Python 3.11 or newer (for tomllib); this is {sys.version.split()[0]}")

import argparse
from dataclasses import dataclass
import fnmatch
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import time
import tomllib
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

CONFIG = "RELEASES.toml"
REGISTRIES = ("github", "crates.io", "pypi", "npm", "vscode")
NUMBER = r"(?:0|[1-9][0-9]*)"
PRERELEASE = rf"(?:{NUMBER}|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
SEMVER = rf"{NUMBER}\.{NUMBER}\.{NUMBER}(?:-{PRERELEASE}(?:\.{PRERELEASE})*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
# PyPI versions follow PEP 440 (1.2.0rc1, 1.2.0.post1), not SemVer.
PEP440 = r"[0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?"
USER_AGENT = "buchochelliq-labs-release-kit (https://github.com/buchochelliq-labs/release-kit)"


def root():
    return Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())


def git(*args, capture=True, check=True):
    result = subprocess.run(["git", *args], cwd=root(), text=True, encoding="utf-8",
                            capture_output=capture, check=check)
    return result.stdout.strip() if capture else ""


def files_on_disk(base=None):
    base = base or root()
    return lambda path: (base / path).read_text(encoding="utf-8")


def files_at(commit):
    return lambda path: git("show", f"{commit}:{path}")


# ---------------------------------------------------------------------------
# RELEASES.toml


@dataclass(frozen=True)
class Package:
    name: str
    registry: str
    manifest: str
    version: str
    tag_format: str = "{name}-v{version}"
    paths: tuple = ()

    @property
    def tag(self):
        return self.tag_format.format(name=self.name, version=self.version)

    @property
    def shipped(self):
        """The files whose change means a different package: `paths`, else
        the manifest's directory."""
        if self.paths:
            return self.paths
        parent = str(PurePosixPath(self.manifest).parent)
        return ("**",) if parent == "." else (f"{parent}/**",)

    def __str__(self):
        return f"{self.name} {self.version} ({self.registry})"


@dataclass(frozen=True)
class Release:
    packages: tuple
    name_from: str
    tag_format: str
    branch: str

    @property
    def name(self):
        return next(p.version for p in self.packages if p.name == self.name_from)

    @property
    def tag(self):
        return self.tag_format.format(release=self.name)


def load(read):
    data = tomllib.loads(read(CONFIG))
    entries = data.get("package", [])
    if not entries:
        raise ValueError(f"{CONFIG} lists no [[package]]")
    packages = []
    for entry in entries:
        missing = {"name", "registry", "manifest", "version"} - entry.keys()
        if missing:
            raise ValueError(f"{CONFIG}: a package lacks {', '.join(sorted(missing))}")
        packages.append(Package(entry["name"], entry["registry"], entry["manifest"], entry["version"],
                                entry.get("tag", "{name}-v{version}"), tuple(entry.get("paths", ()))))
    settings = data.get("release", {})
    name_from = settings.get("name_from", packages[-1].name)
    if name_from not in {p.name for p in packages}:
        raise ValueError(f"{CONFIG}: [release] name_from = {name_from!r} is not a listed package")
    return Release(tuple(packages), name_from, settings.get("tag", "release-{release}"),
                   settings.get("branch", "main"))


def version_key(version):
    """Numeric parts first, so 0.0.10 sorts after 0.0.9."""
    return tuple(int(part) for part in re.findall(r"\d+", version.split("+")[0])[:3])


# Manifests: how each registry's manifest names its package and version.

def _cargo(read, path):
    data = tomllib.loads(read(path))
    package = data.get("package", {})
    version = package.get("version")
    if isinstance(version, dict) and version.get("workspace"):
        workspace = tomllib.loads(read("Cargo.toml"))
        version = workspace["workspace"]["package"]["version"]
    deps = set()
    tables = [data.get("dependencies", {}), data.get("build-dependencies", {})]
    tables += [t.get("dependencies", {}) for t in data.get("target", {}).values()]
    for table in tables:
        for key, spec in table.items():
            deps.add(spec.get("package", key) if isinstance(spec, dict) else key)
    return package.get("name"), version, deps


def _pyproject(read, path):
    data = tomllib.loads(read(path))
    project = data.get("project") or data.get("tool", {}).get("poetry", {})
    deps = {re.split(r"[\s<>=!~;\[(]", d, maxsplit=1)[0] for d in project.get("dependencies", [])
            if isinstance(d, str)}
    return project.get("name"), project.get("version"), deps


def _package_json(read, path, vscode=False):
    data = json.loads(read(path))
    name = f"{data.get('publisher')}.{data.get('name')}" if vscode else data.get("name")
    deps = set(data.get("dependencies", {})) | set(data.get("peerDependencies", {}))
    return name, data.get("version"), deps


def manifest_info(read, package):
    """(name, version, dependency names) from the package's manifest."""
    file = PurePosixPath(package.manifest).name
    if file == "Cargo.toml":
        return _cargo(read, package.manifest)
    if file == "pyproject.toml":
        return _pyproject(read, package.manifest)
    if file == "package.json":
        return _package_json(read, package.manifest, vscode=package.registry == "vscode")
    raise ValueError(f"{package.name}: unsupported manifest {package.manifest} "
                     "(use Cargo.toml, pyproject.toml or package.json)")


def check(read):
    """Every way RELEASES.toml can disagree with the repository, as messages."""
    try:
        release = load(read)
    except (ValueError, tomllib.TOMLDecodeError, KeyError) as error:
        return [str(error)]
    errors, seen, tags, deps = [], set(), {}, {}
    for package in release.packages:
        if package.registry not in REGISTRIES:
            errors.append(f"{package.name}: unknown registry {package.registry!r} (use {', '.join(REGISTRIES)})")
            continue
        if (package.registry, package.name) in seen:
            errors.append(f"{package}: listed twice")
        seen.add((package.registry, package.name))
        grammar = PEP440 if package.registry == "pypi" else SEMVER
        if not re.fullmatch(grammar, package.version):
            errors.append(f"{package.name}: {package.version!r} is not a version")
        if package.tag in tags:
            errors.append(f"{package.name} and {tags[package.tag]} have the same tag {package.tag}")
        tags[package.tag] = package.name
        if package.tag == release.tag:
            errors.append(f"{package.name}'s tag {package.tag} is the release tag; record tags must differ")
        try:
            name, version, deps[package.name] = manifest_info(read, package)
        except (subprocess.CalledProcessError, FileNotFoundError, KeyError, ValueError,
                tomllib.TOMLDecodeError, json.JSONDecodeError) as error:
            errors.append(f"{package.name}: cannot read {package.manifest} ({error})")
            continue
        if version != package.version:
            errors.append(f"{package.name}: {CONFIG} says {package.version}, {package.manifest} says {version} "
                          "(run release.py sync)")
        if package.registry != "github" and name != package.name:
            errors.append(f"{package.name}: {package.manifest} names the package {name!r}")
    # Each package after the listed packages of its own registry it depends on.
    position = {(p.registry, p.name): i for i, p in enumerate(release.packages)}
    for package in release.packages:
        for dependency in sorted(deps.get(package.name, ())):
            index = position.get((package.registry, dependency))
            if index is not None and index > position[(package.registry, package.name)]:
                errors.append(f"{package.name} is listed before {dependency}, which it depends on")
    return errors


def sync(read=None, path=None):
    """Rewrite each version in RELEASES.toml from its manifest, keeping the
    file's comments and layout. Returns the changes made."""
    path = path or root() / CONFIG
    read = read or files_on_disk(path.parent)
    text = path.read_text(encoding="utf-8")
    changes = []
    blocks = re.split(r"(?m)^(?=\[\[package\]\])", text)
    for i, block in enumerate(blocks):
        manifest = re.search(r'(?m)^manifest\s*=\s*"([^"]+)"', block)
        current = re.search(r'(?m)^version\s*=\s*"([^"]+)"', block)
        registry = re.search(r'(?m)^registry\s*=\s*"([^"]+)"', block)
        name = re.search(r'(?m)^name\s*=\s*"([^"]+)"', block)
        if not (manifest and current and registry and name):
            continue
        package = Package(name[1], registry[1], manifest[1], current[1])
        version = manifest_info(read, package)[1]
        if version != current[1]:
            blocks[i] = block[:current.start(1)] + version + block[current.end(1):]
            changes.append(f"{package.name}: {current[1]} -> {version}")
    path.write_text("".join(blocks), encoding="utf-8")
    return changes


# ---------------------------------------------------------------------------
# Registries


def http_status(url, data=None, headers=None):
    request = Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
    for attempt in range(4):
        try:
            with urlopen(request, timeout=30) as response:
                return response.status, response.read()
        except HTTPError as error:
            if error.code == 404:
                return 404, b""
            if error.code < 500 and error.code != 429:
                raise
        except URLError:
            pass
        time.sleep(2 ** attempt)
    raise RuntimeError(f"{url}: the registry did not answer; try again")


def repository():
    """owner/name, from GITHUB_REPOSITORY in CI, else the origin remote."""
    if slug := os.environ.get("GITHUB_REPOSITORY"):
        return slug
    url = git("remote", "get-url", "origin")
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    if not match:
        raise RuntimeError(f"cannot tell the GitHub repository from {url}")
    return match[1]


def published_on(package):
    """Whether `package`'s exact version is on its registry."""
    name, version = package.name, package.version
    if package.registry == "crates.io":
        return http_status(f"https://crates.io/api/v1/crates/{quote(name)}/{quote(version)}")[0] == 200
    if package.registry == "pypi":
        return http_status(f"https://pypi.org/pypi/{quote(name)}/{quote(version)}/json")[0] == 200
    if package.registry == "npm":
        return http_status(f"https://registry.npmjs.org/{quote(name, safe='@')}/{quote(version)}")[0] == 200
    if package.registry == "vscode":
        query = {"filters": [{"criteria": [{"filterType": 7, "value": name}]}], "flags": 0x1}
        status, body = http_status(
            "https://marketplace.visualstudio.com/_apis/public/gallery/extensionquery",
            data=json.dumps(query).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json;api-version=7.2-preview.1"})
        results = json.loads(body or b"{}").get("results", [{}])[0].get("extensions", [])
        return any(v.get("version") == version for e in results for v in e.get("versions", []))
    if package.registry == "github":
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        headers = {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})}
        status, body = http_status(f"https://api.github.com/repos/{repository()}/releases/tags/{quote(package.tag)}",
                                   headers=headers)
        return status == 200 and bool(json.loads(body).get("assets"))
    raise ValueError(f"unknown registry {package.registry!r}")


# ---------------------------------------------------------------------------
# Tags and runs


def remote_tags(remote):
    """{tag: commit} on the remote, peeled to commits."""
    tags = {}
    for line in git("ls-remote", "--tags", remote).splitlines():
        sha, ref = line.split("\t")
        name = ref.removeprefix("refs/tags/")
        if name.endswith("^{}"):
            tags[name[:-3]] = sha
        else:
            tags.setdefault(name, sha)
    return tags


def lightweight(remote, names):
    """The tags among `names` that are lightweight on the remote (no ^{} line)."""
    peeled = {line.split("\t")[1].removeprefix("refs/tags/")[:-3]
              for line in git("ls-remote", "--tags", remote).splitlines() if line.endswith("^{}")}
    return {n for n in names if n not in peeled}


def have_commits(existing, remote):
    """Fetch tagged commits the local clone lacks, so their files can be read."""
    missing = [c for c in set(existing.values())
               if subprocess.run(["git", "cat-file", "-e", f"{c}^{{commit}}"], cwd=root(),
                                 capture_output=True).returncode]
    if missing:
        git("fetch", "--quiet", remote, *missing)


def run_of(tag_name):
    """The latest release run for a tag: status, conclusion, url, head_sha and
    whether a publish job started. None without `gh` or without a run."""
    if not shutil.which("gh"):
        return None
    result = subprocess.run(
        ["gh", "run", "list", "--branch", tag_name, "--limit", "1",
         "--json", "status,conclusion,url,headSha,databaseId",
         "--jq", '.[0] | "\\(.status) \\(.conclusion) \\(.url) \\(.headSha) \\(.databaseId)"'],
        cwd=root(), text=True, capture_output=True)
    fields = result.stdout.split()
    if result.returncode or len(fields) != 5:
        return None
    status, conclusion, url, head_sha, run_id = fields
    jobs = subprocess.run(
        ["gh", "api", f"repos/{{owner}}/{{repo}}/actions/runs/{run_id}/jobs",
         "--jq", '[.jobs[] | select((.name | startswith("publish")) and .status != "queued"'
                 ' and .conclusion != "skipped")] | length'],
        cwd=root(), text=True, capture_output=True)
    # Unknown counts as started: a publish job may have uploaded.
    started = jobs.returncode != 0 or jobs.stdout.strip() != "0"
    return {"status": status, "conclusion": conclusion, "url": url, "head_sha": head_sha,
            "publish_started": started}


def dead(run, tagged):
    """Whether a run proves nothing was published from `tagged`: it ran there,
    ended without success, and no publish job started."""
    return bool(run and run["head_sha"] == tagged and run["status"] == "completed"
                and run["conclusion"] != "success" and not run["publish_started"])


def changed_files(package, old, new):
    names = git("diff", "--name-only", old, new).splitlines()
    return [n for n in names if any(fnmatch.fnmatch(n, pattern) for pattern in package.shipped)]


def split_from(release, existing, sha, binds):
    """Packages tagged on another commit whose shipped files differ at `sha`:
    releasing `sha` would put other code under their numbers. Returns
    {commit: [description]}."""
    found = {}
    for package in release.packages:
        tagged = existing.get(package.tag)
        if not tagged or tagged == sha or not binds(package, tagged):
            continue
        if changed := changed_files(package, tagged, sha):
            shown = ", ".join(changed[:3]) + (", ..." if len(changed) > 3 else "")
            found.setdefault(tagged, []).append(f"{package} ({shown})")
    return found


def split_error(sha, split):
    lines = [f"  tagged at {commit[:12]}: " + "; ".join(names) for commit, names in split.items()]
    hint = " or ".join(f"--commit {commit[:12]}" for commit in split)
    return (f"{sha[:12]} changes packages already tagged under the same version on other commits:\n"
            + "\n".join(lines) + f"\nRelease from that commit ({hint}), or bump those packages first.")


def tag_message(subject, release, sha):
    return f"{subject}\n\nRelease: {release.name}\nCommit: {sha}\n"


def trailers(tag_name):
    text = git("for-each-ref", f"refs/tags/{tag_name}", "--format=%(contents)")
    return dict(re.findall(r"(?m)^(Release|Commit): (\S+)$", text))


# ---------------------------------------------------------------------------
# The gate (CI)


def gate(tag_name, remote="origin", status=published_on, output=None):
    """Everything a release run must hold before building, from the checked-out
    tag. Returns the outputs written to GITHUB_OUTPUT."""
    ref = f"refs/tags/{tag_name}"
    if git("cat-file", "-t", ref) != "tag":
        raise RuntimeError(f"{tag_name} must be an annotated tag (release.py tag makes them)")
    sha = git("rev-parse", "--verify", f"{ref}^{{commit}}")
    read = files_at(sha)
    if errors := check(read):
        raise RuntimeError(f"{CONFIG} at {sha[:12]} disagrees with the repository:\n  " + "\n  ".join(errors))
    release = load(read)
    if tag_name != release.tag:
        raise RuntimeError(f"{tag_name} is not this commit's release tag ({release.tag}); "
                           "a release run starts only from its release tag")
    git("fetch", "--quiet", remote, release.branch)
    if subprocess.run(["git", "merge-base", "--is-ancestor", sha, f"{remote}/{release.branch}"],
                      cwd=root()).returncode:
        raise RuntimeError(f"{tag_name} points at {sha[:12]}, which is not on {release.branch}: "
                           "release tags go on the default branch after the release PR merges")
    recorded = trailers(tag_name)
    if recorded.get("Commit") != sha or recorded.get("Release") != release.name:
        raise RuntimeError(f"{tag_name} must record `Release: {release.name}` and `Commit: {sha}` "
                           "in its message (release.py tag writes them)")
    existing = remote_tags(remote)
    have_commits(existing, remote)
    if split := split_from(release, existing, sha, lambda package, tagged: True):
        raise RuntimeError(split_error(sha, split))
    due = []
    for package in release.packages:
        if status(package):
            print(f"{package}: already published; not selected", flush=True)
        else:
            print(f"{package}: not yet published; selected", flush=True)
            due.append(package)
    if not due:
        raise RuntimeError(f"{tag_name}: every package of release {release.name} is already published")
    untagged = [f"{p.tag} ({'missing' if p.tag not in existing else 'at ' + existing[p.tag][:12]})"
                for p in due if existing.get(p.tag) != sha]
    if untagged:
        raise RuntimeError(f"each selected package's tag must be on {sha[:12]}: {', '.join(untagged)}. "
                           "release.py tag pushes them.")
    if plain := lightweight(remote, [p.tag for p in due]):
        raise RuntimeError(f"record tags must be annotated: {', '.join(sorted(plain))}")
    outputs = {
        "sha": sha,
        "release": release.name,
        "selection": json.dumps([{"name": p.name, "registry": p.registry, "version": p.version,
                                  "manifest": p.manifest, "tag": p.tag} for p in due]),
    }
    for registry in REGISTRIES:
        outputs[registry.replace(".", "-")] = str(any(p.registry == registry for p in due)).lower()
    target = output or os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            for key, value in outputs.items():
                handle.write(f"{key}={value}\n")
    print(f"{tag_name} -> {sha} (release {release.name}): publishing "
          + ", ".join(str(p) for p in due), flush=True)
    return outputs


def selected(registry, selection):
    """The packages of `registry` in a gate's selection (JSON), in order. The
    selection carries each package's rendered tag, which serves as its format."""
    return [Package(s["name"], s["registry"], s["manifest"], s["version"], s.get("tag", "{name}-v{version}"))
            for s in json.loads(selection) if s["registry"] == registry]


def preflight(registry, selection, status=published_on):
    """Refuse, just before uploading, any selected version already published:
    another run may have published it since the gate."""
    chosen = selected(registry, selection)
    if out := [str(p) for p in chosen if status(p)]:
        raise RuntimeError(f"already on {registry}: {', '.join(out)}; a published version is permanent")
    print(f"to publish on {registry}: " + (", ".join(str(p) for p in chosen) or "nothing"), flush=True)
    return chosen


def verify(registry, selection, status=published_on, timeout=900, sleep=time.sleep):
    """Wait until every selected package of `registry` is on it."""
    chosen = selected(registry, selection)
    deadline = time.monotonic() + timeout
    while missing := [p for p in chosen if not status(p)]:
        if time.monotonic() > deadline:
            raise RuntimeError(f"not on {registry} after {timeout // 60} minutes: "
                               + ", ".join(str(p) for p in missing))
        sleep(20)
    print(f"on {registry}: " + (", ".join(str(p) for p in chosen) or "nothing selected"), flush=True)


# ---------------------------------------------------------------------------
# Tagging (person)


def releases_tagged(existing, release_tag_pattern):
    """{commit: Release} for commits carrying a release tag."""
    found = {}
    for name, commit in existing.items():
        if re.fullmatch(release_tag_pattern, name):
            try:
                found[commit] = load(files_at(commit))
            except (subprocess.CalledProcessError, ValueError, tomllib.TOMLDecodeError):
                continue
    return found


def resolve(commit, wanted, remote, existing, binds, published):
    """The commit to release: --commit; else where an unfinished release
    (--release, or the newest tagged) was started; else, for --release, the
    last commit on the default branch naming it; else HEAD."""
    if commit:
        return git("rev-parse", "--verify", f"{commit}^{{commit}}"), "--commit"
    here = load(files_on_disk())
    pattern = re.escape(here.tag_format).replace(r"\{release\}", ".+")
    have_commits(existing, remote)
    tagged = releases_tagged(existing, pattern)
    target = wanted or max((r.name for r in tagged.values()), key=version_key, default=None)
    for candidate, release in tagged.items():
        if release.name != target:
            continue
        mine = [p for p in release.packages if existing.get(p.tag) == candidate]
        if not any(binds(p, candidate) for p in mine) or split_from(release, existing, candidate, binds):
            continue
        if wanted or any(not published(p) for p in release.packages):
            return candidate, f"release {target} is already tagged there"
    if wanted:
        branch = here.branch
        for candidate in git("rev-list", "--first-parent", f"{remote}/{branch}").splitlines():
            try:
                name = load(files_at(candidate)).name
            except (subprocess.CalledProcessError, ValueError, tomllib.TOMLDecodeError):
                break
            if name == wanted:
                return candidate, f"the last commit on {remote}/{branch} naming release {wanted}"
            if version_key(name) < version_key(wanted):
                break
        raise RuntimeError(f"no commit on {remote}/{branch} names release {wanted}")
    return git("rev-parse", "--verify", "HEAD^{commit}"), "the local checkout (HEAD)"


def local_tag(name):
    return subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"refs/tags/{name}^{{commit}}"],
                          cwd=root(), text=True, capture_output=True).stdout.strip()


def wait_for(tag_name, packages, timeout, status=published_on, run=run_of, sleep=time.sleep, stale=None):
    """Return once the release run succeeded and every package is published."""
    deadline = time.monotonic() + timeout
    while True:
        missing = [p for p in packages if not status(p)]
        latest = run(tag_name)
        if latest and latest["url"] == stale:
            latest = None
        if latest and latest["status"] == "completed" and latest["conclusion"] != "success":
            raise RuntimeError(f"the {tag_name} run ended {latest['conclusion']}: {latest['url']} "
                               f"({len(packages) - len(missing)} of {len(packages)} published). Fix the cause, "
                               "then run `release.py tag` again: it pushes the tag again and the new run "
                               "publishes only what is still due.")
        if not missing and (latest is None or latest["status"] == "completed"):
            print(f"{tag_name}: published " + ", ".join(str(p) for p in packages), flush=True)
            return
        if time.monotonic() > deadline:
            raise RuntimeError(f"not published within {timeout // 60} minutes: "
                               + ", ".join(str(p) for p in missing))
        sleep(30)


def tag(commit=None, remote="origin", timeout=3600, dry_run=False, wanted=None,
        status=published_on, run=run_of, wait=wait_for):
    """Tag the release on its commit and wait for it to be published."""
    existing = remote_tags(remote)

    def binds(package, tagged):
        # A tag elsewhere holds the release there unless its run provably
        # published nothing; then the tag is moved.
        return status(package) or not dead(run(load(files_at(tagged)).tag), tagged)

    sha, why = resolve(commit, wanted, remote, existing, binds, status)
    read = files_at(sha)
    release = load(read)
    if subprocess.run(["git", "merge-base", "--is-ancestor", sha, f"{remote}/{release.branch}"],
                      cwd=root()).returncode:
        raise RuntimeError(f"{sha[:12]} is not on {remote}/{release.branch} as last fetched: release from the "
                           "default branch after the release PR merges (git fetch first)")
    if errors := check(read):
        raise RuntimeError(f"{CONFIG} at {sha[:12]} disagrees with the repository:\n  " + "\n  ".join(errors))
    have_commits(existing, remote)
    if split := split_from(release, existing, sha, binds):
        raise RuntimeError(split_error(sha, split))
    print(f"Release {release.name} from {sha} ({why})", flush=True)

    deletions, records, due = [], [], []
    for package in release.packages:
        if status(package):
            print(f"skip   {package.tag}: {package} is already published", flush=True)
            continue
        tagged = existing.get(package.tag)
        if tagged and tagged != sha:
            if binds(package, tagged):
                # Tagged elsewhere, shipping the same files (the split check
                # passed), and its run there may publish it: wait for that run.
                print(f"wait   {package.tag}: tagged at {tagged[:12]}, which ships the same files; "
                      "waiting for its release run", flush=True)
                if not dry_run:
                    wait(load(files_at(tagged)).tag, [package], timeout, status)
                continue
            print(f"move   {package.tag}: the release run at {tagged[:12]} published nothing", flush=True)
            deletions.append(package.tag)
            tagged = None
        if tagged:
            print(f"keep   {package.tag}: already tagged here", flush=True)
        else:
            print(f"tag    {package.tag} at {sha[:12]} (a record)", flush=True)
            records.append((package.tag, tag_message(str(package), release, sha)))
        due.append(package)
    if not due:
        print(f"Every package of release {release.name} is published.")
        return

    start, stale = None, None
    tagged = existing.get(release.tag)
    latest = run(release.tag) if tagged else None
    if tagged and tagged != sha:
        if not dead(latest, tagged):
            raise RuntimeError(f"{release.tag} is on {tagged[:12]}, not {sha[:12]}, and its run there may have "
                               f"published: release from that commit (--commit {tagged[:12]}) or wait for it")
        print(f"move   {release.tag}: its run at {tagged[:12]} ended {latest['conclusion']} before publishing",
              flush=True)
        deletions.append(release.tag)
        tagged = None
    elif tagged and latest and latest["status"] == "completed" and latest["conclusion"] != "success":
        print(f"retag  {release.tag}: its run ended {latest['conclusion']}; the new run publishes only what "
              "is still due", flush=True)
        deletions.append(release.tag)
        stale, tagged = latest["url"], None
    if tagged:
        print(f"wait   {release.tag}: already tagged here", flush=True)
    else:
        subjects = "".join(f"{p}\n" for p in due)
        start = (release.tag, tag_message(f"Release {release.name}\n\n{subjects}".rstrip(), release, sha))
        print(f"tag    {release.tag} at {sha[:12]} (starts the release run: "
              + ", ".join(p.name for p in due) + ")", flush=True)

    if dry_run:
        print("Dry run: nothing was tagged.")
        return
    if deletions:
        git("push", "--atomic", remote, *(f":refs/tags/{t}" for t in deletions), capture=False)
    for tag_name, message in records + ([start] if start else []):
        if local_tag(tag_name):
            git("tag", "-d", tag_name)
        git("tag", "-a", tag_name, "-m", message, sha)
    # Records first, in one atomic push; then the release tag alone, since
    # GitHub starts no workflow for a push of more than three tags.
    if records:
        git("push", "--atomic", remote, *(f"refs/tags/{t}" for t, _ in records), capture=False)
    if start:
        git("push", remote, f"refs/tags/{start[0]}", capture=False)
        print(f"pushed {start[0]}; waiting for the release run", flush=True)
    wait(release.tag, due, timeout, status, stale=stale)


def status_lines(read=None, status=published_on, remote="origin"):
    release = load(read or files_on_disk())
    try:
        existing = remote_tags(remote)
    except subprocess.CalledProcessError:
        existing = {}
    lines = [f"Release {release.name} (tag {release.tag}: "
             f"{existing[release.tag][:12] if release.tag in existing else 'not tagged'})"]
    for package in release.packages:
        tagged = existing.get(package.tag)
        lines.append(f"  {str(package):<48} {package.tag:<28} "
                     f"{'tagged ' + tagged[:12] if tagged else 'untagged':<20} "
                     f"{'published' if status(package) else 'due'}")
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    commands.add_parser("sync")
    commands.add_parser("status")
    tagger = commands.add_parser("tag")
    tagger.add_argument("--commit")
    tagger.add_argument("--release", dest="wanted")
    tagger.add_argument("--remote", default="origin")
    tagger.add_argument("--timeout", type=int, default=3600, help="seconds to wait for the run")
    tagger.add_argument("--dry-run", action="store_true")
    gater = commands.add_parser("gate")
    gater.add_argument("tag")
    gater.add_argument("--remote", default="origin")
    pre = commands.add_parser("preflight")
    pre.add_argument("--registry", required=True, choices=REGISTRIES)
    pre.add_argument("--selection", required=True)
    verifier = commands.add_parser("verify")
    verifier.add_argument("--registry", required=True, choices=REGISTRIES)
    verifier.add_argument("--selection", required=True)
    verifier.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            if errors := check(files_on_disk()):
                print("\n".join(errors), file=sys.stderr)
                return 1
            print(f"{CONFIG} matches the repository (release {load(files_on_disk()).name}).")
        elif args.command == "sync":
            print("\n".join(sync()) or "Nothing to change.")
        elif args.command == "status":
            print("\n".join(status_lines()))
        elif args.command == "tag":
            tag(args.commit, args.remote, args.timeout, args.dry_run, args.wanted)
        elif args.command == "gate":
            gate(args.tag, args.remote)
        elif args.command == "preflight":
            preflight(args.registry, args.selection)
        elif args.command == "verify":
            verify(args.registry, args.selection, timeout=args.timeout)
    except (RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
