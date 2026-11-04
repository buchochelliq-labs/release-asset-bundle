"""release.py: release-kit.yml checks, artifacts, tagging and the CI gate, on real git
repositories in temporary directories. Registries and runs are faked."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import release  # noqa: E402

CONFIG = """
packages:
  - name: core
    registry: crates.io
    manifest: crates/core/Cargo.toml
    version: "0.1.0"
  - name: tool
    registry: crates.io
    manifest: crates/tool/Cargo.toml
    version: "0.2.0"
"""

FILES = {
    "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n[workspace.package]\nversion = "0.1.0"\n',
    "crates/core/Cargo.toml": '[package]\nname = "core"\nversion.workspace = true\n',
    "crates/core/src/lib.rs": "pub fn core() {}\n",
    "crates/tool/Cargo.toml": '[package]\nname = "tool"\nversion = "0.2.0"\n[dependencies]\ncore = { path = "../core" }\n',
    "crates/tool/src/main.rs": "fn main() {}\n",
    "release-kit.yml": CONFIG,
}


def reader(files):
    def read(path):
        if path not in files:
            raise FileNotFoundError(path)
        return files[path]
    return read


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True).stdout.strip()


class Check(unittest.TestCase):
    def test_a_matching_workspace_passes(self):
        self.assertEqual(release.check(reader(FILES)), [])
        self.assertEqual(release.load(reader(FILES)).name, "0.2.0", "named by the last package")
        self.assertEqual(release.load(reader(FILES)).tag, "release-0.2.0")

    def test_every_registry_reads_its_manifest(self):
        files = {
            "py/pyproject.toml": '[project]\nname = "pkg"\nversion = "1.0.0rc1"\n',
            "js/package.json": json.dumps({"name": "@org/lib", "version": "2.0.0"}),
            "ext/package.json": json.dumps({"name": "ext", "publisher": "org", "version": "0.3.0"}),
            "bin/Cargo.toml": '[package]\nname = "bin"\nversion = "1.2.3"\n',
            "release-kit.yml": """
release:
  name_from: bin
packages:
  - name: pkg
    registry: pypi
    manifest: py/pyproject.toml
    version: "1.0.0rc1"
    artifacts: [{kind: python}]
  - name: "@org/lib"
    registry: npm
    manifest: js/package.json
    version: "2.0.0"
  - name: org.ext
    registry: vscode
    manifest: ext/package.json
    version: "0.3.0"
    artifacts: [{kind: vsix}]
  - name: bin
    registry: github
    manifest: bin/Cargo.toml
    version: "1.2.3"
    tag: "v{version}"
    artifacts: [{kind: rust-binary, bin: bin}]
