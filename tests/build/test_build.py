import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from diamaneos_tools import build

CONFIG_PATH = ROOT / "config" / "build-environment.json"


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

    def test_thermal_gate_has_no_machine_specific_default(self):
        arguments = build._parser().parse_args([])
        self.assertIsNone(arguments.thermal_check)

    def test_changed_manifest_pin_changes_build_identity(self):
        changed = copy.deepcopy(self.config)
        changed["upstream"]["tag_object"] = "0" * 40
        changed["upstream"]["peeled_commit"] = "1" * 40
        changed_raw = (json.dumps(changed, sort_keys=True) + "\n").encode()
        original = build.declared_identity(self.config, self.raw, ROOT)
        modified = build.declared_identity(changed, changed_raw, ROOT)
        self.assertNotEqual(original["declared_build_identity_sha256"],
                            modified["declared_build_identity_sha256"])

    def test_changed_repo_tool_pin_changes_build_identity(self):
        changed = copy.deepcopy(self.config)
        changed["upstream"]["repo_tool"]["tag_object"] = "2" * 40
        changed["upstream"]["repo_tool"]["peeled_commit"] = "3" * 40
        changed_raw = (json.dumps(changed, sort_keys=True) + "\n").encode()
        original = build.declared_identity(self.config, self.raw, ROOT)
        modified = build.declared_identity(changed, changed_raw, ROOT)
        self.assertNotEqual(original["declared_build_identity_sha256"],
                            modified["declared_build_identity_sha256"])

    def test_cold_environment_identifies_inputs_without_private_cache(self):
        result = subprocess.run(
            [str(ROOT / "bin" / "diamaneos"), "build", "preflight", "--inputs-only"],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
            env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(0, result.returncode, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual("INPUTS_IDENTIFIED", report["status"])
        self.assertEqual("required-for-full-preflight",
                         report["required_runtime_inputs"]["source_checkout"])
        self.assertNotIn("/var/lib/diamaneos-build", result.stdout)

    def test_moving_manifest_revision_is_rejected(self):
        xml = b"""<manifest><remote name='aosp' fetch='https://example.invalid'/>
        <default remote='aosp'/><project name='one' revision='refs/heads/main'/>
        </manifest>"""
        with self.assertRaisesRegex(build.BuildError, "moving or non-commit"):
            build.parse_project_map(xml)

    def test_declared_source_local_output_is_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            cache = root / "cache"
            output = source / "out"
            source.mkdir()
            cache.mkdir()
            output.mkdir()
            config = copy.deepcopy(self.config)
            config["workspace"]["source_subdirectory"] = "source"
            config["workspace"]["output_subdirectory"] = "source/out"
            config["host"]["minimum_source_free_bytes"] = 1
            config["host"]["minimum_build_free_bytes"] = 1
            observed = build.verify_workspace(config, source, cache, output)
            self.assertEqual(str(output.resolve()), observed["output_root"])

    def test_runtime_output_must_match_declared_source_local_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            cache = root / "cache"
            output = root / "external-output"
            source.mkdir()
            cache.mkdir()
            output.mkdir()
            config = copy.deepcopy(self.config)
            config["workspace"]["source_subdirectory"] = "source"
            config["workspace"]["output_subdirectory"] = "source/out"
            with self.assertRaisesRegex(build.BuildError, "source-local"):
                build.verify_workspace(config, source, cache, output)

    def test_clean_build_rejects_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            cache = root / "cache"
            output = source / "out"
            source.mkdir()
            cache.mkdir()
            output.mkdir()
            (output / "old-intermediate").write_text("stale\n", encoding="utf-8")
            with self.assertRaisesRegex(build.BuildError, "not empty"):
                build.verify_workspace(self.config, source, cache, output,
                                       require_empty_output=True)

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
                # Exercise the full caller with a pinned additive checkout.
                import shutil
                extra = source / "device/example"
                extra.parent.mkdir()
                subprocess.run(["git", "clone", "-q", str(project), str(extra)], check=True)
                overlay = ("<manifest><project name='extra' path='device/example' "
                           f"remote='fixture' revision='{revision}'/></manifest>").encode()
                local = source / ".repo/local_manifests"
                local.mkdir()
                (local / "diamaneos.xml").write_bytes(overlay)
                resolved_manifest = manifest.replace(b"</manifest>",
                    overlay.removeprefix(b"<manifest>"))
                composed_rows, composed_hash = build.parse_project_map(resolved_manifest)
                config["composition"] = {
                    "overlay_revision": "c" * 40,
                    "overlay_sha256": hashlib.sha256(overlay).hexdigest(),
                    "project_count": len(composed_rows),
                    "project_map_sha256": composed_hash,
                }
                self.assertEqual(2, build.verify_manifest_checkout(
                    config, source, allowed)["resolved_project_count"])
                good_resolved = resolved_manifest
                resolved_manifest = resolved_manifest.replace(
                    b"https://example.invalid/", b"https://other.invalid/")
                with self.assertRaisesRegex(build.BuildError, "remotes differ"):
                    build.verify_manifest_checkout(config, source, allowed)
                resolved_manifest = good_resolved
                (extra / "untracked.txt").write_text("untrusted")
                with self.assertRaisesRegex(build.BuildError, "dirty or untracked"):
                    build.verify_manifest_checkout(config, source, allowed)
                shutil.rmtree(extra.parent)
                shutil.rmtree(local)
                del config["composition"]
                resolved_manifest = manifest
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
