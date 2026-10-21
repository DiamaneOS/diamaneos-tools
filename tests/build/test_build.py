import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from diamaneos_tools import build

CONFIG_PATH = ROOT / "config" / "build-environment-fp6.json"


def git(path, *arguments):
    subprocess.run(["git", "-C", str(path), *arguments], check=True,
                   capture_output=True, text=True)


class BuildEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.config, self.raw = build.load_config(CONFIG_PATH)

    def test_committed_environment_is_valid_and_hash_bound(self):
        build.validate_config(self.config)
        identity = build.declared_identity(self.config, self.raw, ROOT)
        self.assertRegex(identity["declared_build_identity_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            self.config["upstream"]["project_map_sha256"],
            identity["project_map_sha256"],
        )

    def test_own_input_files_are_named_and_hashed_and_a_pin_is_optional(self):
        self.assertTrue(all(set(item) == {"id", "repository_path"} for item in self.config["project_inputs"]))
        hashes = build.declared_identity(self.config, self.raw, ROOT)["project_inputs"]
        self.assertEqual({item["id"] for item in self.config["project_inputs"]}, set(hashes))
        first = self.config["project_inputs"][0]
        self.assertEqual(build.sha256_file(ROOT / first["repository_path"]), hashes[first["id"]])
        pinned = copy.deepcopy(self.config)
        pinned["project_inputs"][0]["sha256"] = hashes[first["id"]]
        build.validate_config(pinned)
        self.assertEqual(hashes, build.declared_identity(pinned, self.raw, ROOT)["project_inputs"])
        pinned["project_inputs"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(build.BuildError, "project input hash mismatch: " + first["id"]):
            build.declared_identity(pinned, self.raw, ROOT)

    def test_changed_manifest_pin_changes_build_identity(self):
        original = build.declared_identity(self.config, self.raw, ROOT)
        for name, tag_object, peeled_commit in (("manifest", "0" * 40, "1" * 40),
                                                ("repo tool", "2" * 40, "3" * 40)):
            with self.subTest(pin=name):
                changed = copy.deepcopy(self.config)
                pin = changed["upstream"] if name == "manifest" else changed["upstream"]["repo_tool"]
                pin["tag_object"] = tag_object
                pin["peeled_commit"] = peeled_commit
                changed_raw = (json.dumps(changed, sort_keys=True) + "\n").encode()
                modified = build.declared_identity(changed, changed_raw, ROOT)
                self.assertNotEqual(original["declared_build_identity_sha256"],
                                    modified["declared_build_identity_sha256"])

    def test_cold_environment_identifies_inputs_without_private_cache(self):
        # The default environment, and the same file named with --config.
        for extra in ([], ["--config", str(CONFIG_PATH)]):
            with self.subTest(arguments=extra):
                result = subprocess.run(
                    [str(ROOT / "bin" / "diamaneos"), "build", "preflight", "--inputs-only", *extra],
                    cwd=ROOT, capture_output=True, text=True, timeout=30,
                    env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
                )
                self.assertEqual(0, result.returncode, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual("INPUTS_IDENTIFIED", report["status"])
                inputs = report["required_runtime_inputs"]
                self.assertEqual("required-for-full-preflight", inputs["source_checkout"])
                self.assertEqual(self.config["manifest"], inputs["source_manifest"])
                self.assertNotIn("allowed_signers_file", inputs)
                self.assertNotIn("/var/lib/", result.stdout)

    def test_moving_manifest_revision_is_rejected(self):
        xml = b"""<manifest><remote name='aosp' fetch='https://example.invalid'/>
        <default remote='aosp'/><project name='one' revision='refs/heads/main'/>
        </manifest>"""
        with self.assertRaisesRegex(build.BuildError, "moving or non-commit"):
            build.parse_project_map(xml)

    def test_declared_source_local_output_is_accepted(self):
        declared = copy.deepcopy(self.config)
        declared["workspace"]["source_subdirectory"] = "source"
        declared["workspace"]["output_subdirectory"] = "source/out"
        declared["host"]["minimum_source_free_bytes"] = 1
        declared["host"]["minimum_build_free_bytes"] = 1
        # The output the environment declares, an output elsewhere, and a clean build onto old output.
        for config, output_path, stale, message in ((declared, "source/out", False, None),
                                                    (declared, "external-output", False, "source-local"),
                                                    (self.config, "source/out", True, "not empty")):
            with self.subTest(output=output_path, stale=stale), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, cache, output = root / "source", root / "cache", root / output_path
                for directory in (source, cache, output):
                    directory.mkdir()
                if stale:
                    (output / "old-intermediate").write_text("stale\n", encoding="utf-8")
                if message is None:
                    observed = build.verify_workspace(config, source, cache, output)
                    self.assertEqual(str(output.resolve()), observed["output_root"])
                else:
                    with self.assertRaisesRegex(build.BuildError, message):
                        build.verify_workspace(config, source, cache, output, require_empty_output=stale)

    def test_untracked_source_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            manifests = source / ".repo" / "manifests"
            repo_tool = source / ".repo" / "repo"
            project = source / "project"
            manifests.mkdir(parents=True)
            repo_tool.mkdir()
            project.mkdir()
            for repository in (manifests, repo_tool, project):
                git(repository, "init", "-q")
                git(repository, "config", "user.name", "Fixture")
                git(repository, "config", "user.email", "fixture@example.invalid")
                git(repository, "config", "commit.gpgsign", "false")
                git(repository, "config", "tag.gpgsign", "false")

            revision_file = project / "tracked.txt"
            revision_file.write_text("tracked\n", encoding="utf-8")
            git(project, "add", "tracked.txt")
            git(project, "commit", "-q", "-m", "fixture")
            revision = subprocess.run(
                ["git", "-C", str(project), "rev-parse", "HEAD"], check=True,
                capture_output=True, text=True).stdout.strip()
            manifest = (
                "<?xml version='1.0'?><manifest>"
                "<remote name='fixture' fetch='https://example.invalid/'/>"
                "<default remote='fixture'/><project name='fixture-project' "
                f"path='project' revision='{revision}'/></manifest>"
            ).encode()
            (manifests / "default.xml").write_bytes(manifest)
            git(manifests, "add", "default.xml")
            git(manifests, "commit", "-q", "-m", "manifest")
            git(manifests, "remote", "add", "origin", self.config["upstream"]["manifest_url"])
            git(manifests, "tag", "-a", "fixture-tag", "-m", "fixture")
            tag_object = subprocess.run(
                ["git", "-C", str(manifests), "rev-parse", "fixture-tag^{tag}"],
                check=True, capture_output=True, text=True).stdout.strip()
            peeled = subprocess.run(
                ["git", "-C", str(manifests), "rev-parse", "fixture-tag^{}"],
                check=True, capture_output=True, text=True).stdout.strip()
            repo_revision_file = repo_tool / "repo"
            repo_revision_file.write_text("fixture repo tool\n", encoding="utf-8")
            git(repo_tool, "add", "repo")
            git(repo_tool, "commit", "-q", "-m", "repo fixture")
            git(repo_tool, "remote", "add", "origin",
                self.config["upstream"]["repo_tool"]["url"])
            git(repo_tool, "tag", "-a", "fixture-repo", "-m", "repo fixture")
            repo_tag_object = subprocess.run(
                ["git", "-C", str(repo_tool), "rev-parse", "fixture-repo^{tag}"],
                check=True, capture_output=True, text=True).stdout.strip()
            repo_peeled = subprocess.run(
                ["git", "-C", str(repo_tool), "rev-parse", "fixture-repo^{}"],
                check=True, capture_output=True, text=True).stdout.strip()
            rows, map_hash = build.parse_project_map(manifest)
            allowed = Path(temporary) / "allowed_signers"
            allowed.write_text("fixture\n", encoding="utf-8")

            config = copy.deepcopy(self.config)
            upstream = config["upstream"]
            upstream.update({
                "release_tag": "fixture-tag",
                "release_ref": "refs/tags/fixture-tag",
                "tag_object": tag_object,
                "peeled_commit": peeled,
                "default_manifest_sha256": hashlib.sha256(manifest).hexdigest(),
                "project_count": len(rows),
                "project_map_sha256": map_hash,
                "allowed_signers_sha256": build.sha256_file(allowed),
                "signer_identity": "fixture@example.invalid",
                "signer_key_fingerprint": "SHA256:fixture",
            })
            upstream["repo_tool"].update({
                "release_tag": "fixture-repo",
                "tag_object": repo_tag_object,
                "peeled_commit": repo_peeled,
            })
            (project / "untracked.txt").write_text("dirty\n", encoding="utf-8")

            resolved_manifest = manifest
            original_run = build._run

            def fixture_run(command, cwd=None, env=None, timeout=120):
                if command[:3] == ["repo", "manifest", "-r"]:
                    return subprocess.CompletedProcess(command, 0, resolved_manifest, b"")
                if "verify-tag" in command:
                    text = ("Good signature for fixture@example.invalid "
                            "with key SHA256:fixture\n").encode()
                    return subprocess.CompletedProcess(command, 0, b"", text)
                return original_run(command, cwd=cwd, env=env, timeout=timeout)

            with mock.patch.object(build, "_run", side_effect=fixture_run):
                with self.assertRaisesRegex(build.BuildError, "dirty or untracked"):
                    build.verify_manifest_checkout(config, source, allowed)
                (project / "untracked.txt").unlink()
                self.assertTrue(build.verify_manifest_checkout(
                    config, source, allowed)["source_layout_verified"])
                rogue = source / "vendor/google/security/adb/vendor_key.mk"
                rogue.parent.mkdir(parents=True)
                rogue.write_text("undeclared optional include\n")
                with self.assertRaisesRegex(build.BuildError, "undeclared input"):
                    build.verify_manifest_checkout(config, source, allowed)
                import shutil
                shutil.rmtree(source / "vendor")
                (manifests / "unrelated.txt").write_text("clean but different revision\n")
                git(manifests, "add", "unrelated.txt")
                git(manifests, "commit", "-q", "-m", "other manifest revision")
                with self.assertRaisesRegex(build.BuildError, "HEAD does not match"):
                    build.verify_manifest_checkout(config, source, allowed)
                git(manifests, "checkout", "--detach", "-q", peeled)
                repo_revision_file.write_text("modified implementation\n")
                with self.assertRaisesRegex(build.BuildError, "repo implementation contains"):
                    build.verify_manifest_checkout(config, source, allowed)


def rev(path, expression="HEAD"):
    return subprocess.run(["git", "-C", str(path), "rev-parse", expression], check=True,
                          capture_output=True, text=True).stdout.strip()


def fixture_repository(path, name, text):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    for key, value in (("user.name", "Fixture"), ("user.email", "fixture@example.invalid"),
                       ("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
        git(path, "config", key, value)
    (path / name).write_text(text, encoding="utf-8")
    git(path, "add", name)
    git(path, "commit", "-q", "-m", "fixture")
    return rev(path)


class ManifestModeTests(unittest.TestCase):
    """Builds that follow the DiamaneOS manifest branch."""

    def setUp(self):
        self.config, self.raw = build.load_config(ROOT / "config" / "build-environment-fp6.json")

    def test_fp6_environment_follows_the_diamaneos_manifest(self):
        identity = build.declared_identity(self.config, self.raw, ROOT)
        self.assertEqual(self.config["manifest"], identity["source_manifest"])

    def test_manifest_schema(self):
        good = copy.deepcopy(self.config)
        good["manifest"]["revision"] = "a" * 40
        build.validate_config(good)
        good["manifest"]["branch"] = "refs/tags/diamaneos-2026100500"
        build.validate_config(good)
        changes = (("url", "http://example.invalid/manifest"), ("branch", "../escape"),
                   ("branch", "android17/"), ("revision", "android17"), ("extra", True))
        for key, value in changes:
            with self.subTest(key=key, value=value):
                changed = copy.deepcopy(self.config)
                changed["manifest"][key] = value
                with self.assertRaises(build.BuildError):
                    build.validate_config(changed)
        changed = copy.deepcopy(self.config)
        changed["composition"] = {}
        with self.assertRaisesRegex(build.BuildError, "supported schema"):
            build.validate_config(changed)


class BranchCheckoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.config, _ = build.load_config(ROOT / "config" / "build-environment-fp6.json")
        repo_tool = self.source / ".repo" / "repo"
        fixture_repository(repo_tool, "repo", "fixture repo tool\n")
        git(repo_tool, "remote", "add", "origin", self.config["upstream"]["repo_tool"]["url"])
        git(repo_tool, "tag", "-a", "fixture-repo", "-m", "repo fixture")
        self.config["upstream"]["repo_tool"].update({
            "release_tag": "fixture-repo", "tag_object": rev(repo_tool, "fixture-repo^{tag}"),
            "peeled_commit": rev(repo_tool, "fixture-repo^{}")})
        self.project = self.source / "device" / "example"
        revision = fixture_repository(self.project, "tracked.txt", "tracked\n")
        self.manifests = self.source / ".repo" / "manifests"
        self.resolved = ("<manifest><remote name='diamaneos' fetch='https://example.invalid/'/>"
                         "<remote name='grapheneos' fetch='https://example.invalid/gos/'/>"
                         "<default remote='grapheneos'/><project name='example' path='device/example' "
                         f"remote='diamaneos' revision='{revision}' upstream='android17'/></manifest>").encode()
        # GrapheneOS's default.xml with one include of the DiamaneOS projects.
        self.base = ("<manifest>\n  <remote name='grapheneos' fetch='https://example.invalid/gos/'/>\n"
                     "  <default remote='grapheneos'/>\n"
                     f"  <project name='device_example' path='device/example' revision='{'1' * 40}'/>\n</manifest>\n")
        self.config["upstream"]["default_manifest_sha256"] = hashlib.sha256(self.base.encode()).hexdigest()
        fixture_repository(self.manifests, "diamaneos.xml",
                           "<manifest><remote name='diamaneos' fetch='https://example.invalid/'/>"
                           "<remove-project name='device_example'/><project name='example' path='device/example' "
                           f"remote='diamaneos' revision='{revision}'/></manifest>")
        self.commit_manifest("default.xml", self.base.replace("</manifest>", '  <include name="diamaneos.xml" />\n</manifest>'))
        git(self.manifests, "remote", "add", "origin", self.config["manifest"]["url"])
        git(self.manifests, "update-ref", "refs/remotes/origin/android17", "HEAD")
        self.declared = self.resolved
        original = build._run

        def fixture_run(command, cwd=None, env=None, timeout=120):
            if command[:3] == ["repo", "manifest", "-r"]:
                return subprocess.CompletedProcess(command, 0, self.resolved, b"")
            if command == ["repo", "manifest"]:
                return subprocess.CompletedProcess(command, 0, self.declared, b"")
            if "verify-tag" in command:
                return subprocess.CompletedProcess(command, 0, b"", b"Good signature\n")
            return original(command, cwd=cwd, env=env, timeout=timeout)
        patcher = mock.patch.object(build, "_run", side_effect=fixture_run)
        patcher.start()
        self.addCleanup(patcher.stop)

    def verify(self, **kw):
        return build.verify_branch_checkout(self.config, self.source, **kw)

    def commit_manifest(self, name, text):
        (self.manifests / name).write_text(text)
        git(self.manifests, "add", name)
        git(self.manifests, "commit", "-q", "-m", "manifest")
        git(self.manifests, "update-ref", "refs/remotes/origin/android17", "HEAD")

    def test_an_official_build_needs_the_pinned_grapheneos_manifest_with_one_include(self):
        default = (self.manifests / "default.xml").read_text()
        included = (self.manifests / "diamaneos.xml").read_text()
        cases = (
            ("default.xml", default.replace("device/example", "device/other"), "is not GrapheneOS 2026101600's"),
            ("default.xml", default.replace("</manifest>", '<include name="x.xml" />\n</manifest>'), "one include"),
            ("diamaneos.xml", included.replace("</manifest>", "<include name='x.xml'/></manifest>"), "may only add"),
            ("diamaneos.xml", included.replace("'device_example'", "'other'"), "removes other, which GrapheneOS"),
            ("diamaneos.xml", included.replace("<remove-project name='device_example'/>", ""), "two projects"),
        )
        for name, text, message in cases:
            with self.subTest(message):
                (self.manifests / name).write_text(text)
                with self.assertRaisesRegex(build.BuildError, message):
                    self.verify()
                git(self.manifests, "checkout", "-q", "--", name)
        # repo must read the same composition: no other project, remote or revision.
        self.declared = self.declared.replace(b"remote='diamaneos' revision=", b"remote='grapheneos' revision=")
        with self.assertRaisesRegex(build.BuildError, "repo reads another manifest .*: device/example"):
            self.verify()
        self.assertEqual(["repo reads another manifest than GrapheneOS 2026101600 with diamaneos.xml: device/example"],
                         self.verify(allow_modified=True)["deviations"])

    def test_clean_checkout_records_the_resolved_manifest(self):
        path = self.root / "state" / "resolved-manifest.xml"
        result = self.verify(resolved_path=path)
        self.assertEqual(self.resolved, path.read_bytes())
        self.assertEqual(hashlib.sha256(self.resolved).hexdigest(), result["resolved_manifest_sha256"])
        self.assertEqual(1, result["resolved_project_count"])
        self.assertEqual(rev(self.manifests), result["manifest_commit"])
        self.assertEqual("android17", result["manifest_branch"])
        self.assertTrue(result["source_layout_verified"])

    def test_manifest_checkout_must_follow_the_declared_branch_and_pin(self):
        git(self.manifests, "commit", "-q", "--allow-empty", "-m", "local")
        with self.assertRaisesRegex(build.BuildError, "not at android17"):
            self.verify()
        git(self.manifests, "update-ref", "refs/remotes/origin/android17", "HEAD")
        self.verify()
        self.config["manifest"]["revision"] = "f" * 40
        with self.assertRaisesRegex(build.BuildError, "pinned manifest revision"):
            self.verify()
        del self.config["manifest"]["revision"]
        git(self.manifests, "remote", "set-url", "origin", "https://example.invalid/other.git")
        with self.assertRaisesRegex(build.BuildError, "declared manifest URL"):
            self.verify()

    def test_a_reproduced_manifest_is_checked_at_its_recorded_commit_not_the_branch_head(self):
        recorded = rev(self.manifests)
        git(self.manifests, "commit", "-q", "--allow-empty", "-m", "newer")
        git(self.manifests, "update-ref", "refs/remotes/origin/android17", "HEAD")
        head = rev(self.manifests)
        git(self.manifests, "checkout", "-q", "--detach", recorded)
        with self.assertRaisesRegex(build.BuildError, "not at android17"):
            self.verify()
        self.assertEqual(recorded, self.verify(manifest_commit=recorded)["manifest_commit"])
        with self.assertRaisesRegex(build.BuildError, f"not at the recorded manifest commit {head}"):
            self.verify(manifest_commit=head)
        with self.assertRaisesRegex(build.BuildError, "full commit id"):
            self.verify(manifest_commit=recorded[:12])
        # A commit off the branch (another branch, or a fork's commit a host
        # may serve by id) is refused.
        git(self.manifests, "commit", "-q", "--allow-empty", "-m", "off the branch")
        with self.assertRaisesRegex(build.BuildError, "is not in the history of android17"):
            self.verify(manifest_commit=rev(self.manifests))
        git(self.manifests, "checkout", "-q", "--detach", recorded)
        # The other manifest checks still apply.
        self.config["manifest"]["revision"] = head
        with self.assertRaisesRegex(build.BuildError, "pinned manifest revision"):
            self.verify(manifest_commit=recorded)
        del self.config["manifest"]["revision"]
        git(self.manifests, "remote", "set-url", "origin", "https://example.invalid/other.git")
        with self.assertRaisesRegex(build.BuildError, "declared manifest URL"):
            self.verify(manifest_commit=recorded)

    def test_dirty_project_undeclared_input_and_local_manifest_fail(self):
        (self.project / "untracked.txt").write_text("dirty")
        with self.assertRaisesRegex(build.BuildError, "dirty or untracked"):
            self.verify()
        (self.project / "untracked.txt").unlink()
        rogue = self.source / "device" / "rogue.mk"
        rogue.write_text("undeclared")
        with self.assertRaisesRegex(build.BuildError, "undeclared input"):
            self.verify()
        rogue.unlink()
        local = self.source / ".repo" / "local_manifests"
        local.mkdir()
        (local / "diamaneos.xml").write_text("<manifest/>")
        with self.assertRaisesRegex(build.BuildError, "local manifests"):
            self.verify()

    def test_a_build_that_is_not_official_lists_what_an_official_one_refuses(self):
        self.assertEqual(([], None), tuple(self.verify(allow_modified=True)[k]
                                           for k in ("deviations", "deviations_sha256")))
        self.assertEqual([], self.verify()["deviations"])
        (self.source / "device" / "rogue.mk").write_text("undeclared")
        (self.source / ".workspace.lock").write_text("")
        with self.assertRaisesRegex(build.BuildError, "undeclared inputs outside its projects: "
                                                      ".workspace.lock, device/rogue.mk"):
            self.verify()
        local = self.source / ".repo" / "local_manifests"
        local.mkdir()
        (local / "mine.xml").write_text("<manifest/>")
        git(self.manifests, "remote", "set-url", "origin", "ssh://git@example.invalid/manifest")
        git(self.source / ".repo" / "repo", "commit", "-q", "--allow-empty", "-m", "another repo tool")
        self.resolved = self.resolved.replace(b"https://example.invalid/", b"git://example.invalid/")
        result = self.verify(allow_modified=True)
        self.assertEqual(5, len(result["deviations"]))
        self.assertRegex(result["deviations"][0], "repo tool is not the pinned fixture-repo")
        self.assertEqual(["the manifest checkout's origin is not " + self.config["manifest"]["url"],
                          "the manifest fetches from a remote without HTTPS",
                          "local manifests are in use (.repo/local_manifests)",
                          "files outside the manifest's projects: .workspace.lock, device/rogue.mk"],
                         result["deviations"][1:])
        self.assertTrue(result["source_clean"])
        # The deviations are bound like local changes: other content is another source.
        self.assertRegex(result["deviations_sha256"], "^[0-9a-f]{64}$")
        self.assertEqual(result["deviations_sha256"], self.verify(allow_modified=True)["deviations_sha256"])
        (self.source / "device" / "rogue.mk").write_text("edited")
        self.assertNotEqual(result["deviations_sha256"], self.verify(allow_modified=True)["deviations_sha256"])
        # No checkout at all is never a deviation.
        shutil.rmtree(self.source / ".repo" / "repo")
        with self.assertRaisesRegex(build.BuildError, "lacks the pinned repo implementation"):
            self.verify(allow_modified=True)

    def test_local_changes_pass_only_when_allowed_and_are_bound(self):
        (self.project / "untracked.txt").write_text("dirty")
        with self.assertRaisesRegex(build.BuildError, "dirty or untracked content: device/example"):
            self.verify()
        first = self.verify(allow_modified=True)
        self.assertEqual((["device/example"], False), (first["modified"], first["source_clean"]))
        # The digest follows the content the build sees, untracked or edited.
        (self.project / "untracked.txt").write_text("other content")
        second = self.verify(allow_modified=True)
        (self.project / "untracked.txt").unlink()
        (self.project / "tracked.txt").write_text("edited\n")
        edited = self.verify(allow_modified=True)
        (self.project / "tracked.txt").unlink()
        deleted = self.verify(allow_modified=True)
        digests = [result["modified_sha256"] for result in (first, second, edited, deleted)]
        self.assertEqual(4, len(set(digests)))
        git(self.project, "checkout", "-q", "--", "tracked.txt")
        clean = self.verify(allow_modified=True)
        self.assertEqual(([], None, True), (clean["modified"], clean["modified_sha256"], clean["source_clean"]))
        # The manifest checkout stays strict.
        (self.manifests / "local.xml").write_text("<manifest/>")
        with self.assertRaisesRegex(build.BuildError, "dirty or untracked content: .repo/manifests"):
            self.verify(allow_modified=True)

    def test_a_clean_project_off_its_manifest_pin_fails(self):
        pinned = rev(self.project)
        git(self.project, "commit", "-q", "--allow-empty", "-m", "local")
        # repo manifest -r records the commit the project is at.
        self.resolved = self.resolved.replace(pinned.encode(), rev(self.project).encode())
        with self.assertRaisesRegex(build.BuildError, "not at the commits the manifest pins: device/example"):
            self.verify()
        # A build that is not official records it as a deviation.
        self.assertEqual(["projects are not at the commits the manifest pins: device/example"],
                         self.verify(allow_modified=True)["deviations"])
        # A project that follows a branch may be at any clean commit.
        self.declared = self.declared.replace(f"revision='{pinned}' upstream='android17'".encode(),
                                              b"revision='android17'")
        self.commit_manifest("diamaneos.xml", (self.manifests / "diamaneos.xml").read_text().replace(
            f"revision='{pinned}'", "revision='android17'"))
        self.assertTrue(self.verify()["source_clean"])

    def test_remote_without_https_fails(self):
        self.resolved = self.resolved.replace(b"https://example.invalid/", b"git://example.invalid/")
        with self.assertRaisesRegex(build.BuildError, "without HTTPS"):
            self.verify()


class SourceLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "source"
        self.project = self.source / "build/make"
        self.project.mkdir(parents=True)
        (self.project / "entry.mk").write_text("trusted source\n")
        (self.source / "Makefile").write_text("trusted source\n")
        (self.source / "build/entry.mk").symlink_to("make/entry.mk")
        (self.source / ".repo").mkdir()
        self.config = {"workspace": {"source_subdirectory": "src/source",
                                     "output_subdirectory": "src/source/out/vts"}}
        self.xml = (b'<manifest><project name="make" path="build/make">'
                    b'<copyfile src="entry.mk" dest="Makefile"/>'
                    b'<linkfile src="entry.mk" dest="build/entry.mk"/>'
                    b'</project></manifest>')
        self.rows = [("build/make", "make", "fixture", "a" * 40)]

    def check(self, resolved=None):
        build.verify_source_layout(self.config, self.source, self.rows,
                                   self.xml, resolved or self.xml)

    def test_declared_exports_and_sibling_output_are_accepted(self):
        (self.source / "out/generic").mkdir(parents=True)
        (self.source / "out/generic/generated.mk").write_text("build output\n")
        self.check()

    def test_extra_file_between_projects_is_rejected(self):
        (self.source / "build/extra.mk").write_text("not in a project\n")
        with self.assertRaisesRegex(build.BuildError, "undeclared input"):
            self.check()

    def test_changed_export_without_changed_project_map_is_rejected(self):
        modified = self.xml.replace(b'dest="Makefile"', b'dest="other.mk"')
        with self.assertRaisesRegex(build.BuildError, "exports differ"):
            self.check(modified)

    def test_modified_copyfile_is_rejected(self):
        (self.source / "Makefile").write_text("changed\n")
        with self.assertRaisesRegex(build.BuildError, "copyfile content mismatch"):
            self.check()

    def test_redirected_linkfile_is_rejected(self):
        link = self.source / "build/entry.mk"
        link.unlink()
        link.symlink_to("../Makefile")
        with self.assertRaisesRegex(build.BuildError, "linkfile target mismatch"):
            self.check()

    def test_missing_export_is_rejected(self):
        (self.source / "Makefile").unlink()
        with self.assertRaisesRegex(build.BuildError, "destination is missing"):
            self.check()

    def test_redirected_project_or_container_is_rejected(self):
        import shutil
        shutil.rmtree(self.project)
        self.project.symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(build.BuildError, "redirected directory"):
            self.check()

    def test_undeclared_local_manifest_is_rejected(self):
        local = self.source / ".repo/local_manifests"
        local.mkdir()
        (local / "extra.xml").write_text("<manifest/>\n")
        with self.assertRaisesRegex(build.BuildError, "local manifests"):
            self.check()

    def test_unsafe_manifest_paths_are_rejected(self):
        for value in (b"../escape", b"/absolute", b"a//b", b"a/./b"):
            self.xml = self.xml.replace(b'dest="Makefile"', b'dest="' + value + b'"')
            with self.subTest(value=value), self.assertRaisesRegex(build.BuildError, "unsafe path"):
                self.check()
            self.setUp_xml()

    def setUp_xml(self):
        self.xml = (b'<manifest><project name="make" path="build/make">'
                    b'<copyfile src="entry.mk" dest="Makefile"/>'
                    b'<linkfile src="entry.mk" dest="build/entry.mk"/>'
                    b'</project></manifest>')


if __name__ == "__main__":
    unittest.main()