""",
        }
        self.assertEqual(release.check(reader(files)), [])
        loaded = release.load(reader(files))
        self.assertEqual((loaded.name, loaded.packages[-1].tag), ("1.2.3", "v1.2.3"))

    def test_disagreements_are_reported(self):
        files = dict(FILES)
        files["release-kit.yml"] = CONFIG.replace('version: "0.2.0"', 'version: "0.3.0"').replace(
            "name: core", "name: kore", 1)
        errors = "\n".join(release.check(reader(files)))
        self.assertIn("tool: release-kit.yml says 0.3.0, crates/tool/Cargo.toml says 0.2.0", errors)
        self.assertIn("kore: crates/core/Cargo.toml names the package 'core'", errors)

    def test_a_package_comes_after_what_it_depends_on(self):
        files = dict(FILES)
        head, core, tool = CONFIG.split("  - name: ")
        files["release-kit.yml"] = head + "  - name: " + tool.rstrip() + "\n  - name: " + core
        self.assertIn("tool is listed before core, which it depends on", release.check(reader(files)))

    def test_record_tags_are_distinct_from_each_other_and_the_release_tag(self):
        files = dict(FILES)
        files["release-kit.yml"] = CONFIG.replace('version: "0.2.0"\n', 'version: "0.2.0"\n    tag: "release-{version}"\n')
        self.assertTrue(any("is the release tag" in e for e in release.check(reader(files))))

    def test_unknown_registry_and_unquoted_versions(self):
        files = dict(FILES)
        files["release-kit.yml"] = CONFIG.replace("registry: crates.io", "registry: maven", 1)
        self.assertTrue(any("unknown registry 'maven'" in e for e in release.check(reader(files))))
        files["release-kit.yml"] = CONFIG.replace('version: "0.1.0"', "version: 0.10")
        self.assertTrue(any("quote the version" in e for e in release.check(reader(files))))


class Artifacts(unittest.TestCase):
    def config(self, package):
        files = {"bin/Cargo.toml": '[package]\nname = "app"\nversion = "1.0.0"\n',
                 "release-kit.yml": "packages:\n  - " + package.strip().replace("\n", "\n    ") + "\n"}
        return reader(files)

    def test_artifacts_are_checked_against_their_registry(self):
        cases = {
            'name: app\nregistry: github\nmanifest: bin/Cargo.toml\nversion: "1.0.0"': "needs at least one artifact",
            'name: app\nregistry: github\nmanifest: bin/Cargo.toml\nversion: "1.0.0"\nartifacts: [{kind: wheel}]':
                "unknown kind 'wheel'",
            'name: app\nregistry: github\nmanifest: bin/Cargo.toml\nversion: "1.0.0"\nartifacts: [{kind: rust-binary}]':
                "rust-binary needs bin",
            'name: app\nregistry: github\nmanifest: bin/Cargo.toml\nversion: "1.0.0"\n'
            'artifacts: [{kind: rust-binary, bin: app, targets: [riscv64gc-unknown-linux-gnu]}]': "no runner known",
            'name: app\nregistry: crates.io\nmanifest: bin/Cargo.toml\nversion: "1.0.0"\n'
            'artifacts: [{kind: rust-binary, bin: app}]': "publishes from source",
            'name: app\nregistry: github\nmanifest: bin/Cargo.toml\nversion: "1.0.0"\n'
            'artifacts: [{kind: vsix, bin: x}]': "does not take bin",
        }
        for package, message in cases.items():
            errors = "\n".join(release.check(self.config(package)))
            self.assertIn(message, errors, package)

    def test_the_plan_has_a_job_per_artifact_and_target(self):
        read = self.config('name: app\nregistry: github\nmanifest: bin/Cargo.toml\nversion: "1.0.0"\n'
                           'artifacts:\n  - {kind: rust-binary, bin: app, include: [README.md]}\n'
                           '  - {kind: command, run: "make docs", outputs: "docs/*.pdf"}')
        selection = json.dumps([{"name": "app", "registry": "github"}])
        jobs = release.plan(selection, read=read)
        binaries = [j for j in jobs if j["kind"] == "rust-binary"]
        self.assertEqual([j["target"] for j in binaries], release.DEFAULT_TARGETS)
        self.assertEqual({j["os"] for j in binaries},
                         {"ubuntu-latest", "ubuntu-24.04-arm", "macos-latest", "windows-latest"})
        self.assertEqual(binaries[0]["crate"], "app", "the crate defaults to the manifest's package")
        self.assertEqual(jobs[-1]["kind"], "command")
        self.assertEqual(release.plan(json.dumps([]), read=read), [], "only what the gate selected")

    def test_maturin_plans_wheels_per_target_and_an_sdist(self):
        files = {"py/pyproject.toml": '[project]\nname = "pkg"\nversion = "1.0"\n',
                 "release-kit.yml": 'packages:\n  - name: pkg\n    registry: pypi\n    manifest: py/pyproject.toml\n'
                                    '    version: "1.0"\n    artifacts: [{kind: maturin, targets: [x86_64-unknown-linux-gnu]}]\n'}
        jobs = release.plan(json.dumps([{"name": "pkg", "registry": "pypi"}]), read=reader(files))
        self.assertEqual([j["kind"] for j in jobs], ["maturin", "maturin-sdist"])
        self.assertEqual({j["cargo"] for j in jobs}, {"py/Cargo.toml"}, "maturin builds the crate beside pyproject.toml")

    def test_a_command_artifact_is_built_with_checksums_for_github(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            try:
                job = {"id": "app-command-0", "kind": "command", "package": "app", "slug": "app", "version": "1.0.0",
                       "registry": "github", "run": 'mkdir -p build && echo "v$VERSION" > build/notes.txt',
                       "outputs": "build/*.txt"}
                made = release.build(job, Path(d) / "dist")
                self.assertEqual([p.name for p in made], ["notes.txt"])
                digest, name = (Path(d) / "dist/notes.txt.sha256").read_text().split()
                self.assertEqual(name, "notes.txt")
                self.assertEqual((Path(d) / "dist/notes.txt").read_text().strip(), "v1.0.0")
            finally:
                os.chdir(cwd)

    def test_binaries_are_archived_with_their_files(self):
        import tarfile
        import zipfile
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "onto").write_text("binary")
            (base / "README.md").write_text("read me")
            tgz = release.archive_binary(base / "onto", "onto-v1.0.0-x86_64-unknown-linux-gnu",
                                         [base / "README.md"], base / "out", windows=False)
            with tarfile.open(tgz) as archive:
                members = {m.name: m.mode for m in archive.getmembers()}
            self.assertEqual(set(members), {"onto-v1.0.0-x86_64-unknown-linux-gnu/onto",
                                            "onto-v1.0.0-x86_64-unknown-linux-gnu/README.md"})
            self.assertEqual(members["onto-v1.0.0-x86_64-unknown-linux-gnu/onto"] & 0o111, 0o111, "executable")
            zipped = release.archive_binary(base / "onto", "onto-win", [], base / "out", windows=True)
            self.assertEqual(zipfile.ZipFile(zipped).namelist(), ["onto-win/onto"])


class CiArtifact(unittest.TestCase):
    """kind: ci-artifact promotes the exact files a CI run built; GitHub is faked."""
    WHEEL = "dist/pkg-1.0-py3-none-any.whl"

    def archive(self, members):
        import io
        import zipfile
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name, data in members.items():
                archive.writestr(name, data)
        return stream.getvalue()

    def config(self, artifact):
        files = {"py/pyproject.toml": '[project]\nname = "pkg"\nversion = "1.0"\n',
                 "release-kit.yml": 'packages:\n  - name: pkg\n    registry: pypi\n    manifest: py/pyproject.toml\n'
                                    f'    version: "1.0"\n    artifacts: [{artifact}]\n'}
        return reader(files)

    def job(self, data, **extra):
        import hashlib
        return {"id": "pkg-ci-artifact-0", "kind": "ci-artifact", "package": "pkg", "slug": "pkg",
                "version": "1.0", "registry": "pypi", "artifact": 42,
                "sha256": hashlib.sha256(data).hexdigest(), "files": "dist/*.whl", "test": None, **extra}

    def github(self, data, meta=None, compare=("ahead", ["release-kit.yml"])):
        import hashlib
        meta = {"expired": False, "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                "workflow_run": {"head_sha": "c0ffee" * 6 + "abcd"}, **(meta or {})}

        def api(path, accept=None):
            if path.endswith("/zip"):
                return 302, {"Location": "https://blob.example/a.zip?sig=x"}, b""
            if "/compare/" in path:
                relation, files = compare
                return 200, {}, json.dumps({"status": relation, "files": [{"filename": f} for f in files]}).encode()
            return 200, {}, json.dumps(meta).encode()
        return api

    def promote(self, data, job=None, **github):
        from unittest import mock
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(release, "github_api", self.github(data, **github)), \
                mock.patch.object(release, "download", lambda url: data), \
                mock.patch.object(release, "repository", lambda: "owner/repo"), \
                mock.patch.object(release, "git", lambda *args, **kwargs: "T1"):
            made = release.build(job or self.job(data), Path(d) / "dist")
            return [(p.name, p.read_bytes()) for p in made]

    def test_ci_artifacts_are_checked(self):
        cases = {
            "{kind: ci-artifact}": "ci-artifact needs artifact, files, sha256",
            '{kind: ci-artifact, artifact: "42", sha256: ' + "a" * 64 + ', files: "dist/*"}': "numeric id",
            "{kind: ci-artifact, artifact: 42, sha256: ABC, files: 'dist/*'}": "64 lowercase hex",
            "{kind: ci-artifact, artifact: 42, sha256: " + "a" * 64 + ", files: ''}": "files is a glob",
            "{kind: ci-artifact, artifact: 42, sha256: " + "a" * 64 + ", files: 'dist/*', run: x}":
                "does not take run",
        }
        for artifact, message in cases.items():
            self.assertIn(message, "\n".join(release.check(self.config(artifact))), artifact)
        self.assertEqual(release.check(self.config("{kind: ci-artifact, artifact: 42, sha256: " + "a" * 64
                                                   + ", files: 'dist/*.whl'}")), [])

    def test_the_plan_carries_the_pin(self):
        read = self.config("{kind: ci-artifact, artifact: 42, sha256: " + "a" * 64 + ", files: 'dist/*.whl', "
                           "test: 'pip install $FILES'}")
        [job] = release.plan(json.dumps([{"name": "pkg", "registry": "pypi"}]), read=read)
        self.assertEqual((job["kind"], job["artifact"], job["sha256"], job["files"], job["test"]),
                         ("ci-artifact", 42, "a" * 64, "dist/*.whl", "pip install $FILES"))

    def test_the_reviewed_bytes_are_promoted_unchanged(self):
        data = self.archive({self.WHEEL: b"reviewed wheel", "evidence/log.txt": b"not published"})
        self.assertEqual(self.promote(data), [("pkg-1.0-py3-none-any.whl", b"reviewed wheel")])

    def test_the_commit_that_records_the_pin_may_follow_the_build(self):
        # The pin cannot be in the commit that built the artifact: the commit
        # that records it, changing only release-kit.yml, is the same release.
        data = self.archive({self.WHEEL: b"reviewed wheel"})
        for compare in (("identical", []), ("ahead", ["release-kit.yml"])):
            self.assertEqual(self.promote(data, compare=compare), [("pkg-1.0-py3-none-any.whl", b"reviewed wheel")])

    def test_promotion_fails_closed(self):
        data = self.archive({self.WHEEL: b"reviewed wheel"})
        cases = [
            ("the archive's SHA-256", dict(job=self.job(data, sha256="0" * 64), meta={"digest": None})),
            ("GitHub records", dict(meta={"digest": "sha256:" + "1" * 64})),
            ("has expired", dict(meta={"expired": True})),
            ("does not descend from", dict(compare=("diverged", ["release-kit.yml"]))),
            ("src/app.py changed", dict(compare=("ahead", ["release-kit.yml", "src/app.py"]))),
            ("no member matches", dict(job=self.job(data, files="dist/*.tar.gz"))),
        ]
        for message, kwargs in cases:
            with self.assertRaisesRegex(RuntimeError, message):
                self.promote(data, **kwargs)
        for wrong in ("pkg-0.9", "pkg-11.0", "pkg-1.0rc1", "pkg-1.0.1"):
            stale = self.archive({f"dist/{wrong}-py3-none-any.whl": b"other version"})
            with self.assertRaisesRegex(RuntimeError, "is not version 1.0", msg=wrong):
                self.promote(stale)
        unsafe = self.archive({"dist/../pkg-1.0-py3-none-any.whl": b"x"})
        with self.assertRaisesRegex(RuntimeError, "unsafe path"):
            self.promote(unsafe, job=self.job(unsafe, files="dist/*"))

    def test_the_artifact_test_runs_on_the_promoted_files(self):
        data = self.archive({self.WHEEL: b"reviewed wheel"})
        with self.assertRaises(subprocess.CalledProcessError):
            self.promote(data, job=self.job(data, test="test -z \"$FILES\""))
        self.promote(data, job=self.job(data, test='for f in $FILES; do test -s "$f"; done'))


class Repo(unittest.TestCase):
    """A bare remote, a working clone and helpers, with the cwd in the clone."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        base = Path(self.dir.name)
        self.remote, self.work = base / "remote.git", base / "work"
        sh(base, "git", "init", "-q", "--bare", "-b", "main", str(self.remote))
        sh(base, "git", "init", "-q", "-b", "main", str(self.work))
        for key, value in [("user.name", "t"), ("user.email", "t@example.invalid"), ("tag.gpgSign", "false")]:
            sh(self.work, "git", "config", key, value)
        sh(self.work, "git", "remote", "add", "origin", str(self.remote))
        self.write(FILES)
        self.commit("first")
        self.cwd = os.getcwd()
        os.chdir(self.work)
        self.published = set()

    def tearDown(self):
        os.chdir(self.cwd)
        self.dir.cleanup()

    def write(self, files):
        for path, text in files.items():
            target = self.work / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)

    def commit(self, message):
        sh(self.work, "git", "add", "-A")
        sh(self.work, "git", "commit", "-q", "-m", message)
        sh(self.work, "git", "push", "-q", "origin", "main")
        return sh(self.work, "git", "rev-parse", "HEAD")

    def status(self, package):
        return (package.name, package.version) in self.published

    def tag(self, **kw):
        waited = []
        release.tag(status=self.status, run=kw.pop("run", lambda tag: None),
                    wait=lambda *a, **k: waited.append(a[0]), **kw)
        return waited

    def remote_tags(self):
        return release.remote_tags("origin")


