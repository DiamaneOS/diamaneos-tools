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

            original_run = build._run

            def fixture_run(command, cwd=None, env=None, timeout=120):
                if command[:3] == ["repo", "manifest", "-r"]:
                    return subprocess.CompletedProcess(command, 0, manifest, b"")
                if "verify-tag" in command:
                    text = ("Good signature for fixture@example.invalid "
                            "with key SHA256:fixture\n").encode()
                    return subprocess.CompletedProcess(command, 0, b"", text)
                return original_run(command, cwd=cwd, env=env, timeout=timeout)

            with mock.patch.object(build, "_run", side_effect=fixture_run):
                with self.assertRaisesRegex(build.BuildError, "dirty or untracked"):
                    build.verify_manifest_checkout(config, source, allowed)
                (project / "untracked.txt").unlink()
                repo_revision_file.write_text("modified implementation\n")
                with self.assertRaisesRegex(build.BuildError, "repo implementation contains"):
                    build.verify_manifest_checkout(config, source, allowed)


if __name__ == "__main__":
    unittest.main()
