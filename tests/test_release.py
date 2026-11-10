"""release.py: rab.yml checks, artifacts, tagging and the CI gate, on real git
repositories in temporary directories. Registries and runs are faked."""

import io
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import unittest.mock

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
    "rab.yml": CONFIG,
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
            "rab.yml": """
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
        files["rab.yml"] = CONFIG.replace('version: "0.2.0"', 'version: "0.3.0"').replace(
            "name: core", "name: kore", 1)
        errors = "\n".join(release.check(reader(files)))
        self.assertIn("tool: rab.yml says 0.3.0, crates/tool/Cargo.toml says 0.2.0", errors)
        self.assertIn("kore: crates/core/Cargo.toml names the package 'core'", errors)

    def test_a_package_comes_after_what_it_depends_on(self):
        files = dict(FILES)
        head, core, tool = CONFIG.split("  - name: ")
        files["rab.yml"] = head + "  - name: " + tool.rstrip() + "\n  - name: " + core
        self.assertIn("tool is listed before core, which it depends on", release.check(reader(files)))

    def test_a_versioned_dev_dependency_counts_for_the_order(self):
        # cargo keeps it in the published manifest, so crates.io needs it first
        files = dict(FILES)
        files["Cargo.toml"] += '[workspace.dependencies]\ncore = { path = "crates/core", version = "0.1.0" }\n'
        files["crates/core/Cargo.toml"] += '[dev-dependencies]\ntool.path = "../tool"\n'
        files["crates/tool/Cargo.toml"] = ('[package]\nname = "tool"\nversion = "0.2.0"\n'
                                           '[dev-dependencies]\ncore.workspace = true\n')
        self.assertEqual(release.check(reader(files)), [], "a path-only dev-dependency is stripped")
        head, core, tool = CONFIG.split("  - name: ")
        files["rab.yml"] = head + "  - name: " + tool.rstrip() + "\n  - name: " + core
        self.assertIn("tool is listed before core, which it depends on", release.check(reader(files)))
        files["crates/tool/Cargo.toml"] = files["crates/tool/Cargo.toml"].replace(
            "core.workspace = true", 'core = { path = "../core", version = "0.1" }')
        self.assertIn("tool is listed before core, which it depends on", release.check(reader(files)))

    def test_inherited_dev_dependencies_come_from_the_crates_own_workspace(self):
        workspace = ('[workspace]\nmembers = ["*"]\n[workspace.package]\nversion = "0.1.0"\n'
                     '[workspace.dependencies]\ncore = { path = "core", version = "0.1.0" }\n')
        tool = ('[package]\nname = "tool"\nversion = "0.2.0"\n'
                '[dev-dependencies]\ncore.workspace = true\n')
        config = CONFIG.replace("crates/", "rust/")
        head, core, tool_entry = config.split("  - name: ")
        swapped = head + "  - name: " + tool_entry.rstrip() + "\n  - name: " + core
        # A workspace below the repository root, and no Cargo.toml at the root.
        files = {"rust/Cargo.toml": workspace, "rust/core/Cargo.toml": FILES["crates/core/Cargo.toml"],
                 "rust/tool/Cargo.toml": tool, "rab.yml": config}
        self.assertEqual(release.check(reader(files)), [])
        files["rab.yml"] = swapped
        self.assertIn("tool is listed before core, which it depends on", release.check(reader(files)))
        # A root Cargo.toml of another workspace doesn't hide the crate's own.
        files["Cargo.toml"] = '[workspace]\nmembers = ["other"]\n'
        self.assertIn("tool is listed before core, which it depends on", release.check(reader(files)))
        # `package.workspace` names the root explicitly.
        files["ws/Cargo.toml"] = workspace.replace('path = "core"', 'path = "../rust/core"')
        files["rust/Cargo.toml"] = '[workspace]\nmembers = []\n'
        files["rust/tool/Cargo.toml"] = tool.replace('version = "0.2.0"\n', 'version = "0.2.0"\nworkspace = "../../ws"\n', 1)
        self.assertIn("tool is listed before core, which it depends on", release.check(reader(files)))

    def test_record_tags_are_distinct_from_each_other_and_the_release_tag(self):
        files = dict(FILES)
        files["rab.yml"] = CONFIG.replace('version: "0.2.0"\n', 'version: "0.2.0"\n    tag: "release-{version}"\n')
        self.assertTrue(any("is the release tag" in e for e in release.check(reader(files))))

    def test_unknown_registry_and_unquoted_versions(self):
        files = dict(FILES)
        files["rab.yml"] = CONFIG.replace("registry: crates.io", "registry: maven", 1)
        self.assertTrue(any("unknown registry 'maven'" in e for e in release.check(reader(files))))
        files["rab.yml"] = CONFIG.replace('version: "0.1.0"', "version: 0.10")
        self.assertTrue(any("quote the version" in e for e in release.check(reader(files))))


class Artifacts(unittest.TestCase):
    def config(self, package):
        files = {"bin/Cargo.toml": '[package]\nname = "app"\nversion = "1.0.0"\n',
                 "rab.yml": "packages:\n  - " + package.strip().replace("\n", "\n    ") + "\n"}
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
                 "rab.yml": 'packages:\n  - name: pkg\n    registry: pypi\n    manifest: py/pyproject.toml\n'
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
                 "rab.yml": 'packages:\n  - name: pkg\n    registry: pypi\n    manifest: py/pyproject.toml\n'
                                    f'    version: "1.0"\n    artifacts: [{artifact}]\n'}
        return reader(files)

    def job(self, data, **extra):
        import hashlib
        return {"id": "pkg-ci-artifact-0", "kind": "ci-artifact", "package": "pkg", "slug": "pkg",
                "version": "1.0", "registry": "pypi", "artifact": 42,
                "sha256": hashlib.sha256(data).hexdigest(), "files": "dist/*.whl", "test": None, **extra}

    def github(self, data, meta=None, compare=("ahead", ["rab.yml"])):
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
        # that records it, changing only rab.yml, is the same release.
        data = self.archive({self.WHEEL: b"reviewed wheel"})
        for compare in (("identical", []), ("ahead", ["rab.yml"])):
            self.assertEqual(self.promote(data, compare=compare), [("pkg-1.0-py3-none-any.whl", b"reviewed wheel")])

    def test_promotion_fails_closed(self):
        data = self.archive({self.WHEEL: b"reviewed wheel"})
        cases = [
            ("the archive's SHA-256", dict(job=self.job(data, sha256="0" * 64), meta={"digest": None})),
            ("GitHub records", dict(meta={"digest": "sha256:" + "1" * 64})),
            ("has expired", dict(meta={"expired": True})),
            ("does not descend from", dict(compare=("diverged", ["rab.yml"]))),
            ("src/app.py changed", dict(compare=("ahead", ["rab.yml", "src/app.py"]))),
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

    def test_a_tagged_commit_that_fails_the_check_is_finished_from_head(self):
        # As open-data-suite 0.1.3: tagged before its rab.yml was fixed; the
        # run there stopped; HEAD fixes rab.yml and ships the same files.
        first = sh(self.work, "git", "rev-parse", "HEAD")
        self.tag()
        self.write({"rab.yml": CONFIG + "# fixed on a later commit\n"})
        later = self.commit("fix rab.yml")
        stopped = {"status": "completed", "conclusion": "failure", "url": "u", "head_sha": first,
                   "publish_started": True}
        broken = lambda read: [] if "# fixed" in read("rab.yml") else ["tool is listed before core"]
        with mock.patch.object(release, "check", broken):
            with self.assertRaisesRegex(RuntimeError, "not when the commit was asked for|disagrees") as asked:
                self.tag(commit=first, run=lambda tag: stopped)
            self.assertNotIn("--commit", str(asked.exception))
            self.tag(run=lambda tag: stopped)
        tags = self.remote_tags()
        self.assertEqual({tags[t] for t in ("core-v0.1.0", "tool-v0.2.0", "release-0.2.0")}, {later})
        self.assertEqual(release.trailers("release-0.2.0").get("Carried-from"), first)

    def test_a_tagged_commit_that_head_does_not_fix_points_at_head(self):
        first = sh(self.work, "git", "rev-parse", "HEAD")
        self.tag()
        self.write({"README.md": "later\n"})
        later = self.commit("docs")
        with mock.patch.object(release, "check", lambda read: ["tool is listed before core"]):
            with self.assertRaisesRegex(RuntimeError, rf"(?s)at {first[:12]} disagrees.*"
                                                      rf"release.py tag --commit {later[:12]}"):
                self.tag()

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

    def test_a_published_package_tagged_before_rab_is_skipped(self):
        sh(self.work, "git", "rm", "-q", "rab.yml")
        old = self.commit("before rab")
        sh(self.work, "git", "tag", "-a", "core-v0.1.0", "-m", "core 0.1.0 (crates.io)", old)
        sh(self.work, "git", "push", "-q", "origin", "refs/tags/core-v0.1.0")
        self.write(FILES)
        later = self.commit("adopt rab")
        self.published.add(("core", "0.1.0"))
        self.tag()
        tags = self.remote_tags()
        self.assertEqual((tags["core-v0.1.0"], tags["tool-v0.2.0"], tags["release-0.2.0"]), (old, later, later))

    def test_a_commit_without_release_kit_yml_is_named(self):
        sh(self.work, "git", "rm", "-q", "rab.yml")
        old = self.commit("before release-kit")
        with self.assertRaisesRegex(RuntimeError, "core-v0.1.0 is on .*which has no rab.yml \(nor release-kit.yml\): the tag predates"):
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
                 "rab.yml": 'packages:\n  - name: pkg\n    registry: pypi\n    manifest: py/pyproject.toml\n'
                                    '    version: "1.0"\n    artifacts:\n      - kind: maturin\n'
                                    '        targets: [x86_64-unknown-linux-gnu, x86_64-apple-darwin]\n'
                                    '        test: {run: "pytest", targets: [x86_64-unknown-linux-gnu]}\n'}
        self.assertEqual(release.check(reader(files)), [])
        jobs = release.plan(json.dumps([{"name": "pkg", "registry": "pypi"}]), read=reader(files))
        self.assertEqual({j["target"]: j["test"] for j in jobs},
                         {"x86_64-unknown-linux-gnu": "pytest", "x86_64-apple-darwin": None, "": None})

    def test_a_malformed_test_is_refused(self):
        files = {"bin/Cargo.toml": '[package]\nname = "app"\nversion = "1.0.0"\n',
                 "rab.yml": 'packages:\n  - name: app\n    registry: github\n    manifest: bin/Cargo.toml\n'
                                    '    version: "1.0.0"\n    artifacts: [{kind: rust-binary, bin: app, test: {cmd: x}}]\n'}
        self.assertTrue(any("test is a command" in e for e in release.check(reader(files))))
        files["rab.yml"] = files["rab.yml"].replace(
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
        "rab.yml": 'packages:\n  - name: tool\n    registry: pypi\n    manifest: pyproject.toml\n'
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
        files = {**self.FILES, "rab.yml": self.FILES["rab.yml"].replace("./notices.sh", "''")}
        self.assertTrue(any("prepare is a command" in e for e in release.check(reader(files))))


class GitHubReleaseOptions(unittest.TestCase):
    """A github package's notes, checksums file and attestations."""

    def config(self, options):
        return reader({
            "bin/Cargo.toml": '[package]\nname = "app"\nversion = "1.0.0"\n',
            "rab.yml": 'packages:\n  - name: app\n    registry: github\n    manifest: bin/Cargo.toml\n'
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
                        "rab.yml": 'packages:\n  - name: c\n    registry: crates.io\n'
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
            "rab.yml": self.CONFIG,
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
            Path("rab.yml").write_text(self.CONFIG.replace(
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


class OpenVsx(unittest.TestCase):
    """#17: with `open-vsx: true`, Open VSX is part of a vscode package's
    publication: due until both registries have it, uploaded only where it is
    missing, verified on both. Registries are faked at the HTTP layer."""

    selection = json.dumps([{"name": "org.ext", "registry": "vscode", "version": "1.0.0",
                             "manifest": "ext/package.json", "tag": "org.ext-v1.0.0", "slug": "org.ext",
                             "open-vsx": True}])
    OPEN_VSX = "https://open-vsx.org/api/org/ext/1.0.0"

    def http(self, marketplace, open_vsx, calls=None):
        """A fake http_status: the Marketplace lists 1.0.0 when `marketplace`,
        Open VSX answers 200 for it when `open_vsx`."""
        def status(url, data=None, headers=None):
            if calls is not None:
                calls.append(url)
            if url.startswith("https://marketplace.visualstudio.com/"):
                self.assertEqual(json.loads(data)["filters"][0]["criteria"][0]["value"], "org.ext")
                versions = [{"version": "1.0.0"}] if marketplace() else [{"version": "0.9.0"}]
                return 200, json.dumps({"results": [{"extensions": [{"versions": versions}]}]}).encode()
            self.assertEqual(url, self.OPEN_VSX)
            return (200, b"{}") if open_vsx() else (404, b"")
        from unittest import mock
        return mock.patch.object(release, "http_status", side_effect=status)

    def preflight(self, marketplace, open_vsx, token=True):
        with tempfile.TemporaryDirectory() as d, self.http(lambda: marketplace, lambda: open_vsx):
            out = Path(d) / "out"
            out.write_text("")
            release.preflight("vscode", self.selection, output=out, ovsx_token=token)
            outputs = dict(line.split("=", 1) for line in out.read_text().splitlines())
        return json.loads(outputs["uploads"])

    def test_it_is_checked_and_only_for_vscode(self):
        files = {"ext/package.json": json.dumps({"name": "ext", "publisher": "org", "version": "1.0.0"}),
                 "rab.yml": "packages:\n  - name: org.ext\n    registry: vscode\n"
                                    "    manifest: ext/package.json\n    version: \"1.0.0\"\n"
                                    "    artifacts: [{kind: vsix}]\n    open-vsx: true\n"}
        self.assertEqual(release.check(reader(files)), [])
        self.assertIs(release.load(reader(files)).packages[0].open_vsx, True)
        files["rab.yml"] = files["rab.yml"].replace("open-vsx: true", "open-vsx: yes please")
        self.assertIn("org.ext: open-vsx is true or false", release.check(reader(files)))
        files = dict(FILES, **{"rab.yml": CONFIG + "    open-vsx: true\n"})
        self.assertIn("tool: open-vsx only applies to a vscode package", release.check(reader(files)))

    def test_the_gate_carries_it_in_the_selection(self):
        package = release.Package("org.ext", "vscode", "ext/package.json", "1.0.0", open_vsx=True)
        plain = release.Package("org.other", "vscode", "o/package.json", "1.0.0")
        release_ = release.Release((package, plain), "org.ext", "release-{release}", "main")
        with tempfile.TemporaryDirectory() as d:
            selection = release.gate_outputs("0" * 40, release_, [package, plain], False, Path(d) / "out")["selection"]
        self.assertEqual([p.open_vsx for p in release.selected("vscode", selection)], [True, False])

    def test_both_missing_is_due_and_uploads_to_both(self):
        with self.http(lambda: False, lambda: False):
            self.assertFalse(release.published_on(release.selected("vscode", self.selection)[0]))
        self.assertEqual(self.preflight(False, False),
                         [{"name": "org.ext", "version": "1.0.0", "marketplace": True, "open-vsx": True}])

    def test_only_open_vsx_missing_is_due_and_uploads_only_there(self):
        with self.http(lambda: True, lambda: False):
            self.assertFalse(release.published_on(release.selected("vscode", self.selection)[0]))
        self.assertEqual(self.preflight(True, False),
                         [{"name": "org.ext", "version": "1.0.0", "marketplace": False, "open-vsx": True}])

    def test_only_the_marketplace_missing_uploads_only_there_without_a_token(self):
        self.assertEqual(self.preflight(False, True, token=False),
                         [{"name": "org.ext", "version": "1.0.0", "marketplace": True, "open-vsx": False}])

    def test_both_present_is_published_and_refused(self):
        with self.http(lambda: True, lambda: True):
            self.assertTrue(release.published_on(release.selected("vscode", self.selection)[0]))
        with self.assertRaisesRegex(RuntimeError, "already on vscode: org.ext 1.0.0"):
            self.preflight(True, True)

    def test_an_open_vsx_upload_needs_its_token(self):
        with self.assertRaisesRegex(RuntimeError, "org.ext: rab.yml says open-vsx: true, but no Open VSX"):
            self.preflight(True, False, token=False)

    def test_verify_waits_for_open_vsx_too(self):
        answers, calls = iter([False, False, True]), []
        with self.http(lambda: True, lambda: next(answers), calls):
            release.verify("vscode", self.selection, sleep=lambda s: None)
        self.assertEqual(calls.count(self.OPEN_VSX), 3)

    def test_without_it_open_vsx_is_not_asked(self):
        selection = json.dumps([dict(json.loads(self.selection)[0], **{"open-vsx": False})])
        calls = []
        with self.http(lambda: True, lambda: False, calls):
            release.verify("vscode", selection, sleep=lambda s: None)
        self.assertNotIn(self.OPEN_VSX, calls)


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


class ReleaseRecord(unittest.TestCase):
    """#18: release-record.json, the run's record for a coordinator."""

    FILES = {
        "c/Cargo.toml": '[package]\nname = "core"\nversion = "0.1.0"\n',
        "t/Cargo.toml": '[package]\nname = "tool"\nversion = "0.2.0"\n',
        "py/pyproject.toml": '[project]\nname = "tool-py"\nversion = "0.2.0"\n',
        "rab.yml": (
            "packages:\n"
            "  - {name: core, registry: crates.io, manifest: c/Cargo.toml, version: '0.1.0'}\n"
            "  - {name: tool, registry: crates.io, manifest: t/Cargo.toml, version: '0.2.0'}\n"
            "  - name: tool-py\n    registry: pypi\n    manifest: py/pyproject.toml\n    version: '0.2.0'\n"
            "    artifacts: [{kind: python}]\n"
            "  - name: tool-bin\n    registry: github\n    manifest: t/Cargo.toml\n    version: '0.2.0'\n"
            "    tag: 'v{version}'\n    artifacts: [{kind: rust-binary, bin: tool}]\n"
            "    homebrew: {tap: org/homebrew-tap, formula: tool.rb.tmpl}\n"
            "release: {name_from: tool}\n"),
    }
    SHA = "a" * 40
    RUN = {"id": 42, "attempt": 1, "url": "https://github.com/org/repo/actions/runs/42"}
    JOBS = ("gate", "ci", "build", "publish-github", "publish-homebrew", "publish-chocolatey",
            "publish-crates", "publish-pypi", "publish-npm", "publish-vscode")

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.dist = Path(self.dir.name) / "dist"
        self.write("release--tool-py--tool-py-python", "tool_py-0.2.0-py3-none-any.whl", b"wheel")
        self.write("release--tool-py--tool-py-python", "tool_py-0.2.0.tar.gz", b"sdist")
        self.write("release--tool-bin--tool-bin-rust-binary-x86_64-unknown-linux-gnu",
                   "tool-v0.2.0-x86_64-unknown-linux-gnu.tar.gz", b"archive", sums=True)
        self.published = set()

    def tearDown(self):
        self.dir.cleanup()

    def write(self, artifact, name, data, sums=False):
        import hashlib
        folder = self.dist / artifact
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
        if sums:
            (folder / f"{name}.sha256").write_text(f"{hashlib.sha256(data).hexdigest()}  {name}\n")

    def selection(self, *names):
        packages = release.load(reader(self.FILES)).packages
        return json.dumps([{"name": p.name, "registry": p.registry, "version": p.version, "manifest": p.manifest,
                            "tag": p.tag, "slug": p.slug} for p in packages if p.name in names])

    def needs(self, **results):
        every = {job: "skipped" if job.startswith("publish") else "success" for job in self.JOBS}
        every.update({job.replace("_", "-"): result for job, result in results.items()})
        return json.dumps({job: {"result": result, "outputs": {}} for job, result in every.items()})

    def status(self, package):
        if package.name == "lost":
            raise RuntimeError("the registry did not answer")
        return package.name in self.published

    def record(self, selection, needs, verify_only=False, sha=SHA):
        document = release.record("release-0.2.0", sha, selection, verify_only, needs, self.dist,
                                  read=reader(self.FILES), status=self.status, repo="org/repo", run=self.RUN)
        self.assertEqual(release.schema_errors(document, release.RECORD_SCHEMA), [])
        try:
            import jsonschema
        except ImportError:
            pass
        else:
            schema = json.loads((Path(__file__).resolve().parent.parent / "docs" /
                                 "release-record.schema.json").read_text())
            jsonschema.validate(document, schema)
        return document

    def by_name(self, document):
        return {p["name"]: p for p in document["packages"]}

    def test_a_crate_does_not_take_the_files_of_a_same_named_python_package(self):
        # rs-rich-cli 0.0.25: crates.io rs-rich was recorded with PyPI rs-rich's wheels.
        files = dict(self.FILES)
        files["pc/Cargo.toml"] = '[package]\nname = "tool-py"\nversion = "0.2.0"\n'
        files["rab.yml"] = files["rab.yml"].replace(
            "  - name: tool-py\n",
            "  - {name: tool-py, registry: crates.io, manifest: pc/Cargo.toml, version: '0.2.0'}\n"
            "  - name: tool-py\n", 1)
        self.FILES = files
        document = self.record(self.selection("tool-py"),
                               self.needs(publish_crates="success", publish_pypi="success"))
        both = [p for p in document["packages"] if p["name"] == "tool-py"]
        self.assertEqual([(p["registry"], [f["name"] for f in p["files"]]) for p in both],
                         [("crates.io", []),
                          ("pypi", ["tool_py-0.2.0-py3-none-any.whl", "tool_py-0.2.0.tar.gz"])])

    def test_a_complete_run(self):
        import hashlib
        self.published.add("core")  # before the run: the gate does not select it
        document = self.record(self.selection("tool", "tool-py", "tool-bin"),
                               self.needs(publish_crates="success", publish_pypi="success",
                                          publish_github="success", publish_homebrew="failure"))
        self.assertEqual({k: document[k] for k in ("schema_version", "repository", "release", "tag", "commit",
                                                    "verify_only", "run", "result")},
                         {"schema_version": 1, "repository": "org/repo", "release": "0.2.0",
                          "tag": "release-0.2.0", "commit": self.SHA, "verify_only": False, "run": self.RUN,
                          "result": "partial"}, "the formula is due again (#20): `tag` finishes it")
        self.assertEqual(document["jobs"]["publish-npm"], "skipped")
        packages = self.by_name(document)
        self.assertEqual(list(packages), ["core", "tool", "tool-py", "tool-bin"], "rab.yml's order")
        self.assertEqual(packages["core"], {
            "name": "core", "registry": "crates.io", "version": "0.1.0", "tag": "core-v0.1.0", "selected": False,
            "outcome": "already-published", "job": None, "url": "https://crates.io/crates/core/0.1.0", "files": []})
        self.assertEqual((packages["tool"]["outcome"], packages["tool"]["job"], packages["tool"]["files"]),
                         ("published", "success", []), "crates publish from source")
        self.assertEqual([(f["name"], f["sha256"], f["size"]) for f in packages["tool-py"]["files"]],
                         [("tool_py-0.2.0-py3-none-any.whl", hashlib.sha256(b"wheel").hexdigest(), 5),
                          ("tool_py-0.2.0.tar.gz", hashlib.sha256(b"sdist").hexdigest(), 5)])
        self.assertEqual(packages["tool-py"]["url"], "https://pypi.org/project/tool-py/0.2.0/")
        bin_ = packages["tool-bin"]
        self.assertEqual((bin_["tag"], bin_["url"], [f["name"] for f in bin_["files"]], bin_["channels"]),
                         ("v0.2.0", "https://github.com/org/repo/releases/tag/v0.2.0",
                          ["tool-v0.2.0-x86_64-unknown-linux-gnu.tar.gz"], {"homebrew": "failure"}),
                         "the .sha256 is checked, not listed; a channel does not change the outcome")
        document = self.record(self.selection("tool", "tool-py", "tool-bin"),
                               self.needs(publish_crates="success", publish_pypi="success",
                                          publish_github="success", publish_homebrew="success"))
        self.assertEqual(document["result"], "complete")

    def test_a_run_that_failed_halfway_says_what_it_published(self):
        # publish-crates uploaded core, then failed on tool; the build of the
        # binary failed, so publish-github and publish-pypi never ran.
        self.published.add("core")
        document = self.record(self.selection("core", "tool", "tool-py", "tool-bin"),
                               self.needs(build="failure", publish_crates="failure"))
        packages = self.by_name(document)
        self.assertEqual({n: (p["outcome"], p["job"]) for n, p in packages.items()},
                         {"core": ("published", "failure"), "tool": ("failed", "failure"),
                          "tool-py": ("skipped", "skipped"), "tool-bin": ("skipped", "skipped")})
        self.assertEqual((packages["core"]["url"], packages["tool"]["url"]),
                         ("https://crates.io/crates/core/0.1.0", None))
        self.assertEqual(document["result"], "partial")

    def test_a_run_that_published_nothing_says_so(self):
        document = self.record(self.selection("core", "tool"), self.needs(ci="failure"))
        self.assertEqual([p["outcome"] for p in document["packages"] if p["selected"]], ["skipped", "skipped"])
        self.assertEqual(document["result"], "nothing-published")
        failed = self.record(self.selection("core", "tool"), self.needs(publish_crates="failure"))
        self.assertEqual([p["outcome"] for p in failed["packages"] if p["selected"]], ["failed", "failed"])
        self.assertEqual(failed["result"], "nothing-published")

    def test_a_published_package_whose_job_failed_is_not_complete(self):
        self.published.update({"core", "tool"})  # uploaded; consume failed
        document = self.record(self.selection("core", "tool"), self.needs(publish_crates="failure"))
        self.assertEqual([p["outcome"] for p in document["packages"] if p["selected"]], ["published", "published"])
        self.assertEqual(document["result"], "partial")

    def test_a_registry_that_does_not_answer_is_unknown(self):
        files = dict(self.FILES)
        files["rab.yml"] = files["rab.yml"].replace("name: core,", "name: lost,")
        files["c/Cargo.toml"] = files["c/Cargo.toml"].replace('"core"', '"lost"')
        self.FILES = files
        document = self.record(self.selection("lost"), self.needs(publish_crates="cancelled"))
        self.assertEqual((document["packages"][0]["outcome"], document["result"]), ("unknown", "unknown"))

    def test_a_verify_only_run(self):
        self.published.update({"tool", "tool-py"})
        document = self.record(self.selection("tool", "tool-py"),
                               self.needs(build="skipped", publish_crates="success", publish_pypi="success"),
                               verify_only=True)
        packages = self.by_name(document)
        self.assertTrue(document["verify_only"])
        self.assertEqual({n: (p["selected"], p["outcome"]) for n, p in packages.items()},
                         {"core": (False, "skipped"), "tool": (True, "already-published"),
                          "tool-py": (True, "already-published"), "tool-bin": (False, "skipped")},
                         "a verify-only run vouches only for what it verified")
        self.assertEqual(document["result"], "complete")
        failed = self.record(self.selection("tool"), self.needs(build="skipped", publish_crates="failure"),
                             verify_only=True)
        self.assertEqual((failed["packages"][1]["outcome"], failed["result"]),
                         ("already-published", "nothing-published"))

    def test_a_refused_gate_selects_nothing(self):
        document = self.record("", self.needs(gate="failure", ci="skipped", build="skipped"), sha="")
        self.assertEqual({p["outcome"] for p in document["packages"]}, {"skipped"})
        self.assertEqual((document["release"], document["result"]), ("0.2.0", "nothing-published"))

    def test_a_run_that_only_finishes_a_channel(self):
        # #20 with #18: tool-bin's release is out but its formula is not, so
        # the gate selected it for homebrew only; publish-github was skipped.
        self.published.update({"core", "tool", "tool-py", "tool-bin"})
        release_ = release.load(reader(self.FILES))
        bin_ = next(p for p in release_.packages if p.name == "tool-bin")
        with tempfile.TemporaryDirectory() as d:
            outputs = release.gate_outputs(self.SHA, release_, [bin_], False, Path(d) / "out", {bin_: ["homebrew"]})
        self.assertEqual((outputs["github"], outputs["homebrew"]), ("false", "true"))
        document = self.record(outputs["selection"], self.needs(publish_homebrew="success"))
        packages = self.by_name(document)
        self.assertEqual(packages["tool-bin"], {
            "name": "tool-bin", "registry": "github", "version": "0.2.0", "tag": "v0.2.0", "selected": True,
            "outcome": "already-published", "job": None, "url": "https://github.com/org/repo/releases/tag/v0.2.0",
            "files": [], "channels": {"homebrew": "success"}},
            "published before this run, which built and uploaded nothing for it")
        self.assertEqual({n: p["outcome"] for n, p in packages.items() if n != "tool-bin"},
                         {"core": "already-published", "tool": "already-published",
                          "tool-py": "already-published"})
        self.assertEqual(document["result"], "complete", "the run did what it set out to: the formula")
        failed = self.record(outputs["selection"], self.needs(publish_homebrew="failure"))
        self.assertEqual((self.by_name(failed)["tool-bin"]["outcome"], failed["result"]),
                         ("already-published", "nothing-published"))
        # A verify-only run runs no channel job: its channels do not count.
        verified = self.record(self.selection("tool-bin"), self.needs(build="skipped", publish_github="success"),
                               verify_only=True)
        self.assertEqual((self.by_name(verified)["tool-bin"]["channels"], verified["result"]),
                         ({"homebrew": "skipped"}, "complete"))

    def test_a_vscode_package_with_open_vsx_is_published_only_on_both(self):
        # #17 with #18: the record asks both registries for an extension with
        # open-vsx: true, and gives Open VSX's page too.
        self.FILES = {
            "ext/package.json": json.dumps({"name": "ext", "publisher": "org", "version": "1.0.0"}),
            "rab.yml": "packages:\n  - name: org.ext\n    registry: vscode\n"
                               "    manifest: ext/package.json\n    version: '1.0.0'\n"
                               "    artifacts: [{kind: vsix}]\n    open-vsx: true\n",
        }
        self.write("release--org.ext--org.ext-vsix", "ext-1.0.0.vsix", b"vsix")
        selection = json.dumps([{"name": "org.ext", "registry": "vscode", "version": "1.0.0",
                                 "manifest": "ext/package.json", "tag": "org.ext-v1.0.0", "slug": "org.ext",
                                 "open-vsx": True}])
        on = {"marketplace": True, "open-vsx": True}
        self.status = release.published_on  # the real check, over the faked registries below

        def http(url, data=None, headers=None):
            if url.startswith("https://marketplace.visualstudio.com/"):
                versions = [{"version": "1.0.0"}] if on["marketplace"] else []
                return 200, json.dumps({"results": [{"extensions": [{"versions": versions}]}]}).encode()
            self.assertEqual(url, "https://open-vsx.org/api/org/ext/1.0.0")
            return (200, b"{}") if on["open-vsx"] else (404, b"")

        with unittest.mock.patch.object(release, "http_status", side_effect=http):
            done = self.record(selection, self.needs(publish_vscode="success"))["packages"][0]
            self.assertEqual((done["outcome"], done["url"], done["open_vsx_url"], [f["name"] for f in done["files"]]),
                             ("published", "https://marketplace.visualstudio.com/items?itemName=org.ext",
                              "https://open-vsx.org/extension/org/ext/1.0.0", ["ext-1.0.0.vsix"]))
            # The Marketplace upload went through, then Open VSX's failed.
            on["open-vsx"] = False
            document = self.record(selection, self.needs(publish_vscode="failure"))
            half = document["packages"][0]
            self.assertEqual((half["outcome"], half["url"], half["open_vsx_url"], document["result"]),
                             ("failed", None, None, "nothing-published"),
                             "on the Marketplace alone it is not published: a re-run finishes Open VSX")
            on["open-vsx"] = True
            late = self.record(selection, self.needs(publish_vscode="failure"))
            self.assertEqual((late["packages"][0]["outcome"], late["result"]), ("published", "partial"))
            # Without open-vsx: true, Open VSX is neither asked nor recorded.
            self.FILES = dict(self.FILES, **{"rab.yml": self.FILES["rab.yml"].replace(
                "    open-vsx: true\n", "")})
            on["open-vsx"] = False
            plain = self.record(selection.replace(', "open-vsx": true', ""), self.needs(publish_vscode="failure"))
        self.assertEqual(plain["packages"][0]["outcome"], "published")
        self.assertNotIn("open_vsx_url", plain["packages"][0])

    def test_a_publish_job_the_record_job_does_not_need_is_an_error(self):
        needs = json.loads(self.needs())
        del needs["publish-crates"]
        with self.assertRaisesRegex(RuntimeError, "does not need publish-crates"):
            self.record(self.selection("tool"), json.dumps(needs))

    def test_a_file_that_breaks_its_sha256_is_an_error(self):
        folder = self.dist / "release--tool-bin--tool-bin-rust-binary-x86_64-unknown-linux-gnu"
        (folder / "tool-v0.2.0-x86_64-unknown-linux-gnu.tar.gz").write_bytes(b"changed")
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            self.record(self.selection("tool-bin"), self.needs(publish_github="success"))

    def test_the_schema_is_checked_and_published(self):
        published = Path(__file__).resolve().parent.parent / "docs" / "release-record.schema.json"
        self.assertEqual(json.loads(published.read_text()), release.RECORD_SCHEMA,
                         "regenerate it: release.py record --schema > docs/release-record.schema.json")
        document = self.record(self.selection("tool"), self.needs(publish_crates="success"))
        document["packages"][1]["outcome"] = "shipped"
        document["packages"][0]["files"] = [{"name": "x", "sha256": "abc", "size": -1}]
        del document["run"]
        errors = "\n".join(release.schema_errors(document, release.RECORD_SCHEMA))
        for message in ("record: run is missing", "packages[1].outcome: 'shipped' is not one of",
                        "files[0].sha256: 'abc' does not match", "files[0].size: -1 is below 0"):
            self.assertIn(message, errors)
        self.assertEqual(release.schema_errors(True, {"type": "integer"}), ["record: True is not integer"])

    def test_the_template_records_every_run_without_new_permissions(self):
        import yaml
        template = yaml.safe_load((Path(__file__).resolve().parent.parent / "templates" / "release.yml").read_text())
        job = template["jobs"]["record"]
        self.assertEqual(job["if"], "always()")
        self.assertNotIn("permissions", job, "the workflow's contents: read is enough")
        self.assertEqual(set(job["needs"]), set(template["jobs"]) - {"record"}, "every job, every publish job")
        self.assertTrue(set(release.PUBLISH_JOBS.values()) | set(release.CHANNEL_JOBS.values())
                        <= set(job["needs"]))
        upload = job["steps"][-1]
        self.assertEqual((upload["with"]["name"], upload["with"]["path"]), ("release-record", "release-record.json"))

    def test_the_command_writes_the_file(self):
        out = Path(self.dir.name) / "release-record.json"
        with unittest.mock.patch.object(release, "files_at", lambda sha: reader(self.FILES)), \
                unittest.mock.patch.object(release, "published_on", self.status), \
                unittest.mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "org/repo", "GITHUB_RUN_ID": "7",
                                                      "GITHUB_RUN_ATTEMPT": "2", "GITHUB_STEP_SUMMARY": ""}):
            code = release.main(["record", "--tag", "release-0.2.0", "--sha", self.SHA,
                                 "--selection", self.selection("tool"), "--verify-only", "false",
                                 "--needs", self.needs(publish_crates="success"), "--dist", str(self.dist),
                                 "--out", str(out)])
        self.assertEqual(code, 0)
        document = json.loads(out.read_text())
        self.assertEqual((document["result"], document["run"]),
                         ("complete", {"id": 7, "attempt": 2, "url": "https://github.com/org/repo/actions/runs/7"}))


KIT = Path(__file__).resolve().parent.parent


def kit_yaml(path):
    import yaml
    return yaml.safe_load((KIT / path).read_text())


def every_step(document):
    for job in (document.get("jobs") or {}).values():
        yield from job.get("steps") or []
    yield from (document.get("runs") or {}).get("steps") or []


class ReleaseRuns(unittest.TestCase):
    """A release run builds the commit the gate checked, and writes no cache
    (GitHub's cache is shared with the default branch: CodeQL's
    actions/cache-poisoning queries)."""

    def test_builds_check_out_a_record_tag_and_stop_unless_it_names_the_gated_commit(self):
        workflow = kit_yaml("workflows/rab-build.yml")
        for name, job in workflow["jobs"].items():
            steps = job["steps"]
            checkout = steps[0]
            self.assertTrue(checkout["uses"].startswith("actions/checkout@"), name)
            self.assertTrue(checkout["with"]["ref"].startswith("refs/tags/${{"), name)
            self.assertEqual(checkout["with"]["persist-credentials"], False, name)
            # The check runs next, before anything from the checkout does.
            check = steps[1]
            self.assertNotIn("uses", check, name)
            self.assertEqual(check["env"]["SHA"], "${{ inputs.sha }}", name)
            self.assertEqual(checkout["with"]["ref"], "refs/tags/" + check["env"]["TAG"], name)
        self.assertNotIn("ref: ${{ inputs.sha }}", (KIT / "workflows/rab-build.yml").read_text())

    def test_the_check_refuses_a_tag_moved_since_the_gate(self):
        script = kit_yaml("workflows/rab-build.yml")["jobs"]["build"]["steps"][1]["run"]
        with tempfile.TemporaryDirectory() as d:
            sh(d, "git", "init", "-q", "-b", "main")
            sh(d, "git", "commit", "-q", "--allow-empty", "-m", "gated")
            gated = sh(d, "git", "rev-parse", "HEAD")
            sh(d, "git", "commit", "-q", "--allow-empty", "-m", "moved")

            def check(sha):
                return subprocess.run(["bash", "-c", script], cwd=d, text=True, capture_output=True,
                                      env={**os.environ, "TAG": "app-v1.0.0", "SHA": sha})
            moved = check(gated)
            self.assertEqual(moved.returncode, 1)
            self.assertIn("not " + gated, moved.stdout)
            self.assertEqual(check(sh(d, "git", "rev-parse", "HEAD")).returncode, 0)

    def test_the_plan_names_each_jobs_record_tag(self):
        files = {"py/pyproject.toml": '[project]\nname = "pkg"\nversion = "1.0"\n',
                 "rab.yml": 'packages:\n  - name: pkg\n    registry: pypi\n    manifest: py/pyproject.toml\n'
                                    '    version: "1.0"\n    tag: "python-v{version}"\n'
                                    '    artifacts: [{kind: python}]\n'}
        jobs = release.plan(json.dumps([{"name": "pkg", "registry": "pypi"}]), read=reader(files))
        self.assertEqual([j["tag"] for j in jobs], ["python-v1.0"])

    def test_the_record_job_checks_out_the_release_tag_and_checks_it(self):
        # #18 under the same rule: a tag ref, verified against the gate's sha.
        steps = kit_yaml("templates/release.yml")["jobs"]["record"]["steps"]
        checkout, check = steps[0], steps[1]
        self.assertTrue(checkout["uses"].startswith("actions/checkout@"))
        self.assertEqual(checkout["with"]["ref"], "refs/tags/" + check["env"]["TAG"])
        self.assertEqual(check["env"]["SHA"], "${{ needs.gate.outputs.sha }}")
        with tempfile.TemporaryDirectory() as d:
            sh(d, "git", "init", "-q", "-b", "main")
            sh(d, "git", "commit", "-q", "--allow-empty", "-m", "gated")
            gated = sh(d, "git", "rev-parse", "HEAD")
            sh(d, "git", "commit", "-q", "--allow-empty", "-m", "moved")

            def run(sha):
                return subprocess.run(["bash", "-c", check["run"]], cwd=d, text=True, capture_output=True,
                                      env={**os.environ, "TAG": "release-1.0.0", "SHA": sha}).returncode
            self.assertEqual(run(gated), 1, "a moved tag")
            self.assertEqual(run(sh(d, "git", "rev-parse", "HEAD")), 0)
            self.assertEqual(run(""), 0, "a failed gate has no sha: the record still says what happened")

    def test_a_channel_only_selection_plans_nothing_and_its_entry_has_a_record_tag(self):
        # #20 with the build's checkout: plan checks out selection[0].tag,
        # which the gate found on sha; the package itself builds nothing.
        files = dict(FILES, **{"rab.yml": CONFIG + (
            "  - name: tool-bin\n    registry: github\n    manifest: crates/tool/Cargo.toml\n    version: '0.2.0'\n"
            "    tag: 'v{version}'\n    artifacts: [{kind: rust-binary, bin: tool}]\n"
            "    homebrew: {tap: org/homebrew-tap, formula: tool.rb.tmpl}\n")})
        release_ = release.load(reader(files))
        package = release_.packages[-1]
        with tempfile.TemporaryDirectory() as d:
            selection = release.gate_outputs("0" * 40, release_, [package], False, Path(d) / "out",
                                             {package: ["homebrew"]})["selection"]
            self.assertEqual(json.loads(selection)[0]["tag"], "v0.2.0")
            self.assertEqual(release.plan(selection, read=reader(files), output=Path(d) / "plan"), [])
            full = release.gate_outputs("0" * 40, release_, [package], False, Path(d) / "out")["selection"]
            self.assertTrue(all(j["tag"] == "v0.2.0" for j in release.plan(full, read=reader(files),
                                                                             output=Path(d) / "plan")))

    def test_nothing_writes_a_cache(self):
        files = [*KIT.glob("*/action.yml"), *KIT.glob("workflows/*.yml"), *KIT.glob("templates/*.yml")]
        for file in files:
            for step in every_step(kit_yaml(file.relative_to(KIT))):
                uses, given = step.get("uses", ""), step.get("with") or {}
                where = f"{file.relative_to(KIT)}: {uses}"
                self.assertFalse(uses.startswith("actions/cache"), where)
                self.assertNotIn("cache", given, where)
                if uses.startswith("actions/setup-node@"):
                    # On by default when package.json names npm as packageManager.
                    self.assertEqual(given.get("package-manager-cache"), False, where)
                if uses.startswith("PyO3/maturin-action@"):
                    self.assertEqual(given.get("sccache"), "false", where)


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
        for path in [".github/rab/scripts/release.py", ".github/rab/gate/action.yml",
                     ".github/rab/plan/action.yml", ".github/rab/build/action.yml",
                     ".github/rab/publish-github/action.yml", ".github/workflows/rab-build.yml",
                     ".github/rab/publish-pypi/requirements.txt",
                     ".github/workflows/release.yml", ".github/rab/manifest.json"]:
            self.assertIn(path, written)
            self.assertTrue((self.repo / path).is_file(), path)
        manifest = json.loads((self.repo / ".github/rab/manifest.json").read_text())
        self.assertEqual(manifest["source"], "buchochelliq-labs/release-asset-bundle")
        self.assertEqual(release.vendored(self.repo), [])
        # Everything is used by local path: public repositories can run it.
        workflow = (self.repo / ".github/workflows/release.yml").read_text()
        self.assertIn("uses: ./.github/rab/gate", workflow)
        self.assertNotIn("buchochelliq-labs/release-asset-bundle/", workflow)

    def test_edits_and_additions_to_the_copy_are_caught(self):
        release.install(self.repo)
        gate = self.repo / ".github/rab/gate/action.yml"
        gate.write_text(gate.read_text() + "# local tweak\n")
        (self.repo / ".github/rab/extra.sh").write_text("echo\n")
        problems = "\n".join(release.vendored(self.repo))
        self.assertIn("gate/action.yml was edited", problems)
        self.assertIn("extra.sh is not part of the kit", problems)

    def test_installing_again_replaces_the_copy_and_keeps_the_release_workflow(self):
        release.install(self.repo)
        (self.repo / ".github/rab/extra.sh").write_text("echo\n")
        own = self.repo / ".github/workflows/release.yml"
        own.write_text(own.read_text() + "# this repository's own changes\n")
        release.install(self.repo)
        self.assertEqual(release.vendored(self.repo), [])
        self.assertIn("this repository's own changes", own.read_text())

    def test_install_needs_a_release_kit_checkout(self):
        release.install(self.repo)
        with self.assertRaisesRegex(RuntimeError, "checkout of the kit"):
            release.install(Path(self.dir.name) / "other", kit=self.repo / ".github/rab")
        self.assertEqual(release.vendored(Path(self.dir.name)), [
            ".github/rab/manifest.json is missing: install rab (release.py install)"])


class Sync(unittest.TestCase):
    def test_sync_rewrites_versions_and_keeps_comments(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            for path, text in FILES.items():
                (base / path).parent.mkdir(parents=True, exist_ok=True)
                (base / path).write_text(text)
            (base / "rab.yml").write_text("# keep me\n" + CONFIG)
            (base / "crates/tool/Cargo.toml").write_text(FILES["crates/tool/Cargo.toml"].replace("0.2.0", "0.2.1"))
            changes = release.sync(path=base / "rab.yml")
            self.assertEqual(changes, ["tool: 0.2.0 -> 0.2.1"])
            text = (base / "rab.yml").read_text()
            self.assertTrue(text.startswith("# keep me") and 'version: "0.2.1"' in text)



# ---------------------------------------------------------------------------
# 2.0.0: release-kit is renamed rab; release-kit 1.x's names keep working,
# with a warning, until 3.0.0

def old_names(files):
    return {("release-kit.yml" if path == "rab.yml" else path): text for path, text in files.items()}


class OldNames(unittest.TestCase):
    def setUp(self):
        release._warned.clear()

    def warnings(self, call, *args):
        with unittest.mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            result = call(*args)
        return result, err.getvalue()

    def test_release_kit_yml_is_read_with_a_warning(self):
        loaded, err = self.warnings(release.load, reader(old_names(FILES)))
        self.assertEqual(loaded.name, "0.2.0")
        self.assertIn("release-kit.yml is release-kit 1.x's name for rab.yml; rename it (git mv release-kit.yml "
                      "rab.yml", err)
        self.assertEqual(release.check(reader(old_names(FILES))), [])
        # Once per run, not once per read.
        self.assertEqual(self.warnings(release.load, reader(old_names(FILES)))[1], "")

    def test_rab_yml_is_read_without_one(self):
        self.assertEqual(self.warnings(release.load, reader(FILES))[1], "")

    def test_both_names_are_refused(self):
        both = {**FILES, "release-kit.yml": CONFIG}
        with self.assertRaisesRegex(ValueError, "both rab.yml and release-kit.yml exist: keep rab.yml"):
            release.load(reader(both))
        self.assertEqual(release.check(reader(both)), [
            "both rab.yml and release-kit.yml exist: keep rab.yml (rab 2.x's name) and delete release-kit.yml"])

    def test_neither_name_raises_what_reading_rab_yml_raised(self):
        files = {k: v for k, v in FILES.items() if k != "rab.yml"}
        with self.assertRaisesRegex(FileNotFoundError, "rab.yml"):
            release.load(reader(files))

    def test_an_old_name_is_read_at_a_commit(self):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            sh(work, "git", "init", "-q", "-b", "main")
            for path, text in old_names(FILES).items():
                (work / path).parent.mkdir(parents=True, exist_ok=True)
                (work / path).write_text(text)
            sh(work, "git", "add", "-A")
            sh(work, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "1.x")
            with unittest.mock.patch.object(release, "root", return_value=work):
                loaded, err = self.warnings(release.release_at, sh(work, "git", "rev-parse", "HEAD"))
        self.assertEqual(loaded.tag, "release-0.2.0")
        self.assertIn("release-kit.yml is release-kit 1.x's name", err)

    def test_sync_and_the_config_path_follow_the_old_name(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            for path, text in old_names(FILES).items():
                (base / path).parent.mkdir(parents=True, exist_ok=True)
                (base / path).write_text(text)
            (base / "crates/tool/Cargo.toml").write_text(FILES["crates/tool/Cargo.toml"].replace("0.2.0", "0.2.1"))
            path, err = self.warnings(release.config_path, base)
            self.assertEqual(path, base / "release-kit.yml")
            self.assertIn("rename it", err)
            self.assertEqual(release.sync(path=path), ["tool: 0.2.0 -> 0.2.1"])
            self.assertIn('version: "0.2.1"', (base / "release-kit.yml").read_text())
            self.assertFalse((base / "rab.yml").exists())
            (base / "rab.yml").write_text(CONFIG)
            with self.assertRaisesRegex(ValueError, "both rab.yml and release-kit.yml exist"):
                release.config_path(base)

    def test_the_old_offline_variable_is_read_with_a_warning(self):
        with unittest.mock.patch.dict(os.environ, {"RELEASE_KIT_OFFLINE": "1"}):
            os.environ.pop("RAB_OFFLINE", None)
            offline, err = self.warnings(release.offline)
        self.assertTrue(offline)
        self.assertIn("RELEASE_KIT_OFFLINE is release-kit 1.x's name: set RAB_OFFLINE", err)
        with unittest.mock.patch.dict(os.environ, {"RELEASE_KIT_OFFLINE": "1", "RAB_OFFLINE": "0"}):
            self.assertFalse(release.offline(), "the new name wins")
        with unittest.mock.patch.dict(os.environ, {"RAB_OFFLINE": "yes"}):
            os.environ.pop("RELEASE_KIT_OFFLINE", None)
            self.assertTrue(release.offline())

    def test_the_version_is_the_changelog_s_newest(self):
        changelog = (KIT / "docs" / "changelog.md").read_text(encoding="utf-8")
        newest = re.search(r"^### v(\d+\.\d+\.\d+)", changelog, re.M)[1]
        self.assertEqual(release.VERSION, newest)
        self.assertNotIn("### Unreleased", changelog.split(f"### v{newest}")[0])

    def test_versions_have_no_gaps(self):
        # Each version is the next patch, minor or major after the one below
        # it (docs/changelog.md, "How rab is versioned"); checked from v2.0.0.
        changelog = (KIT / "docs" / "changelog.md").read_text(encoding="utf-8")
        versions = [tuple(map(int, v)) for v in re.findall(r"^### v(\d+)\.(\d+)\.(\d+)\s*$", changelog, re.M)]
        versions = [v for v in versions if v >= (2, 0, 0)]
        for newer, older in zip(versions, versions[1:]):
            major, minor, patch = older
            allowed = {(major, minor, patch + 1), (major, minor + 1, 0), (major + 1, 0, 0)}
            self.assertIn(newer, allowed, f"v{'.'.join(map(str, newer))} does not follow "
                                          f"v{'.'.join(map(str, older))}")


class Migrate(unittest.TestCase):
    """`install` into a repository with release-kit 1.x's layout moves it to rab's."""

    RELEASE_YML = ("jobs:\n  gate:\n    steps:\n      - uses: ./.github/release-kit/gate\n"
                   "  build:\n    uses: ./.github/workflows/release-kit-build.yml\n"
                   "  record:\n    steps:\n      - run: python3 .github/release-kit/scripts/release.py record\n")

    def setUp(self):
        release._warned.clear()
        self.dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.dir.name) / "repo"
        self.repo.mkdir()
        sh(self.repo, "git", "init", "-q", "-b", "main")
        # release-kit 1.x's layout: what its install wrote.
        release.install(self.repo)
        (self.repo / ".github/rab").rename(self.repo / ".github/release-kit")
        (self.repo / ".github/workflows/rab-build.yml").rename(self.repo / ".github/workflows/release-kit-build.yml")
        manifest = self.repo / ".github/release-kit/manifest.json"
        data = json.loads(manifest.read_text())
        data["files"] = {path.replace(".github/rab/", ".github/release-kit/")
                         .replace("rab-build.yml", "release-kit-build.yml"): sha for path, sha in data["files"].items()}
        manifest.write_text(json.dumps(data))
        (self.repo / ".github/workflows/release.yml").write_text(self.RELEASE_YML)
        (self.repo / ".github/workflows/ci.yml").write_text(
            "jobs:\n  t:\n    steps:\n      - run: python3 .github/release-kit/scripts/release.py vendored\n")
        (self.repo / "release-kit.yml").write_text(CONFIG)
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "release-kit 1.x")

    def tearDown(self):
        self.dir.cleanup()

    def test_vendored_accepts_the_old_path_with_a_warning(self):
        with unittest.mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(release.vendored(self.repo), [])
        self.assertIn("the installed copy is at .github/release-kit/, release-kit 1.x's path", err.getvalue())
        gate = self.repo / ".github/release-kit/gate/action.yml"
        gate.write_text(gate.read_text() + "# tweak\n")
        self.assertEqual(release.vendored(self.repo), [
            ".github/release-kit/gate/action.yml was edited; the kit's files change only by installing again"])

    def test_install_migrates_the_old_layout(self):
        with unittest.mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            release.install(self.repo)
        printed = out.getvalue()
        self.assertTrue((self.repo / "rab.yml").is_file())
        self.assertFalse((self.repo / "release-kit.yml").exists())
        self.assertIn("R  release-kit.yml -> rab.yml", sh(self.repo, "git", "status", "--porcelain"), "git mv")
        self.assertFalse((self.repo / ".github/release-kit").exists())
        self.assertFalse((self.repo / ".github/workflows/release-kit-build.yml").exists())
        self.assertTrue((self.repo / ".github/workflows/rab-build.yml").is_file())
        self.assertEqual((self.repo / ".github/workflows/release.yml").read_text(), self.RELEASE_YML.replace(
            ".github/release-kit/", ".github/rab/").replace("release-kit-build.yml", "rab-build.yml"))
        self.assertIn("python3 .github/rab/scripts/release.py vendored",
                      (self.repo / ".github/workflows/ci.yml").read_text())
        self.assertEqual(release.vendored(self.repo), [])
        for line in ["renamed release-kit.yml -> rab.yml (git mv)", "removed .github/release-kit/",
                     "removed .github/workflows/release-kit-build.yml",
                     "rewrote 3 old paths in .github/workflows/release.yml",
                     "rewrote 1 old path in .github/workflows/ci.yml"]:
            self.assertIn(line, printed)
        with unittest.mock.patch.object(release, "root", return_value=self.repo), \
                unittest.mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(release.load(release.files_on_disk(self.repo)).name, "0.2.0")
        self.assertEqual(err.getvalue(), "", "nothing old is left to warn about")

    def test_an_untracked_old_config_is_renamed(self):
        sh(self.repo, "git", "rm", "-q", "--cached", "release-kit.yml")
        with unittest.mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            release.install(self.repo)
        self.assertIn("renamed release-kit.yml -> rab.yml\n", out.getvalue())
        self.assertTrue((self.repo / "rab.yml").is_file())

    def test_install_refuses_both_config_names_and_changes_nothing(self):
        (self.repo / "rab.yml").write_text(CONFIG)
        with self.assertRaisesRegex(RuntimeError, "both rab.yml and release-kit.yml exist"):
            release.install(self.repo)
        self.assertTrue((self.repo / ".github/release-kit/manifest.json").is_file())
        self.assertFalse((self.repo / ".github/rab").exists())
        self.assertEqual((self.repo / ".github/workflows/release.yml").read_text(), self.RELEASE_YML)

    def test_a_copy_left_at_the_old_path_is_a_problem(self):
        with unittest.mock.patch("sys.stdout", new_callable=io.StringIO):
            release.install(self.repo)
        (self.repo / ".github/release-kit").mkdir()
        self.assertIn(".github/release-kit/ is left from release-kit 1.x: delete it (install migrates it)",
                      release.vendored(self.repo))


# ---------------------------------------------------------------------------
# #20: a retry policy per channel, and finishing derived channels with `tag`

class RetryPolicy(unittest.TestCase):
    def test_every_registry_and_channel_has_a_policy(self):
        self.assertEqual(set(release.RETRY), set(release.REGISTRIES) | set(release.CHANNEL_KEYS))
        self.assertEqual({c: release.RETRY[c] for c in ("crates.io", "github", "homebrew", "chocolatey")},
                         {"crates.io": "once", "github": "finish", "homebrew": "idempotent",
                          "chocolatey": "until-accepted"})

    def test_only_channels_that_may_run_again_and_are_not_done_are_pending(self):
        tool = release.Package("tool", "github", "Cargo.toml", "1.2.0", github=(
            ("chocolatey", {"package": "p"}), ("homebrew", {"tap": "o/t", "formula": "f.rb"})))
        self.assertEqual(release.channels_of(tool), ["github", "homebrew", "chocolatey"])
        self.assertEqual(release.channels_of(release.Package("core", "crates.io", "m", "0.1.0")), ["crates.io"])
        states = {"homebrew": "proposed", "chocolatey": "due"}
        self.assertEqual(release.pending_channels(tool, lambda p, c: states[c]), {"chocolatey": "due"})
        # Rejected is terminal: a rejected version can never be pushed again.
        states, ended = {"homebrew": "due", "chocolatey": "rejected"}, {}
        self.assertEqual(release.pending_channels(tool, lambda p, c: states[c], ended), {"homebrew": "due"})
        self.assertEqual(ended, {"chocolatey": "rejected"})
        states = {"homebrew": "due", "chocolatey": "submitted"}
        self.assertEqual(release.pending_channels(tool, lambda p, c: states[c]), {"homebrew": "due"})


class Fetch:
    """A fake `http_status`: answers by URL, records what was asked."""

    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def __call__(self, url, data=None, headers=None):
        self.asked.append(url)
        for prefix, answer in self.answers.items():
            if url.startswith(prefix):
                return answer
        return 404, b""


class HomebrewState(unittest.TestCase):
    settings = {"tap": "org/homebrew-tap", "formula": "packaging/tool.rb.tmpl"}
    package = release.Package("tool", "github", "Cargo.toml", "1.2.0", "v{version}")
    CONTENTS = "https://api.github.com/repos/org/homebrew-tap/contents/Formula/tool.rb"
    PULLS = "https://api.github.com/repos/org/homebrew-tap/pulls?state=open&head=org%3Atool-1.2.0"

    def formula(self, version):
        text = f'class Tool < Formula\n  url "https://github.com/org/tool/releases/download/v{version}/x.tar.gz"\nend\n'
        return 200, json.dumps({"content": __import__("base64").b64encode(text.encode()).decode()}).encode()

    def state(self, answers, package=None):
        fetch = Fetch(answers)
        return release.homebrew_state(package or self.package, self.settings, fetch), fetch.asked

    def test_the_default_branch_at_this_version_is_merged(self):
        self.assertEqual(self.state({self.CONTENTS: self.formula("1.2.0")}), ("merged", [self.CONTENTS]))

    def test_an_open_pull_request_from_the_release_branch_is_proposed(self):
        state, asked = self.state({self.CONTENTS: self.formula("1.1.0"), self.PULLS: (200, b'[{"number": 3}]')})
        self.assertEqual((state, asked), ("proposed", [self.CONTENTS, self.PULLS]))

    def test_an_older_formula_and_no_pull_request_is_due(self):
        self.assertEqual(self.state({self.CONTENTS: self.formula("11.2.0"), self.PULLS: (200, b"[]")})[0], "due")
        self.assertEqual(self.state({})[0], "due", "no formula at all")

    def test_a_prerelease_is_left_out_without_asking(self):
        package = release.Package("tool", "github", "Cargo.toml", "1.2.0-rc.1", "v{version}")
        self.assertEqual(self.state({}, package), ("prerelease", []))


class ChocolateyStepOnWindows(unittest.TestCase):
    """publish-chocolatey's bash step reads jq's output; on Windows jq ends lines with
    CRLF, and a \r left in a field broke the v0.1.3 release of open-data-suite."""

    def test_no_carriage_return_reaches_a_path_or_a_field(self):
        import shutil
        import yaml
        real_jq = shutil.which("jq")
        if not real_jq:
            self.skipTest("jq is not installed")
        step = next(st for st in yaml.safe_load(
            (Path(__file__).resolve().parent.parent / "publish-chocolatey" / "action.yml").read_text()
        )["runs"]["steps"] if st.get("shell") == "bash")
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            stubs = d / "stubs"
            stubs.mkdir()
            # jq as on Windows: every line ends with CRLF.
            (stubs / "jq").write_text(f'#!/bin/sh\n"{real_jq}" "$@" | sed "s/$/\\r/"\n')
            # The kit's script: CRLF too (Python's print on Windows).
            (stubs / "python3").write_text(
                '#!/bin/sh\ncase "$2" in github-release) printf \'{"chocolatey": {"package": "p", '
                '"test": "ods version"}}\\r\\n\' ;; esac\n')
            (stubs / "gh").write_text(
                '#!/bin/sh\nwhile [ $# -gt 0 ]; do [ "$1" = --dir ] && dir=$2; shift; done\n'
                'mkdir -p "$dir" && echo "aa  ods.zip" > "$dir/ods.zip.sha256"\n')
            for stub in stubs.iterdir():
                stub.chmod(0o755)
            selection = json.dumps([{"name": "ods", "registry": "github", "version": "0.1.3",
                                     "tag": "v0.1.3", "slug": "ods", "channels": ["chocolatey"]}])
            env = {**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}", "RUNNER_TEMP": str(d),
                   "GITHUB_ACTION_PATH": str(d / "action"), "SELECTION": selection}
            subprocess.run(["bash", "-c", step["run"]], env=env, check=True)
            root = d / "chocolatey"
            self.assertEqual(sorted(p.name for p in root.iterdir() if p.is_dir()), ["ods"])
            self.assertTrue((root / "ods" / "SHA256SUMS").is_file())
            self.assertEqual((root / "packages").read_bytes(), b"ods\t0.1.3\tods\tods version\n")


class ChocolateyState(unittest.TestCase):
    URL = "https://community.chocolatey.org/api/v2/Packages(Id='onto-cli',Version='1.2.0')"
    package = release.Package("tool", "github", "Cargo.toml", "1.2.0", "v{version}")

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.dir.name)
        sh(".", "git", "init", "-q")
        Path("packaging/choco/tools").mkdir(parents=True)
        Path("packaging/choco/onto.nuspec.tmpl").write_text(
            "<package><metadata><id>onto-cli</id><version>${version}</version></metadata></package>\n")
        Path("packaging/choco/tools/chocolateyinstall.ps1.tmpl").write_text("# <id>not this</id>\n")

    def tearDown(self):
        os.chdir(self.cwd)
        self.dir.cleanup()

    def state(self, answer):
        fetch = Fetch({self.URL: answer} if answer else {})
        return release.chocolatey_state(self.package, {"package": "packaging/choco"}, fetch)

    def entry(self, properties):
        return 200, f"<entry><m:properties>{properties}</m:properties></entry>".encode()

    def test_each_state_of_the_community_repository(self):
        self.assertEqual(self.state(None), "due", "never pushed")
        self.assertEqual(self.state(self.entry("<d:PackageStatus>Approved</d:PackageStatus>")), "listed")
        self.assertEqual(self.state(self.entry("<d:PackageStatus>Exempted</d:PackageStatus>")), "listed")
        self.assertEqual(self.state(self.entry("<d:PackageStatus>Submitted</d:PackageStatus>")), "submitted")
        self.assertEqual(self.state(self.entry("<d:PackageStatus>Rejected</d:PackageStatus>")), "rejected")
        self.assertEqual(self.state(self.entry('<d:IsApproved m:type="Edm.Boolean">false</d:IsApproved>')),
                         "submitted")
        self.assertEqual(self.state(self.entry("")), "listed", "served without a status: listed")

    def test_moderation_counts_as_done_and_a_rejection_does_not(self):
        self.assertIn("submitted", release.DONE)
        self.assertNotIn("rejected", release.DONE)
        self.assertEqual(release.state_text("rejected"), "rejected: publish a new version")

    # choco 2.x's output for a version the community repository already has
    # (its NugetPush.cs), and NuGetGallery's own message as choco 1.x shows it.
    EXISTS_V2 = ("An error has occurred. This package version already exists on the repository and cannot be "
                 "modified.\nPackage versions that are approved, rejected, or exempted cannot be modified.\n"
                 "See https://docs.chocolatey.org/en-us/community-repository/moderation/ for more information")
    EXISTS_V1 = ("Attempting to push onto-cli.1.2.0.nupkg to https://push.chocolatey.org/\nFailed to process request. "
                 "'A package with ID 'onto-cli' and version '1.2.0' already exists and cannot be modified.'.\n"
                 "The remote server returned an error: (409) Conflict..")
    CONFIG = ("packages:\n  - name: tool\n    registry: github\n    manifest: Cargo.toml\n    version: '1.2.0'\n"
              "    tag: 'v{version}'\n    artifacts: [{kind: rust-binary, bin: tool}]\n"
              "    chocolatey: {package: packaging/choco}\n")

    def refused(self, log, state):
        files = {"Cargo.toml": '[package]\nname = "tool"\nversion = "1.2.0"\n', "rab.yml": self.CONFIG}
        asked = []

        def state_of(package, channel):
            asked.append((package.name, package.version, channel))
            if isinstance(state, Exception):
                raise state
            return state
        result = release.chocolatey_refused("tool", log, state_of, read=reader(files))
        self.assertEqual(asked, [("tool", "1.2.0", "chocolatey")])
        return result

    def test_a_push_refused_because_the_version_exists_counts_as_pushed(self):
        self.assertEqual(self.refused(self.EXISTS_V2, "listed"), "listed")
        self.assertEqual(self.refused(self.EXISTS_V1, "submitted"), "submitted")
        self.assertEqual(self.refused(self.EXISTS_V2, "due"), "due", "the API does not show it yet")
        self.assertEqual(self.refused(self.EXISTS_V2, RuntimeError("no answer")), "unknown")

    def test_a_rejected_version_cannot_be_pushed_again(self):
        with self.assertRaisesRegex(RuntimeError, "Chocolatey rejected: publish a new version.*cannot be pushed"):
            self.refused(self.EXISTS_V2, "rejected")

    def test_any_other_push_failure_still_fails(self):
        for log in ("An error has occurred. It's possible the package version already exists on the repository "
                    "or a nuspec element is invalid. See error below...\nResponse status code does not indicate "
                    "success: 409 (Conflict).",
                    "Response status code does not indicate success: 403 (Forbidden).", ""):
            with self.assertRaisesRegex(RuntimeError, "choco push failed"):
                release.chocolatey_refused("tool", log, lambda p, c: self.fail("not asked"))

    def test_the_action_falls_back_only_after_a_failed_push(self):
        import yaml
        steps = yaml.safe_load((Path(__file__).resolve().parent.parent / "publish-chocolatey" / "action.yml")
                               .read_text())["runs"]["steps"]
        script = steps[-1]["run"]
        push = script.index("choco push")
        self.assertIn("Tee-Object -FilePath $log", script[push:])
        fallback = script.index("chocolatey-refused --name $name --log $log")
        self.assertLess(script.index("if ($LASTEXITCODE -ne 0) {", push), fallback)
        self.assertLess(fallback, script.index("pushed\"", push))
        log = Path("push.log")
        log.write_text(self.EXISTS_V2)
        with unittest.mock.patch.object(release, "chocolatey_refused") as refused:
            self.assertEqual(release.main(["chocolatey-refused", "--name", "tool", "--log", str(log)]), 0)
        refused.assert_called_once_with("tool", self.EXISTS_V2)

    def test_the_id_is_read_from_the_nuspec_at_a_commit(self):
        sh(".", "git", "add", "-A")
        sh(".", "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
        commit = sh(".", "git", "rev-parse", "HEAD")
        Path("packaging/choco/onto.nuspec.tmpl").write_text("<id>changed-since</id>\n")
        self.assertEqual(release.chocolatey_id("packaging/choco", commit), "onto-cli")
        self.assertEqual(release.chocolatey_id("packaging/choco"), "changed-since")
        Path("packaging/choco/onto.nuspec.tmpl").write_text("<id>${name}</id>\n")
        with self.assertRaisesRegex(ValueError, "a literal <id>"):
            release.chocolatey_id("packaging/choco")


DERIVED_CONFIG = """
packages:
  - name: core
    registry: crates.io
    manifest: crates/core/Cargo.toml
    version: "0.1.0"
  - name: tool
    registry: github
    manifest: crates/tool/Cargo.toml
    version: "0.2.0"
    artifacts: [{kind: rust-binary, bin: tool}]
    homebrew: {tap: org/homebrew-tap, formula: packaging/tool.rb.tmpl}
    chocolatey: {package: packaging/choco}
"""


class FinishChannels(Repo):
    """A published github package whose Homebrew or Chocolatey is not done is
    selected again for those channels only."""

    def setUp(self):
        super().setUp()
        self.write({"rab.yml": DERIVED_CONFIG})
        self.first = self.commit("channels")
        self.states = {}
        self.success = {"status": "completed", "conclusion": "success", "url": "u", "head_sha": self.first,
                        "publish_started": True}

    def channel_status(self, package, channel, commit):
        return self.states.get(channel, "due")

    def run_tag(self, **kw):
        import contextlib
        import io
        waits, out = [], io.StringIO()
        with contextlib.redirect_stdout(out):
            release.tag(status=self.status, run=kw.pop("run", lambda tag: None), channel_status=self.channel_status,
                        wait=lambda *a, **k: waits.append((a[0], [(p.name, c) for p, c in k.get("channels", [])])),
                        **kw)
        return waits, out.getvalue()

    def gate(self):
        out = Path(self.dir.name) / "out"
        out.write_text("")
        release.gate("release-0.2.0", status=self.status, output=out, channel_status=self.channel_status)
        return dict(line.split("=", 1) for line in out.read_text().splitlines())

    def publish_all(self):
        self.published.update({("core", "0.1.0"), ("tool", "0.2.0")})

    def test_a_first_release_waits_for_its_channels_too(self):
        waits, _ = self.run_tag()
        self.assertEqual(waits, [("release-0.2.0", [("tool", "homebrew"), ("tool", "chocolatey")])])

    def test_the_gate_selects_a_published_package_for_its_unfinished_channels(self):
        self.run_tag()
        self.publish_all()
        self.states = {"homebrew": "due", "chocolatey": "submitted"}
        outputs = self.gate()
        selection = json.loads(outputs["selection"])
        self.assertEqual([(s["name"], s["channels"]) for s in selection], [("tool", ["homebrew"])])
        self.assertEqual({k: outputs[k] for k in ("github", "crates-io", "homebrew", "chocolatey")},
                         {"github": "false", "crates-io": "false", "homebrew": "true", "chocolatey": "false"})
        # publish-github and the build skip it: its files are already out.
        self.assertEqual(release.selected("github", outputs["selection"]), [])
        self.assertEqual(release.plan(outputs["selection"], output=os.devnull), [])

    def test_a_release_with_every_channel_done_is_not_selected(self):
        self.run_tag()
        self.publish_all()
        self.states = {"homebrew": "proposed", "chocolatey": "listed"}
        with self.assertRaisesRegex(RuntimeError, "already published"):
            self.gate()

    def test_an_unpublished_package_is_selected_with_every_channel(self):
        self.run_tag()
        self.published.add(("core", "0.1.0"))
        selection = json.loads(self.gate()["selection"])
        self.assertEqual([(s["name"], s["channels"]) for s in selection],
                         [("tool", ["github", "homebrew", "chocolatey"])])
        self.assertEqual([p.name for p in release.selected("github", json.dumps(selection))], ["tool"])

    def test_tag_goes_back_and_pushes_the_release_tag_again_for_the_channels(self):
        self.run_tag()
        before = sh(self.work, "git", "ls-remote", str(self.remote), "refs/tags/release-0.2.0")
        self.write({"README.md": "later\n"})
        self.commit("docs")
        self.publish_all()
        self.states = {"chocolatey": "due"}
        waits, out = self.run_tag(run=lambda tag: self.success)
        self.assertIn("release 0.2.0 is already tagged there", out)
        self.assertIn("skip   core-v0.1.0", out)
        self.assertIn("finish tool-v0.2.0: tool 0.2.0 (github) is published; homebrew is due (idempotent), "
                      "chocolatey is due (until-accepted)", out)
        self.assertIn("retag  release-0.2.0: its run ended success", out)
        self.assertEqual(waits, [("release-0.2.0", [("tool", "homebrew"), ("tool", "chocolatey")])])
        tags = self.remote_tags()
        self.assertEqual(tags["release-0.2.0"], self.first, "on the release's commit, not HEAD")
        self.assertNotEqual(sh(self.work, "git", "ls-remote", str(self.remote), "refs/tags/release-0.2.0"),
                            before, "a new tag object, which starts a new run")
        message = sh(self.work, "git", "for-each-ref", "refs/tags/release-0.2.0", "--format=%(contents)")
        self.assertIn("tool 0.2.0 (github): homebrew, chocolatey", message)

    def stopped(self):
        return {"status": "completed", "conclusion": "failure", "url": "u", "head_sha": self.first,
                "publish_started": True}

    def test_a_stopped_release_is_carried_to_a_later_commit_that_ships_the_same_files(self):
        # As open-data-suite 0.1.3: the GitHub release went out, then the run
        # failed before core; the fix is in the release tooling on a later commit.
        self.run_tag()
        self.published.add(("tool", "0.2.0"))
        self.write({"README.md": "a fix outside every package\n"})
        later = self.commit("fix the release tooling")
        self.states = {"homebrew": "proposed", "chocolatey": "due"}
        waits, out = self.run_tag(commit=later, run=lambda tag: self.stopped())
        self.assertIn("carry  core-v0.1.0: the release run at", out)
        self.assertIn("carry  release-0.2.0: its run at", out)
        self.assertIn("finish tool-v0.2.0: tool 0.2.0 (github) is published; chocolatey is due", out)
        tags = self.remote_tags()
        self.assertEqual((tags["core-v0.1.0"], tags["release-0.2.0"]), (later, later))
        self.assertEqual(tags["tool-v0.2.0"], self.first, "a published package keeps its record")
        self.assertEqual(release.trailers("release-0.2.0"),
                         {"Release": "0.2.0", "Commit": later, "Carried-from": self.first})
        self.assertEqual(waits, [("release-0.2.0", [("tool", "chocolatey")])])
        selection = json.loads(self.gate()["selection"])
        self.assertEqual([(s["name"], s["channels"]) for s in selection],
                         [("core", ["crates.io"]), ("tool", ["chocolatey"])])

    def test_a_carried_release_whose_channel_failed_is_finished_again_there(self):
        self.run_tag()
        self.published.add(("tool", "0.2.0"))
        self.write({"README.md": "a fix outside every package\n"})
        later = self.commit("fix the release tooling")
        self.states = {"homebrew": "proposed", "chocolatey": "due"}
        self.run_tag(commit=later, run=lambda tag: self.stopped())
        # The carried run publishes core, then Chocolatey fails again.
        self.published.add(("core", "0.1.0"))
        failed_here = {"status": "completed", "conclusion": "failure", "url": "u2", "head_sha": later,
                       "publish_started": True}
        for kw in ({}, {"commit": later}):
            waits, out = self.run_tag(run=lambda tag: failed_here, **kw)
            self.assertIn(f"Release 0.2.0 from {later}", out)
            self.assertIn("finish tool-v0.2.0: tool 0.2.0 (github) is published; chocolatey is due", out)
            self.assertIn("retag  release-0.2.0: its run ended failure", out)
            self.assertEqual(waits, [("release-0.2.0", [("tool", "chocolatey")])])
            self.assertEqual(release.trailers("release-0.2.0"),
                             {"Release": "0.2.0", "Commit": later, "Carried-from": self.first})
        selection = json.loads(self.gate()["selection"])
        self.assertEqual([(s["name"], s["channels"]) for s in selection], [("tool", ["chocolatey"])])

    def test_a_release_is_carried_only_when_asked(self):
        self.run_tag()
        self.published.add(("tool", "0.2.0"))
        self.write({"README.md": "later\n"})
        self.commit("later")
        waits, out = self.run_tag(run=lambda tag: self.stopped())
        self.assertIn("release 0.2.0 is already tagged there", out)
        self.assertNotIn("carry", out)
        self.assertEqual(self.remote_tags()["release-0.2.0"], self.first)

    def test_a_live_run_is_not_carried(self):
        self.run_tag()
        self.write({"README.md": "later\n"})
        later = self.commit("later")
        live = {"status": "in_progress", "conclusion": "", "url": "u", "head_sha": self.first,
                "publish_started": True}
        waits, out = self.run_tag(commit=later, run=lambda tag: live)
        self.assertIn("wait   core-v0.1.0: tagged at", out)
        self.assertNotIn("carry", out)
        self.assertIn("release-0.2.0", [w for w, _ in waits])
        self.assertEqual({self.remote_tags()[t] for t in ("core-v0.1.0", "release-0.2.0")}, {self.first})

    def test_a_later_commit_that_changes_a_package_is_not_carried_to(self):
        self.run_tag()
        self.published.add(("tool", "0.2.0"))
        self.write({"crates/core/src/lib.rs": "pub fn core() { /* changed */ }\n"})
        later = self.commit("change core without a bump")
        with self.assertRaisesRegex(RuntimeError, "changes packages already tagged"):
            self.run_tag(commit=later, run=lambda tag: self.stopped())

    def test_the_gate_refuses_a_carry_from_a_commit_that_is_not_an_ancestor(self):
        self.run_tag()
        head = sh(self.work, "git", "rev-parse", "HEAD")
        sh(self.work, "git", "checkout", "-q", "--orphan", "other")
        self.write({"x.txt": "x"})
        sh(self.work, "git", "add", "-A")
        sh(self.work, "git", "commit", "-q", "-m", "unrelated")
        stray = sh(self.work, "git", "rev-parse", "HEAD")
        sh(self.work, "git", "checkout", "-q", "main")
        sh(self.work, "git", "tag", "-d", "release-0.2.0")
        sh(self.work, "git", "tag", "-a", "release-0.2.0", "-m",
           f"Release 0.2.0\n\nRelease: 0.2.0\nCommit: {head}\nCarried-from: {stray}\n", head)
        with self.assertRaisesRegex(RuntimeError, "not an ancestor"):
            self.gate()

    def test_a_finished_release_is_left_alone(self):
        self.run_tag()
        self.publish_all()
        self.states = {"homebrew": "merged", "chocolatey": "submitted"}
        waits, out = self.run_tag(run=lambda tag: self.success)
        self.assertEqual(waits, [])
        self.assertIn("skip   tool-v0.2.0", out)
        self.assertIn("Every package of release 0.2.0 is published.", out)

    def test_a_newer_release_on_head_is_not_held_back_by_an_older_ones_channels(self):
        self.run_tag()
        self.publish_all()
        self.write({"rab.yml": DERIVED_CONFIG.replace('"0.2.0"', '"0.3.0"'),
                    "crates/tool/Cargo.toml": FILES["crates/tool/Cargo.toml"].replace("0.2.0", "0.3.0")})
        head = self.commit("bump tool")
        waits, out = self.run_tag(run=lambda tag: self.success)
        self.assertIn("note   release 0.2.0 has channels not done", out)
        self.assertIn("run `tag --release 0.2.0` to finish them", out)
        self.assertEqual(self.remote_tags()["release-0.3.0"], head)
        self.assertEqual(waits[0][0], "release-0.3.0")

    def test_a_rejected_chocolatey_version_is_terminal(self):
        self.run_tag()
        self.publish_all()
        self.states = {"homebrew": "merged", "chocolatey": "rejected"}
        # Not retried by `tag`: nothing is pushed or waited for.
        waits, out = self.run_tag(run=lambda tag: self.success)
        self.assertEqual(waits, [])
        self.assertIn("note   chocolatey of tool 0.2.0 (github) is rejected: publish a new version; not retried", out)
        self.assertIn("skip   tool-v0.2.0", out)
        # Not selected by the gate.
        with self.assertRaisesRegex(RuntimeError, "already published"):
            self.gate()
        # Reported by `status`.
        lines = release.status_lines(status=self.status, channel_status=self.channel_status)
        self.assertEqual(lines[-1].split(None, 2), ["chocolatey", "until-accepted", "rejected: publish a new version"])
        # Only the rejected channel ends: a due Homebrew is still finished.
        self.states = {"homebrew": "due", "chocolatey": "rejected"}
        waits, out = self.run_tag(run=lambda tag: self.success)
        self.assertEqual(waits, [("release-0.2.0", [("tool", "homebrew")])])

    def test_status_shows_each_channel_and_its_policy(self):
        self.run_tag()
        self.published.add(("tool", "0.2.0"))
        self.states = {"homebrew": "proposed", "chocolatey": "submitted"}
        lines = release.status_lines(status=self.status, channel_status=self.channel_status)
        self.assertEqual([line.split() for line in lines[-2:]],
                         [["homebrew", "idempotent", "proposed"], ["chocolatey", "until-accepted", "submitted"]])


class WaitForChannels(unittest.TestCase):
    package = release.Package("tool", "github", "Cargo.toml", "1.2.0", "v{version}")
    done = {"status": "completed", "conclusion": "success", "url": "u", "head_sha": "x", "publish_started": True}

    def wait(self, states):
        import contextlib
        import io
        sleeps, out = [], io.StringIO()
        answers = iter(states)
        with contextlib.redirect_stdout(out):
            release.wait_for("release-1.2.0", [self.package], 3600, status=lambda p: True, run=lambda t: self.done,
                             sleep=sleeps.append, channels=[(self.package, "homebrew")],
                             channel_status=lambda p, c: next(answers), settle=3)
        return len(sleeps), out.getvalue()

    def test_a_channel_that_becomes_done_is_waited_for(self):
        self.assertEqual(self.wait(["due", "proposed"])[0], 1)

    def test_one_never_done_is_named_and_does_not_hold_tag_for_hours(self):
        sleeps, out = self.wait(["due"] * 4)
        self.assertEqual(sleeps, 3)
        self.assertIn("note   homebrew of tool 1.2.0 (github) is due, not done", out)

    def test_a_rejected_one_is_not_waited_for(self):
        sleeps, out = self.wait(["rejected"] * 4)
        self.assertEqual(sleeps, 0)
        self.assertIn("note   homebrew of tool 1.2.0 (github) is rejected: publish a new version", out)


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
            (base / "rab.yml").write_text((base / config).read_text())
        self.answers = json.loads((base / "crates-io.json").read_text())
        return base

    def readiness(self, base, repo="acme/ontology", **kw):
        read = release.files_on_disk(base)
        self.assertEqual(release.check(read), [], "rab.yml itself is fine")
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
            "(or take the package out of rab.yml to keep the crate private)"])

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
            "repository), or list it in rab.yml (registry: crates.io) before onto-ods"])
        self.assertIn("library crates rab.yml does not publish: ods-erd.", warnings[0])
        self.answers["index"] = {"ods-erd": ["0.2.0", "0.4.0"]}
        errors, _ = self.readiness(base)
        self.assertIn("onto-ods: depends on ods-erd ^0.3, but crates.io has no matching version (newest: 0.4.0)",
                      errors[0])
        self.answers["index"] = {"ods-erd": ["0.2.0", "0.3.1"]}
        self.assertEqual(self.readiness(base)[0], [])
        # Or this release publishes it first.
        self.answers["index"] = {}
        config = (base / "rab.yml").read_text()
        (base / "rab.yml").write_text(config.replace("packages:\n", "packages:\n  - name: ods-erd\n"
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
            "library crates rab.yml does not publish: onto-core (publish = false), onto-ods. To publish "
            "one, list it with registry: crates.io; a crate meant to stay private needs nothing"]))

    def test_open_data_suite_ontology_before_its_48_is_refused_at_check(self):
        repo = "opendatasuite/open-data-suite-ontology"
        # As it was, only GitHub binaries: its library crates are pointed out.
        errors, warnings = self.readiness(self.fixture("ods-before-48"), repo=repo)
        self.assertEqual(errors, [])
        self.assertIn("does not publish: onto-core (publish = false), onto-ods (publish = false)", warnings[0])
        # With its crates declared for crates.io, every blocker #48 fixed by hand.
        errors = "\n".join(self.readiness(self.fixture("ods-before-48", "rab.crates-io.yml"),
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

    def test_windows_package_lists_are_read_as_posix_paths(self):
        # `cargo package --list` on Windows prints `src\\figlet.rs`: an embed
        # beside the source, or one the package carries, is not refused.
        with tempfile.TemporaryDirectory() as tmp:
            crate = Path(tmp)
            (crate / "src").mkdir()
            (crate / "fonts").mkdir()
            (crate / "assets").mkdir()
            (crate / "src/figlet.rs").write_text('const F: &str = include_str!("../fonts/standard.flf");\n')
            (crate / "src/lib.rs").write_text('const A: &[u8] = include_bytes!(concat!(env!("CARGO_MANIFEST_DIR"), '
                                              '"/assets/cat.gif"));\n'
                                              'const B: &str = include_str!("../../outside.txt");\n')
            packaged = ["Cargo.toml", "src\\figlet.rs", "src\\lib.rs", "fonts\\standard.flf", "assets\\cat.gif"]
            self.assertEqual(release.embed_problems("onto", crate, packaged), [
                "onto: src/lib.rs embeds ../../outside.txt, outside the crate; a package carries only its crate "
                "directory. Copy the file into the crate (and add a test that the copy matches the original)"])

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


def jwt(claims):
    import base64
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJSUzI1NiJ9.{payload}.c2ln"


class PypiUpload(unittest.TestCase):
    """v2.0.1: publish-pypi exchanges the job's OIDC token for a PyPI token and
    uploads with twine itself; pypa/gh-action-pypi-publish's Docker image
    cannot be found from inside a composite action."""

    ENV = {"ACTIONS_ID_TOKEN_REQUEST_URL": "https://token.actions/oidc?api-version=2.0",
           "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-t"}
    CLAIMS = {"sub": "repo:o/r:environment:pypi", "repository": "o/r", "workflow_ref":
              "o/r/.github/workflows/release.yml@refs/tags/release-1.0.0", "environment": "pypi"}

    def setUp(self):
        self.calls = []
        self.oidc = jwt(self.CLAIMS)
        self.answers = {
            "https://pypi.org/_/oidc/audience": (200, b'{"audience": "pypi"}'),
            "https://token.actions/oidc?api-version=2.0&audience=pypi": (200, json.dumps({"value": self.oidc}).encode()),
            "https://pypi.org/_/oidc/mint-token": (200, b'{"success": true, "token": "pypi-minted", "expires": 1}'),
        }

    def fetch(self, url, data=None, headers=None):
        self.calls.append((url, data, headers))
        return self.answers[url]

    def token(self, **kw):
        with unittest.mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            token = release.pypi_token(env=kw.pop("env", self.ENV), fetch=self.fetch, **kw)
        return token, out.getvalue()

    def test_the_token_is_minted_from_the_job_s_oidc_token_and_masked(self):
        token, out = self.token()
        self.assertEqual(token, "pypi-minted")
        self.assertEqual(out, "::add-mask::pypi-minted\n")
        audience, oidc, mint = self.calls
        self.assertEqual(audience[0], "https://pypi.org/_/oidc/audience")
        self.assertEqual(oidc[2], {"Authorization": "bearer request-t"}, "GitHub's request token, as a bearer")
        self.assertEqual(json.loads(mint[1]), {"token": self.oidc})
        self.assertEqual(mint[2], {"Content-Type": "application/json"})

    def test_the_audience_comes_from_the_index(self):
        self.answers["https://test.pypi.org/_/oidc/audience"] = (200, b'{"audience": "testpypi"}')
        self.answers["https://token.actions/oidc?api-version=2.0&audience=testpypi"] = self.answers.pop(
            "https://token.actions/oidc?api-version=2.0&audience=pypi")
        self.answers["https://test.pypi.org/_/oidc/mint-token"] = self.answers["https://pypi.org/_/oidc/mint-token"]
        self.assertEqual(self.token(index="https://test.pypi.org")[0], "pypi-minted")

    def test_without_id_token_write_it_says_so_before_asking_github(self):
        with self.assertRaisesRegex(RuntimeError, "permissions: id-token: write"):
            self.token(env={})
        self.assertEqual(len(self.calls), 1, "only the audience was fetched")

    def test_github_refusing_the_oidc_token_is_explained(self):
        self.answers["https://token.actions/oidc?api-version=2.0&audience=pypi"] = (403, b'{"message": "no"}')
        with self.assertRaisesRegex(RuntimeError, r"GitHub refused an OIDC token for audience 'pypi' \(403\)"):
            self.token()

    def test_a_refused_exchange_shows_pypi_s_reasons_and_the_claims(self):
        self.answers["https://pypi.org/_/oidc/mint-token"] = (422, json.dumps({"message": "Token request failed",
            "errors": [{"code": "invalid-publisher", "description": "valid token, but no corresponding publisher "
                        "(Publisher with matching claims was not found)"}]}).encode())
        with self.assertRaises(RuntimeError) as caught:
            self.token()
        message = str(caught.exception)
        self.assertIn("https://pypi.org refused the Trusted Publishing exchange (422):", message)
        self.assertIn("invalid-publisher: valid token, but no corresponding publisher", message)
        self.assertIn("workflow_ref: o/r/.github/workflows/release.yml@refs/tags/release-1.0.0", message)
        self.assertIn("environment: pypi", message)
        self.assertIn("job_workflow_ref: MISSING", message)
        self.assertNotIn("pypi-minted", message)

    def test_an_index_without_trusted_publishing_or_a_broken_answer_fails(self):
        for answer, error in [((404, b""), "does not offer Trusted Publishing"),
                              ((403, b""), "has Trusted Publishing disabled"),
                              ((503, b"<html>"), "answered 503 without an audience")]:
            self.answers["https://pypi.org/_/oidc/audience"] = answer
            with self.subTest(answer=answer), self.assertRaisesRegex(RuntimeError, error):
                self.token()
        self.setUp()
        self.answers["https://pypi.org/_/oidc/mint-token"] = (502, b"<html>bad gateway</html>")
        with self.assertRaisesRegex(RuntimeError, "mint-token answered an unexpected 502"):
            self.token()
        self.answers["https://pypi.org/_/oidc/mint-token"] = (200, b'{"success": true}')
        with self.assertRaisesRegex(RuntimeError, "answered without a token"):
            self.token()

    def upload(self, d, **kw):
        commands = []

        def runner(command, env=None):
            commands.append((command, env))
            if command[2:4] == ["pypi_attestations", "sign"]:
                for dist in command[4:]:
                    Path(f"{dist}.publish.attestation").write_text("{}")
        with unittest.mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            files = release.pypi_upload(d, env=self.ENV, fetch=self.fetch, runner=runner, python="py", **kw)
        return commands, files, out.getvalue()

    def dists(self, d):
        for name in ["pkg-1.0.0-py3-none-any.whl", "pkg-1.0.0.tar.gz", "notes.txt"]:
            (Path(d) / name).write_bytes(b"x")

    def test_twine_uploads_with_the_minted_token_and_attestations(self):
        with tempfile.TemporaryDirectory() as d:
            self.dists(d)
            commands, files, out = self.upload(d)
            wheel, sdist = f"{d}/pkg-1.0.0-py3-none-any.whl", f"{d}/pkg-1.0.0.tar.gz"
            self.assertEqual([c for c, _ in commands], [
                ["py", "-m", "twine", "check", wheel, sdist],
                ["py", "-m", "pypi_attestations", "sign", wheel, sdist],
                ["py", "-m", "twine", "upload", "--verbose", "--disable-progress-bar", "--non-interactive",
                 "--attestations", wheel, f"{wheel}.publish.attestation", sdist, f"{sdist}.publish.attestation"]])
            self.assertEqual(commands[-1][1], {"TWINE_USERNAME": "__token__", "TWINE_PASSWORD": "pypi-minted",
                                               "TWINE_REPOSITORY_URL": "https://upload.pypi.org/legacy/"})
            self.assertNotIn("--skip-existing", commands[-1][0], "preflight refused what PyPI has")
            self.assertEqual(len(files), 4)
        self.assertTrue(out.startswith("::add-mask::pypi-minted\n"), "masked before anything else is printed")
        self.assertIn("  sha256: 2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881", out)
        self.assertIn("blake2_256: ", out)

    def test_without_attestations_nothing_is_signed(self):
        with tempfile.TemporaryDirectory() as d:
            self.dists(d)
            commands, files, _ = self.upload(d, attestations=False)
        self.assertEqual([c[3] for c, _ in commands], ["check", "upload"])
        self.assertNotIn("--attestations", commands[-1][0])
        self.assertEqual(len(files), 2)

    def test_nothing_is_uploaded_when_the_exchange_fails(self):
        self.answers["https://pypi.org/_/oidc/mint-token"] = (422, b'{"errors": []}')
        with tempfile.TemporaryDirectory() as d:
            self.dists(d)
            with self.assertRaisesRegex(RuntimeError, "refused the Trusted Publishing exchange"):
                self.upload(d)

    def test_an_empty_directory_or_a_stale_attestation_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(RuntimeError, "no wheels or sdists"):
                self.upload(d)
            self.dists(d)
            (Path(d) / "pkg-1.0.0.tar.gz.publish.attestation").write_text("{}")
            with self.assertRaisesRegex(RuntimeError, "already attested, from an earlier step: pkg-1.0.0.tar.gz"):
                self.upload(d)

    def test_the_command_line(self):
        with unittest.mock.patch.object(release, "pypi_upload") as upload:
            self.assertEqual(release.main(["pypi-upload", "--dir", "d", "--attestations", "false"]), 0)
        upload.assert_called_once_with("d", attestations=False)

    def test_http_answer_returns_an_error_s_body(self):
        from urllib.error import HTTPError
        error = HTTPError("https://pypi.org/_/oidc/mint-token", 422, "Unprocessable", {}, io.BytesIO(b'{"errors": []}'))
        with unittest.mock.patch.object(release, "urlopen", side_effect=error):
            self.assertEqual(release.http_answer("https://pypi.org/_/oidc/mint-token"), (422, b'{"errors": []}'))


class NoDockerInComposites(unittest.TestCase):
    """v2.0.1: a composite action cannot run an action that finds its Docker
    image from `github.action_repository`/`action_ref` (pypa/gh-action-pypi-publish:
    nested, it gets the caller's repository and ref). Every action a kit
    composite uses was read at its pinned commit; a new one is read before it is
    added here."""

    DOCKER_BASED = {"pypa/gh-action-pypi-publish"}
    # runs.using at the pinned commit (and what it runs, where it nests).
    READ = {
        "actions/setup-python": "node24",
        "actions/setup-node": "node24",
        "dtolnay/rust-toolchain": "composite (run steps only)",
        "PyO3/maturin-action": "node24 (its manylinux images are named from its inputs)",
        "actions/attest-build-provenance": "composite (actions/attest, node24)",
    }

    def problems(self, where, document):
        problems = []
        if document["runs"]["using"] != "composite":
            problems.append(f"{where}: runs.using is {document['runs']['using']}, not composite")
        for step in every_step(document):
            uses = step.get("uses")
            if not uses or uses.startswith("./"):
                continue
            name = uses.split("@")[0]
            if uses.startswith("docker://") or name in self.DOCKER_BASED:
                problems.append(f"{where}: {uses} is Docker-based")
            elif name not in self.READ:
                problems.append(f"{where}: {name} has not been read (is it Docker-based, or does it read "
                                "github.action_repository?)")
        return problems

    def test_no_composite_uses_a_docker_action(self):
        problems = [p for action in sorted(KIT.glob("*/action.yml"))
                    for p in self.problems(action.relative_to(KIT).as_posix(), kit_yaml(action.relative_to(KIT)))]
        self.assertEqual(problems, [])

    def test_the_check_refuses_v2_0_0_s_step_an_image_and_an_unread_action(self):
        document = {"runs": {"using": "composite", "steps": [
            {"uses": "pypa/gh-action-pypi-publish@7245906fb61fa33a13554f9d5fb856fe61758086"},
            {"uses": "docker://alpine:3.20"}, {"uses": "someone/new-action@" + "0" * 40},
            {"uses": "actions/setup-python@" + "0" * 40}, {"uses": "./local"}, {"run": "true"}]}}
        self.assertEqual(self.problems("x", document), [
            "x: pypa/gh-action-pypi-publish@7245906fb61fa33a13554f9d5fb856fe61758086 is Docker-based",
            "x: docker://alpine:3.20 is Docker-based",
            "x: someone/new-action has not been read (is it Docker-based, or does it read github.action_repository?)"])
        self.assertEqual(self.problems("y", {"runs": {"using": "docker", "image": "Dockerfile"}}),
                         ["y: runs.using is docker, not composite"])

    def test_publish_pypi_installs_its_tools_from_hashes(self):
        steps = list(every_step(kit_yaml("publish-pypi/action.yml")))
        install = next(s["run"] for s in steps if "requirements.txt" in s.get("run", ""))
        self.assertIn("--require-hashes", install)
        self.assertIn("pypi-upload", next(s["run"] for s in steps if "pypi-upload" in s.get("run", "")))
        lock = (KIT / "publish-pypi" / "requirements.txt").read_text()
        for pin in ["twine==7.0.0", "pypi-attestations==0.0.30", "sigstore==4.5.1"]:
            self.assertIn(pin, lock)
        for line in lock.splitlines():
            if re.match(r"[a-z]", line):
                self.assertRegex(line, r"^[\w.-]+==[\w.]+ ", "every requirement is an exact pin")


if __name__ == "__main__":
    unittest.main()