class Tagging(Repo):
    def test_records_then_the_release_tag_all_annotated_with_trailers(self):
        head = sh(self.work, "git", "rev-parse", "HEAD")
        waited = self.tag()
        tags = self.remote_tags()
        self.assertEqual({t: tags[t] for t in ("core-v0.1.0", "tool-v0.2.0", "release-0.2.0")},
                         dict.fromkeys(("core-v0.1.0", "tool-v0.2.0", "release-0.2.0"), head))
        self.assertEqual(release.lightweight("origin", list(tags)), set())
        self.assertEqual(release.trailers("release-0.2.0"), {"Release": "0.2.0", "Commit": head})
        self.assertEqual(waited, ["release-0.2.0"])

    def test_published_packages_are_skipped(self):
        self.published.add(("core", "0.1.0"))
        self.tag()
        self.assertNotIn("core-v0.1.0", self.remote_tags())

    def test_nothing_is_tagged_off_the_default_branch(self):
        sh(self.work, "git", "checkout", "-q", "-b", "feature")
        (self.work / "notes.txt").write_text("x")
        sh(self.work, "git", "add", "-A")
        sh(self.work, "git", "commit", "-q", "-m", "unmerged")
        with self.assertRaisesRegex(RuntimeError, "not on origin/main"):
            self.tag()

    def test_a_split_release_is_refused(self):
        self.tag()  # release 0.2.0 tagged on the first commit, nothing published yet
        self.write({"crates/core/src/lib.rs": "pub fn core() { /* changed */ }\n"})
        later = self.commit("change core without a bump")
        # The run on the first commit may still publish: those tags bind.
        live = {"status": "in_progress", "conclusion": "", "url": "u", "head_sha": "x", "publish_started": True}
        with self.assertRaisesRegex(RuntimeError, "changes packages already tagged"):
            self.tag(commit=later, run=lambda tag: live)

    def test_tags_whose_run_published_nothing_are_moved(self):
        first = sh(self.work, "git", "rev-parse", "HEAD")
        self.tag()
        self.write({"crates/core/src/lib.rs": "pub fn core() { /* fixed */ }\n"})
        later = self.commit("fix")
        failed = {"status": "completed", "conclusion": "failure", "url": "u", "head_sha": first,
                  "publish_started": False}
        self.tag(commit=later, run=lambda tag: failed)
        tags = self.remote_tags()
        self.assertEqual({tags["core-v0.1.0"], tags["release-0.2.0"]}, {later})

    def test_a_tag_elsewhere_with_the_same_files_is_waited_for(self):
        self.tag()
        self.write({"README.md": "docs only\n"})
        later = self.commit("docs")
        live = {"status": "in_progress", "conclusion": "", "url": "u", "head_sha": "x", "publish_started": True}
        waited = self.tag(commit=later, run=lambda tag: live)
        self.assertIn("release-0.2.0", waited)


class Gate(Repo):
    def gate(self, tag_name):
        out = Path(self.dir.name) / "out"
        out.write_text("")
        release.gate(tag_name, status=self.status, output=out)
        return dict(line.split("=", 1) for line in out.read_text().splitlines())

    def test_the_gate_selects_what_is_due(self):
        self.published.add(("core", "0.1.0"))
        self.tag()
        outputs = self.gate("release-0.2.0")
        selection = json.loads(outputs["selection"])
        self.assertEqual([s["name"] for s in selection], ["tool"])
        self.assertEqual((outputs["crates-io"], outputs["pypi"]), ("true", "false"))

    def test_a_lightweight_release_tag_is_refused(self):
        sh(self.work, "git", "tag", "release-0.2.0")
        with self.assertRaisesRegex(RuntimeError, "must be an annotated tag"):
            self.gate("release-0.2.0")

    def test_everything_published_is_refused(self):
        self.tag()
        self.published.update({("core", "0.1.0"), ("tool", "0.2.0")})
        with self.assertRaisesRegex(RuntimeError, "already published"):
            self.gate("release-0.2.0")

    def test_a_tag_not_on_the_default_branch_is_refused(self):
        sh(self.work, "git", "checkout", "-q", "-b", "side")
        (self.work / "x.txt").write_text("x")
        sh(self.work, "git", "add", "-A")
        sh(self.work, "git", "commit", "-q", "-m", "side")
        head = sh(self.work, "git", "rev-parse", "HEAD")
        sh(self.work, "git", "tag", "-a", "release-0.2.0", "-m", f"Release 0.2.0\n\nRelease: 0.2.0\nCommit: {head}\n")
        with self.assertRaisesRegex(RuntimeError, "not on main"):
            self.gate("release-0.2.0")


class Foreign(Repo):
    """#4: a tag made for something else, or before release-kit, is refused clearly."""

    def foreign_tag(self, name, subject):
        old = sh(self.work, "git", "rev-parse", "HEAD")
        sh(self.work, "git", "tag", "-a", name, "-m", f"{subject}\n\nRelease: x\nCommit: {old}\n", old)
        sh(self.work, "git", "push", "-q", "origin", f"refs/tags/{name}")
        sh(self.work, "git", "tag", "-d", name)
        self.write({"crates/core/src/lib.rs": "pub fn core() { /* later */ }\n"})
        return self.commit("later")

    def test_another_packages_tag_of_the_same_name_is_refused(self):
        # As a crate's legacy record `rs-rich 0.0.12` collides with PyPI's rs-rich 0.0.12.
        self.foreign_tag("core-v0.1.0", "core 0.1.0")
        with self.assertRaisesRegex(RuntimeError, "core-v0.1.0 at .*not core 0.1.0 .crates.io.'s record"):
            self.tag()

    def test_the_gate_refuses_it_too(self):
        later = self.foreign_tag("core-v0.1.0", "core 0.1.0")
        sh(self.work, "git", "tag", "-a", "release-0.2.0", "-m", f"Release 0.2.0\n\nRelease: 0.2.0\nCommit: {later}\n")
        out = Path(self.dir.name) / "out"
        out.write_text("")
        with self.assertRaisesRegex(RuntimeError, "already exist for something else"):
            release.gate("release-0.2.0", status=self.status, output=out)

    def test_a_commit_without_release_kit_yml_is_named(self):
        sh(self.work, "git", "rm", "-q", "release-kit.yml")
        old = self.commit("before release-kit")
        with self.assertRaisesRegex(RuntimeError, "core-v0.1.0 is on .*which has no release-kit.yml: the tag predates"):
            release.release_at(old, release.Package("core", "crates.io", "m", "0.1.0"))


class VerifyOnly(Repo):
    """#6: a verify-only run re-checks a finished upload and selects it all."""

    def gate(self):
        out = Path(self.dir.name) / "out"
        out.write_text("")
        release.gate("release-0.2.0", status=self.status, output=out, verify_only=True)
        return dict(line.split("=", 1) for line in out.read_text().splitlines())

    def test_every_published_package_of_the_release_is_selected(self):
        self.tag()
        self.published.update({("core", "0.1.0"), ("tool", "0.2.0")})
        outputs = self.gate()
        self.assertEqual([s["name"] for s in json.loads(outputs["selection"])], ["core", "tool"])
        self.assertEqual((outputs["verify-only"], outputs["crates-io"]), ("true", "true"))

    def test_a_partial_upload_is_left_to_tag(self):
        self.tag()
        self.published.add(("core", "0.1.0"))
        with self.assertRaisesRegex(RuntimeError, "not published: tool 0.2.0.*Run `release.py tag` again"):
            self.gate()


