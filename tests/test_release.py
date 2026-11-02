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


if __name__ == "__main__":
    unittest.main()