class PublishSteps(unittest.TestCase):
    selection = json.dumps([{"name": "core", "registry": "crates.io", "version": "0.1.0",
                             "manifest": "crates/core/Cargo.toml", "tag": "core-v0.1.0"},
                            {"name": "pkg", "registry": "pypi", "version": "1.0", "manifest": "p.toml",
                             "tag": "pkg-v1.0"}])

    def test_preflight_refuses_a_version_published_since_the_gate(self):
        with self.assertRaisesRegex(RuntimeError, "already on crates.io: core 0.1.0"):
            release.preflight("crates.io", self.selection, status=lambda p: True)
        self.assertEqual([p.name for p in release.preflight("pypi", self.selection, status=lambda p: False)], ["pkg"])

    def test_verify_waits_until_the_registry_has_it(self):
        answers = iter([False, False, True])
        release.verify("crates.io", self.selection, status=lambda p: next(answers), sleep=lambda s: None)


class NewCrates(unittest.TestCase):
    """#3: a crate's first version publishes with the new-crate token."""
    selection = json.dumps([
        {"name": n, "registry": "crates.io", "version": "0.1.0", "manifest": f"crates/{n}/Cargo.toml",
         "tag": f"{n}-v0.1.0"} for n in ("core", "fresh", "tool", "app")])

    def test_preflight_says_which_crates_are_new(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            out.write_text("")
            release.preflight("crates.io", self.selection, status=lambda p: False,
                              exists=lambda name: name not in {"fresh", "app"}, output=out)
            outputs = dict(line.split("=", 1) for line in out.read_text().splitlines())
        self.assertEqual(json.loads(outputs["new_crates"]), ["fresh", "app"])
        self.assertEqual((outputs["new_crate"], outputs["existing_crate"]), ("true", "true"))

    def test_runs_of_crates_sharing_a_credential_publish_in_order(self):
        calls = []
        groups = release.publish_crates(self.selection, json.dumps(["fresh", "app"]), "trusted-t", "new-t",
                                        runner=lambda cmd, **kw: calls.append((cmd, kw["env"])))
        self.assertEqual(groups, [("trusted", ["core"]), ("new", ["fresh"]), ("trusted", ["tool"]),
                                  ("new", ["app"])])
        self.assertEqual(calls[0][0][:4], ["cargo", "publish", "--dry-run", "--locked"])
        self.assertEqual(calls[0][1], {"CARGO_REGISTRY_TOKEN": ""}, "the dry run uploads nothing")
        self.assertEqual([(c[c.index("-p") + 1], e["CARGO_REGISTRY_TOKEN"]) for c, e in calls[1:]],
                         [("core", "trusted-t"), ("fresh", "new-t"), ("tool", "trusted-t"), ("app", "new-t")])

    def test_a_missing_token_is_refused_before_any_upload(self):
        calls = []
        with self.assertRaisesRegex(RuntimeError, "missing the new-crate token"):
            release.publish_crates(self.selection, json.dumps(["fresh"]), "trusted-t", "",
                                   runner=lambda cmd, **kw: calls.append(cmd))
        self.assertEqual(calls, [])
        # With no new crate, the new-crate token is not needed.
        release.publish_crates(self.selection, "[]", "trusted-t", "", runner=lambda cmd, **kw: calls.append(cmd))
        self.assertEqual(len(calls), 2)


class NewCrateRateLimit(unittest.TestCase):
    """A first release of many crates waits out crates.io's new-crate limit."""
    selection = json.dumps([
        {"name": n, "registry": "crates.io", "version": "0.1.0", "manifest": f"crates/{n}/Cargo.toml",
         "tag": f"{n}-v0.1.0"} for n in ("a", "b")])
    LIMITED = ("error: failed to publish to registry at https://crates.io\n\nCaused by:\n  the remote server "
               "responded with an error (status 429 Too Many Requests): You have published too many new crates "
               "in a short period of time. Please try again after Wed, 04 Nov 2026 03:10:00 GMT and see "
               "https://crates.io/docs/rate-limits for more details.\n")

    def test_new_crates_publish_one_at_a_time_and_wait_when_limited(self):
        calls, waits, fails = [], [], {"b": 2}

        def runner(cmd, env):
            calls.append(cmd)
            crate = cmd[-1]
            if "--dry-run" not in cmd and fails.get(crate):
                fails[crate] -= 1
                raise subprocess.CalledProcessError(101, cmd, output=self.LIMITED)

        release.publish_crates(self.selection, json.dumps(["a", "b"]), "", "new-t", runner=runner,
                               sleep=waits.append)
        self.assertEqual([c[-1] for c in calls[1:]], ["a", "b", "b", "b"])
        self.assertEqual(len(waits), 2)

    def test_the_wait_comes_from_the_message(self):
        from email.utils import parsedate_to_datetime
        at = parsedate_to_datetime("Wed, 04 Nov 2026 03:10:00 GMT").timestamp()
        self.assertEqual(release.rate_limit_wait(self.LIMITED, now=at - 100), 105)
        self.assertEqual(release.rate_limit_wait("status 429 Too Many Requests"), release.NEW_CRATE_WAIT)
        self.assertIsNone(release.rate_limit_wait("error: invalid token"))

    def test_any_other_failure_stops_at_once(self):
        def runner(cmd, env):
            if "--dry-run" not in cmd:
                raise subprocess.CalledProcessError(101, cmd, output="error: 403 Forbidden")
        with self.assertRaises(subprocess.CalledProcessError):
            release.publish_crates(self.selection, json.dumps(["a", "b"]), "", "new-t", runner=runner,
                                   sleep=lambda s: self.fail("no wait for a refusal"))


class Consumer(unittest.TestCase):
    """#6: a published package is used as a stranger would."""
    selection = json.dumps([
        {"name": "core", "registry": "crates.io", "version": "0.1.0", "manifest": "crates/core/Cargo.toml",
         "tag": "core-v0.1.0"},
        {"name": "tool", "registry": "crates.io", "version": "0.2.0", "manifest": "crates/tool/Cargo.toml",
         "tag": "tool-v0.2.0"},
        {"name": "pkg", "registry": "pypi", "version": "1.0", "manifest": "p.toml", "tag": "python-v1.0"}])

    def test_a_binary_is_installed_and_a_library_is_built_against(self):
        calls = []
        release.consume("crates.io", self.selection, runner=lambda cmd, **kw: calls.append((cmd, kw.get("env"))),
                        read=reader(FILES))
        commands = [c[:3] for c, _ in calls]
        self.assertEqual(commands, [["cargo", "new", "--quiet"], ["cargo", "add", "core@=0.1.0"],
                                    ["cargo", "check", "--quiet"], ["cargo", "install", "tool"]])
        install = calls[-1][0]
        self.assertEqual(install[install.index("--version") + 1], "=0.2.0")
        self.assertIn("--locked", install)
        self.assertTrue(all(env["CARGO_REGISTRY_TOKEN"] == "" for _, env in calls), "no credentials")

    def test_a_lagging_index_is_retried_then_reported(self):
        tries = []

        def runner(cmd, **kw):
            if "install" in cmd:
                tries.append(cmd)
                raise subprocess.CalledProcessError(1, cmd)
        with self.assertRaisesRegex(RuntimeError, "installing pkg 1.0 .pypi. failed 3 times"):
            release.consume("pypi", self.selection, runner=runner, tries=3, sleep=lambda s: None)
        self.assertEqual(len(tries), 3)


class ArtifactTests(unittest.TestCase):
    """#7: an artifact's own test runs on what was built, before publishing."""

    def test_the_plan_carries_the_test_to_the_targets_it_names(self):
        files = {"py/pyproject.toml": '[project]\nname = "pkg"\nversion = "1.0"\n',
                 "release-kit.yml": 'packages:\n  - name: pkg\n    registry: pypi\n    manifest: py/pyproject.toml\n'
                                    '    version: "1.0"\n    artifacts:\n      - kind: maturin\n'
                                    '        targets: [x86_64-unknown-linux-gnu, x86_64-apple-darwin]\n'
                                    '        test: {run: "pytest", targets: [x86_64-unknown-linux-gnu]}\n'}
        self.assertEqual(release.check(reader(files)), [])
        jobs = release.plan(json.dumps([{"name": "pkg", "registry": "pypi"}]), read=reader(files))
        self.assertEqual({j["target"]: j["test"] for j in jobs},
                         {"x86_64-unknown-linux-gnu": "pytest", "x86_64-apple-darwin": None, "": None})

    def test_a_malformed_test_is_refused(self):
        files = {"bin/Cargo.toml": '[package]\nname = "app"\nversion = "1.0.0"\n',
                 "release-kit.yml": 'packages:\n  - name: app\n    registry: github\n    manifest: bin/Cargo.toml\n'
                                    '    version: "1.0.0"\n    artifacts: [{kind: rust-binary, bin: app, test: {cmd: x}}]\n'}
        self.assertTrue(any("test is a command" in e for e in release.check(reader(files))))
        files["release-kit.yml"] = files["release-kit.yml"].replace(
            "kind: rust-binary, bin: app, test: {cmd: x}",
            "kind: command, run: make, outputs: '*.txt', test: {run: x, targets: [x86_64-unknown-linux-gnu]}")
        self.assertTrue(any("command is not built per target" in e for e in release.check(reader(files))),
                        "targets would never match a job that has no target")

    def test_a_failing_test_fails_the_build(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            try:
                job = {"id": "app-command-0", "kind": "command", "package": "app", "slug": "app",
                       "version": "1.0.0", "registry": "github", "run": "mkdir -p b && echo hi > b/n.txt",
                       "outputs": "b/*.txt", "test": 'grep -q hi "$DIST/n.txt" && test "$VERSION" = 1.0.0'}
                self.assertEqual([p.name for p in release.build(job, Path(d) / "dist")], ["n.txt"])
                job["test"] = "exit 3"
                with self.assertRaises(subprocess.CalledProcessError):
                    release.build(job, Path(d) / "dist2")
            finally:
                os.chdir(cwd)


class MaturinFromCargo(unittest.TestCase):
    """A pyproject.toml whose version maturin takes from a Cargo workspace."""

    FILES = {
        "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n[workspace.package]\nversion = "0.2.0-rc.1"\n',
        "crates/cli/Cargo.toml": '[package]\nname = "cli"\nversion.workspace = true\n',
        "pyproject.toml": '[project]\nname = "tool"\ndynamic = ["version"]\n'
                          '[tool.maturin]\nbindings = "bin"\nmanifest-path = "crates/cli/Cargo.toml"\n',
        "release-kit.yml": 'packages:\n  - name: tool\n    registry: pypi\n    manifest: pyproject.toml\n'
                           '    version: "0.2.0rc1"\n    artifacts:\n      - kind: maturin\n'
                           '        targets: [x86_64-unknown-linux-musl]\n        prepare: ./notices.sh\n',
    }

    def test_the_version_is_the_crates_as_pep_440(self):
        self.assertEqual(release.check(reader(self.FILES)), [])
        self.assertEqual(release.pep440("1.0.0"), "1.0.0")
        self.assertEqual(release.pep440("1.0.0-beta.2"), "1.0.0b2")
        files = {**self.FILES, "Cargo.toml": self.FILES["Cargo.toml"].replace("0.2.0-rc.1", "0.3.0")}
        self.assertTrue(any("says 0.2.0rc1" in e and "says 0.3.0" in e for e in release.check(reader(files))))

    def test_maturin_runs_where_pyproject_names_the_crate(self):
        jobs = release.plan(json.dumps([{"name": "tool", "registry": "pypi"}]), read=reader(self.FILES))
        self.assertEqual([(j["kind"], j["os"], j["cargo"], j["dir"]) for j in jobs],
                         [("maturin", "ubuntu-latest", "", "."), ("maturin-sdist", "ubuntu-latest", "", ".")])
        self.assertEqual({j["prepare"] for j in jobs}, {"./notices.sh"}, "prepared before every job")

    def test_prepare_runs_with_the_job_described(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            try:
                release.prepare({"package": "tool", "version": "1.0", "target": "x86_64-unknown-linux-musl",
                                 "prepare": 'echo "$PACKAGE $VERSION $TARGET" > prepared'})
                self.assertEqual(Path("prepared").read_text().strip(), "tool 1.0 x86_64-unknown-linux-musl")
                release.prepare({"package": "tool", "version": "1.0", "prepare": ""})
            finally:
                os.chdir(cwd)

    def test_a_blank_prepare_is_refused(self):
        files = {**self.FILES, "release-kit.yml": self.FILES["release-kit.yml"].replace("./notices.sh", "''")}
        self.assertTrue(any("prepare is a command" in e for e in release.check(reader(files))))


class GitHubReleaseOptions(unittest.TestCase):
    """A github package's notes, checksums file and attestations."""

    def config(self, options):
        return reader({
            "bin/Cargo.toml": '[package]\nname = "app"\nversion = "1.0.0"\n',
            "release-kit.yml": 'packages:\n  - name: app\n    registry: github\n    manifest: bin/Cargo.toml\n'
                               '    version: "1.0.0"\n    tag: "v{version}"\n'
                               '    artifacts: [{kind: rust-binary, bin: app}]\n' + options,
        })

    def test_options_are_checked(self):
        self.assertEqual(release.check(self.config(
            '    notes: ./notes.sh\n    checksums: SHA256SUMS\n    attest: true\n')), [])
        cases = {
            "    notes: ''\n": "notes is a command",
            "    checksums: ../SUMS\n": "checksums is a file name",
            "    attest: yes please\n": "attest is true or false",
        }
        for options, message in cases.items():
            self.assertIn(message, "\n".join(release.check(self.config(options))), options)
        crate = reader({"c/Cargo.toml": '[package]\nname = "c"\nversion = "1.0.0"\n',
                        "release-kit.yml": 'packages:\n  - name: c\n    registry: crates.io\n'
                                           '    manifest: c/Cargo.toml\n    version: "1.0.0"\n    attest: true\n'})
        self.assertIn("only apply to a github package", "\n".join(release.check(crate)))

    def test_the_release_is_shaped_from_its_options(self):
        with tempfile.TemporaryDirectory() as d:
            stage = Path(d) / "files"
            stage.mkdir()
            (stage / "app-v1.0.0-x.tar.gz").write_bytes(b"archive")
            (stage / "app-v1.0.0-x.tar.gz.sha256").write_text("ignored\n")
            calls = []

            def runner(command, **kwargs):
                self.assertEqual(command[1], "-c", "run by bash itself, not a shell wrapper")
                calls.append((command[-1], kwargs["env"]["VERSION"], kwargs["env"]["TAG"]))
                return subprocess.CompletedProcess(command, 0, stdout="## 1.0.0\n\nNotes.\n")

            shape = release.github_release(
                "app", stage, Path(d) / "notes.md", runner=runner,
                read=self.config('    notes: ./notes.sh "$VERSION"\n    checksums: SHA256SUMS\n    attest: true\n'))
            self.assertEqual(shape, {"notes": True, "attest": True, "checksums": "SHA256SUMS", "homebrew": {},
                                     "chocolatey": {}})
            self.assertEqual(calls, [('./notes.sh "$VERSION"', "1.0.0", "v1.0.0")])
            self.assertEqual((Path(d) / "notes.md").read_text(), "## 1.0.0\n\nNotes.\n")
            import hashlib
            self.assertEqual((stage / "SHA256SUMS").read_text(),
                             f"{hashlib.sha256(b'archive').hexdigest()}  app-v1.0.0-x.tar.gz\n",
                             "every file but the .sha256 ones")

    def test_without_options_github_generates_the_notes(self):
        with tempfile.TemporaryDirectory() as d:
            shape = release.github_release("app", d, Path(d) / "notes.md", read=self.config(""))
            self.assertEqual(shape, {"notes": False, "attest": False, "checksums": "", "homebrew": {}, "chocolatey": {}})
            self.assertEqual(list(Path(d).iterdir()), [])

    def test_empty_notes_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError):
                release.github_release("app", d, Path(d) / "n.md", read=self.config("    notes: 'true'\n"),
                                       runner=lambda c, **k: subprocess.CompletedProcess(c, 0, stdout="\n"))


class Channels(unittest.TestCase):
    """Homebrew and Chocolatey, made from a github package's release."""

    CONFIG = ('packages:\n  - name: tool\n    registry: github\n    manifest: Cargo.toml\n'
              '    version: "1.2.0"\n    tag: "v{version}"\n'
              '    artifacts: [{kind: rust-binary, bin: tool}]\n'
              '    homebrew: {tap: org/homebrew-tap, formula: packaging/tool.rb.tmpl}\n'
              '    chocolatey: {package: packaging/choco, test: tool --version}\n')
    SUMS = "\n".join(f"{c * 64}  tool-v1.2.0-{t}" for c, t in [
        ("a", "aarch64-apple-darwin.tar.gz"), ("b", "x86_64-unknown-linux-musl.tar.gz"),
        ("c", "x86_64-pc-windows-msvc.zip")]) + "\n" + "d" * 64 + "  SHA256SUMS\n"

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.dir.name)
        files = {
            "Cargo.toml": '[package]\nname = "tool"\nversion = "1.2.0"\n',
            "release-kit.yml": self.CONFIG,
            "packaging/tool.rb.tmpl": 'class Tool < Formula\n  version "${version}"\n'
                                      '  url "${url_aarch64_apple_darwin}"\n  sha256 "${sha256_aarch64_apple_darwin}"\n'
                                      '  def install\n    bin.install "tool"  # #{prefix} stays\n  end\nend\n',
            "packaging/choco/tool.nuspec.tmpl": "<version>${version}</version>\n",
            "packaging/choco/tools/chocolateyinstall.ps1.tmpl":
                "$d = ${env:TEMP}\nurl64bit = '${url_x86_64_pc_windows_msvc}'\nchecksum64 = '${sha256_x86_64_pc_windows_msvc}'\n",
            "packaging/choco/tools/LICENSE.txt": "MIT\n",
        }
        for name, text in files.items():
            Path(name).parent.mkdir(parents=True, exist_ok=True)
            Path(name).write_text(text)
        sh(".", "git", "init", "-q")

    def tearDown(self):
        os.chdir(self.cwd)
        self.dir.cleanup()

    def test_channels_are_checked(self):
        read = files_on_disk = release.files_on_disk(Path.cwd())
        self.assertEqual(release.check(read), [])
        for bad, message in [("{tap: org/homebrew-tap}", "homebrew needs formula"),
                             ("{tap: nope, formula: f}", "tap is owner/repository"),
                             ("{tap: org/t, formula: f, url: x}", "does not take url")]:
            Path("release-kit.yml").write_text(self.CONFIG.replace(
                "{tap: org/homebrew-tap, formula: packaging/tool.rb.tmpl}", bad))
            self.assertIn(message, "\n".join(release.check(files_on_disk)), bad)

    def test_the_formula_is_rendered_from_the_release(self):
        [formula] = release.render_channel("tool", "homebrew", self.SUMS, "out", repo="org/tool")
        self.assertEqual(formula.name, "tool.rb")
        text = formula.read_text()
        self.assertIn('version "1.2.0"', text)
        self.assertIn('url "https://github.com/org/tool/releases/download/v1.2.0/'
                      'tool-v1.2.0-aarch64-apple-darwin.tar.gz"', text)
        self.assertIn(f'sha256 "{"a" * 64}"', text)
        self.assertIn("#{prefix} stays", text)

    def test_the_chocolatey_package_is_rendered_and_copied(self):
        written = release.render_channel("tool", "chocolatey", self.SUMS, "out", repo="org/tool")
        self.assertEqual(sorted(str(p.relative_to("out")) for p in written),
                         ["tool.nuspec", "tools/LICENSE.txt", "tools/chocolateyinstall.ps1"])
        script = Path("out/tools/chocolateyinstall.ps1").read_text()
        self.assertIn("${env:TEMP}", script, "PowerShell's own ${env:...} is left alone")
        self.assertIn(f"checksum64 = '{'c' * 64}'", script)

    def test_a_placeholder_without_a_value_is_an_error(self):
        Path("packaging/tool.rb.tmpl").write_text('sha256 "${sha256_riscv64gc_unknown_linux_gnu}"\n')
        with self.assertRaisesRegex(ValueError, "no value for .*riscv64gc"):
            release.render_channel("tool", "homebrew", self.SUMS, "out", repo="org/tool")

    def test_the_gate_says_which_channels_are_due(self):
        read = release.files_on_disk(Path.cwd())
        outputs = release.gate_outputs("abc", release.load(read), release.load(read).packages, False,
                                       output=os.devnull)
        self.assertEqual((outputs["homebrew"], outputs["chocolatey"]), ("true", "true"))

    def test_the_tap_gets_a_branch_and_a_pull_request(self):
        sh(".", "git", "init", "-q", "--bare", "-b", "main", "tap.git")
        sh(".", "git", "clone", "-q", "tap.git", "tap")
        sh("tap", "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
        sh("tap", "git", "push", "-q", "origin", "HEAD:main")
        [formula] = release.render_channel("tool", "homebrew", self.SUMS, "out", repo="org/tool")
        prs = []
        url = release.update_tap("tap", formula, "org/homebrew-tap", "1.2.0", "https://example.invalid/r",
                                 open_pr=lambda *args: prs.append(args) or "https://example.invalid/pr/1")
        self.assertEqual(url, "https://example.invalid/pr/1")
        self.assertEqual(prs[0][:4], ("org/homebrew-tap", "tool-1.2.0", "main", "tool 1.2.0"))
        self.assertIn("class Tool", sh(".", "git", "--git-dir=tap.git", "show", "tool-1.2.0:Formula/tool.rb"))
        # Once the tap has it, nothing is pushed again.
        sh("tap", "git", "checkout", "-q", "main")
        sh("tap", "git", "merge", "-q", "--ff-only", "tool-1.2.0")
        self.assertIsNone(release.update_tap("tap", formula, "org/homebrew-tap", "1.2.0", "u",
                                             open_pr=lambda *a: self.fail("no pull request")))


class BashEverywhere(unittest.TestCase):
    """Commands run as `bash -c`, never through `shell=True` (on Windows that
    builds `<shell> /c "..."`, which bash cannot read)."""

    def test_commands_are_passed_to_bash_as_arguments(self):
        from unittest import mock
        with mock.patch.object(release.subprocess, "run") as ran:
            release.run('echo "$VERSION"', shell=True, env={"VERSION": "1"})
        args, kwargs = ran.call_args
        self.assertEqual(args[0][1:], ["-c", 'echo "$VERSION"'])
        self.assertTrue(args[0][0].endswith(("bash", "bash.exe")))
        self.assertNotIn("shell", kwargs)

    def test_a_command_really_runs(self):
        with tempfile.TemporaryDirectory() as d:
            release.run(f'echo "$GREETING" > "{d}/out"', shell=True, env={"GREETING": "hi"})
            self.assertEqual((Path(d) / "out").read_text().strip(), "hi")


class DraftReleases(unittest.TestCase):
    """A GitHub release counts as published only once it is out of draft
    with its files: an interrupted upload is finished by the next run."""

    package = release.Package("tool", "github", "Cargo.toml", "1.0.0", "v{version}")

    def status(self, answer):
        from unittest import mock
        with mock.patch.object(release, "http_status", return_value=answer), \
                mock.patch.object(release, "repository", return_value="org/tool"):
            return release.published_on(self.package)

    def test_a_draft_or_an_empty_release_is_not_published(self):
        self.assertFalse(self.status((404, b"")), "the tags endpoint does not serve drafts")
        self.assertFalse(self.status((200, json.dumps({"draft": True, "assets": [{}]}).encode())))
        self.assertFalse(self.status((200, json.dumps({"draft": False, "assets": []}).encode())))
        self.assertTrue(self.status((200, json.dumps({"draft": False, "assets": [{}]}).encode())))


class Timeout(unittest.TestCase):
    """#8: running out of time while the run is live says how to go on."""

    def test_a_live_run_is_named_when_tag_stops_waiting(self):
        live = {"status": "in_progress", "conclusion": "", "url": "https://run", "head_sha": "x",
                "publish_started": True}
        package = release.Package("core", "crates.io", "m", "0.1.0")
        with self.assertRaisesRegex(RuntimeError, "still going .https://run.; run `release.py tag` again"):
            release.wait_for("release-0.1.0", [package], 0, status=lambda p: False, run=lambda t: live,
                             sleep=lambda s: None)


class Pinned(unittest.TestCase):
    """#5: every third-party action is pinned to a commit, with its version."""

    def test_every_uses_is_local_or_a_commit(self):
        import re
        kit = Path(__file__).resolve().parent.parent
        files = [*kit.glob("*/action.yml"), *kit.glob("workflows/*.yml"), *kit.glob("templates/*.yml"),
                 *kit.glob(".github/workflows/*.yml")]
        loose = []
        for file in files:
            for number, line in enumerate(file.read_text().splitlines(), 1):
                found = re.search(r"uses:\s*(\S+)", line)
                if not found or found[1].startswith("./"):
                    continue
                if not re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", found[1]) or "#" not in line:
                    loose.append(f"{file.relative_to(kit)}:{number}: {found[1]}")
        self.assertEqual(loose, [], "pin to a full commit SHA with a `# vX.Y.Z` comment")


class Install(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.dir.name) / "repo"
        self.repo.mkdir()
        sh(self.repo, "git", "init", "-q", "-b", "main")

    def tearDown(self):
        self.dir.cleanup()

    def test_install_copies_the_kit_and_a_starter_workflow(self):
        written = release.install(self.repo)
        for path in [".github/release-kit/scripts/release.py", ".github/release-kit/gate/action.yml",
                     ".github/release-kit/plan/action.yml", ".github/release-kit/build/action.yml",
                     ".github/release-kit/publish-github/action.yml", ".github/workflows/release-kit-build.yml",
                     ".github/workflows/release.yml", ".github/release-kit/manifest.json"]:
            self.assertIn(path, written)
            self.assertTrue((self.repo / path).is_file(), path)
        manifest = json.loads((self.repo / ".github/release-kit/manifest.json").read_text())
        self.assertEqual(manifest["source"], "buchochelliq-labs/release-kit")
        self.assertEqual(release.vendored(self.repo), [])
        # Everything is used by local path: public repositories can run it.
        workflow = (self.repo / ".github/workflows/release.yml").read_text()
        self.assertIn("uses: ./.github/release-kit/gate", workflow)
        self.assertNotIn("buchochelliq-labs/release-kit/", workflow)

    def test_edits_and_additions_to_the_copy_are_caught(self):
        release.install(self.repo)
        gate = self.repo / ".github/release-kit/gate/action.yml"
        gate.write_text(gate.read_text() + "# local tweak\n")
        (self.repo / ".github/release-kit/extra.sh").write_text("echo\n")
        problems = "\n".join(release.vendored(self.repo))
        self.assertIn("gate/action.yml was edited", problems)
        self.assertIn("extra.sh is not part of release-kit", problems)

    def test_installing_again_replaces_the_copy_and_keeps_the_release_workflow(self):
        release.install(self.repo)
        (self.repo / ".github/release-kit/extra.sh").write_text("echo\n")
        own = self.repo / ".github/workflows/release.yml"
        own.write_text(own.read_text() + "# this repository's own changes\n")
        release.install(self.repo)
        self.assertEqual(release.vendored(self.repo), [])
        self.assertIn("this repository's own changes", own.read_text())

    def test_install_needs_a_release_kit_checkout(self):
        release.install(self.repo)
        with self.assertRaisesRegex(RuntimeError, "release-kit checkout"):
            release.install(Path(self.dir.name) / "other", kit=self.repo / ".github/release-kit")
        self.assertEqual(release.vendored(Path(self.dir.name)), [
            ".github/release-kit/manifest.json is missing: install release-kit (release.py install)"])


class Sync(unittest.TestCase):
    def test_sync_rewrites_versions_and_keeps_comments(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            for path, text in FILES.items():
                (base / path).parent.mkdir(parents=True, exist_ok=True)
                (base / path).write_text(text)
            (base / "release-kit.yml").write_text("# keep me\n" + CONFIG)
            (base / "crates/tool/Cargo.toml").write_text(FILES["crates/tool/Cargo.toml"].replace("0.2.0", "0.2.1"))
            changes = release.sync(path=base / "release-kit.yml")
            self.assertEqual(changes, ["tool: 0.2.0 -> 0.2.1"])
            text = (base / "release-kit.yml").read_text()
            self.assertTrue(text.startswith("# keep me") and 'version: "0.2.1"' in text)


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "crates-io"
HAS_CARGO = bool(release.shutil.which("cargo"))


def crates_io(answers):
    """A fake crates.io from a fixture's crates-io.json: `index` maps a crate to
    its versions (the sparse index), `crates` maps a crate to its `repository`
    and `owners` (the API). Anything else is not on crates.io."""
    def fetch(url):
        if url.startswith("https://index.crates.io/"):
            name = url.rsplit("/", 1)[1]
            versions = answers.get("index", {}).get(name)
            if versions is None:
                return 404, b""
            return 200, "\n".join(json.dumps({"name": name, "vers": v, "yanked": False}) for v in versions).encode()
        name = url.split("/api/v1/crates/")[1].split("/")[0]
        crate = answers.get("crates", {}).get(name)
        if crate is None:
            return 404, b""
        if url.endswith("/owners"):
            return 200, json.dumps({"users": [{"login": o} for o in crate["owners"]]}).encode()
        return 200, json.dumps({"crate": {"name": crate.get("name", name),
                                          "repository": crate.get("repository")}}).encode()
    return fetch


@unittest.skipUnless(HAS_CARGO, "needs cargo")
class CratesIoReadiness(unittest.TestCase):
    """#11: a crate crates.io would refuse is refused by `check`, with the fix,
    before the tag. One fixture repository per case, under tests/fixtures."""

    def fixture(self, name, config=None):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        base = Path(self.dir.name) / name
        release.shutil.copytree(FIXTURES / name, base)
        if config:
            (base / "release-kit.yml").write_text((base / config).read_text())
        self.answers = json.loads((base / "crates-io.json").read_text())
        return base

    def readiness(self, base, repo="acme/ontology", **kw):
        read = release.files_on_disk(base)
        self.assertEqual(release.check(read), [], "release-kit.yml itself is fine")
        kw.setdefault("fetch", crates_io(self.answers))
        return release.crates_io_readiness(release.load(read), base, repo=repo, offline_mode=False, **kw)

    def test_the_ready_repository_passes(self):
        # A path dependency with a version, an embed the crate carries, and an
        # embed from outside it in test code only, which cargo publish does
        # not build. onto-core is owned by a member of acme, and names acme's
        # repository; onto-cli is new.
        self.assertEqual(self.readiness(self.fixture("ready")), ([], []))

    def test_publish_false_or_another_registry_is_refused(self):
        errors, _ = self.readiness(self.fixture("publish-false"))
        self.assertEqual(errors, [
            "onto-core: crates/onto-core/Cargo.toml publishes only to internal (`publish = [...]`); "
            "add \"crates-io\" to the list, or remove `publish`",
            "onto: crates/onto/Cargo.toml says `publish = false`, so cargo will not publish it; remove that line "
            "(or take the package out of release-kit.yml to keep the crate private)"])

    def test_a_git_dependency_without_a_version_is_refused(self):
        errors, _ = self.readiness(self.fixture("git-dependency"))
        self.assertEqual(len(errors), 1)
        self.assertIn("onto-ods: depends on ods-erd from git (https://github.com/opendatasuite/open-data-suite) "
                      "with no version, and crates.io refuses git dependencies. Publish ods-erd to crates.io first, "
                      "then give the dependency a `version`", errors[0])

    def test_a_path_dependency_without_a_version_is_refused(self):
        errors, _ = self.readiness(self.fixture("path-without-version"))
        self.assertEqual(errors, ["onto-cli: depends on onto-core by path with no version; crates.io needs one: "
                                  "onto-core = { path = \"../onto-core\", version = \"0.1.0\" }"])

    def test_a_dependency_must_be_on_crates_io_or_released_first(self):
        base = self.fixture("unpublished-dependency")
        errors, warnings = self.readiness(base)
        self.assertEqual(errors, [
            "onto-ods: depends on ods-erd ^0.3, which is not on crates.io; publish ods-erd first (from its own "
            "repository), or list it in release-kit.yml (registry: crates.io) before onto-ods"])
        self.assertIn("library crates release-kit.yml does not publish: ods-erd.", warnings[0])
        self.answers["index"] = {"ods-erd": ["0.2.0", "0.4.0"]}
        errors, _ = self.readiness(base)
        self.assertIn("onto-ods: depends on ods-erd ^0.3, but crates.io has no matching version (newest: 0.4.0)",
                      errors[0])
        self.answers["index"] = {"ods-erd": ["0.2.0", "0.3.1"]}
        self.assertEqual(self.readiness(base)[0], [])
        # Or this release publishes it first.
        self.answers["index"] = {}
        config = (base / "release-kit.yml").read_text()
        (base / "release-kit.yml").write_text(config.replace("packages:\n", "packages:\n  - name: ods-erd\n"
                                              "    registry: crates.io\n    manifest: crates/ods-erd/Cargo.toml\n"
                                              "    version: \"0.3.0\"\n"))
        self.assertEqual(self.readiness(base), ([], []))

    def test_a_name_someone_else_owns_is_refused(self):
        errors, _ = self.readiness(self.fixture("taken-name"))
        self.assertEqual(len(errors), 1)
        self.assertIn("onto: the name is taken on crates.io (owners: someone; repository https://github.com/"
                      "someone/onto), and acme/ontology cannot publish it. Rename the package (e.g. onto-cli, "
                      "keeping the command with [[bin]] name = \"onto\")", errors[0])

    def test_an_embed_from_outside_the_crate_is_refused(self):
        errors, _ = self.readiness(self.fixture("outside-embed"))
        self.assertEqual(errors, [
            "onto-cli: src/main.rs embeds ../../../skills/onto.md, outside the crate; a package carries only its "
            "crate directory. Copy the file into the crate (and add a test that the copy matches the original)"])

    def test_library_crates_published_nowhere_are_a_warning(self):
        self.assertEqual(self.readiness(self.fixture("undeclared")), ([], [
            "library crates release-kit.yml does not publish: onto-core (publish = false), onto-ods. To publish "
            "one, list it with registry: crates.io; a crate meant to stay private needs nothing"]))

    def test_open_data_suite_ontology_before_its_48_is_refused_at_check(self):
        repo = "opendatasuite/open-data-suite-ontology"
        # As it was, only GitHub binaries: its library crates are pointed out.
        errors, warnings = self.readiness(self.fixture("ods-before-48"), repo=repo)
        self.assertEqual(errors, [])
        self.assertIn("does not publish: onto-core (publish = false), onto-ods (publish = false)", warnings[0])
        # With its crates declared for crates.io, every blocker #48 fixed by hand.
        errors = "\n".join(self.readiness(self.fixture("ods-before-48", "release-kit.crates-io.yml"),
                                          repo=repo)[0])
        for problem in ["onto-core: crates/onto-core/Cargo.toml says `publish = false`",
                        "onto-ods: depends on ods-erd from git",
                        "onto-ods: depends on onto-core by path with no version",
                        "onto: depends on onto-ods by path with no version",
                        "onto: the name is taken on crates.io",
                        "onto: src/main.rs embeds ../../../skills/onto.md, outside the crate"]:
            self.assertIn(problem, errors)

    def test_offline_nothing_asks_crates_io(self):
        def no_network(url):
            raise AssertionError(f"asked {url}")
        base = self.fixture("unpublished-dependency")
        read = release.files_on_disk(base)
        self.assertEqual(release.crates_io_readiness(release.load(read), base, fetch=no_network, repo="acme/o",
                                                     offline_mode=True)[0], [])
        base = self.fixture("git-dependency")
        read = release.files_on_disk(base)
        errors, _ = release.crates_io_readiness(release.load(read), base, fetch=no_network, repo="acme/o",
                                                offline_mode=True)
        self.assertIn("onto-ods: depends on ods-erd from git", errors[0])

    def test_the_check_command_refuses_with_the_fix(self):
        base = self.fixture("git-dependency")
        sh(base, "git", "init", "-q")
        cwd = os.getcwd()
        os.chdir(base)
        self.addCleanup(os.chdir, cwd)
        from contextlib import redirect_stderr
        import io
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(release.main(["check", "--offline"]), 1)
        self.assertIn("onto-ods: depends on ods-erd from git", err.getvalue())
        (base / "crates/onto-ods/Cargo.toml").write_text(
            (base / "crates/onto-ods/Cargo.toml").read_text().replace('ods-erd = { git', '# ods-erd = { git'))
        with redirect_stderr(io.StringIO()):
            self.assertEqual(release.main(["check", "--offline"]), 0)


class CratesIoReadinessParts(unittest.TestCase):
    def test_cargo_version_requirements(self):
        for requirement, version, matches in [
                ("0.3", "0.3.7", True), ("0.3", "0.4.0", False), ("^1.2", "1.9.0", True), ("^1.2", "2.0.0", False),
                ("^0.0.3", "0.0.4", False), ("~1.2.3", "1.2.9", True), ("~1.2.3", "1.3.0", False),
                ("=1.2.3", "1.2.4", False), ("1.*", "1.5.0", True), (">=1, <2", "2.0.0", False),
                (">1.2", "1.2.9", False), ("<=1.2", "1.2.9", True), ("*", "3.0.0", True),
                ("^1.0.0", "1.1.0-rc.1", False), ("^1.1.0-rc.1", "1.1.0-rc.2", True)]:
            self.assertEqual(release.requirement_matches(requirement, version), matches, (requirement, version))

    def test_the_sparse_index_path(self):
        self.assertEqual([release.index_url(n) for n in ("a", "ab", "abc", "Serde")],
                         ["https://index.crates.io/1/a", "https://index.crates.io/2/ab",
                          "https://index.crates.io/3/a/abc", "https://index.crates.io/se/rd/serde"])

    def test_who_may_publish_a_name(self):
        def owner(crate, repo="acme/onto"):
            return release.owner_problem("onto", repo, crates_io({"crates": {"onto": crate}}))
        self.assertEqual(release.owner_problem("onto", "acme/onto", crates_io({})), (None, None), "a free name")
        self.assertEqual(owner({"owners": ["Acme"]}), (None, None), "the repository's owner, a user")
        self.assertEqual(owner({"owners": ["someone", "github:acme:release"]}), (None, None), "a team of the org")
        self.assertEqual(owner({"owners": ["member"], "repository": "https://github.com/ACME/onto.git"}),
                         (None, None), "a member's crate that names a repository of the org")
        self.assertEqual(owner({"owners": ["someone"], "repository": "https://github.com/someone/onto"})[0], "error")
        kind, message = owner({"owners": ["someone"]})
        self.assertEqual(kind, "warning", "nothing says whose it is")
        self.assertIn("cannot tell whether acme/onto may publish it", message)
        kind, message = owner({"name": "Onto", "owners": ["acme"]})
        self.assertEqual(kind, "error")
        self.assertIn("crates.io has the crate Onto", message)

    def test_test_code_is_not_scanned_for_embeds(self):
        source = ('const A: &str = include_str!("a.txt");\n#[cfg(test)]\nmod tests {\n    fn f() { let _ = '
                  'include_str!("../../x"); }\n}\n#[test]\nfn g() { include_bytes!("../y"); }\n'
                  'const B: &[u8] = include_bytes!("b.bin");\n')
        self.assertEqual(release.EMBED.findall(release.without_tests(source)), [("", "a.txt"), ("", "b.bin")])

    def test_without_cargo_the_checks_are_skipped_with_a_warning(self):
        which = release.shutil.which
        release.shutil.which = lambda name: None
        self.addCleanup(setattr, release.shutil, "which", which)
        errors, warnings = release.crates_io_readiness(release.load(reader(FILES)), Path("."), repo="acme/o")
        self.assertEqual(errors, [])
        self.assertIn("cargo is not installed", warnings[0])

    def test_preflight_refuses_a_crate_that_is_not_ready(self):
        selection = json.dumps([{"name": "core", "registry": "crates.io", "version": "0.1.0",
                                 "manifest": "crates/core/Cargo.toml", "tag": "core-v0.1.0"}])
        asked = []

        def ready(names):
            asked.append(names)
            return ["core: depends on x by path with no version"], []
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            out.write_text("")
            with self.assertRaisesRegex(RuntimeError, "not ready for crates.io:\n  core: depends on x"):
                release.preflight("crates.io", selection, status=lambda p: False, exists=lambda n: True,
                                  output=out, ready=ready)
            self.assertEqual((asked, out.read_text()), ([{"core"}], ""), "no outputs, so nothing publishes")

    def test_the_publish_dry_run_covers_every_crate_of_a_workspace(self):
        calls = []
        base = Path("/repo")

        def cargo(args, cwd):
            calls.append((args, cwd))
            # Both crates are members of the workspace at the root.
            return subprocess.CompletedProcess(args, 0, stdout="/repo/Cargo.toml\n", stderr="")
        self.assertIsNone(release.publish_dry_run(release.load(reader(FILES)), base, runner=cargo))
        self.assertEqual(calls[-1], (["publish", "--dry-run", "--locked", "--allow-dirty",
                                      "-p", "core", "-p", "tool"], base))
        self.assertEqual(len(calls), 3, "one locate-project per crate, one dry run per workspace")


if __name__ == "__main__":
    unittest.main()
